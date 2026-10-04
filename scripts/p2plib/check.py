"""Step 4, check: site/ + content.json + extracted.json -> report.md, report.json, screenshots/."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from . import browser, content as content_mod, pdf_text
from .common import Build, is_todo, prompt_instructions
from .figures import MAX_BYTES
from .pdf_text import PdfText
from .render import cell_number, rank_cells
from .site import MAX_PDF_BYTES
from .template import ATTRIBUTION_HINT, is_local, local_refs, sample_tokens

ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}
ABSTRACT_MIN_RATIO = 0.95
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
OPTIONAL_CLOSE = {"p", "li", "td", "th", "tr", "thead", "tbody", "option", "dt", "dd"}
MANUAL = [
    "Author names (and any display-name overrides), affiliation numbers and the * mark against page 1 of the PDF.",
    "Results table: rows, columns, units against the paper, and the metric directions set in the yaml.",
    "Each figure and table image shows the right graphic for its caption, cropped cleanly (extracted/contact_sheet.png).",
    "Tagline and section summaries say only what the paper says.",
    "If a prompt was used: section \"Instructions applied\" matches what you asked for, and no text from a skipped part is on the page.",
    "screenshots/template_vs_output.png: navbar, title area and footer look like the template's apart from the text.",
    "Venue and year in the BibTeX entry (taken from the yaml).",
    "If host_pdf is true: you have the right to host the PDF publicly.",
]


@dataclass
class Check:
    name: str
    status: str = "PASS"
    details: list[str] = field(default_factory=list)

    def warn(self, msg: str) -> None:
        self.details.append(msg)
        self.status = max(self.status, "WARN", key=ORDER.get)

    def fail(self, msg: str) -> None:
        self.details.append(msg)
        self.status = "FAIL"

    def note(self, msg: str) -> None:
        self.details.append(msg)


# ---------- helpers ----------

class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append((tag, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        while self.stack and self.stack[-1][0] != tag and self.stack[-1][0] in OPTIONAL_CLOSE:
            self.stack.pop()
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()
        else:
            self.errors.append(f"line {self.getpos()[0]}: unexpected </{tag}>")


def parse_errors(html: str) -> list[str]:
    parser = _Balance()
    parser.feed(html)
    parser.close()
    return parser.errors[:5] + [f"<{t}> opened on line {ln} is never closed"
                                for t, ln in parser.stack if t not in OPTIONAL_CLOSE][:5]


def visible_text(soup: BeautifulSoup, body_only: bool = False) -> str:
    """Page text. body_only drops the BibTeX entry and the template's footer boilerplate."""
    soup = BeautifulSoup(str(soup), "html.parser")
    for tag in soup(["script", "style", "noscript"] + (["footer"] if body_only else [])):
        tag.decompose()
    if body_only:
        for tag in soup.select("#BibTeX, pre"):
            tag.decompose()
    return soup.get_text(" ")


def abstract_on_page(soup: BeautifulSoup) -> str:
    for h in soup.find_all(["h2", "h3"]):
        if h.get_text(strip=True).lower() == "abstract":
            return pdf_text.squash(" ".join(s.get_text(" ") for s in h.find_next_siblings() if getattr(s, "name", None)))
    return ""


# ---------- checks ----------

def check_complete(content: dict, build: Build) -> Check:
    c = Check("Content complete")
    todos = content_mod.todos(content)
    for path in todos:
        c.fail(f"TODO: {path}")
    if not todos:
        c.note("no TODO fields in content.json")
    return c


def _head_includes(soup: BeautifulSoup) -> list[str]:
    urls = [l.get("href", "") for l in soup.select("head link[href]")] + [s.get("src", "") for s in soup.select("head script[src]")]
    return [re.sub(r"^\./", "", u) for u in urls if "googletagmanager" not in u]


def _signatures(soup: BeautifulSoup, selector: str) -> set[str]:
    return {" ".join(sorted(el.get("class", []))) for el in soup.select(selector)}


def check_skeleton(template_html: str, html: str, template_dir: Path) -> Check:
    c = Check("Template skeleton")
    tpl, page = BeautifulSoup(template_html, "html.parser"), BeautifulSoup(html, "html.parser")
    for err in parse_errors(html):
        c.fail(f"HTML does not parse cleanly: {err}")
    want, got = _head_includes(tpl), _head_includes(page)
    for url in want:
        if url not in got:
            c.fail(f"head include missing: {url}")
    for url in got:
        if url not in want and "mathjax" not in url.lower():
            c.warn(f"head include not in the template: {url}")
    tops = [el.name + ("." + ".".join(el.get("class", [])) if el.get("class") else "")
            for el in page.body.find_all(recursive=False)] if page.body else []
    if not tops or not tops[0].startswith("nav.navbar"):
        c.fail("the page does not start with the template navbar")
    if not tops or not tops[-1].startswith("footer.footer"):
        c.fail("the page does not end with the template footer")
    for label, sel in (("section", "body > section"), ("container", "section > .container, section .hero-body > .container"),
                       ("h1", "h1"), ("h2", "h2"), ("h3", "h3")):
        for sig in sorted(_signatures(page, sel) - _signatures(tpl, sel)):
            c.warn(f'{label} class "{sig or "(none)"}" does not occur in the template')
    footer = page.find("footer")
    if not footer or not footer.select_one(f'a[href*="{ATTRIBUTION_HINT}"]'):
        c.fail("the template attribution link is missing from the footer")
    if "googletagmanager" in html or "gtag(" in html:
        c.fail("Google Analytics is still present")
    if not c.details:
        c.note(f"{len(want)} head includes, section/container/heading classes, navbar and footer match the template")
    return c


def _known_classes(tpl: BeautifulSoup, template_dir: Path) -> set[str]:
    known = {cls for el in tpl.find_all(class_=True) for cls in el["class"]}
    for f in template_dir.rglob("*.css"):
        known |= set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", f.read_text(errors="ignore")))
    return known


def check_fidelity(template_html: str, html: str, template_dir: Path, site: Path, review: dict, info: dict) -> Check:
    """The template is the design system: no added styles, identical CSS, known classes, same computed styles."""
    c = Check("Template fidelity")
    tpl, page = BeautifulSoup(template_html, "html.parser"), BeautifulSoup(html, "html.parser")

    allowed = [el["style"].strip() for el in tpl.find_all(style=True)]
    for el in page.find_all(style=True):
        if el["style"].strip() not in allowed:
            c.fail(f'inline style added on <{el.name}>: style="{el["style"]}"')
    if len(page.find_all("style")) > len(tpl.find_all("style")):
        c.fail(f"{len(page.find_all('style')) - len(tpl.find_all('style'))} <style> tag(s) added")

    css_files = sorted(site.rglob("*.css"))
    for f in css_files:
        rel = f.relative_to(site)
        original = template_dir / rel
        if not original.is_file():
            c.fail(f"CSS file not in the template: {rel}")
        elif original.read_bytes() != f.read_bytes():
            c.fail(f"CSS file differs from the template's: {rel}")

    known = _known_classes(tpl, template_dir)
    unknown = sorted({cls for el in page.find_all(class_=True) for cls in el["class"]} - known)
    for cls in unknown:
        c.fail(f'class "{cls}" is not in the template\'s index.html or its CSS files')

    styles = review.get("styles", {})
    compared = 0
    if "template" in styles and "output" in styles:
        t, o = styles["template"], styles["output"]
        for element, want in t.items():
            if element == "sections" or want is None or o.get(element) is None:
                continue
            for prop, value in want.items():
                compared += 1
                if o[element][prop] != value:
                    c.fail(f"{element}: {prop} is {o[element][prop]} (template: {value})")
        by_sig = {sec["sig"]: sec for sec in t["sections"]}
        for sec in o["sections"]:
            ref = by_sig.get(sec["sig"])
            if ref is None:
                c.fail(f'{sec["sig"]}: this section wrapper does not exist in the template')
                continue
            for prop, value in ref.items():
                compared += 1
                if sec[prop] != value:
                    c.fail(f'{sec["sig"]}: {prop} is {sec[prop]} (template: {value})')
        content = [sec for sec in o["sections"] if sec["sig"] == "section.section"]
        if len({json.dumps(sec, sort_keys=True) for sec in content}) > 1:
            c.fail("content sections do not all have the same background and padding")
        c.note(f"{len(content)} content sections share one wrapper (section.section): background "
               f"{content[0]['backgroundColor']}, padding {content[0]['paddingTop']} {content[0]['paddingRight']}"
               if content else "no content sections")
    else:
        c.warn("computed styles could not be compared (browser not available)")
    c.note(f"no inline style or <style> added; {len(css_files)} CSS file(s) byte-identical to the template; "
           f"all classes exist in the template; {compared} computed style values equal to the template's original page")
    for added in info.get("added", []):
        c.note(f"font file the template's CSS refers to but does not ship, added unchanged: {added}")
    for patched in info.get("patched", []):
        c.note(f"template file changed by the tool (JavaScript, no visual effect): {patched}")
    return c


def check_typography(review: dict) -> Check:
    """No one-word or very short last lines; title at most 3 lines and tagline at most 2 on desktop."""
    c = Check("Typography")
    views = review.get("typography", {})
    if not views:
        c.warn("line layout could not be measured (browser not available)")
        return c
    total = long_words = 0
    for view, items in views.items():
        for it in items:
            total += 1
            where = f'{view}: {it["kind"]} "{it["text"]}"'
            if it["lines"] > 1 and it["lastWords"] == 1 and it["lastRatio"] >= 0.5:
                long_words += 1  # one long unbreakable word (a URL) filling most of the line is not a widow
            elif it["lines"] > 1 and it["lastWords"] == 1 and it["kind"] in ("title", "caption"):
                c.warn(f"{where}: last line is a single word (wording comes from the paper, so it is kept)")
            elif it["lines"] > 1 and it["lastWords"] == 1:
                c.fail(f"{where}: last line is a single word")
            elif it["lines"] > 1 and it["lastRatio"] < 0.2:
                c.warn(f"{where}: last line is only {it['lastRatio']:.0%} of the line width")
            if view == "desktop" and it["kind"] == "title" and it["lines"] > 3:
                c.fail(f"{where}: {it['lines']} lines (at most 3)")
            if view == "desktop" and it["kind"] == "tagline" and it["lines"] > 2:
                c.fail(f"{where}: {it['lines']} lines (at most 2); rewrite the tagline shorter with the same facts")
    if not c.details:
        c.note(f"{total} text blocks measured at 1280 px and 390 px: no single-word or very short last lines; "
               "title and tagline within their line limits"
               + (f" ({long_words} last line(s) hold one long word such as a URL, which fills the line)" if long_words else ""))
    return c


def check_sample_text(template_html: str, html: str) -> Check:
    c = Check("No template sample content")
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select(f'footer a[href*="{ATTRIBUTION_HINT}"]'):
        a.decompose()
    footer = soup.find("footer")
    footer_html = str(footer).lower() if footer else ""
    if footer:
        footer.decompose()
    body = str(soup).lower()
    for tok in sample_tokens(template_html):
        low = tok.lower()
        if low in body or (low != "nerfies" and low in footer_html):
            c.fail(f'template sample text still present: "{tok}"')
    if not c.details:
        c.note("no sample titles, authors, links or analytics left; attribution link kept")
    return c


def check_identity(content: dict, html: str, pdf: PdfText) -> Check:
    c = Check("Title, authors, affiliations")
    first = pdf_text.loose_key(pdf.pages[0])
    page = pdf_text.loose_key(visible_text(BeautifulSoup(html, "html.parser")))
    if pdf_text.loose_key(content["title"]) not in first:
        c.fail(f'title not found on page 1 of the PDF: {content["title"]}')
    if pdf_text.loose_key(content["title"]) not in page:
        c.fail("title is missing on the page")
    authors = content["authors"] if isinstance(content["authors"], list) else []
    pdf_pos, page_pos, renamed = [], [], []
    for a in authors:
        printed = a.get("pdf_name", a["name"])
        if pdf_text.loose_key(printed) not in first:
            c.fail(f"author not found on page 1 of the PDF: {printed}")
        if pdf_text.loose_key(a["name"]) not in page:
            c.fail(f'author missing on the page: {a["name"]}')
        if printed != a["name"]:
            renamed.append(f'{printed} -> {a["name"]}')
        pdf_pos.append(first.find(pdf_text.loose_key(printed)))
        page_pos.append(page.find(pdf_text.loose_key(a["name"])))
    if pdf_pos != sorted(pdf_pos):
        c.fail("author order differs from the PDF")
    if page_pos != sorted(page_pos):
        c.fail("author order on the page differs from content.json")
    for aff in content["affiliations"]:
        if pdf_text.loose_key(aff["name"]) not in first:
            c.warn(f'affiliation text not found verbatim on page 1: {aff["name"]}')
    marked = [a["name"] for a in authors if a["corresponding"]]
    if marked and not re.search(r"[*⋆†‡]|orrespond", pdf.pages[0]):
        c.warn("corresponding author marked, but no footnote symbol found on page 1 of the PDF")
    equal = [a["name"] for a in authors if a.get("equal_contribution")]
    if equal:
        c.note(f'equal contribution (from the footnote on page 1): {", ".join(equal)}')
    if renamed:
        c.note("display-name overrides from the yaml: " + "; ".join(renamed))
    if c.status == "PASS":
        c.note(f'{len(authors)} authors in PDF order; corresponding: {", ".join(marked) or "none marked"}')
    return c


def check_abstract(content: dict, html: str, pdf: PdfText) -> Check:
    c = Check("Abstract match")
    if not content_mod.block(content, "abstract"):
        c.note("the abstract is not shown (no abstract block in the layout)")
        return c
    reference = pdf_text.extract_abstract(pdf)
    if not reference:
        c.warn("could not locate the abstract in the PDF text; compare manually")
        return c
    ratio = fuzz.ratio(pdf_text.fuzzy_key(abstract_on_page(BeautifulSoup(html, "html.parser"))),
                       pdf_text.fuzzy_key(reference)) / 100
    msg = f"fuzzy ratio {ratio:.3f} against the PDF abstract (threshold {ABSTRACT_MIN_RATIO})"
    c.note(msg) if ratio >= ABSTRACT_MIN_RATIO else c.fail(msg)
    return c


def check_numbers(content: dict, html: str, pdf: PdfText) -> Check:
    c = Check("Numbers check")
    soup = BeautifulSoup(html, "html.parser")
    known = pdf.numbers | pdf_text.numbers_in(content.get("year", ""))
    on_page = pdf_text.numbers_in(visible_text(soup, body_only=True))
    missing = sorted(on_page - known, key=lambda n: (len(n), n))
    if missing:
        c.fail(f"numbers on the page that are not in the PDF: {', '.join(missing)}")
    else:
        c.note(f"all {len(on_page)} distinct numbers on the page occur in the PDF text")
    return c


def check_tables(content: dict, extracted: dict, pdf: PdfText) -> Check:
    c = Check("Table check")
    shown = content_mod.shown_tables(content)
    if not shown:
        found = ", ".join(f"Table {t['number']}" for t in extracted["tables"]) or "none"
        c.note(f"no table on the page (tables found in the PDF: {found})")
        return c
    source = {t["id"]: t for t in extracted["tables"]}
    for t in shown:
        src = source.get(t["id"])
        tag = f"Table {t.get('number') or (src or {}).get('number') or t['id']}"
        if not src:
            c.fail(f"{tag}: no such table was found in the PDF")
            continue
        if t.get("display") != "html" or not t.get("columns"):
            c.note(f"{tag}: shown as the image cropped from PDF page {src['page']} (no re-typed values to verify)")
            continue
        if not src["html_verified"]:
            c.fail(f"{tag}: shown as HTML, but its numbers were not verified against the PDF text layer; "
                   'set "display": "image"')
        near = " ".join(pdf.pages[i] for i in (src["page"] - 1, src["page"]) if i < len(pdf.pages))
        cells = " ".join(cell for r in t["rows"] for cell in r["cells"])
        bad = sorted(pdf_text.numbers_in(cells) - pdf_text.numbers_in(near))
        if bad:
            c.fail(f"{tag}: values not on the table's PDF page: {', '.join(bad)}")
        near_key = pdf_text.loose_key(near)
        labels = [col["label"] for col in t["columns"]] + [col["group"] for col in t["columns"]]
        labels += [r["cells"][0] for r in t["rows"] if r["cells"]] + [r["group"] for r in t["rows"]]
        for label in dict.fromkeys(l for l in labels if l):
            if pdf_text.loose_key(re.sub(r"[↑↓]", "", label)) not in near_key:
                c.warn(f'{tag}: label "{label}" not found verbatim on the table\'s PDF page')
        if {len(r["cells"]) for r in t["rows"]} != {len(t["columns"])}:
            c.fail(f"{tag}: rows do not all have {len(t['columns'])} cells")
        grid = [r for r in src["grid"] if any(x.strip() for x in r)]
        want = (len(t["rows"]) + 1, len(t["columns"]) + (1 if any(r.get("group") for r in t["rows"]) else 0))
        if grid and (len(grid), len(grid[0])) != want and not any(col["group"] for col in t["columns"]):
            c.warn(f"{tag}: the PDF grid was read as {len(grid)} x {len(grid[0])} (with header), "
                   f"the page has {want[0]} x {want[1]}; compare manually")
        for i, col in enumerate(t["columns"]):
            places = {len(m.group(1)) for r in t["rows"] if col["role"] == "value" and i < len(r["cells"])
                      and (m := re.search(r"\.(\d+)", r["cells"][i]))}
            if len(places) > 1:
                c.warn(f'{tag}: column "{col["label"]}" has mixed decimal places (kept as printed)')
        marks = rank_cells(t)
        if not marks:
            c.warn(f"{tag}: no metric directions known, so nothing is highlighted. "
                   "Set metric_directions in the paper yaml (higher / lower).")
        best = {n for (i, j), m in marks.items() if m == "best" for n in pdf_text.numbers_in(t["rows"][i]["cells"][j])}
        values = {n for r in t["rows"] for j, cell in enumerate(r["cells"]) if t["columns"][j]["role"] == "value"
                  for n in pdf_text.numbers_in(cell)}
        bold = set(src.get("bold_numbers", [])) & values
        if bold and best and best != bold:
            c.warn(f"{tag}: computed best values {sorted(best)} differ from the bold values in the PDF {sorted(bold)}")
        counts = [sum(1 for m in marks.values() if m == k) for k in ("best", "second")]
        c.note(f"{tag}: {len(t['rows'])} rows x {len(t['columns'])} columns; all values found on PDF page {src['page']}; "
               f"{counts[0]} best / {counts[1]} second-best cells computed from the numbers")
    return c


def check_figures(content: dict, extracted: dict, html: str) -> Check:
    c = Check("Figure mapping and captions")
    source = {f["id"]: f for f in extracted["figures"]}
    used = [f for f in extracted["figures"] if f.get("file") and f'static/images/{f["file"]}' in html]
    for note in content["figures"]:
        fig = source.get(note["id"])
        if fig and fig in used and fig["caption"]:
            # token_set_ratio is 100 when the caption only uses words of the printed caption
            score = fuzz.token_set_ratio(pdf_text.fuzzy_key(note["caption"]), pdf_text.fuzzy_key(fig["caption"]))
            if score < 90:
                c.warn(f'{note["id"]}: caption differs from the printed caption (similarity {score:.0f}); check the meaning')
    if any(f.get("fallback") for f in used):
        c.warn("no figure captions were found in the PDF; the top half of page 1 is used")
    if not used and not content_mod.used_figures(content):
        c.warn("the layout shows no figure")
    elif not used:
        c.fail("no figure is shown on the page")
    if not c.details:
        c.note(f"{len(used)} figure(s) shown ({', '.join(f['id'] for f in used)}); captions match the printed captions")
    return c


def check_bibtex(content: dict, html: str) -> Check:
    c = Check("BibTeX consistency")
    if not content_mod.block(content, "bibtex"):
        c.note("BibTeX is not shown (no bibtex block in the layout)")
        return c
    pre = BeautifulSoup(html, "html.parser").select_one("#BibTeX pre")
    bib = pre.get_text() if pre else ""
    if not bib.strip().startswith("@"):
        c.fail("no BibTeX entry on the page")
        return c
    key = pdf_text.loose_key(bib.replace("{", "").replace("}", ""))
    if pdf_text.loose_key(content["title"]) not in key:
        c.fail("BibTeX title differs from the page title")
    for a in content["authors"] if isinstance(content["authors"], list) else []:
        if pdf_text.loose_key(a["name"].split()[-1]) not in key:
            c.fail(f'BibTeX is missing author "{a["name"]}"')
    if content["year"] and content["year"] not in bib:
        c.fail(f'BibTeX year differs from "{content["year"]}"')
    if content["venue"] and pdf_text.loose_key(content["venue"]) not in key:
        c.fail(f'venue "{content["venue"]}" not in the BibTeX entry')
    if not content["venue"] or not content["year"]:
        c.warn("venue and/or year are empty in the paper yaml, so the BibTeX entry has none")
    if not c.details:
        c.note("title, authors, venue and year agree with the page")
    return c


def check_page_quality(html: str) -> Check:
    c = Check("Accessibility and meta")
    soup = BeautifulSoup(html, "html.parser")
    if len(soup.find_all("h1")) != 1:
        c.fail(f"{len(soup.find_all('h1'))} <h1> elements (expected 1)")
    last = 1
    for h in soup.find_all(re.compile(r"^h[1-6]$")):
        if int(h.name[1]) > last + 1:
            c.warn(f'heading level jumps from h{last} to {h.name} at "{h.get_text(strip=True)[:40]}"')
        last = int(h.name[1])
    for img in soup.find_all("img"):
        if not (img.get("alt") or "").strip():
            c.fail(f'image without alt text: {img.get("src")}')
        if img.get("loading") != "lazy":
            c.warn(f'image without loading="lazy": {img.get("src")}')
    need = {"title": soup.title and soup.title.string, "meta description": soup.select_one('meta[name="description"][content]'),
            "og:title": soup.select_one('meta[property="og:title"][content]'),
            "og:description": soup.select_one('meta[property="og:description"][content]'),
            "og:image": soup.select_one('meta[property="og:image"][content]'), "favicon": soup.select_one('link[rel~="icon"]')}
    for name, present in need.items():
        if not present:
            c.fail(f"missing {name}")
    for p in soup.select("section p"):
        sentences = len(re.findall(r"[.!?](?:\s|$)", p.get_text(" ", strip=True)))
        if sentences > 4:
            c.warn(f'paragraph with {sentences} sentences: "{p.get_text(" ", strip=True)[:50]}..."')
    for table in soup.find_all("table"):
        if not table.find_parent(class_="table-container"):
            c.fail("table without a scrollable container")
        if not table.find("thead"):
            c.fail("table without a header row")
    if not c.details:
        c.note("one h1, logical heading order, alt text on all images, all meta tags and favicon present")
    return c


def _head_problem(url: str) -> str | None:
    headers = {"User-Agent": "paper2page-linkcheck"}
    try:
        r = requests.head(url, allow_redirects=True, timeout=10, headers=headers)
        if r.status_code in (403, 405, 501):
            r = requests.get(url, stream=True, timeout=10, headers=headers)
        return None if r.status_code < 400 else f"HTTP {r.status_code}"
    except requests.RequestException as e:
        return type(e).__name__


def check_links(html: str, site: Path) -> Check:
    c = Check("Links")
    for ref in sorted(r for r in local_refs(html) if not (site / r).exists()):
        c.fail(f"broken local link: {ref}")
    soup = BeautifulSoup(html, "html.parser")
    external = dict.fromkeys(a["href"] for a in soup.select("body a[href]") if a["href"].startswith("http"))
    for url in external:
        if problem := _head_problem(url):
            c.warn(f"{url}: {problem}")
    soon = [a.get_text(strip=True) for a in soup.select("a[disabled]")]
    if soon:
        c.note("disabled buttons: " + ", ".join(soon))
    if c.status == "PASS":
        c.note(f"all local links exist; {len(external)} external link(s) answered")
    return c


def check_files(html: str, site: Path, info: dict) -> Check:
    c = Check("Image and PDF sizes")
    sizes = []
    for img in BeautifulSoup(html, "html.parser").find_all("img", src=True):
        f = site / re.sub(r"^\./", "", img["src"])
        if is_local(img["src"]) and f.is_file():
            sizes.append(f.stat().st_size)
            if f.stat().st_size > MAX_BYTES:
                c.warn(f"{img['src']} is {f.stat().st_size // 1024} KB (target < {MAX_BYTES // 1024} KB)")
    c.note(f"{len(sizes)} image(s), largest {max(sizes, default=0) // 1024} KB")
    pdf = site / "paper.pdf"
    if pdf.is_file():
        size = pdf.stat().st_size
        msg = (f"paper.pdf {info.get('pdf_original_bytes', size) / 1e6:.1f} MB -> {size / 1e6:.1f} MB "
               f"({info.get('pdf_method', 'compressed')})")
        c.warn(msg + f"; still above {MAX_PDF_BYTES // 1024 // 1024} MB") if size > MAX_PDF_BYTES else c.note(msg)
    else:
        c.note('paper.pdf is not hosted (host_pdf: false); the button shows "Paper (coming soon)"')
    total = sum(f.stat().st_size for f in site.rglob("*") if f.is_file() and ".git" not in f.relative_to(site).parts)
    c.note(f"site folder: {total / 1e6:.1f} MB")
    return c


def check_browser(review: dict) -> Check:
    c = Check("Browser review")
    if review.get("error"):
        c.warn(review["error"])
        return c
    inherited = set(review["baseline"]["failed"])
    own = [m for m in review["failed"] if m.split("] ", 1)[-1] not in inherited]
    for msg in sorted({m.split("] ", 1)[-1] for m in review["failed"]} & inherited):
        c.warn(f"request fails on the template's original page too: {msg}")
    for msg in own:
        (c.fail if msg.split("] ", 1)[-1][:1].isdigit() and "http" not in msg else c.warn)(f"request failed: {msg}")
    for msg in review["console"]:
        if "Failed to load resource" in msg and not own:
            continue  # the console echo of the inherited failures above
        c.fail(f"console error: {msg}")
    for name, view in review["views"].items():
        if view["scrollWidth"] > view["viewport"] + 1:
            c.fail(f"{name}: horizontal overflow ({view['scrollWidth']}px > {view['viewport']}px): {', '.join(view['overflow'])}")
        for src in view["broken"]:
            c.fail(f"{name}: image did not load: {src}")
    for item in next(iter(review["views"].values()), {}).get("lowContrast", []):
        c.warn(f"low contrast (template colour): {' '.join(item.split())}")
    if not c.details:
        c.note("no console errors, no horizontal overflow at 1280 px and 390 px, all images load")
    return c


def check_layout(content: dict, extracted: dict) -> Check:
    """The ordered block list of content.json: what is on the page, and in which order."""
    c = Check("Layout")
    errors, warnings = content_mod.validate(content, extracted)
    for e in errors:
        c.fail(e)
    for w in warnings:
        c.warn(w)
    names = [f'section "{b.get("title")}"' if b.get("type") == "section" else str(b.get("type"))
             for b in content.get("blocks", []) if isinstance(b, dict)]
    c.note("page order: title area, " + ", ".join(names))
    default = [b["type"] for b in content_mod.default_blocks("", [])]
    kinds = [b.get("type") for b in content.get("blocks", []) if isinstance(b, dict)]
    if [k for k in kinds if k != "section"] != [k for k in default if k != "section"]:
        c.note("the order differs from the template's default (teaser, abstract, sections, bibtex)")
    return c


def _heading_at(title: str, text: str, start: int = 0) -> int:
    """Position of a section heading in the PDF text: at a line start first, anywhere otherwise. -1 if absent."""
    words = r"\s+".join(re.escape(w) for w in pdf_text.squash(title).split())
    for pattern, flags in ((rf"^[ \t]*{words}", re.M), (rf"^[ \t]*{words}", re.M | re.I), (words, re.I)):
        if m := re.compile(pattern, flags).search(text, start):
            return m.start()
    return -1


def skipped_text(sections: list, pdf: PdfText) -> tuple[str, str, list[str]]:
    """(text of the skipped sections, text of the rest of the PDF, problems).

    An entry is a heading title, or {"title", "until": the next heading that is kept, "pages": [..]}.
    Without "until" or "pages" a section runs to the end of the page its heading is on.
    """
    full = "\n".join(pdf.pages)
    offsets = [0]
    for p in pdf.pages:
        offsets.append(offsets[-1] + len(p) + 1)
    spans, problems = [], []
    for entry in sections or []:
        entry = {"title": entry} if isinstance(entry, str) else entry
        title = str(entry.get("title") or "")
        if entry.get("pages"):
            spans += [(offsets[n - 1], offsets[n]) for n in entry["pages"] if 1 <= n <= len(pdf.pages)]
            continue
        start = _heading_at(title, full) if title else -1
        if start < 0:
            problems.append(f'skipped section "{title}": heading not found in the PDF text, so its numbers cannot be checked')
            continue
        end = _heading_at(str(entry["until"]), full, start + len(title)) if entry.get("until") else -1
        if end < 0:
            if entry.get("until"):
                problems.append(f'skipped section "{title}": end heading "{entry["until"]}" not found; '
                                "checked to the end of its page")
            end = next(o for o in offsets if o > start)
        spans.append((start, end))
    inside, outside, pos = [], [], 0
    for a, b in sorted(spans):
        a = max(a, pos)
        if b <= a:
            continue
        outside.append(full[pos:a])
        inside.append(full[a:b])
        pos = b
    outside.append(full[pos:])
    return "\n".join(inside), "\n".join(outside), problems


def check_instructions(content: dict, extracted: dict, html: str, pdf: PdfText, build: Build) -> Check | None:
    """Only when a prompt was used: every instruction is accounted for and skipped content is not on the page."""
    if not content.get("_source", {}).get("prompt_sha1"):
        return None
    c = Check("Instructions")
    applied = content.get("instructions_applied") or []
    if not applied:
        c.fail("a prompt was used but instructions_applied is empty in content.json")
    for n, entry in enumerate(applied):
        status = entry.get("status")
        text = str(entry.get("instruction") or "")[:70]
        if is_todo(status) or is_todo(entry.get("how")) or is_todo(entry.get("instruction")):
            continue  # reported by "Content complete"
        if status not in content_mod.STATUSES:
            c.fail(f'instructions_applied[{n}]: status must be one of {", ".join(content_mod.STATUSES)} (got "{status}")')
        elif not str(entry.get("how") or "").strip():
            c.fail(f'instructions_applied[{n}]: "how" is empty')
        elif status != "applied":
            c.warn(f'{status}: "{text}": {entry["how"]}')
    if build.prompt_used.is_file():
        used = re.sub(r"<!--.*?-->", "", build.prompt_used.read_text(), flags=re.S)
        have = pdf_text.loose_key(" ".join(str(e.get("instruction") or "") for e in applied))
        for instruction in prompt_instructions(used):
            if pdf_text.loose_key(instruction) not in have:
                c.fail(f'instruction of the prompt is missing from instructions_applied: "{instruction[:70]}"')
    excluded = content.get("excluded") or {}
    for kind, shown in (("figures", content_mod.used_figures(content)), ("tables", content_mod.used_tables(content))):
        for item in sorted(set(excluded.get(kind) or []) & set(shown)):
            c.fail(f"{item} is listed in excluded.{kind} but is shown on the page")
    files = {i["id"]: i.get("file") for i in extracted["figures"] + extracted["tables"]}
    for item in (excluded.get("figures") or []) + (excluded.get("tables") or []):
        if files.get(item) and f'static/images/{files[item]}' in html:
            c.fail(f"{item} is excluded but its image is on the page")
    inside, outside, problems = skipped_text(excluded.get("sections") or [], pdf)
    for problem in problems:
        c.warn(problem)
    on_page = pdf_text.numbers_in(visible_text(BeautifulSoup(html, "html.parser"), body_only=True))
    only_skipped = sorted((on_page & pdf_text.numbers_in(inside)) - pdf_text.numbers_in(outside), key=lambda n: (len(n), n))
    if only_skipped:
        c.warn("numbers on the page that the PDF prints only inside a skipped section (check that no text from "
               f"a skipped part is on the page): {', '.join(only_skipped)}")
    if c.status == "PASS":
        titles = [e if isinstance(e, str) else e.get("title", "") for e in excluded.get("sections") or []]
        c.note(f"{len(applied)} instruction(s) applied; excluded: {len(excluded.get('figures') or [])} figure(s), "
               f"{len(excluded.get('tables') or [])} table(s), {len(titles)} section(s)"
               + (f" ({', '.join(titles)})" if titles else "")
               + "; no excluded figure or table and no number found only in a skipped section is on the page")
    return c


def _instruction_lines(content: dict, build: Build) -> list[str]:
    cell = lambda s: str(s or "").replace("|", "/").replace("\n", " ")  # noqa: E731
    source = f"`{build.prompt_used.name}`" if build.prompt_used.is_file() else "the prompt"
    lines = [f"Prompt: {source} (copy of what was passed with --prompt).", "",
             "| # | Instruction | Status | How it was applied, or why not |", "|---|---|---|---|"]
    lines += [f"| {n} | {cell(e.get('instruction'))} | {cell(e.get('status'))} | {cell(e.get('how'))} |"
              for n, e in enumerate(content.get("instructions_applied") or [], 1)]
    excluded = content.get("excluded") or {}
    titles = [e if isinstance(e, str) else e.get("title", "") for e in excluded.get("sections") or []]
    lines += ["", "Left out of the page:",
              f"- sections: {', '.join(titles) or 'none'}",
              f"- figures: {', '.join(excluded.get('figures') or []) or 'none'}",
              f"- tables: {', '.join(excluded.get('tables') or []) or 'none'}"]
    return lines


# ---------- report ----------

def _extraction_lines(extracted: dict, content: dict) -> list[str]:
    shown = {t["id"]: t.get("display", "image") for t in content_mod.shown_tables(content)}
    lines = []
    for f in extracted["figures"]:
        size = f"{f['width']}x{f['height']} px, {f['bytes'] // 1024} KB" if f.get("file") else "no image"
        lines.append(f"- {f['id']} (page {f['page']}{', appendix' if f.get('appendix') else ''}): {f['status']}; "
                     f"{f['method']}; {size}")
    for t in extracted["tables"]:
        grid = [r for r in t["grid"] if any(x.strip() for x in r)]
        html = "numbers verified against the text layer" if t["html_verified"] else "HTML version not verified, image only"
        use = f"shown as {shown[t['id']]}" if t["id"] in shown else "not shown"
        lines.append(f"- {t['id']} (page {t['page']}{', appendix' if t.get('appendix') else ''}): {t['status']}; {t['method']}; "
                     f"grid {len(grid)} x {len(grid[0]) if grid else 0}, {html}; {use}")
    return lines


def check_extraction(extracted: dict) -> Check:
    c = Check("Extraction")
    items = extracted["figures"] + extracted["tables"]
    for i in items:
        if i["status"].startswith("WARN"):
            c.warn(f"{i['id']} (page {i['page']}): {i['status']}")
    manual = [i["id"] for i in items if "manual" in i["status"]]
    c.note(f"{len(extracted['figures'])} figure(s), {len(extracted['tables'])} table(s) by caption-anchored detection"
           + (f"; manual crops: {', '.join(manual)}" if manual else ""))
    return c


def render_report(name: str, checks: list[Check], extracted: dict, content: dict, shots: dict, build: Build) -> str:
    worst = max((ch.status for ch in checks), key=ORDER.get, default="PASS")
    lines = [f"# Quality report: {name}", "", f"Overall: **{worst}**", "", "| Check | Result |", "|---|---|"]
    lines += [f"| {ch.name} | {ch.status} |" for ch in checks]
    for ch in checks:
        lines += ["", f"## {ch.name}: {ch.status}", ""] + [f"- {d}" for d in ch.details]
    if content.get("_source", {}).get("prompt_sha1"):
        lines += ["", "## Instructions applied", ""] + _instruction_lines(content, build)
    lines += ["", "## Extraction", ""] + _extraction_lines(extracted, content)
    if shots:
        lines += ["", "## Screenshots", ""] + [f"- {k}: `screenshots/{Path(v).name}`" for k, v in shots.items()]
    lines += ["", "## Verify manually", ""] + [f"- {m}" for m in MANUAL]
    return "\n".join(lines) + "\n"


def print_summary(checks: list[Check]) -> None:
    print("\nQuality report")
    for ch in checks:
        print(f"  [{ch.status:4}] {ch.name}")
        for d in ch.details if ch.status != "PASS" else ch.details[:1]:
            print(f"           - {d}")


def run(build: Build, template_dir: Path, info: dict) -> list[Check]:
    content = content_mod.load(build)
    extracted = json.loads(build.extracted_json.read_text())
    html = (build.site / "index.html").read_text()
    template_html = (template_dir / "index.html").read_text()
    pdf = PdfText(extracted["pages"])
    print("  check: reviewing the page in headless Chromium ...")
    review = browser.review(build.site, template_dir, build.screenshots)
    checks = [
        check_complete(content, build), check_extraction(extracted), check_layout(content, extracted),
        check_instructions(content, extracted, html, pdf, build), check_skeleton(template_html, html, template_dir),
        check_fidelity(template_html, html, template_dir, build.site, review, info), check_typography(review),
        check_sample_text(template_html, html), check_identity(content, html, pdf), check_abstract(content, html, pdf),
        check_numbers(content, html, pdf), check_tables(content, extracted, pdf),
        check_figures(content, extracted, html), check_bibtex(content, html), check_page_quality(html),
        check_links(html, build.site), check_files(html, build.site, info), check_browser(review),
    ]
    checks = [c for c in checks if c is not None]  # "Instructions" exists only when a prompt was used
    shots = review.get("shots", {})
    build.report.write_text(render_report(content["name"], checks, extracted, content, shots, build))
    build.report_json.write_text(json.dumps({
        "name": content["name"], "title": content["title"],
        "status": max((c.status for c in checks), key=ORDER.get),
        "todos": content_mod.todos(content), "checks": [asdict(c) for c in checks]}, indent=2))
    print_summary(checks)
    return checks
