"""HTML components that must not be guessed: figures, tables (with ranking), link buttons, extra CSS."""
from __future__ import annotations

import re
from html import escape

from .common import direction_for

CELL_NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
BUTTON_CLASS = "external-link button is-normal is-rounded is-dark"
LINK_KINDS = {  # kind -> (label, icon class)
    "paper": ("Paper", "fas fa-file-pdf"),
    "arxiv": ("arXiv", "ai ai-arxiv"),
    "code": ("Code", "fab fa-github"),
    "video": ("Video", "fab fa-youtube"),
    "data": ("Data", "fas fa-database"),
    "dataset": ("Dataset", "fas fa-database"),
    "model": ("Model", "fas fa-cube"),
    "demo": ("Demo", "fas fa-desktop"),
}

EXTRA_CSS = """

/* --- paper2page additions --- */
.paper-figure {
  margin: 1.5rem auto;
  text-align: center;
}

.paper-figure img {
  max-width: 100%;
  max-height: 720px;
  width: auto;
  height: auto;
  border-radius: 5px;
}

.paper-figure figcaption {
  margin-top: 0.5rem;
  font-size: 0.875rem;
  color: #555;
}

.teaser .paper-figure {
  margin-top: 0;
}

.paper-table {
  margin: 1.5rem auto;
}

.paper-table table {
  margin: 0 auto;
  width: auto;
  font-size: 0.95rem;
}

.paper-table caption {
  caption-side: top;
  padding-bottom: 0.5rem;
  font-size: 0.875rem;
  color: #555;
  text-align: center;
}

.paper-table th,
.paper-table td {
  text-align: center !important;
  vertical-align: middle !important;
  white-space: nowrap;
}

.paper-table th.row-label,
.paper-table td.row-label {
  text-align: left !important;
}
"""


# ---------- link buttons ----------

def render_button(kind: str, url: str) -> str:
    label, icon = LINK_KINDS.get(kind, (kind.replace("-", " ").title(), "fas fa-link"))
    icon_html = f'<span class="icon"><i class="{icon}"></i></span>'
    if url == "soon":
        return (f'<span class="link-block"><a class="{BUTTON_CLASS}" disabled aria-disabled="true" '
                f'title="Coming soon">{icon_html}<span>{escape(label)} (coming soon)</span></a></span>')
    return (f'<span class="link-block"><a href="{escape(url, quote=True)}" class="{BUTTON_CLASS}">'
            f'{icon_html}<span>{escape(label)}</span></a></span>')


def render_links(links: dict[str, str], host_pdf: bool) -> str:
    """Paper button first (paper.pdf, or 'coming soon' when the PDF is not hosted), then the given links."""
    buttons = [render_button("paper", "./paper.pdf" if host_pdf else "soon")]
    buttons += [render_button(kind, url) for kind, url in links.items() if kind != "paper"]
    return "\n".join(buttons)


# ---------- figures ----------

def render_figure(fig: dict, caption: str, alt: str) -> str:
    src = f"./static/images/{fig['file']}"
    label = "" if fig.get("fallback") else f"<strong>Figure {fig['number']}.</strong> "
    cap = f"<figcaption>{label}{escape(caption)}</figcaption>" if (caption or label) else ""
    return (f'<figure class="paper-figure">'
            f'<a href="{src}" target="_blank" rel="noopener" title="Open full size">'
            f'<img src="{src}" alt="{escape(alt or caption, quote=True)}" loading="lazy" '
            f'width="{fig["width"]}" height="{fig["height"]}"></a>{cap}</figure>')


# ---------- tables ----------

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


def _header_rows(table: dict) -> str:
    cols = table["columns"]
    has_groups = any(c.get("group") for c in cols)
    lead = ""
    if any(r.get("group") for r in table["rows"]):
        span = ' rowspan="2"' if has_groups else ""
        lead = f'<th class="row-label" scope="col"{span}>{escape(table.get("row_group_label", ""))}</th>'
    cls = lambda i: ' class="row-label"' if cols[i].get("role") == "label" else ""  # noqa: E731
    if not has_groups:
        cells = "".join(f'<th{cls(i)} scope="col">{escape(c["label"])}</th>' for i, c in enumerate(cols))
        return f"<tr>{lead}{cells}</tr>"
    top, bottom, i = [lead], [], 0
    while i < len(cols):
        g = cols[i].get("group", "")
        if not g:
            top.append(f'<th{cls(i)} scope="col" rowspan="2">{escape(cols[i]["label"])}</th>')
            i += 1
            continue
        j = i
        while j < len(cols) and cols[j].get("group", "") == g:
            bottom.append(f'<th scope="col">{escape(cols[j]["label"])}</th>')
            j += 1
        top.append(f'<th scope="colgroup" colspan="{j - i}">{escape(g)}</th>')
        i = j
    return f"<tr>{''.join(top)}</tr><tr>{''.join(bottom)}</tr>"


def _body_rows(table: dict, marks: dict) -> str:
    rows = table["rows"]
    with_groups = any(r.get("group") for r in rows)
    out = []
    for i, row in enumerate(rows):
        tds = []
        if with_groups and (i == 0 or rows[i - 1].get("group") != row.get("group")):
            span = 1
            while i + span < len(rows) and rows[i + span].get("group") == row.get("group"):
                span += 1
            tds.append(f'<th class="row-label" scope="rowgroup" rowspan="{span}">{escape(row.get("group", ""))}</th>')
        for c, cell in enumerate(row["cells"]):
            text = escape(cell)
            if marks.get((i, c)) == "best":
                text = f"<strong>{text}</strong>"
            elif marks.get((i, c)) == "second":
                text = f"<u>{text}</u>"
            cls = ' class="row-label"' if table["columns"][c].get("role") == "label" else ""
            tds.append(f"<td{cls}>{text}</td>")
        out.append(f"<tr>{''.join(tds)}</tr>")
    return "\n".join(out)


def render_table(table: dict) -> str:
    marks = rank_cells(table)
    caption = f'<strong>Table {table["number"]}.</strong> {escape(table["caption"])}'
    if marks:
        caption += " Best in <strong>bold</strong>"
        caption += ", second best <u>underlined</u>." if "second" in marks.values() else "."
    return (f'<div class="paper-table"><div class="table-container">'
            f'<table class="table is-hoverable"><caption>{caption}</caption>'
            f"<thead>{_header_rows(table)}</thead><tbody>\n{_body_rows(table, marks)}\n</tbody></table></div></div>")
