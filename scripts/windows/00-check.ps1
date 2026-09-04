<#
.SYNOPSIS
    Проверка машины перед установкой: GPU, драйвер, VRAM, Python, место на диске.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\00-check.ps1
#>
. "$PSScriptRoot\_common.ps1"

$problems = 0

Write-Step 'Видеокарта и драйвер NVIDIA'
try {
    $smi = & nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader
    foreach ($line in $smi) {
        $parts = $line -split ',\s*'
        Write-Ok "$($parts[0]), драйвер $($parts[1]), память $($parts[2]) (занято $($parts[3]))"
        $totalMiB = [int](($parts[2] -replace '[^\d]', ''))
        $usedMiB  = [int](($parts[3] -replace '[^\d]', ''))
        $freeGiB  = [math]::Round(($totalMiB - $usedMiB) / 1024, 1)
        if ($freeGiB -lt 13) {
            Write-Warn2 "свободно всего $freeGiB ГБ VRAM — закройте игры, браузер и другие тяжёлые приложения"
        } else {
            Write-Ok "свободно $freeGiB ГБ VRAM — достаточно"
        }
        $driver = [version]($parts[1] -replace '[^\d.]', '')
        if ($driver.Major -lt 550) {
            Write-Warn2 'драйвер старее 550 — обновите: сборки llama.cpp собраны под CUDA 12.4'
        }
    }
} catch {
    Write-Bad 'nvidia-smi не найден: не установлен драйвер NVIDIA или он не в PATH'
    $problems++
}

Write-Step 'Оперативная память и диск'
$ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
if ($ram -lt 32) { Write-Warn2 "ОЗУ $ram ГБ — маловато для выгрузки экспертов MoE" } else { Write-Ok "ОЗУ $ram ГБ" }
$letter = if (Test-Path $script:Base) { (Get-Item $script:Base).PSDrive.Name } else { (Get-Location).Drive.Name }
$free = [math]::Round((Get-PSDrive $letter).Free / 1GB, 1)
if ($free -lt 60) { Write-Warn2 "на диске ${letter}: свободно $free ГБ — модели занимают 15-25 ГБ" } else { Write-Ok "на диске ${letter}: свободно $free ГБ" }

Write-Step 'Python'
try {
    $прежний = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $version = (& python -V 2>&1) -join ' ' } finally { $ErrorActionPreference = $прежний }
    if (-not $version) { throw 'python не отвечает' }
    Write-Ok $version
    $numbers = [version](($version -replace '[^\d.]', '').Trim('.'))
    if ($numbers.Major -lt 3 -or ($numbers.Major -eq 3 -and $numbers.Minor -lt 11)) {
        Write-Bad 'нужен Python 3.11 или новее'
        $problems++
    }
} catch {
    Write-Bad 'python не найден в PATH'
    # На машине отдела интернета нет, и посылать за установщиком на python.org
    # бессмысленно: он лежит в комплекте офлайн-установки.
    $комплект = @('D:\reportgen-offline\tools', 'E:\reportgen-offline\tools',
                  (Join-Path $script:Base 'reportgen-offline\tools')) |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($комплект) {
        Write-Host "     установщик Python лежит в комплекте: $комплект"
        Write-Host '     поставьте его, отметив «Add python.exe to PATH», и запустите проверку снова'
    } else {
        Write-Host '     установщик Python есть в комплекте офлайн-установки (папка tools)'
        Write-Host '     на машине с интернетом его берут с python.org, отметив «Add python.exe to PATH»'
    }
    $problems++
}

Write-Step 'Компоненты установки'
foreach ($item in @(
    @{ Path = (Join-Path $script:Llama 'llama-server.exe'); Name = 'llama.cpp (llama-server.exe)' },
    @{ Path = $script:Models; Name = 'каталог моделей' },
    @{ Path = $script:Config; Name = 'файл настроек settings.json' },
    @{ Path = $script:Venv;   Name = 'виртуальное окружение Python' }
)) {
    if (Test-Path $item.Path) { Write-Ok $item.Name } else { Write-Warn2 "$($item.Name) ещё не установлен: $($item.Path)" }
}

if (Test-Path $script:Models) {
    $ggufs = Get-ChildItem $script:Models -Filter *.gguf -ErrorAction SilentlyContinue
    if ($ggufs) {
        foreach ($file in $ggufs) {
            Write-Ok ("модель {0} ({1} ГБ)" -f $file.Name, [math]::Round($file.Length / 1GB, 1))
        }
    } else {
        Write-Warn2 'в каталоге моделей нет ни одного файла .gguf'
    }
}

Write-Step 'Порты'
foreach ($port in 8000, 8001, 8002, 8080) {
    if (Test-Port $port) { Write-Ok "порт $port свободен" } else { Write-Warn2 "порт $port занят — возможно, сервис уже запущен" }
}

Write-Host ''
if ($problems -eq 0) {
    Write-Host 'Критичных проблем нет.' -ForegroundColor Green
    # Без ключа -Wheels установка пойдёт в PyPI и на изолированной машине
    # встанет по таймауту. Называем сразу ту команду, которая сработает.
    $колёса = @('D:\reportgen-offline\wheels', 'E:\reportgen-offline\wheels',
                (Join-Path $script:Base 'reportgen-offline\wheels')) |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($колёса) {
        Write-Host "Следующий шаг: .\01-install.ps1 -Wheels $колёса"
    } else {
        Write-Host 'Следующий шаг: .\01-install.ps1'
        Write-Host '  на машине без интернета — с каталогом колёс из комплекта:'
        Write-Host '  .\01-install.ps1 -Wheels D:\reportgen-offline\wheels'
    }
} else {
    Write-Host "Найдено критичных проблем: $problems. Исправьте и запустите проверку снова." -ForegroundColor Red
    exit 1
}
