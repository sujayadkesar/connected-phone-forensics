"""Check the built executables actually work: python packaging/smoke_test.py dist

The command-line exe must report its version, list all parsers, and build and scan the demo
case into a report, a PDF summary and a workbook; the desktop exe must run the demo case
through its own window (off screen). Both programs in the portable ZIP must do the same from
the extracted folder.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def run(cmd: list[str], timeout: int = 400) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def check_cli(cli: Path, work: Path, label: str, failures: list[str]) -> None:
    r = run([str(cli), "--version"])
    print(r.stdout.strip())
    if r.returncode or not r.stdout.startswith("cpf "):
        failures.append(f"{label} --version: {r.returncode} {r.stderr}")
    r = run([str(cli), "plugins", "--json"])
    n = len(json.loads(r.stdout)) if r.returncode == 0 else 0
    print(f"{n} parsers")
    if n < 20:
        failures.append(f"{label} plugins: only {n} parsers ({r.stderr[-500:]})")
    r = run([str(cli), "demo", str(work)])
    out = work / "report"
    missing = [f for f in ("report.html", "summary.pdf", "workbook.xlsx", "manifest.json") if not (out / f).is_file()]
    if r.returncode or missing:
        failures.append(f"{label} demo: exit {r.returncode}, missing {missing}: {r.stderr[-800:]}")
    else:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        pdf = (out / "summary.pdf").read_bytes()
        print(f"{label} demo: {manifest['counts']}; summary.pdf {len(pdf):,} bytes")
        if not pdf.startswith(b"%PDF") or len(pdf) < 20_000:
            failures.append(f"{label} demo: summary.pdf looks wrong ({len(pdf)} bytes)")


def check_gui(gui: Path, work: Path, label: str, failures: list[str]) -> None:
    r = run([str(gui), "--smoke-test", str(work)])
    result = work / "smoke-test.txt"
    text = result.read_text(encoding="utf-8").strip() if result.is_file() else "(no result file)"
    print(f"{label}: exit {r.returncode}: {text}")
    if r.returncode or not text.startswith("ok"):
        failures.append(f"{label} smoke test: {r.returncode} {text}")


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    cli = next(dist.glob("cpf-*.exe"))
    gui = next(dist.glob("ConnectedPhoneForensics-*.exe"))
    portable = next(dist.glob("*-portable.zip"))
    failures: list[str] = []
    # The demo case is deeper than 260 characters: clean up through extended-length paths.
    with tempfile.TemporaryDirectory(prefix="cpf-smoke-", ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        check_cli(cli, tmp / "cli-demo", "cli", failures)
        check_gui(gui, tmp / "gui", "gui", failures)
        with zipfile.ZipFile(portable) as z:
            z.extractall(tmp / "portable")
        folder = tmp / "portable" / "ConnectedPhoneForensics"
        if not (folder / "portable.txt").is_file():
            failures.append("portable: portable.txt is missing")
        check_cli(folder / "cpf.exe", tmp / "portable-cli-demo", "portable cli", failures)
        check_gui(folder / "ConnectedPhoneForensics.exe", tmp / "portable-gui", "portable gui", failures)
        if sys.platform == "win32":
            shutil.rmtree("\\\\?\\" + str(tmp.resolve()), ignore_errors=True)
    if failures:
        print("\nFAILED:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print("\nall smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
