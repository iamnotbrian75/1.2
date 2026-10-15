#!/usr/bin/env python3
"""
TITAN v1.1 - offline JARVIS-style assistant  (the BRAIN)
=========================================================
Works in the terminal, and the dashboard (dashboard/server.py) uses it too.

Files that belong together (keep them in the same folder):
  T.v1.1.py           this file: the brain that decides what to do
  titan_persona.py    how Titan TALKS (warm British butler, small talk, AI chat)
  titan_skills.py     what Titan can DO (timers, weather, news, apps, PC controls)
  dashboard/          the browser interface

Features
  - Identity check (name + age) on startup
  - Voice in  (Vosk, offline)  -> falls back to typing if not set up
  - Voice out (pyttsx3, offline) -> falls back to text-only if not set up
  - Built-in commands: time, list files, CPU, RAM, process count, system info, notes
  - Skills: timers/reminders, weather, news, open apps/sites, search, volume,
    screenshot, lock, battery
  - Natural conversation through the local Ollama model (llama3.2:3b)

Setup (Windows, run in a terminal)
  pip install psutil pyttsx3 vosk sounddevice     (setup_titan.py does this for you)
  Install Ollama, then:  ollama pull llama3.2:3b

Run
  python T.v1.1.py          # voice if available, otherwise typing
  python T.v1.1.py --text   # force typing
  python T.v1.1.py --silent # no spoken output
"""

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
from datetime import datetime, timedelta
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
OLLAMA_TIMEOUT = 60          # seconds (router); chatting waits longer, see titan_persona.py

SAMPLE_RATE = 16000
LISTEN_SECONDS = 15          # max time to wait for one spoken phrase
MAX_ID_ATTEMPTS = 3
MAX_LIST_FILES = 15
NOTES_TO_READ = 5

EXIT_WORDS = {"exit", "quit", "goodbye", "shut down", "shutdown", "power down", "shut down titan"}

# ---- the two helper files (must sit next to this one) ----
sys.path.insert(0, str(BASE_DIR))
try:
    import titan_persona as persona
    import titan_skills as skills
    import titan_coder as coder
    import titan_diagnostics as diagnostics
except ImportError as err:
    print(f"ERROR: {err}")
    print("Put titan_persona.py, titan_skills.py, titan_coder.py, and titan_diagnostics.py in the same folder as T.v1.1.py.")
    sys.exit(1)


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
            voices = self.engine.getProperty("voices")
            for preferred in ("natural", "online", "neural", "mark", "zira", "david"):
                voice = next((item for item in voices if preferred in item.name.lower()), None)
                if voice is not None:
                    self.engine.setProperty("voice", voice.id)
                    break
            self.engine.setProperty("rate", 165)
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
def load_profiles():
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None

    if isinstance(data, dict) and isinstance(data.get("users"), list):
        profiles = data["users"]
    elif isinstance(data, dict) and "name" in data and "age" in data:
        profiles = [data]
    else:
        return None
    if any(
        not isinstance(profile, dict)
        or not isinstance(profile.get("name"), str)
        or not isinstance(profile.get("age"), int)
        or isinstance(profile.get("age"), bool)
        or not 1 <= profile["age"] <= 120
        for profile in profiles
    ):
        return None
    return profiles


def load_profile():
    """Return the first registered profile for older integrations."""
    profiles = load_profiles()
    return profiles[0] if profiles else None


def save_profile(name, age):
    profiles = load_profiles() or []
    for profile in profiles:
        if profile["name"].casefold() == name.casefold():
            if profile["age"] == age:
                return profile["name"]
            raise ValueError(f"A profile for {profile['name']} already exists with a different age.")
    name = name.strip().title()
    profiles.append({"name": name, "age": age})
    temporary = PROFILE_FILE.with_name(PROFILE_FILE.name + ".tmp")
    temporary.write_text(json.dumps({"users": profiles}, indent=2), encoding="utf-8")
    os.replace(temporary, PROFILE_FILE)
    return name


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
    profiles = load_profiles()

    # ---- first run: enrol the user ----
    if profiles is None:
        voice.say("No profile found. Let's get you set up.")
        name = ask(voice, ears, "What's your name?")
        age = parse_age(ask(voice, ears, "And how old are you?"))
        if not name or age is None:
            return None
        name = name.strip().title()
        name = save_profile(name, age)
        voice.say(f"Lovely to meet you, {name}. Profile saved.")
        return name

    # ---- later runs: verify ----
    for attempt in range(1, MAX_ID_ATTEMPTS + 1):
        name = ask(voice, ears, "Please state your name.")
        age = parse_age(ask(voice, ears, "And your age?"))
        matched = next(
            (profile for profile in profiles
             if name.strip().casefold() == profile["name"].casefold() and age == profile["age"]),
            None,
        )
        if matched is not None:
            return matched["name"]
        voice.say(f"I'm afraid that doesn't match. Attempt {attempt} of {MAX_ID_ATTEMPTS}.")
    return None


# =============================================================================
# SECTION 6: BUILT-IN COMMANDS (each returns the text Titan will say)
# =============================================================================
def cmd_time(arg=""):
    return persona.say_time(datetime.now())


def cmd_list_files(arg=""):
    try:
        items = sorted(p.name for p in BASE_DIR.iterdir() if p.is_file())
    except OSError as err:
        return f"I couldn't read the folder: {err}"
    return persona.say_files(items[:MAX_LIST_FILES], len(items))


def cmd_cpu(arg=""):
    if psutil is None:
        return "I can't read the CPU without the psutil package. Run: pip install psutil"
    return persona.say_cpu(psutil.cpu_percent(interval=0.5))


def cmd_ram(arg=""):
    if psutil is None:
        return "I can't read memory without the psutil package. Run: pip install psutil"
    mem = psutil.virtual_memory()
    return persona.say_ram(mem.percent, (mem.total - mem.available) / 1024 ** 3, mem.total / 1024 ** 3)


def cmd_processes(arg=""):
    if psutil is None:
        return "I can't count processes without the psutil package. Run: pip install psutil"
    return persona.say_processes(len(psutil.pids()))


def cmd_system_info(arg=""):
    os_name = f"{platform.system()} {platform.release()}"
    if psutil is None:
        return persona.say_system_info(os_name, platform.python_version())
    return persona.say_system_info(
        os_name, platform.python_version(), psutil.cpu_count(logical=True),
        psutil.virtual_memory().total / 1024 ** 3, (time.time() - psutil.boot_time()) / 3600)


def cmd_diagnose_system(arg=""):
    return diagnostics.diagnose_system()


def cmd_diagnose_titan(arg=""):
    return diagnostics.diagnose_titan()


def cmd_add_note(arg=""):
    arg = arg.strip()
    if not arg:
        return persona.say_note_prompt()
    try:
        with NOTES_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {arg}\n")
    except OSError as err:
        return f"I couldn't save the note: {err}"
    return persona.say_note_saved()


def _note_when(stamp):
    """'2026-10-15 09:44' -> 'today' / 'yesterday' / '12 Oct'."""
    try:
        day = datetime.strptime(stamp, "%Y-%m-%d %H:%M").date()
    except ValueError:
        return ""
    today = datetime.now().date()
    if day == today:
        return "today"
    if day == today - timedelta(days=1):
        return "yesterday"
    return f"{day.day} {day.strftime('%b')}"


def cmd_read_notes(arg=""):
    try:
        lines = [ln.strip() for ln in NOTES_FILE.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except FileNotFoundError:
        return persona.say_no_notes()
    except OSError as err:
        return f"I couldn't read the notes: {err}"
    entries = []
    for line in lines[-NOTES_TO_READ:]:
        m = re.match(r"^\[([^\]]+)\]\s*(.*)$", line)
        entries.append((_note_when(m.group(1)), m.group(2)) if m else ("", line))
    return persona.say_notes(entries)


# command name -> function. The AI router may only choose from these (plus the skills).
COMMANDS = {
    "time": cmd_time,
    "list_files": cmd_list_files,
    "cpu": cmd_cpu,
    "ram": cmd_ram,
    "processes": cmd_processes,
    "system_info": cmd_system_info,
    "diagnose_system": cmd_diagnose_system,
    "diagnose_titan": cmd_diagnose_titan,
    "add_note": cmd_add_note,
    "read_notes": cmd_read_notes,
}


def run_command(command, argument, text):
    """Run a built-in command or a skill and return what Titan says."""
    if command in COMMANDS:
        return COMMANDS[command](argument)
    if command == "project_coder":
        return coder.run_project_task(text)
    if command == "self_update":
        task, approved = argument
        return coder.run_self_task(task, approved=approved)
    if command in skills.SKILL_NAMES:
        return skills.run_skill(command, text)          # skills read the full sentence themselves
    raise KeyError(command)


# =============================================================================
# SECTION 7: LOCAL PHRASE MATCHING (fast path, no AI needed)
# =============================================================================
# Checked in this order. First match wins.
def is_code_generation_request(text):
    return re.search(
        r"\b(?:write|generate|create|make|show|give|build|draft|explain|help)\b"
        r".{0,120}\b(?:code|script|program|function|class|python|javascript|typescript|"
        r"java|c\+\+|c#|rust|go|html|css|sql)\b",
        text,
        re.I | re.S,
    ) is not None


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
    re.compile(r"^(?:please\s+)?(?:take|make|add|write)\s+(?:a\s+)?note\b\s*(?:that|:)?\s*(.*)$", re.I),
    re.compile(r"^note\b\s*(?:that|:)?\s*(.*)$", re.I),
]


def match_local(text):
    """Return (command, argument) if the text matches a known phrase, else None."""
    if re.match(r"^\s*(?:please\s+)?diagnos(?:e|is|tic|tics)\b", text, re.I) or re.match(
        r"^\s*(?:please\s+)?(?:run|do|perform|start)\b.{0,40}\b(?:diagnos(?:e|is|tic|tics)|health check)\b",
        text,
        re.I | re.S,
    ) or re.match(r"^\s*(?:please\s+)?health check\b", text, re.I):
        if re.search(r"\b(?:your|titan(?:'s)?|own|code|self)\b", text, re.I):
            return "diagnose_titan", ""
        return "diagnose_system", ""
    if re.match(
        r"^\s*(?:please\s+)?(?:check|inspect|review)\b.{0,50}\b(?:your|titan(?:'s)?|own)\b.{0,30}\bcode\b",
        text,
        re.I | re.S,
    ):
        return "diagnose_titan", ""
    self_update = re.match(
        r"^(?:please\s+)?(?P<approve>approve\s+)?(?:update|fix)\s+"
        r"(?P<target>your|titan(?:'s)?)\s+(?:own\s+)?code\s*--\s*(?P<task>.+)$",
        text.strip(),
        re.I,
    )
    if self_update:
        approved = bool(self_update.group("approve"))
        return "self_update", (self_update.group("task").strip(), approved)
    if re.match(r"^(?:code|work)\s+in\b", text.strip(), re.I):
        return "project_coder", text
    if is_code_generation_request(text):
        return "chat", ""
    for pattern in NOTE_PATTERNS:
        m = pattern.match(text.strip())
        if m:
            return "add_note", m.group(1).strip()
    skill = skills.find_skill(text)
    if skill is not None:
        return skill.name, text
    norm = normalise(text)
    for command, phrases in PHRASES:
        for phrase in phrases:
            if re.search(rf"\b{re.escape(phrase)}\b", norm):
                return command, ""
    return None


# =============================================================================
# SECTION 8: OLLAMA ROUTER (natural language -> command, or "chat")
# =============================================================================
def build_router_prompt():
    return (
        "You are the command router for an offline assistant called Titan.\n"
        "Pick the single best command for the user's request.\n"
        "Commands:\n"
        "- time: current time or date\n"
        "- list_files: list files in Titan's folder\n"
        "- cpu: CPU usage\n"
        "- ram: memory usage\n"
        "- processes: number of running processes\n"
        "- system_info: computer / system information\n"
        "- diagnose_system: inspect laptop resource usage and local Titan service availability (read-only)\n"
        "- diagnose_titan: check Titan's core Python files compile and check local services (read-only)\n"
        "- add_note: save a note (put the note text in \"argument\")\n"
        "- read_notes: read saved notes\n"
        f"{skills.router_lines()}\n"
        "- chat: anything else - conversation, questions, advice, opinions, jokes, general knowledge\n"
        "Code-writing or programming-explanation requests are chat, not time or another device command. "
        "For example, 'write me a small code in python' means chat.\n"
        "Reply with JSON only, like: {\"command\": \"time\", \"argument\": \"\"}\n"
        "Only fill \"argument\" for add_note."
    )


def ask_ollama(text):
    """
    Ask the local model which command fits.
    Returns ((command, argument), "ok") or (None, reason) where reason is
    "offline", "timeout", "nomodel" or "bad".
    """
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": 60, "num_ctx": 2048},
        "messages": [
            {"role": "system", "content": build_router_prompt()},
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
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, "bad"
    except Exception as err:
        return None, persona.error_kind(err)
    if command in COMMANDS or command in skills.SKILL_NAMES:
        return (command, argument), "ok"
    return ("chat", ""), "ok"


# =============================================================================
# SECTION 9: DISPATCHER (the brain: text in, reply out)
# =============================================================================
def handle_memory_command(text):
    """Handle explicit long-term memory requests before ordinary chat routing."""
    cleaned = text.strip()
    lowered = normalise(cleaned)

    if re.fullmatch(r"(?:what|tell me what) do you remember(?: about me)?", lowered) or \
            re.fullmatch(r"(?:show|list) (?:my )?(?:saved )?memories", lowered):
        memories = persona.saved_memories()
        if not memories:
            return "I don't have any saved memories yet. Say 'remember that...' to save one."
        return "I remember: " + "; ".join(memories) + "."

    if re.fullmatch(r"(?:please )?(?:forget everything(?: you remember)?(?: about me)?|"
                    r"forget all(?: my)? memories|clear(?: my)? memory)", lowered):
        removed = persona.forget_memories()
        persona.forget_history()
        if not removed:
            return "There weren't any saved memories to clear."
        return f"Done. I've forgotten all {removed} saved memories."

    match = re.match(r"^(?:please )?remember(?:\s+(?:that|this))?\s*[:,]?\s+(.+)$", cleaned, re.I)
    if match:
        return persona.save_memory(match.group(1))

    match = re.match(r"^(?:please )?forget(?:\s+about)?\s+(?:that\s+)?(.+)$", cleaned, re.I)
    if match:
        query = match.group(1).strip()
        removed = persona.forget_memories(query)
        persona.forget_history(query)
        if not removed:
            return f"I couldn't find a saved memory matching '{query}'."
        noun = "memory" if removed == 1 else "memories"
        return f"All right. I've forgotten {removed} matching {noun}."

    return None


def learning_evaluation():
    """Run and record deterministic checks of common intent-routing behavior."""
    cases = [
        ("standalone Python-code request", lambda: match_local("write me a small code in python"), "chat"),
        ("laptop diagnosis request", lambda: match_local("run a system diagnosis"), "diagnose_system"),
        ("Titan self-diagnosis request", lambda: match_local("diagnose your own code"), "diagnose_titan"),
        ("time request", lambda: match_local("what time is it?"), "time"),
        ("explicit project-edit request", lambda: match_local(r"code in C:\Users\User\Projects\Demo -- fix tests"), "project_coder"),
        ("capability question detection", lambda: persona.is_capabilities_request("what can and cant you do"), True),
        ("optimization question detection", lambda: persona.is_optimization_request("how can we make you more optimised"), True),
        ("incidental diagnosis mention", lambda: match_local("what should we change about your diagnosis response"), None),
    ]
    results = []
    for label, check, expected in cases:
        actual = check()
        if isinstance(expected, str):
            passed = actual is not None and actual[0] == expected
        else:
            passed = actual is expected
        results.append({"name": label, "passed": passed})
    passed, total = persona.record_evaluation(results)
    details = "\n".join(
        f"{'PASS' if item['passed'] else 'FAIL'}: {item['name']}"
        for item in results
    )
    return (
        f"Titan's local intent evaluation: {passed}/{total} passed.\n"
        "This checks deterministic routing and is not a benchmark of the language model's general intelligence.\n"
        f"{details}\n"
        "The evaluation summary was saved in your per-user Titan Brain learning file."
    )


def handle_learning_command(text):
    """Persist only explicit ratings/corrections and user-requested evaluations."""
    lowered = normalise(text)
    if re.fullmatch(r"(?:what have you learned|show|list)(?: from)?(?: my)? (?:feedback|learning feedback)", lowered):
        records = [item for item in persona.learning_records() if item.get("type") == "feedback"]
        if not records:
            return "I haven't saved any explicit feedback yet. Rate an answer from 1 to 5 or say 'correction: ...'."
        lines = ["Explicit feedback saved in your Titan Brain:"]
        for item in records[-8:]:
            rating = f"{item['rating']}/5" if isinstance(item.get("rating"), int) else "correction"
            lines.append(f"- {rating}: {item.get('feedback', '')} (request: {item.get('request', '')[:100]})")
        return "\n".join(lines)

    if re.fullmatch(r"(?:please )?(?:forget|clear|delete) (?:all )?(?:my )?(?:titan )?(?:learning )?feedback", lowered):
        removed = persona.forget_learning_records()
        return f"Removed {removed} saved learning records from your Titan Brain."

    if re.fullmatch(r"(?:please )?(?:run )?(?:a )?(?:titan )?(?:learning )?evaluation|"
                    r"(?:please )?(?:evaluate|test) yourself", lowered):
        return learning_evaluation()

    if re.fullmatch(r"(?:please )?evaluate (?:your|the) last answer", lowered):
        exchange = persona.last_exchange()
        if exchange is None:
            return "There isn't a recent answer to evaluate yet."
        return (
            f"My last request was: {exchange['request'][:300]}\n"
            f"My reply was: {exchange['reply'][:700]}\n"
            "Please rate it with 'rate that 1/5 because ...' or correct me with 'correction: ...'."
        )

    rating_match = re.match(
        r"^(?:please )?(?:rate|score) (?:that|your last answer)(?: as)?\s+([1-5])"
        r"(?:\s*/\s*5|\s+out of 5)?(?:\s*(?:because|:)\s*(.+))?$",
        text.strip(),
        re.I,
    )
    if rating_match:
        rating = int(rating_match.group(1))
        feedback = (rating_match.group(2) or f"User rated this answer {rating}/5.").strip()
        return persona.record_feedback(feedback, rating)

    quality_match = re.match(
        r"^(?:that was|that is|this was) (wrong|incorrect|unhelpful|helpful|good|great|correct)"
        r"(?:\s+(?:because|since)\s+(.+))?$",
        text.strip(),
        re.I,
    )
    if quality_match:
        quality = quality_match.group(1).casefold()
        rating = 1 if quality in {"wrong", "incorrect", "unhelpful"} else 5
        feedback = quality_match.group(2) or f"User marked this answer {quality}."
        return persona.record_feedback(feedback, rating)

    explicit_feedback = re.match(r"^(?:correction|feedback|you misunderstood(?: me)?)\s*:\s*(.+)$", text.strip(), re.I)
    if explicit_feedback:
        return persona.record_feedback(explicit_feedback.group(1))

    return None


def is_learning_request(text):
    lowered = normalise(text)
    return bool(
        re.search(r"\b(?:rate|score) (?:that|your last answer)\b", lowered)
        or re.match(r"^(?:that was|that is|this was) (?:wrong|incorrect|unhelpful|helpful|good|great|correct)\b", lowered)
        or re.match(r"^(?:correction|feedback|you misunderstood)\s*:", lowered)
        or re.search(r"\b(?:evaluation|evaluate yourself|evaluate your last answer|test yourself)\b", lowered)
        or re.search(r"\b(?:what have you learned|show|list)\b.*\bfeedback\b", lowered)
        or re.search(r"\b(?:forget|clear|delete)\b.*\bfeedback\b", lowered)
    )


def titan_handle(text):
    """Return (reply, keep_running). The dashboard calls this too."""
    text = text.strip()
    if not text:
        return "I didn't catch anything there. Try again?", True
    if normalise(text) in EXIT_WORDS:
        return persona.farewell(), False

    try:
        skills.cancel_cleanup_confirmation_unless(text)
        learning_reply = handle_learning_command(text)
        if learning_reply is not None:
            persona.remember(text, learning_reply)
            return learning_reply, True

        memory_reply = handle_memory_command(text)
        if memory_reply is not None:
            if not re.match(r"^(?:please )?forget\b", text, re.I):
                persona.remember(text, memory_reply)
            return memory_reply, True

        if persona.is_capabilities_request(text) or persona.is_optimization_request(text):
            reply = persona.capabilities_response(persona.is_optimization_request(text))
            persona.remember(text, reply)
            return reply, True

        # 1) chit-chat that needs no thinking: hellos, thanks, jokes, "what can you do"
        chat = persona.smalltalk(text)
        if chat is not None:
            reply, keep_running = chat
            persona.remember(text, reply)
            return reply, keep_running

        # 2) a command or skill we recognise straight away
        routed = match_local(text)

        # 3) otherwise let the AI model decide (command, or just conversation)
        if routed is None:
            routed, kind = ask_ollama(text)
            if routed is None and kind == "bad":
                routed = ("chat", "")                 # confusing answer: just chat instead
            elif routed is None:
                return persona.fallback(kind), True

        command, argument = routed
        if command == "chat":
            reply, kind = persona.llm_chat(
                text, OLLAMA_URL, OLLAMA_MODEL,
                preserve_formatting=is_code_generation_request(text),
            )
            if reply is None:
                return persona.fallback(kind), True
        else:
            reply = run_command(command, argument, text)
        persona.remember(text, reply)
        return reply, True
    except Exception as err:        # a bad command must never kill the assistant
        return f"Sorry, that one tripped me up: {err}", True


# =============================================================================
# SECTION 10: MAIN LOOP (terminal version)
# =============================================================================
def start_services():
    """Start the background clock for timers/reminders (safe to call twice)."""
    skills.start()


def main():
    parser = argparse.ArgumentParser(description="Titan v1.1 - offline assistant")
    parser.add_argument("--text", action="store_true", help="force typed input")
    parser.add_argument("--silent", action="store_true", help="no spoken output")
    args = parser.parse_args()

    voice = Voice(enabled=not args.silent)
    ears = Ears(text_only=args.text)

    voice.say("Titan online. Let's start with a quick identity check.")
    user = identity_check(voice, ears)
    if not user:
        voice.say("I'm afraid I can't verify you, so I'll shut down.")
        ears.close()
        return 1

    persona.set_user(user)
    skills.on_alert = lambda text: print(f"\n*** TITAN ALERT: {text}\a\nYOU: ", end="", flush=True)
    start_services()
    voice.say(persona.welcome())
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
# SECTION 11: DASHBOARD HOOKS (used by dashboard/server.py)
# =============================================================================
def get_stats():
    """Live system numbers for the dashboard gauges."""
    if psutil is None:
        return {"cpu_percent": None, "ram_percent": None, "processes": None}
    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "ram_percent": psutil.virtual_memory().percent,
        "processes": len(psutil.pids()),
    }


def set_user_name(name):
    """The dashboard calls this after a successful login."""
    persona.set_user(name)


def greeting():
    """Time-of-day welcome line for the dashboard."""
    return persona.welcome()


def pop_alerts():
    return skills.pop_alerts()


def wait_for_alerts(timeout=20):
    return skills.wait_for_alerts(timeout)


def active_timers():
    return skills.active_timers()


# =============================================================================
# SECTION 12: ARDUINO  >>> SAVED FOR LAST <<<
# =============================================================================
# (Arduino Uno integration goes here after the dashboard.)


if __name__ == "__main__":
    sys.exit(main())
