# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: ui/ascat_window.py
# Description: ASCAT metadata window. Displays the latest ASCAT scatterometer
#              products and active storm metadata discovered by the ASCAT client.
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
# =============================================================================


import logging
from datetime import datetime

from PySide6.QtCore import Qt, QUrl, Signal, QTimer
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QTextEdit, QAbstractItemView,
    QGroupBox, QDoubleSpinBox, QGridLayout, QComboBox,
)

log = logging.getLogger(__name__)

_HEADERS_STORM = ["Satellite", "Storm ID", "Name", "Basin", "Latest Pass (UTC)", "Passes"]
_HEADERS_PROD = ["Product", "Product ID", "Status", "Last Updated", "DOI"]


class AscatWindow(QDialog):
    refresh_requested = Signal()
    download_requested = Signal(object)
    swath_requested = Signal(object)
    podaac_requested = Signal(object)
    region_crop_requested = Signal(object)

    def __init__(self, main_ui, parent=None):
        super().__init__(parent)
        self.main_ui = main_ui
        self._storm_urls = {}
        self._prod_urls = {}

        self.setWindowTitle("ASCAT — Data & Metadata")
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
        self.tabs.addTab(self.storm_table, "Active Storms")

        self.prod_table = self._make_table(_HEADERS_PROD)
        self.tabs.addTab(self.prod_table, "Products & Sources")

        self.details_edit = QTextEdit()
        self.details_edit.setReadOnly(True)
        self.details_edit.setPlaceholderText("Metadata and fetch details will appear here.")
        self.tabs.addTab(self.details_edit, "Details")

        # Download & load area
        self.download_status = QLabel("NetCDF not downloaded yet.")
        self.download_status.setWordWrap(True)
        self.download_status.setStyleSheet("color: #888; padding: 4px;")
        layout.addWidget(self.download_status)

        # Download region control
        region_box = QGroupBox("Swath download region")
        region_box.setStyleSheet("QGroupBox { font-weight: bold; }")
        rg = QGridLayout(region_box)
        self.reg_lat_min = QDoubleSpinBox()
        self.reg_lat_max = QDoubleSpinBox()
        self.reg_lon_min = QDoubleSpinBox()
        self.reg_lon_max = QDoubleSpinBox()
        for sb in (self.reg_lat_min, self.reg_lat_max):
            sb.setRange(-90, 90)
            sb.setDecimals(1)
            sb.setSingleStep(5)
        for sb in (self.reg_lon_min, self.reg_lon_max):
            sb.setRange(-180, 180)
            sb.setDecimals(1)
            sb.setSingleStep(5)
        # Debounced re-crop: whenever the storm focus or the manual download
        # box changes, re-crop the cached swath granules locally instead of
        # re-downloading them.
        self._crop_timer = QTimer(self)
        self._crop_timer.setSingleShot(True)
        self._crop_timer.setInterval(400)
        self._crop_timer.timeout.connect(self._emit_crop)
        for sb in (self.reg_lat_min, self.reg_lat_max,
                   self.reg_lon_min, self.reg_lon_max):
            sb.valueChanged.connect(lambda _=None: self._schedule_crop())
        self.reg_lat_min.setValue(0.0)
        self.reg_lat_max.setValue(40.0)
        self.reg_lon_min.setValue(110.0)
        self.reg_lon_max.setValue(170.0)
        self._reset_region_btn = QPushButton("Reset (W Pacific + SCS)")
        self._reset_region_btn.setStyleSheet(
            "QPushButton { font-weight: normal; padding: 4px 10px; }"
        )
        self._reset_region_btn.clicked.connect(self._reset_region)
        rg.addWidget(QLabel("Lat"), 0, 0)
        rg.addWidget(self.reg_lat_min, 0, 1)
        rg.addWidget(QLabel("to"), 0, 2)
        rg.addWidget(self.reg_lat_max, 0, 3)
        rg.addWidget(QLabel("°  ·  Lon"), 0, 4)
        rg.addWidget(self.reg_lon_min, 0, 5)
        rg.addWidget(QLabel("to"), 0, 6)
        rg.addWidget(self.reg_lon_max, 0, 7)
        rg.addWidget(QLabel("°"), 0, 8)
        rg.addWidget(self._reset_region_btn, 0, 9)
        rg.setColumnStretch(9, 1)
        sg = QHBoxLayout()
        self.region_storm_combo = QComboBox()
        self.region_storm_combo.setMinimumWidth(300)
        self.region_storm_combo.setToolTip(
            "Pick an active storm to auto-center the download box on its "
            "current position (from the app's ATCF storm data)."
        )
        self.region_storm_combo.currentIndexChanged.connect(
            self._on_region_storm_changed
        )
        sg.addWidget(QLabel("Focus storm:"))
        sg.addWidget(self.region_storm_combo, 1)
        layout.addLayout(sg)
        layout.addWidget(region_box)

        self.fetched_label = QLabel("")
        self.fetched_label.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(self.fetched_label)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh (one-click)")
        self.refresh_btn.clicked.connect(self.refresh_requested)
        btn_row.addWidget(self.refresh_btn)

        self.download_btn = QPushButton("Download & Load NetCDF")
        self.download_btn.setStyleSheet(
            "QPushButton { background: #00838F; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #00ACC1; }"
        )
        self.download_btn.clicked.connect(lambda: self.download_requested.emit(self._download_region()))
        btn_row.addWidget(self.download_btn)

        self.swath_btn = QPushButton("Real-Time Swath (KNMI FTP)")
        self.swath_btn.setToolTip(
            "Pull genuine near-real-time ASCAT orbit (swath) wind passes from "
            "the KNMI / EUMETSAT OSI SAF FTP. Requires a stored 'KNMI OSI SAF' "
            "FTP account (free credentials via scat@knmi.nl)."
        )
        self.swath_btn.setStyleSheet(
            "QPushButton { background: #6A1B9A; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #8E24AA; }"
        )
        self.swath_btn.clicked.connect(lambda: self.swath_requested.emit(self._download_region()))
        btn_row.addWidget(self.swath_btn)

        self.podaac_btn = QPushButton("Real-Time Swath (NASA PO.DAAC)")
        self.podaac_btn.setToolTip(
            "Free, no-cost real-time ASCAT orbit (swath) winds from the NASA "
            "PO.DAAC cloud archive over HTTPS (same OSI SAF L2 files KNMI "
            "serves). Requires a 'NASA Earthdata' login account — free, "
            "instant sign-up at https://urs.earthdata.nasa.gov — stored under "
            "System → Account… → Data API (HTTPS) tab."
        )
        self.podaac_btn.setStyleSheet(
            "QPushButton { background: #1B5E20; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #2E7D32; }"
        )
        self.podaac_btn.clicked.connect(lambda: self.podaac_requested.emit(self._download_region()))
        btn_row.addWidget(self.podaac_btn)

        self.remove_cache_btn = QPushButton("Remove ASCAT Cache")
        self.remove_cache_btn.setToolTip(
            "Delete the downloaded ASCAT swath/NetCDF files "
            "(data/ascat_data/) and clear the wind overlay."
        )
        self.remove_cache_btn.setStyleSheet(
            "QPushButton { background: #C62828; color: white; font-weight: bold; }"
            "QPushButton:hover { background: #E53935; }"
        )
        self.remove_cache_btn.clicked.connect(self._remove_ascat_cache)
        btn_row.addWidget(self.remove_cache_btn)

        self.open_btn = QPushButton("Open Selected Link")
        self.open_btn.clicked.connect(self._open_selected)
        btn_row.addWidget(self.open_btn)

        btn_row.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.close)
        btn_row.addWidget(self.close_btn)

        layout.addLayout(btn_row)

        self.set_account_status()
        self._populate_region_storms()

        # Watchdog: ATCF storms can arrive at any time; if valid storms exist
        # but the dropdown is still placeholder-only, refill it. This covers
        # any timing gap between ATCF arrival and open/show/refresh hooks.
        self._storm_timer = QTimer(self)
        self._storm_timer.setInterval(2000)
        self._storm_timer.timeout.connect(self._refresh_storm_combo_if_needed)

    # ------------------------------------------------------------ helpers
    def showEvent(self, event):
        super().showEvent(event)
        # ATCF storms load asynchronously after startup; refresh the focus
        # dropdown whenever the window is (re)shown so late-arriving storms
        # show up.
        self._populate_region_storms()
        self._storm_timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._storm_timer.stop()

    def _refresh_storm_combo_if_needed(self):
        """Refill the storm dropdown if valid storms exist but it shows only
        the placeholder (e.g. ATCF arrived before the window opened)."""
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
        """Number of actual storm entries in the focus dropdown."""
        combo = self.region_storm_combo
        n = 0
        for i in range(combo.count()):
            d = combo.itemData(i, Qt.UserRole)
            if isinstance(d, dict) and not d.get("__full_disk__"):
                n += 1
        return n

    def _download_region(self):
        """Return the swath download box as {lat_min, lat_max, lon_min, lon_max}."""
        return {
            "lat_min": self.reg_lat_min.value(),
            "lat_max": self.reg_lat_max.value(),
            "lon_min": self.reg_lon_min.value(),
            "lon_max": self.reg_lon_max.value(),
        }

    def _schedule_crop(self):
        self._crop_timer.start()

    def _emit_crop(self):
        self.region_crop_requested.emit(self._download_region())

    def _reset_region(self):
        self.region_storm_combo.blockSignals(True)
        self.region_storm_combo.setCurrentIndex(0)
        self.region_storm_combo.blockSignals(False)
        self.reg_lat_min.setValue(0.0)
        self.reg_lat_max.setValue(40.0)
        self.reg_lon_min.setValue(110.0)
        self.reg_lon_max.setValue(170.0)
        self.download_status.setText("Download region reset to Western Pacific + South China Sea.")
        self.download_status.setStyleSheet("color: #4CAF50; padding: 4px;")

    def _populate_region_storms(self):
        """Fill the storm focus dropdown from the app's ATCF storm data."""
        def deg_label(v, pos, neg):
            return f"{abs(v):.1f}°{pos if v >= 0 else neg}"

        combo = self.region_storm_combo

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
                and all(combo.itemText(i + 2) == entries[i][0]
                        for i in range(len(entries)))):
            return

        combo.blockSignals(True)
        combo.clear()
        combo.addItem("— no storm (use manual box) —")
        combo.addItem("— full disk (current satellite) —")
        combo.setItemData(combo.count() - 1, {"__full_disk__": True}, Qt.UserRole)
        for label, s in entries:
            combo.addItem(label)
            combo.setItemData(combo.count() - 1, s, Qt.UserRole)
        # Restore the previous selection when possible.
        if prior:
            idx = combo.findText(prior)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _on_region_storm_changed(self, idx):
        """Auto-center the download box on the selected storm's position."""
        if idx <= 0:
            return
        s = self.region_storm_combo.itemData(idx, Qt.UserRole)
        if isinstance(s, dict) and s.get("__full_disk__"):
            self._apply_full_disk_region()
            return
        if not isinstance(s, dict):
            return
        lat = s.get("current_lat")
        lon = s.get("current_lon")
        if lat is None or lon is None:
            return
        half = 6.0
        self.reg_lat_min.setValue(max(-90.0, lat - half))
        self.reg_lat_max.setValue(min(90.0, lat + half))
        self.reg_lon_min.setValue(max(-180.0, lon - half))
        self.reg_lon_max.setValue(min(180.0, lon + half))
        name = s.get("storm_name") or s.get("name") or "storm"
        self.download_status.setText(
            f"Download box centered on {name} "
            f"({lat:.1f}, {lon:.1f}) ± {half:g}°."
        )
        self.download_status.setStyleSheet("color: #4CAF50; padding: 4px;")

    def _apply_full_disk_region(self):
        """Size the download box to the loaded satellite's full disk.

        The extent is taken from the current scene's projection origin (the
        sub-satellite longitude); when the disk spans the antimeridian the
        box is expressed as lon_min > lon_max, which the swath readers treat
        as a wrap-around region.
        """
        min_lon, max_lon, min_lat, max_lat = 59.7, 221.7, -81.0, 81.0
        get_ext = getattr(self.main_ui, "_get_full_disk_extent", None)
        if get_ext is not None:
            try:
                e = get_ext()
                if isinstance(e, (tuple, list)) and len(e) == 4:
                    min_lon, max_lon, min_lat, max_lat = (float(v) for v in e)
            except Exception:
                pass
        lo, hi = min_lon, max_lon
        if lo > 180.0:
            lo -= 360.0
        if hi > 180.0:
            hi -= 360.0
        if lo < -180.0:
            lo += 360.0
        if hi < -180.0:
            hi += 360.0
        self.reg_lat_min.setValue(max(-90.0, float(min_lat)))
        self.reg_lat_max.setValue(min(90.0, float(max_lat)))
        self.reg_lon_min.setValue(min(180.0, max(-180.0, lo)))
        self.reg_lon_max.setValue(min(180.0, max(-180.0, hi)))
        sat = getattr(self.main_ui, "current_satellite", "") or "current satellite"
        span = ("wraps the antimeridian" if lo > hi
                else f"lon {lo:.0f}…{hi:.0f}°E")
        self.download_status.setText(
            f"Region set to full disk of {sat} (lat {min_lat:.0f}…{max_lat:.0f}°N, "
            f"{span})."
        )
        self.download_status.setStyleSheet("color: #4CAF50; padding: 4px;")

    def _remove_ascat_cache(self):
        from PySide6.QtWidgets import QMessageBox
        res = QMessageBox.question(
            self, "Remove ASCAT Cache",
            "Delete all downloaded ASCAT files (data/ascat_data/) and clear "
            "the wind overlay?",
            QMessageBox.Yes | QMessageBox.No)
        if res != QMessageBox.Yes:
            return
        ctl = getattr(self.main_ui, "ascat_controller", None)
        if ctl is None or not hasattr(ctl, "remove_ascat_cache"):
            self.set_download_status("ASCAT cache controller unavailable.", "error")
            return
        try:
            removed = ctl.remove_ascat_cache()
            self.set_download_status(
                f"ASCAT cache cleared ({len(removed)} file(s) removed).", "ok")
            self.details_edit.append(
                f"Removed ASCAT cache: {len(removed)} file(s)")
        except Exception as e:
            self.set_download_status(f"Failed to clear ASCAT cache: {e}", "error")

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

    def set_account_status(self):
        """Enable/disable the real-time swath buttons based on stored accounts."""
        settings = getattr(self.main_ui, "settings", None)
        knmi_ok = False
        podaac_ok = False
        try:
            from ..clients.knmi_swath import find_knmi_ftp_account
            knmi_ok = find_knmi_ftp_account(settings) is not None
            from ..clients.podaac_swath import find_earthdata_account
            podaac_ok = find_earthdata_account(settings) is not None
        except Exception:
            log.warning("Account availability check failed", exc_info=True)
        self.swath_btn.setEnabled(knmi_ok)
        self.podaac_btn.setEnabled(podaac_ok)
        hints = []
        if not knmi_ok:
            hints.append("KNMI FTP (purple) needs a 'KNMI OSI SAF' account — "
                         "System → Account… → FTP → Easy template")
        if not podaac_ok:
            hints.append("PO.DAAC (green) needs a 'NASA Earthdata' account — "
                         "System → Account… → Data API (HTTPS) → Easy template")
        if hints:
            self.download_status.setText(" | ".join(hints))
            self.download_status.setStyleSheet("color: #F57F17; padding: 4px;")
        else:
            self.download_status.setText("Real-time swath downloaders ready — "
                                         "accounts recognized.")
            self.download_status.setStyleSheet("color: #4CAF50; padding: 4px;")

    # ----------------------------------------------------- download status
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

    def set_download_result(self, info):
        if not isinstance(info, dict):
            return
        path = info.get("file_path", "")
        loaded = info.get("loaded")
        lines = [f"Saved: {path}"]
        lines.append(f"Dataset: {info.get('title', '')} ({info.get('dataset_id', '?')})")
        lines.append(f"Valid time: {info.get('time_str', '?')}")
        passes = info.get("passes")
        if passes:
            lines.append(f"Passes: {len(passes)}")
            for p in passes:
                lines.append(
                    f"  • {p.get('satellite', '?')} {p.get('sampling', '?')} km "
                    f"@ {p.get('dt'):%Y-%m-%d %H:%MZ} — {p.get('points', 0)} wind cells"
                )
        slp = info.get("storm_latest_pass")
        if slp is not None:
            try:
                st = slp.strftime("%Y-%m-%d %H:%M UTC") if hasattr(slp, "strftime") else str(slp)
                lines.append(f"Latest scraped ASCAT pass: {st}")
            except Exception:
                pass
        lines.append(
            f"Region: lat {info.get('lat', ['?'])[0]}–{info.get('lat', ['?'])[1]}, "
            f"lon {info.get('lon', ['?'])[0]}–{info.get('lon', ['?'])[1]}"
        )
        warning = info.get("warning") or ""
        if loaded:
            lines.append("Loaded into viewport as ASCAT wind overlay.")
            self.set_download_status(
                ("Loaded into viewport. " + warning) if warning else "Loaded into viewport.",
                "ok",
            )
        else:
            lines.append("Downloaded but could not load into the viewport.")
            self.set_download_status(
                ("Downloaded but not loaded. " + warning) if warning
                else "Downloaded but not loaded.",
                "ok",
            )
        if warning:
            self.status_label.setText(warning)
            self.status_label.setStyleSheet("font-weight: bold; color: #FB8C00; padding: 4px;")
        self.details_edit.append("\n".join(lines))

    # --------------------------------------------------------------- data
    def set_data(self, data):
        if not isinstance(data, dict):
            return
        fetched = data.get("fetched_at", "")
        self.fetched_label.setText(f"Fetched: {fetched}")
        self.status_label.setText(
            f"Latest ASCAT data ready — {len(data.get('storms', []))} active storm "
            f"product(s), {len(data.get('products', []))} OSI SAF product(s)."
        )
        self.status_label.setStyleSheet("font-weight: bold; color: #4CAF50; padding: 4px;")
        self._populate_storms(data.get("storms", []))
        self._populate_products(data.get("products", []))
        self._populate_details(data)
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
                s.get("storm_number", "") or s.get("storm_id", ""),
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
                p.get("doi", "") or "—",
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
        lines.append(f"ASCAT data fetched at: {data.get('fetched_at', '—')}")
        lines.append("")
        lines.append("Satellites queried:")
        for sat in data.get("satellites", []):
            lines.append(f"  • {sat}")
        lines.append("")
        lines.append("Errors / notes:")
        errors = data.get("errors", [])
        if errors:
            for e in errors:
                lines.append(f"  • {e}")
        else:
            lines.append("  • No errors reported.")
        self.details_edit.setPlainText("\n".join(lines))

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
