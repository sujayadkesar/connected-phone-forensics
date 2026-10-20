"""Scan orchestration: find systems, run plugins, correlate devices."""

from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .correlate import DeviceGraph, build_graph
from .evidence import SystemRoot, discover_systems
from .model import Channel, Identifier, IdKind, Observation, Provenance, Strength
from .plugin import ERROR, Context, CoverageEntry, Plugin, Warning_, load_plugins


@dataclass
class ScanResult:
    input_path: str
    started: datetime
    finished: datetime
    systems: list[SystemRoot]
    observations: list[Observation]
    coverage: list[CoverageEntry]
    warnings: list[Warning_]
    graph: DeviceGraph
    plugins_run: list[dict]
    tool_version: str = __version__
    case: dict[str, str] = field(default_factory=dict)
    assertions: list[list[str]] = field(default_factory=list)
    acquisition: dict | None = None  # live acquisition summary (see connected_phone_forensics.live)


def parse_assertion(text: str) -> list[Identifier]:
    """'udid=00008120...,bt_mac=a4:c3:...' -> identifiers; only strong kinds may be asserted."""
    ids = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        kind, sep, value = part.partition("=")
        if not sep or not value:
            raise ValueError(f"expected KIND=VALUE, got {part!r}")
        try:
            k = IdKind(kind.strip())
        except ValueError as exc:
            raise ValueError(f"unknown identifier kind {kind!r}") from exc
        ident_ = Identifier(k, value.strip())
        if ident_.strength != Strength.STRONG:
            raise ValueError(f"{kind} is not a strong identifier and cannot be asserted; use udid, serial, imei, "
                             "bt_mac, wifi_mac, usb_serial or app_device_id")
        ids.append(ident_)
    if len(ids) < 2:
        raise ValueError("an assertion needs at least two identifiers")
    return ids


class ScanCancelled(Exception):
    """Raised by :func:`scan` when its ``cancel`` event was set."""


def default_workers() -> int:
    return max(1, min(8, os.cpu_count() or 2))


class _Capture(logging.Handler):
    """Collect warnings from parsing libraries so they reach the report, not the console.

    Plugins run on worker threads; each record is kept with the thread that logged
    it, so it is reported against the plugin running on that thread.
    """

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self._lock = threading.Lock()
        self._by_thread: dict[int, list[str]] = defaultdict(list)

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            self._by_thread[record.thread or 0].append(f"{record.name}: {record.getMessage()}")

    def take(self, thread: int | None = None) -> list[str]:
        with self._lock:
            return list(dict.fromkeys(self._by_thread.pop(thread or threading.get_ident(), [])))


def _sort_key(obs: Observation):
    ts = obs.timestamp
    return (ts is None, ts.timestamp() if ts else 0.0, obs.artifact, obs.provenance.locator)


def scan(input_path: Path | str, plugin_ids: list[str] | None = None,
         workdir: Path | None = None, case: dict[str, str] | None = None,
         progress: Callable[[str], None] | None = None,
         export_dir: Path | None = None, assume_same: list[str] | None = None,
         workers: int | None = None, on_event: Callable[[dict], None] | None = None,
         cancel: threading.Event | None = None) -> ScanResult:
    """Scan ``input_path``; parsers run on ``workers`` threads (default: one per CPU, at most 8).

    ``progress`` receives one-line messages, ``on_event`` structured events
    (``discovered``, ``task_done``, ``correlating``); both are called on the
    calling thread. Setting ``cancel`` stops the scan once the running parsers
    finish and raises :class:`ScanCancelled`.
    """
    say = progress or (lambda _msg: None)
    emit = on_event or (lambda _event: None)
    capture = _Capture()
    lib_logger = logging.getLogger("regipy")
    lib_logger.setLevel(logging.WARNING)
    lib_logger.propagate = False
    lib_logger.addHandler(capture)
    try:
        return _scan(input_path, plugin_ids, workdir, case, say, emit, capture, export_dir,
                     assume_same or [], workers or default_workers(), cancel or threading.Event())
    finally:
        lib_logger.removeHandler(capture)


def _run_plugin(cls: type[Plugin], system: SystemRoot, workdir, export_dir, capture: _Capture,
                cancel: threading.Event) -> Context | None:
    if cancel.is_set():
        return None
    plugin: Plugin = cls()
    ctx = Context(plugin, system, workdir, export_dir)
    try:
        plugin.run(ctx)
    except Exception as exc:  # noqa: BLE001 - one plugin must not abort the scan
        ctx.warn(f"plugin crashed: {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}")
        ctx.coverage.append(CoverageEntry(plugin.id, system.label, "", ERROR,
                                          note=f"plugin crashed: {exc}"))
    for msg in capture.take():
        ctx.warn(f"library warning: {msg}")
    return ctx


def _scan(input_path, plugin_ids, workdir, case, say, emit, capture, export_dir, assume_same,
          workers, cancel) -> ScanResult:
    asserted = [parse_assertion(a) for a in assume_same]  # validate before any work
    started = datetime.now(timezone.utc)
    registry = load_plugins()
    if plugin_ids:
        unknown = [p for p in plugin_ids if p not in registry]
        if unknown:
            raise ValueError(f"unknown plugin(s): {', '.join(unknown)}")
        registry = {k: v for k, v in registry.items() if k in plugin_ids}

    systems = discover_systems(input_path)
    say(f"found {len(systems)} system(s) under {input_path}")
    for system in systems:
        say(f"[{system.label}] {system.os}, users: {', '.join(u.name for u in system.users) or '-'}")
    tasks = [(system, cls) for system in systems for cls in registry.values() if cls.os == system.os]
    emit({"type": "discovered", "tasks": len(tasks), "workers": workers,
          "systems": [{"label": s.label, "os": s.os, "users": [u.name for u in s.users]} for s in systems]})
    observations: list[Observation] = []
    coverage: list[CoverageEntry] = []
    warnings: list[Warning_] = [Warning_("discovery", "", f"library warning: {m}")
                                for m in capture.take()]
    plugins_run: list[dict] = []

    contexts: list[Context | None] = [None] * len(tasks)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="cpf-parser") as pool:
        futures = {pool.submit(_run_plugin, cls, system, workdir, export_dir, capture, cancel): i
                   for i, (system, cls) in enumerate(tasks)}
        for done, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            ctx = contexts[i] = fut.result()
            system, cls = tasks[i]
            n_obs, n_files = (len(ctx.observations), len(ctx.coverage)) if ctx else (0, 0)
            if n_obs or n_files:
                say(f"[{system.label}]   {cls.id}: {n_obs} observation(s) from {n_files} file(s)")
            emit({"type": "task_done", "done": done, "total": len(tasks), "system": system.label,
                  "plugin": cls.id, "name": cls.name, "observations": n_obs, "files": n_files,
                  "warnings": len(ctx.warnings) if ctx else 0})
    if cancel.is_set():
        raise ScanCancelled("scan cancelled")

    # Merge in task order (systems as discovered, plugins by id), whatever order they finished in.
    for (system, cls), ctx in zip(tasks, contexts, strict=True):
        observations.extend(ctx.observations)
        coverage.extend(ctx.coverage)
        warnings.extend(ctx.warnings)
        plugins_run.append({**cls.meta(), "system": system.label,
                            "observations": len(ctx.observations),
                            "files": len(ctx.coverage)})

    for text, ids in zip(assume_same, asserted, strict=True):
        observations.append(Observation(
            artifact="examiner_assertion", event="examiner_linked",
            message="Examiner asserted these identifiers belong to one phone: " + ", ".join(map(str, ids)),
            channel=Channel.OTHER, provenance=Provenance("(examiner)", f"--assume-same {text}", "examiner"),
            device_ids=ids, data={"assertion": text, "examiner": (case or {}).get("examiner")},
        ))
    observations.sort(key=_sort_key)
    emit({"type": "correlating", "observations": len(observations)})
    graph = build_graph(observations)
    say(f"correlated {len(observations)} observation(s) into {len(graph.devices)} device(s)")
    return ScanResult(
        input_path=str(Path(input_path).resolve()), started=started,
        finished=datetime.now(timezone.utc), systems=systems, observations=observations,
        coverage=coverage, warnings=warnings, graph=graph, plugins_run=plugins_run,
        case=case or {}, assertions=[[str(i) for i in ids] for ids in asserted],
    )


def stderr_progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
