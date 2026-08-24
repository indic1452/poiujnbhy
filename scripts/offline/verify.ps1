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
if ($missing -eq 0 -and $damaged -eq 0) {
    Write-Host 'Комплект целый. Можно устанавливать: .\install-offline.ps1' -ForegroundColor Green
    exit 0
}
Write-Host "Отсутствует файлов: $missing, повреждено: $damaged" -ForegroundColor Red
Write-Host 'Перенесите повреждённые файлы заново — устанавливать нельзя.'
exit 1
