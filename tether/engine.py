"""Scan orchestration: find systems, run plugins, correlate devices."""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .correlate import DeviceGraph, build_graph
from .evidence import SystemRoot, discover_systems
from .model import Observation
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


def _sort_key(obs: Observation):
    ts = obs.timestamp
    return (ts is None, ts.timestamp() if ts else 0.0, obs.artifact, obs.provenance.locator)


def scan(input_path: Path | str, plugin_ids: list[str] | None = None,
         workdir: Path | None = None, case: dict[str, str] | None = None,
         progress: Callable[[str], None] | None = None) -> ScanResult:
    say = progress or (lambda _msg: None)
    logging.getLogger("regipy").setLevel(logging.ERROR)
    started = datetime.now(timezone.utc)
    registry = load_plugins()
    if plugin_ids:
        unknown = [p for p in plugin_ids if p not in registry]
        if unknown:
            raise ValueError(f"unknown plugin(s): {', '.join(unknown)}")
        registry = {k: v for k, v in registry.items() if k in plugin_ids}

    systems = discover_systems(input_path)
    say(f"found {len(systems)} system(s) under {input_path}")
    observations: list[Observation] = []
    coverage: list[CoverageEntry] = []
    warnings: list[Warning_] = []
    plugins_run: list[dict] = []

    for system in systems:
        say(f"[{system.label}] {system.os}, users: {', '.join(u.name for u in system.users) or '-'}")
        for cls in registry.values():
            if cls.os != system.os:
                continue
            plugin: Plugin = cls()
            ctx = Context(plugin, system, workdir)
            try:
                plugin.run(ctx)
            except Exception as exc:  # noqa: BLE001 - one plugin must not abort the scan
                ctx.warn(f"plugin crashed: {type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}")
                ctx.coverage.append(CoverageEntry(plugin.id, system.label, "", ERROR,
                                                  note=f"plugin crashed: {exc}"))
            observations.extend(ctx.observations)
            coverage.extend(ctx.coverage)
            warnings.extend(ctx.warnings)
            if ctx.coverage or ctx.observations:
                say(f"[{system.label}]   {plugin.id}: {len(ctx.observations)} observation(s) "
                    f"from {len(ctx.coverage)} file(s)")
            plugins_run.append({**cls.meta(), "system": system.label,
                                "observations": len(ctx.observations),
                                "files": len(ctx.coverage)})

    observations.sort(key=_sort_key)
    graph = build_graph(observations)
    say(f"correlated {len(observations)} observation(s) into {len(graph.devices)} device(s)")
    return ScanResult(
        input_path=str(Path(input_path).resolve()), started=started,
        finished=datetime.now(timezone.utc), systems=systems, observations=observations,
        coverage=coverage, warnings=warnings, graph=graph, plugins_run=plugins_run,
        case=case or {},
    )


def stderr_progress(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)
