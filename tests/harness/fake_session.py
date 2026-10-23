"""A local window that stands in for a remote session: a generated eval screen, no real data.

    uv run python tests/harness/fake_session.py [KIND] [seed]

KIND is terminal (default), teams, outlook, calendar, browser, ids or lock: a still
screen. `sim [seed] [app]` hosts the simulated session instead (evals/sim; app in front:
teams, terminal, outlook or browser): the window redraws as clicks, keys and the wheel
reach it, so jharokha's real Windows input path can be smoke-tested against it.

Then, in another terminal, let the allowlist accept python:

    $env:JHAROKHA_PROCESSES = "python.exe,pythonw.exe"
    uv run jharokha --window-title "Fake Remote" read
    uv run jharokha --window-title "Fake Remote" --allow-input click 607 1151  # Outlook
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
from jharokha.core.models import Frame  # noqa: E402

KINDS = {
    "terminal": screens.terminal,
    "teams": screens.teams,
    "outlook": screens.outlook,
    "calendar": screens.calendar,
    "browser": screens.browser,
    "ids": screens.ids,
    "lock": lambda seed: screens.lock(seed, seed % 5),
}


def photo(frame: Frame) -> tk.PhotoImage:
    buf = io.BytesIO()
    frame.image().save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


# Tk's names for the keys jharokha presses by name; modifier bits of event.state on Windows.
TK_KEYS = {"Return": "enter", "BackSpace": "backspace", "Escape": "esc", "Tab": "tab"}
TK_KEYS |= {k: k.lower() for k in ("Up", "Down", "Left", "Right", "Home", "End", "Delete")}
TK_KEYS |= {"Prior": "pageup", "Next": "pagedown", **{f"F{i}": f"f{i}" for i in range(1, 13)}}
TK_MODS = (("ctrl", 0x4), ("alt", 0x20000), ("shift", 0x1))


def host_sim(root: tk.Tk, seed: int, front: str) -> None:
    from evals.sim import Desktop, SimSource

    source = SimSource(Desktop(seed, front=front))
    w = source.window(None)
    assert w is not None
    label = tk.Label(root, borderwidth=0)
    label.pack()
    shown: list[object] = [None, None]  # the frame on screen and its image

    def refresh() -> None:
        f = source.frame(w)
        if f is not shown[0]:
            shown[:] = [f, photo(f)]
            label.configure(image=shown[1])  # type: ignore[arg-type]
        root.after(50, refresh)

    def key(e: tk.Event) -> None:
        mods = [m for m, bit in TK_MODS if int(e.state) & bit]
        name = TK_KEYS.get(e.keysym) or (e.keysym.lower() if len(e.keysym) == 1 else None)
        if name is None:
            return  # a modifier on its own
        if e.keysym in TK_KEYS or {"ctrl", "alt"} & set(mods):
            source.press(w, "+".join([*mods, name]))
        elif e.char and e.char.isprintable():
            source.type_text(w, e.char)

    label.bind("<Button-1>", lambda e: source.click(w, e.x, e.y, "left", False))
    label.bind("<Double-Button-1>", lambda e: source.click(w, e.x, e.y, "left", True))
    label.bind("<Button-3>", lambda e: source.click(w, e.x, e.y, "right", False))
    label.bind("<MouseWheel>", lambda e: source.scroll(w, e.x, e.y, e.delta // 120))
    root.bind("<Key>", key)
    refresh()


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else "terminal"
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    # Otherwise Windows stretches the window by the display scale and the pixels no longer
    # match the ground truth.
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    root = tk.Tk()
    root.title(f"Fake Remote Desktop - {name}")
    if name == "sim":
        host_sim(root, seed, sys.argv[3] if len(sys.argv) > 3 else "teams")
    else:
        frame, _ = render(KINDS[name](seed))
        img = photo(frame)
        tk.Label(root, image=img, borderwidth=0).pack()
    root.mainloop()


if __name__ == "__main__":
    main()
