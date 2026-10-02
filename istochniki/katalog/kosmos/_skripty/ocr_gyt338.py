"""OCR приложений D и E GY/T 338-2020 (стр. PDF 43–62: адреса накопителей LDPC) → istochniki/kitai/GY_T_338-2020_prilDE_ocr.txt
(tesseract eng, только цифры; разметка '=====PAGE n=====' — номер страницы PDF)."""
import os, subprocess, tempfile, pymupdf
KOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
p = os.path.join(KOR, 'istochniki', 'kitai', 'GY_T_338-2020.pdf')
d = pymupdf.open(p)
with open(os.path.join(KOR, 'istochniki', 'kitai', 'GY_T_338-2020_prilDE_ocr.txt'), 'w') as f, tempfile.TemporaryDirectory() as td:
    for i in range(42, 62):
        png = os.path.join(td, 'p.png')
        d[i].get_pixmap(dpi=200).save(png)
        t = subprocess.run(['tesseract', png, '-', '-l', 'eng', '--psm', '6', '-c', 'tessedit_char_whitelist=0123456789 /.()='], capture_output=True, text=True, timeout=240, env=dict(os.environ, OMP_THREAD_LIMIT='1')).stdout
        f.write('\n=====PAGE %d=====\n%s' % (i + 1, t))
        print(i + 1, len(t.split()), flush=True); f.flush()
