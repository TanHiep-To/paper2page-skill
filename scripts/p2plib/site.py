"""Step 3, render: content.json + template -> site/ (index.html, static/, paper.pdf, .nojekyll).

The page is built in Python by editing the template's own DOM, so structure and classes stay the template's.
"""
from __future__ import annotations

import json
import re
import shutil
from html import escape
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup, Comment, NavigableString

from . import render, template
from .common import Build, P2PError, is_todo

MAX_PDF_BYTES = 5 * 1024 * 1024
MARKER = ".paper2page"


def _frag(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _set_children(tag, html: str) -> None:
    tag.clear()
    tag.append(_frag(html))


def _ok(value) -> bool:
    return bool(value) and not is_todo(value)


# ---------- head, navbar, footer ----------

def _head(soup: BeautifulSoup, content: dict, og_image: str) -> None:
    soup.html["lang"] = "en"
    for script in soup.find_all("script"):
        if "googletagmanager" in (script.get("src") or "") or "gtag(" in script.get_text():
            script.decompose()
    for c in soup.head.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    soup.title.string = content["title"]
    soup.select_one('meta[name="description"]')["content"] = content["meta_description"]
    keywords = soup.select_one('meta[name="keywords"]')
    if keywords:
        keywords["content"] = ", ".join([content["name"], *content.get("keywords", [])])
    og = {"og:title": content["title"], "og:description": content["meta_description"], "og:type": "website",
          "og:image": og_image}
    anchor = soup.select_one('meta[name="viewport"]') or soup.head.find("meta")
    for prop, value in reversed(og.items()):
        if value:
            anchor.insert_after(_frag(f'\n  <meta property="{prop}" content="{escape(value, quote=True)}">'))


def _navbar(soup: BeautifulSoup, home_url: str) -> None:
    for dropdown in soup.select("nav .has-dropdown"):
        dropdown.decompose()
    home = soup.select_one("nav .navbar-start > a.navbar-item")
    if home and home_url:
        home["href"], home["aria-label"] = home_url, "Home"
    elif home:
        home.decompose()


def _footer(soup: BeautifulSoup, links: dict[str, str], host_pdf: bool) -> None:
    footer = soup.find("footer")
    if not footer:
        return
    for a in [a for a in footer.find_all("a") if a.find("i")]:  # the icon links (their class attribute is unreliable)
        is_pdf = bool(a.select_one(".fa-file-pdf"))
        code = links.get("code", "")
        if is_pdf and host_pdf:
            a.attrs = {"class": "icon-link", "href": "./paper.pdf", "aria-label": "Paper PDF"}
        elif not is_pdf and code.startswith("http"):
            a.attrs = {"class": "icon-link", "href": code, "aria-label": "Code"}
        else:
            a.decompose()
    for text in footer.find_all(string=re.compile("analytics")):
        text.replace_with(NavigableString(re.sub(r"\s*Please remember.*?website\.", "", str(text), flags=re.S)))


# ---------- hero ----------

def _authors_html(content: dict) -> str:
    spans = []
    authors = content["authors"] if isinstance(content["authors"], list) else []
    for i, a in enumerate(authors):
        marks = ",".join(str(n) for n in a["affiliations"]) + ("*" if a["corresponding"] else "")
        comma = "," if i < len(authors) - 1 else ""
        spans.append(f'<span class="author-block">{escape(a["name"])}<sup>{marks}</sup>{comma}</span>')
    return "\n" + "\n".join(spans) + "\n"


def _affiliations_html(content: dict) -> str:
    affs = content["affiliations"]
    return "\n" + "\n".join(
        f'<span class="author-block"><sup>{a["index"]}</sup>{escape(a["name"])}{"," if i < len(affs) - 1 else ""}</span>'
        for i, a in enumerate(affs)) + "\n"


def _hero(soup: BeautifulSoup, content: dict, links: dict[str, str], host_pdf: bool) -> None:
    soup.select_one("h1.publication-title").string = content["title"]
    blocks = soup.select(".publication-authors")
    _set_children(blocks[0], _authors_html(content))
    _set_children(blocks[1], _affiliations_html(content))
    extra = ""
    if content["venue"] or content["year"]:
        venue = escape(" ".join(v for v in (content["venue"], content["year"]) if v))
        extra += f'\n<div class="is-size-5 publication-authors"><span class="author-block">{venue}</span></div>'
    if isinstance(content["authors"], list) and any(a["corresponding"] for a in content["authors"]):
        extra += ('\n<div class="is-size-6 publication-authors">'
                  '<span class="author-block"><sup>*</sup>Corresponding author</span></div>')
    blocks[1].insert_after(_frag(extra))
    _set_children(soup.select_one(".publication-links"), "\n" + render.render_links(links, host_pdf) + "\n")


# ---------- body sections ----------

def _text(paragraphs: list[str]) -> str:
    ps = "".join(f"<p>{escape(p)}</p>" for p in paragraphs if _ok(p))
    return f'<div class="content has-text-justified">{ps}</div>' if ps else ""


def _block(title: str, inner: str) -> str:
    return (f'<div class="columns is-centered"><div class="column is-full-width">'
            f'<h2 class="title is-3">{title}</h2>{inner}</div></div>\n')


class _Figures:
    def __init__(self, content: dict, extracted: dict) -> None:
        self.files = {f["id"]: f for f in extracted["figures"]}
        self.tables = {t["id"]: t for t in extracted["tables"]}
        self.notes = {f["id"]: f for f in content["figures"]}
        self.used: list[str] = []

    def html(self, fig_id: str) -> str:
        if not _ok(fig_id):
            return ""
        if fig_id not in self.files:
            raise P2PError(f'content.json refers to figure "{fig_id}", which was not extracted '
                           f'(available: {", ".join(self.files)}).')
        if not self.files[fig_id].get("file"):
            raise P2PError(f'Figure "{fig_id}" has no crop yet. Set one with `p2p.py recrop --fig '
                           f'{self.files[fig_id]["number"]} ...` or use another figure.')
        note = self.notes.get(fig_id, {})
        self.used.append(fig_id)
        return render.render_figure(self.files[fig_id], note.get("caption", ""), note.get("alt", ""))


    def table(self, t: dict) -> str:
        """HTML table when the grid was verified against the text layer, otherwise the cropped image."""
        if t.get("display") == "html" and t.get("columns"):
            return render.render_table(t)
        src = self.tables.get(t["id"])
        if not src or not src.get("file"):
            raise P2PError(f'Table "{t["id"]}" has no image crop. Set one with `p2p.py recrop --table ...`, '
                           "or remove the table from content.json.")
        self.used.append(t["id"])
        return render.render_table_image(src, t.get("caption") or src["caption"])


def _sections(content: dict, figs: _Figures) -> str:
    out = ""
    method = content["method"]
    inner = _text(method["paragraphs"]) + figs.html(method["figure"])
    if inner:
        out += _block("Method", inner)
    inner = "".join(figs.table(t) + _text([t["interpretation"]]) for t in content["tables"])
    if inner:
        out += _block("Quantitative Results", inner)
    inner = "".join(_text([q["description"]]) + figs.html(q["figure"]) for q in content["qualitative"])
    if inner:
        out += _block("Qualitative Results", inner)
    return out


def _body(soup: BeautifulSoup, content: dict, figs: _Figures) -> None:
    teaser = soup.select_one("section.teaser .hero-body")
    for media in teaser.find_all(["video", "img", "iframe"]):
        media.decompose()
    subtitle = teaser.find("h2")
    subtitle.insert_before(_frag(figs.html(content["overview_figure"])))
    if _ok(content["tagline"]):
        subtitle.string = content["tagline"]
    else:
        subtitle.decompose()
    for carousel in soup.select("section.hero.is-light"):
        carousel.decompose()

    sections = [s for s in soup.select("section.section") if s.get("id") != "BibTeX"]
    abstract = next(s for s in sections if any(h.get_text(strip=True) == "Abstract" for h in s.find_all("h2")))
    heading = next(h for h in abstract.find_all("h2") if h.get_text(strip=True) == "Abstract")
    _set_children(heading.find_next_sibling(class_="content"),
                  "".join(f"<p>{escape(p)}</p>" for p in content["abstract_paragraphs"] if _ok(p)))
    for video in abstract.select(".publication-video"):
        video.find_parent(class_="columns").decompose()
    for c in abstract.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    others = [s for s in sections if s is not abstract]
    html = _sections(content, figs)
    for extra in others[1:]:
        extra.decompose()
    if others and html:
        _set_children(others[0].select_one(".container"), "\n" + html)
    elif others:
        others[0].decompose()
    soup.select_one("#BibTeX pre code").string = content["bibtex"]


def build_html(template_html: str, content: dict, extracted: dict, links: dict[str, str],
               host_pdf: bool, home_url: str, page_url: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(template_html, "html.parser")
    figs = _Figures(content, extracted)
    _body(soup, content, figs)
    overview = figs.files.get(content["overview_figure"])
    og_image = f"{page_url}static/images/{overview['file']}" if overview and page_url else \
        f"./static/images/{overview['file']}" if overview else ""
    _head(soup, content, og_image)
    _navbar(soup, home_url)
    _hero(soup, content, links, host_pdf)
    _footer(soup, links, host_pdf)
    return str(soup), figs.used


# ---------- PDF ----------

def compress_pdf(src: Path, dst: Path) -> tuple[int, str]:
    """garbage=4 + deflate; if still above 5 MB, downsample images step by step. Returns (bytes, how)."""
    def save(doc: pymupdf.Document) -> int:
        doc.save(dst, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True)
        return dst.stat().st_size

    with pymupdf.open(src) as doc:
        size, how = save(doc), "garbage=4, deflate"
    for dpi, quality in ((150, 80), (110, 70), (80, 60)):
        if size <= MAX_PDF_BYTES:
            break
        with pymupdf.open(src) as doc:
            doc.rewrite_images(dpi_threshold=dpi + 10, dpi_target=dpi, quality=quality)
            size, how = save(doc), f"garbage=4, deflate, images downsampled to {dpi} dpi (JPEG q{quality})"
    return size, how


# ---------- step ----------

def run(build: Build, pdf: Path, template_dir: Path, settings: dict, home_url: str, page_url: str) -> dict:
    content = json.loads(build.content_json.read_text())
    extracted = json.loads(build.extracted_json.read_text())
    if is_todo(content["title"]) or is_todo(content["authors"]):
        raise P2PError(f"Title or authors could not be read from the PDF. Fill them in {build.content_json} first.")
    template_html = (template_dir / "index.html").read_text()
    html, used = build_html(template_html, content, extracted, settings["links"], settings["host_pdf"],
                            home_url, page_url)

    template.reset_dir(build.site)
    template.copy_template(template_dir, build.site)
    images = build.site / "static" / "images"
    images.mkdir(parents=True, exist_ok=True)
    for fig in extracted["figures"] + extracted["tables"]:
        if fig["id"] in used and fig.get("file"):
            shutil.copyfile(build.extracted / fig["file"], images / fig["file"])
    info = {"host_pdf": settings["host_pdf"], "figures_used": used}
    if settings["host_pdf"]:
        size, how = compress_pdf(pdf, build.site / "paper.pdf")
        info.update(pdf_bytes=size, pdf_original_bytes=pdf.stat().st_size, pdf_method=how)
        print(f"  render: paper.pdf {pdf.stat().st_size / 1e6:.1f} MB -> {size / 1e6:.1f} MB ({how})")
    (build.site / ".nojekyll").write_text("")
    (build.site / MARKER).write_text("Generated by paper2page (build-page skill). Safe to overwrite.\n")
    (build.site / "index.html").write_text(html)
    template.patch_assets(build.site, html)
    removed = template.prune_assets(build.site, html)
    print(f"  render: {len(used)} image(s) on the page, {len(removed)} unused template file(s) removed")
    return info
