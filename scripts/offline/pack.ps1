<#
.SYNOPSIS
    Собирает офлайн-комплект на машине С ИНТЕРНЕТОМ.
.DESCRIPTION
    Складывает в один каталог всё, что нужно для установки на машине БЕЗ
    интернета: колёса Python, сборку llama.cpp с библиотеками CUDA, модели
    GGUF, установщик Python и сам код в виде git-бандла. Для каждого файла
    считается SHA-256 и пишется в manifest.json — на офлайн-машине комплект
    проверяется до установки, потому что 20 ГБ по флешке нередко приезжают
    с битым файлом, и обнаружить это лучше сразу.

    Каталог НЕ архивируется: модели GGUF уже сжаты, а архив на 20 ГБ только
    добавит риска. Копируйте каталог целиком на внешний диск.
.PARAMETER Destination
    Куда складывать комплект. По умолчанию .\reportgen-offline рядом со скриптом.
.PARAMETER Models
    JSON со списком моделей. По умолчанию models.example.json рядом со скриптом.
.PARAMETER SkipModels
    Не качать модели (полезно, когда они уже скачаны отдельно).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\pack.ps1 -Destination D:\offline
#>
param(
    [string]$Destination = '',
    [string]$Models = '',
    [switch]$SkipModels,
    [switch]$SkipWheels,
    [switch]$SkipLlama
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
if (-not $Destination) { $Destination = Join-Path (Get-Location) 'reportgen-offline' }
if (-not $Models) { $Models = Join-Path $PSScriptRoot 'models.example.json' }

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }

function New-Dir($path) {
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    return $path
}

function Get-File($url, $target) {
    # curl.exe встроен в Windows 10+ и умеет докачку: на многогигабайтных
    # файлах это важнее удобства Invoke-WebRequest.
    if (Test-Path $target) {
        Warn "уже есть, докачиваю при необходимости: $(Split-Path $target -Leaf)"
    }
    & curl.exe -L --fail --retry 5 --retry-delay 3 -C - -o $target $url
    if ($LASTEXITCODE -ne 0) { throw "не удалось скачать $url" }
}

$bundle   = New-Dir $Destination
$wheels   = New-Dir (Join-Path $bundle 'wheels')
$llamaDir = New-Dir (Join-Path $bundle 'llama')
$modelDir = New-Dir (Join-Path $bundle 'models')
$toolsDir = New-Dir (Join-Path $bundle 'tools')
$codeDir  = New-Dir (Join-Path $bundle 'code')

$config = Get-Content $Models -Raw -Encoding UTF8 | ConvertFrom-Json

# --------------------------------------------------------------- код -------
Step 'Код приложения'
Push-Location $Root
try {
    if (Test-Path (Join-Path $Root '.git')) {
        # git-бандл: на офлайн-машине из него можно клонировать и потом
        # обновляться новыми бандлами, сохраняя историю.
        & git bundle create (Join-Path $codeDir 'reportgen.bundle') --all
        Ok 'git-бандл создан (обновления возят так же — новым бандлом)'
    }
    $exclude = @('.git', 'var', 'build', '__pycache__', '.venv', 'wheels', 'backups')
    $target = Join-Path $codeDir 'reportgen-src'
    if (Test-Path $target) { Remove-Item $target -Recurse -Force }
    New-Dir $target | Out-Null
    Get-ChildItem $Root -Force | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item $_.FullName -Destination $target -Recurse -Force
    }
    Ok 'исходники скопированы'
} finally { Pop-Location }

# ------------------------------------------------------------- колёса ------
if (-not $SkipWheels) {
    Step 'Колёса Python'
    $requirements = Join-Path $Root 'requirements.txt'
    if (-not (Test-Path $requirements)) { throw "не найден $requirements" }
    & python -m pip download --dest $wheels --requirement $requirements
    if ($LASTEXITCODE -ne 0) { throw 'не удалось скачать зависимости' }
    # Поддержка форматов библиотеки: презентации, Excel, RTF, изображения.
    $formats = Join-Path $Root 'requirements-formats.txt'
    if (Test-Path $formats) {
        & python -m pip download --dest $wheels --requirement $formats
        if ($LASTEXITCODE -ne 0) { Warn 'часть пакетов для форматов не скачалась' }
    }
    # pip нужен и на офлайн-машине — версия из колеса надёжнее системной.
    & python -m pip download --dest $wheels pip setuptools wheel
    Ok ("колёс: " + (Get-ChildItem $wheels -File).Count)
    $version = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])")
    Set-Content (Join-Path $wheels 'PYTHON-VERSION.txt') $version -Encoding UTF8
    Warn "колёса собраны для Python $version — на офлайн-машине нужна та же версия"
}

# ------------------------------------------------------------ llama.cpp ----
if (-not $SkipLlama) {
    Step 'llama.cpp (сборка под CUDA) и библиотеки CUDA'
    $release = $config.llama_cpp.release
    $api = if ($release) {
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/$release"
    } else {
        'https://api.github.com/repos/ggml-org/llama.cpp/releases/latest'
    }
    $info = Invoke-RestMethod -Uri $api -Headers @{ 'User-Agent' = 'reportgen-pack' }
    Ok "выпуск $($info.tag_name)"
    foreach ($pattern in $config.llama_cpp.asset_patterns) {
        $asset = $info.assets | Where-Object { $_.name -like "*$pattern*" -and $_.name -like '*x64*' } |
                 Select-Object -First 1
        if (-not $asset) { Warn "в выпуске нет файла по шаблону '$pattern' — скачайте вручную"; continue }
        Get-File $asset.browser_download_url (Join-Path $llamaDir $asset.name)
        Ok $asset.name
    }
    Set-Content (Join-Path $llamaDir 'RELEASE.txt') $info.tag_name -Encoding UTF8
}

# --------------------------------------------------------------- модели ----
if (-not $SkipModels) {
    Step 'Модели GGUF (это надолго)'
    foreach ($model in $config.models) {
        $url = "https://huggingface.co/$($model.repo)/resolve/main/$($model.file)?download=true"
        $target = Join-Path $modelDir $model.file
        Write-Host "  $($model.role): $($model.repo)/$($model.file) (~$($model.approx_gb) ГБ)"
        Get-File $url $target
        if ($model.sha256) {
            $actual = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
            if ($actual -ne $model.sha256.ToLower()) { throw "SHA-256 не совпал: $($model.file)" }
        }
        Ok $model.file
    }
}

# ---------------------------------------------------------------- Python ---
Step 'Установщик Python'
if ($config.python.url) {
    $name = Split-Path $config.python.url -Leaf
    Get-File $config.python.url (Join-Path $toolsDir $name)
    Ok $name
}

# ------------------------------------------- инструменты разбора форматов ---
Step 'Программы для разбора форматов библиотеки'
$manual = @()
foreach ($tool in $config.tools.items) {
    if ($tool.url) {
        $name = Split-Path $tool.url -Leaf
        Get-File $tool.url (Join-Path $toolsDir $name)
        Ok "$($tool.name): $name"
    } else {
        $manual += $tool
    }
}
if ($manual.Count) {
    Write-Host ''
    Warn 'Эти программы скачайте вручную и положите в каталог tools комплекта:'
    foreach ($tool in $manual) {
        Write-Host ("   * {0} (~{1} МБ) — {2}" -f $tool.name, $tool.approx_mb, $tool.why)
        Write-Host ("     {0}" -f $tool.manual) -ForegroundColor DarkGray
    }
    Write-Host ''
    Warn 'Без них система будет читать только PDF, DOCX, презентации, Excel и текст.'
    Warn 'Проверить, чего не хватает, на любой машине: reportgen formats'
    $answer = Read-Host 'Продолжить сборку без них? (д/н)'
    if ($answer -notmatch '^[дd]') {
        Write-Host 'Сборка прервана. Скачайте установщики, положите в tools и запустите снова.'
        exit 1
    }
}

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
$manifest = [pscustomobject]@{
    created  = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    host     = $env:COMPUTERNAME
    python   = (& python -c "import sys; print(sys.version.split()[0])")
    files    = $entries
    total_gb = [math]::Round((($entries | Measure-Object bytes -Sum).Sum / 1GB), 2)
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $bundle 'manifest.json') -Encoding UTF8

Copy-Item (Join-Path $PSScriptRoot 'install-offline.ps1') $bundle -Force
Copy-Item (Join-Path $PSScriptRoot 'verify.ps1') $bundle -Force
Copy-Item (Join-Path $Root 'docs\15-offline.md') (Join-Path $bundle 'ЧИТАТЬ-ПЕРВЫМ.md') -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host "Комплект готов: $bundle" -ForegroundColor Green
Write-Host ("Файлов: {0}, объём: {1} ГБ" -f $entries.Count, $manifest.total_gb)
Write-Host 'Скопируйте каталог целиком на внешний диск и перенесите на офлайн-машину.'
Write-Host 'Там: .\verify.ps1  (проверка), затем .\install-offline.ps1'
