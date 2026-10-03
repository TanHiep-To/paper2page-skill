#!/usr/bin/env bash
# Install the build-page skill for Claude Code and Codex.
#   ./install.sh              symlink this folder into ~/.claude/skills and ~/.agents/skills, set up the venv
#   ./install.sh --copy       copy instead of symlink (the copy gets its own venv)
#   ./install.sh --venv-only  only create the venv and install requirements in this folder
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
MODE="link"
case "${1:-}" in
  --copy) MODE="copy" ;;
  --venv-only) MODE="venv" ;;
  "") ;;
  *) echo "usage: ./install.sh [--copy | --venv-only]" >&2; exit 2 ;;
esac

setup_venv() {
  local dir="$1"
  command -v python3 >/dev/null || { echo "error: python3 is not installed" >&2; exit 1; }
  if [ ! -x "$dir/.venv/bin/python" ]; then
    echo "Creating virtual environment in $dir/.venv"
    python3 -m venv "$dir/.venv"
  fi
  "$dir/.venv/bin/python" -m pip install --quiet --upgrade pip
  "$dir/.venv/bin/python" -m pip install --quiet -r "$dir/requirements.txt"
  "$dir/.venv/bin/python" -m playwright install chromium
  [ -f "$dir/config.yaml" ] || cp "$dir/config.example.yaml" "$dir/config.yaml"
}

place() {  # place <target dir>
  local target="$1"
  mkdir -p "$(dirname "$target")"
  if [ "$(cd "$target" 2>/dev/null && pwd -P)" = "$HERE" ]; then
    echo "Already installed: $target"
    return
  fi
  if [ -e "$target" ] || [ -L "$target" ]; then
    echo "error: $target already exists and is not this folder. Remove it first." >&2
    exit 1
  fi
  if [ "$MODE" = "copy" ]; then
    mkdir -p "$target"
    (cd "$HERE" && tar cf - --exclude .venv --exclude .git --exclude config.yaml --exclude .cache --exclude __pycache__ .) | (cd "$target" && tar xf -)
    echo "Copied to $target"
  else
    ln -s "$HERE" "$target"
    echo "Linked $target -> $HERE"
  fi
}

if [ "$MODE" = "venv" ]; then
  setup_venv "$HERE"
  exit 0
fi

CLAUDE_TARGET="$HOME/.claude/skills/build-page"
CODEX_TARGET="$HOME/.agents/skills/build-page"
place "$CLAUDE_TARGET"
place "$CODEX_TARGET"
if [ "$MODE" = "copy" ]; then
  setup_venv "$CLAUDE_TARGET"
  [ -e "$CODEX_TARGET/.venv" ] || ln -s "$CLAUDE_TARGET/.venv" "$CODEX_TARGET/.venv"
  [ -e "$CODEX_TARGET/config.yaml" ] || ln -s "$CLAUDE_TARGET/config.yaml" "$CODEX_TARGET/config.yaml"
else
  setup_venv "$HERE"
fi

echo
if ! command -v gh >/dev/null; then
  echo "Note: GitHub CLI (gh) is not installed. Building works; publishing needs https://cli.github.com"
elif ! gh auth status >/dev/null 2>&1; then
  echo "Note: gh is installed but not logged in. Run: gh auth login"
else
  echo "gh: logged in as $(gh api user --jq .login)"
fi
echo "Done. Optional defaults (owner, template): $CLAUDE_TARGET/config.yaml"
echo "Claude Code:  /build-page --paper paper.pdf --template ./template"
echo "Codex:        \$build-page --paper paper.pdf --template ./template"
