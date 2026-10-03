"""Figure extraction: find 'Figure N' captions and crop the graphic above each one."""
from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageChops

from .pdf_text import squash

CAPTION_RE = re.compile(r"^\s*(?:Fig\.|Figure|FIGURE|Fig)\s*(\d+)\s*[:.|]")
DPI = 200
MAX_BYTES = 500 * 1024
PAD_PX = 12


@dataclass
class Figure:
    id: str
    number: int
    page: int  # 1-based
    caption: str
    file: str  # file name inside static/images
    width: int
    height: int
    bytes: int
    fallback: bool = False
    method: str = "cropped above its caption (200 dpi, margins trimmed)"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- page geometry ----------

def _text_blocks(page: fitz.Page) -> list[dict]:
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


def _column_range(page: fitz.Page, caption: fitz.Rect, blocks: list[dict]) -> tuple[float, float]:
    """x-range of the column holding the caption: full text width, or one column of a two-column page."""
    left = min(b["rect"].x0 for b in blocks)
    right = max(b["rect"].x1 for b in blocks)
    mid = (left + right) / 2
    gutter = 6
    if caption.x1 < mid + gutter and _is_two_column(blocks, mid):
        return left, mid
    if caption.x0 > mid - gutter and _is_two_column(blocks, mid):
        return mid, right
    return left, right


def _is_two_column(blocks: list[dict], mid: float) -> bool:
    """True when most multi-line text blocks stay on one side of the page middle."""
    body = [b["rect"] for b in blocks if b["lines"] >= 3]
    if len(body) < 4:
        return False
    one_side = sum(1 for r in body if r.x1 < mid + 6 or r.x0 > mid - 6)
    return one_side / len(body) > 0.7


def _is_body_text(block: dict, col_width: float, graphics: list[fitz.Rect]) -> bool:
    """A paragraph or caption that bounds the figure from above (not a label inside the graphic)."""
    r = block["rect"]
    if any(g.intersects(r) and abs(g & r) > 0.5 * abs(r) for g in graphics):
        return False
    return (block["lines"] >= 2 and r.width > 0.5 * col_width) or r.width > 0.8 * col_width


def _figure_rect(page: fitz.Page, caption: dict, blocks: list[dict], graphics: list[fitz.Rect]) -> fitz.Rect | None:
    cap = caption["rect"]
    x0, x1 = _column_range(page, cap, blocks)
    in_col = lambda r: r.x1 > x0 + 2 and r.x0 < x1 - 2  # noqa: E731
    col_graphics = [g for g in graphics if in_col(g)]

    top = page.rect.y0 + 20
    for b in blocks:
        r = b["rect"]
        if b is caption or r.y1 > cap.y0 + 1 or not in_col(r):
            continue
        if _is_body_text(b, x1 - x0, col_graphics):
            top = max(top, r.y1)

    region = fitz.Rect(x0, top, x1, cap.y0)
    if region.height < 15:
        return None
    inside = [g & region for g in col_graphics if g.y1 > top and g.y0 < cap.y0 and g.height + g.width > 4]
    inside = [g for g in inside if not g.is_empty]
    if not inside:
        return None  # nothing drawn above this caption (the figure may sit below it)
    box = fitz.Rect(inside[0])
    for g in inside[1:]:
        box |= g
    for b in blocks:  # labels and sub-captions that belong to the figure
        r = b["rect"]
        if b is not caption and in_col(r) and r.y0 >= box.y0 - 1 and r.y1 <= cap.y0 + 1:
            box |= r & region
    # pad a little, but never into the caption below or the paragraph above
    return (box + (-3, -3, 3, 3)) & fitz.Rect(page.rect.x0, top + 1, page.rect.x1, cap.y0 - 1)


# ---------- image encoding ----------

def _trim(img: Image.Image) -> Image.Image:
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
    colours = small.getcolors(maxcolors=160 * 160) or []
    return len(colours) > 0.45 * 160 * 160


def _encode(img: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpg":
        img.convert("RGB").save(buf, "JPEG", quality=85, optimize=True, progressive=True)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _encode_under_limit(img: Image.Image) -> tuple[bytes, str, Image.Image]:
    """PNG for diagrams, JPEG q85 for photographic figures; shrink until under MAX_BYTES."""
    fmt = "jpg" if _is_photographic(img) else "png"
    data = _encode(img, fmt)
    if fmt == "png" and len(data) > MAX_BYTES:
        jpg = _encode(img, "jpg")
        if len(jpg) < len(data) / 3:  # a "diagram" full of gradients or embedded photos
            fmt, data = "jpg", jpg
    scale = 1.0
    while len(data) > MAX_BYTES and scale > 0.4:
        scale *= 0.88
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        resized = img.resize(size, Image.LANCZOS)
        data = _encode(resized, fmt)
        if len(data) <= MAX_BYTES:
            return data, fmt, resized
    return data, fmt, img if scale == 1.0 else img.resize(
        (max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)


def _render(page: fitz.Page, rect: fitz.Rect) -> Image.Image:
    pix = page.get_pixmap(clip=rect, dpi=DPI, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _save(img: Image.Image, out_dir: Path, stem: str) -> tuple[str, int, int, int]:
    data, fmt, final = _encode_under_limit(_trim(img))
    name = f"{stem}.{fmt}"
    (out_dir / name).write_bytes(data)
    return name, final.width, final.height, len(data)


# ---------- public API ----------

def extract_figures(pdf: Path, out_dir: Path) -> list[Figure]:
    """Crop every captioned figure to out_dir/figN.{png,jpg}. Falls back to the top half of page 1."""
    out_dir.mkdir(parents=True, exist_ok=True)
    figures: dict[int, Figure] = {}
    with fitz.open(pdf) as doc:
        for page in doc:
            blocks = _text_blocks(page)
            captions = [(int(m.group(1)), b) for b in blocks if (m := CAPTION_RE.match(b["text"]))]
            if not captions:
                continue
            graphics = _graphics(page)
            for number, cap in captions:
                if number in figures:
                    continue
                rect = _figure_rect(page, cap, blocks, graphics)
                if rect is None or rect.height < 20 or rect.width < 20:
                    continue
                name, w, h, size = _save(_render(page, rect), out_dir, f"fig{number}")
                figures[number] = Figure(f"fig{number}", number, page.number + 1, squash(cap["text"]), name, w, h, size)
        if not figures:
            page = doc[0]
            r = page.rect
            name, w, h, size = _save(_render(page, fitz.Rect(r.x0, r.y0, r.x1, r.y0 + r.height / 2)), out_dir, "fig1")
            return [Figure("fig1", 1, 1, "", name, w, h, size, fallback=True,
                           method="fallback: top half of page 1 (no figure caption found)")]
    return [figures[n] for n in sorted(figures)]
