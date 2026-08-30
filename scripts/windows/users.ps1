<#
.SYNOPSIS
    Сотрудники: список, создание, смена пароля.
.DESCRIPTION
    Без ключей — показывает личный состав. Этого достаточно, чтобы понять,
    заведён ли создатель системы и под каким логином входить.
.PARAMETER Add
    Логин нового сотрудника. Пароль будет запрошен (не короче 8 символов).
.PARAMETER Reset
    Логин сотрудника, которому нужно сменить пароль. Годится и для себя,
    когда пароль забыт: старый знать не требуется.
.PARAMETER Role
    Должность: owner (создатель системы, полные права), head (начальник
    отдела), deputy (заместитель начальника отдела), lead (начальник группы),
    senior (старший инженер отдела), engineer (инженер отдела).
    По умолчанию owner — первым заводят именно его.
    Права администратора: owner, head, deputy, lead.
.PARAMETER Name
    ФИО для отображения в интерфейсе, например "Петров П. П.".
.PARAMETER Department
    Подразделение, в котором сотрудник стоит ПО ШТАТУ, например "в/ч 74326".
    Работают все в одном отделе — том, что задан в настройках, — и заполнять
    это поле нужно только тем, кто числится в другом подразделении.
.PARAMETER Team
    Группа внутри отдела, например "1 группа".
.EXAMPLE
    .\users.ps1
.EXAMPLE
    .\users.ps1 -Add admin -Role owner -Name "Петров П. П."
.EXAMPLE
    .\users.ps1 -Add ivanov -Role engineer -Name "Иванов И. И." -Team "1 группа"
.EXAMPLE
    .\users.ps1 -Reset admin
#>
param(
    [string]$Add = '',
    [string]$Reset = '',
    [ValidateSet('owner', 'head', 'deputy', 'lead', 'senior', 'engineer')]
    [string]$Role = 'owner',
    [string]$Name = '',
    [string]$Department = '',
    [string]$Team = ''
)

. "$PSScriptRoot\_common.ps1"

$python = Get-PythonExe
if ($python -eq 'python') {
    Write-Bad "не найдено окружение $script:Venv"
    Write-Warn2 'установка не завершена: cd C:\reportgen-offline ; .\install-offline.ps1 -SkipVerify'
    exit 1
}

if ($Add) {
    Write-Step "Создание сотрудника $Add"
    $arguments = @('useradd', '--login', $Add, '--role', $Role)
    if ($Name) { $arguments += @('--name', $Name) }
    if ($Department) { $arguments += @('--department', $Department) }
    if ($Team) { $arguments += @('--team', $Team) }
    Invoke-Reportgen @arguments
    if ($LASTEXITCODE -ne 0) { Write-Bad 'не удалось создать сотрудника'; exit 1 }
    Write-Ok "готово, входите под логином $Add"
    exit 0
}

if ($Reset) {
    # Старый пароль не требуется: это администрирование на самой машине,
    # а не смена пароля через интерфейс.
    Write-Step "Смена пароля для $Reset"
    Invoke-Reportgen passwd --login $Reset
    if ($LASTEXITCODE -ne 0) { Write-Bad 'не удалось сменить пароль'; exit 1 }
    Write-Ok 'пароль изменён'
    exit 0
}

Write-Step 'Личный состав'
Invoke-Reportgen users
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 'список получить не удалось — возможно, база ещё не создана'
    exit 1
}
Write-Host ''
Write-Host 'Завести создателя системы:  .\users.ps1 -Add admin -Role owner -Name "Петров П. П."' -ForegroundColor DarkGray
Write-Host 'Завести инженера отдела:    .\users.ps1 -Add ivanov -Role engineer -Name "Иванов И. И."' -ForegroundColor DarkGray
Write-Host 'Сменить забытый пароль:     .\users.ps1 -Reset admin' -ForegroundColor DarkGray
