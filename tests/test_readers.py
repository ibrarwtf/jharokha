from jharokha.core import apps
from jharokha.core.readers import format_items, items, table

from .conftest import lines_for

BOX = (0, 0, 1000, 1000)

# A request queue: a heading, a sentence, then a table whose header has no digits.
PAGE = [
    ("Open requests", 40, 20),
    ("Everything still waiting on someone", 40, 36),
    ("ID", 40, 90),
    ("Title", 190, 90),
    ("Owner", 560, 90),
    ("Status", 780, 90),
    ("INC0012345", 40, 120),
    ("Refund for order 120", 190, 120),
    ("Ana Alder", 560, 120),
    ("Open", 780, 120),
    ("REQ5550001", 40, 150),
    ("New laptop 311", 190, 150),
    ("Ben Brook", 560, 150),
    ("Waiting", 780, 150),
    ("CHG8000123", 40, 180),
    ("VPN drops 77", 190, 180),
    ("Farah Kim", 560, 180),
    ("In progress", 780, 180),
    ("Last updated a minute ago", 40, 240),
]


def test_text_reads_blocks_and_the_table_among_them():
    got = items("text", lines_for(PAGE), BOX)
    assert got[0]["text"] == "Open requests\nEverything still waiting on someone"
    t = got[1]["table"]
    assert t["header"] == ["ID", "Title", "Owner", "Status"]
    assert t["rows"][1] == ["REQ5550001", "New laptop 311", "Ben Brook", "Waiting"]
    assert len(t["rows"]) == 3
    assert got[2]["text"] == "Last updated a minute ago"
    assert got[1]["refs"][0] == "L2"


# A work week: hours down the left, days across, events drawn a few pixels below their hour.
WEEK = [
    ("Mon 12", 348, 224),
    ("Tue 13", 513, 224),
    ("Wed 14", 678, 224),
    ("8 AM", 288, 256),
    ("Standup", 352, 262),
    ("9 AM", 288, 342),
    ("Pricing review", 517, 348),
    ("Team sync", 682, 348),
    ("10 AM", 288, 428),
    ("11 AM", 288, 514),
    ("Release check", 352, 520),
]


def test_table_rows_follow_the_first_column_and_the_header_sits_above():
    t = table(list(enumerate(lines_for(WEEK))))
    assert t is not None
    assert t.header == ("", "Mon 12", "Tue 13", "Wed 14")
    assert t.rows == (
        ("8 AM", "Standup", "", ""),
        ("9 AM", "", "Pricing review", "Team sync"),
        ("10 AM", "", "", ""),
        ("11 AM", "Release check", "", ""),
    )
    # Without the 10 AM label the rows keep their pitch.
    t = table([c for c in enumerate(lines_for(WEEK)) if c[1].text != "10 AM"])
    assert t is not None and t.rows[-1] == ("11 AM", "Release check", "", "")
    assert items("table", lines_for(WEEK), BOX)[0].as_dict()["table"]["header"][1] == "Mon 12"


def test_table_without_a_header():
    rows = [("crawler", 40, 90), ("Operational", 300, 90), ("pricing", 40, 130)]
    rows += [("Degraded", 300, 130), ("quotes", 40, 170), ("Operational", 300, 170)]
    t = table(list(enumerate(lines_for(rows))))
    assert t is not None and t.header == ()
    assert t.rows[1] == ("pricing", "Degraded")


def test_form_pairs():
    rows = [
        ("Name:", 40, 20),
        ("Ana Alder", 200, 20),
        ("Ticket: INC0012345", 40, 50),
        ("Notes", 40, 80),
        ("Call back after lunch", 40, 96),
    ]
    got = items("form", lines_for(rows), BOX)
    assert [(f["label"], f["value"]) for f in got] == [
        ("Name", "Ana Alder"),
        ("Ticket", "INC0012345"),
        ("Notes", "Call back after lunch"),
    ]


def test_grid_rows_and_lists_through_the_same_entry():
    assert items("grid", lines_for([("PS C:\\> dir", 8, 46)]), BOX)[0]["text"] == "PS C:\\> dir"
    got = items("list", lines_for([("Ana Alder 11:20", 40, 20), ("Hi", 51, 36)]), BOX, "messages")
    assert got[0].sender == "Ana Alder"
    out = format_items(got)
    assert out.startswith('<screen_text source="ocr" untrusted="true">')


def test_built_in_regions_name_their_readers():
    cat = apps.catalog()
    labels = {
        f"{a}.{r}": cat.apps[a].views[v].regions[r].label
        for a, v, r in [
            ("teams", "chat", "list"),
            ("teams", "chat", "thread"),
            ("outlook", "mail", "list"),
            ("outlook", "mail", "reading"),
            ("outlook", "calendar", "week"),
            ("browser", "page", "page"),
            ("terminal", "shell", "body"),
        ]
    }
    assert labels == {
        "teams.list": "list:entries",
        "teams.thread": "list:messages",
        "outlook.list": "list:mails",
        "outlook.reading": "text",
        "outlook.week": "table",
        "browser.page": "text",
        "terminal.body": "grid",
    }
