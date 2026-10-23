"""Which app and view is on screen, scored from the evidence each definition names.

Every (app, view) pair is scored on the app's cues and the view's together: the weighted
share of them present. Text and regex cues count fully where they belong (within `near` of
an `at` position, which calibration replaces with where the anchor was measured) and
ELSEWHERE where they are on screen somewhere else; a background cue counts when the
screen's main colour is within COLOUR of it; a pane cue when a pane found from the pixels
overlaps its box by PANE_MATCH. The best pair is the answer. Its confidence is its score,
halved when the runner-up scores the same and full once the runner-up is 0.5 behind. Below
THRESHOLD nothing is recognised and the screen is `generic`: panes only, with confidence
1 - the best score."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from jharokha.core.apps import GENERIC, RGB, App, Cue
from jharokha.core.layout import to_box
from jharokha.core.models import Box, Line, iou
from jharokha.core.text import find, fold, normalize

THRESHOLD = 0.5
ELSEWHERE = 0.3
COLOUR = 12
PANE_MATCH = 0.6


@dataclass(frozen=True)
class Identity:
    app: str | None  # None on a lock screen
    view: str | None
    confidence: float
    ready: bool | None = None  # None: the view names no ready cue
    runner_up: tuple[str, str, float] | None = None  # app, view, score
    facts: dict[str, str] = field(default_factory=dict)  # untrusted screen text, e.g. url
    found: dict[str, Box] = field(default_factory=dict)  # cue text -> where it was seen
    scores: dict[str, float] = field(default_factory=dict)  # "app.view" -> score

    @property
    def known(self) -> bool:
        return self.app is not None and self.app != GENERIC


LOCKED = Identity(None, None, 1.0)


class _Evidence:
    """The screen, and each cue scored on it once."""

    def __init__(
        self, lines: list[Line], panes: list[Box], background: RGB | None, w: int, h: int
    ) -> None:
        self.lines, self.panes, self.background, self.w, self.h = lines, panes, background, w, h
        self.folded = [fold(ln.text).replace(" ", "") for ln in lines]
        self.seen: dict[Cue, float] = {}
        self.found: dict[str, Box] = {}
        self.reported: dict[Cue, str] = {}

    def hits(self, cue: Cue) -> list[tuple[Box, str]]:
        out: list[tuple[Box, str]] = []
        if cue.regex:
            pat = re.compile(cue.regex, re.IGNORECASE)
            for ln in self.lines:
                if pat.search(ln.text) or pat.search(normalize(ln.text)):
                    m = find([ln], cue.regex, regex=True)
                    # What is reported is matched without spaces: OCR splits a URL into
                    # words (`https://pricing.exa mple.net/...`).
                    whole = pat.search(re.sub(r"\s+", "", ln.text))
                    text = whole.group() if whole else m[0].text if m else ln.text
                    out.append((m[0].box if m else ln.box, text))
            return out
        for alt in cue.text:
            q = fold(alt).replace(" ", "")
            for ln, f in zip(self.lines, self.folded, strict=True):
                if q and q in f:
                    m = find([ln], alt, fuzzy=False)
                    out.append((m[0].box, m[0].text) if m else (ln.box, ln.text))
        return out

    def score(self, cue: Cue) -> float:
        if cue in self.seen:
            return self.seen[cue]
        s = 0.0
        if cue.background is not None:
            bg = self.background
            s = float(
                bg is not None
                and max(abs(a - b) for a, b in zip(bg, cue.background, strict=True)) <= COLOUR
            )
        elif cue.pane is not None:
            want = to_box(cue.pane, self.w, self.h)
            s = float(any(iou(p, want) >= PANE_MATCH for p in self.panes))
        elif hits := self.hits(cue):
            best = hits[0]
            if cue.at:
                d, best = min(
                    (
                        min(
                            ((b[0] / self.w - x) ** 2 + (b[1] / self.h - y) ** 2) ** 0.5
                            for x, y in cue.at
                        ),
                        (b, t),
                    )
                    for b, t in hits
                )
                s = 1.0 if d <= cue.near else ELSEWHERE
            else:
                s = 1.0
            for t in cue.text:
                self.found[t] = best[0]
            if cue.report and s == 1.0:
                self.reported[cue] = best[1]
        self.seen[cue] = s
        return s

    def present(self, text: str) -> bool:
        return bool(self.hits(Cue(text=(text,))))


def identify(
    apps: Iterable[App],
    lines: list[Line],
    panes: list[Box],
    background: RGB | None,
    width: int,
    height: int,
) -> Identity:
    ev = _Evidence(lines, panes, background, width, height)
    apps = list(apps)
    scored: list[tuple[float, str, str]] = []
    for app in apps:
        for view in app.views.values():
            cues = app.cues + view.cues
            total = sum(c.weight for c in cues)
            if total:
                s = sum(c.weight * ev.score(c) for c in cues) / total
                scored.append((round(s, 4), app.id, view.id))
    # Highest score first; a stable sort leaves ties to the app and view listed first.
    scored.sort(key=lambda s: -s[0])
    scores = {f"{a}.{v}": s for s, a, v in scored}
    best = scored[0] if scored else (0.0, "", "")
    runner = scored[1] if len(scored) > 1 else None
    runner_up = (runner[1], runner[2], runner[0]) if runner else None
    if best[0] < THRESHOLD:
        generic = next((a for a in apps if a.id == GENERIC), None)
        view = next(iter(generic.views)) if generic else None
        return Identity(
            GENERIC if generic else None,
            view,
            round(1 - best[0], 2),
            runner_up=(best[1], best[2], best[0]) if scored else None,
            found=dict(ev.found),
            scores=scores,
        )
    s, aid, vid = best
    confidence = s * min(1.0, 0.5 + s - (runner[0] if runner else 0.0))
    app = next(a for a in apps if a.id == aid)
    view = app.views[vid]
    facts = {
        c.report: ev.reported[c] for c in app.cues + view.cues if c.report and c in ev.reported
    }
    return Identity(
        aid,
        vid,
        round(confidence, 2),
        ready=ev.present(view.ready) if view.ready else None,
        runner_up=runner_up,
        facts=facts,
        found=dict(ev.found),
        scores=scores,
    )
