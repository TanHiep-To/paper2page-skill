"""Caption-anchored figure detection, cropping, and image encoding (PyMuPDF only, no ML)."""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageChops

from .pdf_text import squash

# "Figure 3", "Fig. 3", and appendix labels with their own numbering: "Figure A1", "Fig. S3", "Figure B.2".
CAPTION_RE = re.compile(r"^\s*(?:Fig\.|Figure|FIGURE|Fig)\s*([A-Z]?\.?\d+)\s*[:.|]")
DPI = 300
MAX_BYTES = 500 * 1024
PAD_PX = 12


def label_key(label: str) -> str:
    """Id suffix of a printed label: 'A.1' -> 'A1', '3' -> '3'."""
    return re.sub(r"[^A-Za-z0-9]", "", label)


def label_number(label: str) -> int | str:
    """Main-paper labels are integers; appendix labels ('A1', 'S3') stay as printed."""
    return int(label) if label.isdigit() else label


def label_order(key: str) -> tuple:
    """Main figures and tables first, in numeric order; appendix labels after them."""
    return (0, int(key), "") if key.isdigit() else (1, 0, key)


def unique_key(key: str, page_no: int, found: dict[str, dict], appendix_start: int | None) -> str:
    """An appendix may restart at 'Figure 1': that one becomes 'App1' and keeps its printed label."""
    if appendix_start and page_no >= appendix_start and key in found and found[key]["page"] < appendix_start:
        return f"App{key}"
    return key


# ---------- page geometry ----------

def text_blocks(page: fitz.Page) -> list[dict]:
    blocks = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        text = "\n".join("".join(s["text"] for s in ln["spans"]) for ln in b["lines"]).strip()
        if text:
            blocks.append({"rect": fitz.Rect(b["bbox"]), "text": text, "lines": len(b["lines"])})
    return blocks


def _graphics(page: fitz.Page) -> list[fitz.Rect]:
    """Bounding boxes of raster images and vector drawings (page-sized backgrounds dropped)."""
    area = abs(page.rect)
    rects = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    rects += [fitz.Rect(d["rect"]) for d in page.get_drawings()]
    return [r for r in rects if (r.width > 1 or r.height > 1) and abs(r) < 0.9 * area]


def column_range(caption: fitz.Rect, blocks: list[dict]) -> tuple[float, float]:
    """x-range of the column holding the caption: full text width, or one column of a two-column page."""
    left = min(b["rect"].x0 for b in blocks)
    right = max(b["rect"].x1 for b in blocks)
    mid = (left + right) / 2
    if _is_two_column(blocks, mid):
        if caption.x1 < mid + 6:
            return left, mid
        if caption.x0 > mid - 6:
            return mid, right
    return left, right


def _is_two_column(blocks: list[dict], mid: float) -> bool:
    """True when most multi-line text blocks stay on one side of the page middle."""
    body = [b["rect"] for b in blocks if b["lines"] >= 3]
    if len(body) < 4:
        return False
    return sum(1 for r in body if r.x1 < mid + 6 or r.x0 > mid - 6) / len(body) > 0.7


def _is_body_text(block: dict, col_width: float, graphics: list[fitz.Rect]) -> bool:
    """A paragraph or caption that bounds the figure (not a label inside the graphic)."""
    r = block["rect"]
    if any(g.intersects(r) and abs(g & r) > 0.5 * abs(r) for g in graphics):
        return False
    return (block["lines"] >= 2 and r.width > 0.5 * col_width) or r.width > 0.8 * col_width


def _figure_rect(page: fitz.Page, caption: dict, blocks: list[dict], graphics: list[fitz.Rect],
                 above: bool) -> fitz.Rect | None:
    """Graphics between the caption and the nearest body text on one side, with labels and sub-captions."""
    cap = caption["rect"]
    x0, x1 = column_range(cap, blocks)
    in_col = lambda r: r.x1 > x0 + 2 and r.x0 < x1 - 2  # noqa: E731
    col_graphics = [g for g in graphics if in_col(g)]
    if above:
        limit = page.rect.y0 + 20
        for b in blocks:
            r = b["rect"]
            if b is not caption and r.y1 <= cap.y0 + 1 and in_col(r) and _is_body_text(b, x1 - x0, col_graphics):
                limit = max(limit, r.y1)
        region = fitz.Rect(x0, limit + 1, x1, cap.y0 - 1)
    else:
        limit = page.rect.y1 - 20
        for b in blocks:
            r = b["rect"]
            if b is not caption and r.y0 >= cap.y1 - 1 and in_col(r) and _is_body_text(b, x1 - x0, col_graphics):
                limit = min(limit, r.y0)
        region = fitz.Rect(x0, cap.y1 + 1, x1, limit - 1)
    if region.height < 15:
        return None
    inside = [g & region for g in col_graphics if g.y1 > region.y0 and g.y0 < region.y1 and g.height + g.width > 4]
    inside = [g for g in inside if not g.is_empty]
    if not inside:
        return None
    box = fitz.Rect(inside[0])
    for g in inside[1:]:
        box |= g
    for b in blocks:  # panel labels and (a)(b)(c) sub-captions stay with the figure
        r = b["rect"]
        if b is not caption and in_col(r) and r.y0 >= box.y0 - 1 and r.y1 <= region.y1 + 1 and r.y0 >= region.y0 - 1:
            box |= r & region
    return (box + (-3, -3, 3, 3)) & fitz.Rect(page.rect.x0, region.y0, page.rect.x1, region.y1)


def detect_figures(doc: fitz.Document, appendix_start: int | None = None) -> list[dict]:
    """One entry per 'Figure N' caption: key, number, page (1-based), caption, rect (or None), side."""
    found: dict[str, dict] = {}
    for page in doc:
        blocks = text_blocks(page)
        captions = [(m.group(1), b) for b in blocks if (m := CAPTION_RE.match(b["text"]))]
        if not captions:
            continue
        graphics = _graphics(page)
        for label, cap in captions:
            number = unique_key(label_key(label), page.number + 1, found, appendix_start)
            if number in found and found[number]["rect"] is not None:
                continue
            rect, side = _figure_rect(page, cap, blocks, graphics, above=True), "above"
            if rect is None or rect.height < 20 or rect.width < 20:
                rect, side = _figure_rect(page, cap, blocks, graphics, above=False), "below"
            if rect is not None and (rect.height < 20 or rect.width < 20):
                rect = None
            found[number] = {"key": number, "number": label_number(label), "page": page.number + 1,
                             "caption": squash(cap["text"]), "rect": rect, "side": side}
    return [found[k] for k in sorted(found, key=label_order)]


# ---------- embedded originals ----------

def _pdfimages_extract(pdf: Path, page_no: int, xref: int) -> Image.Image | None:
    """The image object `xref` of page `page_no` (1-based), extracted unchanged with poppler's pdfimages."""
    exe = shutil.which("pdfimages")
    if not exe:
        return None
    page = ["-f", str(page_no), "-l", str(page_no)]
    listing = subprocess.run([exe, "-list", *page, str(pdf)], capture_output=True, text=True)
    rows = [ln.split() for ln in listing.stdout.splitlines()[2:]]
    index = next((int(r[1]) for r in rows if len(r) > 10 and r[2] == "image" and r[10] == str(xref)), None)
    if listing.returncode != 0 or index is None:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        if subprocess.run([exe, "-all", *page, str(pdf), f"{tmp}/img"], capture_output=True).returncode != 0:
            return None
        files = sorted(Path(tmp).glob(f"img-{index:03d}.*"))
        try:
            return Image.open(files[0]).convert("RGB").copy() if files else None
        except OSError:
            return None


def embedded_original(pdf: Path, doc: fitz.Document, page: fitz.Page, rect: fitz.Rect) -> tuple[Image.Image, str] | None:
    """If the figure is exactly one embedded photo with more pixels than our render, return the original."""
    images = [i for i in page.get_image_info(xrefs=True) if fitz.Rect(i["bbox"]).intersects(rect)]
    if len(images) != 1 or not images[0].get("xref"):
        return None
    info, box = images[0], fitz.Rect(images[0]["bbox"])
    a, b, c, d = info["transform"][:4]
    upright = a > 0 and d > 0 and abs(b) < 1e-3 and abs(c) < 1e-3
    covers = abs(box & rect) >= 0.85 * abs(rect) and (rect + (-4, -4, 4, 4)).contains(box)
    if not upright or not covers or page.get_text("words", clip=box):
        return None  # rotated, clipped, only part of the figure, or text drawn on top of it
    if info["width"] <= 1.05 * rect.width / 72 * DPI:
        return None  # not higher resolution than the 300 dpi render
    smask = doc.extract_image(info["xref"]).get("smask")
    img = None if smask else _pdfimages_extract(pdf, page.number + 1, info["xref"])
    how = "original embedded image (pdfimages)"
    if img is None:
        img, how = _pymupdf_image(doc, info["xref"], smask), "original embedded image (PyMuPDF" + \
            (", transparency flattened on white)" if smask else ")")
    return trim(img), f"{how}, {info['width']}x{info['height']} px"


def _pymupdf_image(doc: fitz.Document, xref: int, smask: int) -> Image.Image:
    """The embedded image as RGB; pdfimages cannot apply a soft mask, so masked images are composited here."""
    pix = fitz.Pixmap(doc, xref)
    if pix.n - pix.alpha != 3:
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if not smask:
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    rgba = fitz.Pixmap(pix, fitz.Pixmap(doc, smask))
    img = Image.frombytes("RGBA", (rgba.width, rgba.height), rgba.samples)
    white = Image.new("RGBA", img.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, img).convert("RGB")


# ---------- rendering and encoding ----------

def render_clip(page: fitz.Page, rect: fitz.Rect, dpi: int = DPI) -> Image.Image:
    pix = page.get_pixmap(clip=rect, dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def trim(img: Image.Image) -> Image.Image:
    """Remove near-white margins, keeping a small padding."""
    rgb = img.convert("RGB")
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, (255, 255, 255)))
    bbox = diff.point(lambda v: 255 if v > 12 else 0).getbbox()
    if not bbox:
        return rgb
    l, t, r, b = bbox
    return rgb.crop((max(0, l - PAD_PX), max(0, t - PAD_PX), min(rgb.width, r + PAD_PX), min(rgb.height, b + PAD_PX)))


def _is_photographic(img: Image.Image) -> bool:
    """Photos have many distinct colours; diagrams have few flat ones."""
    small = img.convert("RGB").resize((160, 160), Image.BILINEAR)
    return len(small.getcolors(maxcolors=160 * 160) or []) > 0.45 * 160 * 160


def _encode(img: Image.Image, fmt: str, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpg":
        img.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def encode_within(img: Image.Image, fmt: str, max_bytes: int = MAX_BYTES, keep_width: int = 1400) -> tuple[bytes, Image.Image]:
    """Encode under max_bytes when possible: scale down to keep_width, then lower the JPEG quality,
    then keep scaling (never below 30% of the original). Returns (data, the image as encoded)."""
    data, final, scale = _encode(img, fmt), img, 1.0

    def shrink() -> None:
        nonlocal data, final, scale
        scale *= 0.88
        final = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
        data = _encode(final, fmt)

    while len(data) > max_bytes and final.width * 0.88 >= keep_width:
        shrink()
    if fmt == "jpg":
        for quality in (78, 70, 62, 55):
            if len(data) <= max_bytes:
                break
            data = _encode(final, fmt, quality)
    while len(data) > max_bytes and scale > 0.3:
        shrink()
    return data, final


def save_image(img: Image.Image, out_dir: Path, stem: str) -> dict:
    """PNG for diagrams, JPEG q85 for photographic content; shrink until under MAX_BYTES."""
    fmt = "jpg" if _is_photographic(img) else "png"
    data = _encode(img, fmt)
    if fmt == "png" and len(data) > MAX_BYTES and len(jpg := _encode(img, "jpg")) < len(data) / 3:
        fmt, data = "jpg", jpg  # a "diagram" full of gradients or embedded photos
    final = img
    if len(data) > MAX_BYTES:
        data, final = encode_within(img, fmt)
    for old in out_dir.glob(f"{stem}.*"):
        old.unlink()
    (out_dir / f"{stem}.{fmt}").write_bytes(data)
    return {"file": f"{stem}.{fmt}", "width": final.width, "height": final.height, "bytes": len(data)}
