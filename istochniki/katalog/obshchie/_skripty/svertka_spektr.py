"""Спектр расстояний свёрточного кода скорости 1/n (без обратной связи), в т. ч. выколотого.

dfree, A_d (число путей веса d) и B_d (суммарный вес входа) — по решётке: пути, отходящие от нулевого
состояния в момент 0 и впервые возвращающиеся в него. Для выколотого кода с периодом P — отход в каждой
из P фаз, спектр усредняется по фазам (как у Cain–Clark–Geist / Yasuda / Hagenauer).
"""
import numpy as np


def _outs(gens, K):
    S = 1 << (K - 1)
    st = np.arange(S)
    res = {}
    for u in (0, 1):
        reg = (u << (K - 1)) | st          # старший бит — текущий вход
        o = [np.array([bin(int(r) & g).count('1') & 1 for r in reg]) for g in gens]
        res[u] = (np.stack(o, 1), reg >> 1)
    return res


def spektr(gens, K, W=None, punct=None, maxlen=None, terms=5):
    """gens — целые (старший бит = текущий вход, K бит); punct — матрица n×P из 0/1 (None — без выкалывания).
    Возвращает dict(dfree, A=[...], B=[...]) для весов dfree…dfree+len−1 (усреднение по фазам при punct)."""
    n = len(gens); S = 1 << (K - 1)
    tr = _outs(gens, K)
    P = 1 if punct is None else len(punct[0])
    pm = np.ones((n, P), int) if punct is None else np.array(punct)
    if W is None:
        for W in (2 * K + 6 + terms, 4 * K + 10 + terms, 8 * K + 20 + terms):
            r = spektr(gens, K, W, punct, maxlen, terms)
            if r and r['dfree'] + terms - 1 <= W: return r
        return r
    if maxlen is None: maxlen = 400 * K
    totA = np.zeros(W + 1); totB = np.zeros(W + 1)
    for ph in range(P):
        # cnt[w][s], inw[w][s]
        A = np.zeros((W + 1, S)); B = np.zeros((W + 1, S))
        o, ns = tr[1]
        w0 = int((o[0] * pm[:, ph]).sum())
        if w0 <= W:
            A[w0, ns[0]] += 1; B[w0, ns[0]] += 1
        t = 1
        while A.any() and t < maxlen:
            nA = np.zeros_like(A); nB = np.zeros_like(B)
            col = pm[:, (ph + t) % P]
            for u in (0, 1):
                o, ns = tr[u]
                wo = (o * col).sum(1)
                for dw in np.unique(wo):
                    msk = wo == dw
                    src = np.nonzero(msk)[0]
                    if dw > W: continue
                    np.add.at(nA[dw:], (slice(None), ns[src]), A[:W + 1 - dw][:, src])
                    np.add.at(nB[dw:], (slice(None), ns[src]), B[:W + 1 - dw][:, src] + u * A[:W + 1 - dw][:, src])
            # слившиеся в 0 — считаем и убираем
            totA += nA[:, 0]; totB += nB[:, 0]
            nA[:, 0] = 0; nB[:, 0] = 0
            A, B = nA, nB; t += 1
    totA /= P; totB /= P
    nz = np.nonzero(totA)[0]
    if not len(nz): return None
    d = int(nz[0])
    return dict(dfree=d, A=[round(float(x), 4) for x in totA[d:d + terms]], B=[round(float(x), 4) for x in totB[d:d + terms]], W=W)


if __name__ == '__main__':
    print(spektr([0o171, 0o133], 7))
    print(spektr([0o7, 0o5], 3))
    print(spektr([0o171, 0o133], 7, punct=[[1, 0], [1, 1]]))
