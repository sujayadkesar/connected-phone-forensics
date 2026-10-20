"""Guide: how the tool works, where phones leave traces, what is recovered and when it is not,
how to read the results, and the limits of all of it."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QWidget,
)

from ... import __version__
from ...plugin import load_plugins
from ..casedata import CHANNEL_LABEL, OS_LABEL, VALIDATION_LABEL
from ..hero import Banner
from ..icons import CHANNEL_ICON, OS_ICON, icon
from ..theme import channel_color, mix
from ..widgets import (
    THEME,
    Card,
    Chip,
    ChipRow,
    Divider,
    FlowLayout,
    IconBadge,
    PageScroll,
    button,
    channel_color_fn,
    hbox,
    label,
    overline,
    tone_fn,
    vbox,
)

PROJECT_URL = "https://github.com/sujayadkesar/connected-phone-forensics"

STEPS = (
    ("ph.hard-drives", "1  Evidence",
     "A mounted image, an extracted drive, a KAPE, Velociraptor or UAC collection, or this computer (live, as "
     "administrator). Nothing in the evidence is changed."),
    ("ph.puzzle-piece", "2  Parsers",
     "Each parser reads one place where Windows, macOS or Linux records phones: the registry, logs, app "
     "databases, pairing records and backups. Deleted rows are rebuilt from SQLite journals and free pages."),
    ("ph.tree-structure", "3  Devices",
     "Records that share a strong identifier (serial number, IMEI, UDID, Bluetooth or Wi-Fi hardware address) "
     "become one device. Names and numbers only suggest a match; they never merge on their own."),
    ("ph.files", "4  Results",
     "The dashboard, devices, recovered data and timeline here; an HTML report, Excel workbook, PDF summary, CSV "
     "and JSON in the case folder. Every row keeps its file, record and SHA-256."),
)

# (channel, title, what the computer keeps, what it tells you, watch out for)
TRACES = (
    ("usb", "USB",
     "The phone's USB serial (an iPhone's UDID, an Android phone's serial number), maker and model, and when "
     "Windows first set it up, last saw it arrive and last saw it leave.",
     "Which handset was plugged in, and when.",
     "Last-arrival and last-removal times are overwritten at each connection. A charge-only cable or mode can "
     "leave little or nothing."),
    ("bluetooth", "Bluetooth",
     "The phone's Bluetooth address, its name and class (phone, headset), when it was last seen and connected, "
     "and a link key when it was paired.",
     "That the phone was paired with this computer, and its real hardware address, which other records share.",
     "The name is whatever the owner chose. Phones that only advertised nearby are not paired."),
    ("wifi_hotspot", "Wi-Fi hotspot",
     "The network's name (often the phone's name, “Jane's iPhone”), when the computer first and last joined it, "
     "and the hotspot's address (BSSID) and DHCP lease.",
     "That the computer used the phone's internet connection. An iPhone hotspot hands out 172.20.10.x addresses.",
     "Android 11 and later and recent iPhones give the hotspot a new random address each time, so one phone can "
     "appear as many access points. Any phone can use any name."),
    ("phone_link", "Phone Link",
     "Per linked phone: messages, calls, contacts with their pictures, the most recent photos, notifications, "
     "installed apps with icons, the wallpaper, the phone's model and numbers.",
     "The phone's own data, as the app last synced it, and when the phone was linked.",
     "Only what the app cached. Unlinking the phone or resetting the app deletes its cache; an iPhone linked to "
     "Phone Link leaves far less than an Android phone."),
    ("pairing", "iPhone pairing and backups",
     "A pair record when an iPhone was trusted over USB (UDID, Wi-Fi address, the date of trust); a backup's "
     "Info.plist (name, IMEI, serial, phone number, installed apps, last backup), readable even when the "
     "backup is encrypted.",
     "That a specific iPhone trusted this computer, and its identity.",
     "The backup's content (messages, photos) is not parsed here: process the backup with a mobile tool such as "
     "iLEAPP. An encrypted backup needs its password."),
    ("continuity", "Mac with an iPhone",
     "Messages relayed from the iPhone (chat.db), call history, AirDrop receipts with the sender's name, iPhone "
     "Mirroring, and the devices that sync with the Mac through the Apple Account.",
     "The iPhone's messages and calls, and which devices share the owner's account.",
     "Messages and calls sync through iCloud: a row on the Mac does not prove the Mac handled it."),
    ("account_sync", "Microsoft account",
     "Phones signed into the same Microsoft account (TaskFlow device cache): name, maker, model and type.",
     "Phones that belong to the same person, even if never plugged in.",
     "Being on the same account does not prove a phone was ever near the computer."),
    ("companion_app", "KDE Connect",
     "Paired devices with their name, type and the certificate the phone's app generated.",
     "A paired phone, and from its certificate, roughly when the pairing was made.",
     "Certificates carry the app's own validity period, not the pairing time itself."),
)

# what is recovered, by source: 2 kept, 1 partly, 0 not kept here
SOURCES = ("Android + Phone Link", "iPhone + Phone Link", "iPhone backup", "Mac with an iPhone",
           "USB, Bluetooth, hotspot")
MATRIX = (
    ("Messages", (2, "SMS, MMS and RCS the app synced (phone.db)"),
     (1, "Shown over Bluetooth while connected; little is kept"),
     (0, "In the backup's content, which this tool does not parse (use iLEAPP)"),
     (2, "iMessages and SMS relayed from the iPhone (chat.db)"), (0, "Not recorded by these connections")),
    ("Calls", (2, "Call history (calling.db)"), (1, "Calls placed through the computer, when recorded"),
     (0, "In the backup's content"), (2, "Calls synced from the iPhone (CallHistory)"), (0, "Not recorded")),
    ("Contacts", (2, "The address book, with pictures (contacts.db)"), (1, "Synced over Bluetooth; may be kept"),
     (0, "In the backup's content"), (1, "Names in messages and calls"), (0, "Not recorded")),
    ("Photos and files", (1, "The most recent photos are listed; only thumbnails the app cached are kept, not "
                             "the full photos"), (0, "Not shared"), (0, "In the backup's content"),
     (1, "AirDrop receipts: the record of the file, not the file"), (0, "Not recorded")),
    ("Notifications", (1, "Recent ones (notifications.db, Windows toasts that expire within days)"),
     (1, "Windows toasts, recent only"), (0, "Not kept"), (1, "Notification Center, macOS 15 with iPhone Mirroring"),
     (0, "Not recorded")),
    ("Apps", (2, "Installed apps with icons, and recently used ones"), (0, "Not shared"),
     (2, "Installed apps (Info.plist)"), (1, "iPhone Mirroring app placeholders"), (0, "Not recorded")),
    ("Phone identity", (2, "Model, name, numbers, Bluetooth address"), (1, "Name and Bluetooth address"),
     (2, "UDID, IMEI, serial, phone number"), (1, "Model and name of devices on the account"),
     (1, "USB serial, Bluetooth or Wi-Fi address, name")),
    ("Connection times", (2, "When linked, last synced"), (2, "When linked"), (2, "Last backup, date of trust"),
     (2, "Last sync, messages' times"), (2, "First and last connected")),
)

MISSING = (
    ("ph.link-break", "The phone was unlinked or the app reset", "Phone Link deletes a phone's cache when it is "
     "unlinked; an app reset clears everything."),
    ("ph.files", "The collection did not include the files", "A triage collection holds only what its targets "
     "copied. The Coverage page lists every file each parser looked for and found."),
    ("ph.warning-circle", "An app update changed its database", "Tables the parser does not know are reported as "
     "unrecognised on the Coverage page and in Issues, never silently skipped."),
    ("ph.trash", "The data was deleted and overwritten", "Deleted rows survive only until SQLite reuses their "
     "space (or the database is vacuumed)."),
    ("ph.lock-simple", "It is encrypted", "Backup content, keychains and some app data are encrypted; their "
     "metadata may still be readable."),
    ("ph.clock-counter-clockwise", "It expired", "Windows notification toasts expire within days; caches keep "
     "only the most recent items."),
)

LIMITS = (
    ("Only the computer's side", "Everything here comes from the computers' records of phones, not from the phones "
     "themselves. A missing record is not proof that something did not happen."),
    ("Names can be anything", "Device and network names are chosen by the owner and can be changed or copied. "
     "They suggest a match; they never prove one."),
    ("Times mean different things", "Each time says what it records (“first installed”, “last connected”, "
     "“key last written”). Registry times are when a key was last written, which may not be the connection "
     "itself. Times marked “file time” come from the filesystem and depend on how the evidence was copied. All "
     "times are UTC."),
    ("Random addresses", "Wi-Fi hotspots and many Bluetooth Low Energy devices use random addresses, which cannot "
     "be tied to a handset or looked up."),
    ("Recovered rows are best effort", "Deleted rows are rebuilt from journals and free pages: some are older "
     "versions of a live row, and not every deleted row can be recovered."),
    ("Parsers tested on generated data", "Parsers marked “synthetic” have been tested only against generated "
     "data; check important findings against the source record, which every row points to."),
    ("Locations come from Apple", "A Wi-Fi location is where Apple's devices have seen that access point. It is "
     "approximate, can change, and needs the address to be sent to Apple; it is never sent without asking."),
    ("Live acquisition", "Acquiring the running computer needs administrator rights and writes to the output "
     "folder: put it on another drive, never the one being examined."),
)

SHORTCUTS = (("Ctrl+N", "New scan"), ("Ctrl+O", "Open a case folder"), ("Ctrl+F", "Search the current page"),
             ("Ctrl+1 … Ctrl+8", "Go to a page in the sidebar"), ("F1", "This guide"),
             ("Right-click a cell", "Show only or hide its value, copy it, save its file"),
             ("Click a column's funnel", "Filter and sort the column, as in Excel"))


class LevelDot(QWidget):
    """●, ◐ or ○: kept, partly kept, not kept."""

    def __init__(self, level: int, tip: str):
        super().__init__()
        self.level = level
        self.setFixedSize(18, 18)
        self.setToolTip(tip)
        THEME.on_change(self.update, self)

    def paintEvent(self, _e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = THEME.palette
        r = QRectF(2, 2, 14, 14)
        color = QColor({2: pal.ok, 1: pal.warn, 0: pal.faint}[self.level])
        p.setPen(QPen(color, 1.6))
        p.setBrush(color if self.level == 2 else Qt.BrushStyle.NoBrush)
        p.drawEllipse(r)
        if self.level == 1:
            half = QPainterPath()
            half.moveTo(r.center())
            half.arcTo(r, 90, 180)
            half.closeSubpath()
            p.fillPath(half, color)


class LinkDiagram(QWidget):
    """Records, the identifiers they carry, and the device they make: strong identifiers merge, weak ones only
    suggest."""

    RECORDS = (("USB device record", "USB serial R58N3…"), ("Phone Link profile", "Bluetooth 5C:F3:70:…"),
               ("Bluetooth pairing", "Bluetooth 5C:F3:70:…"), ("Hotspot “Galaxy S22”", "name only"))
    IDS = (("USB serial", True), ("Bluetooth address", True), ("Device name", False))
    EDGES = ((0, 0), (1, 1), (1, 2), (2, 1), (3, 2))

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        THEME.on_change(self.update, self)

    def paintEvent(self, _e) -> None:  # noqa: N802
        pal = THEME.palette
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = float(self.width()), float(self.height())
        f = QFont(self.font())
        small = QFont(f)
        small.setPointSizeF(f.pointSizeF() - 1)
        bold = QFont(f)
        bold.setWeight(QFont.Weight.DemiBold)

        from PySide6.QtGui import QFontMetrics

        col_w = min(250.0, w * 0.27)
        rec = [QRectF(0, 12 + i * (h - 24) / 4, col_w, (h - 24) / 4 - 10) for i in range(4)]
        fm = QFontMetrics(bold)
        id_w = max(fm.horizontalAdvance(f"{n}  ·  {'strong' if s else 'weak'}") for n, s in self.IDS) + 36
        mid_x = w * 0.5 - id_w / 2
        ids = [QRectF(mid_x, 30 + i * (h - 60) / 3, id_w, 32) for i in range(3)]
        dev = QRectF(w - col_w, h * 0.5 - 52, col_w, 74)
        weak_dev = QRectF(w - col_w, h - 58, col_w, 54)

        def box(r: QRectF, title: str, sub: str, tone: str | None = None) -> None:
            fill = QColor(pal.raised if tone is None else mix(pal.surface, getattr(pal, tone), 0.14))
            p.setPen(QPen(QColor(pal.border if tone is None else mix(pal.border, getattr(pal, tone), 0.6)), 1))
            p.setBrush(fill)
            p.drawRoundedRect(r, 9, 9)
            p.setPen(QColor(pal.text))
            p.setFont(bold)
            p.drawText(r.adjusted(12, 4, -8, -r.height() / 2), Qt.AlignmentFlag.AlignVCenter, title)
            p.setPen(QColor(pal.muted))
            p.setFont(small)
            p.drawText(r.adjusted(12, r.height() / 2 - 2, -8, -4),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap), sub)

        for (a, b) in self.EDGES:
            strong = self.IDS[b][1]
            start = QPointF(rec[a].right(), rec[a].center().y())
            end = QPointF(ids[b].left(), ids[b].center().y())
            self._curve(p, start, end, pal.accent if strong else pal.warn, strong)
        for b, (_name, strong) in enumerate(self.IDS):
            start = QPointF(ids[b].right(), ids[b].center().y())
            target = dev if strong else weak_dev
            self._curve(p, start, QPointF(target.left(), target.center().y()), pal.accent if strong else pal.warn,
                        strong)
        for r, (title, sub) in zip(rec, self.RECORDS, strict=True):
            box(r, title, sub)
        for r, (name, strong) in zip(ids, self.IDS, strict=True):
            p.setPen(QPen(QColor(pal.accent if strong else pal.warn), 1.2))
            p.setBrush(QColor(mix(pal.surface, pal.accent if strong else pal.warn, 0.16)))
            p.drawRoundedRect(r, 16, 16)
            p.setPen(QColor(pal.text))
            p.setFont(bold)
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, f"{name}  ·  {'strong' if strong else 'weak'}")
        box(dev, "Galaxy S22  ·  DEV-001", "one device, confirmed by two strong identifiers", "ok")
        box(weak_dev, "May be the same phone", "shown for review, never merged", "warn")

    @staticmethod
    def _curve(p: QPainter, a: QPointF, b: QPointF, color: str, solid: bool) -> None:
        path = QPainterPath(a)
        dx = (b.x() - a.x()) * 0.5
        path.cubicTo(QPointF(a.x() + dx, a.y()), QPointF(b.x() - dx, b.y()), b)
        pen = QPen(QColor(color), 1.6)
        if not solid:
            pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)


def notices_file() -> Path | None:
    """The third-party notices shipped with this copy: next to the portable programs, inside a
    single-file program, in a source checkout or in the installed package's metadata."""
    candidates = []
    if getattr(sys, "frozen", False):
        candidates += [Path(sys.executable).parent / "THIRD-PARTY-NOTICES.md",
                       Path(getattr(sys, "_MEIPASS", ".")) / "notices" / "THIRD-PARTY-NOTICES.md"]
    else:
        candidates.append(Path(__file__).resolve().parents[3] / "THIRD-PARTY-NOTICES.md")
        try:
            from importlib import metadata

            files = metadata.distribution("connected-phone-forensics").files or []
            candidates += [Path(str(f.locate())) for f in files if str(f).endswith("THIRD-PARTY-NOTICES.md")]
        except Exception:  # noqa: BLE001 - not installed: the checkout's copy, or the project page
            pass
    return next((c for c in candidates if c.is_file()), None)


def show_notices(parent: QWidget, window) -> None:
    """The third-party notices in a window, with the folder of license texts beside them."""
    path = notices_file()
    if path is None:
        window.open_url(f"{PROJECT_URL}/blob/main/THIRD-PARTY-NOTICES.md")
        return
    dlg = QDialog(parent)
    dlg.setWindowTitle("Third-party notices")
    dlg.resize(980, 720)
    view = QTextBrowser()
    view.setOpenExternalLinks(True)
    view.setMarkdown(path.read_text(encoding="utf-8"))
    licenses = path.parent / "licenses"
    buttons = hbox("stretch", spacing=8)
    if licenses.is_dir():
        buttons.insertWidget(0, button("Open the license texts", "Ghost", "ph.folder-open",
                                       lambda: window.open_path(licenses)))
    buttons.addWidget(button("Close", None, None, dlg.accept))
    dlg.setLayout(vbox(view, buttons, spacing=10, margins=(14, 14, 14, 14)))
    dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dlg.open()


def _arrow() -> QWidget:
    w = IconBadge("ph.arrow-right", "muted", 26)
    w.setToolTip("")
    return w


def _item(icon_name: str, tone: str, title: str, body: str, size: int = 30) -> QWidget:
    """An icon with a title and a paragraph, top-aligned (for the guide's two-column grids)."""
    w = QWidget()
    badge = IconBadge(icon_name, tone, size)
    lay = hbox(badge, (vbox(label(title, "H3"), label(body, "Small", wrap=True), "stretch", spacing=2), 1),
               spacing=10)
    lay.setAlignment(badge, Qt.AlignmentFlag.AlignTop)
    w.setLayout(lay)
    return w


class GuidePage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window_ = window
        content = QWidget()
        lay = vbox(spacing=20)
        content.setLayout(lay)
        self.sections: dict[str, QWidget] = {}

        # banner with a table of contents
        banner = Banner()
        toc = QWidget()
        flow = FlowLayout(spacing=6)
        toc.setLayout(flow)
        self._toc = flow
        text = vbox(overline("Guide"), label("How Connected Phone Forensics works", "HeroTitle", wrap=True),
                    label("What it reads, where phones leave traces, what can be recovered and when it cannot, how "
                          "to read the results, and the limits of all of it.", "Muted", wrap=True), 6, toc,
                    spacing=8)
        banner.setLayout(hbox((text, 1), spacing=16, margins=(30, 24, 30, 22)))
        lay.addWidget(banner)

        # how it works
        steps = Card("How it works", "From evidence to a report, in four steps.")
        row = hbox(spacing=10)
        for i, (ic, title, body) in enumerate(STEPS):
            if i:
                row.addWidget(_arrow(), 0, Qt.AlignmentFlag.AlignVCenter)
            card = QFrame()
            card.setObjectName("Tile")
            card.setLayout(vbox(hbox(IconBadge(ic, "accent", 34), "stretch"), label(title, "H3"),
                                label(body, "Small", wrap=True), "stretch", spacing=6, margins=(14, 14, 14, 14)))
            row.addWidget(card, 1)
        steps.body.addLayout(row)
        steps.body.addSpacing(6)
        steps.body.addWidget(overline("How records become devices"))
        steps.body.addWidget(LinkDiagram())
        steps.body.addWidget(label("Strong identifiers (serial number, IMEI, UDID, Bluetooth or Wi-Fi hardware "
                                   "address, app device IDs) merge records into one device. Phone numbers and "
                                   "accounts link records; names and models are weak: they only put devices side "
                                   "by side under “Records that may be the same phone”.", "Small", wrap=True))
        self._section("how", "How it works", steps, lay)

        # where phones leave traces
        traces = Card("Where phones leave traces", "What each kind of connection leaves on a computer, what it "
                                                  "tells you, and what to watch out for.")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for i, (chan, title, keeps, tells, watch) in enumerate(TRACES):
            grid.addWidget(self._trace_card(chan, title, keeps, tells, watch), i // 2, i % 2)
        traces.body.addLayout(grid)
        self._section("traces", "Where phones leave traces", traces, lay)

        # what is recovered and when it is not
        matrix = Card("What is recovered, and when it is not", "What the computers usually keep of a phone's own "
                                                               "data, by how the phone was connected. Hover a dot "
                                                               "for details.")
        mgrid = QGridLayout()
        mgrid.setHorizontalSpacing(10)
        mgrid.setVerticalSpacing(10)
        for j, src in enumerate(SOURCES, 1):
            head = label(src, "Small", wrap=True)
            head.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mgrid.addWidget(head, 0, j)
        for i, (kind, *cells) in enumerate(MATRIX, 1):
            mgrid.addWidget(label(kind, "H3"), i, 0)
            for j, (level, tip) in enumerate(cells, 1):
                dot = LevelDot(level, tip)
                mgrid.addWidget(dot, i, j, Qt.AlignmentFlag.AlignCenter)
        mgrid.setColumnStretch(0, 2)
        for j in range(1, len(SOURCES) + 1):
            mgrid.setColumnStretch(j, 2)
        matrix.body.addLayout(mgrid)
        legend = hbox(LevelDot(2, ""), label("usually kept", "Small"), 12, LevelDot(1, ""),
                      label("partly, or only sometimes", "Small"), 12, LevelDot(0, ""),
                      label("not kept here", "Small"), "stretch", spacing=6)
        matrix.body.addLayout(legend)
        matrix.body.addWidget(Divider())
        matrix.body.addWidget(overline("Why something can be missing"))
        mg = QGridLayout()
        mg.setHorizontalSpacing(14)
        mg.setVerticalSpacing(10)
        for i, (ic, title, body) in enumerate(MISSING):
            mg.addWidget(_item(ic, "warn", title, body), i // 2, i % 2, Qt.AlignmentFlag.AlignTop)
        matrix.body.addLayout(mg)
        self._section("recovered", "What is recovered", matrix, lay)

        # reading the results
        reading = Card("Reading the results", "What the marks and labels mean.")
        rgrid = QGridLayout()
        rgrid.setHorizontalSpacing(16)
        rgrid.setVerticalSpacing(12)
        items = (
            (Chip.status("Confirmed", "ok", "ph.check-circle"), "The device carries at least one strong identifier."),
            (Chip.status("Weak identifiers only", "warn", "ph.warning"), "Known only by names or numbers: it may be "
             "the same phone as another record. Review it on the Devices page."),
            (Chip.status("Deleted, recovered", "warn", "ph.warning"), "Rebuilt from a database's journal (WAL) or "
             "free pages; amber rows in the tables. “Older version” rows are earlier copies of a live row."),
            (Chip.status("File time", "warn", "ph.warning"), "A time taken from the filesystem, not from the record: "
             "it depends on how the evidence was copied, and never sets a device's first or last seen."),
            (Chip("Source", tone_fn("muted"), "ph.fingerprint"), "Every row keeps the file it came from, the exact "
             "record inside it (table and row, registry key) and the file's SHA-256."),
            (Chip("Coverage", tone_fn("muted"), "ph.files"), "Every file each parser looked at: parsed, empty, "
             "unrecognised or failed. Unrecognised and failed files are listed under Issues too."),
        )
        for i, (chip, text) in enumerate(items):
            rgrid.addWidget(chip, i, 0, Qt.AlignmentFlag.AlignTop)
            rgrid.addWidget(label(text, "Small", wrap=True), i, 1)
        rgrid.setColumnStretch(1, 1)
        reading.body.addLayout(rgrid)
        self._section("reading", "Reading the results", reading, lay)

        # hotspots and locations
        hot = Card("Hotspots and Wi-Fi locations", "Phones known only by the hotspot the computer joined.")
        hot.body.addWidget(label(
            "Windows and macOS keep each Wi-Fi network joined, with its name, when it was first and last joined, and "
            "the access point's address (BSSID). A phone's hotspot is usually named after the phone, so the app "
            "groups the access points by network name: one row per network, one line per access point, with the "
            "times. The same name on two access points does not prove it was the same phone.", "Small", wrap=True))
        sample = hbox(spacing=10)
        for addr, note, tone in (("3C:22:FB:9A:10:4E", "maker's address: the maker is known (here Apple)", "ok"),
                                 ("AA:E3:81:0C:FC:A2", "random address: the second digit is 2, 6, A or E", "warn")):
            box = QFrame()
            box.setObjectName("Chip")
            mark = IconBadge("ph.check-circle" if tone == "ok" else "ph.shuffle", tone, 28)
            box.setLayout(hbox(mark, (vbox(label(addr, "Mono", mono=True), label(note, "Small", wrap=True),
                                           spacing=2), 1), spacing=10, margins=(12, 8, 12, 8)))
            sample.addWidget(box, 1)
        hot.body.addLayout(sample)
        hot.body.addWidget(label(
            "Locating an access point: on a hotspot's page, “Look up locations” asks Apple's Wi-Fi location service "
            "(the one iPhones use) where it has seen each address. It runs only after you confirm, sends nothing "
            "but the addresses, never sends random addresses (no service records them), and logs every query and "
            "answer in the case folder (online-lookups.jsonl). From the command line: cpf locate <BSSID>. A phone's "
            "hotspot moves with the phone, so its answer is where it was seen, not where the computer was; fixed "
            "routers give the most useful answers.", "Small", wrap=True))
        self._section("hotspots", "Hotspots and locations", hot, lay)

        # saving and reports
        save = Card("Saving files and reports", "Taking the results out of the app, without changing them.")
        sgrid = QGridLayout()
        sgrid.setHorizontalSpacing(14)
        sgrid.setVerticalSpacing(12)
        saves = (
            ("ph.floppy-disk", "Save files (Recovered data)", "Photos, attachments, contact pictures and app icons: "
             "the selected items, everything shown, a whole kind, or every file. Each copy is byte for byte, hashed "
             "while written and read back, compared with the hash taken at the scan, and marked read-only. The new "
             "folder holds manifest.csv and manifest.json (phone, record time, deleted or present, source and "
             "hashes), SHA256SUMS.txt and a Read me."),
            ("ph.export", "Export the rows shown", "The table as an Excel workbook or CSV, with the filters you "
             "set (including column filters) in its title."),
            ("ph.file-pdf", "PDF summary", "Phones with their identifiers and drawn handsets, hotspot networks, "
             "and counts of what was recovered."),
            ("ph.table", "Excel workbook", "A summary sheet, one sheet per phone with everything recovered from "
             "it, and a sheet of hotspot networks."),
            ("ph.arrow-square-out", "HTML report", "Every device, identifier and record with its source; opens in "
             "any browser."),
            ("ph.folder-open", "The case folder", "timeline.csv and .jsonl, devices.json, coverage.csv, the files "
             "the scan saved (exports) and manifest.json with the hash of every evidence file read."),
        )
        for i, (ic, title, body) in enumerate(saves):
            sgrid.addWidget(_item(ic, "accent", title, body), i // 2, i % 2, Qt.AlignmentFlag.AlignTop)
        save.body.addLayout(sgrid)
        self._section("saving", "Saving and reports", save, lay)

        # limitations
        limits = Card("Limitations", "What this tool cannot tell you, and where to be careful.", tone="warn")
        lgrid = QGridLayout()
        lgrid.setHorizontalSpacing(16)
        lgrid.setVerticalSpacing(12)
        for i, (title, body) in enumerate(LIMITS):
            lgrid.addWidget(_item("ph.warning", "warn", title, body, 28), i // 2, i % 2, Qt.AlignmentFlag.AlignTop)
        limits.body.addLayout(lgrid)
        self._section("limits", "Limitations", limits, lay)

        # parsers
        parsers = Card("Parsers", "Every parser, what it reads and how it has been tested. “Synthetic”: tested only "
                                  "against generated data; “public sample”: also against public test images.")
        plugins = sorted(load_plugins().values(), key=lambda p: (("windows", "macos", "linux").index(p.os)
                                                                 if p.os in ("windows", "macos", "linux") else 9,
                                                                 p.name.lower()))
        for i, plugin in enumerate(plugins):
            if i:
                parsers.body.addWidget(Divider())
            parsers.body.addWidget(self._parser_row(plugin))
        self._section("parsers", "Parsers", parsers, lay)

        # shortcuts and about
        keys = Card("Keyboard and mouse")
        kgrid = QGridLayout()
        kgrid.setHorizontalSpacing(16)
        kgrid.setVerticalSpacing(8)
        for i, (k, what) in enumerate(SHORTCUTS):
            keyboard = k.startswith(("Ctrl", "F1"))
            kgrid.addWidget(label(k, "Mono" if keyboard else "H3", mono=keyboard), i, 0)
            kgrid.addWidget(label(what, "Small", wrap=True), i, 1)
        kgrid.setColumnStretch(1, 1)
        keys.body.addLayout(kgrid)
        about = Card("About")
        about.body.addWidget(label(f"Connected Phone Forensics {__version__}. Finds the phones connected to "
                                   "Windows, macOS and Linux computers, and what the computers kept of them.",
                                   "Small", wrap=True))
        about.body.addLayout(hbox(button("Project page", "Ghost", "ph.arrow-square-out",
                                         lambda: window.open_url(PROJECT_URL)),
                                  button("Third-party notices", "Ghost", "ph.file-text",
                                         lambda: show_notices(self, window)),
                                  "stretch", spacing=8))
        about.body.addStretch(1)
        bottom = hbox(spacing=16)
        bottom.addWidget(keys, 3)
        bottom.addWidget(about, 2)
        holder = QWidget()
        holder.setLayout(bottom)
        self._section("keys", "Shortcuts and about", holder, lay)
        lay.addStretch(1)

        self.scroll = PageScroll(content)
        page = vbox()
        page.addWidget(self.scroll)
        self.setLayout(page)

    # -- building blocks ---------------------------------------------------------------------

    def _section(self, key: str, title: str, widget: QWidget, lay) -> None:
        self.sections[key] = widget
        lay.addWidget(widget)
        b = QPushButton(title)
        b.setObjectName("TocChip")
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda *_, k=key: self.jump(k))
        self._toc.addWidget(b)

    def jump(self, key: str) -> None:
        w = self.sections.get(key)
        if w is None:
            return
        y = w.mapTo(self.scroll.widget(), QPoint(0, 0)).y()
        self.scroll.verticalScrollBar().setValue(max(0, y - 12))

    def focus_item(self, arg) -> None:
        if isinstance(arg, str):
            self.jump(arg)

    @staticmethod
    def _trace_card(chan: str, title: str, keeps: str, tells: str, watch: str) -> QWidget:
        card = QFrame()
        card.setObjectName("Tile")
        badge = QWidget()
        badge.setFixedSize(34, 34)

        def paint(_e, w=badge, c=chan) -> None:
            p = QPainter(w)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            col = QColor(channel_color(THEME.palette, c))
            bg = QColor(col)
            bg.setAlpha(46)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(w.rect(), 9, 9)
            icon(CHANNEL_ICON.get(c, "ph.link"), col.name()).paint(p, w.rect().adjusted(8, 8, -8, -8))

        badge.paintEvent = paint
        THEME.on_change(badge.update, badge)
        rows = vbox(spacing=6)
        for head, text, role in (("Keeps", keeps, "Overline"), ("Tells you", tells, "Overline"),
                                 ("Watch out", watch, "OverlineWarn")):
            rows.addLayout(vbox(label(head.upper(), role), label(text, "Small", wrap=True), spacing=1))
        card.setLayout(vbox(hbox(badge, label(title, "H3"), "stretch", spacing=10), rows, "stretch", spacing=10,
                            margins=(16, 14, 16, 16)))
        return card

    @staticmethod
    def _parser_row(plugin) -> QWidget:
        w = QWidget()
        first = " ".join(plugin.description.split()).split(". ")[0].rstrip(".") + "."
        chips = [Chip(OS_LABEL.get(plugin.os, plugin.os), tone_fn("muted"), OS_ICON.get(plugin.os)),
                 Chip(CHANNEL_LABEL.get(plugin.channel.value, plugin.channel.value),
                      channel_color_fn(plugin.channel.value)),
                 Chip(VALIDATION_LABEL.get(plugin.validation.value, plugin.validation.value),
                      tone_fn("ok" if plugin.validation.value != "synthetic" else "warn"))]
        paths = ", ".join(plugin.paths[:2]) + (" …" if len(plugin.paths) > 2 else "")
        text = vbox(hbox(label(plugin.name, "H3"), "stretch", ChipRow(chips), spacing=10),
                    label(first, "Small", wrap=True), label(paths, "Faint", wrap=True), spacing=3)
        w.setLayout(text)
        return w
