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
# Схема берётся из настроек: при включённом https адрес http://... не
# откроется вовсе, и человек пойдёт искать, что сломалось.
$схема = 'http'
if ([bool](Get-Setting 'https' $false)) { $схема = 'https' }

if (-not (Test-Port $port)) { throw "порт $port занят — приложение уже запущено?" }

# Окружения нет — Get-PythonExe молча берёт системный Python, в котором
# зависимостей не стоит. Падение будет позже и не про то.
if (-not (Test-Path (Join-Path $script:Venv 'Scripts\python.exe'))) {
    Write-Warn2 "нет окружения $script:Venv — беру системный python"
    Write-Host '     если приложение не поднимется, выполните .\01-install.ps1'
}

if ($host_ -eq '0.0.0.0' -or $host_ -eq '::') {
    # Слушаем всю сеть: показываем адрес, который коллеги наберут в браузере.
    $lan = Get-LanAddress
    Write-Step "Веб-интерфейс: $схема`://127.0.0.1`:$port (на этой машине)"
    if ($lan) {
        Write-Ok "коллегам по сети отдела: $схема`://$lan`:$port"
    } else {
        Write-Warn2 'адрес в сети не определился — посмотрите ipconfig'
    }
    Write-Warn2 'порт должен быть открыт в брандмауэре, вход по паролю обязателен'
} else {
    Write-Step "Веб-интерфейс: $схема`://$host_`:$port"
    Write-Warn2 "слушаем только эту машину; чтобы открыть отделу, поставьте host = 0.0.0.0 в $script:Config"
}
if ($OpenBrowser) {
    Start-Job -ScriptBlock {
        param($url)
        Start-Sleep -Seconds 4
        Start-Process $url
    } -ArgumentList "$схема`://127.0.0.1:$port" | Out-Null
}

Invoke-Reportgen serve
