# Development

```
uv sync
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest            # includes one test on the real Windows OCR engine
```

## Try it without a remote session

`tests/harness/fake_session.py` opens a local window with known text. The allowlist of client processes is `msrdc.exe`, `mstsc.exe`, `windowsapp.exe`, `windows365.exe`; override it with `JHAROKHA_PROCESSES` (comma-separated) to accept the fake window:

```
uv run python tests/harness/fake_session.py terminal     # also: chat, mail, table, lock
$env:JHAROKHA_PROCESSES = "python.exe,pythonw.exe"
uv run jharokha --window-title "Fake Remote" read
uv run jharokha --window-title "Fake Remote" bench
```

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

### Read accuracy (`uv run python tests/eval_ocr.py`)

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

## Open

- TODO: publish to PyPI with Trusted Publishing (name `jharokha`, fallback `jharokha-mcp`) and add the tag-triggered publish workflow, so install becomes `uvx jharokha`.
- Claude Desktop extension (`.mcpb`, `uv` type): does it install without a system Python? Test before shipping one.
- No shared convention for marking untrusted tool output was found; ours is the `<screen_text>` wrapper.
- Recommend a fixed session resolution in the README for stable coordinates?
- Watcher (later): which window does Windows.Graphics.Capture need, the Windows App top-level window or the session child?
- Input tools, a watcher with a change log and more OCR engines are later minor releases, each opt-in.

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag `vX.Y.Z`, push. PyPI publishing is added at the first PyPI release.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and `pre-commit install` to run it before each commit.
