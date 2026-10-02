"""dfree свёрточного кода (k0/n0) с одним регистром длины K = m0 + k0 (структура Lee, TDA 42-82, рис. 1):
за такт в регистр вдвигаются k0 информационных бит, выходы — g(j)·регистр (mod 2), j = 1…n0.
Порядок: newest='left' — новые биты в старших разрядах регистра (как запись генераторов слева направо)."""
import heapq


def dfree_kn(gens, K, k0, newest='left'):
    m0 = K - k0
    S = 1 << m0
    def step(state, u):
        if newest == 'left':
            reg = (u << m0) | state
            ns = reg >> k0
        else:
            reg = (state << k0) | u
            ns = reg & (S - 1)
        w = sum(bin(reg & g).count('1') & 1 for g in gens)
        return ns, w
    best = None
    dist = {}
    pq = []
    for u in range(1, 1 << k0):
        ns, w = step(0, u)
        if ns == 0:
            best = w if best is None else min(best, w); continue
        heapq.heappush(pq, (w, ns))
    while pq:
        w, s = heapq.heappop(pq)
        if best is not None and w >= best: break
        if dist.get(s, 1 << 30) <= w: continue
        dist[s] = w
        for u in range(1 << k0):
            ns, dw = step(s, u)
            if ns == 0:
                if best is None or w + dw < best: best = w + dw
            elif dist.get(ns, 1 << 30) > w + dw:
                heapq.heappush(pq, (w + dw, ns))
    return best


if __name__ == '__main__':
    print(dfree_kn([0o171, 0o133], 7, 1), dfree_kn([0o37, 0o21, 0o5, 0o4], 5, 3), dfree_kn([0o37, 0o21, 0o5, 0o4], 5, 3, 'right'))
