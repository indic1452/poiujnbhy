<#
.SYNOPSIS
    Остановка всех процессов комплекса (llama-server и веб-приложение).
#>
. "$PSScriptRoot\_common.ps1"

Write-Step 'Останавливаю llama-server'
$stopped = 0
Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {
    $_ | Stop-Process -Force
    $stopped++
}
Write-Ok "остановлено процессов llama-server: $stopped"

Write-Step 'Останавливаю веб-приложение'
# Останавливаем ТОЛЬКО веб-сервер. Раньше под раздачу попадал любой процесс
# со словом reportgen в командной строке — в том числе идущая в соседнем окне
# загрузка библиотеки: полдня приёма документов обрывались на полуслове, и
# человек об этом даже не узнавал.
$сервер = 'reportgen(\.web\b|[^\r\n]*\bserve\b)'
$found = 0
$прочие = @()
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | ForEach-Object {
    if (-not $_.CommandLine) { return }
    if ($_.CommandLine -match $сервер) {
        Stop-Process -Id $_.ProcessId -Force
        $found++
    } elseif ($_.CommandLine -match 'reportgen') {
        $прочие += $_
    }
}
Write-Ok "остановлено процессов приложения: $found"
foreach ($это in $прочие) {
    $что = 'работа'
    if ($это.CommandLine -match '\bingest\b') { $что = 'приём библиотеки' }
    elseif ($это.CommandLine -match '\bembed\b') { $что = 'построение векторов' }
    Write-Warn2 "не тронут процесс $($это.ProcessId): $что — остановите его сами, если нужно"
}
