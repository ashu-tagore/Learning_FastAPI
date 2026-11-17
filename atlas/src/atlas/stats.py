"""Size statistics for a folder of source files: how many files and lines of each kind."""

from dataclasses import dataclass, field
from pathlib import Path

from atlas.search import iter_source_files


@dataclass
class ExtensionTotals:
    files: int = 0
    lines: int = 0


@dataclass
class FolderStats:
    totals_by_extension: dict[str, ExtensionTotals] = field(default_factory=dict)
    # (relative path, line count), biggest first
    largest_files: list[tuple[Path, int]] = field(default_factory=list)


def count_lines(path: Path) -> int | None:
    """Number of lines in a UTF-8 text file, or None if it isn't one."""
    try:
        with path.open(encoding="utf-8") as file:
            return sum(1 for _ in file)
    except UnicodeDecodeError:
        return None


def collect_stats(root: Path, extensions: tuple[str, ...], top: int = 5) -> FolderStats:
    stats = FolderStats()
    all_files: list[tuple[Path, int]] = []

    for path in iter_source_files(root, extensions):
        line_count = count_lines(path)
        if line_count is None:
            continue

        totals = stats.totals_by_extension.setdefault(path.suffix, ExtensionTotals())
        totals.files += 1
        totals.lines += line_count
        all_files.append((path.relative_to(root), line_count))

    # Ties are broken by path so the output is the same on every machine.
    all_files.sort(key=lambda item: (-item[1], str(item[0])))
    stats.largest_files = all_files[:top]
    return stats
