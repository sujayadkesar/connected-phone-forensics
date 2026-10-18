"""Render the logo and the Windows icon from the app's own drawing code.

    python scripts/make_brand_assets.py

Writes docs/images/logo.png (512 px) and packaging/app.ico (16-256 px, PNG-compressed).
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice

ROOT = Path(__file__).resolve().parents[1]
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_bytes(pixmap) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[tuple[int, bytes]]) -> None:
    """An ICO with one PNG-compressed image per size (supported since Windows Vista)."""
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.write_bytes(header + entries + blobs)


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    _app = QApplication(sys.argv[:1])
    from connected_phone_forensics.gui.icons import logo_pixmap

    (ROOT / "docs" / "images").mkdir(parents=True, exist_ok=True)
    logo_pixmap(512).save(str(ROOT / "docs" / "images" / "logo.png"), "PNG")
    write_ico(ROOT / "packaging" / "app.ico", [(s, png_bytes(logo_pixmap(s))) for s in SIZES])
    print("wrote docs/images/logo.png and packaging/app.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
