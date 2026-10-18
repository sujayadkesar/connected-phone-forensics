"""Devices: a searchable list, and everything known about the selected device."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QSplitter, QWidget

from ..casedata import (
    CLASS_LABEL,
    ID_LABEL,
    STRENGTH_LABEL,
    CaseData,
    channel_label,
    fmt_time,
    os_label,
)
from ..icons import device_icon
from ..models import Column, RecordModel, make_table
from ..widgets import (
    Card,
    Chip,
    ChipRow,
    Divider,
    EmptyState,
    IconBadge,
    KeyValueGrid,
    PageScroll,
    SearchBox,
    Segmented,
    breakable,
    button,
    channel_color_fn,
    clear_layout,
    copy_text,
    hbox,
    label,
    tone_fn,
    vbox,
)
from .overview import device_summary

STRENGTH_TONE = {"strong": ("ok", "ph.shield-check"), "link": ("accent", "ph.link"),
                 "weak": ("faint", "ph.circle-dashed")}


def _fit_table(view, rows: int) -> None:
    """Size a small table to its rows so the page, not the table, scrolls."""
    h = view.horizontalHeader().sizeHint().height() + rows * view.verticalHeader().defaultSectionSize() + 4
    view.setFixedHeight(h)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


class DeviceItem(QWidget):
    def __init__(self, d: dict):
        super().__init__()
        badge = IconBadge(device_icon(d.get("device_class")), "ok" if d.get("confirmed") else "warn", 34)
        name = label(d.get("label") or d["id"], "H3")
        name.setMinimumWidth(10)
        sub = label(device_summary(d), "Small")
        self.setLayout(hbox(badge, (vbox(name, sub, spacing=1), 1), spacing=11, margins=(10, 8, 12, 8)))
        self.setToolTip(f"{d['id']} · {d.get('observation_count', 0)} records")


class DevicesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.case: CaseData | None = None
        self.current: str | None = None

        # list
        left = QFrame()
        left.setObjectName("Card")
        left.setMinimumWidth(320)
        left.setMaximumWidth(420)
        self.search = SearchBox("Search name, identifier, computer")
        self.search.search.connect(lambda *_: self._fill())
        self.scope = Segmented([("phones", "Phones"), ("all", "All"), ("other", "Other")])
        self.scope.changed.connect(lambda *_: self._fill())
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._picked)
        self.list_empty = label("No devices match.", "Muted")
        self.list_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_empty.hide()
        left.setLayout(vbox(self.search, self.scope, self.list, self.list_empty, spacing=10,
                            margins=(12, 12, 12, 12)))

        # detail
        self.detail = QWidget()
        self.detail_lay = vbox(spacing=16)
        self.detail.setLayout(self.detail_lay)
        self.scroll = PageScroll(self.detail, max_width=1000, margins=(24, 4, 8, 24))

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setHandleWidth(0)
        split.setChildrenCollapsible(False)
        holder = QWidget()
        holder.setLayout(vbox(left, margins=(24, 26, 0, 24)))
        split.addWidget(holder)
        split.addWidget(self.scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([360, 1000])
        self.setLayout(vbox(split))

    # -- data ------------------------------------------------------------------------

    def set_case(self, case: CaseData) -> None:
        self.case = case
        self.current = None
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.scope.set_count("phones", len(case.phones))
        self.scope.set_count("all", len(case.devices))
        self.scope.set_count("other", len(case.others))
        self.scope.select("phones" if case.phones else "all")
        self._fill()

    def _matches(self, d: dict, q: str) -> bool:
        if not q:
            return True
        hay = " ".join([d.get("label") or "", d["id"], " ".join(d.get("systems") or []),
                        " ".join(f"{i['kind']} {i['value']} {' '.join(i.get('as_recorded') or [])}"
                                 for i in d.get("identifiers") or [])]).lower()
        return all(term in hay for term in q.lower().split())

    def _fill(self) -> None:
        if not self.case:
            return
        scope = self.scope.current()
        pool = self.case.phones if scope == "phones" else self.case.others if scope == "other" else self.case.devices
        q = self.search.text().strip()
        shown = [d for d in pool if self._matches(d, q)]
        keep = self.current
        self.list.blockSignals(True)
        self.list.clear()
        select_row = 0
        for i, d in enumerate(shown):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, d["id"])
            w = DeviceItem(d)
            item.setSizeHint(QSize(0, w.sizeHint().height()))
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
            if d["id"] == keep:
                select_row = i
        self.list.blockSignals(False)
        self.list_empty.setVisible(not shown)
        self.list.setVisible(bool(shown))
        if shown:
            self.list.setCurrentRow(select_row)
        else:
            self._show(None)

    def focus_item(self, device_id: str) -> None:
        if not self.case:
            return
        if device_id == "__other__":
            self.scope.select("other")
            self._fill()
            return
        d = self.case.device(device_id)
        if d is None:
            return
        self.search.clear()
        is_phone = d in self.case.phones
        self.scope.select("phones" if is_phone else "other")
        self.current = device_id
        self._fill()

    def _picked(self, item: QListWidgetItem | None, _prev=None) -> None:
        self._show(item.data(Qt.ItemDataRole.UserRole) if item else None)

    # -- detail ----------------------------------------------------------------------

    def _show(self, device_id: str | None) -> None:
        self.current = device_id
        clear_layout(self.detail_lay)
        d = self.case.device(device_id) if self.case else None
        if d is None:
            self.detail_lay.addWidget(EmptyState("ph.device-mobile", "No device selected",
                                                 "Pick a device on the left to see its identifiers, how it "
                                                 "connected and where each fact came from."))
            return
        self.detail_lay.addSpacing(22)
        self.detail_lay.addLayout(self._header(d))
        self.detail_lay.addWidget(self._facts(d))
        self.detail_lay.addWidget(self._channels(d))
        self.detail_lay.addWidget(self._identifiers(d))
        if d.get("merge_reasons"):
            self.detail_lay.addWidget(self._reasons(d))
        matches = self.case.matches_for(d["id"])
        if matches:
            self.detail_lay.addWidget(self._possible(d, matches))
        if d.get("events"):
            events = Card("What was recorded", "Event types and how many records of each.")
            events.body.addWidget(ChipRow([Chip(f"{k.replace('_', ' ')}  {v}", None) for k, v in d["events"].items()]))
            self.detail_lay.addWidget(events)
        self.detail_lay.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)

    def _header(self, d: dict):
        confirmed = d.get("confirmed")
        badge = IconBadge(device_icon(d.get("device_class")), "ok" if confirmed else "warn", 52)
        chips = [Chip.status("Confirmed", "ok", "ph.shield-check",
                             "At least one strong identifier (UDID, serial, IMEI, MAC, ...) names this device")
                 if confirmed else
                 Chip.status("Unconfirmed", "warn", "ph.warning",
                             "Only weak identifiers (names, models): may be several devices, or part of one")]
        if d.get("phone_os"):
            chips.append(Chip(os_label(d["phone_os"]), None, "ph.apple-logo" if d["phone_os"] == "ios"
                              else "ph.android-logo"))
        if d.get("device_class"):
            chips.append(Chip(CLASS_LABEL.get(d["device_class"], d["device_class"])))
        title = label(d.get("label") or d["id"], "H1", wrap=True, select=True)
        ident = label(d["id"], "Faint", select=True)
        actions = hbox(
            button("Show in timeline", None, "ph.clock-counter-clockwise",
                   lambda: self.window_.go("timeline", {"device": d["id"]})),
            button("Copy ID", None, "ph.copy", lambda: copy_text(d["id"], self, f"Copied {d['id']}"),
                   "Copy the device ID"),
            spacing=8)
        row = hbox(badge, (vbox(title, ChipRow(chips), ident, spacing=6), 1), actions, spacing=16)
        row.setAlignment(badge, Qt.AlignmentFlag.AlignTop)
        row.setAlignment(actions, Qt.AlignmentFlag.AlignTop)
        return row

    def _facts(self, d: dict) -> Card:
        card = Card(padding=16)
        kv = KeyValueGrid(columns=2)
        kv.add("First seen", f"{fmt_time(d.get('first_seen'))} UTC" if d.get("first_seen") else "—")
        kv.add("Last seen", f"{fmt_time(d.get('last_seen'))} UTC" if d.get("last_seen") else "—")
        kv.add("Computers", ", ".join(d.get("systems") or []) or "—")
        kv.add("Local users", ", ".join(d.get("users") or []) or "—")
        kv.add("Records", f"{d.get('observation_count', 0):,}")
        kv.add("Identifiers", str(len(d.get("identifiers") or [])))
        card.body.addWidget(kv)
        return card

    def _channels(self, d: dict) -> Card:
        card = Card("Connections", "How this device reached the computers, and when.")
        rows = d.get("channels") or []
        model = RecordModel([
            Column("Connection", lambda r: channel_label(r["channel"]), 170,
                   chip=lambda r: (channel_label(r["channel"]), channel_color_fn(r["channel"]), None)),
            Column("First seen (UTC)", lambda r: fmt_time(r.get("first_seen")), 170, mono=True),
            Column("Last seen (UTC)", lambda r: fmt_time(r.get("last_seen")), 170, mono=True),
            Column("Records", lambda r: str(r.get("observations", 0)), 80, align_right=True,
                   sort_key=lambda r: r.get("observations", 0)),
            Column("Found by", lambda r: ", ".join(r.get("artifacts") or []), None, muted=True, tooltip=True),
        ])
        model.set_rows(rows)
        view = make_table(model)
        view.setAlternatingRowColors(False)
        _fit_table(view, len(rows))
        card.body.addWidget(view)
        return card

    def _identifiers(self, d: dict) -> Card:
        card = Card("Identifiers", "Only strong identifiers merge records into one device; links and weak "
                                   "identifiers are shown but never merge.")
        rows = d.get("identifiers") or []
        model = RecordModel([
            Column("Type", lambda r: ID_LABEL.get(r["kind"], r["kind"]), 150),
            Column("Value", lambda r: r["value"], None, mono=True, tooltip=True),
            Column("Strength", lambda r: STRENGTH_LABEL.get(r["strength"], r["strength"]), 120,
                   chip=lambda r: (STRENGTH_LABEL.get(r["strength"], r["strength"]),
                                   tone_fn(STRENGTH_TONE.get(r["strength"], ("faint", ""))[0]),
                                   STRENGTH_TONE.get(r["strength"], ("", "ph.circle"))[1])),
            Column("Found by", lambda r: ", ".join(r.get("sources") or []), 230, muted=True, tooltip=True),
        ])
        model.set_rows(rows)
        view = make_table(model)
        view.setAlternatingRowColors(False)
        _fit_table(view, len(rows))
        view.doubleClicked.connect(lambda idx: copy_text(model.row(idx.row())["value"], self, "Copied the value"))
        view.setToolTip("Double-click a row to copy its value")
        card.body.addWidget(view)
        as_recorded = [(i, i["as_recorded"]) for i in rows if i.get("as_recorded")]
        if as_recorded:
            card.body.addWidget(label("As recorded in the evidence: " + "; ".join(
                f"{ID_LABEL.get(i['kind'], i['kind'])} {', '.join(v)}" for i, v in as_recorded), "Small", wrap=True))
        return card

    def _reasons(self, d: dict) -> Card:
        reasons = d["merge_reasons"]
        card = Card("Why these records are one device",
                    f"{len(reasons)} artifact{'s' if len(reasons) != 1 else ''} named two strong identifiers "
                    "together.")
        for i, r in enumerate(reasons):
            if i:
                card.body.addWidget(Divider())
            card.body.addWidget(label(breakable(r.get("joined", "")), "Mono", wrap=True, select=True, mono=True))
            card.body.addWidget(label(f"{r.get('artifact', '')} · {r.get('source_path', '')}", "Small", wrap=True,
                                      select=True))
            loc = r.get("locator", "")
            if loc:
                card.body.addWidget(label(breakable(loc), "Faint", wrap=True, select=True, mono=True))
        return card

    def _possible(self, d: dict, matches: list[dict]) -> Card:
        card = Card("May be the same device as",
                    "Not merged: these share only weak signals. Verify before treating them as one phone.",
                    tone="warn")
        for i, m in enumerate(sorted(matches, key=lambda m: -m.get("independent_signals", 0))):
            if i:
                card.body.addWidget(Divider())
            other_id = next((x for x in m["devices"] if x != d["id"]), "")
            other = self.case.device(other_id) or {"label": other_id}
            n = m.get("independent_signals", 0)
            link = button(f"{other.get('label') or other_id}  ({other_id})", "Link", None,
                          lambda oid=other_id: self.focus_item(oid))
            card.body.addLayout(hbox(link, "stretch",
                                     label(f"{n} independent signal{'s' if n != 1 else ''}", "Small")))
            for sg in m.get("signals", []):
                card.body.addWidget(label(f"{sg.get('kind', '').replace('_', ' ')}: {sg.get('value', '')}", "Mono",
                                          wrap=True, select=True, mono=True))
                card.body.addWidget(label(sg.get("note", ""), "Small", wrap=True))
        return card
