#!/usr/bin/env python3
"""
TITAN VOICE - offline neural speech with Piper
==============================================
Turns Titan's replies into speech using a local Piper voice model
(voices/<name>.onnx + voices/<name>.onnx.json). Falls back to pyttsx3, then to
text-only, so Titan never crashes because speech is unavailable.

  speaker = Speaker()
  speaker.say("Hello")            # speak and wait
  speaker.say_async("Sentence.")  # queue a sentence (used while a reply streams in)
  speaker.wait()                  # wait for queued speech to finish
  wav_bytes("Hello")              # WAV audio for the dashboard (/api/tts)

Change the voice with the TITAN_VOICE environment variable or PIPER_VOICE below.
"""
import io
import os
import queue
import threading
import wave
from pathlib import Path

try:
    from piper import PiperVoice
except Exception:          # ImportError, or onnxruntime failing to load
    PiperVoice = None

try:
    import numpy as np
    import sounddevice as sd
except Exception:
    np = sd = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None


VOICES_DIR = Path(__file__).resolve().parent / "voices"
PIPER_VOICE = os.environ.get("TITAN_VOICE", "en_GB-alan-medium")   # British butler
MAX_SPEECH_CHARS = 1200

_voice = None
_voice_error = None
_voice_lock = threading.Lock()          # one synthesis at a time (dashboard + wake word share it)


def _load_voice():
    """Load the Piper voice once, on first use. Returns None if unavailable."""
    global _voice, _voice_error
    if _voice is not None or _voice_error is not None:
        return _voice
    model = VOICES_DIR / f"{PIPER_VOICE}.onnx"
    if PiperVoice is None:
        _voice_error = "piper-tts is not installed"
    elif not model.is_file() or not model.with_suffix(".onnx.json").is_file():
        _voice_error = f"voice files for {PIPER_VOICE} not found in {VOICES_DIR}"
    else:
        try:
            _voice = PiperVoice.load(str(model))
        except Exception as err:
            _voice_error = f"could not load {PIPER_VOICE}: {err}"
    if _voice_error:
        print(f"[notice] Piper voice unavailable ({_voice_error}).")
    return _voice


def piper_available():
    with _voice_lock:
        return _load_voice() is not None


def _synthesize(text):
    """Return (int16 numpy audio, sample_rate) or (None, None)."""
    text = " ".join(str(text).split())[:MAX_SPEECH_CHARS]
    if not text or np is None:
        return None, None
    with _voice_lock:
        voice = _load_voice()
        if voice is None:
            return None, None
        chunks = list(voice.synthesize(text))
    if not chunks:
        return None, None
    return np.concatenate([chunk.audio_int16_array for chunk in chunks]), chunks[0].sample_rate


def wav_bytes(text):
    """WAV file bytes for the given text, or None if Piper is unavailable."""
    audio, rate = _synthesize(text)
    if audio is None:
        return None
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(audio.tobytes())
    return buffer.getvalue()


class Speaker:
    """Speaks through the laptop speakers. Piper first, then pyttsx3, then silence."""

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._fallback = None
        self._queue = queue.Queue()
        self._thread = None
        if enabled and not piper_available():
            self._fallback = self._make_pyttsx3()

    @staticmethod
    def _make_pyttsx3():
        if pyttsx3 is None:
            return None
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 165)
            return engine
        except Exception:
            return None

    def _play(self, text):
        if not self.enabled or not str(text).strip():
            return
        try:
            audio, rate = _synthesize(text)
            if audio is not None and sd is not None:
                sd.play(audio, rate)
                sd.wait()
                return
            if self._fallback is not None:
                self._fallback.say(text)
                self._fallback.runAndWait()
        except Exception as err:
            print(f"[notice] Speech failed ({err}) - switching to text only.")
            self.enabled = False

    def _worker(self):
        while True:
            text = self._queue.get()
            try:
                self._play(text)
            finally:
                self._queue.task_done()

    def say_async(self, text):
        """Queue text to be spoken in order, without waiting."""
        if not self.enabled:
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, name="titan-speaker", daemon=True)
            self._thread.start()
        self._queue.put(text)

    def wait(self):
        """Block until everything queued has been spoken."""
        if self._thread is not None:
            self._queue.join()

    def say(self, text):
        self.say_async(text)
        self.wait()
