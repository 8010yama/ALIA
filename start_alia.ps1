# ALIA startup script
# Starts VOICEVOX (if not already running) and immediately launches main.py without waiting.
# voice_utils.speak() handles the case where VOICEVOX isn't ready yet gracefully
# (it just skips speaking that turn), so there's no need to block here.

$ErrorActionPreference = "Stop"

# ==== Edit these paths to match your environment ====
$ProjectDir = "C:\Users\YH801\My_Projects\AI_Asistant"
$VoicevoxPath = "$env:LOCALAPPDATA\Programs\VOICEVOX\VOICEVOX.exe"
# ======================================================

function Test-VoicevoxRunning {
    try {
        Invoke-RestMethod -Uri "http://localhost:50021/version" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (Test-VoicevoxRunning) {
    Write-Host "[ALIA] VOICEVOX is already running."
} else {
    if (-not (Test-Path $VoicevoxPath)) {
        Write-Warning "[ALIA] VOICEVOX executable not found: $VoicevoxPath"
        Write-Warning "[ALIA] Please edit `$VoicevoxPath in start_alia.ps1 to the correct install location."
    } else {
        Write-Host "[ALIA] Launching VOICEVOX in the background (not waiting)..."
        Start-Process $VoicevoxPath
        Write-Host "[ALIA] Note: voice replies may not work for the first message or two"
        Write-Host "[ALIA] while VOICEVOX finishes loading. This is expected."
    }
}

Write-Host "[ALIA] Activating venv..."
& "$ProjectDir\venv\Scripts\Activate.ps1"

Set-Location $ProjectDir

Write-Host "[ALIA] Starting server.py..."
python server.py