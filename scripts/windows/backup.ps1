<#
.SYNOPSIS
    Резервная копия: база данных, все файлы отдела и настройки.

.DESCRIPTION
    Что копируется, скрипт не помнит — он спрашивает у самого приложения
    («reportgen paths»). Раньше пути были записаны здесь, и они разошлись:
    копия делалась из C:\reportgen\data, даже если данные лежали в другом
    месте, а вложения писем, файлы переписки и личных карточек не попадали в
    неё вовсе. Восстановление из такой копии дало бы письма без приложенных
    к ним файлов — и заметили бы это в тот день, когда файл понадобится.

    Копия устроена в два слоя, и это не прихоть:

      файлы\        — прирастающая копия всех папок. Файлы отдела почти не
                      меняются, зато их много: складывать их в архив каждый
                      день бессмысленно, а Compress-Archive в Windows
                      PowerShell 5.1 к тому же не осилит библиотеку — предел
                      .NET Framework 2 ГБ. Из этого слоя ничего не удаляется:
                      стёртый по ошибке файл останется в копии.

      <дата>\       — база и настройки на этот час. База меняется постоянно и
                      весит мало, поэтому её копий держим несколько.

.PARAMETER Destination
    Куда складывать. По умолчанию C:\reportgen\backups.
.PARAMETER Keep
    Сколько последних копий базы хранить. Слой «файлы» не чистится никогда.
#>
param(
    [string]$Destination = '',
    [int]$Keep = 14
)

. "$PSScriptRoot\_common.ps1"

if (-not $Destination) { $Destination = Join-Path $script:Base 'backups' }
if (-not (Test-Path $Destination)) { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }

# --- что копировать: спрашиваем у приложения -------------------------------
$python = Get-PythonExe
$env:PYTHONPATH = Join-Path $script:Root 'src'
$env:REPORTGEN_CONFIG = $script:Config
$прежний = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $вывод = & $python -m reportgen --config $script:Config paths --json 2>&1
} finally {
    $ErrorActionPreference = $прежний
}
$опись = $null
foreach ($строка in @($вывод | ForEach-Object { "$_" })) {
    if ($строка.TrimStart().StartsWith('{')) {
        try { $опись = $строка | ConvertFrom-Json } catch { }
    }
}
if ($null -eq $опись) {
    Write-Bad 'не удалось спросить у приложения, где лежат данные'
    @($вывод | ForEach-Object { "$_" }) | ForEach-Object { Write-Host "    $_" }
    Write-Host 'Без этого копия вышла бы неполной — а неполная копия хуже, чем никакой.'
    exit 1
}
Write-Ok "каталог данных: $($опись.data_dir)"

$нужные = @($опись.places | Where-Object { $_.в_копию })
$база = $нужные | Where-Object { -not $_.папка } | Select-Object -First 1
$папки = @($нужные | Where-Object { $_.папка })

# --- хватит ли места -------------------------------------------------------
$объём = 0L
foreach ($место in $нужные) {
    if (-not (Test-Path -LiteralPath $место.путь)) { continue }
    if ($место.папка) {
        $объём += (Get-ChildItem -LiteralPath $место.путь -Recurse -File -ErrorAction SilentlyContinue |
                   Measure-Object -Property Length -Sum).Sum
    } else {
        $объём += (Get-Item -LiteralPath $место.путь).Length
    }
}
try {
    $диск = [System.IO.DriveInfo]::new((Resolve-Path -LiteralPath $Destination).Path)
    $свободно = $диск.AvailableFreeSpace
    Write-Ok ("данных {0} ГБ, свободно на диске {1} ГБ" -f `
        [math]::Round($объём / 1GB, 1), [math]::Round($свободно / 1GB, 1))
    if ($свободно -lt $объём * 0.2) {
        Write-Warn2 'места на диске может не хватить — копия будет неполной'
    }
} catch { }

$stamp  = Get-Date -Format 'yyyy-MM-dd_HHmm'
$folder = Join-Path $Destination $stamp
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$отчёт = New-Object System.Collections.ArrayList

# --- база: горячая копия и проверка целостности ----------------------------
Write-Step 'База данных'
$сбои = 0
if ($база -and (Test-Path -LiteralPath $база.путь)) {
    $копия = Join-Path $folder 'reportgen.db'
    & $python -c "import sqlite3,sys; src=sqlite3.connect(sys.argv[1]); dst=sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()" $база.путь $копия
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $копия)) {
        Write-Bad 'база не скопировалась'
        $сбои++
    } else {
        # Проверяем копию, а не подлинник: испорченной она может стать именно
        # при копировании, и узнать об этом надо сейчас, а не при беде.
        $итог = & $python -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('PRAGMA integrity_check').fetchone()[0])" $копия
        if ("$итог".Trim() -ne 'ok') {
            Write-Bad "копия базы повреждена: $итог"
            $сбои++
        } else {
            $размер = [math]::Round((Get-Item $копия).Length / 1MB, 1)
            Write-Ok "база скопирована и проверена ($размер МБ)"
            [void]$отчёт.Add("база: $размер МБ, integrity_check ok")
        }
    }
} else {
    Write-Warn2 "база не найдена: $(if ($база) { $база.путь } else { '—' })"
    [void]$отчёт.Add('база: НЕ НАЙДЕНА')
    $сбои++
}

# --- файлы: прирастающая копия ---------------------------------------------
Write-Step 'Файлы отдела'
$слой = Join-Path $Destination 'файлы'
if (-not (Test-Path $слой)) { New-Item -ItemType Directory -Path $слой -Force | Out-Null }
$естьRobocopy = [bool](Get-Command robocopy.exe -ErrorAction SilentlyContinue)

foreach ($место in $папки) {
    if (-not (Test-Path -LiteralPath $место.путь)) {
        [void]$отчёт.Add("$($место.имя): пусто (папки нет)")
        continue
    }
    $куда = Join-Path $слой $место.имя
    if ($естьRobocopy) {
        # Ключа /MIR тут нарочно нет: копия не должна удалять у себя то, что
        # стёрли в рабочем каталоге. Коды возврата robocopy до 8 — это успех,
        # а не ошибка: 1 значит «скопировано», 3 — «скопировано и пропущено».
        & robocopy.exe $место.путь $куда /E /R:2 /W:2 /NFL /NDL /NP /NJH /NJS | Out-Null
        $код = $LASTEXITCODE
        if ($код -ge 8) {
            Write-Bad "$($место.имя): robocopy вернул $код"
            [void]$отчёт.Add("$($место.имя): ОШИБКА robocopy $код")
            $сбои++
            continue
        }
    } else {
        if (-not (Test-Path $куда)) { New-Item -ItemType Directory -Path $куда -Force | Out-Null }
        Copy-Item -LiteralPath $место.путь -Destination $слой -Recurse -Force
    }
    $файлов = @(Get-ChildItem -LiteralPath $куда -Recurse -File -ErrorAction SilentlyContinue)
    $мб = 0
    if ($файлов.Count) {
        $мб = [math]::Round((($файлов | Measure-Object -Property Length -Sum).Sum) / 1MB, 1)
    }
    Write-Ok ("{0}: {1} файлов, {2} МБ — {3}" -f $место.имя, $файлов.Count, $мб, $место.что)
    [void]$отчёт.Add("$($место.имя): $($файлов.Count) файлов, $мб МБ")
}

if (Test-Path $script:Config) {
    Copy-Item $script:Config $folder
    [void]$отчёт.Add('settings.json: скопирован')
}

# --- опись: что вошло в копию ----------------------------------------------
$лист = @("Резервная копия $stamp", "каталог данных: $($опись.data_dir)", '') + $отчёт
$лист += @('', 'Восстановление:',
           '  1) остановить приложение (.\stop-all.ps1);',
           "  2) положить reportgen.db обратно в $($опись.data_dir);",
           "  3) скопировать содержимое $слой в $($опись.data_dir);",
           '  4) запустить .\start-all.ps1.')
[System.IO.File]::WriteAllLines((Join-Path $folder 'опись.txt'), $лист,
    (New-Object System.Text.UTF8Encoding($true)))

# --- ротация: чистим только слой с базами ----------------------------------
Write-Step 'Ротация'
$старые = Get-ChildItem $Destination -Directory |
    Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_\d{4}$' } |
    Sort-Object Name -Descending | Select-Object -Skip $Keep
foreach ($item in $старые) {
    Remove-Item $item.FullName -Recurse -Force
    Write-Ok "удалена старая копия $($item.Name)"
}

Write-Host ''
if ($сбои -gt 0) {
    Write-Bad "копия сделана НЕ ПОЛНОСТЬЮ: сбоев $сбои. Смотрите опись: $folder\опись.txt"
    exit 1
}
Write-Host "Копия готова: $folder" -ForegroundColor Green
Write-Host "Файлы отдела: $слой"
Write-Host "Что вошло: $folder\опись.txt"
