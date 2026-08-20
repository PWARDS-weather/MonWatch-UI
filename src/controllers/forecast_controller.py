# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: controllers/forecast_controller.py
# Description: Tropical cyclone forecast track retrieval and visualization from NHC, JMA, JTWC, PAGASA, and ATCF sources.
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


import sys
import os
import json
import uuid
import logging
import math
import shutil
import tempfile
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from collections import OrderedDict

import numpy as np
from PIL import Image as PILImage, ImageDraw, ImageFont

from PySide6.QtCore import QObject, Signal, QThread, QTimer, Qt, QDate, QPoint, QSize, QProcess
from PySide6.QtGui import (
    QPixmap, QIcon, QPainter, QFont, QPen, QColor, QPainterPath, QImage,
    QFontMetrics,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTreeWidget, QTreeWidgetItem, QCheckBox, QMessageBox, QProgressBar,
    QComboBox, QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsPathItem,
    QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsPolygonItem,
    QDialog, QGraphicsSimpleTextItem, QMenu, QGroupBox, QToolButton,
    QGridLayout,
)

from src.clients import nhc, jma, agency_tracker
from src.clients.nhc import NHCDownloader, _dl_zip_and_extract, fetch_active_storms, _download_file
from src.core.helpers import (
    normalize_lon, _densify_polygon, _save_tracks_to_disk, _load_tracks_from_disk,
    _get_tracks_folder, _get_archived_nhc_folder, _get_archived_jma_folder,
    _get_archived_jtwc_folder, _get_archived_pagasa_folder, _get_archived_tracks_folder,
    THEMES,
)
from src.ui.dialogs import (
    MeteorologicalTrackDialog, ForecastDialog, CombinedForecastDialog,
)
from src.ui.TearOffTabBar import TabFloatingWindow

log = logging.getLogger(__name__)

if getattr(sys, 'frozen', False):
    _top_dir = Path(sys.executable).resolve().parent
    _src_dir = _top_dir
else:
    _top_dir = Path(__file__).resolve().parent.parent.parent
    _src_dir = Path(__file__).resolve().parent.parent


def _normalize_lon_extent(lons, margin=8.0):
    """Normalize longitude extent handling dateline crossing."""
    if not lons:
        return -180, 180, 0
    norm = [((float(l) + 180.0) % 360.0) - 180.0 for l in lons]
    min_n, max_n = min(norm), max(norm)
    if max_n - min_n > 180:
        shifted = [l if l >= 0 else l + 360.0 for l in norm]
        min_s, max_s = min(shifted), max(shifted)
        center_s = (min_s + max_s) / 2.0
        central_lon = center_s - 360.0 if center_s > 180.0 else center_s
        half_span = max(max_s - center_s, center_s - min_s) + margin
        return center_s - half_span, center_s + half_span, 180.0
    else:
        central_lon = (min_n + max_n) / 2.0
        return max(min_n - margin, -180.0), min(max_n + margin, 180.0), central_lon


class ForecastController(QObject):
    """Tropical cyclone forecast track retrieval and visualization.

    Manages forecast data acquisition from NHC, JMA, JTWC, PAGASA,
    and ATCF sources. Handles track rendering, storm selection, and
    multi-agency forecast comparison.

    Attributes:
        main_ui (MainUI): Reference to the main UI instance.

    Signals:
        forecast_loaded (str): Emitted when forecast data is available.
        storm_selected (dict): Emitted when a storm is selected.
        forecast_progress (int): Emitted during data download.

    Note:
        Supports both real-time forecasts and historical best-track data.
        Cross-references storms across agencies for unified tracking.
    """
    storm_downloaded = Signal(str, str, object)
    forecast_finished = Signal(int, int, str, str)
    progress = Signal(str)
    error = Signal(str)

    def __init__(self, main_ui):
        super().__init__()
        self.main_ui = main_ui


    def _download_all_tc_updates(self, _from_startup=False):

        """Download updates for tropical cyclones in a subprocess.

        Launches Process/forecast/downloader.py as a separate QProcess.
        If the storm combo filter is set to a specific agency (e.g. "All CWA Storms"),
        only that agency is downloaded. Otherwise all agencies run.
        Progress lines are read from stdout and displayed in the UI.
        On completion, data is reloaded from disk.

        Args:
            _from_startup: If True, called from startup sync (no button state changes).
        """
        if getattr(self.main_ui, '_download_proc', None) is not None:
            if _from_startup:
                self.main_ui.log("Download already in progress, skipping startup trigger.")
            return

        if not _from_startup:
            self.main_ui.download_tc_btn.setEnabled(False)
            self.main_ui.download_tc_btn.setText("Updating forecast tracks...")
        self.main_ui.fc_status_label.setText("Updating forecast tracks...")
        self.main_ui.log("Starting forecast track download in subprocess...")
        self._update_download_progress(True)

        # Check storm combo filter - if set to a single agency, only update that one
        agency_filter = None
        if not _from_startup:
            combo = getattr(self.main_ui, 'storm_combo', None)
            if combo:
                data = combo.currentData()
                if data and data != "all":
                    for prefix in ("nhc:", "jma:", "jtwc:", "pagasa:", "cwa:"):
                        if data.startswith(prefix):
                            agency_filter = prefix.rstrip(":")
                            break

        script = str(_top_dir / "Process" / "forecast" / "downloader.py")
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._on_download_proc_ready(proc))
        proc.finished.connect(self._on_download_proc_finished)
        self.main_ui._download_proc = proc
        self.main_ui._download_buffer = ""

        # Pass NHC-discovered EP storm IDs to JTWC subprocess so it can
        # probe for JTWC equivalents (EP bulletin is often blocked).
        extra_jtwc_ids = []
        for sid in getattr(self.main_ui, 'nhc_storms', {}):
            if sid.startswith("ep") and len(sid) >= 6:
                try:
                    num = sid[2:4]
                    yy = sid[-2:]
                    extra_jtwc_ids.append(f"ep{num}{yy}")
                except Exception:
                    pass
        args = [script, "--project-root", str(_top_dir)]
        if agency_filter:
            args.extend(["--agency", agency_filter])
        if extra_jtwc_ids:
            args.extend(["--jtwc-extra"] + extra_jtwc_ids)
        proc.start(sys.executable, args)



    def _download_nhc_updates(self):
        self.main_ui._download_all_tc_updates()



    def _update_download_progress(self, visible):

        if visible:
            bar = getattr(self.main_ui, '_download_progress_bar', None)
            if not bar:
                bar = QProgressBar()
                bar.setRange(0, 0)
                bar.setFixedWidth(160)
                bar.setFixedHeight(16)
                bar.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid #FF8C00;
                        border-radius: 3px;
                        text-align: center;
                        background: #2A2A2A;
                        color: #FFF;
                        font-size: 8px;
                    }
                    QProgressBar::chunk {
                        background: #FF8C00;
                        border-radius: 2px;
                    }
                """)
                bar.setFormat("Updating...")
                self.main_ui.status_bar.addPermanentWidget(bar)
                self.main_ui._download_progress_bar = bar
            bar.setVisible(True)
            bar.repaint()
        else:
            bar = getattr(self.main_ui, '_download_progress_bar', None)
            if bar:
                self.main_ui.status_bar.removeWidget(bar)
                bar.deleteLater()
                self.main_ui._download_progress_bar = None



    def _on_download_proc_ready(self, proc):

        raw = bytes(proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self.main_ui._download_buffer += raw
        while "\n" in self.main_ui._download_buffer:
            line, self.main_ui._download_buffer = self.main_ui._download_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                msg_type = obj.get("type", "")
                agency = obj.get("agency", "").upper()
                message = obj.get("message", "")
                if msg_type == "progress" and message:
                    self.main_ui.fc_status_label.setText(message)
                    self.main_ui.log(f"[{agency}] {message}")
                elif msg_type == "start":
                    self.main_ui.fc_status_label.setText(f"{agency}: downloading...")
                    self.main_ui.log(f"[{agency}] Starting download")
                elif msg_type == "error":
                    self.main_ui.log(f"[{agency}] Error: {obj.get('message', '')}")
            except json.JSONDecodeError:
                self.main_ui.log(line)
            QApplication.processEvents()



    def _on_download_proc_finished(self, exit_code, exit_status):

        self.main_ui.download_tc_btn.setEnabled(True)
        self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
        self._update_download_progress(False)
        self.main_ui._download_proc = None
        self.main_ui._download_buffer = ""

        if exit_code != 0:
            self.main_ui.fc_status_label.setText("Download failed.")
            self.main_ui.log(f"Forecast download process exited with code {exit_code}")
            return

        self.main_ui.fc_status_label.setText("Reloading downloaded data...")
        QApplication.processEvents()

        self.main_ui.log("Reloading data from disk...")
        self._load_nhc_from_disk()
        QApplication.processEvents()
        self._load_jma_from_disk()
        QApplication.processEvents()
        self._load_pagasa_from_disk()
        QApplication.processEvents()
        self._load_jtwc_from_disk()
        QApplication.processEvents()
        self._load_cwa_from_disk()
        QApplication.processEvents()

        # Update track entry points from freshly loaded agency data
        from src.core.helpers import _save_tracks_to_disk
        for t in self.main_ui.tracks:
            tid = t.get("id", "")
            if tid.startswith("jma_"):
                sid = tid[4:]
                storm = self.main_ui.jma_storms.get(sid)
                if storm:
                    pts = storm.get("track_points", [])
                    if pts:
                        t["points"] = [{
                            "lon": p["lon"], "lat": p["lat"],
                            "datetime": p.get("datetime", ""),
                            "intensity": p.get("intensity"),
                            "intensity_category": p.get("intensity_category"),
                            "pressure": p.get("pressure"),
                            "wind_gust_kt": p.get("wind_gust_kt"),
                            "location": p.get("location"),
                            "course": p.get("course"),
                            "speed_kt": p.get("speed_kt"),
                            "scale": p.get("scale"),
                            "intensity_label": p.get("intensity_label"),
                            "prob_circle_km": p.get("prob_circle_km"),
                            "storm_warning_km": p.get("storm_warning_km"),
                        } for p in pts]
            elif tid.startswith("jtwc_"):
                sid = tid[5:]
                storm = self.main_ui.jtwc_storms.get(sid)
                if storm:
                    pts = storm.get("track_points", [])
                    if pts:
                        t["points"] = [{
                            "lon": p["lon"], "lat": p["lat"],
                            "advanced_hours": p.get("advanced_hours"),
                            "datetime": self.main_ui._format_jtwc_dtg(storm.get("issued_dtg", ""), p.get("advanced_hours")),
                            "intensity": p.get("intensity"),
                            "intensity_category": p.get("intensity_category"),
                            "wind_radii": p.get("wind_radii", {}),
                        } for p in pts]
                        t["issued_dtg"] = storm.get("issued_dtg", "")
            elif tid.startswith("pagasa_"):
                sid = tid[7:]
                storm = self.main_ui.pagasa_storms.get(sid)
                if storm:
                    pts = storm.get("track_points", [])
                    if pts:
                        t["points"] = [{
                            "lon": p["lon"], "lat": p["lat"],
                            "datetime": p.get("datetime", ""),
                            "intensity": p.get("intensity"),
                            "intensity_category": p.get("intensity_category"),
                            "cyclone_type": p.get("cyclone_type"),
                            "radius_km": p.get("radius_km"),
                        } for p in pts]

        _save_tracks_to_disk(self.main_ui.tracks)
        self.main_ui._refresh_tracks_list()
        self.main_ui._refresh_nhc_storm_combo()
        if hasattr(self.main_ui, '_refresh_jma_storm_combo'):
            self.main_ui._refresh_jma_storm_combo()
        if hasattr(self.main_ui, '_refresh_jtwc_storm_combo'):
            self.main_ui._refresh_jtwc_storm_combo()
        if hasattr(self.main_ui, '_refresh_pagasa_storm_combo'):
            self.main_ui._refresh_pagasa_storm_combo()
        if hasattr(self.main_ui, '_refresh_cwa_storm_combo'):
            self.main_ui._refresh_cwa_storm_combo()
        if hasattr(self.main_ui, 'update_overlays'):
            self.main_ui.update_overlays()

        agency_tracker.mark_updated("nhc")
        agency_tracker.mark_updated("jma")
        agency_tracker.mark_updated("jtwc")
        agency_tracker.mark_updated("pagasa")
        agency_tracker.mark_updated("cwa")
        self.main_ui.fc_status_label.setText("Forecast tracks updated.")
        self.main_ui.log("Forecast track download complete.")



    def _on_nhc_progress(self, msg):
        self.main_ui.nhc_status_label.setText(msg)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()



    @staticmethod
    def _is_placeholder_name(name):
        from src.core.helpers import _is_placeholder_name as _canonical_is_placeholder_name
        return _canonical_is_placeholder_name(name)



    def _on_nhc_storm_downloaded(self, data):
        storm_id = data.get("storm_id", "")
        storm_name = data.get("storm_name", "Unknown")
        self.main_ui.nhc_storms[storm_id] = data

        track_points = data.get("track_points", [])
        if track_points:
            entry = {
                "id": f"nhc_{storm_id}",
                "name": f"{storm_name} NHC Forecast",
"type": data.get("classification", "Tropical Storm"),
                "year": int(storm_id[-4:]) if len(storm_id) >= 4 and storm_id[-4:].isdigit() else 2026,
                "basin": "East Pacific" if storm_id.startswith("ep") else "Atlantic" if storm_id.startswith("al") else "Central Pacific",
                "notes": f"NHC advisory #{data.get('advisory_number', '')} -- downloaded from nhc.noaa.gov",
                "color": "#00BFFF" if storm_id.startswith("ep") else "#FF6B35" if storm_id.startswith("al") else "#FFD700",
                "visible": False,
                "add_to_infobox": False,
                "display_options": {
                    "show_track_line": True, "show_points": True, "show_cone": True,
                    "show_wind_radii": True, "show_best_track": True,
                    "show_labels": True, "show_label_name": True, "show_label_time": True,
                    "show_label_speed": True, "show_label_category": True,
                },
                "points": [{
                    "lon": p["lon"],
                    "lat": p["lat"],
                    "datetime": p.get("datetime", ""),
                    "intensity": p.get("intensity"),
                    "intensity_category": p.get("intensity_category"),
                } for p in track_points],
            }
            existing_ids = {t["id"] for t in self.main_ui.tracks}
            if entry["id"] not in existing_ids:
                self.main_ui.tracks.append(entry)
                _save_tracks_to_disk(self.main_ui.tracks)
                self.main_ui.log(f"NHC track added: {storm_name} ({len(track_points)} pts)")
            else:
                # Update existing entry if name changed from placeholder to real name
                new_storm_name = storm_name
                for t in self.main_ui.tracks:
                    if t["id"] == entry["id"]:
                        old_name = t.get("name", "")
                        old_storm = old_name.replace(" NHC Forecast", "")
                        if old_storm != new_storm_name and self.main_ui._is_placeholder_name(old_storm) and not self.main_ui._is_placeholder_name(new_storm_name):
                            self.main_ui.tracks = [x for x in self.main_ui.tracks if x["id"] != entry["id"]]
                            self.main_ui.tracks.append(entry)
                            _save_tracks_to_disk(self.main_ui.tracks)
                            self.main_ui.log(f"NHC track renamed: {old_storm} -> {new_storm_name} ({storm_id})")
                        break

        # Cache metadata to disk for future startup loading
        try:
            import json as _json
            nhc_dir = nhc.get_nhc_data_dir()
            meta_path = nhc_dir / storm_id / f"{storm_id}_meta.json"
            exclude_keys = {'cone_polygons', 'track_points', 'best_track_points', 'best_track_line',
                           'wind_radii_initial', 'wind_radii_forecast'}
            meta = {k: v for k, v in data.items() if k not in exclude_keys}
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            with open(meta_path, 'w') as _f:
                _json.dump(meta, _f, indent=2, default=str)
        except Exception:
            pass

        self.main_ui._refresh_tracks_list()
        self.main_ui._refresh_nhc_storm_combo()
        if hasattr(self.main_ui, 'update_overlays'):
            self.main_ui.update_overlays()



    def _load_nhc_from_disk(self):
        """Load previously downloaded NHC storms from data/nhc_data/."""
        import json as _json
        try:
            nhc_dir = nhc.get_nhc_data_dir()
            for storm_dir in sorted(nhc_dir.iterdir()):
                if not storm_dir.is_dir() or storm_dir.name == "archived":
                    continue
                storm_id = storm_dir.name
                if not storm_id[:2].isalpha() or not storm_id[2:].isdigit():
                    continue
                json_path = storm_dir / f"{storm_id}_meta.json"

                kmz_track = storm_dir / f"{storm_id}_TRACK.kmz"
                kmz_cone = storm_dir / f"{storm_id}_CONE.kmz"

                data = None
                if json_path.exists():
                    try:
                        with open(json_path) as f:
                            data = _json.load(f)
                    except Exception:
                        pass

                if data is None:
                    data = {
                        "storm_id": storm_id,
                        "storm_name": storm_id.upper(),
                        "classification": "",
                        "basin": "EP" if storm_id.startswith("ep") else "AL" if storm_id.startswith("al") else "CP",
                        "track_points": [],
                        "cone_polygons": [],
                        "local_dir": str(storm_dir),
                        "advisory_number": "",
                        "wind_radii_initial": {"34":[],"50":[],"64":[]},
                        "wind_radii_forecast": {"34":[],"50":[],"64":[]},
                        "best_track_points": [],
                        "best_track_line": [],
                        "best_track_kmz": str(storm_dir / f"{storm_id}_best_track.kmz") if (storm_dir / f"{storm_id}_best_track.kmz").exists() else None,
                        "kmz_arrival_earliest": str(storm_dir / f"{storm_id}_earliest_reasonable_toa_34.kmz") if (storm_dir / f"{storm_id}_earliest_reasonable_toa_34.kmz").exists() else None,
                        "kmz_arrival_likely": str(storm_dir / f"{storm_id}_most_likely_toa_34.kmz") if (storm_dir / f"{storm_id}_most_likely_toa_34.kmz").exists() else None,
                        "prob_polygons_34": str(storm_dir / f"{storm_id}_prob34kt.kmz") if (storm_dir / f"{storm_id}_prob34kt.kmz").exists() else None,
                        "prob_polygons_50": str(storm_dir / f"{storm_id}_prob50kt.kmz") if (storm_dir / f"{storm_id}_prob50kt.kmz").exists() else None,
                        "prob_polygons_64": str(storm_dir / f"{storm_id}_prob64kt.kmz") if (storm_dir / f"{storm_id}_prob64kt.kmz").exists() else None,
                    }
                # Always set KMZ paths (may be missing in cached meta.json)
                data["kmz_track"] = str(kmz_track) if kmz_track.exists() else data.get("kmz_track")
                data["kmz_cone"] = str(kmz_cone) if kmz_cone.exists() else data.get("kmz_cone")
                wini = storm_dir / f"{storm_id}_initialradii.kmz"
                data["kmz_wind_initial"] = str(wini) if wini.exists() else data.get("kmz_wind_initial")
                wfcst = storm_dir / f"{storm_id}_forecastradii.kmz"
                data["kmz_wind_forecast"] = str(wfcst) if wfcst.exists() else data.get("kmz_wind_forecast")

                # Load geometry from KMZ files (fallback to cached meta data)
                if kmz_track.exists():
                    data["track_points"] = nhc.NHCDownloader._parse_kmz_track_points(kmz_track)
                else:
                    data["track_points"] = data.get("track_points", [])
                if kmz_cone.exists():
                    data["cone_polygons"] = nhc.NHCDownloader._parse_kmz_cone_polygon(kmz_cone)
                else:
                    data["cone_polygons"] = data.get("cone_polygons", [])

                # Load wind radii from KMZ
                if wini.exists():
                    data["wind_radii_initial"] = nhc.NHCDownloader._parse_kmz_wind_radii(wini)
                if wfcst.exists():
                    data["wind_radii_forecast"] = nhc.NHCDownloader._parse_kmz_wind_radii(wfcst)

                # Best track from KMZ
                bt_kmz = storm_dir / f"{storm_id}_best_track.kmz"
                if bt_kmz.exists():
                    bt_pts, bt_line = nhc.NHCDownloader._parse_kmz_best_track(bt_kmz)
                    if bt_pts:
                        data["best_track_points"] = bt_pts
                    if bt_line:
                        data["best_track_line"] = bt_line

                # Preserve checkbox states from existing track JSON if it exists
                from src.core.helpers import _get_tracks_folder, _load_tracks_from_disk
                existing_track_id = f"nhc_{storm_id}"
                existing_tracks = _load_tracks_from_disk()
                saved_tree_fields = {}
                for et in existing_tracks:
                    if et.get("id") == existing_track_id:
                        if "main_tree" in et:
                            saved_tree_fields["main_tree"] = et["main_tree"]
                        if "sub_tree" in et:
                            saved_tree_fields["sub_tree"] = et["sub_tree"]
                        if "label_tree" in et:
                            saved_tree_fields["label_tree"] = et["label_tree"]
                        if "visible" in et and et.get("visible") is not None:
                            saved_tree_fields["visible"] = et["visible"]
                        if "add_to_infobox" in et:
                            saved_tree_fields["add_to_infobox"] = et["add_to_infobox"]
                        if "color" in et:
                            saved_tree_fields["color"] = et["color"]
                        if "notes" in et:
                            saved_tree_fields["notes"] = et["notes"]
                        break

                self.main_ui.nhc_storms[storm_id] = data
                try:
                    meta = {k: v for k, v in data.items() if k not in ('cone_polygons',)}
                    if not json_path.exists():
                        with open(json_path, 'w') as f:
                            _json.dump(meta, f, indent=2, default=str)
                except Exception:
                    pass

                # Create or update track entry with preserved checkbox states
                self.main_ui._upsert_nhc_track_entry(storm_id, data, saved_tree_fields)

            if self.main_ui.nhc_storms:
                if hasattr(self.main_ui, 'nhc_status_label'):
                    self.main_ui.nhc_status_label.setText(f"Loaded {len(self.main_ui.nhc_storms)} storm(s) from disk")
                self.main_ui._refresh_nhc_storm_combo()
        except Exception as e:
            self.main_ui.log(f"Error loading NHC from disk: {e}")



    def _load_jma_from_disk(self):
        try:
            jma_dir = jma.get_jma_data_dir()
            for storm_dir in sorted(jma_dir.iterdir()):
                if not storm_dir.is_dir():
                    continue
                storm_id = storm_dir.name
                if storm_id == "archived":
                    continue
                meta_path = storm_dir / f"{storm_id}_meta.json"
                if not meta_path.exists():
                    self.main_ui.log(f"JMA skipping orphaned dir: {storm_id}")
                    continue
                with open(meta_path) as f:
                    data = json.load(f)
                data["local_dir"] = str(storm_dir)

                # Migration: if cached storm_name is a raw TC-code, fetch English name from specs
                name = data.get("storm_name", "")
                if name and (name.startswith("TC") or name.isdigit()):
                    tc_id = data.get("tc_id", f"TC{storm_id}")
                    try:
                        spec = jma.fetch_specifications(tc_id)
                        if isinstance(spec, list) and spec:
                            t = spec[0]
                            if isinstance(t, dict) and t.get("part") == "title":
                                nd = t.get("name", {})
                                en = nd.get("en", "") or nd.get("jp", "")
                                if en:
                                    data["storm_name"] = en
                                    with open(meta_path, 'w') as f:
                                        json.dump(data, f, indent=2, default=str)
                                    self.main_ui.log(f"JMA name migration: {storm_id} -> {en}")
                    except Exception:
                        pass

                self.main_ui.jma_storms[storm_id] = data
                self.main_ui.log(f"JMA loaded from disk: {storm_id} ({len(data.get('track_points',[]))} pts)")

                track_id = f"jma_{storm_id}"
                existing = next((t for t in self.main_ui.tracks if t["id"] == track_id), None)
                if existing is None:
                    storm_name = data.get("storm_name", storm_id)
                    track_points = data.get("track_points", [])
                    try:
                        _year = 2000 + int(storm_id[:2])
                    except (ValueError, TypeError):
                        _year = 2026
                    entry = {
                        "id": track_id,
                        "name": f"{storm_name} JMA Forecast",
                        "type": data.get("classification", "Typhoon"),
                        "year": _year,
                        "basin": "Western Pacific",
                        "notes": f"JMA forecast from jma.go.jp ({storm_id})",
                        "color": "#f472b6",
                        "visible": False,
                        "add_to_infobox": False,
                        "display_options": {
                            "show_track_line": True, "show_points": True,
                            "show_cone": True, "cone_mode": "jma_standard",
                            "show_wind_radii": True, "show_best_track": True,
                            "show_labels": True, "show_label_name": True, "show_label_time": True,
                            "show_label_speed": False, "show_label_category": True,
                            "show_hist_path": True, "show_swa": True,
                        },
                        "points": [{
                            "lon": p["lon"], "lat": p["lat"],
                            "datetime": p.get("datetime", ""),
                            "intensity": p.get("intensity"),
                            "intensity_category": p.get("intensity_category"),
                            "pressure": p.get("pressure"),
                            "wind_gust_kt": p.get("wind_gust_kt"),
                            "location": p.get("location"),
                            "course": p.get("course"),
                            "speed_kt": p.get("speed_kt"),
                            "scale": p.get("scale"),
                            "intensity_label": p.get("intensity_label"),
                            "prob_circle_km": p.get("prob_circle_km"),
                            "storm_warning_km": p.get("storm_warning_km"),
                        } for p in track_points],
                    }
                    self.main_ui.tracks.append(entry)
                    self.main_ui.log(f"JMA track auto-generated for: {storm_id}")
            from src.core.helpers import _save_tracks_to_disk
            _save_tracks_to_disk(self.main_ui.tracks)
            if self.main_ui.jma_storms and hasattr(self.main_ui, 'fc_status_label'):
                self.main_ui.fc_status_label.setText(f"JMA: {len(self.main_ui.jma_storms)} storm(s) from disk")
        except Exception as e:
            self.main_ui.log(f"Error loading JMA from disk: {e}")



    def _load_pagasa_from_disk(self):
        try:
            from src.clients.pagasa import get_pagasa_data_dir
            pagasa_dir = get_pagasa_data_dir()
            for storm_dir in sorted(pagasa_dir.iterdir()):
                if not storm_dir.is_dir():
                    continue
                storm_id = storm_dir.name
                meta_path = storm_dir / f"{storm_id}_meta.json"
                if not meta_path.exists():
                    continue
                with open(meta_path) as f:
                    data = json.load(f)
                data["local_dir"] = str(storm_dir)
                self.main_ui.pagasa_storms[storm_id] = data
                self.main_ui.log(f"PAGASA loaded from disk: {storm_id} ({len(data.get('track_points',[]))} pts)")

                track_id = f"pagasa_{storm_id}"
                existing = next((t for t in self.main_ui.tracks if t["id"] == track_id), None)
                if existing is None:
                    storm_name = data.get("storm_name", storm_id)
                    track_points = data.get("track_points", [])
                    entry = {
                        "id": track_id,
                        "name": f"{storm_name} PAGASA",
                        "type": data.get("classification", "Tropical Cyclone"),
                        "year": 2026,
                        "basin": "Western Pacific",
                        "notes": f"PAGASA track data ({storm_id})",
                        "color": "#00E676",
                        "visible": False,
                        "add_to_infobox": False,
                        "display_options": {
                            "show_track_line": True, "show_points": True,
                            "show_cone": True, "cone_mode": "pagasa_standard",
                            "show_wind_radii": False, "show_best_track": False,
                            "show_labels": True, "show_label_name": True, "show_label_time": True,
                            "show_label_speed": False, "show_label_category": True,
                        },
                        "points": [{
                            "lon": p["lon"], "lat": p["lat"],
                            "datetime": p.get("datetime", ""),
                            "intensity": p.get("intensity"),
                            "intensity_category": p.get("intensity_category"),
                            "cyclone_type": p.get("cyclone_type"),
                            "radius_km": p.get("radius_km"),
                        } for p in track_points],
                    }
                    self.main_ui.tracks.append(entry)
                    self.main_ui.log(f"PAGASA track auto-generated for: {storm_id}")
            from src.core.helpers import _save_tracks_to_disk
            _save_tracks_to_disk(self.main_ui.tracks)
            if self.main_ui.pagasa_storms and hasattr(self.main_ui, 'fc_status_label'):
                self.main_ui.fc_status_label.setText(f"PAGASA: {len(self.main_ui.pagasa_storms)} storm(s) from disk")
        except Exception as e:
            self.main_ui.log(f"Error loading PAGASA from disk: {e}")



    def _load_jtwc_from_disk(self):
        try:
            from src.clients.jtwc import get_jtwc_data_dir, _parse_jtwc_kmz
            jtwc_dir = get_jtwc_data_dir()
            for storm_dir in sorted(jtwc_dir.iterdir()):
                if not storm_dir.is_dir():
                    continue
                storm_id = storm_dir.name

                kmz_path = storm_dir / f"{storm_id}.kmz"
                meta_path = storm_dir / f"{storm_id}_meta.json"

                data = None
                if kmz_path.exists():
                    try:
                        data = _parse_jtwc_kmz(kmz_path, output_dir=storm_dir)
                        if data.get("success"):
                            data["storm_id"] = storm_id
                            data["local_dir"] = str(storm_dir)
                            data["file_path"] = str(kmz_path)
                            data["basin"] = storm_id[:2].upper()
                    except Exception:
                        data = None

                if data is None and meta_path.exists():
                    with open(meta_path) as f:
                        data = json.load(f)
                    data["local_dir"] = str(storm_dir)

                if data is None:
                    continue

                self.main_ui.jtwc_storms[storm_id] = data
                self.main_ui.log(f"JTWC loaded from disk: {storm_id} ({len(data.get('track_points',[]))} pts)")

                track_id = f"jtwc_{storm_id}"
                existing = next((t for t in self.main_ui.tracks if t["id"] == track_id), None)
                if existing is None:
                    storm_name = data.get("storm_name", storm_id)
                    track_points = data.get("track_points", [])
                    entry = {
                        "id": track_id,
                        "name": f"{storm_name} JTWC",
                        "type": data.get("classification", "Tropical Cyclone"),
                        "year": 2026,
                        "basin": {"wp": "Western Pacific", "io": "North Indian Ocean", "sh": "Southern Hemisphere", "ep": "Eastern Pacific"}.get(storm_id[:2], "Western Pacific"),
                        "notes": f"JTWC track data ({storm_id})",
                        "color": "#FF4500",
                        "visible": False,
                        "add_to_infobox": False,
                        "display_options": {
                            "show_track_line": True, "show_points": True,
                            "show_cone": True, "cone_mode": "jtwc_standard",
                            "show_wind_radii": True, "show_best_track": False,
                            "show_labels": True, "show_label_name": True, "show_label_time": True,
                            "show_label_speed": True, "show_label_category": True,
                        },
                        "warning_num": data.get("warning_num", ""),
                        "points": [{
                            "lon": p["lon"], "lat": p["lat"],
                            "datetime": p.get("datetime", ""),
                            "intensity": p.get("intensity"),
                            "intensity_category": p.get("intensity_category"),
                        } for p in track_points],
                    }
                    self.main_ui.tracks.append(entry)
                    self.main_ui.log(f"JTWC track auto-generated for: {storm_id}")
            from src.core.helpers import _save_tracks_to_disk
            _save_tracks_to_disk(self.main_ui.tracks)
            if self.main_ui.jtwc_storms and hasattr(self.main_ui, 'fc_status_label'):
                self.main_ui.fc_status_label.setText(f"JTWC: {len(self.main_ui.jtwc_storms)} storm(s) from disk")
        except Exception as e:
            self.main_ui.log(f"Error loading JTWC from disk: {e}")



    def _load_cwa_from_disk(self):
        try:
            from src.clients.cwa import CWAParser, get_cwa_data_dir
            cwa_dir = get_cwa_data_dir()
            if not hasattr(self.main_ui, 'cwa_storms'):
                self.main_ui.cwa_storms = {}
            for storm_dir in sorted(cwa_dir.iterdir()):
                if not storm_dir.is_dir():
                    continue
                storm_id = storm_dir.name
                cwa_path = storm_dir / f"{storm_id}_cwa.json"
                if not cwa_path.exists():
                    continue
                entry = CWAParser.parse(str(cwa_path))
                if not entry:
                    continue
                track_id = f"cwa_{storm_id}"
                entry["id"] = track_id
                entry["name"] = f"{entry.get('name', storm_id)} CWA"
                existing = next((t for t in self.main_ui.tracks if t["id"] == track_id), None)
                if existing is None:
                    pt_count = len(entry.get("points", []))
                    self.main_ui.tracks.append(entry)
                    self.main_ui.log(f"CWA loaded from disk: {storm_id} ({pt_count} pts)")
                    self.main_ui.log(f"CWA track auto-generated for: {storm_id}")
                else:
                    existing["points"] = entry.get("points", [])
                    existing["name"] = entry.get("name", existing["name"])
                self.main_ui.cwa_storms[storm_id] = {
                    "storm_name": entry.get("name", storm_id),
                    "track_points": entry.get("points", []),
                }
            from src.core.helpers import _save_tracks_to_disk
            _save_tracks_to_disk(self.main_ui.tracks)
            if self.main_ui.cwa_storms and hasattr(self.main_ui, 'fc_status_label'):
                self.main_ui.fc_status_label.setText(f"CWA: {len(self.main_ui.cwa_storms)} storm(s) from disk")
        except Exception as e:
            self.main_ui.log(f"Error loading CWA from disk: {e}")

    # -- PAGASA handlers -------------------------------------------------



    def _on_nhc_storm_selected(self, idx):
        self.main_ui._redraw_nhc_only()



    def _on_storm_filter_changed(self, idx):
        txt = self.main_ui.storm_combo.currentText() if hasattr(self.main_ui, 'storm_combo') else "?"
        dat = self.main_ui.storm_combo.currentData() if hasattr(self.main_ui, 'storm_combo') else "?"
        self.main_ui.log(f"Storm filter: idx={idx} text={txt!r} data={dat!r}")

    def _on_label_algo_changed(self, text):
        rev_map = {
            "Polar": "polar", "Bezier": "bezier", "Smart Bezier": "smart_bezier",
            "Greedy": "greedy", "Offset": "offset", "Force": "force",
            "Anneal": "anneal", "MILP": "milp", "Auto": "auto",
            "Railway Bezier": "railway_bezier", "8-Direction": "8direction",
            "Staggered Perp": "staggered_perp", "Anti-Clima V2": "anticlima_v2"
        }
        value = rev_map.get(text, "polar")
        self.main_ui.settings.set("labeling_method", value)
        self.main_ui.log(f"[Label Algo] Changed to {value}")

    def _toggle_forecast_overlays(self, _checked=None):
        sender = self.main_ui.sender() if hasattr(self.main_ui, 'sender') else None
        master_enabled = getattr(self.main_ui, 'forecast_enable_cb', None) and self.main_ui.forecast_enable_cb.isChecked()
        if sender is getattr(self.main_ui, 'forecast_enable_cb', None):
            for cb_name in ['forecast_show_trackline_cb', 'forecast_show_points_cb',
                            'nhc_show_cone_cb', 'nhc_show_wind_cb', 'nhc_show_besttrack_cb',
                            'jma_show_hist_cb', 'jma_show_circle_cb', 'jma_show_swa_cb',
                            'jtwc_show_cone_cb', 'jtwc_show_wind_cb']:
                cb = getattr(self.main_ui, cb_name, None)
                if cb:
                    cb.blockSignals(True)
                    cb.setChecked(master_enabled)
                    cb.setEnabled(master_enabled)
                    cb.blockSignals(False)
            self.main_ui.log(f"[Forecast] master enable={'ON' if master_enabled else 'OFF'}")
        elif sender is getattr(self.main_ui, 'forecast_show_trackline_cb', None):
            pts = getattr(self.main_ui, 'forecast_show_points_cb', None)
            if pts:
                te = self.main_ui.forecast_show_trackline_cb.isChecked()
                pts.blockSignals(True)
                pts.setChecked(te)
                pts.setEnabled(te)
                pts.blockSignals(False)
        self.main_ui._redraw_nhc_only()
        self.main_ui._redraw_jma_only()
        self.main_ui._redraw_jtwc_only()
        self.main_ui._redraw_pagasa_only()



    def _toggle_nhc_overlays(self, _checked=None):
        self.main_ui._toggle_forecast_overlays(_checked)



    def _toggle_jma_overlays(self, _checked=None):
        self.main_ui._toggle_forecast_overlays(_checked)



    def _on_nhc_error(self, msg):
        self.main_ui.nhc_status_label.setText(f"Error: {msg}")
        self.main_ui.log(f"NHC download error: {msg}")



    def _on_nhc_finished(self):
        agency_tracker.mark_updated("nhc")
        sel = self.main_ui.storm_combo.currentData() if hasattr(self.main_ui, 'storm_combo') else "all"
        if sel == "all":
            if agency_tracker.should_update("jma"):
                self.main_ui.fc_status_label.setText("NHC complete. Starting JMA fetch...")
                self.main_ui.log("NHC download finished - starting JMA fetch")
                self.main_ui._fetch_jma_data()
            else:
                self.main_ui.fc_status_label.setText("NHC complete.")
                self.main_ui.log("NHC download finished - JMA skipped (schedule slot already updated)")
                self.main_ui.download_tc_btn.setEnabled(True)
                self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
        else:
            self.main_ui.download_tc_btn.setEnabled(True)
            self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
            self.main_ui.fc_status_label.setText("Download complete.")

    # -- JMA handlers ---------------------------------------------------



    def _fetch_jma_data(self):

        """Fetch forecast data from JMA.

        Downloads tropical cyclone forecast data from Japan
        Meteorological Agency API. Parses forecast positions,
        intensities, and advisory information.

        Returns:
            list[dict]: List of storm forecast data.

        Note:
            JMA provides forecasts for Western Pacific typhoons.
        """
        if getattr(self.main_ui, '_jma_busy', False):
            self.main_ui.log("JMA fetch already in progress, skipping")
            return
        self.main_ui._jma_busy = True
        self.main_ui.download_tc_btn.setEnabled(False)
        self.main_ui.download_tc_btn.setText("Downloading JMA...")
        self.main_ui.fc_status_label.setText("JMA: initializing fetch...")
        self.main_ui._jma_downloader = jma.JMAStormDownloader()
        self.main_ui._jma_thread = QThread()
        self.main_ui._jma_downloader.moveToThread(self.main_ui._jma_thread)
        self.main_ui._jma_downloader.progress.connect(self.main_ui._on_jma_progress)
        self.main_ui._jma_downloader.storm_downloaded.connect(self.main_ui._on_jma_storm_downloaded)
        self.main_ui._jma_downloader.error.connect(self.main_ui._on_jma_error)
        self.main_ui._jma_downloader.finished.connect(self.main_ui._jma_thread.quit)
        self.main_ui._jma_downloader.finished.connect(self.main_ui._jma_downloader.deleteLater)
        self.main_ui._jma_thread.finished.connect(self.main_ui._on_jma_finished)
        self.main_ui._jma_thread.finished.connect(self.main_ui._jma_thread.deleteLater)
        self.main_ui._jma_thread.started.connect(self.main_ui._jma_downloader.run)
        self.main_ui._jma_thread.start()



    def _jma_download_update_for_storm(self, storm_id):
        self.main_ui.fc_status_label.setText(f"JMA: updating {storm_id}...")
        QApplication.processEvents()
        from src.clients import jma
        try:
            tc_id = self.main_ui.jma_storms[storm_id].get("tc_id", f"TC{storm_id}")
            forecast = jma.fetch_forecast(tc_id)
            # Fetch specifications for rich metadata (category, intensity, pressure, etc.)
            spec_data = None
            try:
                spec_data = jma.fetch_specifications(tc_id)
            except Exception as e:
                self.main_ui.log(f"JMA specs fetch failed: {e}")
            spec_lookup = {}
            if isinstance(spec_data, list):
                for sp in spec_data:
                    if not isinstance(sp, dict):
                        continue
                    if sp.get("part") == "title" or not isinstance(sp.get("part"), dict):
                        continue
                    ah = sp.get("advancedHours", 0)
                    spec_lookup[ah] = sp
            storm_data = self.main_ui.jma_storms[storm_id]
            storm_data["track_points"] = []
            storm_data["probability_circles"] = []
            storm_data["storm_warning_areas"] = []
            for entry in forecast:
                if not isinstance(entry, dict):
                    continue
                if entry.get("part") == "title":
                    continue
                center = entry.get("center")
                if center and len(center) >= 2:
                    lat, lon = float(center[0]), float(center[1])
                    ah = entry.get("advancedHours", 0)
                    vt = entry.get("validtime", {})
                    dt_utc = vt.get("UTC", "") if isinstance(vt, dict) else ""
                    # Merge specifications data
                    sp = spec_lookup.get(ah, {})
                    cat_en = None
                    wind_kt = None
                    gust_kt = None
                    pressure_val = None
                    location = None
                    course = None
                    speed_kt = None
                    prob_circle_km = None
                    storm_warning_km = None
                    scale = None
                    intensity_label = None
                    if sp:
                        cat = sp.get("category", {})
                        cat_en = cat.get("en") if isinstance(cat, dict) else None
                        mw = sp.get("maximumWind", {})
                        if isinstance(mw, dict):
                            sus = mw.get("sustained", {})
                            wind_kt = int(sus["kt"]) if isinstance(sus, dict) and sus.get("kt", "").lstrip("-").isdigit() else None
                            gust = mw.get("gust", {})
                            gust_kt = int(gust["kt"]) if isinstance(gust, dict) and gust.get("kt", "").lstrip("-").isdigit() else None
                            if wind_kt == 0 and sus.get("kt") == "-":
                                wind_kt = None
                        pressure_val = sp.get("pressure")
                        loc = sp.get("location", "")
                        location = loc if loc != "-" else None
                        course = sp.get("course", "").replace("-", "") or None
                        spd = sp.get("speed", {})
                        if isinstance(spd, dict):
                            sk = spd.get("kt", "")
                            speed_kt = int(sk) if sk and sk != "-" and sk.lstrip("-").isdigit() else None
                        pcr = sp.get("probabilityCircleRadius", {})
                        if isinstance(pcr, dict):
                            km = pcr.get("km")
                            prob_circle_km = int(km) if km else None
                        sw = sp.get("stormWarning", [])
                        if sw and isinstance(sw, list) and len(sw) > 0:
                            rng = sw[0].get("range", {})
                            rkm = rng.get("km")
                            storm_warning_km = int(rkm) if rkm else None
                        sc = sp.get("scale", "")
                        scale = sc if sc and sc != "-" else None
                        il = sp.get("intensity", "")
                        intensity_label = il if il and il != "-" else None
                    # Category from JMA category in targetTc data, or from specs
                    jma_category = storm_data.get("jma_category")
                    ic = cat_en or (jma_category if ah == 0 else None)
                    from collections import OrderedDict
                    pt = OrderedDict([
                        ("lat", lat), ("lon", lon),
                        ("datetime", dt_utc), ("advanced_hours", ah),
                        ("intensity", wind_kt),
                        ("intensity_category", ic),
                        ("pressure", pressure_val),
                        ("wind_gust_kt", gust_kt),
                        ("location", location),
                        ("course", course),
                        ("speed_kt", speed_kt),
                        ("scale", scale),
                        ("intensity_label", intensity_label),
                        ("prob_circle_km", prob_circle_km),
                        ("storm_warning_km", storm_warning_km),
                    ])
                    storm_data["track_points"].append(pt)
                prob = entry.get("probabilityCircle")
                if prob:
                    storm_data["probability_circles"].append({
                        "radius": prob.get("radius", 0),
                        "tangent": prob.get("tangent", []),
                        "center": center,
                    })
                swa = entry.get("stormWarningArea")
                if swa:
                    storm_data["storm_warning_areas"].append({
                        "arc": swa.get("arc", []),
                        "line": swa.get("line", []),
                    })
            # Save meta
            jma_dir = jma.get_jma_data_dir()
            storm_dir = jma_dir / storm_id
            storm_dir.mkdir(exist_ok=True)
            meta_path = storm_dir / f"{storm_id}_meta.json"
            with open(meta_path, 'w') as f:
                json.dump(storm_data, f, indent=2, default=str)
            # Update track entry
            from src.core.helpers import _save_tracks_to_disk
            for t in self.main_ui.tracks:
                if t.get("id") == f"jma_{storm_id}":
                    t["points"] = [{
                        "lon": p["lon"], "lat": p["lat"],
                        "datetime": p.get("datetime", ""),
                        "intensity": p.get("intensity"),
                        "intensity_category": p.get("intensity_category"),
                        "pressure": p.get("pressure"),
                        "wind_gust_kt": p.get("wind_gust_kt"),
                        "location": p.get("location"),
                        "course": p.get("course"),
                        "speed_kt": p.get("speed_kt"),
                        "scale": p.get("scale"),
                        "intensity_label": p.get("intensity_label"),
                        "prob_circle_km": p.get("prob_circle_km"),
                        "storm_warning_km": p.get("storm_warning_km"),
                    } for p in storm_data.get("track_points", [])]
                    t["display_options"]["cone_mode"] = "jma_standard"
                    # Preserve best track data
                    _bt = storm_data.get("best_track", {})
                    if _bt:
                        t["track"] = _bt.get("track", storm_data.get("track", {}))
                    break
            _save_tracks_to_disk(self.main_ui.tracks)
            self.main_ui._refresh_tracks_list()
            self.main_ui.fc_status_label.setText(f"JMA: {storm_id} updated.")
            self.main_ui._last_overlay_key = None
            if hasattr(self.main_ui, 'update_overlays'):
                self.main_ui.update_overlays()
        except Exception as e:
            self.main_ui.log(f"JMA update failed for {storm_id}: {e}")
            self.main_ui.fc_status_label.setText(f"JMA: update failed for {storm_id}")



    def _on_jma_progress(self, msg):
        self.main_ui.jma_status_label.setText(msg)
        QApplication.processEvents()



    def _on_jma_storm_downloaded(self, data):
        storm_id = data.get("storm_id", "")
        storm_name = data.get("storm_name", storm_id)
        self.main_ui.jma_storms[storm_id] = data
        track_points = data.get("track_points", [])
        try:
            _year = 2000 + int(storm_id[:2])
        except (ValueError, TypeError):
            _year = 2026
        track_id = f"jma_{storm_id}"
        existing = next((t for t in self.main_ui.tracks if t["id"] == track_id), None)
        if existing is not None:
            existing["points"] = [{
                "lon": p["lon"], "lat": p["lat"],
                "datetime": p.get("datetime", ""),
                "intensity": p.get("intensity"),
                "intensity_category": p.get("intensity_category"),
                "pressure": p.get("pressure"),
                "wind_gust_kt": p.get("wind_gust_kt"),
                "location": p.get("location"),
                "course": p.get("course"),
                "speed_kt": p.get("speed_kt"),
                "scale": p.get("scale"),
                "intensity_label": p.get("intensity_label"),
                "prob_circle_km": p.get("prob_circle_km"),
                "storm_warning_km": p.get("storm_warning_km"),
            } for p in track_points]
            existing["display_options"]["cone_mode"] = "jma_standard"
            _best_track = data.get("best_track", {})
            if _best_track:
                existing["track"] = _best_track.get("track", data.get("track", {}))
            self.main_ui.log(f"JMA track updated: {storm_name} ({storm_id}) -- {len(track_points)} pts")
        else:
            _best_track = data.get("best_track", {})
            entry = {
                "id": track_id,
                "name": f"{storm_name} JMA Forecast",
                "type": data.get("classification", "Typhoon"),
                "year": _year,
                "basin": "Western Pacific",
                "notes": f"JMA forecast from jma.go.jp ({storm_id})",
                "color": "#f472b6",
                "visible": False,
                "add_to_infobox": False,
                "display_options": {
                    "show_track_line": True, "show_points": True,
                    "show_cone": True, "cone_mode": "jma_standard",
                    "show_wind_radii": True, "show_best_track": True,
                    "show_labels": True, "show_label_name": True, "show_label_time": True,
                    "show_label_speed": False, "show_label_category": True,
                    "show_hist_path": True, "show_swa": True,
                },
                "points": [{
                    "lon": p["lon"], "lat": p["lat"],
                    "datetime": p.get("datetime", ""),
                    "intensity": p.get("intensity"),
                    "intensity_category": p.get("intensity_category"),
                    "pressure": p.get("pressure"),
                    "wind_gust_kt": p.get("wind_gust_kt"),
                    "location": p.get("location"),
                    "course": p.get("course"),
                    "speed_kt": p.get("speed_kt"),
                    "scale": p.get("scale"),
                    "intensity_label": p.get("intensity_label"),
                    "prob_circle_km": p.get("prob_circle_km"),
                    "storm_warning_km": p.get("storm_warning_km"),
                } for p in track_points],
                "track": _best_track.get("track", data.get("track", {})),
            }
            self.main_ui.tracks.append(entry)
            self.main_ui.log(f"JMA track added: {storm_name} ({storm_id}) -- {len(track_points)} pts")
        _save_tracks_to_disk(self.main_ui.tracks)
        self.main_ui._refresh_tracks_list()
        self.main_ui._refresh_jma_storm_combo()
        self.main_ui._last_overlay_key = None
        if hasattr(self.main_ui, 'update_overlays'):
            self.main_ui.update_overlays()



    def _on_jma_error(self, msg):
        self.main_ui.jma_status_label.setText(f"Error: {msg}")
        self.main_ui.log(f"JMA download error: {msg}")



    def _on_jma_finished(self):
        self.main_ui._jma_busy = False
        agency_tracker.mark_updated("jma")
        sel = self.main_ui.storm_combo.currentData() if hasattr(self.main_ui, 'storm_combo') else "all"
        if sel == "all":
            if agency_tracker.should_update("jtwc"):
                self.main_ui.fc_status_label.setText("JMA complete. Starting JTWC download...")
                self.main_ui.log("JMA download finished - starting JTWC download")
                self.main_ui._fetch_jtwc_data()
            else:
                self.main_ui.fc_status_label.setText("JMA complete.")
                self.main_ui.log("JMA download finished - JTWC skipped (schedule slot already updated)")
                self.main_ui.download_tc_btn.setEnabled(True)
                self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
        else:
            self.main_ui.download_tc_btn.setEnabled(True)
            self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
            self.main_ui.fc_status_label.setText("Download complete.")



    def _refresh_jma_storm_combo(self):
        self.main_ui._refresh_all_storm_combo()



    def _on_jma_storm_selected(self, idx):
        self.main_ui._redraw_jma_only()



    def _refresh_all_storm_combo(self):
        if not hasattr(self.main_ui, 'storm_combo'):
            return
        self.main_ui.storm_combo.blockSignals(True)
        self.main_ui.storm_combo.clear()
        self.main_ui.storm_combo.addItem("ALL STORMS", "all")
        self.main_ui.storm_combo.addItem("All NHC Storms", "nhc:all")
        self.main_ui.storm_combo.addItem("All JMA Storms", "jma:all")
        self.main_ui.storm_combo.addItem("All JTWC Storms", "jtwc:all")
        self.main_ui.storm_combo.addItem("All PAGASA Storms", "pagasa:all")
        self.main_ui.storm_combo.addItem("All CWA Storms", "cwa:all")
        # ATCF combo items disabled
        # self.main_ui.storm_combo.addItem("All ATCF Storms", "atcf:all")
        # self.main_ui.storm_combo.addItem("ATCF Invests Only", "atcf:invest")
        seen = set()
        for sid in sorted(getattr(self.main_ui, 'nhc_storms', {}).keys()):
            name = self.main_ui.nhc_storms[sid].get("storm_name", sid)
            label = f"NHC: {name} ({sid})"
            self.main_ui.storm_combo.addItem(label, f"nhc:{sid}")
            seen.add(sid)
        for sid in sorted(getattr(self.main_ui, 'jma_storms', {}).keys()):
            name = self.main_ui.jma_storms[sid].get("storm_name", "")
            label = f"JMA: {sid} - {name}" if name else f"JMA: {sid}"
            self.main_ui.storm_combo.addItem(label, f"jma:{sid}")
        for sid in sorted(getattr(self.main_ui, 'jtwc_storms', {}).keys()):
            name = self.main_ui.jtwc_storms[sid].get("storm_name", "")
            num = self.main_ui.jtwc_storms[sid].get("storm_num", "")
            bl = {"wp": "W", "io": "B", "sh": "S"}.get(sid[:2], "W")
            suffix = f" ({num}{bl})" if num else ""
            label = f"JTWC: {name}{suffix}" if name else f"JTWC: {sid}"
            self.main_ui.storm_combo.addItem(label, f"jtwc:{sid}")
        for sid in sorted(getattr(self.main_ui, 'pagasa_storms', {}).keys()):
            storm = self.main_ui.pagasa_storms[sid]
            local_name = storm.get("storm_name", sid)
            intl_name = storm.get("intl_name", "")
            if intl_name:
                label = f"PAGASA: {intl_name} {{{local_name}}}"
            else:
                label = f"PAGASA: {local_name}"
            self.main_ui.storm_combo.addItem(label, f"pagasa:{sid}")
        for sid in sorted(getattr(self.main_ui, 'cwa_storms', {}).keys()):
            name = self.main_ui.cwa_storms[sid].get("storm_name", sid)
            label = f"CWA: {name} ({sid})"
            self.main_ui.storm_combo.addItem(label, f"cwa:{sid}")
        # ATCF individual storm items disabled
        # for storm in getattr(self.main_ui, 'atcf_storms', []):
        #     sid = storm.get("storm_id", "").lower()
        #     name = storm.get("storm_name", sid)
        #     cat = storm.get("category", "")
        #     wind = storm.get("max_winds_kt")
        #     wind_str = f", {wind} kt" if wind else ""
        #     label = f"ATCF: {name} ({cat}{wind_str})"
        #     self.main_ui.storm_combo.addItem(label, f"atcf:{sid}")
        items = [self.main_ui.storm_combo.itemText(i) for i in range(self.main_ui.storm_combo.count())]
        self.main_ui.log(f"Storm combo items: {items}")
        self.main_ui.storm_combo.blockSignals(False)



    def _fetch_jtwc_data(self):
        if getattr(self.main_ui, '_jtwc_busy', False):
            self.main_ui.log("JTWC fetch already in progress, skipping")
            return
        self.main_ui._jtwc_busy = True
        self.main_ui.download_tc_btn.setEnabled(False)
        self.main_ui.download_tc_btn.setText("Downloading JTWC...")
        self.main_ui.fc_status_label.setText("JTWC: initializing download...")
        from src.clients.jtwc import JTWCStormDownloader
        self.main_ui._jtwc_downloader = JTWCStormDownloader()
        self.main_ui._jtwc_thread = QThread()
        self.main_ui._jtwc_downloader.moveToThread(self.main_ui._jtwc_thread)
        self.main_ui._jtwc_downloader.progress.connect(self.main_ui._on_jtwc_progress)
        self.main_ui._jtwc_downloader.storm_downloaded.connect(self.main_ui._on_jtwc_storm_downloaded)
        self.main_ui._jtwc_downloader.error.connect(self.main_ui._on_jtwc_error)
        self.main_ui._jtwc_downloader.finished.connect(self.main_ui._jtwc_thread.quit)
        self.main_ui._jtwc_downloader.finished.connect(self.main_ui._jtwc_downloader.deleteLater)
        self.main_ui._jtwc_thread.finished.connect(self.main_ui._on_jtwc_finished)
        self.main_ui._jtwc_thread.finished.connect(self.main_ui._jtwc_thread.deleteLater)
        self.main_ui._jtwc_thread.started.connect(self.main_ui._jtwc_downloader.run)
        self.main_ui._jtwc_thread.start()



    def _on_jtwc_progress(self, msg):
        self.main_ui.jtwc_status_label.setText(msg)
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()



    def _format_jtwc_dtg(self, dtg, advanced_hours):
        """Format a JTWC DTG + hours offset into DDHHMMZ string."""
        if not dtg or not dtg.endswith('Z') or advanced_hours is None:
            return f"+{advanced_hours}h" if advanced_hours else ""
        try:
            dd = int(dtg[0:2])
            hh = int(dtg[2:4])
            mm = int(dtg[4:6])
        except (ValueError, IndexError):
            return f"+{advanced_hours}h"
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        try:
            base = datetime(now.year, now.month, dd, hh, mm, tzinfo=timezone.utc)
        except ValueError:
            if now.month == 1:
                base = datetime(now.year - 1, 12, dd, hh, mm, tzinfo=timezone.utc)
            else:
                base = datetime(now.year, now.month - 1, dd, hh, mm, tzinfo=timezone.utc)
        result = base + timedelta(hours=advanced_hours)
        return result.strftime("%d%H%M") + "Z"



    def _on_jtwc_storm_downloaded(self, data):
        storm_id = data.get("storm_id", "")
        if not hasattr(self.main_ui, 'jtwc_storms'):
            self.main_ui.jtwc_storms = {}
        self.main_ui.jtwc_storms[storm_id] = data

        # Save meta JSON with warning_num for disk persistence
        try:
            from src.clients.jtwc import get_jtwc_data_dir
            _jtwc_dir = get_jtwc_data_dir()
            _storm_dir = _jtwc_dir / storm_id
            _storm_dir.mkdir(parents=True, exist_ok=True)
            _meta_path = _storm_dir / f"{storm_id}_meta.json"
            _meta = dict(data)
            _exclude = {'track_points', 'best_track_points', 'best_track_line', 'wind_radii_polygons', 'danger_swath', 'track_line'}
            for _k in _exclude:
                _meta.pop(_k, None)
            with open(_meta_path, 'w') as _f:
                json.dump(_meta, _f, indent=2, default=str)
        except Exception:
            pass

        track_points = data.get("track_points", [])
        if track_points:
            entry = {
                "id": f"jtwc_{storm_id}",
                "name": f"JTWC {storm_id.upper()}",
                "type": data.get("classification", "Tropical Cyclone"),
                "year": 2000 + int(storm_id[-2:]),
                "basin": {"wp": "Western Pacific", "io": "North Indian Ocean", "sh": "Southern Hemisphere", "ep": "Eastern Pacific"}.get(storm_id[:2], "Western Pacific"),
                "notes": f"JTWC products -- downloaded from metoc.navy.mil/jtwc",
                "color": "#FF4500",
                "visible": False,
                "add_to_infobox": False,
                "display_options": {
                    "show_track_line": True, "show_points": True, "show_cone": True,
                    "show_wind_radii": True, "show_best_track": True,
                    "show_labels": True, "show_label_name": True, "show_label_time": True,
                    "show_label_speed": True, "show_label_category": True,
                },
                "issued_dtg": data.get("issued_dtg", ""),
                "warning_num": data.get("warning_num", ""),
                "points": [{
                    "lon": p["lon"],
                    "lat": p["lat"],
                    "advanced_hours": p.get("advanced_hours"),
                    "datetime": self.main_ui._format_jtwc_dtg(data.get("issued_dtg", ""), p.get("advanced_hours")),
                    "intensity": p.get("intensity"),
                    "intensity_category": p.get("intensity_category"),
                    "wind_radii": p.get("wind_radii", {}),
                } for p in track_points],
            }
            if not hasattr(self.main_ui, 'tracks'):
                self.main_ui.tracks = []
            existing_ids = {t["id"] for t in self.main_ui.tracks}
            if entry["id"] not in existing_ids:
                self.main_ui.tracks.append(entry)
                from src.core.helpers import _save_tracks_to_disk
                _save_tracks_to_disk(self.main_ui.tracks)
                self.main_ui.log(f"JTWC track added: {storm_id} ({len(track_points)} pts)")
            else:
                for i, t in enumerate(self.main_ui.tracks):
                    if t["id"] == entry["id"]:
                        saved_dopts = t.get("display_options", {})
                        self.main_ui.tracks[i] = entry
                        self.main_ui.tracks[i]["display_options"] = saved_dopts
                        break
                from src.core.helpers import _save_tracks_to_disk
                _save_tracks_to_disk(self.main_ui.tracks)

        self.main_ui._refresh_jtwc_storm_combo()



    def _on_jtwc_error(self, msg):
        self.main_ui.jtwc_status_label.setText(f"Error: {msg}")
        self.main_ui.log(f"JTWC download error: {msg}")



    def _on_jtwc_finished(self):
        self.main_ui._jtwc_busy = False
        agency_tracker.mark_updated("jtwc")
        sel = self.main_ui.storm_combo.currentData() if hasattr(self.main_ui, 'storm_combo') else "all"
        if sel == "all":
            if agency_tracker.should_update("pagasa"):
                self.main_ui.fc_status_label.setText("JTWC complete. Starting PAGASA fetch...")
                self.main_ui.log("JTWC download finished - starting PAGASA download")
                self.main_ui._fetch_pagasa_data()
            else:
                self.main_ui.fc_status_label.setText("JTWC complete.")
                self.main_ui.log("JTWC download finished - PAGASA skipped (schedule slot already updated)")
                self.main_ui.download_tc_btn.setEnabled(True)
                self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
        else:
            self.main_ui.download_tc_btn.setEnabled(True)
            self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
            self.main_ui.fc_status_label.setText("Download complete.")



    def _refresh_jtwc_storm_combo(self):
        self.main_ui._refresh_all_storm_combo()



    def _refresh_pagasa_storm_combo(self):
        self.main_ui._refresh_all_storm_combo()



    def _refresh_cwa_storm_combo(self):
        self.main_ui._refresh_all_storm_combo()



    def _fetch_pagasa_data(self):
        if getattr(self.main_ui, '_pagasa_busy', False):
            self.main_ui.log("PAGASA fetch already in progress, skipping")
            return
        self.main_ui._pagasa_busy = True
        self.main_ui.download_tc_btn.setEnabled(False)
        self.main_ui.download_tc_btn.setText("Downloading PAGASA...")
        self.main_ui.fc_status_label.setText("PAGASA: initializing fetch...")
        from src.clients.pagasa import PAGASAStormDownloader
        self.main_ui._pagasa_downloader = PAGASAStormDownloader()
        self.main_ui._pagasa_thread = QThread()
        self.main_ui._pagasa_downloader.moveToThread(self.main_ui._pagasa_thread)
        self.main_ui._pagasa_downloader.progress.connect(self.main_ui._on_pagasa_progress)
        self.main_ui._pagasa_downloader.storm_downloaded.connect(self.main_ui._on_pagasa_storm_downloaded)
        self.main_ui._pagasa_downloader.error.connect(self.main_ui._on_pagasa_error)
        self.main_ui._pagasa_downloader.finished.connect(self.main_ui._pagasa_thread.quit)
        self.main_ui._pagasa_downloader.finished.connect(self.main_ui._pagasa_downloader.deleteLater)
        self.main_ui._pagasa_thread.finished.connect(self.main_ui._on_pagasa_finished)
        self.main_ui._pagasa_thread.finished.connect(self.main_ui._pagasa_thread.deleteLater)
        self.main_ui._pagasa_thread.started.connect(self.main_ui._pagasa_downloader.run)
        self.main_ui._pagasa_thread.start()



    def _on_pagasa_progress(self, msg):
        self.main_ui.pagasa_status_label.setText(msg)
        QApplication.processEvents()



    def _on_pagasa_storm_downloaded(self, data):
        storm_id = data.get("storm_id", "")
        storm_name = data.get("storm_name", storm_id)
        
        from src.core.helpers import _is_permanently_archived, _get_archived_pagasa_folder, _mark_permanently_archived, _get_permanent_archive_info
        from datetime import datetime, timezone
        
        if _is_permanently_archived(storm_id, 'pagasa'):
            archive_info = _get_permanent_archive_info(storm_id, 'pagasa')
            archived_track_points = []
            archived_latest_dt = None
            
            arch_pagasa = _get_archived_pagasa_folder()
            archived_meta = arch_pagasa / storm_id / f"{storm_id}_meta.json"
            if archived_meta.exists():
                try:
                    import json
                    with open(archived_meta, 'r') as f:
                        archived_data = json.load(f)
                    archived_track_points = archived_data.get("track_points", [])
                    from src.clients.pagasa import get_latest_forecast_datetime
                    archived_latest_dt = get_latest_forecast_datetime(archived_track_points)
                except Exception:
                    pass
            
            new_track_points = data.get("track_points", [])
            from src.clients.pagasa import get_latest_forecast_datetime
            new_latest_dt = get_latest_forecast_datetime(new_track_points)
            
            if archived_latest_dt and new_latest_dt:
                if new_latest_dt <= archived_latest_dt:
                    self.main_ui.log(f"PAGASA: {storm_id} already permanently archived with newer/same data, sending to archive")
                    import shutil
                    from src.clients.pagasa import get_pagasa_data_dir
                    pagasa_dir = get_pagasa_data_dir()
                    storm_dir = pagasa_dir / storm_id
                    if storm_dir.exists():
                        dest = arch_pagasa / storm_id
                        try:
                            shutil.move(str(storm_dir), str(dest))
                            self.main_ui.log(f"PAGASA: {storm_id} moved to archive (duplicate of archived data)")
                        except Exception as e:
                            self.main_ui.log(f"PAGASA archive error for {storm_id}: {e}")
                    return
                else:
                    self.main_ui.log(f"PAGASA: {storm_id} has genuinely new data (new: {new_latest_dt}, archived: {archived_latest_dt}), allowing download")
                    self.main_ui.pagasa_storms[storm_id] = data
            else:
                self.main_ui.log(f"PAGASA: {storm_id} permanently archived but allowing new data")
                self.main_ui.pagasa_storms[storm_id] = data
        else:
            self.main_ui.pagasa_storms[storm_id] = data
        
        track_points = data.get("track_points", [])

        track_id = f"pagasa_{storm_id}"
        existing = next((t for t in self.main_ui.tracks if t["id"] == track_id), None)
        if existing is not None:
            existing["points"] = [{
                "lon": p["lon"], "lat": p["lat"],
                "datetime": p.get("datetime", ""),
                "intensity": p.get("intensity"),
                "intensity_category": p.get("intensity_category"),
                "cyclone_type": p.get("cyclone_type"),
                "radius_km": p.get("radius_km"),
            } for p in track_points]
            self.main_ui.log(f"PAGASA track updated: {storm_name} ({storm_id}) -- {len(track_points)} pts")
        else:
            entry = {
                "id": track_id,
                "name": f"{storm_name} PAGASA",
                "type": data.get("classification", "Tropical Cyclone"),
                "year": 2026,
                "basin": "Western Pacific",
                "notes": f"PAGASA track data ({storm_id})",
                "color": "#00E676",
                "visible": False,
                "add_to_infobox": False,
                "display_options": {
                    "show_track_line": True, "show_points": True,
                    "show_cone": True, "cone_mode": "pagasa_standard",
                    "show_wind_radii": False, "show_best_track": False,
                    "show_labels": True, "show_label_name": True, "show_label_time": True,
                    "show_label_speed": False, "show_label_category": True,
                },
                "points": [{
                    "lon": p["lon"], "lat": p["lat"],
                    "datetime": p.get("datetime", ""),
                    "intensity": p.get("intensity"),
                    "intensity_category": p.get("intensity_category"),
                    "cyclone_type": p.get("cyclone_type"),
                    "radius_km": p.get("radius_km"),
                } for p in track_points],
            }
            self.main_ui.tracks.append(entry)
            self.main_ui.log(f"PAGASA track added: {storm_name} ({storm_id}) -- {len(track_points)} pts")
        from src.core.helpers import _save_tracks_to_disk
        _save_tracks_to_disk(self.main_ui.tracks)
        self.main_ui._refresh_tracks_list()
        self.main_ui._refresh_pagasa_storm_combo()
        self.main_ui._last_overlay_key = None
        if hasattr(self.main_ui, 'update_overlays'):
            self.main_ui.update_overlays()



    def _on_pagasa_error(self, msg):
        self.main_ui.pagasa_status_label.setText(f"Error: {msg}")
        self.main_ui.log(f"PAGASA download error: {msg}")



    def _on_pagasa_finished(self):
        self.main_ui._pagasa_busy = False
        agency_tracker.mark_updated("pagasa")
        self.main_ui.download_tc_btn.setEnabled(True)
        self.main_ui.download_tc_btn.setText("Download Latest TC Updates")
        sel = self.main_ui.storm_combo.currentData() if hasattr(self.main_ui, 'storm_combo') else "all"
        if sel in ("all", "pagasa:all"):
            self.main_ui.fc_status_label.setText("NHC + JMA + JTWC + PAGASA + CWA download complete.")
        else:
            self.main_ui.fc_status_label.setText("Download complete.")



    def _sync_pagasa_on_startup(self):
        try:
            from src.clients.pagasa import fetch_cyclone_data, parse_storm_name, get_initial_forecast_datetime
            self.main_ui.log("PAGASA sync: checking for updates...")
            self.main_ui.pagasa_status_label.setText("Checking PAGASA for updates...")
            raw = fetch_cyclone_data()
            if not raw or not isinstance(raw, list):
                self.main_ui.pagasa_status_label.setText("PAGASA sync: no active storms")
                return
            active_ids = set()
            for storm in raw:
                raw_name = storm.get("cyclone_name", "")
                local_name, intl_name = parse_storm_name(raw_name)
                sid = local_name.lower().replace(" ", "_") or intl_name.lower().replace(" ", "_")
                if sid:
                    active_ids.add(sid)
            cached_ids = set(getattr(self.main_ui, 'pagasa_storms', {}).keys())
            stale = cached_ids - active_ids
            
            # Archive PAGASA storms whose initial point is 1+ day old
            from datetime import datetime, timezone, timedelta
            from src.core.helpers import _is_permanently_archived, _mark_permanently_archived
            one_day_ago = datetime.now(timezone.utc) - timedelta(days=1)
            expired_ids = set()
            
            for sid in cached_ids:
                storm_data = self.main_ui.pagasa_storms.get(sid, {})
                track_points = storm_data.get("track_points", [])
                initial_dt = get_initial_forecast_datetime(track_points)
                
                if initial_dt is None:
                    continue
                
                if initial_dt < one_day_ago:
                    expired_ids.add(sid)
                    self.main_ui.log(f"PAGASA: {sid} initial point 1+ day old (initial: {initial_dt})")
            
            if expired_ids:
                self.main_ui.log(f"PAGASA sync: {len(expired_ids)} expired storm(s): {', '.join(expired_ids)}")
                import shutil
                from src.core.helpers import _get_archived_pagasa_folder, _archive_storm_dir, _save_tracks_to_disk
                old_tracks_action = self.main_ui.settings.get("old_tracks", "Archive")
                from src.clients.pagasa import get_pagasa_data_dir
                pagasa_dir = get_pagasa_data_dir()
                
                for sid in expired_ids:
                    self.main_ui.pagasa_storms.pop(sid, None)
                    track_id = f"pagasa_{sid}"
                    self.main_ui.tracks = [t for t in self.main_ui.tracks if t.get("id") != track_id]
                    
                    if old_tracks_action == "Archive":
                        if _archive_storm_dir(pagasa_dir / sid, _get_archived_pagasa_folder(), log=self.main_ui.log):
                            _mark_permanently_archived(sid, 'pagasa', 'expired')
                            self.main_ui.log(f"PAGASA: {sid} permanently archived (initial point 1+ day old)")
                
                _save_tracks_to_disk(self.main_ui.tracks)
            
            if stale:
                self.main_ui.log(f"PAGASA sync: {len(stale)} stale storm(s): {', '.join(stale)}")
                import shutil
                from src.core.helpers import _get_archived_pagasa_folder, _archive_storm_dir, _save_tracks_to_disk
                old_tracks_action = self.main_ui.settings.get("old_tracks", "Archive")
                from src.clients.pagasa import get_pagasa_data_dir
                pagasa_dir = get_pagasa_data_dir()
                for sid in stale:
                    if sid in expired_ids:
                        continue
                    self.main_ui.pagasa_storms.pop(sid, None)
                    track_id = f"pagasa_{sid}"
                    self.main_ui.tracks = [t for t in self.main_ui.tracks if t.get("id") != track_id]
                    if old_tracks_action == "Archive":
                        _archive_storm_dir(pagasa_dir / sid, _get_archived_pagasa_folder(), log=self.main_ui.log)
                _save_tracks_to_disk(self.main_ui.tracks)
            new_ids = active_ids - cached_ids
            if new_ids or not cached_ids:
                if agency_tracker.should_update("pagasa"):
                    self.main_ui.log(f"PAGASA sync: {len(new_ids)} new storm(s), triggering download")
                    self.main_ui.pagasa_status_label.setText("Downloading PAGASA data...")
                    self.main_ui._download_all_tc_updates(_from_startup=True)
                else:
                    self.main_ui.log(f"PAGASA sync: {len(new_ids)} new storm(s) but schedule slot already updated, deferring")
                    self.main_ui.pagasa_status_label.setText("PAGASA: deferred (slot already updated)")
            else:
                self.main_ui.pagasa_status_label.setText("PAGASA tracks up to date.")
                self.main_ui.log("PAGASA sync: up to date")
        except Exception as e:
            self.main_ui.log(f"PAGASA sync error: {e}")
            self.main_ui.pagasa_status_label.setText("PAGASA sync skipped (offline?)")

    # -- ATCF handlers (implemented in UI.py: _auto_refresh_atcf / _on_atcf_fetched) ----



    # ------------------------------------------------------------------ #
    #  Alert system (weather.gov NWS alerts + sound notifications)
    # ------------------------------------------------------------------ #



    def _sync_nhc_storms_on_startup(self):
        try:
            from src.clients import nhc
            self.main_ui.nhc_status_label.setText("Checking NHC for updates...")
            self.main_ui.log("NHC sync: fetching active storms...")
            active = nhc.fetch_active_storms()
            active_ids = set()
            for s in active:
                sid = s.get("id", "").lower()
                if not sid:
                    continue
                active_ids.add(sid)
            nhc_dir = nhc.get_nhc_data_dir()
            import shutil
            old_tracks_action = self.main_ui.settings.get("old_tracks", "Archive")
            existing_sids = set()
            for storm_dir in nhc_dir.iterdir():
                if not storm_dir.is_dir() or storm_dir.name == "archived":
                    continue
                existing_sids.add(storm_dir.name)
            stale = existing_sids - active_ids
            if stale:
                self.main_ui.log(f"NHC sync: {len(stale)} stale storm(s): {', '.join(stale)}")
                for sid in stale:
                    storm_dir = nhc_dir / sid
                    track_id = f"nhc_{sid}"
                    if old_tracks_action == "Archive":
                        from src.core.helpers import _get_archived_tracks_folder, _get_archived_nhc_folder, _archive_storm_dir
                        arch_tracks = _get_archived_tracks_folder()
                        for tj in arch_tracks.parent.glob(f"*{track_id.replace('nhc_','')}*.json"):
                            try:
                                if not (arch_tracks / tj.name).exists():
                                    shutil.move(str(tj), str(arch_tracks / tj.name))
                            except Exception:
                                pass
                        _archive_storm_dir(storm_dir, _get_archived_nhc_folder(), log=self.main_ui.log)
                    else:
                        import shutil
                        try:
                            shutil.rmtree(storm_dir)
                        except Exception:
                            pass
                    self.main_ui.tracks = [t for t in self.main_ui.tracks if t.get("id") != track_id]
                    self.main_ui.nhc_storms.pop(sid, None)
                from src.core.helpers import _save_tracks_to_disk
                _save_tracks_to_disk(self.main_ui.tracks)

            common = existing_sids & active_ids
            if common:
                active_map = {s.get("id", "").lower(): s for s in active}
                for sid in common:
                    active_storm = active_map.get(sid)
                    if not active_storm:
                        continue
                    new_name = active_storm.get("name", "")
                    new_class = active_storm.get("classification", "")
                    cached = self.main_ui.nhc_storms.get(sid, {})
                    old_name = cached.get("storm_name", "")
                    if new_name and new_name != old_name:
                        self.main_ui.log(f"NHC sync: storm {sid} renamed '{old_name}' ? '{new_name}'")
                        cached["storm_name"] = new_name
                        cached["classification"] = new_class
                        json_path = nhc_dir / sid / f"{sid}_meta.json"
                        if json_path.exists():
                            try:
                                import json as _json
                                meta = _json.loads(json_path.read_text(encoding='utf-8'))
                                meta["storm_name"] = new_name
                                meta["classification"] = new_class
                                json_path.write_text(_json.dumps(meta, indent=2, default=str))
                            except Exception:
                                pass
                        self.main_ui._upsert_nhc_track_entry(sid, cached)
                        _save_tracks_to_disk(self.main_ui.tracks)

            new_ids = active_ids - existing_sids
            if new_ids:
                if agency_tracker.should_update("nhc"):
                    self.main_ui.log(f"NHC sync: {len(new_ids)} new storm(s): {', '.join(new_ids)}")
                    self.main_ui.nhc_status_label.setText(f"Downloading {len(new_ids)} new NHC storm(s)...")
                    self.main_ui._download_all_tc_updates(_from_startup=True)
                else:
                    self.main_ui.log(f"NHC sync: {len(new_ids)} new storm(s) but schedule slot already updated, deferring")
                    self.main_ui.nhc_status_label.setText("NHC: deferred (slot already updated)")
                    self.main_ui._refresh_tracks_list()
                    self.main_ui._refresh_nhc_storm_combo()
            else:
                self.main_ui._refresh_tracks_list()
                self.main_ui._refresh_nhc_storm_combo()
                self.main_ui.nhc_status_label.setText("NHC tracks up to date.")
        except Exception as e:
            self.main_ui.log(f"NHC sync error: {e}")
            self.main_ui.nhc_status_label.setText("NHC sync skipped (offline?)")



    def _sync_jma_on_startup(self):
        try:
            from src.clients import jma
            self.main_ui.log("JMA sync: checking for updates...")
            self.main_ui.jma_status_label.setText("Checking JMA for updates...")
            target_list = jma.fetch_target_tc()
            if not target_list:
                self.main_ui.jma_status_label.setText("JMA sync: no active storms")
                return
            active_ids = set()
            for storm in target_list:
                tc_id = storm.get("tropicalCyclone", "")
                tn = storm.get("typhoonNumber", "")
                sid = tn if tn and tn.isdigit() else tc_id.replace("TC", "")
                if sid:
                    active_ids.add(sid)
            cached_ids = set(getattr(self.main_ui, 'jma_storms', {}).keys())
            stale = cached_ids - active_ids
            if stale:
                self.main_ui.log(f"JMA sync: {len(stale)} stale storm(s): {', '.join(stale)}")
                import shutil
                from src.core.helpers import _get_archived_jma_folder, _archive_storm_dir, _save_tracks_to_disk
                old_tracks_action = self.main_ui.settings.get("old_tracks", "Archive")
                jma_dir = jma.get_jma_data_dir()
                for sid in stale:
                    self.main_ui.jma_storms.pop(sid, None)
                    track_id = f"jma_{sid}"
                    self.main_ui.tracks = [t for t in self.main_ui.tracks if t.get("id") != track_id]
                    if old_tracks_action == "Archive":
                        _archive_storm_dir(jma_dir / sid, _get_archived_jma_folder(), log=self.main_ui.log)
                _save_tracks_to_disk(self.main_ui.tracks)
            new_ids = active_ids - cached_ids
            needs_dl = (new_ids or not cached_ids) and agency_tracker.should_update("jma")
            if not needs_dl and cached_ids:
                for sid in cached_ids:
                    pts = self.main_ui.jma_storms.get(sid, {}).get("track_points", [])
                    if pts and pts[0].get("intensity_category") == "TY" and pts[0].get("pressure") is None:
                        needs_dl = True
                        break
            if needs_dl:
                self.main_ui.log("JMA sync: triggering download")
                self.main_ui.jma_status_label.setText("Downloading JMA data...")
                self.main_ui._download_all_tc_updates(_from_startup=True)
            else:
                if new_ids:
                    self.main_ui.log(f"JMA sync: {len(new_ids)} new storm(s) but schedule slot already updated, deferring")
                    self.main_ui.jma_status_label.setText("JMA: deferred (slot already updated)")
                else:
                    self.main_ui.jma_status_label.setText("JMA tracks up to date.")
                    self.main_ui.log("JMA sync: up to date")
        except Exception as e:
            self.main_ui.log(f"JMA sync error: {e}")
            self.main_ui.jma_status_label.setText("JMA sync skipped (offline?)")

    



    def _sync_jtwc_on_startup(self):
        try:
            import src.clients.jtwc as jtwc
            from src.clients.jtwc import fetch_active_storm_ids, probe_storm_ids
            self.main_ui.log("JTWC sync: checking for updates...")
            self.main_ui.jtwc_status_label.setText("Checking JTWC for updates...")
            ids = fetch_active_storm_ids()

            # Cross-reference NHC Eastern Pacific storms — the EP bulletin
            # is often blocked (403), but individual storm products are
            # accessible.  If NHC discovered an EP storm, probe JTWC for it.
            nhc_ep_candidates = []
            for sid in getattr(self.main_ui, 'nhc_storms', {}):
                if sid.startswith("ep") and len(sid) >= 6:
                    try:
                        num = sid[2:4]
                        yy = sid[-2:]
                        nhc_ep_candidates.append(f"ep{num}{yy}")
                    except Exception:
                        pass
            if nhc_ep_candidates:
                discovered = probe_storm_ids(nhc_ep_candidates)
                for dsid in discovered:
                    if dsid not in ids:
                        ids.append(dsid)
                        self.main_ui.log(f"JTWC sync: discovered {dsid} via NHC cross-ref")

            if not ids:
                self.main_ui.jtwc_status_label.setText("JTWC sync: no active storms")
                return
            active_ids = set(ids)
            cached_ids = set(getattr(self.main_ui, 'jtwc_storms', {}).keys())
            stale = cached_ids - active_ids
            if stale:
                self.main_ui.log(f"JTWC sync: {len(stale)} stale storm(s): {', '.join(stale)}")
                import shutil
                from src.core.helpers import _get_archived_jtwc_folder, _archive_storm_dir, _save_tracks_to_disk
                old_tracks_action = self.main_ui.settings.get("old_tracks", "Archive")
                jtwc_dir = jtwc.get_jtwc_data_dir()
                for sid in stale:
                    self.main_ui.jtwc_storms.pop(sid, None)
                    track_id = f"jtwc_{sid}"
                    self.main_ui.tracks = [t for t in self.main_ui.tracks if t.get("id") != track_id]
                    if old_tracks_action == "Archive":
                        _archive_storm_dir(jtwc_dir / sid, _get_archived_jtwc_folder(), log=self.main_ui.log)
                _save_tracks_to_disk(self.main_ui.tracks)
            new_ids = active_ids - cached_ids
            if new_ids or not cached_ids:
                if agency_tracker.should_update("jtwc"):
                    self.main_ui.log(f"JTWC sync: {len(new_ids)} new storm(s), triggering download")
                    self.main_ui.jtwc_status_label.setText("Downloading JTWC data...")
                    self.main_ui._download_all_tc_updates(_from_startup=True)
                else:
                    self.main_ui.log(f"JTWC sync: {len(new_ids)} new storm(s) but schedule slot already updated, deferring")
                    self.main_ui.jtwc_status_label.setText("JTWC: deferred (slot already updated)")
            else:
                self.main_ui.jtwc_status_label.setText("JTWC tracks up to date.")
                self.main_ui.log("JTWC sync: up to date")
        except Exception as e:
            self.main_ui.log(f"JTWC sync error: {e}")
            self.main_ui.jtwc_status_label.setText("JTWC sync skipped (offline?)")



    def _sync_cwa_on_startup(self):
        try:
            from src.clients.cwa import fetch_typhoon_tracks, get_cwa_data_dir, CWAParser, extract_tcs
            from src.ui.settings import SettingsManager
            _sm = SettingsManager(str(_src_dir))
            auth_code = _sm.get("cwa_api_key", "")
            if not auth_code:
                self.main_ui.log("CWA sync skipped (no API key configured)")
                return
            self.main_ui.log("CWA sync: checking for updates...")
            raw = fetch_typhoon_tracks(auth_code)
            if not raw:
                self.main_ui.log("CWA sync: API returned empty response")
                return
            if "cwaopendata" not in raw and raw.get("success") != "true":
                self.main_ui.log("CWA sync: API returned unsuccessful response")
                return
            entries = CWAParser._build_entries_from_raw(raw)
            active_ids = set()
            for entry in entries:
                td_no = entry["id"][4:]  # strip "cwa_" prefix
                if td_no:
                    active_ids.add(td_no)
            cached_ids = set(getattr(self.main_ui, 'cwa_storms', {}).keys())
            stale = cached_ids - active_ids
            if stale:
                self.main_ui.log(f"CWA sync: {len(stale)} stale storm(s): {', '.join(stale)}")
                import shutil
                from src.core.helpers import _save_tracks_to_disk, _archive_storm_dir, _get_archived_tracks_folder
                old_tracks_action = self.main_ui.settings.get("old_tracks", "Archive")
                cwa_dir = get_cwa_data_dir()
                for sid in stale:
                    if hasattr(self.main_ui, 'cwa_storms'):
                        self.main_ui.cwa_storms.pop(sid, None)
                    track_id = f"cwa_{sid}"
                    self.main_ui.tracks = [t for t in self.main_ui.tracks if t.get("id") != track_id]
                    if old_tracks_action == "Archive":
                        _archive_storm_dir(cwa_dir / sid, _get_archived_tracks_folder(), log=self.main_ui.log)
                _save_tracks_to_disk(self.main_ui.tracks)
            new_ids = active_ids - cached_ids
            if new_ids or not cached_ids:
                if agency_tracker.should_update("cwa"):
                    self.main_ui.log(f"CWA sync: {len(new_ids)} new storm(s), triggering download")
                    self.main_ui._download_all_tc_updates(_from_startup=True)
                else:
                    self.main_ui.log(f"CWA sync: {len(new_ids)} new storm(s) but schedule slot already updated, deferring")
            else:
                self.main_ui.log("CWA sync: up to date")
        except Exception as e:
            self.main_ui.log(f"CWA sync error: {e}")

    def _refresh_nhc_storm_combo(self):
        names = []
        for sid in sorted(self.main_ui.nhc_storms.keys()):
            name = self.main_ui.nhc_storms[sid].get("storm_name", sid)
            names.append((sid, name))
        self.main_ui._refresh_all_storm_combo()



    def _generate_forecast_map_for_storm(self, storm_id):

        """Generate forecast map for specific storm.

        Creates map visualization showing forecast track with
        intensity cones and position markers. Includes historical
        best-track and model guidance if available.

        Args:
            storm_id (str): Storm identifier.
            agency (str): Source agency ('NHC', 'JMA', etc.).

        Returns:
            QImage: Rendered forecast map.

        Note:
            Uses cone of uncertainty visualization for forecast
            confidence representation.
        """
        storm_data = self.main_ui.nhc_storms.get(storm_id)
        if not storm_data:
            QMessageBox.warning(self.main_ui, "No Data", f"No NHC data for {storm_id}.")
            return
        try:
            import cartopy.crs as ccrs
            import cartopy.feature as cfeature
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
            from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
            from PIL import Image as PILImage, ImageDraw, ImageFont
        except ImportError as e:
            QMessageBox.critical(self.main_ui, "Missing Dependency",
                f"Required: cartopy, matplotlib, PIL.\n\nError: {e}")
            return
        track_pts = storm_data.get("track_points", [])
        if not track_pts:
            QMessageBox.warning(self.main_ui, "No Track Data", f"No track points for {storm_id}.")
            return
        lons = [p["lon"] for p in track_pts if p.get("lon") is not None]
        lats = [p["lat"] for p in track_pts if p.get("lat") is not None]
        if not lons:
            return
        margin = 5.0
        lon_min, lon_max, central_lon = _normalize_lon_extent(lons, margin)
        lat_min = max(min(lats) - margin, -90)
        lat_max = min(max(lats) + margin, 90)
        dpi = 150
        fig_w, fig_h = 12, 10
        fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
        ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
        ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
        ax.add_feature(cfeature.LAND, facecolor='lightgray', edgecolor='gray', linewidth=0.5)
        if storm_id and str(storm_id).startswith("wp"):
            try:
                from src.data.psgc_shapefiles import add_psgc_land_to_axis
                add_psgc_land_to_axis(ax, facecolor='lightgray', edgecolor='gray', linewidth=0.5, zorder=3)
            except Exception:
                pass
        ax.add_feature(cfeature.OCEAN, facecolor='#c8e6f5', alpha=0.5)
        ax.add_feature(cfeature.COASTLINE, edgecolor='gray', linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, edgecolor='gray', linewidth=0.4, linestyle=':')
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.5, color='gray', alpha=0.6, linestyle='--')
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {'size': 7, 'color': 'black'}
        gl.ylabel_style = {'size': 7, 'color': 'black'}
        gl.xformatter = LONGITUDE_FORMATTER
        gl.yformatter = LATITUDE_FORMATTER
        storm_name = storm_data.get("storm_name", storm_id)
        fcst_prefs = self.main_ui.settings.get("forecast_preferences", {})
        forecast_layout = fcst_prefs.get("forecast_layout", "monwatch")

        # NHC layout: use tropycal's native rendering
        if forecast_layout == "nhc" and storm_id:
            try:
                from tropycal import realtime as _tc
                _rt = _tc.Realtime()
                _storm = _rt.get_storm(storm_id)
                if _storm is not None:
                    plt.close(fig)
                    _ax = _storm.plot_forecast_realtime(
                        track_labels="fhr_wind_kt", cone_days=5,
                        domain="dynamic_forecast",
                        map_prop={'figsize': (fig_w, fig_h), 'linewidth': 0.6,
                                  'land_color': '#e8e0d8', 'ocean_color': '#deecf4'},
                        prop={'cone_lw': 1.5, 'cone_alpha': 0.15},
                    )
                    _fig = _ax.figure
                    export_dir = self.main_ui.settings.get("export_folder", str(_top_dir / "Exports"))
                    Path(export_dir).mkdir(parents=True, exist_ok=True)
                    safe_name = storm_name.replace(" ", "_").replace("/", "-")
                    fname = f"forecast_{safe_name}_{storm_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    fpath = Path(export_dir) / fname
                    _fig.savefig(str(fpath), bbox_inches='tight', dpi=dpi, format='png')
                    plt.close(_fig)
                    # Add footer
                    try:
                        from PIL import Image as PILImage, ImageDraw, ImageFont
                        map_img = PILImage.open(str(fpath)).convert("RGBA")
                        mw, mh = map_img.size
                        footer_h = max(36, int(mh * 0.04))
                        font_size = max(8, int(footer_h * 0.35))
                        try:
                            font = ImageFont.truetype("consola.ttf", font_size)
                        except Exception:
                            font = ImageFont.load_default()
                        footer_img = PILImage.new("RGBA", (mw, footer_h), (255, 255, 255, 255))
                        draw = ImageDraw.Draw(footer_img)
                        logo_path = _top_dir / "public" / "images" / "Monwatch-LOGO.png"
                        logo_w = 0
                        if logo_path.exists():
                            try:
                                logo = PILImage.open(str(logo_path)).convert("RGBA")
                                logo_h = footer_h - 4
                                lw = int(logo.width * logo_h / logo.height) if logo.height > 0 else logo_h
                                logo = logo.resize((lw, logo_h), PILImage.LANCZOS)
                                footer_img.paste(logo, (4, 2), logo)
                                logo_w = lw + 8
                            except Exception:
                                pass
                        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                        basin = "East Pacific" if storm_id.startswith("ep") else "Atlantic" if storm_id.startswith("al") else "Central Pacific"
                        utc_offset = fcst_prefs.get("utc_offset", 0)
                        utc_label = f"UTC{utc_offset:+d}" if utc_offset != 0 else "UTC"
                        left_text = f"{utc_label} Forecast \u2014 Created {now_utc}  |  {basin}"
                        bbox = draw.textbbox((0, 0), left_text, font=font)
                        th = bbox[3] - bbox[1]
                        draw.text((logo_w + 4, (footer_h - th) // 2), left_text, fill="black", font=font)
                        brand = "MonWatch-UI"
                        bbox2 = draw.textbbox((0, 0), brand, font=font)
                        bw = bbox2[2] - bbox2[0]
                        draw.text((mw - bw - 8, (footer_h - th) // 2), brand, fill="black", font=font)
                        combined = PILImage.new("RGBA", (mw, mh + footer_h), (0, 0, 0, 0))
                        combined.paste(map_img, (0, 0), map_img)
                        combined.paste(footer_img, (0, mh), footer_img)
                        combined.convert("RGB").save(str(fpath), "PNG")
                    except Exception:
                        pass
                    self.main_ui.status_bar.showMessage(f"Forecast map saved: {fpath.name}")
                    self.main_ui.log(f"Forecast map generated (tropycal): {fpath}")
                    return
            except Exception:
                pass

        if forecast_layout in ("pwards", "monwatch"):
            track_lw = 2.0; track_override = '#1a1a2e'
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['bottom'].set_visible(False)
            ax.spines['left'].set_visible(False)
            gl.alpha = 0.2
        elif forecast_layout == "nhc":
            track_lw = 2.5; track_override = '#cc0000'
        else:
            track_lw = None; track_override = None
        track_id = f"nhc_{storm_id}"
        track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id") == track_id), None)
        if track_entry:
            color_hex = track_entry.get("color", "#00BFFF" if storm_id.startswith("ep") else "#FF6B35" if storm_id.startswith("al") else "#FFD700")
        else:
            color_hex = "#00BFFF" if storm_id.startswith("ep") else "#FF6B35" if storm_id.startswith("al") else "#FFD700"
        import matplotlib.colors as mcolors
        if track_override:
            track_color = mcolors.to_rgba(track_override)
        else:
            track_color = mcolors.to_rgba(color_hex)
        used_lw = track_lw if track_lw else 2
        if len(track_pts) >= 2:
            tlons = [p["lon"] for p in track_pts]
            tlats = [p["lat"] for p in track_pts]
            if forecast_layout in ("pwards", "monwatch"):
                glow_color = (0.42, 0.36, 0.91, 0.15)
                ax.plot(tlons, tlats, color=glow_color, linewidth=6,
                        transform=ccrs.PlateCarree(), zorder=4)
                glow_color2 = (0.42, 0.36, 0.91, 0.3)
                ax.plot(tlons, tlats, color=glow_color2, linewidth=3,
                        transform=ccrs.PlateCarree(), zorder=4)
            ax.plot(tlons, tlats, color=track_color, linewidth=used_lw, transform=ccrs.PlateCarree(), zorder=5)
        cone_polys = storm_data.get("cone_polygons", [])
        for poly in cone_polys:
            if poly:
                c_lons = [pt[0] for pt in poly]
                c_lats = [pt[1] for pt in poly]
                ax.fill(c_lons, c_lats, color='blue', alpha=0.07, transform=ccrs.PlateCarree(), zorder=2)
        from src.core.helpers import _top_dir
        sym_dir = _top_dir / "public" / "images" / "symbols"
        cat_order = {"TD":0,"TS":1,"STS":2,"TY":3,"STY":4,"Hurricane":5,"Major Hurricane":6}
        for p in track_pts:
            pt_lon, pt_lat = p["lon"], p["lat"]
            intensity = p.get("intensity")
            pcat = p.get("intensity_category", "")
            if not pcat and intensity is not None:
                if intensity >= 96: pcat = "Major Hurricane"
                elif intensity >= 64: pcat = "Hurricane"
                elif intensity >= 50: pcat = "STS"
                elif intensity >= 34: pcat = "TS"
                else: pcat = "TD"
            sym_name = self.main_ui._category_to_sym(pcat, intensity)
            sym_path = sym_dir / f"{sym_name}.png" if sym_name else None
            if sym_path and sym_path.exists():
                try:
                    sym_pil = PILImage.open(str(sym_path)).convert("RGBA")
                    sym_w, sym_h = sym_pil.size
                    target_size = 14
                    scale = target_size / max(sym_w, sym_h)
                    new_w, new_h = max(1, int(sym_w * scale)), max(1, int(sym_h * scale))
                    sym_pil = sym_pil.resize((new_w, new_h), PILImage.LANCZOS)
                    import numpy as np
                    sym_arr = np.array(sym_pil)
                    oi = OffsetImage(sym_arr, zoom=1, resample=True)
                    ab = AnnotationBbox(oi, (pt_lon, pt_lat), frameon=False, xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
                                        box_alignment=(0.5, 0.5), zorder=7)
                    ax.add_artist(ab)
                except Exception:
                    pass
            else:
                color = 'red' if intensity and intensity >= 64 else 'darkorange' if intensity and intensity >= 34 else 'yellow'
                sz = 40 if intensity and intensity >= 64 else 30 if intensity and intensity >= 34 else 20
                ax.scatter(pt_lon, pt_lat, color=color, s=sz, edgecolors='black',
                           linewidth=0.5, transform=ccrs.PlateCarree(), zorder=6, alpha=0.8)
        # Add final_track.png legend
        legend_path = sym_dir / "final_track.png"
        if legend_path.exists():
            try:
                leg_pil = PILImage.open(str(legend_path)).convert("RGBA")
                from matplotlib.offsetbox import OffsetImage, AnnotationBbox
                leg_w, leg_h = leg_pil.size
                leg_target_h = 80
                leg_scale = leg_target_h / leg_h
                leg_new_w, leg_new_h = max(1, int(leg_w * leg_scale)), max(1, int(leg_h * leg_scale))
                leg_pil = leg_pil.resize((leg_new_w, leg_new_h), PILImage.LANCZOS)
                import numpy as np
                leg_arr = np.array(leg_pil)
                # Place legend at bottom-left corner
                leg_lon = lon_min + (lon_max - lon_min) * 0.03
                leg_lat = lat_min + (lat_max - lat_min) * 0.03
                oi = OffsetImage(leg_arr, zoom=1, resample=True)
                ab = AnnotationBbox(oi, (leg_lon, leg_lat), frameon=False,
                                    xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
                                    box_alignment=(0, 0), zorder=10)
                ax.add_artist(ab)
            except Exception:
                pass
        # Add alternating point labels with date/time/wind speed
        fcst_prefs = self.main_ui.settings.get("forecast_preferences", {})
        show_dt = fcst_prefs.get("show_date_time", True)
        show_wind = fcst_prefs.get("show_wind_speed", True)
        time_fmt = fcst_prefs.get("time_format", "military")
        utc_offset = fcst_prefs.get("utc_offset", 0)
        wind_unit = fcst_prefs.get("wind_format", "kt")
        forecast_layout = fcst_prefs.get("forecast_layout", "monwatch")

        # Apply layout-specific styling
        if forecast_layout in ("pwards", "monwatch"):
            label_fs = 6.5
            label_bbox = dict(boxstyle='round,pad=0.25', fc='white', ec='#e0d8d0', alpha=0.92)
            label_arrow = dict(arrowstyle='-', lw=0.8, color='#c8c0b8')
        elif forecast_layout == "nhc":
            label_fs = 6.5
            label_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='#222222', alpha=0.9)
            label_arrow = dict(arrowstyle='-', lw=1.0, color='#333333')
        else:
            label_fs = 6.5
            label_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='gray', alpha=0.85)
            label_arrow = dict(arrowstyle='-', lw=0.8, color='#666666')
        if show_dt or show_wind:
            # Build label for each point
            labels = []
            for p in track_pts:
                label_parts = []
                dt_str = p.get("datetime", "")
                intensity = p.get("intensity")
                if show_dt and dt_str:
                    try:
                        dt_obj = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                        if utc_offset != 0:
                            from datetime import timezone, timedelta as tdelta
                            dt_obj = dt_obj.replace(tzinfo=timezone.utc) + tdelta(hours=utc_offset)
                        if time_fmt == "civilian":
                            label_parts.append(dt_obj.strftime("%Y-%m-%d %I:%M %p UTC" if utc_offset == 0 else "%Y-%m-%d %I:%M %p"))
                        else:
                            label_parts.append(dt_obj.strftime("%Y-%m-%d %H:%M UTC" if utc_offset == 0 else "%Y-%m-%d %H:%M"))
                    except Exception:
                        label_parts.append(dt_str)
                if show_wind and intensity is not None:
                    wind_val = float(intensity)
                    if wind_unit == "kmh":
                        wind_val = round(wind_val * 1.852)
                        label_parts.append(f"{wind_val} km/h")
                    elif wind_unit == "mph":
                        wind_val = round(wind_val * 1.151)
                        label_parts.append(f"{wind_val} mph")
                    elif wind_unit == "ms":
                        wind_val = round(wind_val * 0.514)
                        label_parts.append(f"{wind_val} m/s")
                    else:
                        label_parts.append(f"{wind_val} kt")
                labels.append("  ".join(label_parts) if label_parts else "")
            # Cramped system disabled
            skip = [False] * len(track_pts)
            # Draw labels alternating above/below, skipping cramped points
            label_idx = 0
            for i, p in enumerate(track_pts):
                if not labels[i] or skip[i]:
                    continue
                pt_lon, pt_lat = p["lon"], p["lat"]
                if label_idx % 2 == 0:
                    xytext = (-24, -14)
                    ha = 'right'
                else:
                    xytext = (24, 14)
                    ha = 'left'
                ax.annotate("", (pt_lon, pt_lat),
                            textcoords="offset points", xytext=xytext,
                            arrowprops={**label_arrow, 'relpos': (0 if ha == 'left' else 1, 0 if xytext[1] < 0 else 1)},
                            transform=ccrs.PlateCarree(), zorder=1)
                ax.annotate(labels[i], (pt_lon, pt_lat),
                            textcoords="offset points", xytext=xytext,
                            fontsize=label_fs, color='black', ha=ha,
                            bbox=label_bbox,
                            transform=ccrs.PlateCarree(), zorder=90)
                label_idx += 1
        if forecast_layout in ("pwards", "monwatch"):
            ax.set_title(f"{storm_name.upper()}  /  {storm_id.upper()}", fontsize=11, fontweight='600', fontfamily='sans-serif', color='#1a1a2e', pad=12, loc='left')
            ax.text(0.5, 1.0, "Forecast Track", fontsize=8, color='#888888', fontweight='400', fontfamily='sans-serif', transform=ax.transAxes, ha='center', va='bottom', alpha=0.7)
        elif forecast_layout == "nhc":
            title = f"NHC FORECAST  |  {storm_name}  ({storm_id.upper()})"
            ax.set_title(title, fontsize=10, fontweight='bold', fontfamily='monospace')
        else:
            title = f"Forecast Track: {storm_name} ({storm_id.upper()})"
            ax.set_title(title, fontsize=12, fontweight='bold')
        fig.subplots_adjust(bottom=0.08)
        export_dir = self.main_ui.settings.get("export_folder", str(_top_dir / "Exports"))
        Path(export_dir).mkdir(parents=True, exist_ok=True)
        safe_name = storm_name.replace(" ", "_").replace("/", "-")
        fname = f"forecast_{safe_name}_{storm_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = Path(export_dir) / fname
        fig.savefig(str(fpath), bbox_inches='tight', dpi=dpi, format='png')
        plt.close(fig)
        # ---- Add forecast footer ----
        try:
            from PIL import Image as PILImage, ImageDraw, ImageFont
            map_img = PILImage.open(str(fpath)).convert("RGBA")
            mw, mh = map_img.size
            footer_h = max(36, int(mh * 0.04))
            tscale = 1.0
            font_size = max(8, int(footer_h * 0.35 * tscale))
            try:
                font = ImageFont.truetype("consola.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()
            footer_img = PILImage.new("RGBA", (mw, footer_h), (255, 255, 255, 255))
            draw = ImageDraw.Draw(footer_img)
            # Logo
            logo_path = _top_dir / "public" / "images" / "Monwatch-LOGO.png"
            logo = None
            if logo_path.exists():
                try:
                    logo = PILImage.open(str(logo_path)).convert("RGBA")
                    logo_h = footer_h - 4
                    logo_w = int(logo.width * logo_h / logo.height) if logo.height > 0 else logo_h
                    logo = logo.resize((logo_w, logo_h), PILImage.LANCZOS)
                    footer_img.paste(logo, (4, 2), logo)
                except Exception:
                    logo = None
            logo_off = (logo_w + 8) if logo else 4
            # Forecast date/time from first track point
            forecast_dt = ""
            if track_pts:
                first_dt = track_pts[0].get("datetime", "")
                if first_dt:
                    try:
                        fdt = datetime.strptime(first_dt, "%Y-%m-%d %H:%M")
                        forecast_dt = fdt.strftime("%Y-%m-%d %H:%M UTC")
                    except Exception:
                        forecast_dt = first_dt
            # Created now
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            # Region
            basin = track_entry.get("basin", "East Pacific" if storm_id.startswith("ep") else "Atlantic" if storm_id.startswith("al") else "Central Pacific") if track_entry else ("East Pacific" if storm_id.startswith("ep") else "Atlantic" if storm_id.startswith("al") else "Central Pacific")
            fcst_prefs = self.main_ui.settings.get("forecast_preferences", {})
            utc_offset = fcst_prefs.get("utc_offset", 0)
            utc_label = f"UTC{utc_offset:+d}" if utc_offset != 0 else "UTC"
            left_text = f"{utc_label} Forecast {forecast_dt} -- Created {now_utc}  |  {basin}"
            bbox = draw.textbbox((0, 0), left_text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((logo_off, (footer_h - th) // 2), left_text, fill="black", font=font)
            # MonWatch-UI on right
            brand = "MonWatch-UI"
            bbox2 = draw.textbbox((0, 0), brand, font=font)
            bw = bbox2[2] - bbox2[0]
            draw.text((mw - bw - 8, (footer_h - th) // 2), brand, fill="black", font=font)
            combined = PILImage.new("RGBA", (mw, mh + footer_h), (0, 0, 0, 0))
            combined.paste(map_img, (0, 0), map_img)
            combined.paste(footer_img, (0, mh), footer_img)
            combined.convert("RGB").save(str(fpath), "PNG")
            self.main_ui.log(f"Forecast footer added to {fpath.name}")
        except Exception as e:
            self.main_ui.log(f"Forecast footer error: {e}")
        self.main_ui.status_bar.showMessage(f"Forecast map saved: {fpath.name}")
        self.main_ui.log(f"Forecast map generated: {fpath}")



    def _open_combined_forecast_dialog(self):
        dlg = CombinedForecastDialog(self.main_ui)
        dlg.exec()



    def _launch_combined_forecast(self, track_ids, layout_key=None, algorithm=None, cone_method=None, custom_title="", resolution="10m", show_labels=True):
        if not track_ids:
            return
        self.main_ui.log(f"[CombinedForecast] Combining {len(track_ids)} tracks on one map: {track_ids}")
        combined = []
        for tid in track_ids:
            track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id") == tid), None)
            if not track_entry:
                self.main_ui.log(f"[CombinedForecast] Track not found: {tid}")
                continue
            # Extract storm_id suffix for raw storm data lookup
            raw_sid = tid
            for prefix in ("jma_", "jtwc_", "nhc_", "pagasa_", "cwa_"):
                if tid.startswith(prefix):
                    raw_sid = tid[len(prefix):]
                    break
            # Try raw storm data first (same as single-track _launch_forecast_worker)
            track_pts = []
            prob_circles = []
            danger_swath = []
            wind_radii_polygons = {}
            kmz_cone = ""
            kmz_track = ""
            kmz_wind_initial = ""
            raw_data = (self.main_ui.jma_storms if tid.startswith("jma_") else
                        self.main_ui.nhc_storms if tid.startswith("nhc_") else
                        self.main_ui.jtwc_storms if tid.startswith("jtwc_") else
                        self.main_ui.pagasa_storms if tid.startswith("pagasa_") else
                        self.main_ui.cwa_storms if tid.startswith("cwa_") else {}).get(raw_sid)
            kmz_cone = ""
            kmz_track = ""
            kmz_wind_initial = ""
            _best_track_pts = []
            _best_track_line = []
            if raw_data:
                raw_pts = raw_data.get("track_points", [])
                if raw_pts:
                    track_pts = raw_pts
                prob_circles_raw = raw_data.get("probability_circles", [])
                for circ in prob_circles_raw:
                    center = circ.get("center")
                    radius = circ.get("radius")
                    tangents = circ.get("tangent", [])
                    if center and len(center) >= 2 and radius:
                        tangent_out = []
                        for seg in tangents:
                            seg_out = [(float(pt[0]), float(pt[1])) for pt in seg if len(pt) >= 2]
                            if seg_out:
                                tangent_out.append(seg_out)
                        prob_circles.append({
                            "center_lon": float(center[1]),
                            "center_lat": float(center[0]),
                            "radius_m": int(radius),
                            "tangents": tangent_out,
                        })
                kmz_cone = raw_data.get("kmz_cone", "")
                kmz_track = raw_data.get("kmz_track", "") or raw_data.get("track_line", "")
                kmz_wind_initial = raw_data.get("kmz_wind_initial", "")
                danger_swath = raw_data.get("danger_swath", [])
                wind_radii_polygons = raw_data.get("wind_radii_polygons", {})
                _best_track_pts = raw_data.get("best_track_points", [])
                _best_track_line = raw_data.get("best_track_line", [])
            # Fallback to track entry points if raw_data has none
            if not track_pts:
                track_pts = track_entry.get("points", [])
            if not track_pts:
                self.main_ui.log(f"[CombinedForecast] No points for track: {tid}")
                continue
            # Fallback for JTWC: load KMZ data from disk if raw_data not in memory
            if tid.startswith("jtwc_") and not raw_data:
                try:
                    from src.clients.jtwc import _parse_jtwc_kmz, get_jtwc_data_dir
                    _jtwc_dir = get_jtwc_data_dir()
                    _kmz_path = _jtwc_dir / raw_sid / f"{raw_sid}.kmz"
                    if _kmz_path.exists():
                        _parsed = _parse_jtwc_kmz(_kmz_path)
                        if _parsed.get("success"):
                            kmz_track = _parsed.get("track_line", [])
                            prob_circles_raw = _parsed.get("probability_circles", [])
                            for circ in prob_circles_raw:
                                center = circ.get("center")
                                radius = circ.get("radius")
                                tangents = circ.get("tangent", [])
                                if center and len(center) >= 2 and radius:
                                    tangent_out = []
                                    for seg in tangents:
                                        seg_out = [(float(pt[0]), float(pt[1])) for pt in seg if len(pt) >= 2]
                                        if seg_out:
                                            tangent_out.append(seg_out)
                                    prob_circles.append({
                                        "center_lon": float(center[1]),
                                        "center_lat": float(center[0]),
                                        "radius_m": int(radius),
                                        "tangents": tangent_out,
                                    })
                            danger_swath = _parsed.get("danger_swath", [])
                            wind_radii_polygons = _parsed.get("wind_radii_polygons", {})
                            _best_track_pts = _parsed.get("best_track_points", [])
                            _best_track_line = _parsed.get("best_track_line", [])
                except Exception:
                    pass
            # Fallback: build from prob_circle_km or radius_km per point
            if not prob_circles:
                prob_circles = [
                    {"center_lon": p["lon"], "center_lat": p["lat"], "radius_m": (p.get("prob_circle_km") or p.get("radius_km", 0)) * 1000}
                    for p in track_pts if (p.get("prob_circle_km") or p.get("radius_km")) and p.get("lon") is not None and p.get("lat") is not None
                ]
            # Convert JTWC-style dtg to datetime for title-box rendering
            _issued = track_entry.get("issued_dtg", "")
            if _issued:
                try:
                    from Process.forecast.utils import _parse_jtwc_dtg
                    _base_dt = _parse_jtwc_dtg(_issued)
                    if _base_dt is not None:
                        for _p in track_pts:
                            _ah = _p.get("advanced_hours")
                            if _ah is not None and ("datetime" not in _p or not _p.get("datetime")):
                                _p["datetime"] = (_base_dt + timedelta(hours=_ah)).strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
            elif track_pts:
                for _p in track_pts:
                    if "datetime" not in _p or not _p.get("datetime"):
                        _dtg = _p.get("dtg")
                        if _dtg:
                            _p["datetime"] = _dtg
            _basin = ""
            if tid.startswith("jtwc_"):
                _basin = raw_data.get("basin", raw_sid[:2].upper()) if raw_data else raw_sid[:2].upper()
            elif raw_data:
                _basin = raw_data.get("basin", "")
            combined.append({
                "storm_id": tid,
                "storm_name": raw_data.get("storm_name", track_entry.get("name", tid)) if raw_data else track_entry.get("name", tid),
                "track_points": track_pts,
                "probability_circles": prob_circles,
                "issued_dtg": track_entry.get("issued_dtg", ""),
                "source": "nhc" if tid.startswith("nhc_") else "jma" if tid.startswith("jma_") else "jtwc" if tid.startswith("jtwc_") else "pagasa" if tid.startswith("pagasa_") else "cwa" if tid.startswith("cwa_") else "custom",
                "basin": _basin,
                "kmz_cone": kmz_cone,
                "kmz_track": kmz_track,
                "kmz_wind_initial": kmz_wind_initial,
                "danger_swath": danger_swath,
                "wind_radii_polygons": wind_radii_polygons,
                "best_track_points": _best_track_pts,
                "best_track_line": _best_track_line,
            })
        if len(combined) < 1:
            self.main_ui.log("[CombinedForecast] No valid tracks to combine")
            return
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp_name = tmp.name
        json.dump({"combined_tracks": combined, "custom_title": custom_title, "resolution": resolution, "show_labels": show_labels}, tmp)
        tmp.close()
        label = f"combined_{'_'.join(track_ids)}"
        export_dir = self.main_ui.settings.get("export_folder", str(_top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        fname = f"forecast_combined_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        proc = QProcess(self)
        settings_path = str(self.main_ui.settings.settings_file)
        logo_path = str(_top_dir / "public" / "images" / "Monwatch-LOGO.png")
        script = str(_top_dir / "Process" / "forecast" / "common.py")
        self.main_ui._forecast_counter += 1
        task_id = f"{label}_{self.main_ui._forecast_counter}"
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.finished.connect(lambda exit_code, exit_status, _tid=task_id, out=fpath: self.main_ui._on_forecast_finished(exit_code, exit_status, _tid, out))
        labeling_method = algorithm or self.main_ui.settings.get("labeling_method", "polar")
        cone_method = cone_method or self.main_ui.settings.get("cone_method", "smooth")
        forecast_layout = layout_key or self.main_ui.settings.get("forecast_preferences", {}).get("forecast_layout", "monwatch")
        proc.start(sys.executable, [
            script,
            "--storm_id", label,
            "--input", tmp_name,
            "--output", fpath,
            "--light",
            "--settings", settings_path,
            "--logo", logo_path,
            "--algorithm", labeling_method,
            "--cone", cone_method,
            "--layout", forecast_layout,
            "--combined",
        ])
        self.main_ui._forecast_processes[task_id] = {"proc": proc, "output": fpath, "tmp": tmp_name}
        self.main_ui._update_forecast_progress()
        self.main_ui.log(f"[CombinedForecast] Worker launched -> {os.path.basename(fpath)}")



    def _update_selected_nhc_track(self):
        cur = self.main_ui.tracks_list.currentItem()
        if not cur:
            return
        if cur.parent():
            cur = cur.parent()
        track_id = cur.data(0, Qt.UserRole)
        if not track_id or not track_id.startswith("nhc_"):
            return
        storm_id = track_id[4:]
        self.main_ui._download_update_for_storm(storm_id)



    def _launch_forecast_worker(self, storm_id):
        jma_keys = list(getattr(self.main_ui, 'jma_storms', {}).keys())
        self.main_ui.log(f"[Forecast] button clicked -- storm_id={storm_id!r}, in nhc={storm_id in getattr(self.main_ui,'nhc_storms',{})}, in jma={storm_id in getattr(self.main_ui,'jma_storms',{})}, jma keys={jma_keys}")
        # Try direct lookup, then search JMA/JTWC keys by suffix match
        storm_data = (self.main_ui.jma_storms.get(storm_id) or
                      self.main_ui.nhc_storms.get(storm_id) or
                      self.main_ui.jtwc_storms.get(storm_id) or
                      self.main_ui.pagasa_storms.get(storm_id) or
                      self.main_ui.cwa_storms.get(storm_id))
        if not storm_data:
            for k in jma_keys:
                if k.endswith(storm_id) or storm_id.endswith(k):
                    storm_data = self.main_ui.jma_storms.get(k)
                    self.main_ui.log(f"[Forecast] matched jma key {k!r} -> {storm_id!r}")
                    break
        # Fallback: read points straight from saved track entry (no download needed)
        track_pts = []
        track_entry = None
        _track_entry_src = None
        if not storm_data:
            track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id", "").endswith(storm_id)), None)
            if track_entry:
                track_pts = track_entry.get("points", [])
                storm_data = {"storm_name": track_entry.get("name", storm_id), "track_points": track_pts}
                self.main_ui.log(f"[Forecast] using saved track entry {track_entry.get('id')} -- {len(track_pts)} pts")
                _tid = track_entry.get("id", "")
                for _p, _s in [("nhc_","nhc"),("jma_","jma"),("jtwc_","jtwc"),("pagasa_","pagasa"),("cwa_","cwa")]:
                    if _tid.startswith(_p):
                        _track_entry_src = _s
                        break
                # Attempt to load full KMZ data from disk to preserve danger_swath, kmz_cone etc.
                try:
                    from src.clients.jtwc import _parse_jtwc_kmz, get_jtwc_data_dir
                    _jtwc_dir = get_jtwc_data_dir()
                    _kmz_path = _jtwc_dir / storm_id / f"{storm_id}.kmz"
                    if _kmz_path.exists():
                        _parsed = _parse_jtwc_kmz(_kmz_path)
                        if _parsed.get("success"):
                            storm_data.setdefault("danger_swath", _parsed.get("danger_swath", []))
                            storm_data.setdefault("wind_radii_polygons", _parsed.get("wind_radii_polygons", {}))
                            storm_data.setdefault("best_track_points", _parsed.get("best_track_points", []))
                            storm_data.setdefault("best_track_line", _parsed.get("best_track_line", []))
                            self.main_ui.log(f"[Forecast] loaded KMZ extras for {storm_id}: danger_swath={len(storm_data['danger_swath'])} pts")
                except Exception as _kmz_ex:
                    self.main_ui.log(f"[Forecast] KMZ fallback failed for {storm_id}: {_kmz_ex}")
        if not storm_data:
            QMessageBox.warning(self.main_ui, "No Data", f"No track data for {storm_id}.")
            return
        if not track_pts:
            track_pts = storm_data.get("track_points", [])
        # Fallback: use track entry points if storm_data track_points is empty
        if not track_pts:
            track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id", "").endswith(storm_id)), None)
            if track_entry:
                track_pts = track_entry.get("points", [])
        if not track_pts:
            QMessageBox.warning(self.main_ui, "No Track Data", f"No track points for {storm_id}.")
            return
        # Build a temp JSON with storm data for the subprocess
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp_name = tmp.name
        is_jma = storm_id in getattr(self.main_ui, 'jma_storms', {})
        if not is_jma:
            is_jma = bool(storm_data.get("probability_circles"))
        # Also check PAGASA storm data for probability circles
        is_pagasa = storm_id in getattr(self.main_ui, 'pagasa_storms', {})
        pagasa_raw = self.main_ui.pagasa_storms.get(storm_id, {}) if is_pagasa else {}
        # Pass probability_circles (center, radius_m, tangents) for overlay-style rendering in forecast
        prob_circles_raw = storm_data.get("probability_circles", [])
        prob_circles_out = []
        for circ in prob_circles_raw:
            center = circ.get("center")
            radius = circ.get("radius")
            tangents = circ.get("tangent", [])
            if center and len(center) >= 2 and radius:
                tangent_out = []
                for seg in tangents:
                    seg_out = [(float(pt[0]), float(pt[1])) for pt in seg if len(pt) >= 2]
                    if seg_out:
                        tangent_out.append(seg_out)
                prob_circles_out.append({
                    "center_lon": float(center[1]),
                    "center_lat": float(center[0]),
                    "radius_m": int(radius),
                    "tangents": tangent_out,
                })
        # Fallback: generate probability circles from track point radius_km or prob_circle_km
        if not prob_circles_out:
            prob_circles_out = [
                {"center_lon": p["lon"], "center_lat": p["lat"], "radius_m": (p.get("prob_circle_km") or p.get("radius_km", 0) or 0) * 1000}
                for p in track_pts if (p.get("prob_circle_km") or p.get("radius_km")) and p.get("lon") is not None and p.get("lat") is not None
            ]
        # Get issued_dtg from jtwc_storms data or track entry
        issued_dtg = None
        jtwc_data = self.main_ui.jtwc_storms.get(storm_id)
        if jtwc_data:
            issued_dtg = jtwc_data.get("issued_dtg")
        if not issued_dtg and track_entry:
            issued_dtg = track_entry.get("issued_dtg")
        if not issued_dtg and storm_data:
            issued_dtg = storm_data.get("issued_dtg")

        # Convert JTWC-style dtg to proper datetime string for title-box / labels
        if issued_dtg:
            from Process.forecast.utils import _parse_jtwc_dtg
            try:
                _base_dt = _parse_jtwc_dtg(issued_dtg)
                if _base_dt is not None:
                    for _p in track_pts:
                        _ah = _p.get("advanced_hours")
                        if _ah is not None and ("datetime" not in _p or not _p.get("datetime")):
                            _p["datetime"] = (_base_dt + timedelta(hours=_ah)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        elif track_pts:
            for _p in track_pts:
                if "datetime" not in _p or not _p.get("datetime"):
                    _dtg = _p.get("dtg")
                    if _dtg:
                        _p["datetime"] = _dtg

        # NHC KMZ paths for PWARDS direct KMZ rendering
        kmz_cone = storm_data.get("kmz_cone", "")
        kmz_track = storm_data.get("kmz_track", "")
        kmz_wind_initial = storm_data.get("kmz_wind_initial", "")
        # Pre-parsed JTWC KMZ data (NHC-style): avoid re-parsing at render time
        danger_swath = storm_data.get("danger_swath", [])
        wind_radii_polygons = storm_data.get("wind_radii_polygons", {})
        # Source detection: use track-entry source if set (from fallback), otherwise check agency dicts
        if _track_entry_src:
            _src = _track_entry_src
        else:
            _src = "jma" if is_jma else ("pagasa" if is_pagasa else ("jtwc" if storm_id in getattr(self.main_ui, 'jtwc_storms', {}) else ("cwa" if storm_id in getattr(self.main_ui, 'cwa_storms', {}) else "nhc")))
        # Build title_info for per-source title box formatting
        title_info = {}
        if _src == "jtwc":
            # Use JTWC-specific data when source is JTWC (not whatever storm_data was found first)
            _jtwc_data = getattr(self.main_ui, 'jtwc_storms', {}).get(storm_id) or storm_data
            _jtwc_name = _jtwc_data.get("storm_name", "")
            # Check if name is a placeholder number-word (TWELVE, THIRTEEN, etc.) and resolve to proper name
            if _jtwc_name and self._is_placeholder_name(_jtwc_name):
                _jma_data = getattr(self.main_ui, 'jma_storms', {}).get(storm_id, {})
                _jma_name = _jma_data.get("storm_name", "")
                if _jma_name and not forecast_controller._is_placeholder_name(_jma_name):
                    _jtwc_name = _jma_name
            _jtwc_issued = issued_dtg or _jtwc_data.get("issued_dtg", "")
            title_info = {
                "storm_name": _jtwc_name,
                "category": _jtwc_data.get("classification", ""),
                "warning_nr": int(_jtwc_data.get("warning_num", "1") or 1),
                "bulletin_day": _jtwc_issued[:2] if len(_jtwc_issued) >= 2 else "",
                "bulletin_time": _jtwc_issued[2:6] if len(_jtwc_issued) >= 6 else "",
            }
        elif _src == "nhc":
            title_info = {
                "storm_name": storm_data.get("storm_name", ""),
                "advisory_num": int(storm_data.get("advisory_number", 1) or 1),
                "pub_adv_time": track_pts[0].get("datetime", "") if track_pts else "",
            }
        elif _src == "cwa":
            from src.clients.cwa import get_cwa_data_dir
            _cwa_data_dir = get_cwa_data_dir()
            _cwa_raw_path = _cwa_data_dir / storm_id / f"{storm_id}_cwa.json"
            _cwa_raw = {}
            if _cwa_raw_path.exists():
                try:
                    with open(_cwa_raw_path) as _f:
                        _cwa_raw = json.load(_f)
                except Exception:
                    pass
            _cwa_tc = _cwa_raw.get("cwaopendata", {}).get("Dataset", {}).get("TropicalCyclones", {}).get("TropicalCyclone", {})
            _cwa_fixes = _cwa_tc.get("AnalysisData", {}).get("Fix", [])
            _cwa_name = _cwa_tc.get("TyphoonName", storm_data.get("storm_name", ""))
            # Clean CWA storm name: strip "CWA " prefix, trailing " CWA", year (YYYY)
            if _cwa_name:
                _cn = _cwa_name.strip()
                if _cn.upper().startswith("CWA "):
                    _cn = _cn[4:].strip()
                if _cn.upper().endswith(" CWA"):
                    _cn = _cn[:-4].strip()
                import re as _cwa_re
                _cn = _cwa_re.sub(r'\s*\(\d{4}\)', '', _cn).strip()
                _cwa_name = _cn
            _cwa_latest = _cwa_fixes[-1].get("DateTime", "") if _cwa_fixes else ""
            title_info = {
                "storm_name": _cwa_name,
                "latest_time": _cwa_latest,
                "fix_count": len(_cwa_fixes),
            }
        elif _src == "jma":
            _jma_raw = storm_data.get("jma_raw", [])
            _jma_title = next((p for p in _jma_raw if isinstance(p, dict) and p.get("part") == "title"), {}) if _jma_raw else {}
            _jma_issuance = _jma_title.get("issue", {}).get("UTC", "") if isinstance(_jma_title, dict) else ""
            _jma_typhoon = []
            if _jma_raw:
                for _p in _jma_raw:
                    if isinstance(_p, dict) and isinstance(_p.get("track"), dict):
                        _jma_typhoon = _p["track"].get("typhoon", [])
                        break
            title_info = {
                "storm_name": storm_data.get("storm_name", ""),
                "issuance": _jma_issuance,
                "typhoon_count": len(_jma_typhoon),
            }
        elif _src == "pagasa":
            _pagasa_raw = self.main_ui.pagasa_storms.get(storm_id, {})
            _pag_bulletins = sum(1 for _p in track_pts if _p.get("radius_km") == 0)
            title_info = {
                "storm_name": _pagasa_raw.get("raw_name", storm_data.get("storm_name", "")),
                "bulletin_count": _pag_bulletins,
            }
        json.dump({
            "storm_id": storm_id,
            "storm_name": storm_data.get("storm_name", storm_id),
            "track_points": track_pts,
            "probability_circles": prob_circles_out,
            "kmz_cone": kmz_cone,
            "kmz_track": kmz_track,
            "kmz_wind_initial": kmz_wind_initial,
            "danger_swath": danger_swath,
            "wind_radii_polygons": wind_radii_polygons,
            "best_track_points": storm_data.get("best_track_points", []),
            "best_track_line": storm_data.get("best_track_line", []),
            "issued_dtg": issued_dtg or "",
            "source": _src,
            "basin": storm_data.get("basin", ""),
            "title_info": title_info,
        }, tmp)
        tmp.close()
        # Determine output path
        safe_name = storm_data.get("storm_name", storm_id).replace(" ", "_").replace("/", "-")
        export_dir = self.main_ui.settings.get("export_folder", str(_top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        fname = f"forecast_{safe_name}_{storm_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        # Launch subprocess
        proc = QProcess(self)
        settings_path = str(self.main_ui.settings.settings_file)
        logo_path = str(_top_dir / "public" / "images" / "Monwatch-LOGO.png")
        script = str(_top_dir / "Process" / "forecast" / "common.py")
        self.main_ui._forecast_counter += 1
        task_id = f"{storm_id}_{self.main_ui._forecast_counter}"
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.finished.connect(lambda exit_code, exit_status, tid=task_id, out=fpath: self.main_ui._on_forecast_finished(exit_code, exit_status, tid, out))
        labeling_method = self.main_ui.settings.get("labeling_method", "polar")
        cone_method = self.main_ui.settings.get("cone_method", "smooth")
        forecast_layout = self.main_ui.settings.get("forecast_preferences", {}).get("forecast_layout", "monwatch")
        proc.start(sys.executable, [
            script,
            "--storm_id", storm_id,
            "--input", tmp_name,
            "--output", fpath,
            "--light",
            "--settings", settings_path,
            "--logo", logo_path,
            "--algorithm", labeling_method,
            "--cone", cone_method,
            "--layout", forecast_layout,
        ])
        self.main_ui._forecast_processes[task_id] = {"proc": proc, "output": fpath, "tmp": tmp_name}
        self.main_ui._update_forecast_progress()
        self.main_ui.log(f"Forecast worker launched: {storm_id} -> {os.path.basename(fpath)}")



    def _on_forecast_finished(self, exit_code, exit_status, task_id, output_path):
        info = self.main_ui._forecast_processes.pop(task_id, None)
        err_output = ""
        if info and info.get("proc"):
            err_output = bytes(info["proc"].readAll()).decode("utf-8", errors="replace")
        if info:
            try:
                os.unlink(info["tmp"])
            except Exception:
                pass
        storm_id = task_id.split("_")[0] if task_id else "?"
        if exit_code == 0 and os.path.exists(output_path):
            self.main_ui.log(f"Forecast generated: {output_path}")
            self.main_ui.status_bar.showMessage(f"Forecast saved: {os.path.basename(output_path)}")
        else:
            self.main_ui.log(f"Forecast generation failed for {storm_id} (exit {exit_code})")
            if err_output:
                for line in err_output.strip().split("\n"):
                    self.main_ui.log(f"  [forecast stderr] {line}")
        self.main_ui._update_forecast_progress()



    def _update_forecast_progress(self):
        running = len(self.main_ui._forecast_processes)
        if running > 0:
            if not self.main_ui._forecast_progress_bar:
                self.main_ui._forecast_progress_bar = QProgressBar()
                self.main_ui._forecast_progress_bar.setRange(0, 0)
                self.main_ui._forecast_progress_bar.setFixedWidth(160)
                self.main_ui._forecast_progress_bar.setFixedHeight(16)
                self.main_ui._forecast_progress_bar.setStyleSheet("""
                    QProgressBar {
                        border: 1px solid #4CAF50;
                        border-radius: 3px;
                        text-align: center;
                        background: #2A2A2A;
                        color: #FFF;
                        font-size: 8px;
                    }
                    QProgressBar::chunk {
                        background: #4CAF50;
                        border-radius: 2px;
                    }
                """)
                self.main_ui.status_bar.addPermanentWidget(self.main_ui._forecast_progress_bar)
            self.main_ui._forecast_progress_bar.setFormat(f"Forecast: {running}")
            self.main_ui._forecast_progress_bar.setVisible(True)
            self.main_ui._forecast_progress_bar.repaint()
        else:
            if self.main_ui._forecast_progress_bar:
                self.main_ui.status_bar.removeWidget(self.main_ui._forecast_progress_bar)
                self.main_ui._forecast_progress_bar.deleteLater()
                self.main_ui._forecast_progress_bar = None



    def _generate_selected_nhc_forecast(self):
        cur = self.main_ui.tracks_list.currentItem()
        if not cur:
            return
        if cur.parent():
            cur = cur.parent()
        track_id = cur.data(0, Qt.UserRole)
        if not track_id or not track_id.startswith("nhc_"):
            return
        storm_id = track_id[4:]
        self.main_ui._launch_forecast_worker(storm_id)



    def _show_new_track_dialog(self):

        if not hasattr(self.main_ui, 'tracks'):
            self.main_ui.tracks = []

        dlg = MeteorologicalTrackDialog(self.main_ui)
        if dlg.exec() == QDialog.Accepted:
            data = dlg.get_data()
            self.main_ui.tracks.append(data)
            _save_tracks_to_disk(self.main_ui.tracks)
            self.main_ui._refresh_tracks_list()
            self.main_ui.log(f"New track added: {data.get('name') or data.get('type')} {data.get('year')}")
            if hasattr(self.main_ui, '_update_tracks_overlays'):
                self.main_ui._update_tracks_overlays()

        if hasattr(self.main_ui, 'right_tab_widget') and hasattr(self.main_ui, 'tracks_tab_index'):
            self.main_ui.right_tab_widget.setCurrentIndex(self.main_ui.tracks_tab_index)



    def _open_forecast_dialog(self, scene_x, scene_y):
        """Open forecast dialog for a given scene position."""

        if not self.main_ui.graphics_view.scene() or not self.main_ui.graphics_view.scene().items():
            self.main_ui.log("No image loaded -- cannot get coordinates for forecast.")
            return

        pixmap_item = None
        for item in reversed(self.main_ui.graphics_view.scene().items()):
            if isinstance(item, QGraphicsPixmapItem):
                pixmap_item = item
                break
        if not pixmap_item:
            self.main_ui.log("No image loaded.")
            return

        img_w = pixmap_item.pixmap().width()
        img_h = pixmap_item.pixmap().height()
        ox = pixmap_item.pos().x()
        oy = pixmap_item.pos().y()
        px = scene_x - ox
        py = scene_y - oy
        if px < 0 or py < 0 or px > img_w or py > img_h:
            self.main_ui.log("Click is outside the image area.")
            return

        lat = lon = None
        if self.main_ui.current_geotransform and self.main_ui.current_crs:
            try:
                transform = self.main_ui.current_geotransform
                crs_proj = self.main_ui.current_crs
                native_res_m = abs(transform.a)
                native_extent = abs(transform.c)
                native_grid_w = int(round(2 * native_extent / native_res_m))
                scale = native_grid_w / img_w if img_w > 0 else 1.0
                x_native = px * scale
                y_native = py * scale
                x_proj, y_proj = transform * (x_native, y_native)
                transformer = Transformer.from_crs(crs_proj, "EPSG:4326", always_xy=True)
                lon, lat = transformer.transform(x_proj, y_proj)
                if lon is not None:
                    lon = normalize_lon(lon)
            except Exception as e:
                self.main_ui.log(f"Could not convert to lat/lon: {e}")
                return

        if lat is None or lon is None:
            self.main_ui.log("Could not determine lat/lon.")
            return

        api_key = self.main_ui.settings.get("windy_api_key", "")
        dlg = ForecastDialog(self.main_ui, lat, lon, api_key)
        dlg.exec()



    def _add_temp_point_as_track(self, temp_k: float, scene_x: float, scene_y: float):

        if not hasattr(self.main_ui, 'tracks'):
            self.main_ui.tracks = []

        entry = {
            "id": str(uuid.uuid4())[:8],
            "name": f"Point {temp_k:.1f} K",
            "type": "Temperature Measurement",
            "year": QDate.currentDate().year(),
            "basin": "N/A",
            "notes": f"Manual point sample at scene coords ({scene_x:.0f}, {scene_y:.0f})",
            "visible": True,
            "add_to_infobox": False,
            "points": [{"lat": None, "lon": None, "k": temp_k, "scene_x": scene_x, "scene_y": scene_y}]
        }
        self.main_ui.tracks.append(entry)
        _save_tracks_to_disk(self.main_ui.tracks)
        self.main_ui._refresh_tracks_list()
        self.main_ui.log(f"Temp point added to Tracks: {temp_k:.1f} K")
        if hasattr(self.main_ui, '_update_tracks_overlays'):
            self.main_ui._update_tracks_overlays()



    def _edit_selected_track(self):
        cur = self.main_ui.tracks_list.currentItem()
        if not cur:
            return
        if cur.parent():
            cur = cur.parent()
        track_id = cur.data(0, Qt.UserRole)
        track = next((t for t in self.main_ui.tracks if t.get("id") == track_id), None)
        if track is None:
            return
        
        # Handle Meteorological tracks
        if track.get("type") in ["Typhoon", "Hurricane", "Tropical Storm", "Tropical Depression", "Extratropical Cyclone", "Invest", "Other"]:
            dlg = MeteorologicalTrackDialog(self.main_ui)
            dlg.setWindowTitle("Edit Meteorological Track")
            
            # Populate fields with existing data
            dlg.name_edit.setText(track.get("name") or "")
            dlg.type_combo.setCurrentText(track.get("type", "Typhoon"))
            dlg.year_spin.setValue(track.get("year", 2026))
            dlg.basin_combo.setCurrentText(track.get("basin", "West Pacific"))
            dlg.notes_edit.setPlainText(track.get("notes") or "")
            dlg.add_to_infobox_cb.setChecked(track.get("add_to_infobox", False))
            
            # Load existing points
            dlg._track_points = [p.copy() for p in track.get("points", [])]
            dlg._refresh_points_list()
            dlg._track_id = track_id
            
            if dlg.exec() == QDialog.Accepted:
                data = dlg.get_data()
                # Update track in list
                for t in self.main_ui.tracks:
                    if t.get("id") == track_id:
                        t.update(data)
                        break
                _save_tracks_to_disk(self.main_ui.tracks)
                self.main_ui._refresh_tracks_list()
                self.main_ui.log(f"Track updated: {data.get('name') or data.get('type')}")
                if hasattr(self.main_ui, '_update_tracks_overlays'):
                    self.main_ui._update_tracks_overlays()
            return
        
        # Handle Custom AoR tracks
        if track.get("type") == "Custom AoR":
            from src.ui.dialogs import CustomAoRDialog
            dlg = CustomAoRDialog(self.main_ui, track)
            if dlg.exec() == QDialog.Accepted:
                data = dlg.get_data()
                track.update(data)
                _save_tracks_to_disk(self.main_ui.tracks)
                self.main_ui._refresh_tracks_list()
                self.main_ui._update_tracks_overlays()
            return
        
        # Handle NHC tracks -- allow editing name, notes, color, infobox
        if track.get("id","").startswith("nhc_"):
            from src.ui.dialogs import MeteorologicalTrackDialog
            dlg = MeteorologicalTrackDialog(self.main_ui)
            dlg.setWindowTitle("Edit NHC Track Properties")
            dlg.name_edit.setText(track.get("name") or "")
            dlg.type_combo.setCurrentText(track.get("type", "Tropical Storm"))
            dlg.year_spin.setValue(track.get("year", 2026))
            dlg.basin_combo.setCurrentText(track.get("basin", "East Pacific"))
            dlg.notes_edit.setPlainText(track.get("notes") or "")
            dlg.add_to_infobox_cb.setChecked(track.get("add_to_infobox", False))
            dlg._track_points = [p.copy() for p in track.get("points", [])]
            dlg._refresh_points_list()
            dlg._track_id = track_id
            if dlg.exec() == QDialog.Accepted:
                data = dlg.get_data()
                for t in self.main_ui.tracks:
                    if t.get("id") == track_id:
                        t.update(data)
                        break
                _save_tracks_to_disk(self.main_ui.tracks)
                self.main_ui._refresh_tracks_list()
                self.main_ui.log(f"NHC track updated: {data.get('name') or data.get('type')}")
                if hasattr(self.main_ui, '_update_tracks_overlays'):
                    self.main_ui._update_tracks_overlays()
            return
        
        QMessageBox.information(self.main_ui, "Edit Track", "Cannot edit this track type.")



    def _generate_forecast_pixmap(self, storm_id, storm_data=None):
        if storm_data is None:
            storm_data = self.main_ui.nhc_storms.get(storm_id)
        if not storm_data:
            return None
        try:
            import cartopy.crs as ccrs
            import cartopy.feature as cfeature
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
            from PIL import Image as PILImage
            import io
        except ImportError:
            return None
        track_pts = storm_data.get("track_points", [])
        if not track_pts:
            return None
        lons = [p["lon"] for p in track_pts if p.get("lon") is not None]
        lats = [p["lat"] for p in track_pts if p.get("lat") is not None]
        if not lons:
            return None
        margin = 5.0
        lon_min, lon_max, central_lon = _normalize_lon_extent(lons, margin)
        lat_min = max(min(lats) - margin, -90)
        lat_max = min(max(lats) + margin, 90)
        dpi = 150
        fig = plt.figure(figsize=(10, 7.5), dpi=dpi)
        ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
        ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
        ax.set_facecolor('#1a1a2e')
        ax.add_feature(cfeature.LAND, facecolor='#2a2a3e', edgecolor='gray', linewidth=0.5)
        if storm_id and str(storm_id).startswith("wp"):
            try:
                from src.data.psgc_shapefiles import add_psgc_land_to_axis
                add_psgc_land_to_axis(ax, facecolor='#2a2a3e', edgecolor='gray', linewidth=0.5, zorder=3)
            except Exception:
                pass
        ax.add_feature(cfeature.OCEAN, facecolor='#0d1b2a', alpha=0.8)
        ax.add_feature(cfeature.COASTLINE, edgecolor='#88b0d0', linewidth=0.8)
        ax.add_feature(cfeature.BORDERS, edgecolor='#556677', linewidth=0.3, linestyle=':')
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.5, color='#4a6a8a', alpha=0.6, linestyle='--')
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {'size': 7, 'color': '#c0d0e0'}
        gl.ylabel_style = {'size': 7, 'color': '#c0d0e0'}
        storm_name = storm_data.get("storm_name", storm_id)
        cone_polys = storm_data.get("cone_polygons", [])
        for poly in cone_polys:
            if poly and len(poly) >= 3:
                c_lons = [pt[0] for pt in poly]
                c_lats = [pt[1] for pt in poly]
                ax.fill(c_lons, c_lats, color='#4488ff', alpha=0.07, transform=ccrs.PlateCarree(), zorder=2)
        if len(track_pts) >= 2:
            tlons = [p["lon"] for p in track_pts if p.get("lon") is not None]
            tlats = [p["lat"] for p in track_pts if p.get("lat") is not None]
            if tlons:
                ax.plot(tlons, tlats, color='#ffaa44', linewidth=2.5, transform=ccrs.PlateCarree(), zorder=5)
        # Build labels based on forecast preferences
        fcst_prefs = self.main_ui.settings.get("forecast_preferences", {})
        show_dt = fcst_prefs.get("show_date_time", True)
        show_wind = fcst_prefs.get("show_wind_speed", True)
        wind_unit = fcst_prefs.get("wind_format", "kt")
        forecast_layout = fcst_prefs.get("forecast_layout", "monwatch")
        pix_labels = []
        for p in track_pts:
            dt_str = p.get("datetime", "")
            intensity = p.get("intensity")
            parts = []
            if show_dt and dt_str:
                try:
                    d = dt_str.split(" ")[0] if " " in dt_str else dt_str
                    t = dt_str.split(" ")[1] if " " in dt_str else ""
                    parts.append(f"{d[-5:]} {t[:5]}")
                except Exception:
                    parts.append(dt_str)
            if show_wind and intensity is not None:
                wind_val = float(intensity)
                if wind_unit == "kmh":
                    wind_val = round(wind_val * 1.852)
                    parts.append(f"{wind_val} km/h")
                elif wind_unit == "mph":
                    wind_val = round(wind_val * 1.151)
                    parts.append(f"{wind_val} mph")
                elif wind_unit == "ms":
                    wind_val = round(wind_val * 0.514)
                    parts.append(f"{wind_val} m/s")
                else:
                    parts.append(f"{wind_val} kt")
            pix_labels.append("  ".join(parts) if parts else "")
        n = len(track_pts)
        skip = [False] * n
        # Cramped system disabled
        for i, p in enumerate(track_pts):
            pt_lon, pt_lat = p["lon"], p["lat"]
            intensity = p.get("intensity")
            if intensity is not None and intensity >= 64:
                mk = '#ff6666'
                sz = 9
            elif intensity is not None and intensity >= 34:
                mk = '#ffcc44'
                sz = 7
            else:
                mk = '#44aaff'
                sz = 7
            ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                    transform=ccrs.PlateCarree(), zorder=6,
                    markeredgecolor='white', markeredgewidth=0.5)
            if not pix_labels[i] or skip[i]:
                continue
            if (i - sum(skip[:i])) % 2 == 0:
                xytext, ha = (-24, -14), 'right'
            else:
                xytext, ha = (24, 14), 'left'
            ax.annotate("", (pt_lon, pt_lat),
                        textcoords="offset points", xytext=xytext,
                        arrowprops={**dict(arrowstyle='-', lw=0.8, color='#888888'), 'relpos': (0 if ha == 'left' else 1, 0 if xytext[1] < 0 else 1)},
                        transform=ccrs.PlateCarree(), zorder=1)
            ax.annotate(pix_labels[i], (pt_lon, pt_lat),
                        textcoords="offset points", xytext=xytext,
                        fontsize=6.5, color='white', ha=ha,
                        bbox=dict(boxstyle='round,pad=0.15', fc='#00000088', ec='none'),
                        transform=ccrs.PlateCarree(), zorder=90)
        ax.set_title(f"{storm_name} ({storm_id.upper()}) -- Forecast Track", fontsize=11, color='#d0d0e0')
        fig.canvas.draw()
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                    facecolor='#1a1a2e', edgecolor='none')
        buf.seek(0)
        pil_img = PILImage.open(buf).convert("RGBA")
        arr = np.array(pil_img)
        h, w = arr.shape[:2]
        qimg = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg)
        plt.close(fig)
        buf.close()
        return pix



    def _download_update_for_storm(self, storm_id):
        # Check if it's a JMA storm (direct or suffix-fallback)
        jma_data = self.main_ui.jma_storms.get(storm_id)
        if jma_data is None:
            for k in getattr(self.main_ui, 'jma_storms', {}):
                if k.endswith(storm_id) or storm_id.endswith(k):
                    storm_id = k
                    jma_data = self.main_ui.jma_storms.get(k)
                    break
        if jma_data is not None:
            self.main_ui._jma_download_update_for_storm(storm_id)
            return
        self.main_ui.fc_status_label.setText(f"Updating {storm_id}...")
        storm_data = self.main_ui.nhc_storms.get(storm_id)
        if not storm_data:
            return
        storm_name = storm_data.get("storm_name", storm_id)
        classification = storm_data.get("classification", "")
        basin = storm_data.get("basin", "EP")
        from src.clients.nhc import NHCDownloader, _dl_zip_and_extract, fetch_active_storms, _download_file
        import json as _json
        nhc_dir = nhc.get_nhc_data_dir()
        storm_dir = nhc_dir / storm_id
        storm_dir.mkdir(exist_ok=True)
        active = fetch_active_storms()
        storm_meta = next((s for s in active if s.get("id", "").lower() == storm_id), None)
        if storm_meta is None:
            self.main_ui.nhc_status_label.setText(f"{storm_name} ({storm_id}) no longer active on NHC.")
            return
        # Update storm name from live metadata (handles placeholder?real name changes)
        live_name = storm_meta.get("name", "")
        if live_name and live_name != storm_name:
            old_placeholder = storm_name
            storm_name = live_name
            storm_data["storm_name"] = live_name
            self.main_ui.log(f"NHC storm renamed: {old_placeholder} ? {live_name} ({storm_id})")
        ft = storm_meta.get("forecastTrack", {})
        tc = storm_meta.get("trackCone", {})
        btg = storm_meta.get("bestTrackGIS", {})
        iwe = storm_meta.get("initialWindExtent", {})
        fwr = storm_meta.get("forecastWindRadiiGIS", {})
        eat = storm_meta.get("earliestArrivalTimeTSWindsGIS", {})
        mlt = storm_meta.get("mostLikelyTimeTSWindsGIS", {})
        wsp = storm_meta.get("windSpeedProbabilitiesGIS", {})
        dler = NHCDownloader()

        # ---- Download and parse track + cone KMZs ----
        for key, kmz_key, url_key, fallback in [
            ("kmz_track", "TRACK", "kmzFile", f"https://www.nhc.noaa.gov/storm_graphics/api/{storm_id.upper()}_TRACK_latest.kmz"),
            ("kmz_cone", "CONE", "kmzFile", f"https://www.nhc.noaa.gov/storm_graphics/api/{storm_id.upper()}_CONE_latest.kmz"),
        ]:
            url = ft.get(url_key, fallback) if key == "kmz_track" else tc.get(url_key, fallback)
            try:
                self.main_ui.nhc_status_label.setText(f"  Downloading {kmz_key} KMZ for {storm_name}...")
                QApplication.processEvents()
                dest = storm_dir / f"{storm_id}_{kmz_key}.kmz"
                _download_file(url, dest)
                storm_data[key] = str(dest)
            except Exception:
                pass

        kmz_track = storm_dir / f"{storm_id}_TRACK.kmz"
        kmz_cone = storm_dir / f"{storm_id}_CONE.kmz"
        if kmz_track.exists():
            storm_data["track_points"] = dler._parse_kmz_track_points(kmz_track)
        if kmz_cone.exists():
            storm_data["cone_polygons"] = dler._parse_kmz_cone_polygon(kmz_cone)

        # ---- Download and parse wind radii KMZs ----
        for radii_key, zdict, out_key_prefix, out_dest_key, kmz_key in [
            ("initial", iwe, "wind_radii_initial", "kmz_wind_initial", "initialradii"),
            ("forecast", fwr, "wind_radii_forecast", "kmz_wind_forecast", "forecastradii"),
        ]:
            kmz_u = zdict.get("kmzFile", f"https://www.nhc.noaa.gov/storm_graphics/api/{storm_id.upper()}_{kmz_key}_latest.kmz")
            try:
                self.main_ui.nhc_status_label.setText(f"  Downloading {radii_key} wind radii KMZ for {storm_name}...")
                QApplication.processEvents()
                dest = storm_dir / f"{storm_id}_{kmz_key}.kmz"
                _download_file(kmz_u, dest)
                storm_data[out_dest_key] = str(dest)
            except Exception:
                pass
            kmz_path = storm_dir / f"{storm_id}_{kmz_key}.kmz"
            if kmz_path.exists():
                storm_data[out_key_prefix] = dler._parse_kmz_wind_radii(kmz_path)

        # ---- Download and parse best track KMZ ----
        bt_kmz = btg.get("kmzFile", f"https://www.nhc.noaa.gov/gis/best_track/{storm_id}_best_track.kmz")
        try:
            self.main_ui.nhc_status_label.setText(f"  Downloading best track KMZ for {storm_name}...")
            QApplication.processEvents()
            dest = storm_dir / f"{storm_id}_best_track.kmz"
            _download_file(bt_kmz, dest)
            storm_data["best_track_kmz"] = str(dest)
        except Exception:
            pass
        bt_kmz_path = storm_dir / f"{storm_id}_best_track.kmz"
        if bt_kmz_path.exists():
            bt_pts, bt_line = dler._parse_kmz_best_track(bt_kmz_path)
            if bt_pts:
                storm_data["best_track_points"] = bt_pts
            if bt_line:
                storm_data["best_track_line"] = bt_line
        meta = {k: v for k, v in storm_data.items() if k not in ('cone_polygons',)}
        meta_path = storm_dir / f"{storm_id}_meta.json"
        try:
            with open(meta_path, 'w') as f:
                _json.dump(meta, f, indent=2, default=str)
        except Exception:
            pass
        from src.core.helpers import _save_tracks_to_disk
        self.main_ui._upsert_nhc_track_entry(storm_id, storm_data)
        _save_tracks_to_disk(self.main_ui.tracks)
        self.main_ui._refresh_tracks_list()
        self.main_ui._refresh_nhc_storm_combo()
        if hasattr(self.main_ui, 'update_overlays'):
            self.main_ui.update_overlays()
        self.main_ui.nhc_status_label.setText(f"Updated {storm_name} ({storm_id}).")



    def _upsert_nhc_track_entry(self, storm_id, data, saved_tree_fields=None):
        track_id = f"nhc_{storm_id}"
        track_points = data.get("track_points", [])
        existing = next((t for t in self.main_ui.tracks if t.get("id") == track_id), None)
        saved_tree_fields = saved_tree_fields or {}
        entry_data = {
            "id": track_id,
            "name": f"{data.get('storm_name', storm_id.upper())} NHC Forecast",
            "type": data.get("classification", "Tropical Storm"),
            "year": int(storm_id[-4:]) if len(storm_id) >= 4 and storm_id[-4:].isdigit() else 2026,
            "basin": "East Pacific" if storm_id.startswith("ep") else "Atlantic" if storm_id.startswith("al") else "Central Pacific",
            "notes": saved_tree_fields.get("notes", f"NHC advisory #{data.get('advisory_number', '')} -- nhc.noaa.gov"),
            "color": saved_tree_fields.get("color", "#00BFFF" if storm_id.startswith("ep") else "#FF6B35" if storm_id.startswith("al") else "#FFD700"),
            "visible": saved_tree_fields.get("visible", saved_tree_fields.get("main_tree", False)),
            "add_to_infobox": saved_tree_fields.get("add_to_infobox", False),
            "display_options": {
                "show_track_line": True, "show_points": True, "show_cone": True,
                "show_wind_radii": True, "show_best_track": True,
                "show_labels": True, "show_label_name": True, "show_label_time": True,
                "show_label_speed": True, "show_label_category": True,
            },
            "points": [{
                "lon": p["lon"], "lat": p["lat"],
                "datetime": p.get("datetime", ""),
                "intensity": p.get("intensity"),
                "intensity_category": p.get("intensity_category"),
            } for p in track_points],
        }
        st = saved_tree_fields.get("sub_tree", {})
        if st:
            dopts = entry_data["display_options"]
            dopts["show_track_line"] = st.get("track_line", True)
            dopts["show_points"] = st.get("points", True)
            dopts["show_cone"] = st.get("cone", True)
            dopts["show_wind_radii"] = st.get("wind_radii", True)
            dopts["show_best_track"] = st.get("best_track", True)
        lt = saved_tree_fields.get("label_tree", {})
        if lt:
            dopts = entry_data["display_options"]
            dopts["show_label_name"] = lt.get("name", True)
            dopts["show_label_time"] = lt.get("time", True)
            dopts["show_label_speed"] = lt.get("speed", True)
            dopts["show_label_category"] = lt.get("category", True)
        if existing is not None:
            if not saved_tree_fields:
                saved_dopts = existing.get("display_options", {})
            existing.update(entry_data)
            if not saved_tree_fields and saved_dopts:
                existing["display_options"] = saved_dopts
        else:
            self.main_ui.tracks.append(entry_data)
        from src.core.helpers import _save_tracks_to_disk
        _save_tracks_to_disk(self.main_ui.tracks)



    def _refresh_tracks_list(self):

        """Refresh tropical cyclone tracks list.

        Updates the tracks list widget with current storm data.
        Shows storm name, intensity, and agency. Highlights
        selected storm.

        Side Effects:
            - Updates tracks list UI
            - Preserves selection state
            - Updates storm count display
        """
        t = getattr(self.main_ui, '_theme', None)
        print(f"DEBUG: _theme={type(t)} {t}")
        if not t or not isinstance(t, dict) or 'fg2' not in t:
            print(f"DEBUG: Falling back to Dark (Default)")
            t = THEMES["Dark (Default)"]
        print(f"DEBUG: Using t={t.keys() if isinstance(t, dict) else 'not a dict'}")
        self.main_ui._refreshing_tracks = True
        self.main_ui.tracks_list.blockSignals(True)
        try:
            self._build_tracks_tree(t)
        finally:
            self.main_ui.tracks_list.blockSignals(False)
            self.main_ui._refreshing_tracks = False


    def _build_tracks_tree(self, t):

        expanded_ids = set()
        for i in range(self.main_ui.tracks_list.topLevelItemCount()):
            item = self.main_ui.tracks_list.topLevelItem(i)
            tid = item.data(0, Qt.UserRole)
            if tid and item.isExpanded():
                expanded_ids.add(tid)
        self.main_ui.tracks_list.clear()

        _LABEL_SUB = [("show_label_name","Name"),("show_label_time","Time"),("show_label_speed","Speed"),("show_label_category","Category")]

        def _mk_child(text, key, parent, _visible, _dopts, _subs=None):
            child = QTreeWidgetItem()
            child.setText(0, text)
            child.setData(0, Qt.UserRole, f"dopt:{key}" if key else None)
            if _visible:
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
            else:
                child.setFlags(child.flags() & ~Qt.ItemIsUserCheckable)
            child.setCheckState(0, Qt.Checked if _dopts.get(key, True) else Qt.Unchecked)
            parent.addChild(child)
            if _subs:
                lbl_vis = _visible and _dopts.get(key, True)
                for sk, sl in _subs:
                    sub = QTreeWidgetItem()
                    sub.setText(0, sl)
                    sub.setData(0, Qt.UserRole, f"dopt:{sk}")
                    if lbl_vis:
                        sub.setFlags(sub.flags() | Qt.ItemIsUserCheckable)
                    else:
                        sub.setFlags(sub.flags() & ~Qt.ItemIsUserCheckable)
                    sub.setCheckState(0, Qt.Checked if _dopts.get(sk, True) else Qt.Unchecked)
                    child.addChild(sub)
            return child

        for track in self.main_ui.tracks:
            name = track.get("name") or "(unnamed)"
            typ = track.get("type", "")
            year = track.get("year", "")
            pts = track.get("points") or []
            n_pts = len(pts)

            intensity_str = ""
            if pts and (pts[0].get("intensity_category") or pts[0].get("intensity") is not None):
                cats = set()
                vals = []
                for p in pts:
                    ic = p.get("intensity_category")
                    iv = p.get("intensity")
                    if ic: cats.add(ic)
                    if iv is not None: vals.append(iv)
                if cats:
                    _cat_order = {"TD":0,"TS":1,"STS":2,"TY":3,"STY":4,"Hurricane":5,"Major Hurricane":6}
                    sorted_cats = sorted(cats, key=lambda c: _cat_order.get(c,9))
                    intensity_str = f" [{', '.join(sorted_cats)}]"
                elif vals:
                    wu = self.main_ui.settings.get("forecast_preferences", {}).get("wind_format", "kt")
                    if wu == "kmh": ul = "km/h"; sf = 1.852
                    elif wu == "mph": ul = "mph"; sf = 1.151
                    elif wu == "ms": ul = "m/s"; sf = 0.514
                    else: ul = "kt"; sf = 1
                    intensity_str = f" [{round(min(vals)*sf):.0f}-{round(max(vals)*sf):.0f}{ul}]"

            display = f"{name}  •  {typ} {year}{intensity_str}"
            if n_pts > 0:
                display += f"  ({n_pts} pts)"

            top_item = QTreeWidgetItem()
            top_item.setData(0, Qt.UserRole, track.get("id"))
            visible = track.get("visible", False)

            dopts = track.get("display_options", {})
            is_nhc = track.get("id","").startswith("nhc_")

            if is_nhc:
                wid = QWidget()
                wl = QHBoxLayout(wid)
                wl.setContentsMargins(4, 1, 4, 1)
                wl.setSpacing(4)
                cb = QCheckBox()
                cb.setChecked(visible)
                cb.setStyleSheet("QCheckBox::indicator { width: 14px; height: 14px; }")
                cb.stateChanged.connect(lambda chk, tid=track.get("id"): self.main_ui._set_nhc_track_check(tid, chk))
                wl.addWidget(cb)
                display = f"{name}"
                if n_pts > 0:
                    display += f"  ({n_pts} pts)"
                nl = QLabel(display)
                nl.setStyleSheet(f"color: {t['fg2']}; font-size: 10px;")
                wl.addWidget(nl)
                wl.addStretch()
                upd = QPushButton("Update")
                upd.setFixedHeight(22)
                upd.setStyleSheet(f"QPushButton {{ background: {t['bg3']}; color: {t['fg']}; font-size: 9px; padding: 1px 6px; border: 1px solid {t['border2']}; }} QPushButton:hover {{ background: {t['menusel']}; }}")
                upd.clicked.connect(lambda checked=False, sid=track.get("id")[4:]: self.main_ui._download_update_for_storm(sid))
                wl.addWidget(upd)
                genc = QPushButton("Forecast")
                genc.setFixedHeight(22)
                genc.setStyleSheet("QPushButton { background: #3a5f1e; color: #b0ff90; font-size: 9px; padding: 1px 6px; } QPushButton:hover { background: #4a7f2a; }")
                _sid = track.get("id")
                for _p in ("nhc_", "jma_", "jtwc_", "pagasa_", "cwa_"):
                    if _sid.startswith(_p):
                        _sid = _sid[len(_p):]
                        break
                genc.clicked.connect(lambda checked=False, storm_id=_sid: self._launch_forecast_worker(storm_id))
                wl.addWidget(genc)
                top_item.setSizeHint(0, QSize(0, 24))
                self.main_ui.tracks_list.addTopLevelItem(top_item)
                self.main_ui.tracks_list.setItemWidget(top_item, 0, wid)
                _mk_child("Cone", "show_cone", top_item, visible, dopts)
                _mk_child("Track Line", "show_track_line", top_item, visible, dopts)
                _mk_child("Points", "show_points", top_item, visible, dopts)
                _mk_child("Label", "show_labels", top_item, visible, dopts, _LABEL_SUB)
                _mk_child("Wind Radii", "show_wind_radii", top_item, visible, dopts)
                _mk_child("Best Track", "show_best_track", top_item, visible, dopts)
            else:
                wid = QWidget()
                wl = QHBoxLayout(wid)
                wl.setContentsMargins(4, 1, 4, 1)
                wl.setSpacing(4)
                cb = QCheckBox()
                cb.setChecked(visible)
                cb.setStyleSheet("QCheckBox::indicator { width: 14px; height: 14px; }")
                cb.stateChanged.connect(lambda chk, tid=track.get("id"): self.main_ui._set_nhc_track_check(tid, chk))
                wl.addWidget(cb)
                nl = QLabel(display)
                nl.setStyleSheet(f"color: {t['fg2']}; font-size: 10px;")
                wl.addWidget(nl)
                wl.addStretch()
                upd = QPushButton("Update")
                upd.setFixedHeight(22)
                upd.setStyleSheet(f"QPushButton {{ background: {t['bg3']}; color: {t['fg']}; font-size: 9px; padding: 1px 6px; border: 1px solid {t['border2']}; }} QPushButton:hover {{ background: {t['menusel']}; }}")
                upd.clicked.connect(lambda checked=False, sid=track.get("id")[4:]: self.main_ui._download_update_for_storm(sid))
                wl.addWidget(upd)
                genc = QPushButton("Forecast")
                genc.setFixedHeight(22)
                genc.setStyleSheet("QPushButton { background: #3a5f1e; color: #b0ff90; font-size: 9px; padding: 1px 6px; } QPushButton:hover { background: #4a7f2a; }")
                _sid = track.get("id")
                for _p in ("nhc_", "jma_", "jtwc_", "pagasa_", "cwa_"):
                    if _sid.startswith(_p):
                        _sid = _sid[len(_p):]
                        break
                genc.clicked.connect(lambda checked=False, storm_id=_sid: self._launch_forecast_worker(storm_id))
                wl.addWidget(genc)
                top_item.setSizeHint(0, QSize(0, 24))
                self.main_ui.tracks_list.addTopLevelItem(top_item)
                self.main_ui.tracks_list.setItemWidget(top_item, 0, wid)
                _mk_child("Cone", "show_cone", top_item, visible, dopts)
                _mk_child("Track Line", "show_track_line", top_item, visible, dopts)
                _mk_child("Points", "show_points", top_item, visible, dopts)
                _mk_child("Label", "show_labels", top_item, visible, dopts, _LABEL_SUB)
                _mk_child("History Path", "show_hist_path", top_item, visible, dopts)
                _mk_child("Prob Circle", "show_prob_circle", top_item, visible, dopts)
                _mk_child("Storm Warn", "show_swa", top_item, visible, dopts)

            if pts:
                sep = QTreeWidgetItem()
                sep.setText(0, "--- Points ---")
                sep.setFlags(sep.flags() & ~Qt.ItemIsUserCheckable)
                sep.setForeground(0, QColor(t['fg3']))
                top_item.addChild(sep)
                for p in pts:
                    pt_dt = p.get("datetime", "")
                    pt_int = p.get("intensity")
                    pt_cat = p.get("intensity_category", "")
                    parts = []
                    if pt_dt:
                        parts.append(pt_dt)
                    if pt_int is not None:
                        wv = float(pt_int)
                        wu = self.main_ui.settings.get("forecast_preferences", {}).get("wind_format", "kt")
                        if wu == "kmh": wv = round(wv * 1.852); parts.append(f"{wv} km/h")
                        elif wu == "mph": wv = round(wv * 1.151); parts.append(f"{wv} mph")
                        elif wu == "ms": wv = round(wv * 0.514); parts.append(f"{wv} m/s")
                        else: parts.append(f"{wv:.0f} kt")
                    if pt_cat:
                        parts.append(pt_cat)
                    pt_text = " | ".join(parts) if parts else "(no data)"
                    pt_item = QTreeWidgetItem()
                    pt_item.setText(0, pt_text)
                    pt_item.setFlags(pt_item.flags() & ~Qt.ItemIsUserCheckable)
                    pt_item.setForeground(0, QColor(t['fg3']))
                    top_item.addChild(pt_item)

        for i in range(self.main_ui.tracks_list.topLevelItemCount()):
            item = self.main_ui.tracks_list.topLevelItem(i)
            tid = item.data(0, Qt.UserRole)
            if tid in expanded_ids:
                item.setExpanded(True)


    def _debounced_refresh_tracks(self):
        if not hasattr(self.main_ui, '_refresh_timer'):
            from PySide6.QtCore import QTimer
            self.main_ui._refresh_timer = QTimer()
            self.main_ui._refresh_timer.setSingleShot(True)
            self.main_ui._refresh_timer.timeout.connect(self.main_ui._refresh_tracks_list)
        self.main_ui._refresh_timer.start(250)



    def _propagate_children(self, parent_item, checked, dopts):
        _SAVED = Qt.UserRole + 1
        for i in range(parent_item.childCount()):
            child = parent_item.child(i)
            dopt_data = child.data(0, Qt.UserRole)
            if not isinstance(dopt_data, str) or not dopt_data.startswith("dopt:"):
                continue
            dopt_key = dopt_data.split(":", 1)[1]
            if not checked:
                child.setData(0, _SAVED, dopts.get(dopt_key, True))
                child.setCheckState(0, Qt.Unchecked)
                child.setFlags(child.flags() & ~Qt.ItemIsUserCheckable)
                dopts[dopt_key] = False
            else:
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                saved = child.data(0, _SAVED)
                child.setCheckState(0, Qt.Checked if saved else Qt.Unchecked)
                dopts[dopt_key] = bool(saved)
                child.setData(0, _SAVED, None)
            self.main_ui._propagate_children(child, checked, dopts)



    def _set_nhc_track_check(self, track_id, check_state):
        if getattr(self.main_ui, '_refreshing_tracks', False):
            return
        checked = check_state != 0
        for t in self.main_ui.tracks:
            if t.get("id") == track_id:
                t["visible"] = checked
                from src.core.helpers import _save_tracks_to_disk
                _save_tracks_to_disk(self.main_ui.tracks)
                self.main_ui._debounced_refresh_tracks()
                if hasattr(self.main_ui, '_update_tracks_overlays'):
                    self.main_ui._update_tracks_overlays()
                break



    def _on_track_item_changed(self, item, column=0):
        data = item.data(column, Qt.UserRole)
        checked = item.checkState(0) == Qt.Checked
        if isinstance(data, str) and data.startswith("dopt:"):
            opt_key = data.split(":", 1)[1]
            top = item.parent()
            while top is not None and top.parent():
                if top.data(0, Qt.UserRole) and not str(top.data(0, Qt.UserRole)).startswith("dopt:"):
                    break
                top = top.parent()
            if not top:
                return
            track_id = top.data(0, Qt.UserRole)
            for t in self.main_ui.tracks:
                if t.get("id") == track_id:
                    t.setdefault("display_options", {})[opt_key] = checked
                    _save_tracks_to_disk(self.main_ui.tracks)
                    self.main_ui._debounced_refresh_tracks()
                    if hasattr(self.main_ui, '_update_tracks_overlays'):
                        self.main_ui._update_tracks_overlays()
                    break
        # ATCF track item handling disabled
        # elif isinstance(data, str) and data.startswith("atcf:"):
        #     if data in ("atcf:latest", "atcf:invest"):
        #         for i in range(item.childCount()):
        #             child = item.child(i)
        #             child.setCheckState(0, Qt.Checked if checked else Qt.Unchecked)
        #             cd = child.data(0, Qt.UserRole)
        #             if isinstance(cd, str) and cd.startswith("atcf:"):
        #                 sid = cd.split(":", 1)[1]
        #                 self.main_ui._set_atcf_storm_visibility(sid, checked)
        #     else:
        #         storm_id = data.split(":", 1)[1]
        #         self.main_ui._set_atcf_storm_visibility(storm_id, checked)
        #         parent = item.parent()
        #         if parent:
        #             all_checked = all(
        #                 parent.child(i).checkState(0) == Qt.Checked
        #                 for i in range(parent.childCount())
        #             )
        #             parent.setCheckState(0, Qt.Checked if all_checked else Qt.Unchecked)
        else:
            track_id = data
            if track_id and track_id.startswith("nhc_"):
                return
            for t in self.main_ui.tracks:
                if t.get("id") == track_id:
                    t["visible"] = checked
                    _save_tracks_to_disk(self.main_ui.tracks)
                    self.main_ui._debounced_refresh_tracks()
                    if hasattr(self.main_ui, '_update_tracks_overlays'):
                        self.main_ui._update_tracks_overlays()
                    break



    def _delete_selected_track(self):
        cur = self.main_ui.tracks_list.currentItem()
        if not cur:
            return
        if cur.parent():
            cur = cur.parent()
        track_id = cur.data(0, Qt.UserRole)
        idx = next((i for i in range(self.main_ui.tracks_list.topLevelItemCount())
                    if self.main_ui.tracks_list.topLevelItem(i).data(0, Qt.UserRole) == track_id), -1)
        if idx < 0:
            return
        self.main_ui.tracks_list.takeTopLevelItem(idx)
        self.main_ui.tracks = [t for t in self.main_ui.tracks if t.get("id") != track_id]
        if getattr(self.main_ui, '_editing_track_id', None) == track_id:
            self.main_ui._editing_track_id = None
            if hasattr(self.main_ui, 'create_track_btn'):
                self.main_ui.create_track_btn.setText("Create as Track (saved)")
        if track_id and track_id.startswith("nhc_"):
            storm_id = track_id[4:]
            nhc_dir = nhc.get_nhc_data_dir()
            storm_path = nhc_dir / storm_id
            if storm_path.exists():
                try:
                    import shutil
                    shutil.rmtree(storm_path)
                except Exception:
                    pass
            self.main_ui.nhc_storms.pop(storm_id, None)
            self.main_ui._refresh_nhc_storm_combo()
        elif track_id and track_id.startswith("jma_"):
            storm_id = track_id[4:]
            self.main_ui.jma_storms.pop(storm_id, None)
            self.main_ui._refresh_jma_storm_combo()
        elif track_id and track_id.startswith("jtwc_"):
            storm_id = track_id[4:]
            self.main_ui.jtwc_storms.pop(storm_id, None)
            self.main_ui._refresh_jtwc_storm_combo()
        elif track_id and track_id.startswith("pagasa_"):
            storm_id = track_id[7:]
            self.main_ui.pagasa_storms.pop(storm_id, None)
            self.main_ui._refresh_pagasa_storm_combo()
        elif track_id and track_id.startswith("cwa_"):
            storm_id = track_id[4:]
            if hasattr(self.main_ui, 'cwa_storms'):
                self.main_ui.cwa_storms.pop(storm_id, None)
            self.main_ui._refresh_cwa_storm_combo()
        self.main_ui.log("Track deleted.")
        _save_tracks_to_disk(self.main_ui.tracks)
        if hasattr(self.main_ui, '_update_tracks_overlays'):
            self.main_ui._update_tracks_overlays()



    def _create_tracks_tab(self) -> QWidget:

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header = QLabel("METEOROLOGICAL TRACKS")
        header.setStyleSheet("color: #f472b6; font-weight: bold; font-size: 11px;")
        layout.addWidget(header)

        self.main_ui.tracks_list = QTreeWidget()
        self.main_ui.tracks_list.setHeaderHidden(True)
        self.main_ui.tracks_list.setStyleSheet("QTreeWidget { background: #1f1f1f; color: #ddd; border: 1px solid #444; } QTreeWidget::item { padding: 2px 0px; }")
        self.main_ui.tracks_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.main_ui.tracks_list.setIndentation(20)
        self.main_ui.tracks_list.setExpandsOnDoubleClick(True)
        self.main_ui.tracks_list.customContextMenuRequested.connect(self.main_ui._show_tracks_context_menu)
        self.main_ui.tracks_list.itemSelectionChanged.connect(self.main_ui._on_track_selection_changed)
        self.main_ui.tracks_list.itemChanged.connect(self.main_ui._on_track_item_changed)
        layout.addWidget(self.main_ui.tracks_list, 1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("New Track...")
        add_btn.clicked.connect(self.main_ui._show_new_track_dialog)
        btn_row.addWidget(add_btn)

        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self.main_ui._refresh_tracks_list)
        btn_row.addWidget(refresh_btn)

        del_btn = QPushButton("Delete Selected")
        del_btn.clicked.connect(self.main_ui._delete_selected_track)
        btn_row.addWidget(del_btn)

        edit_btn = QPushButton("Edit Selected")
        edit_btn.clicked.connect(self.main_ui._edit_selected_track)
        btn_row.addWidget(edit_btn)
        
        infobox_btn = QPushButton("Show in Info Box")
        infobox_btn.clicked.connect(self.main_ui._toggle_track_in_infobox)
        btn_row.addWidget(infobox_btn)

        combined_fc_btn = QPushButton("Combined Forecast")
        combined_fc_btn.setStyleSheet("QPushButton { background: #6d28d9; color: #c4b5fd; font-weight: bold; } QPushButton:hover { background: #7c3aed; }")
        combined_fc_btn.clicked.connect(self.main_ui._open_combined_forecast_dialog)
        btn_row.addWidget(combined_fc_btn)

        layout.addLayout(btn_row)

        # -- Climate & Weather Overlays (CPC NOAA) --
        self.main_ui._climate_weather_group = QGroupBox("Climate and Weather")
        self.main_ui._climate_weather_group.setStyleSheet("QGroupBox { color: #4CAF50; font-weight: bold; border: 1px solid #444; border-radius: 5px; margin-top: 6px; padding-top: 8px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        cw_l = QVBoxLayout(self.main_ui._climate_weather_group)
        cw_l.setSpacing(3)

        cw_status = QLabel("CPC NOAA Climate Prediction Center")
        cw_status.setStyleSheet("color: #888; font-size: 9px;")
        cw_l.addWidget(cw_status)

        self.main_ui.cw_refresh_btn = QPushButton("Refresh All Climate Data")
        self.main_ui.cw_refresh_btn.setStyleSheet("QPushButton { background: #1e3a5f; color: #90d5ff; font-weight: bold; padding: 4px; } QPushButton:hover { background: #2a4a7f; }")
        self.main_ui.cw_refresh_btn.clicked.connect(self.main_ui._refresh_all_climate_data)
        cw_l.addWidget(self.main_ui.cw_refresh_btn)

        self.main_ui.cw_status_label = QLabel("")
        self.main_ui.cw_status_label.setStyleSheet("color: #aaa; font-size: 9px;")
        self.main_ui.cw_status_label.setWordWrap(True)
        cw_l.addWidget(self.main_ui.cw_status_label)

        _cw_cb_style = "QCheckBox { color: #EEE; padding: 3px 5px; font-size: 10px; background: #2D2D2D; border-radius: 3px; } QCheckBox::indicator { width: 14px; height: 14px; } QCheckBox:hover:!disabled { background: #3D3D3D; }"
        _cw_combo_style = "QComboBox { background: #2D2D2D; color: #CCC; border: 1px solid #444; padding: 2px 4px; font-size: 10px; min-width: 70px; } QComboBox::drop-down { border: none; } QComboBox QAbstractItemView { background: #2D2D2D; color: #CCC; selection-background-color: #3D3D3D; }"

        self.main_ui.climate_hazards = [
            ("Tropical Cyclone Formation Probability", "TC", "#FF6B6B"),
            ("Enhanced Precipitation Probability",    "WET", "#4ECDC4"),
            ("Suppressed Precipitation Probability",  "DRY", "#FFE66D"),
            ("Above Average Temperatures Probability", "WARM", "#FF8C42"),
            ("Below Average Temperatures Probability", "COLD", "#74B9FF"),
        ]

        self.main_ui.climate_widgets = {}
        for label, code, default_color in self.main_ui.climate_hazards:
            row = QHBoxLayout()
            row.setSpacing(4)
            cb = QCheckBox(label)
            cb.setStyleSheet(_cw_cb_style)
            enabled_key = f"climate_{code.lower()}_enabled"
            initial_enabled = self.main_ui.settings.get(enabled_key, False)
            cb.setChecked(bool(initial_enabled))
            cb.toggled.connect(lambda checked, c=code: self.main_ui._toggle_climate_overlay(c, checked))
            row.addWidget(cb, 1)

            week_combo = QComboBox()
            week_combo.addItems(["Week-2", "Week-3"])
            week_combo.setStyleSheet(_cw_combo_style)
            week_key = f"climate_{code.lower()}_week"
            initial_week = self.main_ui.settings.get(week_key, "Week-2")
            week_combo.setCurrentText(initial_week)
            week_combo.currentTextChanged.connect(lambda week, c=code: self.main_ui._on_climate_week_changed(c, week))
            row.addWidget(week_combo)

            gen_solo_btn = QPushButton("Map")
            gen_solo_btn.setFixedWidth(32)
            gen_solo_btn.setStyleSheet("QPushButton { background: #4CAF50; color: white; font-weight: bold; padding: 2px 2px; font-size: 8px; border-radius: 2px; } QPushButton:hover { background: #5CBF60; }")
            gen_solo_btn.clicked.connect(lambda checked, c=code, w=week_combo: self.main_ui._generate_single_climate_overlay(c, w.currentText()))
            row.addWidget(gen_solo_btn)

            cw_l.addLayout(row)
            self.main_ui.climate_widgets[code] = {"checkbox": cb, "combo": week_combo}

        color_row = QHBoxLayout()
        color_row.setSpacing(4)
        cpc_defaults_btn = QPushButton("CPC Default Colors")
        cpc_defaults_btn.setStyleSheet("QPushButton { background: #2D2D2D; color: #AAA; border: 1px solid #444; padding: 3px 6px; font-size: 9px; border-radius: 3px; } QPushButton:hover { background: #3D3D3D; }")
        cpc_defaults_btn.clicked.connect(self.main_ui._reset_climate_colors)
        color_row.addWidget(cpc_defaults_btn)

        self.main_ui.climate_color_btns = {}
        for code, default_color in [(c[1], c[2]) for c in self.main_ui.climate_hazards]:
            color_key = f"climate_{code.lower()}_color"
            saved_color = self.main_ui.settings.get("climate_overlay_colors", {}).get(code, default_color)
            cid = f"clr_{code}_{uuid.uuid4().hex[:4]}"
            btn = QPushButton(code)
            btn.setToolTip(f"Click to change color for {code}")
            btn.setFixedWidth(40)
            btn.setStyleSheet(f"QPushButton {{ background: {saved_color}; border: 1px solid #666; border-radius: 3px; padding: 2px; font-size: 8px; color: {'#000' if saved_color > '#888888' else '#FFF'}; }}")
            btn.clicked.connect(lambda checked, c=code, b=btn: self.main_ui._pick_climate_color(c, b))
            color_row.addWidget(btn)
            self.main_ui.climate_color_btns[code] = btn

        gen_btn = QPushButton("Generate Map")
        gen_btn.setStyleSheet("QPushButton { background: #4CAF50; color: white; font-weight: bold; padding: 3px 8px; font-size: 9px; border-radius: 3px; } QPushButton:hover { background: #5CBF60; }")
        gen_btn.clicked.connect(self.main_ui._generate_climate_overlay)
        color_row.addWidget(gen_btn)

        cw_l.addLayout(color_row)

        # -- GTWO (Graphical Tropical Weather Outlook) --
        gtwo_row = QHBoxLayout()
        gtwo_row.setSpacing(4)
        self.main_ui.gtwo_cb = QCheckBox("Graphical Tropical Weather Outlook")
        self.main_ui.gtwo_cb.setStyleSheet(_cw_cb_style)
        gtwo_enabled = self.main_ui.settings.get("gtwo_enabled", False)
        self.main_ui.gtwo_cb.setChecked(bool(gtwo_enabled))
        self.main_ui.gtwo_cb.toggled.connect(self.main_ui._toggle_gtwo_overlay)
        gtwo_row.addWidget(self.main_ui.gtwo_cb, 1)
        self.main_ui.gtwo_refresh_btn = QPushButton("Refresh")
        self.main_ui.gtwo_refresh_btn.setFixedWidth(52)
        self.main_ui.gtwo_refresh_btn.setStyleSheet("QPushButton { background: #1e3a5f; color: #90d5ff; font-weight: bold; padding: 2px 4px; font-size: 8px; border-radius: 2px; } QPushButton:hover { background: #2a4a7f; }")
        self.main_ui.gtwo_refresh_btn.clicked.connect(lambda: (self.main_ui._download_gtwo_data(force=True), self.main_ui._render_gtwo_overlay()))
        gtwo_row.addWidget(self.main_ui.gtwo_refresh_btn)
        gtwo_map_btn = QPushButton("Map")
        gtwo_map_btn.setFixedWidth(32)
        gtwo_map_btn.setStyleSheet("QPushButton { background: #4CAF50; color: white; font-weight: bold; padding: 2px 2px; font-size: 8px; border-radius: 2px; } QPushButton:hover { background: #5CBF60; }")
        gtwo_map_btn.clicked.connect(self.main_ui._generate_gtwo_map)
        gtwo_row.addWidget(gtwo_map_btn)
        cw_l.addLayout(gtwo_row)

        layout.addWidget(self.main_ui._climate_weather_group)

        # -- Unified Forecast Tracks (NHC + JMA) --
        forecast_box = QGroupBox("Forecast Tracks")
        forecast_box.setStyleSheet("QGroupBox { color: #a78bfa; }")
        fc_l = QVBoxLayout(forecast_box)

        self.main_ui.download_tc_btn = QPushButton("Download Latest TC Updates")
        self.main_ui.download_tc_btn.setStyleSheet("QPushButton { background: #1e3a5f; color: #90d5ff; font-weight: bold; } QPushButton:hover { background: #2a4a7f; }")
        self.main_ui.download_tc_btn.clicked.connect(self.main_ui._download_all_tc_updates)
        fc_l.addWidget(self.main_ui.download_tc_btn)

        self.main_ui.fc_status_label = QLabel("NHC: idle  |  JMA: idle  |  JTWC: idle  |  PAGASA: idle  |  CWA: idle")
        self.main_ui.fc_status_label.setStyleSheet("color: #aaa; font-size: 9px;")
        self.main_ui.fc_status_label.setWordWrap(True)
        fc_l.addWidget(self.main_ui.fc_status_label)
        self.main_ui.nhc_status_label = self.main_ui.fc_status_label
        self.main_ui.jma_status_label = self.main_ui.fc_status_label
        self.main_ui.jtwc_status_label = self.main_ui.fc_status_label
        self.main_ui.pagasa_status_label = self.main_ui.fc_status_label

        fc_header = QWidget()
        fc_header_h = QHBoxLayout(fc_header)
        fc_header_h.setContentsMargins(0, 0, 0, 0)
        self.main_ui.fc_toggle_btn = QToolButton()
        self.main_ui.fc_toggle_btn.setArrowType(Qt.DownArrow)
        self.main_ui.fc_toggle_btn.setCheckable(True)
        self.main_ui.fc_toggle_btn.setChecked(True)
        self.main_ui.fc_toggle_btn.setAutoRaise(True)
        fc_header_h.addWidget(self.main_ui.fc_toggle_btn)
        self.main_ui.forecast_enable_cb = QCheckBox("Enable Forecast Overlays")
        self.main_ui.forecast_enable_cb.setChecked(True)
        self.main_ui.forecast_enable_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_header_h.addWidget(self.main_ui.forecast_enable_cb)
        fc_header_h.addStretch()
        fc_l.addWidget(fc_header)

        self.main_ui._fc_sub_widget = QWidget()
        fc_sub_l = QVBoxLayout(self.main_ui._fc_sub_widget)
        fc_sub_l.setContentsMargins(16, 0, 0, 0)
        fc_grid = QGridLayout()
        # Row 0: common track options
        self.main_ui.forecast_show_trackline_cb = QCheckBox("Track Line")
        self.main_ui.forecast_show_trackline_cb.setChecked(True)
        self.main_ui.forecast_show_trackline_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.forecast_show_trackline_cb, 0, 0)
        self.main_ui.forecast_show_points_cb = QCheckBox("Points & Labels")
        self.main_ui.forecast_show_points_cb.setChecked(True)
        self.main_ui.forecast_show_points_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.forecast_show_points_cb, 0, 1)
        # Row 1: NHC-specific
        self.main_ui.nhc_show_cone_cb = QCheckBox("Cone")
        self.main_ui.nhc_show_cone_cb.setChecked(True)
        self.main_ui.nhc_show_cone_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.nhc_show_cone_cb, 1, 0)
        self.main_ui.nhc_show_wind_cb = QCheckBox("Wind Radii")
        self.main_ui.nhc_show_wind_cb.setChecked(True)
        self.main_ui.nhc_show_wind_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.nhc_show_wind_cb, 1, 1)
        self.main_ui.nhc_show_besttrack_cb = QCheckBox("Best Track")
        self.main_ui.nhc_show_besttrack_cb.setChecked(True)
        self.main_ui.nhc_show_besttrack_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.nhc_show_besttrack_cb, 2, 0)
        # Row 2: JMA-specific
        self.main_ui.jma_show_hist_cb = QCheckBox("History Path")
        self.main_ui.jma_show_hist_cb.setChecked(True)
        self.main_ui.jma_show_hist_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.jma_show_hist_cb, 2, 1)
        self.main_ui.jma_show_circle_cb = QCheckBox("Probability Circle")
        self.main_ui.jma_show_circle_cb.setChecked(True)
        self.main_ui.jma_show_circle_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.jma_show_circle_cb, 3, 0)
        self.main_ui.jma_show_swa_cb = QCheckBox("Storm Warning Area")
        self.main_ui.jma_show_swa_cb.setChecked(True)
        self.main_ui.jma_show_swa_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.jma_show_swa_cb, 3, 1)
        # Row 4: JTWC-specific
        self.main_ui.jtwc_show_cone_cb = QCheckBox("Cone")
        self.main_ui.jtwc_show_cone_cb.setChecked(True)
        self.main_ui.jtwc_show_cone_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.jtwc_show_cone_cb, 4, 0)
        self.main_ui.jtwc_show_wind_cb = QCheckBox("Wind Radii")
        self.main_ui.jtwc_show_wind_cb.setChecked(True)
        self.main_ui.jtwc_show_wind_cb.toggled.connect(self.main_ui._toggle_forecast_overlays)
        fc_grid.addWidget(self.main_ui.jtwc_show_wind_cb, 4, 1)
        fc_sub_l.addLayout(fc_grid)
        fc_l.addWidget(self.main_ui._fc_sub_widget)

        self.main_ui.fc_toggle_btn.toggled.connect(lambda c: self.main_ui._fc_sub_widget.setVisible(c))

        self.main_ui.storm_combo = QComboBox()
        self.main_ui.storm_combo.setToolTip("Filter overlay display by storm (NHC + JMA combined)")
        self.main_ui.storm_combo.currentIndexChanged.connect(self.main_ui._on_storm_filter_changed)
        fc_l.addWidget(self.main_ui.storm_combo)

        algo_row = QHBoxLayout()
        algo_row.setSpacing(4)
        algo_row.addWidget(QLabel("Label Algo:"))
        self.main_ui.label_algo_combo = QComboBox()
        self.main_ui.label_algo_combo.addItems([
            "Polar", "Bezier", "Smart Bezier",
            "Greedy", "Offset", "Force", "Anneal", "MILP", "Auto",
            "Railway Bezier", "8-Direction", "Staggered Perp", "Anti-Clima V2"
        ])
        current_algo = self.main_ui.settings.get("labeling_method", "polar")
        algo_map = {
            "polar": "Polar", "bezier": "Bezier", "smart_bezier": "Smart Bezier",
            "greedy": "Greedy", "offset": "Offset", "force": "Force",
            "anneal": "Anneal", "milp": "MILP", "auto": "Auto",
            "railway_bezier": "Railway Bezier", "8direction": "8-Direction",
            "staggered_perp": "Staggered Perp", "anticlima_v2": "Anti-Clima V2"
        }
        display_text = algo_map.get(current_algo, "Polar")
        self.main_ui.label_algo_combo.setCurrentText(display_text)
        self.main_ui.label_algo_combo.currentTextChanged.connect(self._on_label_algo_changed)
        self.main_ui.label_algo_combo.setStyleSheet(
            "QComboBox { background: #2D2D2D; color: #CCC; border: 1px solid #444; "
            "padding: 2px 4px; font-size: 10px; min-width: 90px; }"
        )
        algo_row.addWidget(self.main_ui.label_algo_combo)
        algo_row.addStretch()
        fc_l.addLayout(algo_row)

        layout.addWidget(forecast_box)

        self.main_ui._refresh_tracks_list()
        return container



    def _refresh_custom_aor_points_list(self):

        self.main_ui.custom_points_list.clear()
        for p in self.main_ui.current_custom_aor_points:
            self.main_ui.custom_points_list.addItem(f"{p[1]:.4f}, {p[0]:.4f}")



    def _create_custom_aor_as_track(self):

        if not hasattr(self.main_ui, 'current_custom_aor_points') or len(self.main_ui.current_custom_aor_points) < 3:
            QMessageBox.warning(self.main_ui, "Custom AoR", "You need at least 3 points to create a polygon AoR.")
            return

        if not hasattr(self.main_ui, 'tracks'):
            self.main_ui.tracks = []

        new_points = [{"lon": p[0], "lat": p[1]} for p in self.main_ui.current_custom_aor_points]

        if getattr(self.main_ui, '_editing_track_id', None):

            for t in self.main_ui.tracks:
                if t.get("id") == self.main_ui._editing_track_id:
                    t["points"] = new_points
                    name = t.get("name", "Custom AoR")
                    break
            self.main_ui._editing_track_id = None
            self.main_ui.create_track_btn.setText("Create as Track (saved)")
            self.main_ui.log(f"Updated Custom AoR Track: {name} with {len(new_points)} points")
        else:

            name = f"Custom AoR - {len(self.main_ui.tracks) + 1}"
            track = {
                "id": str(uuid.uuid4())[:8],
                "name": name,
                "type": "Custom AoR",
                "year": QDate.currentDate().year(),
                "basin": "Custom",
                "notes": "User-defined multi-point AoR polygon",
                "color": "#00E5FF",
                "visible": True,
                "add_to_infobox": False,
                "points": new_points,
            }
            self.main_ui.tracks.append(track)
            self.main_ui.log(f"Created Custom AoR Track: {name} with {len(new_points)} points")

        _save_tracks_to_disk(self.main_ui.tracks)
        self.main_ui._refresh_tracks_list()

        if hasattr(self.main_ui, 'right_tab_widget') and hasattr(self.main_ui, 'tracks_tab_index'):
            self.main_ui.right_tab_widget.setCurrentIndex(self.main_ui.tracks_tab_index)

        self.main_ui._clear_custom_aor_points()

        if hasattr(self.main_ui, 'aor_custom_cb'):
            self.main_ui.aor_custom_cb.setChecked(True)



    def _add_custom_aor_point(self):

        if not hasattr(self.main_ui, 'current_custom_aor_points'):
            self.main_ui.current_custom_aor_points = []

        try:
            lat = float(self.main_ui.custom_lat_edit.text().strip())
            lon = float(self.main_ui.custom_lon_edit.text().strip())
        except ValueError:
            QMessageBox.warning(self.main_ui, "Invalid Point", "Please enter valid numbers for Lat and Lon.")
            return
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            QMessageBox.warning(self.main_ui, "Invalid Point", "Lat must be -90..90, Lon -180..180.")
            return

        self.main_ui.current_custom_aor_points.append((lon, lat))
        self.main_ui.custom_points_list.addItem(f"{lat:.4f}, {lon:.4f}")
        self.main_ui._update_aor_overlays()

        self.main_ui.custom_lat_edit.clear()
        self.main_ui.custom_lon_edit.clear()



    def _clear_custom_aor_points(self):

        self.main_ui.current_custom_aor_points = []
        self.main_ui.custom_points_list.clear()
        if getattr(self.main_ui, '_editing_track_id', None):
            self.main_ui._editing_track_id = None
            if hasattr(self.main_ui, 'create_track_btn'):
                self.main_ui.create_track_btn.setText("Create as Track (saved)")
        self.main_ui._update_aor_overlays()



    def _remove_last_custom_aor_point(self):

        if not getattr(self.main_ui, 'current_custom_aor_points', None):
            return
        self.main_ui.current_custom_aor_points.pop()
        if self.main_ui.custom_points_list.count() > 0:
            self.main_ui.custom_points_list.takeItem(self.main_ui.custom_points_list.count() - 1)
        self.main_ui._update_aor_overlays()



    def _remove_selected_custom_aor_point(self, item):

        if not hasattr(self.main_ui, 'custom_points_list') or not hasattr(self.main_ui, 'current_custom_aor_points'):
            return
        row = self.main_ui.custom_points_list.row(item)
        if 0 <= row < len(self.main_ui.current_custom_aor_points):
            self.main_ui.current_custom_aor_points.pop(row)
            self.main_ui.custom_points_list.takeItem(row)
            self.main_ui._update_aor_overlays()



    def _select_nearest_visible_tab(self, removed_index: int):
        tw = self.main_ui.right_tab_widget
        count = tw.count()
        if count == 0:
            return
        idx = min(removed_index, count - 1)
        if tw.isTabVisible(idx):
            tw.setCurrentIndex(idx)
            return
        for i in range(idx + 1, count):
            if tw.isTabVisible(i):
                tw.setCurrentIndex(i)
                return
        for i in range(idx - 1, -1, -1):
            if tw.isTabVisible(i):
                tw.setCurrentIndex(i)
                return



    def _on_tab_context_menu(self, pos):
        tab_bar = self.main_ui.right_tab_widget.tabBar()
        tab_idx = tab_bar.tabAt(pos)
        if tab_idx < 0:
            return
        menu = QMenu(self.main_ui)
        for i in range(self.main_ui.right_tab_widget.count()):
            if self.main_ui.right_tab_widget.isTabVisible(i):
                action = menu.addAction(self.main_ui.right_tab_widget.tabText(i))
                action.setCheckable(True)
                action.setChecked(i == self.main_ui.right_tab_widget.currentIndex())
                action.setData(i)
        menu.addSeparator()
        float_action = menu.addAction("Float Panel" if not hasattr(self.main_ui, 'right_panel_float_window') or not self.main_ui.right_panel_float_window else "Dock Panel")
        menu.addAction("Toggle Right Panel", self.main_ui.toggle_right_panel)
        action = menu.exec(tab_bar.mapToGlobal(pos))
        if action and action.data() is not None:
            target = action.data()
            log.info("Context menu switch to tab index=%d '%s'", target, self.main_ui.right_tab_widget.tabText(target))
            self.main_ui.right_tab_widget.setCurrentIndex(target)
        elif action == float_action:
            log.info("Context menu: toggling float panel")
            self.main_ui.toggle_right_panel_float(not (hasattr(self.main_ui, 'right_panel_float_window') and self.main_ui.right_panel_float_window))



    def _save_active_tab(self, idx: int):
        if hasattr(self.main_ui, 'settings'):
            tab_text = self.main_ui.right_tab_widget.tabText(idx) if hasattr(self.main_ui, 'right_tab_widget') else "?"
            log.info("Tab switched to index=%d '%s'", idx, tab_text)
            self.main_ui.settings.set("active_tab", idx)



    def _restore_active_tab(self):
        if not hasattr(self.main_ui, 'right_tab_widget') or not hasattr(self.main_ui, 'settings'):
            return
        idx = self.main_ui.settings.get("active_tab", 0)
        tw = self.main_ui.right_tab_widget
        if isinstance(idx, int) and 0 <= idx < tw.count():
            if tw.isTabVisible(idx):
                log.info("Restoring active tab: index=%d '%s'", idx, tw.tabText(idx))
                tw.setCurrentIndex(idx)
            else:
                log.info("Skipping tab restore: index=%d '%s' is hidden", idx, tw.tabText(idx))



    def _on_tab_tear_off(self, tab_index: int, global_pos):
        tw = self.main_ui.right_tab_widget
        if tab_index < 0 or tab_index >= tw.count():
            return
        tab_text = tw.tabText(tab_index)
        tab_tooltip = tw.tabToolTip(tab_index)
        tab_icon = tw.tabIcon(tab_index)
        tab_widget = tw.widget(tab_index)
        if not tab_widget:
            return
        log.info("Tearing off tab: index=%d '%s' at global=(%d,%d)", tab_index, tab_text, global_pos.x(), global_pos.y())
        tw.blockSignals(True)
        tw.removeTab(tab_index)
        tw.blockSignals(False)
        self.main_ui._select_nearest_visible_tab(tab_index)
        if not any(tw.isTabVisible(i) for i in range(tw.count())):
            tw.setVisible(False)
            log.info("No visible tabs remain, hiding right panel")
        floating = TabFloatingWindow(tab_text, tab_widget, tab_icon, tab_tooltip, tab_index, self)
        floating.destroyed.connect(lambda: self.main_ui._floating_windows.discard(floating))
        self.main_ui._floating_windows.add(floating)
        floating.move(global_pos - QPoint(floating.width() // 2, 0))
        floating.show()


