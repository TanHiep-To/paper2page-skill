"""Publish site/ to its own GitHub repository with the GitHub CLI. Never force-pushes, never stores tokens."""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import time
from pathlib import Path

from .common import Build, P2PError, validate_name
from .site import MARKER

CONFIRM_NEEDED = 3  # exit code: the repo does not exist yet and --yes was not given


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise P2PError(f"`{' '.join(cmd)}` failed:\n{(r.stderr or r.stdout).strip()}")
    return r


def check_gh() -> None:
    if not shutil.which("git"):
        raise P2PError("git is not installed.")
    if not shutil.which("gh"):
        raise P2PError("GitHub CLI (gh) is not installed. Install it from https://cli.github.com and run `gh auth login`.")
    if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        raise P2PError("GitHub CLI is not logged in. Run `gh auth login`, then publish again.")


def check_ready(build: Build) -> dict:
    """Refuse to publish an unbuilt page, a page with TODOs, or a page with failed checks."""
    if not (build.site / "index.html").is_file() or not build.report_json.is_file():
        raise P2PError(f"Nothing to publish in {build.dir}. Run the build first.")
    report = json.loads(build.report_json.read_text())
    if report["todos"]:
        raise P2PError("Refusing to publish: TODO fields remain in content.json:\n  - " + "\n  - ".join(report["todos"])
                       + f"\nAsk Claude Code to fill the TODO fields in {build.content_json} from the paper, "
                         "then run with --from render")
    failed = [c["name"] for c in report["checks"] if c["status"] == "FAIL"]
    if failed:
        raise P2PError(f"Refusing to publish: failed checks in {build.report}: {', '.join(failed)}")
    return report


def _repo_exists(slug: str, cwd: Path) -> bool:
    return _run(["gh", "repo", "view", slug, "--json", "name"], cwd, check=False).returncode == 0


def _has_marker(slug: str, cwd: Path) -> bool:
    return _run(["gh", "api", f"repos/{slug}/contents/{MARKER}", "--silent"], cwd, check=False).returncode == 0


def _commit(site: Path, message: str) -> bool:
    _run(["git", "add", "-A"], site)
    if _run(["git", "diff", "--cached", "--quiet"], site, check=False).returncode == 0:
        return False
    _run(["git", "commit", "-m", message], site)
    return True


def _create(site: Path, slug: str, private: bool, title: str) -> None:
    if not (site / ".git").exists():
        _run(["git", "init", "-b", "main"], site)
    _commit(site, "Add project page")
    _run(["git", "branch", "-M", "main"], site)
    _run(["gh", "repo", "create", slug, "--private" if private else "--public", "--source", ".", "--push",
          "--description", f"Project page: {title}"[:340]], site)


def _update(site: Path, slug: str) -> bool:
    """Adopt the remote history, commit the new site on top, push normally."""
    if not (site / ".git").exists():
        _run(["git", "init", "-b", "main"], site)
    url = f"https://github.com/{slug}.git"
    if _run(["git", "remote", "get-url", "origin"], site, check=False).returncode != 0:
        _run(["git", "remote", "add", "origin", url], site)
    _run(["git", "fetch", "origin", "main"], site)
    _run(["git", "reset", "origin/main"], site)  # mixed reset: history from the remote, files from this build
    changed = _commit(site, f"Update project page ({datetime.date.today().isoformat()})")
    _run(["git", "branch", "-M", "main"], site)
    if changed:
        _run(["git", "push", "-u", "origin", "main"], site)
    return changed


def _enable_pages(slug: str, cwd: Path) -> str:
    """Enable Pages on main / (root) and wait up to ~2 minutes. Returns a status line."""
    r = _run(["gh", "api", "-X", "POST", f"repos/{slug}/pages", "-f", "source[branch]=main", "-f", "source[path]=/"],
             cwd, check=False)
    if r.returncode != 0 and "409" not in r.stderr and "already" not in r.stderr.lower():
        return f"could not be enabled: {(r.stderr or r.stdout).strip().splitlines()[0]}"
    status = "queued"
    for _ in range(12):
        s = _run(["gh", "api", f"repos/{slug}/pages", "--jq", ".status"], cwd, check=False)
        status = s.stdout.strip() or status
        if status == "built":
            return "live"
        if status == "errored":
            return "build errored (see the repository's Pages settings)"
        time.sleep(10)
    return f"enabled, still building (status: {status}); check again in a minute"


def publish(build: Build, owner: str, name: str, private: bool, pages: bool, yes: bool) -> int:
    check_gh()
    validate_name(name)
    report = check_ready(build)
    if not owner:
        raise P2PError("GitHub owner unknown. Pass --owner or set owner in the skill's config.yaml.")
    slug, site = f"{owner}/{name}", build.site
    visibility = "private" if private else "public"
    if private:
        print("warning: GitHub Pages on a free account needs a public repository. On paid plans a private repository "
              "can be served, and the page itself is then public.")

    if not _repo_exists(slug, site):
        if not yes:
            print(f"CONFIRM: repository {slug} does not exist. It would be created as {visibility}. "
                  "Ask the user to confirm, then run again with --yes.")
            return CONFIRM_NEEDED
        _create(site, slug, private, report["title"])
        print(f"Created {visibility} repository {slug} and pushed.")
    else:
        if not _has_marker(slug, site):
            raise P2PError(f"Repository {slug} exists but has no {MARKER} marker, so it was not created by this tool. "
                           "Nothing was changed. Choose another --name, or ask the user how to proceed.")
        changed = _update(site, slug)
        print(f"Pushed an update to {slug}." if changed else f"{slug} is already up to date; nothing to push.")

    page_url = f"https://{owner.lower()}.github.io/{name}/"
    print(f"Repository: https://github.com/{slug}")
    print(f"Page URL:   {page_url}")
    if pages:
        print(f"Pages:      {_enable_pages(slug, site)}")
    else:
        print("Pages:      not enabled by this run. Go to Settings > Pages > Deploy from a branch > main / (root).")
    return 0
