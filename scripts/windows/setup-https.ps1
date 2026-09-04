<#
.SYNOPSIS
    Перевод системы на https и установка корневого сертификата на рабочие места.

.DESCRIPTION
    Нужно ровно для одного: чтобы уведомление приходило на рабочий стол, когда
    окно свёрнуто. Браузер показывает такие уведомления только «защищённой»
    странице — https или адрес самой машины с сервером. По http на адрес в сети
    окна нет вовсе: человека вызывают в кабинет, а он не знает.

    Купить сертификат в изолированном контуре не у кого, поэтому система
    выписывает его себе сама, средствами одного лишь Python — без интернета и
    без единого стороннего пакета.

    Выписывается два файла, и это важно понимать, чтобы не ходить по кабинетам
    дважды:

      корень.crt   — им подписан серверный. ЕГО ставят в доверенные на рабочих
                     местах, один раз и навсегда: он не зависит ни от адреса
                     машины, ни от её имени;
      сервер.crt   — его показывает браузеру сама система. Сменился адрес в
                     сети — система перевыпишет его сама при следующем запуске,
                     и на рабочих местах не нужно трогать ничего.

    Скрипт запускается в двух разных местах:

      1. НА СЕРВЕРЕ, один раз:      .\setup-https.ps1
         Включает https в настройках, выписывает сертификат и проверяет его.

      2. НА КАЖДОМ РАБОЧЕМ МЕСТЕ:   .\setup-https.ps1 -Install <путь к корень.crt>
         Ставит корень в доверенные. Нужны права администратора на этой машине.
         Шаг необязательный: без него браузер один раз спросит, и человек
         нажмёт «Дополнительно → Перейти».

.PARAMETER Install
    Путь к файлу корень.crt (или к папке, где он лежит). В этом режиме скрипт
    только ставит сертификат в хранилище Windows и больше ничего не трогает.

.PARAMETER Hosts
    Дополнительные имена и адреса, по которым к системе обращаются: второй
    сетевой адрес, псевдоним из hosts. Имя машины и её адреса система впишет
    сама.

.PARAMETER Renew
    Перевыписать серверный сертификат заново. Обычно не нужно: смену адреса
    система замечает сама.

.PARAMETER Export
    Скопировать корень.crt в указанную папку — например, в общую папку обмена,
    откуда его заберут на рабочие места.

.PARAMETER Off
    Вернуть работу по http. Уведомлений на рабочем столе не будет.

.EXAMPLE
    .\setup-https.ps1
    .\setup-https.ps1 -Hosts '192.168.10.5','otdel-server' -Export \\otdel-server\obmen
    .\setup-https.ps1 -Install \\otdel-server\obmen\корень.crt
#>
param(
    [string]$Install = '',
    [string[]]$Hosts = @(),
    [switch]$Renew,
    [string]$Export = '',
    [switch]$Off
)

$ErrorActionPreference = 'Stop'

# --- режим рабочего места: только поставить корень в доверенные -------------
if ($Install) {
    $file = $Install
    if (Test-Path -LiteralPath $file -PathType Container) {
        # Указали папку — ищем корень сами: человеку незачем помнить имя файла.
        $found = Join-Path $file 'корень.crt'
        if (Test-Path -LiteralPath $found) {
            $file = $found
        } else {
            Write-Host "В папке $Install нет файла корень.crt." -ForegroundColor Red
            Write-Host 'Возьмите его на сервере: <каталог данных>\tls\корень.crt'
            exit 1
        }
    }
    if (-not (Test-Path -LiteralPath $file)) {
        Write-Host "Файл не найден: $file" -ForegroundColor Red
        Write-Host 'Нужен корень.crt с сервера: <каталог данных>\tls\корень.crt'
        exit 1
    }
    $admin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()`
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Write-Host 'Нужны права администратора: запустите PowerShell от имени администратора.' -ForegroundColor Red
        exit 1
    }

    $before = @(Get-ChildItem 'Cert:\LocalMachine\Root' -ErrorAction SilentlyContinue).Count
    $поставили = $false
    # Import-Certificate есть не во всякой сборке Windows — если его нет,
    # обходимся certutil, он был всегда.
    if (Get-Command Import-Certificate -ErrorAction SilentlyContinue) {
        try {
            Import-Certificate -FilePath $file -CertStoreLocation 'Cert:\LocalMachine\Root' | Out-Null
            $поставили = $true
        } catch {
            Write-Host ('Import-Certificate не справился: ' + $_.Exception.Message) -ForegroundColor Yellow
        }
    }
    if (-not $поставили) {
        & certutil.exe -addstore -f Root $file | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'Поставить сертификат не удалось.' -ForegroundColor Red
            Write-Host 'Попробуйте вручную: дважды щёлкните по файлу → Установить сертификат →'
            Write-Host 'Локальный компьютер → Поместить в: Доверенные корневые центры сертификации.'
            exit 1
        }
    }

    # Проверяем, а не рапортуем: сертификат должен появиться в хранилище.
    $отпечаток = ([System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        (Resolve-Path -LiteralPath $file).Path)).Thumbprint
    $вхранилище = Get-ChildItem 'Cert:\LocalMachine\Root' |
        Where-Object { $_.Thumbprint -eq $отпечаток }
    if (-not $вхранилище) {
        Write-Host 'Сертификат в доверенных не появился — установка не удалась.' -ForegroundColor Red
        exit 1
    }
    Write-Host 'Корневой сертификат установлен в доверенные.' -ForegroundColor Green
    Write-Host "  отпечаток: $отпечаток"
    Write-Host 'Закройте браузер целиком и откройте заново — предупреждения больше не будет.'
    exit 0
}

# --- режим сервера ---------------------------------------------------------
. "$PSScriptRoot\_common.ps1"

if (-not (Test-Path -LiteralPath $script:Config)) {
    Write-Bad "нет файла настроек: $script:Config"
    Write-Host 'Сначала выполните установку: .\01-install.ps1 — она создаёт этот файл.'
    $мимо = Join-Path $PSScriptRoot 'settings.json'
    if (Test-Path -LiteralPath $мимо) {
        Write-Warn2 "файл $мимо система не читает — настройки берутся только из $script:Config"
    }
    exit 1
}

$settings = Get-Content -LiteralPath $script:Config -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Off) {
    $settings | Add-Member -NotePropertyName 'https' -NotePropertyValue $false -Force
} else {
    $settings | Add-Member -NotePropertyName 'https' -NotePropertyValue $true -Force
}
# Перечисление через запятую одной строкой — обычное дело; разбираем сами,
# чтобы «192.168.10.5,otdel-server» не попало в сертификат одним именем.
$адреса = @()
foreach ($кусок in $Hosts) {
    foreach ($часть in ("$кусок" -split '[,;]')) {
        $часть = $часть.Trim()
        if ($часть -and ($адреса -notcontains $часть)) { $адреса += $часть }
    }
}
if ($адреса.Count -gt 0) {
    $settings | Add-Member -NotePropertyName 'https_hosts' -NotePropertyValue $адреса -Force
} elseif ($settings.https_hosts) {
    # Повторный запуск без -Hosts не должен выбрасывать из сертификата то,
    # что вписали в прошлый раз: иначе второй запуск ломает первый.
    foreach ($имя in $settings.https_hosts) {
        if ($имя -and ($адреса -notcontains $имя)) { $адреса += "$имя" }
    }
}
# Без BOM: PowerShell 5.1 на "-Encoding UTF8" добавил бы его, а файл настроек
# читают ещё и посторонние средства, которые на BOM спотыкаются.
$json = $settings | ConvertTo-Json -Depth 10
[System.IO.File]::WriteAllText($script:Config, $json, (New-Object System.Text.UTF8Encoding($false)))

if ($Off) {
    Write-Ok 'вернулись на http'
    Write-Host 'Перезапустите приложение: .\stop-all.ps1, затем .\start-all.ps1'
    Write-Warn2 'уведомлений на рабочем столе по http не будет — так устроен браузер'
    exit 0
}
Write-Ok "в $script:Config включено https"

$dataDir = Get-Setting 'data_dir' (Join-Path $script:Base 'data')
$port = [int](Get-Setting 'port' 8080)

# Выписываем прямо сейчас, чтобы человек увидел результат, а не ждал первого
# запуска сервера. Сторонних пакетов для этого не нужно — всё своё.
$аргументы = @('-m', 'reportgen.web.tls', '--data-dir', $dataDir, '--json')
$brand = Get-Setting 'brand_name' ''
if ($brand) { $аргументы += @('--brand', $brand) }
foreach ($имя in $адреса) { $аргументы += @('--host', $имя) }
if ($Renew) { $аргументы += '--renew' }

$python = Get-PythonExe
$env:PYTHONPATH = Join-Path $script:Root 'src'
# Windows PowerShell 5.1 при $ErrorActionPreference = 'Stop' считает ошибкой
# любую строку, которую внешняя программа написала в поток ошибок, — и
# «2>&1» превращает питоновский след в обрыв всего скрипта. Нам нужен
# обратный порядок: сначала прочитать, потом решать.
$прежний = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    $вывод = & $python @аргументы 2>&1
} finally {
    $ErrorActionPreference = $прежний
}
$строки = @($вывод | ForEach-Object { "$_" })
$ответ = $null
foreach ($строка in $строки) {
    if ($строка.TrimStart().StartsWith('{')) {
        try { $ответ = $строка | ConvertFrom-Json } catch { }
    }
}
if ($null -eq $ответ) {
    Write-Bad 'выписать сертификат не удалось'
    $строки | ForEach-Object { Write-Host "    $_" }
    Write-Host ''
    Write-Host 'Что проверить:' -ForegroundColor Cyan
    Write-Host "  * запускалась ли установка .\01-install.ps1 (нужен Python в $script:Venv);"
    Write-Host "  * доступен ли на запись каталог данных $dataDir."
    exit 1
}
if (-not $ответ.ok) {
    Write-Bad ('сертификат не готов: ' + $ответ.problem)
    exit 1
}

Write-Host ''
Write-Ok 'сертификат выписан и проверен'
Write-Host "  серверный: $($ответ.cert)"
Write-Host "  корень:    $($ответ.root)"
Write-Host "  адреса:    $($ответ.hosts -join ', ')"
Write-Host "  годен до:  $($ответ.until)"

# Готовый файл для рабочего места: двойной щелчок — и всё. Ни PowerShell,
# ни прав администратора: сертификат кладётся в хранилище САМОГО человека,
# а не машины, и Chrome с Edge читают его оттуда. Обходить кабинеты с
# командной строкой ради этого никто не станет — значит, и не надо.
$пускач = @'
@echo off
title Доверие серверу отдела
echo.
echo   Ставлю корневой сертификат отдела в доверенные.
echo   Windows один раз спросит подтверждение - согласитесь.
echo.
certutil -addstore -user Root "%~dp0корень.crt"
if errorlevel 1 goto beda
echo.
echo   Готово. Закройте браузер ЦЕЛИКОМ и откройте заново -
echo   предупреждения о сертификате больше не будет.
echo.
pause
exit /b 0
:beda
echo.
echo   Не получилось. Возможные причины:
echo     * рядом с этим файлом нет корень.crt;
echo     * установку отменили в окне подтверждения.
echo.
echo   Ничего страшного: система работает и так, браузер просто
echo   один раз спросит - нажмите "Дополнительно" и "Перейти".
echo.
pause
exit /b 1
'@

function Write-Пускач([string]$каталог) {
    # Кодировка OEM (866): cmd.exe читает её без chcp, и кириллица в окне
    # видна как кириллица, а не как набор знаков.
    $путь = Join-Path $каталог 'Доверять серверу.cmd'
    $кодировка = [System.Text.Encoding]::GetEncoding(866)
    [System.IO.File]::WriteAllText($путь, ($пускач -replace "`r?`n", "`r`n"), $кодировка)
    return $путь
}

$откуда = $ответ.root
$пускачПуть = Write-Пускач (Split-Path -Parent $ответ.root)
if ($Export) {
    if (-not (Test-Path -LiteralPath $Export)) {
        New-Item -ItemType Directory -Path $Export -Force | Out-Null
    }
    Copy-Item -LiteralPath $ответ.root -Destination $Export -Force
    $пускачПуть = Write-Пускач $Export
    Write-Ok "корень и «Доверять серверу.cmd» скопированы в $Export"
    $откуда = Join-Path $Export 'корень.crt'
}

Write-Host ''
Write-Host 'Что дальше:' -ForegroundColor Cyan
Write-Host '  1. Перезапустите приложение: .\stop-all.ps1, затем .\start-all.ps1'
Write-Host '  2. Открывайте систему по https, а не по http:'
# Адрес называем тот, который и правда откроется. Печатать адрес в сети,
# когда сервер слушает только себя, — значит послать человека по адресу,
# который у него не работает.
$слушает = Get-Setting 'host' '127.0.0.1'
if ($слушает -eq '0.0.0.0' -or $слушает -eq '::') {
    $lan = Get-LanAddress
    if ($lan) {
        Write-Host "        https://$lan`:$port"
        if ($ответ.hosts -notcontains $lan) {
            Write-Warn2 ("адреса $lan в сертификате нет — браузер будет ругаться " +
                         "на несовпадение имени; допишите: .\setup-https.ps1 -Hosts '$lan'")
        }
    } else {
        Write-Host "        https://<адрес этой машины>`:$port"
    }
} else {
    Write-Host "        https://$слушает`:$port"
    if ($слушает -eq '127.0.0.1' -or $слушает -eq 'localhost') {
        Write-Warn2 "сервер слушает только эту машину: коллегам по сети система недоступна"
        Write-Host "     чтобы открыть её отделу, поставьте host = 0.0.0.0 в $script:Config"
    }
}
Write-Host '  3. НЕОБЯЗАТЕЛЬНО — чтобы браузер не спрашивал про сертификат.'
Write-Host '     Положите в общую папку два файла:'
Write-Host "        $((Split-Path -Parent $откуда))\корень.crt"
Write-Host "        $((Split-Path -Parent $пускачПуть))\Доверять серверу.cmd"
Write-Host '     На рабочем месте человек запускает «Доверять серверу.cmd»'
Write-Host '     двойным щелчком и один раз соглашается. Ни прав'
Write-Host '     администратора, ни командной строки не нужно.'
Write-Host "     (Ключ -Export кладёт оба файла в нужную папку сам.)"
Write-Host '     Корень секретом не является. Файлы сервер.key и корень.key'
Write-Host '     не копируйте никуда: это ключи.'
Write-Host ''
Write-Host '  Шаг 3 можно и пропустить: система работает и так — браузер'
Write-Host '  один раз спросит, человек нажмёт «Дополнительно → Перейти».'
Write-Host '  Firefox своё хранилище ведёт отдельно: там только этот путь.'
Write-Host '  Сменится адрес машины в сети — система перевыпишет серверный'
Write-Host '  сертификат сама, обходить рабочие места заново не придётся.'
