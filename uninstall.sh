#!/usr/bin/env bash
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SONIC_ROOT="${1:-$(pwd)}"

if [ ! -d "$SONIC_ROOT/.git" ]; then
  echo "Error: $SONIC_ROOT is not a git repo"
  exit 1
fi

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

echo "Plugin uninstall complete."
