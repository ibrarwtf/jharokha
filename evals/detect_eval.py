"""Score text-less UI element detection on the generated screens with icons added.

    uv run python -m evals.detect_eval [--full] [--model FILE.onnx] [--save DIR]

Each Teams-, Outlook-, Edge- and terminal-like screen is copied, drawn (GDI, then the codec
as in the main eval), and given icons where those apps have them: the taskbar's apps and
tray, an icon rail, title-bar and header buttons, the compose bar's attach, emoji and send,
row checkboxes, window controls. Each icon is drawn on its own layer, so its true box is
its ink to the pixel. The frame is read through Windows OCR and panes from pixels, and each
detector is scored: recall and precision at IoU 0.5, time and peak memory a frame."""

from __future__ import annotations

import argparse
import dataclasses
import io
import os
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from statistics import mean, median

from PIL import Image, ImageDraw

from evals import metrics
from evals.render import RGB, Screen, render
from evals.screens import GREY, TASKBAR_Y, TERM_FG, W, browser, outlook, teams, terminal
from jharokha.core.elements import away_from_text, elements
from jharokha.core.models import Box, Frame, Line
from jharokha.core.panes import panes

Icon = tuple[str, int, int, int, RGB]  # shape, x, y, size, colour
Detector = Callable[[Frame, list[Line], list[Box]], list[Box]]

APP_COLOURS: list[RGB] = [
    (0, 120, 212),
    (232, 17, 35),
    (16, 124, 16),
    (255, 185, 0),
    (98, 100, 167),
    (0, 153, 188),
    (202, 80, 16),
    (136, 23, 152),
]
GLYPHS = ["ring", "square", "plus", "x", "arrow", "bell", "send", "clip", "chevron", "gear"]


def _taskbar_icons() -> list[Icon]:
    """Windows 11 at 125%: start, search and six apps centred, 30 px a tile; the tray's
    network, volume and battery glyphs left of the clock."""
    y = TASKBAR_Y + 15
    out: list[Icon] = [
        ("app", W // 2 - 200 + 50 * i, y, 30, APP_COLOURS[i % len(APP_COLOURS)]) for i in range(8)
    ]
    out += [(g, 980 + 26 * i, TASKBAR_Y + 22, 16, (36, 36, 36)) for i, g in enumerate("rsb")]
    return out


def with_icons(screen: Screen) -> tuple[Screen, list[Icon]]:
    """A copy of screen with room made where an icon goes, and the icons for it."""
    icons = _taskbar_icons()
    texts = list(screen.texts)
    g = GREY
    if screen.app == "teams":
        compose = next(i for i, t in enumerate(texts) if t.text == "Type a message")
        texts[compose] = dataclasses.replace(texts[compose], x=494)  # a "+" goes left of it
        icons += [("arrow", 100, 22, 18, g), ("arrow", 130, 22, 18, g), ("gear", 1020, 22, 18, g)]
        icons += [("avatar", 1110, 16, 30, APP_COLOURS[4]), ("dots", 1060, 29, 18, g)]
        icons += [(GLYPHS[i * 2 % 10], 32, 118 + 64 * i, 24, g) for i in range(5)]  # rail
        icons += [(s, 1000 + 40 * i, 92, 20, g) for i, s in enumerate(["ring", "bell", "gear"])]
        icons += [("plus", 460, TASKBAR_Y - 80, 20, g)]
        icons += [("clip", 1000, TASKBAR_Y - 80, 20, g), ("ring", 1040, TASKBAR_Y - 80, 20, g)]
        icons += [("send", 1090, TASKBAR_Y - 81, 22, APP_COLOURS[4])]
    elif screen.app == "outlook":
        icons += [(GLYPHS[(i * 3) % 10], 18, 175 + 48 * i, 24, g) for i in range(5)]  # rail
        icons += [(GLYPHS[i], x - 24, 121, 18, g) for i, x in enumerate((80, 200, 300, 390, 500))]
        icons += [("avatar", 1110, 30, 32, APP_COLOURS[2]), ("bell", 1000, 36, 20, g)]
        icons += [("gear", 1040, 36, 20, g)]
        icons += [(s, 1000 + 40 * i, 172, 20, g) for i, s in enumerate(["arrow", "send", "dots"])]
    elif screen.app == "browser":
        icons += [("arrow", 20, 61, 22, g), ("arrow", 52, 61, 22, g), ("ring", 84, 61, 22, g)]
        icons += [("app", 1020 + 36 * i, 61, 22, APP_COLOURS[i + 5]) for i in range(3)]
        icons += [("x", 236, 18, 10, g)]
        icons += [("check", 16, 347 + 36 * i, 14, g) for i in range(6)]
    elif screen.app == "terminal":
        icons += [("x", 170, 14, 10, TERM_FG), ("plus", 200, 12, 14, TERM_FG)]
        icons += [("chevron", 230, 14, 12, TERM_FG)]
        icons += [("square", 1076, 14, 10, TERM_FG), ("x", 1122, 14, 10, TERM_FG)]
    return dataclasses.replace(screen, texts=texts), icons


def draw_icon(img: Image.Image, icon: Icon) -> Box:
    """Draw icon onto img; its box is the ink of its own layer."""
    shape, x, y, s, c = icon
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col = (*c, 255)
    lw = 2 if s >= 16 else 1
    e = s - 1
    if shape == "app":
        d.rounded_rectangle((0, 0, e, e), radius=s // 5, fill=col)
        d.ellipse((s // 4, s // 4, e - s // 4, e - s // 4), fill=(255, 255, 255, 255))
    elif shape == "avatar":
        d.ellipse((0, 0, e, e), fill=col)
    elif shape in ("ring", "r"):
        d.ellipse((1, 1, e - 1, e - 1), outline=col, width=lw)
    elif shape in ("square", "check"):
        d.rounded_rectangle((1, 1, e - 1, e - 1), radius=2, outline=col, width=lw)
    elif shape == "plus":
        d.line((s // 2, 1, s // 2, e - 1), fill=col, width=lw)
        d.line((1, s // 2, e - 1, s // 2), fill=col, width=lw)
    elif shape == "x":
        d.line((1, 1, e - 1, e - 1), fill=col, width=lw)
        d.line((1, e - 1, e - 1, 1), fill=col, width=lw)
    elif shape == "arrow":
        d.line((2, s // 2, e - 2, s // 2), fill=col, width=lw)
        d.line((2, s // 2, s // 2, 3), fill=col, width=lw)
        d.line((2, s // 2, s // 2, e - 3), fill=col, width=lw)
    elif shape in ("bell", "b"):
        d.rounded_rectangle((s // 5, 2, e - s // 5, e - 4), radius=s // 3, outline=col, width=lw)
        d.line((2, e - 3, e - 2, e - 3), fill=col, width=lw)
    elif shape == "send":
        d.polygon([(1, 1), (e - 1, s // 2), (1, e - 1), (s // 4, s // 2)], fill=col)
    elif shape == "clip":
        d.rounded_rectangle((s // 4, 1, e - s // 4, e - 1), radius=s // 4, outline=col, width=lw)
    elif shape == "chevron":
        d.line((1, s // 3, s // 2, 2 * s // 3), fill=col, width=lw)
        d.line((s // 2, 2 * s // 3, e - 1, s // 3), fill=col, width=lw)
    elif shape == "gear":
        d.ellipse((1, 1, e - 1, e - 1), outline=col, width=lw)
        d.ellipse((s // 3, s // 3, e - s // 3, e - s // 3), fill=col)
    elif shape == "dots":
        r = max(1, s // 9)
        for cx in (r, s // 2, e - r):
            d.ellipse((cx - r, s // 2 - r, cx + r, s // 2 + r), fill=col)
    elif shape == "s":  # volume: a speaker
        d.polygon([(1, s // 3), (s // 3, s // 3), (2 * s // 3, 1), (2 * s // 3, e - 1)], fill=col)
        d.polygon([(2 * s // 3, e - 1), (s // 3, 2 * s // 3), (1, 2 * s // 3)], fill=col)
    else:
        raise ValueError(shape)
    bx = layer.getchannel("A").getbbox()
    assert bx is not None
    img.alpha_composite(layer, (x, y))
    return (x + bx[0], y + bx[1], bx[2] - bx[0], bx[3] - bx[1])


def icon_frame(screen: Screen, codec: bool = True) -> tuple[Frame, list[Box]]:
    """The screen with icons, drawn as the main eval draws it, and each icon's true box."""
    copy, icons = with_icons(screen)
    frame, _ = render(copy, "gdi", False)
    img = frame.image().convert("RGBA")
    truth = [draw_icon(img, i) for i in icons]
    if codec:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=80, subsampling=2)
        img = Image.open(buf).convert("RGBA")
    return Frame(frame.width, frame.height, img.tobytes("raw", "BGRA"), 0, time.time()), truth


def match(found: list[Box], truth: list[Box], at: float = 0.5) -> int:
    """True positives: each true box paired with at most one found box, best IoU first."""
    pairs = sorted(
        ((metrics.iou(t, f), i, j) for i, t in enumerate(truth) for j, f in enumerate(found)),
        reverse=True,
    )
    used_t: set[int] = set()
    used_f: set[int] = set()
    for v, i, j in pairs:
        if v < at:
            break
        if i not in used_t and j not in used_f:
            used_t.add(i)
            used_f.add(j)
    return len(used_t)


def suite(full: bool, tune: bool = False) -> list[Screen]:
    """Seeds from 100 up, as the main eval reports on; tune with --tune, seeds 1 to 3."""
    seeds = [100, 101, 102] if full else [100]
    if tune:
        seeds = [s - 99 for s in seeds]
    out: list[Screen] = []
    for s in seeds:
        out += [teams(s, px) for px in ([12, 14, 16] if full else [14])]
        out += [outlook(s, px) for px in ([12, 14, 16] if full else [14])]
        out += [browser(s, px) for px in ([12, 14, 16] if full else [14])]
        out += [terminal(s, px) for px in ([16, 20, 24] if full else [20])]
    return out


def _model_detectors(path: str) -> dict[str, Detector]:
    """The model on the whole frame and in tiles, raw and less the boxes on text: it boxes
    words as elements too, and the truth here is icons only."""
    from jharokha.win.detect import Detector as Model

    model = Model(Path(path))
    return {
        "model": lambda frame, lines, found: model.detect(frame),
        "model-text": lambda frame, lines, found: away_from_text(model.detect(frame), lines),
        "tiled": lambda frame, lines, found: model.detect(frame, tile=True),
        "tiled-text": lambda frame, lines, found: away_from_text(
            model.detect(frame, tile=True), lines
        ),
    }


def _rss_mb() -> float:
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
            (n, ctypes.c_size_t)
            for n in (
                "PeakWorkingSetSize",
                "WorkingSetSize",
                "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage",
                "QuotaPeakNonPagedPoolUsage",
                "QuotaNonPagedPoolUsage",
                "PagefileUsage",
                "PeakPagefileUsage",
            )
        ]

    c = Counters()
    c.cb = ctypes.sizeof(c)
    k, ps = ctypes.windll.kernel32, ctypes.windll.psapi
    k.GetCurrentProcess.restype = wintypes.HANDLE
    ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    ps.GetProcessMemoryInfo(k.GetCurrentProcess(), ctypes.byref(c), c.cb)
    return c.WorkingSetSize / 2**20


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m evals.detect_eval")
    p.add_argument("--full", action="store_true", help="three seeds and three sizes")
    p.add_argument("--tune", action="store_true", help="the tuning seeds, not the scored ones")
    p.add_argument("--model", help="also score this ONNX detector (win/detect.py)")
    p.add_argument("--clean", action="store_true", help="skip the codec")
    p.add_argument("--save", help="write each frame with its boxes to this directory")
    p.add_argument("--show", action="store_true", help="print misses and false boxes")
    args = p.parse_args()

    from jharokha.win.ocr import Ocr

    ocr = Ocr()
    rss: dict[str, float] = {}
    detectors: dict[str, Detector] = {"baseline": elements}
    if args.model:
        detectors |= _model_detectors(args.model)
    stats: dict[str, dict[str, list[float]]] = {
        n: {"tp": [], "n_true": [], "n_found": [], "ms": [], "peak": []} for n in detectors
    }
    per_app: dict[tuple[str, str], list[int]] = {}
    screens = suite(args.full, args.tune)
    for screen in screens:
        frame, truth = icon_frame(screen, codec=not args.clean)
        lines = ocr.recognize(frame)
        found_panes = panes(frame)
        for name, det in detectors.items():
            before = _rss_mb()
            det(frame, lines, found_panes)  # warm; the first call loads a model
            rss[name] = max(rss.get(name, 0.0), _rss_mb() - before)
            tracemalloc.start()
            t = time.perf_counter()
            got = det(frame, lines, found_panes)
            ms = (time.perf_counter() - t) * 1000
            peak = tracemalloc.get_traced_memory()[1] / 2**20
            tracemalloc.stop()
            tp = match(got, truth)
            s = stats[name]
            s["tp"].append(tp)
            s["n_true"].append(len(truth))
            s["n_found"].append(len(got))
            s["ms"].append(ms)
            s["peak"].append(peak)
            acc = per_app.setdefault((name, screen.app), [0, 0, 0])
            acc[0] += tp
            acc[1] += len(truth)
            acc[2] += len(got)
            if args.show:
                missed = [b for b in truth if match(got, [b]) == 0]
                extra = [f for f in got if match([f], truth) == 0]
                print(f"{name} {screen.name}: missed {missed}\n  extra {extra}")
            if args.save:
                os.makedirs(args.save, exist_ok=True)
                im = frame.image().convert("RGB")
                d = ImageDraw.Draw(im)
                for x, y, w, h in truth:
                    d.rectangle((x - 1, y - 1, x + w, y + h), outline=(0, 200, 0))
                for x, y, w, h in got:
                    d.rectangle((x - 2, y - 2, x + w + 1, y + h + 1), outline=(255, 0, 255))
                im.save(Path(args.save) / f"{screen.name}-{name}.png")
    print(f"{len(screens)} screens, codec {'off' if args.clean else 'on'}")
    for name, s in stats.items():
        r = sum(s["tp"]) / sum(s["n_true"])
        pr = sum(s["tp"]) / max(1, sum(s["n_found"]))
        print(
            f"  {name:9} recall {r:.3f}  precision {pr:.3f}  "
            f"ms median {median(s['ms']):.1f} max {max(s['ms']):.1f}  "
            f"python peak {mean(s['peak']):.0f} MB, RSS +{rss[name]:.0f} MB"
        )
        for (n, app), (tp, nt, nf) in sorted(per_app.items()):
            if n == name:
                print(f"    {app:9} recall {tp / nt:.3f}  precision {tp / max(1, nf):.3f}")


if __name__ == "__main__":
    main()
