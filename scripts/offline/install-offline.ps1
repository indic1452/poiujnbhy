<#
.SYNOPSIS
    Установка на машине БЕЗ интернета из подготовленного комплекта.
.DESCRIPTION
    Разворачивает код, ставит зависимости из локальных колёс (pip в сеть не
    ходит вообще), раскладывает llama.cpp и модели, создаёт настройки и
    администратора. Ничего не скачивает: если чего-то не хватает, скрипт
    останавливается и говорит, что именно доложить в комплект.
.PARAMETER Target
    Куда ставить. По умолчанию C:\reportgen.
.PARAMETER SkipVerify
    Пропустить проверку контрольных сумм (не рекомендуется).
#>
param(
    [string]$Target = 'C:\reportgen',
    [switch]$SkipVerify
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$bundle = $PSScriptRoot
function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Fail($text) { Write-Host "  X   $text" -ForegroundColor Red; exit 1 }

function New-Dir($path) {
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    return $path
}

if (-not $SkipVerify) {
    Step 'Проверка комплекта'
    & (Join-Path $bundle 'verify.ps1')
    if ($LASTEXITCODE -ne 0) { Fail 'комплект повреждён — установка отменена' }
}

# --------------------------------------------------------------- Python ----
Step 'Python'
$python = $null
foreach ($candidate in @('python', 'py -3.11')) {
    try {
        $version = & cmd /c "$candidate -V 2>&1"
        if ($version -match 'Python 3\.(1[1-9]|[2-9][0-9])') { $python = $candidate; Ok $version; break }
    } catch { }
}
if (-not $python) {
    $installer = Get-ChildItem (Join-Path $bundle 'tools') -Filter 'python-*.exe' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
    if (-not $installer) { Fail 'Python не установлен, и установщика нет в комплекте (tools\python-*.exe)' }
    Warn "Запускаю установщик $($installer.Name). Обязательно отметьте «Add python.exe to PATH»."
    Start-Process $installer.FullName -Wait
    Fail 'После установки Python закройте это окно, откройте новое и запустите скрипт снова'
}

$expected = Join-Path $bundle 'wheels\PYTHON-VERSION.txt'
if (Test-Path $expected) {
    $want = (Get-Content $expected -Raw).Trim()
    $have = (& cmd /c "$python -c ""import sys; print('%d.%d' % sys.version_info[:2])""").Trim()
    if ($want -ne $have) {
        Fail "колёса собраны для Python $want, а установлен $have — колёса не подойдут"
    }
}

# ----------------------------------------------------------------- код -----
Step "Каталоги в $Target"
New-Dir $Target | Out-Null
$app = Join-Path $Target 'app'
if (Test-Path (Join-Path $app 'src')) {
    Warn "код уже развёрнут в $app — обновляю файлы"
} else {
    New-Dir $app | Out-Null
}
Copy-Item (Join-Path $bundle 'code\reportgen-src\*') $app -Recurse -Force
Ok 'код развёрнут'

foreach ($name in 'models', 'llama', 'data', 'logs') { New-Dir (Join-Path $Target $name) | Out-Null }
foreach ($type in 'literature', 'standards', 'datasheets', 'reports', 'regulations') {
    New-Dir (Join-Path $Target "data\library\$type") | Out-Null
}

# ------------------------------------------------------------ llama.cpp ----
Step 'llama.cpp'
$llamaTarget = Join-Path $Target 'llama'
$archives = Get-ChildItem (Join-Path $bundle 'llama') -Filter '*.zip' -ErrorAction SilentlyContinue
if (-not $archives) {
    Warn 'в комплекте нет архивов llama.cpp — распакуйте вручную в ' + $llamaTarget
} else {
    foreach ($archive in $archives) {
        Expand-Archive -Path $archive.FullName -DestinationPath $llamaTarget -Force
        Ok "распакован $($archive.Name)"
    }
    # В некоторых выпусках файлы лежат во вложенном каталоге build\bin.
    $nested = Get-ChildItem $llamaTarget -Recurse -Filter 'llama-server.exe' | Select-Object -First 1
    if ($nested -and $nested.DirectoryName -ne $llamaTarget) {
        Copy-Item (Join-Path $nested.DirectoryName '*') $llamaTarget -Force
        Ok 'файлы подняты из вложенного каталога'
    }
    if (Test-Path (Join-Path $llamaTarget 'llama-server.exe')) {
        Ok 'llama-server.exe на месте'
    } else {
        Fail 'llama-server.exe не найден после распаковки'
    }
}

# --------------------------------------------------------------- модели ----
Step 'Модели'
$models = Get-ChildItem (Join-Path $bundle 'models') -Filter '*.gguf' -ErrorAction SilentlyContinue
if (-not $models) {
    Warn 'в комплекте нет моделей .gguf — положите их в ' + (Join-Path $Target 'models')
} else {
    foreach ($model in $models) {
        Copy-Item $model.FullName (Join-Path $Target 'models') -Force
        Ok ("{0} ({1} ГБ)" -f $model.Name, [math]::Round($model.Length / 1GB, 1))
    }
}

# ---------------------------------------------------------- зависимости ----
Step 'Зависимости Python (только из комплекта, без сети)'
$venv = Join-Path $app '.venv'
if (-not (Test-Path $venv)) { & cmd /c "$python -m venv ""$venv""" }
$venvPython = Join-Path $venv 'Scripts\python.exe'
$wheels = Join-Path $bundle 'wheels'

& $venvPython -m pip install --no-index --find-links $wheels --upgrade pip setuptools wheel 2>&1 | Out-Null
& $venvPython -m pip install --no-index --find-links $wheels -r (Join-Path $app 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'не удалось поставить зависимости из локальных колёс' }
& $venvPython -c "import fastapi, uvicorn, docx, pymupdf, numpy; print('пакеты на месте')"
Ok 'зависимости установлены, сеть не использовалась'

# -------------------------------------------------------------- настройка --
Step 'Настройки и администратор'
$config = Join-Path $Target 'settings.json'
if (-not (Test-Path $config)) {
    $sample = Join-Path $app 'scripts\windows\settings.example.json'
    $text = Get-Content $sample -Raw -Encoding UTF8
    $text = $text -replace 'C:\\\\reportgen\\\\app', ($app -replace '\\', '\\')
    $text = $text -replace 'C:\\\\reportgen\\\\data', ((Join-Path $Target 'data') -replace '\\', '\\')
    Set-Content $config -Value $text -Encoding UTF8
    Ok "создан $config"
} else {
    Ok "уже есть: $config"
}

$env:PYTHONPATH = Join-Path $app 'src'
$env:REPORTGEN_CONFIG = $config
& $venvPython -m reportgen users 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Создайте администратора (пароль не короче 8 символов):' -ForegroundColor Yellow
    $login = Read-Host 'Логин'
    if ($login) { & $venvPython -m reportgen useradd --login $login --role admin }
}

Write-Host ''
Write-Host 'Установка завершена. Дальше:' -ForegroundColor Green
Write-Host "  1) отключите резервную системную память CUDA в панели NVIDIA (docs\11-windows.md, шаг 0)"
Write-Host "  2) cd $app\scripts\windows"
Write-Host '  3) .\00-check.ps1   — проверка машины'
Write-Host '  4) .\start-all.ps1  — запуск комплекса'
Write-Host '  5) сложите документы в ' -NoNewline; Write-Host (Join-Path $Target 'data\library') -ForegroundColor Cyan
Write-Host '     и выполните: . .\_common.ps1 ; Invoke-Reportgen ingest ; Invoke-Reportgen embed'
