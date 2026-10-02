"""Прогон всех независимых проверок области obshchie → rezultaty.json (вывод каждого p_*.py как JSON)."""
import glob, json, os, subprocess, sys, time
D = os.path.dirname(os.path.abspath(__file__))
rez = {}
for f in sorted(glob.glob(os.path.join(D, 'p_*.py'))):
    t = time.time()
    r = subprocess.run([sys.executable, f], capture_output=True, text=True, cwd=D, timeout=3600)
    out = r.stdout[r.stdout.index('{'):] if '{' in r.stdout else r.stdout
    try: val = json.loads(out)
    except Exception: val = {'вывод': r.stdout[-2000:], 'ошибка': r.stderr[-2000:]}
    rez[os.path.basename(f)] = dict(код_выхода=r.returncode, секунд=round(time.time() - t, 1), результат=val)
    print(os.path.basename(f), r.returncode, round(time.time() - t, 1), flush=True)
json.dump(rez, open(os.path.join(D, 'rezultaty.json'), 'w'), ensure_ascii=False, indent=1)
