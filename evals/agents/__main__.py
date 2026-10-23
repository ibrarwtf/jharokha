"""Run the agent tasks through headless Claude Code and summarise them.

    uv run python -m evals.agents [--model opus] [--k 3] [--tasks a,b | --group terminal]
        [--out results.json] [--jobs 1] [--timeout 600] [--effort high]
        [--skill [append|plugin]]
    uv run python -m evals.agents --list
    uv run python -m evals.agents --summary results.json
    uv run python -m evals.agents --regrade results.json [--out regraded.json]

Each task runs k times. Per task: passes of k, pass@k (at least one of the k passed),
pass^k (all k passed), and the mean tool calls, screenshots, inputs the desktop received
(from act or an action), tokens, list-price cost and seconds per run. Safety violations
(forbidden side effects or tool calls) are listed one by one. Transcripts (stream-json)
and simulator logs are kept beside --out, in <out>-runs/, or in a temporary folder that is
printed; read them to check the graders, and --regrade grades them again after a grader
changed, without calling claude.

A run whose jharokha server was not connected when the agent started (it had no tools) is
infra, not a fail: it is played again once, and a run that is still infra counts in no
pass rate."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from evals.agents.grade import NOT_CONNECTED, read
from evals.agents.run import SKILLS, play, row
from evals.agents.setups import SCENARIOS
from evals.agents.tasks import Task, load
from jharokha import version


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pass rates count graded runs only: a run the server never answered (infra) says
    nothing about the agent or jharokha, so it is neither a pass nor a fail. Tokens, cost,
    time and screenshots count every run."""
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(r["task"], []).append(r)
    tasks = {}
    for name, rs in by.items():
        graded = [r for r in rs if not r["infra"]]
        n, c = len(graded), sum(r["passed"] for r in graded)
        tasks[name] = {
            "group": rs[0]["group"],
            "n": n,
            "passed": c,
            "pass_rate": round(c / n, 3) if n else None,
            "pass_at_k": c > 0,
            "pass_all_k": n > 0 and c == n,
            "calls": round(mean(r["calls"] for r in graded or rs), 1),
            "screenshots": round(mean(r["screenshots"] for r in rs), 1),
            "inputs": round(mean(r.get("inputs", 0) for r in rs), 1),
            "tokens": round(mean(r["tokens"] for r in rs)),
            "tokens_out": round(mean(r["tokens_out"] for r in rs)),
            "cost_usd": round(mean(r["cost_usd"] for r in rs), 4),
            "seconds": round(mean(r["seconds"] for r in rs), 1),
            "infra": sum(bool(r["infra"]) for r in rs),
        }
    ts = [t for t in tasks.values() if t["n"]]
    graded = [r for r in rows if not r["infra"]]
    violations = [
        {"task": r["task"], "trial": r["trial"], "violation": v}
        for r in rows
        for v in r["violations"]
    ]
    return {
        "tasks": tasks,
        "overall": {
            "tasks": len(ts),
            "runs": len(rows),
            "pass_at_1": round(mean(t["pass_rate"] for t in ts), 3) if ts else 0.0,
            "pass_at_k": round(mean(t["pass_at_k"] for t in ts), 3) if ts else 0.0,
            "pass_all_k": round(mean(t["pass_all_k"] for t in ts), 3) if ts else 0.0,
            "graded": sum(t["n"] for t in ts),
            "calls": round(mean(r["calls"] for r in graded), 1) if graded else 0.0,
            "screenshots": sum(r["screenshots"] for r in rows),
            "tokens": sum(r["tokens"] for r in rows),
            "tokens_out": sum(r["tokens_out"] for r in rows),
            "cost_usd": round(sum(r["cost_usd"] + r.get("retried_cost_usd", 0) for r in rows), 3),
            "seconds": round(sum(r["seconds"] for r in rows), 1),
            "safety_violations": len(violations),
            "infra_failures": sum(bool(r["infra"]) for r in rows),
            "retried": sum(bool(r.get("retried")) for r in rows),
        },
        "groups": groups(tasks),
        "violations": violations,
    }


def groups(tasks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per group: its tasks, graded runs, passes, pass@1 (the mean of its tasks' pass
    rates) and pass^k."""
    out: dict[str, dict[str, Any]] = {}
    for t in tasks.values():
        if t["n"]:
            out.setdefault(t["group"], {"tasks": 0, "runs": 0, "passed": 0, "_r": [], "_a": []})
            g = out[t["group"]]
            g["tasks"] += 1
            g["runs"] += t["n"]
            g["passed"] += t["passed"]
            g["_r"].append(t["pass_rate"])
            g["_a"].append(t["pass_all_k"])
    for g in out.values():
        g["pass_at_1"] = round(mean(g.pop("_r")), 3)
        g["pass_all_k"] = round(mean(g.pop("_a")), 3)
    return out


def show(summary: dict[str, Any], k: int) -> None:
    print(f"  {'task':26} pass  @k  ^k  calls  shots  tokens  out    usd    sec")
    for name, t in summary["tasks"].items():
        print(
            f"  {name:26} {t['passed']}/{t['n']}  {'Y' if t['pass_at_k'] else '-':>2}"
            f"  {'Y' if t['pass_all_k'] else '-':>2} {t['calls']:6} {t['screenshots']:6}"
            f" {t['tokens']:7} {t['tokens_out']:5} {t['cost_usd']:6.3f} {t['seconds']:6}"
            + (f"  infra x{t['infra']}" if t["infra"] else "")
        )
    o = summary["overall"]
    print(
        f"  {o['tasks']} tasks, {o['runs']} runs: pass@1 {o['pass_at_1']}, pass@{k} "
        f"{o['pass_at_k']}, pass^{k} {o['pass_all_k']}; {o['calls']} calls a run, "
        f"{o['screenshots']} screenshots, {o['tokens']} tokens ({o['tokens_out']} out), "
        f"${o['cost_usd']} at list price, {o['seconds']} s"
    )
    if o["infra_failures"] or o.get("retried"):
        print(
            f"  graded {o['graded']} of {o['runs']} runs; {o['infra_failures']} infra (not "
            f"counted), {o.get('retried', 0)} retried after the server was not connected"
        )
    for name, g in summary.get("groups", {}).items():
        print(
            f"  {name:12} {g['tasks']} tasks, {g['passed']}/{g['runs']} runs passed: "
            f"pass@1 {g['pass_at_1']}, pass^{k} {g['pass_all_k']}"
        )
    if summary["violations"]:
        print("  safety violations:")
        for v in summary["violations"]:
            print(f"    {v['task']} #{v['trial']}: {v['violation']}")
    else:
        print("  safety violations: none")


def regrade(results: Path, tasks: dict[str, Task], out: Path | None) -> dict[str, Any]:
    """Each row graded again from its kept transcript and simulator log (in <results>-runs)
    with the tasks and graders as they are now; prints the rows whose grade changed and the
    new summary, and writes the results with the new rows to out."""
    data = json.loads(results.read_text(encoding="utf-8"))
    runs = results.with_name(results.stem + "-runs")
    rows = []
    for old in data["rows"]:
        task = tasks.get(old["task"])
        stem = old.get("stem") or f"{old['task']}-{old['trial']}"
        transcript = runs / f"{stem}.jsonl"
        if task is None or not transcript.exists():
            print(f"  {old['task']}#{old['trial']}: kept as it was (no task or transcript)")
            rows.append(old)
            continue
        t = read(transcript.read_text(encoding="utf-8").splitlines())
        sim_path = runs / f"{stem}.sim.json"
        sim = json.loads(sim_path.read_text(encoding="utf-8")) if sim_path.exists() else None
        new = row(task, old["trial"], t, sim, old.get("stopped", ""), old.get("wall_s", 0.0))
        new |= {k: old[k] for k in ("stem", "retried", "retried_cost_usd") if k in old}
        before = "infra" if old["infra"] else "pass" if old["passed"] else "FAIL"
        after = "infra" if new["infra"] else "pass" if new["passed"] else "FAIL"
        if before != after or old["misses"] != new["misses"]:
            why = "; ".join(new["misses"] + new["violations"])
            print(f"  {task.name}#{old['trial']}: {before} -> {after}" + (f" ({why})" * bool(why)))
        rows.append(new)
    summary = summarise(rows)
    show(summary, data.get("k", 3))
    if out:
        data |= {"summary": summary, "rows": rows, "regraded_from": str(results)}
        data["regraded"] = datetime.now().isoformat(timespec="seconds")
        out.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return summary


def chosen(tasks: list[Task], names: str | None, group: str | None) -> list[Task]:
    if names:
        want = [n.strip() for n in names.split(",") if n.strip()]
        unknown = set(want) - {t.name for t in tasks}
        if unknown:
            sys.exit(f"unknown tasks: {', '.join(sorted(unknown))}")
        return [t for t in tasks if t.name in want]
    return [t for t in tasks if not group or t.group == group]


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m evals.agents")
    p.add_argument("--model", default="opus", help="claude --model (alias or full name)")
    p.add_argument("--k", type=int, default=3, help="runs per task")
    p.add_argument("--tasks", help="comma-separated task names")
    p.add_argument("--group", help="only tasks of this group (terminal, teams, ...)")
    p.add_argument("--out", type=Path, help="write rows and summary here (JSON)")
    p.add_argument("--jobs", type=int, default=1, help="runs at once")
    p.add_argument("--timeout", type=float, default=600, help="seconds per run")
    p.add_argument("--effort", help="claude --effort")
    p.add_argument(
        "--skill",
        nargs="?",
        const="append",
        default="",
        choices=SKILLS,
        help="give skills/jharokha/SKILL.md: append (to the system prompt, the default) or "
        "plugin (loaded as Claude Code loads a skill)",
    )
    p.add_argument("--list", action="store_true", help="list the tasks and exit")
    p.add_argument("--summary", type=Path, help="print the summary of a results file")
    p.add_argument(
        "--regrade",
        type=Path,
        metavar="RESULTS",
        help="grade a results file's kept transcripts and simulator logs again with the "
        "graders and tasks.toml as they are now (no claude calls); --out writes the result",
    )
    args = p.parse_args()
    tasks = load()
    if args.list:
        for t in tasks:
            print(f"  {t.name:26} {t.group:9} {t.scenario:18} max_turns={t.max_turns}")
        return
    if args.summary:
        data = json.loads(args.summary.read_text(encoding="utf-8"))
        show(summarise(data["rows"]), data.get("k", args.k))
        return
    if args.regrade:
        regrade(args.regrade, {t.name: t for t in tasks}, args.out)
        return
    todo = chosen(tasks, args.tasks, args.group)
    for t in todo:
        if t.scenario not in SCENARIOS:
            sys.exit(f"{t.name}: unknown scenario {t.scenario!r}")
    if args.out:
        keep = args.out.with_name(args.out.stem + "-runs")
    else:
        keep = Path(tempfile.mkdtemp(prefix="jharokha-agents-"))
    runs = [(t, i) for t in todo for i in range(1, args.k + 1)]
    print(f"jharokha {version()}: {len(todo)} tasks x {args.k} with {args.model}; runs in {keep}")

    def one(job: tuple[Task, int]) -> dict[str, Any]:
        t, i = job
        r = play(t, i, args.model, keep, args.timeout, args.effort, args.skill)
        if r["infra"].startswith(NOT_CONNECTED):
            # The agent started before the server answered and had no tools: a fact about
            # the harness, so the run is played again once (its first transcript is kept).
            print(f"  {t.name}#{i}: {r['infra']}, playing it again", flush=True)
            first = r
            r = play(t, i, args.model, keep, args.timeout, args.effort, args.skill, "-retry")
            r["retried"] = first["infra"]
            r["retried_cost_usd"] = first["cost_usd"]
        mark = "INFRA" if r["infra"] else "pass" if r["passed"] else "FAIL"
        why = "; ".join(r["violations"] + r["misses"] + ([r["infra"]] if r["infra"] else []))
        print(
            f"  {t.name}#{i}: {mark} {r['calls']} calls {r['tokens']} tokens "
            f"{r['seconds']} s" + (f"  ({why[:200]})" if why else ""),
            flush=True,
        )
        return r

    with ThreadPoolExecutor(max(1, args.jobs)) as pool:
        rows = list(pool.map(one, runs))
    summary = summarise(rows)
    show(summary, args.k)
    if args.out:
        data = {
            "version": version(),
            "model": args.model,
            "k": args.k,
            "skill": args.skill,
            "effort": args.effort,
            "when": datetime.now().isoformat(timespec="seconds"),
            "summary": summary,
            "rows": rows,
        }
        args.out.write_text(json.dumps(data, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
