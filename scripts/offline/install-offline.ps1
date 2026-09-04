<#
.SYNOPSIS
    Установка на машине БЕЗ интернета из подготовленного комплекта.
.DESCRIPTION
    Разворачивает код, ставит зависимости из локальных колёс (pip в сеть не
    ходит вообще), раскладывает llama.cpp и модели, тихо ставит внешние
    программы, докладывает русский язык в Tesseract, создаёт настройки и
    администратора. Ничего не скачивает: если чего-то не хватает, скрипт
    говорит, что именно доложить в комплект, и продолжает с тем, что есть.
.PARAMETER Target
    Куда ставить. По умолчанию C:\reportgen.
.PARAMETER SkipVerify
    Пропустить проверку контрольных сумм (не рекомендуется).
.PARAMETER SkipTools
    Не ставить LibreOffice, Tesseract, DjVuLibre и 7-Zip.
.PARAMETER Unattended
    Не задавать вопросов: ставить всё, что есть в комплекте, администратора
    не создавать (создадите потом командой reportgen useradd).
.EXAMPLE
    .\install-offline.ps1
.EXAMPLE
    .\install-offline.ps1 -Target D:\reportgen -Unattended
#>
param(
    [string]$Target = 'C:\reportgen',
    [switch]$SkipVerify,
    [switch]$SkipTools,
    [switch]$Unattended
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$bundle = $PSScriptRoot
function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Ok($text)   { Write-Host "  OK  $text" -ForegroundColor Green }
function Warn($text) { Write-Host "  !   $text" -ForegroundColor Yellow }
function Note($text) { Write-Host "      $text" -ForegroundColor DarkGray }
function Fail($text) { Write-Host "  X   $text" -ForegroundColor Red; exit 1 }

$script:Warnings = @()
function Later($text) { $script:Warnings += $text; Warn $text }

function Invoke-Native {
    <#
        Запустить внешнюю программу и вернуть её вывод, не оборвав установку.

        Windows PowerShell 5.1 при $ErrorActionPreference = 'Stop' считает
        ошибкой каждую строку, которую внешняя программа написала в поток
        ошибок, — а «2>&1» отдаёт эти строки как ошибки. Из-за этого установка
        обрывалась на git clone (git пишет туда «Cloning into...»), на
        tesseract --list-langs (он пишет туда весь свой ответ) и на pip.
        Обрывалась молча, до создания настроек и администратора.

        Код возврата кладём в $script:NativeExit: $LASTEXITCODE после разбора
        вывода уже не тот.
    #>
    param([string]$Path, [string[]]$Arguments = @())
    $прежний = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $вывод = & $Path @Arguments 2>&1
        $script:NativeExit = $LASTEXITCODE
        return @($вывод | ForEach-Object { "$_" })
    } catch {
        $script:NativeExit = 1
        return @("$($_.Exception.Message)")
    } finally {
        $ErrorActionPreference = $прежний
    }
}

function Write-Utf8NoBom([string]$path, [string]$text) {
    # Windows PowerShell 5.1 на "-Encoding UTF8" пишет BOM. JSON с BOM читается
    # не всеми разборщиками, поэтому пишем через .NET явно без него.
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

function New-Dir([string]$path) {
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
    return $path
}

function Confirm-Step([string]$question) {
    if ($Unattended) { return $true }
    return ((Read-Host "$question (д/н)") -match '^[дdyY]')
}

$plan = $null
$planPath = Join-Path $bundle 'bundle.json'
if (Test-Path $planPath) {
    $plan = Get-Content $planPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

# ------------------------------------------------------- права и место -----
Step 'Условия установки'
$identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # Без прав администратора тихая установка программ в Program Files и
    # запись REPORTGEN_HOME в машинную область просто не сработают, а скрипт
    # дойдёт до конца и отрапортует об успехе.
    Fail 'запустите PowerShell от имени администратора: без этого не встанут LibreOffice, Tesseract и DjVuLibre'
}
Ok 'права администратора есть'

# Комплект копируется целиком: 17 ГБ моделей плюс распакованный llama.cpp.
$needBytes = 0
$manifestPath = Join-Path $bundle 'manifest.json'
if (Test-Path $manifestPath) {
    $manifest = Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $needBytes = ($manifest.files | Measure-Object bytes -Sum).Sum
}
if ($needBytes) {
    # С запасом: модели копируются, архивы llama.cpp ещё и распаковываются.
    $needGb = [math]::Round(($needBytes * 1.4) / 1GB, 1)
    # Split-Path -Qualifier на пути без буквы диска не возвращает пустоту, а
    # БРОСАЕТ исключение — и при $ErrorActionPreference = 'Stop' обрывает
    # установку целиком. Так падало на сетевом пути (\\сервер\обмен\reportgen):
    # запасная ветка ниже, написанная как раз на этот случай, не выполнялась
    # никогда.
    $drive = ''
    try { $drive = Split-Path $Target -Qualifier } catch { $drive = '' }
    if (-not $drive) { $drive = (Get-Location).Drive.Name + ':' }
    $free = (Get-PSDrive -Name $drive.TrimEnd(':') -ErrorAction SilentlyContinue).Free
    if ($free) {
        $freeGb = [math]::Round($free / 1GB, 1)
        if ($free -lt $needBytes * 1.4) {
            Fail "на диске $drive свободно $freeGb ГБ, а нужно не меньше $needGb ГБ"
        }
        Ok "на диске $drive свободно $freeGb ГБ, нужно около $needGb ГБ"
    }
}

# ------------------------------------------------------------- проверка ----
if (-not $SkipVerify) {
    Step 'Проверка комплекта'
    & (Join-Path $bundle 'verify.ps1')
    # 1 — файлы битые или потеряны: ставить нельзя. 2 — файлы целы, но чего-то
    # в комплекте нет вовсе: поставить можно, работать будет не всё, и об этом
    # человек должен прочитать в итоге, а не гадать при первом запуске.
    if ($LASTEXITCODE -eq 1) { Fail 'комплект повреждён — установка отменена' }
    if ($LASTEXITCODE -eq 2) { Later 'комплект неполный — часть возможностей работать не будет (см. список выше)' }
}

# --------------------------------------------------------------- Python ----
Step 'Python'
$python = $null
foreach ($candidate in @('python', 'py -3.11')) {
    try {
        $version = & cmd /c "$candidate -V 2>&1"
        if ($version -match 'Python 3\.(1[1-9]|[2-9][0-9])') { $python = $candidate; Ok $version; break }
    } catch { }
}
if (-not $python) {
    $installer = Get-ChildItem (Join-Path $bundle 'tools') -Filter 'python-*.exe' -ErrorAction SilentlyContinue |
                 Select-Object -First 1
    if (-not $installer) { Fail 'Python не установлен, и установщика нет в комплекте (tools\python-*.exe)' }
    Warn "Ставлю Python из комплекта: $($installer.Name)"
    $process = Start-Process $installer.FullName -Wait -PassThru `
        -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_pip=1'
    if ($process.ExitCode -ne 0) {
        Fail "установщик Python вернул код $($process.ExitCode) — установите Python вручную из $($installer.FullName)"
    }
    Fail 'Python установлен. Закройте это окно, откройте новое (чтобы подхватился PATH) и запустите скрипт снова'
}

$expected = Join-Path $bundle 'wheels\PYTHON-VERSION.txt'
if (Test-Path $expected) {
    $want = (Get-Content $expected -Raw).Trim()
    $have = (& cmd /c "$python -c ""import sys; print('%d.%d' % sys.version_info[:2])""").Trim()
    if ($want -ne $have) {
        Fail "колёса собраны для Python $want, а установлен $have — они не подойдут. Поставьте Python $want из tools или пересоберите комплект"
    }
}

# ------------------------------------------- внешние программы (тихо) ------
function Test-ToolPresent($tool) {
    foreach ($path in @($tool.check)) {
        if (-not $path) { continue }
        if ($path -notmatch '[\\/]') {
            if (Get-Command $path -ErrorAction SilentlyContinue) { return $true }
        } elseif (Test-Path $path) { return $true }
    }
    return $false
}

# Какой файл какой программе принадлежит, знает каталог из manifest.json:
# его пишет сборщик, и это надёжнее угадывания по имени (vc_redist.x64.exe
# никак не похож на идентификатор vcredist).
function Find-ToolInstaller($tool, $toolsDir, $catalog) {
    $named = $catalog | Where-Object { $_.id -eq $tool.id } | Select-Object -First 1
    if ($named) {
        $path = Join-Path $toolsDir $named.file
        if (Test-Path $path) { return (Get-Item $path) }
    }
    $candidates = Get-ChildItem $toolsDir -File -ErrorAction SilentlyContinue |
                  Where-Object { $_.Extension -in '.exe', '.msi' -and $_.Name -notlike 'python-*' }
    $file = $candidates | Where-Object { $_.BaseName -like "*$($tool.id)*" } | Select-Object -First 1
    if ($file) { return $file }
    foreach ($source in @($tool.sources)) {
        if ($source.pattern) {
            $file = $candidates | Where-Object { $_.Name -like $source.pattern } | Select-Object -First 1
            if ($file) { return $file }
        }
        if ($source.url) {
            $leaf = ($source.url -split '\?')[0]
            $leaf = ($leaf -split '/')[-1]
            $file = $candidates | Where-Object { $_.Name -eq $leaf } | Select-Object -First 1
            if ($file) { return $file }
        }
    }
    return $null
}

if (-not $SkipTools) {
    Step 'Внешние программы для разбора форматов'
    $toolsDir = Join-Path $bundle 'tools'
    $items = @()
    if ($plan) { $items = @($plan.tools.items) }
    $catalog = @()
    $manifestPath = Join-Path $bundle 'manifest.json'
    if (Test-Path $manifestPath) {
        $catalog = @((Get-Content $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json).catalog)
    }
    $ready = @()
    $absent = @()

    foreach ($tool in $items) {
        if ($tool.id -eq 'python') { continue }
        if (Test-ToolPresent $tool) { Ok "$($tool.name): уже установлено"; continue }
        $file = Find-ToolInstaller $tool $toolsDir $catalog
        if ($file) { $ready += [pscustomobject]@{ tool = $tool; file = $file } }
        else { $absent += $tool }
    }

    if ($ready.Count) {
        foreach ($item in $ready) { Note ("{0}: {1}" -f $item.tool.name, $item.file.Name) }
        if (Confirm-Step 'Установить эти программы сейчас') {
            foreach ($item in $ready) {
                $tool = $item.tool; $file = $item.file
                $arguments = @()
                if ($tool.install -and $tool.install.args) { $arguments = @($tool.install.args) }
                Write-Host "  ставлю $($tool.name)…"
                try {
                    if ($file.Extension -eq '.msi') {
                        $msi = @('/i', ('"' + $file.FullName + '"')) + $arguments
                        $process = Start-Process msiexec -ArgumentList $msi -Wait -PassThru
                    } else {
                        $process = Start-Process $file.FullName -ArgumentList $arguments -Wait -PassThru
                    }
                    # Тихий установщик ничего не печатает, поэтому проверяем по файлам.
                    if (Test-ToolPresent $tool) { Ok "$($tool.name) установлено" }
                    elseif ($process.ExitCode -ne 0) { Later "$($tool.name): установщик вернул код $($process.ExitCode)" }
                    else { Later "$($tool.name): установщик отработал, но программа не найдена — поставьте вручную" }
                } catch {
                    Later "$($tool.name): $($_.Exception.Message)"
                }
            }
        } else {
            # Это замечание: без LibreOffice, Tesseract и DjVuLibre система
            # прочитает вчетверо меньше форматов, а «завершена без замечаний»
            # означало бы, что всё на месте.
            Later "внешние программы не ставились — установщики остались в $toolsDir"
        }
    }

    foreach ($tool in $absent) {
        Later "$($tool.name) нет в комплекте — $($tool.why)"
    }
    if (-not $items.Count) {
        Later 'в комплекте нет bundle.json — внешние программы не ставились'
    }
}

# ----------------------------------------- русский язык для Tesseract ------
$tessSource = Join-Path $bundle 'tessdata'
if (Test-Path $tessSource) {
    Step 'Русский язык для Tesseract'
    # Тихая установка Tesseract ставит языки по умолчанию, русского там нет.
    # Без этого шага сканы русских книг распознаются в бессмыслицу.
    $meta = $null
    $metaPath = Join-Path $tessSource 'tessdata.json'
    if (Test-Path $metaPath) { $meta = Get-Content $metaPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    $flavour = if ($meta -and $meta.install_from) { $meta.install_from } else { 'best' }
    $targetDir = if ($meta -and $meta.target) { $meta.target } else { 'C:\Program Files\Tesseract-OCR\tessdata' }

    if (-not (Test-Path $targetDir)) {
        Later "каталог $targetDir не найден — Tesseract не установлен, языки не скопированы"
    } else {
        $from = Join-Path $tessSource $flavour
        $copied = 0
        # Каталог лежит в Program Files: без прав администратора копирование
        # бросит исключение и при $ErrorActionPreference='Stop' оборвёт всю
        # установку — до создания настроек и администратора.
        try {
            foreach ($file in (Get-ChildItem $from -Filter '*.traineddata' -ErrorAction SilentlyContinue)) {
                Copy-Item $file.FullName $targetDir -Force
                $copied++
            }
            # osd лежит только в fast — без него Tesseract ругается на
            # определение ориентации страницы.
            $osd = Join-Path $tessSource 'fast\osd.traineddata'
            if ((Test-Path $osd) -and -not (Test-Path (Join-Path $targetDir 'osd.traineddata'))) {
                Copy-Item $osd $targetDir -Force
                $copied++
            }
            if ($copied -eq 0) {
                # Ноль скопированных — это не успех: без русского языка сканы
                # русских книг распознаются в бессмыслицу.
                Later "языковых файлов не скопировано ни одного — проверьте $targetDir"
            } else {
                Ok "языковых файлов скопировано: $copied ($flavour)"
            }
        } catch {
            Later "не удалось скопировать языки в $targetDir ($($_.Exception.Message)) — запустите установку от администратора или скопируйте файлы вручную"
        }

        $tesseract = Join-Path (Split-Path $targetDir -Parent) 'tesseract.exe'
        if (Test-Path $tesseract) {
            $languages = Invoke-Native $tesseract @('--list-langs')
            if ($languages -match '(?m)^rus$') { Ok 'tesseract видит русский язык' }
            else { Later 'tesseract не видит русский язык — проверьте каталог tessdata' }
        }
    }
}

# После тихой установки PATH текущего процесса ещё старый: свежепоставленный
# Git в нём не появится, пока не открыть новое окно. Перечитываем PATH из
# реестра, иначе разворачивание кода не увидит только что поставленный git.
function Update-PathFromRegistry {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user    = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($machine, $user) | Where-Object { $_ }) -join ';'
}
Update-PathFromRegistry

# ----------------------------------------------------------------- код -----
Step "Каталоги в $Target"
New-Dir $Target | Out-Null
$app = Join-Path $Target 'app'
$gitBundle = Join-Path $bundle 'code\reportgen.bundle'
$hasGit = [bool](Get-Command git -ErrorAction SilentlyContinue)

if ((Test-Path (Join-Path $app '.git')) -and $hasGit) {
    # Уже развёрнуто из бандла — обновляем через git, история сохраняется.
    Push-Location $app
    try {
        $null = Invoke-Native 'git' @('pull', $gitBundle)
        if ($script:NativeExit -eq 0) { Ok 'код обновлён из git-бандла, история сохранена' }
        else { Later 'git pull из бандла не прошёл — обновите код вручную' }
    } finally { Pop-Location }
} elseif ($hasGit -and (Test-Path $gitBundle) -and -not (Test-Path (Join-Path $app 'src'))) {
    # Клонируем из бандла: тогда следующее обновление ставится одной командой
    # git pull новый.bundle, а не копированием файлов поверх.
    $branch = 'main'
    $branchFile = Join-Path $bundle 'code\BRANCH.txt'
    if (Test-Path $branchFile) { $branch = (Get-Content $branchFile -Raw).Trim() }
    $null = Invoke-Native 'git' @('clone', '--branch', $branch, $gitBundle, $app)
    if ($script:NativeExit -eq 0) {
        Ok "код склонирован из git-бандла (ветка $branch) — обновления ставятся командой git pull"
    } else {
        Warn 'клонирование из бандла не удалось, разворачиваю копированием'
        New-Dir $app | Out-Null
        Copy-Item (Join-Path $bundle 'code\reportgen-src\*') $app -Recurse -Force
    }
} else {
    if (Test-Path (Join-Path $app 'src')) { Warn "код уже развёрнут в $app — обновляю файлы" } else { New-Dir $app | Out-Null }
    Copy-Item (Join-Path $bundle 'code\reportgen-src\*') $app -Recurse -Force
    if (-not $hasGit) { Later 'git не установлен: обновления придётся возить копированием, без истории' }
    Ok 'код развёрнут'
}

foreach ($name in 'models', 'llama', 'data', 'logs') { New-Dir (Join-Path $Target $name) | Out-Null }
foreach ($type in 'literature', 'standards', 'datasheets', 'reports', 'regulations') {
    New-Dir (Join-Path $Target "data\library\$type") | Out-Null
}

# ------------------------------------------------------------ llama.cpp ----
Step 'llama.cpp'
$llamaTarget = Join-Path $Target 'llama'
$llamaSource = Join-Path $bundle 'llama'
$archives = Get-ChildItem $llamaSource -Filter '*.zip' -ErrorAction SilentlyContinue

function Show-LlamaRecovery([string]$source, [string]$target) {
    # Сообщение «не найден» без списка того, что есть, чинить нечем.
    Note "в комплекте ($source) лежит:"
    $present = Get-ChildItem $source -File -ErrorAction SilentlyContinue
    if ($present) { foreach ($file in $present) { Note "  $($file.Name)" } }
    else { Note '  (пусто)' }
    Note ''
    Note 'Нужны ДВА архива: сборка сервера и библиотеки CUDA к ней.'
    Note 'Судя по именам выше, не хватает сборки сервера (llama-*-bin-win-cuda-*-x64.zip).'
    Note 'На машине С ИНТЕРНЕТОМ доберите только его, не перекачивая весь комплект:'
    Note '  .\pack.ps1 -Destination <тот же каталог комплекта> -Only llama'
    Note 'затем перенесите каталог llama и повторите установку.'
    Note "Либо скачайте архив вручную со страницы выпусков llama.cpp и положите в $source."
}

if (-not $archives) {
    Later "в комплекте нет архивов llama.cpp — модель запускать будет нечем"
    Show-LlamaRecovery $llamaSource $llamaTarget
} else {
    foreach ($archive in $archives) {
        Expand-Archive -Path $archive.FullName -DestinationPath $llamaTarget -Force
        Ok "распакован $($archive.Name)"
    }
    # В некоторых выпусках файлы лежат во вложенном каталоге build\bin.
    $nested = Get-ChildItem $llamaTarget -Recurse -Filter 'llama-server.exe' | Select-Object -First 1
    if ($nested -and $nested.DirectoryName -ne $llamaTarget) {
        # Без -Recurse подкаталоги сборки (например, с библиотеками CUDA)
        # молча не копируются, и сервер падает при запуске.
        Copy-Item (Join-Path $nested.DirectoryName '*') $llamaTarget -Recurse -Force
        Ok 'файлы подняты из вложенного каталога'
    }
    if (Test-Path (Join-Path $llamaTarget 'llama-server.exe')) {
        # Одного сервера мало: без библиотек CUDA рядом он не стартует вовсе,
        # и выяснится это при первом запуске, когда добрать их уже неоткуда.
        $cuda = @(Get-ChildItem $llamaTarget -Filter 'cudart*.dll' -ErrorAction SilentlyContinue) +
                @(Get-ChildItem $llamaTarget -Filter 'cublas*.dll' -ErrorAction SilentlyContinue)
        if (-not $cuda.Count) {
            Later 'рядом с llama-server.exe нет библиотек CUDA (cudart/cublas) — сервер модели не запустится'
        }
        Ok 'llama-server.exe на месте'
    } else {
        # Не обрываем установку: всё остальное — зависимости, настройки,
        # библиотека — поставится, и потом останется только доложить архив.
        Later "llama-server.exe не найден после распаковки в $llamaTarget — модель запускать будет нечем"
        Show-LlamaRecovery $llamaSource $llamaTarget
    }
    # Запасной выпуск не распаковываем: он пригодится, только если основной не
    # заведётся на этой видеокарте. Просто кладём рядом.
    $previous = Join-Path $bundle 'llama\previous'
    if (Test-Path $previous) {
        $keep = New-Dir (Join-Path $Target 'llama-previous')
        Copy-Item (Join-Path $previous '*') $keep -Force
        Note "запасной выпуск llama.cpp лежит в $keep — распакуйте, если основной не запустится"
    }
}

# --------------------------------------------------------------- модели ----
Step 'Модели'
$models = Get-ChildItem (Join-Path $bundle 'models') -Filter '*.gguf' -ErrorAction SilentlyContinue
if (-not $models) {
    Later "в комплекте нет моделей .gguf — положите их в $(Join-Path $Target 'models')"
} else {
    foreach ($model in $models) {
        Copy-Item $model.FullName (Join-Path $Target 'models') -Force
        Ok ("{0} ({1} ГБ)" -f $model.Name, [math]::Round($model.Length / 1GB, 1))
    }
}

# ---------------------------------------------------------- зависимости ----
Step 'Зависимости Python (только из комплекта, без сети)'
$venv = Join-Path $app '.venv'
if (-not (Test-Path $venv)) { & cmd /c "$python -m venv ""$venv""" }
$venvPython = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) { Fail "не создано окружение $venv" }
$wheels = Join-Path $bundle 'wheels'

$null = Invoke-Native $venvPython @('-m', 'pip', 'install', '--no-index',
                                    '--find-links', $wheels, '--upgrade',
                                    'pip', 'setuptools', 'wheel')
& $venvPython -m pip install --no-index --find-links $wheels -r (Join-Path $app 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'не удалось поставить зависимости из локальных колёс' }
$formats = Join-Path $app 'requirements-formats.txt'
if (Test-Path $formats) {
    & $venvPython -m pip install --no-index --find-links $wheels -r $formats
    if ($LASTEXITCODE -ne 0) {
        Later 'пакеты поддержки форматов не встали — презентации, Excel и RTF читаться не будут'
    } else {
        Ok 'поддержка презентаций, Excel и RTF установлена'
    }
}
# Набор тестов — единственная проверка установки, не требующая ни сети, ни
# модели. Ему нужен httpx: без него три модуля падают на импорте.
$dev = Join-Path $app 'requirements-dev.txt'
if (Test-Path $dev) {
    $null = Invoke-Native $venvPython @('-m', 'pip', 'install', '--no-index',
                                        '--find-links', $wheels, '-r', $dev)
    if ($script:NativeExit -ne 0) { Later 'пакеты для прогона тестов не встали — проверить установку тестами не выйдет' }
    else { Ok 'пакеты для прогона тестов установлены' }
}
# Проверяем ВСЁ, без чего приложение не поднимется. Половина списка тут
# отсутствовала, и установка рапортовала «пакеты на месте», а сервер потом
# падал на python-multipart — на машине, где pip идти некуда.
& $venvPython -c "import fastapi, uvicorn, docx, pymupdf, numpy, multipart, itsdangerous, jinja2; print('пакеты на месте')"
if ($LASTEXITCODE -ne 0) { Fail 'зависимости встали не полностью' }
# Пакет лежит в src/ и в окружение не устанавливается — путь к нему нужен
# здесь же, а не только ниже: без него проверка падала на ModuleNotFoundError
# и обрывала установку в самом конце.
$env:PYTHONPATH = Join-Path $app 'src'
& $venvPython -c "import reportgen.web.app as app; app.create_app; print('приложение собирается')"
if ($LASTEXITCODE -ne 0) { Fail 'приложение не собирается — установка не закончена' }
Ok 'зависимости установлены, сеть не использовалась'

# -------------------------------------------------------------- настройка --
Step 'Настройки'
$config = Join-Path $Target 'settings.json'
if (-not (Test-Path $config)) {
    $sample = Join-Path $app 'scripts\windows\settings.example.json'
    $text = Get-Content $sample -Raw -Encoding UTF8
    $text = $text -replace 'C:\\\\reportgen\\\\app', ($app -replace '\\', '\\')
    $text = $text -replace 'C:\\\\reportgen\\\\data', ((Join-Path $Target 'data') -replace '\\', '\\')
    Write-Utf8NoBom $config $text
    Ok "создан $config"
} else {
    Ok "уже есть: $config"
}

# Скрипты запуска ищут установку по REPORTGEN_HOME, а без неё смотрят в
# C:\reportgen. Установка в другой каталог без этой переменной выглядит
# успешной, а потом start-all.ps1 не находит ни моделей, ни настроек.
if ($Target.TrimEnd('\') -ne 'C:\reportgen') {
    $scope = 'Machine'
    try {
        [Environment]::SetEnvironmentVariable('REPORTGEN_HOME', $Target, $scope)
    } catch {
        $scope = 'User'
        [Environment]::SetEnvironmentVariable('REPORTGEN_HOME', $Target, $scope)
    }
    $env:REPORTGEN_HOME = $Target
    Ok "REPORTGEN_HOME = $Target (область $scope)"
    Note 'новое окно PowerShell подхватит переменную автоматически'
}

$env:PYTHONPATH = Join-Path $app 'src'
$env:REPORTGEN_CONFIG = $config

# ------------------------------------------------------- самопроверка -----
Step 'Проверка установки'
& $venvPython -m reportgen formats
if ($LASTEXITCODE -ne 0) { Later 'reportgen formats завершился с ошибкой' }

Step 'Администратор'
$users = Invoke-Native $venvPython @('-m', 'reportgen', 'users')
if ($script:NativeExit -ne 0 -or -not ($users | Where-Object { $_ -match '\S' })) {
    if ($Unattended) {
        # Это именно замечание, а не примечание: без администратора в систему
        # не войти никому, и «установка завершена без замечаний» было бы
        # неправдой ровно в том месте, где она важнее всего.
        Later ('администратор не заведён — войти в систему пока нельзя. Создайте его: ' +
               '"' + $venvPython + '" -m reportgen useradd --login admin --role owner')
    } else {
        Write-Host 'Создайте администратора (пароль не короче 8 символов):' -ForegroundColor Yellow
        $login = Read-Host 'Логин'
        if ($login) {
            & $venvPython -m reportgen useradd --login $login --role owner
            if ($LASTEXITCODE -ne 0) { Later 'администратор не заведён — войти в систему пока нельзя' }
        } else {
            Later 'администратор не заведён — войти в систему пока нельзя'
        }
    }
} else {
    Ok 'пользователи уже заведены'
}

# ------------------------------------------------------------------ итог ---
Write-Host ''
if ($script:Warnings.Count) {
    Write-Host 'Установка завершена, но с замечаниями:' -ForegroundColor Yellow
    foreach ($item in $script:Warnings) { Write-Host "  * $item" -ForegroundColor Yellow }
    Write-Host ''
} else {
    Write-Host 'Установка завершена без замечаний.' -ForegroundColor Green
}
Write-Host 'Дальше:' -ForegroundColor Green
Write-Host "  1) отключите резервную системную память CUDA в панели NVIDIA (docs\11-windows.md, шаг 0)"
Write-Host "  2) cd $app\scripts\windows"
Write-Host '  3) .\00-check.ps1   — проверка машины'
Write-Host '  4) .\start-all.ps1  — запуск комплекса'
Write-Host '  5) сложите документы в ' -NoNewline; Write-Host (Join-Path $Target 'data\library') -ForegroundColor Cyan
Write-Host '     и выполните: ' -NoNewline; Write-Host '.\load-library.ps1' -ForegroundColor Cyan
Write-Host '     Раскладывать по папкам необязательно: тип определится по документу.'
Write-Host ''
Write-Host 'Полный путь от начала до конца — docs\00-start.md' -ForegroundColor Green
