"""Table ranking and the link-button catalogue. HTML is produced in site.py by cloning template blocks."""
from __future__ import annotations

import re

from .common import direction_for

CELL_NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
# Bulma classes that ship with the template; nothing here is defined by this tool.
TABLE_CLASSES = ["table", "is-fullwidth", "is-hoverable"]
TABLE_WRAPPER_CLASS = "table-container"
# Captions are paragraphs, never headings: one size below the body text (Bulma's is-size-6 equals it).
CAPTION_CLASSES = ["is-size-7", "has-text-grey-dark"]
CAPTION_SELECTOR = "p." + ".".join(CAPTION_CLASSES)
BODY_TEXT_SELECTOR = "section.section .content p:not(.is-size-7)"
CAPTION_LABEL = r"^\s*(Figure|Fig\.|Table|Equation|Eq\.)\s*\(?[A-Z]?\.?\d+\)?\s*[.:]"
CAPTION_GAP = {"below": "mt-2", "above": "mb-2"}  # between a caption and its image, table or equation
NARROW_ROW, NARROW_COLUMN = ["columns"], ["column", "is-8", "is-offset-2"]  # a single-column figure
MATHJAX_SRC = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"
LINK_KINDS = {  # kind -> (label, Font Awesome / Academicons icon class shipped with the template)
    "paper": ("Paper", "fas fa-file-pdf"),
    "arxiv": ("arXiv", "ai ai-arxiv"),
    "code": ("Code", "fab fa-github"),
    "video": ("Video", "fab fa-youtube"),
    "data": ("Data", "far fa-images"),
    "dataset": ("Dataset", "fas fa-database"),
    "model": ("Model", "fas fa-cube"),
    "demo": ("Demo", "fas fa-desktop"),
}


def link_spec(kind: str) -> tuple[str, str]:
    return LINK_KINDS.get(kind, (kind.replace("-", " ").title(), "fas fa-link"))


def cell_number(cell: str) -> float | None:
    m = CELL_NUM_RE.search(cell.replace("−", "-"))
    return float(m.group().replace(",", "")) if m else None


def _mark(values: dict, direction: str, marks: dict) -> None:
    """Mark best and second best among {(row, col): number}. Ties share a mark."""
    distinct = sorted(set(values.values()), reverse=direction == "higher")
    if len(values) < 2 or len(distinct) < 2:
        return
    for key, v in values.items():
        if v == distinct[0]:
            marks[key] = "best"
        elif v == distinct[1] and len(distinct) > 2:  # not when it is also the worst
            marks[key] = "second"


def rank_cells(table: dict) -> dict[tuple[int, int], str]:
    """(row, col) -> 'best' | 'second', computed from the numbers and table["directions"].

    metrics_in == "columns": each value column is a metric, compared down the rows (per row group).
    metrics_in == "rows":    each row is a metric, compared across the value columns.
    """
    marks: dict[tuple[int, int], str] = {}
    rows, cols = table["rows"], table["columns"]
    directions = table.get("directions") or {}
    value_cols = [c for c, col in enumerate(cols) if col.get("role") == "value"]

    def num(i: int, c: int) -> float | None:
        return cell_number(rows[i]["cells"][c]) if c < len(rows[i]["cells"]) else None

    if table.get("metrics_in") == "rows":
        for i, row in enumerate(rows):
            if d := direction_for(row["cells"][0], directions):
                _mark({(i, c): v for c in value_cols if (v := num(i, c)) is not None}, d, marks)
    elif table.get("metrics_in") == "columns":
        scopes: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            scopes.setdefault(r.get("group", ""), []).append(i)
        for c in value_cols:
            if d := direction_for(cols[c]["label"], directions):
                for idxs in scopes.values():
                    _mark({(i, c): v for i in idxs if (v := num(i, c)) is not None}, d, marks)
    return marks
