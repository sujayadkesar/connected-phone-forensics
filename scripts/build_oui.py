"""Build the MAC address vendor table from the IEEE registries.

    python scripts/build_oui.py oui.csv mam.csv oui36.csv

Download the three CSVs from https://standards-oui.ieee.org/ (oui/oui.csv, oui28/mam.csv,
oui36/oui36.csv; the server rejects requests without a browser User-Agent). Writes
connected_phone_forensics/data/oui.tsv.gz: one line per assignment, "<hex prefix>\\t<organization>",
with 6, 7 or 9 hex digits for MA-L, MA-M and MA-S blocks.
"""

from __future__ import annotations

import csv
import gzip
import sys
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "connected_phone_forensics" / "data" / "oui.tsv.gz"


def main(paths: list[str]) -> int:
    rows: dict[str, str] = {}
    for path in paths:
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                prefix = r["Assignment"].strip().upper()
                name = " ".join(r["Organization Name"].split())
                if prefix and name:
                    rows[prefix] = name
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# IEEE MA-L, MA-M and MA-S registries, standards-oui.ieee.org, retrieved {date.today()}"]
    lines += [f"{k}\t{v}" for k, v in sorted(rows.items())]
    with gzip.open(OUT, "wt", encoding="utf-8", newline="\n", compresslevel=9) as f:
        f.write("\n".join(lines) + "\n")
    print(f"{len(rows)} assignments -> {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
