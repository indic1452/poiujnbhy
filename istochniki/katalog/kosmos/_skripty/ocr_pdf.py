"""OCR скана PDF → .txt с разметкой '=====PAGE n=====' (tesseract, язык по умолчанию eng). ocr_pdf.py файл.pdf [язык] [dpi]"""
import sys, subprocess, pymupdf, os, tempfile
p = sys.argv[1]; lang = sys.argv[2] if len(sys.argv) > 2 else 'eng'; dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200
d = pymupdf.open(p)
with open(p + '.txt', 'w') as f, tempfile.TemporaryDirectory() as td:
    for i, pg in enumerate(d):
        png = os.path.join(td, 'p.png'); pg.get_pixmap(dpi=dpi).save(png)
        t = subprocess.run(['tesseract', png, '-', '-l', lang, '--psm', '6'], capture_output=True, text=True).stdout
        f.write(f'\n=====PAGE {i+1}=====\n' + t)
print(p, len(d))
