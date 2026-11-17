"""Keyword search over a folder of source files.

Given a folder (for example a clone of PyTorch-Ignite) and a query, find every
line that contains the query and report it as path:line_number.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

DEFAULT_EXTENSIONS = (".py", ".md", ".rst")

# Tool and cache folders hold thousands of files nobody wants in search results.
SKIPPED_FOLDERS = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}


@dataclass(frozen=True)
class Match:
    path: Path  # relative to the folder that was searched
    line_number: int
    line: str


def iter_source_files(root: Path, extensions: tuple[str, ...]) -> Iterator[Path]:
    """Yield files under root with one of the given extensions, in a stable order."""
    for path in sorted(root.rglob("*")):
        relative_parts = path.relative_to(root).parts
        is_in_skipped_folder = any(part in SKIPPED_FOLDERS for part in relative_parts)
        if is_in_skipped_folder:
            continue

        is_wanted_file = path.is_file() and path.suffix in extensions
        if is_wanted_file:
            yield path


def build_pattern(query: str, *, ignore_case: bool, use_regex: bool) -> re.Pattern[str]:
    """Turn the user's query into a compiled regular expression.

    Plain queries are escaped so characters like "." or "(" match literally.
    Raises re.error if use_regex is True and the query is not a valid pattern.
    """
    source = query if use_regex else re.escape(query)
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(source, flags)


def search_file(path: Path, pattern: re.Pattern[str]) -> Iterator[tuple[int, str]]:
    """Yield (line_number, line) for each line in the file that matches."""
    try:
        # Reading line by line keeps memory flat even for very large files.
        with path.open(encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                if pattern.search(line):
                    yield line_number, line.rstrip("\n")
    except UnicodeDecodeError:
        # Not a UTF-8 text file (an image saved as .md, for example): skip it.
        return


def search(
    root: Path,
    query: str,
    *,
    ignore_case: bool = False,
    use_regex: bool = False,
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS,
    limit: int | None = None,
) -> list[Match]:
    """Search every source file under root and return matches in file order."""
    pattern = build_pattern(query, ignore_case=ignore_case, use_regex=use_regex)
    matches: list[Match] = []

    for path in iter_source_files(root, extensions):
        for line_number, line in search_file(path, pattern):
            matches.append(Match(path.relative_to(root), line_number, line.strip()))

            reached_limit = limit is not None and len(matches) >= limit
            if reached_limit:
                return matches

    return matches
