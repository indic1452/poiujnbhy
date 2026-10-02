#!/bin/bash
# dl.sh <подпапка> <имя файла> <url> — скачивание с заголовками браузера, запись в _spisok.txt
K=/tmp/claude-0/-home-user-poiujnbhy/da6b639e-7ecd-5b07-9c36-bf7a3fd312ff/scratchpad/katalog/kosmos/istochniki
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
mkdir -p "$K/$1"
curl -sL -A "$UA" -H 'Accept: text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8' -H 'Accept-Language: en-US,en;q=0.9' -m 400 -o "$K/$1/$2" "$3"
echo "$1/$2 $(stat -c%s "$K/$1/$2" 2>/dev/null) $(file -b "$K/$1/$2" 2>/dev/null | cut -c1-25) $3" | tee -a "$K/${1%%/*}/_spisok.txt"
