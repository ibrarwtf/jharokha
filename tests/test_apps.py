"""The app model: definitions, calibration, identification, regions."""

from __future__ import annotations

import pathlib

import pytest

from jharokha.core import apps, geometry
from jharokha.core.identify import THRESHOLD, identify

from .conftest import lines_for

ROOT = pathlib.Path(__file__).resolve().parents[1]
W, H = 1166, 1181

TEAMS_PANES = [
    (0, 0, W, 62),
    (0, 63, 88, 1058),
    (89, 63, 351, 79),
    (89, 143, 351, 978),
    (440, 63, 726, 79),
    (440, 143, 726, 978),
    (0, 1121, W, 60),
]
TEAMS_TEXT = [
    ("Search (Ctrl+E)", 320, 20),
    ("Activity", 12, 90),
    ("Chat", 100, 90),
    ("Ana Alder", 100, 160),
    ("Type a message", 456, 1041),
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
    assert cat.session_box("session.taskbar", [], W, H) == ((0, 1122, W, 59), "calibrated")


def test_identify_teams_from_text_and_panes():
    cat = apps.catalog()
    who = identify(cat.apps.values(), lines_for(TEAMS_TEXT), TEAMS_PANES, (255, 255, 255), W, H)
    assert (who.app, who.view) == ("teams", "chat")
    assert who.confidence >= 0.9 and who.ready is True
    assert who.runner_up and who.runner_up[2] < THRESHOLD
    # The compose box gone: the chat view is not ready yet.
    who = identify(
        cat.apps.values(), lines_for(TEAMS_TEXT[:-1]), TEAMS_PANES, (255, 255, 255), W, H
    )
    assert (who.app, who.ready) == ("teams", False) and who.confidence < 0.9


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
    who = identify(cat, lines_for([("Ticket INC0012345", 20, 20)]), [], (255, 255, 255), W, H)
    assert (who.app, who.view) == ("generic", "screen") and who.confidence > 0.5


def test_regions_take_the_pane_under_their_point_else_their_box():
    cat = apps.catalog()
    box, how = cat.region_box("teams", "chat", "thread", TEAMS_PANES, {}, W, H)  # type: ignore[misc]
    assert (box, how) == ((440, 143, 726, 978), "pane")
    # A pane that does not fit the box (a dialog over everything) is not taken.
    box, how = cat.region_box("teams", "chat", "thread", [(0, 0, W, H)], {}, W, H)  # type: ignore[misc]
    assert how == "box" and box == geometry.to_box((0.378, 0.122, 0.622, 0.828), W, H)
    assert cat.region_box("teams", "chat", "compose", TEAMS_PANES, {}, W, H)[1] == "box"  # type: ignore[index]
    # `to` widens a region over cells, as a calendar grid's.
    cells = [(300, 216, 40, 905), (341, 216, 165, 34), (1001, 1036, 165, 85)]
    assert apps.pane_at(cells, (0.26, 0.19), (0.97, 0.9), W, H) == (300, 216, 866, 905)
