"""Agent evals: does jharokha let an agent finish real-shaped work from text alone?

Each task in tasks.toml is a prompt an agent gets, a simulated desktop prepared for it
(setups.py, served by evals.simserver), and how its outcome is graded (grade.py): the final
answer, the simulated desktop's side effects and final state. The runner (run.py) plays a
task through headless Claude Code with jharokha as its only tools.

    uv run python -m evals.agents --model opus --k 3 [--tasks a,b] [--out results.json]
"""
