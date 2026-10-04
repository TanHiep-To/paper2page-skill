"""Step 3, render: content.json + template -> site/ (index.html, static/, paper.pdf, .nojekyll).

The page follows content["blocks"] in order: teaser, abstract, any number of sections, bibtex. A block
that is not listed is left out. Navbar, title area and footer are fixed.

Clone-and-fill only. The template is the design system: every element on the page is a clone of a block
found in the template's own index.html, with text, href, src and alt changed. Nothing here adds a
style attribute, a <style> tag, a CSS rule, or a class that the template does not ship. Tables, for
which the template has no block, use Bulma classes that are already in the template's CSS.
"""
from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from . import content as content_mod, render, template, typo
from .common import SKILL_DIR, Build, P2PError, is_todo

MAX_PDF_BYTES = 5 * 1024 * 1024
MARKER = ".paper2page"
TITLE_TAIL_MAX = 14  # characters that fit one line of the template's h1 on a 390 px phone
TAIL = {"paragraph": 26, "caption": 20, "tagline": 24, "heading": 8}


def _ok(value) -> bool:
    return bool(value) and not is_todo(value)


class Blocks:
    """The block library: the template's own DOM nodes, cloned on demand."""

    def __init__(self, template_html: str) -> None:
        self.soup = BeautifulSoup(template_html, "html.parser")
        s = self.soup
        author_divs = s.select(".publication-authors")
        if len(author_divs) < 2 or not s.select_one(".publication-links .link-block"):
            raise P2PError("The template has no author / affiliation / link-button blocks to clone.")
        self.author_div, self.affiliation_div = author_divs[0], author_divs[1]
        self.author = self._first(self.author_div, "span", "author-block")
        self.affiliation = self._first(self.affiliation_div, "span", "author-block")
        self.link = copy.copy(s.select_one(".publication-links .link-block"))
        self.teaser = s.select_one("section.teaser")
        if not self.teaser or not self.teaser.find("h2"):
            raise P2PError("The template has no teaser block (section.teaser with a caption) to clone.")
        self.caption = copy.copy(self.teaser.find("h2"))              # the teaser's caption element
        img = s.find("img")
        self.image = copy.copy(img) if img else None                   # the template's <img> element
        media = next((d for d in s.select("div.content.has-text-centered") if d.find(["video", "img"])), None)
        self.media = copy.copy(media) if media else None               # centred media wrapper
        self.abstract_section = self._abstract_section()
        self.section = self._standard_section()
        self.paragraph = copy.copy(self.abstract_section.select_one(".content p"))
        if self.image is None or self.media is None or self.paragraph is None:
            raise P2PError("The template has no image / centred media / paragraph block to clone.")

    @staticmethod
    def _first(parent: Tag, name: str, cls: str) -> Tag:
        return copy.copy(parent.find(name, class_=cls))

    def _abstract_section(self) -> Tag:
        for sec in self.soup.select("section.section"):
            if any(h.get_text(strip=True) == "Abstract" for h in sec.find_all("h2")):
                return sec
        raise P2PError('The template has no "Abstract" section to use as the standard section.')

    def _standard_section(self) -> Tag:
        """A copy of the Abstract section holding only its heading and text block."""
        sec = copy.copy(self.abstract_section)
        for c in sec.find_all(string=lambda t: isinstance(t, Comment)):
            c.extract()
        heading = next(h for h in sec.find_all("h2") if h.get_text(strip=True) == "Abstract")
        keep = heading.find_parent(class_="columns")
        for sibling in list(keep.parent.find_all(recursive=False)):
            if sibling is not keep:
                sibling.decompose()
        sec.attrs = {"class": sec.get("class", [])}
        return sec

    # ---- clones ----

    def new_paragraph(self, text: str) -> Tag:
        p = copy.copy(self.paragraph)
        p.string = typo.polish(text, TAIL["paragraph"])
        return p

    def new_caption(self, label: str, text: str) -> Tag:
        cap = copy.copy(self.caption)
        cap.clear()
        cap.attrs = {"class": cap.get("class", [])}
        if label:
            strong = self.soup.new_tag("strong")
            strong.string = typo.bind_numbers(label)
            cap.append(strong)
            cap.append(" ")
        cap.append(typo.polish(text, TAIL["caption"]))
        return cap

    def new_image(self, item: dict, alt: str) -> Tag:
        img = copy.copy(self.image)
        img["src"], img["alt"] = f"./static/images/{item['file']}", alt
        img["loading"], img["width"], img["height"] = "lazy", str(item["width"]), str(item["height"])
        return img

    def new_figure(self, item: dict, alt: str, label: str, caption: str) -> list[Tag]:
        """Centred image (the template's centred media wrapper) followed by the teaser's caption element."""
        wrap = copy.copy(self.media)
        wrap.clear()
        wrap.append(self.new_image(item, alt))
        return [wrap, self.new_caption(label, caption)]

    def new_section(self, title: str) -> tuple[Tag, Tag, Tag]:
        """A clone of the Abstract section: (section, the column to append blocks to, its text block)."""
        sec = copy.copy(self.section)
        heading = sec.find("h2")
        heading.string = typo.polish(title, TAIL["heading"])
        text = heading.find_next_sibling(class_="content")
        text.clear()
        return sec, heading.parent, text

    def new_link(self, kind: str, url: str) -> Tag:
        label, icon = render.link_spec(kind)
        block = copy.copy(self.link)
        for c in block.find_all(string=lambda t: isinstance(t, Comment)):
            c.extract()
        a = block.find("a")
        block.find("i")["class"] = icon.split()
        a.find_all("span")[-1].string = label if url != "soon" else typo.name(f"{label} (coming soon)")
        if url == "soon":
            del a["href"]
            a["disabled"], a["aria-disabled"], a["title"] = "", "true", "Coming soon"
        else:
            a["href"] = url
        return block


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
    if keywords := soup.select_one('meta[name="keywords"]'):
        keywords["content"] = ", ".join([content["name"], *content.get("keywords", [])])
    og = {"og:title": content["title"], "og:description": content["meta_description"], "og:type": "website",
          "og:image": og_image}
    anchor = soup.select_one('meta[name="viewport"]') or soup.head.find("meta")
    for prop, value in reversed(og.items()):
        if value:
            meta = soup.new_tag("meta", attrs={"property": prop, "content": value})
            anchor.insert_after(meta)
            anchor.insert_after("\n  ")


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

def _fill_line(div: Tag, block: Tag, entries: list[tuple[str, str]]) -> None:
    """Refill an author / affiliation line with clones of its own block: (text, superscript) pairs.

    The superscript goes where the template's block has it: before the text (affiliations) or after (authors).
    """
    sup = block.find("sup")
    first = next((c for c in block.contents if not (isinstance(c, NavigableString) and not c.strip())), None)
    sup_first = sup is not None and first is sup
    div.clear()
    for i, (text, marks) in enumerate(entries):
        span = copy.copy(block)
        span.clear()
        comma = "," if i < len(entries) - 1 else ""
        mark = None
        if marks and sup is not None:
            mark = copy.copy(sup)
            mark.string = marks
        if mark is not None and sup_first:
            span.append(mark)
            span.append(text + comma)
        else:
            span.append(text if mark is not None else text + comma)
            if mark is not None:
                span.append(mark)
                if comma:
                    span.append(comma)
        div.append("\n")
        div.append(span)
    div.append("\n")


def _hero(soup: BeautifulSoup, blocks: Blocks, content: dict, links: dict[str, str], host_pdf: bool) -> None:
    h1 = soup.select_one("h1.publication-title")
    h1.clear()
    lines = typo.title_parts(content["title"])
    for i, line in enumerate(lines):
        last = typo.bind_tail(typo.bind_numbers(line), TAIL["heading"], max_chars=TITLE_TAIL_MAX)
        h1.append(last if i == len(lines) - 1 else line + " ")
        if i < len(lines) - 1:
            h1.append(soup.new_tag("br"))
    authors = content["authors"] if isinstance(content["authors"], list) else []
    author_div, affiliation_div = soup.select(".publication-authors")[:2]
    _fill_line(author_div, blocks.author, [
        (typo.name(a["name"]), ",".join(str(n) for n in a["affiliations"])
         + ("†" if a.get("equal_contribution") else "") + ("*" if a["corresponding"] else ""))
        for a in authors])
    _fill_line(affiliation_div, blocks.affiliation, [(a["name"], str(a["index"])) for a in content["affiliations"]])
    extra = []  # further lines are clones of the affiliation line
    if content["venue"] or content["year"]:
        extra.append([(" ".join(v for v in (content["venue"], content["year"]) if v), "")])
    notes = []
    if any(a.get("equal_contribution") for a in authors):
        notes.append(("Equal" + typo.NBSP + "contribution", "†"))
    if any(a["corresponding"] for a in authors):
        notes.append(("Corresponding" + typo.NBSP + "author", "*"))
    if notes:
        extra.append(notes)
    anchor = affiliation_div
    for entries in extra:
        line = copy.copy(blocks.affiliation_div)
        _fill_line(line, blocks.affiliation, entries)
        anchor.insert_after(line)
        anchor.insert_after("\n\n          ")
        anchor = line
    box = soup.select_one(".publication-links")
    box.clear()
    for kind, url in [("paper", "./paper.pdf" if host_pdf else "soon"), *((k, u) for k, u in links.items() if k != "paper")]:
        box.append("\n")
        box.append(blocks.new_link(kind, url))
    box.append("\n")


# ---------- body sections ----------

class _Media:
    """Figures and tables from extracted.json, turned into template blocks."""

    def __init__(self, blocks: Blocks, content: dict, extracted: dict) -> None:
        self.blocks = blocks
        self.files = {f["id"]: f for f in extracted["figures"]}
        self.tables = {t["id"]: t for t in extracted["tables"]}
        self.notes = {f["id"]: f for f in content["figures"]}
        self.used: list[str] = []

    def source(self, fig_id: str) -> dict:
        if fig_id not in self.files:
            raise P2PError(f'content.json refers to figure "{fig_id}", which was not extracted '
                           f'(available: {", ".join(self.files)}).')
        if not self.files[fig_id].get("file"):
            raise P2PError(f'Figure "{fig_id}" has no crop yet. Set one with `p2p.py recrop --fig '
                           f'{self.files[fig_id]["number"]} ...` or use another figure.')
        self.used.append(fig_id)
        return self.files[fig_id]

    def alt(self, fig_id: str) -> str:
        return self.notes.get(fig_id, {}).get("alt") or self.notes.get(fig_id, {}).get("caption", "")

    def figure(self, fig_id: str) -> list[Tag]:
        if not _ok(fig_id):
            return []
        src = self.source(fig_id)
        label = "" if src.get("fallback") else f"Figure {src['number']}."
        return self.blocks.new_figure(src, self.alt(fig_id), label, self.notes.get(fig_id, {}).get("caption", ""))

    def table(self, t: dict) -> list[Tag]:
        """An HTML table when its grid was verified against the text layer, otherwise the cropped image."""
        if t.get("display") == "html" and t.get("columns"):
            return [self.blocks.new_caption(f"Table {t['number']}.", _table_caption(t)), _html_table(self.blocks.soup, t)]
        src = self.tables.get(t["id"])
        if not src or not src.get("file"):
            raise P2PError(f'Table "{t["id"]}" has no image crop. Set one with `p2p.py recrop --table ...`, '
                           "or remove the table from content.json.")
        self.used.append(t["id"])
        caption = t.get("caption") or src["caption"]
        return self.blocks.new_figure(src, f"Table {src['number']}: {caption}", f"Table {src['number']}.", caption)


def _table_caption(t: dict) -> str:
    marks = render.rank_cells(t)
    note = ""
    if marks:
        note = " Best in bold" + (", second best underlined." if "second" in marks.values() else ".")
    return t["caption"].rstrip() + note


def _html_table(soup: BeautifulSoup, t: dict) -> Tag:
    """A plain Bulma table (classes shipped with the template) inside Bulma's scroll container."""
    marks = render.rank_cells(t)
    wrap = soup.new_tag("div", attrs={"class": [render.TABLE_WRAPPER_CLASS]})
    table = soup.new_tag("table", attrs={"class": render.TABLE_CLASSES})
    wrap.append(table)
    thead, tbody = soup.new_tag("thead"), soup.new_tag("tbody")
    table.append(thead)
    table.append(tbody)
    cols = t["columns"]
    groups = [c.get("group", "") for c in cols]
    row_groups = any(r.get("group") for r in t["rows"])
    if any(groups):
        top = soup.new_tag("tr")
        if row_groups:
            top.append(soup.new_tag("th"))
        i = 0
        while i < len(cols):
            j = i
            while j < len(cols) and groups[j] == groups[i]:
                j += 1
            th = soup.new_tag("th", attrs={"colspan": str(j - i), "scope": "colgroup"})
            th.string = groups[i]
            top.append(th)
            i = j
        thead.append(top)
    head = soup.new_tag("tr")
    if row_groups:
        th = soup.new_tag("th", attrs={"scope": "col"})
        th.string = t.get("row_group_label", "")
        head.append(th)
    for c in cols:
        th = soup.new_tag("th", attrs={"scope": "col"})
        th.string = typo.bind_numbers(c["label"])
        head.append(th)
    thead.append(head)
    for i, row in enumerate(t["rows"]):
        tr = soup.new_tag("tr")
        if row_groups and (i == 0 or t["rows"][i - 1].get("group") != row.get("group")):
            span = 1
            while i + span < len(t["rows"]) and t["rows"][i + span].get("group") == row.get("group"):
                span += 1
            th = soup.new_tag("th", attrs={"scope": "rowgroup", "rowspan": str(span)})
            th.string = row.get("group", "")
            tr.append(th)
        for c, cell in enumerate(row["cells"]):
            td = soup.new_tag("th", attrs={"scope": "row"}) if cols[c].get("role") == "label" else soup.new_tag("td")
            mark = marks.get((i, c))
            if mark:
                inner = soup.new_tag("strong" if mark == "best" else "u")
                inner.string = cell
                td.append(inner)
            else:
                td.string = typo.name(cell)
            tr.append(td)
        tbody.append(tr)
    return wrap


def _note(blocks: Blocks, paragraphs: list[str]) -> Tag:
    """A further text block inside a section: a clone of the section's own text block."""
    note = copy.copy(blocks.section.find(class_="content"))
    note.clear()
    for p in paragraphs:
        note.append(blocks.new_paragraph(p))
    return note


def _section(blocks: Blocks, content: dict, media: _Media, block: dict) -> Tag | None:
    """One content section, a clone of the Abstract section, filled with its items in order."""
    sec, column, text = blocks.new_section(block.get("title", ""))
    shown = 0
    for item in block.get("items", []):
        kind, tags = item.get("type"), []
        if kind == "text":
            paragraphs = [p for p in item.get("paragraphs", []) if _ok(p)]
            if paragraphs and shown == 0:  # leading text goes into the section's own text block
                for p in paragraphs:
                    text.append(blocks.new_paragraph(p))
                shown += 1
            elif paragraphs:
                tags = [_note(blocks, paragraphs)]
        elif kind == "figure":
            tags = media.figure(item.get("id", ""))
        elif kind == "table" and _ok(item.get("id")):
            t = content_mod.table_def(content, item["id"]) or {"id": item["id"], "display": "image"}
            tags = media.table(t)
            if _ok(t.get("interpretation")):
                tags.append(_note(blocks, [t["interpretation"]]))
        for tag in tags:
            column.append(tag)
        shown += len(tags)
    if not shown:
        return None
    if not text.find("p"):
        text.decompose()
    return sec


def _teaser(section: Tag, blocks: Blocks, media: _Media, block: dict) -> Tag:
    """The template's own teaser block; its media element becomes the overview image."""
    body = section.select_one(".hero-body")
    caption = body.find("h2")
    for old in body.find_all(["video", "img", "iframe"]):
        old.decompose()
    if _ok(block.get("figure")):
        caption.insert_before(blocks.new_image(media.source(block["figure"]), media.alt(block["figure"])))
        caption.insert_before("\n      ")
    if _ok(block.get("tagline")):
        caption.string = typo.polish(block["tagline"], TAIL["tagline"])
    else:
        caption.decompose()
    return section


def _body(soup: BeautifulSoup, blocks: Blocks, content: dict, media: _Media) -> None:
    """Rebuild the page body from content["blocks"], in that order, below the title area."""
    title_area = soup.select_one("h1.publication-title").find_parent("section")
    teaser, bibtex = soup.select_one("section.teaser"), soup.select_one("#BibTeX")
    for sec in [s for s in soup.find_all("section") if s is not title_area and not s.find_parent("section")]:
        sec.extract()  # the template's sample sections; only the blocks listed below come back
    nodes = []
    for block in content["blocks"]:
        kind = block.get("type")
        if kind == "teaser":
            nodes.append(_teaser(teaser, blocks, media, block))
        elif kind == "abstract":  # the standard section; every other content section is a clone of it too
            sec, _, text = blocks.new_section("Abstract")
            for p in content["abstract_paragraphs"]:
                if _ok(p):
                    text.append(blocks.new_paragraph(p))
            nodes.append(sec)
        elif kind == "section":
            if (sec := _section(blocks, content, media, block)) is not None:
                nodes.append(sec)
        elif kind == "bibtex":
            bibtex.select_one("pre code").string = content["bibtex"]
            nodes.append(bibtex)
    anchor = title_area
    for node in nodes:
        anchor.insert_after(node)
        anchor.insert_after("\n\n\n")
        anchor = node


def build_html(template_html: str, content: dict, extracted: dict, links: dict[str, str],
               host_pdf: bool, home_url: str, page_url: str) -> tuple[str, list[str]]:
    blocks = Blocks(template_html)
    soup = BeautifulSoup(template_html, "html.parser")
    media = _Media(blocks, content, extracted)
    _body(soup, blocks, content, media)
    # og:image: the teaser image, else the first figure on the page, else the first table image
    shown = [media.files[i] for i in media.used if i in media.files] + [media.tables[i] for i in media.used if i in media.tables]
    overview = next((item for item in shown if item.get("file")), None)
    og_image = ""
    if overview and overview.get("file"):
        og_image = f"{page_url}static/images/{overview['file']}" if page_url else f"./static/images/{overview['file']}"
    _head(soup, content, og_image)
    _navbar(soup, home_url)
    _hero(soup, blocks, content, links, host_pdf)
    _footer(soup, links, host_pdf)
    return soup.decode(formatter="html5"), media.used


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
    content = content_mod.load(build)
    extracted = json.loads(build.extracted_json.read_text())
    if is_todo(content["title"]) or is_todo(content["authors"]):
        raise P2PError(f"Title or authors could not be read from the PDF. Fill them in {build.content_json} first.")
    if errors := content_mod.validate(content, extracted)[0]:
        raise P2PError(f"{build.content_json}: the block list cannot be rendered:\n  - " + "\n  - ".join(errors))
    template_html = (template_dir / "index.html").read_text()
    html, used = build_html(template_html, content, extracted, settings["links"], settings["host_pdf"],
                            home_url, page_url)

    template.reset_dir(build.site)
    template.copy_template(template_dir, build.site)
    images = build.site / "static" / "images"
    images.mkdir(parents=True, exist_ok=True)
    for item in extracted["figures"] + extracted["tables"]:
        if item["id"] in used and item.get("file"):
            shutil.copyfile(build.extracted / item["file"], images / item["file"])
    info = {"host_pdf": settings["host_pdf"], "used": used}
    if settings["host_pdf"]:
        size, how = compress_pdf(pdf, build.site / "paper.pdf")
        info.update(pdf_bytes=size, pdf_original_bytes=pdf.stat().st_size, pdf_method=how)
        print(f"  render: paper.pdf {pdf.stat().st_size / 1e6:.1f} MB -> {size / 1e6:.1f} MB ({how})")
    (build.site / ".nojekyll").write_text("")
    (build.site / MARKER).write_text("Generated by paper2page (build-page skill). Safe to overwrite.\n")
    (build.site / "index.html").write_text(html)
    info["patched"] = template.patch_assets(build.site, html)
    info["added"] = template.add_missing_webfonts(build.site, SKILL_DIR / ".cache")
    removed = template.prune_assets(build.site, html)
    print(f"  render: {len(used)} image(s) on the page, {len(removed)} unused template file(s) removed")
    return info
