"""Command line: build (extract -> content -> render -> check), grid, recrop, publish."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import check, content, extract, publish, site
from .common import STEPS, Build, P2PError, detect_owner, derive_name, load_config, paper_settings, \
    parse_link_flags, paper_yaml_path, save_crop_override, validate_name, write_paper_yaml

TODO_HINT = "Ask Claude Code to fill the TODO fields in {path} from the paper, then run with --from render"
MINERU_NOTE = ("--engine mineru is documented but not bundled: it needs a separate, heavy install "
               "(see README.md, section \"Optional: MinerU engine\"). Use the default engine.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="p2p.py", description="Build and publish a project page for one paper.")
    sub = p.add_subparsers(dest="command", required=True)

    def paper_args(sp):
        sp.add_argument("--paper", required=True, type=Path, help="paper PDF")
        sp.add_argument("--name", help="project name (default: from the yaml or the title)")

    b = sub.add_parser("build", help="extract -> content -> render -> check")
    paper_args(b)
    b.add_argument("--template", type=Path, help="template folder (default: template in the skill's config.yaml)")
    b.add_argument("--owner", help="GitHub account (default: config.yaml, then `gh api user`)")
    b.add_argument("--link", action="append", default=[], metavar="KIND=URL", help='link button; URL or "soon"')
    b.add_argument("--host-pdf", action="store_true", help="compress the PDF and publish it with the page")
    b.add_argument("--from", dest="start", choices=STEPS, default="extract", help="first step to run")
    b.add_argument("--reset-content", action="store_true", help="rewrite content.json from the PDF (old one is backed up)")
    b.add_argument("--engine", choices=["pymupdf", "mineru"], default="pymupdf", help="extraction engine")

    g = sub.add_parser("grid", help="render one PDF page with a coordinate grid (PDF points) to choose a bbox")
    paper_args(g)
    g.add_argument("--page", required=True, type=int, help="page number, 1-based")

    r = sub.add_parser("recrop", help="set a manual crop for one figure or table and save it in the yaml")
    paper_args(r)
    target = r.add_mutually_exclusive_group(required=True)
    target.add_argument("--fig", help="figure number, e.g. 5")
    target.add_argument("--table", help="table number, e.g. 3 or C1")
    r.add_argument("--page", type=int, help="page number, 1-based")
    r.add_argument("--bbox", help="x0,y0,x1,y1 in PDF points, origin top-left")
    r.add_argument("--reset", action="store_true", help="remove the manual crop and use automatic detection again")

    u = sub.add_parser("publish", help="push build/<name>/site to github.com/<owner>/<name>")
    u.add_argument("build_dir", type=Path, help="the build/<name> folder")
    u.add_argument("--owner", help="GitHub account")
    u.add_argument("--name", help="repository name (default: the build's name)")
    u.add_argument("--private", action="store_true", help="create a private repository")
    u.add_argument("--pages", action="store_true", help="enable GitHub Pages (main, root) and wait for it")
    u.add_argument("--yes", action="store_true", help="the user confirmed creating a new repository")
    return p


def _template_dir(flag: Path | None, config: dict) -> Path:
    path = flag or (Path(config["template"]).expanduser() if config["template"] else None)
    if path is None:
        raise P2PError("No template given. Pass --template <dir> or set `template:` in the skill's config.yaml.")
    if not (path / "index.html").is_file():
        raise P2PError(f"Template has no index.html: {path}")
    return path


def _open(args, link_flags: dict | None = None, host_pdf: bool = False) -> tuple[dict, str, Build]:
    """Settings, project name and build folder for --paper (same resolution for every subcommand)."""
    if not args.paper.is_file():
        raise P2PError(f"PDF not found: {args.paper}")
    settings = paper_settings(args.paper, link_flags or {}, host_pdf)
    name = validate_name(args.name or settings["name"] or derive_name(extract.read_header(args.paper)["title"]))
    return settings, name, Build(Path("build") / name)


def cmd_build(args) -> int:
    if args.engine == "mineru":
        raise P2PError(MINERU_NOTE)
    config = load_config()
    template_dir = _template_dir(args.template, config)
    links = parse_link_flags(args.link)
    settings, name, build = _open(args, links, args.host_pdf)
    owner = detect_owner(args.owner or "", config["owner"])
    page_url = f"https://{owner.lower()}.github.io/{name}/" if owner else ""
    home_url = page_url or "./"  # the home icon reloads this page
    start = STEPS.index(args.start)
    for step, needed in (("content", build.extracted_json), ("render", build.content_json), ("check", build.site / "index.html")):
        if start >= STEPS.index(step) and not needed.exists():
            raise P2PError(f"Cannot start at --from {args.start}: {needed} does not exist yet. Run without --from first.")

    print(f"Building {build.dir} from {args.paper}")
    if start <= 0:
        extracted = extract.run(args.paper, build, settings["crop_overrides"])
        if created := write_paper_yaml(args.paper, name, extracted.get("authors") or [], content.metric_labels(extracted)):
            print(f"  created {created} with the detected values; edit it to add links, venue, year, metric directions")
            settings = paper_settings(args.paper, links, args.host_pdf)
    if start <= 1:
        content.run(build, name, settings, reset=args.reset_content)
    info_path = build.dir / ".render.json"
    if start <= 2:
        info_path.write_text(json.dumps(site.run(build, args.paper, template_dir, settings, home_url, page_url)))
    info = json.loads(info_path.read_text()) if info_path.is_file() else {}
    checks = check.run(build, template_dir, info)

    print(f"\n  page:          {build.site / 'index.html'}")
    print(f"  contact sheet: {build.contact_sheet}")
    print(f"  screenshots:   {build.screenshots}/desktop.png, mobile.png")
    print(f"  report:        {build.report}")
    print(f"  settings:      {paper_yaml_path(args.paper)}")
    todos = content.find_todos(content.load(build))
    if todos:
        print(f"\n{len(todos)} TODO field(s): {', '.join(todos)}")
        print(TODO_HINT.format(path=build.content_json))
    return 1 if any(c.status == "FAIL" for c in checks) else 0


def cmd_grid(args) -> int:
    _, _, build = _open(args)
    extract.page_grid(args.paper, build, args.page)
    return 0


def cmd_recrop(args) -> int:
    _, _, build = _open(args)
    key = f"fig{args.fig}" if args.fig else f"table{args.table}"
    if args.reset:
        path = save_crop_override(args.paper, key, None, None)
        print(f"Removed the manual crop of {key} from {path}.")
    else:
        if args.page is None or not args.bbox:
            raise P2PError("recrop needs --page P and --bbox x0,y0,x1,y1 (or --reset).")
        try:
            bbox = [float(v) for v in args.bbox.split(",")]
        except ValueError:
            bbox = []
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise P2PError(f'Invalid --bbox "{args.bbox}". Expected x0,y0,x1,y1 in PDF points with x1 > x0 and y1 > y0.')
        path = save_crop_override(args.paper, key, args.page, bbox)
        print(f"Saved crop_overrides.{key} in {path}.")
    data = extract.run(args.paper, build, paper_settings(args.paper, {}, False)["crop_overrides"])
    item = next((i for i in data["figures"] + data["tables"] if i["id"] == key), None)
    if item and item.get("file"):
        print(f"New crop: {build.extracted / item['file']} ({item['width']}x{item['height']} px, {item['status']})")
    print(f"Contact sheet: {build.contact_sheet}\nLook at the new crop. When all crops are clean, run build again.")
    return 0


def cmd_publish(args) -> int:
    build = Build(args.build_dir)
    if not build.content_json.is_file():
        raise P2PError(f"Not a build folder: {args.build_dir} (expected content.json inside).")
    name = args.name or content.load(build)["name"]
    owner = detect_owner(args.owner or "", load_config()["owner"])
    return publish.publish(build, owner, name, args.private, args.pages, args.yes)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {"build": cmd_build, "grid": cmd_grid, "recrop": cmd_recrop, "publish": cmd_publish}[args.command](args)
