"""Command line: build (extract -> content -> render -> check) and publish."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import check, content, extract, publish, site
from .common import STEPS, Build, P2PError, detect_owner, derive_name, load_config, paper_settings, \
    parse_link_flags, paper_yaml_path, validate_name, write_paper_yaml

TODO_HINT = "Ask Claude Code to fill the TODO fields in {path} from the paper, then run with --from render"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="paper2page", description="Build and publish a project page for one paper.")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="extract -> content -> render -> check")
    b.add_argument("--paper", required=True, type=Path, help="paper PDF")
    b.add_argument("--template", type=Path, help="template folder (default: template in the skill's config.yaml)")
    b.add_argument("--name", help="project name = repo name = page path (default: from the yaml or the title)")
    b.add_argument("--owner", help="GitHub account (default: config.yaml, then `gh api user`)")
    b.add_argument("--link", action="append", default=[], metavar="KIND=URL", help='link button; URL or "soon"')
    b.add_argument("--host-pdf", action="store_true", help="compress the PDF and publish it with the page")
    b.add_argument("--from", dest="start", choices=STEPS, default="extract", help="first step to run")
    b.add_argument("--reset-content", action="store_true", help="rewrite content.json from the PDF (old one is backed up)")

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


def cmd_build(args) -> int:
    if not args.paper.is_file():
        raise P2PError(f"PDF not found: {args.paper}")
    config = load_config()
    template_dir = _template_dir(args.template, config)
    settings = paper_settings(args.paper, parse_link_flags(args.link), args.host_pdf)
    name = validate_name(args.name or settings["name"] or derive_name(extract.read_header(args.paper)["title"]))
    build = Build(Path("build") / name)
    owner = detect_owner(args.owner or "", config["owner"])
    home_url = config["home_url"] or (f"https://{owner.lower()}.github.io/" if owner else "")
    page_url = f"https://{owner.lower()}.github.io/{name}/" if owner else ""
    start = STEPS.index(args.start)
    for step, needed in (("content", build.extracted_json), ("render", build.content_json), ("check", build.site / "index.html")):
        if start >= STEPS.index(step) and not needed.exists():
            raise P2PError(f"Cannot start at --from {args.start}: {needed} does not exist yet. Run without --from first.")

    print(f"Building {build.dir} from {args.paper}")
    if start <= 0:
        extracted = extract.run(args.paper, build)
        if created := write_paper_yaml(args.paper, name, extracted.get("authors") or [], content.metric_labels(extracted)):
            print(f"  created {created} with the detected values; edit it to add links, venue, year, metric directions")
            settings = paper_settings(args.paper, parse_link_flags(args.link), args.host_pdf)
    if start <= 1:
        content.run(build, name, settings, reset=args.reset_content)
    info_path = build.dir / ".render.json"
    if start <= 2:
        info = site.run(build, args.paper, template_dir, settings, home_url, page_url)
        info_path.write_text(json.dumps(info))
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


def cmd_publish(args) -> int:
    build = Build(args.build_dir)
    if not build.content_json.is_file():
        raise P2PError(f"Not a build folder: {args.build_dir} (expected content.json inside).")
    name = args.name or content.load(build)["name"]
    owner = detect_owner(args.owner or "", load_config()["owner"])
    return publish.publish(build, owner, name, args.private, args.pages, args.yes)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return cmd_build(args) if args.command == "build" else cmd_publish(args)
