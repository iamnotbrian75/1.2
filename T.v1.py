# =============================================================================
# SECTION 0: IMPORTS (optional libraries fail gracefully)
# =============================================================================
import argparse
import json
import os
import platform
import queue
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

try:
    import pyttsx3
except ImportError:
    pyttsx3 = None

try:
    import sounddevice as sd
    from vosk import KaldiRecognizer, Model, SetLogLevel
except Exception:  # ImportError, or OSError if PortAudio is missing
    sd = None
    KaldiRecognizer = Model = SetLogLevel = None


# =============================================================================
# SECTION 1: CONFIG
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
PROFILE_FILE = BASE_DIR / "titan_profile.json"
NOTES_FILE = BASE_DIR / "titan_notes.txt"
VOSK_MODEL_DIR = BASE_DIR / "vosk-model"

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_TIMEOUT = 60          # seconds

SAMPLE_RATE = 16000
LISTEN_SECONDS = 15          # max time to wait for one spoken phrase
MAX_ID_ATTEMPTS = 3
MAX_LIST_FILES = 15
NOTES_TO_READ = 5

EXIT_WORDS = {"exit", "quit", "goodbye", "shut down", "shutdown", "stop"}


# =============================================================================
# SECTION 2: VOICE OUT (text-to-speech)
# =============================================================================
class Voice:
    """Speaks and prints Titan's replies. Never crashes if TTS is missing."""

    def __init__(self, enabled=True):
        self.engine = None
        if not enabled:
            return
        if pyttsx3 is None:
            print("[notice] pyttsx3 not installed - text output only.")
            return
        try:
            self.engine = pyttsx3.init()
            self.engine.setProperty("rate", 175)
        except Exception as err:
            print(f"[notice] Text-to-speech unavailable ({err}) - text output only.")
            self.engine = None

    def say(self, text):
        print(f"TITAN: {text}")
        if self.engine is None:
            return
        try:
            self.engine.say(text)
            self.engine.runAndWait()
        except Exception as err:
            print(f"[notice] Speech failed ({err}) - switching to text only.")
            self.engine = None


# =============================================================================
# SECTION 3: VOICE IN (speech-to-text, with typing fallback)
# =============================================================================
class Ears:
    """Listens via Vosk if set up, otherwise reads typed input."""

    def __init__(self, text_only=False):
        self.mode = "text"
        self.recognizer = None
        self.stream = None
        self.audio_queue = queue.Queue()

        if text_only:
            return
        if sd is None or Model is None:
            print("[notice] vosk/sounddevice not available - using typed input.")
            return
        if not VOSK_MODEL_DIR.is_dir():
            print(f"[notice] No Vosk model at '{VOSK_MODEL_DIR}' - using typed input.")
            print("         Get one from https://alphacephei.com/vosk/models")
            return
        try:
            SetLogLevel(-1)
            model = Model(str(VOSK_MODEL_DIR))
            self.recognizer = KaldiRecognizer(model, SAMPLE_RATE)
            self.stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE, blocksize=4000, dtype="int16",
                channels=1, callback=self._audio_callback,
            )
            self.stream.start()
            self.mode = "voice"
        except Exception as err:
            print(f"[notice] Microphone/model setup failed ({err}) - using typed input.")
            self.recognizer = None
            self.stream = None
            self.mode = "text"

    def _audio_callback(self, indata, frames, time_info, status):
        self.audio_queue.put(bytes(indata))

    def _flush(self):
        """Drop audio captured while Titan was speaking."""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        if self.recognizer is not None:
            self.recognizer.Reset()

    def listen(self):
        """Return one phrase of text ('' if nothing heard)."""
        if self.mode == "voice":
            return self._listen_voice()
        return self._listen_text()

    def _listen_voice(self):
        self._flush()
        print("[listening...]")
        deadline = time.time() + LISTEN_SECONDS
        while time.time() < deadline:
            try:
                data = self.audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.recognizer.AcceptWaveform(data):
                text = json.loads(self.recognizer.Result()).get("text", "").strip()
                if text:
                    print(f"YOU (voice): {text}")
                    return text
        return ""

    def _listen_text(self):
        try:
            return input("YOU: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "exit"

    def close(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass


# =============================================================================
# SECTION 4: HELPERS (text cleanup, number words)
# =============================================================================
def normalise(text):
    """Lowercase and strip punctuation, for phrase matching."""
    return re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("'", "")).strip()


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def parse_age(text):
    """Turn '23', 'twenty three' or 'twenty-three' into an int (or None)."""
    match = re.search(r"\d+", text)
    if match:
        return int(match.group())
    total, found = 0, False
    for word in text.lower().replace("-", " ").split():
        if word in _TENS:
            total += _TENS[word]
            found = True
        elif word in _UNITS:
            total += _UNITS[word]
            found = True
    return total if found else None


# =============================================================================
# SECTION 5: IDENTITY CHECK (name + age)
# =============================================================================
def load_profile():
    try:
        return json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def save_profile(name, age):
    PROFILE_FILE.write_text(json.dumps({"name": name, "age": age}), encoding="utf-8")


def ask(voice, ears, question, tries=3):
    """Ask a question and return the answer ('' if nothing after a few tries)."""
    for _ in range(tries):
        voice.say(question)
        answer = ears.listen()
        if answer:
            return answer
    return ""


def identity_check(voice, ears):
    """Return the user's name if verified, else None."""
    profile = load_profile()

    # ---- first run: enrol the user ----
    if profile is None:
        voice.say("No profile found. Let's set you up.")
        name = ask(voice, ears, "What is your name?")
        age = parse_age(ask(voice, ears, "How old are you?"))
        if not name or age is None:
            return None
        name = name.strip().title()
        save_profile(name, age)
        voice.say(f"Profile saved for {name}.")
        return name

    # ---- later runs: verify ----
    for attempt in range(1, MAX_ID_ATTEMPTS + 1):
        name = ask(voice, ears, "State your name.")
        age = parse_age(ask(voice, ears, "State your age."))
        if name.strip().lower() == str(profile["name"]).lower() and age == profile["age"]:
            return profile["name"]
        voice.say(f"Identity not recognised. Attempt {attempt} of {MAX_ID_ATTEMPTS}.")
    return None


# =============================================================================
# SECTION 6: COMMANDS (each returns the text Titan will say)
# =============================================================================
def cmd_time(arg=""):
    now = datetime.now()
    return f"It is {now.strftime('%H:%M')} on {now.strftime('%A %d %B %Y')}."


def cmd_list_files(arg=""):
    try:
        items = sorted(p.name for p in BASE_DIR.iterdir() if p.is_file())
    except OSError as err:
        return f"I could not read the folder: {err}"
    if not items:
        return "There are no files in my folder."
    shown = items[:MAX_LIST_FILES]
    extra = len(items) - len(shown)
    text = f"{len(items)} files here: " + ", ".join(shown)
    return text + (f", and {extra} more." if extra > 0 else ".")


def cmd_cpu(arg=""):
    if psutil is None:
        return "psutil is not installed, so I cannot read the CPU. Run: pip install psutil"
    return f"CPU usage is {psutil.cpu_percent(interval=0.5):.0f} percent."


def cmd_ram(arg=""):
    if psutil is None:
        return "psutil is not installed, so I cannot read memory. Run: pip install psutil"
    mem = psutil.virtual_memory()
    used = (mem.total - mem.available) / 1024 ** 3
    total = mem.total / 1024 ** 3
    return f"RAM usage is {mem.percent:.0f} percent: {used:.1f} of {total:.1f} gigabytes."


def cmd_processes(arg=""):
    if psutil is None:
        return "psutil is not installed, so I cannot count processes. Run: pip install psutil"
    return f"There are {len(psutil.pids())} processes running."


def cmd_system_info(arg=""):
    parts = [f"{platform.system()} {platform.release()}", f"Python {platform.python_version()}"]
    if psutil is not None:
        parts.append(f"{psutil.cpu_count(logical=True)} logical cores")
        parts.append(f"{psutil.virtual_memory().total / 1024 ** 3:.0f} gigabytes of RAM")
        uptime_h = (time.time() - psutil.boot_time()) / 3600
        parts.append(f"up for {uptime_h:.1f} hours")
    return "System info: " + ", ".join(parts) + "."


def cmd_add_note(arg=""):
    arg = arg.strip()
    if not arg:
        return "What should the note say? Try: note buy milk."
    try:
        with NOTES_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {arg}\n")
    except OSError as err:
        return f"I could not save the note: {err}"
    return "Note saved."


def cmd_read_notes(arg=""):
    try:
        lines = [ln.strip() for ln in NOTES_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except FileNotFoundError:
        return "You have no notes yet."
    except OSError as err:
        return f"I could not read the notes: {err}"
    if not lines:
        return "You have no notes yet."
    latest = lines[-NOTES_TO_READ:]
    return f"Your last {len(latest)} notes: " + " | ".join(latest)


# command name -> function. The Ollama router may only choose from these.
COMMANDS = {
    "time": cmd_time,
    "list_files": cmd_list_files,
    "cpu": cmd_cpu,
    "ram": cmd_ram,
    "processes": cmd_processes,
    "system_info": cmd_system_info,
    "add_note": cmd_add_note,
    "read_notes": cmd_read_notes,
}


# =============================================================================
# SECTION 7: LOCAL PHRASE MATCHING (fast path, no AI needed)
# =============================================================================
# Checked in this order. First match wins.
PHRASES = [
    ("read_notes", ["read notes", "read my notes", "show notes", "show my notes", "my notes"]),
    ("list_files", ["list files", "list the files", "show files", "show me the files", "what files"]),
    ("system_info", ["system info", "system information", "about this computer", "about my computer", "specs"]),
    ("processes", ["process count", "how many processes", "running processes", "processes"]),
    ("cpu", ["cpu", "processor usage", "processor load"]),
    ("ram", ["ram", "memory usage", "memory"]),
    ("time", ["what time", "current time", "the time", "todays date", "what day", "the date"]),
]

NOTE_PATTERNS = [
    re.compile(r"^(?:please\s+)?(?:take|make|add|write)\s+(?:a\s+)?note\s*(?:that|:)?\s*(.*)$", re.I),
    re.compile(r"^note\s*(?:that|:)?\s*(.*)$", re.I),
]


def match_local(text):
    """Return (command, argument) if the text matches a known phrase, else None."""
    for pattern in NOTE_PATTERNS:
        m = pattern.match(text.strip())
        if m:
            return "add_note", m.group(1).strip()
    norm = normalise(text)
    for command, phrases in PHRASES:
        for phrase in phrases:
            if re.search(rf"\b{re.escape(phrase)}\b", norm):
                return command, ""
    return None


# =============================================================================
# SECTION 8: OLLAMA ROUTER (natural language -> command)
# =============================================================================
ROUTER_PROMPT = """You are the command router for an offline assistant called Titan.
Pick the single best command for the user's request.
Commands:
- time: current time or date
- list_files: list files in Titan's folder
- cpu: CPU usage
- ram: memory usage
- processes: number of running processes
- system_info: computer / system information
- add_note: save a note (put the note text in "argument")
- read_notes: read saved notes
- chat: use ONLY if no command fits; put a short helpful reply in "argument"
Reply with JSON only, like: {"command": "time", "argument": ""}"""


def ask_ollama(text):
    """Return (command, argument), or None if Ollama is unavailable or confused."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "messages": [
            {"role": "system", "content": ROUTER_PROMPT},
            {"role": "user", "content": text},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=OLLAMA_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
        choice = json.loads(body["message"]["content"])
        command = str(choice.get("command", "")).strip()
        argument = str(choice.get("argument", "")).strip()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
            KeyError, TypeError, ValueError):
        return None
    if command in COMMANDS or command == "chat":
        return command, argument
    return None


# =============================================================================
# SECTION 9: DISPATCHER (the brain: text in, reply out)
# =============================================================================
def titan_handle(text):
    """Return (reply, keep_running). The interface will also call this later."""
    if normalise(text) in EXIT_WORDS:
        return "Shutting down. Goodbye.", False

    routed = match_local(text)
    if routed is None:
        routed = ask_ollama(text)
    if routed is None:
        return ("I did not understand that, and my local AI model is not reachable. "
                "Try: time, cpu, ram, processes, system info, list files, note ..., read notes."), True

    command, argument = routed
    if command == "chat":
        return (argument or "I am not sure how to help with that."), True
    try:
        return COMMANDS[command](argument), True
    except Exception as err:  # a bad command must never kill the assistant
        return f"That command failed: {err}", True


# =============================================================================
# SECTION 10: MAIN LOOP
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Titan v1 - offline assistant")
    parser.add_argument("--text", action="store_true", help="force typed input")
    parser.add_argument("--silent", action="store_true", help="no spoken output")
    args = parser.parse_args()

    voice = Voice(enabled=not args.silent)
    ears = Ears(text_only=args.text)

    voice.say("Titan online. Starting identity check.")
    user = identity_check(voice, ears)
    if not user:
        voice.say("Identity check failed. Shutting down.")
        ears.close()
        return 1

    voice.say(f"Welcome, {user}. Input mode is {ears.mode}. How can I help?")
    try:
        while True:
            text = ears.listen()
            if not text:
                continue
            reply, keep_running = titan_handle(text)
            voice.say(reply)
            if not keep_running:
                break
    except KeyboardInterrupt:
        print()
        voice.say("Interrupted. Goodbye.")
    finally:
        ears.close()
    return 0


# =============================================================================
# SECTION 11: INTERFACE  >>> NOT BUILT YET - WE STOP HERE <<<
# =============================================================================
# Next phase: dashboard. It should only need these two hooks:
#   titan_handle(text) -> (reply, keep_running)   # send typed/spoken text in
#   get_stats()        -> dict                    # live numbers for gauges


def get_stats():
    """Live system numbers for the future dashboard."""
    if psutil is None:
        return {"cpu_percent": None, "ram_percent": None, "processes": None}
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": psutil.virtual_memory().percent,
        "processes": len(psutil.pids()),
    }


# =============================================================================
# SECTION 12: ARDUINO  >>> SAVED FOR LAST <<<
# =============================================================================
# (Arduino Uno integration goes here after the dashboard.)


if __name__ == "__main__":
    sys.exit(main())
