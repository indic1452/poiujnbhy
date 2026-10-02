#!/bin/bash
# dl.sh <подпапка> <имя файла> <url> — скачивание во временный файл; заменяет цель только годным ответом
# (не пустым, не страницей ошибки/капчи); запись в istochniki/<подпапка>/_spisok.txt
K=/tmp/claude-0/-home-user-poiujnbhy/da6b639e-7ecd-5b07-9c36-bf7a3fd312ff/scratchpad/katalog/obshchie/istochniki
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
mkdir -p "$(dirname "$K/$1/$2")"
T=$(mktemp)
curl -sL -A "$UA" -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8' -H 'Accept-Language: en-US,en;q=0.9' -m 600 -o "$T" "$3"
sz=$(stat -c%s "$T" 2>/dev/null || echo 0)
bad=""
[ "$sz" -lt 200 ] && bad="пусто/коротко"
grep -qi "We're sorry\|404 Not Found\|Enable JavaScript and cookies\|Blocked by egress\|Access Denied" "$T" 2>/dev/null && [ "$sz" -lt 20000 ] && bad="страница ошибки"
case "$2" in *.pdf) head -c 5 "$T" | grep -q '%PDF-' || bad="не PDF";; esac
if [ -n "$bad" ]; then echo "ОШИБКА $1/$2 ($bad) $3"; rm -f "$T"; exit 1; fi
mv "$T" "$K/$1/$2"; chmod 644 "$K/$1/$2"
echo "$1/$2 $sz $(file -b "$K/$1/$2" | cut -c1-25 | tr ' ' '_') $3" | tee -a "$K/${1%%/*}/_spisok.txt"
