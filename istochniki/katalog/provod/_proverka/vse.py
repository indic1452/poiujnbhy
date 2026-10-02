"""Прогон всех сверок проверяющего: журнал в proverka/zhurnal.json, найденные ошибки — proverka/ispravleniya.json."""
import runpy, sys, os, json, traceback
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pv
SEM = ["p_ethernet", "p_otn", "p_pon", "p_dsl", "p_kabel", "p_plc", "p_nositeli", "p_shtrihkody", "p_interfeisy", "dopolnenie", "p_istochniki"]
padeniya = []
for s in SEM:
    n0 = len(pv.ZHURNAL)
    try:
        runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), s + ".py"), run_name=s)
    except Exception as e:
        padeniya.append((s, traceback.format_exc()))
    print(s, len(pv.ZHURNAL) - n0, "проверок")
d = os.path.dirname(os.path.abspath(__file__))
pv.sohranit(os.path.join(d, "zhurnal.json"))
json.dump(pv.ISPR, open(os.path.join(d, "ispravleniya.json"), "w"), ensure_ascii=False, indent=1)
for z in pv.ZHURNAL:
    if z["итог"] != "совпало": print("!!", z["запись"][:50], "|", z["проверка"][:90], "|", z["итог"], "|", z["подробности"][:200])
for s, tb in padeniya: print("ПАДЕНИЕ", s, tb[-1500:])
print("записей проверено:", len({z["запись"] for z in pv.ZHURNAL}), "проверок:", len(pv.ZHURNAL), "расхождений:", sum(z["итог"] == "РАСХОЖДЕНИЕ" for z in pv.ZHURNAL))
