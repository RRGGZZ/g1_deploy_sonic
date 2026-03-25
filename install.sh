#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SONIC_ROOT="${1:-$(pwd)}"
DATA_SRC="$PLUGIN_ROOT/data/example_scalelab"
DATA_DST="$SONIC_ROOT/gear_sonic_deploy/reference/example_scalelab"

if [ ! -d "$SONIC_ROOT/gear_sonic_deploy" ]; then
  echo "Error: $SONIC_ROOT does not look like the Sonic repo root"
  exit 1
fi

resolve_path() {
  python3 - "$1" <<'PY'
import os
import sys

print(os.path.realpath(sys.argv[1]))
PY
}

while IFS= read -r rel; do
  [ -z "$rel" ] && continue
  src="$PLUGIN_ROOT/overlay/$rel"
  dst="$SONIC_ROOT/$rel"
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "Installed $rel"
done < "$PLUGIN_ROOT/files.txt"

if [ -d "$DATA_SRC" ]; then
  mkdir -p "$(dirname "$DATA_DST")"
  if [ -L "$DATA_DST" ]; then
    current_target="$(resolve_path "$DATA_DST")"
    expected_target="$(resolve_path "$DATA_SRC")"
    if [ "$current_target" = "$expected_target" ]; then
      echo "Dataset link already exists at $DATA_DST"
    else
      rm -f "$DATA_DST"
      ln -s "$DATA_SRC" "$DATA_DST"
      echo "Updated dataset link $DATA_DST -> $DATA_SRC"
    fi
  elif [ -e "$DATA_DST" ]; then
    echo "Skipped dataset link because $DATA_DST already exists."
    echo "Example dataset remains available at $DATA_SRC"
  else
    ln -s "$DATA_SRC" "$DATA_DST"
    echo "Linked dataset $DATA_DST -> $DATA_SRC"
  fi
fi

echo "Plugin install complete."
