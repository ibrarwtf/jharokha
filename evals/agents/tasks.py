"""The agent tasks, loaded from tasks.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TASKS = Path(__file__).with_name("tasks.toml")
# Side effects no task asks for: a message others read, text sent to a search engine.
ALWAYS_FORBIDDEN = ("sent", "web_search")


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
    return tasks
