import re
from pathlib import Path

import pytest

from atlas.search import Match, iter_source_files, search


def test_finds_matching_lines_with_relative_paths_and_line_numbers(sample_repo: Path) -> None:
    matches = search(sample_repo, "fire_event")

    assert matches == [
        Match(Path("ignite/engine/engine.py"), 3, "self.fire_event(Events.STARTED)"),
    ]


def test_results_are_in_file_order(sample_repo: Path) -> None:
    paths = [match.path for match in search(sample_repo, "Events")]

    assert paths == [Path("README.md"), Path("ignite/engine/engine.py")]


def test_skips_tool_folders_other_extensions_and_binary_files(sample_repo: Path) -> None:
    searched = {path.name for path in iter_source_files(sample_repo, (".py", ".md"))}

    assert searched == {"engine.py", "metrics.py", "README.md", "logo.md"}
    # logo.md is listed but unreadable as text, so it contributes no matches.
    assert all(match.path.name != "logo.md" for match in search(sample_repo, "Events"))


@pytest.mark.parametrize(
    ("query", "ignore_case", "expected_count"),
    [
        ("events", False, 0),
        ("events", True, 2),
        ("EVENTS", True, 2),
    ],
)
def test_ignore_case(sample_repo: Path, query: str, ignore_case: bool, expected_count: int) -> None:
    assert len(search(sample_repo, query, ignore_case=ignore_case)) == expected_count


def test_plain_queries_treat_regex_characters_literally(sample_repo: Path) -> None:
    assert len(search(sample_repo, "run(self)")) == 1
    assert search(sample_repo, "r.n") == []


def test_regex_queries(sample_repo: Path) -> None:
    matches = search(sample_repo, r"def \w+\(", use_regex=True)

    assert [match.line for match in matches] == ["def run(self):", "def accuracy():"]


def test_invalid_regex_raises(sample_repo: Path) -> None:
    with pytest.raises(re.error):
        search(sample_repo, "(unclosed", use_regex=True)


def test_limit_stops_early(sample_repo: Path) -> None:
    assert len(search(sample_repo, "e", limit=3)) == 3


def test_extensions_filter(sample_repo: Path) -> None:
    matches = search(sample_repo, "Events", extensions=(".md",))

    assert [match.path for match in matches] == [Path("README.md")]
