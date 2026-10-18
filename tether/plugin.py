"""Plugin API.

A plugin knows one artifact family on one operating system. It declares the
paths it reads (used for documentation and for generating collection targets)
and implements :meth:`Plugin.run`, emitting observations through the
:class:`Context`. Plugins never open evidence files directly; they go through
the context so files are hashed, databases are copied before opening, and
every file touched gets a coverage record.
"""

from __future__ import annotations

import hashlib
import importlib
import pkgutil
import re
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .evidence import SystemRoot
from .model import Channel, Observation, Provenance, Validation
from .readers import plist as plistreader
from .readers.registry import Hive, open_hive
from .readers.sqlite import SafeDB, open_db

# Coverage statuses
PARSED = "parsed"              # file recognised and parsed
EMPTY = "empty"                # file recognised, no records
UNRECOGNISED = "unrecognised"  # file present but its format/schema is not one we know
ERROR = "error"                # parser failed on this file


@dataclass
class CoverageEntry:
    plugin: str
    system: str
    source_path: str
    status: str
    records: int = 0
    note: str = ""


@dataclass
class Warning_:
    plugin: str
    system: str
    message: str
    source_path: str | None = None


class Plugin:
    id: ClassVar[str] = ""
    name: ClassVar[str] = ""
    os: ClassVar[str] = ""                 # "windows" | "macos" | "linux"
    channel: ClassVar[Channel] = Channel.OTHER
    description: ClassVar[str] = ""
    paths: ClassVar[list[str]] = []         # glob patterns relative to the system root
    references: ClassVar[list[str]] = []
    validation: ClassVar[Validation] = Validation.SYNTHETIC
    version: ClassVar[str] = "1"

    def run(self, ctx: Context) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def meta(cls) -> dict[str, Any]:
        return {
            "id": cls.id,
            "name": cls.name,
            "os": cls.os,
            "channel": cls.channel.value,
            "description": cls.description,
            "paths": list(cls.paths),
            "references": list(cls.references),
            "validation": cls.validation.value,
            "version": cls.version,
        }


_REGISTRY: dict[str, type[Plugin]] = {}


def register(cls: type[Plugin]) -> type[Plugin]:
    if not cls.id:
        raise ValueError(f"{cls.__name__} has no id")
    if cls.id in _REGISTRY and _REGISTRY[cls.id] is not cls:
        raise ValueError(f"duplicate plugin id {cls.id}")
    _REGISTRY[cls.id] = cls
    return cls


def load_plugins() -> dict[str, type[Plugin]]:
    """Import every module under tether.plugins so their @register calls run."""
    from . import plugins as pkg

    for mod in pkgutil.walk_packages(pkg.__path__, prefix=pkg.__name__ + "."):
        importlib.import_module(mod.name)
    return dict(sorted(_REGISTRY.items()))


class Context:
    """Everything a plugin may use while it runs against one system."""

    def __init__(self, plugin: Plugin, system: SystemRoot, workdir: Path | None = None,
                 export_dir: Path | None = None):
        self.plugin = plugin
        self.system = system
        self.fs = system.fs
        self.workdir = workdir
        self.export_dir = export_dir
        self.observations: list[Observation] = []
        self.coverage: list[CoverageEntry] = []
        self.warnings: list[Warning_] = []

    # -- locating ----------------------------------------------------------

    def files(self, pattern: str) -> list[Path]:
        return self.fs.files(pattern)

    def user_files(self, pattern: str) -> Iterator[tuple[str, Path]]:
        """Match ``pattern`` inside each user profile; yields (user name, path)."""
        for user in self.system.users:
            rel_home = self.fs.rel(user.home)
            for p in self.fs.files(f"{_glob_escape(rel_home)}/{pattern}"):
                yield user.name, p

    def user_dirs(self, pattern: str) -> Iterator[tuple[str, Path]]:
        for user in self.system.users:
            rel_home = self.fs.rel(user.home)
            for p in self.fs.dirs(f"{_glob_escape(rel_home)}/{pattern}"):
                yield user.name, p

    # -- reading -----------------------------------------------------------

    @contextmanager
    def sqlite(self, path: Path) -> Iterator[SafeDB]:
        self.fs.sha256(path)
        for suffix in ("-wal", "-shm", "-journal"):  # sidecars are evidence too
            side = Path(path).with_name(Path(path).name + suffix)
            if side.is_file():
                self.fs.sha256(side)
        with open_db(path, self.workdir) as db:
            yield db

    @contextmanager
    def hive(self, path: Path) -> Iterator[Hive]:
        self.fs.sha256(path)
        for side in Path(path).parent.iterdir():  # transaction logs are evidence too
            if side.name.lower().startswith(Path(path).name.lower() + ".log") and side.is_file():
                self.fs.sha256(side)
        with open_hive(path, self.workdir) as h:
            yield h

    def plist(self, path: Path) -> Any:
        self.fs.sha256(path)
        return plistreader.load(path)

    def read_bytes(self, path: Path) -> bytes:
        self.fs.sha256(path)
        return self.fs.read_bytes(path)

    def read_text(self, path: Path, encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding, errors="replace")

    def export(self, name: str, data: bytes) -> dict[str, Any]:
        """Save content recovered from evidence (e.g. a cached photo) for the examiner.

        Files go to ``<export dir>/<system>/<plugin>/<name>``; returns the
        relative path (None when exporting is off), SHA-256 and size so the
        observation records exactly what was written. Name components are
        sanitised, so evidence-controlled names cannot escape the export dir.
        """
        digest = hashlib.sha256(data).hexdigest()
        rel = None
        if self.export_dir is not None:
            safe_sys = re.sub(r"[^A-Za-z0-9._-]+", "_", self.system.label)
            parts = [re.sub(r"[^A-Za-z0-9._ -]+", "_", p).strip(" .")[:120] or "_"
                     for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
            target = Path(self.export_dir, safe_sys, self.plugin.id, *parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            rel = Path(safe_sys, self.plugin.id, *parts).as_posix()
        return {"exported_path": rel, "sha256": digest, "size": len(data)}

    # -- reporting -----------------------------------------------------------

    def prov(self, path: Path, locator: str) -> Provenance:
        return Provenance(
            source_path=self.fs.rel(path),
            locator=locator,
            parser=self.plugin.id,
            sha256=self.fs.sha256(path),
        )

    def emit(self, obs: Observation) -> None:
        obs.system = self.system.label
        if obs.user is None:
            obs.user = self.system.user_for(obs.provenance.source_path)
        self.observations.append(obs)

    def cover(self, path: Path, status: str, records: int = 0, note: str = "") -> None:
        self.coverage.append(CoverageEntry(self.plugin.id, self.system.label,
                                           self.fs.rel(path), status, records, note))

    def warn(self, message: str, path: Path | None = None) -> None:
        self.warnings.append(Warning_(self.plugin.id, self.system.label, message,
                                      self.fs.rel(path) if path else None))

    @contextmanager
    def file_scope(self, path: Path) -> Iterator[list[int]]:
        """Run per-file parsing; records coverage and contains exceptions to this file.

        Usage::

            with ctx.file_scope(path) as count:
                ...emit observations...
                count[0] += n

        If the block emits nothing and records no coverage, the file is marked EMPTY.
        Exceptions are recorded as ERROR coverage and do not stop the plugin.
        """
        before_obs = len(self.observations)
        before_cov = len(self.coverage)
        counter = [0]
        try:
            yield counter
        except Exception as exc:  # noqa: BLE001 - isolate failures to one file
            self.cover(path, ERROR, note=f"{type(exc).__name__}: {exc}")
            self.warn(f"failed to parse: {type(exc).__name__}: {exc}\n"
                      f"{traceback.format_exc(limit=3)}", path)
            return
        if len(self.coverage) == before_cov:
            produced = counter[0] or (len(self.observations) - before_obs)
            self.cover(path, PARSED if produced else EMPTY, produced)


def _glob_escape(text: str) -> str:
    out = []
    for ch in text:
        out.append(f"[{ch}]" if ch in "*?[" else ch)
    return "".join(out)
