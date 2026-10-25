# ruff: noqa
# Part of the throwaway M-0 spike (see spike.py); lint is off here.
"""Render a page with every terminal ring numbered, for a hand-made reference.

Usage: python -I refkit.py <pdf> <page_no(1-based)> <zoom> <out_dir>
Writes <out_dir>/ref_p<N>.png and <out_dir>/ref_p<N>_terminals.json. Keep
<out_dir> outside the repository: the image is the real drawing.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv2
import fitz
import numpy as np

import spike

pdf, pno, z, out = sys.argv[1], int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
page = fitz.open(pdf)[pno - 1]
rings, dots, *_ = spike.classify(spike.load(page))
pix = page.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csRGB)
img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
terms = []
for i, t in enumerate(rings):
    x, y = int(t["c"].x * z), int(t["c"].y * z)
    cv2.putText(img, str(i), (x - 14, y - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 0, 0), 2)
    terms.append({"id": i, "x": round(t["c"].x, 1), "y": round(t["c"].y, 1)})
cv2.imwrite(f"{out}/ref_p{pno}.png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
json.dump(terms, open(f"{out}/ref_p{pno}_terminals.json", "w"))
print(len(terms), pix.width, pix.height)
