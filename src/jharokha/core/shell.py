"""Run commands in a remote shell and read the result back by OCR.

A typed command gets a suffix that prints `<token>:<status>` once it has finished; the
prompt is no use for that, as OCR misreads it. Files go in as base64 in short chunks (JSON
escaping would mangle raw text) and come back checked by their SHA-256.

No get_file: reading a file back means OCR of base64, which is case-sensitive while
terminal OCR confuses case and lookalikes on every few lines, so it needs an
OCR-safe encoding, per-line checksums and paging through screens, none of which has been
tried on a live session yet.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Literal

from jharokha.core.models import Line
from jharokha.core.models import rows as _rows

Shell = Literal["powershell", "cmd", "bash"]

# Without O/0/Q/D, I/1/l, S/5, B/8, Z/2, G/6, U/V, F (read as E) and T (as 7): what
# remains differs only in case under OCR, and case is ignored.
_ALPHABET = "ACEHKMNPRWXY3479"
_SEPS = ":;.,"
# Lookalikes read in place of digits, in a status code, a chunk length or a hash.
_DIGIT = str.maketrans("OoQDIl|!iSsBZzG", "000011111558226")
_HEX = str.maketrans("oqdil|!sbgz", "09011115862")

CHUNK = 400
"""Longest command a put_file step types, so a dropped keystroke costs one retyped step."""


def new_token(n: int = 6) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


# What clears the screen first, on the same line as the command.
CLEAR: dict[str, str] = {"powershell": "cls;", "cmd": "cls & ", "bash": "clear; "}


def marked(command: str, token: str, shell: Shell) -> str:
    """command with a suffix that prints `<token>:<status>` when it finishes: PowerShell
    `True:0` ($? and $LASTEXITCODE), cmd and bash the exit code. The token is split in two
    in what is typed, so the echoed command line never holds it whole."""
    a, b = token[:2], token[2:]
    if shell == "powershell":
        # $LASTEXITCODE is reset first: it keeps the last native program's code, so a
        # cmdlet after `exit 3` would report 3. $? goes outside the quotes, where `"$?:"`
        # would read as a drive-scoped name.
        return f'$LASTEXITCODE=0;{command.rstrip()};"{a}"+"{b}:"+$?+":"+$LASTEXITCODE'
    if shell == "cmd":
        # %errorlevel% on a typed line expands before the line runs; hiding it behind a
        # caret and expanding again with `call` reads it after the command.
        return f"{command.rstrip()} & call echo {a}^{b}:%^errorlevel%"
    return f'{command.rstrip()}; echo "{a}""{b}:$?"'


@dataclass(frozen=True)
class Reply:
    output: list[Line]
    """The lines between the command's echo and the marker, with their boxes."""
    payload: str
    """What the shell printed after `<token>:`, spaces removed, as OCR read it."""
    echoed: bool
    """False when the command's line was not found (scrolled off, or cleared by its own
    cls): output is everything above the marker and its start may be missing."""
    bottom: int = 0
    """The marker row's bottom edge: near the screen's bottom, the output may have
    scrolled its start off the top."""


def _scan(
    lines: list[Line], token: str
) -> tuple[list[list[Line]], list[tuple[int, str]], list[int]]:
    """The rows, the markers of token (row, payload) and the rows of its echo."""
    rows = _rows(lines)
    texts = [_fold("".join("".join(ln.text.split()) for ln in r)) for r in rows]
    markers: list[tuple[int, str]] = []
    echoes: list[int] = []
    for i, t in enumerate(texts):
        nxt = texts[i + 1] if i + 1 < len(texts) else ""
        p = _payload(t, token)
        if p == "" and nxt and _payload(nxt, token) is None:
            p = _payload(t + nxt, token)
        if p is not None:
            # Never the echo too, however near its letters read: `73XXXX:` holds `73`, a
            # gap, and `XXX:`, one letter off `XXXX`.
            markers.append((i, p))
            continue
        if _echo(t, token):
            echoes.append(i)
        elif (
            nxt
            and not _echo(nxt, token)
            # A row ending in the token's first letters above the marker joins with it
            # into what looks like an echo split over two rows, hiding the marker.
            and _payload(nxt, token) is None
            and _echo(t + nxt, token)
        ):
            echoes.append(i + 1)
    return rows, markers, echoes


def read_reply(lines: list[Line], token: str) -> Reply | None:
    """The reply to the command marked with token, or None while its marker is not on
    screen. Tolerates case, one misread token character, lookalikes OCR reads for the
    token's letters (`VV` for W), spaces OCR adds or drops, a stray character in front, a
    marker OCR split into pieces or that wrapped onto the next row; markers of other
    tokens (earlier runs) do not count."""
    rows, markers, echoes = _scan(lines, token)
    if echoes:
        e = echoes[-1]
        after = [m for m in markers if m[0] > e]
        if not after:
            return None
        start, (m, payload) = e + 1, after[0]
    elif markers:
        start, (m, payload) = 0, markers[-1]
    else:
        return None
    out = [ln for r in rows[start:m] for ln in r]
    bottom = max(ln.box[1] + ln.box[3] for ln in rows[m])
    return Reply(out, payload, bool(echoes), bottom)


def echoed(lines: list[Line], token: str) -> bool:
    """Whether the line marked with token is on screen as typed: in the shell's input
    before Enter, or as its echo after."""
    return bool(_scan(lines, token)[2])


def after_echo(lines: list[Line], token: str) -> tuple[list[Line], bool]:
    """What a command marked with token printed so far: the rows after its echo, or all of
    them when the echo is not on screen; and whether it was."""
    rows, _, echoes = _scan(lines, token)
    start = echoes[-1] + 1 if echoes else 0
    return [ln for r in rows[start:] for ln in r], bool(echoes)


def status(payload: str, shell: Shell) -> tuple[bool | None, int | None]:
    if shell == "powershell":
        m = re.match(rf"^([TtFf])[^{_SEPS}]*(?:[{_SEPS}](.*))?$", payload)
        if not m:
            return None, None
        return m.group(1) in "Tt", _int(m.group(2) or "")
    code = _int(payload)
    return (None if code is None else code == 0), code


def confirms(payload: str, expect: str) -> bool:
    """A step's payload says what it should (`3:372`), after folding digit lookalikes."""
    return re.sub(f"[{_SEPS}]", ":", payload.translate(_DIGIT)) == expect


@dataclass(frozen=True)
class Step:
    command: str
    token: str
    expect: str | None
    """The payload the step prints when it worked (chunk index and stored length; index
    only for cmd); None for the last step, checked by read_run and check_hash instead."""


@dataclass(frozen=True)
class PutPlan:
    steps: tuple[Step, ...]
    """Chunks, then the step that decodes, writes and prints the file's SHA-256. Type them
    in order and retype a failed chunk before the next one: chunk 0 starts afresh."""
    sha256: str
    size: int


def put_file(path: str, data: bytes, shell: Shell) -> PutPlan:
    tag = new_token()
    tok = "W" * len(tag)
    over = max(len(_chunk(shell, i, "", tok, tag)) for i in (0, 9999))
    # A multiple of 4, so each chunk is whole base64 even to a decoder that wants lines.
    size = (CHUNK - over) // 4 * 4
    b64 = base64.b64encode(data).decode()
    pieces = [b64[i : i + size] for i in range(0, len(b64), size)] or [""]
    steps = []
    for i, piece in enumerate(pieces):
        t = new_token()
        expect = str(i) if shell == "cmd" else f"{i}:{len(piece)}"
        steps.append(Step(_chunk(shell, i, piece, t, tag), t, expect))
    t = new_token()
    steps.append(Step(_write(shell, path, len(pieces), t, tag), t, None))
    return PutPlan(tuple(steps), hashlib.sha256(data).hexdigest(), len(data))


BATCH = 3000
"""Longest command a write_text step pastes: under the 4 KB a terminal pastes as one line
without asking, with room for the quoting."""
# PowerShell takes each of these as a single quote, so inside a quoted string each is
# doubled, which stands for itself.
_QUOTES = "'‘’‚‛"


def write_text(path: str, text: str, shell: Shell) -> PutPlan:
    """A text file written as its lines, readable as typed: batches of lines appended to a
    list in the shell, each step confirming how many lines the list holds, then the list
    joined with the text's own line ending, written as UTF-8 without a BOM, and its SHA-256
    printed. ValueError for what a line typed into a shell cannot carry: a control
    character other than a tab, a lone CR or LF beside CRLF line endings, or a shell
    other than PowerShell or bash."""
    if shell not in ("powershell", "bash"):
        raise ValueError("write_file takes powershell or bash")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    for n, line in enumerate(lines, 1):
        bad = next((c for c in line if (ord(c) < 32 and c != "\t") or c == "\x7f"), None)
        if bad is not None:
            raise ValueError(f"line {n} holds the control character {bad!r}")
    steps: list[Step] = []
    parts: list[str] = []
    start = 0  # lines the list holds before the batch

    def flush() -> None:
        nonlocal start
        t = new_token()
        i = len(steps)
        a, b = t[:2], t[2:]
        # A batch first cuts the list back to where it starts, so a batch typed again (its
        # confirmation misread) does not add its lines twice.
        if shell == "powershell":
            if i == 0:
                init = "$jkw=[Collections.Generic.List[string]]::new()"
            else:
                init = f"if($jkw.Count -gt {start}){{$jkw.RemoveRange({start},$jkw.Count-{start})}}"
            mark = f'"{a}"+"{b}:{i}:"+$jkw.Count'
        else:
            init = "jkw=()" if i == 0 else f'jkw=("${{jkw[@]:0:{start}}}")'
            mark = f'echo "{a}""{b}:{i}:${{#jkw[@]}}"'
        start += len(parts)
        steps.append(Step("; ".join([init, *parts, mark]), t, f"{i}:{start}"))
        parts.clear()

    for n, line in enumerate(lines, 1):
        q = _quoted(line, shell)
        stmt = f"$jkw.Add({q})" if shell == "powershell" else f"jkw+=({q})"
        if len(stmt) > BATCH:
            raise ValueError(
                f"line {n} is too long to type as one ({len(line)} characters, quotes and tabs "
                "count more): put_file takes any bytes"
            )
        if parts and sum(len(x) + 2 for x in parts) + len(stmt) > BATCH:
            flush()
        parts.append(stmt)
    flush()
    t = new_token()
    if shell == "powershell":
        q = path.replace("'", "''")
        sep = '"`r`n"' if nl == "\r\n" else '"`n"'
        cmd = (
            f"$jkp=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath('{q}'); "
            f"[IO.File]::WriteAllText($jkp,($jkw -join {sep}),[Text.UTF8Encoding]::new($false)); "
            "(Get-FileHash -A SHA256 -LiteralPath $jkp).Hash"
        )
    else:
        q = "'" + path.replace("'", "'\\''") + "'"
        sep = "\\r\\n" if nl == "\r\n" else "\\n"
        cmd = (
            f"{{ printf %s \"${{jkw[0]}}\"; [ ${{#jkw[@]}} -gt 1 ] && printf '{sep}%s' "
            f'"${{jkw[@]:1}}"; }} > {q}; sha256sum {q} | cut -c1-64'
        )
    steps.append(Step(marked(cmd, t, shell), t, None))
    data = text.encode("utf-8")
    return PutPlan(tuple(steps), hashlib.sha256(data).hexdigest(), len(data))


def _quoted(s: str, shell: Shell) -> str:
    """s as a quoted shell string, a tab joined in as an expression: typed, a tab would
    complete a file name."""
    if shell == "powershell":
        quoted = [
            "'" + "".join(c * 2 if c in _QUOTES else c for c in p) + "'" for p in s.split("\t")
        ]
        return '+"`t"+'.join(quoted)
    return "$'\\t'".join("'" + p.replace("'", "'\\''") + "'" for p in s.split("\t"))


def check_hash(lines: list[Line], expected: str) -> Literal["exact", "folded", "mismatch"]:
    """Whether the lines show the expected SHA-256: exactly (ignoring case and spaces, so a
    hash OCR split or wrapped still counts), only after folding lookalikes (0/O/D, 1/I/l,
    5/S, 8/B, 6/G, 2/Z), or not at all."""
    seen = "".join("".join(ln.text.split()) for ln in lines).lower()
    want = expected.lower()
    if want in seen:
        return "exact"
    return "folded" if want.translate(_HEX) in seen.translate(_HEX) else "mismatch"


def _chunk(shell: Shell, i: int, piece: str, token: str, tag: str) -> str:
    a, b = token[:2], token[2:]
    if shell == "powershell":
        init = "$jkb=@{}; " if i == 0 else ""
        return f'{init}$jkb[{i}]=\'{piece}\'; "{a}"+"{b}:{i}:$($jkb[{i}].Length)"'
    if shell == "bash":
        init = "jkb=(); " if i == 0 else ""
        return f'{init}jkb[{i}]=\'{piece}\'; echo "{a}""{b}:{i}:${{#jkb[{i}]}}"'
    init = f'del "%TEMP%\\jk{tag}_*.b64" 2>nul & ' if i == 0 else ""
    # The redirect goes first so a digit ending the piece is not read as a handle.
    return f'{init}>"%TEMP%\\jk{tag}_{i:04}.b64" echo({piece}& echo {a}^{b}:{i}'


def _write(shell: Shell, path: str, n: int, token: str, tag: str) -> str:
    if shell == "powershell":
        q = path.replace("'", "''")
        # Relative to the shell's location; .NET alone would resolve it against the
        # process's working directory.
        cmd = (
            f"$jkp=$ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath('{q}'); "
            f"[IO.File]::WriteAllBytes($jkp,[Convert]::FromBase64String((-join $jkb[0..{n - 1}])"
            ")); (Get-FileHash -A SHA256 -LiteralPath $jkp).Hash"
        )
        return marked(cmd, token, shell)
    if shell == "bash":
        q = "'" + path.replace("'", "'\\''") + "'"
        cmd = f'printf %s "${{jkb[@]}}" | base64 -d > {q} && sha256sum {q} | cut -c1-64'
        return marked(cmd, token, shell)
    tmp = f'"%TEMP%\\jk{tag}'
    cmd = (
        f'copy /b {tmp}_*.b64" {tmp}.b64" >nul && certutil -f -decode {tmp}.b64" "{path}" >nul'
        f' && certutil -hashfile "{path}" SHA256'
    )
    return marked(cmd, token, shell) + f' & del {tmp}*.b64"'


def _near(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a.upper(), b, strict=True)) <= 1


# What OCR reads for the token alphabet's letters at terminal sizes, folded back: two
# glyphs for one (VV for W, IVI or rn for M) would otherwise shift every character after.
_GLYPHS = (("VV", "W"), ("IVI", "M"), ("|\\/|", "M"), ("I-I", "H"), ("|-|", "H"))


def _fold(text: str) -> str:
    out = text
    for a, b in _GLYPHS:
        out = out.replace(a, b)
    return out


def _payload(text: str, token: str) -> str | None:
    """What follows the token when text is a marker line: at its start, after a stray
    character OCR put in front (the caret, a border), or further in when a separator
    follows it (the row ran together with what was left of the prompt)."""
    n = len(token)
    for at in range(0, max(1, len(text) - n + 1)):
        if _near(text[at : at + n], token):
            rest = text[at + n :]
            # Further in, only a status after a separator: an echo whose quotes OCR dropped
            # reads `...KX7R4C:${jkq}...` and is no marker.
            if at > 1 and not re.match(rf"[{_SEPS}][TtFf\d-]", rest):
                continue
            return rest[1:] if rest[:1] and rest[0] in _SEPS else rest
    return None


def _echo(text: str, token: str) -> bool:
    """text holds the typed command's split token: its two parts with the quotes or caret
    between them, which OCR may garble but never removes entirely."""
    a, b = token[:2], token[2:]
    up = text.upper()
    for m in re.finditer(re.escape(a), up):
        for gap in range(1, 6):
            at = m.end() + gap
            if _near(up[at : at + len(b)], b):
                return True
    return False


def _int(s: str) -> int | None:
    s = s.translate(_DIGIT)
    return int(s) if re.fullmatch(r"-?\d+", s) else None
