"""Read-only health checks for Titan and the local Windows computer."""
import os
import platform
import shutil
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None


BASE_DIR = Path(__file__).resolve().parent
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
DASHBOARD_ADDRESS = ("127.0.0.1", 8765)
CORE_FILES = (
    "T.v1.1.py",
    "titan_persona.py",
    "titan_skills.py",
    "titan_coder.py",
    "titan_diagnostics.py",
    "dashboard/server.py",
)


def _service_status(url, timeout=1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError, TimeoutError):
        return False


def diagnose_system():
    """Report local resource and service health without changing system state."""
    findings = []
    findings.append(f"Operating system: {platform.system()} {platform.release()}")
    findings.append(f"Python: {platform.python_version()} ({sys.executable})")

    if psutil is not None:
        cpu = psutil.cpu_percent(interval=0.25)
        memory = psutil.virtual_memory()
        findings.append(f"CPU: {cpu:.0f}%")
        findings.append(f"Memory: {memory.percent:.0f}% used ({(memory.total - memory.available) / 1024**3:.1f} / {memory.total / 1024**3:.1f} GB)")
        if cpu >= 90:
            findings.append("Warning: CPU use is very high.")
        if round(memory.percent) >= 90:
            findings.append("Warning: available memory is low.")
    else:
        findings.append("CPU and memory: unavailable (install psutil to enable these checks).")

    try:
        root_drive = Path.home().anchor
        disk = shutil.disk_usage(root_drive)
        used_percent = (disk.used / disk.total) * 100 if disk.total else 0
        findings.append(f"System drive {root_drive}: {used_percent:.0f}% used, {disk.free / 1024**3:.1f} GB free")
        if used_percent >= 90:
            findings.append("Warning: system drive space is running low.")
    except OSError as err:
        findings.append(f"System drive: couldn't read disk usage ({err}).")

    if psutil is not None and hasattr(psutil, "sensors_battery"):
        battery = psutil.sensors_battery()
        if battery is not None:
            power_state = "plugged in" if battery.power_plugged else "on battery"
            findings.append(f"Battery: {battery.percent:.0f}%, {power_state}")

    findings.append(f"Ollama: {'responding' if _service_status(OLLAMA_TAGS_URL) else 'not responding'}")
    try:
        with socket.create_connection(DASHBOARD_ADDRESS, timeout=0.5):
            dashboard_running = True
    except OSError:
        dashboard_running = False
    findings.append(f"Titan dashboard: {'running' if dashboard_running else 'not responding'}")

    warnings = [item for item in findings if item.startswith("Warning:")]
    status = "Warnings found" if warnings else "No obvious system issues found"
    return "LAPTOP DIAGNOSIS\n" + status + "\n" + "\n".join(findings)


def diagnose_titan():
    """Compile Titan's core modules and check its local services, without running them."""
    findings = []
    failed = False
    for relative in CORE_FILES:
        path = BASE_DIR / relative
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except FileNotFoundError:
            findings.append(f"FAIL: missing {relative}")
            failed = True
        except (OSError, SyntaxError, UnicodeError) as err:
            findings.append(f"FAIL: {relative}: {err}")
            failed = True
        else:
            findings.append(f"OK: {relative} compiles")

    findings.append(f"Ollama: {'responding' if _service_status(OLLAMA_TAGS_URL) else 'not responding'}")
    try:
        with socket.create_connection(DASHBOARD_ADDRESS, timeout=0.5):
            dashboard_running = True
    except OSError:
        dashboard_running = False
    findings.append(f"Titan dashboard: {'running' if dashboard_running else 'not responding'}")
    status = "Problems found" if failed else "Core code checks passed"
    return "TITAN SELF-DIAGNOSIS\n" + status + "\n" + "\n".join(findings)
