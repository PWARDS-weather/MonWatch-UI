# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: controllers/overlay_controller.py
# Description: Geospatial overlay rendering for meteorological products including grids, coastlines, TC tracks, and wind fields.
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
import math
import json
import uuid
import hashlib
import logging
import traceback
from pathlib import Path
from datetime import date, timedelta, datetime, timezone

import numpy as np
import xarray as xr
import rasterio
from pyproj import Transformer, CRS
import shapefile

from PIL import Image as PILImage, ImageDraw, ImageFont
from scipy.ndimage import zoom

_HAS_NEW_ALGOS = False
try:
    from Process.forecast.label_placement_algorithms.integration_hub import (
        run_new_label_pipeline as _new_run_pipeline
    )
    from Process.forecast.label_placement_algorithms.shared_models import (
        adapt_to_offsets_flat as _new_adapt_flat
    )
    _HAS_NEW_ALGOS = True
except ImportError:
    pass

from PySide6.QtCore import QPoint, QPointF, Qt, QRectF, QObject, Signal, QThread, QTimer
from PySide6.QtGui import (
    QPixmap, QIcon, QImage, QPainter, QFont, QPen, QColor, QPainterPath,
    QBrush, QFontMetrics, QPolygonF, QTransform,
)
from PySide6.QtWidgets import (
    QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsPathItem, QGraphicsLineItem,
    QGraphicsEllipseItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsSimpleTextItem,
    QMessageBox,
)

from src.core.helpers import (
    normalize_lon, _point_in_polygon, _densify_polygon,
    COASTLINE_SHP, BORDERS_SHP,
    load_border_segments_np, decimate_screen_points,
    top_dir,
)
from src.workers.core import OverlayPrecacheWorker
from src.workers.AMVWorker import AMVWorker
from src.ui.components import ClickablePixmapItem, ClickablePointItem, ClickablePolygonItem

log = logging.getLogger(__name__)

xp = np


class OverlayController(QObject):
    """Geospatial overlay rendering controller.

    Manages rendering of cartographic overlays including grids,
    coastlines, political boundaries, TC tracks, and wind fields.
    Implements caching for performance optimization.

    Attributes:
        main_ui (MainUI): Reference to the main UI instance.

    Signals:
        overlay_updated (str): Emitted when overlays are refreshed.
        winds_loaded (WindData): Emitted when wind data is available.

    Note:
        Uses cartopy and matplotlib for high-quality cartographic
        rendering. Supports multiple projection systems.
    """

    overlays_updated = Signal()
    info_box_shown = Signal(object, object)
    info_box_removed = Signal()

    def __init__(self, main_ui, parent=None):
        super().__init__(parent)
        self.main_ui = main_ui

    def load_winds_data(self, winds_nc_path: Path = None):

        """Load wind field data.

        Downloads and processes wind speed/direction data from
        numerical weather models or satellite retrievals.

        Args:
            winds_nc_path (Path | None): Path to wind data NetCDF.

        Returns:
            WindData | None: Wind field data or None if load failed.

        Note:
            Supports GRIB and NetCDF wind data formats.
        """

        nc_path = self.main_ui._current_nc_file()
        if not nc_path:
            self.main_ui.log("No NC file loaded -- cannot load winds.")
            return

        if getattr(self, '_amv_worker', None) and getattr(self, '_amv_thread', None) and self.main_ui._amv_thread.isRunning():
            self.main_ui._amv_worker.cancel()
            self.main_ui._amv_thread.quit()
            self.main_ui._amv_thread.wait()

        self.main_ui.status_bar.showMessage("Loading AMV winds (background)...")

        high_res = self.main_ui.settings.get("high_res_amv", False)
        self.main_ui._amv_thread = QThread()
        self.main_ui._amv_worker = AMVWorker(nc_path, high_res_amv=high_res)
        self.main_ui._amv_worker.moveToThread(self.main_ui._amv_thread)
        self.main_ui._amv_worker.winds_loaded.connect(self.main_ui._on_winds_loaded)
        self.main_ui._amv_worker.progress.connect(lambda msg: self.main_ui.log(msg))
        self.main_ui._amv_worker.error.connect(lambda msg: self.main_ui.log(msg))
        self.main_ui._amv_worker.finished.connect(self.main_ui._amv_thread.quit)
        self.main_ui._amv_worker.finished.connect(self.main_ui._amv_worker.deleteLater)
        self.main_ui._amv_thread.finished.connect(self.main_ui._amv_thread.deleteLater)
        self.main_ui._amv_thread.started.connect(self.main_ui._amv_worker.run)
        self.main_ui._amv_thread.finished.connect(lambda: setattr(self, '_amv_thread', None))
        self.main_ui._amv_thread.start()


    def _on_winds_loaded(self, wind_data):

        self.main_ui.current_winds_uv = wind_data
        self.main_ui.winds_enabled = True
        channels_str = ", ".join(sorted(wind_data.keys()))
        total_obs = sum(len(d["lat"]) for d in wind_data.values())
        self.main_ui.log(f"Loaded AMV winds: {channels_str} ({total_obs} obs)")
        self.main_ui.status_bar.showMessage(f"AMV winds: {channels_str} ({total_obs} obs)")
        self.main_ui._winds_persist = False
        self.main_ui._draw_winds_overlay()
        self.main_ui._update_winds_checkbox_state()


    def _has_wind_data(self) -> bool:
        nc = self.main_ui._current_nc_file()
        if not nc or not nc.exists():
            return False
        try:
            with xr.open_dataset(nc, engine="netcdf4") as ds:
                return any(v.startswith("wind_") for v in ds.data_vars)
        except Exception:
            return False


    def _update_winds_checkbox_state(self):

        cb = self.main_ui.overlay_checkboxes.get("Show Winds (AMV)")
        if cb is None:
            return
        has_data = self.main_ui._has_wind_data()
        has_winds = bool(getattr(self.main_ui, 'current_winds_uv', None))
        helper = getattr(self.main_ui, "ascat_controller", None)
        has_ascat = bool(helper is not None and
                         getattr(helper, "has_available_ascat", None) and
                         helper.has_available_ascat())
        available = has_data or has_winds or has_ascat
        cb.blockSignals(True)
        try:
            cb.setEnabled(available)
            if has_winds or has_data:
                if has_data and not has_winds:
                    cb.setToolTip("Toggle AMV wind barb overlay")
                else:
                    cb.setToolTip("Toggle wind barb overlay")
            elif has_ascat:
                cb.setChecked(False)
                self.main_ui.winds_enabled = False
                cb.setToolTip(
                    "ASCAT wind data available locally — check to display it "
                    "(or refresh via System → ASCAT Data & Metadata…)")
            else:
                cb.setChecked(False)
                self.main_ui.winds_enabled = False
                cb.setToolTip("No wind data in current NC file")
        finally:
            cb.blockSignals(False)


    def _toggle_aor_overlay(self, _=None):

        if hasattr(self.main_ui, 'aor_par_cb'):
            self.main_ui.settings.set("par_enabled", self.main_ui.aor_par_cb.isChecked())
        if hasattr(self.main_ui, 'aor_jma_cb'):
            self.main_ui.settings.set("jma_enabled", self.main_ui.aor_jma_cb.isChecked())
        if hasattr(self.main_ui, 'aor_tcad_cb'):
            self.main_ui.settings.set("tcad_enabled", self.main_ui.aor_tcad_cb.isChecked())
        if hasattr(self.main_ui, 'aor_tcid_cb'):
            self.main_ui.settings.set("tcid_enabled", self.main_ui.aor_tcid_cb.isChecked())
        if hasattr(self.main_ui, 'aor_fir_cb'):
            self.main_ui.settings.set("fir_enabled", self.main_ui.aor_fir_cb.isChecked())
        if hasattr(self.main_ui, 'aor_custom_cb'):
            self.main_ui.settings.set("aor_expanded", self.main_ui.aor_custom_cb.isChecked())
        self.main_ui.log("AOR toggled.")
        if self.main_ui.settings.get("aor_highlight", True):
            self.main_ui._update_aor_overlays()


    def _generate_tcad_image(self):
        tcad_coords = [(114.0, 4.0), (114.0, 27.0), (145.0, 27.0), (145.0, 4.0)]
        if not getattr(self.main_ui, 'current_crs', None) or not getattr(self.main_ui, 'current_geotransform', None):
            self.main_ui.log("Cannot generate TCAD: no CRS/geotransform")
            return
        scene = self.main_ui.graphics_view.scene() if hasattr(self.main_ui, 'graphics_view') else None
        if not scene:
            return
        pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
        if not pixmap_item:
            return
        from pyproj import Transformer
        tf = Transformer.from_crs("EPSG:4326", self.main_ui.current_crs, always_xy=True)
        lons = np.array([p[0] for p in tcad_coords], dtype=float)
        lons = np.where(lons > 180, lons - 360, lons)
        lats = np.array([p[1] for p in tcad_coords], dtype=float)
        xp, yp = tf.transform(lons, lats)
        disk_half = self.main_ui._compute_disk_half_extent()
        native_res_m = abs(self.main_ui.current_geotransform.a)
        px = (xp + disk_half) / native_res_m
        py = (disk_half - yp) / native_res_m
        min_x, max_x = float(px.min()), float(px.max())
        min_y, max_y = float(py.min()), float(py.max())
        domain_w = int(max_x - min_x)
        domain_h = int(max_y - min_y)
        from PySide6.QtCore import QRectF
        img = QImage(domain_w, domain_h + 120, QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(painter, QRectF(0, 0, domain_w, domain_h),
                     QRectF(min_x, min_y, domain_w, domain_h))
        painter.fillRect(0, domain_h, domain_w, 120, QColor(255, 255, 255))
        from datetime import timezone as tzmod, timedelta
        fcst_prefs = self.main_ui.settings.get("forecast_preferences", {})
        utc_offset = fcst_prefs.get("utc_offset", 0)
        imagery_dt = getattr(self.main_ui, 'current_datetime', None)
        if imagery_dt:
            try:
                parts = imagery_dt.replace('_', ' ').split()
                dt_obj = datetime.strptime("".join(parts), "%Y%m%d%H%M")
                dt_obj = dt_obj.replace(tzinfo=tzmod.utc)
            except Exception:
                dt_obj = datetime.now(tzmod.utc)
        else:
            dt_obj = datetime.now(tzmod.utc)
        if utc_offset != 0:
            local_dt = dt_obj + timedelta(hours=utc_offset)
            tz_label = {8: "PHT"}.get(utc_offset, f"UTC{utc_offset:+d}")
            time_str = f"TIME: {local_dt.strftime('%I:%M:%S %p')} {tz_label}"
            date_str = f"DATE: {local_dt.strftime('%A, %B %d, %Y')}"
        else:
            time_str = f"TIME: {dt_obj.strftime('%I:%M:%S %p')} UTC"
            date_str = f"DATE: {dt_obj.strftime('%A, %B %d, %Y')} UTC"
        par_poly = [(115,5),(115,15),(120,21),(120,25),(135,25),(135,5)]
        def _in_par(lon, lat):
            inside = False
            j = len(par_poly) - 1
            for i in range(len(par_poly)):
                xi, yi = par_poly[i]
                xj, yj = par_poly[j]
                if ((yi > lat) != (yj > lat)) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
                    inside = not inside
                j = i
            return inside
        storm_rows = []
        seen_cats = {}
        tcid_bounds = (110.0, 155.0, 0.0, 27.0)
        if hasattr(self.main_ui, 'tracks'):
            for t in self.main_ui.tracks:
                if not t.get("visible", True):
                    continue
                pts = t.get("points", [])
                in_tcid = any(
                    tcid_bounds[0] <= p.get("lon", 0) <= tcid_bounds[1] and
                    tcid_bounds[2] <= p.get("lat", 0) <= tcid_bounds[3]
                    for p in pts
                )
                if not in_tcid:
                    continue
                name = t.get("name", "")
                for suffix in [" NHC Forecast", " JMA Forecast", " JTWC Forecast", " Forecast"]:
                    if name.endswith(suffix):
                        name = name[:-len(suffix)]
                        break
                name = name.strip().upper()
                last_cat = pts[0].get("intensity_category", "") if pts else ""
                cat = (last_cat or t.get("type", "")).upper()
                in_par = any(_in_par(p.get("lon", 0), p.get("lat", 0)) for p in pts)
                storm_rows.append(f"{name} ({cat}) {'INSIDE' if in_par else 'OUTSIDE'}")
                sym_name = self.main_ui._category_to_sym(cat, None)
                if sym_name and sym_name not in seen_cats:
                    sp = (top_dir / 'public' / 'images' / 'symbols') / f"{sym_name}.png"
                    if sp.exists():
                        seen_cats[sym_name] = (sp, cat)
        logo_path = top_dir / 'public' / 'images' / 'Monwatch-LOGO.png'
        if logo_path.exists():
            logo_pix = QPixmap(str(logo_path))
            logo_pix = logo_pix.scaledToHeight(100, Qt.SmoothTransformation)
            painter.drawPixmap(6, domain_h + 10, logo_pix)
        fs = max(8, min(12, domain_w // 110))
        hf = QFont("Segoe UI", fs + 1, QFont.Bold)
        nf = QFont("Segoe UI", fs)
        sf = QFont("Segoe UI", fs - 1)
        c_center = 155
        c_right = domain_w - 170
        painter.setPen(QColor(30, 30, 30))
        painter.setFont(hf)
        from PySide6.QtGui import QFontMetrics
        time_w = QFontMetrics(hf).horizontalAdvance(time_str)
        date_w = QFontMetrics(nf).horizontalAdvance(date_str)
        gap = 20
        c_storms = c_center + max(time_w, date_w) + gap
        painter.drawText(c_center, domain_h + 24, time_str)
        painter.drawText(c_storms, domain_h + 24, "ACTIVE STORMS:")
        painter.setFont(nf)
        painter.setPen(QColor(100, 100, 100))
        painter.drawText(c_center, domain_h + 48, date_str)
        painter.setPen(QColor(30, 30, 30))
        dy = domain_h + 48
        for row in storm_rows:
            dy += 16
            if dy > domain_h + 116:
                break
            painter.drawText(c_storms, dy, row)
        def_lbl = {"lpa": "Low Pressure Area", "td": "Tropical Depression", "ts": "Tropical Storm",
                   "sts": "Severe Tropical Storm", "ty": "Typhoon", "sty": "Super Typhoon",
                   "cat1": "Category 1", "cat2": "Category 2", "cat3": "Category 3",
                   "cat4": "Category 4", "cat5": "Category 5",
                   "Hu": "Hurricane"}
        painter.setFont(hf)
        painter.setPen(QColor(30, 30, 30))
        painter.drawText(c_right, domain_h + 24, "LEGEND:")
        painter.setFont(sf)
        dy2 = domain_h + 24
        for sn in ("lpa", "td", "ts", "sts", "ty", "sty", "Hu"):
            cat_here = sn in seen_cats
            sp = (top_dir / 'public' / 'images' / 'symbols') / f"{sn}.png"
            if not sp.exists():
                continue
            dy2 += 16
            if dy2 > domain_h + 118:
                break
            spix = QPixmap(str(sp)).scaledToHeight(13, Qt.SmoothTransformation)
            painter.drawPixmap(c_right, dy2 - 10, spix)
            painter.setPen(QColor(30, 30, 30) if cat_here else QColor(160, 160, 160))
            painter.drawText(c_right + 18, dy2, def_lbl.get(sn, sn.upper()))
        painter.end()
        export_dir = self.main_ui.settings.get("export_folder", str(top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"TCAD_{ts}.png"
        fpath = os.path.join(export_dir, fname)
        img.save(fpath)
        self.main_ui.log(f"TCAD image saved: {fpath}")
        QMessageBox.information(self, "TCAD Generate", f"Saved to:\n{fpath}")


    def _draw_pagasa_overlays(self, project_func, scene):
        if not getattr(self.main_ui, 'pagasa_storms', None):
            return
        master = getattr(self.main_ui, 'forecast_enable_cb', None)
        if master is not None and not master.isChecked():
            return
        for sid, sdata in self.main_ui.pagasa_storms.items():
            track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id") == f"pagasa_{sid}"), None)
            if track_entry is None or not track_entry.get("visible", True):
                continue
            pts = sdata.get("track_points", [])
            if not pts:
                continue

            tdopts = track_entry.get("display_options", {}) if track_entry else {}
            show_line = tdopts.get("show_track_line", True)
            show_pts = tdopts.get("show_points", True)
            show_cone = tdopts.get("show_cone", True)
            show_labels = tdopts.get("show_labels", True)
            show_time = tdopts.get("show_label_time", True)
            show_cat = tdopts.get("show_label_category", True)
            show_name = tdopts.get("show_label_name", True)

            color = QColor("#00E676")
            sub_paths = []
            current_seg = []
            prev_nlon = None
            point_meta = []

            for p in pts:
                lon, lat = p["lon"], p["lat"]
                nlon = normalize_lon(lon)
                gx, gy = project_func(lon, lat)
                if gx is None or gy is None:
                    if current_seg:
                        sub_paths.append(current_seg)
                        current_seg = []
                    prev_nlon = None
                    continue
                if prev_nlon is not None and abs(nlon - prev_nlon) > 180.0:
                    if current_seg:
                        sub_paths.append(current_seg)
                        current_seg = []
                qpt = QPointF(gx, gy)
                current_seg.append(qpt)
                point_meta.append((qpt, p))
                prev_nlon = nlon
            if current_seg:
                sub_paths.append(current_seg)

            # Best track classification: radius_km=0 followed by another radius_km=0
            is_best_track = []
            for i, (qpt, p) in enumerate(point_meta):
                r = p.get("radius_km", 0)
                if r == 0 and i + 1 < len(point_meta):
                    next_r = point_meta[i+1][1].get("radius_km", 0)
                    is_best_track.append(next_r == 0)
                else:
                    is_best_track.append(False)

            # Track line
            if show_line:
                for seg in sub_paths:
                    if len(seg) < 2:
                        continue
                    gpath = QPainterPath()
                    gpath.moveTo(seg[0])
                    for pt in seg[1:]:
                        gpath.lineTo(pt)
                    item = QGraphicsPathItem(gpath)
                    pen = QPen(color)
                    pen.setWidth(2)
                    pen.setStyle(Qt.SolidLine)
                    item.setPen(pen)
                    item.setZValue(24)
                    scene.addItem(item)
                    self.main_ui._pagasa_overlay_items.append(item)

            # Points with category symbols (forecast) or dots (best track)
            if show_pts:
                bt_idx = 0
                for j, (qpt, p) in enumerate(point_meta):
                    if is_best_track[j]:
                        r = 4
                        if bt_idx % 2 == 0:
                            dot = QGraphicsEllipseItem(qpt.x() - r, qpt.y() - r, r * 2, r * 2)
                            dot.setPen(QPen(QColor("#42A5F5"), 1))
                            dot.setBrush(QBrush(QColor("#42A5F5")))
                            dot.setZValue(25)
                        else:
                            dot = QGraphicsEllipseItem(qpt.x() - r, qpt.y() - r, r * 2, r * 2)
                            dot.setPen(QPen(QColor("#42A5F5"), 1))
                            dot.setBrush(QBrush(QColor("#FFFFFF")))
                            dot.setZValue(25)
                            inner_r = 2
                            inner = QGraphicsEllipseItem(qpt.x() - inner_r, qpt.y() - inner_r, inner_r * 2, inner_r * 2)
                            inner.setPen(QPen(QColor("#42A5F5"), 0.5))
                            inner.setBrush(QBrush(QColor("#42A5F5")))
                            inner.setZValue(26)
                            scene.addItem(inner)
                            self.main_ui._pagasa_overlay_items.append(inner)
                        scene.addItem(dot)
                        self.main_ui._pagasa_overlay_items.append(dot)
                        bt_idx += 1
                    else:
                        ct = p.get("cyclone_type", "")
                        sym_name = self.main_ui._category_to_sym(ct, None)
                        if sym_name:
                            sp = top_dir / "public" / "images" / "symbols" / f"pag{sym_name}.png"
                            if not sp.exists():
                                sp = top_dir / "public" / "images" / "symbols" / f"{sym_name}.png"
                            if sp.exists():
                                pix = QPixmap(str(sp))
                                pix = pix.scaled(20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                                ip = QGraphicsPixmapItem(pix)
                                ip.setPos(qpt.x() - pix.width() / 2, qpt.y() - pix.height() / 2)
                                ip.setZValue(26)
                                scene.addItem(ip)
                                self.main_ui._pagasa_overlay_items.append(ip)
                                continue
                        r = 4
                        dot = QGraphicsEllipseItem(qpt.x() - r, qpt.y() - r, r * 2, r * 2)
                        dot.setPen(QPen(color.darker(120), 1))
                        dot.setBrush(QBrush(color))
                        dot.setZValue(25)
                        scene.addItem(dot)
                        self.main_ui._pagasa_overlay_items.append(dot)

            # Labels
            if show_labels and point_meta:
                proj = [qpt for qpt, _ in point_meta]
                _algo_name = self.main_ui.settings.get("labeling_method", "polar")
                if _algo_name in ("anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "auto",
                                  "railway_bezier", "8direction", "staggered_perp") and _HAS_NEW_ALGOS:
                    _track_pts = [p for _, p in point_meta]
                    offsets = OverlayController._compute_label_offsets_new(_track_pts, _algo_name)
                else:
                    offsets = self.main_ui._compute_label_offsets(proj, _algo_name) if len(proj) >= 2 else None
                if offsets and len(proj) >= 2:
                    cone_r = []
                    for j, (qpt, p) in enumerate(point_meta):
                        lon, lat = p["lon"], p["lat"]
                        gxe, gye = project_func(lon + 0.01, lat)
                        if gxe is not None:
                            scale = math.hypot(gxe - qpt.x(), gye - qpt.y()) / (0.01 * 111320.0)
                            r_km = p.get("radius_km", 0)
                            if r_km <= 0:
                                r_km = 30 + j * 5.0
                            cone_r.append(r_km * 1000.0 * scale)
                        else:
                            cone_r.append(0)
                    offsets = OverlayController._push_offsets_outside_cone(offsets, proj, cone_r)
                for i, (qpt, p) in enumerate(point_meta):
                    if is_best_track[i]:
                        continue
                    if offsets and i < len(offsets):
                        parts = []
                        if show_time:
                            dt = p.get("datetime", "")
                            if dt:
                                parts.append(dt)
                        if show_cat:
                            ic = p.get("intensity_category", "")
                            if ic:
                                ct = p.get("cyclone_type", "")
                                parts.append(ct or ic)
                        if parts:
                            lbl = " ".join(parts)
                            tl = QGraphicsTextItem(lbl)
                            tl.setDefaultTextColor(color)
                            tl.setFont(QFont("Segoe UI", 6, QFont.Normal))
                            ox, oy, ha = offsets[i]
                            lx = qpt.x() + ox
                            if ha == 'right':
                                lx -= tl.boundingRect().width()
                            tl.setPos(lx, qpt.y() + oy)
                            tl.setZValue(26)
                            scene.addItem(tl)
                            self.main_ui._pagasa_overlay_items.append(tl)

            # Storm name label
            if show_name and point_meta and offsets and offsets[0]:
                storm_name = sdata.get("storm_name", "")
                if storm_name:
                    nl = QGraphicsTextItem(storm_name[:24])
                    nl.setDefaultTextColor(color)
                    nl.setFont(QFont("Segoe UI", 7, QFont.Normal))
                    ox, oy, ha = offsets[0]
                    lx = proj[0].x() + ox * 1.5
                    ly = proj[0].y() + oy - 14
                    if ha == 'right':
                        lx -= nl.boundingRect().width()
                    nl.setPos(lx, ly)
                    nl.setZValue(26)
                    scene.addItem(nl)
                    self.main_ui._pagasa_overlay_items.append(nl)

            # Cone (uncertainty cone based on radius_km)
            if show_cone and len(point_meta) >= 2:
                left_pts = []
                right_pts = []
                cone_color = color
                for i, (qpt, p) in enumerate(point_meta):
                    gx, gy = qpt.x(), qpt.y()
                    lon, lat = p["lon"], p["lat"]
                    gxe, gye = project_func(lon + 0.01, lat)
                    if gxe is None:
                        continue
                    scale = math.hypot(gxe - gx, gye - gy) / (0.01 * 111320.0)
                    r_km = p.get("radius_km", 0)
                    if r_km <= 0:
                        r_km = 30 + i * 5.0
                    r_px = r_km * 1000.0 * scale
                    ci = QGraphicsEllipseItem(gx - r_px, gy - r_px, r_px * 2, r_px * 2)
                    cpen = QPen(cone_color)
                    cpen.setWidth(1)
                    cpen.setStyle(Qt.DashLine)
                    ci.setPen(cpen)
                    ci.setBrush(QBrush(QColor(0, 230, 118, 15)))
                    ci.setZValue(22)
                    scene.addItem(ci)
                    self.main_ui._pagasa_overlay_items.append(ci)
                    if i < len(point_meta) - 1:
                        gx2, gy2 = point_meta[i+1][0].x(), point_meta[i+1][0].y()
                    else:
                        gx2, gy2 = point_meta[i-1][0].x(), point_meta[i-1][0].y()
                    dx = gx2 - gx
                    dy = gy2 - gy
                    dl = math.hypot(dx, dy)
                    if dl < 1:
                        continue
                    perp_x = -dy / dl
                    perp_y = dx / dl
                    left_pts.append(QPointF(gx + perp_x * r_px, gy + perp_y * r_px))
                    right_pts.append(QPointF(gx - perp_x * r_px, gy - perp_y * r_px))

                if len(left_pts) >= 2 and len(right_pts) >= 2:
                    lpath = QPainterPath()
                    lpath.moveTo(left_pts[0])
                    for pt in left_pts[1:]:
                        lpath.lineTo(pt)
                    li = QGraphicsPathItem(lpath)
                    lp = QPen(cone_color)
                    lp.setWidth(1)
                    li.setPen(lp)
                    li.setZValue(22)
                    scene.addItem(li)
                    self.main_ui._pagasa_overlay_items.append(li)
                    rpath = QPainterPath()
                    rpath.moveTo(right_pts[0])
                    for pt in right_pts[1:]:
                        rpath.lineTo(pt)
                    ri = QGraphicsPathItem(rpath)
                    ri.setPen(lp)
                    ri.setZValue(22)
                    scene.addItem(ri)
                    self.main_ui._pagasa_overlay_items.append(ri)
                    fpath = QPainterPath()
                    fpath.moveTo(left_pts[0])
                    for pt in left_pts[1:]:
                        fpath.lineTo(pt)
                    for pt in reversed(right_pts):
                        fpath.lineTo(pt)
                    fpath.closeSubpath()
                    fi = QGraphicsPathItem(fpath)
                    fi.setPen(QPen(Qt.NoPen))
                    fi.setBrush(QBrush(QColor(0, 230, 118, 20)))
                    fi.setZValue(21)
                    scene.addItem(fi)
                    self.main_ui._pagasa_overlay_items.append(fi)


    def _draw_jtwc_overlays(self, project_func, scene):
        if not getattr(self.main_ui, 'jtwc_storms', None):
            return
        master = getattr(self.main_ui, 'forecast_enable_cb', None)
        if master is not None and not master.isChecked():
            return
        for sid, sdata in self.main_ui.jtwc_storms.items():
            track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id") == f"jtwc_{sid}"), None)
            if track_entry is None or not track_entry.get("visible", True):
                continue
            pts = sdata.get("track_points", [])
            if not pts:
                continue

            sub_paths = []
            current_seg = []
            prev_nlon = None
            point_meta = []

            for p in pts:
                lon, lat = p["lon"], p["lat"]
                nlon = normalize_lon(lon)
                gx, gy = project_func(lon, lat)
                if gx is None or gy is None:
                    if current_seg:
                        sub_paths.append(current_seg)
                        current_seg = []
                    prev_nlon = None
                    continue
                if prev_nlon is not None and abs(nlon - prev_nlon) > 180.0:
                    if current_seg:
                        sub_paths.append(current_seg)
                        current_seg = []
                qpt = QPointF(gx, gy)
                current_seg.append(qpt)
                point_meta.append((qpt, p))
                if prev_nlon is None:
                    prev_nlon = nlon
                else:
                    prev_nlon = nlon
            if current_seg:
                sub_paths.append(current_seg)

            color = QColor("#FF4500")
            for seg in sub_paths:
                if len(seg) < 2:
                    continue
                gpath = QPainterPath()
                gpath.moveTo(seg[0])
                for pt in seg[1:]:
                    gpath.lineTo(pt)
                item = QGraphicsPathItem(gpath)
                pen = QPen(color)
                pen.setWidth(2)
                pen.setStyle(Qt.SolidLine)
                item.setPen(pen)
                item.setZValue(24)
                scene.addItem(item)
                self.main_ui._jtwc_overlay_items.append(item)

            tdopts = track_entry.get("display_options", {}) if track_entry else {}
            _jtwc_proj = [qpt for qpt, _ in point_meta]
            _jtwc_algo = self.main_ui.settings.get("labeling_method", "polar")
            if _jtwc_algo in ("anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "auto",
                              "railway_bezier", "8direction", "staggered_perp") and _HAS_NEW_ALGOS:
                _jtwc_track_pts = [p for _, p in point_meta]
                _jtwc_offsets = OverlayController._compute_label_offsets_new(_jtwc_track_pts, _jtwc_algo)
            else:
                _jtwc_offsets = self.main_ui._compute_label_offsets(_jtwc_proj, _jtwc_algo) if len(_jtwc_proj) >= 2 else None
            if _jtwc_offsets and len(_jtwc_proj) >= 2:
                _jtwc_cone_r = []
                for j, (qpt, p) in enumerate(point_meta):
                    lon, lat = p["lon"], p["lat"]
                    gxe, gye = project_func(lon + 0.01, lat)
                    if gxe is not None:
                        scale = math.hypot(gxe - qpt.x(), gye - qpt.y()) / (0.01 * 111320.0)
                        hrs = p.get("advanced_hours", 0)
                        r_km = 30 + hrs * 2.0
                        _jtwc_cone_r.append(r_km * 1000.0 * scale)
                    else:
                        _jtwc_cone_r.append(0)
                _jtwc_offsets = OverlayController._push_offsets_outside_cone(_jtwc_offsets, _jtwc_proj, _jtwc_cone_r)
            _jtwc_wu = self.main_ui.settings.get("forecast_preferences", {}).get("wind_format", "kt")
            _jtwc_show_labels = tdopts.get("show_labels", True)
            _jtwc_show_time = tdopts.get("show_label_time", True)
            _jtwc_show_speed = tdopts.get("show_label_speed", True)
            _jtwc_show_name = tdopts.get("show_label_name", True)
            _jtwc_issued = sdata.get("issued_dtg", "")
            _jtwc_base_day = int(_jtwc_issued[0:2]) if len(_jtwc_issued) >= 6 else 0
            _jtwc_base_hr = int(_jtwc_issued[2:4]) if len(_jtwc_issued) >= 6 else 0
            _jtwc_base_min = int(_jtwc_issued[4:6]) if len(_jtwc_issued) >= 6 else 0
            _jtwc_base_total = _jtwc_base_day * 1440 + _jtwc_base_hr * 60 + _jtwc_base_min

            for i_pt, (qpt, p) in enumerate(point_meta):
                r = 3
                mcolor = color
                dot = QGraphicsEllipseItem(qpt.x() - r, qpt.y() - r, r * 2, r * 2)
                dot.setPen(QPen(mcolor.darker(120), 1))
                dot.setBrush(QBrush(mcolor))
                dot.setZValue(25)
                scene.addItem(dot)
                self.main_ui._jtwc_overlay_items.append(dot)
                # Storm category icon from KMZ (scaled to fit ~20px)
                icon_name = p.get("icon", "")
                icons_dir = sdata.get("icons_dir", "")
                if icon_name and icons_dir:
                    icon_path = os.path.join(icons_dir, icon_name)
                    if os.path.exists(icon_path):
                        pix = QPixmap(icon_path)
                        if not pix.isNull():
                            max_dim = max(pix.width(), pix.height())
                            scale = 30.0 / max_dim if max_dim > 0 else 0.1
                            icon_item = QGraphicsPixmapItem(pix)
                            icon_item.setScale(scale)
                            icon_item.setPos(qpt.x() - pix.width() * scale * 0.5, qpt.y() - pix.height() * scale * 0.5)
                            icon_item.setZValue(27)
                            scene.addItem(icon_item)
                            self.main_ui._jtwc_overlay_items.append(icon_item)
                # Label
                if _jtwc_show_labels and _jtwc_offsets and i_pt < len(_jtwc_offsets):
                    _jtwc_label_parts = []
                    if _jtwc_show_time:
                        ah = p.get("advanced_hours", 0)
                        if _jtwc_base_total > 0:
                            total = _jtwc_base_total + ah * 60
                            d = total // 1440
                            rem = total % 1440
                            _h = rem // 60
                            _m = rem % 60
                            _jtwc_label_parts.append(f"{d:02d}{_h:02d}{_m:02d}Z")
                        else:
                            _jtwc_label_parts.append(f"+{ah}h")
                    if _jtwc_show_speed:
                        ji = p.get("intensity")
                        if ji is not None:
                            jw = float(ji)
                            if _jtwc_wu == "kmh": jw = round(jw * 1.852); _jtwc_label_parts.append(f"{jw:.0f} km/h")
                            elif _jtwc_wu == "mph": jw = round(jw * 1.151); _jtwc_label_parts.append(f"{jw:.0f} mph")
                            elif _jtwc_wu == "ms": jw = round(jw * 0.514); _jtwc_label_parts.append(f"{jw:.0f} m/s")
                            else: _jtwc_label_parts.append(f"{jw:.0f} kt")
                    if _jtwc_label_parts:
                        _jl = QGraphicsTextItem(" ".join(_jtwc_label_parts))
                        _jl.setDefaultTextColor(mcolor)
                        _jl.setFont(QFont("Segoe UI", 6, QFont.Normal))
                        ox, oy, ha = _jtwc_offsets[i_pt]
                        lx = qpt.x() + ox
                        if ha == 'right':
                            lx -= _jl.boundingRect().width()
                        _jl.setPos(lx, qpt.y() + oy)
                        _jl.setZValue(26)
                        scene.addItem(_jl)
                        self.main_ui._jtwc_overlay_items.append(_jl)

            # System name label at first valid point
            if _jtwc_show_name and _jtwc_proj and _jtwc_offsets and _jtwc_offsets[0]:
                _jtwc_name = sdata.get("storm_name", "")
                if _jtwc_name:
                    _jn = QGraphicsTextItem(_jtwc_name[:24])
                    _jn.setDefaultTextColor(color)
                    _jn.setFont(QFont("Segoe UI", 7, QFont.Normal))
                    ox, oy, ha = _jtwc_offsets[0]
                    lx = _jtwc_proj[0].x() + ox * 1.5
                    ly = _jtwc_proj[0].y() + oy - 14
                    if ha == 'right':
                        lx -= _jn.boundingRect().width()
                    _jn.setPos(lx, ly)
                    _jn.setZValue(26)
                    scene.addItem(_jn)
                    self.main_ui._jtwc_overlay_items.append(_jn)

            # -- JTWC forecast cone (KMZ danger swath polygon, fallback to computed) --
            show_cone = tdopts.get("show_cone", True) and (not hasattr(self.main_ui, 'jtwc_show_cone_cb') or self.main_ui.jtwc_show_cone_cb.isChecked())
            if show_cone and len(point_meta) >= 2:
                cone_color = QColor("#FF4500")
                # Prefer danger swath polygon from KMZ
                danger_swath = sdata.get("danger_swath", [])
                if danger_swath and len(danger_swath) >= 3:
                    poly_pts = []
                    for lon, lat in danger_swath:
                        gx, gy = project_func(lon, lat)
                        if gx is not None and gy is not None:
                            poly_pts.append(QPointF(gx, gy))
                    if len(poly_pts) >= 3:
                        fpath = QPainterPath()
                        fpath.moveTo(poly_pts[0])
                        for pt in poly_pts[1:]:
                            fpath.lineTo(pt)
                        fpath.closeSubpath()
                        fi = QGraphicsPathItem(fpath)
                        fi.setPen(QPen(cone_color, 1))
                        fi.setBrush(QBrush(QColor(255, 69, 0, 22)))
                        fi.setZValue(21)
                        scene.addItem(fi)
                        self.main_ui._jtwc_overlay_items.append(fi)
                else:
                    # Fallback: computed circles + tangent edges
                    left_pts = []
                    right_pts = []
                    for i, (qpt, p) in enumerate(point_meta):
                        gx, gy = qpt.x(), qpt.y()
                        lon, lat = p["lon"], p["lat"]
                        gxe, gye = project_func(lon + 0.01, lat)
                        if gxe is None:
                            continue
                        scale = math.hypot(gxe - gx, gye - gy) / (0.01 * 111320.0)
                        hrs = p.get("advanced_hours", 0)
                        r_km = 30 + hrs * 2.0
                        r_px = r_km * 1000.0 * scale
                        ci = QGraphicsEllipseItem(gx - r_px, gy - r_px, r_px * 2, r_px * 2)
                        cpen = QPen(cone_color)
                        cpen.setWidth(1)
                        cpen.setStyle(Qt.DashLine)
                        ci.setPen(cpen)
                        ci.setBrush(QBrush(QColor(255, 69, 0, 12)))
                        ci.setZValue(22)
                        scene.addItem(ci)
                        self.main_ui._jtwc_overlay_items.append(ci)
                        if i < len(point_meta) - 1:
                            gx2, gy2 = point_meta[i+1][0].x(), point_meta[i+1][0].y()
                        else:
                            gx2, gy2 = point_meta[i-1][0].x(), point_meta[i-1][0].y()
                        dx = gx2 - gx; dy = gy2 - gy
                        dl = math.hypot(dx, dy)
                        if dl < 1:
                            continue
                        perp_x = -dy / dl; perp_y = dx / dl
                        left_pts.append(QPointF(gx + perp_x * r_px, gy + perp_y * r_px))
                        right_pts.append(QPointF(gx - perp_x * r_px, gy - perp_y * r_px))
                    if len(left_pts) >= 2 and len(right_pts) >= 2:
                        for tag, pts in [("left", left_pts), ("right", right_pts)]:
                            path = QPainterPath()
                            path.moveTo(pts[0])
                            for pt in pts[1:]:
                                path.lineTo(pt)
                            li = QGraphicsPathItem(path)
                            li.setPen(QPen(cone_color, 1))
                            li.setZValue(22)
                            scene.addItem(li)
                            self.main_ui._jtwc_overlay_items.append(li)
                        fpath = QPainterPath()
                        fpath.moveTo(left_pts[0])
                        for pt in left_pts[1:]:
                            fpath.lineTo(pt)
                        for pt in reversed(right_pts):
                            fpath.lineTo(pt)
                        fpath.closeSubpath()
                        fi = QGraphicsPathItem(fpath)
                        fi.setPen(QPen(Qt.NoPen))
                        fi.setBrush(QBrush(QColor(255, 69, 0, 18)))
                        fi.setZValue(21)
                        scene.addItem(fi)
                        self.main_ui._jtwc_overlay_items.append(fi)

            # -- JTWC wind radii (quadrant arc segments per point) --
            show_wr = tdopts.get("show_wind_radii", True) and (not hasattr(self.main_ui, 'jtwc_show_wind_cb') or self.main_ui.jtwc_show_wind_cb.isChecked())
            if show_wr and len(point_meta) >= 2:
                wr_scale_cache = {}
                for qpt, p in point_meta:
                    gx, gy = qpt.x(), qpt.y()
                    lon, lat = p["lon"], p["lat"]
                    key = (round(lon, 3), round(lat, 3))
                    if key not in wr_scale_cache:
                        gxe, gye = project_func(lon + 0.01, lat)
                        if gxe is None:
                            continue
                        wr_scale_cache[key] = math.hypot(gxe - gx, gye - gy) / (0.01 * 111320.0)
                    scale = wr_scale_cache[key]
                    pt_radii = p.get("wind_radii", {})
                    if not pt_radii:
                        continue
                    for kt_key, fill_color in [("34", QColor(0, 200, 0, 55)), ("50", QColor(255, 165, 0, 55)), ("64", QColor(255, 50, 50, 55))]:
                        radii = pt_radii.get(kt_key, {})
                        if not radii or all(v is None for v in radii.values()):
                            continue
                        for quad, start_deg, end_deg in [("ne", -90, 0), ("se", 0, 90), ("sw", 90, 180), ("nw", 180, 270)]:
                            r_nm = radii.get(quad)
                            if r_nm is None:
                                continue
                            r_px = r_nm * 1852.0 * scale
                            poly = QPolygonF()
                            poly.append(QPointF(gx, gy))
                            n_steps = 6
                            for i in range(n_steps + 1):
                                a = start_deg + (end_deg - start_deg) * i / n_steps
                                ar = math.radians(a)
                                poly.append(QPointF(gx + r_px * math.cos(ar), gy + r_px * math.sin(ar)))
                            item = QGraphicsPolygonItem(poly)
                            item.setPen(QPen(fill_color.lighter(130), 1))
                            item.setBrush(fill_color)
                            item.setZValue(22)
                            scene.addItem(item)
                            self.main_ui._jtwc_overlay_items.append(item)


    def _remove_point_info_box(self):
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return
        for item in getattr(self.main_ui, '_point_info_items', []):
            try:
                if item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        self.main_ui._point_info_items = []


    def _show_point_info_box(self, point_data, scene_pos):
        scene = self.main_ui.graphics_view.scene()
        if not scene or not point_data:
            return
        self.main_ui._remove_point_info_box()
        lines = []
        if point_data.get("location"):
            lines.append(f"Location: {point_data['location']}")
        if point_data.get("datetime"):
            lines.append(f"Time: {point_data['datetime']}")
        ah = point_data.get("advanced_hours", 0)
        lines.append(f"Forecast: {ah}h" if ah else "Analysis")
        ic = point_data.get("intensity_category")
        if ic:
            lines.append(f"Category: {ic}")
        intensity = point_data.get("intensity")
        if intensity is not None:
            lines.append(f"Wind: {intensity} kt")
        gust = point_data.get("wind_gust_kt")
        if gust is not None:
            lines.append(f"Gust: {gust} kt")
        pressure = point_data.get("pressure")
        if pressure:
            lines.append(f"Pressure: {pressure} hPa")
        course = point_data.get("course")
        if course:
            lines.append(f"Course: {course}")
        speed = point_data.get("speed_kt")
        if speed is not None:
            lines.append(f"Speed: {speed} kt")
        scale = point_data.get("scale")
        if scale:
            lines.append(f"Scale: {scale}")
        il = point_data.get("intensity_label")
        if il:
            lines.append(f"Intensity: {il}")
        pckm = point_data.get("prob_circle_km")
        if pckm is not None:
            lines.append(f"Prob Circle: {pckm} km")
        swkm = point_data.get("storm_warning_km")
        if swkm is not None:
            lines.append(f"Storm Warning: {swkm} km")
        if not lines:
            lines.append("No details available")
        text = "\n".join(lines)
        item = QGraphicsTextItem(text)
        item.setDefaultTextColor(QColor("#ffffff"))
        item.setFont(QFont("Segoe UI", 8))
        item.setPos(scene_pos.x(), scene_pos.y() - 20)
        item.setZValue(100)
        bg = QGraphicsRectItem(item.boundingRect())
        bg.setPos(item.pos())
        bg.setBrush(QBrush(QColor(26, 26, 46, 220)))
        bg.setPen(QPen(QColor("#60a5fa"), 1))
        bg.setZValue(99)
        scene.addItem(bg)
        scene.addItem(item)
        self.main_ui._point_info_items = [bg, item]
        # Dismiss on next click
        self.main_ui._dismiss_info = True


    def _draw_jma_overlays(self, project_func, scene):
        if not getattr(self.main_ui, 'jma_storms', None):
            return
        master = getattr(self.main_ui, 'forecast_enable_cb', None)
        if master is not None and not master.isChecked():
            return
        for sid, sdata in self.main_ui.jma_storms.items():
            track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id") == f"jma_{sid}"), None)
            if track_entry is None or not track_entry.get("visible", True):
                continue
            tdopts = track_entry.get("display_options", {}) if track_entry else {}

            show_line = tdopts.get("show_track_line", True) and (not hasattr(self.main_ui, 'forecast_show_trackline_cb') or self.main_ui.forecast_show_trackline_cb.isChecked())
            show_pts = tdopts.get("show_points", True) and (not hasattr(self.main_ui, 'forecast_show_points_cb') or self.main_ui.forecast_show_points_cb.isChecked())
            show_hist = tdopts.get("show_hist_path", True) and (not hasattr(self.main_ui, 'jma_show_hist_cb') or self.main_ui.jma_show_hist_cb.isChecked())
            show_circle = tdopts.get("show_cone", True) and (not hasattr(self.main_ui, 'jma_show_circle_cb') or self.main_ui.jma_show_circle_cb.isChecked())
            show_swa = tdopts.get("show_swa", True) and (not hasattr(self.main_ui, 'jma_show_swa_cb') or self.main_ui.jma_show_swa_cb.isChecked())

            # -- Historical track path --
            if show_hist:
                hist = sdata.get("track_history", [])
                if len(hist) >= 2:
                    segs = []
                    cur = []
                    prev_nlon = None
                    for pt in hist:
                        lat, lon = float(pt[0]), float(pt[1])
                        nlon = normalize_lon(lon)
                        gx, gy = project_func(lon, lat)
                        if gx is None:
                            if cur: segs.append(cur); cur = []
                            prev_nlon = None
                            continue
                        if prev_nlon is not None and abs(nlon - prev_nlon) > 180:
                            if cur: segs.append(cur); cur = []
                        cur.append(QPointF(gx, gy))
                        prev_nlon = nlon
                    if cur: segs.append(cur)
                    for seg in segs:
                        if len(seg) < 2: continue
                        path = QPainterPath()
                        path.moveTo(seg[0])
                        for pt in seg[1:]:
                            path.lineTo(pt)
                        item = QGraphicsPathItem(path)
                        pen = QPen(QColor("#F1F5FE"))
                        pen.setWidth(1)
                        pen.setStyle(Qt.DashLine)
                        item.setPen(pen)
                        item.setZValue(23)
                        scene.addItem(item)
                        self.main_ui._jma_overlay_items.append(item)

            # -- Forecast track line --
            if show_line:
                pts = sdata.get("track_points", [])
                if len(pts) >= 2:
                    segs = []
                    cur = []
                    prev_nlon = None
                    for p in pts:
                        lon, lat = p["lon"], p["lat"]
                        nlon = normalize_lon(lon)
                        gx, gy = project_func(lon, lat)
                        if gx is None:
                            if cur: segs.append(cur); cur = []
                            prev_nlon = None
                            continue
                        if prev_nlon is not None and abs(nlon - prev_nlon) > 180:
                            if cur: segs.append(cur); cur = []
                        cur.append(QPointF(gx, gy))
                        prev_nlon = nlon
                    if cur: segs.append(cur)
                    for seg in segs:
                        if len(seg) < 2: continue
                        path = QPainterPath()
                        path.moveTo(seg[0])
                        for pt in seg[1:]:
                            path.lineTo(pt)
                        item = QGraphicsPathItem(path)
                        pen = QPen(QColor("#F1F5FE"))
                        pen.setWidth(2)
                        pen.setStyle(Qt.DashLine)
                        item.setPen(pen)
                        item.setZValue(24)
                        scene.addItem(item)
                        self.main_ui._jma_overlay_items.append(item)

            # -- Points with clickable category icons --
            if show_pts:
                _sym_dir = top_dir / "public" / "images" / "symbols"
                _jma_track_pts = sdata.get("track_points", [])
                _jma_proj = []
                for p in _jma_track_pts:
                    gx, gy = project_func(p["lon"], p["lat"])
                    _jma_proj.append(QPointF(gx, gy) if gx is not None else None)
                _jma_valid_pts = [q for q in _jma_proj if q is not None]
                _jma_algo = self.main_ui.settings.get("labeling_method", "polar")
                if _jma_algo in ("anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "auto",
                                  "railway_bezier", "8direction", "staggered_perp") and _HAS_NEW_ALGOS:
                    _jma_offsets = OverlayController._compute_label_offsets_new(_jma_track_pts, _jma_algo)
                else:
                    _jma_offsets = self.main_ui._compute_label_offsets(_jma_valid_pts, _jma_algo) if len(_jma_valid_pts) >= 2 else None
                _jma_wu = self.main_ui.settings.get("forecast_preferences", {}).get("wind_format", "kt")
                _jma_show_labels = tdopts.get("show_labels", True)
                _jma_show_time = tdopts.get("show_label_time", True)
                _jma_show_speed = tdopts.get("show_label_speed", True)
                _jma_show_name = tdopts.get("show_label_name", True)
                _jma_idx = 0
                for p in _jma_track_pts:
                    lon, lat = p["lon"], p["lat"]
                    gx, gy = project_func(lon, lat)
                    if gx is None:
                        continue
                    ah = p.get("advanced_hours", 0)
                    pt_cat = p.get("intensity_category") or ""
                    pt_int = p.get("intensity")
                    # Background dot
                    if ah == 0:
                        color = QColor("#ffffff"); r = 6
                    elif ah <= 48:
                        color = QColor("#fbbf24"); r = 5
                    else:
                        color = QColor("#f472b6"); r = 4
                    dot = ClickablePointItem(gx, gy, r, dict(p), self,
                        pen=QPen(color.darker(120), 1), brush=QBrush(color))
                    dot.setZValue(25)
                    scene.addItem(dot)
                    self.main_ui._jma_overlay_items.append(dot)
                    # Category symbol on top
                    if pt_cat:
                        sym_name = self.main_ui._category_to_sym(pt_cat, pt_int)
                        if sym_name:
                            sym_path = _sym_dir / f"{sym_name}.png"
                            if not hasattr(self, '_sym_cache'):
                                self.main_ui._sym_cache = {}
                            if sym_path not in self.main_ui._sym_cache:
                                self.main_ui._sym_cache[sym_path] = QPixmap(str(sym_path))
                            pix = self.main_ui._sym_cache.get(sym_path)
                            if pix and not pix.isNull():
                                sz = 28
                                csi = ClickablePixmapItem(pix, dict(p), self, gx - sz / 2, gy - sz / 2, sz)
                                csi.setScale(sz / max(pix.width(), pix.height()))
                                csi.setZValue(27)
                                scene.addItem(csi)
                                self.main_ui._jma_overlay_items.append(csi)
                    # Label
                    if _jma_show_labels and _jma_offsets and _jma_idx < len(_jma_offsets):
                        _jp = QPointF(gx, gy)
                        _j_label_parts = []
                        if _jma_show_time:
                            jdt = p.get("datetime", "")
                            if jdt:
                                try:
                                    jdt_obj = datetime.fromisoformat(jdt.replace("Z", "+00:00"))
                                    _j_label_parts.append(jdt_obj.strftime("%H:%M"))
                                except Exception:
                                    _j_label_parts.append(jdt)
                        if _jma_show_speed and pt_int is not None:
                            jw = float(pt_int)
                            if _jma_wu == "kmh": jw = round(jw * 1.852); _j_label_parts.append(f"{jw:.0f} km/h")
                            elif _jma_wu == "mph": jw = round(jw * 1.151); _j_label_parts.append(f"{jw:.0f} mph")
                            elif _jma_wu == "ms": jw = round(jw * 0.514); _j_label_parts.append(f"{jw:.0f} m/s")
                            else: _j_label_parts.append(f"{jw:.0f} kt")
                        if _j_label_parts:
                            _jl = QGraphicsTextItem(" ".join(_j_label_parts))
                            _jl.setDefaultTextColor(color)
                            _jl.setFont(QFont("Segoe UI", 6, QFont.Normal))
                            ox, oy, ha = _jma_offsets[_jma_idx]
                            lx = _jp.x() + ox
                            if ha == 'right':
                                lx -= _jl.boundingRect().width()
                            _jl.setPos(lx, _jp.y() + oy)
                            _jl.setZValue(26)
                            scene.addItem(_jl)
                            self.main_ui._jma_overlay_items.append(_jl)
                    _jma_idx += 1
                # System name label at first valid point
                if _jma_show_name and _jma_valid_pts and _jma_offsets and _jma_offsets[0]:
                    _jma_name = sdata.get("storm_name", "")
                    if _jma_name:
                        _jn = QGraphicsTextItem(_jma_name[:24])
                        _jn.setDefaultTextColor(QColor("#F1F5FE"))
                        _jn.setFont(QFont("Segoe UI", 7, QFont.Normal))
                        ox, oy, ha = _jma_offsets[0]
                        lx = _jma_valid_pts[0].x() + ox * 1.5
                        ly = _jma_valid_pts[0].y() + oy - 14
                        if ha == 'right':
                            lx -= _jn.boundingRect().width()
                        _jn.setPos(lx, ly)
                        _jn.setZValue(26)
                        scene.addItem(_jn)
                        self.main_ui._jma_overlay_items.append(_jn)

            # -- Probability cone (tangent polylines + dashed radius circles) --
            if show_circle:
                prob_circles = sdata.get("probability_circles", [])
                if not prob_circles:
                    # Fallback: draw simple radius circles from point prob_circle_km
                    for p in sdata.get("track_points", []):
                        pckm = p.get("prob_circle_km")
                        if not pckm: continue
                        lon, lat = p["lon"], p["lat"]
                        gxp, gyp = project_func(lon, lat)
                        if gxp is None: continue
                        gxe, gye = project_func(lon + 0.01, lat)
                        if gxe is None: continue
                        scale = math.hypot(gxe - gxp, gye - gyp) / (0.01 * 111320.0)
                        r_px = pckm * 1000.0 * scale
                        item = QGraphicsEllipseItem(gxp - r_px, gyp - r_px, r_px * 2, r_px * 2)
                        pen = QPen(QColor(241, 245, 254, 120))
                        pen.setWidth(1)
                        pen.setStyle(Qt.DashLine)
                        item.setPen(pen)
                        item.setBrush(QBrush(QColor(241, 245, 254, 15)))
                        item.setZValue(22)
                        scene.addItem(item)
                        self.main_ui._jma_overlay_items.append(item)
                for circ in prob_circles:
                    c = circ.get("center")
                    if not c or len(c) < 2: continue
                    clat, clon = float(c[0]), float(c[1])
                    radius_m = circ.get("radius", 0)
                    tangents = circ.get("tangent", [])
                    gxc, gyc = project_func(clon, clat)
                    if gxc is None: continue
                    # -- Tangent lines (cone wedge) --
                    for seg in tangents:
                        if not seg or len(seg) < 2: continue
                        pts_q = []
                        for pt in seg:
                            if len(pt) < 2: continue
                            tlat, tlon = float(pt[0]), float(pt[1])
                            gx_t, gy_t = project_func(tlon, tlat)
                            if gx_t is None: break
                            pts_q.append(QPointF(gx_t, gy_t))
                        if len(pts_q) >= 2:
                            path = QPainterPath()
                            path.moveTo(pts_q[0])
                            for qp in pts_q[1:]:
                                path.lineTo(qp)
                            item = QGraphicsPathItem(path)
                            pen = QPen(QColor(241, 245, 254, 140))
                            pen.setWidth(1.5)
                            item.setPen(pen)
                            item.setZValue(22)
                            scene.addItem(item)
                            self.main_ui._jma_overlay_items.append(item)
                    # -- Radius circle at endpoint --
                    if radius_m > 0:
                        gxe, gye = project_func(clon + 0.01, clat)
                        if gxe is not None:
                            scale = math.hypot(gxe - gxc, gye - gyc) / (0.01 * 111320.0)
                            r_px = radius_m * scale
                            item = QGraphicsEllipseItem(gxc - r_px, gyc - r_px, r_px * 2, r_px * 2)
                            pen = QPen(QColor(241, 245, 254, 160))
                            pen.setWidth(1)
                            pen.setStyle(Qt.DashLine)
                            item.setPen(pen)
                            item.setBrush(QBrush(QColor(241, 245, 254, 25)))
                            item.setZValue(22)
                            scene.addItem(item)
                            self.main_ui._jma_overlay_items.append(item)

            # -- Storm warning areas (smooth Catmull-Rom cone + boundary detail) --
            if show_swa:
                # Build smooth storm-warning cone from track points' storm_warning_km
                _sw_pts = sdata.get("track_points", [])
                if len(_sw_pts) >= 2:
                    _cp = []
                    for tp in _sw_pts:
                        sw_km = tp.get("storm_warning_km")
                        if not sw_km: continue
                        _cp.append((float(tp["lon"]), float(tp["lat"]), float(sw_km) * 1000.0))
                    if len(_cp) >= 2:
                        def _cr_pts(arr, n):
                            if len(arr) < 2: return arr
                            if len(arr) == 2:
                                out = []
                                for j in range(n):
                                    t = j / n
                                    out.append(tuple(a + (b - a) * t for a, b in zip(arr[0], arr[1])))
                                out.append(arr[-1]); return out
                            out = []
                            for i in range(len(arr) - 1):
                                p0 = arr[max(0, i - 1)]; p1 = arr[i]; p2 = arr[i + 1]; p3 = arr[min(len(arr) - 1, i + 2)]
                                for j in range(n):
                                    t = j / n; t2 = t * t; t3 = t2 * t
                                    out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in range(len(arr[0]))))
                            out.append(arr[-1]); return out
                        interp = _cr_pts(_cp, 20)
                        n = len(interp)
                        last_dx = _cp[-1][0] - _cp[-2][0]; last_dy = _cp[-1][1] - _cp[-2][1]; last_h = math.hypot(last_dx, last_dy)
                        left_lons, left_lats, right_lons, right_lats = [], [], [], []
                        for i, (lon, lat, r) in enumerate(interp):
                            if i == 0:
                                dx = interp[1][0] - interp[0][0]; dy = interp[1][1] - interp[0][1]
                            elif i == n - 1 and last_h > 1e-10:
                                dx, dy = last_dx, last_dy
                            else:
                                dx = interp[min(i + 1, n - 1)][0] - interp[max(0, i - 1)][0]
                                dy = interp[min(i + 1, n - 1)][1] - interp[max(0, i - 1)][1]
                            h = math.hypot(dx, dy)
                            if h < 1e-10:
                                if i > 0:
                                    dx = interp[i][0] - interp[i - 1][0]; dy = interp[i][1] - interp[i - 1][1]
                                elif i < n - 1:
                                    dx = interp[i + 1][0] - interp[i][0]; dy = interp[i + 1][1] - interp[i][1]
                                else: dx, dy = 1.0, 0.0
                                h = math.hypot(dx, dy)
                                if h < 1e-10:
                                    left_lons.append(lon); left_lats.append(lat); right_lons.append(lon); right_lats.append(lat); continue
                            nx = -dy / h; ny = dx / h
                            cos_lat = math.cos(math.radians(lat))
                            r_lon = r / (111320.0 * cos_lat) if cos_lat > 0.01 else r / 111320.0
                            r_lat = r / 111320.0
                            left_lons.append(lon + nx * r_lon); left_lats.append(lat + ny * r_lat)
                            right_lons.append(lon - nx * r_lon); right_lats.append(lat - ny * r_lat)
                        # Assemble polygon with semicircle cap
                        poly_lons = list(left_lons); poly_lats = list(left_lats)
                        last_lon, last_lat, last_r = _cp[-1]
                        if last_r > 0.001 and last_h > 1e-10:
                            heading = math.atan2(last_dy, last_dx)
                            cos_lat = math.cos(math.radians(last_lat))
                            r_lon_cap = last_r / (111320.0 * cos_lat) if cos_lat > 0.01 else last_r / 111320.0
                            r_lat_cap = last_r / 111320.0
                            start_a = heading + math.pi / 2; end_a = heading - math.pi / 2
                            for k in range(1, 25):
                                a = start_a + (end_a - start_a) * (k / 24)
                                poly_lons.append(last_lon + math.cos(a) * r_lon_cap)
                                poly_lats.append(last_lat + math.sin(a) * r_lat_cap)
                            poly_lons.extend(right_lons[-2::-1]); poly_lats.extend(right_lats[-2::-1])
                        else:
                            poly_lons.extend(right_lons[::-1]); poly_lats.extend(right_lats[::-1])
                        # Project and draw smooth cone fill
                        qpath = QPainterPath()
                        first = True
                        for plon, plat in zip(poly_lons, poly_lats):
                            gx, gy = project_func(plon, plat)
                            if gx is None: continue
                            if first: qpath.moveTo(gx, gy); first = False
                            else: qpath.lineTo(gx, gy)
                        if not qpath.isEmpty():
                            qpath.closeSubpath()
                            item = QGraphicsPathItem(qpath)
                            sw_pen = QPen(QColor(251, 146, 60, 160))
                            sw_pen.setWidth(1.5)
                            sw_pen.setStyle(Qt.DashLine)
                            item.setPen(sw_pen)
                            item.setBrush(QBrush(QColor(251, 146, 60, 25)))
                            item.setZValue(20)
                            scene.addItem(item)
                            self.main_ui._jma_overlay_items.append(item)

                # Warning lines (drawn on top of the smooth cone)
                for swa in sdata.get("storm_warning_areas", []):
                    lines = swa.get("line", [])
                    for line in lines:
                        if len(line) < 2: continue
                        qpts = []
                        for lpt in line:
                            if len(lpt) < 2: continue
                            llon, llat = lpt[1], lpt[0]
                            gx_l, gy_l = project_func(llon, llat)
                            if gx_l is None: break
                            qpts.append(QPointF(gx_l, gy_l))
                        if len(qpts) < 2: continue
                        path = QPainterPath()
                        path.moveTo(qpts[0])
                        for qp in qpts[1:]:
                            path.lineTo(qp)
                        item = QGraphicsPathItem(path)
                        pen = QPen(QColor(251, 146, 60, 160))
                        pen.setWidth(1.5)
                        pen.setStyle(Qt.DashDotLine)
                        item.setPen(pen)
                        item.setZValue(21)
                        scene.addItem(item)
                        self.main_ui._jma_overlay_items.append(item)


    def _resolve_overlay_geometry(self):
        """Resolve the overlay projection parameters for the current display.

        Mirrors the geometry logic used by :meth:`_update_overlays` so the wind
        barb overlay lands in exactly the same coordinate space as the
        grid/coastline/track overlays (works for both the full-disk GEOS view
        and the rectilinear/equirectangular display projections).

        Returns a dict with ``img_w``/``img_h`` (displayed pixmap size),
        ``ow``/``oh`` (overlay grid size), ``extent``, ``res_m``,
        ``transformer``, ``rectilinear`` and ``lon_lat_extent``, or ``None``
        when geometry cannot be resolved.
        """
        transform = self.main_ui.current_geotransform
        crs_proj = self.main_ui.current_crs
        if transform is None or crs_proj is None:
            return None
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return None
        pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
        if not pixmap_item:
            return None
        img_w = pixmap_item.pixmap().width()
        img_h = pixmap_item.pixmap().height()
        if img_w <= 0 or img_h <= 0:
            return None
        try:
            transformer = getattr(self.main_ui, '_ol_transformer', None)
            if transformer is None or getattr(self.main_ui, '_ol_crs', None) is not crs_proj:
                transformer = Transformer.from_crs("EPSG:4326", crs_proj, always_xy=True)
                self.main_ui._ol_transformer = transformer
                self.main_ui._ol_crs = crs_proj
        except Exception:
            return None

        display_proj = getattr(self.main_ui, '_current_display_projection', 'full_disk')
        is_rectilinear = display_proj in ("equirectangular", "plate_carree")
        lon_lat_extent = getattr(self.main_ui, '_display_projection_extent', None)

        def _img_extent():
            return max(abs(transform.c), abs(transform.c + transform.a * img_w),
                       abs(transform.f), abs(transform.f + transform.e * img_h))

        if is_rectilinear:
            overlay_w, overlay_h = img_w, img_h
            overlay_extent = _img_extent()
            effective_res_m = (2 * overlay_extent) / max(img_w, img_h, 1)
        else:
            gq = getattr(self.main_ui, '_get_quality_grid', lambda: None)()
            if gq is not None:
                overlay_w, overlay_h, effective_res_m, overlay_extent = gq
                if getattr(self.main_ui, '_is_hsd_source', False) and (img_w > overlay_w or img_h > overlay_h):
                    img_dim = max(img_w, img_h)
                    overlay_extent = _img_extent()
                    overlay_w, overlay_h = img_w, img_h
                    effective_res_m = (2 * overlay_extent) / img_dim if img_dim > 0 else abs(transform.a)
            else:
                img_dim = max(img_w, img_h)
                overlay_extent = _img_extent()
                overlay_w, overlay_h = img_w, img_h
                effective_res_m = (2 * overlay_extent) / img_dim if img_dim > 0 else abs(transform.a)

        return {
            "img_w": int(img_w),
            "img_h": int(img_h),
            "ow": int(overlay_w),
            "oh": int(overlay_h),
            "extent": float(overlay_extent),
            "res_m": float(effective_res_m),
            "transformer": transformer,
            "rectilinear": bool(is_rectilinear),
            "lon_lat_extent": lon_lat_extent,
            "display_proj": display_proj,
        }

    def _project_wind_batch(self, lons, lats, geom):
        """Vectorized lon/lat -> overlay pixel projection for wind points."""
        lons = np.asarray(lons, dtype=np.float64)
        lats = np.asarray(lats, dtype=np.float64)
        out = np.full_like(lons, np.nan)
        outy = np.full_like(lons, np.nan)
        ow, oh = geom["ow"], geom["oh"]
        if geom["rectilinear"]:
            ext = geom.get("lon_lat_extent")
            if ext is None:
                return out, outy
            lon_min, lat_min, lon_max, lat_max = ext
            if lon_max - lon_min <= 0 or lat_max - lat_min <= 0:
                return out, outy
            lons_x = np.where((lons < lon_min) & (lon_max - lon_min > 300), lons + 360.0, lons)
            col = (lons_x - lon_min) * (ow / (lon_max - lon_min))
            row = (lat_max - lats) * (oh / (lat_max - lat_min))
            valid = (col >= 0) & (col < ow) & (row >= 0) & (row < oh)
            valid &= np.isfinite(col) & np.isfinite(row)
            if np.any(valid):
                out[valid] = col[valid]
                outy[valid] = row[valid]
            return out, outy
        extent = geom["extent"]
        res = geom["res_m"]
        if res <= 0 or extent <= 0:
            return out, outy
        try:
            x_proj, y_proj = geom["transformer"].transform(lons, lats)
        except Exception:
            return out, outy
        valid = ~(np.isinf(x_proj) | np.isinf(y_proj) | np.isnan(x_proj) | np.isnan(y_proj))
        valid &= (np.abs(x_proj) <= extent * 1.01) & (np.abs(y_proj) <= extent * 1.01)
        if np.any(valid):
            out[valid] = (x_proj[valid] + extent) / res
            outy[valid] = (extent - y_proj[valid]) / res
        return out, outy

    def _compute_grid_cells(self, lat_arr, lon_arr, u_arr, v_arr, filtered_idx, geom, img_w, img_h):
        """Bin filtered wind points onto a fixed ~1 km geographic grid.

        Returns the same 6-tuple contract as the barb path so the shared
        *paint* routine just receives the mesh centers plus per-cell mean
        wind direction and speed.  Cells without data are dropped, so the
        arrows hug the swath/gridded coverage instead of filling empty ocean.
        """
        import numpy as np
        lt = np.asarray(lat_arr, dtype=np.float64)[filtered_idx]
        ln = np.asarray(lon_arr, dtype=np.float64)[filtered_idx]
        uu = np.asarray(u_arr, dtype=np.float32)[filtered_idx]
        vv = np.asarray(v_arr, dtype=np.float32)[filtered_idx]
        if len(lt) == 0:
            return None

        lat_min, lat_max = float(np.min(lt)), float(np.max(lt))
        lon_min, lon_max = float(np.min(ln)), float(np.max(ln))
        if lat_max <= lat_min or lon_max <= lon_min:
            return None

        # A grid spacing of 1 km in decimal degrees (longitude scaled by
        # cos(latitude)).  Huge domains are coarsened only as far as needed
        # to keep the texture memory-bounded.
        _KM_PER_DEG = 111.32
        d_lat = 1.0 / _KM_PER_DEG
        mid_rad = np.radians((lat_min + lat_max) * 0.5)
        d_lon = d_lat / max(float(np.cos(mid_rad)), 0.05)

        n_lat = max(2, int(np.ceil((lat_max - lat_min) / d_lat)))
        n_lon = max(2, int(np.ceil((lon_max - lon_min) / d_lon)))
        _MAX_CELLS = 1500000
        total = n_lat * n_lon
        if total > _MAX_CELLS:
            ratio = float(np.sqrt(total / _MAX_CELLS))
            n_lat = max(2, int(n_lat / ratio))
            n_lon = max(2, int(n_lon / ratio))

        lat_edges = np.linspace(lat_min, lat_max, n_lat + 1)
        lon_edges = np.linspace(lon_min, lon_max, n_lon + 1)

        # Assign each point to its mesh cell.
        li = np.clip(np.searchsorted(lat_edges, lt, side='right') - 1, 0, n_lat - 1)
        lo = np.clip(np.searchsorted(lon_edges, ln, side='right') - 1, 0, n_lon - 1)
        cell = li * n_lon + lo

        cnt = np.bincount(cell, minlength=n_lat * n_lon)
        sum_u = np.bincount(cell, weights=uu.astype(np.float64), minlength=n_lat * n_lon)
        sum_v = np.bincount(cell, weights=vv.astype(np.float64), minlength=n_lat * n_lon)

        keep = cnt > 0
        cell_ids = np.flatnonzero(keep)
        if len(cell_ids) == 0:
            return None

        crows = cell_ids // n_lon
        ccols = cell_ids % n_lon
        clat = (lat_edges[crows] + lat_edges[crows + 1]) * 0.5
        clon = (lon_edges[ccols] + lon_edges[ccols + 1]) * 0.5
        cu = (sum_u[cell_ids] / cnt[cell_ids]).astype(np.float32)
        cv = (sum_v[cell_ids] / cnt[cell_ids]).astype(np.float32)
        sp = np.hypot(cu.astype(np.float64), cv.astype(np.float64))
        ang = np.arctan2(cv.astype(np.float64), cu.astype(np.float64))

        px, py = self._project_wind_batch(clon, clat, geom)
        good = ~(np.isnan(px) | np.isnan(py) | (px <= 0) | (px >= float(img_w) - 1) |
                 (py <= 0) | (py >= float(img_h) - 1))
        if not np.any(good):
            return None
        return (px[good], py[good], px[good], py[good], ang[good], sp[good])

    def _pass_selection(self):
        """Current ASCAT pass dropdown selection, or None for 'All passes'."""
        combo = getattr(self.main_ui, "amv_pass_combo", None)
        if combo is None:
            return None
        return combo.currentData()

    def _draw_winds_overlay(self, *_args, **_kwargs):

        scene = self.main_ui.graphics_view.scene()
        if not scene or not scene.items():
            return

        # Wind barbs are drawn ONCE and stay put. Zoom/pan (which flows
        # through _update_overlays_static / update_overlays) must never
        # tear down and rebuild them; callers that load NEW wind data or
        # toggle winds off explicitly reset _winds_persist = False first.
        if getattr(self.main_ui, '_winds_persist', False):
            return

        gridded = bool(self.main_ui.settings.get("gridded_winds", False))

        for lst in (getattr(self.main_ui, '_wind_items', None), getattr(self, '_wind_items', None)):
            for item in list(lst or []):
                try:
                    if item.scene():
                        scene.removeItem(item)
                except Exception:
                    pass
        self.main_ui._wind_items = []
        self._wind_items = []

        if not getattr(self.main_ui, 'current_winds_uv', None) or not self.main_ui.winds_enabled:
            self.main_ui._winds_persist = False
            return

        geom = self._resolve_overlay_geometry()
        if geom is None:
            return
        img_w = geom["img_w"]
        img_h = geom["img_h"]
        ow = geom["ow"]
        oh = geom["oh"]

        _SPEED_COLORS_KT = [
            (0,  QColor("#0000C8")),
            (10, QColor("#0090E0")),
            (20, QColor("#00C800")),
            (30, QColor("#E0E000")),
            (40, QColor("#FF7800")),
            (50, QColor("#E00000")),
        ]

        _SPEED_THRESHOLDS = np.array([t for t, _ in _SPEED_COLORS_KT], dtype=np.float64)

        # Raster cap: the wind overlay is baked into a single QImage at most
        # this large (per side). Bigger overlay spaces (native / full-res) are
        # shrunk by _scale and the item is scaled back up by 1/_scale so the
        # barbs stay glued to the imagery while memory stays bounded even with
        # 1-2M barbs. The typical 2200px preview renders 1:1 (crisp).
        MAX_WIND_RASTER = 4096
        _scale = 1.0
        if max(ow, oh) > MAX_WIND_RASTER:
            _scale = MAX_WIND_RASTER / float(max(ow, oh))
        rw = max(1, int(round(ow * _scale)))
        rh = max(1, int(round(oh * _scale)))

        _geom_sig = (ow, oh, geom["display_proj"],
                     round(geom["extent"], 1), round(geom["res_m"], 3),
                     geom.get("lon_lat_extent"), bool(gridded))
        cache_key = (f"winds_{getattr(self.main_ui, 'current_datetime', '')}_"
                     f"{self.main_ui.settings.get('wind_density', 'Normal')}_"
                     f"sat{self.main_ui.amv_sat_combo.currentData()}_"
                     f"pass{self._pass_selection()}_"
                     f"{self.main_ui.amv_press_combo.currentText()}_{_geom_sig}")
        wind_cache = getattr(self.main_ui, '_wind_proj_cache', None)
        if wind_cache is None:
            wind_cache = {}
            self.main_ui._wind_proj_cache = wind_cache
        self._wind_proj_cache = wind_cache

        def _line_polygon(x0, y0, x1, y1):
            """Interleave segment endpoints into a QPolygonF for painter.drawLines."""
            n = len(x0)
            pts = [None] * (n * 2)
            for i in range(n):
                pts[2 * i] = QPointF(x0[i], y0[i])
                pts[2 * i + 1] = QPointF(x1[i], y1[i])
            return QPolygonF(pts)

        def _draw_grid_arrows(painter, px, py, ang, speed, s):
            """Draw simple wind arrows (shaft + arrowhead) at gridded cells."""
            n = len(px)
            if n == 0:
                return
            kts = speed * 1.94384
            ci = np.searchsorted(_SPEED_THRESHOLDS, kts, side='right') - 1
            ci = np.clip(ci, 0, len(_SPEED_COLORS_KT) - 1)

            if s != 1.0:
                px = px * s
                py = py * s

            ca = np.cos(ang)
            sa = np.sin(ang)

            # Shaft length is ~proportional to speed so stronger winds read as
            # longer arrows, but capped so adjacent ~1 km cells don't overlap.
            _res_px_km = 1000.0 / max(float(geom["res_m"]), 1.0) * s
            max_len = max(6.0 * s, _res_px_km * 0.85)
            shaft = np.clip(kts * 0.22, 5.0, 40.0) * s
            shaft = np.minimum(shaft, max_len)
            tx = px + shaft * ca
            ty = py + shaft * sa
            head = np.clip(shaft * 0.42, 3.5, 16.0) * s
            h_ang = 0.5

            pen = QPen()
            pen.setCapStyle(Qt.RoundCap)
            painter.setRenderHint(QPainter.Antialiasing, False)

            for cidx in np.unique(ci):
                sel = ci == cidx
                gc = QColor(_SPEED_COLORS_KT[int(cidx)][1])
                pen.setWidth(1)
                pen.setColor(gc)
                painter.setPen(pen)
                painter.drawLines(_line_polygon(px[sel], py[sel], tx[sel], ty[sel]))
                painter.drawLines(_line_polygon(
                    np.concatenate([tx[sel], tx[sel]]),
                    np.concatenate([ty[sel], ty[sel]]),
                    np.concatenate([
                        tx[sel] - head[sel] * np.cos(ang[sel] - h_ang),
                        tx[sel] - head[sel] * np.cos(ang[sel] + h_ang)]),
                    np.concatenate([
                        ty[sel] - head[sel] * np.sin(ang[sel] - h_ang),
                        ty[sel] - head[sel] * np.sin(ang[sel] + h_ang)])))

        def _grid_paths_append(paths, px, py, ang, speed):
            """Append crisp vector arrow segments (shaft + head) per speed bin.

            ``paths`` maps a speed-bin index to a QPainterPath; each arrow adds
            a shaft plus a two-segment arrowhead. Tails are long enough to be
            clearly visible at any zoom, which is why gridded winds bypass the
            QImage raster entirely.
            """
            n = len(px)
            if n == 0:
                return
            kts = speed * 1.94384
            ci = np.searchsorted(_SPEED_THRESHOLDS, kts, side='right') - 1
            ci = np.clip(ci, 0, len(_SPEED_COLORS_KT) - 1)

            ca = np.cos(ang)
            sa = np.sin(ang)

            shaft = np.clip(kts * 0.28, 9.0, 46.0)
            tx = px + shaft * ca
            ty = py + shaft * sa
            head = np.clip(shaft * 0.42, 5.0, 14.0)
            h_ang = 0.5

            for cidx in np.unique(ci):
                sel = np.flatnonzero(ci == cidx)
                path = paths.setdefault(int(cidx), QPainterPath())
                for i in range(len(sel)):
                    j = sel[i]
                    path.moveTo(px[j], py[j])
                    path.lineTo(tx[j], ty[j])
                    path.moveTo(tx[j], ty[j])
                    path.lineTo(tx[j] - head[j] * np.cos(ang[j] - h_ang),
                                 ty[j] - head[j] * np.sin(ang[j] - h_ang))
                    path.moveTo(tx[j], ty[j])
                    path.lineTo(tx[j] - head[j] * np.cos(ang[j] + h_ang),
                                 ty[j] - head[j] * np.sin(ang[j] + h_ang))

        def _paint_channel(painter, px, py, rx, ry, ang, speed, s, gridded=False):
            """Rasterize one wind channel into the shared wind QImage.

            In barb (default) mode every point gets a station-model barb.
            In gridded mode the incoming points are the bin centers already
            (~grid side squared arrows) and each becomes a simple wind arrow:
            a shaft pointing in the flow direction plus a small arrowhead,
            keeping the raster light even at Intensive density.
            """
            n = len(px)
            if n == 0:
                return

            if gridded:
                _draw_grid_arrows(painter, px, py, ang, speed, s)
                return

            kts = speed * 1.94384
            ci = np.searchsorted(_SPEED_THRESHOLDS, kts, side='right') - 1
            ci = np.clip(ci, 0, len(_SPEED_COLORS_KT) - 1)

            pennants = np.floor(kts / 50).astype(np.int32)
            rem = kts - pennants * 50
            full_b = np.floor(rem / 10).astype(np.int32)
            half_b = ((rem % 10) >= 5).astype(np.int32)
            pennants = np.clip(pennants, 0, 3)
            full_b = np.clip(full_b, 0, 5)

            if s != 1.0:
                px = px * s
                py = py * s
                rx = rx * s
                ry = ry * s

            ca = np.cos(ang)
            sa = np.sin(ang)
            left = ang + np.pi / 2
            right = ang - np.pi / 2
            cl = np.cos(left)
            sl = np.sin(left)
            cr = np.cos(right)
            sr = np.sin(right)

            FULL_H = 5.0 * s
            HALF_H = 3.0 * s
            PENNANT = 6.0 * s
            SPACE = 3.0 * s
            DOT_W = max(1, int(round(2.0 * s)))

            pen = QPen()
            pen.setCapStyle(Qt.RoundCap)
            painter.setRenderHint(QPainter.Antialiasing, False)

            groups = {}
            for cidx in np.unique(ci):
                sel = ci == cidx
                groups[int(cidx)] = (
                    px[sel], py[sel], rx[sel], ry[sel],
                    pennants[sel], full_b[sel], half_b[sel],
                )

            # Pass 1: staffs, pennants and bars
            pen.setWidth(1)
            for cidx, (gx, gy, grx, gry, gpen, gfull, ghalf) in groups.items():
                gc = QColor(_SPEED_COLORS_KT[cidx][1])
                pen.setColor(gc)
                painter.setPen(pen)
                painter.drawLines(_line_polygon(gx, gy, grx, gry))

                if np.any((gpen > 0) | (gfull > 0) | (ghalf > 0)):
                    sel = ci == cidx
                    gca = ca[sel]
                    gsa = sa[sel]
                    gcl = cl[sel]
                    gsl = sl[sel]
                    gcr = cr[sel]
                    gsr = sr[sel]
                    ln_x0 = []
                    ln_y0 = []
                    ln_x1 = []
                    ln_y1 = []

                    for k in range(3):
                        m2 = gpen > k
                        if not np.any(m2):
                            continue
                        offs = k * SPACE
                        bx = grx[m2] - offs * gca[m2]
                        by = gry[m2] - offs * gsa[m2]
                        tx = bx + PENNANT * gcl[m2]
                        ty = by + PENNANT * gsl[m2]
                        qx = tx + PENNANT * 0.6 * gcr[m2]
                        qy = ty + PENNANT * 0.6 * gsr[m2]
                        ln_x0.append(bx); ln_y0.append(by); ln_x1.append(tx); ln_y1.append(ty)
                        ln_x0.append(tx); ln_y0.append(ty); ln_x1.append(qx); ln_y1.append(qy)

                    for k in range(5):
                        m2 = gfull > k
                        if not np.any(m2):
                            continue
                        offs = (gpen[m2] + k) * SPACE
                        bx = grx[m2] - offs * gca[m2]
                        by = gry[m2] - offs * gsa[m2]
                        tx = bx + FULL_H * gcl[m2]
                        ty = by + FULL_H * gsl[m2]
                        ln_x0.append(bx); ln_y0.append(by); ln_x1.append(tx); ln_y1.append(ty)

                    m2 = ghalf > 0
                    if np.any(m2):
                        offs = (gpen[m2] + gfull[m2]) * SPACE
                        bx = grx[m2] - offs * gca[m2]
                        by = gry[m2] - offs * gsa[m2]
                        tx = bx + HALF_H * gcl[m2]
                        ty = by + HALF_H * gsl[m2]
                        ln_x0.append(bx); ln_y0.append(by); ln_x1.append(tx); ln_y1.append(ty)

                    if ln_x0:
                        painter.drawLines(_line_polygon(
                            np.concatenate(ln_x0), np.concatenate(ln_y0),
                            np.concatenate(ln_x1), np.concatenate(ln_y1)))

            # Pass 2: dots on top of every barb
            pen.setWidth(DOT_W)
            for cidx, (gx, gy, _grx, _gry, _gpen, _gfull, _ghalf) in groups.items():
                pen.setColor(QColor(_SPEED_COLORS_KT[cidx][1]))
                painter.setPen(pen)
                painter.drawPoints(QPolygonF([QPointF(gx[i], gy[i]) for i in range(len(gx))]))

        def _compute_channel(ch, data, gridded=False):
            lat_arr = data["lat"]
            lon_arr = data["lon"]
            u_arr = data["u"]
            v_arr = data["v"]
            qi_arr = data.get("qi")
            press_arr = data.get("press")
            n = len(lat_arr)
            if n == 0:
                return None

            # 1. Quality and Speed Filter First
            mask = np.ones(n, dtype=bool)
            if qi_arr is not None:
                mask &= (qi_arr >= 80)
            
            sp_all = np.sqrt(u_arr.astype(np.float64)**2 + v_arr.astype(np.float64)**2)
            mask &= (sp_all >= 1.5)

            # 2. Pressure Filtering
            press_filter = self.main_ui.amv_press_combo.currentText()
            if press_arr is not None and press_filter != "All":
                if "Surface" in press_filter:
                    mask &= (press_arr >= 850) & (press_arr <= 1013)
                elif "Mid" in press_filter:
                    mask &= (press_arr >= 500) & (press_arr < 850)
                elif "Upper" in press_filter:
                    mask &= (press_arr >= 200) & (press_arr < 500)
                elif "Stratosphere" in press_filter:
                    mask &= (press_arr < 200)

            # 2b. Satellite source filter. The combo distinguishes:
            #   None        -> "All Satellite" (show everything)
            #   "AMV"       -> geostationary AMV only (drop ASCAT fields)
            #   "ASCAT"     -> all ASCAT fields (drop geostationary AMV)
            #   0 / 1       -> a single ASCAT platform (Metop-B / Metop-C);
            #                  fields without per-point 'sat' labels are hidden.
            sat_filter = self.main_ui.amv_sat_combo.currentData()
            if sat_filter is not None:
                if "source" in data:
                    channel_source = data["source"]
                else:
                    channel_source = "ascat" if data.get("sat") is not None else "amv"
                if sat_filter == "AMV":
                    if channel_source != "amv":
                        return None
                elif sat_filter == "ASCAT":
                    if channel_source != "ascat":
                        return None
                else:
                    if channel_source != "ascat":
                        return None
                    sat_arr = data.get("sat")
                    if sat_arr is None:
                        return None
                    mask &= (np.asarray(sat_arr) == sat_filter)

            # 2b2. ASCAT pass filter. The channel 'pass' array holds a 1..N
            # pass number per point (1 = oldest, N = newest/latest) assigned by
            # the swath loaders. Selecting a specific pass shows only that
            # pass; fields without per-point pass labels are hidden (the same
            # rule as the single-platform satellite filter).
            pass_sel = None
            pass_combo = getattr(self.main_ui, "amv_pass_combo", None)
            if pass_combo is not None:
                pass_sel = pass_combo.currentData()
            if pass_sel is not None:
                pass_arr = data.get("pass")
                if pass_arr is None:
                    return None
                pass_arr = np.asarray(pass_arr)
                if pass_arr.shape[0] != n:
                    return None
                mask &= (pass_arr == pass_sel)

            filtered_idx = np.where(mask)[0]
            n_filtered = len(filtered_idx)
            if n_filtered == 0:
                return None

            # 2c. Fixed ~1 km geographic mesh when gridded mode is on.
            if gridded:
                return self._compute_grid_cells(
                    lat_arr, lon_arr, u_arr, v_arr, filtered_idx, geom, img_w, img_h)

            # 3. Apply Density Stride to filtered set
            _target = {"Low": 200, "Medium": 1000, "Normal": 3000, "High": 10000, "Intensive": 1500000}
            target_count = _target.get(self.main_ui.settings.get("wind_density", "Normal"), 3000)
            
            if n_filtered > target_count:
                stride = n_filtered // target_count
                final_idx = filtered_idx[::stride]
            else:
                final_idx = filtered_idx
            
            uu = u_arr[final_idx]
            vv = v_arr[final_idx]
            lt = lat_arr[final_idx]
            ln = lon_arr[final_idx]
            sp = sp_all[final_idx]

            px, py = self._project_wind_batch(ln, lt, geom)
            good = ~(np.isnan(px) | np.isnan(py) | (px <= 0) | (px >= float(img_w) - 1) |
                     (py <= 0) | (py >= float(img_h) - 1))
            
            # 4. Cloud Masking
            if not self.main_ui.settings.get("amv_on_clouds", False) and self.main_ui._ir_kelvin is not None:
                try:
                    # Get temp at projected pixels
                    # px, py are in pixels of the current pixmap.
                    # We need to map them back to the _ir_kelvin array coordinates.
                    # If the pixmap is the same size as _ir_kelvin, it's direct.
                    # But it's safer to handle scaling.
                    ir_h, ir_w = self.main_ui._ir_kelvin.shape
                    # Map px, py (scaled) to ir_w, ir_h (native)
                    ix = (px * ir_w / img_w).astype(int)
                    iy = (py * ir_h / img_h).astype(int)
                    
                    # Ensure within bounds
                    valid_pix = (ix >= 0) & (ix < ir_w) & (iy >= 0) & (iy < ir_h)
                    temp_mask = np.ones(len(px), dtype=bool)
                    temp_mask[~valid_pix] = False
                    
                    # Cold pixels (< 235K) are clouds
                    cloud_mask = self.main_ui._ir_kelvin[iy[valid_pix], ix[valid_pix]] < 235.0
                    temp_mask[valid_pix] &= ~cloud_mask
                    good &= temp_mask
                except Exception as e:
                    self.main_ui.log(f"Cloud mask error: {e}")

            uu, vv = uu[good], vv[good]
            px, py = px[good], py[good]
            sp = sp[good]
            if len(px) == 0:
                return None

            ang = np.arctan2(vv.astype(np.float64), uu.astype(np.float64))
            STAFF_LEN = 10

            rx = (px - STAFF_LEN * np.cos(ang)).astype(np.float64)
            ry = (py - STAFF_LEN * np.sin(ang)).astype(np.float64)
            return (px, py, rx, ry, ang, sp)

        try:
            if gridded:
                # Gridded winds are drawn as real vector QGraphicsPathItems
                # (crisp at any zoom, no QImage scaling blur). Tails are long
                # enough to actually read the wind field.
                if cache_key not in wind_cache:
                    cache_entry = {}
                    for ch, data in self.main_ui.current_winds_uv.items():
                        cfg = {"label": ch}
                        result = _compute_channel(ch, data, True)
                        if result is None:
                            continue
                        cache_entry[ch] = (result, cfg)
                    if cache_entry:
                        wind_cache[cache_key] = cache_entry
                        if len(wind_cache) > 4:
                            for _old_key in list(wind_cache)[:-4]:
                                wind_cache.pop(_old_key, None)
                paths = {}
                for ch, (ch_data, _cfg) in wind_cache[cache_key].items():
                    px, py, _rx, _ry, ang, sp = ch_data
                    _grid_paths_append(paths, px, py, ang, sp)
                for cidx, path in paths.items():
                    item = QGraphicsPathItem(path)
                    pen = QPen(QColor(_SPEED_COLORS_KT[cidx][1]))
                    pen.setCapStyle(Qt.RoundCap)
                    item.setPen(pen)
                    item.setZValue(45)
                    scene.addItem(item)
                    self.main_ui._wind_items.append(item)
                    self._wind_items.append(item)
                # Stick: keep the vector arrows as-is on zoom/pan refreshes.
                self.main_ui._winds_persist = True
                return

            # Everything is rasterized ONCE into a single transparent image
            # (one scene item), no matter how many barbs there are.
            raster = QImage(rw, rh, QImage.Format_ARGB32_Premultiplied)
            raster.fill(Qt.transparent)
            _painter = QPainter(raster)
            _painter.setRenderHint(QPainter.Antialiasing, False)
            try:
                if cache_key in wind_cache:
                    for ch, (ch_data, cfg) in wind_cache[cache_key].items():
                        _paint_channel(_painter, *ch_data, _scale, gridded)
                else:
                    cache_entry = {}
                    for ch, data in self.main_ui.current_winds_uv.items():
                        cfg = {"label": ch}
                        result = _compute_channel(ch, data, gridded)
                        if result is None:
                            continue
                        cache_entry[ch] = (result, cfg)
                        _paint_channel(_painter, *result, _scale, gridded)
                    if cache_entry:
                        wind_cache[cache_key] = cache_entry
                        if len(wind_cache) > 4:
                            for _old_key in list(wind_cache)[:-4]:
                                wind_cache.pop(_old_key, None)
            finally:
                _painter.end()

            if not raster.isNull():
                pix = QPixmap.fromImage(raster)
                item = QGraphicsPixmapItem(pix)
                item.setZValue(45)
                if _scale != 1.0:
                    item.setScale(1.0 / _scale)
                scene.addItem(item)
                self.main_ui._wind_items.append(item)
                self._wind_items.append(item)
                # Stick: keep the raster as-is on later zoom/pan refreshes.
                self.main_ui._winds_persist = True
        except Exception as e:
            import traceback
            self.main_ui.log(f"Winds drawing error: {e}\n{traceback.format_exc()}")

    # ------------------------------------------------------------- microwave
    # Brightness-temperature (TB) passive-microwave overlay. Mirrors
    # _draw_winds_overlay but renders a SCALAR field (ATMS 88.2 GHz TB in
    # Kelvin) as color-binned dots instead of wind barbs.
    _TB_COLORS_K = [
        (150, QColor("#0000C8")),
        (180, QColor("#0090E0")),
        (200, QColor("#00C800")),
        (225, QColor("#E0E000")),
        (250, QColor("#FF7800")),
        (280, QColor("#E00000")),
        (320, QColor("#B000B0")),
    ]
    _TB_THRESHOLDS_K = np.array([t for t, _c in _TB_COLORS_K], dtype=np.float64)

    def _draw_microwave_overlay(self, *_args, **_kwargs):
        scene = self.main_ui.graphics_view.scene()
        if not scene or not scene.items():
            return

        if getattr(self.main_ui, '_mw_persist', False):
            return

        for lst in (getattr(self.main_ui, '_mw_items', None),
                    getattr(self, '_mw_items', None)):
            for item in list(lst or []):
                try:
                    if item.scene():
                        scene.removeItem(item)
                except Exception:
                    pass
        self.main_ui._mw_items = []
        self._mw_items = []

        data = getattr(self.main_ui, 'microwave_tb_data', None)
        if not data or not self.main_ui.microwave_enabled:
            self.main_ui._mw_persist = False
            return

        geom = self._resolve_overlay_geometry()
        if geom is None:
            return
        img_w = geom["img_w"]
        img_h = geom["img_h"]
        ow = geom["ow"]
        oh = geom["oh"]

        import numpy as np
        lat_arr = np.asarray(data.get("lat"), dtype=np.float64)
        lon_arr = np.asarray(data.get("lon"), dtype=np.float64)
        tb_arr = np.asarray(data.get("tb"), dtype=np.float64)
        ok = (np.isfinite(lat_arr) & np.isfinite(lon_arr)
              & np.isfinite(tb_arr))
        lat_arr, lon_arr, tb_arr = lat_arr[ok], lon_arr[ok], tb_arr[ok]
        n = len(lat_arr)
        if n == 0:
            self.main_ui._mw_persist = False
            return

        MAX_MW_RASTER = 4096
        _scale = 1.0
        if max(ow, oh) > MAX_MW_RASTER:
            _scale = MAX_MW_RASTER / float(max(ow, oh))
        rw = max(2, int(round(ow * _scale)))
        rh = max(2, int(round(oh * _scale)))

        try:
            from scipy.ndimage import distance_transform_edt, gaussian_filter
            px, py = self._project_wind_batch(lon_arr, lat_arr, geom)
            good = ~(np.isnan(px) | np.isnan(py)
                     | (px <= 0) | (px >= float(img_w) - 1)
                     | (py <= 0) | (py >= float(img_h) - 1))
            if not np.any(good):
                self.main_ui._mw_persist = False
                return
            px, py, tb = px[good], py[good], tb_arr[good]

            # Rasterize the scattered footprints into a continuous field:
            # bin them into per-pixel cells, then close the gaps between
            # samples with a bounded nearest-neighbour fill so the layer
            # renders as smooth brightness-temperature imagery, not dots.
            col = np.clip(np.round(px * _scale).astype(np.intp), 0, rw - 1)
            row = np.clip(np.round(py * _scale).astype(np.intp), 0, rh - 1)
            flat = row * rw + col
            counts = np.bincount(flat, minlength=rw * rh).reshape(rh, rw)
            sums = np.bincount(flat, weights=tb, minlength=rw * rh).reshape(rh, rw)
            field = np.full((rh, rw), np.nan, dtype=np.float64)
            filled_cells = counts > 0
            field[filled_cells] = sums[filled_cells] / counts[filled_cells]

            missing = np.isnan(field)
            if np.all(missing):
                self.main_ui._mw_persist = False
                return
            if np.any(missing):
                # Adaptive fill radius: a few inter-sample spacings, capped so
                # the fill welds the swath into solid imagery without flooding
                # into empty ocean.
                hspan = max(int(col.max()) - int(col.min()) + 1, 1)
                vspan = max(int(row.max()) - int(row.min()) + 1, 1)
                density = n / float(hspan * vspan)
                spacing = 1.0 / np.sqrt(density) if density > 0 else 1.0
                cap = float(max(2.0, min(200.0, 5.0 * spacing)))
                dist, (iy, ix) = distance_transform_edt(
                    missing, return_distances=True, return_indices=True)
                keep = missing & (dist <= cap)
                if np.any(keep):
                    field[keep] = field[iy[keep], ix[keep]]

            # Light smoothing so the gridded field reads like imagery.
            filled_mask = ~np.isnan(field)
            if np.any(filled_mask):
                lo_b = float(np.nanmin(field))
                hi_b = float(np.nanmax(field))
                blurred = gaussian_filter(
                    np.nan_to_num(field, nan=lo_b), sigma=0.8, mode="nearest")
                blurred = np.clip(blurred, lo_b, hi_b)
                field = np.where(filled_mask, blurred, np.nan)

            # Banded Kelvin color map.
            th = np.array([t for t, _c in self._TB_COLORS_K],
                          dtype=np.float64)
            cidx = np.digitize(field, th, right=True)
            colors = np.array(
                [[c.red(), c.green(), c.blue()]
                 for _t, c in self._TB_COLORS_K], dtype=np.uint8)
            cidx = np.where(filled_mask, cidx, len(colors))
            cidx = np.clip(cidx, 0, len(colors) - 1)
            rgb = colors[cidx]

            alpha = np.where(filled_mask, 255, 0).astype(np.uint8)
            img_arr = np.dstack([rgb[..., 0], rgb[..., 1], rgb[..., 2], alpha])
            img_arr = np.ascontiguousarray(img_arr)
            raster = QImage(img_arr.data, rw, rh, 4 * rw,
                            QImage.Format_ARGB32)
            raster = raster.copy()

            if not raster.isNull():
                pix = QPixmap.fromImage(raster)
                item = QGraphicsPixmapItem(pix)
                item.setZValue(46)
                # Georeference the swath raster to the base image item the way
                # Himawari/GOES/MTG/GK2A imagery is placed: reuse the base
                # pixmap's scene position and scale so the TB field stays glued
                # to the map in every display projection (full-disk, rectilinear,
                # native / SATAID). The overlay grid [0, ow]x[0, oh] maps onto
                # the base image's [0, img_w]x[0, img_h] pixel space, so the
                # raster's scene rect must land on the base image's rect
                # (bpos, sized img_w x img_h at the base item's scale). A
                # per-axis transform keeps the swath aspect-correct even when
                # the overlay grid and imagery sizes differ.
                base_item = next((i for i in reversed(scene.items())
                                  if isinstance(i, QGraphicsPixmapItem)
                                  and i is not item), None)
                if base_item is not None:
                    bpos = base_item.pos()
                    bscale = base_item.scale() or 1.0
                    xf = bscale * (float(img_w) / max(rw, 1))
                    yf = bscale * (float(img_h) / max(rh, 1))
                    item.setPos(bpos)
                    if abs(xf - yf) < 1e-9:
                        item.setScale(xf)
                    else:
                        item.setTransform(
                            QTransform(xf, 0.0, 0.0, 0.0, yf, 0.0, 0.0, 0.0, 1.0))
                elif _scale != 1.0:
                    item.setScale(1.0 / _scale)
                scene.addItem(item)
                self.main_ui._mw_items.append(item)
                self._mw_items.append(item)
                self.main_ui._mw_persist = True
        except Exception as e:
            import traceback
            self.main_ui.log(f"Microwave overlay drawing error: {e}\n{traceback.format_exc()}")

    def toggle_microwave(self, checked: bool):
        import time as _t
        self.main_ui._click_log = ('microwave', _t.perf_counter())
        self.main_ui.log(f"Clicked - microwave TB (checked={checked})")
        self.main_ui.microwave_enabled = bool(checked)
        if checked and getattr(self.main_ui, 'microwave_tb_data', None) is None:
            helper = getattr(self.main_ui, "microwave_controller", None)
            if helper is not None and hasattr(helper, "reload_cached_atms_into_view"):
                try:
                    helper.reload_cached_atms_into_view()
                except Exception as e:
                    self.main_ui.log(f"Microwave reload from cache failed: {e}")
        self.main_ui._mw_persist = False
        self.main_ui._draw_microwave_overlay()

    def toggle_mimic(self, checked: bool):
        import time as _t
        self.main_ui._click_log = ('microwave', _t.perf_counter())
        self.main_ui.log(f"Clicked - MIMIC-TC2 89 GHz (checked={checked})")
        if checked:
            helper = getattr(self.main_ui, "microwave_controller", None)
            if helper is not None:
                # Always refresh from the latest MIMIC-TC2 field on enable.
                try:
                    helper.download_mimic()
                    return
                except Exception as e:
                    self.main_ui.log(f"MIMIC-TC2 fetch failed: {e}")
            try:
                helper = getattr(self.main_ui, "microwave_controller", None)
                if helper is not None and hasattr(helper,
                                                  "reload_cached_mimic_into_view"):
                    helper.reload_cached_mimic_into_view()
            except Exception as e:
                self.main_ui.log(f"MIMIC-TC2 reload from cache failed: {e}")
        else:
            self.main_ui.microwave_enabled = False
            self.main_ui._mw_persist = False
            self.main_ui._draw_microwave_overlay()

    def toggle_viirs(self, checked: bool):
        import time as _t
        self.main_ui._click_log = ('microwave', _t.perf_counter())
        self.main_ui.log(f"Clicked - VIIRS I5 11.45 um (checked={checked})")
        if checked:
            helper = getattr(self.main_ui, "microwave_controller", None)
            if helper is not None:
                try:
                    helper.download_viirs()
                    return
                except Exception as e:
                    self.main_ui.log(f"VIIRS I5 fetch failed: {e}")
            try:
                helper = getattr(self.main_ui, "microwave_controller", None)
                if helper is not None and hasattr(
                        helper, "reload_cached_viirs_into_view"):
                    helper.reload_cached_viirs_into_view()
            except Exception as e:
                self.main_ui.log(f"VIIRS I5 reload from cache failed: {e}")
        else:
            self.main_ui.microwave_enabled = False
            self.main_ui._mw_persist = False
            self.main_ui._draw_microwave_overlay()

    def toggle_amsr2_raw(self, checked: bool):
        import time as _t
        self.main_ui._click_log = ('microwave', _t.perf_counter())
        self.main_ui.log(f"Clicked - AMSR2 L1B 89 GHz (checked={checked})")
        if checked:
            helper = getattr(self.main_ui, "microwave_controller", None)
            if helper is not None:
                try:
                    helper.download_amsr2_raw()
                    return
                except Exception as e:
                    self.main_ui.log(f"AMSR2 L1B fetch failed: {e}")
            try:
                helper = getattr(self.main_ui, "microwave_controller", None)
                if helper is not None and hasattr(
                        helper, "reload_cached_amsr2raw_into_view"):
                    helper.reload_cached_amsr2raw_into_view()
            except Exception as e:
                self.main_ui.log(f"AMSR2 L1B reload from cache failed: {e}")
        else:
            self.main_ui.microwave_enabled = False
            self.main_ui._mw_persist = False
            self.main_ui._draw_microwave_overlay()

    def clear_microwave_overlay(self):
        """Remove the microwave TB overlay from the scene entirely."""
        scene = self.main_ui.graphics_view.scene()
        if scene is None:
            return
        for lst in (getattr(self.main_ui, '_mw_items', None),
                    getattr(self, '_mw_items', None)):
            for item in list(lst or []):
                try:
                    if item.scene():
                        scene.removeItem(item)
                except Exception:
                    pass
        self.main_ui._mw_items = []
        self._mw_items = []
        self.main_ui._mw_persist = False

    def _start_overlay_precache(self, nc_path):
        if hasattr(self.main_ui, 'overlay_precache_worker') and self.main_ui.overlay_precache_worker:
            try:
                self.main_ui.overlay_precache_worker.cancel()
            except Exception:
                pass
            self.main_ui.overlay_precache_worker = None
        if hasattr(self.main_ui, 'overlay_precache_thread') and self.main_ui.overlay_precache_thread:
            try:
                self.main_ui.overlay_precache_thread.quit()
                self.main_ui.overlay_precache_thread.wait(1000)
            except Exception:
                pass
            self.main_ui.overlay_precache_thread = None
        if self.main_ui.settings.get("overlay_mode", "cached") == "realtime":
            return
        try:
            from src.core.helpers import BORDERS_SHP, COASTLINE_SHP
        except Exception:
            return
        try:
            crs = self.main_ui.current_crs
            transform = self.main_ui.current_geotransform
            if crs is None or transform is None:
                return
            try:
                proj4 = crs.to_proj4() if hasattr(crs, 'to_proj4') else None
            except Exception:
                proj4 = None
            if not proj4:
                try:
                    proj4 = f"+proj={crs.to_dict().get('proj', 'geos')}"
                except Exception:
                    return
            try:
                transform_tuple = tuple(transform)[:6] if transform else None
            except Exception:
                transform_tuple = None
            if transform_tuple is None:
                return
            if self.main_ui._is_hsd_source:
                native_res_m = abs(transform.a) if hasattr(transform, 'a') else 2000
                disk_half = self.main_ui._compute_disk_half_extent()
                native_dim = max(1, int(round(2 * disk_half / native_res_m)))
                dec_w = native_dim
                dec_h = native_dim
            else:
                dec_w = max(1, int(self.main_ui.preview_max_px))
                dec_h = max(1, int(self.main_ui.preview_max_px))
            self.main_ui._grid_precache_w = dec_w
            self.main_ui._grid_precache_h = dec_h
            coast_shp = self._coast_shapefile() if self._coast_shapefile().exists() else None
            grid_spacing = float(self.main_ui.settings.get("grid_spacing_deg", 10))
            sub_step_tenths = float(self.main_ui.settings.get('grid_sub_step_tenths', 5))
            sub_step = max(0.1, sub_step_tenths * 0.1)
            coast_res = self.main_ui.settings.get("coastline_resolution", "Full")
            coast_stride_map = {"Low": 8, "Medium": 4, "High": 2, "Full": 1}
            coast_stride = coast_stride_map.get(coast_res, 1)
            worker = OverlayPrecacheWorker(
                proj4, transform_tuple, dec_w, dec_h,
                grid_spacing_deg=grid_spacing, sub_step=sub_step,
                coast_shp_path=coast_shp, coast_stride=coast_stride,
            )
            self.main_ui.log(f"Overlay precache: coast resolution={coast_res} (stride={coast_stride})")
            thread = QThread()
            worker.moveToThread(thread)
            worker.grid_ready.connect(self.main_ui._on_precached_grid)
            worker.coast_ready.connect(self.main_ui._on_precached_coast)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self.main_ui.overlay_precache_worker = worker
            self.main_ui.overlay_precache_thread = thread
            self.main_ui._overlay_precache_state = {'grid': '....', 'coast': '....'}
            self.main_ui._update_loading_dialog_progress()
            thread.start()
            self.main_ui.log("Overlay precache started (grids + coastlines).")
        except Exception as e:
            self.main_ui.log(f"Overlay precache start skipped: {e}")


    def _on_precached_grid(self, key, paths):
        try:
            pw = getattr(self, '_grid_precache_w', self.main_ui.preview_max_px)
            ph = getattr(self, '_grid_precache_h', self.main_ui.preview_max_px)
            cache_key = (
                f"grid_{pw}_{ph}_"
                f"{self.main_ui.settings.get('grid_spacing_deg', 10)}_"
                f"{self.main_ui.settings.get('grid_sub_step_tenths', 5) * 0.1:.1f}_"
                f"{self.main_ui.settings.get('grid_color')}_{self.main_ui.settings.get('grid_line_width')}_"
                f"{self.main_ui.settings.get('grid_opacity')}_{self.main_ui.settings.get('grid_pattern', 'solid')}"
            )
            if not hasattr(self, '_projected_grid_cache') or self.main_ui._projected_grid_cache is None:
                self.main_ui._projected_grid_cache = {}
            self.main_ui._projected_grid_cache[cache_key] = paths
            self.main_ui.log(f"Grid precached: {len(paths)} segments ({cache_key[:40]}...)")
            self.main_ui._overlay_precache_state['grid'] = 'OK'

            self.main_ui._build_and_store_grid_item(paths)
            self.main_ui.log(f"Grid pre-rendered (always -- ready for instant toggle)")
            self.main_ui._update_loading_dialog_progress()
        except Exception as e:
            self.main_ui.log(f"Grid precache store error: {e}")
            self.main_ui._overlay_precache_state['grid'] = 'FAIL'


    def _build_and_store_grid_item(self, paths):
        try:
            combined_grid = QPainterPath()
            for entry in paths:
                if isinstance(entry, QPainterPath):
                    combined_grid.addPath(entry)
                    continue
                runs = []
                cur = []
                for pt in entry:
                    if pt is None:
                        if cur:
                            runs.append(np.asarray(cur, dtype=np.float64))
                            cur = []
                    else:
                        cur.append((pt[0], pt[1]))
                if cur:
                    runs.append(np.asarray(cur, dtype=np.float64))
                self._append_pixel_path(combined_grid, runs, min_step_px=1.0)
            if combined_grid.isEmpty():
                self.main_ui.log(f"Grid pre-render: combined path is empty")
                return
            gc = QColor(self.main_ui.settings.get("grid_color", "#C8C8C8"))
            gc.setAlpha(self.main_ui.settings.get("grid_opacity", 160))
            pen = QPen(gc)
            pen.setWidth(self.main_ui.settings.get("grid_line_width", 1))
            pattern = self.main_ui.settings.get("grid_pattern", "solid")
            style_map = {
                "solid": Qt.SolidLine, "dotted": Qt.DotLine,
                "dashed": Qt.DashLine, "dashdot": Qt.DashDotLine,
                "crosshatch": Qt.DashDotDotLine,
            }
            pen.setStyle(style_map.get(pattern, Qt.SolidLine))
            item = QGraphicsPathItem(combined_grid)
            item.setPen(pen)
            item.setZValue(20)
            self.main_ui._grid_overlay_item = item
            self.main_ui.log(f"Grid item built: {combined_grid.elementCount()} path elements")
            if self.main_ui.grid_enabled:
                scene = self.main_ui.graphics_view.scene()
                if scene and item not in scene.items():
                    scene.addItem(item)
                    if not hasattr(self, 'grid_overlay_items') or self.main_ui.grid_overlay_items is None:
                        self.main_ui.grid_overlay_items = []
                    self.main_ui.grid_overlay_items.append(item)
                    self.main_ui.log(f"Grid overlay attached to scene (instant)")
        except Exception as e:
            self.main_ui.log(f"Grid item build error: {e}")


    def _on_precached_coast(self, key, paths):
        try:
            pw = getattr(self, '_grid_precache_w', self.main_ui.preview_max_px)
            ph = getattr(self, '_grid_precache_h', self.main_ui.preview_max_px)
            cache_key = (
                f"coast_{pw}_{ph}_"
                f"{self.main_ui.settings.get('coast_color')}_"
                f"{self.main_ui.settings.get('coast_line_width')}_{self.main_ui.settings.get('coast_opacity')}_"
                f"{self.main_ui.settings.get('coast_pattern', 'solid')}_"
                f"{self.main_ui.settings.get('coastline_resolution', 'Full')}"
            )
            if not hasattr(self, '_projected_coast_cache') or self.main_ui._projected_coast_cache is None:
                self.main_ui._projected_coast_cache = {}
            self.main_ui._projected_coast_cache[cache_key] = paths
            self.main_ui.log(f"Coastline precached: {len(paths)} segments")
            self.main_ui._overlay_precache_state['coast'] = 'OK'

            self.main_ui._build_and_store_coast_item(paths)
            self.main_ui.log(f"Coastline pre-rendered (always -- ready for instant toggle)")
            self.main_ui._update_loading_dialog_progress()
        except Exception as e:
            self.main_ui.log(f"Coastline precache store error: {e}")
            self.main_ui._overlay_precache_state['coast'] = 'FAIL'


    def _build_and_store_coast_item(self, paths):
        try:
            combined_coast = QPainterPath()
            for entry in paths:
                if isinstance(entry, QPainterPath):
                    combined_coast.addPath(entry)
                    continue
                runs = []
                cur = []
                for pt in entry:
                    if pt is None:
                        if cur:
                            runs.append(np.asarray(cur, dtype=np.float64))
                            cur = []
                    else:
                        cur.append((pt[0], pt[1]))
                if cur:
                    runs.append(np.asarray(cur, dtype=np.float64))
                self._append_pixel_path(combined_coast, runs, min_step_px=1.0)
            if combined_coast.isEmpty():
                self.main_ui.log(f"Coastline pre-render: combined path is empty")
                return
            cc = QColor(self.main_ui.settings.get("coast_color", "#FFFFFF"))
            cc.setAlpha(self.main_ui.settings.get("coast_opacity", 200))
            pen = QPen(cc)
            pen.setWidth(self.main_ui.settings.get("coast_line_width", 2))
            style_map = {
                "solid": Qt.SolidLine, "dotted": Qt.DotLine,
                "dashed": Qt.DashLine, "dashdot": Qt.DashDotLine,
                "crosshatch": Qt.DashDotDotLine,
            }
            pen.setStyle(style_map.get(self.main_ui.settings.get("coast_pattern", "solid"), Qt.SolidLine))
            item = QGraphicsPathItem(combined_coast)
            item.setPen(pen)
            item.setZValue(20)
            self.main_ui._coast_overlay_item = item
            self.main_ui.log(f"Coastline item built: {combined_coast.elementCount()} path elements")
            if self.main_ui.coast_enabled:
                scene = self.main_ui.graphics_view.scene()
                if scene and item not in scene.items():
                    scene.addItem(item)
                    if not hasattr(self, 'coast_overlay_items') or self.main_ui.coast_overlay_items is None:
                        self.main_ui.coast_overlay_items = []
                    self.main_ui.coast_overlay_items.append(item)
                    self.main_ui.log(f"Coastline overlay attached to scene (instant)")
        except Exception as e:
            self.main_ui.log(f"Coastline item build error: {e}")


    def toggle_grid(self, checked):

        """Toggle coordinate grid overlay.

        Shows or hides the lat/lon grid overlay. Grid spacing
        adapts to zoom level for optimal readability.

        Side Effects:
            - Updates grid checkbox state
            - Refreshes viewport overlays
        """
        import time as _t
        self.main_ui._click_log = ('grid', _t.perf_counter())
        self.main_ui.log(f"Clicked - grid (checked={checked})")
        self.main_ui.grid_enabled = checked
        self.main_ui._update_grid_coast_overlay()




    def toggle_coastlines(self, checked):

        """Toggle coastline overlay.

        Shows or hides coastline and political boundary overlays.
        Uses cached cartographic data for performance.

        Side Effects:
            - Updates coastlines checkbox state
            - Refreshes viewport overlays
        """
        import time as _t
        self.main_ui._click_log = ('coast', _t.perf_counter())
        self.main_ui.log(f"Clicked - coastlines (checked={checked})")
        self.main_ui.coast_enabled = checked
        self.main_ui._update_grid_coast_overlay()


    def _on_coast_region_changed(self, region):
        if not self.main_ui._is_beta_viewport():
            return
        self.main_ui.log(f"Coastline region changed to: {region}")
        self.main_ui.settings.set("coast_region", region)
        if self.main_ui.coast_enabled:
            self.main_ui._update_grid_coast_overlay()


    def toggle_pro_contours(self, checked):
        self.main_ui.settings.set("pro_contours", checked)
        self.main_ui._contour_active = checked
        if checked:
            self.main_ui.log("Contour mode: on — Ctrl+Left Click to contour, Ctrl+Space to clear")
            self.main_ui.status_bar.showMessage("Contour: Ctrl+Click to draw, Ctrl+Space to clear")
        else:
            self.main_ui.clear_contour_overlay()
            self.main_ui.log("Contour overlay cleared")
            self.main_ui.status_bar.showMessage("Contour cleared")


    def toggle_winds(self, checked: bool):
        import time as _t
        self.main_ui._click_log = ('winds', _t.perf_counter())
        self.main_ui.log(f"Clicked - winds (checked={checked})")
        self.main_ui.winds_enabled = bool(checked)
        if checked and self.main_ui.current_winds_uv is None:
            # Prefer re-publishing the last ASCAT field (swath or gridded
            # product) before falling back to embedded AMV from the NC file.
            helper = getattr(self.main_ui, "ascat_controller", None)
            reloaded = False
            if helper is not None and hasattr(helper, "reload_cached_into_view"):
                try:
                    reloaded = bool(helper.reload_cached_into_view())
                except Exception as e:
                    self.main_ui.log(f"Wind reload from ASCAT cache failed: {e}")
            if not reloaded:
                self.main_ui.load_winds_data()
        self.main_ui._winds_persist = False
        self.main_ui._draw_winds_overlay()
        if checked and self.main_ui.current_winds_uv is not None:
            self._warn_old_ascat_cache()

    @staticmethod
    def _parse_ascat_imagery_dt(value):
        """Parse main_ui.current_datetime (e.g. '2026_08_15_0200') to UTC."""
        if not value:
            return None
        import re as _re
        s = str(value)
        m = _re.fullmatch(r"(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})", s)
        if m:
            return datetime(*[int(g) for g in m.groups()], tzinfo=timezone.utc)
        try:
            return datetime.strptime(s, "%Y_%m_%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def _warn_old_ascat_cache(self):
        """Warn when enabling winds against imagery much newer than the cache."""
        data_dt = getattr(self.main_ui, "_ascat_wind_dt", None)
        if data_dt is None:
            return
        if getattr(data_dt, "tzinfo", None) is None:
            try:
                data_dt = data_dt.replace(tzinfo=timezone.utc)
            except Exception:
                return
        cur_dt = self._parse_ascat_imagery_dt(
            getattr(self.main_ui, "current_datetime", None))
        if cur_dt is None:
            return
        try:
            delta_h = (cur_dt - data_dt).total_seconds() / 3600.0
        except Exception:
            return
        if delta_h <= 0.5:
            return
        if delta_h >= 24:
            label = (f"{int(delta_h // 24)} day(s) and "
                     f"{int(delta_h % 24)} hour(s) old")
        else:
            label = f"{int(round(delta_h))} hour(s) old"

        from PySide6.QtWidgets import QMessageBox
        box = QMessageBox(self.main_ui)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Old ASCAT cache detected")
        box.setTextFormat(Qt.RichText)
        box.setText(
            f"<b>The loaded ASCAT winds are {label}</b> compared to the "
            f"imagery you're viewing ({cur_dt:%Y-%m-%d %H:%MZ}).<br><br>"
            f"ASCAT data time: <b>{data_dt:%Y-%m-%d %H:%MZ}</b>.<br>"
            "Fresh ASCAT passes are usually published within a few hours.<br><br>"
            "Refresh via <b>System → ASCAT Data &amp; Metadata…</b> using the "
            "<b>KNMI FTP</b> or <b>NASA PO.DAAC</b> button, or clear the old "
            "files with <b>Remove ASCAT Cache</b> in that window."
        )
        box.addButton("Got it", QMessageBox.AcceptRole)
        box.exec()


    def _draw_aor_overlays(self, project_func, scene, img_w, img_h):

        if not hasattr(self.main_ui, 'aor_overlay_items'):
            self.main_ui.aor_overlay_items = []
        try:

            aors = []
            if getattr(self.main_ui, 'aor_par_cb', None) and self.main_ui.aor_par_cb.isChecked():

                par_pts = [
                    (115.0, 5.0),
                    (115.0, 15.0),
                    (120.0, 21.0),
                    (120.0, 25.0),
                    (135.0, 25.0),
                    (135.0, 5.0),
                ]
                aors.append( ("PAR", par_pts, self.main_ui.settings.get("par_color", "#00FF9F")) )

            if getattr(self.main_ui, 'aor_jma_cb', None) and self.main_ui.aor_jma_cb.isChecked():

                jma_pts = [
                    (100.0, 0.0),
                    (100.0, 20.0),
                    (100.0, 40.0),
                    (100.0, 60.0),
                    (179.99, 60.0),
                    (179.99, 40.0),
                    (179.99, 20.0),
                    (179.99, 0.0),
                ]
                aors.append( ("JMA AoR", jma_pts, self.main_ui.settings.get("jma_color", "#FFAA00")) )

            if getattr(self.main_ui, 'aor_tcad_cb', None) and self.main_ui.aor_tcad_cb.isChecked():
                tcad_pts = [
                    (114.0, 4.0),
                    (114.0, 27.0),
                    (145.0, 27.0),
                    (145.0, 4.0),
                ]
                aors.append( ("TCAD", tcad_pts, self.main_ui.settings.get("tcad_color", "#FF6B6B")) )

            if getattr(self.main_ui, 'aor_tcid_cb', None) and self.main_ui.aor_tcid_cb.isChecked():
                tcid_pts = [
                    (110.0, 0.0),
                    (110.0, 27.0),
                    (155.0, 27.0),
                    (155.0, 0.0),
                ]
                aors.append( ("TCID", tcid_pts, self.main_ui.settings.get("tcid_color", "#4ECDC4")) )

            if getattr(self.main_ui, 'aor_fir_cb', None) and self.main_ui.aor_fir_cb.isChecked():
                fir_pts = [
                    (117.3, 21.0),
                    (130.0, 21.0),
                    (130.0, 7.0),
                    (120.0, 4.0),
                    (117.3, 7.3),
                ]
                aors.append( ("Manila FIR", fir_pts, self.main_ui.settings.get("fir_color", "#FFE66D")) )
            if getattr(self.main_ui, 'aor_custom_cb', None) and self.main_ui.aor_custom_cb.isChecked():

                custom_color = self.main_ui.settings.get("custom_aor_color", "#00E5FF")
                if hasattr(self.main_ui, 'current_custom_aor_points') and len(self.main_ui.current_custom_aor_points) >= 2:
                    aors.append( ("Custom (drawing)", self.main_ui.current_custom_aor_points[:], custom_color) )

            if getattr(self.main_ui, 'tracks', None):
                for t in self.main_ui.tracks:
                    if t.get("type") == "Custom AoR" and t.get("points"):
                        pts = []
                        for p in t["points"]:
                            if p.get("lon") is not None and p.get("lat") is not None:
                                    pts.append((p["lon"], p["lat"]))
                            if len(pts) >= 3:
                                aors.append( (t.get("name", "Custom AoR"), pts, custom_color) )
            for name, pts, color in aors:

                if name != "PAR":
                    pts = _densify_polygon(pts, steps=10)

                sub_paths = []
                current_seg = []
                prev_nlon = None
                first_point = None
                for lon, lat in pts:
                    nlon = normalize_lon(lon)
                    gx, gy = project_func(lon, lat)
                    if gx is None or gy is None:
                        if current_seg:
                            sub_paths.append(current_seg)
                            current_seg = []
                        prev_nlon = None
                        continue
                    if prev_nlon is not None and abs(nlon - prev_nlon) > 180.0:

                        if current_seg:
                            sub_paths.append(current_seg)
                            current_seg = []
                    current_seg.append(QPointF(gx, gy))
                    if first_point is None:
                        first_point = QPointF(gx, gy)
                    prev_nlon = nlon
                if current_seg:
                    sub_paths.append(current_seg)

                for seg_idx, seg in enumerate(sub_paths):
                    if len(seg) < 2:
                        continue
                    gpath = QPainterPath()
                    gpath.moveTo(seg[0])
                    for pt in seg[1:]:
                        gpath.lineTo(pt)

                    is_custom = name.startswith("Custom")
                    should_close = (len(sub_paths) == 1 and first_point is not None) or is_custom
                    if should_close and first_point is not None:
                        gpath.lineTo(first_point)
                    item = QGraphicsPathItem(gpath)
                    pen = QPen(QColor(color))

                    _aor_style_map = {
                        "solid": Qt.SolidLine, "dotted": Qt.DotLine,
                        "dashed": Qt.DashLine, "dashdot": Qt.DashDotLine,
                        "crosshatch": Qt.DashDotDotLine,
                    }
                    aor_pattern = self.main_ui.settings.get("aor_pattern", "dashed")

                    if name == "Custom (drawing)":
                        pen.setWidth(2.5)
                        pen.setStyle(Qt.SolidLine)

                        c = QColor(custom_color)
                        c.setAlpha(35)
                        brush = QBrush(c)
                        item.setBrush(brush)
                    else:
                        pen.setWidth(self.main_ui.settings.get("grid_line_width", 1) + 0.2)
                        pen.setStyle(_aor_style_map.get(aor_pattern, Qt.DashLine))
                    item.setPen(pen)
                    item.setZValue(25)
                    scene.addItem(item)
                    self.main_ui.aor_overlay_items.append(item)

                    if seg_idx == 0 and first_point:
                        lbl = QGraphicsTextItem(name)
                        lbl.setDefaultTextColor(QColor(color))
                        lbl.setFont(QFont("Segoe UI", 8, QFont.Bold))
                        lbl.setPos(first_point.x() + 4, first_point.y() + 4)
                        lbl.setZValue(26)
                        scene.addItem(lbl)
                        self.main_ui.aor_overlay_items.append(lbl)
        except Exception as e:
            self.main_ui.log(f"AoR draw error: {e}")

    @staticmethod

    def _category_to_sym(cat, intensity):
        _MAP = {
            "LPA": "lpa", "LOW": "lpa", "TD": "td", "TS": "ts", "STS": "sts", "TY": "ty", "STY": "sty",
            "STD": "td", "SSN": "ssn", "SSS": "sss",
            "Super Typhoon": "sty", "Typhoon": "ty", "Severe Tropical Storm": "sts", "Tropical Storm": "ts",
            "Tropical Depression": "td", "Invest": "lpa", "Disturbance": "lpa",
            "Hurricane": "Hu", "Major Hurricane": "Hu",
        }
        if cat in ("Hurricane", "Major Hurricane") and intensity is not None:
            if intensity >= 130: return "Hu"
            if intensity >= 64: return "Hu"
        return _MAP.get(cat)

    @staticmethod
    def _category_to_pag_sym(cat, intensity):
        _MAP = {
            "LPA": "paglpa", "LOW": "paglpa", "TD": "pagtd", "TS": "pagts", "STS": "pagsts",
            "TY": "pagty", "STY": "pagsty",
            "STD": "pagtd", "SSN": "pagtd", "SSS": "pagts",
            "Tropical Depression": "pagtd",
            "Tropical Storm": "pagts",
            "Severe Tropical Storm": "pagsts",
            "Typhoon": "pagty",
            "Super Typhoon": "pagsty",
            "Hurricane": "pagty", "Major Hurricane": "pagsty",
        }
        if cat in ("Hurricane", "Major Hurricane") and intensity is not None:
            if intensity >= 137: return "pagcat5"
            if intensity >= 113: return "pagcat4"
            if intensity >= 96: return "pagcat3"
            if intensity >= 83: return "pagcat2"
            if intensity >= 64: return "pagcat1"
        return _MAP.get(cat)

    @staticmethod
    def _compute_label_offsets_new(track_points, algorithm="auto"):
        if not _HAS_NEW_ALGOS or not track_points:
            return None
        try:
            result = _new_run_pipeline(track_points, strategy=algorithm)
            flat = []
            for (ox, oy), ha in result:
                flat.append((ox, oy, ha))
            return flat if flat else None
        except Exception:
            return None

    @staticmethod

    def _compute_label_offsets(projected_points, labeling_method="polar", normal_dist=30, along_dist=8, stair_step=16):
        n = len(projected_points)
        if n < 2:
            return None

        dirs = []
        for i in range(n):
            if i == 0:
                dx = projected_points[1].x() - projected_points[0].x()
                dy = projected_points[1].y() - projected_points[0].y()
            elif i == n - 1:
                dx = projected_points[-1].x() - projected_points[-2].x()
                dy = projected_points[-1].y() - projected_points[-2].y()
            else:
                dx = (projected_points[i+1].x() - projected_points[i-1].x()) / 2.0
                dy = (projected_points[i+1].y() - projected_points[i-1].y()) / 2.0
            length = math.hypot(dx, dy)
            if length > 1e-10:
                dx /= length
                dy /= length
            else:
                dx, dy = 1.0, 0.0
            nx, ny = -dy, dx
            dirs.append((dx, dy, nx, ny))

        point_dists = []
        for i in range(n - 1):
            d = math.hypot(
                projected_points[i+1].x() - projected_points[i].x(),
                projected_points[i+1].y() - projected_points[i].y()
            )
            point_dists.append(d)

        offsets = []
        for i in range(n):
            if labeling_method == "polar":
                cross = 0
                if i == 0 and n >= 3:
                    cross = dirs[0][0] * dirs[1][1] - dirs[0][1] * dirs[1][0]
                elif i == n - 1 and n >= 3:
                    cross = dirs[n-2][0] * dirs[n-1][1] - dirs[n-2][1] * dirs[n-1][0]
                elif 0 < i < n - 1:
                    cross = dirs[i-1][0] * dirs[i+1][1] - dirs[i-1][1] * dirs[i+1][0]
                if abs(cross) > 0.03:
                    side_right = cross > 0
                else:
                    side_right = i % 2 == 0
                if side_right:
                    offsets.append((-24, -14 - i * stair_step, 'right'))
                else:
                    offsets.append((24, 14 - i * stair_step, 'left'))
            else:
                dx, dy, nx, ny = dirs[i]
                local_nd = normal_dist
                local_ss = stair_step
                if i > 0 and i < n - 1:
                    avg_dist = (point_dists[i-1] + point_dists[i]) / 2.0
                    cross = dirs[i-1][0] * dirs[i+1][1] - dirs[i-1][1] * dirs[i+1][0]
                    if avg_dist < 100 and abs(cross) > 0.03:
                        factor = 1.0 + max(1.0, (100 - avg_dist) / 30.0) * 0.3
                        local_nd = normal_dist * factor
                        local_ss = stair_step * factor
                ox = nx * local_nd - dx * along_dist
                oy = ny * local_nd - dy * along_dist - i * local_ss
                ha = 'right' if ox < 0 else 'left'
                offsets.append((ox, oy, ha))

        if labeling_method == "smart_bezier" and n >= 2:
            curvatures = []
            for i in range(n):
                if i == 0 and n >= 3:
                    c = dirs[0][0] * dirs[1][1] - dirs[0][1] * dirs[1][0]
                elif i == n - 1 and n >= 3:
                    c = dirs[n-2][0] * dirs[n-1][1] - dirs[n-2][1] * dirs[n-1][0]
                elif 0 < i < n - 1:
                    c = dirs[i-1][0] * dirs[i+1][1] - dirs[i-1][1] * dirs[i+1][0]
                else:
                    c = 0
                curvatures.append(c)
            max_curve = max(abs(c) for c in curvatures)
            cramped = False
            for i in range(n - 1):
                dx1 = offsets[i][0] - offsets[i+1][0]
                dy1 = offsets[i][1] - offsets[i+1][1]
                if abs(dx1) < 50 and abs(dy1) < 20:
                    cramped = True
                    break
            if cramped and max_curve > 0.05:
                offsets = [(-ox, -oy, 'left' if ha == 'right' else 'right') for ox, oy, ha in offsets]

        return offsets

    @staticmethod
    def _push_offsets_outside_cone(offsets, projected_points, cone_radii_px, margin=10):
        if not offsets or not cone_radii_px:
            return offsets
        n = min(len(offsets), len(projected_points), len(cone_radii_px))
        result = []
        for i in range(n):
            ox, oy, ha = offsets[i]
            r = cone_radii_px[i]
            if r <= 0:
                result.append((ox, oy, ha))
                continue
            d = math.hypot(ox, oy)
            min_d = r + margin
            if d < min_d:
                if d < 0.1:
                    ox = min_d
                    oy = 0
                else:
                    ox = ox / d * min_d
                    oy = oy / d * min_d
                ha = 'right' if ox < 0 else 'left'
            result.append((ox, oy, ha))
        return result


    def _draw_tracks_overlays(self, project_func, scene, img_w, img_h):

        if not hasattr(self.main_ui, 'track_overlay_items'):
            self.main_ui.track_overlay_items = []
        if not getattr(self.main_ui, 'tracks', None):
            return
        try:
            for t in self.main_ui.tracks:
                if not t.get("visible", True):
                    continue
                tid = t.get("id", "")
                if tid.startswith(("nhc_", "jma_", "jtwc_", "pagasa_")):
                    continue
                pts = t.get("points") or []
                if len(pts) < 1:
                    continue
                if t.get("type") == "Custom AoR":
                    continue
                sub_paths = []
                current_seg = []
                prev_nlon = None
                first_point = None
                point_meta = []

                for p in pts:
                    lon = p.get("lon")
                    lat = p.get("lat")
                    if lon is None or lat is None:
                        continue
                    nlon = normalize_lon(lon)
                    gx, gy = project_func(lon, lat)
                    if gx is None or gy is None:
                        if current_seg:
                            sub_paths.append(current_seg)
                            current_seg = []
                        prev_nlon = None
                        continue
                    if prev_nlon is not None and abs(nlon - prev_nlon) > 180.0:
                        if current_seg:
                            sub_paths.append(current_seg)
                            current_seg = []
                    qpt = QPointF(gx, gy)
                    current_seg.append(qpt)
                    point_meta.append((qpt, p))
                    if first_point is None:
                        first_point = qpt
                    prev_nlon = nlon
                if current_seg:
                    sub_paths.append(current_seg)
                
                # Cache for info box
                self.main_ui._track_proj_cache[t.get("id")] = point_meta

                dopts = t.get("display_options", {})
                show_line = dopts.get("show_track_line", True)
                show_pts  = dopts.get("show_points", True)

                if show_line:
                    for seg in sub_paths:
                        if len(seg) < 2:
                            continue
                        gpath = QPainterPath()
                        gpath.moveTo(seg[0])
                        for pt in seg[1:]:
                            gpath.lineTo(pt)
                        item = QGraphicsPathItem(gpath)
                        t_color = t.get("color", "#FF6B6B")
                        pen = QPen(QColor(t_color))
                        pen.setWidth(1.5)
                        pen.setStyle(Qt.SolidLine)
                        item.setPen(pen)
                        item.setZValue(24)
                        scene.addItem(item)
                        self.main_ui.track_overlay_items.append(item)

                show_cone = dopts.get("show_cone", True)
                cone_mode = dopts.get("cone_mode", "")
                if show_cone and cone_mode == "cwa_standard" and len(point_meta) >= 2:
                    left_pts = []
                    right_pts = []
                    cone_color = QColor(t.get("color", "#00BCD4"))
                    for i, (qpt, p) in enumerate(point_meta):
                        lon, lat = p["lon"], p["lat"]
                        gxe, gye = project_func(lon + 0.01, lat)
                        if gxe is None:
                            continue
                        scale = math.hypot(gxe - qpt.x(), gye - qpt.y()) / (0.01 * 111320.0)
                        r_km = p.get("prob_circle_km") or p.get("radius_km", 0)
                        if r_km <= 0:
                            continue
                        r_px = r_km * 1000.0 * scale
                        ci = QGraphicsEllipseItem(qpt.x() - r_px, qpt.y() - r_px, r_px * 2, r_px * 2)
                        cpen = QPen(cone_color)
                        cpen.setWidth(1)
                        cpen.setStyle(Qt.DashLine)
                        ci.setPen(cpen)
                        ci.setBrush(QBrush(QColor(0, 188, 212, 15)))
                        ci.setZValue(22)
                        scene.addItem(ci)
                        self.main_ui.track_overlay_items.append(ci)
                        if i < len(point_meta) - 1:
                            gx2, gy2 = point_meta[i+1][0].x(), point_meta[i+1][0].y()
                        else:
                            gx2, gy2 = point_meta[i-1][0].x(), point_meta[i-1][0].y()
                        dx = gx2 - qpt.x()
                        dy = gy2 - qpt.y()
                        dl = math.hypot(dx, dy)
                        if dl < 1:
                            continue
                        perp_x = -dy / dl
                        perp_y = dx / dl
                        left_pts.append(QPointF(qpt.x() + perp_x * r_px, qpt.y() + perp_y * r_px))
                        right_pts.append(QPointF(qpt.x() - perp_x * r_px, qpt.y() - perp_y * r_px))
                    if len(left_pts) >= 2 and len(right_pts) >= 2:
                        lpath = QPainterPath()
                        lpath.moveTo(left_pts[0])
                        for pt in left_pts[1:]:
                            lpath.lineTo(pt)
                        li = QGraphicsPathItem(lpath)
                        lp = QPen(cone_color)
                        lp.setWidth(1)
                        li.setPen(lp)
                        li.setZValue(22)
                        scene.addItem(li)
                        self.main_ui.track_overlay_items.append(li)
                        rpath = QPainterPath()
                        rpath.moveTo(right_pts[0])
                        for pt in right_pts[1:]:
                            rpath.lineTo(pt)
                        ri = QGraphicsPathItem(rpath)
                        ri.setPen(lp)
                        ri.setZValue(22)
                        scene.addItem(ri)
                        self.main_ui.track_overlay_items.append(ri)

                labeling_method = self.main_ui.settings.get("labeling_method", "polar")
                _track_proj = [qpt for qpt, _ in point_meta]
                if labeling_method in ("anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "auto",
                                       "railway_bezier", "8direction", "staggered_perp") and _HAS_NEW_ALGOS:
                    _track_pts_src = [p for _, p in point_meta]
                    label_offsets = OverlayController._compute_label_offsets_new(_track_pts_src, labeling_method)
                else:
                    label_offsets = self.main_ui._compute_label_offsets(_track_proj, labeling_method) if len(point_meta) >= 2 else None
                if label_offsets and len(_track_proj) >= 2:
                    _track_cone_r = []
                    for j, (qpt, p) in enumerate(point_meta):
                        lon, lat = p["lon"], p["lat"]
                        gxe, gye = project_func(lon + 0.01, lat)
                        if gxe is not None:
                            scale = math.hypot(gxe - qpt.x(), gye - qpt.y()) / (0.01 * 111320.0)
                            r_km = p.get("radius_km", 0)
                            if r_km <= 0:
                                r_km = 30 + j * 5.0
                            _track_cone_r.append(r_km * 1000.0 * scale)
                        else:
                            _track_cone_r.append(0)
                    label_offsets = OverlayController._push_offsets_outside_cone(label_offsets, _track_proj, _track_cone_r)

                if show_pts:
                    if not hasattr(self, '_sym_cache'):
                        self.main_ui._sym_cache = {}
                    _sym_dir = None

                    for i_pt, (qpt, p) in enumerate(point_meta):
                        val = p.get("k") or p.get("intensity") or p.get("vmax")
                        if isinstance(val, (int, float)) and not (isinstance(val, float) and (val != val)):

                            if val >= 64:
                                mcolor = QColor(255, 50, 50)
                                r = 4.5
                            elif val >= 34:
                                mcolor = QColor(255, 140, 0)
                                r = 3.5
                            elif val > 0:
                                mcolor = QColor(255, 220, 100)
                                r = 3
                            else:
                                mcolor = QColor("#FF6B6B")
                                r = 3
                        else:
                            mcolor = QColor("#FF6B6B")
                            r = 3

                        # Draw circular background dot
                        pt_cat = p.get("intensity_category", "")
                        pt_int = p.get("intensity")
                        use_sym = dopts.get("show_label_category", True)
                        dot = QGraphicsEllipseItem(qpt.x() - r, qpt.y() - r, r * 2, r * 2)
                        dot.setPen(QPen(mcolor.darker(120), 1))
                        dot.setBrush(QBrush(mcolor))
                        dot.setZValue(25)
                        scene.addItem(dot)
                        self.main_ui.track_overlay_items.append(dot)

                        if use_sym and pt_cat:
                            sym_name = self.main_ui._category_to_sym(pt_cat, pt_int)
                            if sym_name:
                                if _sym_dir is None:
                                    from src.core.helpers import top_dir
                                    _sym_dir = top_dir / "public" / "images" / "symbols"
                                sym_path = _sym_dir / f"{sym_name}.png"
                                if sym_path not in self.main_ui._sym_cache:
                                    self.main_ui._sym_cache[sym_path] = QPixmap(str(sym_path))
                                pix = self.main_ui._sym_cache[sym_path]
                                if not pix.isNull():
                                    sw = max(pix.width(), pix.height())
                                    sz = 64
                                    sym_item = QGraphicsPixmapItem(pix)
                                    sym_item.setScale(sz / sw)
                                    sym_item.setPos(qpt.x() - sz / 2, qpt.y() - sz / 2)
                                    sym_item.setZValue(26)
                                    scene.addItem(sym_item)
                                    self.main_ui.track_overlay_items.append(sym_item)

                        show_labels = dopts.get("show_labels", True)
                        if show_labels:
                            pt_time = p.get("datetime", "")
                            label_parts = []
                            if pt_time and dopts.get("show_label_time", True):
                                time_short = pt_time.split(" ")[-1] if " " in pt_time else pt_time
                                label_parts.append(time_short)
                            if pt_int is not None and dopts.get("show_label_speed", True):
                                wv = float(pt_int)
                                wu = self.main_ui.settings.get("forecast_preferences", {}).get("wind_format", "kt")
                                if wu == "kmh": wv = round(wv * 1.852); label_parts.append(f"{wv:.0f} km/h")
                                elif wu == "mph": wv = round(wv * 1.151); label_parts.append(f"{wv:.0f} mph")
                                elif wu == "ms": wv = round(wv * 0.514); label_parts.append(f"{wv:.0f} m/s")
                                else: label_parts.append(f"{wv:.0f} kt")
                            if label_parts:
                                node_lbl = QGraphicsTextItem(" ".join(label_parts))
                                node_lbl.setDefaultTextColor(mcolor)
                                node_lbl.setFont(QFont("Segoe UI", 6, QFont.Normal))
                                if label_offsets and i_pt < len(label_offsets):
                                    ox, oy, ha = label_offsets[i_pt]
                                    lx = qpt.x() + ox
                                    if ha == 'right':
                                        lx -= node_lbl.boundingRect().width()
                                    node_lbl.setPos(lx, qpt.y() + oy)
                                else:
                                    node_lbl.setPos(qpt.x() + r + 2, qpt.y() - 4)
                                node_lbl.setZValue(25)
                                scene.addItem(node_lbl)
                                self.main_ui.track_overlay_items.append(node_lbl)

                if first_point and dopts.get("show_label_name", True):
                    name = t.get("name") or t.get("type", "Track")
                    lbl = QGraphicsTextItem(name[:24])
                    t_color = t.get("color", "#FF6B6B")
                    lbl.setDefaultTextColor(QColor(t_color))
                    lbl.setFont(QFont("Segoe UI", 7, QFont.Normal))
                    if label_offsets and label_offsets[0]:
                        ox, oy, ha = label_offsets[0]
                        lx = first_point.x() + ox * 1.5
                        ly = first_point.y() + oy - 14
                        if ha == 'right':
                            lx -= lbl.boundingRect().width()
                        lbl.setPos(lx, ly)
                    else:
                        lbl.setPos(first_point.x() - 10, first_point.y() - 28)
                    lbl.setZValue(26)
                    scene.addItem(lbl)
                    self.main_ui.track_overlay_items.append(lbl)
        except Exception as e:
            self.main_ui.log(f"Tracks overlay draw error: {e}")


    def _update_sataid_overlays(self):
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return
        pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
        if not pixmap_item:
            return
        img_w = pixmap_item.pixmap().width()
        img_h = pixmap_item.pixmap().height()

        lon = self.main_ui._sataid_current_lon
        lat = self.main_ui._sataid_current_lat
        lon_min, lon_max = float(lon.min()), float(lon.max())
        lat_min, lat_max = float(lat.min()), float(lat.max())
        lon_range = lon_max - lon_min
        lat_range = lat_max - lat_min
        if lon_range <= 0 or lat_range <= 0:
            return

        def _ll2px(lon_val, lat_val):
            return ((lon_val - lon_min) / lon_range * img_w,
                    (lat_max - lat_val) / lat_range * img_h)

        def _deg_label(val, is_lat):
            if is_lat:
                if val == 0:
                    return "0\u00b0"
                return f"{abs(val):.0f}\u00b0{'N' if val > 0 else 'S'}"
            else:
                if val == 0:
                    return "0\u00b0"
                if val > 180:
                    return f"{360 - val:.0f}\u00b0W"
                if val < 0:
                    return f"{abs(val):.0f}\u00b0W"
                if val == 180:
                    return "180\u00b0"
                return f"{val:.0f}\u00b0E"

        grid_spacing = getattr(self, '_sataid_grid_spacing', 10)
        overlay_cache_key = f"sataid_{lon_min:.2f}_{lon_max:.2f}_{lat_min:.2f}_{lat_max:.2f}_{img_w}_{img_h}_g{grid_spacing}_ge{self.main_ui.grid_enabled}"

        if not hasattr(self, '_sataid_overlay_pixmap_cache'):
            self.main_ui._sataid_overlay_pixmap_cache = {}

        cached_pixmap = self.main_ui._sataid_overlay_pixmap_cache.get(overlay_cache_key)

        if cached_pixmap is None:
            qi = QImage(int(img_w), int(img_h), QImage.Format_ARGB32)
            qi.fill(Qt.transparent)
            p = QPainter(qi)
            p.setRenderHint(QPainter.Antialiasing)

            if self.main_ui.grid_enabled:
                gc = QColor(self.main_ui.settings.get("sataid_grid_color", "#4488FF"))
                gc.setAlpha(self.main_ui.settings.get("grid_opacity", 160))
                pen = QPen(gc)
                pen.setWidth(6)
                style_map = {
                    "solid": Qt.SolidLine, "dotted": Qt.DotLine,
                    "dashed": Qt.DashLine, "dashdot": Qt.DashDotLine,
                    "crosshatch": Qt.DashDotDotLine,
                }
                pen.setStyle(style_map.get(self.main_ui.settings.get("grid_pattern", "solid"), Qt.SolidLine))
                p.setPen(pen)

                lon_start = math.ceil(lon_min / grid_spacing) * grid_spacing
                lon_val = lon_start
                while lon_val <= lon_max:
                    path = QPainterPath()
                    first = True
                    lat_v = lat_min
                    while lat_v <= lat_max:
                        px, py = _ll2px(lon_val, lat_v)
                        if first:
                            path.moveTo(px, py)
                            first = False
                        else:
                            path.lineTo(px, py)
                        lat_v += 0.5
                    if not path.isEmpty():
                        p.drawPath(path)
                    lon_val += grid_spacing

                lat_start = math.ceil(lat_min / grid_spacing) * grid_spacing
                lat_val = lat_start
                while lat_val <= lat_max:
                    path = QPainterPath()
                    first = True
                    lon_v = lon_min
                    while lon_v <= lon_max:
                        px, py = _ll2px(lon_v, lat_val)
                        if first:
                            path.moveTo(px, py)
                            first = False
                        else:
                            path.lineTo(px, py)
                        lon_v += 0.5
                    if not path.isEmpty():
                        p.drawPath(path)
                    lat_val += grid_spacing

                gc2 = QColor(self.main_ui.settings.get("sataid_grid_color", "#4488FF"))
                gc2.setAlpha(self.main_ui.settings.get("grid_text_opacity", 200))
                font = QFont("Consolas", 10)
                font.setBold(True)
                p.setFont(font)
                p.setPen(QPen(gc2))

                lon_val = lon_start
                while lon_val <= lon_max:
                    px, py = _ll2px(lon_val, lat_min + (lat_range * 0.05))
                    if 0 <= px <= img_w and 0 <= py <= img_h:
                        label = _deg_label(lon_val, False)
                        p.drawText(int(px + 4), int(py - 10), label)
                    lon_val += grid_spacing

                lat_val = lat_start
                while lat_val <= lat_max:
                    px, py = _ll2px(lon_min + (lon_range * 0.02), lat_val)
                    if 0 <= px <= img_w and 0 <= py <= img_h:
                        label = _deg_label(lat_val, True)
                        p.drawText(int(px + 4), int(py - 10), label)
                    lat_val += grid_spacing

            p.end()
            cached_pixmap = QPixmap.fromImage(qi)
            self.main_ui._sataid_overlay_pixmap_cache[overlay_cache_key] = cached_pixmap

        if not hasattr(self, '_sataid_overlay_item') or self.main_ui._sataid_overlay_item is None:
            self.main_ui._sataid_overlay_item = QGraphicsPixmapItem()
            self.main_ui._sataid_overlay_item.setZValue(20)
            scene.addItem(self.main_ui._sataid_overlay_item)
        self.main_ui._sataid_overlay_item.setPixmap(cached_pixmap)
        self.main_ui._sataid_overlay_item.setVisible(bool(self.main_ui.grid_enabled))

        # Coastline is drawn as a vector path (edges of the admin-0 country
        # boundary polygons), mirroring SIFT's borders layer - not baked into
        # the grid pixmap. Segments are clipped to +/-89.9 and doubled +360
        # so the outline wraps the antimeridian on both sides of the map.
        if self.main_ui.coast_enabled:
            if not self.main_ui._coast_raw_segments:
                self._coast_np_segments()
                if not self.main_ui._coast_raw_segments:
                    self.main_ui.coast_enabled = False
                    self.main_ui.log("Borders/coastline shapefile not found; coastline disabled.")

            coast_cache_key2 = f"sataid_coast_{lon_min:.2f}_{lon_max:.2f}_{lat_min:.2f}_{lat_max:.2f}_{img_w}_{img_h}"
            if not hasattr(self, '_sataid_coast_path_cache') or self.main_ui._sataid_coast_path_cache is None:
                self.main_ui._sataid_coast_path_cache = {}

            cached_path = self.main_ui._sataid_coast_path_cache.get(coast_cache_key2)
            if cached_path is None:
                coast_path = QPainterPath()
                runs = []
                for seg in self.main_ui._coast_raw_segments:
                    lon = seg[:, 0]
                    lat = seg[:, 1]
                    lon_norm = np.where(lon >= 0, lon, lon + 360.0)
                    inb = ((lon_norm >= lon_min) & (lon_norm <= lon_max)
                           & (lat >= lat_min) & (lat <= lat_max))
                    idx = np.where(inb)[0]
                    if len(idx) < 2:
                        continue
                    px = (lon_norm - lon_min) / lon_range * img_w
                    py = (lat_max - lat) / lat_range * img_h
                    for chunk in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                        if len(chunk) >= 2:
                            runs.append(np.column_stack([px[chunk], py[chunk]]))
                self._append_pixel_path(coast_path, runs, min_step_px=1.0)
                cached_path = coast_path
                self.main_ui._sataid_coast_path_cache[coast_cache_key2] = cached_path

            if not hasattr(self, '_sataid_coast_item') or self.main_ui._sataid_coast_item is None:
                self.main_ui._sataid_coast_item = QGraphicsPathItem()
                self.main_ui._sataid_coast_item.setZValue(21)
                scene.addItem(self.main_ui._sataid_coast_item)
            coast_pen = QPen(QColor(self.main_ui.settings.get("coast_color", "#88FF88")))
            coast_pen.setWidth(int(self.main_ui.settings.get("coast_width", 3)))
            try:
                coast_pen.color().setAlpha(int(self.main_ui.settings.get("coast_opacity", 200)))
            except Exception:
                pass
            self.main_ui._sataid_coast_item.setPath(cached_path)
            self.main_ui._sataid_coast_item.setPen(coast_pen)
            self.main_ui._sataid_coast_item.setVisible(True)
        elif hasattr(self, '_sataid_coast_item') and self.main_ui._sataid_coast_item is not None:
            self.main_ui._sataid_coast_item.setVisible(False)


    def _remove_all_overlay_items(self):
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return
        all_lists = [
            getattr(self.main_ui, 'grid_overlay_items', []),
            getattr(self.main_ui, 'coast_overlay_items', []),
            getattr(self, 'aor_overlay_items', []),
            getattr(self, 'track_overlay_items', []),
            getattr(self, '_wind_items', []),
            getattr(self.main_ui, '_wind_items', []),
            getattr(self, 'nhc_cone_overlay_items', []),
            getattr(self, '_nhc_overlay_items', []),
            getattr(self, '_jma_overlay_items', []),
            getattr(self, '_jtwc_overlay_items', []),
            getattr(self, '_pagasa_overlay_items', []),
            getattr(self, '_atcf_overlay_items', []),
            getattr(self.main_ui, '_recon_overlay_items', []),
        ]
        for code in ("TC", "WET", "DRY", "WARM", "COLD"):
            lst = getattr(self, f'_climate_overlay_items_{code.lower()}', [])
            if lst:
                all_lists.append(lst)
        gtwo_items = getattr(self, '_gtwo_overlay_items', [])
        if gtwo_items:
            all_lists.append(gtwo_items)
        dropped_items = getattr(self, '_dropped_shp_items', [])
        if dropped_items:
            all_lists.append(dropped_items)
        for lst in all_lists:
            for item in lst:
                try:
                    if item.scene():
                        scene.removeItem(item)
                except Exception:
                    pass
            lst.clear()
        # Full teardown invalidates the sticky winds so the next overlay
        # pass rebuilds them (new image / settings change, etc.).
        self.main_ui._winds_persist = False
        for attr in ('_overlay_item', '_grid_item', '_coast_item', '_coast_pix_item', '_coast_swap_item', '_sataid_overlay_item', '_sataid_coast_item', '_grid_overlay_item', '_coast_overlay_item'):
            item = getattr(self.main_ui, attr, None) or getattr(self, attr, None)
            if item is not None:
                try:
                    if item.scene():
                        scene.removeItem(item)
                except Exception:
                    pass
                setattr(self.main_ui, attr, None) if hasattr(self.main_ui, attr) else setattr(self, attr, None)


    def _get_quality_grid(self):
        quality = getattr(self, 'preview_quality', "2km res")
        if quality == "Full Res":
            return None
        # Rectilinear (equirectangular / plate carree) imagery is displayed at
        # its native raster size -- there is no geostationary "disk" to fit a
        # quality grid into, so the geos-based canvas math below must be skipped.
        if getattr(self.main_ui, '_current_display_projection', 'full_disk') in ("equirectangular", "plate_carree"):
            return None
        half = self.main_ui._compute_disk_half_extent()
        res_m = getattr(self, 'preview_res_m', 2000)
        if res_m <= 0:
            return None
        dim = max(1, int(round(2 * half / res_m)))
        # Cap quality grid size to preview_max_px to prevent overscaling
        max_px = getattr(self, 'preview_max_px', dim)
        if max_px > 0 and dim > max_px:
            dim = max_px
        return (dim, dim, res_m, half)


    def _compute_disk_half_extent(self):
        h_sat = 35785863.0
        try:
            cf = self.main_ui.current_crs.to_cf()
            _R = float(cf.get("semi_major_axis", 6378137.0))
            _h_sat = float(cf.get("perspective_point_height", h_sat))
        except Exception:
            _R, _h_sat = 6378137.0, h_sat
        _sin_theta = _R / (_R + _h_sat)
        return _h_sat * math.tan(math.asin(_sin_theta))


    def _get_image_scene_pos(self):
        if not self.main_ui.current_crs or not self.main_ui.current_geotransform:
            return None
        gq = self.main_ui._get_quality_grid()
        if gq is not None:
            return QPointF(0, 0)
        gt = self.main_ui.current_geotransform
        if gt.a != 0 and gt.e != 0 and gt.c != 0 and gt.f != 0:
            img_w = int(round(2 * abs(gt.c) / abs(gt.a))) if gt.c != 0 else 0
            img_h = int(round(2 * abs(gt.f) / abs(gt.e))) if gt.f != 0 else 0
            data_extent = max(abs(gt.c), abs(gt.c + gt.a * max(img_w, 1)),
                              abs(gt.f), abs(gt.f + gt.e * max(img_h, 1)))
            img_dim = max(img_w, img_h, 1)
            effective_res_m = (2 * data_extent) / img_dim
            ox = (gt.c + data_extent) / effective_res_m
            oy = (data_extent - gt.f) / effective_res_m
            return QPointF(ox, oy)
        disk_half = self.main_ui._compute_disk_half_extent()
        native_res_m = abs(gt.a)
        ox = (gt.c + disk_half) / native_res_m
        oy = (disk_half - gt.f) / native_res_m
        return QPointF(ox, oy)


    def _overlay_geo_signature(self):
        """Fingerprint of the imaging/projection geometry for the current display.

        This is the key that must change whenever a different satellite (or a
        different sector/display projection) is loaded, because every overlay —
        grid, coastlines, AoRs and tracks — is projected into the image's own
        coordinate space. Returns ``None`` when geometry is not yet available.
        """
        gt = self.main_ui.current_geotransform
        if gt is None:
            return None
        try:
            return (
                round(gt.a, 6), round(gt.b, 6), round(gt.c, 6),
                round(gt.d, 6), round(gt.e, 6), round(gt.f, 6),
                getattr(self.main_ui, '_current_display_projection', 'full_disk'),
            )
        except Exception:
            return None

    def _overlay_cache_key(self):
        """Return a hashable key for current overlay-relevant state, or None."""
        try:
            gt = self.main_ui.current_geotransform
            if gt is None:
                return None
            scene = self.main_ui.graphics_view.scene()
            if not scene:
                return None
            _gq_active = self.main_ui._get_quality_grid() is not None
            parts = [
                round(gt.a, 6), round(gt.b, 6), round(gt.c, 6),
                round(gt.d, 6), round(gt.e, 6), round(gt.f, 6),
                self.main_ui.grid_enabled, self.main_ui.coast_enabled,
                self.main_ui.settings.get("grid_color", ""), int(self.main_ui.settings.get("grid_opacity", 0)),
                int(self.main_ui.settings.get("grid_line_width", 0)), str(self.main_ui.settings.get("grid_pattern", "")),
                float(self.main_ui.settings.get("grid_spacing_deg", 0)), float(self.main_ui.settings.get("grid_sub_step_tenths", 0)),
                str(self.main_ui.settings.get("coast_color", "")), int(self.main_ui.settings.get("coast_opacity", 0)),
                int(self.main_ui.settings.get("coast_line_width", 0)),
                str(self.main_ui.settings.get("coast_region", "")),
                self.main_ui._is_beta_viewport(),
                getattr(self, 'preview_quality', '2km res'),
                getattr(self.main_ui, 'satellite_lon', 140.7),
                getattr(self.main_ui, 'sat_combo', None) and self.main_ui.sat_combo.currentData(),
            ]
            # Always include image dimensions so overlay is sized correctly
            pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
            if pixmap_item:
                _bname = getattr(self, '_current_band_name', '')
                parts += [pixmap_item.pixmap().width(), pixmap_item.pixmap().height(), _bname]
            return str(tuple(parts))
        except Exception:
            return None


    def _grid_cache_key(self):
        """Generate cache key based on geotransform and settings only - not display size."""
        try:
            gt = self.main_ui.current_geotransform
            if gt is None:
                return None
            parts = [
                round(gt.a, 6), round(gt.b, 6), round(gt.c, 6),
                round(gt.d, 6), round(gt.e, 6), round(gt.f, 6),
                self.main_ui._is_beta_viewport(),
                self.main_ui.settings.get("grid_color", ""), int(self.main_ui.settings.get("grid_opacity", 0)),
                int(self.main_ui.settings.get("grid_line_width", 0)), str(self.main_ui.settings.get("grid_pattern", "")),
                float(self.main_ui.settings.get("grid_spacing_deg", 0)), float(self.main_ui.settings.get("grid_sub_step_tenths", 0)),
                getattr(self, 'preview_quality', '2km res'),
                getattr(self.main_ui, 'satellite_lon', 140.7),
                getattr(self.main_ui, '_current_display_projection', 'full_disk'),
                getattr(self.main_ui, 'sat_combo', None) and self.main_ui.sat_combo.currentData(),
            ]
            return str(tuple(parts))
        except Exception:
            return None


    def _coast_shapefile(self):
        """Resolve the borders/coastline shapefile path.

        Mirrors SIFT's ``--border-shapefile`` override (main.py): when
        ``_coastline_shp_path`` is set it wins; otherwise default to the
        admin-0 country polygons (borders layer) with the 10m coastline as
        a fallback.
        """
        override = getattr(self, '_coastline_shp_path', None)
        if override:
            return Path(override)
        return BORDERS_SHP if BORDERS_SHP.exists() else COASTLINE_SHP

    def _coast_np_segments(self, stride=1, double=True):
        """Return cached numpy ``(N, 2)`` lon/lat coastline segments.

        Hardens the shapefile decode the SIFT way: the borders file is parsed
        once per process (cached) into per-segment numpy arrays so every draw
        rebuild can project with vectorized calls instead of a per-point Python
        loop. The result is stashed on ``main_ui._coast_raw_segments`` so the
        existing ``for segment in ..._coast_raw_segments`` loops keep working.
        """
        segs = getattr(self.main_ui, '_coast_raw_segments', None)
        if segs:
            first = segs[0]
            if isinstance(first, np.ndarray):
                return segs
        segs = load_border_segments_np(str(self._coast_shapefile()), stride=stride, double=double)
        self.main_ui._coast_raw_segments = segs
        return segs

    @staticmethod
    def _append_pixel_path(path, pixel_segments, min_step_px=1.0):
        """Decimate projected ``(N, 2)`` pixel segments and append to a QPainterPath.

        ``pixel_segments`` is an iterable of ``(N, 2)`` float arrays of
        ``(col, row)`` — one array per contiguous visible run. Points closer
        than ``min_step_px`` are dropped so the final path stays small enough
        for interactive pan/zoom while remaining crisp at the baked resolution.
        """
        for seg in pixel_segments:
            arr = np.asarray(seg, dtype=np.float64)
            if arr.ndim != 2 or arr.shape[0] < 2:
                continue
            arr = decimate_screen_points(arr, min_step_px)
            if arr.shape[0] < 2:
                continue
            path.moveTo(QPointF(arr[0, 0], arr[0, 1]))
            for k in range(1, arr.shape[0]):
                path.lineTo(QPointF(arr[k, 0], arr[k, 1]))

    def _project_geos_visible_runs(
        self, lons, lats, transformer, disk_half_extent, native_res_m,
        overlay_w, overlay_h, sat_lon, clip_lats=None, min_pts=2,
    ):
        """Vectorized geostationary projection into visible pixel-space runs.

        Given lon/lat arrays, returns a list of ``(M, 2)`` float arrays of
        ``(col, row)`` pixel coordinates covering every contiguous run that is
        (a) on the Earth-facing side of the satellite, (b) within the disk
        extent and (c) inside the overlay bounds. A single vectorized
        ``transformer.transform`` replaces the old per-point Python loop.
        """
        lons_arr = np.asarray(lons, dtype=np.float64)
        lats_arr = np.asarray(lats, dtype=np.float64)
        if lons_arr.ndim != 1 or lons_arr.shape[0] < min_pts:
            return []
        if clip_lats is not None:
            np.clip(lats_arr, -clip_lats, clip_lats, out=lats_arr)
        lons_norm = (lons_arr + 180.0) % 360.0 - 180.0
        cos_angle = np.cos(np.radians(lats_arr)) * np.cos(np.radians(lons_norm) - math.radians(sat_lon))
        visible = cos_angle > 0.05
        idx = np.where(visible)[0]
        if len(idx) < min_pts:
            return []
        runs = []
        _half = disk_half_extent
        _res = native_res_m
        for chunk in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
            if len(chunk) < min_pts:
                continue
            s_lons = lons_norm[chunk]
            s_lats = lats_arr[chunk]
            x_proj, y_proj = transformer.transform(s_lons, s_lats)
            ok = np.isfinite(x_proj) & np.isfinite(y_proj)
            ok &= (np.abs(x_proj) <= _half * 1.01) & (np.abs(y_proj) <= _half * 1.01)
            with np.errstate(invalid='ignore'):
                cols = (x_proj + _half) / _res
                rows = (_half - y_proj) / _res
            ok &= (cols >= 0) & (cols < overlay_w) & (rows >= 0) & (rows < overlay_h)
            ix = np.where(ok)[0]
            if len(ix) < min_pts:
                continue
            for sub in np.split(ix, np.where(np.diff(ix) != 1)[0] + 1):
                if len(sub) >= min_pts:
                    runs.append(np.column_stack([cols[sub], rows[sub]]))
        return runs


    def _coast_cache_key(self):
        """Generate cache key based on geotransform and settings only - not display size."""
        try:
            gt = self.main_ui.current_geotransform
            if gt is None:
                return None
            parts = [
                round(gt.a, 6), round(gt.b, 6), round(gt.c, 6),
                round(gt.d, 6), round(gt.e, 6), round(gt.f, 6),
                self.main_ui.settings.get("coast_color", ""), int(self.main_ui.settings.get("coast_opacity", 0)),
                int(self.main_ui.settings.get("coast_line_width", 0)), str(self.main_ui.settings.get("coast_pattern", "")),
                self.main_ui._is_beta_viewport(),
                getattr(self, 'preview_quality', '2km res'),
                getattr(self.main_ui, 'satellite_lon', 140.7),
                getattr(self.main_ui, '_current_display_projection', 'full_disk'),
                getattr(self.main_ui, 'sat_combo', None) and self.main_ui.sat_combo.currentData(),
            ]
            return str(tuple(parts))
        except Exception:
            return None


    def _overlay_disk_cache_path(self, prefix):
        key = self.main_ui._grid_cache_key() if prefix == "grid" else self.main_ui._coast_cache_key()
        if key is None:
            return None
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self.main_ui._overlay_cache_dir / f"{prefix}_{h}.png"


    def _cache_overlay_geo(self):
        if not self.main_ui.current_crs or not self.main_ui.current_geotransform:
            return False
        try:
            disk_half_extent = self.main_ui._compute_disk_half_extent()
            gt = self.main_ui.current_geotransform
            native_res_m = abs(gt.a)
            transformer = self.main_ui._overlay_transformer
            if transformer is None:
                from pyproj import Transformer
                transformer = Transformer.from_crs("EPSG:4326", self.main_ui.current_crs, always_xy=True)
                self.main_ui._overlay_transformer = transformer
            fd_pix = int(round(2 * disk_half_extent / native_res_m)) if native_res_m > 0 else 0
            self.main_ui._ol_disk_half_extent = disk_half_extent
            self.main_ui._ol_native_res_m = native_res_m
            self.main_ui._ol_transformer = transformer
            self.main_ui._ol_overlay_w = fd_pix
            self.main_ui._ol_overlay_h = fd_pix
            return True
        except Exception:
            return False


    def _project_lonlat_to_pixel(self, lon, lat):
        display_proj = getattr(self.main_ui, '_current_display_projection', 'full_disk')
        if display_proj in ("equirectangular", "plate_carree"):
            extent = getattr(self.main_ui, '_display_projection_extent', None)
            if extent is None:
                return None, None
            lon_min, lat_min, lon_max, lat_max = extent
            w = getattr(self.main_ui, '_ol_overlay_w', 5476)
            h = getattr(self.main_ui, '_ol_overlay_h', 5476)
            lon_range = lon_max - lon_min
            lat_range = lat_max - lat_min
            if lon_range <= 0 or lat_range <= 0:
                return None, None
            if lon < lon_min:
                lon += 360.0
            col = (lon - lon_min) * w / lon_range
            row = (lat_max - lat) * h / lat_range
            if col < 0 or col >= w or row < 0 or row >= h:
                return None, None
            return col, row
        try:
            lon = normalize_lon(lon)
            x_proj, y_proj = self.main_ui._ol_transformer.transform(lon, lat)
            if math.isinf(x_proj) or math.isinf(y_proj) or math.isnan(x_proj) or math.isnan(y_proj):
                return None, None
            if abs(x_proj) > self.main_ui._ol_disk_half_extent * 1.01 or abs(y_proj) > self.main_ui._ol_disk_half_extent * 1.01:
                return None, None
            col = (x_proj + self.main_ui._ol_disk_half_extent) / self.main_ui._ol_native_res_m
            row = (self.main_ui._ol_disk_half_extent - y_proj) / self.main_ui._ol_native_res_m
            return col, row
        except Exception:
            return None, None


    def _pixmap_to_numpy(self, pixmap):
        try:
            img = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
            w, h = img.width(), img.height()
            if w < 2 or h < 2:
                return None
            buf = bytes(img.constBits())
            if len(buf) < w * h * 4:
                return None
            return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        except Exception:
            return None

    def _correct_sub_area_gt(self, gt, tarr, nc_path, sector):
        """Data-validate a non-FLDK geotransform for scene/animation placement.
        Prefers JMA HSD scanning-angle geolocation (exact, no image content
        needed); falls back to content-correlation against the full-disk
        reference (same orientation validation used by the geo-target
        animation composite). Returns the corrected geotransform, or ``gt``
        unchanged when no reference/validation is possible."""
        try:
            if nc_path is None or gt is None:
                return gt
            ac = getattr(self.main_ui, 'animation_controller', None)
            if ac is None:
                return gt
            thr = tw = 0
            if tarr is not None:
                thr, tw = tarr.shape[:2]
            if hasattr(ac, '_read_coff_loff') and hasattr(ac, '_scan_angle_target_gt'):
                nav = ac._read_coff_loff(nc_path)
                ref_gt = None
                if hasattr(ac, '_scene_geo_gt'):
                    ref_gt = ac._scene_geo_gt(nc_path)
                if nav is not None and ref_gt is not None:
                    out = ac._scan_angle_target_gt(nav, ref_gt, thr, tw, gt)
                    if out is not None:
                        try:
                            self.main_ui.log(f"[Geo] Sub-area {nc_path.name}: HSD scan-angle gt (COFF/LOFF)")
                        except Exception:
                            pass
                        return out
            if tarr is None:
                return gt
            if not hasattr(ac, '_scene_geo_reference'):
                return gt
            if not hasattr(ac, '_resolve_target_gt'):
                return gt
            ref, ref_gt = ac._scene_geo_reference(nc_path)
            if ref is None or ref_gt is None:
                return gt
            hgt, wgt = ref.shape[:2]
            native_w = max(1, int(round(2 * abs(ref_gt.c) / abs(ref_gt.a))))
            native_h = max(1, int(round(2 * abs(ref_gt.f) / abs(ref_gt.e))))
            scl_x = wgt / native_w
            scl_y = hgt / native_h
            if sector in (None, "FLDK", "Full Disk", "Geo Target"):
                sector = "Target"
            return ac._resolve_target_gt(ref, ref_gt, gt, tarr, scl_x, scl_y, sector, thr, tw)
        except Exception:
            return gt

    def _prepare_cached_display(self, rgba):
        gq = self.main_ui._get_quality_grid()
        if gq is None:
            return rgba
        gt = getattr(self.main_ui, 'current_geotransform', None)
        if gt is None:
            nc = self.main_ui._current_nc_file()
            if nc is not None:
                _c, gt = self.main_ui.extract_crs_from_ads(nc)
                if _c and gt:
                    self.main_ui.current_crs = _c
                    self.main_ui.current_geotransform = gt
                    self.main_ui._overlay_geo_unavailable = False
        if gt is None:
            return rgba
        rw, rh = gq[:2]
        half = gq[3]
        is_fldk = abs(gt.c + half) < abs(half) * 0.01 and abs(gt.f - half) < abs(half) * 0.01
        if not is_fldk:
            h, w = rgba.shape[:2]
            nc_path = self.main_ui._current_nc_file()
            sector = self.main_ui._detect_current_sector()
            gtc = self._correct_sub_area_gt(gt, rgba, nc_path, sector)
            if gtc is not None:
                gt = gtc
            native_res_m = abs(gt.a)
            target_res = gq[2]
            if native_res_m is not None and target_res > 0:
                scale = native_res_m / target_res
                if abs(scale - 1.0) > 0.01:
                    new_w = max(1, int(round(w * scale)))
                    new_h = max(1, int(round(h * scale)))
                    from skimage.transform import resize as _skresize
                    rgba = _skresize(rgba, (new_h, new_w), preserve_range=True, anti_aliasing=True).clip(0, 255).astype(np.uint8)
                    self.main_ui.log(f"[Cache] Resized imagery {w}x{h} -> {new_w}x{new_h} (scale={scale:.3f})")
                    h, w = new_h, new_w
            ox = int(round((gt.c + half) / gq[2]))
            oy = int(round((half - gt.f) / gq[2]))
            expected_w = w
            expected_h = h
            if w > expected_w or h > expected_h:
                crop_x = max(0, ox)
                crop_y = max(0, oy)
                crop_w = min(expected_w, w - crop_x)
                crop_h = min(expected_h, h - crop_y)
                if crop_w > 0 and crop_h > 0 and crop_x < w and crop_y < h:
                    rgba = rgba[crop_y:crop_y+crop_h, crop_x:crop_x+crop_w]
                    h, w = crop_h, crop_w
                src_x = 0; src_y = 0
                dest_x = max(0, ox); dest_y = max(0, oy)
            else:
                src_y = max(0, -oy); src_x = max(0, -ox)
                dest_y = max(0, oy); dest_x = max(0, ox)
            cw = min(w - src_x, rw - dest_x)
            ch = min(h - src_y, rh - dest_y)
            if cw > 0 and ch > 0:
                canvas = np.zeros((rh, rw, 4), dtype=np.uint8)
                canvas[dest_y:dest_y+ch, dest_x:dest_x+cw] = rgba[src_y:src_y+ch, src_x:src_x+cw]
                self.main_ui.log(f"[Cache] Pre-placed {w}x{h} on canvas {rw}x{rh} at ({ox},{oy}) crop={src_x},{src_y}")
                return canvas
        return rgba


    def _compute_native_res_m(self, pixel_width):
        """Compute native band resolution in meters using ref_grid_size or geotransform."""
        gt = getattr(self, 'current_geotransform', None)
        if gt is None or abs(gt.a) < 0.001 or pixel_width < 1:
            return None
        ref = self.main_ui._get_ref_grid_size()
        if ref is not None:
            ref_w = ref[0]
            return (ref_w * abs(gt.a)) / pixel_width
        return abs(gt.a)


    def _compute_pixmap_scale(self, pixmap, target_res_m):
        """Compute scale factor to adjust pixmap from native band resolution to target_res_m."""
        native_res_m = self.main_ui._compute_native_res_m(pixmap.width())
        if native_res_m is None or native_res_m < 0.001:
            return 1.0
        return native_res_m / target_res_m


    def _resize_for_fldk(self, pixmap, sector=None, use_ref_gt=False):
        """Resize pixmap to match quality grid (when active) or FLDK ref grid.
        Pass `sector` explicitly when called from animation path. Pass
        `use_ref_gt=True` from the animation path to skip the sub-area
        geotransform correction (animation frames are pre-aligned to the
        reference ground)."""
        from PySide6.QtCore import QRect
        gq = self.main_ui._get_quality_grid()
        if gq is not None:
            rw, rh = gq[:2]
            gt = getattr(self.main_ui, 'current_geotransform', None)
            if gt is not None and rw > 0 and rh > 0:
                half = gq[3]
                is_fldk = abs(gt.c + half) < abs(half) * 0.01 and abs(gt.f - half) < abs(half) * 0.01
                _ref_gt = getattr(self.main_ui, '_anim_ref_gt', None)
                ref_is_fldk = (_ref_gt is not None and
                               abs(_ref_gt.c + half) < abs(half) * 0.01 and
                               abs(_ref_gt.f - half) < abs(half) * 0.01)
                if not is_fldk and not (use_ref_gt and ref_is_fldk):
                    sector_name = sector or self.main_ui._detect_current_sector()
                    gtc = self._correct_sub_area_gt(
                        gt, None,
                        self.main_ui._current_nc_file(), sector_name)
                    if gtc is not None:
                        gt = gtc
                    scale = abs(gt.a) / gq[2]
                    if scale > 0 and abs(scale - 1.0) > 0.01:
                        sw = max(1, int(round(pixmap.width() * scale)))
                        sh = max(1, int(round(pixmap.height() * scale)))
                        pixmap = pixmap.scaled(sw, sh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    ox = int(round((gt.c + half) / gq[2]))
                    oy = int(round((half - gt.f) / gq[2]))
                    px, py = pixmap.width(), pixmap.height()
                    expected_w = px
                    expected_h = py
                    if px > expected_w or py > expected_h:
                        crop_x = max(0, ox)
                        crop_y = max(0, oy)
                        crop_w = min(expected_w, px - crop_x)
                        crop_h = min(expected_h, py - crop_y)
                        if crop_w > 0 and crop_h > 0 and crop_x < px and crop_y < py:
                            pixmap = pixmap.copy(QRect(crop_x, crop_y, crop_w, crop_h))
                            px, py = crop_w, crop_h
                        src_x = 0
                        src_y = 0
                        dest_x = max(0, ox)
                        dest_y = max(0, oy)
                    else:
                        src_x = max(0, -ox)
                        src_y = max(0, -oy)
                        dest_x = max(0, ox)
                        dest_y = max(0, oy)
                    cw = min(px - src_x, rw - dest_x)
                    ch = min(py - src_y, rh - dest_y)
                    if cw > 0 and ch > 0 and oy < rh and ox < rw:
                        canvas = QImage(rw, rh, QImage.Format_ARGB32_Premultiplied)
                        canvas.fill(0)
                        p = QPainter(canvas)
                        p.drawPixmap(QPoint(dest_x, dest_y), pixmap, QRect(src_x, src_y, cw, ch))
                        p.end()
                        self.main_ui.current_geotransform = rasterio.transform.Affine(gq[2], 0.0, -half, 0.0, -gq[2], half)
                        return QPixmap.fromImage(canvas)
            if pixmap.width() >= rw and pixmap.height() >= rh:
                return pixmap
            return pixmap.scaled(rw, rh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        else:
            # On Full Res or no quality grid: only FLDK (+ Geo Target composite) gets ref-grid resize
            sec = sector or self.main_ui._detect_current_sector()
            if sec not in ("FLDK", "Geo Target", "Geo", "GeoTarget", "GEOTARGET", "geo target",
                           "Full GEO", "FullGEO", "full geo"):
                return pixmap
            ref = self.main_ui._get_ref_grid_size()
            if ref is None:
                return pixmap
            rw, rh = ref
            if pixmap.width() >= rw and pixmap.height() >= rh:
                return pixmap
            return pixmap.scaled(rw, rh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)


    def _update_grid_coast_overlay(self):
        if not hasattr(self.main_ui, '_ol_disk_half_extent') or not hasattr(self.main_ui, '_ol_overlay_w'):
            self.main_ui.update_overlays()
            return
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return
        self.main_ui._render_grid_cached(scene, self.main_ui._ol_overlay_w, self.main_ui._ol_overlay_h,
                                  self.main_ui._ol_disk_half_extent, self.main_ui._ol_native_res_m, self.main_ui._ol_transformer)
        self.main_ui._render_coast_cached(scene, self.main_ui._ol_overlay_w, self.main_ui._ol_overlay_h,
                                   self.main_ui._ol_disk_half_extent, self.main_ui._ol_native_res_m, self.main_ui._ol_transformer)
        self.main_ui._render_all_climate_overlays()
        if getattr(self, 'gtwo_cb', None) and self.main_ui.gtwo_cb.isChecked():
            self.main_ui._render_gtwo_overlay()
        self.main_ui.graphics_view.viewport().update()


    def _update_aor_overlays(self):
        if (not hasattr(self.main_ui, '_ol_project_func') or not hasattr(self.main_ui, '_ol_overlay_w')
                or getattr(self.main_ui, '_ol_geo_sig', None) is None):
            self.main_ui.update_overlays()
            return
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return
        for item in self.main_ui.aor_overlay_items[:]:
            try:
                if item.scene():
                    scene.removeItem(item)
            except RuntimeError:
                pass
        self.main_ui.aor_overlay_items.clear()
        self.main_ui._draw_aor_overlays(self.main_ui._ol_project_func, scene, self.main_ui._ol_overlay_w, self.main_ui._ol_overlay_h)
        self.main_ui.graphics_view.viewport().update()


    def _update_tracks_overlays(self):
        if (not hasattr(self.main_ui, '_ol_project_func') or not hasattr(self.main_ui, '_ol_overlay_w')
                or getattr(self.main_ui, '_ol_geo_sig', None) is None):
            self.main_ui.update_overlays()
            return
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return
        for item in self.main_ui.track_overlay_items[:]:
            try:
                if item.scene():
                    scene.removeItem(item)
            except RuntimeError:
                pass
        self.main_ui.track_overlay_items.clear()
        self.main_ui._redraw_nhc_only()
        self.main_ui._redraw_jma_only()
        self.main_ui._redraw_jtwc_only()
        self.main_ui._redraw_pagasa_only()
        self.main_ui._draw_tracks_overlays(self.main_ui._ol_project_func, scene, self.main_ui._ol_overlay_w, self.main_ui._ol_overlay_h)
        self.main_ui.graphics_view.viewport().update()


    def _render_grid_cached(self, scene, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer):
        """Render grid as cached QGraphicsPathItem or QImage based on performance setting and display projection."""
        key = self.main_ui._grid_cache_key()
        if key is None:
            return False
        
        render_mode = self.main_ui.settings.get("grid_coast_render_mode", "performance")
        use_qimage = (render_mode == "performance")
        
        # Check cache - path cache for Quality, image cache for Performance
        cache_attr = '_grid_image_cache' if use_qimage else '_grid_path_cache'
        if not hasattr(self, cache_attr):
            setattr(self, cache_attr, {})
        cache_dict = getattr(self, cache_attr)
        
        if key in cache_dict:
            if use_qimage:
                cached_pixmap = cache_dict[key]
                if self.main_ui._grid_item is None:
                    self.main_ui._grid_item = QGraphicsPixmapItem(cached_pixmap)
                    self.main_ui._grid_item.setZValue(20)
                    scene.addItem(self.main_ui._grid_item)
                else:
                    self.main_ui._grid_item.setPixmap(cached_pixmap)
                self.main_ui._grid_item.setVisible(self.main_ui.grid_enabled)
            else:
                cached_path = cache_dict[key]
                if self.main_ui._grid_item is None:
                    self.main_ui._grid_item = QGraphicsPathItem()
                    self.main_ui._grid_item.setZValue(20)
                    scene.addItem(self.main_ui._grid_item)
                self.main_ui._grid_item.setPath(cached_path)
                gc = QColor(self.main_ui.settings.get("grid_color", "#C8C8C8"))
                gc.setAlpha(self.main_ui.settings.get("grid_opacity", 160))
                pen = QPen(gc)
                pen.setWidth(self.main_ui.settings.get("grid_line_width", 1))
                pattern = self.main_ui.settings.get("grid_pattern", "solid")
                style_map = {
                    "solid": Qt.SolidLine, "dotted": Qt.DotLine,
                    "dashed": Qt.DashLine, "dashdot": Qt.DashDotLine,
                    "crosshatch": Qt.DashDotDotLine,
                }
                pen.setStyle(style_map.get(pattern, Qt.SolidLine))
                self.main_ui._grid_item.setPen(pen)
                self.main_ui._grid_item.setVisible(self.main_ui.grid_enabled)
            self.main_ui._last_grid_cache_key = key
            return True
            
        if not self.main_ui.grid_enabled:
            if self.main_ui._grid_item is not None:
                self.main_ui._grid_item.setVisible(False)
            self.main_ui._last_grid_cache_key = key
            return True
            
        gc = QColor(self.main_ui.settings.get("grid_color", "#C8C8C8"))
        gc.setAlpha(self.main_ui.settings.get("grid_opacity", 160))
        pen = QPen(gc)
        pen.setWidth(self.main_ui.settings.get("grid_line_width", 1))
        pattern = self.main_ui.settings.get("grid_pattern", "solid")
        style_map = {
            "solid": Qt.SolidLine, "dotted": Qt.DotLine,
            "dashed": Qt.DashLine, "dashdot": Qt.DashDotLine,
            "crosshatch": Qt.DashDotDotLine,
        }
        pen.setStyle(style_map.get(pattern, Qt.SolidLine))
        grid_spacing = self.main_ui.settings.get("grid_spacing_deg", 10)
        sub_step = max(0.1, self.main_ui.settings.get('grid_sub_step_tenths', 20) * 0.1)
        
        # Determine projection mode
        display_proj = getattr(self.main_ui, '_current_display_projection', 'full_disk')
        try:
            self.main_ui.log(
                "[DIAG-grid] display_proj=%s qimage=%s trans_none=%s half=%.3f res=%.3f ol_w=%s ol_h=%s crs=%s"
                % (
                    display_proj, use_qimage, transformer is None,
                    float(disk_half_extent) if disk_half_extent else -1.0,
                    float(native_res_m) if native_res_m else -1.0,
                    getattr(self.main_ui, '_ol_overlay_w', None),
                    getattr(self.main_ui, '_ol_overlay_h', None),
                    self.main_ui.current_crs.to_proj4() if getattr(self.main_ui, 'current_crs', None) else 'None',
                ))
        except Exception:
            pass
        if display_proj not in ("equirectangular", "plate_carree"):
            if transformer is None or getattr(self.main_ui, 'current_crs', None) is None:
                try:
                    self.main_ui.log(
                        "[OVERLAY] Grid skipped: geos display requires a CRS/transformer "
                        "(refusing to fall back to a straight lat/lon grid)."
                    )
                except Exception:
                    pass
                self.main_ui._last_grid_cache_key = key
                return True
        if use_qimage:
            # Render to QImage for better performance
            grid_img = QImage(overlay_w, overlay_h, QImage.Format_ARGB32)
            grid_img.fill(Qt.transparent)
            painter = QPainter(grid_img)
            painter.setPen(pen)
            painter.setRenderHint(QPainter.Antialiasing, False)
            
            if display_proj == "equirectangular":
                # Equirectangular projection - draw straight grid lines in lat/lon coordinates
                self.main_ui._draw_equirectangular_grid(painter, overlay_w, overlay_h, grid_spacing, sub_step)
            elif display_proj == "plate_carree":
                # Plate Carree - draw straight lat/lon grid
                self.main_ui._draw_plate_carree_grid(painter, overlay_w, overlay_h, grid_spacing, sub_step)
            else:
                # Full Disk (geostationary) - original curved grid
                self.main_ui._draw_geostationary_grid(painter, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer, grid_spacing, sub_step)
            
            painter.end()
            pixmap = QPixmap.fromImage(grid_img)
            cache_dict[key] = pixmap
            
            if self.main_ui._grid_item is None:
                self.main_ui._grid_item = QGraphicsPixmapItem(pixmap)
                self.main_ui._grid_item.setZValue(20)
                scene.addItem(self.main_ui._grid_item)
            else:
                self.main_ui._grid_item.setPixmap(pixmap)
            self.main_ui._grid_item.setVisible(True)
        else:
            # Render to QPainterPath for quality (scalable)
            grid_path = QPainterPath()
            
            if display_proj == "equirectangular":
                self.main_ui._build_equirectangular_grid_path(grid_path, overlay_w, overlay_h, grid_spacing, sub_step)
            elif display_proj == "plate_carree":
                self.main_ui._build_plate_carree_grid_path(grid_path, overlay_w, overlay_h, grid_spacing, sub_step)
            else:
                self.main_ui._build_geostationary_grid_path(grid_path, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer, grid_spacing, sub_step)

            cache_dict[key] = grid_path
            self.main_ui._last_grid_cache_key = key
            if self.main_ui._grid_item is None:
                self.main_ui._grid_item = QGraphicsPathItem()
                self.main_ui._grid_item.setZValue(20)
                scene.addItem(self.main_ui._grid_item)
            self.main_ui._grid_item.setPath(grid_path)
            self.main_ui._grid_item.setPen(pen)
            self.main_ui._grid_item.setVisible(True)
        return True


    def _render_coast_cached(self, scene, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer):
        """Render coastlines the SIFT way.

        SIFT (uwsift/view/visuals.py ShapefileLinesVisual + transform.py
        PROJ4Transform) uploads the borders once as a static GPU vertex buffer
        and re-projects it every frame in the vertex shader, so pan/zoom never
        touches the CPU. In Qt the closest equivalent is: project the borders
        once (vectorized) and either

        * "performance" (default): bake them, WITHOUT antialiasing, into one
          transparent ARGB overlay shown as a pixmap. Pan/zoom frames then only
          GPU-blit a texture -- no per-frame software path stroking. Note that
          the machine's rasterizer is pathologically slow with AA (~1.5 s for a
          20 k-segment path) versus <50 ms without it, so AA is disabled here.
        * "quality": keep the crisp vector QGraphicsPathItem.
        """
        key = self.main_ui._coast_cache_key()
        if key is None:
            return False

        # If an interaction snapshot is active, any rebuild here must first drop
        # the swap item or the vector item and the pixmap would be drawn on top
        # of each other.
        swap = getattr(self.main_ui, '_coast_swap_item', None)
        if swap is not None:
            try:
                if swap.scene():
                    scene.removeItem(swap)
            except Exception:
                pass
            self.main_ui._coast_swap_item = None

        render_mode = self.main_ui.settings.get("grid_coast_render_mode", "performance")
        use_qimage = (render_mode == "performance")

        cache_attr = '_coast_path_cache'
        if not hasattr(self, cache_attr):
            setattr(self, cache_attr, {})
        path_cache = getattr(self, cache_attr)

        img_cache = None
        if use_qimage:
            img_cache_attr = '_coast_image_cache'
            if not hasattr(self, img_cache_attr):
                setattr(self, img_cache_attr, {})
            img_cache = getattr(self, img_cache_attr)
            if key in img_cache:
                self._apply_coast_pixmap(img_cache[key])
                self.main_ui._last_coast_cache_key = key
                return True

        if not self.main_ui.coast_enabled:
            if getattr(self.main_ui, '_coast_item', None) is not None:
                self.main_ui._coast_item.setVisible(False)
            if getattr(self.main_ui, '_coast_pix_item', None) is not None:
                self.main_ui._coast_pix_item.setVisible(False)
            self.main_ui._last_coast_cache_key = key
            return True

        coastline_shp = self._coast_shapefile()
        if not coastline_shp.exists():
            self.main_ui._last_coast_cache_key = key
            return True
        if not self.main_ui._coast_raw_segments:
            self._coast_np_segments()
            if not self.main_ui._coast_raw_segments:
                self.main_ui._last_coast_cache_key = key
                return True

        # Build (or reuse) the projected coastline path once.
        if key in path_cache:
            coast_path = path_cache[key]
        else:
            coast_path = QPainterPath()
            display_proj = getattr(self.main_ui, '_current_display_projection', 'full_disk')
            try:
                self.main_ui.log(
                    "[DIAG-coast] display_proj=%s trans_none=%s half=%.3f res=%.3f crs=%s"
                    % (
                        display_proj, transformer is None,
                        float(disk_half_extent) if disk_half_extent else -1.0,
                        float(native_res_m) if native_res_m else -1.0,
                        self.main_ui.current_crs.to_proj4() if getattr(self.main_ui, 'current_crs', None) else 'None',
                    ))
            except Exception:
                pass
            if display_proj not in ("equirectangular", "plate_carree"):
                if transformer is None or getattr(self.main_ui, 'current_crs', None) is None:
                    try:
                        self.main_ui.log(
                            "[OVERLAY] Coast skipped: geos display requires a CRS/transformer "
                            "(refusing to fall back to a straight lat/lon coastline)."
                        )
                    except Exception:
                        pass
                    self.main_ui._last_coast_cache_key = key
                    return True
            if display_proj == "equirectangular":
                self.main_ui._build_equirectangular_coast_path(coast_path, overlay_w, overlay_h)
            elif display_proj == "plate_carree":
                self.main_ui._build_plate_carree_coast_path(coast_path, overlay_w, overlay_h)
            else:
                # Full Disk (geostationary) - project the country-boundary
                # segments onto the disk, splitting each into visible runs.
                self.main_ui._build_geostationary_coast_path(
                    coast_path, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer
                )
            path_cache[key] = coast_path

        cc = QColor(self.main_ui.settings.get("coast_color", "#FFFFFF"))
        cc.setAlpha(self.main_ui.settings.get("coast_opacity", 200))
        pen = QPen(cc)
        pen.setWidth(self.main_ui.settings.get("coast_line_width", 2))
        _coast_style_map = {
            "solid": Qt.SolidLine, "dotted": Qt.DotLine,
            "dashed": Qt.DashLine, "dashdot": Qt.DashDotLine,
            "crosshatch": Qt.DashDotDotLine,
        }
        pen.setStyle(_coast_style_map.get(self.main_ui.settings.get("coast_pattern", "solid"), Qt.SolidLine))

        if use_qimage:
            pix = self._bake_coast_image(coast_path, pen, overlay_w, overlay_h)
            img_cache[key] = pix
            # Keep memory bounded: only ever retain the current bake.
            for k in [k for k in img_cache if k != key]:
                del img_cache[k]
            self._apply_coast_pixmap(pix)
            self.main_ui._last_coast_cache_key = key
            return True

        # Quality mode: scalable vector path.
        if getattr(self.main_ui, '_coast_item', None) is None:
            self.main_ui._coast_item = QGraphicsPathItem()
            self.main_ui._coast_item.setZValue(21)
            scene.addItem(self.main_ui._coast_item)
        self.main_ui._coast_item.setPath(coast_path)
        self.main_ui._coast_item.setPen(pen)
        self.main_ui._coast_item.setVisible(True)
        if getattr(self.main_ui, '_coast_pix_item', None) is not None:
            self.main_ui._coast_pix_item.setVisible(False)
        self.main_ui._last_coast_cache_key = key
        return True

    def _bake_coast_image(self, coast_path, pen, overlay_w, overlay_h):
        """Rasterize the projected coast path into a transparent ARGB overlay.

        Antialiasing is intentionally OFF: this machine's software rasterizer
        needs ~1.5s to stroke a 20k-segment path with AA but only ~40ms without
        it, and the result is shown as a pixmap that the view composites per
        frame. Returns ``(pixmap, uniform_scale)`` where scale maps the baked
        pixels back onto full overlay size.
        """
        w = max(1, int(overlay_w))
        h = max(1, int(overlay_h))
        cap = int(self.main_ui.settings.get("coast_bake_max_px", 5500))
        mx = max(w, h)
        if mx > cap:
            s = cap / float(mx)
            w = max(1, int(w * s))
            h = max(1, int(h * s))
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, False)
        if w != int(overlay_w) or h != int(overlay_h):
            p.scale(w / float(overlay_w), h / float(overlay_h))
        p.setPen(pen)
        p.drawPath(coast_path)
        p.end()
        return (QPixmap.fromImage(img), float(overlay_w) / w)

    def _apply_coast_pixmap(self, pix):
        """Show the baked coast overlay as a pixmap item (performance mode)."""
        pixmap, scale = pix
        if getattr(self.main_ui, '_coast_pix_item', None) is None:
            scene = self.main_ui.graphics_view.scene()
            if not scene:
                return
            self.main_ui._coast_pix_item = QGraphicsPixmapItem()
            self.main_ui._coast_pix_item.setZValue(21)
            self.main_ui._coast_pix_item.setTransformationMode(Qt.SmoothTransformation)
            scene.addItem(self.main_ui._coast_pix_item)
        self.main_ui._coast_pix_item.setPixmap(pixmap)
        self.main_ui._coast_pix_item.setPos(0.0, 0.0)
        self.main_ui._coast_pix_item.setScale(scale)
        self.main_ui._coast_pix_item.setVisible(self.main_ui.coast_enabled)
        if getattr(self.main_ui, '_coast_item', None) is not None:
            self.main_ui._coast_item.setVisible(False)

    def _swap_coast_visible_to_pixmap(self, item, zvalue):
        """Render one coast path item into a viewport-sized pixmap snapshot.

        The visible scene band that intersects the overlay is stroked once into a
        transparent ARGB image (~viewport device resolution), and a pixmap item is
        placed over the same scene region so zoom/pan frames only blit a texture
        instead of re-stroking the huge vector path. Returns the swap item or None.
        """
        view = getattr(self.main_ui, 'graphics_view', None)
        if view is None or not view.scene():
            return None
        if item is None or not item.scene() or not item.isVisible():
            return None
        path = item.path()
        if path is None or path.isEmpty():
            return None

        ow = float(getattr(self.main_ui, '_ol_overlay_w', 0) or item.boundingRect().width())
        oh = float(getattr(self.main_ui, '_ol_overlay_h', 0) or item.boundingRect().height())
        if ow <= 0 or oh <= 0:
            return None
        overlay_rect = QRectF(0.0, 0.0, ow, oh)

        vr = view.mapToScene(view.viewport().rect()).boundingRect()
        pad_x = max(16.0, vr.width() * 0.35)
        pad_y = max(16.0, vr.height() * 0.35)
        vr = vr.adjusted(-pad_x, -pad_y, pad_x, pad_y)
        clip = vr.intersected(overlay_rect)
        if clip.width() < 2.0 or clip.height() < 2.0:
            return None

        scale = abs(view.transform().m11()) or 1.0
        dw = max(1, min(int(clip.width() * scale * 1.2), 4096))
        dh = max(1, min(int(clip.height() * scale * 1.2), 4096))
        if dw < 2 or dh < 2:
            return None
        if dw > 4096 or dh > 4096:
            r = min(4096.0 / dw, 4096.0 / dh)
            dw = max(2, int(dw * r))
            dh = max(2, int(dh * r))

        img = QImage(dw, dh, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        # No antialiasing: this machine's rasterizer needs ~1.5s per 20k-segment
        # path with AA vs <50ms without it, and this is a transient interaction
        # snapshot anyway.
        p.setRenderHint(QPainter.Antialiasing, False)
        sx = dw / clip.width()
        sy = dh / clip.height()
        p.scale(sx, sy)
        p.translate(-clip.left() * sx, -clip.top() * sy)
        p.setPen(item.pen())
        p.drawPath(path)
        p.end()

        swap = QGraphicsPixmapItem(QPixmap.fromImage(img))
        swap.setPos(clip.left(), clip.top())
        swap.setScale(clip.width() / dw)
        swap.setZValue(zvalue)
        swap.setVisible(True)
        item.scene().addItem(swap)
        return swap

    def _show_item_visible(self, item, visible):
        if item is not None and item.scene():
            item.setVisible(visible)

    def interaction_coast_swap_begin(self):
        """Replace the vector coast item with a pixmap snapshot for smooth interaction.

        Called when wheel-zoom or drag-pan starts (from the graphics view). While
        the view transform changes each frame, Qt only blits this texture instead of
        re-stroking the (very large) vector coastline path in software.
        """
        try:
            if not getattr(self.main_ui, 'coast_enabled', False):
                return
            # Performance mode already shows a persistent baked pixmap, which the
            # view composites per frame -- no per-interaction snapshot needed.
            if self.main_ui.settings.get("grid_coast_render_mode", "performance") == "performance":
                return
            if getattr(self.main_ui, '_coast_swap_item', None) is not None:
                return
            item = getattr(self.main_ui, '_coast_item', None)
            swap = self._swap_coast_visible_to_pixmap(item, zvalue=21)
            if swap is not None:
                self.main_ui._coast_swap_item = swap
                self._show_item_visible(item, False)
                self.main_ui.graphics_view.viewport().update()
        except Exception:
            pass

    def interaction_coast_swap_end(self):
        """Restore the crisp vector coast item after the interaction stops."""
        try:
            swap = getattr(self.main_ui, '_coast_swap_item', None)
            if swap is not None:
                try:
                    sc = swap.scene()
                    if sc:
                        sc.removeItem(swap)
                except Exception:
                    pass
                self.main_ui._coast_swap_item = None
            item = getattr(self.main_ui, '_coast_item', None)
            self._show_item_visible(item, getattr(self.main_ui, 'coast_enabled', False))
            view = self.main_ui.graphics_view
            if view is not None:
                view.viewport().update()
        except Exception:
            pass


    def _draw_equirectangular_grid(self, painter, overlay_w, overlay_h, grid_spacing_deg, sub_step):
        """Draw straight Equirectangular grid lines using actual display extent.
        Same as Plate Carree - straight lat/lon lines on a rectangular grid.
        """
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        lon_min, lat_min, lon_max, lat_max = extent
        scale_x = overlay_w / (lon_max - lon_min) if (lon_max - lon_min) > 0 else 1
        scale_y = overlay_h / (lat_max - lat_min) if (lat_max - lat_min) > 0 else 1
        
        # Longitude lines (vertical)
        if lon_min <= lon_max:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                painter.drawLine(int(px), 0, int(px), overlay_h)
        else:
            # Wrap-around (e.g., 60°E to 140°W)
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, 181, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                painter.drawLine(int(px), 0, int(px), overlay_h)
            lon_start = int(math.ceil(-180.0 / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                painter.drawLine(int(px), 0, int(px), overlay_h)
        
        # Latitude lines (horizontal)
        lat_start = int(math.ceil(lat_min / grid_spacing_deg)) * grid_spacing_deg
        lat_end = int(math.floor(lat_max / grid_spacing_deg)) * grid_spacing_deg
        for lat in range(lat_start, lat_end + 1, grid_spacing_deg):
            py = (lat_max - lat) * scale_y
            painter.drawLine(0, int(py), overlay_w, int(py))
    
    def _draw_mercator_grid(self, painter, overlay_w, overlay_h, grid_spacing_deg, sub_step):
        """Legacy method - redirects to equirectangular grid."""
        self._draw_equirectangular_grid(painter, overlay_w, overlay_h, grid_spacing_deg, sub_step)
    
    def _draw_plate_carree_grid(self, painter, overlay_w, overlay_h, grid_spacing_deg, sub_step):
        """Draw straight Plate Carree grid lines using actual display extent."""
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        lon_min, lat_min, lon_max, lat_max = extent
        scale_x = overlay_w / (lon_max - lon_min) if (lon_max - lon_min) > 0 else 1
        scale_y = overlay_h / (lat_max - lat_min) if (lat_max - lat_min) > 0 else 1
        
        # Longitude lines (vertical)
        if lon_min <= lon_max:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                painter.drawLine(int(px), 0, int(px), overlay_h)
        else:
            # Wrap-around (e.g., 60°E to 140°W)
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, 181, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                painter.drawLine(int(px), 0, int(px), overlay_h)
            lon_start = int(math.ceil(-180.0 / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                painter.drawLine(int(px), 0, int(px), overlay_h)
        
        # Latitude lines (horizontal)
        lat_start = int(math.ceil(lat_min / grid_spacing_deg)) * grid_spacing_deg
        lat_end = int(math.floor(lat_max / grid_spacing_deg)) * grid_spacing_deg
        for lat in range(lat_start, lat_end + 1, grid_spacing_deg):
            py = (lat_max - lat) * scale_y
            painter.drawLine(0, int(py), overlay_w, int(py))
    
    
    def _draw_geostationary_grid(self, painter, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer, grid_spacing_deg, sub_step):
        """Draw curved geostationary grid lines.

        Vectorized: each lat/lon line is projected in bulk, split into visible
        runs, and decimated so the painter only receives ~one point per pixel.
        """
        def _draw_grid_line(lons, lats):
            runs = self._project_geos_visible_runs(
                lons, lats, transformer, disk_half_extent, native_res_m,
                overlay_w, overlay_h, self.main_ui.satellite_lon,
            )
            for run in runs:
                run = decimate_screen_points(run, min_step_px=1.0)
                if len(run) < 2:
                    continue
                poly = QPolygonF()
                for p in run:
                    poly.append(QPointF(p[0], p[1]))
                painter.drawPolyline(poly)

        for lon_val in range(-180, 181, grid_spacing_deg):
            lats = np.arange(-90, 90.1, sub_step)
            lons = np.full_like(lats, lon_val)
            _draw_grid_line(lons, lats)
        for lat_val in range(-90, 91, grid_spacing_deg):
            lons = np.arange(-180, 180.1, sub_step)
            lats = np.full_like(lons, lat_val)
            _draw_grid_line(lons, lats)
    
    
    def _build_mercator_grid_path(self, grid_path, overlay_w, overlay_h, grid_spacing_deg, sub_step):
        """Build QPainterPath for Mercator grid using actual display extent."""
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        ext_min_x, ext_min_y, ext_max_x, ext_max_y = extent
        scale_x = overlay_w / (ext_max_x - ext_min_x) if (ext_max_x - ext_min_x) > 0 else 1
        scale_y = overlay_h / (ext_max_y - ext_min_y) if (ext_max_y - ext_min_y) > 0 else 1
        
        try:
            from pyproj import Transformer as _T
            latlon_to_merc = _T.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
            merc_to_latlon = _T.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
        except Exception:
            return
        
        lon_min, lat_min = merc_to_latlon.transform(ext_min_x, ext_min_y)
        lon_max, lat_max = merc_to_latlon.transform(ext_max_x, ext_max_y)
        
        # Longitude lines (vertical)
        if lon_min <= lon_max:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                mx, _ = latlon_to_merc.transform(lon, 0)
                px = (mx - ext_min_x) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
        else:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, 181, grid_spacing_deg):
                mx, _ = latlon_to_merc.transform(lon, 0)
                px = (mx - ext_min_x) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
            lon_start = int(math.ceil(-180.0 / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                mx, _ = latlon_to_merc.transform(lon, 0)
                px = (mx - ext_min_x) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
        
        # Latitude lines (horizontal)
        lat_start = int(math.ceil(lat_min / grid_spacing_deg)) * grid_spacing_deg
        lat_end = int(math.floor(lat_max / grid_spacing_deg)) * grid_spacing_deg
        for lat in range(lat_start, lat_end + 1, grid_spacing_deg):
            _, my = latlon_to_merc.transform(0, lat)
            py = (ext_max_y - my) * scale_y
            grid_path.moveTo(0, int(py))
            grid_path.lineTo(overlay_w, int(py))

    def _build_equirectangular_grid_path(self, grid_path, overlay_w, overlay_h, grid_spacing_deg, sub_step):
        """Build QPainterPath for Equirectangular grid using actual display extent.
        
        Equirectangular is Plate Carree centered on satellite longitude with crop_deg=85.
        """
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        lon_min, lat_min, lon_max, lat_max = extent
        scale_x = overlay_w / (lon_max - lon_min) if (lon_max - lon_min) > 0 else 1
        scale_y = overlay_h / (lat_max - lat_min) if (lat_max - lat_min) > 0 else 1
        
        # Longitude lines (vertical)
        if lon_min <= lon_max:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
        else:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, 181, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
            lon_start = int(math.ceil(-180.0 / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
        
        # Latitude lines (horizontal)
        lat_start = int(math.ceil(lat_min / grid_spacing_deg)) * grid_spacing_deg
        lat_end = int(math.floor(lat_max / grid_spacing_deg)) * grid_spacing_deg
        for lat in range(lat_start, lat_end + 1, grid_spacing_deg):
            py = (lat_max - lat) * scale_y
            grid_path.moveTo(0, int(py))
            grid_path.lineTo(overlay_w, int(py))

    def _build_plate_carree_grid_path(self, grid_path, overlay_w, overlay_h, grid_spacing_deg, sub_step):
        """Build QPainterPath for Plate Carree grid using actual display extent."""
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        lon_min, lat_min, lon_max, lat_max = extent
        scale_x = overlay_w / (lon_max - lon_min) if (lon_max - lon_min) > 0 else 1
        scale_y = overlay_h / (lat_max - lat_min) if (lat_max - lat_min) > 0 else 1
        
        # Longitude lines (vertical)
        if lon_min <= lon_max:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
        else:
            lon_start = int(math.ceil(lon_min / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, 181, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
            lon_start = int(math.ceil(-180.0 / grid_spacing_deg)) * grid_spacing_deg
            lon_end = int(math.floor(lon_max / grid_spacing_deg)) * grid_spacing_deg
            for lon in range(lon_start, lon_end + 1, grid_spacing_deg):
                px = (lon - lon_min) * scale_x
                grid_path.moveTo(int(px), 0)
                grid_path.lineTo(int(px), overlay_h)
        
        # Latitude lines (horizontal)
        lat_start = int(math.ceil(lat_min / grid_spacing_deg)) * grid_spacing_deg
        lat_end = int(math.floor(lat_max / grid_spacing_deg)) * grid_spacing_deg
        for lat in range(lat_start, lat_end + 1, grid_spacing_deg):
            py = (lat_max - lat) * scale_y
            grid_path.moveTo(0, int(py))
            grid_path.lineTo(overlay_w, int(py))
    

    def _build_geostationary_grid_path(self, grid_path, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer, grid_spacing_deg, sub_step):
        """Build QPainterPath for geostationary grid.

        Vectorized: each lat/lon line is projected in bulk and decimated to
        ~one point per pixel before being appended to the path.
        """
        def _process_grid_line(lons, lats):
            runs = self._project_geos_visible_runs(
                lons, lats, transformer, disk_half_extent, native_res_m,
                overlay_w, overlay_h, self.main_ui.satellite_lon,
            )
            self._append_pixel_path(grid_path, runs, min_step_px=1.0)

        for lon_val in range(-180, 181, grid_spacing_deg):
            lats = np.arange(-90, 90.1, sub_step)
            lons = np.full_like(lats, lon_val)
            _process_grid_line(lons, lats)
        for lat_val in range(-90, 91, grid_spacing_deg):
            lons = np.arange(-180, 180.1, sub_step)
            lats = np.full_like(lons, lat_val)
            _process_grid_line(lons, lats)
    

    def _draw_mercator_coastlines(self, painter, overlay_w, overlay_h):
        """Draw coastlines in Mercator projection using actual display extent.
        Vectorized via the shared path builder.
        """
        coast_path = QPainterPath()
        self._build_mercator_coast_path(coast_path, overlay_w, overlay_h)
        if not coast_path.isEmpty():
            painter.drawPath(coast_path)

    def _draw_plate_carree_coastlines(self, painter, overlay_w, overlay_h):
        """Draw coastlines in Plate Carree projection using actual display extent.
        Vectorized via the shared path builder.
        """
        coast_path = QPainterPath()
        self._build_plate_carree_coast_path(coast_path, overlay_w, overlay_h)
        if not coast_path.isEmpty():
            painter.drawPath(coast_path)
    

    def _build_mercator_coast_path(self, coast_path, overlay_w, overlay_h):
        """Build QPainterPath for Mercator coastlines using actual display extent.

        Vectorized: the whole segment is transformed with one numpy call and
        decimated to ~one point per pixel before being appended.
        """
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        ext_min_x, ext_min_y, ext_max_x, ext_max_y = extent
        scale_x = overlay_w / (ext_max_x - ext_min_x) if (ext_max_x - ext_min_x) > 0 else 1
        scale_y = overlay_h / (ext_max_y - ext_min_y) if (ext_max_y - ext_min_y) > 0 else 1

        try:
            from pyproj import Transformer as _T
            latlon_to_merc = _T.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        except Exception:
            return

        runs = []
        for segment in self.main_ui._coast_raw_segments:
            lons = segment[:, 0]
            lats = segment[:, 1]
            sel = (lats >= -85) & (lats <= 85)
            idx = np.where(sel)[0]
            if len(idx) < 2:
                continue
            for chunk in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                if len(chunk) < 2:
                    continue
                with np.errstate(invalid='ignore'):
                    mx, my = latlon_to_merc.transform(lons[chunk], lats[chunk])
                px = (mx - ext_min_x) * scale_x
                py = (ext_max_y - my) * scale_y
                inb = (np.isfinite(px) & np.isfinite(py)
                       & (px >= 0) & (px <= overlay_w) & (py >= 0) & (py <= overlay_h))
                ix = np.where(inb)[0]
                if len(ix) < 2:
                    continue
                for sub in np.split(ix, np.where(np.diff(ix) != 1)[0] + 1):
                    if len(sub) >= 2:
                        runs.append(np.column_stack([px[sub], py[sub]]))
        if runs:
            self._append_pixel_path(coast_path, runs, min_step_px=1.0)


    def _build_plate_carree_coast_path(self, coast_path, overlay_w, overlay_h):
        """Build QPainterPath for Plate Carree coastlines using actual display extent."""
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        ext_min_x, ext_min_y, ext_max_x, ext_max_y = extent
        scale_x = overlay_w / (ext_max_x - ext_min_x) if (ext_max_x - ext_min_x) > 0 else 1
        scale_y = overlay_h / (ext_max_y - ext_min_y) if (ext_max_y - ext_min_y) > 0 else 1

        runs = []
        for segment in self.main_ui._coast_raw_segments:
            px = (segment[:, 0] - ext_min_x) * scale_x
            py = (ext_max_y - segment[:, 1]) * scale_y
            inb = (px >= 0) & (px <= overlay_w) & (py >= 0) & (py <= overlay_h)
            idx = np.where(inb)[0]
            if len(idx) < 2:
                continue
            for chunk in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                if len(chunk) >= 2:
                    runs.append(np.column_stack([px[chunk], py[chunk]]))
        if runs:
            self._append_pixel_path(coast_path, runs, min_step_px=1.0)
    
    def _draw_equirectangular_coastlines(self, painter, overlay_w, overlay_h):
        """Draw coastlines in Equirectangular projection using actual display extent.
        Same as Plate Carree - straight lat/lon lines. Vectorized via the shared
        path builder.
        """
        coast_path = QPainterPath()
        self._build_equirectangular_coast_path(coast_path, overlay_w, overlay_h)
        if not coast_path.isEmpty():
            painter.drawPath(coast_path)
    
    def _build_equirectangular_coast_path(self, coast_path, overlay_w, overlay_h):
        """Build QPainterPath for Equirectangular coastlines using actual display extent.
        Equirectangular (eqc) uses metric coordinates - convert lat/lon to meters.
        Vectorized with per-segment numpy runs + decimation.
        """
        extent = getattr(self.main_ui, '_display_projection_extent', None)
        if extent is None:
            return
        ext_min_x, ext_min_y, ext_max_x, ext_max_y = extent
        scale_x = overlay_w / (ext_max_x - ext_min_x) if (ext_max_x - ext_min_x) > 0 else 1
        scale_y = overlay_h / (ext_max_y - ext_min_y) if (ext_max_y - ext_min_y) > 0 else 1

        eq_params = getattr(self.main_ui, '_equirectangular_params', None)
        if eq_params is None:
            return
        sat_lon = eq_params.get('sat_lon', 140.7)
        R = eq_params.get('R', 6378137.0)
        deg2rad = eq_params.get('deg2rad', 3.141592653589793 / 180.0)

        runs = []
        for segment in self.main_ui._coast_raw_segments:
            x = (segment[:, 0] - sat_lon) * R * deg2rad
            y = segment[:, 1] * R * deg2rad
            px = (x - ext_min_x) * scale_x
            py = (ext_max_y - y) * scale_y
            inb = (px >= 0) & (px <= overlay_w) & (py >= 0) & (py <= overlay_h)
            idx = np.where(inb)[0]
            if len(idx) < 2:
                continue
            for chunk in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                if len(chunk) >= 2:
                    runs.append(np.column_stack([px[chunk], py[chunk]]))
        if runs:
            self._append_pixel_path(coast_path, runs, min_step_px=1.0)
    
    def _build_geostationary_coast_path(self, coast_path, overlay_w, overlay_h, disk_half_extent, native_res_m, transformer):
        """Build QPainterPath for geostationary coastlines.

        Vectorized SIFT-style projection: each lon/lat segment is transformed in
        bulk with numpy, split into visible runs, and sub-pixel points are
        dropped so the resulting path stays small enough for smooth pan/zoom.
        """
        for segment in self.main_ui._coast_raw_segments:
            runs = self._project_geos_visible_runs(
                segment[:, 0], segment[:, 1], transformer,
                disk_half_extent, native_res_m, overlay_w, overlay_h,
                self.main_ui.satellite_lon, clip_lats=89.9,
            )
            if runs:
                self._append_pixel_path(coast_path, runs, min_step_px=1.0)


    def _draw_nhc_overlays(self, project_func, scene):

        """Draw NHC track overlays.

        Renders National Hurricane Center tropical cyclone tracks
        with standard NHC symbology including position markers,
        intensity labels, and forecast cones.

        Args:
            scene (QGraphicsScene): Graphics scene for rendering.
            project_func (callable): Coordinate projection function.

        Side Effects:
            - Adds track items to scene
            - Stores overlay references for cleanup
        """
        if not getattr(self.main_ui, 'nhc_storms', None):
            return
        fc_master = getattr(self.main_ui, 'forecast_enable_cb', None)
        if fc_master is not None and not fc_master.isChecked():
            return
        for sid, sdata in self.main_ui.nhc_storms.items():
            if sdata is None:
                continue
            local_dir = sdata.get("local_dir")
            _track_entry = next((t for t in getattr(self.main_ui, 'tracks', []) if t.get("id") == f"nhc_{sid}"), None)
            if _track_entry is not None and not _track_entry.get("visible", True):
                continue
            _tdopts = _track_entry.get("display_options", {}) if _track_entry else {}

            if _tdopts.get("show_cone", True) and (not getattr(self, 'nhc_show_cone_cb', None) or self.main_ui.nhc_show_cone_cb.isChecked()):
                cone_polys = sdata.get("cone_polygons", [])
                if not cone_polys and local_dir:
                    kmz = sdata.get("kmz_cone")
                    if kmz and Path(kmz).exists():
                        cone_polys = nhc.NHCDownloader._parse_kmz_cone_polygon(kmz)
                        sdata["cone_polygons"] = cone_polys
                for poly in cone_polys:
                    qpoly = QPainterPath()
                    first = True
                    for lon, lat in poly:
                        gx, gy = project_func(lon, lat)
                        if gx is None:
                            continue
                        if first:
                            qpoly.moveTo(gx, gy)
                            first = False
                        else:
                            qpoly.lineTo(gx, gy)
                    if not qpoly.isEmpty():
                        qpoly.closeSubpath()
                        item = QGraphicsPathItem(qpoly)
                        cone_pen = QPen(QColor(0, 150, 255, 120))
                        cone_pen.setWidth(1)
                        cone_brush = QColor(0, 100, 255, 30)
                        item.setPen(cone_pen)
                        item.setBrush(cone_brush)
                        item.setZValue(22)
                        scene.addItem(item)
                        self.main_ui._nhc_overlay_items.append(item)

            track_pts = sdata.get("track_points", [])
            if not track_pts and local_dir:
                kmz = sdata.get("kmz_track")
                if kmz and Path(kmz).exists():
                    track_pts = nhc.NHCDownloader._parse_kmz_track_points(kmz)
                    sdata["track_points"] = track_pts
            show_trackline = _tdopts.get("show_track_line", True) and (getattr(self, 'forecast_show_trackline_cb', None) is None or self.main_ui.forecast_show_trackline_cb.isChecked())
            show_points = _tdopts.get("show_points", True) and (getattr(self, 'forecast_show_points_cb', None) is None or self.main_ui.forecast_show_points_cb.isChecked())
            track_qpath = QPainterPath()
            if len(track_pts) >= 2:
                first = True
                for p in track_pts:
                    gx, gy = project_func(p["lon"], p["lat"])
                    if gx is None:
                        continue
                    if first:
                        track_qpath.moveTo(gx, gy)
                        first = False
                    else:
                        track_qpath.lineTo(gx, gy)
                if not track_qpath.isEmpty() and show_trackline:
                    item = QGraphicsPathItem(track_qpath)
                    track_pen = QPen(QColor(255, 200, 50, 200))
                    track_pen.setWidth(2)
                    item.setPen(track_pen)
                    item.setBrush(Qt.NoBrush)
                    item.setZValue(22)
                    scene.addItem(item)
                    self.main_ui._nhc_overlay_items.append(item)
            if show_points:
                _nhc_proj = []
                for p in track_pts:
                    gx, gy = project_func(p["lon"], p["lat"])
                    if gx is not None:
                        _nhc_proj.append(QPointF(gx, gy))
                _nhc_algo = self.main_ui.settings.get("labeling_method", "polar")
                if _nhc_algo in ("anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "auto",
                                  "railway_bezier", "8direction", "staggered_perp") and _HAS_NEW_ALGOS:
                    _nhc_offsets = OverlayController._compute_label_offsets_new(track_pts, _nhc_algo)
                else:
                    _nhc_offsets = self.main_ui._compute_label_offsets(_nhc_proj, _nhc_algo) if len(_nhc_proj) >= 2 else None
                if _nhc_offsets and len(_nhc_proj) >= 2:
                    _nhc_cone_r = []
                    _nhc_idx = 0
                    for p in track_pts:
                        gx, gy = project_func(p["lon"], p["lat"])
                        if gx is None:
                            continue
                        gxe, gye = project_func(p["lon"] + 0.01, p["lat"])
                        if gxe is not None:
                            scale = math.hypot(gxe - gx, gye - gy) / (0.01 * 111320.0)
                            hrs = p.get("advanced_hours", 0)
                            r_km = 30 + hrs * 2.0
                            _nhc_cone_r.append(r_km * 1000.0 * scale)
                        else:
                            _nhc_cone_r.append(0)
                        _nhc_idx += 1
                    _nhc_offsets = OverlayController._push_offsets_outside_cone(_nhc_offsets, _nhc_proj, _nhc_cone_r)
                _nhc_wu = self.main_ui.settings.get("forecast_preferences", {}).get("wind_format", "kt")
                _nhc_show_labels = _tdopts.get("show_labels", True)
                _nhc_show_time = _tdopts.get("show_label_time", True)
                _nhc_show_speed = _tdopts.get("show_label_speed", True)
                _nhc_show_name = _tdopts.get("show_label_name", True)
                _nhc_pi = 0
                for p in track_pts:
                    gx, gy = project_func(p["lon"], p["lat"])
                    if gx is None:
                        continue
                    intensity = p.get("intensity")
                    if intensity is not None:
                        mc = QColor(255, 50, 50) if intensity >= 64 else QColor(255, 140, 0) if intensity >= 34 else QColor(255, 220, 100) if intensity > 0 else QColor(255, 100, 100)
                        r = 4.5 if intensity >= 64 else 3.5 if intensity >= 34 else 3
                    else:
                        mc = QColor(200, 200, 200); r = 3
                    dot = QGraphicsEllipseItem(gx - r, gy - r, r * 2, r * 2)
                    dot.setPen(QPen(mc.darker(120), 1))
                    dot.setBrush(QBrush(mc))
                    dot.setZValue(23)
                    scene.addItem(dot)
                    self.main_ui._nhc_overlay_items.append(dot)
                    # Hurricane category icon using pagcat symbols
                    if intensity is not None and intensity >= 64:
                        pt_cat = p.get("intensity_category", "")
                        sym_name = self.main_ui._category_to_pag_sym(pt_cat, intensity)
                        if sym_name:
                            from src.core.helpers import top_dir as _nhc_td
                            _nhc_sp = _nhc_td / "public" / "images" / "symbols" / f"{sym_name}.png"
                            if _nhc_sp.exists():
                                if not hasattr(self.main_ui, '_sym_cache'):
                                    self.main_ui._sym_cache = {}
                                if _nhc_sp not in self.main_ui._sym_cache:
                                    self.main_ui._sym_cache[_nhc_sp] = QPixmap(str(_nhc_sp))
                                _nhc_pix = self.main_ui._sym_cache.get(_nhc_sp)
                                if _nhc_pix and not _nhc_pix.isNull():
                                    _nhc_sz = 24
                                    _nhc_ip = QGraphicsPixmapItem(_nhc_pix)
                                    _nhc_ip.setScale(_nhc_sz / max(_nhc_pix.width(), _nhc_pix.height()))
                                    _nhc_ip.setPos(gx - _nhc_sz / 2, gy - _nhc_sz / 2)
                                    _nhc_ip.setZValue(26)
                                    scene.addItem(_nhc_ip)
                                    self.main_ui._nhc_overlay_items.append(_nhc_ip)
                    # Label
                    if _nhc_show_labels and _nhc_offsets and _nhc_pi < len(_nhc_offsets):
                        _nhc_label_parts = []
                        if _nhc_show_time:
                            ndt = p.get("datetime", "")
                            if ndt:
                                try:
                                    ndt_obj = datetime.strptime(ndt, "%Y-%m-%d %H:%M")
                                    _nhc_label_parts.append(ndt_obj.strftime("%H:%M"))
                                except Exception:
                                    _nhc_label_parts.append(ndt.split(" ")[-1] if " " in ndt else ndt)
                        if _nhc_show_speed and intensity is not None:
                            nw = float(intensity)
                            if _nhc_wu == "kmh": nw = round(nw * 1.852); _nhc_label_parts.append(f"{nw:.0f} km/h")
                            elif _nhc_wu == "mph": nw = round(nw * 1.151); _nhc_label_parts.append(f"{nw:.0f} mph")
                            elif _nhc_wu == "ms": nw = round(nw * 0.514); _nhc_label_parts.append(f"{nw:.0f} m/s")
                            else: _nhc_label_parts.append(f"{nw:.0f} kt")
                        if _nhc_label_parts:
                            _nl = QGraphicsTextItem(" ".join(_nhc_label_parts))
                            _nl.setDefaultTextColor(mc)
                            _nl.setFont(QFont("Segoe UI", 6, QFont.Normal))
                            ox, oy, ha = _nhc_offsets[_nhc_pi]
                            lx = gx + ox
                            if ha == 'right':
                                lx -= _nl.boundingRect().width()
                            _nl.setPos(lx, gy + oy)
                            _nl.setZValue(24)
                            scene.addItem(_nl)
                            self.main_ui._nhc_overlay_items.append(_nl)
                    _nhc_pi += 1
                # System name label at first valid point
                if _nhc_show_name and _nhc_proj and _nhc_offsets and _nhc_offsets[0]:
                    _nhc_name = sdata.get("storm_name", "")
                    if _nhc_name:
                        _nn = QGraphicsTextItem(_nhc_name[:24])
                        _nn.setDefaultTextColor(QColor(255, 200, 50))
                        _nn.setFont(QFont("Segoe UI", 7, QFont.Normal))
                        ox, oy, ha = _nhc_offsets[0]
                        lx = _nhc_proj[0].x() + ox * 1.5
                        ly = _nhc_proj[0].y() + oy - 14
                        if ha == 'right':
                            lx -= _nn.boundingRect().width()
                        _nn.setPos(lx, ly)
                        _nn.setZValue(24)
                        scene.addItem(_nn)
                        self.main_ui._nhc_overlay_items.append(_nn)

            if _tdopts.get("show_wind_radii", True) and getattr(self, 'nhc_show_wind_cb', None) and self.main_ui.nhc_show_wind_cb.isChecked():
                for radii_key, kw_key, label_prefix in [("wind_radii_initial","kmz_wind_initial","Init"), ("wind_radii_forecast","kmz_wind_forecast","Fcst")]:
                    radii_data = sdata.get(radii_key, {})
                    if not any(radii_data.get(k) for k in ("34","50","64")) and local_dir:
                        kmz = sdata.get(kw_key)
                        if kmz and Path(kmz).exists():
                            radii_data = nhc.NHCDownloader._parse_kmz_wind_radii(kmz)
                            sdata[radii_key] = radii_data
                    for kt_key, color in [("34", QColor(0, 200, 0, 80)), ("50", QColor(255, 165, 0, 80)), ("64", QColor(255, 50, 50, 80))]:
                        polys = radii_data.get(kt_key, [])
                        for poly in polys:
                            qpoly = QPainterPath()
                            first = True
                            for lon, lat in poly:
                                gx, gy = project_func(lon, lat)
                                if gx is None:
                                    continue
                                if first:
                                    qpoly.moveTo(gx, gy)
                                    first = False
                                else:
                                    qpoly.lineTo(gx, gy)
                            if not qpoly.isEmpty():
                                qpoly.closeSubpath()
                                item = QGraphicsPathItem(qpoly)
                                p = QPen(color.lighter(130), 1)
                                item.setPen(p)
                                item.setBrush(color)
                                item.setZValue(22)
                                scene.addItem(item)
                                self.main_ui._nhc_overlay_items.append(item)

            if _tdopts.get("show_best_track", True) and getattr(self, 'nhc_show_besttrack_cb', None) and self.main_ui.nhc_show_besttrack_cb.isChecked():
                bt_pts = sdata.get("best_track_points", [])
                bt_line = sdata.get("best_track_line", [])
                if not bt_pts and not bt_line and local_dir:
                    kmz = sdata.get("best_track_kmz")
                    if kmz and Path(kmz).exists():
                        bt_pts, bt_line = nhc.NHCDownloader._parse_kmz_best_track(kmz)
                        sdata["best_track_points"] = bt_pts
                        sdata["best_track_line"] = bt_line
                if bt_line:
                    for seg in bt_line:
                        qpath = QPainterPath()
                        first = True
                        for lon, lat in seg:
                            gx, gy = project_func(lon, lat)
                            if gx is None:
                                continue
                            if first:
                                qpath.moveTo(gx, gy); first = False
                            else:
                                qpath.lineTo(gx, gy)
                        if not qpath.isEmpty():
                            item = QGraphicsPathItem(qpath)
                            item.setPen(QPen(QColor(180, 0, 255, 180), 2, Qt.DashLine))
                            item.setBrush(Qt.NoBrush)
                            item.setZValue(22)
                            scene.addItem(item)
                            self.main_ui._nhc_overlay_items.append(item)
                if bt_pts:
                    for p in bt_pts:
                        gx, gy = project_func(p["lon"], p["lat"])
                        if gx is None:
                            continue
                        mc = QColor(180, 0, 255)
                        dot = QGraphicsEllipseItem(gx - 2, gy - 2, 4, 4)
                        dot.setPen(QPen(mc, 1))
                        dot.setBrush(QBrush(mc))
                        dot.setZValue(23)
                        scene.addItem(dot)
                        self.main_ui._nhc_overlay_items.append(dot)


    def update_overlays(self):
        key = self.main_ui._overlay_cache_key()
        if key is not None and key == self.main_ui._last_overlay_key:
            if self.main_ui.settings.get("overlay_mode", "cached") == "realtime":
                self.main_ui._last_overlay_key = None
            else:
                return

        was_playing = getattr(self.main_ui, '_anim_playing', False)
        if not was_playing:
            self.main_ui._remove_all_overlay_items()
        if (hasattr(self.main_ui, '_sataid_current_lat') and self.main_ui._sataid_current_lat is not None
            and hasattr(self.main_ui, '_sataid_current_lon') and self.main_ui._sataid_current_lon is not None
            and hasattr(self.main_ui, '_sataid_current_data') and self.main_ui._sataid_current_data is not None
            and hasattr(self.main_ui, 'type_combo') and self.main_ui.type_combo.currentText() == "SATAID"):
            self.main_ui._update_sataid_overlays()
            return
        import time as _t
        _t0 = _t.perf_counter()
        if not self.main_ui.current_crs or not self.main_ui.current_geotransform:
            if self.main_ui._overlay_geo_unavailable:
                return
            _has_band = any(cb.isChecked() for cb in getattr(self.main_ui, 'band_checkboxes', {}).values())
            if not _has_band and not self.main_ui.selected_product:
                return
            nc_path = self.main_ui._current_nc_file()
            if nc_path:
                crs, transform = self.main_ui.extract_crs_from_ads(nc_path)
                if crs and transform:
                    self.main_ui.current_crs = crs
                    self.main_ui.current_geotransform = transform
                    self.main_ui._overlay_geo_unavailable = False
                    self.main_ui.log("Re\u2011extracted CRS/geotransform for overlays.")
                else:
                    self.main_ui._overlay_geo_unavailable = True
                    self.main_ui.log("Overlays disabled: missing CRS or geotransform")
                    return
            else:
                self.main_ui._overlay_geo_unavailable = True
                self.main_ui.log("Overlays disabled: missing CRS or geotransform")
                return

        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return

        self.main_ui._track_proj_cache = {}

        items = scene.items()
        pixmap_item = next((i for i in reversed(items) if isinstance(i, QGraphicsPixmapItem)), None)
        if not pixmap_item:
            return
        img_w = pixmap_item.pixmap().width()
        img_h = pixmap_item.pixmap().height()

        transform = self.main_ui.current_geotransform
        crs_proj = self.main_ui.current_crs

        if self.main_ui._overlay_transformer is None or self.main_ui._overlay_crs is not crs_proj:
            self.main_ui._overlay_transformer = Transformer.from_crs("EPSG:4326", crs_proj, always_xy=True)
            self.main_ui._overlay_crs = crs_proj
        transformer = self.main_ui._overlay_transformer

        h_sat = 35785863.0
        if self.main_ui.current_crs:
            try:
                cf = self.main_ui.current_crs.to_cf()
                h_sat = float(cf.get("perspective_point_height", h_sat))
            except Exception:
                pass
        try:
            _cf = self.main_ui.current_crs.to_cf()
            _R = float(_cf.get("semi_major_axis", 6378137.0))
            _h_sat = float(_cf.get("perspective_point_height", h_sat))
        except Exception:
            _R, _h_sat = 6378137.0, h_sat

        display_proj = getattr(self.main_ui, '_current_display_projection', 'full_disk')
        is_rectilinear = display_proj in ("equirectangular", "plate_carree")

        if is_rectilinear:
            overlay_w = img_w
            overlay_h = img_h
            overlay_extent = max(abs(transform.c), abs(transform.c + transform.a * img_w),
                                 abs(transform.f), abs(transform.f + transform.e * img_h))
            effective_res_m = (2 * overlay_extent) / max(img_w, img_h, 1)
            self.main_ui._ol_disk_half_extent = overlay_extent
            self.main_ui._ol_native_res_m = effective_res_m
            self.main_ui._ol_overlay_w = overlay_w
            self.main_ui._ol_overlay_h = overlay_h
        else:
            gq = self.main_ui._get_quality_grid()
            if gq is not None:
                overlay_w, overlay_h, effective_res_m, overlay_extent = gq
                # For HSD/DAT source: image may be at native FLDK resolution
                # (larger than quality grid) — use actual image dimensions so
                # grid/coastline overlay matches the displayed imagery.
                if self.main_ui._is_hsd_source and (img_w > overlay_w or img_h > overlay_h):
                    img_dim = max(img_w, img_h)
                    overlay_extent = max(abs(transform.c), abs(transform.c + transform.a * img_w),
                                         abs(transform.f), abs(transform.f + transform.e * img_h))
                    overlay_w = img_w
                    overlay_h = img_h
                    effective_res_m = (2 * overlay_extent) / img_dim if img_dim > 0 else abs(transform.a)
                    self.main_ui._ol_disk_half_extent = overlay_extent
                    self.main_ui._ol_native_res_m = effective_res_m
                    self.main_ui._ol_overlay_w = overlay_w
                    self.main_ui._ol_overlay_h = overlay_h
                else:
                    self.main_ui._ol_disk_half_extent = overlay_extent
                    self.main_ui._ol_native_res_m = effective_res_m
                    self.main_ui._ol_overlay_w = overlay_w
                    self.main_ui._ol_overlay_h = overlay_h
            else:
                # Full Res: use image-size-based overlay (per-band native size)
                img_dim = max(img_w, img_h)
                overlay_extent = max(abs(transform.c), abs(transform.c + transform.a * img_w),
                                     abs(transform.f), abs(transform.f + transform.e * img_h))
                overlay_w = img_w
                overlay_h = img_h
                effective_res_m = (2 * overlay_extent) / img_dim if img_dim > 0 else abs(transform.a)
                self.main_ui._ol_disk_half_extent = overlay_extent
                self.main_ui._ol_native_res_m = effective_res_m
                self.main_ui._ol_overlay_w = overlay_w
                self.main_ui._ol_overlay_h = overlay_h
        self.main_ui._ol_transformer = transformer
        self.main_ui._ol_geo_sig = self._overlay_geo_signature()

        def project_lonlat_to_pixel(lon, lat):
            if is_rectilinear:
                extent = getattr(self.main_ui, '_display_projection_extent', None)
                if extent is None:
                    return None, None
                lon_min, lat_min, lon_max, lat_max = extent
                w = overlay_w
                h = overlay_h
                lon_range = lon_max - lon_min
                lat_range = lat_max - lat_min
                if lon_range <= 0 or lat_range <= 0:
                    return None, None
                scale_x = w / lon_range
                scale_y = h / lat_range
                if lon < lon_min:
                    lon += 360.0
                col = (lon - lon_min) * scale_x
                row = (lat_max - lat) * scale_y
                if col < 0 or col >= w or row < 0 or row >= h:
                    return None, None
                return col, row
            try:
                lon = normalize_lon(lon)
                x_proj, y_proj = transformer.transform(lon, lat)
                if math.isinf(x_proj) or math.isinf(y_proj) or math.isnan(x_proj) or math.isnan(y_proj):
                    return None, None
                if abs(x_proj) > overlay_extent * 1.01 or abs(y_proj) > overlay_extent * 1.01:
                    return None, None
                col = (x_proj + overlay_extent) / effective_res_m
                row = (overlay_extent - y_proj) / effective_res_m
                return col, row
            except Exception:
                return None, None

        self.main_ui._render_grid_cached(scene, overlay_w, overlay_h, overlay_extent, effective_res_m, transformer)
        self.main_ui._render_coast_cached(scene, overlay_w, overlay_h, overlay_extent, effective_res_m, transformer)

        _nhc_start = _t.perf_counter()
        self.main_ui._draw_nhc_overlays(project_lonlat_to_pixel, scene)
        _nhc_dt = _t.perf_counter() - _nhc_start
        self.main_ui.log(f"[Overlay] NHC overlays: {_nhc_dt*1000:.1f}ms")

        _jma_start = _t.perf_counter()
        self.main_ui._draw_jma_overlays(project_lonlat_to_pixel, scene)
        _jma_dt = _t.perf_counter() - _jma_start
        self.main_ui.log(f"[Overlay] JMA overlays: {_jma_dt*1000:.1f}ms")

        _jtwc_start = _t.perf_counter()
        self.main_ui._draw_jtwc_overlays(project_lonlat_to_pixel, scene)
        _jtwc_dt = _t.perf_counter() - _jtwc_start
        self.main_ui.log(f"[Overlay] JTWC overlays: {_jtwc_dt*1000:.1f}ms")

        _pagasa_start = _t.perf_counter()
        self.main_ui._draw_pagasa_overlays(project_lonlat_to_pixel, scene)
        _pagasa_dt = _t.perf_counter() - _pagasa_start
        self.main_ui.log(f"[Overlay] PAGASA overlays: {_pagasa_dt*1000:.1f}ms")
        # ATCF overlays disabled
        # _atcf_start = _t.perf_counter()
        # self.main_ui._draw_atcf_overlays(project_lonlat_to_pixel, scene)
        # _atcf_dt = _t.perf_counter() - _atcf_start
        # self.main_ui.log(f"[Overlay] ATCF overlays: {_atcf_dt*1000:.1f}ms")

        self.main_ui._ol_project_func = project_lonlat_to_pixel
        self.main_ui._draw_aor_overlays(project_lonlat_to_pixel, scene, overlay_w, overlay_h)
        self.main_ui._draw_tracks_overlays(project_lonlat_to_pixel, scene, overlay_w, overlay_h)

        if getattr(self.main_ui, 'recon_cb', None) is not None and self.main_ui.recon_cb.isChecked():
            try:
                self.main_ui._draw_recon_overlay(project_lonlat_to_pixel, scene)
            except Exception:
                pass

        if hasattr(self.main_ui, 'graphics_view') and self.main_ui.graphics_view:
            if not getattr(self.main_ui.graphics_view, '_user_has_zoomed', False):
                self.main_ui.graphics_view.center_on_image()

        if getattr(self.main_ui, 'winds_enabled', False) and getattr(self.main_ui, 'current_winds_uv', None):
            if not getattr(self.main_ui, '_winds_persist', False):
                self.main_ui._draw_winds_overlay()

        if getattr(self.main_ui, 'microwave_enabled', False) and getattr(self.main_ui, 'microwave_tb_data', None):
            if not getattr(self.main_ui, '_mw_persist', False):
                self.main_ui._draw_microwave_overlay()

        self.main_ui._render_all_climate_overlays()
        if getattr(self.main_ui, 'gtwo_cb', None) and self.main_ui.gtwo_cb.isChecked():
            self.main_ui._render_gtwo_overlay()

        _total = _t.perf_counter() - _t0
        self.main_ui.log(f"[Overlay] Total overlay update: {_total*1000:.1f}ms")
        deferred = getattr(self.main_ui, '_visualizer_deferred', None)
        if deferred:
            self.main_ui._visualizer_deferred = None
            self.main_ui._reproject_current_image(deferred)
        self.main_ui._last_overlay_key = key
        self.main_ui.graphics_view.viewport().update()


    def _draw_recon_overlays(self, project_func, scene):
        """Draw live weather-recon aircraft and their accumulated flight tracks.

        Aircraft positions (and the locally accumulated history) live on
        ``main_ui`` and are refreshed by the ``recon_flights`` client while
        the "Recon Planes" checkbox is enabled. ``project_func`` maps
        lon/lat to scene pixels, exactly as used by the storm overlays.
        """
        aircraft = getattr(self.main_ui, 'recon_aircraft', []) or []
        if not aircraft:
            return
        tracks = getattr(self.main_ui, '_recon_tracks', {}) or {}

        for ac in aircraft:
            lat = ac.get('lat')
            lon = ac.get('lon')
            if lat is None or lon is None:
                continue
            gx, gy = project_func(lon, lat)
            if gx is None or gy is None:
                continue
            color = self._recon_color(ac)
            hex_id = ac.get('hex') or ''

            # --- Accumulated flight track (dashed polyline) ---
            tr = tracks.get(hex_id) or []
            if len(tr) >= 2:
                path = QPainterPath()
                started = False
                for pt in tr:
                    px, py = project_func(pt.get('lon'), pt.get('lat'))
                    if px is None or py is None:
                        started = False
                        continue
                    if not started:
                        path.moveTo(px, py)
                        started = True
                    else:
                        path.lineTo(px, py)
                if started and not path.isEmpty():
                    tip = QGraphicsPathItem(path)
                    pen = QPen(color)
                    pen.setWidth(2)
                    pen.setStyle(Qt.DashLine)
                    tip.setPen(pen)
                    tip.setZValue(24)
                    scene.addItem(tip)
                    self.main_ui._recon_overlay_items.append(tip)

            # --- Heading-oriented aircraft dart marker (clickable) ---
            heading = ac.get('track_deg')
            if heading is None:
                heading = 0.0
            tri = self._recon_dart(heading)
            pd = {
                'location': ac.get('label') or ac.get('callsign') or hex_id,
                'datetime': ac.get('updated'),
                'course': f"{float(heading):.0f}\u00b0",
                'speed_kt': round(ac['speed_kt']) if ac.get('speed_kt') is not None else None,
            }
            if ac.get('on_ground'):
                pd['location'] = f"{pd['location']} (on ground)"
            marker = ClickablePolygonItem(tri, pd, self.main_ui, gx, gy)
            marker.setBrush(QBrush(color))
            marker.setPen(QPen(QColor(0, 0, 0, 255), 1))
            marker.setZValue(28)
            scene.addItem(marker)
            self.main_ui._recon_overlay_items.append(marker)

            # --- Label: callsign + altitude + speed ---
            parts = []
            callsign = (ac.get('callsign') or '').strip()
            label_text = callsign or (ac.get('registration') or '').strip() or hex_id
            alt = ac.get('alt_ft')
            if alt is not None:
                parts.append(f"{alt:,.0f} ft")
            speed = ac.get('speed_kt')
            if speed is not None:
                parts.append(f"{speed:.0f} kt")
            if parts:
                label_text = f"{label_text}  {' / '.join(parts)}"
            lbl = QGraphicsTextItem(label_text)
            lbl.setFont(QFont('Segoe UI', 7, QFont.Normal))
            lbl.setDefaultTextColor(QColor(255, 255, 255))
            lbl.setPos(gx + 13, gy - 6)
            lbl.setZValue(29)
            bg = QGraphicsRectItem(lbl.boundingRect())
            bg.setPos(lbl.pos())
            bg.setBrush(QColor(20, 20, 20, 180))
            bg.setPen(QPen(QColor(120, 120, 120), 1))
            bg.setZValue(28.5)
            scene.addItem(bg)
            scene.addItem(lbl)
            self.main_ui._recon_overlay_items.append(bg)
            self.main_ui._recon_overlay_items.append(lbl)

    @staticmethod
    def _recon_color(ac):
        """Distinct highlight color per recon aircraft family."""
        cs = (ac.get('callsign') or '').upper()
        typ = (ac.get('type') or '').upper()
        if cs.startswith('TEAL') or cs.startswith('AF') or typ in ('C30J', 'WC-130J', 'WC130'):
            return QColor('#00BCD4')  # cyan - USAF WC-130J Hurricane Hunters
        if cs.startswith('NOAA'):
            return QColor('#FF9800')  # orange - NOAA fleet
        return QColor('#FFD700')

    @staticmethod
    def _recon_dart(heading_deg):
        """Triangle dart pointing along ``heading_deg`` (clockwise from north).

        Built in final screen coordinates around the origin so it can be
        placed with a single ``setPos``.
        """
        a = math.radians(float(heading_deg) % 360.0)
        fx, fy = math.sin(a), -math.cos(a)
        rx, ry = math.cos(a), math.sin(a)
        return QPolygonF([
            QPointF(11 * fx, 11 * fy),                      # nose
            QPointF(-7 * fx + 8 * rx, -7 * fy + 8 * ry),    # right wingtip
            QPointF(-4 * fx, -4 * fy),                      # tail notch
            QPointF(-7 * fx - 8 * rx, -7 * fy - 8 * ry),    # left wingtip
        ])


    def _update_overlays_static(self):
        """Update overlays without re-rendering grid/coastlines - only refresh dynamic items.
        
        This prevents overlay lag during zoom/pan by keeping grid and coastlines static
        and only updating storm tracks and other dynamic overlays.
        """
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return

        # When the satellite (or display projection) changed, the cached static
        # geometry no longer matches the imagery. A fast "static" refresh would
        # leave grids, coastlines, AoRs and tracks pinned to the *previous*
        # satellite — fall through to the full projection pass instead.
        if self._overlay_geo_signature() != getattr(self.main_ui, '_ol_geo_sig', None):
            self.main_ui.update_overlays()
            return

        # Remove only dynamic overlay items, keep grid/coastlines
        for item in list(self.main_ui._nhc_overlay_items):
            try: scene.removeItem(item)
            except Exception: pass
        self.main_ui._nhc_overlay_items.clear()
        
        for item in list(self.main_ui._jma_overlay_items):
            try: scene.removeItem(item)
            except Exception: pass
        self.main_ui._jma_overlay_items.clear()
        
        for item in list(self.main_ui._jtwc_overlay_items):
            try: scene.removeItem(item)
            except Exception: pass
        self.main_ui._jtwc_overlay_items.clear()
        
        for item in list(self.main_ui._pagasa_overlay_items):
            try: scene.removeItem(item)
            except Exception: pass
        self.main_ui._pagasa_overlay_items.clear()
        
        for item in list(self.main_ui._atcf_overlay_items):
            try: scene.removeItem(item)
            except Exception: pass
        self.main_ui._atcf_overlay_items.clear()

        for item in list(getattr(self.main_ui, '_recon_overlay_items', [])):
            try: scene.removeItem(item)
            except Exception: pass
        self.main_ui._recon_overlay_items.clear()

        # Re-draw storm overlays only
        if not self.main_ui.current_crs or not self.main_ui.current_geotransform:
            return
        
        if self.main_ui._overlay_transformer is None or self.main_ui._overlay_crs is not self.main_ui.current_crs:
            self.main_ui._overlay_transformer = Transformer.from_crs("EPSG:4326", self.main_ui.current_crs, always_xy=True)
            self.main_ui._overlay_crs = self.main_ui.current_crs
        
        def project_lonlat_to_pixel(lon, lat):
            display_proj = getattr(self.main_ui, '_current_display_projection', 'full_disk')
            if display_proj in ("equirectangular", "plate_carree"):
                extent = getattr(self.main_ui, '_display_projection_extent', None)
                if extent is None:
                    return None, None
                lon_min, lat_min, lon_max, lat_max = extent
                w = getattr(self.main_ui, '_ol_overlay_w', 5476)
                h = getattr(self.main_ui, '_ol_overlay_h', 5476)
                lon_range = lon_max - lon_min
                lat_range = lat_max - lat_min
                if lon_range <= 0 or lat_range <= 0:
                    return None, None
                scale_x = w / lon_range
                scale_y = h / lat_range
                if lon < lon_min:
                    lon += 360.0
                col = (lon - lon_min) * scale_x
                row = (lat_max - lat) * scale_y
                if col < 0 or col >= w or row < 0 or row >= h:
                    return None, None
                return col, row
            try:
                lon = normalize_lon(lon)
                x_proj, y_proj = self.main_ui._overlay_transformer.transform(lon, lat)
                if math.isinf(x_proj) or math.isinf(y_proj) or math.isnan(x_proj) or math.isnan(y_proj):
                    return None, None
                overlay_extent = self.main_ui._ol_disk_half_extent if hasattr(self.main_ui, '_ol_disk_half_extent') else abs(self.main_ui.current_geotransform.c)
                effective_res_m = self.main_ui._ol_native_res_m if hasattr(self.main_ui, '_ol_native_res_m') else abs(self.main_ui.current_geotransform.a)
                if abs(x_proj) > overlay_extent * 1.01 or abs(y_proj) > overlay_extent * 1.01:
                    return None, None
                col = (x_proj + overlay_extent) / effective_res_m
                row = (overlay_extent - y_proj) / effective_res_m
                return col, row
            except Exception:
                return None, None
        
        self.main_ui._draw_nhc_overlays(project_lonlat_to_pixel, scene)
        self.main_ui._draw_jma_overlays(project_lonlat_to_pixel, scene)
        self.main_ui._draw_jtwc_overlays(project_lonlat_to_pixel, scene)
        self.main_ui._draw_pagasa_overlays(project_lonlat_to_pixel, scene)
        # ATCF overlay draw disabled
        # self.main_ui._draw_atcf_overlays(project_lonlat_to_pixel, scene)
        self.main_ui._draw_tracks_overlays(project_lonlat_to_pixel, scene, 
                                   getattr(self.main_ui, '_ol_overlay_w', 5476), 
                                   getattr(self.main_ui, '_ol_overlay_h', 5476))

        if getattr(self.main_ui, 'recon_cb', None) is not None and self.main_ui.recon_cb.isChecked():
            try:
                self.main_ui._draw_recon_overlay(project_lonlat_to_pixel, scene)
            except Exception:
                pass

        if getattr(self.main_ui, 'winds_enabled', False) and getattr(self.main_ui, 'current_winds_uv', None):
            if not getattr(self.main_ui, '_winds_persist', False):
                self.main_ui._draw_winds_overlay()

        if getattr(self.main_ui, 'microwave_enabled', False) and getattr(self.main_ui, 'microwave_tb_data', None):
            if not getattr(self.main_ui, '_mw_persist', False):
                self.main_ui._draw_microwave_overlay()

        scene.update()


    def _get_pixel_latlon(self, col, row):
        lon, lat = None, None
        if hasattr(self.main_ui, '_sataid_current_lat') and self.main_ui._sataid_current_lat is not None:
            try:
                if self.main_ui._sataid_current_lat.ndim == 2:
                    lat = float(self.main_ui._sataid_current_lat[int(row), int(col)])
                    lon = float(self.main_ui._sataid_current_lon[int(row), int(col)])
                else:
                    lat = float(self.main_ui._sataid_current_lat[int(row)])
                    lon = float(self.main_ui._sataid_current_lon[int(col)])
            except Exception:
                pass
        elif hasattr(self.main_ui, 'current_geotransform') and self.main_ui.current_geotransform is not None:
            try:
                from pyproj import Transformer
                xp, yp = self.main_ui.current_geotransform * (col, row)
                t = Transformer.from_crs(self.main_ui.current_crs, "EPSG:4326", always_xy=True)
                lon, lat = t.transform(xp, yp)
            except Exception:
                pass
        if lon is not None and lat is not None:
            return f"{lat:.2f},{lon:.2f}"
        return f"{int(col)},{int(row)}"

