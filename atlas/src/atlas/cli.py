"""The `atlas` command line.

atlas search "query" --path ~/code/ignite
atlas stats --path ~/code/ignite
"""

import argparse
import json
import re
import sys
from pathlib import Path

from atlas.search import DEFAULT_EXTENSIONS, search
from atlas.stats import collect_stats


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

    return parser


def run_search(args: argparse.Namespace) -> int:
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
    stats = collect_stats(args.path, tuple(args.ext), top=args.top)

    print(f"{'type':<6} {'files':>6} {'lines':>8}")
    for extension, totals in sorted(stats.totals_by_extension.items()):
        print(f"{extension:<6} {totals.files:>6} {totals.lines:>8}")

    print(f"\nLargest {len(stats.largest_files)} files:")
    for path, line_count in stats.largest_files:
        print(f"{line_count:>8}  {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    folder_exists = args.path.is_dir()
    if not folder_exists:
        print(f"atlas: not a folder: {args.path}", file=sys.stderr)
        return 1

    if args.command == "search":
        return run_search(args)
    return run_stats(args)


if __name__ == "__main__":
    sys.exit(main())
