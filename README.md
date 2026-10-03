# build-page

A skill for **Claude Code** and **Codex** that turns a paper PDF into a project page and publishes it
to GitHub Pages with one command.

```
/build-page --paper papers/MyPaper/paper.pdf --publish --pages
```

Extraction and rendering are plain Python (PyMuPDF, no ML models, no paid API). The agent reads the
paper, writes the few sentences that need judgement, and reviews the result. The page follows the
bundled HTML template exactly.

Live examples built with this skill:
[CounterSketch](https://tanhiep-to.github.io/CounterSketch/) ·
[Graphilosophy](https://tanhiep-to.github.io/Graphilosophy/)

## Contents

- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Install](#install)
- [Quick start](#quick-start)
- [The routine for a new paper](#the-routine-for-a-new-paper)
- [Command reference](#command-reference)
- [Per-paper settings](#per-paper-settings)
- [Global defaults and the template](#global-defaults-and-the-template)
- [What the agent does](#what-the-agent-does)
- [Output folder](#output-folder)
- [Fixing a bad crop](#fixing-a-bad-crop)
- [Quality report](#quality-report)
- [Template fidelity](#template-fidelity)
- [Publishing](#publishing)
- [Using the tool without an agent](#using-the-tool-without-an-agent)
- [Troubleshooting](#troubleshooting)
- [Update and uninstall](#update-and-uninstall)
- [Repository layout](#repository-layout)
- [Credits and licence](#credits-and-licence)

## What you get

- Title, authors, affiliations, equal-contribution and corresponding-author marks, abstract
  (verbatim), keywords and BibTeX, read from the PDF.
- Results tables re-typed as HTML when every number verifies against the PDF, with row groups kept
  and the best and second-best values marked from the numbers.
- Every figure and table cropped from the PDF at 300 dpi, with its printed caption.
- Sections for Method, Quantitative Results and Qualitative Results, with a one-sentence tagline.
- A page that follows the template exactly: same fonts, colours, spacing and classes.
- A quality report with PASS / WARN / FAIL per check, plus desktop and mobile screenshots.
- A GitHub repository for the page, with GitHub Pages enabled if you ask for it.

## Requirements

| Needed | Version | Why | Check |
|---|---|---|---|
| macOS or Linux | | The installer is a bash script. On Windows use WSL. | |
| Python | 3.10 or newer | Runs the tool. | `python3 --version` |
| git | any recent | Clones this repo and pushes pages. | `git --version` |
| Claude Code or Codex | current | Runs the skill. | `claude --version` |
| GitHub CLI `gh` | 2.x, logged in | Only for publishing. | `gh auth status` |
| poppler (`pdfimages`) | optional | Extracts original embedded photos. Without it PyMuPDF is used. | `pdfimages -v` |

Disk space: the virtual environment with Chromium for the screenshots takes about 500 MB.

Install the system tools if you do not have them:

```bash
# macOS (Homebrew)
brew install python git gh poppler

# Ubuntu / Debian
sudo apt install python3 python3-venv git gh poppler-utils
```

Log in to GitHub once, if you plan to publish:

```bash
gh auth login
```

## Install

```bash
git clone https://github.com/TanHiep-To/paper2page-skill.git
cd paper2page-skill
./install.sh
```

`install.sh` does four things:

1. Links this folder to `~/.claude/skills/build-page` (Claude Code) and
   `~/.agents/skills/build-page` (Codex).
2. Creates `.venv` in this folder and installs `requirements.txt`.
3. Downloads Chromium for Playwright.
4. Creates `config.yaml` from `config.example.yaml` and reports whether `gh` is logged in.

Because the skill is a link to this folder, `git pull` here updates it everywhere. Use
`./install.sh --copy` if you prefer an independent copy in the skill folders.

Check the installation:

```bash
~/.claude/skills/build-page/.venv/bin/python ~/.claude/skills/build-page/scripts/p2p.py --help
```

Then restart Claude Code (or open a new session) so it picks up the new skill. No further setup is
needed: the template is bundled and the GitHub account is taken from `gh`.

## Quick start

1. Put your paper in the `papers/` folder of this repository, one folder per paper:

   ```
   papers/MyPaper/paper.pdf
   ```

   Everything under `papers/` is ignored by git, so your PDFs are never pushed.

2. Open Claude Code in this repository folder and type:

   ```
   /build-page --paper papers/MyPaper/paper.pdf
   ```

   The agent builds the page, shows you the screenshots and the report, and stops. Nothing is
   published.

3. When you are happy with it:

   ```
   /build-page --paper papers/MyPaper/paper.pdf --publish --pages
   ```

   The agent asks you to confirm the repository name before creating it, pushes the page, enables
   GitHub Pages, and prints the URL `https://<owner>.github.io/<name>/`.

You can also run the command from any other folder with any PDF path; outputs then go to `./build/`
in that folder.

In **Codex**, invoke the same skill as `$build-page` with the same arguments, or pick it from
`/skills`.

## The routine for a new paper

1. **Build and review.**
   ```
   /build-page --paper papers/MyPaper/paper.pdf
   ```
   Look at `build/<name>/screenshots/desktop.png`, `mobile.png` and `build/<name>/report.md`.

2. **Adjust the settings.** The first build creates `papers/MyPaper/paper.yaml`, pre-filled with
   what was detected. Add links, venue, year, author display names and metric directions
   (see [Per-paper settings](#per-paper-settings)), then apply them:
   ```
   /build-page --paper papers/MyPaper/paper.pdf --from content
   ```

3. **Publish.**
   ```
   /build-page --paper papers/MyPaper/paper.pdf --publish --pages
   ```

Later changes use the same command; the existing repository is updated, never force-pushed.

## Command reference

```
/build-page --paper <pdf> [--template <dir>] [--name X] [--owner Y] [--link key=url|soon ...]
            [--host-pdf] [--publish] [--pages] [--private] [--from extract|content|render|check]
```

| Argument | Meaning |
|---|---|
| `--paper <pdf>` | The paper. Required. |
| `--template <dir>` | Template folder. Default: `template` in `config.yaml`, else the bundled `template/`. |
| `--name X` | Repository name = page path. Letters, digits, hyphens. Default: `name` in the yaml, else the short name before the colon in the title, else the paper's folder name (`papers/<Name>/`). |
| `--owner Y` | GitHub account. Default: `owner` in `config.yaml`, else the account `gh` is logged in as. |
| `--link kind=url` | A link button. Repeatable. `kind=soon` gives a disabled "coming soon" button, `kind=none` hides it. Kinds: `code`, `model`, `dataset`, `arxiv`, `video`. |
| `--host-pdf` | Compress the PDF and publish it. Without it the button reads "Paper (coming soon)". |
| `--from <step>` | Start at `extract`, `content`, `render` or `check`, reusing earlier outputs. |
| `--publish` | Create or update the GitHub repository and push. |
| `--pages` | With `--publish`: enable GitHub Pages (branch `main`, root) and wait until it is live. |
| `--private` | With `--publish`: create a private repository. |

Precedence: command-line flags, then the per-paper yaml, then `config.yaml`.

These names are rejected because they collide with pages of a personal site:
`projects, publications, cv, people, course, thesis, demo, blog`.

Paths are relative to the folder where you run the command.

## Per-paper settings

The first build writes a yaml next to the PDF: `paper.yaml` for `paper.pdf`, otherwise
`<pdf name>.yaml`. It is never overwritten, so your edits are kept.

```yaml
name: MyPaper          # repo name = page path

links:                 # a URL, "soon", "none", or empty
  code:                # empty = "Code (coming soon)"; none = no button
  model:
  dataset:
  arxiv: https://arxiv.org/abs/2501.00001
  video: soon

venue: CVPR            # used in the header and in BibTeX
year: 2026

host_pdf: false        # true = compress the PDF (target under 5 MB) and publish it

authors:               # left: as printed in the PDF; right: as shown on the page
  "Minh-Thu Do": "Đỗ Minh Thư"

metric_directions:     # which way is better; wildcards allowed
  "PSNR": higher
  "LPIPS": lower
  "P@*": higher

crop_overrides:        # written by `recrop`; see "Fixing a bad crop"
  fig3: {page: 5, bbox: [120.0, 80.0, 480.0, 300.0]}
```

Buttons: the Paper and Code buttons are always shown; without a URL they read "(coming soon)". The
home icon in the navbar points to the page itself.

`metric_directions` is what makes a results table show the best value in bold and the second best
underlined. The marks are computed from the numbers, never typed by hand.

## Global defaults and the template

`config.yaml` in the skill folder is optional and not tracked by git:

| Key | Meaning |
|---|---|
| `owner` | Default GitHub account for page repositories. Empty: the account `gh` is logged in as. |
| `template` | Absolute path of another template folder. Empty: the bundled `template/`. |

The bundled `template/` is the [Nerfies](https://github.com/nerfies/nerfies.github.io) project page,
without its demo videos (built pages never use them, so the template's own demo page shows empty
video boxes).
To use a different design, point `template` (or `--template`) at a folder that contains an
`index.html` and its `static/` files. The renderer expects the Nerfies structure: a title block with
author and affiliation lines and link buttons, a teaser section, an "Abstract" section, and a BibTeX
section. Never edit a template per paper; all per-paper content comes from `content.json`.

## What the agent does

The full workflow is in [`SKILL.md`](SKILL.md). In short:

1. **Parse** the arguments; ask you if the PDF is missing. Set up the venv on first run.
2. **Extract** (script): find "Figure N" / "Fig. N" / "Table N" captions, crop figures above and
   tables below them, keep sub-figures together, render at 300 dpi, trim margins. If a figure is one
   embedded photo with more pixels than the render, the original image is used.
3. **Review crops** (agent): open the contact sheet and fix any bad crop with `recrop`.
4. **Content** (script, then agent): the script fills everything that can be read from the PDF.
   The agent fills the fields marked `TODO`: tagline, method summary, table interpretation, which
   figure goes in which section. Facts only from the paper, with the source page noted.
5. **Render** (script): clone the template's own blocks and fill them.
6. **Check** (script, then agent): run the quality checks, take screenshots, review them.
7. **Report**: commands run, extraction status, crop fixes, filled fields with page numbers,
   screenshots, checks, and what to verify by hand.
8. **Publish**: only with `--publish`, and only if no check failed and no `TODO` remains.

The agent never invents numbers, links, venues or claims, and never edits styles.

## Output folder

Everything is written to `./build/<name>/` in the folder where the command runs (ignored by git):

```
build/<name>/
  extracted/       fig1.png ..., table1.png ..., extracted.json, contact_sheet.png
  content.json     page content; edit this, then --from render
  report.md        PASS / WARN / FAIL per check, extraction log, manual checklist
  screenshots/     desktop.png, mobile.png, template.png, template_vs_output.png
  site/            the page: index.html, static/, .nojekyll, paper.pdf (only with host_pdf)
```

Each step reads the previous step's output, so a build can be resumed with `--from`:

| Step | Reads | Writes |
|---|---|---|
| `extract` | the PDF, `crop_overrides` | `extracted/` |
| `content` | `extracted.json`, the yaml | `content.json` (kept if it already exists for the same PDF) |
| `render` | `content.json`, the template, the yaml | `site/` |
| `check` | `site/`, `content.json`, `extracted.json` | `report.md`, `screenshots/` |

To change wording on the page, edit `content.json` and run with `--from render`. Do not edit
`site/index.html`: it is regenerated on every render.

## Fixing a bad crop

Open `build/<name>/extracted/contact_sheet.png`. If a crop cuts off part of a figure or includes
caption text, set the box by hand. Boxes are in PDF points with the origin at the top left.

```bash
P2P="$HOME/.claude/skills/build-page/.venv/bin/python $HOME/.claude/skills/build-page/scripts/p2p.py"

$P2P grid   --paper paper.pdf --page 8                               # page image with a coordinate grid
$P2P recrop --paper paper.pdf --fig 1 --page 8 --bbox 140,46,480,270
$P2P recrop --paper paper.pdf --table 3 --page 22 --bbox 170,105,450,196
$P2P recrop --paper paper.pdf --fig 1 --reset                        # back to automatic detection
```

`grid` writes `extracted/grid_p8.png` with lines every 10 pt, labels every 50 pt, and the current
crops outlined in red. `recrop` stores the box under `crop_overrides` in the paper's yaml, so every
later run keeps it. You can also just ask the agent: "the crop of figure 3 is cut off, fix it".

## Quality report

`report.md` lists these checks. A FAIL blocks publishing.

| Check | What it verifies |
|---|---|
| Content complete | No `TODO` field is left in `content.json`. |
| Extraction | Every figure and table has a usable crop. |
| Template skeleton | Head includes, navbar, section and heading classes, footer and attribution match the template. |
| Template fidelity | No added styles, CSS byte-identical, no unknown classes, computed styles equal to the template's page. |
| Typography | No one-word or very short last lines at 1280 px and 390 px; title at most 3 lines, tagline at most 2. |
| No template sample content | No sample title, author, link or analytics code is left. |
| Title, authors, affiliations | Found verbatim on page 1, in the same order. |
| Abstract match | Fuzzy ratio of at least 0.95 against the PDF abstract. |
| Numbers check | Every number on the page occurs in the PDF. |
| Table check | Values and labels are on the table's PDF page; computed best values agree with the paper's bold ones. |
| Figure mapping and captions | Captions use only the wording of the printed caption. |
| BibTeX consistency | Title, authors, venue and year agree with the page. |
| Accessibility and meta | One `h1`, heading order, alt text, title, description, Open Graph tags, favicon. |
| Links | Local links exist; external links answer. |
| Image and PDF sizes | Images under 500 KB; hosted PDF size. |
| Browser review | No console errors, no horizontal overflow, all images load. |

Always check by hand: author names and marks, metric directions, that each figure matches its
caption, venue and year, and whether you may host the PDF publicly.

## Template fidelity

The template is the design system and is followed exactly.

- The renderer builds a block library from the template's own `index.html` (navbar, title, author
  and affiliation lines, link button, teaser, the Abstract section, heading, paragraph, image,
  caption, BibTeX, footer) and only clones those blocks, changing text, `href`, `src` and `alt`.
- Every content section is a clone of the Abstract section, so all sections share one background
  and spacing.
- Figures use the template's image element in its centred media wrapper, with the teaser's caption
  element. Tables use Bulma classes that ship with the template.
- No inline style, `<style>` tag, CSS rule or new class is added. The template's CSS files are
  copied byte for byte.
- Typography is fixed with content only: non-breaking spaces keep names, numbers with their units,
  and the last words of a line together.

Two things are changed outside the page content, both without visual effect, and both listed in the
report: in `static/js/index.js` two calls that preload frames of the removed interpolation widget
are commented out, and Font Awesome font files that the template's CSS refers to but does not ship
are added (downloaded once from cdnjs for the version named in the CSS, then cached).

`screenshots/template_vs_output.png` shows the template's original page next to the built page.

## Publishing

- **New repository.** The agent shows owner, name and visibility and asks you to confirm before
  creating it.
- **Existing repository.** It is updated only if it contains the `.paper2page` marker file, which
  this tool creates. An unrelated repository with the same name is never touched.
- **No force-push.** Updates are ordinary commits on `main`.
- **GitHub Pages.** `--pages` enables Pages on `main` / root and waits up to about 2 minutes. Without
  it, enable it yourself under Settings > Pages > Deploy from a branch > `main` / `(root)`.
- **Private repositories.** Pages on a free account needs a public repository. On paid plans a
  private repository can be served, and the page itself is then public.
- **Credentials.** No token is stored anywhere. GitHub access is only through your `gh` login.
- **The PDF** is published only with `host_pdf: true` or `--host-pdf`.
- **What is pushed.** Only `build/<name>/site/`: the page, the images it uses, and the template
  assets it references. Unused template media are removed, so a page repository is a few MB.

## Using the tool without an agent

The scripts work on their own; you then fill the `TODO` fields in `content.json` yourself.

```bash
P2P="$HOME/.claude/skills/build-page/.venv/bin/python $HOME/.claude/skills/build-page/scripts/p2p.py"

$P2P build --paper papers/MyPaper/paper.pdf        # extract -> content -> render -> check
# edit build/<name>/content.json
$P2P build --paper papers/MyPaper/paper.pdf --from render
$P2P publish build/<name> --pages --yes
```

| Exit code | Meaning |
|---|---|
| 0 | Done; no check failed. |
| 1 | At least one check failed, including remaining `TODO` fields. |
| 2 | Usage or environment error; the message says what to fix. |
| 3 | `publish` only: the repository does not exist yet; run again with `--yes` to create it. |

`--engine mineru` is reserved for PDFs where caption-anchored detection is not enough (scans,
figures without captions). It is not implemented or installed here; the default engine is the
supported path.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/build-page` is not offered | Restart Claude Code. Check that `~/.claude/skills/build-page/SKILL.md` exists. |
| `Template has no index.html` | Point `--template` or `template:` at the folder that contains `index.html`, or leave both empty to use the bundled one. |
| `Chromium could not be started` | Run `~/.claude/skills/build-page/.venv/bin/python -m playwright install chromium`. |
| `GitHub CLI is not logged in` | Run `gh auth login`. |
| `Project name ... collides` | Choose another name with `--name`. |
| `Repository ... has no .paper2page marker` | A repository with that name already exists and was not made by this tool. Use another `--name`. |
| Authors or title are wrong | PDFs with unusual title pages may not parse. Edit `title` / `authors` in `content.json`, then `--from render`. |
| A table is shown as an image | Its numbers could not all be verified against the PDF text, so it is not re-typed. |
| No bold or underline in a table | Set `metric_directions` in the paper's yaml. |
| A figure is missing or cut off | See [Fixing a bad crop](#fixing-a-bad-crop). |
| The page did not change online | GitHub Pages can take a minute; reload without cache. |
| Start over for one paper | Delete `build/<name>/` and build again. The yaml beside the PDF is kept. |

## Update and uninstall

```bash
# update (linked install)
cd paper2page-skill && git pull && ./install.sh

# uninstall
rm ~/.claude/skills/build-page ~/.agents/skills/build-page    # remove the links
rm -rf paper2page-skill                                       # remove the clone
```

Pages you published stay on GitHub; delete those repositories there if you no longer want them.

## Repository layout

```
SKILL.md              the agent workflow (Claude Code and Codex read this)
install.sh            links the skill, creates the venv, installs Chromium, checks gh
requirements.txt      pymupdf, playwright, beautifulsoup4, pillow, rapidfuzz, requests, pyyaml
config.example.yaml   template for the optional config.yaml
template/             the default page template (Nerfies): index.html + static/
papers/               put your papers here, one folder each; ignored by git
build/                generated output; ignored by git
scripts/p2p.py        entry point: build, grid, recrop, publish
scripts/p2plib/
  common.py           settings, names, paths
  pdf_text.py         text, abstract, numbers
  figures.py          figure detection and crops
  tables.py           table detection and grids
  extract.py          step 1
  content.py          step 2
  typo.py             non-breaking spaces, title break
  render.py           table ranking, link kinds
  template.py         copy, prune, fonts
  site.py             step 3
  browser.py          Chromium review
  check.py            step 4
  publish.py          git and gh
```

## Credits and licence

The bundled template in `template/` is the
[Nerfies project page](https://github.com/nerfies/nerfies.github.io) by Keunhong Park, licensed under
[CC BY-SA 4.0](http://creativecommons.org/licenses/by-sa/4.0/). Pages built from it keep the footer
link back to that source, as its licence asks. The template uses [Bulma](https://bulma.io) and
[Font Awesome Free](https://fontawesome.com) (fonts under SIL OFL 1.1).
