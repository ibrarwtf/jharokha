from datetime import datetime

import pytest

from jharokha.core.bubbles import Bubble
from jharokha.core.listread import Item, items, merge, parse_when, since
from jharokha.core.models import Line
from jharokha.core.readers import format_items

from .conftest import lines_for

# Laid out like a Teams chat thread, with invented people and text: other people's
# headers at x 540, their text at 551, quotes at 574, your messages at 683 under a time at
# the right. Rows 16 px apart inside a message, a blank of 16 px or more between messages.
THREAD_BOX = (464, 100, 686, 500)  # x and width as Teams' thread region
THREAD = [
    ("o", 524, 100),  # an avatar read as a letter
    ("Ana Alder", 540, 100),
    ("11/10 11:20", 620, 100),
    ("Morning, is the build green?", 551, 116),
    ("I saw a red run overnight.", 551, 132),
    ("11/10 11:40", 1045, 160),
    ("Looking now", 683, 176),
    ("Ben Brook 11/10 11:45", 540, 204),
    ("Fixed it, the cache was stale", 551, 220),
    ("9", 1190, 236),  # a reaction icon
    ("Ana Alder 11/10 11:50", 540, 256),
    ("O Ben Brook 11/10/2030 11:45", 574, 272),
    ("Fixed it, the cache was stale", 574, 288),
    ("Thanks, merging", 551, 304),
    ("42", 551, 320),
    ("11/10 11:55", 1045, 348),
    ("O Ana Alder 11/10/2030 11:50", 706, 364),
    ("Thanks, merging", 706, 380),
    ("You are welcome", 683, 396),
]
FIRST = "Morning, is the build green?\nI saw a red run overnight."
NOW = datetime(2030, 10, 18, 12, 0)


def messages(rows: list[tuple[str, int, int]], box=THREAD_BOX) -> list[Item]:
    return items(lines_for(rows), box, "messages")


def plain(its: list[Item]) -> list[tuple[str, str, str, bool]]:
    return [(it.sender, it.time, it.text, it.own) for it in its]


def test_thread_alternates_senders_and_your_own():
    got = messages(THREAD)
    assert plain(got) == [
        ("Ana Alder", "11/10 11:20", FIRST, False),
        ("", "11/10 11:40", "Looking now", True),
        ("Ben Brook", "11/10 11:45", "Fixed it, the cache was stale", False),
        ("Ana Alder", "11/10 11:50", "Thanks, merging\n42", False),
        ("", "11/10 11:55", "You are welcome", True),
    ]
    assert not any(it.cut for it in got)


def test_quoted_reply_keeps_the_quote_apart():
    reply, own = messages(THREAD)[3:]
    assert reply.quote is not None
    assert (reply.quote.sender, reply.quote.time) == ("Ben Brook", "11/10/2030 11:45")
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
            ("Ana Alder 11/10 12:00", 540, 128),
            ("Agreed", 551, 144),
        ]
    )
    assert got[0].cut and got[0].sender == "" and got[0].text == "of the deploy, so we wait."
    assert (got[1].sender, got[1].text, got[1].cut) == ("Ana Alder", "Agreed", False)


def test_a_time_at_the_end_of_text_is_not_a_header():
    got = messages(
        [
            ("Ana Alder 11/10 12:00", 540, 100),
            ("Can we move the call to", 551, 116),
            ("Friday 14:30", 551, 132),
        ]
    )
    assert len(got) == 1 and got[0].text.endswith("Friday 14:30")


CHATS_BOX = (0, 100, 300, 500)
CHATS = [
    ("v Favourites", 10, 100),
    ("AA Ana Alder", 10, 124),
    ("10:12", 260, 124),
    ("Is the build green?", 40, 140),
    ("x", 290, 140),
    ("MT Mia Torres", 10, 164),
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
        ("Mia Torres", "See you at the stand", "Yesterday", "Favourites", True),
        ("Ben Broo", "Thanks!", "Mon", "Chats", True),
    ]
    assert got[0].as_dict()["section"] == "Favourites"


def test_one_row_entries_a_few_line_pitches_apart_are_entries_each():
    """Teams favourites: one row each, 28 px apart with 12 px text, and wider steps around
    the rows above and the next section; each is an entry, not one entry together."""
    rows = [
        ("Unread Channels Chats", 20, 100),
        ("Saved", 20, 145),
        ("Favourites", 20, 190),
        ("Ana Alder", 40, 220),
        ("Ben Brook, +2", 40, 248),
        ("Cy North", 40, 276),
        ("Teams and channels", 20, 320),
        ("See all your teams", 40, 350),
    ]
    got = items(lines_for(rows), (0, 90, 300, 300), "entries")
    assert [it.title for it in got if it.section == "Favourites"] == [
        "Ana Alder",
        "Ben Brook, +2",
        "Cy North",
    ]


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
    ("Mia Torres", 310, 268),
    ("Release notes", 310, 284),
    ("Mon 14/10", 732, 284),
    ("Draft attached", 310, 300),
    ("Last Week", 310, 328),
    ("Ana Alder", 310, 352),
    ("Re: Build report", 310, 368),
    ("Thu 03/10", 732, 368),
    ("0)", 790, 384),
]


def test_mail_list_with_group_headers():
    got = items(lines_for(MAIL), MAIL_BOX, "mails")
    assert [(it.kind, it.title, it.subtitle, it.time, it.text, it.section) for it in got] == [
        ("mail", "Ana Alder", "Build report", "13:23", "All green this morning", "Today"),
        # A time inside the subject, away from the right edge, is not the item's time.
        ("mail", "Ben Brook", "Lunch on Friday? 09:10", "", "Who is in", "Today"),
        ("mail", "Mia Torres", "Release notes", "Mon 14/10", "Draft attached", "This Week"),
        ("mail", "Ana Alder", "Re: Build report", "Thu 03/10", "", "Last Week"),
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
    ("Ana Alder 20/03 09:00", 540, 0),
    ("Starting the release today", 551, 16),
    ("20/03 09:05", 1045, 44),
    ("Good luck", 683, 60),
    ("Ben Brook 20/03 09:30", 540, 88),
    ("Tests are running on the", 551, 104),
    ("new runner, they take a", 551, 120),
    ("while longer than before", 551, 136),
    ("Ana Alder 20/03 10:00", 540, 164),
    ("Green now", 551, 180),
    ("20/03 10:02", 1045, 208),
    ("Ship it", 683, 224),
    ("Ben Brook 21/03 08:00", 540, 252),
    ("Shipped", 551, 268),
    ("Ana Alder 22/03 17:45", 540, 296),
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
    a = messages([("Ana Alder 11/10 11:20", 540, 100), ("Is the build green?", 551, 116)])
    b = messages([("Ana  Alder 11/10 11:20", 540, 100), ("is the bui ld green ?", 551, 116)])
    assert len(merge(a, b)) == 1


def test_merge_keeps_repeated_texts_from_different_times():
    a = messages([("Ana Alder 11/10 11:20", 540, 100), ("ok", 551, 116)])
    b = messages([("Ana Alder 11/10 11:21", 540, 100), ("ok", 551, 116)])
    assert len(merge(a, b)) == 2


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("11/10 11:40", (datetime(2030, 10, 11, 11, 40), True)),
        ("11/10/2030 11:40", (datetime(2030, 10, 11, 11, 40), True)),
        ("11/10/29 11:40", (datetime(2029, 10, 11, 11, 40), True)),
        ("09:15", (datetime(2030, 10, 18, 9, 15), True)),
        ("Mon 14/10", (datetime(2030, 10, 14), False)),
        ("Mon", (datetime(2030, 10, 14), False)),
        ("Yesterday", (datetime(2030, 10, 17), False)),
        ("Yesterday 18:00", (datetime(2030, 10, 17, 18, 0), True)),
        ("Thu 03/10", (datetime(2030, 10, 3), False)),
        ("24/12 10:00", (datetime(2029, 12, 24, 10, 0), True)),  # no year, not ahead of now
        ("31/02", None),
        ("soon", None),
    ],
)
def test_parse_when(text, want):
    assert parse_when(text, NOW) == want


def test_since_on_a_thread():
    thread = messages(THREAD)
    assert since(thread, "11/10 11:45", NOW) == thread[2:]
    assert since(thread, datetime(2030, 10, 11, 11, 45, 1), NOW) == thread[3:]
    assert since(thread, "12/10", NOW) == []
    assert since(thread, "11/10", NOW) == thread


def test_since_untimed_messages_follow_the_one_before():
    got = messages(
        [
            ("start of something", 551, 100),
            ("11/10 11:40", 1045, 128),
            ("Looking now", 683, 144),
            ("Back on it", 551, 172),
        ]
    )
    assert [it.text for it in since(got, "11/10 11:00", NOW)] == ["Looking now", "Back on it"]


def test_since_on_mail_by_day():
    mail = items(lines_for(MAIL), MAIL_BOX, "mails")
    assert [it.title for it in since(mail, "Mon 14/10", NOW)] == ["Ana Alder", "Mia Torres"]
    assert [it.title for it in since(mail, "Yesterday", NOW)] == ["Ana Alder"]
    with pytest.raises(ValueError):
        since(mail, "later", NOW)


def test_day_dividers_date_the_messages_after_them():
    # Centred dividers in a 700 px region from x 520: middle at 870.
    got = messages(
        [
            ("Ana Alder 11/10 11:20", 540, 100),
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
        ("Ana Alder", "11/10 11:20", "Old news"),
        ("Ben Brook", "Yesterday 18:05", "Evening all"),
        ("", "Yesterday 18:10", "Hi Ben"),
        ("Ana Alder", "Today 09:15", "Morning"),
    ]
    assert [it.text for it in since(got, "Yesterday 18:06", NOW)] == ["Hi Ben", "Morning"]


def test_month_name_dividers_and_headers():
    got = messages(
        [
            ("11 October", 830, 100),
            ("Ana Alder 11:20", 540, 128),
            ("Monday", 840, 156),  # not a date to read into a message
            ("Ben Brook Oct 14 09:00", 540, 184),
            ("Hello", 551, 200),
        ]
    )
    assert [(it.sender, it.time) for it in got] == [
        ("Ana Alder", "11 October 11:20"),
        ("Ben Brook", "Oct 14 09:00"),
    ]
    assert got[0].text == ""


def test_consecutive_messages_under_one_header_split_by_gap():
    got = messages(
        [
            ("Ana Alder 11/10 11:20", 540, 100),
            ("First message", 551, 116),
            ("still the first", 551, 132),
            # Teams starts the next bubble a little short of two line pitches below.
            ("Second message", 551, 162),
            ("Third", 551, 192),
        ]
    )
    assert [(it.sender, it.time, it.text) for it in got] == [
        ("Ana Alder", "11/10 11:20", "First message\nstill the first"),
        ("Ana Alder", "", "Second message"),
        ("Ana Alder", "", "Third"),
    ]
    assert [it.text for it in since(got, "11/10 11:00", NOW)][1:] == ["Second message", "Third"]


def test_an_empty_line_inside_a_message_keeps_it_whole():
    # Rows 16 px apart: an empty line puts the next paragraph 32 px below, the next bubble
    # starts at 30.
    got = messages(
        [
            ("11/10 11:40", 1045, 100),
            ("Morning all.", 683, 116),
            ("The release notes are up,", 683, 148),
            ("have a look before noon", 683, 164),
            ("Ana Alder 11/10 11:45", 540, 196),
            ("Thanks", 551, 212),
            ("Reading them now", 551, 242),
        ]
    )
    assert [(it.sender, it.time, it.text) for it in got] == [
        ("", "11/10 11:40", "Morning all.\nThe release notes are up,\nhave a look before noon"),
        ("Ana Alder", "11/10 11:45", "Thanks"),
        ("Ana Alder", "", "Reading them now"),
    ]


def test_an_indented_list_inside_a_message_keeps_it_whole():
    # A list sits a little further below the row above than a line, a few characters in;
    # the row after it starts back at the text's column. The next bubble starts at 30.
    got = messages(
        [
            ("Ana Alder 11/10 11:20", 540, 100),
            ("Steps for the release:", 551, 116),
            ("1. tag the build", 575, 144),
            ("2. push the image", 575, 160),
            ("Then tell the team", 551, 188),
            ("Another message", 551, 218),
            ("11/10 11:40", 1045, 250),
            ("Here is the plan", 683, 266),
            ("Short one", 900, 296),  # a narrower bubble starts further right
        ]
    )
    steps = "Steps for the release:\n1. tag the build\n2. push the image\nThen tell the team"
    assert [(it.sender, it.text) for it in got] == [
        ("Ana Alder", steps),
        ("Ana Alder", "Another message"),
        ("", "Here is the plan"),
        ("", "Short one"),
    ]


def test_a_row_cut_at_the_top_is_stepped_from_its_bottom():
    # Only the lower half of the row shows: its real top is a line above its bottom, two
    # pitches above the next paragraph.
    cut = Line("the end of it", (683, 101, 91, 6))
    rows = [
        ("A second paragraph", 683, 127),
        ("still the same message", 683, 143),
        ("Ana Alder 11/10 11:50", 540, 175),
        ("Seen", 551, 191),
    ]
    got = items([cut, *lines_for(rows)], THREAD_BOX, "messages")
    assert [(it.sender, it.text, it.cut) for it in got] == [
        ("", "the end of it\nA second paragraph\nstill the same message", True),
        ("Ana Alder", "Seen", False),
    ]


def test_a_stamp_of_yours_without_its_colon():
    got = messages(
        [
            ("11/10 11:52", 1045, 100),
            ("Fine", 683, 116),
            ("1155", 1094, 148),  # ends where the stamp above ends
            ("Done", 683, 164),
            ("Ana Alder 11/10 11:56", 540, 196),
            ("2024", 551, 212),  # digits on the other side are text
        ]
    )
    assert [(it.time, it.text) for it in got] == [
        ("11/10 11:52", "Fine"),
        ("11:55", "Done"),
        ("11/10 11:56", "2024"),
    ]


def test_a_read_marker_is_no_message():
    got = messages(
        [
            ("Ana Alder 11/10 11:45", 540, 100),
            ("Thanks", 551, 116),
            ("Last read", 776, 148),  # centred in the region
            ("Ben Brook 11/10 11:50", 540, 180),
            ("Seen it", 551, 196),
        ]
    )
    assert [(it.sender, it.text) for it in got] == [
        ("Ana Alder", "Thanks"),
        ("Ben Brook", "Seen it"),
    ]


def test_a_call_event_row_is_its_own_item_from_system():
    from jharokha.core.listread import SYSTEM, records
    from jharokha.core.people import People

    got = messages(
        [
            ("Ana Alder 11/10 11:20", 540, 100),
            ("Shall we call?", 551, 116),
            ("o 11/10 11:21 Ana Alder started a meeting.", 501, 144),
            ("Ben Brook 11/10 11:30", 540, 172),
            ("Joining late", 551, 188),
            ("Thu 11:52 Meeting ended 31m 2s", 541, 216),
            ("Ana Alder 11/10 12:00", 540, 244),
            ("Notes to follow", 551, 260),
        ]
    )
    assert [(it.sender, it.time, it.text) for it in got] == [
        ("Ana Alder", "11/10 11:20", "Shall we call?"),
        (SYSTEM, "11/10 11:21", "Ana Alder started a meeting."),
        ("Ben Brook", "11/10 11:30", "Joining late"),
        (SYSTEM, "Thu 11:52", "Meeting ended 31m 2s"),
        ("Ana Alder", "11/10 12:00", "Notes to follow"),
    ]
    recs = records(got, People({"ana": ("Ana Alder",)}), NOW)
    assert [(r["who"], r["day"], r["time"]) for r in recs[:2]] == [
        ("ana", "2030-10-11", "11:20"),
        (SYSTEM, "2030-10-11", "11:21"),
    ]


def test_a_header_named_you_is_your_own_and_clocks_lose_lookalikes():
    got = messages([("Ana Alder 11:20", 540, 100), ("Hi", 551, 116), ("You g:ll", 540, 144)])
    assert [(it.sender, it.own, it.time) for it in got] == [
        ("Ana Alder", False, "11:20"),
        ("You", True, "9:11"),
    ]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("11 October", (datetime(2030, 10, 11), False)),
        ("Oct 11", (datetime(2030, 10, 11), False)),
        ("11 Oct 2029 11:40", (datetime(2029, 10, 11, 11, 40), True)),
        ("Friday, 11 October", (datetime(2030, 10, 11), False)),
        ("October 11, 2030 09:00", (datetime(2030, 10, 11, 9, 0), True)),
        ("Sept 3", (datetime(2030, 9, 3), False)),
        ("24 December", (datetime(2029, 12, 24), False)),
    ],
)
def test_parse_when_month_names(text, want):
    assert parse_when(text, NOW) == want


def test_rows_ignore_lines_outside_the_region():
    outside = Line("Ana Alder 11/10 11:20", (10, 10, 100, 12))
    assert items([outside], THREAD_BOX, "messages") == []


def test_live_teams_quirks():
    # As Teams draws them: the avatar's initials left of the header's column on the first
    # row, an edited message's stamp, and a quote whose message began above the region.
    got = messages(
        [
            ("O Ben Brook 11/10/2030 11:45", 574, 100),
            ("Fixed it, the cache was stale", 574, 116),
            ("Thanks, merging", 551, 132),
            ("Ana Alder 11/10 11:50", 532, 164),
            ("AA Morning all", 511, 180),
            ("11/10 11:55 Edited", 1045, 212),
            ("You are welcome", 683, 228),
        ]
    )
    assert [(m.sender, m.time, m.text, m.own) for m in got] == [
        ("", "", "Thanks, merging", False),
        ("Ana Alder", "11/10 11:50", "Morning all", False),
        ("", "11/10 11:55", "You are welcome", True),
    ]
    assert got[0].cut and got[0].quote is not None and got[0].quote.sender == "Ben Brook"
    assert got[2].as_dict()["sender"] == "me"


def test_merge_searches_only_the_end_of_what_was_collected():
    early = messages([("Ana Alder 11/10 09:00", 540, 100), ("ok", 551, 116)])
    middle = [
        m
        for d in range(10, 30)
        for m in messages([(f"Ben Brook 11/10 {d}:00", 540, 100), (f"note {d}", 551, 116)])
    ]
    # A screen below whose first message is cut to a line the early one also had.
    below = messages([("ok", 551, 100), ("Ana Alder 12/10 08:00", 540, 132), ("new", 551, 148)])
    got = merge(early + middle, below)
    assert got[: len(early + middle)] == early + middle and got[-1].time == "12/10 08:00"


def test_merge_joins_the_next_message_under_a_header_scrolled_off():
    above = messages(
        [("Ana Alder 11/10 11:20", 532, 100), ("Hello", 551, 116), ("Second one", 551, 146)]
    )
    # Scrolled: Ana's header is gone, her first message cut, the second one without a sender.
    below = messages(
        [("Hello", 551, 100), ("Second one", 551, 130), ("Ben Brook 11/10 11:30", 532, 162)]
        + [("Hi all", 551, 180)]
    )
    assert [(m.sender, m.text) for m in merge(above, below)] == [
        ("Ana Alder", "Hello"),
        ("Ana Alder", "Second one"),
        ("Ben Brook", "Hi all"),
    ]


def test_a_message_read_with_and_without_its_day_is_kept_once():
    from jharokha.core.listread import Item, merge, same_time

    def msg(time: str, text: str = "the build is green") -> Item:
        return Item("message", (0, 0, 10, 10), (), time=time, sender="Ana Alder", text=text)

    assert same_time("Today 10:44", "10:44") and same_time("Yesterday 10:44", "10:44")
    assert not same_time("Yesterday 10:44", "Today 10:44") and not same_time("10:44", "10:45")
    got = merge([msg("09:12", "first"), msg("Today 10:44")], [msg("10:44"), msg("10:50", "x")])
    assert [(m.time, m.text) for m in got] == [
        ("09:12", "first"),
        ("Today 10:44", "the build is green"),
        ("10:50", "x"),
    ]


def test_a_read_below_continues_from_its_anchor_and_a_gap_is_reported():
    from jharokha.core.listread import Item, item_key, merged

    def msg(sender: str, time: str, text: str, cut: bool = False) -> Item:
        return Item("message", (0, 0, 9, 9), (), time=time, sender=sender, text=text, cut=cut)

    a, b, c = msg("Ana", "9:00", "ok"), msg("Ben", "9:05", "the report"), msg("Cy", "9:10", "go")
    d = msg("Di", "9:20", "done")
    assert item_key(msg("Ana", "Today 09:00", "ok")) == item_key(a)
    # Newer starts with the tail of a message cut at its top, then the anchor (Ben), then new.
    got, overlapped = merged([a, b], [msg("", "", "report", cut=True), b, c, d])
    assert overlapped and [m.sender for m in got] == ["Ana", "Ben", "Cy", "Di"]
    # Nothing shared: the list moved more than a screen between the reads.
    got, overlapped = merged([a, b], [c, d])
    assert not overlapped and len(got) == 4


# Bubbles from the pixels (core.bubbles), in the THREAD layout: others' at x 545, yours
# right-aligned past 680.
STAMP = ("11/10 11:40", 1045, 160)


def bubbled(rows: list[tuple[str, int, int]], found: list[Bubble]) -> list[Item]:
    return items(lines_for(rows), THREAD_BOX, "messages", found)


def test_rows_in_one_bubble_are_one_message_across_a_blank():
    rows = [
        ("Ana Alder 11/10 11:20", 540, 120),
        ("first paragraph", 551, 140),
        ("still the same", 551, 156),
        ("after a blank", 551, 212),  # three and a half pitches below
    ]
    assert len(messages(rows)) == 2
    got = bubbled(rows, [Bubble((545, 136, 300, 92), True)])
    assert plain(got) == [
        ("Ana Alder", "11/10 11:20", "first paragraph\nstill the same\nafter a blank", False)
    ]


def test_an_empty_bubble_under_a_stamp_is_a_picture_message():
    pictures = [Bubble((800, 180, 330, 120), False), Bubble((800, 306, 330, 120), False)]
    got = bubbled([STAMP], pictures)
    assert plain(got) == [("", "11/10 11:40", "", True), ("", "", "", True)]
    assert got[1].box == (800, 306, 330, 120) and got[1].refs == ()


def test_a_bubble_inside_a_bubble_stays_with_the_outer_message():
    rows = [
        STAMP,
        ("Card title here", 712, 196),
        ("card body text", 712, 214),
        ("Zq wlp tiny", 712, 300),  # OCR inside a picture
        ("my reply below", 700, 395),
    ]
    found = [
        Bubble((690, 180, 440, 240), True),
        Bubble((700, 190, 420, 50), False),  # a card: its rows fill it
        Bubble((700, 250, 420, 130), False),  # a picture: one row in all that height
    ]
    got = bubbled(rows, found)
    assert plain(got) == [
        ("", "11/10 11:40", "Card title here\ncard body text\nmy reply below", True)
    ]
    assert got[0].refs == (0, 1, 2, 4)


def test_your_next_bubble_ending_flush_is_no_indented_block():
    # Right-aligned: the narrower bubble starts further right, as a list item would.
    rows = [
        STAMP,
        ("a wide first bubble", 989, 176),
        ("it ends right there", 989, 192),
        ("a shorter one", 1031, 220),
    ]
    assert plain(messages(rows)) == [
        ("", "11/10 11:40", "a wide first bubble\nit ends right there", True),
        ("", "", "a shorter one", True),
    ]


def test_a_word_in_front_of_a_time_is_no_event():
    rows = [
        ("Ana Alder 11/10 11:20", 540, 120),
        ("sounds good", 551, 136),
        ("at 14:30 works for me", 551, 172),
    ]
    got = messages(rows)
    assert all(it.sender == "Ana Alder" for it in got)
    assert got[-1].text.endswith("at 14:30 works for me")
