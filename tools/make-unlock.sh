#!/bin/bash

# Render unlock.png (the boot and login screen logo: the wallpaper's ASCII
# wordmark in its dotted frame) and regenerate preview-unlock.png with
# Omarchy's Plymouth previewer.
# Apply the new logo with: omarchy plymouth set by theme thinkpad
#
# Usage: tools/make-unlock.sh

set -euo pipefail

theme_dir=$(cd "$(dirname "$0")/.." && pwd)

color() { awk -F'"' -v key="$1" '$1 ~ "^" key " *= *$" { print $2; exit }' "$theme_dir/colors.toml"; }

python3 -I "$theme_dir/tools/make-ascii-wallpaper.py" --boot-logo "$theme_dir/unlock.png"

# The previewer opens the result in imv; shadow it so nothing takes over the screen.
stub=$(mktemp -d)
trap 'rm -rf "$stub"' EXIT
ln -s "$(type -P true)" "$stub/imv"
PATH="$stub:$PATH" omarchy-plymouth-preview "$(color background)" "$(color foreground)" \
  "$theme_dir/unlock.png" "$theme_dir/preview-unlock.png"

echo "Wrote $theme_dir/unlock.png and $theme_dir/preview-unlock.png"
