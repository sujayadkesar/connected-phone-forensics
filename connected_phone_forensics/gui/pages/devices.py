"""Devices: a searchable list, and everything known about the selected device.

The selected phone is drawn as it most likely looks (from its model or the chip in its UDID),
with the wallpaper recovered from Phone Link on its screen when there is one, beside who and
what it is. Its identifiers are listed in full with what each one tells.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QListWidget, QListWidgetItem, QMessageBox, QSplitter, QWidget

from ... import content, phoneart
from .. import jobs, theme
from ..casedata import (
    CLASS_LABEL,
    ID_LABEL,
    STRENGTH_LABEL,
    CaseData,
    channel_label,
    fmt_time,
    os_label,
)
from ..icons import device_badge, device_icon, icon
from ..models import Column, RecordModel, make_table
from ..widgets import (
    THEME,
    Card,
    Chip,
    ChipRow,
    Divider,
    EmptyState,
    FlowLayout,
    IconBadge,
    KeyValueGrid,
    PageScroll,
    SearchBox,
    Segmented,
    Toast,
    breakable,
    button,
    channel_color_fn,
    clear_layout,
    copy_text,
    hbox,
    label,
    overline,
    tool_button,
    vbox,
)
from .data import CategoryTile
from .overview import device_summary

STRENGTH_TONE = {"strong": ("ok", "ph.shield-check"), "link": ("accent", "ph.link"),
                 "weak": ("faint", "ph.circle-dashed")}


def _fit_table(view, rows: int) -> None:
    """Size a small table to its rows so the page, not the table, scrolls."""
    h = view.horizontalHeader().sizeHint().height() + rows * view.verticalHeader().defaultSectionSize() + 4
    view.setFixedHeight(h)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)


class PhoneView(QWidget):
    """The phone, drawn, on a soft glow."""

    def __init__(self, look: phoneart.PhoneLook):
        super().__init__()
        self.look = look
        self.setFixedSize(250, 470)
        THEME.on_change(self.update, self)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QRadialGradient(QPointF(self.width() / 2, self.height() * 0.48), self.width() * 0.62)
        c = QColor(THEME.palette.accent)
        c.setAlpha(60 if THEME.palette.dark else 38)
        glow.setColorAt(0.0, c)
        c.setAlpha(0)
        glow.setColorAt(1.0, c)
        p.fillRect(self.rect(), glow)
        font = THEME.fonts.ui.family() if THEME.fonts else ""
        phoneart.paint_phone(p, QRectF(self.rect()).adjusted(4, 6, -4, -14), self.look, font_family=font)


class IdentifierRow(QWidget):
    """One identifier in full: what it is, its value, what it tells, and how strong it is."""

    def __init__(self, i: dict, on_copy):
        super().__init__()
        dec = i.get("decoded") or {}
        strength = i.get("strength") or "weak"
        tone, icon_name = STRENGTH_TONE.get(strength, ("faint", "ph.circle"))
        title = label(dec.get("label") or ID_LABEL.get(i["kind"], i["kind"]), "H3")
        chip = Chip.status(STRENGTH_LABEL.get(strength, strength), tone, icon_name,
                           {"strong": "Strong: can merge records into one device",
                            "link": "Link: shown and compared, never merges records",
                            "weak": "Weak: names and models; never merges records"}.get(strength, ""))
        value = label(breakable(i.get("display") or i["value"]), "Mono", wrap=True, select=True, mono=True)
        copy = tool_button("ph.copy", "Copy the value", lambda v=i["value"]: on_copy(v), 14)
        lay = vbox(spacing=3, margins=(0, 10, 0, 10))
        lay.addLayout(hbox(title, chip, "stretch", copy, spacing=8))
        lay.addWidget(value)
        if dec.get("summary"):
            lay.addWidget(label(dec["summary"], None, wrap=True, select=True))
        fields = [f"{k}: {v}" for k, v in dec.get("fields") or [] if k != "Value"]
        if fields:
            lay.addWidget(label(" · ".join(fields), "Small", wrap=True, select=True))
        if dec.get("note"):
            lay.addWidget(label(dec["note"], "Small", wrap=True, select=True))
        rec = i.get("as_recorded") or []
        src = ", ".join(i.get("sources") or [])
        lay.addWidget(label(("As recorded: " + ", ".join(rec) + " · " if rec else "") + f"Found by {src}", "Faint",
                            wrap=True, select=True))
        self.setLayout(lay)


class ChannelBadge(QWidget):
    """An icon on a rounded square tinted with a connection type's colour (USB, Bluetooth...)."""

    def __init__(self, icon_name: str, channel: str | None, size: int = 36, tone: str = "muted"):
        super().__init__()
        self.icon_name, self.channel, self.tone = icon_name, channel, tone
        self.setFixedSize(size, size)
        THEME.on_change(self.update, self)

    def _color(self) -> QColor:
        p = THEME.palette
        return QColor(theme.channel_color(p, self.channel) if self.channel else getattr(p, self.tone))

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = self._color()
        bg = QColor(c)
        bg.setAlpha(48)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(self.rect()), self.width() / 4, self.width() / 4)
        m = self.width() * 0.24
        icon(self.icon_name, c.name()).paint(p, self.rect().adjusted(int(m), int(m), -int(m), -int(m)))


class DeviceItem(QWidget):
    def __init__(self, d: dict):
        super().__init__()
        icon_name, channel = device_badge(d)
        badge = ChannelBadge(icon_name, channel, 36)
        badge.setToolTip(f"Mostly seen over {channel_label(channel)}" if channel else
                         CLASS_LABEL.get(d.get("device_class") or "", ""))
        name = label(breakable(d.get("label") or d["id"]), "H3", wrap=True)  # long names wrap, not cut
        name.setMinimumWidth(10)
        state = "" if d.get("confirmed") else "  ·  weak identifiers only"
        sub = label(device_summary(d).replace(" · confirmed", "").replace(" · weak identifiers only", "") + state,
                    "Small")
        chans = " · ".join(channel_label(c["channel"]) for c in (d.get("channels") or [])[:3])
        ch = label(chans, "Faint")
        self.setLayout(hbox(badge, (vbox(name, sub, ch, spacing=1), 1), spacing=11, margins=(10, 8, 12, 8)))
        self.setToolTip(f"{d['id']} · {d.get('observation_count', 0)} records")


class HotspotItem(QWidget):
    """A hotspot network in the list: its name, how many access points, when last joined."""

    def __init__(self, net: dict):
        super().__init__()
        badge = ChannelBadge("ph.wifi-high", "wifi_hotspot", 36)
        name = label(f"“{net['ssid']}”", "H3")
        name.setMinimumWidth(10)
        n = len(net["sessions"])
        likely = net["likely_phone"] or {"ios": "iPhone", "android": "Android phone"}.get(net["os"], "unknown phone")
        sub = label(f"{likely} · {n} access point{'s' if n != 1 else ''}", "Small")
        last = label(f"last joined {fmt_time(net['last'], False)} UTC" if net["last"] else "", "Faint")
        self.setLayout(hbox(badge, (vbox(name, sub, last, spacing=1), 1), spacing=11, margins=(10, 8, 12, 8)))


class HotspotArt(QWidget):
    """A phone broadcasting Wi-Fi: arcs over a small phone, in the hotspot colour."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(220, 220)
        THEME.on_change(self.update, self)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QColor(theme.channel_color(THEME.palette, "wifi_hotspot"))
        glow = QRadialGradient(QPointF(110, 120), 110)
        g = QColor(c)
        g.setAlpha(55 if THEME.palette.dark else 35)
        glow.setColorAt(0, g)
        g.setAlpha(0)
        glow.setColorAt(1, g)
        p.fillRect(self.rect(), glow)
        for i, r in enumerate((86, 64, 42)):
            pen = QPen(QColor(c.red(), c.green(), c.blue(), 110 + i * 50), 7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(110 - r, 112 - r, 2 * r, 2 * r), 45 * 16, 90 * 16)
        phone = QRectF(88, 112, 44, 84)
        look = phoneart.PhoneLook(phoneart.PUNCH_HOLE, "", "", "", minimal=True)
        phoneart.paint_phone(p, phone, look, shadow=False)


class DevicesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        self.case: CaseData | None = None
        self.current: str | None = None

        # list
        left = QFrame()
        left.setObjectName("Card")
        left.setMinimumWidth(360)
        left.setMaximumWidth(440)
        self.search = SearchBox("Search name, identifier, computer")
        self.search.search.connect(lambda *_: self._fill())
        self.scope = Segmented([("phones", "Phones"), ("hotspots", "Hotspots"), ("other", "Other"), ("all", "All")])
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
        self.scope.set_count("hotspots", len(case.hotspots))
        self.scope.set_count("all", len(case.devices))
        self.scope.set_count("other", len(case.others))
        self.scope.select("phones" if case.phones else "hotspots" if case.hotspots else "all")
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
        q = self.search.text().strip()
        keep = self.current
        self.list.blockSignals(True)
        self.list.clear()
        select_row = 0
        if scope == "hotspots":
            shown = [n for n in self.case.hotspots if not q or all(
                t in " ".join([n["ssid"], n["likely_phone"]] + [s["bssid"] for s in n["sessions"]]).lower()
                for t in q.lower().split())]
            for i, net in enumerate(shown):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, f"net:{net['ssid']}")
                w = HotspotItem(net)
                item.setSizeHint(QSize(0, w.sizeHint().height()))
                self.list.addItem(item)
                self.list.setItemWidget(item, w)
                if keep and (keep == f"net:{net['ssid']}" or keep in net["devices"]):
                    select_row = i
            self.list.blockSignals(False)
            self.list_empty.setVisible(not shown)
            self.list.setVisible(bool(shown))
            if shown:
                self.list.setCurrentRow(select_row)
            else:
                self._show(None)
            return
        pool = self.case.phones if scope == "phones" else self.case.others if scope == "other" else self.case.devices
        shown = [d for d in pool if self._matches(d, q)]
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
        if device_id in ("__other__", "__hotspots__"):
            self.scope.select("other" if device_id == "__other__" else "hotspots")
            self._fill()
            return
        d = self.case.device(device_id)
        if d is None:
            return
        self.search.clear()
        if d in self.case.hotspot_devices:
            self.scope.select("hotspots")
        else:
            self.scope.select("phones" if d in self.case.phones else "other")
        self.current = device_id
        self._fill()

    def _picked(self, item: QListWidgetItem | None, _prev=None) -> None:
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(key, str) and key.startswith("net:"):
            net = next((n for n in self.case.hotspots if f"net:{n['ssid']}" == key), None) if self.case else None
            self._show_hotspot(net)
        else:
            self._show(key)

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
        self.detail_lay.addWidget(self._hero(d))
        recovered = self._recovered(d)
        if recovered is not None:
            self.detail_lay.addWidget(recovered)
        self.detail_lay.addWidget(self._identifiers(d))
        self.detail_lay.addWidget(self._channels(d))
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

    # -- a hotspot network -------------------------------------------------------------

    def _show_hotspot(self, net: dict | None) -> None:
        clear_layout(self.detail_lay)
        if net is None:
            self._show(None)
            return
        self.current = f"net:{net['ssid']}"
        n = len(net["sessions"])
        likely = net["likely_phone"]
        os_name = {"ios": "iPhone (iOS)", "android": "Android"}.get(net["os"], "")
        hero = Card(padding=20)
        info = vbox(spacing=10)
        info.addWidget(overline("Hotspot network"))
        info.addWidget(label(f"“{net['ssid']}”", "H1", wrap=True, select=True))
        chips = [Chip.channel("wifi_hotspot", f"{n} access point{'s' if n != 1 else ''}")]
        if likely:
            chips.append(Chip(f"likely {likely}", None, "ph.device-mobile",
                              "Inferred from the network name, which the phone's owner can change"))
        elif os_name:
            chips.append(Chip(os_name, None, "ph.apple-logo" if net["os"] == "ios" else "ph.android-logo"))
        if net["randomised"]:
            chips.append(Chip.status(f"{net['randomised']} randomised", "warn", "ph.shuffle",
                                     "Locally administered addresses: a new one each time the hotspot is switched "
                                     "on; they name no maker and no location service records them"))
        info.addWidget(ChipRow(chips))
        kv = KeyValueGrid(columns=1)
        kv.add("First joined", f"{fmt_time(net['first'])} UTC" if net["first"] else "no time recorded")
        kv.add("Last joined", f"{fmt_time(net['last'])} UTC" if net["last"] else "no time recorded")
        kv.add("Computers", ", ".join(net["computers"]) or "—")
        names = sorted({s["names"] for s in net["sessions"] if s["names"]})
        if names and names != [net["ssid"]]:
            kv.add("Network names", "; ".join(names))
        info.addWidget(kv)
        info.addWidget(label(
            "A phone known only as a hotspot: the computer saved the network's name and the address of the access "
            "point (BSSID). Android 11 and later, and recent iPhones, give the hotspot a new random address each "
            "time it is switched on, so one phone appears as several access points with the same name. The same "
            "name does not prove the same phone.", "Small", wrap=True))
        info.addStretch(1)
        row = hbox(HotspotArt(), (info, 1), spacing=24)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        hero.body.addLayout(row)
        self.detail_lay.addSpacing(22)
        self.detail_lay.addWidget(hero)

        sessions = Card("Access points", "One row per access point address, with when the computer joined it.")
        model = RecordModel([
            Column("Access point (BSSID)", lambda s: s["bssid"] or "—", 170, mono=True),
            Column("Maker", lambda s: s["maker"], 190, muted=True, tooltip=True),
            Column("First joined (UTC)", lambda s: fmt_time(s["first"]), 165, mono=True,
                   sort_key=lambda s: s["first"] or ""),
            Column("Last joined (UTC)", lambda s: fmt_time(s["last"]), 165, mono=True,
                   sort_key=lambda s: s["last"] or ""),
            Column("Windows profile · addresses", lambda s: " · ".join(x for x in (s["profiles"], s["dhcp"]) if x),
                   None, tooltip=True),
        ])
        model.set_rows(net["sessions"])
        view = make_table(model)
        view.setAlternatingRowColors(False)
        _fit_table(view, len(net["sessions"]))
        view.doubleClicked.connect(lambda idx: self.window_.go("timeline", {"device": model.row(idx.row())["device"]}))
        view.setToolTip("Double-click an access point to see its records in the timeline")
        sessions.body.addWidget(view)
        self.detail_lay.addWidget(sessions)
        self.detail_lay.addWidget(self._location_card(net))
        self.detail_lay.addStretch(1)
        self.scroll.verticalScrollBar().setValue(0)

    def _location_card(self, net: dict) -> Card:
        from ... import geo

        card = Card("Where was it?", "An optional online lookup in Apple's Wi-Fi location service.")
        fixed = [s["bssid"] for s in net["sessions"] if s["bssid"] and not geo.is_randomised(s["bssid"])]
        if not fixed:
            card.body.addWidget(label(
                "Every address here is randomised, so no location service records it: nothing can be looked up, "
                "and nothing is sent. (A phone's hotspot moves with the phone in any case.)", "Small", wrap=True))
            return card
        card.body.addWidget(label(
            f"{len(fixed)} address(es) are hardware addresses. Apple's location service may hold a position where "
            "devices saw them. Asking sends each address to Apple over the internet; the lookup is recorded in "
            "online-lookups.jsonl in the case folder. A position is a lead, not a measurement.", "Small", wrap=True))
        results = vbox(spacing=6)
        btn = button("Look up location…", None, "mdi6.map-marker-radius",
                     lambda: self._lookup(fixed, results, btn))
        card.body.addLayout(hbox(btn, "stretch"))
        card.body.addLayout(results)
        return card

    def _lookup(self, bssids: list[str], results, btn) -> None:
        from ... import geo

        answer = QMessageBox.question(
            self, "Send to Apple's location service?",
            f"This sends {len(bssids)} access point address(es) to Apple (gs-loc.apple.com) to ask where they "
            "were seen:\n\n" + "\n".join(bssids) + "\n\nApple learns which addresses were asked about and when. "
            "Make sure your authority covers an online lookup. Each lookup is recorded in the case folder.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        btn.setEnabled(False)
        btn.setText("Looking up…")
        case_dir = self.case.outdir if self.case else None
        examiner = (self.case.case.get("examiner") if self.case else "") or ""

        def work(_job):
            out = []
            for b in bssids:
                try:
                    loc = geo.locate(b)
                except OSError as exc:
                    loc = geo.Location(geo.normalise(b), False, note=f"lookup failed: {exc}")
                geo.record_lookup(case_dir, loc, examiner=examiner, sent=not geo.is_randomised(b))
                out.append(loc)
            return out

        def done(locs) -> None:
            btn.setText("Look up again")
            btn.setEnabled(True)
            clear_layout(results)
            for loc in locs:
                if loc.found:
                    acc = f" (±{loc.accuracy_m} m)" if loc.accuracy_m else ""
                    row = hbox(label(loc.bssid, "Mono", mono=True),
                               label(f"{loc.latitude:.6f}, {loc.longitude:.6f}{acc}", None, select=True),
                               "stretch", button("Open map", "Link", "ph.map-pin",
                                                 lambda u=loc.map_url: self.window_.open_url(u)), spacing=12)
                else:
                    row = hbox(label(loc.bssid, "Mono", mono=True), label(loc.note, "Small"), "stretch", spacing=12)
                results.addLayout(row)

        jobs.run(work, done=done, failed=lambda msg, _d: (btn.setEnabled(True), btn.setText("Look up location…"),
                                                           Toast.show_for(self, f"Lookup failed: {msg}", 4000)))

    def _items(self) -> dict[str, list[dict]]:
        return self.window_.pages["data"].items

    def _wallpaper(self, d: dict) -> Path | None:
        from ...evidence import long_path

        if not self.case or not self.case.outdir:
            return None
        for r in self._items().get("media", []):
            if r["device"] == d["id"] and r.get("kind") == "Wallpaper" and r.get("file"):
                p = Path(self.case.outdir) / "exports" / r["file"]
                if long_path(p).is_file():
                    return p
        return None

    def _hero(self, d: dict) -> Card:
        """The drawn phone beside who and what it is (for phones); a compact header otherwise."""
        is_phone = d.get("device_class") in (None, "phone", "tablet")
        facts = content.phone_facts(d)
        card = Card(padding=20)
        info = vbox(spacing=10)
        info.addWidget(overline(f"{CLASS_LABEL.get(d.get('device_class') or 'phone', 'Device')} · {d['id']}"))
        info.addLayout(self._header(d))
        kv = KeyValueGrid(columns=1)
        kv.add("Operating system", facts["os"])
        if facts["model"]:
            kv.add("Model", facts["model"])
        if facts["names"]:
            kv.add("Name", ", ".join(facts["names"]))
        if facts["numbers"]:
            kv.add("Phone number", ", ".join(facts["numbers"]))
        if facts["primary_label"]:
            kv.add(facts["primary_label"], breakable(facts["primary_value"]), mono=True)
            if facts["primary_summary"]:
                kv.add("What it tells", facts["primary_summary"])
        kv.add("First seen", f"{fmt_time(d.get('first_seen'))} UTC" if d.get("first_seen") else "no time recorded")
        kv.add("Last seen", f"{fmt_time(d.get('last_seen'))} UTC" if d.get("last_seen") else "no time recorded")
        kv.add("Computers", ", ".join(d.get("systems") or []) or "—")
        kv.add("Local users", ", ".join(d.get("users") or []) or "—")
        kv.add("Records", f"{d.get('observation_count', 0):,} from {len(d.get('identifiers') or [])} identifiers")
        info.addWidget(kv)
        info.addStretch(1)
        if is_phone:
            look = phoneart.look_for(d, self._wallpaper(d), caption=facts["model"] or facts["os"])
            row = hbox(PhoneView(look), (info, 1), spacing=24)
            row.setAlignment(Qt.AlignmentFlag.AlignTop)
            card.body.addLayout(row)
        else:
            card.body.addLayout(info)
        return card

    def _recovered(self, d: dict) -> Card | None:
        mine = {k: [r for r in v if r["device"] == d["id"]] for k, v in self._items().items()}
        if not any(mine[k] for k in mine if k != "connections"):
            return None
        card = Card("Recovered from this phone", "Click a kind to see every item, with its source, on the "
                                                  "Recovered data page.")
        tiles = QWidget()
        flow = FlowLayout(tiles, 10)  # wraps, so the page never grows wider than the window
        for cat in content.CATEGORIES:
            rows = mine.get(cat.key) or []
            if not rows:
                continue
            tile = CategoryTile(cat.key, cat.title, cat.icon)
            tile.setFixedWidth(168)
            tile.set(len(rows), sum(1 for r in rows if r.get("status")), len(rows))
            tile.setToolTip(f"Show this phone's {cat.title.lower()}")
            tile.clicked.connect(lambda key, dev=d["id"]: self.window_.go("data", {"device": dev, "category": key}))
            flow.addWidget(tile)
        card.body.addWidget(tiles)
        return card

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
        actions = hbox(
            button("Timeline", None, "ph.clock-counter-clockwise",
                   lambda: self.window_.go("timeline", {"device": d["id"]}), "Show this device's records"),
            button("Recovered data", None, "ph.database",
                   lambda: self.window_.go("data", {"device": d["id"]}), "Show what was recovered from it"),
            tool_button("ph.copy", "Copy the device record ID", lambda: copy_text(d["id"], self, f"Copied {d['id']}"),
                        16),
            "stretch", spacing=8)
        is_phone = d.get("device_class") in (None, "phone", "tablet")
        head = vbox(title, ChipRow(chips), spacing=8)
        if is_phone:
            col = vbox(head, actions, spacing=12)
            return col
        row = hbox(badge, (head, 1), actions, spacing=16)
        row.setAlignment(badge, Qt.AlignmentFlag.AlignTop)
        row.setAlignment(actions, Qt.AlignmentFlag.AlignTop)
        return row

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
        card = Card("Identifiers", "Every identifier recorded for this device, in full, with what its format "
                                   "tells. Only strong identifiers merge records into one device.")
        order = content.ID_ORDER
        rows = sorted(d.get("identifiers") or [],
                      key=lambda i: order.index(i["kind"]) if i["kind"] in order else len(order))
        for n, i in enumerate(rows):
            if n:
                card.body.addWidget(Divider())
            card.body.addWidget(IdentifierRow(i, lambda v: copy_text(v, self, "Copied the value")))
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
