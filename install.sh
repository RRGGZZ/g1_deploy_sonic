#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SONIC_ROOT="${1:-$(pwd)}"

if [ ! -d "$SONIC_ROOT/gear_sonic_deploy" ]; then
  echo "Error: $SONIC_ROOT does not look like the Sonic repo root"
  exit 1
fi

while IFS= read -r rel; do
  [ -z "$rel" ] && continue
  src="$PLUGIN_ROOT/overlay/$rel"
  dst="$SONIC_ROOT/$rel"
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
  echo "Installed $rel"
done < "$PLUGIN_ROOT/files.txt"

echo "Plugin install complete."
