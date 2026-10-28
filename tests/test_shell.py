"""core/shell.py: the state prompt, the numbered output and the file write, on synthetic
rows as the glyph reader gives them (exact) and as OCR gives them (damaged)."""

from __future__ import annotations

import hashlib

from jharokha.core import shell as sh

STATE = "JK{n} 2030-10-23 18:17:42 {ok} {code} ..\\work\\some-project"


def state(n: int, ok: bool = True, code: int = 0) -> str:
    return STATE.format(n=n, ok=ok, code=code)


def test_setup_is_one_ascii_line_that_ends_with_the_alphabet():
    assert "\n" not in sh.SETUP and sh.SETUP.isascii()
    assert sh.SETUP.endswith("-join [char[]](33..79); -join [char[]](80..126)")
    assert "function prompt" in sh.SETUP and "function jk" in sh.SETUP
    assert "PredictionSource None" in sh.SETUP and "OutputRendering='PlainText'" in sh.SETUP
    assert "TERM" not in sh.SETUP  # git's pager says the terminal is not functional under TERM=dumb
    assert "core.pager=" in sh.SETUP
    assert "function prompt" in sh.UNDO and "\n" not in sh.UNDO


def test_the_prompt_resets_the_exit_code_and_cuts_the_directory():
    assert "$global:LASTEXITCODE=0" in sh.SETUP
    assert f"$d.Length -gt {sh.DIR_WIDTH}" in sh.SETUP and "'..'+" in sh.SETUP


def test_state_rows_are_parsed_exactly():
    rows = ["   1 hello", state(3, False, 2), "> "]
    (p,) = sh.prompts(rows)
    assert (p.n, p.date, p.time, p.ok, p.code, p.row) == (3, "2030-10-23", "18:17:42", False, 2, 1)
    assert p.cwd == "..\\work\\some-project"
    assert sh.prompts(["JK3 2030-10-23 18:17:42 True 0"])[0].cwd == ""  # a root with no dir
    assert sh.prompts(["JK 2030-10-23 18:17:42 True 0 x", "JKx", "  JK3"]) == []


def test_a_prepared_tab_is_told_from_an_ocr_read_too():
    assert sh.prepared([state(1), ">"])
    assert sh.prepared(
        ["lA1 2O26-1O-28 18:17:42 True O ..\\x"]
    )  # OCR before the glyphs are learned
    assert not sh.prepared(["PS C:\\Users\\X>", "Dockerfile"])
    assert not sh.prepared([])


def test_idle_is_the_empty_prompt_at_the_bottom():
    assert sh.idle([state(1), "> "]) is not None
    assert sh.idle([state(1), ">"]) is not None
    assert sh.idle([state(1), "> gc Dockerfile"]) is None  # text typed
    assert sh.idle([state(1), "> ", "output of a running command"]) is None
    assert sh.idle(["   1 out", state(2)]) is None  # input row not on screen yet
    assert sh.idle(["PS C:\\>"]) is None


def test_typed_joins_the_input_rows_wraps():
    rows = [state(1), "> cls; . { git log --oneline } 2>&1 | Out-St", "ream | jk"]
    assert (
        sh.typed(rows, sh.prompts(rows)[0]) == "cls; . { git log --oneline } 2>&1 | Out-Stream | jk"
    )
    assert sh.typed([state(1)], sh.prompts([state(1)])[0]) is None


def test_reply_strips_numbers_and_ends_at_the_next_prompt():
    rows = ["   1 one", "   2 two words", "   3 ", state(2), "> "]
    r = sh.reply(rows, 1)
    assert r.lines == ["one", "two words", ""] and (r.first, r.last) == (1, 3)
    assert r.prompt is not None and r.prompt.n == 2 and r.prompt.ok
    assert sh.reply(["   1 one"], 1).prompt is None  # still running
    assert sh.reply(["   1 one"], 1).lines == ["one"]  # what it printed so far


def test_reply_ignores_the_echo_and_older_prompts_when_nothing_cleared():
    rows = [state(1), "> . { dir } 2>&1 | Out-String -Stream | jk", "   1 a.txt", state(2), "> "]
    r = sh.reply(rows, 1)
    assert r.lines == ["a.txt"] and r.prompt is not None and r.prompt.n == 2
    assert sh.reply(["   1 old", state(1), "> "], 1).prompt is None  # prompt 1 is not 2


def test_a_row_without_the_next_number_continues_the_line_before():
    rows = [
        "   1 a line the terminal cut at the edge",
        "ge of the pane",
        "   2 12 files changed",
        state(2),
        ">",
    ]
    r = sh.reply(rows, 1)
    assert r.lines == ["a line the terminal cut at the edgege of the pane", "12 files changed"]
    # A wrapped continuation that happens to start with a number is no line of its own.
    rows = ["   1 total", "42 bytes", "   2 next", state(2), ">"]
    assert sh.reply(rows, 1).lines == ["total42 bytes", "next"]


def test_two_rows_the_reader_joined_are_split_at_the_next_number():
    rows = ["   1 exactly as wide as the screen   2 and the next", state(2), ">"]
    assert sh.reply(rows, 1).lines == ["exactly as wide as the screen", "and the next"]


def test_a_start_that_scrolled_off_shows_in_first():
    rows = ["  18 r18", "  19 r19", state(2), ">"]
    r = sh.reply(rows, 1)
    assert (r.first, r.last, r.lines) == (18, 19, ["r18", "r19"])
    assert sh.page(12, 6) == "cls; $__jk.GetRange(11, 6) | jk 11 -p"
    assert sh.page(12, 6, base=4) == "cls; $__jk.GetRange(7, 6) | jk 11 -p"


def test_numbering_from_a_base_for_a_file_page():
    rows = ["   5 five", "   6 six", state(2), ">"]
    r = sh.reply(rows, 1, base=4)
    assert (r.first, r.last, r.lines) == (5, 6, ["five", "six"])
    assert sh.reply([state(2), ">"], 1, base=4).first == 5  # nothing printed: no paging


def test_wrapped_lines_dot_source_and_number():
    assert sh.wrapped("git status") == "cls; . { git status } 2>&1 | Out-String -Stream | jk"
    assert sh.wrapped("dir", clear=False) == ". { dir } 2>&1 | Out-String -Stream | jk"
    assert sh.wrapped("gc x", base=4) == "cls; . { gc x } 2>&1 | Out-String -Stream | jk 4"


def test_write_file_reads_the_clipboard_and_prints_the_hash():
    cmd = sh.write_file("C:\\a\\it's.txt")
    assert cmd.startswith(
        "Get-Clipboard -Raw | Set-Content -NoNewline -Encoding utf8 -LiteralPath 'C:\\a\\it''s.txt'"
    )
    assert cmd.endswith("(Get-FileHash -Algorithm SHA256 -LiteralPath 'C:\\a\\it''s.txt').Hash")
    assert "\n" not in cmd
    text = "line one\nline two\n"
    assert sh.sha256(text) == hashlib.sha256(text.encode()).hexdigest()


def test_check_hash_exact_folded_and_mismatch():
    h = "ab" * 32
    assert sh.check_hash([h.upper()], h) == "exact"
    assert sh.check_hash(["AB" * 16 + " " + "ab" * 16], h) == "exact"  # a hash OCR split
    assert sh.check_hash([("a8" * 32)], h) == "folded"  # OCR's 8 for B
    assert sh.check_hash(["cd" * 32], h) == "mismatch" and sh.check_hash([], h) == "mismatch"


def test_looks_like_is_exact_for_the_glyph_reader_and_loose_for_ocr():
    line = "cls; . { gc Dockerfile } 2>&1 | Out-String -Stream | jk"
    assert sh.looks_like(line, line)
    assert sh.looks_like(
        "cls;.{gcDockerfile}2>&1|0ut-String-Stream|jk", line
    )  # spaces dropped, 0 for O
    assert not sh.looks_like(line[:-3], line)  # still arriving
    assert not sh.looks_like("", line)
