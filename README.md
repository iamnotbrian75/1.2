# Titan

Offline JARVIS-style assistant in Python.

## Structure

```
Titan/
|-- T.v1.1.py          main assistant brain (terminal and dashboard)
|-- T.v1.py            earlier version
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
- Terminal:  `.venv\Scripts\python T.v1.1.py --text`
- Dashboard with wake word: `.venv\Scripts\python dashboard\server.py --wake-word`
- Start with Windows: run `install_titan_startup.ps1` once in PowerShell. Titan then
  starts silently when you sign in; saying its wake word opens the dashboard.
- Private phone access: with Tailscale connected on the PC and phone, run
  `.\install_titan_startup.ps1 -EnablePhoneAccess` in PowerShell. Keep Tailscale
  Serve private (do not enable Funnel); open the displayed `https://...ts.net/`
  address on the phone. Titan still requires its name-and-age login.

When voice activation is enabled, say "Titan", wait for its acknowledgement, then
speak your command. Wake-word detection and speech recognition run locally using
the Vosk model. Use the dashboard's WAKE WORD button to pause or resume listening.
To remove automatic startup, run `install_titan_startup.ps1 -Uninstall`.
To disable phone access while keeping local wake-word startup, rerun
`.\install_titan_startup.ps1` without `-EnablePhoneAccess`.

## Local project coding helper

Ask Titan to work in one explicit project folder, for example:

`code in C:\Users\Brian\OneDrive\Desktop\MyProject -- fix the failing tests`

For a standalone example or snippet, ask naturally, such as
`write me a small code example in Python`. To have Titan edit an existing
project, use the explicit `code in <folder> -- <task>` form above.

The local `qwen2.5-coder:3b` model can list and read UTF-8 project files, make
bounded file edits, back up files before replacing them, and run a detected
standard test suite. General chat continues to use `llama3.2:3b`. The coding
helper is restricted to the named folder under your Windows user directory;
it cannot delete files, read common credential files, or run arbitrary shell
commands. Backups are stored in `.titan-backups`. Review its changes: this
small local model can make mistakes and is not a master coder.

Titan keeps recent conversation context while it is running. Saved memories,
notes, and reminders persist between launches. Say "remember that..."
to save a fact, "what do you remember about me?" to review memories, or
"forget ..." / "forget everything" to remove them.

Saved facts are kept in the signed-in user's note in the Obsidian `Titan Brain`
folder. The `Projects.md` note is included as project background for local AI
chat; Titan does not scan the rest of the vault. Because the vault is under
OneDrive Documents, its contents may sync if OneDrive sync is enabled.

The identity file can contain multiple users; existing single-user profiles
are supported and preserved when additional users are added.

For disk cleanup, ask Titan to find large files in one specific folder, for
example `find large files in C:\Users\Brian\Downloads`. Titan lists candidates
without changing anything. Select one by number, then separately say
`confirm delete file 1` to move that exact file to the Recycle Bin. Confirmation
expires after two minutes and is cancelled if you send a different command.

## Laptop diagnosis and Titan maintenance

- Ask `what can and can't you do, and how could we improve you?` for Titan's
  current capabilities, limits, and grounded optimization suggestions.
- Capability and improvement questions are handled locally, rather than sent
  through the small model's command router.
- Titan cannot update Ollama's model weights from conversations. To teach it a
  preference explicitly, rate a recent reply with `rate that 1/5 because ...`,
  say `that was wrong because ...`, or submit `correction: ...`. Titan saves
  only these explicitly submitted examples in the signed-in user's
  `Titan Brain\<Name>-Learning.json` (separate from any folder used for file
  cleanup). Each explicit example contains the associated recent request and
  reply plus your rating/correction; long exchanges are clipped to keep the
  saved examples bounded, and sensitive-information keywords in your request
  and feedback are screened. Relevant corrections are supplied as context for
  future answers.
  Titan does not save every conversation as training data. Ask
  `what have you learned from my feedback` to inspect examples, or
  `forget my feedback` to delete them. The Obsidian vault may sync through
  OneDrive if sync is enabled.
- Ask `run a Titan learning evaluation` to run a small, deterministic check of
  common intent routing and save its score. This tests routing, not the local
  language model's general knowledge or intelligence. This evaluation does not
  change model parameters.
- `run a system diagnosis` checks operating-system details, CPU, memory, system
  drive space, battery when available, Ollama, and the dashboard. This is
  read-only; warnings identify high resource use or unavailable services.
- `diagnose your own code` compiles Titan's core Python files and checks whether
  Ollama and the dashboard are responding. It does not modify files or run
  project code.
- Low-risk documentation updates can be requested with
  `update your code -- fix a typo in the README`. Titan may edit only
  `README.md` automatically, keeps a backup, validates the result, and records
  the exact diff and check results in `TITAN_CHANGELOG.md`.
- Executable-code changes require a second, explicit request:
  `approve update your code -- <specific change>`. Approved updates are limited
  to Titan's listed source and dashboard files; profile and memory data,
  startup configuration, dependencies, and arbitrary commands remain out of
  scope. Titan backs up changed files, runs syntax checks and detected standard
  tests, and restores the original files if validation fails. Review each
  change in `TITAN_CHANGELOG.md`; restart Titan before expecting its running
  process to load self-updated code.

## Needs

- Python 3.10+ (3.12 recommended)
- Ollama running with `ollama pull llama3.2:3b`

Tv1 docs
#!/usr/bin/env python3
"""
TITAN v1 - offline JARVIS-style assistant
=========================================
Status: everything works in the terminal. STOPS before the interface (dashboard).

Features
  - Identity check (name + age) on startup
  - Voice in  (Vosk, offline)  -> falls back to typing if not set up
  - Voice out (pyttsx3, offline) -> falls back to text-only if not set up
  - Commands: time, list files, CPU, RAM, process count, system info, notes
  - Local Ollama model (llama3.2:3b) routes natural-language requests to a command

Setup (Windows, run in a terminal)
  pip install psutil pyttsx3 vosk sounddevice
  Download a small Vosk model (e.g. vosk-model-small-en-us-0.15) from
  https://alphacephei.com/vosk/models and unzip it into a folder named
  "vosk-model" next to this file.
  Install Ollama, then:  ollama pull llama3.2:3b

Run
  python T.v1.py            # voice if available, otherwise typing
  python T.v1.py --text     # force typing
  python T.v1.py --silent   # no spoken output
"""

Setup_titan

#!/usr/bin/env python3
"""
TITAN PROJECT SETUP
===================
Put this file in your project folder (next to T.v1.py), then run:

    python setup_titan.py

It will, step by step:
  1. Check your Python version
  2. Create the project folders and starter files (never overwrites your code)
  3. Create a virtual environment (.venv) and install the packages
  4. Download the offline speech model (Vosk, about 40 MB)
  5. Check that Ollama and the llama3.2:3b model are installed
  6. Test that everything imports and that a microphone is found

Options:
  --skip-install   don't create .venv / install packages
  --skip-model     don't download the Vosk model
  --force          overwrite the starter files (README, .gitignore, .vscode ...)
                   T.v1.py is NEVER touched.
"""
T.v1.1
#!/usr/bin/env python3
"""
TITAN v1.1 - offline JARVIS-style assistant  (the BRAIN)
=========================================================
Works in the terminal, and the dashboard (dashboard/server.py) uses it too.

Files that belong together (keep them in the same folder):
  T.v1.py             this file: the brain that decides what to do
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
  python T.v1.py            # voice if available, otherwise typing
  python T.v1.py --text     # force typing
  python T.v1.py --silent   # no spoken output
"""

titan_persona.py
#!/usr/bin/env python3
"""
TITAN PERSONA v1 - how Titan talks
==================================
Warm British butler: polite, quick, dry humour, never stiff.

This file only decides WORDS. It does not run commands (that is T.v1.py and
titan_skills.py). Nothing here needs the internet.

SECTIONS
  0  Imports
  1  Config
  2  State (user name, reply memory)
  3  Helpers (pick a reply variant, time words)
  4  Replies for the built-in commands (time, cpu, ram, notes ...)
  5  Greetings and goodbyes
  6  Small talk (instant, no AI needed)
  7  Jokes and the "what can you do" text
  8  Chatting with the local AI model (Ollama) + conversation memory
  9  Fallback lines when the AI model is not available
"""

titan_skills.py
#!/usr/bin/env python3
"""
TITAN SKILLS v1 - the things Titan can DO
=========================================
Each skill = a "match" test (does this sentence belong to me?) + a "run"
function (do it, return what Titan should say).

HOW TO ADD YOUR OWN SKILL
  1. write  def run_mything(text): ...  that returns a sentence
  2. write  def match_mything(t): ...   that returns True/False  (t = cleaned text)
  3. add one line to SKILLS (Section 9)
  That's it - the dashboard and the AI router pick it up automatically.

SECTIONS
  0  Imports
  1  Config + paths
  2  Helpers (settings file, safe web fetch, number words, durations)
  3  Skill: open apps / folders / websites, and web search
  4  Skill: timers, alarms and reminders (+ background scheduler and alerts)
  5  Skill: weather and "set my city"
  6  Skill: news headlines
  7  Skill: PC controls (volume, screenshot, lock, battery)
  8  Alerts for the dashboard (pop / wait)
  9  The skill registry

SAFETY: Titan only launches things on the whitelists in Section 3 (or a plain
web address). It never runs a command text that you or the AI model typed.
Weather and news need the internet; everything else works offline.
"""
