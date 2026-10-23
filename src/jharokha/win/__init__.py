# onnxruntime crashes the process when it loads after WinRT (still true with onnxruntime
# 1.31), so it loads here, before anything in this package touches WinRT. Only the module:
# the model itself loads on first use.
import onnxruntime  # noqa: F401
