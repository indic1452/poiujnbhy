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

<#
    Какие форматы этого RFC вообще существуют — по указателю.

    В указателе у каждой записи есть перечень форматов (<file-format>ASCII,
    PDF, HTML, XML). Он и есть ответ на вопрос «а текст-то у него есть».
    Прежде скрипт этого не спрашивал: просил .txt у всех подряд и на 404
    писал «эти номера не публиковались». Утверждение неверное — по указателю
    он ходит ТОЛЬКО по опубликованным номерам, — и оно скрывало настоящий
    пробел: у части RFC текстовой версии нет, есть только PDF.

    Разбор нарочно терпимый. Указатель у МСЭ и IETF меняется, пространство
    имён в XML мешает точным выборкам, а ошибиться здесь дороже, чем сходить
    лишний раз: пустой перечень означает «не знаем» и приводит к обычному
    порядку проб, а не к пропуску документа.
#>
function Read-RfcFormats($entry) {
    $найдено = @()
    try {
        foreach ($f in @($entry.format)) {
            if ($null -eq $f) { continue }
            if ($f -is [string]) { $найдено += $f.Trim().ToUpperInvariant(); continue }
            # Внутри ОДНОГО <format> лежит несколько <file-format> — вот так:
            #   <format><file-format>TXT</file-format>
            #           <file-format>HTML</file-format></format>
            # Прежний разбор брал их одним выражением, PowerShell склеивал
            # массив в строку «TXT HTML», и она не совпадала ни с одним
            # форматом. Пробегаем по каждому.
            foreach ($имя in @($f.'file-format')) {
                if ($имя) { $найдено += "$имя".Trim().ToUpperInvariant() }
            }
        }
    } catch { }
    if (-not $найдено.Count) {
        # Точечный разбор не дался — читаем как текст. Некрасиво, зато не
        # зависит ни от пространства имён, ни от формы записи.
        try {
            foreach ($m in [regex]::Matches("$($entry.OuterXml)",
                                            '(?is)<file-format>\s*([^<]+?)\s*</file-format>')) {
                $найдено += $m.Groups[1].Value.Trim().ToUpperInvariant()
            }
        } catch { }
    }
    return @($найдено | Where-Object { $_ } | Select-Object -Unique)
}

#: Что пробовать скачивать и в каком порядке.
#:
#: Текст первым — он и разбирается лучше всех, и в библиотеке удобнее. Нет
#: текста — берём PDF, потом HTML, потом XML: документ на своём месте нужнее,
#: чем красивый формат. Пустой перечень означает «указатель не сказал»: тогда
#: пробуем всё по очереди, а не решаем за него.
function Get-RfcCandidates([string[]]$formats) {
    # Возвращаем ОБЪЕКТЫ, а не пары в массивах. PowerShell при возврате
    # разворачивает массив из одного массива: список с единственным
    # кандидатом @(@('txt','text/plain')) превращался в @('txt','text/plain'),
    # цикл шёл по строкам, и скрипт просил у сервера «rfc1.t». Ловушка тихая:
    # на двух кандидатах всё работало, на одном — нет.
    $известно = @($formats)
    $порядок = @()
    if (-not $известно.Count -or ($известно -match '^(ASCII|TEXT|TXT)$')) {
        $порядок += [pscustomobject]@{ ext = 'txt'; label = 'ASCII' }
    }
    foreach ($пара in @(@('pdf', 'PDF'), @('html', 'HTML'), @('xml', 'XML'))) {
        if (-not $известно.Count -or ($известно -contains $пара[1])) {
            $порядок += [pscustomobject]@{ ext = $пара[0]; label = $пара[1] }
        }
    }
    if (-not $порядок.Count) {
        $порядок += [pscustomobject]@{ ext = 'txt'; label = 'ASCII' }
    }
    return @($порядок)
}

#: Похоже ли скачанное на то, что просили. Сервер на «нет такого файла»
#: отвечает и страницей, и она пройдёт проверку по размеру.
function Test-RfcFile([string]$path, [string]$kind) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $размер = (Get-Item -LiteralPath $path).Length
    if ($размер -le 200) { return $false }
    try {
        $поток = [IO.File]::OpenRead($path)
        try {
            $голова = New-Object byte[] 5
            $прочитано = $поток.Read($голова, 0, 5)
        } finally { $поток.Dispose() }
    } catch { return $false }
    $начало = [Text.Encoding]::ASCII.GetString($голова, 0, [Math]::Max(0, $прочитано))
    if ($kind -eq 'pdf') { return ($начало -eq '%PDF-') }
    if ($kind -eq 'txt') {
        # Текст RFC не начинается с угловой скобки. Страница «404» начинается.
        return (-not $начало.TrimStart().StartsWith('<'))
    }
    return $true
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
        formats = (Read-RfcFormats $entry)
    }
}
Ok "в указателе документов: $($entries.Count)"

# Номера, которых в указателе нет вовсе, — вот они действительно никогда не
# публиковались. Это единственный честный источник такого утверждения:
# отсутствие файла на сервере им не является.
$последний = ($entries.Keys | Measure-Object -Maximum).Maximum
$неВыпускались = 0
for ($n = 1; $n -le $последний; $n++) { if (-not $entries.ContainsKey($n)) { $неВыпускались++ } }
if ($неВыпускались) {
    Note "номеров, которых нет в указателе: $неВыпускались (не публиковались)"
}

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
$done = 0; $skipped = 0; $absent = 0; $broken = 0; $other = 0
$неудачи = @()
foreach ($number in $numbers) {
    $done++
    $meta = $entries[$number]
    $кандидаты = Get-RfcCandidates ($(if ($meta) { $meta.formats } else { @() }))

    # Уже скачанное пропускаем в любом формате: повторный запуск не должен
    # заново тянуть RFC только потому, что он лежит не текстом, а PDF.
    $ужеЕсть = $false
    foreach ($вид in $кандидаты) {
        $путь = Join-Path $texts ("rfc{0}.{1}" -f $number, $вид.ext)
        if (Test-RfcFile $путь $вид.ext) { $ужеЕсть = $true; break }
    }
    if ($ужеЕсть) { $skipped++; continue }

    # Пробуем форматы по очереди. 404 на текст больше не означает «не
    # публиковался»: по указателю мы ходим только по выпущенным номерам, и
    # отсутствие .txt значит ровно одно — текстовой версии у него нет.
    $взят = ''
    $target = ''
    $оборван = $false
    $code = 0
    foreach ($вид in $кандидаты) {
        $target = Join-Path $texts ("rfc{0}.{1}" -f $number, $вид.ext)
        $ответ = Invoke-Download @('-sL', '--max-time', '60', '--retry', '2', '-o', $target,
                                   '-w', '%{http_code}', ("$Base/rfc/rfc{0}.{1}" -f $number, $вид.ext))
        $code = $ответ.code
        # Обрыв посреди передачи: сервер ответил 200, а файл пришёл огрызком.
        $оборван = ($code -eq 200 -and $ответ.exit -ne 0)
        if ($code -eq 200 -and -not $оборван -and (Test-RfcFile $target $вид.ext)) {
            $взят = $вид.ext
            break
        }
        if (Test-Path $target) { Remove-Item $target -Force -ErrorAction SilentlyContinue }
        if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
    }

    if (-not $взят) {
        $почему = if ($оборван) { 'связь оборвалась посреди файла' }
                  elseif ($code -eq 0) { 'ответа не было (связь или шлюз)' }
                  elseif ($code -eq 404) { 'ни один формат не отдан сервером' }
                  else { "ответ $code" }
        $неудачи += [pscustomobject]@{
            номер = $number
            название = $(if ($meta) { $meta.title } else { '' })
            форматы = $(if ($meta) { ($meta.formats -join ' ') } else { '' })
            причина = $почему
        }
        if ($code -eq 404) {
            $absent++
            if ($absent -le 10) { Warn "RFC $number — $почему (по указателю: $($неудачи[-1].форматы))" }
            if ($absent -eq 11) { Note 'дальше о таких молчу, итог будет в конце' }
        } else {
            $broken++
            if ($broken -le 10) { Warn "RFC $number — $почему" }
            if ($broken -eq 11) { Note 'дальше об ошибках связи молчу, итог будет в конце' }
        }
    } elseif ($взят -ne 'txt') {
        # Документ на месте, просто не текстом. Это не пробел, но и не то же
        # самое: PDF читается приёмом иначе, и знать об этом надо.
        $other++
        if ($other -le 10) { Note "RFC $number — текста нет, взят $($взят.ToUpperInvariant())" }
        if ($other -eq 11) { Note 'дальше о таких молчу, итог будет в конце' }
    } else {
        # Отметку «чем отменён» указатель знает точнее самого файла: дописываем
        # её в шапку, если её там нет. По ней приём пометит документ
        # заменённым, и в поиск он не попадёт. Только для текста: в PDF так не
        # допишешь, там актуальность проставляется отдельной разметкой.
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

$files = @(Get-ChildItem $texts -File)
$текстов = @($files | Where-Object { $_.Extension -eq '.txt' })
$size = ($files | Measure-Object Length -Sum).Sum
Write-Host ''
Ok ("файлов: {0}, объём: {1:N0} МБ" -f $files.Count, ($size / 1MB))
Note ("из них текстом: {0}, другим форматом: {1}" -f $текстов.Count, ($files.Count - $текстов.Count))

# Три разных числа вместо одного успокоительного. Прежняя строка «эти номера
# не публиковались» была неверной: по указателю скрипт ходит ТОЛЬКО по
# выпущенным номерам, и 404 означал не «нет такого RFC», а «мы просили не тот
# формат». Из-за неё пробел выглядел нормой.
if ($other) {
    Note "у $other RFC текстовой версии нет — взяты PDF, HTML или XML"
}
if ($absent) {
    Warn "НЕ СКАЧАНО (сервер не отдал ни одного формата): $absent"
    Note 'это выпущенные RFC — их отсутствие не норма; список ниже'
}
if ($broken) {
    Warn "не скачано из-за ошибок связи: $broken"
    Note 'запустите скрипт ещё раз: скачанное не перекачивается, добьёт остаток'
}
if ($неудачи.Count) {
    $списокПуть = Join-Path $root 'не-скачано.csv'
    $неудачи | Sort-Object номер | Export-Csv -LiteralPath $списокПуть -NoTypeInformation -Encoding UTF8
    Note "поимённо, с причиной и списком форматов: $списокПуть"
}

# Сверка с указателем: сколько выпущенных RFC у нас на руках. Это и есть ответ
# на вопрос «всё ли выкачано», и считать его должен скрипт, а не человек.
$должноБыть = $numbers.Count
$естьНаРуках = $должноБыть - $absent - $broken
Write-Host ''
Ok ("по указателю положено {0}, на руках {1}" -f $должноБыть, $естьНаРуках)
if ($естьНаРуках -lt $должноБыть) {
    Warn ("не хватает: {0}" -f ($должноБыть - $естьНаРуках))
} else {
    Ok 'архив полон: каждый выпущенный RFC из указателя лежит на диске'
}

Write-Host ''
Write-Host 'Дальше:' -ForegroundColor Green
Write-Host "  1) перенесите каталог $($texts.FullName) на офлайн-машину"
Write-Host '     в C:\reportgen\data\library\standards\rfc'
Write-Host '  2) там выполните:'
Write-Host '       cd C:\reportgen\app\scripts\windows' -ForegroundColor Cyan
Write-Host '       .\load-library.ps1 -Jobs 12' -ForegroundColor Cyan
Write-Host '  Названия, годы и отменённые редакции определятся сами.'
