<#
.SYNOPSIS
    Скачать архив RFC на машине С ИНТЕРНЕТОМ для переноса в библиотеку.
.DESCRIPTION
    RFC — главный источник ответа на вопрос «какие поля в этом кадре и что в
    них лежит». Инженер, разбирающий дамп мультиплексора или непонятное поле
    заголовка, идёт именно туда, поэтому в библиотеке им место.

    Скрипт качает тексты RFC и указатель. Указатель нужен не для красоты: из
    него берётся, какой RFC каким отменён, и отменённые редакции в поиск потом
    не попадают — иначе система с равной охотой сослалась бы и на RFC 2616, и
    на заменивший его 7230.

    Скачивание с докачкой: прервали — запустите снова, уже полученное не
    перекачивается. Между запросами пауза, чтобы не выглядеть как атака на
    сервер организации, которая раздаёт всё это бесплатно.

    Объём: около 9800 документов, примерно 450 МБ текста.
.PARAMETER Destination
    Куда складывать. По умолчанию .\rfc рядом со скриптом.
.PARAMETER From
    С какого номера начинать. По умолчанию 1.
.PARAMETER To
    Каким номером закончить. 0 — до последнего из указателя.
.PARAMETER Only
    Скачать только эти номера: -Only 791,793,2616,7230
.PARAMETER DelayMs
    Пауза между запросами в миллисекундах. По умолчанию 150.
.PARAMETER BaseUrl
    Откуда качать. По умолчанию https://www.rfc-editor.org. Если корпоративный
    шлюз этот адрес не пускает, у IETF есть зеркало: -BaseUrl https://www.ietf.org
.PARAMETER Probe
    Ничего не качать, только проверить доступность источника.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\rfc.ps1 -Probe
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\rfc.ps1 -Destination D:\rfc
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\rfc.ps1 -Only 791,793,1122,2616,7230
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\rfc.ps1 -BaseUrl https://www.ietf.org
#>
param(
    [string]$Destination = '',
    [int]$From = 1,
    [int]$To = 0,
    [int[]]$Only = @(),
    [int]$DelayMs = 150,
    [string]$BaseUrl = 'https://www.rfc-editor.org',
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

if (-not $Destination) { $Destination = Join-Path (Get-Location).Path 'rfc' }

$script:CurlExe = 'curl.exe'
if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) { $script:CurlExe = 'curl' }

$Base = $BaseUrl.TrimEnd('/')
$IndexUrl = "$Base/rfc-index.xml"

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Note($text) { Write-Host "      $text" -ForegroundColor DarkGray }

function Invoke-Curl {
    <#
        Запустить curl и вернуть его вывод и код выхода, не оборвав выгрузку.

        Windows PowerShell 5.1 при $ErrorActionPreference = 'Stop' считает
        ошибкой каждую строку, которую внешняя программа написала в поток
        ошибок, и «2>$null» от этого НЕ спасает: строка попадает туда раньше,
        чем её отбрасывают. Здесь качаются тысячи файлов по одному, и без
        этой обёртки один обрыв связи на четырёхтысячном номере обрывал бы
        весь запуск трассировкой PowerShell.
    #>
    param([string[]]$Arguments)
    $прежний = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $вывод = & $script:CurlExe @Arguments 2>&1
        $выход = $LASTEXITCODE
    } catch {
        return @{ lines = @(); exit = 1 }
    } finally {
        $ErrorActionPreference = $прежний
    }
    return @{ lines = @($вывод | ForEach-Object { "$_" }); exit = $выход }
}

<#
    Код ответа сервера и код выхода curl.

    Одного кода ответа мало. Если связь оборвалась ПОСРЕДИ файла, сервер уже
    успел ответить 200, а на диск лёг огрызок: у нас так и вышло — вместо
    RFC 794 сохранилось десять байт, и приём положил бы их в библиотеку как
    документ. Обрыв виден только по коду выхода curl (18 — «файл получен не
    целиком»), поэтому возвращаем оба и требуем, чтобы сошлись оба.

    Ноль в коде ответа означает «ответа не было вовсе» — закрытый шлюз,
    вышедшее время. Считать это за 404 нельзя.
#>
function Invoke-Download([string[]]$Arguments) {
    $ответ = Invoke-Curl -Arguments $Arguments
    $строки = @($ответ.lines)
    $код = 0
    if ($строки.Count) {
        $хвост = "$($строки[-1])".Trim()
        if ($хвост -match '^\d{3}$') { $код = [int]$хвост }
    }
    return [pscustomobject]@{ code = $код; exit = $ответ.exit }
}

function Get-HttpCode([string[]]$Arguments) {
    return (Invoke-Download $Arguments).code
}

function Test-Url([string]$url) {
    $target = if ($script:CurlExe -eq 'curl.exe') { 'NUL' } else { '/dev/null' }
    return (Get-HttpCode @('-sL', '-r', '0-0', '--max-time', '45', '-o', $target, '-w', '%{http_code}', $url))
}

# ------------------------------------------------------------- проверка ----
if ($Probe) {
    Step 'Проверка источника'
    foreach ($url in @($IndexUrl, "$Base/rfc/rfc791.txt")) {
        $code = Test-Url $url
        $status = if ($code -ge 200 -and $code -lt 400) { "OK $code" } else { "ОШИБКА $code" }
        Write-Host ("  {0,-44} {1}" -f $url.Replace($Base, ''), $status)
    }
    exit 0
}

$root = New-Item -ItemType Directory -Path $Destination -Force
Step "Указатель RFC"
$indexPath = Join-Path $root 'rfc-index.xml'
$код = Get-HttpCode @('-sL', '--max-time', '180', '--retry', '3', '-o', $indexPath, '-w', '%{http_code}', $IndexUrl)
if ($код -ne 200) {
    Write-Host "  X   не удалось скачать указатель (ответ $код)" -ForegroundColor Red
    Write-Host "      адрес: $IndexUrl"
    Write-Host '      закрыт корпоративным шлюзом — укажите своё зеркало ключом -BaseUrl'
    exit 1
}
Ok ("rfc-index.xml, {0:N1} МБ" -f ((Get-Item $indexPath).Length / 1MB))

# Из указателя берём номера, названия и — главное — чем какой RFC отменён.
[xml]$index = Get-Content $indexPath -Raw -Encoding UTF8
$entries = @{}
foreach ($entry in $index.'rfc-index'.'rfc-entry') {
    $id = "$($entry.'doc-id')"
    if ($id -notmatch '^RFC(\d+)$') { continue }
    $number = [int]$Matches[1]
    $obsoletedBy = @()
    if ($entry.'obsoleted-by') {
        foreach ($item in $entry.'obsoleted-by'.'doc-id') {
            if ("$item" -match '^RFC(\d+)$') { $obsoletedBy += [int]$Matches[1] }
        }
    }
    $entries[$number] = [pscustomobject]@{
        number = $number
        title = "$($entry.title)"
        obsoletedBy = $obsoletedBy
    }
}
Ok "в указателе документов: $($entries.Count)"

# ---------------------------------------------------------- что качаем -----
$numbers = if ($Only.Count) {
    $Only | Sort-Object -Unique
} else {
    $last = if ($To -gt 0) { $To } else { ($entries.Keys | Measure-Object -Maximum).Maximum }
    $entries.Keys | Where-Object { $_ -ge $From -and $_ -le $last } | Sort-Object
}
Step "Тексты RFC: $($numbers.Count) документов"
Note 'прервали — запустите снова, уже скачанное не перекачивается'

$texts = New-Item -ItemType Directory -Path (Join-Path (Join-Path $root 'standards') 'rfc') -Force
$done = 0; $skipped = 0; $absent = 0; $broken = 0
foreach ($number in $numbers) {
    $done++
    $target = Join-Path $texts ("rfc{0}.txt" -f $number)
    if ((Test-Path $target) -and (Get-Item $target).Length -gt 200) {
        $skipped++
        continue
    }
    # Код ответа берём сами: 404 — это нормально (часть номеров никогда не
    # публиковалась), а вот обрыв связи молча засчитывать за «нет такого RFC»
    # нельзя, иначе половина архива тихо не доедет.
    $ответ = Invoke-Download @('-sL', '--max-time', '60', '--retry', '2', '-o', $target,
                               '-w', '%{http_code}', "$Base/rfc/rfc$number.txt")
    $code = $ответ.code
    # Обрыв посреди передачи: сервер ответил 200, а файл пришёл огрызком.
    # Такой номер честнее считать не скачанным — повторный запуск его добьёт.
    $оборван = ($code -eq 200 -and $ответ.exit -ne 0)
    if ($оборван) { $code = 0 }
    if ($code -ne 200) {
        if (Test-Path $target) { Remove-Item $target -Force -ErrorAction SilentlyContinue }
        if ($code -eq 404) { $absent++ } else {
            $broken++
            $почему = if ($оборван) { 'связь оборвалась посреди файла' }
                      elseif ($code -eq 0) { 'ответа не было (связь или шлюз)' }
                      else { "ответ $code" }
            if ($broken -le 10) { Warn "RFC $number — $почему" }
            if ($broken -eq 11) { Note 'дальше об ошибках связи молчу, итог будет в конце' }
        }
    } else {
        # Отметку «чем отменён» указатель знает точнее самого файла: дописываем
        # её в шапку, если её там нет. По ней приём пометит документ
        # заменённым, и в поиск он не попадёт.
        $meta = $entries[$number]
        if ($meta -and $meta.obsoletedBy.Count) {
            $body = Get-Content $target -Raw -Encoding UTF8
            if ($body -notmatch '(?m)^Obsoleted by:') {
                $line = 'Obsoleted by: ' + ($meta.obsoletedBy -join ', ')
                $body = $body -replace '(?m)^(Request for Comments:\s*\d+.*)$', "`$1`r`n$line"
                [System.IO.File]::WriteAllText($target, $body,
                    (New-Object System.Text.UTF8Encoding($false)))
            }
        }
    }
    if ($done % 200 -eq 0) {
        Write-Progress -Activity 'Скачивание RFC' -Status "$done из $($numbers.Count)" `
                       -PercentComplete ([int](100 * $done / $numbers.Count))
        Note "$done из $($numbers.Count), пропущено уже скачанных: $skipped"
    }
    if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
}
Write-Progress -Activity 'Скачивание RFC' -Completed

$files = @(Get-ChildItem $texts -Filter '*.txt')
$size = ($files | Measure-Object Length -Sum).Sum
Write-Host ''
Ok ("файлов: {0}, объём: {1:N0} МБ" -f $files.Count, ($size / 1MB))
if ($absent) { Note "номеров без текста: $absent (эти номера не публиковались — так и должно быть)" }
if ($broken) {
    Warn "не скачано из-за ошибок связи: $broken"
    Note 'запустите скрипт ещё раз: скачанное не перекачивается, добьёт остаток'
}

Write-Host ''
Write-Host 'Дальше:' -ForegroundColor Green
Write-Host "  1) перенесите каталог $($texts.FullName) на офлайн-машину"
Write-Host '     в C:\reportgen\data\library\standards\rfc'
Write-Host '  2) там выполните:'
Write-Host '       cd C:\reportgen\app\scripts\windows' -ForegroundColor Cyan
Write-Host '       .\load-library.ps1 -Jobs 12' -ForegroundColor Cyan
Write-Host '  Названия, годы и отменённые редакции определятся сами.'
