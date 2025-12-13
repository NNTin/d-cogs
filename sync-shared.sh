#!/usr/bin/env bash
set -euo pipefail

# absolute paths are safer
SRC="$HOME/git/d-cogs/assistant/"
DST="$HOME/git/vrt-cogs/assistant/"

# ensure destination exists before syncing
mkdir -p "$DST"

rsync -av \
  --delete \
  --exclude='.git' \
  --exclude='.gitignore' \
  "$SRC" "$DST"
