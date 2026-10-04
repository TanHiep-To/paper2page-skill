"""Step 2, content: extracted.json + paper settings -> content.json.

Everything that can be read from the PDF is filled in. What needs judgement (tagline, section
summaries, which figures illustrate what) is left as "TODO" for a coding agent to fill.

The page is an ordered list, content["blocks"], that the renderer follows:
  {"type": "teaser", "figure": "fig1", "tagline": "..."}
  {"type": "abstract"}
  {"type": "section", "title": "Method", "items": [
      {"type": "text", "paragraphs": ["..."]}, {"type": "figure", "id": "fig3"}, {"type": "table", "id": "table2"}]}
  {"type": "bibtex"}
Blocks may be left out and sections may have any number, order and titles. content["figures"] (caption,
alt) and content["tables"] (cells, display, interpretation) are libraries that the blocks refer to by id.
"""
from __future__ import annotations

import json
import math
import re

from .common import TODO, Build, P2PError, direction_for, is_todo, prompt_instructions

FULL_NUMBER = re.compile(r"^[-+−]?\d[\d,]*(?:\.\d+)?\s*%?(?:\s*±\s*\d[\d.]*)?$")
CAPTION_PREFIX = re.compile(r"^\s*(?:Fig\.|Figure|FIGURE|Fig|Table|TABLE)\s*(?:[A-Z]?\.?\d+|[IVXL]+)\s*[:.|]\s*")
BLOCK_TYPES = ("teaser", "abstract", "section", "bibtex")
ITEM_TYPES = ("text", "figure", "table")
STATUSES = ("applied", "partly", "not applied")


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“(])", text.strip()) if s]


def _paragraphs(abstract: str, max_sentences: int = 4) -> list[str]:
    sents = _sentences(abstract)
    if not sents:
        return []
    n = math.ceil(len(sents) / max_sentences)
    size = math.ceil(len(sents) / n)
    return [" ".join(sents[i:i + size]) for i in range(0, len(sents), size)]


def _meta_description(abstract: str, title: str) -> str:
    first = (_sentences(abstract) or [title])[0]
    return first if len(first) <= 160 else first[:157].rsplit(" ", 1)[0] + "..."


def _authors(extracted: dict, overrides: dict[str, str]):
    if not extracted.get("authors"):
        return TODO
    return [{"name": overrides.get(a["name"], a["name"]), "pdf_name": a["name"],
             "affiliations": a["affiliations"], "corresponding": a["corresponding"],
             "equal_contribution": a.get("equal_contribution", False)} for a in extracted["authors"]]


def _table(t: dict, directions: dict[str, str]) -> dict | None:
    """A content table from an extracted grid, or None when it is not a numeric results table."""
    grid = [r for r in t["grid"] if any(c.strip() for c in r)]
    if len(grid) < 3 or len(grid[0]) < 2:
        return None
    header, body = grid[0], grid[1:]
    roles = ["label"]
    for c in range(1, len(header)):
        numeric = sum(1 for r in body if FULL_NUMBER.match(r[c].strip()))
        roles.append("value" if numeric >= 0.6 * len(body) else "text")
    if "value" not in roles:
        return None
    col_dirs = {h: d for h, role in zip(header, roles) if role == "value" and (d := direction_for(h, directions))}
    row_dirs = {r[0]: d for r in body if (d := direction_for(r[0], directions))}
    metrics_in = "rows" if row_dirs and not col_dirs else "columns" if col_dirs else TODO
    return {
        "id": t["id"], "number": t["number"], "caption": t["caption"],
        # "html" only when every number of the grid was verified against the PDF text layer
        "display": "html" if t.get("html_verified") else "image",
        "metrics_in": metrics_in, "directions": row_dirs if metrics_in == "rows" else col_dirs,
        "columns": [{"label": h, "group": "", "role": role} for h, role in zip(header, roles)],
        "rows": [{"group": "", "cells": r} for r in body],
        "interpretation": TODO,
    }


def metric_labels(extracted: dict) -> list[tuple[int, str]]:
    """(table number, label) for every label that could be a metric in a numeric results table."""
    out = []
    for t in extracted["tables"]:
        if table := _table(t, {}):
            out += [(t["number"], c["label"]) for c in table["columns"] if c["role"] == "value"]
            out += [(t["number"], r["cells"][0]) for r in table["rows"]]
    return out


def bibtex(content: dict) -> str:
    authors = content["authors"] if isinstance(content["authors"], list) else []
    names = []
    for a in authors:
        parts = a["name"].split()
        names.append(f"{parts[-1]}, {' '.join(parts[:-1])}" if len(parts) > 1 else a["name"])
    first = re.sub(r"[^a-z]", "", authors[0]["name"].split()[-1].lower()) if authors else "paper"
    word = re.sub(r"[^a-z]", "", (content["title"].split() or ["paper"])[0].lower())
    kind = "article" if content["venue"] else "misc"
    fields = [("author", " and ".join(names)), ("title", content["title"])]
    if content["venue"]:
        fields.append(("journal", content["venue"]))
    if content["year"]:
        fields.append(("year", content["year"]))
    width = max(len(k) for k, _ in fields)
    body = "\n".join(f"  {k.ljust(width)} = {{{v}}}," for k, v in fields)
    return f"@{kind}{{{first}{content['year']}{word},\n{body}\n}}"


# ---------- blocks ----------

def default_blocks(first_figure: str, tables: list[dict]) -> list[dict]:
    """The template's default order: teaser, Abstract, Method, Quantitative Results, Qualitative Results, BibTeX."""
    blocks = [{"type": "teaser", "figure": first_figure, "tagline": TODO}, {"type": "abstract"},
              {"type": "section", "title": "Method",
               "items": [{"type": "text", "paragraphs": [TODO]}, {"type": "figure", "id": TODO}]}]
    if tables:
        blocks.append({"type": "section", "title": "Quantitative Results",
                       "items": [{"type": "table", "id": t["id"]} for t in tables]})
    blocks.append({"type": "section", "title": "Qualitative Results",
                   "items": [{"type": "text", "paragraphs": [TODO]}, {"type": "figure", "id": TODO}]})
    blocks.append({"type": "bibtex"})
    return blocks


def upgrade(content: dict) -> bool:
    """Turn a content.json written before blocks existed (method / tables / quantitative_figures /
    qualitative / tagline / overview_figure) into the same page as an ordered block list."""
    if "blocks" in content:
        return False
    blocks = [{"type": "teaser", "figure": content.pop("overview_figure", ""), "tagline": content.pop("tagline", "")},
              {"type": "abstract"}]

    def add(title: str, items: list[dict]) -> None:
        if items:
            blocks.append({"type": "section", "title": title, "items": items})

    def pairs(entries: list[dict]) -> list[dict]:
        items = []
        for q in entries:
            if q.get("description"):
                items.append({"type": "text", "paragraphs": [q["description"]]})
            if q.get("figure"):
                items.append({"type": "figure", "id": q["figure"]})
        return items

    method = content.pop("method", None) or {}
    add("Method", ([{"type": "text", "paragraphs": method["paragraphs"]}] if method.get("paragraphs") else [])
        + ([{"type": "figure", "id": method["figure"]}] if method.get("figure") else []))
    add("Quantitative Results", pairs([q for q in content.pop("quantitative_figures", None) or [] if q.get("figure")])
        + [{"type": "table", "id": t["id"]} for t in content.get("tables", [])])
    add("Qualitative Results", pairs(content.pop("qualitative", None) or []))
    blocks.append({"type": "bibtex"})
    content["blocks"] = blocks
    return True


def block(content: dict, kind: str) -> dict | None:
    return next((b for b in content.get("blocks", []) if isinstance(b, dict) and b.get("type") == kind), None)


def items(content: dict) -> list[dict]:
    """Every item of every section, in page order."""
    return [i for b in content.get("blocks", []) if isinstance(b, dict) and b.get("type") == "section"
            for i in b.get("items", []) if isinstance(i, dict)]


def used_figures(content: dict) -> list[str]:
    teaser = block(content, "teaser") or {}
    ids = [teaser.get("figure", "")] + [i.get("id", "") for i in items(content) if i.get("type") == "figure"]
    return [i for i in ids if i and not is_todo(i)]


def used_tables(content: dict) -> list[str]:
    return [i["id"] for i in items(content) if i.get("type") == "table" and i.get("id") and not is_todo(i["id"])]


def table_def(content: dict, table_id: str) -> dict | None:
    return next((t for t in content.get("tables", []) if t["id"] == table_id), None)


def shown_tables(content: dict) -> list[dict]:
    """Table definitions in page order; a table without a definition is shown as its image."""
    return [table_def(content, i) or {"id": i, "display": "image"} for i in used_tables(content)]


def validate(content: dict, extracted: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings) of the block list. TODO values are reported by find_todos, not here."""
    errors, warnings = [], []
    blocks = content.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return ['"blocks" must be a non-empty list'], []
    figs = {f["id"]: f for f in extracted["figures"]}
    tabs = {t["id"]: t for t in extracted["tables"]}

    def figure(fig_id, where: str) -> None:
        if not fig_id or is_todo(fig_id):
            return
        if fig_id not in figs:
            errors.append(f'{where}: figure "{fig_id}" was not extracted (available: {", ".join(figs)})')
        elif not figs[fig_id].get("file"):
            errors.append(f'{where}: figure "{fig_id}" has no crop yet; set one with recrop or use another figure')

    for n, b in enumerate(blocks):
        kind = b.get("type") if isinstance(b, dict) else None
        where = f"blocks[{n}]"
        if kind not in BLOCK_TYPES:
            errors.append(f'{where}: unknown block type "{kind}" (allowed: {", ".join(BLOCK_TYPES)})')
            continue
        if kind == "teaser":
            figure(b.get("figure"), where)
            if not b.get("figure") and not b.get("tagline"):
                warnings.append(f"{where}: the teaser has neither a figure nor a tagline; remove the block to omit it")
        if kind != "section":
            continue
        if not str(b.get("title") or "").strip():
            errors.append(f"{where}: a section needs a title")
        if not b.get("items"):
            warnings.append(f'{where}: section "{b.get("title")}" has no items and is not shown')
        for m, item in enumerate(b.get("items") or []):
            at = f'{where}.items[{m}] ("{b.get("title")}")'
            kind_i = item.get("type") if isinstance(item, dict) else None
            if kind_i not in ITEM_TYPES:
                errors.append(f'{at}: unknown item type "{kind_i}" (allowed: {", ".join(ITEM_TYPES)})')
            elif kind_i == "text" and not isinstance(item.get("paragraphs"), list):
                errors.append(f'{at}: a text item needs "paragraphs": [...]')
            elif kind_i == "figure":
                figure(item.get("id"), at)
            elif kind_i == "table" and item.get("id") and not is_todo(item["id"]):
                if item["id"] not in tabs:
                    errors.append(f'{at}: table "{item["id"]}" was not found in the PDF (available: {", ".join(tabs)})')
                elif not tabs[item["id"]].get("file") and (table_def(content, item["id"]) or {}).get("display") != "html":
                    errors.append(f'{at}: table "{item["id"]}" has no image crop; set one with recrop')
    for kind in ("teaser", "abstract", "bibtex"):
        if sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == kind) > 1:
            errors.append(f'more than one "{kind}" block')
    for ids, what in ((used_figures(content), "figure"), (used_tables(content), "table")):
        for dup in sorted({i for i in ids if ids.count(i) > 1}):
            warnings.append(f'{what} "{dup}" is shown more than once')
    return errors, warnings


def todos(content: dict) -> list[str]:
    """TODO fields that block the page. Tables in the library that no block shows do not count."""
    shown = set(used_tables(content))
    return find_todos({**content, "tables": [t for t in content.get("tables", []) if t["id"] in shown]})


def apply_prompt(content: dict, prompt: dict | None) -> bool:
    """Record a new or changed prompt: one entry per instruction for the agent to fill. The result of
    applying it lives in content.json, so later runs need no prompt."""
    if not prompt or content["_source"].get("prompt_sha1") == prompt["sha1"]:
        return False
    content["_source"]["prompt_sha1"] = prompt["sha1"]
    content["instructions_applied"] = [{"instruction": i, "status": TODO, "how": TODO}
                                       for i in prompt_instructions(prompt["text"])]
    content.setdefault("excluded", {"figures": [], "tables": [], "sections": []})
    return True


def build_fresh(extracted: dict, name: str, settings: dict) -> dict:
    figures = [{"id": f["id"], "caption": CAPTION_PREFIX.sub("", f["caption"]),
                "alt": f"Figure {f['number']}: " + (_sentences(CAPTION_PREFIX.sub('', f['caption'])) or ['figure'])[0]}
               for f in extracted["figures"]]
    tables = [t for t in (_table(t, settings["metric_directions"]) for t in extracted["tables"]) if t]
    content = {
        "_source": {"pdf_sha1": extracted["pdf_sha1"], "pdf_name": extracted.get("pdf_name", "")},
        "name": name,
        "title": extracted.get("title") or TODO,
        "authors": _authors(extracted, settings["authors"]),
        "affiliations": extracted.get("affiliations") or [],
        "venue": settings["venue"], "year": settings["year"],
        "keywords": extracted.get("keywords", []),
        "meta_description": _meta_description(extracted.get("abstract", ""), extracted.get("title", "")),
        "abstract_paragraphs": _paragraphs(extracted.get("abstract", "")) or [TODO],
        "blocks": default_blocks(next((f["id"] for f in extracted["figures"] if f.get("file")), ""), tables),
        "tables": tables,
        "figures": figures,
    }
    content["bibtex"] = bibtex(content)
    return content


def refresh(content: dict, name: str, settings: dict) -> dict:
    """Re-apply the settings-driven fields to an existing content.json; keep everything else as edited."""
    content["name"] = name
    content["venue"], content["year"] = settings["venue"], settings["year"]
    if isinstance(content["authors"], list):
        for a in content["authors"]:
            printed = a.get("pdf_name", a["name"])
            a["pdf_name"], a["name"] = printed, settings["authors"].get(printed, printed)
    for t in content["tables"]:
        if not t.get("columns"):
            continue  # shown as an image only
        labels = [c["label"] for c in t["columns"] if c["role"] == "value"] + [r["cells"][0] for r in t["rows"]]
        for label in labels:
            if d := direction_for(label, settings["metric_directions"]):
                t.setdefault("directions", {})[label] = d
        if is_todo(t.get("metrics_in")) and t.get("directions"):
            rows = {r["cells"][0] for r in t["rows"]}
            t["metrics_in"] = "rows" if set(t["directions"]) <= rows else "columns"
    content["bibtex"] = bibtex(content)
    return content


def find_todos(value, path: str = "") -> list[str]:
    if is_todo(value):
        return [path or "(root)"]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in find_todos(v, f"{path}.{k}" if path else k)]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in find_todos(v, f"{path}[{i}]")]
    return []


def load(build: Build) -> dict:
    try:
        content = json.loads(build.content_json.read_text())
    except json.JSONDecodeError as e:
        raise P2PError(f"{build.content_json} is not valid JSON: {e}") from e
    upgrade(content)
    return content


def run(build: Build, name: str, settings: dict, reset: bool = False, prompt: dict | None = None) -> dict:
    extracted = json.loads(build.extracted_json.read_text())
    existing = json.loads(build.content_json.read_text()) if build.content_json.is_file() else None
    if existing and not reset and existing.get("_source", {}).get("pdf_sha1") == extracted["pdf_sha1"]:
        if upgrade(existing):
            print("  content: content.json upgraded to the ordered block list (same page)")
        content = refresh(existing, name, settings)
        verified = {t["id"]: t.get("html_verified") for t in extracted["tables"]}
        for t in content["tables"]:
            t.setdefault("display", "html" if verified.get(t["id"]) and t.get("columns") else "image")
        print("  content: kept existing content.json (settings refreshed)")
    else:
        if existing:
            build.content_json.with_suffix(".json.bak").write_text(json.dumps(existing, ensure_ascii=False, indent=2))
            print("  content: previous content.json saved as content.json.bak")
        content = build_fresh(extracted, name, settings)
        print(f"  content: written from the PDF ({len(content['tables'])} results table(s))")
        if not prompt:
            build.prompt_used.unlink(missing_ok=True)
    if apply_prompt(content, prompt):
        print(f"  content: prompt recorded ({len(content['instructions_applied'])} instruction(s) to apply); "
              f"copy in {build.prompt_used}")
    build.content_json.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
    return content
