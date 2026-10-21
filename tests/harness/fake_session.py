"""A local window that stands in for a remote session: the synthetic eval screens, no real data.

    uv run python tests/harness/fake_session.py [terminal|chat|mail|ids|lock] [px]

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

sys.path.insert(0, str(Path(__file__).parents[1]))
from eval_ocr import SCREENS, Col, render  # noqa: E402

SCREENS = {**SCREENS, "lock": ("light", [Col(420, 560, ["Press Ctrl+Alt+Del to unlock"])])}


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "terminal"
    px = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    theme, cols = SCREENS[name]
    frame, _ = render(cols, px, theme == "dark")
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
