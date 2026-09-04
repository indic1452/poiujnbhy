<#
.SYNOPSIS
    Проверяет офлайн-комплект по manifest.json.
.DESCRIPTION
    Запускать дважды: на машине с интернетом сразу после сборки (убедиться, что
    комплект целый) и на офлайн-машине после переноса (убедиться, что он доехал).
    Двадцать гигабайт через флешку регулярно приезжают с одним битым файлом,
    и узнать об этом лучше до установки, а не посреди неё.
.PARAMETER Quick
    Сверять только размеры файлов, без чтения содержимого (быстро, менее надёжно).
#>
param([switch]$Quick)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$bundle = $PSScriptRoot
$manifestPath = Join-Path $bundle 'manifest.json'
if (-not (Test-Path $manifestPath)) { throw "не найден manifest.json рядом со скриптом ($bundle)" }

$manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
Write-Host "Комплект от $($manifest.created), файлов: $($manifest.files.Count), объём: $($manifest.total_gb) ГБ"
Write-Host ("Проверка: {0}" -f $(if ($Quick) { 'только размеры' } else { 'SHA-256 (это займёт минуты)' }))

$missing = 0; $damaged = 0; $done = 0
foreach ($entry in $manifest.files) {
    $path = Join-Path $bundle $entry.path
    $done++
    if ($done % 5 -eq 0 -or $entry.bytes -gt 500MB) {
        Write-Progress -Activity 'Проверка комплекта' -Status $entry.path `
                       -PercentComplete ([int](100 * $done / $manifest.files.Count))
    }
    if (-not (Test-Path $path)) {
        Write-Host "  НЕТ ФАЙЛА  $($entry.path)" -ForegroundColor Red
        $missing++
        continue
    }
    $file = Get-Item $path
    if ($file.Length -ne $entry.bytes) {
        Write-Host ("  РАЗМЕР     {0} (ожидалось {1}, получено {2})" -f $entry.path, $entry.bytes, $file.Length) -ForegroundColor Red
        $damaged++
        continue
    }
    if (-not $Quick) {
        $hash = (Get-FileHash $path -Algorithm SHA256).Hash.ToLower()
        if ($hash -ne $entry.sha256) {
            Write-Host "  ПОВРЕЖДЁН  $($entry.path)" -ForegroundColor Red
            $damaged++
        }
    }
}
Write-Progress -Activity 'Проверка комплекта' -Completed

Write-Host ''
if ($missing -gt 0 -or $damaged -gt 0) {
    Write-Host "Отсутствует файлов: $missing, повреждено: $damaged" -ForegroundColor Red
    Write-Host 'Перенесите повреждённые файлы заново — устанавливать нельзя.'
    exit 1
}

# Целые файлы — ещё не полный комплект. manifest.json перечисляет только то,
# что удалось скачать: если сборка сорвалась на моделях, список просто короче,
# и «Комплект целый» было бы неправдой. Сверяем с составом, объявленным при
# сборке.
$нет = @()
if ($manifest.expected) {
    $путиВкомплекте = @($manifest.files | ForEach-Object { $_.path })
    function Есть([string]$образец) {
        return [bool](@($путиВкомплекте | Where-Object { $_ -like $образец }).Count)
    }
    foreach ($файл in @($manifest.expected.models)) {
        if (-not (Есть "models/$файл")) { $нет += "модель $файл" }
    }
    foreach ($язык in @($manifest.expected.tessdata)) {
        if (-not (Есть "tessdata/$язык")) { $нет += "язык Tesseract $язык" }
    }
    $архивов = @($путиВкомплекте | Where-Object { $_ -like 'llama/*.zip' }).Count
    $надо = @($manifest.expected.llama).Count
    if ($надо -and $архивов -lt $надо) {
        $нет += "архивов llama.cpp $архивов из $надо (сервер и библиотеки CUDA)"
    }
    $установщиков = @($путиВкомплекте | Where-Object { $_ -like 'tools/*' }).Count
    $надоПрограмм = @($manifest.expected.tools).Count
    if ($надоПрограмм -and $установщиков -lt $надоПрограмм) {
        $нет += "установщиков программ $установщиков из $надоПрограмм"
    }
}

if ($нет.Count) {
    Write-Host 'Файлы целы, но КОМПЛЕКТ НЕПОЛНЫЙ:' -ForegroundColor Yellow
    foreach ($что in $нет) { Write-Host "  * нет: $что" -ForegroundColor Yellow }
    Write-Host ''
    Write-Host 'Ставить можно, но система заработает не вся: доберите недостающее'
    Write-Host 'на машине с интернетом (.\pack.ps1 -Only <что именно>) и перенесите.'
    exit 2
}

Write-Host 'Комплект целый и полный. Можно устанавливать: .\install-offline.ps1' -ForegroundColor Green
exit 0
