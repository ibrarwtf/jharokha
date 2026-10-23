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
  of yours. A name-and-time row indented 16 px past the message's start (or any on your
  side) opens a quote, which takes the rows at its indent until one starts left of it. Rows
  before any header are a message cut at the region's top. A row switching sides starts a
  new message, and so does a blank taller than a line between two rows of text: the next
  message from the same sender under the same header, without a time of its own.
- A date alone on a row, centred (`Yesterday`, `Monday`, `16 October`), divides a thread
  by day: the messages after it whose time has no date get it in front (`Yesterday 14:05`).
- entries, mails (a list): a section header (`Today`, `v Chats`, ...) starts a section; the
  rows between are split at the larger of two kinds of step from one row's top to the next
  (inside an item, between items); with one kind only, each row is an item. Rows are title,
  subtitle, then text; a date or time at the right edge is the item's time.
- Names lose avatar initials glued in front (`IA Ana Alder`); a trailing `..`, `...` or `…`
  on a name, title or subtitle is cut off and the item marked truncated.

Numeric dates are day first (dd/mm), as the sessions this was measured on print them; month
names are read either way round (`16 October`, `Oct 16`)."""

from __future__ import annotations

import difflib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from jharokha.core.models import Box, Line, union
from jharokha.core.text import clean, normalize, wrap_untrusted

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
_AVATAR = re.compile(r"^[A-Z]{2}\s+(?=[A-Z][a-z])")
# A quote's avatar reads as one letter in front of the name.
_BADGE = re.compile(r"^[A-Za-z0-9]\s+(?=[A-Z])")
_ELLIPSIS = re.compile(r"\s*(?:\.{2,}|…)$")
_SECTION = re.compile(
    r"^(?:[v>›⌄]\s+(?P<any>[A-Z][\w &'-]{0,30})|(?P<known>today|yesterday|this week|last week"
    r"|this month|last month|older|pinned|favourites|favorites|chats|recent|unread"
    r"|teams and channels))$",
    re.I,
)
_OWN_SIDE = 0.15  # of the region's width
_RIGHT_EDGE = 0.15  # of the region's width
_QUOTE_INDENT = 16  # px past the message's start
_ALIGN = 8  # px: rows that start this close start together


@dataclass(frozen=True)
class Quote:
    sender: str
    time: str
    text: str


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

    def as_dict(self) -> dict[str, object]:
        if self.kind == "message":
            out: dict[str, object] = {
                "sender": self.sender,
                "time": self.time,
                "text": self.text,
                "own": self.own,
            }
            if self.quote:
                out["quote"] = {
                    "sender": self.quote.sender,
                    "time": self.quote.time,
                    "text": self.quote.text,
                }
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


def format_items(items: list[Item]) -> str:
    return wrap_untrusted(json.dumps([it.as_dict() for it in items], separators=(",", ":")))


def items(lines: list[Line], box: Box, kind: str) -> list[Item]:
    """The items in the region box: kind is messages (a chat thread), entries (a chat list or
    any list) or mails (an inbox). See the module docstring for how lines become items."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
    rows = _rows(lines, box)
    if not rows:
        return []
    return _messages(rows, box) if kind == "messages" else _listed(rows, box, kind)


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


def _rows(lines: list[Line], box: Box) -> list[_Row]:
    x0, y0, w, h = box
    inside = sorted(
        (
            (i, ln)
            for i, ln in enumerate(lines)
            if x0 <= ln.box[0] + ln.box[2] / 2 < x0 + w and y0 <= ln.box[1] + ln.box[3] / 2 < y0 + h
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

    def item(self) -> Item:
        quote = None
        if self.quote:
            quote = Quote(self.quote[0], self.quote[1], "\n".join(self.quote_text))
        refs = tuple(sorted(i for r in self.rows for i, _ in r.parts))
        return Item(
            "message",
            union(r.box for r in self.rows),
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


def _messages(rows: list[_Row], box: Box) -> list[Item]:
    side = box[0] + _OWN_SIDE * box[2]
    middle = box[0] + box[2] / 2
    h = _median_height(rows)
    drafts: list[_Draft] = []
    cur: _Draft | None = None
    above: int | None = None
    day = ""
    for r in rows:
        x, t = r.box[0], r.text
        gap = r.box[1] - above if above is not None else None
        above = r.box[1] + r.box[3]
        centred = abs(x + r.box[2] / 2 - middle) <= _CENTRED * box[2] and x > side
        if centred and _IS_DAY.fullmatch(t):
            day, cur = t, None
            continue
        own = x > side
        if own and _IS_STAMP.fullmatch(t):
            cur = _Draft(own=True, x=x, time=_dated(t, day))
            drafts.append(cur)
            cur.rows.append(r)
            continue
        badge = _BADGE.match(t)
        head = (badge and _HEADER.match(t[badge.end() :])) or _HEADER.match(t)
        head = head if head and len(head["name"].split()) <= 5 else None
        if head and (own or (cur is not None and not cur.own and x >= cur.x + _QUOTE_INDENT)):
            if own and (cur is None or not cur.own or cur.quote):
                cur = _Draft(own=True, x=x, cut=not drafts)
                drafts.append(cur)
            if cur is not None and cur.quote is None:
                name, trunc = _name(head["name"])
                cur.quote, cur.quote_x, cur.quoting = (name, head["when"]), x, True
                cur.truncated |= trunc
                cur.rows.append(r)
                continue
        if head and not own and (gap is None or gap >= 0.8 * h):
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
        if cur is None or own != cur.own:
            cur = _Draft(own=own, x=x, cut=not drafts)
            drafts.append(cur)
        elif cur.text and not cur.quoting and gap is not None and gap > h:
            # The sender's next message under the same header.
            nxt = _Draft(own=cur.own, x=cur.x, sender=cur.sender, you=cur.you)
            cur = nxt
            drafts.append(cur)
        cur.quoting = False
        cur.text.append(t)
        cur.rows.append(r)
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
    groups: list[tuple[str, list[_Row]]] = []
    section, prev = "", None
    for r, sec in zip(rows, sections, strict=True):
        if sec:
            section, prev = sec, None
            continue
        if prev is None or _step(prev, r) > split:
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
    if b.cut:
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
    if _fold(a.sender or a.title) != _fold(b.sender or b.title) or _fold(a.time) != _fold(b.time):
        return None
    ta, tb = _fold(a.subtitle + a.text), _fold(b.subtitle + b.text)
    n = min(len(ta), len(tb), 30)
    if n and difflib.SequenceMatcher(None, ta[:n], tb[:n]).ratio() < 0.85:
        return None
    # The region's bottom may have cut a short; the longer read is the whole item.
    return b if len(tb) > len(ta) else a


def merge(older: list[Item], newer: list[Item]) -> list[Item]:
    """Items read at two scroll positions as one list, each item once: older is the read
    further up (earlier messages; later mails), newer the one below it. Items match in
    order on sender or title, time, and the first 30 characters of the text, folded for
    OCR; a message cut at newer's top joins the one above whose rows it continues. box and
    refs stay those of the read the item was taken from."""
    out: list[Item] = []
    pending: list[Item] = []
    i = 0
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
    return out + older[i:] + pending


def parse_when(s: str, now: datetime) -> tuple[datetime, bool] | None:
    """A list's or a thread's time as a datetime, and whether it had a clock: `dd/mm hh:mm`,
    `dd/mm/yyyy hh:mm`, `hh:mm` (today), `Mon 19/10`, `Mon`, `Yesterday`, `Yesterday hh:mm`,
    `16 October`, `Oct 16, 2026 hh:mm`, `Friday 16 Oct`. A date without a year is the latest
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


def before(items: list[Item], when: datetime | str, now: datetime) -> bool:
    """Whether an item is at or before when: a list scrolled back far enough to hold every
    item since then."""
    start = _start(when, now)
    return any(_cmp(at, start) <= 0 for _, at in _timed(items, now))
