"""Who is who: the display names an app shows for each person and chat, mapped to the short
ids an agent asks by. The map is local data (`[people]` and `[chats]` in the git-ignored
apps.toml, or a TOML file of its own): nothing here knows a name.

    [people.ana]
    names = ["Ana Alder", "Ana Alder (Guest)", "Ana"]

    [people.me]
    names = ["Your Name", "You"]

    [chats.team]
    names = ["Ana Alder, +2"]
    members = ["ana", "me"]

    [chats.ana]
    person = "ana"

A sender on screen matches a person when, folded (case, accents, punctuation and spaces
dropped), it equals one of the names, or is a name cut short by the screen (`Ana Ald...`),
or an avatar's initials glued in front of one (`AA Ana Alder`). The person `me` is the
viewer: a message on the own side is theirs whatever the screen shows."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jharokha.core.text import normalize

ME = "me"
_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)\s*$")
_INITIALS = re.compile(r"^[A-Z]{1,2}\s+(?=\S)")
_MIN_CUT = 4  # folded characters a cut name must keep to match


def fold(s: str) -> str:
    return re.sub(r"[\W_]+", "", normalize(s).lower())


@dataclass(frozen=True)
class People:
    people: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    chats: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def who(self, sender: str, own: bool = False) -> str:
        """The id of the person a sender on screen is: own messages are `me`'s; a
        sender no person is known by is "" (an agent sees the screen's text beside it)."""
        if own or fold(sender) in ("me", "you"):
            return ME
        s = _ELLIPSIS.sub("", sender.strip())
        cut = s != sender.strip()
        for cand in (s, _INITIALS.sub("", s)):
            if got := self._match(cand, cut):
                return got
        return ""

    def chat(self, header: str) -> str:
        """The id of the chat whose name a header shows: a chat's own names first, then a
        person's (a 1:1 chat is named after them), cut names allowed."""
        s = _ELLIPSIS.sub("", header.strip())
        cut = s != header.strip()
        if got := self._match(s, cut, self.chats):
            return got
        return self._match(s, cut)

    def _match(self, s: str, cut: bool, among: Mapping[str, tuple[str, ...]] | None = None) -> str:
        f = fold(s)
        if not f:
            return ""
        table = self.people if among is None else among
        best, longest = "", 0
        for pid, names in table.items():
            for name in names:
                fn = fold(name)
                if fn == f:
                    return pid
                if cut and len(f) >= _MIN_CUT and fn.startswith(f) and len(fn) > longest:
                    best, longest = pid, len(fn)
        return best


def parse(data: Mapping[str, Any], where: str = "people") -> People:
    """The `people` and `chats` tables of a TOML document as a People."""
    people: dict[str, tuple[str, ...]] = {}
    for pid, t in _tables(data.get("people", {}), f"{where}.people").items():
        people[pid] = _names(t.get("names", []), f"{where}.people.{pid}")
    chats: dict[str, tuple[str, ...]] = {}
    for cid, t in _tables(data.get("chats", {}), f"{where}.chats").items():
        names = list(_names(t.get("names", []), f"{where}.chats.{cid}"))
        person = t.get("person")
        if person is not None:
            if person not in people:
                raise ValueError(f"{where}.chats.{cid}: person {person!r} is not in people")
            names += people[person]
        chats[cid] = tuple(names)
    return People(people, chats)


def load(path: Path) -> People:
    return parse(tomllib.loads(path.read_text("utf-8")), str(path))


def _tables(v: Any, where: str) -> dict[str, dict[str, Any]]:
    if not isinstance(v, dict) or not all(isinstance(t, dict) for t in v.values()):
        raise ValueError(f"{where}: expected tables")
    return v


def _names(v: Any, where: str) -> tuple[str, ...]:
    if not isinstance(v, list) or not all(isinstance(s, str) and s.strip() for s in v):
        raise ValueError(f"{where}.names: expected a list of names")
    return tuple(s.strip() for s in v)
