"""Case tests: does a built page do what its prompt asked?

Each tests/cases/<folder>.json describes one paper: the prompt it was built with (or none) and what the
page must then look like. The tests read the finished build in build/<folder>/ (content.json,
extracted.json, report.json, report.md, prompt_used.md, site/index.html). A case whose build folder
does not exist is skipped, so the suite also runs on a machine without the papers.

Run from the folder that holds papers/ and build/:
    .venv/bin/python -m unittest tests.test_cases -v
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bs4 import BeautifulSoup  # noqa: E402

from p2plib import check, content  # noqa: E402
from p2plib.common import prompt_instructions  # noqa: E402

BUILD = Path("build")
DEFAULT_SECTIONS = ["Abstract", "Method", "Quantitative Results", "Qualitative Results"]


def text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ")).strip()


class Case:
    def __init__(self, spec: dict) -> None:
        self.spec, self.expect = spec, spec["expect"]
        self.dir = BUILD / spec["folder"]
        self.content = json.loads((self.dir / "content.json").read_text())
        self.extracted = json.loads((self.dir / "extracted" / "extracted.json").read_text())
        self.report = json.loads((self.dir / "report.json").read_text())
        self.report_md = (self.dir / "report.md").read_text()
        self.page = BeautifulSoup((self.dir / "site" / "index.html").read_text(), "html.parser")
        self.items = {i["file"]: i for i in self.extracted["figures"] + self.extracted["tables"] if i.get("file")}
        self.sections = [s for s in self.page.select("section.section") if s.get("id") != "BibTeX"]

    def media(self, scope) -> list[str]:
        """Ids of the figure and table images inside `scope`, in page order."""
        ids = [self.items[Path(img["src"]).name]["id"] for img in scope.find_all("img") if Path(img["src"]).name in self.items]
        return ids

    def section(self, title: str):
        return next(s for s in self.sections if text(s.find("h2")) == title)

    def shown(self) -> list[str]:
        """Ids of everything shown: images in page order, then the tables re-typed as HTML."""
        html_tables = [t["id"] for t in content.shown_tables(self.content) if t.get("display") == "html" and t.get("columns")]
        assert len(self.page.find_all("table")) == len(html_tables), "HTML tables on the page do not match content.json"
        return self.media(self.page) + html_tables


def load_cases() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((ROOT / "tests" / "cases").glob("*.json"))]


class BuiltCases(unittest.TestCase):
    maxDiff = None

    def each(self, only_prompt: bool | None = None) -> list[Case]:
        specs = [s for s in load_cases() if (only_prompt is None or bool(s.get("prompt")) == only_prompt)
                 and (BUILD / s["folder"] / "report.json").is_file()]
        if not specs:
            self.skipTest("no built case in ./build (build the papers of tests/cases first)")
        return [Case(s) for s in specs]

    # ---- every case ----

    def test_build_has_no_fail_and_no_todo(self):
        for c in self.each():
            with self.subTest(case=c.spec["folder"]):
                failed = [x["name"] for x in c.report["checks"] if x["status"] == "FAIL"]
                self.assertEqual(failed, [])
                self.assertEqual(c.report["todos"], [])
                self.assertEqual(c.dir.name, Path(c.spec["paper"]).parent.name)  # build folder = folder in papers/

    def test_page_order_is_the_block_order(self):
        for c in self.each():
            with self.subTest(case=c.spec["folder"]):
                titles = [text(s.find("h2")) for s in c.sections]
                self.assertEqual(titles, c.expect["sections"])
                from_blocks = ["Abstract" if b["type"] == "abstract" else b["title"]
                               for b in c.content["blocks"] if b["type"] in ("abstract", "section")]
                self.assertEqual(titles, from_blocks)
                self.assertEqual(c.page.select_one("section.teaser") is not None, c.expect["teaser"])
                self.assertEqual(c.page.select_one("#BibTeX") is not None, c.expect["bibtex"])

    def test_figures_and_tables_on_the_page(self):
        for c in self.each():
            with self.subTest(case=c.spec["folder"]):
                shown = c.shown()
                if "figures" in c.expect:
                    self.assertEqual([i for i in shown if i.startswith("fig")], c.expect["figures"])
                self.assertEqual(sorted(i for i in shown if i.startswith("table")), sorted(c.expect["tables"]))
                for title, figures in c.expect.get("section_figures", {}).items():
                    self.assertEqual([i for i in c.media(c.section(title)) if i.startswith("fig")], figures)
                for item in c.expect["not_on_page"]:
                    self.assertNotIn(item, shown)

    # ---- cases without a prompt: the template's default page ----

    def test_without_prompt_the_page_is_the_default(self):
        for c in self.each(only_prompt=False):
            with self.subTest(case=c.spec["folder"]):
                self.assertEqual(c.expect["sections"], DEFAULT_SECTIONS)
                self.assertTrue(c.expect["teaser"] and c.expect["bibtex"])
                self.assertFalse((c.dir / "prompt_used.md").exists())
                self.assertNotIn("## Instructions applied", c.report_md)
                self.assertNotIn("Instructions", [x["name"] for x in c.report["checks"]])

    # ---- cases with a prompt: the prompt must have had its effect ----

    def test_prompt_is_recorded_and_every_instruction_is_answered(self):
        for c in self.each(only_prompt=True):
            with self.subTest(case=c.spec["folder"]):
                used = re.sub(r"<!--.*?-->", "", (c.dir / "prompt_used.md").read_text(), flags=re.S)
                self.assertEqual(prompt_instructions(used), c.spec["prompt"])
                applied = c.content["instructions_applied"]
                self.assertEqual([e["instruction"] for e in applied], c.spec["prompt"])
                self.assertEqual([e["status"] for e in applied], c.expect["statuses"])
                for e in applied:
                    self.assertGreater(len(e["how"]), 40)
                self.assertIn("## Instructions applied", c.report_md)
                for n, instruction in enumerate(c.spec["prompt"], 1):
                    self.assertIn(f"| {n} | {instruction.replace('|', '/')} |", c.report_md)

    def test_prompt_changed_the_page(self):
        for c in self.each(only_prompt=True):
            with self.subTest(case=c.spec["folder"]):
                layout = (c.expect["teaser"], c.expect["sections"])
                self.assertNotEqual(layout, (True, DEFAULT_SECTIONS), "the page is still the template's default layout")

    def test_skipped_parts_are_not_on_the_page(self):
        for c in self.each(only_prompt=True):
            with self.subTest(case=c.spec["folder"]):
                shown = c.shown()
                excluded = c.content.get("excluded") or {}
                for item in (excluded.get("figures") or []) + (excluded.get("tables") or []):
                    self.assertNotIn(item, shown)
                    self.assertNotIn(f'static/images/{next((f for f, i in c.items.items() if i["id"] == item), "-")}', str(c.page))
                body = check.visible_text(c.page, body_only=True).lower()
                for phrase in c.expect["absent_text"]:
                    self.assertNotIn(phrase.lower(), body, f'"{phrase}" comes from a skipped part')
                instructions = next(x for x in c.report["checks"] if x["name"] == "Instructions")
                self.assertNotEqual(instructions["status"], "FAIL")
                self.assertFalse([d for d in instructions["details"] if "only inside a skipped section" in d])

    def test_overview_only_means_a_short_section(self):
        for c in self.each(only_prompt=True):
            with self.subTest(case=c.spec["folder"]):
                for title, most in c.expect.get("max_method_paragraphs", {}).items():
                    section = c.section(title)
                    self.assertLessEqual(len(section.select(".content p")), most)
                    self.assertLessEqual(len(c.media(section)), 1)

    def test_appendix_figures_come_from_the_appendix(self):
        for c in self.each(only_prompt=True):
            with self.subTest(case=c.spec["folder"]):
                wanted = c.expect.get("appendix_figures")
                if not wanted:
                    continue
                start = c.extracted["appendix_start_page"]
                self.assertIsNotNone(start)
                if "appendix_start_page" in c.expect:
                    self.assertEqual(start, c.expect["appendix_start_page"])
                parts = {f["id"]: f for f in c.extracted["figures"]}
                for fig in wanted:
                    self.assertEqual(parts[fig]["part"], "appendix")
                    self.assertGreaterEqual(parts[fig]["page"], start)
                    self.assertIn(fig, content.used_figures(c.content))
                self.assertTrue(any(f["part"] == "main" for f in c.extracted["figures"]))


if __name__ == "__main__":
    unittest.main()
