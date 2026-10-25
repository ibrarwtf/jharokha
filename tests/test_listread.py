from datetime import datetime

import pytest

from jharokha.core.listread import Item, items, merge, parse_when, since
from jharokha.core.models import Line
from jharokha.core.readers import format_items

from .conftest import lines_for

# Laid out like a chat thread measured live, with invented people and text: other people's
# headers at x 540, their text at 551, quotes at 574, your messages at 683 under a time at
# the right. Rows 16 px apart inside a message, a blank of 16 px or more between messages.
THREAD_BOX = (464, 100, 686, 500)  # x and width as the live thread region
THREAD = [
    ("o", 524, 100),  # an avatar read as a letter
    ("Ana Alder", 540, 100),
    ("16/10 11:20", 620, 100),
    ("Morning, is the build green?", 551, 116),
    ("I saw a red run overnight.", 551, 132),
    ("16/10 11:40", 1045, 160),
    ("Looking now", 683, 176),
    ("Ben Brook 16/10 11:45", 540, 204),
    ("Fixed it, the cache was stale", 551, 220),
    ("9", 1190, 236),  # a reaction icon
    ("Ana Alder 16/10 11:50", 540, 256),
    ("O Ben Brook 16/10/2026 11:45", 574, 272),
    ("Fixed it, the cache was stale", 574, 288),
    ("Thanks, merging", 551, 304),
    ("42", 551, 320),
    ("16/10 11:55", 1045, 348),
    ("O Ana Alder 16/10/2026 11:50", 706, 364),
    ("Thanks, merging", 706, 380),
    ("You are welcome", 683, 396),
]
FIRST = "Morning, is the build green?\nI saw a red run overnight."
NOW = datetime(2026, 10, 23, 12, 0)


def messages(rows: list[tuple[str, int, int]], box=THREAD_BOX) -> list[Item]:
    return items(lines_for(rows), box, "messages")


def plain(its: list[Item]) -> list[tuple[str, str, str, bool]]:
    return [(it.sender, it.time, it.text, it.own) for it in its]


def test_thread_alternates_senders_and_your_own():
    got = messages(THREAD)
    assert plain(got) == [
        ("Ana Alder", "16/10 11:20", FIRST, False),
        ("", "16/10 11:40", "Looking now", True),
        ("Ben Brook", "16/10 11:45", "Fixed it, the cache was stale", False),
        ("Ana Alder", "16/10 11:50", "Thanks, merging\n42", False),
        ("", "16/10 11:55", "You are welcome", True),
    ]
    assert not any(it.cut for it in got)


def test_quoted_reply_keeps_the_quote_apart():
    reply, own = messages(THREAD)[3:]
    assert reply.quote is not None
    assert (reply.quote.sender, reply.quote.time) == ("Ben Brook", "16/10/2026 11:45")
    assert reply.quote.text == "Fixed it, the cache was stale"
    assert own.quote is not None and own.quote.sender == "Ana Alder"
    assert own.quote.text == "Thanks, merging"


def test_items_keep_their_lines():
    lines = lines_for(THREAD)
    first = items(lines, THREAD_BOX, "messages")[0]
    # The avatar letter is junk, the header's two lines and both text rows are the item.
    assert first.refs == (1, 2, 3, 4)
    assert first.box == (540, 100, 551 - 540 + 7 * len("Morning, is the build green?"), 44)
    assert first.as_dict()["refs"] == ["L1", "L2", "L3", "L4"]


def test_message_cut_at_the_top():
    got = messages(
        [
            ("of the deploy, so we wait.", 551, 100),
            ("Ana Alder 16/10 12:00", 540, 128),
            ("Agreed", 551, 144),
        ]
    )
    assert got[0].cut and got[0].sender == "" and got[0].text == "of the deploy, so we wait."
    assert (got[1].sender, got[1].text, got[1].cut) == ("Ana Alder", "Agreed", False)


def test_a_time_at_the_end_of_text_is_not_a_header():
    got = messages(
        [
            ("Ana Alder 16/10 12:00", 540, 100),
            ("Can we move the call to", 551, 116),
            ("Friday 14:30", 551, 132),
        ]
    )
    assert len(got) == 1 and got[0].text.endswith("Friday 14:30")


CHATS_BOX = (0, 100, 300, 500)
CHATS = [
    ("v Favourites", 10, 100),
    ("IA Ana Alder", 10, 124),
    ("10:12", 260, 124),
    ("Is the build green?", 40, 140),
    ("x", 290, 140),
    ("FK Farah Kim", 10, 164),
    ("Yesterday", 236, 164),
    ("See you at the stand...", 40, 180),
    ("v Chats", 10, 204),
    ("BB Ben Broo..", 10, 228),
    ("Mon", 270, 228),
    ("Thanks!", 40, 244),
    ("e", 5, 260),
]


def test_chat_list_entries():
    got = items(lines_for(CHATS), CHATS_BOX, "entries")
    assert [(it.title, it.subtitle, it.time, it.section, it.truncated) for it in got] == [
        ("Ana Alder", "Is the build green?", "10:12", "Favourites", False),
        ("Farah Kim", "See you at the stand", "Yesterday", "Favourites", True),
        ("Ben Broo", "Thanks!", "Mon", "Chats", True),
    ]
    assert got[0].as_dict()["section"] == "Favourites"


MAIL_BOX = (300, 100, 500, 600)
MAIL = [
    ("Today", 310, 100),
    ("Ana Alder", 310, 124),
    ("Build report", 310, 140),
    ("13:23", 760, 140),
    ("All green this morning", 310, 156),
    ("D", 770, 156),
    ("Ben Brook", 310, 184),
    ("Lunch on Friday? 09:10", 310, 200),
    ("Who is in", 310, 216),
    ("This Week", 310, 244),
    ("Farah Kim", 310, 268),
    ("Release notes", 310, 284),
    ("Mon 19/10", 732, 284),
    ("Draft attached", 310, 300),
    ("Last Week", 310, 328),
    ("Ana Alder", 310, 352),
    ("Re: Build report", 310, 368),
    ("Thu 08/10", 732, 368),
    ("0)", 790, 384),
]


def test_mail_list_with_group_headers():
    got = items(lines_for(MAIL), MAIL_BOX, "mails")
    assert [(it.kind, it.title, it.subtitle, it.time, it.text, it.section) for it in got] == [
        ("mail", "Ana Alder", "Build report", "13:23", "All green this morning", "Today"),
        # A time inside the subject, away from the right edge, is not the item's time.
        ("mail", "Ben Brook", "Lunch on Friday? 09:10", "", "Who is in", "Today"),
        ("mail", "Farah Kim", "Release notes", "Mon 19/10", "Draft attached", "This Week"),
        ("mail", "Ana Alder", "Re: Build report", "Thu 08/10", "", "Last Week"),
    ]


def test_format_items_is_untrusted():
    out = format_items(items(lines_for(MAIL), MAIL_BOX, "mails"))
    assert out.startswith('<screen_text source="ocr" untrusted="true">')
    assert '"title":"Ana Alder"' in out


def test_unknown_kind():
    with pytest.raises(ValueError):
        items([], THREAD_BOX, "rows")


# A thread taller than the region, read at three scroll positions that overlap.
LONG = [
    ("Ana Alder 20/10 09:00", 540, 0),
    ("Starting the release today", 551, 16),
    ("20/10 09:05", 1045, 44),
    ("Good luck", 683, 60),
    ("Ben Brook 20/10 09:30", 540, 88),
    ("Tests are running on the", 551, 104),
    ("new runner, they take a", 551, 120),
    ("while longer than before", 551, 136),
    ("Ana Alder 20/10 10:00", 540, 164),
    ("Green now", 551, 180),
    ("20/10 10:02", 1045, 208),
    ("Ship it", 683, 224),
    ("Ben Brook 21/10 08:00", 540, 252),
    ("Shipped", 551, 268),
    ("Ana Alder 22/10 17:45", 540, 296),
    ("Thanks all", 551, 312),
]


def page(top: int, height: int = 150) -> list[Item]:
    """What the region shows when scrolled so that thread y top is at its top."""
    rows = [(t, x, y - top + 100) for t, x, y in LONG if top <= y < top + height - 12]
    return messages(rows, (464, 100, 686, height))


def test_three_pages_merge_into_one_thread():
    whole = page(0, 400)
    first, second, third = page(0), page(110), page(200)
    assert second[0].cut  # starts inside Ben's long message
    got = merge(first, merge(second, third))
    assert plain(got) == plain(whole)
    # Paging back from the bottom collects the same.
    assert plain(merge(first, merge(second, third))) == plain(merge(merge(first, second), third))


def test_merge_joins_a_message_split_across_pages():
    above, below = page(0, 140), page(110)
    assert above[2].text == "Tests are running on the\nnew runner, they take a"
    assert below[0].cut and below[0].text == "new runner, they take a\nwhile longer than before"
    assert plain(merge(above, below)) == plain(page(0, 260))


def test_merge_tolerates_spacing_and_case():
    a = messages([("Ana Alder 16/10 11:20", 540, 100), ("Is the build green?", 551, 116)])
    b = messages([("Ana  Alder 16/10 11:20", 540, 100), ("is the bui ld green ?", 551, 116)])
    assert len(merge(a, b)) == 1


def test_merge_keeps_repeated_texts_from_different_times():
    a = messages([("Ana Alder 16/10 11:20", 540, 100), ("ok", 551, 116)])
    b = messages([("Ana Alder 16/10 11:21", 540, 100), ("ok", 551, 116)])
    assert len(merge(a, b)) == 2


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("16/10 11:40", (datetime(2026, 10, 16, 11, 40), True)),
        ("16/10/2026 11:40", (datetime(2026, 10, 16, 11, 40), True)),
        ("16/10/25 11:40", (datetime(2025, 10, 16, 11, 40), True)),
        ("09:15", (datetime(2026, 10, 23, 9, 15), True)),
        ("Mon 19/10", (datetime(2026, 10, 19), False)),
        ("Mon", (datetime(2026, 10, 19), False)),
        ("Yesterday", (datetime(2026, 10, 22), False)),
        ("Yesterday 18:00", (datetime(2026, 10, 22, 18, 0), True)),
        ("Thu 08/10", (datetime(2026, 10, 8), False)),
        ("24/12 10:00", (datetime(2025, 12, 24, 10, 0), True)),  # no year, not ahead of now
        ("31/02", None),
        ("soon", None),
    ],
)
def test_parse_when(text, want):
    assert parse_when(text, NOW) == want


def test_since_on_a_thread():
    thread = messages(THREAD)
    assert since(thread, "16/10 11:45", NOW) == thread[2:]
    assert since(thread, datetime(2026, 10, 16, 11, 45, 1), NOW) == thread[3:]
    assert since(thread, "17/10", NOW) == []
    assert since(thread, "16/10", NOW) == thread


def test_since_untimed_messages_follow_the_one_before():
    got = messages(
        [
            ("start of something", 551, 100),
            ("16/10 11:40", 1045, 128),
            ("Looking now", 683, 144),
            ("Back on it", 551, 172),
        ]
    )
    assert [it.text for it in since(got, "16/10 11:00", NOW)] == ["Looking now", "Back on it"]


def test_since_on_mail_by_day():
    mail = items(lines_for(MAIL), MAIL_BOX, "mails")
    assert [it.title for it in since(mail, "Mon 19/10", NOW)] == ["Ana Alder", "Farah Kim"]
    assert [it.title for it in since(mail, "Yesterday", NOW)] == ["Ana Alder"]
    with pytest.raises(ValueError):
        since(mail, "later", NOW)


def test_day_dividers_date_the_messages_after_them():
    # Centred dividers in a 700 px region from x 520: middle at 870.
    got = messages(
        [
            ("Ana Alder 16/10 11:20", 540, 100),
            ("Old news", 551, 116),
            ("Yesterday", 835, 144),
            ("Ben Brook 18:05", 540, 172),
            ("Evening all", 551, 188),
            ("18:10", 1045, 216),
            ("Hi Ben", 683, 232),
            ("Today", 850, 260),
            ("Ana Alder 09:15", 540, 288),
            ("Morning", 551, 304),
        ]
    )
    assert [(it.sender, it.time, it.text) for it in got] == [
        ("Ana Alder", "16/10 11:20", "Old news"),
        ("Ben Brook", "Yesterday 18:05", "Evening all"),
        ("", "Yesterday 18:10", "Hi Ben"),
        ("Ana Alder", "Today 09:15", "Morning"),
    ]
    assert [it.text for it in since(got, "Yesterday 18:06", NOW)] == ["Hi Ben", "Morning"]


def test_month_name_dividers_and_headers():
    got = messages(
        [
            ("16 October", 830, 100),
            ("Ana Alder 11:20", 540, 128),
            ("Monday", 840, 156),  # not a date to read into a message
            ("Ben Brook Oct 19 09:00", 540, 184),
            ("Hello", 551, 200),
        ]
    )
    assert [(it.sender, it.time) for it in got] == [
        ("Ana Alder", "16 October 11:20"),
        ("Ben Brook", "Oct 19 09:00"),
    ]
    assert got[0].text == ""


def test_consecutive_messages_under_one_header_split_by_gap():
    got = messages(
        [
            ("Ana Alder 16/10 11:20", 540, 100),
            ("First message", 551, 116),
            ("still the first", 551, 132),
            ("Second message", 551, 164),  # a blank of 20 px: a new bubble
            ("Third", 551, 196),
        ]
    )
    assert [(it.sender, it.time, it.text) for it in got] == [
        ("Ana Alder", "16/10 11:20", "First message\nstill the first"),
        ("Ana Alder", "", "Second message"),
        ("Ana Alder", "", "Third"),
    ]
    assert [it.text for it in since(got, "16/10 11:00", NOW)][1:] == ["Second message", "Third"]


def test_a_header_named_you_is_your_own_and_clocks_lose_lookalikes():
    got = messages([("Ana Alder 11:20", 540, 100), ("Hi", 551, 116), ("You g:ll", 540, 144)])
    assert [(it.sender, it.own, it.time) for it in got] == [
        ("Ana Alder", False, "11:20"),
        ("You", True, "9:11"),
    ]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("16 October", (datetime(2026, 10, 16), False)),
        ("Oct 16", (datetime(2026, 10, 16), False)),
        ("16 Oct 2025 11:40", (datetime(2025, 10, 16, 11, 40), True)),
        ("Friday, 16 October", (datetime(2026, 10, 16), False)),
        ("October 16, 2026 09:00", (datetime(2026, 10, 16, 9, 0), True)),
        ("Sept 3", (datetime(2026, 9, 3), False)),
        ("24 December", (datetime(2025, 12, 24), False)),
    ],
)
def test_parse_when_month_names(text, want):
    assert parse_when(text, NOW) == want


def test_rows_ignore_lines_outside_the_region():
    outside = Line("Ana Alder 16/10 11:20", (10, 10, 100, 12))
    assert items([outside], THREAD_BOX, "messages") == []


def test_live_teams_quirks():
    # As live Teams draws them: the avatar's initials left of the header's column on the first
    # row, an edited message's stamp, and a quote whose message began above the region.
    got = messages(
        [
            ("O Ben Brook 16/10/2026 11:45", 574, 100),
            ("Fixed it, the cache was stale", 574, 116),
            ("Thanks, merging", 551, 132),
            ("Ana Alder 16/10 11:50", 532, 164),
            ("AA Morning all", 511, 180),
            ("16/10 11:55 Edited", 1045, 212),
            ("You are welcome", 683, 228),
        ]
    )
    assert [(m.sender, m.time, m.text, m.own) for m in got] == [
        ("", "", "Thanks, merging", False),
        ("Ana Alder", "16/10 11:50", "Morning all", False),
        ("", "16/10 11:55", "You are welcome", True),
    ]
    assert got[0].cut and got[0].quote is not None and got[0].quote.sender == "Ben Brook"
    assert got[2].as_dict()["sender"] == "me"


def test_merge_searches_only_the_end_of_what_was_collected():
    early = messages([("Ana Alder 16/10 09:00", 540, 100), ("ok", 551, 116)])
    middle = [
        m
        for d in range(10, 30)
        for m in messages([(f"Ben Brook 16/10 {d}:00", 540, 100), (f"note {d}", 551, 116)])
    ]
    # A screen below whose first message is cut to a line the early one also had.
    below = messages([("ok", 551, 100), ("Ana Alder 17/10 08:00", 540, 132), ("new", 551, 148)])
    got = merge(early + middle, below)
    assert got[: len(early + middle)] == early + middle and got[-1].time == "17/10 08:00"


def test_merge_joins_the_next_message_under_a_header_scrolled_off():
    above = messages(
        [("Ana Alder 16/10 11:20", 532, 100), ("Hello", 551, 116), ("Second one", 551, 148)]
    )
    # Scrolled: Ana's header is gone, her first message cut, the second one without a sender.
    below = messages(
        [("Hello", 551, 100), ("Second one", 551, 132), ("Ben Brook 16/10 11:30", 532, 164)]
        + [("Hi all", 551, 180)]
    )
    assert [(m.sender, m.text) for m in merge(above, below)] == [
        ("Ana Alder", "Hello"),
        ("Ana Alder", "Second one"),
        ("Ben Brook", "Hi all"),
    ]
