"""Writers for scan results."""

from __future__ import annotations

from pathlib import Path

from ..engine import ScanResult
from .html import write_html
from .structured import write_structured


def write_all(result: ScanResult, outdir: Path, html: bool = True) -> list[Path]:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = write_structured(result, outdir)
    if html:
        written.append(write_html(result, outdir / "report.html"))
    return written
