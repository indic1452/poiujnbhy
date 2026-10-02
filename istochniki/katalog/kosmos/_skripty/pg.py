"""pg.py файл.txt стр [начало] [длина] — нормализованный текст страницы PDF."""
import re, sys
t = open(sys.argv[1], encoding='utf8', errors='replace').read()
s = re.split(r'=====PAGE (\d+)=====\n', t)
d = {int(s[i]): ' '.join(s[i + 1].split()) for i in range(1, len(s), 2)}
a = int(sys.argv[3]) if len(sys.argv) > 3 else 0
L = int(sys.argv[4]) if len(sys.argv) > 4 else 3000
for p in sys.argv[2].split(','):
    print('==', p, d[int(p)][a:a + L])
