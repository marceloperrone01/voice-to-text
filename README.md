
## What this is

A single-file push-to-talk dictation daemon for Ubuntu 24.04 / X11. Hold Right Ctrl → audio is recorded → release → faster-whisper transcribes → xdotool types the result into the focused window. Supports pt-BR and en-US (auto-detected per utterance).

## Running and managing the service

```bash
# Service lifecycle
systemctl --user status live-dictation
systemctl --user restart live-dictation
systemctl --user stop live-dictation
systemctl --user start live-dictation

# Live logs
journalctl --user -u live-dictation -f

# Run directly (for debugging — kills the service first to avoid mic conflicts)
systemctl --user stop live-dictation
.venv/bin/python dictate.py
```

## Installation (first time or after dependency changes)

```bash
./install.sh
```

This installs apt packages (`xdotool`, `portaudio19-dev`, `libnotify-bin`), creates `.venv`, installs Python deps from `requirements.txt`, downloads the Whisper `small` model (~460 MB to `~/.cache/huggingface/`), and enables the systemd user service.

## Python environment

All Python work uses `.venv`. There is no `pip install` to the system Python.

```bash
.venv/bin/pip install -r requirements.txt   # sync deps
.venv/bin/pip list                          # inspect installed packages
```

## Architecture (`dictate.py`)

Three concurrent execution contexts:

| Context | Role |
|---|---|
| Main thread | Starts everything; runs `pynput.Listener.join()` (blocks until shutdown) |
| `sd.InputStream` callback | Fires per audio block; appends to `audio_frames` while `recording` event is set |
| `transcription-worker` thread | Blocks on `transcription_queue`; calls `faster_whisper` then `xdotool` |

**Data flow:** `on_press(Key.ctrl_r)` sets `recording` → callback accumulates PCM frames → `on_release(Key.ctrl_r)` clears `recording`, snapshots frames, pushes `np.ndarray` to `transcription_queue` → worker pops, calls `model.transcribe()`, calls `inject_text()` → `xdotool type`.

**Key globals:** `recording` (threading.Event), `audio_lock` (Lock), `audio_frames` (list of ndarray), `transcription_queue` (Queue).

**Hardware detection:** `detect_device()` tries `ctranslate2.get_cuda_device_count()`; falls back to CPU `int8` if CUDA is unavailable. Model is loaded once at startup.

**xdotool delay:** `XDOTOOL_DELAY_MS = "20"` is required for pt-BR accented characters (ã, ê, ç) to land correctly. `PRE_TYPE_SLEEP = 0.15` lets X11 process the Ctrl key-up before xdotool fires.

**Minimum audio gate:** `MIN_AUDIO_SAMPLES = 8000` (0.5 s at 16 kHz) prevents hallucination on accidental brief presses.

## systemd service notes

`live-dictation.service` is a **user** service (`systemctl --user`), not system-wide. It depends on `graphical-session.target` and passes `DISPLAY`, `XAUTHORITY`, `DBUS_SESSION_BUS_ADDRESS`, and `XDG_RUNTIME_DIR` from the user environment. The `LD_LIBRARY_PATH` in the service file points to NVIDIA CUDA libs inside `.venv` — update it if the venv is recreated or CUDA packages are upgraded.

Memory is capped at `MemoryMax=800M` in the service unit.
