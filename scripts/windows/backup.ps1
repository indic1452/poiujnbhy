<#
.SYNOPSIS
    Резервная копия: база данных (горячая, через SQLite .backup), библиотека,
    экспортированные отчёты и настройки.
.PARAMETER Destination
    Куда складывать копии. По умолчанию C:\reportgen\backups.
.PARAMETER Keep
    Сколько последних копий хранить.
#>
param(
    [string]$Destination = '',
    [int]$Keep = 14
)

. "$PSScriptRoot\_common.ps1"

if (-not $Destination) { $Destination = Join-Path $script:Base 'backups' }
if (-not (Test-Path $Destination)) { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }

$stamp  = Get-Date -Format 'yyyy-MM-dd_HHmm'
$folder = Join-Path $Destination $stamp
New-Item -ItemType Directory -Path $folder -Force | Out-Null

Write-Step 'База данных'
$python = Get-PythonExe
$db = Join-Path $script:Data 'reportgen.db'
$copy = Join-Path $folder 'reportgen.db'
if (Test-Path $db) {
    # Горячая копия: приложение можно не останавливать.
    & $python -c "import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()" $db $copy
    & $python -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" $copy
    Write-Ok ("база скопирована ({0} МБ)" -f [math]::Round((Get-Item $copy).Length / 1MB, 1))
} else {
    Write-Warn2 "база не найдена: $db"
}

Write-Step 'Библиотека, экспорты и настройки'
$library = Join-Path $script:Data 'library'
if (Test-Path $library) {
    Compress-Archive -Path $library -DestinationPath (Join-Path $folder 'library.zip') -Force
    Write-Ok 'библиотека заархивирована'
}
$exports = Join-Path $script:Data 'exports'
if ((Test-Path $exports) -and (Get-ChildItem $exports -ErrorAction SilentlyContinue)) {
    Compress-Archive -Path $exports -DestinationPath (Join-Path $folder 'exports.zip') -Force
    Write-Ok 'экспорты заархивированы'
}
if (Test-Path $script:Config) { Copy-Item $script:Config $folder }

Write-Step 'Ротация'
$old = Get-ChildItem $Destination -Directory | Sort-Object Name -Descending | Select-Object -Skip $Keep
foreach ($item in $old) { Remove-Item $item.FullName -Recurse -Force; Write-Ok "удалена старая копия $($item.Name)" }

Write-Host ''
Write-Host "Копия готова: $folder" -ForegroundColor Green
Write-Host 'Восстановление: остановить приложение, положить reportgen.db обратно в data\, распаковать library.zip.'
