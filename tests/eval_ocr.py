"""Character error rate of Windows OCR on synthetic screens, per font size and upscale.

uv run python tests/eval_ocr.py
"""

from __future__ import annotations

import difflib
import time

from PIL import Image, ImageDraw, ImageFont

from jharokha.core.models import Frame
from jharokha.core.text import reading_order
from jharokha.win.ocr import Ocr

SCREENS = {
    "terminal": [
        "PS C:\\work> npm run build",
        "Found 0 errors. Build succeeded in 12.4s",
        "exit code 0",
    ],
    "chat": [
        "Alice 10:02 can you check order AB12?",
        "Bob 10:05 on it, ETA 15 min",
        "Alice 10:07 thanks",
    ],
    "table": [
        "Order Status Total",
        "AB12 Shipped 120.00",
        "AB13 Pending 45.50",
        "AB14 Cancelled 0.00",
    ],
    "ids": ["Ticket INC0012345 assigned to O1iver", "Host VM-PRD-0l7 is up", "Serial 5S8B0O1I"],
}
LOOKALIKES = set("0O1lI5S8B")


def render(lines: list[str], size_pt: int, dark: bool) -> Frame:
    font = ImageFont.truetype("consola.ttf", size_pt)
    img = Image.new("RGBA", (900, 60 + 40 * len(lines)), "black" if dark else "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        d.text((20, 20 + 40 * i), line, fill="white" if dark else "black", font=font)
    return Frame(img.width, img.height, img.tobytes("raw", "BGRA"), 0, time.time())


def cer(truth: str, got: str) -> float:
    sm = difflib.SequenceMatcher(None, truth, got)
    return 1 - sm.ratio()


def main() -> None:
    ocr = Ocr()
    print(
        f"{'screen':9} {'pt':>3} {'dark':>4} {'up':>4} {'ms':>5} {'CER':>6}  lookalike-char misses"
    )
    for name, lines in SCREENS.items():
        truth = "\n".join(lines)
        for pt in (11, 14, 18):
            for dark in (False, True):
                for up in (1.0, 1.5, 2.0):
                    frame = render(lines, pt, dark)
                    t = time.perf_counter()
                    got = "\n".join(
                        ln.text for ln in reading_order(ocr.recognize(frame, upscale=up))
                    )
                    ms = (time.perf_counter() - t) * 1000
                    misses = sum(
                        1
                        for tag, i1, i2, _, _ in difflib.SequenceMatcher(
                            None, truth, got
                        ).get_opcodes()
                        if tag != "equal"
                        for c in truth[i1:i2]
                        if c in LOOKALIKES
                    )
                    row = f"{name:9} {pt:3} {str(dark):>4} {up:4.1f} {ms:5.0f}"
                    print(f"{row} {cer(truth, got):6.3f}  {misses}")


if __name__ == "__main__":
    main()
