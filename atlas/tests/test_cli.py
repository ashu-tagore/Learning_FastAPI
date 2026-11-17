import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from atlas.cli import main
from atlas.github import GitHubError, Issue

ISSUE = Issue(
    number=12,
    title="Fix docstring typo",
    url="https://github.com/pytorch/ignite/issues/12",
    author="contributor",
    labels=("good first issue",),
    comments=2,
    created_at=datetime(2026, 11, 2, 9, 14, tzinfo=UTC),
)


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


def test_issues_fetches_then_uses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = []

    async def fake_fetch(repo: str, labels: list[str], token: str | None = None) -> list[Issue]:
        calls.append((repo, labels))
        return [ISSUE]

    monkeypatch.setattr("atlas.cli.fetch_issues", fake_fetch)
    cache = tmp_path / "issues.json"
    arguments = ["issues", "--repo", "pytorch/ignite", "--cache", str(cache)]

    assert main(arguments) == 0
    first = capsys.readouterr()
    assert "#12" in first.out and "Fix docstring typo" in first.out
    assert first.err == "1 open issue from GitHub\n"

    assert main(arguments) == 0
    second = capsys.readouterr()
    assert second.err.startswith("1 open issue from cached copy")
    assert calls == [("pytorch/ignite", ["good first issue"])]


def test_issues_reports_github_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def failing_fetch(repo: str, labels: list[str], token: str | None = None) -> list[Issue]:
        raise GitHubError("GitHub rate limit reached; it resets at 10:00 UTC")

    monkeypatch.setattr("atlas.cli.fetch_issues", failing_fetch)

    exit_code = main(["issues", "--refresh", "--cache", str(tmp_path / "c.json")])

    assert exit_code == 1
    assert "rate limit" in capsys.readouterr().err


def test_commits_rejects_out_of_range_limit(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["commits", "--limit", "500"]) == 1
    assert "between 1 and 100" in capsys.readouterr().err


def test_database_commands_explain_missing_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert main(["find", "Engine"]) == 1
    assert "set DATABASE_URL" in capsys.readouterr().err


def test_unreachable_database_is_reported_not_crashed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Port 1 has nothing listening, so the connection is refused immediately.
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://atlas:x@127.0.0.1:1/atlas")

    assert main(["find", "Engine"]) == 1
    assert "cannot reach the database" in capsys.readouterr().err
