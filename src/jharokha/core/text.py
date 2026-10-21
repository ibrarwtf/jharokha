"""Turn OCR lines into something an agent can read, search and trust only as data."""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass

from jharokha.core.models import Box, Line, Word, union

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏ -‮⁠-⁤﻿]")
_LOOKALIKE = re.compile(r"[A-Za-z]\d|\d[A-Za-z]")
_SUSPECT = re.compile(r"\d|[A-Z]{3}")
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


def suspect(line: Line) -> bool:
    """Lines where a lookalike misread would hide: any digit, or a run of capitals (an ID
    misread as `INCOOI 2345` has no letter-digit mix left to flag)."""
    return bool(_SUSPECT.search(line.text))


def agree(a: str, b: str) -> bool:
    """Same reading apart from spacing, which the two engines split differently."""
    return "".join(a.split()) == "".join(b.split())


def apply_verified(lines: list[Line], alt: dict[int, str]) -> list[Line]:
    """Lines with their second reading where it differs. Word boxes survive when the word
    count matches; otherwise the line is one word."""
    out = []
    for i, ln in enumerate(lines):
        t = alt.get(i)
        if t is None or agree(t, ln.text):
            out.append(ln)
            continue
        parts = t.split()
        if len(parts) == len(ln.words):
            words = tuple(Word(p, w.box) for p, w in zip(parts, ln.words, strict=True))
        else:
            words = (Word(t, ln.box),)
        out.append(Line(t, ln.box, words))
    return out


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


def format_concise(
    lines: list[Line], offset: int = 0, limit: int = 200, alt: dict[int, str] | None = None
) -> str:
    """alt holds second readings by line index; one that disagrees is shown after a `|`."""
    alt = alt or {}
    page, note = _page(lines, offset, limit)
    out = []
    for n, ln in enumerate(page, offset):
        text = clean(ln.text)
        row = f"L{n} [{ln.box[0]},{ln.box[1]}] {text}"
        if n in alt and not agree(alt[n], text):
            row += f" | verify: {alt[n]}"
        out.append(row)
    return wrap_untrusted("\n".join(out) or "(no text)") + note


def format_detailed(
    lines: list[Line], offset: int = 0, limit: int = 200, alt: dict[int, str] | None = None
) -> str:
    alt = alt or {}
    page, note = _page(lines, offset, limit)
    out = [
        {
            "ref": f"L{n}",
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
            **({"verify": alt[n]} if n in alt else {}),
        }
        for n, ln in enumerate(page, offset)
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


def _match(ref: str, words: tuple[Word, ...]) -> Match:
    box = union(w.box for w in words)
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
