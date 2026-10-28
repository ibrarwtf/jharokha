"""The people map (core.people) and the canonical records a thread read becomes
(core.listread.records). Synthetic names only."""

from __future__ import annotations

from datetime import datetime

import pytest

from jharokha.core import listread, people
from jharokha.core.models import Line

TABLE = {
    "people": {
        "me": {"names": ["Pat Example", "You"]},
        "ana": {"names": ["Ana Alder", "Ana Alder (Guest)", "Ana"]},
        "bo": {"names": ["Bo Lindqvist-Ortega"]},
    },
    "chats": {
        "team": {"names": ["Ana Alder, +2", "Ana Alder, +3"], "members": ["ana", "bo", "me"]},
        "ana": {"person": "ana"},
    },
}


@pytest.fixture
def pm() -> people.People:
    return people.parse(TABLE)


def test_a_sender_maps_by_any_of_their_names_folded(pm: people.People):
    assert pm.who("Ana Alder") == "ana"
    assert pm.who("ana alder (guest)") == "ana"
    assert pm.who("Bo Lindqvist Ortega") == "bo"
    assert pm.who("Ana") == "ana"


def test_own_side_and_you_are_me(pm: people.People):
    assert pm.who("Ana Alder", own=True) == "me"
    assert pm.who("You") == "me"
    assert pm.who("me") == "me"


def test_a_cut_name_matches_the_longest_name_it_starts(pm: people.People):
    assert pm.who("Ana Ald...") == "ana"
    assert pm.who("Bo Lindq…") == "bo"
    # Too short to say who.
    assert pm.who("An...") == ""


def test_avatar_initials_in_front_are_dropped(pm: people.People):
    assert pm.who("AA Ana Alder") == "ana"


def test_a_stranger_is_nobody(pm: people.People):
    assert pm.who("Cy Unknown") == ""
    assert pm.who("") == ""


def test_a_chat_by_its_own_names_then_by_the_persons(pm: people.People):
    assert pm.chat("Ana Alder, +2") == "team"
    assert pm.chat("Ana Alder") == "ana"
    assert pm.chat("Ana Alder (Guest)") == "ana"
    assert pm.chat("Nobody") == ""


def test_parse_rejects_a_chat_of_an_unknown_person():
    with pytest.raises(ValueError, match="not in people"):
        people.parse({"people": {}, "chats": {"x": {"person": "ghost"}}})


def test_parse_rejects_names_that_are_not_a_list():
    with pytest.raises(ValueError, match="names"):
        people.parse({"people": {"x": {"names": "Ana"}}})


# Records


def _thread(rows: list[tuple[int, int, str]]) -> list[Line]:
    """Lines at (x, y) with a height of 16 px in a 600 px wide region at x=100."""
    return [Line(t, (x, y, 8 * len(t), 16)) for x, y, t in rows]


BOX = (100, 0, 600, 800)


def test_records_carry_who_day_and_time_and_inherit_under_one_header(pm: people.People):
    lines = _thread(
        [
            (380, 40, "Yesterday"),
            (130, 80, "Ana Alder 10:15"),
            (130, 100, "first"),
            (130, 150, "second, same header"),
            (470, 200, "10:20"),
            (470, 220, "mine"),
        ]
    )
    items = listread.items(lines, BOX, "messages")
    recs = listread.records(items, pm, datetime(2030, 10, 22, 12, 0))
    assert [r["who"] for r in recs] == ["ana", "ana", "me"]
    assert [r["day"] for r in recs] == ["2030-10-21"] * 3
    assert [r["time"] for r in recs] == ["10:15", "~10:15", "10:20"]
    assert recs[1]["text"] == "second, same header"
    assert recs[2]["own"] is True


def test_a_bare_clock_names_no_day(pm: people.People):
    lines = _thread([(130, 80, "Ana Alder 9:05"), (130, 100, "hello")])
    recs = listread.records(listread.items(lines, BOX, "messages"), pm, datetime(2030, 10, 22))
    assert recs[0]["day"] == ""
    assert recs[0]["time"] == "09:05"


def test_a_dated_stamp_names_its_day(pm: people.People):
    lines = _thread([(130, 80, "Ana Alder 08/10 17:32"), (130, 100, "hello")])
    recs = listread.records(listread.items(lines, BOX, "messages"), pm, datetime(2030, 10, 22))
    assert recs[0]["day"] == "2030-10-08"
    assert recs[0]["time"] == "17:32"


def test_a_weekday_stamp_is_the_most_recent_one(pm: people.People):
    lines = _thread([(130, 80, "Ana Alder Wednesday 21:40"), (130, 100, "hello")])
    # 2030-10-27 is a Tuesday: the Wednesday before is the 21st.
    recs = listread.records(listread.items(lines, BOX, "messages"), pm, datetime(2030, 10, 22))
    assert recs[0]["day"] == "2030-10-21"


def test_a_message_cut_at_the_top_has_no_who_and_no_time(pm: people.People):
    lines = _thread([(130, 10, "tail of something"), (130, 60, "Ana Alder 10:15"), (130, 80, "x")])
    recs = listread.records(listread.items(lines, BOX, "messages"), pm, datetime(2030, 10, 22))
    assert recs[0]["cut"] is True
    assert recs[0]["who"] == ""
    assert recs[0]["time"] == ""
