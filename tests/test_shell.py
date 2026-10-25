"""core/shell.py on synthetic OCR lines, damaged the way terminal OCR damages them."""

from __future__ import annotations

import base64
import hashlib
import re

import pytest

from jharokha.core.models import Line, Word
from jharokha.core.shell import (
    CHUNK,
    Shell,
    check_hash,
    confirms,
    marked,
    new_token,
    put_file,
    read_reply,
    status,
)

TOKEN = "KX7R4C"
PROMPT = "PS C:\\Users\\X>"


class Run:
    """read_reply with its status parsed, as the runner's run step reads it."""

    def __init__(self, output: list[Line], echoed: bool, ok: bool | None, code: int | None):
        self.output, self.echoed, self.ok, self.code = output, echoed, ok, code


def read_run(lines: list[Line], token: str, shell: Shell) -> Run | None:
    reply = read_reply(lines, token)
    if reply is None:
        return None
    return Run(reply.output, reply.echoed, *status(reply.payload, shell))


def screen(*rows: str | tuple[str, ...]) -> list[Line]:
    """One row per argument, 20 px apart; a tuple is a row OCR split into pieces."""
    out = []
    for i, row in enumerate(rows):
        x = 10
        for piece in (row,) if isinstance(row, str) else row:
            box = (x, 20 * i, 8 * len(piece), 14)
            out.append(Line(piece, box, (Word(piece, box),)))
            x += 8 * len(piece) + 8
    return out


def texts(lines: list[Line]) -> list[str]:
    return [ln.text for ln in lines]


def echo(command: str = "Get-ChildItem", shell: str = "powershell") -> str:
    return PROMPT + " " + marked(command, TOKEN, shell)  # type: ignore[arg-type]


def test_token_is_short_and_free_of_lookalikes():
    for _ in range(200):
        t = new_token()
        assert len(t) == 6
        assert not set(t) & set("O0QDI1lS5B8Z2G6UVFT")
    assert len({new_token() for _ in range(50)}) == 50


@pytest.mark.parametrize("shell", ["powershell", "cmd", "bash"])
def test_the_echo_never_holds_the_joined_token(shell):
    for _ in range(50):
        t = new_token()
        typed = marked("dir", t, shell)
        assert t not in typed.upper()
        assert t in re.sub(r"[\"+^]", "", typed)


def test_suffixes():
    assert marked("dir  ", TOKEN, "powershell") == (
        '$LASTEXITCODE=0;dir;"KX"+"7R4C:"+$?+":"+$LASTEXITCODE'
    )
    assert marked("dir", TOKEN, "cmd") == "dir & call echo KX^7R4C:%^errorlevel%"
    assert marked("ls", TOKEN, "bash") == 'ls; echo "KX""7R4C:$?"'


def test_reads_output_between_echo_and_marker_and_keeps_boxes():
    lines = screen(PROMPT, echo(), "a.txt", "b.txt", "KX7R4C:True:0", PROMPT)
    run = read_run(lines, TOKEN, "powershell")
    assert run is not None
    assert texts(run.output) == ["a.txt", "b.txt"]
    assert run.output[0] is lines[2] and run.output[0].box == (10, 40, 40, 14)
    assert (run.echoed, run.ok, run.code) == (True, True, 0)


def test_none_while_running():
    assert read_reply(screen(PROMPT, echo(), "a.txt"), TOKEN) is None
    assert read_reply(screen("old output"), TOKEN) is None


def test_case_and_lookalikes_in_the_marker():
    lines = screen(echo(), "out", "kx7r4c : FaIse : 1")
    run = read_run(lines, TOKEN, "powershell")
    assert run is not None and (run.ok, run.code) == (False, 1)
    # One misread token character, an exit code read with letters, `;` for `:`.
    run = read_run(screen(echo(), "out", "KX7RAC;True;O"), TOKEN, "powershell")
    assert run is not None and (run.ok, run.code) == (True, 0)
    run = read_run(screen(echo(), "KxlR4C:True:-l07374l8l9"), TOKEN, "powershell")
    assert run is not None and run.code == -1073741819


def test_two_misread_characters_are_another_token():
    assert read_reply(screen(echo(), "out", "KX7RAA:True:0"), TOKEN) is None


def test_spaces_merged_or_split():
    run = read_run(screen(echo(), "out", "K X7R 4C: True:"), TOKEN, "powershell")
    assert run is not None and (run.ok, run.code) == (True, None)
    run = read_run(screen(echo(), "out", "KX7R4C:True0"), TOKEN, "powershell")
    assert run is not None and run.ok is True


def test_marker_split_into_pieces_on_one_row():
    lines = screen(echo(), "out", ("KX7R", "4C:True", ":0"), PROMPT)
    run = read_run(lines, TOKEN, "powershell")
    assert run is not None and texts(run.output) == ["out"] and run.code == 0


def test_marker_wrapped_onto_the_next_row():
    run = read_run(screen(echo(), "out", "KX7R4C:", "False:2", PROMPT), TOKEN, "powershell")
    assert run is not None and texts(run.output) == ["out"]
    assert (run.ok, run.code) == (False, 2)


def test_stray_character_before_the_marker():
    run = read_run(screen(echo(), "out", "|KX7R4C:True:0"), TOKEN, "powershell")
    assert run is not None and texts(run.output) == ["out"]


def test_echo_with_ocr_damage():
    # `|` read as `I`, quotes lost, spaces moved, case changed.
    damaged = 'PS C:\\Users\\X> Get-Process I select -first 3; $jkq=$?; "kx" + "7r4c:${jkq}'
    run = read_run(screen("before", damaged, "p1", "KX7R4C:True:0"), TOKEN, "powershell")
    assert run is not None and run.echoed and texts(run.output) == ["p1"]
    run = read_run(screen("before", "PS> x; kx+7R4C:", "p1", "KX7R4C:True:"), TOKEN, "powershell")
    assert run is not None and run.echoed and texts(run.output) == ["p1"]


def test_echo_wrapped_over_rows():
    rows = ['PS C:\\> Get-ChildItem -Recurse | Measure; $jkq=$?; "KX"+"7', "R4C:${jkq}:$LAST"]
    lines = screen(*rows, "EXITCODE", "Count : 3", "KX7R4C:True:")
    # The wrapped tail of the echo is not output, apart from what follows the token.
    run = read_run(lines, TOKEN, "powershell")
    assert run is not None and run.echoed
    assert texts(run.output) == ["EXITCODE", "Count : 3"]


def test_echo_scrolled_off():
    lines = screen("line 7", "line 8", "KX7R4C:True:0", PROMPT)
    run = read_run(lines, TOKEN, "powershell")
    assert run is not None and not run.echoed
    assert texts(run.output) == ["line 7", "line 8"]


def test_stale_marker_from_an_earlier_run_does_not_count():
    old = screen(PROMPT + " dir; ...", "x", "MPW9HN:True:0", PROMPT)
    lines = old + [
        Line(ln.text, (ln.box[0], ln.box[1] + 80, ln.box[2], ln.box[3]), ln.words)
        for ln in screen(echo(), "busy")
    ]
    assert read_reply(lines, TOKEN) is None
    # Nor when the new command's echo has scrolled off.
    assert read_reply(screen("MPW9HN:True:0", "busy"), TOKEN) is None


def test_earlier_marker_of_the_same_token_above_the_echo_is_ignored():
    lines = screen(echo(), "first", "KX7R4C:True:0", echo(), "second", "KX7R4C:False:1")
    run = read_run(lines, TOKEN, "powershell")
    assert run is not None and texts(run.output) == ["second"] and run.ok is False
    assert read_reply(lines[:5], TOKEN) is None


def test_output_rows_in_order_with_pieces_left_to_right():
    lines = screen(echo(), ("Name", "Length"), ("a.txt", "12"), "KX7R4C:True:")
    shuffled = [lines[3], lines[0], lines[4], lines[2], lines[1], lines[5]]
    run = read_run(shuffled, TOKEN, "powershell")
    assert run is not None and texts(run.output) == ["Name", "Length", "a.txt", "12"]


def test_cmd_and_bash_status():
    run = read_run(screen(echo("dir", "cmd"), "x", "KX7R4C:O"), TOKEN, "cmd")
    assert run is not None and (run.ok, run.code) == (True, 0)
    run = read_run(screen(echo("false", "bash"), "KX7R4C:l27"), TOKEN, "bash")
    assert run is not None and (run.ok, run.code, run.output) == (False, 127, [])
    assert status("", "bash") == (None, None)
    assert status("garbled", "powershell") == (None, None)


def test_confirms_folds_digits():
    assert confirms("3:372", "3:372")
    assert confirms("3;3T2".replace("T", "7"), "3:372")
    assert confirms("l2.4OO", "12:400")
    assert not confirms("3:371", "3:372")


@pytest.mark.parametrize("shell", ["powershell", "cmd", "bash"])
def test_put_file_plan(shell):
    data = bytes(range(256)) * 40
    plan = put_file("C:\\tmp\\it's.bin", data, shell)
    assert plan.sha256 == hashlib.sha256(data).hexdigest() and plan.size == len(data)
    chunks, last = plan.steps[:-1], plan.steps[-1]
    assert all(len(s.command) <= CHUNK for s in chunks)
    assert last.expect is None and last.token not in last.command
    assert len({s.token for s in plan.steps}) == len(plan.steps)
    pieces = [re.search(r"(?:='|echo\()([A-Za-z0-9+/=]*)", s.command) for s in chunks]
    assert all(pieces)
    joined = "".join(m.group(1) for m in pieces if m)
    assert base64.b64decode(joined) == data
    assert all(len(m.group(1)) % 4 == 0 for m in pieces[:-1] if m)
    for i, s in enumerate(chunks):
        assert s.token not in s.command
        n = len(pieces[i].group(1)) if pieces[i] else -1  # type: ignore[union-attr]
        assert s.expect == (str(i) if shell == "cmd" else f"{i}:{n}")


def test_put_file_quotes_the_path():
    assert (
        "'C:\\tmp\\it''s.bin'"
        in put_file("C:\\tmp\\it's.bin", b"x", "powershell").steps[-1].command
    )
    assert "'/tmp/it'\\''s'" in put_file("/tmp/it's", b"x", "bash").steps[-1].command


def test_put_file_empty():
    plan = put_file("e", b"", "powershell")
    assert len(plan.steps) == 2 and plan.steps[0].expect == "0:0"


def test_put_file_10kb_size():
    plan = put_file("f", bytes(10_000), "powershell")
    typed = sum(len(s.command) + 1 for s in plan.steps)
    assert len(plan.steps) <= 45 and typed < 16_500


H = hashlib.sha256(b"jharokha").hexdigest()


def test_check_hash_exact_case_and_spaces():
    assert check_hash(screen(H.upper()), H) == "exact"
    assert check_hash(screen(("SHA256 hash of f:",), H[:30] + " " + H[30:]), H) == "exact"
    assert check_hash(screen(H[:40], H[40:]), H) == "exact"


def test_check_hash_folded_lookalikes():
    damaged = H.upper().replace("0", "O").replace("1", "l").replace("8", "B").replace("5", "S")
    assert damaged.lower() != H
    assert check_hash(screen(damaged), H) == "folded"


def test_check_hash_mismatch():
    other = hashlib.sha256(b"other").hexdigest()
    assert check_hash(screen(other), H) == "mismatch"
    assert check_hash(screen(H[:63]), H) == "mismatch"
    assert check_hash([], H) == "mismatch"


def test_put_file_round_trip_on_screen():
    """The last step's reply, as OCR might read it, verifies the plan's hash."""
    plan = put_file("f.bin", b"hello", "powershell")
    last = plan.steps[-1]
    shown = PROMPT + " " + last.command
    lines = screen(
        shown[:90], shown[90:180], shown[180:], plan.sha256.upper(), last.token + ":True:"
    )
    run = read_run(lines, last.token, "powershell")
    assert run is not None and run.echoed and run.ok
    assert check_hash(run.output, plan.sha256) == "exact"
