param(
    [string]$CommitMessage = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$publishPaths = @(
    "README.md",
    "setup_titan.py",
    "TITAN_CHANGELOG.md",
    "T.v1.1.py",
    "titan_coder.py",
    "titan_diagnostics.py",
    "titan_knowledge.py",
    "titan_persona.py",
    "titan_rea.py",
    "titan_skills.py",
    "titan_voice.py",
    "titan_autonomy.py",
    "test_titan_autonomy.py",
    "titan_garage.py",
    "test_titan_garage.py",
    "dashboard/garage.js",
    "dashboard/models/e87-front-end.json",
    "dashboard/models/e87-titandrive.json",
    "dashboard/vendor/three.module.min.js",
    "dashboard/vendor/OrbitControls.js",
    "dashboard/vendor/STLExporter.js",
    "dashboard/vendor/GLTFLoader.js",
    "dashboard/vendor/OBJLoader.js",
    "dashboard/vendor/BufferGeometryUtils.js",
    "dashboard/vendor/RoomEnvironment.js",
    ".gitignore",
    "requirements.txt",
    "test_titan_streaming.py",
    "test_titan_knowledge.py",
    "test_titan_learning.py",
    "test_titan_rea.py",
    "test_titan_wake_word.py",
    "sync_to_github.ps1",
    "dashboard/server.py",
    "dashboard/index.html"
)

function Invoke-Git {
    param([string[]]$GitArguments)
    & git -C $repoRoot @GitArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git command failed: git $($GitArguments -join ' ')"
    }
}

$topLevel = (& git -C $repoRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or [System.IO.Path]::GetFullPath($topLevel) -ne [System.IO.Path]::GetFullPath($repoRoot)) {
    throw "The script must be run from inside its Git repository."
}

$remote = & git -C $repoRoot remote get-url origin
if ($LASTEXITCODE -ne 0 -or -not $remote) {
    throw "No GitHub 'origin' remote is configured."
}

$stagedPaths = @(& git -C $repoRoot diff --cached --name-only)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect staged Git changes."
}
$unexpectedStaged = @($stagedPaths | Where-Object { $_ -notin $publishPaths })
if ($unexpectedStaged.Count -gt 0) {
    throw "Unrelated files are already staged; review or unstage them first: $($unexpectedStaged -join ', ')"
}

Invoke-Git -GitArguments (@("add", "--") + $publishPaths)

& git -C $repoRoot diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Output "No Titan source or documentation changes to publish."
    exit 0
}
if ($LASTEXITCODE -ne 1) {
    throw "Could not inspect staged changes."
}

if (-not $CommitMessage.Trim()) {
    $CommitMessage = "Update Titan ($((Get-Date).ToString('yyyy-MM-dd HH:mm')))"
}
Invoke-Git -GitArguments @(
    "commit",
    "--message", $CommitMessage,
    "--trailer", "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
)
Invoke-Git -GitArguments @("push", "origin", "HEAD")
