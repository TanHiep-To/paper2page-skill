"""Shared pieces: errors, build paths, name rules, and settings (flags > paper.yaml > config.yaml)."""
from __future__ import annotations

import fnmatch
import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

SKILL_DIR = Path(__file__).resolve().parents[2]
STEPS = ["extract", "content", "render", "check"]
RESERVED_NAMES = {"projects", "publications", "cv", "people", "course", "thesis", "demo", "blog"}
NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?$")
LINK_ORDER = ["code", "model", "dataset", "arxiv", "video"]
TODO = "TODO"


class P2PError(Exception):
    """A user-facing error: printed without a traceback."""


@dataclass(frozen=True)
class Build:
    """All generated files of one paper: <cwd>/build/<folder>/, named after its folder in papers/."""
    dir: Path

    @property
    def extracted(self) -> Path: return self.dir / "extracted"
    @property
    def extracted_json(self) -> Path: return self.extracted / "extracted.json"
    @property
    def contact_sheet(self) -> Path: return self.extracted / "contact_sheet.png"
    @property
    def content_json(self) -> Path: return self.dir / "content.json"
    @property
    def site(self) -> Path: return self.dir / "site"
    @property
    def prompt_used(self) -> Path: return self.dir / "prompt_used.md"  # copy of --prompt, for reference only
    @property
    def report(self) -> Path: return self.dir / "report.md"
    @property
    def report_json(self) -> Path: return self.dir / "report.json"
    @property
    def screenshots(self) -> Path: return self.dir / "screenshots"


def validate_name(name: str) -> str:
    if not NAME_RE.match(name):
        raise P2PError(f'Invalid project name "{name}". Use letters, digits, hyphens and underscores only.')
    if name.lower() in RESERVED_NAMES:
        raise P2PError(f'Project name "{name}" collides with a page on the main site. '
                       f'Reserved names: {", ".join(sorted(RESERVED_NAMES))}. Choose another with --name.')
    return name


def paper_folder(pdf: Path) -> str:
    """The folder in papers/ that holds the PDF (papers/<folder>/<file>.pdf). It names the build folder
    and is the default repository name; the paper's title is never used for either."""
    parent = pdf.resolve().parent
    if parent.parent.name != "papers":
        raise P2PError(f"{pdf} is not inside papers/<folder>/. Put the PDF in its own folder there, "
                       f"e.g. papers/MyPaper/{pdf.name}, and run again.")
    return parent.name


def resolve_prompt(value: str | None) -> dict | None:
    """--prompt: a .md file (convention: papers/<folder>/prompt.md) or inline instruction text.

    Returns {"text", "source", "sha1"}, or None without --prompt. Any other file type is rejected.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise P2PError("--prompt is empty. Give a .md file or the instruction text, or leave --prompt out.")
    one_token = not re.search(r"\s", value)
    if "\n" not in value and value.lower().endswith(".md"):
        path = Path(value).expanduser()
        if not path.is_file():
            raise P2PError(f"Prompt file not found: {value}. Create it (one instruction per \"- \" bullet), "
                           "or pass the instructions as inline text.")
        text, source = path.read_text().strip(), str(path)
        if not text:
            raise P2PError(f"Prompt file is empty: {value}")
    elif one_token and (Path(value).expanduser().is_file() or re.search(r"[\\/]|\.[A-Za-z0-9]{1,5}$", value)):
        raise P2PError(f'--prompt takes a .md file or inline text, not "{value}". '
                       "Save the instructions as papers/<folder>/prompt.md and pass that path.")
    else:
        text, source = value, "inline text"
    return {"text": text, "source": source, "sha1": hashlib.sha1(text.encode()).hexdigest()}


def prompt_instructions(text: str) -> list[str]:
    """The instructions of a prompt: its "- " bullets (with their continuation lines), else its lines."""
    bullets: list[str] = []
    for line in text.splitlines():
        if m := re.match(r"^\s*[-*]\s+(.*)$", line):
            bullets.append(m.group(1).strip())
        elif bullets and line.strip() and line[:1].isspace():
            bullets[-1] += " " + line.strip()
    if bullets:
        return [b for b in bullets if b]
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "<!--"))]


def is_todo(value) -> bool:
    return isinstance(value, str) and TODO in value


# ---------- yaml ----------

def read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise P2PError(f"{path} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise P2PError(f"{path} must contain a mapping of settings.")
    return data


def paper_yaml_path(pdf: Path) -> Path:
    """paper.yaml beside paper.pdf; <stem>.yaml beside any other PDF name."""
    return pdf.with_name("paper.yaml" if pdf.stem == "paper" else f"{pdf.stem}.yaml")


def parse_link_flags(items: list[str]) -> dict[str, str]:
    links = {}
    for item in items:
        kind, sep, url = item.partition("=")
        if not sep or not kind.strip():
            raise P2PError(f'Invalid --link "{item}". Expected KIND=URL or KIND=soon.')
        links[kind.strip().lower()] = url.strip()
    return links


def _clean_links(raw: dict, source: str) -> dict[str, str]:
    links = {}
    raw = {"code": "", **(raw or {})}
    for kind, url in raw.items():
        url = str(url or "").strip()
        if str(kind).lower() == "code" and not url:
            url = "soon"  # the Code button is always shown; "none" hides it
        if not url or url.lower() == "none":
            continue
        if url.lower() != "soon" and not re.match(r"^https?://", url):
            raise P2PError(f'{source}: link "{kind}" must be a URL, "soon", or empty (got "{url}").')
        links[str(kind).lower()] = "soon" if url.lower() == "soon" else url
    return dict(sorted(links.items(), key=lambda kv: LINK_ORDER.index(kv[0]) if kv[0] in LINK_ORDER else 99))


def load_config() -> dict:
    """Optional <skill>/config.yaml: owner, template."""
    data = read_yaml(SKILL_DIR / "config.yaml")
    return {k: str(data.get(k) or "").strip() for k in ("owner", "template")}


def paper_settings(pdf: Path, link_flags: dict[str, str], host_pdf_flag: bool) -> dict:
    """paper.yaml values with command-line overrides applied."""
    path = paper_yaml_path(pdf)
    raw = read_yaml(path)
    directions = {}
    for label, value in (raw.get("metric_directions") or {}).items():
        value = str(value).strip().lower()
        if value not in ("higher", "lower"):
            raise P2PError(f'{path}: metric_directions["{label}"] must be "higher" or "lower".')
        directions[str(label)] = value
    links = _clean_links({**(raw.get("links") or {}), **link_flags}, str(path))
    return {
        "name": str(raw.get("name") or "").strip(), "links": links,
        "venue": str(raw.get("venue") or "").strip(), "year": str(raw.get("year") or "").strip(),
        "authors": {str(k): str(v) for k, v in (raw.get("authors") or {}).items() if v},
        "metric_directions": directions,
        "host_pdf": bool(host_pdf_flag or raw.get("host_pdf", False)),
        "crop_overrides": _crop_overrides(raw.get("crop_overrides"), path),
    }


def _crop_overrides(raw, path: Path) -> dict:
    out = {}
    for key, value in (raw or {}).items():
        ok = isinstance(value, dict) and isinstance(value.get("page"), int) and isinstance(value.get("bbox"), list) \
            and len(value["bbox"]) == 4 and re.fullmatch(r"(fig|table)[A-Za-z]*\d+", str(key))
        if not ok:
            raise P2PError(f'{path}: crop_overrides.{key} must look like  fig3: {{page: 5, bbox: [x0, y0, x1, y1]}}')
        out[str(key)] = {"page": value["page"], "bbox": [float(v) for v in value["bbox"]]}
    return out


def save_crop_override(pdf: Path, key: str, page: int | None, bbox: list[float] | None) -> Path:
    """Add, replace or (with page=None) remove one entry of crop_overrides in the yaml, keeping its comments."""
    path = paper_yaml_path(pdf)
    if not path.is_file():
        raise P2PError(f"{path} does not exist yet. Run a build first.")
    current = _crop_overrides(read_yaml(path).get("crop_overrides"), path)
    if page is None:
        current.pop(key, None)
    else:
        current[key] = {"page": page, "bbox": [round(float(v), 1) for v in bbox]}
    text = re.sub(r"(?ms)^# Manual crops.*?(?=^crop_overrides:)", "", path.read_text())
    text = re.sub(r"(?ms)^crop_overrides:.*?(?=^\S|\Z)", "", text).rstrip() + "\n"
    if current:
        text += "\n# Manual crops in PDF points (origin top-left), set with `p2p.py recrop`. Reruns keep them.\ncrop_overrides:\n"
        text += "".join(f"  {k}: {{page: {v['page']}, bbox: {v['bbox']}}}\n" for k, v in sorted(current.items()))
    path.write_text(text)
    return path


def write_paper_yaml(pdf: Path, name: str, authors: list[dict], metrics: list[tuple[int, str]]) -> Path | None:
    """First run: create the yaml beside the PDF, pre-filled with what was detected. Never overwrites."""
    path = paper_yaml_path(pdf)
    if path.exists():
        return None
    q = lambda s: '"' + s.replace('"', '\\"') + '"'  # noqa: E731
    author_lines = "\n".join(f"  {q(a['name'])}: {q(a['name'])}" for a in authors)
    metric_lines = "\n".join(f"  # {q(label)}: higher    # Table {n}" for n, label in metrics)
    path.write_text(f"""\
# Settings for the project page of {pdf.name}. Generated on the first run with what was detected
# in the PDF; edit and rebuild. Command-line flags override these values.

name: {name}    # repo name = page path (letters, digits, hyphens, underscores); the build folder is always build/<papers folder>

links:          # a URL, "soon" (disabled "coming soon" button), or empty (no button)
  code:         # empty = "Code (coming soon)"; write none to hide the button
  model:
  dataset:
  arxiv:
  video:

venue:          # e.g. "AI & Society"; empty if not published yet
year:

host_pdf: false # true = compress the PDF and publish it; false = "Paper (coming soon)"

# Display names. Left: as printed in the PDF (do not change). Right: as shown on the page,
# e.g. with diacritics.
authors:
{author_lines or "  # (authors were not detected)"}

# Which way is better, per metric label (wildcards such as "P@*" are allowed). Needed for
# bold / underline in the results table. Uncomment the lines that are metrics and set them to
# higher or lower. Labels detected in the numeric tables:
metric_directions:
{metric_lines or "  # (no numeric results table detected)"}
""")
    return path


def direction_for(label: str, directions: dict[str, str]) -> str | None:
    """'higher' | 'lower' for a metric label: arrows in the label first, then exact or wildcard match."""
    if "↑" in label:
        return "higher"
    if "↓" in label:
        return "lower"
    clean = re.sub(r"[↑↓]", "", label).strip().lower()
    for pattern, value in directions.items():
        p = pattern.strip().lower()
        if clean == p or fnmatch.fnmatchcase(clean, p):
            return value
    return None


def detect_owner(*candidates: str) -> str:
    for c in candidates:
        if c:
            return c
    if shutil.which("gh"):
        r = subprocess.run(["gh", "api", "user", "--jq", ".login"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return ""
