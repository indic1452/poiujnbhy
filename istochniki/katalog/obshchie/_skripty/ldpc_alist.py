"""Чтение alist (формат MacKay) с проверками и ранг над GF(2)."""
import gzip


def read_alist(path):
    raw = gzip.open(path, 'rt').read() if path.endswith('.gz') else open(path).read()
    raw = "\n".join(l.split("#")[0] for l in raw.split("\n"))  # комментарии (#) — у части файлов AFF3CT
    nums = [int(x) for x in raw.split()]
    N, M = nums[0], nums[1]; bn, bm = nums[2], nums[3]
    p = 4
    cw = nums[p:p + N]; p += N
    rw = nums[p:p + M]; p += M
    p0 = p
    for vid in ('с дополнением нулями', 'без дополнения'):
        p = p0; cols = []; rows = []
        for n in range(N):
            k = bn if vid == 'с дополнением нулями' else cw[n]
            cols.append(nums[p:p + cw[n]]); p += k   # сверх веса — заполнитель, игнорируется (mackay_alist.html)
        for mm in range(M):
            k = bm if vid == 'с дополнением нулями' else rw[mm]
            rows.append(nums[p:p + rw[mm]]); p += k
        if p == len(nums) and all(all(x > 0 for x in c) for c in cols) and all(all(x > 0 for x in r) for r in rows): break
    else:
        raise AssertionError(path)
    tr = [[] for _ in range(M)]
    for n, c in enumerate(cols):
        for m in c: tr[m - 1].append(n + 1)
    assert [sorted(r) for r in tr] == [sorted(r) for r in rows], path
    return N, M, cw, rw, rows, vid


def rank(rows, N):
    piv = {}
    r = 0
    for row in rows:
        v = 0
        for n in row: v |= 1 << (n - 1)
        while v:
            h = v.bit_length() - 1
            if h in piv: v ^= piv[h]
            else: piv[h] = v; r += 1; break
    return r


