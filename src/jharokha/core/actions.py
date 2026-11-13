"""Actions as data. An action is a named sequence of steps an agent calls with arguments and
gets a structured result from; anyone can add one in a TOML file (apps.toml, or the file
$JHAROKHA_ACTIONS names). Built-in ones ship in jharokha/apps/actions/.

    format = 1
    [action.teams.draft]                  # the action teams.draft; its app is teams
    summary = "Type a draft into a chat's compose box; never sends."
    args.chat = { type = "text" }
    args.text = { type = "text", max_len = 2000 }

    [[action.teams.draft.step]]
    do = "click"
    target = { text = "{chat}", region = "list" }
    expect = { text = "{chat}", region = "header" }

Steps (`do`), each with `expect` (a condition that must hold after it), `unless` (a
condition under which it is skipped, checked before it: no click on a chat already open)
and `timeout_s`:

    focus     app, view, switch (by the session spot `taskbar.<app>`), else check it is in front
    click     target (a locator, core.locate), button, double
    type      text, newline (enter | shift+enter | forbid), target to click first
    press     keys
    scroll    region, ticks, until (a condition), end (one marking the list's end), max;
              collect the region's items into a list
    wait      for = ready | settled, or until (a condition)
    read      region as items (by the region's reader, core.readers) or lines into a result,
              since a time (a list region); zoom reads it close
    extract   regex on a region into result fields, or a field of collected items
    repeat    times | each (a list argument) | until, max; steps inside
    run       command in the prepared shell in front (core.shell: ended by its next state
              prompt, output numbered and paged), its exit code and status; clear (cls)
              first; first = the number of its first output line; setup = true prepares
              the tab instead and learns the terminal's glyphs (core.glyphs); result =
              true waits for the last command's end; output = true pages the last
              command's output (first, count) from the shell's variable
    write_file  path, text: the text put in this machine's clipboard, written from it by
              one command and its SHA-256 checked; nothing of it typed or shown
    edit_file path, old, new: old replaced by new where it occurs exactly once, both
              through the clipboard as plain JSON; the file's new SHA-256 returned

Items are what jharokha_read format=items gives for the region (a Teams thread's
messages: sender, time, text; who and day too with a people map, mapped once the scroll
has merged its reads); `fields = { author = "sender" }` renames their fields. A
marker Windows OCR drops at 1x (it does, on Cascadia rows) is looked for again in the
rows read up close, then by the second engine, once the screen holds still.

Conditions: text, regex, gone (in region), name (a name as a header shows it: cut short
with an ellipsis, icon glyphs beside it, a letter misread; or an id of the local people
map, the chat it names under any of its names; core.actions.name_on), app,
view, ready, before (an item of a list region at or before this time), after (one past
it). A scroll's collected items keep those since a time and upto one. An empty text or
name (an optional argument left out) holds. Arguments fill `{name}` in step values; in a
regex they are escaped. Only arguments and repeat variables fill placeholders, never text
read off the screen: what is on screen goes into the result, and never becomes a step, a
click target or typed text.

The guard is core.guard's, as for the input tools, with the same codes: before every input
step the app in front must be the action's (wrong_app); a step that presses a key the
app's policy calls irreversible (Enter in Teams sends), or types a newline that is one, is
refused before any input unless the call allows it (irreversible); and the Source refuses
input while another agent holds the window (input_leased) or someone uses this computer
(user_active). A typed newline is the policy's newline (Shift+Enter in Teams), and typed
text never carries a raw newline, so Enter is only ever a declared key. A terminal command
is none of the guard's business: its app's policy names no key."""

from __future__ import annotations

import difflib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import cache
from importlib import resources
from typing import Any, Protocol

from jharokha.core import guard, listread, readers
from jharokha.core import shell as sh
from jharokha.core.apps import GENERIC
from jharokha.core.guard import Policy
from jharokha.core.locate import (
    Hit,
    Locator,
    Miss,
    RegionInfo,
    Spot,
    locate,
    locator,
    row_texts,
    unwrapped,
)
from jharokha.core.models import Box, Frame, Line, Word, inside, union
from jharokha.core.people import People
from jharokha.core.screen import ScreenError, Settled
from jharokha.core.text import find, fold

FORMAT = 1
NEWLINES = ("enter", "shift+enter", "forbid")
ARG_TYPES = ("text", "number", "list", "choice")
COND_KEYS = ("text", "regex", "gone", "name", "region", "app", "view", "ready", "before", "after")
COMMON = ("do", "expect", "unless", "timeout_s", "label")
INPUT = ("click", "type", "press", "scroll", "run", "write_file", "edit_file")
# Refusals, where nothing more was sent: the input tools' codes (core.guard), and locked.
REFUSALS = ("wrong_app", "irreversible", "input_leased", "user_active", "locked")
# Codes of a failed step, beside the refusals, for the terminal: whether anything was sent
# is in each one's reason.
FAIL_CODES = {
    "not_prepared": "no JK state prompt in the tab in front; nothing was sent",
    "not_a_shell": "the tab in front runs a full-screen program (a TUI), not a shell; "
    "nothing was sent",
    "no_prompt": "no empty prompt at the bottom of the shell; nothing was sent",
    "input_pending": "the shell's input already holds text; nothing was sent",
    "echo_mismatch": "the sent line did not show as sent; Escape cleared it, nothing was run",
    "still_running": "the command was sent and is still running",
    "unknown": "the command was sent; whether it ended cannot be read now",
    "edit_count": "the text to replace is not in the file exactly once; nothing was written",
    "clipboard_mismatch": "the clipboard did not reach the session as sent; nothing written",
}
PARAMS: dict[str, tuple[str, ...]] = {
    "focus": ("app", "view", "switch", "spot", "window"),
    "click": ("target", "button", "double"),
    "type": ("text", "newline", "target"),
    "press": ("keys",),
    "scroll": (
        "region",
        "ticks",
        "max_ticks",
        "until",
        "end",
        "max",
        "collect",
        "fields",
        "exclude",
        "since",
        "upto",
    ),
    "wait": ("for", "until"),
    "read": ("region", "as", "into", "fields", "exclude", "since", "last", "zoom", "mark"),
    "extract": ("region", "regex", "items", "field", "pick", "into", "required", "zoom"),
    "repeat": ("times", "each", "as", "until", "max", "step"),
    "run": ("command", "region", "clear", "first", "setup", "result", "output", "count"),
    "write_file": ("path", "text", "region"),
    "edit_file": ("path", "old", "new", "region"),
}
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
TIMEOUT_S = 5.0
POLL_S = 0.1
MAX_REPEAT = 50  # passes of a repeat, whatever times says
STILL_ENDS = 3  # scrolls in a row that change nothing: the end of a list
LOAD_WAIT_S = 3.0  # how long a list that stopped going back gets to load older items
# A line this short is typed: under 2 s at about 100 characters a second, what a
# paste takes to arrive and settle.
TYPE_MAX = 200
ARRIVE_S = 2.0  # for a sent line to start showing in the shell
ARRIVE_CPS = 40  # characters a second a line is given to show, at the slowest measured
STABLE_S = 1.0  # an input row unchanged this long has stopped arriving
BOTTOM_TICKS = 300  # wheel notches down that bring any view back to its newest rows
PAGE_MAX = 40  # pages of output fetched from the shell's variable at most
ROW_PX = 23  # a terminal row's pitch when the screen shows too little to measure it
GLYPHS_MIN = 60  # templates a terminal needs before its cells read without OCR
UNKNOWN_WARN = 20  # cells read by OCR in a reply, past which the result warns
AGREE_S = 0.3  # between the two looks a read of items takes


class ActionError(ValueError):
    pass


# Definitions


@dataclass(frozen=True)
class Arg:
    name: str
    type: str = "text"
    required: bool = True
    default: Any = None
    pattern: str | None = None
    choices: tuple[str, ...] = ()
    max_len: int | None = None
    max: float | None = None  # of a number
    doc: str = ""


@dataclass(frozen=True)
class Step:
    kind: str
    params: dict[str, Any]
    expect: dict[str, Any] | None = None
    timeout_s: float = TIMEOUT_S
    label: str = ""
    steps: tuple[Step, ...] = ()  # a repeat's
    unless: dict[str, Any] | None = None  # skipped when this holds before it
    timeout_arg: str = ""  # the argument timeout_s comes from, per run


@dataclass(frozen=True)
class Action:
    id: str
    app: str
    summary: str
    args: dict[str, Arg]
    steps: tuple[Step, ...]
    newline: str | None = None  # a typed newline: None is the app's policy, else forbidden
    source: str = "built-in"


def _only(t: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    extra = sorted(set(t) - set(allowed))
    if extra:
        raise ActionError(f"{where}: unknown key {', '.join(extra)}")


def _arg(name: str, t: Any, where: str) -> Arg:
    if not isinstance(t, dict):
        raise ActionError(f'{where} must be a table like {{ type = "text" }}')
    _only(t, ("type", "required", "default", "pattern", "choices", "max_len", "max", "doc"), where)
    kind = t.get("type", "text")
    if kind not in ARG_TYPES:
        raise ActionError(f"{where}.type must be one of {', '.join(ARG_TYPES)}")
    if "pattern" in t:
        try:
            re.compile(t["pattern"])
        except (re.error, TypeError) as e:
            raise ActionError(f"{where}.pattern must be a regex: {e}") from e
    choices = tuple(t.get("choices", ()))
    if kind == "choice" and not choices:
        raise ActionError(f"{where}: a choice needs choices")
    return Arg(
        name,
        kind,
        bool(t.get("required", "default" not in t)),
        t.get("default"),
        t.get("pattern"),
        choices,
        t.get("max_len"),
        t.get("max"),
        str(t.get("doc", "")),
    )


def _check_condition(c: Any, where: str) -> dict[str, Any]:
    if not isinstance(c, dict) or not c:
        raise ActionError(f"{where} must be a table of {', '.join(COND_KEYS)}")
    _only(c, COND_KEYS, where)
    for k in ("regex",):
        if k in c and not _PLACEHOLDER.search(c[k]):
            try:
                re.compile(c[k])
            except re.error as e:
                raise ActionError(f"{where}.{k} must be a regex: {e}") from e
    return dict(c)


def _step(t: Any, where: str, names: set[str]) -> Step:
    if not isinstance(t, dict) or "do" not in t:
        raise ActionError(f"{where} needs do = one of {', '.join(PARAMS)}")
    kind = t["do"]
    if kind not in PARAMS:
        raise ActionError(f"{where}.do must be one of {', '.join(PARAMS)}")
    _only(t, COMMON + PARAMS[kind], where)
    params = {k: v for k, v in t.items() if k not in COMMON and k != "step"}
    inner_names = set(names)
    if kind == "repeat":
        if not any(k in t for k in ("times", "each", "until")):
            raise ActionError(f"{where}: repeat needs times, each or until")
        if "each" in t:
            if t["each"] not in names:
                raise ActionError(f"{where}.each must name a list argument")
            inner_names.add(t.get("as", "item"))
        inner = t.get("step", [])
        if not isinstance(inner, list) or not inner:
            raise ActionError(f"{where}: repeat needs [[...step]] inside")
        steps = tuple(_step(s, f"{where}.step[{i}]", inner_names) for i, s in enumerate(inner))
    else:
        steps = ()
    for key in ("target",):
        if key in params:
            if not isinstance(params[key], dict):
                raise ActionError(f"{where}.{key} must be a locator table")
            try:
                locator(params[key])
            except ValueError as e:
                raise ActionError(f"{where}.{key}: {e}") from e
    for key in ("until", "end"):
        if key in params:
            _check_condition(params[key], f"{where}.{key}")
    if "fields" in params and not (
        isinstance(params["fields"], dict)
        and all(isinstance(v, str) for v in params["fields"].values())
    ):
        raise ActionError(f'{where}.fields renames item fields, like {{ who = "sender" }}')
    if kind == "type" and params.get("newline", "forbid") not in NEWLINES:
        raise ActionError(f"{where}.newline must be one of {', '.join(NEWLINES)}")
    if kind == "run" and "first" in params:
        first = params["first"]
        if not (isinstance(first, int) and first > 0) and not _PLACEHOLDER.fullmatch(str(first)):
            raise ActionError(f"{where}.first must be a line number or an argument")
    for need in {
        "type": ("text",),
        "press": ("keys",),
        "click": ("target",),
        "run": ()
        if params.get("setup") or params.get("result") or params.get("output")
        else ("command",),
        "write_file": ("path", "text"),
        "edit_file": ("path", "old", "new"),
        "read": ("region", "into"),
        "scroll": ("region",),
    }.get(kind, ()):
        if need not in params:
            raise ActionError(f"{where}: a {kind} step needs {need}")
    expect = _check_condition(t["expect"], f"{where}.expect") if "expect" in t else None
    unless = _check_condition(t["unless"], f"{where}.unless") if "unless" in t else None
    for name in _placeholders([params, expect, unless]):
        if name not in names:
            raise ActionError(f"{where}: '{{{name}}}' is not an argument of this action")
    timeout, timeout_arg = t.get("timeout_s", TIMEOUT_S), ""
    if isinstance(timeout, str) and (m := _PLACEHOLDER.fullmatch(timeout)):
        if m.group(1) not in names:
            raise ActionError(f"{where}: '{timeout}' is not an argument of this action")
        timeout, timeout_arg = TIMEOUT_S, m.group(1)
    elif not isinstance(timeout, int | float) or timeout <= 0:
        raise ActionError(f"{where}.timeout_s must be a number of seconds")
    label = str(t.get("label", ""))
    return Step(kind, params, expect, float(timeout), label, steps, unless, timeout_arg)


def _placeholders(v: Any) -> set[str]:
    if isinstance(v, str):
        return set(_PLACEHOLDER.findall(v))
    if isinstance(v, dict):
        return {n for x in v.values() for n in _placeholders(x)}
    if isinstance(v, list | tuple):
        return {n for x in v for n in _placeholders(x)}
    return set()


def parse(text: str, source: str = "actions", origin: str = "local") -> dict[str, Action]:
    """The actions in one file, by id (`teams.draft`). An apps.toml's app definitions and
    calibration are left to core.apps."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ActionError(f"{source}: {e}") from e
    if data.get("format") != FORMAT:
        raise ActionError(f"{source}: needs `format = {FORMAT}` at the top")
    _only(data, ("format", "action", "app", "session", "calibration", "people", "chats"), source)
    out: dict[str, Action] = {}
    for app, acts in data.get("action", {}).items():
        if not isinstance(acts, dict):
            raise ActionError(f"{source}: action.{app} must be tables")
        for name, t in acts.items():
            where = f"{source}: action.{app}.{name}"
            if not re.fullmatch(r"[A-Za-z0-9_-]+", f"{app}{name}"):
                raise ActionError(f"{where}: names are letters, digits, - or _")
            if not isinstance(t, dict):
                raise ActionError(f"{where} must be a table")
            _only(t, ("summary", "args", "step", "newline"), where)
            args = {k: _arg(k, a, f"{where}.args.{k}") for k, a in t.get("args", {}).items()}
            steps = t.get("step", [])
            if not isinstance(steps, list) or not steps:
                raise ActionError(f"{where}: needs [[{where.split(': ')[1]}.step]]")
            parsed = tuple(_step(s, f"{where}.step[{i}]", set(args)) for i, s in enumerate(steps))
            newline = t.get("newline")
            if newline is not None and newline not in NEWLINES:
                raise ActionError(f"{where}.newline must be one of {', '.join(NEWLINES)}")
            out[f"{app}.{name}"] = Action(
                f"{app}.{name}",
                app,
                str(t.get("summary", "")),
                args,
                parsed,
                newline,
                origin,
            )
    return out


@cache
def builtin() -> dict[str, Action]:
    """The actions shipped in jharokha/apps/actions/."""
    root = resources.files("jharokha") / "apps" / "actions"
    out: dict[str, Action] = {}
    for f in sorted((f for f in root.iterdir() if f.name.endswith(".toml")), key=lambda f: f.name):
        out.update(parse(f.read_text(encoding="utf-8"), f"built-in actions/{f.name}", "built-in"))
    return out


def load(*texts: str) -> dict[str, Action]:
    """Built-ins, then each local file in order; a later action replaces one of its id."""
    out = dict(builtin())
    for i, t in enumerate(texts):
        out.update(parse(t, f"actions {i}"))
    return out


PICKER_BAND = (12, 110)  # px past the taskbar icon's centre where a window picker's titles sit
PICKER_REACH = 420  # px left and right of the icon a picker's titles reach
PICKER_FADE_S = 0.5  # s a taskbar window picker takes to open or close


def picked(before: Snapshot, after: Snapshot, icon: tuple[int, int]) -> list[Line]:
    """The window titles of the picker a taskbar click opened, left to right: lines in the
    band just past the bar (below a bar at the top, above one at the bottom), around the
    icon, that the click brought. The pointer resting on the icon keeps its picker open
    (neither Esc nor another click closes it), so titles already there count too
    when every one of them ends in the ellipsis a picker cuts titles with, so that a name
    in the app's own text there is never clicked. Empty when no picker shows."""
    x, y = icon
    top = y < after.size[1] / 2
    lo, hi = (
        (y + PICKER_BAND[0], y + PICKER_BAND[1])
        if top
        else (y - PICKER_BAND[1], y - PICKER_BAND[0])
    )
    band = sorted(
        (
            ln
            for whole in after.lines
            for ln in _titles(whole)
            if lo <= ln.box[1] + ln.box[3] // 2 <= hi
            and abs(ln.box[0] + ln.box[2] // 2 - x) <= PICKER_REACH
            and len(ln.text.strip()) >= 3
        ),
        key=lambda ln: ln.box[0],
    )
    seen = {(ln.text, ln.box[0] // 4, ln.box[1] // 4) for ln in before.lines}
    if new := [ln for ln in band if (ln.text, ln.box[0] // 4, ln.box[1] // 4) not in seen]:
        return new
    cut = [ln for ln in band if _ELLIPSIS_END.search(ln.text)]
    return band if band and len(cut) == len(band) else []


_ELLIPSIS_END = re.compile(r"(\.\.\.|…)\s*$")
TITLE_GAP = 24  # px between words that parts two picker titles OCR read as one line


def _titles(ln: Line) -> list[Line]:
    """A line cut where its words stand far apart: over a dark terminal OCR read two picker
    titles side by side as one line, whose centre lies between the two windows."""
    if len(ln.words) < 2:
        return [ln]
    parts: list[list[Word]] = [[ln.words[0]]]
    for w in ln.words[1:]:
        prev = parts[-1][-1].box
        if w.box[0] - (prev[0] + prev[2]) > TITLE_GAP:
            parts.append([w])
        else:
            parts[-1].append(w)
    if len(parts) == 1:
        return [ln]
    return [Line(" ".join(w.text for w in p), union([w.box for w in p]), tuple(p)) for p in parts]


def _titled(title: str, want: str) -> bool:
    """A picker title, cut with an ellipsis, names the window the user means."""
    t = re.sub(r"(\.\.\.|…)\s*$", "", title).strip().lower()
    w = want.strip().lower()
    return bool(t) and (w in t or w.startswith(t))


def _center(b: Box) -> tuple[int, int]:
    return b[0] + b[2] // 2, b[1] + b[3] // 2


# What the runner needs from a session


@dataclass(frozen=True)
class Snapshot:
    """One reading of the screen: its lines, what app and view it is (and how surely),
    where that view's named regions (`teams.list`) and the session's (`session.taskbar`)
    are, how each of the view's regions reads as items, and the spots."""

    frame_id: int
    size: tuple[int, int]
    lines: list[Line]
    app: str | None
    view: str | None
    ready: bool | None = None
    regions: dict[str, Box] = field(default_factory=dict)
    spots: dict[str, Spot] = field(default_factory=dict)
    locked: bool = False
    confidence: float = 1.0
    info: dict[str, RegionInfo] = field(default_factory=dict)
    frame: Frame | None = None  # the pixels read, for a thread's bubbles


class Executor(Protocol):
    """Input and reading, injected: the server's Source in a session, a fake in tests."""

    def read(self) -> Snapshot: ...
    def zoom(self, box: Box) -> list[Line]: ...  # the box read up close, client coordinates
    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None: ...
    def type(self, text: str) -> None: ...  # never holds a newline
    def press(self, keys: str) -> None: ...
    def scroll(self, x: int, y: int, ticks: int) -> None: ...
    # Until the screen holds still after input; within box only, when given.
    def settle(self, box: Box | None = None) -> object: ...  # core.screen.Settled, or None
    def wait(self, seconds: float) -> None: ...
    def now(self) -> float: ...


# Results


@dataclass
class StepReport:
    label: str
    kind: str
    ms: int
    note: str = ""


@dataclass
class Result:
    """What an action did. fields, items, lines and output hold untrusted screen text."""

    action: str
    ok: bool = True
    fields: dict[str, Any] = field(default_factory=dict)
    items: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    lines: dict[str, list[str]] = field(default_factory=dict)
    output: list[str] | None = None
    exit_code: int | None = None
    steps: list[StepReport] = field(default_factory=list)
    failed: dict[str, Any] | None = None  # step, reason, code, candidates (untrusted)
    plan: list[dict[str, Any]] | None = None
    dry_run: bool = False

    def to_dict(self, brief: bool = False) -> dict[str, Any]:
        """brief leaves out steps_done when the action succeeded: what it found is the
        answer, and how it got there costs a few dozen tokens a step."""
        out: dict[str, Any] = {"action": self.action, "ok": self.ok}
        for k in ("fields", "items", "lines", "output", "exit_code", "failed", "plan"):
            v = getattr(self, k)
            if v not in (None, {}, []):
                out[k] = v
        if self.dry_run:
            out["dry_run"] = True
        if brief and self.ok and not self.dry_run:
            return out
        out["steps_done"] = [
            {"step": s.label, "do": s.kind, "ms": s.ms} | ({"note": s.note} if s.note else {})
            for s in self.steps
        ]
        return out


class _Fail(Exception):
    """A step that failed. code, when given, names the failure for a caller to branch on
    (FAIL_CODES); the reason says it in words."""

    def __init__(self, reason: str, candidates: tuple[str, ...] = (), code: str = "") -> None:
        super().__init__(reason)
        self.reason, self.candidates, self.code = reason, candidates, code


class _Refuse(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind, self.reason = kind, reason


# Arguments and placeholders


def check_args(action: Action, args: Mapping[str, Any]) -> dict[str, Any]:
    """The arguments, validated and with defaults; ActionError naming what is wrong."""
    extra = sorted(set(args) - set(action.args))
    if extra:
        takes = ", ".join(action.args) or "no arguments"
        raise ActionError(f"{action.id} takes no argument {', '.join(extra)}; it takes {takes}")
    env: dict[str, Any] = {}
    for name, a in action.args.items():
        if name not in args:
            if a.required:
                raise ActionError(f"{action.id} needs {name}")
            env[name] = a.default
            continue
        v = args[name]
        if a.type == "number":
            if isinstance(v, bool) or not isinstance(v, int | float):
                raise ActionError(f"{name} must be a number")
            if a.max is not None and not 0 < v <= a.max:
                raise ActionError(f"{name} must be more than 0 and at most {a.max:g}")
        elif a.type == "list":
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                raise ActionError(f"{name} must be a list of text")
        elif not isinstance(v, str):
            raise ActionError(f"{name} must be text")
        if a.type == "choice" and v not in a.choices:
            raise ActionError(f"{name} must be one of {', '.join(a.choices)}")
        for x in v if isinstance(v, list) else [v]:
            if a.max_len is not None and isinstance(x, str) and len(x) > a.max_len:
                raise ActionError(f"{name} is longer than {a.max_len} characters")
            if a.pattern and not re.fullmatch(a.pattern, str(x)):
                raise ActionError(f"{name} {str(x)[:60]!r} does not match {a.pattern}")
        env[name] = v
    return env


def fill(v: Any, env: Mapping[str, Any], regex: bool = False) -> Any:
    """v with {name} replaced from env: a value that is only a placeholder keeps the
    argument's type; inside a regex an argument matches literally."""
    if isinstance(v, str):
        whole = _PLACEHOLDER.fullmatch(v)
        if whole and not regex:
            return env[whole.group(1)]

        def sub(m: re.Match[str]) -> str:
            x = env[m.group(1)]
            s = ", ".join(x) if isinstance(x, list) else str(x)
            return re.escape(s) if regex else s

        return _PLACEHOLDER.sub(sub, v)
    if isinstance(v, dict):
        return {k: fill(x, env, regex or k == "regex") for k, x in v.items()}
    if isinstance(v, list):
        return [fill(x, env, regex) for x in v]
    return v


# The runner


class Runner:
    """Runs one action against an executor. allow is a `do` call's: 'irreversible' lets a
    step press a key the app's policy calls irreversible (Enter in Teams). policies are the
    apps' input policies (core.guard.Policy, from apps.Catalog.policies()), applied as the
    input tools apply them. people is the local people map (apps.Catalog.people): a `name`
    condition or locator given one of its ids finds that chat by any of its names."""

    def __init__(
        self,
        ex: Executor,
        allow: str | None = None,
        policies: Mapping[str, Policy] | None = None,
        people: People | None = None,
        marks: dict[str, str] | None = None,
    ) -> None:
        self.ex, self.allow = ex, allow
        self.policies = dict(policies or {})
        self.people = people or People()
        # The newest item a read with a mark returned, per list: the caller keeps it across
        # runs (the server, in memory only), so the next read returns what came after it.
        self.marks = marks if marks is not None else {}

    def run(self, action: Action, args: Mapping[str, Any], dry_run: bool = False) -> Result:
        res = Result(action.id, dry_run=dry_run)
        try:
            env = check_args(action, args)
        except ActionError as e:
            res.ok, res.failed = False, {"step": None, "reason": str(e), "code": "bad_argument"}
            return res
        policy = self.policies.get(action.app, Policy())
        hit = _sends(action, action.steps, env, "", policy)
        if hit and self.allow != guard.ALLOW_IRREVERSIBLE:
            label, why = hit
            detail = (
                f"step {label} {why}: it sends or submits, so nothing was sent. Only if the "
                "user asked for exactly this, call again with allow='irreversible'"
            )
            res.ok = False
            res.failed = {"step": label, "reason": detail, "code": "irreversible"}
            return res
        if dry_run:
            res.plan = _plan(action, action.steps, env, "")
            return res
        self.action, self.res = action, res
        self._last: str | None = None  # the last app recognised during this run
        self._close: dict[tuple[int, str], list[Line]] = {}  # rows read again, per frame
        self._lifted: dict[tuple[int, int], int] = {}  # wheel notches up, net, per point
        self._switched = False  # a focus step brought the app forward by its taskbar icon
        try:
            self._steps(action.steps, env, "")
        except _Fail as e:
            res.ok = False
            res.failed = {"step": self._at, "reason": e.reason}
            if e.code:
                res.failed["code"] = e.code
            if e.candidates:
                res.failed["candidates"] = list(e.candidates)
        except _Refuse as e:
            res.ok = False
            res.failed = {"step": self._at, "reason": e.reason, "code": e.kind}
        except ScreenError as e:
            # The Source's own guards (input_leased, user_active) and errors (covered, ...)
            reason = str(e).removeprefix(f"{e.code}: ")
            if self.res.steps:
                reason += f" ({len(self.res.steps)} steps were done before; see steps_done)"
            res.ok = False
            res.failed = {"step": self._at, "reason": reason, "code": e.code}
        self._lower(res)
        return res

    def _lower(self, res: Result) -> None:
        """A list scrolled up by the run (a thread read back to a start time) goes back to
        its newest end, whether the run finished or stopped half-way: a chat left scrolled
        up hides what comes in next from the user. Overshooting the end is harmless."""
        for (x, y), up in self._lifted.items():
            if up <= 0:
                continue
            try:
                # Undoing the run's own scroll needs the app to read as itself, not the 0.6
                # new input needs: an app read at 0.59 would be left scrolled far up.
                if (snap := self._look()).app != self.action.app:
                    self._guard(snap)
                self.ex.scroll(x, y, -2 * up)
                self.ex.settle()
                note = "scrolled back down to the newest end"
            except (_Refuse, _Fail, ScreenError) as e:
                why = e.reason if isinstance(e, _Refuse | _Fail) else str(e)
                note = f"left scrolled up {up} notches: scrolling back was refused ({why})"
                if res.failed is None:
                    res.fields["warning"] = note
            if res.failed is not None:
                res.failed["reason"] += f"; {note}"

    # Steps

    def _steps(self, steps: tuple[Step, ...], env: dict[str, Any], prefix: str) -> None:
        for i, s in enumerate(steps):
            label = f"{prefix}{i + 1}" + (f" {s.label}" if s.label else "")
            self._at = f"{label}:{s.kind}"
            t0 = self.ex.now()
            if s.unless:
                snap = self._look()
                cond = fill(s.unless, env)
                if self._check(cond, snap)[0]:
                    ms = int((self.ex.now() - t0) * 1000)
                    note = f"skipped: {_describe(cond)} holds"
                    self.res.steps.append(StepReport(f"{prefix}{i + 1}", s.kind, ms, note))
                    continue
            p = fill(s.params, env)
            if s.timeout_arg:
                s = replace(s, timeout_s=float(env[s.timeout_arg]))
            note = getattr(self, f"_{s.kind}")(s, p, env, f"{prefix}{i + 1}.")
            if s.expect:
                self._until(fill(s.expect, env), s.timeout_s, "expected")
            ms = int((self.ex.now() - t0) * 1000)
            self.res.steps.append(StepReport(f"{prefix}{i + 1}", s.kind, ms, note or ""))

    def _focus(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        app, view = p.get("app", self.action.app), p.get("view")
        snap = self._look(fast=True)
        if snap.locked:
            raise _Refuse("locked", "the session is locked; nothing was sent")
        switched = snap.app != app and app != guard.ANY
        if switched and (full := self._look()).app == app:
            # A click on the icon of the app in front minimises it: a glance that missed
            # the app is not enough to click.
            snap, switched = full, False
        if switched:
            if not p.get("switch"):
                self._guard(snap, app)  # refuses: wrong_app, as the input tools do
            spot = p.get("spot", f"taskbar.{app}")
            hit = locate(Locator(spot=spot), [], {}, snap.spots, (0, 0, *snap.size))
            if isinstance(hit, Miss):
                raise _Fail(
                    f"cannot switch to {app}: {hit.reason}. Add its taskbar icon as the spot "
                    f"taskbar.{app} in apps.toml, or bring the app forward yourself"
                )
            # An app with two windows or more answers its icon with a picker of their titles,
            # not a window, so a click alone never brings it forward.
            # A click that showed neither the app nor a picker is followed by one more.
            want = str(p.get("window") or "")
            before, titles = snap, []
            after = snap
            for _click in range(2):
                self.ex.click(*hit.center)
                self.ex.settle()
                self.ex.wait(PICKER_FADE_S)  # a picker fades in after the click
                after = self._look(fast=True)
                if after.app == app or (titles := picked(before, after, hit.center)):
                    break
                before = after
            if after.app != app and titles:
                choice = next((t for t in titles if want and _titled(t.text, want)), None)
                if want and choice is None:
                    raise _Fail(
                        f"{app} has windows {[t.text for t in titles]}, none titled {want!r}; "
                        "its window picker is open: call again with one of them"
                    )
                choice = choice or titles[0]
                self.ex.click(*_center(choice.box))
                self.ex.settle()
                self.res.fields["window"] = choice.text
            snap = self._until({"app": app}, s.timeout_s, f"switching to {app}")
            self._switched = True
        self._guard(snap, app)
        if view and snap.view != view:
            raise _Fail(f"{app} shows its {snap.view} view, not {view}")
        note = f"switched to {app}" if switched else f"{app} in front"
        if snap.ready is False:
            # Its ready cue can be hidden for good (a draft over Teams' compose placeholder):
            # the wait gives a loading view its time, and the steps after check their own.
            try:
                self._until({"ready": True}, s.timeout_s, f"{app} {snap.view} to load")
            except _Fail:
                note += f", not shown ready after {s.timeout_s:g} s"
        return note

    def _look(self, fast: bool = False) -> Snapshot:
        """The screen now; fast, Windows OCR's text alone where the executor can (finding
        a marker, checking the app in front), never for what a step returns."""
        glance = getattr(self.ex, "glance", None) if fast else None
        snap: Snapshot = glance() if glance else self.ex.read()
        if snap.app not in (None, GENERIC):
            self._last = snap.app
        return snap

    def _guard(self, snap: Snapshot, app: str | None = None) -> None:
        """Input goes only to the action's app (core.guard.check_front): in front, or
        unrecognised for a moment (typing over a browser's URL hides its strongest cue)
        while nothing else has been recognised since it was."""
        want = app or self.action.app
        if snap.locked:
            raise _Refuse("locked", "the session is locked; nothing was sent")
        if want == guard.ANY or (snap.app == GENERIC and self._last == want):
            return
        try:
            guard.check_front(want, snap.app, snap.view, snap.confidence)
        except guard.WrongApp as e:
            refusal = guard.refusal(e, snap.spots)
            if snap.app not in (want, GENERIC, None):
                raise _Refuse(*refusal) from None
            # The app itself, or nothing, read with too little confidence: a view redrawing
            # after the step before (a chat opening). Wait for it to read as itself again.
            end = self.ex.now() + TIMEOUT_S
            while self.ex.now() < end:
                self.ex.settle()
                s = self._look()
                try:
                    guard.check_front(want, s.app, s.view, s.confidence)
                    return
                except guard.WrongApp:
                    if s.app not in (want, GENERIC, None):
                        break
            raise _Refuse(*refusal) from None

    def _keys(self, snap: Snapshot, chords: list[str]) -> None:
        """core.guard.check_keys under the policy of the app in front: a key it calls
        irreversible needs a call that allows irreversible steps."""
        policy = self.policies.get(snap.app or "", Policy())
        try:
            guard.check_keys(policy, chords, self.allow, f"{snap.app}.{snap.view}")
        except guard.Irreversible as e:
            raise _Refuse(*guard.refusal(e)) from None

    def _press_key(self, snap: Snapshot, keys: str) -> None:
        try:
            chord = guard.combo_key(keys)
        except ValueError as e:
            raise _Fail(str(e)) from None
        self._keys(snap, [chord])
        self.ex.press(keys)

    def _target(self, snap: Snapshot, t: dict[str, Any]) -> Hit:
        loc = locator(t)
        screen = (0, 0, *snap.size)
        regions, info = self._regions(snap), self._info(snap)
        spots = self._short(snap.spots)
        hit = locate(loc, snap.lines, regions, spots, screen, info, self.people)
        if isinstance(hit, Miss):
            raise _Fail(f"{loc.describe()}: {hit.reason}", hit.candidates)
        return hit

    def _short(self, named: Mapping[str, Any]) -> dict[str, Any]:
        """named, also by the short name of each of the action's own app's entries."""
        out = dict(named)
        for k, b in named.items():
            app, _, short = k.partition(".")
            if app == self.action.app:
                out.setdefault(short, b)
        return out

    def _regions(self, snap: Snapshot) -> dict[str, Box]:
        """Named regions, also by their short name for the action's own app."""
        return self._short(snap.regions)

    def _info(self, snap: Snapshot) -> dict[str, RegionInfo]:
        return self._short(snap.info)

    def _check(self, cond: Mapping[str, Any], snap: Snapshot) -> tuple[bool, str]:
        return _holds(cond, snap, self._regions(snap), self._info(snap), self.people)

    def _click(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        snap = self._look()
        hit = self._target(snap, p["target"])
        if not hit.how.startswith("spot"):
            self._guard(snap)
        self.ex.click(*hit.center, p.get("button", "left"), bool(p.get("double", False)))
        self.ex.settle()
        return hit.how

    def _type(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        text = str(p["text"]).replace("\r\n", "\n").replace("\r", "\n")
        snap = self._look()
        self._guard(snap)
        newline = _newline(s, self.action, self.policies.get(snap.app or "", Policy()))
        if "target" in p:
            hit = self._target(snap, p["target"])
            self.ex.click(*hit.center)
            self.ex.settle()
            snap = self._look()
            self._guard(snap)
        parts = text.split("\n")
        if len(parts) > 1 and newline == "forbid":
            raise _Refuse(
                "irreversible",
                "the text has a newline and this step may not type one (it could be Enter, "
                "which sends or runs); nothing was typed",
            )
        if len(parts) > 1:
            self._keys(snap, [guard.combo_key(newline)])
        for i, part in enumerate(parts):
            if part:
                self.ex.type(part)
            if i < len(parts) - 1:
                self.ex.press(newline)
        self.ex.settle()
        return f"{len(text)} characters" + (f", newlines as {newline}" if len(parts) > 1 else "")

    def _press(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        snap = self._look()
        self._guard(snap)
        self._press_key(snap, str(p["keys"]))
        self.ex.settle()
        return ""

    def _scroll(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        ticks, most = int(p.get("ticks", 3)), int(p.get("max", 10))
        step, grow = ticks, abs(int(p.get("max_ticks", 0)))
        until = p.get("until")
        got: list[Any] = []
        gaps = 0  # reads that shared no item with the read before
        last_seen: tuple[str, ...] | None = None
        n = still = last = 0  # last: the stride scrolled last
        waited = False
        why = f"{most} scrolls"
        for n in range(most + 1):
            snap = self._look()
            if p.get("collect"):
                its = self._items(snap, p)
                # Scrolling up (ticks > 0) reads further up: what is read now comes first.
                got, overlapped = _merge(its, got) if ticks > 0 else _merge(got, its)
                gaps += not overlapped
            if until and self._check(until, snap)[0]:
                why = f"{_describe(until)} held"
                if grow and not p.get("collect") and abs(last) > abs(ticks):
                    k = self._narrow(snap, p, until, last, abs(ticks))
                    why += f", {k} probes back down"
                break
            # end marks the list's end positively (a chat's first line): reached, nothing
            # further can hold until.
            if p.get("end") and self._check(p["end"], snap)[0]:
                why = "the list's end"
                break
            # A list that loads more as it nears its end holds still while it loads (a Teams
            # thread fetching older messages), so a list that stops moving is no proof of its
            # end: with an until still to hold it is a failure, said as one, never an empty
            # success. "Unchanged" is the region's text read the same: a caret blinking
            # elsewhere changes the frame.
            seen = tuple(ln.text for ln in self._pool(snap, p))
            still = still + 1 if seen == last_seen else 0
            if still >= STILL_ENDS:
                # Only history loads late; the newest end of a list is all there.
                if until and ticks > 0:
                    if not waited:
                        # A chat whose every message is newer than `from` would fail here at
                        # its start; history that loads comes within a few seconds.
                        waited = True
                        self.ex.wait(LOAD_WAIT_S)
                        continue
                    why = "the list's start: nothing older came after a wait"
                    self._warn(
                        f"{_describe(until)} never held: the list stopped moving and nothing "
                        f"older loaded in {LOAD_WAIT_S:g} s, so it was taken as its start"
                    )
                    break
                why = "the list stopped moving: its newest end"
                break
            box = self._box(snap, p["region"])
            if still:
                self.ex.settle(box)  # give a list that is loading more a moment longer
            if n == most:
                if until:
                    self._partial(got, p)
                    raise _Fail(f"scrolled {most} times and {_describe(until)} did not hold")
                break
            self._guard(snap)
            last_seen = seen
            at = (box[0] + box[2] // 2, box[1] + box[3] // 2)
            self.ex.scroll(*at, step)
            self._lifted[at] = self._lifted.get(at, 0) + step
            self.ex.settle(box)
            last = step
            # Doubling up to max_ticks: a start close to the newest end is found in a short
            # stride or two, one far back in a few long ones, and a stride never overshoots
            # by more than the way already come.
            step = max(-grow, min(grow, step * 2)) if grow else step
        if p.get("collect"):
            self._store(p["collect"], self._mapped(got), p)
            self.res.fields["stopped"] = why
            if gaps:
                self.res.fields["gaps"] = gaps
                self.res.fields["warning"] = (
                    f"{gaps} times a screen shared no item with the one before it: items "
                    "between them may be missing (the list moved more than a screen)"
                )
        return f"{n} scrolls, stopped: {why}" + (f", {gaps} gaps" if gaps else "")

    def _narrow(
        self, snap: Snapshot, p: dict[str, Any], until: Any, stride: int, least: int
    ) -> int:
        """Back down after a doubling stride overshot: until held after it, not before it,
        so its edge lies within the stride. Half the stride back, kept while until still
        holds, undone when not; then half that, down to least. The reads that follow (a
        collect a screen at a time) then start within a screen of the edge, not up to a
        whole stride above it: a 150-notch stride can mean some 20 screens of messages
        older than asked for. Returns the probes made."""
        box = self._box(snap, p["region"])
        at = (box[0] + box[2] // 2, box[1] + box[3] // 2)
        sign = 1 if stride > 0 else -1
        back, probes = abs(stride) // 2, 0
        while back >= least:
            self._guard(snap)
            self.ex.scroll(*at, -sign * back)
            self._lifted[at] = self._lifted.get(at, 0) - sign * back
            self.ex.settle(box)
            probes += 1
            snap = self._look()
            if not self._check(until, snap)[0]:
                self.ex.scroll(*at, sign * back)
                self._lifted[at] = self._lifted.get(at, 0) + sign * back
                self.ex.settle(box)
            back //= 2
        return probes

    def _partial(self, got: list[Any], p: dict[str, Any]) -> None:
        """What a collecting scroll read before it failed: a long read cut short still
        returns the part it has, with the failure saying where it stopped."""
        if p.get("collect"):
            self._store(p["collect"], self._mapped(got), p)

    def _box(self, snap: Snapshot, region: str) -> Box:
        regions = self._regions(snap)
        if region not in regions:
            raise _Fail(f"region '{region}' is not on screen ({_on(snap)})")
        return regions[region]

    def _pool(self, snap: Snapshot, p: dict[str, Any]) -> list[Line]:
        box = self._box(snap, p["region"])
        lines = self.ex.zoom(box) if p.get("zoom") else inside(snap.lines, box)
        if p.get("exclude"):
            gone = set(inside(lines, self._box(snap, p["exclude"])))
            lines = [ln for ln in lines if ln not in gone]
        return lines

    def _mapped(self, its: list[Any]) -> list[Any]:
        """A whole read's messages, or a whole collection's, as the people map has them."""
        return readers.mapped(its, self.people, datetime.now())

    def _items(self, snap: Snapshot, p: dict[str, Any]) -> list[Any]:
        """The region's items as its reader makes them (core.readers): what
        jharokha_read format=items gives for it."""
        box = self._box(snap, p["region"])
        info = self._info(snap).get(p["region"], RegionInfo())
        inner = list(info.inner)
        if p.get("exclude"):
            inner.append(self._box(snap, p["exclude"]))
        return readers.items(info.reader, self._pool(snap, p), box, info.kind, inner, snap.frame)

    def _store(
        self, into: str, its: list[Any], p: dict[str, Any], shaky: set[int] | None = None
    ) -> None:
        """its as the result's items under into, filtered by the step's since and upto,
        renamed by its fields; those whose id is in shaky marked unsteady."""
        for key, keep in (("since", listread.since), ("upto", listread.upto)):
            if not p.get(key):
                continue
            if not all(isinstance(i, listread.Item) for i in its):
                raise _Fail(f"{key} needs a list region; {p['region']} is not one")
            try:
                its = keep(its, str(p[key]), datetime.now())
            except ValueError as e:
                raise _Fail(f"{key}: {e}") from None
        rename = {v: k for k, v in (p.get("fields") or {}).items()}
        self.res.items[into] = [
            {rename.get(k, k): v for k, v in d.items()}
            | ({"unsteady": True} if shaky and id(it) in shaky else {})
            for it, d in zip(its, readers.plain(its), strict=True)
        ]

    def _wait(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        what = p.get("for")
        if what == "settled":
            got = self.ex.settle()
            return got.head() if isinstance(got, Settled) else ""
        elif what == "ready":
            self._until({"ready": True}, s.timeout_s, "the view to load")
        elif p.get("until"):
            self._until(p["until"], s.timeout_s, "waited for")
        else:
            raise _Fail("wait needs for = ready | settled, or until")
        return ""

    def _read(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        snap = self._look()
        if p.get("as", "lines") == "items":
            # Mapped before `last` cuts the read: the first kept may borrow a time from above.
            its = self._mapped(self._items(snap, p))
            # A second look a moment later, as jharokha_read takes: an item read differently
            # then was caught while the list drew or scrolled.
            self.ex.wait(AGREE_S)
            again = self._look()
            seen = {readers.key(i) for i in self._mapped(self._items(again, p))}
            shaky = (
                set()
                if again.frame_id == snap.frame_id
                else {id(i) for i in its if readers.key(i) not in seen}
            )
            after = self._after_mark(snap, p, its) if "mark" in p else None
            if after is not None:
                its = after
            elif p.get("last"):
                its = its[-int(p["last"]) :]
            self._store(p["into"], its, p, shaky)
            n = sum("unsteady" in d for d in self.res.items[p["into"]])
            return f"{len(self.res.items[p['into']])} items" + (f", {n} unsteady" if n else "")
        self.res.lines[p["into"]] = row_texts(self._pool(snap, p))
        return f"{len(self.res.lines[p['into']])} lines"

    def _after_mark(self, snap: Snapshot, p: dict[str, Any], its: list[Any]) -> list[Any] | None:
        """The items after the one the last read with the same mark ended on, and the mark
        moved to the newest; None the first time (the step's `last` then applies). The mark
        names the list: a chat's name or people-map id, or, empty, the chat the header shows.
        An item is known again by its sender and text: the time a message borrows from the
        header above it goes when that header scrolls off."""
        name = str(p["mark"]).strip()
        if not name and "header" in self._regions(snap):
            rows = row_texts(self._pool(snap, {"region": "header"}))
            name = next((c for r in rows if (c := self.people.chat_in(r))), " ".join(rows))
        cid = self.people.chat_of(name) or self.people.chat(name) if name else ""
        key = f"{self.action.app}:{cid or _compact(name)}"
        seen = [_seen(i) for i in its]
        prev = self.marks.get(key)
        if seen:
            self.marks[key] = seen[-1]
        if prev is None:
            self.res.fields["new"] = "first check of this chat: the newest messages"
            return None
        if prev in seen:
            k = len(seen) - 1 - seen[::-1].index(prev)
            self.res.fields["new"] = f"{len(seen) - 1 - k} since the last check"
            return its[k + 1 :]
        self.res.fields["new"] = "all on screen: the last check's newest is not among them"
        self._warn(
            "the newest message of the last check is not on screen any more: more came "
            "than one screen shows; read from its time with teams.messages"
        )
        return its

    def _extract(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        if "items" in p:
            got = self.res.items.get(p["items"], [])
            pick = p.get("pick", "last")
            into = p.get("into", p.get("field", pick))
            if pick == "count":
                self.res.fields[into] = len(got)
            elif got:
                one = got[0 if pick == "first" else -1]
                self.res.fields[into] = one.get(p.get("field", "text"))
            return ""
        snap = self._look()
        pool = self._pool(snap, p) if p.get("region") else snap.lines
        pat = re.compile(str(p["regex"]), re.IGNORECASE)
        for row in row_texts(pool):
            if m := pat.search(row):
                self.res.fields.update({k: v for k, v in m.groupdict().items() if v is not None})
                return ""
        if p.get("required", True):
            raise _Fail(f"nothing on screen matches {p['regex']!r}")
        return "no match"

    def _repeat(self, s: Step, p: dict[str, Any], env: dict[str, Any], prefix: str) -> str:
        if "each" in p:
            values = list(env[s.params["each"]])
        else:
            values = [None] * min(MAX_REPEAT, int(p.get("times", p.get("max", MAX_REPEAT))))
        var = s.params.get("as", "item")
        n = 0
        for n, v in enumerate(values, 1):
            inner = {**env, var: v} if "each" in p else env
            self._steps(s.steps, inner, f"{prefix}{n}.")
            if p.get("until"):
                snap = self._look()
                cond = fill(s.params["until"], inner)
                if self._check(cond, snap)[0]:
                    break
        return f"{n} times"

    def _shell_box(self, snap: Snapshot, p: dict[str, Any]) -> Box:
        region = p.get("region")
        regions = self._regions(snap)
        return regions[region] if region and region in regions else (0, 0, *snap.size)

    # The shell (core.shell): a tab prepared to show its state in its prompt

    def _rows(self, snap: Snapshot, p: dict[str, Any]) -> list[str]:
        """The shell's rows as text, a wrapped row joined to the one it continues on."""
        box = self._shell_box(snap, p)
        return sh.split(unwrapped(inside(snap.lines, box), box[0] + box[2]))

    def _run(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        if p.get("setup"):
            return self._setup(s, p)
        if p.get("result"):
            return self._result(s, p)
        if p.get("output"):
            return self._output(s, p)
        self._prepare(s, p)
        command = str(p["command"])
        if "\n" in command or "\r" in command:
            raise _Fail("a command is one line; nothing was typed")
        base = int(p.get("first", 1)) - 1
        clear = bool(p.get("clear", True))
        return self._answer(self._command(s, p, command, base=base, clear=clear))

    def _result(self, s: Step, p: dict[str, Any]) -> str:
        """What the last command sent printed, as a run returns it: once the shell is idle
        at a state prompt, the command before that prompt has ended. Waited for up to the
        step's timeout while it runs (its own `cls` takes the prompt off the screen), at
        once when it has ended. For a run that timed out: its command goes on, and this is
        how its end is read, rather than a sleep and a guess. A long wait looks once a
        second after its first ten: a six-minute test run needs no ten looks a second."""
        start = self.ex.now()
        end = start + s.timeout_s
        while True:
            snap = self._look()
            rows = self._rows(snap, p)
            if q := sh.idle(rows):
                self.res.fields["prompt"] = q.n - 1
                self.res.fields["state"] = "finished"
                r = sh.reply(rows, q.n - 1, 0)
                return self._answer(self._paged(s, p, r, 0))
            if self.ex.now() >= end:
                ps = sh.prompts(rows)
                self.res.output = sh.reply(rows, ps[-1].n if ps else 0, 0).lines
                self.res.fields["finished"] = False
                code, why = self._running(snap, rows)
                self.res.fields["state"] = "running" if code == "still_running" else code
                raise _Fail(f"after {s.timeout_s:g} s: {why}", code=code)
            self.ex.wait(POLL_S if self.ex.now() - start < 10 else 1.0)

    def _running(self, snap: Snapshot, rows: list[str]) -> tuple[str, str]:
        """Why the shell in front is not idle at a state prompt, as a code: still_running
        (the terminal in front, no prompt below the command: it goes on), input_pending
        (text sits at the last prompt), or unknown (another app or a program is in front,
        so nothing can be told)."""
        if snap.app not in (self.action.app, GENERIC, None):
            return "unknown", (
                f"{snap.app} is in front, so whether the command ended cannot be read; "
                "nothing more was sent. Bring the terminal forward and call terminal.result"
            )
        if what := sh.not_a_shell(rows):
            return "unknown", (
                f"the terminal tab in front runs a program, not the shell ({what!r}): bring "
                "the prepared PowerShell tab forward and call terminal.result"
            )
        ps = sh.prompts(rows)
        if ps and (got := sh.typed(rows, ps[-1])) and ps[-1].row + 2 >= len(rows):
            return "input_pending", (
                f"the last command has ended, but the input holds {got[:80]!r}: someone "
                "typed into it; clear it (jharokha_act press escape) or read terminal.body"
            )
        return "still_running", (
            "the command is still running (no state prompt below it; output holds what it "
            "printed so far): call terminal.result again with a longer timeout_s, or press "
            "ctrl+c with jharokha_act to stop it"
        )

    def _answer(self, r: sh.Reply) -> str:
        """A command's reply as the result: its output, exit code, status and directory."""
        assert r.prompt is not None
        self.res.output = r.lines
        self.res.exit_code = r.prompt.code
        self.res.fields["ok"] = r.prompt.ok
        self.res.fields["lines"] = r.last
        self.res.fields["cwd"] = r.prompt.cwd
        unknown = getattr(self.ex, "unknown_cells", None)
        if unknown and (k := unknown()) > UNKNOWN_WARN:
            self._warn(
                f"{k} cells matched no learned glyph and were read by OCR, which garbles "
                "terminal text: run terminal.setup (again after a zoom, font or window "
                "size change) and read the output again"
            )
        return f"exit {r.prompt.code}"

    def _shell_only(self, rows: list[str]) -> None:
        """Refuses a tab whose rows show a full-screen program (Claude Code's TUI) instead
        of a shell: nothing typed there is a command."""
        if what := sh.not_a_shell(rows):
            raise _Fail(
                f"nothing was sent: the terminal tab in front runs a program, not a shell "
                f"({what!r} is on screen). Bring the PowerShell tab forward (its tab title, "
                "or ctrl+tab with jharokha_act) and retry",
                code="not_a_shell",
            )

    def _prepare(self, s: Step, p: dict[str, Any]) -> None:
        """A run goes only into a tab that shows its state prompt: one this server
        prepared. A run that found none and prepared the tab in front by itself could take
        a tab running Claude Code and paste the setup line into it. A tab is prepared only
        by an explicit terminal.setup. In a prepared tab whose
        glyphs this executor does not know yet (a new process), setup is sent again: its
        cells otherwise go to the engine, which garbles lookalikes, so a line checked letter
        by letter fails on its own echo; repeating it is safe, the counter goes on."""
        rows = self._rows(self._look(), p)
        self._shell_only(rows)
        if not sh.prepared(rows):
            where = "brought forward" if self._switched else "in front"
            raise _Fail(
                f"nothing was sent: the terminal tab {where} shows no JK<n> state prompt, "
                "so it is not a tab jharokha prepared (another tab, the user's own shell, a "
                "program, or a command whose output fills the screen). Read terminal.body: "
                "a command still running ends with terminal.result; if this is the "
                "PowerShell tab to use, idle at its prompt, call terminal.setup there (only "
                "with the user's say-so if it is their own shell)",
                code="not_prepared",
            )
        known = getattr(self.ex, "glyphs", None)
        n = known() if known else None
        if n is not None and n < GLYPHS_MIN:
            self._setup(s, p)

    def _setup(self, s: Step, p: dict[str, Any]) -> str:
        """core.shell.SETUP sent and its first state prompt waited for, then the glyphs
        learned from the alphabet it printed. Only into a shell waiting at its prompt: not
        a program's screen (Claude Code's TUI takes the line as a prompt of its own), not a
        command still running. The line cannot be checked letter by letter before the font
        is known: it is sent once the screen holds still, and the state row is what tells
        it ran."""
        snap = self._look()
        self._guard(snap)
        rows = self._rows(snap, p)
        self._shell_only(rows)
        if sh.idle(rows) is None and not sh.at_prompt(rows):
            raise _Fail(
                "nothing was sent: the tab in front shows no shell prompt waiting at its "
                "bottom (a PowerShell `PS ...>` or a prepared `>`): a command may be running, "
                "or a program has the tab. Read terminal.body",
                code="no_prompt",
            )
        how = self._put_line(sh.SETUP)
        self.ex.settle()
        self.ex.press("enter")
        self.res.fields["input"] = how
        end = self.ex.now() + s.timeout_s
        while True:
            rows = self._rows(self._look(), p)
            # The line clears the screen as it runs: a state row with the typed line gone
            # is the new prompt, whether the glyphs are known yet or not.
            if sh.prepared(rows) and not any("function prompt" in r for r in rows):
                break
            if self.ex.now() >= end:
                raise _Fail(
                    f"the setup line brought no state prompt up within {s.timeout_s:g} s. "
                    "Read terminal.body: the shell may be busy, or not the one in front; if "
                    "it is idle, its keys did not arrive",
                    code="unknown",
                )
            self.ex.wait(POLL_S)
        self.res.fields["prepared"] = True
        n = self._learn()
        return f"prepared, {n} glyphs learned"

    def _learn(self) -> int:
        learner = getattr(self.ex, "learn_glyphs", None)
        n = learner() if learner else 0
        self.res.fields["glyphs"] = n
        if not n:
            self.res.fields["warning"] = (
                "the alphabet lines were not found on screen, so the font was not learned "
                "and terminal text is read by OCR; run terminal.setup"
            )
        return n

    def _command(
        self,
        s: Step,
        p: dict[str, Any],
        command: str,
        base: int = 0,
        clear: bool = True,
        raw: bool = False,
        typed: bool = False,
    ) -> sh.Reply:
        """command run in the prepared shell: sent as one line (wrapped for numbering
        unless raw), ended by the next state prompt, its start fetched by paging when it
        scrolled off the top. typed sends it key by key whatever its length: a paste would
        replace the clipboard a write_file holds."""
        prompt = self._ready(s, p)
        if raw:
            line = "cls; " + command if clear else command
        else:
            line = sh.wrapped(command, base, clear)
        self.res.fields["input"] = self._send(line, p, prompt, typed)
        return self._paged(s, p, self._reply(p, prompt.n, base, s.timeout_s), base)

    def _ready(self, s: Step, p: dict[str, Any]) -> sh.Prompt:
        """The idle prompt a line goes in after, or a failure that says nothing was sent."""
        try:
            return self._idle(s, p)
        except _Fail as e:
            raise _Fail(f"nothing was sent: {e.reason}", code=e.code) from None

    def _paged(self, s: Step, p: dict[str, Any], r: sh.Reply, base: int) -> sh.Reply:
        """r with the lines that scrolled off the top fetched again, a page at a time."""
        assert r.prompt is not None
        lines, n = r.lines, r.prompt.n
        pages = 0
        try:
            while r.first > base + 1 and pages < PAGE_MAX:
                # The start scrolled off: the lines before the first on screen, a screen at
                # a time, each page a command of its own under the next prompt number.
                fit = max(1, self._fit(p))
                first = max(base + 1, r.first - fit)
                pg = self._page(s, p, first, r.first - first, base, n)
                if pg.first >= r.first:
                    break  # nothing more came: what is read is what there is
                lines = pg.lines + lines
                r, n = replace(r, first=pg.first), n + 1
                pages += 1
        except _Fail as e:
            # The command has run: its status and what was read are the answer, never a
            # failure that reads as if nothing was sent (a caller would run it twice).
            self.res.fields["missing_lines"] = [base + 1, r.first - 1]
            self._warn(
                f"output lines {base + 1} to {r.first - 1} scrolled off the top and were not "
                f"fetched again ({e.reason}); the command itself ran: terminal.output "
                f"first={base + 1} fetches them"
            )
        if pages:
            self.res.fields["pages"] = pages + 1
        return replace(r, lines=lines)

    def _warn(self, text: str) -> None:
        """A warning added to the result's, never replacing one already there."""
        had = self.res.fields.get("warning")
        self.res.fields["warning"] = f"{had}; {text}" if had else text

    def _page(
        self, s: Step, p: dict[str, Any], first: int, count: int, base: int, n: int
    ) -> sh.Reply:
        self.res.fields["input"] = self._send(sh.page(first, count, base), p, self._idle(s, p))
        return self._reply(p, n, first - 1, s.timeout_s)

    def _output(self, s: Step, p: dict[str, Any]) -> str:
        """Lines of the last command's output, from the variable the `jk` filter kept them
        in (the shell's own buffer, not the screen's scrollback), a screenful per page
        however few rows the pane shows: count lines from line first on, fewer at the end.
        Without it, an agent pages long output by hand with `cls; $a[n..n+19]`, a call per
        screen. Nothing is run again; the variable is left as it was."""
        self._prepare(s, p)
        start, want = int(p.get("first", 1)), int(p.get("count", 100))
        at = start
        lines: list[str] = []
        total: int | None = None
        fit = max(1, self._fit(p) - 1)  # the total's own row
        tries = 0
        while len(lines) < want and (total is None or at <= total):
            k = min(fit, want - len(lines))
            prompt = self._ready(s, p)
            self.res.fields["input"] = self._send(sh.page_from(at, k), p, prompt)
            r = self._reply(p, prompt.n, at - 1, s.timeout_s)
            total = sh.total(self._rows(self._look(), p))
            if not r.lines:
                break
            if r.first > at:
                # The page overflowed the screen: fetch it again, smaller.
                tries += 1
                if tries > 3:
                    raise _Fail(f"lines from {at} on do not fit on screen, even {k} at a time")
                fit = max(1, k - (r.first - at) - 1)
                continue
            lines += r.lines[: want - len(lines)]
            at = r.first + len(r.lines)
        self.res.output = lines
        self.res.fields["first"] = start
        self.res.fields["last"] = start + len(lines) - 1 if lines else None
        self.res.fields["total"] = total
        self.res.fields["more"] = bool(total is not None and start + len(lines) <= total)
        if total is None:
            self._warn("the count of output lines was not read: page on by line number")
        return f"{len(lines)} lines of {total}"

    def _fit(self, p: dict[str, Any]) -> int:
        """Lines a page may hold: the rows the shell's box shows, less the prompt's two
        and a margin for lines that wrap."""
        snap = self._look()
        box = self._shell_box(snap, p)
        tops = sorted({ln.box[1] for ln in inside(snap.lines, box)})
        gaps = [b - a for a, b in zip(tops, tops[1:], strict=False) if b - a > 2]
        pitch = min(gaps) if gaps else ROW_PX
        return box[3] // pitch - 4

    def _idle(self, s: Step, p: dict[str, Any]) -> sh.Prompt:
        """The empty prompt at the bottom of the screen, waited for a few seconds (the
        shell drawing its prompt after the command before); a view scrolled up is brought
        down once. Fails when the input holds text (someone typed into the shell), a
        program has the tab (refused at once, never scrolled) or no prompt comes (a
        command still running)."""
        end = self.ex.now() + TIMEOUT_S
        bottomed = False
        while True:
            snap = self._look()
            rows = self._rows(snap, p)
            if p_ := sh.idle(rows):
                return p_
            self._shell_only(rows)
            if self.ex.now() >= end:
                ps = sh.prompts(rows)
                # Text after the last prompt with nothing under it, still there after the
                # wait, is typed by hand: a running command prints or finishes.
                if ps and (got := sh.typed(rows, ps[-1])) and ps[-1].row + 2 >= len(rows):
                    raise _Fail(
                        f"the shell's input already holds {got[:80]!r}: someone typed into "
                        "it; clear it (jharokha_act press escape) and retry",
                        code="input_pending",
                    )
                if not bottomed:
                    bottomed = True
                    self._guard(snap)
                    box = self._shell_box(snap, p)
                    self.ex.scroll(box[0] + box[2] // 2, box[1] + box[3] // 2, -BOTTOM_TICKS)
                    self.ex.settle(box)
                    end = self.ex.now() + TIMEOUT_S / 2
                    continue
                raise _Fail(
                    "no empty prompt at the bottom of the shell: a command may still be "
                    "running (terminal.result waits for its end and returns its output; "
                    "ctrl+c with jharokha_act stops it), or this is not the prepared tab",
                    code="no_prompt",
                )
            self.ex.wait(POLL_S)

    def _put_line(self, line: str) -> str:
        """line into the shell's input: typed when short, pasted when long (the session
        fetches the clipboard, a second or so), typed when the paste never arrives."""
        paste = getattr(self.ex, "paste", None)
        if paste is None or len(line) <= TYPE_MAX:
            self.ex.type(line)
            return "typed"
        try:
            paste(line)
            return "pasted"
        except ScreenError as e:
            if e.code != "paste_failed":
                raise
            self.ex.press("escape")  # whatever part of it came in
            self.ex.type(line)
            return "typed: " + str(e).removeprefix("paste_failed: ")

    def _sent(self, line: str, typed: bool) -> str:
        """line into the input, once more when the user's own key stopped it part-way: the
        part that went is cleared (Escape, sent once the user is idle again, as all input
        is), so no half line is left at the prompt. A second stop clears it again and
        refuses."""
        for attempt in (1, 2):
            try:
                if typed:
                    self.ex.type(line)
                    how = "typed"
                else:
                    how = self._put_line(line)
                return how + ("; again after the user's key" if attempt > 1 else "")
            except ScreenError as e:
                if e.code != "user_active" or not e.sent:
                    raise  # nothing of the line went: nothing to clear
                why = str(e).removeprefix("user_active: ")
                try:
                    self.ex.press("escape")
                except ScreenError as e2:
                    raise ScreenError(
                        "user_active",
                        f"{why} The {e.sent} characters sent are still in the shell's "
                        f"input: clearing them was refused ({e2.code}); press escape before "
                        "the next command",
                    ) from None
                if attempt == 2:
                    raise ScreenError(
                        "user_active",
                        f"{why} Stopped twice; what was sent is cleared (Escape) and nothing "
                        "was run",
                    ) from None
        raise AssertionError("unreachable")

    def _send(self, line: str, p: dict[str, Any], prompt: sh.Prompt, typed: bool = False) -> str:
        """line into the input row after prompt, and Enter only once the row shows exactly
        it: a line is never run with a key dropped or half of it in. Sent again once when
        it does not; then Escape clears it and nothing is run."""
        snap = self._look(fast=True)
        self._guard(snap)
        self._keys(snap, ["enter"])
        how = self._sent(line, typed)
        for attempt in (1, 2):
            seen = self._arrived(line, p, prompt)
            if seen is None:
                break
            self.ex.press("escape")
            if attempt == 1:
                self.ex.type(line)
                how += "; typed again"
                continue
            raise _Fail(
                f"the shell's input showed {seen[:80]!r} where the sent line should be, "
                f"twice; Escape cleared it and nothing was run. {_why_not_shown(seen, line)}",
                code="echo_mismatch",
            )
        self.ex.press("enter")
        return how

    def _arrived(self, line: str, p: dict[str, Any], prompt: sh.Prompt) -> str | None:
        """None once the input row shows line; else what it showed when it stopped
        changing, or when the time a line of that length can take had passed."""
        end = self.ex.now() + ARRIVE_S + len(line) / ARRIVE_CPS
        last: str | None = None
        quiet = self.ex.now()
        while True:
            rows = self._rows(self._look(), p)
            at = next((q for q in sh.prompts(rows) if q.n == prompt.n), None)
            got = sh.typed(rows, at) if at else None
            if got is not None and sh.looks_like(got, line):
                return None
            now = self.ex.now()
            if got != last:
                last, quiet = got, now
            elif now - quiet >= STABLE_S and (got or now >= end):
                return got or ""
            if now >= end:
                return got or ""
            self.ex.wait(POLL_S)

    def _reply(self, p: dict[str, Any], n: int, base: int, timeout: float) -> sh.Reply:
        """What command n printed, polled until the state prompt n+1 is on screen. At the
        timeout the view is brought to its bottom once (a view scrolled up does not follow
        new output) and looked at again; then what was printed so far goes into the
        result with the failure, coded still_running or unknown."""
        start = self.ex.now()
        end = start + timeout
        bottomed = False
        while True:
            snap = self._look()
            box = self._shell_box(snap, p)
            rows = self._rows(snap, p)
            r = sh.reply(rows, n, base)
            if r.prompt is not None:
                return r
            if self.ex.now() >= end:
                if snap.app not in (self.action.app, GENERIC, None):
                    # The user may switch apps during a long command, which must not then
                    # fail wrong_app though it was typed and is running.
                    self.res.fields["finished"] = False
                    self.res.fields["state"] = "unknown"
                    raise _Fail(
                        f"no state prompt within {timeout:g} s, and {snap.app} is in front "
                        "now: the command was typed and may still be running behind it; "
                        "nothing more was sent. Bring the terminal forward when the user is "
                        "done and call terminal.result",
                        code="unknown",
                    )
                if not bottomed:
                    bottomed = True
                    self._guard(snap)
                    self.ex.scroll(box[0] + box[2] // 2, box[1] + box[3] // 2, -BOTTOM_TICKS)
                    self.ex.settle(box)
                    continue
                self.res.output = r.lines
                self.res.fields["finished"] = False
                code, why = self._running(snap, rows)
                self.res.fields["state"] = "running" if code == "still_running" else code
                raise _Fail(f"no state prompt within {timeout:g} s: {why}", code=code)
            self.ex.wait(POLL_S if self.ex.now() - start < 10 else 1.0)

    def _write_file(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        """The text into this machine's clipboard, one command that writes the clipboard
        to the file and prints its SHA-256, checked against the text's. Nothing of the
        text is typed or shown."""
        text, path = str(p["text"]), str(p["path"])
        hold = getattr(self.ex, "clipboard", None)
        if hold is None:
            raise _Fail("this executor cannot hold the clipboard; nothing was sent")
        self._prepare(s, p)
        want = sh.sha256(text)
        restore = hold(text)
        try:
            r = self._command(s, p, sh.write_file(path), typed=True)
        finally:
            restore()
        assert r.prompt is not None
        check = sh.check_hash(r.lines, want)
        size = len(text.encode("utf-8"))
        self.res.fields.update({"sha256": want, "size": size, "hash_check": check})
        self.res.exit_code = r.prompt.code
        if check == "mismatch" or not r.prompt.ok:
            shown = r.lines[0][:80] if r.lines else "nothing"
            raise _Fail(
                f"the file's SHA-256 on the remote side is not {want}: the shell printed "
                f"{shown!r}. The clipboard did not reach the session as sent, or the path "
                "cannot be written"
            )
        return f"{size} bytes, hash {check}"

    def _edit_file(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        """old replaced by new in the file at path, only where old occurs exactly once:
        both go through this machine's clipboard as plain JSON, and one typed command
        (core.shell.edit_once) checks they arrived, counts old, writes the file and prints
        its new SHA-256, so an agent need not type a replace helper of its own each time."""
        path, old, new = str(p["path"]), str(p["old"]), str(p["new"])
        hold = getattr(self.ex, "clipboard", None)
        if hold is None:
            raise _Fail("this executor cannot hold the clipboard; nothing was sent")
        self._prepare(s, p)
        restore = hold(sh.edit_payload(old, new))
        try:
            r = self._command(s, p, sh.edit_once(path, old, new), typed=True)
        finally:
            restore()
        assert r.prompt is not None
        self.res.exit_code = r.prompt.code
        self.res.output = r.lines
        kind, count, sha = sh.edit_result(r.lines)
        if kind == "clip_bad":
            raise _Fail(
                "the clipboard did not reach the session as sent (old and new arrived with "
                "other lengths); nothing was written. Retry",
                code="clipboard_mismatch",
            )
        if kind == "unknown" or count is None:
            shown = r.lines[0][:120] if r.lines else "nothing"
            raise _Fail(
                f"the edit printed no count but {shown!r}: the file may not exist or not be "
                "readable; nothing was written",
                code="edit_failed",
            )
        self.res.fields["count"] = count
        if count != 1:
            why = (
                f"old occurs {count} times: give more of the text around it so it occurs once"
                if count
                else "old is not in the file (every space and line ending counts; read the "
                "lines with terminal.read_file and copy them exactly)"
            )
            raise _Fail(f"nothing was written: {why}", code="edit_count")
        if not r.prompt.ok:
            raise _Fail(
                f"the file could not be written: {' '.join(r.lines)[:160]!r}", code="edit_failed"
            )
        self.res.fields["sha256"] = sha
        if sha is None:
            self._warn("the new SHA-256 was not read off the screen; the edit was written")
        return f"replaced once, sha256 {sha}"

    # Conditions

    def _until(self, cond: dict[str, Any], timeout: float, what: str) -> Snapshot:
        end = self.ex.now() + timeout
        while True:
            snap = self._look()
            ok, why = self._check(cond, snap)
            if ok:
                return snap
            if self.ex.now() >= end:
                raise _Fail(f"{what} {_describe(cond)} within {timeout:g} s: {why}")
            self.ex.wait(POLL_S)


def _why_not_shown(seen: str, line: str) -> str:
    """What a sent line that did not show as sent most likely means, by what showed."""
    if not seen:
        return (
            "Nothing arrived: read terminal.body; the shell may be busy or not the one in "
            "front, or the keys went to another window on this computer (jharokha_status: "
            "foreground)"
        )
    if len(seen) < len(line) and sh.looks_like(seen, line[: len(seen)]):
        return (
            f"Its first {len(seen)} of {len(line)} characters showed: the rest most likely "
            "wrapped onto a row below the terminal's region (jharokha_status warns "
            "calibration_stale), not lost keys"
        )
    return (
        "It showed otherwise than sent: a misread of the row or keys mixed with others. "
        "Read terminal.body before retrying"
    )


def _on(snap: Snapshot) -> str:
    if snap.locked:
        return "the session is locked"
    return f"{snap.app or 'nothing recognised'} {snap.view or ''} is in front".replace("  ", " ")


def _describe(cond: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in cond.items())


def _holds(
    cond: Mapping[str, Any],
    snap: Snapshot,
    regions: Mapping[str, Box],
    info: Mapping[str, RegionInfo] | None = None,
    people: People | None = None,
) -> tuple[bool, str]:
    if "app" in cond and snap.app != cond["app"]:
        return False, _on(snap)
    if "view" in cond and snap.view != cond["view"]:
        return False, _on(snap)
    if "ready" in cond and (snap.ready is not False) != bool(cond["ready"]):
        return False, f"{snap.app} {snap.view} is still loading"
    pool = snap.lines
    if "region" in cond:
        if cond["region"] not in regions:
            return False, f"region '{cond['region']}' is not on screen ({_on(snap)})"
        pool = inside(snap.lines, regions[cond["region"]])
    if "text" in cond:
        # Of typed text over several lines, a box that scrolls shows the last line at least,
        # and of a long one, wrapped over rows whose first may be scrolled away, its end.
        # An empty text (an optional argument left out) asks for nothing.
        last = [t for t in str(cond["text"]).splitlines() if t.strip()][-1:]
        if last and not find(pool, last[0]) and not find(_wrapped(pool), _tail(last[0])):
            return False, f"'{last[0]}' is not on screen"
    if "name" in cond and not name_on(pool, str(cond["name"]), people):
        return False, f"'{cond['name']}' is not the name shown"
    if "regex" in cond and not find(pool, str(cond["regex"]), regex=True):
        return False, "the regex matches nothing on screen"
    if "gone" in cond and find(pool, str(cond["gone"])):
        return False, f"'{cond['gone']}' is still on screen"
    for key, test, miss in (
        ("before", listread.before, "nothing on screen is that old yet"),
        ("after", listread.after, "nothing on screen is that recent yet"),
    ):
        if key not in cond:
            continue
        name = cond.get("region")
        reg = (info or {}).get(name) if name else None
        if reg is None or reg.reader != "list":
            return False, f"{key} needs a list region (one whose reader is list)"
        assert name is not None
        its = readers.items(reg.reader, pool, regions[name], reg.kind, list(reg.inner), snap.frame)
        try:
            if not test(its, str(cond[key]), datetime.now()):
                return False, miss
        except ValueError as e:
            return False, str(e)
    return True, ""


TAIL = 60  # characters of a long line's end looked for: under two rows of a compose box
WRAP_ROWS = 3


def _tail(s: str) -> str:
    """The last words of s, up to TAIL characters (one word at least)."""
    words = s.split()
    n = 1
    while n < len(words) and len(" ".join(words[-n - 1 :])) <= TAIL:
        n += 1
    return " ".join(words[-n:])


def _wrapped(lines: list[Line]) -> list[Line]:
    """Each line joined with the rows wrapped under it, up to WRAP_ROWS: the next rows
    starting at its left edge, each within two heights below the one before."""
    rows = sorted(lines, key=lambda ln: ln.box[1])
    out = []
    for i, first in enumerate(rows):
        run = [first]
        for ln in rows[i + 1 :]:
            prev = run[-1]
            if len(run) == WRAP_ROWS or ln.box[1] - prev.box[1] > 2 * prev.box[3]:
                break
            if abs(ln.box[0] - first.box[0]) <= first.box[3] and ln.box[1] > prev.box[1]:
                run.append(ln)
        if len(run) > 1:
            x, y = first.box[0], first.box[1]
            right = max(ln.box[0] + ln.box[2] for ln in run)
            bottom = run[-1].box[1] + run[-1].box[3]
            words = tuple(w for ln in run for w in ln.words)
            out.append(Line(" ".join(ln.text for ln in run), (x, y, right - x, bottom - y), words))
    return out


_ELLIPSIS = re.compile(r"(?:\.{2,}|…)$")
_CUT_DOT = re.compile(r"(\w)\.(\s*,?\s*\+\d)")
# A group's count after its cut name; OCR may lose the count and keep its comma.
_COUNT = re.compile(r"^(?:,|,?\+\d+)$")
_GROUP = re.compile(r",|\sand\s")  # a group chat as the list names it: `Ana, Ben, +2`
NAME_RATIO = 0.85  # how alike a name and what a header shows of it must read


def name_on(lines: list[Line], want: str, people: People | None = None) -> bool:
    """Whether a row shows the name want as a header does: whole, or cut short at its end
    (`Ana Ald…`), with an avatar's initials or an icon OCR read as a stray glyph or two in
    front, and a character or two misread. An empty name is always shown. want an id of
    the people map is the chat it names, shown under any of that chat's names (core.people:
    cut in the middle, `Ana Ald…, +2`), and not a chat whose name only starts the same."""
    if people and (cid := people.chat_of(want)):
        return any(people.chat_in(row) == cid for row in row_texts(lines))
    w = _compact(want)
    if not w:
        return True
    group = bool(_GROUP.search(want))
    for row in row_texts(lines):
        if w in _compact(row):
            return True
        # OCR reads a group header's ellipsis as one dot too, losing the comma at times
        # (`Ana A., +2`, `Ana. +2`).
        row = _CUT_DOT.sub("\\1…\\2", row)
        # A group chat's count follows the cut name unspaced: `Ana Ald...,+2`. Not a dot:
        # `...` would split as `.. .` and lose its ellipsis.
        tokens = re.sub(r"(\.{2,}|…)(?=[^\s.])", r"\1 ", row).split()
        for j, tok in enumerate(tokens):
            cut = bool(_ELLIPSIS.search(tok))
            if not cut and j < len(tokens) - 1:
                continue  # a cut name ends at its ellipsis, a whole one anywhere
            # A group's header cuts its members' names short before their count, where the
            # list joins them (`Ana and Ben` over `Ana…, +2`): its start is enough.
            # Only a group's name: a person's chat starting the same is another chat.
            counted = group and _COUNT.match("".join(tokens[j + 1 : j + 3]))
            for i in range(j + 1):
                got = _compact(" ".join(tokens[i : j + 1]))
                if not got:
                    continue
                if difflib.SequenceMatcher(None, got, w, autojunk=False).ratio() >= NAME_RATIO:
                    return True
                least = 4 if counted else max(4, len(w) // 2)
                if cut and len(got) >= least and w.startswith(got):
                    return True
    return False


def _seen(it: Any) -> str:
    d = it.as_dict() if hasattr(it, "as_dict") else dict(it)
    return _compact(f"{d.get('who') or d.get('sender') or ''} {readers.item_text(it)}")


def _compact(s: str) -> str:
    return re.sub(r"[\W_]+", "", fold(_ELLIPSIS.sub("", s.strip()))).lower()


def _merge(older: list[Any], newer: list[Any]) -> tuple[list[Any], bool]:
    """Items read at two scroll positions as one list, each once, and whether the two
    reads shared an item: older is the read further up. listread.merged for a list's
    items; for other readers, by their text."""
    if all(isinstance(i, listread.Item) for i in [*older, *newer]):
        return listread.merged(older, newer)
    seen = {fold(readers.item_text(i)).replace(" ", "") for i in older}
    new = [i for i in newer if fold(readers.item_text(i)).replace(" ", "") not in seen]
    return older + new, not older or not newer or len(new) < len(newer)


def _newline(s: Step, action: Action, policy: Policy) -> str:
    """The key a typed newline is: the step's, the action's, the app's policy's (Shift+Enter
    in Teams), else none (forbid)."""
    return s.params.get("newline") or action.newline or policy.newline or "forbid"


def _sends(
    action: Action, steps: tuple[Step, ...], env: Mapping[str, Any], prefix: str, policy: Policy
) -> tuple[str, str] | None:
    """The first step that would send or submit, with why: a press of a key the app's
    policy calls irreversible (Enter in Teams), or typed text with a newline that is Enter
    or such a key. None when no step would."""
    for i, s in enumerate(steps):
        label = f"{prefix}{i + 1}:{s.kind}"
        try:
            p = fill(s.params, env)
        except KeyError:
            p = s.params  # a repeat variable, filled per pass
        try:
            if s.kind == "press" and policy.irreversible(str(p.get("keys", ""))):
                return label, f"({p['keys']} is irreversible in {action.app})"
            if s.kind == "type":
                text = str(p.get("text", ""))
                newline = _newline(s, action, policy)
                if ("\n" in text or "\r" in text) and newline != "forbid":
                    if newline == "enter" or policy.irreversible(newline):
                        return label, f"(a newline typed as {newline})"
        except ValueError:
            pass  # a key that is no key: refused when pressed
        if inner := _sends(action, s.steps, env, f"{prefix}{i + 1}.", policy):
            return inner
    return None


def _plan(
    action: Action, steps: tuple[Step, ...], env: Mapping[str, Any], prefix: str
) -> list[dict[str, Any]]:
    out = []
    for i, s in enumerate(steps):
        try:
            p = fill(s.params, env)
        except KeyError:
            p = dict(s.params)
        p.pop("step", None)
        if s.kind == "write_file":
            p["text"] = f"<{len(str(p.get('text', '')))} characters>"
        if s.kind == "edit_file":
            for k in ("old", "new"):
                p[k] = f"<{len(str(p.get(k, '')))} characters>"
        row: dict[str, Any] = {"step": f"{prefix}{i + 1}", "do": s.kind}
        row |= {k: v for k, v in p.items()}
        for k, cond in (("unless", s.unless), ("expect", s.expect)):
            if cond:
                try:
                    row[k] = fill(cond, env)
                except KeyError:
                    row[k] = cond
        if s.steps:
            row["steps"] = _plan(action, s.steps, env, f"{prefix}{i + 1}.")
        out.append(row)
    return out


def describe(action: Action) -> dict[str, Any]:
    """What an `apps`/`do` tool lists for an action: its arguments."""
    return {
        "action": action.id,
        "summary": action.summary,
        "args": {
            k: {"type": a.type, "required": a.required}
            | ({"doc": a.doc} if a.doc else {})
            | ({"choices": list(a.choices)} if a.choices else {})
            for k, a in action.args.items()
        },
    }
