#!/usr/bin/env python3
"""Render an ASCII-art ThinkPad wallpaper.

An open ThinkPad with a code editor on its screen and the red TrackPoint on its
keyboard, the ThinkPad wordmark spelled out in code underneath, all inside a
dotted frame. Every mark on the image is a character on one monospace grid.

Usage: tools/make-ascii-wallpaper.py [output.png] [--width 3840 --height 2160]
       tools/make-ascii-wallpaper.py --boot-logo [unlock.png]
"""

import argparse
import getpass
import math
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

THEME_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_REGULAR = "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Regular.ttf"
FONT_BOLD = "/usr/share/fonts/TTF/JetBrainsMonoNerdFont-Bold.ttf"


def theme_colors():
    colors = {}
    with open(os.path.join(THEME_DIR, "colors.toml")) as f:
        for line in f:
            m = re.match(r'\s*(\w+)\s*=\s*"#([0-9A-Fa-f]{6})"', line)
            if m:
                colors[m.group(1)] = tuple(int(m.group(2)[i:i + 2], 16) for i in (0, 2, 4))
    return colors


C = theme_colors()
BG = C["background"]
RED = C["red"]
BRIGHT_RED = C["bright_red"]


def gray(v):
    v = int(max(0, min(255, v)))
    return (v, v, v)


# --- the character grid ------------------------------------------------------

class Grid:
    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.ch = np.full((rows, cols), " ", dtype="<U1")
        self.fg = np.zeros((rows, cols, 3), dtype=np.int32)
        self.bg = np.full((rows, cols, 3), -1, dtype=np.int32)
        self.bold = np.zeros((rows, cols), dtype=bool)

    def put(self, r, c, ch, fg, bold=False, bg=None):
        if 0 <= r < self.rows and 0 <= c < self.cols:
            self.ch[r, c] = ch
            self.fg[r, c] = fg
            self.bold[r, c] = bold
            if bg is not None:
                self.bg[r, c] = bg

    def text(self, r, c, s, fg, bold=False, bg=None, clip=None):
        for i, ch in enumerate(s):
            if clip is None or clip(r, c + i):
                self.put(r, c + i, ch, fg, bold, bg)


# --- a tiny 3D laptop --------------------------------------------------------

class Camera:
    def __init__(self, eye, target, fov_scale=1.0):
        self.eye = np.array(eye, float)
        f = np.array(target, float) - self.eye
        self.f = f / np.linalg.norm(f)
        r = np.cross([0, 1, 0], self.f)
        self.r = r / np.linalg.norm(r)
        self.u = np.cross(self.f, self.r)
        self.scale, self.offset = 1.0, np.zeros(2)

    def raw(self, p):
        d = np.asarray(p, float) - self.eye
        z = d @ self.f
        return np.stack([(d @ self.r) / z, -(d @ self.u) / z], axis=-1)

    def fit(self, points, box):
        pts = self.raw(points)
        lo, hi = pts.min(0), pts.max(0)
        x0, y0, x1, y1 = box
        self.scale = min((x1 - x0) / (hi[0] - lo[0]), (y1 - y0) / (hi[1] - lo[1]))
        size = (hi - lo) * self.scale
        self.offset = np.array([x0 + (x1 - x0 - size[0]) / 2, y0 + (y1 - y0 - size[1]) / 2]) - lo * self.scale

    def __call__(self, p):
        return self.raw(p) * self.scale + self.offset


# Dimensions in centimetres, roughly a T14s: x to the right, y up, z away from
# the viewer. The base's front edge is z = 0.
BASE_W, BASE_D, BASE_H = 32.2, 22.6, 1.7
HINGE_Z = 21.9
LID_H = 21.2
LID_TILT = math.radians(14)  # back from vertical
KB_X0, KB_X1 = 3.0, BASE_W - 3.0
KB_BACK, KB_FRONT = 20.9, 10.0
UNIT = (KB_X1 - KB_X0) / 15
PAD_X0, PAD_X1 = BASE_W / 2 - 5.6, BASE_W / 2 + 5.6
BUTTON_Z = 9.05
SCREEN_U = (0.75, BASE_W - 0.75)
SCREEN_V = (1.5, LID_H - 1.05)

# Semantic ids for the raster fills.
BGID, DECK, FRONT, SIDE, BEZEL, SCREEN = range(6)


def deck(x, z):
    return (x, BASE_H, z)


def lid(u, v):
    return (u, BASE_H + v * math.cos(LID_TILT), HINGE_Z + v * math.sin(LID_TILT))


def keyboard_layout():
    """Rows of (label, width-in-units), back row first. Every row is 15 units."""
    fn = ["Esc"] + [f"F{i}" for i in range(1, 13)] + ["Hom", "End", "Ins", "Del"]
    return [
        [(label, 15 / 17) for label in fn],
        [("`", 1)] + [(k, 1) for k in "1234567890-="] + [("Bksp", 2)],
        [("Tab", 1.5)] + [(k, 1) for k in "QWERTYUIOP[]"] + [("\\", 1.5)],
        [("Caps", 1.75)] + [(k, 1) for k in "ASDFGHJKL;'"] + [("Enter", 2.25)],
        [("Shift", 2.25)] + [(k, 1) for k in "ZXCVBNM,./"] + [("Shift", 2.75)],
        [("Fn", 1), ("Ctl", 1), ("Sup", 1), ("Alt", 1), ("", 5), ("Alt", 1), ("Prt", 1), ("Ctl", 1),
         ("<", 1), ("^", 1), (">", 1)],
    ]


def build_fills(camera, size):
    """Rasterize the laptop's surfaces into an id map."""
    ids = Image.new("L", size, BGID)
    draw = ImageDraw.Draw(ids)

    def poly(points3d, value):
        draw.polygon([tuple(p) for p in camera(np.array(points3d, float))], fill=value)

    poly([(BASE_W, 0, 0), (BASE_W, 0, BASE_D), (BASE_W, BASE_H, BASE_D), (BASE_W, BASE_H, 0)], SIDE)
    poly([(0, 0, 0), (0, 0, BASE_D), (0, BASE_H, BASE_D), (0, BASE_H, 0)], SIDE)
    poly([(0, 0, 0), (BASE_W, 0, 0), (BASE_W, BASE_H, 0), (0, BASE_H, 0)], FRONT)
    poly([deck(0, 0), deck(BASE_W, 0), deck(BASE_W, BASE_D), deck(0, BASE_D)], DECK)
    poly([lid(0, 0), lid(BASE_W, 0), lid(BASE_W, LID_H), lid(0, LID_H)], BEZEL)
    (u0, u1), (v0, v1) = SCREEN_U, SCREEN_V
    poly([lid(u0, v0), lid(u1, v0), lid(u1, v1), lid(u0, v1)], SCREEN)
    return np.array(ids)


def line(grid, camera, a, b, cw, ch, color, bold=False):
    """Draw a 3D segment as characters, picked by its on-screen slope."""
    (x0, y0), (x1, y1) = camera(np.array([a, b], float))
    dx, dy = x1 - x0, y1 - y0
    angle = math.degrees(math.atan2(dy, dx)) % 180
    if angle < 18 or angle > 162:
        # Shallow: one cell per column; the character follows the height of
        # the line inside the cell.
        for c in range(int(min(x0, x1) // cw), int(max(x0, x1) // cw) + 1):
            x = (c + 0.5) * cw
            t = 0 if dx == 0 else min(1, max(0, (x - x0) / dx))
            y = y0 + dy * t
            r = int(y // ch)
            frac = y / ch - r
            grid.put(r, c, "\u203e" if frac < 0.3 else ("-" if frac < 0.6 else "_"), color, bold)
        return
    s = "\\" if angle < 72 else ("|" if angle < 108 else "/")
    for r in range(int(min(y0, y1) // ch), int(max(y0, y1) // ch) + 1):
        y = (r + 0.5) * ch
        t = 0 if dy == 0 else min(1, max(0, (y - y0) / dy))
        grid.put(r, int((x0 + dx * t) // cw), s, color, bold)


# --- what the laptop's screen shows ------------------------------------------

EXPLORER = [
    ("EXPLORER", "head"),
    ("", None),
    ("v thinkpad", "dir"),
    ("  > cmd", "dir"),
    ("  v internal", "dir"),
    ("      keyboard.go", "file"),
    ("      trackpoint.go", "file"),
    ("    main.go", "active"),
    ("    go.mod", "file"),
    ("    README.md", "file"),
    ("", None),
    ("v OMARCHY", "dir"),
    ("    hyprland.lua", "file"),
    ("    shell.json", "file"),
    ("    colors.toml", "file"),
]

CODE = """package main

import (
	"fmt"
	"thinkpad/internal/trackpoint"
)

// ThinkPad: matte black, white legends, one red dot.
type ThinkPad struct {
	Model      string
	TrackPoint *trackpoint.Nub
	Battery    int
}

func main() {
	tp := ThinkPad{Model: "T14s Gen 6", Battery: 100}
	tp.TrackPoint = trackpoint.New(trackpoint.Red)

	for _, idea := range backlog() {
		if err := tp.Think(idea); err != nil {
			continue // think harder
		}
		ship(idea)
	}

	fmt.Println("press the red dot to begin")
}"""

GO_KEYWORDS = {"package", "import", "type", "struct", "func", "for", "range", "if", "return",
               "continue", "nil", "err", "string", "int", "var", "const"}

TERMINAL = [
    ("$ go run ./cmd/think", "cmd"),
    ("  compiling trackpoint.go ... ok", "out"),
    ("  12 ideas shipped in 0.42s", "out"),
    ("$ ", "prompt"),
]


def tokenize_go(line):
    """Split a Go line into (text, kind) runs: comment, string, keyword, number, plain."""
    out = []
    i = 0
    while i < len(line):
        if line.startswith("//", i):
            out.append((line[i:], "comment"))
            break
        ch = line[i]
        if ch == '"':
            j = line.find('"', i + 1)
            j = len(line) if j < 0 else j + 1
            out.append((line[i:j], "string"))
            i = j
        elif ch.isalpha() or ch == "_":
            j = i
            while j < len(line) and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            kind = "keyword" if word in GO_KEYWORDS else ("type" if word[0].isupper() else "plain")
            out.append((word, kind))
            i = j
        elif ch.isdigit():
            j = i
            while j < len(line) and (line[j].isdigit() or line[j] == "."):
                j += 1
            out.append((line[i:j], "number"))
            i = j
        else:
            out.append((ch, "punct"))
            i += 1
    return out


SYNTAX = {
    "comment": (gray(0x6B), False),
    "string": (gray(0x9A), False),
    "keyword": (gray(0xFF), True),
    "type": (gray(0xE4), False),
    "number": (RED, False),
    "plain": (gray(0xC0), False),
    "punct": (gray(0x86), False),
}


def draw_screen(grid, mask):
    """Lay an editor into the cells covered by the screen (mask: rows x cols bool)."""
    widths = mask.sum(1)
    rows = np.where(widths >= 0.8 * widths.max())[0]
    if len(rows) < 8:
        return
    top, bottom = rows[0], rows[-1]
    bounds = {r: (np.where(mask[r])[0][0], np.where(mask[r])[0][-1]) for r in rows}

    def inside(r, c):
        return 0 <= r < grid.rows and 0 <= c < grid.cols and mask[r, c]

    # Panel: the largest upright rectangle inside the screen, so the editor's
    # columns stay straight even though the lid is seen in perspective.
    edge = max(bounds[r][0] for r in rows)
    right_edge = min(bounds[r][1] for r in rows)
    bounds = {r: (edge, right_edge) for r in rows}
    mask = np.zeros_like(mask)
    mask[top:bottom + 1, edge:right_edge + 1] = True
    for r in rows:
        for c in range(edge, right_edge + 1):
            grid.put(r, c, " ", gray(0), bg=(17, 17, 17))

    def left(r):
        return edge + 1

    # Tab bar.
    r = top
    tabs = [(" main.go ", True), (" trackpoint.go ", False), (" keyboard.go ", False)]
    c = left(r) + 24
    grid.text(r, left(r) + 1, "THINKPAD", gray(0x8A), True, clip=inside)
    for name, active in tabs:
        grid.text(r, c, name, gray(0xFF) if active else gray(0x6B), active,
                  bg=(32, 32, 32) if active else None, clip=inside)
        if active:
            grid.put(r, c + len(name) - 1, "*", RED, True, bg=(32, 32, 32))
        c += len(name) + 1

    sidebar = 24
    term_rows = len(TERMINAL) + 1
    status_row = bottom
    term_top = status_row - term_rows

    # Explorer.
    for i, (label, kind) in enumerate(EXPLORER):
        r = top + 2 + i
        if r >= term_top:
            break
        color = {"head": gray(0x8A), "dir": gray(0xC8), "file": gray(0x8A), "active": gray(0xFF)}.get(kind, gray(0x6B))
        bg = (40, 40, 40) if kind == "active" else None
        grid.text(r, left(r) + 1, label.ljust(sidebar - 2) if bg else label, color, kind in ("head", "active"),
                  bg=bg, clip=inside)

    # Divider between the explorer and the editor.
    for r in range(top + 1, status_row):
        if inside(r, left(r) + sidebar):
            grid.put(r, left(r) + sidebar, "│", gray(0x30))

    # Code with line numbers; the cursor sits at the end of the Println line.
    code_lines = CODE.split("\n")
    for i, line in enumerate(code_lines):
        r = top + 2 + i
        if r >= term_top - 1:
            break
        base = left(r) + sidebar + 2
        current = "Println" in line
        grid.text(r, base, f"{i + 1:>3}", gray(0xE4) if current else gray(0x44), clip=inside)
        c = base + 5
        for text, kind in tokenize_go(line.replace("\t", "    ")):
            fg, bold = SYNTAX[kind]
            grid.text(r, c, text, fg, bold, clip=inside)
            c += len(text)
        if current:
            grid.put(r, c, " ", gray(0), bg=RED)

    # Terminal panel.
    for c in range(bounds[term_top][0], bounds[term_top][1] + 1):
        if inside(term_top, c):
            grid.put(term_top, c, "─", gray(0x30), bg=(17, 17, 17))
    grid.text(term_top, left(term_top) + 2, " TERMINAL ", gray(0x8A), True, clip=inside)
    for i, (line, kind) in enumerate(TERMINAL):
        r = term_top + 1 + i
        color = {"cmd": gray(0xE4), "out": gray(0x8A), "prompt": gray(0xE4)}[kind]
        grid.text(r, left(r) + 1, line, color, kind == "cmd", clip=inside)
        if kind == "prompt":
            grid.put(r, left(r) + 1 + len(line), " ", gray(0), bg=gray(0xE4))

    # Status line.
    r = status_row
    for c in range(bounds[r][0], bounds[r][1] + 1):
        grid.put(r, c, " ", gray(0), bg=(30, 30, 30))
    grid.text(r, bounds[r][0], " NORMAL ", gray(0xFF), True, bg=RED, clip=inside)
    grid.text(r, bounds[r][0] + 9, " main.go   go   utf-8", gray(0xC8), bg=(30, 30, 30), clip=inside)
    right = "Ln 26, Col 43 "
    grid.text(r, bounds[r][1] - len(right) + 1, right, gray(0xC8), bg=(30, 30, 30), clip=inside)


# --- turning the laptop into characters ------------------------------------

BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16


def cell_cover(ids, cw, ch, cols, rows):
    """Per-cell coverage of every fill id."""
    blocks = ids[:rows * ch, :cols * cw].reshape(rows, ch, cols, cw).transpose(0, 2, 1, 3)
    blocks = blocks.reshape(rows, cols, ch * cw)
    return np.stack([(blocks == i).mean(-1) for i in range(SCREEN + 1)], axis=-1)


def cell(camera, p, cw, ch):
    x, y = camera(np.array(p, float))
    return int(y // ch), int(x // cw)


def draw_laptop(grid, camera, cover, cw, ch):
    rows, cols = grid.rows, grid.cols
    dominant = cover.argmax(-1)
    yy, xx = np.mgrid[0:rows, 0:cols]

    # Matte deck: an ordered dither that thins out toward the front and right,
    # as if lit from behind the screen on the left.
    deck_rows = np.where((dominant == DECK).any(1))[0]
    deck_cols = np.where((dominant == DECK).any(0))[0]
    fy = (yy - deck_rows.min()) / max(1, np.ptp(deck_rows))
    fx = (xx - deck_cols.min()) / max(1, np.ptp(deck_cols))
    density = 0.42 - 0.22 * fy - 0.12 * fx
    for r, c in zip(*np.where(dominant == DECK)):
        t = BAYER[r % 4, c % 4]
        if density[r, c] > t:
            grid.put(r, c, ":" if density[r, c] > t + 0.3 else ".", gray(0x58))

    # Keyboard: one text row per key row, a free row under the function keys
    # and another under the home row for the TrackPoint when there is room.
    rows_kb = keyboard_layout()
    heights = [0.95] + [1.0] * 5
    pitch = (KB_BACK - KB_FRONT) / sum(heights)
    first = cell(camera, deck(BASE_W / 2, KB_BACK - heights[0] * pitch / 2), cw, ch)[0]
    last = cell(camera, deck(BASE_W / 2, KB_FRONT + pitch / 2), cw, ch)[0]
    span = last - first
    offsets = [0, 2, 3, 4, 6, 7] if span >= 7 else ([0, 1, 2, 3, 5, 6] if span >= 6 else [0, 1, 2, 3, 4, 5])
    well_l = int(camera(np.array(deck(KB_X0, KB_FRONT)))[0] // cw)
    well_r = int(camera(np.array(deck(KB_X1, KB_FRONT)))[0] // cw)
    for r in range(first - 1, first + offsets[-1] + 1):
        for c in range(well_l, well_r + 1):
            grid.put(r, c, " ", gray(0))
    keys = {}
    z = KB_BACK
    for i, (row, h) in enumerate(zip(rows_kb, heights)):
        zc = z - h * pitch / 2
        r = first + offsets[i]
        x = KB_X0
        for label, w in row:
            px0 = camera(np.array(deck(x, zc)))[0]
            px1 = camera(np.array(deck(x + w * UNIT, zc)))[0]
            c0, c1 = int(round(px0 / cw)), int(round(px1 / cw)) - 1
            for cc in range(c0, c1 + 1):
                grid.put(r, cc, " ", gray(0))
            span_cells = c1 - c0 + 1
            if i == 0 or len(label) > span_cells - 2:
                # Too small for brackets: the label alone, centred on the key.
                text = label[:span_cells]
                grid.text(r, c0 + (span_cells - len(text)) // 2, text, gray(0xA8 if i == 0 else 0xF4), i > 0)
            else:
                grid.put(r, c0, "[", gray(0x5E))
                grid.put(r, c1, "]", gray(0x5E))
                inner = span_cells - 2
                grid.text(r, c0 + 1 + (inner - len(label)) // 2, label, gray(0xF4), True)
            keys.setdefault(label, (r, c0, c1))
            x += w * UNIT
        z -= h * pitch

    # TrackPoint, under the meeting point of G and H.
    g_row, _, g_end = keys["G"]
    nub_row = g_row + 1 if offsets[4] - offsets[3] > 1 else g_row
    grid.put(nub_row, g_end + (1 if nub_row != g_row else 0), "●", BRIGHT_RED, True)

    # TrackPoint buttons, the middle one between two red stripes.
    r = max(cell(camera, deck(BASE_W / 2, BUTTON_Z), cw, ch)[0], first + offsets[-1] + 2)
    third = (PAD_X1 - PAD_X0) / 3
    stripes = []
    for i in range(3):
        c0 = int(round(camera(np.array(deck(PAD_X0 + i * third + 0.12, BUTTON_Z)))[0] / cw))
        c1 = int(round(camera(np.array(deck(PAD_X0 + (i + 1) * third - 0.12, BUTTON_Z)))[0] / cw)) - 1
        grid.text(r, c0, "[" + "=" * max(0, c1 - c0 - 1) + "]", gray(0x6A))
        if i == 1:
            stripes += [c0 - 1, c1 + 1]
    for c in stripes:
        grid.put(r, c, "|", RED, True)

    # Touchpad outline.
    corners = [deck(PAD_X0, 7.9), deck(PAD_X1, 7.9), deck(PAD_X1, 1.9), deck(PAD_X0, 1.9)]
    for a, b in zip(corners, corners[1:] + corners[:1]):
        line(grid, camera, a, b, cw, ch, gray(0x4E))

    # ThinkPad logo on the palm rest, its i in red.
    r, c = cell(camera, deck(BASE_W - 5.0, 1.15), cw, ch)
    for i, letter in enumerate("ThinkPad"):
        grid.put(r, c - 4 + i, letter, BRIGHT_RED if letter == "i" else gray(0xD0), True)

    # Silhouette and the edges between the big surfaces.
    bright, dim = gray(0xD8), gray(0x62)
    line(grid, camera, lid(0, 0), lid(0, LID_H), cw, ch, bright, True)
    line(grid, camera, lid(BASE_W, 0), lid(BASE_W, LID_H), cw, ch, bright, True)
    line(grid, camera, lid(0, LID_H), lid(BASE_W, LID_H), cw, ch, bright, True)
    line(grid, camera, lid(0, 0.15), lid(BASE_W, 0.15), cw, ch, dim)
    line(grid, camera, deck(0, 0), deck(0, HINGE_Z), cw, ch, bright, True)
    line(grid, camera, deck(BASE_W, 0), deck(BASE_W, HINGE_Z), cw, ch, bright, True)
    line(grid, camera, deck(0, 0), deck(BASE_W, 0), cw, ch, gray(0xB0))
    line(grid, camera, (0, 0, 0), (BASE_W, 0, 0), cw, ch, bright, True)

    # Webcam.
    r, c = cell(camera, lid(BASE_W / 2, LID_H - 0.5), cw, ch)
    grid.put(r, c, "o", gray(0x50))

    # A little light pooling on the desk under the machine.
    r, c_mid = cell(camera, (BASE_W / 2, 0, 0), cw, ch)
    half = int((camera(np.array((BASE_W, 0, 0)))[0] - camera(np.array((0, 0, 0)))[0]) / cw * 0.5)
    for dr, reach in ((1, 1.1), (2, 0.75)):
        for c in range(c_mid - int(half * reach), c_mid + int(half * reach) + 1):
            d = abs(c - c_mid) / (half * reach)
            if d < 0.35:
                grid.put(r + dr, c, ":" if dr == 1 else ".", gray(0x38))
            elif d < 0.7 and c % 2 == 0:
                grid.put(r + dr, c, ".", gray(0x30))
            elif c % 4 == 0:
                grid.put(r + dr, c, ".", gray(0x26))


# --- the wordmark, spelled in code -------------------------------------------

def wordmark_masks(width_px):
    svg = os.path.join(THEME_DIR, "tools", "wordmark.svg")
    png = subprocess.run(["rsvg-convert", "-w", str(width_px), svg], check=True, capture_output=True).stdout
    from io import BytesIO
    img = np.asarray(Image.open(BytesIO(png)).convert("RGBA")).astype(float) / 255
    alpha = img[..., 3]
    red = alpha * ((img[..., 0] > 0.6) & (img[..., 1] < 0.4))
    white = alpha * ~((img[..., 0] > 0.6) & (img[..., 1] < 0.4))
    return white, red


def draw_wordmark(grid, top_row, width_cols, cw, ch, message="ThinkPad // think, then ship. "):
    white, red = wordmark_masks(width_cols * cw)
    rows = int(math.ceil(white.shape[0] / ch))
    pad_h = rows * ch - white.shape[0]
    white = np.pad(white, ((0, pad_h), (0, 0)))
    red = np.pad(red, ((0, pad_h), (0, 0)))
    w_cov = white.reshape(rows, ch, width_cols, cw).mean((1, 3))
    r_cov = red.reshape(rows, ch, width_cols, cw).mean((1, 3))
    left = (grid.cols - width_cols) // 2

    stream = "".join(format(ord(ch_), "08b") for ch_ in message)
    k = 0
    for r in range(rows):
        for c in range(width_cols):
            if r_cov[r, c] > 0.3:
                grid.put(top_row + r, left + c, "@" if r_cov[r, c] > 0.6 else "o", BRIGHT_RED, True)
            elif w_cov[r, c] > 0.55:
                grid.put(top_row + r, left + c, stream[k % len(stream)], gray(0xEC), True)
                k += 1
            elif w_cov[r, c] > 0.18:
                grid.put(top_row + r, left + c, ":" if w_cov[r, c] > 0.35 else ".", gray(0x80))
    return rows


# --- frame and captions ------------------------------------------------------

def draw_frame(grid, inset_c, inset_r):
    r0, r1 = inset_r, grid.rows - 1 - inset_r
    c0, c1 = inset_c, grid.cols - 1 - inset_c
    dot = gray(0x3C)
    for c in range(c0, c1 + 1, 2):
        grid.put(r0, c, "·", dot)
        grid.put(r1, c, "·", dot)
    for r in range(r0, r1 + 1):
        grid.put(r, c0, "·", dot)
        grid.put(r, c1, "·", dot)
    for r, c in ((r0, c0), (r0, c1), (r1, c0), (r1, c1)):
        grid.put(r, c, "+", gray(0x70))
    return r0, r1, c0, c1


# --- output ------------------------------------------------------------------

def render(grid, cw, ch, size, glow=True):
    img = Image.new("RGB", size, BG)
    draw = ImageDraw.Draw(img)
    regular = ImageFont.truetype(FONT_REGULAR, int(cw / 0.6))
    bold = ImageFont.truetype(FONT_BOLD, int(cw / 0.6))
    ox = (size[0] - grid.cols * cw) // 2
    oy = (size[1] - grid.rows * ch) // 2

    for r in range(grid.rows):
        for c in range(grid.cols):
            if grid.bg[r, c, 0] >= 0:
                x, y = ox + c * cw, oy + r * ch
                draw.rectangle([x, y, x + cw - 1, y + ch - 1], fill=tuple(grid.bg[r, c]))
    for r in range(grid.rows):
        for c in range(grid.cols):
            s = grid.ch[r, c]
            if s != " ":
                draw.text((ox + c * cw, oy + r * ch - 1), s, fill=tuple(grid.fg[r, c]),
                          font=bold if grid.bold[r, c] else regular)

    if glow:
        # A soft halo around everything lit, like the reference renders' dots.
        arr = np.asarray(img, np.float32)
        lit = np.clip(arr - np.array(BG, np.float32), 0, 255).astype(np.uint8)
        halo = np.asarray(Image.fromarray(lit).filter(ImageFilter.GaussianBlur(cw * 0.8)), np.float32)
        img = Image.fromarray(np.clip(arr + halo * 0.7, 0, 255).astype(np.uint8))
    return img


def boot_logo(output, wordmark_cols=108, cw=7):
    """The wordmark alone in its dotted frame, drawn at the size Plymouth shows
    it (one image pixel per screen pixel), on the theme's black."""
    ch = cw * 2
    white, _ = wordmark_masks(wordmark_cols * cw)
    wm_rows = int(math.ceil(white.shape[0] / ch))
    grid = Grid(wordmark_cols + 10, wm_rows + 4)
    draw_frame(grid, inset_c=0, inset_r=0)
    draw_wordmark(grid, 2, wordmark_cols, cw, ch)
    img = render(grid, cw, ch, (grid.cols * cw, grid.rows * ch))
    img.save(output, optimize=True)
    print(f"Wrote {output} ({img.width}x{img.height})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("output", nargs="?")
    ap.add_argument("--width", type=int, default=3840)
    ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--boot-logo", action="store_true",
                    help="render only the framed wordmark, for unlock.png (default output: the theme's unlock.png)")
    args = ap.parse_args()

    if args.boot_logo:
        return boot_logo(args.output or os.path.join(THEME_DIR, "unlock.png"))
    args.output = args.output or os.path.expanduser("~/Pictures/thinkpad-ascii.png")

    size = (args.width, args.height)
    cw = round(args.width / 320)
    ch = cw * 2
    cols, rows = args.width // cw, args.height // ch
    grid = Grid(cols, rows)
    r0, r1, c0, c1 = draw_frame(grid, inset_c=24, inset_r=3)

    # Laptop in the upper part of the frame, seen from in front and a little
    # above; the wordmark sits under it.
    elevation, distance = math.radians(32), 150
    eye = (BASE_W / 2, 9 + distance * math.sin(elevation), 11 - distance * math.cos(elevation))
    camera = Camera(eye, (BASE_W / 2, 9, 11))
    corners = [deck(0, 0), deck(BASE_W, 0), (0, 0, 0), (BASE_W, 0, 0), lid(0, LID_H), lid(BASE_W, LID_H)]
    camera.fit(np.array(corners), (0, (r0 + 2) * ch, cols * cw, (r0 + 55) * ch))

    ids = build_fills(camera, (cols * cw, rows * ch))
    cover = cell_cover(ids, cw, ch, cols, rows)
    draw_laptop(grid, camera, cover, cw, ch)
    draw_screen(grid, cover[..., SCREEN] > 0.5)

    wm_rows = draw_wordmark(grid, r0 + 60, 104, cw, ch,
                            message=f"ThinkPad T14s Gen 6 // {getpass.getuser()} // think, then ship. ")

    caption = f"{getpass.getuser()}@thinkpad:~$ think --then-ship"
    grid.text(r1 - 2, c0 + 4, caption, gray(0x5A))
    grid.put(r1 - 2, c0 + 4 + len(caption), " ", gray(0), bg=RED)
    spec = "T14s Gen 6  ·  Ryzen AI 7 PRO 360  ·  Omarchy"
    grid.text(r1 - 2, c1 - 4 - len(spec), spec, gray(0x5A))

    img = render(grid, cw, ch, size)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    img.save(args.output, optimize=True)
    print(f"Wrote {args.output} ({size[0]}x{size[1]}, {cols}x{rows} cells, wordmark {wm_rows} rows)")


if __name__ == "__main__":
    sys.exit(main())
