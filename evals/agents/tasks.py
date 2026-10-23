"""The agent tasks, loaded from tasks.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TASKS = Path(__file__).with_name("tasks.toml")
# Side effects no task asks for: a message others read, text sent to a search engine.
ALWAYS_FORBIDDEN = ("sent", "web_search")
# The server's tools (jharokha.server), which forbid_tools and require_tools name.
TOOLS = (
    "jharokha_status",
    "jharokha_apps",
    "jharokha_read",
    "jharokha_find",
    "jharokha_wait",
    "jharokha_act",
    "jharokha_do",
    "jharokha_learn",
    "jharokha_screenshot",
)


@dataclass(frozen=True)
class Expect:
    exact: str | None = None
    contains: tuple[str, ...] = ()
    regex: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class Forbid:
    kind: str
    match: str = ""  # a regex on the event's detail; empty matches every event of the kind


@dataclass(frozen=True)
class StateCheck:
    path: tuple[str, ...]
    contains: tuple[str, ...] = ()
    equals: Any = None
    has_equals: bool = False


@dataclass(frozen=True)
class Task:
    name: str
    group: str
    prompt: str
    scenario: str
    seed: int = 100
    max_turns: int = 15
    expect: Expect = field(default_factory=Expect)
    forbid: tuple[Forbid, ...] = ()
    forbid_tools: tuple[str, ...] = ()
    require_tools: tuple[str, ...] = ()  # jharokha tools the run must have called
    require_actions: tuple[str, ...] = ()  # actions it must have run through jharokha_do
    state: tuple[StateCheck, ...] = ()

    @property
    def forbidden(self) -> tuple[Forbid, ...]:
        return tuple(Forbid(k) for k in ALWAYS_FORBIDDEN) + self.forbid


def parse(raw: dict[str, Any]) -> Task:
    e = raw.get("expect", {})
    return Task(
        name=raw["name"],
        group=raw.get("group", ""),
        prompt=raw["prompt"].strip(),
        scenario=raw["scenario"],
        seed=int(raw.get("seed", 100)),
        max_turns=int(raw.get("max_turns", 15)),
        expect=Expect(
            exact=e.get("exact"),
            contains=tuple(e.get("contains", ())),
            regex=tuple(e.get("regex", ())),
            none_of=tuple(e.get("none_of", ())),
        ),
        forbid=tuple(Forbid(f["kind"], f.get("match", "")) for f in raw.get("forbid", ())),
        forbid_tools=tuple(raw.get("forbid_tools", ())),
        require_tools=tuple(raw.get("require_tools", ())),
        require_actions=tuple(raw.get("require_actions", ())),
        state=tuple(
            StateCheck(
                tuple(s["path"]), tuple(s.get("contains", ())), s.get("equals"), "equals" in s
            )
            for s in raw.get("state", ())
        ),
    )


def load(path: Path = TASKS) -> list[Task]:
    tasks = [parse(t) for t in tomllib.loads(path.read_text(encoding="utf-8"))["task"]]
    names = [t.name for t in tasks]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"task names used twice: {sorted(dupes)}")
    check_tools(tasks)
    return tasks


def check_tools(tasks: list[Task]) -> None:
    """ValueError on a tool name the server does not have: a tool renamed away would make
    forbid_tools pass and require_tools fail without saying why."""
    for t in tasks:
        unknown = set(t.forbid_tools + t.require_tools) - set(TOOLS)
        if unknown:
            raise ValueError(f"{t.name}: no jharokha tool {sorted(unknown)}")
