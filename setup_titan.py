# =============================================================================
# SECTION 0: IMPORTS
# =============================================================================
import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
import venv
import zipfile
from pathlib import Path


# =============================================================================
# SECTION 1: CONFIG
# =============================================================================
ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
IS_WINDOWS = sys.platform.startswith("win")
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")

MIN_PYTHON = (3, 10)
PACKAGES = ["psutil", "pyttsx3", "vosk", "sounddevice"]

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_FOLDER_IN_ZIP = "vosk-model-small-en-us-0.15"
VOSK_DIR = ROOT / "vosk-model"

OLLAMA_MODEL = "llama3.2:3b"

FOLDERS = [".vscode", "dashboard", "arduino"]

# filled in as steps run; printed at the end
PROBLEMS = []


# =============================================================================
# SECTION 2: STARTER FILE CONTENTS
# =============================================================================
REQUIREMENTS_TXT = "\n".join(PACKAGES) + "\n"

GITIGNORE = """\
# Python
.venv/
__pycache__/
*.pyc

# Titan runtime data (personal)
titan_profile.json
titan_notes.txt
titan_memory.json
titan_memory.json.tmp

# Big downloads
vosk-model/
*.zip
"""

README = """\
# Titan

Offline JARVIS-style assistant in Python.

## Structure

```
Titan/
|-- T.v1.py            main program (terminal version)
|-- setup_titan.py     one-time project setup
|-- requirements.txt   Python packages
|-- vosk-model/        offline speech model (downloaded by setup)
|-- dashboard/         NEXT PHASE: interface
|-- arduino/           LAST PHASE: Arduino Uno integration
|-- .venv/             virtual environment (created by setup)
`-- .vscode/           VS Code settings + run buttons
```

## Run

- VS Code: press F5, then pick "Titan (typed)" or "Titan (voice)"
- Terminal:  `.venv\\Scripts\\python T.v1.py --text`

## Needs

- Python 3.10+ (3.12 recommended)
- Ollama running with `ollama pull llama3.2:3b`
"""

VSCODE_LAUNCH = {
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Titan (typed)",
            "type": "debugpy",
            "request": "launch",
            "program": "${workspaceFolder}/T.v1.py",
            "args": ["--text"],
            "console": "integratedTerminal",
        },
        {
            "name": "Titan (voice)",
            "type": "debugpy",
            "request": "launch",
            "program": "${workspaceFolder}/T.v1.py",
            "console": "integratedTerminal",
        },
        {
            "name": "Titan (typed, no speech)",
            "type": "debugpy",
            "request": "launch",
            "program": "${workspaceFolder}/T.v1.py",
            "args": ["--text", "--silent"],
            "console": "integratedTerminal",
        },
    ],
}


def vscode_settings():
    interpreter = (
        "${workspaceFolder}/.venv/Scripts/python.exe"
        if IS_WINDOWS
        else "${workspaceFolder}/.venv/bin/python"
    )
    return {
        "python.defaultInterpreterPath": interpreter,
        "python.terminal.activateEnvironment": True,
        "files.exclude": {"**/__pycache__": True},
        "editor.formatOnSave": False,
    }


# =============================================================================
# SECTION 3: HELPERS
# =============================================================================
def step(number, title):
    print(f"\n=== STEP {number}: {title} ===")


def ok(msg):
    print(f"  [ok]   {msg}")


def warn(msg):
    print(f"  [warn] {msg}")


def info(msg):
    print(f"  {msg}")


def write_file(path, content, force):
    """Write a starter file. Skip if it exists unless --force."""
    if path.exists() and not force:
        info(f"exists, kept: {path.relative_to(ROOT)}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    ok(f"created: {path.relative_to(ROOT)}")


# =============================================================================
# SECTION 4: STEPS
# =============================================================================
def check_python():
    step(1, "Check Python")
    version = sys.version_info
    info(f"Python {version.major}.{version.minor}.{version.micro} at {sys.executable}")
    if (version.major, version.minor) < MIN_PYTHON:
        print(f"  [FAIL] Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required.")
        print("         Install Python 3.12 from https://www.python.org/downloads/")
        print("         (tick 'Add python.exe to PATH'), then run this again.")
        sys.exit(1)
    ok("Python version is fine")
    if not (ROOT / "T.v1.py").exists():
        warn("T.v1.py is not in this folder yet - copy it here before running Titan.")
    else:
        ok("T.v1.py found")


def create_structure(force):
    step(2, "Create folders and starter files")
    for folder in FOLDERS:
        (ROOT / folder).mkdir(exist_ok=True)
        ok(f"folder: {folder}/")
    for placeholder in ("dashboard", "arduino"):
        keep = ROOT / placeholder / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")
    write_file(ROOT / "requirements.txt", REQUIREMENTS_TXT, force)
    write_file(ROOT / ".gitignore", GITIGNORE, force)
    write_file(ROOT / "README.md", README, force)
    write_file(ROOT / ".vscode" / "launch.json", json.dumps(VSCODE_LAUNCH, indent=4) + "\n", force)
    write_file(ROOT / ".vscode" / "settings.json", json.dumps(vscode_settings(), indent=4) + "\n", force)


def install_packages():
    step(3, "Virtual environment and packages")
    if not VENV_PYTHON.exists():
        info("creating .venv ...")
        venv.EnvBuilder(with_pip=True).create(str(VENV_DIR))
        ok("created .venv")
    else:
        info(".venv already exists, reusing it")

    commands = [
        [str(VENV_PYTHON), "-m", "pip", "install", "--retries", "2", "--timeout", "30", "--upgrade", "pip"],
        [str(VENV_PYTHON), "-m", "pip", "install", "--retries", "2", "--timeout", "30", "-r", str(ROOT / "requirements.txt")],
    ]
    for cmd in commands:
        info("running: " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
        result = subprocess.run(cmd)
        if result.returncode != 0:
            warn("a pip command failed - read the error above, fix it, and run setup again.")
            PROBLEMS.append("Python packages were not installed (check your internet, then re-run setup).")
            return False
    ok("packages installed")
    return True


def download_model():
    step(4, "Offline speech model (Vosk)")
    if VOSK_DIR.is_dir() and any(VOSK_DIR.iterdir()):
        ok("vosk-model/ already present, skipping download")
        return
    zip_path = ROOT / "vosk-model.zip"

    def progress(blocks, block_size, total):
        if total > 0:
            percent = min(100, blocks * block_size * 100 // total)
            print(f"\r  downloading... {percent}%", end="", flush=True)

    try:
        info("downloading about 40 MB ...")
        urllib.request.urlretrieve(VOSK_URL, zip_path, progress)
        print()
        info("unzipping ...")
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(ROOT)
        extracted = ROOT / VOSK_FOLDER_IN_ZIP
        if not extracted.is_dir():
            raise RuntimeError(f"expected folder '{VOSK_FOLDER_IN_ZIP}' not found in the zip")
        extracted.rename(VOSK_DIR)
        ok("speech model ready in vosk-model/")
    except Exception as err:
        print()
        warn(f"model download failed: {err}")
        PROBLEMS.append("Vosk speech model missing (Titan will use typed input until you add it).")
        info("Manual fix: download " + VOSK_URL)
        info("unzip it, rename the folder to 'vosk-model' and put it next to T.v1.py.")
        info("(Titan still works without it - it will ask you to type instead.)")
    finally:
        if zip_path.exists():
            zip_path.unlink()


def check_ollama():
    step(5, "Ollama (local AI model)")
    exe = shutil.which("ollama")
    if not exe:
        warn("Ollama is not installed (or not on PATH).")
        info("1) Install it from https://ollama.com/download")
        info(f"2) Then run:  ollama pull {OLLAMA_MODEL}")
        info("(Titan still works without it - only the natural-language routing is lost.)")
        PROBLEMS.append("Ollama is not installed.")
        return
    ok(f"found Ollama: {exe}")
    try:
        result = subprocess.run([exe, "list"], capture_output=True, text=True, timeout=20)
    except Exception as err:
        warn(f"could not run 'ollama list': {err}")
        return
    if result.returncode != 0:
        warn("Ollama is installed but not responding. Start the Ollama app and re-run setup.")
        PROBLEMS.append("Ollama is not running.")
        return
    if OLLAMA_MODEL in result.stdout:
        ok(f"model {OLLAMA_MODEL} is installed")
    else:
        warn(f"model {OLLAMA_MODEL} is not installed yet. Run:  ollama pull {OLLAMA_MODEL}")
        PROBLEMS.append(f"Ollama model {OLLAMA_MODEL} not pulled yet.")


def self_test():
    step(6, "Self-test")
    if not VENV_PYTHON.exists():
        warn("no .venv yet, skipping the test")
        return
    code = (
        "import importlib\n"
        "for name in ['psutil', 'pyttsx3', 'vosk', 'sounddevice']:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "        print('OK   ' + name)\n"
        "    except Exception as e:\n"
        "        print('FAIL ' + name + ' -> ' + str(e).splitlines()[0])\n"
        "try:\n"
        "    import sounddevice as sd\n"
        "    dev = sd.query_devices(kind='input')\n"
        "    print('OK   microphone: ' + dev['name'])\n"
        "except Exception as e:\n"
        "    print('WARN no microphone found -> ' + str(e).splitlines()[0])\n"
    )
    result = subprocess.run([str(VENV_PYTHON), "-c", code], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        print("  " + line)
        if line.startswith("FAIL"):
            PROBLEMS.append("Import test failed: " + line[5:].strip())
    if result.returncode != 0 and result.stderr:
        warn(result.stderr.strip().splitlines()[-1])


def next_steps():
    print("\n" + "=" * 60)
    if PROBLEMS:
        print(f"SETUP FINISHED WITH {len(PROBLEMS)} THING(S) TO FIX:")
        for problem in PROBLEMS:
            print("  - " + problem)
        print("Fix them, then run  python setup_titan.py  again (it is safe to repeat).")
    else:
        print("SETUP FINISHED - everything checks out.")
    print("\nNext:")
    print("  1. In VS Code press Ctrl+Shift+P > 'Python: Select Interpreter'")
    print("     and pick the one inside .venv")
    print("  2. Press F5 (or the Run and Debug panel) > 'Titan (typed)'")
    print("  3. First run asks your name and age - that becomes your identity check")
    print("=" * 60)


# =============================================================================
# SECTION 5: MAIN
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description="Titan project setup")
    parser.add_argument("--skip-install", action="store_true", help="skip .venv and pip installs")
    parser.add_argument("--skip-model", action="store_true", help="skip the Vosk download")
    parser.add_argument("--force", action="store_true", help="overwrite starter files (not T.v1.py)")
    args = parser.parse_args()

    print(f"Project folder: {ROOT}")
    check_python()
    create_structure(args.force)
    if not args.skip_install:
        install_packages()
    if not args.skip_model:
        download_model()
    check_ollama()
    self_test()
    next_steps()


if __name__ == "__main__":
    main()
