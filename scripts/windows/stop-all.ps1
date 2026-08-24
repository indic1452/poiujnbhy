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
$found = 0
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | ForEach-Object {
    if ($_.CommandLine -and $_.CommandLine -match 'reportgen') {
        Stop-Process -Id $_.ProcessId -Force
        $found++
    }
}
Write-Ok "остановлено процессов приложения: $found"
