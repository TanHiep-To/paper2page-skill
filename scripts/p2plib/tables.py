"""Caption-anchored table detection: the table's area (for an image crop) and a best-effort cell grid."""
from __future__ import annotations

import re
from collections import Counter

import pymupdf

from .figures import column_range, text_blocks
from .pdf_text import clean, numbers_in, squash

CAPTION_RE = re.compile(r"^\s*(?:Table|TABLE)\s+([A-Z]?\d+)\s*[:.|]")
CELL_GAP = 7.0   # words closer than this (pt) belong to the same cell
LINE_TOL = 3.0   # words within this vertical distance are on the same text line


def _rules(page: pymupdf.Page) -> list[pymupdf.Rect]:
    rects = (pymupdf.Rect(d["rect"]) for d in page.get_drawings())
    return sorted((r for r in rects if r.height < 2 and r.width > 40), key=lambda r: r.y0)


def _span(rules: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """The first run of rules that belong to one table (no gap larger than half a page)."""
    run = rules[:1]
    for r in rules[1:]:
        if r.y0 - run[-1].y1 > 420:
            break
        run.append(r)
    return run


def _table_rect(caption: pymupdf.Rect, rules: list[pymupdf.Rect], x0: float, x1: float) -> tuple[pymupdf.Rect | None, str]:
    """Area between the first and last horizontal rule below the caption; falls back to above it."""
    in_col = [r for r in rules if r.x1 > x0 + 2 and r.x0 < x1 - 2]
    below = _span([r for r in in_col if r.y0 >= caption.y1 - 2])
    above = _span([r for r in reversed(in_col) if r.y1 <= caption.y0 + 2])
    group, side = (below, "below") if len(below) >= 2 else (above, "above") if len(above) >= 2 else ([], "")
    if not group:
        return None, ""
    ys = [r.y0 for r in group] + [r.y1 for r in group]
    return pymupdf.Rect(min(r.x0 for r in group) - 2, min(ys) - 2, max(r.x1 for r in group) + 2, max(ys) + 2), side


def _lines(words: list[tuple]) -> list[list[tuple]]:
    lines: list[list[tuple]] = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        mid = (w[1] + w[3]) / 2
        if lines and abs(mid - (lines[-1][0][1] + lines[-1][0][3]) / 2) <= LINE_TOL:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(ln, key=lambda w: w[0]) for ln in lines]


def _segments(line: list[tuple]) -> list[list]:
    """Cells of one text line as [x0, x1, text]."""
    segs: list[list] = []
    for w in line:
        if segs and w[0] - segs[-1][1] < CELL_GAP:
            segs[-1][1] = max(segs[-1][1], w[2])
            segs[-1][2] += " " + w[4]
        else:
            segs.append([w[0], w[2], w[4]])
    return segs


def read_grid(page: pymupdf.Page, rect: pymupdf.Rect) -> list[list[str]]:
    """Rows of cell strings from the text layer. Columns come from the lines with the most common cell count."""
    rows = [_segments(ln) for ln in _lines(page.get_text("words", clip=rect))]
    if not rows:
        return []
    width = Counter(len(r) for r in rows).most_common(1)[0][0]
    cols = [[min(r[i][0] for r in rows if len(r) == width), max(r[i][1] for r in rows if len(r) == width)]
            for i in range(width)]
    grid = []
    for segs in rows:
        cells = [""] * width
        for x0, x1, text in segs:
            overlap = [max(0.0, min(x1, c1) - max(x0, c0)) for c0, c1 in cols]
            i = overlap.index(max(overlap)) if max(overlap) > 0 else min(
                range(width), key=lambda k: abs((cols[k][0] + cols[k][1]) / 2 - (x0 + x1) / 2))
            cells[i] = (cells[i] + " " + text).strip()
        grid.append([clean(c) for c in cells])
    return grid


def bold_numbers(page: pymupdf.Page, rect: pymupdf.Rect) -> list[str]:
    found: set[str] = set()
    for b in page.get_text("dict", clip=rect)["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                if s["flags"] & 16 or re.search(r"bold|medi", s["font"], re.I):
                    found |= numbers_in(s["text"])
    return sorted(found)


def detect_tables(doc: pymupdf.Document) -> list[dict]:
    """One entry per 'Table N' caption: label, page (1-based), caption, rect (or None), side."""
    found: dict[str, dict] = {}
    for page in doc:
        blocks = text_blocks(page)
        rules = None
        for b in blocks:
            m = CAPTION_RE.match(b["text"])
            if not m or m.group(1) in found:
                continue
            rules = _rules(page) if rules is None else rules
            rect, side = _table_rect(b["rect"], rules, *column_range(b["rect"], blocks))
            found[m.group(1)] = {"label": m.group(1), "page": page.number + 1,
                                 "caption": CAPTION_RE.sub("", squash(b["text"])).strip(), "rect": rect, "side": side}
    return list(found.values())
