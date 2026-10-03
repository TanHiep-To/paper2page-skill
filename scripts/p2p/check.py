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
from .common import Build
from .figures import MAX_BYTES
from .pdf_text import PdfText
from .render import cell_number, rank_cells
from .site import MAX_PDF_BYTES
from .template import ATTRIBUTION_HINT, is_local, local_refs, sample_tokens

ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2}
ABSTRACT_MIN_RATIO = 0.95
OUR_CLASSES = {"paper-figure", "paper-table", "row-label"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
OPTIONAL_CLOSE = {"p", "li", "td", "th", "tr", "thead", "tbody", "option", "dt", "dd"}
MANUAL = [
    "Author names (and any display-name overrides), affiliation numbers and the * mark against page 1 of the PDF.",
    "Results table: rows, columns, units against the paper, and the metric directions set in the yaml.",
    "Each figure shows the right graphic for its caption and is cropped cleanly (see extracted/contact_sheet.png).",
    "Tagline and section summaries say only what the paper says.",
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
    todos = content_mod.find_todos(content)
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
    known = OUR_CLASSES | {cls for el in tpl.find_all(class_=True) for cls in el["class"]}
    for f in (template_dir / "static" / "css").glob("*.css"):
        known |= set(re.findall(r"\.(-?[A-Za-z_][\w-]*)", f.read_text(errors="ignore")))
    for cls in sorted({cls for el in page.find_all(class_=True) for cls in el["class"]} - known):
        c.warn(f'CSS class "{cls}" is not defined by the template or Bulma')
    footer = page.find("footer")
    if not footer or not footer.select_one(f'a[href*="{ATTRIBUTION_HINT}"]'):
        c.fail("the template attribution link is missing from the footer")
    if "googletagmanager" in html or "gtag(" in html:
        c.fail("Google Analytics is still present")
    if not c.details:
        c.note(f"{len(want)} head includes, section/container/heading classes, navbar and footer match the template")
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
    if marked and "*" not in pdf.pages[0]:
        c.warn("corresponding author marked, but no * found on page 1 of the PDF")
    if renamed:
        c.note("display-name overrides from the yaml: " + "; ".join(renamed))
    if c.status == "PASS":
        c.note(f'{len(authors)} authors in PDF order; corresponding: {", ".join(marked) or "none marked"}')
    return c


def check_abstract(html: str, pdf: PdfText) -> Check:
    c = Check("Abstract match")
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
    if not content["tables"]:
        c.warn("no numeric results table on the page; confirm the paper has none "
               "(all detected tables are listed in extracted.json)")
        return c
    source = {t["id"]: t for t in extracted["tables"]}
    for t in content["tables"]:
        tag = f"Table {t['number']}"
        src = source.get(t["id"])
        if not src:
            c.fail(f"{tag}: no such table was found in the PDF")
            continue
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
        want = (len(t["rows"]) + 1, len(t["columns"]))
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
        bold = set(src.get("bold_numbers", []))
        if bold and best and best != bold:
            c.warn(f"{tag}: computed best values {sorted(best)} differ from the bold values in the PDF {sorted(bold)}")
        counts = [sum(1 for m in marks.values() if m == k) for k in ("best", "second")]
        c.note(f"{tag}: {len(t['rows'])} rows x {len(t['columns'])} columns; all values found on PDF page {src['page']}; "
               f"{counts[0]} best / {counts[1]} second-best cells computed from the numbers")
    return c


def check_figures(content: dict, extracted: dict, html: str) -> Check:
    c = Check("Figure mapping and captions")
    source = {f["id"]: f for f in extracted["figures"]}
    used = [f for f in extracted["figures"] if f'static/images/{f["file"]}' in html]
    for note in content["figures"]:
        fig = source.get(note["id"])
        if fig and fig in used and fig["caption"]:
            # token_set_ratio is 100 when the caption only uses words of the printed caption
            score = fuzz.token_set_ratio(pdf_text.fuzzy_key(note["caption"]), pdf_text.fuzzy_key(fig["caption"]))
            if score < 90:
                c.warn(f'{note["id"]}: caption differs from the printed caption (similarity {score:.0f}); check the meaning')
    if any(f.get("fallback") for f in used):
        c.warn("no figure captions were found in the PDF; the top half of page 1 is used")
    if not used:
        c.fail("no figure is shown on the page")
    if not c.details:
        c.note(f"{len(used)} figure(s) shown ({', '.join(f['id'] for f in used)}); captions match the printed captions")
    return c


def check_bibtex(content: dict, html: str) -> Check:
    c = Check("BibTeX consistency")
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
        if not table.find("thead") or not table.find("caption"):
            c.fail("table without header row or caption")
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
    for msg in review["console"]:
        c.fail(f"console error: {msg}")
    for msg in review["failed"]:
        (c.fail if "127.0.0.1" in msg else c.warn)(f"request failed: {msg}")
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


# ---------- report ----------

def _extraction_lines(extracted: dict, content: dict) -> list[str]:
    lines = [f"- {f['id']} (page {f['page']}): {f.get('method', 'cropped')}; {f['width']}x{f['height']} px, "
             f"{f['bytes'] // 1024} KB, {f['file'].rsplit('.', 1)[-1].upper()}" for f in extracted["figures"]]
    shown = {t["id"] for t in content["tables"]}
    for t in extracted["tables"]:
        grid = [r for r in t["grid"] if any(x.strip() for x in r)]
        how = f"grid read between the table's rules: {len(grid)} x {len(grid[0])}" if grid else "WARN: grid not detected"
        lines.append(f"- {t['id']} (page {t['page']}): {how}; "
                     f"{'shown on the page' if t['id'] in shown else 'not shown (not a numeric results table)'}")
    return lines


def render_report(name: str, checks: list[Check], extracted: dict, content: dict, shots: dict) -> str:
    worst = max((ch.status for ch in checks), key=ORDER.get, default="PASS")
    lines = [f"# Quality report: {name}", "", f"Overall: **{worst}**", "", "| Check | Result |", "|---|---|"]
    lines += [f"| {ch.name} | {ch.status} |" for ch in checks]
    for ch in checks:
        lines += ["", f"## {ch.name}: {ch.status}", ""] + [f"- {d}" for d in ch.details]
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
    content = json.loads(build.content_json.read_text())
    extracted = json.loads(build.extracted_json.read_text())
    html = (build.site / "index.html").read_text()
    template_html = (template_dir / "index.html").read_text()
    pdf = PdfText(extracted["pages"])
    print("  check: reviewing the page in headless Chromium ...")
    review = browser.review(build.site, build.screenshots)
    checks = [
        check_complete(content, build), check_skeleton(template_html, html, template_dir),
        check_sample_text(template_html, html), check_identity(content, html, pdf), check_abstract(html, pdf),
        check_numbers(content, html, pdf), check_tables(content, extracted, pdf),
        check_figures(content, extracted, html), check_bibtex(content, html), check_page_quality(html),
        check_links(html, build.site), check_files(html, build.site, info), check_browser(review),
    ]
    shots = review.get("shots", {})
    build.report.write_text(render_report(content["name"], checks, extracted, content, shots))
    build.report_json.write_text(json.dumps({
        "name": content["name"], "title": content["title"],
        "status": max((c.status for c in checks), key=ORDER.get),
        "todos": content_mod.find_todos(content), "checks": [asdict(c) for c in checks]}, indent=2))
    print_summary(checks)
    return checks
