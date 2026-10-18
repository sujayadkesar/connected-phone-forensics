"""Scan progress: steps, a progress bar, live counts and the activity log."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QPlainTextEdit, QProgressBar, QWidget

from .. import jobs
from ..casedata import fmt_duration
from ..widgets import (
    Card,
    IconBadge,
    PageScroll,
    Spinner,
    button,
    hbox,
    label,
    mono_font,
    overline,
    vbox,
)

STEPS = [("discover", "Find computers"), ("parse", "Run parsers"), ("link", "Link devices"),
         ("write", "Write report")]


class Step(QWidget):
    def __init__(self, text: str, number: int):
        super().__init__()
        self.badge = IconBadge("ph.check-bold", "ok", 28)
        self.badge.hide()
        self.number = label(str(number), "StepNumber")
        self.number.setFixedSize(28, 28)
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.spinner = Spinner(22)
        self.spinner.hide()
        self.text = label(text, "Muted")
        self.setLayout(hbox(self.number, self.badge, self.spinner, self.text, spacing=10))
        self.state = "pending"

    def set_state(self, state: str) -> None:
        self.state = state
        if state == "active":
            self.spinner.start()
        else:
            self.spinner.stop()
        self.number.setVisible(state == "pending")
        self.badge.setVisible(state in ("done", "failed"))
        if state == "done":
            self.badge.icon_name = "ph.check-bold"
            self.badge.set_tone("ok")
        elif state == "failed":
            self.badge.icon_name = "ph.x-bold"
            self.badge.set_tone("bad")
        self.text.setObjectName("H3" if state == "active" else "Muted")
        self.text.style().unpolish(self.text)
        self.text.style().polish(self.text)


class Counter(QWidget):
    def __init__(self, title: str):
        super().__init__()
        self.value = label("0", "H2")
        self.setLayout(vbox(self.value, label(title, "Small"), spacing=0))

    def set(self, n: int) -> None:
        self.value.setText(f"{n:,}")


class ScanPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self._started = 0.0
        self._timer = QTimer(self, interval=250)
        self._timer.timeout.connect(self._tick)

        card = Card(padding=28, spacing=18)
        self.spinner = Spinner(22)
        self.result_badge = IconBadge("ph.check-bold", "ok", 34)
        self.result_badge.hide()
        self.title = label("Scanning", "H1")
        self.subtitle = label("", "Muted", wrap=True, select=True)
        card.body.addLayout(hbox(self.spinner, self.result_badge, vbox(self.title, self.subtitle, spacing=3),
                                 spacing=14))

        self.steps = {key: Step(text, i) for i, (key, text) in enumerate(STEPS, 1)}
        steps_row = hbox(spacing=0)
        for i, step in enumerate(self.steps.values()):
            if i:
                steps_row.addStretch(1)
            steps_row.addWidget(step)
        card.body.addLayout(steps_row)

        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setRange(0, 0)
        self.status = label("Starting…", "Muted", wrap=True)
        self.elapsed = label("", "Small")
        card.body.addLayout(vbox(self.bar, hbox((self.status, 1), self.elapsed), spacing=8))

        self.c_systems = Counter("Computers")
        self.c_parsers = Counter("Parsers finished")
        self.c_records = Counter("Records found")
        self.c_files = Counter("Files read")
        self.c_issues = Counter("Issues")
        counters = hbox(spacing=28)
        for c in (self.c_systems, self.c_parsers, self.c_records, self.c_files, self.c_issues):
            counters.addWidget(c)
        counters.addStretch(1)
        card.body.addLayout(counters)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.log.setMinimumHeight(180)
        self.log.setFont(mono_font())
        card.body.addWidget(overline("Activity"))
        card.body.addWidget(self.log, 1)

        self.error = label("", "Error", wrap=True, select=True)
        self.error.hide()
        card.body.addWidget(self.error)

        self.cancel_btn = button("Cancel scan", "Danger", "ph.stop-circle", window.cancel_scan)
        self.back_btn = button("Back to new scan", None, "ph.arrow-left", lambda: self.window_.go("new"))
        self.back_btn.hide()
        card.body.addLayout(hbox("stretch", self.back_btn, self.cancel_btn))

        content = QWidget()
        content.setLayout(vbox(card, "stretch"))
        page = vbox()
        page.addWidget(PageScroll(content, max_width=900, margins=(32, 40, 32, 32)))
        self.setLayout(page)

    # -- lifecycle -------------------------------------------------------------------

    def begin(self, params: jobs.ScanParams) -> None:
        self._started = time.monotonic()
        self._timer.start()
        self._records = self._files = self._issues = 0
        self.title.setText("Building the demo case" if params.demo else "Scanning the evidence")
        self.subtitle.setText(str(params.evidence))
        self.spinner.start()
        self.result_badge.hide()
        self.bar.setRange(0, 0)
        self.status.setText("Looking for computers…")
        self.log.clear()
        self.error.hide()
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        self.back_btn.hide()
        for c in (self.c_systems, self.c_parsers, self.c_records, self.c_files, self.c_issues):
            c.set(0)
        for key, step in self.steps.items():
            step.set_state("active" if key == "discover" else "pending")
        self._append(f"evidence: {params.evidence}")
        self._append(f"output:   {params.output}")

    def on_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "log":
            self._append(ev["message"])
        elif kind == "discovered":
            self.steps["discover"].set_state("done")
            self.steps["parse"].set_state("active")
            self.c_systems.set(len(ev["systems"]))
            self.bar.setRange(0, max(1, ev["tasks"]))
            self.bar.setValue(0)
            names = ", ".join(s["label"] for s in ev["systems"]) or "no computers"
            self.status.setText(f"Running {ev['tasks']} parsers on {ev['workers']} threads · {names}")
        elif kind == "task_done":
            self.bar.setValue(ev["done"])
            self.c_parsers.set(ev["done"])
            self._records += ev["observations"]
            self._files += ev["files"]
            self._issues += ev["warnings"]
            self.c_records.set(self._records)
            self.c_files.set(self._files)
            self.c_issues.set(self._issues)
            self.status.setText(f"{ev['done']} of {ev['total']} parsers · last: {ev['name']} on {ev['system']}")
        elif kind == "correlating":
            self.steps["parse"].set_state("done")
            self.steps["link"].set_state("active")
            self.bar.setRange(0, 0)
            self.status.setText(f"Linking {ev['observations']:,} records into devices…")
        elif kind == "writing":
            self.steps["link"].set_state("done")
            self.steps["write"].set_state("active")
            self.status.setText("Writing the report, timeline and hashes…")

    def _append(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _tick(self) -> None:
        self.elapsed.setText(fmt_duration(time.monotonic() - self._started))

    def cancelling(self) -> None:
        self.cancel_btn.setEnabled(False)
        self.status.setText("Stopping after the running parsers finish…")
        self._append("cancel requested")

    def _stop(self, ok: bool) -> None:
        self._timer.stop()
        self._tick()
        self.spinner.stop()
        self.cancel_btn.hide()
        self.result_badge.show()
        self.result_badge.icon_name = "ph.check-bold" if ok else "ph.x-bold"
        self.result_badge.set_tone("ok" if ok else "bad")

    def finish(self) -> None:
        self._stop(True)
        for step in self.steps.values():
            step.set_state("done")
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.title.setText("Scan finished")
        self.status.setText("Done")

    def failed(self, message: str, details: str) -> None:
        self._stop(False)
        active = next((s for s in self.steps.values() if s.state == "active"), None)
        if active:
            active.set_state("failed")
        self.bar.setRange(0, 1)
        self.bar.setValue(0)
        self.title.setText("The scan failed")
        self.status.setText("Nothing was written for this scan.")
        self.error.setText(message)
        self.error.show()
        self._append(details)
        self.back_btn.show()

    def cancelled(self) -> None:
        self._stop(False)
        self.result_badge.icon_name = "ph.stop-circle"
        self.result_badge.set_tone("warn")
        for step in self.steps.values():
            if step.state == "active":
                step.set_state("pending")
        self.title.setText("Scan cancelled")
        self.status.setText("Nothing was written for this scan.")
        self._append("scan cancelled")
        self.back_btn.show()

