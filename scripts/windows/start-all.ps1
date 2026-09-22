<#
.SYNOPSIS
    Запуск всего комплекса: модель, эмбеддинги, реранкер, веб-интерфейс.
.PARAMETER Context
    Размер контекста модели, общий на все слоты. По умолчанию 32768 при
    двух слотах, то есть 16384 токена на разговор — столько нужно помощнику,
    чтобы уложить материал из библиотеки и оставить место под ответ.
    Если не хватает видеопамяти, снижайте ступенями: 24576, затем 16384, и
    вместе с этим уменьшайте assistant_context_chars в settings.json
    (26000 → 18000 → 11000). Подробности — в справке start-llm.ps1.
.PARAMETER CpuMoe
    Для MoE-моделей: сколько слоёв экспертов держать в оперативной памяти.
.PARAMETER NoEmbed
    Не запускать эмбеддинги и реранкер (экономит около 1.4 ГБ VRAM).
#>
param(
    [string]$Model  = '',
    [int]$Context   = 0,
    [int]$CpuMoe    = 0,
    [switch]$NoEmbed
)

. "$PSScriptRoot\_common.ps1"

# Проверяем то, без чего запуск заведомо не удастся. Раньше об этом узнавали
# через пять минут ожидания — хотя знать было можно сразу.
try {
    $null = Get-LlamaServer
} catch {
    Write-Bad $_.Exception.Message
    exit 1
}
if (Test-Path $script:Models) {
    $модели = @(Get-ChildItem $script:Models -Filter *.gguf -Recurse -ErrorAction SilentlyContinue)
    if (-not $модели.Count) {
        Write-Bad "в $script:Models нет ни одного файла .gguf — модель не из чего запускать"
        Write-Host 'Положите файл модели в этот каталог (см. docs\11-windows.md).'
        exit 1
    }
} else {
    Write-Bad "нет каталога моделей $script:Models"
    exit 1
}
if (-not (Test-Path $script:Config)) {
    Write-Bad "нет файла настроек $script:Config — сначала запустите .\01-install.ps1"
    exit 1
}

Write-Step 'Основная модель (отдельное окно)'
$llmArgs = @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'start-llm.ps1'))
if ($Model)   { $llmArgs += @('-Model', $Model) }
if ($Context) { $llmArgs += @('-Context', $Context) }
if ($CpuMoe)  { $llmArgs += @('-CpuMoe', $CpuMoe) }
Start-Process powershell -ArgumentList $llmArgs

Write-Step 'Ожидание готовности модели (первый запуск — до 2 минут)'
if (Wait-Http 'http://127.0.0.1:8000/health' 300 'llama-server') {
    Write-Ok 'модель отвечает'
} else {
    Write-Bad 'модель не поднялась за 5 минут — посмотрите окно llama-server и logs\llm.log'
    exit 1
}

if (-not $NoEmbed) {
    & (Join-Path $PSScriptRoot 'start-embed.ps1')
    Start-Sleep -Seconds 3

    # Проверяем САМИ, а не по коду возврата. Скрипт вызван через «&», и его
    # «exit 1» завершает только его; до сюда он не доходит, а $LASTEXITCODE
    # здесь вообще не от него — ни одного внешнего .exe выше не вызывалось.
    #
    # Молчать тут нельзя. Прежде интерфейс открывался в любом случае, и
    # оператор видел работающее окно: поиск при этом шёл ОДНИМ лексическим
    # каналом, вдвое хуже, а единственный след — красная строка, которую уже
    # прокрутило вверх. Библиотека наполовину английская, вопросы русские —
    # без смыслового канала промахи идут потоком.
    Write-Step 'Проверка смыслового поиска'
    $векторы = Wait-Http 'http://127.0.0.1:8001/health' 20
    $реранк  = Wait-Http 'http://127.0.0.1:8002/health' 20
    if ($векторы) { Write-Ok 'эмбеддер отвечает' } else { Write-Bad 'эмбеддер НЕ отвечает (порт 8001)' }
    if ($реранк)  { Write-Ok 'реранкер отвечает' } else { Write-Bad 'реранкер НЕ отвечает (порт 8002)' }
    if (-not ($векторы -and $реранк)) {
        Write-Host ''
        Write-Bad '============================================================'
        Write-Bad ' СМЫСЛОВОЙ ПОИСК РАБОТАЕТ НЕ ПОЛНОСТЬЮ.'
        Write-Bad ' Система отвечать будет, но ответы будут заметно хуже:'
        Write-Bad ' русский вопрос перестаёт доставать английский документ.'
        Write-Bad ' Причина — в logs\embed.log и logs\rerank.log.'
        Write-Bad ' Чаще всего это нехватка видеопамяти: модель, эмбеддер и'
        Write-Bad ' реранкер не помещаются на карту втроём.'
        Write-Bad '============================================================'
        Write-Host ''
        # Вопроса здесь быть не должно: при автозапуске (register-autostart)
        # задача планировщика зависла бы на нём навсегда. Выдерживаем паузу,
        # чтобы строку успели прочитать, и продолжаем.
        Start-Sleep -Seconds 10
    }
}

Write-Step 'Веб-интерфейс'
& (Join-Path $PSScriptRoot 'start-app.ps1') -OpenBrowser
