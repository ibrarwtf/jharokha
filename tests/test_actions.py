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


def test_builtin_actions_parse():
    acts = actions.builtin()
    assert set(acts) == {
        "terminal.run",
        "terminal.glyphs",
        "terminal.put_file",
        "terminal.write_file",
        "teams.open_chat",
        "teams.messages",
        "teams.latest",
        "teams.draft",
        "outlook.unread",
        "outlook.open",
        "browser.goto",
        "browser.click_sequence",
    }
    assert acts["teams.draft"].newline is None
    assert acts["teams.draft"].steps[-1].params["newline"] == "shift+enter"
    d = actions.describe(acts["teams.draft"])
    assert d["args"]["chat"] == {"type": "text", "required": True}


@pytest.mark.parametrize(
    ("body", "error"),
    [
        ('[[action.x.a.step]]\ndo = "type"\ntext = "{who}"', "'{who}' is not an"),
        ('[[action.x.a.step]]\ndo = "jump"', "do must be one of"),
        ('[[action.x.a.step]]\ndo = "focus"\nnope = 1', "unknown key nope"),
        ('[[action.x.a.step]]\ndo = "click"\ntarget = { near = "x" }', "locator"),
        ('[[action.x.a.step]]\ndo = "type"\ntext = "a"\nrisk = "maybe"', "unknown key risk"),
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
        '[action.x.hello]\nsummary = "Say hello"\n'
        '[[action.x.hello.step]]\ndo = "type"\ntext = "hello"\n'
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
    r = actions.Result("x", fields={"n": 1}, steps=[actions.StepReport("1", "read", 5)])
    assert "steps_done" not in r.to_dict(brief=True) and "steps_done" in r.to_dict()
    r.ok = False
    assert r.to_dict(brief=True)["steps_done"] == [{"step": "1", "do": "read", "ms": 5}]


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


def test_a_step_that_sends_is_refused_before_any_input_unless_allowed():
    a = one(
        '[[action.chat.a.step]]\ndo = "type"\ntext = "hi"\n'
        '[[action.chat.a.step]]\ndo = "press"\nkeys = "Return"'
    )
    ex = Scripted({"chat": [("Type a message", 10, 300)]}, "chat")
    policies = {"chat": Policy(irreversible_keys=frozenset({"enter"}))}
    r = Runner(ex, policies=policies).run(a, {})
    assert not r.ok and ex.inputs == [] and r.failed["code"] == "irreversible"  # type: ignore[index]
    assert r.failed["step"] == "2:press" and "Return is irreversible in chat" in r.failed["reason"]  # type: ignore[index]
    assert "allow='irreversible'" in r.failed["reason"]  # type: ignore[index]
    assert Runner(ex, allow="irreversible", policies=policies).run(a, {}).ok
    assert ex.inputs == ["type hi", "press Return"]
    # Without a policy naming the key, nothing is gated: a terminal command is just a step.
    ex = Scripted({"chat": [("Type a message", 10, 300)]}, "chat")
    assert Runner(ex).run(a, {}).ok and ex.inputs == ["type hi", "press Return"]


def test_a_newline_that_is_enter_makes_typing_send():
    a = one(
        'newline = "enter"\nargs.text = { type = "text" }\n'
        '[[action.chat.a.step]]\ndo = "type"\ntext = "{text}"'
    )
    ex = Scripted({"chat": []}, "chat")
    assert Runner(ex).run(a, {"text": "one line"}).ok
    r = Runner(ex).run(a, {"text": "line one\nline two"})
    assert not r.ok and r.failed["code"] == "irreversible"  # type: ignore[index]
    assert ex.inputs == ["type one line"]
    b = one('args.text = { type = "text" }\n[[action.chat.a.step]]\ndo = "type"\ntext = "{text}"')
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
    detail = r.failed["reason"]  # type: ignore[index]
    assert detail.startswith("expected terminal in front, but the screen shows browser main")
    assert "nothing was sent" in detail


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
        r.res = actions.Result("terminal.run")
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


# terminal.run on a scripted shell


class Shell(Scripted):
    """A shell in a 10-row view: a typed line shows on the prompt's row (unless dropped),
    Enter runs it (cls clears the rows above, echo and all), printing output then the
    marker unless it never finishes; the wheel moves the view a row a notch."""

    def __init__(self, output: list[str], drop: int = 0, finish: bool = True) -> None:
        super().__init__({}, "terminal", regions={"terminal": dict(SCREEN_REGIONS)})
        self.output, self.drop, self.finish = output, drop, finish
        self.rows: list[str] = []
        self.typed = ""
        self.view = 0  # rows scrolled up from the bottom
        self.running = False

    def _all(self) -> list[str]:
        return self.rows + ([] if self.running else [r"PS C:\> " + self.typed])

    def read(self) -> Snapshot:
        rows = self._all()
        end = len(rows) - self.view
        shown = rows[max(0, end - 10) : end]
        lines = lines_for([(t, 10, 10 + 38 * i) for i, t in enumerate(shown) if t])
        return Snapshot(self.fid, (640, 400), lines, "terminal", "shell", None, SCREEN_REGIONS)

    def type(self, text: str) -> None:
        super().type(text)
        if self.drop:
            self.drop -= 1
        else:
            self.typed = text

    def press(self, keys: str) -> None:
        super().press(keys)
        if keys == "escape":
            self.typed = ""
        elif keys == "enter":
            import re

            line, self.typed = self.typed, ""
            m = re.search(r'"(\w\w)"\+"(\w{4}):"', line)
            self.rows = [] if line.startswith("cls;") else [*self.rows, rf"PS C:\> {line}"]
            self.rows += self.output
            if m and self.finish:
                self.rows.append(f"{m[1]}{m[2]}:True:0")
            else:
                self.running = True

    def scroll(self, x: int, y: int, ticks: int) -> None:
        super().scroll(x, y, ticks)
        self.view = max(0, min(self.view + ticks, len(self._all()) - 10))


SCREEN_REGIONS: dict[str, Box] = {"terminal.body": SCREEN}


def run(ex: Shell, **args: object) -> actions.Result:
    return Runner(ex).run(actions.builtin()["terminal.run"], {"command": "Get-Thing", **args})


def test_a_reply_longer_than_the_screen_is_read_up_the_scrollback():
    ex = Shell([f"row {i}" for i in range(25)])
    r = run(ex)
    assert r.ok and r.exit_code == 0
    assert r.output == [f"row {i}" for i in range(25)]
    assert ex.view == 0  # scrolled back down
    assert [i for i in ex.inputs if i.startswith("scroll")][-1] == "scroll -25"


def test_a_short_reply_scrolls_nothing():
    ex = Shell(["one", "two"])
    r = run(ex)
    assert r.ok and r.output == ["one", "two"] and not any("scroll" in i for i in ex.inputs)


def test_a_timeout_returns_what_was_printed_so_far():
    ex = Shell(["working", "still working"], finish=False)
    r = run(ex, timeout_s=2)
    assert not r.ok and "within 2 s" in r.failed["reason"]  # type: ignore[index]
    assert r.output == ["working", "still working"] and r.fields["finished"] is False
    assert "scroll -300" in ex.inputs  # once to the bottom, in case the view was scrolled up
    bad = run(Shell([]), timeout_s=301)
    assert not bad.ok and bad.failed["code"] == "bad_argument"  # type: ignore[index]


def test_a_line_that_never_shows_is_not_run():
    ex = Shell(["x"], drop=2)
    r = run(ex)
    assert not r.ok and "did not show" in r.failed["reason"]  # type: ignore[index]
    assert "press enter" not in ex.inputs and ex.inputs[-1] == "press escape"
    ex = Shell(["x"], drop=1)
    r = run(ex)
    assert r.ok and r.output == ["x"] and "typed again" in r.fields["input"]


def test_scrollback_pages_join_where_they_overlap():
    from jharokha.core.actions import _overlap

    assert _overlap(["a", "b", "c", "d"], ["c", "d", "e"]) == ["a", "b", "c", "d", "e"]
    # The page's last row and the run's first are wrapped lines read in part.
    assert _overlap(["a", "b", "c", "d-cut"], ["d-tail", "b", "c", "d", "e"]) == [
        "a",
        "b",
        "c",
        "d",
        "e",
    ]
    assert _overlap(["a", "b"], ["x", "y"]) == ["a", "b", "…", "x", "y"]


def test_a_chunk_is_confirmed_by_its_length_when_the_index_is_misread():
    from jharokha.core.actions import _confirmed

    assert _confirmed("0:344", "0:344") and _confirmed("0344", "0:344")
    assert _confirmed("8:344", "0:344")
    assert not _confirmed("0:343", "0:344")


def test_the_font_is_learned_before_the_first_command():
    class Unlearned(Shell):
        known = 0

        def glyphs(self) -> int:
            return self.known

        def learn_glyphs(self) -> int:
            self.known = 94
            return 94

    ex = Unlearned(["out"])
    r = run(ex)
    assert r.ok and r.output == ["out"] and r.fields["glyphs"] == 94
    assert ex.inputs.count("press enter") == 2
    r = run(ex)
    assert ex.inputs.count("press enter") == 3  # learned once


def test_a_scroll_back_doubles_its_stride_and_a_run_ends_scrolled_down():
    a = one(
        '[[action.chat.a.step]]\ndo = "scroll"\nregion = "thread"\nticks = 7\n'
        'max_ticks = 50\nmax = 5\nuntil = { text = "never" }'
    )

    class Moving(Scripted):
        def read(self) -> Snapshot:
            self.fid += 1  # a list that keeps moving
            snap = super().read()
            return Snapshot(
                snap.frame_id,
                snap.size,
                lines_for([(f"m{self.fid}", 10, 10)]),
                "chat",
                "main",
                None,
                {"chat.thread": SCREEN},
            )

    ex = Moving({}, "chat")
    r = Runner(ex).run(a, {})
    scrolls = [i for i in ex.inputs if i.startswith("scroll")]
    assert scrolls == [
        "scroll 7",
        "scroll 14",
        "scroll 28",
        "scroll 50",
        "scroll 50",
        "scroll -298",
    ]
    assert not r.ok and "scrolled back down" in r.failed["reason"]  # type: ignore[index]


def test_teams_latest_reads_the_last_n_without_scrolling_back():
    thread = [
        ("Ana Silva  7:05", 10, 30),
        ("first", 10, 52),
        ("Ana Silva  7:09", 10, 100),
        ("second", 10, 122),
        ("Ana Silva  7:12", 10, 170),
        ("third", 10, 192),
    ]
    regions = {
        "teams.header": (0, 0, 640, 20),
        "teams.thread": (0, 20, 640, 280),
        "teams.compose": (0, 300, 640, 100),
    }

    class Thread(Scripted):
        def read(self) -> Snapshot:
            snap = super().read()
            info = {"teams.thread": RegionInfo("list", "messages")}
            return Snapshot(
                snap.frame_id,
                snap.size,
                snap.lines,
                "teams",
                "chat",
                True,
                snap.regions,
                info=info,
            )

    ex = Thread({"teams": thread}, "teams", regions={"teams": regions})
    r = Runner(ex).run(actions.builtin()["teams.latest"], {"n": 2})
    assert r.ok, r.failed
    assert [(m["time"], m["text"]) for m in r.items["messages"]] == [
        ("7:09", "second"),
        ("7:12", "third"),
    ]
    assert [i for i in ex.inputs if i.startswith("scroll")] == ["scroll -150"]


def test_an_ambiguous_text_target_is_refused_not_guessed():
    lines = lines_for(
        [
            ("Ana Alder", 20, 40),
            ("Ana Alder: see you then", 20, 60),
            ("Reports", 20, 100),
            ("Reports", 300, 100),
            ("Reports archive", 20, 140),
        ]
    )

    def at(**t: object) -> Hit | Miss:
        return locate(locator(t), lines, {}, {}, SCREEN)

    # The whole line ranks above a line that only starts with it: no ambiguity.
    hit = at(text="Ana Alder")
    assert isinstance(hit, Hit) and hit.center[1] == 46
    miss = at(text="Reports")
    assert isinstance(miss, Miss) and "ambiguous: 2 matches alike" in miss.reason
    assert miss.candidates == ("Reports", "Reports archive")
    second = at(text="Reports", nth=2)
    assert isinstance(second, Hit) and second.center[0] > 300
    assert isinstance(at(regex=r"Repo\w+"), Miss)


def test_a_read_of_items_looks_twice_and_flags_what_changed():
    a = one('[[action.chat.a.step]]\ndo = "read"\nregion = "thread"\nas = "items"\ninto = "m"')

    class Drawing(Scripted):
        def read(self) -> Snapshot:
            self.fid += 1
            text = "half drawn" if self.fid == 1 else "fully drawn"
            lines = lines_for([("Ana Silva  7:05", 10, 30), ("first", 10, 52)])
            lines += lines_for([("Ben Okoro  7:09", 10, 100), (text, 10, 122)])
            info = {"chat.thread": RegionInfo("list", "messages")}
            return Snapshot(
                self.fid,
                (640, 400),
                lines,
                "chat",
                "main",
                None,
                {"chat.thread": SCREEN},
                info=info,
            )

    r = Runner(Drawing({}, "chat")).run(a, {})
    assert r.ok and [m.get("unsteady") for m in r.items["m"]] == [None, True]
    assert "1 unsteady" in r.steps[0].note
