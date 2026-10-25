# ruff: noqa
# A throwaway M-0 spike kept as evidence, not platform code: nothing in the
# platform imports it, and its tuning constants are the point of the experiment,
# so lint is off here.
"""M-0 spike: vector PDF page -> candidate netlist (outside the platform).

Usage: python -I spike.py <pdf> <page_no(1-based)> <out_dir>
Keep <out_dir> outside the repository: the overlay images are the real drawing.

All geometry is in the page's DISPLAY frame (rotation applied), in points.
"""

import collections
import json
import math
import random
import re
import resource
import sys
import time

import cv2
import fitz
import numpy as np
import pytesseract

SMALL = 14.0  # a path whose bbox fits in SMALL x SMALL pt is a glyph/ring/dot part
HV_EPS = 0.05  # |dx| or |dy| below this => vertical / horizontal
TOL = 0.8  # endpoint-on-wire tolerance (pt)
MERGE_GAP = 0.4  # collinear pieces closer than this are one wire
DASH_MAX = 22.0  # a dash is at most this long
DASH_GAP = (1.5, 16.0)  # gap between dashes
OCR_SCALE = 4.0


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[b] = a


def load(page):
    m = page.rotation_matrix
    out = []
    for path in page.get_drawings():
        segs, kinds = [], set()
        for it in path["items"]:
            kinds.add(it[0])
            if it[0] == "l":
                segs.append((it[1] * m, it[2] * m))
            elif it[0] == "c":
                segs.append((it[1] * m, it[4] * m))
            elif it[0] in ("re", "qu"):
                q = it[1].quad if it[0] == "re" else it[1]
                pts = [q.ul * m, q.ur * m, q.lr * m, q.ll * m]
                segs += [(pts[k], pts[(k + 1) % 4]) for k in range(4)]
        out.append(
            {
                "type": path["type"],
                "rect": path["rect"] * m,
                "segs": segs,
                "kinds": kinds,
                "n": len(path["items"]),
            }
        )
    return out


def classify(paths):
    rings, dots, glyph, wire_segs, diag, outline = [], [], [], [], [], []
    fills = [
        p
        for p in paths
        if p["type"] == "f" and p["rect"].width <= SMALL and p["rect"].height <= SMALL
    ]
    for p in paths:
        r = p["rect"]
        small = r.width <= SMALL and r.height <= SMALL
        if (
            p["type"] == "s"
            and small
            and p["n"] >= 16
            and abs(r.width - r.height) < 1.0
            and 5.5 <= r.width <= 10.0
            and p["kinds"] == {"l"}
        ):
            c = fitz.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
            (dots if r.width < 8.0 else rings).append({"c": c, "r": r.width / 2, "rect": r})
            continue
        if small:
            glyph.append(p)
            continue
        if p["type"] != "s":
            continue
        if "re" in p["kinds"]:
            outline.extend(p["segs"])
            continue
        for a, b in p["segs"]:
            if abs(a.x - b.x) < HV_EPS or abs(a.y - b.y) < HV_EPS:
                if math.hypot(b.x - a.x, b.y - a.y) > 0.2:
                    wire_segs.append((a, b))
            else:
                diag.append((a, b))
    # fill triangles that belong to dots are not glyphs
    dot_rects = [d["rect"] for d in dots]
    glyph = [g for g in glyph if not any(dr.contains(g["rect"]) for dr in dot_rects)]
    del fills
    return rings, dots, glyph, wire_segs, diag, outline


def to_lines(segs):
    """H/V segments -> (orient, k, lo, hi). orient 'H': y=k, x in [lo,hi]."""
    lines = []
    for a, b in segs:
        if abs(a.y - b.y) < HV_EPS:
            lines.append(("H", round((a.y + b.y) / 2, 2), min(a.x, b.x), max(a.x, b.x)))
        else:
            lines.append(("V", round((a.x + b.x) / 2, 2), min(a.y, b.y), max(a.y, b.y)))
    return lines


def group_collinear(lines):
    groups = collections.defaultdict(list)
    for o, k, lo, hi in lines:
        groups[(o, round(k / 0.25))].append((lo, hi, k))
    return groups


def split_dashes(lines, rings):
    """Separate dashed runs (>=3 short pieces, regular gaps) from solid pieces.

    A gap that holds a terminal ring is a terminal, not a dash gap: the run
    stops there, so a short solid stub next to a terminal stays solid.
    """
    solid, dashed = [], []

    def ring_in_gap(o, k, lo, hi):
        for t in rings:
            along, across = (t["c"].x, t["c"].y) if o == "H" else (t["c"].y, t["c"].x)
            if abs(across - k) <= 0.5 and lo - 0.5 <= along <= hi + 0.5:
                return True
        return False

    for (o, _), pieces in group_collinear(lines).items():
        pieces.sort()
        i = 0
        while i < len(pieces):
            j = i
            while (
                j + 1 < len(pieces)
                and pieces[j][1] - pieces[j][0] <= DASH_MAX
                and pieces[j + 1][1] - pieces[j + 1][0] <= DASH_MAX
                and DASH_GAP[0] <= pieces[j + 1][0] - pieces[j][1] <= DASH_GAP[1]
                and not ring_in_gap(o, pieces[j][2], pieces[j][1], pieces[j + 1][0])
            ):
                j += 1
            run = pieces[i : j + 1]
            k = run[0][2]
            if len(run) >= 3:
                dashed.append((o, k, run[0][0], run[-1][1]))
            else:
                solid.extend((o, k, lo, hi) for lo, hi, _ in run)
            i = j + 1
    return solid, dashed


def merge(lines):
    out = []
    for (o, _), pieces in group_collinear(lines).items():
        pieces.sort()
        cur = list(pieces[0])
        for lo, hi, k in pieces[1:]:
            if lo <= cur[1] + MERGE_GAP:
                cur[1] = max(cur[1], hi)
            else:
                out.append((o, cur[2], cur[0], cur[1]))
                cur = [lo, hi, k]
        out.append((o, cur[2], cur[0], cur[1]))
    return [w for w in out if w[3] - w[2] > 0.5]


def endpoints(w):
    o, k, lo, hi = w
    return (
        (fitz.Point(lo, k), fitz.Point(hi, k))
        if o == "H"
        else (fitz.Point(k, lo), fitz.Point(k, hi))
    )


def dist_to_wire(pt, w):
    o, k, lo, hi = w
    if o == "H":
        x = min(max(pt.x, lo), hi)
        return math.hypot(pt.x - x, pt.y - k)
    y = min(max(pt.y, lo), hi)
    return math.hypot(pt.x - k, pt.y - y)


class Grid:
    def __init__(self, items, bbox, cell=24.0):
        self.cell = cell
        self.cells = collections.defaultdict(list)
        for i, it in enumerate(items):
            x0, y0, x1, y1 = bbox(it)
            for gx in range(int(x0 // cell), int(x1 // cell) + 1):
                for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                    self.cells[(gx, gy)].append(i)

    def near(self, pt, rad):
        found = set()
        for gx in range(int((pt.x - rad) // self.cell), int((pt.x + rad) // self.cell) + 1):
            for gy in range(int((pt.y - rad) // self.cell), int((pt.y + rad) // self.cell) + 1):
                found.update(self.cells.get((gx, gy), ()))
        return found


def wire_bbox(w):
    o, k, lo, hi = w
    return (lo, k, hi, k) if o == "H" else (k, lo, k, hi)


def build_nets(wires, rings, dots, diag):
    n = len(wires)
    dsu = DSU(n + len(rings))  # wires first, then terminal nodes
    grid = Grid(wires, wire_bbox)
    stats = collections.Counter()
    on_terminal = collections.defaultdict(set)
    # dots: every wire touching a dot joins
    for d in dots:
        touching = [
            i
            for i in grid.near(d["c"], d["r"] + TOL)
            if dist_to_wire(d["c"], wires[i]) <= d["r"] + TOL
        ]
        for i in touching[1:]:
            dsu.union(touching[0], i)
        stats["dot_joins"] += max(0, len(touching) - 1)
        if len(touching) < 2:
            stats["dot_isolated"] += 1
    # terminals: a wire ending at the ring, or drawn straight through its
    # centre, joins the terminal node
    for ti, t in enumerate(rings):
        for i in grid.near(t["c"], t["r"] + TOL):
            if dist_to_wire(t["c"], wires[i]) <= TOL or any(
                abs(e.distance_to(t["c"]) - t["r"]) <= TOL or e.distance_to(t["c"]) <= t["r"]
                for e in endpoints(wires[i])
            ):
                dsu.union(n + ti, i)
                on_terminal[ti].add(i)
    # endpoint on another wire's END (a corner, an L) joins. Endpoint on another
    # wire's INTERIOR (a T) joins only through a dot (handled above); a T with
    # no dot is a review item, because symbol leads (a CT on its primary) look
    # exactly like that.
    dot_grid = Grid(dots, lambda d: (d["c"].x, d["c"].y, d["c"].x, d["c"].y))
    t_review = []
    for i, w in enumerate(wires):
        for e in endpoints(w):
            for j in grid.near(e, TOL):
                if j == i or dist_to_wire(e, wires[j]) > TOL:
                    continue
                if any(e.distance_to(q) <= TOL for q in endpoints(wires[j])):
                    if dsu.find(i) != dsu.find(j):
                        stats["l_joins"] += 1
                    dsu.union(i, j)
                elif not any(
                    dots[k]["c"].distance_to(e) <= dots[k]["r"] + TOL for k in dot_grid.near(e, 6)
                ):
                    stats["t_without_dot_review"] += 1
                    t_review.append(e)
    # crossings without a dot: NOT connected (rule: no connection from a crossing)
    for i, w in enumerate(wires):
        if w[0] != "H":
            continue
        _, y, x0, x1 = w
        for j in grid.near(fitz.Point((x0 + x1) / 2, y), (x1 - x0) / 2 + 1):
            v = wires[j]
            if v[0] != "V":
                continue
            _, x, y0, y1 = v
            if x0 + TOL < x < x1 - TOL and y0 + TOL < y < y1 - TOL:
                pt = fitz.Point(x, y)
                if not any(
                    dots[k]["c"].distance_to(pt) <= dots[k]["r"] + TOL for k in dot_grid.near(pt, 6)
                ):
                    stats["crossings_not_joined"] += 1
    # diagonal jumpers: a long diagonal whose BOTH ends land on a wire end or a
    # terminal ring. Contact blades have a free end, so they never qualify.
    # Joined, but counted apart: these are candidates for review, not certain.
    ends = [e for w in wires for e in endpoints(w)]
    end_owner = [i for i in range(n) for _ in range(2)]
    end_grid = Grid(ends, lambda e: (e.x, e.y, e.x, e.y))
    ring_grid = Grid(rings, lambda t: (t["c"].x, t["c"].y, t["c"].x, t["c"].y))
    jumpers = []
    for a, b in diag:
        if a.distance_to(b) < 10:
            continue
        sides = []
        for e in (a, b):
            hit = [end_owner[k] for k in end_grid.near(e, TOL) if ends[k].distance_to(e) <= TOL]
            hit += [
                n + k
                for k in ring_grid.near(e, 12)
                if abs(e.distance_to(rings[k]["c"]) - rings[k]["r"]) <= TOL
            ]
            sides.append(hit)
        if sides[0] and sides[1]:
            if dsu.find(sides[0][0]) != dsu.find(sides[1][0]):
                stats["diagonal_jumper_joins"] += 1
            dsu.union(sides[0][0], sides[1][0])
            jumpers.append((a, b))
    return dsu, stats, on_terminal, jumpers, t_review


def ocr_glyphs(glyph, page_rect, out_png):
    w, h = int(page_rect.width * OCR_SCALE), int(page_rect.height * OCR_SCALE)
    img = np.full((h, w), 255, np.uint8)
    for g in glyph:
        if g["type"] == "f":
            pts = np.array(
                [[p.x * OCR_SCALE, p.y * OCR_SCALE] for s in g["segs"] for p in s],
                np.int32,
            )
            if len(pts) >= 3:
                cv2.fillPoly(img, [cv2.convexHull(pts)], 0)
        else:
            for a, b in g["segs"]:
                cv2.line(
                    img,
                    (int(a.x * OCR_SCALE), int(a.y * OCR_SCALE)),
                    (int(b.x * OCR_SCALE), int(b.y * OCR_SCALE)),
                    0,
                    2,
                )
    cv2.imwrite(out_png, img)
    words = []
    tile, ov = 2400, 160
    for ty in range(0, h, tile - ov):
        for tx in range(0, w, tile - ov):
            sub = img[ty : ty + tile, tx : tx + tile]
            if sub.min() == 255:
                continue
            data = pytesseract.image_to_data(
                sub,
                config="--psm 11 -c tessedit_char_whitelist="
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-/.:()+&",
                output_type=pytesseract.Output.DICT,
            )
            for k, txt in enumerate(data["text"]):
                txt = txt.strip()
                if not txt or float(data["conf"][k]) < 30:
                    continue
                x0 = (tx + data["left"][k]) / OCR_SCALE
                y0 = (ty + data["top"][k]) / OCR_SCALE
                words.append(
                    {
                        "t": txt,
                        "conf": float(data["conf"][k]),
                        "r": [
                            x0,
                            y0,
                            x0 + data["width"][k] / OCR_SCALE,
                            y0 + data["height"][k] / OCR_SCALE,
                        ],
                    }
                )
    # de-duplicate overlap zone repeats
    uniq = {}
    for wd in words:
        key = (wd["t"], round(wd["r"][0] / 4), round(wd["r"][1] / 4))
        if key not in uniq or uniq[key]["conf"] < wd["conf"]:
            uniq[key] = wd
    return list(uniq.values())


TERM_RE = re.compile(r"^[A-Z0-9]{1,5}-[0-9]{1,3}$|^[0-9]{1,3}$")


def label_terminals(rings, words):
    """Nearest plausible word: right of the ring first, then above, then left."""
    labels = {}
    for ti, t in enumerate(rings):
        cx, cy, r = t["c"].x, t["c"].y, t["r"]
        best = None
        for wd in words:
            x0, y0, x1, y1 = wd["r"]
            wy, wx = (y0 + y1) / 2, (x0 + x1) / 2
            if -2 <= x0 - (cx + r) <= 16 and abs(wy - cy) <= 6:
                rank = 0 + abs(x0 - cx - r) / 100
            elif 0 <= cy - r - y1 <= 12 and x0 - 4 <= cx <= x1 + 22:
                rank = 1 + (cy - r - y1) / 100
            elif -2 <= (cx - r) - x1 <= 16 and abs(wy - cy) <= 6:
                rank = 2 + ((cx - r) - x1) / 100
            else:
                continue
            if not TERM_RE.match(wd["t"]):
                rank += 3
            if best is None or rank < best[0]:
                best = (rank, wd["t"])
        labels[ti] = best[1] if best and best[0] < 3 else None
    return labels


def main():
    pdf, pno, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    t0 = time.perf_counter()
    page = fitz.open(pdf)[pno - 1]
    paths = load(page)
    rings, dots, glyph, wire_segs, diag, outline = classify(paths)
    t_load = time.perf_counter()
    lines = to_lines(wire_segs)
    solid, dashed = split_dashes(lines, rings)
    wires = merge(solid)
    dashed = merge(dashed)
    dsu, stats, on_terminal, jumpers, t_review = build_nets(wires, rings, dots, diag)
    t_net = time.perf_counter()
    words = ocr_glyphs(glyph, page.rect, f"{out}/p{pno}_glyphs.png")
    labels = label_terminals(rings, words)
    t_ocr = time.perf_counter()

    n = len(wires)
    nets = collections.defaultdict(lambda: {"wires": 0, "terminals": [], "length": 0.0})
    for i, w in enumerate(wires):
        net = nets[dsu.find(i)]
        net["wires"] += 1
        net["length"] += w[3] - w[2]
    for ti in range(len(rings)):
        root = dsu.find(n + ti)
        nets[root]["terminals"].append(
            labels[ti] or f"?@{rings[ti]['c'].x:.0f},{rings[ti]['c'].y:.0f}"
        )
    # dashed links: candidate cable cores when both ends sit on terminals
    ring_grid = Grid(rings, lambda t: (t["c"].x, t["c"].y, t["c"].x, t["c"].y))
    cand, cand_ids = [], []
    for w in dashed:
        ends = []
        for e in endpoints(w):
            hit = [
                k
                for k in ring_grid.near(e, 12)
                if abs(e.distance_to(rings[k]["c"]) - rings[k]["r"]) <= 3.0
                or e.distance_to(rings[k]["c"]) <= rings[k]["r"]
            ]
            ends.append(hit[0] if hit else None)
        if ends[0] is not None and ends[1] is not None:
            cand.append((labels[ends[0]], labels[ends[1]]))
            cand_ids.append((ends[0], ends[1]))
    by_root = collections.defaultdict(list)
    for ti in range(len(rings)):
        by_root[dsu.find(n + ti)].append(ti)
    with open(f"{out}/p{pno}_ids.json", "w") as f:
        json.dump({"nets": list(by_root.values()), "dashed_pairs": cand_ids}, f)

    real_nets = {k: v for k, v in nets.items() if v["terminals"]}
    multi = [v for v in real_nets.values() if len(v["terminals"]) >= 2]
    labelled = sum(1 for v in labels.values() if v)
    result = {
        "page": pno,
        "paths": len(paths),
        "glyph_paths": len(glyph),
        "terminals": len(rings),
        "junction_dots": len(dots),
        "wire_pieces": len(solid),
        "wires_merged": n,
        "dashed_lines": len(dashed),
        "symbol_diagonals": len(diag),
        "outline_segments": len(outline),
        "nets_with_terminals": len(real_nets),
        "nets_with_2plus_terminals": len(multi),
        "terminals_labelled_by_ocr": labelled,
        "ocr_words": len(words),
        "dashed_terminal_to_terminal": len(cand),
        **stats,
        "seconds": {
            "load_classify": round(t_load - t0, 2),
            "nets": round(t_net - t_load, 2),
            "ocr": round(t_ocr - t_net, 2),
        },
        "max_rss_mb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024,
    }
    with open(f"{out}/p{pno}_result.json", "w") as f:
        json.dump(result, f, indent=1)
    with open(f"{out}/p{pno}_nets.json", "w") as f:
        json.dump(
            {
                "nets": sorted(
                    (sorted(map(str, v["terminals"])) for v in multi),
                    key=len,
                    reverse=True,
                ),
                "dashed_candidates": cand,
                "words": [w["t"] for w in sorted(words, key=lambda w: (w["r"][1], w["r"][0]))],
            },
            f,
            indent=1,
            ensure_ascii=False,
        )

    # overlay: each net its own colour; crossings stay as drawn
    s = 1.0
    pix = page.get_pixmap(matrix=fitz.Matrix(s, s), colorspace=fitz.csRGB)
    base = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    img = (255 - (255 - base) // 4).copy()
    rnd = random.Random(7)
    colours = {}
    for i, w in enumerate(wires):
        root = dsu.find(i)
        if root not in colours:
            colours[root] = tuple(rnd.randint(0, 200) for _ in range(3))
        a, b = endpoints(w)
        cv2.line(
            img,
            (int(a.x * s), int(a.y * s)),
            (int(b.x * s), int(b.y * s)),
            colours[root],
            2,
        )
    for w in dashed:
        a, b = endpoints(w)
        cv2.line(
            img,
            (int(a.x * s), int(a.y * s)),
            (int(b.x * s), int(b.y * s)),
            (160, 160, 160),
            1,
        )
    for e in t_review:
        cv2.drawMarker(
            img,
            (int(e.x * s), int(e.y * s)),
            (255, 0, 0),
            cv2.MARKER_TILTED_CROSS,
            9,
            2,
        )
    for a, b in jumpers:
        cv2.line(
            img,
            (int(a.x * s), int(a.y * s)),
            (int(b.x * s), int(b.y * s)),
            (255, 140, 0),
            2,
        )
    for ti, t in enumerate(rings):
        col = (0, 150, 0) if labels[ti] else (0, 0, 255)
        cv2.circle(img, (int(t["c"].x * s), int(t["c"].y * s)), int(t["r"] * s) + 2, col, 1)
    for d in dots:
        cv2.circle(
            img,
            (int(d["c"].x * s), int(d["c"].y * s)),
            int(d["r"] * s) + 3,
            (255, 0, 255),
            1,
        )
    cv2.imwrite(f"{out}/p{pno}_overlay.png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
