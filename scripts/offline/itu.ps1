<#
.SYNOPSIS
    Скачать рекомендации МСЭ-Т (ITU-T) на машине С ИНТЕРНЕТОМ для переноса
    в библиотеку отдела.
.DESCRIPTION
    Рекомендации МСЭ-Т — это нормативная основа половины того, чем занимается
    отдел: G — тракты и системы передачи, H — кодирование видео, J — кабельные
    сети и телевидение, O — измерительная аппаратура, V — модемы, X и Y —
    сети передачи данных. Инженер, разбирающий чужую линию связи, идёт
    туда же, куда и в RFC.

    МСЭ раздаёт действующие рекомендации бесплатно с 2007 года — платить и
    регистрироваться не нужно. Скрипт этим и пользуется.

    КАК УСТРОЕНО. Ссылки на файлы не выдумываются из номера, а вычитываются
    со страниц самого МСЭ: сначала перечень серии, потом страница каждой
    рекомендации, где и лежит ссылка на PDF. Так разбор переживает смену
    вёрстки: опознаётся форма ссылки, а не шаблон страницы.

    ПО УМОЛЧАНИЮ КАЧАЮТСЯ ТОЛЬКО ДЕЙСТВУЮЩИЕ редакции. Это не экономия места,
    а требование к отчёту: сослаться на отменённую редакцию — прямой путь к
    претензии. С ключом -Superseded скачиваются и заменённые, но складываются
    отдельно, и рядом кладётся готовый скрипт, который на офлайн-машине
    пометит их как заменённые — руками столько не разметить.

    Скачивание с докачкой: прервали — запустите снова, уже полученное не
    перекачивается. Между запросами пауза, чтобы не выглядеть как атака на
    сервер организации, которая раздаёт всё это бесплатно.

    Объём: порядка четырёх тысяч действующих рекомендаций, несколько гигабайт.
    Точную цифру скажет сам скрипт с ключом -ListOnly, ничего не скачивая.

.PARAMETER Destination
    Куда складывать. По умолчанию .\itu рядом со скриптом.
.PARAMETER Series
    Какие серии брать: -Series G,H,O,V. По умолчанию все.
.PARAMETER Superseded
    Качать и заменённые редакции тоже. Они лягут отдельно и будут помечены.
.PARAMETER ListOnly
    Только собрать перечень и сказать, сколько чего, ничего не скачивая.
    С этого стоит начинать: станет видно объём.
.PARAMETER DelayMs
    Пауза между запросами в миллисекундах. По умолчанию 700 — сайт МСЭ
    заметно медленнее, чем rfc-editor, и торопить его незачем.
.PARAMETER BaseUrl
    Откуда качать. По умолчанию https://www.itu.int.
.PARAMETER IndexFrom
    Каталог с заранее сохранёнными страницами перечней (файлы вида G.html).
    Нужен, если корпоративный шлюз не пускает скрипт, но страницу можно
    сохранить браузером.
.PARAMETER Probe
    Ничего не качать, только проверить, отвечает ли источник и опознаются ли
    ссылки. С этого начинайте, если что-то идёт не так.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\itu.ps1 -Probe
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\itu.ps1 -ListOnly
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\itu.ps1 -Destination D:\itu
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\itu.ps1 -Series G,O,V
#>
param(
    [string]$Destination = '',
    [string[]]$Series = @(),
    [switch]$Superseded,
    [switch]$ListOnly,
    [int]$DelayMs = 700,
    [string]$BaseUrl = 'https://www.itu.int',
    [string]$IndexFrom = '',
    [switch]$Probe
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

if (-not $Destination) { $Destination = Join-Path (Get-Location).Path 'itu' }

$script:CurlExe = 'curl.exe'
if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) { $script:CurlExe = 'curl' }

$Base = $BaseUrl.TrimEnd('/')

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Note($text) { Write-Host "      $text" -ForegroundColor DarkGray }

#: Серии рекомендаций МСЭ-Т. Буква серии — это область техники, и по ней же
#: раскладываются файлы: инженеру привычнее искать «G.703», чем номер в общем
#: списке из четырёх тысяч.
function Get-ItuSeries {
    return @(
        'A','B','C','D','E','F','G','H','I','J','K','L','M','N',
        'O','P','Q','R','S','T','U','V','X','Y','Z'
    )
}

#: Похож ли текст ссылки на само имя рекомендации, а не на её название.
#: У МСЭ в ссылке лежит то «G.722», то «Recommendation ITU-T G.722» — ни то,
#: ни другое названием не является, и брать его надо из соседней ячейки.
#: Иначе в библиотеку попадёт документ с названием «Recommendation ITU-T
#: G.722», по которому ничего не найти: в нём нет ни одного слова о деле.
function Test-ItuLooksLikeName {
    param([string]$Text, [string]$Id)
    $bare = $Text -replace '(?i)t-rec-', ' '
    $bare = $bare -replace '(?i)\b(recommendations?|itu-?t|rec)\b', ' '
    $bare = ($bare -replace '[^0-9A-Za-z.]', ' ') -replace '\s+', ' '
    $bare = $bare.Trim().Trim('.')
    if ($bare.Length -lt 4) { return $true }
    return ($bare -ieq $Id)
}


<#
    Перечень серии: вытащить рекомендации из страницы МСЭ.

    Ловим не шаблон страницы, а форму ссылки: «T-REC-<серия>.<номер>». Она у
    МСЭ неизменна много лет, а вёрстку вокруг переделывали не раз. Название
    берём из текста ссылки или из соседней ячейки, состояние — из слов «In
    force» / «Superseded» / «Withdrawn», если они на странице есть.
#>
function Read-ItuIndex {
    param([string]$Html, [string]$SeriesLetter)

    $found = [ordered]@{}
    $pattern = '(?is)<a\b[^>]*href\s*=\s*["'']([^"'']*T-REC-' +
               [regex]::Escape($SeriesLetter) +
               '\.(\d+[0-9A-Za-z.]*)[^"'']*)["''][^>]*>(.*?)</a>'
    $link = [regex]::new($pattern)
    foreach ($m in $link.Matches($Html)) {
        $href = $m.Groups[1].Value
        $number = $m.Groups[2].Value.TrimEnd('.')
        $id = "$SeriesLetter.$number"
        if ($found.Contains($id)) { continue }

        # Текст ссылки бывает и самим номером, и названием. Название ищем в
        # той же строке таблицы — до следующего </tr>.
        $title = (($m.Groups[3].Value -replace '<[^>]+>', ' ') -replace '\s+', ' ').Trim()
        if (Test-ItuLooksLikeName -Text $title -Id $id) {
            $tail = $Html.Substring($m.Index, [Math]::Min(1600, $Html.Length - $m.Index))
            $row = $tail -split '(?i)</tr>' | Select-Object -First 1
            $cells = [regex]::Matches($row, '(?is)<t[dh]\b[^>]*>(.*?)</t[dh]>')
            foreach ($cell in $cells) {
                $text = (($cell.Groups[1].Value -replace '<[^>]+>', ' ') -replace '\s+', ' ').Trim()
                if ($text.Length -ge 8 -and $text -notmatch '(?i)^T-REC-' -and $text -ne $id) {
                    $title = $text
                    break
                }
            }
        }

        # Состояние: смотрим в ту же строку таблицы. Слова МСЭ пишет
        # по-английски и с большой буквы, но регистр не гарантируем.
        $status = 'current'
        $tail = $Html.Substring($m.Index, [Math]::Min(1600, $Html.Length - $m.Index))
        $row = $tail -split '(?i)</tr>' | Select-Object -First 1
        if ($row -match '(?i)\b(superseded|replaced\s+by)\b') { $status = 'superseded' }
        elseif ($row -match '(?i)\b(withdrawn|deleted)\b')     { $status = 'archived' }

        $found[$id] = [pscustomobject]@{
            id      = $id
            series  = $SeriesLetter
            number  = $number
            title   = $title
            status  = $status
            page    = $href
        }
    }
    return @($found.Values)
}

<#
    Страница рекомендации: найти ссылку на PDF.

    У МСЭ файл отдаётся через dologin_pub.asp с внутренним ключом издания —
    выдумать такой ключ из номера нельзя, он несёт дату утверждения. Поэтому
    ссылку берём со страницы. Запасной вариант — прямая ссылка на .pdf, если
    вёрстку когда-нибудь упростят.
#>
function Read-ItuPdfLink {
    param([string]$Html)

    # Рядом с PDF на той же странице лежат Word и ZIP — у МСЭ они отдаются той
    # же dologin_pub.asp, и Word обычно стоит ПЕРВЫМ. Брать первую попавшуюся
    # ссылку нельзя: в библиотеку лёг бы документ Word под именем .pdf.
    # Поэтому среди ссылок выбираем ту, у которой в ключе издания написано PDF.
    $links = [regex]::Matches($Html, '(?is)href\s*=\s*["'']([^"'']*dologin_pub\.asp[^"'']*)["'']')
    foreach ($link in $links) {
        if ($link.Groups[1].Value -match '(?i)PDF') { return $link.Groups[1].Value }
    }
    $direct = [regex]::Match($Html, '(?is)href\s*=\s*["'']([^"'']*\.pdf(?:\?[^"'']*)?)["'']')
    if ($direct.Success) { return $direct.Groups[1].Value }
    # Ссылки на PDF нет — так и говорим. Отдать вместо неё Word значит положить
    # в библиотеку файл, который назван не тем, что он есть.
    return ''
}

#: Ссылку со страницы приводим к полному адресу: МСЭ пишет их и относительными.
function Resolve-ItuUrl {
    param([string]$Href, [string]$BaseAddress)
    if (-not $Href) { return '' }
    $href = [System.Web.HttpUtility]::HtmlDecode($Href)
    if ($href -match '^(?i)https?://') { return $href }
    if ($href.StartsWith('//'))        { return "https:$href" }
    if ($href.StartsWith('/'))         { return "$BaseAddress$href" }
    return "$BaseAddress/$($href.TrimStart('./'))"
}

#: Имя файла: «T-REC-G.703.pdf». Двоеточий и косых в номерах МСЭ не бывает,
#: но проверить дешевле, чем потом искать, почему файл не создался.
function Get-ItuFileName {
    param([string]$Id)
    $safe = ($Id -replace '[\\/:*?"<>|]', '-')
    return "T-REC-$safe.pdf"
}

function Get-Text([string]$url) {
    return (& $script:CurlExe -sSL --fail --max-time 90 --retry 2 `
              -A 'reportgen-library/1.0 (offline technical library)' $url 2>$null) -join "`n"
}

# --- Проверка источника ----------------------------------------------------
if ($Probe) {
    Step 'Проверка источника'
    $tries = @("$Base/rec/T-REC-G/en", "$Base/rec/T-REC-G.703/en", "$Base/ITU-T/recommendations/index.aspx?ser=G")
    foreach ($url in $tries) {
        $target = if ($script:CurlExe -eq 'curl.exe') { 'NUL' } else { '/dev/null' }
        $code = [int]((& $script:CurlExe -sL --max-time 45 -o $target -w '%{http_code}' $url 2>$null) |
                      Select-Object -Last 1)
        $status = if ($code -ge 200 -and $code -lt 400) { "OK $code" } else { "ОШИБКА $code" }
        Write-Host ("  {0,-52} {1}" -f $url.Replace($Base, ''), $status)
    }
    Write-Host ''
    Step 'Опознаются ли ссылки на странице перечня'
    $html = Get-Text "$Base/rec/T-REC-G/en"
    if (-not $html) {
        Warn 'страница перечня не получена — смотрите коды ответа выше'
        Note 'если сайт открывается браузером, а скриптом нет, сохраните страницы'
        Note 'перечней вручную и запустите с ключом -IndexFrom <каталог>'
        exit 1
    }
    $entries = Read-ItuIndex -Html $html -SeriesLetter 'G'
    Ok "в серии G опознано рекомендаций: $($entries.Count)"
    foreach ($item in ($entries | Select-Object -First 5)) {
        Note ("{0,-12} {1,-11} {2}" -f $item.id, $item.status, $item.title)
    }
    if ($entries.Count -eq 0) {
        Warn 'ссылки не опознались — вёрстка МСЭ изменилась'
        Note 'сохраните страницу в файл и пришлите: разбор поправим по ней'
    }
    exit 0
}

# --- Перечень --------------------------------------------------------------
$wanted = if ($Series.Count) { $Series | ForEach-Object { $_.ToUpperInvariant() } } else { Get-ItuSeries }
$root = New-Item -ItemType Directory -Path $Destination -Force
Add-Type -AssemblyName System.Web -ErrorAction SilentlyContinue

Step "Перечень рекомендаций: серий $($wanted.Count)"
$all = @()
foreach ($letter in $wanted) {
    $html = if ($IndexFrom) {
        $file = Join-Path $IndexFrom "$letter.html"
        if (Test-Path -LiteralPath $file) { Get-Content -LiteralPath $file -Raw -Encoding UTF8 } else { '' }
    } else {
        Get-Text "$Base/rec/T-REC-$letter/en"
    }
    if (-not $html) { Warn "серия $letter — перечень не получен"; continue }
    $entries = Read-ItuIndex -Html $html -SeriesLetter $letter
    if ($entries.Count -eq 0) { Warn "серия $letter — ни одной ссылки не опознано" }
    else { Note ("серия {0}: {1}" -f $letter, $entries.Count) }
    $all += $entries
    if (-not $IndexFrom -and $DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
}

$inForce = @($all | Where-Object { $_.status -eq 'current' })
$other = @($all | Where-Object { $_.status -ne 'current' })
Ok "всего опознано: $($all.Count) — действующих $($inForce.Count), заменённых и отменённых $($other.Count)"

$indexPath = Join-Path $root 'указатель.csv'
$all | Sort-Object series, number |
    Select-Object id, series, number, status, title, page |
    Export-Csv -LiteralPath $indexPath -NoTypeInformation -Encoding UTF8
Ok "перечень записан: $indexPath"

if ($ListOnly) {
    Write-Host ''
    Note 'ничего не скачано: это был только перечень (-ListOnly)'
    Note 'уберите ключ, чтобы начать скачивание'
    exit 0
}

# --- Скачивание ------------------------------------------------------------
$plan = if ($Superseded) { $all } else { $inForce }
if (-not $Superseded -and $other.Count) {
    Note "заменённые и отменённые ($($other.Count)) пропускаются: ссылаться на них в отчёте нельзя"
    Note 'нужны для истории — добавьте ключ -Superseded'
}

Step "Скачивание: $($plan.Count) документов"
Note 'прервали — запустите снова, уже скачанное не перекачивается'

$done = 0; $got = 0; $skipped = 0; $nolink = 0; $broken = 0
$saved = @()
foreach ($item in ($plan | Sort-Object series, number)) {
    $done++
    $folder = if ($item.status -eq 'current') {
        Join-Path (Join-Path (Join-Path $root 'standards') 'itu-t') $item.series
    } else {
        Join-Path (Join-Path (Join-Path $root 'standards') 'itu-t-заменённые') $item.series
    }
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $target = Join-Path $folder (Get-ItuFileName $item.id)

    if ((Test-Path -LiteralPath $target) -and (Get-Item -LiteralPath $target).Length -gt 4096) {
        $skipped++
        $saved += $item
        continue
    }

    $pageUrl = Resolve-ItuUrl -Href $item.page -BaseAddress $Base
    $page = Get-Text $pageUrl
    if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
    $pdfUrl = Resolve-ItuUrl -Href (Read-ItuPdfLink -Html $page) -BaseAddress $Base
    if (-not $pdfUrl) {
        $nolink++
        if ($nolink -le 10) { Warn "$($item.id) — на странице нет ссылки на PDF" }
        if ($nolink -eq 11) { Note 'дальше о таких молчу, итог будет в конце' }
        continue
    }

    $code = [int]((& $script:CurlExe -sL --max-time 180 --retry 2 -o $target `
                     -A 'reportgen-library/1.0 (offline technical library)' `
                     -w '%{http_code}' $pdfUrl 2>$null) | Select-Object -Last 1)
    # Пустой или крошечный файл — это страница с ошибкой, а не рекомендация.
    $size = if (Test-Path -LiteralPath $target) { (Get-Item -LiteralPath $target).Length } else { 0 }
    if ($code -ne 200 -or $size -le 4096) {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }
        $broken++
        if ($broken -le 10) { Warn "$($item.id) — ответ $code, размер $size" }
        if ($broken -eq 11) { Note 'дальше об ошибках связи молчу, итог будет в конце' }
    } else {
        $got++
        $saved += $item
    }

    if ($done % 50 -eq 0) {
        Write-Progress -Activity 'Скачивание рекомендаций МСЭ-Т' `
                       -Status "$done из $($plan.Count)" `
                       -PercentComplete ([int](100 * $done / $plan.Count))
        Note "$done из $($plan.Count): скачано $got, пропущено готовых $skipped"
    }
    if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
}
Write-Progress -Activity 'Скачивание рекомендаций МСЭ-Т' -Completed

$files = @(Get-ChildItem (Join-Path (Join-Path $root 'standards') 'itu-t') -Recurse -Filter '*.pdf' -ErrorAction SilentlyContinue)
$size = ($files | Measure-Object Length -Sum).Sum
Write-Host ''
Ok ("файлов: {0}, объём: {1:N0} МБ" -f $files.Count, ($size / 1MB))
if ($nolink) { Note "без ссылки на PDF: $nolink (у части рекомендаций публикуется только платная версия)" }
if ($broken) {
    Warn "не скачано из-за ошибок связи: $broken"
    Note 'запустите скрипт ещё раз: скачанное не перекачивается, добьёт остаток'
}

# --- Разметка заменённых ---------------------------------------------------
$replaced = @($saved | Where-Object { $_.status -ne 'current' })
if ($replaced.Count) {
    $markPath = Join-Path $root 'пометить-заменённые.ps1'
    # Invoke-Reportgen — не программа, а функция из _common.ps1. Без этой
    # строки сгенерированный скрипт падал на первой же команде: «имя
    # Invoke-Reportgen не распознано».
    $lines = @(
        '# Пометить скачанные заменённые и отменённые рекомендации МСЭ-Т.',
        '# Выполнить на офлайн-машине ПОСЛЕ load-library.ps1: иначе документов',
        '# в базе ещё нет и метить нечего. Пока они не помечены, поиск считает',
        '# их действующими и модель может сослаться на отменённую редакцию.',
        '',
        '$ErrorActionPreference = ''Stop''',
        '$общее = Join-Path $PSScriptRoot ''_common.ps1''',
        'if (-not (Test-Path $общее)) {',
        '    $общее = ''C:\reportgen\app\scripts\windows\_common.ps1''',
        '}',
        'if (-not (Test-Path $общее)) {',
        '    Write-Host "не найден _common.ps1 — положите этот файл в" -ForegroundColor Red',
        '    Write-Host "C:\reportgen\app\scripts\windows и запустите оттуда"',
        '    exit 1',
        '}',
        '. $общее',
        ''
    )
    foreach ($item in ($replaced | Sort-Object series, number)) {
        $docId = "standards/itu-t-заменённые/$($item.series)/" + [IO.Path]::GetFileNameWithoutExtension((Get-ItuFileName $item.id))
        $lines += "Invoke-Reportgen doc-status --doc-id `"$docId`" --status $($item.status)"
    }
    [IO.File]::WriteAllLines($markPath, $lines, (New-Object System.Text.UTF8Encoding($true)))
    Ok "заменённых скачано: $($replaced.Count); разметка — $markPath"
}

Write-Host ''
Write-Host 'Дальше:' -ForegroundColor Green
# Переносить надо ВЕСЬ standards: заменённые редакции лежат отдельной папкой
# itu-t-заменённые, и без неё разметка ниже метила бы то, чего в библиотеке нет.
Write-Host "  1) перенесите каталог $(Join-Path $root 'standards') на офлайн-машину"
Write-Host '     в подкаталог standards каталога библиотеки'
Write-Host '     (где он — покажет: reportgen paths)'
Write-Host '  2) там выполните:'
Write-Host '       cd C:\reportgen\app\scripts\windows' -ForegroundColor Cyan
Write-Host '       .\load-library.ps1 -Jobs 12' -ForegroundColor Cyan
if ($replaced.Count) {
    Write-Host '  3) положите пометить-заменённые.ps1 в scripts\windows и оттуда:'
    Write-Host '       .\пометить-заменённые.ps1' -ForegroundColor Cyan
    Write-Host '     (он сам подключит _common.ps1; без разметки поиск будет'
    Write-Host '      считать отменённые редакции действующими)'
}
Write-Host '  Названия, годы и направления техники определятся сами.'
