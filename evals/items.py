"""The readers axis: what a region's reader should return (the generators' truth items) and
how close what it returned is.

Truth per region name ("teams.thread"): {"kind": messages | entries | mails, "items": [...]}
with the fields an agent reads (sender, time, text, own; title, subtitle, text, time), or
{"kind": "table", "header": [...], "rows": [[...], ...]}. Fields and cells compare exactly
after folding case and runs of spaces."""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from typing import Any

FIELDS = {
    "messages": ("sender", "time", "text", "own"),
    "entries": ("title", "subtitle", "time"),
    "mails": ("title", "subtitle", "text", "time"),
}
MATCH = 0.8  # how alike two items' fields, joined, must be to be the same item


def norm(v: object) -> str:
    return " ".join(str(v).casefold().split())


def cut(s: str, n: int) -> str:
    """What the sims' fit() leaves of s once a reader drops its `...`."""
    return s if len(s) <= n else s[: n - 3].rstrip()


def listed(kind: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": kind, "items": items}


def table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> dict[str, Any]:
    return {"kind": "table", "header": list(header), "rows": [list(r) for r in rows]}


def _joined(it: dict[str, Any], fields: Sequence[str]) -> str:
    return " | ".join(norm(it.get(f, "")) for f in fields)


def score_list(kind: str, truth: list[dict[str, Any]], got: list[dict[str, Any]]) -> dict[str, Any]:
    """Item precision and recall (items paired in order, each at most once, by how alike
    their fields read) and field accuracy over the pairs."""
    fields = FIELDS[kind]
    used: set[int] = set()
    pairs = []
    for t in truth:
        tj = _joined(t, fields)
        best, at = 0.0, None
        for i, g in enumerate(got):
            if i in used:
                continue
            r = difflib.SequenceMatcher(None, tj, _joined(g, fields)).ratio()
            if r > best:
                best, at = r, i
        if at is not None and best >= MATCH:
            used.add(at)
            pairs.append((t, got[at]))
    right = sum(norm(t.get(f, "")) == norm(g.get(f, "")) for t, g in pairs for f in fields)
    out: dict[str, Any] = {
        "items_precision": len(pairs) / len(got) if got else float(not truth),
        "items_recall": len(pairs) / len(truth) if truth else 1.0,
        "fields_exact": right / (len(pairs) * len(fields)) if pairs else 0.0,
    }
    misses = [_joined(t, fields) for t in truth if all(t is not p[0] for p in pairs)]
    if misses:
        out["item_misses"] = misses
    return out


ROW_MATCH = 0.5  # how alike two rows, joined, must be to be the same row


def score_table(truth: dict[str, Any], got: dict[str, Any] | None) -> dict[str, Any]:
    """Cells right over the truth's filled cells and over the filled cells read, the header
    row's included. A column is the one with the same header (by position when a header was
    misread and both have as many columns); a row is the read row most like it, each read
    row paired once, in order. So one misread cell costs that cell, not its row or column."""
    t_head, t_rows = (
        [norm(v) for v in truth["header"]],
        [[norm(v) for v in r] for r in truth["rows"]],
    )
    g_head = [norm(v) for v in (got or {}).get("header", [])]
    g_rows = [[norm(v) for v in r] for r in (got or {}).get("rows", [])]
    width = max([len(t_head), *map(len, t_rows)], default=0)
    g_width = max([len(g_head), *map(len, g_rows)], default=0)

    def column(c: int) -> int | None:
        if c < len(t_head) and t_head[c] and t_head[c] in g_head:
            return g_head.index(t_head[c])
        return c if width == g_width else None

    def cell(row: list[str], c: int | None) -> str:
        return row[c] if c is not None and c < len(row) else ""

    want = sum(map(bool, t_head)) + sum(bool(v) for r in t_rows for v in r)
    have = sum(map(bool, g_head)) + sum(bool(v) for r in g_rows for v in r)
    hit = sum(bool(v) and v == cell(g_head, c) for c, v in enumerate(t_head))
    misses = [f"header #{c}: {v}" for c, v in enumerate(t_head) if v and v != cell(g_head, c)]

    def content(row: list[str], cols: list[int | None] | None = None) -> str:
        """A row's filled cells with their columns, so empty cells do not make rows alike."""
        at = cols or list(range(len(row)))
        return " | ".join(f"{at[c]}:{v}" for c, v in enumerate(row) if v)

    # The most alike pairs first, so a row with little in it (an hour label) does not take
    # the read row another truth row matches exactly.
    alike = sorted(
        (
            (
                difflib.SequenceMatcher(
                    None, content(row, [column(c) for c in range(len(row))]), content(g)
                ).ratio(),
                t,
                i,
            )
            for t, row in enumerate(t_rows)
            for i, g in enumerate(g_rows)
        ),
        reverse=True,
    )
    pair: dict[int, int] = {}
    for r, t, i in alike:
        if r >= ROW_MATCH and t not in pair and i not in pair.values():
            pair[t] = i
    for t, row in enumerate(t_rows):
        g = g_rows[pair[t]] if t in pair else []
        for c, v in enumerate(row):
            if v and v == cell(g, column(c)):
                hit += 1
            elif v:
                misses.append(f"{row[0] or '?'} #{c}: {v}")
    out: dict[str, Any] = {
        "cells_recall": hit / want if want else 1.0,
        "cells_precision": hit / have if have else float(not want),
    }
    if misses:
        out["cell_misses"] = misses[:10]
    return out
