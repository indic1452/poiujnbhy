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

function Wait-Http($url, $seconds = 180, $process = '') {
    <#
        Ждать, пока служба ответит. Молчать всё это время нельзя: человек
        пять минут смотрит в пустой экран и не знает, идёт ли что-то вообще.
        И если процесс, которого мы ждём, уже умер, ждать его до конца срока
        бессмысленно — об этом было известно на второй секунде.
    #>
    $deadline = (Get-Date).AddSeconds($seconds)
    $начало = Get-Date
    $сказано = 0
    while ((Get-Date) -lt $deadline) {
        try {
            $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3
            return $true
        } catch { }
        $прошло = [int]((Get-Date) - $начало).TotalSeconds
        if ($process -and $прошло -ge 10) {
            if (-not (Get-Process $process -ErrorAction SilentlyContinue)) {
                Write-Warn2 "процесс $process не запущен — ждать больше нечего"
                return $false
            }
        }
        if ($прошло - $сказано -ge 15) {
            $сказано = $прошло
            Write-Host "      ждём... ($прошло с из $seconds)" -ForegroundColor DarkGray
        }
        Start-Sleep -Milliseconds 800
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
    # Метрики у Get-NetIPAddress нет — она у Get-NetIPInterface. Сортировка по
    # несуществующему свойству молча ничего не сортирует, и коллегам называли
    # адрес первой попавшейся сетевой карты: у машины с виртуальными
    # адаптерами (VirtualBox, Hyper-V) это почти всегда не тот адрес.
    try {
        $метрики = @{}
        foreach ($карта in (Get-NetIPInterface -AddressFamily IPv4 -ErrorAction Stop)) {
            if ($карта.ConnectionState -eq 'Connected') {
                $метрики[[int]$карта.InterfaceIndex] = [int]$карта.InterfaceMetric
            }
        }
        $годные = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.IPAddress -ne '127.0.0.1' -and
                $_.IPAddress -notlike '169.254.*' -and
                $_.PrefixOrigin -ne 'WellKnown' -and
                $метрики.ContainsKey([int]$_.InterfaceIndex)
            }
        $лучший = $годные |
            Sort-Object -Property @{ Expression = { $метрики[[int]$_.InterfaceIndex] } } |
            Select-Object -First 1
        if ($лучший) { return $лучший.IPAddress }
    } catch { }

    # Запасной способ, работающий и там, где модуля NetTCPIP нет: спросить у
    # системы, с какого адреса она пошла бы наружу. Ни одного пакета при этом
    # не отправляется — сокет только выбирает маршрут.
    try {
        $проба = New-Object System.Net.Sockets.Socket(
            [System.Net.Sockets.AddressFamily]::InterNetwork,
            [System.Net.Sockets.SocketType]::Dgram,
            [System.Net.Sockets.ProtocolType]::Udp)
        $проба.Connect('10.255.255.255', 1)
        $адрес = $проба.LocalEndPoint.Address.ToString()
        $проба.Dispose()
        if ($адрес -and $адрес -ne '0.0.0.0' -and $адрес -ne '127.0.0.1') { return $адрес }
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
    #
    # llama-server печатает справку в поток ОШИБОК. В Windows PowerShell 5.1
    # при $ErrorActionPreference = 'Stop' это обрывалось исключением, справка
    # выходила пустой — и скрипты решали, что сборка не умеет ни одного флага,
    # советуя обновить llama.cpp там, где скачать её неоткуда.
    $exe = Get-LlamaServer
    $прежний = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        return (& $exe --help 2>&1 | Out-String)
    } catch {
        return ''
    } finally {
        $ErrorActionPreference = $прежний
    }
}

function Get-LlamaServer {
    $exe = Join-Path $script:Llama 'llama-server.exe'
    if (-not (Test-Path $exe)) {
        throw "не найден $exe — распакуйте сборку llama.cpp в $script:Llama (см. docs/11-windows.md, шаг 3)"
    }
    return $exe
}

function Get-DataPlace($name) {
    <#
        Где лежит та или иная часть данных — по слову самого приложения.

        Скрипт, помнящий пути сам, расходится с приложением молча: библиотека
        искалась в C:\reportgen\data\library, даже когда data_dir указывал на
        другой диск, и «файлов на диске: 0» выглядело как пустая библиотека, а
        не как поиск не в том месте.
    #>
    $python = Get-PythonExe
    $env:PYTHONPATH = Join-Path $script:Root 'src'
    $env:REPORTGEN_CONFIG = $script:Config
    $прежний = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $вывод = & $python -m reportgen --config $script:Config paths --json 2>&1
    } finally {
        $ErrorActionPreference = $прежний
    }
    foreach ($строка in @($вывод | ForEach-Object { "$_" })) {
        if (-not $строка.TrimStart().StartsWith('{')) { continue }
        try { $опись = $строка | ConvertFrom-Json } catch { continue }
        foreach ($место in $опись.places) {
            if ($место.имя -eq $name) { return $место.путь }
        }
        if ($name -eq 'data_dir') { return $опись.data_dir }
    }
    return ''
}

function Get-Setting($name, $fallback) {
    if (-not (Test-Path $script:Config)) { return $fallback }
    # Файл настроек правят руками. Лишняя запятая в нём роняла каждый скрипт
    # сырым исключением ConvertFrom-Json, из которого не понять ни имени
    # файла, ни того, что делать.
    try {
        $json = Get-Content $script:Config -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        Write-Bad "файл настроек $script:Config испорчен: $($_.Exception.Message)"
        Write-Host '     откройте его и проверьте запятые и кавычки;'
        Write-Host "     образец рядом: $(Join-Path $PSScriptRoot 'settings.example.json')"
        throw "испорчен файл настроек $script:Config"
    }
    $value = $json.$name
    if ($null -eq $value -or "$value" -eq '') { return $fallback }
    return $value
}
