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
