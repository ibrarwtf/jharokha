"""numpy imported on its first use instead of at import.

numpy takes about 0.14 s to import, and the server's start paid it before it could answer
`tools/list`, though nothing it does at start needs numpy. A module that uses it writes

    if TYPE_CHECKING:
        import numpy as np
    else:
        np = LazyNumpy(globals())

and its code uses `np` as before: the first attribute looked up imports numpy and puts it
in the module's globals in place of the stand-in, so later lookups cost nothing extra."""

from __future__ import annotations

from typing import Any


class LazyNumpy:
    def __init__(self, namespace: dict[str, Any], name: str = "np") -> None:
        self._namespace = namespace
        self._name = name

    def __getattr__(self, attr: str) -> Any:
        import numpy

        self._namespace[self._name] = numpy
        return getattr(numpy, attr)
