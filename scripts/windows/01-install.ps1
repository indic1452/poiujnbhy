<#
.SYNOPSIS
    Установка приложения: структура каталогов, виртуальное окружение Python,
    зависимости, файл настроек, администратор.
.PARAMETER SkipDeps
    Не переустанавливать зависимости Python (быстрый повторный прогон).
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\01-install.ps1
#>
param([switch]$SkipDeps)

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
    & $python -m pip install --upgrade pip --quiet
    $requirements = Join-Path $script:Root 'requirements.txt'
    if (Test-Path $requirements) {
        & $python -m pip install -r $requirements
    } else {
        & $python -m pip install fastapi "uvicorn[standard]" python-docx pymupdf numpy python-multipart
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
    Set-Content $script:Config -Value $text -Encoding UTF8
    Write-Ok "создан $script:Config — откройте и впишите название компании"
} else {
    Write-Ok "уже есть: $script:Config"
}

Write-Step 'Проверка установки'
$env:PYTHONPATH = Join-Path $script:Root 'src'
$env:REPORTGEN_CONFIG = $script:Config
& $python -c "import fastapi, uvicorn, docx, pymupdf; print('пакеты на месте')"

Write-Step 'Администратор'
$users = & $python -m reportgen --config $script:Config users 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Создайте администратора (пароль не короче 8 символов):' -ForegroundColor Yellow
    $login = Read-Host 'Логин'
    if ($login) {
        Invoke-Reportgen useradd --login $login --role admin
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
