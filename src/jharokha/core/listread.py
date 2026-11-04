"""Items in a list-like region: the messages of a chat thread, the entries of a chat list, the
mails of an inbox. OCR gives rows of text; an agent wants who said what, and when.

Grouping rules, all of them:
- Rows: lines whose centres lie inside the region, joined left to right when their vertical
  centres are within half a line height.
- Junk: a line of two characters or fewer is an icon (OCR reads them as `o`, `9`, `0)`),
  unless it is alone in its row, all digits, and starts where the row above starts.
- messages (a thread): a row starting past 15% of the region's width is your own side.
  `Name dd/mm hh:mm` at the left after a blank of most of a line (or first) starts someone
  else's message (yours when the name is You); a bare `dd/mm hh:mm` on your side starts one
  of yours (`1148` too, its colon lost, when it ends where your other stamps end). A
  name-and-time row indented 16 px past the message's start (or any on your side) opens a
  quote, which takes the rows at its indent until one starts left of it. Rows
  before any header are a message cut at the region's top. A row switching sides starts a
  new message, and so does a blank taller than a line between two rows of text: the next
  message from the same sender under the same header, without a time of its own. Unless the
  row starts two line pitches below the one above (up to two and a quarter): that blank is
  an empty line inside the message, a paragraph; the next bubble starts a little closer.
  Nor when the row is in or under an indented block of the message (a list, a code block),
  up to two and a quarter pitches below: indented past the row above by up to four line
  heights, or back at the message's text column under the block. A row the region's top
  cuts shows its lower part only; its top is taken a line height above its bottom.
- Bubbles (core.bubbles, from the pixels, when the reader has them) decide before the gaps
  and pitches: rows whose centres lie in one bubble are one message, on the side the bubble
  is on, and a row in another bubble starts the next. Rows inside a bubble that lies inside
  a larger one (a card, a quote, a picture in a message) stay with the outer one's message.
  A bordered (unfilled) bubble whose own rows are too few for its height is a picture: the
  rows inside it are what OCR made of the picture and are dropped. A bubble at least three
  lines tall holding no rows is an image-only message with empty text: the bare stamp or
  header just above gives it its time, and the next pictures stacked under that stamp are
  messages without a time of their own.
- An event row on the others' side, a stamp followed by text after a blank (`dd/mm hh:mm
  Meeting ended 16m`, `hh:mm Call ended`, an icon read as junk in front), is an item of its
  own from SYSTEM, at that stamp.
- A date alone on a row, centred (`Yesterday`, `Monday`, `21 March`), divides a thread
  by day: the messages after it whose time has no date get it in front (`Yesterday 14:05`).
  A read marker alone and centred (`Last read`, `New messages`) is no message.
- entries, mails (a list): a section header (`Today`, `v Chats`, ...) starts a section; the
  rows between are split at the larger of two kinds of step from one row's top to the next
  (inside an item, between items); with one kind only, each row is an item. Rows are title,
  subtitle, then text; a date or time at the right edge is the item's time.
- Names lose avatar initials glued in front (`AA Ana Alder`); a trailing `..`, `...` or `…`
  on a name, title or subtitle is cut off and the item marked truncated.

Numeric dates are day first (dd/mm), as most locales outside the US print them; month
names are read either way round (`21 March`, `Mar 21`)."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from jharokha.core.bubbles import Bubble
from jharokha.core.models import Box, Line, union
from jharokha.core.people import People
from jharokha.core.text import clean, normalize

KINDS = ("messages", "entries", "mails")

_DAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun"
_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec"
)
_YEAR = r"(?:,?\s+\d{4})?"
_DATE = (
    rf"\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?"
    rf"|\d{{1,2}}\s+(?:{_MONTHS})\b\.?{_YEAR}"
    rf"|(?:{_MONTHS})\.?\s+\d{{1,2}}\b{_YEAR}"
)
_CLOCK = r"\d{1,2}:\d{2}"
_DAYPART = rf"(?:(?:{_DAYS}),?\s+)?(?:{_DATE})|yesterday|today|(?:{_DAYS})\b"
# A message header always has a clock; a list may show only a date or a day.
_STAMP = rf"(?:(?:{_DAYPART})\s+)?{_CLOCK}"
_WHEN = rf"{_STAMP}|{_DAYPART}"
_IS_STAMP = re.compile(_STAMP, re.I)
_IS_WHEN = re.compile(_WHEN, re.I)
_IS_DAY = re.compile(_DAYPART, re.I)
_YOU = ("you", "me")
_CENTRED = 0.1  # of the region's width: how far off its middle a day divider may sit
_TAIL = re.compile(rf"\s(?P<when>{_WHEN})$", re.I)
_HEADER = re.compile(rf"^(?P<name>\D+?)\s+(?P<when>{_STAMP})$", re.I)
# Lazy junk: `Thu 19:48 Call ended` keeps its day rather than losing it as an icon.
# The junk in front is an icon read as one character (`o`), or as digits or symbols: a word
# of small letters is text (`at 14:30 works for me`).
_EVENT = re.compile(
    rf"^(?:(?:\S|(?-i:[^\sa-z]{{2,3}}))\s+)??(?P<when>{_STAMP})\s+(?P<text>[^\W\d_].*)$",
    re.I,
)
SYSTEM = "system"  # the sender of a thread's event rows: calls and meetings
_MARKER = re.compile(r"(?:last read|new messages?|unread messages?)", re.I)
_AVATAR = re.compile(r"^[A-Z]{2}\s+(?=[A-Z][a-z])")
# A quote's avatar reads as one letter in front of the name.
_BADGE = re.compile(r"^[A-Za-z0-9]\s+(?=[A-Z])")
_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)$")
_EDITED = re.compile(r"\s+Edited$", re.I)
_YEAR_IN = re.compile(r"/\d{4}\b")
_AVATAR_TOKEN = re.compile(r"^\S{1,4}\s+")
_SECTION = re.compile(
    r"^(?:[v>›⌄]\s+(?P<any>[A-Z][\w &'-]{0,30})|(?P<known>today|yesterday|this week|last week"
    r"|this month|last month|older|pinned|favourites|favorites|chats|recent|unread"
    r"|teams and channels))$",
    re.I,
)
_ALONE = 2.0  # text heights from one row's top to the next's beyond which they are two entries
# Of the region's width. In Teams, others' text at 13%, the quotes inside their messages up
# to 20%, your own bubbles from 32%.
_OWN_SIDE = 0.25
# A bubble's fill starts a padding before its text: others' bubbles from 8% (12% beside an
# avatar), your own from 22%.
_OWN_BUBBLE = 0.15
_RIGHT_EDGE = 0.15  # of the region's width
_QUOTE_INDENT = 16  # px past the message's start
# Line heights: how far a list or code block inside a message may be indented past the row
# above it (a bullet or number and a space, nested once): 29 to 53 px at 16 to 18 px.
_INDENT = 4
_ALIGN = 8  # px: rows that start this close start together
# Line pitches from one row's top to the next across an empty line inside a message: a
# paragraph is 2.0 to 2.1 pitches, the next bubble 1.8 to 1.95 or past 2.6.
_PARAGRAPH = (2.0, 2.25)
_TOUCH = 2  # px: a row starting this close below the region's top may be cut by it
# A clock whose colon OCR lost (`1148`): too small a mark at a stamp's size.
_BARE_CLOCK = re.compile(rf"(?P<day>(?:{_DAYPART})\s+)?(?:[01]?\d|2[0-3])[0-5]\d", re.I)
_TAIL_SLACK = 4  # items before a screen's worth that a merge still searches
# Line heights of height a picture gives each row of its own: text sits a pitch apart, and a
# card or quote box adds little padding, while OCR finds a word or two in a whole picture.
_SPARSE = 3
_PICTURE = 3  # line heights: the shortest bubble without rows that is a picture


def _said(
    sender: str, time: str, text: str, who: str | None, day: str, clock: str
) -> dict[str, object]:
    """A message or quote as a dict: as the screen shows it, or, mapped, who and day added
    and the time as its clock."""
    if who is None:
        return {"sender": sender, "time": time, "text": text}
    return {"who": who, "sender": sender, "day": day, "time": clock, "text": text}


@dataclass(frozen=True)
class Quote:
    sender: str
    time: str
    text: str
    # Set by mapped(), as on Item.
    who: str | None = None
    day: str = ""
    clock: str = ""


@dataclass(frozen=True)
class Item:
    """One message, list entry or mail. refs index the lines given to items(); box and refs
    are from the read the item was kept from."""

    kind: str  # message, entry, mail
    box: Box
    refs: tuple[int, ...]
    time: str = ""
    sender: str = ""
    text: str = ""
    own: bool = False
    quote: Quote | None = None
    title: str = ""
    subtitle: str = ""
    section: str = ""
    truncated: bool = False
    cut: bool = False  # began above the region's top
    # A message as mapped() sets it with a people map, None and empty without one: time
    # stays the screen's stamp, which since, upto and the merge go by.
    who: str | None = None
    day: str = ""
    clock: str = ""

    def as_dict(self) -> dict[str, object]:
        if self.kind == "message":
            sender = self.sender or ("me" if self.own else "")
            out: dict[str, object] = _said(
                sender, self.time, self.text, self.who, self.day, self.clock
            )
            if q := self.quote:
                out["quote"] = _said(q.sender, q.time, q.text, q.who, q.day, q.clock)
        else:
            out = {"title": self.title, "subtitle": self.subtitle, "time": self.time}
            if self.text:
                out["text"] = self.text
            if self.section:
                out["section"] = self.section
        if self.truncated:
            out["truncated"] = True
        if self.cut:
            out["cut"] = True
        out["box"] = self.box
        out["refs"] = [f"L{i}" for i in self.refs]
        return out


def items(lines: list[Line], box: Box, kind: str, bubbles: Sequence[Bubble] = ()) -> list[Item]:
    """The items in the region box: kind is messages (a chat thread), entries (a chat list or
    any list) or mails (an inbox). bubbles: the thread's bubbles from the pixels
    (core.bubbles), for messages. See the module docstring for how lines become items."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
    rows = _rows(lines, box, clipped=kind == "messages")
    if kind == "messages":
        return _messages(rows, box, bubbles) if rows else []
    return _listed(rows, box, kind) if rows else []


@dataclass
class _Row:
    parts: list[tuple[int, Line]]

    @property
    def box(self) -> Box:
        return union(ln.box for _, ln in self.parts)

    @property
    def text(self) -> str:
        return _text(ln for _, ln in self.parts)


def _text(lines: Iterable[Line]) -> str:
    s = " ".join(" ".join(clean(ln.text) for ln in lines).split())
    return _CLOCKISH.sub(lambda m: m.group().translate(_CLOCK_FOLD), s)


# A clock with digits OCR read as letters (`10:5g`, `g:ll`), which would otherwise not start
# a message. No word is made of these letters around a colon.
_CLOCKISH = re.compile(r"(?<!\S)[\dOoIl|gq]{1,2}:[\dOoIl|gq]{2}(?!\S)")
_CLOCK_FOLD = str.maketrans("OoIl|gq", "0011199")


def _rows(lines: list[Line], box: Box, clipped: bool = False) -> list[_Row]:
    """clipped: the region is a viewport that scrolls under the app's own bars, so a line
    reaching past its top or bottom is partly hidden and reads as garbage (`men we neea 10`);
    the next scroll shows it whole."""
    x0, y0, w, h = box
    inside = sorted(
        (
            (i, ln)
            for i, ln in enumerate(lines)
            if x0 <= ln.box[0] + ln.box[2] / 2 < x0 + w
            and y0 <= ln.box[1] + ln.box[3] / 2 < y0 + h
            and not (clipped and (ln.box[1] < y0 or ln.box[1] + ln.box[3] > y0 + h))
        ),
        key=lambda p: p[1].box[1],
    )
    grouped: list[list[tuple[int, Line]]] = []
    for p in inside:
        b = p[1].box
        if grouped:
            q = grouped[-1][-1][1].box
            if abs(b[1] + b[3] / 2 - (q[1] + q[3] / 2)) < max(b[3], q[3]) / 2:
                grouped[-1].append(p)
                continue
        grouped.append([p])
    out: list[_Row] = []
    for parts in grouped:
        parts.sort(key=lambda p: p[1].box[0])
        keep = [p for p in parts if len(clean(p[1].text).strip()) > 2]
        if not keep and len(parts) == 1:
            ln = parts[0][1]
            aligned = bool(out) and abs(ln.box[0] - out[-1].box[0]) <= _ALIGN
            if aligned and clean(ln.text).strip().isdigit():
                keep = parts
        if keep:
            out.append(_Row(keep))
    return out


def _median_height(rows: list[_Row]) -> int:
    hs = sorted(r.box[3] for r in rows)
    return hs[len(hs) // 2]


def _name(s: str) -> tuple[str, bool]:
    s = _AVATAR.sub("", s.strip())
    cut = _ELLIPSIS.sub("", s)
    return cut, cut != s


@dataclass
class _Draft:
    own: bool
    x: int
    cut: bool = False
    sender: str = ""
    time: str = ""
    rows: list[_Row] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    quote: tuple[str, str] | None = None
    quote_x: int = 0
    quote_text: list[str] = field(default_factory=list)
    quoting: bool = False
    truncated: bool = False
    you: bool = False  # a header named You on the other side
    text_x: int = 0  # where its first row of text starts
    bubble: int | None = None  # the bubble its rows are in
    pictures: list[Box] = field(default_factory=list)  # its own, without text

    def item(self) -> Item:
        quote = None
        if self.quote:
            quote = Quote(self.quote[0], self.quote[1], "\n".join(self.quote_text))
        refs = tuple(sorted(i for r in self.rows for i, _ in r.parts))
        return Item(
            "message",
            union([r.box for r in self.rows] + self.pictures),
            refs,
            time=self.time,
            sender=self.sender,
            text="\n".join(self.text),
            own=self.own or self.you,
            quote=quote,
            truncated=self.truncated,
            cut=self.cut,
        )


def _dated(when: str, day: str) -> str:
    """when with the day of the divider above it, unless it has a day of its own."""
    return f"{day} {when}" if day and re.fullmatch(_CLOCK, when) else when


def _pitch(rows: list[_Row], h: int) -> int:
    """The step from one row's top to the next inside a paragraph: the median of the steps
    across blanks under most of a line; 0 without any."""
    steps = sorted(
        b.box[1] - a.box[1]
        for a, b in zip(rows, rows[1:], strict=False)
        if 0 <= b.box[1] - (a.box[1] + a.box[3]) < 0.8 * h
    )
    return steps[len(steps) // 2] if steps else 0


def _block(x: int, x_above: int, start: int, h: int, flush: bool = False) -> bool:
    """Whether a row at x, under a row at x_above in a message whose text starts at start,
    is in or after an indented block of it (a list, a code block) rather than the next
    bubble: indented past the row above by a few characters, or back at the message's start
    under the block. A narrower bubble below may start any distance to the right. flush:
    the row is on your side and ends where the row above ends: your bubbles are aligned
    right, a narrower one starting further right, while a list in one bubble does not end
    flush with the row above."""
    if flush:
        return False
    indented = _QUOTE_INDENT <= x - x_above <= _INDENT * h
    back = abs(x - start) <= _ALIGN and x_above >= start + _QUOTE_INDENT
    return indented or back


def _right(r: _Row) -> int:
    return r.box[0] + r.box[2]


def _stamp_edges(rows: list[_Row], side: float) -> list[int]:
    """Where your stamps end: right-aligned above your bubbles, past the text in them."""
    return [_right(r) for r in rows if r.box[0] > side and _IS_STAMP.fullmatch(r.text)]


def _holds(b: Box, r: Box) -> bool:
    """Whether r's centre lies in b."""
    cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
    return b[0] <= cx < b[0] + b[2] and b[1] <= cy < b[1] + b[3]


def _within(a: Box, b: Box) -> bool:
    """Whether a lies inside b, and is not b. A side found along a picture's own edge may
    run a pixel or two past it."""
    e = _TOUCH
    return (
        a != b
        and b[0] - e <= a[0]
        and b[1] - e <= a[1]
        and a[0] + a[2] <= b[0] + b[2] + e
        and a[1] + a[3] <= b[1] + b[3] + e
        and a[2] * a[3] < b[2] * b[3]
    )


def _bubbled(
    rows: list[_Row], bubbles: Sequence[Bubble], h: int
) -> tuple[list[_Row], list[int | None], list[int]]:
    """rows without those OCR read out of a picture, the outermost bubble each one left is
    in (an index into bubbles), and the outermost bubbles that hold none of them and are tall
    enough to be a picture."""
    boxes = [b.box for b in bubbles]
    outer = [i for i, b in enumerate(boxes) if not any(_within(b, o) for o in boxes)]
    holders = [[i for i, b in enumerate(boxes) if _holds(b, r.box)] for r in rows]
    innermost = [min(hs, key=lambda i: boxes[i][2] * boxes[i][3]) if hs else None for hs in holders]
    pictures = set()
    for i, (b, filled) in enumerate(bubbles):
        if filled:
            continue
        own = sum(k == i for k in innermost)
        inside = [c for c in boxes if _within(c, b)]
        # Height its own rows could fill: not what a bubble across it takes (a picture or a
        # card above its text); one beside text leaves the rows their height.
        free = b[3] - sum(
            c[3] for c in inside if 3 * c[2] >= 2 * b[2] and not any(_within(c, o) for o in inside)
        )
        if own * _SPARSE * h < free:
            pictures.add(i)
    keep = [k for k, hs in enumerate(holders) if not pictures.intersection(hs)]
    where = [next((i for i in outer if i in holders[k]), None) for k in keep]
    empty = [i for i in outer if i not in where and boxes[i][3] >= _PICTURE * h]
    return [rows[k] for k in keep], where, empty


def _messages(rows: list[_Row], box: Box, bubbles: Sequence[Bubble] = ()) -> list[Item]:
    side = box[0] + _OWN_SIDE * box[2]
    bubble_side = box[0] + _OWN_BUBBLE * box[2]
    middle = box[0] + box[2] / 2
    h = _median_height(rows)
    where: list[int | None] = [None] * len(rows)
    empty: list[int] = []
    if bubbles:
        rows, where, empty = _bubbled(rows, bubbles, h)
        if not rows and not empty:
            return []
        h = _median_height(rows) if rows else h
    pitch = _pitch(rows, h)
    drafts: list[_Draft] = []
    cur: _Draft | None = None
    above: int | None = None
    top: int | None = None
    day = ""
    left = right = 0  # where the row above starts and ends
    stamps = _stamp_edges(rows, side)
    stream: list[tuple[_Row | None, int | None]] = [*zip(rows, where, strict=True)]
    stream += [(None, i) for i in empty]
    stream.sort(key=lambda p: p[0].box[1] if p[0] is not None else bubbles[p[1] or 0].box[1])
    for r, bub in stream:
        if r is None:
            assert bub is not None
            pic = bubbles[bub].box
            own = pic[0] > bubble_side
            above, top, left, right = pic[1] + pic[3], pic[1], pic[0], pic[0] + pic[2]
            if not (
                cur is not None
                and cur.own == own
                and not cur.text
                and not cur.pictures
                and not cur.quote
                and (cur.time or cur.sender)
            ):
                # The next picture under the same stamp or header has no time of its own.
                same = cur is not None and cur.own == own
                sender = cur.sender if cur is not None and same else ""
                you = cur.you if cur is not None and same else False
                cur = _Draft(own=own, x=pic[0], sender=sender, you=you, cut=not drafts)
                drafts.append(cur)
            cur.pictures.append(pic)
            cur.bubble = bub
            continue
        x, t = r.box[0], _EDITED.sub("", r.text)
        gap = r.box[1] - above if above is not None else None
        step = r.box[1] - top if top is not None else None
        above, top, left, x_above = r.box[1] + r.box[3], r.box[1], x, left
        right_above, right = right, _right(r)
        if r.box[1] - box[1] <= _TOUCH and r.box[3] < h:
            # Cut by the region's top, the row shows its lower part: it starts a line above
            # its bottom.
            top = above - h
        # In the bubble the message above is in: part of that message, whatever the gap.
        held = bub is not None and cur is not None and cur.bubble == bub
        if (
            x > side
            and _BARE_CLOCK.fullmatch(t)
            and any(abs(e - _right(r)) <= _ALIGN for e in stamps)
        ):
            # A stamp of yours whose colon OCR lost, ending where your stamps end, its day
            # in front or not.
            t = f"{t[:-2]}:{t[-2:]}"
        centred = abs(x + r.box[2] / 2 - middle) <= _CENTRED * box[2] and x > side
        if not held and centred and _IS_DAY.fullmatch(t):
            day, cur = t, None
            continue
        if not held and centred and _MARKER.fullmatch(t):
            continue
        # A bubble is on one side as a whole, wherever a row in it starts.
        own = bubbles[bub].box[0] > bubble_side if bub is not None else x > side
        event = _EVENT.match(t)
        if not held and event and not own and (gap is None or gap >= 0.8 * h):
            when = _dated(event["when"], day)
            ev = _Draft(own=False, x=x, sender=SYSTEM, time=when, text=[event["text"]])
            ev.rows.append(r)
            drafts.append(ev)
            # One row: what follows has a header of its own, or is no part of the event.
            cur = None
            continue
        if not held and own and _IS_STAMP.fullmatch(t):
            cur = _Draft(own=True, x=x, time=_dated(t, day))
            drafts.append(cur)
            cur.rows.append(r)
            continue
        badge = _BADGE.match(t)
        head = (badge and _HEADER.match(t[badge.end() :])) or _HEADER.match(t)
        head = head if head and len(head["name"].split()) <= 5 else None
        # A quote's header has the year (14/03/2030 10:49), a message's never does: a quote
        # whose message began above the region's top is still a quote.
        quoted = bool(head and _YEAR_IN.search(head["when"]))
        indented = cur is not None and not cur.own and x >= cur.x + _QUOTE_INDENT
        if head and (own or quoted or indented):
            if not held and (
                cur is None or (own and (not cur.own or cur.quote)) or (quoted and cur.quote)
            ):
                cur = _Draft(own=own, x=x, cut=not drafts)
                drafts.append(cur)
            assert cur is not None
            if cur.quote is None:
                name, trunc = _name(head["name"])
                cur.quote, cur.quote_x, cur.quoting = (name, head["when"]), x, True
                cur.truncated |= trunc
                cur.rows.append(r)
                cur.bubble = bub if bub is not None else cur.bubble
                continue
        if not held and head and not own and (gap is None or gap >= 0.8 * h):
            name, trunc = _name(head["name"])
            when = _dated(head["when"], day)
            you = name.casefold() in _YOU
            cur = _Draft(own=False, x=x, sender=name, time=when, truncated=trunc, you=you)
            drafts.append(cur)
            cur.rows.append(r)
            continue
        if cur is not None and cur.quoting and own == cur.own and x >= cur.quote_x - _ALIGN:
            cur.quote_text.append(t)
            cur.rows.append(r)
            continue
        if held:
            pass
        elif cur is None or own != cur.own:
            cur = _Draft(own=own, x=x, cut=not drafts)
            drafts.append(cur)
        elif (bub is not None and cur.bubble is not None and (cur.text or cur.pictures)) or (
            cur.text
            and not cur.quoting
            and gap is not None
            and gap > h
            and not (
                step
                and pitch
                and step <= _PARAGRAPH[1] * pitch
                and (
                    step >= _PARAGRAPH[0] * pitch
                    or _block(x, x_above, cur.text_x, h, own and abs(right - right_above) <= _ALIGN)
                )
            )
        ):
            # The sender's next message under the same header.
            nxt = _Draft(own=cur.own, x=cur.x, sender=cur.sender, you=cur.you)
            cur = nxt
            drafts.append(cur)
        assert cur is not None
        if not own and not cur.text and cur.sender and x < cur.x - _ALIGN:
            # The sender's avatar sits left of the header's column, beside the first row:
            # its initials, or what OCR makes of a photo, are no part of the text.
            t = _AVATAR_TOKEN.sub("", t)
            words = [w for _, ln in r.parts for w in ln.words]
            left = words[1].box[0] if len(words) > 1 else cur.x
        if not cur.text:
            cur.text_x = left
        cur.quoting = False
        cur.text.append(t)
        cur.rows.append(r)
        cur.bubble = bub if bub is not None else cur.bubble
    return [d.item() for d in drafts]


def _step(above: _Row, row: _Row) -> int:
    """How far below the row above a row starts. Top to top, not the blank between: a blank
    moves with every descender and ascender in the row above (9 to 18 px between rows 22 px
    apart in one inbox), the tops by a pixel or two."""
    return row.box[1] - above.box[1]


def _split_gap(gaps: list[int]) -> float:
    """The step from which rows belong to different items: midway across the widest jump
    between step sizes when the wide ones are clearly wider, else any step."""
    s = sorted(max(0, g) for g in gaps)
    if len(s) < 2:
        return -1.0
    jumps = sorted(
        ((b - a, i) for i, (a, b) in enumerate(zip(s, s[1:], strict=False))), reverse=True
    )
    jump, i = jumps[0]
    lo, hi = s[i], s[i + 1]
    if hi >= 1.5 * lo + 2:
        return (lo + hi) / 2
    # Closer kinds (rows 22 px apart in an item, 30 between items) still split when the
    # jump between them stands out from the jitter inside each kind.
    second = jumps[1][0] if len(jumps) > 1 else 0
    if i >= 1 and jump >= max(3, 0.15 * lo) and jump >= 2 * second:
        return (lo + hi) / 2
    return -1.0


def _listed(rows: list[_Row], box: Box, kind: str) -> list[Item]:
    sections: list[str] = []
    gaps: list[int] = []
    prev: _Row | None = None
    for r in rows:
        m = _SECTION.match(r.text)
        sections.append((m["any"] or m["known"]) if m else "")
        if m:
            prev = None
            continue
        if prev is not None:
            gaps.append(_step(prev, r))
        prev = r
    split = _split_gap(gaps)
    # Rows of one entry are a line pitch apart, never two: Teams favourites, one row
    # each 40 px apart with 17 px text, would split on wider steps around their section
    # headers instead and come out as one entry.
    hs = sorted(r.box[3] for r in rows)
    alone = _ALONE * hs[len(hs) // 2] if hs else float("inf")
    groups: list[tuple[str, list[_Row]]] = []
    section, prev = "", None
    for r, sec in zip(rows, sections, strict=True):
        if sec:
            section, prev = sec, None
            continue
        if prev is None or _step(prev, r) > split or _step(prev, r) > alone:
            groups.append((section, []))
        groups[-1][1].append(r)
        prev = r
    edge = box[0] + box[2] * (1 - _RIGHT_EDGE)
    out = []
    for section, group in groups:
        time, texts = "", []
        for r in group:
            t = r.text
            last = r.parts[-1][1]
            if not time and last.box[0] + last.box[2] >= edge:
                lt = _text([last])
                if _IS_WHEN.fullmatch(lt):
                    time, t = lt, _text(ln for _, ln in r.parts[:-1])
                elif m := _TAIL.search(t):
                    time, t = m["when"], t[: m.start()]
            if t:
                texts.append(t)
        if not texts and not time:
            continue
        title, truncated = _name(texts[0]) if texts else ("", False)
        rest = []
        for t in texts[1:]:
            short = _ELLIPSIS.sub("", t)
            truncated |= short != t
            rest.append(short)
        refs = tuple(sorted(i for r in group for i, _ in r.parts))
        out.append(
            Item(
                "mail" if kind == "mails" else "entry",
                union(r.box for r in group),
                refs,
                time=time,
                title=title,
                subtitle=rest[0] if rest else "",
                text="\n".join(rest[1:]),
                section=section,
                truncated=truncated,
            )
        )
    return out


def _fold(s: str) -> str:
    """What two reads of the same text share: OCR changes spacing, case and punctuation."""
    return re.sub(r"[\W_]+", "", normalize(s).lower())


def _join(a: Item, b: Item, edge: bool) -> Item | None:
    """a from the read above, b from the read below, as one item if they are the same one.
    edge: a is the last item above and b the first below."""
    if a.kind != b.kind or a.own != b.own:
        return None
    # A message with neither sender nor time began above the read below (or is the next one
    # under a header above it): only its text can tell which one it is.
    if b.cut or (b.kind == "message" and not b.sender and not b.time and not b.own):
        # b lost its header to the region's top: it is a's rows from some row on.
        ra = [_fold(t) for t in a.text.split("\n")] if a.text else []
        rb = [_fold(t) for t in b.text.split("\n")] if b.text else []
        if not rb:
            return None
        if not ra:
            # a's header was the last row above; nothing to compare, only the place.
            return replace(a, text=b.text) if edge else None
        n = len(rb)
        if any(ra[i : i + n] == rb for i in range(len(ra) - n + 1)):
            return a
        k = next((k for k in range(min(len(ra), n), 0, -1) if ra[-k:] == rb[:k]), 0)
        if not k:
            return None
        return replace(a, text="\n".join(a.text.split("\n") + b.text.split("\n")[k:]))
    if a.cut:
        return None
    if _fold(a.sender or a.title) != _fold(b.sender or b.title) or not same_time(a.time, b.time):
        return None
    ta, tb = _fold(a.subtitle + a.text), _fold(b.subtitle + b.text)
    n = min(len(ta), len(tb), 30)
    if n and difflib.SequenceMatcher(None, ta[:n], tb[:n]).ratio() < 0.85:
        return None
    # The region's bottom may have cut a short; the longer read is the whole item, and the
    # time with its day is the one to keep.
    return replace(b if len(tb) > len(ta) else a, time=max(a.time, b.time, key=len))


_TODAY = re.compile(r"^\s*today\b[,\s]*", re.I)


def same_time(a: str, b: str) -> bool:
    """Whether two reads of an item's time name the same one. A day divider above a message
    dates its bare time (`Today 10:44`) only in the read that had the divider on screen,
    so a bare clock matches the same clock under any day."""
    fa, fb = _fold(_TODAY.sub("", a)), _fold(_TODAY.sub("", b))
    if fa == fb:
        return True
    bare = [t for t in (a, b) if re.fullmatch(_CLOCK, t.strip())]
    if len(bare) != 1:
        return False
    other = b if bare[0] is a else a
    m = re.search(rf"(?:^|\s)({_CLOCK})$", other.strip())
    return bool(m) and m.group(1).lstrip("0") == bare[0].strip().lstrip("0")


def merge(older: list[Item], newer: list[Item]) -> list[Item]:
    """Items read at two scroll positions as one list, each item once: older is the read
    further up (earlier messages; later mails), newer the one below it. See merged."""
    return merged(older, newer)[0]


RECENT = 16  # items at the end of what was collected that an anchor is looked for among


def merged(older: list[Item], newer: list[Item]) -> tuple[list[Item], bool]:
    """merge, and whether the two reads overlapped: a read below that shares no item with
    the one above may have skipped items between them (the list moved more than a screen).

    The anchor first: the newest of older's last RECENT items that newer holds read the
    same way (item_key, and only one such in each). The read below continues from there:
    what newer shows above the anchor is older's already, and only the anchor and what
    follows it are matched item by item (`_fuzzy`), so nothing below it can be pulled back
    onto an older item that merely reads alike. With no anchor, every item is matched."""
    if not older or not newer:
        return older + newer, True
    if at := _anchor(older, newer):
        i, j = at
        tail, _ = _fuzzy(older[i:], newer[j:])
        return _unique(older[:i] + tail), True
    out, joins = _fuzzy(older, newer)
    return _unique(out), joins > 0


def item_key(it: Item) -> str:
    """An item as two reads of it agree on it: kind and side, sender or title, the clock of
    its time (a day divider dates it in one read only), its first 40 characters, folded."""
    clock = re.search(rf"{_CLOCK}$", it.time.strip())
    when = clock.group().lstrip("0") if clock else _fold(it.time)
    text = _fold(it.subtitle + it.text)[:40]
    return "|".join((it.kind, str(it.own), _fold(it.sender or it.title), when, text))


def _anchor(older: list[Item], newer: list[Item]) -> tuple[int, int] | None:
    def once(its: list[Item]) -> dict[str, int]:
        keys: dict[str, int] = {}
        for n, it in enumerate(its):
            k = item_key(it)
            keys[k] = -1 if k in keys or it.cut or not (it.text or it.title) else n
        return keys

    recent = once(older[-RECENT:])
    below = once(newer)
    for k, i in sorted(recent.items(), key=lambda kv: -kv[1]):
        if i >= 0 and below.get(k, -1) >= 0:
            return len(older) - len(older[-RECENT:]) + i, below[k]
    return None


def _fuzzy(older: list[Item], newer: list[Item]) -> tuple[list[Item], int]:
    """Items matched in order on sender or title, time, and the first 30 characters of the
    text, folded for OCR; a message cut at newer's top joins the one above whose rows it
    continues. box and refs stay those of the read the item was taken from. With how many
    items joined.

    A screen read below can only overlap the end of what was collected above it, so only that
    end is searched: a short line it shares with a message days earlier (`ok`, a repeated
    question) would otherwise pull it, and every item after it, back to there."""
    tail = max(0, len(older) - 2 * len(newer) - _TAIL_SLACK)
    if tail:
        out, joins = _fuzzy(older[tail:], newer)
        return older[:tail] + out, joins
    out: list[Item] = []
    pending: list[Item] = []
    i = joins = 0
    for n, it in enumerate(newer):
        found = None
        for k in range(i, len(older)):
            if joined := _join(older[k], it, n == 0 and k == len(older) - 1):
                found = k, joined
                break
        if found is None:
            pending.append(it)
            continue
        j, joined = found
        out += older[i:j] + pending + [joined]
        pending = []
        i = j + 1
        joins += 1
    return out + older[i:] + pending, joins


_NEARBY = 12  # items apart a repeat is looked for: the same item read at two scroll places


def _unique(its: list[Item]) -> list[Item]:
    """its without a message read twice that the merge kept twice (its time read with its
    day in one place and without in the other): same sender, same time, same text.
    Messages without a time are left alone: two short ones under one header can be
    alike."""
    out: list[Item] = []
    for it in its:
        near = range(len(out) - 1, max(-1, len(out) - 1 - _NEARBY), -1)
        twin = next((k for k in near if _twins(out[k], it)), None)
        if twin is None:
            out.append(it)
        elif len(it.time) > len(out[twin].time):
            out[twin] = replace(out[twin], time=it.time)
    return out


def _twins(a: Item, b: Item) -> bool:
    return (
        bool(a.time and b.time)
        and a.kind == b.kind == "message"
        and a.own == b.own
        and _fold(a.sender or a.title) == _fold(b.sender or b.title)
        and _fold(a.subtitle + a.text) == _fold(b.subtitle + b.text)
        and same_time(a.time, b.time)
    )


def parse_when(s: str, now: datetime) -> tuple[datetime, bool] | None:
    """A list's or a thread's time as a datetime, and whether it had a clock: `dd/mm hh:mm`,
    `dd/mm/yyyy hh:mm`, `hh:mm` (today), `Mon 18/03`, `Mon`, `Yesterday`, `Yesterday hh:mm`,
    `21 March`, `Mar 21, 2030 hh:mm`, `Thursday 21 Mar`. A date without a year is the latest
    one not after now."""
    s = " ".join(clean(s).lower().split())
    clock = None
    if m := re.search(r"(?:^|\s)(\d{1,2}):(\d{2})$", s):
        clock = (int(m[1]), int(m[2]))
        s = s[: m.start()].strip()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = rf"(?:(?:{_DAYS}),?\s+)?"
    try:
        dmy: tuple[str, str, str | None] | None = None
        if m := re.fullmatch(rf"{weekday}(\d{{1,2}})/(\d{{1,2}})(?:/(\d{{2,4}}))?", s):
            dmy = (m[1], m[2], m[3])
        elif m := re.fullmatch(rf"{weekday}(\d{{1,2}})\s+({_MONTHS})\.?(?:,?\s+(\d{{4}}))?", s):
            dmy = (m[1], _month(m[2]), m[3])
        elif m := re.fullmatch(rf"{weekday}({_MONTHS})\.?\s+(\d{{1,2}})(?:,?\s+(\d{{4}}))?", s):
            dmy = (m[2], _month(m[1]), m[3])
        if s in ("", "today"):
            day = today
        elif s == "yesterday":
            day = today - timedelta(days=1)
        elif dmy is not None:
            year = int(dmy[2]) if dmy[2] else today.year
            year += 2000 if year < 100 else 0
            day = today.replace(year=year, month=int(dmy[1]), day=int(dmy[0]))
            if not dmy[2] and day > today:
                day = day.replace(year=year - 1)
        elif re.fullmatch(_DAYS, s):
            wd = next(i for i, d in enumerate(_DAYS.split("|")) if d == s) % 7
            day = today - timedelta(days=(today.weekday() - wd) % 7)
        else:
            return None
        if clock is None:
            return day, False
        return day.replace(hour=clock[0], minute=clock[1]), True
    except ValueError:
        return None


def _month(name: str) -> str:
    return str(1 + [m[:3] for m in _MONTHS.split("|")[:12]].index(name[:3]))


INHERITED = "~"  # in front of a time a message got from the one above it


def mapped(items: Iterable[Item], people: People, now: datetime) -> list[Item]:
    """items with each message's who, day and clock set (see records); other items as they
    are. A message under an earlier header borrows that one's day and clock from the item
    above it, so map a whole read, after any merge (which keeps one read's fields)."""
    out: list[Item] = []
    last_day = last_clock = ""
    for it in items:
        if it.kind != "message":
            out.append(it)
            continue
        day, clock = _when(it.time, now)
        if clock:
            last_day, last_clock = day, clock
        else:
            # Under the header above: its day, and its time marked as borrowed.
            day = day or last_day
            clock = f"{INHERITED}{last_clock}" if last_clock else ""
        who = SYSTEM if it.sender == SYSTEM else people.who(it.sender, it.own)
        quote = it.quote
        if quote:
            qday, qclock = _when(quote.time, now)
            quote = replace(quote, who=people.who(quote.sender), day=qday, clock=qclock)
        out.append(replace(it, who=who, day=day, clock=clock, quote=quote))
    return out


def records(items: Iterable[Item], people: People, now: datetime) -> list[dict[str, Any]]:
    """Messages as an agent keeps them: `who` the sender's id in people ("" for one it does
    not know, SYSTEM for an event row), `day` the absolute date (ISO) when the screen gave one
    (a divider above, a date in the stamp; "" when it did not: a bare clock says nothing about
    the day), `time` the clock as `HH:MM`, or the one above's with `~` in front for a message
    under an earlier header, `text`, `own`, `cut`, and the quote's fields under `quote`."""
    out: list[dict[str, Any]] = []
    for it in mapped(items, people, now):
        if it.kind != "message":
            continue
        rec: dict[str, Any] = {
            "who": it.who,
            "day": it.day,
            "time": it.clock,
            "text": it.text,
            "own": it.own,
            "cut": it.cut,
        }
        if q := it.quote:
            rec["quote"] = {"who": q.who, "day": q.day, "time": q.clock, "text": q.text}
        out.append(rec)
    return out


def _when(time: str, now: datetime) -> tuple[str, str]:
    """A stamp as the day it names (ISO, "" when it names none) and its clock (`HH:MM`,
    "" without one)."""
    s = time.strip()
    if not s:
        return "", ""
    m = re.search(rf"(?:^|\s)({_CLOCK})$", s)
    clock = ""
    if m:
        hh, mm = m.group(1).split(":")
        clock = f"{int(hh):02d}:{mm}"
    if re.fullmatch(_CLOCK, s):
        return "", clock
    parsed = parse_when(s, now)
    return (parsed[0].date().isoformat() if parsed else ""), clock


def _start(when: datetime | str, now: datetime) -> tuple[datetime, bool]:
    if isinstance(when, str):
        parsed = parse_when(when, now)
        if parsed is None:
            raise ValueError(f"cannot read {when!r} as a date or time")
        return parsed
    return when, True


def _timed(items: list[Item], now: datetime) -> Iterable[tuple[Item, tuple[datetime, bool]]]:
    """Each item with its time. A message without a time (one of several under one header)
    has the time of the one before it; other items without a time, or none before, are
    left out."""
    last: tuple[datetime, bool] | None = None
    for it in items:
        at = parse_when(it.time, now) if it.time else None
        if at is None and it.kind == "message":
            at = last
        last = at or last
        if at is not None:
            yield it, at


def _cmp(at: tuple[datetime, bool], start: tuple[datetime, bool]) -> int:
    """-1, 0 or 1 as at is before, at or after start; by day without a clock on either."""
    (dt, clock), (st, st_clock) = at, start
    a, b = (dt, st) if clock and st_clock else (dt.date(), st.date())
    return (a > b) - (a < b)  # type: ignore[operator]


def since(items: list[Item], when: datetime | str, now: datetime) -> list[Item]:
    """The items at or after when (a datetime or a string parse_when reads). Without a clock
    on either side, by day."""
    start = _start(when, now)
    return [it for it, at in _timed(items, now) if _cmp(at, start) >= 0]


def upto(items: list[Item], when: datetime | str, now: datetime) -> list[Item]:
    """The items at or before when; by day without a clock on either side, so a day keeps
    all of its messages."""
    end = _start(when, now)
    return [it for it, at in _timed(items, now) if _cmp(at, end) <= 0]


def after(items: list[Item], when: datetime | str, now: datetime) -> bool:
    """Whether an item is past when: a list scrolled on far enough to hold every item up
    to it."""
    end = _start(when, now)
    return any(_cmp(at, end) > 0 for _, at in _timed(items, now))


def before(items: list[Item], when: datetime | str, now: datetime) -> bool:
    """Whether an item is at or before when: a list scrolled back far enough to hold every
    item since then. A day without a clock is its start, so a message later that day is not
    before it: scrolled back to 18/03 11:25, 18/03's earlier messages are still to come."""
    day, clock = _start(when, now)
    start = (day, True) if clock else (day.replace(hour=0, minute=0), True)
    return any(_cmp(at, start) <= 0 for _, at in _timed(items, now))
