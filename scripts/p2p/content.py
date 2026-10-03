"""Step 2, content: extracted.json + paper settings -> content.json.

Everything that can be read from the PDF is filled in. What needs judgement (tagline, section
summaries, which figures illustrate what) is left as "TODO" for a coding agent to fill.
"""
from __future__ import annotations

import json
import math
import re

from .common import TODO, Build, direction_for, is_todo

FULL_NUMBER = re.compile(r"^[-+−]?\d[\d,]*(?:\.\d+)?\s*%?(?:\s*±\s*\d[\d.]*)?$")
CAPTION_PREFIX = re.compile(r"^\s*(?:Fig\.|Figure|FIGURE|Fig|Table|TABLE)\s*\d+\s*[:.|]\s*")
AGENT_FIELDS = ("tagline", "method", "qualitative", "overview_figure")


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
             "affiliations": a["affiliations"], "corresponding": a["corresponding"]} for a in extracted["authors"]]


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
        "tagline": TODO,
        "abstract_paragraphs": _paragraphs(extracted.get("abstract", "")) or [TODO],
        "overview_figure": figures[0]["id"] if figures else "",
        "method": {"figure": TODO, "paragraphs": [TODO]},
        "tables": tables,
        "qualitative": [{"figure": TODO, "description": TODO}],
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
    return json.loads(build.content_json.read_text())


def run(build: Build, name: str, settings: dict, reset: bool = False) -> dict:
    extracted = json.loads(build.extracted_json.read_text())
    existing = load(build) if build.content_json.is_file() else None
    if existing and not reset and existing.get("_source", {}).get("pdf_sha1") == extracted["pdf_sha1"]:
        content = refresh(existing, name, settings)
        print("  content: kept existing content.json (settings refreshed)")
    else:
        if existing:
            build.content_json.with_suffix(".json.bak").write_text(json.dumps(existing, ensure_ascii=False, indent=2))
            print("  content: previous content.json saved as content.json.bak")
        content = build_fresh(extracted, name, settings)
        print(f"  content: written from the PDF ({len(content['tables'])} results table(s))")
    build.content_json.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n")
    return content
