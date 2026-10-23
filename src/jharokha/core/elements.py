"""Text-less UI elements from pixels: icons and buttons (a taskbar's apps, an app's icon
rail, a compose bar's send and attach), found as compact blobs of colour that differ from
their pane's background, away from the text OCR read. No model: numpy only, about 30 ms
on a 1166 x 1181 frame. The boxes are unlabelled; what an element is comes from where it
sits ("the icon left of 'Type a message'")."""

from __future__ import annotations

import numpy as np

from jharokha.core.models import Box, Frame, Line, Word, union

# Largest channel difference from the pane's background that is still background: the
# codec leaves a flat area within a few levels, and panes inside a pane (an address box at
# 255 on a bar at 247) stay background.
_FG = 28
# Px of background that keep two parts apart. An icon's strokes and dots sit closer than
# this; separate buttons sit farther.
_GAP = 4
_MIN, _MAX = 7, 64  # px; an element's shorter side at least, its longer side at most
_ASPECT = 2.5  # longer side over shorter at most: dividers, scrollbars and words are longer
_FLAT = 4  # px; the shorter side of a flat glyph at least
_FILL = 0.08  # share of its box an element covers at least; thin diagonals are stray edges
_PAD = 3  # px around a word OCR read, so its antialiased edges go with it
_EDGE = 2  # px at each pane's edge, where a divider the pane cut ran
_SAMPLE = 3  # every 3rd pixel each way is enough to see a pane's background colour


def elements(frame: Frame, lines: list[Line], panes: list[Box] | None = None) -> list[Box]:
    """Boxes of the compact non-text things inside panes (default the whole frame), top to
    bottom, left to right."""
    h, w = frame.height, frame.width
    rgb = np.frombuffer(frame.bgra, np.uint8).reshape(h, w, 4)[..., :3]
    ink = np.zeros((h, w), bool)
    for px, py, pw, ph in panes or [(0, 0, w, h)]:
        x0, y0 = max(0, px + _EDGE), max(0, py + _EDGE)
        x1, y1 = min(w, px + pw - _EDGE), min(h, py + ph - _EDGE)
        if x1 - x0 < _MIN or y1 - y0 < _MIN:
            continue
        sub = rgb[y0:y1, x0:x1]
        bg = _background(sub)
        # A channel at a time in uint8, each side of the background clipped: a third of
        # the time of an int16 difference reduced across channels.
        hit = ink[y0:y1, x0:x1]
        for c in range(3):
            ch, lo, hi = sub[..., c], int(bg[c]) - _FG, int(bg[c]) + _FG
            if lo >= 0:
                hit |= ch <= lo
            if hi <= 255:
                hit |= ch >= hi
    spans, words = _text(lines)
    for x, y, bw, bh in spans:
        ink[max(0, y - _PAD) : y + bh + _PAD, max(0, x - _PAD) : x + bw + _PAD] = False
    found = [b for b in _blobs(ink) if _compact(b, ink)]
    # Words Windows OCR left out of a line (it drops a terminal's timestamps) are blobs the
    # height of the words on their row, with a word on one side and a word or another such
    # blob on the other. An icon stands at a line's start or end, or is taller than its text.
    # Each blob found to be text is a word to its neighbours, so a run of them goes too.
    while True:
        text = [b for b in found if _between(b, words, found)]
        if not text:
            break
        words += text
        found = [b for b in found if b not in text]
    return sorted(found, key=lambda b: (b[1], b[0]))


def _between(box: Box, spans: list[Box], blobs: list[Box]) -> bool:
    left, right = _beside(box, spans)
    if left and right:
        return True
    more_left, more_right = _beside(box, [b for b in blobs if b != box])
    return (left and more_right) or (right and more_left)


def _beside(box: Box, others: list[Box]) -> tuple[bool, bool]:
    """Whether a box of about this one's height sits on its row within a few heights to its
    left, and to its right."""
    x, y, w, h = box
    left = right = False
    for sx, sy, sw, sh in others:
        overlap = min(y + h, sy + sh) - max(y, sy)
        if overlap < 0.7 * min(h, sh) or abs(h - sh) > 0.2 * sh:
            continue
        left |= 0 <= x - (sx + sw) <= 4 * sh
        right |= 0 <= sx - (x + w) <= 4 * sh
    return left, right


# What Windows OCR makes of an icon: a ring or a filled tile is "o" or "O", a checkbox "D",
# a row of app tiles "ooo", a plus "4-", a close button "X", two arrows "><", a bin "Ü", a
# reply arrow "—l-". Besides these, any character but an ASCII letter or digit; no digits
# here, as "04" in a terminal is text.
_ICONISH = set("oOQDdxXlI")


def away_from_text(boxes: list[Box], lines: list[Line]) -> list[Box]:
    """The boxes less than half inside the text OCR read: for a detector that boxes words
    as elements too."""
    spans = _text(lines)[0]

    def inside(b: Box) -> int:
        x, y, w, h = b
        return sum(
            max(0, min(x + w, sx + sw) - max(x, sx)) * max(0, min(y + h, sy + sh) - max(y, sy))
            for sx, sy, sw, sh in spans
        )

    return [b for b in boxes if inside(b) < 0.5 * b[2] * b[3]]


def _text(lines: list[Line]) -> tuple[list[Box], list[Box]]:
    """The boxes to mask as text, and the words in them. A box is each run of words in a
    line that are not icons OCR tried to read, so the gaps between its words (and glyphs OCR
    left out of a word's box, as it does with a terminal's colons) go with it."""
    spans: list[Box] = []
    kept: list[Box] = []
    for line in lines:
        if not line.words:
            spans.append(line.box)
            kept.append(line.box)
            continue
        words = sorted(line.words, key=lambda wd: wd.box[0])
        run: list[Box] = []
        for i, wd in enumerate(words):
            if _icon_read(wd.box, wd.text, words, i):
                if run:
                    spans.append(union(run))
                run = []
            else:
                run.append(wd.box)
                kept.append(wd.box)
        if run:
            spans.append(union(run))
    return spans, kept


def _iconish(text: str) -> bool:
    return len(text) <= 3 and all(
        c in _ICONISH or not (c.isascii() and c.isalnum()) for c in text.replace("4-", "+")
    )


def _icon_read(box: Box, text: str, words: list[Word], i: int) -> bool:
    """A word that is an icon read as letters: at most three iconish characters, big enough
    to be found, and alone in its line, set apart from its neighbours by more than a space,
    or taller than the smallest text beside it."""
    x, _, w, h = box
    if min(w, h) < _FLAT or max(w, h) < _MIN or not _iconish(text):
        return False
    others = [wd.box[3] for j, wd in enumerate(words) if j != i and not _iconish(wd.text)]
    if not others:
        return True
    if h >= 1.3 * min(others):
        return True
    left = x - (words[i - 1].box[0] + words[i - 1].box[2]) if i > 0 else h
    right = words[i + 1].box[0] - (x + w) if i + 1 < len(words) else h
    return min(left, right) >= 0.6 * h


def _background(sub: np.ndarray) -> np.ndarray:
    """The pane's most common colour, to 3 bits a channel: a histogram of 32768 bins, not a
    sort of a million pixels."""
    s = sub[::_SAMPLE, ::_SAMPLE].reshape(-1, 3) >> 3
    key = (s[:, 0].astype(np.int32) << 10) | (s[:, 1].astype(np.int32) << 5) | s[:, 2]
    k = int(np.bincount(key, minlength=1 << 15).argmax())
    return np.array([(k >> 10) << 3 | 4, ((k >> 5) & 31) << 3 | 4, (k & 31) << 3 | 4], np.int16)


def _blobs(ink: np.ndarray) -> list[Box]:
    """Recursive XY cuts: split at every run of at least _GAP empty rows, then of empty
    columns, until nothing splits. UI is laid out on a grid, so this finds what connected
    regions merged by proximity would, with whole-array work at each step."""
    out: list[Box] = []
    stack: list[Box] = [(0, 0, ink.shape[1], ink.shape[0])]
    while stack:
        x, y, w, h = stack.pop()
        m = ink[y : y + h, x : x + w]
        rows = np.flatnonzero(m.any(axis=1))
        if not len(rows):
            continue
        cols = np.flatnonzero(m.any(axis=0))
        # Trim to the ink, so a box is tight before it is cut or kept.
        x, y = x + int(cols[0]), y + int(rows[0])
        w, h = int(cols[-1] - cols[0]) + 1, int(rows[-1] - rows[0]) + 1
        bands = _runs(rows - rows[0])
        if len(bands) > 1:
            stack += [(x, y + a, w, b - a) for a, b in bands]
            continue
        spans = _runs(cols - cols[0])
        if len(spans) > 1:
            stack += [(x + a, y, b - a, h) for a, b in spans]
            continue
        out.append((x, y, w, h))
    return out


def _runs(idx: np.ndarray) -> list[tuple[int, int]]:
    """Start and end of each group of indices no more than _GAP apart."""
    breaks = np.flatnonzero(np.diff(idx) > _GAP)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]] + 1
    return [(int(a), int(b)) for a, b in zip(starts, ends, strict=True)]


def _compact(box: Box, ink: np.ndarray) -> bool:
    x, y, w, h = box
    short, long = min(w, h), max(w, h)
    if long > _MAX or long < _MIN:
        return False
    # Flat glyphs, a "more" button's three dots or a dropdown's chevron, are as long as an
    # icon and a third to a quarter as tall.
    if short < _MIN and not (short >= _FLAT and long >= 2 * _FLAT + 2):
        return False
    if long > (_ASPECT if short >= _MIN else 4.5) * short:
        return False
    return float(ink[y : y + h, x : x + w].mean()) >= _FILL
