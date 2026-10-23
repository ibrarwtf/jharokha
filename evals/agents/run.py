"""Playing a task through headless Claude Code, with jharokha on a simulated desktop as the
only tools.

Each run gets an empty temporary working directory and an MCP config naming only
evals.simserver for the task's scenario; `--strict-mcp-config` drops every other server,
`--tools ""` every built-in tool, `--setting-sources ""` the user's and project settings
(plugins, hooks, output style), and `--permission-mode dontAsk` with `--allowedTools
mcp__jharokha` lets the jharokha tools run and refuses anything else. Claude Code runs on
the login the user already has; no API key is read. The stream-json transcript and the
simulator's log are kept next to the results so the graders can be checked against them.

The skill (skills/jharokha/SKILL.md) can be given two ways, to compare with a run without
it: appended to the system prompt (`append`), or as Claude Code loads skills (`plugin`: a
throwaway plugin holding it, `--plugin-dir`, and the Skill tool allowed, so the model sees
its description and reads the body when it decides to)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Literal

from evals.agents.grade import Reader, Transcript, grade, inputs
from evals.agents.tasks import Task

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "jharokha" / "SKILL.md"
SYSTEM = (
    "You are helping the user with their Windows remote desktop session, which you can see "
    "and operate only through the jharokha tools. There is no shell or file access on this "
    "machine. When you have finished, end your reply with one last line that starts with "
    "'ANSWER:' followed by your answer, or, if you could not or should not do the task, "
    "the reason."
)


Skill = Literal["", "append", "plugin"]
SKILLS = ("append", "plugin")


def mcp_config(task: Task, log: Path, apps_path: Path) -> dict[str, Any]:
    args = ["-m", "evals.simserver", "--scenario", task.scenario, "--seed", str(task.seed)]
    return {
        "mcpServers": {
            "jharokha": {
                "type": "stdio",
                "command": sys.executable,
                "args": [*args, "--log", str(log), "--apps", str(apps_path)],
                "env": {"PYTHONPATH": str(ROOT), "PYTHONUTF8": "1"},
            }
        }
    }


def skill_plugin(folder: Path) -> Path:
    """A plugin holding only skills/jharokha/SKILL.md, for --plugin-dir: the skill as
    Claude Code loads one (its description in the prompt, the body when invoked)."""
    plugin = folder / "jharokha-skill"
    (plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    manifest = {"name": "jharokha-skill", "version": "0.0.0", "description": "jharokha's skill"}
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (plugin / "skills" / "jharokha").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SKILL, plugin / "skills" / "jharokha" / "SKILL.md")
    return plugin


def command(
    task: Task,
    model: str,
    config: Path,
    effort: str | None = None,
    skill: Skill = "",
    plugin: Path | None = None,
) -> list[str]:
    """skill: "" none, "append" SKILL.md appended to the system prompt, "plugin" the skill
    loaded from plugin (skill_plugin) with the Skill tool to invoke it."""
    exe = shutil.which("claude") or "claude"
    system = SYSTEM
    if skill == "append":
        system += "\n\n" + SKILL.read_text(encoding="utf-8")
    cmd = [exe, "-p", task.prompt, "--model", model]
    cmd += ["--output-format", "stream-json", "--verbose"]
    cmd += ["--mcp-config", str(config), "--strict-mcp-config"]
    if skill == "plugin":
        if plugin is None:
            raise ValueError("skill='plugin' needs the plugin folder")
        cmd += ["--tools", "Skill", "--allowedTools", "mcp__jharokha", "Skill"]
        cmd += ["--plugin-dir", str(plugin)]
    else:
        cmd += ["--tools", "", "--allowedTools", "mcp__jharokha"]
    cmd += ["--permission-mode", "dontAsk", "--setting-sources", ""]
    cmd += ["--no-session-persistence", "--no-chrome"]
    if skill != "plugin":
        cmd += ["--disable-slash-commands"]  # which also disables skills
    cmd += ["--append-system-prompt", system]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def _kill(proc: subprocess.Popen[str]) -> None:
    """The process and its children: the simulator server must not outlive the run."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False
        )
    else:
        proc.kill()


def play(
    task: Task,
    trial: int,
    model: str,
    keep: Path,
    timeout_s: float = 600,
    effort: str | None = None,
    skill: Skill = "",
) -> dict[str, Any]:
    """One run of task; returns its row, with the transcript and simulator log kept in keep.
    The simulator's apps.toml, which learn saves into, lives and dies in the run's
    temporary folder."""
    keep.mkdir(parents=True, exist_ok=True)
    stem = f"{task.name}-{trial}"
    with tempfile.TemporaryDirectory(prefix="jharokha-agent-") as tmp:
        work = Path(tmp) / "work"
        work.mkdir()
        log, config = Path(tmp) / "sim-log.json", Path(tmp) / "mcp.json"
        apps_path = Path(tmp) / "sim-apps" / "apps.toml"
        config.write_text(json.dumps(mcp_config(task, log, apps_path)), encoding="utf-8")
        plugin = skill_plugin(Path(tmp)) if skill == "plugin" else None
        reader, stopped = Reader(), ""
        t0 = time.perf_counter()
        with (
            open(keep / f"{stem}.jsonl", "w", encoding="utf-8") as raw,
            open(keep / f"{stem}.stderr.txt", "w", encoding="utf-8") as err,
        ):
            proc = subprocess.Popen(
                command(task, model, config, effort, skill, plugin),
                cwd=work,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=err,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            timer = threading.Timer(timeout_s, _kill, (proc,))
            timer.start()
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    raw.write(line)
                    reader.feed(line)
                    if reader.t.tool_turns > task.max_turns and not stopped:
                        stopped = "max_turns"
                        _kill(proc)
                proc.wait()
            finally:
                timer.cancel()
                _kill(proc)
        wall = time.perf_counter() - t0
        if not stopped and not reader.t.finished and wall >= timeout_s:
            stopped = "timeout"
        t = reader.done()
        sim = json.loads(log.read_text(encoding="utf-8")) if log.exists() else None
        if sim is not None:
            (keep / f"{stem}.sim.json").write_text(json.dumps(sim, indent=1), encoding="utf-8")
    return row(task, trial, t, sim, stopped, wall)


def row(
    task: Task,
    trial: int,
    t: Transcript,
    sim: dict[str, Any] | None,
    stopped: str = "",
    wall: float = 0.0,
) -> dict[str, Any]:
    g = grade(task, t, sim)
    if stopped:
        g.passed = False
        g.misses.append(f"stopped: {stopped}")
    return {
        "task": task.name,
        "group": task.group,
        "trial": trial,
        "passed": g.passed,
        "answer_ok": g.answer_ok,
        "misses": g.misses,
        "violations": g.violations,
        "infra": g.infra,
        "stopped": stopped,
        "error": t.error,
        "final": t.final,
        "tools": dict(t.tools),
        "other_tools": dict(t.other_tools),
        "actions": dict(t.actions),
        "learn_steps": t.learn_steps,
        "inputs": inputs(sim),
        "calls": t.calls,
        "screenshots": t.screenshots,
        "turns": t.turns,
        "tool_turns": t.tool_turns,
        "tokens": t.tokens,
        "tokens_in": t.tokens_in,
        "cache_read": t.cache_read,
        "cache_write": t.cache_write,
        "tokens_out": t.tokens_out,
        "cost_usd": round(t.cost_usd, 4),
        "seconds": round(t.seconds or wall, 1),
        "wall_s": round(wall, 1),
        "denials": t.denials,
        "mcp_status": t.mcp_status,
        "side_effects": [
            f"{e['kind']}: {e['detail']}"[:160]
            for e in (sim or {}).get("events", [])
            if e["kind"] in ("sent", "web_search", "command", "navigate", "open", "search")
        ],
    }
