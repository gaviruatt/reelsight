#!/usr/bin/env bash
# ReelSight installer: checks dependencies and links the skill into Claude Code.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
dest="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/reelsight"

need() { command -v "$1" >/dev/null 2>&1; }
if ! need ffmpeg; then
  if need brew; then brew install ffmpeg; else echo "Install ffmpeg first (e.g. sudo apt install ffmpeg)"; exit 1; fi
fi
if ! need uv; then
  if need brew; then brew install uv; else curl -LsSf https://astral.sh/uv/install.sh | sh; fi
fi

mkdir -p "$(dirname "$dest")"
ln -sfn "$here/skills/reelsight" "$dest"
echo "Linked $dest -> $here/skills/reelsight"

echo "Warming up dependencies (first run only)…"
uv run --script "$here/skills/reelsight/scripts/watch.py" --help >/dev/null
echo "Done. In Claude Code, paste a reel link and say: watch this."
