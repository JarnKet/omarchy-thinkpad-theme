#!/usr/bin/env python3
"""Render the ThinkPad wallpapers into backgrounds/.

Every mark on every wallpaper is a character on one monospace grid: braille
dots for land, shading and fills, box-drawing for keys and traces, plain
text for labels. Black, white and grays, and one thing in TrackPoint red.

  world       the time-zone map: the world in braille, 24 hour columns, the
              date line in red, Yamato Lab's zone lit, the wordmark over it
  trackpoint  the red nub up close, among the keys around it
  hinge       a lid opening from closed to flat, every 30 degrees
  bento       the shokado bento box the design came from, one red plum
  keyboard    the keyboard from above, TrackPoint, buttons and touchpad
  orbit       a space station over the curve of the Earth, its orbit in red
  notepad     the pocket notepad with THINK on its cover, and a pencil
  exploded    the base taken apart into its six layers
  mountains   ridges going back into the haze, a red sun behind them
  setup       a setup utility, the TrackPoint row selected
  halftone    the wordmark's i blown up until the print shows
  drop        a closed laptop dropped from 122 cm, landing on a corner
  source      a wall of source code, the TrackPoint driver, in three columns

Nothing in them depends on who renders them or on what machine.

Usage: tools/make-ascii-wallpaper.py [name ...|all] [--out DIR] [--width 3840 --height 2160]
       tools/make-ascii-wallpaper.py --boot-logo [unlock.png]
"""

import argparse
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


# --- keys and lines ---------------------------------------------------------

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


def line2d(grid, p0, p1, cw, ch, color, bold=False, clip=None, vertical="|"):
    """Draw a segment between two pixel positions as characters, picked by its
    slope. clip(r, c) can veto cells."""
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = x1 - x0, y1 - y0

    def put(r, c, s):
        if clip is None or clip(r, c):
            grid.put(r, c, s, color, bold)

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
            put(r, c, "\u203e" if frac < 0.3 else ("-" if frac < 0.6 else "_"))
        return
    s = "\\" if angle < 72 else (vertical if angle < 108 else "/")
    for r in range(int(min(y0, y1) // ch), int(max(y0, y1) // ch) + 1):
        y = (r + 0.5) * ch
        t = 0 if dy == 0 else min(1, max(0, (y - y0) / dy))
        put(r, int((x0 + dx * t) // cw), s)


# --- syntax for the source wallpaper -----------------------------------------

GO_KEYWORDS = {"package", "import", "type", "struct", "func", "for", "range", "if", "return",
               "continue", "nil", "err", "string", "int", "var", "const", "switch", "case", "default",
               "map", "bool", "float64", "const"}


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


def wordmark_cover(width_cols, cw, ch):
    """Per-cell coverage of the wordmark's white letters and its red dot."""
    white, red = wordmark_masks(width_cols * cw)
    rows = int(math.ceil(white.shape[0] / ch))
    pad_h = rows * ch - white.shape[0]
    white = np.pad(white, ((0, pad_h), (0, 0)))
    red = np.pad(red, ((0, pad_h), (0, 0)))
    return (white.reshape(rows, ch, width_cols, cw).mean((1, 3)),
            red.reshape(rows, ch, width_cols, cw).mean((1, 3)))


def draw_wordmark(grid, top_row, width_cols, cw, ch, message="ThinkPad // think, then ship. "):
    w_cov, r_cov = wordmark_cover(width_cols, cw, ch)
    rows = w_cov.shape[0]
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


# --- the world map variant ---------------------------------------------------
#
# After the ThinkPad time-zone wallpaper: the world in braille dots (the dots
# btop draws its graphs with), cut into 24 hour columns, the date line in
# TrackPoint red and one zone lit, with the wordmark over it all.

# Natural Earth 1:50m land minus the larger lakes (public domain), rasterized
# to an equirectangular 1-bit image, 10 pixels per degree.
LAND_MASK = os.path.join(THEME_DIR, "tools", "world-land.png")

# NATO time-zone letters by UTC offset; J is skipped.
ZONE_LETTERS = {0: "Z", **{h: "ABCDEFGHIKLM"[h - 1] for h in range(1, 13)},
                **{-h: "NOPQRSTUVWXY"[h - 1] for h in range(1, 13)}}

# The International Date Line, simplified, north to south (lat, lon): east of
# Chukotka, west of the Aleutians, around Kiribati's Line Islands, between the
# two Samoas, east of Tonga.
DATE_LINE = [(90, 180), (75, 180), (67.5, -168.75), (65.5, -169), (52.5, 170), (48, 180),
             (5.3, 180), (5.3, -150), (-11.5, -150), (-11.5, -169), (-15, -171.5),
             (-45, -172.5), (-51.2, 180), (-90, 180)]

# Where ThinkPads come from: label, lat, lon, which side the label goes.
LANDMARKS = [
    ("ARMONK", 41.13, -73.71, "right"),    # IBM
    ("RALEIGH", 35.78, -78.64, "left"),    # IBM PC division, then Lenovo
    ("BEIJING", 39.90, 116.40, "right"),   # Lenovo
    ("YAMATO LAB", 35.46, 139.63, "right"),  # where ThinkPads are designed
]

LAND, LAND_LIT = 0x30, 0x68

BRAILLE_BITS = np.array([[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]])


def miller(lat):
    return 1.25 * np.log(np.tan(np.pi / 4 + 0.4 * np.radians(np.clip(lat, -89, 89))))


def miller_inv(y):
    return np.degrees(2.5 * np.arctan(np.exp(0.8 * y)) - 0.625 * np.pi)


class WorldMap:
    """The whole world in the Miller projection, 360 degrees east of `west`,
    laid on the grid from cell (r0, c0)."""

    def __init__(self, r0, c0, cols, west, cw, ch, north=84, south=-57):
        self.r0, self.c0, self.cols, self.west = r0, c0, cols, west
        self.cw, self.ch = cw, ch
        self.ppr = cols * cw / (2 * math.pi)  # pixels per radian
        self.top = miller(north)
        self.rows = int(round((self.top - miller(south)) * self.ppr / ch))

    def x(self, lon):
        """Pixel x of a longitude, unwrapped: west + 360 is the right edge."""
        return self.c0 * self.cw + math.radians(lon - self.west) * self.ppr

    def y(self, lat):
        return self.r0 * self.ch + (self.top - miller(lat)) * self.ppr

    def cell(self, lat, lon):
        x = self.x((lon - self.west) % 360 + self.west)
        return int(self.y(lat) // self.ch), int(x // self.cw)

    def inside(self, r, c):
        return self.r0 <= r < self.r0 + self.rows and self.c0 <= c < self.c0 + self.cols

    def land(self, oversample=3):
        """Braille land cells: a (rows, cols) array of dot bits, 2x4 per cell."""
        mask = np.asarray(Image.open(LAND_MASK).convert("L")) > 127
        ppd = mask.shape[1] / 360
        n = oversample
        xs = (np.arange(self.cols * 2 * n) + 0.5) / n * (self.cw / 2)
        ys = (np.arange(self.rows * 4 * n) + 0.5) / n * (self.ch / 4)
        lon = self.west + np.degrees(xs / self.ppr)
        lat = miller_inv(self.top - ys / self.ppr)
        mc = ((lon + 180) % 360 * ppd).astype(int)
        mr = np.clip(((90 - lat) * ppd).astype(int), 0, mask.shape[0] - 1)
        cover = mask[mr][:, mc].reshape(self.rows * 4, n, self.cols * 2, n).mean((1, 3))
        dots = (cover >= 0.5).reshape(self.rows, 4, self.cols, 2)
        return np.einsum("ayxb,yb->ax", dots, BRAILLE_BITS)

    def trace(self, points, grid, color, bold=False):
        """Draw a lat/lon polyline as a circuit trace of box-drawing characters
        with rounded corners, wrapping it around the map's edges."""
        lons = [(points[0][1] - self.west) % 360 + self.west]
        for (_, a), (_, b) in zip(points, points[1:]):
            lons.append(lons[-1] + (b - a + 180) % 360 - 180)  # the short way round
        for shift in (-360, 0, 360):
            px = [(self.x(lon + shift), self.y(lat)) for (lat, _), lon in zip(points, lons)]
            cells = trace_cells(px, self.cw, self.ch)
            for i, (r, c) in enumerate(cells):
                if self.inside(r, c):
                    grid.put(r, c, trace_char(cells, i), color, bold)


TRACE_CHARS = {frozenset("UD"): "│", frozenset("LR"): "─", frozenset("DR"): "╭",
               frozenset("DL"): "╮", frozenset("UR"): "╰", frozenset("UL"): "╯",
               frozenset("U"): "│", frozenset("D"): "│", frozenset("L"): "─", frozenset("R"): "─"}


def trace_cells(px, cw, ch):
    """The 4-connected run of cells a pixel polyline passes through."""
    cells = []
    for (x0, y0), (x1, y1) in zip(px, px[1:]):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        seg = np.array([x1 - x0, y1 - y0])

        def miss(cell):
            # Distance from the cell's centre to the segment.
            p = np.array([(cell[1] + 0.5) * cw - x0, (cell[0] + 0.5) * ch - y0])
            t = min(1, max(0, p @ seg / max(1e-9, seg @ seg)))
            return np.hypot(*(p - t * seg))

        for i in range(n + 1):
            cell = (int((y0 + (y1 - y0) * i / n) // ch), int((x0 + (x1 - x0) * i / n) // cw))
            if cells and cell == cells[-1]:
                continue
            if cells and cell[0] != cells[-1][0] and cell[1] != cells[-1][1]:
                # A diagonal step: turn through whichever neighbour the line passes closer to.
                across, down = (cells[-1][0], cell[1]), (cell[0], cells[-1][1])
                cells.append(across if miss(across) <= miss(down) else down)
            cells.append(cell)
    return cells


def trace_char(cells, i):
    sides = set()
    for j in (i - 1, i + 1):
        if 0 <= j < len(cells):
            dr, dc = cells[j][0] - cells[i][0], cells[j][1] - cells[i][1]
            sides.add({(-1, 0): "U", (1, 0): "D", (0, -1): "L", (0, 1): "R"}.get((dr, dc), "?"))
    return TRACE_CHARS.get(frozenset(sides), "┼")


def utc_label(offset):
    sign = "+" if offset >= 0 else "-"
    h, m = divmod(round(abs(offset) * 60), 60)
    return f"UTC{sign}{h:02d}:{m:02d}"


def dilate(mask, dr, dc):
    out = mask.copy()
    rows, cols = mask.shape
    for r in range(-dr, dr + 1):
        for c in range(-dc, dc + 1):
            out[max(0, r):rows + min(0, r), max(0, c):cols + min(0, c)] |= \
                mask[max(0, -r):rows + min(0, -r), max(0, -c):cols + min(0, -c)]
    return out


def draw_world(grid, m, zone_cols, offset, home, keep_clear):
    """The map with its hour columns, date line, landmarks and the local zone.
    Landmarks that would land in keep_clear (rows x cols bool) are left out."""
    zones = m.cols // zone_cols
    local = math.floor(offset + 0.5)
    rows = range(m.r0, m.r0 + m.rows)

    def zone_offset(k):
        """UTC offset of the k-th column from the left (-12..12)."""
        centre = m.west + (k + 0.5) * 15
        return int(round(((centre + 180) % 360 - 180) / 15))

    local_k = next((k for k in range(zones) if zone_offset(k) == local), None)
    if local_k is not None:
        # The local column, lit a little, from its letter down to its hour.
        lc0 = m.c0 + local_k * zone_cols + 1
        grid.bg[m.r0 - 2:m.r0 + m.rows + 2, lc0:lc0 + zone_cols - 1] = (14, 14, 14)

    # Land, in braille dots, with a seam where each hour column starts.
    bits = m.land()
    for k in range(zones + 1):
        c = k * zone_cols
        if c < m.cols:
            bits[:, c] &= ~(0x01 | 0x02 | 0x04 | 0x40)
    for r in rows:
        for c in range(m.cols):
            if bits[r - m.r0, c]:
                lit = local_k is not None and c // zone_cols == local_k
                grid.put(r, m.c0 + c, chr(0x2800 + bits[r - m.r0, c]), gray(LAND_LIT if lit else LAND))

    def sea(r, c):
        return m.inside(r, c) and grid.ch[r, c] == " "

    # Graticule every 30 degrees of latitude, over the sea only.
    for lat in (60, 30, 0, -30):
        y = m.y(lat)
        line2d(grid, (m.c0 * m.cw, y), ((m.c0 + m.cols) * m.cw - 1, y), m.cw, m.ch,
               gray(0x34 if lat == 0 else 0x24), clip=lambda r, c: sea(r, c) and c % 2 == 0)

    # Hour columns.
    for k in range(zones + 1):
        c = m.c0 + k * zone_cols
        for r in rows:
            if grid.ch[r, c] == " " or c == m.c0 + m.cols:
                grid.put(r, c, "│", gray(0x26))

    # The date line, in red, over everything on the map.
    m.trace(DATE_LINE, grid, RED, True)

    # Compass rose in the empty South Pacific.
    r, c = m.cell(-38, -128)
    for dr, row in enumerate(["    N    ", "    |    ", "W --+-- E", "    |    ", "    S    "]):
        for dc, s in enumerate(row):
            if s != " ":
                grid.put(r - 2 + dr, c - 4 + dc, s, gray(0xE4) if s == "N" else gray(0x5A), s == "N")

    # Landmarks, and the red dot where the system clock says we are.
    marks = [(label, lat, lon, side, "+", gray(0xD8), gray(0x8A)) for label, lat, lon, side in LANDMARKS]
    if home:
        marks.append(("YOU ARE HERE", home[0], home[1], "right", "●", BRIGHT_RED, gray(0xE4)))
    for label, lat, lon, side, mark, mark_color, label_color in marks:
        r, c = m.cell(lat, lon)
        text = f" {label} "
        start = c + 1 if side == "right" else c - len(text)
        if keep_clear[r, min(c, start):max(c + 1, start + len(text))].any():
            continue
        grid.put(r, c, mark, mark_color, True)
        grid.text(r, start, text, label_color, mark == "●")

    # Letters above the columns, hours below as the original has them (the
    # hour in each zone when it is noon in Greenwich), GMT and the date line
    # marked under theirs.
    letters_row, hours_row = m.r0 - 2, m.r0 + m.rows + 1
    for k in range(zones):
        z = zone_offset(k)
        c = m.c0 + k * zone_cols + zone_cols // 2
        is_local = k == local_k
        letter = ZONE_LETTERS[z] if abs(z) < 12 else "M Y"  # either side of the date line
        grid.text(letters_row, c - len(letter) // 2, letter, RED if is_local else gray(0x5A), is_local)
        hour = f"{(12 + z) % 24 or 24:02d}h"
        if is_local:
            grid.text(hours_row, c - 2, f" {hour} ", gray(0xFF), True, bg=RED)
            grid.text(hours_row + 1, c - len(utc_label(offset)) // 2, utc_label(offset), BRIGHT_RED)
        else:
            grid.text(hours_row, c - 1, hour, gray(0xC8) if z == 0 else gray(0x7A), z == 0)
        if z == 0:
            c0 = m.c0 + k * zone_cols + 1
            grid.text(hours_row + 1, c0, "─" * (zone_cols - 5) + " GMT", gray(0xB0))
    idl_c = m.cell(0, 180)[1]
    grid.text(hours_row + 1, idl_c - 4, "IDL ─", RED, True)


def draw_lat_lon_labels(grid, m, frame):
    """Graticule labels outside the frame, with ticks on it, like a printed map."""
    r0, r1, c0, c1 = frame
    tick = gray(0x70)
    for lat in (60, 30, 0, -30):
        r = int(m.y(lat) // m.ch)
        label = "0°" if lat == 0 else f"{abs(lat)}°{'N' if lat > 0 else 'S'}"
        grid.text(r, c0 - 1 - len(label), label, gray(0x5A))
        grid.text(r, c1 + 2, label, gray(0x5A))
        grid.put(r, c0, "+", tick)
        grid.put(r, c1, "+", tick)
    for lon in range(-150, 181, 30):
        c = int(m.x((lon - m.west) % 360 + m.west) // m.cw)
        label = "0°" if lon == 0 else (f"{abs(lon)}°" if abs(lon) == 180 else f"{abs(lon)}°{'E' if lon > 0 else 'W'}")
        grid.text(r0 - 1, c - len(label) // 2, label, gray(0x5A))
        grid.put(r0, c, "+", tick)


# --- output ------------------------------------------------------------------

def render(grid, cw, ch, size, glow=True):
    img = Image.new("RGB", size, BG)
    draw_grid(img, grid, cw, ch, ((size[0] - grid.cols * cw) // 2, (size[1] - grid.rows * ch) // 2))
    return add_glow(img, cw) if glow else img


def draw_grid(img, grid, cw, ch, origin):
    """Draw a grid's cells onto img with its top-left cell at origin (pixels)."""
    draw = ImageDraw.Draw(img)
    regular = ImageFont.truetype(FONT_REGULAR, int(cw / 0.6))
    bold = ImageFont.truetype(FONT_BOLD, int(cw / 0.6))
    ox, oy = origin

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


def add_glow(img, cw):
    """A soft halo around everything lit, like the reference renders' dots."""
    arr = np.asarray(img, np.float32)
    lit = np.clip(arr - np.array(BG, np.float32), 0, 255).astype(np.uint8)
    halo = np.asarray(Image.fromarray(lit).filter(ImageFilter.GaussianBlur(cw * 0.8)), np.float32)
    return Image.fromarray(np.clip(arr + halo * 0.7, 0, 255).astype(np.uint8))


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


# --- shared by the sheets ----------------------------------------------------
#
# Every wallpaper is drawn on the world map's grid: cw = width / 320 pixels a
# cell, twice as tall, the dotted frame inset 24 columns and 3 rows, a prompt
# in the bottom-left corner and a short spec in the bottom-right. Shapes are
# drawn on a canvas of braille dots (2 x 4 a cell, so one dot every cw / 2
# pixels both ways) and laid on the grid one color at a time.

BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) + 0.5) / 16


def sheet(width, height):
    cw = round(width / 320)
    ch = cw * 2
    grid = Grid(width // cw, height // ch)
    frame = draw_frame(grid, inset_c=24, inset_r=3)
    return grid, frame, cw, ch


def caption(grid, frame, verb, spec):
    """The prompt bottom-left with its red cursor, and the spec bottom-right."""
    r0, r1, c0, c1 = frame
    prompt = f"$ think --{verb}"
    grid.text(r1 - 2, c0 + 4, prompt, gray(0x5A))
    grid.put(r1 - 2, c0 + 4 + len(prompt), " ", gray(0), bg=RED)
    grid.text(r1 - 2, c1 - 4 - len(spec), spec, gray(0x5A))


def shade(color, k):
    return tuple(int(max(0, min(255, v * k))) for v in color)


class Dots:
    """A canvas of braille dots the size of the grid, drawn on with PIL in
    screen pixels. Values are tones, 0..255."""

    def __init__(self, grid, cw):
        self.grid, self.pitch = grid, cw / 2
        self.img = Image.new("L", (grid.cols * 2, grid.rows * 4), 0)
        self.d = ImageDraw.Draw(self.img)

    def P(self, pts):
        return [(x / self.pitch, y / self.pitch) for x, y in pts]

    def polygon(self, pts, tone=255):
        self.d.polygon(self.P(pts), fill=tone)

    def line(self, pts, tone=255, width=1):
        self.d.line(self.P(pts), fill=tone, width=width, joint="curve")

    def outline(self, pts, tone=255, width=1):
        self.line(list(pts) + [pts[0], pts[1]], tone, width)

    def ellipse(self, cx, cy, rx, ry=None, tone=255, width=0):
        ry = rx if ry is None else ry
        box = [(cx - rx) / self.pitch, (cy - ry) / self.pitch, (cx + rx) / self.pitch, (cy + ry) / self.pitch]
        if width:
            self.d.ellipse(box, outline=tone, width=width)
        else:
            self.d.ellipse(box, fill=tone)

    def text(self, x, y, s, size, tone=255, bold=True, anchor="mm"):
        font = ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, max(1, round(size / self.pitch)))
        self.d.text((x / self.pitch, y / self.pitch), s, font=font, fill=tone, anchor=anchor)

    def tone(self):
        return np.asarray(self.img, np.float32) / 255

    def coords(self):
        """Screen x and y of every dot's centre."""
        h, w = self.img.size[1], self.img.size[0]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        return (xx + 0.5) * self.pitch, (yy + 0.5) * self.pitch


def dither(tone):
    """Tones 0..1 a dot to dots, by a 4x4 ordered dither."""
    h, w = tone.shape
    return tone > np.tile(BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]


def halftone(tone, cell=6.0, angle=45.0):
    """Tones 0..1 a dot to clustered halftone dots on a screen `cell` dots
    apart, turned by `angle`, as on a printed page."""
    h, w = tone.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    a = math.radians(angle)
    u = (xx * math.cos(a) + yy * math.sin(a)) / cell
    v = (-xx * math.sin(a) + yy * math.cos(a)) / cell
    return np.hypot(u - np.round(u), v - np.round(v)) < np.sqrt(np.clip(tone, 0, 1) / math.pi) * 1.05


def cell_mean(a, grid):
    return a.reshape(grid.rows, 4, grid.cols, 2).mean((1, 3))


def erode(m, n):
    """Shrink a dot mask by n dots."""
    out = m.copy()
    for _ in range(n):
        e = out.copy()
        e[1:] &= out[:-1]
        e[:-1] &= out[1:]
        e[:, 1:] &= out[:, :-1]
        e[:, :-1] &= out[:, 1:]
        out = e
    return out


def chaikin(pts, n=3):
    """Round off a closed polygon by corner cutting."""
    for _ in range(n):
        out = []
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            out += [(0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by), (0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by)]
        pts = out
    return pts


def red_ball(grid, frame, cw, cx, cy, r, texture=False):
    """A TrackPoint-red dome lit from the top left; with texture, the
    soft-dome cap's grid of little pits."""
    x, y = Dots(grid, cw).coords()
    dx, dy = (x - cx) / r, (y - cy) / r
    rr = dx * dx + dy * dy
    light = np.array([-0.45, -0.55, 0.70])
    light /= np.linalg.norm(light)
    lam = np.clip(dx * light[0] + dy * light[1] + np.sqrt(np.clip(1 - rr, 0, 1)) * light[2], 0, 1)
    tone = np.where(rr < 1, 0.22 + 0.78 * lam ** 1.3, 0)
    if texture:
        ii, jj = np.mgrid[0:x.shape[0], 0:x.shape[1]]
        tone = np.where((ii % 3 == 1) & (jj % 3 == 1) & (rr < 0.75), tone * 0.3, tone)
    k = cell_mean(tone, grid)
    colors = np.zeros((grid.rows, grid.cols, 3))
    colors[:] = RED
    colors *= (0.45 + 0.7 * k)[..., None]
    colors[k > 0.85] = BRIGHT_RED
    put_dots(grid, dither(tone) & safe(grid, frame), np.clip(colors, 0, 255))


def put_dots(grid, on, color, over=True):
    """Lay a (rows*4, cols*2) array of dots on the grid as braille, in one
    color or a color a cell ((rows, cols, 3) array). Cells holding a
    character already are left alone unless over is set."""
    bits = np.einsum("ayxb,yb->ax", on.reshape(grid.rows, 4, grid.cols, 2).astype(np.int64), BRAILLE_BITS)
    per_cell = np.ndim(color) == 3
    for r, c in zip(*np.nonzero(bits)):
        if not over and grid.ch[r, c] != " ":
            continue
        grid.put(r, c, chr(0x2800 + int(bits[r, c])), tuple(int(v) for v in color[r, c]) if per_cell else color)


def safe(grid, frame):
    """A dot mask of the frame's inside, above the caption row."""
    r0, r1, c0, c1 = frame
    m = np.zeros((grid.rows * 4, grid.cols * 2), bool)
    m[(r0 + 1) * 4:(r1 - 3) * 4, (c0 + 2) * 2:(c1 - 1) * 2] = True
    return m


def lay(grid, frame, canvas, color, over=True, mode="dither"):
    """Dither (or halftone) a canvas and lay it on the grid inside the frame."""
    t = canvas.tone() if isinstance(canvas, Dots) else canvas
    on = (dither(t) if mode == "dither" else halftone(t) if mode == "halftone" else t > 0.5) & safe(grid, frame)
    put_dots(grid, on, color, over)


def box(grid, r0, c0, r1, c1, color, style="round", bold=False):
    h, v, tl, tr, bl, br = {"round": "─│╭╮╰╯", "square": "─│┌┐└┘", "double": "═║╔╗╚╝"}[style]
    for c in range(c0 + 1, c1):
        grid.put(r0, c, h, color, bold)
        grid.put(r1, c, h, color, bold)
    for r in range(r0 + 1, r1):
        grid.put(r, c0, v, color, bold)
        grid.put(r, c1, v, color, bold)
    for r, c, s in ((r0, c0, tl), (r0, c1, tr), (r1, c0, bl), (r1, c1, br)):
        grid.put(r, c, s, color, bold)


def rotate(pts, cx, cy, a):
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * ca - (y - cy) * sa, cy + (x - cx) * sa + (y - cy) * ca) for x, y in pts]


def rounded_rect(x0, y0, x1, y1, r, n=6):
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        pts += [(cx + r * math.cos(math.radians(a0 + 90 * i / n)), cy + r * math.sin(math.radians(a0 + 90 * i / n)))
                for i in range(n + 1)]
    return pts


def dimension(grid, r, c0, c1, label, color=None):
    """├──── label ────┤ across columns c0..c1 on row r."""
    color = color or gray(0x8A)
    for c in range(c0 + 1, c1):
        grid.put(r, c, "─", color)
    grid.put(r, c0, "├", color)
    grid.put(r, c1, "┤", color)
    text = f" {label} "
    grid.text(r, (c0 + c1 - len(text)) // 2 + 1, text, gray(0xC8))


def finish(grid, cw, ch, width, height):
    return render(grid, cw, ch, (width, height))


# --- 01 world ----------------------------------------------------------------

def render_world(width, height, centre=37.5):
    """The world map, lit at Yamato Lab's zone, UTC+09."""
    # Hour columns line up with the map's edges when the centre is a column
    # boundary: 7.5 degrees off a multiple of 15.
    west = 7.5 + 15 * round((centre - 7.5) / 15) - 180
    grid, frame, cw, ch = sheet(width, height)
    cols, rows = grid.cols, grid.rows
    offset = 9
    r0, r1, c0, c1 = frame

    # 24 hour columns of `zone_cols` cells each, centred in the frame; the map
    # and its labels centred between the frame top and the caption.
    zone_cols = 11
    m = WorldMap(0, (cols - 24 * zone_cols) // 2, 24 * zone_cols, west, cw, ch)
    block = m.rows + 6
    m.r0 = r0 + 1 + max(2, (r1 - 3 - r0 - block) // 2) + 2

    # The wordmark in the middle of the screen, the tagline under it, with the
    # map cleared around their shapes.
    width_cols = 100
    w_cov, r_cov = wordmark_cover(width_cols, cw, ch)
    wm_rows = w_cov.shape[0]
    tagline = "New World.  New Thinking."
    top = (rows - wm_rows - 2) // 2
    left = (cols - width_cols) // 2
    knock = np.zeros((rows, cols), bool)
    shape = (w_cov > 0.18) | (r_cov > 0.3)
    for r in range(wm_rows):
        # Each row cleared from its first lit cell to its last, so no map
        # shows between the letters.
        lit = np.where(shape[r])[0]
        if len(lit):
            knock[top + r, left + lit[0]:left + lit[-1] + 1] = True
    t0 = (cols - len(tagline)) // 2
    knock[top + wm_rows + 1, t0:t0 + len(tagline)] = True
    knock = dilate(knock, 1, 3)
    draw_world(grid, m, zone_cols, offset, None, knock)
    draw_lat_lon_labels(grid, m, frame)
    grid.ch[knock] = " "
    grid.bg[knock] = -1
    draw_wordmark(grid, top, width_cols, cw, ch, message="ThinkPad // new world, new thinking. ")
    grid.text(top + wm_rows + 1, t0, tagline, gray(0x9A))
    caption(grid, frame, "globally", f"24 ZONES  ·  {utc_label(offset)}  ·  YAMATO")
    return finish(grid, cw, ch, width, height)


# --- 02 trackpoint -----------------------------------------------------------

def render_trackpoint(width, height):
    """The nub up close, between the keys it lives among."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    cx, cy, u = width / 2, 1080 * s, 1180 * s        # the nub; u is the 19 mm key pitch
    half, rnd, nub = 0.408 * u, 0.05 * u, 0.2 * u
    keys = {"F": (-1.5, -0.5), "G": (-0.5, -0.5), "H": (0.5, -0.5), "J": (1.5, -0.5),
            "V": (-1.0, 0.5), "B": (0.0, 0.5), "N": (1.0, 0.5)}

    # The keycaps, G, H and B cut back in a curve around the nub: a matte,
    # slightly dished top, the edge, the rim of the dish, the legend.
    x, y = Dots(grid, cw).coords()
    well = (x - cx) ** 2 + (y - cy) ** 2 < (nub + 0.045 * u) ** 2
    caps = np.zeros(x.shape, bool)
    dish = np.zeros(x.shape, np.float32)
    legend = Dots(grid, cw)
    for k, (kx, ky) in keys.items():
        kx, ky = cx + kx * u, cy + ky * u
        key = Dots(grid, cw)
        key.polygon(rounded_rect(kx - half, ky - half, kx + half, ky + half, rnd))
        m = (key.tone() > 0.5) & ~well
        caps |= m
        d2 = ((x - kx) ** 2 + (y - ky) ** 2) / half ** 2
        dish = np.where(m, 0.04 + 0.11 * np.clip(d2, 0, 1), dish)
        legend.text(kx - 0.13 * u, ky - 0.11 * u, k, 0.3 * u)
    lay(grid, frame, dish, gray(0x3A))
    put_dots(grid, erode(caps, 10) & ~erode(caps, 11) & safe(grid, frame), gray(0x34))
    put_dots(grid, caps & ~erode(caps, 2) & safe(grid, frame), gray(0x9A))
    lay(grid, frame, legend, gray(0xEC), mode="solid")

    # The nub's shadow to the bottom right, then the nub.
    dx, dy = (x - cx) / nub, (y - cy) / nub
    sx, sy = (x - cx - 0.035 * u) / nub, (y - cy - 0.045 * u) / nub
    lay(grid, frame, np.where((sx * sx + sy * sy < 1.1) & (dx * dx + dy * dy >= 1), 0.35, 0), gray(0x26))
    red_ball(grid, frame, cw, cx, cy, nub, texture=True)

    # Measurements in the gap between the rows of keys.
    r = int(cy // ch)
    dimension(grid, r, int((cx - 1.5 * u) // cw), int((cx - 0.5 * u) // cw), "19.0 mm")
    c = int((cx + nub + 24 * s) // cw)
    for cc in range(c, c + 14):
        grid.put(r, cc, "─", gray(0x8A))
    grid.put(r, c - 1, "●", gray(0x8A))
    grid.text(r, c + 15, "Ø 7.6 mm  SOFT DOME", gray(0xC8))
    caption(grid, frame, "pointing", "TRACKPOINT  ·  SOFT DOME  ·  19.0 mm PITCH")
    return finish(grid, cw, ch, width, height)


# --- 03 hinge ----------------------------------------------------------------

def render_hinge(width, height):
    """A lid opening from closed to flat, every 30 degrees."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    hx, hy = width / 2 + 40 * s, 1580 * s
    L, th, base_th = 1240 * s, 28 * s, 56 * s

    def lid(a):
        r = math.radians(a)
        d = (-math.cos(r), -math.sin(r))
        n = (-d[1], d[0])
        p1 = (hx + d[0] * L, hy + d[1] * L)
        return [(hx, hy), p1, (p1[0] + n[0] * th, p1[1] + n[1] * th), (hx + n[0] * th, hy + n[1] * th)]

    def on_arc(a, rad):
        r = math.radians(a)
        return hx - math.cos(r) * rad, hy - math.sin(r) * rad

    # The angle scale: a dotted arc, ticks every 10 degrees, labels every 30.
    R = L + 90 * s
    scale = Dots(grid, cw)
    for i in range(0, 361):
        if i % 4 < 2:
            scale.line([on_arc(i / 2, R), on_arc((i + 1) / 2, R)])
    for a in range(0, 181, 10):
        t = 26 * s if a % 30 == 0 else 12 * s
        scale.line([on_arc(a, R - t), on_arc(a, R + t)])
    lay(grid, frame, scale, gray(0x4E), mode="solid")
    for a in range(0, 181, 30):
        lx, ly = on_arc(a, R + 70 * s)
        label = f"{a}°"
        grid.text(int(ly // ch), int(lx // cw) - len(label) // 2, label, RED if a == 180 else gray(0x8A), a == 180)

    # The lid at each angle, fading in; flat in red.
    for i, a in enumerate(range(0, 151, 30)):
        fill, d = Dots(grid, cw), Dots(grid, cw)
        fill.polygon(lid(a), 46)
        d.outline(lid(a))
        lay(grid, frame, fill, gray(0x34 + 0x16 * i))
        lay(grid, frame, d, gray(0x34 + 0x16 * i), mode="solid")
    flat = Dots(grid, cw)
    flat.polygon(lid(180), 150)
    flat.outline(lid(180), 255, 2)
    lay(grid, frame, flat, RED)

    # The base, and the hinge axis.
    base = Dots(grid, cw)
    pts = rounded_rect(hx - L - 16 * s, hy, hx + 12 * s, hy + base_th, 12 * s)
    base.polygon(pts, 140)
    base.outline(pts, 255, 2)
    for fx in (hx - L * 0.9, hx - L * 0.12):           # rubber feet
        base.ellipse(fx, hy + base_th + 2 * s, 48 * s, 10 * s, 255)
    for k in range(3):                                # ports on the side
        px0 = hx - L * 0.82 + k * 76 * s
        port = rounded_rect(px0, hy + 17 * s, px0 + 50 * s, hy + 37 * s, 9 * s)
        base.polygon(port, 0)
        base.outline(port, 255)
    lay(grid, frame, base, gray(0xC8))
    axis = Dots(grid, cw)
    axis.ellipse(hx, hy, 24 * s, width=2)
    axis.ellipse(hx, hy, 5 * s)
    lay(grid, frame, axis, gray(0xF0), mode="solid")

    # The desk.
    r = int((hy + base_th + 18 * s) // ch) + 1
    r0, r1, c0, c1 = frame
    for c in range(c0 + 6, c1 - 5):
        grid.put(r, c, "─", gray(0x44))
        if c % 2 == 0:
            grid.put(r + 1, c, "╱", gray(0x26))
    grid.text(int((hy - 56 * s) // ch), int((hx - L * 0.5) // cw) - 3, "CLOSED", gray(0x6A))
    grid.text(int((hy + 70 * s) // ch), int((hx + L * 0.5) // cw) - 3, "FLAT", BRIGHT_RED, True)
    caption(grid, frame, "flat-out", "HINGE  ·  0–180°  ·  ONE HAND")
    return finish(grid, cw, ch, width, height)


# --- 04 bento ----------------------------------------------------------------

def render_bento(width, height):
    """A shokado bento box from above, the box the design came from, with a
    single red plum on the rice."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    rng = np.random.default_rng(3)
    r0, r1 = 9, 77
    c0 = 44
    c1 = c0 + round((r1 - r0) * ch / cw)
    rm, cm = (r0 + r1) // 2, (c0 + c1) // 2

    # The lid, set aside on the right, and the chopsticks on their rest.
    lr0, lr1, lc0 = 22, 64, 196
    lc1 = lc0 + round((lr1 - lr0) * ch / cw)
    box(grid, lr0, lc0, lr1, lc1, gray(0x7A), "double")
    box(grid, lr0 + 2, lc0 + 4, lr1 - 2, lc1 - 4, gray(0x3A), "square")
    sheen = Dots(grid, cw)
    for k in range(5):
        x0 = (lc0 + 10 + k * 7) * cw
        sheen.line([(x0, (lr1 - 3) * ch), (x0 + 420 * s, (lr0 + 3) * ch)], 255)
    lay(grid, frame, sheen, gray(0x24), mode="solid", over=False)
    sticks = Dots(grid, cw)
    for off in (0, 44 * s):
        a, b = (2330 * s, 1830 * s + off), (3420 * s, 1640 * s + off)
        n = (0.17, 0.98)
        sticks.polygon([(a[0] - n[0] * 8 * s, a[1] - n[1] * 8 * s), (b[0] - n[0] * 14 * s, b[1] - n[1] * 14 * s),
                        (b[0] + n[0] * 14 * s, b[1] + n[1] * 14 * s), (a[0] + n[0] * 8 * s, a[1] + n[1] * 8 * s)])
    sticks.ellipse(3160 * s, 1720 * s, 60 * s, 22 * s, 120)
    lay(grid, frame, sticks, gray(0xA8))

    # The box: a lacquered rim, a cross dividing it into four, an inner wall
    # around each compartment.
    box(grid, r0, c0, r1, c1, gray(0xC8), "double", True)
    for c in range(c0 + 1, c1):
        grid.put(rm, c, "═", gray(0xC8), True)
    for r in range(r0 + 1, r1):
        grid.put(r, cm, "║", gray(0xC8), True)
    for r, c, sym in ((rm, cm, "╬"), (rm, c0, "╠"), (rm, c1, "╣"), (r0, cm, "╦"), (r1, cm, "╩")):
        grid.put(r, c, sym, gray(0xC8), True)
    rooms = [(r0, c0, rm, cm), (r0, cm, rm, c1), (rm, c0, r1, cm), (rm, cm, r1, c1)]
    for a, b, c, d in rooms:
        box(grid, a + 1, b + 2, c - 1, d - 2, gray(0x3A), "square")

    def room(i, inset=40 * s):
        a, b, c, d = rooms[i]
        return (b + 3) * cw + inset, (a + 2) * ch + inset, (d - 2) * cw - inset, (c - 1) * ch - inset

    # Rice in a soft mound, a few sesame seeds, and the plum.
    x0, y0, x1, y1 = room(0, 14 * s)
    rice = Dots(grid, cw)
    rice.polygon(rounded_rect(x0, y0, x1, y1, 70 * s))
    X, Y = rice.coords()
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    mound = 1 - 0.45 * (((X - mx) / ((x1 - x0) / 2)) ** 2 + ((Y - my) / ((y1 - y0) / 2)) ** 2)
    px, py, pr = mx, my, 92 * s
    for _ in range(40):
        rice.ellipse(rng.uniform(x0, x1), rng.uniform(y0, y1), 6 * s, 3.5 * s, 0)
    rice.ellipse(px, py, pr + 14 * s, tone=0)
    tone = rice.tone() * np.clip(0.82 * mound + 0.3 * (rng.random(X.shape) - 0.5), 0, 1)
    lay(grid, frame, tone, gray(0xD8))
    plum = Dots(grid, cw)
    x, y = plum.coords()
    dx, dy = (x - px) / pr, (y - py) / pr
    rr = dx * dx + dy * dy
    wrinkle = 0.75 + 0.25 * np.sin(9 * np.arctan2(dy, dx) + 6 * np.sqrt(rr))
    tone = np.where(rr < 1, (0.35 + 0.65 * np.clip(-0.5 * dx - 0.6 * dy + 0.62 * np.sqrt(np.clip(1 - rr, 0, 1)), 0, 1)) * wrinkle, 0)
    k = cell_mean(tone, grid)
    colors = np.zeros((grid.rows, grid.cols, 3))
    colors[:] = RED
    colors *= (0.5 + 0.7 * k)[..., None]
    put_dots(grid, dither(tone) & safe(grid, frame), np.clip(colors, 0, 255))

    # Two pieces of grilled salmon, their fat lines and grill marks; lemon.
    x0, y0, x1, y1 = room(1)
    fish, fat, skin = Dots(grid, cw), Dots(grid, cw), Dots(grid, cw)
    for k_ in range(2):
        fx0, fy0 = x0 + 10 * s + k_ * 330 * s, y0 + 20 * s + k_ * 46 * s
        corners = [(fx0, fy0 + 40 * s), (fx0 + 300 * s, fy0), (fx0 + 318 * s, fy0 + 250 * s), (fx0 + 18 * s, fy0 + 290 * s)]
        fish.polygon(chaikin(corners), 150)
        skin.line([corners[0], corners[1]], 255, 2)
        for t in (0.25, 0.5, 0.75):
            a_ = (corners[0][0] + (corners[3][0] - corners[0][0]) * t, corners[0][1] + (corners[3][1] - corners[0][1]) * t)
            b_ = (corners[1][0] + (corners[2][0] - corners[1][0]) * t, corners[1][1] + (corners[2][1] - corners[1][1]) * t)
            m_ = ((a_[0] + b_[0]) / 2 + 20 * s, (a_[1] + b_[1]) / 2 + 26 * s)
            fat.line([a_, m_, b_], 255)
        for t in (0.3, 0.62):
            gx = fx0 + 300 * s * t
            fish.line([(gx, fy0 - 10 * s), (gx + 70 * s, fy0 + 300 * s)], 0, 4)
    lay(grid, frame, fish, gray(0x8A))
    lay(grid, frame, fat, gray(0xC8), mode="solid", over=False)
    lay(grid, frame, skin, gray(0xD8), mode="solid")
    lemon = Dots(grid, cw)
    lx, ly, lr = x1 - 150 * s, y1 - 110 * s, 110 * s
    lemon.d.pieslice([(lx - lr) / lemon.pitch, (ly - lr) / lemon.pitch, (lx + lr) / lemon.pitch, (ly + lr) / lemon.pitch],
                     180, 360, outline=255, width=2)
    for a_ in range(200, 341, 28):
        lemon.line([(lx, ly), (lx + math.cos(math.radians(a_)) * lr * 0.85, ly + math.sin(math.radians(a_)) * lr * 0.85)])
    lay(grid, frame, lemon, gray(0xB4), mode="solid")
    leaf = Dots(grid, cw)
    zig = [(x0 + 30 * s + i * 36 * s, y1 - 40 * s - (14 * s if i % 2 else 0)) for i in range(12)]
    leaf.line(zig, 255, 2)
    lay(grid, frame, leaf, gray(0x6A), mode="solid")

    # Simmered vegetables: lotus root, carrot flowers, a shiitake, snow peas.
    x0, y0, x1, y1 = room(2)
    veg = Dots(grid, cw)
    for lx, ly in ((x0 + 150 * s, y0 + 150 * s), (x0 + 390 * s, y0 + 250 * s)):
        veg.ellipse(lx, ly, 120 * s, tone=110)
        veg.ellipse(lx, ly, 22 * s, tone=0)
        for a_ in range(0, 360, 51):
            veg.ellipse(lx + math.cos(math.radians(a_)) * 66 * s, ly + math.sin(math.radians(a_)) * 66 * s, 22 * s, tone=0)
    lay(grid, frame, veg, gray(0xA8))
    carrot = Dots(grid, cw)
    for fx_, fy_ in ((x1 - 110 * s, y0 + 110 * s), (x1 - 110 * s, y0 + 370 * s), (x0 + 110 * s, y1 - 120 * s)):
        petals = [(fx_ + 78 * s * (0.75 + 0.25 * math.cos(5 * t)) * math.cos(t),
                   fy_ + 78 * s * (0.75 + 0.25 * math.cos(5 * t)) * math.sin(t)) for t in np.linspace(0, 2 * math.pi, 60)]
        carrot.polygon(petals, 170)
        carrot.ellipse(fx_, fy_, 14 * s, tone=0)
    lay(grid, frame, carrot, gray(0xC4))
    mush = Dots(grid, cw)
    mx, my = x0 + 300 * s, y1 - 150 * s
    mush.ellipse(mx, my, 95 * s, tone=80)
    for a_ in (45, 135):
        mush.line([(mx - math.cos(math.radians(a_)) * 64 * s, my - math.sin(math.radians(a_)) * 64 * s),
                   (mx + math.cos(math.radians(a_)) * 64 * s, my + math.sin(math.radians(a_)) * 64 * s)], 0, 3)
    lay(grid, frame, mush, gray(0x7A))
    pea = Dots(grid, cw)
    for k_ in range(2):
        cx_, cy_ = x1 - 150 * s, y1 - 170 * s + k_ * 74 * s
        pod = [(cx_ + 110 * s * math.cos(t), cy_ + 26 * s * math.sin(t) * (1 - 0.3 * math.cos(t))) for t in np.linspace(0, 2 * math.pi, 40)]
        pea.outline(pod, 255, 2)
    lay(grid, frame, pea, gray(0x9A), mode="solid")

    # Rolled omelette, maki rolls, pickles.
    x0, y0, x1, y1 = room(3)
    egg = Dots(grid, cw)
    for k_ in range(3):
        ex0 = x0 + 10 * s + k_ * 200 * s
        egg.polygon(rounded_rect(ex0, y0 + 10 * s, ex0 + 180 * s, y0 + 190 * s, 24 * s), 60)
    lay(grid, frame, egg, gray(0x9A))
    roll = Dots(grid, cw)
    for k_ in range(3):
        ex0 = x0 + 10 * s + k_ * 200 * s
        for inset in (0, 34 * s, 68 * s):
            roll.outline(rounded_rect(ex0 + inset, y0 + 10 * s + inset, ex0 + 180 * s - inset * 0.6, y0 + 190 * s - inset,
                                      max(6 * s, 24 * s - inset * 0.3)))
    lay(grid, frame, roll, gray(0xD8), mode="solid")
    maki = Dots(grid, cw)
    for k_ in range(3):
        mx, my = x0 + 100 * s + k_ * 190 * s, y1 - 110 * s
        maki.ellipse(mx, my, 84 * s, tone=255)
        maki.ellipse(mx, my, 66 * s, tone=0)
    lay(grid, frame, maki, gray(0x5A), mode="solid")
    rice2 = Dots(grid, cw)
    for k_ in range(3):
        mx, my = x0 + 100 * s + k_ * 190 * s, y1 - 110 * s
        rice2.ellipse(mx, my, 62 * s, tone=210)
        rice2.ellipse(mx, my, 24 * s, tone=0)
    lay(grid, frame, rice2, gray(0xD4))
    core = Dots(grid, cw)
    for k_ in range(3):
        core.ellipse(x0 + 100 * s + k_ * 190 * s, y1 - 110 * s, 20 * s, tone=255)
    lay(grid, frame, core, gray(0x8A), mode="solid")
    pick = Dots(grid, cw)
    for k_ in range(4):
        px0 = x1 - 120 * s
        py0 = y0 + 230 * s + k_ * 52 * s
        pick.polygon([(px0, py0), (px0 + 110 * s, py0 - 14 * s), (px0 + 110 * s, py0 + 26 * s), (px0, py0 + 40 * s)], 90)
    lay(grid, frame, pick, gray(0x7A))
    caption(grid, frame, "inside-the-box", "SHOKADO BENTO  ·  BLACK BOX  ·  1992")
    return finish(grid, cw, ch, width, height)


# --- 05 keyboard -------------------------------------------------------------

SHIFTED = dict(zip("`1234567890-=[]\\;',./", "~!@#$%^&*()_+{}|:\"<>?"))
ARROWS = {"<": "←", "^": "↑", ">": "→"}


def render_keyboard(width, height):
    """The keyboard from above, with the TrackPoint, its three buttons and the
    touchpad."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    U = 17                                           # columns a key unit
    kc0 = (grid.cols - 15 * U) // 2
    heights = [5, 9, 9, 9, 9, 9]
    r = 8
    rows_at = []
    for i, (row, hgt) in enumerate(zip(keyboard_layout(), heights)):
        rows_at.append(r)
        x = 0.0
        for label, w in row:
            a, b = kc0 + round(x * U), kc0 + round((x + w) * U) - 1
            bottom = r + hgt - 3
            box(grid, r, a, bottom, b, gray(0x6A))
            for c in range(a + 1, b + 1):            # the keycap's side, lit from the top left
                grid.put(bottom + 1, c, "▀", gray(0x22))
            text = ARROWS.get(label, label)
            if i == 0:
                grid.text(r + 1, a + (b - a + 1 - len(text)) // 2, text, gray(0xA8))
            elif text in SHIFTED:
                grid.text(r + 2, a + 3, SHIFTED[text], gray(0x8A))
                grid.text(r + 4, a + 3, text, gray(0xF0), True)
            else:
                grid.text(r + 2, a + 3, text, gray(0xF0), True)
            x += w
        r += hgt

    # The TrackPoint where G, H and B meet, the corners around it cleared.
    home = rows_at[3]
    col = kc0 + round(6.75 * U)
    nr = home + heights[3] - 2
    for rr_ in range(nr - 2, nr + 3):
        for cc in range(col - 4, col + 4):
            grid.put(rr_, cc, " ", gray(0))
    red_ball(grid, frame, cw, col * cw, (nr + 0.5) * ch, 36 * s)

    # Three buttons, the middle one between two red stripes, and the touchpad.
    br_ = r
    mid = kc0 + round(7.5 * U)
    bw = 24
    for k in range(3):
        a = mid - bw * 3 // 2 + k * bw
        box(grid, br_, a + 1, br_ + 2, a + bw - 1, gray(0x6A))
    for c in (mid - bw // 2, mid + bw // 2):
        for rr_ in (br_, br_ + 1, br_ + 2):
            grid.put(rr_, c, "┃", RED, True)
    box(grid, br_ + 4, mid - 38, min(br_ + 20, frame[1] - 5), mid + 38, gray(0x4E))
    caption(grid, frame, "typing", "6 ROWS  ·  1.5 mm TRAVEL  ·  TRACKPOINT")
    return finish(grid, cw, ch, width, height)


# --- 06 orbit ----------------------------------------------------------------

def render_orbit(width, height):
    """The space station over the curve of the Earth, its orbit in red."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    rng = np.random.default_rng(11)
    R, gx, gy = 2500 * s, width / 2, height + 1300 * s
    lat0, lon0 = math.radians(8), math.radians(15)

    globe = Dots(grid, cw)
    x, y = globe.coords()
    X, Y = (x - gx) / R, (gy - y) / R
    rho = np.hypot(X, Y)
    inside = rho < 1
    c = np.arcsin(np.clip(rho, 0, 1))
    with np.errstate(invalid="ignore", divide="ignore"):
        lat = np.arcsin(np.cos(c) * math.sin(lat0) + np.where(rho > 0, Y * np.sin(c) * math.cos(lat0) / rho, 0))
        lon = lon0 + np.arctan2(X * np.sin(c), rho * np.cos(c) * math.cos(lat0) - Y * np.sin(c) * math.sin(lat0))
    land_mask = np.asarray(Image.open(LAND_MASK).convert("L")) > 127
    ppd = land_mask.shape[1] / 360
    mr = np.clip(((90 - np.degrees(lat)) * ppd).astype(int), 0, land_mask.shape[0] - 1)
    mc = (((np.degrees(lon) + 180) % 360) * ppd).astype(int) % land_mask.shape[1]
    land = inside & land_mask[mr, mc]
    z = np.sqrt(np.clip(1 - rho * rho, 0, 1))
    tone = np.where(land, 0.35 + 0.6 * z ** 0.6, 0)
    k = cell_mean(tone, grid)
    lay(grid, frame, tone, np.clip(np.stack([k] * 3, -1) * 255 * 0.85 + 40, 0, 255).astype(int) * (k[..., None] > 0))
    latd, lond = np.degrees(lat), np.degrees(lon)
    grat = inside & ~land & ((np.abs((latd + 7.5) % 15 - 7.5) < 0.18) | (np.abs((lond + 7.5) % 15 - 7.5) < 0.18 * 1 / np.maximum(np.cos(lat), 0.2)))
    put_dots(grid, grat & safe(grid, frame), gray(0x2C), over=False)
    atmos = (rho >= 1) & (rho < 1 + 34 * s / R)
    glow = np.where(atmos, 1 - (rho - 1) * R / (34 * s), 0) ** 1.5
    lay(grid, frame, glow, gray(0x7A))

    # Stars in the sky above the limb.
    r0, r1, c0, c1 = frame
    for _ in range(300):
        r_, c_ = int(rng.integers(r0 + 2, r1 - 4)), int(rng.integers(c0 + 3, c1 - 2))
        px, py = (c_ + 0.5) * cw, (r_ + 0.5) * ch
        if math.hypot(px - gx, gy - py) < R + 80 * s or grid.ch[r_, c_] != " ":
            continue
        b = rng.random()
        grid.put(r_, c_, "*" if b > 0.97 else "+" if b > 0.9 else "·" if b > 0.5 else ".", gray(int(0x30 + 0x80 * b ** 2)))

    # The orbit, dashed in red, 420 km up, and the station on it.
    Ro = R + 190 * s
    orbit = Dots(grid, cw)
    angles = np.linspace(-0.75, 0.75, 1600)
    for i in range(len(angles) - 1):
        if (i // 14) % 3 != 2:
            a, b = angles[i], angles[i + 1]
            orbit.line([(gx + Ro * math.sin(a), gy - Ro * math.cos(a)), (gx + Ro * math.sin(b), gy - Ro * math.cos(b))], 255, 2)
    lay(grid, frame, orbit, RED, mode="solid")
    th = -0.22
    ix, iy = gx + Ro * math.sin(th), gy - Ro * math.cos(th)
    station(grid, frame, cw, ix, iy, th, s)
    caption(grid, frame, "in-orbit", "ALT 420 km  ·  92 min  ·  51.6°")
    return finish(grid, cw, ch, width, height)


def station(grid, frame, cw, cx, cy, a, s):
    """A space station along angle a: the truss, eight solar arrays, the
    radiators and a string of modules."""
    def R(pts):
        return rotate(pts, cx, cy, a)

    def rect(x0, y0, x1, y1):
        return R([(cx + x0, cy + y0), (cx + x1, cy + y0), (cx + x1, cy + y1), (cx + x0, cy + y1)])

    arrays, frames_, truss, modules = (Dots(grid, cw) for _ in range(4))
    aw, ah = 70 * s, 250 * s
    for sgn in (-1, 1):
        for off in (330 * s, 430 * s):
            for up in (-1, 1):
                x0 = sgn * off - aw / 2
                y0, y1 = (-ah - 14 * s, -14 * s) if up < 0 else (14 * s, ah + 14 * s)
                frames_.outline(rect(x0, y0, x0 + aw, y1))
                for k in range(1, 12):
                    yy = y0 + (y1 - y0) * k / 12
                    arrays.line(R([(cx + x0, cy + yy), (cx + x0 + aw, cy + yy)]))
                arrays.line(R([(cx + x0 + aw / 2, cy + y0), (cx + x0 + aw / 2, cy + y1)]))
    truss.polygon(rect(-500 * s, -9 * s, 500 * s, 9 * s), 255)
    for k in range(-12, 13):
        truss.line(R([(cx + k * 40 * s, cy - 9 * s), (cx + k * 40 * s + 20 * s, cy + 9 * s)]), 0)
    for x0, y0, x1, y1 in ((-30, -150, 30, -14), (-26, 14, 26, 210), (-110, -120, -44, -96), (44, 120, 140, 144),
                           (-50, 210, 50, 250), (-150, -40, -95, -14), (95, 14, 150, 40)):
        modules.polygon(rect(x0 * s, y0 * s, x1 * s, y1 * s), 255)
    lay(grid, frame, arrays, gray(0x8A), mode="solid")
    lay(grid, frame, frames_, gray(0xD8), mode="solid")
    lay(grid, frame, truss, gray(0xC8), mode="solid")
    lay(grid, frame, modules, gray(0xE4), mode="solid")


# --- 07 notepad --------------------------------------------------------------

def render_notepad(width, height):
    """The pocket notepad with THINK on its cover, and a pencil."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    rng = np.random.default_rng(7)
    x0, y0, x1, y1 = 1100 * s, 290 * s, 2100 * s, 1800 * s
    cover = rounded_rect(x0, y0, x1, y1, 46 * s)

    # The ribbon bookmark first: the cover goes over its top end.
    ribbon = Dots(grid, cw)
    rx = x0 + 330 * s
    ribbon.polygon([(rx, y1 - 60 * s), (rx + 54 * s, y1 - 60 * s), (rx + 54 * s, y1 + 150 * s), (rx + 27 * s, y1 + 112 * s),
                    (rx, y1 + 150 * s)])
    lay(grid, frame, ribbon, RED, mode="solid")

    grain = Dots(grid, cw)
    grain.polygon(cover, 255)
    tone = grain.tone() * (0.06 + 0.05 * rng.random(grain.tone().shape))
    put_dots(grid, (rng.random(tone.shape) < tone) & safe(grid, frame), gray(0x38))
    edge = Dots(grid, cw)
    edge.outline(cover, 255, 2)
    lay(grid, frame, edge, gray(0x9A), mode="solid")
    stitch = Dots(grid, cw)
    inner = rounded_rect(x0 + 40 * s, y0 + 40 * s, x1 - 40 * s, y1 - 40 * s, 24 * s, 10)
    total = 0.0
    for (ax, ay), (bx, by) in zip(inner, inner[1:] + inner[:1]):
        seg = math.hypot(bx - ax, by - ay)
        n = max(1, int(seg / (8 * s)))
        for i in range(n):
            if int((total + seg * i / n) / (26 * s)) % 2 == 0:
                stitch.line([(ax + (bx - ax) * i / n, ay + (by - ay) * i / n),
                             (ax + (bx - ax) * (i + 1) / n, ay + (by - ay) * (i + 1) / n)])
        total += seg
    lay(grid, frame, stitch, gray(0x5E), mode="solid")

    # The band holding it shut.
    band = Dots(grid, cw)
    band.polygon([(x1 - 120 * s, y0 - 6 * s), (x1 - 84 * s, y0 - 6 * s), (x1 - 84 * s, y1 + 6 * s), (x1 - 120 * s, y1 + 6 * s)], 90)
    lay(grid, frame, band, gray(0x5A))
    bedge = Dots(grid, cw)
    for bx in (x1 - 120 * s, x1 - 84 * s):
        bedge.line([(bx, y0 - 6 * s), (bx, y1 + 6 * s)])
    lay(grid, frame, bedge, gray(0x8A), mode="solid")

    # THINK, embossed: a shadow below right, the letters lit.
    tx, ty, size = (x0 + x1) / 2 - 50 * s, y0 + 520 * s, 250 * s
    shadow = Dots(grid, cw)
    shadow.text(tx + 9 * s, ty + 9 * s, "THINK", size)
    letters = Dots(grid, cw)
    letters.text(tx, ty, "THINK", size)
    lay(grid, frame, np.clip(shadow.tone() - letters.tone(), 0, 1), gray(0x1E), mode="solid")
    lay(grid, frame, letters, gray(0xE6), mode="solid")
    rule = Dots(grid, cw)
    rule.line([(tx - 330 * s, ty + 210 * s), (tx + 330 * s, ty + 210 * s)])
    lay(grid, frame, rule, gray(0x4A), mode="solid")

    # A pencil across the right, sharpened, its eraser worn.
    a = math.radians(-56)
    px, py, plen, pw = 2380 * s, 1760 * s, 1300 * s, 34 * s
    d, n = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))

    def along(t, w):
        return px + d[0] * t + n[0] * w, py + d[1] * t + n[1] * w

    body, facet, metal, tip = (Dots(grid, cw) for _ in range(4))
    body.polygon([along(0, -pw), along(plen * 0.86, -pw), along(plen * 0.86, pw), along(0, pw)], 110)
    for w in (-pw / 3, pw / 3):
        facet.line([along(90 * s, w), along(plen * 0.86, w)])
    for t in np.arange(0, 80 * s, 13 * s):
        metal.line([along(10 * s + t, -pw - 2 * s), along(10 * s + t, pw + 2 * s)])
    tip.polygon([along(plen * 0.86, -pw), along(plen, -6 * s), along(plen, 6 * s), along(plen * 0.86, pw)], 60)
    tip.polygon([along(plen * 0.965, -9 * s), along(plen + 18 * s, 0), along(plen * 0.965, 9 * s)], 255)
    eraser = Dots(grid, cw)
    eraser.polygon([along(-60 * s, -pw + 2 * s), along(10 * s, -pw), along(10 * s, pw), along(-60 * s, pw - 2 * s)], 70)
    lay(grid, frame, body, gray(0x8A))
    lay(grid, frame, facet, gray(0xC8), mode="solid")
    lay(grid, frame, metal, gray(0xB0), mode="solid")
    lay(grid, frame, tip, gray(0xD8))
    lay(grid, frame, eraser, gray(0x7A))
    caption(grid, frame, "on-paper", "THINK  ·  POCKET NOTEPAD  ·  90 × 140 mm")
    return finish(grid, cw, ch, width, height)


# --- 08 exploded -------------------------------------------------------------

def render_exploded(width, height):
    """The base of a laptop taken apart, its layers lifted off one another."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    W, D, k = 320.0, 225.0, 3.45 * s
    ox, oy, gap = 800 * s, 1730 * s, 74.0

    def P(x, y, z):
        return ox + x * 0.94 * k + z * 0.45 * k, oy + x * 0.17 * k - z * 0.30 * k - y * k

    def quad(x0, z0, x1, z1, y):
        return [P(x0, y, z0), P(x1, y, z0), P(x1, y, z1), P(x0, y, z1)]

    def ring(cx, cz, r, y, n=40):
        return [P(cx + r * math.cos(t), y, cz + r * math.sin(t)) for t in np.linspace(0, 2 * math.pi, n)]

    hidden, plate, detail = (Dots(grid, cw) for _ in range(3))
    tops = [gap * i for i in range(6)]
    for cx_, cz_ in ((0, 0), (W, 0), (W, D), (0, D)):
        a, b = P(cx_, 0, cz_), P(cx_, tops[-1], cz_)
        n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / (12 * s))
        for i in range(0, n, 3):
            hidden.line([(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n),
                         (a[0] + (b[0] - a[0]) * (i + 1) / n, a[1] + (b[1] - a[1]) * (i + 1) / n)])
    lay(grid, frame, hidden, gray(0x2E), mode="solid")

    def slab(x0, z0, x1, z1, y, t):
        plate.outline(quad(x0, z0, x1, z1, y))
        for x_, z_ in ((x0, z0), (x1, z0), (x1, z1)):
            plate.line([P(x_, y, z_), P(x_, y - t, z_)])
        plate.line([P(x0, y - t, z0), P(x1, y - t, z0), P(x1, y - t, z1)])

    # 1 bottom cover: feet and vents.
    y = tops[0]
    slab(0, 0, W, D, y, 6)
    for fx, fz in ((22, 20), (W - 22, 20), (W - 22, D - 20), (22, D - 20)):
        detail.outline(ring(fx, fz, 9, y, 16))
    for vx in np.arange(110, 215, 9):
        detail.line([P(vx, y, 168), P(vx, y, 196)])
    # 2 battery: three cells.
    y = tops[1]
    slab(40, 12, 280, 86, y, 9)
    for bx in (120, 200):
        detail.line([P(bx, y, 12), P(bx, y, 86)])
    # 3 mainboard: chips, memory, an SSD and the fan.
    y = tops[2]
    board = [P(20, y, 96), P(300, y, 96), P(300, y, 214), P(20, y, 214)]
    plate.outline(board)
    for x0, z0, x1, z1 in ((130, 140, 176, 182), (190, 150, 214, 174), (60, 120, 112, 132), (60, 140, 112, 152),
                           (60, 160, 112, 172), (60, 190, 150, 204), (110, 104, 170, 116)):
        detail.outline(quad(x0, z0, x1, z1, y))
    detail.outline(ring(255, 165, 30, y))
    for t in np.linspace(0, 2 * math.pi, 9)[:-1]:
        detail.line([P(255, y, 165), P(255 + 27 * math.cos(t + 0.4), y, 165 + 27 * math.sin(t + 0.4))])
    # 4 roll cage.
    y = tops[3]
    slab(0, 0, W, D, y, 4)
    for z_ in (80, 150):
        detail.line([P(8, y, z_), P(W - 8, y, z_)])
    for x_ in (100, 220):
        detail.line([P(x_, y, 8), P(x_, y, D - 8)])
    detail.line([P(8, y, 8), P(100, y, 80)])
    detail.line([P(W - 8, y, 8), P(220, y, 80)])
    # 5 keyboard: the key rows, and the TrackPoint.
    y = tops[4]
    slab(25, 96, 295, 206, y, 5)
    for i in range(1, 6):
        z_ = 206 - 110 * i / 6
        detail.line([P(25, y, z_), P(295, y, z_)])
    u = 270 / 15
    for i, row in enumerate(keyboard_layout()):
        xk = 25.0
        for _, w in row[:-1]:
            xk += w * u
            z_top, z_bot = 206 - 110 * i / 6, 206 - 110 * (i + 1) / 6
            detail.line([P(xk, y, z_top), P(xk, y, z_bot)])
    nub = Dots(grid, cw)
    nub.polygon(ring(25 + 6.75 * u, 206 - 110 * 4 / 6, 4.5, y, 20))
    # 6 top case: the keyboard opening, the touchpad and its buttons.
    y = tops[5]
    slab(0, 0, W, D, y, 6)
    detail.outline(quad(25, 96, 295, 206, y))
    detail.outline(quad(110, 14, 210, 70, y))
    detail.outline(quad(110, 74, 210, 88, y))
    for bx in (143, 177):
        detail.line([P(bx, y, 74), P(bx, y, 88)])
    lay(grid, frame, detail, gray(0x7A), mode="solid")
    lay(grid, frame, plate, gray(0xC8), mode="solid")
    lay(grid, frame, nub, BRIGHT_RED, mode="solid")

    # Labels on the right, each with a leader from its layer.
    names = ["BOTTOM COVER", "BATTERY  ·  3 CELLS", "MAINBOARD", "ROLL CAGE", "KEYBOARD  ·  TRACKPOINT", "TOP CASE"]
    lc = 222
    for i, (yv, name) in enumerate(zip(tops, names)):
        x_, y_ = P(W, yv, D * 0.5)
        r = int(y_ // ch)
        for c in range(int(x_ // cw) + 2, lc - 1):
            grid.put(r, c, "·", gray(0x3A))
        grid.text(r, lc, f"{i + 1:02d}", RED if i == 4 else gray(0xE4), True)
        grid.text(r, lc + 4, name, gray(0x9A))
    caption(grid, frame, "in-layers", "6 LAYERS  ·  ROLL CAGE  ·  SPILL DRAINS")
    return finish(grid, cw, ch, width, height)


# --- 09 mountains ------------------------------------------------------------

def noise1d(n, rng, scale, octaves=5):
    out = np.zeros(n)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        step = max(2, int(scale / 2 ** o))
        pts = rng.random(n // step + 3)
        xs = np.arange(n) / step
        i = xs.astype(int)
        f = xs - i
        f = f * f * (3 - 2 * f)
        out += (pts[i] * (1 - f) + pts[i + 1] * f) * amp
        total += amp
        amp *= 0.5
    return out / total


def render_mountains(width, height):
    """Ridges going back into the haze, and a red sun going down behind them."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    rng = np.random.default_rng(5)
    x, y = Dots(grid, cw).coords()
    h, w = x.shape
    crests = []
    for k in range(5):                                  # far to near
        n = noise1d(w, rng, 360 - 45 * k, octaves=4)
        ridge = 1 - np.abs(2 * n - 1)                   # folded, for sharper peaks
        ridge = (ridge - ridge.min()) / (np.ptp(ridge) + 1e-9)
        crests.append((980 + 170 * k) * s - (420 - 60 * k) * s * ridge ** 1.6)
    covered = np.zeros((h, w), bool)
    for k in reversed(range(5)):                        # near first, so it hides what's behind
        crest = crests[k][None, :]
        below = y >= crest
        region = below & ~covered
        depth = (y - crest) / ((110 + 60 * (k == 4)) * s)
        lay(grid, frame, np.where(region, 0.62 * np.exp(-depth), 0), gray(0x50 + 0x1C * k))
        put_dots(grid, region & (y - crest < 6.5 * s) & safe(grid, frame), gray(0x80 + 0x1A * k))
        covered |= below
    # The sun behind the farthest ridge, its lower half cut into bands.
    sx, sy, sr = 2380 * s, 720 * s, 230 * s
    dx, dy = (x - sx) / sr, (y - sy) / sr
    band = np.floor((dy - 0.1) / 0.16)
    cut = (dy > 0.1) & (((dy - 0.1) % 0.16) < 0.03 + 0.018 * band)
    put_dots(grid, (dx * dx + dy * dy < 1) & ~covered & ~cut & safe(grid, frame), RED)
    r0, r1, c0, c1 = frame
    for _ in range(90):
        r_, c_ = int(rng.integers(r0 + 2, r0 + 22)), int(rng.integers(c0 + 3, c1 - 2))
        if grid.ch[r_, c_] == " ":
            grid.put(r_, c_, "·" if rng.random() > 0.3 else ".", gray(int(rng.integers(0x26, 0x50))))
    caption(grid, frame, "bigger", "5 RIDGES  ·  2 840 m  ·  DUSK")
    return finish(grid, cw, ch, width, height)


# --- 10 setup ----------------------------------------------------------------

SETUP_ITEMS = [
    ("TrackPoint", "[Enabled]"),
    ("Trackpad", "[Enabled]"),
    ("Fn and Ctrl Key swap", "[Disabled]"),
    ("Fn Sticky key", "[Disabled]"),
    ("F1-F12 as Primary Function", "[Enabled]"),
    ("Keyboard Backlight", "[Auto]"),
    ("Keyboard Layout", "[English]"),
]

SETUP_MENU = ["Network", "USB", "Keyboard/Mouse", "Display", "Power", "Beep and Alarm", "Storage", "CPU",
              "Virtualization", "Security Chip"]


def render_setup(width, height):
    """A setup utility, as it looks before the system boots."""
    grid, frame, cw, ch = sheet(width, height)
    r0, r1, c0, c1 = 10, 76, 58, 262
    dim, mid, hi = gray(0x5A), gray(0x9A), gray(0xE8)
    box(grid, r0, c0, r1, c1, mid, "double")
    title = " SETUP UTILITY "
    for c in range(c0 + 1, c1):
        grid.put(r0 + 1, c, " ", gray(0), bg=(28, 28, 28))
    grid.text(r0 + 1, (c0 + c1 - len(title)) // 2, title, hi, True, bg=(28, 28, 28))
    tabs = ["Main", "Config", "Date/Time", "Security", "Startup", "Restart"]
    c = c0 + 4
    for t in tabs:
        on = t == "Config"
        grid.text(r0 + 3, c, f" {t} ", gray(0x0) if on else mid, on, bg=gray(0xE8) if on else None)
        c += len(t) + 6
    for c in range(c0 + 1, c1):
        grid.put(r0 + 4, c, "─", dim)
    split = c0 + 136
    for r in range(r0 + 5, r1 - 4):
        grid.put(r, split, "│", dim)

    # Left: the Keyboard/Mouse submenu, its first row selected.
    grid.text(r0 + 7, c0 + 5, "Config  ▸  Keyboard/Mouse", mid)
    for i, (name, value) in enumerate(SETUP_ITEMS):
        r = r0 + 10 + i * 3
        if i == 0:
            for cc in range(c0 + 3, split - 2):
                grid.put(r, cc, " ", gray(0), bg=RED)
            grid.text(r, c0 + 5, name, gray(0xFF), True, bg=RED)
            grid.text(r, c0 + 60, value, gray(0xFF), True, bg=RED)
        else:
            grid.text(r, c0 + 5, name, hi)
            grid.text(r, c0 + 60, value, mid)
    r = r0 + 10 + len(SETUP_ITEMS) * 3 + 2
    grid.text(r, c0 + 5, "Other settings", dim)
    for i, name in enumerate(SETUP_MENU):
        grid.text(r + 2 + i * 2, c0 + 7, ("▸ " if name == "Keyboard/Mouse" else "  ") + name,
                  hi if name == "Keyboard/Mouse" else dim)

    # Right: help for the selected item.
    help_ = ["Item Specific Help", "",
             "Enable or disable the TrackPoint.", "",
             "[Enabled]", "  The red dot moves the", "  pointer without your hands", "  leaving the home row.", "",
             "[Disabled]", "  The pointing stick is", "  turned off."]
    for i, line in enumerate(help_):
        grid.text(r0 + 7 + i * 2, split + 4, line, hi if i == 0 else mid, i == 0)

    # The key legend along the bottom.
    for c in range(c0 + 1, c1):
        grid.put(r1 - 4, c, "─", dim)
    legend = [("F1", "Help"), ("↑↓", "Select Item"), ("F5/F6", "Change Values"), ("F9", "Setup Defaults"),
              ("Esc", "Exit"), ("Enter", "Select ▸ Sub-Menu"), ("F10", "Save and Exit")]
    c = c0 + 4
    for i, (key, what) in enumerate(legend):
        rr_ = r1 - 3 + (i % 2) * 1
        if i % 2 == 0 and i:
            c += 26
        grid.text(rr_, c, key, hi, True)
        grid.text(rr_, c + 7, what, mid)
    grid.text(r1 - 3, c1 - 18, "Version 1.00", dim)
    caption(grid, frame, "before-booting", "SETUP  ·  CONFIG  ·  KEYBOARD/MOUSE")
    return finish(grid, cw, ch, width, height)


# --- 11 halftone -------------------------------------------------------------

def render_halftone(width, height):
    """The wordmark's i, blown up until the print shows: gray letters, and the
    red dot as a big halftone sphere."""
    grid, frame, cw, ch = sheet(width, height)
    canvas = Dots(grid, cw)
    h, w = canvas.tone().shape
    dot_d = 120                                        # the red dot, in dots across
    wm_w = int(round(dot_d / 0.057))
    white, red = wordmark_masks(wm_w)
    ys, xs = np.nonzero(red > 0.5)
    rcx, rcy = xs.mean(), ys.mean()
    ox, oy = int(w * 0.5 - rcx), int(h * 0.34 - rcy)
    letters = np.zeros((h, w), np.float32)
    dot = np.zeros((h, w), np.float32)
    y0, y1 = max(0, oy), min(h, oy + white.shape[0])
    x0, x1 = max(0, ox), min(w, ox + white.shape[1])
    letters[y0:y1, x0:x1] = white[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    dot[y0:y1, x0:x1] = red[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # The letters fade toward the bottom, as if the light falls off.
    letters *= np.clip(0.92 - (yy / h) * 0.8, 0.12, 1)
    on = halftone(letters, 9.0, 45) & safe(grid, frame)
    put_dots(grid, on, gray(0x9A))
    r = dot_d / 2
    dx, dy = (xx - (ox + rcx)) / r, (yy - (oy + rcy)) / r
    rr = dx * dx + dy * dy
    lit = np.clip(-0.45 * dx - 0.55 * dy + 0.70 * np.sqrt(np.clip(1 - rr, 0, 1)), 0, 1)
    tone = dot * (0.28 + 0.72 * lit)
    on = halftone(tone, 8.0, 15) & safe(grid, frame)
    k = cell_mean(tone, grid)
    colors = np.zeros((grid.rows, grid.cols, 3))
    colors[:] = RED
    colors *= (0.55 + 0.6 * k)[..., None]
    put_dots(grid, on, np.clip(colors, 0, 255))
    caption(grid, frame, "in-dots", "HALFTONE  ·  45° SCREEN  ·  ONE RED DOT")
    return finish(grid, cw, ch, width, height)


# --- 12 drop -----------------------------------------------------------------

def render_drop(width, height):
    """A closed laptop dropped from 122 cm, every tenth of a second, landing
    on a corner."""
    grid, frame, cw, ch = sheet(width, height)
    s = height / 2160
    ground = 1700 * s
    H = 1300 * s                                        # 122 cm
    L, T = 1000 * s, 56 * s
    cx = width / 2 + 120 * s
    times = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    fall = [0.5 * 9.81 * t * t / 1.22 * H for t in times]
    tilts = [0, 2, 4, 6, 8, 9.5]

    def slab(i):
        a = math.radians(tilts[i])
        top = ground - H + fall[i] - T
        pts = rounded_rect(cx - L / 2, top, cx + L / 2, top + T, 14 * s)
        pts = rotate(pts, cx, top + T / 2, a)
        lowest = max(p[1] for p in pts)
        if i == len(times) - 1:
            pts = [(px, py + ground - lowest) for px, py in pts]   # resting on its lowest corner
        return pts

    streak = Dots(grid, cw)
    last = slab(len(times) - 1)
    for frac in (0.08, 0.5, 0.92):
        xs_ = cx - L / 2 + L * frac
        for yv in np.arange(ground - H - T, ground - 200 * s, 22 * s):
            streak.line([(xs_, yv), (xs_, yv + 8 * s)])
    lay(grid, frame, streak, gray(0x24), mode="solid")
    for i in range(len(times) - 1):
        d = Dots(grid, cw)
        d.outline(slab(i), 255, 1)
        lay(grid, frame, d, gray(0x30 + 0x18 * i), mode="solid")
    body = Dots(grid, cw)
    body.polygon(last, 120)
    body.outline(last, 255, 2)
    lay(grid, frame, body, gray(0xD8))

    # The impact, in red, where the corner meets the floor.
    hx, hy = max(last, key=lambda p: p[1])
    burst = Dots(grid, cw)
    for a in range(200, 341, 20):
        r = math.radians(a)
        burst.line([(hx + math.cos(r) * 40 * s, hy + math.sin(r) * 40 * s),
                    (hx + math.cos(r) * (110 + 40 * (a % 40 == 0)) * s, hy + math.sin(r) * (110 + 40 * (a % 40 == 0)) * s)], 255, 2)
    for rad in (60, 100):
        burst.d.arc([(hx - rad * 2.2 * s) / burst.pitch, (hy - rad * 0.5 * s) / burst.pitch,
                     (hx + rad * 2.2 * s) / burst.pitch, (hy + rad * 0.5 * s) / burst.pitch], 0, 180, fill=255, width=1)
    lay(grid, frame, burst, BRIGHT_RED, mode="solid")

    # Floor, the height, and the clock.
    r0, r1, c0, c1 = frame
    gr = int(ground // ch) + 1
    for c in range(c0 + 6, c1 - 5):
        grid.put(gr, c, "─", gray(0x5A))
        if c % 2 == 0:
            grid.put(gr + 1, c, "╱", gray(0x2A))
    dc = int((cx - L / 2 - 260 * s) // cw)
    ra, rb = int((ground - H) // ch), gr - 1
    for r in range(ra + 1, rb):
        grid.put(r, dc, "│", gray(0x8A))
    grid.put(ra, dc, "┬", gray(0x8A))
    grid.put(rb, dc, "┴", gray(0x8A))
    grid.text((ra + rb) // 2, dc - 9, "122 cm", gray(0xE4), True)
    for i in (0, 3, 5):
        top = min(p[1] for p in slab(i))
        grid.text(int(top // ch), int((cx + L / 2 + 60 * s) // cw), f"t = {times[i]:.1f} s",
                  gray(0xC8) if i == 5 else gray(0x6A))
    caption(grid, frame, "tougher", "DROP 122 cm  ·  0.5 s  ·  CORNER FIRST")
    return finish(grid, cw, ch, width, height)


# --- 13 source ---------------------------------------------------------------

SOURCE = r"""package trackpoint

import (
	"math"
	"time"
)

// A Nub is the red stick between G, H and B. It doesn't move; it measures
// how hard you push it, and the pointer moves at a speed that follows.
type Nub struct {
	Sensitivity float64 // 0..1, from the settings
	Drift       Vec     // what the strain gauges read at rest
	last        time.Time
	pressed     bool
}

type Vec struct{ X, Y float64 }

// Force turns the four strain gauge readings into a push, in newtons.
func (n *Nub) Force(north, east, south, west float64) Vec {
	f := Vec{X: (east - west) / 2, Y: (south - north) / 2}
	return Vec{f.X - n.Drift.X, f.Y - n.Drift.Y}
}

// Speed is the transfer function: a dead zone so a resting finger stays
// still, a slow region for aiming, then a ramp for crossing the screen.
func (n *Nub) Speed(f float64) float64 {
	const deadZone, aim, ramp = 0.02, 0.35, 2.4
	switch {
	case f < deadZone:
		return 0
	case f < aim:
		return 40 * (f - deadZone) * (0.5 + n.Sensitivity)
	default:
		return 40*(aim-deadZone)*(0.5+n.Sensitivity) + ramp*math.Pow(f-aim, 1.6)*900
	}
}

// Move is called a hundred times a second.
func (n *Nub) Move(gauges [4]float64, now time.Time) (dx, dy float64) {
	f := n.Force(gauges[0], gauges[1], gauges[2], gauges[3])
	mag := math.Hypot(f.X, f.Y)
	if mag == 0 {
		n.recalibrate(gauges, now)
		return 0, 0
	}
	dt := now.Sub(n.last).Seconds()
	n.last = now
	v := n.Speed(mag) * dt
	return v * f.X / mag, v * f.Y / mag
}

// recalibrate takes the rest reading as the new zero after a quiet second,
// so a warm finger or a cold morning doesn't leave the pointer creeping.
func (n *Nub) recalibrate(gauges [4]float64, now time.Time) {
	if now.Sub(n.last) < time.Second {
		return
	}
	n.Drift = Vec{X: (gauges[1] - gauges[3]) / 2, Y: (gauges[2] - gauges[0]) / 2}
}

// Press to select: a tap on the nub is a click, if it was quick and light.
func (n *Nub) Tap(peak float64, held time.Duration) bool {
	return peak > 0.6 && held < 180*time.Millisecond
}

// Buttons below the space bar: left, middle, right. Holding the middle
// one turns the nub into a scroll wheel.
type Buttons struct{ Left, Middle, Right bool }

func Scroll(n *Nub, b Buttons, gauges [4]float64, now time.Time) (sx, sy float64) {
	if !b.Middle {
		return 0, 0
	}
	dx, dy := n.Move(gauges, now)
	return dx / 8, dy / 8
}

// Sensitivity curves people tend to settle on.
var Presets = map[string]float64{
	"light":   0.75,
	"default": 0.50,
	"firm":    0.30,
}

func clamp(v, lo, hi float64) float64 {
	return math.Max(lo, math.Min(hi, v))
}

func (n *Nub) SetSensitivity(v float64) {
	n.Sensitivity = clamp(v, 0, 1)
}

// Negative inertia: when the push starts, the pointer moves a little
// faster than the force says, and when it stops, a little backward,
// so it starts and stops where the eye expects.
func (n *Nub) Inertia(prev, cur float64) float64 {
	const k = 0.25
	return cur + k*(cur-prev)
}

func (n *Nub) Reset() {
	n.Drift = Vec{}
	n.pressed = false
}

// Calibrate averages a second of readings with nobody touching the nub
// and keeps it as the zero. Run at boot and after waking up.
func (n *Nub) Calibrate(read func() [4]float64) {
	var sum [4]float64
	const samples = 100
	for i := 0; i < samples; i++ {
		g := read()
		for j := range sum {
			sum[j] += g[j]
		}
		time.Sleep(10 * time.Millisecond)
	}
	for j := range sum {
		sum[j] /= samples
	}
	n.Drift = Vec{X: (sum[1] - sum[3]) / 2, Y: (sum[2] - sum[0]) / 2}
	n.last = time.Now()
}

// Report is what goes over the wire to the host, eight bytes a time.
type Report struct {
	Buttons byte
	DX, DY  int16
	Wheel   int8
}

func (n *Nub) Report(b Buttons, gauges [4]float64, now time.Time) Report {
	var r Report
	if b.Left {
		r.Buttons |= 1
	}
	if b.Right {
		r.Buttons |= 2
	}
	if b.Middle {
		r.Buttons |= 4
		_, sy := Scroll(n, b, gauges, now)
		r.Wheel = int8(clamp(-sy, -127, 127))
		return r
	}
	dx, dy := n.Move(gauges, now)
	r.DX = int16(clamp(dx, -32767, 32767))
	r.DY = int16(clamp(dy, -32767, 32767))
	return r
}

// Run polls the gauges a hundred times a second until the context ends.
func Run(n *Nub, read func() [4]float64, buttons func() Buttons, send func(Report)) {
	n.Calibrate(read)
	tick := time.NewTicker(10 * time.Millisecond)
	defer tick.Stop()
	for now := range tick.C {
		r := n.Report(buttons(), read(), now)
		if r != (Report{}) {
			send(r)
		}
	}
}

// Lift is how far a resting finger can lean before the pointer moves:
// past the dead zone, and for longer than a blink.
func (n *Nub) Lift(f float64, held time.Duration) bool {
	return f > 0.02 && held > 30*time.Millisecond
}

// Feel, in one table: force in, speed out.
//
//	force (N)   speed (px/s)
//	0.00        0
//	0.10        64
//	0.35        460
//	1.00        2 100
//	2.00        5 800
"""


def render_source(width, height):
    """A wall of source code, the TrackPoint driver, in three columns."""
    grid, frame, cw, ch = sheet(width, height)
    r0, r1, c0, c1 = frame
    lines = SOURCE.split("\n")
    top, bottom = r0 + 3, r1 - 5
    per_col = bottom - top
    colw = (c1 - c0 - 8) // 3
    cursor_line = next(i for i, ln in enumerate(lines) if "return cur + k*(cur-prev)" in ln)
    for i, line in enumerate(lines[:per_col * 3]):
        col, row = divmod(i, per_col)
        r = top + row
        base = c0 + 5 + col * colw
        fade = 1.0 - 0.55 * row / per_col
        current = i == cursor_line
        if current:
            for c in range(base, base + colw - 3):
                grid.put(r, c, " ", gray(0), bg=(20, 20, 20))
        grid.text(r, base, f"{i + 1:>3}", gray(0xE4) if current else gray(int(0x40 * fade)))
        c = base + 5
        for text, kind in tokenize_go(line.replace("\t", "    ")):
            fg, bold = SYNTAX[kind]
            if kind == "number":
                fg = gray(0xB8)
            fg = fg if current else shade(fg, fade)
            grid.text(r, c, text[:max(0, base + colw - 4 - c)], fg, bold, bg=(20, 20, 20) if current else None)
            c += len(text)
        if current:
            grid.put(r, min(c, base + colw - 4), " ", gray(0), bg=RED)
    for k in (1, 2):
        cc = c0 + 5 + k * colw - 2
        for r in range(top, bottom):
            grid.put(r, cc, "│", gray(0x22))
    caption(grid, frame, "then-ship", "trackpoint.go  ·  go  ·  utf-8")
    return finish(grid, cw, ch, width, height)


# --- main --------------------------------------------------------------------

RENDERERS = {
    "world": ("01-world.png", render_world),
    "trackpoint": ("02-trackpoint.png", render_trackpoint),
    "hinge": ("03-hinge.png", render_hinge),
    "bento": ("04-bento.png", render_bento),
    "keyboard": ("05-keyboard.png", render_keyboard),
    "orbit": ("06-orbit.png", render_orbit),
    "notepad": ("07-notepad.png", render_notepad),
    "exploded": ("08-exploded.png", render_exploded),
    "mountains": ("09-mountains.png", render_mountains),
    "setup": ("10-setup.png", render_setup),
    "halftone": ("11-halftone.png", render_halftone),
    "drop": ("12-drop.png", render_drop),
    "source": ("13-source.png", render_source),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", default=["all"], help="which to render: " + ", ".join(RENDERERS) + ", or all")
    ap.add_argument("--width", type=int, default=3840)
    ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--out", default=os.path.join(THEME_DIR, "backgrounds"))
    ap.add_argument("--world", action="store_true", help="same as naming world")
    ap.add_argument("--centre", type=float, default=37.5, metavar="LON",
                    help="world: longitude in the middle of the map, rounded to an hour-column boundary "
                         "(default 37.5: the map's seam runs down the Alaska-Yukon border, so the whole "
                         "date line fits)")
    ap.add_argument("--boot-logo", metavar="PNG", nargs="?", const=os.path.join(THEME_DIR, "unlock.png"),
                    help="render only the framed wordmark, for the boot and login screens (default: the theme's unlock.png)")
    args = ap.parse_args()

    if args.boot_logo:
        return boot_logo(args.boot_logo)
    names = ["world"] if args.world else (list(RENDERERS) if "all" in args.names else args.names)
    os.makedirs(args.out, exist_ok=True)
    for name in names:
        filename, render_ = RENDERERS[name]
        img = render_(args.width, args.height, args.centre) if name == "world" else render_(args.width, args.height)
        path = os.path.join(args.out, filename)
        img.save(path, optimize=True)
        print(f"Wrote {path} ({args.width}x{args.height}, {os.path.getsize(path) // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
