#!/usr/bin/env python3
"""
TITAN DASHBOARD - SERVER
========================
Starts a small local web server and opens the JARVIS-style HUD in your browser.
It uses your existing brain in ../T.v1.1.py (titan_handle, get_stats, Ears ...),
so every command you add to T.v1.1.py shows up here automatically.

Run (from the project folder, with the .venv active in VS Code):
    python dashboard/server.py

Options:
    --port 8765      change the port
    --no-browser     don't open the browser automatically

Safety: the server only listens on 127.0.0.1 (this computer), checks the Host
header, and every command needs the token you get by passing the identity check.
Nothing here needs the internet.
"""

# =============================================================================
# SECTION 0: IMPORTS
# =============================================================================
import argparse
import importlib.util
import json
import queue
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


# =============================================================================
# SECTION 1: CONFIG
# =============================================================================
HERE = Path(__file__).resolve().parent          # .../dashboard
ROOT = HERE.parent                              # project folder
CORE_FILE = ROOT / "T.v1.1.py"                  # the brain
INDEX_FILE = HERE / "index.html"                # the HUD page
VENDOR_DIR = HERE / "vendor"                    # three.js, kept locally so Titan works offline

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 10_000
MAX_TEXT_CHARS = 500
MAX_SPEECH_CHARS = 1500
MAX_AUDIO_SECONDS = 20                          # phone recordings for /api/transcribe
MAX_AUDIO_BYTES = MAX_AUDIO_SECONDS * 16000 * 2  # 16 kHz, 16-bit mono PCM

# "Add to Home Screen" support, so Titan opens like an app on the phone.
MANIFEST = {
    "name": "TITAN", "short_name": "TITAN", "start_url": "/", "scope": "/",
    "display": "standalone", "orientation": "portrait",
    "background_color": "#060203", "theme_color": "#060203",
    "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
}
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<rect width="512" height="512" rx="96" fill="#060203"/>'
    '<circle cx="256" cy="256" r="170" fill="none" stroke="#ff3b30" stroke-opacity=".35" stroke-width="10"/>'
    '<circle cx="256" cy="256" r="120" fill="#1a0707" stroke="#ff3b30" stroke-width="10"/>'
    '<path d="M256 76a180 180 0 0 1 156 90" fill="none" stroke="#ff3b30" stroke-width="16" stroke-linecap="round"/>'
    '<path d="M256 436a180 180 0 0 1-156-90" fill="none" stroke="#ff3b30" stroke-width="16" stroke-linecap="round"/>'
    '<text x="256" y="276" text-anchor="middle" font-family="Consolas,monospace" font-size="62" '
    'letter-spacing="10" fill="#ffe6e2">TITAN</text></svg>'
)
MAX_LOGIN_ATTEMPTS = 3
LOCKOUT_SECONDS = 30
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"   # not "localhost": avoids a 2s IPv6 delay


def is_wake_word(text):
    """Match Titan as a recognized word, not an approximate substring."""
    return "titan" in re.findall(r"[a-z0-9]+", str(text).casefold())


# =============================================================================
# SECTION 2: LOAD THE BRAIN (T.v1.1.py)
# =============================================================================
def load_core():
    """Import T.v1.1.py (the dots in the name mean a normal import won't work)."""
    if not CORE_FILE.is_file():
        print(f"ERROR: cannot find {CORE_FILE}")
        print("Put server.py in the 'dashboard' folder next to T.v1.1.py's folder.")
        sys.exit(1)
    spec = importlib.util.spec_from_file_location("titan_core", CORE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load_core()


# =============================================================================
# SECTION 3: SERVER STATE (tokens, lockout, microphone)
# =============================================================================
class State:
    def __init__(self):
        self.tokens = set()
        self.failed = 0
        self.locked_until = 0.0
        self.ears = None                   # created the first time you press MIC
        self.ears_lock = threading.Lock()
        self.ollama_ok = False
        self.ollama_checked = 0.0
        self.lock = threading.Lock()


STATE = State()


class WakeWordAssistant:
    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._status = "OFF"

    def status(self):
        with self._lock:
            return self._status

    def wait_for_first_login(self):
        with self._lock:
            self._status = "WAITING FOR FIRST LOGIN"

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return not self._stop.is_set()
            self._stop.clear()
            self._status = "LOADING OFFLINE SPEECH MODEL"
            self._thread = threading.Thread(target=self._run, name="titan-wake-word", daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()
        with self._lock:
            self._status = "STOPPING"
            thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if thread is None or not thread.is_alive():
            with self._lock:
                self._status = "OFF"

    def _open_dashboard(self):
        url = f"http://127.0.0.1:{DEFAULT_PORT}/"
        try:
            opened = webbrowser.open(url, new=0)
        except (OSError, webbrowser.Error) as err:
            with self._lock:
                self._status = f"BROWSER DID NOT OPEN: {err}"
            return False
        if not opened:
            with self._lock:
                self._status = "BROWSER DID NOT OPEN"
        return opened

    def _run(self):
        stream = None
        try:
            if core.sd is None or core.Model is None or core.KaldiRecognizer is None:
                raise RuntimeError("Vosk or sounddevice is unavailable.")
            if not core.VOSK_MODEL_DIR.is_dir():
                raise RuntimeError(f"Offline Vosk model not found at {core.VOSK_MODEL_DIR}.")

            core.SetLogLevel(-1)
            model = core.Model(str(core.VOSK_MODEL_DIR))
            recognizer = core.KaldiRecognizer(model, core.SAMPLE_RATE)
            audio = queue.Queue(maxsize=32)

            def on_audio(indata, frames, time_info, status):
                try:
                    audio.put_nowait(bytes(indata))
                except queue.Full:
                    try:
                        audio.get_nowait()
                    except queue.Empty:
                        pass

            stream = core.sd.RawInputStream(
                samplerate=core.SAMPLE_RATE,
                blocksize=4000,
                dtype="int16",
                channels=1,
                callback=on_audio,
            )
            stream.start()
            with self._lock:
                self._status = "LISTENING FOR TITAN"
            voice = core.Voice()
            while not self._stop.is_set():
                try:
                    data = audio.get(timeout=0.5)
                except queue.Empty:
                    continue
                if not recognizer.AcceptWaveform(data):
                    continue
                heard = json.loads(recognizer.Result()).get("text", "").strip()
                if not is_wake_word(heard):
                    continue

                while not audio.empty():
                    try:
                        audio.get_nowait()
                    except queue.Empty:
                        break
                dashboard_opened = self._open_dashboard()
                with self._lock:
                    self._status = "LISTENING FOR COMMAND"
                voice.say("Yes?")

                while not audio.empty():
                    try:
                        audio.get_nowait()
                    except queue.Empty:
                        break
                command_recognizer = core.KaldiRecognizer(model, core.SAMPLE_RATE)
                deadline = time.monotonic() + core.LISTEN_SECONDS
                command = ""
                while time.monotonic() < deadline and not self._stop.is_set():
                    try:
                        data = audio.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if command_recognizer.AcceptWaveform(data):
                        command = json.loads(command_recognizer.Result()).get("text", "").strip()
                        if command:
                            break
                if not command:
                    command = json.loads(command_recognizer.FinalResult()).get("text", "").strip()
                if command:
                    core.respond(voice, command)    # speaks sentence by sentence as the reply streams
                with self._lock:
                    self._status = "LISTENING FOR TITAN" if dashboard_opened else "LISTENING FOR TITAN · BROWSER DID NOT OPEN"
                recognizer = core.KaldiRecognizer(model, core.SAMPLE_RATE)
                while not audio.empty():
                    try:
                        audio.get_nowait()
                    except queue.Empty:
                        break
        except Exception as err:
            with self._lock:
                self._status = f"WAKE WORD ERROR: {err}"
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception as err:
                    with self._lock:
                        self._status = f"WAKE WORD ERROR: {err}"
            if self._stop.is_set():
                with self._lock:
                    self._status = "OFF"


STATE.wake_word = WakeWordAssistant()
STATE.wake_word_enabled = False


# =============================================================================
# SECTION 4: API FUNCTIONS (each returns (http_status, dict))
# =============================================================================
def api_status():
    """Public: tells the page whether this is a first-time setup or a login."""
    remaining = max(0, int(STATE.locked_until - time.time()))
    return 200, {
        "enroll": not core.load_profiles(),
        "locked_for": remaining,
        "wake_word": STATE.wake_word.status(),
        "wake_word_enabled": STATE.wake_word_enabled,
    }


def api_login(body):
    with STATE.lock:
        now = time.time()
        if now < STATE.locked_until:
            wait = int(STATE.locked_until - now) + 1
            return 429, {"error": f"Locked. Try again in {wait}s."}

        name = str(body.get("name", "")).strip()
        age = core.parse_age(str(body.get("age", "")))
        if not name or len(name) > 40 or age is None or not 1 <= age <= 120:
            return 400, {"error": "Enter a name and a valid age."}

        profiles = core.load_profiles()
        if not profiles:                                   # first run: enrol
            user = core.save_profile(name, age)
        else:
            matched = next(
                (profile for profile in profiles
                 if name.casefold() == profile["name"].casefold() and age == profile["age"]),
                None,
            )
            if matched is None:
                STATE.failed += 1
                if STATE.failed >= MAX_LOGIN_ATTEMPTS:
                    STATE.failed = 0
                    STATE.locked_until = time.time() + LOCKOUT_SECONDS
                    return 429, {"error": f"Identity not recognised. Locked for {LOCKOUT_SECONDS}s."}
                return 401, {"error": f"Identity not recognised. Attempt {STATE.failed} of {MAX_ID_ATTEMPTS}."}
            user = matched["name"]

        STATE.failed = 0
        core.set_user_name(user)
        if STATE.wake_word_enabled:
            STATE.wake_word.start()
        token = secrets.token_hex(16)
        STATE.tokens.add(token)
        return 200, {"token": token, "name": user, "greeting": core.greeting()}


def check_ollama():
    """Is the local AI model server answering? (cached for 10 seconds)"""
    if time.time() - STATE.ollama_checked < 10:
        return STATE.ollama_ok
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=0.7) as response:
            STATE.ollama_ok = response.status == 200
    except Exception:
        STATE.ollama_ok = False
    STATE.ollama_checked = time.time()
    return STATE.ollama_ok


def api_stats():
    stats = core.get_stats()
    now = datetime.now()
    stats["time"] = now.strftime("%H:%M:%S")
    stats["date"] = now.strftime("%a %d %b %Y").upper()
    stats["ollama"] = check_ollama()
    stats["wake_word"] = STATE.wake_word.status()
    stats["wake_word_enabled"] = STATE.wake_word_enabled
    stats["model"] = core.OLLAMA_MODEL
    stats["voice"] = core.voice_out.PIPER_VOICE
    auto = core.autonomy.settings()
    stats["autonomy"] = {
        "mode": auto["mode"], "self_check": auto["self_check"], "watchdog": auto["watchdog_on"],
        "briefing": auto["briefing_time"] if auto["briefing_on"] else "off",
        "goals": [{"id": g["id"], "time": g["time"], "task": g["task"]} for g in auto["goals"]],
        "routines": sorted(auto["routines"]), "suggestions": len(auto["suggestions"]),
    }
    stats["uptime_hours"] = None
    stats["ram_used_gb"] = None
    stats["ram_total_gb"] = None
    if core.psutil is not None:
        mem = core.psutil.virtual_memory()
        stats["ram_used_gb"] = round((mem.total - mem.available) / 1024 ** 3, 1)
        stats["ram_total_gb"] = round(mem.total / 1024 ** 3, 1)
        stats["uptime_hours"] = round((time.time() - core.psutil.boot_time()) / 3600, 1)
    return 200, stats


def _chat_text(body):
    """Return (text, None) or (None, (status, error_dict))."""
    text = str(body.get("text", "")).strip()
    if not text:
        return None, (400, {"error": "Empty message."})
    if len(text) > MAX_TEXT_CHARS:
        return None, (400, {"error": f"Message too long (max {MAX_TEXT_CHARS} characters)."})
    return text, None


def speech_for(text, reply):
    """What Titan should say aloud for a reply (long on-screen results get a short line)."""
    route = core.match_local(text)
    if core.is_code_generation_request(text):
        return "I've put the code example on screen."
    if core.persona.is_capabilities_request(text) or core.persona.is_optimization_request(text):
        return "I've put my capabilities and suggestions on screen."
    if core.is_learning_request(text):
        return "I've put the feedback or evaluation result on screen."
    if route and route[0] in {"diagnose_system", "diagnose_titan"}:
        return "I've put the diagnosis on screen."
    return reply


def api_chat(body):
    text, error = _chat_text(body)
    if error:
        return error
    reply, keep_running = core.titan_handle(text)
    return 200, {"reply": reply, "speech": speech_for(text, reply), "keep_running": keep_running,
                 "ui": core.garage.take_ui()}


def api_tts(body):
    """Piper speech for the page, as WAV bytes (or an error dict)."""
    text = str(body.get("text", "")).strip()
    if not text or len(text) > MAX_SPEECH_CHARS:
        return 400, {"error": "Bad speech text."}
    audio = core.voice_out.wav_bytes(text)
    if audio is None:
        return 503, {"error": "Piper voice unavailable."}
    return 200, audio


def api_garage():
    return 200, {"project": core.garage.project(), "models": core.garage.list_models(),
                 "assets": core.garage.list_assets()}


MAX_OVERRIDE_BYTES = 512 * 1024                 # part mappings for big models can be large


def api_garage_model(model_id):
    spec = core.garage.get_model(model_id)
    if spec is None:
        return 404, {"error": "No such model."}
    return 200, spec


def api_memories():
    """'What I know about you': the signed-in user's saved memories."""
    try:
        return 200, {"memories": core.persona.saved_memories()}
    except Exception as err:
        return 200, {"memories": [], "error": str(err)}


def api_forget_memory(body):
    text = str(body.get("text", ""))
    if not text or len(text) > 400:
        return 400, {"error": "Which memory?"}
    try:
        removed = core.persona.forget_memory_item(text)
    except Exception as err:
        return 500, {"error": f"Could not update memories: {err}"}
    return 200, {"removed": removed}


def api_alerts():
    return 200, {"alerts": core.pop_alerts()}


def api_wake_word(body):
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return 400, {"error": "Wake-word enabled must be true or false."}
    if enabled:
        STATE.wake_word_enabled = True
        if not STATE.wake_word.start():
            STATE.wake_word_enabled = False
            return 409, {"error": "Wake-word listener is still stopping. Try again in a moment."}
    else:
        STATE.wake_word_enabled = False
        STATE.wake_word.stop()
    return 200, {"enabled": STATE.wake_word_enabled, "status": STATE.wake_word.status()}


_vosk_model = None
_vosk_lock = threading.Lock()


def api_transcribe(pcm):
    """Turn raw 16 kHz 16-bit mono PCM recorded on the phone into text, offline."""
    global _vosk_model
    if core.Model is None or core.KaldiRecognizer is None:
        return 503, {"error": "Offline speech recognition (Vosk) is not installed."}
    if not core.VOSK_MODEL_DIR.is_dir():
        return 503, {"error": "The offline Vosk speech model is missing."}
    with _vosk_lock:
        if _vosk_model is None:
            core.SetLogLevel(-1)
            _vosk_model = core.Model(str(core.VOSK_MODEL_DIR))
        recognizer = core.KaldiRecognizer(_vosk_model, 16000)
        for start in range(0, len(pcm), 8000):
            recognizer.AcceptWaveform(pcm[start:start + 8000])
        text = json.loads(recognizer.FinalResult()).get("text", "").strip()
    return 200, {"text": text, "error": "" if text else "I did not hear anything."}


def api_listen():
    """Listen on the microphone (offline, Vosk) for one phrase."""
    with STATE.ears_lock:
        if STATE.ears is None:
            STATE.ears = core.Ears(text_only=False)
        if STATE.ears.mode != "voice":
            return 200, {"text": "", "error": "Voice input is not set up (no Vosk model or microphone)."}
        text = STATE.ears.listen()
    return 200, {"text": text, "error": "" if text else "I did not hear anything."}


# =============================================================================
# SECTION 5: HTTP HANDLER
# =============================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "TitanHUD"

    def log_message(self, fmt, *args):      # keep the terminal quiet
        pass

    # ---- helpers ----
    def _send(self, code, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _stream_chat(self, body):
        """Send a chat reply as newline-delimited JSON: {"sentence": ...} lines while
        the AI model writes, then one {"done": true, "reply", "speech", ...} line."""
        text, error = _chat_text(body)
        if error:
            return self._send(*error)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()          # HTTP/1.0: no length, the body ends when we close

        def emit(event):
            self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
            self.wfile.flush()

        streamed = []

        def on_sentence(sentence):
            streamed.append(sentence)
            emit({"sentence": sentence})

        try:
            reply, keep_running = core.titan_handle(text, on_sentence=on_sentence)
            emit({"done": True, "reply": reply, "speech": speech_for(text, reply),
                  "streamed": bool(streamed), "keep_running": keep_running,
                  "ui": core.garage.take_ui()})
        except (BrokenPipeError, ConnectionResetError):
            pass                    # the page was closed mid-reply
        except Exception as err:
            try:
                emit({"done": True, "error": f"Server error: {err}"})
            except OSError:
                pass

    def _host_ok(self):
        return self.headers.get("Host", "") in self.server.allowed_hosts

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin in self.server.allowed_origins

    def _authed(self):
        return self.headers.get("X-Titan-Token", "") in STATE.tokens

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            return None
        try:
            parsed = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, UnicodeDecodeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    # ---- routes ----
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "Bad host."})
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                return self._send(200, INDEX_FILE.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                return self._send(500, {"error": "index.html is missing from the dashboard folder."})
        if path == "/manifest.webmanifest":
            return self._send(200, json.dumps(MANIFEST).encode("utf-8"), "application/manifest+json")
        if path == "/icon.svg":
            return self._send(200, ICON_SVG.encode("utf-8"), "image/svg+xml")
        if path == "/garage.js":
            try:
                return self._send(200, (HERE / "garage.js").read_bytes(), "text/javascript; charset=utf-8")
            except OSError:
                return self._send(404, {"error": "Not found."})
        vendor = re.fullmatch(r"/vendor/([A-Za-z0-9_.-]+\.js)", path)
        if vendor:
            try:
                data = (VENDOR_DIR / vendor.group(1)).read_bytes()
            except OSError:
                return self._send(404, {"error": "Not found."})
            return self._send(200, data, "text/javascript; charset=utf-8")
        if path in ("/api/garage", "/api/garage/model", "/api/garage/asset"):
            if not self._authed():
                return self._send(401, {"error": "Not signed in."})
            if path == "/api/garage":
                return self._send(*api_garage())
            query = urllib.parse.parse_qs(self.path.partition("?")[2])
            if path == "/api/garage/asset":
                name = (query.get("name") or [""])[0]
                file = core.garage.asset_path(name)
                if file is None:
                    return self._send(404, {"error": "No such 3D file."})
                return self._send(200, file.read_bytes(), core.garage.ASSET_TYPES[file.suffix.lower()])
            return self._send(*api_garage_model((query.get("id") or [""])[0]))
        if path == "/api/status":
            return self._send(*api_status())
        if path == "/api/stats":
            if not self._authed():
                return self._send(401, {"error": "Not signed in."})
            return self._send(*api_stats())
        if path == "/api/alerts":
            if not self._authed():
                return self._send(401, {"error": "Not signed in."})
            return self._send(*api_alerts())
        if path == "/api/memories":
            if not self._authed():
                return self._send(401, {"error": "Not signed in."})
            return self._send(*api_memories())
        return self._send(404, {"error": "Not found."})

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._send(403, {"error": "Blocked."})
        path = self.path.split("?")[0]
        if path in ("/api/garage/upload", "/api/garage/override"):   # large bodies: own limits
            if not self._authed():
                return self._send(401, {"error": "Not signed in."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            limit = core.garage.MAX_ASSET_BYTES if path.endswith("upload") else MAX_OVERRIDE_BYTES
            if length <= 0 or length > limit:
                return self._send(413, {"error": f"Too large (limit {limit // 1024 ** 2 or 1} MB)."})
            data = self.rfile.read(length)
            if path.endswith("upload"):
                query = urllib.parse.parse_qs(self.path.partition("?")[2])
                name, error = core.garage.save_asset((query.get("name") or [""])[0], data)
                return self._send(400, {"error": error}) if error else self._send(200, {"name": name})
            try:
                body = json.loads(data)
            except ValueError:
                return self._send(400, {"error": "Bad request body."})
            if not isinstance(body, dict):
                return self._send(400, {"error": "Bad request body."})
            error = core.garage.set_override(str(body.get("model", "")), body)
            return self._send(400, {"error": error}) if error else self._send(200, {"ok": True})
        if path == "/api/transcribe":           # raw audio, not JSON
            if not self._authed():
                return self._send(401, {"error": "Not signed in."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_AUDIO_BYTES or length % 2:
                return self._send(400, {"error": f"Recording must be 16-bit audio under {MAX_AUDIO_SECONDS} seconds."})
            try:
                return self._send(*api_transcribe(self.rfile.read(length)))
            except Exception as err:
                return self._send(500, {"error": f"Speech recognition failed: {err}"})
        body = self._body()
        if body is None:
            return self._send(400, {"error": "Bad request body."})
        try:
            if path == "/api/login":
                return self._send(*api_login(body))
            if not self._authed():
                return self._send(401, {"error": "Not signed in."})
            if path == "/api/chat":
                return self._send(*api_chat(body))
            if path == "/api/chat-stream":
                return self._stream_chat(body)
            if path == "/api/tts":
                status, result = api_tts(body)
                if isinstance(result, bytes):
                    return self._send(status, result, "audio/wav")
                return self._send(status, result)
            if path == "/api/memories/forget":
                return self._send(*api_forget_memory(body))
            if path == "/api/wake-word":
                return self._send(*api_wake_word(body))
            if path == "/api/listen":
                return self._send(*api_listen())
            if path == "/api/logout":
                STATE.tokens.discard(self.headers.get("X-Titan-Token", ""))
                return self._send(200, {"ok": True})
        except Exception as err:            # never let one bad request kill the server
            return self._send(500, {"error": f"Server error: {err}"})
        return self._send(404, {"error": "Not found."})


# =============================================================================
# SECTION 6: MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Titan dashboard server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--wake-word", action="store_true", help="listen offline for the 'Titan' wake word")
    parser.add_argument("--tailnet-host", help="allow this exact Tailscale HTTPS hostname")
    args = parser.parse_args()

    tailnet_host = args.tailnet_host.rstrip(".").lower() if args.tailnet_host else None
    if tailnet_host and (
        not tailnet_host.endswith(".ts.net")
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for char in tailnet_host)
        or ".." in tailnet_host
    ):
        parser.error("--tailnet-host must be one MagicDNS hostname ending in .ts.net")

    core.start_services()
    STATE.wake_word_enabled = args.wake_word
    if args.wake_word:
        profiles = core.load_profiles()
        if profiles:
            core.set_user_name(profiles[0]["name"])
            STATE.wake_word.start()
        else:
            STATE.wake_word.wait_for_first_login()
    if core.psutil is not None:
        core.psutil.cpu_percent(interval=None)      # prime the CPU reading

    server = ThreadingHTTPServer((HOST, args.port), Handler)
    server.daemon_threads = True
    server.allowed_hosts = {f"127.0.0.1:{args.port}", f"localhost:{args.port}"}
    server.allowed_origins = {"http://127.0.0.1:" + str(args.port), "http://localhost:" + str(args.port)}
    if tailnet_host:
        server.allowed_hosts.update({tailnet_host, f"{tailnet_host}:443"})
        server.allowed_origins.update({f"https://{tailnet_host}", f"https://{tailnet_host}:443"})
        print(f" Private Tailscale access allowed at https://{tailnet_host}/")

    url = f"http://127.0.0.1:{args.port}/"
    print("=" * 56)
    print(" TITAN DASHBOARD online")
    print(f" Open:  {url}")
    print(" Stop:  press Ctrl+C in this terminal")
    print("=" * 56)
    if not args.no_browser and not args.wake_word:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
        STATE.wake_word.stop()
        if STATE.ears is not None:
            STATE.ears.close()


# =============================================================================
# SECTION 7: ARDUINO  >>> SAVED FOR LAST <<<
# =============================================================================

if __name__ == "__main__":
    main()
