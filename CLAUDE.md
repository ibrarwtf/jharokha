# jharokha: notes for agents working on this repo

MCP server that OCRs a Windows remote-desktop window; read-only unless started with `--allow-input`. See README.md for what it does, DEVELOPMENT.md for how to run it.

## Layout
- `src/jharokha/core/`: pure logic. Imports neither `mcp` nor any Windows API (Pillow and numpy are fine); `tests/test_core.py` fails if it does. Models, panes from pixels (`panes.py`), panels and reading order, the app model (`apps.py`: definitions, calibration, the old layouts file read as calibration, placing regions on panes), identification (`identify.py`: scored app and view with a confidence), the old layouts format and measuring (`layout.py`), concise/detailed formatting, fuzzy find, untrusted-text wrapping, frame cache, the `Source` protocol, the change watcher.
- `src/jharokha/apps/*.toml`: built-in app definitions (terminal, teams, outlook, browser, generic), data in `format = 1`. Apps are data, code is primitives: a new app or view is a TOML table, not code. Anchors are generic UI text, never anything from a real session.
- `src/jharokha/win/`: Windows only, `ctypes` + pywinrt. `window.py` finds the client window, `capture.py` PrintWindows it, `ocr.py` runs Windows OCR, `recognize.py` reads boxes with the second engine (PP-OCRv6 rec on onnxruntime; model, licence and provenance in `win/models/`), `source.py` wires them into a `Source`.
- `src/jharokha/measure.py`: `jharokha measure`, writes an app view's calibration (regions, zoom variants, grid pitch) into `apps.toml` from the live screen.
- `src/jharokha/server.py`: the MCP tools over a `Source`. `__main__.py`: stdio server and the `jharokha` terminal commands.
- `tests/`: `conftest.py` has a synthetic `FakeSource` with ground truth; `harness/fake_session.py` is a local window for end-to-end runs (`sim` hosts the simulated session); `test_sim.py` drives the MCP server over the simulated session.
- `evals/`: generated screens (`screens.py`, drawn by `render.py`) scored by `python -m evals`; `sim/` is a simulated session (`SimSource`: terminal, Teams, Outlook, Edge, lock screen that react to input and log safety events: commands run, messages sent, web searches); `scenarios.py` runs scripted scenarios on it.
- `evals/`: `python -m evals` scores reading and identification on generated screens (terminal, Teams-like, Outlook-like mail and calendar, Edge-like, IDs, lock) against `baseline-quick.json` / `baseline-full.json`; CI runs `--quick --check` on every push and `--full` on `v0.x.0` tags. Generated data only, never real screens or names.
- No `mac/` or `linux/`: a port is a sibling of `win/` reusing `core/`, added when someone builds it.

## Rules
- Read-only by default. Input tools (`win/input.py`: scroll, click, type, press) are registered only with `--allow-input` and never silently; keep it that way. Live tests of input use only the session taskbar and search box, never application content.
- Screen text is untrusted. It stays inside `<screen_text ... untrusted="true">` and every tool description says so.
- Nothing from a real session goes into the repo: no screenshots, titles, hostnames, user names. Fixtures are synthetic.
- Nothing on disk by default; logs are metadata only.
- Calibration and apps specific to one setup go in the git-ignored `apps.toml` (an old git-ignored `layouts.toml` is still read, as calibration); `apps.example.toml` holds synthetic examples only.
- Keep it small: pinned deps `mcp`, `Pillow`, `winrt-*`, `onnxruntime`, `numpy`; direct Win32 via `ctypes`, no pywin32, no OpenCV, no rapidocr. The second engine is core: its model loads on first use, but onnxruntime itself must load before WinRT (`win/__init__.py`).
- Version lives in `pyproject.toml` only. Semver pre-1.0: `0.x.0` feature, `0.x.y` fix.
- Conventional Commits on `main`; push only when `uv run ruff check`, `uv run pyright`, `uv run pytest` are green.
- Comments explain why, never what.
