from pathlib import Path

import pytest


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """A tiny fake codebase: two Python files, one doc, and files that must be ignored."""
    (tmp_path / "ignite" / "engine").mkdir(parents=True)
    (tmp_path / "ignite" / "engine" / "engine.py").write_text(
        "class Engine:\n    def run(self):\n        self.fire_event(Events.STARTED)\n",
        encoding="utf-8",
    )
    (tmp_path / "ignite" / "metrics.py").write_text(
        "def accuracy():\n    return 1.0\n", encoding="utf-8"
    )
    (tmp_path / "README.md").write_text("# Ignite\n\nUse Events to hook into training.\n")

    # Must never appear in results: a skipped folder, a wrong extension, a binary file.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.py").write_text("Events = 'hidden'\n")
    (tmp_path / "notes.txt").write_text("Events in a text file\n")
    (tmp_path / "logo.md").write_bytes(b"\xff\xfe\x00 not utf-8 Events")
    return tmp_path
