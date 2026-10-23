"""Which shell commands only read, so terminal.run may type them under the default allow.

An allowlist, not a blocklist. The line is split where the shell would run a new command
(`;`, `|`, `&&`, `||`, newlines; `&` too in cmd), and each command's name must be one of
those below, with arguments its rule accepts. The line must also hold nothing that runs
code of its own or writes: script blocks, subexpressions, parentheses, the call operator,
escapes, redirections, an assignment, verbs that change state, -Force. Quotes are honoured
for operators (a `;` or `>` inside quotes is text), not for the names of what changes
state, which are refused even inside a string.

When the shell might read the line differently from this lexer, the answer is no: a wrong
refusal costs an allow="irreversible" and the user's say-so; a wrong yes could cost a file.
The user's command is classified before core.shell.marked appends jharokha's end marker."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

SHELLS = ("powershell", "cmd", "bash")


@dataclass(frozen=True)
class Verdict:
    read_only: bool
    reason: str = ""
    """Why not, when it is not."""
    commands: tuple[str, ...] = ()
    """What the line runs, by canonical name (`gci` is Get-ChildItem), when read-only."""

    def describe(self) -> str:
        if self.read_only:
            return "read-only: " + ", ".join(self.commands)
        return f"not read-only: {self.reason}"


def read_only(command: str, shell: str = "powershell") -> tuple[bool, str]:
    """(True, "") when every command in the line only reads, else (False, why)."""
    v = classify(command, shell)
    return v.read_only, v.reason


@dataclass
class _Word:
    text: str = ""  # as the command receives it, quotes removed
    quoted: bool = False
    expands: bool = False  # a $ outside single quotes: reads a variable
    eq: bool = False  # an = outside quotes
    raw: list[str] = field(default_factory=list)


Check = Callable[[list[_Word]], str | None]

# PowerShell reads typographic dashes and quotes as the ASCII ones; other shells do not.
_PS_FOLD = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "―": "-",
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
    }
)

_VERBS = (
    "set|new|remove|move|copy|rename|clear|stop|restart|invoke|start|add|tee|export|import"
    "|install|uninstall|update|register|unregister|enable|disable|suspend|resume|send|push"
    "|pop|reset|save|publish|unblock|connect|disconnect|enter|wait|debug|mount|dismount"
)
_DENY: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p, re.IGNORECASE), why)
    for p, why in (
        (rf"(?<![\w-])(?:{_VERBS})-\w+", "{} changes state"),
        (r"(?<![\w-])out-(?!string\b)\w+", "{} writes or shows output elsewhere"),
        (r"(?<![\w-])(?:iex|icm|saps)(?![\w-])", "{} runs code"),
        (r"(?<![\w-])-(?:encoded)?command\b", "{} runs code"),
        (r"--exec\w*", "{} runs a program"),
        (r"--%", "stop-parsing ({}) hides the rest of the line from this check"),
        (r"(?<![\w-])--?force\b", "{} overrides safeguards"),
        (r"(?<![\w-])-confirm\s*:", "{} skips the confirmation"),
        (r"\|\s*(?:sh|bash|zsh|cmd|powershell|pwsh)\b", "piping into a shell ({}) runs code"),
        (r"[$@]\(", "a subexpression ({}) runs a command"),
    )
)


def classify(command: str, shell: str = "powershell") -> Verdict:
    if shell not in SHELLS:
        return Verdict(False, f"unknown shell {shell!r}")
    line = command.translate(_PS_FOLD) if shell == "powershell" else command
    if any((ord(c) < 32 and c not in "\t\n\r") or ord(c) == 127 for c in line):
        return Verdict(False, "a control character")
    for pat, why in _DENY:
        if m := pat.search(line):
            return Verdict(False, why.format(m.group(0).strip()))
    segs = _lex(line, shell)
    if isinstance(segs, str):
        return Verdict(False, segs)
    names: list[str] = []
    for seg in segs:
        if not seg:
            continue  # `a;` or `a; ;b`: nothing runs there
        head, args = seg[0], seg[1:]
        if head.quoted or head.expands:
            return Verdict(False, f"{''.join(head.raw)} is not a plain command name")
        key = head.text.lower().removesuffix(".exe")
        rule = _TABLES[shell].get(key)
        if rule is None:
            return Verdict(False, f"{head.text} is not on the read-only list")
        canon, check = rule
        if why := check(args):
            return Verdict(False, f"{canon} {why}")
        for w in seg:
            if w.eq and not w.text.startswith("-"):
                return Verdict(False, f"an assignment (=) in {w.text}")
        if canon not in names:
            names.append(canon)
    if not names:
        return Verdict(False, "no command")
    return Verdict(True, "", tuple(names))


def _lex(line: str, shell: str) -> list[list[_Word]] | str:
    """The line's commands as words, or why it cannot be read safely."""
    segs: list[list[_Word]] = [[]]
    word: _Word | None = None
    quotes = '"' if shell == "cmd" else "\"'"
    ps, bash, cmd = shell == "powershell", shell == "bash", shell == "cmd"
    i, n = 0, len(line)

    def end() -> None:
        nonlocal word
        if word is not None:
            segs[-1].append(word)
            word = None

    while i < n:
        ch = line[i]
        if ch in quotes:
            j = line.find(ch, i + 1)
            if j < 0:
                return "an unclosed quote"
            body = line[i + 1 : j]
            if "\n" in body or "\r" in body:
                return "a line break inside quotes"
            if why := _quoted(body, ch, shell):
                return why
            word = word or _Word()
            word.text += body
            word.raw.append(line[i : j + 1])
            word.quoted = True
            word.expands |= ch == '"' and "$" in body and not cmd
            i = j + 1
            continue
        if ch in " \t":
            end()
        elif line[i : i + 2] in ("&&", "||"):
            end()
            segs.append([])
            i += 2
            continue
        elif ch in ";|\n\r":
            end()
            segs.append([])
        elif ch == "&":
            if not cmd:
                return "the call operator or a background job (&)"
            end()
            segs.append([])
        elif ch in "<>":
            return f"a redirection ({ch}) reads or writes a file"
        elif ch in "{}":
            return "a script block or braces ({ }) can run anything"
        elif ch in "()":
            return "parentheses run a nested command"
        elif ch == "`" and (ps or bash):
            return "a backtick: an escape in PowerShell, a command in bash"
        elif ch == "\\" and bash:
            return "a backslash escape"
        elif ch == "^" and cmd:
            return "a caret escape"
        elif ch in "%!" and cmd:
            return f"a variable expansion ({ch}) in cmd, which happens before the line is read"
        elif ch == "!" and bash:
            return "history expansion (!)"
        else:
            word = word or _Word()
            word.text += ch
            word.raw.append(ch)
            word.expands |= ch == "$" and not cmd
            word.eq |= ch == "="
        i += 1
    end()
    return segs


def _quoted(body: str, quote: str, shell: str) -> str | None:
    """Why a quoted string is not plain text, or None."""
    if shell == "cmd":
        return "a variable expansion in cmd" if "%" in body or "!" in body else None
    if quote == "'":
        return None  # literal in PowerShell and bash
    if "`" in body:
        return "a backtick in a double-quoted string"
    if shell == "bash":
        for bad, why in (("\\", "a backslash escape"), ("!", "history expansion (!)")):
            if bad in body:
                return f"{why} in a double-quoted string"
        if "${" in body or "$[" in body:
            return "a parameter expansion in a double-quoted string"
    return None


# Rules for arguments: None when they only read, else why not.


def _any(args: list[_Word]) -> str | None:
    return None


def _literal(args: list[_Word]) -> str | None:
    return "with a variable in its arguments" if any(w.expands for w in args) else None


def _bare(args: list[_Word]) -> str | None:
    return "with arguments" if args else None


def _switches(args: list[_Word]) -> str | None:
    bad = next((w.text for w in args if not w.text.startswith(("/", "-"))), None)
    return f"with {bad}" if bad else None


def _version(args: list[_Word]) -> str | None:
    if [w.text for w in args] in (["--version"], ["-V"]):
        return None
    return "other than --version or -V runs a program"


def _sort(args: list[_Word]) -> str | None:
    bad = next((w.text for w in args if w.text.lower().startswith(("-o", "/o"))), None)
    return f"{bad} can write a file" if bad else None


def _help(args: list[_Word]) -> str | None:
    bad = next((w.text for w in args if w.text.lower().startswith(("-o", "-sh"))), None)
    return f"{bad} opens a window" if bad else None


_GIT_FLAGS = ("--output", "--ext-diff", "--upload-pack", "--receive-pack", "--open-files")
_BRANCH_OK = {"-a", "--all", "-r", "--remotes", "-l", "--list", "-v", "-vv", "--verbose"}
_BRANCH_OK |= {"--show-current", "--no-color"}
_CONFIG_READ = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l"}
_CONFIG_OK = _CONFIG_READ | {"--global", "--system", "--local", "--worktree", "--show-origin"}
_CONFIG_OK |= {"--show-scope", "--name-only", "-z", "--null", "--bool", "--int", "--path"}


def _git_branch(rest: list[str]) -> str | None:
    listing = any(x in ("-l", "--list") for x in rest)
    for x in rest:
        if x.startswith("-") and x not in _BRANCH_OK:
            return f"{x} can change branches"
        if not x.startswith("-") and not listing:
            return f"{x} creates a branch"
    return None


def _git_tag(rest: list[str]) -> str | None:
    if not rest:
        return None
    if not any(x in ("-l", "--list") for x in rest):
        return "with arguments creates or deletes a tag; only -l lists"
    for x in rest:
        if x.startswith("-") and x not in ("-l", "--list") and not re.fullmatch(r"-n\d*", x):
            return f"{x} can change tags"
    return None


def _git_remote(rest: list[str]) -> str | None:
    return None if rest in ([], ["-v"], ["--verbose"]) else "other than -v changes remotes"


def _git_config(rest: list[str]) -> str | None:
    if rest[:1] in (["get"], ["list"]):  # git 2.46's subcommands
        rest = rest[1:]
    elif not any(x in _CONFIG_READ for x in rest):
        return "without --get or --list sets a value"
    for x in rest:
        if x.startswith("-") and x not in _CONFIG_OK and not x.startswith("--type="):
            return f"{x} can change the configuration"
    return None


def _git_free(rest: list[str]) -> str | None:
    return None


_GIT: dict[str, Callable[[list[str]], str | None]] = {
    "log": _git_free,
    "status": _git_free,
    "diff": _git_free,
    "show": _git_free,
    "grep": _git_free,
    "ls-files": _git_free,
    "rev-parse": _git_free,
    "blame": _git_free,
    "describe": _git_free,
    "branch": _git_branch,
    "tag": _git_tag,
    "remote": _git_remote,
    "config": _git_config,
}


def _git(args: list[_Word]) -> str | None:
    a = [w.text for w in args]
    i = 0
    while i < len(a) and a[i].startswith("-"):
        if a[i] in ("--no-pager", "-P"):
            i += 1
        elif a[i] == "-C" and i + 1 < len(a):
            i += 2
        else:
            return f"option {a[i]} before the subcommand can change what git runs"
    if i == len(a):
        return "without a subcommand"
    sub, rest = a[i], a[i + 1 :]
    if sub not in _GIT:
        return f"{sub} is not a read-only git command"
    for x in rest:
        if x.lower().startswith(_GIT_FLAGS) or (sub == "grep" and x.startswith("-O")):
            return f"{sub} {x} can write a file or run a program"
    why = _GIT[sub](rest)
    return f"{sub} {why}" if why else None


def _az(args: list[_Word]) -> str | None:
    path = []
    for w in args:
        if w.text.startswith("-"):
            break
        path.append(w.text)
    if len(path) < 2 or path[-1] not in ("show", "list"):
        return "other than `az ... show` or `az ... list` can change resources"
    return None


_COMMON = {"hostname": (), "whoami": (), "git": (), "python": (), "az": ()}
_CHECKS: dict[str, Check] = {
    "hostname": _bare,
    "whoami": _switches,
    "git": _git,
    "python": _version,
    "python3": _version,
    "py": _version,
    "az": _az,
    "Write-Output": _literal,
    "Sort-Object": _sort,
    "Get-Help": _help,
    "ver": _bare,
}
_BASH_CHECKS: dict[str, Check] = {"echo": _literal, "pwd": _bare}


def _aliases(
    table: dict[str, tuple[str, ...]], checks: dict[str, Check] = _CHECKS
) -> dict[str, tuple[str, Check]]:
    return {
        name.lower(): (canon, checks.get(canon, _any))
        for canon, names in (_COMMON | table).items()
        for name in (canon, *names)
    }


_PS = _aliases(
    {
        "Get-ChildItem": ("gci", "ls", "dir"),
        "Get-Content": ("gc", "cat", "type"),
        "Get-Location": ("pwd", "gl"),
        "Get-Date": (),
        "Get-Item": ("gi",),
        "Get-ItemProperty": ("gp",),
        "Get-FileHash": (),
        "Get-Process": ("ps", "gps"),
        "Get-Service": ("gsv",),
        "Get-Command": ("gcm",),
        "Get-Help": (),
        "Get-History": ("ghy", "h", "history"),
        "Get-Variable": ("gv",),
        "Get-Member": ("gm",),
        "Select-String": ("sls",),
        "Select-Object": ("select",),
        "Where-Object": ("where", "?"),
        "Sort-Object": ("sort",),
        "Measure-Object": ("measure",),
        "Group-Object": ("group",),
        "Format-Table": ("ft",),
        "Format-List": ("fl",),
        "Format-Wide": ("fw",),
        "Out-String": (),
        "Test-Path": (),
        "Resolve-Path": ("rvpa",),
        "Split-Path": (),
        "Join-Path": (),
        "ConvertFrom-Json": (),
        "ConvertTo-Json": (),
        "ConvertFrom-Csv": (),
        "Write-Output": ("echo", "write"),
        "py": (),
    }
)
_CMD = _aliases(
    {"dir": (), "type": (), "echo": (), "where": (), "findstr": (), "ver": (), "py": ()}
)
_BASH = _aliases(
    {"ls": (), "cat": (), "pwd": (), "echo": (), "ps": (), "head": (), "tail": (), "wc": ()}
    | {"grep": (), "python3": ()},
    _CHECKS | _BASH_CHECKS,
)

_TABLES = {"powershell": _PS, "cmd": _CMD, "bash": _BASH}
