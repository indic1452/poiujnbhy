"""Запуск всей независимой сверки (второй проход) и запись итогов в sverka.json в корне области."""
import importlib, json, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from obsh import KOREN
import validaciya
R = []
for m in ("p_dvb", "p_vesch", "p_radio", "p_besprov", "p_dop", "p_novye"):
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), m + ".py")):
        R += [dict(r, модуль=m) for r in importlib.import_module(m).R]
k, osh = validaciya.main()
imena = {z["имя"] for z in k}
sv = collections.OrderedDict()
for r in R:
    assert r["запись"] in imena, r["запись"]
    sv.setdefault(r["запись"], []).append({x: r[x] for x in ("что сверено", "источник", "метод", "результат", "модуль")})
itog = {"записей в каталоге": len(k), "записей сверено": len(sv), "доля": round(len(sv) / len(k), 3), "проверок": len(R),
        "результаты": collections.Counter(r["результат"] for r in R), "замечаний валидации": osh, "сверка": sv}
json.dump(itog, open(os.path.join(KOREN, "sverka.json"), "w"), ensure_ascii=False, indent=1)
print(json.dumps({x: itog[x] for x in ("записей в каталоге", "записей сверено", "доля", "проверок", "результаты")}, ensure_ascii=False))
print("\n".join(osh))
