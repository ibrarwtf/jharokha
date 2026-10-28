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
viewer: a message on the own side is theirs whatever the screen shows.

A header names a chat the same way, and may cut a name in the middle and keep its tail
(`Ana Ald…, +2`): the part before the ellipsis starts the name, the part after it ends it.
OCR may lose that ellipsis but keeps the comma after it. Folding drops punctuation, so the
commas and brackets shown must be the name's: a list (`Ana Ald…, +2`) is not the person
(`Ana Alder (…`), nor the other way round."""

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
_GAP = re.compile(r"\s*(?:\.{2,}|…)\s*")
_COMMA = re.compile(r"\s*,")
_MARKS = re.compile(r"[^,()]")
_INITIALS = re.compile(r"^[A-Z]{1,2}\s+(?=\S)")
_MIN_CUT = 4  # folded characters a cut name must keep to match


def fold(s: str) -> str:
    return re.sub(r"[\W_]+", "", normalize(s).lower())


@dataclass(frozen=True)
class People:
    people: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    chats: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    chat_with: Mapping[str, str] = field(default_factory=dict)

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
        return self._chat(header)[1]

    def chat_in(self, text: str) -> str:
        """The id of the chat named somewhere in a header's text, among the icons, tabs and
        counts around it: of every run of its words that names one, a chat's own name
        first, then the most text matched, then the longest name."""
        words = text.split()
        best: tuple[tuple[int, int, int], str] = ((0, 0, 0), "")
        for a in range(len(words)):
            for b in range(a + 1, len(words) + 1):
                got = self._chat(" ".join(words[a:b]))
                if got[1] and got[0] > best[0]:
                    best = got
        return best[1]

    def _chat(self, shown: str) -> tuple[tuple[int, int, int], str]:
        """The best chat a text shows, with its rank (own name, text matched, name)."""
        best: tuple[tuple[int, int, int], str] = ((0, 0, 0), "")
        span = len(fold(shown))
        named = [(1, cid, names) for cid, names in self.chats.items()]
        named += [(0, self.chat_with.get(pid, pid), names) for pid, names in self.people.items()]
        for own, cid, names in named:
            for name in names:
                rank = (own, span, len(fold(name)))
                if rank > best[0] and _shows(shown, name):
                    best = (rank, cid)
        return best

    def _match(self, s: str, cut: bool) -> str:
        f = fold(s)
        if not f:
            return ""
        best, longest = "", 0
        for pid, names in self.people.items():
            for name in names:
                fn = fold(name)
                if fn == f:
                    return pid
                if cut and len(f) >= _MIN_CUT and fn.startswith(f) and len(fn) > longest:
                    best, longest = pid, len(fn)
        return best


def _shows(shown: str, name: str) -> bool:
    """Whether a text shows a name: the same folded, or cut in the middle (at an ellipsis,
    or before a comma when OCR lost the ellipsis) with the tail kept, and with the name's
    commas and brackets."""
    fn = fold(name)
    if not fn or not _same_marks(shown, name):
        return False
    if fold(shown) == fn:
        return True
    gap = _GAP.search(shown) or _COMMA.search(shown)
    if not gap:
        return False
    head, tail = fold(shown[: gap.start()]), fold(shown[gap.end() :])
    return (
        len(head) >= _MIN_CUT
        and len(head) + len(tail) <= len(fn)
        and fn.startswith(head)
        and fn.endswith(tail)
    )


def _same_marks(shown: str, name: str) -> bool:
    """The commas shown are the name's, and its brackets are among the name's, in order."""
    got, want = _MARKS.sub("", shown), _MARKS.sub("", name)
    rest = iter(want)
    return got.count(",") == want.count(",") and all(c in rest for c in got)


def parse(data: Mapping[str, Any], where: str = "people") -> People:
    """The `people` and `chats` tables of a TOML document as a People."""
    people: dict[str, tuple[str, ...]] = {}
    for pid, t in _tables(data.get("people", {}), f"{where}.people").items():
        people[pid] = _names(t.get("names", []), f"{where}.people.{pid}")
    chats: dict[str, tuple[str, ...]] = {}
    chat_with: dict[str, str] = {}
    for cid, t in _tables(data.get("chats", {}), f"{where}.chats").items():
        chats[cid] = _names(t.get("names", []), f"{where}.chats.{cid}")
        person = t.get("person")
        if person is not None:
            if person not in people:
                raise ValueError(f"{where}.chats.{cid}: person {person!r} is not in people")
            chat_with[person] = cid
    return People(people, chats, chat_with)


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
