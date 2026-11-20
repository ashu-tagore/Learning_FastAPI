"""Cutting source files into chunks along their structure (lesson 6.5).

A chunk is what search returns and what the model reads as context, so it should be a
whole thought: a function, a method, a section under a heading. Fixed-size windows cut
functions in half and glue the end of one to the start of the next.

    Python      one chunk per top-level function, per class header, per method
    Markdown    one chunk per heading (outside code fences)
    rst         one chunk per underlined heading
    other       40-line windows

Every chunk records its symbol ("Engine.run", "Ignite > Installation") and exact lines.
Pieces longer than MAX_LINES are cut into windows that keep the symbol.
"""

import ast
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

WINDOW_LINES = 40
MAX_LINES = 60


@dataclass(frozen=True)
class Piece:
    symbol: str | None
    start_line: int  # 1-based, inclusive
    end_line: int
    text: str


def chunk_file(path: str, text: str) -> list[Piece]:
    lines = text.splitlines()
    suffix = PurePosixPath(path).suffix
    if suffix == ".py":
        spans = python_spans(text)
    elif suffix == ".md":
        spans = markdown_spans(lines)
    elif suffix == ".rst":
        spans = rst_spans(lines)
    else:
        spans = None
    if spans is None:
        return windows(lines, None, 1, len(lines), WINDOW_LINES)

    pieces: list[Piece] = []
    for symbol, start, end in spans:
        start, end = trim_blank_edges(lines, start, end)
        if start > end:
            continue
        pieces.extend(windows(lines, symbol, start, end, MAX_LINES))
    return pieces


def windows(lines: list[str], symbol: str | None, start: int, end: int, size: int) -> list[Piece]:
    pieces = []
    for window_start in range(start, end + 1, size):
        window_end = min(window_start + size - 1, end)
        text = "\n".join(lines[window_start - 1 : window_end])
        pieces.append(Piece(symbol, window_start, window_end, text))
    return pieces


def trim_blank_edges(lines: list[str], start: int, end: int) -> tuple[int, int]:
    while start <= end and not lines[start - 1].strip():
        start += 1
    while end >= start and not lines[end - 1].strip():
        end -= 1
    return start, end


# A span is (symbol, first line, last line), before blank edges are trimmed.
Span = tuple[str | None, int, int]

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def first_line(node: ast.stmt) -> int:
    """Where a definition starts: its first decorator, if it has any."""
    decorators: list[ast.expr] = getattr(node, "decorator_list", [])
    return min([node.lineno, *(d.lineno for d in decorators)])


def last_line(node: ast.stmt) -> int:
    return node.end_lineno or node.lineno


def python_spans(text: str) -> list[Span] | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None  # half-written or Python 2 code: line windows instead

    spans: list[Span] = []
    line_count = len(text.splitlines())
    next_free = 1  # first line not yet in a span
    for node in tree.body:
        if not isinstance(node, DEFINITIONS):
            continue
        start = first_line(node)
        if start > next_free:
            spans.append((None, next_free, start - 1))  # module-level code before it
        if isinstance(node, ast.ClassDef):
            spans.extend(class_spans(node))
        else:
            spans.append((node.name, start, last_line(node)))
        next_free = last_line(node) + 1
    if next_free <= line_count:
        spans.append((None, next_free, line_count))
    return spans


def class_spans(node: ast.ClassDef) -> list[Span]:
    """The class header (up to its first method), then each method, then anything else."""
    methods = [
        item for item in node.body if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    end = last_line(node)
    if not methods:
        return [(node.name, first_line(node), end)]

    spans: list[Span] = [(node.name, first_line(node), first_line(methods[0]) - 1)]
    for method, following in zip(methods, [*methods[1:], None], strict=True):
        method_end = last_line(method)
        spans.append((f"{node.name}.{method.name}", first_line(method), method_end))
        # Class-level code between this method and the next stays with the class.
        gap_end = first_line(following) - 1 if following is not None else end
        if gap_end > method_end:
            spans.append((node.name, method_end + 1, gap_end))
    return spans


MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def markdown_spans(lines: list[str]) -> list[Span]:
    headings: list[tuple[int, int, str]] = []  # (line, level, title)
    in_fence = False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        match = None if in_fence else MARKDOWN_HEADING.match(line)
        if match:
            headings.append((number, len(match.group(1)), match.group(2)))
    return sections(headings, len(lines))


RST_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+:.])\1+\s*$")


def rst_spans(lines: list[str]) -> list[Span]:
    """reStructuredText marks a heading by underlining it; the order in which underline
    characters first appear sets the levels (the first one seen is level 1)."""
    levels: dict[str, int] = {}
    headings: list[tuple[int, int, str]] = []
    for number in range(1, len(lines)):
        title, underline = lines[number - 1], lines[number]
        match = RST_UNDERLINE.match(underline)
        is_heading = (
            match is not None and title.strip() and len(underline.strip()) >= len(title.strip())
        )
        if not is_heading:
            continue
        assert match is not None
        level = levels.setdefault(match.group(1), len(levels) + 1)
        headings.append((number, level, title.strip()))
    return sections(headings, len(lines))


def sections(headings: list[tuple[int, int, str]], line_count: int) -> list[Span]:
    """Turn headings into spans that run to the next heading, named by their heading path."""
    spans: list[Span] = []
    first_heading = headings[0][0] if headings else line_count + 1
    if first_heading > 1:
        spans.append((None, 1, first_heading - 1))
    path: list[tuple[int, str]] = []  # (level, title) of the enclosing headings
    for index, (line, level, title) in enumerate(headings):
        path = [entry for entry in path if entry[0] < level]
        path.append((level, title))
        end = headings[index + 1][0] - 1 if index + 1 < len(headings) else line_count
        spans.append((" > ".join(name for _, name in path), line, end))
    return spans
