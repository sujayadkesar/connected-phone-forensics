"""Overview: the case at a glance."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QWidget

from ... import content
from ..casedata import CLASS_LABEL, CaseData, channel_label, fmt_duration, fmt_time, os_label
from ..icons import OS_ICON, device_icon
from ..widgets import (
    BarList,
    Card,
    Chip,
    ChipRow,
    Divider,
    IconBadge,
    KeyValueGrid,
    PageHeader,
    PageScroll,
    StatTile,
    button,
    clear_layout,
    hbox,
    label,
    vbox,
)
from ..window import header_actions


def device_summary(d: dict) -> str:
    parts = [os_label(d.get("phone_os")) if d.get("phone_os") else CLASS_LABEL.get(d.get("device_class") or "", "")]
    parts.append("confirmed" if d.get("confirmed") else "weak identifiers only")
    n = len(d.get("systems") or [])
    parts.append(f"{n} computer{'s' if n != 1 else ''}")
    return " · ".join(p for p in parts if p)


class DeviceRow(Card):
    """One phone in a list: icon, name, how it connected, when last seen."""

    def __init__(self, d: dict, on_open):
        super().__init__(padding=12, spacing=6, clickable=True)
        tone = "ok" if d.get("confirmed") else "warn"
        badge = IconBadge(device_icon(d.get("device_class")), tone, 38)
        name = label(d.get("label") or d["id"], "H3")
        sub = label(device_summary(d), "Small")
        chips = ChipRow([Chip.channel(c["channel"]) for c in d.get("channels", [])[:5]])
        last = vbox(label("Last seen", "Small"), label(fmt_time(d.get("last_seen"), False), "Mono", mono=True),
                    "stretch", spacing=1)
        row = hbox(badge, (vbox(name, sub, chips, spacing=4), 1), last, spacing=14)
        row.setAlignment(badge, Qt.AlignmentFlag.AlignTop)
        self.body.addLayout(row)
        self.clicked.connect(lambda: on_open(d["id"]))
        self.setToolTip("Open this device")


class OverviewPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        content = QWidget()
        lay = vbox(spacing=20)
        content.setLayout(lay)
        self.header = PageHeader("Overview")
        for b in header_actions(window):
            self.header.actions.addWidget(b)
        lay.addWidget(self.header)

        tiles = QGridLayout()
        tiles.setSpacing(14)
        self.t_phones = StatTile("ph.device-mobile", "Phones and tablets", "accent")
        self.t_other = StatTile("ph.headphones", "Other devices", "muted")
        self.t_records = StatTile("ph.clock-counter-clockwise", "Records", "accent")
        self.t_issues = StatTile("ph.warning-circle", "Issues", "ok")
        self.t_phones.clicked.connect(lambda: window.go("devices"))
        self.t_other.clicked.connect(lambda: window.go("devices", "__other__"))
        self.t_records.clicked.connect(lambda: window.go("timeline"))
        self.t_issues.clicked.connect(lambda: window.go("issues"))
        for i, t in enumerate((self.t_phones, self.t_other, self.t_records, self.t_issues)):
            tiles.addWidget(t, 0, i)
        lay.addLayout(tiles)

        self.recovered = Card("Recovered from the phones", "Messages, calls, contacts, photos and more the computers "
                                                             "held. Click a kind to see every item.")
        self.recovered.add_action(button("Recovered data", "Ghost", "ph.arrow-right", lambda: window.go("data")))
        self.recovered_row = hbox(spacing=10)
        self.recovered.body.addLayout(self.recovered_row)
        lay.addWidget(self.recovered)

        self.matches = Card("Records that may be the same phone", tone="warn")
        self.matches_text = label("", "Muted", wrap=True)
        self.matches.body.addWidget(self.matches_text)
        self.matches.add_action(button("Review on Devices", None, "ph.arrow-right", lambda: window.go("devices")))
        lay.addWidget(self.matches)

        cols = hbox(spacing=16)
        left, right = vbox(spacing=16), vbox(spacing=16)
        cols.addLayout(left, 3)
        cols.addLayout(right, 2)
        lay.addLayout(cols)

        self.phones = Card("Phones", "Every handset and tablet found, strongest identification first.")
        self.phones.add_action(button("All devices", "Ghost", "ph.arrow-right", lambda: window.go("devices")))
        self.phone_list = vbox(spacing=8)
        self.phones.body.addLayout(self.phone_list)
        left.addWidget(self.phones)
        left.addStretch(1)

        self.computers = Card("Computers", "Systems found in the evidence.")
        self.computer_list = vbox(spacing=10)
        self.computers.body.addLayout(self.computer_list)
        right.addWidget(self.computers)

        self.channels = Card("Records by connection type")
        self.bars = BarList()
        self.channels.body.addWidget(self.bars)
        right.addWidget(self.channels)

        self.live_card = Card("Live acquisition", "Acquired from the running computer, as administrator.")
        self.live_kv = KeyValueGrid()
        self.live_card.body.addWidget(self.live_kv)
        self.live_card.add_action(button("Record", "Ghost", "ph.file-text", self._open_acquisition,
                                         "Open acquisition.json: every item with its source, method and SHA-256"))
        right.addWidget(self.live_card)

        self.case_card = Card("Case")
        self.case_kv = KeyValueGrid()
        self.case_card.body.addWidget(self.case_kv)
        right.addWidget(self.case_card)
        right.addStretch(1)

        page = vbox()
        page.addWidget(PageScroll(content))
        self.setLayout(page)

    def set_case(self, case: CaseData) -> None:
        sub = []
        if case.case.get("examiner"):
            sub.append(f"Examiner {case.case['examiner']}")
        if case.started:
            sub.append(f"scanned {fmt_time(case.started, False)} UTC")
        if case.duration is not None:
            sub.append(f"took {fmt_duration(case.duration)}")
        self.header.set(case.title, " · ".join(sub))

        phones, others = case.phones, case.others
        confirmed = sum(1 for d in phones if d.get("confirmed"))
        self.t_phones.set(f"{len(phones)}", f"{confirmed} confirmed by a strong identifier" if phones else
                          "None found")
        self.t_other.set(f"{len(others)}", "Headsets, computers, peripherals" if others else "None found")
        timed = sum(1 for r in case.records if r.get("datetime"))
        self.t_records.set(f"{len(case.records):,}", f"{timed:,} with a time, from {len(case.coverage):,} files")
        issues = len(case.warnings) + len(case.errors)
        self.t_issues.set(f"{issues}", "Warnings and parser errors" if issues else "Every parser ran cleanly",
                          tone="bad" if case.errors else "warn" if issues else "ok")

        groups = case.candidate_groups
        self.matches.setVisible(bool(groups))
        if groups:
            n = sum(len(g["devices"]) for g in groups)
            self.matches_text.setText(
                f"{n} device records in {len(groups)} group{'s' if len(groups) != 1 else ''} share names, "
                "phone numbers or related addresses, but no identifier strong enough to merge them "
                "automatically. Review them before relying on device counts.")

        clear_layout(self.phone_list)
        if phones:
            for d in phones[:12]:
                self.phone_list.addWidget(DeviceRow(d, lambda i: self.window_.go("devices", i)))
            if len(phones) > 12:
                self.phone_list.addWidget(label(f"and {len(phones) - 12} more on the Devices page", "Small"))
        else:
            self.phone_list.addWidget(label("No phones were identified in this evidence.", "Muted"))

        clear_layout(self.computer_list)
        for i, s in enumerate(case.systems):
            if i:
                self.computer_list.addWidget(Divider())
            det = s.get("details") or {}
            product = det.get("product") or det.get("pretty_name") or \
                (f"macOS {det['product_version']}" if det.get("product_version") else os_label(s.get("os")))
            users = ", ".join(s.get("users") or []) or "no user profiles"
            text = vbox(label(s["label"], "H3"), label(product, "Small"), label(f"Users: {users}", "Small"),
                        spacing=1)
            self.computer_list.addLayout(hbox(IconBadge(OS_ICON.get(s.get("os"), "ph.desktop"), "muted", 34),
                                              (text, 1), spacing=12))

        from .data import CategoryTile

        clear_layout(self.recovered_row)
        items = self.window_.pages["data"].items
        for cat in content.CATEGORIES:
            rows = items.get(cat.key) or []
            tile = CategoryTile(cat.key, cat.title, cat.icon)
            tile.set(len(rows), sum(1 for r in rows if r.get("status")), len(rows))
            tile.clicked.connect(lambda key: self.window_.go("data", key))
            self.recovered_row.addWidget(tile, 1)
        self.recovered.setVisible(any(items.get(c.key) for c in content.CATEGORIES))

        a = case.acquisition
        self.live_card.setVisible(bool(a))
        self.live_kv.clear()
        if a:
            drive = a.get("destination_drive") or {}
            self.live_kv.add("Computer", f"{a.get('computer')} · {a.get('windows') or 'Windows'}")
            self.live_kv.add("By", f"{a.get('user')}" + (" (administrator)" if a.get("elevated") else ""))
            self.live_kv.add("Started", f"{fmt_time(a.get('started'))} UTC")
            self.live_kv.add("Acquired", f"{a.get('files_acquired', 0):,} files · {a.get('hives_saved', 0)} registry "
                                         f"hives · {(a.get('bytes_acquired') or 0) / 1e6:,.1f} MB")
            self.live_kv.add("Errors", f"{a.get('errors', 0):,}" + (" (see Issues)" if a.get("errors") else ""))
            self.live_kv.add("Output", f"{drive.get('root', '')} {drive.get('label') or ''} "
                                       f"({drive.get('kind', '')} {drive.get('filesystem', '')})".strip())

        counts = case.channel_counts()
        self.bars.set([(channel_label(c), n, f"{channel_label(c)}: {n:,} record{'s' if n != 1 else ''}")
                       for c, n in counts])
        self.channels.setVisible(bool(counts))

        self.case_kv.clear()
        self.case_kv.add("Case", case.case.get("case") or "—")
        self.case_kv.add("Examiner", case.case.get("examiner") or "—")
        self.case_kv.add("Evidence", case.input_path, mono=True)
        self.case_kv.add("Output", str(case.outdir) if case.outdir else "—", mono=True)
        self.case_kv.add("Started", f"{fmt_time(case.started)} UTC" if case.started else "—")
        self.case_kv.add("Tool", f"version {case.tool_version}")

    def _open_acquisition(self) -> None:
        case = self.window_.case
        if case and case.outdir:
            record = Path(case.outdir).parent / "acquisition.json"
            self.window_.open_path(record if record.is_file() else Path(case.outdir).parent)


