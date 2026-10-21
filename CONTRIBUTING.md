# Contributing

- Small Conventional Commits on `main` (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`).
- Green `uv run ruff check`, `uv run pyright`, `uv run pytest` before pushing.
- Never commit anything read from a real remote session: no screenshots, window titles, hostnames or user names. Test screens are synthetic.
- Read-only by default. Anything that sends input to the session is registered only with `--allow-input`.
