from __future__ import annotations

import tomllib
from importlib.metadata import version as _installed
from pathlib import Path


def version() -> str:
    """The version of the code that is running. From a source checkout that is its
    pyproject.toml: `uv run --no-sync` leaves the installed metadata at whatever was last
    synced, so it can be releases behind the code."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        if project.get("name") == "jharokha":
            return project["version"]
    return _installed("jharokha")
