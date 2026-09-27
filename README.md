# Thinkpad — an Omarchy theme

Professional, reliable, executive. A matte-black chassis, crisp white key
legends, and a single TrackPoint-red accent used only where attention matters.

![Thinkpad theme preview](preview.png)

## Install

```bash
omarchy theme install https://github.com/JarnKet/omarchy-thinkpad-theme
```

Cycle the three wallpapers with `omarchy theme bg next`.

### Boot screen (optional)

The theme includes a ThinkPad wordmark for the disk-unlock screen. Applying
it needs sudo and rebuilds the initramfs:

```bash
omarchy plymouth set by theme thinkpad
```

![Boot screen preview](preview-unlock.png)

Undo with `omarchy plymouth reset`.

## Palette

| Role              | Hex       | Inspiration                    |
|-------------------|-----------|--------------------------------|
| accent            | `#E3241A` | TrackPoint / logo dot red      |
| background        | `#141414` | Matte chassis                  |
| darker background | `#0A0A0A` | Keyboard deck                  |
| selection         | `#2F2F2F` | Graphite                       |
| foreground        | `#E4E4E4` | Key legends                    |
| dim text          | `#8A8A8A` | "New World. New Thinking."     |
| red               | `#EC3A30` | Errors (lifted for legibility) |
| green             | `#28985F` | Power LED / GMT line           |
| yellow            | `#D4A13A` | ThinkLight amber               |
| blue              | `#5585CC` | Classic IBM blue Enter key     |

Every ANSI color meets WCAG 4.5:1 against the background; comments (`muted`)
meet 3:1.

## Where the red goes

Active window border, focused workspace, selected menu row, lock screen while
typing, btop at peak load, and errors. Popups, notifications, menus, and btop
boxes use graphite borders.

## Files

- `colors.toml`: the palette. Omarchy generates Hyprland borders, terminals,
  Neovim (aether.nvim), VS Code, Chromium, Helix, gum, etc. from it.
- `shell.toml`: bar, popups, menus, notifications, and lock screen.
  Keep it in sync with `colors.toml`.
- `btop.theme`: a monochrome btop.
- `icons.theme`: `Yaru-red`.
- `backgrounds/`: the timezone map (the default), the mountain, and the red
  ThinkPad.
- `unlock.png`, `preview-unlock.png`: boot screen logo and its preview.
- `preview.png`: the theme switcher thumbnail.
- `tools/make-preview.sh`: regenerates `preview.png` (takes over the screen
  for about 10 seconds). `tools/palette.sh` prints the terminal palette.

## Developing

Link a checkout instead of installing, so edits apply on the next
`omarchy theme set thinkpad`:

```bash
ln -s ~/Projects/thinkpad-theme ~/.config/omarchy/themes/thinkpad
```

## Credits

Not affiliated with or endorsed by Lenovo. ThinkPad, TrackPoint, and Lenovo
are trademarks of Lenovo. The wallpapers belong to their respective creators
and are not covered by this repository's license.

## License

The theme files are MIT licensed; see [LICENSE](LICENSE).
