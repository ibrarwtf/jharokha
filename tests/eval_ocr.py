"""Accuracy of the read path (Windows OCR + reading order) on synthetic app screens.

    uv run python tests/eval_ocr.py            # all
    uv run python tests/eval_ocr.py chat 16    # one screen, one pixel size

Screens imitate what a VDI shows: a terminal, a Teams-like chat (list beside thread), an
Outlook-like mailbox (folders, list, reading pane) and a block of lookalike-heavy IDs.
Ground truth is the text as rendered, column by column, so the character error rate (CER)
punishes both misreads and columns read in the wrong order; `lines` is the share of truth
lines that came back exactly, ignoring order, which separates the two.
"""

from __future__ import annotations

import difflib
import sys
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from jharokha.core.models import Frame
from jharokha.core.text import reading_order
from jharokha.win.ocr import Ocr

W, H = 1166, 1181
FONTS = {"consolas": "consola.ttf", "segoe": "segoeui.ttf", "segoe-semibold": "seguisb.ttf"}
LOOKALIKES = set("0O1lI5S8B")


@dataclass
class Col:
    x: int
    y: int
    lines: list[str]
    font: str = "segoe"
    pitch: float = 1.6  # line spacing as a multiple of the pixel size


SCREENS: dict[str, tuple[str, list[Col]]] = {
    "terminal": (
        "dark",
        [
            Col(
                20,
                20,
                [
                    "PS C:\\work\\api> npm run build",
                    "> api@2.4.1 build",
                    "> tsc -p tsconfig.json && vite build",
                    "Found 0 errors. Build succeeded in 12.4s",
                    "dist/index.html   0.46 kB",
                    "dist/assets/index-B3kL9.js   142.07 kB",
                    "PS C:\\work\\api> _",
                ],
                "consolas",
            )
        ],
    ),
    "chat": (
        "light",
        [
            Col(24, 16, ["Chat"], "segoe-semibold"),
            Col(
                24,
                70,
                [
                    "Alice Meyer",
                    "can you check order AB12?",
                    "Platform team",
                    "Bob: deploy is done",
                    "Priya Nair",
                    "thanks, see you at 3",
                    "IT Service Desk",
                    "Ticket INC0012345 resolved",
                ],
            ),
            Col(
                340,
                70,
                [
                    "Alice Meyer  10:02",
                    "can you check order AB12? customer says it never shipped",
                    "You  10:04",
                    "on it, the tracking page shows it left the warehouse on the 19th",
                    "Alice Meyer  10:07",
                    "ok, can you send me the tracking id",
                    "You  10:08",
                    "1Z5S8B0O1I9 via UPS, ETA tomorrow 15:00",
                ],
            ),
        ],
    ),
    "mail": (
        "light",
        [
            Col(16, 20, ["Inbox", "Sent Items", "Drafts", "Archive", "Deleted Items"]),
            Col(
                220,
                20,
                [
                    "Alice Meyer",
                    "Re: Q4 budget review",
                    "Please see the attached figures for",
                    "Bob Lindqvist",
                    "Build failed on main",
                    "Pipeline 2547241 failed at step test",
                    "IT Service Desk",
                    "Patch window tonight 22:00",
                    "Servers will restart between 22:00 and",
                ],
            ),
            Col(
                600,
                20,
                [
                    "Build failed on main",
                    "Bob Lindqvist  Thu 08:40",
                    "To: Platform team",
                    "Pipeline 2547241 failed at step test with exit code 1.",
                    "All 5 workers failed on their first connection within 2 s.",
                    "Build 2541506 connected yesterday with the same config.",
                    "Can someone check the allowlist on zone z0rd5?",
                ],
            ),
        ],
    ),
    "ids": (
        "light",
        [
            Col(
                20,
                20,
                [
                    "Ticket INC0012345 assigned to O1iver",
                    "Host VM-PRD-0l7 is up",
                    "Serial 5S8B0O1I",
                    "Order AB12 shipped, AB13 pending",
                ],
            )
        ],
    ),
}


def render(cols: list[Col], px: int, dark: bool) -> tuple[Frame, list[str]]:
    img = Image.new("RGBA", (W, H), "black" if dark else "white")
    d = ImageDraw.Draw(img)
    truth: list[str] = []
    for col in cols:
        font = ImageFont.truetype(FONTS[col.font], px)
        for i, line in enumerate(col.lines):
            y = col.y + int(px * col.pitch) * i
            d.text((col.x, y), line, fill="white" if dark else "black", font=font)
            truth.append(line)
    return Frame(W, H, img.tobytes("raw", "BGRA"), 0, time.time()), truth


def score(truth: list[str], got: list[str]) -> tuple[float, float, int]:
    t, g = "\n".join(truth), "\n".join(got)
    sm = difflib.SequenceMatcher(None, t, g)
    misses = sum(
        1
        for tag, i1, i2, _, _ in sm.get_opcodes()
        if tag != "equal"
        for c in t[i1:i2]
        if c in LOOKALIKES
    )
    exact = sum(1 for ln in truth if ln in got) / len(truth)
    return 1 - sm.ratio(), exact, misses


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    sizes = [int(sys.argv[2])] if len(sys.argv) > 2 else [13, 16, 20]
    ocr = Ocr()
    print(f"{'screen':9} {'px':>3} {'up':>4} {'ms':>5} {'CER':>6} {'lines':>6}  lookalike misses")
    for name, (theme, cols) in SCREENS.items():
        if only and name != only:
            continue
        for px in sizes:
            for up in (1.0, 1.5, 2.0):
                frame, truth = render(cols, px, theme == "dark")
                t = time.perf_counter()
                got = [ln.text for ln in reading_order(ocr.recognize(frame, upscale=up))]
                ms = (time.perf_counter() - t) * 1000
                cer, exact, misses = score(truth, got)
                print(f"{name:9} {px:3} {up:4.1f} {ms:5.0f} {cer:6.3f} {exact:6.2f}  {misses}")


if __name__ == "__main__":
    main()
