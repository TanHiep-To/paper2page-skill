# build-page

A skill for **Claude Code** and **Codex** that turns a paper PDF into a project page and publishes it
to GitHub Pages with one command.

```
/build-page --paper papers/CounterSketch/CounterSketch.pdf --publish --pages
```

Live examples:
[CounterSketch](https://tanhiep-to.github.io/CounterSketch/) ·
[Graphilosophy](https://tanhiep-to.github.io/Graphilosophy/)

## What it is for

You have a paper and want a clean project page without writing HTML.

| You give | You get |
|---|---|
| A paper PDF | Title, authors, affiliations, abstract and BibTeX read from the PDF |
| | Every figure and table cropped at 300 dpi with its printed caption |
| | Method, Quantitative Results and Qualitative Results sections |
| | A page that follows the bundled [Nerfies](https://github.com/nerfies/nerfies.github.io) template exactly |
| | A quality report (PASS / WARN / FAIL) with desktop and mobile screenshots |
| `--publish --pages` | A GitHub repository and a live page at `https://<owner>.github.io/<name>/` |

Extraction and rendering are plain Python (PyMuPDF, no ML models, no paid API). The agent reads the
paper, writes the few sentences that need judgement, and reviews the result. It never invents
numbers, links or claims.

## Requirements

| Needed | Why |
|---|---|
| macOS or Linux (Windows: WSL) | The installer is a bash script |
| Python 3.10+ and git | Run the tool, push pages |
| Claude Code or Codex | Runs the skill |
| GitHub CLI `gh`, logged in | Only for publishing |
| poppler (optional) | Keeps original embedded photos |

```bash
brew install python git gh poppler                              # macOS
sudo apt install python3 python3-venv git gh poppler-utils      # Ubuntu / Debian
gh auth login                                                   # once, if you plan to publish
```

## Install

```bash
git clone https://github.com/TanHiep-To/paper2page-skill.git
cd paper2page-skill
./install.sh
```

The installer links this folder into `~/.claude/skills/build-page` and
`~/.agents/skills/build-page`, creates `.venv`, installs the requirements and Chromium (about
500 MB), and creates `config.yaml`. Restart Claude Code afterwards so it picks up the skill.

## Quick start

1. Put the paper in `papers/`, one folder per paper (ignored by git, never pushed):

   ```
   papers/MyPaper/MyPaper.pdf
   ```

2. Build and review. Nothing is published:

   ```
   /build-page --paper papers/MyPaper/MyPaper.pdf
   ```

3. Edit `papers/MyPaper/MyPaper.yaml` (links, venue, year), then publish:

   ```
   /build-page --paper papers/MyPaper/MyPaper.pdf --publish --pages
   ```

In Codex, use `$build-page` with the same arguments.

## Documentation

| File | Content |
|---|---|
| [`INSTRUCTION.md`](INSTRUCTION.md) | Commands, every argument, the per-paper yaml, and a full example |
| [`SKILL.md`](SKILL.md) | The workflow the agent follows |

## Update and uninstall

```bash
cd paper2page-skill && git pull && ./install.sh                 # update
rm ~/.claude/skills/build-page ~/.agents/skills/build-page      # uninstall (removes the links)
```

## Credits and licence

The bundled template is the [Nerfies project page](https://github.com/nerfies/nerfies.github.io) by
Keunhong Park, licensed under [CC BY-SA 4.0](http://creativecommons.org/licenses/by-sa/4.0/). Built
pages keep the footer link back to that source. The template uses [Bulma](https://bulma.io) and
[Font Awesome Free](https://fontawesome.com).
