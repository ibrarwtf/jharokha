"""A UI element detector on onnxruntime, for comparing with core/elements.py's pixel
blobs: a one-class YOLOv5-style ONNX model (input "images" 1x3x640x640, outputs of decoded
cx, cy, w, h, objectness and class scores per anchor), Pillow to scale, NMS in numpy. No
model ships with jharokha: the small ones are trained with Ultralytics code (AGPL) or on
data without a stated licence, and the one clearly MIT model (OmniParser v2 icon_detect_v3)
is 281 MB. The path is given. Loads on first use, like recognize.py."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from jharokha.core.models import Box, Frame

_SIZE = 640
_OVERLAP = 64  # px; tiles overlap by an element's largest size, so one is whole in a tile


class DetectorUnavailable(RuntimeError):
    pass


class Detector:
    def __init__(self, model: Path, score: float = 0.25, iou: float = 0.45) -> None:
        self._model, self._score, self._iou = model, score, iou
        self._session: Any = None
        self._busy = threading.Lock()

    def _load(self) -> Any:
        if self._session is None:
            try:
                import onnxruntime as ort

                opts = ort.SessionOptions()
                opts.log_severity_level = 4
                opts.enable_cpu_mem_arena = False
                self._session = ort.InferenceSession(
                    str(self._model), sess_options=opts, providers=["CPUExecutionProvider"]
                )
            except Exception as e:
                raise DetectorUnavailable(f"the detector did not load: {e}") from e
        return self._session

    def detect(self, frame: Frame, tile: bool = False) -> list[Box]:
        """Element boxes in client pixels. Whole frame scaled into 640 x 640, or with tile, in
        640 px tiles at full scale, which keeps a 16 px icon 16 px instead of 9 at the cost
        of one run a tile (4 on a 1166 x 1181 frame)."""
        img = frame.image().convert("RGB")
        if not tile:
            boxes, scores = self._run(img, (0, 0))
        else:
            parts = [
                self._run(img.crop((x, y, x + _SIZE, y + _SIZE)), (x, y))
                for y in _starts(frame.height)
                for x in _starts(frame.width)
            ]
            boxes = np.concatenate([b for b, _ in parts])
            scores = np.concatenate([s for _, s in parts])
        keep = _nms(boxes, scores, self._iou)
        out = []
        for x0, y0, x1, y1 in boxes[keep]:
            x0, y0 = max(0, round(x0)), max(0, round(y0))
            x1, y1 = min(frame.width, round(x1)), min(frame.height, round(y1))
            if x1 > x0 and y1 > y0:
                out.append((x0, y0, x1 - x0, y1 - y0))
        return sorted(out, key=lambda b: (b[1], b[0]))

    def _run(self, img: Image.Image, at: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        """Boxes (x0, y0, x1, y1 in the frame) and scores above the threshold in one image,
        letterboxed into the model's square: scaled to fit, padded grey at right and bottom."""
        scale = min(_SIZE / img.width, _SIZE / img.height)
        w, h = round(img.width * scale), round(img.height * scale)
        canvas = Image.new("RGB", (_SIZE, _SIZE), (114, 114, 114))
        canvas.paste(img.resize((w, h), Image.Resampling.BILINEAR) if scale != 1 else img)
        x = np.asarray(canvas, np.float32).transpose(2, 0, 1)[None] / 255
        with self._busy:
            outs = self._load().run(None, {"images": x})
        pred = np.concatenate([o.reshape(-1, o.shape[-1]) for o in outs])
        scores = pred[:, 4] * pred[:, 5:].max(axis=1)
        pred, scores = pred[scores >= self._score], scores[scores >= self._score]
        cx, cy, bw, bh = pred[:, 0], pred[:, 1], pred[:, 2], pred[:, 3]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1) / scale
        return boxes + np.array([at[0], at[1], at[0], at[1]], np.float32), scores


def _starts(n: int) -> list[int]:
    """Tile origins along a side of n px, evenly spaced, the last flush with the edge, as
    few as leave every pair of neighbours overlapping by at least _OVERLAP."""
    if n <= _SIZE:
        return [0]
    k = -(-(n - _SIZE) // (_SIZE - _OVERLAP)) + 1
    return [round(i * (n - _SIZE) / (k - 1)) for i in range(k)]


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    """Greedy non-maximum suppression: keep the best box, drop those overlapping it by more
    than iou, repeat."""
    order = np.argsort(-scores)
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep: list[int] = []
    while len(order):
        i, rest = int(order[0]), order[1:]
        keep.append(i)
        ix = np.minimum(boxes[i, 2], boxes[rest, 2]) - np.maximum(boxes[i, 0], boxes[rest, 0])
        iy = np.minimum(boxes[i, 3], boxes[rest, 3]) - np.maximum(boxes[i, 1], boxes[rest, 1])
        inter = np.clip(ix, 0, None) * np.clip(iy, 0, None)
        order = rest[inter / (area[i] + area[rest] - inter + 1e-9) <= iou]
    return keep
