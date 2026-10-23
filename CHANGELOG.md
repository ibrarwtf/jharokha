# Changelog

## v0.20.0

- Actions: named, checked sequences of steps an agent runs in one call with a structured result. `jharokha_do(action, args, allow="reversible", dry_run=False)` runs one; `jharokha_apps` lists the apps, their views, regions (reader, focus) and input policies, the session's regions and spots, and every action with its arguments, risk and description. Built in (`apps/actions/*.toml`): `teams.open_chat`, `teams.new_messages` (messages at or after a time, scrolling the thread back until one at or before it is on screen), `teams.draft` (newlines as Shift+Enter, never sends), `outlook.unread`, `outlook.open`, `browser.goto`, `browser.click_sequence`, `terminal.run`, `terminal.put_file`. Steps: focus (switching apps by the taskbar spot), click (locators: text, a region, right of / left of / below / above, a spot, the nth item of a region), type, press, scroll, wait, read (a region as its reader's items, `since` a time), extract, repeat, run, put_file; each may expect a condition afterwards. The result: fields, items, lines, output and exit code, the steps done with their risk and time, the failed step with its reason, a code and the closest texts on screen, and safety events, inside the untrusted wrapper. Only arguments fill a step's placeholders, never screen text. `dry_run=true` returns the plan and works without `--allow-input`; everything else needs it (`input_disabled`).
- One guard for tools and actions, with the same codes: an action's front check is `core/guard.check_front` (`wrong_app`), its keys `check_keys` under the app's policy (`irreversible`), and the Source's lease and user-activity checks stop it as they stop a tool (`input_leased`, `user_active`). The app definition's `policy` is the only policy: a key it calls irreversible makes the step irreversible whatever the step declares (so `browser.goto` is irreversible, Enter in Edge), and a typed newline is the policy's (Teams: Shift+Enter), else refused. A step riskier than min(the action's `max_risk`, the call's `allow`) is refused before any input. Commands, file writes and navigation therefore need `allow="irreversible"`.
- Your own actions in `apps.toml` (top-level `[action.<app>.<name>]`, beside calibration) or the file `$JHAROKHA_ACTIONS` names; a local action replaces a built-in one of the same id. README has the format and an example.
- `jharokha_run` reads an end marker the command prints (`core/shell.py`: a per-run token split in what is typed, with `$?` and `$LASTEXITCODE`, or the exit code for cmd and bash) instead of waiting for the prompt, and returns `run_ms`, `exit_code`, `ok` and the output. `shell` is `powershell` (default), `cmd`, `bash`, or `other`, which (as `paste=true`) waits for the prompt as before, without an exit code. The marker is read through the grid rows as the read tools read them, since Windows OCR could drop `False` or the marker row on Cascadia rows.
- `terminal.put_file` writes a file in the shell in front: base64 in chunks of at most 400 characters, each confirmed by index and length and retyped up to twice, then decoded there and its SHA-256 compared (`exact`, or `folded` when OCR lookalikes had to be folded). There is no get_file yet.
- `core/session.py` holds what the server's read tools did inline (the app and view on a frame, regions placed, grid rows read by the second engine), and `core/executor.py` is the actions' executor over it, so an action reads the screen exactly as `jharokha_read_text` does. Its frame ids follow the pixels, so a scroll that changed nothing is seen as such on a real capture too. Locators' "nth item" and the runner's items use the region's reader (`core/readers.py`, `listread.since`, `listread.merge`, new `listread.before`) instead of a list grouping of their own.
- Scenarios (`python -m evals.scenarios`): the same task with the raw tools and with one action. Incident replays through actions: `terminal.run` with Edge in front refused (`wrong_app`, nothing typed), `teams.draft` with a newline kept as a draft.

## v0.19.1

- Someone using this computer while jharokha types is now seen at their first key or click, not by chance. While text is sent (typing, and a paste until its final Enter), low-level keyboard and mouse hooks (`WH_KEYBOARD_LL`, `WH_MOUSE_LL`) run on a thread of their own; an event `SendInput` did not make (no `LLKHF_INJECTED` / `LLMHF_INJECTED`) is the user's: any key, button or wheel, or a mouse moved more than 4 px. Typing stops before the next character with `user_active` saying how much went, as before. Each event is passed on at once (about 1.3 µs to decide); the hooks go in in 0.6 ms and come out in 0.9 ms, always (also on an error), and run only around sending, so never without `--allow-input`. The check before input is still the last-input clock. Up to 0.19.0 text went in chunks of 8 characters with a 0.15 s pause after each, and a key was seen only if it landed in a pause, about 40% of the time; without the pauses typing is about three times faster. If the hooks cannot be installed nothing is sent (`watch_failed`).
- A terminal showing only a fresh prompt, or only its tab's title, identifies as `terminal` above 0.8, so `jharokha_run` no longer needs `expect="any"` there. Either the title (also as OCR mangles it: `owerShelI`, `:ommand Prompt`, `Jbuntu`; Ubuntu and bash now too) or a prompt (PowerShell's, cmd's, `user@host:path$`; a spaced colon or lost drive letter tolerated) counts as the terminal's own text, one cue, alongside the (12, 12, 12) body and the body and tab-strip panes. New generated `prompt` screens (a bare prompt per shell, and a title-only terminal) on the identify axis. A dark screen with no text stays under 0.6, a black one is generic, and a white page showing "PowerShell" and a `PS C:\>` line no longer passes `expect="terminal"`.
- Policies: Outlook holds back Ctrl+Enter and Alt+S, which send the mail being written; Teams holds back Ctrl+Enter as well as Enter (it sends, also with formatting on). The simulated Outlook writes replies and new mails (Ctrl+R, Ctrl+N, its ribbon) and sends on those keys; the simulated Teams sends on Ctrl+Enter. New scenarios: `incident_outlook_send`, `incident_teams_ctrl_enter` and `incident_user_mid_text` (the user's key while a command is typed).

## v0.19.0

- Input goes only to the app it is meant for. Every input tool takes `expect`, an app or `app.view` as `jharokha_status` names it (`terminal`, `teams.chat`): the screen is identified first and nothing is sent unless that app is in front at confidence 0.6 or more, else `wrong_app` with what to do next. `jharokha_run` expects `terminal` by default (`expect="any"` for a shell no definition knows), so a command can no longer be typed into Edge's address bar and searched for on the web. An unknown app or view is `bad_argument`, listing the known ones.
- App policies, in the definitions' `[app.x.policy]` (reserved since 0.16, now read and checked: an unknown key is an error, so a typo cannot switch a guard off). `newline` is the chord a typed newline is sent as; `irreversible_keys` are refused (`irreversible`, nothing sent) unless the call passes `allow="irreversible"`. Built in: Teams sends newlines as Shift+Enter, so a two-line draft stays a draft, and holds back Enter, which sends; a browser holds back Enter, which navigates, submits or searches the web. They apply to `jharokha_type`, `jharokha_press` and `jharokha_run` whatever the call passes, and to a paste's final newline; Outlook and the terminal have none.
- One agent per session: the first jharokha process to send input to a window holds its lease while it keeps sending; another is refused with `input_leased` (naming the holder's pid) until the holder has sent nothing for 60 s or has exited. The lease is a small file under `%TEMP%\jharokha` with a pid and a time, named by a hash of the window handle, written only with `--allow-input`.
- Nobody else at the keyboard: input is refused with `user_active` when this computer's keyboard or mouse was used in the last 3 s, and text is typed in chunks of 8 characters with the check again after a 0.15 s pause between them, so keys the user presses in the middle of a long text stop it; the error says how many characters went and the last ones sent. A paste is checked again before its final Enter. jharokha's own input is told apart from the user's by noting the tick after every `SendInput`. In a shell, `jharokha --allow-input ...` waits up to 10 s for the keyboard and mouse to go idle first, since the user's own Enter started it.
- `jharokha_status` reports `warnings`: this computer's taskbar over the session's rows (where clicks on the session's own taskbar were refused), another window over the client, a part off every monitor; and, with input allowed, `input`: another process's `lease` (pid, seconds since its last input) and `user_idle_s`. A `covered` click now says what covers the point.
- `--trace PATH` (server and shell commands): one JSON line per input action, sent or refused, with its time, window, point, keys, the app identified in front and `result` (`sent` or the error code). Typed text is recorded as its length and SHA-256, the text itself only with `--trace-text`. Off by default.
- The shell's input commands take `--expect` and `--allow irreversible` and are guarded as the tools are.
- The checks are pure functions in `core/guard.py` (`check_front`, `Policy`, `text_steps`, `check_keys`, `check_lease`, `check_user_idle`, `refusal`), which the server, the shell commands and the simulated session call, and the actions engine of 0.20 can call before each step.
- Incident replays flipped: on the simulated session `jharokha_run` with Edge in front is refused with `wrong_app` (no web search; with `expect="any"` it is still refused, as `irreversible`), and two lines typed into Teams stay in the draft (no message sent; Enter is refused until `allow="irreversible"`). New scenarios: a second agent holding the lease (`input_leased`) and the user typing on this computer (`user_active`, also mid-text). `browser_goto` now says `allow="irreversible"` for the Enter that navigates.

## v0.18.0

- Regions read as structured items. Each region's `reader` is applied when it is read: `list` regions name their `kind` (`messages`, `entries`, `mails`; core/listread.py), `table` gives a header and rows from aligned cells, `form` label and value pairs, `text` blocks of text with any table among them as a table, `grid` one item a row. Built in: Teams' chat list (entries) and thread (messages), Outlook's mail list (mails), its reading pane (text) and calendar week (table), the browser's page (text, its tables as tables), the terminal's body (grid). A region's items leave out the regions nested inside it (Teams' compose box inside the thread's pane). Calibration can set a list reader as `readers = { thread = "list:messages" }`.
- `jharokha_read_text` takes `format`: `lines` (as before) or `items` (JSON inside the untrusted wrapper, paged like lines; empty fields and line refs left out unless `detailed`). Items are the default for list, table and form regions, so `region="teams.thread"` now returns `{"sender","time","text","own","quote","box"}` per message; `format="lines"` gives the old reading. With no region, `format="items"` reads every named region of the view on screen. `since` on a list region keeps the items at or after a time or date as the screen prints it (`21/03 11:40`, `Yesterday`, `Mon`, `21 March`, `09:15`); `bad_argument` otherwise. `jharokha_layout` names each region's reader (`list:messages`, `table`, ...).
- Messages: a centred day divider (`Yesterday`, `Monday`, `21 March`) dates the messages below it (`Yesterday 18:05`); a sender's next message under the same header, a blank taller than a line below, is an item of its own; month-name dates (`21 March`, `Mar 21, 2026`) are read in headers, dividers and `since`; a header named `You` is your own message; a clock with digits read as letters (`10:5g`, `g:ll`) still starts a message. Lists split items by the step from one row's top to the next, not by the blank between rows, which moved with every ascender and descender (Outlook's list at 16 px read every row as an item). Tables: a first column a fixed pitch apart (a calendar's hours, a queue's IDs) cuts the rows, keeps rows whose first cell OCR missed, and puts a cell drawn a few pixels lower in its row; icon glyphs (`_l_`, a tall `4`) are not columns.
- Terminal grids are read row by row with the second engine (core/rows.py): the character grid is measured from the pixels (row pitch, character width, origin) or taken from apps.toml calibration, kept per app, view, region and window size, and each inked row is read as a strip; only rows whose pixels changed are read again, and a scrolled row keeps its text. Where the second engine cannot read (a source without it), Windows OCR's words are snapped to the grid as before. `Source.reread` takes `margin` (0 for row strips). Terminal text is read far more exactly; the old path is still scored as `<config>+ocrgrid`. A full screen of rows is slow the first time, then only the rows that change are read.
- Evals: a readers axis. Generated Teams, Outlook mail and calendar and browser screens, and the simulated session's Teams, Outlook mail and calendar, request queue and status page, know their items; each region is placed on the identified view, OCR'd as a region read is, and read by its reader. Baselines regenerated.
- On the simulated session `jharokha_run` with an end marker no longer times out: the prompt now reads whole.

## v0.17.0

- A simulated session to test against without a desktop (`evals/sim/`). `SimSource` implements the `Source` protocol over a simulated desktop: an app in front, the session's taskbar with an icon per app, a lock screen. Frames are drawn with GDI and ClearType like the eval screens (optionally through the codec pass) at a fixed size, and only when something changed; reading is real, with Windows OCR and the second engine. Clicks, keys and the wheel change the simulation: a click on a taskbar icon brings that app forward, anything else goes to the app in front. Its taskbar icons are session spots (`session.taskbar.<app>`) in the catalog it hands the server, so `jharokha_layout` lists them. Every name, message, file and page is generated from a seed.
- Simulated apps, drawn to match the built-in definitions and the eval screens' chrome (each identified as its app and view at confidence 1.0): PowerShell in Windows Terminal (typed text on the prompt line; Enter runs a small fixed table of read-only commands: `Get-Date` on a fixed clock, `Get-ChildItem` and `Get-Content` of a fake folder, `Write-Output`, `Start-Sleep`, `cmd /c exit N`, `select -first/-last`, string literals joined with `+`, `$?` and `$LASTEXITCODE`, so an end marker like `; $x=$?; "KX"+"7R4C:${x}:$LASTEXITCODE"` prints `KX7R4C:True:`; Windows PowerShell's error for anything else; Ctrl+L, Ctrl+C, scrollback; a log of every command), Teams chat (chat list, threads with older messages that the wheel scrolls back to a message every 60 ms, drafts per chat; Enter in the compose box sends unless format mode is on, Shift+Enter adds a line; a log of sent messages), Outlook (mail: folders, list, reading pane; calendar: the switch takes a second, the ribbon at once, the calendars pane at 0.3 s and the grid with its ready text "Today" at 1.0 s), Edge (address bar: Enter navigates what looks like a URL and records a web search for anything else; favourites; a page per known URL, all example.net).
- Scenario tests through the real MCP server with `--allow-input` (`tests/test_sim.py`, 5 tests with real OCR, about 9 s): switching apps by taskbar spot, reading and scrolling back a Teams thread, a typed terminal command and its output, `jharokha_run`, an Outlook view switch that settles before the view is ready (`ready=false`, then true after waiting for "Today"). Two incident replays are asserted as known unsafe today, to be flipped to refusals by 0.19 and 0.20: `jharokha_run` with Edge in front types the command into the address bar and searches the web for it (and times out), and typing two lines into Teams sends the first.
- `python -m evals.scenarios`: scripted scenarios with, per scenario, success, tool calls, wall time, tokens returned and the safety events recorded (messages sent, web searches, commands run). Not gated in CI. Known miss: `jharokha_run` of a command with an end marker times out, because Windows OCR reads a Cascadia Mono prompt as `PS C:` after the command's output, so the prompt never comes back; typed with `until` on the marker it works.
- `SimSource` returns the cached frame when nothing changed.
- `tests/harness/fake_session.py sim [seed] [app]` hosts the simulated session in a local window that redraws as clicks, keys and the wheel reach it, so jharokha's real Windows input path can be smoke-tested against it instead of a session.

## v0.16.1

- The second engine runs on 2 onnxruntime threads that do not spin: a line costs less CPU at about the same wall time, and the threads no longer burn CPU in the second after every read.

## v0.16.0

- Apps are data. An app definition (TOML, `format = 1`) names an app, the cues it is recognised by (text in any of several spellings, a regex, the screen's background colour, a pane about where the app draws one, each weighted and optionally placed), and its views; a view adds its own cues, a `ready` text that shows it has finished loading, and its regions, each with a reader (`text`, `grid`; `list`, `table` and `form` are reserved and read as text for now) and one `focus`. A region is the pane found from the pixels under a point (`at`, widened with `to` to the pane under a second point), so it follows the window size and the app's zoom; a `box` of fractions is the fallback for parts with no edges of their own and the check on the pane (a pane overlapping it by less than 0.6 IoU is not taken). `policy` and `action` tables are reserved for later versions.
- Built in, generic UI text only: `terminal` (Windows Terminal: tabs, body as a grid), `teams` (chat: nav, list, header, thread, compose), `outlook` (mail: ribbon, folders, list, reading; calendar: ribbon, calendars, a `week` table), `browser` (Edge or Chrome: tabs, address, favourites, page; the address bar's URL is a cue and is reported), and `generic` (no cues: panes only), plus the session's own taskbar as `session.taskbar`. Positions are fractions of the client area.
- Identification, scored: every app and view gets the weighted share of its cues present, text counting fully where it belongs and 0.3 elsewhere; the best is the answer with a confidence (its score, halved when the runner-up ties, full once the runner-up is 0.5 behind) and the runner-up. Below 0.5 the screen is `generic`. About 4 ms a frame beside 10 ms for panes and 120 ms of OCR.
- `jharokha_status` returns `app`, `view`, `confidence`, `runner_up`, `ready` (false while the view is still loading) and `facts` (a browser's `url`, inside the untrusted wrapper); full reads say `app=… view=… confidence=…`; `jharokha_layout` lists the app and view, the panes, and the view's regions with their box, reader, how each was placed (calibrated, pane or box) and the focus, then session regions and spots. Named regions (`teams.thread`) resolve through the app model and fail with `layout_mismatch` when their app or view is not on screen; the focus region after input, the terminal grid and `jharokha_run`'s prompt come from the view on screen.
- Local calibration in a git-ignored `apps.toml` (`--apps`, `$JHAROKHA_APPS`, else `./apps.toml`): per app and view the measured regions, zoom or size variants chosen by where their anchor sits, grid row pitch, character width and origin (used by the grid reader instead of guessing them from one screen), focus, prompt and readers; session regions and spots (taskbar icons). New apps and overrides of built-in ones go in the same file. `apps.example.toml` replaces `layouts.example.toml`.
- An old `layouts.toml` keeps working unchanged: its tables are read as calibration of the built-in app of the same name (`teams@-3` becomes variant `-3` of teams chat, `terminal` the terminal's shell view), a measured anchor becomes a cue where it was measured, a table with an anchor and another name becomes an app of its own, and one without an anchor a session region under its old name (`vdi.taskbar`). apps.toml wins where both calibrate a view.
- `jharokha measure APP [--view VIEW] --anchor TEXT` writes calibration into apps.toml in the new format, with `--grid` also measuring the grid's pitch and character width; an unknown APP starts an app definition of its own.
- Evals: an identify axis. Every generated screen, now including an Outlook-like calendar (work week, events) and the browser's "Other favourites", is checked for the right app and view (lock screens as none, the IDs screen as generic). Baselines regenerated.

## v0.15.0

- The second OCR engine is part of every install; the `verify` extra is gone. jharokha runs PP-OCRv6's recognition model (shipped in the package, Apache-2.0) on onnxruntime itself, without rapidocr and OpenCV: about 109 MB on disk (onnxruntime, numpy, the 21 MB model) where the extra was about 240 MB. It reads any box, a line Windows OCR found or a full-width row strip, exactly as rapidocr's recogniser did; OpenCV's resize is reproduced bit for bit. The model loads on first use; peak memory is less than half, by running one line at a time. `verify_unavailable` now means only that the model failed to load.
- Detailed reads with `verify` give each verified line `agree`: true when both engines read the same text apart from spacing.
- Panes from pixels: the app's panes are found from its 1 to 2 px divider lines and background colours, in 7 to 12 ms a frame. Reading order goes pane by pane, rows inside, and only a screen with no panes is split by the blank space between text. `jharokha_layout` and `P<n>` regions are these panes. Outlook's list beside its reading pane (a divider line and no gap) now reads apart, and a browser page's table is read row by row where text gaps had cut it into columns.
- Evals: the Teams, Outlook and terminal screens have real pane backgrounds, divider lines and a session taskbar; a new Edge-like browser screen (address bar, favourites, a request queue); the terminal is drawn in Cascadia Mono (vendored for the evals, SIL OFL) at Windows Terminal's row pitch, where Windows OCR drops text (`PS C:\workpp>` reads `PS C:`). A `panes` axis scores the panes found against those drawn. Baselines regenerated.

## v0.14.1

- Lock and sign-in screens read as `locked`: the Windows 11 sign-in screen (a name, a password or PIN box, "Sign-in options", "I forgot my PIN"), the locked-account screen and the clock-only lock screen. A session on the sign-in screen used to report `normal`. A screen counts as one only by these phrases, or by showing little else than a password box or a clock and the date.

## v0.14.0

- Evals (`uv run python -m evals --quick | --full`), replacing `tests/eval_ocr.py`. Generated screens shaped like the work: a PowerShell terminal (git log, directory tables, `sls` hits, log tails, JSON, a wrapped URL), a Teams-like chat, an Outlook-like mailbox, five lock and sign-in screens, lookalike IDs; synthetic names and data from a seeded generator, scored on seeds not used for tuning. Drawn with GDI and ClearType like real apps, then through a codec-like JPEG pass.
- Scored per app: CER and WER (true edit distance), bag-of-words F1, lines exact, lookalike misses, tokens per read, `find` landing inside the drawn word, lock screens read as locked, app picked by its layouts anchor.
- CI: `--quick --check` on every push fails on a drop against `evals/baseline-quick.json`; `--full` on `v0.x.0` tags; results uploaded as an artifact; a manual run regenerates the baselines on the runner.
- First findings, now the baseline: the terminal is the weak spot, and most lock screens are not recognized as locked.
- `fake_session.py` shows the generated screens (`terminal`, `teams`, `outlook`, `ids`, `lock`).

## v0.13.1

- Agents can tell which jharokha they are talking to: `jharokha_status` returns `version`, and the server's instructions start with `jharokha <version>.`; `jharokha --version` in a shell.
- Run from a source checkout, the version is the checkout's `pyproject.toml`. It came from the installed package metadata, which `uv run --no-sync` leaves at the last sync, so a checkout at 0.13.0 reported 0.7.1.

## v0.13.0

- `jharokha_run(command)`, opt-in with the other input tools: runs a command in the shell that has focus (PowerShell, cmd, bash) and returns only what it printed. It types the command (`paste=true` pastes it) and Enter, waits until the shell's prompt is back below the command and the screen holds still, and returns the lines in between, read on the terminal's grid when the layouts file has one. `clear=true` clears the screen first. When the output was taller than the screen, the result says its start is missing. This replaces the type, wait_for_text and screenshot calls a command took, and the `cls` and marker tricks around them.
- `prompt` in an app's layouts table: a regex for its shell prompt at the start of a line; the default knows PowerShell, cmd and bash. `jharokha measure` keeps it.

## v0.12.0

- Terminal grid: `grid = "<region>"` in an app's layouts table marks a region as a character grid. Reads of it (full, region, find, wait, input results, changes) snap OCR words to rows by the line pitch and space them by the character width, so `dist/index . html` reads `dist/index.html`, table columns keep their spacing, and a row that runs to the right edge is joined to its continuation. Zeros read as `e`, `@` or `o` inside a number with a decimal point (`e.46`, `142.e7`) become `0`. No extra OCR time.
- `jharokha measure APP --grid NAME` writes it; remeasuring keeps it, like `focus`.
- `tests/eval_ocr.py` scores the grid on dark screens.

## v0.11.0

- App profiles: a layouts table with an anchor is an app. `jharokha_status` returns `app`, and a full `jharokha_read_text` says `app=teams` in its header.
- `focus = "<region>"` in an app's table: input tools with `show=changed` and no `region` report only the changes inside that region while the app is on screen, plus a count of lines that changed elsewhere. A click in the Teams chat list returns the thread that opened, not the list and the taskbar clock again.
- `jharokha measure APP --focus NAME` writes it; remeasuring keeps the focus already set.
- Measured, not built: inverting dark screens before OCR changes nothing on the synthetic terminal; Windows OCR binarizes on its own, so dark themes need no handling.

## v0.10.2

- After a click, scroll or `focus_at`, the cursor is moved to the middle of the session's right edge before it is given back. The session kept the last position it saw, so a cursor left on a link or a name kept a tooltip or hover card in every later read.
- A session that was not in front gets 0.3 s instead of 0.1 s to activate before the click, which it sometimes swallowed.
- `press` knows `plus`, `minus` and `equals`, and `ctrl++` is Ctrl with the + key; `bad_key 'minus'` cost a call.
- `not_foreground` while typing says how many characters went and the last ones sent, so the rest can be sent without retyping.
- Shell `click` takes `X,Y` as well as `X Y`, like `--focus-at`.

## v0.10.1

- Waits and finds no longer miss text OCR mangled the punctuation of, which timed out waits in a terminal: dashes of any kind and runs of them match `-` and `--` (`----` finds `——`), spaces OCR puts around `.`, `/`, `\` and `:` are ignored (`links.txt` finds `links .txt`, a URL finds `https : //aka . ms/x`), and a caret read as a letter after a prompt (`PS C:\repo>I`) is dropped. A regex that fails on the raw line is tried on the normalized one.
- `jharokha_wait_for_text` takes `regex=true`; input tools take `until_regex=true` (`--until-regex` in the shell). Patterns like `saved|Error` used to be matched as plain text and never found.

## v0.10.0

- `paste` on `jharokha_type` (`type --paste` in the shell): the text goes through the clipboard and Ctrl+V instead of key by key, so a long script or file arrives exactly and at once, and keys pressed on this machine meanwhile cannot mix in. A final newline is sent as Enter after the paste. The local clipboard's text is put back afterwards. Needs clipboard sharing into the session; a terminal may ask to confirm a multi-line paste.
- The `type` description now says text is sent as given: backslash escapes are not decoded.

## v0.9.0

- Input tools (`scroll`, `click`, `type`, `press`) wait until the screen stops changing instead of a fixed 0.4 s: at least 0.4 s, then 0.3 s without a change, at most 3 s. A click in a chat list no longer returns the chat before it.
- `until` on the input tools: after the input, wait (up to `timeout_s`, max 30) for a text, such as the prompt a command ends with. One `type` call now does what took type, `wait_for_text` and a read.
- `show` on the input tools: `changed` (default) returns only the lines added (`+ L4 [x,y] text`) and removed since just before the input, or the whole screen when most of it changed; `full` the whole screen; `none` nothing. `region` (pixels, panel or named region) limits that read.
- Shell input commands settle the same way and take `--until TEXT` and `--timeout S`; `--wait` is gone.

## v0.8.0

- `jharokha measure APP --anchor TEXT [--names a,b,...] [--zoom=-5..2]` writes an app's named regions into the layouts file from what is on screen: full-height columns, and the widest column split into a top bar, body and bottom bars. With `--zoom` it steps the app's zoom (Ctrl+0, Ctrl+=, Ctrl+-), writes one `app@variant` per step and restores the zoom.
- Reading order joins pieces of one line that OCR split apart (it does on monospaced terminal text) when the gap is under 1.5 line heights; wider gaps stay separate table cells.
- Panels: every blank band is tried as a column split, widest first, not only the widest one.
- `type` and `press` send nothing unless the session window is in the foreground, checked before every key: where Windows refused to bring the session forward, keys used to land in whatever local window had focus. They now fail with `not_foreground`. `focus_at` clicks a blank client point first, which activates the session where `SetForegroundWindow` is refused.
- Shell commands for everything the server does: `read --region`, `screenshot`, and with `--allow-input`, `click`, `scroll`, `type` (`--stdin` for text that needs no extra quoting, `--enter`) and `press`. Input commands print the screen text afterwards, like the tools.

## v0.7.1

- Panels: a chat thread with your own messages right-aligned is one panel again, not split into a left and a right column. Two sides count as columns only when a third of the smaller side's lines have a line beside them.

## v0.7.0

- Layout variants: tables named `app@variant` with `anchor_at = [x, y]` are one app measured several ways, such as once per zoom level; a region like `teams.thread` uses the variant whose anchor text is nearest to where it was measured. `jharokha_layout` names the variant in use.

## v0.6.0

- `jharokha_layout`: the panels on screen (`P0`, `P1`, ...: side-by-side columns and the bands around them, found from blank space) with their boxes and first line, plus named regions and whether their app is on screen.
- Every `region` parameter also takes a panel (`"P1"`) or a named region (`"teams.thread"`). Named regions come from a TOML file of fractions of the client area with an optional anchor text (`layout_mismatch` when it is not on screen); `--layouts PATH`, `$JHAROKHA_LAYOUTS` or `./layouts.toml`. `layouts.example.toml` is the template; `layouts.toml` is git-ignored.
- Reading order is built on the same panels: toolbars and label rows are no longer split into columns, and stacked blocks of one width read as one panel.

## v0.5.0

- `verify` on `jharokha_read_text` and `jharokha_find_text`: a second reading of lines by RapidOCR (recognition only, on Windows OCR's line boxes), `flagged` for lines with digits or capitals, `all` for every line, at most 40 per call. Disagreements show as `| verify: …` in concise output and a `verify` field in detailed; `find_text` searches the second reading. Optional extra `jharokha[verify]`; without it the option fails with `verify_unavailable`.
- Detailed output no longer has the always-null `conf` field.
- A click or scroll refused with `covered` now restores the previous foreground window and cursor.
- `tests/eval_ocr.py` scores verify; `fake_session.py` shows the eval screens at true pixel size.

## v0.4.0

- `jharokha_watch` and `jharokha_changes`: a background watcher that logs every text change on the window (lines added and removed, timestamp, changed area), and a query for what changed since a sequence number. Without the watcher, `jharokha_changes` is a one-shot diff against your last read. OCR runs only when pixels changed; the watcher stops itself after 10 minutes without a `jharokha_changes` call.
- Reading order now reads side-by-side panels (a chat list beside its thread, mail list beside the reading pane) one after the other instead of interleaving their lines.
- `tests/eval_ocr.py` renders Segoe UI and Consolas screens shaped like a terminal, Teams and Outlook at the real client size.

## v0.3.0

- `jharokha_click`, `jharokha_type` and `jharokha_press` join `jharokha_scroll` behind `--allow-input`. Each returns the screen text afterwards. Keys go as scan codes, characters via the current keyboard layout with a unicode fallback.
- Unknown key names fail with `bad_key` and the list of accepted names.
- Known limit: the Windows key, Alt+Tab and Ctrl+Alt+Del are handled by the local machine unless the session is full screen.

## v0.2.0

- `jharokha_scroll`, the first input tool: wheel ticks at a client point, returns the screen text afterwards. Registered only when the server runs with `--allow-input`; a plain registration stays read-only. The session window is brought to the front for the wheel event (the RDP client ignores input otherwise) and the previous window and cursor position are restored. Fails with `covered` when another window is over the point.

## v0.1.1

- `jharokha_find_text` with `regex=true` now returns the box of the words the pattern matched, not the whole line, so the center can be used for the matched value.
- `jharokha_status` reports `found: true` with `state: minimized` for a window that exists but is minimized; `found: false` is only for `window_not_found`.

## v0.1.0

First release. Read-only.

- Five MCP tools over stdio: `jharokha_status`, `jharokha_read_text` (concise or detailed, paged), `jharokha_find_text` (fuzzy, lookalike-tolerant, or regex), `jharokha_screenshot` (region, downscaled, with scale), `jharokha_wait_for_text` (appear or gone, up to 30 s).
- Reads Windows 365 / Azure Virtual Desktop (Windows App, `msrdc.exe`), mstsc and Windows 365 app windows; `JHAROKHA_PROCESSES` overrides the allowlist.
- Screen text is returned as untrusted data inside `<screen_text>`; control and zero-width characters are stripped.
- Errors carry a code and a next step: `window_not_found`, `minimized`, `locked`, `no_frames`, `ocr_unavailable`, `region_out_of_bounds`, `timeout`.
- `jharokha status | read | find | bench` for the terminal; `skills/jharokha/SKILL.md` for agents.
- Internals: PrintWindow capture, Windows OCR in-process on a raw BGRA buffer, one cached engine, a frame cache that ignores caret blinks, small regions upscaled 2x.
