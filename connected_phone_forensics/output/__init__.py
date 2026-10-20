"""Writers for scan results."""

from __future__ import annotations

import sys
from pathlib import Path

from ..engine import ScanResult
from .html import write_html
from .structured import write_structured


def write_all(result: ScanResult, outdir: Path, html: bool = True, xlsx: bool = True, pdf: bool = True,
              case=None) -> list[Path]:
    """Write every output of a scan into ``outdir``.

    The structured files (timeline, devices, coverage, manifest) and the HTML report come first;
    the Excel workbook and the PDF summary are made from the same records. If one of those two
    cannot be written (a missing library, a system without the fonts Qt needs), the scan's other
    outputs stand and the reason is printed.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    written = write_structured(result, outdir)
    if html:
        companions = tuple(n for n, on in (("summary.pdf", pdf), ("workbook.xlsx", xlsx)) if on)
        written.append(write_html(result, outdir / "report.html", companions))
    if xlsx or pdf:
        from .. import content
        from ..casedata import CaseData

        case = case if case is not None else CaseData.from_result(result, outdir)
        items = content.extract(case.records, case.devices)
        if xlsx:
            try:
                from .excel import write_workbook

                written.append(write_workbook(case, outdir / "workbook.xlsx", items))
            except Exception as exc:  # noqa: BLE001 - the other outputs must not be lost
                print(f"warning: workbook.xlsx not written: {type(exc).__name__}: {exc}", file=sys.stderr)
        if pdf:
            try:
                from .pdf import write_pdf

                written.append(write_pdf(case, outdir / "summary.pdf", items))
            except Exception as exc:  # noqa: BLE001
                print(f"warning: summary.pdf not written: {type(exc).__name__}: {exc}", file=sys.stderr)
    return written
