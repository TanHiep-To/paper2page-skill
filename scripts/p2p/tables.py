"""Best-effort table extraction: find 'Table N' captions and read the grid between the table's rules."""
from __future__ import annotations

import re
from collections import Counter

import pymupdf

from .pdf_text import clean, numbers_in, squash

CAPTION_RE = re.compile(r"^\s*(?:Table|TABLE)\s+(\d+)\s*[:.|]")
CELL_GAP = 7.0   # words closer than this (pt) belong to the same cell
LINE_TOL = 3.0   # words within this vertical distance are on the same text line


def _rules(page: pymupdf.Page) -> list[pymupdf.Rect]:
    rects = (pymupdf.Rect(d["rect"]) for d in page.get_drawings())
    return sorted((r for r in rects if r.height < 2 and r.width > 40), key=lambda r: r.y0)


def _table_clip(caption: pymupdf.Rect, rules: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    """Area between the first and last horizontal rule under the caption (or above it)."""
    below = [r for r in rules if r.y0 >= caption.y1 - 2]
    above = [r for r in rules if r.y1 <= caption.y0 + 2]
    group = below if len(below) >= 2 else above if len(above) >= 2 else []
    if not group:
        return None
    return pymupdf.Rect(min(r.x0 for r in group) - 2, group[0].y0, max(r.x1 for r in group) + 2, group[-1].y1)


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


def _grid(words: list[tuple]) -> list[list[str]]:
    """Rows of cell strings. Columns come from the lines that have the most common cell count."""
    rows = [_segments(ln) for ln in _lines(words)]
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


def _bold_numbers(page: pymupdf.Page, clip: pymupdf.Rect) -> list[str]:
    found: set[str] = set()
    for b in page.get_text("dict", clip=clip)["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                if s["flags"] & 16 or re.search(r"bold|medi", s["font"], re.I):
                    found |= numbers_in(s["text"])
    return sorted(found)


def extract_tables(doc: pymupdf.Document) -> list[dict]:
    tables: dict[int, dict] = {}
    for page in doc:
        rules = None
        for b in page.get_text("blocks"):
            m = CAPTION_RE.match(b[4])
            if not m or int(m.group(1)) in tables:
                continue
            rules = _rules(page) if rules is None else rules
            clip = _table_clip(pymupdf.Rect(b[:4]), rules)
            grid = _grid(page.get_text("words", clip=clip)) if clip else []
            n = int(m.group(1))
            tables[n] = {
                "id": f"table{n}", "number": n, "page": page.number + 1,
                "caption": CAPTION_RE.sub("", squash(b[4])).strip(),
                "grid": grid, "bold_numbers": _bold_numbers(page, clip) if clip else [],
            }
    return [tables[n] for n in sorted(tables)]
