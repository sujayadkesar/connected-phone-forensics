"""Colours, fonts and the application style sheet, for a light and a dark theme.

Every colour the GUI uses comes from a :class:`Palette`; widgets never hard-code
hex values. Channel colours are the categorical slots of a colour-blind-safe
palette (validated for both themes), used for identity only: the text beside a
colour always stays in the normal text colour.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str           # window background
    sidebar: str
    surface: str      # cards, tables
    raised: str       # inputs, hover, secondary buttons
    border: str
    divider: str      # row separators inside cards
    text: str
    muted: str
    faint: str
    accent: str
    accent_hover: str
    accent_text: str  # text on an accent background
    accent_soft: str  # selection
    ok: str
    warn: str
    bad: str
    series: tuple[str, ...]  # categorical slots, fixed order

    @property
    def dark(self) -> bool:
        return self.name == "dark"


# Brand colours from the logo kit (docs/brand): phone blues, monitor blue, navy, signal yellow.
BRAND_BLUE = "#2456DE"
BRAND_BLUE_LIGHT = "#5C95FF"
BRAND_SKY = "#7CCBFF"
BRAND_NAVY = "#0B1630"
BRAND_YELLOW = "#FFC23D"

DARK = Palette(
    "dark", bg="#0a1120", sidebar="#070d19", surface="#101a2e", raised="#16223a", border="#22314f",
    divider="#1a2742", text="#e9eef8", muted="#9dabc6", faint="#5e6f91", accent=BRAND_BLUE_LIGHT,
    accent_hover="#7dabff", accent_text="#ffffff", accent_soft="#1a2f5c", ok="#3fb950",
    warn=BRAND_YELLOW, bad="#f26b6b",
    series=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"),
)
LIGHT = Palette(
    "light", bg="#f3f6fb", sidebar="#e9eff9", surface="#ffffff", raised="#f3f6fb", border="#dae2ef",
    divider="#ecf1f8", text="#0e1a33", muted="#4c5b79", faint="#93a0b8", accent=BRAND_BLUE,
    accent_hover="#1b44c2", accent_text="#ffffff", accent_soft="#dee8ff", ok="#1a8f3c",
    warn="#b57800", bad="#d03b3b",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"),
)

# Channel -> categorical slot. Fixed, so a channel keeps its colour whatever is on screen;
# channels beyond the seven slots are drawn neutral (their chips still carry the label).
CHANNEL_SLOT = {"usb": 0, "wifi_hotspot": 1, "phone_link": 2, "pairing": 3, "continuity": 4,
                "backup": 5, "bluetooth": 6}


def channel_color(p: Palette, channel: str) -> str:
    slot = CHANNEL_SLOT.get(channel)
    return p.series[slot] if slot is not None else p.faint


def mix(a: str, b: str, t: float) -> str:
    """Blend colour ``a`` towards ``b`` by ``t`` (0..1)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name()


def _pick(families: list[str], available: set[str]) -> str | None:
    return next((f for f in families if f in available), None)


@dataclass(frozen=True)
class Fonts:
    ui: QFont
    mono: QFont


def load_fonts() -> Fonts:
    available = set(QFontDatabase.families())
    ui_family = _pick(["Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue",
                       "Cantarell", "Noto Sans", "DejaVu Sans"], available)
    mono_family = _pick(["Cascadia Mono", "JetBrains Mono", "Consolas", "SF Mono", "Menlo",
                         "DejaVu Sans Mono", "Liberation Mono"], available)
    ui = QFont(ui_family) if ui_family else QGuiApplication.font()
    ui.setPointSizeF(9.75)
    ui.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    mono = QFont(mono_family) if mono_family else QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    mono.setPointSizeF(9.0)
    mono.setStyleHint(QFont.StyleHint.Monospace)
    return Fonts(ui, mono)


def _asset_dir(p: Palette) -> Path:
    """Small theme-coloured SVGs the style sheet refers to (combo arrows, check marks)."""
    d = Path(tempfile.gettempdir()) / "cpf-gui-assets" / p.name
    d.mkdir(parents=True, exist_ok=True)
    svgs = {
        "chevron-down.svg": f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" '
                            f'fill="none" stroke="{p.muted}" stroke-width="2.5" stroke-linecap="round" '
                            f'stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
        "check.svg": '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" '
                     f'fill="none" stroke="{p.accent_text}" stroke-width="3.5" stroke-linecap="round" '
                     'stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
        "spin-up.svg": f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" '
                       f'fill="none" stroke="{p.muted}" stroke-width="3" stroke-linecap="round" '
                       f'stroke-linejoin="round"><path d="M6 15l6-6 6 6"/></svg>',
        "spin-down.svg": f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" '
                         f'fill="none" stroke="{p.muted}" stroke-width="3" stroke-linecap="round" '
                         f'stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>',
    }
    for name, svg in svgs.items():
        f = d / name
        if not f.exists() or f.read_text(encoding="utf-8") != svg:
            f.write_text(svg, encoding="utf-8")
    return d


def stylesheet(p: Palette) -> str:
    a = _asset_dir(p).as_posix()
    zebra = mix(p.surface, p.raised, 0.45)
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p.text}; }}
QMainWindow, #Root, #Page, QScrollArea#PageScroll, QScrollArea#PageScroll > QWidget > QWidget {{ background: {p.bg}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea#PanelScroll > QWidget, QScrollArea#PanelScroll > QWidget > QWidget {{ background: transparent; }}
QToolTip {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; padding: 6px 8px; border-radius: 6px; }}

/* sidebar */
#Sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
#NavSection {{ color: {p.faint}; font-size: 7.75pt; font-weight: 700; padding: 14px 12px 4px 12px; }}
QPushButton#Nav {{ text-align: left; padding: 8px 12px; border-radius: 8px; border: none; background: transparent;
    color: {p.muted}; font-weight: 500; }}
QPushButton#Nav:hover {{ background: {p.raised}; color: {p.text}; }}
QPushButton#Nav:checked {{ background: {p.accent_soft}; color: {p.text}; font-weight: 600; }}
QPushButton#Nav:disabled {{ color: {p.faint}; }}
#NavCount {{ background: {p.raised}; color: {p.muted}; border-radius: 8px; padding: 1px 7px; font-size: 8pt; font-weight: 600; }}
#NavCount[alert="true"] {{ background: {mix(p.surface, p.bad, 0.22)}; color: {p.text}; }}
#CaseBox {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}

/* typography */
#H1 {{ font-size: 17pt; font-weight: 600; }}
#H2 {{ font-size: 12pt; font-weight: 600; }}
#H3 {{ font-size: 10.25pt; font-weight: 600; }}
#Muted {{ color: {p.muted}; }}
#Faint {{ color: {p.faint}; }}
#Small {{ color: {p.muted}; font-size: 8.5pt; }}
#Overline {{ color: {p.faint}; font-size: 7.75pt; font-weight: 700; }}
#StatValue {{ font-size: 21pt; font-weight: 600; }}
#Error {{ color: {p.bad}; }}
#Mono {{ color: {p.text}; }}

/* cards */
#Card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
#Card[tone="warn"] {{ background: {mix(p.surface, p.warn, 0.07)}; border-color: {mix(p.border, p.warn, 0.45)}; }}
#Card[tone="bad"] {{ background: {mix(p.surface, p.bad, 0.07)}; border-color: {mix(p.border, p.bad, 0.45)}; }}
#Card[clickable="true"]:hover {{ border-color: {p.faint}; }}
#Divider {{ background: {p.divider}; max-height: 1px; min-height: 1px; border: none; }}
#DropZone {{ background: {mix(p.surface, p.accent, 0.03)}; border: 2px dashed {p.faint}; border-radius: 12px; }}
#DropZone[active="true"] {{ border-color: {p.accent}; background: {mix(p.surface, p.accent, 0.10)}; }}
#DropZone[filled="true"] {{ border-style: solid; border-color: {p.border}; background: {p.surface}; }}
#Chip {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 10px; }}
#ChipText {{ font-size: 8.5pt; font-weight: 500; color: {p.text}; }}
#IconBadge {{ border-radius: 10px; }}
#StepNumber {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 14px; color: {p.muted};
    font-weight: 600; font-size: 8.5pt; }}

/* buttons */
QPushButton {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 8px; padding: 7px 14px;
    color: {p.text}; font-weight: 500; }}
QPushButton:hover {{ border-color: {p.faint}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.faint}; border-color: {p.divider}; }}
QPushButton#Primary {{ background: {p.accent}; color: {p.accent_text}; border: 1px solid {p.accent}; font-weight: 600;
    padding: 8px 18px; }}
QPushButton#Primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton#Primary:disabled {{ background: {p.raised}; color: {p.faint}; border-color: {p.border}; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {p.muted}; }}
QPushButton#Ghost:hover {{ background: {p.raised}; color: {p.text}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid {mix(p.border, p.bad, 0.6)}; color: {p.bad}; }}
QPushButton#Danger:hover {{ background: {mix(p.surface, p.bad, 0.12)}; }}
QPushButton#Link {{ background: transparent; border: none; padding: 0; color: {p.accent}; text-align: left; }}
QPushButton#Link:hover {{ color: {p.accent_hover}; text-decoration: underline; }}
QPushButton#Seg {{ background: transparent; border: none; border-radius: 6px; padding: 5px 12px; color: {p.muted};
    font-weight: 500; }}
QPushButton#Seg:hover {{ color: {p.text}; }}
QPushButton#Seg:checked {{ background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; }}
#SegGroup {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 8px; }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {p.raised}; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {p.accent}; selection-color: {p.accent_text}; min-height: 18px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{ border-color: {p.faint}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p.accent}; }}
QLineEdit[invalid="true"] {{ border-color: {p.bad}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {p.faint}; }}
QComboBox {{ padding-right: 28px; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{a}/chevron-down.svg"); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px;
    selection-background-color: {p.accent_soft}; selection-color: {p.text}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 18px; subcontrol-origin: border; }}
QSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow {{ image: url("{a}/spin-up.svg"); width: 9px; height: 9px; }}
QSpinBox::down-arrow {{ image: url("{a}/spin-down.svg"); width: 9px; height: 9px; }}
QCheckBox {{ spacing: 9px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid {p.faint}; background: {p.raised}; }}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: url("{a}/check.svg"); }}
QCheckBox:disabled {{ color: {p.faint}; }}

/* tables and lists */
QTableView, QTableWidget {{ background: {p.surface}; alternate-background-color: {zebra}; border: none;
    gridline-color: transparent; selection-background-color: {p.accent_soft}; selection-color: {p.text}; }}
QTableView::item {{ padding: 0 10px; border: none; }}
QTableView::item:selected {{ background: {p.accent_soft}; color: {p.text}; }}
QHeaderView {{ background: {p.surface}; border: none; }}
QHeaderView::section {{ background: {p.surface}; color: {p.muted}; border: none; border-bottom: 1px solid {p.border};
    padding: 8px 10px; font-weight: 600; font-size: 8.75pt; }}
QHeaderView::section:hover {{ color: {p.text}; }}
QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border: none; border-radius: 10px; margin: 1px 0; }}
QListWidget::item:hover {{ background: {p.raised}; }}
QListWidget::item:selected {{ background: {p.accent_soft}; }}
QPlainTextEdit, QTextEdit {{ background: {p.raised}; border: 1px solid {p.border}; border-radius: 8px; padding: 6px;
    selection-background-color: {p.accent}; selection-color: {p.accent_text}; }}

/* misc */
QProgressBar {{ background: {p.raised}; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 4px; }}
QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px 2px 2px 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0 2px 2px 2px; }}
QScrollBar::handle {{ background: {p.border}; border-radius: 4px; }}
QScrollBar::handle:hover {{ background: {p.faint}; }}
QScrollBar::handle:vertical {{ min-height: 32px; }}
QScrollBar::handle:horizontal {{ min-width: 32px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {p.accent_soft}; }}
QMessageBox {{ background: {p.surface}; }}
#Toast {{ background: {p.text}; color: {p.bg}; border-radius: 10px; padding: 10px 16px; font-weight: 500; }}
"""


def qpalette(p: Palette) -> QPalette:
    """Palette for what style sheets do not reach (native dialogs, focus frames, links)."""
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.bg, QPalette.ColorRole.WindowText: p.text, QPalette.ColorRole.Base: p.surface,
        QPalette.ColorRole.AlternateBase: p.raised, QPalette.ColorRole.Text: p.text,
        QPalette.ColorRole.Button: p.raised, QPalette.ColorRole.ButtonText: p.text,
        QPalette.ColorRole.Highlight: p.accent, QPalette.ColorRole.HighlightedText: p.accent_text,
        QPalette.ColorRole.ToolTipBase: p.surface, QPalette.ColorRole.ToolTipText: p.text,
        QPalette.ColorRole.PlaceholderText: p.faint, QPalette.ColorRole.Link: p.accent,
        QPalette.ColorRole.Mid: p.border, QPalette.ColorRole.Dark: p.border, QPalette.ColorRole.Light: p.raised,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(p.faint))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(p.faint))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(p.faint))
    return pal


def system_prefers_dark() -> bool:
    try:
        from PySide6.QtCore import Qt

        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - older Qt: fall back to dark
        return True


def apply(app: QApplication, p: Palette) -> None:
    app.setPalette(qpalette(p))
    app.setStyleSheet(stylesheet(p))


def style_title_bar(window: QWidget, p: Palette) -> None:
    """Match the Windows 10/11 title bar to the theme (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.c_void_p(int(window.winId()))
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1 if p.dark else 0)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (newer, older builds)
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(dark), ctypes.sizeof(dark)) == 0:
                break
        c = QColor(p.sidebar)
        caption = ctypes.c_int(c.red() | (c.green() << 8) | (c.blue() << 16))  # COLORREF 0x00BBGGRR
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))  # DWMWA_CAPTION_COLOR
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
