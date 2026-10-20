"""A scan's results as plain records, from a finished scan or from a saved output folder.

Both sources are reduced to the same records (the JSON written to the output
folder), so the app's screens, the Excel workbook and the PDF summary work the
same way whether the case was just scanned or reopened later.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

CHANNEL_LABEL = {
    "usb": "USB", "bluetooth": "Bluetooth", "wifi_hotspot": "Wi-Fi hotspot", "usb_tethering": "USB tethering",
    "pairing": "Pairing", "backup": "Backup", "phone_link": "Phone Link", "continuity": "Continuity",
    "account_sync": "Account sync", "messaging_client": "Messaging", "companion_app": "Companion app",
    "developer": "Developer tools", "other": "Other",
}
OS_LABEL = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "ios": "iOS", "android": "Android"}
CLASS_LABEL = {"phone": "Phone", "tablet": "Tablet", "computer": "Computer", "audio": "Audio",
               "wearable": "Wearable", "peripheral": "Peripheral", "other": "Other"}
ID_LABEL = {
    "udid": "UDID", "serial": "Serial number", "imei": "IMEI", "meid": "MEID", "iccid": "ICCID (SIM)",
    "phone_number": "Phone number", "bt_mac": "Bluetooth address", "wifi_mac": "Wi-Fi address",
    "usb_serial": "USB serial", "app_device_id": "App device ID", "device_name": "Device name",
    "model": "Model", "os_version": "OS version", "account": "Account",
}
STRENGTH_LABEL = {"strong": "Strong", "link": "Link", "weak": "Weak"}
STATUS_LABEL = {"parsed": "Parsed", "empty": "Empty", "unrecognised": "Unrecognised", "error": "Error"}
VALIDATION_LABEL = {"synthetic": "Synthetic", "public_sample": "Public sample", "ground_truth": "Ground truth"}

REQUIRED_FILES = ("manifest.json", "devices.json", "observations.jsonl")


def channel_label(c: str | None) -> str:
    return CHANNEL_LABEL.get(c or "", (c or "—").replace("_", " ").capitalize())


def os_label(o: str | None) -> str:
    return OS_LABEL.get(o or "", o or "—")


def fmt_time(value: str | None, with_seconds: bool = True) -> str:
    """'2025-10-09T16:12:00+00:00' -> '2025-10-09 16:12:00' (UTC); '—' when missing."""
    if not value:
        return "—"
    text = value.replace("T", " ")
    text = text.split("+")[0].split(".")[0].rstrip("Z")
    return text if with_seconds else text[:16]


def fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    m, s = divmod(int(seconds), 60)
    return f"{m} min {s:02d} s" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def _parse_iso(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _search_blob(rec: dict) -> str:
    parts = [rec.get("message"), rec.get("device_label"), rec.get("device"), rec.get("event"),
             rec.get("artifact"), rec.get("channel"), rec.get("system"), rec.get("user"),
             rec.get("source_path"), rec.get("locator"), rec.get("timestamp_desc"), rec.get("datetime"),
             " ".join(rec.get("device_ids") or [])]
    data = rec.get("data")
    if data:
        parts.append(json.dumps(data, ensure_ascii=False, default=str))
    return " ".join(str(p) for p in parts if p).lower()


@dataclass
class CaseData:
    outdir: Path | None
    case: dict[str, str]
    input_path: str
    started: str | None
    finished: str | None
    tool_version: str
    systems: list[dict]
    devices: list[dict]
    possible_matches: list[dict]
    candidate_groups: list[dict]
    records: list[dict]          # one per observation, in timeline order
    coverage: list[dict]
    warnings: list[dict]
    plugins: list[dict]
    assertions: list[list[str]] = field(default_factory=list)
    acquisition: dict | None = None  # live acquisition summary (see live.acquire.Acquisition.summary)

    def __post_init__(self) -> None:
        self._by_id = {d["id"]: d for d in self.devices}
        self._hotspots: list[dict] | None = None
        for rec in self.records:
            rec["_search"] = _search_blob(rec)

    # -- building ----------------------------------------------------------------

    @classmethod
    def from_result(cls, result: Any, outdir: Path | None = None) -> CaseData:
        from .evidence import display_path
        from .output.structured import _jsonable, iter_records
        from .timeutil import iso

        graph = result.graph.to_dict()
        # Round-trip through JSON so live results look exactly like reloaded ones.
        records = [json.loads(json.dumps(rec, default=_jsonable, ensure_ascii=False))
                   for _obs, rec in iter_records(result)]
        return cls(
            outdir=Path(outdir) if outdir else None, case=dict(result.case), input_path=result.input_path,
            started=iso(result.started), finished=iso(result.finished), tool_version=result.tool_version,
            systems=[{"label": s.label, "os": s.os, "root": display_path(s.fs.root), "details": s.details,
                      "users": [u.name for u in s.users]} for s in result.systems],
            devices=json.loads(json.dumps(graph["devices"], default=_jsonable)),
            possible_matches=json.loads(json.dumps(graph["possible_matches"], default=_jsonable)),
            candidate_groups=graph["candidate_groups"], records=records,
            coverage=[{"plugin": c.plugin, "system": c.system, "source_path": c.source_path, "status": c.status,
                       "records": c.records, "note": c.note} for c in result.coverage],
            warnings=[{"plugin": w.plugin, "system": w.system, "source_path": w.source_path, "message": w.message}
                      for w in result.warnings],
            plugins=result.plugins_run, assertions=result.assertions, acquisition=result.acquisition,
        )

    @classmethod
    def load(cls, outdir: Path | str) -> CaseData:
        """Read a folder written by a scan (manifest.json, devices.json, observations.jsonl, coverage.csv)."""
        outdir = Path(outdir)
        missing = [f for f in REQUIRED_FILES if not (outdir / f).is_file()]
        if missing:
            raise FileNotFoundError(f"{outdir} is not a scan output folder (missing {', '.join(missing)})")
        manifest = json.loads((outdir / "manifest.json").read_text(encoding="utf-8"))
        graph = json.loads((outdir / "devices.json").read_text(encoding="utf-8"))
        with open(outdir / "observations.jsonl", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        coverage: list[dict] = []
        if (outdir / "coverage.csv").is_file():
            with open(outdir / "coverage.csv", encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    row = {k: _unsafe(v) for k, v in row.items()}
                    row["records"] = int(row.get("records") or 0)
                    coverage.append(row)
        return cls(
            outdir=outdir, case=manifest.get("case") or {}, input_path=manifest.get("input_path", ""),
            started=manifest.get("started"), finished=manifest.get("finished"),
            tool_version=manifest.get("tool_version", ""), systems=manifest.get("systems", []),
            devices=graph.get("devices", []), possible_matches=graph.get("possible_matches", []),
            candidate_groups=graph.get("candidate_groups", []), records=records, coverage=coverage,
            warnings=manifest.get("warnings", []), plugins=manifest.get("plugins", []),
            assertions=manifest.get("examiner_assertions", []), acquisition=manifest.get("acquisition"),
        )

    # -- queries -------------------------------------------------------------------

    @property
    def title(self) -> str:
        ref = (self.case.get("case") or "").strip()
        return ref or Path(self.input_path).name or "Untitled case"

    @property
    def report(self) -> Path | None:
        p = self.outdir / "report.html" if self.outdir else None
        return p if p and p.is_file() else None

    @property
    def duration(self) -> float | None:
        a, b = _parse_iso(self.started), _parse_iso(self.finished)
        return (b - a).total_seconds() if a and b else None

    def device(self, device_id: str | None) -> dict | None:
        return self._by_id.get(device_id or "")

    @property
    def phones(self) -> list[dict]:
        """Phones and tablets, except those known only as hotspots (see :attr:`hotspots`)."""
        from .content import is_hotspot_only

        return [d for d in self.devices if d.get("device_class") in (None, "phone", "tablet")
                and not is_hotspot_only(d)]

    @property
    def hotspot_devices(self) -> list[dict]:
        from .content import is_hotspot_only

        return [d for d in self.devices if d.get("device_class") in (None, "phone", "tablet") and is_hotspot_only(d)]

    @property
    def hotspots(self) -> list[dict]:
        """Phones seen only as Wi-Fi hotspots, grouped by network name (content.hotspot_networks)."""
        if self._hotspots is None:
            from .content import hotspot_networks

            self._hotspots = hotspot_networks(self.records, self.devices)
        return self._hotspots

    @property
    def others(self) -> list[dict]:
        return [d for d in self.devices if d.get("device_class") not in (None, "phone", "tablet")]

    @property
    def errors(self) -> list[dict]:
        return [c for c in self.coverage if c.get("status") == "error"]

    def matches_for(self, device_id: str) -> list[dict]:
        return [m for m in self.possible_matches if device_id in m.get("devices", [])]

    def channel_counts(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for r in self.records:
            counts[r.get("channel") or "other"] = counts.get(r.get("channel") or "other", 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def _unsafe(value: str | None) -> str:
    """Undo the CSV formula guard (a leading apostrophe) applied when the file was written."""
    if value and len(value) > 1 and value[0] == "'" and value[1] in "=@+-\t\r":
        return value[1:]
    return value or ""
