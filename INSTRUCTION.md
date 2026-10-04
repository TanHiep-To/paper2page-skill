# Instructions

How to use `/build-page`: the command, its arguments, the settings file, and a full example.
For installation see [`README.md`](README.md).

- [The command](#the-command)
- [Arguments](#arguments)
- [Per-paper settings](#per-paper-settings)
- [Example: GenKOL](#example-genkol)
- [Output folder](#output-folder)
- [Fixing a bad crop](#fixing-a-bad-crop)
- [Running without an agent](#running-without-an-agent)
- [Troubleshooting](#troubleshooting)

## The command

```
/build-page --paper <pdf> [--template <dir>] [--name X] [--owner Y] [--link kind=url ...]
            [--host-pdf] [--from extract|content|render|check]
            [--publish] [--pages] [--private]
```

One command runs four steps and then, if asked, publishes:

| Step | What happens | Writes |
|---|---|---|
| `extract` | Finds figures, tables, title, authors and abstract in the PDF | `extracted/` |
| `content` | Fills the page content; the agent writes tagline and summaries | `content.json` |
| `render` | Builds the page from the template | `site/` |
| `check` | Runs the quality checks and takes screenshots | `report.md`, `screenshots/` |

Paths are relative to the folder where you run the command. In Codex, use `$build-page`.

## Arguments

### Build

| Argument | Purpose | Default |
|---|---|---|
| `--paper <pdf>` | The paper to build from. **Required.** | |
| `--template <dir>` | Use another page template (a folder with `index.html` and `static/`). | `template` in `config.yaml`, else the bundled `template/` |
| `--name X` | Repository name and page path. Letters, digits, hyphens. | `name` in the yaml, else the short name before the colon in the title, else the paper's folder name |
| `--owner Y` | GitHub account that owns the page. | `owner` in `config.yaml`, else the `gh` login |
| `--link kind=url` | Add a link button. Repeatable. Kinds: `code`, `model`, `dataset`, `arxiv`, `video`. Use `kind=soon` for a disabled "coming soon" button, `kind=none` to hide it. | Paper and Code read "(coming soon)" |
| `--host-pdf` | Compress the PDF and publish it behind the Paper button. | Off |
| `--from <step>` | Start at `extract`, `content`, `render` or `check` and reuse earlier outputs. | `extract` |

Which `--from` to use:

| You changed | Use |
|---|---|
| The yaml, or a crop | `--from content` |
| Only `content.json` | `--from render` |

### Publish

| Argument | Purpose |
|---|---|
| `--publish` | Create or update the GitHub repository and push the page. Blocked while any check is FAIL. |
| `--pages` | With `--publish`: enable GitHub Pages (`main`, root) and wait until it is live. |
| `--private` | With `--publish`: create a private repository. Pages on a free account needs a public one. |

Publishing rules:

- A new repository is created only after you confirm its owner, name and visibility.
- An existing repository is updated only if it has the `.paper2page` marker this tool creates.
- Updates are ordinary commits; nothing is force-pushed.
- These names are rejected: `projects, publications, cv, people, course, thesis, demo, blog`.

Precedence: command-line arguments, then the per-paper yaml, then `config.yaml`.

## Per-paper settings

The first build writes a yaml next to the PDF (`GenKOL.yaml` for `GenKOL.pdf`). It is never
overwritten, so your edits are kept.

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

host_pdf: false        # true = compress the PDF and publish it

authors:               # left: as printed in the PDF; right: as shown on the page
  "Minh-Thu Do": "Đỗ Minh Thư"

metric_directions:     # which way is better; wildcards allowed
  "PSNR": higher
  "LPIPS": lower
```

`metric_directions` makes a results table show the best value in bold and the second best
underlined. The marks are computed from the numbers.

## Example: GenKOL

The paper is at `papers/GenKOL/GenKOL.pdf`.

**1. Build and review.** Nothing is published.

```
/build-page --paper papers/GenKOL/GenKOL.pdf
```

Look at `build/GenKOL/screenshots/desktop.png`, `mobile.png` and `build/GenKOL/report.md`.

**2. Add the links.** Either edit `papers/GenKOL/GenKOL.yaml`:

```yaml
links:
  arxiv: https://arxiv.org/abs/2509.14927
year: 2025
```

and apply it:

```
/build-page --paper papers/GenKOL/GenKOL.pdf --from content
```

or pass the link on the command line:

```
/build-page --paper papers/GenKOL/GenKOL.pdf --link arxiv=https://arxiv.org/abs/2509.14927
```

**3. Publish.**

```
/build-page --paper papers/GenKOL/GenKOL.pdf --publish --pages
```

The agent asks you to confirm the new repository, then prints the page URL
`https://<owner>.github.io/GenKOL/`.

Other variants:

```
/build-page --paper papers/GenKOL/GenKOL.pdf --host-pdf --publish --pages     # also publish the PDF
/build-page --paper papers/GenKOL/GenKOL.pdf --name genkol-page --publish     # another repository name
/build-page --paper papers/GenKOL/GenKOL.pdf --from render                    # after editing content.json
```

## Output folder

Everything goes to `./build/<name>/` (ignored by git):

```
build/GenKOL/
  extracted/       fig1.png ..., table1.png ..., extracted.json, contact_sheet.png
  content.json     page content; edit this, then --from render
  report.md        PASS / WARN / FAIL per check
  screenshots/     desktop.png, mobile.png, template_vs_output.png
  site/            the page that is published
```

Do not edit `site/index.html`: it is regenerated on every render.

## Fixing a bad crop

If a crop in `contact_sheet.png` cuts off a figure or includes caption text, ask the agent
("the crop of figure 3 is cut off, fix it") or set the box yourself. Boxes are in PDF points,
origin top-left.

```bash
P2P="$HOME/.claude/skills/build-page/.venv/bin/python $HOME/.claude/skills/build-page/scripts/p2p.py"

$P2P grid   --paper papers/GenKOL/GenKOL.pdf --page 4                              # page with a coordinate grid
$P2P recrop --paper papers/GenKOL/GenKOL.pdf --fig 2 --page 4 --bbox 140,46,480,270
$P2P recrop --paper papers/GenKOL/GenKOL.pdf --table 1 --page 6 --bbox 170,105,450,196
$P2P recrop --paper papers/GenKOL/GenKOL.pdf --fig 2 --reset                       # back to automatic
```

| Argument | Purpose |
|---|---|
| `--page P` | Page number, starting at 1. |
| `--fig N` / `--table N` | Which figure or table to recrop. |
| `--bbox x0,y0,x1,y1` | The new box, read off the grid image. |
| `--reset` | Remove the manual box. |

The box is saved under `crop_overrides` in the yaml, so later runs keep it. The page and box values
above are placeholders; read the real ones off the grid.

## Running without an agent

The scripts work on their own; you then fill the `TODO` fields in `content.json` yourself.

```bash
$P2P build --paper papers/GenKOL/GenKOL.pdf
# edit build/GenKOL/content.json
$P2P build --paper papers/GenKOL/GenKOL.pdf --from render
$P2P publish build/GenKOL --pages --yes
```

| Exit code | Meaning |
|---|---|
| 0 | Done; no check failed. |
| 1 | A check failed, including remaining `TODO` fields. |
| 2 | Usage or environment error. |
| 3 | `publish` only: the repository does not exist yet; run again with `--yes`. |

`build` also accepts `--reset-content`, which rewrites `content.json` from the PDF and backs up the
old one.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `/build-page` is not offered | Restart Claude Code. Check that `~/.claude/skills/build-page/SKILL.md` exists. |
| The PDF is not found | Paths are relative to the current folder; the folder is `papers/`, not `paper/`. |
| `Chromium could not be started` | Run `~/.claude/skills/build-page/.venv/bin/python -m playwright install chromium`. |
| `GitHub CLI is not logged in` | Run `gh auth login`. |
| `Project name ... collides` | Choose another name with `--name`. |
| `Repository ... has no .paper2page marker` | A repository with that name exists and was not made by this tool. Use another `--name`. |
| Authors or title are wrong | Edit `title` / `authors` in `content.json`, then `--from render`. |
| A table is shown as an image | Its numbers could not all be verified against the PDF text. |
| No bold or underline in a table | Set `metric_directions` in the yaml. |
| The page did not change online | GitHub Pages can take a minute; reload without cache. |
| Start over for one paper | Delete `build/<name>/` and build again. The yaml is kept. |
