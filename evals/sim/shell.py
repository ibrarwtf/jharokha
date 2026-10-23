"""PowerShell in Windows Terminal, simulated: a prompt, typed text on the prompt line, and
Enter running it against a small fixed table of read-only commands over a fake folder.

    Get-Date (a fixed clock), Get-ChildItem / dir / ls / gci, Get-Content / cat / type
    (-Tail, -TotalCount), Write-Output / echo, Get-Location / pwd, Start-Sleep (the prompt
    comes back that much later), Clear-Host / cls, cmd /c exit N (sets $LASTEXITCODE),
    | select -first N / -last N, `;` between statements, string literals joined with +,
    "..." with $var and ${var} expanded, $x = ..., $?, $LASTEXITCODE, $true, $false, $null

so an end marker like `; $x=$?; "KX"+"7R4C:${x}:$LASTEXITCODE"` prints `KX7R4C:True:` as
PowerShell would. Anything else fails with Windows PowerShell's error for an unknown
command. Ctrl+L clears the screen, Ctrl+C abandons the line or a running sleep, Up recalls
the last command; output scrolls up once it passes the bottom, and the wheel scrolls back.
Every line run is logged."""

from __future__ import annotations

import math
import random
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from PIL import ImageFont

from evals.render import CASCADIA, RGB
from evals.screens import PROJECTS, TASKBAR_Y, TERM_BG, TERM_FG, W, _hex, _id, _sentence
from evals.sim.base import Canvas, Mods, SimApp

if TYPE_CHECKING:
    from evals.sim.desktop import Desktop

ERROR: RGB = (231, 72, 86)  # Windows Terminal's red, which PowerShell errors are drawn in
DATE = "Friday, October 23, 2026 9:41:07 AM"
LEFT, TOP, RIGHT = 8, 46, 1150
Out = list[tuple[str, RGB]]


class Failed(Exception):
    """A statement that wrote an error; the error is its output."""

    def __init__(self, out: Out) -> None:
        self.out = out


def _split(s: str, sep: str) -> list[str]:
    """s split at sep where sep is outside quotes."""
    parts, cur, quote = [], "", ""
    for ch in s:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == sep:
            parts.append(cur)
            cur = ""
            continue
        cur += ch
    return [*parts, cur]


def _words(s: str) -> list[str]:
    return re.findall(r'"[^"]*"|\'[^\']*\'|\S+', s)


def _text(v: object) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "True" if v else "False"
    return str(v)


class Shell(SimApp):
    id = "terminal"
    bg = TERM_BG

    def __init__(self, desk: Desktop, seed: int, px: int = 20) -> None:
        super().__init__(desk)
        rng = random.Random(seed * 7 + 1)
        self.cwd = f"C:\\work\\{rng.choice(PROJECTS)}"
        self.px, self.pitch = px, round(px * 1.15)
        adv = math.ceil(ImageFont.truetype(str(CASCADIA), px).getlength("M" * 100) / 100)
        self.cols = (RIGHT - LEFT) // adv
        self.rows = (TASKBAR_Y - 6 - TOP) // self.pitch
        self.out: Out = []  # every line printed, as the shell printed it
        self.input = ""
        self.back = 0  # rows scrolled back from the bottom
        self.running = 0  # a run's number while it sleeps, else 0
        self._runs = 0
        self.vars: dict[str, object] = {}
        self.ok, self.exit_code = True, None
        self.log: list[str] = []  # every line run, as typed
        self.files = _files(rng)

    @property
    def view(self) -> str:
        return "shell"

    @property
    def prompt(self) -> str:
        return f"PS {self.cwd}>"

    # Drawing

    def _rows(self) -> Out:
        lines = list(self.out)
        if not self.running:
            lines.append((f"{self.prompt} {self.input}".rstrip(), TERM_FG))
        elif self.input:
            lines.append((self.input, TERM_FG))  # typed ahead while a command runs
        rows: Out = []
        for text, colour in lines:
            for c in range(0, max(1, len(text)), self.cols):
                rows.append((text[c : c + self.cols], colour))
        return rows

    def draw(self, c: Canvas) -> None:
        c.rect((0, 0, W, 38), (32, 32, 32))
        c.rect((0, 38, W, 1), (64, 64, 64))
        c.text(14, 10, "Windows PowerShell", 12, TERM_FG)
        rows = self._rows()
        self.back = min(self.back, max(0, len(rows) - self.rows))
        end = len(rows) - self.back
        shown = rows[max(0, end - self.rows) : end]
        for i, (text, colour) in enumerate(shown):
            c.text(LEFT, TOP + i * self.pitch, text, self.px, colour, font="Cascadia Mono")
        if len(rows) > self.rows:
            track = TASKBAR_Y - 20 - TOP
            h = max(20, track * self.rows // len(rows))
            y = TOP + (track - h) * (end - self.rows) // max(1, len(rows) - self.rows)
            c.rect((1156, y, 5, h), (110, 110, 110))

    # Input

    def scroll(self, x: int, y: int, ticks: int) -> None:
        self.back = max(0, self.back + 3 * ticks)

    def char(self, ch: str) -> None:
        self.input += ch
        self.back = 0

    def paste(self, text: str) -> None:
        # PSReadLine keeps a pasted newline in the line instead of running it.
        self.input += text.replace("\r\n", "\n").replace("\n", " ")
        self.back = 0

    def key(self, key: str, mods: Mods) -> None:
        ctrl = "ctrl" in mods
        if ctrl and key == "l":
            self.out = []
        elif ctrl and key == "c":
            if self.running:
                self.running = 0
            else:
                self.out.append((f"{self.prompt} {self.input}".rstrip(), TERM_FG))
            self.input = ""
        elif key == "backspace":
            self.input = self.input[:-1]
        elif key == "esc":
            self.input = ""
        elif key == "up" and self.log:
            self.input = self.log[-1]
        elif key in ("pageup", "pagedown"):
            self.back = max(0, self.back + (self.rows if key == "pageup" else -self.rows))
        elif key == "enter" and not self.running:
            self.enter()
        else:
            return
        if key != "pageup" and key != "pagedown":
            self.back = 0

    def enter(self) -> None:
        line, self.input = self.input, ""
        self.out.append((f"{self.prompt} {line}".rstrip(), TERM_FG))
        if not line.strip():
            return
        self.log.append(line)
        self.event("command", line)
        self._runs += 1
        self.running = self._runs
        self._go(line, [s for s in _split(line, ";") if s.strip()], self._runs)

    def _go(self, line: str, todo: list[str], run: int) -> None:
        """Run statements until one sleeps; the rest run when it wakes."""
        if self.running != run:
            return  # Ctrl+C
        while todo:
            stmt = todo.pop(0)
            sleep = re.fullmatch(
                r"\s*(?:Start-Sleep|sleep)\s+(?:-(s|Seconds|m|Milliseconds)\s+)?([\d.]+)\s*",
                stmt,
                re.IGNORECASE,
            )
            if sleep:
                ms = (sleep.group(1) or "s").lower().startswith("m")
                secs = float(sleep.group(2)) / (1000 if ms else 1)
                self.ok = True
                self.desk.after(secs, lambda: self._go(line, todo, run))
                return
            try:
                out = self.statement(stmt.strip(), line)  # Clear-Host replaces self.out
                self.ok = True
            except Failed as e:
                out = e.out
                self.ok = False
            self.out += out
        self.running = 0

    # PowerShell, the little of it the simulator knows

    def statement(self, stmt: str, line: str) -> Out:
        if m := re.fullmatch(r"\$(\w+)\s*=\s*(.+)", stmt):
            self.vars[m.group(1).lower()] = self.value(m.group(2), stmt, line)
            return []
        first, *pipes = _split(stmt, "|")
        out = self.command(first.strip(), line)
        for p in pipes:
            words = _words(p.strip())
            name = words[0].lower() if words else ""
            m = re.fullmatch(r"-(first|last)", words[1].lower()) if len(words) == 3 else None
            if name not in ("select", "select-object") or not m or not words[2].isdigit():
                raise Failed(self.unknown(p.strip(), line))
            n = int(words[2])
            out = out[:n] if m.group(1) == "first" else out[-n:]
        return out

    def command(self, stmt: str, line: str) -> Out:
        if not stmt:
            return []
        if stmt[0] in "\"'$(" or stmt[0].isdigit():
            v = self.value(stmt, stmt, line)
            return [] if v is None else [(_text(v), TERM_FG)]
        name, *args = _words(stmt)
        fn = self.COMMANDS.get(name.lower())
        if fn is None:
            raise Failed(self.unknown(stmt, line))
        return fn(self, args, stmt, line)

    def value(self, expr: str, stmt: str, line: str) -> object:
        vals = [self.operand(p.strip(), stmt, line) for p in _split(expr, "+")]
        if all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
            return sum(vals)  # type: ignore[arg-type]
        if len(vals) == 1:
            return vals[0]
        return "".join(_text(v) for v in vals)

    def operand(self, s: str, stmt: str, line: str) -> object:
        if len(s) >= 2 and s[0] == s[-1] == "'":
            return s[1:-1]
        if len(s) >= 2 and s[0] == s[-1] == '"':
            return re.sub(
                r"\$\{(\w+|\?)\}|\$(\w+|\?)",
                lambda m: _text(self.var(m.group(1) or m.group(2))),
                s[1:-1],
            )
        if s.startswith("$") and re.fullmatch(r"\$(\w+|\?)", s):
            return self.var(s[1:])
        if re.fullmatch(r"-?\d+", s):
            return int(s)
        raise Failed(self.unknown(stmt, line))

    def var(self, name: str) -> object:
        name = name.lower()
        fixed = {"?": self.ok, "lastexitcode": self.exit_code, "true": True, "false": False}
        if name in fixed:
            return fixed[name]
        return self.vars.get(name)

    def error(self, stmt: str, line: str, message: str, category: str, fqid: str) -> Out:
        """Windows PowerShell's error record, as it prints one."""
        col = line.find(stmt) + 1
        lines = [
            message,
            f"At line:1 char:{col}",
            f"+ {stmt}",
            "+ " + "~" * len(stmt),
            f"    + CategoryInfo          : {category}",
            f"    + FullyQualifiedErrorId : {fqid}",
        ]
        return [(t, ERROR) for t in lines]

    def unknown(self, stmt: str, line: str) -> Out:
        name = _words(stmt)[0] if _words(stmt) else stmt
        return self.error(
            stmt,
            line,
            f"{name} : The term '{name}' is not recognized as the name of a cmdlet, function, "
            "script file, or operable program. Check the spelling of the name, or if a path "
            "was included, verify that the path is correct and try again.",
            f"ObjectNotFound: ({name}:String) [], CommandNotFoundException",
            "CommandNotFoundException",
        )

    def _path(self, args: list[str]) -> str | None:
        plain = [a.strip("\"'") for a in args if not a.startswith("-")]
        return plain[0] if plain else None

    def get_date(self, args: list[str], stmt: str, line: str) -> Out:
        return [("", TERM_FG), (DATE, TERM_FG), ("", TERM_FG), ("", TERM_FG)]

    def get_location(self, args: list[str], stmt: str, line: str) -> Out:
        return [(t, TERM_FG) for t in ("", "Path", "----", self.cwd, "", "")]

    def get_childitem(self, args: list[str], stmt: str, line: str) -> Out:
        path = self._path(args)
        if path not in (None, ".", self.cwd):
            assert path is not None
            full = f"{self.cwd}\\{path}"
            raise Failed(
                self.error(
                    stmt,
                    line,
                    f"Get-ChildItem : Cannot find path '{full}' because it does not exist.",
                    f"ObjectNotFound: ({full}:String) [Get-ChildItem], ItemNotFoundException",
                    "PathNotFound,Microsoft.PowerShell.Commands.GetChildItemCommand",
                )
            )
        rows = ["", f"    Directory: {self.cwd}", "", ""]
        rows += ["Mode                 LastWriteTime         Length Name"]
        rows += ["----                 -------------         ------ ----"]
        for i, (name, body) in enumerate(sorted(self.files.items())):
            size = sum(len(t) + 2 for t in body)
            rows.append(f"-a----        10/{20 + i}/2026   9:{14 + 7 * i} AM {size:>14} {name}")
        return [(t, TERM_FG) for t in [*rows, "", ""]]

    def get_content(self, args: list[str], stmt: str, line: str) -> Out:
        path = self._path(args)
        lower = [a.lower() for a in args]
        body = self.files.get((path or "").lower().removeprefix(".\\"))
        if body is None:
            full = f"{self.cwd}\\{path}"
            raise Failed(
                self.error(
                    stmt,
                    line,
                    f"Get-Content : Cannot find path '{full}' because it does not exist.",
                    f"ObjectNotFound: ({full}:String) [Get-Content], ItemNotFoundException",
                    "PathNotFound,Microsoft.PowerShell.Commands.GetContentCommand",
                )
            )
        for flag, cut in (("-tail", lambda b, n: b[-n:]), ("-totalcount", lambda b, n: b[:n])):
            if flag in lower and lower.index(flag) + 1 < len(args):
                body = cut(body, int(args[lower.index(flag) + 1]))
        return [(t, TERM_FG) for t in body]

    def write_output(self, args: list[str], stmt: str, line: str) -> Out:
        return [(_text(self.value(a, stmt, line)), TERM_FG) for a in args]

    def clear_host(self, args: list[str], stmt: str, line: str) -> Out:
        self.out = []
        return []

    def cmd(self, args: list[str], stmt: str, line: str) -> Out:
        m = re.fullmatch(r"/c exit (\d+)", " ".join(args).lower())
        self.exit_code = int(m.group(1)) if m else 0
        if self.exit_code:
            raise Failed([])
        return []

    COMMANDS: dict[str, Callable[[Shell, list[str], str, str], Out]] = {
        "get-date": get_date,
        "get-location": get_location,
        "pwd": get_location,
        "get-childitem": get_childitem,
        "dir": get_childitem,
        "ls": get_childitem,
        "gci": get_childitem,
        "get-content": get_content,
        "cat": get_content,
        "type": get_content,
        "gc": get_content,
        "write-output": write_output,
        "echo": write_output,
        "clear-host": clear_host,
        "cls": clear_host,
        "clear": clear_host,
        "cmd": cmd,
    }


def _files(rng: random.Random) -> dict[str, list[str]]:
    """The fake folder: names lower case, as Get-Content looks them up."""
    log = []
    for _ in range(12):
        p, code = rng.randint(1, 15), rng.choice([200, 200, 400, 429])
        level = "ERROR" if code >= 400 else "INFO"
        t = f"09:{rng.randint(10, 59)}:{rng.randint(10, 59)}"
        log.append(f"2026-10-22 {t} {level} profile {p:02d} status {code} in {rng.randint(1, 9)}s")
    meta = [
        "{",
        f'  "profile": "P{rng.randint(1, 15):02d}",',
        f'  "ticket": "{_id(rng)}",',
        f'  "build": "{_hex(rng, 7)}"',
        "}",
    ]
    profiles = ["profile,status,seconds"] + [
        f"P{i:02d},{rng.choice([200, 400])},{rng.randint(1, 9)}" for i in range(1, 6)
    ]
    notes = [_sentence(rng, rng.randint(6, 10)) + "." for _ in range(3)]
    return {"run.log": log, "meta.json": meta, "profiles.csv": profiles, "notes.txt": notes}
