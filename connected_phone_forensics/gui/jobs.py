"""Background work: scans, case loading and evidence previews run off the UI thread.

A :class:`Job` runs a function on Qt's thread pool and reports back through
signals, which Qt delivers on the UI thread. The scan itself spreads its
parsers over a thread pool of its own (see :func:`connected_phone_forensics.engine.scan`).
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

_running: set[Job] = set()  # keep jobs alive until their result reaches the UI thread


class Job(QObject):
    progress = Signal(object)  # dict events, see ScanParams / scan_job
    done = Signal(object)
    failed = Signal(str, str)  # short message, details
    cancelled = Signal()

    def __init__(self, fn: Callable[[Job], Any]):
        super().__init__()
        self._fn = fn
        self.cancel_event = threading.Event()

    def start(self) -> Job:
        _running.add(self)
        QThreadPool.globalInstance().start(_Runner(self))
        return self

    def cancel(self) -> None:
        self.cancel_event.set()

    def _run(self) -> None:
        from ..engine import ScanCancelled

        try:
            result = self._fn(self)
        except ScanCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001 - reported to the user, never swallowed
            self.failed.emit(f"{type(exc).__name__}: {exc}", traceback.format_exc())
        else:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.done.emit(result)


class _Runner(QRunnable):
    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self.setAutoDelete(True)

    def run(self) -> None:
        self.job._run()


def run(fn: Callable[[Job], Any], *, done: Callable[[Any], None] | None = None,
        failed: Callable[[str, str], None] | None = None, progress: Callable[[dict], None] | None = None,
        cancelled: Callable[[], None] | None = None) -> Job:
    job = Job(fn)
    if done:
        job.done.connect(done)
    if failed:
        job.failed.connect(failed)
    if progress:
        job.progress.connect(progress)
    if cancelled:
        job.cancelled.connect(cancelled)
    # Released only once the result has been delivered on the UI thread (these run after the slots
    # above); dropping the job any earlier can delete it with its result still queued.
    for signal in (job.done, job.failed, job.cancelled):
        signal.connect(lambda *_: _running.discard(job))
    return job.start()


@dataclass
class ScanParams:
    evidence: Path
    output: Path
    case: dict[str, str] = field(default_factory=dict)
    plugin_ids: list[str] | None = None
    export_files: bool = True
    workers: int | None = None
    workdir: Path | None = None
    demo: bool = False  # build the synthetic demo evidence into ``evidence`` first


def scan_job(params: ScanParams) -> Callable[[Job], Any]:
    def work(job: Job):
        from ..engine import scan
        from ..output import write_all
        from .casedata import CaseData

        evidence = params.evidence
        if params.demo:
            from ..demo import build_demo_evidence

            job.progress.emit({"type": "log", "message": "building the synthetic demo evidence"})
            evidence = build_demo_evidence(params.evidence)
        result = scan(
            evidence, plugin_ids=params.plugin_ids, workdir=params.workdir, case=params.case,
            progress=lambda msg: job.progress.emit({"type": "log", "message": msg}),
            export_dir=params.output / "exports" if params.export_files else None,
            workers=params.workers, on_event=job.progress.emit, cancel=job.cancel_event,
        )
        job.progress.emit({"type": "writing"})
        params.output.mkdir(parents=True, exist_ok=True)
        write_all(result, params.output)
        return CaseData.from_result(result, params.output)

    return work


def load_job(outdir: Path) -> Callable[[Job], Any]:
    def work(_job: Job):
        from .casedata import CaseData

        return CaseData.load(outdir)

    return work


def discover_job(path: Path) -> Callable[[Job], Any]:
    """Preview which computers a folder holds, before the examiner starts the scan."""

    def work(_job: Job):
        from ..evidence import discover_systems

        return [{"label": s.label, "os": s.os, "details": s.details, "users": [u.name for u in s.users]}
                for s in discover_systems(path)]

    return work
