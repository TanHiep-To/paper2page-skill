"""Headless Chromium: screenshots, console errors, overflow, contrast, typography, and the
computed-style comparison between the template's original page and the built page."""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

from PIL import Image, ImageDraw

VIEWPORTS = {"desktop": (1280, 900), "mobile": (390, 844)}

_PAGE_PROBE = """
() => {
  const vw = document.documentElement.clientWidth;
  const overflow = [];
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('.table-container') && !el.classList.contains('table-container')) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.right > vw + 1) {
      overflow.push(el.tagName.toLowerCase() + (el.className && el.className.baseVal === undefined ? '.' + String(el.className).trim().split(/\\s+/).join('.') : ''));
    }
  }
  const broken = [...document.images].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.getAttribute('src'));
  const lum = c => { const a = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); });
                     return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2]; };
  const rgb = s => (s.match(/[\\d.]+/g) || []).map(Number);
  const bgOf = el => { for (let e = el; e; e = e.parentElement) { const c = rgb(getComputedStyle(e).backgroundColor);
                       if (c.length >= 3 && (c.length < 4 || c[3] > 0.5)) return c; } return [255, 255, 255]; };
  const low = new Map();
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('[disabled]')) continue;
    const hasText = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim().length > 1);
    if (!hasText || !el.getClientRects().length) continue;
    const fg = rgb(getComputedStyle(el).color), bg = bgOf(el);
    const l1 = lum(fg), l2 = lum(bg);
    const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
    if (ratio < 4.5) {
      const key = el.tagName.toLowerCase() + ' ' + getComputedStyle(el).color;
      if (!low.has(key)) low.set(key, ratio.toFixed(2) + ':1 (' + el.textContent.trim().slice(0, 30) + ')');
    }
  }
  return {
    scrollWidth: document.documentElement.scrollWidth, viewport: vw,
    overflow: [...new Set(overflow)].slice(0, 8), broken,
    lowContrast: [...low].map(([k, v]) => k + ' -> ' + v).slice(0, 8),
  };
}
"""

# Line layout of text elements: how many lines, and how many words / how wide the last line is.
_TYPO_PROBE = """
() => {
  const targets = [
    ['title', 'h1'], ['tagline', '.teaser h2'], ['heading', 'section.section h2.title'],
    ['caption', 'section.section h2.subtitle'], ['paragraph', 'section.section p'],
  ];
  const out = [];
  for (const [kind, selector] of targets) {
    for (const el of document.querySelectorAll(selector)) {
      const words = [];
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const re = /[^ \\t\\n\\r]+/g;  // a run joined by non-breaking spaces is one unit
        for (let m = re.exec(node.textContent); m; m = re.exec(node.textContent)) {
          const range = document.createRange();
          range.setStart(node, m.index); range.setEnd(node, m.index + m[0].length);
          const rects = [...range.getClientRects()];
          if (!rects.length) continue;
          const last = rects[rects.length - 1];
          words.push({top: last.top, left: last.left, right: last.right, n: m[0].split('\\u00a0').length});
        }
      }
      if (!words.length) continue;
      const lines = [];
      for (const w of words) {
        const line = lines.find(l => Math.abs(l.top - w.top) < 4);
        if (line) { line.left = Math.min(line.left, w.left); line.right = Math.max(line.right, w.right); line.words += w.n; }
        else lines.push({top: w.top, left: w.left, right: w.right, words: w.n});
      }
      lines.sort((a, b) => a.top - b.top);
      const widest = Math.max(...lines.map(l => l.right - l.left));
      const last = lines[lines.length - 1];
      out.push({kind, text: el.textContent.trim().replace(/\\s+/g, ' ').slice(0, 48), lines: lines.length,
                lastWords: last.words, lastRatio: (last.right - last.left) / widest,
                endsWithUrl: /(https?:\/\/|www\.)\S+$/.test(el.textContent.trim())});
    }
  }
  return out;
}
"""

# Computed styles that define the template's look.
_STYLE_PROBE = """
() => {
  const pick = (el, props) => { if (!el) return null; const cs = getComputedStyle(el); const o = {};
                                for (const p of props) o[p] = cs[p]; return o; };
  const text = ['fontFamily', 'fontSize', 'fontWeight', 'color', 'lineHeight'];
  const box = ['backgroundColor', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft'];
  const sections = [...document.querySelectorAll('body > section')].map(s => ({
    sig: 'section.' + [...s.classList].sort().join('.'), ...pick(s, box)}));
  return {
    'body': pick(document.body, ['fontFamily', 'fontSize', 'color', 'backgroundColor']),
    'h1': pick(document.querySelector('h1'), [...text, 'textAlign', 'marginBottom']),
    'section h2 (h2.title.is-3)': pick(document.querySelector('section.section h2.title.is-3'), [...text, 'textAlign', 'marginBottom']),
    'paragraph (section .content p)': pick(document.querySelector('section.section .content p'), [...text, 'textAlign']),
    'teaser caption (.teaser h2.subtitle)': pick(document.querySelector('.teaser h2.subtitle'), text),
    'link (footer a)': pick(document.querySelector('footer .content p a[href]'), ['color', 'textDecorationLine']),
    'author line (.publication-authors)': pick(document.querySelector('.publication-authors'), text),
    'link button (.link-block a)': pick(document.querySelector('.link-block a'), ['fontSize', 'color', 'backgroundColor', 'borderRadius', 'height']),
    'navbar': pick(document.querySelector('nav.navbar'), ['backgroundColor', 'minHeight']),
    'title area (.hero-body)': pick(document.querySelector('.hero-body'), box),
    'footer': pick(document.querySelector('footer'), box),
    '#BibTeX': pick(document.querySelector('#BibTeX'), box),
    sections,
  };
}
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):  # noqa: D102
        pass


def _serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/index.html"


def _visit(browser, url: str, name: str, size: tuple[int, int], shot: Path, log: dict, probes: dict) -> dict:
    from playwright.sync_api import Error as PlaywrightError
    page = browser.new_page(viewport={"width": size[0], "height": size[1]})
    path = lambda u: "/" + u.split("/", 3)[-1]  # noqa: E731
    page.on("console", lambda m: m.type == "error" and log["console"].append(f"[{name}] {m.text}"))
    page.on("pageerror", lambda e: log["console"].append(f"[{name}] {e}"))
    page.on("response", lambda r: r.status >= 400 and log["failed"].append(
        f"[{name}] {r.status} {path(r.url) if '127.0.0.1' in r.url else r.url}"))
    try:
        page.goto(url, wait_until="load", timeout=30000)
    except PlaywrightError:
        pass  # heavy template media may still be loading; the DOM and CSS are in place
    page.evaluate("document.querySelectorAll('img').forEach(i => i.loading = 'eager')")
    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except PlaywrightError:
        pass
    result = {key: page.evaluate(js) for key, js in probes.items()}
    page.screenshot(path=str(shot), full_page=True)
    page.close()
    return result


def _side_by_side(template_shot: Path, output_shot: Path, out: Path) -> None:
    """Top (navbar + title area) and bottom (footer) of both pages next to each other."""
    with Image.open(template_shot) as a, Image.open(output_shot) as b:
        a, b = a.convert("RGB"), b.convert("RGB")
        top, foot, gap, label = 760, 330, 24, 26
        w = a.width
        sheet = Image.new("RGB", (2 * w + gap, 2 * label + top + foot + gap), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for i, (img, name) in enumerate(((a, "TEMPLATE (original page)"), (b, "OUTPUT (built page)"))):
            x = i * (w + gap)
            draw.text((x + 8, 6), f"{name}: navbar and title area", fill=(0, 0, 0))
            sheet.paste(img.crop((0, 0, w, top)), (x, label))
            draw.text((x + 8, label + top + gap // 2), f"{name}: footer", fill=(0, 0, 0))
            sheet.paste(img.crop((0, max(0, img.height - foot), w, img.height)), (x, 2 * label + top + gap))
        draw.line((w + gap // 2, 0, w + gap // 2, sheet.height), fill=(200, 0, 0), width=2)
        sheet.save(out, optimize=True)


def review(site: Path, template_dir: Path, shots_dir: Path) -> dict:
    """Open the built page at each viewport and the template's original page at desktop width."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright is not installed (pip install playwright && playwright install chromium)."}
    shots_dir.mkdir(parents=True, exist_ok=True)
    site_server, site_url = _serve(site)
    tpl_server, tpl_url = _serve(template_dir)
    result: dict = {"shots": {}, "console": [], "failed": [], "views": {}, "typography": {}, "styles": {},
                    "baseline": {"console": [], "failed": []}}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for name, size in VIEWPORTS.items():
                shot = shots_dir / f"{name}.png"
                probes = {"view": _PAGE_PROBE, "typo": _TYPO_PROBE}
                if name == "desktop":
                    probes["styles"] = _STYLE_PROBE
                got = _visit(browser, site_url, name, size, shot, result, probes)
                result["views"][name], result["typography"][name] = got["view"], got["typo"]
                result["shots"][name] = shot
                if name == "desktop":
                    result["styles"]["output"] = got["styles"]
            tpl_shot = shots_dir / "template.png"
            got = _visit(browser, tpl_url, "template", VIEWPORTS["desktop"], tpl_shot, result["baseline"],
                         {"styles": _STYLE_PROBE})
            result["styles"]["template"] = got["styles"]
            result["shots"]["template"] = tpl_shot
            browser.close()
        compare = shots_dir / "template_vs_output.png"
        _side_by_side(tpl_shot, shots_dir / "desktop.png", compare)
        result["shots"]["template_vs_output"] = compare
    except PlaywrightError as e:
        result["error"] = f"Chromium could not be started: {str(e).splitlines()[0]} (run `playwright install chromium`)."
    finally:
        site_server.shutdown()
        tpl_server.shutdown()
    for key in ("console", "failed"):
        result[key] = sorted(set(result[key]))
        result["baseline"][key] = sorted({m.split("] ", 1)[-1] for m in result["baseline"][key]})
    return result
