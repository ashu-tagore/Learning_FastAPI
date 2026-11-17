from pathlib import Path

from atlas.stats import collect_stats, count_lines


def test_count_lines(sample_repo: Path) -> None:
    assert count_lines(sample_repo / "ignite" / "metrics.py") == 2
    assert count_lines(sample_repo / "logo.md") is None


def test_totals_by_extension_skip_unreadable_files(sample_repo: Path) -> None:
    stats = collect_stats(sample_repo, (".py", ".md"))

    assert stats.totals_by_extension[".py"].files == 2
    assert stats.totals_by_extension[".py"].lines == 5
    assert stats.totals_by_extension[".md"].files == 1  # logo.md is not text
    assert stats.totals_by_extension[".md"].lines == 3


def test_largest_files_biggest_first_and_limited(sample_repo: Path) -> None:
    stats = collect_stats(sample_repo, (".py", ".md"), top=2)

    assert stats.largest_files == [
        (Path("README.md"), 3),
        (Path("ignite/engine/engine.py"), 3),
    ]
