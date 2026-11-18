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
the Vosk model. The wake-word listener uses unrestricted recognition and only
triggers when Vosk recognizes "Titan" as a separate word; it does not force all
speech into that single-word result. Use the dashboard's WAKE WORD button to
pause or resume listening.
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
standard test suite. General chat and command routing use the same model, so
Ollama never has to hold or swap two models on an 8 GB laptop. The coding
helper is restricted to the named folder under your Windows user directory;
it cannot delete files, read common credential files, or run arbitrary shell
commands. Backups are stored in `.titan-backups`. Review its changes: this
small local model can make mistakes and is not a master coder.

Titan keeps recent conversation context while it is running. Saved memories,
notes, and reminders persist between launches. Say "remember that..."
to save a fact, "what do you remember about me?" to review memories, or
"forget ..." / "forget everything" to remove them.

Saved facts are kept in the signed-in user's note in the Obsidian `Titan Brain`
folder. The `Projects.md` note remains available as project background, and
relevant sections from Markdown notes under `Knowledge/` and `Projects/` are
searched for each chat request. Retrieval is keyword-based, read-only, and
limited to three matching note sections and 4,200 characters; personal root
memory notes are not part of the knowledge search. Treat retrieved note text as
reference material, not instructions. Because the vault is under OneDrive
Documents, its contents may sync if OneDrive sync is enabled.

Add topic notes under `Titan Brain\Knowledge\` and project notes under
`Titan Brain\Projects\`. Starter notes for Python, cybersecurity, Linux,
artificial intelligence, and Arduino, plus Titan and ANPR projects, are in the
detected Obsidian Brain folder. For GitHub publishing, run
`.\sync_to_github.ps1` from PowerShell after reviewing your changes. It commits
and pushes only allowlisted Titan source, tests, and documentation to `origin`;
it does not include profile, memory, Obsidian vault, or screenshot files.

## Restricted REA website inspection

REA 6.7.0 is configured as a VS Code MCP server. Restart VS Code to load it.
REA's VS Code integration exposes its broader reverse-engineering toolkit;
use REA's exact-origin browser filters and dedicated browser instructions
when inspecting a page directly from VS Code.

Titan itself only starts page inspection for an explicit analysis request, for
example `Use REA to reverse engineer this webpage https://example.com/`. If you
ask to analyze a website without naming one, Titan remembers that it is waiting
for the target; a reply such as `Instagram` continues the REA analysis instead
of becoming an open-site command. Say `cancel` to abandon the request, or give
another explicit command to change tasks. This pending request lasts only for
the current Titan process. Titan opens the page in a separate Chrome/Edge
profile with sync and extensions disabled, then uses REA for passive inspection
only. The URL must be HTTP(S), have no query or fragment, and resolve to a
public host or loopback development server
(`localhost`, `127.0.0.1`, or `::1`). Titan does not use your everyday signed-in
browser profile, follow cross-origin redirects, click controls, submit forms,
or browse pages on its own. Page evidence is treated as untrusted input and
network query values and URL credentials are removed before the local model
receives the findings. Titan includes bounded page metadata, DOM structure and
script URLs in its summary context. Network observation
starts after REA attaches, so it cannot report requests that happened earlier.
The page is rendered normally in the isolated browser; its scripts run as they
would in a regular browser. Close Titan's separate REA browser window when
finished.

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

## Independence (titan_autonomy.py)

- **Multi-step tasks**: `task: <goal>` or `get me set up for revision` makes
  Titan plan and chain its own actions. `X and then Y` made of known commands
  runs each part directly, without the AI. Supervised mode allows 6 actions per
  task, autonomous mode 25 (`switch to autonomous mode` / `supervised mode`).
- **Approval gates stay on**: during tasks and routines Titan cannot delete
  files, lock the PC, edit code or inspect websites; you must ask directly.
- **Self-check** (`turn on/off self checking`): before replying, Titan checks
  its draft against what each action actually returned.
- **Watchdog** (`turn on/off the watchdog`): every minute it checks battery,
  memory, CPU, disk and Ollama, and speaks up only when something needs you.
- **Heartbeat**: every 30 minutes, at most one useful item (a routine
  suggestion, a reminder due within the hour, a long uptime).
- **Daily briefing**: weather, headlines, reminders and laptop health at 08:00
  by default. `give me my briefing`, `set my briefing for 7:30am`,
  `turn off my briefing`.
- **Standing goals**: `every morning at 8 check the weather and the news` runs
  as a task each day and reports back. `list my goals`, `cancel goal 1`.
- **Routine learning**: when the same 2-4 commands happen in a row in three
  separate sessions, Titan suggests a routine; nothing is saved until you say
  `adopt routine 1` (optionally `as revision`). Run with `run routine revision`
  or `/revision`. `list routines`, `dismiss routine 1`, `forget my routines`,
  `stop learning routines`. Only command names and wording are logged, locally
  in `titan_autonomy.json` (git-ignored).
- The dashboard shows all of this in the AUTO // AGENT panel, and saved
  memories in MEM // WHAT I KNOW, where each can be deleted.

## Garage: 3D models, parts lists and build steps (titan_garage.py)

- Tap GARAGE on the wheel, or say `open the garage` / `show me the front end`.
  Drag to rotate, pinch/scroll to zoom, use EXPLODE to pull parts apart, tap a
  part for its details. STEPS lights up the parts for each step, with a
  checklist and who does it (you or the mechanic). PROJECT shows owned /
  considering / check items, totals in pounds, and the next moves.
- Built-in models: `dashboard/models/*.json` (E87 front end, E87 TitanDrive
  head unit). They are schematic (positions and order, not to scale); each
  part links to a project item so its colour shows its status.
- Project commands: `car status`, `what needs checking`, `what am I
  considering`, `what do I own`, `next moves`, `garage budget`,
  `mark the radiator as checked`, `add wrap film to my garage for £120`,
  `the head unit costs £185`. Data: `titan_garage.json` (git-ignored).
- Custom parts you can 3D print: `design a 52mm gauge pod`,
  `design an enclosure 90 by 60 by 30`, `design a bracket 40mm`. These have
  real holes; EXPORT STL saves a millimetre STL for your slicer.
- Car facts come from checked sources in the Obsidian note
  `Projects/E87 build.md`, not from the language model's memory. Always
  confirm part numbers and fitment against the VIN.
- three.js 0.160.0 is stored in `dashboard/vendor/` so the garage works offline.
- Animagraffs-style presentation: labels with pointer lines (LABELS: KEY /
  ALL / OFF), animated steps where parts slide off and back on (NEXT, arrow
  keys, or ▶ PLAY), moving colour flows (coolant, intake air, boost air through
  the intercooler), a CUTAWAY slice and studio lighting.
- Real 3D models: tap ＋ 3D FILE (or drag a file onto the view) to add a
  `.glb`, `.gltf` (embedded) or `.obj` up to 150 MB; it's stored in
  `garage_models/` (git-ignored). Titan scales and centres it automatically.
  Then MAP PARTS: pick a part, tap the matching pieces of the model, turn or
  resize the model if it's sideways, and SAVE MAPPING. Labels, status colours,
  steps and flows then work on the real model. GLB is best.

## Phone (Tailscale)

- Open the private `https://<pc>.ts.net/` address, then Share > Add to Home
  Screen to use Titan like an app. The session is remembered on the phone
  until the Titan server restarts.
- MIC records on the device you're using (phone or laptop); the audio is
  transcribed offline on the laptop by Vosk (`/api/transcribe`). Replies are
  spoken through the phone with the Piper voice.

## Voice and performance

- Titan speaks with a local Piper neural voice (`titan_voice.py`). Voice files
  live in `voices/` as `<name>.onnx` plus `<name>.onnx.json`; the default is
  the British `en_GB-alan-medium` (download it from
  https://huggingface.co/rhasspy/piper-voices). Set the `TITAN_VOICE`
  environment variable to switch, for example `en_US-john-medium` or
  `en_US-lessac-medium`.
  If Piper is unavailable Titan falls back to pyttsx3, then text only.
- AI chat replies stream: each sentence is spoken (and shown in the dashboard)
  as soon as the model writes it, instead of after the whole answer.
- Model settings (environment variables): `TITAN_CHAT_MODEL` (default
  `qwen2.5-coder:3b`, used for routing and chat), `TITAN_CODER_MODEL` (default
  `qwen2.5-coder:3b`) and `TITAN_KEEP_ALIVE` (default `5m`: how long the model
  stays in RAM after a reply). On the 8 GB laptop, `gemma4:e4b` routed most
  accurately (22/22 vs 21/22) but ran at half the speed and needs 6.6 GB.
- Recommended Ollama settings for 8 GB RAM: `OLLAMA_MAX_LOADED_MODELS=1`,
  `OLLAMA_NUM_PARALLEL=2`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`.
  Two slots let the router prompt and the chat prompt each stay cached; with one
  slot they evict each other and every reply reprocesses ~900 tokens (8-13 s).
- Titan talks to Ollama on `127.0.0.1`, not `localhost`: on Windows `localhost`
  adds about 2 seconds to every request while IPv6 times out.

## Needs

- Python 3.10+ (3.12 recommended)
- Ollama running with `ollama pull qwen2.5-coder:3b`
- A Piper voice in `voices/` (optional; see above)

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
