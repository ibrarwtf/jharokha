"""Learning apps from the screen (core/learn.py), on generated screens drawn with PIL and
read as their ground truth: no OCR."""

from __future__ import annotations

import dataclasses
import json
import sys
from functools import cache
from typing import Any

import pytest
from PIL import ImageFont

from jharokha.core import apps, learn
from jharokha.core.identify import identify
from jharokha.core.models import Box, Frame, Line, Word

from .conftest import FakeSource

win = pytest.mark.skipif(sys.platform != "win32", reason="Segoe UI and Cascadia Mono")


def truth_lines(screen) -> list[Line]:
    """Each drawn text as OCR at its best would read it: words boxed by their ink."""
    from evals.render import _PIL_FONTS

    out = []
    for t in screen.texts:
        font = ImageFont.truetype(_PIL_FONTS[(t.font, t.bold)], t.px)
        top, bottom = (int(v) for v in font.getbbox("Hg")[1::2])
        words = []
        parts = t.text.split(" ")
        for i, w in enumerate(parts):
            if w:
                x = t.x + round(font.getlength(" ".join(parts[:i]) + (" " if i else "")))
                width = round(font.getlength(w))
                words.append(Word(w, (x, t.y + top, width, bottom - top)))
        if words:
            x0, x1 = words[0].box[0], words[-1].box[0] + words[-1].box[2]
            out.append(Line(t.text, (x0, t.y + top, x1 - x0, bottom - top), tuple(words)))
    return out


@cache
def scanned(kind: str, seed: int) -> learn.Scan:
    from evals import screens
    from evals.render import render

    s = getattr(screens, kind)(seed)
    frame, _ = render(s, "pil")
    return learn.scan(frame, truth_lines(s))


def others(app_id: str) -> list[apps.App]:
    return [a for d in apps.builtin() for a in d.apps.values() if a.id != app_id]


def who(app: apps.App, s: learn.Scan, hide: str | None = None):
    """Identification with app learned and the built-in app it stands for hidden."""
    pool = [a for a in others(app.id) if a.id != hide] + [app]
    return identify(pool, list(s.lines), list(s.raw), s.background, s.width, s.height)


def test_labels_and_content():
    for label in ["Search (Ctrl+E)", "New mail", "Other favourites", "Type a message", "Inbox"]:
        assert learn.is_label(label), label
    for content in [
        "the run on staging finished",
        "Chen Brook 11:37",
        "INC0O1I5S8",
        "https://pricing.example.net/x",
        "Request queue - Pricing",
        "October 9-13, 2026",
        "I paused the schedule until we know",
        "OK",
    ]:
        assert not learn.is_label(content), content


def test_definitions_round_trip_through_toml():
    for d in apps.builtin():
        for app in d.apps.values():
            text = f"format = 1\n{learn.definition_toml(app)}"
            back = apps.parse(text, "t", "built-in").apps[app.id]
            assert back == app


@win
def test_scan_finds_roles_readers_anchors_and_ready():
    s = scanned("teams", 1)
    roles = [p.role for p in s.panes]
    assert roles == ["toolbar", "nav", "header", "list", "header", "content"]
    assert [p.reader for p in s.panes][3] == "list"
    assert s.focus == 5 and s.panes[5].reader == "text"
    texts = [a.text for a in s.anchors]
    assert "Search (Ctrl+E)" in texts and "Activity" in texts and "Type a message" in texts
    # The selected chat's name repeats in the thread's header: content, not an anchor.
    assert not any(a.text in {ln.text for ln in s.panes[3].lines} for a in s.anchors)
    assert s.ready == "Type a message"
    described = learn.describe(s)
    assert described.startswith("<screen_text") and "P3 list reader=list" in described


@win
def test_proposal_parses_and_recognises_other_chats():
    p = learn.propose(scanned("teams", 1), "teams", "chat")
    doc = apps.parse(p.text, "learned")
    app = doc.apps["teams"]
    assert app.views["chat"].focus == "content" and app.views["chat"].ready == "Type a message"
    assert app.views["chat"].regions["list"].reader == "list"
    for seed in (100, 101, 102):
        got = who(app, scanned("teams", seed))
        assert (got.app, got.view) == ("teams", "chat") and got.confidence >= 0.9
    # It does not take another app's screen.
    assert who(app, scanned("outlook", 100)).app == "outlook"


@win
def test_terminal_is_a_grid_with_calibration_and_prompt():
    s = scanned("terminal", 1)
    p = learn.propose(s, "term", "shell")
    body = p.app.views["shell"].regions["content"]
    assert body.reader == "grid" and body.focus
    assert p.app.views["shell"].prompt == "^PS "
    assert p.calibration is not None
    g = p.calibration.variants[0].grids["content"]
    assert g.pitch == pytest.approx(23, abs=1)
    assert any(c.background == (12, 12, 12) for c in p.app.cues)
    # Proportional labels in a rail are not a grid.
    assert all(q.reader != "grid" for q in scanned("outlook", 1).panes)


@win
def test_calendar_cells_are_one_table_region():
    s = scanned("calendar", 1)
    p = learn.propose(s, "cal", "week")
    week = p.app.views["week"].regions["content"]
    assert week.reader == "table" and week.to is not None and week.focus
    got = apps.place(week, list(s.raw), s.width, s.height)
    assert got is not None and got[1] == "pane"
    assert got[0][0] == 280 and got[0][2] == 1166 - 280  # the hours and every day


@win
def test_names_override_roles():
    s = scanned("teams", 1)
    p = learn.propose(s, "teams", "chat", names={3: "chats", 5: "thread"})
    assert {"chats", "thread"} <= set(p.app.views["chat"].regions)
    assert p.app.views["chat"].focus == "thread"
    with pytest.raises(apps.AppError):
        learn.propose(s, "teams", "chat", names={42: "x"})
    with pytest.raises(apps.AppError):
        learn.propose(s, "session", "chat")


@win
def test_verify_finds_content_anchors_and_refine_drops_them():
    s1 = scanned("teams", 1)
    p = learn.propose(s1, "teams", "chat")
    # A name from the chat list, as a careless definition would have it.
    name = s1.panes[3].lines[0]
    at = (name.box[0] / s1.width, name.box[1] / s1.height)
    view = p.app.views["chat"]
    bad = dataclasses.replace(view, cues=(*view.cues, apps.Cue(text=(name.text,), at=(at,))))
    p = dataclasses.replace(p, app=dataclasses.replace(p.app, views={"chat": bad}))
    v = learn.verify(p.app, "chat", [scanned("teams", 2), scanned("teams", 3)], others("teams"))
    assert v.unstable == (name.text,) and not v.ok
    fixed = learn.refine(p, v)
    assert learn.verify(fixed.app, "chat", [scanned("teams", 2)], others("teams")).ok
    # The definition can be verified as text, too.
    assert learn.verify(fixed.text, "chat", [scanned("teams", 4)]).ok


@win
def test_two_scans_keep_only_anchors_both_show():
    a, b = scanned("browser", 1), scanned("browser", 2)
    one = learn.propose(a, "web", "page").anchors
    both = learn.propose([a, b], "web", "page").anchors
    assert set(both) <= set(one) and "Other favourites" in both


@win
def test_new_view_of_a_known_app():
    mail = learn.propose(scanned("outlook", 1), "mailer", "mail").app
    cal = learn.propose(scanned("calendar", 1), "mailer", "calendar", known=mail).app
    assert set(cal.views) == {"mail", "calendar"}
    # App-wide cues the calendar does not show belong to mail now.
    shown = {t for c in cal.cues for t in c.text}
    assert "Inbox" not in shown and "Search" in shown
    assert any("Inbox" in c.text for c in cal.views["mail"].cues)
    for kind, view in (("outlook", "mail"), ("calendar", "calendar")):
        got = who(cal, scanned(kind, 100), hide="outlook")
        assert (got.app, got.view) == ("mailer", view)


@win
def test_ready_is_the_last_anchor_to_arrive_while_loading():
    from evals.render import render
    from evals.sim.desktop import Desktop

    clock = [0.0]
    desk = Desktop(seed=100, front="outlook", clock=lambda: clock[0])
    desk.press("ctrl+2")
    scans = []
    for t in (0.0, 0.5, 1.5):
        clock[0] = t
        desk.tick()
        screen = desk.screen()
        frame, _ = render(screen, "pil")
        scans.append(learn.scan(frame, truth_lines(screen)))
    p = learn.propose(scans[-1], "mailer", "calendar")
    v = learn.verify(p.app, "calendar", scans, others("outlook"), loading=True)
    assert v.ready == "Today" and v.ok, v.problems
    assert learn.refine(p, v).app.views["calendar"].ready == "Today"


def _scaled(s: learn.Scan, k: float) -> learn.Scan:
    """s as a window k times the size would show it."""

    def box(b: Box) -> Box:
        return (round(b[0] * k), round(b[1] * k), round(b[2] * k), round(b[3] * k))

    lines = tuple(
        Line(ln.text, box(ln.box), tuple(Word(w.text, box(w.box)) for w in ln.words))
        for ln in s.lines
    )
    return dataclasses.replace(
        s,
        width=round(s.width * k),
        height=round(s.height * k),
        raw=tuple(box(b) for b in s.raw),
        lines=lines,
    )


@win
def test_variants_are_told_apart_by_anchor_position():
    s = scanned("terminal", 1)
    p = learn.propose(s, "term", "shell")
    # The same layout at 150%: the grid's pitch is measured on each.
    big = _scaled(s, 1.5)
    cal = learn.variants(p.app, "shell", [s, big], ["100", "150"])
    assert [v.name for v in cal.variants] == ["100", "150"]
    assert all(v.anchor == "Windows PowerShell" for v in cal.variants)
    assert cal.variants[1].grids["content"].pitch == pytest.approx(1.5 * 23, abs=1.5)
    text = apps.calibration_toml("term", "shell", cal)
    back = apps.parse(f"format = 1\n{p.definition}\n{text}", "t")
    assert len(back.calibration[("term", "shell")].variants) == 2


def test_save_replaces_the_app_and_keeps_the_rest(tmp_path):
    other = 'format = 1\n\n# mine\n[app.notes]\nname = "Notes"\n[app.notes.view.main]\n'
    app = apps.App(
        "tool",
        "Tool",
        (apps.Cue(text=("Toolbox",), at=((0.1, 0.02),), weight=2.0),),
        {
            "main": apps.View(
                "main",
                regions={"content": apps.Region("content", "grid", (0.5, 0.5), None, None, True)},
            )
        },
    )
    cal = apps.Calibration((apps.Variant("", grids={"content": apps.Grid(23.0, 11.75, (9, 12))}),))
    p = learn.Proposal(app, "main", cal)
    once = learn.save(other, p)
    twice = learn.save(once, p)
    assert once == twice
    path = tmp_path / "apps.toml"
    path.write_text(twice, encoding="utf-8")
    cat = apps.load(path)
    assert {"notes", "tool"} <= set(cat.apps) and "# mine" in twice
    assert cat.calibration[("tool", "main")].variants[0].grids["content"].pitch == 23.0
    renamed = learn.save(twice, dataclasses.replace(p, app=dataclasses.replace(app, name="T2")))
    assert renamed.count("[app.tool]") == 1 and 'name = "T2"' in renamed
    assert learn.save("", p).startswith("format = 1")


def test_content_is_never_saved_as_an_anchor():
    for content in [
        "ana.alder@example.net",
        "+44 20 7946 0958",
        "INC0012345",
        "Ticket 4821 escalated",
        "the run on staging finished but two profiles failed",
    ]:
        assert learn.content_like(content), content
    for label in ["Search (Ctrl+E)", "Type a message", "Queue Desk", "Other favourites"]:
        assert learn.content_like(label) is None, label
    # No built-in view would be refused when it is calibrated.
    for d in apps.builtin():
        for a in d.apps.values():
            for vid in a.views:
                p = learn.Proposal(a, vid, None, calibration_only=True)
                assert learn.content_anchors(p) == [], (a.id, vid)
    view = apps.View(
        "main", ready="Call +44 20 7946 0958", regions={"body": apps.Region("body", at=(0.5, 0.5))}
    )
    cues = (
        apps.Cue(text=("Queue Desk",), at=((0.01, 0.01),)),
        apps.Cue(text=("ana.alder@example.net",), at=((0.5, 0.1),)),
    )
    p = learn.Proposal(apps.App("crm", "CRM", cues, {"main": view}), "main", None)
    with pytest.raises(apps.AppError) as e:
        learn.save("", p)
    msg = str(e.value)
    assert "'ana.alder@example.net' (an email address)" in msg and "(a phone number)" in msg
    assert "Queue Desk" not in msg


def test_calibrating_a_known_view_saves_only_its_calibration():
    teams = apps.catalog().apps["teams"]
    var = apps.Variant("-1", "Search (Ctrl+E)", (0.3, 0.02), {"thread": (0.4, 0.1, 0.6, 0.8)})
    p = learn.Proposal(teams, "chat", apps.Calibration((var,)), calibration_only=True)
    out = learn.save("format = 1\n# mine\n", p)
    assert "[app.teams]" not in out and '[calibration.teams.chat.variant."-1"]' in out
    assert "# mine" in out and p.text.startswith("format = 1\n\n[calibration.teams.chat]")
    cat = apps.catalog(apps.parse(out))
    assert cat.apps["teams"].views["chat"].regions["thread"].reader == "list"  # as defined


def test_a_tooltip_is_the_text_that_appeared_by_the_icon():
    from .conftest import lines_for

    before = lines_for([("Open tickets", 16, 104), ("Badge not working", 16, 160)])
    after = lines_for(
        [
            ("Open tic", 16, 104),  # partly under the tooltip: not it
            ("Assign to me", 120, 104),
            ("Badge not working", 16, 160),
            ("far away", 600, 380),
        ]
    )
    assert learn.tooltip(before, after, (100, 60, 24, 24)) == "Assign to me"
    assert learn.tooltip(before, before, (100, 60, 24, 24)) is None
    assert learn.icon_name("Assign to me (Ctrl+M)") == "assign_to_me"
    assert learn.icon_name("Close the selected ticket") == "close_the_selected"
    assert learn.icon_name("...") is None


def test_zoom_steps_go_in_then_out_from_the_default():
    assert learn.zoom_steps("-3..2") == [0, 1, 2, -1, -2, -3]
    assert learn.zoom_steps("0..0") == [0]
    for bad in ("2..3", "-9..1", "-1..8", "x"):
        with pytest.raises(ValueError):
            learn.zoom_steps(bad)


def test_app_spots_round_trip_and_hold_only_where_an_icon_is():
    from jharokha.core.locate import Hit, Locator, Miss, locate

    text = 'format = 1\n[app.crm]\nname = "CRM"\nspots = { send = [0.5, 0.25] }\n'
    cat = apps.catalog(apps.parse(text + "[app.crm.view.main]\n"))
    crm = cat.apps["crm"]
    assert crm.spots == {"send": (0.5, 0.25)}
    assert "crm.send" in cat.spots_for("crm") and "crm.send" not in cat.spots_for("teams")
    back = apps.parse(f"format = 1\n{learn.definition_toml(crm)}").apps["crm"]
    assert back == crm
    with pytest.raises(apps.AppError):
        apps.parse("format = 1\n[app.crm]\nspots = { send = [2, 0] }\n[app.crm.view.main]\n")
    spots = {"crm.send": (320, 100), "session.taskbar.crm": (20, 390)}
    screen = (0, 0, 640, 400)
    hit = locate(Locator(spot="crm.send"), [], {}, spots, screen, None, [(310, 92, 24, 20)])
    assert isinstance(hit, Hit) and hit.center == (322, 102) and hit.how == "app spot crm.send"
    miss = locate(Locator(spot="crm.send"), [], {}, spots, screen, None, [(400, 92, 24, 20)])
    assert isinstance(miss, Miss) and "no icon of the size learned" in miss.reason
    # Learned at 40 x 40: an icon of 24 x 20 there now is another one, or zoomed.
    sized = {"crm.send": (320, 100, 40, 40)}
    miss = locate(Locator(spot="crm.send"), [], {}, sized, screen, None, [(310, 92, 24, 20)])
    assert isinstance(miss, Miss)
    taskbar = locate(Locator(spot="taskbar.crm"), [], {}, spots, screen)
    assert isinstance(taskbar, Hit) and taskbar.how == "spot taskbar.crm"


def _tickets(zoom: int = 0) -> learn.Scan:
    """The simulated ticket queue no built-in app knows, drawn with PIL, at a zoom step."""
    from evals.render import render
    from evals.sim.desktop import Desktop

    desk = Desktop(100, front="tickets", tickets=True)
    desk.tickets.zoom = zoom
    screen = desk.screen()
    frame, _ = render(screen, "pil")
    return learn.scan(frame, truth_lines(screen))


@win
def test_icons_become_spots_and_verify_drops_one_no_longer_there():
    s = _tickets()
    assert len(s.icons) == 5 and {i.pane for i in s.icons} == {1}
    assert "I4 P1 [188, 71]" in learn.describe(s)
    names = {3: "queue", 4: "detail"}
    p = learn.propose(s, "tickets", "queue", names, icons={1: "refresh", 3: "reply"})
    assert set(p.app.spots) == {"refresh", "reply"} and "spots = { refresh = [" in p.text
    assert apps.parse(p.text).apps["tickets"].spots == p.app.spots
    for bad in ({9: "x"}, {0: "a", 1: "a"}, {0: "not a name"}):
        with pytest.raises(apps.AppError):
            learn.propose(s, "tickets", "queue", icons=bad)
    # Zoomed out, the toolbar's icons sit further left: none is where reply was.
    v = learn.verify(p.app, "queue", [_tickets(-3)], others("tickets"))
    assert "reply" in v.lost_spots and "no icon of its size at spot 'reply'" in "".join(v.problems)
    assert "reply" not in learn.refine(p, v).app.spots


@win
def test_zoom_variants_of_a_new_app_are_told_apart():
    scans = [_tickets(0), _tickets(-1), _tickets(2)]
    p = learn.propose(scans[0], "tickets", "queue", {3: "queue", 4: "detail"})
    cal = learn.variants(p.app, "queue", scans, ["+0", "-1", "+2"])
    widths = [v.regions["queue"][2] for v in cal.variants]
    assert widths[1] < widths[0] < widths[2]  # the list's width follows the zoom
    assert all(v.anchor == "Queue Desk" for v in cal.variants)
    assert [learn.matching(cal, s).name for s in scans] == ["+0", "-1", "+2"]  # type: ignore[union-attr]


async def test_learn_input_steps_need_allow_input_and_save_shows_the_change_first(source, tmp_path):
    from mcp import Client

    from jharokha.server import build_server

    path = tmp_path / "apps.toml"
    async with Client(build_server(source, apps_path=path)) as c:
        await c.call_tool("jharokha_learn", {"step": "scan", "app": "mine"})
        for step in ("name_icons", "zoom"):
            r = await c.call_tool("jharokha_learn", {"step": step})
            assert r.is_error and r.content[0].text.startswith("input_disabled")  # type: ignore[union-attr]
        r = await c.call_tool("jharokha_learn", {"step": "propose"})
        assert not r.is_error
        r = await c.call_tool("jharokha_learn", {"step": "save"})
        assert r.content[0].text.startswith("not saved yet") and not path.exists()  # type: ignore[union-attr]
        r = await c.call_tool("jharokha_learn", {"step": "save", "confirm": True})
        assert not r.is_error and "[app.mine]" in path.read_text(encoding="utf-8")
    assert not any(t.startswith(("pressed", "hovered")) for t, _, _ in source.items)


# On the simulated session, with the real OCR


@pytest.mark.windows
async def test_onboarding_a_new_app_through_the_tools():
    from evals.learn_eval import onboard

    o = await onboard(codec=False)
    assert all(o.goals.values()), o.goals
    # Before the save only the pointer moved (onto icons and away); nothing was sent.
    assert o.before_save == ["hover"] and o.sent == 0
    assert o.calls == 12


async def _sim(tmp_path, catalog=None):  # noqa: ANN001, ANN202
    from mcp import Client

    from evals.sim import Desktop, SimSource
    from jharokha.server import build_server

    src = SimSource(Desktop(100, front="tickets", tickets=True))
    cat = catalog or src.catalog()
    return src, Client(build_server(src, True, cat, apps_path=tmp_path / "apps.toml"))


async def _call(c: Any, name: str, **args: Any) -> tuple[bool, str]:
    r = await c.call_tool(name, args)
    return bool(r.is_error), "\n".join(x.text for x in r.content)


@pytest.mark.windows
async def test_zoom_and_tooltips_refuse_another_app_and_a_known_view_is_calibrated(tmp_path):
    src, client = await _sim(tmp_path)
    async with client as c:
        await _call(c, "jharokha_learn", step="scan", app="tickets", view="queue")
        assert not (await _call(c, "jharokha_learn", step="propose", names={"P3": "queue"}))[0]
        src.desk.switch("teams")
        for step in ("zoom", "name_icons"):
            err, out = await _call(c, "jharokha_learn", step=step)
            assert err and out.startswith("wrong_app"), out
        assert src.desk.tickets.zoom == 0
        assert not any(e.kind == "hover" for e in src.desk.events)
        # Teams is known: its view is calibrated at the zoom it shows, the definition kept.
        err, out = await _call(
            c, "jharokha_learn", step="variant", app="teams", view="chat", variant="default"
        )
        assert not err and out.startswith("variant default of teams.chat kept"), out
        err, out = await _call(c, "jharokha_learn", step="variant", variant="again")
        assert err and "laid out as variant default" in out
        err, out = await _call(c, "jharokha_learn", step="save", confirm=True)
        assert out.startswith("saved the calibration of teams.chat"), out
        status = json.loads((await _call(c, "jharokha_status"))[1])
        assert status["app"] == "teams" and status["warnings"] == []
    saved = (tmp_path / "apps.toml").read_text(encoding="utf-8")
    assert '[calibration.teams.chat.variant."default"]' in saved and "[app.teams]" not in saved


@pytest.mark.windows
async def test_an_app_spot_is_clicked_only_while_its_app_is_in_front(tmp_path):
    from evals.screens import H, W

    defined = (
        'format = 1\n[app.tickets]\nname = "Queue Desk"\n'
        f"spots = {{ assign = [{108 / W:.4f}, {71 / H:.4f}] }}\n"  # its toolbar's third icon
        '[[app.tickets.cue]]\ntext = "Queue Desk"\nat = [0.014, 0.01]\nweight = 2\n'
        "[app.tickets.view.queue]\n[app.tickets.view.queue.region.detail]\n"
        "at = [0.68, 0.5]\nfocus = true\n"
    )
    src, client = await _sim(tmp_path, apps.catalog(apps.parse(defined)))
    async with client as c:
        err, out = await _call(
            c, "jharokha_act", op="click", target={"spot": "tickets.assign"}, expect="tickets"
        )
        assert not err and "app spot tickets.assign" in out, out
        t = src.desk.tickets
        assert t.tickets[t.open].assigned == "You"
        apps_list = json.loads((await _call(c, "jharokha_apps"))[1])
        assert next(a for a in apps_list["apps"] if a["app"] == "tickets")["spots"] == [
            "tickets.assign"
        ]
        src.desk.switch("teams")
        err, out = await _call(c, "jharokha_act", op="click", target={"spot": "tickets.assign"})
        assert err and out.startswith("not_found") and "count only while" in out


# The flow as an agent runs it: jharokha_learn through the MCP server.


@dataclasses.dataclass
class Shown(FakeSource):
    """A generated screen, read as its ground truth."""

    screen: Any = None

    def frame(self, w):  # noqa: ANN001, ANN201
        from evals.render import render

        self.frame_id += 1
        f, _ = render(self.screen, "pil")
        return Frame(f.width, f.height, f.bgra, self.frame_id, f.ts)

    def ocr(self, frame, region):  # noqa: ANN001, ANN201
        self.ocr_calls += 1
        lines = truth_lines(self.screen)
        if region is None:
            return lines
        x, y, w, h = region
        return [ln for ln in lines if x <= ln.box[0] < x + w and y <= ln.box[1] < y + h]


@win
async def test_learn_tool_scans_proposes_verifies_and_saves_an_app(tmp_path):
    import json

    from mcp import Client

    from evals import screens
    from jharokha.server import build_server

    builtin = apps.catalog()
    # Teams as an app jharokha does not know yet.
    unknown = dataclasses.replace(
        builtin, apps={k: v for k, v in builtin.apps.items() if k != "teams"}
    )
    src = Shown(screen=screens.teams(1))
    path = tmp_path / "apps.toml"

    async def call(c: Client, name: str, **args: Any) -> str:
        r = await c.call_tool(name, args)
        out = r.content[0].text  # type: ignore[union-attr]
        assert not r.is_error, out
        return out

    async with Client(build_server(src, catalog=unknown, apps_path=path)) as c:
        assert json.loads(await call(c, "jharokha_status"))["app"] != "teams"
        r = await call(c, "jharokha_learn", step="scan", app="teams", view="chat")
        assert r.startswith("scan 1 of teams") and "P3 list reader=list" in r
        src.screen = screens.teams(2)  # another chat open
        assert (await call(c, "jharokha_learn")).startswith("scan 2 of teams")
        r = await call(c, "jharokha_learn", step="propose", names={"P3": "chats", "P5": "thread"})
        assert r.startswith("proposal: teams.chat from 2 scans; regions ") and " ok," in r
        assert "teams.thread=P5 text focus" in r and "[app.teams]" in r
        src.screen = screens.teams(3)
        r = await call(c, "jharokha_learn", step="verify")
        assert r.startswith("verified: teams.chat from 3 scans") and "problems:\n(none)" in r
        assert not path.exists()  # nothing written before save
        r = await call(c, "jharokha_learn", step="save")
        assert r.startswith("not saved yet") and "+[app.teams]" in r and not path.exists()
        r = await call(c, "jharokha_learn", step="save", confirm=True)
        assert r.startswith(f"saved teams.chat to {path}")
        s = json.loads(await call(c, "jharokha_status"))
        assert (s["app"], s["view"]) == ("teams", "chat") and s["confidence"] >= 0.9
        r = await call(c, "jharokha_read", region="teams.chats", format="lines")
        assert "region=teams.chats" in r
        listed = json.loads(await call(c, "jharokha_apps"))
        assert next(a for a in listed["apps"] if a["app"] == "teams")["source"] == "local"
    assert "[app.teams]" in path.read_text(encoding="utf-8")
    async with Client(build_server(src, catalog=unknown)) as c:
        await call(c, "jharokha_learn", step="propose", app="teams", view="chat")
        r = await c.call_tool("jharokha_learn", {"step": "save"})
        assert r.is_error and "no apps.toml" in r.content[0].text  # type: ignore[union-attr]
