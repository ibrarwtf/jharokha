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
    with pytest.raises(inp.NotForeground, match="after 1 of 3 characters, 'a' being"):
        inp.type_text(SESSION, "abc")
    assert len(inp.sent) == 2  # only "a" went, as one down and one up


def test_key_names_and_a_plus_key_in_a_chord(inp, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([SESSION]))
    assert inp._vks("ctrl+minus") == [inp.KEYS["ctrl"], 0xBD]
    assert inp._vks("ctrl+plus") == inp._vks("ctrl+equals") == [inp.KEYS["ctrl"], 0xBB]
    assert inp._vks("ctrl++") == [inp.KEYS["ctrl"], ord("+")]
    assert inp._vks("ctrl+-") == [inp.KEYS["ctrl"], ord("-")]


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


def test_typing_stops_when_the_user_types_between_chunks(inp, monkeypatch):
    from jharokha.core.guard import TYPE_CHUNK, TypingInterrupted, UserActive

    monkeypatch.setattr(inp, "user32", FakeUser32([SESSION]))
    checks = []

    def between() -> None:
        checks.append(len(inp.sent))
        if len(checks) == 2:
            raise UserActive(0.1, 3)

    text = "a" * (3 * TYPE_CHUNK)
    with pytest.raises(TypingInterrupted, match=f"after {2 * TYPE_CHUNK} of {len(text)}"):
        inp.type_text(SESSION, text, between=between)
    assert checks == [2 * TYPE_CHUNK, 4 * TYPE_CHUNK]  # a down and an up per character
    assert len(inp.sent) == 4 * TYPE_CHUNK


def test_paste_checks_for_the_user_before_its_enter(inp, clip, monkeypatch):
    from jharokha.core.guard import UserActive

    monkeypatch.setattr(inp, "user32", FakeUser32([SESSION]))

    def busy() -> None:
        raise UserActive(0.2, 3)

    with pytest.raises(UserActive, match="the rest was not sent"):
        inp.paste_text(SESSION, "Get-Date\n", between=busy)
    assert len(inp.sent) == 4 and clip[-1] == "user's text"  # Ctrl+V, no Enter


def test_every_send_is_noted_as_our_own_input(monkeypatch):
    from jharokha.win import input

    calls: list[str] = []

    class Sender:
        def SendInput(self, n, arr, size) -> int:  # noqa: N802, ANN001
            calls.append("send")
            return n

    monkeypatch.setattr(input, "user32", Sender())
    monkeypatch.setattr(input, "record_own_input", lambda: calls.append("own"))
    input._send(input._mouse(0))
    assert calls == ["send", "own"]
