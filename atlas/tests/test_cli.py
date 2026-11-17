import json
from pathlib import Path

import pytest

from atlas.cli import main


def test_search_prints_path_line_and_text(
    sample_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["search", "fire_event", "--path", str(sample_repo)])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out == "ignite/engine/engine.py:3: self.fire_event(Events.STARTED)\n"
    assert output.err == "1 match\n"


def test_search_json_output(sample_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["search", "accuracy", "--path", str(sample_repo), "--json"])

    records = json.loads(capsys.readouterr().out)
    assert records == [{"path": "ignite/metrics.py", "line_number": 1, "line": "def accuracy():"}]


def test_invalid_regex_is_reported_not_crashed(
    sample_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["search", "(", "--regex", "--path", str(sample_repo)])

    assert exit_code == 1
    assert "invalid regular expression" in capsys.readouterr().err


def test_missing_folder_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["search", "x", "--path", str(tmp_path / "nope")])

    assert exit_code == 1
    assert "not a folder" in capsys.readouterr().err


def test_stats_table(sample_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["stats", "--path", str(sample_repo), "--top", "1"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert ".py         2        5" in output
    assert "Largest 1 files:" in output
