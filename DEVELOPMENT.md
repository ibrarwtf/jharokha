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

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag `vX.Y.Z`, push. PyPI publishing is added at the first PyPI release.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and `pre-commit install` to run it before each commit.
