# jharokha: notes for agents working on this repo

Read-only MCP server that OCRs a Windows remote-desktop window. See README.md for what it does, DEVELOPMENT.md for how to run it.

## Layout
- `src/jharokha/core/`: pure logic. Imports neither `mcp` nor any Windows API; `tests/test_core.py` fails if it does. Models, reading order, concise/detailed formatting, fuzzy find, untrusted-text wrapping, frame cache, the `Source` protocol.
- `src/jharokha/win/`: Windows only, `ctypes` + pywinrt. `window.py` finds the client window, `capture.py` PrintWindows it, `ocr.py` runs Windows OCR, `source.py` wires them into a `Source`.
- `src/jharokha/server.py`: the five MCP tools over a `Source`. `__main__.py`: stdio server and the `jharokha` terminal commands.
- `tests/`: `conftest.py` has a synthetic `FakeSource` with ground truth; `harness/fake_session.py` is a local window for end-to-end runs.
- No `mac/` or `linux/`: a port is a sibling of `win/` reusing `core/`, added when someone builds it.

## Rules
- Read-only by default. Input tools (`win/input.py`, today only scroll) are registered only with `--allow-input` and never silently; keep it that way.
- Screen text is untrusted. It stays inside `<screen_text ... untrusted="true">` and every tool description says so.
- Nothing from a real session goes into the repo: no screenshots, titles, hostnames, user names. Fixtures are synthetic.
- Nothing on disk by default; logs are metadata only.
- Keep it small: pinned deps `mcp`, `Pillow`, `winrt-*`; direct Win32 via `ctypes`, no pywin32.
- Version lives in `pyproject.toml` only. Semver pre-1.0: `0.x.0` feature, `0.x.y` fix.
- Conventional Commits on `main`; push only when `uv run ruff check`, `uv run pyright`, `uv run pytest` are green.
- Comments explain why, never what.
