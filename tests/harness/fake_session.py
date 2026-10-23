"""A local window that stands in for a remote session: a generated eval screen, no real data.

    uv run python tests/harness/fake_session.py [KIND] [seed]

KIND is terminal (default), teams, outlook, calendar, browser, ids or lock.

Then, in another terminal, let the allowlist accept python:

    $env:JHAROKHA_PROCESSES = "python.exe,pythonw.exe"
    uv run jharokha --window-title "Fake Remote" read
"""

from __future__ import annotations

import base64
import ctypes
import io
import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))
from evals import screens  # noqa: E402
from evals.render import render  # noqa: E402

KINDS = {
    "terminal": screens.terminal,
    "teams": screens.teams,
    "outlook": screens.outlook,
    "calendar": screens.calendar,
    "browser": screens.browser,
    "ids": screens.ids,
    "lock": lambda seed: screens.lock(seed, seed % 5),
}


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "terminal"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    frame, _ = render(KINDS[name](seed))
    buf = io.BytesIO()
    frame.image().save(buf, "PNG")
    # Otherwise Windows stretches the window by the display scale and the pixels no longer
    # match the ground truth.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    root.title(f"Fake Remote Desktop - {name}")
    img = tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
    tk.Label(root, image=img, borderwidth=0).pack()
    root.mainloop()


if __name__ == "__main__":
    main()
