"""The app model: definitions, calibration, identification, regions."""

from __future__ import annotations

import pathlib

import pytest
from PIL import Image, ImageDraw

from jharokha.core import apps, geometry
from jharokha.core.identify import THRESHOLD, identify
from jharokha.core.models import Frame

from .conftest import lines_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
W, H = 1200, 1200

TEAMS_PANES = [
    (0, 0, W, 62),
    (0, 63, 88, 1077),
    (89, 63, 351, 79),
    (89, 143, 351, 997),
    (440, 63, 760, 79),
    (440, 143, 760, 997),
    (0, 1140, W, 60),
]
TEAMS_TEXT = [
    ("Search (Ctrl+E)", 320, 20),
    ("Activity", 12, 90),
    ("Chat", 100, 90),
    ("Ana Alder", 100, 160),
    ("Type a message", 456, 1060),
]


def test_builtins_load_and_name_generic_anchors_only():
    cat = apps.catalog()
    assert set(cat.apps) == {"browser", "generic", "outlook", "teams", "terminal"}
    assert list(cat.apps["outlook"].views) == ["mail", "calendar"]
    assert cat.apps["teams"].views["chat"].focus == "thread"
    assert cat.apps["terminal"].views["shell"].grids == ["body"]
    assert cat.apps["outlook"].views["calendar"].regions["week"].reader == "table"
    assert "session.taskbar" in cat.session


def test_builtin_input_policies_keep_sends_behind_allow():
    cat = apps.catalog()
    teams = cat.apps["teams"].input_policy
    assert teams.newline == "shift+enter" and teams.irreversible("enter")
    assert teams.irreversible("Ctrl+Return") and not teams.irreversible("shift+enter")
    outlook = cat.apps["outlook"].input_policy
    assert outlook.irreversible("ctrl+enter") and outlook.irreversible("Alt+S")
    assert outlook.newline is None and not outlook.irreversible("enter")  # a line in a mail
    assert cat.apps["browser"].input_policy.irreversible("Return")
    assert cat.apps["terminal"].input_policy == apps.Policy()
    assert cat.knows("teams") and cat.knows("outlook.calendar") and not cat.knows("teams.mail")
    bad = "format = 1\n[app.x]\n[app.x.policy]\nirreversible = ['enter']\n[app.x.view.v]\n"
    with pytest.raises(apps.AppError, match="app.x.policy: unknown key irreversible"):
        apps.parse(bad)


def test_an_app_with_no_policy_holds_back_the_send_keys_until_it_declares_one():
    head = 'format = 1\n[app.crm]\n[[app.crm.cue]]\ntext = "CRM"\n'
    view = "[app.crm.view.main]\n"
    implicit = apps.catalog(apps.parse(head + view)).apps["crm"]
    p = implicit.input_policy
    assert implicit.policy is None and p.newline == "shift+enter"
    assert all(p.irreversible(k) for k in ("Return", "ctrl+enter", "Alt+S"))
    assert not p.irreversible("shift+enter") and not p.irreversible("tab")
    # Declared, even empty, it is the app's own: the user opted out.
    empty = apps.catalog(apps.parse(head + "[app.crm.policy]\n" + view)).apps["crm"]
    assert empty.policy == {} and empty.input_policy == apps.Policy()
    own = 'newline = "alt+enter"\nirreversible_keys = ["ctrl+s"]\n'
    mine = apps.catalog(apps.parse(head + "[app.crm.policy]\n" + own + view)).apps["crm"]
    assert mine.input_policy == apps.Policy("alt+enter", frozenset({"ctrl+s"}))
    # The screen no definition knows keeps none.
    assert apps.catalog().policies()["generic"] == apps.Policy()


def test_definitions_are_validated():
    ok = "format = 1\n[app.x]\n[app.x.view.v]\n[app.x.view.v.region.r]\nbox = [0, 0, 1, 1]\n"
    assert apps.parse(ok).apps["x"].views["v"].regions["r"].box == (0, 0, 1, 1)
    bad = [
        "[app.x]\n",  # no format
        "format = 2\n",
        "format = 1\n[app.x]\n",  # no view
        "format = 1\n[app.x]\nnope = 1\n[app.x.view.v]\n",
        'format = 1\n[app.x]\n[[app.x.cue]]\ntext = "a"\nregex = "b"\n[app.x.view.v]\n',
        "format = 1\n[app.x]\n[app.x.view.v.region.r]\nreader = 'chart'\nbox = [0, 0, 1, 1]\n",
        "format = 1\n[app.x]\n[app.x.view.v.region.r]\nfocus = true\n",  # no at or box
        "format = 1\n[app.session]\n[app.session.view.v]\n",
        "format = 1\n[calibration.teams.chat]\nregions = { a = [0, 0, 2, 1] }\n",
    ]
    for text in bad:
        with pytest.raises(apps.AppError):
            apps.parse(text)
    with pytest.raises(apps.AppError, match="no view 'nope'"):
        apps.catalog(apps.parse("format = 1\n[calibration.teams.nope]\n"))


def test_example_file_parses_and_adds_an_app():
    cat = apps.load(ROOT / "apps.example.toml")
    assert cat.apps["tickets"].views["queue"].focus == "list"
    assert cat.calibration[("teams", "chat")].focus == "thread"
    grid = cat.grid("terminal", "shell", "body", {}, W, H)
    assert (grid.pitch, grid.char_width, grid.origin) == (23, 11, (8, 4))
    assert cat.spots["session.search"] == (0.49, 0.975)
    assert cat.session_box("session.taskbar", [], W, H) == ((0, 1140, W, 60), "calibrated")
    assert cat.people.chat_of("team") == "team"
    assert cat.people.chat_of("ana") == "ana"


PEOPLE = """format = 1
[people.ana]
names = ["Ana Alder", "Ana Alder (Guest)"]
[people.bo]
names = ["Bo Lindqvist"]
[chats.team]
names = ["Ana Alder, +2"]
[chats.ana]
person = "ana"
"""


def test_a_local_file_holds_the_people_map():
    cat = apps.catalog(apps.parse(PEOPLE, "apps.toml"))
    assert cat.people.people["ana"] == ("Ana Alder", "Ana Alder (Guest)")
    assert cat.people.chats["team"] == ("Ana Alder, +2",)
    assert cat.people.chat_of("ana") == "ana"
    # A person with no chat of their own is their 1:1 chat by their id.
    assert cat.people.chat_of("bo") == "bo"
    assert cat.people.chat_of("Ana Alder") == ""


def test_no_people_map_is_an_empty_one_and_a_later_map_replaces_an_earlier_whole():
    assert apps.catalog().people == apps.People()
    assert apps.catalog(apps.parse("format = 1\n")).people == apps.People()
    later = 'format = 1\n[people.cy]\nnames = ["Cy Cole"]\n'
    cat = apps.catalog(apps.parse(PEOPLE), apps.parse(later), apps.parse("format = 1\n"))
    assert list(cat.people.people) == ["cy"]
    assert not cat.people.chats


def test_the_people_map_is_validated_naming_the_table():
    bad = {
        'format = 1\n[people.ana]\nnames = "Ana"\n': "apps.toml: people.ana.names",
        'format = 1\n[people.ana]\nname = ["Ana"]\n': "apps.toml: people.ana: unknown key name",
        "format = 1\n[chats.team]\nmembers = []\n": "apps.toml: chats.team: needs names or",
        'format = 1\n[chats.x]\nperson = "ghost"\n': "apps.toml: chats.x: person 'ghost'",
        "format = 1\npeople = 3\n": "apps.toml: people must be tables",
    }
    for text, msg in bad.items():
        with pytest.raises(apps.AppError, match=msg):
            apps.parse(text, "apps.toml")


def test_identify_teams_from_text_and_panes():
    cat = apps.catalog()
    who = identify(cat.apps.values(), lines_for(TEAMS_TEXT), TEAMS_PANES, (255, 255, 255), W, H)
    assert (who.app, who.view) == ("teams", "chat")
    assert who.confidence >= 0.9 and who.ready is True
    assert who.runner_up and who.runner_up[2] < THRESHOLD
    # The compose box's placeholder gone: not ready, though still surely Teams (a draft
    # hides it too).
    who = identify(
        cat.apps.values(), lines_for(TEAMS_TEXT[:-1]), TEAMS_PANES, (255, 255, 255), W, H
    )
    assert (who.app, who.ready) == ("teams", False) and who.confidence >= 0.9


def test_teams_holds_with_a_draft_and_pictures():
    """A draft hides the compose box's placeholder and pictures break up the panes: both are
    optional cues, so the rest still says Teams at the input guard's 0.6."""
    cat = apps.catalog()
    text = [*TEAMS_TEXT[:-1], ("Calendar", 12, 250)]
    who = identify(cat.apps.values(), lines_for(text), [(0, 0, W, H)], (255, 255, 255), W, H)
    assert (who.app, who.view) == ("teams", "chat") and who.confidence >= 0.6
    # Without Teams' own text, the optional cues alone say nothing.
    who = identify(cat.apps.values(), [], TEAMS_PANES, (255, 255, 255), W, H)
    assert who.app != "teams"


def test_teams_holds_with_its_anchor_misread_and_no_rail_labels():
    """In a wider window OCR can read the search box as `Search (Ctrl* E)` and no rail label
    at all; Teams must still score above the input guard's 0.6."""
    w = 1300
    text = [
        ("Q Search (Ctrl* E)", 425, 21),
        ("Chat", 113, 80),
        ("Ana Ald..., +2 Chat Shared Notes", 477, 80),
        ("Type a message", 488, 1063),
    ]
    cat = apps.catalog()
    who = identify(cat.apps.values(), lines_for(text), [(0, 0, w, H)], (255, 255, 255), w, H)
    assert (who.app, who.view) == ("teams", "chat") and who.confidence >= 0.8
    assert "Search (Ctrl+E)" in who.found
    # Short cues keep their punctuation: `Chat` is not found in `Ch-at`-like noise.
    who = identify(cat.apps.values(), lines_for([("C.hat", 113, 80)]), [], None, w, H)
    assert who.app != "teams"


def test_a_browser_page_with_a_side_nav_is_not_teams():
    """A page whose side nav and title strip look like Teams' rail and title bar, with a
    `Chat` button in the toolbar, an address OCR read without its scheme's first letter and
    the address box's suggestions open: it was Teams' chat at 0.5, its thread read as
    messages, and the browser 0.42 on the URLs among the suggestions."""
    w = W
    panes = [(0, 0, w, 62), (0, 63, 88, 1058)]
    page = [
        ("Request queue", 266, 12),
        ("ttps://portal.example.net", 215, 67),
        ("Chat", 1102, 69),
        ("Request queue https://portal.example.net", 206, 267),
        ("Favourites", 465, 529),
        ("Datasets", 20, 347),
        ("Settings", 21, 1098),
    ]
    cat = apps.catalog().apps.values()
    who = identify(cat, lines_for(page), panes, (255, 255, 255), w, H)
    assert (who.app, who.view) == ("browser", "page")
    assert who.facts == {"url": "ttps://portal.example.net"}
    assert who.scores["teams.chat"] < THRESHOLD
    # Teams' panes and a `Chat` alone are not Teams: its search box is.
    chat = [("Chat", 100, 90), ("Ana Alder", 100, 160)]
    who = identify(cat, lines_for(chat), TEAMS_PANES, (255, 255, 255), W, H)
    assert who.app != "teams"
    who = identify(cat, lines_for([TEAMS_TEXT[0], *chat]), TEAMS_PANES, (255, 255, 255), W, H)
    assert (who.app, who.view) == ("teams", "chat") and who.confidence >= 0.9


def test_session_spots_keep_their_pixels_when_the_window_is_resized():
    """Taskbar icons keep their distance from the left and bottom edges: a fraction measured
    at 1200 px wide would click 15 px right of the terminal's icon at 1300."""
    toml = (
        "format = 1\n[calibration.session]\nsize = [1200, 1200]\n"
        "spots = { 'taskbar.terminal' = [0.15, 0.975] }\n"
    )
    cat = apps.catalog(apps.parse(toml))
    x, y = cat.spots_for(None, 1300, 1200)["session.taskbar.terminal"]
    assert round(x * 1300) == 180 and round(y * 1200) == 1170
    x, y = cat.spots_for(None, 1200, 1100)["session.taskbar.terminal"]
    assert round(x * 1200) == 180 and round(y * 1100) == 1100 - (1200 - 1170)
    # Without a size they stay fractions of the client area, as before.
    plain = apps.catalog(apps.parse(toml.replace("size = [1200, 1200]\n", "")))
    assert plain.spots_for(None, 1300, 1200)["session.taskbar.terminal"] == (0.15, 0.975)
    with pytest.raises(apps.AppError, match="size must be"):
        apps.parse("format = 1\n[calibration.session]\nsize = [0.5, 1]\n")


def _taskbar(xs: list[int], size: tuple[int, int] = (1200, 1200)) -> Frame:
    """A session screen whose taskbar holds 24 px icons centred at xs, a running mark under
    each, as Windows draws them."""
    img = Image.new("RGBA", size, (32, 32, 32))
    d = ImageDraw.Draw(img)
    top = size[1] - 65
    d.rectangle((0, top, size[0], size[1]), fill=(238, 240, 244))
    for x in xs:
        d.rectangle((x - 12, top + 20, x + 12, top + 44), fill=(80, 90, 200))
        d.rectangle((x - 3, top + 58, x + 3, top + 60), fill=(120, 120, 120))
    return Frame(size[0], size[1], img.tobytes("raw", "BGRA"), 1, 0.0)


def test_a_spot_as_a_slot_is_the_nth_taskbar_icon_wherever_it_moved():
    """More running apps move the pinned icons closer (55 to 40 px apart), so a Teams spot
    measured as a fraction would click the icon right of Teams'."""
    from jharokha.core.elements import taskbar_icons
    from jharokha.core.session import Session, Where

    toml = (
        "format = 1\n[calibration.session]\nsize = [1200, 1200]\n"
        "spots = { 'taskbar.start' = [0.03, 0.975], 'taskbar.teams' = { slot = 3 } }\n"
    )
    cat = apps.catalog(apps.parse(toml))
    assert cat.slots == {"session.taskbar.teams": 3}
    wide = [40 + 55 * i for i in range(10)]
    tight = [33 + 40 * i for i in range(12)]
    assert [b[0] + b[2] // 2 for b in taskbar_icons(_taskbar(wide), (0, 1135, 1200, 65))] == wide
    s = Session(None, cat)  # type: ignore[arg-type]
    for xs in (wide, tight):
        f = _taskbar(xs)
        spots = s.spots(Where(f, None, [], []), None)  # type: ignore[arg-type]
        assert spots["session.taskbar.teams"][0] == xs[2]
        assert spots["session.taskbar.start"][:2] == (36, 1170)  # a point stays a point
    assert "session.taskbar.teams" not in s.spots(Where(_taskbar(wide[:2]), None, [], []), None)  # type: ignore[arg-type]
    with pytest.raises(apps.AppError, match="slot = n"):
        apps.parse("format = 1\n[calibration.session]\nspots = { 'a' = { slot = 0 } }\n")


OUTLOOK_PANES = [(0, 0, W, 59), (0, 59, 60, 1062), (60, 103, 1068, 56)]


def test_identify_outlook_mail_by_title_in_any_folder():
    """Collapsed folder pane (how it fits a session this wide): no Inbox anywhere; the
    title bar, the ribbon and the list header's folder name carry it. Ready is any folder
    name, and the calendar view sharing the title cue costs no confidence."""
    cat = apps.catalog().apps.values()
    sent = lines_for(
        [
            ("Outlook", 64, 22),
            ("New email", 116, 122),
            ("Quick steps", 771, 122),
            ("Sent Items", 148, 183),
        ]
    )
    who = identify(cat, sent, OUTLOOK_PANES, (255, 255, 255), W, H)
    assert (who.app, who.view, who.ready) == ("outlook", "mail", True)
    assert who.confidence >= 0.75 and who.runner_up and who.runner_up[0] == "outlook"
    inbox = lines_for(
        [("Outlook", 64, 22), ("New email", 116, 122), ("Focused", 148, 190), ("Other", 235, 190)]
    )
    who = identify(cat, inbox, OUTLOOK_PANES, (255, 255, 255), W, H)
    assert (who.app, who.view, who.ready) == ("outlook", "mail", True)
    loading = lines_for([("Outlook", 64, 22), ("New email", 116, 122), ("Quick steps", 771, 122)])
    who = identify(cat, loading, OUTLOOK_PANES, (255, 255, 255), W, H)
    assert (who.app, who.view, who.ready) == ("outlook", "mail", False)


def test_a_view_ready_takes_a_list():
    doc = apps.parse("""format = 1
[app.x]
name = "X"
[[app.x.cue]]
text = "X"
[app.x.view.a]
ready = ["One", "Two"]
[app.x.view.a.region.r]
box = [0, 0, 1, 1]
""")
    assert apps.catalog(doc).apps["x"].views["a"].ready == ("One", "Two")
    with pytest.raises(apps.AppError, match="ready"):
        apps.parse("""format = 1
[app.x]
name = "X"
[app.x.view.a]
ready = []
[app.x.view.a.region.r]
box = [0, 0, 1, 1]
""")


def test_identify_terminal_browser_and_generic():
    cat = apps.catalog().apps.values()
    term = lines_for([("Windows PowerShell", 14, 10), ("PS C:\\work> dir", 8, 46)])
    who = identify(cat, term, [(0, 0, W, 38), (0, 39, W, 1082)], (12, 12, 12), W, H)
    assert (who.app, who.view) == ("terminal", "shell") and who.confidence == 1.0
    url = "https://queue.example.net/requests?page=2"
    web = lines_for([("Request queue", 24, 15), (url, 140, 61), ("Other favourites", 900, 134)])
    who = identify(cat, web, [(0, 191, W, 930)], (255, 255, 255), W, H)
    assert who.app == "browser" and who.facts == {"url": url}
    # A URL printed in a terminal is not an address bar.
    who = identify(cat, lines_for([(url, 8, 400)]), [], (255, 255, 255), W, H)
    assert who.app == "generic" and "url" not in who.facts
    who = identify(cat, lines_for([("Ticket T-1034", 20, 20)]), [], (255, 255, 255), W, H)
    assert (who.app, who.view) == ("generic", "screen") and who.confidence > 0.5


def test_a_browser_with_a_web_shell_is_a_browser_not_a_terminal():
    """A private browser window (no favourites bar) with a web shell open under its page,
    dark and with `PowerShell` on a button, is the browser above the input guard's 0.6, not
    the terminal."""
    cat = apps.catalog().apps.values()
    url = "https://portal.example.net/#/resource/app"
    shown = [
        ("Request queue", 24, 15),
        (url, 140, 61),
        ("Switch to PowerShell Restart Manage files", 10, 620),
        ("Welcome to the cloud shell", 8, 660),
    ]
    panes = [(0, 0, W, 38), (0, 39, W, 1082)]
    who = identify(cat, lines_for(shown), panes, (0, 0, 0), W, H)
    assert who.app == "browser" and who.confidence >= 0.6
    who = identify(cat, lines_for(shown), [], (255, 255, 255), W, H)
    assert who.app == "browser" and who.confidence >= 0.6
    # The title in the tab strip still makes a terminal.
    term = lines_for([("PowerShell", 30, 12), ("Get-ChildItem", 8, 300)])
    who = identify(cat, term, panes, (12, 12, 12), W, H)
    assert who.app == "terminal" and who.confidence >= 0.8


def test_regions_take_the_pane_under_their_point_else_their_box():
    cat = apps.catalog()
    box, how = cat.region_box("teams", "chat", "thread", TEAMS_PANES, {}, W, H)  # type: ignore[misc]
    assert (box, how) == ((440, 143, 760, 997), "pane")
    # A pane that does not fit the box (a dialog over everything) is not taken.
    box, how = cat.region_box("teams", "chat", "thread", [(0, 0, W, H)], {}, W, H)  # type: ignore[misc]
    assert how == "box" and box == geometry.to_box((0.378, 0.122, 0.622, 0.828), W, H)
    assert cat.region_box("teams", "chat", "compose", TEAMS_PANES, {}, W, H)[1] == "box"  # type: ignore[index]
    # `to` widens a region over cells, as a calendar grid's.
    cells = [(300, 216, 40, 924), (341, 216, 165, 34), (1035, 1055, 165, 85)]
    assert apps.pane_at(cells, (0.26, 0.19), (0.97, 0.9), W, H) == (300, 216, 900, 924)
