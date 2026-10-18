"""Build the Windows executables with PyInstaller.

    pip install -e ".[build]"
    python packaging/build.py            # -> dist/ConnectedPhoneForensics-<v>-windows-x64.exe, cpf-<v>-windows-x64.exe
    python packaging/smoke_test.py dist  # run both and check they work

Each executable is a single file: the desktop app (no console window) and the
command-line tool. SHA256SUMS.txt is written next to them.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from connected_phone_forensics import __version__  # noqa: E402

PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build" / "pyinstaller"
ARCH = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), "x64")

APPS = [
    # (entry script, output name, windowed, description, internal name)
    ("cpf_gui.py", f"ConnectedPhoneForensics-{__version__}-windows-{ARCH}", True,
     "Connected Phone Forensics", "ConnectedPhoneForensics"),
    ("cpf_cli.py", f"cpf-{__version__}-windows-{ARCH}", False,
     "Connected Phone Forensics command line", "cpf"),
]

# Qt modules the app never uses; leaving them out keeps the download small.
QT_EXCLUDES = ["QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtOpenGL", "QtOpenGLWidgets", "QtPdf",
               "QtPdfWidgets", "QtSql", "QtTest", "QtXml", "QtDBus", "QtDesigner", "QtHelp", "QtMultimedia",
               "QtWebEngineCore", "QtWebEngineWidgets", "QtConcurrent", "QtUiTools", "Qt3DCore"]


def version_file(name: str, description: str, internal: str) -> Path:
    parts = [int(p) for p in __version__.split(".")[:3] if p.isdigit()]
    parts += [0] * (4 - len(parts))
    v = tuple(parts[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Connected Phone Forensics'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{internal}'),
      StringStruct('LegalCopyright', 'MIT License. Copyright (c) 2026 Connected Phone Forensics contributors'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'Connected Phone Forensics'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = BUILD / f"{internal}-version.txt"
    path.write_text(text, encoding="utf-8")
    return path


def build(dist: Path) -> list[Path]:
    import PyInstaller.__main__

    BUILD.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    out = []
    for entry, name, windowed, description, internal in APPS:
        args = [
            str(PACKAGING / entry), "--onefile", "--noconfirm", "--clean",
            "--name", name, "--icon", str(PACKAGING / "app.ico"),
            "--version-file", str(version_file(name, description, internal)),
            "--distpath", str(dist), "--workpath", str(BUILD / internal), "--specpath", str(BUILD),
            "--paths", str(ROOT),
            # Parsers are found at run time (pkgutil), so PyInstaller cannot see their imports.
            "--collect-submodules", "connected_phone_forensics.plugins",
            "--exclude-module", "tkinter", "--exclude-module", "pytest", "--exclude-module", "ruff",
        ]
        if windowed:
            args += ["--windowed", "--collect-data", "qtawesome",
                     "--collect-submodules", "connected_phone_forensics.gui"]
            for mod in QT_EXCLUDES:
                args += ["--exclude-module", f"PySide6.{mod}"]
        else:
            args += ["--console"]
            for mod in ("PySide6", "shiboken6", "qtawesome", "qtpy"):
                args += ["--exclude-module", mod]
        PyInstaller.__main__.run(args)
        out.append(dist / f"{name}.exe")
    return out


def checksums(files: list[Path]) -> Path:
    lines = []
    for f in files:
        h = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append(f"{h}  {f.name}")
    path = files[0].parent / "SHA256SUMS.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    if sys.platform != "win32":
        print("packaging/build.py builds the Windows executables; run it on Windows.", file=sys.stderr)
        return 2
    dist = ROOT / "dist"
    for old in dist.glob("*.exe"):
        old.unlink()
    files = build(dist)
    sums = checksums(files)
    shutil.rmtree(BUILD, ignore_errors=True)
    for f in files:
        print(f"{f.name}: {f.stat().st_size / 1e6:.1f} MB")
    print(sums.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
