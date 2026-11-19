"""The `atlas` command line.

atlas search "query" --path ~/code/ignite
atlas stats --path ~/code/ignite
atlas issues --repo pytorch/ignite
atlas commits --repo pytorch/ignite
atlas init-db
atlas index --path ~/code/ignite --repo pytorch/ignite
atlas find "event handlers" --repo pytorch/ignite
atlas issue-stats --repo pytorch/ignite
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from atlas import issue_cache, storage
from atlas.config import Settings
from atlas.github import GitHubError, fetch_issues, fetch_recent_commits
from atlas.issue_cache import CachedIssues
from atlas.models import User
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
    issues_parser.add_argument("--save", action="store_true", help="also store them in Postgres")

    commits_parser = commands.add_parser("commits", help="list recent commits from GitHub")
    commits_parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name on GitHub")
    commits_parser.add_argument("--limit", type=int, default=10, help="how many (1 to 100)")

    commands.add_parser("init-db", help="create or upgrade the database tables")

    admin_parser = commands.add_parser("make-admin", help="give a user the admin role")
    admin_parser.add_argument("--email", required=True)

    index_parser = commands.add_parser("index", help="store a codebase in Postgres")
    index_parser.add_argument("--path", type=Path, default=Path("."), help="folder to index")
    index_parser.add_argument("--repo", default=DEFAULT_REPO, help="name to store it under")
    index_parser.add_argument("--ext", nargs="+", default=list(DEFAULT_EXTENSIONS))

    find_parser = commands.add_parser("find", help="ranked full-text search in Postgres")
    find_parser.add_argument("query", help="words to look for")
    find_parser.add_argument("--repo", default=DEFAULT_REPO)
    find_parser.add_argument("--limit", type=int, default=10)

    stats_db_parser = commands.add_parser("issue-stats", help="summarise stored issues")
    stats_db_parser.add_argument("--repo", default=DEFAULT_REPO)

    return parser


def database_url() -> str | None:
    # Required for the database commands; read with the rest of the settings (lesson 4.2).
    return Settings().database_url


def open_database() -> Session | None:
    """A session on the configured database, or None after explaining what's missing."""
    url = database_url()
    if url is None:
        print(
            "atlas: set DATABASE_URL, for example "
            "postgresql+psycopg://atlas:atlas-dev-password@localhost:5432/atlas",
            file=sys.stderr,
        )
        return None
    return Session(create_engine(url))


def folder_error(path: Path) -> int | None:
    """Print an error and return exit code 1 if path is not a folder."""
    if path.is_dir():
        return None
    print(f"atlas: not a folder: {path}", file=sys.stderr)
    return 1


def github_token() -> str | None:
    token = Settings().github_token
    return token.get_secret_value() if token is not None else None


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

    if args.save:
        session = open_database()
        if session is None:
            return 1
        with session, session.begin():
            stored = storage.save_issues(session, args.repo, issues)
        print(f"stored {stored} issues in the database", file=sys.stderr)

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


def run_init_db() -> int:
    """Bring the database schema up to date by running every pending migration (lesson 4.13)."""
    url = database_url()
    if url is None:
        return 1 if open_database() is None else 0

    config = AlembicConfig()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    alembic_command.upgrade(config, "head")
    print("database schema is up to date", file=sys.stderr)
    return 0


def run_make_admin(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    with session, session.begin():
        user = session.scalar(select(User).where(User.email == args.email.lower()))
        if user is None:
            print(f"atlas: no user with email {args.email}", file=sys.stderr)
            return 1
        user.role = "admin"
    print(f"{args.email} is now an administrator", file=sys.stderr)
    return 0


def run_index(args: argparse.Namespace) -> int:
    error_code = folder_error(args.path)
    if error_code is not None:
        return error_code
    session = open_database()
    if session is None:
        return 1

    # One transaction: searches see either the old index or the complete new one.
    with session, session.begin():
        summary = storage.index_folder(session, args.repo, args.path, extensions=tuple(args.ext))
    print(
        f"indexed {summary.documents} files into {summary.chunks} chunks "
        f"({summary.skipped} skipped) as {args.repo}",
        file=sys.stderr,
    )
    return 0


def run_find(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    with session:
        hits = storage.find_chunks(session, args.repo, args.query, limit=args.limit)

    for hit in hits:
        print(f"{hit.path}:{hit.start_line}-{hit.end_line}  {hit.rank:.3f}  {hit.snippet}")
    noun = "chunk" if len(hits) == 1 else "chunks"
    print(f"{len(hits)} {noun}", file=sys.stderr)
    return 0


def run_issue_stats(args: argparse.Namespace) -> int:
    session = open_database()
    if session is None:
        return 1

    with session:
        by_label = storage.label_counts(session, args.repo)
        by_age = storage.age_buckets(session, args.repo, datetime.now(UTC))

    print("By label:")
    for name, count in by_label:
        print(f"{count:>5}  {name}")
    print("\nBy age:")
    for bucket, count in by_age:
        print(f"{count:>5}  {bucket}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        return dispatch(args)
    except OperationalError as error:
        # The usual cause: the database isn't running or the URL is wrong.
        print(f"atlas: cannot reach the database: {error.orig}", file=sys.stderr)
        return 1


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "search":
        return run_search(args)
    if args.command == "stats":
        return run_stats(args)
    if args.command == "issues":
        return run_issues(args)
    if args.command == "commits":
        return run_commits(args)
    if args.command == "init-db":
        return run_init_db()
    if args.command == "make-admin":
        return run_make_admin(args)
    if args.command == "index":
        return run_index(args)
    if args.command == "find":
        return run_find(args)
    return run_issue_stats(args)


if __name__ == "__main__":
    sys.exit(main())
