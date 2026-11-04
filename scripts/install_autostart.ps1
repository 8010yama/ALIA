# PC にログインしたら、ALIA のサーバーを自動で起動するように登録する（Windows のタスク スケジューラ）
#
#   .\scripts\install_autostart.ps1           登録する（次にログインしたときから自動で起動）
#   .\scripts\install_autostart.ps1 -Remove   登録をやめる
#
# 起動したサーバーは、最小化したウィンドウで動き続ける。止めるときは、そのウィンドウで Ctrl+C。
# PC がスリープすると iPhone から届かなくなるので、使うときは PC をスリープさせない設定にしておく。
param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "ALIA"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "[ALIA] 自動起動の登録をやめました。"
    } else {
        Write-Host "[ALIA] 自動起動は登録されていません。"
    }
    exit 0
}

$StartScript = Join-Path $PSScriptRoot "start_alia.ps1"
if (-not (Test-Path $StartScript)) {
    throw "start_alia.ps1 が見つかりません: $StartScript"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$StartScript`"" `
    -WorkingDirectory (Split-Path -Parent $PSScriptRoot)

# ログインしてから 30 秒待つ（ネットや Ollama の準備ができるまで）
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Trigger.Delay = "PT30S"

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "ALIA のサーバーをログイン時に起動する" `
    -Force | Out-Null

Write-Host "[ALIA] 登録しました。次に PC にログインすると、ALIA のサーバーが自動で起動します。"
Write-Host "[ALIA] 今すぐ起動するなら: Start-ScheduledTask -TaskName $TaskName"
