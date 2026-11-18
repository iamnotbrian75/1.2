#!/usr/bin/env python3
"""
TITAN AUTONOMY - Titan acting on its own, within safe limits
============================================================
  1 Multi-step tasks   "task: get me set up for revision" -> Titan plans and
                       chains its own actions with one tool, do("<command>").
                       Each command goes through Titan's normal command
                       matching, and blocked actions (deleting files, locking
                       the PC, editing code, website inspection) are refused.
                       Supervised mode allows 6 steps, autonomous mode 25.
  2 Self-check         Before replying, Titan compares its draft with what the
                       actions actually returned and fixes unsupported claims.
  3 Watchdog           Every minute: battery, memory, CPU, disk and Ollama.
                       Speaks up only when something needs attention.
  4 Heartbeat          Every 30 minutes, surfaces at most ONE useful item.
  5 Daily briefing     Weather, headlines, reminders and laptop health at a set
                       time (default 08:00), or "give me my briefing".
  6 Standing goals     "every morning at 8 check the weather and the news" ->
                       runs as a task each day and reports back.
  7 Routine learning   Notices a sequence of commands repeated in 3 separate
                       sessions and SUGGESTS saving it as a routine. Nothing is
                       adopted until you say "adopt routine 1". Only command
                       names and their wording are logged, locally.

Settings, goals, routines and the command log live in titan_autonomy.json
(personal data: git-ignored). T.v1.1.py wires this module in with configure().
"""
import json
import re
import threading
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

import titan_skills as skills

# =============================================================================
# SECTION 1: CONFIG
# =============================================================================
BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "titan_autonomy.json"

SUPERVISED_ROUNDS = 6
AUTONOMOUS_ROUNDS = 25
WATCH_SECONDS = 60
HEARTBEAT_SECONDS = 30 * 60
USAGE_LIMIT = 300
SESSION_GAP_SECONDS = 15 * 60
ROUTINE_REPEATS = 3
WARN_COOLDOWN_SECONDS = 30 * 60

DEFAULTS = {
    "mode": "supervised",          # or "autonomous"
    "self_check": True,
    "briefing_on": True,
    "briefing_time": "08:00",
    "last_briefing": "",
    "watchdog_on": True,
    "learn_routines": True,
    "goals": [],                   # {"id", "time": "HH:MM", "task", "last_run": "YYYY-MM-DD"}
    "routines": {},                # name -> [command text, ...]
    "suggestions": [],             # [{"steps": [...], "commands": [...]}]
    "dismissed": [],               # command-name sequences not to suggest again
    "usage": [],                   # [{"t": epoch, "cmd": name, "text": wording}]
}

_config = {
    "execute": None,               # function(command_text) -> result text  (set by T.v1.1.py)
    "is_action": None,             # function(text) -> True if it's a Titan command (set by T.v1.1.py)
    "url": "http://127.0.0.1:11434/api/chat",
    "model": "qwen2.5-coder:3b",
    "keep_alive": "5m",
}
_lock = threading.RLock()
_state = None
_thread = None
_stop = threading.Event()


def configure(execute, is_action=None, url=None, model=None, keep_alive=None):
    _config["execute"] = execute
    _config["is_action"] = is_action
    if url:
        _config["url"] = url
    if model:
        _config["model"] = model
    if keep_alive:
        _config["keep_alive"] = keep_alive


# =============================================================================
# SECTION 2: SAVED STATE
# =============================================================================
def _load():
    global _state
    with _lock:
        if _state is None:
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            _state = {**json.loads(json.dumps(DEFAULTS)), **(data if isinstance(data, dict) else {})}
        return _state


def _save():
    with _lock:
        try:
            skills._atomic_write(STATE_FILE, json.dumps(_load(), indent=2))
        except OSError:
            pass


def settings():
    with _lock:
        return dict(_load())


def _reset_for_tests(path):
    """Point the module at a temporary file and start from defaults."""
    global STATE_FILE, _state
    with _lock:
        STATE_FILE = Path(path)
        _state = None


# =============================================================================
# SECTION 3: MULTI-STEP TASKS (agent loop + self-check)
# =============================================================================
TOOLS = [{
    "type": "function",
    "function": {
        "name": "do",
        "description": (
            "Carry out ONE Titan action, written exactly as the user would say it to Titan, for example "
            "'set a timer for 25 minutes', 'open youtube', \"what's the weather\", 'news headlines', "
            "'take a note that the essay is due Friday', 'turn the volume down', 'battery'. Returns what happened."
        ),
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "One spoken-style Titan command."}},
            "required": ["command"],
        },
    },
}]


def _chat(messages, tools=None, num_predict=400, temperature=0.2, timeout=180):
    payload = {
        "model": _config["model"], "stream": False, "think": False, "keep_alive": _config["keep_alive"],
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": 4096},
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
    request = urllib.request.Request(_config["url"], data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8")).get("message", {})


def _json_objects(text):
    """Every top-level JSON object in the text (handles nesting, unlike a regex)."""
    decoder, found, i = json.JSONDecoder(), [], 0
    text = text or ""
    while True:
        i = text.find("{", i)
        if i < 0:
            return found
        try:
            obj, end = decoder.raw_decode(text, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        i = end


def _command_from(item):
    """Pull the command out of the many shapes a small model writes a tool call in."""
    args = item.get("arguments", item.get("parameters", {}))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {"command": args}
    command = args.get("command") if isinstance(args, dict) else None
    if isinstance(command, dict):                 # {"type": "string", "description": "open youtube"}
        command = command.get("value") or command.get("description")
    name = str(item.get("name", "do"))
    if not command and name not in ("do", "") and not args:
        command = name                            # {"name": "open youtube", "arguments": {}}
    return command.strip() if isinstance(command, str) and command.strip() else None


def _text_tool_calls(content):
    """Small local models often write the tool call as JSON text instead of using the API."""
    calls = []
    for item in _json_objects(content):
        command = _command_from(item)
        if command:
            calls.append({"function": {"name": "do", "arguments": {"command": command}}})
    return calls


def _looks_like_json(text):
    return bool(_json_objects(text)) and text.lstrip().startswith(("{", "["))


def _task_system(max_rounds):
    return (
        "You are Titan's task planner on the user's Windows laptop. Achieve the user's goal by calling the "
        "`do` tool once per action, phrased as a short spoken Titan command. Use each result before deciding "
        f"the next step. You have at most {max_rounds} actions. Only do what the goal asks for. "
        "Available actions: the time, weather, news headlines, timers/alarms/reminders, opening apps, folders "
        "and websites, web/YouTube searches, volume, screenshots, battery, CPU, memory, system diagnosis, "
        "taking notes and reading notes. You can NOT delete files, lock the PC, change code, or inspect "
        "websites; if the goal needs those, say the user must ask for them directly. "
        "Do EVERY part of the goal, one action at a time. To act, reply with only this JSON and nothing else:\n"
        '{"name": "do", "arguments": {"command": "set a timer for 25 minutes"}}\n'
        "When every part is finished, reply in plain words (no JSON): one to three natural spoken sentences "
        "saying what you did and anything the user should know. Never claim an action happened unless its "
        "result says so."
    )


def _self_check(goal, steps, draft):
    evidence = "\n".join(f"{i}. do({cmd!r}) -> {str(result)[:300]}" for i, (cmd, result) in enumerate(steps, 1))
    messages = [
        {"role": "system", "content": (
            "You check an assistant's reply before it is spoken aloud. Compare the DRAFT with the ACTION "
            "RESULTS. Correct or remove anything the results do not support, such as saying something was done "
            "when its result shows it failed, was blocked or did not run. Keep the warm, concise British butler "
            "tone: one to four spoken sentences, no lists, no markdown. Reply with the corrected reply only.")},
        {"role": "user", "content": f"GOAL: {goal}\nACTION RESULTS:\n{evidence}\nDRAFT: {draft}"},
    ]
    try:
        fixed = str(_chat(messages, num_predict=220, temperature=0).get("content", "")).strip()
    except Exception:
        return draft
    return fixed or draft


def run_task(goal, max_rounds=None):
    """Plan and carry out a multi-step goal. Returns (reply, steps)."""
    execute = _config["execute"]
    if execute is None:
        return "My task planner isn't connected yet.", []
    s = settings()
    if max_rounds is None:
        max_rounds = AUTONOMOUS_ROUNDS if s["mode"] == "autonomous" else SUPERVISED_ROUNDS
    messages = [{"role": "system", "content": _task_system(max_rounds)}, {"role": "user", "content": goal}]
    steps, seen, draft = [], Counter(), ""
    for _ in range(max_rounds):
        try:
            message = _chat(messages, TOOLS)
        except Exception as err:
            if not steps:
                return f"I couldn't reach my language model to plan that: {err}", steps
            break
        content = str(message.get("content", ""))
        calls = message.get("tool_calls") or _text_tool_calls(content)
        if not calls:
            draft = "" if _looks_like_json(content) else content.strip()
            break
        messages.append(message)
        refused_all = True
        for call in calls[:3]:
            args = call.get("function", {}).get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            command = (_command_from({"name": "do", "arguments": args}) or "")[:200]
            key = command.casefold()
            seen[key] += 1
            if not command:
                result = "No command given."
            elif seen[key] > 1:
                result = "Already done. Don't repeat it."
            elif len(steps) >= max_rounds:
                result = "Step limit reached. Finish now."
            else:
                refused_all = False
                try:
                    result = execute(command)
                except Exception as err:
                    result = f"That action failed: {err}"
                steps.append((command, str(result)))
            done = "; ".join(cmd for cmd, _ in steps)
            messages.append({"role": "tool", "tool_name": "do", "content": (
                f"{str(result)[:1000]}\n(Done so far: {done or 'nothing'}. If every part of the goal is done, "
                "stop calling tools and answer in plain words now.)")})
        if refused_all:
            break                          # only repeats left: the work is done
    if not draft:
        if steps:
            draft = "All done. " + " ".join(result for _, result in steps[:8])
        else:
            draft = "I couldn't work out any actions for that goal. Could you put it another way?"
    if s["self_check"] and steps:
        checked = _self_check(goal, steps, draft)
        draft = draft if _looks_like_json(checked) else checked
    return draft, steps


def run_chain(parts):
    """'X and then Y': every part is already a known command, so just run them in
    order - no AI planning needed, nothing gets skipped."""
    execute = _config["execute"]
    results = []
    for part in parts:
        try:
            results.append(str(execute(part)))
        except Exception as err:
            results.append(f"{part} failed: {err}")
    return " ".join(results)


# =============================================================================
# SECTION 4: LAPTOP HEALTH (watchdog + heartbeat + briefing share these)
# =============================================================================
_watch = {"ram_high": 0, "cpu_high": 0, "ollama_down": 0, "warned": {}, "last_heartbeat": time.time()}


def _top_process():
    if psutil is None:
        return ""
    best, best_mem = "", 0
    for proc in psutil.process_iter(["name", "memory_info"]):
        try:
            mem = proc.info["memory_info"].rss if proc.info["memory_info"] else 0
        except Exception:
            continue
        if mem > best_mem:
            best, best_mem = proc.info["name"] or "", mem
    return best.removesuffix(".exe")


def _ollama_up():
    try:
        with urllib.request.urlopen(_config["url"].replace("/api/chat", "/api/tags"), timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def _warn_once(key, text, cooldown=WARN_COOLDOWN_SECONDS):
    now = time.time()
    if now - _watch["warned"].get(key, 0) < cooldown:
        return False
    _watch["warned"][key] = now
    skills.push_alert(text, "watchdog")
    return True


def _clear(key):
    _watch["warned"].pop(key, None)


def health_summary():
    """One or two spoken sentences about the laptop, for the briefing."""
    if psutil is None:
        return "I can't read the laptop's health without psutil."
    parts = []
    mem = psutil.virtual_memory()
    parts.append(f"memory is at {round(mem.percent)} percent")
    try:
        disk = psutil.disk_usage("C:\\" if Path("C:\\").exists() else "/")
        parts.append(f"the drive has {disk.free / 1024 ** 3:.0f} gigabytes free")
    except OSError:
        pass
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery is not None:
        parts.append(f"battery is at {round(battery.percent)} percent{' and charging' if battery.power_plugged else ''}")
    return "Laptop check: " + ", ".join(parts) + "."


def watchdog_check():
    """Run every minute. Speaks up only when something needs attention."""
    if psutil is None or not settings()["watchdog_on"]:
        return
    battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if battery is not None and not battery.power_plugged and battery.percent <= 10:
        _warn_once("battery_critical", f"Battery's down to {round(battery.percent)} percent. Please plug me in now, or I'll be going dark shortly.", 10 * 60)
    elif battery is not None and not battery.power_plugged and battery.percent <= 20:
        _warn_once("battery_low", f"Battery's at {round(battery.percent)} percent. Might be time to find the charger.")
    elif battery is not None and battery.power_plugged:
        _clear("battery_low"), _clear("battery_critical")

    mem = psutil.virtual_memory().percent
    _watch["ram_high"] = _watch["ram_high"] + 1 if mem >= 90 else 0
    if _watch["ram_high"] >= 3:
        hog = _top_process()
        _warn_once("ram", f"Memory's been above 90 percent for a few minutes{f'; {hog} is the biggest user' if hog else ''}. Closing something would help me think faster.")

    cpu = psutil.cpu_percent(interval=None)
    _watch["cpu_high"] = _watch["cpu_high"] + 1 if cpu >= 95 else 0
    if _watch["cpu_high"] >= 5:
        _warn_once("cpu", "The processor's been flat out for five minutes. Something may be stuck; want me to run a system diagnosis?")

    try:
        disk = psutil.disk_usage("C:\\" if Path("C:\\").exists() else "/")
        if disk.free / disk.total < 0.08:
            _warn_once("disk", f"The drive's nearly full: only {disk.free / 1024 ** 3:.0f} gigabytes left. Ask me to find large files in Downloads.", 6 * 3600)
    except OSError:
        pass

    if _ollama_up():
        _watch["ollama_down"] = 0
        _clear("ollama")
    else:
        _watch["ollama_down"] += 1
        if _watch["ollama_down"] >= 3:
            _warn_once("ollama", "My language model, Ollama, has stopped responding. Commands still work, but I can't chat until it's back.", 60 * 60)


def heartbeat_item():
    """At most ONE useful, non-urgent thing to mention. Returns text or None."""
    s = settings()
    if s["suggestions"]:
        steps = s["suggestions"][0]["steps"]
        return (f"I've noticed you often do this in a row: {', then '.join(steps)}. "
                "Say 'adopt routine 1' and I'll save it so you can run it in one go.")
    soon = [i for i in skills.active_timers() if i["kind"] == "reminder" and 0 < i["due"] - time.time() <= 3600]
    if soon:
        item = min(soon, key=lambda i: i["due"])
        minutes = max(1, round((item["due"] - time.time()) / 60))
        return f"Heads up: your reminder to {item['label']} is in {minutes} minutes."
    if psutil is not None:
        days = (time.time() - psutil.boot_time()) / 86400
        if days >= 5:
            return f"The laptop's been running for {days:.0f} days without a restart. A reboot would clear out the cobwebs."
    return None


# =============================================================================
# SECTION 5: DAILY BRIEFING AND STANDING GOALS
# =============================================================================
def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _time_of(value):
    hour, minute = (int(x) for x in value.split(":"))
    return hour, minute


def briefing():
    """Compose the daily briefing from Titan's own skills (no AI model needed)."""
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    name = skills._user_name()
    lines = [f"Good {part}{', ' + name if name and name != 'you' else ''}. Here's your briefing."]
    for label, fn in (("weather", lambda: skills.run_weather("weather")),
                      ("news", lambda: skills.run_news("news headlines")),
                      ("reminders", skills.describe_timers)):
        try:
            lines.append(str(fn()))
        except Exception as err:
            lines.append(f"I couldn't get the {label}: {err}")
    lines.append(health_summary())
    return " ".join(lines)


def _due_today(hhmm, last_run, now):
    hour, minute = _time_of(hhmm)
    return last_run != _today() and (now.hour, now.minute) >= (hour, minute)


def _run_goal(goal):
    reply, _ = run_task(goal["task"])
    skills.push_alert(f"Goal report: {reply}", "goal")


def scheduled_check(now=None):
    """Run once a minute: deliver the briefing and start any standing goals that are due."""
    now = now or datetime.now()
    with _lock:
        s = _load()
        deliver = s["briefing_on"] and _due_today(s["briefing_time"], s["last_briefing"], now)
        if deliver:
            s["last_briefing"] = _today()
        due_goals = [g for g in s["goals"] if _due_today(g["time"], g.get("last_run", ""), now)]
        for goal in due_goals:
            goal["last_run"] = _today()
        if deliver or due_goals:
            _save()
    if deliver:
        skills.push_alert(briefing(), "briefing")
    for goal in due_goals:
        threading.Thread(target=_run_goal, args=(goal,), name="titan-goal", daemon=True).start()


# =============================================================================
# SECTION 6: ROUTINE LEARNING
# =============================================================================
def record_command(command, text):
    """Log one successful command (name + wording only) for routine detection."""
    with _lock:
        s = _load()
        if not s["learn_routines"]:
            return
        s["usage"].append({"t": time.time(), "cmd": command, "text": str(text)[:120]})
        del s["usage"][:-USAGE_LIMIT]
        _detect_routines(s)
        _save()


def _sessions(usage):
    sessions, current, last = [], [], None
    for entry in usage:
        if last is not None and entry["t"] - last > SESSION_GAP_SECONDS:
            sessions.append(current)
            current = []
        current.append(entry)
        last = entry["t"]
    if current:
        sessions.append(current)
    return sessions


def _detect_routines(s):
    """Find command sequences (2-4 long) that appear in at least 3 separate sessions."""
    counts, latest_text = Counter(), {}
    for session in _sessions(s["usage"]):
        found = set()
        names = [e["cmd"] for e in session]
        for size in (4, 3, 2):
            for i in range(len(names) - size + 1):
                seq = tuple(names[i:i + size])
                if len(set(seq)) == size:
                    found.add(seq)
                    latest_text[seq] = [e["text"] for e in session[i:i + size]]
        counts.update(found)
    known = {tuple(r["commands"]) for r in s["suggestions"]} | {tuple(d) for d in s["dismissed"]}
    adopted = {tuple(v.get("commands", [])) for v in s["routines"].values() if isinstance(v, dict)}
    for seq, count in sorted(counts.items(), key=lambda kv: (-len(kv[0]), -kv[1])):
        if count < ROUTINE_REPEATS or seq in known or seq in adopted:
            continue
        if any(set(seq) <= set(k) for k in known | adopted):
            continue                       # a longer routine already covers it
        s["suggestions"].append({"commands": list(seq), "steps": latest_text[seq]})
        known.add(seq)


def _routine_name(steps):
    words = re.findall(r"[a-z]+", " ".join(steps).casefold())
    common = [w for w in words if w not in {"the", "a", "for", "to", "me", "my", "what", "whats", "s", "is", "set", "of", "and"}]
    return "-".join(dict.fromkeys(common))[:24].strip("-") or "routine"


def run_routine(name):
    execute = _config["execute"]
    s = settings()
    routine = s["routines"].get(name)
    if routine is None or execute is None:
        return f"I don't have a routine called {name}. Say 'list routines' to see them."
    results = []
    for step in routine["steps"]:
        try:
            results.append(str(execute(step)))
        except Exception as err:
            results.append(f"{step} failed: {err}")
    return f"Running your {name} routine. " + " ".join(results)


# =============================================================================
# SECTION 7: SPOKEN COMMANDS FOR ALL OF THE ABOVE
# =============================================================================
_TASK_RE = re.compile(r"^(?:task|goal|mission|plan)\s*[:\-]\s*(.+)$", re.I | re.S)
_SETUP_RE = re.compile(r"^(?:please\s+)?(?:get|help)\s+me\s+(?:ready|set\s*up|sorted|prepared)\s+for\b", re.I)
_CHAIN_RE = re.compile(r"\b(?:and then|then also|after that|and after that)\b|,\s*then\b", re.I)
_EVERY_RE = re.compile(r"^(?:please\s+)?every\s+(day|morning|evening|night|afternoon)\b\s*(.*)$", re.I | re.S)
_DEFAULT_GOAL_TIMES = {"day": "08:00", "morning": "08:00", "afternoon": "13:00", "evening": "18:00", "night": "21:00"}


def is_task_request(text):
    t = text.strip()
    if _TASK_RE.match(t) or _SETUP_RE.match(t):
        return True
    if not _CHAIN_RE.search(t):
        return False
    # "set a timer for 25 minutes and then open youtube" is a task;
    # "tell me a story and then explain it" is just conversation.
    is_action = _config["is_action"]
    parts = [p.strip(" ,.") for p in _CHAIN_RE.split(t) if p and p.strip(" ,.")]
    return is_action is not None and any(is_action(p) for p in parts)


def _parse_clock(text):
    found = skills.find_clock(text)
    if not found:
        return None, text
    due, start, end = found
    return due.strftime("%H:%M"), (text[:start] + text[end:]).strip(" ,")


def status():
    s = settings()
    goals = len(s["goals"])
    return (f"I'm in {s['mode']} mode ({AUTONOMOUS_ROUNDS if s['mode'] == 'autonomous' else SUPERVISED_ROUNDS} steps per task), "
            f"self-checking is {'on' if s['self_check'] else 'off'}, the watchdog is {'on' if s['watchdog_on'] else 'off'}, "
            f"the daily briefing is {'at ' + s['briefing_time'] if s['briefing_on'] else 'off'}, "
            f"and I have {goals} standing goal{'s' if goals != 1 else ''} and {len(s['routines'])} saved routine{'s' if len(s['routines']) != 1 else ''}.")


def _set(key, value):
    with _lock:
        _load()[key] = value
        _save()


def handle(text):
    """Return Titan's reply if the text is an autonomy command, else None."""
    t = text.strip()
    low = re.sub(r"\s+", " ", t.casefold()).strip(" .!?")

    # ---- modes and switches ----
    if re.search(r"\b(?:autonomy|autonomous) status\b|\bwhat mode are you in\b|\bhow independent are you\b", low):
        return status()
    if re.search(r"\b(?:switch|go|change|set|turn)\b.*\bautonomous\b", low) or low in {"autonomous mode", "go autonomous"}:
        _set("mode", "autonomous")
        return f"Autonomous mode on. I'll take up to {AUTONOMOUS_ROUNDS} steps on a task without checking in. I still won't delete files, lock the PC or change code unless you ask directly."
    if re.search(r"\b(?:switch|go|change|set|turn)\b.*\bsupervised\b", low) or low == "supervised mode":
        _set("mode", "supervised")
        return f"Supervised mode. I'll keep tasks to {SUPERVISED_ROUNDS} steps."
    m = re.search(r"\bturn (on|off) (?:self[- ]?check(?:ing)?|reliability mode)\b|\b(?:self[- ]?check(?:ing)?|reliability mode) (on|off)\b", low)
    if m:
        on = (m.group(1) or m.group(2)) == "on"
        _set("self_check", on)
        return "Self-checking on. I'll double-check my answers against what actually happened." if on else "Self-checking off. Replies will be a little quicker."
    m = re.search(r"\bturn (on|off) (?:the )?(?:watchdog|warnings|health warnings)\b|\b(?:watchdog|warnings) (on|off)\b", low)
    if m:
        on = (m.group(1) or m.group(2)) == "on"
        _set("watchdog_on", on)
        return "Watchdog on. I'll warn you about battery, memory, disk and my model." if on else "Watchdog off. I'll stay quiet unless you ask."

    # ---- briefing ----
    if re.search(r"\b(?:give me|read me|what'?s|start) (?:my |the )?(?:daily |morning )?briefing\b|^(?:daily |morning )?briefing$", low):
        return briefing()
    m = re.search(r"\b(?:set|move|change) (?:my |the )?(?:daily |morning )?briefing\b(.*)$", low)
    if m:
        hhmm, _ = _parse_clock(m.group(1))
        if not hhmm:
            return "What time should I give you the briefing? For example: set my briefing for 7:30am."
        with _lock:
            _load().update(briefing_time=hhmm, briefing_on=True, last_briefing=_today() if datetime.now().strftime("%H:%M") >= hhmm else "")
            _save()
        return f"Done. Your briefing is set for {hhmm} each day."
    m = re.search(r"\bturn (on|off) (?:my |the )?(?:daily |morning )?briefing\b", low)
    if m:
        _set("briefing_on", m.group(1) == "on")
        return "Daily briefing on." if m.group(1) == "on" else "Daily briefing off."

    # ---- standing goals ----
    m = _EVERY_RE.match(t)
    if m:
        period, rest = m.group(1).casefold(), m.group(2).strip(" ,")
        hhmm, task = _parse_clock(rest)
        hhmm = hhmm or _DEFAULT_GOAL_TIMES[period]
        task = re.sub(r"^(?:,|:|-|please)\s*", "", task).strip()
        if len(task) < 4:
            return "What should I do each time? For example: every morning at 8 check the weather and the news."
        with _lock:
            s = _load()
            goal = {"id": max([g["id"] for g in s["goals"]] + [0]) + 1, "time": hhmm, "task": task,
                    "last_run": _today() if datetime.now().strftime("%H:%M") >= hhmm else ""}
            s["goals"].append(goal)
            _save()
        return f"Standing goal {goal['id']} saved: every day at {hhmm}, I'll {task} and report back."
    if re.search(r"\b(?:list|show|what are) (?:my |the )?(?:standing )?goals\b", low):
        goals = settings()["goals"]
        if not goals:
            return "No standing goals yet. Try: every morning at 8 check the weather and the news."
        return "Your standing goals: " + "; ".join(f"{g['id']}: at {g['time']}, {g['task']}" for g in goals) + "."
    m = re.search(r"\b(?:cancel|delete|remove|stop) (?:standing )?goal (\d+)\b", low)
    if m:
        with _lock:
            s = _load()
            before = len(s["goals"])
            s["goals"] = [g for g in s["goals"] if g["id"] != int(m.group(1))]
            _save()
        return f"Goal {m.group(1)} cancelled." if len(s["goals"]) < before else f"I don't have a goal {m.group(1)}."

    # ---- routines ----
    if re.search(r"\b(?:list|show|what are) (?:my |the )?(?:suggested )?routines\b", low):
        s = settings()
        parts = []
        if s["routines"]:
            parts.append("Saved routines: " + "; ".join(f"{n} ({', then '.join(r['steps'])})" for n, r in s["routines"].items()) + ".")
        if s["suggestions"]:
            parts.append("Suggestions: " + "; ".join(f"{i}: {', then '.join(x['steps'])}" for i, x in enumerate(s["suggestions"], 1))
                         + ". Say 'adopt routine 1' to save one.")
        return " ".join(parts) or "No routines yet. Once you repeat the same few commands a few times, I'll suggest one."
    m = re.search(r"\badopt routine (\d+)(?: as ([a-z0-9 -]{2,24}))?\b", low)
    if m:
        with _lock:
            s = _load()
            index = int(m.group(1)) - 1
            if not 0 <= index < len(s["suggestions"]):
                return "I don't have a suggestion with that number. Say 'list routines' to see them."
            suggestion = s["suggestions"].pop(index)
            name = re.sub(r"\s+", "-", (m.group(2) or _routine_name(suggestion["steps"])).strip())
            s["routines"][name] = {"steps": suggestion["steps"], "commands": suggestion["commands"]}
            _save()
        return f"Saved as the '{name}' routine. Say 'run routine {name}', or just '/{name}', to do it all in one go."
    m = re.search(r"\b(?:dismiss|ignore|no thanks to) routine (\d+)\b", low)
    if m:
        with _lock:
            s = _load()
            index = int(m.group(1)) - 1
            if 0 <= index < len(s["suggestions"]):
                s["dismissed"].append(s["suggestions"].pop(index)["commands"])
                _save()
                return "Understood, I won't suggest that one again."
        return "I don't have a suggestion with that number."
    m = re.match(r"^/([a-z0-9-]{2,24})$", low) or re.search(r"\brun (?:my |the )?routine ([a-z0-9-]{2,24})\b", low) \
        or re.search(r"\brun (?:my |the )?([a-z0-9-]{2,24}) routine\b", low)
    if m and (m.group(1) in settings()["routines"] or not low.startswith("/")):
        return run_routine(m.group(1))
    m = re.search(r"\b(?:delete|remove) (?:the )?routine ([a-z0-9-]{2,24})\b", low)
    if m:
        with _lock:
            removed = _load()["routines"].pop(m.group(1), None)
            _save()
        return f"Routine {m.group(1)} deleted." if removed else f"I don't have a routine called {m.group(1)}."
    if re.search(r"\b(?:forget|clear) (?:my )?(?:routines|command history|usage)\b", low):
        with _lock:
            _load().update(usage=[], suggestions=[])
            _save()
        return "Done. I've cleared the command history I use to spot routines. Saved routines are kept."
    m = re.search(r"\b(stop|start) learning (?:my )?routines\b", low)
    if m:
        _set("learn_routines", m.group(1) == "start")
        return "I'll keep an eye out for routines." if m.group(1) == "start" else "I've stopped logging commands for routines."

    # ---- multi-step tasks ----
    if is_task_request(t):
        task_match = _TASK_RE.match(t)
        if not task_match and not _SETUP_RE.match(t):
            parts = [p.strip(" ,.") for p in _CHAIN_RE.split(t) if p and p.strip(" ,.")]
            is_action = _config["is_action"]
            if is_action is not None and all(is_action(p) for p in parts):
                return run_chain(parts)
        goal = task_match.group(1).strip() if task_match else t
        reply, _ = run_task(goal)
        return reply
    return None


# =============================================================================
# SECTION 8: BACKGROUND LOOP
# =============================================================================
def _loop():
    if psutil is not None:
        psutil.cpu_percent(interval=None)           # prime the CPU reading
    while not _stop.wait(WATCH_SECONDS):
        try:
            watchdog_check()
            scheduled_check()
            if time.time() - _watch["last_heartbeat"] >= HEARTBEAT_SECONDS:
                _watch["last_heartbeat"] = time.time()
                item = heartbeat_item()
                if item:
                    skills.push_alert(item, "heartbeat")
        except Exception:
            pass                                    # never let a check kill the loop


def start():
    """Start the watchdog / heartbeat / briefing / goals loop (safe to call twice)."""
    global _thread
    with _lock:
        _load()
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="titan-autonomy", daemon=True)
        _thread.start()


def stop():
    _stop.set()
