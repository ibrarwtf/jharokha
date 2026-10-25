"""Actions as data: the definitions, locators and the runner on a scripted executor (fast, no
OCR), then the built-in actions on the simulated session with the real Windows OCR,
including the two incident replays, now refused."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pytest

from jharokha.core import actions, listread
from jharokha.core.actions import ActionError, Runner, Snapshot
from jharokha.core.guard import Policy
from jharokha.core.locate import Hit, Miss, RegionInfo, inside, locate, locator, unwrapped
from jharokha.core.models import Box, Line

from .conftest import lines_for

SCREEN: Box = (0, 0, 640, 400)


# Definitions


def test_builtin_actions_parse_and_declare_their_risk():
    acts = actions.builtin()
    assert set(acts) == {
        "terminal.run",
        "terminal.put_file",
        "teams.open_chat",
        "teams.messages",
        "teams.draft",
        "outlook.unread",
        "outlook.open",
        "browser.goto",
        "browser.click_sequence",
    }
    assert acts["terminal.run"].risk == "irreversible"
    assert acts["teams.draft"].risk == "reversible" and acts["teams.draft"].newline is None
    assert acts["teams.draft"].steps[-1].params["newline"] == "shift+enter"
    assert acts["outlook.unread"].max_risk == "reversible"
    assert acts["browser.goto"].risk == "irreversible"  # Enter in a browser, as its policy says
    d = actions.describe(acts["teams.draft"])
    assert d["args"]["chat"] == {"type": "text", "required": True}


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ('[[action.x.a.step]]\ndo = "press"\nkeys = "enter"', "declares its risk"),
        ('[[action.x.a.step]]\ndo = "type"\ntext = "{who}"\nrisk = "read"', "'{who}' is not an"),
        ('[[action.x.a.step]]\ndo = "jump"', "do must be one of"),
        ('[[action.x.a.step]]\ndo = "focus"\nnope = 1', "unknown key nope"),
        ('[[action.x.a.step]]\ndo = "click"\ntarget = { near = "x" }\nrisk = "read"', "locator"),
        ('[[action.x.a.step]]\ndo = "type"\ntext = "a"\nrisk = "maybe"', "risk must be"),
        ('[action.x.a]\nsummary = "no steps"', "needs [["),
    ],
)
def test_definitions_are_checked_when_loaded(body: str, error: str):
    with pytest.raises(ActionError, match=error.replace("[", r"\[").replace("{", r"\{")):
        actions.parse("format = 1\n" + body)


def test_arguments_are_checked_and_filled_literally_into_a_regex():
    goto = actions.builtin()["browser.goto"]
    assert actions.check_args(goto, {"url": "status.example.net"}) == {"url": "status.example.net"}
    with pytest.raises(ActionError, match="does not match"):
        actions.check_args(goto, {"url": "Get-ChildItem"})
    with pytest.raises(ActionError, match="takes no argument"):
        actions.check_args(goto, {"url": "a.example.net", "x": 1})
    assert actions.fill({"regex": r"{f}\s+(\d+)", "text": "{f}"}, {"f": "a.b"}) == {
        "regex": r"a\.b\s+(\d+)",
        "text": "a.b",
    }
    assert actions.fill("{n}", {"n": 3}) == 3


def test_apps_toml_holds_local_actions_beside_app_definitions():
    text = (
        'format = 1\n[app.x]\nname = "X"\n[app.x.view.main]\n'
        '[action.x.hello]\nsummary = "Say hello"\nmax_risk = "reversible"\n'
        '[[action.x.hello.step]]\ndo = "type"\ntext = "hello"\nrisk = "reversible"\n'
    )
    acts = actions.load(text)
    assert acts["x.hello"].source == "local" and "teams.draft" in acts
    from jharokha.core import apps

    assert "x" in apps.catalog(apps.parse(text)).apps


def test_before_a_time_by_the_list_reader():
    now = datetime(2026, 10, 23, 9, 41)
    thread = lines_for([("Ana Silva  7:05", 10, 30), ("first", 10, 52), ("You  7:12", 10, 100)])
    its = listread.items(thread, (0, 0, 640, 400), "messages")
    assert listread.before(its, "7:05", now) and not listread.before(its, "7:04", now)
    assert [m.time for m in listread.since(its, "7:06", now)] == ["7:12"]
    assert listread.after(its, "7:11", now) and not listread.after(its, "7:12", now)
    assert [m.time for m in listread.upto(its, "7:11", now)] == ["7:05"]


def test_a_day_without_a_clock_keeps_all_of_its_messages():
    now = datetime(2026, 10, 24, 12, 0)
    thread = lines_for(
        [("Ana Silva  25/09 10:47", 10, 30), ("hi", 10, 52), ("Ana Silva  01/10 15:02", 10, 100)]
    )
    its = listread.items(thread, (0, 0, 640, 400), "messages")
    assert [m.time for m in listread.upto(its, "27/09", now)] == ["25/09 10:47"]
    assert listread.after(its, "27/09", now) and not listread.after(its, "01/10", now)
    # Scrolled back to a message during the day, the start of that day is not yet reached.
    assert not listread.before(its[1:], "01/10", now)
    assert listread.before(its, "26/09", now) and listread.before(its[1:], "01/10 15:02", now)


# Locators


def screen_lines():
    return lines_for(
        [
            ("Inbox 16", 20, 40),
            ("Drafts", 20, 70),
            ("To", 200, 40),
            ("Subject", 200, 80),
            ("Ana Silva", 400, 40),
            ("Run report 120", 400, 56),
            ("Ben Okoro", 400, 100),
            ("Invite 381", 400, 116),
        ]
    )


def test_locators_resolve_to_a_box_or_a_clear_miss():
    lines = screen_lines()
    regions = {"list": (390, 30, 240, 120), "folders": (10, 30, 150, 80)}
    spots = {"session.taskbar.teams": (300, 380)}

    def at(**t: object) -> Hit | Miss:
        return locate(locator(t), lines, regions, spots, SCREEN)

    hit = at(text="Drafts")
    assert isinstance(hit, Hit) and hit.center == (41, 76)
    assert isinstance(at(text="Inbox", region="list"), Miss)
    right = at(right_of="To")
    assert isinstance(right, Hit) and right.text == "Ana"
    below = at(below="To")
    assert isinstance(below, Hit) and below.text == "Subject"
    field_ = at(right_of="Subject")  # nothing to its right on its row: the empty field
    assert isinstance(field_, Hit) and field_.how.startswith("empty field")
    assert isinstance(at(spot="taskbar.teams"), Hit)
    second = at(region="list", nth=2)
    assert isinstance(second, Hit) and second.text == "Ben Okoro\nInvite 381"
    last = at(region="list", nth=-1)
    assert isinstance(last, Hit) and last.text == second.text
    centre = at(region="folders")
    assert isinstance(centre, Hit) and centre.center == (85, 70)
    miss = at(text="Archive")
    assert isinstance(miss, Miss) and "not on screen" in miss.reason and miss.candidates
    assert "regions now: list, folders" in at(region="reading").reason  # type: ignore[union-attr]
    with pytest.raises(ValueError, match="spot stands alone"):
        locator({"spot": "x", "text": "y"})


def test_a_brief_result_is_its_answer_and_a_failure_keeps_its_steps():
    r = actions.Result("x", fields={"n": 1}, steps=[actions.StepReport("1", "read", "read", 5)])
    assert "steps_done" not in r.to_dict(brief=True) and "steps_done" in r.to_dict()
    r.ok = False
    assert r.to_dict(brief=True)["steps_done"] == [
        {"step": "1", "do": "read", "risk": "read", "ms": 5}
    ]


def test_the_nth_item_of_a_region_is_its_readers_item():
    thread = lines_for(
        [
            ("cut off at the top", 10, 0),
            ("Ana Silva  7:05", 10, 30),
            ("first", 10, 52),
            ("You  7:12", 10, 100),
            ("second", 10, 122),
            ("and more", 10, 144),
        ]
    )
    regions = {"thread": (0, 0, 640, 200)}
    info = {"thread": RegionInfo("list", "messages")}
    hit = locate(locator({"region": "thread", "nth": -1}), thread, regions, {}, SCREEN, info)
    assert isinstance(hit, Hit) and hit.text == "You\nsecond\nand more"
    hit = locate(locator({"region": "thread", "nth": 2}), thread, regions, {}, SCREEN, info)
    assert isinstance(hit, Hit) and hit.text == "Ana Silva\nfirst"


# The runner, on a scripted executor


@dataclass
class Scripted:
    """An executor over a fixed screen per app; input is recorded, and a click on a spot
    brings that app forward."""

    screens: dict[str, list[tuple[str, int, int]]]
    app: str
    regions: dict[str, dict[str, Box]] = field(default_factory=dict)
    spots: dict[str, tuple[int, int]] = field(default_factory=dict)
    inputs: list[str] = field(default_factory=list)
    t: float = 0.0
    fid: int = 0

    def read(self) -> Snapshot:
        return Snapshot(
            self.fid,
            (640, 400),
            lines_for(self.screens.get(self.app, [])),
            self.app,
            "chat" if self.app == "teams" else "main",
            None,
            self.regions.get(self.app, {}),
            self.spots,
        )

    def zoom(self, box: Box) -> list[Line]:
        return inside(self.read().lines, box)

    def reread(self, boxes: list[Box]) -> list[str]:
        return []

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        self.inputs.append(f"click {x},{y}")
        self.fid += 1
        for k, (sx, sy) in self.spots.items():
            if (sx, sy) == (x, y):
                self.app = k.rsplit(".", 1)[1]

    def type(self, text: str) -> None:
        assert "\n" not in text
        self.inputs.append(f"type {text}")
        self.fid += 1

    def press(self, keys: str) -> None:
        self.inputs.append(f"press {keys}")
        self.fid += 1

    def scroll(self, x: int, y: int, ticks: int) -> None:
        self.inputs.append(f"scroll {ticks}")

    def settle(self, box=None) -> None:  # noqa: ANN001
        pass

    def wait(self, seconds: float) -> None:
        self.t += seconds

    def now(self) -> float:
        return self.t


def one(text: str, app: str = "chat") -> actions.Action:
    return next(iter(actions.parse(f"format = 1\n[action.{app}.a]\n{text}").values()))


def test_a_step_riskier_than_the_action_allows_is_refused_before_any_input():
    a = one(
        'max_risk = "reversible"\n'
        '[[action.chat.a.step]]\ndo = "type"\ntext = "hi"\nrisk = "reversible"\n'
        '[[action.chat.a.step]]\ndo = "press"\nkeys = "enter"\nrisk = "irreversible"'
    )
    ex = Scripted({"chat": [("Type a message", 10, 300)]}, "chat")
    r = Runner(ex).run(a, {})
    assert not r.ok and ex.inputs == [] and r.failed["code"] == "irreversible"  # type: ignore[index]
    assert r.safety == [{"kind": "irreversible", "step": "2:press", "detail": r.failed["reason"]}]  # type: ignore[index]
    # A call's allow caps it the same way, below the action's own.
    draft = actions.builtin()["teams.draft"]
    r = Runner(ex, allow="read").run(draft, {"chat": "Ana", "text": "x"})
    assert not r.ok and r.safety[0]["kind"] == "irreversible" and ex.inputs == []
    assert "allow='reversible'" in r.failed["reason"]  # type: ignore[index]


def test_a_key_the_apps_policy_calls_irreversible_makes_its_step_irreversible():
    a = one(
        'max_risk = "irreversible"\n'
        '[[action.chat.a.step]]\ndo = "press"\nkeys = "Return"\nrisk = "reversible"'
    )
    ex = Scripted({"chat": []}, "chat")
    policies = {"chat": Policy(irreversible_keys=frozenset({"enter"}))}
    r = Runner(ex, allow="reversible", policies=policies).run(a, {})
    assert not r.ok and r.failed["code"] == "irreversible" and ex.inputs == []  # type: ignore[index]
    assert "Return is irreversible in chat" in r.failed["reason"]  # type: ignore[index]
    assert Runner(ex, allow="irreversible", policies=policies).run(a, {}).ok
    assert ex.inputs == ["press Return"]


def test_a_newline_that_is_enter_makes_typing_irreversible():
    a = one(
        'max_risk = "reversible"\nnewline = "enter"\nargs.text = { type = "text" }\n'
        '[[action.chat.a.step]]\ndo = "type"\ntext = "{text}"\nrisk = "reversible"'
    )
    ex = Scripted({"chat": []}, "chat")
    assert Runner(ex).run(a, {"text": "one line"}).ok
    r = Runner(ex).run(a, {"text": "line one\nline two"})
    assert not r.ok and r.safety[0]["kind"] == "irreversible"
    assert ex.inputs == ["type one line"]
    b = one(
        'args.text = { type = "text" }\n'
        '[[action.chat.a.step]]\ndo = "type"\ntext = "{text}"\nrisk = "reversible"'
    )
    r = Runner(ex).run(b, {"text": "a\nb"})  # no newline policy: forbidden at run time
    assert not r.ok and "newline" in r.failed["reason"]  # type: ignore[index]


def test_dry_run_returns_the_plan_without_input():
    ex = Scripted({}, "teams")
    r = Runner(ex).run(actions.builtin()["teams.draft"], {"chat": "Ana", "text": "a\nb"}, True)
    assert r.ok and r.dry_run and ex.inputs == []
    assert [p["do"] for p in r.plan or []] == ["focus", "click", "click", "type"]
    assert r.plan[3]["text"] == "a\nb" and r.plan[3]["newline"] == "shift+enter"  # type: ignore[index]
    seq = Runner(ex).run(
        actions.builtin()["browser.click_sequence"], {"buttons": ["A", "B"], "times": 2}, True
    )
    assert seq.plan[1]["steps"][0]["steps"][0]["target"] == {"text": "{button}"}  # type: ignore[index]


def test_terminal_run_with_another_app_in_front_is_refused():
    ex = Scripted({"browser": [("https://requests.example.net/queue", 140, 61)]}, "browser")
    r = Runner(ex).run(actions.builtin()["terminal.run"], {"command": "Get-ChildItem"})
    assert not r.ok and ex.inputs == []
    assert r.failed["step"] == "1:focus" and r.failed["code"] == "wrong_app"  # type: ignore[index]
    detail = r.safety[0]["detail"]
    assert detail.startswith("expected terminal in front, but the screen shows browser main")
    assert r.safety[0]["kind"] == "wrong_app" and "nothing was sent" in detail


def test_steps_check_their_outcome_and_report_where_they_failed():
    teams = {"teams": {"teams.list": (0, 0, 200, 400), "teams.header": (200, 0, 440, 40)}}
    ex = Scripted(
        {"teams": [("Ana Silva", 10, 50), ("Ben Okoro", 10, 100)], "terminal": []},
        "terminal",
        regions=teams,
        spots={"session.taskbar.teams": (300, 390)},
    )
    r = Runner(ex).run(actions.builtin()["teams.open_chat"], {"name": "Ben Okoro"})
    # Switched by the spot and clicked the chat, but the header never showed its name.
    assert ex.inputs == ["click 300,390", "click 41,106"]
    assert not r.ok and r.failed["step"] == "2:click"  # type: ignore[index]
    assert "expected name='Ben Okoro'" in r.failed["reason"]  # type: ignore[index]
    assert [s.kind for s in r.steps] == ["focus"]
    r = Runner(ex).run(actions.builtin()["teams.open_chat"], {"name": "Cy Dube"})
    assert r.failed["candidates"]  # type: ignore[index]
    assert "'Cy Dube' is not on screen in list" in r.failed["reason"]  # type: ignore[index]


def test_extract_and_repeat():
    a = one(
        'args.words = { type = "list" }\n'
        '[[action.chat.a.step]]\ndo = "repeat"\neach = "words"\nas = "w"\n'
        '[[action.chat.a.step.step]]\ndo = "click"\ntarget = { text = "{w}" }\n'
        'risk = "reversible"\n'
        "[[action.chat.a.step]]\ndo = \"extract\"\nregex = 'Inbox\\s+(?P<unread>\\d+)'"
    )
    ex = Scripted({"chat": [("Inbox 16", 10, 10), ("Drafts", 10, 40)]}, "chat")
    r = Runner(ex).run(a, {"words": ["Drafts", "Inbox"]})
    assert r.ok and r.fields == {"unread": "16"} and len(ex.inputs) == 2
    assert [s.label for s in r.steps] == ["1.1.1", "1.2.1", "1", "2"]


def test_header_names_match_cut_short_and_beside_icon_glyphs():
    from jharokha.core.actions import name_on
    from jharokha.core.guard import WrongApp, check_front

    from .conftest import lines_for

    assert name_on(lines_for([("@ Ana Alder", 0, 0)]), "Ana Alder")
    assert name_on(lines_for([("Ana Ald…", 0, 0)]), "Ana Alder")
    assert not name_on(lines_for([("Ana Altman", 0, 0)]), "Ana Alder")
    assert name_on([], "")
    check_front("generic", "generic", "screen", 0.55)  # holds at any confidence
    with pytest.raises(WrongApp):
        check_front("generic", "teams", "chat", 1.0)


def test_a_short_line_is_typed_and_a_long_one_pasted_else_typed():
    from jharokha.core.screen import ScreenError

    class Pasting(Scripted):
        fail: bool = False

        def paste(self, text: str) -> bool:
            if self.fail:
                raise ScreenError("paste_failed", "nothing came")
            self.inputs.append(f"paste {text}")
            return False

    def send(line: str, fail: bool = False) -> tuple[list[str], str]:
        ex = Pasting({"terminal": [(r"PS C:\>", 10, 10)]}, "terminal")
        ex.fail = fail
        r = Runner(ex, "irreversible")
        r.action = actions.builtin()["terminal.run"]
        r.res, r.cap = actions.Result("terminal.run"), "irreversible"
        r._send(line)
        return ex.inputs, r.res.fields["input"]

    long = "Get-ChildItem C:/Windows | Select-Object -First 3 Name, Length, LastWriteTime | ft" * 3
    assert send("Get-Date") == (["type Get-Date", "press enter"], "typed")
    assert send(long) == ([f"paste {long}", "press enter"], "pasted")
    assert send(long, fail=True) == (
        ["press escape", f"type {long}", "press enter"],
        "typed: the paste did not arrive",
    )


def test_a_row_the_terminal_wrapped_is_joined_to_the_next():
    def row(text: str, y: int, right: int) -> Line:
        return Line(text, (10, y, right - 10, 16))

    lines = [
        row("a long line the terminal cut at the ed", 10, 1130),
        row("ge of the pane", 30, 200),
        row("short", 50, 100),
        row("ends near the edge but not the furthest", 70, 1100),
        row("next", 90, 80),
    ]
    assert unwrapped(lines, 1150) == [
        "a long line the terminal cut at the edge of the pane",
        "short",
        "ends near the edge but not the furthest",
        "next",
    ]
