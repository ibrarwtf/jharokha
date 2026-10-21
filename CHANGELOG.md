# Changelog

## v0.6.0

- `jharokha_layout`: the panels on screen (`P0`, `P1`, ...: side-by-side columns and the bands around them, found from blank space) with their boxes and first line, plus named regions and whether their app is on screen.
- Every `region` parameter also takes a panel (`"P1"`) or a named region (`"teams.thread"`). Named regions come from a TOML file of fractions of the client area with an optional anchor text (`layout_mismatch` when it is not on screen); `--layouts PATH`, `$JHAROKHA_LAYOUTS` or `./layouts.toml`. `layouts.example.toml` is the template; `layouts.toml` is git-ignored.
- Reading order is built on the same panels: toolbars and label rows are no longer split into columns, and stacked blocks of one width read as one panel. On the live session the body went from 9 slices to one panel; synthetic eval scores are unchanged.

## v0.5.0

- `verify` on `jharokha_read_text` and `jharokha_find_text`: a second reading of lines by RapidOCR (recognition only, on Windows OCR's line boxes), `flagged` for lines with digits or capitals, `all` for every line, at most 40 per call. Disagreements show as `| verify: …` in concise output and a `verify` field in detailed; `find_text` searches the second reading. Optional extra `jharokha[verify]`; without it the option fails with `verify_unavailable`. On the synthetic lookalike screen, lines read exactly go from 0 of 4 to 3 of 4.
- Detailed output no longer has the always-null `conf` field.
- A click or scroll refused with `covered` now restores the previous foreground window and cursor.
- `tests/eval_ocr.py` scores verify; `fake_session.py` shows the eval screens at true pixel size.

## v0.4.0

- `jharokha_watch` and `jharokha_changes`: a background watcher that logs every text change on the window (lines added and removed, timestamp, changed area), and a query for what changed since a sequence number. Without the watcher, `jharokha_changes` is a one-shot diff against your last read. OCR runs only when pixels changed; the watcher stops itself after 10 minutes without a `jharokha_changes` call.
- Reading order now reads side-by-side panels (a chat list beside its thread, mail list beside the reading pane) one after the other instead of interleaving their lines. On the synthetic Teams-like and Outlook-like screens CER fell from .56 and .62 to .04 and .001.
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
- Internals: PrintWindow capture, Windows OCR in-process on a raw BGRA buffer, one cached engine, a frame cache that ignores caret blinks, small regions upscaled 2x. Measured: OCR ~200 ms and ~1.1k tokens for a 1166x1181 session screen, 80 MB peak.
