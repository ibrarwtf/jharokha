"""Experiment: is a UI element detector or a second OCR worth adding next to Windows OCR?

    uv run --with rapidocr --with onnxruntime python tests/eval_vision.py MODELS_DIR [live N]

MODELS_DIR holds ONNX exports of YOLO UI detectors (screenparser.onnx at 1280, gpa.onnx at
640, carwin.onnx at 640); any that is missing is skipped. With `live N`, N frames of the
real remote-desktop window are measured too (timings and agreement only; nothing is saved).

Per candidate and screen: wall ms (warm), CPU ms, RSS delta after load, and for OCR the
CER against the synthetic ground truth; for detectors the element count, class histogram
and the share of OCR lines that fall inside a detected element.
"""

from __future__ import annotations

import ctypes
import difflib
import logging
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np
import onnxruntime  # noqa: F401  # must load before any winrt module or the process crashes
from PIL import Image

from jharokha.core.models import Frame, Line, Word
from jharokha.core.text import reading_order
from jharokha.win.ocr import Ocr

sys.path.insert(0, str(Path(__file__).parent))
from eval_ocr import SCREENS, render  # noqa: E402

logging.disable(logging.INFO)


def rss_mb() -> float:
    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("rest", ctypes.c_size_t * 6),
        ]

    pmc = PMC(cb=ctypes.sizeof(PMC))
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
    k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / 2**20


def rgb(frame: Frame) -> Image.Image:
    return Image.frombuffer(
        "RGBA", (frame.width, frame.height), frame.bgra, "raw", "BGRA", 0, 1
    ).convert("RGB")


def cer(truth: list[str], got: list[str]) -> float:
    return 1 - difflib.SequenceMatcher(None, "\n".join(truth), "\n".join(got)).ratio()


def timed(fn, *a):  # noqa: ANN001, ANN202
    t, c = time.perf_counter(), time.process_time()
    out = fn(*a)
    return out, (time.perf_counter() - t) * 1000, (time.process_time() - c) * 1000


class WinOcr:
    name = "windows-ocr"

    def __init__(self) -> None:
        self.ocr = Ocr()

    def __call__(self, frame: Frame) -> list[Line]:
        return reading_order(self.ocr.recognize(frame))


class Rapid:
    name = "rapidocr"

    def __init__(self) -> None:
        from rapidocr import RapidOCR

        self.ocr = RapidOCR()
        self.scores: list[float] = []

    def __call__(self, frame: Frame) -> list[Line]:
        r = self.ocr(np.array(rgb(frame)))
        lines = []
        self.scores = list(r.scores or [])
        boxes = r.boxes if r.boxes is not None else []
        for box, txt in zip(boxes, r.txts or [], strict=False):
            xs, ys = [int(p[0]) for p in box], [int(p[1]) for p in box]
            b = (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
            lines.append(Line(txt, b, (Word(txt, b),)))
        return reading_order(lines)


class Detector:
    def __init__(self, path: Path, size: int, names: list[str] | None = None) -> None:
        import onnxruntime as ort

        self.name = path.stem
        self.size = size
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name
        meta = self.sess.get_modelmeta().custom_metadata_map.get("names")
        self.names = names or (eval(meta) if meta else None)  # noqa: S307  # ultralytics stores a dict literal

    def __call__(
        self, frame: Frame, conf: float = 0.25, iou: float = 0.45
    ) -> list[tuple[str, float, tuple[int, int, int, int]]]:
        img = rgb(frame)
        s = self.size / max(img.size)
        nw, nh = round(img.width * s), round(img.height * s)
        canvas = Image.new("RGB", (self.size, self.size), (114, 114, 114))
        canvas.paste(img.resize((nw, nh), Image.Resampling.BILINEAR), (0, 0))
        x = np.asarray(canvas, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
        out = self.sess.run(None, {self.input: x})[0]
        pred = out[0].T if out.shape[1] < out.shape[2] else out[0]  # (n, 4 + classes)
        ncls = len(self.names) if self.names else pred.shape[1] - 4
        if pred.shape[1] == 5 + ncls:  # YOLOv5 layout: objectness then class scores
            cls = pred[:, 5:]
            boxes, scores, classes = pred[:, :4], pred[:, 4] * cls.max(1), cls.argmax(1)
        else:
            boxes, scores, classes = pred[:, :4], pred[:, 4:].max(1), pred[:, 4:].argmax(1)
        keep = scores >= conf
        boxes, scores, classes = boxes[keep], scores[keep], classes[keep]
        xyxy = (
            np.stack(
                [
                    boxes[:, 0] - boxes[:, 2] / 2,
                    boxes[:, 1] - boxes[:, 3] / 2,
                    boxes[:, 0] + boxes[:, 2] / 2,
                    boxes[:, 1] + boxes[:, 3] / 2,
                ],
                1,
            )
            / s
        )
        order = scores.argsort()[::-1]
        kept: list[int] = []
        for i in order:
            if all(_iou(xyxy[i], xyxy[j]) < iou for j in kept):
                kept.append(i)
        result = []
        for i in kept:
            x1, y1, x2, y2 = (int(v) for v in xyxy[i])
            label = self.names[int(classes[i])] if self.names else str(int(classes[i]))
            result.append((label, float(scores[i]), (x1, y1, x2 - x1, y2 - y1)))
        return result


def _iou(a, b):  # noqa: ANN001, ANN202
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua else 0


def inside(line: Line, elements) -> bool:  # noqa: ANN001
    cx, cy = line.box[0] + line.box[2] / 2, line.box[1] + line.box[3] / 2
    return any(x <= cx <= x + w and y <= cy <= y + h for _, _, (x, y, w, h) in elements)


def main() -> None:
    models = Path(sys.argv[1])
    live = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "live" else 0

    frames: list[tuple[str, Frame, list[str] | None]] = []
    for name, (theme, cols) in SCREENS.items():
        frame, truth = render(cols, 16, theme == "dark")
        frames.append((name, frame, truth))
    if live:
        from jharokha.core.screen import Screen
        from jharokha.win.source import WindowsSource

        screen = Screen(WindowsSource())
        for i in range(live):
            frames.append((f"live{i}", screen.frame(None)[1], None))
            time.sleep(0.5)

    base = rss_mb()
    ocrs = []
    for cls in (WinOcr, Rapid):
        before = rss_mb()
        try:
            o = cls()
        except Exception as e:
            print(f"{cls.name}: not available ({e})")
            continue
        o(frames[0][1])  # warm up
        ocrs.append((o, rss_mb() - before))
    dets = []
    for fname, size in (("screenparser.onnx", 1280), ("gpa.onnx", 640), ("carwin.onnx", 640)):
        p = models / fname
        if not p.exists():
            continue
        before = rss_mb()
        d = Detector(p, size)
        d(frames[0][1])
        dets.append((d, rss_mb() - before))
    print(f"baseline rss {base:.0f} MB")
    for o, delta in ocrs:
        print(f"{o.name:14} load +{delta:.0f} MB rss")
    for d, delta in dets:
        print(f"{d.name:14} load +{delta:.0f} MB rss  classes={len(d.names) if d.names else '?'}")

    print(f"\n{'screen':9} {'candidate':14} {'wall':>6} {'cpu':>6} {'CER':>6} {'lines':>5}  extra")
    win_lines: dict[str, list[Line]] = {}
    for name, frame, truth in frames:
        for o, _ in ocrs:
            lines, wall, cpu = timed(o, frame)
            if o.name == "windows-ocr":
                win_lines[name] = lines
            c = f"{cer(truth, [ln.text for ln in lines]):6.3f}" if truth else "   n/a"
            extra = ""
            if isinstance(o, Rapid) and o.scores:
                low = sum(1 for s in o.scores if s < 0.9)
                agree = sum(1 for ln in lines if any(ln.text == w.text for w in win_lines[name]))
                extra = f"conf<0.9: {low}/{len(o.scores)}  same-as-windows: {agree}/{len(lines)}"
            print(f"{name:9} {o.name:14} {wall:6.0f} {cpu:6.0f} {c} {len(lines):5}  {extra}")
        for d, _ in dets:
            els, wall, cpu = timed(d, frame)
            hist: dict[str, int] = {}
            for label, _, _ in els:
                hist[label] = hist.get(label, 0) + 1
            top = ", ".join(f"{k}:{v}" for k, v in sorted(hist.items(), key=lambda kv: -kv[1])[:5])
            covered = sum(1 for ln in win_lines.get(name, []) if inside(ln, els))
            print(
                f"{name:9} {d.name:14} {wall:6.0f} {cpu:6.0f} {'   n/a'} {len(els):5}  "
                f"ocr-lines-in-element: {covered}/{len(win_lines.get(name, []))}  {top}"
            )
    print(f"\nfinal rss {rss_mb():.0f} MB (baseline {base:.0f})")
    os._exit(0)  # onnxruntime and winrt threads can hang interpreter shutdown


if __name__ == "__main__":
    main()
