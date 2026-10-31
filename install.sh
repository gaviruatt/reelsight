#!/usr/bin/env bash
# One-line installer:  curl -fsSL https://raw.githubusercontent.com/gaviruatt/reelsight/main/install.sh | bash
set -euo pipefail
dest="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/reelsight"
if [ -d "$dest/.git" ]; then
  git -C "$dest" pull --ff-only -q
elif [ -e "$dest" ]; then
  echo "ReelSight is already installed at $dest"
else
  mkdir -p "$(dirname "$dest")"
  git clone -q --depth 1 https://github.com/gaviruatt/reelsight.git "$dest"
fi
echo "Setting up (first run only)…"
bash "$dest/scripts/reelsight" --help >/dev/null
echo "ReelSight is ready. In Claude Code, paste any reel, TikTok or Short link."
