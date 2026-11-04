"""A PowerShell tab prepared once to announce its own state, so a command's end, status
and output are read off the screen exactly instead of inferred.

The tab's prompt is replaced (in memory only, gone with the tab) by two rows: a state row
`JK<n> <date> <time> <ok> <exit code> <directory>` with a counter that makes every prompt
unique, and an input row `> `. A command has finished when the state row with the next
number is on screen; its `$?` and `$LASTEXITCODE` are on that row; its output is the rows
between. Colours and predictions are turned off, so the terminal draws one ink that the
glyph reader (core.glyphs) matches exactly, and the output of a command goes through a
filter that numbers its lines (`   1 text`) and keeps them in a variable, so a line that
wrapped, a screen that overflowed and the page to fetch next are all told by the numbers.

Nothing of a file's content is ever typed or shown: a file is written from this machine's
clipboard, which the session reads, and checked by its SHA-256.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from jharokha.core.glyphs import ALPHABET_COMMAND

# The prompt's marker; an environment variable lets frames saved under another marker
# still be read.
MARK = os.environ.get("JHAROKHA_MARK", "JK")
"""What a state row starts with."""
DIR_WIDTH = 40
"""Characters of the directory a state row shows: its end, after `..` when cut."""
VAR = "__jk"
"""The variable holding the last command's output lines, for paging."""

# Lookalikes read in place of digits, in a counter or an exit code read by OCR before the
# glyphs are learned.
_DIGIT = str.maketrans("OoQDIl|!iSsBZzG", "000011111558226")
_HEX = str.maketrans("oqdil|!sbgz", "09011115862")

_PROMPT_FN = (
    "function prompt { $ok=$?; $c=[int]$LASTEXITCODE; $global:LASTEXITCODE=0; "
    f"$global:{VAR}n++; $d=$PWD.Path; if($d.Length -gt {DIR_WIDTH})"
    f"{{$d='..'+$d.Substring($d.Length-{DIR_WIDTH - 2})}}; "
    f"\"{MARK}$global:{VAR}n $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $ok $c $d`n> \" }}"
)
# `jk` numbers the lines piped into it from base+1 and, unless paging, keeps them in VAR.
_FILTER_FN = (
    "function jk { param($b=0, [switch]$p) begin { $n=$b; "
    f"if(-not $p){{ $global:{VAR}=[Collections.Generic.List[string]]::new() }} }} "
    f"process {{ $n++; if(-not $p){{ $global:{VAR}.Add($_) }}; '{{0,4}} {{1}}' -f $n,$_ }} }}"
)
_COLOURS = ";".join(
    f"{k}='White'"
    for k in (
        "Command",
        "Parameter",
        "Operator",
        "Variable",
        "String",
        "Number",
        "Member",
        "Type",
        "Comment",
        "Keyword",
        "Default",
        "Error",
        "Selection",
        "InlinePrediction",
        "ContinuationPrompt",
        "Emphasis",
    )
)
_PLAIN = (
    "Set-PSReadLineOption -PredictionSource None; "
    f"Set-PSReadLineOption -Colors @{{{_COLOURS}}}; "
    "$PSStyle.OutputRendering='PlainText'; $env:NO_COLOR='1'; "
    "$env:GIT_CONFIG_PARAMETERS=\"'color.ui=false' 'core.pager='\""
)
SETUP = f"{_PROMPT_FN}; {_FILTER_FN}; {_PLAIN}; cls; {ALPHABET_COMMAND}"
"""The one line that prepares a tab: the state prompt, the numbering filter, one ink, then
the screen cleared and the glyph alphabet printed (core.glyphs learns from it)."""
UNDO = (
    'function prompt { "PS $($executionContext.SessionState.Path.CurrentLocation)'
    "$('>' * ($nestedPromptLevel + 1)) \" }; Remove-Item function:jk; "
    "Set-PSReadLineOption -PredictionSource HistoryAndPlugin; "
    "$PSStyle.OutputRendering='Host'; Remove-Item env:NO_COLOR, env:GIT_CONFIG_PARAMETERS"
)
"""What puts the tab back as it was (colours stay white until the tab is closed)."""

_STATE = re.compile(
    rf"^\s*{MARK}(\d+)\s+(\d{{4}}-\d\d-\d\d)\s+(\d\d:\d\d:\d\d)\s+(True|False)\s+(-?\d+)(?:\s+(\S.*?))?\s*$"
)
# Before the glyphs are learned, Windows OCR reads a state row with digits as lookalikes
# and spaces dropped: enough of its shape to know the tab is prepared.
_STATE_LOOSE = re.compile(r"^\s*[I1l|]?A[0-9OIl|!]+\s*[0-9OIl]{4}-[0-9OIl]{2}-[0-9OIl]{2}")
# The row under a state row is the input row by construction; its `>` alone in a cell the
# glyphs do not know yet goes to the engine, which often reads it as `v`.
_INPUT = re.compile(r"^[>v]\s?(.*)$")
_NUMBERED = re.compile(r"^\s{0,3}(\d+) (.*)$")


@dataclass(frozen=True)
class Prompt:
    n: int
    date: str
    time: str
    ok: bool
    code: int
    cwd: str
    row: int
    """Index of the state row among the rows it was read from."""


def prompts(rows: list[str]) -> list[Prompt]:
    """The state rows among rows, in order."""
    out = []
    for i, r in enumerate(rows):
        if m := _STATE.match(r):
            out.append(Prompt(int(m[1]), m[2], m[3], m[4] == "True", int(m[5]), m[6] or "", i))
    return out


def prepared(rows: list[str]) -> bool:
    """Whether the tab shows a state row, read exactly or by OCR."""
    return any(_STATE.match(r) or _STATE_LOOSE.match(r) for r in rows)


def typed(rows: list[str], p: Prompt) -> str | None:
    """What the input row after prompt p holds (its wraps joined), or None when the row
    is not on screen."""
    if p.row + 1 >= len(rows):
        return None
    m = _INPUT.match(rows[p.row + 1])
    if not m:
        return None
    return m[1] + "".join(rows[p.row + 2 :])


def idle(rows: list[str]) -> Prompt | None:
    """The prompt at the bottom of the screen with nothing typed after it, or None when
    the last rows are not an empty prompt (a command running, text in the input, a
    screen scrolled up)."""
    ps = prompts(rows)
    if not ps:
        return None
    p = ps[-1]
    return p if typed(rows, p) == "" else None


def state(rows: list[str]) -> dict[str, object] | None:
    """What the last prompt on screen says, for a status: its counter, time, the last
    command's status and exit code, the directory, and whether the shell is idle (nothing
    after the prompt). None when no state row is on screen (a tab not prepared)."""
    ps = prompts(rows)
    if not ps:
        return None
    p = ps[-1]
    return {
        "prompt": p.n,
        "time": f"{p.date} {p.time}",
        "ok": p.ok,
        "code": p.code,
        "cwd": p.cwd,
        "idle": typed(rows, p) == "",
    }


@dataclass(frozen=True)
class Reply:
    lines: list[str]
    """The command's output lines, their numbers stripped, in order."""
    first: int
    """The number of the first line on screen: more than base+1 when the start scrolled
    off the top, so the lines before it are fetched by paging."""
    last: int
    """The number of the last line: the count of lines the command printed."""
    prompt: Prompt | None
    """The prompt that ended the command; None while it is still running."""


def reply(rows: list[str], n: int, base: int = 0) -> Reply:
    """What command n printed, numbered from base+1 by the `jk` filter: the numbered rows
    before the state row of prompt n+1 (all rows when it is not on screen yet). A row
    that does not start with the next number continues the line before it (a wrapped
    line); the next number inside a row (two rows the reader joined) splits it."""
    end = next((p.row for p in prompts(rows) if p.n == n + 1), None)
    ended = next((p for p in prompts(rows) if p.n == n + 1), None)
    body = rows[:end] if end is not None else rows
    lines: list[str] = []
    first = 0
    want = 0
    for row in body:
        m = _NUMBERED.match(row)
        if m and (not lines or int(m[1]) == want):
            if not lines:
                first = int(m[1])
            lines.append("")
            want, row = int(m[1]), m[2]
        elif not lines:
            continue  # the echo, an older prompt: nothing numbered yet
        # The row continues the line (one that wrapped), unless the next line's number
        # sits further in: two rows the reader joined, split there.
        while k := re.search(rf"\s{{1,4}}{want + 1} ", row):
            lines[-1] += row[: k.start()]
            lines.append("")
            want, row = want + 1, row[k.end() :]
        lines[-1] += row
    last = first + len(lines) - 1 if lines else base
    return Reply(lines, first if lines else base + 1, last, ended)


def wrapped(command: str, base: int = 0, clear: bool = True) -> str:
    """command as one typed line: dot-sourced (so cd and variables keep their effect),
    its error stream merged, formatted as the console would and numbered by `jk`."""
    tail = f" | Out-String -Stream | jk {base}" if base else " | Out-String -Stream | jk"
    return ("cls; " if clear else "") + ". { " + command.strip() + " } 2>&1" + tail


def page(first: int, count: int, base: int = 0) -> str:
    """The line that shows lines first..first+count-1 of the last command's output again,
    with their numbers, from the variable `jk` kept them in."""
    return f"cls; ${VAR}.GetRange({first - 1 - base}, {count}) | jk {first - 1} -p"


def write_file(path: str) -> str:
    """The command that writes this machine's clipboard to path as UTF-8 without a BOM,
    the text as it is, and prints the file's SHA-256."""
    q = path.replace("'", "''")
    return (
        f"Get-Clipboard -Raw | Set-Content -NoNewline -Encoding utf8 -LiteralPath '{q}'; "
        f"(Get-FileHash -Algorithm SHA256 -LiteralPath '{q}').Hash"
    )


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def check_hash(lines: list[str], expected: str) -> Literal["exact", "folded", "mismatch"]:
    """Whether the lines show the expected SHA-256: exactly (ignoring case and spaces),
    only after folding lookalikes (0/O, 1/I/l, 5/S, 8/B, 6/G, 2/Z: a hash read by OCR),
    or not at all."""
    seen = "".join("".join(ln.split()) for ln in lines).lower()
    want = expected.lower()
    if want in seen:
        return "exact"
    return "folded" if want.translate(_HEX) in seen.translate(_HEX) else "mismatch"


def looks_like(a: str, b: str) -> bool:
    """Whether a, read off the screen, is b typed: the same, or the same after the
    spaces are dropped and lookalikes folded (a line read by OCR)."""
    if a == b:
        return True
    fa = "".join(a.split()).translate(_DIGIT).lower()
    fb = "".join(b.split()).translate(_DIGIT).lower()
    return fa == fb
