"""Small synthetic pictures for the demo case (PNG, drawn pixel by pixel; nothing real).

A landscape photo, a photographed document, a floor plan and a phone wallpaper, so the
demo's photos, attachments and wallpaper look like pictures in the app, the workbook links
and the PDF summary.
"""

from __future__ import annotations

import math
import struct
import zlib
from functools import cache


def png(width: int, height: int, rows) -> bytes:
    """Encode ``rows`` (an iterable of ``bytes`` of RGB triples) as an 8-bit RGB PNG."""
    raw = b"".join(b"\x00" + bytes(r) for r in rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


@cache
def landscape(width: int = 320, height: int = 240) -> bytes:
    """Sky, sun, two ridges and a lake."""
    sky_top, sky_low = (52, 104, 214), (196, 228, 255)
    sun, ridge_far, ridge_near = (255, 200, 80), (60, 92, 160), (23, 44, 85)
    lake_top, lake_low = (90, 140, 210), (30, 60, 120)
    sx, sy, sr = width * 0.72, height * 0.32, width * 0.08
    horizon = height * 0.68

    def row(y: int) -> bytes:
        out = bytearray()
        for x in range(width):
            far = height * (0.52 + 0.06 * math.sin(x / width * 7.0 + 1.3) + 0.03 * math.sin(x / width * 19.0))
            near = height * (0.6 + 0.05 * math.sin(x / width * 4.5 + 0.4) + 0.02 * math.sin(x / width * 23.0))
            if y >= horizon:
                c = _mix(lake_top, lake_low, (y - horizon) / (height - horizon))
                mirror = horizon - (y - horizon)  # a faint reflection of the ridge
                if mirror < near:
                    c = _mix(c, ridge_near, 0.35)
                if (y + x // 9) % 7 == 0:
                    c = _mix(c, (230, 240, 255), 0.25)
            elif y >= near:
                c = ridge_near
            elif y >= far:
                c = _mix(ridge_far, ridge_near, (y - far) / max(1.0, near - far) * 0.5)
            else:
                c = _mix(sky_top, sky_low, y / horizon)
                d = math.hypot(x - sx, y - sy)
                if d < sr:
                    c = sun
                elif d < sr * 2.4:
                    c = _mix(c, sun, (1 - (d - sr) / (sr * 1.4)) * 0.35)
            out += bytes(c)
        return bytes(out)

    return png(width, height, (row(y) for y in range(height)))


@cache
def document(width: int = 240, height: int = 320) -> bytes:
    """A photographed page: paper on a desk, lines of text and a round stamp."""
    desk, paper, ink, stamp = (92, 70, 52), (246, 243, 236), (70, 78, 96), (196, 52, 52)
    px0, py0, px1, py1 = width * 0.1, height * 0.07, width * 0.9, height * 0.93
    cx, cy, cr = width * 0.7, height * 0.78, width * 0.11

    def row(y: int) -> bytes:
        out = bytearray()
        for x in range(width):
            if px0 <= x <= px1 and py0 <= y <= py1:
                c = paper
                line = int((y - py0 - height * 0.12) // (height * 0.045))
                in_line = 0 <= line < 13 and (y - py0 - height * 0.12) % (height * 0.045) < height * 0.014
                length = px1 - width * (0.06 + (0.25 if line % 4 == 3 else 0.0))
                if in_line and px0 + width * 0.06 <= x <= length:
                    c = ink
                if y < py0 + height * 0.08 and px0 + width * 0.06 <= x <= px0 + width * 0.45 and \
                        y > py0 + height * 0.035:
                    c = (24, 40, 80)
                ring = abs(math.hypot(x - cx, y - cy) - cr)
                if ring < 2.2:
                    c = _mix(c, stamp, 0.85)
            else:
                c = _mix(desk, (60, 44, 32), y / height)
                if math.hypot(x - px1, y - py1) < width * 0.05:  # a soft shadow at the corner
                    c = _mix(c, (30, 22, 16), 0.4)
            out += bytes(c)
        return bytes(out)

    return png(width, height, (row(y) for y in range(height)))


@cache
def floor_plan(width: int = 320, height: int = 240) -> bytes:
    """A blueprint: a grid, walls and a door."""
    bg, grid, wall = (14, 58, 122), (40, 96, 170), (232, 242, 255)
    walls = [(0.08, 0.1, 0.92, 0.1), (0.08, 0.9, 0.92, 0.9), (0.08, 0.1, 0.08, 0.9), (0.92, 0.1, 0.92, 0.9),
             (0.5, 0.1, 0.5, 0.42), (0.5, 0.58, 0.5, 0.9), (0.08, 0.55, 0.5, 0.55), (0.7, 0.1, 0.7, 0.5),
             (0.7, 0.5, 0.92, 0.5)]
    segs = [(a * width, b * height, c * width, d * height) for a, b, c, d in walls]

    def on_wall(x: int, y: int) -> bool:
        for x0, y0, x1, y1 in segs:
            if x0 == x1 and abs(x - x0) <= 1.5 and min(y0, y1) <= y <= max(y0, y1):
                return True
            if y0 == y1 and abs(y - y0) <= 1.5 and min(x0, x1) <= x <= max(x0, x1):
                return True
        return False

    def row(y: int) -> bytes:
        out = bytearray()
        for x in range(width):
            c = grid if x % 16 == 0 or y % 16 == 0 else bg
            if on_wall(x, y):
                c = wall
            out += bytes(c)
        return bytes(out)

    return png(width, height, (row(y) for y in range(height)))


@cache
def wallpaper(width: int = 216, height: int = 468) -> bytes:
    """A phone wallpaper: a diagonal gradient with soft light circles."""
    a, b, c = (124, 92, 224), (36, 86, 222), (16, 160, 170)
    blobs = [(0.8, 0.25, 0.45), (0.15, 0.7, 0.4), (0.6, 0.9, 0.3)]

    def row(y: int) -> bytes:
        out = bytearray()
        for x in range(width):
            t = (x / width * 0.4 + y / height * 0.6)
            col = _mix(a, b, t * 2) if t < 0.5 else _mix(b, c, (t - 0.5) * 2)
            for bx, by, br in blobs:
                d = math.hypot(x - bx * width, y - by * height) / (br * width)
                if d < 1:
                    col = _mix(col, (255, 255, 255), (1 - d) * 0.18)
            out += bytes(col)
        return bytes(out)

    return png(width, height, (row(y) for y in range(height)))


@cache
def avatar(top: tuple = (92, 149, 255), bottom: tuple = (36, 86, 222), size: int = 96) -> bytes:
    """A contact picture: a head and shoulders on a coloured disc."""
    c = size / 2

    def row(y: int) -> bytes:
        out = bytearray()
        for x in range(size):
            if math.hypot(x - c, y - c) > c:
                col = (255, 255, 255)
            else:
                col = _mix(top, bottom, y / size)
                head = math.hypot(x - c, y - size * 0.4) < size * 0.18
                body = ((x - c) / (size * 0.32)) ** 2 + ((y - size * 0.92) / (size * 0.3)) ** 2 < 1
                if head or body:
                    col = _mix(col, (255, 255, 255), 0.85)
            out += bytes(col)
        return bytes(out)

    return png(size, size, (row(y) for y in range(size)))


@cache
def app_icon(color: tuple, glyph: str = "bubble", size: int = 72) -> bytes:
    """An app icon: a rounded square in the app's colour with a simple white glyph."""
    r = size * 0.22

    def inside_rounded(x: float, y: float) -> bool:
        cx = min(max(x, r), size - r)
        cy = min(max(y, r), size - r)
        return math.hypot(x - cx, y - cy) <= r

    def on_glyph(x: float, y: float) -> bool:
        u, v = x / size, y / size
        if glyph == "bubble":  # a chat bubble with a tail
            return ((u - 0.5) / 0.3) ** 2 + ((v - 0.47) / 0.25) ** 2 < 1 or (0.28 < u < 0.4 and 0.62 < v < 0.8
                                                                            and v - 0.62 < (0.4 - u) * 1.5)
        if glyph == "shield":
            return 0.28 < u < 0.72 and 0.22 < v < 0.8 and (v < 0.55 or abs(u - 0.5) < (0.8 - v) * 0.9)
        if glyph == "lock":
            ring = 0.13 < math.hypot(u - 0.5, v - 0.4) < 0.19 and v < 0.45
            return ring or (0.3 < u < 0.7 and 0.43 < v < 0.75)
        return math.hypot(u - 0.5, v - 0.5) < 0.22

    def row(y: int) -> bytes:
        out = bytearray()
        for x in range(size):
            if not inside_rounded(x + 0.5, y + 0.5):
                col = (255, 255, 255)
            else:
                col = _mix(color, tuple(max(0, c - 40) for c in color), y / size)
                if on_glyph(x + 0.5, y + 0.5):
                    col = (255, 255, 255)
            out += bytes(col)
        return bytes(out)

    return png(size, size, (row(y) for y in range(size)))
