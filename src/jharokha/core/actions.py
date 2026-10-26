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
    run       command in the shell in front, ended by core.shell's marker; exit code;
              clear (cls) first; learn = "glyphs" prints the alphabet instead and learns
              the terminal's glyphs from it (core.glyphs)
    put_file  path, content_base64; per-chunk confirmation, then the SHA-256 checked
    write_file  path, text: the text's lines typed in batches, each confirmed by the line
              count, then written as UTF-8 and the SHA-256 checked

Items are what jharokha_read format=items gives for the region (a Teams thread's
messages: sender, time, text); `fields = { who = "sender" }` renames their fields. A
marker Windows OCR drops at 1x (it does, on Cascadia rows) is looked for again in the
rows read up close, then by the second engine, once the screen holds still.

Conditions: text, regex, gone (in region), name (a name as a header shows it: cut short
with an ellipsis, icon glyphs beside it, a letter misread; core.actions.name_on), app,
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

import base64
import binascii
import difflib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import cache
from importlib import resources
from typing import Any, Protocol

from jharokha.core import glyphs, guard, listread, readers
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
from jharokha.core.models import Box, Line, inside
from jharokha.core.screen import ScreenError, Settled
from jharokha.core.text import find, fold

FORMAT = 1
NEWLINES = ("enter", "shift+enter", "forbid")
ARG_TYPES = ("text", "number", "list", "base64", "choice")
SHELLS = ("powershell", "cmd", "bash")
COND_KEYS = ("text", "regex", "gone", "name", "region", "app", "view", "ready", "before", "after")
COMMON = ("do", "expect", "unless", "timeout_s", "label")
INPUT = ("click", "type", "press", "scroll", "run", "put_file", "write_file")
# Refusals, where nothing more was sent: the input tools' codes (core.guard), and locked.
REFUSALS = ("wrong_app", "irreversible", "input_leased", "user_active", "locked")
PARAMS: dict[str, tuple[str, ...]] = {
    "focus": ("app", "view", "switch", "spot"),
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
    "read": ("region", "as", "into", "fields", "exclude", "since", "last", "zoom"),
    "extract": ("region", "regex", "items", "field", "pick", "into", "required", "zoom"),
    "repeat": ("times", "each", "as", "until", "max", "step"),
    "run": ("command", "shell", "region", "clear", "learn"),
    "put_file": ("path", "content_base64", "shell", "region"),
    "write_file": ("path", "text", "shell", "region"),
}
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
TIMEOUT_S = 5.0
POLL_S = 0.1
MAX_REPEAT = 50  # passes of a repeat, whatever times says
STILL_ENDS = 3  # scrolls in a row that change nothing: the end of a list
# Characters pasted as one line: Windows Terminal asks before pasting more than 5 KiB.
PASTE_MAX = 4000
# A line this short is typed: under 1.6 s at about 126 characters a second, what a paste
# takes to arrive and settle.
TYPE_MAX = 200
ARRIVE_S = 2.0  # for a sent line to show in the shell before Enter
BOTTOM_TICKS = 300  # wheel notches down that bring any view back to its newest rows
# Wheel notches up the scrollback a page: Windows scrolls 3 rows a notch by default, so a
# third of a 45-row screen, and pages overlap wherever the setting is up to 8.
PAGE_TICKS = 5
MAX_PAGES = 60
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
    if (
        kind in ("run", "put_file", "write_file")
        and params.get("shell", "powershell") not in SHELLS
    ):
        if not _PLACEHOLDER.fullmatch(str(params.get("shell"))):
            raise ActionError(f"{where}.shell must be one of {', '.join(SHELLS)}")
    if kind == "run" and params.get("learn") not in (None, "glyphs"):
        raise ActionError(f"{where}.learn must be 'glyphs'")
    for need in {
        "type": ("text",),
        "press": ("keys",),
        "click": ("target",),
        "run": () if params.get("learn") else ("command",),
        "put_file": ("path", "content_base64"),
        "write_file": ("path", "text"),
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
    _only(data, ("format", "action", "app", "session", "calibration"), source)
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
    def __init__(self, reason: str, candidates: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason, self.candidates = reason, candidates


class _Refuse(Exception):
    def __init__(self, kind: str, reason: str) -> None:
        super().__init__(reason)
        self.kind, self.reason = kind, reason


# Arguments and placeholders


def check_args(action: Action, args: Mapping[str, Any]) -> dict[str, Any]:
    """The arguments, validated and with defaults; ActionError naming what is wrong."""
    extra = sorted(set(args) - set(action.args))
    if extra:
        raise ActionError(f"{action.id} takes no argument {', '.join(extra)}")
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
        if a.type == "base64":
            try:
                base64.b64decode(str(v), validate=True)
            except (binascii.Error, ValueError) as e:
                raise ActionError(f"{name} must be base64: {e}") from e
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
    input tools apply them."""

    def __init__(
        self,
        ex: Executor,
        allow: str | None = None,
        policies: Mapping[str, Policy] | None = None,
    ) -> None:
        self.ex, self.allow = ex, allow
        self.policies = dict(policies or {})

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
        try:
            self._steps(action.steps, env, "")
        except _Fail as e:
            res.ok = False
            res.failed = {"step": self._at, "reason": e.reason}
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
                self._guard(self._look(fast=True))
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
                if _holds(cond, snap, self._regions(snap), self._info(snap))[0]:
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
            self.ex.click(*hit.center)
            self.ex.settle()
            snap = self._until({"app": app}, s.timeout_s, f"switching to {app}")
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
        hit = locate(loc, snap.lines, regions, spots, screen, info)
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
        last_seen: tuple[str, ...] | None = None
        n = still = 0
        why = f"{most} scrolls"
        for n in range(most + 1):
            snap = self._look()
            if p.get("collect"):
                its = self._items(snap, p)
                # Scrolling up (ticks > 0) reads further up: what is read now comes first.
                got = _merge(its, got) if ticks > 0 else _merge(got, its)
            if until and _holds(until, snap, self._regions(snap), self._info(snap))[0]:
                why = f"{_describe(until)} held"
                break
            # end marks the list's end positively (a chat's first line): reached, nothing
            # further can hold until.
            if p.get("end") and _holds(p["end"], snap, self._regions(snap), self._info(snap))[0]:
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
                    self._partial(got, p)
                    raise _Fail(f"the list stopped moving before {_describe(until)} held")
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
            # Doubling up to max_ticks: a start close to the newest end is found in a short
            # stride or two, one far back in a few long ones, and a stride never overshoots
            # by more than the way already come.
            step = max(-grow, min(grow, step * 2)) if grow else step
        if p.get("collect"):
            self._store(p["collect"], got, p)
            self.res.fields["stopped"] = why
        return f"{n} scrolls, stopped: {why}"

    def _partial(self, got: list[Any], p: dict[str, Any]) -> None:
        """What a collecting scroll read before it failed: a long read cut short still
        returns the part it has, with the failure saying where it stopped."""
        if p.get("collect"):
            self._store(p["collect"], got, p)

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

    def _items(self, snap: Snapshot, p: dict[str, Any]) -> list[Any]:
        """The region's items as its reader makes them (core.readers): what
        jharokha_read format=items gives for it."""
        box = self._box(snap, p["region"])
        info = self._info(snap).get(p["region"], RegionInfo())
        inner = list(info.inner)
        if p.get("exclude"):
            inner.append(self._box(snap, p["exclude"]))
        return readers.items(info.reader, self._pool(snap, p), box, info.kind, inner)

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
            its = self._items(snap, p)
            # A second look a moment later, as jharokha_read takes: an item read differently
            # then was caught while the list drew or scrolled.
            self.ex.wait(AGREE_S)
            again = self._look()
            seen = {readers.key(i) for i in self._items(again, p)}
            shaky = (
                set()
                if again.frame_id == snap.frame_id
                else {id(i) for i in its if readers.key(i) not in seen}
            )
            self._store(p["into"], its[-int(p["last"]) :] if p.get("last") else its, p, shaky)
            n = sum("unsteady" in d for d in self.res.items[p["into"]])
            return f"{len(self.res.items[p['into']])} items" + (f", {n} unsteady" if n else "")
        self.res.lines[p["into"]] = row_texts(self._pool(snap, p))
        return f"{len(self.res.lines[p['into']])} lines"

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
                if _holds(cond, snap, self._regions(snap), self._info(snap))[0]:
                    break
        return f"{n} times"

    def _shell_box(self, snap: Snapshot, p: dict[str, Any]) -> Box:
        region = p.get("region")
        regions = self._regions(snap)
        return regions[region] if region and region in regions else (0, 0, *snap.size)

    def _reply(
        self, p: dict[str, Any], token: str, timeout: float, partial: bool = True
    ) -> sh.Reply:
        """The reply to the line marked with token: the shell's rows as the executor reads
        them (a terminal's by its glyphs), polled until the end marker is on screen. The
        marker is what tells a finished command, never the prompt. At the timeout the view
        is scrolled to its bottom once and looked at again, since a view scrolled up does
        not follow new output; then, when partial, what the command printed so far goes
        into the result with the failure."""
        end = self.ex.now() + timeout
        bottomed = False
        while True:
            snap = self._look()
            box = self._shell_box(snap, p)
            if r := sh.read_reply(inside(snap.lines, box), token):
                return r
            if self.ex.now() >= end:
                if not bottomed:
                    bottomed = True
                    self._guard(snap)
                    self.ex.scroll(box[0] + box[2] // 2, box[1] + box[3] // 2, -BOTTOM_TICKS)
                    self.ex.settle(box)
                    continue
                if partial:
                    so_far, _ = sh.after_echo(inside(snap.lines, box), token)
                    self.res.output = unwrapped(so_far, box[0] + box[2])
                    self.res.fields["finished"] = False
                raise _Fail(
                    f"no end marker within {timeout:g} s: the command may still be running "
                    "(output holds what it printed so far: wait, read the screen, or press "
                    "ctrl+c; give the next one a longer timeout_s), or it went somewhere else"
                )
            self.ex.wait(POLL_S)

    def _send(self, line: str, token: str | None = None) -> None:
        """line into the shell, then Enter. A short line is typed (faster than a paste,
        which waits about a second for the session to fetch the clipboard); a long one is
        pasted, and typed when the paste never arrives. With the line's token, Enter waits
        until the line shows in the shell: a paste can come in empty, and an Enter alone
        would leave the run waiting for a marker that never comes."""
        snap = self._look(fast=True)
        self._guard(snap)
        self._keys(snap, ["enter"])
        paste = getattr(self.ex, "paste", None)
        how = "typed"
        if paste is not None and TYPE_MAX < len(line) <= PASTE_MAX:
            try:
                how = "pasted after its warning" if paste(line) else "pasted"
                self.ex.settle()  # all of it in before Enter
            except ScreenError as e:
                if e.code != "paste_failed":
                    raise
                self.ex.press("escape")  # whatever part of it came in
                self.ex.type(line)
                how = "typed: the paste did not arrive"
        else:
            self.ex.type(line)
        if token and not self._shows(token):
            self.ex.press("escape")
            self.ex.type(line)
            how += "; typed again: it did not show"
            if not self._shows(token):
                self.ex.press("escape")
                raise _Fail(
                    "the line did not show in the shell after it was sent twice (nothing, or "
                    "only part of it, arrived); Escape cleared it and nothing was run. Read "
                    "the screen: the shell may be busy, or not the one in front"
                )
        self.ex.press("enter")
        self.res.fields["input"] = how

    def _shows(self, token: str) -> bool:
        end = self.ex.now() + ARRIVE_S
        while True:
            if sh.echoed(self._look().lines, token):
                return True
            if self.ex.now() >= end:
                return False
            self.ex.wait(POLL_S)

    def _run(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        shell = p.get("shell", "powershell")
        if p.get("learn"):
            return self._learn(s, p, shell)
        command = str(p["command"])
        if "\n" in command or "\r" in command:
            raise _Fail("a command is one line; nothing was typed")
        known = getattr(self.ex, "glyphs", None)
        n = known() if known else None
        if n is not None and n < GLYPHS_MIN:
            # Until the font is learned, cells go to the engine, which garbles lookalikes
            # and takes seconds: the alphabet first, once, and the command after it.
            try:
                self._learn(s, p, shell)
            except _Fail as e:
                self.res.fields["warning"] = f"terminal.glyphs did not learn the font: {e}"
        code = self._command(s, p, shell, command)
        unknown = getattr(self.ex, "unknown_cells", None)
        if unknown and (k := unknown()) > UNKNOWN_WARN:
            self.res.fields["warning"] = (
                f"{k} cells matched no learned glyph and were read by OCR, which garbles "
                "terminal text: run terminal.glyphs (again after a zoom, font or window "
                "size change) and read the output again"
            )
        return f"exit {code}"

    def _learn(self, s: Step, p: dict[str, Any], shell: sh.Shell) -> str:
        code = self._command(s, p, shell, glyphs.COMMAND[shell])
        learner = getattr(self.ex, "learn_glyphs", None)
        n = learner() if learner else 0
        self.res.fields["glyphs"] = n
        if not n:
            raise _Fail("the alphabet lines were not found on screen; nothing was learned")
        return f"exit {code}, {n} glyphs learned"

    def _command(self, s: Step, p: dict[str, Any], shell: sh.Shell, command: str) -> int | None:
        token = sh.new_token()
        # Cleared by the line itself: the reply is then all the text above the marker.
        line = (sh.CLEAR[shell] if p.get("clear") else "") + sh.marked(command, token, shell)
        self._send(line, token)
        reply = self._reply(p, token, s.timeout_s)
        ok, code = sh.status(reply.payload, shell)
        if code is None and ok is not None:
            # $LASTEXITCODE is empty until a native program has run in the session: a
            # cmdlet's success is PowerShell's own exit code, 0, or 1 when it failed.
            code = 0 if ok else 1
        box = self._shell_box(self._look(fast=True), p)
        out = unwrapped(reply.output, box[0] + box[2])
        if not reply.echoed and _filled(reply, box):
            out = self._scrollback(p, box, token, out)
        self.res.output = out
        self.res.exit_code = code
        self.res.fields["ok"] = ok
        return code

    def _scrollback(self, p: dict[str, Any], box: Box, token: str, out: list[str]) -> list[str]:
        """out with the start of the output that scrolled off the top: the scrollback read
        going up a few rows at a time, until the command's own line shows or the view stops
        moving (its top, or what the command's cls left), then scrolled back down."""
        x, y, right = box[0] + box[2] // 2, box[1] + box[3] // 2, box[0] + box[2]
        last = unwrapped(inside(self._look().lines, box), right)
        up = 0
        try:
            for _ in range(MAX_PAGES):
                self._guard(self._look(fast=True))
                self.ex.scroll(x, y, PAGE_TICKS)
                up += PAGE_TICKS
                self.ex.settle(box)
                lines = inside(self._look().lines, box)
                screen = unwrapped(lines, right)
                if screen == last:
                    return out
                last = screen
                page, found = sh.after_echo(lines, token)
                out = _overlap(unwrapped(page, right), out)
                if found:
                    return out
            return ["…", *out]
        finally:
            if up:
                self.ex.scroll(x, y, -up)
                self.ex.settle(box)

    def _put_file(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        shell = p.get("shell", "powershell")
        data = base64.b64decode(str(p["content_base64"]).encode())
        plan = sh.put_file(str(p["path"]), data, shell)
        return self._put(s, p, shell, plan, "chunk")

    def _write_file(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        shell = p.get("shell", "powershell")
        try:
            plan = sh.write_text(str(p["path"]), str(p["text"]), shell)
        except ValueError as e:
            raise _Fail(f"{e}; nothing was typed") from None
        self.res.fields["lines"] = int(str(plan.steps[-2].expect).rpartition(":")[2])
        return self._put(s, p, shell, plan, "batch")

    def _put(self, s: Step, p: dict[str, Any], shell: sh.Shell, plan: sh.PutPlan, what: str) -> str:
        """plan's steps typed in order, each confirmed by what it prints (retyped up to
        three times: each is safe to repeat), then the last, which writes the file and
        prints its SHA-256, checked against the one sent."""
        for k, st in enumerate(plan.steps[:-1]):
            seen: list[str] = []
            for attempt in range(3):
                if attempt:
                    st = _retoken(st, shell)
                self._send(st.command, st.token)
                try:
                    # A chunk is stored at once: a reply not read by then is retyped.
                    reply = self._reply(p, st.token, min(s.timeout_s, 5.0), partial=False)
                except _Fail:
                    reply = None
                seen.append(repr(reply.payload) if reply else "no marker")
                if reply and st.expect and _confirmed(reply.payload, st.expect):
                    break
                if attempt == 2:
                    raise _Fail(
                        f"{what} {k} was not confirmed after 3 tries: expected {st.expect!r} "
                        f"after the marker, read {', '.join(seen)}"
                    )
        last = plan.steps[-1]
        self._send(last.command, last.token)
        reply = self._reply(p, last.token, s.timeout_s)
        ok, code = sh.status(reply.payload, shell)
        check = sh.check_hash(reply.output, plan.sha256)
        self.res.fields.update({"sha256": plan.sha256, "size": plan.size, "hash_check": check})
        self.res.exit_code = code
        if check == "mismatch" or ok is False:
            raise _Fail(f"the file's SHA-256 on the remote side is not {plan.sha256}")
        return f"{len(plan.steps) - 1} × {what}, hash {check}"

    # Conditions

    def _until(self, cond: dict[str, Any], timeout: float, what: str) -> Snapshot:
        end = self.ex.now() + timeout
        while True:
            snap = self._look()
            ok, why = _holds(cond, snap, self._regions(snap), self._info(snap))
            if ok:
                return snap
            if self.ex.now() >= end:
                raise _Fail(f"{what} {_describe(cond)} within {timeout:g} s: {why}")
            self.ex.wait(POLL_S)


def _retoken(step: sh.Step, shell: str) -> sh.Step:
    """step with a new token, for a retry: a reply read under the old one could be the
    failed attempt's."""
    new = sh.new_token()
    a, b, x, y = step.token[:2], step.token[2:], new[:2], new[2:]
    joins = {"powershell": ('"{}"+"{}', '"{}"+"{}'), "bash": ('"{}""{}', '"{}""{}')}
    old_s, new_s = joins.get(shell, ("{}^{}", "{}^{}"))
    command = step.command.replace(old_s.format(a, b), new_s.format(x, y))
    return sh.Step(command, new, step.expect) if command != step.command else step


def _confirmed(payload: str, expect: str) -> bool:
    """A chunk's `index:length`, also when OCR dropped the colon between them (a length
    never starts with 0, so the digits alone still tell them apart) or misread the index:
    the marker's token is this chunk's own, so the length it ends with is the one that
    says the chunk arrived whole."""
    if sh.confirms(payload, expect):
        return True
    digits = re.sub(r"\D", "", payload.translate(sh._DIGIT))
    if digits == expect.replace(":", ""):
        return True
    length = expect.rpartition(":")[2]
    got = re.split(f"[{sh._SEPS}]", payload.translate(sh._DIGIT))
    return ":" in expect and len(got) > 1 and got[-1].strip() == length


def _filled(reply: sh.Reply, box: Box) -> bool:
    """Whether the output reached both ends of the shell's box: its start may then have
    scrolled off the top."""
    tops = sorted({ln.box[1] for ln in reply.output})
    if len(tops) < 2:
        return False
    pitch = (tops[-1] - tops[0]) / (len(tops) - 1)  # rows apart, blank rows averaged in
    return tops[0] <= box[1] + pitch and reply.bottom >= box[1] + box[3] - 3 * pitch


def _overlap(above: list[str], below: list[str]) -> list[str]:
    """Rows read at two places in a scrollback as one run, joined where the end of above is
    the start of below. Either edge row may be a wrapped line read in part, so each may be
    left out of the match; where nothing matches, `…` marks rows that were not read."""
    for k in range(min(len(above), len(below)), 0, -1):
        if k < 2 and min(len(above), len(below)) > 1:
            break  # one row alike is chance: a blank-ish line, a repeated divider
        for da in (0, 1):
            for db in (0, 1):
                a = above[len(above) - da - k : len(above) - da]
                if len(a) == k and a == below[db : db + k]:
                    return above[: len(above) - da] + below[db + k :]
    return [*above, "…", *below]


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
        # Of typed text over several lines, a box that scrolls shows the last line at least.
        # An empty text (an optional argument left out) asks for nothing.
        last = [t for t in str(cond["text"]).splitlines() if t.strip()][-1:]
        if last and not find(pool, last[0]):
            return False, f"'{last[0]}' is not on screen"
    if "name" in cond and not name_on(pool, str(cond["name"])):
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
        its = readers.items(reg.reader, pool, regions[name], reg.kind, list(reg.inner))
        try:
            if not test(its, str(cond[key]), datetime.now()):
                return False, miss
        except ValueError as e:
            return False, str(e)
    return True, ""


_ELLIPSIS = re.compile(r"(?:\.{2,}|…)$")
NAME_RATIO = 0.85  # how alike a name and what a header shows of it must read


def name_on(lines: list[Line], want: str) -> bool:
    """Whether a row shows the name want as a header does: whole, or cut short at its end
    (`Ana Ald…`), with an avatar's initials or an icon OCR read as a stray glyph or two in
    front, and a character or two misread. An empty name is always shown."""
    w = _compact(want)
    if not w:
        return True
    for row in row_texts(lines):
        if w in _compact(row):
            return True
        tokens = row.split()
        for j, tok in enumerate(tokens):
            cut = bool(_ELLIPSIS.search(tok))
            if not cut and j < len(tokens) - 1:
                continue  # a cut name ends at its ellipsis, a whole one anywhere
            for i in range(j + 1):
                got = _compact(" ".join(tokens[i : j + 1]))
                if not got:
                    continue
                if difflib.SequenceMatcher(None, got, w, autojunk=False).ratio() >= NAME_RATIO:
                    return True
                if cut and len(got) >= max(4, len(w) // 2) and w.startswith(got):
                    return True
    return False


def _compact(s: str) -> str:
    return re.sub(r"[\W_]+", "", fold(_ELLIPSIS.sub("", s.strip()))).lower()


def _merge(older: list[Any], newer: list[Any]) -> list[Any]:
    """Items read at two scroll positions as one list, each once: older is the read
    further up. listread.merge for a list's items; for other readers, by their text."""
    if all(isinstance(i, listread.Item) for i in [*older, *newer]):
        return listread.merge(older, newer)
    seen = {fold(readers.item_text(i)).replace(" ", "") for i in older}
    return older + [i for i in newer if fold(readers.item_text(i)).replace(" ", "") not in seen]


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
        if s.kind == "put_file":
            p["content_base64"] = f"<{len(str(p.get('content_base64', '')))} characters>"
        if s.kind == "write_file":
            p["text"] = f"<{len(str(p.get('text', '')))} characters>"
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
