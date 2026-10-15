# =============================================================================
# SECTION 0: IMPORTS
# =============================================================================
import ctypes
import json
import os
import random
import re
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

try:
    import titan_persona as persona
except ImportError:                       # skills still work without the persona
    persona = None


# =============================================================================
# SECTION 1: CONFIG + PATHS
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "titan_settings.json"
REMINDERS_FILE = BASE_DIR / "titan_reminders.json"
SCREENSHOT_DIR = BASE_DIR / "screenshots"

IS_WINDOWS = sys.platform.startswith("win")

HTTP_TIMEOUT = 8                    # seconds to wait for weather / news
MAX_DOWNLOAD_BYTES = 1_000_000
USER_AGENT = "Titan/1.1 (personal offline assistant)"
MAX_TIMER_SECONDS = 7 * 24 * 3600   # a week
WEATHER_CACHE_SECONDS = 600
NEWS_HEADLINES = 4
MAX_CLEANUP_ENTRIES = 20_000
MAX_CLEANUP_RESULTS = 10
CLEANUP_CONFIRMATION_SECONDS = 120

NEWS_FEEDS = {                      # BBC News RSS
    "top": "https://feeds.bbci.co.uk/news/rss.xml",
    "uk": "https://feeds.bbci.co.uk/news/uk/rss.xml",
    "world": "https://feeds.bbci.co.uk/news/world/rss.xml",
    "technology": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "science": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "health": "https://feeds.bbci.co.uk/news/health/rss.xml",
    "politics": "https://feeds.bbci.co.uk/news/politics/rss.xml",
    "entertainment": "https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml",
    "sport": "https://feeds.bbci.co.uk/sport/rss.xml",
}
NEWS_TOPIC_WORDS = {
    "uk": "uk", "britain": "uk", "world": "world", "global": "world", "international": "world",
    "tech": "technology", "technology": "technology", "business": "business", "money": "business",
    "economy": "business", "science": "science", "health": "health", "politics": "politics",
    "political": "politics", "entertainment": "entertainment", "celebrity": "entertainment",
    "sport": "sport", "sports": "sport", "football": "sport",
}


# =============================================================================
# SECTION 2: HELPERS
# =============================================================================
on_alert = None          # optional function(text) - the terminal sets this to print alerts


def _say(key, options, **values):
    """Pick a reply variant (with the persona if available) and fill <placeholders>."""
    if persona is not None:
        text = persona.pick(key, options)
    else:
        text = re.sub(r",?\s*\{name\}", "", options[0]).strip()
    for k, v in values.items():
        text = text.replace(f"<{k}>", str(v))
    return text


def _user_name():
    return persona.get_user() if persona is not None else None


def norm(text):
    """Lowercase, drop apostrophes and odd symbols (keeps . : / - _ % for web addresses)."""
    text = text.lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9:./_%\- ]", " ", text)).strip()


def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    _atomic_write(SETTINGS_FILE, json.dumps(settings, indent=2))


def _atomic_write(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


NETWORK_ERRORS = (urllib.error.URLError, OSError, ValueError, TimeoutError)


def fetch_bytes(url, timeout=HTTP_TIMEOUT, limit=MAX_DOWNLOAD_BYTES):
    """Download a small https page. Anything else is refused."""
    if not url.startswith("https://"):
        raise ValueError("only https addresses are allowed")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("response too large")
    return data


def fetch_json(url):
    return json.loads(fetch_bytes(url).decode("utf-8"))


# ---- number words and durations ------------------------------------------------
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
          "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}

_NUMWORD = (r"(?:\d+(?:\.\d+)?|(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[ -](?:one|two|three|"
            r"four|five|six|seven|eight|nine))?|zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
            r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|an?)")
_DUR_UNIT = r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)"
_DURATION_RE = re.compile(
    rf"(?<![a-z0-9.])(?P<n>{_NUMWORD})(?P<half>\s+and\s+a\s+half)?\s*(?P<u>{_DUR_UNIT})\b(?P<half2>\s+and\s+a\s+half\b)?")
_SPECIAL_DURATIONS = [
    (re.compile(r"\b(?:a )?quarter of an hour\b|\bquarter hour\b"), 900),
    (re.compile(r"\bhalf an hour\b|\ba half hour\b|\bhalf hour\b"), 1800),
]


def _number_value(token):
    token = token.replace("-", " ")
    if token in ("a", "an"):
        return 1.0
    if re.fullmatch(r"\d+(?:\.\d+)?", token):
        return float(token)
    return float(sum(_TENS.get(p, _UNITS.get(p, 0)) for p in token.split()))


def _unit_seconds(unit):
    if unit.startswith("s"):
        return 1
    if unit.startswith("m"):
        return 60
    if unit.startswith("h"):
        return 3600
    return 86400


def find_duration(text):
    """
    Find a length of time in a sentence.
    "in 1 hour 30 minutes to call mum" -> (5400, start, end) where start:end covers
    "in 1 hour 30 minutes". Returns None if there is no duration.
    """
    found = []
    for pattern, seconds in _SPECIAL_DURATIONS:
        for m in pattern.finditer(text):
            found.append((m.start(), m.end(), seconds))
    for m in _DURATION_RE.finditer(text):
        if any(m.start() < e and m.end() > s for s, e, _ in found):      # already counted
            continue
        value = _number_value(m.group("n")) + (0.5 if (m.group("half") or m.group("half2")) else 0)
        found.append((m.start(), m.end(), value * _unit_seconds(m.group("u"))))
    if not found:
        return None
    found.sort()
    run = [found[0]]
    for item in found[1:]:                       # keep only one connected run: "1 hour and 30 minutes"
        gap = text[run[-1][1]:item[0]]
        if re.fullmatch(r"[\s,]*(?:and)?[\s,]*", gap):
            run.append(item)
        else:
            break
    start, end = run[0][0], run[-1][1]
    prefix = re.search(r"\b(?:in|for|after|within)\s+(?:about\s+|around\s+|roughly\s+)?$", text[:start])
    if prefix:
        start = prefix.start()
    return int(round(sum(s for _, _, s in run))), start, end


_CLOCK_RE = re.compile(
    r"\b(?:at|for|by|around)\s+(?:(?P<word>noon|midday|midnight)|(?P<h>\d{1,2})(?:[:.](?P<m>\d{2}))?"
    r"\s*(?P<ap>am|pm|a\.?m\.?|p\.?m\.?)?)(?![0-9])(?!\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b)")


def find_clock(text, now=None):
    """
    Find a time of day: "at 3pm", "at 15:30", "tomorrow at 9", "at noon".
    Returns (datetime, start, end) or None. Without am/pm, picks the next sensible time.
    """
    now = now or datetime.now()
    m = _CLOCK_RE.search(text)
    if not m:
        return None
    tomorrow = bool(re.search(r"\btomorrow\b", text))
    if m.group("word"):
        hour, minute = (0, 0) if m.group("word") == "midnight" else (12, 0)
        candidates = [hour]
    else:
        hour, minute = int(m.group("h")), int(m.group("m") or 0)
        suffix = (m.group("ap") or "").replace(".", "")
        if minute > 59 or hour > 24 or (suffix and not 1 <= hour <= 12):
            return None
        if suffix:
            hour = hour % 12 + (12 if suffix.startswith("p") else 0)
            candidates = [hour]
        elif hour == 0 or hour >= 13:
            candidates = [hour % 24]
        elif tomorrow:
            candidates = [12] if hour == 12 else [hour + 12 if hour <= 6 else hour]
        else:
            candidates = [hour % 12, hour % 12 + 12]
    base = now.replace(second=0, microsecond=0)
    if tomorrow:
        due = (base + timedelta(days=1)).replace(hour=candidates[0], minute=minute)
    else:
        options = [base.replace(hour=c, minute=minute) for c in candidates]
        future = [d for d in options if d > now]
        due = min(future) if future else (options[0] + timedelta(days=1))
    start = m.start()
    tom = re.search(r"\btomorrow\s+(?:morning\s+|afternoon\s+|evening\s+)?$", text[:start])
    if tom:
        start = tom.start()
    return due, start, m.end()


def fmt_duration(seconds):
    """3600 -> '1 hour', 5400 -> '1 hour 30 minutes', 45 -> '45 seconds'."""
    seconds = max(0, int(round(seconds)))
    parts = []
    for name, size in (("day", 86400), ("hour", 3600), ("minute", 60), ("second", 1)):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count} {name}{'s' if count != 1 else ''}")
    return " ".join(parts[:2]) or "0 seconds"


def fmt_clock(moment, now=None):
    now = now or datetime.now()
    text = f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    days = (moment.date() - now.date()).days
    if days == 1:
        return f"tomorrow at {text}"
    if days > 1:
        return f"{moment.strftime('%A')} at {text}"
    return text


# =============================================================================
# SECTION 3: OPEN APPS / FOLDERS / WEBSITES, AND WEB SEARCH
# =============================================================================
# EDIT THESE LISTS to teach Titan more. Left = what you say, right = what Windows launches.
APPS = {
    "notepad": ("notepad.exe", "Notepad"),
    "calculator": ("calc.exe", "Calculator"), "calc": ("calc.exe", "Calculator"),
    "paint": ("mspaint.exe", "Paint"),
    "file explorer": ("explorer.exe", "File Explorer"), "explorer": ("explorer.exe", "File Explorer"),
    "files": ("explorer.exe", "File Explorer"),
    "task manager": ("taskmgr.exe", "Task Manager"),
    "settings": ("ms-settings:", "Settings"),
    "control panel": ("control.exe", "Control Panel"),
    "command prompt": ("cmd.exe", "Command Prompt"), "cmd": ("cmd.exe", "Command Prompt"),
    "terminal": ("cmd.exe", "a terminal"), "powershell": ("powershell.exe", "PowerShell"),
    "chrome": ("chrome", "Chrome"), "google chrome": ("chrome", "Chrome"),
    "edge": ("msedge", "Edge"), "microsoft edge": ("msedge", "Edge"),
    "firefox": ("firefox", "Firefox"),
    "word": ("winword", "Word"), "excel": ("excel", "Excel"), "powerpoint": ("powerpnt", "PowerPoint"),
    "outlook": ("outlook", "Outlook"),
    "vs code": ("code", "VS Code"), "vscode": ("code", "VS Code"), "visual studio code": ("code", "VS Code"),
    "spotify": ("spotify:", "Spotify"), "discord": ("discord:", "Discord"), "steam": ("steam:", "Steam"),
    "camera": ("microsoft.windows.camera:", "the Camera"),
    "snipping tool": ("ms-screenclip:", "the Snipping Tool"),
    "store": ("ms-windows-store:", "the Microsoft Store"),
}

FOLDERS = {                        # shell folder names understood by Windows Explorer
    "downloads": "Downloads", "documents": "Personal", "desktop": "Desktop",
    "pictures": "My Pictures", "photos": "My Pictures", "music": "My Music", "videos": "My Video",
}

SITES = {                          # what you say -> (address, spoken name)
    "youtube": ("https://www.youtube.com", "YouTube"), "you tube": ("https://www.youtube.com", "YouTube"),
    "google": ("https://www.google.com", "Google"),
    "gmail": ("https://mail.google.com", "Gmail"), "email": ("https://mail.google.com", "Gmail"),
    "maps": ("https://maps.google.com", "Google Maps"), "google maps": ("https://maps.google.com", "Google Maps"),
    "drive": ("https://drive.google.com", "Google Drive"), "google drive": ("https://drive.google.com", "Google Drive"),
    "calendar": ("https://calendar.google.com", "Google Calendar"),
    "github": ("https://github.com", "GitHub"), "reddit": ("https://www.reddit.com", "Reddit"),
    "twitter": ("https://x.com", "X"), "x": ("https://x.com", "X"),
    "facebook": ("https://www.facebook.com", "Facebook"), "instagram": ("https://www.instagram.com", "Instagram"),
    "linkedin": ("https://www.linkedin.com", "LinkedIn"), "amazon": ("https://www.amazon.co.uk", "Amazon"),
    "ebay": ("https://www.ebay.co.uk", "eBay"), "netflix": ("https://www.netflix.com", "Netflix"),
    "twitch": ("https://www.twitch.tv", "Twitch"), "whatsapp": ("https://web.whatsapp.com", "WhatsApp Web"),
    "bbc": ("https://www.bbc.co.uk", "the BBC"), "bbc news": ("https://www.bbc.co.uk/news", "BBC News"),
    "wikipedia": ("https://en.wikipedia.org", "Wikipedia"), "claude": ("https://claude.ai", "Claude"),
}

SEARCH_ENGINES = {
    "google": ("https://www.google.com/search?q=", "Google"),
    "youtube": ("https://www.youtube.com/results?search_query=", "YouTube"),
    "bing": ("https://www.bing.com/search?q=", "Bing"),
    "duckduckgo": ("https://duckduckgo.com/?q=", "DuckDuckGo"),
    "wikipedia": ("https://en.wikipedia.org/w/index.php?search=", "Wikipedia"),
}

_DOMAIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?P<host>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*"
    r"\.[a-z]{2,24})(?P<path>/[a-z0-9._~%/\-]*)?")


class Unsupported(Exception):
    """This action only works on Windows."""


def resolve_target(raw):
    """Work out what 'open <raw>' means. Returns (kind, target, spoken_name) or None."""
    t = raw.strip()
    t = re.sub(r"\s+dot\s+", ".", t)
    t = re.sub(r"(?<=\w) \. (?=\w)", ".", t)                      # "youtube . com"
    t = re.sub(r"^(?:up |the |my )+", "", t)
    t = re.sub(r"\s+(?:please|for me|now|thanks|thank you)$", "", t)
    t = re.sub(r"\s+(?:app|application|program|website|site|page|window)$", "", t)
    t = re.sub(r"\s+(?:folder|directory)$", "", t).strip()
    if t in FOLDERS:
        return "folder", FOLDERS[t], f"your {t} folder"
    if t in APPS:
        return "app", APPS[t][0], APPS[t][1]
    if t in SITES:
        return "site", SITES[t][0], SITES[t][1]
    m = _DOMAIN_RE.fullmatch(t)
    if m:
        return "site", "https://" + m.group("host") + (m.group("path") or ""), m.group("host")
    return None


def open_url(url):
    """Open a web address in the default browser. Only http(s) addresses get here."""
    if not url.startswith(("https://", "http://")):
        return False
    return bool(webbrowser.open(url, new=2))


def launch_app(target):
    if not IS_WINDOWS:
        raise Unsupported
    os.startfile(target)                       # noqa: only whitelisted targets reach this


def open_folder(shell_name):
    if not IS_WINDOWS:
        raise Unsupported
    subprocess.Popen(["explorer.exe", f"shell:{shell_name}"])


_cleanup_lock = threading.Lock()
_cleanup_root = None
_cleanup_candidates = []
_cleanup_confirmation = None


def match_cleanup(text):
    return bool(
        re.match(r"^(?:please )?(?:(?:scan|find|show|list)\s+(?:the )?(?:largest|large|big)\s+files?\s+(?:in|under|inside)\b.*"
                 r"|delete\s+(?:file\s+)?\d+\b|confirm\s+delete\s+(?:file\s+)?\d+\b|cancel\s+delete)$", text)
    )


def _format_file_size(size):
    value = float(size)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024


def _scan_large_files(folder):
    path_text = folder.strip().strip("\"'")
    if not path_text:
        return "Tell me the full path of the folder you'd like me to scan."
    root = Path(path_text)
    if not root.is_absolute():
        return "For safety, give me the full path of one specific folder to scan."
    try:
        root = root.resolve(strict=True)
    except OSError as err:
        return f"I couldn't open that folder: {err}"
    if not root.is_dir():
        return "That path isn't a folder. I only scan folders you specify."
    if root == Path(root.anchor):
        return "I won't scan an entire drive. Please choose one specific folder."
    windows_dir = os.environ.get("WINDIR")
    if windows_dir:
        system_root = Path(windows_dir).resolve()
        try:
            root.relative_to(system_root)
            return "I won't scan Windows system folders."
        except ValueError:
            pass

    found = []
    stack = [(root, 0)]
    entries_seen = 0
    skipped = 0
    truncated = False
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > MAX_CLEANUP_ENTRIES:
                        truncated = True
                        stack.clear()
                        break
                    try:
                        details = entry.stat(follow_symlinks=False)
                        attributes = getattr(details, "st_file_attributes", 0)
                        if entry.is_symlink() or attributes & (0x2 | 0x4 | 0x400):
                            continue
                        if stat.S_ISDIR(details.st_mode):
                            if depth < 24:
                                stack.append((Path(entry.path), depth + 1))
                            continue
                        if stat.S_ISREG(details.st_mode):
                            found.append({
                                "path": Path(entry.path),
                                "size": details.st_size,
                                "mtime_ns": details.st_mtime_ns,
                            })
                    except OSError:
                        skipped += 1
        except OSError:
            skipped += 1

    found.sort(key=lambda item: item["size"], reverse=True)
    candidates = found[:MAX_CLEANUP_RESULTS]
    with _cleanup_lock:
        global _cleanup_root, _cleanup_candidates, _cleanup_confirmation
        _cleanup_root = root
        _cleanup_candidates = candidates
        _cleanup_confirmation = None

    if not candidates:
        return "I couldn't find any accessible files in that folder to list."
    lines = [f"The largest files I found in {root}:"]
    lines.extend(f"{index}. {_format_file_size(item['size'])} — {item['path']}"
                 for index, item in enumerate(candidates, 1))
    lines.append("Nothing has been deleted. To choose one, say 'delete file 1' using its number.")
    if truncated:
        lines.append(f"I stopped after checking {MAX_CLEANUP_ENTRIES:,} entries.")
    if skipped:
        lines.append(f"I couldn't inspect {skipped} inaccessible entries.")
    return "\n".join(lines)


def _move_to_recycle_bin(path):
    if not IS_WINDOWS:
        raise Unsupported

    class FileOperation(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    source = str(path) + "\0\0"
    operation = FileOperation(
        None, 3, source, None, 0x40 | 0x10, 0, None, None
    )
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHFileOperationW.argtypes = [ctypes.POINTER(FileOperation)]
    shell32.SHFileOperationW.restype = ctypes.c_int
    result = shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        raise OSError(result, "Windows could not move the file to the Recycle Bin.")
    if operation.fAnyOperationsAborted:
        raise OSError("Windows cancelled moving the file to the Recycle Bin.")


def _confirm_cleanup_deletion(number):
    global _cleanup_candidates, _cleanup_confirmation
    with _cleanup_lock:
        confirmation = _cleanup_confirmation
        if confirmation is None or confirmation["number"] != number or time.monotonic() > confirmation["expires"]:
            _cleanup_confirmation = None
            return "There isn't a current deletion awaiting that confirmation. Scan a folder and select a file first."
        candidate = confirmation["candidate"]
        root = _cleanup_root
        _cleanup_confirmation = None

    path = candidate["path"]
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        details = path.lstat()
    except (OSError, ValueError) as err:
        return f"I couldn't verify that file is still safe to move: {err}"
    if path.is_symlink() or not stat.S_ISREG(details.st_mode) or \
            getattr(details, "st_file_attributes", 0) & 0x400:
        return "That file is no longer a regular file, so I won't move it."
    if details.st_size != candidate["size"] or details.st_mtime_ns != candidate["mtime_ns"]:
        return "That file has changed since the scan. Scan the folder again before deciding."
    try:
        _move_to_recycle_bin(path)
    except Unsupported:
        return "Moving files to the Recycle Bin is only supported on Windows."
    except OSError as err:
        return f"I couldn't move that file to the Recycle Bin: {err}"

    return f"Moved {path.name} to the Recycle Bin."


def cancel_cleanup_confirmation_unless(text):
    """Require the exact confirmation to immediately follow a delete request."""
    if re.match(r"^confirm\s+delete\s+(?:file\s+)?\d+\b$", norm(text)):
        return
    global _cleanup_confirmation
    with _cleanup_lock:
        _cleanup_confirmation = None


def run_cleanup(text):
    global _cleanup_confirmation
    scan = re.match(
        r"^(?:please )?(?:(?:scan|find|show|list)\s+(?:the )?(?:largest|large|big)\s+files?\s+(?:in|under|inside)\s+)(.+)$",
        text.strip(), re.I,
    )
    if scan:
        return _scan_large_files(scan.group(1))

    cancel = re.fullmatch(r"cancel delete", norm(text.strip()))
    if cancel:
        with _cleanup_lock:
            _cleanup_confirmation = None
        return "Cancelled. I won't move anything."

    confirm = re.fullmatch(r"confirm delete (?:file )?(\d+)", norm(text.strip()))
    if confirm:
        return _confirm_cleanup_deletion(int(confirm.group(1)))

    delete = re.fullmatch(r"delete (?:file )?(\d+)", norm(text.strip()))
    if delete:
        number = int(delete.group(1))
        with _cleanup_lock:
            if number < 1 or number > len(_cleanup_candidates):
                return "That number isn't in the current file list. Scan a folder first."
            candidate = _cleanup_candidates[number - 1]
            _cleanup_confirmation = {
                "number": number,
                "candidate": candidate,
                "expires": time.monotonic() + CLEANUP_CONFIRMATION_SECONDS,
            }
        return (f"This will move {candidate['path']} ({_format_file_size(candidate['size'])}) to the Recycle Bin. "
                f"Nothing has been moved yet. To approve this one file, say 'confirm delete file {number}'. "
                "Say 'cancel delete' to cancel.")

    return ("Tell me a full folder path to scan, such as 'find large files in C:\\Users\\Brian\\Downloads'. "
            "I'll list candidates first and ask again before moving any file to the Recycle Bin.")


def match_open(t):
    if re.match(r"^(?:please )?(?:(?:can|could|would) you )?(?:open|launch|go to|pull up|fire up|bring up)\b", t):
        return True
    m = re.match(r"^(?:please )?(?:(?:can|could) you )?(?:start|run|load)\s+(.+)$", t)
    return bool(m and resolve_target(m.group(1)))


def run_open(text):
    t = norm(text)
    m = re.match(r"^(?:please )?(?:(?:can|could|would) you )?(?:open|launch|go to|pull up|fire up|bring up|start|run|load)\s+(.+)$", t)
    if not m:
        return "What would you like me to open?"
    resolved = resolve_target(m.group(1))
    if resolved is None:
        return ("I don't know an app or site called '" + m.group(1) + "'. I only launch things on my list, so "
                "nothing odd gets run. Try 'open notepad' or 'open youtube', or give me a web address like bbc.co.uk.")
    kind, target, spoken = resolved
    try:
        if kind == "site":
            if not open_url(target):
                return "I tried, but I couldn't find a web browser to open that in."
        elif kind == "folder":
            open_folder(target)
        else:
            launch_app(target)
    except Unsupported:
        return f"I can open websites from anywhere, but launching {spoken} only works on Windows, I'm afraid."
    except OSError:
        return f"I tried to open {spoken}, but Windows couldn't find it. It might not be installed."
    return _say("open", ["Opening <x>.", "<x>, coming right up.", "On it — opening <x>.", "Opening <x> now."], x=spoken)


def parse_search(t):
    """Returns (engine, query) from a cleaned sentence, or None."""
    t = re.sub(r"^(?:please )?(?:(?:can|could|would) you )?", "", t)
    m = re.match(r"^(search|look up|google|bing|duckduckgo|play)\b\s*(?:for\s+|up\s+)?(.*)$", t)
    if not m:
        return None
    verb, rest = m.group(1), m.group(2).strip()
    engine = {"google": "google", "bing": "bing", "duckduckgo": "duckduckgo", "play": "youtube"}.get(verb, "google")
    lead = re.match(r"^(google|youtube|bing|duckduckgo|wikipedia)\s+(?:for\s+)?(.+)$", rest)
    if lead:
        engine, rest = lead.group(1), lead.group(2)
    tail = re.search(r"\s+(?:on|in|using|with)\s+(google|youtube|bing|duckduckgo|wikipedia)$", rest)
    if tail:
        engine, rest = tail.group(1), rest[:tail.start()]
    return engine, rest.strip()


def match_search(t):
    return bool(re.match(r"^(?:please )?(?:(?:can|could|would) you )?(?:search|look up|google|bing|duckduckgo|play)\b", t))


def run_search(text):
    parsed = parse_search(norm(text))
    if not parsed or not parsed[1]:
        return "What would you like me to search for?"
    engine, query = parsed
    base, label = SEARCH_ENGINES[engine]
    if not open_url(base + urllib.parse.quote_plus(query)):
        return "I tried, but I couldn't find a web browser to open that in."
    return _say("search", ["Searching <e> for <q>.", "Pulling up <e> results for <q>.",
                           "Here you go — <e> results for <q>."], e=label, q=query)


# =============================================================================
# SECTION 4: TIMERS, ALARMS AND REMINDERS
# =============================================================================
_lock = threading.RLock()
_alert_cond = threading.Condition(_lock)
_items = []                        # active timers/reminders: dicts
_alerts = deque()                  # alerts waiting for the dashboard
_stop = threading.Event()
_thread = None


def _load_items():
    try:
        data = json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    good = []
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and isinstance(item.get("due"), (int, float)) and item.get("kind") in ("timer", "reminder", "alarm"):
            good.append({"id": str(item.get("id", "")), "kind": item["kind"], "text": str(item.get("text", ""))[:200],
                         "due": float(item["due"]), "length": int(item.get("length", 0) or 0)})
    return good


def _save_items():
    try:
        _atomic_write(REMINDERS_FILE, json.dumps(_items))
    except OSError:
        pass


def _new_id():
    return f"{int(time.time() * 1000):x}{random.randrange(4096):03x}"


def _alert_text(item, missed=False):
    name = _user_name()
    kind, label = item["kind"], item["text"]
    if kind == "timer":
        length = fmt_duration(item["length"])
        if label:
            text = _say("alert_timer_l", ["Your <l> timer is done.", "Time's up on the <l> timer, {name}."], l=label)
        else:
            text = _say("alert_timer", ["Your <d> timer is up.", "Time's up — that's your <d> timer.",
                                        "Ding ding. <d> is up, {name}."], d=length)
    elif kind == "alarm":
        text = _say("alert_alarm", ["It's time. Your alarm is going off.", "Rise and shine — that's your alarm, {name}."])
    else:
        text = _say("alert_rem", ["Reminder: <t>.", "Just a nudge — <t>.", "Excuse me, {name} — reminder: <t>."], t=label or "you asked me to remind you")
    if missed:
        text = "Sorry, I was switched off when this came due. " + text
    return text


def _fire(item, missed=False):
    text = _alert_text(item, missed)
    with _alert_cond:
        _alerts.append({"text": text, "kind": item["kind"], "time": datetime.now().isoformat(timespec="seconds")})
        _alert_cond.notify_all()
    callback = on_alert
    if callback is not None:
        try:
            callback(text)
        except Exception:
            pass


def _scheduler_loop():
    while not _stop.is_set():
        now = time.time()
        with _lock:
            due = [i for i in _items if i["due"] <= now]
            if due:
                for item in due:
                    _items.remove(item)
                _save_items()
        for item in due:
            _fire(item)
        _stop.wait(0.5)


def start():
    """Load saved reminders, announce any that came due while Titan was off, start the clock."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _items[:] = _load_items()
        now = time.time()
        missed = [i for i in _items if i["due"] <= now]
        for item in missed:
            _items.remove(item)
        if missed:
            _save_items()
        _thread = threading.Thread(target=_scheduler_loop, name="titan-scheduler", daemon=True)
        _thread.start()
    for item in missed:
        _fire(item, missed=True)


def stop():
    _stop.set()


def _reset_for_tests():
    stop()
    if _thread is not None:
        _thread.join(timeout=2)
    with _lock:
        _items.clear()
        _alerts.clear()


def _add_item(kind, text, due, length=0):
    start()
    item = {"id": _new_id(), "kind": kind, "text": text, "due": due, "length": length}
    with _lock:
        _items.append(item)
        _save_items()
    return item


def _you(text):
    """'call my mum' -> 'call your mum' (so the alert reads naturally)."""
    for old, new in ((r"\bmyself\b", "yourself"), (r"\bmy\b", "your"), (r"\bmine\b", "yours"),
                     (r"\bim\b", "youre"), (r"\bi\b", "you"), (r"\bme\b", "you")):
        text = re.sub(old, new, text)
    return text


def _cut(text, start, end):
    return (text[:start] + " " + text[end:]).strip()


def _reminder_message(t, spans):
    for start, end in sorted(spans, reverse=True):
        t = t[:start] + " " + t[end:]
    t = re.sub(r"\bremind me\b", " ", t)
    t = re.sub(r"\b(?:set|add|create|make)\s+(?:me\s+)?(?:a|an)\s+(?:reminder|alarm)\b", " ", t)
    t = re.sub(r"\b(?:tomorrow|today|tonight|this (?:morning|afternoon|evening))\b", " ", t)
    t = re.sub(r"\bplease\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"^(?:to|that|about|of)\s+", "", t)
    t = re.sub(r"\s+(?:to|that|about|of|and)$", "", t)
    return _you(t).strip()


def match_timers(t):
    return bool(re.search(r"\btimers?\b|\bremind(?:er|ers)?\b|\balarms?\b|\bwake me\b|\bcountdown\b|"
                          r"\bhow long (?:is )?(?:left|remaining)\b", t))


def run_timers(text):
    t = norm(text)
    kinds = [k for k, w in (("timer", "timer"), ("reminder", "remind"), ("alarm", "alarm")) if w in t]

    # ---- cancel ----
    if re.search(r"\b(?:cancel|stop|clear|delete|remove|dismiss|turn off|forget)\b", t) and kinds:
        with _lock:
            wanted = [i for i in _items if i["kind"] in kinds or ("reminder" in kinds and i["kind"] == "alarm")]
            for item in wanted:
                _items.remove(item)
            _save_items()
        if not wanted:
            return "There's nothing to cancel — no matching timers or reminders are running."
        return _say("cancel", ["Done — cancelled <n>.", "All cleared. That's <n> gone."],
                    n=f"{len(wanted)} item{'s' if len(wanted) != 1 else ''}")

    # ---- list ----
    if re.search(r"\b(?:what|which|any|list|show|how long|how much time|check|do i have)\b", t) and \
            not re.search(r"\b(?:set|start|create|make|remind me)\b", t):
        return describe_timers()

    now = datetime.now()

    # ---- reminder: "remind me to X in 20 minutes / at 3pm" ----
    if "remind" in t:
        dur = find_duration(t)
        if dur:
            seconds, s, e = dur
            due, spans, when = time.time() + seconds, [(s, e)], "in " + fmt_duration(seconds)
        else:
            clock = find_clock(t, now)
            if not clock:
                return "When should I remind you? Try 'remind me in 20 minutes to check the oven' or 'remind me at 3 PM to call mum'."
            moment, s, e = clock
            due, spans, when = moment.timestamp(), [(s, e)], fmt_clock(moment, now)
        if due - time.time() > MAX_TIMER_SECONDS * 52:
            return "That's a little too far ahead for me — I can manage up to a year."
        message = _reminder_message(t, spans)
        _add_item("reminder", message, due)
        what = f"to {message}" if message else "about that"
        return _say("rem_set", ["I'll remind you <w> <when>.", "Consider it done — I'll remind you <w> <when>.",
                                "You've got it, {name}. I'll remind you <w> <when>."], w=what, when=when)

    # ---- alarm: "set an alarm for 7am", "wake me at 6:30" ----
    if "alarm" in t or "wake me" in t:
        clock = find_clock(t, now)
        if not clock:
            dur = find_duration(t)
            if dur:
                _add_item("alarm", "", time.time() + dur[0])
                return f"Alarm set for {fmt_duration(dur[0])} from now."
            return "What time should I set the alarm for? Try 'set an alarm for 7 AM'."
        moment = clock[0]
        _add_item("alarm", "", moment.timestamp())
        return _say("alarm_set", ["Alarm set for <w>.", "Done — I'll wake you at <w>."], w=fmt_clock(moment, now))

    # ---- timer: "set a timer for 10 minutes" ----
    dur = find_duration(t)
    if not dur:
        return "How long should I set it for? Try 'set a timer for ten minutes'."
    seconds = dur[0]
    if seconds > MAX_TIMER_SECONDS:
        return "That's a bit long for a timer — I top out at a week. A reminder or alarm might suit better."
    label = ""                                    # "set a pasta timer for 10 minutes" -> "pasta"
    lm = re.search(r"\b(?:set|start|create|make|put on)\s+(?:me\s+)?(?:a|an|the)?\s*(?P<l>[a-z ]*?)\s*timer\b", t)
    if lm:
        label = lm.group("l").strip()
        if label in ("new", "quick", "countdown", "another", "one") or len(label.split()) > 3:
            label = ""
    _add_item("timer", label, time.time() + seconds, length=seconds)
    name = f"{label} timer" if label else "timer"
    return _say("timer_set", ["<n>'s running — <d>, starting now.", "Right, <d> on the clock. I'll shout when it's up.",
                              "Done. <d> — I'll let you know, {name}."], n=name.capitalize(), d=fmt_duration(seconds))


def describe_timers():
    now = time.time()
    with _lock:
        items = sorted(_items, key=lambda i: i["due"])
    if not items:
        return _say("no_timers", ["Nothing's running at the moment — no timers or reminders.",
                                  "All quiet. You've got no timers or reminders set."])
    parts = []
    for i in items:
        left = fmt_duration(i["due"] - now)
        if i["kind"] == "timer":
            label = f"{i['text']} timer" if i["text"] else f"{fmt_duration(i['length'])} timer"
            parts.append(f"a {label} with {left} left")
        elif i["kind"] == "alarm":
            parts.append(f"an alarm in {left}")
        else:
            parts.append(f"a reminder to {i['text']} in {left}" if i["text"] else f"a reminder in {left}")
    joiner = persona.natural_join(parts) if persona else ", ".join(parts)
    return f"You've got {joiner}."


def active_timers():
    """For the dashboard's timer panel: [{'id', 'kind', 'label', 'due'}]."""
    with _lock:
        items = sorted(_items, key=lambda i: i["due"])
    out = []
    for i in items:
        if i["kind"] == "timer":
            label = i["text"] + " timer" if i["text"] else fmt_duration(i["length"]) + " timer"
        elif i["kind"] == "alarm":
            label = "Alarm"
        else:
            label = i["text"] or "Reminder"
        out.append({"id": i["id"], "kind": i["kind"], "label": label, "due": i["due"]})
    return out


# =============================================================================
# SECTION 5: WEATHER AND "SET MY CITY"
# =============================================================================
WEATHER_CODES = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "heavy showers", 85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms",
    96: "thunderstorms with hail", 99: "thunderstorms with hail",
}
_RAINY = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82}
_weather_cache = {}

OFFLINE_NOTE = "I can't reach the internet right now, so no live data — everything else still works offline."


def geocode(name):
    """Look a place up. Returns {'name','label','lat','lon'} or None."""
    url = "https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": name, "count": 1, "language": "en", "format": "json"})
    results = fetch_json(url).get("results") or []
    if not results:
        return None
    r = results[0]
    label = r["name"] if r.get("country_code") == "GB" else ", ".join(x for x in (r["name"], r.get("country")) if x)
    return {"name": r["name"], "label": label, "lat": r["latitude"], "lon": r["longitude"]}


def get_forecast(lat, lon):
    key = (round(lat, 2), round(lon, 2))
    cached = _weather_cache.get(key)
    if cached and time.time() - cached[0] < WEATHER_CACHE_SECONDS:
        return cached[1]
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        "timezone": "auto", "forecast_days": 3, "wind_speed_unit": "mph"})
    data = fetch_json(url)
    _weather_cache[key] = (time.time(), data)
    return data


def weather_tip(temp, rain, wind, code):
    if code in (95, 96, 99):
        return " Best to stay in if you can."
    if rain >= 60 or code in _RAINY:
        return " You'll want a brolly."
    if temp <= 3:
        return " Wrap up warm — it's properly cold."
    if temp >= 25:
        return " Shorts weather."
    if wind >= 25:
        return " It's rather blustery out there."
    return ""


def match_weather(t):
    return bool(re.search(
        r"\b(?:weather|forecast)\b|\bhow (?:hot|cold|warm|chilly) is it\b|\bis it (?:going to |gonna )?"
        r"(?:rain|raining|snow|snowing|sunny|cold|hot|warm|windy)\b|\b(?:will|going to) it (?:rain|snow)\b|"
        r"\bdo i need (?:a|an|my) (?:umbrella|coat|jacket|brolly)\b|\bshould i (?:take|bring|wear) (?:a|an|my) "
        r"(?:umbrella|coat|jacket|brolly)\b|\bis it (?:going to )?(?:chuck|pour|pouring)", t))


def _place_from(t):
    m = re.search(r"\b(?:in|for|at|near)\s+(?!the\b|my\b|a\b|an\b|this\b|that\b|case\b)([a-z][a-z .'-]*?)"
                  r"(?:\s+(?:today|tomorrow|tonight|now|right now|please|this (?:morning|afternoon|evening|weekend)))*\s*$", t)
    return m.group(1).strip() if m else None


def run_weather(text):
    t = norm(text)
    day = 2 if "day after tomorrow" in t else 1 if "tomorrow" in t else 0
    place = _place_from(t)
    try:
        if place:
            geo = geocode(place)
            if not geo:
                return f"I couldn't find a place called {place}. Try the city name on its own, like 'weather in Leeds'."
        else:
            home = load_settings().get("home_city")
            if not home:
                return "Which city should I check? Say 'set my city to Leeds' and I'll remember it, or ask 'weather in Paris'."
            geo = home
        data = get_forecast(geo["lat"], geo["lon"])
        cur, daily = data["current"], data["daily"]
        hi, lo = round(daily["temperature_2m_max"][day]), round(daily["temperature_2m_min"][day])
        rain = round(daily["precipitation_probability_max"][day] or 0)
        code = int(daily["weather_code"][day] if day else cur["weather_code"])
        desc = WEATHER_CODES.get(code, "mixed conditions")
        temp, feels, wind = round(cur["temperature_2m"]), round(cur["apparent_temperature"]), cur["wind_speed_10m"]
    except NETWORK_ERRORS:
        return OFFLINE_NOTE
    except (KeyError, IndexError, TypeError):
        return "The weather service gave me something I couldn't make sense of. Try again in a minute?"
    place_name = geo.get("label") or geo.get("name") or "your area"
    tip = weather_tip(hi if day else temp, rain, wind, code)
    if day:
        when = "Tomorrow" if day == 1 else "The day after tomorrow"
        return f"{when} in {place_name}: {desc}, between {lo} and {hi} degrees, with a {rain} percent chance of rain.{tip}"
    feels_text = f" Feels like {feels}." if abs(feels - temp) >= 2 else ""
    options = [
        "It's <t> degrees in <p> right now, with <d>.<f> Today's high is <hi>, low <lo>, and a <r> percent chance of rain.<tip>",
        "<p> is looking <d> at the moment — <t> degrees.<f> Expect a high of <hi> and a low of <lo>, with a <r> percent chance of rain.<tip>",
    ]
    return _say("weather", options, t=temp, p=place_name, d=desc, f=feels_text, hi=hi, lo=lo, r=rain, tip=tip)


_SET_CITY_RES = [
    re.compile(r"\b(?:set|change|update|save)\s+(?:my\s+)?(?:home\s+)?(?:city|town|location|home)\s+(?:to|as)\s+(?P<c>.+)$"),
    re.compile(r"\bmy (?:home )?(?:city|town|location) is\s+(?P<c>.+)$"),
    re.compile(r"\bi live in\s+(?P<c>.+)$"),
]


def match_set_city(t):
    return any(p.search(t) for p in _SET_CITY_RES)


def run_set_city(text):
    t = norm(text)
    name = next((p.search(t).group("c") for p in _SET_CITY_RES if p.search(t)), "").strip()
    if not name:
        return "Which city should I remember?"
    try:
        geo = geocode(name)
    except NETWORK_ERRORS:
        return OFFLINE_NOTE + " Say it again once you're online and I'll save it."
    if not geo:
        return f"I couldn't find a place called {name}. Try just the city name."
    settings = load_settings()
    settings["home_city"] = geo
    save_settings(settings)
    return _say("city_set", ["Done. I'll use <c> for the weather from now on.",
                             "Lovely — <c> it is. I'll remember that."], c=geo["label"])


# =============================================================================
# SECTION 6: NEWS HEADLINES
# =============================================================================
def match_news(t):
    return bool(re.search(r"\b(?:news|headlines)\b", t))


def parse_rss(data, limit=NEWS_HEADLINES):
    """Pull headlines out of an RSS feed. Refuses feeds with DOCTYPE/ENTITY tricks."""
    if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", data, re.I):
        raise ValueError("unsafe xml")
    root = ET.fromstring(data)
    titles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if title and title not in titles:
            titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def run_news(text):
    t = norm(text)
    topic = next((NEWS_TOPIC_WORDS[w] for w in t.split() if w in NEWS_TOPIC_WORDS), "top")
    try:
        titles = parse_rss(fetch_bytes(NEWS_FEEDS[topic]))
    except NETWORK_ERRORS + (ET.ParseError,):
        return OFFLINE_NOTE
    if not titles:
        return "The news feed came back empty. Try again in a little while."
    ordinals = ["First", "Second", "Third", "Fourth", "Fifth"]
    spoken = []
    for index, title in enumerate(titles):
        lead = "And finally" if index == len(titles) - 1 and index > 0 else ordinals[index]
        spoken.append(f"{lead}, {title}{'' if title[-1] in '.!?' else '.'}")
    where = "" if topic == "top" else f" {topic}"
    intro = _say("news", ["Here's what the BBC is leading with<w>.", "Right, the latest<w> headlines from the BBC.",
                          "Top stories<w>, according to the BBC."], w=(" in" + where) if where else "")
    return intro + " " + " ".join(spoken)


# =============================================================================
# SECTION 7: PC CONTROLS (volume, screenshot, lock, battery)
# =============================================================================
VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP = 0xAD, 0xAE, 0xAF


def _press_key(vk, times=1):
    """Press a keyboard key (Windows). Each volume key press changes the volume by 2 percent."""
    if not IS_WINDOWS:
        raise Unsupported
    import ctypes
    for _ in range(times):
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
        time.sleep(0.004)


def match_volume(t):
    return bool(re.search(r"\b(?:volume|louder|quieter|unmute|mute|too (?:loud|quiet)|"
                          r"turn (?:it|the (?:sound|music|volume|audio)) (?:up|down)|sound (?:up|down))\b", t))


def run_volume(text):
    t = norm(text)
    try:
        absolute = re.search(r"\b(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?(?:\s|$)", t) or re.search(r"\bvolume\s+(\d{1,3})\b", t)
        if absolute:
            level = max(0, min(100, int(absolute.group(1))))
            _press_key(VK_VOLUME_DOWN, 50)
            _press_key(VK_VOLUME_UP, round(level / 2))
            return f"Volume set to about {level} percent."
        if re.search(r"\bunmute\b", t) or re.search(r"\bmute\b", t):
            _press_key(VK_VOLUME_MUTE, 1)
            return "Unmuting." if "unmute" in t else "Muted. Say 'unmute' to bring the sound back."
        small = bool(re.search(r"\b(?:bit|little|slightly|touch|notch)\b", t))
        big = bool(re.search(r"\b(?:lot|much|loads|way|max)\b", t))
        steps = 2 if small else 12 if big else 5
        if re.search(r"\b(?:down|quieter|lower|decrease|reduce|too loud|softer)\b", t):
            _press_key(VK_VOLUME_DOWN, steps)
            return _say("vol_down", ["Turning it down.", "Quieter it is.", "Down a touch."])
        _press_key(VK_VOLUME_UP, steps)
        return _say("vol_up", ["Turning it up.", "Louder coming up.", "Up it goes."])
    except Unsupported:
        return "Volume control only works on Windows, I'm afraid."


def _take_screenshot(path):
    """Capture every screen into a PNG using Windows PowerShell (nothing to install)."""
    if not IS_WINDOWS:
        raise Unsupported
    script = "; ".join([
        "Add-Type -TypeDefinition 'using System.Runtime.InteropServices; public class TitanDpi "
        "{ [DllImport(\"user32.dll\")] public static extern bool SetProcessDPIAware(); }'",
        "[void][TitanDpi]::SetProcessDPIAware()",
        "Add-Type -AssemblyName System.Windows.Forms, System.Drawing",
        "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen",
        "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height",
        "$g = [System.Drawing.Graphics]::FromImage($bmp)",
        "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)",
        "$bmp.Save($env:TITAN_SHOT, [System.Drawing.Imaging.ImageFormat]::Png)",
        "$g.Dispose(); $bmp.Dispose()",
    ])
    env = dict(os.environ, TITAN_SHOT=str(path))
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0 or not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise RuntimeError((result.stderr or "no image was written").strip().splitlines()[0][:120])


def match_screenshot(t):
    return bool(re.search(r"\b(?:screenshot|screen shot|screen capture|capture (?:my |the )?screen|"
                          r"take a picture of (?:my |the )?screen)\b", t))


def run_screenshot(text):
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOT_DIR / f"titan_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"
    try:
        _take_screenshot(path)
    except Unsupported:
        return "Screenshots only work on Windows, I'm afraid."
    except (RuntimeError, OSError, subprocess.SubprocessError) as err:
        return f"The screenshot didn't work: {err}"
    return _say("shot", ["Got it. The screenshot's in the screenshots folder: <f>.",
                         "Screenshot taken — it's saved as <f> in the screenshots folder."], f=path.name)


def lock_screen():
    if not IS_WINDOWS:
        raise Unsupported
    import ctypes
    ctypes.windll.user32.LockWorkStation()


def match_lock(t):
    return bool(re.search(r"\block (?:the |my )?(?:pc|computer|laptop|screen|workstation|windows|it|me out)\b|^lock$|"
                          r"\block up\b", t))


def run_lock(text):
    try:
        lock_screen()
    except Unsupported:
        return "Locking the screen only works on Windows, I'm afraid."
    return _say("lock", ["Locking the screen. See you shortly.", "Locked. I'll keep an eye on things while you're away."])


def match_battery(t):
    return bool(re.search(r"\bbattery\b|\bam i plugged in\b|\bis it charging\b|\bpower left\b", t))


def run_battery(text):
    if psutil is None or not hasattr(psutil, "sensors_battery"):
        return "I can't read the battery without the psutil package."
    battery = psutil.sensors_battery()
    if battery is None:
        return "I can't find a battery, so I'd guess you're on a desktop."
    percent = round(battery.percent)
    if battery.power_plugged:
        state = "plugged in" + (" and fully charged" if percent >= 99 else " and charging")
        return _say("bat_plug", ["Battery's at <p> percent, <s>.", "You're on <p> percent and <s>."], p=percent, s=state)
    left = ""
    secs = battery.secsleft
    if isinstance(secs, (int, float)) and secs > 0:
        left = f" That's roughly {fmt_duration(secs)} left."
    warning = " You might want to plug in soon." if percent <= 20 else ""
    return _say("bat", ["Battery's at <p> percent and not plugged in.<l><w>",
                        "You're on <p> percent, running on battery.<l><w>"], p=percent, l=left, w=warning)


# =============================================================================
# SECTION 8: ALERTS FOR THE DASHBOARD
# =============================================================================
def pop_alerts():
    """Take every waiting alert (oldest first)."""
    with _lock:
        out = list(_alerts)
        _alerts.clear()
    return out


def wait_for_alerts(timeout=20):
    """Like pop_alerts, but waits up to `timeout` seconds for one to arrive."""
    with _alert_cond:
        if not _alerts:
            _alert_cond.wait(timeout)
        out = list(_alerts)
        _alerts.clear()
    return out


# =============================================================================
# SECTION 9: THE SKILL REGISTRY  (order matters: first match wins)
# =============================================================================
class Skill:
    def __init__(self, name, description, match, run):
        self.name, self.description, self.match, self.run = name, description, match, run


SKILLS = [
    Skill("cleanup", "Scan one explicitly named folder for large files, list them, and move a chosen file to the Recycle Bin only after a separate exact confirmation",
          match_cleanup, run_cleanup),
    Skill("set_city", "Remember the user's home city for the weather ('set my city to Leeds')", match_set_city, run_set_city),
    Skill("timers", "Set, list or cancel timers, alarms and reminders ('timer for 10 minutes', 'remind me at 3pm to call mum')",
          match_timers, run_timers),
    Skill("volume", "Change the PC volume: up, down, mute, or set to a number", match_volume, run_volume),
    Skill("screenshot", "Take a screenshot of the screen", match_screenshot, run_screenshot),
    Skill("lock", "Lock the PC screen", match_lock, run_lock),
    Skill("battery", "Report the laptop battery level and charging state", match_battery, run_battery),
    Skill("weather", "Weather or forecast, for the home city or any place ('weather in Paris tomorrow')", match_weather, run_weather),
    Skill("news", "Read the latest BBC headlines, optionally by topic (tech, world, sport...)", match_news, run_news),
    Skill("search", "Search the web, YouTube or Wikipedia in the browser ('search youtube for cats')", match_search, run_search),
    Skill("open", "Open an app, folder or website: notepad, chrome, downloads, youtube, bbc.co.uk", match_open, run_open),
]
SKILL_NAMES = [s.name for s in SKILLS]


def find_skill(text):
    t = norm(text)
    for skill in SKILLS:
        try:
            if skill.match(t):
                return skill
        except Exception:
            continue
    return None


def run_skill(name, text):
    for skill in SKILLS:
        if skill.name == name:
            return skill.run(text)
    raise KeyError(name)


def router_lines():
    """One line per skill, for the AI router prompt."""
    return "\n".join(f"- {s.name}: {s.description}" for s in SKILLS)
