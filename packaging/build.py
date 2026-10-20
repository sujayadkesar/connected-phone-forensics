"""Build the Windows executables with PyInstaller.

    pip install -e ".[build]"
    python packaging/build.py            # -> dist/
    python packaging/smoke_test.py dist  # run them and check they work

Writes to dist/:

- ConnectedPhoneForensics-<v>-windows-x64.exe: the desktop app, one file (no console window);
- cpf-<v>-windows-x64.exe: the command-line tool, one file;
- ConnectedPhoneForensics-<v>-windows-x64-portable.zip: both programs in one folder, for a USB
  drive. A one-file program unpacks itself into the temporary folder of the system drive each
  time it runs; the portable folder runs in place, so a live acquisition writes nothing to the
  drive being examined. A portable.txt marker keeps the app's settings next to it.

SHA256SUMS.txt lists every file.
"""

from __future__ import annotations

import hashlib
import platform
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from connected_phone_forensics import __version__  # noqa: E402

PACKAGING = ROOT / "packaging"
BUILD = ROOT / "build" / "pyinstaller"
ARCH = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower(), "x64")
GUI_NAME = f"ConnectedPhoneForensics-{__version__}-windows-{ARCH}"
CLI_NAME = f"cpf-{__version__}-windows-{ARCH}"
PORTABLE_DIR = "ConnectedPhoneForensics"
PORTABLE_ZIP = f"ConnectedPhoneForensics-{__version__}-windows-{ARCH}-portable.zip"

APPS = [
    # (entry script, output name, windowed, description, internal name)
    ("cpf_gui.py", GUI_NAME, True, "Connected Phone Forensics", "ConnectedPhoneForensics"),
    ("cpf_cli.py", CLI_NAME, False, "Connected Phone Forensics command line", "cpf"),
]

# Qt modules the tool never uses; leaving them out keeps the download small. The command line
# keeps QtGui and QtSvg: it draws the PDF summary.
QT_EXCLUDES = ["QtNetwork", "QtQml", "QtQuick", "QtQuickWidgets", "QtOpenGL", "QtOpenGLWidgets", "QtPdf",
               "QtPdfWidgets", "QtSql", "QtTest", "QtXml", "QtDBus", "QtDesigner", "QtHelp", "QtMultimedia",
               "QtWebEngineCore", "QtWebEngineWidgets", "QtConcurrent", "QtUiTools", "Qt3DCore"]
EXCLUDES = ["tkinter", "pytest", "ruff", "pypdf", "PIL", *(f"PySide6.{m}" for m in QT_EXCLUDES)]  # PIL: optional in openpyxl

PORTABLE_README = """Connected Phone Forensics {version}, portable

Run it from this folder, for example on a USB drive:

  ConnectedPhoneForensics.exe   the desktop app
  cpf.exe                       the command line (cpf --help)

Nothing is unpacked to the computer's temporary folder, and the app keeps its settings in
cpf-settings.ini in this folder (because of portable.txt), not in the computer's registry.

Live acquisition of the computer the program runs on: run as administrator, with the output on
another drive than the one examined (this USB drive or an external disk):

  cpf.exe live -o E:\\cases\\2026-0142 --case 2026-0142 --examiner "Your name"

or, in the app, New scan > This computer (live).

https://github.com/sujayadkesar/connected-phone-forensics
"""


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


def build_one_file(dist: Path) -> list[Path]:
    import PyInstaller.__main__

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
            # Logo, style-sheet SVGs and the MAC-vendor table.
            "--collect-data", "connected_phone_forensics",
        ]
        for mod in EXCLUDES:
            args += ["--exclude-module", mod]
        if windowed:
            args += ["--windowed", "--collect-data", "qtawesome",
                     "--collect-submodules", "connected_phone_forensics.gui"]
        else:
            args += ["--console", "--exclude-module", "qtawesome", "--exclude-module", "qtpy"]
        PyInstaller.__main__.run(args)
        out.append(dist / f"{name}.exe")
    return out


def build_portable(dist: Path) -> Path:
    """Both programs in one folder sharing their libraries, zipped."""
    import PyInstaller.__main__

    gui_v = version_file("ConnectedPhoneForensics", "Connected Phone Forensics", "portable-gui")
    cli_v = version_file("cpf", "Connected Phone Forensics command line", "portable-cli")
    spec = BUILD / "portable.spec"
    spec.write_text(f"""# Generated by packaging/build.py
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = collect_submodules("connected_phone_forensics.plugins") + collect_submodules("connected_phone_forensics.gui")
datas = collect_data_files("connected_phone_forensics") + collect_data_files("qtawesome")
excludes = {EXCLUDES!r}

gui = Analysis([{str(PACKAGING / "cpf_gui.py")!r}], pathex=[{str(ROOT)!r}], hiddenimports=hidden, datas=datas,
               excludes=excludes)
cli = Analysis([{str(PACKAGING / "cpf_cli.py")!r}], pathex=[{str(ROOT)!r}], hiddenimports=hidden, datas=datas,
               excludes=excludes)
gui_exe = EXE(PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="ConnectedPhoneForensics",
              console=False, icon={str(PACKAGING / "app.ico")!r}, version={str(gui_v)!r})
cli_exe = EXE(PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="cpf",
              console=True, icon={str(PACKAGING / "app.ico")!r}, version={str(cli_v)!r})
COLLECT(gui_exe, gui.binaries, gui.datas, cli_exe, cli.binaries, cli.datas, name={PORTABLE_DIR!r})
""", encoding="utf-8")
    folder_dist = BUILD / "portable-dist"
    PyInstaller.__main__.run([str(spec), "--noconfirm", "--clean", "--distpath", str(folder_dist),
                              "--workpath", str(BUILD / "portable")])
    folder = folder_dist / PORTABLE_DIR
    (folder / "portable.txt").write_text("Keep the app's settings in this folder (portable mode).\n",
                                         encoding="utf-8")
    (folder / "README.txt").write_text(PORTABLE_README.format(version=__version__), encoding="utf-8")
    target = dist / PORTABLE_ZIP
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file():
                z.write(f, f"{PORTABLE_DIR}/{f.relative_to(folder).as_posix()}")
    return target


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
    for old in [*dist.glob("*.exe"), *dist.glob("*.zip")]:
        old.unlink()
    BUILD.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    files = build_one_file(dist)
    files.append(build_portable(dist))
    sums = checksums(files)
    shutil.rmtree(BUILD, ignore_errors=True)
    for f in files:
        print(f"{f.name}: {f.stat().st_size / 1e6:.1f} MB")
    print(sums.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
