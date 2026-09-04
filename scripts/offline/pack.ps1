<#
.SYNOPSIS
    Собирает офлайн-комплект на машине С ИНТЕРНЕТОМ.
.DESCRIPTION
    Складывает в один каталог всё, что нужно для установки на машине БЕЗ
    интернета: колёса Python, сборку llama.cpp с библиотеками CUDA, модели
    GGUF, установщики внешних программ (LibreOffice, Tesseract с русским
    языком, DjVuLibre, 7-Zip, Git, Visual C++ Redistributable, Python) и сам
    код в виде git-бандла.

    Ничего качать вручную не нужно: у каждой программы в bundle.example.json
    задан список источников, скрипт пробует их по порядку и берёт первый
    рабочий. Для каждого файла считается SHA-256 и пишется в manifest.json —
    на офлайн-машине комплект проверяется до установки, потому что 20 ГБ по
    флешке нередко приезжают с битым файлом.

    Каталог НЕ архивируется: модели GGUF уже сжаты, а архив на 20 ГБ только
    добавит риска. Копируйте каталог целиком на внешний диск.
.PARAMETER Destination
    Куда складывать комплект. По умолчанию .\reportgen-offline рядом со скриптом.
.PARAMETER Config
    JSON со списком того, что качать. По умолчанию bundle.example.json.
.PARAMETER Probe
    Ничего не качать: только проверить, что все адреса живы, и напечатать
    таблицу. Занимает полминуты — прогоняйте перед многочасовой сборкой.
.PARAMETER Skip
    Не качать перечисленное: -Skip llm-fallback,git
.PARAMETER Only
    Качать только перечисленное: -Only tesseract,tessdata
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\pack.ps1 -Probe
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\pack.ps1 -Destination D:\reportgen-offline
#>
param(
    [string]$Destination = '',
    [string]$Config = '',
    [switch]$Probe,
    [string[]]$Skip = @(),
    [string[]]$Only = @(),
    [switch]$SkipModels,
    [switch]$SkipWheels,
    [switch]$SkipLlama,
    [switch]$SkipTools
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
# PowerShell 5.1 по умолчанию ходит по TLS 1.0 — половина сайтов такое уже не принимает.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $Destination) { $Destination = Join-Path (Get-Location).Path 'reportgen-offline' }
if (-not $Config) {
    $Config = Join-Path $PSScriptRoot 'bundle.example.json'
    if (-not (Test-Path $Config)) { $Config = Join-Path $PSScriptRoot 'models.example.json' }
}

# curl умеет докачку — на многогигабайтных файлах это важнее удобства
# Invoke-WebRequest. На Windows это curl.exe (встроен с Windows 10 1803).
$script:CurlExe = 'curl.exe'
if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) { $script:CurlExe = 'curl' }

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Note($text) { Write-Host "      $text" -ForegroundColor DarkGray }

# Замечания копим и повторяем в самом конце. Предупреждение, сказанное в
# середине многочасовой сборки, уезжает вверх за экран и до человека не
# доходит — а везти неполный комплект на изолированную машину нельзя.
$script:Warnings = @()
function Later($text) { $script:Warnings += $text; Warn $text }

function New-Dir([string]$path) {
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    return (Resolve-Path $path).Path
}

# При запуске через "powershell -File" параметры приходят строками, и
# "-Only a,b" остаётся ОДНИМ элементом "a,b", а не массивом из двух. Без этой
# нормализации -Only молча отбрасывал бы всё, а -Skip молча ничего не пропускал.
function Split-List($values) {
    $result = @()
    foreach ($value in @($values)) {
        if ($null -eq $value) { continue }
        foreach ($part in ([string]$value -split '[,;]')) {
            $trimmed = $part.Trim()
            if ($trimmed) { $result += $trimmed }
        }
    }
    return $result
}

$Only = Split-List $Only
$Skip = Split-List $Skip

function Test-Wanted([string]$id) {
    if ($Only.Count -and ($Only -notcontains $id)) { return $false }
    if ($Skip -contains $id) { return $false }
    return $true
}

# Всё по сети — через curl, а не через Invoke-RestMethod: в PowerShell 5.1 тот
# ходит по устаревшему TLS и своей дорогой мимо системных настроек прокси, а
# curl ведёт себя одинаково и там, и в PowerShell 7.
# SourceForge выбирает «лучший выпуск» по системе, с которой пришёл запрос, и
# без пометки Windows отдаёт .tar.gz вместо .exe. Комплект всегда собирается
# для Windows, поэтому представляемся так независимо от машины сборки.
$script:UserAgent = 'reportgen-pack (Windows NT 10.0; Win64; x64)'

function Invoke-Http([string]$url) {
    # curl пишет ход загрузки в поток ошибок, а Windows PowerShell 5.1 при
    # $ErrorActionPreference = 'Stop' считает такую строку ошибкой и обрывает
    # сборку комплекта. Читаем сначала, решаем потом.
    $прежний = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $text = & $script:CurlExe -sSL --fail --max-time 90 -A $script:UserAgent $url 2>&1
        $код = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $прежний
    }
    if ($код -ne 0) { throw "не удалось получить $url" }
    return ($text -join "`n")
}

function Invoke-Json([string]$url) { return (Invoke-Http $url | ConvertFrom-Json) }
function Invoke-Text([string]$url) { return (Invoke-Http $url) }

# --------------------------------------------------------------- источники --
# Каждый источник превращается в объект {url, filename, md5, sha256, bytes}.
# Ошибка одного источника не фатальна: пробуется следующий по списку.

function Resolve-SourceForge($source) {
    # best_release.json отдаёт РАЗНОЕ в зависимости от того, с какой системы
    # спрашивают: с Linux тот же проект вернёт .tar.xz вместо .exe. Поэтому
    # платформа задаётся явно — иначе комплект для Windows, собранный из WSL,
    # молча получит не те файлы.
    $project = $source.project
    try {
        $best = Invoke-Json "https://sourceforge.net/projects/$project/best_release.json?platform=windows"
        $path = $best.release.filename          # вида /7-Zip/26.01/7z2601-x64.exe
        $leaf = ($path -split '/')[-1]
        if (-not $source.pattern -or ($leaf -like $source.pattern)) {
            return [pscustomobject]@{
                url      = "https://downloads.sourceforge.net/project/$project$path"
                filename = $leaf
                md5      = $best.release.md5sum
                sha256   = $null
                bytes    = $best.release.bytes
                note     = "SourceForge/$project"
            }
        }
        Note "в best_release проекта $project лежит '$leaf' — ищу '$($source.pattern)' в списке файлов"
    } catch {
        Note "best_release проекта $project недоступен — ищу в списке файлов"
    }
    return Resolve-SourceForgeListing $source
}

function Resolve-SourceForgeListing($source) {
    # Запасной путь: лента файлов проекта, отсортированная по свежести.
    $project = $source.project
    $url = "https://sourceforge.net/projects/$project/rss?limit=200"
    if ($source.path) { $url += "&path=$($source.path)" }
    $xml = Invoke-Text $url
    $matched = [regex]::Matches($xml, '<link>https://sourceforge\.net/projects/[^<]+?/files/([^<]+?)/download</link>')
    foreach ($item in $matched) {
        $relative = [uri]::UnescapeDataString($item.Groups[1].Value)
        $leaf = ($relative -split '/')[-1]
        if ($source.pattern -and ($leaf -notlike $source.pattern)) { continue }
        return [pscustomobject]@{
            url      = "https://downloads.sourceforge.net/project/$project/$relative"
            filename = $leaf
            md5      = $null
            sha256   = $null
            bytes    = $null
            note     = "SourceForge/$project (список файлов)"
        }
    }
    throw "в проекте $project нет файла по шаблону '$($source.pattern)'"
}

function Resolve-GitHubRelease($source) {
    # Не берём «последний выпуск» вслепую: у проектов рядом с обычными
    # сборками попадаются выпуски вообще без прикреплённых файлов. Идём от
    # свежих к старым и берём первый, где нужный файл есть.
    $releases = if ($source.tag) {
        @(Invoke-Json "https://api.github.com/repos/$($source.repo)/releases/tags/$($source.tag)")
    } else {
        @(Invoke-Json "https://api.github.com/repos/$($source.repo)/releases?per_page=20")
    }
    foreach ($info in $releases) {
        $asset = $info.assets | Where-Object { $_.name -like $source.pattern } | Select-Object -First 1
        if (-not $asset) { continue }
        return [pscustomobject]@{
            url      = $asset.browser_download_url
            filename = $asset.name
            md5      = $null
            sha256   = $null
            bytes    = $asset.size
            note     = "GitHub/$($source.repo) $($info.tag_name)"
        }
    }
    $newest = $releases | Where-Object { $_.assets.Count } | Select-Object -First 1
    if ($newest) {
        Note "в выпуске $($newest.tag_name) репозитория $($source.repo) есть:"
        foreach ($name in ($newest.assets.name | Select-Object -First 10)) { Note "  $name" }
    }
    throw "в выпусках репозитория $($source.repo) нет файла '$($source.pattern)'"
}

function Resolve-GitHubRaw($source) {
    $ref = if ($source.ref) { $source.ref } else { 'main' }
    return [pscustomobject]@{
        url      = "https://raw.githubusercontent.com/$($source.repo)/$ref/$($source.path)"
        filename = (Split-Path $source.path -Leaf)
        md5      = $null
        sha256   = $null
        bytes    = $null
        note     = "GitHub/$($source.repo)@$ref"
    }
}

function Resolve-TdfStable($source) {
    # Индекс https://download.documentfoundation.org/libreoffice/stable/ — список
    # каталогов вида 25.2.5/. Берём наибольшую версию.
    $html = Invoke-Text 'https://download.documentfoundation.org/libreoffice/stable/'
    $versions = [regex]::Matches($html, 'href="(\d+\.\d+\.\d+)/"') |
                ForEach-Object { $_.Groups[1].Value } | Sort-Object { [version]$_ } -Unique
    if (-not $versions) { throw 'не удалось разобрать список версий LibreOffice' }
    # Если совпадение одно, Sort-Object возвращает строку, и $versions[-1] дал
    # бы последний СИМВОЛ версии вместо самой версии.
    $version = @($versions)[-1]
    $url = $source.template -replace '\{version\}', $version
    return [pscustomobject]@{
        url      = $url
        filename = (Split-Path $url -Leaf)
        md5      = $null
        sha256   = $null
        bytes    = $null
        note     = "documentfoundation.org $version"
    }
}

# --------------------------------------------------------------- llama.cpp --
# «Последний выпуск» по мнению GitHub — не обязательно тот, в котором лежат
# нужные архивы: у llama.cpp рядом с обычными сборками (b7xxx) появились
# выпуски вида v0.2.0 вообще без бинарников под Windows. Поэтому перебираем
# выпуски от свежих к старым и берём первый, где есть ВСЕ нужные файлы.

function Get-LlamaAssetRules($plan) {
    # Имена архивов llama.cpp со временем менялись: bin-win-cuda-12.4-x64.zip,
    # bin-win-cu12.4-x64.zip. Одного шаблона мало, поэтому у каждого нужного
    # архива список вариантов и список того, что явно НЕ он: без исключения
    # «cudart» подстрока bin-win-cu поймала бы библиотеки CUDA вместо сервера.
    $rules = @()
    foreach ($item in @($plan.llama_cpp.asset_patterns)) {
        if ($item -is [string]) {
            # Старый формат настроек — одна строка.
            $rules += [pscustomobject]@{ id = $item; match = @($item); exclude = @() }
        } else {
            # Отсутствующее поле даёт @($null) — список ИЗ ОДНОГО пустого
            # элемента, а шаблон "**" совпадает с чем угодно: правило без
            # exclude отбрасывало бы все файлы подряд.
            $rules += [pscustomobject]@{
                id      = if ($item.id) { $item.id } else { @($item.match)[0] }
                match   = @($item.match | Where-Object { $_ })
                exclude = @($item.exclude | Where-Object { $_ })
            }
        }
    }
    return $rules
}

function Select-LlamaAssets($release, $rules) {
    $found = @()
    foreach ($rule in $rules) {
        $candidates = $release.assets | Where-Object {
            $name = $_.name
            $hit = $false
            foreach ($pattern in $rule.match) { if ($name -like "*$pattern*") { $hit = $true } }
            foreach ($pattern in $rule.exclude) { if ($name -like "*$pattern*") { $hit = $false } }
            $hit -and $name -like '*.zip'
        }
        $asset = $candidates |
                 Where-Object { $_.name -like '*x64*' -or $_.name -like '*amd64*' } |
                 Select-Object -First 1
        # Бывает, что разрядность в имя не вынесена, — пробуем без неё.
        if (-not $asset) { $asset = $candidates | Select-Object -First 1 }
        if (-not $asset) { return $null }
        $found += $asset
    }
    return $found
}

function Get-LlamaReleases($plan) {
    if ($plan.llama_cpp.release) {
        return @(Invoke-Json "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$($plan.llama_cpp.release)")
    }
    $depth = if ($plan.llama_cpp.search_depth) { [int]$plan.llama_cpp.search_depth } else { 30 }
    return @(Invoke-Json "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=$depth")
}

function Find-LlamaRelease($plan) {
    # Возвращает список подходящих выпусков (свежие первыми) — второй нужен
    # как запасной, если основная сборка не заведётся на этом драйвере.
    $rules = Get-LlamaAssetRules $plan
    $matching = @()
    $scanned = @()
    foreach ($release in (Get-LlamaReleases $plan)) {
        $scanned += $release
        $assets = Select-LlamaAssets $release $rules
        if ($assets) { $matching += [pscustomobject]@{ release = $release; assets = $assets } }
    }
    if (-not $matching.Count) {
        # Молчаливое «нет файла по шаблону» бесполезно: показываем, что в
        # выпусках вообще лежит, чтобы шаблон можно было поправить сразу.
        $withAssets = $scanned | Where-Object { $_.assets.Count } | Select-Object -First 1
        if ($withAssets) {
            Warn ("в выпусках llama.cpp нет архивов: " + (($rules | ForEach-Object { $_.id }) -join ', '))
            Note "самый свежий выпуск с файлами — $($withAssets.tag_name), в нём есть:"
            foreach ($name in ($withAssets.assets.name | Select-Object -First 20)) { Note "  $name" }
        } else {
            Warn 'ни в одном из просмотренных выпусков llama.cpp нет прикреплённых файлов'
        }
        throw ("не найден выпуск llama.cpp с архивами: " + (($rules | ForEach-Object { $_.id }) -join ', '))
    }
    return $matching
}

function Resolve-Url($source) {
    $name = if ($source.filename) { $source.filename } else { Split-Path ($source.url -split '\?')[0] -Leaf }
    return [pscustomobject]@{
        url      = $source.url
        filename = $name
        md5      = $source.md5
        sha256   = $source.sha256
        bytes    = $source.bytes
        note     = if ($source.note) { $source.note } else { 'прямая ссылка' }
    }
}

function Resolve-Source($source) {
    switch ($source.kind) {
        'sourceforge'    { return Resolve-SourceForge $source }
        'github-release' { return Resolve-GitHubRelease $source }
        'github-raw'     { return Resolve-GitHubRaw $source }
        'tdf-stable'     { return Resolve-TdfStable $source }
        'url'            { return Resolve-Url $source }
        default          { throw "неизвестный источник '$($source.kind)'" }
    }
}

function Resolve-FirstWorking($sources, [string]$label) {
    $errors = @()
    foreach ($source in $sources) {
        try {
            $resolved = Resolve-Source $source
            return $resolved
        } catch {
            $errors += "$($source.kind): $($_.Exception.Message)"
        }
    }
    throw ("не удалось определить адрес для '$label'`n        " + ($errors -join "`n        "))
}

function Get-FromFirstWorking($sources, [string]$label, [string]$directory) {
    # Мало определить адрес: зеркало может отдать обрыв или файл с неверной
    # суммой. Тогда нужно переходить к следующему источнику, а не бросать всю
    # программу — иначе запасные адреса бесполезны.
    $errors = @()
    foreach ($source in $sources) {
        $resolved = $null
        try {
            $resolved = Resolve-Source $source
        } catch {
            $errors += "$($source.kind): адрес не определён — $($_.Exception.Message)"
            continue
        }
        $target = Join-Path $directory $resolved.filename
        try {
            Get-File $resolved.url $target
            Test-Checksum $target $resolved
            return $resolved
        } catch {
            $errors += "$($resolved.note): $($_.Exception.Message)"
            # Битую или недокачанную половину файла оставлять нельзя: докачка
            # при следующем запуске продолжит именно её.
            if (Test-Path $target) { Remove-Item $target -Force -ErrorAction SilentlyContinue }
            Warn "$label — источник не подошёл, пробую следующий"
        }
    }
    throw ("не удалось скачать '$label'`n        " + ($errors -join "`n        "))
}

# ------------------------------------------------------------- скачивание ---

function Test-Url([string]$url) {
    # Не HEAD, а запрос первого байта: зеркала SourceForge на HEAD иногда
    # отвечают через раз, а на обычный GET отдают данные. Плюс это проверяет,
    # что зеркало действительно ОТДАЁТ файл, а не только знает о нём.
    $target = if ($script:CurlExe -eq 'curl.exe') { 'NUL' } else { '/dev/null' }
    foreach ($attempt in 1..2) {
        $code = & $script:CurlExe -sL -r 0-0 --max-time 60 --retry 1 --retry-delay 2 `
                    -A $script:UserAgent -o $target -w '%{http_code}' $url 2>$null
        $value = [int]($code | Select-Object -Last 1)
        if ($value -ge 200 -and $value -lt 400) { return $value }
    }
    return $value
}

function Get-File([string]$url, [string]$target) {
    if (Test-Path $target) { Note "уже есть, докачиваю при необходимости: $(Split-Path $target -Leaf)" }
    # --progress-bar вместо таблицы: на десятигигабайтной модели одна строка
    # прогресса читается, а простыня цифр — нет.
    & $script:CurlExe -L --fail --retry 5 --retry-delay 3 --progress-bar `
        -A $script:UserAgent -C - -o $target $url
    if ($LASTEXITCODE -ne 0) { throw "не удалось скачать $url" }
}

function Test-Payload([string]$path, [string]$what) {
    <#
        Скачалось ли то, что заказывали.

        curl с --fail отсекает явные 4xx, но зеркало может ответить кодом 200
        и отдать страницу «файл не найден» или оборвать передачу на середине.
        Такой файл ложится в комплект, попадает в manifest.json как здоровый —
        и обнаруживается на изолированной машине, где заменить его нечем.
        Поэтому смотрим в начало файла: настоящие .exe, .zip и .gguf узнаются
        по первым байтам.
    #>
    if (-not (Test-Path $path)) { throw "файл не появился: $what" }
    $item = Get-Item $path
    if ($item.Length -lt 1024) {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
        throw "$what — вместо файла пришло $($item.Length) байт (похоже на страницу ошибки)"
    }
    $поток = [System.IO.File]::OpenRead($path)
    try {
        $начало = New-Object byte[] 8
        $прочитано = $поток.Read($начало, 0, 8)
    } finally { $поток.Dispose() }
    if ($прочитано -lt 4) { throw "$what — файл нечитаем" }
    $знаки = [System.Text.Encoding]::ASCII.GetString($начало, 0, 4)
    if ($знаки -match '^(<!DO|<htm|<HTM|<\?xm)') {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
        throw "$what — вместо файла пришла веб-страница (зеркало ответило текстом)"
    }
    switch -Regex ($item.Extension) {
        '\.exe$' { if ($начало[0] -ne 0x4D -or $начало[1] -ne 0x5A) {
                       throw "$what — это не программа Windows (нет подписи MZ)" } }
        '\.(zip|msi)$' {
            # MSI — это составной документ OLE, ZIP — обычный архив.
            $zip = ($начало[0] -eq 0x50 -and $начало[1] -eq 0x4B)
            $ole = ($начало[0] -eq 0xD0 -and $начало[1] -eq 0xCF)
            if (-not ($zip -or $ole)) { throw "$what — это не архив и не пакет установки" }
        }
        '\.gguf$' { if ($знаки -ne 'GGUF') {
                       throw "$what — это не модель GGUF (нет подписи GGUF)" } }
    }
}

function Test-Checksum([string]$path, $expected) {
    if ($expected.bytes) {
        $actual = (Get-Item $path).Length
        if ([int64]$actual -ne [int64]$expected.bytes) {
            throw "размер не совпал: $(Split-Path $path -Leaf) — ожидалось $($expected.bytes), получено $actual"
        }
    }
    if ($expected.md5) {
        $actual = (Get-FileHash $path -Algorithm MD5).Hash.ToLower()
        if ($actual -ne ([string]$expected.md5).ToLower()) { throw "MD5 не совпал: $(Split-Path $path -Leaf)" }
    }
    if ($expected.sha256) {
        $actual = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
        if ($actual -ne ([string]$expected.sha256).ToLower()) { throw "SHA-256 не совпал: $(Split-Path $path -Leaf)" }
    }
}

# ------------------------------------------------------------------ старт ---

if (-not (Test-Path $Config)) { throw "не найден файл настроек $Config" }
$plan = Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json

Write-Host ''
Write-Host 'Сборка офлайн-комплекта' -ForegroundColor White
Note "настройки: $Config"
Note "назначение: $Destination"
if ($Probe) { Warn 'режим проверки: ничего не скачивается' }
Write-Host ''

# --------------------------------------------------------- режим проверки ---
if ($Probe) {
    $rows = @()
    $failed = 0

    foreach ($tool in $plan.tools.items) {
        if (-not (Test-Wanted $tool.id)) { continue }
        try {
            $resolved = Resolve-FirstWorking $tool.sources $tool.name
            $code = Test-Url $resolved.url
            if ($code -ge 200 -and $code -lt 400) { $status = "OK $code" } else { $status = "ОШИБКА $code"; $failed++ }
            $rows += [pscustomobject]@{ Что = $tool.id; Файл = $resolved.filename; Источник = $resolved.note; Ответ = $status }
        } catch {
            $failed++
            $rows += [pscustomobject]@{ Что = $tool.id; Файл = '—'; Источник = '—'; Ответ = "НЕ НАЙДЕН: $($_.Exception.Message.Split([char]10)[0])" }
        }
    }

    foreach ($file in $plan.tessdata.files) {
        if (-not (Test-Wanted 'tessdata')) { continue }
        $resolved = Resolve-GitHubRaw $file
        $code = Test-Url $resolved.url
        if ($code -ge 200 -and $code -lt 400) { $status = "OK $code" } else { $status = "ОШИБКА $code"; $failed++ }
        $rows += [pscustomobject]@{ Что = 'tessdata'; Файл = $file.as; Источник = $resolved.note; Ответ = $status }
    }

    foreach ($model in $plan.models) {
        if (-not (Test-Wanted $model.id)) { continue }
        $url = "https://huggingface.co/$($model.repo)/resolve/main/$($model.file)"
        $code = Test-Url $url
        if ($code -ge 200 -and $code -lt 400) { $status = "OK $code" } else { $status = "ОШИБКА $code"; $failed++ }
        $rows += [pscustomobject]@{ Что = $model.id; Файл = $model.file; Источник = "huggingface/$($model.repo)"; Ответ = $status }
    }

    if (Test-Wanted 'llama') {
        try {
            $candidates = @(Find-LlamaRelease $plan)
            $chosen = $candidates[0]
            foreach ($asset in $chosen.assets) {
                $code = Test-Url $asset.browser_download_url
                if ($code -ge 200 -and $code -lt 400) { $status = "OK $code" } else { $status = "ОШИБКА $code"; $failed++ }
                $rows += [pscustomobject]@{ Что = 'llama.cpp'; Файл = $asset.name; Источник = "GitHub $($chosen.release.tag_name)"; Ответ = $status }
            }
            if ($plan.llama_cpp.keep_previous -and $candidates.Count -lt 2) {
                Note 'запасного выпуска llama.cpp с нужными файлами не нашлось — поедет только основной'
            }
        } catch {
            $failed++
            $rows += [pscustomobject]@{ Что = 'llama.cpp'; Файл = '—'; Источник = 'GitHub'; Ответ = "ОШИБКА: $($_.Exception.Message.Split([char]10)[0])" }
        }
    }

    if (-not $rows.Count) {
        Warn 'проверять нечего: -Only не совпал ни с одним идентификатором'
        Note ('доступны: ' + (($plan.tools.items.id + @('tessdata', 'llama', 'wheels') + $plan.models.id) -join ', '))
        exit 1
    }
    $rows | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
    if ($failed) {
        Warn "недоступно источников: $failed — поправьте адреса в $Config или скачайте эти файлы вручную в tools"
        exit 1
    }
    Ok 'все источники доступны, можно запускать сборку без -Probe'
    exit 0
}

$bundle   = New-Dir $Destination
$wheels   = New-Dir (Join-Path $bundle 'wheels')
$llamaDir = New-Dir (Join-Path $bundle 'llama')
$modelDir = New-Dir (Join-Path $bundle 'models')
$toolsDir = New-Dir (Join-Path $bundle 'tools')
$tessDir  = New-Dir (Join-Path $bundle 'tessdata')
$codeDir  = New-Dir (Join-Path $bundle 'code')
$docsDir  = New-Dir (Join-Path $bundle 'docs')

$catalog = @()   # что и откуда взято — попадёт в manifest.json

# --------------------------------------------------------------- код -------
Step 'Код приложения'
Push-Location $Root
try {
    if (Test-Path (Join-Path $Root '.git')) {
        # git-бандл: на офлайн-машине из него клонируется репозиторий с историей,
        # и обновления возятся такими же бандлами.
        & git bundle create (Join-Path $codeDir 'reportgen.bundle') --all
        if ($LASTEXITCODE -eq 0) {
            $branch = (& git rev-parse --abbrev-ref HEAD)
            Set-Content (Join-Path $codeDir 'BRANCH.txt') $branch -Encoding UTF8
            Ok "git-бандл создан (ветка $branch); обновления возятся так же — новым бандлом"
        } else {
            Warn 'git-бандл не создан — на офлайн-машине не будет истории'
        }
    }
    $exclude = @('.git', 'var', 'build', 'dist', '__pycache__', '.venv', 'wheels', 'backups', 'reportgen-offline')
    $target = Join-Path $codeDir 'reportgen-src'
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    New-Dir $target | Out-Null
    Get-ChildItem $Root -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item $_.FullName -Destination $target -Recurse -Force
    }
    Ok 'исходники скопированы'
} finally { Pop-Location }

# Документация отдельно, чтобы её можно было читать не разворачивая комплект.
Copy-Item (Join-Path $Root 'docs\*.md') $docsDir -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root 'README.md') $docsDir -Force -ErrorAction SilentlyContinue
Ok "документация: $((Get-ChildItem $docsDir -File).Count) файлов"

# ------------------------------------------------------------- колёса ------
if (-not $SkipWheels -and (Test-Wanted 'wheels')) {
    Step 'Колёса Python'
    if ($PSVersionTable.PSVersion.Major -ge 6 -and -not $IsWindows) {
        Later 'сборка идёт не на Windows: колёса не подойдут для Windows-машины (см. docs/15-offline.md, 15.2)'
    }
    $requirements = Join-Path $Root 'requirements.txt'
    if (-not (Test-Path $requirements)) { throw "не найден $requirements" }
    & python -m pip download --dest $wheels --requirement $requirements
    if ($LASTEXITCODE -ne 0) { throw 'не удалось скачать зависимости' }
    $formats = Join-Path $Root 'requirements-formats.txt'
    if (Test-Path $formats) {
        & python -m pip download --dest $wheels --requirement $formats
        if ($LASTEXITCODE -ne 0) { Later 'часть пакетов для форматов не скачалась' }
    }
    # Набор тестов на офлайн-машине — единственный способ проверить установку
    # без модели и без сети. Ему нужен httpx (тестовый клиент FastAPI), иначе
    # три модуля падают на импорте, и проверка становится невыполнимой.
    $dev = Join-Path $Root 'requirements-dev.txt'
    if (Test-Path $dev) {
        & python -m pip download --dest $wheels --requirement $dev
        if ($LASTEXITCODE -ne 0) { Later 'пакеты для прогона тестов не скачались' }
    }
    # pip нужен и на офлайн-машине — версия из колеса надёжнее системной.
    & python -m pip download --dest $wheels pip setuptools wheel
    Ok ("колёс: " + (Get-ChildItem $wheels -File).Count)
    $version = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])")
    Set-Content (Join-Path $wheels 'PYTHON-VERSION.txt') $version -Encoding ASCII
    Warn "колёса собраны для Python $version — на офлайн-машине нужна ровно та же версия"

    # Установщик Python из комплекта обязан быть той же ветки: иначе на объекте
    # тупик — колёса не встанут на установленный интерпретатор, а другого
    # установщика взять негде.
    $pythonTool = $plan.tools.items | Where-Object { $_.id -eq 'python' } | Select-Object -First 1
    if ($pythonTool) {
        $url = @($pythonTool.sources)[0].url
        if ($url -match 'python-(\d+\.\d+)\.\d+') {
            $bundled = $Matches[1]
            if ($bundled -ne $version) {
                Warn "в комплект кладётся установщик Python $bundled, а колёса собраны для $version"
                Warn "поправьте ссылку на установщик в $Config или соберите комплект на Python $bundled"
                if (-not $Only.Count) { throw "версии Python не совпадают: колёса $version, установщик $bundled" }
            } else {
                Ok "установщик Python в комплекте той же ветки ($bundled)"
            }
        }
    }
}

# ------------------------------------------------------------ llama.cpp ----
if (-not $SkipLlama -and (Test-Wanted 'llama')) {
    Step 'llama.cpp (сборка под CUDA) и библиотеки CUDA'
    $candidates = @(Find-LlamaRelease $plan)
    $chosen = $candidates[0]
    Ok "выпуск $($chosen.release.tag_name)"
    foreach ($asset in $chosen.assets) {
        Get-File $asset.browser_download_url (Join-Path $llamaDir $asset.name)
        Ok $asset.name
        $catalog += [pscustomobject]@{ id = 'llama'; file = $asset.name; source = "GitHub $($chosen.release.tag_name)" }
    }
    Set-Content (Join-Path $llamaDir 'RELEASE.txt') $chosen.release.tag_name -Encoding ASCII

    # Предыдущий выпуск как страховка: свежая сборка иногда не заводится на
    # конкретном драйвере, а на офлайн-машине скачать другую уже негде.
    if ($plan.llama_cpp.keep_previous -and $candidates.Count -ge 2) {
        try {
            $previous = $candidates[1]
            $prevDir = New-Dir (Join-Path $llamaDir 'previous')
            foreach ($asset in $previous.assets) {
                Get-File $asset.browser_download_url (Join-Path $prevDir $asset.name)
            }
            Set-Content (Join-Path $prevDir 'RELEASE.txt') $previous.release.tag_name -Encoding ASCII
            Ok "запасной выпуск $($previous.release.tag_name) — на случай, если свежий не заведётся"
        } catch { Warn "запасной выпуск llama.cpp не скачан: $($_.Exception.Message)" }
    } elseif ($plan.llama_cpp.keep_previous) {
        Warn 'запасного выпуска llama.cpp с нужными файлами не нашлось'
    }
}

# --------------------------------------------------------------- модели ----
if (-not $SkipModels) {
    Step 'Модели GGUF (это надолго)'
    foreach ($model in $plan.models) {
        if (-not (Test-Wanted $model.id)) { Note "пропуск: $($model.id)"; continue }
        $url = "https://huggingface.co/$($model.repo)/resolve/main/$($model.file)?download=true"
        $target = Join-Path $modelDir $model.file
        Write-Host "  $($model.role): $($model.repo)/$($model.file) (~$($model.approx_gb) ГБ)"
        # Обрыв на девятом гигабайте не должен рвать весь скрипт: без манифеста
        # уже скачанные восемнадцать гигабайт становятся непроверяемыми, и
        # человеку приходится начинать многочасовую сборку сначала. Копим беду
        # и доходим до конца — недостающее назовёт проверка полноты.
        try {
            Get-File $url $target
            Test-Payload $target $model.file
            Test-Checksum $target $model
            Ok $model.file
            $catalog += [pscustomobject]@{ id = $model.id; file = $model.file; source = "huggingface/$($model.repo)" }
        } catch {
            Later ("модель {0} не скачана: {1}" -f $model.file, $_.Exception.Message)
            Note "докачать её отдельно: .\pack.ps1 -Destination <тот же каталог> -Only $($model.id)"
        }
    }
}

# --------------------------------------------- внешние программы Windows ----
if (-not $SkipTools) {
    Step 'Установщики внешних программ'
    $missing = @()
    foreach ($tool in $plan.tools.items) {
        if (-not (Test-Wanted $tool.id)) { Note "пропуск: $($tool.id)"; continue }
        try {
            Write-Host "  $($tool.name) (~$($tool.approx_mb) МБ)"
            $resolved = Get-FromFirstWorking $tool.sources $tool.name $toolsDir
            Test-Payload (Join-Path $toolsDir $resolved.filename) $resolved.filename
            Ok "$($resolved.filename) — $($resolved.note)"
            $catalog += [pscustomobject]@{ id = $tool.id; file = $resolved.filename; source = $resolved.note }
        } catch {
            $missing += $tool
            Warn "$($tool.name): $($_.Exception.Message)"
        }
    }

    if ($missing.Count) {
        Write-Host ''
        Warn 'Эти программы скачать не удалось — положите установщики в каталог tools вручную:'
        foreach ($tool in $missing) {
            Write-Host ("   * {0} (~{1} МБ) — {2}" -f $tool.name, $tool.approx_mb, $tool.why)
        }
        Warn 'Без них система будет читать только PDF, DOCX, презентации, Excel и текст.'
    }

    # Языковые файлы Tesseract: тихая установка русский не ставит.
    if ($plan.tessdata -and (Test-Wanted 'tessdata')) {
        Step 'Языковые файлы Tesseract'
        foreach ($file in $plan.tessdata.files) {
            try {
                $resolved = Resolve-GitHubRaw $file
                $target = Join-Path $tessDir $file.as
                New-Dir (Split-Path $target -Parent) | Out-Null
                Get-File $resolved.url $target
                Test-Payload $target $file.as
                Ok $file.as
                $catalog += [pscustomobject]@{ id = 'tessdata'; file = "tessdata/$($file.as)"; source = $resolved.note }
            } catch {
                Later ("языковой файл {0} не скачан: {1}" -f $file.as, $_.Exception.Message)
            }
        }
        $meta = @{ target = $plan.tessdata.target; install_from = $plan.tessdata.install_from }
        $meta | ConvertTo-Json | Set-Content (Join-Path $tessDir 'tessdata.json') -Encoding UTF8
    }
}

# ------------------------------------------------ установщик и документы ----
Step 'Скрипты установки'
Copy-Item (Join-Path $PSScriptRoot 'install-offline.ps1') $bundle -Force
Copy-Item (Join-Path $PSScriptRoot 'verify.ps1') $bundle -Force
Copy-Item $Config (Join-Path $bundle 'bundle.json') -Force
Copy-Item (Join-Path $Root 'docs\15-offline.md') (Join-Path $bundle 'ЧИТАТЬ-ПЕРВЫМ.md') -Force -ErrorAction SilentlyContinue
Ok 'install-offline.ps1, verify.ps1, ЧИТАТЬ-ПЕРВЫМ.md'

# -------------------------------------------------------------- манифест ---
Step 'Манифест и контрольные суммы'
$files = Get-ChildItem $bundle -Recurse -File | Where-Object { $_.Name -ne 'manifest.json' }
$entries = foreach ($file in $files) {
    [pscustomobject]@{
        path   = $file.FullName.Substring($bundle.Length).TrimStart('\', '/') -replace '\\', '/'
        bytes  = $file.Length
        sha256 = (Get-FileHash $file.FullName -Algorithm SHA256).Hash.ToLower()
    }
}
# Из чего комплект ОБЯЗАН состоять, чтобы система заработала. Пишем по
# настройкам сборки, а не по тому, что удалось скачать: manifest.json со
# списком одних лишь удавшихся файлов не отличает полный комплект от
# половины — и verify.ps1 на изолированной машине рапортовал «целый».
$expected = [pscustomobject]@{
    models   = @(@($plan.models) | ForEach-Object { $_.file })
    tools    = @(@($plan.tools.items) | ForEach-Object { $_.id })
    tessdata = @(@($plan.tessdata.files) | ForEach-Object { $_.as })
    llama    = @(@($plan.llama_cpp.asset_patterns) | ForEach-Object { $_.id })
}
$manifest = [pscustomobject]@{
    created  = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    machine  = $env:COMPUTERNAME
    python   = (& python -c "import sys; print(sys.version.split()[0])" 2>$null)
    catalog  = $catalog
    expected = $expected
    files    = $entries
    total_gb = [math]::Round((($entries | Measure-Object bytes -Sum).Sum / 1GB), 2)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $bundle 'manifest.json') -Encoding UTF8

# ------------------------------------------------------ полнота комплекта ---
# Раньше скрипт печатал «Комплект готов» и при неполной сборке: одна сорвавшаяся
# закачка колёс давала только предупреждение, оно уезжало вверх за экран, и на
# изолированной машине выяснялось, что ставить нечем. Проверяем сами — здесь,
# пока машина ещё в сети и добрать недостающее можно одной командой.
Step 'Полнота комплекта'
$пробелы = @()

# Колёса: не «сколько файлов», а «встанет ли из них приложение». Спрашиваем
# у самого pip, не устанавливая: он же будет ставить их на машине отдела.
# Проверяем то, что в комплекте ЛЕЖИТ, а не то, что мы только что качали:
# при доборе одной части (-Only llama) колёса могли остаться неполными с
# прошлого раза, и узнать об этом надо здесь, а не на изолированной машине.
if (@(Get-ChildItem $wheels -File -ErrorAction SilentlyContinue).Count) {
    foreach ($набор in @('requirements.txt', 'requirements-formats.txt', 'requirements-dev.txt')) {
        $файл = Join-Path $Root $набор
        if (-not (Test-Path $файл)) { continue }
        $прежний = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $вывод = & python -m pip install --dry-run --ignore-installed --no-index `
                --find-links $wheels --requirement $файл 2>&1
            $код = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $прежний
        }
        if ($код -eq 0) {
            Ok "$набор — из комплекта поставится"
        } else {
            $беда = @($вывод | ForEach-Object { "$_" } |
                     Where-Object { $_ -match 'ERROR|No matching distribution' }) -join '; '
            if ($набор -eq 'requirements-dev.txt') {
                Later "$набор — не поставится: $беда (без этого на машине отдела не прогнать набор проверок)"
            } else {
                $пробелы += "$набор — из комплекта НЕ поставится: $беда"
            }
        }
    }
}

# Остальное — по наличию файлов: без модели и без llama.cpp система не
# заработает вовсе, и узнать об этом надо здесь.
if (-not $SkipModels -and $plan.models -and @($plan.models).Count) {
    $ждём = @($plan.models | Where-Object { Test-Wanted $_.id })
    $есть = @(Get-ChildItem $modelDir -Filter *.gguf -ErrorAction SilentlyContinue)
    if ($ждём.Count -and $есть.Count -lt $ждём.Count) {
        $пробелы += ("моделей в комплекте {0} из {1}" -f $есть.Count, $ждём.Count)
    } elseif ($ждём.Count) {
        Ok ("моделей: {0}" -f $есть.Count)
    }
}
if (-not $SkipLlama -and (Test-Wanted 'llama')) {
    $архивы = @(Get-ChildItem $llamaDir -Filter *.zip -ErrorAction SilentlyContinue)
    if ($архивы.Count -lt 2) {
        $пробелы += ("архивов llama.cpp {0}, нужно 2 (сервер и библиотеки CUDA)" -f $архивы.Count)
    } else { Ok 'llama.cpp: сервер и библиотеки CUDA' }
}
if (-not $SkipTools -and $plan.tools) {
    $ждём = @($plan.tools.items | Where-Object { Test-Wanted $_.id })
    $есть = @(Get-ChildItem $toolsDir -File -ErrorAction SilentlyContinue)
    if ($ждём.Count -and $есть.Count -lt $ждём.Count) {
        $пробелы += ("установщиков программ {0} из {1}" -f $есть.Count, $ждём.Count)
    } elseif ($ждём.Count) { Ok ("установщиков программ: {0}" -f $есть.Count) }
}
if (Test-Wanted 'tessdata') {
    $языки = @(Get-ChildItem $tessDir -Recurse -Filter *.traineddata -ErrorAction SilentlyContinue)
    if (-not ($языки | Where-Object { $_.Name -eq 'rus.traineddata' })) {
        $пробелы += 'нет русского языка для Tesseract (rus.traineddata) — сканы распознаются в бессмыслицу'
    } else { Ok ("языков Tesseract: {0}" -f $языки.Count) }
}

Write-Host ''
if ($пробелы.Count) {
    Write-Host 'КОМПЛЕКТ НЕПОЛНЫЙ:' -ForegroundColor Red
    foreach ($п in $пробелы) { Write-Host "  * $п" -ForegroundColor Red }
    Write-Host ''
    Write-Host 'Доберите недостающее, не перекачивая весь комплект:' -ForegroundColor Cyan
    Write-Host "  .\pack.ps1 -Destination $bundle -Only <что именно>"
    Write-Host 'Везти такой комплект на изолированную машину нельзя: доложить там будет неоткуда.'
    exit 1
}
Ok 'комплект полный'

Write-Host ''
Write-Host "Комплект готов: $bundle" -ForegroundColor Green
Write-Host ("Файлов: {0}, объём: {1} ГБ" -f $entries.Count, $manifest.total_gb)
if ($script:Warnings.Count) {
    Write-Host ''
    Write-Host 'Замечания при сборке:' -ForegroundColor Yellow
    foreach ($item in $script:Warnings) { Write-Host "  * $item" -ForegroundColor Yellow }
}
Write-Host ''
Write-Host 'Проверьте его здесь же: .\verify.ps1'
Write-Host 'Затем скопируйте каталог целиком на внешний диск (NTFS или exFAT, не FAT32).'
Write-Host 'На офлайн-машине: .\verify.ps1, затем .\install-offline.ps1'
