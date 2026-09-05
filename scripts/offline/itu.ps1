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
.PARAMETER UserAgent
    Чем представляться серверу. По умолчанию скрипт сам подбирает: сначала
    обычным браузерным заголовком, а если сервер его не принял — своим именем.
    Ключ нужен, только если корпоративный шлюз требует чего-то определённого.
.PARAMETER StopAfterFailures
    Сколько страниц подряд может не открыться, прежде чем скрипт остановится.
    По умолчанию 15: если МСЭ закрыт, незачем час долбиться в шесть тысяч
    адресов, чтобы в конце сказать «ничего не скачано».
.PARAMETER Languages
    Языки изданий по порядку предпочтения. По умолчанию E,R,F,S — английское
    издание, если есть; иначе русское, французское, испанское. Так в
    библиотеку попадает больше документов: часть рекомендаций выложена не на
    всех языках. Нужен русский текст в первую очередь — укажите -Languages R,E.
.PARAMETER SelfTest
    Проверить сам скрипт на этой машине, ничего не скачивая и вообще не
    выходя в сеть. Проверяется то, что ломалось: переживает ли скрипт ошибку
    внешней программы на этой версии PowerShell, разбирает ли страницы МСЭ,
    отличает ли PDF от подделки. С этого стоит начинать на новой машине.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\itu.ps1 -SelfTest
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
    [switch]$Probe,
    [string]$UserAgent = '',
    [int]$StopAfterFailures = 15,
    [string[]]$Languages = @('E', 'R', 'F', 'S'),
    [switch]$SelfTest
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
function Fail($text) { Write-Host "  X   $text" -ForegroundColor Red }

Add-Type -AssemblyName System.Web -ErrorAction SilentlyContinue

<#
    Чем представляться серверу МСЭ.

    Первым идёт обычный браузерный заголовок. Это не уловка: действующие
    рекомендации МСЭ раздаёт бесплатно и без регистрации, но защита сайта от
    наплыва запросов отвечает 403 на незнакомое имя программы. Перечень серии
    при этом открывается — он отдаётся из кэша, — а страница самой
    рекомендации уже нет. Ровно это отдел и видел: 25 перечней прочитались,
    6584 рекомендации опознались, а первая же страница получила 403.

    Вторым идёт честное имя нашей качалки: если сервер принимает его, лучше
    называться собой. Что сработало — то и запоминается на весь запуск.
#>
$script:UserAgents = @(
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'reportgen-library/1.0 (offline technical library)'
)
if ($UserAgent) { $script:UserAgents = @($UserAgent) }
$script:AgentIndex = 0

<#
    Банка печений на весь запуск.

    Файл рекомендации МСЭ отдаёт не по прямой ссылке, а через dologin_pub.asp
    — «вход для публики»: тот ставит печенье и перенаправляет на сам PDF. Без
    общей банки каждый запрос начинается с нуля, и вместо PDF приезжает
    html-страница. Она проходила проверку «файл больше 4 КБ» и ложилась в
    библиотеку под именем .pdf.
#>
$script:CookieJar = ''

function Invoke-Curl {
    <#
        Запустить curl и вернуть его вывод, не оборвав выгрузку.

        Windows PowerShell 5.1 при $ErrorActionPreference = 'Stop' считает
        ошибкой каждую строку, которую внешняя программа написала в поток
        ошибок. Ключ «2>$null» от этого НЕ спасает: строка попадает в поток
        ошибок раньше, чем её отбрасывают. Именно поэтому один 403 на первой
        же рекомендации обрывал выгрузку всех шести тысяч — с трассировкой
        PowerShell вместо человеческого объяснения.
    #>
    param([string[]]$Arguments)
    $прежний = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $вывод = & $script:CurlExe @Arguments 2>&1
        $выход = $LASTEXITCODE
    } catch {
        return @{ lines = @("$($_.Exception.Message)"); exit = 1 }
    } finally {
        $ErrorActionPreference = $прежний
    }
    return @{ lines = @($вывод | ForEach-Object { "$_" }); exit = $выход }
}

<#
    Один запрос к МСЭ: вернуть тело, код ответа и код выхода curl.

    Код ответа спрашиваем у самого curl через -w: с ключом --fail он
    молчит о теле и пишет ошибку в поток ошибок, а нам нужно и то, и другое —
    отличить «сервер отказал» от «страница пустая» иначе нельзя.
#>
function Invoke-Request {
    param(
        [string]$Url,
        [string]$Referer = '',
        [string]$OutFile = '',
        [int]$Timeout = 90,
        [int]$Agent = -1
    )
    if ($Agent -lt 0) { $Agent = $script:AgentIndex }
    # Кроме кода ответа спрашиваем у curl ИТОГОВЫЙ адрес. Он нужен не для
    # красоты: относительные ссылки на странице считаются от того адреса, на
    # котором браузер в итоге оказался, а не от того, который был запрошен.
    # МСЭ перенаправляет, и без этого ссылки развернулись бы не туда.
    $аргументы = @(
        '-sL', '--max-time', "$Timeout", '--retry', '2', '--retry-delay', '2',
        '-A', $script:UserAgents[$Agent], '-w', "`n%{http_code}`n%{url_effective}"
    )
    if ($script:CookieJar) { $аргументы += @('-c', $script:CookieJar, '-b', $script:CookieJar) }
    if ($Referer) { $аргументы += @('-e', $Referer) }
    if ($OutFile) { $аргументы += @('-o', $OutFile) }
    $аргументы += $Url

    $ответ = Invoke-Curl -Arguments $аргументы
    $строки = @($ответ.lines)
    # Две последние строки вывода — это код ответа и итоговый адрес, всё до
    # них — тело. При -o тела в выводе нет и остаются только эти две строки.
    $код = 0
    $итоговый = ''
    if ($строки.Count -ge 2) {
        $адресСтрока = "$($строки[-1])".Trim()
        $кодСтрока = "$($строки[-2])".Trim()
        if ($кодСтрока -match '^\d{3}$') {
            $код = [int]$кодСтрока
            $итоговый = $адресСтрока
            $строки = if ($строки.Count -ge 3) { @($строки[0..($строки.Count - 3)]) } else { @() }
        }
    }
    return [pscustomobject]@{
        code = $код
        exit = $ответ.exit
        text = ($строки -join "`n")
        url  = $итоговый
    }
}

<#
    Запрос с уступкой перегруженному серверу.

    Ответы 429 («слишком часто») и 503 («занят») — не отказ, а просьба
    подождать. Считать их за неудачу значит терять документы там, где хватило
    бы паузы: на выгрузке в шесть тысяч штук такие ответы приходят пачками, и
    без уступки в библиотеку не доезжают целые серии. Ждём всё дольше —
    5, 10, 20 секунд, — а не долбим сервер организации, которая раздаёт всё
    это бесплатно.
#>
function Invoke-Polite {
    param(
        [string]$Url,
        [string]$Referer = '',
        [string]$OutFile = '',
        [int]$Timeout = 90,
        [int]$Agent = -1,
        [int]$Waits = 3
    )
    $пауза = 5
    for ($попытка = 0; $попытка -le $Waits; $попытка++) {
        $ответ = Invoke-Request -Url $Url -Referer $Referer -OutFile $OutFile -Timeout $Timeout -Agent $Agent
        if ($ответ.code -ne 429 -and $ответ.code -ne 503) { return $ответ }
        if ($попытка -eq $Waits) { return $ответ }
        if (-not $script:SaidBusy) {
            Note "сервер просит подождать (ответ $($ответ.code)) — уступаю, выгрузка продолжится"
            $script:SaidBusy = $true
        }
        Start-Sleep -Seconds $пауза
        $пауза = $пауза * 2
    }
    return $ответ
}
$script:SaidBusy = $false
$script:SaidFallback = $false
$script:SaidEdition = $false

#: Код ответа человеческими словами. Инженеру на изолированной машине
#: «ошибка 403» ничего не говорит, а «не пускает эту программу» говорит.
function Read-HttpCode([int]$code, [int]$exitCode) {
    if ($code -eq 0) {
        switch ($exitCode) {
            6  { return 'не нашёл сервер по имени (нет DNS или шлюза)' }
            7  { return 'сервер не отвечает (закрыт порт или шлюз)' }
            28 { return 'время ожидания вышло' }
            35 { return 'не удалось договориться о шифровании (TLS)' }
            60 { return 'сертификат сервера не признан' }
            default { return "нет ответа (curl вышел с кодом $exitCode)" }
        }
    }
    switch ($code) {
        401 { return 'сервер требует входа (401)' }
        403 { return 'сервер отказал (403): не пускает эту программу' }
        404 { return 'страницы нет (404): адрес изменился' }
        429 { return 'слишком часто (429): увеличьте -DelayMs' }
        500 { return 'сервер сломался (500)' }
        503 { return 'сервер занят (503)' }
        default { return "ответ $code" }
    }
}

<#
    Страница МСЭ с подбором имени программы.

    На 403 пробуем следующее имя из списка — один раз за запуск. Что
    сработало, тем и ходим дальше: перебирать на каждой из шести тысяч
    страниц значило бы утроить нагрузку на чужой сервер.
#>
function Get-Page {
    param([string]$Url, [string]$Referer = '')
    $ответ = Invoke-Polite -Url $Url -Referer $Referer
    if ($ответ.code -eq 403 -and -not $script:AgentTried) {
        for ($i = 0; $i -lt $script:UserAgents.Count; $i++) {
            if ($i -eq $script:AgentIndex) { continue }
            $другой = Invoke-Request -Url $Url -Referer $Referer -Agent $i
            if ($другой.code -ge 200 -and $другой.code -lt 400) {
                $script:AgentIndex = $i
                $script:AgentTried = $true
                Note 'сервер не принял прежнее имя программы — перешёл на другое'
                return $другой
            }
        }
        $script:AgentTried = $true
    }
    return $ответ
}
$script:AgentTried = $false

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
    param([string]$Html, [string[]]$Languages = @('E'))

    # Рядом с PDF на той же странице лежат Word и ZIP — у МСЭ они отдаются той
    # же dologin_pub.asp, и Word обычно стоит ПЕРВЫМ. Брать первую попавшуюся
    # ссылку нельзя: в библиотеку лёг бы документ Word под именем .pdf.
    # Поэтому среди ссылок выбираем ту, у которой в ключе издания написано PDF.
    $links = @([regex]::Matches($Html, '(?is)href\s*=\s*["'']([^"'']*dologin_pub\.asp[^"'']*)["'']') |
               ForEach-Object { $_.Groups[1].Value })

    # Язык издания записан в ключе последней буквой: «!!PDF-E» — английское,
    # «!!PDF-R» — русское. МСЭ выкладывает не всякую рекомендацию на всех
    # языках, поэтому идём по списку предпочтений: так в библиотеку попадает
    # больше документов, чем при одном-единственном языке. И порядок здесь не
    # прихоть — без него на странице с шестью языками бралась бы первая
    # попавшаяся, то есть какая придётся.
    foreach ($язык in $Languages) {
        $образец = '(?i)PDF-' + [regex]::Escape($язык) + '\b'
        foreach ($href in $links) {
            if ($href -match $образец) { return $href }
        }
    }
    # Языка из списка нет, но PDF на странице есть — берём его: документ на
    # неожиданном языке всё равно лучше, чем пропуск.
    foreach ($href in $links) {
        if ($href -match '(?i)PDF') { return $href }
    }
    $direct = [regex]::Match($Html, '(?is)href\s*=\s*["'']([^"'']*\.pdf(?:\?[^"'']*)?)["'']')
    if ($direct.Success) { return $direct.Groups[1].Value }
    # Ссылки на PDF нет — так и говорим. Отдать вместо неё Word значит положить
    # в библиотеку файл, который назван не тем, что он есть.
    return ''
}

<#
    Ссылки на страницы отдельных ИЗДАНИЙ рекомендации, свежие первыми.

    У МСЭ две ступени, а не одна. Ссылка из перечня серии ведёт на
    «recommendation.asp?parent=T-REC-A.1» — и параметр назван «parent» не
    случайно: это список изданий, а не сам документ. Файла на нём нет, он
    лежит на странице конкретного издания «T-REC-A.1-201911-I». Разбор,
    искавший файл только на первой ступени, честно сообщал «на странице нет
    ссылки на PDF» — и так по всем шести тысячам рекомендаций.

    Порядок по дате издания, свежие первыми: в отчёт годится действующая
    редакция, а она у МСЭ последняя.
#>
function Read-ItuEditionLinks {
    param([string]$Html, [string]$Id)
    $образец = 'T-REC-' + [regex]::Escape($Id) + '-(\d{6})-[0-9A-Za-z]+'
    $найдено = [ordered]@{}
    foreach ($m in [regex]::Matches($Html, '(?is)href\s*=\s*["'']([^"'']+)["'']')) {
        $href = $m.Groups[1].Value
        # dologin_pub — это уже сам файл, а не страница издания.
        if ($href -match '(?i)dologin_pub') { continue }
        $ключ = [regex]::Match($href, $образец)
        if (-not $ключ.Success) { continue }
        if ($найдено.Contains($ключ.Value)) { continue }
        $найдено[$ключ.Value] = [pscustomobject]@{
            href = $href
            date = $ключ.Groups[1].Value
            id   = $ключ.Value
        }
    }
    return @($найдено.Values | Sort-Object -Property date -Descending)
}

#: Расшифровать «&amp;» и прочие подстановки HTML. Отдельной функцией потому,
#: что System.Web подгружается не везде, а падение при
#: $ErrorActionPreference = 'Stop' оборвало бы весь запуск на первой ссылке.
function ConvertFrom-HtmlText([string]$text) {
    if (-not $text) { return '' }
    try { return [System.Web.HttpUtility]::HtmlDecode($text) } catch { }
    return ($text -replace '&amp;', '&' -replace '&#38;', '&' `
                  -replace '&quot;', '"' -replace '&#39;', "'" -replace '&#39;', "'")
}

<#
    Ссылку со страницы — в полный адрес, по правилам браузера.

    ЗДЕСЬ БЫЛА ОШИБКА, ИЗ-ЗА КОТОРОЙ ВЫГРУЗКА НЕ СКАЧАЛА НИ ОДНОГО ДОКУМЕНТА.
    Прежний разбор приклеивал относительную ссылку к корню сайта и вдобавок
    срезал ведущие точки и косые скопом: «TrimStart('./')» съедает и точки, и
    косые, поэтому «../» исчезал целиком. МСЭ на странице перечня пишет
    ссылки именно так — «../recommendation.asp?lang=en&parent=T-REC-A.1», — и
    вместо

        https://www.itu.int/rec/recommendation.asp?lang=en&parent=T-REC-A.1

    получалось

        https://www.itu.int/recommendation.asp?lang=en&parent=T-REC-A.1

    без «/rec/». Сайт МСЭ на SharePoint, а тот на чужой путь отвечает 403, и
    выглядело это как отказ защиты сайта, хотя мы просто просили не тот адрес.

    Теперь считаем так же, как считает браузер: System.Uri разворачивает
    ссылку относительно страницы, НА КОТОРОЙ она найдена, и «../», «./» и
    полные адреса обрабатываются правильно сами.
#>
function Resolve-ItuUrl {
    param([string]$Href, [string]$PageUrl)
    if (-not $Href) { return '' }
    $href = ConvertFrom-HtmlText $Href
    if (-not $PageUrl) { return $href }
    try {
        $основа = New-Object -TypeName System.Uri -ArgumentList @($PageUrl)
        return (New-Object -TypeName System.Uri -ArgumentList @($основа, $href)).AbsoluteUri
    } catch {
        # Ни страница, ни ссылка не разобрались как адрес. Собирать адрес из
        # обломков вручную — ровно то, чем прежний разбор и промахнулся;
        # честнее вернуть ссылку как есть и дать неудаче попасть в отчёт.
        return $href
    }
}

<#
    Собственная основа страницы, если она объявлена тегом <base href>.

    Браузер, встретив такой тег, считает от него, а не от адреса страницы.
    Не учитывать его значит разворачивать ссылки не туда на всех страницах,
    где он есть.
#>
function Read-ItuBaseHref {
    param([string]$Html, [string]$PageUrl)
    $m = [regex]::Match($Html, '(?is)<base\b[^>]*\bhref\s*=\s*["'']([^"'']+)["'']')
    if (-not $m.Success) { return $PageUrl }
    $свой = Resolve-ItuUrl -Href $m.Groups[1].Value -PageUrl $PageUrl
    if ($свой) { return $свой }
    return $PageUrl
}

<#
    Настоящий ли это PDF.

    Проверки «файл больше 4 КБ» не хватает: страница «доступ закрыт» или
    «сервер занят» весит больше и ложилась в библиотеку под именем .pdf.
    Приём такой файл не прочитает, а инженер увидит в перечне документ,
    которого нет. Смотрим подпись формата — первые пять байт.
#>
function Test-PdfFile([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    try {
        $поток = [IO.File]::OpenRead($path)
        try {
            $голова = New-Object byte[] 5
            $прочитано = $поток.Read($голова, 0, 5)
        } finally { $поток.Dispose() }
    } catch { return $false }
    if ($прочитано -lt 5) { return $false }
    return ([Text.Encoding]::ASCII.GetString($голова) -eq '%PDF-')
}

#: Имя файла: «T-REC-G.703.pdf». Двоеточий и косых в номерах МСЭ не бывает,
#: но проверить дешевле, чем потом искать, почему файл не создался.
function Get-ItuFileName {
    param([string]$Id)
    $safe = ($Id -replace '[\\/:*?"<>|]', '-')
    return "T-REC-$safe.pdf"
}

#: Тело страницы или пустая строка. Причину неудачи кладём в $script:LastWhy,
#: чтобы вызывающий мог сказать человеку, что именно случилось.
$script:LastWhy = ''
$script:LastCode = 0
#: Адрес, на котором curl оказался в итоге. От него, а не от запрошенного,
#: считаются относительные ссылки на полученной странице.
$script:LastUrl = ''
function Get-Text([string]$url, [string]$referer = '') {
    $ответ = Get-Page -Url $url -Referer $referer
    $script:LastCode = $ответ.code
    $script:LastUrl = if ($ответ.url) { $ответ.url } else { $url }
    if ($ответ.code -ge 200 -and $ответ.code -lt 400 -and $ответ.text) {
        $script:LastWhy = ''
        return $ответ.text
    }
    $script:LastWhy = Read-HttpCode $ответ.code $ответ.exit
    return ''
}

# --- Самопроверка ----------------------------------------------------------
<#
    Проверить сам скрипт на этой машине, не выходя в сеть.

    Зачем отдельный ключ. Выгрузка МСЭ идёт часами, и узнать, что скрипт
    несовместим с этой версией PowerShell, лучше за десять секунд до начала, а
    не через час. Именно так и вышло однажды: под Windows PowerShell 5.1 любая
    строка, написанная curl в поток ошибок, обрывала весь запуск — а выяснилось
    это после того, как отдел прочитал перечни всех 25 серий.

    Проверяется ровно то, что ломалось, и без единого обращения наружу:
    обращение к заведомо закрытому порту на 127.0.0.1 заставляет curl написать
    в поток ошибок — если скрипт это переживает, переживёт и отказ МСЭ.
    Остальное — разбор страниц по вложенным образцам и проверка того, что
    подделка не будет принята за PDF.
#>
$script:SelfChecks = 0
$script:SelfFailed = 0
function Assert-That([string]$что, [bool]$верно, [string]$пояснение = '') {
    $script:SelfChecks++
    if ($верно) {
        Write-Host ("  OK  {0}" -f $что) -ForegroundColor Green
    } else {
        $script:SelfFailed++
        Write-Host ("  X   {0}" -f $что) -ForegroundColor Red
        if ($пояснение) { Note $пояснение }
    }
}

#: Образцы страниц МСЭ вшиты в скрипт: самопроверка обязана работать и там,
#: где рядом нет ни репозитория, ни сети.
function Get-SelfSampleIndex {
    return @'
<html><body><table>
<tr><th>Recommendation</th><th>Title</th><th>Status</th></tr>
<tr><td><a href="/rec/T-REC-G.703-201604-I/en">G.703</a></td>
    <td>Physical/electrical characteristics of hierarchical digital interfaces</td>
    <td>In force</td></tr>
<tr><td><a href="/rec/T-REC-G.711-198811-I/en">Recommendation ITU-T G.711</a></td>
    <td>Pulse code modulation (PCM) of voice frequencies</td>
    <td>In force</td></tr>
<tr><td><a href="/rec/T-REC-G.721-198811-S/en">G.721</a></td>
    <td>32 kbit/s adaptive differential pulse code modulation</td>
    <td>Superseded</td></tr>
<tr><td><a href="/rec/T-REC-G.722-201209-I/en">G.722</a></td>
    <td>7 kHz audio-coding within 64 kbit/s</td>
    <td>Withdrawn</td></tr>
<tr><td><a href="/rec/T-REC-G.703-201604-I/en">G.703</a></td>
    <td>Duplicate row</td><td>In force</td></tr>
</table>
<p><a href="/rec/T-REC-H.264-202108-I/en">H.264</a> from another series</p>
</body></html>
'@
}

function Get-SelfSamplePage {
    return @'
<html><body>
<a href="/rec/dologin_pub.asp?lang=e&amp;id=T-REC-G.703-201604-I!!SOFT-E&amp;type=items">Word</a>
<a href="/rec/dologin_pub.asp?lang=f&amp;id=T-REC-G.703-201604-I!!PDF-F&amp;type=items">PDF francais</a>
<a href="/rec/dologin_pub.asp?lang=e&amp;id=T-REC-G.703-201604-I!!PDF-E&amp;type=items">PDF English</a>
</body></html>
'@
}

if ($SelfTest) {
    Step 'Самопроверка: сеть не нужна'

    # 1. Есть ли curl вообще. На Windows 10 он в комплекте, но встречаются
    #    сборки, где его вырезали, — тогда всё остальное бессмысленно.
    $версия = Invoke-Curl -Arguments @('--version')
    # Мало найти слово «curl» в выводе: сообщение «имя curl не распознано»
    # тоже его содержит, и проверка приняла бы отсутствие curl за наличие.
    # Смотрим и код выхода, и то, что первая строка — это его заголовок.
    $естьCurl = ($версия.exit -eq 0 -and $версия.lines.Count -gt 0 -and
                 "$($версия.lines[0])" -match '^(?i)curl\s+\d')
    Assert-That 'curl найден и запускается' $естьCurl `
        "команда «$script:CurlExe --version» ничего не ответила: без curl выгрузка невозможна"
    if ($естьCurl) { Note ("      {0}" -f $версия.lines[0]) }

    # 2. Главная проверка. Обращение к закрытому порту на своей же машине
    #    заставляет curl написать в поток ошибок. Под Windows PowerShell 5.1
    #    при $ErrorActionPreference = 'Stop' это раньше обрывало весь скрипт.
    #    Если строка ниже выполнилась — обёртка работает.
    $закрытый = Invoke-Request -Url 'http://127.0.0.1:9/' -Timeout 5
    Assert-That 'ошибка внешней программы не обрывает скрипт' ($закрытый.code -eq 0) `
        'обращение к закрытому порту вернуло код ответа — проверка не состоялась'
    Note ("      что скажет человеку: {0}" -f (Read-HttpCode $закрытый.code $закрытый.exit))

    # 3. Разбор перечня серии.
    $записи = @(Read-ItuIndex -Html (Get-SelfSampleIndex) -SeriesLetter 'G')
    Assert-That 'перечень серии разбирается' ($записи.Count -eq 4) `
        "опознано записей: $($записи.Count), ожидалось 4 (дубль считается один раз, чужая серия не берётся)"
    $g703 = $записи | Where-Object { $_.id -eq 'G.703' } | Select-Object -First 1
    Assert-That 'название берётся из соседней ячейки, а не из ссылки' `
        ($g703 -and $g703.title -match 'hierarchical') `
        "у G.703 названием оказалось: «$($g703.title)»"
    $g711 = $записи | Where-Object { $_.id -eq 'G.711' } | Select-Object -First 1
    Assert-That 'подпись «Recommendation ITU-T G.711» названием не считается' `
        ($g711 -and $g711.title -notmatch '(?i)recommendation itu') `
        "у G.711 названием оказалось: «$($g711.title)»"

    # 4. Состояние редакции. Ошибка здесь дороже всех прочих: отчёт сошлётся
    #    на отменённую норму.
    $g721 = $записи | Where-Object { $_.id -eq 'G.721' } | Select-Object -First 1
    $g722 = $записи | Where-Object { $_.id -eq 'G.722' } | Select-Object -First 1
    Assert-That 'заменённая редакция опознана заменённой' ($g721 -and $g721.status -eq 'superseded') `
        "у G.721 состояние: «$($g721.status)» вместо superseded"
    Assert-That 'отменённая редакция опознана отменённой' ($g722 -and $g722.status -eq 'archived') `
        "у G.722 состояние: «$($g722.status)» вместо archived"

    # 5. Выбор ссылки на файл: PDF, а не Word, и нужного языка.
    $страницаОбразец = Get-SelfSamplePage
    $ссылка = Read-ItuPdfLink -Html $страницаОбразец -Languages @('E', 'R', 'F', 'S')
    Assert-That 'берётся PDF, а не документ Word' ($ссылка -match '(?i)PDF') `
        "выбрана ссылка: $ссылка"
    Assert-That 'язык выбирается по порядку предпочтения' ($ссылка -match '(?i)PDF-E') `
        "при предпочтении E,R,F,S выбрана ссылка: $ссылка"
    $поРусски = Read-ItuPdfLink -Html $страницаОбразец -Languages @('R', 'F', 'E')
    Assert-That 'нет предпочтённого языка — берётся следующий по списку' `
        ($поРусски -match '(?i)PDF-F') `
        "при предпочтении R,F,E выбрана ссылка: $поРусски"

    # 6. Подделка не должна пройти за PDF.
    $проба = Join-Path ([IO.Path]::GetTempPath()) ("itu-selftest-{0}.bin" -f $PID)
    try {
        [IO.File]::WriteAllBytes($проба, [Text.Encoding]::ASCII.GetBytes('%PDF-1.4' + ("`n" * 20)))
        Assert-That 'настоящий PDF принимается' (Test-PdfFile $проба)
        [IO.File]::WriteAllBytes($проба, [Text.Encoding]::ASCII.GetBytes('<html>доступ закрыт</html>'))
        Assert-That 'html-страница за PDF не принимается' (-not (Test-PdfFile $проба))
        [IO.File]::WriteAllBytes($проба, [byte[]]@(37, 80))
        Assert-That 'обрезок в два байта за PDF не принимается' (-not (Test-PdfFile $проба))
    } finally {
        Remove-Item -LiteralPath $проба -Force -ErrorAction SilentlyContinue
    }

    # 7. Путь с кириллицей: каталог заменённых называется по-русски, и если
    #    кодировка на машине сбита, файлы просто не создадутся.
    $русскийПуть = Join-Path ([IO.Path]::GetTempPath()) ("itu-заменённые-{0}" -f $PID)
    try {
        New-Item -ItemType Directory -Path $русскийПуть -Force | Out-Null
        $файлВнутри = Join-Path $русскийПуть (Get-ItuFileName 'G.703')
        [IO.File]::WriteAllText($файлВнутри, 'проба')
        Assert-That 'путь с русскими буквами создаётся и читается' (Test-Path -LiteralPath $файлВнутри) `
            "не удалось создать $файлВнутри"
    } finally {
        Remove-Item -LiteralPath $русскийПуть -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ''
    if ($script:SelfFailed) {
        Fail "самопроверка не пройдена: $script:SelfFailed из $script:SelfChecks"
        Note 'выгрузку запускать нельзя — сначала разберитесь с отмеченными строками'
        exit 1
    }
    Ok "самопроверка пройдена: $script:SelfChecks из $script:SelfChecks"
    Note 'скрипт на этой машине исправен. Дальше: -Probe (отвечает ли МСЭ),'
    Note 'потом -ListOnly (сколько чего), потом сама выгрузка.'
    exit 0
}

# --- Проверка источника ----------------------------------------------------
<#
    Проверка идёт по всем трём ступеням выгрузки, а не только по первой.

    Раньше проверялись адреса перечней — и они открываются даже тогда, когда
    всё остальное закрыто: перечень отдаётся из кэша. Из-за этого проверка
    говорила «источник отвечает», а выгрузка ложилась на первой же
    рекомендации. Теперь проверяются перечень, страница рекомендации и сам
    файл, и каждым именем программы по очереди.
#>
if ($Probe) {
    $script:CookieJar = Join-Path ([IO.Path]::GetTempPath()) 'itu-probe-cookies.txt'
    if (Test-Path -LiteralPath $script:CookieJar) {
        Remove-Item -LiteralPath $script:CookieJar -Force -ErrorAction SilentlyContinue
    }

    Step 'Проверка источника — по ступеням выгрузки'
    $рабочий = -1
    for ($i = 0; $i -lt $script:UserAgents.Count; $i++) {
        $имя = $script:UserAgents[$i]
        $коротко = if ($имя -match '^Mozilla') { 'как браузер' } else { 'своим именем' }
        Write-Host ("  представляемся {0}:" -f $коротко)
        $ступени = @(
            @{ что = 'перечень серии G'; url = "$Base/rec/T-REC-G/en" },
            @{ что = 'страница G.703';   url = "$Base/rec/T-REC-G.703/en" }
        )
        $всеОткрылись = $true
        foreach ($ступень in $ступени) {
            $ответ = Invoke-Request -Url $ступень.url -Timeout 45 -Agent $i
            $хорошо = ($ответ.code -ge 200 -and $ответ.code -lt 400)
            if (-not $хорошо) { $всеОткрылись = $false }
            $итог = if ($хорошо) { "OK $($ответ.code)" } else { Read-HttpCode $ответ.code $ответ.exit }
            Write-Host ("    {0,-20} {1}" -f $ступень.что, $итог)
        }
        if ($всеОткрылись -and $рабочий -lt 0) { $рабочий = $i }
    }

    if ($рабочий -lt 0) {
        Write-Host ''
        Fail 'ни одним именем страница рекомендации не открылась'
        Note 'проверьте в браузере на этой же машине:'
        Note "  $Base/rec/T-REC-G.703/en"
        Note 'открывается в браузере, а здесь нет — значит, мешает шлюз или'
        Note 'защита сайта. Тогда сохраните страницы перечней браузером и'
        Note 'запустите с ключом -IndexFrom <каталог с G.html, H.html и т.д.>'
        exit 1
    }
    $script:AgentIndex = $рабочий
    Write-Host ''
    Ok ("сервер принимает: {0}" -f $(if ($script:UserAgents[$рабочий] -match '^Mozilla') { 'браузерное имя' } else { 'имя качалки' }))

    Step 'Опознаются ли ссылки на странице перечня'
    $html = Get-Text "$Base/rec/T-REC-G/en"
    if (-not $html) {
        Fail "страница перечня не получена: $script:LastWhy"
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
        exit 1
    }

    Step 'Доходит ли дело до самого файла'
    $первая = $entries | Select-Object -First 1
    $основаПеречня = Read-ItuBaseHref -Html $html -PageUrl $script:LastUrl
    $адресСтраницы = Resolve-ItuUrl -Href $первая.page -PageUrl $основаПеречня
    Note "адрес страницы: $адресСтраницы"
    $страница = Get-Text $адресСтраницы -referer "$Base/rec/T-REC-G/en"
    if (-not $страница) {
        Fail "страница $($первая.id) не получена: $script:LastWhy"
        Note 'если в адресе выше не хватает части пути — ссылка в перечне записана'
        Note 'иначе, чем ожидает разбор; пришлите этот адрес, поправим'
        exit 1
    }
    $основаСтраницы = Read-ItuBaseHref -Html $страница -PageUrl $script:LastUrl
    $ссылкаНаФайл = Read-ItuPdfLink -Html $страница -Languages $Languages
    if (-not $ссылкаНаФайл) {
        Note 'на этой странице файла нет — это список изданий, иду на издание'
        foreach ($издание in (@(Read-ItuEditionLinks -Html $страница -Id $первая.id) | Select-Object -First 2)) {
            $адресИздания = Resolve-ItuUrl -Href $издание.href -PageUrl $основаСтраницы
            Note "  издание: $адресИздания"
            $страницаИздания = Get-Text $адресИздания -referer $адресСтраницы
            if (-not $страницаИздания) { continue }
            $найденная = Read-ItuPdfLink -Html $страницаИздания -Languages $Languages
            if ($найденная) {
                $ссылкаНаФайл = $найденная
                $адресСтраницы = $script:LastUrl
                $основаСтраницы = Read-ItuBaseHref -Html $страницаИздания -PageUrl $адресСтраницы
                break
            }
        }
    }
    $адресФайла = Resolve-ItuUrl -Href $ссылкаНаФайл -PageUrl $основаСтраницы
    if (-not $адресФайла) {
        Warn "на странице $($первая.id) не нашлось ссылки на PDF"
        Note 'у части рекомендаций публикуется только платная версия — но если'
        Note 'так отвечают все подряд, значит изменилась вёрстка страницы'
        exit 1
    }
    $проба = Join-Path ([IO.Path]::GetTempPath()) 'itu-probe.pdf'
    $файл = Invoke-Request -Url $адресФайла -Referer $адресСтраницы -OutFile $проба -Timeout 120
    $размер = if (Test-Path -LiteralPath $проба) { (Get-Item -LiteralPath $проба).Length } else { 0 }
    $этоPdf = Test-PdfFile $проба
    Remove-Item -LiteralPath $проба -Force -ErrorAction SilentlyContinue
    if ($файл.code -ne 200 -or -not $этоPdf) {
        Fail ("файл {0} не скачался: {1}, размер {2}, PDF: {3}" -f `
              $первая.id, (Read-HttpCode $файл.code $файл.exit), $размер, $(if ($этоPdf) { 'да' } else { 'нет' }))
        Note 'страницы открываются, а файлы — нет. Так бывает, когда сервер'
        Note 'отдаёт файл только по печенью: проверьте, что curl не запрещено'
        Note 'писать во временный каталог.'
        exit 1
    }
    Ok ("файл {0} скачивается: {1:N0} КБ" -f $первая.id, ($размер / 1KB))
    Write-Host ''
    Ok 'источник исправен — можно запускать выгрузку'
    exit 0
}

# --- Перечень --------------------------------------------------------------
$wanted = if ($Series.Count) { $Series | ForEach-Object { $_.ToUpperInvariant() } } else { Get-ItuSeries }
$root = New-Item -ItemType Directory -Path $Destination -Force
$script:CookieJar = Join-Path $root 'cookies.txt'

Step "Перечень рекомендаций: серий $($wanted.Count)"
$all = @()
foreach ($letter in $wanted) {
    $адресПеречня = "$Base/rec/T-REC-$letter/en"
    $html = if ($IndexFrom) {
        $file = Join-Path $IndexFrom "$letter.html"
        if (Test-Path -LiteralPath $file) { Get-Content -LiteralPath $file -Raw -Encoding UTF8 } else { '' }
    } else {
        Get-Text $адресПеречня
    }
    if (-not $html) {
        $почему = if ($IndexFrom) { "нет файла $letter.html в $IndexFrom" } else { $script:LastWhy }
        Warn "серия $letter — перечень не получен: $почему"
        continue
    }
    # Разворачиваем ссылки СРАЗУ и от той страницы, на которой они найдены:
    # МСЭ пишет их относительными, и позже, в цикле скачивания, страница уже
    # неизвестна. Сохранённая браузером страница считается лежащей по своему
    # обычному адресу — иначе ссылки из неё развернуть не от чего.
    $страницаПеречня = if ($IndexFrom) { $адресПеречня } else { $script:LastUrl }
    $основаПеречня = Read-ItuBaseHref -Html $html -PageUrl $страницаПеречня
    $entries = @(Read-ItuIndex -Html $html -SeriesLetter $letter)
    foreach ($запись in $entries) {
        $запись.page = Resolve-ItuUrl -Href $запись.page -PageUrl $основаПеречня
    }
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

# «Не скачано» разложено по причинам: без этого 403 на весь сайт выглядел
# как «у части рекомендаций публикуется только платная версия», и отдел
# уносил на изолированную машину пустой каталог, считая это нормой.
$done = 0; $got = 0; $skipped = 0; $nolink = 0; $broken = 0; $noPage = 0
$saved = @()
$неудачи = @()
$подряд = 0
$оборвано = $false
foreach ($item in ($plan | Sort-Object series, number)) {
    $done++
    $folder = if ($item.status -eq 'current') {
        Join-Path (Join-Path (Join-Path $root 'standards') 'itu-t') $item.series
    } else {
        Join-Path (Join-Path (Join-Path $root 'standards') 'itu-t-заменённые') $item.series
    }
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $target = Join-Path $folder (Get-ItuFileName $item.id)

    # Пропускаем уже скачанное — но только если это действительно PDF.
    # Прежняя редакция скрипта сохраняла html-страницу «доступ закрыт» под
    # именем .pdf: она тяжелее четырёх килобайт и по одному размеру
    # пропускалась бы вечно. Проверка подписи формата чинит это сама —
    # мусор будет перекачан при следующем запуске.
    if ((Test-Path -LiteralPath $target) -and (Get-Item -LiteralPath $target).Length -gt 4096) {
        if (Test-PdfFile $target) {
            $skipped++
            $saved += $item
            continue
        }
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    }

    $indexUrl = "$Base/rec/T-REC-$($item.series)/en"
    # Адрес развёрнут ещё при разборе перечня — от той страницы, где найден.
    $pageUrl = $item.page
    $page = Get-Text $pageUrl -referer $indexUrl
    if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }

    if (-not $page) {
        # Запасной путь: канонический адрес рекомендации. У МСЭ он неизменен
        # много лет и не зависит от того, как записана ссылка в перечне. Одна
        # лишняя попытка на документ дешевле, чем пропущенный документ.
        $канонический = "$Base/rec/T-REC-$($item.id)/en"
        if ($канонический -ne $pageUrl) {
            $запасная = Get-Text $канонический -referer $indexUrl
            if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
            if ($запасная) {
                if (-not $script:SaidFallback) {
                    Note 'ссылка из перечня не открылась — беру канонический адрес рекомендации'
                    $script:SaidFallback = $true
                }
                $page = $запасная
                $pageUrl = $script:LastUrl
            }
        }
    }

    if (-not $page) {
        # Страница не открылась. Это НЕ «нет ссылки на PDF»: путать их нельзя,
        # иначе закрытый сайт выдаётся за платные документы.
        $noPage++
        $подряд++
        $неудачи += [pscustomobject]@{ id = $item.id; этап = 'страница'; причина = $script:LastWhy; адрес = $pageUrl }
        if ($noPage -le 10) { Warn "$($item.id) — страница не открылась: $script:LastWhy" }
        if ($noPage -eq 11) { Note 'дальше о таких молчу, итог будет в конце' }
        if ($подряд -ge $StopAfterFailures) {
            Write-Host ''
            Fail "подряд не открылось страниц: $подряд — дальше идти незачем"
            $оборвано = $true
            break
        }
        continue
    }
    $подряд = 0

    $основаСтраницы = Read-ItuBaseHref -Html $page -PageUrl $pageUrl
    $ссылкаНаФайл = Read-ItuPdfLink -Html $page -Languages $Languages

    # Вторая ступень. Ссылка из перечня ведёт на СПИСОК ИЗДАНИЙ
    # («recommendation.asp?parent=...»), а файл лежит на странице конкретного
    # издания. Берём свежие первыми: в отчёт годится действующая редакция.
    # Двух хватает: если и в предыдущей редакции файла нет, дело не в ней.
    if (-not $ссылкаНаФайл) {
        $издания = @(Read-ItuEditionLinks -Html $page -Id $item.id) | Select-Object -First 2
        foreach ($издание in $издания) {
            $адресИздания = Resolve-ItuUrl -Href $издание.href -PageUrl $основаСтраницы
            if ($адресИздания -eq $pageUrl) { continue }
            $страницаИздания = Get-Text $адресИздания -referer $pageUrl
            if ($DelayMs -gt 0) { Start-Sleep -Milliseconds $DelayMs }
            if (-not $страницаИздания) { continue }
            $найденная = Read-ItuPdfLink -Html $страницаИздания -Languages $Languages
            if ($найденная) {
                $ссылкаНаФайл = $найденная
                $pageUrl = $script:LastUrl
                $основаСтраницы = Read-ItuBaseHref -Html $страницаИздания -PageUrl $pageUrl
                if (-not $script:SaidEdition) {
                    Note 'файл лежит на странице издания — перехожу туда'
                    $script:SaidEdition = $true
                }
                break
            }
        }
    }

    $pdfUrl = Resolve-ItuUrl -Href $ссылкаНаФайл -PageUrl $основаСтраницы
    if (-not $pdfUrl) {
        $nolink++
        $неудачи += [pscustomobject]@{ id = $item.id; этап = 'ссылка'; причина = 'на странице нет ссылки на PDF'; адрес = $pageUrl }
        if ($nolink -le 10) { Warn "$($item.id) — на странице нет ссылки на PDF" }
        if ($nolink -eq 11) { Note 'дальше о таких молчу, итог будет в конце' }
        # Первые несколько таких страниц сохраняем целиком. Без них разбор
        # чужой вёрстки превращается в переписку: «пришлите страницу» —
        # «какую?». Теперь она уже лежит рядом с отчётом.
        if ($nolink -le 5) {
            $складПусто = Join-Path $root 'не-найдено-ссылок'
            New-Item -ItemType Directory -Path $складПусто -Force | Out-Null
            $имяСтраницы = Join-Path $складПусто ((Get-ItuFileName $item.id) -replace '\.pdf$', '.html')
            [IO.File]::WriteAllText($имяСтраницы, $page, (New-Object System.Text.UTF8Encoding($false)))
            if ($nolink -eq 1) { Note "страница сохранена для разбора: $складПусто" }
        }
        continue
    }

    # Referer обязателен: МСЭ отдаёт файл через dologin_pub.asp, и без ссылки
    # на страницу, с которой пришли, тот отвечает страницей, а не файлом.
    $ответ = Invoke-Polite -Url $pdfUrl -Referer $pageUrl -OutFile $target -Timeout 180
    $size = if (Test-Path -LiteralPath $target) { (Get-Item -LiteralPath $target).Length } else { 0 }
    $этоPdf = Test-PdfFile $target
    # Обрыв посреди передачи: сервер ответил 200, подпись %PDF- на месте, а
    # хвоста файла нет. Виден такой обрыв только по коду выхода curl (18).
    $оборван = ($ответ.exit -ne 0)
    if ($ответ.code -ne 200 -or $size -le 4096 -or -not $этоPdf -or $оборван) {
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }
        $broken++
        $причина = if ($ответ.code -eq 200 -and -not $этоPdf) {
            "сервер отдал не PDF, а страницу ($size байт)"
        } elseif ($ответ.code -eq 200 -and $оборван) {
            "связь оборвалась посреди файла (получено $size байт)"
        } else {
            Read-HttpCode $ответ.code $ответ.exit
        }
        $неудачи += [pscustomobject]@{ id = $item.id; этап = 'файл'; причина = $причина; адрес = $pdfUrl }
        if ($broken -le 10) { Warn "$($item.id) — $причина" }
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
if ($noPage) { Warn "страница не открылась: $noPage" }
if ($broken) { Warn "файл не скачался: $broken" }

if ($неудачи.Count) {
    $списокПуть = Join-Path $root 'не-скачано.csv'
    $неудачи | Export-Csv -LiteralPath $списокПуть -NoTypeInformation -Encoding UTF8
    Note "что именно не доехало — список: $списокПуть"
}

# Пустой каталог не должен выглядеть успехом: молчаливый ноль уносили на
# изолированную машину и обнаруживали пропажу только там.
if ($got -eq 0 -and $skipped -eq 0) {
    Write-Host ''
    Fail 'не скачано НИ ОДНОГО документа'
    if ($noPage) {
        Note 'страницы рекомендаций не открываются. Проверьте в браузере на'
        Note 'этой же машине один адрес из не-скачано.csv. Открывается там, а'
        Note 'здесь нет — мешает шлюз или защита сайта.'
        Note 'Разбор по ступеням покажет: .\itu.ps1 -Probe'
        Note 'Запасной путь: сохранить перечни браузером и запустить с ключом'
        Note '-IndexFrom <каталог с G.html, H.html и т.д.>'
    }
    exit 1
}

if ($оборвано) {
    Write-Host ''
    Warn 'выгрузка оборвана на середине — в каталоге только часть документов'
    Note 'причина в не-скачано.csv; после устранения запустите скрипт снова:'
    Note 'скачанное не перекачивается, добьёт остаток'
    exit 1
}
if ($broken -or $noPage) {
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
