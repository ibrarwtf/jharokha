# Development

```
uv sync
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest            # unit tests, the input guards, the server on a small synthetic source
```

## Captures

`--capture DIR` (a global flag, for the server and the shell verbs alike) saves each distinct
frame a tool call or action step read as `<ts>-<frame_id>.png`, with a `.json` beside it: the
call, the app and view identified, the OCR lines and the regions placed, and for a terminal
the rows read cell by cell. Off unless given. Frames hold whatever was on the screen: keep DIR
outside the repo.

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag
`vX.Y.Z`, push.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and
`pre-commit install` to run it before each commit.
