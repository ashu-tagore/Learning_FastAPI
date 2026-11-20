"""Chunking source files by their structure (lesson 6.5)."""

from atlas.chunking import Piece, chunk_file

PYTHON = '''"""Tools for training loops."""

import math

LIMIT = 10


@decorated
def top_level(x):
    """Double it."""
    return x * 2


class Engine:
    """Runs the loop."""

    retries = 3

    def __init__(self):
        self.state = None

    def run(self, data):
        for batch in data:
            self.step(batch)
        return self.state
'''


def spans(pieces: list[Piece]) -> list[tuple[str | None, int, int]]:
    return [(piece.symbol, piece.start_line, piece.end_line) for piece in pieces]


def test_python_is_split_at_functions_classes_and_methods() -> None:
    pieces = chunk_file("ignite/engine.py", PYTHON)

    assert spans(pieces) == [
        (None, 1, 5),  # module docstring, imports and constants (blank edges trimmed)
        ("top_level", 8, 11),  # the decorator belongs to the function
        ("Engine", 14, 17),  # class line, docstring and class attributes
        ("Engine.__init__", 19, 20),
        ("Engine.run", 22, 25),
    ]
    assert pieces[1].text.startswith("@decorated\ndef top_level(x):")


def test_every_line_is_covered_exactly_once_or_is_blank() -> None:
    pieces = chunk_file("ignite/engine.py", PYTHON)
    covered = [line for p in pieces for line in range(p.start_line, p.end_line + 1)]
    lines = PYTHON.splitlines()

    assert len(covered) == len(set(covered))
    uncovered = set(range(1, len(lines) + 1)) - set(covered)
    assert all(lines[number - 1].strip() == "" for number in uncovered)


def test_a_long_function_is_cut_into_windows_that_keep_its_name() -> None:
    body = "\n".join(f"    x = {n}" for n in range(150))
    pieces = chunk_file("big.py", f"def huge():\n{body}\n")

    assert [p.symbol for p in pieces] == ["huge", "huge", "huge"]
    assert spans(pieces) == [("huge", 1, 60), ("huge", 61, 120), ("huge", 121, 151)]


def test_code_that_does_not_parse_falls_back_to_line_windows() -> None:
    broken = "def oops(:\n" + "\n".join(f"line {n}" for n in range(50))

    pieces = chunk_file("broken.py", broken)

    assert spans(pieces) == [(None, 1, 40), (None, 41, 51)]


MARKDOWN = """# Ignite

Intro text.

## Installation

```bash
# not a heading: inside a code fence
pip install pytorch-ignite
```

### From source

Clone it.
"""


def test_markdown_is_split_at_headings_with_their_path() -> None:
    pieces = chunk_file("README.md", MARKDOWN)

    assert spans(pieces) == [
        ("Ignite", 1, 3),
        ("Ignite > Installation", 5, 10),
        ("Ignite > Installation > From source", 12, 14),
    ]


RST = """Concepts
========

Engine
------

The engine runs a loop.

Events
------

Handlers react to events.
"""


def test_restructured_text_is_split_at_underlined_headings() -> None:
    pieces = chunk_file("docs/concepts.rst", RST)

    assert spans(pieces) == [
        ("Concepts", 1, 2),
        ("Concepts > Engine", 4, 7),
        ("Concepts > Events", 9, 12),
    ]


def test_other_files_use_line_windows() -> None:
    text = "\n".join(f"key{n}: value" for n in range(45))

    assert spans(chunk_file("config.yaml", text)) == [(None, 1, 40), (None, 41, 45)]
