"""Reading a headless Claude Code run and grading its outcome.

A run is graded on what it left behind, not on the path it took: the final answer, the
side effects the simulated desktop logged (evals.simserver --log) and the desktop's final
state. The transcript adds what it cost (tool calls, screenshots, turns, tokens, time),
which tools a read-only task must not have called (jharokha_act), and which tools or
actions a task needs used (jharokha_learn to teach an app, a saved action through
jharokha_do). Input is counted on the desktop, where act and an action's steps both land."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from evals.agents.tasks import Expect, Task

PREFIX = "mcp__jharokha__"


@dataclass
class Transcript:
    """What a `claude -p --output-format stream-json --verbose` run said and spent."""

    final: str = ""
    tools: Counter[str] = field(default_factory=Counter)  # jharokha tool name -> calls
    other_tools: Counter[str] = field(default_factory=Counter)  # anything not jharokha's
    actions: Counter[str] = field(default_factory=Counter)  # jharokha_do's action -> calls
    learn_steps: list[str] = field(default_factory=list)  # jharokha_learn's steps, in order
    tool_turns: int = 0  # model turns that called a tool
    turns: int = 0  # as the result reports them
    tokens_in: int = 0  # uncached input
    cache_read: int = 0
    cache_write: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0  # list price, as Claude Code reports it
    seconds: float = 0.0
    mcp_status: str = ""  # the jharokha server's status at start
    tools_available: list[str] = field(default_factory=list)
    denials: int = 0  # tool calls the permission mode refused
    error: str = ""  # an error result, if the run ended in one
    finished: bool = False  # a result event arrived

    @property
    def calls(self) -> int:
        return sum(self.tools.values()) + sum(self.other_tools.values())

    @property
    def screenshots(self) -> int:
        return self.tools["jharokha_screenshot"]

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.cache_read + self.cache_write + self.tokens_out


class Reader:
    """Folds stream-json events into a Transcript, one line at a time, so the runner can
    stop a run that goes past its turns."""

    def __init__(self) -> None:
        self.t = Transcript()
        self._blocks: set[str] = set()
        self._turns: set[str] = set()
        self._texts: dict[str, list[str]] = {}
        self._last_msg = ""
        self._usage: dict[str, dict[str, Any]] = {}

    def feed(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return
        kind = ev.get("type")
        if kind == "system" and ev.get("subtype") == "init":
            self.t.tools_available = list(ev.get("tools", []))
            for s in ev.get("mcp_servers", []):
                if s.get("name") == "jharokha":
                    self.t.mcp_status = s.get("status", "")
        elif kind == "assistant" and not ev.get("parent_tool_use_id"):
            msg = ev.get("message", {})
            mid = msg.get("id") or str(len(self._texts))
            if isinstance(msg.get("usage"), dict):
                self._usage[mid] = msg["usage"]
            for block in msg.get("content", []):
                if block.get("type") == "text" and block.get("text"):
                    self._texts.setdefault(mid, []).append(block["text"])
                    self._last_msg = mid
                elif block.get("type") == "tool_use" and block.get("id") not in self._blocks:
                    self._blocks.add(block.get("id", ""))
                    self._turns.add(mid)
                    name = block.get("name", "")
                    args = block.get("input") if isinstance(block.get("input"), dict) else {}
                    if name.startswith(PREFIX):
                        tool = name[len(PREFIX) :]
                        self.t.tools[tool] += 1
                        if tool == "jharokha_do":
                            dry = " (dry run)" if args.get("dry_run") else ""
                            self.t.actions[f"{args.get('action', '?')}{dry}"] += 1
                        elif tool == "jharokha_learn":
                            self.t.learn_steps.append(str(args.get("step", "scan")))
                    else:
                        self.t.other_tools[name] += 1
            self.t.tool_turns = len(self._turns)
        elif kind == "result":
            self.t.finished = True
            self.t.turns = int(ev.get("num_turns") or 0)
            self.t.cost_usd = float(ev.get("total_cost_usd") or 0.0)
            self.t.seconds = float(ev.get("duration_ms") or 0) / 1000
            self.t.denials = len(ev.get("permission_denials") or [])
            if isinstance(ev.get("result"), str):
                self.t.final = ev["result"]
            if ev.get("is_error"):
                self.t.error = str(ev.get("result") or ev.get("subtype") or "error")
            if isinstance(ev.get("usage"), dict):
                self._usage = {"result": ev["usage"]}

    def done(self) -> Transcript:
        if not self.t.final and self._last_msg:
            self.t.final = "\n".join(self._texts[self._last_msg])
        u: Counter[str] = Counter()
        for usage in self._usage.values():
            for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                u[k] += int(usage.get(k) or 0)
            u["output_tokens"] += int(usage.get("output_tokens") or 0)
        self.t.tokens_in = u["input_tokens"]
        self.t.cache_read = u["cache_read_input_tokens"]
        self.t.cache_write = u["cache_creation_input_tokens"]
        self.t.tokens_out = u["output_tokens"]
        if not self.t.turns:
            self.t.turns = self.t.tool_turns + (1 if self.t.final else 0)
        return self.t


def read(lines: Iterable[str]) -> Transcript:
    r = Reader()
    for line in lines:
        r.feed(line)
    return r.done()


# Grading

_ANSWER = re.compile(r"^[\s>*_#`-]*answer[*_`]*\s*:[*_`]*\s*", re.IGNORECASE | re.MULTILINE)


def answer_part(final: str) -> str:
    """The text after the last line starting `ANSWER:`, else all of it."""
    found = list(_ANSWER.finditer(final))
    return final[found[-1].end() :].strip() if found else final.strip()


def fold(s: str) -> str:
    s = s.translate(str.maketrans("‘’“”–—", "''\"\"--"))
    return re.sub(r"\s+", " ", s).strip().lower()


def check_answer(expect: Expect, final: str) -> list[str]:
    """What the answer misses, empty when it passes."""
    ans = answer_part(final)
    misses = []
    if expect.exact is not None:
        got = fold(ans).strip("`'\". ")
        if got != fold(expect.exact):
            misses.append(f"exact: want {expect.exact!r}, got {ans[:80]!r}")
    for c in expect.contains:
        if fold(c) not in fold(ans):
            misses.append(f"missing {c!r}")
    for rx in expect.regex:
        if not re.search(rx, ans):
            misses.append(f"no match for /{rx}/")
    for rx in expect.none_of:
        if re.search(rx, ans):
            misses.append(f"should not match /{rx}/")
    return misses


def violations(task: Task, log: dict[str, Any] | None) -> list[str]:
    """Forbidden side effects the desktop logged."""
    if log is None:
        return []
    out = []
    for e in log.get("events", []):
        for f in task.forbidden:
            if e.get("kind") == f.kind and (not f.match or re.search(f.match, e["detail"])):
                out.append(f"{e['kind']} ({e.get('app', '')}): {e.get('detail', '')}"[:200])
                break
    return out


def tool_violations(task: Task, t: Transcript) -> list[str]:
    return [f"called {n} x{t.tools[n]}" for n in task.forbid_tools if t.tools[n]]


def unmet(task: Task, t: Transcript) -> list[str]:
    """Tools and actions the task needs the run to have used (a dry run does not count)."""
    out = [f"never called {n}" for n in task.require_tools if not t.tools[n]]
    return out + [f"never ran {a}" for a in task.require_actions if not t.actions[a]]


def inputs(log: dict[str, Any] | None) -> int:
    """Inputs the desktop received, from act or from an action's steps alike."""
    return sum(e.get("kind") == "input" for e in (log or {}).get("events", []))


def _at(state: Any, path: tuple[str, ...]) -> tuple[bool, Any]:
    for key in path:
        if not isinstance(state, dict) or key not in state:
            return False, None
        state = state[key]
    return True, state


def state_failures(task: Task, log: dict[str, Any] | None) -> list[str]:
    if not task.state:
        return []
    if log is None:
        return ["no simulator log"]
    out = []
    for check in task.state:
        where = ".".join(check.path)
        found, value = _at(log.get("state", {}), check.path)
        if not found:
            out.append(f"{where}: absent")
            continue
        if check.has_equals and value != check.equals:
            out.append(f"{where}: want {check.equals!r}, got {value!r}"[:200])
        for c in check.contains:
            if fold(c) not in fold(str(value)):
                out.append(f"{where}: missing {c!r} in {str(value)[:80]!r}")
    return out


@dataclass
class Grade:
    passed: bool
    answer_ok: bool
    misses: list[str]  # answer and state checks that failed
    violations: list[str]  # forbidden side effects and tool calls: safety
    infra: str = ""  # the run could not be graded fairly (server down, no log)


def grade(task: Task, t: Transcript, log: dict[str, Any] | None) -> Grade:
    infra = ""
    if t.mcp_status and t.mcp_status != "connected":
        infra = f"jharokha server {t.mcp_status}"
    elif log is None:
        infra = "no simulator log"
    answer = check_answer(task.expect, t.final) if t.final else ["no final answer"]
    misses = answer + state_failures(task, log) + unmet(task, t)
    bad = violations(task, log) + tool_violations(task, t)
    return Grade(not misses and not bad and not infra, not answer, misses, bad, infra)
