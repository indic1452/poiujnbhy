# Разбор страниц МСЭ-Т в качалке scripts/offline/itu.ps1.
#
# Сеть при разработке была закрыта, поэтому разбор проверяется на сохранённых
# образцах страниц. Проверяется главное: из перечня серии вынимаются номера,
# названия и состояние; со страницы рекомендации — ссылка на PDF; ссылка на
# чужую серию не подхватывается; заменённая редакция опознаётся как заменённая
# (иначе отчёт сошлётся на отменённую норму); повтор считается один раз.
#
# Запускается из tests/test_offline.py, если в системе есть pwsh.
# Код возврата: 0 — всё разобрано верно, 1 — есть расхождения.

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# Вытаскиваем разбирающие функции из скрипта, не запуская его целиком.
$text = Get-Content (Join-Path $PSScriptRoot '..\..\scripts\offline\itu.ps1') -Raw -Encoding UTF8
foreach ($name in 'Test-ItuLooksLikeName', 'Read-ItuIndex', 'Read-ItuPdfLink', 'ConvertFrom-HtmlText',
                  'Resolve-ItuUrl', 'Read-ItuBaseHref', 'Get-ItuFileName', 'Get-ItuSeries') {
    $start = $text.IndexOf("function $name")
    if ($start -lt 0) { Write-Host "функция $name не найдена" -ForegroundColor Red; exit 1 }
    $depth = 0; $j = $text.IndexOf('{', $start)
    while ($true) {
        if ($text[$j] -eq '{') { $depth++ }
        elseif ($text[$j] -eq '}') { $depth--; if ($depth -eq 0) { break } }
        $j++
    }
    Invoke-Expression $text.Substring($start, $j - $start + 1)
}
Add-Type -AssemblyName System.Web -ErrorAction SilentlyContinue

$script:Failures = 0
function Check($label, $actual, $expected) {
    $ok = "$actual" -eq "$expected"
    $color = if ($ok) { 'Green' } else { 'Red' }
    Write-Host ("  {0,-58} {1}" -f $label, $actual) -ForegroundColor $color
    if (-not $ok) { Write-Host "     ожидалось: $expected" -ForegroundColor Red; $script:Failures++ }
}

$fixtures = Join-Path $PSScriptRoot 'itu'
$indexHtml = Get-Content (Join-Path $fixtures 'G.html') -Raw -Encoding UTF8
$entries = Read-ItuIndex -Html $indexHtml -SeriesLetter 'G'
$byId = @{}; foreach ($e in $entries) { $byId[$e.id] = $e }

Write-Host 'Перечень серии:'
Check 'рекомендаций опознано (повтор — один раз)' $entries.Count 6
Check 'чужая серия не подхвачена' ($entries | Where-Object { $_.series -ne 'G' }).Count 0
Check 'номер с точкой не потерян' $byId.Contains('G.711.1') 'True'
Check 'название взято из соседней ячейки' $byId['G.703'].title `
      'Physical/electrical characteristics of hierarchical digital interfaces'
Check 'название, когда в ссылке лежит имя рекомендации' $byId['G.722'].title `
      '7 kHz audio-coding within 64 kbit/s'

Write-Host 'Имя рекомендации против её названия:'
Check 'голый номер — не название' (Test-ItuLooksLikeName 'G.722' 'G.722') 'True'
Check 'номер с приставками — не название' (Test-ItuLooksLikeName 'Recommendation ITU-T G.722' 'G.722') 'True'
Check 'настоящее название' (Test-ItuLooksLikeName '7 kHz audio-coding within 64 kbit/s' 'G.722') 'False'
Check 'название, начинающееся с номера, не отбрасывается' `
      (Test-ItuLooksLikeName 'G.722 wideband audio coding' 'G.722') 'False'

Write-Host 'Состояние:'
Check 'действующая' $byId['G.703'].status 'current'
Check 'заменённая, ссылка абсолютная' $byId['G.721'].status 'superseded'
Check 'заменённая, ссылка относительная' $byId['G.726'].status 'superseded'
Check 'отменённая (Withdrawn)' $byId['G.722'].status 'archived'

Write-Host 'Ссылка на PDF:'
$pdf703 = Read-ItuPdfLink -Html (Get-Content (Join-Path $fixtures 'G.703.html') -Raw -Encoding UTF8)
Check 'через dologin_pub' ($pdf703 -like '*dologin_pub.asp*id=T-REC-G.703-201604-I!!PDF-E*') 'True'
Check 'взят PDF, а не Word' ($pdf703 -notlike '*SOFT-E*') 'True'
$pdf711 = Read-ItuPdfLink -Html (Get-Content (Join-Path $fixtures 'G.711.html') -Raw -Encoding UTF8)
Check 'прямая ссылка на .pdf' ($pdf711 -like '*T-REC-G.711-198811-I.pdf') 'True'
$pdf722 = Read-ItuPdfLink -Html (Get-Content (Join-Path $fixtures 'G.722.html') -Raw -Encoding UTF8)
Check 'ссылки нет — пустая строка, а не выдумка' "[$pdf722]" '[]'

Write-Host 'Адреса и имена:'
Check 'относительный адрес развёрнут' (Resolve-ItuUrl '/rec/T-REC-G.703/en' 'https://www.itu.int') `
      'https://www.itu.int/rec/T-REC-G.703/en'

# Ниже — та самая ошибка, из-за которой выгрузка не скачала ни одного
# документа: ссылку «../recommendation.asp» прежний разбор приклеивал к корню
# сайта, теряя «/rec/», и МСЭ отвечал 403 на несуществующий путь.
Check 'ссылка с «../» считается от страницы, а не от корня' `
      (Resolve-ItuUrl '../recommendation.asp?lang=en&parent=T-REC-A.1' 'https://www.itu.int/rec/T-REC-A/en') `
      'https://www.itu.int/rec/recommendation.asp?lang=en&parent=T-REC-A.1'
Check 'ссылка без косой считается от каталога страницы' `
      (Resolve-ItuUrl 'recommendation.asp?parent=T-REC-A.1' 'https://www.itu.int/rec/T-REC-A/en') `
      'https://www.itu.int/rec/T-REC-A/recommendation.asp?parent=T-REC-A.1'
Check 'ссылка с «./» не теряет каталог' `
      (Resolve-ItuUrl './dologin_pub.asp?id=1' 'https://www.itu.int/rec/recommendation.asp') `
      'https://www.itu.int/rec/dologin_pub.asp?id=1'
Check 'без указания страницы адрес не выдумывается' `
      (Resolve-ItuUrl 'https://www.itu.int/x' '') 'https://www.itu.int/x'

Write-Host 'Собственная основа страницы (<base href>):'
Check 'основа объявлена — считаем от неё' `
      (Read-ItuBaseHref '<html><head><base href="/rec/"></head></html>' 'https://www.itu.int/rec/T-REC-A/en') `
      'https://www.itu.int/rec/'
Check 'основы нет — считаем от самой страницы' `
      (Read-ItuBaseHref '<html><head></head></html>' 'https://www.itu.int/rec/T-REC-A/en') `
      'https://www.itu.int/rec/T-REC-A/en'
Check 'ссылка разворачивается от объявленной основы' `
      (Resolve-ItuUrl 'recommendation.asp?parent=T-REC-A.1' `
        (Read-ItuBaseHref '<base href="/rec/">' 'https://www.itu.int/rec/T-REC-A/en')) `
      'https://www.itu.int/rec/recommendation.asp?parent=T-REC-A.1'
Check 'полный адрес не тронут' (Resolve-ItuUrl 'https://www.itu.int/x' 'https://www.itu.int') `
      'https://www.itu.int/x'
Check 'амперсанд в ссылке раскодирован' `
      ((Resolve-ItuUrl '/rec/dologin_pub.asp?lang=e&amp;id=T-REC-G.703' 'https://www.itu.int') -like '*&id=*') 'True'
Check 'имя файла' (Get-ItuFileName 'G.703') 'T-REC-G.703.pdf'
Check 'серий МСЭ-Т' (Get-ItuSeries).Count 25

Write-Host ''
if ($script:Failures) {
    Write-Host "расхождений: $script:Failures" -ForegroundColor Red
    exit 1
}
Write-Host 'разбор страниц МСЭ-Т — без расхождений' -ForegroundColor Green
exit 0
