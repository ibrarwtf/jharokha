"""The app model: definitions, calibration, the old layouts file, identification, regions."""

from __future__ import annotations

import pathlib

import pytest

from jharokha.core import apps, layout
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
    assert cat.apps["browser"].input_policy.irreversible("Return")
    assert cat.apps["terminal"].input_policy == apps.Policy()
    assert cat.knows("teams") and cat.knows("outlook.calendar") and not cat.knows("teams.mail")
    bad = "format = 1\n[app.x]\n[app.x.policy]\nirreversible = ['enter']\n[app.x.view.v]\n"
    with pytest.raises(apps.AppError, match="app.x.policy: unknown key irreversible"):
        apps.parse(bad)


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


def test_old_layouts_file_becomes_calibration_of_the_builtin_apps():
    old = layout.parse(
        "[vdi]\ntaskbar = [0, 0.949, 1, 0.051]\n"
        '["teams@0"]\nanchor = "Search (Ctrl+E)"\nfocus = "thread"\nanchor_at = [0.25, 0.02]\n'
        "thread = [0.39, 0.1, 0.61, 0.77]\nextra = [0, 0, 0.1, 0.1]\n"
        '["teams@-3"]\nanchor = "Search (Ctrl+E)"\nfocus = "thread"\nanchor_at = [0.34, 0.017]\n'
        "thread = [0.29, 0.08, 0.71, 0.8]\nextra = [0, 0, 0.1, 0.1]\n"
        '[terminal]\nanchor = "PowerShell"\nfocus = "body"\ngrid = "body"\n'
        "body = [0, 0.034, 1, 0.915]\n"
        '[mine]\nanchor = "Build"\nall = [0, 0, 1, 1]\n'
    )
    cat = apps.load(None, old)
    assert cat.session_cal == {"vdi.taskbar": (0, 0.949, 1, 0.051)}
    assert [v.name for v in cat.calibration[("teams", "chat")].variants] == ["0", "-3"]
    assert "extra" in cat.apps["teams"].views["chat"].regions  # measured, not defined
    search = cat.apps["teams"].cues[0]
    assert search.at == ((0.25, 0.02), (0.34, 0.017))  # calibration says where it belongs
    assert cat.apps["mine"].views["main"].cues[0].text == ("Build",)
    found = {"Search (Ctrl+E)": (400, 20, 100, 14)}  # x 0.343: the -3 variant
    box, how = cat.region_box("teams", "chat", "thread", [], found, W, H)  # type: ignore[misc]
    assert how == "calibrated -3" and box[0] == round(0.29 * W)
    # As before, a calibration with an anchor applies while its anchor is on screen.
    shell = {"PowerShell": (14, 10, 120, 14)}
    assert cat.region_box("terminal", "shell", "body", [], shell, W, H)[1] == "calibrated"  # type: ignore[index]
    assert cat.region_box("terminal", "shell", "body", [], {}, W, H)[1] == "box"  # type: ignore[index]
    # apps.toml wins over the old file.
    newer = apps.parse(
        "format = 1\n[calibration.terminal.shell]\nregions = { body = [0, 0.5, 1, 0.5] }\n"
    )
    known = {a.id: a for d in apps.builtin() for a in d.apps.values()}
    cat = apps.catalog(apps.from_layouts(old, known), newer)
    assert cat.region_box("terminal", "shell", "body", [], {}, W, H)[0][1] == round(0.5 * H)  # type: ignore[index]


def test_user_layouts_toml_still_loads_if_present():
    path = ROOT / "layouts.toml"
    if not path.exists():
        pytest.skip("no local layouts.toml")
    cat = apps.load(None, layout.load(path))
    assert all(app in cat.apps for app, _ in cat.calibration)


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
    assert how == "box" and box == layout.to_box((0.378, 0.122, 0.622, 0.828), W, H)
    assert cat.region_box("teams", "chat", "compose", TEAMS_PANES, {}, W, H)[1] == "box"  # type: ignore[index]
    # `to` widens a region over cells, as a calendar grid's.
    cells = [(300, 216, 40, 905), (341, 216, 165, 34), (1001, 1036, 165, 85)]
    assert apps.pane_at(cells, (0.26, 0.19), (0.97, 0.9), W, H) == (300, 216, 866, 905)


def test_measured_calibration_round_trips_and_replaces_its_view():
    cal = apps.Calibration(
        (
            apps.Variant("-1", "Search (Ctrl+E)", (0.3, 0.02), {"thread": (0.4, 0.1, 0.6, 0.8)}),
            apps.Variant(
                "+1",
                "Search (Ctrl+E)",
                (0.25, 0.02),
                {"thread": (0.38, 0.1, 0.62, 0.8)},
                {"thread": apps.Grid(23.0, 11.0, (8, 4))},
            ),
        ),
        focus="thread",
    )
    block = apps.calibration_toml("teams", "chat", cal)
    old = 'format = 1\n[calibration.teams.chat]\nfocus = "list"\n[calibration.terminal.shell]\n'
    text = apps.replace_calibration(old, "teams", "chat", block)
    doc = apps.parse(text)
    assert doc.calibration[("teams", "chat")] == cal
    assert ("terminal", "shell") in doc.calibration
    assert apps.replace_calibration("", "x", "main", block).startswith("format = 1\n")
