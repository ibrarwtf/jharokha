"""Actions as data: the definitions, locators and the runner on a scripted executor (fast, no
OCR), then the built-in actions on the simulated session with the real Windows OCR,
including the two incident replays, now refused."""

from __future__ import annotations

import base64
import sys
from dataclasses import dataclass, field, replace

import pytest

from evals.scenarios import seen
from jharokha.core import actions
from jharokha.core.actions import ActionError, Runner, Snapshot, at_or_before, when
from jharokha.core.locate import Hit, Miss, inside, items, locate, locator
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
        "teams.new_messages",
        "teams.draft",
        "outlook.unread",
        "outlook.open",
        "browser.goto",
        "browser.click_sequence",
    }
    assert acts["terminal.run"].risk == "irreversible"
    assert acts["teams.draft"].risk == "reversible" and acts["teams.draft"].newline == "shift+enter"
    assert acts["outlook.unread"].max_risk == "reversible"
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


def test_times_on_screen():
    assert when("Priya Nair  7:12") == (None, 432)
    assert when("10/23/2026 9:41 PM") == (when("2026-10-23")[0], 21 * 60 + 41)  # type: ignore[index]
    assert when("no time here") is None
    assert at_or_before((None, 430), (None, 432)) and not at_or_before((None, 433), (None, 432))
    assert at_or_before((1, 900), (2, 10))


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


def test_items_by_gaps_and_by_a_head_regex():
    by_gap = items(screen_lines()[4:])
    assert [i.text for i in by_gap] == ["Ana Silva\nRun report 120", "Ben Okoro\nInvite 381"]
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
    heads = items(thread, r"^(?P<who>\D.*?)\s+(?P<time>\d{1,2}:\d{2})$")
    assert [(i.fields["who"], i.fields["time"]) for i in heads] == [
        ("Ana Silva", "7:05"),
        ("You", "7:12"),
    ]
    assert heads[1].text == "You  7:12\nsecond\nand more"


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

    def settle(self) -> None:
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
    assert not r.ok and ex.inputs == []
    assert r.safety == [{"kind": "refused", "step": "2:press", "detail": r.failed["reason"]}]  # type: ignore[index]
    # The session guard's cap works the same way, below the action's own.
    draft = actions.builtin()["teams.draft"]
    r = Runner(ex, allow="read").run(draft, {"chat": "Ana", "text": "x"})
    assert not r.ok and r.safety[0]["kind"] == "refused" and ex.inputs == []


def test_a_newline_that_is_enter_makes_typing_irreversible():
    a = one(
        'max_risk = "reversible"\nnewline = "enter"\nargs.text = { type = "text" }\n'
        '[[action.chat.a.step]]\ndo = "type"\ntext = "{text}"\nrisk = "reversible"'
    )
    ex = Scripted({"chat": []}, "chat")
    assert Runner(ex).run(a, {"text": "one line"}).ok
    r = Runner(ex).run(a, {"text": "line one\nline two"})
    assert not r.ok and r.safety[0]["kind"] == "refused"
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
    assert r.failed["step"] == "1:focus"  # type: ignore[index]
    assert "terminal is not in front (browser main is in front)" in r.safety[0]["detail"]


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
    assert "expected text='Ben Okoro'" in r.failed["reason"]  # type: ignore[index]
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


# On the simulated session, with the real Windows OCR

sim = pytest.mark.skipif(sys.platform != "win32", reason="GDI and Windows OCR")


def session(front: str):  # noqa: ANN201
    from evals.executor import SourceExecutor
    from evals.sim import Desktop, SimSource

    source = SimSource(Desktop(100, front=front))
    return source.desk, SourceExecutor(source, source.catalog())


def do(ex, name: str, **args: object) -> actions.Result:  # noqa: ANN001
    return Runner(ex).run(actions.builtin()[name], args)


@sim
@pytest.mark.windows
def test_terminal_run_returns_output_and_exit_code_by_its_end_marker():
    desk, ex = session("terminal")
    r = do(ex, "terminal.run", command="Get-Content notes.txt")
    assert r.ok, r.failed
    assert r.output == desk.shell.files["notes.txt"]
    assert (r.exit_code, r.fields["ok"]) == (None, True)
    r = do(ex, "terminal.run", command="cmd /c exit 3")
    assert r.ok and r.output == [] and (r.exit_code, r.fields["ok"]) == (3, False)
    assert len(desk.shell.log) == 2 and desk.shell.log[0].startswith("Get-Content notes.txt; $jkq")


@sim
@pytest.mark.windows
def test_terminal_put_file_writes_the_bytes_and_checks_their_hash():
    desk, ex = session("terminal")
    data = bytes(range(256)) + "naïve résumé\r\n".encode() * 20
    b64 = base64.b64encode(data).decode()
    r = do(ex, "terminal.put_file", path="out\\blob.bin", content_base64=b64)
    assert r.ok, r.failed
    assert desk.shell.written == {f"{desk.shell.cwd}\\out\\blob.bin": data}
    assert r.fields["hash_check"] in ("exact", "folded") and r.fields["size"] == len(data)


@sim
@pytest.mark.windows
def test_teams_draft_leaves_the_text_in_compose_and_sends_nothing():
    desk, ex = session("terminal")
    chat = desk.teams.chats[2]
    r = do(ex, "teams.draft", chat=chat.name, text="draft line one\ndraft line two")
    assert r.ok, r.failed
    assert desk.front == "teams" and desk.teams.chat is chat
    assert chat.draft == ["draft line one", "draft line two"]
    assert desk.teams.sent == [] and desk.safety() == []
    assert "press shift+enter" in ex.inputs and "press enter" not in ex.inputs


@sim
@pytest.mark.windows
def test_teams_new_messages_after_a_time_across_scrolling():
    desk, ex = session("outlook")
    chat = desk.teams.chats[1]
    since = chat.msgs[-17].time  # older than the screen shows
    r = do(ex, "teams.new_messages", chat=chat.name, since=since)
    assert r.ok, r.failed
    want = chat.msgs[-16:]
    got = r.items["messages"]
    assert [m["time"].replace(" ", "") for m in got] == [m.time for m in want]
    assert all(seen(g["text"], m.lines[0]) for g, m in zip(got, want, strict=True))
    assert all(seen(g["who"], m.who) for g, m in zip(got, want, strict=True))
    assert r.fields["count"] == 16 and desk.teams.offset > 0 and desk.safety() == []


@sim
@pytest.mark.windows
def test_browser_goto_navigates_and_refuses_what_is_not_a_url():
    desk, ex = session("teams")
    r = do(ex, "browser.goto", url="status.example.net")
    assert r.ok, r.failed
    assert desk.browser.url == "https://status.example.net/" and desk.browser.searches == []
    before = list(ex.inputs)
    r = do(ex, "browser.goto", url="Get-ChildItem")
    assert not r.ok and ex.inputs == before and desk.browser.searches == []


@sim
@pytest.mark.windows
def test_outlook_unread_open_and_a_click_sequence():
    desk, ex = session("browser")
    r = do(ex, "outlook.unread", folder="Inbox")
    assert r.ok, r.failed
    assert r.fields["unread"] == str(sum(m.unread for m in desk.outlook.folders["Inbox"]))
    shown = desk.outlook.mails[: desk.outlook.visible()]
    assert len(r.items["messages"]) == len(shown)
    pairs = zip(r.items["messages"], shown, strict=True)
    assert all(seen(i["subject"], m.subject) for i, m in pairs)
    mail = desk.outlook.mails[3]
    r = do(ex, "outlook.open", subject=mail.subject)
    assert r.ok, r.failed
    assert desk.outlook.selected == 3 and seen("\n".join(r.lines["message"]), mail.body[0])
    r = do(ex, "browser.click_sequence", buttons=["Docs", "Status board"], times=2)
    assert r.ok, r.failed
    docs, status = "https://docs.example.net/", "https://status.example.net/"
    assert desk.browser.history[-4:] == [docs, status, docs, status]


# The incidents of tests/test_sim.py, replayed through actions: both refused.


@sim
@pytest.mark.windows
def test_incident_terminal_run_with_edge_in_front_is_refused():
    desk, ex = session("browser")
    r = do(ex, "terminal.run", command="Get-ChildItem")
    assert not r.ok and r.safety and r.safety[0]["kind"] == "refused"
    assert ex.inputs == [] and desk.browser.searches == [] and desk.shell.log == []
    assert desk.safety() == []


@sim
@pytest.mark.windows
def test_incident_a_newline_typed_through_teams_draft_never_sends():
    desk, ex = session("teams")
    chat = desk.teams.chat
    r = do(ex, "teams.draft", chat=chat.name, text="draft line one\ndraft line two")
    assert r.ok, r.failed
    assert desk.teams.sent == [] and desk.safety() == []
    assert chat.draft == ["draft line one", "draft line two"]
    # A variant that would press Enter is refused before it types anything.
    draft = actions.builtin()["teams.draft"]
    sends = replace(
        draft,
        newline="enter",
        steps=tuple(replace(s, params={**s.params, "newline": "enter"}) for s in draft.steps),
    )
    r = Runner(ex).run(sends, {"chat": chat.name, "text": "a\nb"})
    assert not r.ok and r.safety[0]["kind"] == "refused" and desk.teams.sent == []
