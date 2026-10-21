"""Turn OCR lines into something an agent can read, search and trust only as data."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass

from jharokha.core.models import Box, Line, Word

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏ -‮⁠-⁤﻿]")
_LOOKALIKE = re.compile(r"[A-Za-z]\d|\d[A-Za-z]")
# OCR confuses these; fold both sides of a search to the same letter.
_FOLD = str.maketrans("0|1!5$8", "oll1ssb")
_LOCK_HINTS = (
    "press ctrl+alt+del",
    "ctrl + alt + delete",
    "ctrl+alt+delete to unlock",
    "pc is locked",
)


def clean(s: str) -> str:
    return _CONTROL.sub("", s)


def reading_order(lines: list[Line]) -> list[Line]:
    """Panels before rows: a blank band running the full height of a region (three line
    heights or wider) splits it into side-by-side columns read one after the other, a blank
    band running the full width splits it into stacked blocks; inside, rows top to bottom and
    left to right. That keeps a chat list separate from the thread beside it."""
    if len(lines) < 2:
        return list(lines)
    heights = sorted(ln.box[3] for ln in lines)
    h = heights[len(heights) // 2]
    cut = _cut(lines, 0, 3 * h) or _cut(lines, 1, 1.5 * h)
    if cut:
        return reading_order(cut[0]) + reading_order(cut[1])
    return _rows(lines)


def _cut(lines: list[Line], axis: int, min_gap: float) -> tuple[list[Line], list[Line]] | None:
    """Split at the widest empty band along axis (0 = x, 1 = y) if it is at least min_gap."""
    spans = sorted((ln.box[axis], ln.box[axis] + ln.box[axis + 2]) for ln in lines)
    end, best = spans[0][1], (0.0, 0)
    for start, stop in spans[1:]:
        if start - end >= max(min_gap, best[0]):
            best = (start - end, end)
        end = max(end, stop)
    if not best[0]:
        return None
    before = [ln for ln in lines if ln.box[axis] + ln.box[axis + 2] <= best[1]]
    after = [ln for ln in lines if ln.box[axis] + ln.box[axis + 2] > best[1]]
    return before, after


def _rows(lines: list[Line]) -> list[Line]:
    """Two lines share a row when their vertical centres are closer than half a line height."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda ln: ln.box[1]):
        cy = ln.box[1] + ln.box[3] / 2
        for row in rows:
            r = row[-1]
            if abs(cy - (r.box[1] + r.box[3] / 2)) < max(r.box[3], ln.box[3]) / 2:
                row.append(ln)
                break
        else:
            rows.append([ln])
    return [ln for row in rows for ln in sorted(row, key=lambda ln: ln.box[0])]


def lookalike(word: str) -> bool:
    return bool(_LOOKALIKE.search(word))


def wrap_untrusted(text: str) -> str:
    return f'<screen_text source="ocr" untrusted="true">\n{text}\n</screen_text>'


def _page(lines: list[Line], offset: int, limit: int) -> tuple[list[Line], str]:
    page = lines[offset : offset + limit]
    end = offset + len(page)
    note = ""
    if end < len(lines):
        note = (
            f"\n[truncated: lines {offset}-{end - 1} of {len(lines)}; "
            f"pass offset={end}, a region, or use jharokha_find_text]"
        )
    return page, note


def format_concise(lines: list[Line], offset: int = 0, limit: int = 200) -> str:
    page, note = _page(lines, offset, limit)
    body = "\n".join(
        f"L{offset + i} [{ln.box[0]},{ln.box[1]}] {clean(ln.text)}" for i, ln in enumerate(page)
    )
    return wrap_untrusted(body or "(no text)") + note


def format_detailed(lines: list[Line], offset: int = 0, limit: int = 200) -> str:
    page, note = _page(lines, offset, limit)
    out = [
        {
            "ref": f"L{offset + i}",
            "text": clean(ln.text),
            "box": ln.box,
            "words": [
                {
                    "text": clean(w.text),
                    "box": w.box,
                    **({"lookalike": True} if lookalike(w.text) else {}),
                }
                for w in ln.words
            ],
            "conf": None,
        }
        for i, ln in enumerate(page)
    ]
    return wrap_untrusted(json.dumps(out, separators=(",", ":"))) + note


@dataclass(frozen=True)
class Match:
    ref: str
    text: str
    box: Box
    center: tuple[int, int]


def _fold(s: str) -> str:
    return clean(s).lower().translate(_FOLD)


def _span_box(words: tuple[Word, ...]) -> Box:
    x1 = min(w.box[0] for w in words)
    y1 = min(w.box[1] for w in words)
    x2 = max(w.box[0] + w.box[2] for w in words)
    y2 = max(w.box[1] + w.box[3] for w in words)
    return (x1, y1, x2 - x1, y2 - y1)


def _match(ref: str, words: tuple[Word, ...]) -> Match:
    box = _span_box(words)
    return Match(
        ref,
        clean(" ".join(w.text for w in words)),
        box,
        (box[0] + box[2] // 2, box[1] + box[3] // 2),
    )


def find(lines: list[Line], query: str, regex: bool = False, all: bool = False) -> list[Match]:
    """Word spans matching the query. Plain queries fold case and OCR lookalikes and fall back
    to fuzzy (ratio >= 0.8) only when nothing matches exactly. Regex runs on the raw line."""
    exact: list[Match] = []
    fuzzy: list[Match] = []
    n = max(1, len(query.split()))
    pat = re.compile(query, re.IGNORECASE) if regex else None
    q = _fold(query)
    for i, ln in enumerate(lines):
        ref = f"L{i}"
        if pat:
            joined = clean(" ".join(w.text for w in ln.words))
            if m := pat.search(joined):
                # Words whose character span overlaps the match.
                pos, hit = 0, []
                for w in ln.words:
                    end = pos + len(clean(w.text))
                    if pos < m.end() and end > m.start():
                        hit.append(w)
                    pos = end + 1
                exact.append(_match(ref, tuple(hit) or ln.words))
            continue
        spans = [
            (size, ln.words[s : s + size])
            for size in (n, n + 1)
            for s in range(len(ln.words) - size + 1)
        ]
        for size, span in spans:
            folded = _fold(" ".join(w.text for w in span))
            if q in folded:
                exact.append(_match(ref, span))
                break
            if size == n and difflib.SequenceMatcher(None, q, folded).ratio() >= 0.8:
                fuzzy.append(_match(ref, span))
                break
    found = exact or fuzzy
    return found if all else found[:1]


def looks_locked(lines: list[Line]) -> bool:
    text = " ".join(ln.text for ln in lines).lower()
    return any(h in text for h in _LOCK_HINTS)
