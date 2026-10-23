"""Run commands in a remote shell and read the result back by OCR.

A typed command gets a suffix that prints `<token>:<status>` once it has finished; the
prompt is no use for that, as OCR misreads it. Files go in as base64 typed in short
chunks (paste is blocked by policy, and JSON escaping mangles raw text) and come back
checked by their SHA-256.

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


def marked(command: str, token: str, shell: Shell) -> str:
    """command with a suffix that prints `<token>:<status>` when it finishes: PowerShell
    `True:0` ($? and $LASTEXITCODE, empty when no native program has run), cmd and bash the
    exit code. The token is split in two in what is typed, so the echoed command line never
    holds it whole."""
    a, b = token[:2], token[2:]
    if shell == "powershell":
        # Through a variable, since `"$?:"` in a string reads as a drive-scoped name.
        return f'{command.rstrip()}; $jkq=$?; "{a}"+"{b}:${{jkq}}:$LASTEXITCODE"'
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
    """False when the command's line was not found (scrolled off): output is everything
    above the marker and its start may be missing."""


@dataclass(frozen=True)
class Run:
    output: list[Line]
    echoed: bool
    ok: bool | None
    """PowerShell's $?; for cmd and bash, exit code 0. None when OCR left it unreadable."""
    code: int | None


def read_reply(lines: list[Line], token: str) -> Reply | None:
    """The reply to the command marked with token, or None while its marker is not on
    screen. Tolerates case, one misread token character, spaces OCR adds or drops, a
    marker OCR split into pieces or that wrapped onto the next row; markers of other
    tokens (earlier runs) do not count."""
    rows = _rows(lines)
    texts = ["".join("".join(ln.text.split()) for ln in r) for r in rows]
    markers: list[tuple[int, str]] = []
    echoes: list[int] = []
    for i, t in enumerate(texts):
        nxt = texts[i + 1] if i + 1 < len(texts) else ""
        p = _payload(t, token)
        if p == "" and nxt and _payload(nxt, token) is None:
            p = _payload(t + nxt, token)
        if p is not None:
            markers.append((i, p))
        if _echo(t, token):
            echoes.append(i)
        elif nxt and not _echo(nxt, token) and _echo(t + nxt, token):
            echoes.append(i + 1)
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
    return Reply(out, payload, bool(echoes))


def read_run(lines: list[Line], token: str, shell: Shell) -> Run | None:
    """read_reply for a command from marked(), with its status parsed."""
    reply = read_reply(lines, token)
    if reply is None:
        return None
    ok, code = status(reply.payload, shell)
    return Run(reply.output, reply.echoed, ok, code)


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


def _rows(lines: list[Line]) -> list[list[Line]]:
    """Lines on one screen row (centres closer than half a line height), left to right."""
    rows: list[list[Line]] = []
    for ln in sorted(lines, key=lambda ln: ln.box[1]):
        cy = ln.box[1] + ln.box[3] / 2
        if rows:
            r = rows[-1][0]
            if abs(cy - (r.box[1] + r.box[3] / 2)) < max(r.box[3], ln.box[3]) / 2:
                rows[-1].append(ln)
                continue
        rows.append([ln])
    return [sorted(r, key=lambda ln: ln.box[0]) for r in rows]


def _near(a: str, b: str) -> bool:
    return len(a) == len(b) and sum(x != y for x, y in zip(a.upper(), b, strict=True)) <= 1


def _payload(text: str, token: str) -> str | None:
    """What follows the token when text is a marker line; a stray character OCR put in
    front (the caret, a border) is allowed."""
    n = len(token)
    for at in (0, 1):
        if _near(text[at : at + n], token):
            rest = text[at + n :]
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
