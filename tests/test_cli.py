"""The shell commands: input needs --allow-input, and acts like the server's tools."""

from __future__ import annotations

import io
import sys

import pytest

from jharokha import __main__ as cli


def run(monkeypatch, source, *argv: str, stdin: str = "") -> None:
    monkeypatch.setattr(cli, "_source", lambda: source)
    monkeypatch.setattr(sys, "argv", ["jharokha", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    cli.main()


def test_input_commands_need_allow_input(monkeypatch, source):
    for argv in (["click", "1", "1"], ["type", "x"], ["press", "enter"], ["scroll", "-1"]):
        with pytest.raises(SystemExit, match="input_disabled"):
            run(monkeypatch, source, *argv)


def test_type_from_stdin_with_focus_and_enter(monkeypatch, source, capsys):
    cmd = "gci 'a b' | % { \"$_\" }"
    run(monkeypatch, source, "--allow-input", "type", "--stdin", "--focus-at", "40,50", "--enter",
        stdin=cmd + "\n")  # fmt: skip
    assert f"typed {cmd}\n after a click at 40,50" in source.items[-1][0]
    assert "Build succeeded" in capsys.readouterr().out


def test_click_out_of_bounds_and_quiet(monkeypatch, source, capsys):
    with pytest.raises(SystemExit, match="region_out_of_bounds"):
        run(monkeypatch, source, "--allow-input", "click", "20", "9999")
    run(monkeypatch, source, "--allow-input", "click", "20", "30", "--quiet")
    assert source.items[-1][0] == "clicked left at 20,30"
    run(monkeypatch, source, "--allow-input", "click", "21,31", "--quiet")
    assert source.items[-1][0] == "clicked left at 21,31"
    assert capsys.readouterr().out == ""
    with pytest.raises(SystemExit, match="give X Y or X,Y"):
        run(monkeypatch, source, "--allow-input", "click", "20")


def test_read_region_and_bad_region(monkeypatch, source, capsys):
    run(monkeypatch, source, "read", "--region", "0,0,400,60")
    assert "Build succeeded" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        run(monkeypatch, source, "read", "--region", "0,0,400")


def test_screenshot_writes_a_png(monkeypatch, source, tmp_path):
    out = tmp_path / "s.png"
    run(monkeypatch, source, "screenshot", str(out))
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
