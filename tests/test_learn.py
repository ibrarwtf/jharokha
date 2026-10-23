"""Learning apps from the screen (core/learn.py), on generated screens drawn with PIL and
read as their ground truth: no OCR."""

from __future__ import annotations

import dataclasses
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
