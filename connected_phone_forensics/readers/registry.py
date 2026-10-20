"""Windows registry hive access via regipy, with transaction-log recovery.

Hives copied from a live or crashed system are often "dirty": recent writes
sit in the ``.LOG1``/``.LOG2`` transaction logs. Logs are also left behind on
clean hives, full of entries that were flushed long ago, so
:mod:`connected_phone_forensics.readers.regf_log` decides what to apply. Recovery happens on a
copy in a work directory; the evidence is never modified.
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
from ..evidence import describe_error
from .regf_log import APPLIED, NOT_APPLIED, LogResult, Replay, replay


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
    def __init__(self, registry_hive: Any, source: Path, replay: Replay | None = None,
                 logs: list[Path] | None = None):
        self._h = registry_hive
        self.source = source
        self.replay = replay      # what was done with the transaction logs; None when there are none
        self.logs = logs or []    # the transaction-log files next to the hive, in replay.logs order

    @property
    def recovered(self) -> bool:
        """Transaction-log entries were applied to the copy being read."""
        return self.replay is not None and self.replay.status == APPLIED

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


def transaction_logs(path: Path) -> list[Path]:
    """Non-empty ``.LOG``/``.LOG1``/``.LOG2`` files of a hive; names are matched case-insensitively."""
    path = Path(path)
    wanted = {f"{path.name.lower()}{s}" for s in (".log", ".log1", ".log2")}
    try:
        found = [p for p in path.parent.iterdir() if p.name.lower() in wanted and p.is_file()]
    except OSError:
        return []
    return sorted((p for p in found if p.stat().st_size > 0), key=lambda p: p.name.lower())


@contextmanager
def open_hive(path: Path, workdir: Path | None = None) -> Iterator[Hive]:
    from regipy.registry import RegistryHive

    path = Path(path)
    logs = transaction_logs(path)
    if not logs:
        yield Hive(RegistryHive(str(path)), path)
        return
    try:
        result = replay(path.read_bytes(), [(p.name, p.read_bytes()) for p in logs])
    except Exception as exc:  # noqa: BLE001 - a damaged log must not hide the hive itself
        result = Replay(NOT_APPLIED, summary=f"transaction-log replay failed: {type(exc).__name__}",
                        logs=[LogResult(p.name, note="not applied: replay failed") for p in logs],
                        warnings=[f"transaction-log replay failed ({describe_error(exc)}); "
                                  "the hive was read without its logs"])
    if result.data is None:
        yield Hive(RegistryHive(str(path)), path, result, logs)
        return
    tmp = Path(tempfile.mkdtemp(prefix="cpf-hive-", dir=workdir))
    try:
        recovered = tmp / (path.name + ".recovered")
        recovered.write_bytes(result.data)
        result.data = None  # the copy on disk is what gets read; free the memory
        yield Hive(RegistryHive(str(recovered)), path, result, logs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
