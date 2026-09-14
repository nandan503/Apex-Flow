#!/usr/bin/env python3
"""Audit presentation/Apex-Flow-deck.pptx against presentation/index.html.

    python3 presentation/check_pptx.py            # exit 0 = clean

There is no PowerPoint renderer in this environment, so this checks what can be
checked mechanically:

  1. the file opens and has the expected slide count;
  2. every atomic text unit in the HTML (paragraph, list item, heading, node
     label, code line, table cell, chip, evidence ref) survives into the slide,
     with its words in the same order — this is what catches a renderer that
     silently drops or reorders content;
  3. no shape falls outside the slide, and no two content shapes overlap by more
     than a tolerance (a proxy for the overflow a renderer cannot see);
  4. every slide carries speaker notes.

Text units are compared as word sequences, so punctuation and line-wrapping
differences do not register as failures.
"""
from __future__ import annotations

import importlib.util
import re
import sys

from pptx import Presentation

PPTX = "presentation/Apex-Flow-deck.pptx"
SRC = "presentation/index.html"
OVERLAP_TOL = 0.35          # square inches of accidental overlap tolerated
BLOCK_TAGS = ("div", "table", "ul", "ol", "p", "section")


def load_builder():
    spec = importlib.util.spec_from_file_location("bp", "presentation/build_pptx.py")
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    return bp


def words(t):
    return re.findall(r"[a-z0-9₹%→·]+", t.lower())


def shape_text(slide):
    out = []
    for sh in slide.shapes:
        if sh.has_text_frame:
            out.append(sh.text_frame.text)
        if getattr(sh, "has_table", False) and sh.has_table:
            for r in sh.table.rows:
                out.append(" | ".join(c.text for c in r.cells))
    return "\n".join(out)


def units(bp, node):
    """Atomic text units worth checking, in document order."""
    seen = set()
    for k in node.walk():
        want = (k.tag in ("p", "li", "h1", "h2", "h3")
                or (k.tag == "div" and any(k.has(c) for c in
                    ("t", "m", "d", "mono", "small", "stat", "chip", "chips", "row")))
                or (k.tag == "span" and any(k.has(c) for c in ("l", "k2", "small", "chip")))
                or k.tag in ("td", "th"))
        if not want:
            continue
        if k.tag == "div" and (k.has("chips") or k.has("row")):
            for s in k.only("span"):
                t = bp.text(s)
                if len(words(t)) >= 3 and t not in seen:
                    seen.add(t); yield t
            continue
        t = bp.text(k)
        if len(words(t)) >= 3 and t not in seen:
            seen.add(t)
            yield t


def main():
    bp = load_builder()
    prs = Presentation(PPTX)
    secs = bp.parse(SRC)
    W, H = prs.slide_width / 914400, prs.slide_height / 914400
    fails = []

    if len(prs.slides._sldIdLst) != len(secs):
        fails.append(f"slide count {len(prs.slides._sldIdLst)} != {len(secs)} sections")

    print(f"{PPTX}: {len(prs.slides._sldIdLst)} slides at {W:.2f} x {H:.2f} in")
    print("slide  units  missing  notes  oob  overlaps")
    tot_units = tot_miss = 0
    for i, (sec, sl) in enumerate(zip(secs, prs.slides), 1):
        # ---- 2 · content fidelity ----
        pw = words(shape_text(sl))
        windows = {tuple(pw[j:j + L]) for L in range(3, 40) for j in range(max(0, len(pw) - L + 1))}
        miss = []
        us = list(units(bp, sec))
        for u in us:
            uw = words(u)[:39]
            if uw and tuple(uw) not in windows:
                miss.append(u)
        tot_units += len(us); tot_miss += len(miss)

        # ---- 3 · geometry ----
        rects, oob = [], 0
        for sh in sl.shapes:
            if sh.left is None:
                continue
            x, y = sh.left / 914400, sh.top / 914400
            w, h = sh.width / 914400, sh.height / 914400
            if x < -0.02 or y < -0.02 or x + w > W + 0.02 or y + h > H + 0.02:
                oob += 1
            if w > 0.5 and h > 0.2:
                rects.append((x, y, w, h))
        overlaps = 0
        for a in range(len(rects)):
            for b in range(a + 1, len(rects)):
                x1, y1, w1, h1 = rects[a]; x2, y2, w2, h2 = rects[b]
                # containment is intentional: a card holds its own text boxes and
                # code panels, so only partial overlaps indicate a layout error
                t = 0.02
                if (x1 <= x2 + t and y1 <= y2 + t and x1 + w1 >= x2 + w2 - t and y1 + h1 >= y2 + h2 - t) or \
                   (x2 <= x1 + t and y2 <= y1 + t and x2 + w2 >= x1 + w1 - t and y2 + h2 >= y1 + h1 - t):
                    continue
                ox = max(0.0, min(x1 + w1, x2 + w2) - max(x1, x2))
                oy = max(0.0, min(y1 + h1, y2 + h2) - max(y1, y2))
                if ox * oy > OVERLAP_TOL:
                    overlaps += 1

        # ---- 4 · notes ----
        notes = sl.notes_slide.notes_text_frame.text if sl.has_notes_slide else ""
        if len(notes) < 80:
            fails.append(f"slide {i}: speaker notes too short ({len(notes)} chars)")
        if miss:
            fails.append(f"slide {i}: {len(miss)} unit(s) missing, e.g. {miss[0][:60]!r}")
        if oob:
            fails.append(f"slide {i}: {oob} shape(s) outside the slide")
        if overlaps:
            fails.append(f"slide {i}: {overlaps} shape pair(s) overlap > {OVERLAP_TOL} in²")

        flag = "✓" if not (miss or oob or overlaps) else "⚠"
        print(f" {flag} {i:>3}  {len(us):>5}  {len(miss):>7}  {len(notes):>5}  {oob:>3}  {overlaps:>8}")

    print(f"\nunits checked: {tot_units} | missing: {tot_miss}")
    if fails:
        print("\nFAILURES:")
        for f in fails:
            print("  ·", f)
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
