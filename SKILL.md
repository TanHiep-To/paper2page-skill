---
name: build-page
description: Build a project page for a research paper from an HTML template (Nerfies style), review it, and optionally publish it to its own GitHub repository with GitHub Pages.
argument-hint: "--paper <pdf> [--template <dir>] [--name X] [--owner Y] [--link key=url|soon ...] [--host-pdf] [--publish] [--pages] [--private]"
disable-model-invocation: true
---

# build-page

Arguments given by the user: `$ARGUMENTS`

Build a project page for one paper, fill in the parts that need reading the paper, review the result,
and publish it only if asked.

## Paths

- `SKILL` = the folder that contains this file: `${CLAUDE_SKILL_DIR}` in Claude Code. If that variable
  is empty, use `~/.claude/skills/build-page` (Claude Code) or `~/.agents/skills/build-page` (Codex).
- Paths in the arguments are relative to the user's current folder. Do not `cd` elsewhere.
- Tool: `"$SKILL/.venv/bin/python" "$SKILL/scripts/paper2page.py"` (called `P2P` below).
- Outputs go to `./build/<name>/` in the user's current folder:
  `extracted/` (figure crops, `extracted.json`, `contact_sheet.png`), `content.json`, `report.md`,
  `screenshots/desktop.png`, `screenshots/mobile.png`, and `site/` (the page to publish).
- Per-paper settings live in a yaml beside the PDF (`paper.yaml` for `paper.pdf`, otherwise
  `<pdf name>.yaml`). The first build creates it. Command-line flags override it, and it overrides
  `$SKILL/config.yaml` (default `owner`, `home_url`, `template`).

## Workflow

### 1. Parse the arguments
- `--paper <pdf>` is required and must be an existing file. `--template <dir>` must contain
  `index.html`; it may be omitted only if `template:` is set in `$SKILL/config.yaml`.
- If either is missing or invalid, ask the user. Never guess a path or pick a PDF yourself.
- Build flags to pass through: `--template --paper --name --owner --link --host-pdf`.
  Publish flags, used only in step 7: `--publish --pages --private` (and `--owner --name`).

### 2. First run only: set up
If `$SKILL/.venv/bin/python` does not exist, run `"$SKILL/install.sh" --venv-only` and wait for it.

### 3. Build (deterministic, no paid API)
```
P2P build --paper <pdf> [--template <dir>] [--name X] [--owner Y] [--link k=v ...] [--host-pdf]
```
Exit code 1 with only "Content complete: FAIL" is expected here: it means TODO fields remain.
Exit code 2 is a usage or environment error: report it to the user and stop.

### 4. Fill every TODO in `build/<name>/content.json`
Read the paper PDF itself (all of it, not just the abstract) and edit `content.json`.
Keep a note of the PDF page each fact came from; you report it in step 6.

Rules:
- Facts only from the paper. Never invent numbers, links, venues, dates or claims. Every number you
  write must be printed in the paper, in the same form (same decimals, same thousands separators).
- Neutral academic tone. No marketing words ("novel", "state-of-the-art", "powerful") unless quoted
  from the paper. Paragraphs of at most 3 sentences.
- Plain text only: no HTML, Markdown or LaTeX.

Fields:
- `tagline`: exactly one sentence saying what the work is, containing its key number.
- `overview_figure`: id of the figure that gives the best overview (default: `fig1`). Change it if
  another figure is the architecture or teaser figure.
- `method.figure`: id of the main method or system figure; `""` if there is none or it is already
  the overview figure. `method.paragraphs`: 1-3 short paragraphs on how the method works; `[]` to
  omit the section.
- `tables`: numeric results tables detected in the PDF. Compare every cell with the PDF and fix
  extraction errors. Keep only the main results table(s) and delete the others. If the main table
  is missing, copy its grid from `extracted/extracted.json` (`tables[].grid`) using the same shape.
  - `metrics_in`: `"columns"` if each value column is a metric and rows are methods; `"rows"` if
    each row is a metric and the value columns are methods.
  - `directions`: metric label -> `"higher"` or `"lower"`. Set one only when the paper makes the
    direction clear (an arrow, bold best values, or a statement). Otherwise leave it out and tell
    the user to set `metric_directions` in the yaml. Never mark best values yourself: bold and
    underline are computed from the numbers.
  - `interpretation`: 2-3 sentences on what the table shows, using only numbers from the table,
    including any caveat the paper itself states.
- `qualitative`: 1-3 entries `{figure, description}` for figures that show results or the system
  in use; 1-2 sentences each. Do not reuse the overview or method figure. `[]` to omit the section.
- `figures[].caption`: the printed caption. You may shorten it, never change its meaning.
  `figures[].alt`: a short literal description of what the image shows.
- Do not edit: `title`, `authors`, `affiliations`, `abstract_paragraphs` (verbatim from the PDF),
  `bibtex`, `venue`, `year`, `name`, `_source`. If the title or an author is wrong, tell the user;
  display names, venue and year are changed in the yaml, not here.

### 5. Re-render and review
```
P2P build --paper <pdf> [same flags] --from render
```
Then look at these images yourself:
- `build/<name>/extracted/contact_sheet.png`: every crop must contain the whole figure and nothing
  else (no "Fig. N" caption, no body text, nothing cut off). Panel labels and "(a) ... (b) ..."
  sub-captions printed inside a multi-panel figure belong to the figure and are fine.
- `build/<name>/screenshots/desktop.png` and `mobile.png`: layout matches the template, figures are
  readable, the table fits (it may scroll sideways on mobile), nothing overlaps or overflows.

If a figure on the page has a bad crop, use a different figure, or drop it and say so; do not publish
a bad crop. Fix content problems in `content.json` and re-render until `report.md` has no FAIL.
Do not edit `site/index.html` by hand: it is regenerated on every render.

### 6. Report
Show the user:
- the commands you ran;
- the extraction method per figure and table and any WARN (section "Extraction" in `report.md`);
- each TODO field you filled, with the PDF page the facts came from;
- both screenshots;
- the PASS / WARN / FAIL table from `report.md`;
- a short list of what they must verify by hand (section "Verify manually" in `report.md`, plus
  anything you were unsure about).

Stop here unless `--publish` was given.

### 7. Publish (only with `--publish`, and only if `report.md` has no FAIL and no TODO remains)
a. Run `gh auth status`. If `gh` is missing or not logged in, tell the user to run `gh auth login`
   and stop.
b. Owner = `--owner`, else `owner` in `$SKILL/config.yaml`, else `gh api user --jq .login`.
   Repo name = the build name. The tool rejects names that collide with the main site:
   projects, publications, cv, people, course, thesis, demo, blog.
c. Run:
   ```
   P2P publish build/<name> [--owner Y] [--private] [--pages]
   ```
   Exit code 3 and a line starting with `CONFIRM:` mean the repository does not exist yet.
   Show the user owner, name and visibility and ASK them to confirm. Only after they say yes, run
   the same command again with `--yes`. Never add `--yes` on your own.
d. If the repository exists, the tool updates it only when it contains the `.paper2page` marker
   (created by this tool). If it stops because the marker is missing, do not work around it:
   report it and ask the user. The tool never force-pushes; do not force-push either.
e. With `--pages` the tool enables GitHub Pages (main, root) through `gh api` and waits up to about
   2 minutes. Without it, it prints the manual Settings > Pages instructions.
   With `--private`, repeat its warning: Pages on a free account needs a public repository, and
   on paid plans a page served from a private repository is still public. If the user combines
   `--private` and `--pages`, ask them to confirm that the page may be public before running it.
f. Report the repository URL, the page URL `https://<owner>.github.io/<name>/`, and whether Pages
   is live.

### 8. Credentials
Never write tokens or keys to any file and never ask the user for one. GitHub access is only through
the `gh` login that already exists.
