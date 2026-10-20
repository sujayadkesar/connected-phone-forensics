"""Windows calls for live acquisition (ctypes; importable only on Windows).

- the backup privilege, which lets an administrator read any file or folder whatever its ACL;
- RegSaveKeyEx, which writes a consistent copy of a registry hive that Windows has locked
  (the API behind "reg save");
- CreateFile / GetFileInformationByHandleEx with FILE_FLAG_BACKUP_SEMANTICS, to read files
  and list folders the ACL would otherwise deny;
- SetFileTime, to give copies the original creation, access and modification times;
- drive information, and restarting the app as administrator (UAC).
"""

from __future__ import annotations

import ctypes
import msvcrt
import os
import struct
import sys
import winreg
from ctypes import wintypes as wt
from dataclasses import dataclass
from pathlib import Path

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
GENERIC_READ = 0x80000000
FILE_LIST_DIRECTORY = 0x0001
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
FILE_SHARE_ALL = 0x1 | 0x2 | 0x4
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x10
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
ERROR_NO_MORE_FILES = 18
ERROR_NOT_ALL_ASSIGNED = 1300
FileIdBothDirectoryInfo = 10
FileIdBothDirectoryRestartInfo = 11
REG_LATEST_FORMAT = 2
REG_NO_COMPRESSION = 4
DRIVE_TYPES = {0: "unknown", 1: "no root", 2: "removable", 3: "fixed", 4: "network", 5: "CD/DVD", 6: "RAM disk"}

kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetFileInformationByHandleEx.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
kernel32.GetFileInformationByHandleEx.restype = wt.BOOL
kernel32.SetFileTime.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
kernel32.GetCurrentProcess.restype = wt.HANDLE
kernel32.GetDriveTypeW.argtypes = [wt.LPCWSTR]
kernel32.GetLogicalDrives.restype = wt.DWORD
kernel32.SetErrorMode.argtypes = [wt.UINT]
kernel32.SetErrorMode.restype = wt.UINT
kernel32.GetVolumePathNameW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
kernel32.GetVolumeInformationW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                           ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD), wt.LPWSTR, wt.DWORD]
kernel32.GetDiskFreeSpaceExW.argtypes = [wt.LPCWSTR, ctypes.POINTER(ctypes.c_ulonglong),
                                         ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong)]
advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
advapi32.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
advapi32.AdjustTokenPrivileges.argtypes = [wt.HANDLE, wt.BOOL, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                           ctypes.c_void_p]
advapi32.RegSaveKeyExW.argtypes = [wt.HANDLE, wt.LPCWSTR, ctypes.c_void_p, wt.DWORD]
advapi32.RegSaveKeyExW.restype = wt.LONG
shell32.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
shell32.ShellExecuteW.restype = ctypes.c_void_p


class LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class LUID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = [("Luid", LUID), ("Attributes", wt.DWORD)]


class TOKEN_PRIVILEGES(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]


def _error(what: str, path: str | None = None) -> OSError:
    code = ctypes.get_last_error()
    return OSError(None, f"{what}: {ctypes.FormatError(code).strip()} (Windows error {code})", path, code)


def is_admin() -> bool:
    """True when the process runs elevated (as administrator)."""
    try:
        return bool(shell32.IsUserAnAdmin())
    except OSError:
        return False


def enable_privilege(name: str) -> bool:
    """Enable a privilege the token holds (e.g. SeBackupPrivilege); False if it does not hold it."""
    token = wt.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)):
        raise _error("OpenProcessToken")
    try:
        luid = LUID()
        if not advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            raise _error(f"LookupPrivilegeValue {name}")
        tp = TOKEN_PRIVILEGES(1, (LUID_AND_ATTRIBUTES * 1)(LUID_AND_ATTRIBUTES(luid, 0x2)))
        if not advapi32.AdjustTokenPrivileges(token, False, ctypes.byref(tp), 0, None, None):
            raise _error(f"AdjustTokenPrivileges {name}")
        return ctypes.get_last_error() != ERROR_NOT_ALL_ASSIGNED
    finally:
        kernel32.CloseHandle(token)


def reg_save(root: int, subkey: str, dest: Path) -> str:
    """Save the hive rooted at ``root\\subkey`` to ``dest`` (which must not exist); returns the method."""
    with winreg.OpenKeyEx(root, subkey, 0, winreg.KEY_READ) as key:
        handle = wt.HANDLE(int(key))
        for flags, how in ((REG_NO_COMPRESSION, "uncompressed"), (REG_LATEST_FORMAT, "latest format")):
            rc = advapi32.RegSaveKeyExW(handle, str(dest), None, flags)
            if rc == 0:
                return f"RegSaveKeyEx ({how})"
            if dest.exists():
                dest.unlink()
        raise OSError(None, f"RegSaveKeyEx failed: {ctypes.FormatError(rc).strip()} (error {rc})", str(dest), rc)


def _open_handle(path: str, access: int, flags: int) -> int:
    h = kernel32.CreateFileW(path, access, FILE_SHARE_ALL, None, OPEN_EXISTING, flags, None)
    if h is None or h == INVALID_HANDLE_VALUE:
        raise _error("CreateFile", path)
    return h


def open_backup(path: str):
    """Open a file for reading with backup semantics (ignores its ACL when the privilege is enabled)."""
    h = _open_handle(path, GENERIC_READ, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_SEQUENTIAL_SCAN)
    try:
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
    except OSError:
        kernel32.CloseHandle(h)
        raise
    return os.fdopen(fd, "rb")


@dataclass
class Entry:
    name: str
    is_dir: bool
    is_link: bool
    size: int
    created: int   # FILETIME ticks (100 ns since 1601)
    accessed: int
    modified: int


def list_dir_backup(path: str) -> list[Entry]:
    """List a folder with backup semantics (works where FindFirstFile is denied)."""
    h = _open_handle(path, FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES, FILE_FLAG_BACKUP_SEMANTICS)
    out: list[Entry] = []
    buf = ctypes.create_string_buffer(64 * 1024)
    cls = FileIdBothDirectoryRestartInfo
    try:
        while True:
            if not kernel32.GetFileInformationByHandleEx(h, cls, buf, len(buf)):
                if ctypes.get_last_error() == ERROR_NO_MORE_FILES:
                    return out
                raise _error("GetFileInformationByHandleEx", path)
            cls = FileIdBothDirectoryInfo
            off = 0
            raw = buf.raw
            while True:
                (nxt, _idx, created, accessed, modified, _changed, size, _alloc, attrs,
                 name_len) = struct.unpack_from("<IIqqqqqqII", raw, off)
                name = raw[off + 104: off + 104 + name_len].decode("utf-16-le")
                if name not in (".", ".."):
                    out.append(Entry(name, bool(attrs & FILE_ATTRIBUTE_DIRECTORY),
                                     bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT), size, created, accessed, modified))
                if not nxt:
                    break
                off += nxt
    finally:
        kernel32.CloseHandle(h)


def set_times(path: str, created: int, accessed: int, modified: int) -> None:
    """Give a file or folder copy its original times (FILETIME ticks)."""
    h = _open_handle(path, FILE_WRITE_ATTRIBUTES, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
    try:
        c, a, m = (ctypes.c_ulonglong(t) for t in (created, accessed, modified))
        if not kernel32.SetFileTime(h, ctypes.byref(c), ctypes.byref(a), ctypes.byref(m)):
            raise _error("SetFileTime", path)
    finally:
        kernel32.CloseHandle(h)


def file_times(path: str) -> tuple[int, int, int, int]:
    """(created, accessed, modified, size) of a file or folder, read with backup semantics."""
    h = _open_handle(path, FILE_READ_ATTRIBUTES, FILE_FLAG_BACKUP_SEMANTICS)
    try:
        buf = ctypes.create_string_buffer(40)  # FILE_BASIC_INFO
        if not kernel32.GetFileInformationByHandleEx(h, 0, buf, len(buf)):
            raise _error("GetFileInformationByHandleEx", path)
        created, accessed, modified, _changed = struct.unpack_from("<qqqq", buf.raw, 0)
        std = ctypes.create_string_buffer(24)  # FILE_STANDARD_INFO
        size = 0
        if kernel32.GetFileInformationByHandleEx(h, 1, std, len(std)):
            size = struct.unpack_from("<q", std.raw, 8)[0]
        return created, accessed, modified, size
    finally:
        kernel32.CloseHandle(h)


@dataclass
class DriveInfo:
    root: str
    kind: str
    filesystem: str
    serial: str
    label: str
    free_bytes: int


def drive_info(path: str) -> DriveInfo:
    buf = ctypes.create_unicode_buffer(1024)
    if not kernel32.GetVolumePathNameW(str(path), buf, len(buf)):
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
    else:
        root = buf.value
    kind = DRIVE_TYPES.get(kernel32.GetDriveTypeW(root), "unknown")
    name, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    serial = wt.DWORD()
    ok = kernel32.GetVolumeInformationW(root, name, 261, ctypes.byref(serial), None, None, fs, 261)
    free = ctypes.c_ulonglong()
    kernel32.GetDiskFreeSpaceExW(root, ctypes.byref(free), None, None)
    return DriveInfo(root, kind, fs.value if ok else "", f"{serial.value:08X}" if ok else "",
                     name.value if ok else "", free.value)


def drives() -> list[DriveInfo]:
    """Every drive letter in use, with its type, file system, label and free space."""
    out = []
    old = kernel32.SetErrorMode(0x0001)  # no "insert a disk" dialogs for empty card readers
    try:
        mask = kernel32.GetLogicalDrives()
        for i in range(26):
            if mask & (1 << i):
                root = f"{chr(65 + i)}:\\"
                if DRIVE_TYPES.get(kernel32.GetDriveTypeW(root)) in ("removable", "fixed"):
                    info = drive_info(root)
                    if info.filesystem:  # skips empty card readers and unformatted volumes
                        out.append(info)
    finally:
        kernel32.SetErrorMode(old)
    return out


def relaunch_elevated(args: list[str], module: str = "connected_phone_forensics.gui") -> bool:
    """Start this program again as administrator (Windows shows the UAC prompt); True if it started."""
    frozen = getattr(sys, "frozen", False)
    exe = sys.executable
    params = args if frozen else ["-m", module, *args]
    rc = shell32.ShellExecuteW(None, "runas", exe, " ".join(_quote(a) for a in params), None, 1)
    return (rc or 0) > 32


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) and not arg.startswith('"') else arg


def computer_name() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName") as k:
            return str(winreg.QueryValueEx(k, "ComputerName")[0])
    except OSError:
        return os.environ.get("COMPUTERNAME", "")


def windows_version() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            def val(name):
                try:
                    return str(winreg.QueryValueEx(k, name)[0])
                except OSError:
                    return ""
            build = val("CurrentBuild")
            product = val("ProductName")
            if build.isdigit() and int(build) >= 22000:
                product = product.replace("Windows 10", "Windows 11")
            return " ".join(p for p in (product, val("DisplayVersion"), f"build {build}" if build else "") if p)
    except OSError:
        return ""


def user_profiles() -> list[tuple[str, str]]:
    """(SID, profile folder) for every local and domain/Entra user profile on this computer."""
    out = []
    base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
        i = 0
        while True:
            try:
                sid = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            if not sid.startswith(("S-1-5-21-", "S-1-12-1-")):
                continue  # service accounts (S-1-5-18/19/20) have no user evidence
            try:
                with winreg.OpenKey(k, sid) as sk:
                    folder = os.path.expandvars(str(winreg.QueryValueEx(sk, "ProfileImagePath")[0]))
                out.append((sid, folder))
            except OSError:
                continue
    return out


def loaded_user_hives() -> set[str]:
    """SIDs whose NTUSER.DAT is loaded under HKEY_USERS (users signed in now)."""
    sids, i = set(), 0
    while True:
        try:
            name = winreg.EnumKey(winreg.HKEY_USERS, i)
        except OSError:
            return sids
        i += 1
        if name.startswith(("S-1-5-21-", "S-1-12-1-")) and not name.endswith("_Classes"):
            sids.add(name)
