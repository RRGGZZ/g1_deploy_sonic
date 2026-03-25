#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SONIC_ROOT="${1:-$(pwd)}"
DATA_SRC="$PLUGIN_ROOT/data/example_scalelab"
DATA_DST="$SONIC_ROOT/gear_sonic_deploy/reference/example_scalelab"

if [ ! -d "$SONIC_ROOT/.git" ]; then
  echo "Error: $SONIC_ROOT is not a git repo"
  exit 1
fi

resolve_path() {
  python3 - "$1" <<'PY'
import os
import sys

print(os.path.realpath(sys.argv[1]))
PY
}

tracked_files=()
new_files=()

while IFS= read -r rel; do
  [ -z "$rel" ] && continue
  if git -C "$SONIC_ROOT" ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
    tracked_files+=("$rel")
  else
    new_files+=("$rel")
  fi
done < "$PLUGIN_ROOT/files.txt"

if [ ${#tracked_files[@]} -gt 0 ]; then
  git -C "$SONIC_ROOT" restore "${tracked_files[@]}"
fi

if [ ${#new_files[@]} -gt 0 ]; then
  for rel in "${new_files[@]}"; do
    rm -f "$SONIC_ROOT/$rel"
  done
fi

if [ -L "$DATA_DST" ] && [ -d "$DATA_SRC" ]; then
  current_target="$(resolve_path "$DATA_DST")"
  expected_target="$(resolve_path "$DATA_SRC")"
  if [ "$current_target" = "$expected_target" ]; then
    rm -f "$DATA_DST"
    echo "Removed dataset link $DATA_DST"
  fi
fi

echo "Plugin uninstall complete."
