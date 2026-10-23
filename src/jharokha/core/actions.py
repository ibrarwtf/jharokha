"""Actions as data. An action is a named sequence of steps an agent calls with arguments and
gets a structured result from; anyone can add one in a TOML file. Built-in ones ship in
jharokha/apps/actions/.

    format = 1
    [policy.teams]
    newline = "shift+enter"               # what a newline in typed text becomes in this app

    [action.teams.draft]                  # the action teams.draft; its app is teams
    summary = "Type a draft into a chat's compose box; never sends."
    max_risk = "reversible"               # a step declared riskier is refused before anything
    args.chat = { type = "text" }
    args.text = { type = "text", max_len = 2000 }

    [[action.teams.draft.step]]
    do = "click"
    target = { text = "{chat}", region = "list" }
    risk = "reversible"
    expect = { text = "{chat}", region = "header" }

Steps (`do`), each with `risk` (read, reversible, irreversible; required on click, type,
press, run and put_file), `expect` (a condition that must hold after it) and `timeout_s`:

    focus     app, view, switch (by the session spot `taskbar.<app>`), else check it is in front
    click     target (a locator, core.locate), button, double
    type      text, newline (enter | shift+enter | forbid), target to click first
    press     keys
    scroll    region, ticks, until (a condition), max; collect items into a result list
    wait      for = ready | settled, or until (a condition)
    read      region as items or lines into a result list, since a time; zoom reads it close
    extract   regex on a region into result fields, or a field of collected items
    repeat    times | each (a list argument) | until, max; steps inside
    run       command in the shell in front, ended by core.shell's marker; exit code;
              clear (Ctrl+L) first
    put_file  path, content_base64; per-chunk confirmation, then the SHA-256 checked

A marker Windows OCR drops at 1x (it does, on Cascadia rows) is looked for again in the
rows read up close, then by the second engine, once the screen holds still.

Conditions: text, regex, gone (in region), app, view, ready, before (a time on screen at or
before this one). Arguments fill `{name}` in step values; in a regex they are escaped. Only
arguments and repeat variables fill placeholders, never text read off the screen: what is
on screen goes into the result, and never becomes a step, a click target or typed text.

Before every input step the runner checks the app in front is the action's, and refuses
otherwise; typed text never carries a raw newline, so Enter is only ever a declared key."""

from __future__ import annotations

import base64
import binascii
import difflib
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from importlib import resources
from typing import Any, Literal, Protocol

from jharokha.core import shell as sh
from jharokha.core.apps import GENERIC
from jharokha.core.locate import (
    Hit,
    Item,
    Locator,
    Miss,
    inside,
    items,
    locate,
    locator,
    row_texts,
    rows,
)
from jharokha.core.models import Box, Line, Point, Word
from jharokha.core.text import find, fold

FORMAT = 1
RISKS = ("read", "reversible", "irreversible")
Risk = Literal["read", "reversible", "irreversible"]
NEWLINES = ("enter", "shift+enter", "forbid")
ARG_TYPES = ("text", "number", "list", "base64", "choice")
SHELLS = ("powershell", "cmd", "bash")
COND_KEYS = ("text", "regex", "gone", "region", "app", "view", "ready", "before")
COMMON = ("do", "risk", "expect", "timeout_s", "label")
INPUT = ("click", "type", "press", "scroll", "run", "put_file")
DECLARED = ("click", "type", "press", "run", "put_file")  # risk must be stated
PARAMS: dict[str, tuple[str, ...]] = {
    "focus": ("app", "view", "switch", "spot"),
    "click": ("target", "button", "double"),
    "type": ("text", "newline", "target"),
    "press": ("keys",),
    "scroll": ("region", "ticks", "until", "max", "collect", "item", "fields", "exclude", "since"),
    "wait": ("for", "until"),
    "read": ("region", "as", "into", "item", "fields", "exclude", "since", "zoom"),
    "extract": ("region", "regex", "items", "field", "pick", "into", "required", "zoom"),
    "repeat": ("times", "each", "as", "until", "max", "step"),
    "run": ("command", "shell", "region", "clear"),
    "put_file": ("path", "content_base64", "shell", "region"),
}
_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)\}")
TIMEOUT_S = 5.0
POLL_S = 0.1
MAX_REPEAT = 50  # passes of a repeat, whatever times says
# A band of terminal rows read up close: a quarter of the screen's area or less is what a
# Source upscales, and Windows OCR drops short tokens on Cascadia rows at 1x.
ZOOM_AREA = 0.24


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
    doc: str = ""


@dataclass(frozen=True)
class Step:
    kind: str
    params: dict[str, Any]
    risk: Risk
    expect: dict[str, Any] | None = None
    timeout_s: float = TIMEOUT_S
    label: str = ""
    steps: tuple[Step, ...] = ()  # a repeat's


@dataclass(frozen=True)
class Action:
    id: str
    app: str
    summary: str
    args: dict[str, Arg]
    steps: tuple[Step, ...]
    max_risk: Risk = "irreversible"
    newline: str = "forbid"
    source: str = "built-in"

    @property
    def risk(self) -> Risk:
        """The riskiest step it declares."""
        return _max(s.risk for s in _walk(self.steps))


def _walk(steps: tuple[Step, ...]) -> list[Step]:
    return [x for s in steps for x in (s, *_walk(s.steps))]


def _max(risks: Any) -> Risk:
    return max(risks, key=RISKS.index, default="read")


def _only(t: Mapping[str, Any], allowed: tuple[str, ...], where: str) -> None:
    extra = sorted(set(t) - set(allowed))
    if extra:
        raise ActionError(f"{where}: unknown key {', '.join(extra)}")


def _risk(v: Any, where: str) -> Risk:
    if v not in RISKS:
        raise ActionError(f"{where}.risk must be one of {', '.join(RISKS)}")
    return v


def _arg(name: str, t: Any, where: str) -> Arg:
    if not isinstance(t, dict):
        raise ActionError(f'{where} must be a table like {{ type = "text" }}')
    _only(t, ("type", "required", "default", "pattern", "choices", "max_len", "doc"), where)
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
    if kind in DECLARED and "risk" not in t:
        raise ActionError(f"{where}: a {kind} step declares its risk")
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
    for key in ("until",):
        if key in params:
            _check_condition(params[key], f"{where}.{key}")
    if kind == "type" and params.get("newline", "forbid") not in NEWLINES:
        raise ActionError(f"{where}.newline must be one of {', '.join(NEWLINES)}")
    if kind in ("run", "put_file") and params.get("shell", "powershell") not in SHELLS:
        if not _PLACEHOLDER.fullmatch(str(params.get("shell"))):
            raise ActionError(f"{where}.shell must be one of {', '.join(SHELLS)}")
    for need in {
        "type": ("text",),
        "press": ("keys",),
        "click": ("target",),
        "run": ("command",),
        "put_file": ("path", "content_base64"),
        "read": ("region", "into"),
        "scroll": ("region",),
    }.get(kind, ()):
        if need not in params:
            raise ActionError(f"{where}: a {kind} step needs {need}")
    expect = _check_condition(t["expect"], f"{where}.expect") if "expect" in t else None
    for name in _placeholders([params, expect]):
        if name not in names:
            raise ActionError(f"{where}: '{{{name}}}' is not an argument of this action")
    risk = _risk(t["risk"], where) if "risk" in t else None
    if risk is None:
        if kind == "repeat":
            risk = _max(s.risk for s in steps)
        elif kind == "focus" and t.get("switch"):
            risk = "reversible"
        elif kind == "scroll":
            risk = "reversible"
        else:
            risk = "read"
    elif kind == "repeat" and RISKS.index(risk) < RISKS.index(_max(s.risk for s in steps)):
        raise ActionError(f"{where}: repeat risk is below a step inside it")
    timeout = t.get("timeout_s", TIMEOUT_S)
    if not isinstance(timeout, int | float) or timeout <= 0:
        raise ActionError(f"{where}.timeout_s must be a number of seconds")
    return Step(kind, params, risk, expect, float(timeout), str(t.get("label", "")), steps)


def _placeholders(v: Any) -> set[str]:
    if isinstance(v, str):
        return set(_PLACEHOLDER.findall(v))
    if isinstance(v, dict):
        return {n for x in v.values() for n in _placeholders(x)}
    if isinstance(v, list | tuple):
        return {n for x in v for n in _placeholders(x)}
    return set()


def parse(text: str, source: str = "actions", origin: str = "local") -> dict[str, Action]:
    """The actions in one file, by id (`teams.draft`)."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ActionError(f"{source}: {e}") from e
    if data.get("format") != FORMAT:
        raise ActionError(f"{source}: needs `format = {FORMAT}` at the top")
    _only(data, ("format", "policy", "action"), source)
    policy = data.get("policy", {})
    for app, p in policy.items():
        _only(p, ("newline",), f"{source}: policy.{app}")
        if p.get("newline", "forbid") not in NEWLINES:
            raise ActionError(f"{source}: policy.{app}.newline must be one of {NEWLINES}")
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
            _only(t, ("summary", "max_risk", "args", "step", "newline"), where)
            args = {k: _arg(k, a, f"{where}.args.{k}") for k, a in t.get("args", {}).items()}
            steps = t.get("step", [])
            if not isinstance(steps, list) or not steps:
                raise ActionError(f"{where}: needs [[{where.split(': ')[1]}.step]]")
            parsed = tuple(_step(s, f"{where}.step[{i}]", set(args)) for i, s in enumerate(steps))
            newline = t.get("newline", policy.get(app, {}).get("newline", "forbid"))
            if newline not in NEWLINES:
                raise ActionError(f"{where}.newline must be one of {', '.join(NEWLINES)}")
            out[f"{app}.{name}"] = Action(
                f"{app}.{name}",
                app,
                str(t.get("summary", "")),
                args,
                parsed,
                _risk(t.get("max_risk", "irreversible"), f"{where}.max_risk"),
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
    """One reading of the screen: its lines, what app and view it is, where that view's
    named regions (`teams.list`) and the session's (`session.taskbar`) are, and the spots."""

    frame_id: int
    size: tuple[int, int]
    lines: list[Line]
    app: str | None
    view: str | None
    ready: bool | None = None
    regions: dict[str, Box] = field(default_factory=dict)
    spots: dict[str, Point] = field(default_factory=dict)
    locked: bool = False


class Executor(Protocol):
    """Input and reading, injected: the server's Source in a session, a fake in tests."""

    def read(self) -> Snapshot: ...
    def zoom(self, box: Box) -> list[Line]: ...  # the box read up close, client coordinates
    def reread(self, boxes: list[Box]) -> list[str]: ...  # by the second engine; [] if none
    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None: ...
    def type(self, text: str) -> None: ...  # never holds a newline
    def press(self, keys: str) -> None: ...
    def scroll(self, x: int, y: int, ticks: int) -> None: ...
    def settle(self) -> None: ...  # until the screen holds still after input
    def wait(self, seconds: float) -> None: ...
    def now(self) -> float: ...


# Results


@dataclass
class StepReport:
    label: str
    kind: str
    risk: str
    ms: int
    note: str = ""


@dataclass
class Result:
    """What an action did. fields, items, lines and output hold untrusted screen text."""

    action: str
    ok: bool = True
    fields: dict[str, Any] = field(default_factory=dict)
    items: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    lines: dict[str, list[str]] = field(default_factory=dict)
    output: list[str] | None = None
    exit_code: int | None = None
    steps: list[StepReport] = field(default_factory=list)
    failed: dict[str, Any] | None = None  # step, reason, candidates (untrusted)
    safety: list[dict[str, str]] = field(default_factory=list)  # kind, step, detail
    plan: list[dict[str, Any]] | None = None
    dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"action": self.action, "ok": self.ok}
        for k in ("fields", "items", "lines", "output", "exit_code", "failed", "safety", "plan"):
            v = getattr(self, k)
            if v not in (None, {}, []):
                out[k] = v
        if self.dry_run:
            out["dry_run"] = True
        out["steps_done"] = [
            {"step": s.label, "do": s.kind, "risk": s.risk, "ms": s.ms}
            | ({"note": s.note} if s.note else {})
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


# Times on screen, for since and before


_DATE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DATE_US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_TIME = re.compile(r"\b(\d{1,2}) ?[:.] ?(\d{2})(?:\s*([AaPp])\.?[Mm]\.?)?(?!\d)")


def when(s: str) -> tuple[int | None, int | None] | None:
    """The first date (as an ordinal) and time of day (minutes) in s, or None."""
    d = None
    if m := _DATE_ISO.search(s):
        d = _ordinal(int(m[1]), int(m[2]), int(m[3]))
    elif m := _DATE_US.search(s):
        d = _ordinal(int(m[3]), int(m[1]), int(m[2]))
    t = None
    if m := _TIME.search(s):
        h, mi = int(m[1]), int(m[2])
        if m[3]:
            h = h % 12 + (12 if m[3].lower() == "p" else 0)
        if h < 24 and mi < 60:
            t = h * 60 + mi
    return None if d is None and t is None else (d, t)


def _ordinal(y: int, mo: int, d: int) -> int | None:
    try:
        return date(y, mo, d).toordinal()
    except ValueError:
        return None


def at_or_before(a: tuple[int | None, int | None], b: tuple[int | None, int | None]) -> bool:
    """a is not after b, on what both say: dates when both have one, then times."""
    (da, ta), (db, tb) = a, b
    if da is not None and db is not None and da != db:
        return da < db
    if ta is not None and tb is not None:
        return ta <= tb
    return da is not None and db is not None


# The runner


class Runner:
    """Runs one action against an executor. allow caps the risk of any step, below the
    action's own max_risk: 0.19's session guard passes its limit here."""

    def __init__(self, ex: Executor, allow: Risk = "irreversible") -> None:
        self.ex, self.allow = ex, allow

    def run(self, action: Action, args: Mapping[str, Any], dry_run: bool = False) -> Result:
        res = Result(action.id, dry_run=dry_run)
        try:
            env = check_args(action, args)
        except ActionError as e:
            res.ok, res.failed = False, {"step": None, "reason": str(e)}
            return res
        cap = min(action.max_risk, self.allow, key=RISKS.index)
        for label, s, risk in _risks(action, action.steps, env, ""):
            if RISKS.index(risk) > RISKS.index(cap):
                detail = (
                    f"step {label} ({s.kind}) is {risk}, above what {action.id} may do here "
                    f"({cap}); nothing was done"
                )
                res.ok = False
                res.failed = {"step": label, "reason": detail}
                res.safety.append({"kind": "refused", "step": label, "detail": detail})
                return res
        if dry_run:
            res.plan = _plan(action, action.steps, env, "")
            return res
        self.action, self.res = action, res
        self._last: str | None = None  # the last app recognised during this run
        self._close: dict[tuple[int, str], list[Line]] = {}  # rows read again, per frame
        try:
            self._steps(action.steps, env, "")
        except _Fail as e:
            res.ok = False
            res.failed = {"step": self._at, "reason": e.reason}
            if e.candidates:
                res.failed["candidates"] = list(e.candidates)
        except _Refuse as e:
            res.ok = False
            res.failed = {"step": self._at, "reason": e.reason}
            res.safety.append({"kind": e.kind, "step": self._at, "detail": e.reason})
        return res

    # Steps

    def _steps(self, steps: tuple[Step, ...], env: dict[str, Any], prefix: str) -> None:
        for i, s in enumerate(steps):
            label = f"{prefix}{i + 1}" + (f" {s.label}" if s.label else "")
            self._at = f"{label}:{s.kind}"
            t0 = self.ex.now()
            note = getattr(self, f"_{s.kind}")(s, fill(s.params, env), env, f"{prefix}{i + 1}.")
            if s.expect:
                self._until(fill(s.expect, env), s.timeout_s, "expected")
            ms = int((self.ex.now() - t0) * 1000)
            self.res.steps.append(StepReport(f"{prefix}{i + 1}", s.kind, s.risk, ms, note or ""))

    def _focus(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        app, view = p.get("app", self.action.app), p.get("view")
        snap = self._look()
        if snap.locked:
            raise _Refuse("refused", "the session is locked; nothing was done")
        switched = snap.app != app
        if switched:
            if not p.get("switch"):
                raise _Refuse(
                    "refused",
                    f"{app} is not in front ({_on(snap)}); {self.action.id} works only in "
                    f"{app} and does not switch to it, so nothing was typed or clicked",
                )
            spot = p.get("spot", f"taskbar.{app}")
            hit = locate(Locator(spot=spot), [], {}, snap.spots, (0, 0, *snap.size))
            if isinstance(hit, Miss):
                raise _Fail(f"cannot switch to {app}: {hit.reason}")
            self.ex.click(*hit.center)
            self.ex.settle()
            snap = self._until({"app": app}, s.timeout_s, f"switching to {app}")
        if view and snap.view != view:
            raise _Fail(f"{app} shows its {snap.view} view, not {view}")
        if snap.ready is False:
            self._until({"ready": True}, s.timeout_s, f"{app} {snap.view} to load")
        return f"switched to {app}" if switched else f"{app} in front"

    def _look(self) -> Snapshot:
        snap: Snapshot = self.ex.read()
        if snap.app not in (None, GENERIC):
            self._last = snap.app
        return snap

    def _guard(self, snap: Snapshot, app: str | None = None) -> None:
        """Input goes only to the action's app: in front, or unrecognised for a moment
        (typing over a browser's URL hides its strongest cue) while nothing else has been
        recognised since it was."""
        want = app or self.action.app
        if snap.locked:
            raise _Refuse("refused", "the session is locked; no input was sent")
        if snap.app != want and not (snap.app == GENERIC and self._last == want):
            raise _Refuse(
                "refused",
                f"{want} is not in front ({_on(snap)}); no input was sent to it",
            )

    def _target(self, snap: Snapshot, t: dict[str, Any]) -> Hit:
        loc = locator(t)
        hit = locate(loc, snap.lines, self._regions(snap), snap.spots, (0, 0, *snap.size))
        if isinstance(hit, Miss):
            raise _Fail(f"{loc.describe()}: {hit.reason}", hit.candidates)
        return hit

    def _regions(self, snap: Snapshot) -> dict[str, Box]:
        """Named regions, also by their short name for the action's own app."""
        out = dict(snap.regions)
        for k, b in snap.regions.items():
            app, _, short = k.partition(".")
            if app == self.action.app:
                out.setdefault(short, b)
        return out

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
        newline = s.params.get("newline", self.action.newline)
        snap = self._look()
        self._guard(snap)
        if "target" in p:
            hit = self._target(snap, p["target"])
            self.ex.click(*hit.center)
            self.ex.settle()
            self._guard(self._look())
        parts = text.split("\n")
        if len(parts) > 1 and newline == "forbid":
            raise _Refuse(
                "refused", "the text has a newline and this step may not type one; nothing typed"
            )
        for i, part in enumerate(parts):
            if part:
                self.ex.type(part)
            if i < len(parts) - 1:
                self.ex.press(newline)
        self.ex.settle()
        return f"{len(text)} characters" + (f", newlines as {newline}" if len(parts) > 1 else "")

    def _press(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        self._guard(self._look())
        self.ex.press(str(p["keys"]))
        self.ex.settle()
        return ""

    def _scroll(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        ticks, most = int(p.get("ticks", 3)), int(p.get("max", 10))
        until = p.get("until")
        got: list[Item] = []
        last_id, n = None, 0
        for n in range(most + 1):
            snap = self._look()
            if p.get("collect"):
                fresh = [i for i in self._items(snap, p) if not _seen(i, got)]
                got = fresh + got if ticks > 0 else got + fresh
            if until and _holds(until, snap, self._regions(snap))[0]:
                break
            if snap.frame_id == last_id:
                break  # the last scroll changed nothing: the end of the list
            if n == most:
                if until:
                    raise _Fail(f"scrolled {most} times and {_describe(until)} did not hold")
                break
            box = self._box(snap, p["region"])
            self._guard(snap)
            last_id = snap.frame_id
            self.ex.scroll(box[0] + box[2] // 2, box[1] + box[3] // 2, ticks)
            self.ex.settle()
        if p.get("collect"):
            self._store(p["collect"], got, p)
        return f"{n} scrolls"

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

    def _items(self, snap: Snapshot, p: dict[str, Any]) -> list[Item]:
        its = items(self._pool(snap, p), p.get("item"))
        names = p.get("fields")
        if names and not p.get("item"):
            its = [
                Item(
                    i.lines,
                    i.box,
                    {
                        k: "  ".join(ln.text for ln in r)
                        for k, r in zip(names, rows(list(i.lines)), strict=False)
                    },
                )
                for i in its
            ]
        return its

    def _store(self, into: str, its: list[Item], p: dict[str, Any]) -> None:
        since = when(str(p["since"])) if p.get("since") else None
        if p.get("since") and since is None:
            raise _Fail(f"since {p['since']!r} is not a date or time")
        out = []
        for i in its:
            if since is not None:
                w = when(i.fields.get("time", "") or i.text)
                if w is None or at_or_before(w, since):
                    continue
            body = row_texts(list(i.lines))
            if p.get("item"):
                body = body[1:]  # the first row is the entry's head, in fields
            out.append({**i.fields, "text": "\n".join(body)})
        self.res.items[into] = out

    def _wait(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        what = p.get("for")
        if what == "settled":
            self.ex.settle()
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
            self._store(p["into"], its, p)
            return f"{len(self.res.items[p['into']])} items"
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
                if _holds(fill(s.params["until"], inner), snap, self._regions(snap))[0]:
                    break
        return f"{n} times"

    def _shell_box(self, snap: Snapshot, p: dict[str, Any]) -> Box:
        region = p.get("region")
        regions = self._regions(snap)
        return regions[region] if region and region in regions else (0, 0, *snap.size)

    def _reread_rows(self, snap: Snapshot, box: Box) -> list[Line]:
        """The shell's rows read by the second engine, each as a full-width strip, so text
        Windows OCR dropped from a row is read too; once per frame."""
        key = (snap.frame_id, "reread")
        if key not in self._close:
            strips = []
            for r in rows(inside(snap.lines, box)):
                top = min(ln.box[1] for ln in r) - 3
                bottom = max(ln.box[1] + ln.box[3] for ln in r) + 3
                strips.append((box[0], max(box[1], top), box[2], bottom - max(box[1], top)))
            texts = self.ex.reread(strips) if strips else []
            self._close[key] = [
                Line(t, b, (Word(t, b),)) for t, b in zip(texts, strips, strict=False) if t
            ]
        return self._close[key]

    def _zoom_rows(self, snap: Snapshot, box: Box) -> list[Line]:
        """The shell's rows read up close, in bands cut between rows, once per frame."""
        key = (snap.frame_id, "zoom")
        if key in self._close:
            return self._close[key]
        rs = rows(inside(snap.lines, box))
        most = max(1, int(ZOOM_AREA * snap.size[0] * snap.size[1] / max(1, box[2])))
        bands: list[tuple[int, int]] = []
        for r in rs:
            top = min(ln.box[1] for ln in r) - 3
            bottom = max(ln.box[1] + ln.box[3] for ln in r) + 3
            if bands and bottom - bands[-1][0] <= most:
                bands[-1] = (bands[-1][0], bottom)
            else:
                bands.append((top, bottom))
        out: list[Line] = []
        for i, (top, bottom) in enumerate(bands):
            # Cut halfway to the next band, so no row is read twice.
            nxt = bands[i + 1][0] if i + 1 < len(bands) else bottom
            prev = bands[i - 1][1] if i else top
            y0, y1 = max(box[1], (top + prev) // 2), min(box[1] + box[3], (bottom + nxt) // 2)
            if y1 > y0:
                out += self.ex.zoom((box[0], y0, box[2], y1 - y0))
        self._close[key] = out
        return out

    def _reply(self, p: dict[str, Any], token: str, timeout: float) -> sh.Reply:
        """The reply to the line marked with token. Read at 1x first; once the screen holds
        still without it (or without the command's echo), read the rows up close."""
        end = self.ex.now() + timeout
        last = None
        while True:
            snap = self._look()
            box = self._shell_box(snap, p)
            r = sh.read_reply(inside(snap.lines, box), token)
            if r and r.echoed:
                return r
            if snap.frame_id == last:
                close = sh.read_reply(self._zoom_rows(snap, box), token)
                if close and (close.echoed or r is None):
                    return close
                if r or close:
                    return r or close  # type: ignore[return-value]
                # The second engine finds a marker Windows OCR dropped at both scales, but
                # reads prose worse: its rows place the reply, Windows OCR's give the text.
                far = sh.read_reply(self._reread_rows(snap, box), token)
                if far:
                    text = _band(inside(snap.lines, box), far.output)
                    return sh.Reply(text, far.payload, far.echoed)
            last = snap.frame_id
            if self.ex.now() >= end:
                raise _Fail(
                    f"no end marker within {timeout:g} s: the command may still be running "
                    "(press ctrl+c), or it went somewhere else"
                )
            self.ex.wait(POLL_S * 3)

    def _send(self, line: str) -> None:
        self._guard(self._look())
        self.ex.type(line)
        self.ex.press("enter")

    def _run(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        shell = p.get("shell", "powershell")
        command = str(p["command"])
        if "\n" in command or "\r" in command:
            raise _Refuse("refused", "a command is one line; nothing was typed")
        if p.get("clear"):
            # Then a reply whose echo OCR garbled starts at the top, not at older output.
            self._guard(self._look())
            self.ex.press("ctrl+l")
            self.ex.settle()
        token = sh.new_token()
        line = sh.marked(command, token, shell)
        self._send(line)
        reply = self._reply(p, token, s.timeout_s)
        ok, code = sh.status(reply.payload, shell)
        out = row_texts(reply.output)
        if not reply.echoed:
            out = _drop_echo(out, line)
            if not p.get("clear"):
                self.res.fields["start_missing"] = True
        self.res.output = out
        self.res.exit_code = code
        self.res.fields["ok"] = ok
        return f"exit {code}"

    def _put_file(self, s: Step, p: dict[str, Any], env: dict[str, Any], _: str) -> str:
        shell = p.get("shell", "powershell")
        data = base64.b64decode(str(p["content_base64"]).encode())
        plan = sh.put_file(str(p["path"]), data, shell)
        for k, st in enumerate(plan.steps[:-1]):
            for attempt in range(3):
                if attempt:
                    st = _retoken(st, shell)
                self._send(st.command)
                try:
                    # A chunk is stored at once: a reply not read by then is retyped.
                    reply = self._reply(p, st.token, min(s.timeout_s, 5.0))
                except _Fail:
                    reply = None
                if reply and st.expect and _confirmed(reply.payload, st.expect):
                    break
                if attempt == 2:
                    raise _Fail(f"chunk {k} was not confirmed after 3 tries")
        last = plan.steps[-1]
        self._send(last.command)
        reply = self._reply(p, last.token, s.timeout_s)
        ok, code = sh.status(reply.payload, shell)
        check = sh.check_hash(reply.output, plan.sha256)
        if check == "mismatch":
            snap = self._look()
            check = sh.check_hash(self._zoom_rows(snap, self._shell_box(snap, p)), plan.sha256)
        self.res.fields.update({"sha256": plan.sha256, "size": plan.size, "hash_check": check})
        self.res.exit_code = code
        if check == "mismatch" or ok is False:
            raise _Fail(f"the file's SHA-256 on the remote side is not {plan.sha256}")
        return f"{len(plan.steps) - 1} chunks, hash {check}"

    # Conditions

    def _until(self, cond: dict[str, Any], timeout: float, what: str) -> Snapshot:
        end = self.ex.now() + timeout
        while True:
            snap = self._look()
            ok, why = _holds(cond, snap, self._regions(snap))
            if ok:
                return snap
            if self.ex.now() >= end:
                raise _Fail(f"{what} {_describe(cond)} within {timeout:g} s: {why}")
            self.ex.wait(POLL_S)


def _band(lines: list[Line], strips: list[Line]) -> list[Line]:
    """lines from the rows strips cover."""
    if not strips:
        return []
    top = min(s.box[1] for s in strips)
    bottom = max(s.box[1] + s.box[3] for s in strips)
    return [ln for ln in lines if top <= ln.box[1] + ln.box[3] / 2 < bottom]


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
    """A chunk's `index:length`, also when OCR dropped the colon between them: a length
    never starts with 0, so the digits alone still tell them apart."""
    if sh.confirms(payload, expect):
        return True
    digits = re.sub(r"\D", "", payload.translate(sh._DIGIT))
    return digits == expect.replace(":", "")


def _drop_echo(rows_: list[str], typed: str) -> list[str]:
    """rows without the leading ones that are the typed line's echo (prompt and command,
    wrapped), for a reply whose echo OCR garbled past recognising."""
    want = fold(typed).replace(" ", "")
    out = list(rows_)
    while out:
        row = fold(out[0]).replace(" ", "")
        sm = difflib.SequenceMatcher(None, row, want, autojunk=False)
        if not row or sum(b.size for b in sm.get_matching_blocks()) < 0.6 * len(row):
            break
        out.pop(0)
    return out


def _on(snap: Snapshot) -> str:
    if snap.locked:
        return "the session is locked"
    return f"{snap.app or 'nothing recognised'} {snap.view or ''} is in front".replace("  ", " ")


def _describe(cond: Mapping[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in cond.items())


def _holds(cond: Mapping[str, Any], snap: Snapshot, regions: Mapping[str, Box]) -> tuple[bool, str]:
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
        last = [t for t in str(cond["text"]).splitlines() if t.strip()][-1:] or [""]
        if not find(pool, last[0]):
            return False, f"'{last[0]}' is not on screen"
    if "regex" in cond and not find(pool, str(cond["regex"]), regex=True):
        return False, "the regex matches nothing on screen"
    if "gone" in cond and find(pool, str(cond["gone"])):
        return False, f"'{cond['gone']}' is still on screen"
    if "before" in cond:
        since = when(str(cond["before"]))
        if since is None:
            return False, f"before {cond['before']!r} is not a date or time"
        seen = [w for r in row_texts(pool) if (w := when(r))]
        if not any(at_or_before(w, since) for w in seen):
            return False, "nothing on screen is that old yet"
    return True, ""


def _seen(item: Item, got: list[Item]) -> bool:
    """item is one already collected, as OCR read it again on another screen."""
    a = fold(item.text).replace(" ", "")
    for g in got:
        if g.fields.get("time") != item.fields.get("time"):
            continue
        b = fold(g.text).replace(" ", "")
        if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.9:
            return True
    return False


def _effective(s: Step, p: dict[str, Any], action: Action) -> Risk:
    """A step's risk with what its filled values show: typed text with a newline that is
    Enter is irreversible, whatever was declared."""
    if s.kind == "type":
        text = str(p.get("text", ""))
        newline = s.params.get("newline", action.newline)
        if newline == "enter" and ("\n" in text or "\r" in text):
            return "irreversible"
    return s.risk


def _risks(
    action: Action, steps: tuple[Step, ...], env: Mapping[str, Any], prefix: str
) -> list[tuple[str, Step, Risk]]:
    out = []
    for i, s in enumerate(steps):
        label = f"{prefix}{i + 1}:{s.kind}"
        try:
            p = fill(s.params, env)
        except KeyError:
            p = s.params  # a repeat variable, filled per pass
        out.append((label, s, _effective(s, p, action)))
        out += _risks(action, s.steps, env, f"{prefix}{i + 1}.")
    return out


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
        row: dict[str, Any] = {"step": f"{prefix}{i + 1}", "do": s.kind, "risk": s.risk}
        row |= {k: v for k, v in p.items()}
        if s.expect:
            try:
                row["expect"] = fill(s.expect, env)
            except KeyError:
                row["expect"] = s.expect
        if s.steps:
            row["steps"] = _plan(action, s.steps, env, f"{prefix}{i + 1}.")
        out.append(row)
    return out


def describe(action: Action) -> dict[str, Any]:
    """What an `apps`/`do` tool lists for an action: its arguments and its riskiest step."""
    return {
        "action": action.id,
        "summary": action.summary,
        "risk": action.risk,
        "max_risk": action.max_risk,
        "args": {
            k: {"type": a.type, "required": a.required}
            | ({"doc": a.doc} if a.doc else {})
            | ({"choices": list(a.choices)} if a.choices else {})
            for k, a in action.args.items()
        },
    }
