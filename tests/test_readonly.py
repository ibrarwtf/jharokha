"""Which commands only read: the allowlist terminal.run checks before it types a command
under the default allow. Every refusal here is a command that writes, deletes, runs code
or hides what it runs from the check; when unsure, the classifier refuses."""

from __future__ import annotations

import pytest

from jharokha.core import actions, shell
from jharokha.core.actions import Runner
from jharokha.core.readonly import classify, read_only

from .test_actions import Scripted

READ_ONLY: list[tuple[str, str, tuple[str, ...]]] = [
    # PowerShell cmdlets and their aliases
    ("powershell", "Get-ChildItem", ("Get-ChildItem",)),
    ("powershell", "gci -Recurse -Filter *.py", ("Get-ChildItem",)),
    ("powershell", "ls", ("Get-ChildItem",)),
    ("powershell", "dir C:\\Users", ("Get-ChildItem",)),
    ("powershell", "Get-ChildItem | select -first 3", ("Get-ChildItem", "Select-Object")),
    ("powershell", "Get-Content notes.txt -Tail 20", ("Get-Content",)),
    ("powershell", "gc .\\a.txt | Select-String TODO", ("Get-Content", "Select-String")),
    ("powershell", "cat README.md", ("Get-Content",)),
    ("powershell", "type a.txt", ("Get-Content",)),
    ("powershell", "Get-Location", ("Get-Location",)),
    ("powershell", "pwd", ("Get-Location",)),
    ("powershell", "Get-Date", ("Get-Date",)),
    ("powershell", "Get-Item .\\x | Format-List", ("Get-Item", "Format-List")),
    ("powershell", "Get-ItemProperty HKCU:\\Environment", ("Get-ItemProperty",)),
    ("powershell", "Get-FileHash .\\a.zip -Algorithm SHA256", ("Get-FileHash",)),
    (
        "powershell",
        "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5",
        ("Get-Process", "Sort-Object", "Select-Object"),
    ),
    ("powershell", "ps", ("Get-Process",)),
    ("powershell", "gsv | Where-Object Status -eq Running", ("Get-Service", "Where-Object")),
    ("powershell", "gci | ? Name -like *.txt", ("Get-ChildItem", "Where-Object")),
    ("powershell", "Get-Command git", ("Get-Command",)),
    ("powershell", "Get-Help Get-ChildItem", ("Get-Help",)),
    ("powershell", "Get-History", ("Get-History",)),
    ("powershell", "Get-Variable PSVersionTable", ("Get-Variable",)),
    ("powershell", "gci | gm", ("Get-ChildItem", "Get-Member")),
    ("powershell", 'sls -Path *.log -Pattern "error; warning"', ("Select-String",)),
    ("powershell", "gci | Measure-Object", ("Get-ChildItem", "Measure-Object")),
    (
        "powershell",
        "gci | Group-Object Extension | sort Count",
        ("Get-ChildItem", "Group-Object", "Sort-Object"),
    ),
    ("powershell", "gci | ft Name, Length -AutoSize", ("Get-ChildItem", "Format-Table")),
    ("powershell", "gci | Format-Wide", ("Get-ChildItem", "Format-Wide")),
    ("powershell", "Get-Date | Out-String", ("Get-Date", "Out-String")),
    ("powershell", "Test-Path .\\x.txt", ("Test-Path",)),
    ("powershell", "Resolve-Path ..", ("Resolve-Path",)),
    ("powershell", "Split-Path C:\\a\\b.txt -Leaf", ("Split-Path",)),
    ("powershell", "Join-Path C:\\a b", ("Join-Path",)),
    ("powershell", "gc a.json | ConvertFrom-Json", ("Get-Content", "ConvertFrom-Json")),
    ("powershell", "Get-Date | ConvertTo-Json", ("Get-Date", "ConvertTo-Json")),
    ("powershell", "gc a.csv | ConvertFrom-Csv", ("Get-Content", "ConvertFrom-Csv")),
    ("powershell", "echo hello", ("Write-Output",)),
    ("powershell", "Write-Output 'a > b'", ("Write-Output",)),
    ("powershell", 'echo "x | y; z"', ("Write-Output",)),
    ("powershell", "hostname", ("hostname",)),
    ("powershell", "whoami", ("whoami",)),
    ("powershell", "whoami /groups", ("whoami",)),
    ("powershell", "Get-Date; Get-Location", ("Get-Date", "Get-Location")),
    ("powershell", "Get-ChildItem;", ("Get-ChildItem",)),
    ("powershell", "gci && git status", ("Get-ChildItem", "git")),
    ("powershell", "Get-ChildItem \u2013Recurse", ("Get-ChildItem",)),  # an en dash
    ("powershell", "Get-Content 'it''s.txt'", ("Get-Content",)),
    ("powershell", "Select-String -Pattern 'a{2}' x.txt", ("Select-String",)),
    ("powershell", 'Get-ChildItem -Path "C:\\Program Files"', ("Get-ChildItem",)),
    ("powershell", "Get-Content $profile", ("Get-Content",)),
    ("powershell", "GET-CHILDITEM", ("Get-ChildItem",)),
    # git, reads only
    ("powershell", "git status", ("git",)),
    ("powershell", "git log --oneline -10", ("git",)),
    ("powershell", "git log --pretty=format:%h --since=2.weeks", ("git",)),
    ("powershell", "git diff HEAD~1", ("git",)),
    ("powershell", "git show HEAD:README.md", ("git",)),
    ("powershell", "git branch", ("git",)),
    ("powershell", "git branch -a", ("git",)),
    ("powershell", "git branch -r", ("git",)),
    ("powershell", "git branch --list 'feat*'", ("git",)),
    ("powershell", "git grep -n TODO", ("git",)),
    ("powershell", "git ls-files", ("git",)),
    ("powershell", "git rev-parse HEAD", ("git",)),
    ("powershell", "git remote -v", ("git",)),
    ("powershell", "git config --get user.name", ("git",)),
    ("powershell", "git config --list --show-origin", ("git",)),
    ("powershell", "git blame README.md", ("git",)),
    ("powershell", "git describe --tags", ("git",)),
    ("powershell", "git tag", ("git",)),
    ("powershell", "git tag -l 'v0.*'", ("git",)),
    ("powershell", "git --no-pager log -3", ("git",)),
    ("powershell", "git.exe status", ("git",)),
    # versions and az reads
    ("powershell", "python --version", ("python",)),
    ("powershell", "py -V", ("py",)),
    ("powershell", "az account show", ("az",)),
    ("powershell", "az group list -o table", ("az",)),
    ("powershell", "az vm list --query '[].name'", ("az",)),
    # cmd
    ("cmd", "dir /b", ("dir",)),
    ("cmd", "type a.txt & ver", ("type", "ver")),
    ("cmd", "findstr /s TODO *.py", ("findstr",)),
    ("cmd", "git status && hostname", ("git", "hostname")),
    ("cmd", 'echo "a > b"', ("echo",)),
    # bash
    ("bash", "ls -la | grep x", ("ls", "grep")),
    ("bash", "cat a.txt | head -5", ("cat", "head")),
    ("bash", "echo 'a; b > c'", ("echo",)),
    ("bash", "git log -1; pwd", ("git", "pwd")),
    ("bash", "python3 --version", ("python3",)),
]

REFUSED: list[tuple[str, str, str]] = [
    # the cases the allowlist was written against
    ("powershell", "gci | % { rm $_ }", "script block"),
    ("powershell", "Get-Content x > y", "redirection (>)"),
    ("powershell", "git status; rm x", "rm is not on the read-only list"),
    ("powershell", "ls $(Remove-Item x)", "Remove-Item changes state"),
    ("powershell", "Get-Process | Stop-Process", "Stop-Process changes state"),
    ("powershell", "git branch -D x", "-D can change branches"),
    ("powershell", "git config user.name x", "sets a value"),
    ("powershell", "& rm x", "call operator"),
    ("powershell", "cmd /c del x", "cmd is not on the read-only list"),
    ("powershell", "Get-ChildItem -Recurse | Remove-Item", "Remove-Item changes state"),
    ("powershell", "echo hi > out.txt", "redirection (>)"),
    ("powershell", '"$(rm x)"', "subexpression"),
    ("powershell", "iex 'rm x'", "iex runs code"),
    ("powershell", "git -c core.pager=sh log", "option -c before the subcommand"),
    # writes, deletes and state changes
    ("powershell", "Remove-Item x", "Remove-Item changes state"),
    ("powershell", "rm x", "rm is not on the read-only list"),
    ("powershell", "del x", "del is not on the read-only list"),
    ("powershell", "Get-Content x >> y", "redirection (>)"),
    ("powershell", "Get-ChildItem 2> err.txt", "redirection (>)"),
    ("powershell", "Set-Content a.txt b", "Set-Content changes state"),
    ("powershell", "New-Item x", "New-Item changes state"),
    ("powershell", "Move-Item a b", "Move-Item changes state"),
    ("powershell", "Copy-Item a b", "Copy-Item changes state"),
    ("powershell", "Rename-Item a b", "Rename-Item changes state"),
    ("powershell", "Clear-Content a", "Clear-Content changes state"),
    ("powershell", "Restart-Service x", "Restart-Service changes state"),
    ("powershell", "gci | Out-File x.txt", "Out-File writes"),
    ("powershell", "Add-Content a b", "Add-Content changes state"),
    ("powershell", "Get-Date | Tee-Object x.txt", "Tee-Object changes state"),
    ("powershell", "Get-ChildItem -Force", "-Force overrides safeguards"),
    ("powershell", "Get-ChildItem -Confirm:$false", "-Confirm: skips the confirmation"),
    ("powershell", "Get-Content -Path $profile | sc x", "sc is not on the read-only list"),
    # code that runs other code
    ("powershell", "gci | ForEach-Object { $_.Delete() }", "script block"),
    ("powershell", "gci | Where-Object { $_.Length -gt 1 }", "script block"),
    ("powershell", "gci | % Delete", "% is not on the read-only list"),
    ("powershell", ". .\\script.ps1", ". is not on the read-only list"),
    ("powershell", ".\\script.ps1", "is not on the read-only list"),
    ("powershell", 'Invoke-Expression "rm x"', "Invoke-Expression changes state"),
    ("powershell", "Get-Content x | iex", "iex runs code"),
    ("powershell", "Start-Process notepad", "Start-Process changes state"),
    ("powershell", 'powershell -Command "rm x"', "-Command runs code"),
    ("powershell", "Write-Output (rm x)", "parentheses"),
    ("powershell", "echo $(whoami)", "subexpression"),
    ("powershell", "echo hi`; rm x", "backtick"),
    ("powershell", "Get-ChildItem `\n| rm", "backtick"),
    ("powershell", "Get-ChildItem\nRemove-Item x", "Remove-Item changes state"),
    ("powershell", "gci --% ; rm x", "stop-parsing"),
    ("powershell", "gci | Select-Object @{n='x';e={rm y}}", "script block"),
    ("powershell", "$x = 1", "is not a plain command name"),
    ("powershell", "$cmd", "is not a plain command name"),
    ("powershell", '"Get-ChildItem"', "is not a plain command name"),
    ("powershell", "Get-ChildItem | sh", "piping into a shell"),
    ("powershell", "echo $env:USERNAME", "with a variable"),
    ("powershell", "Get-ChildItem x=y", "an assignment (=)"),
    ("powershell", "Get-Help Get-Process -Online", "opens a window"),
    ("powershell", "gc a | sort -o b", "can write a file"),
    ("powershell", "echo \u2018a\u2019; rm x", "rm is not on the read-only list"),
    ("powershell", "gci 'unclosed", "unclosed quote"),
    ("powershell", "", "no command"),
    ("powershell", "Get-Date \x1b[2J", "control character"),
    # a name that changes state is refused even as text: unsure is no
    ("powershell", "Select-String 'Remove-Item' x.ps1", "Remove-Item changes state"),
    # git that writes, deletes or runs a program
    ("powershell", "git log --output=x.txt", "can write a file or run a program"),
    ("powershell", "git diff --ext-diff", "can write a file or run a program"),
    ("powershell", "git grep -O TODO", "can write a file or run a program"),
    ("powershell", "git rebase --exec=sh main", "--exec runs a program"),
    ("powershell", "git branch newname", "newname creates a branch"),
    ("powershell", "git branch -a newname", "newname creates a branch"),
    ("powershell", "git tag v1.0", "creates or deletes a tag"),
    ("powershell", "git tag -l -d v1.0", "-d can change tags"),
    ("powershell", "git remote add o https://x", "changes remotes"),
    ("powershell", "git config --get user.name --add x", "--add can change"),
    ("powershell", "git config --unset user.name", "sets a value"),
    ("powershell", "git commit -m x", "commit is not a read-only git command"),
    ("powershell", "git push", "push is not a read-only git command"),
    ("powershell", "git checkout .", "checkout is not a read-only git command"),
    ("powershell", "git reset --hard", "reset is not a read-only git command"),
    ("powershell", "git clean -fdx", "clean is not a read-only git command"),
    ("powershell", "git -C . -c x=y status", "option -c"),
    ("powershell", "git", "without a subcommand"),
    # others on the list, with arguments that do more than read
    ("powershell", "hostname newname", "hostname with arguments"),
    ("powershell", "python script.py", "runs a program"),
    ("powershell", "python -c 'print(1)'", "runs a program"),
    ("powershell", "python -v", "runs a program"),
    ("powershell", "az group delete -n x", "can change resources"),
    ("powershell", "az vm start -n x", "can change resources"),
    ("powershell", "az rest --method get", "can change resources"),
    ("powershell", "python --version; Remove-Item x", "Remove-Item changes state"),
    ("powershell", "whoami && shutdown /s", "shutdown is not on the read-only list"),
    # cmd
    ("cmd", "dir & del x", "del is not on the read-only list"),
    ("cmd", "echo %PATH%", "variable expansion"),
    ("cmd", "dir ^& del x", "caret"),
    ("cmd", "type a.txt > b.txt", "redirection (>)"),
    ("cmd", 'cmd /c "del x"', "cmd is not on the read-only list"),
    ("cmd", "sort /o out.txt in.txt", "sort is not on the read-only list"),
    # bash
    ("bash", "cat a | tee b", "tee is not on the read-only list"),
    ("bash", "ls; rm -rf /", "rm is not on the read-only list"),
    ("bash", 'echo "$(rm x)"', "subexpression"),
    ("bash", "echo `rm x`", "backtick"),
    ("bash", "ls \\; rm x", "backslash"),
    ("bash", "echo !!", "history expansion"),
    ("bash", "ls &", "background job"),
    ("bash", "ls | bash", "piping into a shell"),
    ("bash", "ls \u2018;rm x;\u2019", "rm is not on the read-only list"),  # literal in bash
    ("bash", 'echo "${x:=1}"', "parameter expansion"),
    ("bash", "echo $HOME", "with a variable"),
    ("bash", "find . -delete", "find is not on the read-only list"),
    # an unknown shell
    ("fish", "ls", "unknown shell"),
]


@pytest.mark.parametrize(("sh", "command", "names"), READ_ONLY)
def test_commands_that_only_read(sh: str, command: str, names: tuple[str, ...]):
    v = classify(command, sh)
    assert v.read_only, v.reason
    assert v.commands == names
    assert read_only(command, sh) == (True, "")


@pytest.mark.parametrize(("sh", "command", "why"), REFUSED)
def test_commands_that_do_more_are_refused_with_why(sh: str, command: str, why: str):
    ok, reason = read_only(command, sh)
    assert not ok
    assert why in reason
    assert classify(command, sh).describe() == f"not read-only: {reason}"


def test_the_table_is_large_and_both_ways():
    assert len(READ_ONLY) >= 60 and len(REFUSED) >= 60
    assert not {(s, c) for s, c, _ in READ_ONLY} & {(s, c) for s, c, _ in REFUSED}


def test_the_command_is_classified_without_jharokhas_end_marker():
    # The marker assigns and joins strings: classified with it, nothing would pass.
    assert read_only("Get-ChildItem")[0]
    assert not read_only(shell.marked("Get-ChildItem", "KX7R4C", "powershell"))[0]
    assert classify("gci | select -first 3").describe() == (
        "read-only: Get-ChildItem, Select-Object"
    )


def test_terminal_run_is_reversible_only_for_a_read_only_command():
    run = actions.builtin()["terminal.run"]
    ex = Scripted({"terminal": []}, "terminal")
    listing = {"command": "Get-ChildItem | select -first 3"}
    r = Runner(ex, allow="reversible").run(run, listing, True)
    assert r.ok and ex.inputs == []
    assert r.risk == "reversible (read-only: Get-ChildItem, Select-Object)"
    assert r.to_dict()["risk"] == r.risk and r.plan and r.plan[1]["risk"] == "reversible"
    r = Runner(ex, allow="reversible").run(run, {"command": "Remove-Item x"})
    assert not r.ok and ex.inputs == [] and r.failed and r.failed["code"] == "irreversible"
    assert r.risk == "irreversible (not read-only: Remove-Item changes state)"
    assert r.failed["reason"].startswith(
        "step 2:run (run, not read-only: Remove-Item changes state) is irreversible"
    )
    assert "allow='irreversible'" in r.failed["reason"]
    # The shell is the call's: in cmd, % expands before the line is read.
    r = Runner(ex, allow="reversible").run(run, {"command": "echo %X%", "shell": "cmd"}, True)
    assert not r.ok and r.risk and "variable expansion" in r.risk
    # put_file writes, whatever it writes.
    put = actions.builtin()["terminal.put_file"]
    r = Runner(ex, allow="reversible").run(put, {"path": "x", "content_base64": "eA=="}, True)
    assert not r.ok and r.failed and r.failed["code"] == "irreversible"


def test_a_run_step_never_goes_above_what_it_declares():
    a = actions.parse(
        'format = 1\n[action.terminal.where]\nmax_risk = "read"\n'
        '[[action.terminal.where.step]]\ndo = "run"\ncommand = "Get-Location"\nrisk = "read"\n'
    )["terminal.where"]
    ex = Scripted({"terminal": []}, "terminal")
    r = Runner(ex, allow="read").run(a, {}, True)
    assert r.ok and r.risk == "read (read-only: Get-Location)"
