# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: controllers/satellite_controller.py
# Description: Satellite band acquisition, composite generation, and real-time data pipeline management for Himawari/GOES.
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
import io
import json
import uuid
import hashlib
import threading
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import importlib.util
import subprocess
import logging
import traceback
import re
import requests
import zipfile
import tempfile
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from scipy.ndimage import zoom
import xarray as xr
import rasterio
from pyproj import Transformer, CRS
import shapefile
import math
import time
from collections import OrderedDict, Counter, defaultdict
import gc
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import (
    QPoint, QPointF, Qt, QUrl, QMimeData, QObject, Signal, QThread,
    QSize, QTimer, QProcess, QDate, QDateTime, QEvent, QEventLoop,
    QStandardPaths, QSaveFile, QIODevice,
)
from PySide6.QtGui import (
    QPixmap, QIcon, QDrag, QMouseEvent,
    QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent,
    QWheelEvent, QImage, QPainter, QFont, QPen, QColor, QPainterPath,
    QImageReader, QPalette, QAction, QActionGroup, QCursor,
    QBrush, QKeySequence, QShortcut, QPolygonF, QFontMetrics,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QGroupBox, QRadioButton,
    QPushButton, QTabWidget, QStatusBar, QHBoxLayout, QLabel, QSplitter,
    QListWidget, QListWidgetItem, QTextEdit, QSlider, QFrame, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QSizePolicy, QGraphicsView, QGraphicsScene, QMenu,
    QProgressDialog, QMessageBox, QCheckBox, QSpinBox, QComboBox,
    QProgressBar, QDateEdit, QScrollArea, QButtonGroup, QDateTimeEdit,
    QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsPathItem, QGraphicsLineItem,
    QGraphicsEllipseItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsSimpleTextItem, QGridLayout,
    QDialog, QDialogButtonBox, QStackedWidget, QDoubleSpinBox, QLineEdit,
    QFormLayout, QToolButton, QSystemTrayIcon
)
from scipy.ndimage import zoom

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from src.core.engine_dispatcher import get_engine, get_products, get_tag_colors
from src.workers.core import PrecacheWorker, RawBandCacheWorker, CompositeWorker, ScenePreparationWorker
from src.core.helpers import top_dir, _get_nc_glob_pattern
from src.parsers.sataid_reader import is_sataid_file, list_sataid_files, parse_filename, read_sataid_cached
from src.managers.sataid_cache_manager import SataidCacheManager as SATAIDCache
from src.ui.dialogs import CachingDialog
from src.config.reader_manager import reader_manager
from src.data.prerequisite_loader import _collect_prerequisites_paths

try:
    from src.goes_cache import GOESCacheManager
except ImportError:
    try:
        from src.managers.goes_cache import GOESCacheManager
    except ImportError:
        GOESCacheManager = None


def _auto_range(data: np.ndarray, low_pct: float = 1.0, high_pct: float = 98.0):
    valid = data[np.isfinite(data)]
    if len(valid) == 0:
        return None, None
    p_low, p_high = np.percentile(valid, [low_pct, high_pct])
    if p_high - p_low < 1.0:
        return float(valid.min()), float(valid.max())
    return float(p_low), float(p_high)



# ---- PWARDS band ordering (moved from UI.py module scope) ----
_PWARDS_BAND_ORDER = [
    "VIS01", "VIS02", "VIS03", "VIS04", "VIS05", "VIS06",
    "IR07", "IR08", "IR09", "IR10", "IR11", "IR12", "IR13", "IR14", "IR15", "IR16",
]
_PWARDS_BAND_SORT_LOOKUP = {b: i for i, b in enumerate(_PWARDS_BAND_ORDER)}

def _PWARDS_BAND_SORT_KEY(band: str) -> int:
    return _PWARDS_BAND_SORT_LOOKUP.get(band, 999)

HAS_GEO = True


class SatelliteController(QObject):
    """Owns all satellite band / product / scene loading logic extracted from MainUI."""

    band_loaded = Signal(str, object)       # band_name, rgba_array
    composite_ready = Signal(str, object)   # product_key, rgba_array
    scene_ready = Signal(object)            # result dict
    progress = Signal(str, int)             # label, percent
    cache_finished = Signal()

    _IR_BANDS = {"B07", "B08", "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"}

    _BAND_CENTRAL_WAVELENGTHS = {
        "B01": 0.47, "B02": 0.51, "B03": 0.64, "B04": 0.86,
        "B05": 1.61, "B06": 2.26, "B07": 3.89, "B08": 6.25,
        "B09": 6.95, "B10": 7.35, "B11": 8.59, "B12": 9.64,
        "B13": 10.41, "B14": 11.24, "B15": 12.38, "B16": 13.30,
    }

    _COLORMAP_LIST = [
        "BT Enhanced (IR)", "Sandwich (IR)", "Dvorak (IR)", "Dvorak Experimental", "gray", "jet", "viridis", "plasma", "inferno", "magma", "coolwarm",
        "rainbow", "Spectral", "RdYlBu", "RdBu", "RdYlGn", "PiYG",
        "PRGn", "BrBG", "PuOr", "turbo", "nipy_spectral", "gist_ncar",
        "gist_rainbow", "gist_earth", "terrain", "ocean", "CMRmap",
        "hot", "cool", "copper", "bone", "pink", "spring", "summer",
        "autumn", "winter", "Wistia", "afmhot",
    ]

    def __init__(self, main_ui):
        super().__init__(main_ui)
        self.main_ui = main_ui

# ----------------------------------------
    #  log / settings shortcuts
# ----------------------------------------
    def log(self, msg):
        self.main_ui.log(msg)

    @property
    def settings(self):
        return self.main_ui.settings

# ----------------------------------------
    #  Helper: find ADS sidecar from a NC path
# ----------------------------------------
    def _current_nc_file(self):
        return getattr(self.main_ui, 'current_nc_path', None)

    def _find_ads_sidecar_from_ncpath(self, nc_path):
        return self.main_ui._find_ads_sidecar(nc_path)

    def _get_quality_grid(self):
        return self.main_ui._get_quality_grid()

    def _get_image_scene_pos(self):
        return self.main_ui._get_image_scene_pos()

    def _sync_gridded_native(self, arr):
        """Gridded (tile-LOD) mode always serves the band's NATIVE resolution:
        re-derive preview_max_px / preview_res_m from the actual array dims so
        the quality grid matches the source 1:1 (no upscale, no decimate)."""
        try:
            h, w = arr.shape[:2]
            if not h or not w:
                return
            dim = max(h, w)
            self.main_ui.preview_max_px = max(512, int(dim))
            half = self.main_ui.overlay_controller._compute_disk_half_extent()
            self.main_ui.preview_res_m = max(1, int(round(2 * half / dim)))
        except Exception:
            pass

    def _fit_array_to_quality_grid(self, arr):
        """Fit an RGBA array onto the current quality-grid canvas in array space.

        Mirror of ``overlay_controller._resize_for_fldk``'s quality-grid branch,
        but operating on a numpy array so the gridded tile layer can lay the
        source out at scene coordinates that exactly match the overlay grid.
        Returns ``(out_arr, scene_origin)``.
        """
        try:
            gq = self.main_ui._get_quality_grid()
            if gq is None:
                return arr, QPointF(0, 0)
            rw, rh = int(gq[0]), int(gq[1])
            h, w = arr.shape[:2]
            if w == rw and h == rh:
                return arr, QPointF(0, 0)
            gt = getattr(self.main_ui, 'current_geotransform', None)
            half = float(gq[3])
            if gt is not None and rw > 0 and rh > 0:
                is_fldk = abs(gt.c + half) < abs(half) * 0.01 and abs(gt.f - half) < abs(half) * 0.01
                if not is_fldk:
                    ox = int(round((float(gt.c) + half) / float(gq[2])))
                    oy = int(round((half - float(gt.f)) / float(gq[2])))
                    expected_w = max(1, int(round(2 * abs(gt.c) / abs(gt.a))))
                    expected_h = max(1, int(round(2 * abs(gt.f) / abs(gt.e))))
                    if w > expected_w or h > expected_h:
                        crop_x = max(0, ox)
                        crop_y = max(0, oy)
                        crop_w = min(expected_w, w - crop_x)
                        crop_h = min(expected_h, h - crop_y)
                        if crop_w > 0 and crop_h > 0 and crop_x < w and crop_y < h:
                            arr = arr[crop_y:crop_y + crop_h, crop_x:crop_x + crop_w]
                            h, w = crop_h, crop_w
                        src_x = 0
                        src_y = 0
                        dest_x = max(0, ox)
                        dest_y = max(0, oy)
                    else:
                        src_x = max(0, -ox)
                        src_y = max(0, -oy)
                        dest_x = max(0, ox)
                        dest_y = max(0, oy)
                    cw = min(w - src_x, rw - dest_x)
                    ch = min(h - src_y, rh - dest_y)
                    if cw > 0 and ch > 0 and ox < rw and oy < rh:
                        canvas = np.zeros((rh, rw, 4), dtype=np.uint8)
                        canvas[dest_y:dest_y + ch, dest_x:dest_x + cw] = arr[src_y:src_y + ch, src_x:src_x + cw]
                        return canvas, QPointF(0, 0)
            if w >= rw and h >= rh:
                return arr, QPointF(0, 0)
            from skimage.transform import resize as _skresize
            out = _skresize(arr, (rh, rw), preserve_range=True, anti_aliasing=True)
            return out.clip(0, 255).astype(np.uint8), QPointF(0, 0)
        except Exception:
            return arr, QPointF(0, 0)

    def _resize_for_fldk(self, pixmap):
        return self.main_ui._resize_for_fldk(pixmap)

    def _compute_native_res_m(self, w):
        return self.main_ui._compute_native_res_m(w)

    def _get_max_texture_size(self):
        return 0

    def _is_beta_viewport(self):
        return self.main_ui.settings.get("viewport_mode", "beta") == "beta"

    def _get_ref_grid_size(self):
        return self.main_ui.projection_service._get_ref_grid_size()

    def _prepare_cached_display(self, rgba_arr):
        return self.main_ui._prepare_cached_display(rgba_arr)

    @staticmethod
    def _sat_data_dir(input_dir, sat):
        """Local download-folder name for a satellite id.

        GK-2A scenes are saved by the downloader under ``gk2a-pds`` even though
        the satellite id is ``gk2a`` (mirroring the NOAA S3 bucket name).
        """
        if "gk2a" in (sat or "").lower() or "gk-2a" in (sat or "").lower():
            return Path(input_dir) / "gk2a-pds"
        return Path(input_dir) / sat

    def _update_winds_checkbox_state(self):
        self.main_ui._update_winds_checkbox_state()

# ----------------------------------------
    #  Band loading - main entry
# ----------------------------------------

    def load_bands_for_date(self):

        """Load available bands for specified date.

        Scans cache directory for available satellite bands on
        the given date. Populates UI with available band options
        and updates band metadata display.

        Args:
            date_str (str): Date in YYYY-MM-DD format.
            band (str | None): Optional specific band to load.

        Returns:
            list[Path]: List of available band file paths.

        Raises:
            ValueError: If date format is invalid.
            FileNotFoundError: If cache directory doesn't exist.

        Note:
            Himawari data is cached in 10-minute intervals.
        """
        self.main_ui._is_hsd_source = False
        sat = self.main_ui.sat_combo.currentData() or self.main_ui.sat_combo.currentText()
        year = self.main_ui.year_combo.currentText()
        month = self.main_ui.month_combo.currentText()
        day = self.main_ui.day_combo.currentText()
        hour = self.main_ui.hour_combo.currentText()
        minute = self.main_ui.minute_combo.currentText()
        self.main_ui.current_datetime = f"{year}_{month}_{day}_{hour}{minute}"
        self.log(f"Selected datetime: {self.main_ui.current_datetime}")

        base_path = self._sat_data_dir(self.main_ui.input_dir, sat)
        if not base_path.exists():
            self.log(f"Satellite directory not found: {base_path}")
            self.main_ui.status_bar.showMessage("Satellite directory not found")
            return

        matches = list(base_path.glob(f"*{self.main_ui.current_datetime}*"))

        if "himawari" in sat.lower() and hasattr(self.main_ui, 'type_combo'):
            product = self.main_ui.get_current_himawari_product()
            matches = [m for m in matches if product in m.name]

        if not matches and "goes" in sat.lower():
            try:
                y = int(year)
                m_int = int(month)
                d_int = int(day)
                doy_val = datetime(y, m_int, d_int).timetuple().tm_yday
                hh = hour if hour else ""
                goes_pattern = f"*{y}_{doy_val:03d}_{hh}*"
                matches = list(base_path.glob(goes_pattern))
                self.log(f"Trying GOES DOY pattern: {goes_pattern} -> {len(matches)} match(es)")
            except Exception as e:
                self.log(f"DOY fallback failed: {e}")

        # GK-2A quick-scene folders are named like <product>_YYYYMMDD_HHMM (compact,
        # with a variable product token that may even contain "/" -> nested dirs), or
        # gk2a-pds/<product>_YYYY_MM_DD_HHMM from date-range downloads. The scene's exact
        # scan time always lives in the filename suffix (_YYYYMMDDHHMM.nc), so first try
        # the direct globs, then fall back to a deep scan keyed on that trailing timestamp.
        if not matches and ("gk2a" in sat.lower() or "gk-2a" in sat.lower()):
            compact = f"{year}{month}{day}_{hour}{minute}"
            matches = list(base_path.glob(f"*{compact}*"))
            if not matches:
                underscore = f"{year}_{month}_{day}_{hour}{minute}"
                matches = list(base_path.glob(f"*{underscore}*"))
            if not matches:
                ts = f"{year}{month}{day}{hour}{minute}"
                scene_dirs = {}
                for f in Path(self.main_ui.input_dir).rglob("gk2a_*.nc"):
                    m_gk = re.search(r'_(\d{12})\.nc$', f.name)
                    if m_gk and m_gk.group(1) == ts:
                        scene_dirs[str(f.parent)] = f.parent
                matches = list(scene_dirs.values())
            self.log(f"Trying GK-2A compact pattern: *{compact}* -> {len(matches)} match(es)")

        # Japan/Target rapid-scan: sub-minute observation times (0002/0042...) map
        # to their containing 10-minute slot folder.
        if not matches and "himawari" in sat.lower() and hasattr(self.main_ui, 'type_combo') \
                and self.main_ui.type_combo.currentText().lower() in ("japan", "target"):
            _slot = self._resolve_rapid_slot_folder(base_path, f"{hour}{minute}")
            if _slot is not None:
                self.log(f"[Rapid] {self.main_ui.current_datetime} -> slot folder {_slot.name}")
                matches = [_slot]

        if not matches:
            self.log(f"No data folder found for {self.main_ui.current_datetime}")
            self.main_ui.status_bar.showMessage(f"No data for {self.main_ui.current_datetime}")
            return

        data_folder = matches[0]
        sat = self.main_ui.sat_combo.currentText()
        is_goes = "goes" in sat.lower()

        if is_goes and self._load_goes_satpy(data_folder):
            return

        # GOES: dedicated multi-file cache system
        self.main_ui._band_nc_map = {}
        self.main_ui._goes_cache = None
        if is_goes:
            try:
                self.main_ui._goes_cache = GOESCacheManager(
                    data_folder, runtime_cache=self.main_ui.cache, settings=self.main_ui.settings,
                    log_func=lambda msg: self.log(f"[GOES] {msg}")
                )
                if hasattr(self.main_ui._goes_cache.loader, 'max_px'):
                    self.main_ui._goes_cache.loader.max_px = self.main_ui.preview_max_px
                band_names = self.main_ui._goes_cache.get_band_names()
                self.main_ui._band_nc_map = self.main_ui._goes_cache.get_file_map()
            except Exception:
                self.main_ui._band_nc_map = self.main_ui._extract_goes_bands_from_folder(data_folder)
                band_names = sorted(self.main_ui._band_nc_map.keys()) if self.main_ui._band_nc_map else []
                self.main_ui._goes_cache = None

            if band_names:
                nc_path = list(self.main_ui._band_nc_map.values())[0] if self.main_ui._band_nc_map else None
                self.log(f"GOES detected: {len(band_names)} bands from {len(self.main_ui._band_nc_map)} NC files")
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                self.main_ui.current_crs = self.main_ui._goes_cache.get_crs() if self.main_ui._goes_cache else None
                self.main_ui.current_geotransform = self.main_ui._goes_cache.get_geotransform() if self.main_ui._goes_cache else None
                gt2 = self.main_ui._goes_cache.get_geotransform_2km() if self.main_ui._goes_cache else None
                if gt2 is not None:
                    self.main_ui.current_geotransform = gt2
                self.main_ui._overlay_geo_unavailable = self.main_ui.current_crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)

                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                if hasattr(self.main_ui, '_display_image_cache'):
                    self.main_ui._display_image_cache.clear()
                if hasattr(self.main_ui, '_resized_display_cache'):
                    self.main_ui._resized_display_cache.clear()

                for w in getattr(self.main_ui, '_active_cache_workers', []):
                    if w:
                        try: w.cancel()
                        except RuntimeError: pass
                for t in getattr(self.main_ui, '_active_cache_threads', []):
                    if t is None: continue
                    try:
                        alive = t.isRunning()
                    except RuntimeError:
                        alive = False
                    if alive:
                        try:
                            t.quit()
                            t.wait(1000)
                        except RuntimeError:
                            pass
                self.main_ui._active_cache_workers = []
                self.main_ui._active_cache_threads = []

                for attr_w, attr_t in [('raw_cache_worker', 'raw_cache_thread'),
                                        ('raw_cache_worker2', 'raw_cache_thread2')]:
                    w = getattr(self.main_ui, attr_w, None)
                    t = getattr(self.main_ui, attr_t, None)
                    if w:
                        try:
                            w.band_cached.disconnect()
                            w.finished.disconnect()
                            w.progress.disconnect()
                        except Exception:
                            pass
                        try:
                            w.cancel()
                        except Exception:
                            pass
                    if t:
                        try:
                            if t.isRunning():
                                t.quit()
                                t.wait(1000)
                        except RuntimeError:
                            pass
                        try:
                            t.deleteLater()
                        except RuntimeError:
                            pass
                        setattr(self.main_ui, attr_t, None)
                    setattr(self.main_ui, attr_w, None)

                if hasattr(self.main_ui, 'scene_prep_thread') and self.main_ui.scene_prep_thread:
                    try:
                        if self.main_ui.scene_prep_thread.isRunning():
                            self.main_ui.scene_prep_thread.quit()
                            self.main_ui.scene_prep_thread.wait(2000)
                    except RuntimeError:
                        pass
                    try:
                        self.main_ui.scene_prep_thread.deleteLater()
                    except RuntimeError:
                        pass
                    self.main_ui.scene_prep_thread = None
                if hasattr(self.main_ui, 'scene_prep_worker'):
                    self.main_ui.scene_prep_worker = None

                self.main_ui._build_band_checkboxes(band_names)

                self.log(f"Metadata loaded for {self.main_ui.current_datetime}. Available bands: {band_names}")
                self.main_ui.status_bar.showMessage(f"Ready to load bands for {self.main_ui.current_datetime}")

                import time as _t
                self.main_ui._dialog_opened_at = _t.perf_counter()
                self.main_ui._suppress_display_until_cache_done = True
                self.main_ui.loading_dialog = CachingDialog(self.main_ui)
                self.main_ui.loading_dialog.show()
                self.main_ui.loading_dialog.repaint()
                QApplication.processEvents()

                try:
                    self.main_ui.status_bar.showMessage(f"Loaded {len(band_names)} GOES bands -- using GOES cache...")
                    self.main_ui._build_band_ui_immediately(band_names, nc_path)
                    if band_names:
                        if hasattr(self.main_ui, '_populate_professional_bands'):
                            self.main_ui._populate_professional_bands(band_names)
                    else:
                        self.main_ui.load_preview_images()
                except Exception as e:
                    self.log(f"Error during GOES band UI setup: {e}")
                    if hasattr(self.main_ui, 'loading_dialog'):
                        self.main_ui.loading_dialog.close()

                if band_names and self.main_ui._goes_cache:
                    _preferred = ["B01", "B02", "B04", "B05", "B06", "B07", "B08",
                                  "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]
                    first_band = next((b for b in _preferred if b in band_names), band_names[0])
                    self.main_ui._goes_preload_bands(band_names, first_band)
                elif band_names:
                    self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map) if self.main_ui._band_nc_map else self.main_ui.load_preview_images()
                else:
                    self.main_ui.load_preview_images()
                return

        pattern = _get_nc_glob_pattern(sat)
        nc_hits = list(data_folder.glob(pattern))
        if not nc_hits:
            nc_hits = list(data_folder.glob("*.nc"))

        is_himawari = "himawari" in sat.lower()
        jma_detected = False

        if is_himawari and nc_hits:
            from src.parsers.himawari_jaxa_parser import is_himawari_jaxa_nc, extract_himawari_jaxa_bands, extract_himawari_jaxa_crs
            _sample = nc_hits[0]
            if is_himawari_jaxa_nc(_sample):
                self.log("Detected Himawari JAXA NC format")
                jma_detected = True
                self.main_ui._band_nc_map = extract_himawari_jaxa_bands(nc_hits)
                band_names = sorted(self.main_ui._band_nc_map.keys())
                nc_path = next(iter(self.main_ui._band_nc_map.values())) if self.main_ui._band_nc_map else nc_hits[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                _jma_crs, _jma_gt = extract_himawari_jaxa_crs(nc_path)
                self.main_ui.current_crs = _jma_crs
                self.main_ui.current_geotransform = _jma_gt
                # Also read ADS sidecar for ref_grid_size
                _c, _t = self.main_ui.extract_crs_from_ads(nc_path)
                if _c and _t:
                    self.main_ui.current_crs = _c
                    self.main_ui.current_geotransform = _t
                self.main_ui._overlay_geo_unavailable = _jma_crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.log(f"JAXA NC bands: {band_names}")
                self.main_ui.status_bar.showMessage(f"JAXA NC: {len(band_names)} bands for {self.main_ui.current_datetime}")
                if band_names:
                    self.main_ui.loading_dialog = CachingDialog(self.main_ui)
                    self.main_ui.loading_dialog.show()
                    self.main_ui.loading_dialog.repaint()
                    QApplication.processEvents()
                    self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                return

            from src.parsers.himawari_jma_parser import is_himawari_jma_nc, parse_himawari_jma_metadata, extract_himawari_jma_bands, read_himawari_jma_band_data, extract_himawari_jma_crs
            if is_himawari_jma_nc(_sample):
                self.log("Detected Himawari JMA NC format (lon/lat dims)")
                jma_detected = True
                self.main_ui._band_nc_map = extract_himawari_jma_bands(nc_hits)
                band_names = sorted(self.main_ui._band_nc_map.keys())
                nc_path = next(iter(self.main_ui._band_nc_map.values())) if self.main_ui._band_nc_map else nc_hits[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                _jma_crs, _jma_gt = extract_himawari_jma_crs(nc_path)
                self.main_ui.current_crs = _jma_crs
                self.main_ui.current_geotransform = _jma_gt
                # Also read ADS sidecar for ref_grid_size
                _c, _t = self.main_ui.extract_crs_from_ads(nc_path)
                if _c and _t:
                    self.main_ui.current_crs = _c
                    self.main_ui.current_geotransform = _t
                self.main_ui._overlay_geo_unavailable = _jma_crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.log(f"JMA NC bands: {band_names}")
                self.main_ui.status_bar.showMessage(f"JMA NC: {len(band_names)} bands for {self.main_ui.current_datetime}")
                if band_names:
                    self.main_ui.loading_dialog = CachingDialog(self.main_ui)
                    self.main_ui.loading_dialog.show()
                    self.main_ui.loading_dialog.repaint()
                    QApplication.processEvents()
                    self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                return

        if not jma_detected and nc_hits and any("_B" in f.name for f in nc_hits):
            if not is_goes and is_himawari:
                _sample_nc = next((f for f in nc_hits if "_B" in f.name), nc_hits[0])
                if self._find_ads_sidecar_from_ncpath(_sample_nc) is None:
                    self.log(f"NC files found but no sidecar - using HSD/DAT for CRS")
                    if self._load_himawari_hsd_satpy(data_folder):
                        return
            import re as _re
            self.main_ui._band_nc_map = {}
            _nc_area_map = {}
            for f in nc_hits:
                m = _re.search(r'_B(\d{2})_', f.name)
                if m:
                    self.main_ui._band_nc_map[f"B{m.group(1)}"] = f
                    _am = _re.search(r'_(R\d{3}|JP\d{2})_', f.name)
                    if _am:
                        _nc_area_map.setdefault(_am.group(1).upper(), []).append(f)
            if len(_nc_area_map) > 1:
                # Target/Japan multi-sub-area folders hold one NC set per sub-area.
                # Deterministically pick the smallest token (e.g. R301) so the CRS/GT
                # and discovered bands always come from a fixed segment.
                _pick = min(_nc_area_map)
                _pick_names = {f.name for f in _nc_area_map[_pick]}
                self.main_ui._band_nc_map = {
                    b: f for b, f in self.main_ui._band_nc_map.items() if f.name in _pick_names
                }
                self.log(f"[NC] Multiple sub-areas in {data_folder.name}: {sorted(_nc_area_map)} - using {_pick}")
            band_names = sorted(self.main_ui._band_nc_map.keys())
            nc_path = next(iter(self.main_ui._band_nc_map.values())) if self.main_ui._band_nc_map else None
            self.log(f"Detected per-band NC format: {len(self.main_ui._band_nc_map)} files")
        else:
            if not nc_hits:
                old_pattern = "*_AHI.nc"
                nc_hits = list(data_folder.glob(old_pattern))
            if not nc_hits and is_goes:
                for fb in ["*C*.nc", "*.nc"]:
                    nc_hits = list(data_folder.glob(fb))
                    if nc_hits:
                        break
            if not nc_hits:
                if not is_goes and "himawari" in sat.lower():
                    self.log(f"No NC files - trying HSD .DAT direct loading via satpy...")
                    if self._load_himawari_hsd_satpy(data_folder):
                        return
                self.log(f"No NC files found in {data_folder.name}.")
                self.main_ui.status_bar.showMessage(f"No matching NC for {sat}")
                return
            nc_path = nc_hits[0]
            self.main_ui._band_nc_map = {}
            is_gk2a = "gk2a" in sat.lower() or "gk-2a" in sat.lower()
            if is_gk2a:
                # GK-2A scenes are native per-band NetCDF files (no .ads sidecar,
                # no B-vars inside a single NC) — enumerate bands from filenames.
                from src.parsers.gk2a_parser import extract_gk2a_bands
                self.main_ui._band_nc_map = extract_gk2a_bands(nc_hits)
                band_names = sorted(self.main_ui._band_nc_map.keys())
                if self.main_ui._band_nc_map:
                    nc_path = next(iter(self.main_ui._band_nc_map.values()))
                    self.log(f"GK-2A per-band NC format: {len(self.main_ui._band_nc_map)} files -> {band_names}")
            else:
                sidecar = self._find_ads_sidecar_from_ncpath(nc_path)
                band_names = []
                if sidecar is not None:
                    try:
                        with open(sidecar, "r", encoding="utf-8") as f:
                            ads_data = json.load(f)
                        band_names = sorted(ads_data.get("bands_loaded", []))
                    except Exception:
                        pass
                if not band_names:
                    try:
                        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
                            band_names = sorted(v for v in ds.data_vars if v.startswith("B") and len(v) == 3)
                    except Exception:
                        pass

        self.main_ui.available_bands = band_names
        self.main_ui.current_nc_path = nc_path
        _sat_text = (self.main_ui.sat_combo.currentText().lower() if hasattr(self.main_ui, 'sat_combo') else "")
        _sat_id = ((self.main_ui.sat_combo.currentData() or "").lower() if hasattr(self.main_ui, 'sat_combo') else "")
        _is_gk2a = "gk2a" in _sat_text or "gk2a" in _sat_id or "gk-2a" in _sat_id
        _is_meteosat = "meteosat" in _sat_text or "meteosat" in _sat_id
        _is_mtsat = "mtsat" in _sat_text or "mtsat" in _sat_id
        if _is_gk2a:
            from src.parsers.gk2a_parser import extract_gk2a_crs
            self.main_ui.current_crs, self.main_ui.current_geotransform = extract_gk2a_crs(nc_path)
        elif _is_meteosat:
            from src.parsers.meteosat_parser import extract_meteosat_crs
            self.main_ui.current_crs, self.main_ui.current_geotransform = extract_meteosat_crs(nc_path)
        elif _is_mtsat:
            from src.parsers.mtsat_parser import extract_mtsat_crs, extract_mtsat_extent
            self.main_ui.current_crs, self.main_ui.current_geotransform = extract_mtsat_crs(nc_path)
            extent = extract_mtsat_extent(nc_path)
            if extent:
                self.main_ui._display_projection_extent = extent
                self.main_ui._current_display_projection = "plate_carree"
                self.main_ui._display_projection_crs = self.main_ui.current_crs
        else:
            self.main_ui.current_crs = None
            self.main_ui.current_geotransform = None
            # Himawari/PWARDS NCs keep their georeferencing in the .ads.json sidecar.
            if nc_path is not None:
                _c2, _t2 = self.main_ui.extract_crs_from_ads(nc_path)
                if _c2 and _t2:
                    self.main_ui.current_crs = _c2
                    self.main_ui.current_geotransform = _t2
        self.main_ui._overlay_geo_unavailable = self.main_ui.current_crs is None
        self.main_ui._ir_kelvin = None
        self.main_ui.current_winds_uv = None
        self.main_ui.winds_enabled = False
        self.main_ui._wind_proj_cache.clear()
        self._update_winds_checkbox_state()
        if hasattr(self.main_ui, 'update_devkit_band_lists'):
            self.main_ui.update_devkit_band_lists(band_names)

        self.main_ui.cache.clear_raw()
        self.main_ui.cache.clear_rgb()
        self.main_ui.cache.clear_precached()
        if hasattr(self.main_ui, '_display_image_cache'):
            self.main_ui._display_image_cache.clear()
        if hasattr(self.main_ui, '_resized_display_cache'):
            self.main_ui._resized_display_cache.clear()

        for w in getattr(self.main_ui, '_active_cache_workers', []):
            if w:
                try: w.cancel()
                except RuntimeError: pass
        for t in getattr(self.main_ui, '_active_cache_threads', []):
            if t is None: continue
            try:
                alive = t.isRunning()
            except RuntimeError:
                alive = False
            if alive:
                try:
                    t.quit()
                    t.wait(1000)
                except RuntimeError:
                    pass
        self.main_ui._active_cache_workers = []
        self.main_ui._active_cache_threads = []

        for attr_w, attr_t in [('raw_cache_worker', 'raw_cache_thread'),
                                ('raw_cache_worker2', 'raw_cache_thread2')]:
            w = getattr(self.main_ui, attr_w, None)
            t = getattr(self.main_ui, attr_t, None)
            if w:
                try:
                    w.band_cached.disconnect()
                    w.finished.disconnect()
                    w.progress.disconnect()
                except Exception:
                    pass
                try:
                    w.cancel()
                except Exception:
                    pass
            if t:
                try:
                    if t.isRunning():
                        t.quit()
                        t.wait(1000)
                except RuntimeError:
                    pass
                try:
                    t.deleteLater()
                except RuntimeError:
                    pass
                setattr(self.main_ui, attr_t, None)
            setattr(self.main_ui, attr_w, None)

        if hasattr(self.main_ui, 'scene_prep_thread') and self.main_ui.scene_prep_thread:
            try:
                if self.main_ui.scene_prep_thread.isRunning():
                    self.main_ui.scene_prep_thread.quit()
                    self.main_ui.scene_prep_thread.wait(2000)
            except RuntimeError:
                pass
            try:
                self.main_ui.scene_prep_thread.deleteLater()
            except RuntimeError:
                pass
            self.main_ui.scene_prep_thread = None
        if hasattr(self.main_ui, 'scene_prep_worker'):
            self.main_ui.scene_prep_worker = None

        self.main_ui._build_band_checkboxes(band_names)

        self.log(f"Metadata loaded for {self.main_ui.current_datetime}. Available bands: {band_names}")
        self.main_ui.status_bar.showMessage(f"Ready to load bands for {self.main_ui.current_datetime}")

        import time as _t
        self.main_ui._dialog_opened_at = _t.perf_counter()
        self.main_ui._suppress_display_until_cache_done = True
        self.log(f"Display suppressed at dialog open -- will only show after ALL bands cached")
        self.main_ui.loading_dialog = CachingDialog(self.main_ui)
        self.main_ui.loading_dialog.show()
        self.main_ui.loading_dialog.repaint()
        QApplication.processEvents()

        try:
            self.main_ui.status_bar.showMessage(f"Loaded {len(band_names)} bands from {nc_path.name} -- preparing in background...")

            bfm = getattr(self.main_ui, '_band_nc_map', {}) or None
            self.log(f"[DEBUG] Creating ScenePreparationWorker for {nc_path}")
            self.main_ui.scene_prep_worker = ScenePreparationWorker(nc_path, self.main_ui.settings.get("lazy_nc", True), band_file_map=bfm)
            self.main_ui.scene_prep_thread = QThread()
            self.main_ui.scene_prep_worker.moveToThread(self.main_ui.scene_prep_thread)
            self.main_ui.scene_prep_worker.prepared.connect(self.main_ui._on_scene_prepared)
            self.main_ui.scene_prep_worker.error.connect(lambda msg: self.log(f"Scene prep error: {msg}"))
            self.main_ui.scene_prep_thread.started.connect(self.main_ui.scene_prep_worker.run)
            self.main_ui.scene_prep_worker.prepared.connect(self.main_ui.scene_prep_thread.quit)
            self.main_ui.scene_prep_worker.prepared.connect(self.main_ui.scene_prep_worker.deleteLater)
            self.main_ui.scene_prep_worker.progress.connect(lambda msg: self.main_ui.loading_dialog.setLabelText(msg))

            self.main_ui._build_band_ui_immediately(band_names, nc_path)

            if band_names:
                if hasattr(self.main_ui, '_populate_professional_bands'):
                    self.main_ui._populate_professional_bands(band_names)
            else:
                self.main_ui.load_preview_images()

            self.log(f"[DEBUG] Starting ScenePreparationWorker thread")
            self.main_ui.scene_prep_thread.start()
            self.log(f"[DEBUG] ScenePreparationWorker thread started")
        except Exception as e:
            self.log(f"Error during band loading setup: {e}")
            if hasattr(self.main_ui, 'loading_dialog'):
                self.main_ui.loading_dialog.close()

        if band_names:
            _preferred = ["B01", "B02", "B04", "B05", "B06", "B07", "B08",
                          "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]
            first_band = next((b for b in _preferred if b in band_names), band_names[0])
            cb = self.main_ui.band_checkboxes.get(first_band)
            if cb:
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)

            self.log(f"First band {first_band} selected -- display suppressed until all bands cached.")

        else:
            self.main_ui.load_preview_images()

    def load_date_metadata(self):
        sat = self.main_ui.sat_combo.currentData() or self.main_ui.sat_combo.currentText()
        year = self.main_ui.year_combo.currentText()
        month = self.main_ui.month_combo.currentText()
        day = self.main_ui.day_combo.currentText()
        hour = self.main_ui.hour_combo.currentText()
        minute = self.main_ui.minute_combo.currentText()
        self.main_ui.current_datetime = f"{year}_{month}_{day}_{hour}{minute}"
        self.log(f"Selected datetime (metadata only): {self.main_ui.current_datetime}")

        base_path = self._sat_data_dir(self.main_ui.input_dir, sat)
        if not base_path.exists():
            self.log(f"Satellite directory not found: {base_path}")
            self.main_ui.status_bar.showMessage("Satellite directory not found")
            return

        matches = list(base_path.glob(f"*{self.main_ui.current_datetime}*"))

        if "himawari" in sat.lower() and hasattr(self.main_ui, 'type_combo'):
            product = self.main_ui.get_current_himawari_product()
            matches = [m for m in matches if product in m.name]

        if not matches and "goes" in sat.lower():
            try:
                y = int(year)
                m_int = int(month)
                d_int = int(day)
                doy_val = datetime(y, m_int, d_int).timetuple().tm_yday
                hh = hour if hour else ""
                goes_pattern = f"*{y}_{doy_val:03d}_{hh}*"
                matches = list(base_path.glob(goes_pattern))
                self.log(f"Trying GOES DOY pattern: {goes_pattern} -> {len(matches)} match(es)")
            except Exception as e:
                self.log(f"DOY fallback failed: {e}")

        # GK-2A quick-scene folders are named like <product>_YYYYMMDD_HHMM (compact,
        # with a variable product token that may even contain "/" -> nested dirs), or
        # gk2a-pds/<product>_YYYY_MM_DD_HHMM from date-range downloads. The scene's exact
        # scan time always lives in the filename suffix (_YYYYMMDDHHMM.nc), so first try
        # the direct globs, then fall back to a deep scan keyed on that trailing timestamp.
        if not matches and ("gk2a" in sat.lower() or "gk-2a" in sat.lower()):
            compact = f"{year}{month}{day}_{hour}{minute}"
            matches = list(base_path.glob(f"*{compact}*"))
            if not matches:
                underscore = f"{year}_{month}_{day}_{hour}{minute}"
                matches = list(base_path.glob(f"*{underscore}*"))
            if not matches:
                ts = f"{year}{month}{day}{hour}{minute}"
                scene_dirs = {}
                for f in Path(self.main_ui.input_dir).rglob("gk2a_*.nc"):
                    m_gk = re.search(r'_(\d{12})\.nc$', f.name)
                    if m_gk and m_gk.group(1) == ts:
                        scene_dirs[str(f.parent)] = f.parent
                matches = list(scene_dirs.values())
            self.log(f"Trying GK-2A compact pattern: *{compact}* -> {len(matches)} match(es)")

        # Japan/Target rapid-scan: sub-minute observation times (0002/0042...) map
        # to their containing 10-minute slot folder.
        if not matches and "himawari" in sat.lower() and hasattr(self.main_ui, 'type_combo') \
                and self.main_ui.type_combo.currentText().lower() in ("japan", "target"):
            _slot = self._resolve_rapid_slot_folder(base_path, f"{hour}{minute}")
            if _slot is not None:
                self.log(f"[Rapid] {self.main_ui.current_datetime} -> slot folder {_slot.name}")
                matches = [_slot]

        if not matches:
            self.log(f"No data folder found for {self.main_ui.current_datetime}")
            self.main_ui.status_bar.showMessage(f"No data for {self.main_ui.current_datetime}")
            return

        data_folder = matches[0]
        is_goes = "goes" in sat.lower()
        is_gk2a = "gk2a" in sat.lower() or "gk-2a" in sat.lower()

        if is_gk2a:
            # GK-2A quick-scene folders hold native per-band NetCDF files
            # (gk2a_ami_le1b_<code>_*_<YYYYMMDDHHMM>.nc) — build the band->file map
            # from those filenames and georeference from GK-2A's GEOS attributes.
            from src.parsers.gk2a_parser import extract_gk2a_bands, extract_gk2a_crs
            nc_hits = list(data_folder.glob("*.nc"))
            band_nc_map = extract_gk2a_bands(nc_hits)
            if band_nc_map:
                band_names = sorted(band_nc_map.keys())
                nc_path = next(iter(band_nc_map.values()))
                self.main_ui._band_nc_map = band_nc_map
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                crs, gt = extract_gk2a_crs(nc_path)
                self.main_ui.current_crs = crs
                self.main_ui.current_geotransform = gt
                self.main_ui._overlay_geo_unavailable = crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.log(f"GK-2A metadata loaded: {band_names}")
                self.main_ui.status_bar.showMessage(
                    f"GK-2A ready: {len(band_names)} bands for {self.main_ui.current_datetime}")
                if band_names:
                    self.main_ui.loading_dialog = CachingDialog(self.main_ui)
                    self.main_ui.loading_dialog.show()
                    self.main_ui.loading_dialog.repaint()
                    QApplication.processEvents()
                    self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=band_nc_map)
                return

        if is_goes:
            band_nc_map = self.main_ui._extract_goes_bands_from_folder(data_folder)
            if band_nc_map:
                band_names = sorted(band_nc_map.keys())
                nc_path = list(band_nc_map.values())[0]
                self.main_ui._band_nc_map = band_nc_map
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                self.main_ui.current_crs = None
                self.main_ui.current_geotransform = None
                self.main_ui._overlay_geo_unavailable = False
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self._update_winds_checkbox_state()
                _ads = self._find_ads_sidecar_from_ncpath(nc_path)
                if _ads is not None:
                    try:
                        with open(_ads, "r", encoding="utf-8") as _f:
                            _ad = json.load(_f)
                        _rw = _ad.get("ref_grid_w")
                        _rh = _ad.get("ref_grid_h")
                        self.main_ui._ref_grid_size = (_rw, _rh) if (_rw and _rh) else None
                        _rw1 = _ad.get("ref_grid_1km_w")
                        _rh1 = _ad.get("ref_grid_1km_h")
                        self.main_ui._ref_grid_1km = (_rw1, _rh1) if (_rw1 and _rh1) else None
                        _rw05 = _ad.get("ref_grid_0_5km_w")
                        _rh05 = _ad.get("ref_grid_0_5km_h")
                        self.main_ui._ref_grid_0_5km = (_rw05, _rh05) if (_rw05 and _rh05) else None
                    except Exception:
                        pass
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui._build_band_checkboxes(band_names)
                self.log(f"Metadata loaded for {self.main_ui.current_datetime}. Available bands: {band_names}")
                self.main_ui.status_bar.showMessage(f"Ready to load bands for {self.main_ui.current_datetime}")
                return

        pattern = _get_nc_glob_pattern(sat)
        nc_hits = list(data_folder.glob(pattern))
        if not nc_hits:
            nc_hits = list(data_folder.glob("*.nc"))

        is_himawari = "himawari" in sat.lower()
        jma_detected = False

        if is_himawari and nc_hits:
            from src.parsers.himawari_jaxa_parser import is_himawari_jaxa_nc, extract_himawari_jaxa_bands, extract_himawari_jaxa_crs
            _sample = nc_hits[0]
            if is_himawari_jaxa_nc(_sample):
                self.log("Detected Himawari JAXA NC format in metadata")
                jma_detected = True
                self.main_ui._band_nc_map = extract_himawari_jaxa_bands(nc_hits)
                band_names = sorted(self.main_ui._band_nc_map.keys())
                nc_path = next(iter(self.main_ui._band_nc_map.values())) if self.main_ui._band_nc_map else nc_hits[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                _jma_crs, _jma_gt = extract_himawari_jaxa_crs(nc_path)
                self.main_ui.current_crs = _jma_crs
                self.main_ui.current_geotransform = _jma_gt
                self.main_ui._overlay_geo_unavailable = _jma_crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.log(f"JAXA NC bands: {band_names}")
                self.main_ui.status_bar.showMessage(f"JAXA NC: {len(band_names)} bands for {self.main_ui.current_datetime}")
                if band_names:
                    self.main_ui.loading_dialog = CachingDialog(self.main_ui)
                    self.main_ui.loading_dialog.show()
                    self.main_ui.loading_dialog.repaint()
                    QApplication.processEvents()
                    self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                return

            from src.parsers.himawari_jma_parser import is_himawari_jma_nc, extract_himawari_jma_bands, extract_himawari_jma_crs
            if is_himawari_jma_nc(_sample):
                self.log("Detected Himawari JMA NC format (lon/lat dims) in metadata")
                jma_detected = True
                self.main_ui._band_nc_map = extract_himawari_jma_bands(nc_hits)
                band_names = sorted(self.main_ui._band_nc_map.keys())
                nc_path = next(iter(self.main_ui._band_nc_map.values())) if self.main_ui._band_nc_map else nc_hits[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                _jma_crs, _jma_gt = extract_himawari_jma_crs(nc_path)
                self.main_ui.current_crs = _jma_crs
                self.main_ui.current_geotransform = _jma_gt
                self.main_ui._overlay_geo_unavailable = _jma_crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui._build_band_checkboxes(band_names)
                self.log(f"JMA NC metadata: {band_names}")
                self.main_ui.status_bar.showMessage(f"JMA NC: {len(band_names)} bands for {self.main_ui.current_datetime}")
                return

        if not jma_detected and nc_hits and any("_B" in f.name for f in nc_hits):
            import re as _re
            self.main_ui._band_nc_map = {}
            _nc_area_map = {}
            for f in nc_hits:
                m = _re.search(r'_B(\d{2})_', f.name)
                if m:
                    self.main_ui._band_nc_map[f"B{m.group(1)}"] = f
                    _am = _re.search(r'_(R\d{3}|JP\d{2})_', f.name)
                    if _am:
                        _nc_area_map.setdefault(_am.group(1).upper(), []).append(f)
            if len(_nc_area_map) > 1:
                # Target/Japan multi-sub-area folders now hold one NC set per sub-area.
                # Deterministically pick the smallest token (e.g. R301) so the CRS/GT
                # and discovered bands always come from a fixed segment.
                _pick = min(_nc_area_map)
                _pick_names = {f.name for f in _nc_area_map[_pick]}
                self.main_ui._band_nc_map = {
                    b: f for b, f in self.main_ui._band_nc_map.items() if f.name in _pick_names
                }
                self.log(f"[NC] Multiple sub-areas in {data_folder.name}: {sorted(_nc_area_map)} - using {_pick}")
            band_names = sorted(self.main_ui._band_nc_map.keys())
            nc_path = next(iter(self.main_ui._band_nc_map.values())) if self.main_ui._band_nc_map else None
            self.main_ui.current_nc_path = nc_path
        else:
            if not nc_hits:
                old_pattern = "*_AHI.nc"
                nc_hits = list(data_folder.glob(old_pattern))
            if not nc_hits and is_goes:
                for fb in ["*C*.nc", "*.nc"]:
                    nc_hits = list(data_folder.glob(fb))
                    if nc_hits:
                        break
            if not nc_hits:
                if not is_goes:
                    _dat = (list(data_folder.glob("*.dat")) + list(data_folder.glob("*.DAT")))
                    if _dat:
                        band_names = sorted(set(
                            f"B{m.group(1)}" for f in _dat
                            for m in [__import__("re").search(r'_B(\d{2})_', f.name)]
                            if m
                        ))
                        if band_names:
                            self.main_ui._band_nc_map = {}
                            self.main_ui.current_nc_path = data_folder
                            self.main_ui.current_crs = None
                            self.main_ui.current_geotransform = None
                            self.main_ui._overlay_geo_unavailable = True
                            self.main_ui.available_bands = band_names
                            self.main_ui._build_band_checkboxes(band_names)
                            self.log(f"Metadata loaded from .DAT filenames: {band_names}")
                            self.main_ui.status_bar.showMessage(f"HSD bands: {len(band_names)} for {self.main_ui.current_datetime}")
                            return
                self.log(f"No NC files found in {data_folder.name}.")
                self.main_ui.status_bar.showMessage(f"No matching NC for {sat}")
                return
            nc_path = nc_hits[0]
            self.main_ui.current_nc_path = nc_path
            self.main_ui._band_nc_map = {}
            sidecar = self._find_ads_sidecar_from_ncpath(nc_path)
            band_names = []
            if sidecar is not None:
                try:
                    with open(sidecar, "r", encoding="utf-8") as f:
                        ads_data = json.load(f)
                    band_names = sorted([b.strip() for b in ads_data.get("bands_loaded", [])])
                    _rw = ads_data.get("ref_grid_w")
                    _rh = ads_data.get("ref_grid_h")
                    self.main_ui._ref_grid_size = (_rw, _rh) if (_rw and _rh) else None
                    _rw1 = ads_data.get("ref_grid_1km_w")
                    _rh1 = ads_data.get("ref_grid_1km_h")
                    self.main_ui._ref_grid_1km = (_rw1, _rh1) if (_rw1 and _rh1) else None
                    _rw05 = ads_data.get("ref_grid_0_5km_w")
                    _rh05 = ads_data.get("ref_grid_0_5km_h")
                    self.main_ui._ref_grid_0_5km = (_rw05, _rh05) if (_rw05 and _rh05) else None
                except Exception:
                    pass

        self.main_ui.available_bands = band_names
        _sat_text = (self.main_ui.sat_combo.currentText().lower() if hasattr(self.main_ui, 'sat_combo') else "")
        _sat_id = ((self.main_ui.sat_combo.currentData() or "").lower() if hasattr(self.main_ui, 'sat_combo') else "")
        _is_gk2a = "gk2a" in _sat_text or "gk2a" in _sat_id or "gk-2a" in _sat_id
        _is_meteosat = "meteosat" in _sat_text or "meteosat" in _sat_id
        _is_mtsat = "mtsat" in _sat_text or "mtsat" in _sat_id
        if _is_gk2a:
            from src.parsers.gk2a_parser import extract_gk2a_crs
            self.main_ui.current_crs, self.main_ui.current_geotransform = extract_gk2a_crs(nc_path)
        elif _is_meteosat:
            from src.parsers.meteosat_parser import extract_meteosat_crs
            self.main_ui.current_crs, self.main_ui.current_geotransform = extract_meteosat_crs(nc_path)
        elif _is_mtsat:
            from src.parsers.mtsat_parser import extract_mtsat_crs, extract_mtsat_extent
            self.main_ui.current_crs, self.main_ui.current_geotransform = extract_mtsat_crs(nc_path)
            extent = extract_mtsat_extent(nc_path)
            if extent:
                self.main_ui._display_projection_extent = extent
                self.main_ui._current_display_projection = "plate_carree"
                self.main_ui._display_projection_crs = self.main_ui.current_crs
        else:
            self.main_ui.current_crs = None
            self.main_ui.current_geotransform = None
            # Himawari/PWARDS NCs keep their georeferencing in the .ads.json sidecar.
            if nc_path is not None:
                _c2, _t2 = self.main_ui.extract_crs_from_ads(nc_path)
                if _c2 and _t2:
                    self.main_ui.current_crs = _c2
                    self.main_ui.current_geotransform = _t2
        self.main_ui._overlay_geo_unavailable = self.main_ui.current_crs is None
        self.main_ui._ir_kelvin = None
        self.main_ui.current_winds_uv = None
        self.main_ui.winds_enabled = False
        self.main_ui._wind_proj_cache.clear()
        self._update_winds_checkbox_state()
        if hasattr(self.main_ui, 'update_devkit_band_lists'):
            self.main_ui.update_devkit_band_lists(band_names)

        self.main_ui._build_band_checkboxes(band_names)

        self.log(f"Metadata loaded for {self.main_ui.current_datetime}. Available bands: {band_names}")
        self.main_ui.status_bar.showMessage(f"Ready to load bands for {self.main_ui.current_datetime}")

    def load_selected_band_or_product(self):

        """Load selected band or RGB product.

        Loads the user-selected spectral band or generates the
        selected RGB composite product. Handles both single-band
        and multi-band composite operations.

        Args:
            band_id (str): Band identifier or product name.
            timestamp (datetime): Acquisition timestamp.

        Returns:
            QImage: Loaded or generated image.

        Side Effects:
            - Updates viewport display
            - Updates band info panel
            - May trigger cache operations
        """
        if getattr(self.main_ui, '_suppress_display_until_cache_done', False):
            self.log(f"[Suppress] load_selected_band_or_product blocked -- cache still in progress")
            return
        is_sataid = (hasattr(self.main_ui, 'type_combo') and
                     self.main_ui.type_combo.currentText() == "SATAID")
        is_pwards = (hasattr(self.main_ui, 'sat_combo') and
                     self.main_ui.sat_combo.currentData() == "pwards" and
                     hasattr(self.main_ui, 'type_combo') and
                     self.main_ui.type_combo.currentText() == "Stream")
        selected_band = next((b for b, cb in self.main_ui.band_checkboxes.items() if cb.isChecked()), None)
        self.main_ui._current_band_name = selected_band or getattr(self.main_ui, 'selected_product', '')
        if selected_band and (is_sataid or is_pwards):
            self.main_ui._load_sataid_band_file(selected_band)
            return
        if selected_band:
            nc_path = self._current_nc_file()
            if not nc_path:
                return
            arr = self.main_ui.cache.get_precached(selected_band)
            if arr is None:
                gc = getattr(self.main_ui, '_goes_cache', None)
                if gc is not None:
                    disp = gc.load_band_for_display(selected_band)
                    if disp is not None:
                        arr = disp
                        self.main_ui.cache.put_precached(selected_band, arr)
                if arr is None:
                    raw = self.main_ui.cache.get_raw(selected_band)
                    if raw is not None:
                        try:
                            display_cache = getattr(self.main_ui, '_display_image_cache', None)
                            if display_cache is None:
                                from collections import OrderedDict
                                display_cache = OrderedDict()
                                self.main_ui._display_image_cache = display_cache
                            cached_disp = display_cache.get(selected_band)
                            _vis = self.main_ui.settings.get("visualizer", "Full Disk")
                            if (cached_disp is not None and
                                    cached_disp.get('max_px') == self.main_ui.preview_max_px
                                    and _vis == "Full Disk"):
                                arr = cached_disp['arr']
                            else:
                                band_num = int(selected_band.replace("B", ""))
                                sat_text = (self.main_ui.sat_combo.currentText().lower() if hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo else "")
                                sat_id = ((self.main_ui.sat_combo.currentData() or "").lower() if hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo else "")
                                is_goes = bool(getattr(self.main_ui, '_band_nc_map', None)) and "goes" in sat_text
                                is_gk2a = "gk2a" in sat_text or "gk2a" in sat_id or "gk-2a" in sat_id
                                is_meteosat = "meteosat" in sat_text or "meteosat" in sat_id
                                _eng_satdep = get_engine(self.main_ui.sat_combo.currentText())
                                scale, offset = _eng_satdep._get_band_scale_offset(nc_path, selected_band)
                                if is_goes and scale == 1.0 and offset == 0.0:
                                    if 1 <= band_num <= 6:
                                        try:
                                            import xarray as xr, re, math
                                            from pathlib import Path
                                            bfm = getattr(self.main_ui, '_band_nc_map', {})
                                            fp = bfm.get(selected_band, nc_path)
                                            with xr.open_dataset(fp, mask_and_scale=False) as _ds:
                                                esun = float(_ds["esun"].values) if "esun" in _ds else 0.0
                                            if esun > 0.0:
                                                doy = 166
                                                m = re.search(r'_s(\d{4})(\d{3})', Path(str(fp)).name)
                                                if m: doy = int(m.group(2))
                                                d = 1.0 / math.sqrt(1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0))
                                                raw_cal = np.where(np.isfinite(raw), (math.pi * raw) / (esun * d * d), np.nan)
                                            else:
                                                raw_cal = raw.copy()
                                        except Exception:
                                            raw_cal = raw.copy()
                                        vmin, vmax = _auto_range(raw_cal)
                                        if vmin is None:
                                            vmin, vmax = 0.0, 100.0
                                    elif 7 <= band_num <= 16:
                                        try:
                                            import xarray as xr
                                            bfm = getattr(self.main_ui, '_band_nc_map', {})
                                            fp = bfm.get(selected_band, nc_path)
                                            with xr.open_dataset(fp, mask_and_scale=False) as _ds:
                                                fk1 = float(_ds["planck_fk1"].values) if "planck_fk1" in _ds else 0.0
                                                fk2 = float(_ds["planck_fk2"].values) if "planck_fk2" in _ds else 0.0
                                            if fk1 > 0.0 and fk2 > 0.0:
                                                raw_cal = np.where(np.isfinite(raw) & (raw > 0),
                                                                   fk2 / np.log(fk1 / raw + 1.0), np.nan)
                                            else:
                                                raw_cal = raw.copy()
                                        except Exception:
                                            raw_cal = raw.copy()
                                        vmin, vmax = _auto_range(raw_cal)
                                        if vmin is None:
                                            vmin, vmax = 180.0, 330.0
                                    else:
                                        raw_cal = raw.copy()
                                        vmin, vmax = None, None
                                elif is_gk2a or is_meteosat:
                                    raw_cal = raw.copy()
                                    vmin, vmax = _auto_range(raw_cal)
                                else:
                                    raw_cal = raw * scale + offset
                                    vmin, vmax = _auto_range(raw_cal)
                                    if vmin is None:
                                        if 1 <= band_num <= 6:
                                            vmin, vmax = 0.0, 100.0
                                        elif 7 <= band_num <= 16:
                                            vmin, vmax = 180.0, 330.0
                                        else:
                                            vmin, vmax = None, None
                                _eng_satdep = get_engine(self.main_ui.sat_combo.currentText())
                                alpha = _eng_satdep._earth_mask([raw_cal])
                                gray = _eng_satdep._linear(raw_cal, vmin, vmax, gamma=1.0)
                                if alpha.shape != gray.shape:
                                    alpha = alpha[:gray.shape[0], :gray.shape[1]]
                                arr = np.stack([gray, gray, gray, alpha], axis=-1)
                                if display_cache is not None:
                                    display_cache[selected_band] = {
                                        'arr': arr,
                                        'max_px': self.main_ui.preview_max_px,
                                        'shape': raw.shape,
                                    }
                                    display_cache.move_to_end(selected_band)
                                    if len(display_cache) > 16:
                                        display_cache.popitem(last=False)
                            self.log(f"Displaying {selected_band} (display cache hit)" if cached_disp and _vis == "Full Disk" else f"Displaying {selected_band} from raw cache (computed on-the-fly)")
                        except Exception as e:
                            arr = None
                            self.log(f"Raw->display conversion failed: {e}")

            if arr is None:
                _cache_running = getattr(self.main_ui, '_raw_cache_threads_running', 0) > 0
                if _cache_running:
                    self.main_ui.status_bar.showMessage(f"Loading {selected_band}... (caching in background)")
                    self.log(f"Deferring {selected_band} display -- cache worker still populating")
                    return

            if arr is None:
                arr = get_engine(self.main_ui.sat_combo.currentText()).band_as_image(nc_path, selected_band, self.main_ui.preview_max_px)
            if arr is not None:
                h, w = arr.shape[:2]
                if getattr(self.main_ui, 'preview_quality', None) == "gridded res":
                    self._sync_gridded_native(arr)
                if not self.main_ui.current_crs or not self.main_ui.current_geotransform:
                    _nc = self._current_nc_file()
                    if _nc:
                        _c, _t = self.main_ui.extract_crs_from_ads(_nc)
                        if _c and _t:
                            self.main_ui.current_crs = _c
                            self.main_ui.current_geotransform = _t
                            self.main_ui._overlay_geo_unavailable = False
                _is_goes = False
                _sat_text = (self.main_ui.sat_combo.currentText().lower() if hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo else "")
                _sat_id = ((self.main_ui.sat_combo.currentData() or "").lower() if hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo else "")
                if "goes" in _sat_text:
                    _is_goes = True
                elif not _is_goes and getattr(self.main_ui, '_band_nc_map', None) and not ("gk2a" in _sat_text or "gk2a" in _sat_id or "meteosat" in _sat_text or "meteosat" in _sat_id):
                    _is_goes = True
                if not _is_goes:
                    _nc = self._current_nc_file()
                    if _nc and "goes" in str(_nc).lower():
                        _is_goes = True
                if _is_goes:
                    if self.main_ui.preview_max_px == 0:
                        target_w, target_h = w, h
                    else:
                        gq = self._get_quality_grid()
                        if gq is not None:
                            target_w, target_h = int(gq[0]), int(gq[1])
                        else:
                            target_w, target_h = w, h
                    if target_w != w or target_h != h:
                        _resize_key = (selected_band, w, h, target_w, target_h)
                        cached_resize = self.main_ui._resized_display_cache.get(_resize_key)
                        if cached_resize is not None:
                            arr = cached_resize
                        else:
                            zh = target_h / h
                            zw = target_w / w
                            if abs(zh - 1.0) < 0.001 and abs(zw - 1.0) < 0.001:
                                pass
                            else:
                                from scipy.ndimage import zoom as _z
                                arr = _z(arr, (zh, zw, 1), order=1).clip(0, 255).astype(np.uint8)
                            self.main_ui._resized_display_cache[_resize_key] = arr
                        h, w = target_h, target_w
                else:
                    _gq = self._get_quality_grid()
                    if _gq is not None:
                        _nr = self._compute_native_res_m(w)
                        if _nr is not None and _gq[2] > 0:
                            _scale = _nr / _gq[2]
                            if _scale > 1.01:
                                nw = max(1, int(round(w * _scale)))
                                nh = max(1, int(round(h * _scale)))
                                _resize_key = (selected_band, w, h, _gq[2], 'up')
                                _cached = self.main_ui._resized_display_cache.get(_resize_key)
                                if _cached is not None:
                                    arr = _cached
                                else:
                                    try:
                                        from skimage.transform import resize as _res
                                        arr = _res(arr, (nh, nw), preserve_range=True, anti_aliasing=True).clip(0, 255).astype(np.uint8)
                                    except (ImportError, ModuleNotFoundError):
                                        from scipy.ndimage import zoom as _zoom
                                        zh = nh / h
                                        zw = nw / w
                                        arr = _zoom(arr, (zh, zw, 1), order=1).clip(0, 255).astype(np.uint8)
                                    self.main_ui._resized_display_cache[_resize_key] = arr
                                h, w = nh, nw
                                self.log(f"[Resize] Upsampled band {w}x{h} (scale={_scale:.3f})")
                    elif _gq is None and hasattr(self.main_ui, '_ref_grid_size') and self.main_ui._ref_grid_size:
                        _rw, _rh = self.main_ui._ref_grid_size
                        if _rw > 0 and _rh > 0 and (w > _rw or h > _rh):
                            _sf = min(_rw / w, _rh / h)
                            if _sf < 0.99:
                                nw = max(1, int(round(w * _sf)))
                                nh = max(1, int(round(h * _sf)))
                                _resize_key = (selected_band, w, h, _rw, _rh)
                                cached_resize = self.main_ui._resized_display_cache.get(_resize_key)
                                if cached_resize is not None:
                                    arr = cached_resize
                                else:
                                    try:
                                        from skimage.transform import resize
                                        arr = resize(arr, (nh, nw), preserve_range=True, anti_aliasing=True).clip(0, 255).astype(np.uint8)
                                    except (ImportError, ModuleNotFoundError):
                                        from scipy.ndimage import zoom as _z
                                        zh = nh / h
                                        zw = nw / w
                                        arr = _z(arr, (zh, zw, 1), order=1).clip(0, 255).astype(np.uint8)
                                    self.main_ui._resized_display_cache[_resize_key] = arr
                                h, w = nh, nw
                                self.log(f"[Resize] Decimated band image {w}x{h} (factor={_sf:.3f})")
                    _max_tex = self._get_max_texture_size()
                    if _max_tex > 0 and (h > _max_tex or w > _max_tex):
                        _scale = min(_max_tex / w, _max_tex / h)
                        _nw = max(1, int(round(w * _scale)))
                        _nh = max(1, int(round(h * _scale)))
                        if _nw != w or _nh != h:
                            try:
                                from skimage.transform import resize as _hwresize
                                arr = _hwresize(arr, (_nh, _nw), preserve_range=True, anti_aliasing=True).clip(0, 255).astype(np.uint8)
                            except (ImportError, ModuleNotFoundError):
                                from scipy.ndimage import zoom as _hwzoom
                                zh = _nh / h
                                zw = _nw / w
                                arr = _hwzoom(arr, (zh, zw, 1), order=1).clip(0, 255).astype(np.uint8)
                            gt = self.main_ui.current_geotransform
                            if gt is not None:
                                _sfx = w / _nw
                                _sfy = h / _nh
                                self.main_ui.current_geotransform = rasterio.Affine(
                                    gt.a * _sfx, gt.b, gt.c,
                                    gt.d, gt.e * _sfy, gt.f
                                )
                            self.log(f"[Resize] GPU tex limit: {w}x{h} -> {_nw}x{_nh}")
                            h, w = _nh, _nw
                import time as _t
                _t0 = _t.perf_counter()

                _t1 = _t.perf_counter()
                raw_bytes = arr.tobytes()
                _t2 = _t.perf_counter()

                qimg = QImage(raw_bytes, w, h, w * 4, QImage.Format_RGBA8888)
                _t3 = _t.perf_counter()

                pixmap = QPixmap.fromImage(qimg)
                _t4 = _t.perf_counter()

                self.main_ui.current_original = nc_path
                self.main_ui.current_base = f"{nc_path.stem}_{selected_band}"
                if _is_goes:
                    scene_pos = QPointF(0, 0)
                else:
                    _gq = self._get_quality_grid()
                    _pre_placed = _gq is not None and w == _gq[0] and h == _gq[1]
                    if not _pre_placed:
                        pixmap = self._resize_for_fldk(pixmap)
                        scene_pos = self._get_image_scene_pos()
                    else:
                        scene_pos = QPointF(0, 0)
                self.main_ui._visualizer_reprojecting = False
                self.main_ui._reproject_cache.clear()
                self.main_ui._current_visualizer_mode = None
                if getattr(self.main_ui, 'preview_quality', None) == "gridded res":
                    _src, _sp = self._fit_array_to_quality_grid(np.ascontiguousarray(arr))
                    self.main_ui.graphics_view.set_tiled_image(
                        _src, preserve_view=True, scene_pos=_sp)
                else:
                    self.main_ui.graphics_view.set_image(pixmap, preserve_view=True, quality_level=1.0, is_original=False, scene_pos=scene_pos)
                self.main_ui.graphics_view.viewport().update()
                QApplication.processEvents()
                self.main_ui.graphics_view.viewport().repaint()
                self.main_ui._push_texture_to_multi_globe()
                _t5 = _t.perf_counter()

                self.log(f"[Timing] {selected_band}: shape={w}x{h} | bytes={_t2-_t1:.1f}ms qimg={_t3-_t2:.1f}ms pixmap={_t4-_t3:.1f}ms view+repaint={_t5-_t4:.1f}ms total={_t5-_t0:.1f}ms")
                import time as _t
                click = getattr(self.main_ui, '_click_log', None)
                if click and click[0] == selected_band:
                    dt_ms = (_t.perf_counter() - click[1]) * 1000.0
                    self.log(f"Displayed - {selected_band} (after {dt_ms:.1f}ms from click, includes paint)")
                    self.main_ui._click_log = None
                else:
                    self.log(f"Displaying band {selected_band}")
                self.main_ui.status_bar.showMessage(f"{selected_band}")
                self.main_ui.update_band_info()
                _aor_active = any(
                    getattr(getattr(self.main_ui, f'aor_{_tag}_cb', None), 'isChecked', lambda: False)()
                    for _tag in ('par', 'jma', 'tcad', 'tcid', 'fir', 'custom')
                ) or bool(getattr(self.main_ui, 'aor_overlay_items', None))
                _track_active = any(
                    _t.get("visible", True) for _t in getattr(self.main_ui, 'tracks', []) or []
                ) or bool(getattr(self.main_ui, 'track_overlay_items', None))
                if (self.main_ui.grid_enabled or self.main_ui.coast_enabled
                        or self.main_ui.winds_enabled
                        or getattr(self.main_ui, 'nhc_storms', None)
                        or _aor_active or _track_active):
                    scene = self.main_ui.graphics_view.scene()
                    has_grid = len(getattr(self.main_ui, 'grid_overlay_items', [])) > 0
                    has_coast = len(getattr(self.main_ui, 'coast_overlay_items', [])) > 0
                    if not has_grid and not has_coast:
                        self.main_ui.update_overlays()
                    else:
                        self.main_ui._update_overlays_static()
                if getattr(self.main_ui, 'winds_enabled', False):
                    self.main_ui.load_winds_data()
                self._zoom_to_sector_region()
                return
        elif self.main_ui.selected_product:
            self.generate_rgb_product(self.main_ui.selected_product)

    def _load_himawari_hsd_satpy(self, data_folder: Path) -> bool:
        self.main_ui._is_hsd_source = True
        import re as _re
        import gc as _gc
        from collections import Counter, defaultdict
        import numpy as np

        dat_files = (
            list(data_folder.glob("*.dat"))
            + list(data_folder.glob("*.DAT"))
        )
        if not dat_files:
            self.main_ui._is_hsd_source = False
            return False

        try:
            from satpy import Scene
            HAS_SATPY = True
        except ImportError:
            self.log("[HSD] satpy not installed - cannot load .DAT files directly.")
            self.main_ui._is_hsd_source = False
            return False

        if not getattr(self.main_ui, "loading_dialog", None):
            self.main_ui.loading_dialog = CachingDialog(self.main_ui, title="Loading HSD Data",
                                                        main_text="Reading Himawari .DAT files via satpy...")
            self.main_ui.loading_dialog.show()
            self.main_ui.loading_dialog.repaint()
            QApplication.processEvents()

        sector = "FLDK"
        name_upper = data_folder.name.upper()
        for p in [data_folder] + list(data_folder.parents):
            pu = p.name.upper()
            if "JAPAN" in pu:
                sector = "Japan"; break
            if "TARGET" in pu:
                sector = "Target"; break
            if "FLDK" in pu:
                sector = "FLDK"; break

        self.log(f"[HSD] Detected sector: {sector}  ({len(dat_files)} .DAT files)")

        filenames = [str(f) for f in dat_files]
        all_bands = [f"B{i:02d}" for i in range(1, 17)]
        loaded_data: dict = {}
        area_def = None

        if sector == "FLDK":
            # Use dynamic reader selection instead of hardcoded "ahi_hsd"
            reader_name = reader_manager.get_appropriate_reader(filenames)
            scn = Scene(reader=reader_name, filenames=filenames)
            try:
                scn.load(all_bands)
                loaded_bands = [b for b in all_bands if b in scn]
            except Exception:
                loaded_bands = []
                for b in all_bands:
                    try:
                        scn.load([b])
                        loaded_bands.append(b)
                    except Exception:
                        pass
            if not loaded_bands and scn.available_dataset_names():
                try:
                    avail = list(scn.available_dataset_names())[:12]
                    scn.load(avail)
                    loaded_bands = list(set(loaded_bands) | set(avail))
                except Exception:
                    pass
            # Collect prerequisite paths for loaded bands
            if loaded_bands:
                # Extract datasets from loaded bands for prerequisite analysis
                band_datasets = []
                for b in loaded_bands:
                    try:
                        da = scn[b]
                        # For xarray DataArray, we can use the dataset or the dataarray itself
                        # The prerequisite loader works with datasets that have the right attributes
                        if hasattr(da, 'dataset'):
                            band_datasets.append(da.dataset)
                        else:
                            band_datasets.append(da)
                    except Exception as e:
                        logger.debug(f"Could not extract dataset for band {b}: {e}")

                # Collect prerequisite paths
                prerequisite_paths = _collect_prerequisites_paths(band_datasets)
                if prerequisite_paths:
                    logger.info(f"Prerequisite loader found {len(prerequisite_paths)} prerequisite files: {prerequisite_paths}")
                    # In a full implementation, we would load these files here
                    # For now, we just log them
            for b in loaded_bands:
                da = scn[b]
                loaded_data[b] = da.values.astype(np.float32)
                if area_def is None and "area" in da.attrs:
                    area_def = da.attrs["area"]
        else:
            micro_groups = defaultdict(list)
            for f in dat_files:
                fn = f.name
                for i in range(1, 17):
                    bn = f"B{i:02d}"
                    if f"_{bn}_" in fn:
                        m = _re.search(r'_(R\d{3}|JP\d{2})_', fn)
                        area_tok = m.group(1) if m else "UNK"
                        micro_groups[(bn, area_tok)].append(str(f))
                        break
            if not micro_groups:
                micro_groups[("ALL", "UNK")] = filenames

            all_segs: dict = {}
            for (b, area), fg in micro_groups.items():
                try:
                    # Collect prerequisite paths for this group's files
                    if fg:
                        group_datasets = []
                        # For now, we'll just log that we're checking prerequisites for this group
                        # A full implementation would extract datasets from the files
                        logger.debug(f"Checking prerequisites for group {b}-{area} with {len(fg)} files")

                    # Use dynamic reader selection instead of hardcoded "ahi_hsd"
                    reader_name = reader_manager.get_appropriate_reader(fg)
                    scn_b = Scene(reader=reader_name, filenames=fg)
                    scn_b.load([b])
                    if b in scn_b:
                        da = scn_b[b]
                        all_segs.setdefault(b, {})[area] = (
                            da.values.astype(np.float32),
                            da.attrs.get("area"),
                        )
                    del scn_b; _gc.collect()
                except Exception:
                    pass

            area_cnt: Counter = Counter()
            for b, segs in all_segs.items():
                for a in segs:
                    area_cnt[a] += 1
            best = area_cnt.most_common(1)[0][0] if area_cnt else "UNK"

            for b, segs in all_segs.items():
                if best in segs:
                    arr, a_def = segs[best]
                    loaded_data[b] = arr
                    if area_def is None and a_def is not None:
                        area_def = a_def

            loaded_bands = list(loaded_data.keys())
            self.log(f"[HSD] Japan/Target: best sub-area={best}, {len(loaded_bands)} bands")

        if not loaded_bands:
            self.log("[HSD] satpy could not load any bands from .DAT files.")
            if getattr(self.main_ui, "loading_dialog", None):
                try:
                    self.main_ui.loading_dialog.close()
                except Exception:
                    pass
                self.main_ui.loading_dialog = None
            self.main_ui._is_hsd_source = False
            return False

        band_names = sorted(loaded_data.keys())
        self.log(f"[HSD] Satpy loaded {len(band_names)} bands: {band_names}")

        crs = None
        transform = None
        if area_def is not None:
            try:
                from pyproj import CRS as _CRS
                import rasterio
                pd = area_def.proj_dict
                lo = pd.get("lon_0", 140.7)
                hh = pd.get("h", 35785863.0)
                sw = pd.get("sweep", "x")
                swp = f" +sweep={sw}" if sw else ""
                p4 = (
                    f"+proj=geos +lon_0={lo} +h={hh} +x_0=0 +y_0=0 "
                    f"+ellps=WGS84{swp} +units=m +no_defs"
                )
                crs = _CRS.from_proj4(p4)
                x_ll, y_ll, x_ur, y_ur = area_def.area_extent
                nr, nc = area_def.shape
                rx = (x_ur - x_ll) / nc
                ry = (y_ur - y_ll) / nr
                transform = rasterio.transform.Affine(rx, 0.0, x_ll, 0.0, -ry, y_ur)
                self.log(f"[HSD] CRS extracted from satpy area definition")
            except Exception as exc:
                self.log(f"[HSD] CRS extraction failed: {exc}")

        for attr_w, attr_t in [("raw_cache_worker", "raw_cache_thread"),
                               ("raw_cache_worker2", "raw_cache_thread2")]:
            w = getattr(self.main_ui, attr_w, None)
            t = getattr(self.main_ui, attr_t, None)
            if w:
                try:
                    w.band_cached.disconnect()
                    w.finished.disconnect()
                    w.progress.disconnect()
                except Exception:
                    pass
                try:
                    w.cancel()
                except Exception:
                    pass
            if t:
                if t.isRunning():
                    t.quit()
                    t.wait(1000)
                t.deleteLater()
                setattr(self.main_ui, attr_t, None)
            setattr(self.main_ui, attr_w, None)
        for a in ("scene_prep_thread", "scene_prep_worker"):
            o = getattr(self.main_ui, a, None)
            if o:
                try:
                    if a == "scene_prep_thread" and o.isRunning():
                        o.quit()
                        o.wait(2000)
                    o.deleteLater()
                except Exception:
                    pass
                setattr(self.main_ui, a, None)

        self.main_ui.current_crs = crs
        self.main_ui.current_geotransform = transform
        self.main_ui._ref_grid_size = (area_def.shape[1], area_def.shape[0]) if area_def else None
        # Store source area_def from satpy for reprojection
        self.main_ui._source_area_def = area_def
        self.main_ui.current_nc_path = data_folder
        self.main_ui._band_nc_map = {}
        self.main_ui._overlay_geo_unavailable = crs is None
        self.main_ui._ir_kelvin = None
        self.main_ui.current_winds_uv = None
        self.main_ui.winds_enabled = False
        self.main_ui._wind_proj_cache.clear()
        self._update_winds_checkbox_state()
        if hasattr(self.main_ui, "update_devkit_band_lists"):
            self.main_ui.update_devkit_band_lists(band_names)

        self.main_ui.cache.clear_raw()
        self.main_ui.cache.clear_rgb()
        self.main_ui.cache.clear_precached()
        if hasattr(self.main_ui, "_display_image_cache"):
            self.main_ui._display_image_cache.clear()
        if hasattr(self.main_ui, "_resized_display_cache"):
            self.main_ui._resized_display_cache.clear()
        for band, arr in loaded_data.items():
            self.main_ui.cache.put_raw(band, arr)

        self.main_ui.available_bands = band_names
        self.main_ui._build_band_checkboxes(band_names)

        self.log(f"[HSD] Loaded {len(band_names)} bands via satpy - ready.")
        self.main_ui.status_bar.showMessage(f"HSD loaded: {len(band_names)} bands for {self.main_ui.current_datetime}")

        _preferred = ["B01", "B02", "B04", "B05", "B06", "B07", "B08",
                      "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]
        first_band = next((b for b in _preferred if b in band_names), band_names[0])
        cb = self.main_ui.band_checkboxes.get(first_band)
        if cb:
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)

        self.main_ui.current_nc_path = data_folder
        try:
            import xarray as _xr
            _ts = getattr(self.main_ui, "current_datetime", "unknown")
            _prod = data_folder.name[:-(len(_ts) + 1)] if data_folder.name.endswith(_ts) else data_folder.name
            for _bn, _arr in loaded_data.items():
                _nc = data_folder / f"{_prod}_{_bn}_{_ts}.nc"
                if not _nc.exists():
                    _ds = _xr.Dataset({_bn: (("y", "x"), _arr)})
                    _ds[_bn].attrs["scale_factor"] = np.float32(1.0)
                    _ds[_bn].attrs["add_offset"] = np.float32(0.0)
                    _ds[_bn].attrs["_FillValue"] = np.nan
                    _ds.to_netcdf(str(_nc))
                    _ds.close()
            self.main_ui.current_nc_path = data_folder / f"{_prod}_{band_names[0]}_{_ts}.nc"
            self.log(f"[HSD] Cached {len(loaded_data)} bands to per-band NC files")
        except Exception as _exc:
            self.log(f"[HSD] NC write skipped: {_exc}")

        if hasattr(self.main_ui, "_start_overlay_precache"):
            self.main_ui._start_overlay_precache(data_folder)

        self.main_ui._raw_bands_processed = set(band_names)
        self.main_ui._raw_cache_threads_running = 0
        if hasattr(self.main_ui, "_cache_watchdog"):
            try:
                self.main_ui._cache_watchdog.stop()
            except Exception:
                pass
        self._check_and_close_loading_dialog()

        if hasattr(self.main_ui, "_populate_professional_bands"):
            self.main_ui._populate_professional_bands(band_names)
        return True

    def _load_goes_satpy(self, data_folder: Path) -> bool:
        """Load GOES ABI L1b NetCDF files via satpy Scene(reader='abi_l1b').

        Uses satpy's ABI L1b reader to load all available bands, CRS,
        and geotransform in one shot instead of opening each NC file
        manually with xarray.

        Returns True on success. Returns False if satpy is unavailable,
        no GOES files are found, or the satpy load fails — letting
        callers fall back to the manual xarray loading path.
        """
        import re as _re
        import gc as _gc
        from collections import defaultdict

        nc_files = list(data_folder.glob("*_ABI*.nc"))
        if not nc_files:
            nc_files = list(data_folder.glob("*.nc"))
        if not nc_files:
            return False

        has_goes = any(_re.search(r'C\d{2}', f.name) for f in nc_files)
        if not has_goes:
            return False

        try:
            from satpy import Scene
        except ImportError:
            self.log("[GOES] satpy not available — falling back to manual loader")
            return False

        if not getattr(self.main_ui, 'loading_dialog', None):
            self.main_ui.loading_dialog = CachingDialog(
                self.main_ui, title="Loading GOES Data",
                main_text="Reading GOES ABI files via satpy...",
            )
            self.main_ui.loading_dialog.show()
            self.main_ui.loading_dialog.repaint()
            QApplication.processEvents()

        all_native = [f"C{i:02d}" for i in range(1, 17)]

        band_map = {
            'C01': 'B01', 'C02': 'B03', 'C03': 'B02',
        }
        for i in range(4, 17):
            band_map[f'C{i:02d}'] = f'B{i:02d}'

        loaded_data = {}
        area_def = None
        filenames = [str(f) for f in nc_files]

        try:
            # Use dynamic reader selection instead of hardcoded "abi_l1b"
            reader_name = reader_manager.get_appropriate_reader(filenames)
            scn = Scene(reader=reader_name, filenames=filenames)
            try:
                scn.load(all_native)
                loaded_native = [b for b in all_native if b in scn]
            except Exception:
                loaded_native = []
                for b in all_native:
                    try:
                        scn.load([b])
                        loaded_native.append(b)
                    except Exception:
                        pass
            if not loaded_native:
                avail = list(scn.available_dataset_names())[:16]
                scn.load(avail)
                loaded_native = [b for b in avail if b in scn]
            _max_px = getattr(self.main_ui, 'preview_max_px', 2200) or 2200
            for b in loaded_native:
                da = scn[b]
                internal = band_map.get(b, b)
                arr = da.values.astype(np.float32)
                if _max_px > 0 and arr.ndim >= 2:
                    h, w = arr.shape[:2]
                    if max(h, w) > _max_px:
                        step = max(1, int(np.ceil(max(h, w) / _max_px)))
                        new_h = int(np.ceil(h / step))
                        new_w = int(np.ceil(w / step))
                        zoom_factors = (new_h / h, new_w / w) + (1,) * max(0, arr.ndim - 2)
                        if np.isnan(arr).any():
                            from scipy.ndimage import distance_transform_edt
                            nan_mask = np.isnan(arr)
                            if arr.ndim == 2:
                                indices = distance_transform_edt(nan_mask, return_distances=False, return_indices=True)
                                filled = arr[tuple(indices)]
                            else:
                                filled = arr.copy()
                                for c in range(arr.shape[2]):
                                    mc = nan_mask[:, :, c]
                                    if mc.any():
                                        idx = distance_transform_edt(mc, return_distances=False, return_indices=True)
                                        filled[:, :, c] = arr[:, :, c][tuple(idx)]
                            result = zoom(filled, zoom_factors, order=1)
                            mask_resized = zoom(nan_mask.astype(np.float32), zoom_factors[:2], order=0) > 0.5
                            if result.ndim > mask_resized.ndim:
                                mask_resized = np.expand_dims(mask_resized, axis=-1)
                            arr = np.where(mask_resized, np.nan, result).astype(np.float32)
                        else:
                            arr = zoom(arr, zoom_factors, order=1).astype(np.float32)
                loaded_data[internal] = arr
                if area_def is None and "area" in da.attrs:
                    area_def = da.attrs["area"]
        except Exception as exc:
            self.log(f"[GOES] satpy scene load failed: {exc}")
            if getattr(self.main_ui, 'loading_dialog', None):
                try:
                    self.main_ui.loading_dialog.close()
                except Exception:
                    pass
                self.main_ui.loading_dialog = None
            return False

        if not loaded_data:
            self.log("[GOES] satpy loaded zero bands.")
            if getattr(self.main_ui, 'loading_dialog', None):
                try:
                    self.main_ui.loading_dialog.close()
                except Exception:
                    pass
                self.main_ui.loading_dialog = None
            return False

        band_names = sorted(loaded_data.keys())
        self.log(f"[GOES] satpy loaded {len(band_names)} bands: {band_names}")

        crs = None
        transform = None
        ref_grid = None
        if area_def is not None:
            try:
                from pyproj import CRS as _CRS
                pd = area_def.proj_dict
                lo = pd.get("lon_0", -75.0)
                hh = pd.get("h", 35786023.0)
                sw = pd.get("sweep", "x")
                swp = f" +sweep={sw}" if sw else ""
                p4 = (
                    f"+proj=geos +lon_0={lo} +h={hh} +x_0=0 +y_0=0 "
                    f"+ellps=WGS84{swp} +units=m +no_defs"
                )
                crs = _CRS.from_proj4(p4)
                x_ll, y_ll, x_ur, y_ur = area_def.area_extent
                nr, nc = area_def.shape
                rx = (x_ur - x_ll) / nc
                ry = (y_ur - y_ll) / nr
                import rasterio
                transform = rasterio.transform.Affine(rx, 0.0, x_ll, 0.0, -ry, y_ur)
                ref_grid = (nc, nr)
                self.log(f"[GOES] CRS extracted from satpy area definition")
            except Exception as exc:
                self.log(f"[GOES] CRS extraction failed: {exc}")

        for attr_w, attr_t in [("raw_cache_worker", "raw_cache_thread"),
                               ("raw_cache_worker2", "raw_cache_thread2")]:
            w = getattr(self.main_ui, attr_w, None)
            t = getattr(self.main_ui, attr_t, None)
            if w:
                try:
                    w.band_cached.disconnect()
                    w.finished.disconnect()
                    w.progress.disconnect()
                except Exception:
                    pass
                try:
                    w.cancel()
                except Exception:
                    pass
            if t:
                if t.isRunning():
                    t.quit()
                    t.wait(1000)
                t.deleteLater()
                setattr(self.main_ui, attr_t, None)
            setattr(self.main_ui, attr_w, None)
        for a in ("scene_prep_thread", "scene_prep_worker"):
            o = getattr(self.main_ui, a, None)
            if o:
                try:
                    if a == "scene_prep_thread" and o.isRunning():
                        o.quit()
                        o.wait(2000)
                    o.deleteLater()
                except Exception:
                    pass
                setattr(self.main_ui, a, None)

        self.main_ui.current_crs = crs
        self.main_ui.current_geotransform = transform
        self.main_ui._ref_grid_size = ref_grid
        self.main_ui._source_area_def = area_def
        self.main_ui.current_nc_path = data_folder
        self.main_ui._band_nc_map = {}
        self.main_ui._overlay_geo_unavailable = crs is None
        self.main_ui._ir_kelvin = None
        self.main_ui.current_winds_uv = None
        self.main_ui.winds_enabled = False
        self.main_ui._wind_proj_cache.clear()
        self._update_winds_checkbox_state()
        if hasattr(self.main_ui, "update_devkit_band_lists"):
            self.main_ui.update_devkit_band_lists(band_names)

        self.main_ui.cache.clear_raw()
        self.main_ui.cache.clear_rgb()
        self.main_ui.cache.clear_precached()
        if hasattr(self.main_ui, "_display_image_cache"):
            self.main_ui._display_image_cache.clear()
        if hasattr(self.main_ui, "_resized_display_cache"):
            self.main_ui._resized_display_cache.clear()

        # Write per-band NC files first (disk I/O), freeing each array immediately
        _ts = getattr(self.main_ui, "current_datetime", "unknown")
        _prod = data_folder.name[:-(len(_ts) + 1)] if data_folder.name.endswith(_ts) else data_folder.name
        try:
            for _bn in list(loaded_data.keys()):
                _arr = loaded_data.pop(_bn)
                _nc = data_folder / f"{_prod}_{_bn}_{_ts}.nc"
                _nc.parent.mkdir(parents=True, exist_ok=True)
                _ds = xr.Dataset({_bn: (("y", "x"), _arr)})
                _ds.to_netcdf(str(_nc))
                _ds.close()
                del _arr
                _gc.collect()
            self.main_ui.current_nc_path = data_folder / f"{_prod}_{band_names[0]}_{_ts}.nc"
            # Point _band_nc_map to per-band NC files (preserves satpy's reflectance/BT calibration)
            try:
                _band_nc_map = {}
                for _bn in band_names:
                    _nc = data_folder / f"{_prod}_{_bn}_{_ts}.nc"
                    if _nc.exists():
                        _band_nc_map[_bn] = str(_nc)
                if not _band_nc_map:
                    # Fall back to original GOES C{n} files
                    for f in nc_files:
                        m = _re.search(r'C(\d{2})', f.name)
                        if m:
                            c_band = f"C{m.group(1)}"
                            internal = band_map.get(c_band, c_band)
                            _band_nc_map[internal] = str(f)
                self.main_ui._band_nc_map = _band_nc_map
            except Exception:
                self.main_ui._band_nc_map = {}
            self.log(f"[GOES] Cached {len(band_names)} bands to per-band NC files")
        except Exception as _exc:
            self.log(f"[GOES] NC write skipped: {_exc}")

        # Load only the first band into runtime cache (others lazy-loaded on demand)
        if band_names:
            _nc = data_folder / f"{_prod}_{band_names[0]}_{_ts}.nc"
            if _nc.exists():
                try:
                    with xr.open_dataset(_nc, engine="netcdf4", mask_and_scale=False) as _ds:
                        if band_names[0] in _ds:
                            _arr = _ds[band_names[0]].values.astype(np.float32)
                            self.main_ui.cache.put_raw(band_names[0], _arr)
                            del _arr
                except Exception:
                    pass
                _gc.collect()

        # Wire up GOESCacheManager for disk-based .npz RGBA cache (seamless switching)
        if GOESCacheManager is not None:
            try:
                gc = GOESCacheManager(
                    data_folder, runtime_cache=self.main_ui.cache,
                    settings=getattr(self.main_ui, 'settings', {}),
                    log_func=lambda msg: self.log(f"[GOES] {msg}"),
                    band_nc_map=getattr(self.main_ui, '_band_nc_map', {})
                )
                if hasattr(gc.loader, 'max_px'):
                    gc.loader.max_px = getattr(self.main_ui, 'preview_max_px', 2200)
                self.main_ui._goes_cache = gc
                from concurrent.futures import ThreadPoolExecutor, as_completed
                with ThreadPoolExecutor(max_workers=1) as pool:
                    futs = {pool.submit(gc.ensure_display_ready, b): b for b in band_names}
                    for f in as_completed(futs):
                        try:
                            f.result()
                        except Exception:
                            pass
                self.log(f"[GOES] Pre-cached {len(band_names)} bands to disk cache")
            except Exception as exc:
                self.main_ui._goes_cache = None
                self.log(f"[GOES] Disk cache setup skipped: {exc}")

        self.main_ui.available_bands = band_names
        self.main_ui._build_band_checkboxes(band_names)

        self.log(f"[GOES] Loaded {len(band_names)} bands via satpy — ready.")
        self.main_ui.status_bar.showMessage(
            f"GOES loaded: {len(band_names)} bands for {self.main_ui.current_datetime}"
        )

        _preferred = ["B01", "B02", "B04", "B05", "B06", "B07", "B08",
                      "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]
        first_band = next((b for b in _preferred if b in band_names), band_names[0])
        cb = self.main_ui.band_checkboxes.get(first_band)
        if cb:
            cb.blockSignals(True)
            cb.setChecked(True)
            cb.blockSignals(False)

        if hasattr(self.main_ui, "_start_overlay_precache"):
            self.main_ui._start_overlay_precache(data_folder)

        self.main_ui._raw_bands_processed = set(band_names)
        self.main_ui._raw_cache_threads_running = 0
        if hasattr(self.main_ui, "_cache_watchdog"):
            try:
                self.main_ui._cache_watchdog.stop()
            except Exception:
                pass
        self._check_and_close_loading_dialog()

        if hasattr(self.main_ui, "_populate_professional_bands"):
            self.main_ui._populate_professional_bands(band_names)
        return True

    def _generate_goes_true_color(self, nc_path, raw_veggie=False, max_px=None):
        import time as _time
        _t0 = _time.time()
        product_key = "basic_goes_true_color"
        if max_px is None:
            max_px = getattr(self.main_ui, 'preview_max_px', 2200)
        mem_qi = self.main_ui.cache.get_rgb(product_key)
        if mem_qi is not None:
            buf = bytes(mem_qi.bits())
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(mem_qi.height(), mem_qi.width(), 4)
            return arr
        gc = getattr(self.main_ui, '_goes_cache', None)
        if gc is not None:
            disk_arr = gc.get_rgb_composite(product_key, max_px)
            if disk_arr is not None:
                from PySide6.QtGui import QImage
                h, w = disk_arr.shape[:2]
                qi = QImage(disk_arr.data, w, h, w * 4, QImage.Format_RGBA8888)
                self.main_ui.cache.put_rgb(product_key, qi.copy())
                return disk_arr
        bfm = getattr(self.main_ui, '_band_nc_map', {}) or {}
        cache = {}
        for b in ("B03", "B02", "B01"):
            p = bfm.get(b)
            if p is None:
                self.log(f"GOES True Color: missing band {b}")
                return None
            arr = self.main_ui.cache.get_raw(b)
            if arr is None:
                try:
                    ds = xr.open_dataset(p, mask_and_scale=False)
                    vn = b if b in ds else ("Rad" if "Rad" in ds else None)
                    if vn is None: ds.close(); return None
                    arr = ds[vn].values.astype(np.float32)
                    fill = ds[vn].attrs.get('_FillValue')
                    if fill is not None: arr[arr == fill] = np.nan
                    missing = ds[vn].attrs.get('missing_value')
                    if missing is not None: arr[arr == missing] = np.nan
                    scale = ds[vn].attrs.get('scale_factor')
                    offset = ds[vn].attrs.get('add_offset')
                    if scale is not None and offset is not None:
                        arr = np.where(np.isfinite(arr), arr * np.float32(scale) + np.float32(offset), np.nan)
                    ds.close()
                except Exception as e:
                    self.log(f"GOES True Color: load error {b}: {e}")
                    return None
            cache[b] = arr
        if max_px == 0:
            _target = max(cache.keys(), key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        else:
            _target = min(cache.keys(), key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        _th, _tw = cache[_target].shape
        from scipy.ndimage import zoom as _z
        for b in ("B03", "B02", "B01"):
            a = cache[b]
            if a.shape != (_th, _tw):
                zh = _th / a.shape[0]
                zw = _tw / a.shape[1]
                cache[b] = _z(a, (zh, zw), order=1)
        R = cache["B02"].copy()
        veggie = cache["B03"].copy()
        B = cache["B01"].copy()
        h = w = 0
        for a in (R, veggie, B):
            a[np.isnan(a)] = 0.0
        if np.nanmax(R) > 1.0:
            R = np.clip(R / 100.0, 0.0, 1.0)
            veggie = np.clip(veggie / 100.0, 0.0, 1.0)
            B = np.clip(B / 100.0, 0.0, 1.0)
        else:
            R = np.clip(R, 0.0, 1.0)
            veggie = np.clip(veggie, 0.0, 1.0)
            B = np.clip(B, 0.0, 1.0)
        G_out = 0.45 * R + 0.1 * veggie + 0.45 * B
        G_out = np.clip(G_out, 0.0, 1.0)
        rgb = np.stack([R, G_out, B], axis=-1)
        alpha = get_engine(self.main_ui.sat_combo.currentText())._earth_mask([R, G_out, B])
        if alpha.shape[:2] != rgb.shape[:2]:
            zh = rgb.shape[0] / alpha.shape[0]
            zw = rgb.shape[1] / alpha.shape[1]
            alpha = _z(alpha, (zh, zw), order=0)
        alpha = alpha.astype(np.uint8)
        arr = np.concatenate([(rgb * 255).astype(np.uint8), alpha[:, :, None]], axis=-1)
        self.log(f"[TC] done in {_time.time()-_t0:.3f}s")
        if gc is not None:
            gc.put_rgb_composite(product_key, arr, max_px)
        from PySide6.QtGui import QImage
        h, w = arr.shape[:2]
        qi = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888)
        self.main_ui.cache.put_rgb(product_key, qi.copy())
        return arr

    def _generate_goes_true_daynight(self, nc_path, raw_veggie=False, max_px=None):
        import time as _time
        _t0 = _time.time()
        product_key = "basic_goes_true_daynight"
        if max_px is None:
            max_px = getattr(self.main_ui, 'preview_max_px', 2200)
        mem_qi = self.main_ui.cache.get_rgb(product_key)
        if mem_qi is not None:
            buf = bytes(mem_qi.bits())
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(mem_qi.height(), mem_qi.width(), 4)
            return arr
        gc = getattr(self.main_ui, '_goes_cache', None)
        if gc is not None:
            disk_arr = gc.get_rgb_composite(product_key, max_px)
            if disk_arr is not None:
                from PySide6.QtGui import QImage
                h, w = disk_arr.shape[:2]
                qi = QImage(disk_arr.data, w, h, w * 4, QImage.Format_RGBA8888)
                self.main_ui.cache.put_rgb(product_key, qi.copy())
                return disk_arr
        bfm = getattr(self.main_ui, '_band_nc_map', {}) or {}
        cache = {}
        for b in ("B13", "B03", "B02", "B01"):
            p = bfm.get(b)
            if p is None:
                self.log(f"GOES True Day/Night: missing band {b}")
                return None
            arr = self.main_ui.cache.get_raw(b)
            if arr is None:
                try:
                    ds = xr.open_dataset(p, mask_and_scale=False)
                    vn = b if b in ds else ("Rad" if "Rad" in ds else None)
                    if vn is None:
                        ds.close(); return None
                    arr = ds[vn].values.astype(np.float32)
                    fill = ds[vn].attrs.get('_FillValue')
                    if fill is not None: arr[arr == fill] = np.nan
                    missing = ds[vn].attrs.get('missing_value')
                    if missing is not None: arr[arr == missing] = np.nan
                    scale = ds[vn].attrs.get('scale_factor')
                    offset = ds[vn].attrs.get('add_offset')
                    if scale is not None and offset is not None:
                        arr = np.where(np.isfinite(arr), arr * np.float32(scale) + np.float32(offset), np.nan)
                    ds.close()
                except Exception as e:
                    self.log(f"GOES True Day/Night: load error {b}: {e}")
                    return None
            cache[b] = arr
        if max_px == 0:
            _target = max(cache.keys(), key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        else:
            _target = min(cache.keys(), key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        _th, _tw = cache[_target].shape
        from scipy.ndimage import zoom as _z
        for b in ("B13", "B03", "B02", "B01"):
            a = cache[b]
            if a.shape != (_th, _tw):
                zh = _th / a.shape[0]
                zw = _tw / a.shape[1]
                cache[b] = _z(a, (zh, zw), order=1)
        R = cache["B02"].copy()
        veggie = cache["B03"].copy()
        B = cache["B01"].copy()
        IR = cache["B13"].copy()
        h, w = _th, _tw
        _nan_alpha = np.isnan(R) & np.isnan(IR)
        for a in (R, veggie, B, IR):
            a[np.isnan(a)] = 0.0
        if np.nanmax(R) > 1.0:
            R = np.clip(R / 100.0, 0.0, 1.0)
            veggie = np.clip(veggie / 100.0, 0.0, 1.0)
            B = np.clip(B / 100.0, 0.0, 1.0)
        else:
            R = np.clip(R, 0.0, 1.0)
            veggie = np.clip(veggie, 0.0, 1.0)
            B = np.clip(B, 0.0, 1.0)
        sza = None
        if nc_path is not None:
            _eng_sza = get_engine(self.main_ui.sat_combo.currentText())
            sza = _eng_sza._compute_solar_zenith_angle(nc_path, h, w)
        if sza is None:
            sza = np.zeros((h, w), dtype=np.float32)
        cos_sza = np.clip(np.cos(np.radians(sza)), 0.33, 1.0)
        cos2_sza = np.clip(np.cos(np.radians(sza)), 0.40, 1.0)
        path_sun = 0.8 / cos2_sza
        path_sun_a = 1.0 / cos_sza
        day_weight = np.clip((90.0 - sza) / 5.0, 0.0, 1.0)
        night_weight = 1.0 - day_weight
        R_day = np.clip(R * path_sun_a, 0.0, 1.0)
        veggie_day = np.clip(veggie * path_sun_a, 0.0, 1.0)
        B_day = np.clip(B * path_sun_a, 0.0, 1.0)
        G_out = 0.45 * R_day + 0.1 * veggie_day + 0.45 * B_day
        G_out = np.clip(G_out, 0.0, 1.0)
        rayleigh_r = 0.011 * path_sun
        rayleigh_g = 0.033 * path_sun
        rayleigh_b = 0.050 * path_sun
        R_corr = np.clip(R_day - rayleigh_r, 0.0, 1.0)
        G_corr = np.clip(G_out - rayleigh_g, 0.0, 1.0)
        B_corr = np.clip(B_day - rayleigh_b, 0.0, 1.0)
        day_rgb = np.stack([R_corr, G_corr, B_corr], axis=-1) * day_weight[:, :, None]
        _eng_sza2 = get_engine(self.main_ui.sat_combo.currentText())
        scale13, off13 = _eng_sza2._get_band_scale_offset(nc_path, "B13") if nc_path else (1.0, 0.0)
        ir = IR * scale13 + off13
        ir = np.where(np.isnan(ir), 300.0, ir)
        ir_norm = np.clip((313.15 - ir) / (313.15 - 173.15), 0.0, 1.0)
        ir_layer = np.power(ir_norm, 1.5) * 2
        night_rgb = np.stack([ir_layer, ir_layer, ir_layer], axis=-1) * night_weight[:, :, None]
        rgb = day_rgb + night_rgb
        sat_factor = 1.33
        lum = 0.2989 * rgb[:,:,0] + 0.5870 * rgb[:,:,1] + 0.1140 * rgb[:,:,2]
        r_final = np.clip(lum + sat_factor * (rgb[:,:,0] - lum), 0.0, 1.0)
        g_final = np.clip(lum + sat_factor * (rgb[:,:,1] - lum), 0.0, 1.0)
        b_final = np.clip(lum + sat_factor * (rgb[:,:,2] - lum), 0.0, 1.0)
        rgb = np.stack([r_final, g_final, b_final], axis=-1)
        rgb = np.clip(rgb, 0.0, 3.0)
        rgb_u8 = (rgb * 255).astype(np.uint8)
        alpha_ch = np.where(_nan_alpha, np.uint8(0), np.uint8(255))
        arr = np.concatenate([rgb_u8, alpha_ch[:, :, None]], axis=-1)
        self.log(f"[TC_DN] done in {_time.time()-_t0:.3f}s")
        if gc is not None:
            gc.put_rgb_composite(product_key, arr, max_px)
        from PySide6.QtGui import QImage
        qi = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888)
        self.main_ui.cache.put_rgb(product_key, qi.copy())
        return arr

    def _get_composite_band_cache(self):
        """Return raw bands already resampled to a common grid, cached per scene.

        Composites (True Color, Sandwich, Day/Night, etc.) need float radiometric
        data on a shared pixel grid. Computing that zoom once per scene (instead of
        on every product click) removes the repeated scipy.ndimage.zoom cost.
        """
        raw = getattr(self.main_ui, 'cache', None)
        if raw is None or not getattr(raw, 'raw', None):
            return None
        fp = tuple((b, raw.raw[b].shape) for b in sorted(raw.raw))
        cur = getattr(self.main_ui, '_composite_band_cache', None)
        if isinstance(cur, dict) and cur.get('_fp') == fp and cur.get('bands'):
            return cur['bands']
        bands = {}
        for b, arr in raw.raw.items():
            a = np.asarray(arr, dtype=np.float32)
            if not a.flags['C_CONTIGUOUS']:
                a = np.ascontiguousarray(a)
            bands[b] = a
        if not bands:
            return None
        _smallest = min(bands.values(), key=lambda a: a.shape[0] * a.shape[1])
        th, tw = _smallest.shape
        for b in list(bands):
            a = bands[b]
            if a.shape != (th, tw):
                zh = th / a.shape[0]
                zw = tw / a.shape[1]
                bands[b] = zoom(a, (zh, zw), order=1).astype(np.float32)
        self.main_ui._composite_band_cache = {'_fp': fp, 'bands': bands}
        return bands

    @staticmethod
    def _resolve_nc_entry(entry):
        if isinstance(entry, tuple):
            return (entry[0] if len(entry) > 0 else None,
                    entry[1] if len(entry) > 1 else None)
        return entry, None

    def _pull_band_from_anim_cache(self, band, nc_path):
        """Return a cached animation band array whose frame uses the exact same
        NC file as ``nc_path`` (so the Scene reuses it instead of re-reading)."""
        if nc_path is None:
            return None
        band_frames = getattr(self.main_ui, '_anim_band_frames', []) or []
        nc_paths = getattr(self.main_ui, '_anim_nc_paths', []) or []
        if not band_frames:
            return None
        try:
            target = Path(nc_path).resolve()
        except Exception:
            return None
        for i, bc in enumerate(band_frames):
            if not bc or band not in bc:
                continue
            entry = nc_paths[i] if i < len(nc_paths) else None
            p, _ = self._resolve_nc_entry(entry)
            if p is None:
                continue
            try:
                if Path(p).resolve() == target:
                    return bc[band]
            except Exception:
                continue
        return None

    def _prefill_required_bands_from_animation(self, required_bands, nc_path):
        """Pull any missing required band arrays from the animation band cache
        (matching by NC file) so composites reuse already-loaded data."""
        cache = getattr(self.main_ui, 'cache', None)
        if cache is None or not hasattr(cache, 'raw') or not required_bands:
            return
        for b in list(required_bands):
            if b in cache.raw:
                continue
            ba = self._pull_band_from_anim_cache(b, nc_path)
            if ba is not None:
                try:
                    cache.put_raw(b, ba)
                except Exception:
                    pass

    def _required_bands_for_key(self, key):
        """Bands a product needs to composite (mirrors generate_rgb_product)."""
        try:
            info = get_products(self.main_ui.sat_combo.currentText())[key]
        except Exception:
            return set()
        if not isinstance(info, dict):
            return set()
        required = set(info.get("bands") or [])
        formula = info.get("formula", {}) or {}
        if info.get("single_band") and "band" in formula:
            required.add(formula["band"])
        else:
            for ch in info.get("channels", []):
                spec = formula.get(ch, {}) or {}
                if "band1" in spec:
                    required.add(spec["band1"])
                if "band2" in spec:
                    required.add(spec["band2"])
                if "band" in spec:
                    required.add(spec["band"])
                if "bands" in spec:
                    required.update(spec["bands"])
        # Color-scale products have an empty "bands" list but still need a real
        # band to render. BT/IR color scales force B13 (mirrors the forced band
        # logic in _render_color_scale_from_cache and _apply_color_scale_product);
        # the rest (jet/viridis/inferno) run off any loaded band and are handled
        # as "any band" in refresh_product_tile_availability.
        cs = info.get("color_scale") or {}
        cmap_name = cs.get("colormap", "")
        if cmap_name in ("BT Enhanced (IR)", "Sandwich (IR)", "Sandwich IR (SATAID)",
                         "Sandwich (SATAID)", "Dvorak (IR)", "Dvorak Experimental", "SST (IR)"):
            required.add("B13")
        return {b for b in required if isinstance(b, str)}

    def _cached_bands(self):
        """All band arrays currently in RAM: runtime cache + animation band cache."""
        bands = set()
        cache = getattr(self.main_ui, 'cache', None)
        if cache is not None and hasattr(cache, 'raw'):
            try:
                bands.update(cache.raw.keys())
            except Exception:
                pass
        frames = getattr(self.main_ui, '_anim_band_frames', None) or []
        for bc in frames:
            if bc:
                try:
                    bands.update(bc.keys())
                except Exception:
                    pass
        return bands

    def refresh_product_tile_availability(self):
        """Enable a Scene product tile only when all of its required bands are
        cached (runtime cache or animation band cache); others stay disabled."""
        ui = self.main_ui
        tiles = getattr(ui, '_product_tile_widgets', None)
        if not tiles:
            return
        cached = self._cached_bands()
        for key, tile in list(tiles.items()):
            try:
                info = get_products(self.main_ui.sat_combo.currentText()).get(key) or {}
                req = self._required_bands_for_key(key)
                if info.get("color_scale") and not req:
                    # Flexible color scales (jet/viridis/inferno) render from
                    # whichever band happens to be loaded -- enable them as soon
                    # as any band data is in RAM.
                    tile.setEnabled(bool(cached))
                else:
                    tile.setEnabled(bool(req) and req.issubset(cached))
            except Exception:
                continue

    def _current_anim_frame_nc(self):
        """NC path for the currently displayed animation frame, used as a
        Scene product source when no Scene file was loaded explicitly."""
        ui = self.main_ui
        idx = getattr(ui, '_anim_index', -1)
        paths = getattr(ui, '_anim_nc_paths', None)
        if not paths or not (0 <= idx < len(paths)):
            return None
        entry = paths[idx]
        p = entry[0] if isinstance(entry, tuple) and entry else entry
        if isinstance(p, (list, tuple)):
            p = p[0] if p else None
        return p or None

    def _warm_composite_scene(self):
        """Pre-compute the composite band grid + SZA in a background thread."""
        try:
            nc_path = self._current_nc_file()
            if not nc_path:
                return
            if not getattr(self.main_ui, 'cache', None) or not getattr(self.main_ui.cache, 'raw', None):
                return
            def _work():
                try:
                    band_cache = self._get_composite_band_cache()
                    if not band_cache:
                        return
                    _smallest = min(band_cache.values(), key=lambda a: a.shape[0] * a.shape[1])
                    _h, _w = _smallest.shape[:2]
                    _eng = get_engine(self.main_ui.sat_combo.currentText())
                    _eng._compute_solar_zenith_angle(nc_path, _h, _w)
                except Exception:
                    pass
            threading.Thread(target=_work, daemon=True).start()
        except Exception:
            pass

    def generate_rgb_product(self, key, _from_animation=False):

        """Generate RGB composite product.

        Creates RGB composite from multiple spectral bands using
        predefined or custom channel mappings. Supports true-color,
        natural-color, and specialized RGB products.

        Args:
            product_name (str): RGB product identifier.
            bands (dict[str, np.ndarray]): Input band data.

        Returns:
            np.ndarray: RGB composite as uint8 array.

        Note:
            Applies gamma correction and histogram equalization
            for optimal visual appearance.
        """
        if self.main_ui.generating_product:
            self.log("Already generating a product, please wait.")
            return
        nc_path = self._current_nc_file()
        if not nc_path:
            nc_path = self._current_anim_frame_nc()
        if not nc_path:
            return
        self.main_ui.current_nc_path = nc_path

        if key in ("basic_goes_true_color", "basic_goes_true_daynight"):
            self.main_ui.generating_product = True
            import time as _t
            self.main_ui._product_disabled_at = _t.perf_counter()
            for tile in self.main_ui._product_tile_widgets.values():
                tile.setEnabled(False)
            if key == "basic_goes_true_daynight":
                arr = self._generate_goes_true_daynight(nc_path, raw_veggie=False, max_px=self.main_ui.preview_max_px)
            else:
                arr = self._generate_goes_true_color(nc_path, raw_veggie=False, max_px=self.main_ui.preview_max_px)
            if arr is not None:
                h, w = arr.shape[:2]
                if self.main_ui.preview_max_px == 0:
                    target_w, target_h = w, h
                else:
                    gq = self._get_quality_grid()
                    if gq is not None:
                        target_w, target_h = int(gq[0]), int(gq[1])
                    else:
                        target_w, target_h = w, h
                if target_w != w or target_h != h:
                    _resize_key = (key, w, h, target_w, target_h)
                    cached_resize = self.main_ui._resized_display_cache.get(_resize_key)
                    if cached_resize is not None:
                        arr = cached_resize
                    else:
                        zh = target_h / h
                        zw = target_w / w
                        if abs(zh - 1.0) < 0.001 and abs(zw - 1.0) < 0.001:
                            pass
                        else:
                            from scipy.ndimage import zoom as _z
                            arr = _z(arr, (zh, zw, 1), order=1).clip(0, 255).astype(np.uint8)
                        self.main_ui._resized_display_cache[_resize_key] = arr
                self._display_composite(arr, key, skip_resize=True)
            self.main_ui.generating_product = False
            self.main_ui._product_disabled_at = 0.0
            self.refresh_product_tile_availability()
            return

        qimage = self.main_ui.cache.get_rgb(key)
        _anim_paste = _from_animation and getattr(self.main_ui, '_anim_sync_target_meta', None)
        if qimage is not None and not _anim_paste:
            pixmap = QPixmap.fromImage(qimage)
            self.main_ui.current_original = nc_path
            self.main_ui.current_base = f"{nc_path.stem}_{key}"
            pixmap = self._resize_for_fldk(pixmap)
            scene_pos = self._get_image_scene_pos()
            self.main_ui.graphics_view.set_image(pixmap, preserve_view=True, quality_level=1.0, is_original=False, scene_pos=scene_pos)
            self.log(f"Displayed cached {get_products(self.main_ui.sat_combo.currentText())[key]['name']}")
            if self.main_ui.grid_enabled or self.main_ui.coast_enabled or self.main_ui.winds_enabled:
                self.main_ui.update_overlays()
            return

        info = get_products(self.main_ui.sat_combo.currentText())[key]

        # ── Color Scale / Professional Product ──
        if info.get("color_scale"):
            self.main_ui._apply_color_scale_product(key, info)
            return

        required_bands = self._required_bands_for_key(key)

        self._prefill_required_bands_from_animation(required_bands, nc_path)

        use_raw = all(b in self.main_ui.cache.raw for b in required_bands)
        band_cache = self._get_composite_band_cache() if use_raw else None
        if use_raw:
            self.log(f"Using raw band cache for {get_products(self.main_ui.sat_combo.currentText())[key]['name']}")

        self.main_ui.generating_product = True
        import time as _t
        self.main_ui._product_disabled_at = _t.perf_counter()
        if not _from_animation:
            self.main_ui._anim_sync_target_meta = None
            self.main_ui._anim_sync_product_idx = None
            self.main_ui._anim_sync_pending_idx = None
            for tile in self.main_ui._product_tile_widgets.values():
                tile.setEnabled(False)

        if self.main_ui.settings.get("bg_compositing", True):
            self.main_ui.status_bar.showMessage(f"Generating {get_products(self.main_ui.sat_combo.currentText())[key]['name']}...")
            _cthread = QThread()
            self.main_ui.composite_thread = _cthread
            bfm = getattr(self.main_ui, '_band_nc_map', {}) or None
            _eng_cw = get_engine(self.main_ui.sat_combo.currentText())
            self.main_ui.composite_worker = CompositeWorker(key, nc_path, self.main_ui.preview_max_px,
                                                            band_cache=band_cache, band_file_map=bfm,
                                                            engine_class=_eng_cw)
            self.main_ui.composite_worker.moveToThread(_cthread)
            self.main_ui.composite_worker.finished.connect(self.on_composite_finished)
            self.main_ui.composite_worker.progress.connect(self.log)
            _cthread.started.connect(self.main_ui.composite_worker.run)
            self.main_ui.composite_worker.finished.connect(_cthread.quit)
            self.main_ui.composite_worker.finished.connect(self.main_ui.composite_worker.deleteLater)
            _cthread.finished.connect(_cthread.deleteLater)

            def _release_comp_thread(t=_cthread):
                if self.main_ui.composite_thread is t:
                    self.main_ui.composite_thread = None

            _cthread.finished.connect(_release_comp_thread)
            _cthread.start()
        else:
            self.main_ui.setCursor(QCursor(Qt.WaitCursor))
            try:
                bfm = getattr(self.main_ui, '_band_nc_map', {}) or None
                _eng_inline = get_engine(self.main_ui.sat_combo.currentText())
                arr = _eng_inline.composite_realtime(key, nc_path, self.main_ui.preview_max_px,
                                                           band_cache=band_cache, band_file_map=bfm)
                self._display_composite(arr, key)
            except Exception as e:
                self.log(f"RGB error: {e}")
            finally:
                self.main_ui.setCursor(QCursor(Qt.ArrowCursor))
                self.main_ui.generating_product = False
                self.main_ui._product_disabled_at = 0.0
                self.refresh_product_tile_availability()

    def on_composite_finished(self, result):
        arr, key = result
        ui = self.main_ui
        sync_idx = getattr(ui, '_anim_sync_product_idx', None)
        anim_idx = getattr(ui, '_anim_index', None)
        if sync_idx is not None and anim_idx is not None and anim_idx != sync_idx:
            # A Scene product generated for a slot we have since left: do not
            # overwrite the current view with that stale full-disk product.
            # Do NOT clear generating_product here (another composite, e.g. a
            # manual product click, may still be running and owns that flag).
            # Remember the slot and rebuild it later through a guarded re-sync.
            ui.log(f"Animation product for slot {sync_idx} skipped (now at {anim_idx}).")
            ui._anim_sync_target_meta = None
            ui._anim_sync_pending_idx = anim_idx
            try:
                ac = getattr(ui, 'animation_controller', None)
                if ac is not None and hasattr(ac, '_sync_scene_product_to_anim'):
                    def _retry():
                        if getattr(ui, '_anim_sync_pending_idx', None) != anim_idx:
                            return
                        try:
                            if not getattr(ui, 'generating_product', False):
                                ui._anim_sync_pending_idx = None
                                ac._sync_scene_product_to_anim(anim_idx)
                        except Exception:
                            pass
                    QTimer.singleShot(400, _retry)
            except Exception:
                pass
            # The composite that just finished is the one that owned the
            # generating_product flag (a new one can't start while it was set),
            # so this skipped slot must still release the lock and re-enable the
            # product tiles -- otherwise every later product click is ignored.
            ui.generating_product = False
            ui._product_disabled_at = 0.0
            self.refresh_product_tile_availability()
            return
        if arr is None:
            self.log(f"Composite worker returned None for {key}")
        self._display_composite(arr, key)
        self.main_ui.status_bar.showMessage(f"RGB: {get_products(self.main_ui.sat_combo.currentText())[key]['name']} ready")
        self.main_ui.generating_product = False
        self.main_ui._product_disabled_at = 0.0
        pending = getattr(self.main_ui, '_anim_sync_pending_idx', None)
        self.main_ui._anim_sync_pending_idx = None
        self.refresh_product_tile_availability()
        if pending is not None:
            try:
                ac = getattr(self.main_ui, 'animation_controller', None)
                if ac is not None and hasattr(ac, '_sync_scene_product_to_anim'):
                    def _retry_later():
                        if getattr(self.main_ui, 'generating_product', False):
                            return
                        try:
                            ac._sync_scene_product_to_anim(pending)
                        except Exception:
                            pass
                    QTimer.singleShot(400, _retry_later)
            except Exception:
                pass

    def _display_composite(self, arr, key, skip_resize=False):
        if arr is None:
            self.log(f"Failed to generate {get_products(self.main_ui.sat_combo.currentText())[key]['name']}")
            return
        h, w = arr.shape[:2]
        if getattr(self.main_ui, 'preview_quality', None) == "gridded res":
            self._sync_gridded_native(arr)
        if not skip_resize and getattr(self.main_ui, 'preview_max_px', 0) != 0:
            _gq = self._get_quality_grid()
            if _gq is None and hasattr(self.main_ui, '_ref_grid_size') and self.main_ui._ref_grid_size:
                _rw, _rh = self.main_ui._ref_grid_size
                if _rw > 0 and _rh > 0 and (w > _rw or h > _rh):
                    _sf = min(_rw / w, _rh / h)
                    if _sf < 0.99:
                        nw = max(1, int(round(w * _sf)))
                        nh = max(1, int(round(h * _sf)))
                        from skimage.transform import resize
                        arr = resize(arr, (nh, nw), preserve_range=True, anti_aliasing=True).clip(0, 255).astype(np.uint8)
                        h, w = nh, nw

        # In geo-target animation mode, paste the target-area product onto the
        # full-disk Scene product (mirrors the animation's inset composite).
        try:
            _meta = getattr(self.main_ui, '_anim_sync_target_meta', None)
            if _meta and getattr(self.main_ui, 'animation_controller', None) is not None:
                ac = self.main_ui.animation_controller
                if hasattr(ac, '_composite_product_geotarget'):
                    _eng_paste = get_engine(self.main_ui.sat_combo.currentText())
                    _pasted = ac._composite_product_geotarget(
                        np.ascontiguousarray(arr), key, _meta,
                        fallback_frames=None, engine_cls=_eng_paste)
                    if _pasted is not None:
                        arr = _pasted
                        h, w = arr.shape[:2]
        except Exception as _e:
            self.log(f"Geo product paste error: {_e}")
        finally:
            self.main_ui._anim_sync_target_meta = None
        if arr.ndim == 2 or arr.shape[2] != 4:
            if arr.ndim == 2:
                arr = np.stack([arr, arr, arr], axis=-1)
            if arr.ndim == 3 and arr.shape[2] == 3:
                hh, ww = arr.shape[:2]
                arr = np.concatenate([arr, np.full((hh, ww, 1), 255, dtype=np.uint8)], axis=2)
            h, w = arr.shape[:2]

        qimage = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)

        import time
        self.main_ui.cache.put_rgb(key, qimage)
        pixmap = QPixmap.fromImage(qimage)
        nc_path = self._current_nc_file()
        if nc_path:
            self.main_ui.current_original = nc_path
            self.main_ui.current_base = f"{nc_path.stem}_{key}"
        if not skip_resize and getattr(self.main_ui, 'preview_max_px', 0) != 0:
            pixmap = self._resize_for_fldk(pixmap)
        scene_pos = QPointF(0, 0) if (skip_resize or getattr(self.main_ui, 'preview_max_px', 0) == 0) else self._get_image_scene_pos()
        if getattr(self.main_ui, 'preview_quality', None) == "gridded res":
            _src, _sp = self._fit_array_to_quality_grid(np.ascontiguousarray(arr))
            self.main_ui.graphics_view.set_tiled_image(
                _src, preserve_view=True, scene_pos=_sp)
        else:
            self.main_ui.graphics_view.set_image(pixmap, preserve_view=True, quality_level=1.0, is_original=False, scene_pos=scene_pos)
        try:
            self.main_ui.graphics_view.viewport().update()
            QApplication.processEvents()
            self.main_ui.graphics_view.viewport().repaint()
        except Exception:
            pass
        import time as _t
        click = getattr(self.main_ui, '_click_log', None)
        if click and click[0] == key:
            dt_ms = (_t.perf_counter() - click[1]) * 1000.0
            self.log(f"Displayed - product:{key} (after {dt_ms:.1f}ms from click, includes paint)")
        else:
            self.log(f"Displayed {get_products(self.main_ui.sat_combo.currentText())[key]['name']}")
        if self.main_ui.grid_enabled or self.main_ui.coast_enabled:
            self.main_ui.update_overlays()
        self._zoom_to_sector_region()
        self._export_broadcast_textures(arr)

    def _export_broadcast_textures(self, rgba_arr):
        if self.main_ui._broadcast_process is None:
            return
        try:
            temp_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
            h, w = rgba_arr.shape[:2]
            qimg = QImage(rgba_arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)

            sat_path = os.path.join(temp_dir, "broadcast_satellite_temp.png")
            final_sat = os.path.join(temp_dir, "broadcast_satellite.png")
            sf = QSaveFile(sat_path)
            if sf.open(QIODevice.WriteOnly):
                qimg.save(sf, "PNG")
                sf.commit()
            if os.path.exists(final_sat):
                os.remove(final_sat)
            os.rename(sat_path, final_sat)

            ir_array = self.main_ui._ir_kelvin
            if ir_array is not None:
                cloud_alpha = np.zeros(ir_array.shape, dtype=np.uint8)
                mask_cloudy = ir_array < 235.0
                mask_transition = (ir_array >= 230.0) & (ir_array <= 240.0)
                cloud_alpha[mask_cloudy] = 255
                if np.any(mask_transition):
                    ramp = (240.0 - ir_array[mask_transition]) / 10.0
                    ramp = np.clip(ramp * 255, 0, 255).astype(np.uint8)
                    cloud_alpha[mask_transition] = ramp

                ch, cw = cloud_alpha.shape
                cloud_qimg = QImage(cloud_alpha.tobytes(), cw, ch, cw, QImage.Format_Grayscale8)
                cloud_path = os.path.join(temp_dir, "broadcast_clouds_temp.png")
                final_cloud = os.path.join(temp_dir, "broadcast_clouds.png")
                sf2 = QSaveFile(cloud_path)
                if sf2.open(QIODevice.WriteOnly):
                    cloud_qimg.save(sf2, "PNG")
                    sf2.commit()
                if os.path.exists(final_cloud):
                    os.remove(final_cloud)
                os.rename(cloud_path, final_cloud)
        except Exception as ex:
            pass

    def _populate_professional_bands(self, band_names: list[str]):
        if hasattr(self.main_ui, 'update_devkit_band_lists'):
            self.main_ui.update_devkit_band_lists(band_names)

    def on_band_cached(self, band, arr):
        self.main_ui.cache.put_precached(band, arr)
        selected_band = next((b for b, cb in getattr(self.main_ui, 'band_checkboxes', {}).items() if cb.isChecked()), None)
        if band == selected_band and not getattr(self.main_ui, 'selected_product', None):
            try:
                self.load_selected_band_or_product()
            except Exception:
                pass
        self.refresh_product_tile_availability()

    def on_precache_progress(self, current, total):
        if self.main_ui.cache_progress_bar:
            self.main_ui.cache_progress_bar.setValue(current)
            self.main_ui.cache_progress_bar.setFormat(f"Caching: {current}/{total}")

    def on_precache_finished(self):
        if self.main_ui.cache_progress_bar:
            self.main_ui.status_bar.removeWidget(self.main_ui.cache_progress_bar)
            self.main_ui.cache_progress_bar.deleteLater()
            self.main_ui.cache_progress_bar = None
        self.main_ui.status_bar.showMessage(f"Band cache complete. Cached {len(self.main_ui.cache.precached)} bands in RAM.")
        self.log(f"Pre-cached {len(self.main_ui.cache.precached)} bands.")

    def _on_raw_cache_finished(self):
        self.main_ui._raw_cache_threads_running = max(
            0, getattr(self.main_ui, '_raw_cache_threads_running', 1) - 1)
        if self.main_ui._raw_cache_threads_running == 0:
            if hasattr(self.main_ui, '_cache_watchdog'):
                self.main_ui._cache_watchdog.stop()
            self.log(f"Raw band cache ready: {len(self.main_ui.cache.raw)} bands in memory.")
            self._check_and_close_loading_dialog()
            self._warm_composite_scene()

    def _force_finish_cache(self, reason):
        if self.main_ui._raw_cache_threads_running > 0:
            self.log(f"Force-finishing cache: {reason}")
            self.main_ui._raw_cache_threads_running = 0
            if hasattr(self.main_ui, '_cache_watchdog'):
                self.main_ui._cache_watchdog.stop()
            self._check_and_close_loading_dialog()

    def _check_and_close_loading_dialog(self):
        if not getattr(self.main_ui, 'loading_dialog', None):
            return
        expected = list(getattr(self.main_ui, 'available_bands', []))
        bands_done = not expected or all(b in self.main_ui._raw_bands_processed for b in expected)
        if bands_done:
            self.log("All bands cached. Rendering display...")
            try:
                self.main_ui.loading_dialog.setLabelText(f"All {len(expected)} bands cached.\nRendering display...")
                self.main_ui.loading_dialog.progress_bar.setValue(100)
                self.main_ui.loading_dialog.repaint()
                QApplication.processEvents()
            except Exception:
                pass
            self.main_ui._pending_close_dialog = self.main_ui.loading_dialog
            self.main_ui.loading_dialog = None
            self.main_ui._suppress_display_until_cache_done = False
            self.log(f"Display suppression released -- triggering first display now")
            try:
                self.load_selected_band_or_product()
            except Exception:
                pass
            if hasattr(self.main_ui, 'update_overlays'):
                try:
                    self.main_ui.update_overlays()
                except Exception:
                    pass

    def _update_loading_dialog_progress(self, just_cached_band=None):
        if not getattr(self.main_ui, 'loading_dialog', None):
            return
        try:
            dialog = self.main_ui.loading_dialog
            if dialog is None:
                return
            expected = list(getattr(self.main_ui, 'available_bands', []))
            total = len(expected)
            if total <= 0:
                return
            cached_now = set(self.main_ui.cache.raw.keys())
            processed = getattr(self.main_ui, '_raw_bands_processed', set())
            cached_count = sum(1 for b in expected if b in processed)
            band_pct = int(round((cached_count / total) * 100))
            is_goes = "goes" in self.main_ui.sat_combo.currentText().lower() if hasattr(self.main_ui, 'sat_combo') else False
            def _label(b):
                return f"C{b[1:]}" if is_goes and b.startswith("B") and len(b) == 3 else b
            def _status(b):
                return "[x]" if b in cached_now else ("[....]" if b == just_cached_band else "[    ]")
            lines = [f"Caching bands: {cached_count}/{total} ({band_pct}%)"]
            if total <= 8:
                for b in expected:
                    lines.append(f"  {_status(b)} {_label(b)}")
            else:
                half = (total + 1) // 2
                for i in range(half):
                    left = f"{_status(expected[i])} {_label(expected[i])}"
                    right = f"{_status(expected[i + half])} {_label(expected[i + half])}" if i + half < total else ""
                    lines.append(f"  {left}  {right}" if right else f"  {left}")
            if getattr(self.main_ui.cache, '_pressure_hit', False):
                lines.append("")
                lines.append("? RAM LIMIT EXCEEDED -- increase cache_size_mb")
                lines.append("  in settings if you have more RAM")
            label_text = "\n".join(lines)
            dialog.update_progress(label_text, band_pct)
        except Exception:
            pass

    def _zoom_to_sector_region(self):
        sector = self.main_ui._detect_current_sector()
        if not sector:
            return
        self.log(f"[Region] Sector detected: {sector}")
        if sector in ("Japan", "Target"):
            for name in ("Show Grid", "Show Coastlines"):
                cb = self.main_ui.overlay_checkboxes.get(name)
                if cb and not cb.isChecked():
                    cb.setChecked(True)
        if sector in ("FLDK", "Japan", "Target"):
            return
        if sector == "CONUS":
            region_bounds = {"north": 52.5, "south": 24.5, "west": -130.0, "east": -64.0}
        elif sector == "Meso":
            region_bounds = {"north": 40.0, "south": 15.0, "west": -110.0, "east": -80.0}
        else:
            return
        self.log(f"[Region] Zooming to {sector}: N={region_bounds['north']} S={region_bounds['south']} W={region_bounds['west']} E={region_bounds['east']}")
        if not self.main_ui.current_crs or not self.main_ui.current_geotransform:
            nc_path = self._current_nc_file()
            if nc_path:
                crs, transform = self.main_ui.extract_crs_from_ads(nc_path)
                if crs and transform:
                    self.main_ui.current_crs = crs
                    self.main_ui.current_geotransform = transform
        if not self.main_ui.current_crs or not self.main_ui.current_geotransform:
            return
        try:
            from pyproj import Transformer
            tr = Transformer.from_crs("EPSG:4326", self.main_ui.current_crs, always_xy=True)
            inv_gt = ~self.main_ui.current_geotransform
            scene = self.main_ui.graphics_view.scene()
            if not scene:
                return
            pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
            if not pixmap_item:
                return
            img_w = pixmap_item.pixmap().width()
            img_h = pixmap_item.pixmap().height()
            is_beta = self._is_beta_viewport()
            if is_beta:
                ref_size = self._get_ref_grid_size()
                if ref_size is not None:
                    ref_w, ref_h = ref_size
                    ref_grid = ref_w
                    scale = img_w / ref_grid if ref_grid > 0 else 1.0
                else:
                    ref_grid = int(round(2 * abs(self.main_ui.current_geotransform.c) / abs(self.main_ui.current_geotransform.a))) if abs(self.main_ui.current_geotransform.a) > 0 else 0
                    scale = img_w / ref_grid if ref_grid > 0 else 1.0
            else:
                ref_grid = int(round(2 * abs(self.main_ui.current_geotransform.c) / abs(self.main_ui.current_geotransform.a))) if abs(self.main_ui.current_geotransform.a) > 0 else 0
                scale = img_w / ref_grid if ref_grid > 0 else 1.0
            n, s, w, e = region_bounds["north"], region_bounds["south"], region_bounds["west"], region_bounds["east"]
            img_scene_pos = pixmap_item.pos()
            _gq_zoom = self._get_quality_grid()
            if _gq_zoom is not None:
                _res_m, _half = _gq_zoom[2], _gq_zoom[3]
                def _ll2pix(lon, lat):
                    xp, yp = tr.transform(lon, lat)
                    return (_half + xp) / _res_m, (_half - yp) / _res_m
            else:
                def _ll2pix(lon, lat):
                    xp, yp = tr.transform(lon, lat)
                    col, row = inv_gt * (xp, yp)
                    return img_scene_pos.x() + col * scale, img_scene_pos.y() + row * scale
            x1, y1 = _ll2pix(w, n)
            x2, y2 = _ll2pix(e, s)
            if None in (x1, y1, x2, y2):
                return
            px = min(x1, x2)
            py = min(y1, y2)
            pw = abs(x2 - x1)
            ph = abs(y2 - y1)
            if pw <= 0 or ph <= 0:
                return
            from PySide6.QtCore import QRectF
            self.main_ui.graphics_view.fitInView(QRectF(px, py, pw, ph), Qt.KeepAspectRatio)
            self.main_ui.graphics_view._user_has_zoomed = True
            self.main_ui.graphics_view.zoom_factor = self.main_ui.graphics_view.transform().m11()
            self.log(f"[Region] Zoomed to {sector}: [{w:.1f}, {e:.1f}] x [{s:.1f}, {n:.1f}] "
                     f"-> pixel rect ({px:.0f},{py:.0f} {pw:.0f}x{ph:.0f})")
        except Exception as exc:
            self.log(f"[Region] Zoom to {sector} failed: {exc}")

    def _run_latest_symlink(self):
        try:
            if getattr(sys, 'frozen', False):
                exe_path = str(top_dir / 'latest_symlink.exe')
                subprocess.run([exe_path], timeout=10, capture_output=True)
            else:
                symlink_script = top_dir / 'Process' / 'tools' / 'latest_symlink.py'
                if symlink_script.exists():
                    subprocess.run([sys.executable, str(symlink_script)], timeout=10, capture_output=True)
        except Exception:
            pass
        if not self.main_ui.current_base:
            self.main_ui.load_preview_images()

    def _ensure_temp_satellite(self, sat_id, sat_label):
        """Add a temp satellite to combo if not in permanent list, return the index."""
        if hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo:
            for i in range(self.main_ui.sat_combo.count()):
                if self.main_ui.sat_combo.itemData(i) == sat_id:
                    return i
            self.main_ui.sat_combo.blockSignals(True)
            self.main_ui.sat_combo.addItem(sat_label, sat_id)
            idx = self.main_ui.sat_combo.count() - 1
            self.main_ui._temp_satellite = sat_id
            self.main_ui.sat_combo.blockSignals(False)
            return idx
        return -1

    def _remove_temp_satellite(self):
        if self.main_ui._temp_satellite and hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo:
            for i in range(self.main_ui.sat_combo.count()):
                if self.main_ui.sat_combo.itemData(i) == self.main_ui._temp_satellite:
                    self.main_ui.sat_combo.blockSignals(True)
                    self.main_ui.sat_combo.removeItem(i)
                    self.main_ui.sat_combo.blockSignals(False)
                    break
            self.main_ui._temp_satellite = None

    def _on_sat_combo_changed(self, text):
        if not getattr(self.main_ui, '_removing_temp_sat', False) and self.main_ui._temp_satellite and hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo:
            current_data = self.main_ui.sat_combo.currentData()
            if current_data != self.main_ui._temp_satellite:
                self.main_ui._removing_temp_sat = True
                self._remove_temp_satellite()
                self.main_ui._removing_temp_sat = False
        if getattr(self.main_ui, 'pwards_stream', None) is not None and self.main_ui.sat_combo.currentData() != "pwards":
            self.main_ui.pwards_stream.stop()
        _eng = get_engine(text)
        _prods = get_products(text)
        self.log(f"Satellite switched to '{text}' -> using engine from {_eng.__module__} ({len(_prods)} products)")

    def process_dropped_nc_files(self, files):
        """Handle dropped NetCDF files -- GOES native, GK-2A, Meteosat, MTSAT, Himawari JMA/ADS, or app-processed."""
        from PySide6.QtWidgets import QMessageBox, QApplication
        from src.parsers.mtsat_parser import is_mtsat_file
        import re, json
        from datetime import datetime, timedelta, timezone
        from pathlib import Path

        self.log(f"process_dropped_nc_files called with {len(files)} file(s)")
        if not files:
            self.log("No files, returning")
            return

        try:
            QApplication.processEvents()
            sidecar_files = []
            goes_files = []
            gk2a_files = []
            meteosat_files = []
            mtsat_files = []
            hrit_files = []

            for fp in files:
                p = Path(fp)
                self.log(f"Checking file: {p.name}")
                if p.with_suffix(".ads.json").exists() or any(p.parent.glob("AHI_*.ads.json")):
                    self.log("Has sidecar")
                    sidecar_files.append(p)
                elif p.name.upper().startswith("HRIT_MTSAT") and not p.name.lower().endswith(".gz"):
                    self.log("Is MTSAT HRIT binary")
                    hrit_files.append(p)
                elif re.search(r'C\d{2}', p.name) and re.search(r'_G\d{2}_', p.name):
                    self.log("Is GOES native")
                    goes_files.append(p)
                elif p.name.lower().startswith("gk2a") or "gk-2a" in p.name.lower():
                    self.log("Is GK-2A")
                    gk2a_files.append(p)
                elif re.search(r'NC_H\d{2}_\d{8}_\d{4}_', p.name):
                    self.log("Is Himawari JMA (by filename)")
                    sidecar_files.append(p)
                elif re.search(r'M\d_FCI_\d{8}_\d{4}_', p.name):
                    self.log("Is MTG FCI (by filename)")
                    meteosat_files.append(p)
                elif "meteosat" in p.name.lower() or "msg" in p.name.lower() or "seviri" in p.name.lower():
                    self.log("Is Meteosat (by filename)")
                    meteosat_files.append(p)
                elif re.search(r'MT\d_', p.name):
                    self.log("Is MTSAT (by filename)")
                    mtsat_files.append(p)

            # Check unclassified files by opening NC metadata
            remaining = [Path(fp) for fp in files
                         if Path(fp) not in sidecar_files
                         and Path(fp) not in goes_files
                         and Path(fp) not in gk2a_files
                         and Path(fp) not in meteosat_files
                         and Path(fp) not in mtsat_files
                         and Path(fp) not in hrit_files]
            for fp in remaining:
                try:
                    import xarray as xr
                    with xr.open_dataset(fp, engine="netcdf4") as ds:
                        dims = list(ds.dims)
                        sat = str(ds.attrs.get("satellite_name", "")).lower()
                        inst = str(ds.attrs.get("institution", "")).lower()
                        if "eumetsat" in inst:
                            self.log("Is Meteosat (by institution attr)")
                            meteosat_files.append(fp)
                        elif "gk-2a" in sat or "gk2a" in sat or "ami" in inst:
                            self.log("Is GK-2A (by satellite attr)")
                            gk2a_files.append(fp)
                        elif "himawari" in sat and ("lon" in dims and "lat" in dims):
                            self.log("Is Himawari JMA NC (lon/lat dims)")
                            sidecar_files.append(fp)
                        elif "himawari" in sat:
                            self.log("Is Himawari (by attr)")
                            sidecar_files.append(fp)
                        elif "meteosat" in sat or "msg" in sat:
                            self.log("Is Meteosat (by satellite attr)")
                            meteosat_files.append(fp)
                        else:
                            mtsat = is_mtsat_file(fp)
                            if mtsat:
                                self.log("Is MTSAT (by attr/structure)")
                                mtsat_files.append(fp)
                except Exception:
                    pass

            self.log(f"Classified: {len(sidecar_files)} sidecar/Himawari, {len(goes_files)} GOES, "
                     f"{len(gk2a_files)} GK2A, {len(meteosat_files)} Meteosat, {len(mtsat_files)} MTSAT, "
                     f"{len(hrit_files)} MTSAT HRIT")

            # --- Multi-timestamp: play dropped frames as an animation sequence ---
            # When several files of the SAME satellite but DIFFERENT times are
            # dropped together, load them as an animation instead of a single scene.
            try:
                if self._try_dropped_as_animation(sidecar_files, goes_files, gk2a_files,
                                                  meteosat_files, mtsat_files, hrit_files):
                    self.log("Dropped files loaded as an animation sequence.")
                    return
            except Exception as _anim_err:
                import traceback
                self.log(f"Drop-animation attempt skipped, falling back to single scene: {_anim_err}")
                traceback.print_exc()

            # --- App-processed NC (sidecar) or Himawari ADS/JMA/JAXA ---
            if sidecar_files:
                from src.parsers.himawari_jaxa_parser import is_himawari_jaxa_nc, extract_himawari_jaxa_bands
                _sample = sidecar_files[0]
                is_jaxa = is_himawari_jaxa_nc(_sample)
                if is_jaxa:
                    self.log("Processing as Himawari JAXA NC")
                    self.main_ui._band_nc_map = extract_himawari_jaxa_bands(sidecar_files)
                    band_names = sorted(self.main_ui._band_nc_map.keys())
                    nc_path = sidecar_files[0]
                    self.main_ui.current_nc_path = nc_path
                    self.main_ui.available_bands = band_names
                    self.main_ui._build_band_checkboxes(band_names)
                    self.main_ui._show_caching_dialog(nc_path, band_names)
                    self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                    QApplication.processEvents()
                    return

                from src.parsers.himawari_jma_parser import is_himawari_jma_nc, extract_himawari_jma_bands, read_himawari_jma_band_data
                is_jma = is_himawari_jma_nc(_sample)
                if is_jma:
                    self.log("Processing as Himawari JMA NC")
                    self.main_ui._band_nc_map = extract_himawari_jma_bands(sidecar_files)
                    if not self.main_ui._band_nc_map:
                        self.log("JMA: no bands extracted, using filename-based mapping")
                        self.main_ui._band_nc_map = {}
                        for f in sidecar_files:
                            m = re.search(r'B(\d{2})', f.name)
                            if m:
                                self.main_ui._band_nc_map[f"B{m.group(1)}"] = f
                    band_names = sorted(self.main_ui._band_nc_map.keys())
                    if not band_names:
                        band_names = ["B01"]
                    nc_path = sidecar_files[0]
                    self.main_ui.current_nc_path = nc_path
                    self.main_ui.available_bands = band_names
                    self.main_ui._build_band_checkboxes(band_names)
                    self.main_ui._show_caching_dialog(nc_path, band_names)
                    self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                    QApplication.processEvents()
                    return
                # Existing sidecar flow
                all_bands = []
                self.main_ui._band_nc_map = {}
                for nc_path in sidecar_files:
                    ads_path = nc_path.with_suffix(".ads.json")
                    if not ads_path.exists():
                        for _f in nc_path.parent.glob("AHI_*.ads.json"):
                            ads_path = _f
                            break
                    if ads_path.exists():
                        with open(ads_path) as f:
                            ads = json.load(f)
                        band_names = list(ads.get("band_stats", {}).keys()) or ["B01"]
                    else:
                        band_names = ["B01"]
                    all_bands.extend(band_names)
                    for bn in band_names:
                        self.main_ui._band_nc_map[bn] = nc_path
                self.main_ui.current_nc_path = sidecar_files[0]
                self.main_ui.available_bands = all_bands
                self.main_ui._build_band_checkboxes(all_bands)
                self.main_ui._show_caching_dialog(Path(files[0]), all_bands)
                self.main_ui._start_raw_band_cache(Path(files[0]), all_bands, band_file_map=self.main_ui._band_nc_map)
                QApplication.processEvents()
                return

            # --- Native GOES NC ---
            if goes_files:
                first_path = goes_files[0]
                self.log(f"Processing GOES: {first_path.name}")

                m_sat = re.search(r'_G(\d{2})_', first_path.name)
                m_ts = re.search(r'_s(\d{4})(\d{3})(\d{2})(\d{2})\d{3}', first_path.name)
                m_prod = re.search(r'OR_([^_]+)', first_path.name)
                self.log(f"Regex: sat={bool(m_sat)} ts={bool(m_ts)} prod={bool(m_prod)}")

                if m_sat:
                    sat_val = f"GOES {m_sat.group(1)}"
                    if hasattr(self.main_ui, 'sat_combo'):
                        self.main_ui.sat_combo.setCurrentText(sat_val)
                    self.log(f"Sat set to {sat_val}")

                if m_prod:
                    if hasattr(self.main_ui, 'type_combo'):
                        self.main_ui.type_combo.blockSignals(True)
                        self.main_ui.type_combo.clear()
                        self.main_ui.type_combo.addItem("Full Disk", "Full")
                        self.main_ui.type_combo.addItem("CONUS", "CONUS")
                        self.main_ui.type_combo.addItem("Meso", "Meso")
                        prod_str = m_prod.group(1)
                        if "RadF" in prod_str:
                            type_val = "Full"
                        elif "RadM" in prod_str:
                            type_val = "Meso"
                        else:
                            type_val = "CONUS"
                        idx = self.main_ui.type_combo.findData(type_val)
                        if idx >= 0:
                            self.main_ui.type_combo.setCurrentIndex(idx)
                        self.main_ui.type_combo.blockSignals(False)
                    self.log(f"Type set based on {m_prod.group(1)}")

                if m_ts:
                    try:
                        y, doy, h, mi = m_ts.groups()
                        self.log(f"TS parsed: y={y} doy={doy} h={h} mi={mi}")
                        dt = datetime(int(y), 1, 1) + timedelta(days=int(doy) - 1)
                        month, day = f"{dt.month:02d}", f"{dt.day:02d}"
                        for attr, val in [('year_combo', y), ('month_combo', month),
                                           ('day_combo', day), ('hour_combo', h), ('minute_combo', mi)]:
                            if hasattr(self.main_ui, attr):
                                getattr(self.main_ui, attr).blockSignals(True)
                                getattr(self.main_ui, attr).setCurrentText(val)
                                getattr(self.main_ui, attr).blockSignals(False)
                        self.main_ui.current_datetime = f"{y}_{month}_{day}_{h}{mi}"
                        self.log(f"Datetime set to {self.main_ui.current_datetime}")
                    except Exception as e:
                        self.log(f"TS error: {e}")

                # Try satpy if all dropped files share a common parent
                try:
                    parents = {p.parent for p in goes_files}
                    if len(parents) == 1:
                        common_parent = parents.pop()
                        self.log(f"All GOES files share parent: {common_parent} â€” trying satpy...")
                        if self._load_goes_satpy(common_parent):
                            self.log("process_dropped_nc_files complete (via satpy)")
                            QApplication.processEvents()
                            return
                except Exception as satpy_err:
                    self.log(f"Satpy attempt failed: {satpy_err}")

                self.main_ui._band_nc_map = {}
                mapped_bands = []
                self.log("Building band map...")
                for nc_path in goes_files:
                    m_band = re.search(r'C(\d{2})', nc_path.name)
                    if m_band:
                        c_band = f"C{m_band.group(1)}"
                        b_band = self.main_ui.GOES_BAND_MAP.get(c_band, c_band)
                        self.main_ui._band_nc_map[b_band] = nc_path
                        mapped_bands.append(b_band)
                        self.log(f"Mapped {c_band} -> {b_band}: {nc_path.name}")

                if not mapped_bands:
                    self.log("No mapped bands, returning")
                    return
                self.log(f"Mapped {len(mapped_bands)} bands")

                self.main_ui.current_nc_path = first_path
                self.main_ui.available_bands = mapped_bands
                self.main_ui.current_crs = None
                self.main_ui.current_geotransform = None
                self.main_ui._overlay_geo_unavailable = False
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                self.main_ui.overlay_controller._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(mapped_bands)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.log("Building band checkboxes...")
                self.main_ui._build_band_checkboxes(mapped_bands)
                self.main_ui.status_bar.showMessage(f"Loading {len(mapped_bands)} GOES bands...")
                self.log("Showing caching dialog...")
                self.main_ui._show_caching_dialog(first_path, mapped_bands)
                self.log("Starting raw band cache...")
                self.main_ui._start_raw_band_cache(first_path, mapped_bands, band_file_map=self.main_ui._band_nc_map)
                self.log("process_dropped_nc_files complete")
                QApplication.processEvents()
                return

            # --- GK-2A NC ---
            if gk2a_files:
                from src.parsers.gk2a_parser import parse_gk2a_metadata, extract_gk2a_bands, read_gk2a_band_data, extract_gk2a_crs
                self.log("Processing GK-2A files")
                meta = parse_gk2a_metadata(gk2a_files[0])
                self._ensure_temp_satellite("gk2a", "GK-2A")
                if hasattr(self.main_ui, 'sat_combo'):
                    self.main_ui.sat_combo.blockSignals(True)
                    self.main_ui.sat_combo.setCurrentText("GK-2A")
                    self.main_ui.sat_combo.blockSignals(False)
                if meta.get("timestamp"):
                    ts = meta["timestamp"]
                    parts = ts.split("_")
                    if len(parts) == 4:
                        y, m, d, hm = parts
                        h, mi = hm[:2], hm[2:]
                        for attr, val in [('year_combo', y), ('month_combo', m),
                                           ('day_combo', d), ('hour_combo', h), ('minute_combo', mi)]:
                            if hasattr(self.main_ui, attr):
                                getattr(self.main_ui, attr).blockSignals(True)
                                getattr(self.main_ui, attr).setCurrentText(val)
                                getattr(self.main_ui, attr).blockSignals(False)
                        self.main_ui.current_datetime = ts
                        self.log(f"GK-2A datetime set to {ts}")
                if hasattr(self.main_ui, 'type_combo'):
                    self.main_ui.type_combo.blockSignals(True)
                    self.main_ui.type_combo.clear()
                    self.main_ui.type_combo.addItem("Full Disk", "Full")
                    self.main_ui.type_combo.blockSignals(False)
                band_map = extract_gk2a_bands(gk2a_files)
                if not band_map:
                    band_map = {f"B{(i%16)+1:02d}": Path(fp) for i, fp in enumerate(gk2a_files)}
                self.main_ui._band_nc_map = band_map
                band_names = sorted(band_map.keys())
                nc_path = gk2a_files[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                
                # Extract GK-2A CRS and geotransform
                from src.parsers.gk2a_parser import extract_gk2a_crs
                crs, gt = extract_gk2a_crs(nc_path)
                self.main_ui.current_crs = crs
                self.main_ui.current_geotransform = gt
                self.main_ui._overlay_geo_unavailable = False
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                if hasattr(self.main_ui, 'overlay_controller'):
                    self.main_ui.overlay_controller._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.main_ui.status_bar.showMessage(f"Loading {len(band_names)} GK-2A bands...")
                self.main_ui._show_caching_dialog(nc_path, band_names)
                self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                self.log("GK-2A processing complete")
                QApplication.processEvents()
                return

            # --- MTSAT HRIT binary ---
            if hrit_files:
                from src.parsers.hrit_parser import (parse_hrit_metadata, extract_hrit_bands,
                                                     extract_hrit_crs)
                self.log("Processing MTSAT HRIT files")
                meta = parse_hrit_metadata(hrit_files[0])
                sat_label = "MTSAT-1R"
                sat_id = "mtsat-1r"
                if hasattr(self.main_ui, 'sat_combo'):
                    self.main_ui.sat_combo.blockSignals(True)
                    found = False
                    for i in range(self.main_ui.sat_combo.count()):
                        if self.main_ui.sat_combo.itemText(i) == sat_label:
                            self.main_ui.sat_combo.setCurrentIndex(i)
                            found = True
                            break
                    if not found:
                        self._ensure_temp_satellite(sat_id, sat_label)
                        self.main_ui.sat_combo.setCurrentText(sat_label)
                    self.main_ui.sat_combo.blockSignals(False)
                if meta.get("timestamp"):
                    ts = meta["timestamp"]
                    parts = ts.split("_")
                    if len(parts) == 4:
                        y, m, d, hm = parts
                        h, mi = hm[:2], hm[2:]
                        for attr, val in [('year_combo', y), ('month_combo', m),
                                          ('day_combo', d), ('hour_combo', h), ('minute_combo', mi)]:
                            if hasattr(self.main_ui, attr):
                                getattr(self.main_ui, attr).blockSignals(True)
                                getattr(self.main_ui, attr).setCurrentText(val)
                                getattr(self.main_ui, attr).blockSignals(False)
                        self.main_ui.current_datetime = ts
                        self.log(f"MTSAT HRIT datetime set to {ts}")
                if hasattr(self.main_ui, 'type_combo'):
                    self.main_ui.type_combo.blockSignals(True)
                    self.main_ui.type_combo.clear()
                    self.main_ui.type_combo.addItem("Full Disk", "Full")
                    self.main_ui.type_combo.blockSignals(False)
                band_map = extract_hrit_bands(hrit_files)
                if not band_map:
                    self.log("MTSAT HRIT: no bands extracted")
                    return
                self.main_ui._band_nc_map = band_map
                band_names = sorted(band_map.keys())
                nc_path = hrit_files[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                crs, gt = extract_hrit_crs(nc_path)
                self.main_ui.current_crs = crs
                self.main_ui.current_geotransform = gt
                # HRIT is native GEOS full-disk imagery like GK-2A: no
                # plate-carree display box, overlays project via geos CRS.
                self.main_ui._overlay_geo_unavailable = crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                if hasattr(self.main_ui, 'overlay_controller'):
                    self.main_ui.overlay_controller._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.main_ui.status_bar.showMessage(f"Loading {len(band_names)} MTSAT HRIT bands...")
                self.main_ui._show_caching_dialog(nc_path, band_names)
                self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                self.log("MTSAT HRIT processing complete")
                QApplication.processEvents()
                return

            # --- MTSAT NC ---
            if mtsat_files:
                from src.parsers.mtsat_parser import (parse_mtsat_metadata, extract_mtsat_bands,
                                                      extract_mtsat_crs, extract_mtsat_extent)
                self.log("Processing MTSAT files")
                meta = parse_mtsat_metadata(mtsat_files[0])
                sat_label = meta.get("sat_label", "MTSAT")
                sat_id = meta.get("satellite", "mtsat")
                if hasattr(self.main_ui, 'sat_combo'):
                    self.main_ui.sat_combo.blockSignals(True)
                    found = False
                    for i in range(self.main_ui.sat_combo.count()):
                        if self.main_ui.sat_combo.itemText(i) == sat_label:
                            self.main_ui.sat_combo.setCurrentIndex(i)
                            found = True
                            break
                    if not found:
                        self._ensure_temp_satellite(sat_id, sat_label)
                        self.main_ui.sat_combo.setCurrentText(sat_label)
                    self.main_ui.sat_combo.blockSignals(False)
                if meta.get("timestamp"):
                    ts = meta["timestamp"]
                    parts = ts.split("_")
                    if len(parts) == 4:
                        y, m, d, hm = parts
                        h, mi = hm[:2], hm[2:]
                        for attr, val in [('year_combo', y), ('month_combo', m),
                                          ('day_combo', d), ('hour_combo', h), ('minute_combo', mi)]:
                            if hasattr(self.main_ui, attr):
                                getattr(self.main_ui, attr).blockSignals(True)
                                getattr(self.main_ui, attr).setCurrentText(val)
                                getattr(self.main_ui, attr).blockSignals(False)
                        self.main_ui.current_datetime = ts
                        self.log(f"MTSAT datetime set to {ts}")
                if hasattr(self.main_ui, 'type_combo'):
                    self.main_ui.type_combo.blockSignals(True)
                    self.main_ui.type_combo.clear()
                    self.main_ui.type_combo.addItem("Full Disk", "Full")
                    self.main_ui.type_combo.blockSignals(False)
                band_map = extract_mtsat_bands(mtsat_files)
                if not band_map:
                    self.log("MTSAT: no channels extracted")
                    return
                self.main_ui._band_nc_map = band_map
                band_names = sorted(band_map.keys())
                nc_path = mtsat_files[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                crs, gt = extract_mtsat_crs(nc_path)
                self.main_ui.current_crs = crs
                self.main_ui.current_geotransform = gt
                extent = extract_mtsat_extent(nc_path)
                if extent:
                    self.main_ui._display_projection_extent = extent
                    self.main_ui._current_display_projection = "plate_carree"
                    self.main_ui._display_projection_crs = crs
                self.main_ui._overlay_geo_unavailable = crs is None
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                if hasattr(self.main_ui, 'overlay_controller'):
                    self.main_ui.overlay_controller._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.main_ui.status_bar.showMessage(f"Loading {len(band_names)} MTSAT bands...")
                self.main_ui._show_caching_dialog(nc_path, band_names)
                self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                self.log("MTSAT processing complete")
                QApplication.processEvents()
                return

            # --- Meteosat NC ---
            if meteosat_files:
                from src.parsers.meteosat_parser import parse_meteosat_metadata, extract_meteosat_bands, read_meteosat_band_data
                self.log("Processing Meteosat files")
                meta = parse_meteosat_metadata(meteosat_files[0])
                sat_id = meta.get("satellite", "meteosat11")
                sat_label = "Meteosat 11"
                num_map = {"meteosat9": "Meteosat 9", "meteosat10": "Meteosat 10",
                           "meteosat11": "Meteosat 11", "meteosat12": "Meteosat 12"}
                sat_label = num_map.get(sat_id, "Meteosat 11")
                if hasattr(self.main_ui, 'sat_combo'):
                    self.main_ui.sat_combo.blockSignals(True)
                    found = False
                    for i in range(self.main_ui.sat_combo.count()):
                        if self.main_ui.sat_combo.itemText(i) == sat_label:
                            self.main_ui.sat_combo.setCurrentIndex(i)
                            found = True
                            break
                    if not found:
                        self._ensure_temp_satellite(sat_id, sat_label)
                        self.main_ui.sat_combo.setCurrentText(sat_label)
                    self.main_ui.sat_combo.blockSignals(False)
                if meta.get("timestamp"):
                    ts = meta["timestamp"]
                    parts = ts.split("_")
                    if len(parts) == 4:
                        y, m, d, hm = parts
                        h, mi = hm[:2], hm[2:]
                        for attr, val in [('year_combo', y), ('month_combo', m),
                                           ('day_combo', d), ('hour_combo', h), ('minute_combo', mi)]:
                            if hasattr(self.main_ui, attr):
                                getattr(self.main_ui, attr).blockSignals(True)
                                getattr(self.main_ui, attr).setCurrentText(val)
                                getattr(self.main_ui, attr).blockSignals(False)
                        self.main_ui.current_datetime = ts
                        self.log(f"Meteosat datetime set to {ts}")
                if hasattr(self.main_ui, 'type_combo'):
                    self.main_ui.type_combo.blockSignals(True)
                    self.main_ui.type_combo.clear()
                    self.main_ui.type_combo.addItem("Full Disk", "Full")
                    self.main_ui.type_combo.blockSignals(False)
                band_map = extract_meteosat_bands(meteosat_files)
                if not band_map:
                    band_map = {f"B{(i%12)+1:02d}": Path(fp) for i, fp in enumerate(meteosat_files)}
                self.main_ui._band_nc_map = band_map
                band_names = sorted(band_map.keys())
                nc_path = meteosat_files[0]
                self.main_ui.current_nc_path = nc_path
                self.main_ui.available_bands = band_names
                self.main_ui.current_crs = None
                self.main_ui.current_geotransform = None
                self.main_ui._overlay_geo_unavailable = True
                self.main_ui._ir_kelvin = None
                self.main_ui.current_winds_uv = None
                self.main_ui.winds_enabled = False
                self.main_ui._wind_proj_cache.clear()
                if hasattr(self.main_ui, 'overlay_controller'):
                    self.main_ui.overlay_controller._update_winds_checkbox_state()
                if hasattr(self.main_ui, 'update_devkit_band_lists'):
                    self.main_ui.update_devkit_band_lists(band_names)
                self.main_ui.cache.clear_raw()
                self.main_ui.cache.clear_rgb()
                self.main_ui.cache.clear_precached()
                self.main_ui._build_band_checkboxes(band_names)
                self.main_ui.status_bar.showMessage(f"Loading {len(band_names)} Meteosat bands...")
                self.main_ui._show_caching_dialog(nc_path, band_names)
                self.main_ui._start_raw_band_cache(nc_path, band_names, band_file_map=self.main_ui._band_nc_map)
                self.log("Meteosat processing complete")
                QApplication.processEvents()
                return

        except Exception as e:
            QMessageBox.critical(self, "Drop Error", f"ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.log(f"CRITICAL ERROR: {e} - {traceback.format_exc()}")

    # ------------------------------------------------------------------
    # Drop-animation helpers: multi-timestamp drops become animation frames
    # ------------------------------------------------------------------
    def _try_dropped_as_animation(self, sidecar_files, goes_files, gk2a_files,
                                  meteosat_files, mtsat_files, hrit_files):
        """If any satellite family was dropped at >= 2 distinct times, load it
        as an animation sequence and return True."""
        categories = [
            ("goes", list(goes_files)),
            ("himawari", list(sidecar_files)),
            ("gk2a", list(gk2a_files)),
            ("meteosat", list(meteosat_files)),
            ("mtsat", list(mtsat_files)),
        ]
        hrit_groups = self._group_dropped_by_timestamp(hrit_files, "hrit")
        if len(hrit_groups) >= 2:
            categories.append(("hrit", list(hrit_files)))
        elif hrit_files:
            # HRIT is MTSAT imagery; fold into the mtsat family detection.
            categories.append(("mtsat", list(mtsat_files) + list(hrit_files)))

        for family, files in categories:
            groups = self._group_dropped_by_timestamp(files, family)
            if len(groups) >= 2:
                self.log(f"Drop animation: {len(groups)} distinct time(s) of {family} detected.")
                if self._load_dropped_animation(family, groups):
                    return True
        return False

    def _group_dropped_by_timestamp(self, files, family):
        groups = OrderedDict()
        for fp in files:
            ts, _band = self._dropped_frame_info(fp, family)
            if not ts:
                continue
            groups.setdefault(ts, []).append(Path(fp))
        for g in groups.values():
            g.sort(key=lambda p: p.name)
        return OrderedDict(sorted(groups.items()))

    def _dropped_frame_info(self, fp, family):
        """Return (timestamp_key, band) for a dropped file, or (None, None)."""
        name = Path(fp).name
        if family == "goes":
            m = re.search(r'_s(\d{4})(\d{3})(\d{2})(\d{2})\d{3}', name)
            if not m:
                return None, None
            return f"{m.group(1)}_{int(m.group(2)):03d}_{m.group(3)}{m.group(4)}", None
        if family == "himawari":
            m = re.search(r'_(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})\.nc$', name)
            if m:
                return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
            m = re.search(r'_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', name)
            if m:
                return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
            return None, None
        if family == "gk2a":
            m = re.search(r'_fd\d{3}ge_(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})\.nc$', name, re.IGNORECASE)
            if not m:
                return None, None
            return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
        if family == "mtsat":
            m = re.search(r'MT\d_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_', name, re.IGNORECASE)
            if not m:
                return None, None
            return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
        if family == "hrit":
            m = re.search(r'HRIT_MTSAT1_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_DK01', name, re.IGNORECASE)
            if not m:
                return None, None
            return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
        if family == "meteosat":
            m = re.search(r'FCI_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', name, re.IGNORECASE)
            if m:
                return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
            m = re.search(r'_(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})\.nc$', name)
            if m:
                return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
            m = re.search(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', name)
            if m:
                return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}", None
            return None, None
        return None, None

    def _load_dropped_animation(self, family, groups):
        """Build animation frame descriptors (timestamp, nc_path, band_file_map)."""
        frames = []
        for ts, files in groups.items():
            bfm = self._band_map_for_frames(family, files)
            if not bfm:
                self.log(f"Drop animation: no bands found for {family} at {ts}; skipping this frame.")
                continue
            frames.append((ts, files[0], bfm))
        if len(frames) < 2:
            self.log(f"Drop animation: fewer than 2 animatable frames for {family}.")
            return False

        if family == "goes":
            engine_label = self._goes_anim_engine_label(frames)
        elif family in ("mtsat", "hrit"):
            engine_label = "himawari9"
        elif family == "gk2a":
            engine_label = "GK-2A"
        elif family == "meteosat":
            engine_label = "meteosat11"
        else:
            engine_label = "himawari9"

        controller = getattr(self.main_ui, "animation_controller", None)
        if controller is None:
            self.log("Drop animation: no animation controller available.")
            return False
        return controller.load_dropped_animation(
            frames, engine_label=engine_label, autoplay=True)

    def _band_map_for_frames(self, family, files):
        paths = [Path(f) for f in files]
        try:
            if family == "goes":
                bfm = {}
                for fp in paths:
                    bm = re.search(r'C(\d{2})', fp.name)
                    if bm:
                        c_num = int(bm.group(1))
                        if c_num == 2:
                            bfm["B03"] = fp
                        elif c_num == 3:
                            bfm["B02"] = fp
                        else:
                            bfm[f"B{c_num:02d}"] = fp
                        bfm[f"C{c_num:02d}"] = fp
                return bfm
            if family == "himawari":
                from src.parsers.himawari_jaxa_parser import is_himawari_jaxa_nc, extract_himawari_jaxa_bands
                from src.parsers.himawari_jma_parser import is_himawari_jma_nc, extract_himawari_jma_bands
                if is_himawari_jaxa_nc(paths[0]):
                    return extract_himawari_jaxa_bands(paths)
                if is_himawari_jma_nc(paths[0]):
                    return extract_himawari_jma_bands(paths)
                return self._extract_sidecar_band_map(paths)
            if family == "gk2a":
                from src.parsers.gk2a_parser import extract_gk2a_bands
                return extract_gk2a_bands(paths)
            if family == "meteosat":
                from src.parsers.meteosat_parser import extract_meteosat_bands
                return extract_meteosat_bands(paths)
            if family == "mtsat":
                from src.parsers.mtsat_parser import extract_mtsat_bands
                return extract_mtsat_bands(paths)
            if family == "hrit":
                from src.parsers.hrit_parser import extract_hrit_bands
                return extract_hrit_bands(paths)
        except Exception:
            import traceback
            self.log(f"Drop animation: band map extraction failed ({family}): {traceback.format_exc()}")
        return {}

    def _extract_sidecar_band_map(self, paths):
        bfm = {}
        for fp in paths:
            bm = re.search(r'_B(\d{2})_', fp.name)
            if bm:
                bfm[f"B{bm.group(1)}"] = fp
                continue
            sidecar = fp.with_suffix(".ads.json")
            if not sidecar.exists():
                for f in fp.parent.glob("AHI_*.ads.json"):
                    sidecar = f
                    break
            if sidecar.exists():
                try:
                    with open(sidecar, "r", encoding="utf-8") as f:
                        ads = json.load(f)
                    keys = list(ads.get("band_stats", {}).keys()) or ["B01"]
                    for k in keys:
                        bfm[k] = fp
                except Exception:
                    bfm.setdefault("B01", fp)
            else:
                bfm.setdefault("B01", fp)
        return bfm

    def _goes_anim_engine_label(self, frames):
        for _ts, nc, _bfm in frames:
            m = re.search(r'_G(\d{2})_', str(nc))
            if m:
                return f"GOES {int(m.group(1))}"
        return "GOES 16"

    def process_file(self, fp):
        if fp.lower().endswith('.nc'):
            self.log("NetCDF files cannot be opened directly. Use band selection.")
            return False
        fpath = Path(fp)
        if is_sataid_file(fpath):
            self.log(f"SATAID file detected: {fpath.name}")
            self._handle_sataid_drop(fpath)
            return True
        if re.search(r'\.z\d{4}$', fpath.name.lower()):
            info = parse_filename(str(fpath))
            if info.get("date") and info.get("band"):
                try:
                    sat = read_sataid_cached(fpath)
                    self.main_ui._sataid_current_data = sat.data
                    self.main_ui._sataid_current_lat = sat.lat
                    self.main_ui._sataid_current_lon = sat.lon
                    self.main_ui._sataid_channel = sat.channel_name
                    self.main_ui._sataid_units = sat.units
                    if sat.data is not None and sat.data.size > 0:
                        if not hasattr(self.main_ui, '_sataid_external'):
                            self.main_ui._sataid_external = {}
                        date_str = info["date"]
                        band = info["band"]
                        self.main_ui._sataid_external.setdefault(date_str, {})[band] = fpath
                        self._handle_sataid_drop(fpath)
                        self.main_ui._render_sataid(sat.data, 0.0, 1.0)
                        self.main_ui.current_original = fpath
                        self.main_ui.current_base = f"SATAID_{band}"
                        self.main_ui.status_bar.showMessage(f"SATAID: {band}")
                        return True
                except Exception as e:
                    self.log(f"SATAID fallback failed for {fpath.name}: {e}")
        self.log(f'Processing file: {fp}')
        self.main_ui.current_original = fpath
        self.main_ui.clear_overlays()
        transform, crs = self.extract_georeferencing(fp)
        self.main_ui.current_geotransform = transform
        self.main_ui.current_crs = crs
        self.main_ui._overlay_geo_unavailable = not (crs and transform)
        return self.main_ui.load_current_image()

    def _process_files(self, fps):
        failed = []
        for fp in fps:
            fpath = Path(fp)
            if not self.process_file(fp):
                failed.append(fpath.name)
        if failed:
            QMessageBox.warning(self, "Unsupported Format",
                f"Cannot open:\n{chr(10).join(failed)}"
                f"\n\nUnsupported or unrecognized file format.")

    def _handle_sataid_drop(self, path: Path):
        info = parse_filename(str(path))
        date_str = info.get("date", "")
        if len(date_str) == 8:
            try:
                y, m, d = date_str[:4], date_str[4:6], date_str[6:8]
                self.main_ui.year_combo.setCurrentText(y)
                self.main_ui.month_combo.setCurrentText(m)
                self.main_ui.day_combo.setCurrentText(d)
            except Exception:
                pass
        time_part = path.suffix.lstrip(".Z")
        if len(time_part) == 4 and time_part.isdigit():
            try:
                if hasattr(self.main_ui, 'hour_combo'):
                    self.main_ui.hour_combo.setCurrentText(time_part[:2])
                if hasattr(self.main_ui, 'minute_combo'):
                    self.main_ui.minute_combo.setCurrentText(time_part[2:])
            except Exception:
                pass
        if hasattr(self.main_ui, 'type_combo'):
            self.main_ui.type_combo.blockSignals(True)
            self.main_ui.type_combo.setCurrentText("SATAID")
            self.main_ui.type_combo.blockSignals(False)
        self._load_sataid_bands()
        self._cache_sataid_bands()
        band = info.get("band", "")
        if band and band in self.main_ui.band_checkboxes:
            self.main_ui.band_checkboxes[band].setChecked(True)

    def _handle_sataid_folder_drop(self, folder: Path):
        name_parts = folder.name.split("_")
        date_str = ""
        for part in name_parts:
            if part.isdigit() and len(part) == 8:
                date_str = part
                break
        if date_str:
            try:
                self.main_ui.year_combo.setCurrentText(date_str[:4])
                self.main_ui.month_combo.setCurrentText(date_str[4:6])
                self.main_ui.day_combo.setCurrentText(date_str[6:8])
            except Exception:
                pass
        if hasattr(self.main_ui, 'type_combo'):
            self.main_ui.type_combo.blockSignals(True)
            self.main_ui.type_combo.setCurrentText("SATAID")
            self.main_ui.type_combo.blockSignals(False)
        self._load_sataid_bands()
        self._cache_sataid_bands()

    def extract_georeferencing(self, tiff_path):
        if not HAS_GEO:
            return None, None
        try:
            with rasterio.open(tiff_path) as src:
                transform = src.transform
                crs = src.crs
                if crs is None:
                    return None, None
                return transform, CRS.from_wkt(crs.to_wkt())
        except Exception:
            return None, None

    def _populate_available_dates(self):
        self.main_ui._available_dates = self._discover_available_dates()
        self.main_ui.year_combo.blockSignals(True)
        self.main_ui.month_combo.blockSignals(True)
        self.main_ui.day_combo.blockSignals(True)
        try:
            self.main_ui.year_combo.clear()
            self.main_ui.year_combo.addItems(list(self.main_ui._available_dates.keys()))
            self._update_month_day_combos()
        finally:
            self.main_ui.year_combo.blockSignals(False)
            self.main_ui.month_combo.blockSignals(False)
            self.main_ui.day_combo.blockSignals(False)
        self._populate_available_times()

    def _discover_available_dates(self):
        dates = {}
        download_dir = Path(self.main_ui.input_dir)
        is_sataid = (hasattr(self.main_ui, 'type_combo') and
                     self.main_ui.type_combo.currentText() == "SATAID")
        if is_sataid:
            for folder in download_dir.glob("SATAID_*"):
                if not folder.is_dir():
                    continue
                parts = folder.name.split("_")
                if len(parts) >= 2 and len(parts[1]) == 8 and parts[1].isdigit():
                    y, m, d = parts[1][:4], parts[1][4:6], parts[1][6:8]
                    dates.setdefault(y, {}).setdefault(m, set()).add(d)
        else:
            sat = self.main_ui.sat_combo.currentData() or self.main_ui.sat_combo.currentText()
            is_goes = "goes" in sat.lower()
            is_gk2a = "gk2a" in sat.lower() or "gk-2a" in sat.lower()

            if is_gk2a:
                # GK-2A scenes are per-band NetCDF files named
                # gk2a_ami_le1b_*_<YYYYMMDDHHMM>.nc. The downloader places them under
                # varying folder layouts (quick-scene <product>_YYYYMMDD_HHMM, date-range
                # gk2a-pds/<product>_YYYY_MM_DD_HHMM, or any nested <product>_... form
                # since the AMI product token itself contains "/"). The filename's
                # trailing 12-digit scan time is the one stable key, so scan recursively
                # for gk2a-prefixed .nc files anywhere under the download root.
                if download_dir.exists():
                    for nc in download_dir.rglob("gk2a_*.nc"):
                        m = re.search(r'_(\d{12})\.nc$', nc.name)
                        if m:
                            ts = m.group(1)
                            dates.setdefault(ts[:4], {}).setdefault(ts[4:6], set()).add(ts[6:8])
            else:
                product_prefix = None
                if not is_goes and hasattr(self.main_ui, 'type_combo'):
                    product_prefix = self.get_current_product()
                is_meteosat = "meteosat" in sat.lower() or "msg" in sat.lower()
                base_path = download_dir / sat
                if base_path.exists():
                    for folder in base_path.iterdir():
                        if not folder.is_dir():
                            continue
                        segs = folder.name.split("_")
                        if product_prefix and segs[0] != product_prefix:
                            continue
                        if len(segs) >= 5 and segs[-4].isdigit() and len(segs[-4]) == 4 \
                           and segs[-3].isdigit() and len(segs[-3]) == 2 \
                           and segs[-2].isdigit() and len(segs[-2]) == 2:
                            y, m, d = segs[-4], segs[-3], segs[-2]
                            dates.setdefault(y, {}).setdefault(m, set()).add(d)
                        elif is_goes and len(segs) >= 4 \
                             and segs[-3].isdigit() and len(segs[-3]) == 4 \
                             and segs[-2].isdigit() and len(segs[-2]) == 3:
                            y = segs[-3]
                            doy = int(segs[-2])
                            dt = datetime(int(y), 1, 1) + timedelta(days=doy - 1)
                            m = f"{dt.month:02d}"
                            d = f"{dt.day:02d}"
                            dates.setdefault(y, {}).setdefault(m, set()).add(d)
                        elif is_meteosat and len(segs) >= 4 \
                             and segs[-3].isdigit() and len(segs[-3]) == 4 \
                             and segs[-2].isdigit() and len(segs[-2]) == 2:
                            y, m = segs[-3], segs[-2]
                            d = segs[-1].split("_")[0] if "_" not in segs[-1] else segs[-1][:2]
                            if d.isdigit():
                                dates.setdefault(y, {}).setdefault(m, set()).add(d)
        result = {}
        for y in sorted(dates):
            result[y] = {}
            for m in sorted(dates[y]):
                result[y][m] = sorted(dates[y][m])
        return result

    def _update_month_day_combos(self):
        prev_month = self.main_ui.month_combo.currentText()
        prev_day = self.main_ui.day_combo.currentText()
        month_blocked = self.main_ui.month_combo.signalsBlocked()
        day_blocked = self.main_ui.day_combo.signalsBlocked()
        if not month_blocked:
            self.main_ui.month_combo.blockSignals(True)
        if not day_blocked:
            self.main_ui.day_combo.blockSignals(True)
        try:
            year = self.main_ui.year_combo.currentText()
            months = list(self.main_ui._available_dates.get(year, {}).keys())
            self.main_ui.month_combo.clear()
            if months:
                self.main_ui.month_combo.addItems(months)
            idx = self.main_ui.month_combo.findText(prev_month)
            if idx >= 0:
                self.main_ui.month_combo.setCurrentIndex(idx)
            month = self.main_ui.month_combo.currentText()
            days = self.main_ui._available_dates.get(year, {}).get(month, [])
            self.main_ui.day_combo.clear()
            if days:
                self.main_ui.day_combo.addItems(days)
            idx = self.main_ui.day_combo.findText(prev_day)
            if idx >= 0:
                self.main_ui.day_combo.setCurrentIndex(idx)
        finally:
            if not month_blocked:
                self.main_ui.month_combo.blockSignals(False)
            if not day_blocked:
                self.main_ui.day_combo.blockSignals(False)
        self._populate_available_times()

    def _populate_available_times(self, files=None):
        prev_hour = self.main_ui.hour_combo.currentText()
        prev_min = self.main_ui.minute_combo.currentText()
        self.main_ui.hour_combo.blockSignals(True)
        self.main_ui.minute_combo.blockSignals(True)
        try:
            is_sataid = (hasattr(self.main_ui, 'type_combo') and
                         self.main_ui.type_combo.currentText() == "SATAID")
            if is_sataid:
                times = set()
                for f in (files or list_sataid_files(
                    f"{self.main_ui.year_combo.currentText()}{self.main_ui.month_combo.currentText()}{self.main_ui.day_combo.currentText()}"
                )):
                    ext = Path(f).suffix
                    tp = ext.lstrip(".Z")
                    if len(tp) == 4 and tp.isdigit():
                        times.add(tp)
                if times:
                    sorted_times = sorted(times)
                    self.main_ui._available_raw_times = sorted_times
                    hours = sorted(set(t[:2] for t in sorted_times))
                    mins = sorted(set(t[2:] for t in sorted_times))
                    self.main_ui.hour_combo.clear()
                    self.main_ui.hour_combo.addItems(hours)
                    self.main_ui.minute_combo.clear()
                    self.main_ui.minute_combo.addItems(mins)
                    self.main_ui._restore_combo(self.main_ui.hour_combo, prev_hour)
                    self.main_ui._restore_combo(self.main_ui.minute_combo, prev_min)
            else:
                sat = self.main_ui.sat_combo.currentData() or self.main_ui.sat_combo.currentText()
                is_goes = "goes" in sat.lower()
                is_gk2a = "gk2a" in sat.lower() or "gk-2a" in sat.lower()
                product_prefix = None
                if not is_goes and not is_gk2a and hasattr(self.main_ui, 'type_combo'):
                    product_prefix = self.get_current_product()
                date_prefix = f"{self.main_ui.year_combo.currentText()}_{self.main_ui.month_combo.currentText()}_{self.main_ui.day_combo.currentText()}"
                base_path = self._sat_data_dir(self.main_ui.input_dir, sat)
                times = set()
                rapid_slot_map = {}
                is_rapid = (hasattr(self.main_ui, 'type_combo') and
                            self.main_ui.type_combo.currentText().lower() in ("japan", "target"))
                if is_gk2a:
                    # GK-2A: read the exact scan time from each scene's gk2a_*.nc filename
                    # (trailing _YYYYMMDDHHMM.nc) wherever the downloader stored it, so any
                    # quick-scene/date-range/nested AMI product layout resolves correctly.
                    root = Path(self.main_ui.input_dir)
                    if root.exists():
                        for f in root.rglob("gk2a_*.nc"):
                            m_gk = re.search(r'_(\d{12})\.nc$', f.name)
                            if m_gk:
                                ts = m_gk.group(1)
                                if f"{ts[:4]}_{ts[4:6]}_{ts[6:8]}" == date_prefix:
                                    times.add(ts[8:10] + ts[10:12])
                elif base_path.exists():
                    for p in base_path.iterdir():
                        # Extract timestamp from name (works for both dirs and files)
                        name = p.name
                        if product_prefix and not name.startswith(product_prefix):
                            continue
                        # Himawari pattern 1: _YYYY_MM_DD_HHMM
                        m_h1 = re.search(r'_(\d{4})_(\d{2})_(\d{2})_(\d{4})', name)
                        if m_h1:
                            y, m, d, hm = m_h1.groups()
                            if f"{y}_{m}_{d}" == date_prefix:
                                if p.is_dir() and is_rapid:
                                    # Rapid-scan (Japan/Target): enumerate the nominal
                                    # folder slot AND any sub-minute observation times
                                    # (HSD scan filenames or ADS sidecars). Sub-minute
                                    # picks are mapped back to their containing folder
                                    # via main_ui._rapid_slot_map.
                                    times.add(hm)
                                    rapid_slot_map[hm] = p.name
                                    for f in p.glob("*.nc"):
                                        m_rapid = re.search(r'_(\d{4})(\d{2})(\d{2})_(\d{6})', f.name)
                                        if m_rapid:
                                            fy, fm, fd, fhm = m_rapid.groups()
                                            if f"{fy}_{fm}_{fd}" == date_prefix:
                                                times.add(fhm[:4])
                                                rapid_slot_map.setdefault(fhm[:4], p.name)
                                    for _af in p.glob("*.ads.json"):
                                        try:
                                            with open(_af, "r", encoding="utf-8") as _ah:
                                                _ads = json.load(_ah)
                                            _ost = str(_ads.get("observation_start_time", "")).strip()
                                            _mo = re.search(r'\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})', _ost)
                                            if _mo:
                                                _obshm = _mo.group(1) + _mo.group(2)
                                                times.add(_obshm)
                                                rapid_slot_map.setdefault(_obshm, p.name)
                                        except Exception:
                                            pass
                                else:
                                    times.add(hm)
                                continue
                        
                        # Himawari pattern 2: _YYYYMMDD_HHMM
                        m_h2 = re.search(r'_(\d{4})(\d{2})(\d{2})_(\d{4})', name)
                        if m_h2:
                            y, m, d, hm = m_h2.groups()
                            if f"{y}_{m}_{d}" == date_prefix:
                                times.add(hm)
                                continue

                        # GOES discovery (must be a directory for our current structure)
                        if p.is_dir():
                            segs = name.split("_")
                            if is_goes and len(segs) >= 4 \
                                 and segs[-3].isdigit() and len(segs[-3]) == 4 \
                                 and segs[-2].isdigit() and len(segs[-2]) == 3 \
                                 and segs[-1].isdigit() and len(segs[-1]) == 2:
                                y_str, doy_str, hh = segs[-3], segs[-2], segs[-1]
                                dt = datetime(int(y_str), 1, 1) + timedelta(days=int(doy_str) - 1)
                                m_str = f"{dt.month:02d}"
                                d_str = f"{dt.day:02d}"
                                if f"{y_str}_{m_str}_{d_str}" == date_prefix:
                                    # Scan files in this folder for all exact minutes
                                    files = list(p.glob("*.nc")) + list(p.glob("*.dat")) + list(p.glob("*.DAT"))
                                    for f in files:
                                        m_goes = re.search(r'_s\d{4}\d{3}\d{2}(\d{2})', f.name)
                                        if m_goes:
                                            times.add(f"{hh}{m_goes.group(1)}")

                        # Generic catch-all: extract HHMM from folder/file names
                        if not (m_h1 or m_h2):
                            m_gen = re.search(r'_(\d{2})(\d{2})_?$', name)
                            if not m_gen:
                                m_gen = re.search(r'_(\d{4})$', name)
                            if m_gen:
                                hm = m_gen.group(1)
                                if len(hm) == 4 and hm.isdigit():
                                    times.add(hm)

                self.main_ui._rapid_slot_map = {date_prefix: rapid_slot_map} if rapid_slot_map else getattr(
                    self.main_ui, '_rapid_slot_map', {})

                if times:
                    sorted_times = sorted(times)
                    self.main_ui._available_raw_times = sorted_times
                    hours = sorted(set(t[:2] for t in sorted_times))
                    mins = sorted(set(t[2:] for t in sorted_times))
                    self.main_ui.hour_combo.clear()
                    self.main_ui.hour_combo.addItems(hours)
                    self.main_ui.minute_combo.clear()
                    self.main_ui.minute_combo.addItems(mins)
                    self.main_ui._restore_combo(self.main_ui.hour_combo, prev_hour)
                    self.main_ui._restore_combo(self.main_ui.minute_combo, prev_min)
                else:
                    # No selectable times for this satellite/type — clear stale values so
                    # auto-load can't construct a bogus datetime (e.g. "___0045").
                    self.main_ui._available_raw_times = []
                    self.main_ui.hour_combo.clear()
                    self.main_ui.minute_combo.clear()
        finally:
            self.main_ui.hour_combo.blockSignals(False)
            self.main_ui.minute_combo.blockSignals(False)

    def _resolve_rapid_slot_folder(self, base_path, hm):
        """For Japan/Target rapid-scan sectors, map a 4-digit HHMM (nominal slot or
        sub-minute observation time such as 0002/0042) to its containing data folder.

        Uses the slot map built by _populate_available_times, then falls back to
        flooring the minute onto the 10-minute Himawari grid.
        """
        if not hm or len(hm) != 4 or not hm.isdigit():
            return None
        base_path = Path(base_path)
        date_prefix = (f"{self.main_ui.year_combo.currentText()}_{self.main_ui.month_combo.currentText()}"
                       f"_{self.main_ui.day_combo.currentText()}")
        slot_map = getattr(self.main_ui, '_rapid_slot_map', {}).get(date_prefix, {}) or {}
        folder_name = slot_map.get(hm)
        if folder_name:
            cand = base_path / folder_name
            if cand.is_dir():
                return cand
        floor_min = int(hm[2:]) // 10 * 10
        slot_hm = f"{hm[:2]}{floor_min:02d}"
        if slot_hm != hm:
            folder_name = slot_map.get(slot_hm)
            if folder_name:
                cand = base_path / folder_name
                if cand.is_dir():
                    return cand
        for cand in sorted(base_path.glob(f"*_{slot_hm}")):
            if cand.is_dir():
                return cand
        return None

    def _filter_minutes_for_hour(self):
        hour = self.main_ui.hour_combo.currentText()
        if not self.main_ui._available_raw_times or not hour:
            return
        prev_min = self.main_ui.minute_combo.currentText()
        mins = sorted(set(t[2:] for t in self.main_ui._available_raw_times if t[:2] == hour))
        self.main_ui.minute_combo.blockSignals(True)
        self.main_ui.minute_combo.clear()
        if mins:
            self.main_ui.minute_combo.addItems(mins)
            idx = self.main_ui.minute_combo.findText(prev_min)
            if idx >= 0:
                self.main_ui.minute_combo.setCurrentIndex(idx)
        self.main_ui.minute_combo.blockSignals(False)

    def _load_sataid_bands(self):
        year = self.main_ui.year_combo.currentText()
        month = self.main_ui.month_combo.currentText()
        day = self.main_ui.day_combo.currentText()
        date_str = f"{year}{month}{day}"
        self.log(f"SATAID: scanning files for date {date_str}")
        self.main_ui.current_datetime = f"{year}_{month}_{day}"

        files = list_sataid_files(date_str)
        self.main_ui.available_bands = []
        for f in files:
            info = parse_filename(str(f))
            if info["band"] and info["band"] not in self.main_ui.available_bands:
                self.main_ui.available_bands.append(info["band"])

        ext = getattr(self.main_ui, '_sataid_external', {}).get(date_str, {})
        for band in ext:
            if band not in self.main_ui.available_bands:
                self.main_ui.available_bands.append(band)

        if not self.main_ui.available_bands:
            self.log(f"No SATAID files found for {date_str}")
            self.main_ui.status_bar.showMessage("No SATAID files found")
            return

        self.log(f"SATAID bands found: {self.main_ui.available_bands}")
        self.main_ui.current_nc_path = None
        self.main_ui._sataid_current_data = None
        self.main_ui._sataid_current_lat = None
        self.main_ui._sataid_current_lon = None
        if hasattr(self.main_ui, '_sataid_overlay_pixmap_cache'):
            self.main_ui._sataid_overlay_pixmap_cache.clear()
        if hasattr(self.main_ui, '_sataid_coast_path_cache'):
            self.main_ui._sataid_coast_path_cache.clear()
        self.main_ui.cache.clear_raw()
        self.main_ui.cache.clear_rgb()
        self.main_ui.cache.clear_precached()
        self.main_ui._build_band_checkboxes(self.main_ui.available_bands)

        self.main_ui.status_bar.showMessage(f"SATAID: {len(self.main_ui.available_bands)} bands loaded")
        self.main_ui._update_sataid_tab_visibility()

    def _cache_sataid_bands(self):
        if getattr(self.main_ui, '_sataid_caching_in_progress', False):
            return
        bands = getattr(self.main_ui, 'available_bands', [])
        if not bands:
            return
        date_str = self.main_ui.current_datetime.replace('_', '')
        files = list_sataid_files(date_str)
        ext = getattr(self.main_ui, '_sataid_external', {}).get(date_str, {})
        for f in ext.values():
            if f not in files:
                files.append(f)
        self.main_ui._sataid_caching_in_progress = True
        self.main_ui.sataid_cache.cache_all(files, parent=self.main_ui, log_func=self.log,
                                     on_finished=lambda: self._on_sataid_cache_done())
        self.main_ui.status_bar.showMessage(
            f"SATAID cache: {self.main_ui.sataid_cache.count()} band(s) cached")

    def _on_sataid_cache_done(self):
        self.main_ui._sataid_caching_in_progress = False

    def _load_sataid_band_file(self, band: str):
        is_pwards = (hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo.currentData() == "pwards")
        if is_pwards:
            fpath = self.main_ui.pwards_stream.get_band_file(band)
            if fpath is None:
                self.log(f"PWARDS: no file for band {band}")
                return
            target = fpath
        else:
            year = self.main_ui.year_combo.currentText()
            month = self.main_ui.month_combo.currentText()
            day = self.main_ui.day_combo.currentText()
            date_str = f"{year}{month}{day}"

            files = list_sataid_files(date_str)
            target = None
            for f in files:
                info = parse_filename(str(f))
                if info["band"] == band:
                    target = f
                    break

            if target is None:
                ext = getattr(self.main_ui, '_sataid_external', {}).get(date_str, {})
                target = ext.get(band)

            if target is None:
                self.log(f"SATAID: no file found for band {band}")
                return

        self.log(f"SATAID: loading {target.name}")
        cached = self.main_ui.sataid_cache.get(target.name)
        if cached is not None:
            sat = cached
            self.log(f"SATAID cache: HIT for {target.name}")
        else:
            self.log(f"SATAID cache: MISS for {target.name}, decompressing...")
            try:
                sat = read_sataid_cached(target)
                self.main_ui.sataid_cache.put(target.name, sat)
            except Exception as e:
                self.log(f"SATAID: failed to load {target.name}: {e}")
                return

        data = sat.data
        self.main_ui._sataid_current_data = data
        self.main_ui._sataid_current_lat = sat.lat
        self.main_ui._sataid_current_lon = sat.lon
        self.main_ui._sataid_channel = sat.channel_name
        self.main_ui._sataid_units = sat.units

        if data is None or data.size == 0:
            self.log(f"SATAID: empty data in {target.name}")
            return

        brit = getattr(self.main_ui, '_sataid_brightness', 0.0)
        cntr = getattr(self.main_ui, '_sataid_contrast', 1.0)
        self.main_ui._render_sataid(data, brit, cntr)

        self.main_ui.current_original = target
        self.main_ui.current_base = f"SATAID_{band}"
        self.main_ui.status_bar.showMessage(f"SATAID: {band}")
        self.log(f"SATAID: displayed {band} ({data.shape[1]}x{data.shape[0]})")
        if self.main_ui.grid_enabled or self.main_ui.coast_enabled:
            self.main_ui.overlay_controller.update_overlays()

    def _load_pwards_stream(self):
        if not getattr(self.main_ui, 'pwards_stream', None):
            return
        cfg = self.settings.get("pwards_api", {})
        if not cfg.get("enabled") or not cfg.get("base_url") or not cfg.get("api_code"):
            self.main_ui.status_bar.showMessage("PWARDS API not configured â€” go to System > PWARDS API")
            return
        self.main_ui.pwards_stream.configure(
            base_url=cfg["base_url"],
            api_code=cfg["api_code"],
            poll_interval_sec=cfg.get("poll_interval_sec", 60),
        )
        self.main_ui.pwards_stream.start()

    def _on_pwards_config_changed(self):
        is_pwards = (hasattr(self.main_ui, 'sat_combo') and
                     self.main_ui.sat_combo.currentData() == "pwards")
        if is_pwards and getattr(self.main_ui, 'pwards_stream', None):
            self.main_ui.pwards_stream.stop()
            self._load_pwards_stream()

    def _on_pwards_bands_ready(self, band_files):
        self.main_ui.available_bands = sorted(band_files.keys(), key=_PWARDS_BAND_SORT_KEY)
        self.main_ui.current_nc_path = None
        self.main_ui._sataid_current_data = None
        self.main_ui._sataid_current_lat = None
        self.main_ui._sataid_current_lon = None
        self.main_ui.cache.clear_raw()
        self.main_ui.cache.clear_rgb()
        self.main_ui.cache.clear_precached()
        self.main_ui._build_band_checkboxes(self.main_ui.available_bands)
        slot = self.main_ui.pwards_stream.get_active_slot_info() or {}
        date_str = slot.get("date", "??????")
        time_str = slot.get("time", "????")
        self.main_ui.current_datetime = f"{date_str}_{time_str}"
        self.main_ui.status_bar.showMessage(
            f"PWARDS Stream: {len(self.main_ui.available_bands)} bands ({date_str} {time_str})"
        )

    def auto_load_bands(self):
        is_sataid = (hasattr(self.main_ui, 'type_combo') and
                     self.main_ui.type_combo.currentText() == "SATAID")
        if is_sataid:
            self._load_sataid_bands()
            return
        is_pwards_stream = (hasattr(self.main_ui, 'sat_combo') and self.main_ui.sat_combo.currentData() == "pwards" and
                           hasattr(self.main_ui, 'type_combo') and self.main_ui.type_combo.currentText() == "Stream")
        if is_pwards_stream:
            self._load_pwards_stream()
            return
        if not hasattr(self, '_load_timer'):
            self._load_timer = QTimer(self)
            self._load_timer.setSingleShot(True)
            self._load_timer.timeout.connect(self.load_date_metadata)
        self._load_timer.start(500)

    def update_satellite_lon(self):
        sat = self.main_ui.sat_combo.currentText()
        sat_id = (self.main_ui.sat_combo.currentData() or sat).lower()
        self.log(f"Satellite changed to {sat} (id={sat_id})")
        if "himawari" in sat.lower() or "pwards" in sat_id:
            self.main_ui.satellite_lon = 140.7
        elif "gk2a" in sat_id or "gk-2a" in sat_id:
            self.main_ui.satellite_lon = 128.2
        elif "meteosat" in sat_id or "msg" in sat_id:
            self.main_ui.satellite_lon = 0.0
        else:
            import re as _re
            m = _re.search(r'(\d+)', sat)
            num = int(m.group(1)) if m else 0
            lon_map = {16: -75.2, 17: -137.2, 18: -137.2, 19: -75.2}
            self.main_ui.satellite_lon = lon_map.get(num, 140.7)
        self.main_ui._last_overlay_key = None
        self.main_ui._last_grid_cache_key = None
        self.main_ui._last_coast_cache_key = None
        # Switching satellites changes the imagery's projection geometry —
        # invalidate the cached overlay geometry so the next refresh fully
        # re-projects grids, coastlines, AoRs and tracks (they currently keep
        # the previous satellite's placement).
        self.main_ui._ol_geo_sig = None
        if hasattr(self.main_ui, '_grid_path_cache'):
            self.main_ui._grid_path_cache.clear()
        if hasattr(self.main_ui, '_coast_path_cache'):
            self.main_ui._coast_path_cache.clear()
        if hasattr(self.main_ui, '_grid_image_cache'):
            self.main_ui._grid_image_cache.clear()
        if hasattr(self.main_ui, '_coast_image_cache'):
            self.main_ui._coast_image_cache.clear()
        if hasattr(self.main_ui, '_projected_coast_cache'):
            self.main_ui._projected_coast_cache.clear()
        if hasattr(self.main_ui, '_projected_grid_cache'):
            self.main_ui._projected_grid_cache.clear()
        # update product combo for satellite
        if hasattr(self.main_ui, 'devkit_preset_combo') and self.main_ui.devkit_preset_combo:
            prods = list(get_products(sat).keys())
            self.main_ui.devkit_preset_combo.blockSignals(True)
            self.main_ui.devkit_preset_combo.clear()
            self.main_ui.devkit_preset_combo.addItem("-- select RGB product --")
            self.main_ui.devkit_preset_combo.addItems(prods)
            self.main_ui.devkit_preset_combo.blockSignals(False)
        if hasattr(self.main_ui, '_anim_band_checkboxes'):
            try:
                self.main_ui._build_anim_content_grid()
            except Exception:
                pass
        self.refresh_product_tile_availability()

    def _update_type_options(self):

        sat = self.main_ui.sat_combo.currentText().lower()
        self.log(f"Updating type options for satellite {sat}")
        current = self.main_ui.type_combo.currentData() if hasattr(self.main_ui, 'type_combo') else "FLDK"

        self.main_ui.type_combo.blockSignals(True)
        self.main_ui.type_combo.clear()

        if "himawari" in sat:
            self.main_ui.type_combo.addItem("Full Disk", "FLDK")
            self.main_ui.type_combo.addItem("Japan", "Japan")
            self.main_ui.type_combo.addItem("Target", "Target")
            self.main_ui.type_combo.addItem("SATAID", "SATAID")
            if current in ["FLDK", "Japan", "Target", "SATAID"]:
                idx = self.main_ui.type_combo.findData(current)
                if idx >= 0:
                    self.main_ui.type_combo.setCurrentIndex(idx)
            else:
                idx = self.main_ui.type_combo.findData("FLDK")
                if idx >= 0:
                    self.main_ui.type_combo.setCurrentIndex(idx)
        elif "gk2a" in sat or "gk-2a" in sat:
            self.main_ui.type_combo.addItem("Full Disk", "Full")
            idx = self.main_ui.type_combo.findData("Full")
            if idx >= 0:
                self.main_ui.type_combo.setCurrentIndex(idx)
        elif "meteosat" in sat or "msg" in sat:
            self.main_ui.type_combo.addItem("Full Disk", "Full")
            idx = self.main_ui.type_combo.findData("Full")
            if idx >= 0:
                self.main_ui.type_combo.setCurrentIndex(idx)
        elif "pwards" in sat:
            self.main_ui.type_combo.addItem("Stream", "Stream")
            idx = self.main_ui.type_combo.findData("Stream")
            if idx >= 0:
                self.main_ui.type_combo.setCurrentIndex(idx)
        else:
            self.main_ui.type_combo.addItem("Full Disk", "Full")
            self.main_ui.type_combo.addItem("CONUS", "CONUS")
            self.main_ui.type_combo.addItem("Meso", "Meso")
            idx = self.main_ui.type_combo.findData("Full")
            if idx >= 0:
                self.main_ui.type_combo.setCurrentIndex(idx)

        self.main_ui.type_combo.blockSignals(False)

    def get_current_product(self) -> str:
        sat = self.main_ui.sat_combo.currentText().lower()
        sat_id = (self.main_ui.sat_combo.currentData() or sat).lower()
        typ = self.main_ui.type_combo.currentData() or self.main_ui.type_combo.currentText()
        if "himawari" in sat:
            mapping = {
                "FLDK": "AHI-L1b-FLDK",
                "Japan": "AHI-L1b-Japan",
                "Target": "AHI-L1b-Target"
            }
            return mapping.get(typ, "AHI-L1b-FLDK")
        elif "gk2a" in sat_id or "gk-2a" in sat_id:
            return "GK2A-L1B-FD"
        elif "meteosat" in sat_id or "msg" in sat_id:
            return "MSG-L1B-FD"
        return "ABI-L1b-RadF"
