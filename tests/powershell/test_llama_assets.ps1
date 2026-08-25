# Подбор архивов llama.cpp по именам файлов выпуска.
#
# У заказчика в комплект попал только cudart, сборка сервера — нет, и установка
# на изолированной машине встала на «llama-server.exe не найден». Имена архивов
# менялись между выпусками (bin-win-cuda-12.4-x64.zip, bin-win-cu12.4-x64.zip),
# а выпуск без сборки сервера обязан отвергаться целиком, а не давать половину.
#
# Запускается из tests/test_offline.py, если в системе есть pwsh.
# Код возврата: 0 — все случаи разобраны верно, 1 — есть расхождения.

$ErrorActionPreference = 'Stop'
# Вытаскиваем две функции подбора из pack.ps1 и проверяем на реальных наборах имён.
$text = Get-Content (Join-Path $PSScriptRoot '..\..\scripts\offline\pack.ps1') -Raw
foreach ($name in 'Get-LlamaAssetRules', 'Select-LlamaAssets') {
    $start = $text.IndexOf("function $name")
    $depth = 0; $i = $text.IndexOf('{', $start); $j = $i
    while ($true) {
        if ($text[$j] -eq '{') { $depth++ }
        elseif ($text[$j] -eq '}') { $depth--; if ($depth -eq 0) { break } }
        $j++
    }
    Invoke-Expression $text.Substring($start, $j - $start + 1)
}

$plan = Get-Content (Join-Path $PSScriptRoot '..\..\scripts\offline\bundle.example.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$rules = Get-LlamaAssetRules $plan
Write-Host "правил: $($rules.Count) — $(($rules | ForEach-Object { $_.id }) -join ' | ')"

$script:Failures = 0

function Check($label, $names, $expectServer, $expectCudart) {
    $release = [pscustomobject]@{ assets = @($names | ForEach-Object { [pscustomobject]@{ name = $_ } }) }
    $found = Select-LlamaAssets $release $rules
    if (-not $found) { Write-Host "  $label -> НЕ ПОДОБРАНО" -ForegroundColor Red; $script:Failures++; return }
    $server = $found[0].name; $cudart = $found[1].name
    $ok = ($server -eq $expectServer) -and ($cudart -eq $expectCudart)
    $color = if ($ok) { 'Green' } else { 'Red' }
    Write-Host "  $label -> сервер=$server cudart=$cudart" -ForegroundColor $color
    if (-not $ok) { Write-Host "     ожидалось сервер=$expectServer cudart=$expectCudart" -ForegroundColor Red; $script:Failures++ }
}

Write-Host 'современные имена:'
Check 'cuda-12.4' @(
  'llama-b7654-bin-win-cuda-12.4-x64.zip',
  'cudart-llama-bin-win-cuda-12.4-x64.zip',
  'llama-b7654-bin-win-cpu-x64.zip',
  'llama-b7654-bin-ubuntu-x64.zip'
) 'llama-b7654-bin-win-cuda-12.4-x64.zip' 'cudart-llama-bin-win-cuda-12.4-x64.zip'

Write-Host 'старые имена (cu вместо cuda):'
Check 'cu12.4' @(
  'llama-b4589-bin-win-cu12.4-x64.zip',
  'cudart-llama-bin-win-cu12.4-x64.zip',
  'llama-b4589-bin-win-avx2-x64.zip'
) 'llama-b4589-bin-win-cu12.4-x64.zip' 'cudart-llama-bin-win-cu12.4-x64.zip'

Write-Host 'выпуск без сборок под Windows (должен быть отвергнут целиком):'
$release = [pscustomobject]@{ assets = @([pscustomobject]@{ name = 'source-code.tar.gz' }) }
$r = Select-LlamaAssets $release $rules
Write-Host "  результат: $(if ($null -eq $r) { 'отвергнут — верно' } else { 'ПОДОБРАН — ОШИБКА' })" -ForegroundColor $(if ($null -eq $r) { 'Green' } else { 'Red' })
if ($null -ne $r) { $script:Failures++ }

Write-Host 'есть только cudart (случай заказчика — выпуск не должен подойти):'
$release = [pscustomobject]@{ assets = @([pscustomobject]@{ name = 'cudart-llama-bin-win-cuda-12.4-x64.zip' }) }
$r = Select-LlamaAssets $release $rules
Write-Host "  результат: $(if ($null -eq $r) { 'отвергнут — верно' } else { 'ПОДОБРАН — ОШИБКА' })" -ForegroundColor $(if ($null -eq $r) { 'Green' } else { 'Red' })
if ($null -ne $r) { $script:Failures++ }

Write-Host ''
if ($script:Failures) { Write-Host "расхождений: $script:Failures" -ForegroundColor Red; exit 1 }
Write-Host 'подбор архивов llama.cpp верен' -ForegroundColor Green
exit 0
