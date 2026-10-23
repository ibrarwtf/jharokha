"""Checks before input reaches the session: the right app in front, keys its policy calls
irreversible, one writer per window, nobody at this laptop's keyboard, nothing over the
client area. The decisions are here, pure; the server and `win/guard.py` gather the facts
(the app identified on screen, the clock, the lock file, Win32) and call them, and a later
actions engine can call the same checks before each step."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from jharokha.core.models import Point

Rect = tuple[
    int, int, int, int
]  # left, top, right, bottom in screen pixels; right, bottom exclusive

LEASE_TTL_S = 60.0
USER_IDLE_S = 3.0
# Our SendInput reaches the raw input thread a little after we note the tick, and the tick
# itself moves in 15.6 ms steps; input this close after ours is still ours.
OWN_MARGIN_MS = 250
_WRAP = 1 << 32  # GetTickCount wraps every 49.7 days

# While input is sent, low-level keyboard and mouse hooks see every event on this computer
# (win/guard.UserWatch); these flags mark the ones SendInput made, which are ours.
LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01
WM_MOUSEMOVE = 0x0200
# A mouse resting on a desk drifts by a pixel or two; a hand on it moves it further.
MOVE_SLACK_PX = 4

# Identified this surely, or the front app does not count as the one expected.
EXPECT_CONFIDENCE = 0.6
ANY = "any"  # expect when the app is in no definition
MODIFIERS = ("ctrl", "alt", "shift", "win")
_ALIASES = {"return": "enter", "escape": "esc", "control": "ctrl", "del": "delete"}
ALLOW_IRREVERSIBLE = "irreversible"


class WrongApp(RuntimeError):
    """The app in front is not the one the input was meant for."""

    def __init__(self, message: str, want: str) -> None:
        super().__init__(message)
        self.want = want


class Irreversible(RuntimeError):
    """A key the front app's policy says cannot be taken back (Enter sends in Teams)."""


class InputLeased(RuntimeError):
    """Another live process sent input to this window within the lease time."""

    def __init__(self, owner_pid: int, idle_s: float, ttl_s: float) -> None:
        super().__init__(
            f"process {owner_pid} is sending input to this window (its last input "
            f"{idle_s:.0f} s ago); nothing was sent. Its lease lapses after {ttl_s:.0f} s "
            "without input."
        )
        self.owner_pid = owner_pid
        self.idle_s = idle_s


class UserActive(RuntimeError):
    """Someone used this laptop's keyboard or mouse moments ago."""

    def __init__(self, idle_s: float, threshold_s: float) -> None:
        super().__init__(
            f"someone used this computer {idle_s:.1f} s ago; nothing was sent, so their "
            f"keystrokes do not mix with ours. Retry after {threshold_s:.0f} s without input."
        )
        self.idle_s = idle_s


class TypingInterrupted(UserActive):
    """The user touched this laptop while we were typing; what went before is in the session."""

    def __init__(self, idle_s: float, threshold_s: float, sent: int, text: str) -> None:
        super().__init__(idle_s, threshold_s)
        RuntimeError.__init__(
            self,
            f"someone used this computer while jharokha was typing: stopped after {sent} of "
            f"{len(text)} characters, {text[:sent][-20:]!r} being the last sent; the rest "
            f"was not sent. Read the screen before sending the rest, after {threshold_s:.0f} "
            "s without input.",
        )
        self.sent = sent


def refusal(e: WrongApp | Irreversible | InputLeased | UserActive) -> tuple[str, str]:
    """The error code and message, with what to do next, for a refused input."""
    if isinstance(e, WrongApp):
        return "wrong_app", (
            f"{e}; nothing was sent. Bring {e.want} to the front (its icon on the session's "
            "taskbar; jharokha_apps lists them as spots), confirm with jharokha_status, and "
            "retry. Do not send it to the app in front instead."
        )
    if isinstance(e, Irreversible):
        return "irreversible", (
            f"{e}; nothing was sent. Read the screen to be sure what it would send or submit; "
            "only if the user asked for exactly that, call again with allow='irreversible'. "
            "Never retry it blindly."
        )
    if isinstance(e, InputLeased):
        return "input_leased", (
            f"{e} Another agent is working in this session: wait for it to finish, or ask "
            "the user which one should go on. jharokha_status shows the holder under "
            "input.lease."
        )
    return "user_active", (
        f"{e} The user is at this computer: wait, or ask them to leave keyboard and mouse "
        "alone while you work. jharokha_status shows input.user_idle_s."
    )


@dataclass(frozen=True)
class Holder:
    pid: int
    last_s: float  # wall clock, so that every process reads the same time


def check_lease(
    holder: Holder | None, pid: int, now: float, ttl_s: float, alive: Callable[[int], bool]
) -> None:
    """Raises InputLeased unless pid may send now. A holder that is this pid, has not sent
    for ttl_s, or no longer runs (crashed) leaves the window free. A last_s in the future
    means the clock went back; that record cannot be trusted to lapse, so it does not hold."""
    if holder is None or holder.pid == pid:
        return
    idle = now - holder.last_s
    if 0 <= idle < ttl_s and alive(holder.pid):
        raise InputLeased(holder.pid, idle, ttl_s)


def user_idle_s(
    last_input_ms: int, now_ms: int, own_input_ms: int | None, margin_ms: int = OWN_MARGIN_MS
) -> float | None:
    """Seconds since the user's last input, from GetLastInputInfo and GetTickCount ticks.
    Our own SendInput moves the same clock, so input no newer than own_input_ms plus the
    margin is ours, and then None: no user input since we last sent."""
    if own_input_ms is not None:
        newer_by = (last_input_ms - own_input_ms) % _WRAP
        if newer_by <= margin_ms or newer_by >= _WRAP // 2:  # the upper half is "older"
            return None
    return ((now_ms - last_input_ms) % _WRAP) / 1000


def check_user_idle(idle_s: float | None, threshold_s: float = USER_IDLE_S) -> None:
    if idle_s is not None and idle_s < threshold_s:
        raise UserActive(idle_s, threshold_s)


class Activity:
    """The user's input while jharokha sends, decided one hook event at a time: a key or a
    button or wheel that SendInput did not make, or a mouse moved more than slack pixels
    from where it was when watching began (origin, else its first move). `at` is the
    clock's time of the first such event, None while there was none. Fed by the hook
    thread, read by the sender before each character."""

    def __init__(
        self,
        origin: Point | None = None,
        clock: Callable[[], float] | None = None,
        slack: int = MOVE_SLACK_PX,
    ) -> None:
        self.origin = origin
        self.clock = clock or time.monotonic
        self.slack = slack
        self.at: float | None = None
        self.what = ""  # "key", "button" or "move": the first event that was the user's

    def key(self, flags: int) -> bool:
        """A key down or up; True when it was the user's."""
        return not flags & LLKHF_INJECTED and self._seen("key")

    def mouse(self, msg: int, x: int, y: int, flags: int) -> bool:
        """A mouse event at screen point (x, y); True when it was the user's."""
        if flags & LLMHF_INJECTED:
            return False
        if msg != WM_MOUSEMOVE:
            return self._seen("button")
        if self.origin is None:
            self.origin = (x, y)
        ox, oy = self.origin
        return max(abs(x - ox), abs(y - oy)) > self.slack and self._seen("move")

    def _seen(self, what: str) -> bool:
        if self.at is None:
            self.at, self.what = self.clock(), what
        return True

    def user_seen(self) -> float | None:
        """Seconds since the user's first event while watching, or None."""
        return None if self.at is None else max(0.0, self.clock() - self.at)


def covered_parts(
    client: Rect,
    monitor: Rect,
    work: Rect,
    owners: Mapping[Point, str | None],
    step: int,
) -> list[dict]:
    """Parts of the client area the user would not see, in client coordinates, half-open.

    The band of the monitor outside its work area is the taskbar (or a docked app bar):
    {"kind": "taskbar", "rows": [y0, y1]} for one at the top or bottom, "cols" for a side.
    owners maps sampled client points to what is on top there: None for the session window
    itself, "" for nothing (off every monitor), else the covering app. Samples under the
    taskbar are left to its warning; the rest group per app into one box, grown by step so
    it reaches the next sample: {"kind": "window", "app": name, "box": [x, y, w, h]} or
    {"kind": "offscreen", "box": [...]}."""
    left, top, right, bottom = client
    width, height = right - left, bottom - top
    parts: list[dict] = []
    bands: list[tuple[str, int, int]] = []  # axis, client-relative start, end
    m_left, m_top, m_right, m_bottom = monitor
    w_left, w_top, w_right, w_bottom = work
    spans_x = max(left, m_left) < min(right, m_right)
    spans_y = max(top, m_top) < min(bottom, m_bottom)
    for axis, lo, hi, overlaps, origin, size in (
        ("rows", m_top, w_top, spans_x, top, height),
        ("rows", w_bottom, m_bottom, spans_x, top, height),
        ("cols", m_left, w_left, spans_y, left, width),
        ("cols", w_right, m_right, spans_y, left, width),
    ):
        a, b = max(lo - origin, 0), min(hi - origin, size)
        if overlaps and a < b:
            parts.append({"kind": "taskbar", axis: [a, b]})
            bands.append((axis, a, b))

    groups: dict[str, list[Point]] = {}
    for (x, y), app in owners.items():
        if app is None or any(a <= (y if ax == "rows" else x) < b for ax, a, b in bands):
            continue
        groups.setdefault(app, []).append((x, y))
    for app, pts in sorted(groups.items()):
        x0 = max(min(x for x, _ in pts) - step // 2, 0)
        y0 = max(min(y for _, y in pts) - step // 2, 0)
        x1 = min(max(x for x, _ in pts) + step - step // 2, width)
        y1 = min(max(y for _, y in pts) + step - step // 2, height)
        box = [x0, y0, x1 - x0, y1 - y0]
        parts.append(
            {"kind": "window", "app": app, "box": box} if app else {"kind": "offscreen", "box": box}
        )
    return parts


def sample_points(size: tuple[int, int], step: int) -> list[Point]:
    """A grid every step pixels, offset half a step so no sample sits on the frame edge."""
    w, h = size
    return [(x, y) for y in range(step // 2, h, step) for x in range(step // 2, w, step)]


def warnings(parts: list[dict]) -> list[dict]:
    """covered_parts with what each means for input, for jharokha_status."""
    out = []
    for p in parts:
        if p["kind"] == "taskbar":
            axis = "rows" if "rows" in p else "cols"
            a, b = p[axis]
            detail = (
                f"this computer's taskbar covers client {axis} {a} to {b}: clicks there (such "
                "as the session's own taskbar) are refused. Auto-hide the local taskbar or "
                "make the session full screen."
            )
        elif p["kind"] == "window":
            detail = (
                f"{p['app']} is over the session at {p['box']}: clicks there are refused "
                "until it is moved or the session is brought to the front."
            )
        else:
            detail = f"{p['box']} is off every monitor: clicks there are refused."
        out.append({**p, "detail": detail})
    return out


# The app in front and its policy


def combo_key(combo: str) -> str:
    """A chord in one spelling, to compare against a policy: 'Shift+Return' -> 'shift+enter',
    modifiers in a fixed order. Splits as win/input does, so 'ctrl++' is ctrl and +."""
    parts = [p.strip() for p in re.split(r"\+(?=.)", combo.lower())]
    if not all(parts):
        raise ValueError(f"'{combo}' is not a key or chord like enter or ctrl+shift+x")
    names = [_ALIASES.get(p, p) for p in parts]
    mods = sorted({n for n in names[:-1] if n in MODIFIERS}, key=MODIFIERS.index)
    return "+".join([*mods, names[-1]])


@dataclass(frozen=True)
class Policy:
    """How input to an app is shaped and limited, from its definition's [app.x.policy]:
    newline, the chord a typed newline is sent as (Teams: shift+enter, so a newline adds a
    line instead of sending); irreversible_keys, chords that cannot be taken back there
    (Enter sends a Teams message, or submits Edge's address bar), refused unless the call
    passes allow='irreversible'."""

    newline: str | None = None
    irreversible_keys: frozenset[str] = frozenset()

    @classmethod
    def parse(cls, table: Mapping[str, Any], where: str) -> Policy:
        extra = sorted(set(table) - {"newline", "irreversible_keys"})
        if extra:
            raise ValueError(f"{where}: unknown key {', '.join(extra)}")
        newline = table.get("newline")
        if newline is not None and not (isinstance(newline, str) and newline.strip()):
            raise ValueError(f"{where}.newline must be a key or chord, like shift+enter")
        keys = table.get("irreversible_keys", [])
        if not isinstance(keys, list) or not all(isinstance(k, str) and k for k in keys):
            raise ValueError(f'{where}.irreversible_keys must be a list of chords, like ["enter"]')
        return cls(
            combo_key(newline) if newline else None,
            frozenset(combo_key(k) for k in keys),
        )

    def irreversible(self, combo: str) -> bool:
        return combo_key(combo) in self.irreversible_keys


def check_front(
    expect: str,
    app: str | None,
    view: str | None,
    confidence: float,
    min_confidence: float = EXPECT_CONFIDENCE,
) -> None:
    """Raises WrongApp unless expect ('terminal' or 'teams.chat') is what was identified in
    front, at min_confidence or more."""
    if expect == ANY:
        return
    want_app, _, want_view = expect.partition(".")
    on = f"{app} {view}, confidence {confidence}" if app else "a lock screen or nothing known"
    if app != want_app or (want_view and view != want_view):
        raise WrongApp(f"expected {expect} in front, but the screen shows {on}", want_app)
    if confidence < min_confidence:
        raise WrongApp(
            f"expected {expect} in front; the screen looks like it, but only at confidence "
            f"{confidence} (needs {min_confidence})",
            want_app,
        )


Step = tuple[str, str]  # ("type" | "paste" | "press", text or chord)


def text_steps(text: str, paste: bool, newline: str | None) -> list[Step]:
    """How text is sent under a newline policy. Without one, as given: one type step (a
    newline is Enter) or one paste step (a final newline is Enter). With one, every typed
    newline, and a paste's final one, is that chord instead."""
    if newline is None or ("\n" not in text):
        return [("paste" if paste else "type", text)]
    if paste:
        body = text.removesuffix("\n")
        steps: list[Step] = [("paste", body)] if body else []
        return steps + ([("press", newline)] if body != text else [])
    steps = []
    for i, part in enumerate(text.split("\n")):
        if i:
            steps.append(("press", newline))
        if part:
            steps.append(("type", part))
    return steps


def chords_of(steps: list[Step]) -> list[str]:
    """Every key or chord the steps send besides plain characters: Enter for a typed newline
    or a paste's final one, and each press."""
    out = []
    for kind, s in steps:
        if kind == "press":
            out.append(combo_key(s))
        elif (kind == "type" and "\n" in s) or (kind == "paste" and s.endswith("\n")):
            out.append("enter")
    return out


def check_keys(policy: Policy, chords: list[str], allow: str | None, app: str) -> None:
    """Raises Irreversible when a chord is one of the policy's irreversible keys and the
    call did not pass allow='irreversible'."""
    if allow == ALLOW_IRREVERSIBLE:
        return
    hit = next((c for c in chords if policy.irreversible(c)), None)
    if hit:
        raise Irreversible(f"{hit} is irreversible in {app}: it sends or submits what is there")
