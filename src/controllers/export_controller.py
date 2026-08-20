# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: controllers/export_controller.py
# Description: Image export pipeline with geospatial footer annotation and serial bitmap generation.
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


import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import numpy as np

from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtGui import QImage, QPainter, QFont, QColor, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QProgressBar, QGraphicsPixmapItem

from src.core.engine_dispatcher import get_engine, get_products
from src.ui.dialogs import AnimationExportDialog, FlatProjectionOptionsDialog
from src.workers.core import ExportWorker
from src.config.reader_manager import reader_manager
from src.data.prerequisite_loader import _collect_prerequisites_paths
from src.exporters.geotiff_exporter import (
    build_tags,
    display_geotransform,
    translated_geotransform,
    write_rgba_geotiff,
)

_BAND_NICKNAMES = {
    "01": "Blue (VIS)", "02": "Red (VIS)", "03": "Veggie (NIR)",
    "04": "Cirrus (NIR)", "05": "Snow/Ice (NIR)", "06": "Cloud Particle (NIR)",
    "07": "Shortwave IR (IR)", "08": "Upper Water Vapor (WV)",
    "09": "Mid Water Vapor (WV)", "10": "Lower Water Vapor (WV)",
    "11": "Cloud Phase (IR)", "12": "Ozone (IR)", "13": "Clean IR (IR)",
    "14": "Longwave IR (IR)", "15": "Dirty IR (IR)", "16": "CO2 Longwave (IR)",
}

if getattr(sys, 'frozen', False):
    top_dir = Path(sys.executable).resolve().parent
else:
    top_dir = Path(__file__).resolve().parent.parent.parent

_FLATGEN_PENDING = object()


class ExportController(QObject):
    """Image export pipeline controller with geospatial annotation.

    Manages export operations for satellite imagery including PNG,
    animated GIF, and MP4 generation. Adds geospatial footers
    with coordinate information, timestamps, and band metadata.

    Attributes:
        main_ui (MainUI): Reference to the main UI instance.

    Signals:
        export_progress (int): Emitted during export operations (0-100).
        export_finished (Path): Emitted when export completes successfully.
        export_error (str): Emitted when export fails.

    Note:
        Exports include footer with lat/lon coordinates, acquisition time,
        and spectral band information for scientific documentation.
    """
    export_progress = Signal(int, int)
    export_finished = Signal(str)
    export_error = Signal(str)

    def __init__(self, main_ui):
        super().__init__()
        self.main_ui = main_ui

    def _export_viewport_plain(self):

        """Export current viewport image.

        Captures the current viewport content and saves to file.
        Includes optional geospatial footer with coordinates
        and metadata.

        Returns:
            Path | None: Path to exported file, or None if cancelled.

        Side Effects:
            - Shows file save dialog
            - Updates export progress
            - Logs export operation
        """
        self.main_ui.status_bar.showMessage("Generating image...")
        self.main_ui.status_bar.repaint()
        paths = self.main_ui.settings.get("paths", {})
        default_ex = str(Path(__file__).resolve().parent.parent.parent / "Exports")
        export_dir = paths.get("export_folder", "").strip() or default_ex
        os.makedirs(export_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = str(Path(export_dir) / f"monwatch_{timestamp}.png")
        try:
            pixmap = self.main_ui.graphics_view.grab()
            image = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        except Exception:
            scene = self.main_ui.graphics_view.scene()
            if not scene:
                self.main_ui.status_bar.showMessage("No image to export")
                return
            rect = scene.sceneRect()
            image = QImage(int(rect.width()), int(rect.height()), QImage.Format_ARGB32)
            image.fill(Qt.black)
            painter = QPainter(image)
            scene.render(painter)
            painter.end()
        sat_name = self.main_ui.sat_combo.currentText() if hasattr(self.main_ui, 'sat_combo') else ""
        scan_type = self.main_ui.type_combo.currentText() if hasattr(self.main_ui, 'type_combo') else ""
        dt_str = self.main_ui.current_datetime or ""
        band_label = self._get_current_band_label(sat_name)
        if band_label and not self.main_ui.selected_product:
            image = self._upscale_single_band_image(image, sat_name)
        if self.main_ui.settings.get("cartopy_grid_in_exports", False):
            image = self.main_ui._apply_cartopy_grid_to_image(image)
        result = self._add_footer_to_qimage(image, sat_name, scan_type, dt_str, band_label)
        result.save(file_path)
        self.main_ui.log(f"Image with footer exported: {file_path}")
        self.main_ui.status_bar.showMessage(f"Exported: {Path(file_path).name}")

    def _export_bitmap(self):
        paths = self.main_ui.settings.get("paths", {})
        default_ex = str(Path(__file__).resolve().parent.parent.parent / "Exports")
        export_dir = paths.get("export_folder", "").strip() or default_ex
        os.makedirs(export_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"monwatch_{timestamp}.png"
        file_path, _ = QFileDialog.getSaveFileName(
            self.main_ui, "Export Bitmap", str(Path(export_dir) / default_name),
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)"
        )
        if not file_path:
            return
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            QMessageBox.warning(self.main_ui, "Export", "No image to export.")
            return
        rect = scene.sceneRect()
        image = QImage(int(rect.width()), int(rect.height()), QImage.Format_ARGB32)
        painter = QPainter(image)
        scene.render(painter)
        painter.end()
        image.save(file_path)
        self.main_ui.log(f"Bitmap exported: {file_path}")

    def _export_serial_bitmap(self):
        paths = self.main_ui.settings.get("paths", {})
        default_ex = str(Path(__file__).resolve().parent.parent.parent / "Exports")
        export_dir = paths.get("export_folder", "").strip() or default_ex
        os.makedirs(export_dir, exist_ok=True)
        band_names = getattr(self.main_ui, "available_bands", [])
        if not band_names:
            QMessageBox.warning(self.main_ui, "Export", "No bands loaded to export.")
            return
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = Path(export_dir) / f"serial_{timestamp}"
        export_path.mkdir(parents=True, exist_ok=True)
        for band in band_names:
            arr = self.main_ui.cache.get_raw(band)
            if arr is None:
                _eng_export = get_engine(self.main_ui.sat_combo.currentText())
                arr = _eng_export.band_as_image(
                    Path(self.main_ui.current_nc_path), band, getattr(self.main_ui, "preview_max_px", 2200),
                    band_file_map=getattr(self.main_ui, "_band_nc_map", None)
                )
            if arr is not None:
                h, w = arr.shape[:2]
                if arr.dtype != np.uint8:
                    arr = np.clip(arr, 0, 255).astype(np.uint8)
                if arr.ndim == 2:
                    arr = np.stack([arr, arr, arr, np.full_like(arr, 255)], axis=-1)
                elif arr.shape[-1] == 3:
                    alpha = np.full((h, w, 1), 255, dtype=np.uint8)
                    arr = np.concatenate([arr, alpha], axis=-1)
                elif arr.shape[-1] == 1:
                    arr = np.concatenate([arr]*3 + [np.full((h,w,1),255,np.uint8)], axis=-1)
                img = QImage(arr.data, w, h, QImage.Format_RGBA8888)
                band_file = export_path / f"band_{band}.png"
                img.save(str(band_file))
                self.main_ui.log(f"Exported {band_file}")
        self.main_ui.log(f"Serial bitmap export complete: {export_path}")

    def _base_pixmap_item(self):
        """Return the base satellite QGraphicsPixmapItem of the scene.

        Mirrors the lookup used by ``_get_footer_latlon``: the last pixmap
        item in scene order is the georeferenced satellite layer.
        """
        gv = getattr(self.main_ui, 'graphics_view', None)
        if gv is None or gv.scene() is None:
            return None
        for item in reversed(gv.scene().items()):
            if isinstance(item, QGraphicsPixmapItem) and item.pixmap() is not None and not item.pixmap().isNull():
                return item
        return None

    def _resolve_current_georef(self):
        """Return the currently active (crs, geotransform).

        Prefers the animation track georeferencing (used while scrubbing an
        animation) and falls back to the normal scene georeferencing.
        """
        track_crs = getattr(self.main_ui, '_anim_track_crs', None)
        track_gt = getattr(self.main_ui, '_anim_track_gt', None)
        if track_crs is not None and track_gt is not None:
            return track_crs, track_gt
        return getattr(self.main_ui, 'current_crs', None), getattr(self.main_ui, 'current_geotransform', None)

    def _export_geotiff(self):

        """Export the displayed satellite image as a GeoTIFF.

        Saves the base image layer (no decorative footers) as an RGBA 8-bit
        GeoTIFF carrying the native CRS/geotransform plus full metadata tags:
        satellite, band/product, scan type, acquisition time, lat/lon bounds,
        center coordinates and projection details.

        Returns:
            str | None: Path to the written GeoTIFF, or None if cancelled/failed.
        """
        item = self._base_pixmap_item()
        if item is None:
            QMessageBox.warning(self.main_ui, "Export GeoTIFF", "No image loaded to export.")
            return None
        crs, gt = self._resolve_current_georef()
        if crs is None or gt is None:
            QMessageBox.warning(self.main_ui, "Export GeoTIFF",
                                "No georeferencing (CRS/geotransform) is available for the current scene.")
            return None
        pix = item.pixmap()
        w_img, h_img = pix.width(), pix.height()
        if w_img <= 0 or h_img <= 0:
            QMessageBox.warning(self.main_ui, "Export GeoTIFF", "Displayed image is empty.")
            return None
        qimg = pix.toImage().convertToFormat(QImage.Format_RGBA8888)
        arr = self._qimage_to_numpy(qimg)
        img_gt, _ = display_geotransform(gt, w_img)
        paths = self.main_ui.settings.get("paths", {})
        default_ex = str(Path(__file__).resolve().parent.parent.parent / "Exports")
        export_dir = paths.get("export_folder", "").strip() or default_ex
        os.makedirs(export_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"monwatch_{timestamp}.tif"
        file_path, _ = QFileDialog.getSaveFileName(
            self.main_ui, "Export GeoTIFF", str(Path(export_dir) / default_name),
            "GeoTIFF (*.tif *.tiff)"
        )
        if not file_path:
            return None
        if not str(file_path).lower().endswith((".tif", ".tiff")):
            file_path += ".tif"
        sat_name = self.main_ui.sat_combo.currentText() if hasattr(self.main_ui, 'sat_combo') else ""
        scan_type = self.main_ui.type_combo.currentText() if hasattr(self.main_ui, 'type_combo') else ""
        dt_str = self.main_ui.current_datetime or ""
        band_label = self._get_current_band_label(sat_name)
        footer_info = " | ".join(p for p in (
            sat_name, scan_type, self._format_dt_for_footer(dt_str), band_label) if p)
        tags = build_tags(
            satellite=sat_name, scan_type=scan_type, band=band_label,
            product=getattr(self.main_ui, 'selected_product', None) or "",
            dt_str=dt_str, crs=crs, transform=img_gt,
            width=w_img, height=h_img, footer_info=footer_info,
        )
        try:
            write_rgba_geotiff(file_path, arr, img_gt, crs, tags=tags)
        except Exception as e:
            QMessageBox.critical(self.main_ui, "Export GeoTIFF", f"Failed to write GeoTIFF:\n{e}")
            self.main_ui.log(f"GeoTIFF export error: {e}")
            return None
        self.main_ui.log(f"GeoTIFF exported: {file_path}")
        self.main_ui.status_bar.showMessage(f"Exported: {Path(file_path).name}")
        return file_path

    def _qg_metadata_tags(self, region_name: str = "", bbox=None, crs=None,
                          transform=None, width: int = 0, height: int = 0) -> dict:
        """Build the metadata tag set embedded in a QuickGenerate GeoTIFF."""
        sat_name = self.main_ui.sat_combo.currentText() if hasattr(self.main_ui, 'sat_combo') else ""
        scan_type = self.main_ui.type_combo.currentText() if hasattr(self.main_ui, 'type_combo') else ""
        dt_str = self.main_ui.current_datetime or ""
        band_label = self._get_current_band_label(sat_name)
        footer_info = " | ".join(p for p in (
            sat_name, scan_type, self._format_dt_for_footer(dt_str), band_label) if p)
        tags = build_tags(
            satellite=sat_name, scan_type=scan_type, band=band_label,
            product=getattr(self.main_ui, 'selected_product', None) or "",
            dt_str=dt_str, crs=crs, transform=transform,
            width=width, height=height, footer_info=footer_info,
        )
        if region_name:
            tags["MONWATCH_REGION"] = str(region_name)
        if bbox and len(bbox) == 4:
            min_lon, max_lon, min_lat, max_lat = bbox
            tags["MONWATCH_BBOX_MINLON"] = f"{min_lon:.6f}"
            tags["MONWATCH_BBOX_MAXLON"] = f"{max_lon:.6f}"
            tags["MONWATCH_BBOX_MINLAT"] = f"{min_lat:.6f}"
            tags["MONWATCH_BBOX_MAXLAT"] = f"{max_lat:.6f}"
        return tags

    @staticmethod
    def _flat_geotiff_context(width: int, height: int, min_lon: float,
                              max_lon: float, min_lat: float, max_lat: float):
        """Return {crs, transform} for a flat (eqc centered) QG render grid.

        Matches the exact Plate Carree target grid used by
        ``src.workers.flat_gen`` so the exported GeoTIFF lines up with the
        resampled imagery.
        """
        from rasterio.transform import from_bounds
        central_lon = (min_lon + max_lon) / 2.0
        center_lat = (min_lat + max_lat) / 2.0
        crop_lon = (max_lon - min_lon) / 2.0
        crop_lat = (max_lat - min_lat) / 2.0
        m_per_deg = 111320.0
        x_min = -crop_lon * m_per_deg
        y_min = (center_lat - crop_lat) * m_per_deg
        x_max = crop_lon * m_per_deg
        y_max = (center_lat + crop_lat) * m_per_deg
        crs = "+proj=eqc +lon_0={:.6f} +lat_ts=0 +lat_0=0 +datum=WGS84 +units=m +no_defs".format(central_lon)
        transform = from_bounds(x_min, y_min, x_max, y_max, max(1, width), max(1, height))
        return {"crs": crs, "transform": transform}

    def _export_animation(self):

        """Export animation sequence.

        Generates animated GIF or video from loaded animation
        frames. Configurable frame rate and quality settings.

        Args:
            output_path (Path): Output file path.
            fps (int): Frames per second.
            quality (int): Output quality (1-100).

        Returns:
            bool: True if export succeeded.

        Note:
            Uses PIL for GIF encoding, ffmpeg for video.
        """
        frames = getattr(self.main_ui, "_anim_frames", [])
        if not frames or all(f is None for f in frames):
            QMessageBox.warning(self.main_ui, "Export", "No animation frames loaded. Load an animation sequence first.")
            return
        paths = self.main_ui.settings.get("paths", {})
        default_ex = str(Path(__file__).resolve().parent.parent.parent / "Exports")
        export_dir = paths.get("export_folder", "").strip() or default_ex
        os.makedirs(export_dir, exist_ok=True)
        fps = self.main_ui.settings.get("animation_fps", 5)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"animation_{timestamp}.mp4"
        file_path, _ = QFileDialog.getSaveFileName(
            self.main_ui, "Export Animation", str(Path(export_dir) / default_name),
            "MP4 (*.mp4);;GIF (*.gif);;AVI (*.avi)"
        )
        if not file_path:
            return
        use_full_disk = self.main_ui.settings.get("animation_export_use_full_disk", True)
        _was_playing = getattr(self.main_ui, '_anim_playing', False)
        if _was_playing:
            self.main_ui._toggle_animation()
        _saved_just_started = getattr(self.main_ui, '_anim_just_started', False)
        self.main_ui._anim_just_started = False
        self.main_ui._anim_exporting = True
        try:
            rendered = []
            total = sum(1 for f in frames if f is not None)
            done = 0
            for i in range(len(frames)):
                if frames[i] is None:
                    rendered.append(None)
                    continue
                self.main_ui._display_anim_frame(i)
                QApplication.processEvents()
                px = getattr(self.main_ui, '_anim_export_pixmap', None)
                if not use_full_disk:
                    px = self._crop_to_viewport(px)
                if px is not None and not px.isNull():
                    qimg = px.toImage().convertToFormat(QImage.Format_RGB888)
                else:
                    qimg = self.main_ui.graphics_view.grab().toImage().convertToFormat(QImage.Format_RGB888)
                arr = self._qimage_to_numpy(qimg)
                rendered.append(arr)
                done += 1
                self.main_ui.status_bar.showMessage(f"Rendering frame {done}/{total} for export...")
                self.main_ui.status_bar.repaint()
        finally:
            self.main_ui._anim_exporting = False
        self.main_ui._anim_just_started = _saved_just_started
        self._launch_export_worker(rendered, file_path, fps=fps)

    def _launch_export_worker(self, frames, file_path, fps=5,
                              with_footer=False, footer_data=None):
        ctx = getattr(self.main_ui, '_export_ctx', None)
        if ctx:
            try:
                ctx['worker'].cancel()
                ctx['thread'].quit()
                ctx['thread'].wait(3000)
            except RuntimeError:
                pass

        if self.main_ui._export_progress_bar:
            self.main_ui.status_bar.removeWidget(self.main_ui._export_progress_bar)
            self.main_ui._export_progress_bar.deleteLater()
            self.main_ui._export_progress_bar = None

        total = sum(1 for f in frames if f is not None)
        self.main_ui._export_progress_bar = QProgressBar()
        self.main_ui._export_progress_bar.setRange(0, total)
        self.main_ui._export_progress_bar.setValue(0)
        self.main_ui._export_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #2196F3;
                border-radius: 3px;
                text-align: center;
                background: #2A2A2A;
                color: #FFF;
                font-size: 8px;
            }
            QProgressBar::chunk {
                background: #2196F3;
                border-radius: 2px;
            }
        """)
        self.main_ui._export_progress_bar.setFixedWidth(200)
        self.main_ui._export_progress_bar.setFormat(f"Exporting: 0/{total}")
        self.main_ui.status_bar.addPermanentWidget(self.main_ui._export_progress_bar)
        self.main_ui.status_bar.showMessage(f"Exporting animation ({total} frames)...")

        thread = QThread()
        worker = ExportWorker(frames, file_path, fps=fps,
                              with_footer=with_footer, footer_data=footer_data)
        worker.moveToThread(thread)
        worker.progress.connect(self._on_export_progress)
        worker.finished.connect(self._on_export_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(self._on_export_error)
        thread.finished.connect(self._on_export_thread_finished)
        thread.started.connect(worker.run)
        thread.start()

        self.main_ui._export_ctx = dict(thread=thread, worker=worker)

    def _on_export_progress(self, cur, tot):
        if self.main_ui._export_progress_bar:
            self.main_ui._export_progress_bar.setValue(cur)
            self.main_ui._export_progress_bar.setFormat(f"Exporting: {cur}/{tot}")

    def _on_export_finished(self, path):
        if self.main_ui._export_progress_bar:
            self.main_ui.status_bar.removeWidget(self.main_ui._export_progress_bar)
            self.main_ui._export_progress_bar.deleteLater()
            self.main_ui._export_progress_bar = None
        if path:
            self.main_ui.status_bar.showMessage(f"Exported: {Path(path).name}")
            self.main_ui.log(f"Animation exported: {path}")

    def _on_export_thread_finished(self):
        ctx = getattr(self.main_ui, '_export_ctx', None)
        if ctx:
            ctx['thread'].deleteLater()
            self.main_ui._export_ctx = None

    def _on_export_error(self, msg):
        if self.main_ui._export_progress_bar:
            self.main_ui.status_bar.removeWidget(self.main_ui._export_progress_bar)
            self.main_ui._export_progress_bar.deleteLater()
            self.main_ui._export_progress_bar = None
        ctx = getattr(self.main_ui, '_export_ctx', None)
        if ctx:
            ctx['worker'].cancel()
            ctx['thread'].quit()
            ctx['thread'].wait(3000)
            ctx['thread'].deleteLater()
            self.main_ui._export_ctx = None
        self.main_ui.log(f"Animation export error: {msg}")
        QMessageBox.critical(self.main_ui, "Export Error", f"Failed to export: {msg}")

    def _get_current_band_label(self, sat_name: str = "") -> str:
        if self.main_ui.selected_product:
            info = get_products(sat_name if sat_name else self.main_ui.sat_combo.currentText()).get(self.main_ui.selected_product, {})
            return info.get("name", self.main_ui.selected_product)
        if hasattr(self.main_ui, 'band_checkboxes'):
            for b, cb in self.main_ui.band_checkboxes.items():
                if cb.isChecked():
                    num = b[1:]
                    is_goes = "goes" in sat_name.lower()
                    prefix = "C" if is_goes else "B"
                    nickname = _BAND_NICKNAMES.get(num, "")
                    if nickname:
                        return f"{prefix}{num} - {nickname}"
                    return f"{prefix}{num}"
        return ""

    @staticmethod
    def _format_dt_for_footer(dt_str: str) -> str:
        if not dt_str:
            return ""
        try:
            parts = dt_str.split("_")
            if len(parts) >= 4:
                return f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3][:2]}:{parts[3][2:]}"
            return dt_str
        except Exception:
            return dt_str

    def _get_footer_latlon(self) -> str:
        try:
            if not self.main_ui.graphics_view or not self.main_ui.graphics_view.viewport():
                return ""
            vp = self.main_ui.graphics_view.viewport()
            cp = vp.rect().center()
            sc = self.main_ui.graphics_view.mapToScene(cp)
            pix_item = None
            for item in reversed(self.main_ui.graphics_view.scene().items()):
                if isinstance(item, QGraphicsPixmapItem):
                    pix_item = item
                    break
            if not pix_item:
                return ""
            img_x = sc.x() - pix_item.pos().x()
            img_y = sc.y() - pix_item.pos().y()
            img_w = pix_item.pixmap().width()
            img_h = pix_item.pixmap().height()
            if img_x < 0 or img_y < 0 or img_x > img_w or img_y > img_h:
                return ""
            track_crs = getattr(self.main_ui, '_anim_track_crs', None)
            track_gt = getattr(self.main_ui, '_anim_track_gt', None)
            if track_crs is not None and track_gt is not None:
                crs = track_crs
                transform = track_gt
            else:
                crs = self.main_ui.current_crs
                transform = self.main_ui.current_geotransform
            if transform is not None and crs is not None:
                native_res_m = abs(transform.a)
                native_extent = abs(transform.c)
                native_grid_w = int(round(2 * native_extent / native_res_m))
                scale = native_grid_w / img_w if img_w > 0 else 1.0
                x_native = img_x * scale
                y_native = img_y * scale
                x_proj, y_proj = transform * (x_native, y_native)
                from pyproj import Transformer
                transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                lon, lat = transformer.transform(x_proj, y_proj)
                if lon is not None and lat is not None:
                    lat_s = f"{abs(lat):.2f}\u00b0{'N' if lat >= 0 else 'S'}"
                    lon_s = f"{abs(lon):.2f}\u00b0{'E' if lon >= 0 else 'W'}"
                    return f"{lat_s} {lon_s}"
        except Exception:
            pass
        return ""

    def _crop_to_viewport(self, pixmap):
        from PySide6.QtCore import QRect
        if pixmap is None or pixmap.isNull():
            return pixmap
        gv = self.main_ui.graphics_view
        if not gv or not gv.viewport() or not gv.scene():
            return pixmap
        pix_item = None
        for item in reversed(gv.scene().items()):
            if isinstance(item, QGraphicsPixmapItem) and item.pixmap() is not None and not item.pixmap().isNull():
                pix_item = item
                break
        if pix_item is None:
            return pixmap
        vprect = gv.viewport().rect()
        tl = gv.mapToScene(vprect.topLeft())
        br = gv.mapToScene(vprect.bottomRight())
        ox = pix_item.pos().x()
        oy = pix_item.pos().y()
        x0 = max(0.0, tl.x() - ox)
        y0 = max(0.0, tl.y() - oy)
        x1 = min(float(pixmap.width()), br.x() - ox)
        y1 = min(float(pixmap.height()), br.y() - oy)
        if x1 - x0 < 1 or y1 - y0 < 1:
            return pixmap
        return pixmap.copy(QRect(int(x0), int(y0), int(x1 - x0), int(y1 - y0)))

    @staticmethod
    def _qimage_to_numpy(qimg):
        w, h = qimg.width(), qimg.height()
        fmt = qimg.format()
        if fmt == QImage.Format_RGB888:
            channels = 3
        elif fmt in (QImage.Format_ARGB32, QImage.Format_RGB32, QImage.Format_RGBA8888):
            channels = 4
        else:
            raise ValueError(f"Unsupported QImage format: {fmt}")
        bpl = qimg.bytesPerLine()
        raw = np.frombuffer(qimg.constBits(), dtype=np.uint8)
        if bpl == w * channels:
            return raw.reshape(h, w, channels).copy()
        return np.lib.stride_tricks.as_strided(
            raw, shape=(h, w, channels), strides=(bpl, channels, 1)
        ).copy()

    @staticmethod
    def _point_latlon_str(crs, geotransform, x_proj, y_proj):
        try:
            from pyproj import Transformer
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            lon, lat = transformer.transform(x_proj, y_proj)
            if lon is not None and lat is not None:
                lat_s = f"{abs(lat):.2f}\u00b0{'N' if lat >= 0 else 'S'}"
                lon_s = f"{abs(lon):.2f}\u00b0{'E' if lon >= 0 else 'W'}"
                return f"{lat_s} {lon_s}"
        except Exception:
            pass
        return ""

    def _get_image_center_latlon(self, img_w, img_h):
        track_crs = getattr(self.main_ui, '_anim_track_crs', None)
        track_gt = getattr(self.main_ui, '_anim_track_gt', None)
        if track_crs is not None and track_gt is not None:
            crs = track_crs
            gt = track_gt
        else:
            crs = self.main_ui.current_crs
            gt = self.main_ui.current_geotransform
        if not crs or not gt:
            return ""
        try:
            cx, cy = img_w / 2.0, img_h / 2.0
            x_proj, y_proj = gt * (cx, cy)
            return self._point_latlon_str(crs, gt, x_proj, y_proj)
        except Exception:
            return ""

    def _make_footer_parts(self, sat_name, scan_type, dt_str, band_label):
        fsh = self.main_ui.settings.get("footer_show", {})
        show_sat = fsh.get("satellite", True)
        show_dt = fsh.get("datetime", True)
        show_band = fsh.get("band", True)
        show_latlon = fsh.get("latlon", True)
        parts = [sat_name] if show_sat and sat_name else []
        if scan_type:
            parts.append(scan_type)
        dt_fmt = self._format_dt_for_footer(dt_str)
        if show_dt and dt_fmt:
            parts.append(f"{dt_fmt} UTC")
        if show_band and band_label:
            parts.append(band_label)
        ll = self._get_footer_latlon()
        if show_latlon and ll:
            parts.append(ll)
        info_text = " | ".join(parts)
        brand_text = "Made with MonWatch-UI"
        return info_text, brand_text

    def _add_footer_to_qimage(self, image, sat_name: str, scan_type: str, dt_str: str, band_label: str):

        """Add geospatial footer to image.

        Renders footer with acquisition time, coordinates,
        spectral band, and agency information. Footer is
        anti-aliased for professional appearance.

        Args:
            image (QImage): Base image to annotate.
            metadata (dict): Image metadata.

        Returns:
            QImage: Image with footer overlay.

        Note:
            Footer height adapts to image width for consistent
            proportions.
        """
        h = image.height()
        w = image.width()
        footer_size = self.main_ui.settings.get("footer_size", "small")
        size_mult = {"small": 1.0, "normal": 1.8, "big": 2.5}.get(footer_size, 1.0)
        footer_h = max(40, min(int(h * 0.04), 60))
        footer_h = int(footer_h * size_mult)
        fp = self.main_ui.settings.get("footer_position", "bottom")
        info_pos = self.main_ui.settings.get("footer_info_position", "left")
        result = QImage(w, h + footer_h, QImage.Format_ARGB32)
        result.fill(Qt.black)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        info_text, brand_text = self._make_footer_parts(sat_name, scan_type, dt_str, band_label)
        if fp == "top":
            painter.fillRect(0, 0, w, footer_h, Qt.white)
            painter.drawImage(0, footer_h, image)
            fy = 0
        else:
            painter.drawImage(0, 0, image)
            painter.fillRect(0, h, w, footer_h, Qt.white)
            fy = h
        logo_path = top_dir / 'public' / 'images' / 'Monwatch-LOGO.png'
        logo_w = 0
        if logo_path.exists():
            logo_pix = QPixmap(str(logo_path))
            logo_h = footer_h - 8
            logo_pix = logo_pix.scaledToHeight(logo_h, Qt.SmoothTransformation)
            painter.drawPixmap(6, fy + 4, logo_pix)
            logo_w = logo_pix.width() + 10
        ts = self.main_ui.settings.get("text_size", "normal")
        tscale = {"small": 1.0, "normal": 1.3, "large": 1.7}.get(ts, 1.0)
        font_size = max(8, int(footer_h * 0.35 * tscale))
        font = QFont("Consolas", font_size)
        painter.setFont(font)
        painter.setPen(QColor(0, 0, 0))
        if info_pos == "left":
            left_t, right_t = info_text, brand_text
        else:
            left_t, right_t = brand_text, info_text
        painter.drawText(logo_w, fy, w // 2 - logo_w, footer_h, Qt.AlignLeft | Qt.AlignVCenter, left_t)
        painter.drawText(w // 2, fy, w // 2 - 8, footer_h, Qt.AlignRight | Qt.AlignVCenter, right_t)
        painter.end()
        return result

    def _add_footer_to_frame(self, frame: np.ndarray, sat_name: str, scan_type: str, dt_str: str, band_label: str) -> np.ndarray:
        h_img, w_img = frame.shape[:2]
        footer_size = self.main_ui.settings.get("footer_size", "small")
        size_mult = {"small": 1.0, "normal": 1.8, "big": 2.5}.get(footer_size, 1.0)
        footer_h = max(40, min(int(h_img * 0.04), 60))
        footer_h = int(footer_h * size_mult)
        fp = self.main_ui.settings.get("footer_position", "bottom")
        info_pos = self.main_ui.settings.get("footer_info_position", "left")
        if frame.ndim == 2:
            frame_rgb = np.stack([frame] * 3, axis=-1).astype(np.uint8)
        elif frame.shape[-1] == 4:
            frame_rgb = frame[..., :3].astype(np.uint8)
        elif frame.shape[-1] == 3:
            frame_rgb = frame.astype(np.uint8)
        elif frame.shape[-1] == 1:
            frame_rgb = np.concatenate([frame] * 3, axis=-1).astype(np.uint8)
        else:
            frame_rgb = frame[..., :3].astype(np.uint8)
        pil_img = Image.fromarray(frame_rgb)
        new_img = Image.new("RGB", (w_img, h_img + footer_h), (0, 0, 0))
        info_text, brand_text = self._make_footer_parts(sat_name, scan_type, dt_str, band_label)
        if fp == "top":
            new_img.paste(pil_img, (0, footer_h))
            fy = 0
        else:
            new_img.paste(pil_img, (0, 0))
            fy = h_img
        draw = ImageDraw.Draw(new_img)
        draw.rectangle([(0, fy), (w_img, fy + footer_h)], fill=(255, 255, 255))
        logo_path = top_dir / 'public' / 'images' / 'Monwatch-LOGO.png'
        logo_x = 6
        if logo_path.exists():
            try:
                logo_pil = Image.open(str(logo_path)).convert("RGBA")
                logo_h = footer_h - 8
                ratio = logo_h / logo_pil.height
                logo_pil = logo_pil.resize((int(logo_pil.width * ratio), logo_h), Image.LANCZOS)
                new_img.paste(logo_pil, (logo_x, fy + 4), logo_pil)
                logo_x += logo_pil.width + 10
            except Exception:
                pass
        ts = self.main_ui.settings.get("text_size", "normal")
        tscale = {"small": 1.0, "normal": 1.3, "large": 1.7}.get(ts, 1.0)
        font_size = max(8, int(footer_h * 0.35 * tscale))
        try:
            font = ImageFont.truetype("consola.ttf", font_size)
        except Exception:
            try:
                font = ImageFont.truetype("cour.ttf", font_size)
            except Exception:
                font = ImageFont.load_default()
        if info_pos == "left":
            left_t, right_t = info_text, brand_text
        else:
            left_t, right_t = brand_text, info_text
        draw.text((logo_x + 1, fy + 5), left_t, fill=(255, 255, 255), font=font)
        draw.text((logo_x, fy + 4), left_t, fill=(0, 0, 0), font=font)
        try:
            rtw = draw.textbbox((0, 0), right_t, font=font)[2]
        except Exception:
            try:
                rtw = draw.textlength(right_t, font=font)
            except Exception:
                rtw = len(right_t) * font_size
        rx = w_img - rtw - 8
        draw.text((rx + 1, fy + 5), right_t, fill=(255, 255, 255), font=font)
        draw.text((rx, fy + 4), right_t, fill=(0, 0, 0), font=font)
        return np.array(new_img)

    def _upscale_single_band_image(self, image, sat_name=""):
        try:
            is_goes = "goes" in sat_name.lower()
            target_band = "C02" if is_goes else "B03"
            nc_path = self.main_ui._current_nc_file()
            if not nc_path:
                return image
            target_arr = get_engine(self.main_ui.sat_combo.currentText()).band_as_image(nc_path, target_band, 99999,
                                                           band_file_map=getattr(self.main_ui, '_band_nc_map', None))
            if target_arr is None:
                return image
            th, tw = target_arr.shape[:2]
            h, w = image.height(), image.width()
            if th <= h and tw <= w:
                return image
            import io as _io
            buf = _io.BytesIO()
            image.save(buf, "PNG")
            buf.seek(0)
            pil_img = Image.open(buf).convert("RGBA")
            ratio = max(th / h, tw / w)
            nh, nw = int(h * ratio), int(w * ratio)
            pil_up = pil_img.resize((nw, nh), Image.LANCZOS)
            qimg = QImage(pil_up.tobytes(), nw, nh, QImage.Format_RGBA8888)
            return QImage(qimg)
        except Exception:
            return image

    def _export_animation_with_footer(self):
        frames = getattr(self.main_ui, "_anim_frames", [])
        if not frames or all(f is None for f in frames):
            QMessageBox.warning(self.main_ui, "Export", "No animation frames loaded.")
            return
        dlg = AnimationExportDialog(self.main_ui)
        if dlg.exec() != QDialog.Accepted:
            return
        name = dlg.name_edit.text().strip() or "animation"
        fps = dlg.fps_spin.value()
        ext = "." + dlg.fmt_combo.currentText().lower()
        use_full_disk = dlg.cb_full_disk.isChecked()
        self.main_ui.settings.set("animation_export_use_full_disk", use_full_disk)
        footer_size = dlg.footer_size_combo.currentText().lower()
        show_sat = dlg.cb_sat.isChecked()
        show_dt = dlg.cb_dt.isChecked()
        show_band = dlg.cb_band.isChecked()
        show_latlon = dlg.cb_latlon.isChecked()
        footer_pos = dlg.footer_pos_combo.currentText()
        info_pos = dlg.footer_infopos_combo.currentText()
        text_size = dlg.text_size_combo.currentText()
        orig_fsh = self.main_ui.settings.get("footer_show", {})
        orig_fs = self.main_ui.settings.get("footer_size", "small")
        orig_ts = self.main_ui.settings.get("text_size", "normal")
        orig_fp = self.main_ui.settings.get("footer_position", "bottom")
        orig_ip = self.main_ui.settings.get("footer_info_position", "left")
        self.main_ui.settings.set("footer_size", footer_size)
        self.main_ui.settings.set("text_size", text_size)
        self.main_ui.settings.set("footer_position", footer_pos)
        self.main_ui.settings.set("footer_info_position", info_pos)
        self.main_ui.settings.set("footer_show", {"satellite": show_sat, "datetime": show_dt, "band": show_band, "latlon": show_latlon})
        paths = self.main_ui.settings.get("paths", {})
        default_ex = str(Path(__file__).resolve().parent.parent.parent / "Exports")
        export_dir = paths.get("export_folder", "").strip() or default_ex
        os.makedirs(export_dir, exist_ok=True)
        file_path = str(Path(export_dir) / f"{name}{ext}")
        sat_name = self.main_ui.anim_sat_combo.currentText() if hasattr(self.main_ui, 'anim_sat_combo') else ""
        scan_type = self.main_ui.anim_type_combo.currentText() if hasattr(self.main_ui, 'anim_type_combo') else ""
        is_rgb, selector = self.main_ui._get_anim_selected_content() if hasattr(self.main_ui, '_get_anim_selected_content') else (False, "")
        if is_rgb:
            band_label = selector
        else:
            band_label = selector
            if band_label:
                num = band_label[1:]
                is_goes = "goes" in sat_name.lower()
                prefix = "C" if is_goes else "B"
                nickname = _BAND_NICKNAMES.get(num, "")
                if nickname:
                    band_label = f"{prefix}{num} - {nickname}"
                else:
                    band_label = f"{prefix}{num}"
        anim_ts = getattr(self.main_ui, '_anim_timestamps', [])

        base_footer = dict(
            sat_name=sat_name, scan_type=scan_type, dt_str="", band_label=band_label,
            footer_show={"satellite": show_sat, "datetime": show_dt, "band": show_band, "latlon": show_latlon},
            footer_size=footer_size, text_size=text_size,
            footer_position=footer_pos, info_position=info_pos,
            latlon="",
        )
        frame_texts = {}
        for i, f in enumerate(frames):
            if f is None:
                continue
            ts = anim_ts[i] if i < len(anim_ts) else ""
            dt_fmt = self._format_dt_for_footer(ts) if ts else ""
            parts = [sat_name] if show_sat and sat_name else []
            if scan_type:
                parts.append(scan_type)
            if show_dt and dt_fmt:
                parts.append(f"{dt_fmt} UTC")
            if show_band and band_label:
                parts.append(band_label)
            frame_texts[i] = " | ".join(parts)

        footer_data = dict(base_footer, frame_texts=frame_texts)
        _was_playing = getattr(self.main_ui, '_anim_playing', False)
        if _was_playing:
            self.main_ui._toggle_animation()
        _saved_just_started = getattr(self.main_ui, '_anim_just_started', False)
        self.main_ui._anim_just_started = False
        self.main_ui._anim_exporting = True
        try:
            rendered = []
            total = sum(1 for f in frames if f is not None)
            done = 0
            for i in range(len(frames)):
                if frames[i] is None:
                    rendered.append(None)
                    continue
                self.main_ui._display_anim_frame(i)
                QApplication.processEvents()
                if show_latlon and i < len(self.main_ui._anim_crs_list) and self.main_ui._anim_crs_list[i] is not None:
                    _sc, _sg = self.main_ui.current_crs, self.main_ui.current_geotransform
                    self.main_ui.current_crs = self.main_ui._anim_crs_list[i]
                    self.main_ui.current_geotransform = self.main_ui._anim_geotransform_list[i]
                    hh, ww = frames[i].shape[:2]
                    ll = self._get_image_center_latlon(ww, hh)
                    self.main_ui.current_crs, self.main_ui.current_geotransform = _sc, _sg
                    if ll:
                        prev = frame_texts.get(i, "")
                        frame_texts[i] = f"{prev} | {ll}" if prev else ll
                px = getattr(self.main_ui, '_anim_export_pixmap', None)
                if not use_full_disk:
                    px = self._crop_to_viewport(px)
                if px is not None and not px.isNull():
                    qimg = px.toImage().convertToFormat(QImage.Format_RGB888)
                else:
                    qimg = self.main_ui.graphics_view.grab().toImage().convertToFormat(QImage.Format_RGB888)
                arr = self._qimage_to_numpy(qimg)
                rendered.append(arr)
                done += 1
                self.main_ui.status_bar.showMessage(f"Rendering frame {done}/{total} for export...")
                self.main_ui.status_bar.repaint()
        finally:
            self.main_ui._anim_exporting = False
        self.main_ui._anim_just_started = _saved_just_started
        self._launch_export_worker(rendered, file_path, fps=fps,
                                   with_footer=True, footer_data=footer_data)
        self.main_ui.settings.set("footer_size", orig_fs)
        self.main_ui.settings.set("text_size", orig_ts)
        self.main_ui.settings.set("footer_position", orig_fp)
        self.main_ui.settings.set("footer_info_position", orig_ip)
        self.main_ui.settings.set("footer_show", orig_fsh)

    @staticmethod
    def _pil_to_qimage(pil_img):
        arr = np.array(pil_img.convert("RGBA"))
        h, w = arr.shape[:2]
        return QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)

    def _set_grid_coast_overlays_visible(self, visible, prev_states=None):
        """Hide/show viewport grid+coast+AoR overlay items.

        Returns a list of (item, was_visible) tuples when prev_states is None,
        so callers can restore them later. When prev_states is provided, restores
        the original visibility instead of blindly setting all visible.
        """
        states = prev_states if prev_states is not None else []
        items = []
        for attr in ('_grid_item', '_coast_item', '_coast_pix_item', '_coast_swap_item', '_sataid_overlay_item', '_sataid_coast_item',
                     '_grid_overlay_item', '_coast_overlay_item'):
            item = getattr(self.main_ui, attr, None)
            if item is not None and item.scene():
                items.append(item)
        for lst_name in ('grid_overlay_items', 'coast_overlay_items',
                         'aor_overlay_items'):
            for item in list(getattr(self.main_ui, lst_name, []) or []):
                if item is not None and item.scene():
                    items.append(item)
        if prev_states is None:
            for item in items:
                states.append((item, item.isVisible()))
                item.setVisible(False)
            return states
        seen = set()
        for item, was_visible in states:
            seen.add(id(item))
            try:
                item.setVisible(was_visible)
            except Exception:
                pass
        for item in items:
            if id(item) not in seen:
                try:
                    item.setVisible(True)
                except Exception:
                    pass
        return states

    def _zoom_to_and_capture(self, min_lon, max_lon, min_lat, max_lat, name, target_size=None, center_lon=None, center_lat=None, category=None, wind_str=None, pressure_str=None, projection_mode=None, fmt="png"):
        self.main_ui.log(f"DEBUG _zoom_to_and_capture: name={name}")
        
        atcf_storms = getattr(self.main_ui, 'atcf_storms', [])
        atcf_age = getattr(self.main_ui, '_atcf_last_fetch_time', None)
        if not atcf_storms or (atcf_age and (datetime.now(timezone.utc) - atcf_age).total_seconds() > 1800):
            self.main_ui.log("QG: ATCF data stale or missing, fetching before image generation...")
            self.main_ui._fetch_and_not_show_atcf_storms()
        
        if not getattr(self.main_ui, 'current_crs', None) or not getattr(self.main_ui, 'current_geotransform', None):
            self.main_ui.log("QG: no CRS/geotransform available")
            return
        
        if target_size and center_lon is not None and center_lat is not None:
            cx_pix, cy_pix = self.main_ui.overlay_controller._project_lonlat_to_pixel(center_lon, center_lat)
            if cx_pix is None or cy_pix is None:
                self.main_ui.log("QG: center point outside visible area")
                return None
            half_size = target_size / 2.0
            min_px = cx_pix - half_size
            max_px = cx_pix + half_size
            min_py = cy_pix - half_size
            max_py = cy_pix + half_size
            pw = ph = target_size
        else:
            corners = [(min_lon, min_lat), (max_lon, min_lat), (max_lon, max_lat), (min_lon, max_lat)]
            pix_positions = []
            for clon, clat in corners:
                px, py = self.main_ui.overlay_controller._project_lonlat_to_pixel(clon, clat)
                if px is not None and py is not None:
                    pix_positions.append((px, py))
            if not pix_positions:
                self.main_ui.log("QG: region completely outside visible area")
                return None
            all_px = [p[0] for p in pix_positions]
            all_py = [p[1] for p in pix_positions]
            min_px, max_px = min(all_px), max(all_px)
            min_py, max_py = min(all_py), max(all_py)
            pw = max_px - min_px
            ph = max_py - min_py
            if target_size:
                pw = ph = target_size
        if pw < 10 or ph < 10:
            self.main_ui.log("QG: region too small")
            return None
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return None
        target_items_visible = []
        if hasattr(self.main_ui, '_target_boxes'):
            for rect, title, box_name, box_lon, box_lat in self.main_ui._target_boxes:
                if rect.isVisible():
                    rect.setVisible(False)
                    title.setVisible(False)
                    target_items_visible.append((rect, title))
        if hasattr(self.main_ui, '_target_popups'):
            for proxy, widget in self.main_ui._target_popups:
                proxy.setVisible(False)
        grid_coast_visible = []
        if projection_mode == "Flat Projection":
            grid_coast_visible = self._set_grid_coast_overlays_visible(False)
        from PySide6.QtCore import QRectF
        img = QImage(int(pw), int(ph), QImage.Format_ARGB32)
        img.fill(Qt.black)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scene.render(painter, QRectF(0, 0, pw, ph), QRectF(min_px, min_py, pw, ph))
        painter.end()
        self._set_grid_coast_overlays_visible(True, grid_coast_visible)
        for rect, title in target_items_visible:
            rect.setVisible(True)
            title.setVisible(True)
        for proxy in getattr(self.main_ui, '_target_popups', []):
            proxy[0].setVisible(True)
        
        if projection_mode == "Flat Projection":
            self.main_ui.log(f"QG: Applying Flat Projection ...")
            flat_gt, flat_src_crs = self._flat_capture_geotransform()
            self._flatgen_async_img(img, min_lon, max_lon, min_lat, max_lat,
                                     flat_src_crs, flat_gt,
                                     min_px, max_px, min_py, max_py,
                                     name, target_size, center_lon, center_lat,
                                     category, wind_str, pressure_str,
                                     fmt=fmt)
            return
        
        geotiff_ctx = None
        if fmt == "geotiff":
            capture_gt, capture_crs = self._flat_capture_geotransform()
            geotiff_ctx = {
                "crs": capture_crs,
                "transform": translated_geotransform(capture_gt, min_px, min_py),
            }
        return self._finish_qg_capture(img, name, min_lon, max_lon, min_lat, max_lat,
                                        target_size, center_lon, center_lat,
                                        category, wind_str, pressure_str,
                                        fmt=fmt, geotiff_ctx=geotiff_ctx)

    def _finish_qg_capture(self, img, name, min_lon, max_lon, min_lat, max_lat,
                           target_size, center_lon=None, center_lat=None,
                           category=None, wind_str=None, pressure_str=None,
                           fmt="png", geotiff_ctx=None):
        safe_name = "".join(c if c.isalnum() or c in (' ','-','_') else '_' for c in name).strip()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = self.main_ui.settings.get("export_folder", "")
        if not export_dir:
            export_dir = str(top_dir / "Exports")
        os.makedirs(export_dir, exist_ok=True)
        if fmt == "geotiff":
            if geotiff_ctx is None:
                self.main_ui.log("QG GeoTIFF requested but georeferencing is unavailable; aborting")
                return None
            qimg = img.convertToFormat(QImage.Format_RGBA8888)
            arr = self._qimage_to_numpy(qimg)
            h, w = arr.shape[:2]
            tags = self._qg_metadata_tags(region_name=name,
                                          bbox=(min_lon, max_lon, min_lat, max_lat),
                                          crs=geotiff_ctx.get("crs"),
                                          transform=geotiff_ctx.get("transform"),
                                          width=w, height=h)
            fname = f"QG_{safe_name}_{ts}.tif"
            fpath = os.path.join(export_dir, fname)
            write_rgba_geotiff(fpath, arr, geotiff_ctx.get("transform"),
                               geotiff_ctx.get("crs"), tags=tags)
            self.main_ui.log(f"QG GeoTIFF saved: {fpath}")
            return fpath
        use_tcid_footer = name in ["Philippines Region", "Westpac", "PAGASA TCID"]
        is_target = target_size is not None
        if use_tcid_footer:
            img = self._add_tcid_style_footer(img, name, min_lon, max_lon, min_lat, max_lat)
        elif is_target:
            img = self._add_floater_style(img, name, min_lon, max_lon, min_lat, max_lat, center_lon, center_lat, category, wind_str, pressure_str)
        else:
            img = self._add_normal_footer(img, name, min_lon, max_lon, min_lat, max_lat)
        fname = f"QG_{safe_name}_{ts}.png"
        fpath = os.path.join(export_dir, fname)
        img.save(fpath)
        self.main_ui.log(f"QG saved: {fpath}")
        return fpath

    def _finish_qg_direct(self, img, name, min_lon, max_lon, min_lat, max_lat,
                          target_res, center_lon, center_lat,
                          category, wind_str, pressure_str,
                          fmt="png", geotiff_ctx=None):
        safe_name = "".join(c if c.isalnum() or c in (' ','-','_') else '_' for c in name).strip()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = self.main_ui.settings.get("export_folder", "")
        if not export_dir:
            export_dir = str(top_dir / "Exports")
        os.makedirs(export_dir, exist_ok=True)
        fpath = ""
        if fmt == "geotiff":
            if geotiff_ctx is None:
                self.main_ui.log("[QG-Direct] GeoTIFF requested but georeferencing is unavailable; aborting")
                return None
            qimg = img.convertToFormat(QImage.Format_RGBA8888)
            arr = self._qimage_to_numpy(qimg)
            h, w = arr.shape[:2]
            tags = self._qg_metadata_tags(region_name=name,
                                          bbox=(min_lon, max_lon, min_lat, max_lat),
                                          crs=geotiff_ctx.get("crs"),
                                          transform=geotiff_ctx.get("transform"),
                                          width=w, height=h)
            fname = f"QG_{safe_name}_{ts}.tif"
            fpath = os.path.join(export_dir, fname)
            write_rgba_geotiff(fpath, arr, geotiff_ctx.get("transform"),
                               geotiff_ctx.get("crs"), tags=tags)
            self.main_ui.log(f"[QG-Direct] QG GeoTIFF saved: {fpath}")
            preview = self._add_normal_footer(img, name, min_lon, max_lon, min_lat, max_lat)
            fname_pv = f"QG_{safe_name}_{ts}_preview.png"
            fpath_pv = os.path.join(export_dir, fname_pv)
            preview.save(fpath_pv)
            disp_path = fpath_pv
        else:
            use_tcid_footer = name in ["Philippines Region", "Westpac", "PAGASA TCID"]
            is_target = target_res is not None
            if use_tcid_footer:
                img = self._add_tcid_style_footer(img, name, min_lon, max_lon, min_lat, max_lat)
            elif is_target:
                img = self._add_floater_style(img, name, min_lon, max_lon, min_lat, max_lat, center_lon, center_lat, category, wind_str, pressure_str)
            else:
                img = self._add_normal_footer(img, name, min_lon, max_lon, min_lat, max_lat)
            self.main_ui.log(f"[QG-Direct] Footer added")
            fname = f"QG_{safe_name}_{ts}.png"
            fpath = os.path.join(export_dir, fname)
            img.save(fpath)
            self.main_ui.log(f"[QG-Direct] QG saved: {fpath}")
            disp_path = fpath
        pixmap = QPixmap(disp_path)
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            from PySide6.QtWidgets import QGraphicsScene
            scene = QGraphicsScene()
            self.main_ui.graphics_view.setScene(scene)
        else:
            scene.clear()
        from PySide6.QtWidgets import QGraphicsPixmapItem
        item = QGraphicsPixmapItem(pixmap)
        scene.addItem(item)
        self.main_ui.graphics_view.fitInView(item, Qt.AspectRatioMode.KeepAspectRatio)
        self.main_ui.log(f"[QG-Direct] Image displayed in viewport")
        return fpath

    def _flatgen_script_path(self):
        if getattr(sys, 'frozen', False):
            base = Path(sys.executable).resolve().parent
        else:
            base = top_dir
        candidate = base / 'Process' / 'tools' / 'flatgen_worker.py'
        return str(candidate) if candidate.exists() else None

    def _flat_capture_geotransform(self):
        """Return (gt, src_crs) matching the current capture pixel space.

        In full_disk display the scene/capture is the geos disk in meters
        (from the quality-grid overlay state `_ol_disk_half_extent`,
        `_ol_native_res_m`). In rectilinear display (equirectangular /
        plate_carree) the scene is a linear lon/lat grid over
        `_display_projection_extent` — return an eqc lon_0=0 meter
        geotransform so the worker's resample maps it correctly (pyresample
        cannot use a degree-based longlat source directly).
        """
        import math
        from rasterio.crs import CRS
        from rasterio.transform import Affine
        display_proj = getattr(self.main_ui, '_current_display_projection', 'full_disk')
        if display_proj in ("equirectangular", "plate_carree"):
            extent = getattr(self.main_ui, '_display_projection_extent', None)
            w = getattr(self.main_ui, '_ol_overlay_w', 0)
            h = getattr(self.main_ui, '_ol_overlay_h', 0)
            if extent and w and h:
                lon_min, lat_min, lon_max, lat_max = extent
                lon_range = lon_max - lon_min
                lat_range = lat_max - lat_min
                if lon_range > 0 and lat_range > 0:
                    m_per_deg = 6378137.0 * math.pi / 180.0
                    return (Affine(lon_range / w * m_per_deg, 0.0, lon_min * m_per_deg,
                                   0.0, -lat_range / h * m_per_deg, lat_max * m_per_deg),
                            CRS.from_proj4('+proj=eqc +lon_0=0 +datum=WGS84'))
        try:
            half = getattr(self.main_ui, '_ol_disk_half_extent', None)
            res_m = getattr(self.main_ui, '_ol_native_res_m', None)
            if half and res_m and res_m > 0:
                return (Affine(res_m, 0.0, -half, 0.0, -res_m, half),
                        self.main_ui.current_crs)
        except Exception:
            pass
        return self.main_ui.current_geotransform, self.main_ui.current_crs

    def _flatgen_aors(self):
        """Collect enabled AoR polygons for the flat projection export.

        Replicates the viewport 'AoR' overlay list (_draw_aor_overlays): PAR,
        JMA, TCAD, TCID, Manila FIR, Custom AoR (drawn polygon or 'Custom AoR'
        tracks). Tracks/other overlays are intentionally NOT included here —
        they are baked into the captured scene and carried through the resample.
        """
        aors = []
        s = self.main_ui.settings
        aor_pattern = s.get("aor_pattern", "dashed")

        def _push(name, color, pts):
            if pts and len(pts) >= 2:
                aors.append({"name": name, "color": color,
                             "pattern": aor_pattern, "points": [[lon, lat] for lon, lat in pts]})

        if getattr(self.main_ui, 'aor_par_cb', None) and self.main_ui.aor_par_cb.isChecked():
            _push("PAR", s.get("par_color", "#00FF9F"),
                  [(115.0, 5.0), (115.0, 15.0), (120.0, 21.0), (120.0, 25.0), (135.0, 25.0), (135.0, 5.0)])
        if getattr(self.main_ui, 'aor_jma_cb', None) and self.main_ui.aor_jma_cb.isChecked():
            _push("JMA AoR", s.get("jma_color", "#FFAA00"),
                  [(100.0, 0.0), (100.0, 20.0), (100.0, 40.0), (100.0, 60.0),
                   (179.99, 60.0), (179.99, 40.0), (179.99, 20.0), (179.99, 0.0)])
        if getattr(self.main_ui, 'aor_tcad_cb', None) and self.main_ui.aor_tcad_cb.isChecked():
            _push("TCAD", s.get("tcad_color", "#FF6B6B"),
                  [(114.0, 4.0), (114.0, 27.0), (145.0, 27.0), (145.0, 4.0)])
        if getattr(self.main_ui, 'aor_tcid_cb', None) and self.main_ui.aor_tcid_cb.isChecked():
            _push("TCID", s.get("tcid_color", "#4ECDC4"),
                  [(110.0, 0.0), (110.0, 27.0), (155.0, 27.0), (155.0, 0.0)])
        if getattr(self.main_ui, 'aor_fir_cb', None) and self.main_ui.aor_fir_cb.isChecked():
            _push("Manila FIR", s.get("fir_color", "#FFE66D"),
                  [(117.3, 21.0), (130.0, 21.0), (130.0, 7.0), (120.0, 4.0), (117.3, 7.3)])
        if getattr(self.main_ui, 'aor_custom_cb', None) and self.main_ui.aor_custom_cb.isChecked():
            custom_color = s.get("custom_aor_color", "#00E5FF")
            drawn = getattr(self.main_ui, 'current_custom_aor_points', None)
            if drawn and len(drawn) >= 2:
                _push("Custom (drawing)", custom_color,
                      [(p[0], p[1]) for p in drawn])
            if getattr(self.main_ui, 'tracks', None):
                for t in self.main_ui.tracks:
                    if t.get("type") == "Custom AoR" and t.get("points"):
                        pts = [(p.get("lon"), p.get("lat")) for p in t["points"]
                               if p.get("lon") is not None and p.get("lat") is not None]
                        if len(pts) >= 3:
                            _push(t.get("name", "Custom AoR"), custom_color, pts)
        return aors

    def _flatgen_opts(self):
        opts = getattr(self, '_flat_opts', None) or {}
        return {
            "grid_enabled": bool(opts.get("grid_enabled", False)),
            "coast_enabled": bool(opts.get("coast_enabled", True)),
            "labels": bool(opts.get("labels", False)),
            "grid_step": opts.get("grid_step", 10),
            "grid_color": opts.get("grid_color", '#00feed'),
            "grid_opacity": opts.get("grid_opacity", 160),
            "grid_width": opts.get("grid_width", 1),
            "grid_pattern": opts.get("grid_pattern", 'dotted'),
            "coast_color": opts.get("coast_color", '#00ff00'),
            "coast_opacity": opts.get("coast_opacity", 200),
            "coast_width": opts.get("coast_width", 1),
            "coast_pattern": opts.get("coast_pattern", 'solid'),
        }

    def _flatgen_cli_args(self, opts):
        return [
            '--grid', '1' if opts["grid_enabled"] else '0',
            '--coast', '1' if opts["coast_enabled"] else '0',
            '--labels', '1' if opts["labels"] else '0',
            '--grid-step', str(opts["grid_step"]),
            '--grid-color', opts["grid_color"],
            '--grid-opacity', str(opts["grid_opacity"]),
            '--grid-width', str(opts["grid_width"]),
            '--grid-style', opts["grid_pattern"],
            '--coast-color', opts["coast_color"],
            '--coast-opacity', str(opts["coast_opacity"]),
            '--coast-width', str(opts["coast_width"]),
            '--coast-style', opts["coast_pattern"],
        ]

    def _flatgen_aors_args(self, aors):
        if not aors:
            return []
        return ['--aors', json.dumps(aors)]

    def _flatgen_launch_worker(self, cmd, output_path, in_path, on_result, on_error):
        self.main_ui.flatgen_progress.setValue(0)
        self.main_ui.flatgen_progress.show()

        if hasattr(self.main_ui, '_fg_thread') and self.main_ui._fg_thread:
            try:
                if self.main_ui._fg_thread.isRunning():
                    self.main_ui._fg_thread.quit()
                    self.main_ui._fg_thread.wait(2000)
            except RuntimeError:
                pass

        class _FlatGenWorker(QObject):
            progress = Signal(int)
            result_ready = Signal(str)
            error_sig = Signal(str)

            def __init__(self, cmd, output_path):
                super().__init__()
                self.cmd = cmd
                self.output_path = output_path

            def run(self):
                import subprocess
                try:
                    proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
                    for line in iter(proc.stdout.readline, ''):
                        line = line.strip()
                        if line.startswith('PROGRESS:'):
                            try:
                                pct = int(line.split(':')[1])
                                self.progress.emit(pct)
                            except Exception:
                                pass
                    proc.wait()
                    if proc.returncode == 0:
                        self.result_ready.emit(self.output_path)
                    else:
                        err = proc.stderr.read()
                        self.error_sig.emit(err or "Flat render subprocess failed")
                except Exception as e:
                    self.error_sig.emit(str(e))

        worker = _FlatGenWorker(cmd, output_path)
        thread = QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(self.main_ui.flatgen_progress.setValue)
        worker.result_ready.connect(lambda p: on_result(p))
        worker.result_ready.connect(thread.quit)
        worker.error_sig.connect(lambda e: on_error(e))
        worker.error_sig.connect(thread.quit)

        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._flatgen_hide_progress)
        self.main_ui._fg_worker = worker
        self.main_ui._fg_thread = thread
        thread.start()

    def _flatgen_hide_progress(self):
        self.main_ui.flatgen_progress.hide()
        self.main_ui._fg_thread = None
        self.main_ui._fg_worker = None

    def _flatgen_async_img(self, img, min_lon, max_lon, min_lat, max_lat,
                           src_crs, gt, min_px, max_px, min_py, max_py,
                           name, target_size, center_lon, center_lat,
                           category, wind_str, pressure_str, fmt="png"):
        import tempfile, json
        script = self._flatgen_script_path()
        opts = self._flatgen_opts()
        aors = self._flatgen_aors()

        def _ctx_for(result):
            if fmt == "geotiff":
                return self._flat_geotiff_context(result.width(), result.height(),
                                                  min_lon, max_lon, min_lat, max_lat)
            return None

        if not script:
            from src.workers.flat_gen import render_flat as _fr
            result = _fr(img, min_lon, max_lon, min_lat, max_lat,
                         src_crs, gt, min_px, max_px, min_py, max_py,
                         grid_enabled=opts["grid_enabled"], coast_enabled=opts["coast_enabled"],
                         grid_step=opts["grid_step"], grid_color=opts["grid_color"],
                         grid_opacity=opts["grid_opacity"], grid_width=opts["grid_width"],
                         grid_pattern=opts["grid_pattern"],
                         coast_color=opts["coast_color"], coast_opacity=opts["coast_opacity"],
                         coast_width=opts["coast_width"], coast_pattern=opts["coast_pattern"],
                         labels=opts["labels"], aors=aors)
            self._finish_qg_capture(result, name, min_lon, max_lon, min_lat, max_lat,
                                    target_size, center_lon, center_lat,
                                    category, wind_str, pressure_str,
                                    fmt=fmt, geotiff_ctx=_ctx_for(result))
            return

        fd_in, in_path = tempfile.mkstemp(suffix='_fg_in.png')
        os.close(fd_in)
        img.save(in_path)

        fd_out, out_path = tempfile.mkstemp(suffix='_fg_out.png')
        os.close(fd_out)

        cmd = [sys.executable, script,
               '--mode', 'img',
               '--input', in_path,
               '--output', out_path,
               '--min-lon', str(min_lon),
               '--max-lon', str(max_lon),
               '--min-lat', str(min_lat),
               '--max-lat', str(max_lat),
               '--min-px', str(min_px),
               '--max-px', str(max_px),
               '--min-py', str(min_py),
               '--max-py', str(max_py)]
        cmd += self._flatgen_cli_args(opts)
        if aors:
            cmd += ['--aors', json.dumps(aors)]
        if src_crs is not None:
            cmd += ['--src-crs', json.dumps(src_crs.to_dict())]
        if gt is not None:
            cmd += ['--geotransform', json.dumps([gt.a, gt.b, gt.c, gt.d, gt.e, gt.f])]

        def on_success(out_p):
            result = QImage(out_p)
            if not result.isNull():
                self._finish_qg_capture(result, name, min_lon, max_lon, min_lat, max_lat,
                                        target_size, center_lon, center_lat,
                                        category, wind_str, pressure_str,
                                        fmt=fmt, geotiff_ctx=_ctx_for(result))
            _cleanup()

        def on_error(err):
            self.main_ui.log(f"Flat projection subprocess error: {err}, falling back to in-process")
            from src.workers.flat_gen import render_flat as _fr
            result = _fr(img, min_lon, max_lon, min_lat, max_lat,
                         src_crs, gt, min_px, max_px, min_py, max_py,
                         grid_enabled=opts["grid_enabled"], coast_enabled=opts["coast_enabled"],
                         grid_step=opts["grid_step"], grid_color=opts["grid_color"],
                         grid_opacity=opts["grid_opacity"], grid_width=opts["grid_width"],
                         grid_pattern=opts["grid_pattern"],
                         coast_color=opts["coast_color"], coast_opacity=opts["coast_opacity"],
                         coast_width=opts["coast_width"], coast_pattern=opts["coast_pattern"],
                         labels=opts["labels"], aors=aors)
            self._finish_qg_capture(result, name, min_lon, max_lon, min_lat, max_lat,
                                    target_size, center_lon, center_lat,
                                    category, wind_str, pressure_str,
                                    fmt=fmt, geotiff_ctx=_ctx_for(result))
            _cleanup()

        def _cleanup():
            try:
                os.unlink(in_path)
            except Exception:
                pass
            try:
                os.unlink(out_path)
            except Exception:
                pass

        self._flatgen_launch_worker(cmd, out_path, in_path, on_success, on_error)

    def _flatgen_async_array(self, data, min_lon, max_lon, min_lat, max_lat,
                             name, target_res, center_lon, center_lat,
                             category, wind_str, pressure_str, projection_mode,
                             fmt="png"):
        import tempfile, json
        script = self._flatgen_script_path()
        opts = self._flatgen_opts()
        aors = self._flatgen_aors()

        def _ctx_for(result):
            if fmt == "geotiff":
                return self._flat_geotiff_context(result.width(), result.height(),
                                                  min_lon, max_lon, min_lat, max_lat)
            return None

        if not script:
            from src.workers.flat_gen import render_flat_from_array as _fra
            img = _fra(data, min_lon, max_lon, min_lat, max_lat,
                       grid_enabled=opts["grid_enabled"], coast_enabled=opts["coast_enabled"],
                       grid_step=opts["grid_step"], grid_color=opts["grid_color"],
                       grid_opacity=opts["grid_opacity"], grid_width=opts["grid_width"],
                       grid_pattern=opts["grid_pattern"],
                       coast_color=opts["coast_color"], coast_opacity=opts["coast_opacity"],
                       coast_width=opts["coast_width"], coast_pattern=opts["coast_pattern"],
                       labels=opts["labels"], aors=aors)
            self._finish_qg_direct(img, name, min_lon, max_lon, min_lat, max_lat,
                                   target_res, center_lon, center_lat,
                                   category, wind_str, pressure_str,
                                   fmt=fmt, geotiff_ctx=_ctx_for(img))
            return

        fd_in, in_path = tempfile.mkstemp(suffix='_fg_in.npy')
        os.close(fd_in)
        np.save(in_path, data)

        fd_out, out_path = tempfile.mkstemp(suffix='_fg_out.png')
        os.close(fd_out)

        cmd = [sys.executable, script,
               '--mode', 'array',
               '--input', in_path,
               '--output', out_path,
               '--min-lon', str(min_lon),
               '--max-lon', str(max_lon),
               '--min-lat', str(min_lat),
               '--max-lat', str(max_lat)]
        cmd += self._flatgen_cli_args(opts)
        cmd += self._flatgen_aors_args(aors)

        def on_success(out_p):
            img = QImage(out_p)
            if not img.isNull():
                w, h = img.width(), img.height()
                self.main_ui.log(f"[QG-Direct] Flat Projection rendered: {w}x{h}")
                self._finish_qg_direct(img, name, min_lon, max_lon, min_lat, max_lat,
                                       target_res, center_lon, center_lat,
                                       category, wind_str, pressure_str,
                                       fmt=fmt, geotiff_ctx=_ctx_for(img))
            _cleanup()

        def on_error(err):
            self.main_ui.log(f"Flat projection subprocess error: {err}, falling back to in-process")
            from src.workers.flat_gen import render_flat_from_array as _fra
            img = _fra(data, min_lon, max_lon, min_lat, max_lat,
                       grid_enabled=opts["grid_enabled"], coast_enabled=opts["coast_enabled"],
                       grid_step=opts["grid_step"], grid_color=opts["grid_color"],
                       grid_opacity=opts["grid_opacity"], grid_width=opts["grid_width"],
                       grid_pattern=opts["grid_pattern"],
                       coast_color=opts["coast_color"], coast_opacity=opts["coast_opacity"],
                       coast_width=opts["coast_width"], coast_pattern=opts["coast_pattern"],
                       labels=opts["labels"], aors=aors)
            self._finish_qg_direct(img, name, min_lon, max_lon, min_lat, max_lat,
                                   target_res, center_lon, center_lat,
                                   category, wind_str, pressure_str,
                                   fmt=fmt, geotiff_ctx=_ctx_for(img))
            _cleanup()

        def _cleanup():
            try:
                os.unlink(in_path)
            except Exception:
                pass
            try:
                os.unlink(out_path)
            except Exception:
                pass

        self._flatgen_launch_worker(cmd, out_path, in_path, on_success, on_error)

    def _flatgen_async_img_full_disk(self, img, min_lon, max_lon, min_lat, max_lat,
                                      sw, sh, name, fmt="png"):
        import tempfile, json
        # Capture is in scene/quality-grid pixel space (sw/sh are the scene capture dims)
        flat_gt, flat_src_crs = self._flat_capture_geotransform()
        script = self._flatgen_script_path()
        opts = self._flatgen_opts()
        aors = self._flatgen_aors()

        def _save_result(result):
            safe_name = "".join(c if c.isalnum() or c in (' ','-','_') else '_' for c in name).strip()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_dir = self.main_ui.settings.get("export_folder", "")
            if not export_dir:
                export_dir = str(top_dir / "Exports")
            os.makedirs(export_dir, exist_ok=True)
            if fmt == "geotiff":
                ctx = self._flat_geotiff_context(result.width(), result.height(),
                                                 min_lon, max_lon, min_lat, max_lat)
                qimg = result.convertToFormat(QImage.Format_RGBA8888)
                arr = self._qimage_to_numpy(qimg)
                h, w = arr.shape[:2]
                tags = self._qg_metadata_tags(region_name=name,
                                              bbox=(min_lon, max_lon, min_lat, max_lat),
                                              crs=ctx.get("crs"),
                                              transform=ctx.get("transform"),
                                              width=w, height=h)
                fname = f"QG_{safe_name}_{ts}.tif"
                fpath = os.path.join(export_dir, fname)
                write_rgba_geotiff(fpath, arr, ctx.get("transform"), ctx.get("crs"), tags=tags)
                preview = self._add_tcid_style_footer(result, name, min_lon, max_lon, min_lat, max_lat)
                fname_pv = f"QG_{safe_name}_{ts}_preview.png"
                fpath_pv = os.path.join(export_dir, fname_pv)
                preview.save(fpath_pv)
                self.main_ui.log(f"QG Full Disk GeoTIFF saved: {fpath}")
            else:
                result = self._add_tcid_style_footer(result, name, min_lon, max_lon, min_lat, max_lat)
                fname = f"QG_{safe_name}_{ts}.png"
                fpath = os.path.join(export_dir, fname)
                result.save(fpath)
                self.main_ui.log(f"QG Full Disk saved: {fpath}")
            return fpath

        if not script:
            from src.workers.flat_gen import render_flat as _fr
            result = _fr(img, min_lon, max_lon, min_lat, max_lat,
                         flat_src_crs, flat_gt,
                         0, sw, 0, sh,
                         grid_enabled=opts["grid_enabled"], coast_enabled=opts["coast_enabled"],
                         grid_step=opts["grid_step"], grid_color=opts["grid_color"],
                         grid_opacity=opts["grid_opacity"], grid_width=opts["grid_width"],
                         grid_pattern=opts["grid_pattern"],
                         coast_color=opts["coast_color"], coast_opacity=opts["coast_opacity"],
                         coast_width=opts["coast_width"], coast_pattern=opts["coast_pattern"],
                         labels=opts["labels"], aors=aors)
            _save_result(result)
            return

        fd_in, in_path = tempfile.mkstemp(suffix='_fg_in.png')
        os.close(fd_in)
        img.save(in_path)

        fd_out, out_path = tempfile.mkstemp(suffix='_fg_out.png')
        os.close(fd_out)

        cmd = [sys.executable, script,
               '--mode', 'img',
               '--input', in_path,
               '--output', out_path,
               '--min-lon', str(min_lon),
               '--max-lon', str(max_lon),
               '--min-lat', str(min_lat),
               '--max-lat', str(max_lat),
               '--min-px', '0',
               '--max-px', str(sw),
               '--min-py', '0',
               '--max-py', str(sh)]
        cmd += self._flatgen_cli_args(opts)
        cmd += self._flatgen_aors_args(aors)
        if flat_src_crs is not None:
            cmd += ['--src-crs', json.dumps(flat_src_crs.to_dict())]
        if flat_gt is not None:
            cmd += ['--geotransform', json.dumps([flat_gt.a,
                                                   flat_gt.b,
                                                   flat_gt.c,
                                                   flat_gt.d,
                                                   flat_gt.e,
                                                   flat_gt.f])]

        def on_success(out_p):
            result = QImage(out_p)
            if not result.isNull():
                _save_result(result)
            _cleanup()

        def on_error(err):
            self.main_ui.log(f"Flat projection Full Disk error: {err}, falling back to in-process")
            from src.workers.flat_gen import render_flat as _fr
            result = _fr(img, min_lon, max_lon, min_lat, max_lat,
                         self.main_ui.current_crs, flat_gt,
                         0, sw, 0, sh,
                         grid_enabled=opts["grid_enabled"], coast_enabled=opts["coast_enabled"],
                         grid_step=opts["grid_step"], grid_color=opts["grid_color"],
                         grid_opacity=opts["grid_opacity"], grid_width=opts["grid_width"],
                         grid_pattern=opts["grid_pattern"],
                         coast_color=opts["coast_color"], coast_opacity=opts["coast_opacity"],
                         coast_width=opts["coast_width"], coast_pattern=opts["coast_pattern"],
                         labels=opts["labels"], aors=aors)
            _save_result(result)
            _cleanup()

        def _cleanup():
            try:
                os.unlink(in_path)
            except Exception:
                pass
            try:
                os.unlink(out_path)
            except Exception:
                pass

        self._flatgen_launch_worker(cmd, out_path, in_path, on_success, on_error)

    def _quick_generate_direct(self, bbox, name, target_res=None, center_lon=None, center_lat=None,
                               category=None, wind_str=None, pressure_str=None, projection_mode=None,
                               fmt="png"):
        """Direct QG generation using satpy Scene with AreaDefinition crop.
        
        This method generates images directly from HSD files using pyresample's AreaDefinition
        to crop to the specified region without loading the full disk first.
        
        Args:
            bbox: Either (min_lon, max_lon, min_lat, max_lat) tuple OR (lon, lat) center point
            name: Storm/region name for filename
            target_res: Resolution in meters (default: 2000)
            center_lon, center_lat: Center coordinates if bbox is a point
            category, wind_str, pressure_str: Storm metadata for footer
        """
        from datetime import datetime, timezone
        from pyresample.geometry import AreaDefinition
        from satpy import Scene
        import numpy as np
        from PySide6.QtGui import QImage, QPainter, QColor, QFont, QFontMetrics, QPixmap
        from PySide6.QtCore import Qt
        from pathlib import Path
        
        self.main_ui.log(f"[QG-Direct] Starting direct generation for: {name}")
        
        # Determine current data folder (from loaded scene or satellite controller)
        data_folder = None
        if hasattr(self.main_ui, 'current_data_folder') and self.main_ui.current_data_folder:
            data_folder = self.main_ui.current_data_folder
        elif hasattr(self.main_ui, '_current_nc_file'):
            nc_path = self.main_ui._current_nc_file()
            if nc_path:
                data_folder = nc_path.parent
        elif hasattr(self.main_ui.satellite_controller, 'current_data_folder'):
            data_folder = self.main_ui.satellite_controller.current_data_folder
        
        if not data_folder:
            self.main_ui.log("[QG-Direct] ERROR: No data folder available - load a scene first")
            return None
        
        # Find HSD files
        dat_files = list(data_folder.glob("*.dat")) + list(data_folder.glob("*.DAT"))
        if not dat_files:
            self.main_ui.log(f"[QG-Direct] ERROR: No .DAT files found in {data_folder}")
            return None
        
        self.main_ui.log(f"[QG-Direct] Found {len(dat_files)} .DAT files")
        
        # Parse bbox
        if isinstance(bbox, tuple) and len(bbox) == 2:
            # Center point - create bounding box
            lon, lat = bbox
            margin = 3.0 if target_res is None else (target_res / 1000.0)
            min_lon, max_lon = lon - margin, lon + margin
            min_lat, max_lat = lat - margin, lat + margin
            self.main_ui.log(f"[QG-Direct] Center point: ({lon}, {lat}) -> bbox: {min_lon:.1f}-{max_lon:.1f}E, {min_lat:.1f}-{max_lat:.1f}N")
        else:
            min_lon, max_lon, min_lat, max_lat = bbox
            self.main_ui.log(f"[QG-Direct] Region: {min_lon:.1f}-{max_lon:.1f}E, {min_lat:.1f}-{max_lat:.1f}N")
        
        # Determine resolution
        res = target_res if target_res else 2000
        self.main_ui.log(f"[QG-Direct] Target resolution: {res}m")
        
        # Determine which band to use (default to B13 for IR)
        band_str = "B13"
        if hasattr(self.main_ui, 'selected_band') and self.main_ui.selected_band:
            band_str = self.main_ui.selected_band
        elif hasattr(self.main_ui, 'band_checkboxes') and self.main_ui.band_checkboxes:
            for b in ["B13", "B14", "B07", "B03"]:
                if b in self.main_ui.band_checkboxes and self.main_ui.band_checkboxes[b].isChecked():
                    band_str = b
                    break
        
        self.main_ui.log(f"[QG-Direct] Using band: {band_str}")
        
        is_flat = (projection_mode == "Flat Projection")
        
        if is_flat:
            # Flat Projection: use PlateCarree (lat/lon) AreaDefinition
            deg_per_pixel = res / 111320.0
            out_w = max(1, int(round((max_lon - min_lon) / deg_per_pixel)))
            out_h = max(1, int(round((max_lat - min_lat) / deg_per_pixel)))
            self.main_ui.log(f"[QG-Direct] Flat Projection output dimensions: {out_w}x{out_h} pixels")
            
            if out_w < 10 or out_h < 10:
                self.main_ui.log("[QG-Direct] ERROR: Output region too small")
                return None
            
            projection_dict = {'proj': 'longlat', 'datum': 'WGS84'}
            try:
                from pyresample.geometry import AreaDefinition
                target_area = AreaDefinition(
                    'crop', 'UserCrop', 'crop_area',
                    projection_dict, out_w, out_h,
                    (min_lon, min_lat, max_lon, max_lat)
                )
                self.main_ui.log(f"[QG-Direct] Created PlateCarree AreaDefinition: {out_w}x{out_h}, extent=({min_lon:.2f}, {min_lat:.2f}, {max_lon:.2f}, {max_lat:.2f})")
            except Exception as e:
                self.main_ui.log(f"[QG-Direct] AreaDefinition creation failed: {e}")
                return None
        else:
            # Current Projection: use Mercator AreaDefinition
            try:
                from pyproj import Transformer
                tf = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
                x_min, y_min = tf.transform(min_lon, min_lat)
                x_max, y_max = tf.transform(max_lon, max_lat)
                self.main_ui.log(f"[QG-Direct] Area extent (EPSG:3857): ({x_min:.0f}, {y_min:.0f}, {x_max:.0f}, {y_max:.0f})")
            except Exception as e:
                self.main_ui.log(f"[QG-Direct] Projection transform failed: {e}")
                return None
            
            width_m = x_max - x_min
            height_m = y_max - y_min
            out_w = max(1, int(round(width_m / res)))
            out_h = max(1, int(round(height_m / res)))
            self.main_ui.log(f"[QG-Direct] Output dimensions: {out_w}x{out_h} pixels")
            
            if out_w < 10 or out_h < 10:
                self.main_ui.log("[QG-Direct] ERROR: Output region too small")
                return None
            
            projection_dict = {
                'proj': 'merc',
                'lon_0': (min_lon + max_lon) / 2.0,
                'datum': 'WGS84'
            }
            try:
                from pyresample.geometry import AreaDefinition
                target_area = AreaDefinition(
                    'crop', 'UserCrop', 'crop_area',
                    projection_dict, out_w, out_h,
                    (x_min, y_min, x_max, y_max)
                )
                self.main_ui.log(f"[QG-Direct] Created Mercator AreaDefinition: {out_w}x{out_h}, extent=({x_min:.0f}, {y_min:.0f}, {x_max:.0f}, {y_max:.0f})")
            except Exception as e:
                self.main_ui.log(f"[QG-Direct] AreaDefinition creation failed: {e}")
                return None
        
        # Build georeferencing context for GeoTIFF output (non-flat Mercator grid)
        geotiff_ctx = None
        if fmt == "geotiff" and not is_flat:
            try:
                from rasterio.transform import from_bounds
                ext = target_area.area_extent
                gt = from_bounds(ext[0], ext[1], ext[2], ext[3],
                                 max(1, out_w), max(1, out_h))
                geotiff_ctx = {"crs": target_area.crs, "transform": gt}
            except Exception as e:
                self.main_ui.log(f"[QG-Direct] GeoTIFF context creation failed: {e}")
                geotiff_ctx = None
        
        # Load and resample with satpy
        all_filepaths = [str(f) for f in dat_files]
        resample_type = "nearest"
        
        try:
            self.main_ui.log(f"[QG-Direct] Creating Scene with {len(all_filepaths)} files...")
            # Use dynamic reader selection instead of hardcoded 'ahi_hsd'
            reader_name = reader_manager.get_appropriate_reader(all_filepaths)
            scn = Scene(filenames=all_filepaths, reader=reader_name)

            self.main_ui.log(f"[QG-Direct] Loading band {band_str}...")
            scn.load([band_str])

            # Collect prerequisite paths for loaded data
            try:
                # Extract the dataset from the scene
                data_array = scn[band_str]
                # For prerequisite analysis, we can use the dataset
                if hasattr(data_array, 'dataset'):
                    data_dataset = data_array.dataset
                else:
                    data_dataset = data_array

                # Collect prerequisite paths
                prerequisite_paths = _collect_prerequisites_paths([data_dataset])
                if prerequisite_paths:
                    self.main_ui.log(f"[QG-Direct] Prerequisite loader found {len(prerequisite_paths)} prerequisite files")
                    # In a full implementation, we would load these files here
                    # For now, we just log them
            except Exception as e:
                logger.debug(f"Could not collect prerequisites: {e}")

            self.main_ui.log(f"[QG-Direct] Resampling to target area (resampler={resample_type})...")
            
            self.main_ui.log(f"[QG-Direct] Extracting data array...")
            data_arr = res_scene[band_str].values
            self.main_ui.log(f"[QG-Direct] Data shape: {data_arr.shape}, dtype={data_arr.dtype}")
            
        except Exception as e:
            self.main_ui.log(f"[QG-Direct] Satpy processing failed: {e}")
            import traceback
            self.main_ui.log(f"[QG-Direct] Traceback: {traceback.format_exc()}")
            return None
        
        # Convert to displayable image
        try:
            valid = data_arr[np.isfinite(data_arr)]
            if len(valid) == 0:
                self.main_ui.log("[QG-Direct] ERROR: No valid pixels in data")
                return None
            
            vmin, vmax = valid.min(), valid.max()
            self.main_ui.log(f"[QG-Direct] Data range: [{vmin:.1f}, {vmax:.1f}]")
            
            if vmax > vmin:
                scaled = ((data_arr - vmin) / (vmax - vmin) * 255).clip(0, 255).astype(np.uint8)
            else:
                scaled = np.zeros_like(data_arr, dtype=np.uint8)
            
            scaled[~np.isfinite(data_arr)] = 0
            
            if is_flat:
                self._flatgen_async_array(scaled, min_lon, max_lon, min_lat, max_lat,
                                          name, target_res, center_lon, center_lat,
                                          category, wind_str, pressure_str, projection_mode,
                                          fmt=fmt)
                return _FLATGEN_PENDING
            else:
                # Create grayscale QImage (Mercator)
                img = QImage(scaled.data, out_w, out_h, out_w, QImage.Format_Grayscale8)
                img = img.convertToFormat(QImage.Format_ARGB32)
                self.main_ui.log(f"[QG-Direct] Created QImage: {img.width()}x{img.height()}")
            
        except Exception as e:
            self.main_ui.log(f"[QG-Direct] Image conversion failed: {e}")
            import traceback
            self.main_ui.log(f"[QG-Direct] Traceback: {traceback.format_exc()}")
            return None
        
        # Track the clean (no-footer) image for GeoTIFF output
        clean_img = img.copy()
        
        # Add footer with storm info
        try:
            use_tcid_footer = name in ["Philippines Region", "Westpac", "PAGASA TCID"]
            is_target = target_res is not None
            
            if fmt == "geotiff" and geotiff_ctx is not None:
                pass
            elif use_tcid_footer:
                img = self._add_tcid_style_footer(img, name, min_lon, max_lon, min_lat, max_lat)
            elif is_target:
                img = self._add_floater_style(img, name, min_lon, max_lon, min_lat, max_lat, center_lon, center_lat, category, wind_str, pressure_str)
            else:
                img = self._add_normal_footer(img, name, min_lon, max_lon, min_lat, max_lat)
            
            self.main_ui.log(f"[QG-Direct] Footer added")
            
        except Exception as e:
            self.main_ui.log(f"[QG-Direct] Footer addition failed: {e}")
        
        # Save to file
        try:
            if fmt == "geotiff" and geotiff_ctx is None:
                self.main_ui.log("[QG-Direct] GeoTIFF requested but georeferencing is unavailable; aborting")
                return None
            safe_name = "".join(c if c.isalnum() or c in (' ','-','_') else '_' for c in name).strip()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_dir = self.main_ui.settings.get("export_folder", "")
            if not export_dir:
                export_dir = str(top_dir / "Exports")
            os.makedirs(export_dir, exist_ok=True)
            if fmt == "geotiff" and geotiff_ctx is not None:
                qimg = clean_img.convertToFormat(QImage.Format_RGBA8888)
                arr = self._qimage_to_numpy(qimg)
                h, w = arr.shape[:2]
                tags = self._qg_metadata_tags(region_name=name,
                                              bbox=(min_lon, max_lon, min_lat, max_lat),
                                              crs=geotiff_ctx.get("crs"),
                                              transform=geotiff_ctx.get("transform"),
                                              width=w, height=h)
                fname = f"QG_{safe_name}_{ts}.tif"
                fpath = os.path.join(export_dir, fname)
                write_rgba_geotiff(fpath, arr, geotiff_ctx.get("transform"),
                                   geotiff_ctx.get("crs"), tags=tags)
                self.main_ui.log(f"[QG-Direct] QG GeoTIFF saved: {fpath}")
                preview_footer = self._add_normal_footer(clean_img, name, min_lon, max_lon, min_lat, max_lat)
                fname_pv = f"QG_{safe_name}_{ts}_preview.png"
                fpath_pv = os.path.join(export_dir, fname_pv)
                preview_footer.save(fpath_pv)
                self.main_ui.log(f"[QG-Direct] QG preview saved: {fpath_pv}")
            else:
                fname = f"QG_{safe_name}_{ts}.png"
                fpath = os.path.join(export_dir, fname)
                img.save(fpath)
                self.main_ui.log(f"[QG-Direct] QG saved: {fpath}")
            
            disp_path = fpath_pv if (fmt == "geotiff" and geotiff_ctx is not None) else fpath
            
            # Display in graphics view
            pixmap = QPixmap(disp_path)
            scene = self.main_ui.graphics_view.scene()
            if not scene:
                from PySide6.QtWidgets import QGraphicsScene
                scene = QGraphicsScene()
                self.main_ui.graphics_view.setScene(scene)
            else:
                scene.clear()
            
            from PySide6.QtWidgets import QGraphicsPixmapItem
            item = QGraphicsPixmapItem(pixmap)
            scene.addItem(item)
            self.main_ui.graphics_view.fitInView(item, Qt.AspectRatioMode.KeepAspectRatio)
            
            self.main_ui.log(f"[QG-Direct] Image displayed in viewport")
            
            return fpath
            
        except Exception as e:
            self.main_ui.log(f"[QG-Direct] Save failed: {e}")
            return None

    def _get_active_storms_in_region(self, min_lon, max_lon, min_lat, max_lat, check_point=None):
        """Get active storms in the specified region based on ATCF data only.
        
        Uses ATCF (KnackWX API) as the sole source for active storm information
        to avoid duplicates from multiple agency sources. Only includes systems
        that are actual tropical cyclones (excludes invests/low pressure areas).
        
        Args:
            min_lon, max_lon, min_lat, max_lat: Bounding box coordinates
            check_point: Optional (lon, lat) tuple for PAR inclusion check
        
        Returns:
            tuple: (list of storm dicts, seen_cats dict)
        """
        storms = []
        par_poly = [(115.0,5.0),(115.0,15.0),(120.0,21.0),(120.0,25.0),(135.0,25.0),(135.0,5.0)]
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
        def _in_region(lon, lat):
            return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat
        seen_cats = {}
        
        # ATCF data ONLY - no duplicates from other agencies
        atcf_storms = getattr(self.main_ui, 'atcf_storms', [])
        for storm in atcf_storms:
            lon = storm.get("current_lon")
            lat = storm.get("current_lat")
            if lon is None or lat is None:
                continue
            
            # Exclude invests and low pressure areas - only show actual tropical cyclones
            cat = storm.get("category", "")
            if cat.lower() in ("invest", "lpa", "lo", "db", "disturbance", "tropical wave"):
                continue
            
            if not _in_region(lon, lat):
                continue
            
            name = storm.get("storm_name", "")
            check_lon = check_point[0] if check_point else lon
            check_lat = check_point[1] if check_point else lat
            in_par = _in_par(check_lon, check_lat)
            
            storms.append({
                'name': name,
                'cat': cat.upper() if cat else "UNKNOWN",
                'in_par': in_par,
                'sid': storm.get("atcf_id", "")
            })
        
        return storms, seen_cats

    def _add_tcid_style_footer(self, img, name, min_lon, max_lon, min_lat, max_lat):
        from datetime import timezone as tzmod, timedelta
        from pathlib import Path
        from PySide6.QtGui import QFont, QFontMetrics, QPixmap, QColor, QImage
        from PySide6.QtCore import QRectF

        w, h = img.width(), img.height()
        footer_h = 140
        new_img = QImage(w, h + footer_h, QImage.Format_ARGB32)
        new_img.fill(QColor(255, 255, 255))
        painter = QPainter(new_img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawImage(0, 0, img)
        painter.fillRect(0, h, w, footer_h, QColor(255, 255, 255))
        logo_path = top_dir / "public" / "images" / "Monwatch-LOGO.png"
        logo_w, logo_h = 0, 0
        if logo_path.exists():
            logo_pix = QPixmap(str(logo_path))
            logo_pix = logo_pix.scaledToHeight(70, Qt.SmoothTransformation)
            logo_w, logo_h = logo_pix.width(), logo_pix.height()
            painter.drawPixmap(10, h + 10, logo_pix)
        logo_offset = logo_w + 15 if logo_w > 0 else 0
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
            date_str = f"DATE: {local_dt.strftime('%B %d, %Y')}"
        else:
            time_str = f"TIME: {dt_obj.strftime('%I:%M:%S %p')} UTC"
            date_str = f"DATE: {dt_obj.strftime('%B %d, %Y')} UTC"
        storms, seen_cats = self._get_active_storms_in_region(min_lon, max_lon, min_lat, max_lat)
        fs = max(7, min(11, w // 120))
        hf = QFont("Segoe UI", fs + 1, QFont.Bold)
        nf = QFont("Segoe UI", fs)
        sf = QFont("Segoe UI", fs - 1)
        c_center = logo_offset + 10
        c_right = max(w - 170, c_center + 130)
        painter.setPen(QColor(30, 30, 30))
        painter.setFont(hf)
        time_w = QFontMetrics(hf).horizontalAdvance(time_str)
        date_w = QFontMetrics(nf).horizontalAdvance(date_str)
        gap = 20
        c_storms = c_center + max(time_w, date_w) + gap
        painter.drawText(c_center, h + 24, time_str)
        painter.drawText(c_storms, h + 24, "ACTIVE STORMS:")
        painter.setFont(nf)
        painter.setPen(QColor(100, 100, 100))
        painter.drawText(c_center, h + 48, date_str)
        painter.setPen(QColor(30, 30, 30))
        dy = h + 48
        for row in storms[:5]:
            dy += 16
            if dy > h + footer_h - 15:
                break
            in_out = "INSIDE" if row['in_par'] else "OUTSIDE"
            painter.drawText(c_storms, dy, f"{row['name']} ({row['cat']}) {in_out}")
        def_lbl = {"lpa": "Low Pressure Area", "td": "Tropical Depression", "ts": "Tropical Storm",
                   "sts": "Severe Tropical Storm", "ty": "Typhoon", "sty": "Super Typhoon",
                   "cat1": "Category 1", "cat2": "Category 2", "cat3": "Category 3",
                   "cat4": "Category 4", "cat5": "Category 5",
                   "Hu": "Hurricane"}
        
        # Map full category names to short codes for legend matching
        cat_to_short = {
            "low pressure area": "lpa", "lpa": "lpa",
            "tropical depression": "td", "td": "td",
            "tropical storm": "ts", "ts": "ts",
            "severe tropical storm": "sts", "sts": "sts",
            "typhoon": "ty", "ty": "ty",
            "super typhoon": "sty", "sty": "sty",
            "hurricane": "Hu", "Hu": "Hu",
            "major hurricane": "Hu",
            "category 1": "cat1", "cat1": "cat1",
            "category 2": "cat2", "cat2": "cat2",
            "category 3": "cat3", "cat3": "cat3",
            "category 4": "cat4", "cat4": "cat4",
            "category 5": "cat5", "cat5": "cat5",
        }
        
        painter.setFont(hf)
        painter.setPen(QColor(30, 30, 30))
        painter.drawText(c_right, h + 24, "LEGEND:")
        painter.setFont(sf)
        dy2 = h + 24
        for sn in ("lpa", "td", "ts", "sts", "ty", "sty", "Hu"):
            # Check if any storm's category matches this legend item
            cat_here = False
            for s in storms:
                storm_cat = s['cat'].lower()
                short_cat = cat_to_short.get(storm_cat, storm_cat)
                if short_cat == sn or storm_cat == sn:
                    cat_here = True
                    break
            sp = top_dir / "public" / "images" / "symbols" / f"{sn}.png"
            if not sp.exists():
                continue
            dy2 += 16
            if dy2 > h + footer_h - 15:
                break
            spix = QPixmap(str(sp)).scaledToHeight(13, Qt.SmoothTransformation)
            painter.drawPixmap(c_right, dy2 - 10, spix)
            painter.setPen(QColor(30, 30, 30) if cat_here else QColor(160, 160, 160))
            painter.drawText(c_right + 18, dy2, def_lbl.get(sn, sn.upper()))
        painter.end()
        return new_img

    def _add_floater_style(self, img, name, min_lon, max_lon, min_lat, max_lat, center_lon=None, center_lat=None, category=None, wind_str=None, pressure_str=None):
        from datetime import timezone as tzmod, timedelta
        from pathlib import Path
        w, h = img.width(), img.height()
        from PySide6.QtGui import QFont, QFontMetrics, QPixmap, QColor, QBrush, QPen, QPainterPath
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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
            time_str = f"{local_dt.strftime('%I:%M:%S %p')} {tz_label}"
            date_str = f"{local_dt.strftime('%b %d, %Y')}"
        else:
            time_str = f"{dt_obj.strftime('%I:%M:%S %p')} UTC"
            date_str = f"{dt_obj.strftime('%b %d, %Y')}"
        check_point = (center_lon, center_lat) if center_lon and center_lat else None
        storms, _ = self._get_active_storms_in_region(min_lon, max_lon, min_lat, max_lat, check_point)
        pad_x = 15
        pad_y = 12
        fs = 9
        lf_fs = 7
        hf = QFont("Segoe UI", fs + 1, QFont.Bold)
        nf = QFont("Segoe UI", fs)
        sf = QFont("Segoe UI", lf_fs, QFont.Light)
        logo_w = 30
        logo_h = 24
        logo_margin = 6
        lines = []
        target_label = "TARGET"
        lines.append((target_label, hf, QColor(255, 255, 255)))
        lines.append((f"{name}", QFont("Segoe UI", fs + 2, QFont.Bold), QColor(255, 255, 255)))
        if center_lat is not None and center_lon is not None:
            clon_str = f"{abs(center_lon):.2f}°{'E' if center_lon >= 0 else 'W'}"
            clat_str = f"{abs(center_lat):.2f}°{'N' if center_lat >= 0 else 'S'}"
            latlon_str = f"{clat_str}, {clon_str}"
        else:
            mid_lon = (min_lon + max_lon) / 2.0
            mid_lat = (min_lat + max_lat) / 2.0
            clon_str = f"{abs(mid_lon):.1f}°{'E' if mid_lon >= 0 else 'W'}"
            clat_str = f"{abs(mid_lat):.1f}°{'N' if mid_lat >= 0 else 'S'}"
            latlon_str = f"{clat_str}, {clon_str}"
        lines.append((f"{date_str} | {time_str}", sf, QColor(200, 200, 200)))
        lines.append((latlon_str, sf, QColor(180, 180, 180)))
        
        # Add VMAX | PMIN if available
        if wind_str or pressure_str:
            vmax_pmin = []
            if wind_str:
                vmax_pmin.append(f"VMAX: {wind_str}")
            if pressure_str:
                vmax_pmin.append(f"PMIN: {pressure_str}")
            lines.append((" | ".join(vmax_pmin), sf, QColor(255, 215, 0)))
        
        sat_name = getattr(self.main_ui, 'sat_combo', None)
        band_name = ""
        if sat_name:
            sat_name = sat_name.currentText() if hasattr(sat_name, 'currentText') else ""
        selected_band = next((b for b, cb in getattr(self.main_ui, 'band_checkboxes', {}).items() if cb.isChecked()), None)
        if selected_band:
            band_name = selected_band
        elif getattr(self.main_ui, 'selected_product', None):
            _sat_for_prod = sat_name if sat_name else "himawari9"
            band_name = get_products(_sat_for_prod).get(self.main_ui.selected_product, {}).get("name", self.main_ui.selected_product)
        if sat_name or band_name:
            product_str = f"{sat_name}"
            if band_name:
                product_str += f" | {band_name}"
            lines.append((product_str, sf, QColor(170, 200, 255)))
        
        # Add TC Category | PAR status
        
        if storms:
            lines.append(("", nf, QColor(0, 0, 0)))
            for s in storms[:3]:
                in_out = "IN PAR" if s['in_par'] else "OUTSIDE PAR"
                lines.append((f"{s['name']} ({s['cat']}) - {in_out}", nf, QColor(255, 255, 255)))
        else:
            lines.append(("No active storms", sf, QColor(200, 200, 200)))
        line_h = 16
        text_start_x = pad_x + logo_w + logo_margin
        max_text_w = 0
        fm = QFontMetrics(nf)
        for text, font, color in lines:
            if text:
                tw = fm.horizontalAdvance(text)
                max_text_w = max(max_text_w, tw)
        box_w = text_start_x + max_text_w + pad_x
        total_h = pad_y * 2 + len(lines) * line_h
        box_x = 20
        box_y = h - total_h - 20
        clip_path = QPainterPath()
        clip_path.addRoundedRect(box_x, box_y, box_w, total_h, 8, 8)
        painter.setClipPath(clip_path)
        gradient = QColor(0, 0, 0, 180)
        painter.fillRect(box_x, box_y, box_w, total_h, gradient)
        painter.setClipping(False)
        border_pen = QPen(QColor(255, 255, 255, 100))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 0)))
        painter.drawRoundedRect(box_x, box_y, box_w, total_h, 8, 8)
        logo_path = top_dir / "public" / "images" / "Monwatch-LOGO.png"
        if logo_path.exists():
            logo_pix = QPixmap(str(logo_path))
            logo_pix = logo_pix.scaledToHeight(logo_h, Qt.SmoothTransformation)
            painter.setOpacity(0.8)
            painter.drawPixmap(box_x + pad_x, box_y + pad_y - 2, logo_pix)
            painter.setOpacity(1.0)
        cur_y = box_y + pad_y + line_h
        for i, (text, font, color) in enumerate(lines):
            if not text:
                cur_y += 4
                continue
            painter.setFont(font)
            painter.setPen(color)
            fm = QFontMetrics(font)
            text_w = fm.horizontalAdvance(text)
            if i == 0 or i == 1:
                tx = box_x + text_start_x
            else:
                tx = box_x + pad_x
            painter.drawText(tx, cur_y, text)
            cur_y += line_h
        painter.end()
        return img

    def _add_normal_footer(self, img, name, min_lon, max_lon, min_lat, max_lat):
        from datetime import timezone as tzmod, timedelta
        from pathlib import Path
        w, h = img.width(), img.height()
        footer_h = 100
        from PySide6.QtGui import QFont, QFontMetrics, QPixmap, QColor
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, h - footer_h, w, footer_h, QColor(240, 240, 240))
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
        fs = max(8, min(12, w // 100))
        hf = QFont("Segoe UI", fs + 1, QFont.Bold)
        nf = QFont("Segoe UI", fs)
        logo_path = top_dir / "public" / "images" / "Monwatch-LOGO.png"
        logo_w = 0
        if logo_path.exists():
            logo_pix = QPixmap(str(logo_path))
            logo_pix = logo_pix.scaledToHeight(60, Qt.SmoothTransformation)
            logo_w = logo_pix.width()
            painter.drawPixmap(w - logo_w - 10, h - footer_h + 10, logo_pix)
        text_right = w - logo_w - 20 if logo_w > 0 else w - 10
        painter.setPen(QColor(30, 30, 30))
        painter.setFont(hf)
        name_w = painter.fontMetrics().horizontalAdvance(f"{name}")
        painter.drawText(20, h - footer_h + 30, f"{name}")
        painter.setFont(nf)
        painter.setPen(QColor(80, 80, 80))
        painter.drawText(20, h - footer_h + 55, time_str)
        painter.drawText(20, h - footer_h + 75, date_str)
        def _fmt_lon(l): return f"{abs(l):.1f}°{'E' if l >= 0 else 'W'}"
        def _fmt_lat(l): return f"{abs(l):.1f}°{'N' if l >= 0 else 'S'}"
        region_str = f"Region: {_fmt_lon(min_lon)} - {_fmt_lon(max_lon)}, {_fmt_lat(min_lat)} - {_fmt_lat(max_lat)}"
        region_w = painter.fontMetrics().horizontalAdvance(region_str)
        painter.drawText(text_right - region_w, h - footer_h + 30, region_str)
        painter.end()
        return img

    def _quick_generate(self):
        from pyproj import Transformer
        gv = self.main_ui.graphics_view
        scene = gv.scene()
        if not scene:
            return
        pix_item = None
        for item in reversed(scene.items()):
            if isinstance(item, QGraphicsPixmapItem):
                pix_item = item
                break
        if not pix_item:
            self.main_ui.log("QG: no image in scene")
            return
        vr = gv.mapToScene(gv.viewport().rect()).boundingRect()
        ix0 = vr.left() - pix_item.pos().x()
        iy0 = vr.top() - pix_item.pos().y()
        ix1 = vr.right() - pix_item.pos().x()
        iy1 = vr.bottom() - pix_item.pos().y()
        if ix0 < 0 or iy0 < 0 or ix1 > pix_item.pixmap().width() or iy1 > pix_item.pixmap().height():
            self.main_ui.log("QG: viewport extends beyond image")
            return
        gt = self.main_ui.current_geotransform
        nr = abs(gt.a)
        ne = abs(gt.c)
        ngw = int(round(2 * ne / nr))
        sf = ngw / max(pix_item.pixmap().width(), 1)
        def vp_to_lonlat(vx, vy):
            xn = (vx - pix_item.pos().x()) * sf
            yn = (vy - pix_item.pos().y()) * sf
            xp, yp = gt * (xn, yn)
            tr = Transformer.from_crs(self.main_ui.current_crs, "EPSG:4326", always_xy=True)
            return tr.transform(xp, yp)
        pts = [vp_to_lonlat(vr.left(), vr.top()), vp_to_lonlat(vr.right(), vr.top()),
               vp_to_lonlat(vr.right(), vr.bottom()), vp_to_lonlat(vr.left(), vr.bottom())]
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        self._zoom_to_and_capture(min(lons), max(lons), min(lats), max(lats), "Viewport", None)

    def _export_current_image(self, *args, **kwargs):
        from src.ui.dialogs import QuickGenerateDialog
        from datetime import datetime, timezone
        
        atcf_storms = getattr(self.main_ui, 'atcf_storms', [])
        atcf_age = getattr(self.main_ui, '_atcf_last_fetch_time', None)
        if not atcf_storms or (atcf_age and (datetime.now(timezone.utc) - atcf_age).total_seconds() > 1800):
            self.main_ui.log("QG: ATCF data stale or missing, fetching before dialog...")
            self.main_ui._fetch_and_not_show_atcf_storms()
            self.main_ui.log("QG: Waiting for ATCF fetch to complete...")
            import time
            while getattr(self.main_ui, '_atcf_busy', False):
                time.sleep(0.1)
                QApplication.processEvents()
        
        dlg = QuickGenerateDialog(self.main_ui)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        bbox, name, target_res, projection_mode, fmt = dlg.get_selection()
        if bbox is None:
            return
        
        is_flat = (projection_mode == "Flat Projection")
        
        if is_flat:
            opts_dlg = FlatProjectionOptionsDialog(self.main_ui, self.main_ui.settings)
            if opts_dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self._flat_opts = opts_dlg.get_options()
        else:
            self._flat_opts = None
        
        # Handle Full Disk selection — capture the current scene at its original size
        if bbox == "full_disk":
            self.main_ui.log(f"QG Full Disk: {name}")
            scene = self.main_ui.graphics_view.scene()
            if scene:
                # Compute Full Disk extent from satellite position for footer
                full_extent = self._get_full_disk_extent()
                min_lon, max_lon, min_lat, max_lat = full_extent
                # Temporarily hide target items
                target_items_visible = []
                if hasattr(self.main_ui, '_target_boxes'):
                    for rect, title, box_name, box_lon, box_lat in self.main_ui._target_boxes:
                        if rect.isVisible():
                            rect.setVisible(False); title.setVisible(False)
                            target_items_visible.append((rect, title))
                if hasattr(self.main_ui, '_target_popups'):
                    for proxy, widget in self.main_ui._target_popups:
                        proxy.setVisible(False)
                grid_coast_visible = []
                if is_flat:
                    grid_coast_visible = self._set_grid_coast_overlays_visible(False)
                # Render the full scene
                scene_rect = scene.sceneRect()
                img = QImage(int(scene_rect.width()), int(scene_rect.height()), QImage.Format_ARGB32)
                img.fill(Qt.black)
                painter = QPainter(img)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                scene.render(painter)
                painter.end()
                self._set_grid_coast_overlays_visible(True, grid_coast_visible)
                # Restore target items
                for rect, title in target_items_visible:
                    rect.setVisible(True); title.setVisible(True)
                for proxy in getattr(self.main_ui, '_target_popups', []):
                    proxy[0].setVisible(True)
                # Apply Flat Projection if selected
                if projection_mode == "Flat Projection":
                    self.main_ui.log(f"QG: Applying Flat Projection to Full Disk ...")
                    sw = scene_rect.width()
                    sh = scene_rect.height()
                    self._flatgen_async_img_full_disk(img, min_lon, max_lon, min_lat, max_lat,
                                                       sw, sh, name, fmt=fmt)
                    return
                safe_name = "".join(c if c.isalnum() or c in (' ','-','_') else '_' for c in name).strip()
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                export_dir = self.main_ui.settings.get("export_folder", "")
                if not export_dir:
                    export_dir = str(top_dir / "Exports")
                os.makedirs(export_dir, exist_ok=True)
                if fmt == "geotiff":
                    crs, gt = self._resolve_current_georef()
                    if crs is None or gt is None:
                        self.main_ui.log("QG Full Disk: no CRS/geotransform available for GeoTIFF")
                        return
                    img_gt, _ = display_geotransform(gt, int(scene_rect.width()))
                    qimg = img.convertToFormat(QImage.Format_RGBA8888)
                    arr = self._qimage_to_numpy(qimg)
                    h, w = arr.shape[:2]
                    tags = self._qg_metadata_tags(region_name=name,
                                                  bbox=(min_lon, max_lon, min_lat, max_lat),
                                                  crs=crs, transform=img_gt,
                                                  width=w, height=h)
                    fname = f"QG_{safe_name}_{ts}.tif"
                    fpath = os.path.join(export_dir, fname)
                    write_rgba_geotiff(fpath, arr, img_gt, crs, tags=tags)
                    self.main_ui.log(f"QG Full Disk GeoTIFF saved: {fpath}")
                    preview = self._add_tcid_style_footer(img, name, min_lon, max_lon, min_lat, max_lat)
                    fname_pv = f"QG_{safe_name}_{ts}_preview.png"
                    fpath_pv = os.path.join(export_dir, fname_pv)
                    preview.save(fpath_pv)
                else:
                    img = self._add_tcid_style_footer(img, name, min_lon, max_lon, min_lat, max_lat)
                    fname = f"QG_{safe_name}_{ts}.png"
                    fpath = os.path.join(export_dir, fname)
                    img.save(fpath)
                    self.main_ui.log(f"QG Full Disk saved: {fpath}")
            return
        
        # Use direct generation for target areas (center points)
        if isinstance(bbox, tuple) and len(bbox) == 2:
            lon, lat = bbox
            center_lon, center_lat = lon, lat
            
            # Get storm metadata if available
            category = None
            wind_str = None
            pressure_str = None
            for storm in atcf_storms:
                s_name = storm.get("storm_name", "")
                s_atcf_id = storm.get("atcf_id", "")
                if s_name == "INVEST" and s_atcf_id:
                    s_name = f"INVEST {s_atcf_id}"
                if s_name.strip().upper() == name.strip().upper():
                    category = storm.get("category", "")
                    vmax = storm.get("max_winds_kt")
                    wind_str = f"{vmax} kt" if vmax else ""
                    pres = storm.get("min_pressure")
                    pressure_str = f"{pres} hPa" if pres else ""
                    break
            
            # Try direct generation first (faster, crops during resample)
            self.main_ui.log(f"QG Direct: {name} ({lon:.1f}E, {lat:.1f}N) @ {target_res or 2000}m projection={projection_mode}")
            result = self._quick_generate_direct(bbox, name, target_res, center_lon, center_lat,
                                         category, wind_str, pressure_str, projection_mode,
                                         fmt=fmt)
            
            if result is _FLATGEN_PENDING:
                return
            if result:
                self.main_ui.log(f"QG Direct completed successfully: {result}")
                return
            else:
                self.main_ui.log("QG Direct failed, falling back to viewport capture method")
        
        # Fallback to viewport capture method for regions or if direct fails
        if isinstance(bbox, tuple) and len(bbox) == 2:
            lon, lat = bbox
            center_lon, center_lat = lon, lat
            margin = 3 if target_res is None else (target_res / 1000.0)
            min_lon, max_lon = lon - margin, lon + margin
            min_lat, max_lat = lat - margin, lat + margin
        else:
            min_lon, max_lon, min_lat, max_lat = bbox
            center_lon, center_lat = None, None
        
        self.main_ui.log(f"Generate (viewport): {name} ({min_lon:.1f}-{max_lon:.1f}E, {min_lat:.1f}-{max_lat:.1f}N) projection={projection_mode}")
        self._zoom_to_and_capture(min_lon, max_lon, min_lat, max_lat, name, target_res, center_lon, center_lat, projection_mode=projection_mode, fmt=fmt)

    def _get_full_disk_extent(self):
        try:
            sat_lon = 140.7
            if self.main_ui.current_crs is not None:
                try:
                    cf = self.main_ui.current_crs.to_cf()
                    sat_lon = float(cf.get("longitude_of_projection_origin", sat_lon))
                except Exception:
                    pass
        except Exception:
            sat_lon = 140.7
        crop_deg = 81
        return (sat_lon - crop_deg, sat_lon + crop_deg, -crop_deg, crop_deg)
