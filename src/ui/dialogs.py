# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: ui/dialogs.py
# Description: Dialog windows for forecast selection, theme configuration, animation export, and user interaction prompts.
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

import uuid
from datetime import datetime

from PySide6.QtCore import QDate, QDateTime, Qt, Signal, QEventLoop
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
    QDoubleSpinBox, QComboBox, QCheckBox, QGroupBox, QGridLayout,
    QPushButton, QDialogButtonBox, QScrollArea, QWidget, QMessageBox,
    QTextEdit, QDateTimeEdit, QFormLayout, QListWidget, QListWidgetItem,
    QMenu, QProgressBar, QApplication,
)
from PySide6.QtGui import QAction

from ..core.helpers import THEMES
from .components import ColorButton


class DraggablePointsListWidget(QListWidget):
    """Custom list widget that allows drag-and-drop reordering of track points."""
    points_reordered = Signal(int, int)  # old_index, new_index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QListWidget.InternalMove)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.model().rowsMoved.connect(self._on_rows_moved)

    def _on_rows_moved(self, sourceParent, sourceStart, sourceEnd, destinationParent, destinationRow):
        """Handle the rows moved signal from the model."""
        self.points_reordered.emit(sourceStart, destinationRow)
        self.setCurrentRow(destinationRow)


class MeteorologicalTrackDialog(QDialog):
    INTENSITY_CATEGORIES = {
        "TD": (0, 33),           # Tropical Depression
        "TS": (34, 63),          # Tropical Storm
        "STS": (64, 95),         # Severe Tropical Storm
        "TY": (96, 129),         # Typhoon
        "STY": (130, 999),       # Super Typhoon
    }

    @staticmethod
    def get_intensity_category(kt):
        """Get intensity category from wind speed in knots."""
        if kt is None:
            return None
        for cat, (min_kt, max_kt) in MeteorologicalTrackDialog.INTENSITY_CATEGORIES.items():
            if min_kt <= kt <= max_kt:
                return cat
        return None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Meteorological Track")
        self.setModal(True)
        self.resize(560, 580)
        self._track_id = None

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Optional name (e.g. Typhoon Hagibis)")
        form.addRow("Track Name:", self.name_edit)

        self.type_combo = QComboBox()
        self.type_combo.addItems([
            "Typhoon", "Hurricane", "Tropical Storm", "Tropical Depression",
            "Extratropical Cyclone", "Invest", "Other"
        ])
        form.addRow("Type:", self.type_combo)

        self.year_spin = QSpinBox()
        self.year_spin.setRange(1980, 2035)
        self.year_spin.setValue(QDate.currentDate().year())
        form.addRow("Year:", self.year_spin)

        self.basin_combo = QComboBox()
        self.basin_combo.addItems(["West Pacific", "East Pacific", "Atlantic", "Indian Ocean", "Other"])
        form.addRow("Basin:", self.basin_combo)

        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText("Additional notes, JTWC / JMA ID, etc.")
        self.notes_edit.setMaximumHeight(50)
        form.addRow("Notes:", self.notes_edit)

        row_col = QHBoxLayout()
        row_col.addWidget(QLabel("Track Color:"))
        self.color_btn = ColorButton("#FFFFFF")
        row_col.addWidget(self.color_btn)
        row_col.addStretch()
        form.addRow(row_col)

        self.add_to_infobox_cb = QCheckBox("Add to Info Box (will show track info in viewport)")
        form.addRow("Display:", self.add_to_infobox_cb)

        layout.addLayout(form)

        pts_group = QGroupBox("Track Points (point-to-point)")
        pts_group.setStyleSheet("QGroupBox { font-size:9pt; color:#f472b6; }")
        pts_l = QVBoxLayout(pts_group)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Lat:"))
        self.lat_edit = QLineEdit()
        self.lat_edit.setFixedWidth(70)
        self.lat_edit.setPlaceholderText("e.g. 15.3")
        row1.addWidget(self.lat_edit)
        row1.addWidget(QLabel("Lon:"))
        self.lon_edit = QLineEdit()
        self.lon_edit.setFixedWidth(70)
        self.lon_edit.setPlaceholderText("e.g. 134.5")
        row1.addWidget(self.lon_edit)
        row1.addStretch()
        pts_l.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Date/Time:"))
        self.pt_datetime_edit = QDateTimeEdit()
        self.pt_datetime_edit.setCalendarPopup(True)
        self.pt_datetime_edit.setDateTime(QDateTime.currentDateTime())
        self.pt_datetime_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.pt_datetime_edit.setFixedWidth(160)
        row2.addWidget(self.pt_datetime_edit)
        row2.addWidget(QLabel("Intensity (kt):"))
        self.intensity_edit = QLineEdit()
        self.intensity_edit.setFixedWidth(60)
        self.intensity_edit.setPlaceholderText("65")
        self.intensity_edit.setToolTip("Wind speed in knots (e.g. 65, 120)")
        row2.addWidget(self.intensity_edit)
        self.intensity_name_label = QLabel("")
        self.intensity_name_label.setStyleSheet("color: #f472b6; font-weight: bold;")
        row2.addWidget(self.intensity_name_label)
        row2.addStretch()
        self.intensity_edit.textChanged.connect(self._update_intensity_name)
        pts_l.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Intensity Category:"))
        self.pt_intensity_cat_combo = QComboBox()
        self.pt_intensity_cat_combo.addItems(["None", "LPA", "LOW", "TD", "TS", "STS", "TY", "STY", "Hurricane", "Major Hurricane"])
        self.pt_intensity_cat_combo.setToolTip("Manually set cyclone intensity category (optional)")
        row3.addWidget(self.pt_intensity_cat_combo)
        row3.addStretch()
        pts_l.addLayout(row3)

        btn_row = QHBoxLayout()
        add_pt_btn = QPushButton("Add Point")
        add_pt_btn.setFixedHeight(22)
        add_pt_btn.clicked.connect(self._add_track_point)
        btn_row.addWidget(add_pt_btn)

        edit_pt_btn = QPushButton("Edit Selected")
        edit_pt_btn.setFixedHeight(22)
        edit_pt_btn.clicked.connect(self._edit_selected_track_point)
        btn_row.addWidget(edit_pt_btn)

        remove_sel_btn = QPushButton("Remove Selected")
        remove_sel_btn.setFixedHeight(22)
        remove_sel_btn.clicked.connect(self._remove_selected_track_point_button)
        btn_row.addWidget(remove_sel_btn)

        order_btn = QPushButton("Order by Time")
        order_btn.setFixedHeight(22)
        order_btn.clicked.connect(self._order_points_by_time)
        btn_row.addWidget(order_btn)

        clear_pts_btn = QPushButton("Clear All")
        clear_pts_btn.setFixedHeight(22)
        clear_pts_btn.clicked.connect(self._clear_track_points)
        btn_row.addWidget(clear_pts_btn)
        pts_l.addLayout(btn_row)

        self.points_list = DraggablePointsListWidget()
        self.points_list.setFixedHeight(120)
        self.points_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.points_list.customContextMenuRequested.connect(self._show_point_context_menu)
        self.points_list.points_reordered.connect(self._on_points_reordered)
        pts_l.addWidget(self.points_list)

        self._track_points = []

        layout.addWidget(pts_group)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _update_intensity_name(self):
        """Update intensity category label based on wind speed."""
        try:
            kt = float(self.intensity_edit.text().strip()) if self.intensity_edit.text().strip() else None
            if kt is not None:
                cat = self.get_intensity_category(kt)
                if cat:
                    self.intensity_name_label.setText(f"({cat})")
                else:
                    self.intensity_name_label.setText("")
            else:
                self.intensity_name_label.setText("")
        except (ValueError, AttributeError):
            self.intensity_name_label.setText("")

    def _edit_selected_track_point(self):
        """Edit the selected track point."""
        row = self.points_list.currentRow()
        if row < 0 or row >= len(self._track_points):
            return

        point = self._track_points[row]
        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Track Point")
        dlg.setModal(True)
        dlg.resize(400, 280)

        layout = QFormLayout(dlg)

        lat_edit = QLineEdit()
        lat_edit.setText(str(point["lat"]))
        layout.addRow("Latitude:", lat_edit)

        lon_edit = QLineEdit()
        lon_edit.setText(str(point["lon"]))
        layout.addRow("Longitude:", lon_edit)

        dt_edit = QDateTimeEdit()
        dt_edit.setDateTime(QDateTime.fromString(point["datetime"], "yyyy-MM-dd HH:mm"))
        dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        layout.addRow("Date/Time:", dt_edit)

        intensity_edit = QLineEdit()
        if point.get("intensity") is not None:
            intensity_edit.setText(str(point["intensity"]))
        layout.addRow("Intensity (kt):", intensity_edit)

        intensity_cat_combo = QComboBox()
        intensity_cat_combo.addItems(["None", "LPA", "LOW", "TD", "TS", "STS", "TY", "STY", "Hurricane", "Major Hurricane"])
        current_cat = point.get("intensity_category")
        if current_cat:
            intensity_cat_combo.setCurrentText(current_cat)
        layout.addRow("Intensity Category:", intensity_cat_combo)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addRow(btns)

        if dlg.exec() == QDialog.Accepted:
            try:
                lat = float(lat_edit.text().strip())
                lon = float(lon_edit.text().strip())
                if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                    QMessageBox.warning(self, "Invalid Point", "Lat must be -90..90, Lon -180..180.")
                    return

                intensity_text = intensity_edit.text().strip()
                intensity = float(intensity_text) if intensity_text else None

                intensity_category = intensity_cat_combo.currentText()
                if intensity_category == "None":
                    intensity_category = None

                self._track_points[row] = {
                    "lat": lat, "lon": lon,
                    "datetime": dt_edit.dateTime().toString("yyyy-MM-dd HH:mm"),
                    "intensity": intensity,
                    "intensity_category": intensity_category
                }
                self._refresh_points_list()
            except ValueError:
                QMessageBox.warning(self, "Invalid Input", "Please enter valid numbers.")

    def _show_point_context_menu(self, pos):
        """Show context menu for track points."""
        item = self.points_list.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)
        edit_action = menu.addAction("Edit")
        delete_action = menu.addAction("Delete")

        action = menu.exec_(self.points_list.mapToGlobal(pos))
        if action == edit_action:
            row = self.points_list.row(item)
            self.points_list.setCurrentRow(row)
            self._edit_selected_track_point()
        elif action == delete_action:
            row = self.points_list.row(item)
            if 0 <= row < len(self._track_points):
                self._track_points.pop(row)
                self.points_list.takeItem(row)

    def _refresh_points_list(self):
        """Refresh the points list display."""
        self.points_list.clear()
        for p in self._track_points:
            lat = p.get("lat")
            lon = p.get("lon")
            dt = p.get("datetime", "")
            intensity = p.get("intensity")
            intensity_category = p.get("intensity_category")

            label = f"{lat:.2f}, {lon:.2f}  {dt}"
            if intensity is not None:
                cat = intensity_category or self.get_intensity_category(intensity)
                if cat:
                    label += f"  {intensity:.0f} kt ({cat})"
                else:
                    label += f"  {intensity:.0f} kt"
            elif intensity_category:
                label += f"  ({intensity_category})"
            self.points_list.addItem(label)

    def _add_track_point(self):
        try:
            lat = float(self.lat_edit.text().strip())
            lon = float(self.lon_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self, "Invalid Point", "Please enter valid numbers for Lat and Lon.")
            return
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            QMessageBox.warning(self, "Invalid Point", "Lat must be -90..90, Lon -180..180.")
            return

        dt = self.pt_datetime_edit.dateTime().toString("yyyy-MM-dd HH:mm")
        intensity_text = self.intensity_edit.text().strip()
        intensity = float(intensity_text) if intensity_text else None
        intensity_category = self.pt_intensity_cat_combo.currentText()
        if intensity_category == "None":
            intensity_category = None

        self._track_points.append({
            "lat": lat, "lon": lon,
            "datetime": dt, "intensity": intensity,
            "intensity_category": intensity_category
        })

        self._refresh_points_list()

        self.lat_edit.clear()
        self.lon_edit.clear()
        self.intensity_edit.clear()
        self.intensity_name_label.setText("")

    def _remove_selected_track_point_button(self):
        """Remove the currently selected track point (button handler)."""
        row = self.points_list.currentRow()
        if row >= 0 and row < len(self._track_points):
            self._track_points.pop(row)
            self._refresh_points_list()

    def _clear_track_points(self):
        self._track_points.clear()
        self.points_list.clear()

    def _order_points_by_time(self):
        """Sort track points by datetime."""
        def parse_dt(p):
            dt_str = p.get("datetime", "")
            if dt_str:
                try:
                    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    pass
            return datetime.min
        self._track_points.sort(key=parse_dt)
        self._refresh_points_list()

    def _remove_selected_track_point(self, item):
        """Remove selected point (context menu handler)."""
        row = self.points_list.row(item)
        if 0 <= row < len(self._track_points):
            self._track_points.pop(row)
            self._refresh_points_list()

    def _on_points_reordered(self, old_index, new_index):
        """Handle point reordering via drag and drop."""
        if 0 <= old_index < len(self._track_points) and 0 <= new_index < len(self._track_points):
            point = self._track_points.pop(old_index)
            self._track_points.insert(new_index, point)
            self._refresh_points_list()
            self.points_list.setCurrentRow(new_index)

    def get_data(self):
            return {
                "id": self._track_id or str(uuid.uuid4())[:8],
                "name": self.name_edit.text().strip() or None,
                "type": self.type_combo.currentText(),
                "year": self.year_spin.value(),
                "basin": self.basin_combo.currentText(),
                "notes": self.notes_edit.toPlainText().strip() or None,
                "color": self.color_btn.color(),
                "visible": True,
                "add_to_infobox": self.add_to_infobox_cb.isChecked(),
                "points": list(self._track_points)
            }



class ForecastDialog(QDialog):
    """Dialog for fetching and displaying weather forecast data from Windy API."""

    MODELS = ["gfs", "gfsWave", "arome", "iconEu", "namConus", "namHawaii", "namAlaska", "cams"]
    PARAMS = {
        "gfs":        ["temp", "dewpoint", "precip", "convPrecip", "snowPrecip", "wind", "windGust", "cape", "ptype", "lclouds", "mclouds", "hclouds", "rh", "gh", "pressure"],
        "gfsWave":    ["waves", "windWaves", "swell1", "swell2"],
        "arome":      ["temp", "dewpoint", "precip", "convPrecip", "wind", "windGust", "cape", "ptype", "lclouds", "mclouds", "hclouds", "rh"],
        "iconEu":     ["temp", "dewpoint", "precip", "convPrecip", "snowPrecip", "wind", "windGust", "cape", "ptype", "lclouds", "mclouds", "hclouds", "rh", "gh", "pressure"],
        "namConus":   ["temp", "dewpoint", "precip", "convPrecip", "snowPrecip", "wind", "windGust", "cape", "ptype", "lclouds", "mclouds", "hclouds", "rh", "gh", "pressure"],
        "namHawaii":  ["temp", "dewpoint", "precip", "convPrecip", "snowPrecip", "wind", "windGust", "cape", "ptype", "lclouds", "mclouds", "hclouds", "rh", "gh", "pressure"],
        "namAlaska":  ["temp", "dewpoint", "precip", "convPrecip", "snowPrecip", "wind", "windGust", "cape", "ptype", "lclouds", "mclouds", "hclouds", "rh", "gh", "pressure"],
        "cams":       ["so2sm", "dustsm", "cosc"],
    }
    LEVELS = ["surface", "800h", "300h"]

    def __init__(self, parent, lat, lon, api_key):
        super().__init__(parent)
        self.setWindowTitle("Weather Forecast")
        self.setModal(True)
        self.resize(700, 600)
        self._lat = lat
        self._lon = lon
        self._api_key = api_key

        layout = QVBoxLayout(self)

        loc_layout = QHBoxLayout()
        loc_layout.addWidget(QLabel(f"Lat: {lat:.4f}  Lon: {lon:.4f}"))
        loc_layout.addStretch()
        layout.addLayout(loc_layout)

        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(self.MODELS)
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(self.model_combo)
        model_row.addStretch()
        layout.addLayout(model_row)

        params_group = QGroupBox("Parameters")
        params_layout = QVBoxLayout(params_group)
        self._param_cbs = {}
        for p in self.PARAMS["gfs"]:
            cb = QCheckBox(p)
            cb.setChecked(p in ["wind", "dewpoint", "rh", "pressure"])
            self._param_cbs[p] = cb
            params_layout.addWidget(cb)
        self._params_scroll = QScrollArea()
        self._params_scroll.setWidgetResizable(True)
        self._params_scroll.setWidget(params_group)
        self._params_scroll.setFixedHeight(180)
        layout.addWidget(self._params_scroll)

        levels_group = QGroupBox("Levels")
        levels_layout = QHBoxLayout(levels_group)
        self._level_cbs = {}
        for lv in self.LEVELS:
            cb = QCheckBox(lv)
            cb.setChecked(lv in ["surface"])
            self._level_cbs[lv] = cb
            levels_layout.addWidget(cb)
        layout.addWidget(levels_group)

        self.fetch_btn = QPushButton("Fetch Forecast")
        self.fetch_btn.clicked.connect(self._fetch_forecast)
        layout.addWidget(self.fetch_btn)

        self.result_display = QTextEdit()
        self.result_display.setReadOnly(True)
        self.result_display.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        layout.addWidget(self.result_display, 1)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _on_model_changed(self, model):
        """Update parameter checkboxes based on selected model."""
        allowed = set(self.PARAMS.get(model, []))
        for p, cb in self._param_cbs.items():
            visible = p in allowed
            cb.setVisible(visible)
            if not visible:
                cb.setChecked(False)

    def _fetch_forecast(self):
        """Fetch forecast from Windy API."""
        import requests
        model = self.model_combo.currentText()
        params = [p for p, cb in self._param_cbs.items() if cb.isChecked()]
        levels = [lv for lv, cb in self._level_cbs.items() if cb.isChecked()]

        if not params:
            QMessageBox.warning(self, "Forecast", "Select at least one parameter.")
            return
        if not levels:
            QMessageBox.warning(self, "Forecast", "Select at least one level.")
            return

        self.result_display.setText("Fetching forecast data...")
        self.fetch_btn.setEnabled(False)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

        try:
            url = "https://api.windy.com/api/point-forecast/v2"
            payload = {
                "lat": self._lat,
                "lon": self._lon,
                "model": model,
                "parameters": params,
                "levels": levels,
                "key": self._api_key or "windyisthebest",
            }
            resp = requests.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                self._display_response(data)
            else:
                self.result_display.setText(f"Error: {resp.status_code}\n{resp.text}")
        except Exception as e:
            self.result_display.setText(f"Request failed: {e}")
        finally:
            self.fetch_btn.setEnabled(True)

    def _display_response(self, data):
        """Format and display the API response."""
        lines = []
        ts_list = data.get("ts", [])
        units = data.get("units", {})
        line_count = len(ts_list) if ts_list else 0

        lines.append(f"{'Timestamp':<22}")
        data_keys = [k for k in sorted(data.keys()) if k not in ("ts", "units", "warning")]
        for k in data_keys:
            unit = units.get(k, "")
            if unit:
                lines[-1] += f"{k:>24}"
            else:
                lines[-1] += f"{k:>18}"
        lines[-1] += "  (warning)"

        lines.append("-" * (22 + 26 * len(data_keys) + 14))

        for i in range(line_count):
            from datetime import datetime as dt
            ts_ms = ts_list[i]
            ts_str = dt.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d %H:%M")
            row = f"{ts_str:<22}"
            for k in data_keys:
                vals = data.get(k, [])
                if i < len(vals):
                    v = vals[i]
                    if isinstance(v, float):
                        row += f"{v:>24.4f}"
                    else:
                        row += f"{str(v):>18}"
                else:
                    row += f"{'N/A':>18}"
            warning = data.get("warning", "")
            if i == 0 and warning:
                row += f"  {warning[:40]}"
            lines.append(row)

        self.result_display.setText("\n".join(lines))


class ThemeDialog(QDialog):
    pass
    def __init__(self, parent, settings_mgr):
        super().__init__(parent)
        self.settings = settings_mgr
        self.setWindowTitle("Theme & Overlay Settings")
        self.setModal(True)
        self.resize(480, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        theme_box = QGroupBox("Application Theme")
        theme_layout = QVBoxLayout(theme_box)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        current_theme = self.settings.get("theme_name", "Dark (Default)")
        idx = self.theme_combo.findText(current_theme)
        self.theme_combo.setCurrentIndex(max(0, idx))
        theme_row.addWidget(self.theme_combo, 1)
        theme_layout.addLayout(theme_row)

        self.preview_label = QLabel("Preview: background / text / accent")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(32)
        self.preview_label.setStyleSheet("border-radius:4px; font-size:10px; padding:4px;")
        theme_layout.addWidget(self.preview_label)
        self.theme_combo.currentTextChanged.connect(self._update_preview)
        self._update_preview(self.theme_combo.currentText())
        layout.addWidget(theme_box)

        vp_box = QGroupBox("Viewport Background")
        vp_layout = QVBoxLayout(vp_box)
        self.viewport_bg_use_default_cb = QCheckBox("Use Theme Default")
        self.viewport_bg_use_default_cb.setChecked(self.settings.get("viewport_bg_use_theme_default", False))
        vp_layout.addWidget(self.viewport_bg_use_default_cb)
        vp_color_row = QHBoxLayout()
        vp_color_row.addWidget(QLabel("Color:"))
        self.viewport_bg_btn = ColorButton(self.settings.get("viewport_bg", "#000000"))
        self.viewport_bg_btn.colorChanged.connect(lambda c: None)
        vp_color_row.addWidget(self.viewport_bg_btn)
        vp_color_row.addStretch()
        vp_layout.addLayout(vp_color_row)
        layout.addWidget(vp_box)
        self.viewport_bg_use_default_cb.toggled.connect(self._on_viewport_bg_use_default_toggled)
        self._on_viewport_bg_use_default_toggled(self.viewport_bg_use_default_cb.isChecked())

        grid_box = QGroupBox("Grid Overlay")
        grid_layout = QGridLayout(grid_box)
        grid_layout.setColumnStretch(1, 1)

        grid_layout.addWidget(QLabel("Color:"), 0, 0)
        self.grid_color_btn = ColorButton(self.settings.get("grid_color", "#C8C8C8"))
        grid_layout.addWidget(self.grid_color_btn, 0, 1)

        grid_layout.addWidget(QLabel("Opacity (0-255):"), 1, 0)
        self.grid_opacity_spin = QSpinBox()
        self.grid_opacity_spin.setRange(0, 255)
        self.grid_opacity_spin.setValue(self.settings.get("grid_opacity", 160))
        grid_layout.addWidget(self.grid_opacity_spin, 1, 1)

        grid_layout.addWidget(QLabel("Line Width (px):"), 2, 0)
        self.grid_width_spin = QSpinBox()
        self.grid_width_spin.setRange(1, 10)
        self.grid_width_spin.setValue(self.settings.get("grid_line_width", 1))
        grid_layout.addWidget(self.grid_width_spin, 2, 1)

        grid_layout.addWidget(QLabel("Spacing (degrees):"), 3, 0)
        self.grid_spacing_spin = QSpinBox()
        self.grid_spacing_spin.setRange(1, 45)
        self.grid_spacing_spin.setValue(self.settings.get("grid_spacing_deg", 10))
        grid_layout.addWidget(self.grid_spacing_spin, 3, 1)

        grid_layout.addWidget(QLabel("Pattern:"), 4, 0)
        self.grid_pattern_combo = QComboBox()
        self.grid_pattern_combo.addItems(["solid", "dotted", "dashed", "dashdot", "crosshatch"])
        self.grid_pattern_combo.setCurrentText(self.settings.get("grid_pattern", "solid"))
        self.grid_pattern_combo.setToolTip("Solid=straight; dotted/dashed for clarity; crosshatch for dense meteo grids")
        grid_layout.addWidget(self.grid_pattern_combo, 4, 1)

        layout.addWidget(grid_box)

        coast_box = QGroupBox("Coastline Overlay")
        coast_layout = QGridLayout(coast_box)
        coast_layout.setColumnStretch(1, 1)

        coast_layout.addWidget(QLabel("Color:"), 0, 0)
        self.coast_color_btn = ColorButton(self.settings.get("coast_color", "#FFFFFF"))
        coast_layout.addWidget(self.coast_color_btn, 0, 1)

        coast_layout.addWidget(QLabel("Opacity (0-255):"), 1, 0)
        self.coast_opacity_spin = QSpinBox()
        self.coast_opacity_spin.setRange(0, 255)
        self.coast_opacity_spin.setValue(self.settings.get("coast_opacity", 200))
        coast_layout.addWidget(self.coast_opacity_spin, 1, 1)

        coast_layout.addWidget(QLabel("Line Width (px):"), 2, 0)
        self.coast_width_spin = QSpinBox()
        self.coast_width_spin.setRange(1, 10)
        self.coast_width_spin.setValue(self.settings.get("coast_line_width", 2))
        coast_layout.addWidget(self.coast_width_spin, 2, 1)

        coast_layout.addWidget(QLabel("Pattern:"), 3, 0)
        self.coast_pattern_combo = QComboBox()
        self.coast_pattern_combo.addItems(["solid", "dotted", "dashed", "dashdot", "crosshatch"])
        self.coast_pattern_combo.setCurrentText(self.settings.get("coast_pattern", "solid"))
        coast_layout.addWidget(self.coast_pattern_combo, 3, 1)

        layout.addWidget(coast_box)

        aor_box = QGroupBox("AoR (Area of Responsibility)")
        aor_layout = QGridLayout(aor_box)
        aor_layout.setColumnStretch(1, 1)

        aor_layout.addWidget(QLabel("Line Style:"), 0, 0)
        self.aor_pattern_combo = QComboBox()
        self.aor_pattern_combo.addItems(["dashed", "solid", "dotted", "dashdot", "crosshatch"])
        self.aor_pattern_combo.setCurrentText(self.settings.get("aor_pattern", "dashed"))
        aor_layout.addWidget(self.aor_pattern_combo, 0, 1)

        aor_items = [
            (1, "PAR (PAGASA AoR)", "par_color", "#00FF9F"),
            (2, "JMA AoR (Japan)", "jma_color", "#FFAA00"),
            (3, "TCAD (Advisory)", "tcad_color", "#FF6B6B"),
            (4, "TCID (Information)", "tcid_color", "#4ECDC4"),
            (5, "Manila FIR", "fir_color", "#FFE66D"),
            (6, "Custom AoR", "custom_aor_color", "#00E5FF"),
        ]
        self._aor_color_btns = {}
        for row, label, key, default in aor_items:
            aor_layout.addWidget(QLabel(label + ":"), row, 0)
            btn = ColorButton(self.settings.get(key, default))
            aor_layout.addWidget(btn, row, 1)
            self._aor_color_btns[key] = btn

        layout.addWidget(aor_box)

        anim_box = QGroupBox("Animation Playback Controls")
        anim_layout = QVBoxLayout(anim_box)
        self.anim_controls_cb = QCheckBox("Show Play/Pause/Speed/Scrub bar at bottom of viewport")
        self.anim_controls_cb.setChecked(self.settings.get("animation_controls_on_viewport", False))
        self.anim_controls_cb.setStyleSheet("font-size: 10px;")
        anim_layout.addWidget(self.anim_controls_cb)
        layout.addWidget(anim_box)

        layout.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply)
        btn_box.accepted.connect(self._save_and_close)
        btn_box.rejected.connect(self.reject)
        btn_box.button(QDialogButtonBox.Apply).clicked.connect(self._apply)
        layout.addWidget(btn_box)

    def _update_preview(self, name: str):
        t = THEMES.get(name, THEMES["Dark (Default)"])
        self.preview_label.setStyleSheet(
            f"background:{t['bg']}; color:{t['fg']}; border:2px solid {t['accent']};"
            f"border-radius:4px; font-size:10px; padding:4px;"
        )
        self.preview_label.setText(
            f"Background: {t['bg']}  .  Text: {t['fg']}  .  Accent: {t['accent']}"
        )

    def _on_viewport_bg_use_default_toggled(self, checked):
        self.viewport_bg_btn.setEnabled(not checked)

    def _apply(self):
        self.settings.set("theme_name", self.theme_combo.currentText())
        self.settings.set("grid_color",      self.grid_color_btn.color())
        self.settings.set("grid_opacity",    self.grid_opacity_spin.value())
        self.settings.set("grid_line_width", self.grid_width_spin.value())
        self.settings.set("grid_spacing_deg", self.grid_spacing_spin.value())
        self.settings.set("grid_pattern", self.grid_pattern_combo.currentText())
        if self.viewport_bg_use_default_cb.isChecked():
            t = THEMES.get(self.theme_combo.currentText(), THEMES["Dark (Default)"])
            self.settings.set("viewport_bg", t.get("viewport_bg", "#000000"))
        else:
            self.settings.set("viewport_bg", self.viewport_bg_btn.color())
        self.settings.set("viewport_bg_use_theme_default", self.viewport_bg_use_default_cb.isChecked())
        self.settings.set("coast_color",      self.coast_color_btn.color())
        self.settings.set("coast_opacity",    self.coast_opacity_spin.value())
        self.settings.set("coast_line_width", self.coast_width_spin.value())
        self.settings.set("coast_pattern", self.coast_pattern_combo.currentText())
        self.settings.set("aor_pattern", self.aor_pattern_combo.currentText())
        for key, btn in getattr(self, '_aor_color_btns', {}).items():
            self.settings.set(key, btn.color())
        self.settings.set("animation_controls_on_viewport", self.anim_controls_cb.isChecked())
        if self.parent():
            self.parent().apply_theme()
            self.parent().update_overlays()

    def _save_and_close(self):
        self._apply()
        self.accept()


class CustomAoRDialog(QDialog):
    """Dialog for editing a Custom AoR track's metadata and points."""
    def __init__(self, parent, track=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Custom AoR")
        self.setModal(True)
        self.resize(500, 500)
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.name_edit = QLineEdit()
        self.name_edit.setText(track.get("name", "Custom AoR") if track else "Custom AoR")
        form.addRow("Name:", self.name_edit)
        
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel("Color:"))
        self.color_btn = ColorButton(track.get("color", "#00E5FF") if track else "#00E5FF")
        color_row.addWidget(self.color_btn)
        color_row.addStretch()
        form.addRow(color_row)
        
        self.add_to_infobox_cb = QCheckBox("Add to Info Box")
        self.add_to_infobox_cb.setChecked(track.get("add_to_infobox", False) if track else False)
        form.addRow("Display:", self.add_to_infobox_cb)
        
        layout.addLayout(form)
        
        pts_group = QGroupBox("Points")
        pts_l = QVBoxLayout(pts_group)
        
        row = QHBoxLayout()
        row.addWidget(QLabel("Lat:"))
        self.lat_edit = QLineEdit()
        self.lat_edit.setFixedWidth(70)
        row.addWidget(self.lat_edit)
        row.addWidget(QLabel("Lon:"))
        self.lon_edit = QLineEdit()
        self.lon_edit.setFixedWidth(70)
        row.addWidget(self.lon_edit)
        row.addStretch()
        pts_l.addLayout(row)
        
        btn_row = QHBoxLayout()
        add_pt_btn = QPushButton("Add Point")
        add_pt_btn.clicked.connect(self._add_point)
        btn_row.addWidget(add_pt_btn)
        
        self.points_list = QListWidget()
        self.points_list.setFixedHeight(150)
        pts_l.addWidget(self.points_list)
        
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_point)
        btn_row.addWidget(remove_btn)
        pts_l.addLayout(btn_row)
        
        layout.addWidget(pts_group)
        
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
        
        self._points = [p.copy() for p in track.get("points", [])] if track else []
        self._refresh_list()

    def _add_point(self):
        try:
            lat = float(self.lat_edit.text().strip())
            lon = float(self.lon_edit.text().strip())
            self._points.append({"lat": lat, "lon": lon})
            self._refresh_list()
            self.lat_edit.clear()
            self.lon_edit.clear()
        except ValueError:
            pass

    def _remove_point(self):
        row = self.points_list.currentRow()
        if 0 <= row < len(self._points):
            self._points.pop(row)
            self._refresh_list()

    def _refresh_list(self):
        self.points_list.clear()
        for p in self._points:
            self.points_list.addItem(f"{p['lat']:.4f}, {p['lon']:.4f}")

    def get_data(self):
        return {
            "name": self.name_edit.text().strip(),
            "color": self.color_btn.color(),
            "add_to_infobox": self.add_to_infobox_cb.isChecked(),
            "points": self._points
        }


class ContourPlotDialog(QDialog):
    def __init__(self, data, title="Contour Plot", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 600)
        self.setModal(False)

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure
        import matplotlib
        matplotlib.use('QtAgg')

        self.fig = Figure(figsize=(7, 5.5), dpi=100)
        self.fig.patch.set_facecolor('#1a1a1a')
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setParent(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.canvas)

        self._plot(data)

    def _plot(self, data):
        import numpy as np
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor('#1a1a1a')
        ax.tick_params(colors='#ccc', labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#444')

        valid = data[np.isfinite(data)]
        if len(valid) < 10:
            ax.set_title("Not enough valid data", color='#ff6b6b', fontsize=10)
            self.canvas.draw()
            return

        vmin, vmax = np.percentile(valid, [1, 99])
        n_levels = 14
        levels = np.linspace(vmin, vmax, n_levels)

        cs = ax.contourf(data, levels=levels, cmap='viridis', extend='both')
        cont = ax.contour(data, levels=levels, colors='white', linewidths=0.4, alpha=0.5)
        ax.clabel(cont, inline=True, fontsize=7, colors='white', fmt='%.0f')

        cbar = self.fig.colorbar(cs, ax=ax, shrink=0.85)
        cbar.ax.tick_params(colors='#ccc', labelsize=7)
        cbar.outline.set_edgecolor('#444')

        ax.set_title("Contour Plot (Selected Region)", color='#ccc', fontsize=10, pad=6)
        ax.set_xlabel("Column (px)", color='#999', fontsize=8)
        ax.set_ylabel("Row (px)", color='#999', fontsize=8)
        self.fig.tight_layout()
        self.canvas.draw()


class ContourToolWindow(QWidget):
    def __init__(self, parent_app):
        super().__init__(parent_app, Qt.Window)
        self._app = parent_app
        self.setWindowTitle("Contour Tool")
        self.resize(700, 640)

        import matplotlib
        matplotlib.use('QtAgg')
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
        from matplotlib.figure import Figure

        self.fig = Figure(figsize=(7, 5.0), dpi=100)
        self.fig.patch.set_facecolor('#1a1a1a')
        self.canvas = FigureCanvas(self.fig)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- control panel ---
        ctrl = QVBoxLayout()
        ctrl.setSpacing(4)

        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Algorithm:"))
        self.algo_combo = QComboBox()
        self.algo_combo.addItems(["MonWatch-UI (Default)", "SATAID", "PWARDS"])
        self.algo_combo.model().item(2).setEnabled(False)
        self.algo_combo.model().item(2).setToolTip(
            "Typically used by the Pasacao Weather Atmospheric and Real-Time Data System")
        algo_row.addWidget(self.algo_combo, 1)
        ctrl.addLayout(algo_row)

        cl_row = QHBoxLayout()
        cl_row.addWidget(QLabel("Custom Levels:"))
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("e.g. -22.3, -20, -15, -10")
        cl_row.addWidget(self.custom_edit, 1)
        self.apply_btn = QPushButton("Apply")
        cl_row.addWidget(self.apply_btn)
        self.reset_btn = QPushButton("Reset")
        cl_row.addWidget(self.reset_btn)
        ctrl.addLayout(cl_row)

        smooth_row = QHBoxLayout()
        self.smooth_cb = QCheckBox("Gaussian Blur")
        smooth_row.addWidget(self.smooth_cb)
        smooth_row.addWidget(QLabel("Sigma:"))
        self.sigma_spin = QDoubleSpinBox()
        self.sigma_spin.setRange(0.1, 10.0)
        self.sigma_spin.setValue(1.0)
        self.sigma_spin.setSingleStep(0.1)
        self.sigma_spin.setEnabled(False)
        smooth_row.addWidget(self.sigma_spin)
        smooth_row.addStretch()
        ctrl.addLayout(smooth_row)

        overlay_row = QHBoxLayout()
        self.overlay_cb = QCheckBox("Display as overlay")
        self.overlay_cb.setStyleSheet("font-size:10px; color:#4CAF50;")
        overlay_row.addWidget(self.overlay_cb)
        overlay_row.addStretch()
        ctrl.addLayout(overlay_row)

        _ctrl_style = "font-size:10px; background:#2a2a2a; color:#ccc;"
        for w in [self.algo_combo, self.custom_edit, self.apply_btn,
                  self.reset_btn, self.smooth_cb, self.sigma_spin]:
            w.setStyleSheet(_ctrl_style)
        self.algo_combo.setStyleSheet("font-size:10px; color:#ccc;")

        layout.addLayout(ctrl)
        layout.addWidget(self.canvas)

        self._band_name = None
        self._last_region = None
        self._last_kwargs = None
        self._contour_line_segments = []
        self._overlay_col_start = 0
        self._overlay_row_start = 0
        self._draw_blank()

        self.algo_combo.currentTextChanged.connect(self._replot)
        self.apply_btn.clicked.connect(self._replot)
        self.reset_btn.clicked.connect(self._on_reset_custom)
        self.smooth_cb.toggled.connect(self._on_smooth_toggled)
        self.sigma_spin.valueChanged.connect(self._replot)
        self.overlay_cb.toggled.connect(self._on_overlay_toggled)

    def _on_smooth_toggled(self, checked):
        self.sigma_spin.setEnabled(checked)
        self._replot()

    def _on_reset_custom(self):
        self.custom_edit.clear()
        self._replot()

    def _on_overlay_toggled(self, checked):
        if checked and self._contour_line_segments:
            self._update_overlay()
        elif not checked:
            self._clear_overlay()

    def _update_overlay(self):
        if hasattr(self._app, 'set_contour_overlay') and self._contour_line_segments:
            self._app.set_contour_overlay(
                self._contour_line_segments,
                self._overlay_col_start, self._overlay_row_start,
            )

    def _clear_overlay(self):
        if hasattr(self._app, 'clear_contour_overlay'):
            self._app.clear_contour_overlay()

    def _draw_blank(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor('#1a1a1a')
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.text(0.5, 0.5, "Drag a rectangle on the main viewport",
                ha='center', va='center', color='#555', fontsize=14,
                transform=ax.transAxes)
        ax.text(0.5, 0.45, "to generate a contour plot here",
                ha='center', va='center', color='#444', fontsize=11,
                transform=ax.transAxes)
        ax.tick_params(colors='#444', labelsize=7)
        for spine in ax.spines.values():
            spine.set_color('#333')
        self.fig.tight_layout()
        self.canvas.draw()

    def _replot(self, *_args):
        if self._last_region is not None and self._last_kwargs is not None:
            self.plot_region(self._last_region, **dict(self._last_kwargs))

    def _get_custom_levels(self):
        import numpy as np
        text = self.custom_edit.text().strip()
        if text:
            try:
                parsed = [float(x.strip()) for x in text.split(",")]
                if len(parsed) > 1:
                    return np.array(sorted(parsed), dtype=float)
            except ValueError:
                pass
        return None

    @staticmethod
    def _get_sataid_steps(band_name):
        bn = (band_name or "").upper()
        if bn.startswith("VS") or bn.startswith("S1") or bn.startswith("S2"):
            return 0.05, 0.2, 0.02
        if bn.startswith("IR") or bn.startswith("WV") or bn.startswith("I2") or bn.startswith("I4"):
            return 1.0, 5.0, 0.5
        return 0.5, 2.0, 0.1

    def _compute_contour_levels(self, vmin, vmax, band_name=None):
        import numpy as np
        algo = self.algo_combo.currentText()
        custom = self._get_custom_levels()
        if custom is not None:
            return None, None, None, custom
        if algo == "SATAID":
            minor_step, major_step, half_step = self._get_sataid_steps(band_name)
        else:
            dr = vmax - vmin
            if dr > 100:
                minor_step, major_step = 10.0, 50.0
            elif dr > 40:
                minor_step, major_step = 5.0, 25.0
            elif dr > 10:
                minor_step, major_step = 1.0, 5.0
            elif dr > 4:
                minor_step, major_step = 0.5, 2.5
            elif dr > 1:
                minor_step, major_step = 0.2, 1.0
            else:
                minor_step, major_step = 0.1, 0.5
            half_step = minor_step / 2.0
        lo = np.floor(vmin / minor_step) * minor_step
        hi = np.ceil(vmax / minor_step) * minor_step
        n = int(round((hi - lo) / minor_step)) + 1
        all_lvls = np.round(lo + np.arange(n) * minor_step, decimals=6)
        idx_mask = np.abs(all_lvls % major_step) < 1e-6
        idx_lvls = all_lvls[idx_mask]
        int_lvls = all_lvls[~idx_mask]
        half_lvls = np.round(all_lvls[:-1] + half_step, decimals=6)
        half_lvls = half_lvls[(half_lvls > vmin) & (half_lvls < vmax)]
        return half_lvls, int_lvls, idx_lvls, None

    def plot_region(self, region, band_name, col_start, row_start, col_end, row_end,
                    crs=None, geotransform=None, coast_segments=None, satellite_lon=None,
                    settings=None, sataid_lat=None, sataid_lon=None,
                    source=None, datetime_str=None, start_latlon=None, end_latlon=None):
        import numpy as np
        from ..core.helpers import normalize_lon

        self._last_region = region
        self._last_kwargs = dict(
            band_name=band_name, col_start=col_start, row_start=row_start,
            col_end=col_end, row_end=row_end, crs=crs,
            geotransform=geotransform, coast_segments=coast_segments,
            satellite_lon=satellite_lon, settings=settings,
            sataid_lat=sataid_lat, sataid_lon=sataid_lon,
            source=source, datetime_str=datetime_str,
            start_latlon=start_latlon, end_latlon=end_latlon,
        )

        self._band_name = band_name
        self._contour_line_segments.clear()
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.set_facecolor('#1a1a1a')
        ax.tick_params(colors='#ccc', labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#444')

        rh, rw = region.shape
        modern = settings.get("modern_contour", False) if settings else False

        # optional smoothing
        if self.smooth_cb.isChecked():
            from scipy.ndimage import gaussian_filter
            region = gaussian_filter(region.astype(np.float64), self.sigma_spin.value())

        # Kelvin -> Celsius for IR/WV bands
        bn = (band_name or "").upper()
        if bn.startswith("IR") or bn.startswith("WV") or bn.startswith("I2") or bn.startswith("I4"):
            valid_check = region[np.isfinite(region)]
            if len(valid_check) > 0 and float(np.mean(valid_check)) > 100:
                region = region.astype(np.float64) - 273.15

        valid = region[np.isfinite(region)]
        if len(valid) > 10:
            vmin, vmax = np.percentile(valid, [1, 99])

            from matplotlib.collections import LineCollection

            self._contour_line_segments = []

            def _extract_mpl_contours(level_list):
                if not level_list:
                    return {}
                _dedup = []
                _seen = set()
                for lv in level_list:
                    k = round(lv, 10)
                    if k not in _seen:
                        _seen.add(k)
                        _dedup.append(lv)
                _cs = ax.contour(region, levels=_dedup, linewidths=0)
                result = dict(zip(_cs.levels, _cs.allsegs))
                for coll in _cs.collections:
                    coll.remove()
                return result

            def _label_line(line, val, color, fs=7, fw='normal'):
                mid = len(line) // 2
                ax.text(line[mid, 0], line[mid, 1], f'{val:.1f}' if abs(val) < 100 else f'{val:.0f}',
                        fontsize=fs, color=color, ha='center', va='center', fontweight=fw)

            if modern:
                custom = self._get_custom_levels()
                if custom is not None:
                    levels = custom
                else:
                    levels = np.linspace(vmin, vmax, 14)
                cs = ax.contourf(region, levels=levels, cmap='viridis', extend='both')
                _all_lines = []
                _contour_segs = _extract_mpl_contours(list(levels))
                for lv in levels:
                    for seg in _contour_segs.get(lv, []):
                        if len(seg) >= 2:
                            _all_lines.append(seg)
                            _label_line(seg, lv, 'white')
                if _all_lines:
                    lc = LineCollection(_all_lines, colors='white', linewidths=0.4, alpha=0.5)
                    ax.add_collection(lc)
                self._contour_line_segments.extend(_all_lines)
                cbar = self.fig.colorbar(cs, ax=ax, shrink=0.85)
                cbar.ax.tick_params(colors='#ccc', labelsize=7)
                cbar.outline.set_edgecolor('#444')
            else:
                half_lvls, int_lvls, idx_lvls, custom = self._compute_contour_levels(vmin, vmax, band_name)
                if custom is not None:
                    _contour_segs = _extract_mpl_contours(list(custom))
                    _lines = []
                    for lv in custom:
                        for seg in _contour_segs.get(lv, []):
                            if len(seg) >= 2:
                                _lines.append(seg)
                                _label_line(seg, lv, 'red', fw='bold')
                    if _lines:
                        lc = LineCollection(_lines, colors='red', linewidths=2.0, alpha=0.9)
                        ax.add_collection(lc)
                    self._contour_line_segments.extend(_lines)
                else:
                    _all_levels = []
                    if half_lvls is not None and len(half_lvls) > 0:
                        _all_levels.extend(half_lvls)
                    if int_lvls is not None and len(int_lvls) > 0:
                        _all_levels.extend(int_lvls)
                    if idx_lvls is not None and len(idx_lvls) > 0:
                        _all_levels.extend(idx_lvls)
                    _contour_segs = _extract_mpl_contours(_all_levels)
                    if half_lvls is not None and len(half_lvls) > 0:
                        _half = []
                        for lv in half_lvls:
                            for seg in _contour_segs.get(lv, []):
                                if len(seg) >= 2:
                                    _half.append(seg)
                        if _half:
                            lc = LineCollection(_half, colors='red', linewidths=0.8, alpha=0.4, linestyles='dashed')
                            ax.add_collection(lc)
                        self._contour_line_segments.extend(_half)
                    if int_lvls is not None and len(int_lvls) > 0:
                        _int = []
                        for lv in int_lvls:
                            for seg in _contour_segs.get(lv, []):
                                if len(seg) >= 2:
                                    _int.append(seg)
                        if _int:
                            lc = LineCollection(_int, colors='red', linewidths=1.0, alpha=0.5)
                            ax.add_collection(lc)
                        self._contour_line_segments.extend(_int)
                    if idx_lvls is not None and len(idx_lvls) > 0:
                        _idx = []
                        for lv in idx_lvls:
                            for seg in _contour_segs.get(lv, []):
                                if len(seg) >= 2:
                                    _idx.append(seg)
                                    _label_line(seg, lv, 'red', fw='bold')
                        if _idx:
                            lc = LineCollection(_idx, colors='red', linewidths=2.0, alpha=0.9)
                            ax.add_collection(lc)
                        self._contour_line_segments.extend(_idx)
        else:
            ax.text(0.5, 0.5, "Not enough valid data in region",
                    ha='center', va='center', color='#ff6b6b', fontsize=12,
                    transform=ax.transAxes)

        self._overlay_col_start = col_start
        self._overlay_row_start = row_start

        # --- overlay grid & coastline on contour ---
        if settings is not None and (geotransform is not None or (sataid_lat is not None and sataid_lon is not None)):
            # ---- choose coordinate mapper ----
            if sataid_lat is not None and sataid_lon is not None:
                _sataid_map = True
                if sataid_lat.ndim == 2:
                    _lat_s = sataid_lat[row_start:row_end, col_start:col_end]
                    _lon_s = sataid_lon[row_start:row_end, col_start:col_end]
                else:
                    _lat_s = sataid_lat[row_start:row_end]
                    _lon_s = sataid_lon[col_start:col_end]
                lat_min_g, lat_max_g = float(_lat_s.min()), float(_lat_s.max())
                lon_min_g, lon_max_g = float(_lon_s.min()), float(_lon_s.max())
                _lr = lon_max_g - lon_min_g
                _lar = lat_max_g - lat_min_g

                def _ll2col(lon, lat):
                    c = (lon - lon_min_g) / _lr * rw if _lr > 0 else 0
                    r = (lat_max_g - lat) / _lar * rh if _lar > 0 else 0
                    return c, r
            else:
                _sataid_map = False
                from pyproj import Transformer
                _transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
                _inv_xform = ~geotransform
                _h_sat = 35785863.0
                if hasattr(crs, 'to_cf'):
                    try:
                        _cf = crs.to_cf()
                        _h_sat = float(_cf.get("perspective_point_height", _h_sat))
                    except Exception:
                        pass
                _disk = abs(geotransform.c) if geotransform.c != 0 else 1.3e7

                def _ll2col(lon, lat):
                    lon = normalize_lon(lon)
                    xp, yp = _transformer.transform(lon, lat)
                    if np.isinf(xp) or np.isinf(yp) or np.isnan(xp) or np.isnan(yp):
                        return None, None
                    if abs(xp) > _disk * 1.01 or abs(yp) > _disk * 1.01:
                        return None, None
                    c, r = _inv_xform * (xp, yp)
                    return c - col_start, r - row_start

                # compute region geographic bounds from its 4 corners
                _xc = [geotransform * (col_start + c, row_start + r)
                       for c, r in [(0, 0), (rw - 1, 0), (0, rh - 1), (rw - 1, rh - 1)]]
                _inv_t = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                _ll = [_inv_t.transform(x, y) for x, y in _xc]
                lon_min_g, lon_max_g = min(p[0] for p in _ll), max(p[0] for p in _ll)
                lat_min_g, lat_max_g = min(p[1] for p in _ll), max(p[1] for p in _ll)

            # ---- grid lines ----
            if _sataid_map:
                gc = settings.get("sataid_grid_color", "#4488FF")
            else:
                gc = settings.get("grid_color", "#C8C8C8")
            go = settings.get("grid_opacity", 160)
            gw = settings.get("grid_line_width", 1)
            gspacing = settings.get("grid_spacing_deg", 10)
            g_alpha = max(0, min(1, go / 255.0))
            gl_kw = dict(color=gc, alpha=g_alpha, linewidth=gw, zorder=5)

            lon_s = np.ceil(lon_min_g / gspacing) * gspacing
            lon_v = lon_s
            while lon_v <= lon_max_g:
                pts = []
                for lt in np.linspace(max(lat_min_g, -89.9), min(lat_max_g, 89.9), 200):
                    px, py = _ll2col(lon_v, lt)
                    if px is not None and 0 <= px < rw and 0 <= py < rh:
                        pts.append((px, py))
                if len(pts) > 1:
                    ax.plot(*zip(*pts), **gl_kw)
                lon_v += gspacing

            lat_s = np.ceil(lat_min_g / gspacing) * gspacing
            lat_v = lat_s
            while lat_v <= lat_max_g:
                pts = []
                for ln in np.linspace(lon_min_g, lon_max_g, 400):
                    px, py = _ll2col(ln, lat_v)
                    if px is not None and 0 <= px < rw and 0 <= py < rh:
                        pts.append((px, py))
                if len(pts) > 1:
                    ax.plot(*zip(*pts), **gl_kw)
                lat_v += gspacing

            # ---- coastline ----
            if coast_segments:
                cc = settings.get("coast_color", "#FFFFFF")
                co = settings.get("coast_opacity", 200)
                cw = settings.get("coast_line_width", 2)
                c_alpha = max(0, min(1, co / 255.0))
                cl_kw = dict(color=cc, alpha=c_alpha, linewidth=cw, zorder=6)

                for seg in coast_segments:
                    pts = []
                    for lon_p, lat_p in seg:
                        px, py = _ll2col(lon_p, lat_p)
                        if px is not None and 0 <= px < rw and 0 <= py < rh:
                            pts.append((px, py))
                        else:
                            if len(pts) > 1:
                                ax.plot(*zip(*pts), **cl_kw)
                            pts = []
                    if len(pts) > 1:
                        ax.plot(*zip(*pts), **cl_kw)

        ax.set_xlim(0, rw)
        ax.set_ylim(rh, 0)
        title_parts = ["Contour"]
        if source:
            title_parts.append(source)
        if band_name:
            title_parts.append(band_name)
        if start_latlon and end_latlon:
            title_parts.append(f"{start_latlon}-{end_latlon}")
        if datetime_str:
            title_parts.append(datetime_str)
        ax.set_title(
            " -- ".join(title_parts),
            color='#ccc', fontsize=10, pad=6
        )
        self.fig.tight_layout()
        self.canvas.draw()
        if self.overlay_cb.isChecked():
            self._update_overlay()


class CombinedForecastDialog(QDialog):
    LAYOUT_OPTIONS = [
        ("Default (Settings)", ""),
        ("MonWatch-UI", "monwatch"),
        ("PAGASA", "pagasa"),
        ("JTWC", "jtwc"),
        ("JMA", "jma"),
        ("NHC (Tropycal)", "nhc"),
        ("Develope", "develope"),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Combined Forecast")
        self.setModal(True)
        self.resize(500, 420)

        self._main_ui = parent
        self._track_rows = []
        self._max_tracks = 5
        self._available_tracks = self._collect_tracks()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        header = QLabel("Select up to 5 storm tracks to combine into a single forecast map")
        header.setStyleSheet("color: #aaa; font-size: 10px;")
        header.setWordWrap(True)
        layout.addWidget(header)

        self._tracks_container = QWidget()
        self._tracks_layout = QVBoxLayout(self._tracks_container)
        self._tracks_layout.setContentsMargins(0, 0, 0, 0)
        self._tracks_layout.setSpacing(6)
        layout.addWidget(self._tracks_container)

        add_btn = QPushButton("+ Add Track")
        add_btn.setStyleSheet("QPushButton { background: #1e3a5f; color: #90d5ff; font-weight: bold; padding: 4px 12px; } QPushButton:hover { background: #2a4a7f; }")
        add_btn.clicked.connect(self._add_track_row)
        layout.addWidget(add_btn)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Forecast Style:"))
        self._style_combo = QComboBox()
        for label, val in self.LAYOUT_OPTIONS:
            self._style_combo.addItem(label, val)
        
        # Set default based on settings
        current_layout = self._main_ui.settings.get("forecast_preferences", {}).get("forecast_layout", "default")
        idx = self._style_combo.findData(current_layout)
        self._style_combo.setCurrentIndex(max(0, idx))
        
        style_row.addWidget(self._style_combo, 1)
        layout.addLayout(style_row)

        algo_row = QHBoxLayout()
        algo_row.addWidget(QLabel("Label Algorithm:"))
        self._algo_combo = QComboBox()
        self._algo_combo.addItem("Polar (Zigzag)", "polar")
        self._algo_combo.addItem("Zigzag", "zigzag")
        self._algo_combo.addItem("Bezier", "bezier")
        self._algo_combo.addItem("Smart Bezier", "smart_bezier")
        self._algo_combo.addItem("Anti-Clima", "anticlima")
        self._algo_combo.addItem("Anti-Clima V2", "anticlima_v2")
        self._algo_combo.addItem("Greedy", "greedy")
        self._algo_combo.addItem("Offset", "offset")
        self._algo_combo.addItem("Force", "force")
        self._algo_combo.addItem("Anneal", "anneal")
        self._algo_combo.addItem("MILP", "milp")
        self._algo_combo.addItem("Railway Bezier", "railway_bezier")
        self._algo_combo.addItem("8-Direction", "8direction")
        self._algo_combo.addItem("Staggered Perp", "staggered_perp")
        self._algo_combo.addItem("Auto", "auto")
        saved_algo = self._main_ui.settings.get("labeling_method", "polar") if hasattr(self._main_ui, 'settings') else "polar"
        algo_idx = max(0, self._algo_combo.findData(saved_algo))
        self._algo_combo.setCurrentIndex(algo_idx)
        algo_row.addWidget(self._algo_combo, 1)
        layout.addLayout(algo_row)

        cone_row = QHBoxLayout()
        cone_row.addWidget(QLabel("Cone Method:"))
        self._cone_combo = QComboBox()
        self._cone_combo.addItem("Smooth", "smooth")
        self._cone_combo.addItem("Union", "union")
        saved_cone = self._main_ui.settings.get("cone_method", "smooth") if hasattr(self._main_ui, 'settings') else "smooth"
        cone_idx = max(0, self._cone_combo.findData(saved_cone))
        self._cone_combo.setCurrentIndex(cone_idx)
        cone_row.addWidget(self._cone_combo, 1)
        layout.addLayout(cone_row)

        res_row = QHBoxLayout()
        res_row.addWidget(QLabel("Resolution:"))
        self._res_combo = QComboBox()
        self._res_combo.addItem("110m", "110m")
        self._res_combo.addItem("50m", "50m")
        self._res_combo.addItem("10m", "10m")
        self._res_combo.setCurrentIndex(2)
        res_row.addWidget(self._res_combo, 1)
        layout.addLayout(res_row)

        label_row = QHBoxLayout()
        self._show_labels_cb = QCheckBox("Show Labels")
        self._show_labels_cb.setChecked(True)
        self._show_labels_cb.setToolTip("Enable/disable track point labels. Disable to improve performance.")
        label_row.addWidget(self._show_labels_cb)
        label_row.addStretch()
        layout.addLayout(label_row)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Title:"))
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Combined Forecast")
        title_row.addWidget(self._title_edit, 1)
        layout.addLayout(title_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        generate_btn = QPushButton("Generate")
        generate_btn.setStyleSheet("QPushButton { background: #4CAF50; color: white; font-weight: bold; padding: 6px 20px; border-radius: 4px; } QPushButton:hover { background: #5CBF60; }")
        generate_btn.clicked.connect(self._on_generate)
        btn_row.addWidget(generate_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._add_track_row()

    def _collect_tracks(self):
        tracks = []
        seen = set()
        mu = self._main_ui
        for t in getattr(mu, 'tracks', []):
            tid = t.get("id", "")
            name = t.get("name", tid)
            if tid and tid not in seen:
                tracks.append((tid, f"{name} ({tid})"))
                seen.add(tid)
        tracks.sort(key=lambda x: x[1].lower())
        return tracks

    def _add_track_row(self, checked=False):
        if len(self._track_rows) >= self._max_tracks:
            return
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(4)

        combo = QComboBox()
        combo.addItem("-- Select Track --", None)
        for tid, display in self._available_tracks:
            combo.addItem(display, tid)
        row_layout.addWidget(combo, 1)

        remove_btn = QPushButton("x")
        remove_btn.setFixedWidth(24)
        remove_btn.setStyleSheet("QPushButton { background: #5f1e1e; color: #ff9090; font-weight: bold; } QPushButton:hover { background: #7f2a2a; }")
        remove_btn.clicked.connect(lambda checked=False, r=row: self._remove_track_row(r))
        row_layout.addWidget(remove_btn)

        self._tracks_layout.addWidget(row)
        self._track_rows.append(row)

    def _remove_track_row(self, row):
        if row in self._track_rows:
            self._track_rows.remove(row)
            row.deleteLater()

    def _get_selected_tracks(self):
        selected = []
        for row in self._track_rows:
            combo = row.findChild(QComboBox)
            if combo:
                tid = combo.currentData()
                if tid:
                    selected.append(tid)
        return selected

    def _on_generate(self):
        tracks = self._get_selected_tracks()
        if len(tracks) < 1:
            QMessageBox.warning(self, "No Tracks", "Select at least one track.")
            return
        style_idx = self._style_combo.currentIndex()
        layout_key = self.LAYOUT_OPTIONS[style_idx][1] if style_idx > 0 else None
        algorithm = self._algo_combo.currentData()
        cone_method = self._cone_combo.currentData()
        custom_title = self._title_edit.text().strip()
        resolution = self._res_combo.currentData()
        show_labels = self._show_labels_cb.isChecked()
        self.accept()
        mu = self._main_ui
        if hasattr(mu, '_launch_combined_forecast'):
            mu._launch_combined_forecast(tracks, layout_key, algorithm, cone_method, custom_title, resolution, show_labels)


class CachingDialog(QDialog):
    def __init__(self, parent=None, title="Caching Bands", main_text="PLEASE WAIT, CACHING BANDS FOR GOOD PERFORMANCE"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumSize(460, 320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        self.title_label = QLabel(main_text)
        self.title_label.setStyleSheet("font-weight:bold; color:#FFF; font-size:12px;")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setMinimumHeight(22)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("Initializing...")
        self.status_label.setStyleSheet("color:#DDD; font-size:11px; font-family: Consolas, monospace;")
        self.status_label.setWordWrap(False)
        self.status_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.status_label.setMinimumWidth(420)
        self.status_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self.status_label, 1)
        self._last_text = ""
        self._last_pct = -1

    def update_progress(self, text, pct):
        if text != self._last_text:
            self.status_label.setText(text)
            self._last_text = text
        if pct != self._last_pct:
            self.progress_bar.setValue(pct)
            self._last_pct = pct
        self.status_label.repaint()
        self.progress_bar.repaint()
        QApplication.processEvents(QEventLoop.AllEvents, 30)

    def set_final(self, text):
        self.status_label.setText(text)
        self.progress_bar.setValue(100)
        self.status_label.repaint()
        self.progress_bar.repaint()
        QApplication.processEvents(QEventLoop.AllEvents, 30)

    def setLabelText(self, text):
        if text != self._last_text:
            self.status_label.setText(text)
            self._last_text = text
            self.status_label.repaint()
            QApplication.processEvents(QEventLoop.AllEvents, 30)


class AnimationExportDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Export Animation")
        self.setModal(True)
        self.resize(400, 380)
        layout = QVBoxLayout(self)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.name_edit = QLineEdit(f"animation_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        name_row.addWidget(self.name_edit)
        layout.addLayout(name_row)

        fps_row = QHBoxLayout()
        fps_row.addWidget(QLabel("Speed (FPS):"))
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 60)
        self.fps_spin.setValue(parent.settings.get("animation_fps", 5) if hasattr(parent, 'settings') else 5)
        fps_row.addWidget(self.fps_spin)
        fps_row.addStretch()
        layout.addLayout(fps_row)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["MP4", "GIF", "AVI"])
        self.fmt_combo.currentTextChanged.connect(self._on_format_changed)
        fmt_row.addWidget(self.fmt_combo)
        fmt_row.addStretch()
        layout.addLayout(fmt_row)

        self.cb_full_disk = QCheckBox("Full Disk (uncheck to use current viewport)")
        self.cb_full_disk.setChecked(parent.settings.get("animation_export_use_full_disk", True) if hasattr(parent, 'settings') else True)
        layout.addWidget(self.cb_full_disk)

        footer_group = QGroupBox("Footer Settings")
        footer_layout = QVBoxLayout(footer_group)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Footer Bar Size:"))
        self.footer_size_combo = QComboBox()
        self.footer_size_combo.addItems(["small", "normal", "big"])
        fsize = parent.settings.get("footer_size", "small") if hasattr(parent, 'settings') else "small"
        self.footer_size_combo.setCurrentText(fsize)
        size_row.addWidget(self.footer_size_combo)
        size_row.addStretch()
        footer_layout.addLayout(size_row)

        txt_row = QHBoxLayout()
        txt_row.addWidget(QLabel("Text Size:"))
        self.text_size_combo = QComboBox()
        self.text_size_combo.addItems(["small", "normal", "large"])
        tsize = parent.settings.get("text_size", "normal") if hasattr(parent, 'settings') else "normal"
        self.text_size_combo.setCurrentText(tsize)
        txt_row.addWidget(self.text_size_combo)
        txt_row.addStretch()
        footer_layout.addLayout(txt_row)

        show = parent.settings.get("footer_show", {}) if hasattr(parent, 'settings') else {}
        self.cb_sat = QCheckBox("Satellite name")
        self.cb_sat.setChecked(show.get("satellite", True))
        footer_layout.addWidget(self.cb_sat)
        self.cb_dt = QCheckBox("Date & Time")
        self.cb_dt.setChecked(show.get("datetime", True))
        footer_layout.addWidget(self.cb_dt)
        self.cb_band = QCheckBox("Band / Composition")
        self.cb_band.setChecked(show.get("band", True))
        footer_layout.addWidget(self.cb_band)
        self.cb_latlon = QCheckBox("Lat / Lon")
        self.cb_latlon.setChecked(show.get("latlon", True))
        footer_layout.addWidget(self.cb_latlon)

        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Position:"))
        self.footer_pos_combo = QComboBox()
        self.footer_pos_combo.addItems(["bottom", "top"])
        self.footer_pos_combo.setCurrentText(parent.settings.get("footer_position", "bottom") if hasattr(parent, 'settings') else "bottom")
        pos_row.addWidget(self.footer_pos_combo)
        pos_row.addStretch()
        footer_layout.addLayout(pos_row)

        infopos_row = QHBoxLayout()
        infopos_row.addWidget(QLabel("Info Position:"))
        self.footer_infopos_combo = QComboBox()
        self.footer_infopos_combo.addItems(["left", "right"])
        self.footer_infopos_combo.setCurrentText(parent.settings.get("footer_info_position", "left") if hasattr(parent, 'settings') else "left")
        infopos_row.addWidget(self.footer_infopos_combo)
        infopos_row.addStretch()
        footer_layout.addLayout(infopos_row)

        layout.addWidget(footer_group)

        est_group = QGroupBox("Estimate")
        est_layout = QVBoxLayout(est_group)
        self.est_size_label = QLabel("Estimated size: ---")
        est_layout.addWidget(self.est_size_label)
        self.est_time_label = QLabel("Estimated time: ---")
        est_layout.addWidget(self.est_time_label)

        frames = getattr(parent, "_anim_frames", []) if hasattr(parent, "_anim_frames") else []
        valid = [f for f in frames if f is not None]
        if valid:
            h, w = valid[0].shape[:2]
            n = len(valid)
            raw_bytes = w * h * 3 * n
            fmt = self.fmt_combo.currentText()
            comp_ratio = {"MP4": 30, "GIF": 5, "AVI": 8}.get(fmt, 30)
            est_mb = raw_bytes / comp_ratio / 1024 / 1024
            self.est_size_label.setText(f"Estimated size: {est_mb:.1f} MB")
            est_sec = n / (self.fps_spin.value() or 1) * 2
            self.est_time_label.setText(f"Estimated time: {est_sec:.0f}s")

        def update_est():
            if valid:
                h, w = valid[0].shape[:2]
                n = len(valid)
                raw_bytes = w * h * 3 * n
                fmt = self.fmt_combo.currentText()
                cr = {"MP4": 10, "GIF": 2, "AVI": 4}.get(fmt, 10)
                est_mb = raw_bytes / cr / 1024 / 1024
                self.est_size_label.setText(f"Estimated size: {est_mb:.1f} MB")
                fps_v = self.fps_spin.value() or 1
                est_sec = n / fps_v * 2
                self.est_time_label.setText(f"Estimated time: {est_sec:.0f}s")

        self.fps_spin.valueChanged.connect(update_est)
        self.fmt_combo.currentTextChanged.connect(update_est)
        layout.addWidget(est_group)

        btn_row = QHBoxLayout()
        export_btn = QPushButton("Export")
        export_btn.setStyleSheet("QPushButton { background: #FF9800; color: white; font-weight: bold; padding: 6px 20px; border-radius: 3px; }")
        export_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(export_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_format_changed(self, fmt):
        if fmt == "GIF":
            self.fps_spin.setEnabled(False)
            self.fps_spin.setValue(5)
        else:
            self.fps_spin.setEnabled(True)


class FlatProjectionOptionsDialog(QDialog):
    """Dialog for configuring grid/coastline overlays on flat-projection exports.

    Defaults are read from the settings manager so each value matches the
    user's configured grid/coastline style. Accepted choices are written back
    to settings so the defaults stay in sync with the last selection.
    """

    def __init__(self, parent, settings_mgr):
        super().__init__(parent)
        self.settings = settings_mgr
        self.setWindowTitle("Flat Projection Options")
        self.setModal(True)
        self.resize(460, 560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.show_grid_cb = QCheckBox("Show Grid")
        self.show_grid_cb.setChecked(self.settings.get("grid_enabled", False))
        layout.addWidget(self.show_grid_cb)

        grid_box = QGroupBox("Grid Overlay")
        grid_layout = QGridLayout(grid_box)
        grid_layout.setColumnStretch(1, 1)

        grid_layout.addWidget(QLabel("Color:"), 0, 0)
        self.grid_color_btn = ColorButton(self.settings.get("grid_color", "#C8C8C8"))
        grid_layout.addWidget(self.grid_color_btn, 0, 1)

        grid_layout.addWidget(QLabel("Opacity (0-255):"), 1, 0)
        self.grid_opacity_spin = QSpinBox()
        self.grid_opacity_spin.setRange(0, 255)
        self.grid_opacity_spin.setValue(self.settings.get("grid_opacity", 160))
        grid_layout.addWidget(self.grid_opacity_spin, 1, 1)

        grid_layout.addWidget(QLabel("Line Width (px):"), 2, 0)
        self.grid_width_spin = QSpinBox()
        self.grid_width_spin.setRange(1, 10)
        self.grid_width_spin.setValue(self.settings.get("grid_line_width", 1))
        grid_layout.addWidget(self.grid_width_spin, 2, 1)

        grid_layout.addWidget(QLabel("Spacing (degrees):"), 3, 0)
        self.grid_spacing_spin = QSpinBox()
        self.grid_spacing_spin.setRange(1, 45)
        self.grid_spacing_spin.setValue(self.settings.get("grid_spacing_deg", 10))
        grid_layout.addWidget(self.grid_spacing_spin, 3, 1)

        grid_layout.addWidget(QLabel("Pattern:"), 4, 0)
        self.grid_pattern_combo = QComboBox()
        self.grid_pattern_combo.addItems(["solid", "dotted", "dashed", "dashdot", "crosshatch"])
        self.grid_pattern_combo.setCurrentText(self.settings.get("grid_pattern", "solid"))
        grid_layout.addWidget(self.grid_pattern_combo, 4, 1)

        self.show_labels_cb = QCheckBox("Show edge degree labels")
        self.show_labels_cb.setChecked(self.settings.get("flat_grid_labels", False))
        grid_layout.addWidget(self.show_labels_cb, 5, 0, 1, 2)

        layout.addWidget(grid_box)

        self.show_coast_cb = QCheckBox("Show Coastlines")
        self.show_coast_cb.setChecked(self.settings.get("coast_enabled", False))
        layout.addWidget(self.show_coast_cb)

        coast_box = QGroupBox("Coastline Overlay")
        coast_layout = QGridLayout(coast_box)
        coast_layout.setColumnStretch(1, 1)

        coast_layout.addWidget(QLabel("Color:"), 0, 0)
        self.coast_color_btn = ColorButton(self.settings.get("coast_color", "#FFFFFF"))
        coast_layout.addWidget(self.coast_color_btn, 0, 1)

        coast_layout.addWidget(QLabel("Opacity (0-255):"), 1, 0)
        self.coast_opacity_spin = QSpinBox()
        self.coast_opacity_spin.setRange(0, 255)
        self.coast_opacity_spin.setValue(self.settings.get("coast_opacity", 200))
        coast_layout.addWidget(self.coast_opacity_spin, 1, 1)

        coast_layout.addWidget(QLabel("Line Width (px):"), 2, 0)
        self.coast_width_spin = QSpinBox()
        self.coast_width_spin.setRange(1, 10)
        self.coast_width_spin.setValue(self.settings.get("coast_line_width", 2))
        coast_layout.addWidget(self.coast_width_spin, 2, 1)

        coast_layout.addWidget(QLabel("Pattern:"), 3, 0)
        self.coast_pattern_combo = QComboBox()
        self.coast_pattern_combo.addItems(["solid", "dotted", "dashed", "dashdot", "crosshatch"])
        self.coast_pattern_combo.setCurrentText(self.settings.get("coast_pattern", "solid"))
        coast_layout.addWidget(self.coast_pattern_combo, 3, 1)

        layout.addWidget(coast_box)

        layout.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self._accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _accept(self):
        self.settings.set("grid_enabled", self.show_grid_cb.isChecked())
        self.settings.set("coast_enabled", self.show_coast_cb.isChecked())
        self.settings.set("grid_color", self.grid_color_btn.color())
        self.settings.set("grid_opacity", self.grid_opacity_spin.value())
        self.settings.set("grid_line_width", self.grid_width_spin.value())
        self.settings.set("grid_spacing_deg", self.grid_spacing_spin.value())
        self.settings.set("grid_pattern", self.grid_pattern_combo.currentText())
        self.settings.set("coast_color", self.coast_color_btn.color())
        self.settings.set("coast_opacity", self.coast_opacity_spin.value())
        self.settings.set("coast_line_width", self.coast_width_spin.value())
        self.settings.set("coast_pattern", self.coast_pattern_combo.currentText())
        self.settings.set("flat_grid_labels", self.show_labels_cb.isChecked())
        self.accept()

    def get_options(self):
        return {
            "grid_enabled": self.show_grid_cb.isChecked(),
            "coast_enabled": self.show_coast_cb.isChecked(),
            "labels": self.show_labels_cb.isChecked(),
            "grid_step": self.grid_spacing_spin.value(),
            "grid_color": self.grid_color_btn.color(),
            "grid_opacity": self.grid_opacity_spin.value(),
            "grid_width": self.grid_width_spin.value(),
            "grid_pattern": self.grid_pattern_combo.currentText(),
            "coast_color": self.coast_color_btn.color(),
            "coast_opacity": self.coast_opacity_spin.value(),
            "coast_width": self.coast_width_spin.value(),
            "coast_pattern": self.coast_pattern_combo.currentText(),
        }


class QuickGenerateDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Generate Image")
        self.setModal(True)
        self.resize(450, 480)
        self._parent = parent
        layout = QVBoxLayout(self)

        # Projection mode selector (at top, applies to all selections)
        proj_row = QHBoxLayout()
        proj_label = QLabel("Projection:")
        proj_label.setStyleSheet("font-weight:600;")
        proj_row.addWidget(proj_label)
        self.proj_combo = QComboBox()
        self.proj_combo.addItems(["Current Projection", "Flat Projection"])
        saved = parent.settings.get("quick_generate_projection", "current")
        self.proj_combo.setCurrentIndex(0 if saved == "current" else 1)
        self.proj_combo.currentIndexChanged.connect(self._on_projection_changed)
        proj_row.addWidget(self.proj_combo)
        proj_row.addStretch()
        layout.addLayout(proj_row)

        # Output format selector
        fmt_row = QHBoxLayout()
        fmt_label = QLabel("Output:")
        fmt_label.setStyleSheet("font-weight:600;")
        fmt_row.addWidget(fmt_label)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["PNG", "GeoTIFF"])
        saved_fmt = parent.settings.get("quick_generate_format", "png")
        self.fmt_combo.setCurrentIndex(0 if saved_fmt == "png" else 1)
        self.fmt_combo.currentIndexChanged.connect(self._on_format_changed)
        self.fmt_combo.setToolTip("PNG saves a display image with footer. GeoTIFF saves a georeferenced RGBA raster (PNG preview is also produced).")
        fmt_row.addWidget(self.fmt_combo)
        fmt_row.addStretch()
        layout.addLayout(fmt_row)

        regions = [
            ("Full Disk", "Full Earth Disk", "full_disk", None),
            ("Philippines Region", "100-150E, 3S-27N", (100, 150, -3, 27), "PAGASA TCID"),
            ("PWARDS Region", "108-135E, 8-20N", (108, 135, 8, 20), None),
            ("Japan Region", "120-150E, 20-50N", (120, 150, 20, 50), None),
            ("Westpac", "130-179E, 0-30N", (130, 179, 0, 30), None),
            ("Australia", "108-160E, 10-50S", (108, 160, -50, -10), None),
        ]
        self._region_data = []

        intro = QLabel("Select a region to generate:")
        intro.setStyleSheet("font-size:10pt; font-weight:600; padding:4px 0;")
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_w = QWidget()
        scroll_l = QVBoxLayout(scroll_w)
        scroll_l.setSpacing(2)
        scroll.setStyleSheet("QScrollArea { border:none; }")

        for name, coords, bbox, note in regions:
            btn = QPushButton(f"{name}  ({coords})")
            btn.setStyleSheet("QPushButton { text-align:left; padding:8px 12px; font-size:9pt; } QPushButton:hover { background:#3d3d3d; }")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked, b=bbox, n=name: self._select(b, n))
            scroll_l.addWidget(btn)
            self._region_data.append((btn, bbox, name))

        self._target_areas_widget = QWidget()
        self._target_areas_widget.setStyleSheet("margin-top: 4px;")
        self._build_target_areas()
        scroll_l.addWidget(self._target_areas_widget)

        scroll_l.addStretch()
        scroll.setWidget(scroll_w)
        layout.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setFixedHeight(32)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._selected_bbox = None
        self._selected_name = None
        self._target_resolution = 2000
        self._projection_mode = self.proj_combo.currentText()
        self._output_format = "png" if self.fmt_combo.currentIndex() == 0 else "geotiff"

    def _on_projection_changed(self, idx):
        value = "current" if idx == 0 else "flat"
        self._parent.settings.set("quick_generate_projection", value)
        self._projection_mode = self.proj_combo.currentText()

    def _on_format_changed(self, idx):
        value = "png" if idx == 0 else "geotiff"
        self._parent.settings.set("quick_generate_format", value)
        self._output_format = "png" if idx == 0 else "geotiff"

    def _build_target_areas(self):
        """Build the target areas section based on current ATCF storms."""
        layout = self._target_areas_widget.layout()
        if layout:
            QWidget().setLayout(layout)
        
        new_layout = QVBoxLayout(self._target_areas_widget)
        new_layout.setContentsMargins(0, 0, 0, 0)
        new_layout.setSpacing(2)
        
        atcf_storms = getattr(self._parent, 'atcf_storms', [])
        if atcf_storms:
            sep = QLabel("Target Areas (ATCF):")
            sep.setStyleSheet("font-size:9pt; font-weight:600; padding:6px 0 2px 0; color:#FFD700;")
            new_layout.addWidget(sep)
            
            res_row = QHBoxLayout()
            res_row.addWidget(QLabel("Resolution:"))
            self.res_combo = QComboBox()
            self.res_combo.addItems(["1km (1000x1000)", "2km (2000x2000)", "5km (5000x5000)"])
            self.res_combo.setCurrentIndex(1)
            self.res_combo.setMinimumWidth(150)
            res_row.addWidget(self.res_combo)
            res_row.addStretch()
            new_layout.addLayout(res_row)
            
            for storm in atcf_storms:
                name = storm.get("storm_name", "UNKNOWN")
                atcf_id = storm.get("atcf_id", "")
                if name == "INVEST" and atcf_id:
                    name = f"INVEST {atcf_id}"
                
                lon = storm.get("current_lon")
                lat = storm.get("current_lat")
                if lon is None or lat is None:
                    continue
                
                cat = storm.get("category", "")
                vmax = storm.get("max_winds_kt")
                wind_str = f"{vmax} kt" if vmax else ""
                
                label = f"{name.strip().upper()}"
                if cat:
                    label += f" [{cat}]"
                if wind_str:
                    label += f" {wind_str}"
                lon_s = f"{abs(lon):.1f}°{'E' if lon >= 0 else 'W'}"
                lat_s = f"{abs(lat):.1f}°{'N' if lat >= 0 else 'S'}"
                label += f"  ({lat_s}, {lon_s})"
                
                btn = QPushButton(label)
                btn.setStyleSheet("QPushButton { text-align:left; padding:8px 12px; font-size:9pt; } QPushButton:hover { background:#3d3d3d; }")
                btn.setCursor(Qt.PointingHandCursor)
                btn.clicked.connect(lambda checked, b=(lon, lat), n=name: self._select_target(b, n))
                new_layout.addWidget(btn)
                self._region_data.append((btn, (lon, lat), name))
        else:
            info_label = QLabel("No ATCF storms available")
            info_label.setStyleSheet("font-size:9pt; color:#888; font-style: italic;")
            new_layout.addWidget(info_label)

    def _select(self, bbox, name):
        self._selected_bbox = bbox
        self._selected_name = name
        self._target_resolution = None
        self.accept()

    def _select_target(self, center, name):
        self._selected_bbox = center
        self._selected_name = name
        res_text = self.res_combo.currentText()
        if "1km" in res_text:
            self._target_resolution = 1000
        elif "5km" in res_text:
            self._target_resolution = 5000
        else:
            self._target_resolution = 2000
        self.accept()

    def get_selection(self):
        return self._selected_bbox, self._selected_name, self._target_resolution, self._projection_mode, self._output_format
