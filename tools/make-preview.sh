#!/bin/bash

# Regenerate preview.png for the theme switcher. Stages Neovim, btop, and a
# terminal on an empty workspace, screenshots the focused monitor, then closes
# them and returns to the previous workspace. Takes over the screen for ~10s.
# Apply the theme first: omarchy theme set thinkpad
#
# Usage: tools/make-preview.sh [empty-workspace-number]

set -euo pipefail

theme_dir=$(cd "$(dirname "$0")/.." && pwd)
workspace=${1:-5}
monitor=$(hyprctl monitors -j | jq -r '.[] | select(.focused) | .name')
previous=$(hyprctl activeworkspace -j | jq -r .name)

if (( $(hyprctl clients -j | jq --arg ws "$workspace" '[.[] | select(.workspace.name == $ws)] | length') > 0 )); then
  echo "Workspace $workspace has windows; pass an empty workspace number." >&2
  exit 1
fi

dispatch() { hyprctl dispatch "$1" >/dev/null; }

# btop with only the cpu and mem boxes, so the net box's IP address stays out of the shot.
btop_conf=$(mktemp --suffix=.conf)
sed 's/^shown_boxes = .*/shown_boxes = "cpu mem"/' ~/.config/btop/btop.conf > "$btop_conf"

pids=()
cleanup() {
  if (( ${#pids[@]} )); then kill "${pids[@]}" 2>/dev/null || true; fi
  rm -f "$btop_conf"
  dispatch "hl.dsp.focus({ workspace = \"$previous\" })"
}
trap cleanup EXIT

cd "$theme_dir"
dispatch "hl.dsp.focus({ workspace = \"$workspace\" })"
sleep 0.5

# force_split = 2 tiles each new window right/below the focused one:
# Neovim top-left, terminal bottom-left, btop full height on the right.
foot nvim tools/make-preview.sh &>/dev/null & pids+=($!)
sleep 1.5
foot btop -c "$btop_conf" &>/dev/null & pids+=($!)
sleep 1
dispatch 'hl.dsp.focus({ direction = "l" })'
sleep 0.3
# Wait for the tile to reach its final size so nothing scrolls off the top.
foot bash -c 'sleep 0.7; clear; eza -l --icons --no-user --group-directories-first; echo; tools/palette.sh; exec bash' &>/dev/null & pids+=($!)

# Let btop collect a few samples before the shot.
sleep 5
grim -o "$monitor" "$theme_dir/preview.png"
echo "Wrote $theme_dir/preview.png"
