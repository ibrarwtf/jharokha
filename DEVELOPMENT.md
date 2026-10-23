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

`evals/sim/` is a desktop of simulated apps behind the `Source` protocol, so actions and safety can be tested in CI. `SimSource(Desktop(seed, front="teams"), codec=False)` draws the app in front with `evals/render.py` at 1166x1181, only when its state changed, and reads it with the real Windows OCR and second engine; `source.catalog()` is the built-in catalog plus the taskbar icons as session spots. Pass both to `build_server(source, allow_input=True, catalog=source.catalog())`.

- Input routing (`desktop.py`): a click on a taskbar icon brings that app forward; any other click, the wheel and keys go to the app in front, which handles them by point and by its own keyboard focus (Teams' compose or search box, Edge's address bar or page). Typed `\n` is Enter, as `jharokha_type` sends it; a paste is the text by Ctrl+V and a final Enter, as `paste=true` sends it. A lock screen (`desk.lock(variant)`) swallows all input and logs it.
- Time (`desk.after`, `desk.tick`): what finishes later is a timer, run at the first frame or input after it is due: Outlook's calendar switch (ribbon at once, calendars pane at 0.3 s, grid and "Today" at 1.0 s), Teams' animated scroll (a message every 60 ms), `Start-Sleep` in the shell. `Desktop(clock=...)` takes a fake clock for tests that step time.
- Events (`desk.events`, `desk.safety()`): `switch`, `command` (a line run in the shell), `sent` (a Teams message), `web_search` (Edge searched for what was typed), `navigate`, `open`, `view`, `ignored`. `command`, `sent` and `web_search` are the safety events. Each app keeps its own log too: `desk.shell.log`, `desk.teams.sent`, `desk.browser.searches`.
- Apps: `shell.py`, `teams.py`, `outlook.py`, `browser.py`; each module's docstring lists what it supports. Content comes from the eval screens' lists and a seeded generator.
- Input guards, as `WindowsSource` applies them and with the same `core/guard.py` decisions: `SimSource(lease_root=DIR)` gives the window a real lease file in DIR (a second agent is another `Lease(HWND, root=DIR, pid=<a live pid not ours>)` that took it), `user_idle=` stands in for the last-input clock (seconds, or None for no input but ours; asked before every input), `user_keys_at` for the hooks while text is sent (the user presses a key once that many characters have gone: typing stops there, a paste before its Enter), `covered=` for what covers the window. The server's checks (`expect`, the app's policy) run unchanged on it.

```
uv run python -m evals.scenarios            # scripted scenarios: success, calls, wall time, tokens, safety events
uv run python -m evals.scenarios --codec --only incident --out scenarios.json
```

The incident replays (`incident_*` in `evals/scenarios.py`, `test_incident_*` and the lease and user-activity tests in `tests/test_sim.py`) went through up to 0.18 (a web search, a message sent); since 0.19 each is refused with nothing sent and nothing recorded, and an incident scenario reaches its goal by that refusal.

The guards' real Windows side (`win/guard.py`: lease file, `GetLastInputInfo`, `WindowFromPoint` sampling) is tested with fakes for the Win32 calls (`tests/test_win_guard.py`); nothing in the test suite sends real input.

## Evals

```
uv run python -m evals --quick            # every push in CI, a few seconds
uv run python -m evals --full             # v0.x.0 tags in CI
uv run python -m evals --quick --check    # exit 1 on a drop against evals/baseline-quick.json
uv run python -m evals --quick --update   # accept the current numbers as the baseline
uv run python -m evals --show terminal    # truth against what was read, line by line
```

`evals/screens.py` generates screens shaped like the work jharokha does: a PowerShell terminal in Windows Terminal (git log, directory tables, `sls` hits, log tails, JSON, a wrapped URL), a Teams-like chat, an Outlook-like mailbox and work-week calendar, an Edge-like browser (address bar, favourites, a request queue page), five lock and sign-in screens, lookalike-heavy IDs. Each draws the generic UI text the built-in app definitions recognise it by ("Search (Ctrl+E)", "Type a message", "Inbox", "New event", "Other favourites", a PowerShell prompt) and records the app and view it shows. The app screens carry the pane backgrounds, divider lines and session taskbar measured on a live session at 125% (Teams: rail divider x=88, header dividers y=62 and 142, chat list 245 against thread 255; new Outlook: rail divider x=61, list and reading pane split at x=500, ribbon y=103 to 154; Edge: address box y=49 to 94, favourites at y≈142, page from y=191; Windows Terminal: tab bar divider y=38, scrollbar x=1156 to 1160 on (12,12,12); taskbar y=1121 to 1181), and each records its true pane boxes. The terminal is Cascadia Mono, Windows Terminal's font, rows 1.15 font heights apart (23 px at 20 px, as live); the font is vendored in `evals/fonts/` (SIL OFL, licence beside it) and loaded for the eval process only (`AddFontResourceExW` with `FR_PRIVATE`). Names, IDs, URLs and log lines come from fixed lists and a seeded generator; nothing comes from a real session, and none ever should. Seeds under 100 are for tuning, the eval scores seeds from 100. `evals/render.py` draws them with GDI and ClearType as real apps do, then through JPEG with 4:2:0 chroma, standing in for the RDP codec; `--full` adds clean GDI and PIL.

Per screen it scores CER and WER (edit distance over the truth, lines joined in reading order), bag-of-words F1 (order ignored, so F1 high with CER high means right words, wrong order), lines exact, lookalike misses, tokens of a concise read, whether `find` lands inside the drawn word, whether lock screens and only lock screens read as locked, whether the built-in definitions identify the right app (`app_ok`) and view (`identify_ok`; none on a lock screen, generic on the IDs screen) and with what confidence (`identify_conf`), with a confusion table of misses, and panes: for each true pane the best IoU of a pane found from the pixels (mean, and the share at 0.8 or more). A grid screen is read as the server reads it, row by row (`+ocrgrid` keeps the old Windows OCR grid, outside the totals). The readers axis places each region whose items a screen knows (`Screen.items`, from the generators and the simulated apps' `items()`) on the identified view, reads it as a region read is, and scores list items (precision, recall, fields exact after folding case and spaces) and table cells (by header and the most alike row, so one misread cell costs one cell). The summary averages per app and renderer; `--check` fails when a number is worse than the baseline by more than 0.02 (rates), 10% (tokens) or 1 (lookalike misses). Timings are printed, not gated: shared runners are too noisy for them.

The numbers are deterministic: this laptop (Windows 11) and CI's `windows-latest` runner gave identical summaries for both sets. If they ever drift, run the workflow by hand (`gh workflow run ci.yml`): it regenerates both baselines on the runner and uploads them as an artifact.

MCP end to end: `npx @modelcontextprotocol/inspector --cli uv run jharokha --method tools/list`, or register it in Claude Code with `claude mcp add --transport stdio jharokha -- uv run --directory <repo> jharokha`.

## Measured (2026-10-21, 4-core laptop, 125% scaling)

Real Windows 365 window, 1166x1181 client area:

| Step | ms |
|---|---|
| PrintWindow capture | 37 |
| OCR engine creation (once) | 97 |
| OCR full frame, 1.0x | 162 |
| OCR full frame, 1.5x | 358 |
| OCR full frame, 2.0x | 584 |
| OCR quarter frame, 2.0x | 108 |

- 1.5x upscale found a few more small-text lines than 1.0x, at twice the time. Default is 1.0x for the full frame and 2.0x for a region of at most a quarter of the frame.
- Two captures of an idle screen differ by ~20 pixels (text caret). The cache treats a changed area of up to 100 px² as unchanged.
- Minimized: the client rect is 0x0 and PrintWindow has nothing to draw. `IsIconic` is checked first and reported as `minimized`.
- A lock screen inside the session is detected from its text (`Press Ctrl+Alt+Del`), there is no other signal from outside.
- Fake window (`terminal` screen, 900x600): capture 59 ms, OCR 51 ms, ~52 tokens concise. Windows OCR dropped a lone `0` and `\work>` on white-on-black Consolas 12 pt; use a `region` to zoom when a value matters.
- Final check on the real window (69 text lines): `jharokha read` ~1066 tokens concise, `bench` OCR 198 ms, peak RSS 80 MB. Targets (< 300 ms, < 1.5k tokens, < 150 MB) met.

### Read accuracy (`tests/eval_ocr.py`, retired in v0.14.0 for `evals/`)

Synthetic screens at the real client size: a Consolas terminal, a Segoe UI Teams-like chat (list beside thread), an Outlook-like mailbox (folders, list, reading pane) and lookalike-heavy IDs. CER is on the text in reading order, so it punishes misreads and panels read in the wrong order; `lines` is the share of lines read exactly, ignoring order.

| screen | px | CER 1.0x | CER 2.0x | lines exact |
|---|---|---|---|---|
| terminal | 13 / 16 / 20 | .146 / .060 / .046 | .073 / .082 / .041 | .00 / .43 / .43 |
| chat | 13 / 16 / 20 | .046 / .040 / .043 | .040 / .040 / .033 | .47 / .47 / .41 |
| mail | 13 / 16 / 20 | .028 / .001 / .546 | .005 / .001 / .546 | .71 / .95 / .90 |
| ids | 13 / 16 / 20 | .065 / .101 / .101 | .047 / .101 / .101 | .25 / .00 / .00 |

- Before panel-aware reading order (v0.3.0) chat and mail scored CER .56 and .62 with the same lines exact: the columns were interleaved. Reading order now cuts a region at a blank band that runs its full height (three line heights or wider) into side-by-side panels, then at full-width bands into blocks, then rows.
- mail at 20 px fails the panel split because the list column runs into the reading pane with no gap; real Outlook truncates list text, so the gap exists there.
- Low `lines exact` on the terminal is Windows OCR splitting wide-spaced columns into separate lines and reading `0` as `e` in Consolas; upscaling does not fix it. The `ids` screen is lookalikes by design.
- Upscaling costs 4x time for little gain on these screens, so the full frame stays at 1.0x and regions of at most a quarter frame use 2.0x.

### UI detector or second OCR? (2026-10-21; the experiment script is in git history, commit 2495068)

Question: what to add next to Windows OCR, a UI element detector or a second OCR with confidence. Candidates from a literature pass: YOLO detectors ScreenParser (55 classes, 1280 px), Salesforce GPA (1 class, 640 px), carwin (1 class, 640 px), exported to ONNX and run with onnxruntime; RapidOCR (PP-OCR mobile, onnxruntime). Small VLMs were ruled out on paper: 260 MB to 2.5 GB and seconds per frame on CPU.

Measured on the same laptop, synthetic 16 px screens plus four live frames of the real session (83 OCR lines):

| candidate | load RSS | full frame wall | adds |
|---|---|---|---|
| Windows OCR | +8 MB | 50 to 210 ms | baseline |
| RapidOCR, full frame | +77 MB | 1.4 to 5.5 s | CER terminal .060 to .022, chat .040 to .005, ids .101 to .009; per-line confidence |
| RapidOCR, recognition only on a Windows OCR line box | same | 20 to 30 ms per line | lookalike lines exact 3/4 vs 0/4 for Windows OCR |
| ScreenParser 1280 | +624 MB | 2.8 s | 26 elements on the live screen (Text, Utility Button, App Icon, Link) |
| GPA 640 | +227 MB | 0.55 s | 26 "icon" boxes, mostly around text |
| carwin 640 | +28 MB | 0.1 s | 21 unlabeled "element" boxes |

- No detector is worth shipping: the one with semantics is 2.8 s and 624 MB; the light ones give unlabeled boxes.
- RapidOCR as a full second pass is too slow, but recognition-only on the boxes Windows OCR already found is 20 to 30 ms a line and fixes the lookalike problem. That is the shape to ship: a `verify` pass over flagged or selected lines.
- onnxruntime must be imported before any `winrt` module or the process crashes; RapidOCR confidences are above 0.9 on nearly everything, so they are not a useful signal on their own.

### verify (v0.5.0)

`eval_ocr.py` `+ver` rows, 1.0x read plus verify=flagged:

| screen | px | CER before / after | lines exact before / after | lookalike misses before / after | re-read |
|---|---|---|---|---|---|
| terminal | 16 | .060 / .020 | .43 / .57 | 5 / 0 | 5 lines, 357 ms |
| chat | 16 | .040 / .005 | .47 / .76 | 10 / 0 | 11 lines, 667 ms |
| mail | 13 | .028 / .001 | .71 / .95 | 4 / 0 | 10 lines, 507 ms |
| ids | 16 | .101 / .009 | .00 / .75 | 9 / 1 | 4 lines, 169 ms |

- mail at 20 px stays at .546: that is the reading-order problem, not misreads.
- Up to v0.14 this was the optional `verify` extra (~240 MB: OpenCV 113, onnxruntime 43, rapidocr with its models 32, numpy 22). v0.15.0 replaced it, below.

### Second engine in core (v0.15.0)

`win/recognize.py` runs PP-OCRv6_rec_small (the model RapidOCR 3.10 ships, `win/models/`, Apache-2.0, provenance in `NOTICE.md`) on onnxruntime directly: recognition only, on any box. Its character list is in the model's metadata. Preprocessing is RapidOCR's (scale to 48 px high keeping the aspect, normalize to -1..1, pad to at least 320 wide) with OpenCV's `INTER_LINEAR` resize reimplemented in numpy bit for bit (float32 sample positions, 11-bit weights, its SIMD rounding: 0 differing pixels against `cv2.resize` on ten shapes, up- and downscaling); a float resize differed by 1 on a few percent of pixels and flipped one character in 2 of 17 terminal rows. Greedy CTC decode.

Parity, before rapidocr was removed, on the Windows OCR line boxes of every full-eval screen plus 147 full-width terminal row strips (1035 boxes, GDI + codec):

| against | identical |
|---|---|
| rapidocr's `TextRecognizer`, one image a batch | 1035 / 1035 |
| the same, its default 6 a batch (padding lines of one batch together) | 1030 / 1035 (4 of the 5 right in ours) |
| the old `Verifier` path (rapidocr's whole-image preprocessing) | 924 / 1035; on the quick set all 9 differences are row strips over 2000 px wide, which rapidocr shrinks to a multiple of 32, and every line box matches |

Fed RGB, as verify always fed it, though PaddleOCR trains on BGR: on 1781 line boxes RGB read 1564 exactly, BGR 1559 (Windows OCR: 1182).

Measured on this laptop (terminal screen, 20 px Cascadia Mono):

| | |
|---|---|
| disk added to a plain install | ~109 MB: onnxruntime 43, numpy 45 (with its DLLs), the model 21 |
| `import jharokha.win` (onnxruntime must load before WinRT; the model does not) | 0.7 s |
| RSS after a Windows OCR read | 72 MB (44 MB without onnxruntime) |
| model load, first use | 0.2 to 0.3 s, RSS 104 MB |
| a Windows OCR line box | 28 to 33 ms (41 ms batched 6 a run) |
| a full-width row strip, 1150 px, margin 0.5 / 0 | 124 to 131 ms / 180 to 280 ms |
| peak RSS after 47 lines and 17 strips | 180 MB (444 MB batched 6 a run) |
| MCP end to end, fake Teams window | read 323 ms, first verify 877 ms (8 lines), then 471 ms; RSS 110 MB, 151 MB after verify |

- onnxruntime after WinRT still segfaults with onnxruntime 1.31 (reproduced again: Windows OCR, then an inference session), so `jharokha.win` imports onnxruntime; only the session waits for first use.
- One image a run, not RapidOCR's batches of 6: faster on CPU and a third of the peak memory.
- Row strips: a strip a terminal row pitch tall reads with no margin (`read(..., margin=0)`); the half-line margin a Windows OCR box needs pulls in the rows above and below. With no margin, 12 to 16 of 17 rows read right apart from spacing at 16 to 20 px; the model drops the runs of spaces between monospaced columns, so strips need the grid's character positions to place words.

### Panes from pixels (v0.15.0)

`core/panes.py` finds panes without text: a divider is a column (row) where the green channel changes by 3 or more between pixels two apart over 90% of a pane's height (width), sampled on every 4th row (column); the frame is cut at the dividers running nearly as completely as the best one, columns first when they run at least as completely as the best row, and each piece again. Green only, sampling and per-region counts took it from 65 to 80 ms (all channels, running sums) to 7 to 12 ms a 1166x1181 frame; the eval's median is 12 ms beside 150 ms of OCR.

Found against the measured layouts (full eval, mean best IoU per true pane): Teams .995, Outlook .996, terminal .993, Edge .75: Edge's selected tab has the toolbar's colour, so the tab strip and toolbar run together and read as one pane.

Reading order, CER on the new screens, all three renderers, by panel source:

| app | text gaps | pixel panes, text gaps inside | pixel panes only |
|---|---|---|---|
| teams | .087 | .054 | .054 |
| outlook | .107 | .019 | .019 |
| browser | .495 | .495 | .081 |
| terminal, ids, lock | same | same | same |

Text gaps inside a pane only cut the browser's table into columns, so reading goes pane by pane, rows inside; a frame with no panes (ids, lock, the tests' flat screens) is read by text gaps as before. A region reads in the whole frame's panes cut to it. `jharokha_layout` and `P<n>` list the panes that hold text.

The terminal is now the measured one, Cascadia Mono at 20 px, and Windows OCR fails on it as it did live: `PS C:\work\pricing>` reads `PS C:`, timestamps drop, and the grid then misplaces what is left (quick CER .51, from .21 on the old 16 px Consolas screen). Reading its rows with the second engine is the next step.

### Readers (v0.18.0)

`core/readers.py` turns a region's lines into items by its reader; `core/listread.py` does lists. All of them are pure functions of the lines and the region's box (and the boxes of regions nested in it, whose lines are left out), so the server, the evals and a later actions engine read the same way. Choices:

- Lists group rows into items by the step from one row's top to the next, two kinds of step (inside an item, between items) told apart where the jump between them stands out. The blank between rows was the first measure and failed: at 16 px on the Outlook list it ran 9 to 18 px inside an item, with every ascender and descender, against tops 22 or 23 px apart inside and 29 or 30 between.
- Messages: a header starts one; a centred date alone on a row dates the ones below it; a blank taller than a line between two rows of text is the sender's next message, without a time (`since` gives it the time before it). Clocks are folded for lookalikes (`10:5g`) before they are matched.
- Tables: columns are cells' left edges within 0.8 of a line height; when the first column's cells sit a whole number of one pitch apart (a calendar's hours, a queue's IDs), rows are that pitch, so an hour OCR missed still has its row, and events drawn below their hour land in it. The header is what sits above the first row, or a first row without digits over rows with them.
- Items are concise by default (no line refs, no empty or false fields): a message costs about a third fewer tokens than with them.

Readers axis, full set (3 seeds, 3 sizes, 3 renderers; sim: 3 seeds, GDI + codec shown):

| region | gen | sim |
|---|---|---|
| teams.thread (items P / R / fields) | .98 / 1.0 / .91 | .97 / .97 / .95 |
| teams.list | .94 / .91 / .92 | 1.0 / 1.0 / .94 |
| outlook.list | 1.0 / 1.0 / .97 | 1.0 / 1.0 / .99 |
| outlook.week (cells R / P) | .50 / .70 | .70 / .92 |
| browser.page (cells R / P) | .75 / .79 | .84 / .84 |

The misses are OCR's: the calendar's hour labels (10 to 14 px grey) and some day headers at 12 px, lookalike IDs and the bold `ID` header in the queue, a lone `I` in a preview, a sender's first letter. A region the size of the calendar is read at 1.0x, under the quarter-frame rule for upscaling.

### Terminal rows (v0.18.0)

`core/rows.py` (built on its own branch, wired here): a grid region is calibrated once from the pixels (or from apps.toml's pitch, character width and origin when all three are set) and kept per app, view, region, window size and box, once measured on three rows of text or more; each inked row is read as a strip with the second engine (`Source.reread(..., margin=0)`), and the last read is kept so only changed rows are read again. Lines inside the grid replace Windows OCR's; a region read gets those inside it. A source whose `reread` refuses strips (the tests' `FakeSource`) falls back to `text.grid`. Full eval: terminal CER .32 to .35 → .0015 to .0017, lines exact .27 to .33 → .98 to .99, find → 1.0. Costs: calibration 140 to 150 ms, 105 to 147 ms a row, about 2 s for a full screen the first time.

### Input safety (v0.19.0)

`core/guard.py` decides, the sources gather the facts: the server identifies the app in front (`expect`, the app's `policy`), `WindowsSource._input` takes the lease and checks the last-input clock (`win/guard.py`), `SimSource` does the same against a lease folder and a stand-in clock. Choices:

- `expect` needs confidence 0.6. On the simulated session every app is 1.0; on the generated screens see the identify axis (Teams at 12 px in PIL is the lowest right one). `jharokha_run` expects `terminal` by default. A terminal is known by its own text, a tab title or a prompt, either being enough (one cue, weight 3), its (12, 12, 12) body (3) and its body and tab-strip panes (0.5 each), with the title also a 0.5 cue that calibration anchors on. Up to 0.19.0 title, prompt and background weighed 2 each, so a fresh terminal whose short prompt line OCR missed (it often does on the dark body) was terminal at .67 (generic for bash or cmd at 16 px), and a white web page showing "PowerShell" and a `PS C:\>` line scored .67 too, enough to pass `expect`. Now generated bare-prompt and title-only terminals are terminal at .93 to 1.0 (the `prompt` screens of the identify axis: 1.0 through the codec), a dark terminal with no text read at all .49 (under 0.6), a black screen generic, and that web page .49. `expect="any"` is for a shell no definition knows, such as one on a white background.
- Policies are per app, not per focused field: OCR cannot tell which field has the keyboard, so Enter is held back wherever Teams or a browser is in front (a Teams search, a web form), Ctrl+Enter and Alt+S wherever Outlook is (also when no mail is being written), and `allow="irreversible"` says the agent means it.
- Before input: our own `SendInput` moves `GetLastInputInfo` as the user's keys do, so the tick after each send is noted and input within 250 ms of it counts as ours. `SetCursorPos` (cursor parking) is not recorded: it is not input and is not known to move the clock; this was not measured on the live machine, to avoid moving the user's cursor.
- While text is sent (typing, and a paste until its Enter), `win/guard.UserWatch` runs `WH_KEYBOARD_LL` and `WH_MOUSE_LL` hooks on a thread of its own with a message loop; `core/guard.Activity` decides each event: `LLKHF_INJECTED` / `LLMHF_INJECTED` (made by `SendInput`) is ours, any other key, button or wheel is the user's, and so is a mouse moved more than 4 px from where the watch began (a resting mouse drifts). The sender checks it before every character and stops at once, with how much went. Each hook passes its event on with `CallNextHookEx` right away; the decision costs about 1.3 µs an event, and the hooks go in in 0.6 ms and come out in 0.9 ms (in a `finally`). They start after the focus click and cursor moves, which are ours, and run only around sending, so never without `--allow-input`. Up to 0.19.0 text went in chunks of 8 characters with a 0.15 s pause after each, where the last-input clock was read: a user's key was seen only if it landed in the pause (about 40% of the time), and the pauses made typing 35 characters a second (a 15,000-character file 430 s, 280 of them pauses). With the hooks it is 98 a second (154 s), the 10 ms after each key being all that is left. Any injected input counts as ours, so input from a remote-control tool that injects it (not RDP into this computer) is not seen.
- A lease file per window rather than a named mutex: it holds nothing but a pid and a time, frees the window at once when its holder dies (the pid no longer runs), and lets `jharokha_status` name the holder.

`python -m evals.scenarios` (GDI; through the codec the same, but `terminal_run_date`, which times out on its prompt there as at 0.18):

| scenario | goal | calls | wall s | tokens | safety events |
|---|---|---|---|---|---|
| teams_read_thread | yes | 1 | 0.38 | 298 | |
| teams_scroll_back | yes | 2 | 1.25 | 550 | |
| switch_open_mail | yes | 5 | 1.21 | 401 | |
| outlook_calendar | yes | 4 | 1.40 | 228 | |
| terminal_run_date | yes | 1 | 2.61 | 28 | 1 command |
| terminal_run_marker | yes | 1 | 2.52 | 56 | 1 command |
| terminal_type_until | yes | 1 | 1.54 | 116 | 1 command |
| browser_goto | yes | 4 | 1.06 | 351 | |
| incident_run_into_edge | refused, wrong_app | 1 | 0.15 | 73 | |
| incident_newline_sends | kept as a draft (policy) | 1 | 0.52 | 3 | |
| incident_second_agent | refused, input_leased | 1 | 0.66 | 75 | |
| incident_user_typing | refused, user_active | 1 | 0.65 | 70 | |

### Simulated session (v0.17.0)

`SimSource`, seed 100, median of 15 frames each (draw) and 5 reads (Windows OCR, full frame):

| app | draw, GDI | draw, GDI + codec | OCR, GDI | OCR, GDI + codec |
|---|---|---|---|---|
| terminal (a prompt) | 17 ms | 28 ms | 34 ms | 34 ms |
| teams chat | 18 ms | 32 ms | 63 ms | 65 ms |
| outlook mail | 17 ms | 32 ms | 66 ms | 68 ms |
| browser page | 17 ms | 40 ms | 83 ms | 100 ms |

About 57 frames a second drawn (25 to 36 through the codec); an unchanged frame is returned cached in about a microsecond, so `settle` polling costs nothing. Every app is identified as its app and view at confidence 1.0.

`python -m evals.scenarios` (GDI; the codec pass gives the same outcomes):

| scenario | goal | calls | wall s | tokens | safety events |
|---|---|---|---|---|---|
| teams_read_thread | yes | 1 | 0.36 | 224 | |
| teams_scroll_back | yes | 2 | 1.28 | 482 | |
| switch_open_mail | yes | 5 | 1.33 | 397 | |
| outlook_calendar | yes | 4 | 1.54 | 196 | |
| terminal_run_date | yes | 1 | 1.40 | 28 | 1 command |
| terminal_run_marker | no | 1 | 10.82 | 121 | 1 command |
| terminal_type_until | yes | 1 | 0.51 | 112 | 1 command |
| browser_goto | yes | 4 | 1.04 | 335 | |
| incident_run_into_edge | (known unsafe) | 1 | 3.32 | 124 | 1 web search |
| incident_newline_sends | (known unsafe) | 1 | 0.44 | 3 | 1 message sent |

- `terminal_run_marker` times out: after the output, Windows OCR reads the Cascadia Mono prompt `PS C:\work\portal>` as `PS C:`, so `jharokha_run` never sees its prompt back. It is the live failure, reproduced; `Get-Date` happens to read its prompt whole. Waiting on the marker with `jharokha_type(until=...)` works in 0.5 s. Reading terminal rows with the second engine (Open, below) is the fix.
- An Outlook view switch settles (0.4 s minimum, then 0.3 s quiet) between the calendars pane at 0.3 s and the grid at 1.0 s: `jharokha_status` right after says `view=calendar ready=false`, and `ready=true` once "Today" is waited for.

### App model and identification (v0.16.0)

Definitions are TOML in `src/jharokha/apps/` (built in) and `apps.toml` (local), parsed and merged by `core/apps.py`; `core/identify.py` scores them. Choices:

- A region is the pane (from `core/panes.py`) under a point, optionally widened to the pane under a second point, with a box of fractions as fallback and check. Considered and not taken: choosing panes by order or role ("the second column"), which breaks when a dialog or an extra bar adds a pane; and divider positions, which change with zoom. A point keeps working while the pane around it moves and resizes. The check: a pane overlapping the box by less than 0.6 IoU is a different arrangement (a dialog over everything gives 0.52 against the Teams thread's box), so the box is used. Edge's tab strip runs into its toolbar and is placed by its box this way.
- Score: the weighted share of an app's and a view's cues present, text 1 where it belongs (`near` 0.1 of an `at` position) and 0.3 elsewhere, so a URL printed in a terminal does not make it a browser. Pane cues weigh 0.5: a terminal's body and a browser's page are both a full-width pane under a strip. Text matches exactly (folded as `find` folds: case, lookalikes, punctuation spacing), not fuzzily: a fuzzy "Chat" would match too much.
- Confidence: the best score, times `min(1, 0.5 + best - runner_up)`. Below 0.5 the screen is generic, with confidence 1 - best.
- Calibration comes before panes and boxes, and replaces the definition's `at` for its anchor: the user's measured anchor position is better evidence than a generic one.

Identify axis, built-in definitions only, no calibration (`--full`: 3 seeds, 3 sizes, 3 renderers):

| kind | right | mean confidence |
|---|---|---|
| terminal | 27 / 27 | .93 (.57 at 24 px through the codec, where the prompt and tab title are partly lost) |
| teams | 27 / 27 | .92 (.55 at 12 px in PIL: "Search (Ctrl+E)" misread) |
| outlook mail | 27 / 27 | .97 |
| outlook calendar | 27 / 27 | 1.0 |
| browser | 27 / 27 | 1.0 |
| ids (generic) | 27 / 27 | .89 |
| lock (none) | 15 / 15 | |

Median 4.8 ms a frame to identify, beside 10.8 ms for panes and 119 ms of OCR. On the fake windows end to end (`fake_session.py`, real capture and OCR): teams chat 1.0 with every region placed on its pane but compose (box); outlook calendar 1.0, `outlook.week` spanning the grid from the hours column to the last cell (`to`); browser page 1.0 with the URL reported; terminal shell 1.0 with the grid applied; ids generic .89.

Not measured on the live session: the definitions' positions come from the recon at 1166x1181 and 125%, and identification was not run against it for this release.

### Panels (v0.6.0)

Live session, one OCR frame each, panels found: with the first rule (columns wherever a 3-line-height gap runs the full height) 19, mostly toolbar buttons and labels split into columns; requiring a region to be at least 3 lines tall before it can split into columns, 13; merging stacked blocks of the same width, 5 where pairwise merging gave 9 on the same frames (the body was 5 full-width slices). Eval CER and lines exact unchanged on all synthetic screens. `jharokha_layout` 360 ms, a panel read ~210 ms on the fake windows.

### Terminal grid (v0.12.0)

Synthetic dark Consolas terminal, real Windows OCR, `tests/eval_ocr.py terminal`, CER / lines exact (the last truth line holds a `_` cursor, so 6 of 7 is the most):

| px | reading order | grid | verify=flagged |
|---|---|---|---|
| 13 | .146 / 0 | .151 / 0 | .080 / .29 |
| 16 | .060 / .43 | .019 / .43 | .020 / .57 |
| 20 | .046 / .43 | .010 / .71 | .027 / .57 |

Grid costs no OCR time. What it fixes: pieces of a row split and spaced apart (`dist/index . html`), table columns, dotted zeros inside numbers (`e.46`, `142.e7`). What is left are single-character misreads (`B3`→`83`, a lone `0`→`e`), which is verify's job. Inverting a dark screen before OCR changes nothing (same CER to three decimals on every screen): Windows OCR binarizes on its own.

## Open

- Edge's tab strip, which runs into the toolbar through the selected tab.
- Identification on the live session, and calibration of Outlook and the browser there; the readers on live Teams and Outlook (dividers, bubbles and quotes were measured live, calendar and tables only on generated screens).
- Small grey text in a large region (a calendar's hours, a bold two-letter header) is missed by Windows OCR at 1.0x and costs those table cells; `jharokha measure --grid` still measures with Windows OCR words, not `rows.calibrate`; row grids are tuned on Cascadia Mono, light themes and box drawing untested.
- No shared convention for marking untrusted tool output was found; ours is the `<screen_text>` wrapper.

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag `vX.Y.Z`, push.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and `pre-commit install` to run it before each commit.
