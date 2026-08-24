<#
.SYNOPSIS
    Запуск веб-приложения (интерфейс инженера) на порту из settings.json.
.PARAMETER OpenBrowser
    Открыть браузер после старта.
#>
param([switch]$OpenBrowser)

. "$PSScriptRoot\_common.ps1"

if (-not (Test-Path $script:Config)) {
    throw "нет файла настроек $script:Config — сначала запустите .\01-install.ps1"
}

$port = [int](Get-Setting 'port' 8080)
$host_ = Get-Setting 'host' '127.0.0.1'

if (-not (Test-Port $port)) { throw "порт $port занят — приложение уже запущено?" }

Write-Step "Веб-интерфейс: http://$host_`:$port"
if ($OpenBrowser) {
    Start-Job -ScriptBlock {
        param($url)
        Start-Sleep -Seconds 4
        Start-Process $url
    } -ArgumentList "http://127.0.0.1:$port" | Out-Null
}

Invoke-Reportgen serve
