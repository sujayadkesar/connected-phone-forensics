"""Windows registry hive access via regipy, with transaction-log recovery.

Hives copied from a live or crashed system are often "dirty": recent writes
sit in the ``.LOG1``/``.LOG2`` transaction logs. When logs are present the
hive is copied to a work directory and the logs are applied to the copy,
so the evidence is never modified and recent keys are not missed.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import timeutil


@dataclass
class RegValue:
    name: str
    value: Any
    type: str


@dataclass
class RegKey:
    path: str
    name: str
    last_written: datetime | None
    values: dict[str, RegValue]
    _hive: Any = None
    _nk: Any = None

    def get(self, name: str, default: Any = None) -> Any:
        v = self.values.get(name)
        if v is None:
            # value names are case-insensitive in the registry
            for k, val in self.values.items():
                if k.lower() == name.lower():
                    return val.value
            return default
        return v.value


class Hive:
    def __init__(self, registry_hive: Any, source: Path, recovered: bool):
        self._h = registry_hive
        self.source = source
        self.recovered = recovered  # transaction logs were applied

    def key(self, path: str) -> RegKey | None:
        try:
            nk = self._h.get_key(path)
        except Exception:  # noqa: BLE001 - regipy raises its own types for missing keys
            return None
        return self._wrap(nk, path)

    def subkeys(self, path: str) -> Iterator[RegKey]:
        try:
            nk = self._h.get_key(path)
        except Exception:  # noqa: BLE001
            return
        base = path.rstrip("\\")
        for sk in nk.iter_subkeys():
            yield self._wrap(sk, f"{base}\\{sk.name}")

    def walk(self, path: str, max_depth: int = 8) -> Iterator[RegKey]:
        """Yield the key at ``path`` and its descendants."""
        k = self.key(path)
        if k is None:
            return
        stack = [(k, 0)]
        while stack:
            cur, depth = stack.pop()
            yield cur
            if depth < max_depth:
                for sk in self.subkeys(cur.path):
                    stack.append((sk, depth + 1))

    def _wrap(self, nk: Any, path: str) -> RegKey:
        values: dict[str, RegValue] = {}
        try:
            # trim_values=False: regipy truncates long values by default, which
            # would silently cut binary data such as Bluetooth names.
            for v in nk.iter_values(as_json=False, trim_values=False):
                values[v.name] = RegValue(v.name, v.value, str(v.value_type))
        except Exception:  # noqa: BLE001 - keep partial values from damaged keys
            pass
        lw = getattr(nk.header, "last_modified", None)
        return RegKey(path=path, name=nk.name, last_written=timeutil.from_filetime(lw),
                      values=values, _hive=self, _nk=nk)


def control_set_paths(system: Hive) -> list[str]:
    """ControlSet roots of a SYSTEM hive, current set first."""
    sets = []
    sel = system.key("\\Select")
    current = sel.get("Current") if sel else None
    if isinstance(current, int):
        sets.append(f"\\ControlSet{current:03d}")
    root = system.key("\\")
    if root:
        for sk in system.subkeys("\\"):
            if sk.name.lower().startswith("controlset") and f"\\{sk.name}" not in sets:
                sets.append(f"\\{sk.name}")
    return sets


@contextmanager
def open_hive(path: Path, workdir: Path | None = None) -> Iterator[Hive]:
    from regipy.registry import RegistryHive

    path = Path(path)
    logs = [path.with_name(path.name + s) for s in (".LOG1", ".LOG2", ".LOG")]
    if not any(p.is_file() for p in logs):  # transaction logs are often lower-case on disk
        logs = [p for p in path.parent.iterdir()
                if p.name.lower() in {f"{path.name.lower()}{s}" for s in (".log1", ".log2", ".log")}]
    logs = [p for p in logs if p.is_file() and p.stat().st_size > 0]
    if not logs:
        yield Hive(RegistryHive(str(path)), path, recovered=False)
        return
    tmp = Path(tempfile.mkdtemp(prefix="tether-hive-", dir=workdir))
    try:
        hive_copy = tmp / path.name
        shutil.copyfile(path, hive_copy)
        recovered = False
        target = hive_copy
        try:
            from regipy.recovery import apply_transaction_logs

            log1 = next((str(p) for p in logs if p.name.upper().endswith(".LOG1")), None)
            log2 = next((str(p) for p in logs if p.name.upper().endswith(".LOG2")), None)
            out = tmp / (path.name + ".recovered")
            restored, _count = apply_transaction_logs(
                str(hive_copy), primary_log_path=log1 or str(logs[0]),
                secondary_log_path=log2, restored_hive_path=str(out),
            )
            target = Path(restored)
            recovered = True
        except Exception:  # noqa: BLE001 - fall back to the hive as-is
            target = hive_copy
        yield Hive(RegistryHive(str(target)), path, recovered=recovered)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
