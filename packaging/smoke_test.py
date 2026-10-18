"""Check the built executables actually work: python packaging/smoke_test.py dist

The command-line exe must report its version, list all parsers and build and scan the demo
case; the desktop exe must run the demo case through its own window (off screen).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    cli = next(dist.glob("cpf-*.exe"))
    gui = next(dist.glob("ConnectedPhoneForensics-*.exe"))
    failures = []
    with tempfile.TemporaryDirectory(prefix="cpf-smoke-") as tmp:
        tmp = Path(tmp)
        r = run([str(cli), "--version"])
        print(r.stdout.strip())
        if r.returncode or not r.stdout.startswith("cpf "):
            failures.append(f"cli --version: {r.returncode} {r.stderr}")
        r = run([str(cli), "plugins", "--json"])
        n = len(json.loads(r.stdout)) if r.returncode == 0 else 0
        print(f"{n} parsers")
        if n < 20:
            failures.append(f"cli plugins: only {n} parsers ({r.stderr[-500:]})")
        r = run([str(cli), "demo", str(tmp / "cli-demo")])
        report = tmp / "cli-demo" / "report" / "report.html"
        if r.returncode or not report.is_file():
            failures.append(f"cli demo: {r.returncode} {r.stderr[-800:]}")
        else:
            manifest = json.loads((tmp / "cli-demo" / "report" / "manifest.json").read_text(encoding="utf-8"))
            print(f"cli demo: {manifest['counts']}")
        r = run([str(gui), "--smoke-test", str(tmp / "gui")])
        result = tmp / "gui" / "smoke-test.txt"
        text = result.read_text(encoding="utf-8").strip() if result.is_file() else "(no result file)"
        print(f"gui: exit {r.returncode}: {text}")
        if r.returncode or not text.startswith("ok"):
            failures.append(f"gui smoke test: {r.returncode} {text}")
    if failures:
        print("\nFAILED:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    print("\nall smoke tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
