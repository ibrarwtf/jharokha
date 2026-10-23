"""Pure guards before input (lease decision, user idle, covered geometry) and the trace."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest

from jharokha.core.guard import (
    LLKHF_INJECTED,
    LLMHF_INJECTED,
    MOVE_SLACK_PX,
    WM_MOUSEMOVE,
    Activity,
    Holder,
    InputLeased,
    Irreversible,
    Policy,
    TypingInterrupted,
    UserActive,
    WrongApp,
    check_front,
    check_keys,
    check_lease,
    check_user_idle,
    chords_of,
    combo_key,
    covered_parts,
    refusal,
    sample_points,
    text_steps,
    user_idle_s,
    warnings,
)
from jharokha.core.trace import Trace

ME, OTHER = 10, 20


def alive(pid: int) -> bool:
    return True


def dead(pid: int) -> bool:
    return False


def test_free_or_own_lease_lets_input_through():
    check_lease(None, ME, 100.0, 60, alive)
    check_lease(Holder(ME, 99.0), ME, 100.0, 60, alive)


def test_live_other_holder_refuses_with_its_pid_and_idle_time():
    with pytest.raises(InputLeased, match="process 20 .* 5 s ago") as e:
        check_lease(Holder(OTHER, 95.0), ME, 100.0, 60, alive)
    assert e.value.owner_pid == OTHER and e.value.idle_s == 5.0


def test_lease_lapses_after_ttl_or_when_the_holder_died():
    check_lease(Holder(OTHER, 40.0), ME, 100.0, 60, alive)
    check_lease(Holder(OTHER, 99.0), ME, 100.0, 60, dead)


def test_a_holder_from_the_future_does_not_hold():
    check_lease(Holder(OTHER, 500.0), ME, 100.0, 60, alive)


def test_user_idle_counts_input_newer_than_ours_as_the_users():
    assert user_idle_s(last_input_ms=9_000, now_ms=10_000, own_input_ms=None) == 1.0
    assert user_idle_s(last_input_ms=9_000, now_ms=10_000, own_input_ms=5_000) == 1.0


def test_our_own_input_is_not_the_users():
    assert user_idle_s(last_input_ms=5_100, now_ms=10_000, own_input_ms=5_000) is None
    assert user_idle_s(last_input_ms=4_000, now_ms=10_000, own_input_ms=5_000) is None


def test_user_idle_survives_the_tick_counter_wrapping():
    wrap = 1 << 32
    assert user_idle_s(last_input_ms=wrap - 500, now_ms=500, own_input_ms=None) == 1.0
    assert user_idle_s(last_input_ms=100, now_ms=1_100, own_input_ms=wrap - 2_000) == 1.0
    assert user_idle_s(last_input_ms=wrap - 100, now_ms=500, own_input_ms=wrap - 200) is None


def test_check_user_idle_raises_inside_the_threshold():
    check_user_idle(None)
    check_user_idle(3.5)
    with pytest.raises(UserActive, match="1.0 s ago") as e:
        check_user_idle(1.0)
    assert e.value.idle_s == 1.0


# What the low-level hooks report while jharokha sends: message, point, flags.
WM_LBUTTONDOWN, WM_MOUSEWHEEL = 0x0201, 0x020A


def watched(origin: tuple[int, int] | None = (500, 300)) -> tuple[Activity, list[float]]:
    now = [10.0]
    return Activity(origin, clock=lambda: now[0]), now


def test_our_injected_keys_and_clicks_are_not_the_user():
    a, _ = watched()
    assert not a.key(LLKHF_INJECTED)
    assert not a.key(LLKHF_INJECTED | 0x80)  # a key up we sent
    assert not a.mouse(WM_LBUTTONDOWN, 10, 10, LLMHF_INJECTED)
    assert not a.mouse(WM_MOUSEMOVE, 900, 900, LLMHF_INJECTED | 0x02)
    assert a.user_seen() is None


def test_a_users_key_stops_input_and_is_dated():
    a, now = watched()
    assert not a.key(LLKHF_INJECTED)
    now[0] = 10.5
    assert a.key(0x00) and a.what == "key"
    now[0] = 10.75
    assert a.user_seen() == 0.25
    assert a.key(0x80) and a.at == 10.5  # the first one dates it


def test_a_users_click_or_wheel_counts_and_a_resting_mouse_does_not():
    a, _ = watched()
    assert not a.mouse(WM_MOUSEMOVE, 503, 298, 0)  # drift: within MOVE_SLACK_PX
    assert not a.mouse(WM_MOUSEMOVE, 496, 304, 0)
    assert a.user_seen() is None
    assert a.mouse(WM_MOUSEMOVE, 500 + MOVE_SLACK_PX + 1, 300, 0) and a.what == "move"
    b, _ = watched()
    assert b.mouse(WM_MOUSEWHEEL, 500, 300, 0) and b.what == "button"
    c, _ = watched(origin=None)  # no cursor position: the first move is where it rests
    assert not c.mouse(WM_MOUSEMOVE, 50, 50, 0)
    assert not c.mouse(WM_MOUSEMOVE, 52, 51, 0)
    assert c.mouse(WM_MOUSEMOVE, 40, 50, 0)


MONITOR = (0, 0, 1920, 1080)
WORK = (0, 0, 1920, 1032)  # a 48 px taskbar at the bottom


def test_fully_visible_window_has_no_warnings():
    client = (100, 100, 900, 700)
    owners = {p: None for p in sample_points((800, 600), 32)}
    assert covered_parts(client, MONITOR, WORK, owners, 32) == []


def test_taskbar_over_the_bottom_rows_in_client_coordinates():
    client = (0, 30, 1920, 1080)  # maximized past the work area
    parts = covered_parts(client, MONITOR, WORK, {}, 32)
    assert parts == [{"kind": "taskbar", "rows": [1002, 1050]}]


def test_side_and_top_taskbars():
    client = (0, 0, 1920, 1080)
    left = covered_parts(client, MONITOR, (60, 0, 1920, 1080), {}, 32)
    assert left == [{"kind": "taskbar", "cols": [0, 60]}]
    top = covered_parts((10, 20, 500, 500), MONITOR, (0, 40, 1920, 1080), {}, 32)
    assert top == [{"kind": "taskbar", "rows": [0, 20]}]


def test_window_on_another_monitor_is_not_under_this_taskbar():
    client = (1920, 900, 2500, 1200)  # right of the monitor whose taskbar it is
    assert covered_parts(client, MONITOR, WORK, {}, 32) == []


def test_other_windows_group_per_app_and_skip_the_taskbar_band():
    client = (0, 0, 320, 1080)
    owners: dict[tuple[int, int], str | None] = dict.fromkeys(sample_points((320, 1080), 32))
    owners[(16, 16)] = owners[(48, 48)] = "notepad.exe"
    owners[(16, 1040)] = "explorer.exe"  # the taskbar itself: already reported
    owners[(304, 528)] = ""
    parts = covered_parts(client, MONITOR, (0, 0, 1920, 1032), owners, 32)
    assert parts == [
        {"kind": "taskbar", "rows": [1032, 1080]},
        {"kind": "offscreen", "box": [288, 512, 32, 32]},
        {"kind": "window", "app": "notepad.exe", "box": [0, 0, 64, 64]},
    ]


def test_sample_grid_stays_inside_the_client():
    pts = sample_points((100, 40), 32)
    assert pts == [(16, 16), (48, 16), (80, 16)]


FIXED = datetime(2026, 1, 2, 3, 4, 5, 678000, tzinfo=UTC)


def test_trace_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    t = Trace(None)
    assert not t.enabled
    assert t.record("type", "w", text="secret") is None
    assert list(tmp_path.iterdir()) == []


def test_trace_appends_jsonl_without_the_text_by_default(tmp_path):
    path = tmp_path / "trace.jsonl"
    t = Trace(path, clock=lambda: FIXED)
    t.record("click", "Session", point=(5, 6), front_app="msrdc.exe")
    t.record("type", "Session", point=(5, 6), text="hunter2")
    t.record("scroll", "Session", region=(0, 0, 10, 10))
    recs = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert recs[0] == {
        "time": "2026-01-02T03:04:05.678+00:00",
        "action": "click",
        "window": "Session",
        "point": [5, 6],
        "front_app": "msrdc.exe",
    }
    assert "text" not in recs[1] and "hunter2" not in path.read_text("utf-8")
    assert recs[1]["text_len"] == 7
    assert recs[1]["text_sha256"] == hashlib.sha256(b"hunter2").hexdigest()
    assert recs[2]["region"] == [0, 0, 10, 10]


def test_trace_includes_text_only_when_asked(tmp_path):
    t = Trace(tmp_path / "t.jsonl", include_text=True, clock=lambda: FIXED)
    rec = t.record("run", "Session", text="dir", keys=None)
    assert rec is not None and rec["text"] == "dir" and rec["text_len"] == 3


def test_trace_rejects_unknown_actions(tmp_path):
    with pytest.raises(ValueError):
        Trace(tmp_path / "t.jsonl").record("drag", "Session")


def test_trace_records_the_result(tmp_path):
    t = Trace(tmp_path / "t.jsonl", clock=lambda: FIXED)
    rec = t.record("press", "Session", keys="enter", front_app="teams.chat", result="irreversible")
    assert rec is not None and rec["result"] == "irreversible" and rec["keys"] == "enter"


# The app in front and its policy


def test_chords_compare_in_one_spelling():
    assert combo_key("Shift+Return") == "shift+enter"
    assert combo_key("shift+ctrl+x") == combo_key("ctrl+shift+x") == "ctrl+shift+x"
    assert combo_key("ctrl++") == "ctrl++" and combo_key("Escape") == "esc"
    with pytest.raises(ValueError):
        combo_key("ctrl+ +x")


def test_policy_parses_and_names_its_mistakes():
    p = Policy.parse({"newline": "Shift+Enter", "irreversible_keys": ["Return"]}, "app.t.policy")
    assert p.newline == "shift+enter" and p.irreversible("enter") and not p.irreversible("tab")
    assert Policy.parse({}, "x") == Policy()
    with pytest.raises(ValueError, match="unknown key irreversible_key"):
        Policy.parse({"irreversible_key": ["enter"]}, "app.t.policy")
    with pytest.raises(ValueError, match="list of chords"):
        Policy.parse({"irreversible_keys": "enter"}, "app.t.policy")
    with pytest.raises(ValueError, match="newline must be"):
        Policy.parse({"newline": 1}, "app.t.policy")


def test_text_steps_send_newlines_as_the_policy_says():
    assert text_steps("a\nb", False, None) == [("type", "a\nb")]
    assert text_steps("a\n\nb\n", False, "shift+enter") == [
        ("type", "a"),
        ("press", "shift+enter"),
        ("press", "shift+enter"),
        ("type", "b"),
        ("press", "shift+enter"),
    ]
    # A paste keeps the newlines inside it; only its final one is a key.
    assert text_steps("a\nb\n", True, "shift+enter") == [
        ("paste", "a\nb"),
        ("press", "shift+enter"),
    ]
    assert text_steps("a\nb\n", True, None) == [("paste", "a\nb\n")]
    assert text_steps("plain", False, "shift+enter") == [("type", "plain")]


def test_irreversible_keys_are_refused_unless_allowed():
    teams = Policy("shift+enter", frozenset({"enter"}))
    assert chords_of(text_steps("a\nb", False, teams.newline)) == ["shift+enter"]
    check_keys(teams, ["shift+enter"], None, "teams.chat")
    assert chords_of(text_steps("Get-Date\n", False, None)) == ["enter"]
    with pytest.raises(Irreversible, match="enter is irreversible in teams.chat"):
        check_keys(teams, ["enter"], None, "teams.chat")
    check_keys(teams, ["enter"], "irreversible", "teams.chat")
    assert chords_of([("paste", "x\n"), ("paste", "y")]) == ["enter"]


def test_front_app_must_be_the_expected_one_and_sure():
    check_front("terminal", "terminal", "shell", 0.93)
    check_front("teams.chat", "teams", "chat", 1.0)
    check_front("any", "browser", "page", 1.0)
    with pytest.raises(WrongApp, match="expected terminal in front, but the screen shows browser"):
        check_front("terminal", "browser", "page", 1.0)
    with pytest.raises(WrongApp, match="outlook.calendar"):
        check_front("outlook.calendar", "outlook", "mail", 1.0)
    with pytest.raises(WrongApp, match="only at confidence 0.55") as e:
        check_front("teams", "teams", "chat", 0.55)
    assert e.value.want == "teams"
    with pytest.raises(WrongApp, match="a lock screen or nothing known"):
        check_front("teams", None, None, 1.0)


def test_every_refusal_has_a_code_and_a_next_step():
    cases = [
        (WrongApp("expected terminal in front", "terminal"), "wrong_app", "Bring terminal"),
        (Irreversible("enter is irreversible"), "irreversible", "allow='irreversible'"),
        (InputLeased(20, 5, 60), "input_leased", "jharokha_status"),
        (UserActive(1.0, 3), "user_active", "input.user_idle_s"),
        (TypingInterrupted(0.1, 3, 8, "Get-ChildItem"), "user_active", "after 8 of 13"),
    ]
    for e, code, step in cases:
        got, message = refusal(e)
        assert got == code and step in message
        assert "nothing was sent" in message or "the rest was not sent" in message


def test_warnings_say_what_covering_means_for_input():
    parts = [
        {"kind": "taskbar", "rows": [1121, 1181]},
        {"kind": "window", "app": "notepad.exe", "box": [0, 0, 64, 64]},
        {"kind": "offscreen", "box": [288, 512, 32, 32]},
    ]
    got = warnings(parts)
    assert [w["kind"] for w in got] == ["taskbar", "window", "offscreen"]
    assert "taskbar covers client rows 1121 to 1181" in got[0]["detail"]
    assert "notepad.exe" in got[1]["detail"] and "off every monitor" in got[2]["detail"]
