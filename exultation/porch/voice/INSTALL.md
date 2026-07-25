# Installing the voice helper (optional — the porch works without it)

The porch speaks without any install (browser voices, or audio the speaker
attaches). This helper upgrades what YOUR machine hears to real local models.
Windows-focused; Linux/mac differ only in the venv and autostart steps.

## 1. Get the files

Copy this `voice/` directory anywhere local (it is self-contained).

## 2. Python env — the pins are load-bearing, every trap below was paid for

```
py -3.11 -m venv .venv          # 3.11: onnxruntime has no 3.14 wheels
.venv\Scripts\pip install piper-tts kokoro-onnx onnxruntime
# GPU (optional, NVIDIA): kokoro 480ms -> ~150ms. EXACT versions:
.venv\Scripts\pip install onnxruntime-gpu==1.22.0 nvidia-cudnn-cu12==9.8.0.87
#   - onnxruntime-gpu 1.27 wants CUDA 13; the cu13 pip pkgs have NO Windows wheels
#   - cuDNN 9.25 is too new for ort 1.22 (CUDNN_BACKEND_API_FAILED at first Conv,
#     then ort SILENTLY falls back to CPU — the server asserts the real provider)
```

## 3. Models (~600MB, one-time)

```
.venv\Scripts\python fetch_models.py
```
(Kokoro ONNX comes from GitHub `thewh1teagle/kokoro-onnx` tag `model-files-v1.0`
— NOT from HF hexgrad, which ships only `.pth`.)

## 4. Run + verify

```
run_tts_models.cmd              # or: .venv\Scripts\python tts_server.py
```
Then open **http://127.0.0.1:8124/** — a human status page. Green "Running"
= working. It shows the device (cuda/cpu), voices loaded, uptime, recent log.

If anything is wrong: double-click **`CHECK VOICE.cmd`** — it works when the
server is DOWN (starts it; on failure re-runs foreground to show the traceback).
`tts_server.log` gets one `SELF-TEST PASS/PARTIAL/FAIL` line every boot.

## 5. Autostart (optional)

Scheduled task at logon running `pythonw.exe tts_server.py` in this directory
(no console window). Set: restart 3x on failure, no time limit, IgnoreNew,
StopOnIdleEnd OFF (default-on; kills the service mid-idle). The server is
pythonw-safe (logs to file; stderr may not exist).

## How you know it's working, day to day

- The porch page shows a quiet **tier line**: `voice: local · kokoro · cuda` =
  this helper. `browser voice — local helper unreachable` = it's down; check
  http://127.0.0.1:8124/ then `CHECK VOICE.cmd`.
- The status page at :8124 answers ⇒ helper fine ⇒ problem is page-side.
- `TTS_DEVICE=cpu` env hands the GPU back to VR (kokoro returns to ~480ms);
  `cuda` errors loudly rather than degrading silently; default `auto`.
