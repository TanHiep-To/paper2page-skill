"""Template handling: copy, sample-text list, asset pruning, small asset patches. The template is only read."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup

from .render import EXTRA_CSS

SKIP_NAMES = {".git", ".DS_Store", "._Icon", "Icon\r", ".github", "node_modules"}
KEEP_AT_ROOT = {"index.html", "paper.pdf", ".nojekyll", ".paper2page"}
ATTRIBUTION_HINT = "nerfies/nerfies.github.io"
ALWAYS_SAMPLE = ["nerfies", "keunhong"]


def reset_dir(site: Path) -> None:
    """Empty the site folder but keep its .git (so a published site can be updated, never force-pushed)."""
    site.mkdir(parents=True, exist_ok=True)
    for child in site.iterdir():
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()


def copy_template(template: Path, out: Path) -> None:
    ignore = lambda _dir, names: [n for n in names if n in SKIP_NAMES or n.startswith("._")]  # noqa: E731
    shutil.copytree(template, out, ignore=ignore, dirs_exist_ok=True)


def is_local(url: str) -> bool:
    if not url or url.startswith(("#", "data:", "mailto:", "javascript:", "tel:")):
        return False
    return not urlparse(url).scheme and not url.startswith("//")


def local_refs(html: str) -> set[str]:
    """Local paths referenced by src / href / poster in the page."""
    soup = BeautifulSoup(html, "html.parser")
    refs = {_norm(val) for tag in soup.find_all(True) for attr in ("src", "href", "poster")
            if isinstance(val := tag.get(attr), str) and is_local(val)}
    return {r for r in refs if r}


def _norm(url: str) -> str:
    return re.sub(r"^(\./)+", "", unquote(urlparse(url).path)).lstrip("/")


def _css_refs(css_file: Path, root: Path) -> set[str]:
    refs = set()
    for m in re.finditer(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", css_file.read_text(errors="ignore")):
        if is_local(m.group(1)):
            target = (css_file.parent / unquote(urlparse(m.group(1)).path)).resolve()
            if target.is_relative_to(root.resolve()):
                refs.add(str(target.relative_to(root.resolve())))
    return refs


def prune_assets(out: Path, html: str) -> list[str]:
    """Delete files that the page (and the CSS it loads) no longer reference."""
    keep = set(local_refs(html))
    for ref in list(keep):
        if ref.endswith(".css") and (out / ref).is_file():
            keep |= _css_refs(out / ref, out)
    removed = []
    for f in sorted(out.rglob("*")):
        rel = f.relative_to(out)
        if not f.is_file() or rel.parts[0] == ".git" or str(rel) in KEEP_AT_ROOT:
            continue
        if str(rel) not in keep:
            f.unlink()
            removed.append(str(rel))
    for d in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if ".git" not in d.relative_to(out).parts and not any(d.iterdir()):
            d.rmdir()
    return removed


def _drop_dead_font_faces(css_file: Path) -> None:
    """Remove @font-face rules whose local font files do not exist (they only cause 404s)."""
    src = css_file.read_text(errors="ignore")

    def keep(m: re.Match) -> str:
        urls = [u for u in re.findall(r"url\(\s*['\"]?([^'\")]+)", m.group(0)) if is_local(u)]
        exists = any((css_file.parent / unquote(urlparse(u).path)).is_file() for u in urls)
        return m.group(0) if exists or not urls else ""

    cleaned = re.sub(r"@font-face\s*\{[^}]*\}", keep, src)
    if cleaned != src:
        css_file.write_text(cleaned)


def patch_assets(out: Path, html: str) -> None:
    """Append our few CSS rules; stop index.js from preloading frames of removed widgets."""
    for css_file in (out / "static" / "css").glob("*.css"):
        _drop_dead_font_faces(css_file)
    css = out / "static" / "css" / "index.css"
    if css.is_file() and "paper2page additions" not in css.read_text():
        css.write_text(css.read_text().rstrip() + "\n" + EXTRA_CSS)
    js = out / "static" / "js" / "index.js"
    if js.is_file() and "interpolation-image-wrapper" not in html:
        src = js.read_text()
        src = re.sub(r"^(\s*)(preloadInterpolationImages\(\);|setInterpolationImage\(0\);)", r"\1// \2", src, flags=re.M)
        js.write_text(src)


def sample_tokens(template_html: str) -> list[str]:
    """Strings that identify the template's demo content: its title, authors and sample links."""
    soup = BeautifulSoup(template_html, "html.parser")
    tokens = set(ALWAYS_SAMPLE)
    if soup.title and soup.title.string:
        tokens.add(soup.title.string.strip())
    for a in soup.select(".publication-authors a"):
        if len(a.get_text(strip=True)) > 4:
            tokens.add(a.get_text(" ", strip=True))
    for a in soup.select("body a[href]"):
        url = urlparse(a["href"])  # the full sample URL, so that e.g. github.com itself stays usable
        if url.netloc and "creativecommons.org" not in a["href"] and ATTRIBUTION_HINT not in a["href"]:
            tokens.add((url.netloc + url.path).rstrip("/").lower())
    if any("googletagmanager" in s["src"] for s in soup.find_all("script", src=True)):
        tokens.add("googletagmanager")
    return sorted(t for t in tokens if t)
