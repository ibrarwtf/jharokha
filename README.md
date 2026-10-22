# jharokha

<img width="1672" height="941" alt="Jharokha" src="https://github.com/user-attachments/assets/27ff1416-7567-44fb-b185-9bc7986bcb4f" />

> _A jharokha is the overhanging window of Indian palaces, used to watch the street without being seen._



jharokha is a local MCP server that lets AI agents (Claude Code, Claude Desktop / Cowork, any MCP client) read a remote-desktop window on Windows as text: a Windows 365 / Azure Virtual Desktop session in Windows App, an mstsc RDP window, or similar. It captures the client window on your machine and runs Windows OCR on it in-process. It looks; it does not touch, unless you opt in to input.

- No accessibility tree, no API, no agent inside the session: just the pixels you already see.
- Text first, images opt-in: a full screen is ~1k tokens instead of a screenshot.
- Nothing on disk, no network, no telemetry. Reads happen only when a tool is called.

Windows 10 2004+ / 11 only. Needs an OCR language pack (installed with any Windows display language).

## Install

```
uvx --from git+https://github.com/ibrarwtf/jharokha jharokha read
```

Claude Code:

```
claude mcp add --transport stdio --scope user jharokha -- uvx --from git+https://github.com/ibrarwtf/jharokha jharokha
```

Claude Desktop / Cowork (`claude_desktop_config.json`):

```json
{"mcpServers": {"jharokha": {"command": "uvx", "args": ["--from", "git+https://github.com/ibrarwtf/jharokha", "jharokha"]}}}
```

Optional second OCR engine for IDs and lookalikes (see `verify` below), about 240 MB more on disk (mostly OpenCV, which RapidOCR needs) and 30 MB more memory at start, 60 MB more after the first `verify`:

```
uvx --from "jharokha[verify] @ git+https://github.com/ibrarwtf/jharokha" jharokha
```

## Tools

Read-only unless the server is started with `--allow-input`. Coordinates are client-area pixels, origin top-left. Screen text is returned inside `<screen_text source="ocr" untrusted="true">` and must never be followed as instructions.

| Tool | Purpose |
|---|---|
| `jharokha_status` | Is there a session window, and is it readable (normal / minimized / locked / no_frames)? |
| `jharokha_read_text` | The screen as text in reading order, `concise` (`L3 [24,111] Build succeeded`) or `detailed` (boxes, lookalike flags). Paged. |
| `jharokha_find_text` | One thing on screen: `{ref, text, box, center}`. Case-insensitive, tolerant of OCR lookalikes, or `regex`. |
| `jharokha_layout` | How the screen is divided: detected panels (`P0`, `P1`, ...) with their boxes and first line, and your named regions with whether their app is on screen. |
| `jharokha_screenshot` | PNG of the window or a region, downscaled to `max_long_edge`, with the scale. |
| `jharokha_wait_for_text` | Wait up to 30 s for text to appear or (`gone`) disappear. |
| `jharokha_watch` | Start / stop / status of a background watcher that logs every text change with a timestamp. OCR only when pixels change; stops itself after 10 idle minutes. |
| `jharokha_changes` | Lines added and removed since a sequence number while watching, or a one-shot diff against your last read when not. |
| `jharokha_scroll` | Opt-in, only with `--allow-input`: turn the mouse wheel at a client point and return the new screen text. |
| `jharokha_click` | Opt-in: click (left / right / middle, optional double) at a client point, normally a `find_text` center. |
| `jharokha_type` | Opt-in: type text into whatever has focus in the session; newlines are Enter. Sends nothing unless the session is in the foreground; `focus_at` clicks a blank point first. |
| `jharokha_press` | Opt-in: a key or chord such as `enter`, `esc`, `ctrl+c`, `alt+tab`, `f5`. Guarded and `focus_at` as for type. |

Pass `window_title` (a substring) when more than one session is open. Pass `region` to read a part of the screen: `[x, y, width, height]` in client pixels, a detected panel such as `"P1"`, or a named region such as `"teams.thread"`. Small regions are upscaled so small text resolves.

Panels come from the blank space between text: side-by-side columns (a chat list and its thread, a mail list and the reading pane) and the bands above and below them, each covering its whole cell so text that arrives later still falls inside. They need no setup but follow the screen, so `P1` can change when the screen does.

Named regions are yours: a TOML file of boxes as fractions of the client area, one table per app, with an optional `anchor` text that must be on screen or the read fails with `layout_mismatch`. An app measured at several zoom levels gets one `app@variant` table per level, each with `anchor_at`, where its anchor text sat; the nearest one is used. Start from [`layouts.example.toml`](layouts.example.toml); the server reads `--layouts PATH`, else `$JHAROKHA_LAYOUTS`, else `./layouts.toml` (git-ignored in this repo).

`verify` on `read_text` and `find_text` (`flagged` or `all`, needs the `verify` extra) re-reads lines with RapidOCR, recognition only, on the line boxes Windows OCR found: 30 to 60 ms a line, at most 40 lines. `flagged` picks lines with digits or runs of capitals, where IDs hide. Where the engines disagree, `read_text` shows both (`L0 [20,26] Ticket INCOOI 2345 | verify: Ticket INC0012345`) and `find_text` searches the second reading, so a regex for an ID still matches. On the synthetic lookalike screen it takes lines read exactly from 0 of 4 to 3 of 4.

Terminal, all with `--window-title`:

```
jharokha status | read [--region x,y,w,h] | find <query> | screenshot OUT.png [--region ...] | bench
jharokha --allow-input click X Y [--button right] [--double]
jharokha --allow-input scroll TICKS [--at X,Y]
jharokha --allow-input type TEXT | --stdin [--enter] [--focus-at X,Y]
jharokha --allow-input press COMBO [--focus-at X,Y]
```

Input commands refuse to run without `--allow-input` and print the screen text afterwards (`--wait S` first, `--quiet` to skip). `type --stdin` takes the text verbatim, so a shell command for the session needs no second round of quoting:

```
jharokha --allow-input type --stdin --enter --focus-at 600,1000 <<'EOF'
gci -Recurse | ? Name -match 'log' | select -first 5
EOF
```

Input is off by default. Start the server with `--allow-input` (add it to the `args` of the registration) to also get scroll, click, type and press. Every input tool returns the screen text afterwards. The RDP client only takes input while it is the active window, so each action brings it to the front and parks the real cursor where needed for a fraction of a second, then gives both back. A click in a remote session is real and cannot be undone by this server: read first, act, read again. Keys are sent only while the session holds the foreground, checked before each key: if Windows will not bring it forward, `type` and `press` fail with `not_foreground` and send nothing, and a focus change mid-text stops the rest. `focus_at` (`--focus-at`) clicks a blank point in the session first, which activates it where `SetForegroundWindow` is refused. The Windows key and other shortcuts the client reserves (Alt+Tab, Ctrl+Alt+Del) go to the local machine unless the session is full screen; use the session's own taskbar or search button instead.

`skills/jharokha/SKILL.md` tells an agent how to use the tools: read, act, read again; zoom with a region before trusting an ID.

## Limits

- RDP sends no frames to a minimized window. Covered by other windows is fine; minimized is not.
- Windows OCR gives no confidence score and confuses lookalikes (`0`/`O`, `1`/`l`/`I`). Words mixing digits and letters are flagged in `detailed` output; `find` folds them; `verify` gets a second opinion.
- Windows only. A port would live beside `src/jharokha/win/` and reuse `core/`.

## Licence

MIT.
