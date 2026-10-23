def before_winrt() -> None:
    """Load onnxruntime's module (the model itself loads on first use). It crashes the
    process when it loads after WinRT (still true with onnxruntime 1.31), so everything in
    this package that loads WinRT calls this first. Not at import: onnxruntime takes about
    0.1 s to import, which the server's start would pay before it can list its tools."""
    import onnxruntime  # noqa: F401
