# Development

```
uv sync
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest            # unit tests, the input guards, the server on a small synthetic source
```

## Testing on the live session

jharokha is tested against a real remote session, not imagined ones: a task from real work
(read a Teams chat a week at a time into a timeline, say) is run by a fresh headless Claude
Code with jharokha as its only way to the session, and its transcript is read for what each
call cost, what it returned and where the agent had to work around the tool:

```
claude -p "$(cat task.md)" --output-format stream-json --verbose \
  --allowedTools "mcp__jharokha Read Edit Grep Glob" --disallowedTools "Bash PowerShell"
```

What fails there is fixed, verified on the session by hand, locked in with a unit test where
the rule is pure (a date range, a guard), and the task run again to compare calls and time.
Nothing from the session goes into the repo: no frames, no names, no message text.

The `jharokha` shell verbs are the tools called in-process, for checking a fix by hand:
`jharokha status`, `jharokha read --region teams.thread --format items`,
`jharokha --allow-input do teams.messages --args '{"from": "21/09", "to": "27/09"}'`.

To fix a reader without the session, capture what it read: `--capture DIR` (a global flag,
for the server and the shell verbs alike) saves each distinct frame a tool call or action
step identified as `<ts>-<frame_id>.png`, with a `.json` beside it holding the call, the app
and view identified, the whole screen's OCR lines and the regions placed. Off unless given.
The frames hold real names and messages: point DIR outside the repo (or at the git-ignored
`captures/`) and never commit them. A replay test can then run the readers on those lines
and regions offline.

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag
`vX.Y.Z`, push.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and
`pre-commit install` to run it before each commit.
