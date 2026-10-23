# Development

```
uv sync
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest            # includes tests on the real Windows OCR engine and the second engine
```

## Try it without a remote session

`tests/harness/fake_session.py` opens a local window showing one of the generated eval screens. The allowlist of client processes is `msrdc.exe`, `mstsc.exe`, `windowsapp.exe`, `windows365.exe`; override it with `JHAROKHA_PROCESSES` (comma-separated) to accept the fake window:

```
uv run python tests/harness/fake_session.py terminal     # also: teams, outlook, browser, ids, lock; optional seed
$env:JHAROKHA_PROCESSES = "python.exe,pythonw.exe"
uv run jharokha --window-title "Fake Remote" read
uv run jharokha --window-title "Fake Remote" bench
```

## Evals

```
uv run python -m evals --quick            # every push in CI, a few seconds
uv run python -m evals --full             # v0.x.0 tags in CI
uv run python -m evals --quick --check    # exit 1 on a drop against evals/baseline-quick.json
uv run python -m evals --quick --update   # accept the current numbers as the baseline
uv run python -m evals --show terminal    # truth against what was read, line by line
```

`evals/screens.py` generates screens shaped like the work jharokha does: a PowerShell terminal in Windows Terminal (git log, directory tables, `sls` hits, log tails, JSON, a wrapped URL), a Teams-like chat, an Outlook-like mailbox, an Edge-like browser (address bar, favourites, a request queue page), five lock and sign-in screens, lookalike-heavy IDs. The app screens carry the pane backgrounds, divider lines and session taskbar measured on the apps (Teams: rail divider x=88, header dividers y=62 and 142, chat list 245 against thread 255; new Outlook: rail divider x=61, list and reading pane split at x=500, ribbon y=103 to 154; Edge: address box y=49 to 94, favourites at y≈142, page from y=191; Windows Terminal: tab bar divider y=38, scrollbar x=1156 to 1160 on (12,12,12); taskbar y=1121 to 1200), and each records its true pane boxes. The terminal is Cascadia Mono, Windows Terminal's font, rows 1.15 font heights apart (23 px at 20 px, as live); the font is vendored in `evals/fonts/` (SIL OFL, licence beside it) and loaded for the eval process only (`AddFontResourceExW` with `FR_PRIVATE`). Names, IDs, URLs and log lines come from fixed lists and a seeded generator; nothing comes from a real session, and none ever should. Seeds under 100 are for tuning, the eval scores seeds from 100. `evals/render.py` draws them with GDI and ClearType as real apps do, then through JPEG with 4:2:0 chroma, standing in for the RDP codec; `--full` adds clean GDI and PIL.

Per screen it scores CER and WER (edit distance over the truth, lines joined in reading order), bag-of-words F1 (order ignored, so F1 high with CER high means right words, wrong order), lines exact, lookalike misses, tokens of a concise read, whether `find` lands inside the drawn word, whether lock screens and only lock screens read as locked, whether the layouts anchor picks the right app, and panes: for each true pane the best IoU of a pane found from the pixels (mean, and the share at 0.8 or more). The summary averages per app and renderer; `--check` fails when a number is worse than the baseline by more than 0.02 (rates), 10% (tokens) or 1 (lookalike misses). Timings are printed, not gated: shared runners are too noisy for them.

The numbers are deterministic: this computer (Windows 11) and CI's `windows-latest` runner gave identical summaries for both sets. If they ever drift, run the workflow by hand (`gh workflow run ci.yml`): it regenerates both baselines on the runner and uploads them as an artifact.

MCP end to end: `npx @modelcontextprotocol/inspector --cli uv run jharokha --method tools/list`, or register it in Claude Code with `claude mcp add --transport stdio jharokha -- uv run --directory <repo> jharokha`.

## Open

- Terminal rows through the second engine (strips at the grid's pitch, words placed by its character width), where Windows OCR drops text on Cascadia Mono.
- Edge's tab strip, which runs into the toolbar through the selected tab.
- No shared convention for marking untrusted tool output was found; ours is the `<screen_text>` wrapper.

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag `vX.Y.Z`, push.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and `pre-commit install` to run it before each commit.
