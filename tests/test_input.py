"""The foreground guard: keys reach the session or nothing is sent."""

from __future__ import annotations

import sys
from contextlib import nullcontext

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows input")

SESSION, OTHER = 100, 200


class FakeUser32:
    """GetForegroundWindow answers from a script; the last answer repeats."""

    def __init__(self, foreground: list[int]) -> None:
        self.foreground = foreground

    def GetForegroundWindow(self) -> int:  # noqa: N802
        return self.foreground.pop(0) if len(self.foreground) > 1 else self.foreground[0]

    def VkKeyScanW(self, ch: str) -> int:  # noqa: N802
        return ord(ch.upper())

    def MapVirtualKeyW(self, vk: int, kind: int) -> int:  # noqa: N802
        return vk


@pytest.fixture
def inp(monkeypatch):
    from jharokha.win import input

    sent: list[object] = []
    monkeypatch.setattr(input, "_active", lambda *a: nullcontext())
    monkeypatch.setattr(input, "_send", lambda *i: sent.extend(i))
    monkeypatch.setattr(input.time, "sleep", lambda s: None)
    input.sent = sent  # type: ignore[attr-defined]
    return input


def test_type_sends_nothing_when_the_session_is_not_in_front(inp, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([OTHER]))
    with pytest.raises(inp.NotForeground):
        inp.type_text(SESSION, "cls")
    assert inp.sent == []


def test_focus_at_clicks_first_then_types(inp, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([OTHER, SESSION]))
    inp.type_text(SESSION, "ab", focus_at=(5, 5))
    kinds = [i.type for i in inp.sent]
    assert kinds[:2] == [inp.INPUT_MOUSE, inp.INPUT_MOUSE]
    assert kinds[2:] == [inp.INPUT_KEYBOARD] * 4


def test_typing_stops_when_focus_leaves_mid_text(inp, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([SESSION, SESSION, OTHER]))
    with pytest.raises(inp.NotForeground):
        inp.type_text(SESSION, "abc")
    assert len(inp.sent) == 2  # only "a" went, as one down and one up


@pytest.fixture
def clip(inp, monkeypatch):
    """The local clipboard as a list of what was set, starting with the user's text."""
    board = ["user's text"]
    monkeypatch.setattr(inp, "_clipboard", lambda: board[-1])
    monkeypatch.setattr(inp, "_set_clipboard", board.append)
    return board


def test_paste_sets_the_clipboard_presses_ctrl_v_then_enter_and_restores(inp, clip, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([SESSION]))
    inp.paste_text(SESSION, "line 1\nline 2\n")
    assert clip == ["user's text", "line 1\r\nline 2", "user's text"]
    vks = [i.u.ki.wScan for i in inp.sent]  # FakeUser32 maps scan code = vk
    assert vks == [inp.KEYS["ctrl"], ord("V"), ord("V"), inp.KEYS["ctrl"]] + [inp.KEYS["enter"]] * 2


def test_paste_restores_the_clipboard_when_nothing_was_sent(inp, clip, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([OTHER]))
    with pytest.raises(inp.NotForeground):
        inp.paste_text(SESSION, "secret")
    assert inp.sent == [] and clip[-1] == "user's text"


def test_press_is_guarded_too(inp, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([OTHER]))
    with pytest.raises(inp.NotForeground):
        inp.press(SESSION, "enter")
    assert inp.sent == []
