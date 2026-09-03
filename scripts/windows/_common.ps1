# Общие настройки и вспомогательные функции для всех скриптов.
# Подключается так:  . "$PSScriptRoot\_common.ps1"

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Корень установки: на два уровня выше каталога scripts\windows.
$script:Root   = (Resolve-Path "$PSScriptRoot\..\..").Path
$script:Base   = if ($env:REPORTGEN_HOME) { $env:REPORTGEN_HOME } else { 'C:\reportgen' }
$script:Models = Join-Path $script:Base 'models'
$script:Llama  = Join-Path $script:Base 'llama'
$script:Data   = Join-Path $script:Base 'data'
$script:Logs   = Join-Path $script:Base 'logs'
$script:Config = Join-Path $script:Base 'settings.json'
$script:Venv   = Join-Path $Root '.venv'

function Write-Step($text)  { Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Ok($text)    { Write-Host "  OK  $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Write-Bad($text)   { Write-Host "  X   $text" -ForegroundColor Red }

function Test-Port($port) {
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $port)
        $listener.Start(); $listener.Stop(); return $true          # порт свободен
    } catch { return $false }                                       # порт занят
}

function Wait-Http($url, $seconds = 180) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
            return $true
        } catch { Start-Sleep -Milliseconds 800 }
    }
    return $false
}

function Get-LanAddress {
    <#
        Адрес машины в сети отдела — тот, который коллеги набирают в браузере.
        При host = 0.0.0.0 печатать «http://0.0.0.0:8080» бессмысленно: такой
        адрес не открывается ниоткуда, и человек идёт спрашивать, что вводить.
        Берём первый адрес IPv4, который не петля и не APIPA (169.254.x).
    #>
    try {
        $found = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -ne '127.0.0.1' -and
                $_.IPAddress -notlike '169.254.*' -and
                $_.PrefixOrigin -ne 'WellKnown'
            } |
            Sort-Object -Property InterfaceMetric |
            Select-Object -First 1
        if ($found) { return $found.IPAddress }
    } catch { }
    return ''
}

function Get-PythonExe {
    $venvPython = Join-Path $script:Venv 'Scripts\python.exe'
    if (Test-Path $venvPython) { return $venvPython }
    return 'python'
}

function Invoke-Reportgen {
    param([Parameter(ValueFromRemainingArguments = $true)] $Arguments)
    $python = Get-PythonExe
    $env:PYTHONPATH = Join-Path $script:Root 'src'
    $env:REPORTGEN_CONFIG = $script:Config
    & $python -m reportgen @Arguments
}

function Get-LlamaHelp {
    # Флаги llama.cpp меняются между сборками — определяем поддержку по --help,
    # чтобы скрипты работали и на свежей, и на прошлогодней сборке.
    $exe = Get-LlamaServer
    try { return (& $exe --help 2>&1 | Out-String) } catch { return '' }
}

function Get-LlamaServer {
    $exe = Join-Path $script:Llama 'llama-server.exe'
    if (-not (Test-Path $exe)) {
        throw "не найден $exe — распакуйте сборку llama.cpp в $script:Llama (см. docs/11-windows.md, шаг 3)"
    }
    return $exe
}

function Get-Setting($name, $fallback) {
    if (-not (Test-Path $script:Config)) { return $fallback }
    $json = Get-Content $script:Config -Raw -Encoding UTF8 | ConvertFrom-Json
    $value = $json.$name
    if ($null -eq $value -or "$value" -eq '') { return $fallback }
    return $value
}
