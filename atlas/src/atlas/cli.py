"""The `atlas` command line.

atlas search "query" --path ~/code/ignite
atlas stats --path ~/code/ignite
atlas issues --repo pytorch/ignite
atlas commits --repo pytorch/ignite
"""

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas import issue_cache
from atlas.github import GitHubError, fetch_issues, fetch_recent_commits
from atlas.issue_cache import CachedIssues
from atlas.search import DEFAULT_EXTENSIONS, search
from atlas.stats import collect_stats

DEFAULT_REPO = "pytorch/ignite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas", description="Explore a codebase.")
    commands = parser.add_subparsers(dest="command", required=True)

    search_parser = commands.add_parser("search", help="find lines containing a query")
    search_parser.add_argument("query", help="text to look for")
    search_parser.add_argument("--path", type=Path, default=Path("."), help="folder to search")
    search_parser.add_argument("-i", "--ignore-case", action="store_true")
    search_parser.add_argument("--regex", action="store_true", help="treat query as a regex")
    search_parser.add_argument("--limit", type=int, default=None, help="stop after N matches")
    search_parser.add_argument("--json", action="store_true", help="print results as JSON")
    search_parser.add_argument(
        "--ext",
        nargs="+",
        default=list(DEFAULT_EXTENSIONS),
        help="file extensions to include (default: .py .md .rst)",
    )

    stats_parser = commands.add_parser("stats", help="count files and lines by type")
    stats_parser.add_argument("--path", type=Path, default=Path("."), help="folder to measure")
    stats_parser.add_argument("--top", type=int, default=5, help="how many largest files to show")
    stats_parser.add_argument("--ext", nargs="+", default=list(DEFAULT_EXTENSIONS))

    issues_parser = commands.add_parser("issues", help="list open issues from GitHub")
    issues_parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name on GitHub")
    issues_parser.add_argument(
        "--label",
        action="append",
        help="issue label; repeat for several (default: good first issue)",
    )
    issues_parser.add_argument("--refresh", action="store_true", help="ignore the cached copy")
    issues_parser.add_argument(
        "--max-age", type=int, default=60, help="minutes a cached copy stays valid"
    )
    issues_parser.add_argument("--cache", type=Path, default=None, help="cache file location")
    issues_parser.add_argument("--json", action="store_true", help="print results as JSON")

    commits_parser = commands.add_parser("commits", help="list recent commits from GitHub")
    commits_parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name on GitHub")
    commits_parser.add_argument("--limit", type=int, default=10, help="how many (1 to 100)")

    return parser


def folder_error(path: Path) -> int | None:
    """Print an error and return exit code 1 if path is not a folder."""
    if path.is_dir():
        return None
    print(f"atlas: not a folder: {path}", file=sys.stderr)
    return 1


def github_token() -> str | None:
    # Optional: without a token GitHub allows 60 requests an hour, with one 5,000.
    # Part 4 moves this into a proper settings object.
    return os.environ.get("GITHUB_TOKEN")


def run_search(args: argparse.Namespace) -> int:
    error_code = folder_error(args.path)
    if error_code is not None:
        return error_code

    try:
        matches = search(
            args.path,
            args.query,
            ignore_case=args.ignore_case,
            use_regex=args.regex,
            extensions=tuple(args.ext),
            limit=args.limit,
        )
    except re.error as error:
        print(f"atlas: invalid regular expression: {error}", file=sys.stderr)
        return 1

    if args.json:
        records = [
            {"path": str(match.path), "line_number": match.line_number, "line": match.line}
            for match in matches
        ]
        print(json.dumps(records, indent=2))
        return 0

    for match in matches:
        print(f"{match.path}:{match.line_number}: {match.line}")

    # The count goes to stderr so `atlas search ... > results.txt` saves only the results.
    noun = "match" if len(matches) == 1 else "matches"
    print(f"{len(matches)} {noun}", file=sys.stderr)
    return 0


def run_stats(args: argparse.Namespace) -> int:
    error_code = folder_error(args.path)
    if error_code is not None:
        return error_code

    stats = collect_stats(args.path, tuple(args.ext), top=args.top)

    print(f"{'type':<6} {'files':>6} {'lines':>8}")
    for extension, totals in sorted(stats.totals_by_extension.items()):
        print(f"{extension:<6} {totals.files:>6} {totals.lines:>8}")

    print(f"\nLargest {len(stats.largest_files)} files:")
    for path, line_count in stats.largest_files:
        print(f"{line_count:>8}  {path}")
    return 0


def run_issues(args: argparse.Namespace) -> int:
    labels = tuple(args.label or ["good first issue"])
    cache_path = args.cache or issue_cache.default_cache_path(args.repo)
    now = datetime.now(UTC)

    cached = None if args.refresh else issue_cache.load(cache_path)
    max_age = timedelta(minutes=args.max_age)
    use_cache = cached is not None and cached.is_usable(args.repo, labels, now, max_age)

    if cached is not None and use_cache:
        issues = cached.issues
        minutes_old = int((now - cached.fetched_at).total_seconds() // 60)
        source = f"cached copy from {minutes_old} min ago"
    else:
        try:
            issues = asyncio.run(fetch_issues(args.repo, list(labels), token=github_token()))
        except GitHubError as error:
            print(f"atlas: {error}", file=sys.stderr)
            return 1
        issue_cache.save(cache_path, CachedIssues(args.repo, labels, now, issues))
        source = "GitHub"

    if args.json:
        records = [
            {
                "number": issue.number,
                "title": issue.title,
                "url": issue.url,
                "labels": list(issue.labels),
                "created_at": issue.created_at.isoformat(),
            }
            for issue in issues
        ]
        print(json.dumps(records, indent=2))
    else:
        for issue in issues:
            age_days = (now - issue.created_at).days
            print(f"#{issue.number:<6} {age_days:>4}d  {issue.title}  [{', '.join(issue.labels)}]")

    noun = "issue" if len(issues) == 1 else "issues"
    print(f"{len(issues)} open {noun} from {source}", file=sys.stderr)
    return 0


def run_commits(args: argparse.Namespace) -> int:
    limit_is_valid = 1 <= args.limit <= 100
    if not limit_is_valid:
        print("atlas: --limit must be between 1 and 100", file=sys.stderr)
        return 1

    try:
        commits = asyncio.run(fetch_recent_commits(args.repo, args.limit, token=github_token()))
    except GitHubError as error:
        print(f"atlas: {error}", file=sys.stderr)
        return 1

    for commit in commits:
        day = commit.committed_at.strftime("%Y-%m-%d")
        print(f"{commit.sha}  {day}  {commit.author:<20}  {commit.summary}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "search":
        return run_search(args)
    if args.command == "stats":
        return run_stats(args)
    if args.command == "issues":
        return run_issues(args)
    return run_commits(args)


if __name__ == "__main__":
    sys.exit(main())
