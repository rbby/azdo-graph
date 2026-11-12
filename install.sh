#!/bin/sh
# Installs the CLI and the Claude Code skill.
set -e
here=$(cd "$(dirname "$0")" && pwd)
if command -v pipx >/dev/null; then pipx install --force "$here"; else uv tool install --force "$here"; fi
dest="${CLAUDE_SKILLS_DIR:-$HOME/.claude/skills}/ado-pipeline-graph"
mkdir -p "$dest"
cp "$here/skills/ado-pipeline-graph/SKILL.md" "$dest/"
echo "Installed CLI and skill to $dest"
