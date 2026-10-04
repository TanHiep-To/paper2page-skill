# Instructions

How to use `/build-page`: the command, its arguments, the settings file, and a full example.
For installation see [`README.md`](README.md).

- [The command](#the-command)
- [Arguments](#arguments)
- [Where the paper goes](#where-the-paper-goes)
- [Per-paper settings](#per-paper-settings)
- [Prompt: what to extract and how to lay out the page](#prompt-what-to-extract-and-how-to-lay-out-the-page)
- [Appendix](#appendix)
- [Checking that a prompt took effect](#checking-that-a-prompt-took-effect)
- [Example: GenKOL](#example-genkol)
- [Using the agent](#using-the-agent)
- [Output folder](#output-folder)
- [Fixing a bad crop](#fixing-a-bad-crop)
- [Running without an agent](#running-without-an-agent)
- [Troubleshooting](#troubleshooting)

## The command

```
/build-page --paper <pdf> [--prompt <file.md | "text">] [--template <dir>] [--name X] [--owner Y]
            [--link kind=url ...] [--host-pdf] [--from extract|content|render|check]
            [--publish] [--pages] [--private]
```

One command runs four steps and then, if asked, publishes:

| Step | What happens | Writes |
|---|---|---|
| `extract` | Finds figures, tables, title, authors and abstract in the PDF | `extracted/` |
| `content` | Fills the page content; the agent writes tagline and summaries and applies `--prompt` | `content.json` |
| `render` | Builds the page from the template | `site/` |
| `check` | Runs the quality checks and takes screenshots | `report.md`, `screenshots/` |

Paths are relative to the folder where you run the command. In Codex, use `$build-page`.

## Arguments

### Build

| Argument | Purpose | Default |
|---|---|---|
| `--paper <pdf>` | The paper to build from, inside `papers/<folder>/`. **Required.** | |
| `--prompt <file.md \| "text">` | Optional instructions: what to take from the paper and how to lay out the page. A `.md` file or inline text. See [Prompt](#prompt-what-to-extract-and-how-to-lay-out-the-page). | None: the template's default page |
| `--template <dir>` | Use another page template (a folder with `index.html` and `static/`). | `template` in `config.yaml`, else the bundled `template/` |
| `--name X` | Repository name and page path. Letters, digits, hyphens, underscores. It never changes the build folder. | `name` in the yaml, else the paper's folder name in `papers/` |
| `--owner Y` | GitHub account that owns the page. | `owner` in `config.yaml`, else the `gh` login |
| `--link kind=url` | Add a link button. Repeatable. Kinds: `code`, `model`, `dataset`, `arxiv`, `video`. Use `kind=soon` for a disabled "coming soon" button, `kind=none` to hide it. | Paper and Code read "(coming soon)" |
| `--host-pdf` | Compress the PDF and publish it behind the Paper button. | Off |
| `--from <step>` | Start at `extract`, `content`, `render` or `check` and reuse earlier outputs. | `extract` |

Which `--from` to use:

| You changed | Use |
|---|---|
| The yaml, or a crop | `--from content` |
| Only `content.json` | `--from render` |
| The prompt | pass `--prompt` again; the run starts at the content step |

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

## Where the paper goes

Put each paper in its own folder inside `papers/`:

```
papers/VG_Cap/paper.pdf      ->  build/VG_Cap/
papers/GenKOL/GenKOL.pdf     ->  build/GenKOL/
```

- The build folder is always `build/<folder>`, named after the folder in `papers/`. It is never taken
  from the paper's title.
- The repository name defaults to the same folder name. `--name` or `name:` in the yaml change only
  the repository name and page path (`https://<owner>.github.io/<name>/`), not the build folder.
- A PDF that is not inside `papers/<folder>/` is rejected: move it there and run again.

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

## Prompt: what to extract and how to lay out the page

`--prompt` is optional. Without it the page follows the template: teaser, Abstract, Method,
Quantitative Results, Qualitative Results, BibTeX.

```
/build-page --paper papers/CPAM/paper.pdf --prompt papers/CPAM/prompt.md
/build-page --paper papers/VG_Cap/paper.pdf --prompt "No teaser: start with the abstract."
```

| Value | Result |
|---|---|
| a path ending in `.md` | The file is read. A missing file is an error. |
| a file with another extension (`.txt`, `.docx`, ...) | Rejected: save the instructions as `.md`. |
| anything else | Used as inline instruction text. |

Convention: keep the prompt in `papers/<folder>/prompt.md`, one instruction per `- ` bullet. Prompt
files are never created for you.

The prompt is free text. It can say:

- **what to take from the paper**: skip sections, only an overview of the method, only the main
  results against the state of the art, use figures from the appendix;
- **how to lay out the page**: no teaser, the order of the sections, extra or removed sections,
  section titles.

Example, `papers/CPAM/prompt.md` (a long journal paper):

```markdown
- Skip PRELIMINARY ANALYSIS, ablation study and user study.
- Only the overview of the proposed method.
- Show a few main results compared with SOTA.
- Add visualizations from the appendix (the PDF is main paper + appendix merged).
```

Example, `papers/VG_Cap/prompt.md` (a paper with no teaser figure):

```markdown
- No teaser: this paper has no teaser figure, so start with the abstract.
- Put Fig. 1 (the pipeline overview) in the Method section.
- Results: show only Table 1 (comparison with existing methods); skip the ablation study.
- After the results add a section "Entity Selection Analysis" with Fig. 2, then a section
  "Qualitative Comparison" with Fig. 4.
```

How it works:

- The agent applies the prompt in the content step and writes the result to `content.json`: an
  ordered list of `blocks` (teaser, abstract, any number of sections, bibtex) that the renderer
  follows. A block that is left out is not on the page.
- Whatever the prompt skips does not appear on the page: no text, figure or table from it. A skipped
  figure or table on the page is a FAIL. A number that the PDF prints only inside a skipped section
  is a WARN, so you can check that sentence.
- The style does not change. Every block is a clone of the template's own blocks; no CSS, inline
  style or class is added, and the template fidelity and typography checks still have to pass.
- The result lives in `content.json`, so `--from render` needs no prompt. If you pass `--prompt`
  together with `--from render` or `--from check`, the run starts at the content step and says so.
- A copy of the prompt is saved as `build/<folder>/prompt_used.md` (reference only).
- `report.md` gets a section **Instructions applied**: each instruction, whether it was applied,
  partly applied or not applied, and how or why.

Example, `papers/DDPM/prompt.md` (main paper and appendix in one PDF,
[arXiv 2006.11239](https://arxiv.org/abs/2006.11239)):

```markdown
- Only the overview of the proposed method.
- Show the main quantitative results (FID/IS comparison).
- Skip the ablations and the related work.
- Add visualizations from the appendix: 2-3 sample figures.
```

Tips for a prompt that works:

- One instruction per bullet, each about one thing.
- Name sections by their heading in the paper ("skip Section 4.2", "skip the user study") and
  figures or tables by their number when you care which one is used.
- Say how much you want: "only an overview", "2-3 figures", "only Table 1".
- Layout instructions are about blocks and order ("no teaser", "Results before Method", "add a
  section Dataset"). The look of the page cannot be changed.

## Appendix

A PDF that holds the main paper followed by its appendix is handled as one document.

- Appendix captions keep their own numbering: `Figure A1`, `Fig. S3`, `Figure B.2`, `Table B2`
  (ids `figA1`, `figS3`, `figB2`, `tableB2`).
- IEEE-style captions are read too: `TABLE II` with the caption on the next line is `table2`.
- The first appendix page is detected from the PDF outline or from the first appendix heading after
  the references ("Appendix", "A ..."). Every figure and table in `extracted.json` is tagged
  `"part": "main"` or `"part": "appendix"`, also when the appendix continues the main numbering
  (Figure 9, 10, ...). The report's Extraction list shows the tag.
- "From the appendix" in a prompt means figures tagged `appendix`. In the DDPM example the page
  shows Figures 11, 13 and 16 (PDF pages 17, 19 and 22); the appendix starts on page 13.
- If a prompt asks for appendix figures and the PDF has none, the instruction is reported as not
  applied; nothing else is put in their place.
- If the appendix restarts at `Figure 1`, that figure becomes `figApp1` and keeps its printed label.
- Appendix figures are used like any other. Ask for them in the prompt: "use the qualitative figures
  from the appendix".
- If your appendix is a separate file, merge it after the main paper into one PDF first.

## Checking that a prompt took effect

**In the report.** `build/<folder>/report.md` has the section "Instructions applied":

```
| # | Instruction | Status | How it was applied, or why not |
| 1 | No teaser: start with the abstract. | applied | No teaser block; the page goes from the title area to the Abstract. |
| 4 | Add visualizations from the appendix. | not applied | The PDF has no appendix. |

Left out of the page:
- sections: Ablation Study
- tables: table2
```

and two checks: **Layout** (the page order, taken from `blocks`) and **Instructions** (FAIL when an
excluded figure or table is on the page or an instruction has no answer; WARN for "partly" and
"not applied", and for a number on the page that the PDF prints only inside a skipped section).

**On the page.** Open `screenshots/desktop.png` and go down the instructions one by one: is the
teaser gone, are the sections in the order you asked for, is the skipped table really absent.

**With the case tests.** `tests/cases/<folder>.json` says, for one paper, which prompt it was built
with and what the page must then contain:

```json
{
  "folder": "VG_Cap",
  "paper": "papers/VG_Cap/paper.pdf",
  "prompt": ["No teaser: this paper has no teaser figure, so start with the abstract.", "..."],
  "expect": {
    "teaser": false,
    "bibtex": true,
    "sections": ["Abstract", "Method", "Quantitative Results", "Entity Selection Analysis", "Qualitative Comparison"],
    "figures": ["fig1", "fig2", "fig4"],
    "section_figures": {"Method": ["fig1"]},
    "tables": ["table1"],
    "not_on_page": ["table2", "fig3"],
    "absent_text": ["ablation"],
    "statuses": ["applied", "applied", "applied", "applied"]
  }
}
```

Run them from the folder that holds `papers/` and `build/`, after building the papers:

```bash
.venv/bin/python -m unittest tests.test_cases -v     # built pages against their prompts
.venv/bin/python -m unittest discover tests          # these plus the unit tests
```

For every built case the tests check that:

| Test | Checks |
|---|---|
| build has no FAIL and no TODO | the report, and that the build folder is the folder in `papers/` |
| page order is the block order | section titles, teaser and BibTeX on the page match `blocks` and the expectation |
| figures and tables on the page | exactly the expected ones, in order and in the expected section; none of `not_on_page` |
| without a prompt the page is the default | default sections, no `prompt_used.md`, no "Instructions applied" |
| prompt is recorded and answered | `prompt_used.md` and `instructions_applied` hold every instruction, with the expected status |
| prompt changed the page | the layout is not the template's default |
| skipped parts are not on the page | no excluded figure or table, none of the `absent_text` words, no number only from a skipped section |
| overview only means a short section | at most the given number of paragraphs and one figure |
| appendix figures come from the appendix | they are tagged `appendix` and lie after the appendix start page |

A case whose `build/<folder>` does not exist is skipped. The shipped cases are GenKOL,
Graphilosophy and CounterSketch (no prompt), VG_Cap, CogCanvas and CPAM (with a prompt), and DDPM
(appendix figures; get the PDF with
`curl -L -o papers/DDPM/paper.pdf https://arxiv.org/pdf/2006.11239`). To add your own paper, copy
one of the files, set the prompt and what you expect, build the page, and run the tests.

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
/build-page --paper papers/GenKOL/GenKOL.pdf --prompt papers/GenKOL/prompt.md # your own selection and layout
/build-page --paper papers/GenKOL/GenKOL.pdf --prompt "No BibTeX yet. Put the user study before the method."
/build-page --paper papers/GenKOL/GenKOL.pdf --host-pdf --publish --pages     # also publish the PDF
/build-page --paper papers/GenKOL/GenKOL.pdf --name genkol-page --publish     # another repository name
/build-page --paper papers/GenKOL/GenKOL.pdf --from render                    # after editing content.json
```

## Using the agent

`/build-page` is run by the coding agent. The scripts do the deterministic work; the agent does what
needs reading:

1. reviews every crop and fixes bad ones;
2. reads the whole paper and, if you passed `--prompt`, each instruction of it;
3. fills `content.json`: the ordered `blocks`, the summaries, the alt texts, and with a prompt also
   `excluded` (what was left out) and `instructions_applied` (what it did for each instruction);
4. renders, reads `report.md`, looks at the desktop and mobile screenshots, and fixes what fails;
5. reports to you, including the section "Instructions applied".

After a build you can keep steering in words in the same session, for example "move Results before
Method", "drop the user study figure" or "the crop of figure 3 is cut off". The agent edits
`content.json` (or the crop) and renders again. An instruction that cannot be met with the template's
own blocks, such as a new visual style, is reported as not applied instead of being forced.

## Output folder

Everything goes to `./build/<folder>/`, named after the paper's folder in `papers/` (ignored by git):

```
build/GenKOL/
  extracted/       fig1.png ..., table1.png ..., extracted.json, contact_sheet.png
  content.json     page content and block order; edit this, then --from render
  prompt_used.md   copy of the --prompt that was used (only with --prompt)
  report.md        PASS / WARN / FAIL per check, "Instructions applied"
  screenshots/     desktop.png, mobile.png, template_vs_output.png
  site/            the page that is published
```

Do not edit `site/index.html`: it is regenerated on every render. Images above 500 KB are
compressed for the page automatically. The arXiv stamp in the margin of page 1 is removed from the
extracted text.

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
| `--fig N` / `--table N` | Which figure or table to recrop. Appendix labels work too (`--fig A1`); `TABLE II` is `--table 2`. |
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
| `... is not inside papers/<folder>/` | Move the PDF into its own folder in `papers/`, e.g. `papers/MyPaper/paper.pdf`. |
| `--prompt takes a .md file or inline text` | Save the instructions as `papers/<folder>/prompt.md`, or pass them as quoted text. |
| `Prompt file not found` | Check the path of the `.md` file; prompt files are not created automatically. |
| `the block list cannot be rendered` | `content.json` refers to a figure or table that does not exist, or has an unknown block type; the message lists each problem. |
| `Chromium could not be started` | Run `~/.claude/skills/build-page/.venv/bin/python -m playwright install chromium`. |
| `GitHub CLI is not logged in` | Run `gh auth login`. |
| `Project name ... collides` | Choose another name with `--name`. |
| `Repository ... has no .paper2page marker` | A repository with that name exists and was not made by this tool. Use another `--name`. |
| Authors or title are wrong | Edit `title` / `authors` in `content.json`, then `--from render`. |
| A table is shown as an image | Its numbers could not all be verified against the PDF text. |
| An instruction of the prompt was not followed | Read "Instructions applied" in `report.md`; reword the instruction (name the section, figure or table) and run with `--prompt` again. |
| `WARN: numbers ... only inside a skipped section` | A sentence on the page may come from a part you skipped. Check it, or ask the agent to remove it. |
| Two tables side by side are cropped wrongly | Set each box with `recrop --table N` (see [Fixing a bad crop](#fixing-a-bad-crop)). |
| No bold or underline in a table | Set `metric_directions` in the yaml. |
| The page did not change online | GitHub Pages can take a minute; reload without cache. |
| Start over for one paper | Delete `build/<folder>/` and build again. The yaml is kept. |
