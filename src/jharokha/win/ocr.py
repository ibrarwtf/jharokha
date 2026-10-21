"""Windows.Media.Ocr in-process on a raw BGRA buffer. One cached engine, one worker thread."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any

from jharokha.core.models import Box, Frame, Line, Word

_MAX_DIM = 7500  # OcrEngine.max_image_dimension on current Windows


class OcrUnavailable(RuntimeError):
    pass


class Ocr:
    """Owns the engine and the thread with the event loop that pywinrt async calls need."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="jharokha-ocr").start()
        self._engine = self._run(self._create()).result()

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Future[Any]:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _create(self) -> Any:
        from winrt.windows.media.ocr import OcrEngine

        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise OcrUnavailable("No Windows OCR language pack installed")
        return engine

    def recognize(
        self, frame: Frame, region: Box | None = None, upscale: float = 1.0
    ) -> list[Line]:
        """Lines in client coordinates. region crops before OCR; upscale helps small text."""
        return self._run(self._recognize(frame, region, upscale)).result()

    async def _recognize(self, frame: Frame, region: Box | None, upscale: float) -> list[Line]:
        from PIL import Image
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import Buffer

        ox, oy = 0, 0
        img = Image.frombuffer("RGBA", (frame.width, frame.height), frame.bgra, "raw", "BGRA", 0, 1)
        if region:
            ox, oy, rw, rh = region
            img = img.crop((ox, oy, ox + rw, oy + rh))
        if upscale != 1.0:
            img = img.resize(
                (int(img.width * upscale), int(img.height * upscale)), Image.Resampling.BICUBIC
            )
        w, h = img.size
        if w > _MAX_DIM or h > _MAX_DIM:
            raise OcrUnavailable(f"image {w}x{h} exceeds OCR limit {_MAX_DIM}")
        raw = img.tobytes("raw", "BGRA")
        buf = Buffer(len(raw))
        buf.length = len(raw)
        memoryview(buf)[:] = raw
        bitmap = SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, w, h)
        result = await self._engine.recognize_async(bitmap)
        s = 1 / upscale

        def box(r: Any) -> Box:
            return (int(r.x * s) + ox, int(r.y * s) + oy, int(r.width * s), int(r.height * s))

        lines: list[Line] = []
        for ln in result.lines:
            words = tuple(Word(w.text, box(w.bounding_rect)) for w in ln.words)
            x1 = min(w.box[0] for w in words)
            y1 = min(w.box[1] for w in words)
            x2 = max(w.box[0] + w.box[2] for w in words)
            y2 = max(w.box[1] + w.box[3] for w in words)
            lines.append(Line(ln.text, (x1, y1, x2 - x1, y2 - y1), words))
        return lines
