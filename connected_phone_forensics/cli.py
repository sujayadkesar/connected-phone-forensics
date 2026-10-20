"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__


def _cmd_scan(args: argparse.Namespace) -> int:
    from .engine import scan, stderr_progress
    from .output import write_all

    case = {"case": args.case or "", "examiner": args.examiner or ""}
    plugin_ids = [p.strip() for p in args.plugins.split(",")] if args.plugins else None
    from .live import is_system_drive_root

    if is_system_drive_root(args.input):
        print(f"warning: {args.input} is this computer's running system: Windows keeps its registry hives "
              "locked and protects some folders, so they cannot be read as a plain folder. Run "
              "'cpf live -o <folder on a USB drive>' as administrator instead.", file=sys.stderr)
    try:
        result = scan(args.input, plugin_ids=plugin_ids, workdir=args.workdir, case=case,
                      progress=None if args.quiet else stderr_progress,
                      export_dir=None if args.no_export else Path(args.output) / "exports",
                      assume_same=args.assume_same, workers=args.workers)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not result.systems:
        print(f"error: no Windows, macOS or Linux filesystem found under {args.input}", file=sys.stderr)
        return 2
    written = write_all(result, args.output, html=not args.no_html, xlsx=not args.no_xlsx, pdf=not args.no_pdf)
    if not args.quiet:
        for p in written:
            print(f"wrote {p}", file=sys.stderr)
    return 0


def _cmd_live(args: argparse.Namespace) -> int:
    from datetime import datetime

    from . import live
    from .engine import stderr_progress

    def finish(code: int) -> int:
        if args.pause_at_end:  # an elevated console opened by --elevate closes when the command ends
            print(file=sys.stderr)
            input("Press Enter to close this window.")
        return code

    if not live.is_supported():
        print("error: live acquisition is available on Windows only", file=sys.stderr)
        return 2
    if not live.is_admin():
        if args.elevate:
            from .live import winapi

            again = [a for a in sys.argv[1:] if a != "--elevate"] + ["--pause-at-end"]
            if winapi.relaunch_elevated(again, module="connected_phone_forensics.cli"):
                print("started again as administrator in a new window", file=sys.stderr)
                return 0
            print("error: the administrator prompt was declined", file=sys.stderr)
            return 2
        print("error: live acquisition needs administrator rights (the registry hives and protected folders "
              "cannot be read otherwise). Open a terminal as administrator and run the command again, or "
              "add --elevate.", file=sys.stderr)
        return finish(2)
    output = args.output
    if output is None:
        from .live import winapi

        output = live.suggest_output(winapi.computer_name(), datetime.now().strftime("%Y-%m-%d %H%M"))
        if output is None:
            print("error: choose the output folder with -o, on a USB drive or external disk (no removable "
                  "drive was found)", file=sys.stderr)
            return finish(2)
    quiet = args.quiet
    count = {"n": 0}

    def on_event(ev: dict) -> None:
        if quiet:
            return
        if ev.get("type") == "acquire_stage":
            print(f"[live] {ev['message']}", file=sys.stderr)
        elif ev.get("type") == "acquire_item":
            count["n"] += 1
            if ev["status"] != "acquired":
                print(f"[live] NOT acquired: {ev['path']}", file=sys.stderr)
            elif count["n"] % 50 == 0:
                print(f"[live] {count['n']} items copied", file=sys.stderr)

    try:
        result, acq = live.run_live(
            Path(output), case={"case": args.case or "", "examiner": args.examiner or ""},
            workers=args.workers, allow_same_drive=args.allow_system_drive,
            progress=None if quiet else stderr_progress, on_event=on_event,
            export_files=not args.no_export, html=not args.no_html, xlsx=not args.no_xlsx, pdf=not args.no_pdf)
    except live.AcquisitionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return finish(2)
    s = acq.summary()
    print(f"acquired {s['files_acquired']} file(s) ({s['bytes_acquired'] / 1e6:.1f} MB, {s['hives_saved']} "
          f"registry hive(s) saved) with {s['errors']} error(s); record: {Path(output) / 'acquisition.json'}",
          file=sys.stderr)
    phones = [d for d in result.graph.devices if d.device_class in (None, "phone", "tablet")]
    print(f"{len(phones)} phone(s) found; report: {Path(output) / 'report' / 'report.html'}", file=sys.stderr)
    return finish(0)


def _cmd_demo(args: argparse.Namespace) -> int:
    from .demo import build_demo_evidence
    from .engine import scan, stderr_progress
    from .output import write_all

    out = Path(args.output)
    if out.exists() and any(out.iterdir()):
        print(f"error: {out} is not empty", file=sys.stderr)
        return 2
    evidence = build_demo_evidence(out)
    print(f"synthetic evidence written to {evidence}", file=sys.stderr)
    result = scan(evidence, case={"case": "DEMO (synthetic data)", "examiner": ""},
                  progress=stderr_progress, export_dir=out / "report" / "exports")
    written = write_all(result, out / "report")
    for name in ("report.html", "summary.pdf", "workbook.xlsx"):
        if out / "report" / name in written:
            print(f"wrote {out / 'report' / name}", file=sys.stderr)
    return 0


def _cmd_plugins(args: argparse.Namespace) -> int:
    from .plugin import load_plugins

    metas = [cls.meta() for cls in load_plugins().values()]
    if args.json:
        print(json.dumps(metas, indent=2))
        return 0
    for m in metas:
        print(f"{m['id']:<34} {m['os']:<8} {m['channel']:<16} {m['validation']:<14} {m['name']}")
    return 0


def _cmd_targets(args: argparse.Namespace) -> int:
    from .targets import render_targets

    print(render_targets(args.format))
    return 0


def _cmd_docs(args: argparse.Namespace) -> int:
    from .targets import render_docs

    text = render_docs()
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def _cmd_locate(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from . import geo

    try:
        macs = [geo.normalise(b) for b in args.bssid]
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    to_send = [m for m in macs if not geo.is_randomised(m)]
    if to_send and not args.yes:
        print(f"This sends {len(to_send)} address(es) to {geo.SERVICE}: Apple learns which addresses were asked "
              "about, and when. Randomised addresses are never sent.", file=sys.stderr)
        if not sys.stdin.isatty():
            print("error: add --yes to send them without asking", file=sys.stderr)
            return 2
        if input("Send them? [y/N] ").strip().lower() not in ("y", "yes"):
            print("nothing was sent", file=sys.stderr)
            return 1
    results = []
    for mac in macs:
        sent = mac in to_send
        try:
            loc = geo.locate(mac)
        except OSError as exc:  # no connection, timeout, refused
            loc = geo.Location(mac, False, note=f"the lookup failed: {exc}")
        geo.record_lookup(args.case_dir, loc, examiner=args.examiner or "", sent=sent)
        results.append(loc)
        if not args.json:
            if loc.found:
                print(f"{mac}  {loc.latitude:.6f}, {loc.longitude:.6f}  within about {loc.accuracy_m} m  "
                      f"{loc.map_url}")
            else:
                print(f"{mac}  no location ({loc.note})")
    if args.json:
        print(json.dumps([{**asdict(r), "map": r.map_url} for r in results], indent=2))
    if args.case_dir and not args.json:
        print(f"logged in {Path(args.case_dir) / 'online-lookups.jsonl'}", file=sys.stderr)
    return 0


def _cmd_save_files(args: argparse.Namespace) -> int:
    from . import content
    from . import export as file_export
    from .casedata import CaseData

    try:
        case = CaseData.load(args.results)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    items = content.extract(case.records, case.devices)
    kinds = [k.strip() for k in args.kind.split(",")] if args.kind else list(items)
    unknown = [k for k in kinds if k not in items]
    if unknown:
        print(f"error: unknown kind {', '.join(unknown)} (choose from {', '.join(items)})", file=sys.stderr)
        return 2
    rows = [r for k in kinds for r in items[k]]
    if args.device:
        rows = [r for r in rows if r.get("device") == args.device]
    if args.status == "present":
        rows = [r for r in rows if not r.get("status")]
    elif args.status == "deleted":
        rows = [r for r in rows if r.get("status")]
    files = file_export.with_files(rows)
    if not files:
        print("no saved files match", file=sys.stderr)
        return 1
    args.output.mkdir(parents=True, exist_ok=True)
    title = f"{case.title} - " + (", ".join(kinds) if args.kind else "recovered files")
    res = file_export.save_files(args.results, files, args.output, title=title, case=case.case)
    print(f"saved {len(res.saved)} file(s), {res.total_bytes:,} bytes, to {res.folder}", file=sys.stderr)
    if res.mismatched:
        print(f"warning: {len(res.mismatched)} file(s) differ from the hash recorded at the scan (manifest.csv)",
              file=sys.stderr)
    if res.skipped:
        print(f"{len(res.skipped)} item(s) not saved (manifest.json says why)", file=sys.stderr)
    return 3 if res.mismatched else 0


def _cmd_validate(args: argparse.Namespace) -> int:
    from .validate import render_markdown, validate

    report = validate(args.truth, args.results)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_markdown(report))
    s = report["summary"]
    return 0 if s["expected"] and s["found"] == s["expected"] else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cpf",
        description="Recover smartphone evidence from the computers a phone was connected to.",
    )
    p.add_argument("--version", action="version", version=f"cpf {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan a mounted image or triage collection")
    s.add_argument("input", type=Path, help="mounted volume, extracted image or collection directory")
    s.add_argument("-o", "--output", type=Path, required=True, help="output directory")
    s.add_argument("--plugins", help="comma-separated plugin ids to run (default: all)")
    s.add_argument("--case", help="case name shown in the reports (e.g. ACME-Incident-EmpID39323)")
    s.add_argument("--examiner", help="examiner name shown in the report")
    s.add_argument("--workdir", type=Path, help="directory for temporary copies (default: system temp)")
    s.add_argument("--no-html", action="store_true", help="skip the HTML report")
    s.add_argument("--no-xlsx", action="store_true", help="skip the Excel workbook (workbook.xlsx)")
    s.add_argument("--no-pdf", action="store_true", help="skip the PDF summary (summary.pdf)")
    s.add_argument("--no-export", action="store_true",
                   help="do not save recovered files (photos, attachments); hashes are still recorded")
    s.add_argument("--assume-same", action="append", default=[], metavar="KIND=VALUE,KIND=VALUE",
                   help="record an examiner conclusion that these strong identifiers are one phone "
                        "(e.g. udid=...,bt_mac=...); repeatable; listed in the report and manifest")
    s.add_argument("--workers", type=int, metavar="N",
                   help="parser threads (default: one per CPU, at most 8; 1 runs parsers one at a time)")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=_cmd_scan)

    lv = sub.add_parser("live", help="acquire this running Windows computer's evidence, then scan it "
                                     "(as administrator)",
                        description="Copy the evidence the Windows parsers read from this running computer: "
                                    "registry hives are saved through the registry API (RegSaveKeyEx), files "
                                    "are read with the backup privilege and hashed, and their times are kept. "
                                    "The copy is then scanned and reported. The output must be on another "
                                    "drive than the system drive (a USB drive or external disk).")
    lv.add_argument("-o", "--output", type=Path,
                    help="new or empty output folder on another drive (default: a new folder under "
                         "'CPF cases' on the drive this program runs from, or on a removable drive)")
    lv.add_argument("--case", help="case name shown in the reports (e.g. ACME-Incident-EmpID39323)")
    lv.add_argument("--examiner", help="examiner name shown in the report")
    lv.add_argument("--elevate", action="store_true",
                    help="if not running as administrator, start again as administrator (UAC prompt)")
    lv.add_argument("--allow-system-drive", action="store_true",
                    help="allow the output on the drive being examined (not recommended: overwrites free "
                         "space that may hold deleted evidence; recorded as a warning)")
    lv.add_argument("--no-html", action="store_true", help="skip the HTML report")
    lv.add_argument("--no-xlsx", action="store_true", help="skip the Excel workbook (workbook.xlsx)")
    lv.add_argument("--no-pdf", action="store_true", help="skip the PDF summary (summary.pdf)")
    lv.add_argument("--no-export", action="store_true", help="do not save recovered files (photos, attachments)")
    lv.add_argument("--workers", type=int, metavar="N", help="parser threads")
    lv.add_argument("--pause-at-end", action="store_true", help=argparse.SUPPRESS)
    lv.add_argument("-q", "--quiet", action="store_true")
    lv.set_defaults(func=_cmd_live)

    dm = sub.add_parser("demo", help="build a synthetic three-computer case and scan it")
    dm.add_argument("output", type=Path, help="empty directory for the demo evidence and report")
    dm.set_defaults(func=_cmd_demo)

    pl = sub.add_parser("plugins", help="list parsers")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=_cmd_plugins)

    t = sub.add_parser("targets", help="print collection targets for the files Connected Phone Forensics reads")
    t.add_argument("--format", choices=["list", "kape", "velociraptor"], default="list")
    t.set_defaults(func=_cmd_targets)

    lc = sub.add_parser("locate", help="look up Wi-Fi access points (BSSIDs) in Apple's location service (online)",
                        description="Ask Apple's Wi-Fi location service where each access point address (BSSID) "
                                    "has been seen. This is an online query: Apple learns which addresses were "
                                    "asked about. Randomised (locally administered) addresses are never sent. "
                                    "A phone's hotspot moves with the phone; fixed routers give the most useful "
                                    "answers.")
    lc.add_argument("bssid", nargs="+", help="access point address, e.g. 3c:22:fb:9a:10:4e")
    lc.add_argument("--yes", action="store_true", help="send without asking")
    lc.add_argument("--case-dir", type=Path, help="case folder: each lookup is logged in online-lookups.jsonl")
    lc.add_argument("--examiner", help="examiner name recorded with each lookup")
    lc.add_argument("--json", action="store_true")
    lc.set_defaults(func=_cmd_locate)

    sf = sub.add_parser("save-files", help="save recovered photos, attachments, contact pictures and app icons as "
                                           "verified copies, with a manifest",
                        description="Copy the files a scan recovered, byte for byte, into a new folder below "
                                    "OUTPUT. Each copy is hashed while written and read back, compared with the "
                                    "hash recorded at the scan, marked read-only and given its record's time; "
                                    "manifest.csv, manifest.json and SHA256SUMS.txt describe every file.")
    sf.add_argument("results", type=Path, help="output directory of 'cpf scan' (the report folder)")
    sf.add_argument("-o", "--output", type=Path, required=True, help="where the new folder is made")
    sf.add_argument("--kind", help="comma-separated: media, contacts, apps, messages... (default: every kind)")
    sf.add_argument("--device", metavar="DEV-ID", help="only this device's files")
    sf.add_argument("--status", choices=["any", "present", "deleted"], default="any",
                    help="present (live records) or deleted, recovered")
    sf.set_defaults(func=_cmd_save_files)

    v = sub.add_parser("validate", help="score scan results against a ground-truth file")
    v.add_argument("truth", type=Path, help="ground-truth JSON (see docs/VALIDATION.md)")
    v.add_argument("results", type=Path, help="output directory of 'cpf scan'")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=_cmd_validate)

    d = sub.add_parser("docs", help="generate the artifact reference (Markdown)")
    d.add_argument("-o", "--output", type=Path)
    d.set_defaults(func=_cmd_docs)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
