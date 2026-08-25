<#
.SYNOPSIS
    Пользователи системы: список, создание, смена пароля.
.DESCRIPTION
    Без ключей — показывает список заведённых пользователей. Этого достаточно,
    чтобы понять, есть ли вообще администратор и под каким логином входить.
.PARAMETER Add
    Логин нового пользователя. Пароль будет запрошен (не короче 8 символов).
.PARAMETER Reset
    Логин пользователя, которому нужно сменить пароль. Годится и для себя,
    когда пароль забыт: старый знать не требуется.
.PARAMETER Role
    Роль при создании: admin, engineer или viewer. По умолчанию admin.
.PARAMETER Name
    Имя пользователя для отображения в интерфейсе, например "Петров П.П.".
.EXAMPLE
    .\users.ps1
.EXAMPLE
    .\users.ps1 -Add admin
.EXAMPLE
    .\users.ps1 -Reset admin
#>
param(
    [string]$Add = '',
    [string]$Reset = '',
    [ValidateSet('admin', 'engineer', 'viewer')]
    [string]$Role = 'admin',
    [string]$Name = ''
)

. "$PSScriptRoot\_common.ps1"

$python = Get-PythonExe
if ($python -eq 'python') {
    Write-Bad "не найдено окружение $script:Venv"
    Write-Warn2 'установка не завершена: cd C:\reportgen-offline ; .\install-offline.ps1 -SkipVerify'
    exit 1
}

if ($Add) {
    Write-Step "Создание пользователя $Add"
    $arguments = @('useradd', '--login', $Add, '--role', $Role)
    if ($Name) { $arguments += @('--name', $Name) }
    Invoke-Reportgen @arguments
    if ($LASTEXITCODE -ne 0) { Write-Bad 'не удалось создать пользователя'; exit 1 }
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

Write-Step 'Пользователи системы'
Invoke-Reportgen users
if ($LASTEXITCODE -ne 0) {
    Write-Warn2 'список получить не удалось — возможно, база ещё не создана'
    exit 1
}
Write-Host ''
Write-Host 'Создать администратора:   .\users.ps1 -Add admin' -ForegroundColor DarkGray
Write-Host 'Сменить забытый пароль:   .\users.ps1 -Reset admin' -ForegroundColor DarkGray
