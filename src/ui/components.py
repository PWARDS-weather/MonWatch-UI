# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: ui/components.py
# Description: Custom Qt widgets and components for satellite imagery interaction including clickable pixmap items and file list widgets.
#
# Principal Developer: Zero (Prince Al Zhanjie B. Dela Rosa)
# Affiliation: PWARDS-weather
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# See also: https://www.apache.org/licenses/LICENSE-2.0
#
# Copyright (C) 2025-2026 PWARDS-weather (Pasacao Weather Atmospheric
# and Real-Time Data System)
#
# --- OPEN-SOURCE POLICY ---
# Redistribution or modification without formally notifying PWARDS-weather
# developers constitutes unauthorized use and violates the license terms.
# Developers must be notified via email or GitHub issue before any changes
# are distributed. See LICENSE file for complete terms.
# =============================================================================

from PySide6.QtCore import QPointF, Qt, QMimeData, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDrag, QPaintEvent, QPainter, QFont, QPen, QBrush, QCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QPushButton,
    QScrollArea, QListWidget, QListWidgetItem, QSizePolicy,
    QColorDialog, QDialog, QComboBox, QCheckBox, QRadioButton,
    QButtonGroup, QSlider, QGroupBox, QLineEdit,
    QGraphicsPixmapItem, QGraphicsEllipseItem, QGraphicsPolygonItem,
)


class FileListWidget(QListWidget):
    pass
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setMinimumHeight(150)
    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        mime = QMimeData()
        url = QUrl.fromLocalFile(item.data(Qt.UserRole))
        mime.setUrls([url])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class ColorButton(QPushButton):

    colorChanged = Signal(str)

    def __init__(self, color: str = "#FFFFFF", parent=None):
        super().__init__(parent)
        self._color = color
        self.setFixedSize(48, 24)
        self._refresh()
        self.clicked.connect(self._pick)

    def _refresh(self):
        self.setStyleSheet(
            f"background:{self._color}; border:1px solid #666; border-radius:3px;"
        )

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Pick Color")
        if c.isValid():
            self._color = c.name()
            self._refresh()
            self.colorChanged.emit(self._color)

    def color(self) -> str:
        return self._color

    def set_color(self, c: str):
        self._color = c
        self._refresh()


class ViewportInfoBox(QWidget):
    """Floating info box widget that appears inside the viewport (JMA-style bulletin)."""

    position_changed = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Widget | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        self.title_bar = QFrame()
        self.title_bar.setStyleSheet("""
            QFrame {
                background-color: rgba(40, 40, 40, 240);
                border-top: 2px solid #f472b6;
                border-left: 1px solid #555;
                border-right: 1px solid #555;
                padding: 4px 8px;
            }
        """)
        self.title_bar.setCursor(Qt.OpenHandCursor)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(8)

        self.title_label = QLabel()
        self.title_label.setStyleSheet("color: #f472b6; font-weight: bold; font-size: 10px;")
        self.title_label.setText("TRACK BULLETIN")
        title_layout.addWidget(self.title_label, 1)

        close_btn = QPushButton("X")
        close_btn.setFixedSize(20, 20)
        close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #aaa;
                border: none;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover { color: #fff; }
        """)
        close_btn.clicked.connect(lambda: self.setVisible(False))
        title_layout.addWidget(close_btn)

        self.main_layout.addWidget(self.title_bar)

        self.content_scroll = QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setStyleSheet("""
            QScrollArea {
                border: 1px solid #444;
                border-top: none;
                background-color: rgba(30, 30, 30, 220);
            }
            QScrollBar:vertical { width: 8px; background: rgba(50, 50, 50, 150); }
            QScrollBar::handle:vertical { background: #666; border-radius: 4px; min-height: 20px; }
            QScrollBar::handle:vertical:hover { background: #777; }
        """)

        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background-color: transparent;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(6, 6, 6, 6)
        self.content_layout.setSpacing(4)

        self.content_scroll.setWidget(self.content_widget)
        self.main_layout.addWidget(self.content_scroll, 1)

        self.setFixedSize(320, 280)
        self.setVisible(True)

        self._dragging = False
        self._drag_start = None
        self._drag_offset = None

    def set_track_info(self, track_name, track_data):
        """Update the info box with track information (JMA bulletin style)."""
        self.title_label.setText(f"BULLETIN . {track_name[:20]}")

        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        header_label = QLabel()
        track_type = track_data.get("type", "")
        track_year = track_data.get("year", "")
        track_basin = track_data.get("basin", "")
        header_text = f"{track_type} {track_year} . {track_basin}"
        header_label.setText(header_text)
        header_label.setStyleSheet("color: #60a5fa; font-size: 9px; font-weight: bold; padding: 2px;")
        self.content_layout.addWidget(header_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #444;")
        sep.setFixedHeight(1)
        self.content_layout.addWidget(sep)

        points = track_data.get("points", [])
        if points:
            for i, point in enumerate(points[-15:]):
                pt_time = point.get("datetime", "")
                pt_lat = point.get("lat")
                pt_lon = point.get("lon")
                pt_int = point.get("intensity")
                pt_int_cat = point.get("intensity_category")

                point_frame = QFrame()
                point_frame.setStyleSheet("""
                    QFrame {
                        background-color: rgba(50, 50, 50, 150);
                        border: 1px solid #444;
                        border-radius: 2px;
                        padding: 4px;
                    }
                """)
                point_layout = QVBoxLayout(point_frame)
                point_layout.setContentsMargins(4, 3, 4, 3)
                point_layout.setSpacing(1)

                time_label = QLabel()
                time_label.setStyleSheet("color: #00ff9f; font-size: 9px; font-weight: bold;")
                if pt_time:
                    time_label.setText(f"Time: {pt_time}")
                point_layout.addWidget(time_label)

                if pt_lat is not None and pt_lon is not None:
                    pos_label = QLabel()
                    pos_label.setStyleSheet("color: #60a5fa; font-size: 8px;")
                    pos_label.setText(f"Lat: {pt_lat:+.2f}  Lon: {pt_lon:+.2f}")
                    point_layout.addWidget(pos_label)

                if pt_int is not None or pt_int_cat:
                    int_label = QLabel()
                    int_label.setStyleSheet("color: #ff9f43; font-size: 9px; font-weight: bold;")

                    intensity_text = ""
                    if pt_int is not None:
                        from .dialogs import MeteorologicalTrackDialog
                        cat = pt_int_cat or MeteorologicalTrackDialog.get_intensity_category(pt_int)
                        if cat:
                            intensity_text = f"Wind: {pt_int:.0f} kt  ({cat})"
                        else:
                            intensity_text = f"Wind: {pt_int:.0f} kt"
                    elif pt_int_cat:
                        intensity_text = f"Wind ({pt_int_cat})"

                    int_label.setText(intensity_text)
                    point_layout.addWidget(int_label)

                self.content_layout.addWidget(point_frame)
        else:
            empty_label = QLabel("No track points")
            empty_label.setStyleSheet("color: #888; font-size: 9px; padding: 10px; text-align: center;")
            empty_label.setAlignment(Qt.AlignCenter)
            self.content_layout.addWidget(empty_label)

        self.content_layout.addStretch()

    def set_position(self, x, y):
        """Set the position of the info box within its parent."""
        self.move(max(0, int(x)), max(0, int(y)))

    def get_position(self):
        """Get the current position of the info box."""
        return (self.pos().x(), self.pos().y())

    def mousePressEvent(self, event):
        """Handle mouse press for dragging (title bar only)."""
        if event.pos().y() < self.title_bar.height():
            self._dragging = True
            self._drag_start = event.globalPos()
            self._drag_offset = self.pos()
            self.title_bar.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        """Handle mouse move for dragging."""
        if self._dragging and self._drag_start and self._drag_offset:
            delta = event.globalPos() - self._drag_start
            new_pos = self._drag_offset + delta
            self.move(new_pos)
        event.accept()

    def mouseReleaseEvent(self, event):
        """Handle mouse release."""
        if self._dragging:
            self.position_changed.emit(self.pos().x(), self.pos().y())
        self._dragging = False
        self._drag_start = None
        self.title_bar.setCursor(Qt.OpenHandCursor)
        event.accept()


class SataidControlPanel(QWidget):
    bandChanged = Signal(str)
    functionChanged = Signal(str)
    gridToggled = Signal(bool)
    coastToggled = Signal(bool)
    overlayToggled = Signal(str, bool)
    playbackStepped = Signal(int)
    playbackPlayToggled = Signal(bool)
    brightnessChanged = Signal(float)
    contrastChanged = Signal(float)
    gridIntervalChanged = Signal(float)
    measModeChanged = Signal(str)

    _SATAID_STYLE = """
        QWidget#sataid_panel {
            background: #D0D0D0;
            color: #111;
            font-family: "Courier New", "Lucida Console", monospace;
            font-size: 10px;
        }
        QGroupBox {
            border: 1px inset #999;
            border-radius: 3px;
            margin-top: 4px;
            padding-top: 10px;
            font-weight: bold;
            color: #222;
            font-size: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 6px;
            padding: 0 4px;
        }
        QCheckBox, QRadioButton {
            spacing: 3px;
            font-size: 10px;
            font-family: "Courier New", monospace;
        }
        QCheckBox::indicator, QRadioButton::indicator {
            width: 11px;
            height: 11px;
        }
        QPushButton {
            background: #C8C8C8;
            border: 1px outset #AAA;
            border-radius: 2px;
            padding: 2px 6px;
            font-family: "Courier New", monospace;
            font-size: 10px;
            font-weight: bold;
            color: #111;
            max-height: 20px;
        }
        QPushButton:pressed {
            border-style: inset;
            background: #B0B0B0;
        }
        QPushButton:checked {
            border-style: inset;
            background: #A0A0A0;
        }
        QSlider::groove:horizontal {
            border: 1px inset #888;
            height: 5px;
            background: #D4D4D4;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #666;
            width: 10px;
            margin: -3px 0;
            border-radius: 2px;
        }
        QComboBox {
            color: #000;
            background: #D8D8D8;
            border: 1px inset #999;
            border-radius: 2px;
            padding: 1px 3px;
            font-family: "Courier New", monospace;
            font-size: 9px;
            min-width: 45px;
            max-height: 20px;
        }
        QLineEdit {
            background: #E8E8E8;
            border: 1px inset #AAA;
            border-radius: 2px;
            padding: 1px 3px;
            font-family: "Courier New", monospace;
            font-size: 10px;
            color: #222;
        }
        QSlider::sub-page:horizontal {
            background: #888;
            border-radius: 2px;
        }
    """

    _BAND_NAME_MAP = {
        "B13": "IR", "B07": "WV", "B01": "VIS", "B06": "I4s",
        "B08": "I2", "B03": "HVS",
    }

    _BAND_DISPLAY_NAMES = {
        "S1": "S01", "S2": "S02", "S3": "S03", "S4": "S04", "S5": "S05",
        "S6": "S06", "S7": "S07", "S8": "S08", "S9": "S09",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sataid_panel")
        self.setStyleSheet(self._SATAID_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(3, 3, 3, 3)

        self._function = None
        self._show_secondary_names = True

        self._build_playback_bar(layout)
        self._build_image_panel(layout)
        self.set_secondary_names(self._show_secondary_names)
        self._build_tools_group(layout)
        self._build_sub_panels(layout)
        layout.addStretch()

    def _build_playback_bar(self, parent):
        gb = QGroupBox()
        gb.setStyleSheet("QGroupBox { border: none; margin: 0px; padding: 0px; }")
        gl = QVBoxLayout(gb)
        gl.setSpacing(3)
        gl.setContentsMargins(0, 0, 0, 0)

        self.playback_slider = QSlider(Qt.Horizontal)
        self.playback_slider.setRange(0, 0)
        self.playback_slider.setFixedHeight(14)
        gl.addWidget(self.playback_slider)

        row1 = QHBoxLayout()
        row1.setSpacing(2)
        self.btn_prev = QPushButton("◀")
        self.btn_auto = QPushButton("AUTO")
        self.btn_auto.setCheckable(True)
        self.btn_next = QPushButton("▶")
        row1.addStretch()
        row1.addWidget(self.btn_prev)
        row1.addWidget(self.btn_auto)
        row1.addWidget(self.btn_next)
        row1.addStretch()
        gl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(2)
        self.btn_first = QPushButton("◀◀")
        self.btn_last = QPushButton("▶▶")
        row2.addWidget(self.btn_first)
        row2.addStretch()
        row2.addWidget(self.btn_last)
        gl.addLayout(row2)

        speed_row = QHBoxLayout()
        speed_row.setSpacing(4)
        speed_row.addWidget(QLabel("Fast"))
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(0, 100)
        self.speed_slider.setValue(50)
        self.speed_slider.setTickPosition(QSlider.TicksBelow)
        self.speed_slider.setTickInterval(25)
        self.speed_slider.setFixedHeight(14)
        speed_row.addWidget(self.speed_slider)
        speed_row.addWidget(QLabel("Slow"))
        gl.addLayout(speed_row)

        time_row = QHBoxLayout()
        time_row.setSpacing(4)
        self.time_display = QLineEdit()
        self.time_display.setReadOnly(True)
        self.time_display.setAlignment(Qt.AlignCenter)
        self.time_display.setText("--/--/---- --:--")
        self.time_display.setFixedWidth(100)
        time_row.addStretch()
        time_row.addWidget(self.time_display)
        time_row.addWidget(QLabel("UTC"))
        time_row.addStretch()
        gl.addLayout(time_row)

        self.btn_first.clicked.connect(lambda: self.playbackStepped.emit(-1))
        self.btn_prev.clicked.connect(lambda: self.playbackStepped.emit(-1))
        self.btn_next.clicked.connect(lambda: self.playbackStepped.emit(1))
        self.btn_last.clicked.connect(lambda: self.playbackStepped.emit(1))
        self.btn_auto.toggled.connect(self.playbackPlayToggled.emit)

        parent.addWidget(gb)

    def _build_image_panel(self, parent):
        gb = QGroupBox("Image")
        gl = QGridLayout()
        gl.setSpacing(1)
        gl.setContentsMargins(4, 10, 4, 4)

        self.band_group = QButtonGroup(self)
        self._band_buttons = []
        col1 = ["B13", "B07", "B01", "B04", "B06", "B10", "B12", "B15", "B07S", "EIRc"]
        col2 = ["B08", "B03", "B02", "B05", "B09", "B11", "B14", "B16", "B03H", "EIRm"]
        col3 = ["S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9"]
        for r, name in enumerate(col1):
            rb = QRadioButton(name)
            self.band_group.addButton(rb)
            gl.addWidget(rb, r, 0)
            self._band_buttons.append((rb, name))
        for r, name in enumerate(col2):
            rb = QRadioButton(name)
            self.band_group.addButton(rb)
            gl.addWidget(rb, r, 1)
            self._band_buttons.append((rb, name))
        for r, name in enumerate(col3):
            display = self._BAND_DISPLAY_NAMES.get(name, name)
            rb = QRadioButton(display)
            self.band_group.addButton(rb)
            gl.addWidget(rb, r, 2)
            self._band_buttons.append((rb, name))
        self.band_group.buttonClicked.connect(self._emit_band_changed)
        if self.band_group.buttons():
            self.band_group.buttons()[0].setChecked(True)

        gb.setLayout(gl)
        parent.addWidget(gb)

    def _emit_band_changed(self, btn):
        for rb, internal in self._band_buttons:
            if rb is btn:
                self.bandChanged.emit(internal)
                return
        self.bandChanged.emit(btn.text())

    def set_secondary_names(self, enabled: bool):
        self._show_secondary_names = enabled
        for rb, internal in self._band_buttons:
            if internal in self._BAND_DISPLAY_NAMES:
                rb.setText(self._BAND_DISPLAY_NAMES[internal])
            elif enabled:
                rb.setText(self._BAND_NAME_MAP.get(internal, internal))
            else:
                rb.setText(internal)

    def _build_tools_group(self, parent):
        container = QWidget()
        tl = QVBoxLayout(container)
        tl.setSpacing(6)
        tl.setContentsMargins(0, 0, 0, 0)

        gb_o = QGroupBox("Display")
        ol = QVBoxLayout(gb_o)
        ol.setSpacing(3)
        ol.setContentsMargins(4, 10, 4, 4)

        grid_row = QHBoxLayout()
        grid_row.setSpacing(6)
        self.grid_cb = QCheckBox("Grid")
        self.grid_interval = QComboBox()
        self.grid_interval.addItems(["1deg", "2deg", "5deg", "10deg", "15deg", "20deg", "30deg", "45deg", "60deg", "90deg"])
        self.grid_interval.setCurrentText("10deg")
        grid_row.addWidget(self.grid_cb)
        grid_row.addWidget(self.grid_interval)
        grid_row.addStretch()
        ol.addLayout(grid_row)

        pairs = [
            ("Coast", "Line"),
            ("Text", "NWP"),
            ("RADAR", "Wind"),
        ]
        self._overlay_cbs = {}
        for left, right in pairs:
            hr = QHBoxLayout()
            hr.setSpacing(8)
            cbl = QCheckBox(left)
            cbr = QCheckBox(right)
            self._overlay_cbs[left] = cbl
            self._overlay_cbs[right] = cbr
            hr.addWidget(cbl)
            hr.addWidget(cbr)
            hr.addStretch()
            ol.addLayout(hr)

        self.grid_cb.toggled.connect(lambda c: self.gridToggled.emit(c))
        self.grid_interval.currentTextChanged.connect(self._emit_grid_interval)
        for name, cb in self._overlay_cbs.items():
            if name == "Coast":
                cb.toggled.connect(self.coastToggled.emit)
            else:
                cb.toggled.connect(lambda c, n=name: self.overlayToggled.emit(n.lower(), c))
        tl.addWidget(gb_o)

        gb_f = QGroupBox("Function")
        fl = QGridLayout()
        fl.setSpacing(4)
        fl.setContentsMargins(4, 10, 4, 4)

        self.func_buttons = {}
        funcs = [("Gray", 0, 0), ("Info", 0, 1), ("Measur", 1, 0), ("Draw", 1, 1), ("Obs", 2, 0), ("TC", 2, 1)]
        for label, r, c in funcs:
            cb = QCheckBox(label)
            cb.toggled.connect(lambda checked, l=label: self._on_func_toggle(l, checked))
            fl.addWidget(cb, r, c)
            self.func_buttons[label] = cb
        gb_f.setLayout(fl)
        tl.addWidget(gb_f)

        parent.addWidget(container)

    def _on_func_toggle(self, label, checked):
        if not checked:
            self._function = None
        else:
            for name, cb in self.func_buttons.items():
                if name != label:
                    cb.setChecked(False)
            self._function = label
        self.functionChanged.emit(self._function or "")
        self._update_sub_panel_visibility()

    def _emit_grid_interval(self, text):
        deg = text.replace("deg", "")
        try:
            self.gridIntervalChanged.emit(float(deg))
        except ValueError:
            self.gridIntervalChanged.emit(5.0)

    def _build_sub_panels(self, parent):
        self._sub_panels = {}
        self._build_gray_panel(parent)
        self._build_info_panel(parent)
        self._build_meas_panel(parent)
        self._build_draw_panel(parent)
        self._build_obs_panel(parent)
        self._build_tc_panel(parent)
        self._update_sub_panel_visibility()

    def _build_gray_panel(self, parent):
        p = QGroupBox("Gray")
        gl = QVBoxLayout(p)
        gl.setSpacing(3)
        gl.setContentsMargins(4, 10, 4, 4)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.revs_cb = QCheckBox("Revs")
        self.color_btn = QPushButton("Color")
        self.initial_btn = QPushButton("Initial")
        top.addWidget(self.revs_cb)
        top.addWidget(self.color_btn)
        top.addWidget(self.initial_btn)
        gl.addLayout(top)

        brit_r = QHBoxLayout()
        brit_r.setSpacing(4)
        brit_r.addWidget(QLabel("Brit"))
        self.brit_slider = QSlider(Qt.Horizontal)
        self.brit_slider.setRange(-100, 100)
        self.brit_slider.setValue(0)
        brit_r.addWidget(self.brit_slider)
        self.brit_label = QLabel("0")
        self.brit_label.setFixedWidth(24)
        brit_r.addWidget(self.brit_label)
        gl.addLayout(brit_r)

        cntr_r = QHBoxLayout()
        cntr_r.setSpacing(4)
        cntr_r.addWidget(QLabel("Ctrl"))
        self.cntr_slider = QSlider(Qt.Horizontal)
        self.cntr_slider.setRange(10, 300)
        self.cntr_slider.setValue(100)
        cntr_r.addWidget(self.cntr_slider)
        self.cntr_label = QLabel("1.0")
        self.cntr_label.setFixedWidth(24)
        cntr_r.addWidget(self.cntr_label)
        gl.addLayout(cntr_r)

        self.brit_slider.valueChanged.connect(self._on_brit_changed)
        self.cntr_slider.valueChanged.connect(self._on_cntr_changed)
        self.initial_btn.clicked.connect(self._reset_gray)

        parent.addWidget(p)
        self._sub_panels["Gray"] = p

    def _build_info_panel(self, parent):
        p = QGroupBox("Info")
        gl = QVBoxLayout(p)
        gl.setSpacing(4)
        gl.setContentsMargins(4, 10, 4, 4)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.info_ctrl_btn = QRadioButton("Ctrl")
        self.info_calb_btn = QRadioButton("Calb")
        row.addWidget(self.info_ctrl_btn)
        row.addWidget(self.info_calb_btn)
        row.addStretch()
        gl.addLayout(row)

        self.info_label = QLabel("Lat: ---  Lon: ---")
        gl.addWidget(self.info_label)

        parent.addWidget(p)
        self._sub_panels["Info"] = p

    def _build_meas_panel(self, parent):
        p = QGroupBox("Measure")
        gl = QGridLayout()
        gl.setSpacing(4)
        gl.setContentsMargins(4, 10, 4, 4)

        self.meas_group = QButtonGroup(self)
        meas_items = [
            ("Brit", 0, 0), ("Move", 0, 1),
            ("Time", 1, 0), ("Cross", 1, 1),
            ("Contour", 2, 0), ("Hist", 2, 1),
        ]
        for label, r, c in meas_items:
            rb = QRadioButton(label)
            self.meas_group.addButton(rb)
            gl.addWidget(rb, r, c)
        self.meas_group.buttonClicked.connect(lambda btn: self.measModeChanged.emit(btn.text()))

        p.setLayout(gl)
        parent.addWidget(p)
        self._sub_panels["Measur"] = p

    def _build_draw_panel(self, parent):
        p = QGroupBox("Draw")
        gl = QGridLayout()
        gl.setSpacing(4)
        gl.setContentsMargins(4, 10, 4, 4)

        self.thickness_group = QButtonGroup(self)
        for i, label in enumerate(["Thin", "Std", "Thick"]):
            rb = QRadioButton(label)
            self.thickness_group.addButton(rb)
            gl.addWidget(rb, i, 0)
            if label == "Std":
                rb.setChecked(True)

        self.curve_cb = QCheckBox("Curve")
        gl.addWidget(self.curve_cb, 0, 1)
        self.erase_rb = QRadioButton("Erase")
        gl.addWidget(self.erase_rb, 1, 1)
        self.extra_btn = QPushButton("Extra")
        gl.addWidget(self.extra_btn, 2, 1)

        p.setLayout(gl)
        parent.addWidget(p)
        self._sub_panels["Draw"] = p

    def _build_obs_panel(self, parent):
        p = QGroupBox("Obs")
        gl = QGridLayout()
        gl.setSpacing(4)
        gl.setContentsMargins(4, 10, 4, 4)

        self.obs_group = QButtonGroup(self)
        obs_items = [
            ("Synop", 0, 0), ("AWS", 0, 1),
            ("WPR", 1, 0), ("Track", 1, 1),
            ("LIDEN", 2, 0),
        ]
        for label, r, c in obs_items:
            rb = QRadioButton(label)
            self.obs_group.addButton(rb)
            gl.addWidget(rb, r, c)

        p.setLayout(gl)
        parent.addWidget(p)
        self._sub_panels["Obs"] = p

    def _build_tc_panel(self, parent):
        p = QGroupBox("TC")
        gl = QGridLayout()
        gl.setSpacing(4)
        gl.setContentsMargins(4, 10, 4, 4)

        self.tc_left_group = QButtonGroup(self)
        for i, label in enumerate(["Center", "Early", "Objec"]):
            rb = QRadioButton(label)
            self.tc_left_group.addButton(rb)
            gl.addWidget(rb, i, 0)

        self.tc_right_group = QButtonGroup(self)
        self.tc_intens_rb = QRadioButton("Intens")
        self.tc_right_group.addButton(self.tc_intens_rb)
        gl.addWidget(self.tc_intens_rb, 0, 1)

        self.tc_hist_cb = QCheckBox("Hist")
        gl.addWidget(self.tc_hist_cb, 1, 1)

        p.setLayout(gl)
        parent.addWidget(p)
        self._sub_panels["TC"] = p

    def _update_sub_panel_visibility(self):
        for name, panel in self._sub_panels.items():
            panel.setVisible(name == self._function)

    def _on_brit_changed(self, val):
        self.brit_label.setText(str(val))
        self.brightnessChanged.emit(val / 100.0)

    def _on_cntr_changed(self, val):
        cntr = val / 100.0
        self.cntr_label.setText(f"{cntr:.1f}")
        self.contrastChanged.emit(cntr)

    def _reset_gray(self):
        self.brit_slider.setValue(0)
        self.cntr_slider.setValue(100)
        self.revs_cb.setChecked(False)

    def set_time(self, text: str):
        self.time_display.setText(text)

    def set_playback_range(self, maximum: int):
        self.playback_slider.setRange(0, max(0, maximum - 1))

    def set_playback_position(self, pos: int):
        self.playback_slider.blockSignals(True)
        self.playback_slider.setValue(pos)
        self.playback_slider.blockSignals(False)

    def set_band(self, band: str):
        for btn in self.band_group.buttons():
            if btn.text() == band:
                btn.setChecked(True)
                break

    def get_brightness(self) -> float:
        return self.brit_slider.value() / 100.0

    def get_contrast(self) -> float:
        return self.cntr_slider.value() / 100.0

    def get_grid_interval(self) -> float:
        deg = self.grid_interval.currentText().replace("deg", "")
        try:
            return float(deg)
        except ValueError:
            return 5.0


class ClickablePixmapItem(QGraphicsPixmapItem):
    def __init__(self, pixmap, point_data, app_ref, x, y, sz):
        super().__init__(pixmap)
        self._pd = point_data
        self._app = app_ref
        self.setPos(x, y)
        self.setShapeMode(QGraphicsPixmapItem.BoundingRectShape)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self._cx = x + sz / 2
        self._cy = y + sz / 2

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            ev.accept()
            if ev.modifiers() & Qt.ShiftModifier:
                self._app._remove_point_info_box()
            else:
                self._app._show_point_info_box(self._pd, QPointF(self._cx, self._cy))
        else:
            super().mousePressEvent(ev)


class ClickablePointItem(QGraphicsEllipseItem):
    def __init__(self, cx, cy, r, point_data, app_ref, pen=None, brush=None):
        super().__init__(cx - r, cy - r, r * 2, r * 2)
        if pen: self.setPen(pen)
        if brush: self.setBrush(brush)
        self._pd = point_data
        self._app = app_ref
        self._cx, self._cy = cx, cy
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            ev.accept()
            if ev.modifiers() & Qt.ShiftModifier:
                self._app._remove_point_info_box()
            else:
                self._app._show_point_info_box(self._pd, QPointF(self._cx, self._cy))
        else:
            super().mousePressEvent(ev)


class ClickablePolygonItem(QGraphicsPolygonItem):
    """A clickable polygon marker (e.g. a rotated aircraft symbol).

    The polygon should be built in final screen coordinates around an origin
    at the marker centre; ``x``/``y`` is the world position it is placed at.
    """

    def __init__(self, polygon, point_data, app_ref, x, y):
        super().__init__(polygon)
        self._pd = point_data
        self._app = app_ref
        b = polygon.boundingRect()
        self._cx = x + b.center().x()
        self._cy = y + b.center().y()
        self.setPos(x, y)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            ev.accept()
            if ev.modifiers() & Qt.ShiftModifier:
                self._app._remove_point_info_box()
            else:
                self._app._show_point_info_box(self._pd, QPointF(self._cx, self._cy))
        else:
            super().mousePressEvent(ev)
