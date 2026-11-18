# Titan Change Log

This log records Titan self-maintenance changes from 2026-10-15 onward. Earlier
project work predates this detailed change log and is not reconstructed here.
Future self-updates append the request, approval level, affected files, exact
diffs, validation results, and whether a failed change was rolled back.

## 2026-10-15 - Laptop and Titan diagnosis; guarded self-updates

- Added read-only laptop diagnosis for CPU, memory, system drive space, battery
  where available, Ollama, and dashboard availability.
- Added Titan self-diagnosis that compiles core Python modules and checks local
  Ollama/dashboard availability without running project code.
- Added a restricted self-update flow. Automatic changes are limited to
  README documentation; executable-code changes require a separate explicit
  approval request and are restricted to an allowlist of Titan source and
  dashboard files.
- Added backups, post-edit syntax/test validation, rollback on failure, and an
  append-only record of approved self-update diffs and checks.
- Added targeted file search, bounded line reads, and exact unique-text
  replacement so approved updates can safely work with larger Titan source
  files without rewriting them wholesale.
- Updated Titan's capability description and dashboard speech to announce
  diagnosis results while leaving the full report visible on screen.
- Files changed: `T.v1.1.py`, `titan_coder.py`, `titan_diagnostics.py`,
  `titan_persona.py`, `dashboard/server.py`, `README.md`, and
  `TITAN_CHANGELOG.md`.
- Validation: all six Titan core modules compile; diagnostic routing,
  self-update approval gates, README-only automatic scope, exact replacement,
  backups, and changelog diff recording passed smoke tests. A Qwen self-update
  test in a temporary project changed one allowlisted source file, preserved
  the existing function, passed both unittests, and recorded the exact diff.
  A second test updated a source file larger than the normal model read limit
  using a targeted excerpt and surgical replacement; both unittests passed.
  An automatic README-only typo correction was backed up and logged. Dashboard
  JavaScript and the PowerShell installer syntax checks passed.

## 2026-10-15 - Fixing combined capability and improvement questions

- Added deterministic detection for capability questions with contractions and
  requests asking how Titan could be improved. Combined questions now get both
  the capabilities/limitations summary and grounded optimization advice without
  consulting the command-classification model.
- Narrowed local diagnostic routing so a passing mention of "diagnosis" inside
  an unrelated question does not invoke a laptop check.
- Updated the dashboard's spoken acknowledgement and README usage guidance.
- Files changed: `T.v1.1.py`, `titan_persona.py`, `dashboard/server.py`,
  `README.md`, and `TITAN_CHANGELOG.md`.
- Validation: Python compilation and dashboard JavaScript syntax checks passed.
  The combined capabilities/optimization query routes directly to its local
  response, while explicit laptop and Titan-code diagnosis requests still
  select their respective checks. A non-imperative mention of diagnosis does
  not select a diagnostic command.

## 2026-10-15 - Explicit feedback and routing evaluation

- Added per-user feedback records for only explicitly submitted answer ratings
  and corrections. Records include the associated recent request/reply, are
  bounded and screened for sensitive information, and remain separate by user.
- Relevant feedback can guide later model replies as in-context examples. Titan
  clearly states that this does not fine-tune or change Ollama model weights.
- Added commands to rate/correct the last answer, review or erase saved feedback,
  evaluate the last answer, and run a deterministic intent-routing evaluation.
- Evaluation records are saved only when the user explicitly runs the check;
  ordinary conversations are not logged as training data.
- Long exchanges are clipped to bounded request/reply snippets before saving
  explicit feedback; sensitive-information screening applies to user-provided
  request and feedback text, avoiding false rejection from generic assistant
  explanations that mention sensitive topics.
- Files changed: `T.v1.1.py`, `titan_persona.py`, `dashboard/server.py`,
  `README.md`, `TITAN_CHANGELOG.md`, and `test_titan_learning.py`.
- Validation: all nine `test_titan_learning.py` unittests passed. They verify
  that ordinary chats are not persisted; explicit corrections/ratings are
  saved with only their associated exchange; feedback is used only for
  lexically relevant prompts; sensitive-information feedback is refused;
  long exchanges are safely truncated to bounded context; Brian/Alicia records
  remain isolated; feedback review and deletion work; and the explicit
  eight-case intent evaluation passes and persists its score. The dashboard
  API flow passed with a temporary Brain folder; Python compilation and
  dashboard JavaScript syntax checks passed.

## 2026-10-16 - Obsidian knowledge retrieval

- Added offline keyword retrieval for Markdown sections in the Obsidian
  `Knowledge/` and `Projects/` folders and the root `Projects.md`, excluding
  per-user memory notes.
- Relevant note sections are added to local chat context with source paths,
  bounded to three sections and 4,200 characters. Retrieval is read-only and
  note content is treated as reference material.
- Added retrieval tests and included the retrieval module in Titan's approved
  self-update file list.
- Added starter Knowledge and Projects notes in the detected Obsidian Brain.
- Added `sync_to_github.ps1` to stage, commit, and push only an allowlist of
  Titan source, tests, and documentation to the configured `origin`.
- Validation: all 15 unittests passed; all Titan Python modules compiled; the
  publishing script passed PowerShell syntax parsing; and a CRLF-aware diff
  whitespace check passed. Retrieval was verified against the live Obsidian
  notes. The publishing helper successfully committed and pushed to an
  isolated local Git remote; no GitHub push was performed.

## 2026-10-16 - Reducing false wake-word activations

- Removed Vosk's single-word constrained grammar from the background wake
  listener, which could force unrelated speech into a "titan" recognition.
- The listener now uses normal recognition and activates only when "Titan" is
  returned as a separate recognized word.
- Added tests for unrelated speech, punctuation, casing, and partial-word
  matches. All 18 tests, Python compilation, PowerShell syntax parsing, and
  the CRLF-aware diff check passed. Hardware microphone accuracy still needs
  testing on the laptop.

## 2026-10-18 - Switching Titan to Gemma 4

- Changed Titan's active Ollama model to the installed `gemma4:e4b` in the
  main assistant and setup model check.
- Disabled Gemma's thinking mode for routing and chat replies so its thinking
  tokens do not consume the answer budget.
- Extended the first-request timeout to 150 seconds to allow for cold model
  loading. Updated the active setup guidance, missing-model fallback, and
  README model reference.
- Validation: 18 unittests passed; Titan modules compiled; a warm local Gemma
  response ran at approximately 6 tokens/second with thinking disabled. Titan's
  actual command router returned a valid command and the full chat path returned
  a non-empty reply using Gemma.

## 2026-10-18 - Restricted REA website inspection

- Configured REA 6.7.0 as a VS Code MCP server using REA's setup flow; no bundled
  skill or repository-level MCP config was installed.
- Added an explicit Titan request for a supplied public or loopback HTTP(S) URL.
  Titan opens a dedicated Chrome/Edge profile and asks REA for passive,
  exact-origin inspection; it does not use the normal signed-in browser profile
  or submit page actions.
- Added URL/DNS checks, query/fragment rejection, origin-scoped REA calls, and
  removal of query values from evidence before local-model summarization.
- Reduced the evidence sent to Gemma to a bounded summary of page metadata, DOM
  structure, script URLs, and observed network resources. Increased only this
  local analysis request's timeout to 240 seconds. The summary explicitly notes
  that REA observes network activity only after attaching to the page.
- Validation: VS Code's user-level REA setup completed and its MCP registration
  passed REA's doctor check. The 25-unit-test suite and Python compilation pass.
  A live local page was captured by REA in an earlier run, but the final
  end-to-end browser capture could not be repeated reliably because REA
  intermittently could not reach Chrome's loopback debugging endpoint. Gemma
  did successfully summarize compact, representative REA evidence locally;
  full live Titan-to-REA-to-Gemma operation still needs a successful repeat.

## 2026-10-18 - Preserve website-analysis intent

- Titan now remembers when an explicit REA analysis request is waiting for a
  website, and treats the next URL or recognized site name (such as
  `Instagram`) as the analysis target instead of routing it to the open-site
  skill.
- Added cancellation and explicit task-change handling; opening a site remains
  a separate action. The pending request is in-memory and lasts only for the
  current Titan process.
- Added missing-target routing for common phrasing such as "I'd like to reverse
  engineer a website", and directed the model router to keep REA analysis
  separate from opening a page.
- Validation: 30 unittests pass and the modified Python modules compile.

## 2026-11-18 - Piper voice, streaming replies, and 8 GB RAM tuning

- Model choice benchmarked on the laptop (i5-1135G7, 7.7 GB RAM) with Titan's
  real router prompt and 22 routing cases:
  `gemma4:e4b` 22/22, 7.3 s per route, 8.4 tok/s, 31 s cold load, 6.6 GB;
  `qwen2.5-coder:3b` 21/22 (only miss: answered inside the router JSON, now
  prevented by the prompt), 3.5 s, 17 tok/s, 7 s load, 1.9 GB;
  `llama3.2:3b` 20/22 (wrong routes for news and cleanup), 3.9 s, 16 tok/s.
  Titan now uses `qwen2.5-coder:3b` for routing, chat and coding, so only one
  1.9 GB model is ever loaded (previously Gemma + Qwen could need ~8.5 GB).
  Models are configurable with `TITAN_CHAT_MODEL`, `TITAN_CODER_MODEL` and
  `TITAN_KEEP_ALIVE` (now 5 minutes instead of 10).
- Added `titan_voice.py`: offline Piper neural speech (default British
  `en_GB-alan-medium`, override with `TITAN_VOICE`), falling back to pyttsx3
  and then text. Used by the terminal, the wake-word listener and the
  dashboard's new `/api/tts` endpoint (WAV, token required). Voice files live
  in `voices/` and are git-ignored.
- AI chat replies now stream. `persona.llm_chat` and `titan_handle` accept an
  `on_sentence` callback; each finished sentence is spoken while the rest is
  generated. Code replies are not streamed. The dashboard uses the new
  `/api/chat-stream` endpoint (newline-delimited JSON), shows the reply as it
  arrives and plays Piper audio sentence by sentence, falling back to the
  browser voice if Piper is unavailable.
- Speed fixes found by profiling: Ollama URLs use `127.0.0.1` instead of
  `localhost` (on Windows `localhost` added ~2 s to every request); the chat
  system prompt now puts stable content first and the per-minute timestamp
  last so Ollama reuses its prompt cache; router output limit raised from 60
  to 120 tokens (60 truncated some JSON).
- Ollama user environment set to `OLLAMA_MAX_LOADED_MODELS=1`,
  `OLLAMA_NUM_PARALLEL=2` (router and chat prompts each keep a cache slot),
  `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`.
- Result: time to Titan's first spoken sentence fell from 11-22 s to about
  5 s in the terminal path and 2.5 s with a warm model in the dashboard;
  commands such as the time remain instant.
- Files changed: `T.v1.1.py`, `titan_persona.py`, `titan_voice.py` (new),
  `titan_coder.py`, `titan_diagnostics.py`, `dashboard/server.py`,
  `dashboard/index.html`, `setup_titan.py`, `requirements.txt`, `.gitignore`,
  `sync_to_github.ps1`, `README.md`, `test_titan_streaming.py` (new).
- Validation: 35 unittests pass (5 new streaming tests); all modules compile;
  dashboard JavaScript passes `node --check`; a live server test confirmed
  streamed sentences, non-streamed commands, valid Piper WAV output, and 401
  for unauthenticated TTS requests.
