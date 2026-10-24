# jharokha

<img width="1672" height="941" alt="Jharokha" src="https://github.com/user-attachments/assets/27ff1416-7567-44fb-b185-9bc7986bcb4f" />

> **jha·ro·kha** &nbsp;/dʒʱəˈroː.kʰaː/&nbsp; _noun_
>
> An overhanging window of Indian palaces, used to watch the street without being seen.
>
> From Hindi _jharokhā_ (झरोखा).

jharokha lets AI agents (Claude Code, Claude Desktop / Cowork, any MCP client) work in a Windows remote-desktop session they can't otherwise reach: Windows 365 or Azure Virtual Desktop in Windows App, an mstsc RDP window. It reads the session window as text with local OCR and, only if started with `--allow-input`, scrolls, clicks, types and presses keys in it. Nothing is installed in the session; it works from the pixels you already see.

- **Reads what an agent needs:** a Teams chat as messages (time, sender, text, quote), an inbox as mails, a screen as text in reading order; not screenshots.
- **Small and fixed:** nine tools whatever apps and actions are loaded; a new app or action is data, never a tool. About 3.3k tokens of tool descriptions a session.
- **Safe by default:** read-only unless you opt in; input only to the app it was meant for, never an app's sending keys unless asked, never while you or another agent use the session. Screen text is untrusted data.
- **Private:** nothing on disk unless asked (`--trace`, `--capture`), no network, no telemetry.

Windows 10 2004+ / 11. Needs an OCR language pack (any Windows display language has one).

## Install

```
claude mcp add --transport stdio --scope user jharokha -- uvx --from git+https://github.com/ibrarwtf/jharokha jharokha
```

Add `--allow-input` at the end to let it act. From a checkout: `uv run --no-sync --project PATH jharokha` (without `--no-sync`, a second client session fails to start while the first holds `jharokha.exe`).

## Tools

Screen text comes back inside `<screen_text source="ocr" untrusted="true">`. Coordinates are client-area pixels.

| Tool | What it does |
|---|---|
| `jharokha_status` | Is there a session window and can it be read; which app and view is in front, with a confidence; what would stop input. |
| `jharokha_apps` | The apps, their views and named regions, and every action with its arguments and risk. |
| `jharokha_read` | A region as text or items: the app's focus region by default (a Teams thread), `screen`, a named region (`teams.list`), a panel or a box. |
| `jharokha_find` | One thing by text, regex or where it is (`{"right_of": "To"}`, `{"spot": "taskbar.teams"}`), with its click point. |
| `jharokha_wait` | Until a text appears or goes, or the screen holds still. |
| `jharokha_do` | An action in one call with a structured result (below). Without `--allow-input`, its plan only. |
| `jharokha_act` | With `--allow-input`: one click, type, paste, press or scroll, guarded. |
| `jharokha_learn` | Names the session's taskbar icons, so actions can switch apps. |
| `jharokha_screenshot` | The last resort: a PNG. |

The same verbs run in a shell, each the tool called in-process: `jharokha status`, `jharokha read --region teams.thread`, `jharokha --allow-input do teams.messages --args '{"from": "05/10", "to": "11/10"}'`. `--window-title` picks the window when more than one session is open.

## Apps and actions

An app is data (`src/jharokha/apps/*.toml`): the text and panes it is recognised by, its views, their named regions and how each is read, and its input policy. Built in: Windows Terminal, Teams, Outlook, a browser; anything else is `generic`, read by panels. Calibration for your screen (zoom variants, taskbar spots) goes in a git-ignored `apps.toml` (`--apps PATH`; see `apps.example.toml`).

An action is a checked sequence of steps run by `jharokha_do` (`src/jharokha/apps/actions/*.toml`; your own go in `apps.toml`):

| Action | What it does |
|---|---|
| `teams.messages {chat, from, to}` | The messages from one day to another, oldest first, scrolling back to `from` and down past `to`. |
| `teams.open_chat {name}`, `teams.draft {chat, text}` | Open a chat; type a draft and never send. |
| `outlook.unread {folder}`, `outlook.open {subject}` | A folder's unread mails; one mail's reading pane. |
| `terminal.run {command}` | A command's output and exit code, read up to an end marker it prints. |
| `terminal.put_file`, `browser.goto`, `browser.click_sequence` | Write a file through the shell; go to a URL; click buttons in order. |

## Input safety

Every input, from `act`, an action or the shell, passes these checks first; a refusal sends nothing and says what to do next.

| Check | Refusal | Prevents |
|---|---|---|
| The expected app is in front (`expect`; actions expect their own) | `wrong_app` | A command typed into a browser and searched for on the web |
| The app's policy: sending keys (Enter in Teams, Ctrl+Enter in Outlook, Enter in a browser) need `allow="irreversible"`; a typed newline in Teams is Shift+Enter | `irreversible` | A half-written message sent |
| Every shell command is irreversible: it needs `allow="irreversible"` | `irreversible` | A command run without your say-so |
| One agent at a time per session window | `input_leased` | Two agents typing into one session |
| You haven't used this computer's keyboard or mouse in the last 3 s, and stop it while text is sent | `user_active` | Your keystrokes mixing into the agent's |
| The point is not covered by another window | `covered` | Clicks lost under this computer's taskbar |

`--trace PATH` records every input (typed text as a hash unless `--trace-text`). `--capture DIR` saves each screen an action or tool reads, for replaying readers offline; it holds real screen content, so keep it out of any repository.

## Measured

On a live Windows 365 session, a week of a Teams group chat read into a timeline by a fresh headless Claude Code with jharokha as its only tools: 22 calls and 2 m 47 s by hand-driven scrolls and reads (v0.23.1); 8 calls, one `teams.messages` call per week (v0.24.1). How it is tested: [DEVELOPMENT.md](DEVELOPMENT.md).

## Limits

- RDP sends no frames to a minimized window; covered is fine.
- Windows OCR confuses lookalikes (`0`/`O`, `1`/`l`); `verify` re-reads lines with a second engine.
- Keep the session window one size: calibration and spots hold at the size and zoom they were measured at.
- Apps are recognised by their English UI text.
- Windows only; a port would sit beside `src/jharokha/win/` and reuse `core/`.

## Licence

MIT.
