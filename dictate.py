#!/usr/bin/env python3
"""live-dictation: push-to-talk voice dictation daemon for X11."""

import os
import queue
import signal
import subprocess
import sys
import threading
import time

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel
from pynput.keyboard import Key, Listener

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
MODEL_SIZE = "small"
MIN_AUDIO_SAMPLES = 8000   # 0.5 s — prevents hallucination on silence
PRE_TYPE_SLEEP = 0.15      # let X11 process the Ctrl key-up before xdotool fires

# xdotool WM_CLASS values for terminal emulators — these use Ctrl+Shift+V to paste
TERMINAL_CLASSES = frozenset({
    "gnome-terminal", "xterm", "konsole", "alacritty",
    "kitty", "tilix", "terminator", "urxvt", "foot",
    "org.wezfurlong.wezterm",
})


def detect_device() -> tuple[str, str]:
    """Return (device, compute_type) based on available hardware."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            # return "cuda", "int8_float16"
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"

recording = threading.Event()
audio_lock = threading.Lock()
audio_frames: list[np.ndarray] = []
transcription_queue: queue.Queue = queue.Queue()

model: WhisperModel = None
stream: sd.InputStream = None
listener: Listener = None
transcription_thread: threading.Thread = None


def load_model() -> WhisperModel:
    device, compute_type = detect_device()
    print(f"[live-dictation] Loading Whisper model (small/{compute_type} on {device})…", flush=True)
    kwargs = {"device": device, "compute_type": compute_type, "num_workers": 1}
    if device == "cpu":
        kwargs["cpu_threads"] = 0
    m = WhisperModel(MODEL_SIZE, **kwargs)
    print(f"[live-dictation] Model ready ({device}).", flush=True)
    return m


def audio_callback(
    indata: np.ndarray,
    frames: int,
    time_info,
    status: sd.CallbackFlags,
) -> None:
    if status:
        print(f"[live-dictation] Audio status: {status}", flush=True)
    if recording.is_set():
        with audio_lock:
            # indata.copy() is mandatory — PortAudio reuses the same buffer
            audio_frames.append(indata.copy())


def on_press(key: Key) -> None:
    if key == Key.ctrl_r and not recording.is_set():
        with audio_lock:
            audio_frames.clear()
        recording.set()
        print("[live-dictation] Recording…", flush=True)
        subprocess.Popen(
            ["notify-send", "-t", "1500", "-i", "audio-input-microphone",
             "live-dictation", "Recording…"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def on_release(key: Key) -> None:
    if key == Key.ctrl_r and recording.is_set():
        recording.clear()
        with audio_lock:
            frames_snapshot = list(audio_frames)
            audio_frames.clear()

        print("[live-dictation] Transcribing…", flush=True)
        subprocess.Popen(
            ["notify-send", "-t", "2000", "-i", "audio-input-microphone-muted",
             "live-dictation", "Transcribing…"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not frames_snapshot:
            return

        audio_flat = np.concatenate(frames_snapshot).flatten()

        if len(audio_flat) < MIN_AUDIO_SAMPLES:
            print("[live-dictation] Audio too short, skipping.", flush=True)
            return

        transcription_queue.put(audio_flat)


def _get_active_window(env: dict) -> tuple[str, str]:
    """Return (window_id, wm_class_lower) for the currently focused window."""
    try:
        win_id = subprocess.check_output(
            ["xdotool", "getwindowfocus"],
            env=env, timeout=2, stderr=subprocess.DEVNULL,
        ).decode().strip()
        cls = subprocess.check_output(
            ["xdotool", "getwindowclassname", win_id],
            env=env, timeout=2, stderr=subprocess.DEVNULL,
        ).decode().strip().lower()
        return win_id, cls
    except subprocess.CalledProcessError as exc:
        print(f"[live-dictation] getwindowfocus failed (code {exc.returncode})", flush=True)
        return "", ""
    except Exception as exc:
        print(f"[live-dictation] _get_active_window error: {exc}", flush=True)
        return "", ""


def inject_text(text: str) -> None:
    time.sleep(PRE_TYPE_SLEEP)
    env = {
        **os.environ,
        "DISPLAY": os.environ.get("DISPLAY", ":1"),
        "XAUTHORITY": os.environ.get(
            "XAUTHORITY",
            f"/run/user/{os.getuid()}/gdm/Xauthority",
        ),
    }
    try:
        win_id, cls = _get_active_window(env)
        is_term = cls in TERMINAL_CLASSES or cls.startswith("st-")
        paste_key = "ctrl+shift+v" if is_term else "ctrl+v"
        print(f"[live-dictation] window class={cls!r} is_term={is_term} paste_key={paste_key}", flush=True)

        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode(),
            check=True,
            timeout=5,
            env=env,
        )

        cmd = ["xdotool", "key", "--clearmodifiers"]
        if win_id:
            cmd += ["--window", win_id]
        cmd.append(paste_key)
        subprocess.run(cmd, check=True, timeout=5, env=env)
        print(f"[live-dictation] Injected: {text!r}", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"[live-dictation] inject failed: {exc}", flush=True)
    except FileNotFoundError as exc:
        missing = str(exc).split("'")[1] if "'" in str(exc) else "unknown"
        print(f"[live-dictation] {missing} not found — install with: sudo apt install xclip xdotool", flush=True)


def transcription_worker() -> None:
    while True:
        audio_flat = transcription_queue.get()
        if audio_flat is None:
            break

        try:
            segments, info = model.transcribe(
                audio_flat,
                language=None,
                task="transcribe",
                beam_size=2,
                best_of=1,
                temperature=0.0,
                vad_filter=True,
                vad_parameters={
                    "min_silence_duration_ms": 300,
                    "speech_pad_ms": 200,
                },
            )
            # segments is a lazy generator — iterate to drive inference
            text = "".join(seg.text for seg in segments).strip()

            print(
                f"[live-dictation] [{info.language} {info.language_probability:.0%}] {text!r}",
                flush=True,
            )

            if text:
                inject_text(text)
        except Exception as exc:
            print(f"[live-dictation] Transcription error: {exc}", flush=True)
        finally:
            transcription_queue.task_done()


def main() -> None:
    global model, stream, listener, transcription_thread

    print("[live-dictation] Starting up…", flush=True)

    model = load_model()

    transcription_thread = threading.Thread(
        target=transcription_worker,
        name="transcription-worker",
        daemon=False,
    )
    transcription_thread.start()

    try:
        stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=DTYPE,
            callback=audio_callback,
            blocksize=0,
        )
    except sd.PortAudioError as exc:
        print(f"[live-dictation] Failed to open audio stream: {exc}", flush=True)
        print("[live-dictation] Check that a microphone is connected and accessible.", flush=True)
        transcription_queue.put(None)
        transcription_thread.join(timeout=5)
        sys.exit(1)

    stream.start()
    print("[live-dictation] Audio stream open. Hold Right Ctrl to dictate.", flush=True)

    def _handle_shutdown(sig, frame):
        print(f"[live-dictation] Received signal {sig}, shutting down…", flush=True)
        recording.clear()
        stream.stop()
        stream.close()
        if listener is not None:
            listener.stop()
        transcription_queue.put(None)
        transcription_thread.join(timeout=10)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    listener = Listener(on_press=on_press, on_release=on_release)
    listener.start()

    print("[live-dictation] Daemon ready.", flush=True)
    listener.join()


if __name__ == "__main__":
    main()
