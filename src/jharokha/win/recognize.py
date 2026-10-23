"""A second OCR engine: PP-OCRv6's recognition model (models/NOTICE.md) on onnxruntime, for
any box: a line Windows OCR found, or a full-width row strip where Windows OCR found
nothing. Recognition only, no detection: the box says where the text is. The model loads on
first use. Preprocessing and decoding follow RapidOCR's, without OpenCV."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from jharokha.core.models import Box, Frame

MODEL = Path(__file__).parent / "models" / "PP-OCRv6_rec_small.onnx"
_HEIGHT, _MIN_RATIO = 48, 320 / 48  # the model's input height and narrowest width


class RecognizerUnavailable(RuntimeError):
    pass


class Recognizer:
    def __init__(self) -> None:
        try:
            import onnxruntime as ort

            opts = ort.SessionOptions()
            opts.log_severity_level = 4
            # The arena keeps the peak of the widest strip ever read; without it memory
            # goes back after each read.
            opts.enable_cpu_mem_arena = False
            # Measured on a 4-core i5 laptop: 2 threads read a line as fast as the default 4
            # at half the CPU, and without spinning the threads stop burning ~300 ms of CPU
            # in the second after every read.
            opts.intra_op_num_threads = 2
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            self._session: Any = ort.InferenceSession(
                str(MODEL), sess_options=opts, providers=["CPUExecutionProvider"]
            )
            chars = self._session.get_modelmeta().custom_metadata_map["character"].splitlines()
        except Exception as e:
            raise RecognizerUnavailable(f"the recognition model did not load: {e}") from e
        # CTC: index 0 is the blank, and the model's last class is a space.
        self._chars = ["", *chars, " "]
        self._busy = threading.Lock()

    def read(self, frame: Frame, boxes: list[Box], margin: float = 0.5) -> list[str]:
        """Text in each box of the frame, in client pixels, read with margin x its height
        around it. The model wants space around the glyphs: a box Windows OCR found hugs
        the ink and reads best with 0.5; a row strip a terminal's pitch tall already has
        it, and a margin brings in the rows beside it, so 0."""
        img = frame.image().convert("RGB")
        crops = []
        for x, y, w, h in boxes:
            pad = int(h * margin)
            crop = img.crop(
                (
                    max(0, x - pad),
                    max(0, y - pad),
                    min(frame.width, x + w + pad),
                    min(frame.height, y + h + pad),
                )
            )
            # Screen text has fewer pixels than the model was trained on.
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.BICUBIC)
            # RGB, though PaddleOCR trains on BGR: on the eval screens' 1781 line boxes RGB
            # read 1564 exactly, BGR 1559, and RGB is what verify always fed it.
            crops.append(np.asarray(crop))
        return self.recognize(crops)

    def recognize(self, images: list[np.ndarray]) -> list[str]:
        """Text in each image (H x W x 3, uint8)."""
        out = []
        with self._busy:
            # One image a run, where RapidOCR pads six into one: on 47 terminal lines that
            # was 28 ms a line instead of 41, and a peak of 180 MB instead of 444.
            for img in images:
                ratio = max(_MIN_RATIO, img.shape[1] / img.shape[0])
                probs = self._session.run(None, {"x": _normalize(img, ratio)[None]})[0]
                out.append(self._decode(probs[0].argmax(axis=1)))
        return out

    def _decode(self, best: np.ndarray) -> str:
        """Greedy CTC: repeated classes are one character, blanks separate them."""
        keep = np.ones(len(best), dtype=bool)
        keep[1:] = best[1:] != best[:-1]
        return "".join(self._chars[c] for c in best[keep & (best != 0)])


def _normalize(img: np.ndarray, ratio: float) -> np.ndarray:
    """Scaled to the model's height keeping its aspect, to -1..1, CHW, padded with zeros on
    the right to width ratio x height."""
    h, w = img.shape[:2]
    width = int(_HEIGHT * ratio)
    resized_w = min(width, int(np.ceil(_HEIGHT * w / h)))
    small = _bilinear(img, _HEIGHT, resized_w)
    out = np.zeros((3, _HEIGHT, width), dtype=np.float32)
    out[:, :, :resized_w] = (small.transpose(2, 0, 1) / 255 - 0.5) / 0.5
    return out


def _bilinear(img: np.ndarray, h: int, w: int) -> np.ndarray:
    """OpenCV's INTER_LINEAR resize of uint8, bit for bit: float32 sample positions, 11-bit
    fixed-point weights, and its SIMD path's rounding. The model is trained on what that
    produces; a float resize differs by 1 on a few percent of pixels, which flipped one
    character in 2 of 17 terminal rows. Pillow's filters antialias when shrinking."""

    def taps(n_in: int, n_out: int, clamp: bool) -> tuple[np.ndarray, ...]:
        pos = ((np.arange(n_out) + 0.5) * (n_in / n_out) - 0.5).astype(np.float32)
        lo = np.floor(pos).astype(np.intp)
        f = pos - lo.astype(np.float32)
        if clamp:  # OpenCV zeroes the weight at the edges across, not down
            f[lo < 0] = 0
            lo[lo < 0] = 0
            edge = lo >= n_in - 1
            f[edge] = 0
            lo[edge] = n_in - 1
        c0 = np.rint((np.float32(1) - f) * np.float32(2048)).astype(np.int64)
        return np.clip(lo, 0, n_in - 1), np.clip(lo + 1, 0, n_in - 1), c0, 2048 - c0

    y0, y1, b0, b1 = taps(img.shape[0], h, False)
    x0, x1, a0, a1 = taps(img.shape[1], w, True)
    src = img.astype(np.int64)
    rows = src[:, x0] * a0[None, :, None] + src[:, x1] * a1[None, :, None]
    top, bottom = rows[y0] >> 4, rows[y1] >> 4
    v = ((top * b0[:, None, None]) >> 16) + ((bottom * b1[:, None, None]) >> 16)
    return ((v + 2) >> 2).clip(0, 255).astype(np.uint8)
