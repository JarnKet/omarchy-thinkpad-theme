# Thinkpad — an Omarchy theme

Vantablack, ThinkPad edition. Pure black surfaces, crisp white key legends,
every color a shade of gray, and a single TrackPoint-red accent used only where
attention matters.

![Thinkpad theme preview](preview.png)

## Install

```bash
omarchy theme install https://github.com/JarnKet/omarchy-thinkpad-theme
```

The theme ships no wallpapers of its own. Render the two ASCII ones (they
put your username in the art, so they're made on your machine), then cycle
them with `omarchy theme bg next`:

```bash
T=~/.config/omarchy/themes/thinkpad/tools
mkdir -p ~/.config/omarchy/backgrounds/thinkpad
python3 $T/make-ascii-wallpaper.py --world ~/.config/omarchy/backgrounds/thinkpad/1-ascii-world.png
python3 $T/make-ascii-wallpaper.py ~/.config/omarchy/backgrounds/thinkpad/2-ascii-thinkpad.png
omarchy theme set thinkpad
```

### Boot screen (optional)

The theme includes an ASCII ThinkPad wordmark for the disk-unlock and login
screens. Applying it needs sudo and rebuilds the initramfs:

```bash
omarchy plymouth set by theme thinkpad
```

![Boot screen preview](preview-unlock.png)

Undo with `omarchy plymouth reset`.

## Palette

| Role              | Hex       | Inspiration                     |
|-------------------|-----------|---------------------------------|
| background        | `#000000` | Vantablack                      |
| foreground        | `#FFFFFF` | Key legends                     |
| selection         | `#1A1A1A` | Keyboard deck                   |
| accent            | `#8D8D8D` | Graphite                        |
| muted             | `#7A7A7A` | Comments, hints                 |
| ANSI colors       | `#5C5C5C`–`#CECECE` | Grays, as in Vantablack |
| red               | `#E3241A` | TrackPoint / logo dot red       |
| bright red        | `#FF5A4F` | Error text                      |

Red passes WCAG 4.5:1 against the black background.

## Where the red goes

The active window border, the selected launcher and menu row, the lock screen
while typing, notification countdowns, bar modules calling for attention, btop
at peak load, and errors (including terminal red). Everything else, popups,
notifications, menus, and btop boxes included, stays black, white, and gray.

## Files

- `colors.toml`: the palette. Omarchy generates Hyprland borders, terminals,
  Neovim (aether.nvim), VS Code, Chromium, Helix, gum, etc. from it.
- `shell.toml`: bar, popups, menus, notifications, and lock screen.
  Keep it in sync with `colors.toml`.
- `btop.theme`: a monochrome btop on black.
- `icons.theme`: `Yaru-gray`, as in Vantablack.
- `unlock.png`, `preview-unlock.png`: boot and login screen logo (the
  wallpaper's ASCII wordmark in its dotted frame) and its preview, rendered
  by `tools/make-unlock.sh` from `tools/wordmark.svg`.
- `preview.png`: the theme switcher thumbnail.
- `tools/make-preview.sh`: regenerates `preview.png` (takes over the screen
  for about 10 seconds). `tools/palette.sh` prints the terminal palette.
- `tools/make-ascii-wallpaper.py`: renders an ASCII-art wallpaper in the
  theme's colors (an open ThinkPad with a code editor on screen, and the
  wordmark in binary) to `~/Pictures/thinkpad-ascii.png`. With `--world` it
  renders the other one, after the time-zone map: the wordmark over a braille
  world map in 24 hour columns, the date line in red and your own zone lit
  (`~/Pictures/thinkpad-ascii-world.png`). Pass an output path to write it
  somewhere else, e.g. into `~/.config/omarchy/backgrounds/thinkpad/`.
- `tools/world-land.png`: the land for `--world`, from Natural Earth's 1:50m
  land and lakes, rasterized at 10 pixels per degree.

## Developing

Link a checkout instead of installing, so edits apply on the next
`omarchy theme set thinkpad`:

```bash
ln -s ~/Projects/thinkpad-theme ~/.config/omarchy/themes/thinkpad
```

## Credits

Not affiliated with or endorsed by Lenovo. ThinkPad, TrackPoint, and Lenovo
are trademarks of Lenovo. The wordmark outline comes from
[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:ThinkPad_Logo.svg),
where it is a public-domain text logo. Map data from
[Natural Earth](https://www.naturalearthdata.com/) (public domain).

## License

The theme files are MIT licensed; see [LICENSE](LICENSE).
