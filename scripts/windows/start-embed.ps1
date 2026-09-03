<#
.SYNOPSIS
    Запуск сервера эмбеддингов (порт 8001) и реранкера (порт 8002) на GPU.
    Оба занимают около 0.7 ГБ VRAM каждый и сильно улучшают качество поиска
    по технической библиотеке.
.PARAMETER EmbedModel
    Файл .gguf модели эмбеддингов (bge-m3 и подобные).
.PARAMETER RerankModel
    Файл .gguf модели реранкера (bge-reranker-v2-m3 и подобные).
.PARAMETER Only
    embed | rerank | both — что именно запускать.
#>
param(
    [string]$EmbedModel  = '',
    [string]$RerankModel = '',
    [ValidateSet('embed', 'rerank', 'both')][string]$Only = 'both'
)

. "$PSScriptRoot\_common.ps1"

$exe = Get-LlamaServer

function Find-Model($pattern, $exclude) {
    $found = Get-ChildItem $script:Models -Filter *.gguf |
        Where-Object { $_.Name -match $pattern -and $_.Name -notmatch $exclude }
    if (-not $found) { return $null }
    return $found[0].FullName
}

$started = @()

if ($Only -in @('embed', 'both')) {
    if (-not $EmbedModel) { $EmbedModel = Find-Model 'bge-m3|embed|e5' 'rerank' }
    if (-not $EmbedModel) {
        Write-Bad  'модель эмбеддингов не найдена — смысловой поиск работать не будет'
        Write-Warn2 "положите файл bge-m3*.gguf в $script:Models и запустите этот скрипт снова"
    } elseif (-not (Test-Port 8001)) {
        Write-Warn2 'порт 8001 занят — сервер эмбеддингов уже запущен'
        $started += @{ Name = 'эмбеддинги'; Port = 8001 }
    } else {
        $path = if (Test-Path $EmbedModel) { $EmbedModel } else { Join-Path $script:Models $EmbedModel }
        Write-Step "Эмбеддинги: $(Split-Path $path -Leaf) → порт 8001"
        Start-Process -FilePath $exe -WindowStyle Minimized -ArgumentList @(
            '-m', $path, '--host', '127.0.0.1', '--port', '8001',
            '--embeddings', '--pooling', 'cls',
            '-c', '8192', '-ub', '8192', '-ngl', '999',
            '--alias', 'bge-m3',
            '--log-file', (Join-Path $script:Logs 'embed.log')
        )
        $started += @{ Name = 'эмбеддинги'; Port = 8001 }
    }
}

if ($Only -in @('rerank', 'both')) {
    if (-not $RerankModel) { $RerankModel = Find-Model 'rerank' 'нет-такого' }
    if (-not $RerankModel) {
        Write-Warn2 'модель реранкера не найдена — реранк будет отключён'
    } elseif (-not (Test-Port 8002)) {
        Write-Warn2 'порт 8002 занят — реранкер уже запущен'
        $started += @{ Name = 'реранкер'; Port = 8002 }
    } else {
        $path = if (Test-Path $RerankModel) { $RerankModel } else { Join-Path $script:Models $RerankModel }
        Write-Step "Реранкер: $(Split-Path $path -Leaf) → порт 8002"
        Start-Process -FilePath $exe -WindowStyle Minimized -ArgumentList @(
            '-m', $path, '--host', '127.0.0.1', '--port', '8002',
            '--reranking', '--pooling', 'rank',
            '-c', '8192', '-ngl', '999',
            '--alias', 'bge-reranker',
            '--log-file', (Join-Path $script:Logs 'rerank.log')
        )
        $started += @{ Name = 'реранкер'; Port = 8002 }
    }
}

# Запустить — не то же самое, что поднять. Окно свёрнуто, и если модель не
# влезла в видеопамять или файл оказался битым, llama-server закрывается
# молча: скрипт рапортовал «запущены», а в библиотеке потом стояло «сервер
# эмбеддингов недоступен», и связать одно с другим было нечем. Поэтому
# дожидаемся ответа и говорим правду.
$bad = 0
foreach ($item in $started) {
    Write-Step "Проверка: $($item.Name) на порту $($item.Port)"
    if (Wait-Http "http://127.0.0.1:$($item.Port)/health" 120) {
        Write-Ok "$($item.Name) отвечают"
    } else {
        $bad++
        Write-Bad "$($item.Name) не поднялись за две минуты"
        $log = if ($item.Port -eq 8001) { 'embed.log' } else { 'rerank.log' }
        Write-Warn2 "причина — в $(Join-Path $script:Logs $log); чаще всего не хватает видеопамяти: закройте лишние окна llama-server и попробуйте снова"
    }
}

if (-not $started) {
    Write-Bad 'ничего не запущено: нет файлов моделей'
    exit 1
}
if ($bad) { exit 1 }
Write-Ok 'вспомогательные модели работают'
