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


def test_press_is_guarded_too(inp, monkeypatch):
    monkeypatch.setattr(inp, "user32", FakeUser32([OTHER]))
    with pytest.raises(inp.NotForeground):
        inp.press(SESSION, "enter")
    assert inp.sent == []
