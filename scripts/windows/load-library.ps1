<#
.SYNOPSIS
    Загрузка библиотеки: разбор документов и построение векторов, одной командой.
.DESCRIPTION
    Заменяет собой последовательность «подключить _common.ps1, вызвать ingest,
    вызвать embed, посмотреть library». Проверяет, что установка завершена и
    что запущен эмбеддер, разбирает документы, строит векторы и печатает, что
    получилось и на что смотреть.
.PARAMETER Path
    Что загружать. По умолчанию — вся библиотека из настроек
    (C:\reportgen\data\library).
.PARAMETER DocType
    Тип для всех файлов: literature, standards, datasheets, reports,
    regulations, misc. Нужен, когда папка названа по-своему, а не по типу.
    Без него тип определяется по содержимому самого документа.
.PARAMETER Domain
    Направление техники для всех файлов: hf, satellite, microwave, mobile,
    protocols, signal, method, software, hardware, standard, misc.
.PARAMETER Jobs
    Сколько файлов разбирать одновременно. 0 — по числу ядер минус одно.
    Разбор упирается в процессор, поэтому на многоядерной машине это главный
    способ ускорить загрузку большой библиотеки.
.PARAMETER Force
    Переиндексировать всё заново, даже неизменившиеся файлы.
.PARAMETER NoEmbed
    Не строить векторы (быстрее, но смысловой поиск работать не будет).
.EXAMPLE
    .\load-library.ps1
.EXAMPLE
    .\load-library.ps1 -Path "D:\Архив\Стандарты по релейкам" -DocType standards -Domain microwave
#>
param(
    [string]$Path = '',
    [ValidateSet('literature', 'standards', 'datasheets', 'reports', 'regulations', 'misc')]
    [string]$DocType = '',
    [string]$Domain = '',
    [int]$Jobs = 0,
    [switch]$Force,
    [switch]$NoEmbed
)

. "$PSScriptRoot\_common.ps1"

# --------------------------------------------------- готова ли установка ----
Write-Step 'Проверка установки'
$python = Get-PythonExe
if ($python -eq 'python') {
    Write-Bad "не найдено окружение $script:Venv"
    Write-Warn2 'установка не завершена. Сначала выполните:'
    Write-Warn2 '  cd C:\reportgen-offline ; .\install-offline.ps1 -SkipVerify'
    exit 1
}
if (-not (Test-Path $script:Config)) {
    Write-Bad "не найден файл настроек $script:Config"
    Write-Warn2 'установка не завершена — выполните install-offline.ps1 ещё раз'
    exit 1
}
Write-Ok "окружение и настройки на месте"

$library = if ($Path) { $Path } else { Get-Setting 'library_dir' (Join-Path $script:Data 'library') }
if (-not (Test-Path $library)) {
    Write-Bad "не найден каталог библиотеки: $library"
    exit 1
}
$count = @(Get-ChildItem $library -Recurse -File -ErrorAction SilentlyContinue).Count
Write-Ok "библиотека: $library (файлов на диске: $count)"
if ($count -eq 0) {
    Write-Warn2 'в каталоге нет ни одного файла — положите документы в подпапки и запустите снова'
    exit 1
}

# ------------------------------------------------ что умеем читать сейчас ---
Write-Step 'Поддержка форматов на этой машине'
Invoke-Reportgen formats

# ---------------------------------------------------------------- разбор ---
Write-Step 'Разбор документов (сканы идут медленно, это нормально)'
$arguments = @('ingest', $library)
if ($Force)   { $arguments += '--force' }
if ($Jobs)    { $arguments += @('--jobs', $Jobs) }
if ($DocType) { $arguments += @('--doc-type', $DocType) }
if ($Domain)  { $arguments += @('--domain', $Domain) }
Invoke-Reportgen @arguments
# Код 3 — часть файлов не принята, остальные разобраны. Это не повод бросать
# пачку: векторы нужны тем, что прошли. Но и молчать нельзя — список файлов и
# причин уже напечатан выше, а итог повторим в конце.
$script:NotAccepted = ($LASTEXITCODE -eq 3)
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 3) {
    Write-Bad 'разбор документов завершился с ошибкой'
    exit 1
}
if ($script:NotAccepted) {
    Write-Warn2 'часть файлов не принята — причины перечислены выше'
}

# --------------------------------------------------------------- векторы ---
if ($NoEmbed) {
    Write-Warn2 'векторы не строились (-NoEmbed): смысловой поиск работать не будет'
} else {
    Write-Step 'Векторы для смыслового поиска'
    # Эмбеддер — отдельный сервер; без него embed просто не к кому обратиться.
    $embedUrl = Get-Setting 'embed_base_url' 'http://127.0.0.1:8001/v1'
    $health = ($embedUrl -replace '/v1/?$', '') + '/health'
    if (Wait-Http $health 5) {
        Invoke-Reportgen embed
        if ($LASTEXITCODE -ne 0) {
            Write-Warn2 'построение векторов завершилось с ошибкой'
            Write-Warn2 'часть библиотеки останется без векторов — сколько именно,'
            Write-Warn2 'скажет строка «итого» ниже; смысловой поиск по ним не работает'
        }
    } else {
        Write-Warn2 "эмбеддер не отвечает на $health"
        Write-Warn2 'запустите комплекс (.\start-all.ps1) и выполните этот скрипт снова —'
        Write-Warn2 'разбор документов уже сделан, повторно он не выполняется'
    }
}

# ------------------------------------------------------------------ итог ---
Write-Step 'Что получилось'
Invoke-Reportgen library

Write-Host ''
Write-Host 'На что посмотреть в списке выше:' -ForegroundColor Green
Write-Host '  * документы с 0 чанков — текст не извлёкся (обычно скан без OCR)'
Write-Host '  * строка «по направлениям» — если «не указано» больше трети,'
Write-Host '    разметьте папки ключом -Domain или поправьте templates\domains.json'
Write-Host '  * последнее число «векторов» — если 0, смысловой поиск не работает'
Write-Host ''
if ($script:NotAccepted) {
    Write-Warn2 'ЧАСТЬ ФАЙЛОВ В БИБЛИОТЕКУ НЕ ПОПАЛА.'
    Write-Warn2 'Причины напечатаны выше, каждая с именем файла. Обычно это'
    Write-Warn2 'скан без текстового слоя, пустой файл или подменённое расширение.'
    Write-Warn2 'Исправьте и запустите этот скрипт снова — принятое не перечитывается.'
    Write-Host ''
}
Write-Host 'Подробности: docs\18-library.md' -ForegroundColor DarkGray
if ($script:NotAccepted) { exit 3 }
