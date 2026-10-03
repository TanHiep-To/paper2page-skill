"""Step 1, extract: the PDF -> extracted/ (figure images, extracted.json, contact_sheet.png)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw

from . import figures, pdf_text, tables
from .common import Build

MARKER_RE = re.compile(r"^[\d,\s*†‡§¶]+$")


def pdf_sha1(pdf: Path) -> str:
    return hashlib.sha1(pdf.read_bytes()).hexdigest()


def read_header(pdf: Path) -> dict:
    """Title, authors and affiliations from page 1 only (cheap; used to pick the project name)."""
    with pymupdf.open(pdf) as doc:
        return _header(doc[0])


# ---------- title, authors, affiliations from page 1 ----------

def _header_blocks(page: pymupdf.Page) -> list[list[dict]]:
    """Text blocks above the abstract, each as a flat list of spans."""
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        spans = [s for ln in b["lines"] for s in ln["spans"]]
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


def _parse_authors(spans: list[dict]) -> list[dict]:
    body = max(s["size"] for s in spans)
    authors, name, marks = [], "", ""

    def flush() -> None:
        nonlocal name, marks
        clean = pdf_text.squash(re.sub(r"\band\b", " ", name)).strip(" ,;")
        if clean:
            authors.append({"name": clean.rstrip("*").strip(),
                            "affiliations": [int(n) for n in re.findall(r"\d+", marks)],
                            "corresponding": "*" in marks or clean.endswith("*")})
        name, marks = "", ""

    for s in spans:
        if _is_marker(s, body):
            marks += s["text"]
            continue
        for part in re.split(r"(,|;|\band\b)", s["text"]):
            if part in (",", ";", "and"):
                flush()
            else:
                if marks and part.strip():
                    flush()
                name += part
    flush()
    return authors


def _parse_affiliations(spans: list[dict]) -> list[dict]:
    body = max(s["size"] for s in spans)
    out: list[dict] = []
    for s in spans:
        if _is_marker(s, body) and re.search(r"\d", s["text"]):
            out.append({"index": int(re.search(r"\d+", s["text"]).group()), "name": ""})
        elif out:
            out[-1]["name"] += s["text"]
    for a in out:
        a["name"] = pdf_text.squash(a["name"]).rstrip(".,; ")
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
        elif not authors and not affiliations:
            authors = _parse_authors(spans)
    return {"title": title, "authors": authors, "affiliations": affiliations}


def _keywords(pdf: pdf_text.PdfText) -> list[str]:
    lines = "\n".join(pdf.pages[:2]).splitlines()
    start = next((i for i, ln in enumerate(lines) if re.match(r"^\s*keywords?\b", ln, re.I)), None)
    if start is None:
        return []
    chunk = [re.sub(r"^\s*keywords?\s*[:.—-]*\s*", "", lines[start], flags=re.I)]
    for ln in lines[start + 1:start + 4]:
        if not ln.strip() or re.match(r"^\s*(\d+\.?\s+)?[A-Z][a-z]+\s*$", ln) or re.match(r"^\s*\d+\s+\w", ln):
            break
        chunk.append(ln)
    return [k.strip(" .") for k in re.split(r"[,;·]", pdf_text.squash(" ".join(chunk))) if k.strip(" .")]


# ---------- contact sheet ----------

def _contact_sheet(build: Build, figs: list[dict]) -> None:
    """All cropped figures on one image, labelled, for a quick visual check."""
    cell_w, cell_h, pad, cols = 420, 340, 12, 4
    rows = max(1, -(-len(figs) // cols))
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (235, 235, 235))
    draw = ImageDraw.Draw(sheet)
    for i, f in enumerate(figs):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        with Image.open(build.extracted / f["file"]) as im:
            im = im.convert("RGB")
            im.thumbnail((cell_w - 2 * pad, cell_h - 2 * pad - 20))
            sheet.paste(im, (x + pad, y + pad + 20))
        draw.text((x + pad, y + 6), f'{f["id"]}  p.{f["page"]}  {f["width"]}x{f["height"]}  {f["bytes"] // 1024} KB',
                  fill=(20, 20, 20))
    sheet.save(build.contact_sheet, optimize=True)


# ---------- step ----------

def run(pdf_path: Path, build: Build) -> dict:
    sha = pdf_sha1(pdf_path)
    if build.extracted_json.is_file():
        old = json.loads(build.extracted_json.read_text())
        if old.get("pdf_sha1") == sha and all((build.extracted / f["file"]).is_file() for f in old["figures"]):
            print("  extract: up to date")
            return old
    if build.extracted.exists():
        shutil.rmtree(build.extracted)
    build.extracted.mkdir(parents=True)
    pdf = pdf_text.load(pdf_path)
    figs = [f.to_dict() for f in figures.extract_figures(pdf_path, build.extracted)]
    with pymupdf.open(pdf_path) as doc:
        header = _header(doc[0])
        tabs = tables.extract_tables(doc)
    for item in figs + tabs:
        item["caption"] = pdf_text.dehyphenate(item["caption"], pdf.full)
    data = {"pdf_sha1": sha, "pdf_name": pdf_path.name, "n_pages": len(pdf.pages), **header,
            "abstract": pdf_text.extract_abstract(pdf) or "", "keywords": _keywords(pdf),
            "figures": figs, "tables": tabs, "pages": pdf.pages}
    build.extracted_json.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    _contact_sheet(build, figs)
    print(f"  extract: {len(figs)} figure(s), {len(tabs)} table(s), {len(header['authors'])} author(s)")
    return data
