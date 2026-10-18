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
    written = write_all(result, args.output, html=not args.no_html)
    if not args.quiet:
        for p in written:
            print(f"wrote {p}", file=sys.stderr)
    return 0


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
    print(f"open {written[-1]}", file=sys.stderr)
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
        prog="tether",
        description="Recover smartphone evidence from the computers a phone was connected to.",
    )
    p.add_argument("--version", action="version", version=f"tether {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("scan", help="scan a mounted image or triage collection")
    s.add_argument("input", type=Path, help="mounted volume, extracted image or collection directory")
    s.add_argument("-o", "--output", type=Path, required=True, help="output directory")
    s.add_argument("--plugins", help="comma-separated plugin ids to run (default: all)")
    s.add_argument("--case", help="case reference shown in the report")
    s.add_argument("--examiner", help="examiner name shown in the report")
    s.add_argument("--workdir", type=Path, help="directory for temporary copies (default: system temp)")
    s.add_argument("--no-html", action="store_true", help="skip the HTML report")
    s.add_argument("--no-export", action="store_true",
                   help="do not save recovered files (photos, attachments); hashes are still recorded")
    s.add_argument("--assume-same", action="append", default=[], metavar="KIND=VALUE,KIND=VALUE",
                   help="record an examiner conclusion that these strong identifiers are one phone "
                        "(e.g. udid=...,bt_mac=...); repeatable; listed in the report and manifest")
    s.add_argument("--workers", type=int, metavar="N",
                   help="parser threads (default: one per CPU, at most 8; 1 runs parsers one at a time)")
    s.add_argument("-q", "--quiet", action="store_true")
    s.set_defaults(func=_cmd_scan)

    dm = sub.add_parser("demo", help="build a synthetic three-computer case and scan it")
    dm.add_argument("output", type=Path, help="empty directory for the demo evidence and report")
    dm.set_defaults(func=_cmd_demo)

    pl = sub.add_parser("plugins", help="list parsers")
    pl.add_argument("--json", action="store_true")
    pl.set_defaults(func=_cmd_plugins)

    t = sub.add_parser("targets", help="print collection targets for the files Tether reads")
    t.add_argument("--format", choices=["list", "kape", "velociraptor"], default="list")
    t.set_defaults(func=_cmd_targets)

    v = sub.add_parser("validate", help="score scan results against a ground-truth file")
    v.add_argument("truth", type=Path, help="ground-truth JSON (see docs/VALIDATION.md)")
    v.add_argument("results", type=Path, help="output directory of 'tether scan'")
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
