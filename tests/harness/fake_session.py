"""A local window that stands in for a remote session: known text, no real data.

    uv run python tests/harness/fake_session.py [terminal|chat|mail|table|lock]

Then, in another terminal, let the allowlist accept python:

    $env:JHAROKHA_PROCESSES = "python.exe,pythonw.exe"
    uv run jharokha --window-title "Fake Remote" read
"""

from __future__ import annotations

import sys
import tkinter as tk

SCREENS = {
    "terminal": [
        "PS C:\\work> npm run build",
        "> build",
        "> tsc -p tsconfig.json",
        "Found 0 errors. Build succeeded in 12.4s",
        "PS C:\\work> _",
    ],
    "chat": ["Teams", "Alice  10:02  can you check order AB12?", "Bob  10:05  on it", "Alice  10:07  thanks"],
    "mail": ["Inbox", "Alice   Re: budget   09:12", "Bob   Build failed on main   08:40", "IT   Patch window tonight   07:55"],
    "table": ["Order   Status     Total", "AB12    Shipped    120.00", "AB13    Pending     45.50", "AB14    Cancelled    0.00"],
    "lock": ["", "", "Press Ctrl+Alt+Del to unlock"],
}


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "terminal"
    root = tk.Tk()
    root.title(f"Fake Remote Desktop - {name}")
    root.geometry("900x600")
    root.configure(bg="black" if name == "terminal" else "white")
    fg = "white" if name == "terminal" else "black"
    for i, line in enumerate(SCREENS[name]):
        tk.Label(root, text=line, fg=fg, bg=root["bg"], font=("Consolas", 12), anchor="w").place(
            x=20, y=20 + 28 * i
        )
    root.mainloop()


if __name__ == "__main__":
    main()
