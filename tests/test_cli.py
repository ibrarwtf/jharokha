"""The shell commands: the tools' verbs, called in-process, so they answer and are guarded as
the tools are; input needs --allow-input."""

from __future__ import annotations

import io
import json
import sys

import pytest

from jharokha import __main__ as cli


def run(monkeypatch, source, *argv: str, stdin: str = "") -> None:
    monkeypatch.setattr(cli, "_source", lambda: source)
    monkeypatch.setattr(sys, "argv", ["jharokha", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    cli.main()


def test_act_needs_allow_input(monkeypatch, source):
    for argv in (["act", "click", "1,1"], ["act", "type", "--text", "x"], ["act", "scroll"]):
        with pytest.raises(SystemExit, match="input_disabled"):
            run(monkeypatch, source, *argv)


def test_type_from_stdin_with_a_target_and_enter(monkeypatch, source, capsys):
    cmd = "gci 'a b' | % { \"$_\" }"
    run(monkeypatch, source, "--allow-input", "act", "type", "40,50", "--stdin", "--enter",
        stdin=cmd + "\n")  # fmt: skip
    assert f"typed {cmd}\n after a click at 40,50" in source.items[-1][0]
    assert "op=type at=[40,50]" in capsys.readouterr().out


def test_act_is_guarded_and_traced(monkeypatch, source, tmp_path):
    path = tmp_path / "t.jsonl"
    with pytest.raises(SystemExit, match="wrong_app: expected teams in front"):
        run(monkeypatch, source, "--allow-input", "--trace", str(path), "--trace-text",
            "act", "type", "--text", "hi", "--expect", "teams")  # fmt: skip
    assert not any(t.startswith("typed") for t, _, _ in source.items)
    run(monkeypatch, source, "--allow-input", "--trace", str(path), "act", "press", "--keys",
        "esc", "--quiet")  # fmt: skip
    recs = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    assert [(r["action"], r["result"]) for r in recs] == [("type", "wrong_app"), ("press", "sent")]
    assert recs[0]["text"] == "hi" and recs[1]["front_app"] == "generic.screen"


def test_click_a_point_or_a_text(monkeypatch, source, capsys):
    with pytest.raises(SystemExit, match="region_out_of_bounds"):
        run(monkeypatch, source, "--allow-input", "act", "click", "20,9999")
    run(monkeypatch, source, "--allow-input", "act", "click", "20,30", "--quiet")
    assert source.items[-1][0] == "clicked left at 20,30"
    run(monkeypatch, source, "--allow-input", "act", "click", "second column", "--quiet")
    assert source.items[-1][0].startswith("clicked left at 3")
    assert capsys.readouterr().out.startswith("op=click")


def test_read_find_wait_and_bad_region(monkeypatch, source, capsys):
    run(monkeypatch, source, "read", "--region", "0,0,400,60")
    assert "Build succeeded" in capsys.readouterr().out
    run(monkeypatch, source, "find", "shipped")
    assert json.loads(capsys.readouterr().out)["matches"][0]["text"] == "shipped"
    run(monkeypatch, source, "wait", "--text", "Order")
    assert json.loads(capsys.readouterr().out)["found"] is True
    with pytest.raises(SystemExit):
        run(monkeypatch, source, "read", "--region", "0,0,400")
    run(monkeypatch, source, "status")
    assert json.loads(capsys.readouterr().out)["app"] == "generic"


def test_do_shows_a_plan_without_input(monkeypatch, source, capsys):
    run(monkeypatch, source, "do", "teams.draft", "--args", '{"chat": "Ana", "text": "hi"}',
        "--dry-run")  # fmt: skip
    assert '"plan"' in capsys.readouterr().out
    with pytest.raises(SystemExit, match="input_disabled"):
        run(monkeypatch, source, "do", "teams.draft", "--args", '{"chat": "Ana", "text": "hi"}')


def test_measure_writes_calibration_to_apps_toml(monkeypatch, source, tmp_path, capsys):
    from jharokha.core import apps

    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    path = tmp_path / "apps.toml"
    run(monkeypatch, source, "--apps", str(path), "measure", "mine", "--anchor", "Build")
    cat = apps.load(path)
    assert cat.apps["mine"].views["main"].cues[0].text == ("Build",)
    regions = cat.calibration[("mine", "main")].variants[0].regions
    run(monkeypatch, source, "--apps", str(path), "measure", "mine", "--anchor", "Build",
        "--focus", next(iter(regions)))  # fmt: skip
    # Remeasuring replaces the view's calibration and keeps the focus set before.
    run(monkeypatch, source, "--apps", str(path), "measure", "mine", "--anchor", "Build")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("format = 1") and text.count("[calibration.mine.main]") == 1
    assert text.count("written by") == 1
    assert apps.load(path).calibration[("mine", "main")].focus == next(iter(regions))
    # A built-in app is calibrated in its first view.
    run(monkeypatch, source, "--apps", str(path), "measure", "terminal", "--anchor", "Build")
    assert ("terminal", "shell") in apps.load(path).calibration
    assert "wrote" in capsys.readouterr().out


def test_screenshot_writes_a_png(monkeypatch, source, tmp_path, capsys):
    out = tmp_path / "s.png"
    run(monkeypatch, source, "screenshot", str(out))
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert json.loads(capsys.readouterr().out)["out"] == str(out)
