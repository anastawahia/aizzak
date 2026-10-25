# ruff: noqa
# Part of the throwaway M-0 spike (see spike.py); lint is off here.
"""Score spike nets against a hand-read reference, as terminal pairs.

Usage: python -I score.py reference_p<N>.json out/p<N>_ids.json
"""

import itertools, json, sys

ref = json.load(open(sys.argv[1]))
got = json.load(open(sys.argv[2]))
gnd = set(ref["gnd_bus"])


def pairs(groups):
    s = set()
    for g in groups:
        g = sorted(x for x in g if x not in gnd)
        s.update(itertools.combinations(g, 2))
    return s


def merge(groups, extra):
    parent = {}

    def f(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            a = parent[a]
        return a

    for g in groups:
        for x in g[1:]:
            parent[f(x)] = f(g[0])
        if g:
            f(g[0])
    for a, b in extra:
        parent[f(a)] = f(b)
    out = {}
    for x in list(parent):
        out.setdefault(f(x), []).append(x)
    return list(out.values())


R_full, R_solid = pairs(ref["full"]), pairs(ref["solid"])
P_c = pairs(got["nets"])
P_cc = pairs(merge(got["nets"], got["dashed_pairs"]))


def rep(name, P, R):
    tp = len(P & R)
    print(
        f"{name}: predicted={len(P)} correct={tp} wrong={len(P - R)} missed={len(R - P)} "
        f"precision={tp / len(P) if P else 0:.3f} recall={tp / len(R):.3f}"
    )
    return P - R, R - P


w, m = rep("certain vs solid reference", P_c, R_solid)
print("  wrong:", sorted(w)[:20])
print("  missed:", sorted(m)[:30])
w2, _ = rep("certain vs full reference ", P_c, R_full)
w3, m3 = rep("certain+dashed vs full    ", P_cc, R_full)
print("  wrong:", sorted(w3)[:20])
print("  missed:", len(m3))
