# ALIA の起動スクリプト（Windows）
#   .\scripts\start_alia.ps1          スマホ用のサーバーを起動（ngrok の URL を作る）
#   .\scripts\start_alia.ps1 -Cli     PC で会話する（キーボード）
#   .\scripts\start_alia.ps1 -Voice   PC で会話する（マイク）
#
# VOICEVOX が動いていなければ裏で起動してから始める。待たずに始めるので、
# 最初の1〜2回は VOICEVOX の読み込みが間に合わず、読み上げが飛ばされることがある。
param(
    [switch]$Cli,
    [switch]$Voice
)

$ErrorActionPreference = "Stop"

# このスクリプトの1つ上のフォルダがプロジェクトのフォルダ
$ProjectDir = Split-Path -Parent $PSScriptRoot
$VoicevoxPath = "$env:LOCALAPPDATA\Programs\VOICEVOX\VOICEVOX.exe"

function Test-VoicevoxRunning {
    try {
        Invoke-RestMethod -Uri "http://localhost:50021/version" -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (Test-VoicevoxRunning) {
    Write-Host "[ALIA] VOICEVOX は起動済みです。"
} elseif (Test-Path $VoicevoxPath) {
    Write-Host "[ALIA] VOICEVOX を裏で起動します..."
    Start-Process $VoicevoxPath
} else {
    Write-Warning "[ALIA] VOICEVOX が見つかりません: $VoicevoxPath"
    Write-Warning "[ALIA] このスクリプトの `$VoicevoxPath を、VOICEVOX を入れた場所に書き換えてください。"
}

Set-Location $ProjectDir
& "$ProjectDir\venv\Scripts\Activate.ps1"

if ($Voice) {
    python -m alia.cli --voice
} elseif ($Cli) {
    python -m alia.cli
} else {
    python -m alia.server
}
