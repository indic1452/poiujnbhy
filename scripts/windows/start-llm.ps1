<#
.SYNOPSIS
    Запуск основной языковой модели на GPU (llama.cpp server, порт 8000).
.PARAMETER Model
    Имя файла .gguf в каталоге моделей. Если не указано — берётся единственный
    подходящий файл или тот, что задан в settings.json (llm_model_file).
.PARAMETER Context
    Размер контекста в токенах. 16384 хватает для посекционной генерации.
.PARAMETER CpuMoe
    Сколько слоёв экспертов MoE держать в оперативной памяти. Для плотных
    (не-MoE) моделей оставьте 0. Для MoE подбирайте так, чтобы VRAM была
    занята примерно на 14.5 из 16 ГБ.
.PARAMETER Threads
    Число потоков CPU (по числу физических ядер).
#>
param(
    [string]$Model   = '',
    [int]$Context    = 16384,
    [int]$CpuMoe     = 0,
    [int]$Threads    = 14,
    [int]$Port       = 8000,
    [int]$Parallel   = 2
)

. "$PSScriptRoot\_common.ps1"

$exe = Get-LlamaServer

if (-not $Model) {
    $Model = Get-Setting 'llm_model_file' ''
}
if (-not $Model) {
    $candidates = Get-ChildItem $script:Models -Filter *.gguf |
        Where-Object { $_.Name -notmatch 'bge|rerank|embed' } |
        Sort-Object Length -Descending
    if (-not $candidates) { throw "в $script:Models нет ни одной модели .gguf" }
    $Model = $candidates[0].Name
    Write-Warn2 "модель не указана, беру $Model"
}
$path = if (Test-Path $Model) { $Model } else { Join-Path $script:Models $Model }
if (-not (Test-Path $path)) { throw "не найден файл модели: $path" }

if (-not (Test-Port $Port)) { throw "порт $Port уже занят — сервер запущен? Остановите его: .\stop-all.ps1" }

$help = Get-LlamaHelp

$arguments = @(
    '-m', $path,
    '--host', '127.0.0.1', '--port', $Port,
    '-c', $Context,
    '-ngl', '999',                       # все слои на GPU, что не влезет — уйдёт в ОЗУ
    '--cache-type-k', 'q8_0',            # квантованный KV-кэш: вдвое меньше памяти под контекст
    '--cache-type-v', 'q8_0',
    '-t', $Threads,
    '--parallel', $Parallel,
    '--cont-batching',
    '--alias', 'local',
    '--log-file', (Join-Path $script:Logs 'llm.log')
)

# В свежих сборках --flash-attn принимает значение on/off/auto, в старых это ключ без значения.
if ($help -match '--flash-attn\s*\[?\s*on') {
    $arguments += @('--flash-attn', 'on')
} elseif ($help -match '--flash-attn') {
    $arguments += '--flash-attn'
} else {
    Write-Warn2 'сборка без flash-attention — будет медленнее и займёт больше памяти'
}

if ($CpuMoe -gt 0) {
    if ($help -match '--n-cpu-moe') {
        $arguments += @('--n-cpu-moe', $CpuMoe)
    } elseif ($help -match '--override-tensor') {
        # Старый способ выгрузить экспертов MoE в оперативную память.
        Write-Warn2 'сборка без --n-cpu-moe, использую --override-tensor (выгружаются все эксперты)'
        $arguments += @('--override-tensor', 'ffn_(up|down|gate)_exps=CPU')
    } else {
        Write-Warn2 'сборка не умеет выгружать эксперты MoE — обновите llama.cpp'
    }
}

Write-Step "Запуск модели: $(Split-Path $path -Leaf)"
Write-Host ($exe + ' ' + ($arguments -join ' ')) -ForegroundColor DarkGray
& $exe @arguments
