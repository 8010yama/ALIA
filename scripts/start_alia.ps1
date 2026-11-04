# ALIA の起動スクリプト（Windows）
#   .\scripts\start_alia.ps1          スマホ用のサーバーを起動（ngrok の URL を作る）
#   .\scripts\start_alia.ps1 -Cli     PC で会話する（キーボード）
#   .\scripts\start_alia.ps1 -Voice   PC で会話する（マイク）
#
# Ollama と VOICEVOX が動いていなければ、裏で起動してから始める。待たずに始めるので、
# 最初の1〜2回は読み込みが間に合わず、返事や読み上げが失敗することがある。
param(
    [switch]$Cli,
    [switch]$Voice
)

$ErrorActionPreference = "Stop"

# このスクリプトの1つ上のフォルダがプロジェクトのフォルダ
$ProjectDir = Split-Path -Parent $PSScriptRoot
$VoicevoxPath = "$env:LOCALAPPDATA\Programs\VOICEVOX\VOICEVOX.exe"
$OllamaPath = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"

function Test-Url($Url) {
    try {
        Invoke-RestMethod -Uri $Url -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Start-IfNeeded($Name, $Url, $ExePath) {
    if (Test-Url $Url) {
        Write-Host "[ALIA] $Name は起動済みです。"
    } elseif (Test-Path $ExePath) {
        Write-Host "[ALIA] $Name を裏で起動します..."
        Start-Process $ExePath
    } else {
        Write-Warning "[ALIA] $Name が見つかりません: $ExePath"
        Write-Warning "[ALIA] $Name を起動してから使ってください（入れた場所が違うなら、このスクリプトのパスを書き換える）。"
    }
}

Start-IfNeeded "Ollama" "http://localhost:11434/api/tags" $OllamaPath
Start-IfNeeded "VOICEVOX" "http://localhost:50021/version" $VoicevoxPath

Set-Location $ProjectDir
& "$ProjectDir\venv\Scripts\Activate.ps1"

if ($Voice) {
    python -m alia.cli --voice
} elseif ($Cli) {
    python -m alia.cli
} else {
    python -m alia.server
}
