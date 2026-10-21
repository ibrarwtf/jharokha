import importlib
import importlib.util

# onnxruntime crashes the process when it loads after WinRT, so when the verify extra is
# installed it loads here, before anything in this package touches WinRT.
if importlib.util.find_spec("onnxruntime"):
    importlib.import_module("onnxruntime")
