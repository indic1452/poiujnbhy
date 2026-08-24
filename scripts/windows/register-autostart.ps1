<#
.SYNOPSIS
    Автозапуск комплекса при входе пользователя в систему (планировщик задач).
.PARAMETER Remove
    Удалить задачу автозапуска.
.NOTES
    Запускать от имени того пользователя, под которым работает система.
#>
param([switch]$Remove, [int]$CpuMoe = 0)

. "$PSScriptRoot\_common.ps1"

$taskName = 'Reportgen'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Ok 'задача автозапуска удалена'
    return
}

$script = Join-Path $PSScriptRoot 'start-all.ps1'
$argument = "-NoExit -ExecutionPolicy Bypass -File `"$script`""
if ($CpuMoe -gt 0) { $argument += " -CpuMoe $CpuMoe" }

$action    = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument
$trigger   = New-ScheduledTaskTrigger -AtLogOn
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                                          -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
                                          -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null

Write-Ok "задача '$taskName' зарегистрирована: комплекс будет подниматься при входе в систему"
Write-Host 'Проверить: Планировщик заданий → Библиотека → Reportgen'
