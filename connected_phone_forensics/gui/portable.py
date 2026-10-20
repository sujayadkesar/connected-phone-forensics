"""Portable mode: run from a USB drive without leaving settings on the computer being examined.

The packaged app is portable when it runs from a drive other than the system drive, or when a
file named ``portable.txt`` sits next to it. Then:

- settings go to ``cpf-settings.ini`` next to the program instead of the user's registry;
- folder dialogs are Qt's own: Windows' dialogs add the folder to the user's recently-used
  lists (ComDlg32 MRUs), which are evidence and hold a limited number of entries.

``CPF_PORTABLE=1`` turns it on when running from source (for testing).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SETTINGS_FILE = "cpf-settings.ini"


def program_dir() -> Path | None:
    """The folder of the packaged program (None when running from source)."""
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None


def is_one_file() -> bool:
    """True for the single-file build, which unpacks itself into the temporary folder to run."""
    here = program_dir()
    unpacked = getattr(sys, "_MEIPASS", None)
    return bool(here and unpacked and Path(unpacked).resolve() != here)


def is_portable() -> bool:
    if os.environ.get("CPF_PORTABLE") == "1":
        return True
    here = program_dir()
    if here is None:
        return False
    if (here / "portable.txt").exists():
        return True
    if sys.platform == "win32":
        return here.drive.lower() != os.environ.get("SYSTEMDRIVE", "C:").lower()
    return False


def settings(organization: str, application: str):
    from PySide6.QtCore import QSettings

    if is_portable():
        base = program_dir() or Path.cwd()
        return QSettings(str(base / SETTINGS_FILE), QSettings.Format.IniFormat)
    return QSettings(organization, application)
