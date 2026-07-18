# Instagram Saves -> Obsidian Vault - one-time setup
# Run from the pipeline folder:  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir
Write-Host "== Instagram -> Vault pipeline setup ==" -ForegroundColor Cyan

# 1. Python
$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $pyCmd = "py"; $pyArgs = @("-3") }
else {
    $p = Get-Command python -ErrorAction SilentlyContinue
    if (-not $p) { Write-Host "Python 3 not found. Install from python.org first." -ForegroundColor Red; exit 1 }
    $pyCmd = "python"; $pyArgs = @()
}
& $pyCmd @pyArgs --version

# 2. Virtual env + dependencies
if (-not (Test-Path "$dir\venv")) {
    Write-Host "Creating virtual environment..."
    & $pyCmd @pyArgs -m venv "$dir\venv"
}
Write-Host "Installing dependencies (instaloader, faster-whisper, ffmpeg)..."
& "$dir\venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& "$dir\venv\Scripts\python.exe" -m pip install -r "$dir\requirements.txt"

# 3. Config
if (-not (Test-Path "$dir\config.json")) {
    Copy-Item "$dir\config.default.json" "$dir\config.json"
    Write-Host "Created config.json (edit to tune caps, model, folders)."
}
$cfg = Get-Content "$dir\config.json" -Raw | ConvertFrom-Json
if (-not $cfg.vault_path -or $cfg.vault_path -eq "auto") {
    $vp = Read-Host "Path to your Obsidian vault (Enter = 'auto', two levels above this folder)"
    if ($vp) {
        $cfg.vault_path = ($vp -replace '\\', '/')
        $cfg | ConvertTo-Json -Depth 5 | Set-Content "$dir\config.json"
        Write-Host "Vault set to: $vp"
    }
}

# 4. Brain mode detection
$claude = Get-Command claude -ErrorAction SilentlyContinue
if ($claude) {
    Write-Host "Claude Code CLI found -> transcripts will be reviewed by Claude locally." -ForegroundColor Green
    Write-Host "  NOTE: run 'claude' once inside your vault folder and accept the trust"
    Write-Host "  prompt, so headless runs work. (One time only.)" -ForegroundColor Yellow
} elseif ($env:ANTHROPIC_API_KEY) {
    Write-Host "ANTHROPIC_API_KEY found -> using direct API for review." -ForegroundColor Green
} else {
    Write-Host "No Claude CLI or API key found -> notes will be filed as 'unreviewed'" -ForegroundColor Yellow
    Write-Host "  (install Claude Code:  npm install -g @anthropic-ai/claude-code )"
}

# 5. Instagram session (never a password)
Write-Host ""
Write-Host "== Instagram session import ==" -ForegroundColor Cyan
& "$dir\venv\Scripts\python.exe" "$dir\import_session.py"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Session import failed - fix this before scheduling. You can re-run:" -ForegroundColor Red
    Write-Host "  venv\Scripts\python import_session.py"
}

# 6. Scheduled task (daily, randomized start for a human-ish pattern)
Write-Host ""
$taskName = "Instagram Saves to Vault"
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $taskName -Confirm:$false }
$action  = New-ScheduledTaskAction -Execute "$dir\run_pipeline.bat" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 09:30 -RandomDelay (New-TimeSpan -Minutes 45)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Downloads new saved Instagram videos, transcribes, files into Obsidian vault." | Out-Null
Write-Host "Scheduled task '$taskName' registered: daily 09:30 + up to 45 min random delay." -ForegroundColor Green

# 7. Offer a small first test
Write-Host ""
$test = Read-Host "Run a first test now (downloads up to 2 videos)? [y/N]"
if ($test -eq "y") {
    & "$dir\venv\Scripts\python.exe" "$dir\pipeline.py" --limit 2 --verbose
}
Write-Host ""
Write-Host "Setup complete. Logs: $dir\logs\  |  Run log: RUNLOG.md  |  Manual run: run_pipeline.bat" -ForegroundColor Cyan
