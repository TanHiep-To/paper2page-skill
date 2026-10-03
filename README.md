# build-page

A skill for Claude Code and Codex that turns a paper PDF into a project page built on an HTML
template (tested with [Nerfies](https://github.com/nerfies/nerfies.github.io)) and, if asked,
publishes it to its own GitHub repository.

Extraction and rendering are deterministic Python; no paid API is called. The agent's job is to read
the paper, fill the few fields that need judgement, and review the result.

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
`owner`, `home_url` for the navbar home icon, and `template`, which makes `--template` optional.

## The 3-step routine for a new paper

1. Build and review:
   ```
   /build-page --paper path/to/paper.pdf --template path/to/template
   ```
   The agent builds the page, fills the TODO fields from the paper, and shows the screenshots and
   the report.
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
| `--link kind=url` | Link button; `kind=soon` gives a disabled "coming soon" button. Repeatable. |
| `--host-pdf` | Compress the PDF and publish it. Without it the button reads "Paper (coming soon)". |
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
  extracted/       fig1.png ..., extracted.json, contact_sheet.png
  content.json     page content; TODO fields are filled by the agent
  report.md        PASS / WARN / FAIL per check, extraction log, manual checklist
  screenshots/     desktop.png (1280 px), mobile.png (390 px)
  site/            index.html, static/, .nojekyll, paper.pdf (only with host_pdf)
```

Steps, each reading the previous step's output:

| Step | Reads | Writes |
|---|---|---|
| extract | the PDF | `extracted/` |
| content | `extracted.json`, the yaml | `content.json` (kept if it exists for the same PDF) |
| render | `content.json`, the template, the yaml | `site/` |
| check | `site/`, `content.json`, `extracted.json` | `report.md`, `screenshots/` |

Filled from the PDF without an agent: title, authors, affiliations, corresponding-author mark,
abstract (verbatim), keywords, all figures with captions, numeric tables, BibTeX. Left as `TODO`:
tagline, method summary and figure, table interpretation and orientation, qualitative figures.

## Using the tool directly

```bash
P2P="$HOME/.claude/skills/build-page/.venv/bin/python $HOME/.claude/skills/build-page/scripts/paper2page.py"
$P2P build --paper paper.pdf --template ./template          # extract -> content -> render -> check
$P2P build --paper paper.pdf --template ./template --from render   # after editing content.json
$P2P publish build/<name> [--owner Y] [--private] [--pages] [--yes]
```

`build` exits 0 when no check failed, 1 when a check failed (including remaining TODOs), 2 on usage
or environment errors. `publish` refuses to push while a TODO or a failed check remains, exits 3 when
a new repository needs the user's confirmation (`--yes`), updates an existing repository only if it
carries the `.paper2page` marker, and never force-pushes. No token is stored anywhere; GitHub access
is through `gh` only.

## Checks in report.md

Content complete, template skeleton, no template sample content, title / authors / affiliations,
abstract match (fuzzy ratio >= 0.95), numbers check (every number on the page is in the PDF), table
check, figure captions, BibTeX consistency, accessibility and meta tags, links, image and PDF sizes,
browser review (console errors, overflow at 1280 px and 390 px, contrast).

## Code layout

```
SKILL.md            the agent workflow
scripts/paper2page.py
scripts/p2p/
  common.py     settings, names, paths        extract.py   step 1
  pdf_text.py   text, abstract, numbers       content.py   step 2
  figures.py    figure crops                  site.py      step 3
  tables.py     table grids                   check.py     step 4
  render.py     figures, tables, buttons      browser.py   Chromium review
  template.py   copy, prune, patch            publish.py   git / gh
```
