#!/usr/bin/env bash
set -euo pipefail

# absolute paths are safer
SRC="~/git/d-cogs/assistant/"
DST="~/git/vrt-cogs/assistant/"

rsync -av \
  --delete \
  --exclude='.git' \
  --exclude='.gitignore' \
  "$SRC" "$DST"
