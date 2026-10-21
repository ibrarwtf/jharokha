"""A second reading of single lines with RapidOCR, recognition only, on the boxes Windows OCR
found. Optional: needs the `verify` extra. 30 to 60 ms a line; the model loads on first use,
which takes about 3 s."""

from __future__ import annotations

import threading
from typing import Any

from PIL import Image

from jharokha.core.models import Box, Frame


class VerifyUnavailable(RuntimeError):
    pass


class Verifier:
    def __init__(self) -> None:
        try:
            import numpy
            from rapidocr import RapidOCR
        except ImportError as e:
            raise VerifyUnavailable(
                "verify needs the optional extra: install jharokha[verify], e.g. "
                'uvx --from "jharokha[verify] @ git+https://github.com/ibrarwtf/jharokha" jharokha'
            ) from e
        self._array = numpy.asarray
        self._rapid: Any = RapidOCR(
            params={"Global.use_det": False, "Global.use_cls": False, "Global.log_level": "error"}
        )
        self._busy = threading.Lock()

    def read(self, frame: Frame, boxes: list[Box]) -> list[str]:
        img = frame.image().convert("RGB")
        out = []
        with self._busy:
            for x, y, w, h in boxes:
                # Recognition alone needs a margin around the glyphs and more pixels than
                # screen text has.
                pad = h // 2
                crop = img.crop(
                    (
                        max(0, x - pad),
                        max(0, y - pad),
                        min(frame.width, x + w + pad),
                        min(frame.height, y + h + pad),
                    )
                )
                crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.BICUBIC)
                r = self._rapid(self._array(crop), use_det=False, use_cls=False)
                out.append(" ".join(r.txts or ()))
        return out
