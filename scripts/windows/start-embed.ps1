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

if ($Only -in @('embed', 'both')) {
    if (-not $EmbedModel) { $EmbedModel = Find-Model 'bge-m3|embed|e5' 'rerank' }
    if (-not $EmbedModel) {
        Write-Warn2 'модель эмбеддингов не найдена — плотный поиск будет отключён'
    } elseif (-not (Test-Port 8001)) {
        Write-Warn2 'порт 8001 занят — сервер эмбеддингов уже запущен'
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
    }
}

if ($Only -in @('rerank', 'both')) {
    if (-not $RerankModel) { $RerankModel = Find-Model 'rerank' 'нет-такого' }
    if (-not $RerankModel) {
        Write-Warn2 'модель реранкера не найдена — реранк будет отключён'
    } elseif (-not (Test-Port 8002)) {
        Write-Warn2 'порт 8002 занят — реранкер уже запущен'
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
    }
}

Write-Ok 'вспомогательные модели запущены в свёрнутых окнах'
