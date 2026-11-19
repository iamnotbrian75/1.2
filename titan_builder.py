#!/usr/bin/env python3
"""
TITAN BUILDER - ask how to make something, get a blueprint you can follow
==========================================================================
Clicky-style build guides drawn as blueprints:

  * "how do I wire a boost gauge to my car?"  ->  Titan drafts a guide
    (parts, tools, how the parts connect, numbered steps) with the local
    model, then the dashboard draws every part as a blueprint symbol and a
    pointer flies to each part as Titan talks you through the steps.
  * Hand-checked guides (the E87 front end, TitanDrive head unit) live in
    dashboard/guides/*.json and are marked as checked, not AI drafts.
  * Printable parts made by titan_garage.design() (gauge pod, enclosure,
    bracket) open here too and can still be exported as STL.

AI-drafted guides are clearly labelled as drafts: a 3B model can be wrong
about part numbers, wiring colours or torque figures.

Drafted guides are stored in titan_guides.json (git-ignored).
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import titan_skills as skills

BASE_DIR = Path(__file__).resolve().parent
GUIDES_DIR = BASE_DIR / "dashboard" / "guides"
STORE_FILE = BASE_DIR / "titan_guides.json"
OLLAMA_URL = os.environ.get("TITAN_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
CHAT_MODEL = os.environ.get("TITAN_CHAT_MODEL", "qwen2.5-coder:3b")
# llama3.2 knows more about real-world jobs than the coding model and answers in ~half the time;
# if it isn't installed, drafting falls back to the chat model.
MODEL = os.environ.get("TITAN_BUILDER_MODEL", "llama3.2:3b")
KEEP_ALIVE = os.environ.get("TITAN_KEEP_ALIVE", "5m")
DRAFT_TIMEOUT = 300
MAX_STORED = 25
MAX_PARTS, MAX_STEPS, MAX_LINKS = 14, 10, 20

# Every part is drawn as one of these blueprint symbols.
KINDS = ("module", "board", "screen", "sensor", "gauge", "connector", "cable", "wire", "fuse", "switch",
         "relay", "battery", "light", "bolt", "bracket", "panel", "grille", "bar", "cooler", "fan", "pipe",
         "hose", "filter", "latch", "printed", "tool", "box")
LINKS = ("wire", "pipe", "bolt", "plug", "fits", "flow")
WHO = ("you", "mechanic")
DISCLAIMER_AI = ("AI draft made offline by a small model. Check part numbers, wiring, fuse ratings and "
                 "torque figures against the manual or the seller before you buy or build.")
CAR_CONTEXT = ("The user's car is a 2009 BMW 118d E87 LCI hatchback, UK right-hand drive, N47 2.0 diesel, "
               "automatic climate control. They are on a budget and do DIY work; a mechanic does structural repairs.")
CAR_WORDS = re.compile(r"\b(?:car|bmw|e87|118d|1 series|engine|bumper|bonnet|headlight|obd2?|dash(?:board)?|"
                       r"head ?unit|radio|intake|airbox|exhaust|wheel|tyre|brake|boost|turbo|intercooler)\b")

_lock = threading.RLock()
_store = None
_ui = {}
_jobs = {}                      # guide id -> thread, while drafting

VERBS = (r"build|make|wire(?: up)?|fit|install|assemble|replace|mount|connect|solder|print|fix|repair|change|swap|"
         r"set up|hook up|upgrade|remove|rebuild|convert|modify|add|put in|put on|refit|wrap|paint|clean|service")
ASK = re.compile(
    r"^(?:titan[,\s]+)?(?:please\s+)?(?:"
    rf"how (?:do|can|would|should|could) (?:i|you|we) (?:go about )?(?P<a>(?:{VERBS})\b.+)"
    rf"|(?:show|teach) me how to (?P<b>.+)"
    rf"|walk me through (?:how to )?(?P<c>.+)"
    rf"|(?:help|guide) me (?:to )?(?P<d>(?:{VERBS})\b.+)"
    rf"|(?:draw|make|give) (?:me )?(?:a |the )?(?:blueprint|build guide|wiring diagram|diagram) (?:for|of|to|on|showing)(?: how to)? (?P<e>.+)"
    rf"|(?:blueprint|build guide) (?:for|of) (?P<f>.+)"
    r")$")


# =============================================================================
# SECTION 1: STORE
# =============================================================================
def _load():
    global _store
    with _lock:
        if _store is None:
            try:
                data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            _store = data if isinstance(data, dict) else {}
            _store.setdefault("guides", {})
            _store.setdefault("order", [])
            for guide in _store["guides"].values():          # a restart interrupted these
                if guide.get("state") == "drafting":
                    guide.update(state="failed", error="Titan restarted while drafting. Ask again.")
        return _store


def _save():
    with _lock:
        skills._atomic_write(STORE_FILE, json.dumps(_load(), indent=2))


def _reset_for_tests(path):
    global STORE_FILE, _store
    with _lock:
        STORE_FILE = Path(path)
        _store = None
        _ui.clear()
        _jobs.clear()


def take_ui():
    with _lock:
        out = dict(_ui)
        _ui.clear()
        return out


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:32] or "guide"


# =============================================================================
# SECTION 2: GUIDES (built-in, drafted, printable)
# =============================================================================
def list_guides():
    out = []
    for path in sorted(GUIDES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": path.stem, "title": data.get("title", path.stem), "source": "checked", "state": "ready"})
    with _lock:
        store = _load()
        for gid in reversed(store["order"]):
            guide = store["guides"].get(gid)
            if guide:
                out.append({"id": gid, "title": guide.get("title") or guide.get("question", gid),
                            "source": guide.get("source", "ai"), "state": guide.get("state", "ready")})
    try:
        import titan_garage as garage
        for model_id, spec in garage.custom_models().items():
            out.append({"id": model_id, "title": spec.get("title", model_id), "source": "printable", "state": "ready"})
    except Exception:          # garage is optional for the builder
        pass
    return out


def get_guide(guide_id):
    """A guide ready to draw, or None. Drafts in progress come back with state 'drafting'."""
    if not re.fullmatch(r"[a-z0-9-]{1,48}", guide_id or ""):
        return None
    path = GUIDES_DIR / f"{guide_id}.json"
    if path.is_file():
        try:
            guide = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        guide.update(id=guide_id, source="checked", state="ready")
        return guide
    with _lock:
        guide = _load()["guides"].get(guide_id)
        if guide is not None:
            return json.loads(json.dumps(guide))
    try:
        import titan_garage as garage
        spec = garage.custom_models().get(guide_id)
    except Exception:
        spec = None
    return guide_from_printable(guide_id, spec) if spec else None


def guide_from_printable(guide_id, spec):
    """A garage printable part (gauge pod, enclosure, bracket) as a blueprint guide."""
    parts = []
    for p in spec["parts"]:
        shape = p.get("shape", {})
        dims = ""
        if shape.get("type") == "plate":
            dims = f"{shape['w']:g} × {shape['h']:g} × {shape['t']:g} mm"
        elif shape.get("type") == "tube":
            dims = f"Ø{shape['r_out'] * 2:g} mm × {shape['h']:g} mm"
        parts.append({"id": p["id"], "name": p["name"], "kind": "printed", "qty": 1, "note": dims,
                      "shape": shape, "pos": p.get("pos", [0, 0, 0]), "rot": p.get("rot", [0, 0, 0])})
    ids = [p["id"] for p in parts]
    links = [{"a": ids[i], "b": ids[i + 1], "how": "fits"} for i in range(len(ids) - 1)]
    return {"id": guide_id, "title": spec["title"], "summary": spec.get("subtitle", ""), "source": "printable",
            "state": "ready", "printable": True, "units": "mm", "tools": ["3D printer (PETG or ASA)", "M3/M4 screws"],
            "parts": parts, "links": links,
            "steps": [{"title": s["title"], "text": s["text"], "parts": s["parts"], "who": s.get("who", "you")}
                      for s in spec.get("steps", [])],
            "disclaimer": "Generated from your measurements. Print a test piece first."}


# =============================================================================
# SECTION 3: DRAFTING A GUIDE WITH THE LOCAL MODEL
# =============================================================================
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "difficulty": {"type": "string", "enum": ["easy", "medium", "hard"]},
        "time": {"type": "string"},
        "parts": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "kind": {"type": "string", "enum": list(KINDS)},
            "qty": {"type": "integer"}, "price_gbp": {"type": "number"}, "note": {"type": "string"}},
            "required": ["name", "kind"]}},
        "tools": {"type": "array", "items": {"type": "string"}},
        "connections": {"type": "array", "items": {"type": "object", "properties": {
            "from": {"type": "integer"}, "to": {"type": "integer"},
            "how": {"type": "string", "enum": list(LINKS)}, "label": {"type": "string"}},
            "required": ["from", "to", "how"]}},
        "steps": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "text": {"type": "string"},
            "parts": {"type": "array", "items": {"type": "integer"}}, "warning": {"type": "string"}},
            "required": ["title", "text", "parts"]}},
        "safety": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "summary", "parts", "connections", "steps"],
}

EXAMPLE = {
    "title": "Hardwire a dash cam", "summary": "Power a dash cam from the fuse box so it starts with the ignition.",
    "difficulty": "easy", "time": "1 hour",
    "parts": [
        {"name": "Dash cam", "kind": "module", "qty": 1, "price_gbp": 60},
        {"name": "Hardwire kit (12 V to 5 V)", "kind": "cable", "qty": 1, "price_gbp": 12},
        {"name": "Add-a-fuse tap", "kind": "fuse", "qty": 1, "price_gbp": 5},
        {"name": "Earth ring terminal", "kind": "connector", "qty": 1, "price_gbp": 1},
    ],
    "tools": ["Trim tool", "Multimeter", "Crimping pliers"],
    "connections": [
        {"from": 2, "to": 1, "how": "plug", "label": "mini USB"},
        {"from": 3, "to": 2, "how": "wire", "label": "12 V"},
        {"from": 4, "to": 2, "how": "wire", "label": "earth"},
    ],
    "steps": [
        {"title": "Find a switched fuse", "text": "With the multimeter, find a fuse that is only live with the ignition on.", "parts": [3]},
        {"title": "Fit the fuse tap", "text": "Put the original fuse and a 5 A fuse in the add-a-fuse tap and push it into that slot.", "parts": [3], "warning": "Ignition off while you swap fuses."},
        {"title": "Earth and connect", "text": "Bolt the earth ring to bare metal, crimp the kit's wires to the tap, and plug it into the dash cam.", "parts": [4, 2, 1]},
        {"title": "Tuck the cable", "text": "Run the cable along the headlining and down the A-pillar, keeping it behind the airbag, then test.", "parts": [2, 1], "warning": "Never route cables in front of the curtain airbag."},
    ],
    "safety": ["Use a fuse no bigger than the kit says.", "Keep wires away from airbags."],
}

PROMPT = """You are Titan's workshop planner. You write short, correct build guides for real DIY jobs.

Example of a good guide (for a different job):
{example}

Now write the guide for: "{task}"
{context}
Rules:
- Only list parts this job really needs. Think about how it is actually done (e.g. a boost gauge needs a
  sensor or hose tapped into the boost/vacuum line, power, earth and an illumination wire). No made-up parts.
- 3 to 8 parts, each a real thing you could buy, with a UK name; price_gbp is a rough estimate, 0 if unsure.
  Number parts 1, 2, 3... in the order listed. Pick each part's kind from: {kinds}.
- connections: which parts join and how (wire, pipe, bolt, plug, fits or flow), using part numbers.
- 3 to 7 steps in order. Each step lists only the part numbers it touches (usually 1 to 3).
  Put real dangers (battery or mains power, airbags, hot coolant, working under a car) in that step's warning.
- tools: only tools this job needs. safety: the 1 to 3 most important points.
- Never invent part numbers or torque figures; say "check the manual" instead.
Return only JSON."""


def _clip(value, n):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:n]


def clean_draft(raw, question):
    """Turn the model's JSON into a safe, consistent guide. Raises ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError("not an object")
    parts, numbers = [], {}
    for n, p in enumerate(raw.get("parts") or [], 1):
        if not isinstance(p, dict) or not _clip(p.get("name"), 60) or len(parts) >= MAX_PARTS:
            continue
        pid = f"p{len(parts) + 1}"
        numbers[n] = pid
        price = p.get("price_gbp")
        qty = p.get("qty")
        parts.append({
            "id": pid, "name": _clip(p.get("name"), 60),
            "kind": p.get("kind") if p.get("kind") in KINDS else "box",
            "qty": qty if isinstance(qty, int) and 1 <= qty <= 99 else 1,
            "price": round(float(price), 2) if isinstance(price, (int, float)) and 0 < price < 5000 else None,
            "note": _clip(p.get("note"), 120),
        })
    if len(parts) < 2:
        raise ValueError("too few parts")

    def ref(n):
        return numbers.get(n) if isinstance(n, int) else None

    links, seen = [], set()
    for c in raw.get("connections") or []:
        if not isinstance(c, dict):
            continue
        a, b = ref(c.get("from")), ref(c.get("to"))
        if not a or not b or a == b or (a, b) in seen or (b, a) in seen or len(links) >= MAX_LINKS:
            continue
        seen.add((a, b))
        links.append({"a": a, "b": b, "how": c.get("how") if c.get("how") in LINKS else "fits",
                      "label": _clip(c.get("label"), 30)})

    steps = []
    for s in raw.get("steps") or []:
        if not isinstance(s, dict) or not _clip(s.get("text"), 600) or len(steps) >= MAX_STEPS:
            continue
        ids = [ref(n) for n in (s.get("parts") or []) if ref(n)]
        text = _clip(s.get("text"), 600)
        if not ids:            # fall back to the parts the step mentions by name
            ids = [p["id"] for p in parts if p["name"].casefold() in text.casefold()][:3] or [parts[0]["id"]]
        if len(ids) > 4:       # small models often list every part; keep the ones the step names
            named = [p["id"] for p in parts if p["id"] in ids and p["name"].casefold() in text.casefold()]
            ids = named or ids[:3]
        step = {"title": _clip(s.get("title"), 60) or f"Step {len(steps) + 1}", "text": text,
                "parts": list(dict.fromkeys(ids)), "who": "you", "warn": _clip(s.get("warning"), 200)}
        risk = danger_of(f"{step['title']} {text}")
        if risk:
            step["danger"] = (f"Titan's check: this step involves {risk}. It was written by a small offline model, "
                              "so don't follow it without a proper workshop guide or your mechanic.")
        steps.append(step)
    if not steps:
        raise ValueError("no steps")

    return {
        "title": _clip(raw.get("title"), 70) or _clip(question, 70),
        "summary": _clip(raw.get("summary"), 300),
        "difficulty": raw.get("difficulty") if raw.get("difficulty") in ("easy", "medium", "hard") else "",
        "time": _clip(raw.get("time"), 30),
        "tools": [_clip(t, 50) for t in (raw.get("tools") or []) if _clip(t, 50)][:10],
        "safety": [_clip(t, 160) for t in (raw.get("safety") or []) if _clip(t, 160)][:3],
        "parts": parts, "links": links, "steps": steps,
        "flagged": sum(1 for s in steps if s.get("danger")),
        "disclaimer": DISCLAIMER_AI,
    }


def _ask_model(task):
    context = CAR_CONTEXT if CAR_WORDS.search(task.casefold()) else ""
    content = PROMPT.format(task=task, context=context, kinds=", ".join(KINDS),
                            example=json.dumps(EXAMPLE, separators=(",", ":")))
    for model in dict.fromkeys((MODEL, CHAT_MODEL)):
        payload = {
            "model": model, "stream": False, "keep_alive": KEEP_ALIVE, "format": SCHEMA,
            "messages": [{"role": "user", "content": content}],
            "options": {"temperature": 0.2, "num_predict": 1100, "num_ctx": 4096, "repeat_penalty": 1.15},
        }
        req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=DRAFT_TIMEOUT) as res:
                body = json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            if err.code == 404 and model != CHAT_MODEL:      # drafting model not installed
                continue
            raise
        return json.loads(body["message"]["content"])
    raise urllib.error.URLError("no drafting model available")


# Small models can be confidently wrong about dangerous systems. Any step that touches one is
# flagged so nobody follows an AI draft into a fuel line, the brakes or the airbag circuit.
DANGER = [
    (r"\bfuel (?:rail|line|pipe|injector|pump|tank|filter|system)|\binjectors?\b|high[- ]pressure", "the fuel system (a diesel common rail runs at around 2,000 bar)"),
    (r"\bbrake (?:line|pipe|hose|fluid|caliper|disc|pad|master cylinder)s?\b|\bbleed(?:ing)? the brakes\b", "the brakes"),
    (r"\bair ?bags?\b|\bsrs\b|seat ?belt (?:pre)?tensioner", "the airbag/SRS system"),
    (r"\bmains\b|\b2[34]0 ?v\b|\bhigh voltage\b|\bconsumer unit\b|\bplug socket\b", "mains electricity"),
    (r"\brefrigerant\b|\ba/?c gas\b|\bair[- ]con(?:ditioning)? gas\b", "air-con refrigerant (needs a licensed technician)"),
    (r"\b(?:jack|lift)(?:ed|ing)? (?:up )?the car\b|\bunder the car\b|\baxle stands?\b", "working under a lifted car"),
    (r"\bsteering (?:rack|column|arm)\b|\bsuspension (?:spring|strut)\b|\bspring compressor\b", "steering or suspension"),
    (r"\bweld(?:ing|er)?\b", "welding"),
]


def danger_of(text):
    low = text.casefold()
    return next((label for pattern, label in DANGER if re.search(pattern, low)), None)


def _draft(guide_id, task):
    started = time.time()
    try:
        guide = clean_draft(_ask_model(task), task)
        guide.update(state="ready", seconds=round(time.time() - started))
    except (urllib.error.URLError, TimeoutError, OSError):
        guide = {"state": "failed", "error": "I couldn't reach the AI model. Is Ollama running?"}
    except (ValueError, KeyError, TypeError):
        guide = {"state": "failed", "error": "The model's plan didn't make sense. Try asking in a different way."}
    with _lock:
        record = _load()["guides"].get(guide_id)
        if record is not None:
            record.update(guide)
            _save()
        _jobs.pop(guide_id, None)
    if guide["state"] == "ready":
        try:
            skills.push_alert(f"Your blueprint for {guide['title']} is ready.", "builder")
        except Exception:      # alerts are a nicety; never break drafting
            pass


def start_draft(task, wait=False):
    """Begin drafting a guide in the background. Returns its id."""
    task = _clip(task, 200).rstrip("?.! ")
    guide_id = f"ai-{_slug(task)}"[:40] + f"-{int(time.time()) % 100000}"
    with _lock:
        store = _load()
        store["guides"][guide_id] = {"id": guide_id, "question": task, "title": task[:1].upper() + task[1:],
                                     "source": "ai", "state": "drafting", "created": time.strftime("%Y-%m-%d %H:%M")}
        store["order"].append(guide_id)
        while len(store["order"]) > MAX_STORED:
            store["guides"].pop(store["order"].pop(0), None)
        _save()
    thread = threading.Thread(target=_draft, args=(guide_id, task), daemon=True, name=f"builder-{guide_id}")
    _jobs[guide_id] = thread
    thread.start()
    if wait:
        thread.join(DRAFT_TIMEOUT + 5)
    return guide_id


def delete_guide(guide_id):
    with _lock:
        store = _load()
        if guide_id not in store["guides"]:
            return False
        store["guides"].pop(guide_id)
        store["order"] = [g for g in store["order"] if g != guide_id]
        _save()
        return True


# =============================================================================
# SECTION 4: SPOKEN COMMANDS
# =============================================================================
def match_request(text):
    """'how do I wire a boost gauge' -> 'wire a boost gauge', else None."""
    low = re.sub(r"\s+", " ", text.casefold()).strip(" .!?")
    m = ASK.match(low)
    if not m:
        return None
    task = next(v for v in m.groupdict().values() if v)
    task = re.sub(r"^(?:a |an |the )?(?:step by step )?", "", task).strip()
    return task if len(task) >= 4 else None


def handle(text):
    low = re.sub(r"\s+", " ", text.casefold()).strip(" .!?")
    if re.search(r"\b(?:open|show)(?: me)? (?:the |my )?(?:builder|blueprints?|build guides?)\b|^(?:builder|blueprints?)$", low):
        _ui.update(open="builder")
        return "Opening the builder. Pick a blueprint, or ask me how to make something."
    task = match_request(text)
    if not task:
        return None
    with _lock:                                    # asked before? open it straight away
        for guide in _load()["guides"].values():
            if guide.get("state") in ("ready", "drafting") and guide.get("question", "").casefold() == _clip(task, 200).casefold():
                _ui.update(open="builder", guide=guide["id"])
                if guide["state"] == "drafting":
                    return f"I'm still drafting the blueprint for {task}. It'll appear as soon as it's ready."
                return f"Here's the blueprint for {guide['title']} again."
    guide_id = start_draft(task)
    _ui.update(open="builder", guide=guide_id)
    return (f"Drafting a blueprint for {task}. On this laptop that takes about a minute; "
            "I'll draw each part as it's ready, then walk you through the steps.")
