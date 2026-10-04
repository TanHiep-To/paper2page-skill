"""Caption-anchored table detection: the table's area (for an image crop) and a best-effort cell grid."""
from __future__ import annotations

import re
from collections import Counter

import pymupdf

from .figures import CAPTION_RE as FIG_CAPTION_RE
from .figures import column_range, label_key, label_order, text_blocks, unique_key
from .pdf_text import clean, numbers_in, squash

# "Table 3:", appendix labels ("Table B2."), Roman numerals ("Table II."), and the IEEE form where
# "TABLE II" stands alone on its line with the caption below it.
CAPTION_RE = re.compile(r"^\s*(?:Table|TABLE)\s+([A-Z]?\.?\d+|[IVXL]+)\s*[:.|]")
LABEL_ONLY_RE = re.compile(r"^\s*TABLE\s+([IVXL]+|\d+)[ \t]*(?:\n|$)")
ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}
CELL_GAP = 7.0   # words closer than this (pt) belong to the same cell
LINE_TOL = 3.0   # words within this vertical distance are on the same text line


def caption_match(text: str) -> re.Match | None:
    return CAPTION_RE.match(text) or LABEL_ONLY_RE.match(text)


def roman_to_int(label: str) -> int:
    total = 0
    for ch, nxt in zip(label, label[1:] + " "):
        total += -ROMAN[ch] if ROMAN.get(nxt, 0) > ROMAN[ch] else ROMAN[ch]
    return total


def table_key(label: str) -> str:
    """Id suffix: 'II' -> '2', 'B.2' -> 'B2', '3' -> '3'."""
    return str(roman_to_int(label)) if re.fullmatch(r"[IVXL]+", label) else label_key(label)


def _caption(block: dict, m: re.Match, blocks: list[dict]) -> tuple[str, pymupdf.Rect]:
    """Caption text and area. A label that stands alone takes the lines (or the block) right below it."""
    rect = pymupdf.Rect(block["rect"])
    text = squash(block["text"][m.end():])
    if text or m.re is CAPTION_RE:
        return text, rect
    below = [b for b in blocks if b is not block and 0 <= b["rect"].y0 - rect.y1 < 14
             and b["rect"].x0 < rect.x1 and b["rect"].x1 > rect.x0]
    if below:
        nxt = min(below, key=lambda b: b["rect"].y0)
        return squash(nxt["text"]), rect | nxt["rect"]
    return "", rect


def _rules(page: pymupdf.Page) -> list[pymupdf.Rect]:
    rects = (pymupdf.Rect(d["rect"]) for d in page.get_drawings())
    return sorted((r for r in rects if r.height < 2 and r.width > 40), key=lambda r: r.y0)


def _table_rect(caption: pymupdf.Rect, rules: list[pymupdf.Rect], x0: float, x1: float,
                others: list[pymupdf.Rect]) -> tuple[pymupdf.Rect | None, str]:
    """Area between the first and last full-width rule below the caption; falls back to above it.

    Only rules as wide as the table count (an underlined word in a caption is not a table rule), and the
    table never extends past the neighbouring caption on that side.
    """
    in_col = [r for r in rules if r.x1 > x0 + 2 and r.x0 < x1 - 2]
    below_limit = min((o.y0 for o in others if o.y0 > caption.y1 and o.x1 > x0 and o.x0 < x1), default=float("inf"))
    above_limit = max((o.y1 for o in others if o.y1 < caption.y0 and o.x1 > x0 and o.x0 < x1), default=float("-inf"))
    for side, group in (("below", [r for r in in_col if caption.y1 - 2 <= r.y0 and r.y1 <= below_limit]),
                        ("above", [r for r in in_col if above_limit <= r.y0 and r.y1 <= caption.y0 + 2])):
        if not group:
            continue
        widest = max(r.width for r in group)
        full = [r for r in group if r.width >= 0.9 * widest]
        if len(full) >= 2 and full[-1].y1 - full[0].y0 < 600:
            ys = [r.y0 for r in full] + [r.y1 for r in full]
            return pymupdf.Rect(min(r.x0 for r in full) - 2, min(ys) - 2, max(r.x1 for r in full) + 2, max(ys) + 2), side
    return None, ""


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
    def column_of(x0: float, x1: float) -> int:
        overlap = [max(0.0, min(x1, c1) - max(x0, c0)) for c0, c1 in cols]
        if max(overlap) > 0:
            return overlap.index(max(overlap))
        return min(range(width), key=lambda k: max(cols[k][0] - x1, x0 - cols[k][1]))  # nearest column edge

    grid = []
    for line in _lines(page.get_text("words", clip=rect)):  # word by word, so tight header rows split correctly
        cells = [""] * width
        for w in line:
            i = column_of(w[0], w[2])
            cells[i] = (cells[i] + " " + w[4]).strip()
        grid.append(_fix_arrows([clean(c) for c in cells]))
    if grid and sum(len(c) for c in grid[0]) <= 2:  # a sliver of the caption line above the top rule
        grid = grid[1:]
    return grid


def _fix_arrows(cells: list[str]) -> list[str]:
    """'BLEU', '↑ROUGE-L' -> 'BLEU ↑', 'ROUGE-L': a direction arrow belongs to the header before it."""
    for i in range(1, len(cells)):
        m = re.match(r"^([↑↓])\s*(.+)$", cells[i])
        if m and cells[i - 1] and cells[i - 1][-1] not in "↑↓":
            cells[i - 1], cells[i] = f"{cells[i - 1]} {m.group(1)}", m.group(2)
    return cells


def bold_numbers(page: pymupdf.Page, rect: pymupdf.Rect) -> list[str]:
    found: set[str] = set()
    for b in page.get_text("dict", clip=rect)["blocks"]:
        for ln in b.get("lines", []):
            for s in ln["spans"]:
                if s["flags"] & 16 or re.search(r"bold|medi", s["font"], re.I):
                    found |= numbers_in(s["text"])
    return sorted(found)


def detect_tables(doc: pymupdf.Document, appendix_start: int | None = None) -> list[dict]:
    """One entry per 'Table N' caption: key, label (as printed), page (1-based), caption, rect (or None), side."""
    found: dict[str, dict] = {}
    for page in doc:
        blocks = text_blocks(page)
        rules = None
        for b in blocks:
            m = caption_match(b["text"])
            if not m:
                continue
            key = unique_key(table_key(m.group(1)), page.number + 1, found, appendix_start)
            if key in found:
                continue
            rules = _rules(page) if rules is None else rules
            caption, cap_rect = _caption(b, m, blocks)
            others = [o["rect"] for o in blocks if o is not b and not cap_rect.contains(o["rect"])
                      and (caption_match(o["text"]) or FIG_CAPTION_RE.match(o["text"]))]
            rect, side = _table_rect(cap_rect, rules, *column_range(cap_rect, blocks), others)
            found[key] = {"key": key, "label": m.group(1), "page": page.number + 1, "caption": caption,
                          "rect": rect, "side": side}
    return [found[k] for k in sorted(found, key=label_order)]
