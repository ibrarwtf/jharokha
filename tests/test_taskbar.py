"""core/taskbar.py and the placements that follow the session's taskbar: calibrations are
measured with it at the bottom; at the top it moves every app down by its height."""

from __future__ import annotations

from jharokha.core import apps, taskbar
from jharokha.core.models import Line

W, H = 1200, 1200


def line(text: str, x: int, y: int, h: int = 12) -> Line:
    return Line(text, (x, y, 8 * len(text), h))


def test_the_bar_is_where_its_date_is():
    top = [line("ENG", 922, 14), line("22'.22", 1090, 14), line("06/11/2030", 1049, 34)]
    bar = taskbar.find(top, [(0, 0, W, 59), (0, 59, W, H - 59)], W, H, 60)
    assert bar == taskbar.Bar("top", (0, 0, W, 59)) and bar.shift == 59  # type: ignore[union-attr]
    bottom = [line("22/10/2030", 1049, 1155)]
    bar = taskbar.find(bottom, [], W, H, 60)
    assert bar == taskbar.Bar("bottom", (0, H - 60, W, 60)) and bar.shift == 0  # type: ignore[union-attr]
    # A date in an app's text, at the left, mid-screen or in a big heading, is no bar.
    assert taskbar.find([line("22/10/2030", 100, 14)], [], W, H, 60) is None
    assert taskbar.find([line("22/10/2030", 1049, 600)], [], W, H, 60) is None
    assert taskbar.find([line("22/10/2030", 1000, 14, h=60)], [], W, H, 60) is None
    assert taskbar.find([], [], W, H, 60) is None  # hidden


CAL = """format = 1
[calibration.session]
size = [1200, 1200]
spots = { "taskbar.terminal" = [0.15, 0.975], "taskbar.teams" = [0.4, 0.975] }

[calibration.terminal.shell]
anchor = "PowerShell"
regions = { tabs = [0, 0, 1, 0.0333], body = [0, 0.0333, 1, 0.9167] }
"""


def test_a_top_taskbar_moves_calibrated_regions_and_spots_with_it():
    """A session taskbar at the top moves what was calibrated with it at the bottom: else the
    calibrated body takes the tab strip in and cuts a wrapped command's end off, and the
    taskbar spots click into the app at the bottom of the screen."""
    cat = apps.catalog(apps.parse(CAL))
    found = {"PowerShell": (56, 75, 72, 11)}
    top = taskbar.Bar("top", (0, 0, W, 59))
    panes = [(0, 0, W, 59), (0, 59, W, H - 59)]
    body = cat.region_box("terminal", "shell", "body", panes, found, W, H, top)
    assert body is not None and body[0] == (0, 99, W, H - 99)  # below the tabs, to the bottom
    tabs = cat.region_box("terminal", "shell", "tabs", panes, found, W, H, top)
    assert tabs is not None and tabs[0] == (0, 99 - 40, W, 40)
    x, y = cat.spots_for(None, W, H, top)["session.taskbar.terminal"][:2]
    assert (round(x * W), round(y * H)) == (180, 30)  # where the icon is, as clicked by hand
    x, y = cat.spots_for(None, W, H)["session.taskbar.terminal"][:2]
    assert (round(x * W), round(y * H)) == (180, 1170)
    # At the bottom nothing moves; a hidden bar lets the grid reach the window's bottom.
    bottom = taskbar.Bar("bottom", (0, 1140, W, 60))
    panes = [(0, 0, W, 1140), (0, 1140, W, 60)]
    body = cat.region_box("terminal", "shell", "body", panes, found, W, H, bottom)
    assert body is not None and body[0] == (0, 40, W, 1100)
    body = cat.region_box("terminal", "shell", "body", [(0, 0, W, H)], found, W, H, None)
    assert body is not None and body[0] == (0, 40, W, H - 40)


def test_a_variant_is_chosen_by_its_anchor_as_if_the_bar_were_at_the_bottom():
    toml = CAL + (
        '[calibration.teams.chat]\nanchor = "Search (Ctrl+E)"\n'
        '[calibration.teams.chat.variant."0"]\nanchor_at = [0.25, 0.02]\n'
        "regions = { thread = [0.385, 0.1, 0.615, 0.775] }\n"
        '[calibration.teams.chat.variant."+2"]\nanchor_at = [0.275, 0.07]\n'
        "regions = { thread = [0.4, 0.2, 0.6, 0.7] }\n"
    )
    cat = apps.catalog(apps.parse(toml))
    found = {"Search (Ctrl+E)": (300, 24 + 59, 118, 17)}
    top = taskbar.Bar("top", (0, 0, W, 59))
    var = cat.variant("teams", "chat", found, W, H, top)
    assert var is not None and var.name == "0"
    thread = cat.region_box("teams", "chat", "thread", [], found, W, H, top)
    assert thread is not None and thread[0][1] == round(0.1 * H) + 59
