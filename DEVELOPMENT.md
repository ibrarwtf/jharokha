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
uv run python tests/harness/fake_session.py terminal     # also: teams, outlook, calendar, browser, ids, lock; optional seed
$env:JHAROKHA_PROCESSES = "python.exe,pythonw.exe"
uv run jharokha --window-title "Fake Remote" read
uv run jharokha --window-title "Fake Remote" bench
```

`fake_session.py sim [seed] [app]` hosts the simulated session below instead of a still screen: the window redraws as clicks, keys and the wheel arrive, so `--allow-input` commands exercise the real Windows input path (SendInput, foreground checks, cursor parking) against it. The taskbar icons are at y=1151, x=511 (terminal), 559 (Teams), 607 (Outlook), 655 (Edge).

## Simulated session

`evals/sim/` is a desktop of simulated apps behind the `Source` protocol, so actions and safety can be tested in CI. `SimSource(Desktop(seed, front="teams"), codec=False)` draws the app in front with `evals/render.py` at 1200x1200, only when its state changed, and reads it with the real Windows OCR and second engine; `source.catalog()` is the built-in catalog plus the taskbar icons as session spots. Pass both to `build_server(source, allow_input=True, catalog=source.catalog())`.

- Input routing (`desktop.py`): a click on a taskbar icon brings that app forward; any other click, the wheel and keys go to the app in front, which handles them by point and by its own keyboard focus (Teams' compose or search box, Edge's address bar or page). Typed `\n` is Enter, as `jharokha_act(op="type")` sends it; a paste is the text by Ctrl+V and a final Enter, as `op="paste"` sends it. A lock screen (`desk.lock(variant)`) swallows all input and logs it.
- Time (`desk.after`, `desk.tick`): what finishes later is a timer, run at the first frame or input after it is due: Outlook's calendar switch (ribbon at once, calendars pane at 0.3 s, grid and "Today" at 1.0 s), Teams' animated scroll (a message every 60 ms), `Start-Sleep` in the shell. `Desktop(clock=...)` takes a fake clock for tests that step time.
- Events (`desk.events`, `desk.safety()`): `switch`, `command` (a line run in the shell), `sent` (a Teams message or a ticket's reply), `web_search` (Edge searched for what was typed), `navigate`, `open`, `view`, `hover`, a ticket toolbar's `new`, `refresh`, `assign`, `reply`, `close`, `ignored`. `command`, `sent` and `web_search` are the safety events. Each app keeps its own log too: `desk.shell.log`, `desk.teams.sent`, `desk.browser.searches`.
- Apps: `shell.py`, `teams.py`, `outlook.py`, `browser.py`; each module's docstring lists what it supports. Content comes from the eval screens' lists and a seeded generator. `tickets.py` is a ticket queue no built-in definition knows, for learning: `Desktop(seed, front="tickets", tickets=True)` adds it with a taskbar icon of its own (left out by default, so the other apps' taskbar spots stay put). Its toolbar's five icons show tooltips while the pointer rests on them (`Source.hover`, `desk.hover`), and Ctrl+= / Ctrl+- / Ctrl+0 zoom the whole app (0.8 to 1.2).
- Input guards, as `WindowsSource` applies them and with the same `core/guard.py` decisions: `SimSource(lease_root=DIR)` gives the window a real lease file in DIR (a second agent is another `Lease(HWND, root=DIR, pid=<a live pid not ours>)` that took it), `user_idle=` stands in for the last-input clock (seconds, or None for no input but ours; asked before every input), `user_keys_at` for the hooks while text is sent (the user presses a key once that many characters have gone: typing stops there, a paste before its Enter), `covered=` for what covers the window. The server's checks (`expect`, the app's policy) run unchanged on it.

```
uv run python -m evals.scenarios            # scripted scenarios: success, calls, wall time, tokens, safety events
uv run python -m evals.scenarios --codec --only incident --out scenarios.json
```

The incident replays (`incident_*` in `evals/scenarios.py`, `test_incident_*` and the lease and user-activity tests in `tests/test_sim.py`) went through up to 0.18 (a web search, a message sent); since 0.19 each is refused with nothing sent and nothing recorded, and an incident scenario reaches its goal by that refusal.

Actions run on it the way the server runs them: `SessionExecutor(Session(Screen(source), source.catalog()))` (`core/executor.py`, `core/session.py`) is the `Executor`, and `Runner(ex, allow, catalog.policies()).run(actions.builtin()["teams.draft"], {...})` runs one; `tests/test_actions.py` does this for every built-in action and both incident replays, and drives the runner without OCR on a scripted executor. `tests/test_sim.py` calls `jharokha_do` and `jharokha_apps` through the MCP server. The simulated shell knows the statements `terminal.put_file` types for PowerShell, so a file round trip checks real bytes against `desk.shell.written`. The `raw_*` and `do_*` scenarios do the same task step by step (read, find, act) and with one action, and `python -m evals.scenarios` ends with them side by side; `--out FILE` saves a run and `--compare FILE` puts each scenario beside an earlier one.

To write an action, start with `jharokha_do(..., dry_run=true)` (the plan, no input), then run it on the simulated session in a test before a real one. Declare every step's risk honestly: the runner refuses a step riskier than the call allows before anything is sent, and the app's policy can only raise a step's risk, never lower it.

The guards' real Windows side (`win/guard.py`: lease file, `GetLastInputInfo`, `WindowFromPoint` sampling) is tested with fakes for the Win32 calls (`tests/test_win_guard.py`); nothing in the test suite sends real input.

## Evals

```
uv run python -m evals --quick            # every push in CI, a few seconds
uv run python -m evals --full             # v0.x.0 tags in CI
uv run python -m evals --quick --check    # exit 1 on a drop against evals/baseline-quick.json
uv run python -m evals --quick --update   # accept the current numbers as the baseline
uv run python -m evals --show terminal    # truth against what was read, line by line
```

`evals/screens.py` generates screens shaped like the work jharokha does: a PowerShell terminal in Windows Terminal (git log, directory tables, `sls` hits, log tails, JSON, a wrapped URL), a Teams-like chat, an Outlook-like mailbox and work-week calendar, an Edge-like browser (address bar, favourites, a request queue page), five lock and sign-in screens, lookalike-heavy IDs. Each draws the generic UI text the built-in app definitions recognise it by ("Search (Ctrl+E)", "Type a message", "Inbox", "New event", "Other favourites", a PowerShell prompt) and records the app and view it shows. The app screens carry the pane backgrounds, divider lines and session taskbar measured on the apps (Teams: rail divider x=88, header dividers y=62 and 142, chat list 245 against thread 255; new Outlook: rail divider x=61, list and reading pane split at x=500, ribbon y=103 to 154; Edge: address box y=49 to 94, favourites at y≈142, page from y=191; Windows Terminal: tab bar divider y=38, scrollbar x=1156 to 1160 on (12,12,12); taskbar y=1121 to 1200), and each records its true pane boxes. The terminal is Cascadia Mono, Windows Terminal's font, rows 1.15 font heights apart (23 px at 20 px, as live); the font is vendored in `evals/fonts/` (SIL OFL, licence beside it) and loaded for the eval process only (`AddFontResourceExW` with `FR_PRIVATE`). Names, IDs, URLs and log lines come from fixed lists and a seeded generator; nothing comes from a real session, and none ever should. Seeds under 100 are for tuning, the eval scores seeds from 100. `evals/render.py` draws them with GDI and ClearType as real apps do, then through JPEG with 4:2:0 chroma, standing in for the RDP codec; `--full` adds clean GDI and PIL.

Per screen it scores CER and WER (edit distance over the truth, lines joined in reading order), bag-of-words F1 (order ignored, so F1 high with CER high means right words, wrong order), lines exact, lookalike misses, tokens of a concise read, whether `find` lands inside the drawn word, whether lock screens and only lock screens read as locked, whether the built-in definitions identify the right app (`app_ok`) and view (`identify_ok`; none on a lock screen, generic on the IDs screen) and with what confidence (`identify_conf`), with a confusion table of misses, and panes: for each true pane the best IoU of a pane found from the pixels (mean, and the share at 0.8 or more). A grid screen is read as the server reads it, row by row (`+ocrgrid` keeps the old Windows OCR grid, outside the totals). The readers axis places each region whose items a screen knows (`Screen.items`, from the generators and the simulated apps' `items()`) on the identified view, reads it as a region read is, and scores list items (precision, recall, fields exact after folding case and spaces) and table cells (by header and the most alike row, so one misread cell costs one cell). The summary averages per app and renderer; `--check` fails when a number is worse than the baseline by more than 0.02 (rates), 10% (tokens) or 1 (lookalike misses). Timings are printed, not gated: shared runners are too noisy for them.

The numbers are deterministic: this computer (Windows 11) and CI's `windows-latest` runner gave identical summaries for both sets. If they ever drift, run the workflow by hand (`gh workflow run ci.yml`): it regenerates both baselines on the runner and uploads them as an artifact.

MCP end to end: `npx @modelcontextprotocol/inspector --cli uv run jharokha --method tools/list`, or register it in Claude Code with `claude mcp add --transport stdio jharokha -- uv run --directory <repo> jharokha`.

## Open

- Edge's tab strip, which runs into the toolbar through the selected tab.
- Identification, and calibration of Outlook and the browser; the readers on Teams and Outlook (dividers, bubbles and quotes; calendar and tables on generated screens).
- Small grey text in a large region (a calendar's hours, a bold two-letter header) is missed by Windows OCR at 1.0x and costs those table cells; a learned grid's calibration is still measured from Windows OCR words, not `rows.calibrate`; row grids are tuned on Cascadia Mono, light themes and box drawing untested.
- Learning on a live app: tooltips' delay and placement, icons a hover card answers instead, zoom keys an app does not take. Spots do not follow a zoom (they are refused there); per-variant spots would. A learned app gets no input policy, so nothing in it is held back as irreversible until one is written by hand.
- No shared convention for marking untrusted tool output was found; ours is the `<screen_text>` wrapper.

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag `vX.Y.Z`, push.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and `pre-commit install` to run it before each commit.
