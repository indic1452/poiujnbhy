"""Общее для сценариев сверки: разбор alist."""


def alist_pary(put):
    """Формат alist: n m; наибольшие веса; веса столбцов; веса строк; номера строк по столбцам
    (дополненные нулями до наибольшего веса или ровно по весу)."""
    ch = [int(x) for x in open(put).read().split()]
    n, m, mv, mr = ch[0], ch[1], ch[2], ch[3]
    vesa = ch[4:4 + n]
    ost = ch[4 + n + m:]
    dopolneny = len(ost) >= n * mv + m * mr
    pary, mesto = set(), 0
    for c in range(n):
        shir = mv if dopolneny else vesa[c]
        for x in ost[mesto:mesto + shir]:
            if x:
                pary.add((x - 1, c))
        mesto += shir
    return n, m, pary
