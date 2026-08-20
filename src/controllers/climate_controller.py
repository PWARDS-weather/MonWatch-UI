# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: controllers/climate_controller.py
# Description: Climate data overlay processing for CPC rainfall indices and GTwo seasonal outlook products.
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


import os
import json
import uuid
import sys
import re
import tempfile
import zipfile
import shutil
import traceback
import numpy as np
import requests
from pathlib import Path
from datetime import datetime
from PySide6.QtCore import QObject, Signal, QProcess
from PySide6.QtGui import QColor, QPainterPath, QPen, QBrush, QPolygonF, QPixmap
from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import QProgressBar, QColorDialog, QMessageBox, QGraphicsPixmapItem
from src.core.helpers import top_dir


class ClimateController(QObject):
    """Climate data overlay processing controller.

    Manages climate data retrieval, caching, and overlay rendering for
    CPC (Climate Prediction Center) rainfall indices and GTwo seasonal
    outlook products. Handles data refresh schedules and cache management.

    Attributes:
        main_ui (MainUI): Reference to the main UI instance.

    Signals:
        climate_data_loaded (str): Emitted when climate data is ready.
        overlay_rendered (str): Emitted when climate overlay is drawn.
    """
    overlay_ready = Signal(str)
    progress = Signal(str)

    def __init__(self, main_ui):
        super().__init__(main_ui)
        self.main_ui = main_ui

    def _get_climate_cache_dir(self):
        d = top_dir / "data" / "nhc_data" / "climate"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _get_climate_cache_file(self):
        return top_dir / "cache" / "last_updated_clim.json"

    def _fetch_cpc_index(self):
        url = "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/ghaz/index.php"
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            html = r.text
            last_updated = ""
            valid = ""
            m = re.search(r'Last Updated\s*[-\u2013]\s*(\d{2}/\d{2}/\d{2,4})', html)
            if m: last_updated = m.group(1)
            m = re.search(r'Valid\s*[-\u2013]\s*(\d{2}/\d{2}/\d{2,4})\s*[-\u2013]\s*(\d{2}/\d{2}/\d{2,4})', html)
            if m: valid = f"{m.group(1)} - {m.group(2)}"
            return {"last_updated": last_updated, "valid": valid}
        except Exception as e:
            self.main_ui.log(f"CPC index fetch failed: {e}")
            return None

    def _read_climate_cache(self):
        cf = self.main_ui._get_climate_cache_file()
        if cf.exists():
            try:
                with open(cf) as f:
                    return json.load(f)
            except Exception:
                pass
        return None

    def _save_climate_cache(self, info):
        cf = self.main_ui._get_climate_cache_file()
        try:
            cf.parent.mkdir(parents=True, exist_ok=True)
            with open(cf, "w") as f:
                json.dump(info, f, indent=2)
        except Exception as e:
            self.main_ui.log(f"Failed to save climate cache: {e}")

    def _get_hazard_shp_path(self, code, week):
        week_num = week.split("-")[1]
        cache_dir = self.main_ui._get_climate_cache_dir()
        dest = cache_dir / f"{code}_W{week_num}"
        for f in dest.glob("*.shp"):
            return str(f)
        return None

    def _download_climate_data(self, code, week, force=False):

        """Download climate data from CPC.

        Fetches Climate Prediction Center rainfall and outlook
        products. Caches data locally for offline access.

        Returns:
            bool: True if download succeeded.

        Note:
            Data is updated daily at 12Z.
        """
        week_num = week.split("-")[1]
        cache_dir = self.main_ui._get_climate_cache_dir()
        dest = cache_dir / f"{code}_W{week_num}"
        shp_path = self.main_ui._get_hazard_shp_path(code, week)
        if shp_path and not force:
            return shp_path
        url = f"https://www.cpc.ncep.noaa.gov/products/precip/CWlink/ghaz/shps/W{week_num}_{code}_latest.zip"
        self.main_ui.log(f"Downloading climate data: {code} W{week_num} from {url}")
        try:
            dest.mkdir(parents=True, exist_ok=True)
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
            tmp_path = tmp.name
            for chunk in r.iter_content(8192):
                tmp.write(chunk)
            tmp.close()
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                zf.extractall(str(dest))
            os.unlink(tmp_path)
            for f in dest.glob("*.shp"):
                return str(f)
            self.main_ui.log(f"No .shp found in {url}")
            return None
        except Exception as e:
            self.main_ui.log(f"Climate data download failed for {code} W{week_num}: {e}")
            return None

    def _refresh_all_climate_data(self):

        """Refresh all climate data products.

        Downloads latest CPC rainfall indices and GTwo outlook
        products. Updates cache and triggers overlay refresh.

        Side Effects:
            - Downloads climate data from servers
            - Updates cache files
            - Refreshes climate overlays
        """
        if hasattr(self.main_ui, 'cw_status_label'):
            self.main_ui.cw_status_label.setText("Checking for climate data updates...")
        cpc_info = self.main_ui._fetch_cpc_index()
        cached = self.main_ui._read_climate_cache()
        needs_update = False
        if cpc_info:
            if cached and cpc_info.get("last_updated") and cached.get("last_updated") == cpc_info["last_updated"]:
                self.main_ui.log("Climate data is up to date")
                if hasattr(self.main_ui, 'cw_status_label'):
                    valid_str = cpc_info.get("valid", "")
                    self.main_ui.cw_status_label.setText(f"Up to date. Last Updated: {cpc_info.get('last_updated')}  Valid: {valid_str}")
            else:
                needs_update = True
                self.main_ui.log(f"Climate data outdated (cached: {cached}, remote: {cpc_info}), downloading...")
                if hasattr(self.main_ui, 'cw_status_label'):
                    self.main_ui.cw_status_label.setText(f"Updating climate data... Last Updated: {cpc_info.get('last_updated')}")
        else:
            self.main_ui.log("Could not reach CPC index, using cached data if available")
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText("CPC index unreachable, using cached data")
        for code, wdata in self.main_ui.climate_widgets.items():
            if wdata["checkbox"].isChecked() or needs_update:
                week = wdata["combo"].currentText()
                self.main_ui._download_climate_data(code, week, force=needs_update)
        if cpc_info:
            self.main_ui._save_climate_cache(cpc_info)
        for code in self.main_ui.climate_widgets:
            self.main_ui._render_climate_overlay(code)

    def _toggle_climate_overlay(self, code, checked):

        """Toggle climate data overlay.

        Shows or hides climate data overlay on the viewport.
        Manages overlay lifecycle and cache usage.

        Side Effects:
            - Updates climate checkbox state
            - Renders or removes overlay
        """
        import time as _t
        self.main_ui._click_log = (f'climate_{code}', _t.perf_counter())
        self.main_ui.log(f"Climate overlay {code}: {'ON' if checked else 'OFF'}")
        enabled_key = f"climate_{code.lower()}_enabled"
        self.main_ui.settings.set(enabled_key, checked)
        if checked:
            week = self.main_ui.climate_widgets[code]["combo"].currentText()
            shp = self.main_ui._get_hazard_shp_path(code, week)
            if not shp:
                self.main_ui._download_climate_data(code, week)
            self.main_ui._render_climate_overlay(code)
        else:
            self.main_ui._remove_climate_overlay(code)

    def _on_climate_week_changed(self, code, week):
        self.main_ui.log(f"Climate overlay {code} week changed to {week}")
        week_key = f"climate_{code.lower()}_week"
        self.main_ui.settings.set(week_key, week)
        if self.main_ui.climate_widgets[code]["checkbox"].isChecked():
            self.main_ui._download_climate_data(code, week, force=True)
            self.main_ui._render_climate_overlay(code)

    def _remove_climate_overlay(self, code):
        items_key = f"_climate_overlay_items_{code.lower()}"
        items = getattr(self.main_ui, items_key, [])
        scene = self.main_ui.graphics_view.scene() if hasattr(self.main_ui, 'graphics_view') else None
        for item in items:
            try:
                if scene and item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        items.clear()
        setattr(self.main_ui, items_key, items)

    def _render_climate_overlay(self, code):
        if not hasattr(self.main_ui, 'graphics_view') or not self.main_ui.graphics_view.scene():
            return
        scene = self.main_ui.graphics_view.scene()
        if not getattr(self.main_ui, 'current_crs', None) or not getattr(self.main_ui, 'current_geotransform', None):
            return
        if not self.main_ui.climate_widgets[code]["checkbox"].isChecked():
            self.main_ui._remove_climate_overlay(code)
            return
        week = self.main_ui.climate_widgets[code]["combo"].currentText()
        shp_path = self.main_ui._get_hazard_shp_path(code, week)
        if not shp_path:
            self.main_ui.log(f"No shapefile for {code} {week}, downloading...")
            shp_path = self.main_ui._download_climate_data(code, week)
            if not shp_path:
                return
        try:
            import shapefile
            from pyproj import Transformer
            sf = shapefile.Reader(shp_path)
            transform = self.main_ui.current_geotransform
            crs_proj = self.main_ui.current_crs
            transformer = Transformer.from_crs("EPSG:4326", crs_proj, always_xy=True)
            pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
            if not pixmap_item:
                sf.close()
                return
            img_w = pixmap_item.pixmap().width()
            img_h = pixmap_item.pixmap().height()
            disk_half_extent = self.main_ui._compute_disk_half_extent()
            img_dim = max(img_w, img_h)
            native_res_m = (2 * disk_half_extent) / img_dim if img_dim > 0 else abs(transform.a)
            color_key = f"climate_{code.lower()}_color"
            colors = self.main_ui.settings.get("climate_overlay_colors", {})
            hex_color = colors.get(code, "#FF6B6B")
            clr = QColor(hex_color)
            fill_clr = QColor(clr.red(), clr.green(), clr.blue(), 80)
            edge_clr = QColor(clr.red(), clr.green(), clr.blue(), 200)
            edge_pen = QPen(edge_clr, 1.0)
            self.main_ui._remove_climate_overlay(code)
            items_key = f"_climate_overlay_items_{code.lower()}"
            setattr(self.main_ui, items_key, [])
            items = getattr(self.main_ui, items_key)
            for shape in sf.shapes():
                points = shape.points
                parts = shape.parts.tolist() if hasattr(shape.parts, 'tolist') else list(shape.parts)
                parts.append(len(points))
                for i in range(len(parts) - 1):
                    ring = points[parts[i]:parts[i+1]]
                    if len(ring) < 3:
                        continue
                    lons = np.array([p[0] for p in ring])
                    lons = np.where(lons > 180, lons - 360, lons)
                    lats = np.array([p[1] for p in ring])
                    x_proj, y_proj = transformer.transform(lons, lats)
                    mask = np.isfinite(x_proj) & np.isfinite(y_proj)
                    mask &= (np.abs(x_proj) <= disk_half_extent * 1.01) & (np.abs(y_proj) <= disk_half_extent * 1.01)
                    with np.errstate(invalid='ignore'):
                        px_cols = (x_proj + disk_half_extent) / native_res_m
                        px_rows = (disk_half_extent - y_proj) / native_res_m
                    bound_mask = mask & (px_cols >= -1) & (px_cols < img_w + 1) & (px_rows >= -1) & (px_rows < img_h + 1)
                    valid_pts = [(px_cols[j], px_rows[j]) for j in range(len(ring)) if bound_mask[j]]
                    if len(valid_pts) < 3:
                        continue
                    poly = QPolygonF()
                    for x, y in valid_pts:
                        poly.append(QPointF(x, y))
                    path = QPainterPath()
                    path.addPolygon(poly)
                    item = scene.addPath(path, edge_pen, fill_clr)
                    item.setZValue(30)
                    items.append(item)
            sf.close()
            self.main_ui.log(f"Rendered climate overlay {code} ({len(items)} polygons)")
        except Exception as e:
            self.main_ui.log(f"Climate overlay render error for {code}: {e}")
            traceback.print_exc()

    def _render_all_climate_overlays(self):
        if not hasattr(self.main_ui, 'climate_widgets'):
            return
        for code in self.main_ui.climate_widgets:
            if self.main_ui.climate_widgets[code]["checkbox"].isChecked():
                self.main_ui._render_climate_overlay(code)
        if hasattr(self.main_ui, 'gtwo_cb') and self.main_ui.gtwo_cb.isChecked():
            self.main_ui._render_gtwo_overlay()

    def _pick_climate_color(self, code, button):
        colors = self.main_ui.settings.get("climate_overlay_colors", {})
        current = colors.get(code, "#FF6B6B")
        clr = QColorDialog.getColor(QColor(current), self.main_ui, f"Choose color for {code}")
        if clr and clr.isValid():
            hex_color = clr.name()
            if "climate_overlay_colors" not in self.main_ui.settings.settings:
                self.main_ui.settings.settings["climate_overlay_colors"] = {}
            self.main_ui.settings.settings["climate_overlay_colors"][code] = hex_color
            self.main_ui.settings.save()
            r, g, b = clr.red(), clr.green(), clr.blue()
            text_color = '#000' if (r * 0.299 + g * 0.587 + b * 0.114) > 128 else '#FFF'
            button.setStyleSheet(f"QPushButton {{ background: {hex_color}; border: 1px solid #666; border-radius: 3px; padding: 2px; font-size: 8px; color: {text_color}; }}")
            if self.main_ui.climate_widgets[code]["checkbox"].isChecked():
                self.main_ui._render_climate_overlay(code)

    def _reset_climate_colors(self):
        defaults = {"TC": "#FF6B6B", "WET": "#4ECDC4", "DRY": "#FFE66D", "WARM": "#FF8C42", "COLD": "#74B9FF"}
        self.main_ui.settings.settings["climate_overlay_colors"] = dict(defaults)
        self.main_ui.settings.save()
        for code, hex_color in defaults.items():
            btn = self.main_ui.climate_color_btns.get(code)
            if btn:
                clr = QColor(hex_color)
                r, g, b = clr.red(), clr.green(), clr.blue()
                text_color = '#000' if (r * 0.299 + g * 0.587 + b * 0.114) > 128 else '#FFF'
                btn.setStyleSheet(f"QPushButton {{ background: {hex_color}; border: 1px solid #666; border-radius: 3px; padding: 2px; font-size: 8px; color: {text_color}; }}")
            if code in self.main_ui.climate_widgets and self.main_ui.climate_widgets[code]["checkbox"].isChecked():
                self.main_ui._render_climate_overlay(code)
        if hasattr(self.main_ui, 'cw_status_label'):
            self.main_ui.cw_status_label.setText("Colors reset to CPC defaults")

    def _get_week_dates_json(self, week_nums):
        try:
            cached = self.main_ui._read_climate_cache()
            if not cached:
                cached = self.main_ui._fetch_cpc_index()
            valid_str = (cached or {}).get("valid", "")
            if not valid_str or "-" not in valid_str:
                return None
            import re as _re
            parts = valid_str.split("-")
            start_str = parts[0].strip()
            end_str = parts[1].strip() if len(parts) > 1 else ""
            if not start_str or not end_str:
                return None
            start_dt = datetime.strptime(start_str, "%m/%d/%Y") if start_str.count("/") == 2 else None
            if not start_dt:
                return None
            result = {}
            for wn in sorted(set(wn for wn in week_nums if wn in ("2", "3"))):
                offset_days = (int(wn) - 2) * 7
                s = start_dt + timedelta(days=offset_days)
                e = s + timedelta(days=13)
                result[wn] = s.strftime("%m/%d") + "-" + e.strftime("%m/%d")
            return json.dumps(result) if result else None
        except Exception:
            return None

    def _generate_single_climate_overlay(self, code, week):
        export_dir = self.main_ui.settings.get("export_folder", str(top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        colors = self.main_ui.settings.get("climate_overlay_colors", {})
        color = colors.get(code, "#FF6B6B")
        week_num = week.split("-")[1]
        fname = f"climate_{code}_W{week_num}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        cache_dir = str(self.main_ui._get_climate_cache_dir())
        shp_path = self.main_ui._get_hazard_shp_path(code, week)
        if not shp_path:
            shp_path = self.main_ui._download_climate_data(code, week)
            if not shp_path:
                if hasattr(self.main_ui, 'cw_status_label'):
                    self.main_ui.cw_status_label.setText(f"Failed to get shapefile for {code}")
                return
        proc = QProcess(self.main_ui)
        settings_path = str(self.main_ui.settings.settings_file)
        script = str(top_dir / "Process" / "climate" / "MonWatch-UI-Climate.py")
        proc.setProcessChannelMode(QProcess.MergedChannels)
        cid = f"{code}_{week_num}_{uuid.uuid4().hex[:4]}"
        proc.finished.connect(lambda ec, es, c=code, o=fpath, i=cid: self.main_ui._on_single_climate_gen_finished(ec, es, c, o, i))
        week_dates_json = self.main_ui._get_week_dates_json([week_num])
        args = [
            script,
            "--codes", code,
            "--weeks", week_num,
            "--colors", color,
            "--shp-files", shp_path,
            "--output", fpath,
        ]
        if week_dates_json:
            args.extend(["--week-dates", week_dates_json])
        if settings_path:
            args.extend(["--settings", settings_path])
        logo_path = top_dir / "public" / "images" / "Monwatch-LOGO.png"
        if logo_path.exists():
            args.extend(["--logo", str(logo_path)])
        proc.start(sys.executable, args)
        self.main_ui._climate_gen_procs[cid] = {"proc": proc, "output": fpath, "code": code}
        self.main_ui._update_climate_progress()
        if hasattr(self.main_ui, 'cw_status_label'):
            self.main_ui.cw_status_label.setText(f"Generating {code} {week}...")

    def _update_climate_progress(self):
        running = len(self.main_ui._climate_gen_procs)
        if running > 0:
            if not self.main_ui._climate_gen_progress_bar:
                self.main_ui._climate_gen_progress_bar = QProgressBar()
                self.main_ui._climate_gen_progress_bar.setRange(0, 0)
                self.main_ui._climate_gen_progress_bar.setFixedWidth(160)
                self.main_ui._climate_gen_progress_bar.setFixedHeight(16)
                self.main_ui._climate_gen_progress_bar.setStyleSheet("""
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
                self.main_ui.status_bar.addPermanentWidget(self.main_ui._climate_gen_progress_bar)
            self.main_ui._climate_gen_progress_bar.setFormat(f"Climate: {running}")
            self.main_ui._climate_gen_progress_bar.setVisible(True)
            self.main_ui._climate_gen_progress_bar.repaint()
        else:
            if self.main_ui._climate_gen_progress_bar:
                self.main_ui.status_bar.removeWidget(self.main_ui._climate_gen_progress_bar)
                self.main_ui._climate_gen_progress_bar.deleteLater()
                self.main_ui._climate_gen_progress_bar = None

    def _on_single_climate_gen_finished(self, exit_code, exit_status, code, output_path, cid):
        info = self.main_ui._climate_gen_procs.pop(cid, None)
        self.main_ui._update_climate_progress()
        if exit_code == 0:
            self.main_ui.log(f"Climate map generated: {output_path}")
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText(f"Map saved: {os.path.basename(output_path)}")
            QMessageBox.information(self.main_ui, f"Climate Map - {code}", f"Map saved to:\n{output_path}")
        else:
            self.main_ui.log(f"Climate map generation failed for {code} (code {exit_code})")
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText(f"Map generation failed for {code}")

    def _generate_climate_overlay(self):
        enabled = [(code, w["combo"].currentText()) for code, w in self.main_ui.climate_widgets.items() if w["checkbox"].isChecked()]
        if not enabled:
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText("No climate overlays enabled to generate")
            return

        export_dir = self.main_ui.settings.get("export_folder", str(top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        colors = self.main_ui.settings.get("climate_overlay_colors", {})
        cache_dir = str(self.main_ui._get_climate_cache_dir())

        codes = []
        weeks = []
        hex_colors = []
        shp_files = []
        for code, week_text in enabled:
            week_num = week_text.split("-")[1]
            shp = self.main_ui._get_hazard_shp_path(code, week_text)
            if not shp:
                shp = self.main_ui._download_climate_data(code, week_text)
            if not shp:
                self.main_ui.log(f"Skipping {code}: shapefile not available")
                continue
            codes.append(code)
            weeks.append(week_num)
            hex_colors.append(colors.get(code, "#FF6B6B"))
            shp_files.append(shp)

        if not codes:
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText("No shapefiles available for enabled overlays")
            return

        fname = f"climate_combined_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        settings_path = str(self.main_ui.settings.settings_file)
        script = str(top_dir / "Process" / "climate" / "MonWatch-UI-Climate.py")

        proc = QProcess(self.main_ui)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        cid = f"combined_{uuid.uuid4().hex[:4]}"
        proc.finished.connect(lambda ec, es, o=fpath, i=cid: self.main_ui._on_combined_climate_gen_finished(ec, es, o, i))
        week_dates_json = self.main_ui._get_week_dates_json(weeks)
        args = [
            script,
            "--codes", ",".join(codes),
            "--weeks", ",".join(weeks),
            "--colors", ",".join(hex_colors),
            "--shp-files", ",".join(shp_files),
            "--output", fpath,
        ]
        if week_dates_json:
            args.extend(["--week-dates", week_dates_json])
        if settings_path:
            args.extend(["--settings", settings_path])
        logo_path = top_dir / "public" / "images" / "Monwatch-LOGO.png"
        if logo_path.exists():
            args.extend(["--logo", str(logo_path)])
        proc.start(sys.executable, args)
        self.main_ui._climate_gen_procs[cid] = {"proc": proc, "output": fpath, "code": "combined"}
        self.main_ui._update_climate_progress()
        if hasattr(self.main_ui, 'cw_status_label'):
            week_str = ", ".join(f"{c} W{w}" for c, w in zip(codes, weeks))
            self.main_ui.cw_status_label.setText(f"Generating combined map: {week_str}...")

    def _on_combined_climate_gen_finished(self, exit_code, exit_status, output_path, cid):
        info = self.main_ui._climate_gen_procs.pop(cid, None)
        self.main_ui._update_climate_progress()
        if exit_code == 0:
            self.main_ui.log(f"Combined climate map generated: {output_path}")
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText(f"Map saved: {os.path.basename(output_path)}")
            QMessageBox.information(self.main_ui, "Combined Climate Map",
                                    f"Combined probability map saved to:\n{output_path}")
        else:
            self.main_ui.log(f"Combined climate map generation failed (code {exit_code})")
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText("Combined map generation failed")

    def _get_gtwo_cache_dir(self):
        d = top_dir / "data" / "nhc_data" / "gtwo"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _get_gtwo_shp_files(self):
        d = self.main_ui._get_gtwo_cache_dir()
        return sorted(d.glob("*areas*.shp")) + sorted(d.glob("*points*.shp"))

    def _download_gtwo_data(self, force=False):
        shp = self.main_ui._get_gtwo_shp_files()
        if shp and not force:
            return shp[0]
        url = "https://www.nhc.noaa.gov/xgtwo/gtwo_shapefiles.zip"
        self.main_ui.log(f"Downloading GTWO shapefiles from {url}")
        try:
            d = self.main_ui._get_gtwo_cache_dir()
            for old in d.glob("*"):
                try:
                    if old.is_file(): old.unlink()
                except Exception:
                    pass
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()
            tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
            tmp_path = tmp.name
            for chunk in r.iter_content(8192):
                tmp.write(chunk)
            tmp.close()
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                zf.extractall(str(d))
            os.unlink(tmp_path)
            result = self.main_ui._get_gtwo_shp_files()
            if result:
                return result[0]
            self.main_ui.log("No .shp found in GTWO zip")
            return None
        except Exception as e:
            self.main_ui.log(f"GTWO download failed: {e}")
            return None

    def _generate_gtwo_map(self):
        export_dir = self.main_ui.settings.get("export_folder", str(top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        gtwo_dir = self.main_ui._get_gtwo_cache_dir()
        if not list(gtwo_dir.glob("*.shp")):
            self.main_ui._download_gtwo_data(force=True)
        if not list(gtwo_dir.glob("*.shp")):
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText("No GTWO shapefiles available")
            return
        fname = f"gtwo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        script = str(top_dir / "Process" / "climate" / "MonWatch-UI-GTWO.py")
        proc = QProcess(self.main_ui)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        cid = f"gtwo_{uuid.uuid4().hex[:4]}"
        proc.finished.connect(lambda ec, es, o=fpath, i=cid: self.main_ui._on_gtwo_gen_finished(ec, es, o, i))
        proc.start(sys.executable, [
            script,
            "--shp-dir", str(gtwo_dir),
            "--output", fpath,
        ])
        self.main_ui._climate_gen_procs[cid] = {"proc": proc, "output": fpath, "code": "gtwo"}
        self.main_ui._update_climate_progress()
        if hasattr(self.main_ui, 'cw_status_label'):
            self.main_ui.cw_status_label.setText("Generating GTWO map...")

    def _on_gtwo_gen_finished(self, exit_code, exit_status, output_path, cid):
        info = self.main_ui._climate_gen_procs.pop(cid, None)
        self.main_ui._update_climate_progress()
        if exit_code == 0:
            self.main_ui.log(f"GTWO map generated: {output_path}")
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText(f"Map saved: {os.path.basename(output_path)}")
            QMessageBox.information(self.main_ui, "GTWO Map",
                                    f"GTWO map saved to:\n{output_path}")
        else:
            self.main_ui.log(f"GTWO map generation failed (code {exit_code})")
            if hasattr(self.main_ui, 'cw_status_label'):
                self.main_ui.cw_status_label.setText("GTWO map generation failed")

    def _toggle_gtwo_overlay(self, checked):
        self.main_ui.log(f"GTWO overlay: {'ON' if checked else 'OFF'}")
        self.main_ui.settings.set("gtwo_enabled", checked)
        if checked:
            self.main_ui._download_gtwo_data()
            self.main_ui._render_gtwo_overlay()
        else:
            self.main_ui._remove_gtwo_overlay()

    def _remove_gtwo_overlay(self):
        items = getattr(self.main_ui, '_gtwo_overlay_items', [])
        scene = self.main_ui.graphics_view.scene() if hasattr(self.main_ui, 'graphics_view') else None
        for item in items:
            try:
                if scene and item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        items.clear()
        self.main_ui._gtwo_overlay_items = items

    def _render_gtwo_overlay(self):
        if not hasattr(self.main_ui, 'graphics_view') or not self.main_ui.graphics_view.scene():
            return
        scene = self.main_ui.graphics_view.scene()
        if not getattr(self.main_ui, 'current_crs', None) or not getattr(self.main_ui, 'current_geotransform', None):
            return
        if not hasattr(self.main_ui, 'gtwo_cb') or not self.main_ui.gtwo_cb.isChecked():
            self.main_ui._remove_gtwo_overlay()
            return
        shp_files = self.main_ui._get_gtwo_shp_files()
        if not shp_files:
            self.main_ui.log("No GTWO shapefiles available, downloading...")
            r = self.main_ui._download_gtwo_data()
            if not r:
                return
            shp_files = self.main_ui._get_gtwo_shp_files()
            if not shp_files:
                return
        try:
            from pyproj import Transformer
            transform = self.main_ui.current_geotransform
            crs_proj = self.main_ui.current_crs
            pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
            if not pixmap_item:
                return
            img_w = pixmap_item.pixmap().width()
            img_h = pixmap_item.pixmap().height()
            disk_half_extent = self.main_ui._compute_disk_half_extent()
            img_dim = max(img_w, img_h)
            native_res_m = (2 * disk_half_extent) / img_dim if img_dim > 0 else abs(transform.a)
            self.main_ui._remove_gtwo_overlay()
            self.main_ui._gtwo_overlay_items = []
            items = self.main_ui._gtwo_overlay_items
            tf = Transformer.from_crs("EPSG:4326", crs_proj, always_xy=True)
            for shp_path in shp_files:
                import shapefile
                sf = shapefile.Reader(str(shp_path))
                is_point_shp = "points" in Path(shp_path).stem.lower()
                fields = [f[0] for f in sf.fields if f[0] not in ('DeletionFlag',)]
                prob_field = None
                for candidate in ('PROB', 'PROBABILITY', 'PCT', 'PERCENT', 'RISK', 'DN', 'VALUE', 'CAT', 'CATEGORY', 'LABEL', 'TEXT'):
                    if candidate in fields:
                        prob_field = candidate
                        break
                records = sf.records()
                has_records = len(records) > 0 and len(records) == len(sf.shapes())
                for idx, shape in enumerate(sf.shapes()):
                    prob_val = None
                    if has_records and prob_field:
                        raw = records[idx].as_dict() if hasattr(records[idx], 'as_dict') else None
                        if raw is None:
                            try:
                                raw = dict(zip(fields, list(records[idx])))
                            except Exception:
                                raw = {}
                        if raw:
                            val = raw.get(prob_field, raw.get(prob_field.upper(), raw.get(prob_field.lower())))
                            if val is not None:
                                try:
                                    val_s = str(val).replace('%', '').replace(' ', '').strip()
                                    prob_val = int(float(val_s))
                                    if prob_val <= 1:
                                        prob_val = int(prob_val * 100)
                                except Exception:
                                    try:
                                        cat_map = {'LOW': 10, 'MEDIUM': 50, 'HIGH': 90, 'MED': 50, 'ELEVATED': 40, 'CRITICAL': 70, 'EXTREME': 95}
                                        prob_val = cat_map.get(val_s.upper())
                                    except Exception:
                                        pass
                    fill_color, edge_color = self.main_ui._gtwo_prob_color(prob_val)
                    if is_point_shp:
                        pt = shape.points[0]
                        lon, lat = pt[0], pt[1]
                        if lon > 180: lon -= 360
                        xp, yp = tf.transform(lon, lat)
                        if np.isfinite(xp) and np.isfinite(yp) and abs(xp) <= disk_half_extent * 1.01 and abs(yp) <= disk_half_extent * 1.01:
                            pc = (xp + disk_half_extent) / native_res_m
                            pr = (disk_half_extent - yp) / native_res_m
                            if 0 <= pc < img_w and 0 <= pr < img_h:
                                dot = scene.addEllipse(pc - 3, pr - 3, 6, 6, QPen(edge_color, 1.5), QBrush(fill_color))
                                dot.setZValue(31)
                                items.append(dot)
                    else:
                        points = shape.points
                        parts = shape.parts.tolist() if hasattr(shape.parts, 'tolist') else list(shape.parts)
                        parts.append(len(points))
                        for i in range(len(parts) - 1):
                            ring = points[parts[i]:parts[i+1]]
                            if len(ring) < 3:
                                continue
                            lons = np.array([p[0] for p in ring])
                            lons = np.where(lons > 180, lons - 360, lons)
                            lats = np.array([p[1] for p in ring])
                            x_proj, y_proj = tf.transform(lons, lats)
                            mask = np.isfinite(x_proj) & np.isfinite(y_proj)
                            mask &= (np.abs(x_proj) <= disk_half_extent * 1.01) & (np.abs(y_proj) <= disk_half_extent * 1.01)
                            with np.errstate(invalid='ignore'):
                                px_cols = (x_proj + disk_half_extent) / native_res_m
                                px_rows = (disk_half_extent - y_proj) / native_res_m
                            bound_mask = mask & (px_cols >= -1) & (px_cols < img_w + 1) & (px_rows >= -1) & (px_rows < img_h + 1)
                            valid_pts = [(px_cols[j], px_rows[j]) for j in range(len(ring)) if bound_mask[j]]
                            if len(valid_pts) < 3:
                                continue
                            poly = QPolygonF()
                            for x, y in valid_pts:
                                poly.append(QPointF(x, y))
                            path = QPainterPath()
                            path.addPolygon(poly)
                            item = scene.addPath(path, QPen(edge_color, 1.5), QBrush(fill_color))
                            item.setZValue(30)
                            items.append(item)
                sf.close()
            self.main_ui.log(f"Rendered GTWO overlay ({len(items)} items)")
        except Exception as e:
            self.main_ui.log(f"GTWO overlay render error: {e}")
            traceback.print_exc()

    @staticmethod
    def _gtwo_prob_color(prob_val):
        if prob_val is None:
            return QColor(255, 165, 0, 55), QColor(255, 140, 0, 200)
        if prob_val >= 60:
            return QColor(255, 50, 50, 70), QColor(255, 30, 30, 220)
        if prob_val >= 40:
            return QColor(255, 165, 0, 55), QColor(255, 140, 0, 200)
        return QColor(255, 220, 50, 60), QColor(255, 200, 30, 200)
