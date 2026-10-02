"""Скачать действующую редакцию Рекомендации МСЭ-R (английский PDF). itu_get.py <папка> BO.1408 S.1709 ..."""
import re, subprocess, sys, os, json
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
dst = sys.argv[1]; os.makedirs(dst, exist_ok=True)
for rec in sys.argv[2:]:
    ser = rec.split('.')[0].lower()
    page = subprocess.run(['curl','-sL','-A',UA,'-m','90',f'https://www.itu.int/rec/R-REC-{rec}/en'],capture_output=True).stdout.decode('latin1')
    ids = sorted(set(re.findall(r'R-REC-'+re.escape(rec)+r'-\d+-\d{6}-[IS]', page)), key=lambda s:int(s.split('-')[3]))
    if not ids: print('НЕТ', rec); continue
    rid = ids[-1]
    url = f'https://www.itu.int/dms_pubrec/itu-r/rec/{ser}/{rid}!!PDF-E.pdf'
    fn = os.path.join(dst, rid + '.pdf')
    subprocess.run(['curl','-sL','-A',UA,'-m','300','-o',fn,url])
    ok = open(fn,'rb').read(5) == b'%PDF-'
    if not ok:  # иногда только ZIP/Word
        url = url.replace('!!PDF-E.pdf','!!ZPF-E.zip'); os.remove(fn)
        fn = fn[:-4]+'.zip'; subprocess.run(['curl','-sL','-A',UA,'-m','300','-o',fn,url])
    print(rec, rid, os.path.getsize(fn), fn)
    open(os.path.join(dst,'_itu.jsonl'),'a').write(json.dumps({'rec':rec,'id':rid,'url':url,'file':os.path.basename(fn)})+'\n')
