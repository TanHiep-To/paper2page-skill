# build-page

A skill for Claude Code and Codex that turns a paper PDF into a project page built on an HTML
template (tested with [Nerfies](https://github.com/nerfies/nerfies.github.io)) and, if asked,
publishes it to its own GitHub repository.

One command runs the whole flow: extract figures and tables, review the crops, fill the content,
render, check, report, and optionally publish. Extraction and rendering are deterministic Python
(PyMuPDF, no ML, no paid API). The agent reads the paper, fills the few fields that need judgement,
and reviews the crops and screenshots.

## Install

```bash
git clone <this repo> paper2page-skill && cd paper2page-skill && ./install.sh
```

`install.sh` links this folder to `~/.claude/skills/build-page` and `~/.agents/skills/build-page`,
creates `.venv` here, installs the requirements and Chromium for Playwright, and checks `gh`.
Use `./install.sh --copy` to copy instead of link.

You also need a template folder (an `index.html` plus `static/`), for example a clone of the Nerfies
repository. Publishing needs the [GitHub CLI](https://cli.github.com), logged in with `gh auth login`.

Optional defaults go in `config.yaml` in the skill folder (created from `config.example.yaml`):
`owner` and `template`, which makes `--template` optional.

## The 3-step routine for a new paper

1. Build and review:
   ```
   /build-page --paper path/to/paper.pdf --template path/to/template
   ```
   The agent extracts figures and tables, fixes bad crops, fills the TODO fields from the paper,
   and shows the screenshots and the report.
2. Edit the yaml that the first build created beside the PDF (`paper.yaml` for `paper.pdf`, else
   `<pdf name>.yaml`): links, venue, year, author display names, metric directions, `host_pdf`.
   Run the same command again to apply it.
3. Publish:
   ```
   /build-page --paper path/to/paper.pdf --template path/to/template --publish --pages
   ```

In Codex, invoke the skill as `$build-page` with the same arguments (or pick it from `/skills`).

## Arguments

| Argument | Meaning |
|---|---|
| `--paper <pdf>` | The paper. Required. |
| `--template <dir>` | Template folder. Optional if `template` is set in `config.yaml`. |
| `--name X` | Repo name = page path. Default: `name` in the yaml, else the short name in the title. |
| `--owner Y` | GitHub account. Default: `owner` in `config.yaml`, else `gh api user --jq .login`. |
| `--link kind=url` | Link button; `kind=soon` gives a disabled "coming soon" button. Repeatable. The Code button is always shown ("Code (coming soon)" without a URL); `code=none` hides it. |
| `--host-pdf` | Compress the PDF and publish it. Without it the button reads "Paper (coming soon)". |
| `--from step` | Start at `extract`, `content`, `render` or `check`, reusing earlier outputs. |
| `--publish` | Create or update the GitHub repository and push. |
| `--pages` | With `--publish`: enable GitHub Pages (main, root) and wait for it. |
| `--private` | With `--publish`: create a private repository. Pages on a free account needs a public one. |

Precedence: command-line flags, then the per-paper yaml, then `config.yaml`.

Names that collide with the main site are rejected:
`projects, publications, cv, people, course, thesis, demo, blog`.

## What is produced

Everything goes to `./build/<name>/` in the folder where the command runs:

```
build/<name>/
  extracted/       fig1.png ..., table1.png ..., extracted.json, contact_sheet.png
  content.json     page content; TODO fields are filled by the agent
  report.md        PASS / WARN / FAIL per check, extraction log, manual checklist
  screenshots/     desktop.png (1280 px), mobile.png (390 px), template.png, template_vs_output.png
  site/            index.html, static/, .nojekyll, paper.pdf (only with host_pdf)
```

Steps, each reading the previous step's output:

| Step | Reads | Writes |
|---|---|---|
| extract | the PDF | `extracted/` |
| content | `extracted.json`, the yaml | `content.json` (kept if it exists for the same PDF) |
| render | `content.json`, the template, the yaml | `site/` |
| check | `site/`, `content.json`, `extracted.json` | `report.md`, `screenshots/` |

## Extraction

- Captions "Figure N" / "Fig. N" / "Table N" anchor the detection. Figures are taken above the
  caption and tables below it, each falling back to the other side; single-column and full-width
  layouts; sub-figures (a)(b)(c) stay together. Rendered at 300 dpi, white margins trimmed, PNG for
  diagrams and JPEG for photographic content, each under 500 KB.
- If a figure is one embedded photo with more pixels than the 300 dpi render, the original image is
  used (`pdfimages` from poppler when installed; PyMuPDF otherwise and for images with a
  transparency mask).
- Captions are the exact text of the PDF text layer.
- Every table gets an image. It also gets an HTML version only if every number in its grid is found
  in the PDF text layer; only then may `content.json` use `"display": "html"`, which adds bold for
  the best and underline for the second-best value, computed from the numbers.
- `extracted/contact_sheet.png` shows every crop for review.

Fixing a crop (saved under `crop_overrides` in the paper's yaml, so reruns keep it):

```bash
$P2P grid   --paper paper.pdf --page 8                              # page with a coordinate grid
$P2P recrop --paper paper.pdf --fig 1 --page 8 --bbox 140,46,480,270   # PDF points, origin top-left
$P2P recrop --paper paper.pdf --table 3 --page 22 --bbox 170,105,450,196
$P2P recrop --paper paper.pdf --fig 1 --reset
```

### Optional: MinerU engine

`--engine mineru` is reserved for papers where caption-anchored detection is not enough (scanned
PDFs, figures without captions). It is off by default, not installed by `install.sh`, and not
implemented in this repository: MinerU downloads layout models of several GB and wants a GPU or a
lot of RAM. To try it, install it yourself in a separate environment (`pip install mineru`), run it
on the PDF, and set crops from its output with `recrop`. The default engine stays the supported path.

Filled from the PDF without an agent: title, authors, affiliations, corresponding-author mark,
abstract (verbatim), keywords, all figures and tables with captions, BibTeX. Left as `TODO`:
tagline, method summary and figure, table interpretation and orientation, qualitative figures.

## Using the tool directly

```bash
P2P="$HOME/.claude/skills/build-page/.venv/bin/python $HOME/.claude/skills/build-page/scripts/p2p.py"
$P2P build --paper paper.pdf --template ./template          # extract -> content -> render -> check
$P2P build --paper paper.pdf --template ./template --from render   # after editing content.json
$P2P publish build/<name> [--owner Y] [--private] [--pages] [--yes]
```

`build` exits 0 when no check failed, 1 when a check failed (including remaining TODOs), 2 on usage
or environment errors. `publish` refuses to push while a TODO or a failed check remains, exits 3 when
a new repository needs the user's confirmation (`--yes`), updates an existing repository only if it
carries the `.paper2page` marker, and never force-pushes. No token is stored anywhere; GitHub access
is through `gh` only.

## Template fidelity

The template is the design system. The renderer builds a block library from the template's own
`index.html` and only clones those blocks, changing text, `href`, `src` and `alt`:

- every content section (Method, Quantitative Results, Qualitative Results) is a clone of the
  Abstract section, so all sections share one background and spacing;
- figures use the template's image element in its centred media wrapper, with the teaser's caption
  element;
- tables use Bulma classes that ship with the template (`table-container`, `table`,
  `is-fullwidth`, `is-hoverable`);
- no inline style, `<style>` tag, CSS rule or new class is ever added, and the template's CSS files
  are copied byte for byte. The one template file the tool changes is `static/js/index.js`, where two
  calls that preload frames of the removed interpolation widget are commented out. Font Awesome font
  files that the template's CSS points to but the template does not ship are added unchanged (fetched
  once from cdnjs for the version named in the CSS), which removes the template's 404 requests;
- the navbar home icon points to the page itself.

The check "Template fidelity" fails, and blocks publishing, on any added style, changed CSS file,
unknown class, or computed style (fonts, colours, section backgrounds and padding) that differs
from the template's original page as rendered in Chromium. `screenshots/template_vs_output.png`
shows both pages side by side.

## Typography

Content-level only, no CSS: non-breaking spaces keep author names, numbers with their units, and
the last words of titles, headings, captions and paragraphs together; the title breaks after its
colon when that gives balanced lines. The check "Typography" measures every text block at 1280 px
and 390 px and reports one-word or very short last lines, a title over 3 lines, or a tagline over
2 lines. The abstract and captions are never reworded.

## Checks in report.md

Content complete, extraction, template skeleton, template fidelity, typography, no template sample content, title / authors / affiliations,
abstract match (fuzzy ratio >= 0.95), numbers check (every number on the page is in the PDF), table
check, figure captions, BibTeX consistency, accessibility and meta tags, links, image and PDF sizes,
browser review (console errors, overflow at 1280 px and 390 px, contrast).

## Code layout

```
SKILL.md            the agent workflow
scripts/p2p.py      entry point: build, grid, recrop, publish
scripts/p2plib/
  common.py     settings, names, paths        extract.py   step 1
  pdf_text.py   text, abstract, numbers       content.py   step 2
  figures.py    figure detection and crops    site.py      step 3
  tables.py     table detection and grids     check.py     step 4
  render.py     table ranking, link kinds     browser.py   Chromium review
  typo.py       non-breaking spaces, title
  template.py   copy, prune, patch            publish.py   git / gh
```
