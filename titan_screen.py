#!/usr/bin/env python3
"""
TITAN SCREEN - "where's the export button?" -> Titan points at it on your screen
=================================================================================
Clicky-style help for whatever app is on the laptop's screen:

  1. You ask: "Titan, where's the export button?" / "point to the wifi settings" /
     "where do I click to add a layer?"
  2. If Titan's own dashboard is in front, Titan waits for you to switch to the
     app you mean (up to 10 seconds), then takes ONE screenshot of the main screen.
  3. A small local vision model (qwen3-vl:2b-instruct in Ollama, fully offline) reads it
     and answers with a short sentence plus a location on a 0-1000 grid.
  4. A red triangle flies from your mouse to that spot, with a label, on a
     click-through overlay, and Titan says the answer.

Privacy: the screenshot stays in memory, goes only to the local Ollama server
and is never written to disk. Nothing leaves the laptop.
"""
import base64
import io
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import json

import titan_skills as skills

OLLAMA_URL = os.environ.get("TITAN_OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
# the instruct build answers straight away; the plain qwen3-vl:2b "thinks" first and runs out of tokens
VISION_MODEL = os.environ.get("TITAN_VISION_MODEL", "qwen3-vl:2b-instruct")
KEEP_ALIVE = "2m"              # unload soon after: 8 GB laptop
MAX_SIDE = 1024                # smaller screenshot = fewer image tokens = faster on CPU
TIMEOUT = 240
POINTER_SECONDS = 8
IS_WINDOWS = sys.platform == "win32"

_busy = threading.Lock()
last = {}                      # last result, for the dashboard and tests

ASK = re.compile(
    r"^(?:titan[,\s]+)?(?:please\s+)?(?:"
    r"point (?:to|at|out) (?P<a>.+)"
    r"|show me where (?P<b>.+?)(?: is| are)?(?: on (?:my|the) screen)?"
    r"|where (?:do|should|can) i (?:click|tap|press|go|find)(?: to| for)? (?P<c>.+)"
    r"|where(?:'s| is| are) (?P<d>.+?) on (?:my|the|this) screen"
    r"|where(?:'s| is| are) (?P<e>(?:the |my )?.+?\b(?:button|menu|icon|tab|setting|settings|option|toggle|slider|link|field|box|bar))"
    r"|find (?P<f>.+?) on (?:my|the) screen"
    r")$")

PROMPT = """You are Titan, looking at the user's computer screen to help them find something.
The user asks: "{q}"
Reply in one or two short, friendly sentences saying what to click and where it is on screen.
Then, on the last line, give the location of that thing like this:
POINT: x, y
x goes from 0 (left edge) to 1000 (right edge) and y from 0 (top) to 1000 (bottom) of the screenshot.
If you can't see it, write POINT: none and say what they could try instead."""


# =============================================================================
# SECTION 1: UNDERSTANDING THE REQUEST AND THE ANSWER
# =============================================================================
def match_request(text):
    low = re.sub(r"\s+", " ", text.casefold()).strip(" .!?")
    m = ASK.match(low)
    if not m:
        return None
    target = next(v for v in m.groupdict().values() if v).strip()
    return target if 2 <= len(target) <= 120 else None


def parse_answer(reply):
    """-> (spoken text, (x, y) on a 0-1000 grid or None)."""
    reply = str(reply or "").strip()
    point = None
    m = re.search(r"POINT\s*:\s*\(?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", reply, re.I)
    if m:
        point = (float(m.group(1)), float(m.group(2)))
    else:                                       # Qwen sometimes answers with a box instead
        box = re.search(r'"?bbox(?:_2d)?"?\s*:\s*\[\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', reply)
        if box:
            x1, y1, x2, y2 = map(float, box.groups())
            point = ((x1 + x2) / 2, (y1 + y2) / 2)
    if point and not (0 <= point[0] <= 1000 and 0 <= point[1] <= 1000):
        point = None
    text = re.sub(r"POINT\s*:.*$", "", reply, flags=re.I | re.S)
    text = re.sub(r"```.*?```|\{.*?\}", "", text, flags=re.S)
    text = re.sub(r"\s+", " ", text).strip(" -:")[:300]
    if not text:
        text = "Here it is." if point else "I couldn't spot that on your screen."
    return text, point


def to_screen(point, width, height):
    """0-1000 grid -> pixel on a screen of this size."""
    x, y = point
    return round(x / 1000 * (width - 1)), round(y / 1000 * (height - 1))


# =============================================================================
# SECTION 2: WINDOWS HELPERS (screen size, foreground window, mouse)
# =============================================================================
def _dpi_aware():
    """Use real pixels so the screenshot and the overlay line up on scaled displays."""
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def _foreground_title():
    if not IS_WINDOWS:
        return ""
    import ctypes
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _cursor():
    if not IS_WINDOWS:
        return None
    import ctypes
    from ctypes import wintypes
    pt = wintypes.POINT()
    return (pt.x, pt.y) if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)) else None


def wait_for_other_app(timeout=10.0):
    """If Titan's own page is in front, give the user a moment to switch apps."""
    end = time.time() + timeout
    switched = False
    while time.time() < end and "titan" in _foreground_title().casefold():
        switched = True
        time.sleep(0.25)
    if switched:
        time.sleep(0.8)            # let the other window finish drawing


# =============================================================================
# SECTION 3: SCREENSHOT + VISION MODEL
# =============================================================================
def capture():
    """One screenshot of the main screen, in memory only. -> (JPEG base64, (w, h))."""
    from PIL import ImageGrab
    img = ImageGrab.grab()
    size = img.size
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("ascii"), size


def ask_vision(question, image_b64):
    payload = {
        "model": VISION_MODEL, "stream": False, "keep_alive": KEEP_ALIVE, "think": False,
        "messages": [{"role": "user", "content": PROMPT.format(q=question), "images": [image_b64]}],
        "options": {"temperature": 0.1, "num_predict": 200, "num_ctx": 4096},
    }
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        body = json.loads(res.read().decode("utf-8"))
    return body["message"]["content"]


# =============================================================================
# SECTION 4: THE POINTER OVERLAY (click-through, always on top)
# =============================================================================
def show_pointer(x, y, label, seconds=POINTER_SECONDS):
    """Fly a red triangle from the mouse to (x, y) with a label. Runs in its own thread."""
    thread = threading.Thread(target=_overlay, args=(x, y, label, seconds), daemon=True, name="titan-pointer")
    thread.start()
    return thread


def _overlay(x, y, label, seconds):
    import tkinter as tk
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{sw}x{sh}+0+0")
    key = "#010203"                                   # this colour becomes see-through
    root.configure(bg=key)
    try:
        root.attributes("-transparentcolor", key)
    except tk.TclError:
        root.attributes("-alpha", 0.9)
    canvas = tk.Canvas(root, width=sw, height=sh, bg=key, highlightthickness=0)
    canvas.pack()
    root.update_idletasks()
    if IS_WINDOWS:                                    # let clicks pass straight through
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = -20, 0x80000, 0x20, 0x80
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)

    start = _cursor() or (sw // 2, sh // 2)
    tx, ty = max(0, min(sw - 1, x)), max(0, min(sh - 1, y))
    dist = math.hypot(tx - start[0], ty - start[1])
    ctrl = ((start[0] + tx) / 2, min(start[1], ty) - min(260, dist * 0.45))
    frames = max(18, int(min(0.95, 0.35 + dist / 2400) * 60))

    def triangle(px, py, angle, scale):
        pts = [(0, 0), (30, 11), (14, 15), (8, 31)]       # tip at (0, 0)
        a = math.radians(angle)
        out = []
        for ux, uy in pts:
            ux, uy = ux * scale, uy * scale
            out += [px + ux * math.cos(a) - uy * math.sin(a), py + ux * math.sin(a) + uy * math.cos(a)]
        return out

    shape = canvas.create_polygon(*triangle(*start, 0, 1), fill="#ff3b30", outline="white", width=2)

    def fly(i=0):
        t = i / frames
        e = 1 - (1 - t) ** 3
        px = (1 - e) ** 2 * start[0] + 2 * (1 - e) * e * ctrl[0] + e * e * tx
        py = (1 - e) ** 2 * start[1] + 2 * (1 - e) * e * ctrl[1] + e * e * ty
        dx = 2 * (1 - e) * (ctrl[0] - start[0]) + 2 * e * (tx - ctrl[0])
        dy = 2 * (1 - e) * (ctrl[1] - start[1]) + 2 * e * (ty - ctrl[1])
        angle = (math.degrees(math.atan2(dy, dx)) - 22) * (1 - t) if t < 1 else 0
        canvas.coords(shape, *triangle(px, py, angle, 1 + 0.45 * math.sin(math.pi * t)))
        if i < frames:
            root.after(16, fly, i + 1)
        else:
            arrive()

    def arrive():
        ring = canvas.create_oval(tx - 6, ty - 6, tx + 6, ty + 6, outline="#ff3b30", width=3)
        text = label[:140]
        lx, ly = tx + 38, ty + 30
        item = canvas.create_text(lx + 12, ly + 10, text=text, anchor="nw", fill="white",
                                  font=("Segoe UI", 12, "bold"), width=340)
        bx1, by1, bx2, by2 = canvas.bbox(item)
        if bx2 + 12 > sw:                                 # keep the label on screen
            canvas.move(item, -(bx2 - bx1) - 90, 0)
        if by2 + 12 > sh:
            canvas.move(item, 0, -(by2 - by1) - 70)
        bx1, by1, bx2, by2 = canvas.bbox(item)
        box = canvas.create_rectangle(bx1 - 12, by1 - 9, bx2 + 12, by2 + 9, fill="#0b2f63", outline="white", width=2)
        canvas.tag_lower(box, item)

        def pulse(k=0):
            r = 6 + (k % 30) * 1.3
            canvas.coords(ring, tx - r, ty - r, tx + r, ty + r)
            root.after(33, pulse, k + 1)
        pulse()
        canvas.tag_raise(shape)
        root.after(int(seconds * 1000), root.destroy)

    root.after(30, fly)
    root.mainloop()
    # free Tk in this same thread (Tk objects must die in the thread that made them)
    canvas = shape = root = fly = arrive = None
    import gc
    gc.collect()


# =============================================================================
# SECTION 5: PUTTING IT TOGETHER
# =============================================================================
def look_and_point(question, announce=None):
    """Screenshot -> vision model -> pointer + spoken answer. Returns the result dict."""
    announce = announce or (lambda text: skills.push_alert(text, "screen"))
    if not _busy.acquire(blocking=False):
        return None
    try:
        wait_for_other_app()
        started = time.time()
        try:
            image, (w, h) = capture()
        except Exception as err:          # Pillow missing, locked screen, etc.
            result = {"ok": False, "text": f"I couldn't take a screenshot ({err.__class__.__name__})."}
        else:
            try:
                text, point = parse_answer(ask_vision(question, image))
            except urllib.error.HTTPError as err:
                text, point = ("My vision model isn't installed. Run: ollama pull " + VISION_MODEL
                               if err.code == 404 else "The vision model hit an error."), None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
                text, point = "I couldn't reach the vision model. Is Ollama running?", None
            result = {"ok": point is not None, "text": text, "point": point, "screen": (w, h),
                      "seconds": round(time.time() - started)}
            if point is not None:
                sx, sy = to_screen(point, w, h)
                result["pixel"] = (sx, sy)
                show_pointer(sx, sy, text)
        last.clear(); last.update(result, question=question)
        announce(result["text"])
        return result
    finally:
        _busy.release()


def handle(text):
    target = match_request(text)
    if not target:
        return None
    if _busy.locked():
        return "I'm still looking at your screen for the last question."
    question = text.strip().rstrip("?") + "?"
    threading.Thread(target=look_and_point, args=(question,), daemon=True, name="titan-look").start()
    return (f"Looking for {target} on the laptop screen. If my page is in front, switch to the app you mean; "
            "I'll point at it in a few seconds.")


_dpi_aware()
