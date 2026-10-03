"""Headless Chromium review: screenshots, console errors, overflow, broken images, contrast."""
from __future__ import annotations

import functools
import http.server
import threading
from pathlib import Path

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


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass


def _serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/index.html"


def review(out: Path, shots_dir: Path) -> dict:
    """Open the built page at each viewport. Returns screenshots and findings; never raises on page problems."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright is not installed (pip install playwright && playwright install chromium)."}
    shots_dir.mkdir(parents=True, exist_ok=True)
    server, url = _serve(out)
    result: dict = {"shots": {}, "console": [], "failed": [], "views": {}}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for name, (w, h) in VIEWPORTS.items():
                page = browser.new_page(viewport={"width": w, "height": h})
                page.on("console", lambda m, n=name: m.type == "error" and result["console"].append(f"[{n}] {m.text}"))
                page.on("pageerror", lambda e, n=name: result["console"].append(f"[{n}] {e}"))
                page.on("requestfailed", lambda r, n=name: result["failed"].append(f"[{n}] {r.failure} {r.url}"))
                page.on("response", lambda r, n=name: r.status >= 400 and result["failed"].append(f"[{n}] {r.status} {r.url}"))
                page.goto(url, wait_until="load")
                page.evaluate("document.querySelectorAll('img').forEach(i => i.loading = 'eager')")
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightError:
                    pass
                result["views"][name] = page.evaluate(_PAGE_PROBE)
                shot = shots_dir / f"{name}.png"
                page.screenshot(path=str(shot), full_page=True)
                result["shots"][name] = shot
                page.close()
            browser.close()
    except PlaywrightError as e:
        result["error"] = f"Chromium could not be started: {str(e).splitlines()[0]} (run `playwright install chromium`)."
    finally:
        server.shutdown()
    result["console"] = sorted(set(result["console"]))
    result["failed"] = sorted(set(result["failed"]))
    return result
