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
import secrets
import sys
import threading
import time
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

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 10_000
MAX_TEXT_CHARS = 500
MAX_LOGIN_ATTEMPTS = 3
LOCKOUT_SECONDS = 30
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"


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
            recognizer.SetGrammar(json.dumps(["titan"]))
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
                if heard != "titan":
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
                    reply, _ = core.titan_handle(command)
                    voice.say(reply)
                with self._lock:
                    self._status = "LISTENING FOR TITAN" if dashboard_opened else "LISTENING FOR TITAN · BROWSER DID NOT OPEN"
                recognizer = core.KaldiRecognizer(model, core.SAMPLE_RATE)
                recognizer.SetGrammar(json.dumps(["titan"]))
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
    stats["uptime_hours"] = None
    stats["ram_used_gb"] = None
    stats["ram_total_gb"] = None
    if core.psutil is not None:
        mem = core.psutil.virtual_memory()
        stats["ram_used_gb"] = round((mem.total - mem.available) / 1024 ** 3, 1)
        stats["ram_total_gb"] = round(mem.total / 1024 ** 3, 1)
        stats["uptime_hours"] = round((time.time() - core.psutil.boot_time()) / 3600, 1)
    return 200, stats


def api_chat(body):
    text = str(body.get("text", "")).strip()
    if not text:
        return 400, {"error": "Empty message."}
    if len(text) > MAX_TEXT_CHARS:
        return 400, {"error": f"Message too long (max {MAX_TEXT_CHARS} characters)."}
    reply, keep_running = core.titan_handle(text)
    route = core.match_local(text)
    if core.is_code_generation_request(text):
        speech = "I've put the code example on screen."
    elif core.persona.is_capabilities_request(text) or core.persona.is_optimization_request(text):
        speech = "I've put my capabilities and suggestions on screen."
    elif core.is_learning_request(text):
        speech = "I've put the feedback or evaluation result on screen."
    elif route and route[0] in {"diagnose_system", "diagnose_titan"}:
        speech = "I've put the diagnosis on screen."
    else:
        speech = reply
    return 200, {"reply": reply, "speech": speech, "keep_running": keep_running}


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
        return self._send(404, {"error": "Not found."})

    def do_POST(self):
        if not self._host_ok() or not self._origin_ok():
            return self._send(403, {"error": "Blocked."})
        path = self.path.split("?")[0]
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
