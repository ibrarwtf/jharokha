"""What a named region reads as when an agent asks for items instead of lines. Each region of
an app's view names its reader (core.apps):

- text: blocks of text, a blank of more than a line apart, and any table among them (three
  or more rows of two or more cells whose columns line up) as a table item
- grid: a terminal's rows, one item a row
- list: messages, entries or mails (core.listread), as the region's `kind` says
- table: one table item, rows and columns from aligned cells, the header row told apart
- form: label and value pairs, from a `Label: value` row or a label column beside (or a
  label above) its value

All of them take the lines of a read (the region's, or the whole screen's) and the region's
box, and keep only the lines whose centre is inside it. refs index the lines given, as the
`L<n>` of a lines read of the same frame."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jharokha.core import listread
from jharokha.core.models import Box, Line, union
from jharokha.core.text import clean, wrap_untrusted

READERS = ("text", "grid", "list", "table", "form")
KINDS = listread.KINDS
# Readers whose items say more than their lines; the others read as lines unless asked.
ITEM_READERS = ("list", "table", "form")

Cell = tuple[int, Line]  # index into the lines given, and the line


_GONE = Line("", (-(10**6), -(10**6), 0, 0))  # inside no region


def items(
    reader: str,
    lines: Sequence[Line],
    box: Box,
    kind: str | None = None,
    exclude: Sequence[Box] = (),
) -> list[Any]:
    """The region's items as the reader makes them: listread Items for a list, dicts for
    the others (table, text block, row, field). Lines inside an exclude box (a region nested
    in this one, such as a compose box) are left out."""
    # Left out by moving them off screen, so refs still index the lines given.
    lines = [ln if not any(_inside([ln], b) for b in exclude) else _GONE for ln in lines]
    if reader == "list":
        return listread.items(lines, box, kind or "entries")
    if reader == "table":
        t = table(_inside(lines, box))
        return [t] if t else []
    if reader == "form":
        return form(_inside(lines, box))
    if reader == "grid":
        return [
            {"text": clean(ln.text), "box": ln.box, "refs": [f"L{i}"]}
            for i, ln in _inside(lines, box)
            if ln.text.strip()
        ]
    return blocks(_inside(lines, box))


def as_dicts(its: Sequence[Any]) -> list[dict[str, Any]]:
    return [it.as_dict() if hasattr(it, "as_dict") else it for it in its]


def _concise(v: Any) -> Any:
    """v without refs and without empty or false fields: a third fewer tokens a message."""
    if isinstance(v, dict):
        return {
            k: _concise(x)
            for k, x in v.items()
            if k != "refs" and not (x is False or x is None or x == "" or x == [])
        }
    if isinstance(v, list):
        return [_concise(x) for x in v]
    return v


def format_items(
    its: Sequence[Any], offset: int = 0, limit: int = 200, detailed: bool = False
) -> str:
    """The items as JSON inside the untrusted wrapper, paged as lines are. Concise leaves out
    refs and fields that are empty or false; detailed keeps every field."""
    page = as_dicts(its[offset : offset + limit])
    if not detailed:
        page = _concise(page)
    out = wrap_untrusted(json.dumps(page, separators=(",", ":")))
    end = offset + len(page)
    if end < len(its):
        out += f"\n[truncated: items {offset}-{end - 1} of {len(its)}; pass offset={end}]"
    return out


def _inside(lines: Sequence[Line], box: Box) -> list[Cell]:
    x0, y0, w, h = box
    return [
        (i, ln)
        for i, ln in enumerate(lines)
        if x0 <= ln.box[0] + ln.box[2] / 2 < x0 + w and y0 <= ln.box[1] + ln.box[3] / 2 < y0 + h
    ]


def _height(cells: Sequence[Cell]) -> float:
    hs = sorted(ln.box[3] for _, ln in cells)
    return float(hs[len(hs) // 2]) if hs else 12.0


def _bands(cells: Sequence[Cell]) -> list[list[Cell]]:
    """Rows of cells: a cell joins the row above when they overlap by half the smaller
    height; left to right inside a row."""
    out: list[list[Cell]] = []
    span = (0, 0)
    for c in sorted(cells, key=lambda c: (c[1].box[1], c[1].box[0])):
        top, bottom = c[1].box[1], c[1].box[1] + c[1].box[3]
        if out:
            overlap = min(bottom, span[1]) - max(top, span[0])
            if overlap >= 0.5 * min(c[1].box[3], span[1] - span[0]):
                out[-1].append(c)
                span = (min(top, span[0]), max(bottom, span[1]))
                continue
        out.append([c])
        span = (top, bottom)
    return [sorted(b, key=lambda c: c[1].box[0]) for b in out]


def _columns(cells: Sequence[Cell], h: float) -> list[int]:
    """Left edges of the columns: cells' left edges, those within a little of each other
    taken as one column (a cell's text starts a few pixels in, or not)."""
    tol = max(10.0, 0.8 * h)
    xs = sorted(ln.box[0] for _, ln in cells)
    cols: list[int] = []
    prev = None
    for x in xs:
        if prev is None or x - prev > tol:
            cols.append(x)
        prev = x
    return cols


def _col(x: int, cols: Sequence[int]) -> int:
    return max(i for i, c in enumerate(cols) if c <= x) if x >= cols[0] else 0


_DIGIT = re.compile(r"\d")


# Tables


@dataclass(frozen=True)
class Table:
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    box: Box
    refs: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": {"header": list(self.header), "rows": [list(r) for r in self.rows]},
            "box": self.box,
            "refs": [f"L{i}" for i in self.refs],
        }


def table(cells: Sequence[Cell]) -> Table | None:
    """The cells as a table. Columns are the cells' left edges lined up. Rows: when the first
    column holds a cell on most rows (an hour on a calendar, an ID in a queue), each of those
    starts a row that takes every cell starting below it and above the next, so a cell
    drawn a few pixels lower still lands in its row; the cells above the first are the
    header. Otherwise rows are the cells side by side, and the first row is the header when
    it has no digits and the rows below do."""
    if len(cells) < 2:
        return None
    h = _height(cells)
    # An icon or the edge of a drawn box reads as a glyph or two taller than the text
    # (`_l_`, `4`), and would start a column of its own.
    cells = [c for c in cells if not _junk(c[1], h)]
    if len(cells) < 2:
        return None
    cols = _columns(cells, h)
    bands = _bands(cells)
    first = [c for c in cells if _col(c[1].box[0], cols) == 0]
    tops = [b[0][1].box[1] for b in _bands(first)]
    pitch = _pitch(tops, h)
    rows: list[list[Cell]]
    head: list[Cell] = []
    if len(cols) >= 2 and len(tops) >= 3 and (pitch or len(tops) >= 0.6 * len(bands)):
        slack = 0.5 * h
        if pitch:
            # Rows a fixed pitch apart: one for every pitch down to the last cell, so a row
            # whose first cell OCR missed (a faint hour) still holds its cells.
            last = max(c[1].box[1] for c in cells)
            top = tops[0]
            # Up too, while cells start in the upper part of the row above: the header sits
            # closer above the first row than that.
            while any(top - pitch - slack <= c[1].box[1] < top - pitch / 2 for c in cells):
                top -= pitch
            tops = [top + k * pitch for k in range(int((last - top + slack) // pitch) + 1)]
        rows = [[] for _ in tops]
        for c in cells:
            y = c[1].box[1]
            if y < tops[0] - slack:
                head.append(c)
                continue
            rows[max(i for i, t in enumerate(tops) if t - slack <= y)].append(c)
    else:
        rows = [list(b) for b in bands]
    grid = [_lay(r, cols) for r in rows]
    header: list[str] = []
    if head:
        header = _lay(head, cols)
    elif len(grid) >= 2 and _looks_header(grid[0], grid[1:]):
        header, grid = grid[0], grid[1:]
    return Table(
        tuple(header),
        tuple(tuple(r) for r in grid if any(r)),
        union(ln.box for _, ln in cells),
        tuple(sorted(i for i, _ in cells)),
    )


def _junk(ln: Line, h: float) -> bool:
    t = ln.text.strip()
    return len(t) <= 3 and (ln.box[3] > 1.3 * h or 2 * sum(ch.isalnum() for ch in t) < len(t))


def _pitch(tops: Sequence[float], h: float) -> float | None:
    """The fixed distance between rows when the first column's cells are spaced by whole
    multiples of one (within 15%) taller than a line and a half; None otherwise."""
    diffs = [b - a for a, b in zip(tops, tops[1:], strict=False)]
    if len(diffs) < 2:
        return None
    p = min(diffs)
    if p < 1.5 * h or any(abs(d / p - round(d / p)) > 0.15 for d in diffs):
        return None
    fit = [(t - tops[0]) / round((t - tops[0]) / p) for t in tops[1:]]
    return sum(fit) / len(fit)


def _lay(cells: Sequence[Cell], cols: Sequence[int]) -> list[str]:
    out = [""] * len(cols)
    for _, ln in sorted(cells, key=lambda c: (c[1].box[1], c[1].box[0])):
        k = _col(ln.box[0], cols)
        t = " ".join(clean(ln.text).split())
        out[k] = f"{out[k]} {t}" if out[k] else t
    return out


def _looks_header(first: Sequence[str], body: Sequence[Sequence[str]]) -> bool:
    filled = [t for t in first if t]
    if len(filled) < 2 or any(_DIGIT.search(t) for t in filled):
        return False
    with_digits = sum(any(_DIGIT.search(t) for t in row) for row in body)
    return with_digits * 3 >= len(body)


# Text, with any tables in it


def blocks(cells: Sequence[Cell]) -> list[dict[str, Any]]:
    """Blocks of text a blank of more than a line apart, and the tables among them: runs of
    three or more rows of two or more cells whose columns mostly line up."""
    if not cells:
        return []
    h = _height(cells)
    bands = _bands(cells)
    out: list[dict[str, Any]] = []
    i = 0
    block: list[list[Cell]] = []

    def flush() -> None:
        if block:
            flat = [c for b in block for c in b]
            out.append(
                {
                    "text": "\n".join(_joined(b) for b in block),
                    "box": union(ln.box for _, ln in flat),
                    "refs": [f"L{k}" for k in sorted(k for k, _ in flat)],
                }
            )
            block.clear()

    while i < len(bands):
        run = _table_run(bands, i, h)
        if run:
            flush()
            t = table([c for b in bands[i : i + run] for c in b])
            if t:
                out.append(t.as_dict())
            i += run
            continue
        b = bands[i]
        if block:
            prev = block[-1]
            gap = min(ln.box[1] for _, ln in b) - max(ln.box[1] + ln.box[3] for _, ln in prev)
            if gap > h:
                flush()
        block.append(b)
        i += 1
    flush()
    return out


def _joined(band: Sequence[Cell]) -> str:
    return " ".join(" ".join(clean(ln.text).split()) for _, ln in band)


def _table_run(bands: Sequence[Sequence[Cell]], i: int, h: float) -> int:
    """How many rows from bands[i] on make a table: rows of two or more cells in a row,
    with at least two columns that most of them have a cell in. 0 when under three."""
    n = 0
    while i + n < len(bands) and len(bands[i + n]) >= 2:
        n += 1
    while n >= 3:
        run = bands[i : i + n]
        cols = _columns([c for b in run for c in b], h)
        hits = [0] * len(cols)
        for b in run:
            for k in {_col(ln.box[0], cols) for _, ln in b}:
                hits[k] += 1
        if sum(x >= 0.6 * n for x in hits) >= 2:
            return n
        n -= 1
    return 0


# Forms


def form(cells: Sequence[Cell]) -> list[dict[str, Any]]:
    """Label and value pairs: a row `Label: value`, a row of a label and its value beside it
    (the labels' column), or a label alone with its value on the row below."""
    out: list[dict[str, Any]] = []
    waiting: dict[str, Any] | None = None
    for b in _bands(cells):
        texts = [" ".join(clean(ln.text).split()) for _, ln in b]
        refs = [f"L{k}" for k, _ in b]
        box = union(ln.box for _, ln in b)
        if len(texts) >= 2:
            label, value = texts[0].rstrip(":").strip(), " ".join(texts[1:])
        elif m := re.fullmatch(r"([^:]{1,40}?)\s*:\s*(.*)", texts[0]):
            label, value = m[1], m[2]
        elif waiting is not None:
            waiting["value"] = texts[0]
            waiting["refs"] += refs
            waiting["box"] = union((waiting["box"], box))
            waiting = None
            continue
        else:
            label, value = texts[0], ""
        field = {"label": label, "value": value, "box": box, "refs": refs}
        out.append(field)
        waiting = field if not value else None
    return out
