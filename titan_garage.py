#!/usr/bin/env python3
"""
TITAN GARAGE - build projects, 3D models, parts lists and build steps
=====================================================================
  * Project tracker: what you OWN, are CONSIDERING, what still NEEDS CHECKING,
    what's ORDERED or FITTED, prices in pounds, and the next moves in order.
  * 3D models: JSON specs drawn by the dashboard's GARAGE view (three.js).
    Built-in models live in dashboard/models/*.json; each part can link to a
    project item so its colour shows its status.
  * Custom parts: parametric generators (gauge pod, electronics enclosure,
    L-bracket) whose shapes have real holes, so the dashboard can export a
    printable STL in millimetres.

Facts about the car (fitment, part numbers, procedures) are NOT invented by
the language model: they live in the project data and in the Obsidian note
"Projects/E87 build.md", written from checked sources and marked as
"verify against your VIN" where they still need confirming.

Personal project data is stored in titan_garage.json (git-ignored).
"""
import json
import re
import threading
from pathlib import Path

import titan_skills as skills

BASE_DIR = Path(__file__).resolve().parent
STORE_FILE = BASE_DIR / "titan_garage.json"
MODELS_DIR = BASE_DIR / "dashboard" / "models"
ASSETS_DIR = BASE_DIR / "garage_models"          # real 3D files you add (GLB / glTF / OBJ); git-ignored
ASSET_TYPES = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".obj": "text/plain; charset=utf-8"}
MAX_ASSET_BYTES = 150 * 1024 * 1024
_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.()-]{0,80}\.(?:glb|gltf|obj)")
STATUSES = ("owned", "considering", "check", "ordered", "fitted", "needed")
STATUS_WORDS = {
    "own": "owned", "owned": "owned", "have": "owned", "bought": "owned", "got": "owned",
    "considering": "considering", "thinking about": "considering", "maybe": "considering",
    "check": "check", "needs checking": "check", "to check": "check", "checking": "check",
    "ordered": "ordered", "order": "ordered",
    "fitted": "fitted", "installed": "fitted", "done": "fitted",
    "needed": "needed", "need": "needed", "missing": "needed",
}

_lock = threading.RLock()
_store = None
_ui = {}                         # one-shot instruction for the dashboard, e.g. open the 3D view


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
            _store.setdefault("active", "e87")
            _store.setdefault("projects", {})
            _store.setdefault("custom_models", {})
            _store.setdefault("overrides", {})       # model id -> {asset, mesh_map, rotate, scale}
            if "e87" not in _store["projects"]:
                _store["projects"]["e87"] = starter_e87_project()
        return _store


def _save():
    with _lock:
        skills._atomic_write(STORE_FILE, json.dumps(_load(), indent=2))


def _reset_for_tests(path):
    global STORE_FILE, _store
    with _lock:
        STORE_FILE = Path(path)
        _store = None


def project(project_id=None):
    with _lock:
        s = _load()
        return json.loads(json.dumps(s["projects"][project_id or s["active"]]))


def take_ui():
    """The dashboard asks this after each reply: should it open the 3D view?"""
    with _lock:
        out = dict(_ui)
        _ui.clear()
        return out


# =============================================================================
# SECTION 2: THE E87 PROJECT (from the owner's brief, 18 Nov 2026)
# =============================================================================
def starter_e87_project():
    def item(id_, name, category, status, price=None, who="you", notes=""):
        return {"id": id_, "name": name, "category": category, "status": status,
                "price": price, "who": who, "notes": notes}

    return {
        "id": "e87",
        "title": "BMW 118d E87 LCI rebuild (2009, UK RHD hatchback)",
        "engine": "N47 2.0 diesel",
        "budget_notes": "Budget build. Head unit budget was £200.",
        "items": [
            # front-end repair (mechanic)
            item("slam_panel", "Replacement slam panel (front panel / lock carrier)", "front-end repair", "owned",
                 notes="Snapped mounts were for the slam panel. Replacement already bought. Photos (18 Nov) show the "
                       "old panel's front corner torn and crumpled."),
            item("crumpled_corner", "Crumpled silver section at the front corner", "front-end repair", "check", who="mechanic",
                 notes="Seen in the 18 Nov photo between the BMW and the Insignia. Could be the slam panel end, a "
                       "bracket, or a crash absorber. Crash absorbers are designed to crush and must be replaced, "
                       "not straightened. Mechanic to identify."),
            item("labour", "Front-end repair + alignment labour", "front-end repair", "considering", 150, "mechanic",
                 notes="Mechanic's quote: £150 labour."),
            item("chassis_legs", "Chassis legs", "front-end repair", "fitted", who="mechanic",
                 notes="Mechanic inspected: fine."),
            item("radiator", "Radiator", "front-end repair", "check", who="mechanic",
                 notes="Mechanic thinks it may be fine; needs a proper check (leaks, bent core, mounts)."),
            item("intercooler", "Intercooler + boost pipes", "front-end repair", "check", who="mechanic",
                 notes="On the 118d the intercooler sits behind the front bumper, so a front impact can damage it or its pipes."),
            item("condenser", "Air-con condenser", "front-end repair", "check", who="mechanic",
                 notes="Sits at the front of the cooling stack; check for impact damage and whether the A/C still holds gas."),
            item("carrier", "Bumper carrier (impact bar) + crash absorbers", "front-end repair", "check", who="mechanic",
                 notes="The bar the bumper bolts to. Must be straight before any bumper fits properly."),
            item("headlight_mounts", "Headlights + mounting tabs", "front-end repair", "check", who="mechanic",
                 notes="Broken tabs ruin headlight/bumper gaps. In the 18 Nov photo the headlight on the side nearest "
                       "the camera isn't fitted: confirm whether it was removed or is broken."),
            item("bonnet_latch", "Bonnet latch + release cable", "front-end repair", "check", who="mechanic"),
            item("alignment", "Wheel alignment", "front-end repair", "needed", who="mechanic"),
            # exterior
            item("bumper", "1M-style front bumper", "exterior", "owned", 250,
                 notes="Bought (seen in the 18 Nov photos). Unpainted grey primer. Has round parking-sensor (PDC) "
                       "holes and what looks like a headlight-washer opening. Must match the car's VIN options."),
            item("bumper_grilles", "Bumper grilles: centre lower (have), side vents, kidney grilles", "exterior", "check",
                 notes="Centre lower honeycomb grille and the left side-vent mesh are in the photos. The right side "
                       "vent looks open and no kidney grilles are visible: check what's in the boxes."),
            item("tow_cover", "Tow-hook cover", "exterior", "owned", notes="Fitted in the new bumper (photo)."),
            item("bumper_fixings", "Bumper brackets, wing guides + fixings", "exterior", "check",
                 notes="There's a small E-O Auto Parts box with the bumper: photograph its label. Reuse originals "
                       "only if undamaged."),
            item("bumper_prep", "Seal or paint the bumper before wrapping", "exterior", "needed",
                 notes="Wrap vinyl doesn't stick well to bare primer or textured plastic and edges lift. Have it "
                       "sealed/painted (any colour) and smooth before the wrap."),
            item("wrap", "Satin gunmetal grey wrap (low sheen, full car, DIY)", "exterior", "considering",
                 notes="Only after the front end is repaired, test-fitted and gapped."),
            item("panel_gaps", "Bonnet / wing / headlight gaps", "exterior", "check",
                 notes="Check after the slam panel is fitted and again with the new bumper test-fitted."),
            # engine bay
            item("intake", "Universal aluminium cold-air intake kit", "engine bay", "owned",
                 notes="From photos: red cone filter, polished bend and straight pipe with two sensor bosses, "
                       "silicone couplers, clamps. Universal, not made for the N47: no holder for the BMW air-mass "
                       "sensor, no heat shield, no bracket. Hold off fitting it."),
            item("stock_airbox", "Original airbox + air-mass (MAF) sensor", "engine bay", "fitted",
                 notes="Still on the car with the sensor plugged in (video, 18 Nov). Keep it for now."),
            item("air_snorkel", "Air intake snorkel (duct feeding the airbox)", "engine bay", "needed",
                 notes="Front edge is torn in the video. It sits by the slam panel, so replace it during the repair."),
            item("bay_paint", "Engine-bay paint (black / silver selected parts)", "engine bay", "considering"),
            # interior / TitanDrive
            item("head_unit", "BMW-specific Android head unit", "interior / TitanDrive", "considering", 200,
                 notes="Wants CarPlay, Android Auto, custom launcher, own APKs, later a live OBD2 dash. "
                       "No reversing camera needed. Must suit AUTOMATIC climate control. Share the listing to check."),
            item("hu_kit", "Fitting frame (auto climate) + CAN-bus/steering-control interface + aerial adaptor",
                 "interior / TitanDrive", "check",
                 notes="Kits differ for auto vs manual climate and PDC vs non-PDC cars."),
            item("vin_options", "VIN option list (PDC, headlight washers, fogs, radio type, climate)", "admin", "check",
                 notes="Decides which bumper version and head-unit kit fit."),
        ],
        "next_moves": [
            {"title": "VIN option list + four more photos",
             "who": "you",
             "detail": "Get the option list from the VIN to confirm parking sensors and headlight washers (the new "
                       "bumper has holes for both). Photograph: the crumpled corner close up, the front straight on "
                       "at bumper height, low under the front for the intercooler, and everything that came with "
                       "the bumper including the E-O Auto Parts box label."},
            {"title": "Front-end repair with the photo findings",
             "who": "mechanic",
             "detail": "Ask him to identify the crumpled silver section (crash absorbers must be replaced, not "
                       "straightened), check the impact bar, intercooler and pipes, condenser, radiator and the "
                       "missing headlight, replace the torn airbox snorkel, fit the slam panel, align, and note the gaps."},
            {"title": "Get the new bumper ready, then test-fit",
             "who": "you",
             "detail": "List what came with it (kidney grilles? right side-vent mesh? fixings?), plan to seal or paint "
                       "the primer before any wrap, and test-fit only after the repair. Keep the stock airbox; hold "
                       "off on the universal intake kit."},
        ],
    }


# =============================================================================
# SECTION 3: MODELS (built-in JSON + parametric custom parts)
# =============================================================================
def list_models():
    out = []
    for path in sorted(MODELS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append({"id": path.stem, "title": data.get("title", path.stem), "kind": data.get("kind", "assembly")})
        except (OSError, ValueError):
            continue
    with _lock:
        for model_id, data in _load()["custom_models"].items():
            out.append({"id": model_id, "title": data.get("title", model_id), "kind": "custom"})
    return out


def get_model(model_id):
    """A model spec, with any real 3D file and part mapping you've attached merged in."""
    if not re.fullmatch(r"[a-z0-9-]{1,40}", model_id or ""):
        return None
    with _lock:
        custom = _load()["custom_models"].get(model_id)
        override = json.loads(json.dumps(_load()["overrides"].get(model_id, {})))
    spec = custom
    if spec is None:
        try:
            spec = json.loads((MODELS_DIR / f"{model_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    spec = json.loads(json.dumps(spec))
    spec["id"] = model_id
    if override.get("asset") and asset_path(override["asset"]) is not None:
        spec["asset"] = override["asset"]
        spec["mesh_map"] = override.get("mesh_map", {})
        spec["asset_rotate"] = override.get("rotate", [0, 0, 0])
        spec["asset_scale"] = override.get("scale", 1.0)
    return spec


# ---- real 3D files (GLB / glTF / OBJ) ----
def safe_asset_name(name):
    name = Path(str(name or "")).name.strip()
    return name if _ASSET_NAME.fullmatch(name) else None


def asset_path(name):
    name = safe_asset_name(name)
    if not name:
        return None
    path = ASSETS_DIR / name
    return path if path.is_file() else None


def list_assets():
    if not ASSETS_DIR.is_dir():
        return []
    return [{"name": p.name, "mb": round(p.stat().st_size / 1024 ** 2, 1)}
            for p in sorted(ASSETS_DIR.iterdir()) if p.is_file() and safe_asset_name(p.name)]


def save_asset(name, data):
    """Store an uploaded model file. Returns (name, None) or (None, error)."""
    name = safe_asset_name(name)
    if not name:
        return None, "Use a .glb, .gltf or .obj file with a simple name (letters, numbers, spaces, - _ . ( ))."
    if not data or len(data) > MAX_ASSET_BYTES:
        return None, f"The file must be under {MAX_ASSET_BYTES // 1024 ** 2} MB."
    if name.endswith(".glb") and data[:4] != b"glTF":
        return None, "That isn't a valid .glb file."
    ASSETS_DIR.mkdir(exist_ok=True)
    tmp = ASSETS_DIR / (name + ".part")
    tmp.write_bytes(data)
    tmp.replace(ASSETS_DIR / name)
    return name, None


def set_override(model_id, data):
    """Attach a 3D file to a model and/or save its part mapping and alignment."""
    if get_model(model_id) is None:
        return "No such model."
    clean = {}
    if "asset" in data:
        if data["asset"] in (None, ""):
            clean["asset"] = None
        elif asset_path(data["asset"]) is None:
            return "That 3D file isn't in the garage folder."
        else:
            clean["asset"] = safe_asset_name(data["asset"])
    if "mesh_map" in data:
        mesh_map = data["mesh_map"]
        part_ids = {p["id"] for p in get_model(model_id)["parts"]}
        if not isinstance(mesh_map, dict) or len(mesh_map) > 5000:
            return "Bad part mapping."
        clean["mesh_map"] = {str(k)[:200]: v for k, v in mesh_map.items() if v in part_ids}
    if "rotate" in data:
        rot = data["rotate"]
        if not (isinstance(rot, list) and len(rot) == 3 and all(isinstance(r, (int, float)) and abs(r) <= 360 for r in rot)):
            return "Bad rotation."
        clean["rotate"] = [float(r) for r in rot]
    if "scale" in data:
        if not isinstance(data["scale"], (int, float)) or not 0.0001 <= data["scale"] <= 100000:
            return "Bad scale."
        clean["scale"] = float(data["scale"])
    with _lock:
        override = _load()["overrides"].setdefault(model_id, {})
        override.update(clean)
        if override.get("asset") is None:
            _load()["overrides"].pop(model_id, None)
        _save()
    return None


def _plate(w, h, t, holes=(), radius=2.0):
    return {"type": "plate", "w": w, "h": h, "t": t, "r": radius, "holes": [list(x) for x in holes]}


def gauge_pod(diameter=52.0, depth=40.0, wall=2.5, angle=15.0):
    """A cup for a round gauge (52 mm and 60 mm are the standard sizes) on a mounting base."""
    outer = diameter / 2 + wall
    base_w = outer * 2 + 16
    return {
        "title": f"{diameter:g} mm gauge pod", "kind": "custom", "units": "mm", "printable": True,
        "subtitle": f"{depth:g} mm deep cup, {wall:g} mm wall, tilted {angle:g}°, on a screw-down base",
        "parts": [
            {"id": "cup", "name": f"Gauge cup (fits {diameter:g} mm gauge)", "color": "#3a3a3e",
             "shape": {"type": "tube", "r_out": outer, "r_in": diameter / 2 + 0.3, "h": depth},
             "pos": [0, depth / 2 + 4, 0], "rot": [-angle, 0, 0], "explode": [0, 30, 0]},
            {"id": "base", "name": "Mounting base (2 × M4 holes)", "color": "#2a2a2e",
             "shape": _plate(base_w, outer * 2, 4, holes=[(-base_w / 2 + 6, 0, 2.2), (base_w / 2 - 6, 0, 2.2)]),
             "pos": [0, 0, 0], "rot": [-90, 0, 0], "explode": [0, -10, 0]},
        ],
        "steps": [
            {"title": "Print", "text": "Print the cup open-end up and the base flat. PETG or ASA survive a hot car; PLA will soften in summer.", "parts": ["cup", "base"], "who": "you"},
            {"title": "Glue and wire", "text": "Bond the cup to the base, feed the gauge wires through the cup, then push the gauge in (0.3 mm clearance).", "parts": ["cup"], "who": "you"},
            {"title": "Mount", "text": "Screw the base down with two M4 screws where it won't block airbags, vents or your view.", "parts": ["base"], "who": "you"},
        ],
    }


def enclosure(width=90.0, depth=60.0, height=30.0, wall=2.0):
    """A box with a screw-on lid, e.g. for a Raspberry Pi / ESP32 OBD2 reader for TitanDrive."""
    inner_w, inner_d = width - 2 * wall, depth - 2 * wall
    corner = [(-width / 2 + 5, -depth / 2 + 5, 1.6), (width / 2 - 5, -depth / 2 + 5, 1.6),
              (-width / 2 + 5, depth / 2 - 5, 1.6), (width / 2 - 5, depth / 2 - 5, 1.6)]
    return {
        "title": f"Electronics enclosure {width:g}×{depth:g}×{height:g} mm", "kind": "custom", "units": "mm", "printable": True,
        "subtitle": f"{wall:g} mm walls, 4-screw lid, cable slot. Inside: {inner_w:g}×{inner_d:g} mm",
        "parts": [
            {"id": "floor", "name": "Floor", "color": "#2a2a2e", "shape": _plate(width, depth, wall),
             "pos": [0, 0, 0], "rot": [-90, 0, 0], "explode": [0, -15, 0]},
            {"id": "wall_front", "name": "Front wall (cable slot)", "color": "#3a3a3e",
             "shape": _plate(width, height, wall, holes=[(0, -height / 2 + 7, 4)]),
             "pos": [0, height / 2, depth / 2 - wall / 2], "rot": [0, 0, 0], "explode": [0, 0, 20]},
            {"id": "wall_back", "name": "Back wall", "color": "#3a3a3e", "shape": _plate(width, height, wall),
             "pos": [0, height / 2, -depth / 2 + wall / 2], "rot": [0, 0, 0], "explode": [0, 0, -20]},
            {"id": "wall_left", "name": "Left wall", "color": "#3a3a3e", "shape": _plate(inner_d, height, wall),
             "pos": [-width / 2 + wall / 2, height / 2, 0], "rot": [0, 90, 0], "explode": [-20, 0, 0]},
            {"id": "wall_right", "name": "Right wall", "color": "#3a3a3e", "shape": _plate(inner_d, height, wall),
             "pos": [width / 2 - wall / 2, height / 2, 0], "rot": [0, 90, 0], "explode": [20, 0, 0]},
            {"id": "lid", "name": "Lid (4 × M3 holes)", "color": "#ff3b30", "shape": _plate(width, depth, wall, holes=corner),
             "pos": [0, height + wall / 2, 0], "rot": [-90, 0, 0], "explode": [0, 30, 0]},
        ],
        "steps": [
            {"title": "Print", "text": "Print the floor and walls as one piece (or glue them) and the lid flat. Use PETG/ASA for in-car heat.", "parts": ["floor", "wall_front", "wall_back", "wall_left", "wall_right"], "who": "you"},
            {"title": "Fit the board", "text": "Mount the board on stand-offs, route the OBD2/USB cable through the front slot.", "parts": ["floor", "wall_front"], "who": "you"},
            {"title": "Close", "text": "Screw the lid down with four M3 screws. Keep it away from the footwell pedals and airbags.", "parts": ["lid"], "who": "you"},
        ],
    }


def l_bracket(width=40.0, leg_a=40.0, leg_b=30.0, thickness=4.0, hole=5.2):
    """A simple L-bracket with two holes in each leg (5.2 mm clears an M5 bolt)."""
    return {
        "title": f"L-bracket {width:g} mm wide", "kind": "custom", "units": "mm", "printable": True,
        "subtitle": f"Legs {leg_a:g} and {leg_b:g} mm, {thickness:g} mm thick, {hole:g} mm holes",
        "parts": [
            {"id": "leg_a", "name": "Upright leg", "color": "#3a3a3e",
             "shape": _plate(width, leg_a, thickness, holes=[(-width / 4, leg_a / 6, hole / 2), (width / 4, leg_a / 6, hole / 2)]),
             "pos": [0, leg_a / 2, 0], "rot": [0, 0, 0], "explode": [0, 10, -10]},
            {"id": "leg_b", "name": "Base leg", "color": "#2a2a2e",
             "shape": _plate(width, leg_b, thickness, holes=[(-width / 4, 0, hole / 2), (width / 4, 0, hole / 2)]),
             "pos": [0, thickness / 2, leg_b / 2 - thickness / 2], "rot": [-90, 0, 0], "explode": [0, -10, 10]},
        ],
        "steps": [
            {"title": "Print or cut", "text": "Print with the corner flat on the bed (strongest), or cut from 3–4 mm aluminium.", "parts": ["leg_a", "leg_b"], "who": "you"},
            {"title": "Fit", "text": "Bolt through the holes; add washers on plastic.", "parts": ["leg_a", "leg_b"], "who": "you"},
        ],
    }


GENERATORS = {"gauge-pod": gauge_pod, "enclosure": enclosure, "bracket": l_bracket}


def _numbers(text):
    return [float(n) for n in re.findall(r"(\d+(?:\.\d+)?)\s*(?:mm|millimet(?:re|er)s?)?", text)]


def design(text):
    """Turn 'design a 60mm gauge pod' / 'design an enclosure 90 by 60 by 30' into a model."""
    low = text.casefold()
    nums = _numbers(low)
    if "gauge" in low or "pod" in low:
        kind, spec = "gauge-pod", gauge_pod(*(nums[:1] or [52.0]))
    elif "enclosure" in low or "box" in low or "case" in low:
        kind, spec = "enclosure", enclosure(*(nums[:3] or [90.0, 60.0, 30.0]))
    elif "bracket" in low:
        kind, spec = "bracket", l_bracket(*(nums[:1] or [40.0]))
    else:
        return None, None
    model_id = f"custom-{kind}-{'-'.join(f'{n:g}' for n in nums[:3]) or 'default'}".replace(".", "p")[:40]
    with _lock:
        _load()["custom_models"][model_id] = spec
        _save()
    return model_id, spec


# =============================================================================
# SECTION 4: SPOKEN COMMANDS
# =============================================================================
def _money(value):
    return f"£{value:,.0f}" if isinstance(value, (int, float)) else ""


def _find_item(proj, words):
    words = words.casefold().strip(" .")
    best, best_score = None, 0
    for item in proj["items"]:
        name = item["name"].casefold()
        if words == item["id"] or words in name:
            return item
        score = len(set(re.findall(r"[a-z0-9]+", words)) & set(re.findall(r"[a-z0-9]+", name)))
        if score > best_score:
            best, best_score = item, score
    return best if best_score else None


def summary(status_filter=None):
    proj = project()
    groups = {}
    for item in proj["items"]:
        if status_filter and item["status"] != status_filter:
            continue
        groups.setdefault(item["status"], []).append(item)
    if not groups:
        return f"Nothing is marked {status_filter} at the moment."
    labels = {"owned": "You own", "considering": "You're considering", "check": "Still to check",
              "ordered": "On order", "fitted": "Done or fitted", "needed": "Still needed"}
    parts = []
    for status in STATUSES:
        if status in groups:
            names = [f"{i['name']}{' (' + _money(i['price']) + ')' if i['price'] else ''}" for i in groups[status]]
            parts.append(f"{labels[status]}: " + "; ".join(names) + ".")
    return " ".join(parts)


def budget():
    proj = project()
    owned = sum(i["price"] or 0 for i in proj["items"] if i["status"] in ("owned", "ordered", "fitted"))
    planned = sum(i["price"] or 0 for i in proj["items"] if i["status"] in ("considering", "needed"))
    return (f"Spent or committed so far: {_money(owned)}. Being considered: {_money(planned)}. "
            "Items without prices aren't counted yet; tell me prices as you find them.")


def next_moves():
    moves = project()["next_moves"]
    return " ".join(f"{n}. {m['title']} ({'you' if m['who'] == 'you' else 'your mechanic'}): {m['detail']}"
                    for n, m in enumerate(moves, 1))


def handle(text):
    """Return Titan's reply for garage commands, else None."""
    t = text.strip()
    low = re.sub(r"\s+", " ", t.casefold()).strip(" .!?")

    if re.search(r"\b(?:open|show)(?: me)? (?:the |my )?(?:garage|3d (?:view|model)s?|workshop)\b|^garage$", low):
        _ui.update(open="garage", model="e87-front-end")
        return "Opening the garage. Drag to turn the model, use the slider to explode it, and tap a part to see what it is."
    if re.search(r"\b(?:show|open)(?: me)? (?:the )?front[- ]end(?: model)?\b", low):
        _ui.update(open="garage", model="e87-front-end")
        return "Here's the E87 front end, exploded. Coloured parts show what you own, what's being considered and what still needs checking."

    if re.search(r"\b(?:design|make|model|create)\b.*\b(?:gauge|pod|enclosure|box|case|bracket)\b", low):
        model_id, spec = design(low)
        if model_id:
            _ui.update(open="garage", model=model_id)
            return f"Designed: {spec['title']}. {spec['subtitle']}. It's in the garage, where you can export it as an STL to print."

    if re.search(r"\b(?:next (?:three )?moves|what(?:'s| is) next on the car|car plan|build plan)\b", low):
        return next_moves()
    if re.search(r"\bgarage budget\b|\bhow much (?:have i spent|am i spending) on the car\b|\bcar budget\b", low):
        return budget()
    if re.search(r"\bwhat do i own\b|\bwhat have i got for the car\b", low):
        return summary("owned")
    if re.search(r"\bwhat (?:still )?needs (?:checking|to be checked)\b|\bwhat'?s (?:left|still) to check\b|\bwhat should i check\b", low):
        return summary("check")
    if re.search(r"\bwhat am i considering\b", low):
        return summary("considering")
    if re.search(r"\b(?:garage|car|project|build) (?:status|summary)\b", low):
        return summary()

    m = re.match(r"^(?:mark|set|move) (?:the |my )?(.+?) (?:as|to) (owned|considering|check|checked|ordered|fitted|needed|bought|installed|done)$", low)
    if m:
        with _lock:
            proj = _load()["projects"][_load()["active"]]
            item = _find_item(proj, m.group(1))
            if item is None:
                return f"I can't find '{m.group(1)}' in the car project. Say 'car status' to see the list."
            word = m.group(2)
            item["status"] = "fitted" if word == "checked" else STATUS_WORDS.get(word, word)
            _save()
        return f"Marked {item['name']} as {item['status']}."

    m = re.match(r"^add (?:a |an |the )?(.+?) to (?:my |the )?(?:garage|car|project|build)"
                 r"(?: as (owned|considering|check|ordered|needed))?(?: (?:for|at) £?(\d+(?:\.\d+)?))?$", low)
    if m:
        name, status, price = m.group(1).strip(), m.group(2) or "considering", m.group(3)
        with _lock:
            proj = _load()["projects"][_load()["active"]]
            item_id = re.sub(r"[^a-z0-9]+", "_", name)[:30].strip("_") or "item"
            proj["items"].append({"id": item_id, "name": name[:80], "category": "added", "status": status,
                                  "price": float(price) if price else None, "who": "you", "notes": ""})
            _save()
        return f"Added {name} to the car project as {status}{' at ' + _money(float(price)) if price else ''}."

    m = re.match(r"^(?:the )?(.+?) (?:costs?|is|was) £(\d+(?:\.\d+)?)$", low)
    if m:
        with _lock:
            proj = _load()["projects"][_load()["active"]]
            item = _find_item(proj, m.group(1))
            if item is not None:
                item["price"] = float(m.group(2))
                _save()
                return f"Noted: {item['name']} at {_money(item['price'])}."
    return None
