"""Tests for the build folder rule, --prompt, appendix captions, ordered blocks and the new checks.

Run from the repository root:  .venv/bin/python -m unittest discover tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from bs4 import BeautifulSoup  # noqa: E402

from PIL import Image  # noqa: E402

from p2plib import check, content, figures, pdf_text, render as render_mod, site, tables  # noqa: E402
from p2plib.common import P2PError, paper_folder, prompt_instructions, resolve_prompt, validate_name  # noqa: E402
from p2plib.pdf_text import PdfText  # noqa: E402

TEMPLATE = ROOT / "template"
EXTRACTED = {
    "figures": [{"id": f"fig{n}", "number": n, "caption": f"Fig. {n}: Caption {n}.", "fallback": False,
                 "file": f"fig{n}.png", "width": 800, "height": 400, "page": n} for n in (1, 2, 3)],
    "tables": [{"id": "table1", "number": "1", "caption": "Main results.", "file": "table1.png", "width": 800,
                "height": 300, "page": 2, "grid": [["Method", "Acc"], ["Ours", "91.5"]], "html_verified": True}],
}


def make_content(blocks):
    return {"_source": {}, "name": "Demo", "title": "Demo: A Paper", "venue": "", "year": "", "keywords": [],
            "meta_description": "A paper.", "abstract_paragraphs": ["The abstract."], "blocks": blocks, "tables": [],
            "authors": [{"name": "Ada Lovelace", "pdf_name": "Ada Lovelace", "affiliations": [1], "corresponding": False}],
            "affiliations": [{"index": 1, "name": "Some University"}],
            "figures": [{"id": f"fig{n}", "caption": f"Caption {n}.", "alt": f"Figure {n}"} for n in (1, 2, 3)],
            "bibtex": "@misc{x,\n  title = {Demo: A Paper},\n}"}


def render(blocks, layout=None, extracted=EXTRACTED, figures=()):
    data = make_content(blocks)
    data["figures"] += list(figures)
    html, used = site.build_html((TEMPLATE / "index.html").read_text(), data, extracted,
                                 {"code": "soon"}, False, "./", "", layout)
    return BeautifulSoup(html, "html.parser"), used


class FolderAndName(unittest.TestCase):
    def test_build_folder_is_the_papers_folder(self):
        self.assertEqual(paper_folder(Path("papers/VG_Cap/paper.pdf")), "VG_Cap")
        self.assertEqual(paper_folder(Path("papers/CogCanvas/Some Title.pdf")), "CogCanvas")

    def test_pdf_outside_papers_is_rejected(self):
        with self.assertRaisesRegex(P2PError, r"not inside papers/<folder>/"):
            paper_folder(Path("cases/CPAM.pdf"))
        with self.assertRaisesRegex(P2PError, r"not inside papers/<folder>/"):
            paper_folder(Path("papers/paper.pdf"))

    def test_names(self):
        self.assertEqual(validate_name("VG_Cap"), "VG_Cap")
        for bad in ("my paper", "demo", "-x"):
            with self.assertRaises(P2PError):
                validate_name(bad)


class Prompt(unittest.TestCase):
    def test_none_means_default(self):
        self.assertIsNone(resolve_prompt(None))

    def test_inline_text(self):
        p = resolve_prompt("No teaser: start with the abstract.")
        self.assertEqual((p["source"], p["text"]), ("inline text", "No teaser: start with the abstract."))

    def test_md_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "prompt.md"
            f.write_text("- Skip the ablation study.\n- Only the overview of the method,\n  no details.\n")
            p = resolve_prompt(str(f))
            self.assertEqual(prompt_instructions(p["text"]),
                             ["Skip the ablation study.", "Only the overview of the method, no details."])

    def test_missing_md_and_other_extensions_are_rejected(self):
        with self.assertRaisesRegex(P2PError, "Prompt file not found"):
            resolve_prompt("papers/X/missing.md")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "prompt.txt"
            f.write_text("- x")
            with self.assertRaisesRegex(P2PError, r"takes a \.md file or inline text"):
                resolve_prompt(str(f))
        with self.assertRaisesRegex(P2PError, r"takes a \.md file or inline text"):
            resolve_prompt("notes.docx")

    def test_prompt_is_recorded_once(self):
        c = make_content(content.default_blocks("fig1", []))
        p = resolve_prompt("- No teaser.\n- Skip the ablation study.")
        self.assertTrue(content.apply_prompt(c, p))
        self.assertEqual([e["instruction"] for e in c["instructions_applied"]], ["No teaser.", "Skip the ablation study."])
        self.assertTrue(any(t.startswith("instructions_applied") for t in content.todos(c)))
        self.assertFalse(content.apply_prompt(c, p))  # same prompt again: what the agent filled in is kept
        self.assertFalse(content.apply_prompt(c, None))


class Captions(unittest.TestCase):
    def test_figure_labels(self):
        cases = {"Figure A1: x": "A1", "Fig. S3. x": "S3", "Figure B.2: x": "B.2", "Fig. 12: x": "12", "FIGURE 4. x": "4"}
        for text, label in cases.items():
            self.assertEqual(figures.CAPTION_RE.match(text).group(1), label)
        self.assertIsNone(figures.CAPTION_RE.match("Figure 3 shows that"))
        self.assertEqual(figures.label_key("B.2"), "B2")
        self.assertEqual(figures.label_number("3"), 3)
        self.assertEqual(figures.label_number("A1"), "A1")
        self.assertEqual(sorted(["A1", "10", "2", "S3"], key=figures.label_order), ["2", "10", "A1", "S3"])

    def test_table_labels(self):
        cases = {"Table B2: x": "B2", "Table 3. x": "3", "TABLE II\nCOMPARISON WITH": "2", "Table IV: x": "4"}
        for text, key in cases.items():
            self.assertEqual(tables.table_key(tables.caption_match(text).group(1)), key)
        self.assertIsNone(tables.caption_match("Table 2 shows the results"))
        self.assertIsNone(tables.caption_match("Table I\nshows the results"))

    def test_appendix_restart_keeps_both(self):
        found = {"1": {"page": 2}}
        self.assertEqual(figures.unique_key("1", 15, found, 13), "App1")
        self.assertEqual(figures.unique_key("1", 15, found, None), "1")
        self.assertEqual(figures.unique_key("A1", 15, found, 13), "A1")


class AppendixTags(unittest.TestCase):
    def test_lettered_captions_mark_the_appendix_but_manual_crops_do_not(self):
        from p2plib.extract import _mark_appendix
        items = [{"id": "fig1", "page": 2, "caption": "Fig. 1: x"}, {"id": "figEq1", "page": 6, "caption": ""},
                 {"id": "fig4", "page": 11, "caption": "Fig. 4: y"}]
        self.assertIsNone(_mark_appendix(items, None))
        self.assertEqual([i["part"] for i in items], ["main", "main", "main"])
        items.append({"id": "figA1", "page": 14, "caption": "Figure A1: z"})
        self.assertEqual(_mark_appendix(items, None), 14)
        self.assertEqual([i["part"] for i in items], ["main", "main", "main", "appendix"])


class Blocks(unittest.TestCase):
    OLD = {"overview_figure": "fig1", "tagline": "A tagline.", "method": {"figure": "fig2", "paragraphs": ["How it works."]},
           "tables": [{"id": "table1"}], "quantitative_figures": [{"figure": "fig3", "description": "A chart."}],
           "qualitative": [{"figure": "", "description": "Only text."}]}

    def test_old_content_is_upgraded(self):
        c = json.loads(json.dumps(self.OLD))
        self.assertTrue(content.upgrade(c))
        self.assertFalse(content.upgrade(c))
        self.assertEqual([b["type"] for b in c["blocks"]], ["teaser", "abstract", "section", "section", "section", "bibtex"])
        self.assertEqual([b.get("title") for b in c["blocks"] if b["type"] == "section"],
                         ["Method", "Quantitative Results", "Qualitative Results"])
        self.assertEqual(content.used_figures(c), ["fig1", "fig2", "fig3"])
        self.assertEqual(content.used_tables(c), ["table1"])
        self.assertNotIn("method", c)

    def test_validation(self):
        ok = make_content([{"type": "abstract"}, {"type": "section", "title": "Method",
                                                  "items": [{"type": "figure", "id": "fig1"}]}])
        self.assertEqual(content.validate(ok, EXTRACTED), ([], []))
        bad = make_content([{"type": "abstract"}, {"type": "abstract"}, {"type": "video"},
                            {"type": "section", "title": "", "items": [{"type": "figure", "id": "fig9"},
                                                                       {"type": "table", "id": "table7"}]}])
        errors = " | ".join(content.validate(bad, EXTRACTED)[0])
        for expected in ('unknown block type "video"', "needs a title", '"fig9" was not extracted',
                         '"table7" was not found', 'more than one "abstract"'):
            self.assertIn(expected, errors)

    def test_unused_library_tables_do_not_block(self):
        c = make_content([{"type": "abstract"}])
        c["tables"] = [{"id": "table1", "interpretation": "TODO"}]
        self.assertEqual(content.todos(c), [])
        c["blocks"].append({"type": "section", "title": "Results", "items": [{"type": "table", "id": "table1"}]})
        self.assertEqual(content.todos(c), ["tables[0].interpretation"])


class Rendering(unittest.TestCase):
    def setUp(self):
        self.template = BeautifulSoup((TEMPLATE / "index.html").read_text(), "html.parser")

    def assert_template_only(self, page):
        known = check._known_classes(self.template, TEMPLATE)
        used = {cls for el in page.find_all(class_=True) for cls in el["class"]}
        self.assertEqual(used - known, set())
        allowed = {el["style"].strip() for el in self.template.find_all(style=True)}
        self.assertEqual({el["style"].strip() for el in page.find_all(style=True)} - allowed, set())
        self.assertLessEqual(len(page.find_all("style")), len(self.template.find_all("style")))

    def test_default_order(self):
        page, used = render(content.upgrade(c := json.loads(json.dumps(Blocks.OLD))) and c["blocks"])
        self.assertIsNotNone(page.select_one("section.teaser img"))
        self.assertEqual([h.get_text(strip=True) for h in page.select("section.section h2.title")],
                         ["Abstract", "Method", "Quantitative Results", "Qualitative Results", "BibTeX"])
        self.assertEqual(used[0], "fig1")
        self.assert_template_only(page)

    def test_no_teaser_reordered_no_bibtex(self):
        page, used = render([
            {"type": "abstract"},
            {"type": "section", "title": "Results", "items": [{"type": "table", "id": "table1"}]},
            {"type": "section", "title": "Dataset", "items": [{"type": "text", "paragraphs": ["About the data."]},
                                                              {"type": "figure", "id": "fig2"},
                                                              {"type": "text", "paragraphs": ["More."]}]},
            {"type": "section", "title": "Method", "items": [{"type": "figure", "id": "fig3"}]},
        ])
        self.assertIsNone(page.select_one("section.teaser"))
        self.assertIsNone(page.select_one("#BibTeX"))
        self.assertEqual([h.get_text(strip=True) for h in page.select("section.section h2.title")],
                         ["Abstract", "Results", "Dataset", "Method"])
        self.assertEqual(used, ["table1", "fig2", "fig3"])
        self.assertIn("fig2.png", page.select_one('meta[property="og:image"]')["content"])
        self.assertEqual(len(page.find_all("h1")), 1)
        self.assertTrue(page.body.find_all(recursive=False)[-1].name == "footer")
        self.assert_template_only(page)

    def test_teaser_after_abstract(self):
        page, _ = render([{"type": "abstract"}, {"type": "teaser", "figure": "fig1", "tagline": "One sentence here."},
                          {"type": "bibtex"}])
        order = [("teaser" if "teaser" in s.get("class", []) else s.get("id") or "section")
                 for s in page.body.find_all("section", recursive=False)][1:]
        self.assertEqual(order, ["section", "teaser", "BibTeX"])


class Formatting(unittest.TestCase):
    """Captions, single-column figures and equations."""
    SECTION = [{"type": "section", "title": "Results", "items": [
        {"type": "figure", "id": "fig2"}, {"type": "table", "id": "table1"}, {"type": "figure", "id": "figEq1"}]}]
    WIDTHS = {"fig2": [100, 50, 300, 150], "figEq1": [200, 100, 300, 120]}  # PDF points; the text is 400 wide
    EQUATION = {"id": "figEq1", "caption": "Equation (1): the score.", "alt": "Equation"}

    def page(self, layout=None, **note):
        extracted = json.loads(json.dumps(EXTRACTED))
        extracted["figures"].append({"id": "figEq1", "number": "Eq1", "caption": "", "fallback": False,
                                     "file": "figEq1.png", "width": 600, "height": 120, "page": 3})
        for f in extracted["figures"]:
            f["bbox"] = self.WIDTHS.get(f["id"], [0, 0, 400, 100])
        return render(self.SECTION, layout, extracted, [{**self.EQUATION, **note}])

    def test_captions_are_paragraphs_with_a_bold_label(self):
        page, _ = self.page()
        self.assertEqual([h.get_text(strip=True) for h in page.select("section.section h2")], ["Results"])
        captions = page.select(render_mod.CAPTION_SELECTOR)
        self.assertEqual([c.strong.get_text().replace("\u00a0", " ") for c in captions],
                         ["Figure 2.", "Table 1.", "Equation (1):"])
        self.assertEqual({tuple(c["class"][:2]) for c in captions}, {tuple(render_mod.CAPTION_CLASSES)})
        figure, table = captions[0], captions[1]
        self.assertEqual(figure.find_previous_sibling().name, "img")       # below the image
        self.assertEqual(table.find_next_sibling().name, "div")            # above the table
        self.assertIsNotNone(table.find_next_sibling().find("img"))

    def test_only_classes_of_the_template(self):
        tpl = BeautifulSoup((TEMPLATE / "index.html").read_text(), "html.parser")
        page, _ = self.page({"text_width": 400, "body_pt": 10, "body_px": 16,
                             "align": [["has-text-justified", "has-text-centered-desktop"]]})
        used = {cls for el in page.find_all(class_=True) for cls in el["class"]}
        self.assertEqual(used - check._known_classes(tpl, TEMPLATE), set())
        self.assertEqual({el["style"] for el in page.find_all(style=True)} - {el["style"] for el in tpl.find_all(style=True)}, set())

    def test_single_column_figure_is_narrower(self):
        page, _ = self.page({"text_width": 400})
        narrow = page.select("div.columns > div.column.is-8.is-offset-2 img")
        self.assertEqual([i["src"].split("/")[-1] for i in narrow], ["fig2.png"])  # 200 of 400 pt; table1 spans the text
        page, _ = self.page()
        self.assertFalse(page.select("div.column.is-offset-2"))

    def test_alignment_follows_the_measured_lines(self):
        self.assertEqual(site.caption_alignment({"mobile": [3, 1], "tablet-only": [1, 1], "desktop": [1, 1]}, ["a", "b"]),
                         [["has-text-justified", "has-text-centered-tablet-only", "has-text-centered-desktop"],
                          ["has-text-centered"]])
        self.assertEqual(site.caption_alignment({}, ["Short.", "x" * 200]), [["has-text-centered"], ["has-text-justified"]])
        page, _ = self.page({"align": [["has-text-centered"]]})
        self.assertIn("has-text-centered", page.select(render_mod.CAPTION_SELECTOR)[0]["class"])

    def test_equation_with_latex_uses_mathjax(self):
        layout = {}
        page, used = self.page(layout, latex="S(e) = \\max(0, x)")
        scripts = [s["src"] for s in page.select("head script[src]") if "mathjax" in s["src"]]
        self.assertEqual(scripts, [render_mod.MATHJAX_SRC])
        self.assertIn("\\[S(e) = \\max(0, x)\\]", page.get_text())
        self.assertNotIn("figEq1", used)
        self.assertEqual(layout["equations"], [{"id": "figEq1", "mode": "mathjax", "file": ""}])

    def test_equation_without_latex_is_the_crop_at_body_scale(self):
        page, used = self.page({"body_pt": 10, "body_px": 16})
        img = page.select_one('img[src$="figEq1.png"]')
        self.assertEqual((img["width"], img["height"]), ("160", "32"))  # 100 x 20 pt at 16 px per 10 pt
        self.assertFalse([s for s in page.select("head script[src]") if "mathjax" in s["src"]])
        self.assertIn("figEq1", used)

    def test_checks(self):
        ok = {"bodyPx": 16, "line": 24, "unrendered": 0, "captions": [{"text": "Figure 1. x", "tag": "p", "heading": "", "fontPx": 12}],
              "equations": [{"kind": "mathjax", "height": 60}],
              "figures": [{"file": "fig2.png", "width": 488, "natural": 800, "content": 744}]}
        review, info = {"format": {"desktop": ok}}, {"narrow": ["fig2.png"], "equations": [{"id": "figEq1"}]}
        html = '<section><h2 class="title is-3">Results</h2><p>Figure 1. x</p></section>'
        self.assertEqual([check.check_captions(html, review).status, check.check_equations(review, info).status,
                          check.check_figure_sizes(review, info).status], ["PASS"] * 3)
        bad = json.loads(json.dumps(ok))
        bad["captions"][0]["fontPx"], bad["equations"][0]["height"], bad["figures"][0]["width"] = 20, 173, 744
        review = {"format": {"desktop": bad}}
        heading = '<section><h2 class="subtitle">Figure 1. x</h2></section>'
        self.assertEqual([check.check_captions(html, review).status, check.check_captions(heading, {"format": {"desktop": ok}}).status,
                          check.check_equations(review, info).status, check.check_figure_sizes(review, info).status], ["FAIL"] * 4)


class SkippedText(unittest.TestCase):
    PDF = PdfText(["Intro\nWe report 91.5 accuracy.\n4.3\nAblation Study\nRemoving X gives 77.7 and 91.5.\n"
                   "4.4\nUser Study\nWe asked 30 people.", "Table text\n12.5\n30\nConclusion with 2024."])

    def test_section_span(self):
        inside, outside, problems = check.skipped_text([{"title": "Ablation Study", "until": "User Study"}], [], self.PDF)
        self.assertEqual(problems, [])
        self.assertIn("77.7", inside)
        self.assertNotIn("77.7", outside)
        self.assertIn("91.5", outside)   # also printed in a kept part
        self.assertIn("30 people", outside)

    def test_missing_heading_is_reported(self):
        _, _, problems = check.skipped_text(["Preliminary Analysis"], [], self.PDF)
        self.assertEqual(len(problems), 1)

    def test_excluded_table_cells(self):
        table = {"id": "table2", "page": 2, "grid": [["12.5", "30"]]}
        inside, outside, _ = check.skipped_text([], [table], self.PDF)
        self.assertIn("12.5", inside)
        self.assertNotIn("12.5", outside)
        self.assertIn("2024", outside)


class ArxivStamp(unittest.TestCase):
    def test_stamp_is_removed_from_text(self):
        text = "pipeline shows\narXiv:2606.15867v3  [cs.CV]  30 Sep 2026\nthat object fidelity drops"
        self.assertEqual(pdf_text.clean(text), "pipeline shows\nthat object fidelity drops")
        self.assertEqual(pdf_text.squash("shows arXiv:2006.11239v2 [cs.LG] 16 Dec 2020 that"), "shows that")
        self.assertEqual(pdf_text.clean("arXiv:hep-th/9901001v1 [hep-th] 1 Jan 1999\nTitle"), "Title")

    def test_citations_are_kept(self):
        for kept in ("arXiv preprint arXiv:2006.09011, 2020.", "arXiv:1909.12000 (2019)", "see arXiv:2006.06676v1, 2020."):
            self.assertEqual(pdf_text.clean(kept), kept)

    def test_abstract_has_no_stamp(self):
        pdf = PdfText([pdf_text.clean("Title\nAbstract\nWe study X and the pipeline shows\n"
                                      "arXiv:2606.15867v3  [cs.CV]  30 Sep 2026\nthat Y holds.\n1 Introduction\nText")])
        self.assertEqual(pdf_text.extract_abstract(pdf), "We study X and the pipeline shows that Y holds.")
        self.assertNotIn("2606.15867", pdf.numbers)


class ImageSize(unittest.TestCase):
    def big_image(self, tmp: Path, name: str) -> dict:
        img = Image.effect_noise((3200, 1700), 90).convert("RGB")  # noise: the worst case for JPEG
        img.save(tmp / name, "JPEG", quality=95)
        return {"id": "fig1", "file": name, "width": 3200, "height": 1700}

    def test_large_image_is_compressed_for_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            item = self.big_image(tmp, "fig1.jpg")
            self.assertGreater((tmp / "fig1.jpg").stat().st_size, figures.MAX_BYTES)
            out = site.web_images([item], tmp)
            self.assertLessEqual(len(out["fig1.jpg"]), figures.MAX_BYTES)
            self.assertLess(item["width"], 3200)
            self.assertAlmostEqual(item["width"] / item["height"], 3200 / 1700, places=1)
            with Image.open(__import__("io").BytesIO(out["fig1.jpg"])) as small:
                self.assertEqual(small.size, (item["width"], item["height"]))

    def test_small_image_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            Image.new("RGB", (800, 400), (200, 30, 30)).save(tmp / "fig2.png")
            item = {"id": "fig2", "file": "fig2.png", "width": 800, "height": 400}
            self.assertEqual(site.web_images([item], tmp), {})
            self.assertEqual((item["width"], item["height"]), (800, 400))


if __name__ == "__main__":
    unittest.main()
