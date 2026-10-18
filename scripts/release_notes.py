"""Release notes for a tag: its CHANGELOG section, the downloads and their checksums.

    python scripts/release_notes.py v0.1.0 dist/SHA256SUMS.txt > notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def changelog_section(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    # The section ends at the next version heading or at the link definitions at the bottom.
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    return m.group(1).strip()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tag = sys.argv[1]
    version = tag.lstrip("v")
    sums = Path(sys.argv[2]).read_text(encoding="utf-8").strip() if len(sys.argv) > 2 else ""
    print(changelog_section(version))
    print(f"""
## Downloads

| File | What it is |
|---|---|
| `ConnectedPhoneForensics-{version}-windows-x64.exe` | **Desktop app** for Windows 10/11. Download and run; nothing to install. |
| `cpf-{version}-windows-x64.exe` | Command-line tool for Windows (`cpf scan`, `cpf demo`, ...). |
| `connected_phone_forensics-{version}-py3-none-any.whl` | Python package (the same as `pip install connected-phone-forensics`) for Windows, macOS and Linux. |
| `connected_phone_forensics-{version}.tar.gz` | Source archive. |

The Windows executables are not code-signed yet, so SmartScreen may show *Windows protected your PC*:
choose **More info → Run anyway**. Verify the download first against the checksums below
(`certutil -hashfile <file> SHA256` on Windows, `sha256sum <file>` elsewhere).

```
{sums}
```
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
