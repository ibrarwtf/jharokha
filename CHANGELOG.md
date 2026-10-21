# Changelog

## Unreleased

- `jharokha_scroll`, the first input tool: wheel ticks at a client point, returns the screen text afterwards. Registered only when the server runs with `--allow-input`; a plain registration stays read-only. Fails with `covered` when another window is over the point.

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
