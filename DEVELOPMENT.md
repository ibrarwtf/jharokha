# Development

```
uv sync
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest            # includes one test on the real Windows OCR engine
```

## Try it without a remote session

`tests/harness/fake_session.py` opens a local window showing one of the synthetic `eval_ocr` screens. The allowlist of client processes is `msrdc.exe`, `mstsc.exe`, `windowsapp.exe`, `windows365.exe`; override it with `JHAROKHA_PROCESSES` (comma-separated) to accept the fake window:

```
uv run python tests/harness/fake_session.py terminal     # also: chat, mail, ids, lock; optional px
$env:JHAROKHA_PROCESSES = "python.exe,pythonw.exe"
uv run jharokha --window-title "Fake Remote" read
uv run jharokha --window-title "Fake Remote" bench
```

MCP end to end: `npx @modelcontextprotocol/inspector --cli uv run jharokha --method tools/list`, or register it in Claude Code with `claude mcp add --transport stdio jharokha -- uv run --directory <repo> jharokha`.

## Measured (2026-10-21, 4-core laptop, 125% scaling)

Real Windows 365 window, 1166x1181 client area:

| Step | ms |
|---|---|
| PrintWindow capture | 37 |
| OCR engine creation (once) | 97 |
| OCR full frame, 1.0x | 162 |
| OCR full frame, 1.5x | 358 |
| OCR full frame, 2.0x | 584 |
| OCR quarter frame, 2.0x | 108 |

- 1.5x upscale found a few more small-text lines than 1.0x, at twice the time. Default is 1.0x for the full frame and 2.0x for a region of at most a quarter of the frame.
- Two captures of an idle screen differ by ~20 pixels (text caret). The cache treats a changed area of up to 100 px² as unchanged.
- Minimized: the client rect is 0x0 and PrintWindow has nothing to draw. `IsIconic` is checked first and reported as `minimized`.
- A lock screen inside the session is detected from its text (`Press Ctrl+Alt+Del`), there is no other signal from outside.
- Fake window (`terminal` screen, 900x600): capture 59 ms, OCR 51 ms, ~52 tokens concise. Windows OCR dropped a lone `0` and `\work>` on white-on-black Consolas 12 pt; use a `region` to zoom when a value matters.
- Final check on the real window (69 text lines): `jharokha read` ~1066 tokens concise, `bench` OCR 198 ms, peak RSS 80 MB. Targets (< 300 ms, < 1.5k tokens, < 150 MB) met.

### Read accuracy (`uv run python tests/eval_ocr.py`)

Synthetic screens at the real client size: a Consolas terminal, a Segoe UI Teams-like chat (list beside thread), an Outlook-like mailbox (folders, list, reading pane) and lookalike-heavy IDs. CER is on the text in reading order, so it punishes misreads and panels read in the wrong order; `lines` is the share of lines read exactly, ignoring order.

| screen | px | CER 1.0x | CER 2.0x | lines exact |
|---|---|---|---|---|
| terminal | 13 / 16 / 20 | .146 / .060 / .046 | .073 / .082 / .041 | .00 / .43 / .43 |
| chat | 13 / 16 / 20 | .046 / .040 / .043 | .040 / .040 / .033 | .47 / .47 / .41 |
| mail | 13 / 16 / 20 | .028 / .001 / .546 | .005 / .001 / .546 | .71 / .95 / .90 |
| ids | 13 / 16 / 20 | .065 / .101 / .101 | .047 / .101 / .101 | .25 / .00 / .00 |

- Before panel-aware reading order (v0.3.0) chat and mail scored CER .56 and .62 with the same lines exact: the columns were interleaved. Reading order now cuts a region at a blank band that runs its full height (three line heights or wider) into side-by-side panels, then at full-width bands into blocks, then rows.
- mail at 20 px fails the panel split because the list column runs into the reading pane with no gap; real Outlook truncates list text, so the gap exists there.
- Low `lines exact` on the terminal is Windows OCR splitting wide-spaced columns into separate lines and reading `0` as `e` in Consolas; upscaling does not fix it. The `ids` screen is lookalikes by design.
- Upscaling costs 4x time for little gain on these screens, so the full frame stays at 1.0x and regions of at most a quarter frame use 2.0x.

### UI detector or second OCR? (2026-10-21; the experiment script is in git history, commit 2495068)

Question: what to add next to Windows OCR, a UI element detector or a second OCR with confidence. Candidates from a literature pass: YOLO detectors ScreenParser (55 classes, 1280 px), Salesforce GPA (1 class, 640 px), carwin (1 class, 640 px), exported to ONNX and run with onnxruntime; RapidOCR (PP-OCR mobile, onnxruntime). Small VLMs were ruled out on paper: 260 MB to 2.5 GB and seconds per frame on CPU.

Measured on the same laptop, synthetic 16 px screens plus four live frames of the real session (83 OCR lines):

| candidate | load RSS | full frame wall | adds |
|---|---|---|---|
| Windows OCR | +8 MB | 50 to 210 ms | baseline |
| RapidOCR, full frame | +77 MB | 1.4 to 5.5 s | CER terminal .060 to .022, chat .040 to .005, ids .101 to .009; per-line confidence |
| RapidOCR, recognition only on a Windows OCR line box | same | 20 to 30 ms per line | lookalike lines exact 3/4 vs 0/4 for Windows OCR |
| ScreenParser 1280 | +624 MB | 2.8 s | 26 elements on the live screen (Text, Utility Button, App Icon, Link) |
| GPA 640 | +227 MB | 0.55 s | 26 "icon" boxes, mostly around text |
| carwin 640 | +28 MB | 0.1 s | 21 unlabeled "element" boxes |

- No detector is worth shipping: the one with semantics is 2.8 s and 624 MB; the light ones give unlabeled boxes.
- RapidOCR as a full second pass is too slow, but recognition-only on the boxes Windows OCR already found is 20 to 30 ms a line and fixes the lookalike problem. That is the shape to ship: a `verify` pass over flagged or selected lines.
- onnxruntime must be imported before any `winrt` module or the process crashes; RapidOCR confidences are above 0.9 on nearly everything, so they are not a useful signal on their own.

### verify (v0.5.0)

`eval_ocr.py` `+ver` rows, 1.0x read plus verify=flagged:

| screen | px | CER before / after | lines exact before / after | lookalike misses before / after | re-read |
|---|---|---|---|---|---|
| terminal | 16 | .060 / .020 | .43 / .57 | 5 / 0 | 5 lines, 357 ms |
| chat | 16 | .040 / .005 | .47 / .76 | 10 / 0 | 11 lines, 667 ms |
| mail | 13 | .028 / .001 | .71 / .95 | 4 / 0 | 10 lines, 507 ms |
| ids | 16 | .101 / .009 | .00 / .75 | 9 / 1 | 4 lines, 169 ms |

- mail at 20 px stays at .546: that is the reading-order problem, not misreads.
- Live through the MCP server on the fake `ids` window: plain read 248 ms; first verify 3.1 s (RapidOCR loads); later verify adds ~10 ms for 4 lines. Memory with the extra installed: 108 MB idle (onnxruntime preloaded), 185 MB peak after verify. Without the extra nothing changes (80 MB).
- `jharokha.win` imports onnxruntime when it is installed, because loading it after WinRT segfaults the process (reproduced: Windows OCR, then `import onnxruntime`, then a RapidOCR call).
- The extra is ~240 MB on disk: OpenCV 113, onnxruntime 43, rapidocr (models) 32, numpy 22. Running the rec model on onnxruntime directly would drop OpenCV and rapidocr, at the cost of shipping the model and the CTC decode ourselves.

### Panels (v0.6.0)

Live session, one OCR frame each, panels found: with the first rule (columns wherever a 3-line-height gap runs the full height) 19, mostly toolbar buttons and labels split into columns; requiring a region to be at least 3 lines tall before it can split into columns, 13; merging stacked blocks of the same width, 5 where pairwise merging gave 9 on the same frames (the body was 5 full-width slices). Eval CER and lines exact unchanged on all synthetic screens. `jharokha_layout` 360 ms, a panel read ~210 ms on the fake windows.

### Terminal grid (v0.12.0)

Synthetic dark Consolas terminal, real Windows OCR, `tests/eval_ocr.py terminal`, CER / lines exact (the last truth line holds a `_` cursor, so 6 of 7 is the most):

| px | reading order | grid | verify=flagged |
|---|---|---|---|
| 13 | .146 / 0 | .151 / 0 | .080 / .29 |
| 16 | .060 / .43 | .019 / .43 | .020 / .57 |
| 20 | .046 / .43 | .010 / .71 | .027 / .57 |

Grid costs no OCR time. What it fixes: pieces of a row split and spaced apart (`dist/index . html`), table columns, dotted zeros inside numbers (`e.46`, `142.e7`). What is left are single-character misreads (`B3`→`83`, a lone `0`→`e`), which is verify's job. Inverting a dark screen before OCR changes nothing (same CER to three decimals on every screen): Windows OCR binarizes on its own.

## Open

- No shared convention for marking untrusted tool output was found; ours is the `<screen_text>` wrapper.

## Release

By hand: bump `pyproject.toml`, add the CHANGELOG section, commit `vX.Y.Z — summary`, tag `vX.Y.Z`, push.

## Secrets

CI runs gitleaks. Locally, install [gitleaks](https://github.com/gitleaks/gitleaks) and `pre-commit install` to run it before each commit.
