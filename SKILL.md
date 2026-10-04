---
name: build-page
description: Build a project page for a research paper from an HTML template (Nerfies style) with one command - extract figures and tables, fill the content, render, check, and optionally publish to its own GitHub repository with GitHub Pages.
argument-hint: "--paper <pdf> [--prompt <file.md|text>] [--template <dir>] [--name X] [--owner Y] [--link key=url|soon ...] [--host-pdf] [--publish] [--pages] [--private] [--from extract|content|render|check]"
disable-model-invocation: true
---

# build-page

Arguments given by the user: `$ARGUMENTS`

One command runs the whole flow: extract, review crops, content, render, check, report, and (only if
asked) publish. Scripts do everything deterministic; you review images and fill what needs reading.

## Paths and settings

- `SKILL` = the folder that contains this file: `${CLAUDE_SKILL_DIR}` in Claude Code. If that variable
  is empty, use `~/.claude/skills/build-page` (Claude Code) or `~/.agents/skills/build-page` (Codex).
- `P2P` = `"$SKILL/.venv/bin/python" "$SKILL/scripts/p2p.py"`.
- Paths in the arguments are relative to the user's current folder. Do not `cd` elsewhere.
- The paper must be in its own folder: `papers/<folder>/<file>.pdf`. Outputs go to `./build/<folder>/`
  in the user's current folder, always named after that folder and never after the paper's title:
  `extracted/` (figN / tableN images, `extracted.json`, `contact_sheet.png`), `content.json`,
  `prompt_used.md` (only with `--prompt`), `report.md`, `screenshots/desktop.png`,
  `screenshots/mobile.png`, `site/` (the page to publish).
- The repository name is `--name`, else `name:` in the yaml, else the folder name. It changes only
  the repository and page path, never the build folder.
- Per-paper settings: a yaml beside the PDF (`paper.yaml` for `paper.pdf`, otherwise
  `<pdf name>.yaml`), created by the first build. Precedence: command-line flags, then that yaml,
  then `$SKILL/config.yaml` (default `owner`, `template`).
- `--from extract|content|render|check` starts the build at that step, using earlier outputs.

## Template fidelity (applies to every step)

The template is the design system and is followed 100%. The renderer only clones blocks that exist
in the template's own `index.html` (navbar, title, author and affiliation lines, link button,
teaser, the Abstract section as the standard section, heading, paragraph, image, caption, BibTeX,
footer) and changes their text, `href`, `src` and `alt`. Every content section is a clone of the
Abstract section, so all sections share one background and spacing. Tables, captions and narrow
figures, for which the template has no block, use Bulma classes already shipped in the template's
CSS. The one addition is the MathJax script tag on a page that shows an equation.

Captions, equations and figure widths are formatted by the renderer; do not try to steer them:
- A caption is a paragraph below the body text size (`is-size-7 has-text-grey-dark`), never a
  heading; its label is bold; it sits below a figure and above a table; one line is centred, more
  lines are justified. Only section titles are headings, and only the teaser caption uses the
  template's subtitle element.
- A figure narrower than the PDF's text width (one column of two, or a small figure) is shown in a
  narrower column; a full-width figure uses the full content width. Nothing is upscaled.
- The checks "Captions", "Equations" and "Figure sizes" in `report.md` fail on a caption in a
  heading or larger than the body text, an equation taller than 3 body lines, and a single-column
  figure wider than 70% of the content width.

The page is flexible in content and order, never in style: `content.json` holds an ordered list of
`blocks` (teaser, abstract, any number of sections with any titles, bibtex) and the renderer follows
it. Leaving a block out removes it from the page. Navbar, title area and footer are fixed.

When you fix a problem you may change only:
- `content.json` (text, the `blocks` list: which blocks, sections, figures and tables, in which order);
- crops, with `recrop`;
- the yaml settings.

You must never:
- add or edit CSS files or rules, `<style>` tags, or `style` attributes;
- add a class that is not in the template's `index.html` or its CSS files;
- change fonts, colours, backgrounds, spacing or heading levels;
- edit `site/index.html`, files under `site/static/`, or anything in the template folder.

If a layout problem cannot be solved by content, crops or settings, do not invent a style: describe
the problem in the report and ask the user. The check "Template fidelity" in `report.md` fails on any
added style, changed CSS file, unknown class, or computed style that differs from the template's
original page, and a FAIL blocks publishing.

## Typography (content-level only)

The renderer already inserts non-breaking spaces so that names, numbers with their units
("Table 3", "23 images"), and the last words of titles, headings, captions and paragraphs stay
together, and it breaks the title after its colon when that gives balanced lines. You handle what
needs judgement:
- The check "Typography" in `report.md` measures the page at 1280 px and 390 px. A one-word last
  line or a last line under 20% of the width in text you wrote (tagline, summaries, interpretation,
  descriptions) is fixed by rewording that text slightly: same facts, same numbers.
- Tagline: at most 2 lines on desktop. If it is longer or unbalanced, rewrite it shorter with the
  same facts and the same key number.
- Title: at most 3 lines on desktop. If it is longer, tell the user; do not change the title.
- Abstract and captions come from the paper: never change their wording. Only non-breaking spaces
  (automatic) and where the abstract is split into paragraphs may change.
- Never fix typography with CSS, `<br>` in `content.json`, or manual `&nbsp;`.

## 1. Parse the arguments and set up

- `--paper <pdf>` is required and must be an existing file. If it is missing or invalid, ask the user.
  Never guess a path or pick a PDF yourself. If the tool says the PDF is not inside
  `papers/<folder>/`, stop and tell the user to move it there; do not move it yourself.
- `--prompt <file.md | text>` is optional: instructions for what to take from the paper and how to
  lay out the page. Pass the user's value through unchanged. A value ending in `.md` must be an
  existing file; any other file type is rejected by the tool; anything else is inline text. Never
  create a prompt file on your own. Without `--prompt`, build the template's default page.
- `--template <dir>` is optional. Without it the tool uses `template:` from `$SKILL/config.yaml`, and
  if that is empty, the template bundled with the skill at `$SKILL/template`. If a given template
  folder has no `index.html`, ask the user.
- Build flags: `--paper --prompt --template --name --owner --link --host-pdf --from`.
  Publish flags, used only in step 8: `--publish --pages --private`.
- First run only: if `$SKILL/.venv/bin/python` does not exist, run `"$SKILL/install.sh" --venv-only`.

## 2. Extract (script)

```
P2P build --paper <pdf> [--prompt <file.md|text>] [--template <dir>] [--name X] [--owner Y] [--link k=v ...] [--host-pdf]
```
This runs all four steps once. The extract step uses PyMuPDF only (no ML, fine on a weak machine):
- Finds "Figure N" / "Fig. N" / "Table N" captions, appendix captions with their own numbering
  ("Figure A1", "Fig. S3", "Table B2" -> ids `figA1`, `figS3`, `tableB2`), and IEEE captions
  ("TABLE II" with the caption on the next line -> `table2`). A PDF that is main paper + appendix is
  one document: `appendix_start_page` in `extracted.json` is the first appendix page, and figures
  and tables are tagged `"part": "main"` or `"part": "appendix"`, also when the appendix continues
  the main numbering. The arXiv margin stamp is removed from all extracted text.
- Finds each figure or table by its caption. Figures are taken above the caption, tables
  below it, each falling back to the other side. Works for single-column and full-width layouts and
  keeps sub-figures (a)(b)(c) together. Renders at 300 dpi and trims white margins.
- If a figure is one embedded photo with more pixels than the render, the original image is kept
  (`pdfimages`, or PyMuPDF when the image has a transparency mask).
- Captions are the exact text of the PDF text layer.
- Every table gets an image. A table also gets an HTML version only if every number in it verifies
  against the PDF text layer (`html_verified` in `extracted.json`).
- Writes `extracted.json` and `contact_sheet.png`.

Exit code 1 with "Content complete: FAIL" is expected on the first run (TODO fields remain).
Exit code 2 is a usage or environment error: report it and stop.

## 3. Review the crops (you)

Open `build/<folder>/extracted/contact_sheet.png`, then open each crop file you are unsure about.
A crop is good when it contains the whole figure or table and nothing else: no "Fig. N" / "Table N"
caption, no body text, nothing cut off. Panel labels and "(a) ... (b) ..." sub-captions printed
inside a multi-panel figure belong to the figure.

Fix a bad crop with the script commands only. Do not write ad-hoc cropping code and do not edit
image files.

```
P2P grid   --paper <pdf> --page P                               # page image with a coordinate grid
P2P recrop --paper <pdf> --fig N   --page P --bbox x0,y0,x1,y1  # PDF points, origin top-left
P2P recrop --paper <pdf> --table N --page P --bbox x0,y0,x1,y1
P2P recrop --paper <pdf> --fig N --reset                        # back to automatic detection
```
`grid` writes `extracted/grid_pP.png`: lines every 10 pt, labels every 50 pt, current crops outlined
in red with their bbox. Read the bbox off the grid, run `recrop`, then open the new crop and check it.
`recrop` saves the box in the yaml under `crop_overrides`, so every later run keeps it.
Repeat until every crop you will use is clean. Keep a list of the fixes for the report.
A figure or table whose status in `extracted.json` starts with `WARN` has no usable crop yet: recrop
it or do not use it.

## 4. Content (script, then you)

The script has already written `build/<folder>/content.json` with title, authors, affiliations,
abstract, figures, captions, tables, BibTeX and the default `blocks`. Read the paper PDF itself (all
of it, not just the abstract) and fill every field that says `TODO`. Note the PDF page of each fact;
you report it in step 7.

Rules:
- Facts only from the paper. Never invent numbers, links, venues, dates or claims. Every number you
  write must be printed in the paper in the same form (same decimals, same separators).
- Neutral academic tone. No marketing words unless quoted from the paper. Paragraphs of at most
  3 sentences. Plain text only: no HTML, Markdown or LaTeX.

### The page: `blocks`

`blocks` is the ordered list the renderer follows:

```json
"blocks": [
  {"type": "teaser", "figure": "fig1", "tagline": "One sentence."},
  {"type": "abstract"},
  {"type": "section", "title": "Method", "items": [
      {"type": "text", "paragraphs": ["...", "..."]},
      {"type": "figure", "id": "fig3"}]},
  {"type": "section", "title": "Quantitative Results", "items": [
      {"type": "table", "id": "table2"},
      {"type": "text", "paragraphs": ["..."]},
      {"type": "figure", "id": "fig7"}]},
  {"type": "bibtex"}
]
```

- Block types: `teaser`, `abstract`, `section`, `bibtex`. At most one teaser, abstract and bibtex;
  any number of sections, in any order, with any titles. Remove a block to leave it off the page.
- Item types inside a section: `text` (1-3 short paragraphs), `figure` (an id from `figures`),
  `table` (an id from `extracted.json`). Items are shown in the order listed. A figure is shown with
  its caption from `figures[]`.
- Without `--prompt`, keep the default order the script wrote: teaser, Abstract, Method,
  Quantitative Results (tables, and figures that report results), Qualitative Results, BibTeX.
  Delete a default section only when the paper has nothing for it.
- `teaser.tagline`: exactly one sentence saying what the work is, containing its key number.
  `teaser.figure`: the figure that gives the best overview (default: the first figure).
- Method: 1-3 short paragraphs on how the method works, and the main method or system figure unless
  it is already the teaser.
- Qualitative sections: figures that show results or the system in use, each with 1-2 sentences.
  Do not show a figure twice.

### Tables

`tables` is the library of numeric tables found in the PDF; a table is on the page only when a
`table` item in `blocks` refers to it. For each table you show:
- `display`: `"html"` (re-typed table with computed highlighting; allowed only when the table's
  `html_verified` is true in `extracted.json`) or `"image"` (the crop from the PDF). When
  `"html"`, compare every cell and every column label with the PDF and fix any difference.
- `metrics_in`: `"columns"` if each value column is a metric and rows are methods; `"rows"` if
  each row is a metric and the value columns are methods.
- `directions`: metric label -> `"higher"` or `"lower"`. Set one only when the paper makes the
  direction clear (an arrow, bold best values, or a statement). Otherwise leave it out and tell
  the user to set `metric_directions` in the yaml. Never mark best values yourself: bold and
  underline are computed from the numbers.
- `interpretation`: 2-3 sentences on what the table shows, using only numbers from the table,
  including any caveat the paper itself states. It is shown below the table.
- A table that is in `extracted.json` but not in `tables` is shown as its image: just refer to its
  id. To give it a note, add `{"id": "table2", "number": "2", "caption": "...", "display": "image",
  "interpretation": "..."}` to `tables`.

### Applying `--prompt`

When a prompt was given, the script saved a copy in `build/<folder>/prompt_used.md` and wrote one
entry per instruction into `instructions_applied`, with `status` and `how` set to `TODO`. Read
`prompt_used.md` first, then:

1. Apply every instruction while filling `blocks`: which blocks exist, their order, the section
   titles, which figures and tables are shown, and how detailed the text is ("only an overview of
   the method" means 1-2 paragraphs and one figure; "a few main results" means the main comparison
   table or tables, not every table).
2. Whatever the prompt skips must not be on the page: no text, no number, no figure and no table
   from it. Record it in `excluded`:
   ```json
   "excluded": {
     "figures": ["fig8", "fig9"],
     "tables": ["table3"],
     "sections": [{"title": "Ablation Study", "until": "User Study"}]
   }
   ```
   `title` is the heading as printed in the PDF; `until` is the next heading that is not skipped
   (or use `"pages": [9, 10]` for whole pages). List every figure and table that belongs to a
   skipped part. The check fails if one of them is on the page, and warns about numbers on the page
   that the PDF prints only inside a skipped section: for each such warning, find the sentence and
   remove or reword it unless the number really comes from a part that is kept.
3. "Use figures from the appendix": pick only from the items with `"part": "appendix"` in
   `extracted.json`, also when the appendix continues the main numbering, and name each chosen
   figure with its number and PDF page in `how`. If the PDF has no appendix, do not substitute
   silently: mark the instruction "not applied", say why, and tell the user.
4. Fill `instructions_applied`: for each instruction, `status` is `"applied"`, `"partly"` or
   `"not applied"`, and `how` says in one or two sentences what you did (blocks, figure and table
   ids, PDF pages) or why it could not be done. Keep the `instruction` text as written. An
   instruction that would need a new style, CSS or a block the template does not have is
   `"not applied"`; say so, do not force it.
5. If an instruction is unclear or contradicts another, ask the user instead of guessing.

A later run with the same prompt keeps your work; a changed prompt resets `instructions_applied` to
`TODO`, and you apply it again. `--prompt` with `--from render` or `--from check` starts at the
content step, because the prompt is applied there. A run without `--prompt` uses `content.json` as
it is.

### Other fields

- `figures[].caption`: the printed caption. You may shorten it, never change its meaning.
  `figures[].alt`: a short literal description of what the image shows.
- Equations: an equation is a manual crop named `figEq<N>` (`recrop --fig Eq1 ...`) with a note in
  `figures[]` whose caption starts with "Equation (N):". Add `"latex"` to that note: the equation
  transcribed from the PDF as LaTeX, without delimiters and without the number, e.g.
  `"latex": "S(e) = \\max\\left(0, \\sum_{f \\in \\mathcal{F}} w_f \\phi_f(e)\\right),"`. Compare every symbol,
  subscript and punctuation mark with the PDF text of that crop (`extracted.json` > `pages`, or the
  crop image) before rendering. It is then rendered with MathJax at the size of the body text. If
  the equation cannot be transcribed with certainty, leave `latex` out: the crop is shown instead,
  scaled to the body text.
- Link buttons: Paper and Code are always shown; without a URL they read "(coming soon)". The home
  icon in the navbar points to the page itself.
- Links: never add or change a link yourself, even one printed in the paper. If the paper gives a
  code, data or project URL, tell the user so they can put it in the yaml or pass `--link`.
- Do not edit `title`, `authors`, `affiliations`, `abstract_paragraphs` (verbatim from the PDF),
  `bibtex`, `venue`, `year`, `name`, `_source`. If the title or an author is wrong, tell the user.
  Display names, venue and year are changed in the yaml, not here.
- A `content.json` written by an older version (with `method`, `qualitative`, ...) is converted to
  `blocks` automatically and gives the same page.

## 5. Render (script)

```
P2P build --paper <pdf> [same flags] --from content
```
Use `--from content` after recrops or yaml changes, `--from render` when only `content.json`
changed. The page is built from the template and `content.json` into `build/<folder>/site/`.
Never edit `site/index.html` by hand: it is regenerated on every render.

## 6. Check (script, then you)

The same command runs every quality check and takes the screenshots. Then look at
`screenshots/desktop.png` and `screenshots/mobile.png` yourself: layout matches the template,
figures are readable, the table fits (it may scroll sideways on mobile), nothing overlaps or
overflows. Open `screenshots/template_vs_output.png` as well: the template's original page and the
built page side by side; navbar, title area and footer must look the same apart from the text.
Read `report.md`, in particular "Layout", "Template fidelity", "Typography", "Captions", "Equations"
and "Figure sizes", and with a prompt
"Instructions": go through `prompt_used.md` once more and confirm on the screenshots that each
instruction is visibly followed and that nothing from a skipped part is on the page. If the paper
has a case file in `$SKILL/tests/cases/`, also run
`"$SKILL/.venv/bin/python" "$SKILL/tests/test_cases.py"` from the user's folder; it compares the
built page with the prompt (layout, shown and skipped figures and tables, appendix figures).

Fix problems at their source and run step 5 again: content problems in `content.json`, bad images
with `recrop`, settings in the yaml. Repeat until `report.md` has no FAIL and the screenshots are
clean.

## 7. Report

Show the user:
- the commands you ran;
- extraction method and status per figure and table (section "Extraction" in `report.md`), with
  every WARN;
- the crop fixes you made (figure or table, page, bbox, why);
- each TODO field you filled, with the PDF page the facts came from;
- with a prompt: the section "Instructions applied" of `report.md` (each instruction, its status and
  how it was applied or why not), and what was left out;
- both screenshots and `screenshots/template_vs_output.png`;
- the "Layout", "Template fidelity", "Typography", "Captions", "Equations" and "Figure sizes" results;
- the PASS / WARN / FAIL table from `report.md`;
- what they must verify by hand (section "Verify manually" in `report.md`, plus anything you were
  unsure about).

Stop here unless `--publish` was given.

## 8. Publish (only with `--publish`, and only if `report.md` has no FAIL and no TODO remains)

a. Run `gh auth status`. If `gh` is missing or not logged in, tell the user to run `gh auth login`
   and stop.
b. Owner = `--owner`, else `owner` in `$SKILL/config.yaml`, else `gh api user --jq .login`.
   Repo name = the build name. The tool rejects names that collide with the main site:
   projects, publications, cv, people, course, thesis, demo, blog.
c. Run:
   ```
   P2P publish build/<folder> [--owner Y] [--private] [--pages]
   ```
   Exit code 3 and a line starting with `CONFIRM:` mean the repository does not exist yet.
   Show the user owner, name and visibility and ASK them to confirm. Only after they say yes, run
   the same command again with `--yes`. Never add `--yes` on your own.
d. If the repository exists, the tool updates it only when it contains the `.paper2page` marker
   (created by this tool). If it stops because the marker is missing, do not work around it:
   report it and ask the user. The tool never force-pushes; do not force-push either.
e. With `--pages` the tool enables GitHub Pages (main, root) through `gh api` and polls the status
   for up to about 2 minutes. Without it, it prints the manual Settings > Pages instructions.
   With `--private`, repeat its warning: Pages on a free account needs a public repository, and on
   paid plans a page served from a private repository is still public. If the user combines
   `--private` and `--pages`, ask them to confirm that the page may be public before running it.
f. Report the repository URL, the page URL `https://<owner>.github.io/<name>/`, and whether Pages
   is live.

Credentials: never write tokens or keys to any file and never ask the user for one. GitHub access is
only through the existing `gh` login.

## Optional heavy engine (off by default)

`--engine mineru` is documented in `README.md` but not installed or bundled. Do not install it
unless the user asks. The default engine needs nothing beyond `requirements.txt`.
