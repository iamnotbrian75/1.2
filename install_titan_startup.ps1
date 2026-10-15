param(
    [switch]$Uninstall,
    [switch]$EnablePhoneAccess
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "Titan Wake Word.lnk"

if ($Uninstall) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath
    }
    Write-Output "Titan will no longer start automatically when you sign in."
    exit 0
}

$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$server = Join-Path $root "dashboard\server.py"
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "Titan's virtual environment is missing. Run setup_titan.py first."
}
if (-not (Test-Path -LiteralPath $server -PathType Leaf)) {
    throw "Titan's dashboard server is missing: $server"
}
if (-not (Test-Path -LiteralPath (Join-Path $root "vosk-model") -PathType Container)) {
    throw "Titan's offline Vosk model is missing. Run setup_titan.py first."
}

$arguments = "`"$server`" --wake-word"
if ($EnablePhoneAccess) {
    $tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($tailscale) {
        $tailscalePath = $tailscale.Source
    } else {
        $tailscalePath = "C:\Program Files\Tailscale\tailscale.exe"
    }
    if (-not (Test-Path -LiteralPath $tailscalePath -PathType Leaf)) {
        throw "Tailscale CLI was not found. Install Tailscale and sign in, then try again."
    }

    $statusText = & $tailscalePath status --json
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read Tailscale status. Confirm Tailscale is signed in and connected."
    }
    try {
        $tailscaleStatus = $statusText | ConvertFrom-Json
    } catch {
        throw "Tailscale returned invalid status data."
    }
    $tailnetHost = ([string]$tailscaleStatus.Self.DNSName).TrimEnd(".").ToLowerInvariant()
    if ($tailscaleStatus.BackendState -ne "Running" -or
        -not $tailscaleStatus.CurrentTailnet.MagicDNSEnabled -or
        -not $tailnetHost.EndsWith(".ts.net")) {
        throw "Tailscale must be connected with MagicDNS enabled before phone access can be configured."
    }
    $arguments += " --tailnet-host `"$tailnetHost`""
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $root
$shortcut.Description = "Start Titan's offline wake-word assistant"
$shortcut.Save()

Write-Output "Titan will start silently when you sign in and listen locally for the wake word 'Titan'."
Write-Output "When you say 'Titan', the dashboard will open and Titan will answer. Then speak your command."
if ($EnablePhoneAccess) {
    Write-Output "Private phone access is enabled for https://$tailnetHost/ (Tailscale tailnet only)."
    Write-Output "Keep Tailscale connected on both devices."
}
