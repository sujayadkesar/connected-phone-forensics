"""Machine-readable outputs.

* ``timeline.jsonl`` / ``timeline.csv`` — timestamped observations in a shape
  Timesketch imports directly (``message``, ``datetime``, ``timestamp_desc``).
* ``observations.jsonl`` — every observation, timed or not.
* ``devices.json`` — the device graph.
* ``coverage.csv`` — every evidence file a plugin touched and what happened.
* ``manifest.json`` — run metadata and SHA-256 of every evidence file read.

JSON outputs carry values exactly as parsed. CSV files are a convenience for
spreadsheets: they start with a UTF-8 byte-order mark (so Excel reads them as
UTF-8) and cells that a spreadsheet would execute as a formula (starting with
``=``, ``@``, or ``+``/``-`` followed by something other than a number) are
prefixed with an apostrophe, because evidence strings are attacker-controlled.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from ..engine import ScanResult
from ..evidence import display_path
from ..model import Observation
from ..timeutil import iso

_NUMBERISH = re.compile(r"[+-][\d\s().-]*")


def csv_safe(value: Any) -> Any:
    if not isinstance(value, str) or not value:
        return value
    if value[0] in "=@\t\r" or (value[0] in "+-" and not _NUMBERISH.fullmatch(value)):
        return "'" + value
    return value


def _jsonable(v: Any) -> Any:
    if isinstance(v, bytes):
        return v.hex()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, (set, tuple)):
        return list(v)
    return str(v)


def observation_record(obs: Observation, device_id: str | None, device_label: str | None) -> dict:
    return {
        "message": obs.message,
        "datetime": iso(obs.timestamp),
        "timestamp": int(obs.timestamp.timestamp() * 1_000_000) if obs.timestamp else None,
        "timestamp_desc": obs.timestamp_desc or "Not a time",
        "time_source": obs.time_source,
        "data_type": f"cpf:{obs.artifact}:{obs.event}",
        "artifact": obs.artifact,
        "event": obs.event,
        "channel": obs.channel.value,
        "device": device_id,
        "device_label": device_label,
        "device_ids": [i.key() for i in obs.device_ids],
        "phone_os": obs.phone_os,
        "system": obs.system,
        "user": obs.user,
        "source_path": obs.provenance.source_path,
        "locator": obs.provenance.locator,
        "sha256": obs.provenance.sha256,
        "data": obs.data,
    }


def iter_records(result: ScanResult):
    labels = {d.id: d.label for d in result.graph.devices}
    for idx, obs in enumerate(result.observations):
        dev = result.graph.assignment.get(idx)
        yield obs, observation_record(obs, dev, labels.get(dev) if dev else None)


_CSV_COLS = ["datetime", "timestamp_desc", "time_source", "device", "device_label", "channel", "artifact",
             "event", "message", "user", "system", "source_path", "locator", "sha256",
             "device_ids", "data"]


def write_structured(result: ScanResult, outdir: Path) -> list[Path]:
    written: list[Path] = []
    p_tl, p_tlcsv, p_obs = outdir / "timeline.jsonl", outdir / "timeline.csv", outdir / "observations.jsonl"
    with open(p_tl, "w", encoding="utf-8") as tl, open(p_obs, "w", encoding="utf-8") as allf, \
            open(p_tlcsv, "w", encoding="utf-8-sig", newline="") as tlcsv:
        w = csv.DictWriter(tlcsv, fieldnames=_CSV_COLS, extrasaction="ignore")
        w.writeheader()
        for obs, rec in iter_records(result):
            line = json.dumps(rec, default=_jsonable, ensure_ascii=False)
            allf.write(line + "\n")
            if obs.timestamp is not None:
                tl.write(line + "\n")
                row = {k: csv_safe(v) for k, v in rec.items()}
                row["device_ids"] = csv_safe(";".join(rec["device_ids"]))
                row["data"] = csv_safe(json.dumps(rec["data"], default=_jsonable, ensure_ascii=False))
                w.writerow(row)
    written += [p_tl, p_tlcsv, p_obs]

    p_dev = outdir / "devices.json"
    p_dev.write_text(json.dumps(result.graph.to_dict(), indent=2, default=_jsonable,
                                ensure_ascii=False), encoding="utf-8")
    written.append(p_dev)

    p_cov = outdir / "coverage.csv"
    with open(p_cov, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["plugin", "system", "source_path", "status", "records", "note"])
        for c in result.coverage:
            w.writerow([csv_safe(x) for x in (c.plugin, c.system, c.source_path, c.status, c.records, c.note)])
    written.append(p_cov)

    p_man = outdir / "manifest.json"
    manifest = {
        "tool": "connected-phone-forensics",
        "tool_version": result.tool_version,
        "python": sys.version.split()[0],
        "command": sys.argv,
        "case": result.case,
        "input_path": result.input_path,
        "started": iso(result.started),
        "finished": iso(result.finished),
        "systems": [
            {"label": s.label, "os": s.os, "root": display_path(s.fs.root), "details": s.details,
             "users": [u.name for u in s.users]} for s in result.systems
        ],
        "plugins": result.plugins_run,
        "examiner_assertions": result.assertions,
        "counts": {
            "observations": len(result.observations),
            "timed_observations": sum(1 for o in result.observations if o.timestamp),
            "devices": len(result.graph.devices),
            "files_touched": len(result.coverage),
        },
        "evidence_files_sha256": {
            s.label: s.fs.hashed_files() for s in result.systems
        },
        "warnings": [
            {"plugin": w.plugin, "system": w.system, "source_path": w.source_path,
             "message": w.message} for w in result.warnings
        ],
    }
    p_man.write_text(json.dumps(manifest, indent=2, default=_jsonable, ensure_ascii=False),
                     encoding="utf-8")
    written.append(p_man)
    return written
