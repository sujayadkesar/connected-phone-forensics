"""Render verified research JSON (one file per cluster) into docs/research/*.md.

Usage: python scripts/research_to_md.py <dir with research_*.json> docs/research
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TITLES = {
    "phonelink": "Microsoft Phone Link and the Windows cross-device stack",
    "mac_continuity": "macOS Continuity and iPhone Mirroring",
    "apple_sync_dbs": "Apple account-sync databases on macOS (Biome, knowledgeC, Screen Time, Safari, Messages)",
    "apple_pairing_backups": "iPhone pairing records and local backups",
    "windows_usb_mtp": "Phones over USB on Windows (and macOS/Linux)",
    "radio_network": "Bluetooth, Wi-Fi hotspots and USB tethering",
    "messaging_companions": "Desktop messaging clients linked to a phone",
    "android_companions": "Android companion software (KDE Connect, Samsung, Quick Share, ...)",
    "landscape": "Existing tools, conventions and public test data",
}
NOVELTY = {"well_documented": "well documented", "partially_documented": "partially documented",
           "undocumented": "**undocumented** (open research)"}


def _list(items) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none recorded)"


def render(cluster: dict) -> str:
    key = cluster["cluster"]
    lines = [f"# {TITLES.get(key, key)}", "",
             "> Research notes compiled for Connected Phone Forensics from public sources and open-source parser code, then "
             "adversarially fact-checked. Claims are marked by confidence; anything marked as inference or "
             "undocumented needs validation on real devices (see docs/VALIDATION.md).", "",
             "## Summary", "", cluster.get("cluster_summary", ""), "",
             "## Recommended priority", "", cluster.get("recommended_priority", ""), "",
             "## Correlation keys", "", _list(cluster.get("cross_source_correlation_keys")), "",
             "## Artifacts", "", "| Artifact | Platform | Value | Documentation | Confidence |", "|---|---|---|---|---|"]
    arts = sorted(cluster.get("artifacts", []),
                  key=lambda a: (["critical", "high", "medium", "low"].index(a.get("forensic_value", "low")), a["id"]))
    for a in arts:
        lines.append(f"| [{a['name']}](#{a['id'].replace('_', '-')}) | {a['platform']} | {a['forensic_value']} "
                     f"| {NOVELTY.get(a['novelty'], a['novelty'])} | {a['confidence']} |")
    for a in arts:
        lines += ["", f"### {a['id']}", "", f"**{a['name']}**", "",
                  f"*Platform:* {a['platform']} · *Phones:* {', '.join(a.get('phone_platforms') or ['any'])} · "
                  f"*Value:* {a['forensic_value']} · *Documentation:* {NOVELTY.get(a['novelty'], a['novelty'])} · "
                  f"*Confidence:* {a['confidence']}", ""]
        if a.get("versions"):
            lines += [f"*Versions:* {a['versions']}", ""]
        lines += ["**Paths**", "", _list(f"`{p}`" for p in a.get("paths", [])), "",
                  f"**Format:** {a.get('format', '')}", "", "**Structure**", "", a.get("structure", ""), "",
                  "**Timestamps**", "", a.get("timestamps", ""), "",
                  "**Phone identity**", "", _list(a.get("phone_identity_fields")), "",
                  "**Phone-originated content**", "", _list(a.get("phone_content")), ""]
        if a.get("retention"):
            lines += [f"**Retention:** {a['retention']}", ""]
        if a.get("encryption_or_access_barriers"):
            lines += [f"**Access barriers:** {a['encryption_or_access_barriers']}", ""]
        parsers = a.get("existing_parsers") or []
        if parsers:
            lines += ["**Existing parsers**", "", _list(
                f"[{p['name']}]({p['url']}) ({p.get('license') or 'licence unknown'}){' — ' + p['status'] if p.get('status') else ''}"
                for p in parsers), ""]
        samples = a.get("sample_data") or []
        if samples:
            lines += ["**Sample data**", "", _list(f"[{s['description']}]({s['url']})" for s in samples), ""]
        lines += ["**Open questions**", "", _list(a.get("open_questions")), "", "**Sources**", "",
                  _list(a.get("sources")), ""]
    return "\n".join(lines) + "\n"


def main(src: str, dst: str) -> None:
    out = Path(dst)
    out.mkdir(parents=True, exist_ok=True)
    clusters = [json.loads(p.read_text()) for p in sorted(Path(src).glob("research_*.json"))]
    index = ["# Research notes", "",
             "Everything Connected Phone Forensics knows about where phones leave traces on computers, by channel. These notes are "
             "the basis of the parsers and are published so others can check, reuse and extend them.", "",
             "| Channel | Artifacts | Undocumented |", "|---|---|---|"]
    gaps = []
    for c in clusters:
        (out / f"{c['cluster']}.md").write_text(render(c), encoding="utf-8")
        und = [a for a in c.get("artifacts", []) if a.get("novelty") == "undocumented"]
        index.append(f"| [{TITLES.get(c['cluster'], c['cluster'])}]({c['cluster']}.md) | {len(c.get('artifacts', []))} "
                     f"| {len(und)} |")
        gaps += [(c["cluster"], a) for a in und]
    index += ["", "## Open research: undocumented artifacts", "",
              "Each of these is a chance to publish first. A ground-truth dataset (docs/VALIDATION.md) that "
              "documents one of them is the most valuable contribution to the project.", ""]
    for cluster, a in sorted(gaps, key=lambda g: (["critical", "high", "medium", "low"].index(g[1].get("forensic_value", "low")), g[1]["id"])):
        q = (a.get("open_questions") or [""])[0]
        index.append(f"- **{a['name']}** ({a['forensic_value']}; [{cluster}]({cluster}.md#{a['id'].replace('_', '-')})): {q}")
    (out / "README.md").write_text("\n".join(index) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
