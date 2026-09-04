<#
.SYNOPSIS
    Установка приложения: структура каталогов, виртуальное окружение Python,
    зависимости, файл настроек, администратор.
.PARAMETER SkipDeps
    Не переустанавливать зависимости Python (быстрый повторный прогон).
.PARAMETER Wheels
    Каталог с колёсами. Задан — pip ставит только из него и в сеть не ходит.
    Нужен на изолированной машине: без этого ключа pip пойдёт в PyPI.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\01-install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\01-install.ps1 -Wheels D:\reportgen-offline\wheels
#>
param(
    [switch]$SkipDeps,
    [string]$Wheels = ''
)

. "$PSScriptRoot\_common.ps1"

Write-Step "Каталоги в $script:Base"
foreach ($dir in $script:Base, $script:Models, $script:Llama, $script:Data, $script:Logs,
                 (Join-Path $script:Data 'library'), (Join-Path $script:Data 'exports')) {
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}
foreach ($type in 'literature', 'standards', 'datasheets', 'reports', 'regulations') {
    $dir = Join-Path (Join-Path $script:Data 'library') $type
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
}
Write-Ok 'структура каталогов создана'

Write-Step 'Виртуальное окружение Python'
if (-not (Test-Path $script:Venv)) {
    & python -m venv $script:Venv
    Write-Ok "создано: $script:Venv"
} else {
    Write-Ok "уже есть: $script:Venv"
}
$python = Join-Path $script:Venv 'Scripts\python.exe'

if (-not $SkipDeps) {
    Write-Step 'Зависимости Python'
    # На изолированной машине pip молча уходит в PyPI и падает по таймауту, а
    # скрипт раньше всё равно рапортовал «зависимости установлены» и «Готово».
    $offline = @()
    if ($Wheels) {
        if (-not (Test-Path $Wheels)) { Write-Bad "не найден каталог колёс $Wheels"; exit 1 }
        $offline = @('--no-index', '--find-links', $Wheels)
        Write-Ok "ставлю только из $Wheels, сеть не используется"
    }
    & $python -m pip install @offline --upgrade pip --quiet
    $requirements = Join-Path $script:Root 'requirements.txt'
    if (Test-Path $requirements) {
        & $python -m pip install @offline -r $requirements
    } else {
        & $python -m pip install @offline fastapi "uvicorn[standard]" python-docx pymupdf numpy python-multipart
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Bad 'не удалось установить зависимости'
        if (-not $Wheels) {
            Write-Warn2 'на машине без интернета укажите каталог с колёсами: -Wheels D:\reportgen-offline\wheels'
        }
        exit 1
    }
    $formats = Join-Path $script:Root 'requirements-formats.txt'
    if (Test-Path $formats) {
        & $python -m pip install @offline -r $formats
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 'пакеты поддержки форматов не встали — презентации, Excel и RTF читаться не будут'
        }
    }
    Write-Ok 'зависимости установлены'
}

Write-Step 'Файл настроек'
if (-not (Test-Path $script:Config)) {
    Copy-Item (Join-Path $PSScriptRoot 'settings.example.json') $script:Config
    $text = Get-Content $script:Config -Raw -Encoding UTF8
    $appPath = $script:Root -replace '\\', '\\'
    $dataPath = $script:Data -replace '\\', '\\'
    $text = $text -replace 'C:\\\\reportgen\\\\app', $appPath
    $text = $text -replace 'C:\\\\reportgen\\\\data', $dataPath
    # Без BOM: PowerShell 5.1 на "-Encoding UTF8" добавил бы его,
    # и JSON стал бы нечитаемым для части разборщиков.
    [System.IO.File]::WriteAllText($script:Config, $text, (New-Object System.Text.UTF8Encoding($false)))
    Write-Ok "создан $script:Config — откройте и впишите название компании"
} else {
    Write-Ok "уже есть: $script:Config"
}

Write-Step 'Проверка установки'
$env:PYTHONPATH = Join-Path $script:Root 'src'
$env:REPORTGEN_CONFIG = $script:Config
# Проверяем ВСЁ, без чего приложение не поднимется. Раньше здесь смотрели на
# четыре пакета из восьми, писали «пакеты на месте» и «Готово», а сервер потом
# падал с «pip install python-multipart» — на машине, где pip идти некуда.
$нужные = 'fastapi', 'uvicorn', 'docx', 'pymupdf', 'numpy', 'multipart',
          'itsdangerous', 'jinja2'
$проверка = ($нужные | ForEach-Object { "import $_" }) -join '; '
& $python -c "$проверка; print('пакеты на месте')"
if ($LASTEXITCODE -ne 0) {
    Write-Bad 'зависимости встали не полностью — дальше идти нельзя'
    Write-Host 'Какого именно пакета не хватает, видно в строке выше (ModuleNotFoundError).'
    if (-not $Wheels) {
        Write-Warn2 'на машине без интернета укажите каталог с колёсами: -Wheels D:\reportgen-offline\wheels'
    }
    exit 1
}
# Приложение должно не только ввозиться по частям, но и собираться целиком.
& $python -c "import reportgen.web.app as app; app.create_app; print('приложение собирается')"
if ($LASTEXITCODE -ne 0) {
    Write-Bad 'приложение не собирается — установка не закончена'
    exit 1
}

Write-Step 'Администратор'
# Windows PowerShell 5.1 при $ErrorActionPreference = 'Stop' считает ошибкой
# каждую строку, написанную внешней программой в поток ошибок. С «2>&1» это
# обрывало установку ровно здесь: администратор не заводился, «Готово» не
# печаталось, и человек оставался с наполовину установленной системой.
$прежний = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $users = & $python -m reportgen --config $script:Config users 2>&1
} finally {
    $ErrorActionPreference = $прежний
}
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Создайте администратора (пароль не короче 8 символов):' -ForegroundColor Yellow
    $login = Read-Host 'Логин'
    if ($login) {
        Invoke-Reportgen useradd --login $login --role owner
    }
} else {
    Write-Ok 'пользователи уже заведены'
}

Write-Host ''
Write-Host 'Готово. Дальше:' -ForegroundColor Green
Write-Host '  1) скачайте модели в ' -NoNewline; Write-Host $script:Models -ForegroundColor Cyan
Write-Host '  2) распакуйте llama.cpp в ' -NoNewline; Write-Host $script:Llama -ForegroundColor Cyan
Write-Host '  3) запустите .\start-all.ps1'
Write-Host 'Подробности: docs\11-windows.md'
