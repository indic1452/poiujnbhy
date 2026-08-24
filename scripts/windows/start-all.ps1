<#
.SYNOPSIS
    Запуск всего комплекса: модель, эмбеддинги, реранкер, веб-интерфейс.
.PARAMETER Context
    Размер контекста модели. Уменьшите (8192), если не хватает видеопамяти.
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

Write-Step 'Основная модель (отдельное окно)'
$llmArgs = @('-NoExit', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'start-llm.ps1'))
if ($Model)   { $llmArgs += @('-Model', $Model) }
if ($Context) { $llmArgs += @('-Context', $Context) }
if ($CpuMoe)  { $llmArgs += @('-CpuMoe', $CpuMoe) }
Start-Process powershell -ArgumentList $llmArgs

Write-Step 'Ожидание готовности модели (первый запуск — до 2 минут)'
if (Wait-Http 'http://127.0.0.1:8000/health' 300) {
    Write-Ok 'модель отвечает'
} else {
    Write-Bad 'модель не поднялась за 5 минут — посмотрите окно llama-server и logs\llm.log'
    exit 1
}

if (-not $NoEmbed) {
    & (Join-Path $PSScriptRoot 'start-embed.ps1')
    Start-Sleep -Seconds 3
}

Write-Step 'Веб-интерфейс'
& (Join-Path $PSScriptRoot 'start-app.ps1') -OpenBrowser
