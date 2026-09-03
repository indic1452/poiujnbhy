<#
.SYNOPSIS
    Перевод системы на https и установка своего сертификата на рабочие места.

.DESCRIPTION
    Нужно ровно для одного: чтобы уведомление приходило на рабочий стол,
    когда окно свёрнуто. Браузер показывает такие уведомления только
    «защищённой» странице — https или адрес самой машины с сервером. По http
    на адрес в сети окна нет вовсе: человека вызывают в кабинет, а он не
    знает.

    Купить сертификат в изолированном контуре не у кого, поэтому система
    выписывает его себе сама — на своё имя и на свои адреса в сети. Дальше
    два пути:

      * ничего не делать на рабочих местах — браузер будет ругаться при
        первом заходе, человек нажмёт «Дополнительно → Перейти», и дальше
        всё работает. Ругань повторится после чистки данных браузера;

      * поставить сертификат в доверенные (этот скрипт с ключом -Install на
        каждом рабочем месте) — ругани не будет вовсе.

    Скрипт запускается ДВАЖДЫ и по-разному:

      1. НА СЕРВЕРЕ, один раз:      .\setup-https.ps1
         Включает https в settings.json, выписывает сертификат и говорит,
         куда его скопировать.

      2. НА КАЖДОМ РАБОЧЕМ МЕСТЕ:   .\setup-https.ps1 -Install <путь к .crt>
         Ставит сертификат в доверенные корневые. Нужны права администратора
         на этой машине.

.PARAMETER Install
    Путь к файлу сервер.crt. В этом режиме скрипт только ставит сертификат в
    хранилище Windows и ничего больше не трогает.

.PARAMETER Hosts
    Дополнительные имена и адреса, по которым к системе обращаются: второй
    сетевой адрес, псевдоним из hosts. Имя машины и её адреса система впишет
    сама.

.PARAMETER Renew
    Выписать сертификат заново. Нужно, если у машины сменился адрес в сети:
    браузер сверяет адрес в строке с тем, что в сертификате.

.EXAMPLE
    .\setup-https.ps1
    .\setup-https.ps1 -Hosts '192.168.10.5','otdel-server'
    .\setup-https.ps1 -Install \\otdel-server\obmen\сервер.crt
#>
param(
    [string]$Install = '',
    [string[]]$Hosts = @(),
    [switch]$Renew
)

$ErrorActionPreference = 'Stop'

# --- режим рабочего места: только поставить сертификат в доверенные --------
if ($Install) {
    if (-not (Test-Path -LiteralPath $Install)) {
        Write-Host "Файл не найден: $Install" -ForegroundColor Red
        exit 1
    }
    $admin = ([Security.Principal.WindowsPrincipal] `
        [Security.Principal.WindowsIdentity]::GetCurrent()`
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $admin) {
        Write-Host 'Нужны права администратора: запустите PowerShell от имени администратора.' -ForegroundColor Red
        exit 1
    }
    Import-Certificate -FilePath $Install -CertStoreLocation 'Cert:\LocalMachine\Root' | Out-Null
    Write-Host 'Сертификат установлен в доверенные корневые.' -ForegroundColor Green
    Write-Host 'Закройте браузер целиком и откройте заново — предупреждения больше не будет.'
    exit 0
}

# --- режим сервера ---------------------------------------------------------
. "$PSScriptRoot\_common.ps1"

$settingsPath = Join-Path $PSScriptRoot 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    Write-Host "Нет файла настроек: $settingsPath" -ForegroundColor Red
    Write-Host 'Скопируйте settings.example.json в settings.json и заполните.'
    exit 1
}

$settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$settings | Add-Member -NotePropertyName 'https' -NotePropertyValue $true -Force
if ($Hosts.Count -gt 0) {
    $settings | Add-Member -NotePropertyName 'https_hosts' -NotePropertyValue $Hosts -Force
}
$settings | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $settingsPath -Encoding UTF8
Write-Host 'В settings.json включено https.' -ForegroundColor Green

$dataDir = if ($settings.data_dir) { $settings.data_dir } else { Join-Path $PSScriptRoot '..\..\var' }
$tlsDir = Join-Path $dataDir 'tls'
$certPath = Join-Path $tlsDir 'сервер.crt'

if ($Renew -and (Test-Path -LiteralPath $tlsDir)) {
    Remove-Item -LiteralPath $tlsDir -Recurse -Force
    Write-Host 'Прежний сертификат убран — будет выписан новый.'
}

# Выписываем сертификат прямо сейчас, чтобы человек увидел результат, а не
# ждал первого запуска сервера.
$extra = if ($Hosts.Count -gt 0) { ($Hosts | ForEach-Object { "'$_'" }) -join ', ' } else { '' }
$code = @"
import sys
sys.path.insert(0, r'$(Resolve-Path (Join-Path $PSScriptRoot '..\..\src'))')
from pathlib import Path
from reportgen.web.tls import ensure_certificate, local_addresses
cert, key = ensure_certificate(Path(r'$dataDir'), extra_hosts=[$extra])
print('CERT ' + str(cert))
print('ADDR ' + ', '.join(local_addresses()))
"@

$python = Get-PythonExe
$output = $code | & $python -
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Сертификат выписать не удалось.' -ForegroundColor Red
    Write-Host 'Проверьте, что установлен пакет cryptography: pip install cryptography'
    exit 1
}

$addresses = ($output | Where-Object { $_ -like 'ADDR *' }) -replace '^ADDR ', ''
Write-Host ''
Write-Host 'Сертификат выписан.' -ForegroundColor Green
Write-Host "  файл:    $certPath"
Write-Host "  адреса:  $addresses"
Write-Host ''
Write-Host 'Что дальше:' -ForegroundColor Cyan
Write-Host '  1. Перезапустите приложение: .\stop-all.ps1, затем .\start-all.ps1'
Write-Host "  2. Открывайте систему по https, а не по http:  https://<адрес>:$($settings.port)"
Write-Host '  3. Чтобы браузер не ругался, на КАЖДОМ рабочем месте от имени'
Write-Host '     администратора выполните:'
Write-Host "        .\setup-https.ps1 -Install <путь к сервер.crt>"
Write-Host '     Файл сертификата секретом не является — его можно положить в'
Write-Host '     общую папку. Файл сервер.key не копируйте никуда: это ключ.'
Write-Host ''
Write-Host '  Без шага 3 система тоже работает: браузер один раз спросит,'
Write-Host '  человек нажмёт «Дополнительно → Перейти».'
