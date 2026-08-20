# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: ui/microwave_window.py
# Description: Passive-microwave data window. Displays the latest microwave
#              brightness-temperature storm products (NOAA Manati GCOM-W1 /
#              AMSR2), NODD JPSS ATMS/VIIRS availability and NASA Earthdata
#              sources, and downloads AMSR2 pass images plus NODD ATMS
#              88.2 GHz overpass granules.
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
# Copyright (C) 2025-2026 PWARDS-weather (Pasacao Weather Atmospheric
# and Real-Time Data System)
# =============================================================================


import logging
import os

from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QPixmap, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit, QAbstractItemView,
    QGroupBox, QComboBox, QGridLayout,
)

log = logging.getLogger(__name__)

_HEADERS_STORM = ["Satellite", "Storm ID", "Name", "Basin", "Latest Pass (UTC)", "Passes"]
_HEADERS_PROD = ["Product", "Product ID", "Status", "Last Updated", "Source"]


class MicrowaveWindow(QDialog):
    refresh_requested = Signal()
    amsr2_requested = Signal(object)
    atms_requested = Signal(object)
    mimic_requested = Signal(object)
    viirs_requested = Signal(object)
    amsr2raw_requested = Signal(object)

    def __init__(self, main_ui, parent=None):
        super().__init__(parent)
        self.main_ui = main_ui
        self._storm_urls = {}
        self._prod_urls = {}

        self.setWindowTitle("Microwave — Data & Metadata")
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.resize(1000, 700)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Preparing...")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-weight: bold; color: #4FC3F7; padding: 4px;")
        layout.addWidget(self.status_label)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        self.storm_table = self._make_table(_HEADERS_STORM)
        self.tabs.addTab(self.storm_table, "Active Storms (AMSR2)")

        self.prod_table = self._make_table(_HEADERS_PROD)
        self.tabs.addTab(self.prod_table, "Products & Sources")

        self.details_edit = QTextEdit()
        self.details_edit.setReadOnly(True)
        self.details_edit.setPlaceholderText("Metadata and fetch details will appear here.")
        self.tabs.addTab(self.details_edit, "Details")

        # Download & load area
        self.download_status = QLabel("Microwave data not downloaded yet.")
        self.download_status.setWordWrap(True)
        self.download_status.setStyleSheet("color: #888; padding: 4px;")
        layout.addWidget(self.download_status)

        # Target selection
        target_box = QGroupBox("Download targets")
        target_box.setStyleSheet("QGroupBox { font-weight: bold; }")
        tg = QHBoxLayout(target_box)

        self.amsr2_storm_combo = QComboBox()
        self.amsr2_storm_combo.setMinimumWidth(280)
        self.amsr2_storm_combo.setToolTip(
            "Pick an AMSR2 storm product to download its newest 37/89 GHz "
            "brightness-temperature pass images."
        )
        tg.addWidget(QLabel("AMSR2 storm:"))
        tg.addWidget(self.amsr2_storm_combo, 1)

        self.pos_storm_combo = QComboBox()
        self.pos_storm_combo.setMinimumWidth(280)
        self.pos_storm_combo.setToolTip(
            "Pick an active storm (from the app's ATCF storm data) to center "
            "the ATMS / MIMIC-TC2 / VIIRS / AMSR2 overlay downloads on its "
            "current position."
        )
        tg.addWidget(QLabel("Overlay focus storm:"))
        tg.addWidget(self.pos_storm_combo, 1)
        layout.addWidget(target_box)

        self.fetched_label = QLabel("")
        self.fetched_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.fetched_label)

        btn_row = QGridLayout()
        btn_row.setSpacing(6)
        self.refresh_btn = QPushButton("Refresh (one-click)")
        self.refresh_btn.clicked.connect(self.refresh_requested)
        btn_row.addWidget(self.refresh_btn, 0, 0)

        self.amsr2_btn = QPushButton("Download AMSR2 Pass (37/89 GHz)")
        self.amsr2_btn.setStyleSheet(
            "QPushButton { background: #00695C; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #00897B; }"
        )
        self.amsr2_btn.clicked.connect(self._request_amsr2)
        btn_row.addWidget(self.amsr2_btn, 0, 1)

        self.atms_btn = QPushButton("Download ATMS 88.2 GHz Overpass")
        self.atms_btn.setToolTip(
            "Search the NOAA Open Data Dissemination (NODD) JPSS public "
            "archive for the ATMS granule whose 88.2 GHz swath passes nearest "
            "the focus storm within the last 12 hours, and download the "
            "SDR + GEO pair."
        )
        self.atms_btn.setStyleSheet(
            "QPushButton { background: #6A1B9A; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #8E24AA; }"
        )
        self.atms_btn.clicked.connect(self._request_atms)
        btn_row.addWidget(self.atms_btn, 0, 2)

        self.mimic_btn = QPushButton("Download MIMIC-TC2 89 GHz")
        self.mimic_btn.setToolTip(
            "Fetch the CIMSS/SSEC MIMIC-TC2 89 GHz coherent brightness-"
            "temperature field (NetCDF) for the storm nearest the focus "
            "position and load it as a viewport overlay."
        )
        self.mimic_btn.setStyleSheet(
            "QPushButton { background: #00838F; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #00ACC1; }"
        )
        self.mimic_btn.clicked.connect(self._request_mimic)
        btn_row.addWidget(self.mimic_btn, 0, 3)

        self.viirs_btn = QPushButton("Download VIIRS I5 11.45 um")
        self.viirs_btn.setToolTip(
            "Search the NOAA Open Data Dissemination (NODD) JPSS public "
            "archive for the VIIRS imaging-band I5 (11.45 um) SDR + GEO "
            "granule nearest the focus position and load the swath as a "
            "viewport overlay."
        )
        self.viirs_btn.setStyleSheet(
            "QPushButton { background: #AD1457; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #D81B60; }"
        )
        self.viirs_btn.clicked.connect(self._request_viirs)
        btn_row.addWidget(self.viirs_btn, 0, 4)

        self.amsr2raw_btn = QPushButton("Download AMSR2 L1B 89 GHz (raw)")
        self.amsr2raw_btn.setToolTip(
            "Download the raw NOAA OSPO GCOM-W1 AMSR2 L1B granule nearest the "
            "focus position and load its 89.0 GHz-A brightness-temperature "
            "swath as a viewport overlay."
        )
        self.amsr2raw_btn.setStyleSheet(
            "QPushButton { background: #2E7D32; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #43A047; }"
        )
        self.amsr2raw_btn.clicked.connect(self._request_amsr2raw)
        btn_row.addWidget(self.amsr2raw_btn, 0, 5)

        self.remove_cache_btn = QPushButton("Remove Microwave Cache")
        self.remove_cache_btn.setToolTip(
            "Delete the downloaded microwave files (data/microwave_data/)."
        )
        self.remove_cache_btn.setStyleSheet(
            "QPushButton { background: #C62828; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #E53935; }"
        )
        self.remove_cache_btn.clicked.connect(self._remove_microwave_cache)
        btn_row.addWidget(self.remove_cache_btn, 1, 0)

        self.open_btn = QPushButton("Open Selected Link")
        self.open_btn.clicked.connect(self._open_selected)
        btn_row.addWidget(self.open_btn, 1, 1)

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.close_btn, 1, 2)

        layout.addLayout(btn_row)

        self._populate_region_storms()

        # Watchdog: ATCF storms can arrive at any time; refill the focus
        # dropdown whenever valid storms exist but the dropdown is not filled.
        self._storm_timer = QTimer(self)
        self._storm_timer.setInterval(2000)
        self._storm_timer.timeout.connect(self._refresh_storm_combo_if_needed)

    # ------------------------------------------------------------ helpers
    def showEvent(self, event):
        super().showEvent(event)
        self._populate_region_storms()
        self._storm_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._storm_timer.stop()

    def _refresh_storm_combo_if_needed(self):
        """Refill the ATMS focus dropdown if valid storms exist but the
        dropdown still shows only the placeholder."""
        try:
            if self._storm_item_count() > 0:
                return
            storms = getattr(self.main_ui, "atcf_storms", None) or []
            if any(s.get("current_lat") is not None
                   and s.get("current_lon") is not None for s in storms):
                self._populate_region_storms()
        except Exception:
            pass

    def _storm_item_count(self):
        combo = self.pos_storm_combo
        n = 0
        for i in range(combo.count()):
            d = combo.itemData(i, Qt.UserRole)
            if isinstance(d, dict) and not d.get("__full_disk__"):
                n += 1
        return n

    def _populate_region_storms(self):
        """Fill the ATMS focus dropdown from the app's ATCF storm data."""
        def deg_label(v, pos, neg):
            return f"{abs(v):.1f}°{pos if v >= 0 else neg}"

        combo = self.pos_storm_combo

        view = combo.view()
        if view is not None and view.isVisible():
            return

        prior = combo.currentText()
        entries = []
        storms = getattr(self.main_ui, "atcf_storms", None) or []
        for s in storms:
            lat = s.get("current_lat")
            lon = s.get("current_lon")
            if lat is None or lon is None:
                continue
            name = (s.get("storm_name") or s.get("name")
                    or s.get("atcf_id") or "Storm")
            cat = s.get("category") or ""
            label = (f"{name} — {deg_label(lat, 'N', 'S')} "
                     f"{deg_label(lon, 'E', 'W')}")
            if cat:
                label += f" ({cat})"
            entries.append((label, s))

        if (self._storm_item_count() == len(entries)
                and all(combo.itemText(i + 1) == entries[i][0]
                        for i in range(len(entries)))):
            return

        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— no storm (viewport centre) —")
        combo.setItemData(combo.count() - 1,
                          {"__use_viewport__": True, "__full_disk__": True},
                          Qt.UserRole)
        for label, s in entries:
            combo.addItem(label)
            combo.setItemData(combo.count() - 1, s, Qt.UserRole)
        if prior:
            idx = combo.findText(prior)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    # -------------------------------------------------------------- tables
    def _make_table(self, headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.cellDoubleClicked.connect(lambda r, c: self._open_selected())
        return table

    def set_status(self, msg):
        self.status_label.setText(msg)

    def set_download_status(self, msg, state="busy"):
        self.download_status.setText(msg)
        colors = {
            "busy": "#FFA726",
            "ok": "#4CAF50",
            "error": "#E53935",
        }
        self.download_status.setStyleSheet(
            f"color: {colors.get(state, '#888')}; font-weight: bold; padding: 4px;"
        )

    # --------------------------------------------------------------- data
    def set_data(self, data):
        if not isinstance(data, dict):
            return
        fetched = data.get("fetched_at", "")
        self.fetched_label.setText(f"Fetched: {fetched}")
        self.status_label.setText(
            f"Latest microwave data ready — {len(data.get('storms', []))} "
            f"AMSR2 storm product(s), {len(data.get('products', []))} source(s)."
        )
        self.status_label.setStyleSheet("font-weight: bold; color: #4CAF50; padding: 4px;")
        self._populate_storms(data.get("storms", []))
        self._populate_products(data.get("products", []))
        self._populate_details(data)
        self._populate_amsr2_combo(data.get("storms", []))
        self.tabs.setCurrentIndex(0)

    def _populate_storms(self, storms):
        table = self.storm_table
        table.setRowCount(0)
        self._storm_urls.clear()
        storms = sorted(
            storms,
            key=lambda s: (s.get("basin", ""), s.get("storm_id", "")),
        )
        for row, s in enumerate(storms):
            table.insertRow(row)
            latest = s.get("latest_pass") or "—"
            passes = s.get("pass_count", 0)
            values = [
                s.get("satellite", ""),
                s.get("storm_id", ""),
                s.get("storm_name", ""),
                s.get("basin", ""),
                latest,
                str(passes),
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                if col in (0, 1, 2, 4):
                    item.setForeground(QColor("#BBDEFB"))
                table.setItem(row, col, item)
            self._storm_urls[row] = s.get("product_url") or s.get("url") or ""
        table.resizeColumnsToContents()

    def _populate_products(self, products):
        table = self.prod_table
        table.setRowCount(0)
        self._prod_urls.clear()
        for row, p in enumerate(products):
            table.insertRow(row)
            values = [
                p.get("name", ""),
                p.get("product_id", "") or "—",
                p.get("status", "") or "—",
                p.get("updated", "") or "—",
                p.get("url", "") or "—",
            ]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                if col == 0:
                    item.setForeground(QColor("#BBDEFB"))
                table.setItem(row, col, item)
            self._prod_urls[row] = p.get("url") or ""
        table.resizeColumnsToContents()

    def _populate_details(self, data):
        lines = []
        lines.append(f"Microwave data fetched at: {data.get('fetched_at', '—')}")
        lines.append("")
        lines.append("Satellites queried:")
        for sat in data.get("satellites", []):
            lines.append(f"  • {sat}")
        lines.append("")
        lines.append("Sources:")
        lines.append("  • NOAA Manati — GCOM-W1 / AMSR2 storm products")
        lines.append("  • NOAA Open Data Dissemination (NODD) — JPSS ATMS / VIIRS SDR")
        lines.append("  • NASA Earthdata — GPM GMI, AMSR2 L2 (JAXA), JPSS SDR")
        lines.append("")
        lines.append("Errors / notes:")
        errors = data.get("errors", [])
        if errors:
            for e in errors:
                lines.append(f"  • {e}")
        else:
            lines.append("  • No errors reported.")
        self.details_edit.setPlainText("\n".join(lines))

    # ---------------------------------------------------------- storm combos
    def _populate_amsr2_combo(self, storms):
        combo = self.amsr2_storm_combo
        combo.blockSignals(True)
        combo.clear()
        if not storms:
            combo.addItem("— no AMSR2 storm products available —")
        for s in storms:
            label = (f"{s.get('storm_name', '?')} — {s.get('basin', '?')}")
            combo.addItem(label)
            combo.setItemData(combo.count() - 1, s, Qt.UserRole)
        combo.blockSignals(False)

    def _selected_amsr2_storm(self):
        combo = self.amsr2_storm_combo
        idx = combo.currentIndex()
        if idx < 0:
            return {}
        s = combo.itemData(idx, Qt.UserRole)
        return s if isinstance(s, dict) else {}

    def _selected_atms_storm(self):
        combo = self.pos_storm_combo
        idx = combo.currentIndex()
        if idx < 0:
            return {}
        s = combo.itemData(idx, Qt.UserRole)
        return s if isinstance(s, dict) else {}

    # ----------------------------------------------------------- downloads
    def _request_amsr2(self):
        storm = self._selected_amsr2_storm()
        if not storm or not storm.get("storm_id"):
            self.set_download_status(
                "Pick a valid AMSR2 storm from the dropdown first.", "error")
            return
        self.amsr2_requested.emit(storm)

    def _request_atms(self):
        storm = self._selected_atms_storm()
        self.atms_requested.emit(storm)

    def _request_mimic(self):
        storm = self._selected_atms_storm()
        self.mimic_requested.emit(storm)

    def _request_viirs(self):
        storm = self._selected_atms_storm()
        self.viirs_requested.emit(storm)

    def _request_amsr2raw(self):
        storm = self._selected_atms_storm()
        self.amsr2raw_requested.emit(storm)

    def _remove_microwave_cache(self):
        from PySide6.QtWidgets import QMessageBox
        res = QMessageBox.question(
            self, "Remove Microwave Cache",
            "Delete all downloaded microwave files (data/microwave_data/)?",
            QMessageBox.Yes | QMessageBox.No)
        if res != QMessageBox.Yes:
            return
        ctl = getattr(self.main_ui, "microwave_controller", None)
        if ctl is None or not hasattr(ctl, "remove_microwave_cache"):
            self.set_download_status("Microwave cache controller unavailable.", "error")
            return
        try:
            removed = ctl.remove_microwave_cache()
            self.set_download_status(
                f"Microwave cache cleared ({len(removed)} file(s) removed).", "ok")
            self.details_edit.append(
                f"Removed microwave cache: {len(removed)} file(s)")
        except Exception as e:
            self.set_download_status(f"Failed to clear microwave cache: {e}", "error")

    # ----------------------------------------------------------- results
    def set_amsr2_result(self, info):
        if not isinstance(info, dict):
            return
        lines = []
        lines.append(f"AMSR2 pass saved: {info.get('file_path', '')}")
        lines.append(f"Storm: {info.get('storm_name', '?')}")
        lines.append(f"Pass time: {info.get('time_str', '?')}")
        channels = info.get("channels") or []
        lines.append(f"Channels: {', '.join(channels) if channels else '—'}")
        for fp in info.get("file_paths") or []:
            if fp != info.get("file_path"):
                lines.append(f"  • {fp}")
        if info.get("file_path"):
            names = [os.path.basename(p) for p in (info.get("file_paths") or [])]
            self.set_download_status(
                f"AMSR2 pass downloaded: {', '.join(names)} — found in "
                f"data/microwave_data/amsr2/", "ok")
        else:
            self.set_download_status("AMSR2 pass downloaded.", "ok")
        self.details_edit.append("\n".join(lines))

    def set_atms_result(self, info):
        if not isinstance(info, dict):
            return
        loaded = info.get("loaded")
        lines = []
        lines.append(f"ATMS 88.2 GHz overpass granule: {info.get('sdr_file', '')}")
        lines.append(f"Satellite: {info.get('satellite', '?')}")
        lines.append(f"Scan time: {info.get('time_str', '?')} "
                     f"(age {info.get('age_hours', '?')} h)")
        lines.append(f"Nearest swath distance: {info.get('distance_km', '?')} km")
        lines.append(f"88.2 GHz samples within 150 km: "
                     f"{info.get('coverage_points', 0)}")
        if info.get("tb_min") is not None and info.get("tb_max") is not None:
            lines.append(f"TB88 range near storm: {info.get('tb_min')} – "
                         f"{info.get('tb_max')} K")
        lines.append(f"Bucket: {info.get('bucket', '?')} — SDR/GEO cached in "
                     f"data/microwave_data/nodac_mw/")
        if loaded:
            lines.append("Loaded into viewport as a microwave TB overlay.")
            self.set_download_status(
                "Loaded into viewport as microwave TB overlay.", "ok")
        else:
            swath = info.get("swath_tb")
            n = 0 if swath is None else len(swath)
            lines.append(f"Downloaded but could not overlay ({n} valid TB points).")
            if n == 0:
                lines.append("The granule's 88.2 GHz swath did not map to the "
                             "loaded viewport projection/region.")
            self.set_download_status(
                ("Downloaded but not loaded into viewport." if n
                 else "Downloaded but the granule has no 88.2 GHz swath."),
                "ok",
            )
        self.details_edit.append("\n".join(lines))

    def set_mimic_result(self, info):
        if not isinstance(info, dict):
            return
        loaded = info.get("loaded")
        lines = []
        lines.append(f"MIMIC-TC2 89 GHz field granule: {info.get('sdr_key', '')}")
        lines.append(f"Storm grid: {info.get('storm', '?')}")
        lines.append(f"Valid time: {info.get('time_str', '?')} "
                     f"(age {info.get('age_hours', '?')} h)")
        lines.append(f"89 GHz samples: {info.get('coverage_points', 0)}")
        if info.get("tb_min") is not None and info.get("tb_max") is not None:
            lines.append(f"TB89 range: {info.get('tb_min')} – "
                         f"{info.get('tb_max')} K")
        lines.append(f"Source: {info.get('bucket', '?')} — NetCDF cached in "
                     f"data/microwave_data/mimic_tc2/")
        if loaded:
            lines.append("Loaded into viewport as a microwave TB overlay.")
            self.set_download_status(
                "Loaded into viewport as microwave TB overlay.", "ok")
        else:
            lines.append("Downloaded but could not overlay.")
            self.set_download_status(
                "Downloaded but not loaded into viewport.", "ok")
        self.details_edit.append("\n".join(lines))

    def set_viirs_result(self, info):
        if not isinstance(info, dict):
            return
        loaded = info.get("loaded")
        lines = []
        lines.append(f"VIIRS I5 11.45 um overpass granule: {info.get('sdr_key', '')}")
        lines.append(f"Satellite: {info.get('satellite', '?')}")
        lines.append(f"Scan time: {info.get('time_str', '?')} "
                     f"(age {info.get('age_hours', '?')} h)")
        lines.append(f"Nearest swath distance: {info.get('distance_km', '?')} km")
        lines.append(f"11.45 um samples within 150 km: "
                     f"{info.get('coverage_points', 0)}")
        if info.get("tb_min") is not None and info.get("tb_max") is not None:
            lines.append(f"I5 TB range: {info.get('tb_min')} – "
                         f"{info.get('tb_max')} K")
        lines.append(f"Bucket: {info.get('bucket', '?')} — SDR/GEO cached in "
                     f"data/microwave_data/nodac_mw/")
        if loaded:
            lines.append("Loaded into viewport as a microwave TB overlay.")
            self.set_download_status(
                "Loaded into viewport as microwave TB overlay.", "ok")
        else:
            lines.append("Downloaded but could not overlay.")
            self.set_download_status(
                "Downloaded but not loaded into viewport.", "ok")
        self.details_edit.append("\n".join(lines))

    def set_amsr2raw_result(self, info):
        if not isinstance(info, dict):
            return
        loaded = info.get("loaded")
        lines = []
        lines.append(f"Raw AMSR2 L1B 89 GHz granule: {info.get('sdr_key', '')}")
        lines.append(f"Satellite: {info.get('satellite', '?')}")
        lines.append(f"Scan time: {info.get('time_str', '?')} "
                     f"(age {info.get('age_hours', '?')} h)")
        lines.append(f"Nearest swath distance: {info.get('distance_km', '?')} km")
        lines.append(f"Raw 89 GHz samples: {info.get('coverage_points', 0)}")
        if info.get("tb_min") is not None and info.get("tb_max") is not None:
            lines.append(f"TB89 range: {info.get('tb_min')} – "
                         f"{info.get('tb_max')} K")
        lines.append(f"Source: {info.get('bucket', '?')} — L1B cached in "
                     f"data/microwave_data/amsr2_l1b/")
        if loaded:
            lines.append("Loaded into viewport as a microwave TB overlay.")
            self.set_download_status(
                "Loaded into viewport as microwave TB overlay.", "ok")
        else:
            lines.append("Downloaded but could not overlay.")
            self.set_download_status(
                "Downloaded but not loaded into viewport.", "ok")
        self.details_edit.append("\n".join(lines))

    # -------------------------------------------------------------- links
    def _open_selected(self):
        table = self.tabs.currentWidget()
        if table is None:
            return
        row = table.currentRow()
        if row < 0:
            self.status_label.setText("Select a row first, then press 'Open Selected Link'.")
            return
        url_map = self._storm_urls if table is self.storm_table else self._prod_urls
        url = url_map.get(row, "")
        if not url:
            self.status_label.setText("No link available for the selected row.")
            return
        QDesktopServices.openUrl(QUrl(url))
        self.status_label.setText(f"Opened: {url}")