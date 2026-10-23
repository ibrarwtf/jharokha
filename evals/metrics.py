"""Scores for text read off a screen against the text that was drawn.

CER and WER are edit distances divided by the length of the truth, the usual OCR measures.
Both are computed on the lines joined in reading order, so a column read in the wrong
place costs as much as misread text. Bag-of-words F1 ignores order: a high F1 with a high
CER means the words were read right and put in the wrong order."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

LOOKALIKES = set("0O1lI5S8B")


def distance(a: Sequence[object], b: Sequence[object]) -> int:
    """Levenshtein distance: insertions, deletions and substitutions, each costing 1."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def cer(truth: list[str], got: list[str]) -> float:
    t = "\n".join(truth)
    return distance(t, "\n".join(got)) / max(1, len(t))


def wer(truth: list[str], got: list[str]) -> float:
    t = " ".join(truth).split()
    return distance(t, " ".join(got).split()) / max(1, len(t))


def bag_f1(truth: list[str], got: list[str]) -> float:
    t, g = Counter(" ".join(truth).split()), Counter(" ".join(got).split())
    hit = sum((t & g).values())
    if not hit:
        return 0.0
    p, r = hit / sum(g.values()), hit / sum(t.values())
    return 2 * p * r / (p + r)


def lines_exact(truth: list[str], got: list[str]) -> float:
    """Share of truth lines read exactly, in any order."""
    pool = Counter(got)
    hit = 0
    for ln in truth:
        if pool[ln]:
            pool[ln] -= 1
            hit += 1
    return hit / max(1, len(truth))


def lookalike_misses(truth: list[str], got: list[str]) -> int:
    """Characters from the 0/O, 1/l/I, 5/S, 8/B families that were not read as drawn."""
    import difflib

    t, g = "\n".join(truth), "\n".join(got)
    ops = difflib.SequenceMatcher(None, t, g, autojunk=False).get_opcodes()
    return sum(
        1 for tag, i1, i2, _, _ in ops if tag != "equal" for c in t[i1:i2] if c in LOOKALIKES
    )
