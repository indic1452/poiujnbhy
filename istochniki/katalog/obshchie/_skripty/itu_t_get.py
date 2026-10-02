"""Скачать редакции Рекомендаций МСЭ-T (английский PDF). itu_t_get.py <подпапка> V.22bis O.150 ...
Запись в istochniki/<подпапка>/_spisok.txt (как dl.sh). Берётся самая новая действующая редакция (-I) по странице itu.int/rec."""
import re, subprocess, sys, os
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
K = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'istochniki')
sub = sys.argv[1]; dst = os.path.join(K, sub); os.makedirs(dst, exist_ok=True)
for rec in sys.argv[2:]:
    want = None
    if '=' in rec: rec, want = rec.split('=')
    page = subprocess.run(['curl', '-sL', '-A', UA, '-m', '90', f'https://www.itu.int/rec/T-REC-{rec}/en'], capture_output=True).stdout.decode('latin1')
    ids = sorted(set(re.findall(r'T-REC-' + re.escape(rec) + r'-\d{6}-[IST]', page)), key=lambda s: s.split('-')[-2])
    if want: ids = [i for i in ids if want in i]
    if not ids: print('НЕТ', rec); continue
    rid = ids[-1]
    url = f'https://www.itu.int/rec/dologin_pub.asp?lang=e&id={rid}!!PDF-E&type=items'
    fn = os.path.join(dst, rid + '.pdf')
    subprocess.run(['curl', '-sL', '-A', UA, '-m', '300', '-o', fn, url])
    if open(fn, 'rb').read(5) != b'%PDF-':
        print('не PDF', rec, rid); os.remove(fn); continue
    line = f'{sub}/{rid}.pdf {os.path.getsize(fn)} PDF {url}'
    print(line); open(os.path.join(K, sub.split('/')[0], '_spisok.txt'), 'a').write(line + '\n')
