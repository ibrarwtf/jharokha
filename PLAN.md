# jharokha: plan

A jharokha is the overhanging window of Indian palaces, used to watch the street without being seen.
jharokha is a local MCP server that lets AI agents (Claude Code, Claude Desktop / Cowork, any MCP client) read a remote-desktop window on Windows as text: a Windows 365 / Azure Virtual Desktop session in Windows App, an mstsc RDP window, or similar. It looks; it does not touch.

This file is the build plan for the first release. It moves into the docs (README, CLAUDE.md, DEVELOPMENT.md) as they are written, then goes.

## Goal
The best MCP for agents to access, then control, a Windows remote desktop (VDI) from the host: light on CPU, memory and tokens, and usable from any coding agent (MCP over stdio for Claude Code, Cowork, Cursor, Codex and others; the `jharokha` command for anything else).
Targets for 0.1.0: a read in < 300 ms on a 4-core laptop CPU; a concise read of a full screen in < 1.5k tokens; < 150 MB RAM; 0% CPU when idle (nothing runs between calls).

## Why
- Remote desktops are opaque to agents: no accessibility tree, no API, just pixels. Agents fall back to screenshots, which are slow, cost many tokens and need a computer-use grant.
- Corporate VDIs often block every other channel (clipboard, drive mapping, installs inside the session). The window on the local machine is the one thing you always have.
- OCR on the local machine turns that window into compact text an agent can search and wait on, at a fraction of the cost of a screenshot.

## Scope of the first release (0.1.0): read-only
- In: find the remote-desktop window, read its text, find text, crop a screenshot, wait for text to appear or go.
- Out: any input (keys, typing, clicks), background watching, alerts, anything running inside the remote session, network calls, telemetry.
- Later releases, each its own minor version: input tools (a separate opt-in server or flag), a watcher with a change log, more OCR engines.

## Constraints we design around
- Windows only (Windows 10 2004+ / 11). Fail fast with a clear message elsewhere.
- The session is pixels: no accessibility tree from outside the client.
- RDP clients stop receiving screen updates while minimized (RDP "Suppress Output"). Covered by other windows is fine; minimized is not. Status must say so.
- A locked machine or a sleeping display likely stops new frames. Detect it; don't paper over it.
- Display scaling (125%, 150%) skews window coordinates unless the process is per-monitor-v2 DPI aware.
- Reference machine for budgets: 4-core laptop CPU, 8 GB RAM, no GPU.

## Measured baseline (PowerShell prototype)
- PrintWindow capture of a ~1200x1200 covered window: 63 ms. Windows OCR on the frame: 441 ms, incl. PNG round trip and PowerShell overhead. Good on UI text; mixes lookalikes (`4`/`u`, `l`/`1`/`I`, `O`/`0`).
- A local MCP server registered in Claude Desktop is usable from Cowork and runs on the host (not in Cowork's VM), so it can see host windows.

## What we take from similar tools
- agent-rdp: one result envelope with actionable error codes; OCR "locate" returning box and center; a SKILL.md shipped next to the tool ("read, act, read again"; never estimate coordinates from a screenshot). It opens its own RDP connection with a password, so it can't reach Windows 365 / AVD; jharokha attaches to the client window you already have.
- Windows-MCP: screenshot by `region`, a downscale setting, a wait-for tool, DPI info in status. Avoid its 21-tool scope.
- playwright-mcp: text first, images opt-in; a cheap `find` instead of a full dump; `wait_for(text | gone, timeout <= 30 s)`.
- Anthropic computer use: coordinates live in the pixel space of the image returned, so return the scale; a region crop is the zoom; keep images <= 1568 px on the long edge.
- screenpipe: Windows OCR on a cached engine fed a raw BGRA buffer from one worker; a change gate (4x-downscaled hash, histogram diff < 0.02) and an OCR cache keyed by image hash; compact "outline" output (~90% fewer tokens than raw JSON).
- Anthropic, "Writing effective tools for agents": a shared tool prefix; descriptions written for a new hire; `response_format` concise vs detailed; pagination and truncation that tell the model how to narrow; errors that say what to do next; evaluate with realistic multi-step tasks.

## Design decisions
- Python >= 3.11, uv, `mcp>=2.3,<3` (SDK 2.x: `from mcp.server import MCPServer`), stdio only, no HTTP port.
- Capture: PrintWindow (PW_RENDERFULLCONTENT) per call. Reads happen only when an agent asks, so a capture stream buys nothing yet. Windows.Graphics.Capture (via `windows-capture`) comes with the watcher, when frames are polled.
- OCR: Windows.Media.Ocr in-process via pywinrt, one cached engine, raw BGRA buffer, no PNG round trip, one worker thread with its own event loop. Optional upscale (1.5x) for small text. No other engine in 0.1.0.
- Windows OCR gives no confidence: return `conf: null` and flag lookalike-prone tokens (digits next to letters) instead of inventing a score.
- Cache: the last frame's hash and OCR result, so repeated reads of an unchanged screen are free.
- Coordinates: pixels of the client area (window frame cropped off), origin top-left; every result carries `frame_id`, `ts`, `scale`.
- Window selection: an allowlist of client processes (`msrdc.exe`, `mstsc.exe`, Windows App, configurable), optional `window_title` filter. Never the whole desktop.
- Screen text is untrusted data: returned inside `<screen_text source="ocr" untrusted="true">...</screen_text>`, control and zero-width characters stripped, and every tool description says it must never be followed as instructions.
- Nothing on disk by default: frames and text stay in memory. Logs go to stderr, metadata only (timings, window title hash); content only with an explicit debug flag.
- Errors are `isError` results with a next step ("No remote-desktop window found. Candidates: ... Pass window_title=...").

## Tools (0.1.0)
All annotated `read_only_hint=True, open_world_hint=False`.

| Tool | Args | Returns |
|---|---|---|
| `jharokha_status` | `window_title?` | `found, title, process, state (normal / minimized / locked / no_frames), client_size, dpi_scale, foreground` |
| `jharokha_read_text` | `region?, response_format = concise / detailed, offset = 0, limit = 200` | concise: reading-order text, one line each as `L12 [x,y] text`; detailed: lines with boxes, word boxes, lookalike flags; `truncated` + how to narrow |
| `jharokha_find_text` | `query, regex = false, region?, all = false` | matches `{ref, text, box, center}`; fuzzy by default (OCR misreads) |
| `jharokha_screenshot` | `region?, max_long_edge = 1568` | PNG image + `{frame_id, region, scale}` |
| `jharokha_wait_for_text` | `query, gone = false, region?, timeout_s = 10 (max 30)` | `{found, elapsed_ms, match?}`; on timeout `isError` with the last text seen |

Error codes: `not_windows`, `window_not_found`, `minimized`, `locked`, `no_frames`, `ocr_unavailable`, `region_out_of_bounds`.
`locked` = a Windows lock screen shown inside the session, detected from the OCR text.

## Layout
```
src/jharokha/
  core/        pure logic, no Windows APIs, no MCP: models, layout (lines -> blocks -> reading order),
               concise / detailed formatting, lookalike flags, fuzzy find, untrusted-text wrapping, frame cache
  win/         Windows only: window.py (DPI awareness, find the client window, client rect, state),
               capture.py (PrintWindow -> BGRA frame), ocr.py (Windows OCR engine, buffer, crop, upscale)
  server.py    MCP tools: wires core + win, envelope, errors
  __main__.py  `jharokha` (stdio server) and `jharokha read|find|status` for the terminal
skills/jharokha/SKILL.md   how an agent should use the tools
tests/                     pytest; fixtures/ = synthetic screens rendered with PIL, never real ones
```
- `core/` imports neither `mcp` nor Windows APIs; a test fails if it does (boundary as a build error, as in claude-limits). Tests inject a fixture screen source in place of `win/`.
- No empty `mac/`: a port would be a sibling of `win/` reusing `core/`; CLAUDE.md says so.
- No `scripts/`: install is `uvx jharokha`, uninstall is removing the client registration, the "see it working" check is `jharokha read`, and checks are `uv run ruff check`, `uv run pyright`, `uv run pytest`.
- Small, pinned dependencies only: `mcp`, `winrt-*` (OCR, imaging, streams), `Pillow`. Direct Win32 via `ctypes`, no pywin32.

## Repo conventions (from claude-limits)
- Docs start minimal and grow with the code: `README.md`, `CLAUDE.md`, `DEVELOPMENT.md`, `CONTRIBUTING.md`, `CHANGELOG.md`, `LICENSE` (MIT). A few lines each at first; nothing written ahead of the code it describes.
- Version: one place, `pyproject.toml`; the code reads it with `importlib.metadata.version("jharokha")`. No VERSION file.
- Semver pre-1.0: `0.x.0` a feature, `0.x.y` a fix or polish.
- CHANGELOG: one `## vX.Y.Z` section per release, newest first, written as user-facing notes; internals in their own bullet.
- Release by hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — one-line summary`, tag `vX.Y.Z`, push. A GitHub Actions workflow on the tag (PyPI Trusted Publishing + GitHub release from the CHANGELOG section) is added at the first PyPI release, not before. No release script.
- Comments explain why, never what. Match existing naming. Boilerplate over indirection.
- Public from the first push: no personal data, no client names, hostnames, user names or real screenshots. `gitleaks` as a pre-commit hook and in CI; `.gitignore` blocks `*.png` outside `tests/fixtures/`.

## Distribution
- 0.1.0 installs from GitHub: `uvx --from git+https://github.com/ibrarwtf/jharokha jharokha`. PyPI (`jharokha`, free on 2026-10-21; fallback `jharokha-mcp`) comes later, then `uvx jharokha`; classifier `Operating System :: Microsoft :: Windows`.
- Install snippets: Claude Code `claude mcp add --transport stdio --scope user jharokha -- uvx --from git+https://github.com/ibrarwtf/jharokha jharokha`; Claude Desktop `{"mcpServers": {"jharokha": {"command": "uvx", "args": ["--from", "git+https://github.com/ibrarwtf/jharokha", "jharokha"]}}}`.
- Later: MCP Registry (`server.json`, `io.github.ibrarwtf/jharokha`, `<!-- mcp-name: ... -->` in the README) and a Claude Desktop extension (`.mcpb`, `platforms: ["win32"]`). Both after 0.1.0 works from PyPI.

## Testing
- Unit: `layout` and formatting on synthetic OCR results; `ocr` on PIL-rendered fixture screens (terminal, chat list, mail list, portal table) with expected text; marked `windows` where the real OCR engine runs.
- Server: in-process `mcp.Client(server)` calls for every tool, incl. error paths and truncation.
- CI: GitHub Actions `windows-latest`, Python 3.11-3.13: ruff, pyright, pytest, gitleaks. Trusted Publishing to PyPI on tag.
- Manual: MCP Inspector (`npx @modelcontextprotocol/inspector uvx jharokha`), then Claude Code and Cowork against a live session.
- Evals: ~20 realistic tasks against fixture screens ("what is the latest message from X", "wait until the build says succeeded"); track accuracy, calls, tokens.

## Feedback loop for an agent building this alone
- Fake remote window: `tests/harness/fake_session.py` opens a plain local window (tkinter) titled like a remote session and draws synthetic screens (terminal, chat list, mail list, table, lock screen) from text we control. The real capture + OCR path runs against it end to end, with known ground truth, no remote session and no real data. A test-only `window_title` / process override lets it pass the allowlist.
- Accuracy is a number, not an impression: `tests/eval_ocr.py` renders fixtures at several font sizes and scales, runs OCR, and prints character error rate and lookalike errors per setting. Tuning (upscale, greyscale, crop) is judged on that table.
- Budgets are measured, not guessed: `jharokha bench` prints ms per capture / OCR / format, tokens of the concise output (chars / 4), and peak RSS, against the fake window.
- Every step ends green on `uv run ruff check`, `uv run pyright`, `uv run pytest` before it is committed.
- MCP end to end without a person: in-process `mcp.Client` tests, then `npx @modelcontextprotocol/inspector --cli` calls against `uv run jharokha`, then a headless `claude -p` that calls each tool through a project-scoped registration.
- The real remote session is used only for the spikes and one final check. It must be connected, unlocked and not minimized; if it isn't, stop and ask, don't try to sign in. Nothing read from it goes into the repo, commits or docs.
- Needs a person: Cowork (restart Claude Desktop, ask in a Cowork session) and anything that changes the machine (registry key, installs, Claude Desktop config).

## Acceptance per step
- 1 skeleton: `uv run pytest` passes with the boundary test; CI green on GitHub; gitleaks clean.
- 2 read: `jharokha read` against the fake window returns its text in reading order; CER on fixtures recorded; `jharokha bench` within the targets or the gap written here.
- 3 server: all five tools covered by in-process tests incl. errors and truncation; Inspector CLI lists and calls them; `claude -p` calls `jharokha_read_text` successfully.
- 4 skill + evals: SKILL.md written; evals run with numbers recorded; one check against the real session passes.
- 5 release: CHANGELOG `v0.1.0`, tag, GitHub release, after a last gitleaks + personal-data review.

## Build order
0. Spikes on a real session, numbers written back here: S1 in-process OCR speed (target < 200 ms per full frame); S2 accuracy at 1.0 / 1.5 / 2.0x upscale, colour vs grey, counting lookalike errors on ~20 crops; S3 minimized and locked behaviour, so status reports it right. Real crops stay on the machine, never in the repo.
1. Repo skeleton: pyproject, `src/jharokha/core` + `win`, minimal docs, CI (ruff, pyright, pytest, gitleaks on `windows-latest`), MIT licence. The repo `ibrarwtf/jharokha` is public from the start; push after each green step.
2. `win` + `core` behind the terminal command: `jharokha read` works.
3. `server` with the five tools; tests; Inspector.
4. `SKILL.md`; try it from Claude Code and Cowork; evals.
5. Release 0.1.0: CHANGELOG, tag, GitHub release. PyPI later; until then install from git: `uvx --from git+https://github.com/ibrarwtf/jharokha jharokha`.

## Open
- Can the Claude Desktop extension (`uv` type) install without a system Python? Test before shipping an `.mcpb`.
- Is there a shared convention for marking untrusted tool output? None found; ours is the `<screen_text>` wrapper.
- Fixed session resolution (e.g. 1280x800) recommended in the README for stable coordinates?
- Which window does WGC need later: the Windows App top-level window or the session child window?
