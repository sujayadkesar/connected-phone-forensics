"""Score a scan against a ground-truth file.

A ground-truth file records what was actually done with real devices while
the dataset was created (see docs/VALIDATION.md): which phones, their
identifiers, and the actions with their wall-clock times. ``cpf validate``
checks every expected item against a scan's output and reports which were
found, missed, or attributed to the wrong device, so a lab can show how a
parser version performs before relying on it.

Ground truth (JSON)::

    {
      "dataset": "GT-001 Windows 11 23H2 + iPhone 15 (iOS 18.1)",
      "devices": {
        "phone1": {"udid": "00008120-...", "bt_mac": "a4:c3:37:11:22:33"}
      },
      "expected": [
        {"id": "usb-first", "device": "phone1", "event": "usb_first_install",
         "time": "2026-01-10T10:00:00Z", "tolerance_seconds": 120},
        {"id": "sms", "device": "phone1", "channel": "phone_link", "contains": "CPF-GT-001"}
      ]
    }

Match fields (all optional except one of event/channel/artifact/contains):
``artifact``, ``event``, ``channel``, ``contains`` (case-insensitive, over
message and data), ``time`` + ``tolerance_seconds`` (default 300), and
``device`` (a key of ``devices``; the matching record must be attributed to
the device that carries at least one of those identifiers).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import IdKind, normalize
from .timeutil import from_iso


@dataclass
class ItemResult:
    id: str
    status: str               # "found" | "missing" | "wrong_device" | "time_off"
    detail: str = ""
    matches: list[dict] = field(default_factory=list)


def _load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _device_keys(spec: dict[str, str]) -> set[str]:
    keys = set()
    for kind, value in spec.items():
        try:
            k = IdKind(kind)
        except ValueError:
            continue
        keys.add(f"{k.value}={normalize(k, value)}")
    return keys


def validate(truth_path: Path, results_dir: Path) -> dict[str, Any]:
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    observations = _load_jsonl(Path(results_dir) / "observations.jsonl")
    graph = json.loads((Path(results_dir) / "devices.json").read_text(encoding="utf-8"))
    dev_ids = {d["id"]: {f"{i['kind']}={i['value']}" for i in d["identifiers"]} for d in graph["devices"]}
    truth_devs = {name: _device_keys(spec) for name, spec in truth.get("devices", {}).items()}

    # Which scan device corresponds to each ground-truth device?
    mapping: dict[str, list[str]] = {
        name: sorted(d for d, ids in dev_ids.items() if keys & ids)
        for name, keys in truth_devs.items()
    }

    results: list[ItemResult] = []
    for item in truth.get("expected", []):
        iid = item.get("id") or json.dumps(item, sort_keys=True)
        cands = observations
        for f in ("artifact", "event", "channel"):
            if item.get(f):
                cands = [o for o in cands if o.get(f) == item[f]]
        if item.get("contains"):
            needle = item["contains"].lower()
            cands = [o for o in cands
                     if needle in (o.get("message", "") + json.dumps(o.get("data", {}), ensure_ascii=False)).lower()]
        if not cands:
            results.append(ItemResult(iid, "missing", "no record matches the event/content criteria"))
            continue
        if item.get("time"):
            want = from_iso(item["time"])
            tol = float(item.get("tolerance_seconds", 300))
            timed = [o for o in cands if o.get("datetime")]
            close = [o for o in timed if abs((from_iso(o["datetime"]) - want).total_seconds()) <= tol]
            if not close:
                nearest = min(timed, key=lambda o: abs((from_iso(o["datetime"]) - want).total_seconds()),
                              default=None)
                delta = (f"nearest is {abs((from_iso(nearest['datetime']) - want).total_seconds()):.0f}s away"
                         if nearest else "matching records have no timestamp")
                results.append(ItemResult(iid, "time_off", delta, _brief(cands[:3])))
                continue
            cands = close
        if item.get("device"):
            expected_devs = set(mapping.get(item["device"], []))
            on_device = [o for o in cands if o.get("device") in expected_devs]
            if not on_device:
                got = sorted({o.get("device") or "(unattributed)" for o in cands})
                results.append(ItemResult(iid, "wrong_device",
                                          f"expected {item['device']} -> {sorted(expected_devs) or 'no device'}, "
                                          f"found on {got}", _brief(cands[:3])))
                continue
            cands = on_device
        results.append(ItemResult(iid, "found", f"{len(cands)} matching record(s)", _brief(cands[:3])))

    found = sum(1 for r in results if r.status == "found")
    return {
        "dataset": truth.get("dataset", ""),
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results_dir": str(results_dir),
        "device_mapping": mapping,
        "split_devices": {k: v for k, v in mapping.items() if len(v) > 1},
        "summary": {"expected": len(results), "found": found,
                    "recall": round(found / len(results), 4) if results else None,
                    "by_status": {s: sum(1 for r in results if r.status == s)
                                  for s in ("found", "missing", "time_off", "wrong_device")}},
        "items": [r.__dict__ for r in results],
    }


def _brief(obs: list[dict]) -> list[dict]:
    return [{k: o.get(k) for k in ("datetime", "event", "device", "source_path", "locator")} for o in obs]


def render_markdown(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [f"# Validation: {report['dataset']}", "",
             f"Found **{s['found']} / {s['expected']}** expected items (recall {s['recall']}).", "",
             "| Status | Count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in s["by_status"].items()]
    if report["split_devices"]:
        lines += ["", "**Split devices** (one real phone reported as several):", ""]
        lines += [f"- {k}: {', '.join(v)}" for k, v in report["split_devices"].items()]
    lines += ["", "| Item | Status | Detail |", "|---|---|---|"]
    lines += [f"| {r['id']} | {r['status']} | {r['detail']} |" for r in report["items"]]
    return "\n".join(lines) + "\n"
