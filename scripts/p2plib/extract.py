"""Step 1, extract: the PDF -> extracted/ (figure and table images, extracted.json, contact_sheet.png)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from . import figures, pdf_text, tables
from .common import Build, P2PError

MARKER_RE = re.compile(r"^[\d,\s*†‡§¶]+$")
ENGINE = "pymupdf-caption-anchored"


def pdf_sha1(pdf: Path) -> str:
    return hashlib.sha1(pdf.read_bytes()).hexdigest()


# ---------- title, authors, affiliations from page 1 ----------

def _header_blocks(page: pymupdf.Page) -> list[list[dict]]:
    """Text blocks above the abstract, each as a flat list of spans."""
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        spans = []
        for ln in b["lines"]:
            spans += ln["spans"]
            if ln["spans"]:  # a line break is a word break
                spans.append({"text": " ", "size": ln["spans"][-1]["size"], "flags": 0, "font": ""})
        text = "".join(s["text"] for s in spans).strip()
        if re.match(r"^abstract\b", text, re.I):
            break
        if text:
            blocks.append(spans)
    return blocks


def _title(blocks: list[list[dict]]) -> tuple[str, int]:
    """Title = the run of blocks set in the largest font. Returns (title, index of the next block)."""
    if not blocks:
        return "", 0
    biggest = max(s["size"] for spans in blocks for s in spans)
    idx = [i for i, spans in enumerate(blocks) if max(s["size"] for s in spans) > biggest - 0.5]
    title = " ".join(pdf_text.squash("".join(s["text"] for s in blocks[i])) for i in idx)
    return title, idx[-1] + 1


def _is_marker(span: dict, body_size: float) -> bool:
    return span["size"] < 0.85 * body_size and bool(MARKER_RE.match(span["text"].strip() or "x"))


SYMBOLS = "⋆*†‡§¶"
SYMBOL_RUN = re.compile(f"([{re.escape(SYMBOLS)}]+)")


def _parse_authors(spans: list[dict]) -> list[dict]:
    """Names with their affiliation numbers and footnote symbols (⋆, *, †, ...), in printed order."""
    body = max(s["size"] for s in spans)
    authors, name, marks = [], "", ""

    def flush() -> None:
        nonlocal name, marks
        clean = pdf_text.squash(re.sub(r"\band\b", " ", name)).strip(" ,;")
        if clean:
            authors.append({"name": clean, "affiliations": [int(n) for n in re.findall(r"\d+", marks)],
                            "symbols": "".join(c for c in marks if c in SYMBOLS)})
        name, marks = "", ""

    for s in spans:
        if _is_marker(s, body):
            marks += s["text"]
            continue
        for part in re.split(r"(,|;|\band\b)", s["text"]):
            if part in (",", ";", "and"):
                flush()
                continue
            for piece in SYMBOL_RUN.split(part):
                if SYMBOL_RUN.fullmatch(piece):
                    marks += piece
                elif piece.strip():
                    if marks:
                        flush()
                    name += piece
                else:
                    name += piece
    flush()
    return authors


def _symbol_legend(page_text: str) -> dict[str, str]:
    """Footnotes that explain author symbols, e.g. {'⋆': 'These authors contributed equally', '⋆⋆': 'Corresponding author'}."""
    legend = {}
    for line in page_text.splitlines():
        if m := re.match(rf"^\s*([{re.escape(SYMBOLS)}]+)\s*(\S.*)$", line):
            legend[m.group(1)] = m.group(2).strip()
    return legend


def _apply_symbols(authors: list[dict], legend: dict[str, str]) -> None:
    """Turn footnote symbols into corresponding / equal_contribution flags.

    With a footnote legend the symbol's meaning decides. Without one, a lone * means corresponding author.
    """
    for a in authors:
        symbols = a.pop("symbols", "")
        meaning = legend.get(symbols, "")
        a["corresponding"] = "orrespond" in meaning or (bool(symbols) and not meaning and symbols == "*")
        a["equal_contribution"] = bool(re.search(r"equal", meaning, re.I))


def _parse_affiliations(spans: list[dict]) -> list[dict]:
    body = max(s["size"] for s in spans)
    out: list[dict] = []
    for s in spans:
        if _is_marker(s, body) and re.search(r"\d", s["text"]):
            out.append({"index": int(re.search(r"\d+", s["text"]).group()), "name": ""})
        elif out:
            out[-1]["name"] += s["text"]
    for a in out:  # e-mail lines that follow the last affiliation are not part of it
        a["name"] = re.split(r"\{|\S+@", pdf_text.squash(a["name"]))[0].rstrip(".,; ")
    return [a for a in out if a["name"]]


def _header(page: pymupdf.Page) -> dict:
    """Best effort; anything that cannot be parsed stays empty and becomes a TODO in content.json."""
    blocks = _header_blocks(page)
    title, nxt = _title(blocks)
    authors, affiliations = [], []
    for spans in blocks[nxt:]:
        body = max(s["size"] for s in spans)
        first = next((s for s in spans if s["text"].strip()), spans[0])
        if _is_marker(first, body) and re.search(r"\d", first["text"]):
            affiliations += _parse_affiliations(spans)
        elif not affiliations:
            authors += _parse_authors(spans)
    _apply_symbols(authors, _symbol_legend(pdf_text.clean(page.get_text("text"))))
    return {"title": title, "authors": authors, "affiliations": affiliations}


def _keywords(pdf: pdf_text.PdfText) -> list[str]:
    lines = "\n".join(pdf.pages[:2]).splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^\s*keywords?\b", ln, re.I)), None)
    if start is None:
        return []
    chunk = [re.sub(r"^\s*keywords?\s*[:.—-]*\s*", "", lines[start], flags=re.I)]
    for ln in lines[start + 1:start + 4]:
        if not ln.strip() or re.match(r"^\s*(\d+\.?\s*)?([A-Z][a-z]+)?\s*$", ln) or re.match(r"^\s*\d+\s+\w", ln):
            break
        chunk.append(ln)
    return [k.strip(" .") for k in re.split(r"[,;·]", pdf_text.squash(" ".join(chunk))) if k.strip(" .")]


# ---------- figures and tables ----------

def _override_rect(doc: pymupdf.Document, key: str, ov: dict) -> tuple[pymupdf.Page, pymupdf.Rect]:
    if not 1 <= ov["page"] <= len(doc):
        raise P2PError(f"crop_overrides.{key}: page {ov['page']} is outside the PDF (1-{len(doc)}).")
    page = doc[ov["page"] - 1]
    rect = pymupdf.Rect(ov["bbox"]) & page.rect
    if rect.is_empty or rect.width < 10 or rect.height < 10:
        raise P2PError(f"crop_overrides.{key}: bbox {ov['bbox']} is empty or outside page {ov['page']} "
                       f"({page.rect.width:.0f} x {page.rect.height:.0f} pt).")
    return page, rect


def _bbox(rect: pymupdf.Rect) -> list[float]:
    return [round(v, 1) for v in rect]


def _extract_figures(pdf: Path, doc: pymupdf.Document, build: Build, overrides: dict) -> list[dict]:
    detected = {f["number"]: f for f in figures.detect_figures(doc)}
    for key in overrides:  # an override can add a figure the detector missed
        if m := re.fullmatch(r"fig(\d+)", key):
            detected.setdefault(int(m.group(1)), {"number": int(m.group(1)), "caption": "", "rect": None})
    out = []
    for number in sorted(detected):
        d, key = detected[number], f"fig{number}"
        entry = {"id": key, "number": number, "caption": d["caption"], "fallback": False}
        if key in overrides:
            page, rect = _override_rect(doc, key, overrides[key])
            img, entry["method"], entry["status"] = figures.trim(figures.render_clip(page, rect)), \
                "manual crop_override, rendered at 300 dpi", "ok (manual)"
        elif d["rect"] is None:
            out.append({**entry, "page": d["page"], "bbox": None, "file": None, "status": "WARN: no graphic found near "
                        "the caption; set a crop with `recrop`", "method": "caption found, area not detected"})
            continue
        else:
            page, rect = doc[d["page"] - 1], d["rect"]
            original = figures.embedded_original(pdf, doc, page, rect)
            if original:
                img, entry["method"] = original
            else:
                img = figures.trim(figures.render_clip(page, rect))
                entry["method"] = f"graphics {d['side']} the caption, rendered at 300 dpi, margins trimmed"
            entry["status"] = "ok"
        out.append({**entry, "page": page.number + 1, "bbox": _bbox(rect), **figures.save_image(img, build.extracted, key)})
    if not any(f["file"] for f in out):  # no captioned figure at all: top half of page 1
        r = doc[0].rect
        rect = pymupdf.Rect(r.x0, r.y0, r.x1, r.y0 + r.height / 2)
        img = figures.trim(figures.render_clip(doc[0], rect))
        out = [{"id": "fig1", "number": 1, "caption": "", "fallback": True, "page": 1, "bbox": _bbox(rect),
                "method": "fallback: top half of page 1 (no figure caption found)", "status": "WARN: fallback",
                **figures.save_image(img, build.extracted, "fig1")}]
    return out


def _verified(grid: list[list[str]], page_text: str) -> bool:
    """A grid may be shown as HTML only if it is a real grid and every number in it is in the page's text layer."""
    rows = [r for r in grid if any(c.strip() for c in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return False
    cells = " ".join(c for r in rows for c in r)
    return bool(pdf_text.numbers_in(cells)) and pdf_text.numbers_in(cells) <= pdf_text.numbers_in(page_text)


def _extract_tables(doc: pymupdf.Document, build: Build, overrides: dict, pages: list[str]) -> list[dict]:
    out = []
    for d in tables.detect_tables(doc):
        key = f"table{d['label']}"
        entry = {"id": key, "number": d["label"], "caption": d["caption"]}
        if key in overrides:
            page, rect = _override_rect(doc, key, overrides[key])
            entry.update(method="manual crop_override, rendered at 300 dpi", status="ok (manual)")
        elif d["rect"] is None:
            out.append({**entry, "page": d["page"], "bbox": None, "file": None, "grid": [], "bold_numbers": [],
                        "html_verified": False, "method": "caption found, rules not detected",
                        "status": "WARN: table area not detected; set a crop with `recrop`"})
            continue
        else:
            page, rect = doc[d["page"] - 1], d["rect"]
            entry.update(method=f"area between the rules {d['side']} the caption, rendered at 300 dpi", status="ok")
        grid = tables.read_grid(page, rect)
        image = figures.save_image(figures.trim(figures.render_clip(page, rect)), build.extracted, key)
        out.append({**entry, "page": page.number + 1, "bbox": _bbox(rect), **image, "grid": grid,
                    "bold_numbers": tables.bold_numbers(page, rect),
                    "html_verified": _verified(grid, pages[page.number])})
    return out


# ---------- contact sheet and grid helper ----------

def _contact_sheet(build: Build, items: list[dict]) -> None:
    """All crops on one image, labelled, for a quick visual check."""
    items = [i for i in items if i.get("file")]
    cell_w, cell_h, pad, cols = 420, 340, 12, 4
    rows = max(1, -(-len(items) // cols))
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (235, 235, 235))
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(items):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        with Image.open(build.extracted / f["file"]) as im:
            im = im.convert("RGB")
            im.thumbnail((cell_w - 2 * pad, cell_h - 2 * pad - 20))
            sheet.paste(im, (x + pad, y + pad + 20))
        draw.rectangle((x + 2, y + 2, x + cell_w - 3, y + cell_h - 3), outline=(190, 190, 190))
        draw.text((x + pad, y + 6), f'{f["id"]}  p.{f["page"]}  {f["width"]}x{f["height"]}  {f["bytes"] // 1024} KB',
                  fill=(20, 20, 20))
    sheet.save(build.contact_sheet, optimize=True)


def page_grid(pdf: Path, build: Build, page_no: int) -> Path:
    """Render one page with a coordinate grid in PDF points, and the current crops outlined in red."""
    with pymupdf.open(pdf) as doc:
        if not 1 <= page_no <= len(doc):
            raise P2PError(f"Page {page_no} is outside the PDF (1-{len(doc)}).")
        page = doc[page_no - 1]
        zoom = 2.0  # 144 dpi: 1 pt = 2 px
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        w, h = page.rect.width, page.rect.height
    draw = ImageDraw.Draw(img, "RGBA")
    for x in range(0, int(w) + 1, 10):
        major = x % 50 == 0
        draw.line((x * zoom, 0, x * zoom, img.height), fill=(0, 90, 255, 110 if major else 35), width=1)
        if major:
            draw.text((x * zoom + 2, 2), str(x), fill=(0, 60, 200, 255))
    for y in range(0, int(h) + 1, 10):
        major = y % 50 == 0
        draw.line((0, y * zoom, img.width, y * zoom), fill=(0, 90, 255, 110 if major else 35), width=1)
        if major:
            draw.text((2, y * zoom + 2), str(y), fill=(0, 60, 200, 255))
    if build.extracted_json.is_file():
        data = json.loads(build.extracted_json.read_text())
        for item in data["figures"] + data["tables"]:
            if item["page"] == page_no and item.get("bbox"):
                x0, y0, x1, y1 = (v * zoom for v in item["bbox"])
                draw.rectangle((x0, y0, x1, y1), outline=(220, 0, 0, 255), width=2)
                draw.text((x0 + 3, y0 + 3), f'{item["id"]} {item["bbox"]}', fill=(220, 0, 0, 255))
    build.extracted.mkdir(parents=True, exist_ok=True)
    out = build.extracted / f"grid_p{page_no}.png"
    img.save(out, optimize=True)
    print(f"{out}\npage {page_no}: {w:.0f} x {h:.0f} pt; grid lines every 10 pt, labelled every 50 pt; "
          "origin top-left; bbox = x0,y0,x1,y1")
    return out


# ---------- step ----------

def run(pdf_path: Path, build: Build, overrides: dict) -> dict:
    sha = pdf_sha1(pdf_path)
    if build.extracted_json.is_file():
        old = json.loads(build.extracted_json.read_text())
        files = [i["file"] for i in old["figures"] + old["tables"] if i.get("file")]
        if (old.get("pdf_sha1"), old.get("crop_overrides"), old.get("engine")) == (sha, overrides, ENGINE) \
                and all((build.extracted / f).is_file() for f in files):
            print("  extract: up to date")
            return old
    build.extracted.mkdir(parents=True, exist_ok=True)
    for old_file in build.extracted.iterdir():
        if old_file.is_file() and not old_file.name.startswith("grid_"):  # keep the grid helper pages
            old_file.unlink()
    pdf = pdf_text.load(pdf_path)
    with pymupdf.open(pdf_path) as doc:
        header = _header(doc[0])
        figs = _extract_figures(pdf_path, doc, build, overrides)
        tabs = _extract_tables(doc, build, overrides, pdf.pages)
    for item in figs + tabs:
        item["caption"] = pdf_text.dehyphenate(item["caption"], pdf.full)
    data = {"pdf_sha1": sha, "pdf_name": pdf_path.name, "engine": ENGINE, "crop_overrides": overrides,
            "n_pages": len(pdf.pages), **header, "abstract": pdf_text.extract_abstract(pdf) or "",
            "keywords": [pdf_text.dehyphenate(k, pdf.full) for k in _keywords(pdf)], "figures": figs, "tables": tabs, "pages": pdf.pages}
    build.extracted_json.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    _contact_sheet(build, figs + tabs)
    warns = [i["id"] for i in figs + tabs if i["status"].startswith("WARN")]
    print(f"  extract: {len(figs)} figure(s), {len(tabs)} table(s), {len(header['authors'])} author(s)"
          + (f"; WARN: {', '.join(warns)}" if warns else ""))
    return data
