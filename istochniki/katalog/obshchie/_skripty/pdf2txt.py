"""PDF -> .txt с разметкой страниц '=====PAGE n=====' (номер физической страницы PDF, с 1)."""
import sys, pymupdf
for p in sys.argv[1:]:
    d = pymupdf.open(p)
    with open(p + '.txt', 'w') as f:
        for i, pg in enumerate(d):
            f.write(f'\n=====PAGE {i+1}=====\n')
            f.write(pg.get_text())
    print(p, len(d))
