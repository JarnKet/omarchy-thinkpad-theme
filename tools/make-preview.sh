#!/bin/bash

# Regenerate preview.png for the theme switcher. Stages Neovim, btop, and a
# terminal on an empty workspace, screenshots the focused monitor, then closes
# them and returns to the previous workspace. Takes over the screen for ~10s.
# Apply the theme first: omarchy theme set thinkpad
#
# Window titles are pinned, so the title bars never show user@host.
# Hyprland launches the windows with a workspace rule, so they land on the
# staging workspace whatever terminal this runs from. The shot is only kept
# if that workspace is on screen holding exactly those windows; anything else
# could put your own windows in a picture meant for the theme switcher.
#
# Usage: tools/make-preview.sh [empty-workspace-number]

set -euo pipefail

theme_dir=$(cd "$(dirname "$0")/.." && pwd)
workspace=${1:-5}
monitor=$(hyprctl monitors -j | jq -r '.[] | select(.focused) | .name')
previous=$(hyprctl activeworkspace -j | jq -r .name)

dispatch() { hyprctl dispatch "$1" >/dev/null; }
on_workspace() { hyprctl clients -j | jq -r --arg ws "$workspace" '.[] | select(.workspace.name == $ws) | .address' | sort; }

if [[ -n $(on_workspace) ]]; then
  echo "Workspace $workspace has windows; pass an empty workspace number." >&2
  exit 1
fi

stage=$(mktemp -d)
ours=()
cleanup() {
  for address in "${ours[@]}"; do
    dispatch "hl.dsp.window.close({ window = \"address:$address\" })"
  done
  rm -rf "$stage"
  dispatch "hl.dsp.focus({ workspace = \"$previous\" })"
}
trap cleanup EXIT

# Start a window on the staging workspace and remember it as ours. Only a
# window with the title we gave it counts: anything else that opens there
# meanwhile (another app, another session) is left alone, and the final check
# below then refuses the shot.
launch() {
  local title=$1 cmd=$2 addr
  dispatch "hl.dsp.exec_cmd(\"$cmd\", { workspace = \"$workspace\" })"
  for _ in {1..50}; do
    sleep 0.1
    addr=$(hyprctl clients -j | jq -r --arg ws "$workspace" --arg t "$title" \
      '.[] | select(.workspace.name == $ws and .class == "foot" and .title == $t) | .address' | head -1)
    if [[ -n $addr ]]; then
      ours+=("$addr")
      return
    fi
  done
  echo "A preview window did not open on workspace $workspace." >&2
  exit 1
}

# btop with only the cpu and mem boxes, so the net box's IP address stays out of the shot.
sed 's/^shown_boxes = .*/shown_boxes = "cpu mem"/' ~/.config/btop/btop.conf > "$stage/btop.conf"
cat > "$stage/term.sh" <<EOF
cd "$theme_dir"
# Wait for the tile to reach its final size so nothing scrolls off the top.
sleep 0.7; clear; eza -l --icons --no-user --group-directories-first; echo; tools/palette.sh; exec bash
EOF

dispatch "hl.dsp.focus({ workspace = \"$workspace\" })"
sleep 0.5

# force_split = 2 tiles each new window right/below the focused one:
# Neovim top-left, terminal bottom-left, btop full height on the right.
launch make-preview.sh "foot -o locked-title=yes --title=make-preview.sh --working-directory=$theme_dir nvim tools/make-preview.sh"
sleep 1.5
launch btop "foot -o locked-title=yes --title=btop btop -c $stage/btop.conf"
sleep 1
dispatch 'hl.dsp.focus({ direction = "l" })'
sleep 0.3
launch thinkpad-theme "foot -o locked-title=yes --title=thinkpad-theme --working-directory=$theme_dir bash $stage/term.sh"

# Let btop collect a few samples before the shot.
sleep 5

shown=$(hyprctl monitors -j | jq -r --arg m "$monitor" '.[] | select(.name == $m) | .activeWorkspace.name')
if [[ $shown != "$workspace" || $(on_workspace) != "$(printf '%s\n' "${ours[@]}" | sort)" ]]; then
  echo "$monitor is not showing workspace $workspace with only the preview windows; preview.png left unchanged." >&2
  exit 1
fi

grim -o "$monitor" "$stage/preview.png"
mv "$stage/preview.png" "$theme_dir/preview.png"
echo "Wrote $theme_dir/preview.png"
