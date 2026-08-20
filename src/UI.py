# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: UI.py
# Description: Main application window implementing the primary user interface with controller delegation for all business logic operations.
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

_crash_log = Path(__file__).resolve().parent.parent / "logs" / "crash.log"

# Ensure project root is on sys.path so from src.* imports resolve
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# Patch PySide6.QtCore.QThread into a SafeQThread BEFORE any module imports
# QThread, so no QThread can ever be destroyed while its thread is running.
import src.services.thread_guard  # noqa: E402,F401

def _log_crash(exc_type, exc_value, exc_tb):
    with open(str(_crash_log), "a") as f:
        f.write(f"[{type(exc_type).__name__}] {exc_type}: {exc_value}\n")
        traceback.print_tb(exc_tb, file=f)
        f.write("\n")
    print(f"[FATAL] {exc_type.__name__ if hasattr(exc_type, '__name__') else exc_type}: {exc_value}", file=sys.stderr)

sys.excepthook = lambda t,v,b: _log_crash(t,v,b)
threading.excepthook = lambda args: _log_crash(args.exc_type, args.exc_value, args.exc_traceback)

log = logging.getLogger(__name__)

import warnings
warnings.filterwarnings("ignore", category=Warning, module="requests")
warnings.filterwarnings("ignore", category=UserWarning, module="cupy")
warnings.filterwarnings("ignore", message=r".*doesn't match a supported version.*")
warnings.filterwarnings("ignore", message=r".*CUDA path could not be detected.*")
warnings.filterwarnings("ignore", category=RuntimeWarning, message=r"Failed to disconnect")
warnings.filterwarnings("ignore", category=DeprecationWarning, message=r".*QMouseEvent\.pos.*")

import re
import requests
import zipfile
import xml.etree.ElementTree as ET
import tempfile
from PIL import Image, ImageDraw, ImageFont
import numpy as np
import xarray as xr
import rasterio
from rasterio.transform import Affine
from pyproj import Transformer, CRS
import shapefile

from src.clients import nhc, jma, agency_tracker, weathergov, cwa as cwa_client

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl, QMimeData, QObject, Signal, QThread, QSize, QTimer, QProcess, QDate, QDateTime, QEvent, QEventLoop, QFileSystemWatcher
from PySide6.QtGui import (
    QPixmap, QIcon, QDrag, QMouseEvent,
    QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent,
    QWheelEvent, QImage, QPainter, QFont, QPen, QColor, QPainterPath,
    QImageReader, QPalette, QAction, QActionGroup, QCursor,
    QBrush, QKeySequence, QShortcut, QPolygonF, QFontMetrics,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from src.services import glsl_proj
from src.ui.GLMapWidget import GLMapWidget
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QGroupBox, QRadioButton,
    QPushButton, QTabWidget, QStatusBar, QHBoxLayout, QLabel, QSplitter,
    QListWidget, QListWidgetItem, QTextEdit, QSlider, QFrame, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QSizePolicy, QGraphicsView, QGraphicsScene, QMenu,
    QProgressDialog, QMessageBox, QCheckBox, QSpinBox, QComboBox,
    QProgressBar, QDateEdit, QScrollArea, QButtonGroup, QDateTimeEdit,
    QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsPathItem, QGraphicsLineItem,
    QGraphicsEllipseItem, QGraphicsPolygonItem, QGraphicsRectItem, QGraphicsSimpleTextItem,
    QGraphicsProxyWidget, QGraphicsItem, QGridLayout,
    QDialog, QDialogButtonBox, QStackedWidget, QDoubleSpinBox, QLineEdit,
    QFormLayout, QToolButton, QSystemTrayIcon, QStyle
)

from scipy.ndimage import zoom
import math

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

def _default_cache_mb():
    if HAS_PSUTIL:
        try:
            mem = psutil.virtual_memory()
            total_gb = mem.total / (1024**3)
            return max(2048, min(8192, int(total_gb * 0.5 * 1024)))
        except Exception:
            pass
    return 2048

try:
    import imageio.v2 as iio
    HAS_IMAGEIO = True
except ImportError:
    HAS_IMAGEIO = False

from src.exporters.animation_exporter import export_animation_direct, add_footer_to_frame

try:
    import cupy as cp
    cp.array([1.0])
    if cp.cuda.runtime.getDeviceCount() > 0:
        HAS_CUPY = True
    else:
        HAS_CUPY = False
        cp = None
except Exception:
    cp = None
    HAS_CUPY = False

try:
    from numba import njit, prange
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

xp = cp if HAS_CUPY else np

from concurrent.futures import ThreadPoolExecutor

from src.services.eq_cache import eq_cache
from src.services.fc_cache import fc_cache
from src.services.fd_cache import fd_cache

Image.MAX_IMAGE_PIXELS = None

_BAND_NICKNAMES = {
    "01": "Blue (VIS)", "02": "Red (VIS)", "03": "Veggie (NIR)",
    "04": "Cirrus (NIR)", "05": "Snow/Ice (NIR)", "06": "Cloud Particle (NIR)",
    "07": "Shortwave IR (IR)", "08": "Upper Water Vapor (WV)",
    "09": "Mid Water Vapor (WV)", "10": "Lower Water Vapor (WV)",
    "11": "Cloud Phase (IR)", "12": "Ozone (IR)", "13": "Clean IR (IR)",
    "14": "Longwave IR (IR)", "15": "Dirty IR (IR)", "16": "CO2 Longwave (IR)",
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
HAS_GEO = True

if getattr(sys, 'frozen', False):
    top_dir = Path(sys.executable).resolve().parent
    src_dir = top_dir
else:
    top_dir = Path(__file__).resolve().parent.parent
    src_dir = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------

# Imports from refactored modules
# ---------------------------------------------------------------------------
from src.core.engine_dispatcher import get_engine, get_products, get_tag_colors
from src.core.Engine import set_compute_backend, _GPU_FORCE_DISABLE
from src.core.helpers import (
    normalize_lon, _point_in_polygon, _densify_polygon, _get_nc_glob_pattern,
    _load_cache_manager, CacheManager,
    COASTLINE_URLS, COASTLINE_DIR, COASTLINE_SHP,
    ensure_coastline_data,
    THEMES, build_stylesheet,
    _get_tracks_folder, _save_tracks_to_disk, _load_tracks_from_disk,
)
from src.workers.core import (
    cache_worker_factory, ProcessDatWorker, CompositeWorker,
    PrecacheWorker, ScenePreparationWorker, RawBandCacheWorker,
    AnimationPrefetchWorker, OverlayPrecacheWorker, ExportWorker,
)
from src.managers.goes_cache import GOESCacheManager, is_goes_folder
from src.ui.components import (
    FileListWidget, ColorButton, ViewportInfoBox, SataidControlPanel,
    ClickablePixmapItem, ClickablePointItem,
)
from src.ui.ReconWindow import ReconWindow
from src.ui.Centerviewport import ZoomableGraphicsView, ViewportFrame
from src.ui.settings import SettingsManager, SettingsWindow
from src.ui.account_window import AccountWindow
from src.ui.downloader_selector import DownloaderSelector
from src.ui.dialogs import (
    DraggablePointsListWidget, MeteorologicalTrackDialog,
    ForecastDialog, ThemeDialog, CombinedForecastDialog,
    CachingDialog, AnimationExportDialog,
)
from src.managers.cache_manager import RuntimeCacheManager
from src.workers.AMVWorker import AMVWorker
from src.parsers.sataid_reader import (
    is_sataid_file, list_sataid_files, parse_filename,
    read_sataid_cached,
)
from src.managers.sataid_cache_manager import SataidCacheManager
from src.ui.MultiViewportWindow import MultiViewportManager
from src.ui.MultiPanelWindow import MultiPanelManager, MainSplitHost
from src.ui.TearOffTabBar import TabTearOffFilter, TabFloatingWindow
from src.managers.alert_manager import (AlertManager, play_notification, play_emergency,
                           is_expired)
from src.workers.alert_map_worker import AlertMapWorker
from src.clients import weathergov

# Controller and service imports
from src.controllers.satellite_controller import SatelliteController
from src.controllers.overlay_controller import OverlayController
from src.controllers.forecast_controller import ForecastController
from src.controllers.animation_controller import AnimationController
from src.controllers.alert_controller import AlertController
from src.controllers.export_controller import ExportController
from src.controllers.climate_controller import ClimateController
from src.controllers.ascat_controller import AscatController
from src.controllers.microwave_controller import MicrowaveController
from src.services.projection import ProjectionService
from src.services.contour import ContourService

class MainUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = SettingsManager(src_dir)
        self._theme = THEMES.get("Dark (Default)")

        set_compute_backend(self.settings.get("gpu_acceleration", False))

        self.setWindowTitle("Monwatch - Cyclone V3.0.5.1 | Satellite Renderer")
        _icon_path = top_dir / 'public' / 'images' / 'Monwatch-LOGO.png'
        if _icon_path.exists():
            _app_icon = QIcon(str(_icon_path))
            self.setWindowIcon(_app_icon)
        self.resize(1520, 930)
        QApplication.setFont(QFont("Segoe UI", 9))

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        self.map_progress = QProgressBar()
        self.map_progress.setFixedWidth(180)
        self.map_progress.setFixedHeight(16)
        self.map_progress.setTextVisible(False)
        self.map_progress.setRange(0, 0)
        self.map_progress.hide()
        self.status_bar.addPermanentWidget(self.map_progress)

        self.flatgen_progress = QProgressBar()
        self.flatgen_progress.setFixedWidth(180)
        self.flatgen_progress.setFixedHeight(16)
        self.flatgen_progress.setTextVisible(True)
        self.flatgen_progress.setFormat("Flat Projection: %p%")
        self.flatgen_progress.setRange(0, 100)
        self.flatgen_progress.setValue(0)
        self.flatgen_progress.hide()
        self.status_bar.addPermanentWidget(self.flatgen_progress)

        self.broadcast_progress = QProgressBar()
        self.broadcast_progress.setFixedWidth(180)
        self.broadcast_progress.setFixedHeight(16)
        self.broadcast_progress.setTextVisible(True)
        self.broadcast_progress.setFormat("Starting Broadcast...")
        self.broadcast_progress.setRange(0, 0)
        self.broadcast_progress.hide()
        self.status_bar.addPermanentWidget(self.broadcast_progress)

        self._prevent_duplicate_events = False
        self._suppress_display_until_cache_done = False

        default_dl = str(top_dir / 'data' / 'Download')
        paths = self.settings.get("paths", {})
        self.input_dir = paths.get("download_folder", "").strip() or default_dl
        self._init_download_folder_watcher()
        self.cache_dir = str(top_dir / 'cache' / 'images')
        os.makedirs(self.cache_dir, exist_ok=True)
        self._overlay_cache_dir = top_dir / 'cache' / 'overlays'
        self._overlay_cache_dir.mkdir(parents=True, exist_ok=True)

        images_dir = top_dir / 'public' / 'images'
        self.latest_path = images_dir / 'latest.png'

        self.current_base = None
        self.current_original = None
        self.current_zoom = 1.0
        self.is_closing = False
        self._ref_grid_size = None
        self._ref_grid_1km = None
        self._ref_grid_0_5km = None
        self.active_thread = None
        self.active_worker = None
        self.cache = RuntimeCacheManager(self.settings, self.log)
        eq_cache.log = self.log
        fc_cache.log = self.log
        fd_cache.log = self.log
        self._contour_overlay_items = []
        self.preview_max_px = 2200
        self.preview_res_m = 2000
        self.preview_quality = "2km res"
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self.cache_size_mb = _default_cache_mb()
        self.gpu_acceleration = False
        raw_threads = self.settings.get("max_threads", 4)
        if raw_threads == 0:
            raw_threads = os.cpu_count() or 4
        self.max_threads = raw_threads
        self.render_quality = "2km res"
        self._thread_pool = ThreadPoolExecutor(max_workers=self.max_threads)

        self.process_dat_thread = None
        self.process_dat_worker = None

        self.current_satellite = "himawari9"
        self._temp_satellite = None
        self._removing_temp_sat = False
        _pwards_cfg = self.settings.get("pwards_api", {})
        self._pwards_configured = bool(_pwards_cfg.get("enabled") and _pwards_cfg.get("base_url") and _pwards_cfg.get("api_code"))
        self._perm_sat_ids = ["himawari8", "himawari9", "goes16", "goes17", "goes18", "goes19", "gk2a"]
        self._perm_sat_labels = ["Himawari 8", "Himawari 9", "GOES 16", "GOES 17", "GOES 18", "GOES 19", "GK-2A"]
        if self._pwards_configured:
            self._perm_sat_ids.append("pwards")
            self._perm_sat_labels.append("PWARDS")
        self.current_datetime = None
        self.available_bands = []
        self.band_checkboxes = {}
        self.selected_product = None
        self.bands_grid_layout = None
        self.products_grid_layout = None
        self.overlays_grid_layout = None

        self.sataid_cache = SataidCacheManager()
        self.current_geotransform = None
        self.current_crs = None
        self._overlay_geo_unavailable = False
        self._goes_geotransform_2km = None
        self.grid_overlay_items = []
        self.coast_overlay_items = []
        self.aor_overlay_items = []
        self._overlay_item = None
        self._grid_item = None
        self._coast_item = None
        self._coast_pix_item = None
        self._coast_swap_item = None
        self._nhc_overlay_items = []
        self.track_overlay_items = []

        # Visualizer display projection tracking
        self._current_display_projection = "full_disk"  # "full_disk", "mercator", "plate_carree"
        self._display_projection_extent = None  # (min_x, min_y, max_x, max_y) in display CRS
        self._display_projection_crs = None  # CRS for current display mode
        self._full_disk_crs = None
        self._full_disk_geotransform = None

        self._projected_coast_cache = {}
        self._projected_grid_cache = {}
        self._coast_raw_segments = None
        self._last_overlay_key = None
        self._last_grid_cache_key = None
        self._last_coast_cache_key = None
        self._grid_path_cache = {}
        self._coast_path_cache = {}
        self._current_band_name = ''
        self._resized_display_cache = {}
        self._is_hsd_source = False

        self._available_dates = {}
        self._available_raw_times = []
        self._overlay_transformer = None
        self._overlay_crs = None
        self._ads_provenance = None

        self.tracks = _load_tracks_from_disk()
        for _t in self.tracks:
            _t["visible"] = False
        self._editing_track_id = None
        self.nhc_storms = {}
        self.nhc_cone_overlay_items = []
        self.nhc_enabled = True
        self.jma_storms = {}
        self._jma_overlay_items = []
        self.jtwc_storms = {}
        self._jtwc_overlay_items = []
        self.pagasa_storms = {}
        self._pagasa_overlay_items = []
        self.cwa_storms = {}
        self._cwa_overlay_items = []
        # ATCF storms from KnackWX API
        self.atcf_storms = []
        self._atcf_overlay_items = []
        self._atcf_visible_storms = set()
        self._atcf_busy = False
        # Live weather/recon aircraft (NOAA Hurricane Hunters, USAF WC-130J)
        self.recon_aircraft = []
        self._recon_overlay_items = []
        self._recon_flights_busy = False
        self._recon_tracks = {}
        self._recon_last_fetch_time = None
        self._recon_source = None
        self._recon_scan_timer = QTimer(self)
        self._recon_scan_timer.setInterval(60000)
        self.recon_window = None
        # NHC/JTWC ATCF sources temporarily disabled - using KnackWX API for technical reasons
        # (legacy ATCFClient code preserved in src/clients/atcf.py for future re-enablement)

        # Controller and service instantiation
        self.projection_service = ProjectionService(self)
        self.projection = self.projection_service
        self.contour_service = ContourService(self)
        self.contour = self.contour_service
        self.satellite_controller = SatelliteController(self)
        self.overlay_controller = OverlayController(self)
        # --border-shapefile <path> overrides the borders layer source
        # (mirrors SIFT's main.py): coastline = edges of the given shapefile.
        try:
            if "--border-shapefile" in sys.argv:
                _bi = sys.argv.index("--border-shapefile")
                if _bi + 1 < len(sys.argv) and Path(sys.argv[_bi + 1]).exists():
                    self.overlay_controller._coastline_shp_path = Path(sys.argv[_bi + 1])
                    self.log(f"Borders shapefile override: {sys.argv[_bi + 1]}")
        except Exception:
            pass
        self.forecast_controller = ForecastController(self)
        self.animation_controller = AnimationController(self)
        self.alert_controller = AlertController(self)
        self.export_controller = ExportController(self)
        self.climate_controller = ClimateController(self)
        self.ascat_controller = AscatController(self)
        self.microwave_controller = MicrowaveController(self)

        self.forecast_controller._load_nhc_from_disk()
        self.forecast_controller._load_jma_from_disk()
        self.forecast_controller._load_pagasa_from_disk()
        self.forecast_controller._load_jtwc_from_disk()
        self.forecast_controller._load_cwa_from_disk()

        self.pwards_stream = None
        if self._pwards_configured:
            from src.managers.pwards_stream_manager import PwardsStreamManager
            self.pwards_stream = PwardsStreamManager(self, self.settings)
            self.pwards_stream.bands_ready.connect(self._on_pwards_bands_ready)
            self.pwards_stream.status_message.connect(lambda msg: self.log(f"[PWARDS] {msg}"))
            self.pwards_stream.poll_error.connect(lambda msg: self.log(f"[PWARDS] poll error: {msg}"))

        self._forecast_processes = {}
        self._forecast_progress_bar = None
        self._forecast_counter = 0

        self._climate_gen_procs = {}
        self._climate_gen_progress_bar = None

        self._export_progress_bar = None

        self._wind_items = []
        self._wind_proj_cache = {}

        self._last_mouse_scene_pos = None
        self.static_coastlines = False
        self.earth_radius_pixels = None
        self.satellite_lon = 140.7

        self.band_info_panel = None
        self.secondary_status_label = QLabel("Lat: --, Lon: --  |  Temp: -- K")
        self.secondary_status_label.setStyleSheet("color: #AAA; font-size: 9px; padding-right: 6px;")
        self.secondary_status_label.setVisible(False)

        self.current_mode = self.settings.get("mode", "casual")

        if HAS_GEO:
            ensure_coastline_data()

        container = QWidget()
        self.setCentralWidget(container)
        self.main_layout = QHBoxLayout(container)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(2)
        self.main_layout.addWidget(self.splitter)

        self.left_panel = self._init_left_panel_compact()
        self._mp_manager = None
        self._init_center_panel()
        self.alert_controller._init_alert_system()
        self.right_panel = self._init_right_panel()

        self.left_panel_auto_hide = (self.settings.get("left_panel_mode", "permanent") == "autohide")
        self._fullscreen_left_visible = True
        self._left_reveal_button = None

        self._create_menu_bar()
        self.status_bar.addPermanentWidget(self.secondary_status_label)

        QTimer.singleShot(0, self._run_latest_symlink)
        self.load_preview_images()

        self.splitter.setSizes([64, 800, 400])
        self.reset_view()
        self._broadcast_process = None
        self._broadcast_forecast_timer = QTimer(self)
        self._broadcast_forecast_timer.setInterval(300000)
        self._broadcast_forecast_timer.timeout.connect(self._export_broadcast_forecast)
        self._apply_mode(self.current_mode)
        self.forecast_controller._restore_active_tab()
        self._apply_custom_styles()

        self.composite_thread = None
        self.composite_worker = None
        self.generating_product = False

        self.cache_progress_bar = None

        self._saved_render_hints = None
        self.raw_cache_thread = None
        self.raw_cache_worker = None

        self.raw_cache_thread2 = None
        self.raw_cache_worker2 = None
        self._raw_cache_threads_running = 0
        self._raw_bands_processed = set()



        low_vram = self.apply_gpu_acceleration()
        self.apply_render_quality()
        self.apply_zoom_interpolation()
        self.apply_texture_cache_size()
        if low_vram:
            if self.preview_quality == "Full Res":
                self.preview_quality = "0.5km res"
                self.preview_max_px = 22000
                self.preview_res_m = 500
                self.log("Low VRAM: Full Res downgraded to 0.5km res")
            elif self.preview_quality != "gridded res" and self.preview_max_px > 2048:
                self.preview_max_px = 2048
        self.cache.enforce_size_limit()
        self.apply_adaptive_settings()

        if HAS_CUPY:
            try:
                dev = cp.cuda.Device(0)
                dev_name = cp.cuda.runtime.getDeviceProperties(dev.id).get("name", b"GPU")
                if isinstance(dev_name, bytes):
                    dev_name = dev_name.decode(errors="replace").rstrip("\x00")
                gpu_text = f"GPU: {dev_name}"
            except Exception:
                gpu_text = "GPU Compute"
            gpu_lbl = QLabel(gpu_text)
            gpu_lbl.setStyleSheet("color: #4CAF50; font-size: 8px; padding-right: 8px; font-weight: bold;")
        else:
            gpu_lbl = QLabel("CPU mode")
            gpu_lbl.setStyleSheet("color: #666; font-size: 8px; padding-right: 8px;")
        self.status_bar.addPermanentWidget(gpu_lbl)

        self.setMouseTracking(True)
        self.graphics_view.setMouseTracking(True)
        self._init_mouse_throttle()

        self._ir_kelvin = None
        self.current_winds_uv = None
        self.winds_enabled = False

        self.microwave_tb_data = None
        self.microwave_enabled = False
        self._mw_items = []
        self._mw_persist = False

        self._anim_frames = []
        self._anim_timestamps = []
        self._anim_nc_paths = []
        self._anim_restored_count = 0
        self._anim_content_signature = None
        self._anim_base_signature = None
        self._anim_playing = False
        self._anim_index = 0
        self._anim_crs_list = []
        self._anim_geotransform_list = []
        self._anim_band_frames = []
        self._anim_target_band_frames = []
        self._anim_sync_product_idx = None
        self._anim_sync_target_meta = None
        self._anim_sync_pending_idx = None
        self._mv_manager = None
        self._anim_prefetch_worker = None
        self._anim_prefetch_thread = None
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self.animation_controller._advance_animation)
        self._anim_timer.setSingleShot(False)

        self._product_watchdog = QTimer(self)
        self._product_watchdog.timeout.connect(self._check_product_watchdog)
        self._product_watchdog.start(2000)
        self._product_disabled_at = 0.0
        QTimer.singleShot(3000, self._sync_nhc_storms_on_startup)
        QTimer.singleShot(8000, self._sync_jma_on_startup)
        QTimer.singleShot(13000, self._sync_jtwc_on_startup)
        QTimer.singleShot(18000, self._sync_pagasa_on_startup)
        QTimer.singleShot(23000, self._sync_cwa_on_startup)
        QTimer.singleShot(28000, self._sync_atcf_on_startup)

        # ATCF auto-refresh timer — runs at startup and every 15 minutes.
        # Aligned to the clock minute marks :02, :17, :32, :47 of each hour.
        self._atcf_refresh_minutes = (2, 17, 32, 47)
        self._atcf_refresh_timer = QTimer(self)
        self._atcf_refresh_timer.setSingleShot(True)
        self._atcf_refresh_timer.timeout.connect(self._on_atcf_refresh_fire)
        self._schedule_atcf_refresh()

        # Populate the ASCAT pass dropdown from any swath files already on disk.
        QTimer.singleShot(1500, self._load_local_ascat_passes)

    def closeEvent(self, event):
        try:
            self.is_closing = True
        except Exception:
            pass

        try:
            if self.recon_window is not None:
                self.recon_window.close()
                self.recon_window = None
        except Exception:
            pass

        # Cancel all known cancellable workers so their threads can exit early.
        for worker in (getattr(self, 'precache_worker', None),
                       getattr(self, '_atcf_worker', None)):
            if worker is not None and hasattr(worker, 'cancel'):
                try:
                    worker.cancel()
                except Exception:
                    pass

        for worker in (getattr(self, '_active_cache_workers', None) or []):
            if worker is not None and hasattr(worker, 'cancel'):
                try:
                    worker.cancel()
                except Exception:
                    pass

        ctx = getattr(self, '_export_ctx', None)
        if ctx and ctx.get('worker') is not None:
            try:
                ctx['worker'].cancel()
            except Exception:
                pass

        try:
            if hasattr(self, 'pwards_stream') and self.pwards_stream:
                self.pwards_stream.stop()
        except Exception:
            pass
        try:
            if hasattr(self, 'alert_controller'):
                self.alert_controller._save_last_alerts(sync=True)
        except Exception:
            pass
        try:
            self.settings.save_immediate()
        except Exception:
            pass
        try:
            self._thread_pool.shutdown(wait=False)
        except Exception:
            pass

        # Wait for every live QThread to finish (requestInterruption -> wait ->
        # terminate fallback) so none is destroyed while still running.
        try:
            from src.services import thread_guard
            thread_guard.shutdown_threads()
        except Exception:
            pass

        for mgr in (getattr(self, '_mp_manager', None),
                    getattr(self, '_mv_manager', None)):
            if mgr is not None and hasattr(mgr, 'destroy'):
                try:
                    mgr.destroy()
                except Exception:
                    pass

        super().closeEvent(event)

    def _std_icon(self, standard_pixmap: QStyle.StandardPixmap, size: int = 16, color: str = "#CCCCCC") -> QIcon:
        pm = self.style().standardIcon(standard_pixmap).pixmap(size, size)
        out = QPixmap(pm.size())
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.drawPixmap(0, 0, pm)
        painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        painter.fillRect(out.rect(), QColor(color))
        painter.end()
        return QIcon(out)

    def _init_left_panel_compact(self):

        panel = QFrame()
        panel.setFixedWidth(64)
        t = self._theme
        panel.setStyleSheet(f"""
            QFrame {{
                background: {t['bg2']};
                border-right: 1px solid {t['border3']};
            }}
        """)
        self._left_panel_frame = panel
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 8)
        layout.setSpacing(8)

        splash_path = top_dir / 'public' / 'images' / 'Monwatch-LOGO.png'
        if splash_path.exists():
            try:
                splash_lbl = QLabel()
                splash_pix = QPixmap(str(splash_path)).scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                splash_lbl.setPixmap(splash_pix)
                splash_lbl.setAlignment(Qt.AlignCenter)
                splash_lbl.setStyleSheet("margin: 2px 0 6px 0;")
                splash_lbl.setFixedWidth(56)
                layout.addWidget(splash_lbl)
            except Exception:
                pass

        self._action_buttons = []
        self._symbol_labels = []
        buttons = [
            ("QG", "G", self._quick_generate, "#5D8AA8"),
            ("Generate", "G", self._export_current_image, "#4CAF50"),
            ("Download", "D", self.run_process_dat, "#9C6FD6"),
            ("Refresh", "R", self.refresh_image, "#5D8AA8"),
            ("ASCAT", "S", self.open_ascat_window, "#00BCD4"),
            ("MW", "M", self.open_microwave_window, "#FF7043")
        ]
        t = self._theme
        for label, symbol, callback, color in buttons:
            btn = QPushButton()
            btn.setFixedSize(56, 56)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {t['bg3']};
                    border: none;
                    border-left: 3px solid {color};
                    border-radius: 4px;
                    text-align: center;
                }}
                QPushButton:hover {{
                    background: {t['menusel']};
                }}
                QPushButton:pressed {{
                    background: {t['bg2']};
                }}
            """)
            vlayout = QVBoxLayout(btn)
            vlayout.setContentsMargins(2, 8, 2, 8)
            vlayout.setSpacing(4)
            symbol_lbl = QLabel(symbol)
            symbol_lbl.setAlignment(Qt.AlignCenter)
            symbol_lbl.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: bold; background: transparent;")
            text_lbl = QLabel(label)
            text_lbl.setAlignment(Qt.AlignCenter)
            text_lbl.setStyleSheet("font-size: 9px;")
            vlayout.addWidget(symbol_lbl)
            vlayout.addWidget(text_lbl)
            btn.clicked.connect(callback)
            layout.addWidget(btn)
            self._action_buttons.append(btn)
            self._symbol_labels.append(symbol_lbl)

        zoom_box = QWidget()
        zoom_layout = QVBoxLayout(zoom_box)
        zoom_layout.setContentsMargins(3, 6, 3, 4)
        zoom_layout.setSpacing(2)

        zoom_title = QLabel("Zoom")
        zoom_title.setAlignment(Qt.AlignCenter)
        zoom_title.setStyleSheet("font-size:8px; font-weight:500;")
        zoom_layout.addWidget(zoom_title)

        for txt, val in [('-', -1), ('+', 1)]:
            btn = QPushButton(txt)
            btn.setFixedSize(52, 22)
            btn.setStyleSheet("font-size: 14px; font-weight: bold;")
            btn.clicked.connect(lambda _, v=val: self.adjust_slider(v))
            zoom_layout.addWidget(btn)

        layout.addWidget(zoom_box)
        layout.addStretch()
        self.splitter.addWidget(panel)
        return panel

    def _zoom_to_and_capture(self, *args, **kwargs):
        return self.export_controller._zoom_to_and_capture(*args, **kwargs)

    def _finish_qg_capture(self, *args, **kwargs):
        return self.export_controller._finish_qg_capture(*args, **kwargs)

    def _finish_qg_direct(self, *args, **kwargs):
        return self.export_controller._finish_qg_direct(*args, **kwargs)

    def _flatgen_script_path(self, *args, **kwargs):
        return self.export_controller._flatgen_script_path(*args, **kwargs)

    def _flatgen_launch_worker(self, *args, **kwargs):
        return self.export_controller._flatgen_launch_worker(*args, **kwargs)

    def _flatgen_hide_progress(self, *args, **kwargs):
        return self.export_controller._flatgen_hide_progress(*args, **kwargs)

    def _flatgen_async_img(self, *args, **kwargs):
        return self.export_controller._flatgen_async_img(*args, **kwargs)

    def _flatgen_async_array(self, *args, **kwargs):
        return self.export_controller._flatgen_async_array(*args, **kwargs)

    def _flatgen_async_img_full_disk(self, *args, **kwargs):
        return self.export_controller._flatgen_async_img_full_disk(*args, **kwargs)

    def _quick_generate_direct(self, *args, **kwargs):
        return self.export_controller._quick_generate_direct(*args, **kwargs)

    def _get_active_storms_in_region(self, *args, **kwargs):
        return self.export_controller._get_active_storms_in_region(*args, **kwargs)

    def _add_tcid_style_footer(self, *args, **kwargs):
        return self.export_controller._add_tcid_style_footer(*args, **kwargs)

    def _add_floater_style(self, *args, **kwargs):
        return self.export_controller._add_floater_style(*args, **kwargs)

    def _add_normal_footer(self, *args, **kwargs):
        return self.export_controller._add_normal_footer(*args, **kwargs)

    def _quick_generate(self, *args, **kwargs):
        return self.export_controller._quick_generate(*args, **kwargs)

    def _export_current_image(self, *args, **kwargs):
        return self.export_controller._export_current_image(*args, **kwargs)

    def _get_full_disk_extent(self, *args, **kwargs):
        return self.export_controller._get_full_disk_extent(*args, **kwargs)

    def _apply_cartopy_grid_to_image(self, image: QImage) -> QImage:
        try:
            import cartopy.crs as ccrs
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
            import numpy as np
            from PySide6.QtCore import QBuffer
            from io import BytesIO
        except ImportError:
            return image
        try:
            w, h = image.width(), image.height()
            if w < 100 or h < 100:
                return image
            vp = self.graphics_view.viewport()
            if not vp:
                return image
            pix_item = None
            for item in reversed(self.graphics_view.scene().items()):
                if isinstance(item, QGraphicsPixmapItem):
                    pix_item = item
                    break
            if not pix_item:
                return image
            def pixel_to_lonlat(px, py):
                sc = self.graphics_view.mapToScene(px, py)
                ix = sc.x() - pix_item.pos().x()
                iy = sc.y() - pix_item.pos().y()
                if ix < 0 or iy < 0 or ix > pix_item.pixmap().width() or iy > pix_item.pixmap().height():
                    return None, None
                if self.current_geotransform and self.current_crs:
                    from pyproj import Transformer
                    transform = self.current_geotransform
                    native_res_m = abs(transform.a)
                    native_extent = abs(transform.c)
                    native_grid_w = int(round(2 * native_extent / native_res_m))
                    scale = native_grid_w / max(pix_item.pixmap().width(), 1)
                    x_native = ix * scale
                    y_native = iy * scale
                    x_proj, y_proj = transform * (x_native, y_native)
                    tr = Transformer.from_crs(self.current_crs, "EPSG:4326", always_xy=True)
                    lon, lat = tr.transform(x_proj, y_proj)
                    return lon, lat
                return None, None
            vrect = vp.rect()
            corners = [
                pixel_to_lonlat(vrect.left(), vrect.top()),
                pixel_to_lonlat(vrect.right(), vrect.top()),
                pixel_to_lonlat(vrect.right(), vrect.bottom()),
                pixel_to_lonlat(vrect.left(), vrect.bottom()),
            ]
            lons = [c[0] for c in corners if c[0] is not None]
            lats = [c[1] for c in corners if c[1] is not None]
            if len(lons) < 4 or len(lats) < 4:
                return image
            lon_min, lon_max = min(lons), max(lons)
            lat_min, lat_max = min(lats), max(lats)
            dpi = 150
            fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi)
            central_lon = (lon_min + lon_max) / 2
            ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
            ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
            ax.set_frame_on(False)
            ax.axis("off")
            gl = ax.gridlines(draw_labels=False, dms=True, linewidth=0.5, color='gray', alpha=0.7, linestyle='--')
            fig.canvas.draw()
            fig_w, fig_h = fig.canvas.get_width_height()
            buf = BytesIO()
            fig.savefig(buf, format='raw', dpi=dpi, bbox_inches='tight', pad_inches=0, transparent=True)
            buf.seek(0)
            raw = np.frombuffer(buf.getvalue(), dtype=np.uint8)
            buf.close()
            plt.close(fig)
            overlay_arr = raw.reshape((fig_h, fig_w, 4))
            if overlay_arr.shape[1] != w or overlay_arr.shape[0] != h:
                from PIL import Image as PILImage
                pil_over = PILImage.fromarray(overlay_arr, 'RGBA')
                pil_over = pil_over.resize((w, h), PILImage.LANCZOS)
                overlay_arr = np.array(pil_over)
            qimg_over = QImage(overlay_arr.data, overlay_arr.shape[1], overlay_arr.shape[0],
                               overlay_arr.strides[0], QImage.Format_RGBA8888)
            painter = QPainter(image)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            painter.drawImage(0, 0, qimg_over)
            painter.end()
        except Exception as e:
            self.log(f"Cartopy grid overlay error: {e}")
        return image

    def _quick_load_common_product(self, kind: str):

        self.log(f"Quick load requested: {kind}")

        if kind == "temperature":

            if hasattr(self, 'band_checkboxes') and self.band_checkboxes:
                for b in ["B13", "B14", "B15", "B07"]:
                    if b in self.band_checkboxes:
                        self.band_checkboxes[b].setChecked(True)
                        self.satellite_controller.load_selected_band_or_product()
                        return
            QMessageBox.information(self, "Quick Load", "Temperature band not found in current scene. Use Band Selection or Load Available Bands.")
        elif kind in ("true_color", "night_microphysics"):

            key = "true_color" if kind == "true_color" else "night_microphysics"
            if hasattr(self, '_product_tile_widgets') and key in self._product_tile_widgets:
                tile = self._product_tile_widgets[key]
                if tile.isEnabled():
                    tile.click()
                    return
            QMessageBox.information(self, "Quick Load", f"{kind} RGB product not available for current scene.")
        else:
            self.log(f"No direct mapping yet for quick product '{kind}' -- extend _quick_load_common_product as needed.")



    def _view_temperature_at_cursor(self):

        if self._ir_kelvin is None:
            self.log("No temperature data loaded.")
            return
        if not hasattr(self, '_last_mouse_scene_pos') or self._last_mouse_scene_pos is None:
            self.log("Move mouse over image first.")
            return

        sx, sy = self._last_mouse_scene_pos
        try:

            h, w = self._ir_kelvin.shape

            pixmap_item = None
            for item in reversed(self.graphics_view.scene().items()):
                if isinstance(item, QGraphicsPixmapItem):
                    pixmap_item = item
                    break
            if pixmap_item:
                img_w = pixmap_item.pixmap().width()
                img_h = pixmap_item.pixmap().height()
                ox = pixmap_item.pos().x()
                oy = pixmap_item.pos().y()
                px = sx - ox
                py = sy - oy
            else:
                img_w, img_h = w, h
                px, py = sx, sy
            ix = max(0, min(int(px * w / img_w) if img_w > 0 else int(px), w-1))
            iy = max(0, min(int(py * h / img_h) if img_h > 0 else int(py), h-1))
            val = float(self._ir_kelvin[iy, ix])

            if np.isnan(val):
                self.log("No valid temp at this location.")
                return

            dot = QGraphicsEllipseItem(sx-8, sy-8, 16, 16)
            dot.setPen(QPen(QColor(255, 0, 0), 2))
            dot.setBrush(QColor(255, 0, 0, 180))
            dot.setZValue(100)

            text = QGraphicsTextItem(f"{val:.1f} K")
            text.setDefaultTextColor(QColor(255, 255, 0))
            text.setFont(QFont("Consolas", 10, QFont.Bold))
            text.setPos(sx + 12, sy - 12)
            text.setZValue(101)

            bg = QGraphicsRectItem(text.boundingRect())
            bg.setPos(text.pos())
            bg.setPen(QPen(QColor(0, 0, 0), 1))
            bg.setBrush(QColor(0, 0, 0, 160))
            bg.setZValue(100)

            self.graphics_view.scene().addItem(bg)
            self.graphics_view.scene().addItem(dot)
            self.graphics_view.scene().addItem(text)

            marker = {
                "type": "temp_point",
                "x": sx, "y": sy,
                "value_k": val,
                "dot": dot,
                "text": text,
                "bg": bg,
            }
            if not hasattr(self, 'temp_markers'):
                self.temp_markers = []
            self.temp_markers.append(marker)

            self.log(f"Temp marker placed: {val:.1f} K")

            self.forecast_controller._add_temp_point_as_track(val, sx, sy)

        except Exception as e:
            self.log(f"Marker error: {e}")

    def clear_temp_markers(self, log=True):

        if not hasattr(self, 'temp_markers') or not self.temp_markers:
            return
        cleared = 0
        for m in list(self.temp_markers):
            for key in ('dot', 'text', 'bg'):
                item = m.get(key)
                if item and hasattr(item, 'scene') and item.scene():
                    try:
                        item.scene().removeItem(item)
                        cleared += 1
                    except Exception:
                        pass
        self.temp_markers.clear()
        if log:
            self.log(f"Cleared {cleared} temperature markers.")

    def _init_center_panel(self):
        self.center_panel = QWidget()
        layout = QVBoxLayout(self.center_panel)
        layout.setSpacing(8)
        self.graphics_view = ZoomableGraphicsView()
        self.graphics_view.zoomChanged.connect(self.handle_zoom_change)
        self.graphics_view.imageMouseMoved.connect(self._on_viewport_mouse_moved)
        self.graphics_view.imageRectSelected.connect(self._on_viewport_rect_selected)

        _orig_set_image = self.graphics_view.set_image
        _app_self = self
        self._visualizer_reprojecting = False
        self._visualizer_deferred = None
        self._current_visualizer_mode = None
        self._original_scene_image = None
        self._full_disk_scene_image = None
        self._reproject_cache = {}
        def _visualizer_aware_set_image(*args, **kwargs):
            _orig_set_image(*args, **kwargs)
            if _app_self._visualizer_reprojecting:
                return
            scene = _app_self.graphics_view.scene()
            if scene:
                for item in scene.items():
                    if isinstance(item, QGraphicsPixmapItem) and item.zValue() == 0:
                        pm = item.pixmap()
                        if pm and not pm.isNull():
                            _app_self._original_scene_image = pm.toImage()
                            _app_self._full_disk_scene_image = pm.toImage()
                            # Save original CRS and geotransform for later restoration
                            _app_self._full_disk_crs = _app_self.current_crs
                            _app_self._full_disk_geotransform = _app_self.current_geotransform
                            break
            vis = _app_self.settings.get("visualizer", "Full Disk")
            if vis != "Full Disk":
                _app_self.apply_visualizer()
            elif _app_self._gl_mode_active():
                # New full-disk image arrived while a GL projection is showing:
                # refresh the GPU texture + mesh so it stays in sync.
                _app_self._gl_refresh_image()
        self.graphics_view.set_image = _visualizer_aware_set_image
        
        self.viewport_frame = ViewportFrame(self)
        fl = QVBoxLayout(self.viewport_frame)
        fl.setContentsMargins(0, 0, 0, 0)
        self.viewport_stack = QStackedWidget()
        fl.addWidget(self.viewport_stack)
        self.gl_map_widget = GLMapWidget()
        self.gl_map_widget.setVisible(False)
        self.viewport_stack.addWidget(self.graphics_view)
        self.viewport_stack.addWidget(self.gl_map_widget)
        self._gl_page_index = self.viewport_stack.indexOf(self.gl_map_widget)
        self._main_split_host = MainSplitHost(self)
        self.viewport_stack.addWidget(self._main_split_host)
        self._main_split_host.setVisible(False)
        if self._mp_manager is None:
            self._mp_manager = MultiPanelManager(self)
        self._mp_manager.attach_main_host(self._main_split_host)
        
        self._create_viewport_animation_bar()
        
        layout.addWidget(self.viewport_frame, 1)
        self.splitter.addWidget(self.center_panel)

    def _create_viewport_animation_bar(self):

        if hasattr(self, 'viewport_anim_bar') and self.viewport_anim_bar:
            return

        bar = QFrame(self.graphics_view)
        bar.setStyleSheet("""
            QFrame {
                background: rgba(20, 20, 20, 220);
                border: 1px solid #444;
                border-radius: 4px;
            }
            QPushButton { background: #2A2A2A; color: #EEE; border: 1px solid #555; border-radius: 3px; padding: 2px 8px; }
            QPushButton:hover { background: #3A3A3A; }
            QComboBox { background: #2A2A2A; color: #EEE; border: 1px solid #555; border-radius: 3px; min-width: 55px; }
            QSlider::groove:horizontal { background: #444; height: 4px; border-radius: 2px; }
            QSlider::handle:horizontal { background: #00BCD4; width: 10px; margin: -3px 0; border-radius: 5px; }
        """)
        bar.setFixedHeight(32)
        bar.hide()

        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 2, 6, 2)
        h.setSpacing(6)

        self.vab_play_btn = QPushButton()
        self.vab_play_btn.setIcon(self._std_icon(QStyle.StandardPixmap.SP_MediaPlay, 12))
        self.vab_play_btn.setFixedSize(28, 24)
        self.vab_play_btn.clicked.connect(self._toggle_animation)
        h.addWidget(self.vab_play_btn)

        h.addWidget(QLabel("Speed:"))
        self.vab_speed = QComboBox()
        self.vab_speed.addItems(["0.5x", "1x", "2x", "4x", "8x"])
        self.vab_speed.setCurrentText("1x")
        self.vab_speed.currentTextChanged.connect(self._on_anim_speed_changed)
        h.addWidget(self.vab_speed)

        self.vab_slider = QSlider(Qt.Horizontal)
        self.vab_slider.setRange(0, 0)
        self.vab_slider.setFixedHeight(18)
        self.vab_slider.valueChanged.connect(self._on_anim_frame_changed)
        h.addWidget(self.vab_slider, 1)

        self.vab_frame_label = QLabel("0 / 0")
        self.vab_frame_label.setStyleSheet("color:#AAA; font-size:9px; min-width:48px;")
        h.addWidget(self.vab_frame_label)

        self.viewport_anim_bar = bar

        self._update_viewport_animation_bar_visibility()
        
        # Initialize viewport info box
        self.viewport_info_box = ViewportInfoBox(self.graphics_view)
        self._viewport_infobox_track_id = None
        
        # Restore info box position if saved
        saved_x = self.settings.get("track_bulletin_x")
        saved_y = self.settings.get("track_bulletin_y")
        if saved_x is not None and saved_y is not None:
            self.viewport_info_box.set_position(saved_x, saved_y)
        else:
            self.apply_track_info_position()
        
        # Connect position changed signal
        self.viewport_info_box.position_changed.connect(self._on_info_box_position_changed)
        
        # Set visibility based on settings
        self.viewport_info_box.setVisible(self.settings.get("track_bulletin_visible", True))

    def _update_viewport_animation_bar_visibility(self):

        enabled = self.settings.get("animation_controls_on_viewport", False)
        if not hasattr(self, 'viewport_anim_bar') or not self.viewport_anim_bar:
            return
        self.viewport_anim_bar.setVisible(enabled)

        if enabled:
            self._position_viewport_animation_bar()

    def _position_viewport_animation_bar(self):
        
        if not hasattr(self, 'viewport_anim_bar') or not self.viewport_anim_bar or not self.viewport_anim_bar.isVisible():
            return
        if not hasattr(self, 'graphics_view') or not self.graphics_view:
            return
        
        gv = self.graphics_view
        bar = self.viewport_anim_bar
        
        w = min(520, max(280, gv.width() - 40))
        bar.setFixedWidth(w)
        
        x = (gv.width() - w) // 2
        y = gv.height() - bar.height() - 8
        bar.move(max(8, x), max(8, y))
        bar.raise_()

    def apply_track_info_position(self):
        """Apply the track information box and label position based on settings."""
        pos_setting = self.settings.get("track_info_position", "top_right").lower().replace(" ", "_")
        gv = self.graphics_view
        if not gv:
            return
            
        margin = 12
        
        # Position viewport_info_box
        if hasattr(self, 'viewport_info_box') and self.viewport_info_box:
            box_w = self.viewport_info_box.width()
            box_h = self.viewport_info_box.height()
            
            if pos_setting == "top_right":
                x = gv.width() - box_w - margin
                y = margin
            elif pos_setting == "top_left":
                x = margin
                y = margin
            elif pos_setting == "bottom_left":
                x = margin
                y = gv.height() - box_h - margin
            elif pos_setting == "bottom_right":
                x = gv.width() - box_w - margin
                y = gv.height() - box_h - margin
            else:
                x = gv.width() - box_w - margin
                y = margin
                
            self.viewport_info_box.set_position(max(8, x), max(8, y))
            self.viewport_info_box.raise_()
        
        # Position _track_info_label
        if hasattr(self, '_track_info_label') and self._track_info_label is not None:
            _m = 10
            lbl_w = self._track_info_label.width()
            lbl_h = self._track_info_label.height()
            if pos_setting == "top_left":
                tx, ty = _m, _m
            elif pos_setting == "bottom_left":
                tx, ty = _m, gv.height() - lbl_h - _m
            elif pos_setting == "bottom_right":
                tx, ty = gv.width() - lbl_w - _m, gv.height() - lbl_h - _m
            else:
                tx, ty = gv.width() - lbl_w - _m, _m
            self._track_info_label.move(max(0, tx), max(0, ty))

    def _sync_viewport_anim_bar(self):

        if not hasattr(self, 'viewport_anim_bar') or not self.viewport_anim_bar or not self.viewport_anim_bar.isVisible():
            return

        if hasattr(self, 'vab_play_btn'):
            playing = getattr(self, '_anim_playing', False)
            self.vab_play_btn.setIcon(self._std_icon(
                QStyle.StandardPixmap.SP_MediaPause if playing else QStyle.StandardPixmap.SP_MediaPlay, 12))

        if hasattr(self, 'vab_speed') and hasattr(self, 'anim_speed'):
            try:
                self.vab_speed.blockSignals(True)
                self.vab_speed.setCurrentText(self.anim_speed.currentText())
                self.vab_speed.blockSignals(False)
            except Exception:
                pass

        if hasattr(self, 'vab_slider'):
            frames = getattr(self, '_anim_frames', [])
            total = len(frames) if frames else 0
            idx = getattr(self, '_anim_index', 0)
            self.vab_slider.blockSignals(True)
            self.vab_slider.setRange(0, max(0, total - 1))
            self.vab_slider.setValue(idx)
            self.vab_slider.blockSignals(False)

        if hasattr(self, 'vab_frame_label'):
            frames = getattr(self, '_anim_frames', [])
            total = len(frames) if frames else 0
            idx = getattr(self, '_anim_index', 0) + 1
            self.vab_frame_label.setText(f"{idx} / {total}")

    def _init_right_panel(self):
        sidebar_container = QScrollArea()
        sidebar_container.setWidgetResizable(True)
        sidebar_container.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._sidebar_container = sidebar_container
        sidebar_content = QWidget()
        sidebar_layout = QVBoxLayout(sidebar_content)
        sidebar_layout.setSpacing(8)
        sidebar_layout.setContentsMargins(6, 6, 6, 6)

        self._data_group = QGroupBox("Select Data & Product")
        self._data_group.setStyleSheet("""
            QGroupBox {
                color: #5D8AA8;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        data_group = self._data_group
        data_layout = QVBoxLayout(data_group)

        sat_layout = QHBoxLayout()
        sat_layout.addWidget(QLabel("Satellite:"))
        self.sat_combo = QComboBox()
        for sid, label in zip(self._perm_sat_ids, self._perm_sat_labels):
            self.sat_combo.addItem(label, sid)
        self.sat_combo.setCurrentText("Himawari 9")
        self.sat_combo.currentTextChanged.connect(self.update_satellite_lon)
        self.sat_combo.setMinimumWidth(120)
        sat_layout.addWidget(self.sat_combo)
        data_layout.addLayout(sat_layout)

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItem("Full Disk", "FLDK")
        self.type_combo.addItem("Japan", "Japan")
        self.type_combo.addItem("Target", "Target")
        self.type_combo.setCurrentText("Full Disk")
        self.type_combo.setMinimumWidth(100)
        type_layout.addWidget(self.type_combo)
        data_layout.addLayout(type_layout)

        self.sat_combo.currentTextChanged.connect(self._update_type_options)
        self.sat_combo.currentTextChanged.connect(lambda: self._build_band_checkboxes(getattr(self, 'available_bands', [])))
        self.sat_combo.currentTextChanged.connect(self._on_sat_combo_changed)
        self._update_type_options()

        is_pro = (getattr(self, 'current_mode', 'casual') == "professional")

        if is_pro:

            self._dt_group = QGroupBox("Date / Time (UTC)")
            self._dt_group.setStyleSheet("QGroupBox { color:#5D8AA8; font-weight:bold; border:1px solid #444; }")
            dt_group = self._dt_group
            dt_l = QVBoxLayout(dt_group)

            row1 = QHBoxLayout()
            row1.addWidget(QLabel("Y:"))
            self.year_combo = QComboBox()
            row1.addWidget(self.year_combo, 1)

            row1.addWidget(QLabel("M:"))
            self.month_combo = QComboBox()
            row1.addWidget(self.month_combo, 1)

            row1.addWidget(QLabel("D:"))
            self.day_combo = QComboBox()
            row1.addWidget(self.day_combo, 1)

            dt_l.addLayout(row1)

            row2 = QHBoxLayout()
            row2.addWidget(QLabel("HH:"))
            self.hour_combo = QComboBox()
            row2.addWidget(self.hour_combo, 1)

            row2.addWidget(QLabel("mm:"))
            self.minute_combo = QComboBox()
            row2.addWidget(self.minute_combo, 1)
            dt_l.addLayout(row2)

            data_layout.addWidget(dt_group)
        else:

            date_layout = QHBoxLayout()
            date_layout.addWidget(QLabel("Date:"))
            self.year_combo = QComboBox()
            date_layout.addWidget(self.year_combo)

            self.month_combo = QComboBox()
            date_layout.addWidget(self.month_combo)

            self.day_combo = QComboBox()
            date_layout.addWidget(self.day_combo)

            data_layout.addLayout(date_layout)

            time_layout = QHBoxLayout()
            time_layout.addWidget(QLabel("Time (UTC):"))
            self.hour_combo = QComboBox()
            time_layout.addWidget(self.hour_combo)

            self.minute_combo = QComboBox()
            time_layout.addWidget(self.minute_combo)
            data_layout.addLayout(time_layout)

        self._populate_available_dates()
        self.year_combo.currentTextChanged.connect(self._update_month_day_combos)
        self.month_combo.currentTextChanged.connect(self._update_month_day_combos)
        self.sat_combo.currentTextChanged.connect(self._populate_available_dates)
        self.sat_combo.currentTextChanged.connect(self.auto_load_bands)
        self.type_combo.currentTextChanged.connect(self._populate_available_dates)
        self.type_combo.currentTextChanged.connect(self._update_sataid_tab_visibility)
        self.type_combo.currentTextChanged.connect(self.auto_load_bands)
        self.type_combo.currentTextChanged.connect(lambda t: self.log(f"Type changed to {t}"))
        self.year_combo.currentTextChanged.connect(self.auto_load_bands)
        self.month_combo.currentTextChanged.connect(self.auto_load_bands)
        self.day_combo.currentTextChanged.connect(self._populate_available_times)
        self.day_combo.currentTextChanged.connect(self.auto_load_bands)
        self.hour_combo.currentTextChanged.connect(self._filter_minutes_for_hour)
        self.hour_combo.currentTextChanged.connect(self.auto_load_bands)
        self.minute_combo.currentTextChanged.connect(self.auto_load_bands)

        if self._available_dates and self.year_combo.currentText():
            self.auto_load_bands()
        self.load_bands_btn = QPushButton("Load Available Bands")
        self.load_bands_btn.setStyleSheet("""
            QPushButton {
                background: #5D8AA8;
                color: white;
                padding: 8px;
                border-radius: 3px;
                border: 1px solid #444;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #4A6572;
            }
        """)
        self.load_bands_btn.clicked.connect(self._on_load_bands_clicked)

        data_layout.addWidget(self.load_bands_btn)

        self._bands_group = QGroupBox("Band Selection")
        self._bands_group.setStyleSheet("""
            QGroupBox {
                color: #FF9800;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 0px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        bands_group = self._bands_group
        bands_layout = QVBoxLayout(bands_group)
        self.bands_grid_layout = QGridLayout()
        self.bands_grid_layout.setSpacing(5)
        bands_layout.addLayout(self.bands_grid_layout)
        self.selected_bands_label = QLabel("Selected: 0 bands")
        self.selected_bands_label.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")
        bands_layout.addWidget(self.selected_bands_label)
        data_layout.addWidget(bands_group)

        self._products_group = QGroupBox("RGB Products")
        self._products_group.setStyleSheet("""
            QGroupBox {
                color: #8B5CF6;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 4px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        products_group = self._products_group
        products_outer = QVBoxLayout(products_group)
        products_outer.setSpacing(4)
        products_outer.setContentsMargins(6, 4, 6, 6)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        self._product_filter = "ALL"
        self._product_filter_btns = {}
        for label in ("All", "Day", "Night"):
            fb = QPushButton(label)
            fb.setCheckable(True)
            fb.setChecked(label == "All")
            fb.setFixedHeight(22)
            fb.setStyleSheet("""
                QPushButton {
                    background: #2D2D2D; color: #AAA;
                    border: 1px solid #444; border-radius: 3px;
                    font-size: 10px; padding: 0 8px;
                }
                QPushButton:checked {
                    background: #3D2B6E; color: #C9B8F8;
                    border: 1px solid #7C5CBF;
                }
                QPushButton:hover:!checked { background: #3A3A3A; }
            """)
            fb.clicked.connect(lambda _, l=label: self._set_product_filter(l))
            filter_row.addWidget(fb)
            self._product_filter_btns[label] = fb
        # Professional filter button (shown only in professional mode)
        self._pro_filter_btn = QPushButton("Professional")
        self._pro_filter_btn.setCheckable(True)
        self._pro_filter_btn.setChecked(False)
        self._pro_filter_btn.setFixedHeight(22)
        self._pro_filter_btn.setStyleSheet("""
            QPushButton {
                background: #2D2D2D; color: #AAA;
                border: 1px solid #444; border-radius: 3px;
                font-size: 10px; padding: 0 8px;
            }
            QPushButton:checked {
                background: #4A1A6E; color: #E8D0F8;
                border: 1px solid #9C5CFF;
            }
            QPushButton:hover:!checked { background: #3A3A3A; }
        """)
        self._pro_filter_btn.clicked.connect(lambda: self._set_product_filter("Professional"))
        filter_row.addWidget(self._pro_filter_btn)
        self._product_filter_btns["Professional"] = self._pro_filter_btn
        filter_row.addStretch()
        products_outer.addLayout(filter_row)

        self.products_grid_layout = QGridLayout()
        self.products_grid_layout.setSpacing(4)
        self.products_grid_layout.setContentsMargins(0, 2, 0, 0)
        products_outer.addLayout(self.products_grid_layout)
        self._product_tile_widgets = {}
        self._build_product_tiles()
        data_layout.addWidget(products_group)

        self._overlays_group = QGroupBox("Overlays")
        self._overlays_group.setStyleSheet("""
            QGroupBox {
                color: #5D8AA8;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        overlays_group = self._overlays_group
        overlays_layout = QVBoxLayout(overlays_group)
        overlays_layout.setSpacing(4)
        self.overlay_checkboxes = {}

        _cb_style = """
            QCheckBox {
                color: #EEE; padding: 5px; font-size: 10px;
                background: #2D2D2D; border-radius: 3px;
            }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border-radius: 3px;
                background-color: #2D2D2D;
                border: 2px solid #555;
            }
            QCheckBox::indicator:checked {
                background-color: #4CAF50;
                border: 2px solid #4CAF50;
            }
            QCheckBox::indicator:hover {
                border: 2px solid #66BB6A;
            }
            QCheckBox:hover:!disabled { background: #3D3D3D; }
        """
        _inner_style = """
            QGroupBox {
                border: 1px solid #3A3A3A;
                border-radius: 3px;
                padding: 4px 2px;
                margin-top: 1px;
            }
        """

        # Inner group 1: Show Grid / Show Coastlines / Show Info Box / Track Legends
        inner1 = QGroupBox()
        inner1.setStyleSheet(_inner_style)
        inner1_grid = QGridLayout()
        inner1_grid.setSpacing(3)
        inner1.setLayout(inner1_grid)

        initial = self.settings.get("grid_enabled", True)
        cb_grid = QCheckBox("Show Grid")
        cb_grid.setChecked(bool(initial))
        cb_grid.setStyleSheet(_cb_style)
        cb_grid.toggled.connect(self.toggle_grid)
        self.overlay_checkboxes["Show Grid"] = cb_grid
        self.grid_enabled = bool(initial)
        inner1_grid.addWidget(cb_grid, 0, 0)

        initial = self.settings.get("coast_enabled", True)
        cb_coast = QCheckBox("Show Coastlines")
        cb_coast.setChecked(bool(initial))
        cb_coast.setStyleSheet(_cb_style)
        cb_coast.toggled.connect(self.toggle_coastlines)
        self.overlay_checkboxes["Show Coastlines"] = cb_coast
        self.coast_enabled = bool(initial)
        inner1_grid.addWidget(cb_coast, 0, 1)

        initial = self.settings.get("info_box_enabled", False)
        cb_info = QCheckBox("Show Info Box")
        cb_info.setChecked(bool(initial))
        cb_info.setStyleSheet(_cb_style)
        cb_info.toggled.connect(self._on_show_info_toggled)
        self.overlay_checkboxes["Show Info Box"] = cb_info
        inner1_grid.addWidget(cb_info, 0, 2)

        initial = self.settings.get("track_info_enabled", False)
        cb_track = QCheckBox("Track Legends")
        cb_track.setChecked(bool(initial))
        cb_track.setStyleSheet(_cb_style)
        cb_track.toggled.connect(self.toggle_track_info)
        self.overlay_checkboxes["Track Legends"] = cb_track
        self.track_info_enabled = bool(initial)
        inner1_grid.addWidget(cb_track, 1, 0)

        self.coast_region_combo = QComboBox()
        self.coast_region_combo.addItems(["Auto", "WestPac", "EastPac", "Indian", "Mediterranean", "Atlantic", "Pacific"])
        self.coast_region_combo.setCurrentText(self.settings.get("coast_region", "Auto"))
        self.coast_region_combo.setMinimumWidth(80)
        self.coast_region_combo.currentTextChanged.connect(self._on_coast_region_changed)
        self.coast_region_combo.setVisible(self.satellite_controller._is_beta_viewport())
        self.coast_region_combo.setEnabled(False)
        self.coast_region_combo.setStyleSheet("QComboBox { background-color: #505050; color: #e0e0e0; }")
        inner1_grid.addWidget(self.coast_region_combo, 1, 1)

        self.target_areas_cb = QCheckBox("Target")
        self.target_areas_cb.setStyleSheet(_cb_style)
        self.target_areas_cb.setChecked(False)
        self.target_areas_cb.toggled.connect(self._toggle_target_areas)
        inner1_grid.addWidget(self.target_areas_cb, 1, 2)
        self.overlay_checkboxes["Target"] = self.target_areas_cb

        # ATCF Storm Trackers (KnackWX API)
        self.atcf_cb = QCheckBox("ATCF")
        self.atcf_cb.setStyleSheet(_cb_style)
        self.atcf_cb.setChecked(False)
        self.atcf_cb.setToolTip("Show ATCF storm positions from KnackWX API")
        self.atcf_cb.toggled.connect(self._toggle_atcf_overlay)
        inner1_grid.addWidget(self.atcf_cb, 2, 0)
        self.overlay_checkboxes["ATCF"] = self.atcf_cb

        # Live weather/recon aircraft (NOAA Hurricane Hunters, USAF WC-130J)
        self.recon_cb = QCheckBox("Recon Planes")
        self.recon_cb.setStyleSheet(_cb_style)
        self.recon_cb.setChecked(False)
        self.recon_cb.setToolTip(
            "Live-track NOAA Hurricane Hunters & USAF WC-130J recon planes\n"
            "(adsb.fi open data, OpenSky fallback)")
        self.recon_cb.toggled.connect(self._toggle_recon_overlay)
        inner1_grid.addWidget(self.recon_cb, 2, 1)
        self.overlay_checkboxes["Recon Planes"] = self.recon_cb

        self.recon_list_btn = QPushButton("Recon List")
        self.recon_list_btn.setStyleSheet(
            "QPushButton { background: #3D3D3D; color: #EEE; border: 1px solid #555;"
            "  border-radius: 3px; padding: 4px 6px; font-size: 10px; }"
            "QPushButton:hover { background: #4D4D4D; }")
        self.recon_list_btn.setToolTip("Open the detached live recon aircraft status window")
        self.recon_list_btn.clicked.connect(self._open_recon_window)
        inner1_grid.addWidget(self.recon_list_btn, 2, 2)

        self._coast_row_widget = None
        overlays_layout.addWidget(inner1)

        # Inner group 3: Show Winds (AMV) + AMV Height Filter
        self._press_group = QGroupBox()
        self._press_group.setStyleSheet(_inner_style)
        _press_l = QVBoxLayout(self._press_group)
        _press_l.setSpacing(4)

        if getattr(self, 'current_mode', 'casual') == "professional":
            initial = self.settings.get("winds_enabled", False)
            cb_winds = QCheckBox("Show Winds (AMV)")
            cb_winds.setChecked(bool(initial))
            cb_winds.setStyleSheet(_cb_style)
            cb_winds.toggled.connect(self.toggle_winds)
            self.overlay_checkboxes["Show Winds (AMV)"] = cb_winds
            self.winds_enabled = bool(initial)
            _press_l.addWidget(cb_winds)

        press_row = QHBoxLayout()
        press_label = QLabel("AMV HEIGHT FILTER:")
        press_label.setStyleSheet("color: #AAA; font-size: 10px; font-weight: bold;")
        press_row.addWidget(press_label)
        self.amv_press_combo = QComboBox()
        self.amv_press_combo.addItems(["All", "Surface (1000-850 hPa)", "Mid (850-500 hPa)", "Upper (500-200 hPa)", "Stratosphere (<200 hPa)"])
        self.amv_press_combo.setCurrentText("All")
        self.amv_press_combo.currentTextChanged.connect(
            lambda _t: (setattr(self, '_winds_persist', False), self._draw_winds_overlay()))
        press_row.addWidget(self.amv_press_combo, 1)
        _press_l.addLayout(press_row)

        sat_row = QHBoxLayout()
        sat_label = QLabel("SATELLITE SOURCE:")
        sat_label.setStyleSheet("color: #AAA; font-size: 10px; font-weight: bold;")
        sat_row.addWidget(sat_label)
        self.amv_sat_combo = QComboBox()
        self.amv_sat_combo.addItem("All Satellite", None)
        self.amv_sat_combo.addItem("GEOSAT AMV ONLY", "AMV")
        self.amv_sat_combo.addItem("ALL ASCAT ONLY", "ASCAT")
        self.amv_sat_combo.addItem("ASCAT B (Metop-B)", 0)
        self.amv_sat_combo.addItem("ASCAT C (Metop-C)", 1)
        self.amv_sat_combo.setCurrentIndex(0)
        self.amv_sat_combo.currentIndexChanged.connect(
            lambda _i: (setattr(self, '_winds_persist', False), self._draw_winds_overlay()))
        sat_row.addWidget(self.amv_sat_combo, 1)
        _press_l.addLayout(sat_row)

        pass_row = QHBoxLayout()
        pass_label = QLabel("ASCAT PASS:")
        pass_label.setStyleSheet("color: #AAA; font-size: 10px; font-weight: bold;")
        pass_row.addWidget(pass_label)
        self.amv_pass_combo = QComboBox()
        self.amv_pass_combo.setToolTip(
            "Pick one of the downloaded ASCAT passes to show in the wind "
            "overlay.\nHigher pass number = NEWER/LATEST pass.\n'All Available "
            "Pass' shows every downloaded pass combined."
        )
        self.amv_pass_combo.addItem("All Available Pass", None)
        self.amv_pass_combo.setCurrentIndex(0)
        self.amv_pass_combo.currentIndexChanged.connect(
            lambda _i: self._on_amv_pass_changed())
        pass_row.addWidget(self.amv_pass_combo, 1)
        _press_l.addLayout(pass_row)

        overlays_layout.addWidget(self._press_group)

        # Inner group 4: passive-microwave brightness-temperature IMAGERY
        # (raw sensor swaths — unrelated to the AMV wind-vector toggles above).
        self._mw_group = QGroupBox()
        self._mw_group.setStyleSheet(_inner_style)
        _mw_l = QVBoxLayout(self._mw_group)
        _mw_l.setSpacing(4)

        if getattr(self, 'current_mode', 'casual') == "professional":
            cb_mw = QCheckBox("Show Microwave")
            cb_mw.setStyleSheet(_cb_style)
            cb_mw.toggled.connect(self.toggle_microwave)
            self.overlay_checkboxes["Show Microwave"] = cb_mw
            self.microwave_enabled = False
            _mw_l.addWidget(cb_mw)

        mw_src_row = QHBoxLayout()
        mw_src_label = QLabel("SATELLITE SOURCE:")
        mw_src_label.setStyleSheet("color: #AAA; font-size: 10px; font-weight: bold;")
        mw_src_row.addWidget(mw_src_label)
        self.mw_src_combo = QComboBox()
        self.mw_src_combo.addItem("ATMS 88.2 GHz", "atms")
        self.mw_src_combo.addItem("MIMIC-TC2 89 GHz", "mimic_tc2")
        self.mw_src_combo.addItem("VIIRS I5 11.45 um", "viirs_i5")
        self.mw_src_combo.addItem("AMSR2 L1B 89 GHz", "amsr2_raw")
        self.mw_src_combo.setCurrentIndex(0)
        self.mw_src_combo.currentIndexChanged.connect(self._on_mw_source_changed)
        mw_src_row.addWidget(self.mw_src_combo, 1)
        _mw_l.addLayout(mw_src_row)

        overlays_layout.addWidget(self._mw_group)
        data_layout.addWidget(overlays_group)

        self._band_info_scene = QGroupBox("Band Info & Stats")

        self._band_info_scene.setMaximumHeight(118)
        self._band_info_scene.setStyleSheet("""
            QGroupBox {
                color: #4CAF50; font-weight: bold; font-size: 10pt;
                border: 1px solid #355E35; border-radius: 5px;
                margin-top: 6px; padding-top: 7px; background: #162016;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 6px; }
        """)
        band_info_scene = self._band_info_scene
        bi_l = QVBoxLayout(band_info_scene)
        bi_l.setContentsMargins(8, 4, 8, 4)
        self.scene_band_info_lbl = QLabel("Load scene + pick band (or use Pro grid) for full wavelength, units, and live stats.")
        self.scene_band_info_lbl.setStyleSheet("color:#E8F5E9; font-size:10pt; padding:3px 1px; line-height: 1.3;")
        self.scene_band_info_lbl.setWordWrap(True)
        self.scene_band_info_lbl.setMinimumHeight(82)
        bi_l.addWidget(self.scene_band_info_lbl)
        data_layout.addWidget(band_info_scene)

        sidebar_layout.addWidget(data_group)

        # -- MultiPanel (2x2 four-panel split) ----------------------------------
        # Kept on the Scene tab (NOT inside the Multi-Viewport tab — MultiPanel
        # is a pure 4-way split of the main viewport, nothing else).
        self._build_multipanel_group()
        sidebar_layout.addWidget(self._mp_group)

        sidebar_layout.addStretch()

        self.right_tab_widget = QTabWidget()

        self._tear_filter = TabTearOffFilter(self.right_tab_widget)
        self._floating_windows = set()
        self.right_tab_widget.tabBar().installEventFilter(self._tear_filter)
        self._tear_filter.tearOffRequested.connect(self._on_tab_tear_off)

        scene_tab = QWidget()
        scene_outer = QVBoxLayout(scene_tab)
        scene_outer.setContentsMargins(0, 0, 0, 0)
        self._scene_scroll = QScrollArea()
        self._scene_scroll.setWidgetResizable(True)
        self._scene_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scene_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._scene_scroll.setWidget(sidebar_content)
        scene_scroll = self._scene_scroll
        scene_outer.addWidget(scene_scroll)
        self.right_tab_widget.addTab(scene_tab, "Scene")

        self._pro_tab_widget = QWidget()
        self._pro_tab_index = self.right_tab_widget.addTab(self._pro_tab_widget, "Professional")
        self.right_tab_widget.setTabVisible(self._pro_tab_index, False)
        self._build_pro_tab()

        mode = getattr(self, 'current_mode', 'casual')
        self.tracks_tab = self.forecast_controller._create_tracks_tab()
        self.tracks_tab_index = self.right_tab_widget.addTab(self.tracks_tab, "Tracks")
        self.right_tab_widget.setTabVisible(self.tracks_tab_index, mode in ("professional", "hobby"))

        self.animation_tab = self._create_animation_tab()
        self.animation_tab_index = self.right_tab_widget.addTab(self.animation_tab, "Animation")
        self.right_tab_widget.setTabVisible(self.animation_tab_index, True)

        self.alerts_tab = self.alert_controller._create_alerts_tab()
        self.alerts_tab_index = self.right_tab_widget.addTab(self.alerts_tab, "Alerts")
        self.right_tab_widget.setTabVisible(self.alerts_tab_index, True)

        self.sataid_tab = QWidget()
        self.sataid_tab_layout = QVBoxLayout(self.sataid_tab)
        self.sataid_tab_layout.setContentsMargins(0, 0, 0, 0)
        sataid_scroll = QScrollArea()
        sataid_scroll.setWidgetResizable(True)
        sataid_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sataid_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self.sataid_panel = SataidControlPanel()
        sataid_scroll.setWidget(self.sataid_panel)
        self.sataid_tab_layout.addWidget(sataid_scroll)
        self.sataid_tab_index = self.right_tab_widget.addTab(self.sataid_tab, "SATAID")
        self.right_tab_widget.setTabVisible(self.sataid_tab_index, False)
        self._connect_sataid_signals()

        self.multi_viewport_tab = self._build_multi_viewport_tab()
        self.multi_viewport_tab_index = self.right_tab_widget.addTab(self.multi_viewport_tab, "Multi-Viewport")
        self.right_tab_widget.setTabVisible(self.multi_viewport_tab_index, True)

        self.right_tab_widget.setTabToolTip(0, "Select satellite, band, date/time, and scene overlays")
        self.right_tab_widget.setTabToolTip(self._pro_tab_index, "Advanced channel compositing, devkit, contours, and rendering controls")
        self.right_tab_widget.setTabToolTip(self.tracks_tab_index, "Meteorological track management, NHC data, and overlay controls")
        self.right_tab_widget.setTabToolTip(self.animation_tab_index, "Create and play frame animations with date range selection")
        self.right_tab_widget.setTabToolTip(self.alerts_tab_index, "Weather alerts overview and map generation")
        self.right_tab_widget.setTabToolTip(self.sataid_tab_index, "SATAID-specific controls for brightness, contrast, and measurements")
        self.right_tab_widget.setTabToolTip(self.multi_viewport_tab_index, "Multi-window viewport management for simultaneous monitoring")

        self.right_tab_widget.setMovable(True)
        self.right_tab_widget.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.right_tab_widget.tabBar().customContextMenuRequested.connect(self._on_tab_context_menu)

        self._init_tab_shortcuts()

        self.right_tab_widget.currentChanged.connect(self._save_active_tab)

        self.splitter.addWidget(self.right_tab_widget)
        self.right_panel = self.right_tab_widget
        return self.right_tab_widget

    def _on_load_bands_clicked(self):
        if hasattr(self, 'type_combo') and self.type_combo.currentText() == "SATAID":
            self._load_sataid_bands()
            self._cache_sataid_bands()
        else:
            self.satellite_controller.load_bands_for_date()

    def _build_pro_tab(self):

        pro_scroll = QScrollArea()
        pro_scroll.setWidgetResizable(True)
        pro_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        pro_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        pro_content = QWidget()
        pro_layout = QVBoxLayout(pro_content)
        pro_layout.setSpacing(6)
        pro_layout.setContentsMargins(4, 4, 4, 4)

        band_row = QHBoxLayout()
        band_row.setSpacing(4)
        self.band_lbl = QLabel("Band:")
        self.band_lbl.setStyleSheet("color:#4CAF50; font-size:9pt; font-weight:600;")
        band_row.addWidget(self.band_lbl)
        self.pro_compact_band = QLabel("-- (select in grid or load scene)")
        self.pro_compact_band.setStyleSheet("color:#B0BEC5; font-size:9pt;")
        self.pro_compact_band.setMinimumWidth(140)
        band_row.addWidget(self.pro_compact_band, 1)

        self.pro_show_stats_cb = QCheckBox("Detailed stats here")
        self.pro_show_stats_cb.setChecked(self.settings.get("pro_show_detailed_stats", False))
        self.pro_show_stats_cb.setStyleSheet("QCheckBox { color:#90CAF9; font-size:8pt; }")
        self.pro_show_stats_cb.setToolTip("Hide for clean Pro workspace; rich version lives in Scene tab (recommended)")
        self.pro_show_stats_cb.toggled.connect(self._toggle_pro_stats_visibility)
        band_row.addWidget(self.pro_show_stats_cb)
        pro_layout.addLayout(band_row)

        # -- Birds Eye View ---------------------------------------------------
        self._bev_group = QGroupBox("Birds Eye View (Global Context)")
        self._bev_group.setStyleSheet("""
            QGroupBox { color: #BDBDBD; font-weight: bold; border: 1px solid #444;
                border-radius: 4px; margin-top: 4px; padding-top: 6px; background: #121212; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; font-size: 9px; }
        """)
        bev_layout = QVBoxLayout(self._bev_group)
        self.bev_widget = QLabel("Loading global context...")
        self.bev_widget.setFixedSize(200, 120)
        self.bev_widget.setAlignment(Qt.AlignCenter)
        self.bev_widget.setStyleSheet("background: #000; color: #555; border: 1px solid #333; font-size: 8pt;")
        bev_layout.addWidget(self.bev_widget)
        pro_layout.addWidget(self._bev_group)

        self._devkit_group = QGroupBox("Channel Editor -- Live R/G/B Compositor")
        self._devkit_group.setStyleSheet("""
            QGroupBox { color: #FF9800; font-weight: bold; border: 1px solid #555;
                border-radius: 4px; margin-top: 6px; padding-top: 8px; background: #1E1E1E; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        """)
        dk_layout = QVBoxLayout(self._devkit_group)
        dk_layout.setSpacing(5)
        dk_layout.setContentsMargins(6, 4, 6, 4)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(3)
        mode_row.addWidget(QLabel("Mode:"))
        self._devkit_mode_btns = {}
        self._devkit_mode = "rgb"
        mode_btn_style = """
            QPushButton { background:#252525; color:#BBB; border:1px solid #444; border-radius:3px;
                          font-size:9.5px; padding:2px 8px; min-height:20px; }
            QPushButton:checked { background:#3D2B6E; color:#E8D8FF; border:1px solid #8B5CF6; font-weight:600; }
            QPushButton:hover:!checked { background:#2A2A2A; color:#DDD; }
        """
        for label, key in [("R/G/B", "rgb"), ("Single", "single")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == "rgb")
            btn.setStyleSheet(mode_btn_style)
            btn.clicked.connect(lambda _, k=key: self._switch_devkit_mode(k))
            mode_row.addWidget(btn)
            self._devkit_mode_btns[key] = btn
        mode_row.addStretch()

        self.devkit_live_cb = QCheckBox("Live Apply")
        self.devkit_live_cb.setChecked(True)
        self.devkit_live_cb.setStyleSheet("QCheckBox { color:#BBB; font-size:9.5px; }")
        mode_row.addWidget(self.devkit_live_cb)
        dk_layout.addLayout(mode_row)

        self._devkit_channel_grid = QGridLayout()
        self._devkit_channel_grid.setSpacing(3)
        self._devkit_channel_grid.setContentsMargins(0, 0, 0, 0)
        dk_layout.addLayout(self._devkit_channel_grid)

        

        action_row = QHBoxLayout()
        action_row.setSpacing(4)

        self.apply_btn = QPushButton("Apply (or use Live)")
        self.apply_btn.setStyleSheet("""
            QPushButton { background:#2E7D32; color:white; font-weight:600; border-radius:3px; padding:3px 10px; font-size:9px; }
            QPushButton:hover { background:#388E3C; }
            QPushButton:pressed { background:#1B5E20; }
        """)
        self.apply_btn.clicked.connect(self.generate_devkit_composite)
        action_row.addWidget(self.apply_btn)

        reset_btn = QPushButton("Reset Sliders")
        reset_btn.setStyleSheet("QPushButton { background:#424242; color:#DDD; border-radius:3px; padding:2px 8px; font-size:8.5px; }")
        reset_btn.clicked.connect(self._reset_devkit_sliders)
        action_row.addWidget(reset_btn)

        action_row.addWidget(QLabel("Preset:"))
        self.devkit_preset_combo = QComboBox()
        self.devkit_preset_combo.setStyleSheet("font-size:8.5px; min-width:120px;")
        self.devkit_preset_combo.addItem("-- select RGB product --")
        self.devkit_preset_combo.addItems(list(get_products(self.sat_combo.currentText()).keys()))
        self.devkit_preset_combo.currentTextChanged.connect(self._load_professional_preset)
        action_row.addWidget(self.devkit_preset_combo, 1)

        dk_layout.addLayout(action_row)

        self.pro_status = QLabel("Ready -- load bands for full channel control.")
        self.pro_status.setStyleSheet("color:#888; font-size:9.5px; padding-left:2px;")
        dk_layout.addWidget(self.pro_status)

        pro_layout.addWidget(self._devkit_group)

        self._stats_group = QGroupBox("Band Statistics (current channel data)")
        self._stats_group.setStyleSheet("""
            QGroupBox { color:#90A4AE; font-weight:bold; border:1px solid #444;
                border-radius:4px; margin-top:4px; padding-top:6px; background:#1A1A1A; }
            QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 4px; font-size:9px; }
        """)
        stats_layout = QVBoxLayout(self._stats_group)
        stats_layout.setContentsMargins(6, 3, 6, 3)
        self.pro_stats_label = QLabel("Min/Max/Mean/Std (load scene + apply channel)")
        self.pro_stats_label.setWordWrap(True)
        self.pro_stats_label.setStyleSheet("color:#B0BEC5; font-size:9pt; background:transparent;")
        stats_layout.addWidget(self.pro_stats_label)
        self.pro_stats_group = self._stats_group
        pro_layout.addWidget(self._stats_group)

        if hasattr(self, 'pro_show_stats_cb') and not self.pro_show_stats_cb.isChecked():
            self.pro_stats_group.setVisible(False)

        self._render_group = QGroupBox("Global Render Tweaks")
        self._render_group.setStyleSheet("""
            QGroupBox { color:#78909C; font-weight:bold; border:1px solid #444;
                border-radius:4px; margin-top:4px; padding-top:6px; background:#1A1A1A; }
            QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 4px; font-size:9px; }
        """)
        r_l = QGridLayout(self._render_group)
        r_l.setSpacing(3)
        r_l.setContentsMargins(6, 3, 6, 3)

        r_l.addWidget(QLabel("Global Gamma:"), 0, 0)
        self.pro_global_gamma = QLineEdit("1.0")
        self.pro_global_gamma.setFixedWidth(42)
        self.pro_global_gamma.setStyleSheet("font-size:8.5px;")
        r_l.addWidget(self.pro_global_gamma, 0, 1)

        self.pro_auto_stretch = QCheckBox("Auto min/max from data")
        self.pro_auto_stretch.setChecked(True)
        self.pro_auto_stretch.setStyleSheet("font-size:8.5px; color:#B0BEC5;")
        r_l.addWidget(self.pro_auto_stretch, 0, 2, 1, 2)

        self.pro_equalize = QCheckBox("Histogram equalize")
        self.pro_equalize.setStyleSheet("font-size:8.5px; color:#B0BEC5;")
        r_l.addWidget(self.pro_equalize, 1, 0, 1, 2)

        self.pro_contours = QCheckBox("Show Contours")
        self.pro_contours.setChecked(self.settings.get("pro_contours", False))
        self.pro_contours.setStyleSheet("font-size:8.5px; color:#B0BEC5;")
        self.pro_contours.toggled.connect(self.toggle_pro_contours)
        r_l.addWidget(self.pro_contours, 2, 0, 1, 2)
        
        # Ctrl+Space to clear contour overlay
        self._clear_contour_sc = QShortcut(QKeySequence("Ctrl+Space"), self)
        self._clear_contour_sc.activated.connect(self.clear_contour_overlay)

        apply_render = QPushButton("Re-apply Tweaks")
        apply_render.setFixedHeight(20)
        apply_render.setStyleSheet("QPushButton { background:#455A64; color:#ECEFF1; font-size:8px; border-radius:2px; }")
        apply_render.clicked.connect(self.generate_devkit_composite)
        r_l.addWidget(apply_render, 1, 3)

        pro_layout.addWidget(self._render_group)
        
        # -- Image Emphasis & Function --------------------------------------------
        self._func_group = QGroupBox("Image Emphasis & Function")
        self._func_group.setStyleSheet("""
            QGroupBox { color: #B0BEC5; font-weight: bold; border: 1px solid #444;
                border-radius: 4px; margin-top: 6px; padding-top: 8px; background: #1A1A1A; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; font-size: 9px; }
        """)
        func_l = QVBoxLayout(self._func_group)
        func_l.setSpacing(6)
        func_l.setContentsMargins(8, 4, 8, 4)
        
        # Mode Selection
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("<b>Mode:</b>"))
        self.func_mode_btns = {}
        self.func_mode_group = QButtonGroup(self)
        self.func_mode_group.setExclusive(True)
        func_mode_style = "QPushButton { background:#252525; color:#BBB; border:1px solid #444; border-radius:3px; font-size:8.5px; padding:2px 6px; min-height:20px; } QPushButton:checked { background:#3D2B6E; color:#E8D8FF; border:1px solid #8B5CF6; }"
        for label in ["6bit", "4bit", "Cols", "Mix", "Ext0", "Ext1", "Ext2", "Ext3", "Cmap"]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(func_mode_style)
            mode_row.addWidget(btn)
            self.func_mode_btns[label] = btn
            self.func_mode_group.addButton(btn)
        mode_row.addStretch()
        func_l.addLayout(mode_row)
        
        # VIS & Overlays
        vis_row = QHBoxLayout()
        vis_row.addWidget(QLabel("<b>VIS:</b>"))
        self.func_vis_hour_cb = QCheckBox("hour")
        self.func_vis_hour_cb.setStyleSheet("font-size:8.5px; color:#B0BEC5;")
        vis_row.addWidget(self.func_vis_hour_cb)
        vis_row.addSpacing(20)
        self.func_blue_cb = QCheckBox("Blue")
        self.func_blue_cb.setStyleSheet("font-size:8.5px; color:#B0BEC5;")
        self.func_sandwich_cb = QCheckBox("Sandwich")
        self.func_sandwich_cb.setStyleSheet("font-size:8.5px; color:#B0BEC5;")
        vis_row.addWidget(self.func_blue_cb)
        vis_row.addWidget(self.func_sandwich_cb)
        vis_row.addStretch()
        func_l.addLayout(vis_row)
        
        # Function selection
        fn_row = QHBoxLayout()
        fn_row.addWidget(QLabel("<b>Function:</b>"))
        self.func_selection_btns = {}
        self.func_selection_group = QButtonGroup(self)
        self.func_selection_group.setExclusive(True)
        for label in ["Gray", "Info", "Measur", "Draw", "Obs", "TC"]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setStyleSheet(func_mode_style)
            fn_row.addWidget(btn)
            self.func_selection_btns[label] = btn
            self.func_selection_group.addButton(btn)
        fn_row.addStretch()
        func_l.addLayout(fn_row)
        
        # Gray settings sub-group
        self._gray_group = QGroupBox("Gray")
        self._gray_group.setStyleSheet("QGroupBox { font-size:8.5px; color:#B0BEC5; border: 1px solid #333; margin-top:4px; }")
        gray_l = QGridLayout(self._gray_group)
        
        self.func_gray_revs_cb = QCheckBox("Revs")
        self.func_gray_revs_cb.setStyleSheet("font-size:8.5px; color:#B0BEC5;")
        gray_l.addWidget(self.func_gray_revs_cb, 0, 0)
        
        self.func_gray_color_btn = QPushButton("Color")
        self.func_gray_color_btn.setFixedWidth(50)
        self.func_gray_color_btn.setStyleSheet("font-size:8px; padding:2px;")
        self.func_gray_initial_btn = QPushButton("Initial")
        self.func_gray_initial_btn.setFixedWidth(50)
        self.func_gray_initial_btn.setStyleSheet("font-size:8px; padding:2px;")
        gray_l.addWidget(self.func_gray_color_btn, 0, 1)
        gray_l.addWidget(self.func_gray_initial_btn, 0, 2)
        
        gray_l.addWidget(QLabel("Brit"), 1, 0)
        self.func_gray_brit_sld = QSlider(Qt.Horizontal)
        self.func_gray_brit_sld.setRange(0, 200)
        self.func_gray_brit_sld.setValue(100)
        gray_l.addWidget(self.func_gray_brit_sld, 1, 1, 1, 2)
        
        gray_l.addWidget(QLabel("Cntr"), 2, 0)
        self.func_gray_cntr_sld = QSlider(Qt.Horizontal)
        self.func_gray_cntr_sld.setRange(0, 200)
        self.func_gray_cntr_sld.setValue(100)
        gray_l.addWidget(self.func_gray_cntr_sld, 2, 1, 1, 2)
        
        func_l.addWidget(self._gray_group)
        pro_layout.addWidget(self._func_group)
        
        self.func_gray_brit_sld.valueChanged.connect(self._reapply_emphasis)
        self.func_gray_cntr_sld.valueChanged.connect(self._reapply_emphasis)
        self.func_gray_revs_cb.toggled.connect(self._reapply_emphasis)
        self.func_gray_initial_btn.clicked.connect(self._reset_emphasis)
        self.func_gray_color_btn.clicked.connect(self._open_color_scale_dialog)
        self.func_selection_group.buttonClicked.connect(lambda btn: self._set_func_mode(btn.text()))
        self.func_mode_group.buttonClicked.connect(lambda btn: self._set_emphasis_mode(btn.text()))

        # -- Multi Viewport -----------------------------------------------------
        self._mv_group = QGroupBox("Multi Viewport")
        self._mv_group.setStyleSheet("""
            QGroupBox { color: #CE93D8; font-weight: bold; border: 1px solid #555;
                border-radius: 4px; margin-top: 6px; padding-top: 8px; background: #1E1E1E; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        """)
        mv_layout = QVBoxLayout(self._mv_group)
        mv_layout.setSpacing(4)
        mv_layout.setContentsMargins(6, 4, 6, 4)

        mv_row1 = QHBoxLayout()
        mv_row1.addWidget(QLabel("Windows:"))
        self.mv_count_spin = QSpinBox()
        self.mv_count_spin.setRange(1, 5)
        self.mv_count_spin.setValue(1)
        self.mv_count_spin.setFixedWidth(50)
        mv_row1.addWidget(self.mv_count_spin)
        mv_row1.addSpacing(12)
        self.mv_enable_cb = QCheckBox("Enable")
        self.mv_enable_cb.setStyleSheet("QCheckBox { color: #B0BEC5; font-size: 9pt; }")
        mv_row1.addWidget(self.mv_enable_cb)
        mv_row1.addStretch()
        mv_layout.addLayout(mv_row1)

        self.mv_launch_btn = QPushButton("Launch / Update Windows")
        self.mv_launch_btn.setStyleSheet("""
            QPushButton { background: #7B1FA2; color: white; font-weight: 600;
                border-radius: 3px; padding: 4px 12px; font-size: 9px; }
            QPushButton:hover { background: #8E24AA; }
            QPushButton:pressed { background: #6A1B9A; }
            QPushButton:disabled { background: #444; color: #888; }
        """)
        self.mv_launch_btn.setEnabled(False)
        mv_layout.addWidget(self.mv_launch_btn)

        mv_info = QLabel("Creates separate windows with a mode dropdown.\n"
                         "Modes: viewport (mirror), bands, animation,\n"
                         "forecast, 3d globe.")
        mv_info.setStyleSheet("color: #888; font-size: 8pt; padding: 2px 4px;")
        mv_layout.addWidget(mv_info)

        pro_layout.addWidget(self._mv_group)

        pro_layout.addStretch()

        winds_btn = QPushButton("Load AMV Winds (from embedded NC data)")
        winds_btn.setStyleSheet("QPushButton { background:#1565C0; color:white; font-size:8.5px; padding:2px 6px; border-radius:3px; }")
        winds_btn.clicked.connect(lambda: self.overlay_controller.load_winds_data())
        pro_layout.addWidget(winds_btn)

        self._aor_group = QGroupBox("Area of Responsibilities (AoR) - PAR / JMA (Japan) / Custom")
        self._aor_group.setStyleSheet("""
            QGroupBox { color:#00BCD4; font-weight:bold; border:1px solid #444;
                border-radius:4px; margin-top:4px; padding-top:6px; background:#1A1A1A; }
            QGroupBox::title { subcontrol-origin: margin; left:8px; padding:0 4px; font-size:9px; }
        """)
        aor_l = QVBoxLayout(self._aor_group)
        aor_l.setSpacing(2)
        aor_l.setContentsMargins(6, 3, 6, 3)

        _aor_cb_style = "QCheckBox { color:#B0BEC5; font-size:9pt; }"
        _aor_btn_style = "QPushButton { background:#1e3a5f; color:#90d5ff; font-weight:bold; padding:1px 6px; font-size:8px; border-radius:2px; } QPushButton:hover { background:#2a4a7f; }"

        self.aor_par_cb = QCheckBox("PAR (PAGASA Area of Responsibility)")
        self.aor_jma_cb = QCheckBox("JMA AoR (Japan Area of Responsibility)")
        self.aor_tcid_cb = QCheckBox("TCID (Tropical Cyclone Information Domain)")
        self.aor_fir_cb = QCheckBox("Manila FIR (Flight Information Region)")
        self.aor_custom_cb = QCheckBox("Custom / Other AoR")
        for cb in (self.aor_par_cb, self.aor_jma_cb, self.aor_tcid_cb, self.aor_fir_cb, self.aor_custom_cb):
            cb.setStyleSheet(_aor_cb_style)
            cb.toggled.connect(self._toggle_aor_overlay)
            aor_l.addWidget(cb)

        tcad_row = QHBoxLayout()
        tcad_row.setSpacing(4)
        self.aor_tcad_cb = QCheckBox("TCAD (Tropical Cyclone Advisory Domain)")
        self.aor_tcad_cb.setStyleSheet(_aor_cb_style)
        self.aor_tcad_cb.toggled.connect(self._toggle_aor_overlay)
        tcad_row.addWidget(self.aor_tcad_cb, 1)
        self.tcad_gen_btn = QPushButton("Generate")
        self.tcad_gen_btn.setStyleSheet(_aor_btn_style)
        self.tcad_gen_btn.clicked.connect(self._generate_tcad_image)
        tcad_row.addWidget(self.tcad_gen_btn)
        aor_l.addLayout(tcad_row)

        custom_group = QGroupBox("Custom AoR (multi-point)")
        custom_group.setStyleSheet("QGroupBox { font-size:9pt; }")
        custom_l = QVBoxLayout(custom_group)

        point_row = QHBoxLayout()
        point_row.addWidget(QLabel("Lat:"))
        self.custom_lat_edit = QLineEdit()
        self.custom_lat_edit.setFixedWidth(70)
        point_row.addWidget(self.custom_lat_edit)
        point_row.addWidget(QLabel("Lon:"))
        self.custom_lon_edit = QLineEdit()
        self.custom_lon_edit.setFixedWidth(70)
        point_row.addWidget(self.custom_lon_edit)

        add_point_btn = QPushButton("Add Point")
        add_point_btn.setFixedHeight(22)
        add_point_btn.clicked.connect(self._add_custom_aor_point)
        point_row.addWidget(add_point_btn)

        clear_points_btn = QPushButton("Clear")
        clear_points_btn.setFixedHeight(22)
        clear_points_btn.clicked.connect(self._clear_custom_aor_points)
        point_row.addWidget(clear_points_btn)

        remove_last_btn = QPushButton("Remove Last")
        remove_last_btn.setFixedHeight(22)
        remove_last_btn.clicked.connect(self._remove_last_custom_aor_point)
        point_row.addWidget(remove_last_btn)

        custom_l.addLayout(point_row)

        self.custom_points_list = QListWidget()
        self.custom_points_list.setFixedHeight(80)
        self.custom_points_list.itemDoubleClicked.connect(self._remove_selected_custom_aor_point)
        custom_l.addWidget(self.custom_points_list)

        self.create_track_btn = QPushButton("Create as Track (saved)")
        self.create_track_btn.setStyleSheet("QPushButton { background:#2E7D32; color:white; }")
        self.create_track_btn.clicked.connect(self._create_custom_aor_as_track)
        custom_l.addWidget(self.create_track_btn)

        aor_l.addWidget(custom_group)

        legacy_box_row = QHBoxLayout()
        legacy_box_row.addWidget(QLabel("Legacy (deprecated):"))
        self.aor_custom_edit = QLineEdit("115,5,135,25")
        self.aor_custom_edit.setFixedWidth(120)
        legacy_box_row.addWidget(self.aor_custom_edit)
        aor_apply_btn = QPushButton("Apply")
        aor_apply_btn.setFixedSize(40, 18)
        aor_apply_btn.clicked.connect(lambda: (self.overlay_controller._toggle_aor_overlay(True), self.overlay_controller._update_aor_overlays()))
        legacy_box_row.addWidget(aor_apply_btn)
        aor_l.addLayout(legacy_box_row)

        pro_layout.addWidget(self._aor_group)

        self.aor_par_cb.setChecked(self.settings.get("par_enabled", False))
        self.aor_jma_cb.setChecked(self.settings.get("jma_enabled", False))
        self.aor_tcad_cb.setChecked(self.settings.get("tcad_enabled", False))
        self.aor_tcid_cb.setChecked(self.settings.get("tcid_enabled", False))
        self.aor_fir_cb.setChecked(self.settings.get("fir_enabled", False))
        self.aor_custom_cb.setChecked(self.settings.get("aor_expanded", False))

        self.mv_enable_cb.toggled.connect(self._toggle_multi_viewport)
        self.mv_launch_btn.clicked.connect(self._update_multi_viewport_count)

        self.current_custom_aor_points = []

        pro_scroll.setWidget(pro_content)

        tab_layout = QVBoxLayout(self._pro_tab_widget)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.addWidget(pro_scroll)

        self.devkit_timer = QTimer(self)
        self.devkit_timer.setSingleShot(True)
        self.devkit_timer.setInterval(110)
        self.devkit_timer.timeout.connect(self._on_devkit_timer_fire)

        self.devkit_band1_combos = []
        self.devkit_band2_combos = []
        self.devkit_invert_checkboxes = []
        self.devkit_min_edits = []
        self.devkit_max_edits = []
        self.devkit_gamma_edits = []
        self._last_pro_rgb = None
        self._last_pro_pixmap = None
        self._current_emphasis_mode = "Gray"
        self._rebuild_devkit_channel_grid()
        self._restyle_pro_tab()

    def update_devkit_band_lists(self, band_names):

        if not band_names:
            return
        band_items = ["None"] + list(band_names)

        for combo in getattr(self, 'devkit_band1_combos', []):
            if combo:
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(band_items)
                combo.blockSignals(False)
        for combo in getattr(self, 'devkit_band2_combos', []):
            if combo:
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(band_items)
                combo.blockSignals(False)



        self.set_devkit_enabled(bool(band_names))
        self.pro_status.setText(f"{len(band_names)} bands ready for channel editor.")

    def set_devkit_enabled(self, enabled: bool):
        for lst in (getattr(self, 'devkit_band1_combos', []),
                    getattr(self, 'devkit_band2_combos', []),
                    getattr(self, 'devkit_invert_checkboxes', []),
                    getattr(self, 'devkit_min_edits', []),
                    getattr(self, 'devkit_max_edits', []),
                    getattr(self, 'devkit_gamma_edits', [])):
            for w in lst:
                if w:
                    w.setEnabled(enabled)


    def _rebuild_devkit_channel_grid(self):

        while self._devkit_channel_grid.count():
            item = self._devkit_channel_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.devkit_band1_combos.clear()
        self.devkit_band2_combos.clear()
        self.devkit_invert_checkboxes.clear()
        self.devkit_min_edits.clear()
        self.devkit_max_edits.clear()
        self.devkit_gamma_edits.clear()

        mode = getattr(self, '_devkit_mode', 'rgb')
        grid = self._devkit_channel_grid

        if mode == "single":
            channels = ["Gray"]
        else:
            channels = ["R", "G", "B"]

        headers = ["Ch", "Band 1", "Band 2 (-)", "Inv", "Min", "Max", "Avg"]
        for c, h in enumerate(headers):
            lbl = QLabel(f"<b>{h}</b>")
            lbl.setStyleSheet("color:#78909C; font-size:8px;")
            grid.addWidget(lbl, 0, c)

        ch_colors = {"R": "#FF6B6B", "G": "#69F0AE", "B": "#64B5F6",
                     "Gray": "#E0E0E0"}

        for i, ch in enumerate(channels):
            color = ch_colors.get(ch, "#CFD8DC")
            lbl = QLabel(f"<b>{ch}</b>")
            lbl.setStyleSheet(f"color:{color}; font-size:10px; font-weight:700;")
            grid.addWidget(lbl, i + 1, 0)

            b1 = QComboBox()
            b1.setStyleSheet("font-size:8.5px; min-height:17px;")
            b2 = QComboBox()
            b2.setStyleSheet("font-size:8.5px; min-height:17px;")
            inv = QCheckBox()
            inv.setStyleSheet("margin-left:4px;")
            mn = QLineEdit("0"); mn.setFixedWidth(46); mn.setStyleSheet("font-size:8.5px;")
            mx = QLineEdit("100"); mx.setFixedWidth(46); mx.setStyleSheet("font-size:8.5px;")
            gm = QLineEdit("1.0"); gm.setFixedWidth(36); gm.setStyleSheet("font-size:8.5px;")

            grid.addWidget(b1, i + 1, 1)
            grid.addWidget(b2, i + 1, 2)
            grid.addWidget(inv, i + 1, 3)
            grid.addWidget(mn, i + 1, 4)
            grid.addWidget(mx, i + 1, 5)
            grid.addWidget(gm, i + 1, 6)

            self.devkit_band1_combos.append(b1)
            self.devkit_band2_combos.append(b2)
            self.devkit_invert_checkboxes.append(inv)
            self.devkit_min_edits.append(mn)
            self.devkit_max_edits.append(mx)
            self.devkit_gamma_edits.append(gm)

        if hasattr(self, 'available_bands') and self.available_bands:
            for combo in self.devkit_band1_combos + self.devkit_band2_combos:
                combo.clear()
                combo.addItems(["None"] + list(self.available_bands))


        self._connect_devkit_signals()

    def _switch_devkit_mode(self, key: str):
        self._devkit_mode = key
        for k, btn in self._devkit_mode_btns.items():
            btn.setChecked(k == key)
        self._rebuild_devkit_channel_grid()
        self.pro_status.setText(f"Switched to {key.upper()} mode.")

    def _connect_devkit_signals(self):
        def _maybe_start_timer():
            if getattr(self, 'devkit_live_cb', None) and self.devkit_live_cb.isChecked():
                self.devkit_timer.start()

        for w in (getattr(self, 'devkit_band1_combos', []) +
                  getattr(self, 'devkit_band2_combos', [])):
            try: w.currentTextChanged.disconnect()
            except Exception: pass
            w.currentTextChanged.connect(_maybe_start_timer)

        for w in getattr(self, 'devkit_invert_checkboxes', []):
            try: w.toggled.disconnect()
            except Exception: pass
            w.toggled.connect(_maybe_start_timer)

        for w in (getattr(self, 'devkit_min_edits', []) +
                  getattr(self, 'devkit_max_edits', []) +
                  getattr(self, 'devkit_gamma_edits', [])):
            try: w.textChanged.disconnect()
            except Exception: pass
            w.textChanged.connect(_maybe_start_timer)

        for w in (getattr(self, 'pro_global_gamma', None),):
            if w:
                try: w.textChanged.disconnect()
                except Exception: pass
                w.textChanged.connect(_maybe_start_timer)

        if hasattr(self, 'pro_auto_stretch'):
            try: self.pro_auto_stretch.toggled.disconnect()
            except Exception: pass
            self.pro_auto_stretch.toggled.connect(_maybe_start_timer)
        if hasattr(self, 'pro_equalize'):
            try: self.pro_equalize.toggled.disconnect()
            except Exception: pass
            self.pro_equalize.toggled.connect(_maybe_start_timer)

    def _on_devkit_timer_fire(self):
        if getattr(self, 'devkit_live_cb', None) and self.devkit_live_cb.isChecked():
            self.generate_devkit_composite()

    def _reset_devkit_sliders(self):
        for lst in (getattr(self, 'devkit_min_edits', []),
                    getattr(self, 'devkit_max_edits', [])):
            for e in lst:
                if e: e.setText("0" if lst is self.devkit_min_edits else "100")
        for e in getattr(self, 'devkit_gamma_edits', []):
            if e: e.setText("1.0")
        for cb in getattr(self, 'devkit_invert_checkboxes', []):
            if cb: cb.setChecked(False)
        self.pro_status.setText("Sliders reset.")
        self.generate_devkit_composite()

    def generate_devkit_composite(self):

        if not self.cache.raw:
            self.pro_status.setText("Load a date/scene first -- raw band cache not ready.")
            self.log("Professional: No raw cache yet.")
            return
        if not hasattr(self, 'devkit_band1_combos') or not self.devkit_band1_combos:
            self.pro_status.setText("No channel grid -- switch to non-FG+BG mode first.")
            return

        mode = getattr(self, '_devkit_mode', 'rgb')
        self.pro_status.setText("Compositing...")

        try:

            if mode != "rgb" and mode != "single":
                mode = "rgb"

            n = len(self.devkit_band1_combos)
            channels = []
            stats_src = None

            auto = self.pro_auto_stretch.isChecked() if hasattr(self, 'pro_auto_stretch') else True
            glob_g = 1.0
            try:
                glob_g = float(self.pro_global_gamma.text())
            except Exception:
                pass
            eq = self.pro_equalize.isChecked() if hasattr(self, 'pro_equalize') else False

            for i in range(n):
                b1 = self.devkit_band1_combos[i].currentText()
                b2 = self.devkit_band2_combos[i].currentText() if i < len(self.devkit_band2_combos) else "None"
                if b1 not in self.cache.raw:
                    self.pro_status.setText(f"Waiting for band {b1} in cache...")
                    return
                data = self.cache.raw[b1].astype(np.float32)
                if b2 and b2 != "None" and b2 in self.cache.raw:
                    d2 = self.cache.raw[b2]
                    if d2.shape != data.shape:
                        d2 = zoom(d2.astype(np.float32), (data.shape[0]/d2.shape[0], data.shape[1]/d2.shape[1]), order=1)
                    data = data - d2

                if i == 0:
                    stats_src = data

                mn_str = self.devkit_min_edits[i].text() if i < len(self.devkit_min_edits) else ""
                mx_str = self.devkit_max_edits[i].text() if i < len(self.devkit_max_edits) else ""
                gm_str = self.devkit_gamma_edits[i].text() if i < len(self.devkit_gamma_edits) else "1.0"
                inv = self.devkit_invert_checkboxes[i].isChecked() if i < len(self.devkit_invert_checkboxes) else False

                try:
                    vmin = float(mn_str) if mn_str.strip() else (float(np.nanmin(data)) if auto else 0.0)
                    vmax = float(mx_str) if mx_str.strip() else (float(np.nanmax(data)) if auto else 100.0)
                    gam = float(gm_str) if gm_str.strip() else 1.0
                except Exception:
                    vmin = float(np.nanmin(data))
                    vmax = float(np.nanmax(data))
                    gam = 1.0

                _eng = get_engine(self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9")
                ch_u8 = _eng._linear(data, vmin, vmax, gam * max(glob_g, 0.1), inv)

                if eq:

                    flat = ch_u8.flatten().astype(np.float32)
                    hist, bins = np.histogram(flat, bins=256, range=(0, 256))
                    cdf = hist.cumsum()
                    cdf = (cdf - cdf[0]) / (cdf[-1] - cdf[0] + 1e-9)
                    ch_u8 = np.interp(flat, bins[:-1], cdf * 255).reshape(ch_u8.shape).astype(np.uint8)

                channels.append(ch_u8)

            if mode == "single" and len(channels) == 1:
                channels = channels * 3

            # Align all channels to the largest dimensions before stacking
            if len(channels) >= 2:
                _mh = max(c.shape[0] for c in channels)
                _mw = max(c.shape[1] for c in channels)
                if any(c.shape[:2] != (_mh, _mw) for c in channels):
                    from skimage.transform import resize as _resize
                    channels = [_resize(c.astype(np.float32), (_mh, _mw), order=1, preserve_range=True).clip(0, 255).astype(np.uint8) if c.shape[:2] != (_mh, _mw) else c for c in channels]

            if len(channels) >= 3:
                rgb = np.stack(channels[:3], axis=-1)
            else:
                rgb = np.stack([channels[0]]*3, axis=-1) if channels else np.zeros((64, 64, 3), dtype=np.uint8)

            if stats_src is not None:
                self._update_pro_stats(stats_src, "active channel")

            h, w = rgb.shape[:2]
            qimage = QImage(rgb.tobytes(), w, h, w * 3, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimage)
            self._last_pro_rgb = rgb
            self._last_pro_pixmap = pix
            self.graphics_view.set_image(pix, preserve_view=True, quality_level=1.0, is_original=False)
            self._push_texture_to_multi_globe()

            self.pro_status.setText(f"Applied {mode} -- {h}×{w} preview live.")
            self.log(f"Professional grid applied ({mode} mode).")
            self._update_bev(pix)

        except Exception as ex:
            self.pro_status.setText(f"Error: {str(ex)[:60]}")
            self.log(f"Devkit composite error: {ex}")

    def _update_pro_stats(self, arr: np.ndarray, label: str = ""):

        if not hasattr(self, 'pro_stats_label') or arr is None:
            return
        try:
            valid = arr[~np.isnan(arr)]
            if valid.size == 0:
                txt = "No valid data."
            else:
                txt = (f"{label}  |  Min:{np.nanmin(valid):.2f}  Max:{np.nanmax(valid):.2f}  "
                       f"Mean:{np.nanmean(valid):.2f}  Std:{np.nanstd(valid):.2f}  "
                       f"Shape:{arr.shape[1]}×{arr.shape[0]}")
            self.pro_stats_label.setText(txt)

            if hasattr(self, 'scene_band_info_lbl') and self.scene_band_info_lbl:
                current = self.scene_band_info_lbl.text()
                if "Stats:" not in current:
                    self.scene_band_info_lbl.setText(current + f"\nLive Stats: {txt[:80]}...")
                else:

                    base = current.split("Live Stats:")[0].strip()
                    self.scene_band_info_lbl.setText(f"{base}\nLive Stats: {txt[:90]}")
        except Exception:
            self.pro_stats_label.setText("Stats unavailable for current selection.")

    def _load_professional_preset(self, key: str):
        _sat_prod = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
        _prod_dict = get_products(_sat_prod)
        if key == "-- select RGB product --" or key not in _prod_dict:
            return
        info = _prod_dict[key]
        if not hasattr(self, 'devkit_band1_combos'):
            return

        if info.get("special") == "goes_true_color":
            self.selected_product = key
            self.satellite_controller.generate_rgb_product(key)
            return

        if info.get("single_band"):
            self._switch_devkit_mode("single")
            ch_list = ["Gray"]
        else:
            self._switch_devkit_mode("rgb")
            ch_list = info.get("channels", ["R", "G", "B"])

        formula = info.get("formula", {})
        for i, ch in enumerate(ch_list[:len(self.devkit_band1_combos)]):
            spec = formula.get(ch, formula) if not info.get("single_band") else formula
            band = spec.get("band") or (spec.get("bands", [None])[0] if isinstance(spec.get("bands"), (list, tuple)) else None)
            band2 = None
            if spec.get("operation") == "diff" and isinstance(spec.get("bands"), (list, tuple)) and len(spec["bands"]) > 1:
                band2 = spec["bands"][1]

            if band and i < len(self.devkit_band1_combos):
                idx = self.devkit_band1_combos[i].findText(band)
                if idx >= 0:
                    self.devkit_band1_combos[i].setCurrentIndex(idx)
            if band2 and i < len(self.devkit_band2_combos):
                idx = self.devkit_band2_combos[i].findText(band2)
                if idx >= 0:
                    self.devkit_band2_combos[i].setCurrentIndex(idx)

            if i < len(self.devkit_min_edits):
                self.devkit_min_edits[i].setText(str(spec.get("min", 0)))
                self.devkit_max_edits[i].setText(str(spec.get("max", 100)))
                self.devkit_gamma_edits[i].setText(str(spec.get("gamma", 1.0)))
                if i < len(self.devkit_invert_checkboxes):
                    self.devkit_invert_checkboxes[i].setChecked(bool(spec.get("invert", False)))

        self.devkit_preset_combo.blockSignals(True)
        self.devkit_preset_combo.setCurrentIndex(0)
        self.devkit_preset_combo.blockSignals(False)

        self.pro_status.setText(f"Preset '{info.get('name', key)}' loaded.")
        self.generate_devkit_composite()

    def load_winds_data(self, *args, **kwargs):
        return self.overlay_controller.load_winds_data(*args, **kwargs)
    def _on_winds_loaded(self, *args, **kwargs):
        return self.overlay_controller._on_winds_loaded(*args, **kwargs)
    def _has_wind_data(self, *args, **kwargs):
        return self.overlay_controller._has_wind_data(*args, **kwargs)
    def _update_winds_checkbox_state(self, *args, **kwargs):
        return self.overlay_controller._update_winds_checkbox_state(*args, **kwargs)
    def _toggle_aor_overlay(self, *args, **kwargs):
        return self.overlay_controller._toggle_aor_overlay(*args, **kwargs)
    def _generate_tcad_image(self, *args, **kwargs):
        return self.overlay_controller._generate_tcad_image(*args, **kwargs)
    def _add_custom_aor_point(self, *args, **kwargs):
        return self.forecast_controller._add_custom_aor_point(*args, **kwargs)
    def _clear_custom_aor_points(self, *args, **kwargs):
        return self.forecast_controller._clear_custom_aor_points(*args, **kwargs)
    def _remove_last_custom_aor_point(self, *args, **kwargs):
        return self.forecast_controller._remove_last_custom_aor_point(*args, **kwargs)
    def _remove_selected_custom_aor_point(self, *args, **kwargs):
        return self.forecast_controller._remove_selected_custom_aor_point(*args, **kwargs)
    def _edit_selected_track(self, *args, **kwargs):
        return self.forecast_controller._edit_selected_track(*args, **kwargs)
    def _refresh_custom_aor_points_list(self, *args, **kwargs):
        return self.forecast_controller._refresh_custom_aor_points_list(*args, **kwargs)
    def _create_custom_aor_as_track(self, *args, **kwargs):
        return self.forecast_controller._create_custom_aor_as_track(*args, **kwargs)
    def _toggle_pro_stats_visibility(self, checked: bool):

        if hasattr(self, 'pro_stats_group') and self.pro_stats_group:
            self.pro_stats_group.setVisible(bool(checked))
        self.settings.set("pro_show_detailed_stats", bool(checked))

    def _update_bev(self, source_pixmap=None):
        if not hasattr(self, 'bev_widget') or not self.bev_widget:
            return
        try:
            if source_pixmap is not None and not source_pixmap.isNull():
                thumb = source_pixmap.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                self.bev_widget.setPixmap(thumb)
            elif hasattr(self, 'graphics_view') and self.graphics_view:
                vp_pix = self.graphics_view.grab()
                if vp_pix and not vp_pix.isNull():
                    thumb = vp_pix.scaled(200, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self.bev_widget.setPixmap(thumb)
        except Exception:
            pass

    def _reapply_emphasis(self):
        if not hasattr(self, '_last_pro_pixmap') or self._last_pro_pixmap is None:
            return
        try:
            pix = self._last_pro_pixmap
            qimg = pix.toImage().convertToFormat(QImage.Format_ARGB32)
            arr = self.export_controller._qimage_to_numpy(qimg).astype(np.float32)

            brit = (self.func_gray_brit_sld.value() / 100.0) if hasattr(self, 'func_gray_brit_sld') else 1.0
            cntr = (self.func_gray_cntr_sld.value() / 100.0) if hasattr(self, 'func_gray_cntr_sld') else 1.0
            revs = self.func_gray_revs_cb.isChecked() if hasattr(self, 'func_gray_revs_cb') else False

            luminance = 0.299 * arr[:,:,2] + 0.587 * arr[:,:,1] + 0.114 * arr[:,:,0]
            luminance = (luminance - 128.0) * cntr + 128.0
            luminance = luminance * brit
            if revs:
                luminance = 255.0 - luminance
            luminance = np.clip(luminance, 0, 255).astype(np.uint8)
            arr[:, :, 0] = luminance
            arr[:, :, 1] = luminance
            arr[:, :, 2] = luminance

            out_img = QImage(arr.tobytes(), w, h, QImage.Format_ARGB32)
            out_pix = QPixmap.fromImage(out_img)
            self.graphics_view.set_image(out_pix, preserve_view=True)
        except Exception:
            pass

    _COLORMAP_LIST = [
        "BT Enhanced (IR)", "Sandwich (IR)", "Sandwich IR (SATAID)", "Sandwich (SATAID)", "Dvorak (IR)", "Dvorak Experimental", "gray", "jet", "viridis", "plasma", "inferno", "magma", "coolwarm",
        "rainbow", "Spectral", "RdYlBu", "RdBu", "RdYlGn", "PiYG",
        "PRGn", "BrBG", "PuOr", "turbo", "nipy_spectral", "gist_ncar",
        "gist_rainbow", "gist_earth", "terrain", "ocean", "CMRmap",
        "hot", "cool", "copper", "bone", "pink", "spring", "summer",
        "autumn", "winter", "Wistia", "afmhot",
    ]

    _BAND_CENTRAL_WAVELENGTHS = {
        "B01": 0.47, "B02": 0.51, "B03": 0.64, "B04": 0.86,
        "B05": 1.61, "B06": 2.26, "B07": 3.89, "B08": 6.25,
        "B09": 6.95, "B10": 7.35, "B11": 8.59, "B12": 9.64,
        "B13": 10.41, "B14": 11.24, "B15": 12.38, "B16": 13.30,
    }

    _IR_BANDS = {"B07", "B08", "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"}

    def _radiance_to_bt(self, data: np.ndarray, band: str) -> np.ndarray:
        if band in self._IR_BANDS:
            return data
        c1 = 1.191042e-16
        c2 = 0.01438777
        wl = self._BAND_CENTRAL_WAVELENGTHS.get(band)
        if wl is None:
            return data
        wl_m = wl * 1e-6
        safe = np.where(np.isfinite(data) & (data > 0), data, np.nan)
        with np.errstate(divide='ignore', invalid='ignore'):
            bt = c2 / (wl_m * np.log(c1 / (safe * wl_m**5) + 1))
        bt = np.where((bt < 100) | (bt > 400), np.nan, bt)
        return bt.astype(np.float32)

    def _radiance_to_bt_cached(self, data: np.ndarray, band: str) -> np.ndarray:
        if not hasattr(self, '_bt_cache'):
            self._bt_cache = {}
        got = self._bt_cache.get(band)
        if got is not None and got[0] is data and got[1].shape == data.shape:
            return got[1]
        bt = self._radiance_to_bt(np.asarray(data, dtype=np.float32), band)
        self._bt_cache[band] = (data, bt)
        return bt

    def _cmap_lut(self, name: str):
        if not hasattr(self, '_cmap_lut_cache'):
            self._cmap_lut_cache = {}
        got = self._cmap_lut_cache.get(name)
        if got is not None:
            return got
        n = 2048
        ts = np.linspace(0.0, 1.0, n)
        if name == "BT Enhanced (IR)":
            cmap = self._bt_enhanced_cmap()
        elif name == "Sandwich (IR)":
            cmap = self._sandwich_ir_cmap()
        elif name in ("Sandwich IR (SATAID)", "Sandwich (SATAID)"):
            cmap = self._sandwich_sataid_cmap()
        elif name == "Dvorak (IR)":
            cmap = self._dvorak_cmap()
        elif name == "Dvorak Experimental":
            cmap = self._dvorak_experimental_cmap()
        elif name == "SST (IR)":
            cmap = self._sst_cmap()
        else:
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib as mpl
                if hasattr(mpl.colormaps, 'get'):
                    cmap = mpl.colormaps.get(name)
                else:
                    import matplotlib.cm as cm
                    cmap = cm.get_cmap(name)
                if cmap is None:
                    return None
            except Exception:
                return None
        lut = np.rint(np.asarray(cmap(ts))[:, :3] * 255.0).astype(np.uint8)
        self._cmap_lut_cache[name] = lut
        return lut

    @staticmethod
    def _bt_enhanced_cmap():
        import matplotlib.colors as mcolors
        n = 1501
        colors = np.zeros((n, 3), dtype=np.float64)
        temps = np.linspace(-100, 50, n)
        for i, t in enumerate(temps):
            if t >= -30:
                frac = (t + 30) / 80.0
                c = 0.5 * (1.0 - frac)
                colors[i] = [c, c, c]
            elif t >= -40:
                colors[i] = [14/255.0, 14/255.0, 146/255.0]
            elif t >= -50:
                frac = (t + 50) / 10.0
                colors[i] = [0.0, 1.0 * (1.0 - frac) + 14/255.0 * frac, 1.0 * (1.0 - frac) + 146/255.0 * frac]
            elif t >= -60:
                frac = (t + 60) / 10.0
                if frac < 0.5:
                    f = frac * 2.0
                    colors[i] = [1.0 * (1.0 - f), 1.0, 0.0]
                else:
                    f = (frac - 0.5) * 2.0
                    colors[i] = [0.0, 1.0, f]
            elif t >= -70:
                frac = (t + 70) / 10.0
                colors[i] = [1.0, 0.5 * (1.0 - frac) + 1.0 * frac, 0.0]
            elif t >= -90:
                frac = (t + 90) / 20.0
                if frac < 0.5:
                    f = frac * 2.0
                    colors[i] = [1.0, 0.4 * (1.0 - f), 0.7 * (1.0 - f)]
                else:
                    f = (frac - 0.5) * 2.0
                    colors[i] = [1.0, f * 0.5, 0.0]
            else:
                frac = (t + 100) / 10.0
                colors[i] = [1.0, 1.0 * (1.0 - frac) + 0.4 * frac, 1.0 * (1.0 - frac) + 0.7 * frac]
        return mcolors.ListedColormap(colors, name="bt_enhanced")

    @staticmethod
    def _sandwich_ir_cmap():
        import matplotlib.colors as mcolors
        import numpy as np

        n = 1501
        temps = np.linspace(-100, 50, n)
        colors = np.zeros((n, 3), dtype=np.float64)

        NEW_RED = np.array([251/255.0, 5/255.0, 0.0])
        ORANGE  = np.array([1.0, 0.5, 0.0])
        YELLOW  = np.array([1.0, 1.0, 0.0])
        GREEN   = np.array([0.0, 1.0, 0.0])
        CYAN    = np.array([0.0, 1.0, 1.0])
        DARK_BLUE = np.array([14/255.0, 14/255.0, 146/255.0])
        GREY    = np.array([0.5, 0.5, 0.5])
        BLACK   = np.array([0.0, 0.0, 0.0])

        for i, t in enumerate(temps):
            if t <= -72:
                colors[i] = NEW_RED
            elif t < -65:                     # -72 → -65 (length 7)
                frac = (t + 72) / 7.0
                colors[i] = NEW_RED * (1 - frac) + ORANGE * frac
            elif t < -58:                     # -65 → -58 (length 7)
                frac = (t + 65) / 7.0
                colors[i] = ORANGE * (1 - frac) + YELLOW * frac
            elif t < -52:                     # -58 → -52 (length 6)
                frac = (t + 58) / 6.0
                colors[i] = GREEN * (1 - frac) + CYAN * frac
            elif t < -32:                     # -52 → -32 (length 20)
                frac = (t + 52) / 20.0
                colors[i] = CYAN * (1 - frac) + DARK_BLUE * frac
            elif t < -25:                     # -32 → -25 flat dark blue
                colors[i] = DARK_BLUE
            else:                             # -25 → 50 (length 75)
                frac = (t + 25) / 75.0
                colors[i] = GREY * (1 - frac) + BLACK * frac

        return mcolors.ListedColormap(colors, name="sandwich_ir")

    @staticmethod
    def _sandwich_sataid_lut():
        """Build the 256-entry Sandwich.dat IR-temperature LUT (index 0 = -73.15 C, 255 = -33.15 C)."""
        import numpy as np
        lut = np.zeros((256, 3), dtype=np.float64)
        DARK_BLUE = np.array([0, 0, 131], dtype=np.float64)
        BLUE      = np.array([0, 0, 255], dtype=np.float64)
        CYAN      = np.array([0, 255, 255], dtype=np.float64)
        GREEN     = np.array([0, 255, 0], dtype=np.float64)
        YELLOW    = np.array([255, 255, 0], dtype=np.float64)
        RED       = np.array([255, 0, 0], dtype=np.float64)
        DARK_RED  = np.array([131, 0, 0], dtype=np.float64)

        def _lerp(a, b, t):
            return a * (1.0 - t) + b * t

        for i in range(0, 34):                          # 0-33 dark blue -> blue
            lut[i] = _lerp(DARK_BLUE, BLUE, i / 33.0)
        for i in range(34, 99):                         # 34-98 blue -> cyan
            lut[i] = _lerp(BLUE, CYAN, (i - 34) / (98 - 34))
        for i in range(99, 163):                        # 99-162 cyan -> green -> yellow
            if i <= 130:
                lut[i] = _lerp(CYAN, GREEN, (i - 99) / (130 - 99))
            else:
                lut[i] = _lerp(GREEN, YELLOW, (i - 131) / (162 - 131))
        for i in range(163, 226):                       # 163-225 yellow -> red
            lut[i] = _lerp(YELLOW, RED, (i - 163) / (225 - 163))
        for i in range(226, 256):                       # 226-255 red -> dark red
            lut[i] = _lerp(RED, DARK_RED, (i - 226) / (255 - 226))
        # Invert the cold/warm color order: index 0 (-73.15 C / coldest tops)
        # now maps to dark red and index 255 (-33.15 C) to dark blue. The
        # grey-to-black ramp warmer than -33.15 C is appended separately below
        # and is intentionally NOT reversed.
        lut = lut[::-1].copy()
        return np.clip(lut, 0.0, 255.0) / 255.0

    @staticmethod
    def _sandwich_sataid_cmap():
        """SATAID sandwich colormap: Sandwich.dat 256-entry LUT plus grayscale ramp.

        LUT covers -73.15 C (dark blue) to -33.15 C (dark red); warmer than -33.15 C
        fades white (-33.14 C) to black (70 C). Evaluated over the 173..343 K window
        used by the SATAID color-scale products.
        """
        import matplotlib.colors as mcolors
        import numpy as np
        n = 1501
        temps = np.linspace(-100.15, 70.0, n)           # 173..343.15 K
        lut = MainUI._sandwich_sataid_lut()
        colors = np.zeros((n, 3), dtype=np.float64)
        LUT_MIN = -73.15
        LUT_MAX = -33.15
        GRAY_MAX = 70.0
        for i, t in enumerate(temps):
            if t <= LUT_MIN:
                colors[i] = lut[0]
            elif t <= LUT_MAX:
                f = (t - LUT_MIN) / (LUT_MAX - LUT_MIN) * 255.0
                idx = int(np.floor(f))
                idx = min(max(idx, 0), 254)
                frac = f - idx
                colors[i] = lut[idx] * (1.0 - frac) + lut[idx + 1] * frac
            else:
                g = 1.0 - (t - LUT_MAX) / (GRAY_MAX - LUT_MAX)
                g = min(max(g, 0.0), 1.0)
                colors[i] = [g, g, g]
        return mcolors.ListedColormap(colors, name="sandwich_sataid")

    @staticmethod
    def _dvorak_cmap():
        import matplotlib.colors as mcolors
        n = 1501
        colors = np.zeros((n, 3), dtype=np.float64)
        temps = np.linspace(-100, 50, n)
        for i, t in enumerate(temps):
            if t > 9:
                colors[i] = [0.45, 0.45, 0.45]
            elif t > -30:
                colors[i] = [0.90, 0.90, 0.90]
            elif t > -41:
                colors[i] = [0.20, 0.20, 0.20]
            elif t > -53:
                colors[i] = [0.50, 0.50, 0.50]
            elif t > -63:
                colors[i] = [0.75, 0.75, 0.75]
            elif t > -69:
                colors[i] = [0.0, 0.0, 0.0]
            elif t > -75:
                colors[i] = [1.0, 1.0, 1.0]
            elif t > -81:
                colors[i] = [0.50, 0.50, 0.50]
            else:
                colors[i] = [0.20, 0.20, 0.20]
        return mcolors.ListedColormap(colors, name="dvorak")

    @staticmethod
    def _dvorak_experimental_cmap():
        import matplotlib.colors as mcolors
        vmin, vmax = 173.15, 323.15
        dr = vmax - vmin
        nodes = [
            (0.0, "#585858"),
            ((193.15 - vmin) / dr, "#585858"),
            ((193.16 - vmin) / dr, "#888888"),
            ((198.15 - vmin) / dr, "#888888"),
            ((198.16 - vmin) / dr, "#FFFFFF"),
            ((204.15 - vmin) / dr, "#FFFFFF"),
            ((204.16 - vmin) / dr, "#000000"),
            ((210.15 - vmin) / dr, "#000000"),
            ((210.16 - vmin) / dr, "#A0A0A0"),
            ((220.15 - vmin) / dr, "#A0A0A0"),
            ((220.16 - vmin) / dr, "#707070"),
            ((232.15 - vmin) / dr, "#707070"),
            ((232.16 - vmin) / dr, "#404040"),
            ((243.15 - vmin) / dr, "#404040"),
            ((243.16 - vmin) / dr, "#D2D2D2"),
            ((282.15 - vmin) / dr, "#3A3A3A"),
            ((282.16 - vmin) / dr, "#FAFAFA"),
            ((299.15 - vmin) / dr, "#2A2A2A"),
            (1.0, "#000000"),
        ]
        return mcolors.LinearSegmentedColormap.from_list("dvorak_experimental", nodes, N=1501)

    @staticmethod
    def _sst_cmap():
        import matplotlib.colors as mcolors
        import numpy as np
        n = 401
        temps = np.linspace(0, 40, n)
        colors = np.zeros((n, 3), dtype=np.float64)
        YELLOW = np.array([1.0, 1.0, 0.0])
        RED = np.array([1.0, 0.0, 0.0])
        for i, t in enumerate(temps):
            frac = t / 40.0
            colors[i] = YELLOW * (1.0 - frac) + RED * frac
        return mcolors.ListedColormap(colors, name="sst")

    def _get_active_raw_band(self, prefer_ir=False, forced_band: str = None):
        cache = getattr(self, 'cache', None)
        if cache is None:
            return None, None

        def _obtain(band):
            if band in cache.raw:
                return cache.raw[band]
            sc = getattr(self, 'satellite_controller', None)
            if sc is not None and hasattr(sc, '_pull_band_from_anim_cache'):
                try:
                    nc = sc._current_nc_file()
                except Exception:
                    nc = None
                if nc is not None:
                    ba = sc._pull_band_from_anim_cache(band, nc)
                    if ba is not None:
                        try:
                            cache.put_raw(band, ba)
                        except Exception:
                            pass
                        return cache.raw.get(band)
            return None

        if forced_band:
            data = _obtain(forced_band)
            if data is not None:
                return forced_band, data
            return None, None
        if prefer_ir:
            for band in list(cache.raw.keys()):
                if band in self._IR_BANDS:
                    return band, cache.raw[band]
            data = _obtain("B13")
            if data is not None:
                return "B13", data
        if hasattr(self, 'devkit_band1_combos') and self.devkit_band1_combos:
            band = self.devkit_band1_combos[0].currentText()
            if band and band != "None":
                data = _obtain(band)
                if data is not None:
                    return band, data
        for band in list(cache.raw.keys()):
            return band, cache.raw[band]
        return None, None

    def _open_color_scale_dialog(self):
        init_band, init_data = self._get_active_raw_band()
        if init_data is None:
            QMessageBox.information(self, "Color Scale", "No band data loaded. Load a scene first.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Color Scale")
        dlg.setMinimumWidth(420)
        dlg.setStyleSheet("""
            QDialog { background: #1E1E1E; color: #DDD; }
            QLabel { color: #B0BEC5; font-size: 9pt; }
            QGroupBox { color: #B0BEC5; font-weight: bold; border: 1px solid #444;
                border-radius: 4px; margin-top: 6px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; font-size: 9px; }
            QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {
                background: #2A2A2A; color: #EEE; border: 1px solid #555;
                border-radius: 3px; padding: 2px 4px; font-size: 9pt;
            }
            QRadioButton { color: #B0BEC5; font-size: 9pt; spacing: 6px; }
            QPushButton { background: #333; color: #DDD; border: 1px solid #555;
                border-radius: 3px; padding: 4px 14px; font-size: 9pt; }
            QPushButton:hover { background: #444; }
        """)

        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        self._cs_band_info = QLabel()
        self._cs_band_info.setStyleSheet("color: #DDD; font-size: 9pt; padding: 4px;")
        layout.addWidget(self._cs_band_info)

        def _band_range(band_name, band_data):
            if band_data is not None and np.any(np.isfinite(band_data)):
                return float(np.nanmin(band_data)), float(np.nanmax(band_data))
            return 0.0, 1.0

        def _refresh_band_info():
            use_bt = self._cs_bt_rb.isChecked()
            forced = None
            if hasattr(self, '_cs_cmap_combo'):
                cmap_name = self._cs_cmap_combo.currentText()
                if cmap_name in ("BT Enhanced (IR)", "Sandwich (IR)", "Sandwich IR (SATAID)", "Sandwich (SATAID)", "Dvorak (IR)", "Dvorak Experimental", "SST (IR)"):
                    forced = "B13"
            b, d = self._get_active_raw_band(prefer_ir=use_bt, forced_band=forced)
            if d is not None:
                rlo, rhi = _band_range(b, d)
                self._cs_band_info.setText(f"Band: <b>{b}</b>  |  Data range: {rlo:.2f} - {rhi:.2f}")
            else:
                self._cs_band_info.setText("Band: <b>--</b>  |  No data")

        init_lo, init_hi = _band_range(init_band, init_data)
        self._cs_band_info.setText(f"Band: <b>{init_band}</b>  |  Data range: {init_lo:.2f} - {init_hi:.2f}")

        mode_group = QGroupBox("Mode")
        mode_layout = QHBoxLayout(mode_group)
        self._cs_rad_rb = QRadioButton("Radiance + Colormap")
        self._cs_bt_rb = QRadioButton("Brightness Temperature")
        self._cs_rad_rb.setChecked(True)
        mode_layout.addWidget(self._cs_rad_rb)
        mode_layout.addWidget(self._cs_bt_rb)
        mode_layout.addStretch()
        layout.addWidget(mode_group)

        def _on_mode_changed():
            _refresh_band_info()
            if self._cs_bt_rb.isChecked():
                idx = self._cs_cmap_combo.findText("BT Enhanced (IR)")
                if idx >= 0:
                    self._cs_cmap_combo.setCurrentIndex(idx)
                self._cs_auto_cb.setChecked(False)
                self._cs_min_edit.setText("173.0")
                self._cs_max_edit.setText("323.0")
        self._cs_rad_rb.toggled.connect(_on_mode_changed)
        self._cs_bt_rb.toggled.connect(_on_mode_changed)

        cmap_group = QGroupBox("Colormap")
        cmap_layout = QVBoxLayout(cmap_group)
        cmap_row = QHBoxLayout()
        cmap_row.addWidget(QLabel("Colormap:"))
        self._cs_cmap_combo = QComboBox()
        self._cs_cmap_combo.addItems(self._COLORMAP_LIST)
        self._cs_cmap_combo.setCurrentText("jet")
        cmap_row.addWidget(self._cs_cmap_combo, 1)
        self._cs_invert_cb = QCheckBox("Invert")
        self._cs_invert_cb.setStyleSheet("color: #B0BEC5;")
        cmap_row.addWidget(self._cs_invert_cb)
        self._cs_show_cbar_cb = QCheckBox("On-image bar")
        self._cs_show_cbar_cb.setStyleSheet("color: #B0BEC5;")
        self._cs_show_cbar_cb.setChecked(self.settings.get("color_scale_show_bar", False) if hasattr(self, 'settings') else False)
        cmap_row.addWidget(self._cs_show_cbar_cb)
        cmap_layout.addLayout(cmap_row)

        self._cs_colorbar_lbl = QLabel()
        self._cs_colorbar_lbl.setFixedHeight(32)
        self._cs_colorbar_lbl.setAlignment(Qt.AlignCenter)
        self._cs_colorbar_lbl.setStyleSheet("background: #111; border: 1px solid #444; border-radius: 2px;")
        cmap_layout.addWidget(self._cs_colorbar_lbl)

        self._cs_cbar_min = QLabel(f"{init_lo:.2f}")
        self._cs_cbar_min.setStyleSheet("color: #888; font-size: 7pt;")
        self._cs_cbar_max = QLabel(f"{init_hi:.2f}")
        self._cs_cbar_max.setStyleSheet("color: #888; font-size: 7pt;")
        cbar_label_row = QHBoxLayout()
        cbar_label_row.addWidget(self._cs_cbar_min)
        cbar_label_row.addStretch()
        cbar_label_row.addWidget(self._cs_cbar_max)
        cmap_layout.addLayout(cbar_label_row)

        layout.addWidget(cmap_group)

        range_group = QGroupBox("Data Range")
        range_layout = QGridLayout(range_group)
        range_layout.setSpacing(4)
        range_layout.addWidget(QLabel("Min:"), 0, 0)
        self._cs_min_edit = QLineEdit(f"{init_lo:.4f}")
        range_layout.addWidget(self._cs_min_edit, 0, 1)
        range_layout.addWidget(QLabel("Max:"), 0, 2)
        self._cs_max_edit = QLineEdit(f"{init_hi:.4f}")
        range_layout.addWidget(self._cs_max_edit, 0, 3)
        self._cs_auto_cb = QCheckBox("Auto range from data")
        self._cs_auto_cb.setChecked(True)
        self._cs_auto_cb.setStyleSheet("color: #B0BEC5;")
        range_layout.addWidget(self._cs_auto_cb, 0, 4)
        range_layout.addWidget(QLabel("Gamma:"), 1, 0)
        self._cs_gamma_edit = QLineEdit("1.0")
        self._cs_gamma_edit.setFixedWidth(60)
        range_layout.addWidget(self._cs_gamma_edit, 1, 1)
        layout.addWidget(range_group)

        def _on_auto_toggled(auto):
            self._cs_min_edit.setEnabled(not auto)
            self._cs_max_edit.setEnabled(not auto)
        self._cs_auto_cb.toggled.connect(_on_auto_toggled)

        def _update_colorbar_preview():
            try:
                cmap_name = self._cs_cmap_combo.currentText()
                if cmap_name == "BT Enhanced (IR)":
                    cmap = self._bt_enhanced_cmap()
                elif cmap_name == "Sandwich (IR)":
                    cmap = self._sandwich_ir_cmap()
                elif cmap_name in ("Sandwich IR (SATAID)", "Sandwich (SATAID)"):
                    cmap = self._sandwich_sataid_cmap()
                elif cmap_name == "Dvorak (IR)":
                    cmap = self._dvorak_cmap()
                elif cmap_name == "Dvorak Experimental":
                    cmap = self._dvorak_experimental_cmap()
                else:
                    import matplotlib
                    matplotlib.use("Agg")
                    import matplotlib as mpl
                    if hasattr(mpl.colormaps, 'get'):
                        cmap = mpl.colormaps.get(cmap_name)
                    else:
                        import matplotlib.cm as cm
                        cmap = cm.get_cmap(cmap_name)
                if cmap is None:
                    return
                cw, ch = 300, 28
                grad = np.linspace(0, 1, cw).astype(np.float32)
                cbar_rgb = cmap(grad)[:, :3]
                if self._cs_invert_cb.isChecked():
                    cbar_rgb = 1.0 - cbar_rgb
                cbar_img = (cbar_rgb * 255).astype(np.uint8)
                cbar_img = np.tile(cbar_img[np.newaxis, :, :], (ch, 1, 1))
                qimg = QImage(cbar_img.tobytes(), cw, ch, cw * 3, QImage.Format_RGB888)
                self._cs_colorbar_lbl.setPixmap(QPixmap.fromImage(qimg))
            except Exception:
                pass
            try:
                mn = float(self._cs_min_edit.text()) if self._cs_min_edit.text().strip() else init_lo
                mx = float(self._cs_max_edit.text()) if self._cs_max_edit.text().strip() else init_hi
            except Exception:
                mn, mx = init_lo, init_hi
            self._cs_cbar_min.setText(f"{mn:.2f}")
            self._cs_cbar_max.setText(f"{mx:.2f}")

        self._cs_cmap_combo.currentTextChanged.connect(_update_colorbar_preview)
        self._cs_invert_cb.toggled.connect(_update_colorbar_preview)
        self._cs_min_edit.textChanged.connect(_update_colorbar_preview)
        self._cs_max_edit.textChanged.connect(_update_colorbar_preview)
        _update_colorbar_preview()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        apply_btn = QPushButton("Apply")
        apply_btn.setStyleSheet("QPushButton { background: #2E7D32; color: white; font-weight: bold; padding: 6px 24px; } QPushButton:hover { background: #388E3C; }")
        cancel_btn = QPushButton("Cancel")
        btn_row.addWidget(apply_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        apply_btn.clicked.connect(lambda: self._apply_color_scale(dlg))
        cancel_btn.clicked.connect(dlg.reject)

        dlg.exec()

    def _draw_colorbar_on_pixmap(self, pix: QPixmap, cmap_name: str, invert: bool, vmin: float, vmax: float) -> QPixmap:
        if cmap_name == "BT Enhanced (IR)":
            cmap = self._bt_enhanced_cmap()
        elif cmap_name == "Sandwich (IR)":
            cmap = self._sandwich_ir_cmap()
        elif cmap_name in ("Sandwich IR (SATAID)", "Sandwich (SATAID)"):
            cmap = self._sandwich_sataid_cmap()
        elif cmap_name == "Dvorak (IR)":
            cmap = self._dvorak_cmap()
        elif cmap_name == "Dvorak Experimental":
            cmap = self._dvorak_experimental_cmap()
        else:
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib as mpl
                if hasattr(mpl.colormaps, 'get'):
                    cmap = mpl.colormaps.get(cmap_name)
                else:
                    import matplotlib.cm as cm
                    cmap = cm.get_cmap(cmap_name)
                if cmap is None:
                    return pix
            except Exception:
                return pix

        pw, ph = pix.width(), pix.height()
        bar_w = int(pw * 0.25)
        bar_h = 18
        margin = 12
        bar_x = pw - bar_w - margin
        bar_y = ph - bar_h - margin

        grad = np.linspace(0, 1, bar_w).astype(np.float32)
        cbar_rgb = cmap(grad)[:, :3]
        if invert:
            cbar_rgb = 1.0 - cbar_rgb
        cbar_u8 = (cbar_rgb * 255).astype(np.uint8)
        cbar_strip = np.tile(cbar_u8[np.newaxis, :, :], (bar_h, 1, 1))
        cbar_qimg = QImage(cbar_strip.tobytes(), bar_w, bar_h, bar_w * 3, QImage.Format_RGB888)

        result = QPixmap(pix)
        p = QPainter(result)
        p.drawImage(bar_x, bar_y, cbar_qimg)
        p.setPen(QPen(QColor(60, 60, 60), 1))
        p.drawRect(bar_x, bar_y, bar_w, bar_h)
        font = QFont("Consolas", 8)
        p.setFont(font)
        p.setPen(QColor(220, 220, 220))
        lbl_min = f"{vmin:.1f}"
        lbl_max = f"{vmax:.1f}"
        p.drawText(bar_x, bar_y - 2, lbl_min)
        p.drawText(bar_x + bar_w - p.fontMetrics().horizontalAdvance(lbl_max), bar_y - 2, lbl_max)
        p.end()
        return result

    def _apply_color_scale(self, dlg):
        use_bt = self._cs_bt_rb.isChecked()
        cmap_name = self._cs_cmap_combo.currentText()
        invert = self._cs_invert_cb.isChecked()
        auto_range = self._cs_auto_cb.isChecked()
        show_bar = self._cs_show_cbar_cb.isChecked()
        try:
            gamma = float(self._cs_gamma_edit.text())
        except Exception:
            gamma = 1.0

        forced = "B13" if cmap_name in ("BT Enhanced (IR)", "Sandwich (IR)", "Sandwich IR (SATAID)", "Sandwich (SATAID)", "Dvorak (IR)", "Dvorak Experimental", "SST (IR)") else None
        band, data = self._get_active_raw_band(prefer_ir=use_bt, forced_band=forced)
        if data is None:
            QMessageBox.warning(dlg, "Error", "No band data available.")
            return

        if use_bt:
            arr = np.asarray(self._radiance_to_bt_cached(data, band), dtype=np.float32)
        else:
            arr = np.asarray(data, dtype=np.float32)

        valid = arr[np.isfinite(arr)]
        if len(valid) == 0:
            QMessageBox.warning(dlg, "Error", "No valid data after conversion.")
            return

        if auto_range:
            vmin, vmax = float(valid.min()), float(valid.max())
        else:
            try:
                vmin = float(self._cs_min_edit.text())
                vmax = float(self._cs_max_edit.text())
            except Exception:
                vmin, vmax = float(valid.min()), float(valid.max())
        if cmap_name in ("Sandwich IR (SATAID)", "Sandwich (SATAID)"):
            vmin, vmax = 173.0, 343.15
        elif cmap_name in ("BT Enhanced (IR)", "Sandwich (IR)", "Dvorak (IR)", "Dvorak Experimental"):
            vmin, vmax = 173.0, 323.0
        if vmax <= vmin:
            vmax = vmin + 1e-6

        norm = np.clip((arr - vmin) / (vmax - vmin), 0.0, 1.0)
        if gamma != 1.0 and gamma > 0:
            norm = np.power(norm, 1.0 / gamma)

        if cmap_name == "BT Enhanced (IR)":
            cmap = self._bt_enhanced_cmap()
        elif cmap_name == "Sandwich (IR)":
            cmap = self._sandwich_ir_cmap()
        elif cmap_name in ("Sandwich IR (SATAID)", "Sandwich (SATAID)"):
            cmap = self._sandwich_sataid_cmap()
        elif cmap_name == "Dvorak (IR)":
            cmap = self._dvorak_cmap()
        elif cmap_name == "Dvorak Experimental":
            cmap = self._dvorak_experimental_cmap()
        else:
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib as mpl
                if hasattr(mpl.colormaps, 'get'):
                    cmap = mpl.colormaps.get(cmap_name)
                else:
                    import matplotlib.cm as cm
                    cmap = cm.get_cmap(cmap_name)
                if cmap is None:
                    self.pro_status.setText(f"Colormap '{cmap_name}' not found.")
                    return
            except Exception:
                self.pro_status.setText(f"Colormap '{cmap_name}' not available.")
                return

        lut = self._cmap_lut(cmap_name)
        idx = np.clip(np.floor(norm * (lut.shape[0] - 1)).astype(np.int32), 0, lut.shape[0] - 1)
        rgb_u8 = lut[idx]
        if invert:
            rgb_u8 = 255 - rgb_u8

        alpha = np.where(np.isnan(arr), 0, 255).astype(np.uint8)
        h, w = rgb_u8.shape[:2]
        rgba = np.concatenate([rgb_u8, alpha[:, :, np.newaxis]], axis=-1)

        raw_bytes = rgba.tobytes()
        qimg = QImage(raw_bytes, w, h, w * 4, QImage.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg)

        pix = self.overlay_controller._resize_for_fldk(pix, sector=self._detect_current_sector())
        scene_pos = self.overlay_controller._get_image_scene_pos()

        if show_bar:
            pix = self._draw_colorbar_on_pixmap(pix, cmap_name, invert, vmin, vmax)

        self.settings.set("color_scale_show_bar", show_bar)
        self._last_pro_pixmap = pix
        self.graphics_view.set_image(pix, preserve_view=True, quality_level=1.0, is_original=False, scene_pos=scene_pos)
        self._update_bev(pix)

        mode_str = "BT" if use_bt else "Radiance"
        self.pro_status.setText(f"Color scale: {cmap_name} ({mode_str}) applied to {band}.")
        dlg.accept()

    def _apply_color_scale_product(self, key: str, info: dict):
        cs = info.get("color_scale", {})
        cmap_name = cs.get("colormap", "jet")
        use_bt = cs.get("mode", "rad") == "bt"
        gamma = cs.get("gamma", 1.0)
        vmin = cs.get("vmin")
        vmax = cs.get("vmax")

        forced = "B13" if cmap_name in ("BT Enhanced (IR)", "Sandwich (IR)", "Sandwich IR (SATAID)", "Sandwich (SATAID)", "Dvorak (IR)", "Dvorak Experimental", "SST (IR)") else None
        band, data = self._get_active_raw_band(prefer_ir=use_bt, forced_band=forced)
        if data is None:
            self.log(f"Color scale '{cmap_name}': no band data available.")
            return

        if use_bt:
            arr = np.asarray(self._radiance_to_bt_cached(data, band), dtype=np.float32)
        else:
            arr = np.asarray(data, dtype=np.float32)

        valid = arr[np.isfinite(arr)]
        if len(valid) == 0:
            self.log(f"Color scale '{cmap_name}': no valid data.")
            return

        if vmin is None or vmax is None:
            lo, hi = float(valid.min()), float(valid.max())
        else:
            lo, hi = float(vmin), float(vmax)
        if hi <= lo:
            hi = lo + 1e-6

        norm = np.clip((arr - lo) / (hi - lo), 0.0, 1.0)
        if gamma != 1.0 and gamma > 0:
            norm = np.power(norm, 1.0 / gamma)

        if cmap_name == "BT Enhanced (IR)":
            cmap = self._bt_enhanced_cmap()
        elif cmap_name == "Sandwich (IR)":
            cmap = self._sandwich_ir_cmap()
        elif cmap_name in ("Sandwich IR (SATAID)", "Sandwich (SATAID)"):
            cmap = self._sandwich_sataid_cmap()
        elif cmap_name == "Dvorak (IR)":
            cmap = self._dvorak_cmap()
        elif cmap_name == "Dvorak Experimental":
            cmap = self._dvorak_experimental_cmap()
        elif cmap_name == "SST (IR)":
            cmap = self._sst_cmap()
            alpha = np.where((arr < vmin) | (arr > vmax) | (~np.isfinite(arr)), 0, 255).astype(np.uint8)
            lut = self._cmap_lut(cmap_name)
            idx = np.clip(np.floor(norm * (lut.shape[0] - 1)).astype(np.int32), 0, lut.shape[0] - 1)
            rgb_u8 = lut[idx]
            h, w = rgb_u8.shape[:2]
            rgba = np.concatenate([rgb_u8, alpha[:, :, np.newaxis]], axis=-1)
            raw_bytes = rgba.tobytes()
            qimg = QImage(raw_bytes, w, h, w * 4, QImage.Format_RGBA8888)
            sst_pix = QPixmap.fromImage(qimg)
            sst_pix = self.overlay_controller._resize_for_fldk(sst_pix, sector=self._detect_current_sector())
            scene_pos = self.overlay_controller._get_image_scene_pos()
            current_pix = self._get_current_scene_pixmap()
            if current_pix is not None and not current_pix.isNull():
                composited = self._composite_overlay(current_pix, sst_pix)
                self.graphics_view.set_image(composited, preserve_view=True, quality_level=1.0, is_original=False, scene_pos=scene_pos)
                self._update_bev(composited)
            else:
                self.graphics_view.set_image(sst_pix, preserve_view=True, quality_level=1.0, is_original=False, scene_pos=scene_pos)
                self._update_bev(sst_pix)
            name = info.get("name", cmap_name)
            self.log(f"Color scale product '{name}' applied to {band}.")
            return
        else:
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib as mpl
                if hasattr(mpl.colormaps, 'get'):
                    cmap = mpl.colormaps.get(cmap_name)
                else:
                    import matplotlib.cm as cm
                    cmap = cm.get_cmap(cmap_name)
                if cmap is None:
                    self.log(f"Colormap '{cmap_name}' not found.")
                    return
            except Exception:
                self.log(f"Colormap '{cmap_name}' not available.")
                return

        lut = self._cmap_lut(cmap_name)
        norm_clean = np.nan_to_num(norm, nan=0.0)
        idx = np.clip(np.floor(norm_clean * (lut.shape[0] - 1)).astype(np.int32), 0, lut.shape[0] - 1)
        rgb_u8 = lut[idx]

        alpha = np.where(np.isnan(arr), 0, 255).astype(np.uint8)
        h, w = rgb_u8.shape[:2]
        rgba = np.concatenate([rgb_u8, alpha[:, :, np.newaxis]], axis=-1)

        raw_bytes = rgba.tobytes()
        qimg = QImage(raw_bytes, w, h, w * 4, QImage.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg)

        pix = self.overlay_controller._resize_for_fldk(pix, sector=self._detect_current_sector())
        scene_pos = self.overlay_controller._get_image_scene_pos()

        self.graphics_view.set_image(pix, preserve_view=True, quality_level=1.0, is_original=False, scene_pos=scene_pos)
        self._update_bev(pix)

        name = info.get("name", cmap_name)
        self.log(f"Color scale product '{name}' applied to {band}.")

    def _reset_emphasis(self):
        if hasattr(self, 'func_gray_brit_sld'):
            self.func_gray_brit_sld.setValue(100)
        if hasattr(self, 'func_gray_cntr_sld'):
            self.func_gray_cntr_sld.setValue(100)
        if hasattr(self, 'func_gray_revs_cb'):
            self.func_gray_revs_cb.setChecked(False)

    def _get_current_scene_pixmap(self):
        from PySide6.QtWidgets import QGraphicsPixmapItem
        scene = self.graphics_view.scene()
        if not scene:
            return None
        for item in scene.items():
            if isinstance(item, QGraphicsPixmapItem) and item.zValue() == 0:
                return item.pixmap()
        return None

    def _composite_overlay(self, base_pix, overlay_pix):
        from PySide6.QtGui import QPainter, QPixmap
        result = QPixmap(base_pix.size())
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(0, 0, base_pix)
        painter.drawPixmap(0, 0, overlay_pix)
        painter.end()
        return result

    def _set_emphasis_mode(self, mode: str):
        self._current_emphasis_mode = mode
        self.pro_status.setText(f"Emphasis mode: {mode}")
        self._reapply_emphasis()

    def _set_func_mode(self, mode: str):
        self._current_func_mode = mode
        self.pro_status.setText(f"Function: {mode}")

    def _show_tracks_context_menu(self, pos):
        """Show context menu for track list items."""
        item = self.tracks_list.itemAt(pos)
        if not item:
            return
        if item.parent():
            item = item.parent()
        
        track_id = item.data(0, Qt.UserRole)
        menu = QMenu(self)
        
        menu.addAction("Show in Info Box", lambda: self._set_track_infobox(track_id))
        menu.addSeparator()
        menu.addAction("Edit", lambda: self._edit_track_from_context(track_id))
        menu.addAction("Delete", lambda: self._delete_track_from_context(track_id))
        
        menu.exec_(self.tracks_list.mapToGlobal(pos))
    
    def _set_track_infobox(self, track_id):
        """Set a specific track to display in the info box."""
        self._viewport_infobox_track_id = track_id
        self._refresh_info_box_if_visible()
        self.log(f"Track shown in info box")
    
    def _toggle_track_in_infobox(self):
        """Toggle the selected track in the info box."""
        cur = self.tracks_list.currentItem()
        if not cur:
            return
        if cur.parent():
            cur = cur.parent()
        track_id = cur.data(0, Qt.UserRole)
        self._set_track_infobox(track_id)
    
    def _on_track_selection_changed(self):
        pass
    
    def _on_info_box_position_changed(self, x, y):
        """Handle info box position change and save to settings."""
        self.settings["track_bulletin_x"] = x
        self.settings["track_bulletin_y"] = y
        self.settings.save()
    
    def _edit_track_from_context(self, track_id):
        """Edit a track from context menu."""
        for i in range(self.tracks_list.topLevelItemCount()):
            item = self.tracks_list.topLevelItem(i)
            if item.data(0, Qt.UserRole) == track_id:
                self.tracks_list.setCurrentItem(item)
                self.forecast_controller._edit_selected_track()
                break
    
    def _delete_track_from_context(self, track_id):
        """Delete a track from context menu."""
        for i in range(self.tracks_list.topLevelItemCount()):
            item = self.tracks_list.topLevelItem(i)
            if item.data(0, Qt.UserRole) == track_id:
                self.tracks_list.setCurrentItem(item)
                self.forecast_controller._delete_selected_track()
                break

    def _create_tracks_tab(self, *args, **kwargs):
        return self.forecast_controller._create_tracks_tab(*args, **kwargs)
    def _populate_anim_geo_sector_combo(self):
        if not hasattr(self, '_anim_geo_sector_combo'):
            return
        sat = self.sat_combo.currentText().lower() if hasattr(self, 'sat_combo') else "himawari9"
        if hasattr(self, 'anim_sat_combo') and self.anim_sat_combo.currentText():
            sat = self.anim_sat_combo.currentText().lower()
        type_data = ""
        if hasattr(self, 'anim_type_combo') and self.anim_type_combo.currentData():
            type_data = str(self.anim_type_combo.currentData()).strip().lower()
        is_fullgeo = type_data == "fullgeo"
        if hasattr(self, 'geo_inset_label'):
            self.geo_inset_label.setText("Full GEO insert:" if is_fullgeo else "Geo Target insert:")
        saved = self.settings.get("geotarget_sectors", "Both")
        self._anim_geo_sector_combo.blockSignals(True)
        self._anim_geo_sector_combo.clear()
        if is_fullgeo:
            if "himawari" in sat:
                self._anim_geo_sector_combo.addItem("All", "All")
                self._anim_geo_sector_combo.addItem("Target Area", "Target")
                self._anim_geo_sector_combo.addItem("Japan", "Japan")
            else:
                self._anim_geo_sector_combo.addItem("All", "All")
                self._anim_geo_sector_combo.addItem("CONUS", "C")
                self._anim_geo_sector_combo.addItem("Meso 1 & 2", "Both")
                self._anim_geo_sector_combo.addItem("Meso 1", "M1")
                self._anim_geo_sector_combo.addItem("Meso 2", "M2")
        else:
            if "himawari" in sat:
                self._anim_geo_sector_combo.addItem("Target", "Target")
            else:
                self._anim_geo_sector_combo.addItem("Meso 1 & 2", "Both")
                self._anim_geo_sector_combo.addItem("Meso 1", "M1")
                self._anim_geo_sector_combo.addItem("Meso 2", "M2")
        if is_fullgeo:
            idx = self._anim_geo_sector_combo.findData("All")
        else:
            idx = self._anim_geo_sector_combo.findData(saved)
        if idx < 0:
            idx = 0
        if idx >= 0:
            self._anim_geo_sector_combo.setCurrentIndex(idx)
        self._anim_geo_sector_combo.setEnabled(True)
        self._anim_geo_sector_combo.blockSignals(False)

    def _set_anim_geo_controls_visible(self, visible):
        for w in (getattr(self, 'geo_inset_label', None),
                  getattr(self, '_anim_geo_sector_combo', None),
                  getattr(self, '_anim_geo_style_combo', None),
                  getattr(self, '_anim_geo_color_btn', None)):
            if w is not None:
                w.setVisible(visible)
        if visible:
            self._populate_anim_geo_sector_combo()

    def _on_anim_geo_style_changed(self):
        style = self._anim_geo_style_combo.currentData() if hasattr(self, '_anim_geo_style_combo') else "border"
        if hasattr(self, '_anim_composite_cache'):
            self._anim_composite_cache.clear()
        if style:
            self.settings.set("geotarget_style", style)
        if hasattr(self, '_anim_geo_color_btn'):
            self._anim_geo_color_btn.setEnabled(style == "border")
        self._on_anim_geo_sector_changed()

    def _on_anim_geo_sector_changed(self):
        if hasattr(self, '_anim_composite_cache'):
            self._anim_composite_cache.clear()
        if hasattr(self, '_anim_geo_sector_combo') and self._anim_geo_sector_combo.currentData():
            self.settings.set("geotarget_sectors", self._anim_geo_sector_combo.currentData())

    def _on_anim_geo_pick_color(self):
        from PySide6.QtWidgets import QColorDialog
        if not hasattr(self, '_anim_geo_color_btn'):
            return
        col = QColorDialog.getColor(QColor(self.settings.get("geotarget_border_color", "#00E5FF")), self, "Geo Target border color")
        if col.isValid():
            hx = col.name()
            self.settings.set("geotarget_border_color", hx)
            self._anim_geo_color_btn.setStyleSheet(
                "QPushButton { background: %s; border: 1px solid #888; }" % hx)
        self._on_anim_geo_style_changed()

    def _populate_anim_track_target_combo(self):
        cb = getattr(self, 'anim_track_target_combo', None)
        if cb is None:
            return
        sat = self.anim_sat_combo.currentText().lower() if hasattr(self, 'anim_sat_combo') else "himawari9"
        saved = self.settings.get("anim_track_target", None)
        cur = cb.currentData()
        cb.blockSignals(True)
        cb.clear()
        if "himawari" in sat:
            cb.addItem("Target Area", "Target")
            cb.addItem("Full Disk", "FLDK")
        else:
            cb.addItem("Meso 1", "M1")
            cb.addItem("Meso 2", "M2")
            cb.addItem("Full Disk", "FLDK")
        storms = getattr(self, 'atcf_storms', None) or []
        for s in storms:
            nm = s.get("storm_name") or "INVEST"
            aid = s.get("atcf_id")
            if aid is None:
                continue
            cb.addItem(f"Storm: {nm} ({aid})", ("storm", str(aid)))
        want = saved if saved is not None else cur
        idx = -1
        if isinstance(want, (tuple, list)):
            for i in range(cb.count()):
                d = cb.itemData(i)
                if isinstance(d, (tuple, list)) and d and d[0] == want[0] and str(d[1]) == str(want[1]):
                    idx = i
                    break
        elif want is not None:
            idx = cb.findData(want)
            if idx < 0:
                idx = cb.findData(str(want))
        if idx >= 0:
            cb.setCurrentIndex(idx)
        elif cb.count():
            cb.setCurrentIndex(0)
        cb.blockSignals(False)
        if hasattr(self, 'anim_track_cb') and hasattr(self, 'anim_track_target_combo'):
            self.anim_track_target_combo.setEnabled(self.anim_track_cb.isChecked())

    def _on_anim_track_toggled(self, checked):
        if hasattr(self, 'anim_track_target_combo'):
            self.anim_track_target_combo.setEnabled(checked)

    def _on_anim_track_target_changed(self):
        cb = getattr(self, 'anim_track_target_combo', None)
        if cb is None:
            return
        self.settings.set("anim_track_target", cb.currentData())
        setattr(self, '_anim_track_crs', None)

    def _create_animation_tab(self) -> QWidget:

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        header = QLabel("ANIMATION / LOOP  •  Supports Himawari (FLDK/Japan/Target) + GOES")
        header.setStyleSheet("color: #00BCD4; font-weight: bold; font-size: 12px;")
        layout.addWidget(header)

        seq_group = QGroupBox("Sequence Definition")
        seq_group.setStyleSheet("QGroupBox { color: #00BCD4; font-weight: bold; border: 1px solid #444; }")
        seq_layout = QVBoxLayout(seq_group)

        _now = datetime.now()
        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("From:"))
        self.anim_from_year = QComboBox()
        self.anim_from_year.addItems([str(y) for y in range(2015, 2031)])
        self.anim_from_year.setCurrentText(str(_now.year))
        self.anim_from_year.setFixedWidth(55)
        date_row.addWidget(self.anim_from_year)
        self.anim_from_month = QComboBox()
        self.anim_from_month.addItems([f"{m:02d}" for m in range(1, 13)])
        self.anim_from_month.setCurrentText(f"{_now.month:02d}")
        self.anim_from_month.setFixedWidth(55)
        date_row.addWidget(self.anim_from_month)
        self.anim_from_day = QComboBox()
        self.anim_from_day.addItems([f"{d:02d}" for d in range(1, 32)])
        self.anim_from_day.setCurrentText(f"{_now.day:02d}")
        self.anim_from_day.setFixedWidth(55)
        date_row.addWidget(self.anim_from_day)
        date_row.addStretch()
        seq_layout.addLayout(date_row)

        date_row2 = QHBoxLayout()
        date_row2.addWidget(QLabel("To:"))
        self.anim_to_year = QComboBox()
        self.anim_to_year.addItems([str(y) for y in range(2015, 2031)])
        self.anim_to_year.setCurrentText(str(_now.year))
        self.anim_to_year.setFixedWidth(55)
        date_row2.addWidget(self.anim_to_year)
        self.anim_to_month = QComboBox()
        self.anim_to_month.addItems([f"{m:02d}" for m in range(1, 13)])
        self.anim_to_month.setCurrentText(f"{_now.month:02d}")
        self.anim_to_month.setFixedWidth(55)
        date_row2.addWidget(self.anim_to_month)
        self.anim_to_day = QComboBox()
        self.anim_to_day.addItems([f"{d:02d}" for d in range(1, 32)])
        self.anim_to_day.setCurrentText(f"{_now.day:02d}")
        self.anim_to_day.setFixedWidth(55)
        date_row2.addWidget(self.anim_to_day)
        date_row2.addStretch()
        seq_layout.addLayout(date_row2)

        time_row = QHBoxLayout()
        time_row.addWidget(QLabel("From Time:"))
        self.anim_from_hour = QComboBox()
        self.anim_from_hour.addItems([f"{h:02d}" for h in range(24)])
        self.anim_from_hour.setCurrentText("00")
        self.anim_from_hour.setFixedWidth(55)
        time_row.addWidget(self.anim_from_hour)
        time_row.addWidget(QLabel(":"))
        self.anim_from_minute = QComboBox()
        self.anim_from_minute.addItems([f"{m:02d}" for m in range(0, 60, 10)])
        self.anim_from_minute.setCurrentText("00")
        self.anim_from_minute.setFixedWidth(55)
        time_row.addWidget(self.anim_from_minute)

        time_row.addSpacing(20)
        time_row.addWidget(QLabel("To Time:"))
        self.anim_to_hour = QComboBox()
        self.anim_to_hour.addItems([f"{h:02d}" for h in range(24)])
        self.anim_to_hour.setCurrentText("23")
        self.anim_to_hour.setFixedWidth(55)
        time_row.addWidget(self.anim_to_hour)
        time_row.addWidget(QLabel(":"))
        self.anim_to_minute = QComboBox()
        self.anim_to_minute.addItems([f"{m:02d}" for m in range(0, 60, 10)])
        self.anim_to_minute.setCurrentText("50")
        self.anim_to_minute.setFixedWidth(55)
        time_row.addWidget(self.anim_to_minute)
        time_row.addStretch()
        seq_layout.addLayout(time_row)

        step_row = QHBoxLayout()
        step_row.addWidget(QLabel("Time Step:"))
        self.anim_time_step = QComboBox()
        self.anim_time_step.addItems(["2.5 min", "10 min", "30 min", "1 hour", "3 hours"])
        self.anim_time_step.setCurrentText("30 min")
        step_row.addWidget(self.anim_time_step)
        step_row.addStretch()
        seq_layout.addLayout(step_row)

        sat_type_row = QHBoxLayout()
        sat_type_row.addWidget(QLabel("Satellite:"))
        self.anim_sat_combo = QComboBox()
        for sid, label in zip(self._perm_sat_ids, self._perm_sat_labels):
            self.anim_sat_combo.addItem(label, sid)
        self.anim_sat_combo.setCurrentText("Himawari 9")
        self.anim_sat_combo.setStyleSheet("QComboBox { min-width: 110px; }")
        sat_type_row.addWidget(self.anim_sat_combo)

        sat_type_row.addSpacing(12)
        sat_type_row.addWidget(QLabel("Type:"))
        self.anim_type_combo = QComboBox()
        self.anim_type_combo.addItem("Full Disk", "FLDK")
        self.anim_type_combo.addItem("Japan", "Japan")
        self.anim_type_combo.addItem("Target", "Target")
        self.anim_type_combo.setCurrentText("Full Disk")
        self.anim_type_combo.setStyleSheet("QComboBox { min-width: 95px; }")
        sat_type_row.addWidget(self.anim_type_combo)
        sat_type_row.addStretch()
        seq_layout.addLayout(sat_type_row)

        geo_row = QHBoxLayout()
        geo_row.setSpacing(4)
        self.geo_inset_label = QLabel("Geo Target insert:")
        geo_row.addWidget(self.geo_inset_label)
        self._anim_geo_sector_combo = QComboBox()
        self._anim_geo_sector_combo.setMinimumWidth(80)
        self._anim_geo_sector_combo.currentIndexChanged.connect(self._on_anim_geo_sector_changed)
        geo_row.addWidget(self._anim_geo_sector_combo)
        geo_row.addSpacing(8)
        geo_row.addWidget(QLabel("Style:"))
        self._anim_geo_style_combo = QComboBox()
        self._anim_geo_style_combo.addItem("Border box", "border")
        self._anim_geo_style_combo.addItem("Seamless blend", "blend")
        self._anim_geo_style_combo.setCurrentIndex(
            0 if self.settings.get("geotarget_style", "border") == "border" else 1)
        self._anim_geo_style_combo.currentIndexChanged.connect(self._on_anim_geo_style_changed)
        geo_row.addWidget(self._anim_geo_style_combo)
        geo_row.addSpacing(8)
        geo_row.addWidget(QLabel("Border:"))
        self._anim_geo_color_btn = QPushButton()
        self._anim_geo_color_btn.setFixedWidth(28)
        self._anim_geo_color_btn.setFixedHeight(22)
        self._anim_geo_color_btn.setStyleSheet(
            "QPushButton { background: %s; border: 1px solid #888; }" % self.settings.get("geotarget_border_color", "#00E5FF"))
        self._anim_geo_color_btn.setToolTip("Geo Target insert border color")
        self._anim_geo_color_btn.clicked.connect(self._on_anim_geo_pick_color)
        geo_row.addWidget(self._anim_geo_color_btn)
        geo_row.addStretch()
        seq_layout.addLayout(geo_row)
        self._anim_geo_color_btn.setEnabled(self._anim_geo_style_combo.currentData() == "border")
        self._anim_geo_style_combo.currentIndexChanged.connect(
            lambda: self._anim_geo_color_btn.setEnabled(self._anim_geo_style_combo.currentData() == "border"))
        self._populate_anim_geo_sector_combo()
        self._set_anim_geo_controls_visible(False)

        sync_st_row = QHBoxLayout()
        sync_st_btn = QPushButton("Use current Scene satellite + type")
        sync_st_btn.setIcon(self._std_icon(QStyle.StandardPixmap.SP_CommandLink, 16))
        sync_st_btn.setToolTip("Copy Satellite and Type from the main Scene controls into this animation sequence")
        sync_st_btn.clicked.connect(self._sync_anim_sat_type_from_main)
        sync_st_btn.setFixedWidth(230)
        sync_st_row.addWidget(sync_st_btn)
        sync_st_row.addStretch()
        seq_layout.addLayout(sync_st_row)

        self.anim_sat_combo.currentTextChanged.connect(self._update_anim_type_options)
        self.anim_sat_combo.currentTextChanged.connect(self._update_anim_band_labels)
        self.anim_sat_combo.currentTextChanged.connect(self._populate_anim_track_target_combo)
        self.anim_type_combo.currentTextChanged.connect(self._on_anim_type_changed)
        self.anim_type_combo.currentTextChanged.connect(self._populate_anim_track_target_combo)
        self.animation_controller._update_anim_type_options()

        try:
            if hasattr(self, 'sat_combo') and self.sat_combo.currentText():
                self.anim_sat_combo.setCurrentText(self.sat_combo.currentText())
            if hasattr(self, 'type_combo') and self.type_combo.currentText():

                desired = self.type_combo.currentText()
                if desired in [self.anim_type_combo.itemText(i) for i in range(self.anim_type_combo.count())]:
                    self.anim_type_combo.setCurrentText(desired)
        except Exception:
            pass

        content_group = QGroupBox("Content")
        content_group.setStyleSheet("""
            QGroupBox { color: #8B5CF6; font-weight: bold; border: 1px solid #555;
                border-radius: 5px; margin-top: 4px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
        """)
        content_outer = QVBoxLayout(content_group)
        content_outer.setSpacing(4)
        content_outer.setContentsMargins(6, 4, 6, 6)

        bands_label = QLabel("Bands (check which to load):")
        bands_label.setStyleSheet("color: #8B5CF6; font-weight: bold; font-size: 9px;")
        content_outer.addWidget(bands_label)

        self.anim_band_check_layout = QGridLayout()
        self.anim_band_check_layout.setSpacing(4)
        self.anim_band_check_layout.setContentsMargins(0, 0, 0, 0)
        content_outer.addLayout(self.anim_band_check_layout)
        self._anim_band_checkboxes = {}

        self.anim_checked_bands_label = QLabel("Selected: 1 band")
        self.anim_checked_bands_label.setStyleSheet("color:#8B5CF6; font-size:8px;")
        content_outer.addWidget(self.anim_checked_bands_label)

        prods_label = QLabel("Cached bands are shared with the Scene tab — click any RGB/Professional product there to build it instantly.")
        prods_label.setWordWrap(True)
        prods_label.setStyleSheet("color: #8B5CF6; font-weight: bold; font-size: 8px;")
        content_outer.addWidget(prods_label)

        self._anim_content_filter = "BAND"
        self._anim_filter_btns = {}
        self._anim_selected_band = "B13"
        self._anim_checked_bands = ["B13"]
        self._anim_active_content = ("BAND", "B13")
        self._build_anim_content_grid()

        self.anim_load_btn = QPushButton("Load / Prefetch Sequence")
        self.anim_load_btn.setStyleSheet("QPushButton { background: #00BCD4; color: white; font-weight: bold; }")
        self.anim_load_btn.clicked.connect(self._load_animation_sequence)
        seq_layout.addWidget(self.anim_load_btn)

        self.anim_progress = QProgressBar()
        self.anim_progress.setVisible(False)
        self.anim_progress.setFormat("Full Disk: %v/%m")
        seq_layout.addWidget(self.anim_progress)

        self.anim_target_progress = QProgressBar()
        self.anim_target_progress.setVisible(False)
        self.anim_target_progress.setFormat("Target inset: %v/%m")
        self.anim_target_progress.setStyleSheet("QProgressBar::chunk { background-color: #FF9800; }")
        seq_layout.addWidget(self.anim_target_progress)

        self.anim_japan_progress = QProgressBar()
        self.anim_japan_progress.setVisible(False)
        self.anim_japan_progress.setFormat("Japan: %v/%m")
        self.anim_japan_progress.setStyleSheet("QProgressBar::chunk { background-color: #4CAF50; }")
        seq_layout.addWidget(self.anim_japan_progress)

        prefetch_row = QHBoxLayout()
        self.anim_limit_frames = QCheckBox("Limit frames")
        self.anim_limit_frames.setChecked(True)
        prefetch_row.addWidget(self.anim_limit_frames)
        self.anim_max_prefetch = QSpinBox()
        self.anim_max_prefetch.setRange(5, 300)
        self.anim_max_prefetch.setValue(48)
        self.anim_max_prefetch.setSuffix(" frames")
        self.anim_max_prefetch.setToolTip(
            "Maximum number of frames to PRE-FETCH into RAM, applied SEPARATELY per frame type\n"
            "(e.g. 48 frames for the full disk, 48 for the Target sector, 48 for the Japan tiles).\n"
            "This does NOT limit the timeline length.\n"
            "You can still generate a very long From?To range; only up to N of each type will be loaded in the background.\n"
            "Scrubbing beyond the prefetched frames will load them on demand."
        )
        prefetch_row.addWidget(self.anim_max_prefetch)
        self.anim_limit_frames.toggled.connect(self.anim_max_prefetch.setEnabled)

        self.anim_cancel_btn = QPushButton("Cancel Prefetch")
        self.anim_cancel_btn.clicked.connect(self._cancel_anim_prefetch)
        prefetch_row.addWidget(self.anim_cancel_btn)

        self.anim_clear_btn = QPushButton("Clear Cache")
        self.anim_clear_btn.clicked.connect(self._clear_anim_cache)
        prefetch_row.addWidget(self.anim_clear_btn)
        prefetch_row.addStretch()
        seq_layout.addLayout(prefetch_row)

        self.anim_ready_label = QLabel("Ready: 0 / 0 frames")
        self.anim_ready_label.setStyleSheet("color:#4CAF50; font-size:9pt;")
        seq_layout.addWidget(self.anim_ready_label)

        sync_btn = QPushButton("Sync From/To from current loaded scene (+3h)")
        sync_btn.setIcon(self._std_icon(QStyle.StandardPixmap.SP_BrowserReload, 16))
        sync_btn.clicked.connect(self._sync_anim_dates_from_current)
        seq_layout.addWidget(sync_btn)

        layout.addWidget(seq_group)

        layout.addWidget(content_group)

        play_group = QGroupBox("Playback")
        play_group.setStyleSheet("QGroupBox { color: #00BCD4; font-weight: bold; border: 1px solid #444; }")
        play_layout = QVBoxLayout(play_group)

        controls = QHBoxLayout()
        self.anim_play_btn = QPushButton("Play")
        self.anim_play_btn.setIcon(self._std_icon(QStyle.StandardPixmap.SP_MediaPlay, 16))
        self.anim_play_btn.clicked.connect(self._toggle_animation)
        controls.addWidget(self.anim_play_btn)

        self.anim_speed = QComboBox()
        self.anim_speed.addItems(["0.5x", "1x", "2x", "4x", "8x"])
        self.anim_speed.setCurrentText("1x")
        self.anim_speed.currentTextChanged.connect(self._on_anim_speed_changed)
        controls.addWidget(self.anim_speed)

        self.anim_loop_cb = QCheckBox("Loop")
        self.anim_loop_cb.setChecked(True)
        controls.addWidget(self.anim_loop_cb)
        self.anim_track_cb = QCheckBox("Track")
        self.anim_track_cb.setChecked(False)
        self.anim_track_cb.setEnabled(self.anim_type_combo.currentText() in ("Target", "Full Disk"))
        self.anim_track_cb.setToolTip("Follow a reference during playback: the geo target area (Himawari Target / GOES Meso 1-2) or an ATCF storm position.")
        self.anim_track_cb.toggled.connect(lambda checked: setattr(self, '_anim_track_crs', None) if not checked else None)
        self.anim_track_cb.toggled.connect(self._on_anim_track_toggled)
        controls.addWidget(self.anim_track_cb)
        self.anim_track_target_combo = QComboBox()
        self.anim_track_target_combo.setEnabled(False)
        self.anim_track_target_combo.setMinimumWidth(120)
        self.anim_track_target_combo.setToolTip("What the Track system should follow: Full Disk / Target Area (Himawari) or Meso 1/2 (GOES) / ATCF storm.")
        self.anim_track_target_combo.currentIndexChanged.connect(self._on_anim_track_target_changed)
        controls.addWidget(self.anim_track_target_combo)
        self._populate_anim_track_target_combo()
        self.anim_export_btn = QPushButton("Export")
        self.anim_export_btn.setStyleSheet("QPushButton { background: #FF9800; color: white; font-weight: bold; padding: 2px 10px; border-radius: 3px; } QPushButton:hover { background: #F57C00; }")
        self.anim_export_btn.clicked.connect(self._export_animation_with_footer)
        controls.addWidget(self.anim_export_btn)
        controls.addStretch()
        play_layout.addLayout(controls)

        self.anim_frame_slider = QSlider(Qt.Horizontal)
        self.anim_frame_slider.setRange(0, 0)
        self.anim_frame_slider.valueChanged.connect(self._on_anim_frame_changed)
        play_layout.addWidget(self.anim_frame_slider)

        self.anim_frame_label = QLabel("Frame: 0 / 0")
        play_layout.addWidget(self.anim_frame_label)

        layout.addWidget(play_group)
        layout.addStretch()
        return container

    def _create_alerts_tab(self, *args, **kwargs):
        return self.alert_controller._create_alerts_tab(*args, **kwargs)
    def _apply_alert_filters(self, *args, **kwargs):
        return self.alert_controller._apply_alert_filters(*args, **kwargs)
    def _build_alert_item(self, *args, **kwargs):
        return self.alert_controller._build_alert_item(*args, **kwargs)
    def _rebuild_alerts_list(self, *args, **kwargs):
        return self.alert_controller._rebuild_alerts_list(*args, **kwargs)
    def _populate_alerts_list(self, *args, **kwargs):
        return self.alert_controller._populate_alerts_list(*args, **kwargs)
    def _build_multi_viewport_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        header = QLabel("MULTI-VIEWPORT  •  Per-Viewport Controls")
        header.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 11px;")
        layout.addWidget(header)

        self._mv_viewport_tabs = QTabWidget()
        self._mv_viewport_tabs.setStyleSheet("""
            QTabWidget::pane { border: 1px solid #444; background: #1E1E1E; }
            QTabBar::tab { background: #2D2D2D; color: #AAA; padding: 4px 10px;
                font-size: 9px; border: 1px solid #444; border-bottom: none;
                border-top-left-radius: 3px; border-top-right-radius: 3px; }
            QTabBar::tab:selected { background: #1E1E1E; color: #CE93D8; font-weight: bold; }
            QTabBar::tab:hover:!selected { background: #3A3A3A; }
        """)
        self._mv_tab_widgets = []
        self._mv_configs = []
        for i in range(5):
            self._mv_configs.append({
                "mode": "viewport", "band": None, "product": None,
                "product_filter": "BANDS", "overlays_enabled": True,
            })
            self._mv_tab_widgets.append(None)
        layout.addWidget(self._mv_viewport_tabs, 1)

        self.mv_count_spin.valueChanged.connect(self._rebuild_mv_tabs)

        self._rebuild_mv_tabs()

        return container

    def _build_multipanel_group(self):
        """Build the compact MultiPanel controls on the Scene tab.

        MultiPanel is a plain 2x2 split of the viewport — no per-panel mode
        pickers, no product tiles. Each panel simply shows the imagery of the
        main viewport (seeded) and is otherwise fully independent; Scene-tab
        product/band picks are routed to the selected panels only while the
        split IS the main display.
        """
        self._mp_group = QGroupBox("MultiPanel — Split Viewport into 4")
        self._mp_group.setStyleSheet("""
            QGroupBox { color: #00E5FF; font-weight: bold; border: 1px solid #555;
                border-radius: 4px; margin-top: 6px; padding-top: 8px; background: #1E1E1E; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        """)
        mp_layout = QVBoxLayout(self._mp_group)
        mp_layout.setSpacing(4)
        mp_layout.setContentsMargins(6, 4, 6, 4)

        mp_row1 = QHBoxLayout()
        self.mp_enable_cb = QCheckBox("Enable 4-Panel Split")
        self.mp_enable_cb.setStyleSheet("QCheckBox { color: #B0BEC5; font-size: 9pt; }")
        self.mp_enable_cb.toggled.connect(self._toggle_multipanel)
        mp_row1.addWidget(self.mp_enable_cb)
        self.mp_sync_master_cb = QCheckBox("Sync Views (Share Views)")
        self.mp_sync_master_cb.setStyleSheet("QCheckBox { color: #B0BEC5; font-size: 8pt; }")
        self.mp_sync_master_cb.setChecked(True)
        self.mp_sync_master_cb.toggled.connect(self._on_mp_sync_master)
        mp_row1.addWidget(self.mp_sync_master_cb)
        mp_row1.addStretch()
        mp_layout.addLayout(mp_row1)

        mp_row2 = QHBoxLayout()
        mp_row2.addWidget(QLabel("Placement:"))
        self.mp_placement_main = QRadioButton("Main viewport")
        self.mp_placement_float = QRadioButton("Floating window")
        self.mp_placement_main.setChecked(True)
        for rb in (self.mp_placement_main, self.mp_placement_float):
            rb.setStyleSheet("QRadioButton { color: #B0BEC5; font-size: 8pt; }")
        self.mp_placement_main.toggled.connect(self._on_mp_placement_changed)
        self.mp_placement_float.toggled.connect(self._on_mp_placement_changed)
        mp_row2.addWidget(self.mp_placement_main)
        mp_row2.addWidget(self.mp_placement_float)
        mp_row2.addStretch()
        mp_layout.addLayout(mp_row2)

        # Pure image mode for every panel: a 4-way split of the viewport.
        self._mp_configs = []
        self._mp_tab_widgets = [None, None, None, None]
        for i in range(4):
            self._mp_configs.append({
                "mode": "bands", "band": None, "product": None,
                "product_filter": "BANDS", "overlays_enabled": True,
            })

    def _seed_mp_panels(self):
        """Seed each panel with a snapshot of the current main viewport.

        Called when the split is enabled or the placement changes so the
        panels show the imagery right away instead of a black box. After the
        seed, panels only change through explicit routing while the split is
        the main display.
        """
        if not self._mp_manager or not self._mp_manager.is_active():
            return
        pix = self._get_current_scene_pixmap()
        if pix is None or pix.isNull():
            return
        for p in self._mp_manager.panels():
            p.set_snapshot_pixmap(pix)

    def _rebuild_mv_tabs(self):
        if not hasattr(self, '_mv_viewport_tabs'):
            return
        self._mv_viewport_tabs.clear()
        count = self.mv_count_spin.value() if hasattr(self, 'mv_count_spin') else 1
        count = max(1, min(5, count))
        for i in range(count):
            tab = self._build_mv_tab(i)
            self._mv_viewport_tabs.addTab(tab, f"VP #{i + 1}")

    def _toggle_multipanel(self, checked):
        if not checked:
            if self._mp_manager:
                self._mp_manager.set_placement("none")
            return
        placement = "main"
        if hasattr(self, 'mp_placement_float') and self.mp_placement_float.isChecked():
            placement = "floating"
        if self._mp_manager:
            self._mp_manager.set_placement(placement)
            self._mp_manager.bind_selection(self._on_mp_selection)
            self._apply_mp_master_sync()
            self._seed_mp_panels()

    def _on_mp_sync_master(self, checked):
        if self._mp_manager:
            c = self._mp_manager.active_container()
            if c is not None:
                c.set_sync_master(checked)

    def _on_mp_placement_changed(self):
        if not getattr(self, 'mp_enable_cb', None) or not self.mp_enable_cb.isChecked():
            return
        placement = "main"
        if self.mp_placement_float.isChecked():
            placement = "floating"
        if self._mp_manager:
            self._mp_manager.set_placement(placement)
            self._mp_manager.bind_selection(self._on_mp_selection)
            self._apply_mp_master_sync()
            self._seed_mp_panels()

    def _apply_mp_master_sync(self):
        if not self._mp_manager:
            return
        checked = self.mp_sync_master_cb.isChecked() if hasattr(self, 'mp_sync_master_cb') else False
        c = self._mp_manager.active_container()
        if c is not None:
            c.set_sync_master(checked)
        h = self._mp_manager.active_header()
        if h is not None:
            h.sync_cb.setChecked(checked)

    def _build_mv_tab(self, idx, kind="window"):
        is_panel = kind == "panel"
        widgets_list = self._mp_tab_widgets if is_panel else self._mv_tab_widgets
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        mode_row = QHBoxLayout()
        mode_row.setSpacing(4)
        mode_row.addWidget(QLabel("Mode:"))
        mode_combo = QComboBox()
        mode_combo.blockSignals(True)
        mode_combo.addItems(["viewport", "bands", "animation", "forecast", "3d globe"])
        mode_combo.blockSignals(False)
        mode_combo.setStyleSheet("font-size: 9px; padding: 2px 4px;")
        mode_combo.setFixedWidth(110)
        mode_row.addWidget(mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        stacked = QStackedWidget()

        # Page 0: viewport
        p0 = QWidget()
        lo0 = QVBoxLayout(p0)
        lo0.addWidget(QLabel("Mirroring main viewport", alignment=Qt.AlignCenter))
        stacked.addWidget(p0)

        # Page 1: bands
        p1, filter_btns, tile_grid, tile_container, grid_cb, coast_cb = self._build_mv_bands_page(idx, kind)
        stacked.addWidget(p1)

        # Page 2: animation
        p2 = self._build_mv_animation_page(idx)
        stacked.addWidget(p2)

        # Page 3: forecast
        p3 = QWidget()
        lo3 = QVBoxLayout(p3)
        lo3.setSpacing(4)
        lo3.addWidget(QLabel("Storm:"))
        storm_combo = QComboBox()
        storm_combo.setStyleSheet("font-size: 9px; padding: 2px 4px;")
        lo3.addWidget(storm_combo)
        gen_btn = QPushButton("Generate Forecast")
        gen_btn.setStyleSheet("QPushButton{background:#2E7D32;color:#fff;font-weight:600;padding:4px 10px;border-radius:3px;font-size:9px;}")
        lo3.addWidget(gen_btn)
        lo3.addStretch()
        stacked.addWidget(p3)

        # Page 4: 3d globe
        p4 = QWidget()
        lo4 = QVBoxLayout(p4)
        lo4.addWidget(QLabel("Globe mode - no additional controls", alignment=Qt.AlignCenter))
        stacked.addWidget(p4)

        layout.addWidget(stacked, 1)
        stacked.setCurrentIndex(0)

        tab_data = {
            "mode_combo": mode_combo,
            "stacked": stacked,
            "filter_btns": filter_btns,
            "tile_grid": tile_grid,
            "tile_container": tile_container,
            "grid_cb": grid_cb,
            "coast_cb": coast_cb,
            "storm_combo": storm_combo,
            "gen_btn": gen_btn,
        }
        widgets_list[idx] = tab_data

        mode_combo.currentIndexChanged.connect(
            lambda i, ii=idx, k=kind: self._on_mv_mode_changed(ii, mode_combo.currentText(), k))
        grid_cb.toggled.connect(
            lambda checked, ii=idx, k=kind: self._on_mv_overlay_toggle(ii, "grid", checked, k))
        coast_cb.toggled.connect(
            lambda checked, ii=idx, k=kind: self._on_mv_overlay_toggle(ii, "coast", checked, k))
        gen_btn.clicked.connect(lambda: self._on_mv_forecast_btn(idx, kind))
        storm_combo.currentIndexChanged.connect(
            lambda i, ii=idx, k=kind: self._on_mv_storm_changed(ii, storm_combo.currentData(), k))

        return container

    def _build_mv_bands_page(self, idx, kind="window"):
        p = QWidget()
        lo = QVBoxLayout(p)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.setSpacing(3)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(3)
        filter_btns = {}
        for label in ("Bands", "RGBs", "Professional"):
            fb = QPushButton(label)
            fb.setCheckable(True)
            fb.setChecked(label == "Bands")
            fb.setFixedHeight(22)
            fb.setStyleSheet("""
                QPushButton { background: #2D2D2D; color: #AAA;
                    border: 1px solid #444; border-radius: 3px;
                    font-size: 9px; padding: 0 6px; }
                QPushButton:checked { background: #3D2B6E; color: #C9B8F8;
                    border: 1px solid #7C5CBF; }
                QPushButton:hover:!checked { background: #3A3A3A; }
            """)
            fb.clicked.connect(lambda checked, l=label, ii=idx, k=kind: self._on_mv_product_filter(ii, l, k))
            filter_row.addWidget(fb)
            filter_btns[label] = fb
        filter_row.addStretch()
        lo.addLayout(filter_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        tile_container = QWidget()
        tile_grid = QGridLayout(tile_container)
        tile_grid.setSpacing(3)
        tile_grid.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(tile_container)
        lo.addWidget(scroll, 1)

        ol_row = QHBoxLayout()
        ol_row.setSpacing(4)
        grid_cb = QCheckBox("Grid")
        grid_cb.setStyleSheet("QCheckBox { color: #B0BEC5; font-size: 8px; }")
        grid_cb.setChecked(True)
        coast_cb = QCheckBox("Coast")
        coast_cb.setStyleSheet("QCheckBox { color: #B0BEC5; font-size: 8px; }")
        coast_cb.setChecked(True)
        ol_row.addWidget(grid_cb)
        ol_row.addWidget(coast_cb)
        ol_row.addStretch()
        lo.addLayout(ol_row)

        return p, filter_btns, tile_grid, tile_container, grid_cb, coast_cb

    def _build_mv_animation_page(self, idx):
        p = QWidget()
        lo = QVBoxLayout(p)
        lo.setSpacing(4)
        group = QGroupBox("Overlays & Tracks")
        group.setStyleSheet("QGroupBox { color: #00E5FF; font-size: 9px; font-weight: bold; border: 1px solid #444; border-radius: 3px; margin-top: 4px; padding-top: 6px; } QGroupBox::title { subcontrol-origin: margin; left: 6px; padding: 0 3px; }")
        gl = QVBoxLayout(group)
        gl.setSpacing(2)
        cbs = {}
        for label in ("Grid", "Coastline", "NHC Tracks", "JMA Tracks", "JTWC Tracks"):
            cb = QCheckBox(label)
            cb.setStyleSheet("QCheckBox { color: #B0BEC5; font-size: 8px; }")
            cb.setChecked(True)
            gl.addWidget(cb)
            cbs[label] = cb
        lo.addWidget(group)
        lo.addStretch()
        self._mv_anim_cbs = cbs
        return p

    def _on_mv_mode_changed(self, idx, mode, kind="window"):
        mode_map = {"viewport": 0, "bands": 1, "animation": 2, "forecast": 3, "3d globe": 4}
        if kind == "panel":
            for p in self._mp_target_panels(idx):
                pi = p.index
                self._mp_configs[pi]["mode"] = mode
                td = self._mp_tab_widgets[pi]
                if td is not None:
                    c = td["mode_combo"]
                    c.blockSignals(True)
                    c.setCurrentText(mode)
                    c.blockSignals(False)
                    td["stacked"].setCurrentIndex(mode_map.get(mode, 0))
                    if mode == "bands":
                        self._mv_rebuild_tiles(pi, "panel")
                    elif mode == "forecast":
                        self._mv_refresh_storms(pi, "panel")
                p.update_config(mode=mode)
            return
        self._mv_configs[idx]["mode"] = mode
        td = self._mv_tab_widgets[idx]
        td["stacked"].setCurrentIndex(mode_map.get(mode, 0))
        if mode == "bands":
            self._mv_rebuild_tiles(idx)
        elif mode == "forecast":
            self._mv_refresh_storms(idx)
        if self._mv_manager and idx < len(self._mv_manager.windows):
            self._mv_manager.windows[idx].update_config(mode=mode)

    def _mp_target_panels(self, idx):
        """Panels that config changes from tab ``idx`` should reach.

        If the user has clicked panels in the grid, the change lands on the
        selected set; otherwise it falls back to the panel matching ``idx``.
        """
        mgr = getattr(self, '_mp_manager', None)
        if not mgr or not mgr.is_active():
            return []
        sel = mgr.selected_panels()
        if sel:
            return list(sel)
        p = mgr.panel(idx)
        return [p] if p is not None else []

    def _mp_set_tile_checked(self, pi, item_key):
        td = self._mp_tab_widgets[pi]
        if td is None:
            return
        grid = td["tile_grid"]
        for gi in range(grid.count()):
            w = grid.itemAt(gi).widget()
            if w is not None:
                w.setChecked(w.property("item_key") == item_key)

    def _on_mp_selection(self, selected, anchor):
        pass

    def _mp_route_main_selection(self, product_key=None, band=None):
        """Route a main-viewport product/band pick to the selected panels.

        Only routes when the MultiPanel surface is the main display (placement
        "main"), so Scene-tab clicks drive the panels they are looking at.
        Returns True when the pick was consumed by the panels so the main
        viewport is left untouched, False when it should proceed normally.
        """
        mgr = getattr(self, '_mp_manager', None)
        if not mgr or not mgr.is_active():
            return False
        if getattr(mgr, 'placement', None) != "main":
            return False
        targets = [p for p in mgr.selected_panels() if p is not None]
        if not targets:
            return False
        _names = ", ".join(f"VP#{p.index + 1}" for p in targets)
        for p in targets:
            pi = p.index
            cfg = self._mp_configs[pi]
            cfg["mode"] = "bands"
            if band is not None:
                cfg["band"] = band
                cfg["product"] = None
                self._mp_sync_panel_tab_config(pi, mode="bands", band=band)
                p.update_config(mode="bands", band=band, force=True)
            elif product_key is not None:
                cfg["product"] = product_key
                cfg["band"] = None
                self._mp_sync_panel_tab_config(pi, mode="bands", product=product_key)
                p.update_config(mode="bands", product=product_key, force=True)
        if band is not None:
            self.log(f"MultiPanel: routed band '{band}' to {_names}")
        elif product_key is not None:
            self.log(f"MultiPanel: routed product '{product_key}' to {_names}")
        return True

    def _mp_sync_panel_tab_config(self, pi, mode=None, band=None, product=None):
        td = self._mp_tab_widgets[pi]
        if td is None:
            return
        mode_map = {"viewport": 0, "bands": 1, "animation": 2, "forecast": 3, "3d globe": 4}
        if mode is not None:
            c = td["mode_combo"]
            c.blockSignals(True)
            c.setCurrentText(mode)
            c.blockSignals(False)
            td["stacked"].setCurrentIndex(mode_map.get(mode, 0))
            if mode == "bands":
                self._mv_rebuild_tiles(pi, "panel")
        want = band or product
        if want:
            self._mp_set_tile_checked(pi, want)

    def _on_mv_product_filter(self, idx, label, kind="window"):
        is_panel = kind == "panel"
        cfg_list = self._mp_configs if is_panel else self._mv_configs
        widgets_list = self._mp_tab_widgets if is_panel else self._mv_tab_widgets
        cfg = cfg_list[idx]
        cfg["product_filter"] = label.upper()
        td = widgets_list[idx]
        for lbl, btn in td["filter_btns"].items():
            btn.setChecked(lbl == label)
        self._mv_rebuild_tiles(idx, kind)

    def _mv_rebuild_tiles(self, idx, kind="window"):
        is_panel = kind == "panel"
        cfg_list = self._mp_configs if is_panel else self._mv_configs
        widgets_list = self._mp_tab_widgets if is_panel else self._mv_tab_widgets
        td = widgets_list[idx]
        grid = td["tile_grid"]
        while grid.count():
            item = grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        cfg = cfg_list[idx]
        filt = cfg["product_filter"]

        sat = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
        all_products = get_products(sat)
        bands = getattr(self, 'available_bands', [])

        items_to_show = []

        if filt == "BANDS":
            for b in bands:
                nickname = _BAND_NICKNAMES.get(b.replace("B", ""), "")
                items_to_show.append(("band", b, f"{b} {nickname}", ""))
        else:
            for key, info in all_products.items():
                tag = info.get("tag", "ALL")
                if filt == "RGBS" and tag == "PROFESSIONAL":
                    continue
                if filt == "PROFESSIONAL" and tag != "PROFESSIONAL":
                    continue
                items_to_show.append(("product", key, info["name"], tag))

        col_count = 2
        row, col = 0, 0
        for item_type, item_key, display_name, tag in items_to_show:
            tile = self._mv_make_tile(item_type, item_key, display_name, tag, idx,
                                      is_panel=is_panel)
            grid.addWidget(tile, row, col)
            col += 1
            if col >= col_count:
                col = 0
                row += 1

    def _mv_make_tile(self, item_type, item_key, name, tag, idx, is_panel=False):
        tile = QPushButton()
        tile.setCheckable(True)
        tile.setFixedHeight(38)
        tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        tile.setProperty("item_type", item_type)
        tile.setProperty("item_key", item_key)
        tile.setProperty("vp_idx", idx)
        tile.setProperty("is_panel", is_panel)
        tile.setToolTip(name)
        layout = QHBoxLayout(tile)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(5)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        if item_type == "band":
            dot_color = "#4FC3F7"
            badge_text = "B"
            badge_color = "#4FC3F7"
        elif tag == "PROFESSIONAL":
            dot_color = "#6A1B9A"
            badge_text = "PRO"
            badge_color = "#6A1B9A"
        else:
            dot_color = get_tag_colors().get(tag, "#1B5E20")
            badge_text = tag
            badge_color = dot_color
        dot.setStyleSheet(f"background: {dot_color}; border-radius: 4px;")
        dot.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(dot)
        name_lbl = QLabel(name)
        name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        name_lbl.setStyleSheet("color: #DDD; font-size: 9px; background: transparent;")
        layout.addWidget(name_lbl, 1)
        badge = QLabel(badge_text)
        badge.setFixedSize(30, 14)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"""
            background: {badge_color}; color: #EEE;
            border-radius: 3px; font-size: 7px; font-weight: bold;
        """)
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(badge)
        tile.setStyleSheet("""
            QPushButton { background: #252525; border: 1px solid #3A3A3A;
                border-radius: 4px; }
            QPushButton:disabled { background: #1A1A1A; border: 1px solid #2A2A2A; }
            QPushButton:hover:!checked { background: #2E2E2E; border-color: #555; }
            QPushButton:checked { background: #2A1F4A; border: 1px solid #8B5CF6; }
        """)
        tile.clicked.connect(self._on_mv_tile_clicked)
        return tile

    def _on_mv_tile_clicked(self, tile=None):
        if tile is None:
            tile = self.sender()
        if not tile:
            return
        idx = tile.property("vp_idx")
        item_type = tile.property("item_type")
        item_key = tile.property("item_key")
        is_panel = bool(tile.property("is_panel"))
        if idx is None or item_type is None or item_key is None:
            return
        if is_panel:
            for p in self._mp_target_panels(idx):
                pi = p.index
                cfg = self._mp_configs[pi]
                if item_type == "band":
                    cfg["band"] = item_key
                    cfg["product"] = None
                    p.update_config(band=item_key)
                else:
                    cfg["product"] = item_key
                    cfg["band"] = None
                    p.update_config(product=item_key)
                self._mp_set_tile_checked(pi, item_key)
            return
        cfg = self._mv_configs[idx]
        td = self._mv_tab_widgets[idx]
        if item_type == "band":
            cfg["band"] = item_key
            cfg["product"] = None
            if self._mv_manager and idx < len(self._mv_manager.windows):
                self._mv_manager.windows[idx].update_config(band=item_key)
        else:
            cfg["product"] = item_key
            cfg["band"] = None
            if self._mv_manager and idx < len(self._mv_manager.windows):
                self._mv_manager.windows[idx].update_config(product=item_key)
        for i in range(td["tile_grid"].count()):
            w = td["tile_grid"].itemAt(i).widget()
            if w:
                w.setChecked(w.property("item_key") == item_key)

    def _on_mv_overlay_toggle(self, idx, kind, enabled, owner="window"):
        is_panel = owner == "panel"
        kwargs = {}
        if kind == "grid":
            kwargs["grid_enabled"] = enabled
        elif kind == "coast":
            kwargs["coast_enabled"] = enabled
        if is_panel:
            if self._mp_manager and self._mp_manager.is_active():
                p = self._mp_manager.panel(idx)
                if p is not None:
                    p.update_config(**kwargs)
        else:
            if self._mv_manager and idx < len(self._mv_manager.windows):
                self._mv_manager.windows[idx].update_config(**kwargs)

    def _on_mv_forecast_btn(self, idx, kind="window"):
        is_panel = kind == "panel"
        widgets_list = self._mp_tab_widgets if is_panel else self._mv_tab_widgets
        td = widgets_list[idx]
        storm_id = td["storm_combo"].currentData()
        if not storm_id:
            return
        if is_panel:
            if self._mp_manager and self._mp_manager.is_active():
                p = self._mp_manager.panel(idx)
                if p is not None and hasattr(self, '_request_multi_forecast'):
                    self._request_multi_forecast(idx, storm_id, panel=True)
        else:
            if self._mv_manager and idx < len(self._mv_manager.windows):
                if hasattr(self, '_request_multi_forecast'):
                    self._request_multi_forecast(idx, storm_id)

    def _on_mv_storm_changed(self, idx, storm_id, kind="window"):
        pass

    def _mv_refresh_storms(self, idx, kind="window"):
        is_panel = kind == "panel"
        widgets_list = self._mp_tab_widgets if is_panel else self._mv_tab_widgets
        td = widgets_list[idx]
        combo = td["storm_combo"]
        combo.blockSignals(True)
        combo.clear()
        nhc_storms = getattr(self, 'nhc_storms', None)
        if nhc_storms:
            for sid, sd in nhc_storms.items():
                label = sd.get("storm_name", sid)
                combo.addItem(f"{label} ({sid})", sid)
        combo.blockSignals(False)

    def _init_tab_shortcuts(self):
        self._tab_shortcut_objects = []
        self._rebuild_tab_shortcuts()

    def _rebuild_tab_shortcuts(self):
        for s in getattr(self, '_tab_shortcut_objects', []):
            try:
                s.setEnabled(False)
            except RuntimeError:
                pass
        self._tab_shortcut_objects = []
        if not hasattr(self, 'right_tab_widget'):
            return
        sc = getattr(self, 'settings', None)
        defaults = {
            "tab_next": "Ctrl+Tab",
            "tab_prev": "Ctrl+Shift+Tab",
        }
        for i in range(self.right_tab_widget.count()):
            defaults[f"tab_jump_{i}"] = f"Ctrl+{i + 1}"
        if sc:
            saved = sc.get("shortcuts", {})
        else:
            saved = {}
        for key, default_seq in defaults.items():
            seq_str = saved.get(key, default_seq)
            if key == "tab_next":
                s = QShortcut(QKeySequence(seq_str), self)
                s.activated.connect(lambda: self._cycle_tab(1))
                self._tab_shortcut_objects.append(s)
            elif key == "tab_prev":
                s = QShortcut(QKeySequence(seq_str), self)
                s.activated.connect(lambda: self._cycle_tab(-1))
                self._tab_shortcut_objects.append(s)
            elif key.startswith("tab_jump_"):
                idx = int(key.split("_")[-1])
                s = QShortcut(QKeySequence(seq_str), self)
                s.activated.connect(lambda i=idx: self._jump_to_tab(i))
                self._tab_shortcut_objects.append(s)

    def _cycle_tab(self, direction: int):
        tw = self.right_tab_widget
        count = tw.count()
        if count < 2:
            return
        current = tw.currentIndex()
        for _ in range(count):
            current = (current + direction) % count
            if tw.isTabVisible(current):
                tw.setCurrentIndex(current)
                return

    def _jump_to_tab(self, idx: int):
        tw = self.right_tab_widget
        if 0 <= idx < tw.count() and tw.isTabVisible(idx):
            tw.setCurrentIndex(idx)

    def _on_anim_content_changed(self, *args, **kwargs):
        return self.animation_controller._on_anim_content_changed(*args, **kwargs)

    def _set_anim_content_filter(self, label: str):
        self._anim_content_filter = label.upper()
        for lbl, btn in getattr(self, '_anim_filter_btns', {}).items():
            try:
                btn.setChecked(lbl == label)
            except Exception:
                pass
        self._build_anim_content_grid()

    def _build_anim_content_grid(self):
        if not hasattr(self, 'anim_band_check_layout'):
            return
        self._build_anim_band_checkboxes()

    def _build_anim_band_grid(self):
        self._build_anim_band_checkboxes()

    def _build_anim_band_checkboxes(self):
        if not hasattr(self, 'anim_band_check_layout'):
            return
        while self.anim_band_check_layout.count():
            item = self.anim_band_check_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._anim_band_checkboxes = {}
        sat = self.anim_sat_combo.currentText().lower() if hasattr(self, 'anim_sat_combo') else "himawari9"
        is_goes = "goes" in sat
        prefix = "C" if is_goes else "B"
        col_count = 4
        row, col = 0, 0
        checked_set = set(self._anim_checked_bands)
        for i in range(1, 17):
            band = f"{prefix}{i:02d}"
            cb = QCheckBox(band)
            cb.setChecked(band in checked_set)
            cb.setStyleSheet("""
                QCheckBox { color: #DDD; font-size: 11px; spacing: 4px; }
                QCheckBox::indicator { width: 14px; height: 14px; }
                QCheckBox:hover { color: #C9B8F8; }
            """)
            cb.toggled.connect(lambda checked, b=band: self._on_anim_band_toggled(b, checked))
            self.anim_band_check_layout.addWidget(cb, row, col)
            self._anim_band_checkboxes[band] = cb
            col += 1
            if col >= col_count:
                col = 0
                row += 1

    def _on_anim_band_toggled(self, band: str, checked: bool):
        if checked:
            if band not in self._anim_checked_bands:
                self._anim_checked_bands.append(band)
            self._anim_selected_band = band
            self._anim_active_content = ("BAND", band)
        else:
            if band in self._anim_checked_bands:
                self._anim_checked_bands.remove(band)
            if self._anim_active_content == ("BAND", band):
                if self._anim_checked_bands:
                    last = self._anim_checked_bands[-1]
                    self._anim_selected_band = last
                    self._anim_active_content = ("BAND", last)
                else:
                    self._anim_selected_band = ""
                    self._anim_active_content = None
        self._update_anim_checked_bands_label()

    def _on_anim_band_clicked(self, band: str):
        self._on_anim_band_toggled(band, True)

    def _highlight_anim_band(self, band: str):
        for name, cb in getattr(self, '_anim_band_checkboxes', {}).items():
            if cb is not None:
                try:
                    cb.setChecked(name == band)
                except Exception:
                    pass

    def _update_anim_checked_bands_label(self):
        label = getattr(self, 'anim_checked_bands_label', None)
        if label is None:
            return
        n = len(self._anim_checked_bands)
        names = ", ".join(self._anim_checked_bands[:6])
        if n > 6:
            names += f" (+{n - 6})"
        label.setText(f"Selected: {n} band(s)  —  {names}")

    def _get_anim_selected_content(self) -> tuple:
        return False, (self._anim_selected_band or "")

    def _get_anim_checked_bands(self) -> tuple:
        return tuple(self._anim_checked_bands)

    def _get_anim_active_content(self):
        return self._anim_active_content or ("BAND", "")
    def _on_anim_type_changed(self, *args, **kwargs):
        return self.animation_controller._on_anim_type_changed(*args, **kwargs)
    def _load_animation_sequence(self, *args, **kwargs):
        return self.animation_controller._load_animation_sequence(*args, **kwargs)
    def _refresh_tracks_list(self, *args, **kwargs):
        return self.forecast_controller._refresh_tracks_list(*args, **kwargs)
    def _debounced_refresh_tracks(self, *args, **kwargs):
        return self.forecast_controller._debounced_refresh_tracks(*args, **kwargs)
    def _propagate_children(self, *args, **kwargs):
        return self.forecast_controller._propagate_children(*args, **kwargs)
    def _set_nhc_track_check(self, *args, **kwargs):
        return self.forecast_controller._set_nhc_track_check(*args, **kwargs)
    def _on_track_item_changed(self, *args, **kwargs):
        return self.forecast_controller._on_track_item_changed(*args, **kwargs)
    def _delete_selected_track(self, *args, **kwargs):
        return self.forecast_controller._delete_selected_track(*args, **kwargs)
    def _download_all_tc_updates(self, *args, **kwargs):
        return self.forecast_controller._download_all_tc_updates(*args, **kwargs)
    def _download_nhc_updates(self, *args, **kwargs):
        return self.forecast_controller._download_nhc_updates(*args, **kwargs)
    def _on_nhc_progress(self, *args, **kwargs):
        return self.forecast_controller._on_nhc_progress(*args, **kwargs)
    def _is_placeholder_name(self, *args, **kwargs):
        return self.forecast_controller._is_placeholder_name(*args, **kwargs)
    def _on_nhc_storm_downloaded(self, *args, **kwargs):
        return self.forecast_controller._on_nhc_storm_downloaded(*args, **kwargs)
    def _refresh_nhc_storm_combo(self, *args, **kwargs):
        return self.forecast_controller._refresh_nhc_storm_combo(*args, **kwargs)
    def _load_nhc_from_disk(self, *args, **kwargs):
        return self.forecast_controller._load_nhc_from_disk(*args, **kwargs)
    def _load_jma_from_disk(self, *args, **kwargs):
        return self.forecast_controller._load_jma_from_disk(*args, **kwargs)
    def _on_nhc_storm_selected(self, *args, **kwargs):
        return self.forecast_controller._on_nhc_storm_selected(*args, **kwargs)
    def _on_storm_filter_changed(self, *args, **kwargs):
        return self.forecast_controller._on_storm_filter_changed(*args, **kwargs)
    def _redraw_nhc_only(self):
        scene = self.graphics_view.scene()
        if not scene:
            self.overlay_controller.update_overlays()
            return
        if not hasattr(self, '_ol_project_func'):
            self.overlay_controller.update_overlays()
            return
        for item in getattr(self, '_nhc_overlay_items', []):
            try:
                if item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        self._nhc_overlay_items.clear()
        self.overlay_controller._draw_nhc_overlays(self._ol_project_func, scene)
        self.graphics_view.viewport().update()

    def _toggle_forecast_overlays(self, *args, **kwargs):
        return self.forecast_controller._toggle_forecast_overlays(*args, **kwargs)
    def _toggle_nhc_overlays(self, *args, **kwargs):
        return self.forecast_controller._toggle_nhc_overlays(*args, **kwargs)
    def _toggle_jma_overlays(self, *args, **kwargs):
        return self.forecast_controller._toggle_jma_overlays(*args, **kwargs)
    def _on_nhc_error(self, *args, **kwargs):
        return self.forecast_controller._on_nhc_error(*args, **kwargs)
    def _on_nhc_finished(self, *args, **kwargs):
        return self.forecast_controller._on_nhc_finished(*args, **kwargs)
    def _fetch_jma_data(self, *args, **kwargs):
        return self.forecast_controller._fetch_jma_data(*args, **kwargs)
    def _jma_download_update_for_storm(self, *args, **kwargs):
        return self.forecast_controller._jma_download_update_for_storm(*args, **kwargs)
    def _on_jma_progress(self, *args, **kwargs):
        return self.forecast_controller._on_jma_progress(*args, **kwargs)
    def _on_jma_storm_downloaded(self, *args, **kwargs):
        return self.forecast_controller._on_jma_storm_downloaded(*args, **kwargs)
    def _on_jma_error(self, *args, **kwargs):
        return self.forecast_controller._on_jma_error(*args, **kwargs)
    def _on_jma_finished(self, *args, **kwargs):
        return self.forecast_controller._on_jma_finished(*args, **kwargs)
    def _refresh_jma_storm_combo(self, *args, **kwargs):
        return self.forecast_controller._refresh_jma_storm_combo(*args, **kwargs)
    def _on_jma_storm_selected(self, *args, **kwargs):
        return self.forecast_controller._on_jma_storm_selected(*args, **kwargs)
    def _refresh_all_storm_combo(self, *args, **kwargs):
        return self.forecast_controller._refresh_all_storm_combo(*args, **kwargs)
    def _toggle_jma_overlays(self, *args, **kwargs):
        return self.forecast_controller._toggle_jma_overlays(*args, **kwargs)
    def _redraw_jma_only(self):
        scene = self.graphics_view.scene()
        if not scene:
            self.overlay_controller.update_overlays()
            return
        if not hasattr(self, '_ol_project_func'):
            self.overlay_controller.update_overlays()
            return
        for item in getattr(self, '_jma_overlay_items', []):
            try:
                if item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        self._jma_overlay_items.clear()
        self.overlay_controller._draw_jma_overlays(self._ol_project_func, scene)
        self.graphics_view.viewport().update()

    def _redraw_jtwc_only(self):
        scene = self.graphics_view.scene()
        if not scene:
            self.overlay_controller.update_overlays()
            return
        if not hasattr(self, '_ol_project_func'):
            self.overlay_controller.update_overlays()
            return
        for item in getattr(self, '_jtwc_overlay_items', []):
            try:
                if item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        self._jtwc_overlay_items.clear()
        self.overlay_controller._draw_jtwc_overlays(self._ol_project_func, scene)
        self.graphics_view.viewport().update()

    # -- JTWC handlers --------------------------------------------------

    def _fetch_jtwc_data(self, *args, **kwargs):
        return self.forecast_controller._fetch_jtwc_data(*args, **kwargs)
    def _on_jtwc_progress(self, *args, **kwargs):
        return self.forecast_controller._on_jtwc_progress(*args, **kwargs)
    def _format_jtwc_dtg(self, *args, **kwargs):
        return self.forecast_controller._format_jtwc_dtg(*args, **kwargs)
    def _on_jtwc_storm_downloaded(self, *args, **kwargs):
        return self.forecast_controller._on_jtwc_storm_downloaded(*args, **kwargs)
    def _on_jtwc_error(self, *args, **kwargs):
        return self.forecast_controller._on_jtwc_error(*args, **kwargs)
    def _on_jtwc_finished(self, *args, **kwargs):
        return self.forecast_controller._on_jtwc_finished(*args, **kwargs)
    def _refresh_jtwc_storm_combo(self, *args, **kwargs):
        return self.forecast_controller._refresh_jtwc_storm_combo(*args, **kwargs)
    def _refresh_pagasa_storm_combo(self, *args, **kwargs):
        return self.forecast_controller._refresh_pagasa_storm_combo(*args, **kwargs)
    def _refresh_cwa_storm_combo(self, *args, **kwargs):
        return self.forecast_controller._refresh_cwa_storm_combo(*args, **kwargs)
    def _load_pagasa_from_disk(self, *args, **kwargs):
        return self.forecast_controller._load_pagasa_from_disk(*args, **kwargs)
    def _load_jtwc_from_disk(self, *args, **kwargs):
        return self.forecast_controller._load_jtwc_from_disk(*args, **kwargs)
    def _fetch_pagasa_data(self, *args, **kwargs):
        return self.forecast_controller._fetch_pagasa_data(*args, **kwargs)
    def _on_pagasa_progress(self, *args, **kwargs):
        return self.forecast_controller._on_pagasa_progress(*args, **kwargs)
    def _on_pagasa_storm_downloaded(self, *args, **kwargs):
        return self.forecast_controller._on_pagasa_storm_downloaded(*args, **kwargs)
    def _on_pagasa_error(self, *args, **kwargs):
        return self.forecast_controller._on_pagasa_error(*args, **kwargs)
    def _on_pagasa_finished(self, *args, **kwargs):
        return self.forecast_controller._on_pagasa_finished(*args, **kwargs)
    def _sync_pagasa_on_startup(self, *args, **kwargs):
        return self.forecast_controller._sync_pagasa_on_startup(*args, **kwargs)
    def _sync_cwa_on_startup(self, *args, **kwargs):
        return self.forecast_controller._sync_cwa_on_startup(*args, **kwargs)
    # --- ATCF methods ---
    def _sync_atcf_on_startup(self):
        try:
            self.log("ATCF sync: fetching storm data on startup...")
            self._auto_refresh_atcf()
        except Exception as e:
            self.log(f"ATCF startup sync error: {e}")
    def _fetch_atcf_data(self, *args, **kwargs): pass
    def _on_atcf_storms_updated(self, *args, **kwargs): pass
    def _on_atcf_finished(self, *args, **kwargs): pass
    def _cross_reference_atcf_storms(self, *args, **kwargs): pass
    def _set_atcf_storm_visibility(self, *args, **kwargs): pass
    def _clear_atcf_overlays(self): pass
    def _redraw_atcf_overlays(self): pass
    @staticmethod
    def _atcf_jtwc_icon_name(*args, **kwargs): return None
    @staticmethod
    def _atcf_nhc_icon_name(*args, **kwargs): return None
    @staticmethod
    def _atcf_generic_icon_name(*args, **kwargs): return None
    def _init_alert_system(self, *args, **kwargs):
        return self.alert_controller._init_alert_system(*args, **kwargs)
    def _last_alerts_path(self, *args, **kwargs):
        return self.alert_controller._last_alerts_path(*args, **kwargs)
    def _load_last_alerts(self, *args, **kwargs):
        return self.alert_controller._load_last_alerts(*args, **kwargs)
    def _save_last_alerts(self, *args, **kwargs):
        return self.alert_controller._save_last_alerts(*args, **kwargs)
    def _cleanup_expired_alerts(self, *args, **kwargs):
        return self.alert_controller._cleanup_expired_alerts(*args, **kwargs)
    def _on_new_alert(self, *args, **kwargs):
        return self.alert_controller._on_new_alert(*args, **kwargs)
    def _on_emergency_alert(self, *args, **kwargs):
        return self.alert_controller._on_emergency_alert(*args, **kwargs)
    def _on_fulemer_alert(self, *args, **kwargs):
        return self.alert_controller._on_fulemer_alert(*args, **kwargs)
    def _set_all_alert_checks(self, *args, **kwargs):
        return self.alert_controller._set_all_alert_checks(*args, **kwargs)
    def _acknowledge_selected(self, *args, **kwargs):
        return self.alert_controller._acknowledge_selected(*args, **kwargs)
    def _acknowledge_all_alerts(self, *args, **kwargs):
        return self.alert_controller._acknowledge_all_alerts(*args, **kwargs)
    def _on_alert_summary(self, *args, **kwargs):
        return self.alert_controller._on_alert_summary(*args, **kwargs)
    def _apply_alert_settings(self, *args, **kwargs):
        return self.alert_controller._apply_alert_settings(*args, **kwargs)
    def _show_alert_popup(self, *args, **kwargs):
        return self.alert_controller._show_alert_popup(*args, **kwargs)
    def _show_emergency_popup(self, *args, **kwargs):
        return self.alert_controller._show_emergency_popup(*args, **kwargs)
    def _switch_to_alerts_tab(self, *args, **kwargs):
        return self.alert_controller._switch_to_alerts_tab(*args, **kwargs)
    def _make_pagasa_filename(self, *args, **kwargs):
        return self.alert_controller._make_pagasa_filename(*args, **kwargs)
    def _make_pagasa_filename(self, *args, **kwargs):
        return self.alert_controller._make_pagasa_filename(*args, **kwargs)
    def _generate_alert_map(self, *args, **kwargs):
        QMessageBox.warning(
            self,
            "Alert Map",
            "Alert map generation is not possible at the moment. "
            "This feature is temporarily disabled for development testing.",
        )
    def _cleanup_temp(self, *args, **kwargs):
        return self.alert_controller._cleanup_temp(*args, **kwargs)
    def _show_alert_detail(self, *args, **kwargs):
        return self.alert_controller._show_alert_detail(*args, **kwargs)
    def _speak_alert(self, *args, **kwargs):
        return self.alert_controller._speak_alert(*args, **kwargs)
    def _generate_single_alert_map(self, *args, **kwargs):
        QMessageBox.warning(
            self,
            "Alert Map",
            "Alert map generation is not possible at the moment. "
            "This feature is temporarily disabled for development testing.",
        )
    def _get_himawari_product_for_alert(self, *args, **kwargs):
        return self.alert_controller._get_himawari_product_for_alert(*args, **kwargs)
    def _generate_whole_alert_map(self, *args, **kwargs):
        QMessageBox.warning(
            self,
            "Alert Map",
            "Alert map generation is not possible at the moment. "
            "This feature is temporarily disabled for development testing.",
        )
    def _redraw_pagasa_only(self):
        scene = self.graphics_view.scene()
        if not scene:
            self.overlay_controller.update_overlays()
            return
        if not hasattr(self, '_ol_project_func'):
            self.overlay_controller.update_overlays()
            return
        for item in getattr(self, '_pagasa_overlay_items', []):
            try:
                if item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        self._pagasa_overlay_items.clear()
        self.overlay_controller._draw_pagasa_overlays(self._ol_project_func, scene)
        self.graphics_view.viewport().update()

    def _draw_pagasa_overlays(self, *args, **kwargs):
        return self.overlay_controller._draw_pagasa_overlays(*args, **kwargs)
    def _sync_jtwc_on_startup(self, *args, **kwargs):
        return self.forecast_controller._sync_jtwc_on_startup(*args, **kwargs)
    def _draw_jtwc_overlays(self, *args, **kwargs):
        return self.overlay_controller._draw_jtwc_overlays(*args, **kwargs)
    def _remove_point_info_box(self, *args, **kwargs):
        return self.overlay_controller._remove_point_info_box(*args, **kwargs)
    def _show_point_info_box(self, *args, **kwargs):
        return self.overlay_controller._show_point_info_box(*args, **kwargs)
    def _draw_jma_overlays(self, *args, **kwargs):
        return self.overlay_controller._draw_jma_overlays(*args, **kwargs)
    def _upsert_nhc_track_entry(self, *args, **kwargs):
        return self.forecast_controller._upsert_nhc_track_entry(*args, **kwargs)
    def _download_update_for_storm(self, *args, **kwargs):
        return self.forecast_controller._download_update_for_storm(*args, **kwargs)
    def _generate_forecast_map_for_storm(self, *args, **kwargs):
        return self.forecast_controller._generate_forecast_map_for_storm(*args, **kwargs)
    def _open_combined_forecast_dialog(self, *args, **kwargs):
        return self.forecast_controller._open_combined_forecast_dialog(*args, **kwargs)
    def _launch_combined_forecast(self, *args, **kwargs):
        return self.forecast_controller._launch_combined_forecast(*args, **kwargs)
    def _update_selected_nhc_track(self, *args, **kwargs):
        return self.forecast_controller._update_selected_nhc_track(*args, **kwargs)
    def _launch_forecast_worker(self, *args, **kwargs):
        return self.forecast_controller._launch_forecast_worker(*args, **kwargs)
    def _on_forecast_finished(self, *args, **kwargs):
        return self.forecast_controller._on_forecast_finished(*args, **kwargs)
    def _update_forecast_progress(self, *args, **kwargs):
        return self.forecast_controller._update_forecast_progress(*args, **kwargs)
    def _generate_selected_nhc_forecast(self, *args, **kwargs):
        return self.forecast_controller._generate_selected_nhc_forecast(*args, **kwargs)
    def _sync_nhc_storms_on_startup(self, *args, **kwargs):
        return self.forecast_controller._sync_nhc_storms_on_startup(*args, **kwargs)
    def _sync_jma_on_startup(self, *args, **kwargs):
        return self.forecast_controller._sync_jma_on_startup(*args, **kwargs)
    def _show_new_track_dialog(self, *args, **kwargs):
        return self.forecast_controller._show_new_track_dialog(*args, **kwargs)
    def _open_forecast_dialog(self, *args, **kwargs):
        return self.forecast_controller._open_forecast_dialog(*args, **kwargs)
    def _add_temp_point_as_track(self, *args, **kwargs):
        return self.forecast_controller._add_temp_point_as_track(*args, **kwargs)
    def _set_pro_mode(self, mode: str):
        m = "single" if mode == "single" else "rgb"
        if hasattr(self, '_switch_devkit_mode'):
            self._switch_devkit_mode(m)

    def _pro_apply_grayscale(self):
        if hasattr(self, 'devkit_band1_combos') and self.devkit_band1_combos:
            band = self.devkit_band1_combos[0].currentText() if self.devkit_band1_combos else ""
            for c in self.devkit_band1_combos[1:]:
                c.setCurrentText(band)
        if hasattr(self, 'pro_status'):
            self.pro_status.setText("Grayscale (all channels = first)")

    def _pro_apply_natural_rgb(self):

        if hasattr(self, 'devkit_preset_combo'):
            self.devkit_preset_combo.setCurrentText("natural")
        if hasattr(self, 'pro_status'):
            self.pro_status.setText("Natural Color preset applied via channel editor.")

    def _pro_swap_channels(self, ch1: str, ch2: str):

        if hasattr(self, '_devkit_mode') and self._devkit_mode == "rgb" and hasattr(self, 'devkit_band1_combos'):

            idx_map = {ch: i for i, ch in enumerate(["R", "G", "B"])}
            i1, i2 = idx_map.get(ch1, 0), idx_map.get(ch2, 1)
            if 0 <= i1 < len(self.devkit_band1_combos) and 0 <= i2 < len(self.devkit_band1_combos):
                v1 = self.devkit_band1_combos[i1].currentText()
                v2 = self.devkit_band1_combos[i2].currentText()
                self.devkit_band1_combos[i1].setCurrentText(v2)
                self.devkit_band1_combos[i2].setCurrentText(v1)
        if hasattr(self, 'pro_status'):
            self.pro_status.setText(f"Swapped {ch1}?{ch2} in channel grid.")

    def _populate_professional_bands(self, *args, **kwargs):
        return self.satellite_controller._populate_professional_bands(*args, **kwargs)
    def _render_professional_composite(self):
        if hasattr(self, 'generate_devkit_composite'):
            self.generate_devkit_composite()

    def _render_professional_from_grid(self):
        if hasattr(self, 'generate_devkit_composite'):
            self.generate_devkit_composite()

    def _load_single_band_pro(self, band: str):
        if hasattr(self, 'band_checkboxes') and band in self.band_checkboxes:
            for b, cb in self.band_checkboxes.items():
                cb.setChecked(b == band)
        if hasattr(self, 'pro_status'):
            self.pro_status.setText(f"Single band focus: {band}")

    def _reset_to_scene_view(self):
        if hasattr(self, 'right_tab_widget'):
            self.right_tab_widget.setCurrentIndex(0)

    def _draw_winds_overlay(self, *args, **kwargs):
        return self.overlay_controller._draw_winds_overlay(*args, **kwargs)

    def _on_amv_pass_changed(self):
        """Re-render the wind overlay when the ASCAT pass selection changes."""
        try:
            self._winds_persist = False
        except Exception:
            pass
        try:
            for c in (self._wind_proj_cache,):
                if isinstance(c, dict):
                    c.clear()
        except Exception:
            pass
        try:
            oc = getattr(self, "overlay_controller", None)
            if oc is not None:
                oc._wind_proj_cache = {}
        except Exception:
            pass
        try:
            self._draw_winds_overlay()
        except Exception as e:
            self.log(f"winds redraw (pass change) error: {e}")

    def _populate_ascat_passes(self, passes_meta):
        """Rebuild the ASCAT pass dropdown from pass metadata.

        Item 0 stays 'All Available Pass' (no filtering); then one entry per
        downloaded pass numbered oldest-first so a higher number = newer pass.
        The previous selection is preserved when it still exists.
        """
        combo = getattr(self, "amv_pass_combo", None)
        if combo is None:
            return
        passes_meta = [p for p in (passes_meta or []) if p.get("pass_no") is not None]
        passes_meta = sorted(passes_meta, key=lambda p: int(p["pass_no"]))
        prior = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All Available Pass", None)
        for p in passes_meta:
            dt = p.get("dt")
            ts = dt.strftime("%Y-%m-%d %H:%MZ") if hasattr(dt, "strftime") else str(dt or "")
            sat = (p.get("satellite") or "").strip()
            label = f"Pass {int(p['pass_no'])} — {ts}"
            if sat:
                label += f" · {sat}"
            combo.addItem(label, int(p["pass_no"]))
        if prior is not None:
            ix = combo.findData(prior)
            if ix >= 0:
                combo.setCurrentIndex(ix)
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _load_local_ascat_passes(self):
        """Populate the pass dropdown from ASCAT swath files on disk."""
        try:
            from src.clients.ascat import list_local_swath_passes
            self._populate_ascat_passes(list_local_swath_passes())
        except Exception as e:
            self.log(f"Local ASCAT pass list failed: {e}")

    def _draw_microwave_overlay(self, *args, **kwargs):
        return self.overlay_controller._draw_microwave_overlay(*args, **kwargs)
    def _build_proj4_from_geos_params(self, *args, **kwargs):
        return self.projection._build_proj4_from_geos_params(*args, **kwargs)
    def _is_beta_viewport(self, *args, **kwargs):
        return self.satellite_controller._is_beta_viewport(*args, **kwargs)
    def _get_ref_grid_size(self, *args, **kwargs):
        return self.satellite_controller._get_ref_grid_size(*args, **kwargs)
    def _find_ads_sidecar(self, *args, **kwargs):
        return self.projection._find_ads_sidecar(*args, **kwargs)
    def extract_crs_from_ads(self, *args, **kwargs):
        crs, gt = self.projection.extract_crs_from_ads(*args, **kwargs)
        gt0 = gt
        gt = self._corrected_subarea_gt(args[0] if args else kwargs.get("nc_path"), gt)
        try:
            _p = crs.to_proj4() if crs is not None else "None"
            _c = bool(gt is not None and gt0 is not None and list(gt) != list(gt0))
            self.log(f"[DIAG-crs] final crs={_p} gt={gt} corrected={_c}")
        except Exception:
            pass
        return crs, gt
    def _corrected_subarea_gt(self, nc_path, gt):
        """Correct the geotransform of a sub-area NC (Target/Japan/Meso) using
        JMA HSD scanning-angle geolocation anchored to the nearest full-disk
        sidecar. Full-disk (FLDK) geotransforms are left untouched."""
        try:
            if nc_path is None or gt is None:
                return gt
            ac = getattr(self, "animation_controller", None)
            if ac is None or not hasattr(ac, "_scan_angle_target_gt"):
                return gt
            nav = ac._read_coff_loff(nc_path)
            if nav is None:
                return gt
            ref_gt = ac._scene_geo_gt(nc_path)
            if ref_gt is None:
                return gt
            out = ac._scan_angle_target_gt(nav, ref_gt, 0, 0, gt)
            if out is None:
                return gt
            if abs(out.c - float(gt.c)) < 2 * abs(float(gt.a)) and \
               abs(out.f - float(gt.f)) < 2 * abs(float(gt.e)):
                return gt
            try:
                self.log(f"[CRS] Sub-area {nc_path.name}: corrected to HSD scan-angle geotransform")
            except Exception:
                pass
            return out
        except Exception:
            return gt
    def _extract_crs_from_nc(self, *args, **kwargs):
        return self.projection._extract_crs_from_nc(*args, **kwargs)
    def _load_ir_kelvin(self, nc_path: Path):
        try:
            b13_path = getattr(self, '_band_nc_map', {}).get("B13", nc_path)
            with xr.open_dataset(b13_path, engine="netcdf4", mask_and_scale=False) as ds:
                if "B13" not in ds:
                    self.log("B13 not found for Kelvin hover.")
                    return None
                arr = ds["B13"].values.astype(np.float32)
                h, w = arr.shape
                max_px = self.preview_max_px
                if max(h, w) > max_px:
                    step = max(1, max(h, w) // max_px)
                    new_h = int(np.ceil(h / step))
                    new_w = int(np.ceil(w / step))
                    arr = zoom(arr, (new_h / h, new_w / w), order=1)
                scale = ds["B13"].attrs.get("scale_factor", 1.0)
                offset = ds["B13"].attrs.get("add_offset", 0.0)
                kelvin = arr * scale + offset
                kelvin = np.where((kelvin < 150) | (kelvin > 350), np.nan, kelvin)
                self.log(f"IR Kelvin array loaded: shape {kelvin.shape}, range {np.nanmin(kelvin):.1f}-{np.nanmax(kelvin):.1f} K")
                return kelvin.astype(np.float32)
        except Exception as e:
            self.log(f"Failed to load IR Kelvin: {e}")
            return None

    def estimate_and_choose_cache_bands(self, nc_path, all_band_names):
        sample_band = all_band_names[0]
        _eng = get_engine(self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9")
        sample_arr = _eng.band_as_image(nc_path, sample_band, self.preview_max_px)
        if sample_arr is None:
            return all_band_names, self.settings.get("cache_size_mb", _default_cache_mb())

        per_band_bytes = sample_arr.nbytes
        total_est_mb = (per_band_bytes * len(all_band_names)) / (1024 * 1024)
        configured_limit_mb = self.settings.get("cache_size_mb", _default_cache_mb())
        effective_limit_mb = configured_limit_mb

        allow_extra = False
        free_mb = 0
        if HAS_PSUTIL:
            mem = psutil.virtual_memory()
            if mem.percent <= 40 and mem.total >= 8*1024**3:
                allow_extra = True
                free_mb = mem.available / (1024*1024)

        force_full = self.settings.get("force_full_cache", False)
        if force_full:
            self.log("Force full band cache enabled -- ignoring RAM estimate.")
            return all_band_names, effective_limit_mb + 2000

        if total_est_mb <= effective_limit_mb:
            return all_band_names, effective_limit_mb
        elif allow_extra and total_est_mb <= effective_limit_mb + 1000:
            effective_limit_mb += 1000
            self.log(f"Allowing extra 1000 MB (system RAM free: {free_mb:.0f} MB)")
            return all_band_names, effective_limit_mb
        else:
            ir_bands = {"B13", "B14", "B15"}
            filtered_bands = [b for b in all_band_names if b not in ir_bands]
            new_est_mb = (per_band_bytes * len(filtered_bands)) / (1024*1024)
            if new_est_mb <= effective_limit_mb:
                self.log(f"Excluding IR bands ({ir_bands}) to fit RAM limit.")
                return filtered_bands, effective_limit_mb
            else:
                self.log(f"RAM limit too tight after excluding IR bands. Will cache only {len(filtered_bands)} bands.")
                return filtered_bands, effective_limit_mb

    def start_precache(self, nc_path, band_names):
        bands_to_cache, effective_limit = self.estimate_and_choose_cache_bands(nc_path, band_names)

        if self.cache_progress_bar:
            self.status_bar.removeWidget(self.cache_progress_bar)
            self.cache_progress_bar.deleteLater()
            self.cache_progress_bar = None

        self.cache_progress_bar = QProgressBar()
        self.cache_progress_bar.setRange(0, len(bands_to_cache))
        self.cache_progress_bar.setValue(0)
        self.cache_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #FF9800;
                border-radius: 3px;
                text-align: center;
                background: #2A2A2A;
                color: #FFF;
            }
            QProgressBar::chunk {
                background: #FF9800;
                border-radius: 2px;
            }
        """)
        self.cache_progress_bar.setFixedWidth(200)
        self.status_bar.addPermanentWidget(self.cache_progress_bar)
        self.status_bar.showMessage(f"Caching {len(bands_to_cache)} bands (limit {effective_limit:.0f} MB)...")

        self.precache_thread = QThread()
        bfm = getattr(self, '_band_nc_map', {}) or None
        self.precache_worker = PrecacheWorker(nc_path, bands_to_cache, self.preview_max_px, band_file_map=bfm)
        self.precache_worker.moveToThread(self.precache_thread)
        self.precache_worker.band_cached.connect(self.on_band_cached)
        self.precache_worker.progress.connect(self.on_precache_progress)
        self.precache_worker.finished.connect(self.on_precache_finished)
        self.precache_thread.started.connect(self.precache_worker.run)
        self.precache_worker.finished.connect(self.precache_thread.quit)
        self.precache_worker.finished.connect(self.precache_worker.deleteLater)
        self.precache_thread.finished.connect(self.precache_thread.deleteLater)
        self.precache_thread.start()

    def on_band_cached(self, *args, **kwargs):
        return self.satellite_controller.on_band_cached(*args, **kwargs)
    def on_precache_progress(self, *args, **kwargs):
        return self.satellite_controller.on_precache_progress(*args, **kwargs)
    def on_precache_finished(self, *args, **kwargs):
        return self.satellite_controller.on_precache_finished(*args, **kwargs)
    def _restyle_band_checkboxes(self):
        t = getattr(self, '_theme', THEMES["Dark (Default)"])
        cb_style = f"""
            QCheckBox {{ color: {t['fg2']}; padding: 3px; font-size: 10px;
                background: {t['bg3']}; border-radius: 3px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; }}
            QCheckBox:hover:!disabled {{ background: {t['menusel']}; }}
        """
        for cb in self.band_checkboxes.values():
            cb.setStyleSheet(cb_style)

    def _build_band_checkboxes(self, band_names):
        for cb in getattr(self, 'band_checkboxes', {}).values():
            cb.deleteLater()
        if hasattr(self, 'band_checkboxes'):
            self.band_checkboxes.clear()
        
        if hasattr(self, 'bands_grid_layout'):
            while self.bands_grid_layout.count():
                item = self.bands_grid_layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            row, col = 0, 0
            max_cols = 4
            is_goes = "goes" in self.sat_combo.currentText().lower() if hasattr(self, 'sat_combo') else False
            for band in band_names:
                label = band
                if is_goes and band.startswith("B") and len(band) == 3:
                    label = f"C{band[1:]}"
                cb = QCheckBox(label)
                cb.toggled.connect(self.on_band_checkbox_toggled)
                self.bands_grid_layout.addWidget(cb, row, col)
                if hasattr(self, 'band_checkboxes'):
                    self.band_checkboxes[band] = cb
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1
            if hasattr(self, 'type_combo') and self.type_combo.currentText() == "Full Disk":
                for i in range(max_cols):
                    self.bands_grid_layout.setColumnStretch(i, 1)
            self._restyle_band_checkboxes()
            self.update_selected_bands_label()
            self._build_product_tiles()

        if band_names:
            _preferred = ["B01", "B02", "B04", "B05", "B06", "B07", "B08",
                          "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]
            first_band = next((b for b in _preferred if b in band_names), band_names[0])
            cb = self.band_checkboxes.get(first_band)
            if cb and not cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)

    @staticmethod
    def _extract_goes_bands_from_folder(data_folder: Path):
        """Scan GOES NC files in folder, extract channel from filename, return band list and band->file mapping."""
        import re
        band_nc_map = {}
        files = list(data_folder.glob("*_ABI*.nc")) + list(data_folder.glob("*.dat")) + list(data_folder.glob("*.DAT"))
        for f in files:
            m = re.search(r'C(\d{2})', f.name)
            if m:
                band_name = f"B{m.group(1)}"
                band_nc_map[band_name] = f
        if not band_nc_map:
            files = list(data_folder.glob("*.nc")) + list(data_folder.glob("*.dat")) + list(data_folder.glob("*.DAT"))
            for f in files:
                m = re.search(r'C(\d{2})', f.name)
                if m:
                    band_name = f"B{m.group(1)}"
                    band_nc_map[band_name] = f
        return band_nc_map

    @staticmethod
    def _extract_gk2a_bands_from_folder(data_folder: Path):
        """Scan GK-2A NetCDF files in a folder and map B01..B16 -> file.

        GK-2A AMI L1B downloads are native per-band NetCDF files named
        gk2a_ami_le1b_<code>_fd|la<nnn>ge_<YYYYMMDDHHMM>.nc (see Process_dat.py).
        Band codes come from src.parsers.gk2a_parser.GK2A_BAND_MAP.
        """
        from src.parsers.gk2a_parser import extract_gk2a_bands
        return extract_gk2a_bands(list(data_folder.glob("*.nc")))

    def _load_himawari_hsd_satpy(self, *args, **kwargs):
        return self.satellite_controller._load_himawari_hsd_satpy(*args, **kwargs)
    def load_bands_for_date(self, *args, **kwargs):
        return self.satellite_controller.load_bands_for_date(*args, **kwargs)
    def load_date_metadata(self, *args, **kwargs):
        return self.satellite_controller.load_date_metadata(*args, **kwargs)
    def _start_raw_band_cache(self, nc_path, band_names, band_file_map=None):

        if not band_names:
            return

        if getattr(self, '_raw_cache_running', False):
            self.log(f"[Cache] Re-entering _start_raw_band_cache")

        self._raw_cache_running = True
        try:
            old_workers = getattr(self, '_active_cache_workers', [])
            old_threads = getattr(self, '_active_cache_threads', [])
            for w in old_workers:
                if w:
                    try:
                        w.cancel()
                    except RuntimeError:
                        pass
            for t in old_threads:
                if t is None:
                    continue
                try:
                    alive = t.isRunning()
                except RuntimeError:
                    alive = False
                if alive:
                    try:
                        t.quit()
                        t.wait(2000)
                    except RuntimeError:
                        pass
            self._active_cache_workers = []
            self._active_cache_threads = []

            selected_band = next((b for b, cb in getattr(self, 'band_checkboxes', {}).items() if cb.isChecked()), None)
            ordered = list(band_names)
            if selected_band and selected_band in ordered:
                ordered.remove(selected_band)
                ordered.insert(0, selected_band)
            if "B03" in ordered:
                ordered.remove("B03")
                ordered.append("B03")

            bfm = band_file_map or getattr(self, '_band_nc_map', {}) or {}

            is_multi_file = bool(bfm) and len(bfm) > 1
            if is_multi_file and len(ordered) > 1:
                n_workers = min(self.max_threads, len(ordered), 4)
                chunks = [ordered[i::n_workers] for i in range(n_workers)]
                self.log(f"Parallel raw cache: {len(ordered)} bands across {n_workers} workers")
            else:
                chunks = [ordered]
                n_workers = 1

            self._raw_cache_threads_running = 0
            self._raw_bands_processed = set()
            self._active_cache_threads = []
            self._suppress_display_until_cache_done = True
            self._auto_display_after_cache = True
            self.log(f"Display suppressed until raw cache fully completes (no auto-display of priority band)")

            total_bands = len(ordered)
            _completed = [0]

            def _make_progress_fn():
                def update_dialog_progress(done, total):
                    _completed[0] += done
                    pct = int((_completed[0] / total_bands) * 100) if total_bands > 0 else 0
                    if getattr(self, 'loading_dialog', None):
                        self.loading_dialog.update_progress(f"Caching band {_completed[0]}/{total_bands}...", pct)
                return update_dialog_progress

            _workers = []
            _target_grid = None
            try:
                _gq = self._get_quality_grid()
                _gt = getattr(self, 'current_geotransform', None)
                if _gq is not None and _gt is not None and len(_gq) >= 4 and _gq[0] > 0 and _gq[1] > 0:
                    _half = _gq[3]
                    _is_fldk = abs(_gt.c + _half) < abs(_half) * 0.01 and abs(_gt.f - _half) < abs(_half) * 0.01
                    if _is_fldk:
                        _target_grid = (int(_gq[0]), int(_gq[1]))
            except Exception:
                _target_grid = None
            for i, chunk in enumerate(chunks):
                if not chunk:
                    continue
                thread = QThread()
                worker = RawBandCacheWorker(nc_path, chunk, self.preview_max_px,
                                            priority_band=selected_band if i == 0 else None,
                                            precompute_display=True,
                                            band_file_map=bfm, ref_grid_size=self._ref_grid_size,
                                            target_grid=_target_grid)
                worker.moveToThread(thread)
                worker.band_cached.connect(self._on_raw_band_cached)
                worker.display_ready.connect(self._on_display_ready)
                worker.finished.connect(self._on_raw_cache_finished)

                if getattr(self, 'loading_dialog', None):
                    worker.progress.connect(_make_progress_fn())

                thread.started.connect(worker.run)
                worker.finished.connect(thread.quit)
                worker.finished.connect(worker.deleteLater)
                _workers.append((worker, thread))
                self._raw_cache_threads_running += 1

            if _workers:
                self._active_cache_workers = [w for w, _ in _workers]
                for worker, thread in _workers:
                    self._active_cache_threads.append(thread)
                    thread.start()
                def _on_cache_thread_finished(t):
                    try:
                        if t in self._active_cache_threads:
                            self._active_cache_threads.remove(t)
                    except (RuntimeError, ValueError):
                        pass
                    try:
                        t.deleteLater()
                    except RuntimeError:
                        pass
                for worker, thread in _workers:
                    thread.finished.connect(lambda t=thread: _on_cache_thread_finished(t))
                self._cache_watchdog = QTimer(self)
                self._cache_watchdog.setSingleShot(True)
                self._cache_watchdog.timeout.connect(lambda: self.satellite_controller._force_finish_cache("watchdog timeout"))
                self._cache_watchdog.start(120000)
                self.log(f"Raw band cache started: {len(ordered)} bands across {len(_workers)} worker(s), priority={selected_band}.")
            else:
                self.log(f"No bands to cache.")
        finally:
            self._raw_cache_running = False

    def _goes_preload_bands(self, band_names, priority_band=None):
        gc = getattr(self, '_goes_cache', None)
        if gc is None:
            self._start_raw_band_cache(self.current_nc_path, band_names, band_file_map=getattr(self, '_band_nc_map', {}))
            return
        ordered = list(band_names)
        if priority_band and priority_band in ordered:
            ordered.remove(priority_band)
            ordered.insert(0, priority_band)
        total = len(ordered)
        self.log(f"[GOES] Preloading {total} bands via GOES cache (parallel)...")
        nc_path = self.satellite_controller._current_nc_file()
        is_goes = True
        def _label(b):
            return f"C{b[1:]}" if is_goes and b.startswith("B") and len(b) == 3 else b

        completed = set()
        results = {}

        def _update_progress():
            done = len(completed)
            pct = int((done / total) * 100) if total > 0 else 0
            def _s(b):
                return "[x]" if b in completed else ("[....]" if b == ordered[min(done, total-1)] else "[    ]")
            lines = [f"Caching bands: {done}/{total} ({pct}%)"]
            if total <= 8:
                for b in ordered:
                    lines.append(f"  {_s(b)} {_label(b)}")
            else:
                half = (total + 1) // 2
                for i in range(half):
                    left = f"{_s(ordered[i])} {_label(ordered[i])}"
                    right = f"{_s(ordered[i + half])} {_label(ordered[i + half])}" if i + half < total else ""
                    lines.append(f"  {left}  {right}" if right else f"  {left}")
            label_text = "\n".join(lines)
            if hasattr(self, 'loading_dialog') and self.loading_dialog:
                self.loading_dialog.update_progress(label_text, pct)

        def _load_band(band):
            try:
                disp = gc.load_band_for_display(band)
                return band, disp
            except Exception as e:
                self.log(f"[GOES] Error loading {band}: {e}")
                return band, None

        with ThreadPoolExecutor(max_workers=min(self.max_threads, total)) as pool:
            futures = {pool.submit(_load_band, band): band for band in ordered}
            for future in futures:
                try:
                    band, disp = future.result()
                    if disp is not None:
                        self.cache.put_precached(band, disp)
                        self._on_display_ready(band, disp)
                    completed.add(band)
                    _update_progress()
                    QApplication.processEvents()
                except Exception as e:
                    self.log(f"[GOES] Unexpected error loading band: {e}")

        self.log("[GOES] Preload complete")
        self._suppress_display_until_cache_done = False
        self.log("Display suppression released -- triggering first display")
        if getattr(self, 'loading_dialog', None):
            try:
                self.loading_dialog.set_final(f"All {total} bands cached -- ready.")
                QApplication.processEvents()
                self.loading_dialog.close()
                self.loading_dialog = None
            except Exception:
                pass
        try:
            self.satellite_controller.load_selected_band_or_product()
        except Exception:
            pass
        if hasattr(self, 'update_overlays'):
            try:
                self.overlay_controller.update_overlays()
            except Exception:
                pass

    def _start_overlay_precache(self, *args, **kwargs):
        return self.overlay_controller._start_overlay_precache(*args, **kwargs)
    def _on_precached_grid(self, *args, **kwargs):
        return self.overlay_controller._on_precached_grid(*args, **kwargs)
    def _build_and_store_grid_item(self, *args, **kwargs):
        return self.overlay_controller._build_and_store_grid_item(*args, **kwargs)
    def _on_precached_coast(self, *args, **kwargs):
        return self.overlay_controller._on_precached_coast(*args, **kwargs)
    def _build_and_store_coast_item(self, *args, **kwargs):
        return self.overlay_controller._build_and_store_coast_item(*args, **kwargs)
    def _on_raw_band_cached(self, band, arr):
        self.cache.put_raw(band, arr)
        self._raw_bands_processed.add(band)

        self.satellite_controller._update_loading_dialog_progress(band)

    def _on_display_ready(self, band, rgba_arr):
        import time
        from collections import OrderedDict
        if not hasattr(self, '_display_image_cache') or self._display_image_cache is None:
            self._display_image_cache = OrderedDict()
        rgba_arr = self.overlay_controller._prepare_cached_display(rgba_arr)
        self._display_image_cache[band] = {
            'arr': rgba_arr,
            'max_px': self.preview_max_px,
            'shape': rgba_arr.shape[:2],
        }
        self._display_image_cache.move_to_end(band)
        if len(self._display_image_cache) > 16:
            self._display_image_cache.popitem(last=False)

        if getattr(self, '_suppress_display_until_cache_done', False):
            cached = len(self._display_image_cache)
            expected = len(getattr(self, 'available_bands', []))
            self.log(f"[Suppress] {band} display image ready ({cached}/{expected}) -- held until cache finishes")
            return

        try:
            selected_band = next((b for b, cb in getattr(self, 'band_checkboxes', {}).items() if cb.isChecked()), None)
            if band == selected_band and not getattr(self, 'selected_product', None):
                if self.graphics_view.scene() and self.graphics_view.scene().items() and \
                        getattr(self, 'current_base', '').endswith(f'_{band}'):
                    try:
                        self.satellite_controller.load_selected_band_or_product()
                    except Exception:
                        pass
        except Exception:
            pass

        selected_band = next((b for b, cb in getattr(self, 'band_checkboxes', {}).items() if cb.isChecked()), None)
        if band == selected_band and not getattr(self, 'selected_product', None):
            try:
                self.satellite_controller.load_selected_band_or_product()
            except Exception:
                pass

        if (getattr(self, 'current_mode', '') == 'professional' and
                getattr(self, 'devkit_live_cb', None) and self.devkit_live_cb.isChecked() and
                hasattr(self, 'generate_devkit_composite') and len(self.cache.raw) >= 3):
            try:

                if getattr(self, 'devkit_band1_combos', None) and any(c.currentText() in self.cache.raw for c in self.devkit_band1_combos):
                    self.generate_devkit_composite()
            except Exception:
                pass

        if (self.selected_product and not self.generating_product
                and self.settings.get('auto_composite_on_cache', True)):
            _sat_for_prod = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
            info = get_products(_sat_for_prod).get(self.selected_product, {})
            needed = set(info.get("bands", []))
            formula = info.get("formula", {})
            for ch in info.get("channels", []):
                spec = formula.get(ch, {})
                for k in ("band", "band1", "band2"):
                    if k in spec:
                        needed.add(spec[k])
            if needed and all(b in self.cache.raw for b in needed):
                self.log(f"[Auto] All {len(needed)} bands for {self.selected_product} cached -- auto-generating composite")
                if self.selected_product not in self.cache.rgb:
                    self.satellite_controller.generate_rgb_product(self.selected_product)
            else:
                missing = [b for b in needed if b not in self.cache.raw]
                if missing:
                    self.log(f"[Auto] {self.selected_product} waiting for: {missing}")

    def _update_loading_dialog_progress(self, *args, **kwargs):
        return self.satellite_controller._update_loading_dialog_progress(*args, **kwargs)
    def _check_and_close_loading_dialog(self, *args, **kwargs):
        return self.satellite_controller._check_and_close_loading_dialog(*args, **kwargs)
    def _on_raw_cache_finished(self, *args, **kwargs):
        return self.satellite_controller._on_raw_cache_finished(*args, **kwargs)
    def _force_finish_cache(self, *args, **kwargs):
        return self.satellite_controller._force_finish_cache(*args, **kwargs)
    def load_selected_band_or_product(self, *args, **kwargs):
        return self.satellite_controller.load_selected_band_or_product(*args, **kwargs)
    def _generate_goes_true_color(self, *args, **kwargs):
        return self.satellite_controller._generate_goes_true_color(*args, **kwargs)
    def generate_rgb_product(self, *args, **kwargs):
        return self.satellite_controller.generate_rgb_product(*args, **kwargs)
    def on_composite_finished(self, *args, **kwargs):
        return self.satellite_controller.on_composite_finished(*args, **kwargs)
    def _check_product_watchdog(self):
        try:
            if getattr(self, 'generating_product', False) and getattr(self, '_product_disabled_at', 0.0) > 0:
                import time as _t
                elapsed = _t.perf_counter() - self._product_disabled_at
                if elapsed > 15.0:
                    self.log(f"[Watchdog] Product generation stuck {elapsed:.1f}s -- force releasing.")
                    self.generating_product = False
                    self._product_disabled_at = 0.0
                    self._apply_product_tile_availability()
        except Exception:
            pass

    def _display_composite(self, *args, **kwargs):
        return self.satellite_controller._display_composite(*args, **kwargs)
    def _compute_data_latlon_bounds(self):
        """Compute (lat_min, lat_max, lon_min, lon_max) from geotransform + image pixels.

        Projects the four image corners from projection coords to lat/lon.
        Returns None if CRS/geotransform/image are not ready or result is degenerate.
        """
        if not self.current_crs or not self.current_geotransform:
            return None
        scene = self.graphics_view.scene()
        if not scene:
            return None
        # Find image pixmap (lowest z), not overlay (z=20)
        pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
        if not pixmap_item:
            return None
        img_w = pixmap_item.pixmap().width()
        img_h = pixmap_item.pixmap().height()
        if img_w <= 0 or img_h <= 0:
            return None
        from pyproj import Transformer
        tr = Transformer.from_crs(self.current_crs, "EPSG:4326", always_xy=True)
        gt = self.current_geotransform
        corners_px = [(0, 0), (img_w, 0), (0, img_h), (img_w, img_h)]
        lons, lats = [], []
        for col, row in corners_px:
            x_proj = gt.a * col + gt.b * row + gt.c
            y_proj = gt.d * col + gt.e * row + gt.f
            try:
                lon, lat = tr.transform(x_proj, y_proj)
                if not (math.isnan(lon) or math.isnan(lat) or math.isinf(lon) or math.isinf(lat)):
                    lons.append(lon)
                    lats.append(lat)
            except Exception:
                pass
        if len(lons) < 3:
            return None
        lon_min, lon_max = min(lons), max(lons)
        lat_min, lat_max = min(lats), max(lats)
        if (lon_max - lon_min) < 5.0 or (lat_max - lat_min) < 5.0:
            return None
        pad_lon = max(0.5, (lon_max - lon_min) * 0.05)
        pad_lat = max(0.5, (lat_max - lat_min) * 0.05)
        return (lat_min - pad_lat, lat_max + pad_lat,
                lon_min - pad_lon, lon_max + pad_lon)

    def _detect_current_sector(self):
        """Detect current sector from ads.json sidecar, NC filename, or UI controls.

        Returns string: 'FLDK', 'Japan', 'Target', 'CONUS', 'Meso', or None.
        """
        nc_path = self.satellite_controller._current_nc_file()
        if not nc_path:
            return None
        sector = None
        sidecar = self.projection._find_ads_sidecar(nc_path)
        if sidecar is not None:
            try:
                import json as _json
                with open(sidecar, "r", encoding='utf-8') as _f:
                    _ads = _json.load(_f)
                sector = _ads.get("sector", "")
            except Exception:
                pass
        if not sector:
            name = nc_path.name.upper()
            if "FLDK" in name:
                sector = "FLDK"
            elif "JAPAN" in name:
                sector = "Japan"
            elif "TARGET" in name:
                sector = "Target"
            elif "CONUS" in name:
                sector = "CONUS"
            elif "MESO" in name or "RADM" in name:
                sector = "Meso"
        # Check GOES product type
        is_goes = ("goes" in nc_path.name.lower() or
                   getattr(self, 'sat_combo', None) and "goes" in self.sat_combo.currentText().lower())
        if is_goes and not sector:
            prod = getattr(self, 'type_combo', None)
            if prod:
                pval = prod.currentData() or prod.currentText()
                if pval in ("CONUS", "Meso"):
                    sector = pval
        return sector

    def _zoom_to_sector_region(self, *args, **kwargs):
        return self.satellite_controller._zoom_to_sector_region(*args, **kwargs)
    def _build_product_tiles(self):
        while self.products_grid_layout.count():
            item = self.products_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._product_tile_widgets.clear()
        filter_tag = self._product_filter.upper()
        sat = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
        filtered = get_products(sat)
        col_count = 2
        row, col = 0, 0
        for key, info in filtered.items():
            tag = info.get("tag", "ALL")
            if tag == "PROFESSIONAL" and filter_tag != "PROFESSIONAL":
                continue
            if filter_tag != "ALL" and tag != "ALL" and tag != filter_tag:
                continue
            tile = self._make_product_tile(key, info)
            self.products_grid_layout.addWidget(tile, row, col)
            self._product_tile_widgets[key] = tile
            col += 1
            if col >= col_count:
                col = 0
                row += 1
        if self.selected_product and self.selected_product in self._product_tile_widgets:
            self._highlight_tile(self.selected_product)
        self._restyle_product_tiles()
        self._apply_product_tile_availability()

    def _apply_product_tile_availability(self, *args, **kwargs):
        try:
            return self.satellite_controller.refresh_product_tile_availability(*args, **kwargs)
        except Exception:
            return None

    def _restyle_product_tiles(self):
        t = getattr(self, '_theme', THEMES["Dark (Default)"])
        tile_style = f"""
            QPushButton {{ background: {t['bg3']}; border: 1px solid {t['border2']};
                border-radius: 4px; }}
            QPushButton:disabled {{ background: {t['bg2']}; border: 1px solid {t['border3']}; }}
            QPushButton:hover:!checked {{ background: {t['menusel']}; border-color: {t['fg3']}; }}
            QPushButton:checked {{ background: #2A1F4A; border: 1px solid #8B5CF6; }}
        """
        for tile in self._product_tile_widgets.values():
            tile.setStyleSheet(tile_style)
            name_lbl = tile.findChild(QLabel)
            if name_lbl:
                name_lbl.setStyleSheet(f"color: {t['fg2']}; font-size: 10px; font-weight: bold; background: transparent;")

    def _restyle_pro_tab(self):
        t = getattr(self, '_theme', THEMES["Dark (Default)"])

        def gb_style(title_color, border_color, bg_color=None):
            bg = f"background: {bg_color};" if bg_color else ""
            return f"""
                QGroupBox {{ color: {title_color}; font-weight: bold;
                    border: 1px solid {border_color}; border-radius: 4px;
                    margin-top: 4px; padding-top: 6px; {bg} }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; font-size: 9px; }}
            """

        gb_cb_style = f"""
            QCheckBox {{ color: {t['fg2']}; font-size: 9pt; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; }}
            QCheckBox:hover:!disabled {{ background: {t['menusel']}; }}
        """
        small_cb_style = f"""
            QCheckBox {{ color: {t['fg2']}; font-size: 8.5px; }}
            QCheckBox::indicator {{ width: 14px; height: 14px; }}
            QCheckBox:hover:!disabled {{ background: {t['menusel']}; }}
        """
        mode_btn_style = f"""
            QPushButton {{ background: {t['bg3']}; color: {t['fg2']};
                border: 1px solid {t['border2']}; border-radius: 3px;
                font-size: 9.5px; padding: 2px 8px; min-height: 20px; }}
            QPushButton:checked {{ background: #3D2B6E; color: #E8D8FF;
                border: 1px solid #8B5CF6; font-weight: 600; }}
            QPushButton:hover:!checked {{ background: {t['menusel']}; color: {t['fg']}; }}
        """
        small_btn_style = f"""
            QPushButton {{ background: {t['bg3']}; color: {t['fg2']};
                border: 1px solid {t['border2']}; border-radius: 3px;
                font-size: 8.5px; padding: 2px 6px; min-height: 20px; }}
            QPushButton:checked {{ background: #3D2B6E; color: #E8D8FF;
                border: 1px solid #8B5CF6; }}
            QPushButton:hover:!checked {{ background: {t['menusel']}; color: {t['fg']}; }}
        """

        if hasattr(self, '_bev_group'):
            self._bev_group.setStyleSheet(gb_style(t['fg3'], t['border2'], t['bg2']))
        if hasattr(self, '_devkit_group'):
            self._devkit_group.setStyleSheet(gb_style("#FF9800", t['border2'], t['bg2']))
        if hasattr(self, '_stats_group'):
            self._stats_group.setStyleSheet(gb_style(t['fg3'], t['border2'], t['bg2']))
        if hasattr(self, '_render_group'):
            self._render_group.setStyleSheet(gb_style(t['fg3'], t['border2'], t['bg2']))
        if hasattr(self, '_func_group'):
            self._func_group.setStyleSheet(gb_style(t['fg3'], t['border2'], t['bg2']))
        if hasattr(self, '_gray_group'):
            self._gray_group.setStyleSheet(f"QGroupBox {{ font-size:8.5px; color: {t['fg2']}; border: 1px solid {t['border2']}; margin-top:4px; }}")
        if hasattr(self, '_mv_group'):
            self._mv_group.setStyleSheet(gb_style("#CE93D8", t['border2'], t['bg2']))
        if hasattr(self, '_aor_group'):
            self._aor_group.setStyleSheet(gb_style("#00BCD4", t['border2'], t['bg2']))

        if hasattr(self, '_devkit_mode_btns'):
            for btn in self._devkit_mode_btns.values():
                btn.setStyleSheet(mode_btn_style)
        if hasattr(self, 'func_mode_btns'):
            for btn in self.func_mode_btns.values():
                btn.setStyleSheet(small_btn_style)
        if hasattr(self, 'func_selection_btns'):
            for btn in self.func_selection_btns.values():
                btn.setStyleSheet(small_btn_style)

        if hasattr(self, 'pro_show_stats_cb'):
            self.pro_show_stats_cb.setStyleSheet(gb_cb_style)
        if hasattr(self, 'devkit_live_cb'):
            self.devkit_live_cb.setStyleSheet(gb_cb_style)
        if hasattr(self, 'pro_auto_stretch'):
            self.pro_auto_stretch.setStyleSheet(small_cb_style)
        if hasattr(self, 'pro_equalize'):
            self.pro_equalize.setStyleSheet(small_cb_style)
        if hasattr(self, 'pro_contours'):
            self.pro_contours.setStyleSheet(small_cb_style)
        if hasattr(self, 'func_vis_hour_cb'):
            self.func_vis_hour_cb.setStyleSheet(small_cb_style)
        if hasattr(self, 'func_blue_cb'):
            self.func_blue_cb.setStyleSheet(small_cb_style)
        if hasattr(self, 'func_sandwich_cb'):
            self.func_sandwich_cb.setStyleSheet(small_cb_style)
        if hasattr(self, 'func_gray_revs_cb'):
            self.func_gray_revs_cb.setStyleSheet(small_cb_style)
        if hasattr(self, 'mv_enable_cb'):
            self.mv_enable_cb.setStyleSheet(gb_cb_style)

        _aor_cb_style = f"QCheckBox {{ color:{t['fg2']}; font-size:9pt; }}"
        for cb in (getattr(self, 'aor_par_cb', None), getattr(self, 'aor_jma_cb', None),
                   getattr(self, 'aor_tcid_cb', None), getattr(self, 'aor_fir_cb', None),
                   getattr(self, 'aor_custom_cb', None), getattr(self, 'aor_tcad_cb', None)):
            if cb:
                cb.setStyleSheet(_aor_cb_style)

        if hasattr(self, 'pro_status'):
            self.pro_status.setStyleSheet(f"color:{t['fg3']}; font-size:9.5px; padding-left:2px;")
        if hasattr(self, 'pro_stats_label'):
            self.pro_stats_label.setStyleSheet(f"color:{t['fg2']}; font-size:9pt; background:transparent;")
        if hasattr(self, 'pro_compact_band'):
            self.pro_compact_band.setStyleSheet(f"color:{t['fg2']}; font-size:9pt;")
        if hasattr(self, 'band_lbl'):
            self.band_lbl.setStyleSheet(f"color:#4CAF50; font-size:9pt; font-weight:600;")

        if hasattr(self, 'pro_global_gamma'):
            self.pro_global_gamma.setStyleSheet(f"font-size:8.5px; color:{t['fg2']}; background:{t['bg3']}; border:1px solid {t['border2']};")
        if hasattr(self, 'devkit_preset_combo'):
            self.devkit_preset_combo.setStyleSheet(f"font-size:8.5px; min-width:120px; color:{t['fg2']}; background:{t['bg3']};")

    def _make_product_tile(self, key: str, info: dict) -> QPushButton:
        tag = info.get("tag", "ALL")
        color = get_tag_colors().get(tag, "#1B5E20")
        name = info["name"]
        desc = info["description"]
        tile = QPushButton()
        # Tiles stay clickable: if a required band isn't in RAM yet, the
        # generator pulls it from the animation band cache or reads the NC
        # file on demand instead of blocking the user.
        tile.setCheckable(True)
        if info.get("color_scale"):
            bands_str = "Color scale: " + info["color_scale"].get("colormap", "N/A")
            tip_prefix = ""
        else:
            bands_str = ', '.join(info['bands'])
            tip_prefix = "Bands: "
        tile.setToolTip(f"<b>{name}</b><br>{desc}<br><i>{tip_prefix}{bands_str}</i>")
        tile.setFixedHeight(38)
        tile.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(tile)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(5)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background: {color}; border-radius: 4px;")
        dot.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(dot)
        name_lbl = QLabel(name)
        name_lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(name_lbl, 1)
        badge = QLabel(tag)
        badge.setFixedSize(34, 14)
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(f"""
            background: {color}; color: #EEE;
            border-radius: 3px; font-size: 8px; font-weight: bold;
        """)
        badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(badge)
        tile.setStyleSheet("""
            QPushButton {
                background: #252525;
                border: 1px solid #3A3A3A;
                border-radius: 4px;
            }
            QPushButton:disabled {
                background: #1A1A1A;
                color: #555;
                border: 1px solid #2A2A2A;
            }
            QPushButton:hover:!checked { background: #2E2E2E; border-color: #555; }
            QPushButton:checked {
                background: #2A1F4A;
                border: 1px solid #8B5CF6;
            }
        """)
        tile.clicked.connect(lambda _, k=key: self.on_product_tile_clicked(k))
        return tile

    def _highlight_tile(self, key: str):
        for k, t in self._product_tile_widgets.items():
            t.setChecked(k == key)

    def _set_product_filter(self, label: str):
        self._product_filter = label.upper() if label != "All" else "ALL"
        for lbl, btn in self._product_filter_btns.items():
            btn.setChecked(lbl == label)
        self._build_product_tiles()

    def on_product_tile_clicked(self, key: str):
        import time as _t
        self._click_log = (key, _t.perf_counter())
        self.log(f"Clicked - product:{key} (generating_product={getattr(self, 'generating_product', False)})")
        if self.generating_product:
            self.log(f"  -> product click IGNORED (already generating another product)")
            return
        if self._mp_route_main_selection(product_key=key):
            return
        if self.selected_product == key:
            self.selected_product = None
            self._highlight_tile("")
            for cb in self.band_checkboxes.values():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
            self._anim_sync_product_idx = None
            self._anim_sync_target_meta = None
            frames = getattr(self, '_anim_frames', None) or []
            ac = getattr(self, 'animation_controller', None)
            if ac is not None:
                ac._scene_prod_sync_sig = None
                ac._scene_prod_sync_prod = None
            if ac is not None and frames and 0 <= self._anim_index < len(frames):
                ac._display_anim_frame(self._anim_index)
            else:
                self.load_preview_images()
            self._build_product_tiles()
            return
        self.selected_product = key
        self._highlight_tile(key)
        for cb in self.band_checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        _anim_geo = getattr(self, '_anim_geo_mode', False)
        _tbf = getattr(self, '_anim_target_band_frames', None) or []
        _cur = getattr(self, '_anim_index', None)
        if _anim_geo and _cur is not None and 0 <= _cur < len(_tbf) and _tbf[_cur]:
            _ac = getattr(self, 'animation_controller', None)
            if _ac is not None and hasattr(_ac, '_sync_scene_product_to_anim'):
                try:
                    _ac._sync_scene_product_to_anim(_cur)
                    self._build_product_tiles()
                    return
                except Exception:
                    pass
        self.satellite_controller.generate_rgb_product(key)
        self._build_product_tiles()

    # ----- Climate & Weather Overlay Handlers -----

    def _get_climate_cache_dir(self, *args, **kwargs):
        return self.climate_controller._get_climate_cache_dir(*args, **kwargs)
    def _get_climate_cache_file(self, *args, **kwargs):
        return self.climate_controller._get_climate_cache_file(*args, **kwargs)
    def _fetch_cpc_index(self, *args, **kwargs):
        return self.climate_controller._fetch_cpc_index(*args, **kwargs)
    def _read_climate_cache(self, *args, **kwargs):
        return self.climate_controller._read_climate_cache(*args, **kwargs)
    def _save_climate_cache(self, *args, **kwargs):
        return self.climate_controller._save_climate_cache(*args, **kwargs)
    def _get_hazard_shp_path(self, *args, **kwargs):
        return self.climate_controller._get_hazard_shp_path(*args, **kwargs)
    def _download_climate_data(self, *args, **kwargs):
        return self.climate_controller._download_climate_data(*args, **kwargs)
    def _refresh_all_climate_data(self, *args, **kwargs):
        return self.climate_controller._refresh_all_climate_data(*args, **kwargs)
    def _toggle_climate_overlay(self, *args, **kwargs):
        return self.climate_controller._toggle_climate_overlay(*args, **kwargs)
    def _on_climate_week_changed(self, *args, **kwargs):
        return self.climate_controller._on_climate_week_changed(*args, **kwargs)
    def _remove_climate_overlay(self, *args, **kwargs):
        return self.climate_controller._remove_climate_overlay(*args, **kwargs)
    def _render_climate_overlay(self, *args, **kwargs):
        return self.climate_controller._render_climate_overlay(*args, **kwargs)
    def _render_all_climate_overlays(self, *args, **kwargs):
        return self.climate_controller._render_all_climate_overlays(*args, **kwargs)
    def _pick_climate_color(self, *args, **kwargs):
        return self.climate_controller._pick_climate_color(*args, **kwargs)
    def _reset_climate_colors(self, *args, **kwargs):
        return self.climate_controller._reset_climate_colors(*args, **kwargs)
    def _generate_single_climate_overlay(self, *args, **kwargs):
        return self.climate_controller._generate_single_climate_overlay(*args, **kwargs)
    def _update_climate_progress(self, *args, **kwargs):
        return self.climate_controller._update_climate_progress(*args, **kwargs)
    def _on_single_climate_gen_finished(self, *args, **kwargs):
        return self.climate_controller._on_single_climate_gen_finished(*args, **kwargs)
    def _generate_climate_overlay(self, *args, **kwargs):
        return self.climate_controller._generate_climate_overlay(*args, **kwargs)
    def _get_week_dates_json(self, *args, **kwargs):
        return self.climate_controller._get_week_dates_json(*args, **kwargs)
    def _on_combined_climate_gen_finished(self, *args, **kwargs):
        return self.climate_controller._on_combined_climate_gen_finished(*args, **kwargs)
    def _get_gtwo_cache_dir(self, *args, **kwargs):
        return self.climate_controller._get_gtwo_cache_dir(*args, **kwargs)
    def _get_gtwo_shp_files(self, *args, **kwargs):
        return self.climate_controller._get_gtwo_shp_files(*args, **kwargs)
    def _download_gtwo_data(self, *args, **kwargs):
        return self.climate_controller._download_gtwo_data(*args, **kwargs)
    def _toggle_gtwo_overlay(self, *args, **kwargs):
        return self.climate_controller._toggle_gtwo_overlay(*args, **kwargs)
    def _generate_gtwo_map(self, *args, **kwargs):
        return self.climate_controller._generate_gtwo_map(*args, **kwargs)
    def _on_gtwo_gen_finished(self, *args, **kwargs):
        return self.climate_controller._on_gtwo_gen_finished(*args, **kwargs)
    def _remove_gtwo_overlay(self, *args, **kwargs):
        return self.climate_controller._remove_gtwo_overlay(*args, **kwargs)
    def _render_gtwo_overlay(self, *args, **kwargs):
        return self.climate_controller._render_gtwo_overlay(*args, **kwargs)
    def _gtwo_prob_color(self, *args, **kwargs):
        return self.climate_controller._gtwo_prob_color(*args, **kwargs)
    def _gtwo_prob_color(self, *args, **kwargs):
        return self.climate_controller._gtwo_prob_color(*args, **kwargs)
    def _load_dropped_shapefile(self, shp_path):
        cache_dir = top_dir / "cache" / "dropped_shp"
        cache_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        src = Path(shp_path)
        base = src.stem
        dest_dir = cache_dir / f"{base}_{uuid.uuid4().hex[:8]}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for ext in ['.shp', '.shx', '.dbf', '.prj', '.sbn', '.sbx', '.cpg', '.xml']:
            f = src.with_suffix(ext)
            if f.exists():
                shutil.copy2(str(f), str(dest_dir / f.name))
        dest_shp = dest_dir / f"{base}.shp"
        if not dest_shp.exists():
            self.log(f"Dropped shapefile copy failed: {dest_shp}")
            return
        self._render_dropped_shapefile(str(dest_shp))

    def _remove_dropped_shapefile_overlay(self):
        items = getattr(self, '_dropped_shp_items', [])
        scene = self.graphics_view.scene() if hasattr(self, 'graphics_view') else None
        for item in items:
            try:
                if scene and item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        items.clear()
        self._dropped_shp_items = items

    def _render_dropped_shapefile(self, shp_path):
        if not hasattr(self, 'graphics_view') or not self.graphics_view.scene():
            return
        scene = self.graphics_view.scene()
        if not getattr(self, 'current_crs', None) or not getattr(self, 'current_geotransform', None):
            return
        try:
            import shapefile
            from pyproj import Transformer
            sf = shapefile.Reader(shp_path)
            transform = self.current_geotransform
            crs_proj = self.current_crs
            transformer = Transformer.from_crs("EPSG:4326", crs_proj, always_xy=True)
            pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
            if not pixmap_item:
                sf.close()
                return
            img_w = pixmap_item.pixmap().width()
            img_h = pixmap_item.pixmap().height()
            disk_half_extent = self.overlay_controller._compute_disk_half_extent()
            img_dim = max(img_w, img_h)
            native_res_m = (2 * disk_half_extent) / img_dim if img_dim > 0 else abs(transform.a)
            from PySide6.QtGui import QColor, QPainterPath, QPen, QBrush, QPolygonF
            from PySide6.QtCore import QPointF
            fill_clr = QColor(100, 149, 237, 60)
            edge_clr = QColor(70, 130, 180, 200)
            edge_pen = QPen(edge_clr, 1.5)
            self._remove_dropped_shapefile_overlay()
            self._dropped_shp_items = []
            items = self._dropped_shp_items
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
            self.log(f"Rendered dropped shapefile ({len(items)} polygons)")
        except Exception as e:
            self.log(f"Dropped shapefile render error: {e}")
            import traceback
            traceback.print_exc()

    def _load_dropped_cwa_or_kml(self, path, clear_existing=True):
        path = Path(path)
        if not path.exists():
            self.log(f"Dropped file not found: {path}")
            return
        low = path.name.lower()
        if low.endswith('.kmz'):
            self._load_dropped_kmz(path, clear_existing=clear_existing)
        elif low.endswith('.kml'):
            self._load_dropped_kml_file(path, clear_existing=clear_existing)
        elif low.endswith(('.xml', '.json')):
            try:
                from src.clients.cwa import CWAParser
                entry = CWAParser.parse(str(path))
                if entry is not None:
                    existing = next((t for t in getattr(self, 'tracks', []) if t.get("id") == entry.get("id")), None)
                    if existing is not None:
                        existing["points"] = entry["points"]
                        existing["name"] = entry["name"]
                        self.log(f"CWA track updated: {entry['name']} ({len(entry['points'])} pts)")
                    else:
                        if not hasattr(self, 'tracks'):
                            self.tracks = []
                        self.tracks.append(entry)
                        self.log(f"CWA track added: {entry['name']} ({len(entry['points'])} pts)")
                    from src.core.helpers import _save_tracks_to_disk
                    _save_tracks_to_disk(self.tracks)
                    self._refresh_tracks_list()
                    self._last_overlay_key = None
                    if hasattr(self, '_update_tracks_overlays'):
                        self._update_tracks_overlays()
                    return
                if low.endswith('.xml'):
                    self._load_dropped_kml_file(path, clear_existing=clear_existing)
                    return
            except Exception as e:
                self.log(f"CWA parse error: {e}")
                traceback.print_exc()
                if low.endswith('.xml'):
                    try:
                        self._load_dropped_kml_file(path, clear_existing=clear_existing)
                        return
                    except Exception:
                        pass
            self.log(f"Unrecognized XML/JSON format: {path}")
        else:
            self.log(f"Unsupported file format: {path}")

    def _load_dropped_kml_file(self, path, clear_existing=True):
        try:
            kml_text = Path(path).read_text(encoding="utf-8")
            self._render_kml_overlays(kml_text, f"KML: {path.name}", clear_existing=clear_existing)
        except Exception as e:
            self.log(f"KML parse error: {e}")
            traceback.print_exc()

    def _load_dropped_kmz(self, path, clear_existing=True):
        try:
            kml_text = self._extract_kml_text_from_kmz(path)
            if kml_text is None:
                self.log(f"No KML found in KMZ: {path}")
                return
            self._render_kml_overlays(kml_text, f"KMZ: {path.name}", clear_existing=clear_existing)
        except Exception as e:
            self.log(f"KMZ parse error: {e}")
            traceback.print_exc()

    @staticmethod
    def _extract_kml_text_from_kmz(kmz_path):
        with zipfile.ZipFile(str(kmz_path), 'r') as zf:
            kml_files = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_files:
                return None
            with zf.open(kml_files[0]) as f:
                return f.read().decode("utf-8", errors="replace")

    @staticmethod
    def _kml_ns(root):
        tag = root.tag
        m = re.search(r'\{([^}]+)\}', tag)
        return m.group(1) if m else ""

    @staticmethod
    def _kml_local_tag(element):
        tag = element.tag
        return tag.split("}")[-1] if "}" in tag else tag

    @classmethod
    def _kml_find_tag(cls, element, tag, ns):
        child = element.find(f"{{{ns}}}{tag}") if ns else element.find(tag)
        if child is None and ns:
            child = element.find(tag)
        return child

    @classmethod
    def _kml_parse_coords(cls, coord_text):
        pts = []
        for part in coord_text.strip().split():
            part = part.strip()
            if not part:
                continue
            vals = part.split(",")
            if len(vals) >= 2:
                try:
                    pts.append((float(vals[0]), float(vals[1])))
                except (ValueError, TypeError):
                    continue
        return pts

    @classmethod
    def _kml_get_coords(cls, geom, ns):
        coords_el = cls._kml_find_tag(geom, "coordinates", ns)
        if coords_el is not None and coords_el.text:
            return cls._kml_parse_coords(coords_el.text)
        obi = cls._kml_find_tag(geom, "outerBoundaryIs", ns)
        if obi is not None:
            lr = cls._kml_find_tag(obi, "LinearRing", ns)
            if lr is not None:
                coords_el = cls._kml_find_tag(lr, "coordinates", ns)
                if coords_el is not None and coords_el.text:
                    return cls._kml_parse_coords(coords_el.text)
        mg = cls._kml_find_tag(geom, "MultiGeometry", ns)
        if mg is not None:
            for child in list(mg):
                tag = cls._kml_local_tag(child)
                if tag in ("Point", "LineString", "LinearRing", "Polygon"):
                    result = cls._kml_get_coords(child, ns)
                    if result:
                        return result
        for geom_tag in ("Point", "LineString", "LinearRing"):
            g = cls._kml_find_tag(geom, geom_tag, ns)
            if g is not None:
                coords_el = cls._kml_find_tag(g, "coordinates", ns)
                if coords_el is not None and coords_el.text:
                    return cls._kml_parse_coords(coords_el.text)
        return []

    def _render_kml_overlays(self, kml_text, label, clear_existing=True):
        if not hasattr(self, 'graphics_view') or not self.graphics_view.scene():
            return
        scene = self.graphics_view.scene()
        if not getattr(self, 'current_crs', None) or not getattr(self, 'current_geotransform', None):
            return
        root = ET.fromstring(kml_text)
        ns = self._kml_ns(root)
        pixmap_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None)
        if not pixmap_item:
            return
        img_w = pixmap_item.pixmap().width()
        img_h = pixmap_item.pixmap().height()
        disk_half_extent = self.overlay_controller._compute_disk_half_extent()
        img_dim = max(img_w, img_h)
        native_res_m = (2 * disk_half_extent) / img_dim if img_dim > 0 else abs(self.current_geotransform.a)
        from pyproj import Transformer
        transformer = Transformer.from_crs("EPSG:4326", self.current_crs, always_xy=True)
        if clear_existing:
            self._remove_dropped_kml_overlay()
            self._dropped_kml_items = []
        fill_clr = QColor(100, 149, 237, 60)
        edge_clr = QColor(70, 130, 180, 200)
        edge_pen = QPen(edge_clr, 1.5)
        line_clr = QColor(255, 200, 50, 220)
        line_pen = QPen(line_clr, 2.0)
        dot_clr = QColor(255, 100, 100, 200)

        def project(lon, lat):
            try:
                lon_n = lon if lon <= 180 else lon - 360
                x_proj, y_proj = transformer.transform(lon_n, lat)
                if not (np.isfinite(x_proj) and np.isfinite(y_proj)):
                    return None
                if abs(x_proj) > disk_half_extent * 1.01 or abs(y_proj) > disk_half_extent * 1.01:
                    return None
                with np.errstate(invalid='ignore'):
                    px = (x_proj + disk_half_extent) / native_res_m
                    py = (disk_half_extent - y_proj) / native_res_m
                if px < -1 or px >= img_w + 1 or py < -1 or py >= img_h + 1:
                    return None
                return (px, py)
            except Exception:
                return None

        placemark_count = 0
        path_count = 0
        poly_count = 0
        pt_count = 0
        ns_tag = f"{{{ns}}}" if ns else ""
        for pm in root.iter(f"{ns_tag}Placemark" if ns else "Placemark"):
            placemark_count += 1
            name_el = pm.find(f"{ns_tag}name" if ns else "name")
            pm_name = name_el.text.strip() if name_el is not None and name_el.text else ""
            geom_candidates = []
            for child in list(pm):
                tag = self._kml_local_tag(child)
                if tag in ("Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"):
                    geom_candidates.append(child)
            for geom in geom_candidates:
                tag = self._kml_local_tag(geom)
                coords = self._kml_get_coords(geom, ns)
                if not coords:
                    continue
                projected = []
                for lon, lat in coords:
                    p = project(lon, lat)
                    if p is not None:
                        projected.append(QPointF(p[0], p[1]))
                if len(projected) < 2:
                    continue
                if tag == "Polygon" or tag == "LinearRing":
                    if len(projected) >= 3:
                        poly = QPolygonF(projected)
                        path = QPainterPath()
                        path.addPolygon(poly)
                        item = scene.addPath(path, edge_pen, QBrush(fill_clr))
                        item.setZValue(30)
                        self._dropped_kml_items.append(item)
                        poly_count += 1
                elif tag == "LineString":
                    path = QPainterPath()
                    path.moveTo(projected[0])
                    for pt in projected[1:]:
                        path.lineTo(pt)
                    item = scene.addPath(path, line_pen)
                    item.setZValue(30)
                    self._dropped_kml_items.append(item)
                    path_count += 1
                elif tag == "Point":
                    for qpt in projected:
                        dot = scene.addEllipse(qpt.x() - 3, qpt.y() - 3, 6, 6,
                            QPen(dot_clr, 1), QBrush(dot_clr))
                        dot.setZValue(31)
                        self._dropped_kml_items.append(dot)
                        pt_count += 1
        self.log(f"Rendered {label}: {placemark_count} placemarks, {poly_count} polygons, {path_count} lines, {pt_count} points")

    def _remove_dropped_kml_overlay(self):
        items = getattr(self, '_dropped_kml_items', [])
        scene = self.graphics_view.scene() if hasattr(self, 'graphics_view') else None
        for item in items:
            try:
                if scene and item.scene():
                    scene.removeItem(item)
            except Exception:
                pass
        items.clear()
        self._dropped_kml_items = items

    def _load_dropped_pagasa_dat(self, path):
        path = Path(path)
        if not path.exists():
            self.log(f"Dropped .dat file not found: {path}")
            return
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        except Exception as e:
            self.log(f"Cannot read .dat file: {e}")
            return
        if not lines:
            self.log("Empty .dat file")
            return
        header = lines[0].strip()
        m = re.match(r'^(\w+)\{(\w+)\}$', header)
        if m:
            storm_name = m.group(2)
        else:
            storm_name = path.stem
        points = []
        cat_kmh = {
            "LPA": 45, "TD": 55, "TS": 80, "STS": 120, "TY": 200, "STY": 260,
        }
        for raw in lines[1:]:
            raw = raw.strip()
            if not raw or raw.upper() == "EOF":
                continue
            parts = [p.strip() for p in raw.split(",")]
            if len(parts) < 6:
                continue
            category = parts[0].upper()
            date_str = parts[1]
            time_str = parts[2]
            try:
                lat = float(parts[3])
                lon = float(parts[4])
            except (ValueError, TypeError):
                continue
            try:
                radius_val = float(parts[5])
            except (ValueError, TypeError):
                radius_val = 0
            dt_str = f"{date_str} {time_str}" if ":" in time_str else f"{date_str} {time_str}:00"
            pt = {
                "lon": lon,
                "lat": lat,
                "datetime": dt_str,
                "intensity_category": category,
                "cyclone_type": category,
                "radius_km": radius_val,
            }
            points.append(pt)
        if not points:
            self.log(f"No valid track points in {path.name}")
            return
        storm_id = f"pagasa_dat_{path.stem}"
        existing = next((t for t in getattr(self, 'tracks', []) if t.get("id") == storm_id), None)
        entry = {
            "id": storm_id,
            "name": f"{storm_name} (PAGASA)",
            "type": "Tropical Cyclone",
            "year": points[0]["datetime"][:4] if points else "2026",
            "basin": "Western Pacific",
            "notes": f"PAGASA track from {path.name}",
            "color": "#00E676",
            "visible": True,
            "add_to_infobox": False,
            "display_options": {
                "show_track_line": True, "show_points": True,
                "show_cone": True, "cone_mode": "pagasa_standard",
                "show_wind_radii": False, "show_best_track": False,
                "show_labels": True, "show_label_name": True, "show_label_time": True,
                "show_label_speed": True, "show_label_category": True,
            },
            "points": points,
        }
        if existing is not None:
            idx = self.tracks.index(existing)
            self.tracks[idx] = entry
            self.log(f"PAGASA track updated: {storm_name} ({len(points)} pts)")
        else:
            if not hasattr(self, 'tracks'):
                self.tracks = []
            self.tracks.append(entry)
            self.log(f"PAGASA track added: {storm_name} ({len(points)} pts)")
        from src.core.helpers import _save_tracks_to_disk
        _save_tracks_to_disk(self.tracks)
        self._refresh_tracks_list()
        self._last_overlay_key = None
        if hasattr(self, '_update_tracks_overlays'):
            self._update_tracks_overlays()
        self.status_bar.showMessage(f"Loaded PAGASA track: {storm_name} ({len(points)} points)", 5000)

    def _current_nc_file(self, *args, **kwargs):
        return self.satellite_controller._current_nc_file(*args, **kwargs)
    def update_band_info(self):

        selected_band = next((b for b, cb in self.band_checkboxes.items() if cb.isChecked()), None) if hasattr(self, 'band_checkboxes') else None

        scene_text = "Load scene + select band for details."
        pro_text = "No band selected."

        if selected_band and self.satellite_controller._current_nc_file():
            nc_path = self.satellite_controller._current_nc_file()
            sidecar = self.projection._find_ads_sidecar(nc_path)
            if sidecar is not None:
                try:
                    with open(sidecar, "r", encoding='utf-8') as f:
                        ads = json.load(f)
                    bands_stats = ads.get("band_stats", {})
                    stats = bands_stats.get(selected_band, {})
                    wavelength = stats.get("wavelength", ["N/A"])
                    if isinstance(wavelength, list) and len(wavelength) >= 3:
                        wave_str = f"{wavelength[0]} - {wavelength[2]} {wavelength[3] if len(wavelength)>3 else 'µm'}"
                    else:
                        wave_str = str(wavelength)
                    units = stats.get("units", "N/A")
                    pro_text = f"<b>{selected_band}</b><br>Wavelength: {wave_str}<br>Units: {units}"
                    
                    # Enhanced metadata display
                    obs_time = ads.get("observation_time", ads.get("start_time", "N/A"))
                    obs_duration = ads.get("observation_duration", ads.get("duration", "N/A"))
                    data_kind = ads.get("kind", ads.get("data_kind", "N/A"))
                    family = ads.get("family", ads.get("fam", "N/A"))
                    category = ads.get("category", ads.get("cat", "N/A"))
                    
                    # Format observation time if it's a datetime
                    if isinstance(obs_time, str) and "T" in obs_time:
                        try:
                            from datetime import datetime
                            dt = datetime.fromisoformat(obs_time.replace("Z", "+00:00"))
                            obs_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            pass  # Keep original format if parsing fails
                    
                    scene_text = f"OBS: {obs_time} | DUR: {obs_duration} | WL: {wave_str} | KIND: {data_kind} | FAM: {family} | CAT: {category}"
                    # Also enhance pro_text with some additional info
                    pro_text = f"<b>{selected_band}</b><br>Wavelength: {wave_str}<br>Units: {units}<br>Observation: {obs_time}<br>Kind: {data_kind}<br>Family: {family}"
                except Exception as e:
                    pro_text = f"Error: {e}"
                    scene_text = "Sidecar read error."
            else:
                pro_text = "ADS sidecar not found."
                scene_text = f"{selected_band} (no sidecar metadata)"

        if hasattr(self, 'pro_compact_band') and self.pro_compact_band:
            compact = selected_band or "--"
            if 'wave_str' in locals():
                compact += f" • {wave_str.split()[0] if wave_str else ''}"
            self.pro_compact_band.setText(compact)

        if hasattr(self, 'scene_band_info_lbl') and self.scene_band_info_lbl:
            self.scene_band_info_lbl.setText(scene_text)

        if hasattr(self, 'band_info_panel') and self.band_info_panel and getattr(self.band_info_panel, 'setHtml', None):
            try:
                if self.current_mode == "professional":
                    self.band_info_panel.setHtml(pro_text)
                else:
                    self.band_info_panel.setText("Pro mode only.")
            except Exception:
                pass

    def toggle_grid(self, *args, **kwargs):
        return self.overlay_controller.toggle_grid(*args, **kwargs)
    def toggle_coastlines(self, *args, **kwargs):
        return self.overlay_controller.toggle_coastlines(*args, **kwargs)
    def _on_coast_region_changed(self, *args, **kwargs):
        return self.overlay_controller._on_coast_region_changed(*args, **kwargs)
    def _toggle_target_areas(self, checked):
        self.coast_region_combo.setEnabled(not checked)
        if checked:
            self._show_target_area_boxes()
        else:
            self._hide_target_area_boxes()

    def _toggle_atcf_overlay(self, checked):
        """Toggle ATCF storm display on/off."""
        if checked:
            self._fetch_and_show_atcf_storms()
        else:
            self._hide_atcf_storms()
        # Update target boxes to use ATCF priority if Target mode is enabled
        if self.target_areas_cb.isChecked():
            self._show_target_area_boxes()

    def _toggle_recon_overlay(self, checked):
        """Toggle live weather-recon aircraft display on/off."""
        if checked:
            self._open_recon_window()
            self._scan_recon_flights()
            if not self._recon_scan_timer.isActive():
                self._recon_scan_timer.timeout.connect(self._scan_recon_flights)
                self._recon_scan_timer.start()
        else:
            self._recon_scan_timer.stop()
            self._hide_recon_overlay()
            self._close_recon_window()

    def _open_recon_window(self):
        """Create (lazily) and show the detached recon status window.

        A plain top-level window: never setAlwaysOnTop, so it stays behind the
        main window and the operator can move/leave it open as needed.
        """
        if self.recon_window is None:
            self.recon_window = ReconWindow()
            try:
                geo = self.geometry()
                self.recon_window.move(geo.right() + 12, geo.top())
            except Exception:
                pass
        self._update_recon_window()
        self.recon_window.show()

    def _close_recon_window(self):
        if self.recon_window is not None:
            try:
                self.recon_window.close()
            except Exception:
                pass
            self.recon_window = None

    def _update_recon_window(self):
        if self.recon_window is None:
            return
        try:
            self.recon_window.update_rows(
                aircraft=self.recon_aircraft,
                tracks=self._recon_tracks,
                last_scan=self._recon_last_fetch_time,
                source=self._recon_source,
            )
        except Exception:
            pass

    def _scan_recon_flights(self):
        """Fetch the latest recon-aircraft positions off the UI thread."""
        if self._recon_flights_busy:
            return
        self._recon_flights_busy = True

        from src.clients.recon_flights import fetch_recon_aircraft
        from PySide6.QtCore import QThread, QObject, Signal

        class ReconFlightsWorker(QObject):
            finished = Signal(list, str)
            error = Signal(str)

            def run(self):
                try:
                    aircraft, source = fetch_recon_aircraft()
                    self.finished.emit(aircraft, source)
                except Exception as e:
                    self.error.emit(str(e))

        self.log("Scanning for live weather/recon aircraft...")
        self._recon_thread = QThread()
        self._recon_worker = ReconFlightsWorker()
        self._recon_worker.moveToThread(self._recon_thread)
        self._recon_worker.finished.connect(self._on_recon_flights_fetched)
        self._recon_worker.error.connect(lambda msg: self.log(f"Recon scan error: {msg}"))
        self._recon_worker.finished.connect(self._recon_thread.quit)
        self._recon_worker.finished.connect(self._recon_worker.deleteLater)
        self._recon_worker.error.connect(self._recon_thread.quit)
        self._recon_worker.error.connect(self._recon_worker.deleteLater)
        self._recon_thread.finished.connect(self._recon_thread.deleteLater)
        self._recon_thread.finished.connect(lambda: setattr(self, '_recon_flights_busy', False))
        self._recon_thread.started.connect(self._recon_worker.run)
        self._recon_thread.start()

    def _on_recon_flights_fetched(self, aircraft, source):
        """Handle fetched recon-aircraft positions."""
        now = datetime.now(timezone.utc)
        for ac in aircraft:
            hex_id = ac.get('hex') or ''
            if not hex_id:
                continue
            hist = self._recon_tracks.setdefault(hex_id, [])
            if not hist or (hist[-1].get('lat') != ac.get('lat') or hist[-1].get('lon') != ac.get('lon')):
                hist.append({'lat': ac.get('lat'), 'lon': ac.get('lon'), 't': now.isoformat()})
            hist[:] = hist[-120:]
        self.recon_aircraft = aircraft
        self._recon_source = source
        self._recon_last_fetch_time = now
        if aircraft:
            self.log(f"Recon: {len(aircraft)} aircraft found ({source})")
        else:
            self.log(f"Recon: no weather/recon aircraft currently airborne ({source})")
        self._update_recon_window()
        if getattr(self, 'recon_cb', None) and self.recon_cb.isChecked():
            self.overlay_controller.update_overlays()

    def _hide_recon_overlay(self):
        """Remove recon overlay items from the scene."""
        scene = self.graphics_view.scene()
        if not scene:
            return
        for item in list(getattr(self, '_recon_overlay_items', [])):
            try:
                scene.removeItem(item)
            except Exception:
                pass
        self._recon_overlay_items = []

    def _draw_recon_overlay(self, project_lonlat_to_pixel, scene):
        """Delegate recon-aircraft drawing to the overlay controller."""
        self.overlay_controller._draw_recon_overlays(project_lonlat_to_pixel, scene)

    def _fetch_and_not_show_atcf_storms(self):
        """Fetch ATCF data from KnackWX and display storm icons."""
        if self._atcf_busy:
            return
        self._atcf_busy = True
        
        from src.clients.atcf import fetch_knackwx_atcf
        from PySide6.QtCore import QThread, QObject, Signal
        
        class ATCFWorker(QObject):
            finished = Signal(list)
            error = Signal(str)
            
            def run(self):
                try:
                    storms = fetch_knackwx_atcf()
                    self.finished.emit(storms)
                except Exception as e:
                    self.error.emit(str(e))
        
        self.log("Fetching ATCF data from KnackWX...")
        self._atcf_thread = QThread()
        self._atcf_worker = ATCFWorker()
        self._atcf_worker.moveToThread(self._atcf_thread)
        self._atcf_worker.finished.connect(self._on_atcf_fetched_hide)
        self._atcf_worker.error.connect(lambda msg: self.log(f"ATCF fetch error: {msg}"))
        self._atcf_worker.finished.connect(self._atcf_thread.quit)
        self._atcf_worker.finished.connect(self._atcf_worker.deleteLater)
        self._atcf_thread.finished.connect(self._atcf_thread.deleteLater)
        self._atcf_thread.finished.connect(lambda: setattr(self, '_atcf_busy', False))
        self._atcf_thread.started.connect(self._atcf_worker.run)
        self._atcf_thread.start()

    def _fetch_and_show_atcf_storms(self):
        """Fetch ATCF data from KnackWX and display storm icons."""
        if self._atcf_busy:
            return
        self._atcf_busy = True
        
        from src.clients.atcf import fetch_knackwx_atcf
        from PySide6.QtCore import QThread, QObject, Signal
        
        class ATCFWorker(QObject):
            finished = Signal(list)
            error = Signal(str)
            
            def run(self):
                try:
                    storms = fetch_knackwx_atcf()
                    self.finished.emit(storms)
                except Exception as e:
                    self.error.emit(str(e))
        
        self.log("Fetching ATCF data from KnackWX...")
        self._atcf_thread = QThread()
        self._atcf_worker = ATCFWorker()
        self._atcf_worker.moveToThread(self._atcf_thread)
        self._atcf_worker.finished.connect(self._on_atcf_fetched)
        self._atcf_worker.error.connect(lambda msg: self.log(f"ATCF fetch error: {msg}"))
        self._atcf_worker.finished.connect(self._atcf_thread.quit)
        self._atcf_worker.finished.connect(self._atcf_worker.deleteLater)
        self._atcf_thread.finished.connect(self._atcf_thread.deleteLater)
        self._atcf_thread.finished.connect(lambda: setattr(self, '_atcf_busy', False))
        self._atcf_thread.started.connect(self._atcf_worker.run)
        self._atcf_thread.start()

    def _on_atcf_fetched_hide(self, storms):
        """Handle fetched ATCF data."""
        self.atcf_storms = storms
        self._atcf_visible_storms = {s["atcf_id"] for s in storms}
        self._atcf_last_fetch_time = datetime.now(timezone.utc)
        try:
            self._populate_anim_track_target_combo()
        except Exception:
            pass
        if storms:
            self.log(f"ATCF: {len(storms)} storm(s) loaded from KnackWX")
        else:
            self.log("ATCF: no storms found")
        # Fresh positions are available — refresh the ASCAT focus-storm list.
        try:
            self.ascat_controller.on_atcf_updated()
        except Exception:
            pass
        # Fresh positions are available — refresh the Microwave focus-storm list.
        try:
            self.microwave_controller.on_atcf_updated()
        except Exception:
            pass
        # Refresh target boxes if enabled
        if self.target_areas_cb.isChecked():
            self._hide_target_area_boxes()
            self._show_target_area_boxes()

    def _on_atcf_fetched(self, storms):
        """Handle fetched ATCF data."""
        self.atcf_storms = storms
        self._atcf_visible_storms = {s["atcf_id"] for s in storms}
        self._atcf_last_fetch_time = datetime.now(timezone.utc)
        try:
            self._populate_anim_track_target_combo()
        except Exception:
            pass
        self._draw_atcf_overlay(storms)
        if storms:
            self.log(f"ATCF: {len(storms)} storm(s) loaded from KnackWX")
        else:
            self.log("ATCF: no storms found")
        # Fresh positions are available — refresh the ASCAT focus-storm list.
        try:
            self.ascat_controller.on_atcf_updated()
        except Exception:
            pass
        # Fresh positions are available — refresh the Microwave focus-storm list.
        try:
            self.microwave_controller.on_atcf_updated()
        except Exception:
            pass
        # Refresh target boxes if enabled
        if self.target_areas_cb.isChecked():
            self._hide_target_area_boxes()
            self._show_target_area_boxes()

    def _draw_atcf_overlay(self, storms):
        """Draw ATCF storm icons on the scene (only for non-Invest storms)."""
        self._hide_atcf_storms()  # Clear existing
        
        scene = self.graphics_view.scene()
        if not scene:
            return
        
        for storm in storms:
            lat = storm.get("current_lat")
            lon = storm.get("current_lon")
            if lat is None or lon is None:
                continue
            
            # Skip invests for icon display (user will create invest icon later)
            cat = storm.get("category", "")
            if cat == "Invest" or "Invest" in cat:
                continue
            
            proj_lon, proj_lat = self.overlay_controller._project_lonlat_to_pixel(lon, lat)
            if proj_lon is None or proj_lat is None:
                continue
            
            # Create clickable icon based on category
            vmax = storm.get("max_winds_kt")
            sym_name = self._category_to_sym(cat, vmax)
            
            if sym_name:
                sym_path = top_dir / "public" / "images" / "symbols" / f"{sym_name}.png"
                if sym_path.exists():
                    pix = QPixmap(str(sym_path)).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    icon = ClickablePixmapItem(
                        pix,
                        storm,  # Pass full storm data for popup
                        self.overlay_controller,
                        proj_lon - 14,
                        proj_lat - 14,
                        28
                    )
                    icon.setZValue(28)
                    pmin = storm.get("central_pressure", "--")
                    wind_str = f"{vmax} kt" if vmax else "--"
                    icon.setToolTip(f"{storm['storm_name']}: {cat}\nVMAX: {wind_str}\nPMIN: {pmin} hPa")
                    scene.addItem(icon)
                    self._atcf_overlay_items.append(icon)
                    continue
            
            # Fallback: colored dot for tropical systems without icons
            color = QColor("#FFD700") if "Tropical" in cat else QColor("#AAAAAA")
            dot = ClickablePointItem(proj_lon, proj_lat, 6, storm, self.overlay_controller,
                                    pen=QPen(color.darker(120), 1), brush=QBrush(color))
            dot.setZValue(27)
            dot.setToolTip(f"{storm['storm_name']}: {cat}")
            scene.addItem(dot)
            self._atcf_overlay_items.append(dot)

    def _hide_atcf_storms(self):
        """Remove ATCF overlay items from scene."""
        scene = self.graphics_view.scene()
        if not scene:
            return
        for item in getattr(self, '_atcf_overlay_items', []):
            try:
                scene.removeItem(item)
            except Exception:
                pass
        self._atcf_overlay_items = []

    def _on_atcf_refresh_fire(self):
        """Run the scheduled ATCF refresh, then re-arm the timer for the next clock mark."""
        self._auto_refresh_atcf()
        self._schedule_atcf_refresh()

    def _schedule_atcf_refresh(self):
        """Arm the ATCF refresh timer for the next :02/:17/:32/:47 minute mark."""
        from datetime import datetime as _dt
        targets = getattr(self, '_atcf_refresh_minutes', (2, 17, 32, 47))
        now = _dt.now()
        minute = now.minute
        for t in targets:
            if t > minute:
                delay_ms = ((t - minute) * 60 - now.second) * 1000 - now.microsecond // 1000
                if delay_ms <= 0:
                    delay_ms = 1000
                self._atcf_refresh_timer.start(delay_ms)
                return
        # next hour
        delay_ms = (((60 - minute) + targets[0]) * 60 - now.second) * 1000 - now.microsecond // 1000
        if delay_ms <= 0:
            delay_ms = 1000
        self._atcf_refresh_timer.start(delay_ms)

    def _auto_refresh_atcf(self):
        """Automatically refresh ATCF data (runs even when overlay is disabled)."""
        if getattr(self, 'atcf_cb', None) and self.atcf_cb.isChecked():
            self.log("Auto-refreshing ATCF data from KnackWX...")
            self._fetch_and_show_atcf_storms()
        else:
            self._fetch_and_not_show_atcf_storms()

    def _show_target_area_boxes(self):
        """
        Show target area boxes for storms.
        ATCF data has priority over JMA data (always use ATCF if available).
        """
        scene = self.graphics_view.scene()
        if not scene:
            return
        tracks = getattr(self, 'tracks', [])
        if not tracks:
            self.log("No tracks available for Target mode")
            self.target_areas_cb.setChecked(False)
            return
        
        # Always fetch ATCF data if not already loaded or stale
        if not hasattr(self, 'atcf_storms') or not self.atcf_storms:
            # Fetch ATCF data in background
            if not getattr(self, '_atcf_busy', False):
                self._fetch_and_not_show_atcf_storms()
        
        if not hasattr(self, '_target_boxes'):
            self._target_boxes = []
        if not hasattr(self, '_target_popups'):
            self._target_popups = []
        
        # Check if ATCF has data (regardless of checkbox state)
        atcf_storms = getattr(self, 'atcf_storms', [])
        
        box_size = 1000
        half_box = box_size / 2.0
        
        # === ATCF TARGET BOXES (PRIORITY - ALWAYS) ===
        if atcf_storms:
            for storm in atcf_storms:
                name = storm.get("storm_name", "UNKNOWN")
                # Clean up name - remove "INVEST" prefix if it's actually a named storm
                if name == "INVEST" and storm.get("atcf_id"):
                    # Use ATCF ID for invests
                    name = f"INVEST {storm['atcf_id']}"
                
                lon = storm.get("current_lon")
                lat = storm.get("current_lat")
                if lon is None or lat is None:
                    continue
                
                cat = storm.get("category", "")
                vmax = storm.get("max_winds_kt")
                pmin = storm.get("central_pressure")
                wind_str = f"{vmax} kt" if vmax else ""
                pressure_str = f"{pmin} hPa" if pmin else ""
                
                proj_lon, proj_lat = self.overlay_controller._project_lonlat_to_pixel(lon, lat)
                if proj_lon is None or proj_lat is None:
                    continue
                
                # Create target box
                aor_rect = QGraphicsRectItem(proj_lon - half_box, proj_lat - half_box, box_size, box_size)
                aor_rect.setPen(QPen(QColor(255, 107, 107), 2, Qt.DashLine))
                aor_rect.setBrush(QBrush(Qt.NoBrush))
                aor_rect.setZValue(900)
                aor_rect.setCursor(Qt.PointingHandCursor)
                lon_s = f"{abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
                lat_s = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}"
                aor_rect.setToolTip(f"{name}: {lat_s}, {lon_s}\nVMAX: {wind_str}\nPMIN: {pressure_str}\nClick to generate 1000x1000px target")
                aor_rect.setFlag(QGraphicsItem.ItemIsSelectable, False)
                aor_rect.setAcceptHoverEvents(True)
                scene.addItem(aor_rect)
                
                # Title label
                title_lbl = QGraphicsTextItem(f"{name.strip().upper()}")
                title_lbl.setDefaultTextColor(QColor(255, 107, 107))
                title_font = QFont("Segoe UI", 10, QFont.Bold)
                title_lbl.setFont(title_font)
                title_lbl.setPos(proj_lon - title_lbl.boundingRect().width()/2, proj_lat - half_box - 25)
                title_lbl.setZValue(901)
                title_lbl.setCursor(Qt.PointingHandCursor)
                title_lbl.setFlag(QGraphicsItem.ItemIsSelectable, False)
                title_lbl.setAcceptHoverEvents(True)
                scene.addItem(title_lbl)
                
                # Click handler
                def make_atcf_handler(n, lo, la, c, w, p, px, py):
                    def handler(event):
                        if event.button() == Qt.LeftButton:
                            if event.modifiers() & Qt.ShiftModifier:
                                self._hide_target_popup()
                            else:
                                self._show_atcf_target_popup(n, lo, la, c, w, p, px, py, event)
                        event.accept()
                    return handler
                
                aor_rect.mousePressEvent = make_atcf_handler(name, lon, lat, cat, wind_str, pressure_str, proj_lon, proj_lat)
                title_lbl.mousePressEvent = make_atcf_handler(name, lon, lat, cat, wind_str, pressure_str, proj_lon, proj_lat)
                
                self._target_boxes.append((aor_rect, title_lbl, name, lon, lat))
            
            self.log(f"Target mode: showing {len(atcf_storms)} ATCF storm(s)")
            return  # Don't show JMA if ATCF is available
        
        # === JMA TARGET BOXES (FALLBACK) ===
        for t in tracks:
            tid = t.get("id", "")
            if not tid.startswith("jma_"):
                continue
            name = t.get("name", "Unknown Track")
            for suffix in [" NHC Forecast", " JMA Forecast", " JTWC Forecast", " Forecast"]:
                if name.endswith(suffix):
                    name = name[:-len(suffix)]
                    break
            pts = t.get("points", [])
            if not pts:
                continue
            fp = pts[0]
            lon = fp.get("lon")
            lat = fp.get("lat")
            if lon is None or lat is None:
                continue
            cat = fp.get("intensity_category", "")
            intensity = fp.get("intensity", "")
            wind_str = f"{intensity} kt" if intensity else ""
            proj_lon, proj_lat = self.overlay_controller._project_lonlat_to_pixel(lon, lat)
            if proj_lon is None or proj_lat is None:
                continue
            aor_rect = QGraphicsRectItem(proj_lon - half_box, proj_lat - half_box, box_size, box_size)
            aor_rect.setPen(QPen(QColor(255, 107, 107), 2, Qt.DashLine))
            aor_rect.setBrush(QBrush(Qt.NoBrush))
            aor_rect.setZValue(900)
            aor_rect.setCursor(Qt.PointingHandCursor)
            lon_s = f"{abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"
            lat_s = f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}"
            aor_rect.setToolTip(f"{name}: {lat_s}, {lon_s}\nClick to generate 1000x1000px target")
            aor_rect.setFlag(QGraphicsItem.ItemIsSelectable, False)
            aor_rect.setAcceptHoverEvents(True)
            scene.addItem(aor_rect)
            title_lbl = QGraphicsTextItem(f"{name.strip().upper()}")
            title_lbl.setDefaultTextColor(QColor(255, 107, 107))
            title_font = QFont("Segoe UI", 10, QFont.Bold)
            title_lbl.setFont(title_font)
            title_lbl.setPos(proj_lon - title_lbl.boundingRect().width()/2, proj_lat - half_box - 25)
            title_lbl.setZValue(901)
            title_lbl.setCursor(Qt.PointingHandCursor)
            title_lbl.setFlag(QGraphicsItem.ItemIsSelectable, False)
            title_lbl.setAcceptHoverEvents(True)
            scene.addItem(title_lbl)
            def make_click_handler(n, lo, la, c, w, px, py):
                def handler(event):
                    if event.button() == Qt.LeftButton:
                        if event.modifiers() & Qt.ShiftModifier:
                            self._hide_target_popup()
                        else:
                            self._show_target_popup(n, lo, la, c, w, px, py, event)
                    event.accept()
                return handler
            aor_rect.mousePressEvent = make_click_handler(name, lon, lat, cat, wind_str, proj_lon, proj_lat)
            title_lbl.mousePressEvent = make_click_handler(name, lon, lat, cat, wind_str, proj_lon, proj_lat)
            self._target_boxes.append((aor_rect, title_lbl, name, lon, lat))
        self.log("Target mode enabled - click JMA storm boxes to generate 1km target")

    def _show_target_popup(self, name, lon, lat, cat, wind_str, px, py, event):
        scene = self.graphics_view.scene()
        if not scene:
            return
        for proxy, _ in getattr(self, '_target_popups', []):
            try:
                scene.removeItem(proxy)
            except Exception:
                pass
            self._target_popups = []
        popup_widget = QWidget()
        popup_widget.setFixedSize(200, 110)
        popup_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0,0,0,220), stop:1 rgba(0,0,0,200));
                border: 2px solid #FF6B6B;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(popup_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(5)
        name_lbl = QLabel(f"{name.strip().upper()}")
        name_lbl.setStyleSheet("color: white; font-weight: bold; font-size: 12pt;")
        name_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(name_lbl)
        info_lbl = QLabel(f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}")
        info_lbl.setStyleSheet("color: #AAA; font-size: 9pt;")
        info_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(info_lbl)
        cat_lbl = QLabel(f"{cat} | {wind_str}" if wind_str else f"{cat}")
        cat_lbl.setStyleSheet("color: #FFD700; font-size: 10pt; font-weight: bold;")
        cat_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(cat_lbl)
        gen_btn = QPushButton("GENERATE TARGET (1km)")
        gen_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #FF6B6B, stop:1 #C44569);
                color: white; font-weight: bold; font-size: 9pt;
                border: 1px solid white; border-radius: 4px;
                padding: 6px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #FF8787, stop:1 #E05679);
                border: 1px solid #FFD700;
            }
        """)
        gen_btn.setCursor(Qt.PointingHandCursor)
        gen_btn.clicked.connect(lambda checked, n=name, lo=lon, la=lat: self._generate_target_image(n, lo, la))
        layout.addWidget(gen_btn)
        close_btn = QPushButton("Close (Shift+Click)")
        close_btn.setStyleSheet("color: #888; font-size: 8pt; padding: 2px;")
        close_btn.clicked.connect(lambda: self._hide_target_popup())
        layout.addWidget(close_btn)
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(popup_widget)
        popup_x = px + 520
        popup_y = py - 55
        proxy.setPos(popup_x, popup_y)
        proxy.setZValue(1000)
        scene.addItem(proxy)
        self._target_popups.append((proxy, popup_widget))

    def _show_atcf_target_popup(self, name, lon, lat, cat, wind_str, pressure_str, px, py, event):
        """Show popup with ATCF data for target generation."""
        scene = self.graphics_view.scene()
        if not scene:
            return
        
        # Clear existing popups
        for proxy, _ in getattr(self, '_target_popups', []):
            try:
                scene.removeItem(proxy)
            except Exception:
                pass
        self._target_popups = []
        
        # Get current imagery info
        imagery_dt = getattr(self, 'current_datetime', None)
        sat_name = getattr(self, 'sat_combo', None)
        sat_name = sat_name.currentText() if sat_name else "Unknown"
        selected_band = next((b for b, cb in getattr(self, 'band_checkboxes', {}).items() if cb.isChecked()), None)
        band_name = selected_band if selected_band else get_products(sat_name).get(self.selected_product, {}).get("name", self.selected_product or "Unknown")
        
        # Format date/time
        from datetime import timezone as tzmod, timedelta
        fcst_prefs = self.settings.get("forecast_preferences", {})
        utc_offset = fcst_prefs.get("utc_offset", 8)
        
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
            date_str = local_dt.strftime("%b %d, %Y")
            time_str = local_dt.strftime("%I:%M %p") + f" {tz_label}"
        else:
            date_str = dt_obj.strftime("%b %d, %Y")
            time_str = dt_obj.strftime("%I:%M %p") + " UTC"
        
        # Check if in PAR
        par_poly = [(115.0,5.0),(115.0,15.0),(120.0,21.0),(120.0,25.0),(135.0,25.0),(135.0,5.0)]
        def _in_par(lo, la):
            inside = False
            j = len(par_poly) - 1
            for i in range(len(par_poly)):
                xi, yi = par_poly[i]
                xj, yj = par_poly[j]
                if ((yi > la) != (yj > la)) and lo < (xj - xi) * (la - yi) / (yj - yi) + xi:
                    inside = not inside
                j = i
            return inside
        
        in_par = _in_par(lon, lat)
        par_str = "INSIDE PAR" if in_par else "OUTSIDE PAR"
        
        popup_widget = QWidget()
        # Dynamic sizing based on content
        popup_widget.setMinimumWidth(280)
        popup_widget.setMaximumWidth(400)
        popup_widget.setMinimumHeight(180)
        
        popup_widget.setStyleSheet("""
            QWidget {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(0,0,0,220), stop:1 rgba(0,0,0,200));
                border: 2px solid #FF6B6B;
                border-radius: 8px;
            }
        """)
        
        layout = QVBoxLayout(popup_widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        
        # Storm name (larger)
        name_lbl = QLabel(f"{name.strip().upper()}")
        name_lbl.setStyleSheet("color: white; font-weight: bold; font-size: 14pt;")
        name_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(name_lbl)
        
        # Date | Time
        datetime_lbl = QLabel(f"{date_str} | {time_str}")
        datetime_lbl.setStyleSheet("color: #AAA; font-size: 9pt;")
        datetime_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(datetime_lbl)
        
        # Position
        pos_lbl = QLabel(f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'}, {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}")
        pos_lbl.setStyleSheet("color: #BBB; font-size: 9pt;")
        pos_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(pos_lbl)
        
        # VMAX | PMIN row
        vmax_pmin_row = QHBoxLayout()
        vmax_pmin_row.setSpacing(20)
        
        if wind_str:
            wind_lbl = QLabel(f"VMAX: {wind_str}")
            wind_lbl.setStyleSheet("color: #4CAF50; font-size: 11pt; font-weight: bold;")
            wind_lbl.setAlignment(Qt.AlignCenter)
            vmax_pmin_row.addWidget(wind_lbl)
        
        if pressure_str:
            pres_lbl = QLabel(f"PMIN: {pressure_str}")
            pres_lbl.setStyleSheet("color: #2196F3; font-size: 11pt; font-weight: bold;")
            pres_lbl.setAlignment(Qt.AlignCenter)
            vmax_pmin_row.addWidget(pres_lbl)
        
        layout.addLayout(vmax_pmin_row)
        
        # Satellite | Band
        sat_band_row = QHBoxLayout()
        sat_band_row.setSpacing(20)
        sat_lbl = QLabel(f"Satellite: {sat_name}")
        sat_lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")
        sat_lbl.setAlignment(Qt.AlignCenter)
        sat_band_row.addWidget(sat_lbl, 1)
        
        band_lbl = QLabel(f"Band: {band_name}")
        band_lbl.setStyleSheet("color: #90CAF9; font-size: 8pt;")
        band_lbl.setAlignment(Qt.AlignCenter)
        sat_band_row.addWidget(band_lbl, 1)
        
        layout.addLayout(sat_band_row)
        
        # TC Category | PAR status
        tc_par_row = QHBoxLayout()
        tc_par_row.setSpacing(20)
        tc_lbl = QLabel(f"TC: {cat}")
        tc_lbl.setStyleSheet("color: #FFD700; font-size: 10pt; font-weight: bold;")
        tc_lbl.setAlignment(Qt.AlignCenter)
        tc_par_row.addWidget(tc_lbl, 1)
        
        par_color = "#4CAF50" if in_par else "#FF5722"
        par_lbl = QLabel(par_str)
        par_lbl.setStyleSheet(f"color: {par_color}; font-size: 9pt; font-weight: bold;")
        par_lbl.setAlignment(Qt.AlignCenter)
        tc_par_row.addWidget(par_lbl, 1)
        
        layout.addLayout(tc_par_row)
        
        # Source badge
        source_lbl = QLabel("Data: KnackWX ATCF v2")
        source_lbl.setStyleSheet("color: #666; font-size: 7pt;")
        source_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(source_lbl)
        
        # Generate button
        gen_btn = QPushButton("GENERATE TARGET (1km)")
        gen_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #FF6B6B, stop:1 #C44569);
                color: white; font-weight: bold; font-size: 10pt;
                border: 1px solid white; border-radius: 4px;
                padding: 8px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #FF8787, stop:1 #E05679);
                border: 1px solid #FFD700;
            }
        """)
        gen_btn.setCursor(Qt.PointingHandCursor)
        gen_btn.clicked.connect(lambda checked, n=name, lo=lon, la=lat, c=cat, w=wind_str, p=pressure_str: self._generate_target_image(n, lo, la, c, w, p))
        layout.addWidget(gen_btn)
        
        # Close button
        close_btn = QPushButton("Close (Shift+Click)")
        close_btn.setStyleSheet("color: #666; font-size: 8pt; padding: 2px;")
        close_btn.clicked.connect(lambda: self._hide_target_popup())
        layout.addWidget(close_btn)
        
        # Add to scene
        proxy = QGraphicsProxyWidget()
        proxy.setWidget(popup_widget)
        popup_x = px + 520
        popup_y = py - 90
        proxy.setPos(popup_x, popup_y)
        proxy.setZValue(1000)
        scene.addItem(proxy)
        self._target_popups.append((proxy, popup_widget))

    def _hide_target_popup(self):
        scene = self.graphics_view.scene()
        if not scene:
            return
        for proxy, widget in getattr(self, '_target_popups', []):
            try:
                scene.removeItem(proxy)
            except Exception:
                pass
        self._target_popups = []

    def _hide_target_area_boxes(self):
        self._hide_target_popup()
        scene = self.graphics_view.scene()
        if not scene:
            return
        for rect, title, name, lon, lat in getattr(self, '_target_boxes', []):
            try:
                scene.removeItem(rect)
                scene.removeItem(title)
            except Exception:
                pass
        self._target_boxes = []
        self.log("Target mode disabled")

    def _generate_target_image(self, name, lon, lat, category=None, wind_str=None, pressure_str=None):
        lon_str = f"{abs(lon):.1f}{'E' if lon >= 0 else 'W'}"
        lat_str = f"{abs(lat):.1f}{'N' if lat >= 0 else 'S'}"
        self.log(f"Generating target image for {name} at {lon_str}, {lat_str}")
        self.log(f"DEBUG: name={name}, category={category}, wind_str={wind_str}, pressure_str={pressure_str}")
        result = self._zoom_to_and_capture(lon - 0.5, lon + 0.5, lat - 0.5, lat + 0.5, name, 1000, lon, lat, category, wind_str, pressure_str)
        self._hide_target_popup()
        return result
    def toggle_pro_contours(self, *args, **kwargs):
        return self.overlay_controller.toggle_pro_contours(*args, **kwargs)
    def _on_show_info_toggled(self, state):

        self.settings.set("info_box_enabled", bool(state))

        if not hasattr(self, 'info_box_panel') or self.info_box_panel is None:
            self.info_box_panel = QGroupBox("Info Box")
            self.info_box_panel.setStyleSheet("""
                QGroupBox { color: #4CAF50; font-weight: bold; border: 1px solid #444; border-radius: 4px; }
                QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            """)
            l = QVBoxLayout(self.info_box_panel)
            self.info_box_text = QLabel("No scene loaded")
            self.info_box_text.setStyleSheet("color: #ccc; font-size: 9pt; padding: 4px;")
            self.info_box_text.setWordWrap(True)
            l.addWidget(self.info_box_text)

        if state:
            self._update_info_box()

    def toggle_winds(self, *args, **kwargs):
        return self.overlay_controller.toggle_winds(*args, **kwargs)
    def toggle_microwave(self, checked: bool):
        src = self._mw_selected_source()
        if src == "mimic_tc2":
            return self.overlay_controller.toggle_mimic(checked)
        if src == "viirs_i5":
            return self.overlay_controller.toggle_viirs(checked)
        if src == "amsr2_raw":
            return self.overlay_controller.toggle_amsr2_raw(checked)
        return self.overlay_controller.toggle_microwave(checked)

    def _mw_selected_source(self):
        try:
            combo = self.mw_src_combo
            if combo is not None:
                return combo.currentData()
        except Exception:
            pass
        return "atms"

    def _on_mw_source_changed(self, _index):
        import time as _t
        self._click_log = ('microwave', _t.perf_counter())
        if not getattr(self, "microwave_enabled", False):
            return
        self.microwave_tb_data = None
        self._mw_persist = False
        self._draw_microwave_overlay()
        src = self._mw_selected_source()
        if src == "mimic_tc2":
            return self.overlay_controller.toggle_mimic(True)
        if src == "viirs_i5":
            return self.overlay_controller.toggle_viirs(True)
        if src == "amsr2_raw":
            return self.overlay_controller.toggle_amsr2_raw(True)
        return self.overlay_controller.toggle_microwave(True)

    def toggle_track_info(self, checked: bool):
        import time as _t
        self._click_log = ('track_info', _t.perf_counter())
        self.log(f"Clicked - track info (checked={checked})")
        self.track_info_enabled = checked
        gv = self.graphics_view
        if checked:
            if not hasattr(self, '_track_info_label') or self._track_info_label is None:
                from src.core.helpers import top_dir
                from PySide6.QtWidgets import QLabel
                lbl = QLabel(gv)
                _p = QPixmap(str(top_dir / "public" / "images" / "symbols" / "final_track.png"))
                if not _p.isNull():
                    _p = _p.scaled(150, 230, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    lbl.setPixmap(_p)
                    lbl.setStyleSheet("background: transparent;")
                    lbl.setAttribute(Qt.WA_TranslucentBackground)
                    lbl.adjustSize()
                    self._track_info_label = lbl
                    gv.installEventFilter(self)
                else:
                    self.log("final_track.png not found or invalid")
                    return
            _m = 10
            pos_setting = self.settings.get("track_info_position", "top_right").lower().replace(" ", "_")
            lbl_w = self._track_info_label.width()
            lbl_h = self._track_info_label.height()
            if pos_setting == "top_left":
                tx, ty = _m, _m
            elif pos_setting == "bottom_left":
                tx, ty = _m, gv.height() - lbl_h - _m
            elif pos_setting == "bottom_right":
                tx, ty = gv.width() - lbl_w - _m, gv.height() - lbl_h - _m
            else:
                tx, ty = gv.width() - lbl_w - _m, _m
            self._track_info_label.move(max(0, tx), max(0, ty))
            self._track_info_label.show()
        else:
            if hasattr(self, '_track_info_label') and self._track_info_label is not None:
                self._track_info_label.hide()

    def eventFilter(self, obj, event):
        if obj is self.graphics_view and event.type() == QEvent.Resize:
            if hasattr(self, '_track_info_label') and self._track_info_label is not None and self._track_info_label.isVisible():
                _m = 10
                pos_setting = self.settings.get("track_info_position", "top_right").lower().replace(" ", "_")
                lbl_w = self._track_info_label.width()
                lbl_h = self._track_info_label.height()
                if pos_setting == "top_left":
                    tx, ty = _m, _m
                elif pos_setting == "bottom_left":
                    tx, ty = _m, obj.height() - lbl_h - _m
                elif pos_setting == "bottom_right":
                    tx, ty = obj.width() - lbl_w - _m, obj.height() - lbl_h - _m
                else:
                    tx, ty = obj.width() - lbl_w - _m, _m
                self._track_info_label.move(max(0, tx), max(0, ty))
        return super().eventFilter(obj, event)

    def _update_info_box(self):
        if not hasattr(self, 'info_box_text') or not self.info_box_text:
            return

        lines = []
        if self.current_satellite:
            lines.append(f"<b>Satellite:</b> {self.current_satellite}")
        if self.current_datetime:
            lines.append(f"<b>Time (UTC):</b> {self.current_datetime}")

        if self._ir_kelvin is not None:
            try:
                valid = self._ir_kelvin[~np.isnan(self._ir_kelvin)]
                if len(valid) > 0:
                    lines.append(f"<b>Min K:</b> {np.min(valid):.1f}")
                    lines.append(f"<b>Max K:</b> {np.max(valid):.1f}")
            except Exception:
                pass

        if self.selected_product:
            _sat_for_prod = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
            display_name = get_products(_sat_for_prod).get(self.selected_product, {}).get("name", self.selected_product)
            lines.append(f"<b>Product:</b> {display_name}")

        text = "<br>".join(lines) if lines else "Load a scene to see info."
        self.info_box_text.setText(text)

    def _update_floating_info_box(self):

        return

    def _position_info_box(self):

        return

        pos_parent = self.info_box.parent()
        if pos_parent is None:
            pos_parent = getattr(self, 'viewport_frame', None) or self

        parent_w = pos_parent.width()
        parent_h = pos_parent.height()

        pos = self.settings.get("info_box_position", "top_right")
        margin = 10

        self.info_box.adjustSize()
        w = max(80, min(self.info_box.width(), max(100, parent_w - 2*margin)))
        h = max(20, min(self.info_box.height(), max(30, parent_h - 2*margin)))

        x = margin
        y = margin
        if pos == "top_right":
            x = parent_w - w - margin
        elif pos == "top_left":
            x = margin
        elif pos == "bottom_right":
            x = parent_w - w - margin
            y = parent_h - h - margin
        elif pos == "bottom_left":
            y = parent_h - h - margin
        else:
            x = (parent_w - w) // 2
            y = (parent_h - h) // 2

        x = max(0, min(x, parent_w - w))
        y = max(0, min(y, parent_h - h))
        self.info_box.move(x, y)

    def _refresh_info_box_if_visible(self):
        """Update and reposition the viewport info box if a track is selected."""
        if not hasattr(self, 'viewport_info_box') or not self.viewport_info_box:
            return
        
        # Find a track with add_to_infobox enabled or use currently selected one
        track_to_show = None
        if hasattr(self, '_viewport_infobox_track_id'):
            for t in self.tracks:
                if t.get("id") == self._viewport_infobox_track_id:
                    track_to_show = t
                    break
        
        if track_to_show and track_to_show.get("points"):
            self.viewport_info_box.set_track_info(track_to_show.get("name", "Track"), track_to_show)
            self.viewport_info_box.setVisible(True)
            
            # Position based on settings
            if hasattr(self, 'graphics_view') and self.graphics_view:
                self.apply_track_info_position()
        else:
            self.viewport_info_box.setVisible(False)

    def _draw_aor_overlays(self, *args, **kwargs):
        return self.overlay_controller._draw_aor_overlays(*args, **kwargs)
    def _category_to_sym(self, *args, **kwargs):
        return self.overlay_controller._category_to_sym(*args, **kwargs)
    def _category_to_pag_sym(self, *args, **kwargs):
        return self.overlay_controller._category_to_pag_sym(*args, **kwargs)
    def _compute_label_offsets(self, *args, **kwargs):
        return self.overlay_controller._compute_label_offsets(*args, **kwargs)
    def _compute_label_offsets(self, *args, **kwargs):
        return self.overlay_controller._compute_label_offsets(*args, **kwargs)
    def _draw_tracks_overlays(self, *args, **kwargs):
        return self.overlay_controller._draw_tracks_overlays(*args, **kwargs)
    def _compute_aor_status(self, lon: float, lat: float) -> str:

        if lon is None or lat is None:
            return ""
        active = []

        if getattr(self, 'aor_par_cb', None) and self.aor_par_cb.isChecked():
            par = [(115.0, 5.0), (115.0, 15.0), (120.0, 21.0), (120.0, 25.0), (135.0, 25.0), (135.0, 5.0)]
            if _point_in_polygon(lon, lat, par):
                active.append("PAR")

        if getattr(self, 'aor_jma_cb', None) and self.aor_jma_cb.isChecked():
            jma = [(100.0, 0.0), (180.0, 0.0), (180.0, 60.0), (100.0, 60.0)]
            if _point_in_polygon(lon, lat, jma):
                active.append("JMA")

        if (getattr(self, 'aor_custom_cb', None) and self.aor_custom_cb.isChecked() and
                getattr(self, 'current_custom_aor_points', None) and len(self.current_custom_aor_points) >= 3):
            if _point_in_polygon(lon, lat, self.current_custom_aor_points):
                active.append("Custom(live)")

        if getattr(self, 'aor_custom_cb', None) and self.aor_custom_cb.isChecked() and getattr(self, 'tracks', None):
            for t in self.tracks:
                if t.get("type") == "Custom AoR" and len(t.get("points", [])) >= 3:
                    poly = [(p["lon"], p["lat"]) for p in t["points"] if p.get("lon") is not None and p.get("lat") is not None]
                    if len(poly) >= 3 and _point_in_polygon(lon, lat, poly):
                        active.append("Custom(saved)")
                        break
        return " + ".join(active) if active else ""

    def _finish_scene_load(self, nc_path, band_names):

        pass

    def _build_band_ui_immediately(self, band_names, nc_path):

        self.available_bands = band_names
        if hasattr(self, 'update_devkit_band_lists'):
            self.update_devkit_band_lists(band_names)

        for attr_w, attr_t in [('raw_cache_worker', 'raw_cache_thread'), ('raw_cache_worker2', 'raw_cache_thread2')]:
            w = getattr(self, attr_w, None)
            t = getattr(self, attr_t, None)
            if w: w.cancel()
            if t and t.isRunning(): t.quit()
        self.cache.raw.clear()
        self.cache.rgb.clear()

        for cb in list(self.band_checkboxes.values()):
            cb.deleteLater()
        self.band_checkboxes.clear()
        while self.bands_grid_layout.count():
            item = self.bands_grid_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        row = col = 0
        for band in band_names:
            cb = QCheckBox(band)
            cb.toggled.connect(self.on_band_checkbox_toggled)
            self.bands_grid_layout.addWidget(cb, row, col)
            self.band_checkboxes[band] = cb
            col += 1
            if col >= 4:
                col = 0
                row += 1
        if hasattr(self, 'type_combo') and self.type_combo.currentText() == "Full Disk":
            for i in range(4):
                self.bands_grid_layout.setColumnStretch(i, 1)

        self._restyle_band_checkboxes()
        self.update_selected_bands_label()

        if band_names:
            _preferred = ["B01", "B02", "B04", "B05", "B06", "B07", "B08",
                          "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]
            first_band = next((b for b in _preferred if b in band_names), band_names[0])
            cb = self.band_checkboxes.get(first_band)
            if cb and not cb.isChecked():
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)

        self.status_bar.showMessage(f"Loaded {len(band_names)} bands -- preparing in background...")

    def _on_scene_prepared(self, result):
        self.current_crs = result.get("crs")
        self.current_geotransform = result.get("transform")
        self._ref_grid_size = result.get("ref_grid_size")
        self._ref_grid_1km = result.get("ref_grid_1km")
        self._ref_grid_0_5km = result.get("ref_grid_0_5km")
        self._ir_kelvin = result.get("ir_kelvin")
        if self._ir_kelvin is None:
            nc_path = result.get("nc_path")
            if nc_path:
                self._ir_kelvin = self._load_ir_kelvin(nc_path)

        self._refresh_info_box_if_visible()

        if hasattr(self, 'clear_temp_markers'):
            self.clear_temp_markers(log=False)

        nc_path = result["nc_path"]
        band_names = result["band_names"] or self.available_bands

        if band_names and set(band_names) != set(self.band_checkboxes.keys()):

            for cb in list(self.band_checkboxes.values()):
                cb.deleteLater()
            self.band_checkboxes.clear()
            while self.bands_grid_layout.count():
                item = self.bands_grid_layout.takeAt(0)
                if item.widget(): item.widget().deleteLater()
            row = col = 0
            for band in band_names:
                cb = QCheckBox(band)
                cb.toggled.connect(self.on_band_checkbox_toggled)
                self.bands_grid_layout.addWidget(cb, row, col)
                self.band_checkboxes[band] = cb
                col += 1
                if col >= 4:
                    col = 0
                    row += 1
            if hasattr(self, 'type_combo') and self.type_combo.currentText() == "Full Disk":
                for i in range(4):
                    self.bands_grid_layout.setColumnStretch(i, 1)
            self._restyle_band_checkboxes()
            self.update_selected_bands_label()
            self.available_bands = band_names

        _sat_sp = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
        _eng = get_engine(_sat_sp)
        log.info("Scene prepared: satellite=%s engine=%s", _sat_sp, _eng.__module__)
        if getattr(self, '_band_nc_map', {}):
            available_keys = _eng.available_for_nc(nc_path, band_names=band_names)
        else:
            available_keys = _eng.available_for_nc(nc_path)
        for key, tile in self._product_tile_widgets.items():
            tile.setEnabled(key in available_keys)

        if self.settings.get("precache_bands", False):
            self.start_precache(nc_path, band_names)

        if not getattr(self, '_raw_cache_threads_running', 0):
            self._start_raw_band_cache(nc_path, band_names)
        else:
            self.log(f"Raw band cache already running -- skipping redundant start")
        self.overlay_controller._start_overlay_precache(nc_path)
        self.log(f"Background scene prep finished for {nc_path.name} -- raw caches running")

        self.overlay_controller._update_winds_checkbox_state()

        if band_names:
            any_checked = any(cb.isChecked() for cb in self.band_checkboxes.values())
            if not any_checked:
                _preferred = ["B01", "B02", "B04", "B05", "B06", "B07", "B08",
                              "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"]
                first = next((b for b in _preferred if b in band_names), band_names[0])
                if first in self.band_checkboxes:
                    cb = self.band_checkboxes[first]
                    cb.blockSignals(True)
                    cb.setChecked(True)
                    cb.blockSignals(False)

    def _update_sataid_overlays(self, *args, **kwargs):
        return self.overlay_controller._update_sataid_overlays(*args, **kwargs)
    def _remove_all_overlay_items(self, *args, **kwargs):
        return self.overlay_controller._remove_all_overlay_items(*args, **kwargs)
    def _compute_disk_half_extent(self, *args, **kwargs):
        return self.overlay_controller._compute_disk_half_extent(*args, **kwargs)
    def _get_image_scene_pos(self, *args, **kwargs):
        return self.overlay_controller._get_image_scene_pos(*args, **kwargs)
    def _overlay_cache_key(self, *args, **kwargs):
        return self.overlay_controller._overlay_cache_key(*args, **kwargs)
    def _get_max_texture_size(self, *args, **kwargs):
        return self.satellite_controller._get_max_texture_size(*args, **kwargs)
    def _get_quality_grid(self, *args, **kwargs):
        return self.overlay_controller._get_quality_grid(*args, **kwargs)
    def _prepare_cached_display(self, *args, **kwargs):
        return self.overlay_controller._prepare_cached_display(*args, **kwargs)
    def _compute_native_res_m(self, *args, **kwargs):
        return self.overlay_controller._compute_native_res_m(*args, **kwargs)
    def _compute_pixmap_scale(self, *args, **kwargs):
        return self.overlay_controller._compute_pixmap_scale(*args, **kwargs)
    def _resize_for_fldk(self, *args, **kwargs):
        return self.overlay_controller._resize_for_fldk(*args, **kwargs)
    def _cache_overlay_geo(self, *args, **kwargs):
        return self.overlay_controller._cache_overlay_geo(*args, **kwargs)
    def _project_lonlat_to_pixel(self, *args, **kwargs):
        return self.overlay_controller._project_lonlat_to_pixel(*args, **kwargs)
    def _update_grid_coast_overlay(self, *args, **kwargs):
        return self.overlay_controller._update_grid_coast_overlay(*args, **kwargs)
    def _on_coast_interaction_begin(self, *args, **kwargs):
        return self.overlay_controller.interaction_coast_swap_begin(*args, **kwargs)
    def _on_coast_interaction_end(self, *args, **kwargs):
        return self.overlay_controller.interaction_coast_swap_end(*args, **kwargs)
    def _update_aor_overlays(self, *args, **kwargs):
        return self.overlay_controller._update_aor_overlays(*args, **kwargs)
    def _update_tracks_overlays(self, *args, **kwargs):
        return self.overlay_controller._update_tracks_overlays(*args, **kwargs)
    def _overlay_disk_cache_path(self, *args, **kwargs):
        return self.overlay_controller._overlay_disk_cache_path(*args, **kwargs)
    def _grid_cache_key(self, *args, **kwargs):
        return self.overlay_controller._grid_cache_key(*args, **kwargs)
    def _coast_cache_key(self, *args, **kwargs):
        return self.overlay_controller._coast_cache_key(*args, **kwargs)
    def _render_grid_cached(self, *args, **kwargs):
        return self.overlay_controller._render_grid_cached(*args, **kwargs)
    def _render_coast_cached(self, *args, **kwargs):
        return self.overlay_controller._render_coast_cached(*args, **kwargs)
    def _draw_mercator_grid(self, *args, **kwargs):
        return self.overlay_controller._draw_mercator_grid(*args, **kwargs)
    def _draw_plate_carree_grid(self, *args, **kwargs):
        return self.overlay_controller._draw_plate_carree_grid(*args, **kwargs)
    def _draw_geostationary_grid(self, *args, **kwargs):
        return self.overlay_controller._draw_geostationary_grid(*args, **kwargs)
    def _build_mercator_grid_path(self, *args, **kwargs):
        return self.overlay_controller._build_mercator_grid_path(*args, **kwargs)
    def _build_plate_carree_grid_path(self, *args, **kwargs):
        return self.overlay_controller._build_plate_carree_grid_path(*args, **kwargs)
    def _build_geostationary_grid_path(self, *args, **kwargs):
        return self.overlay_controller._build_geostationary_grid_path(*args, **kwargs)
    def _draw_equirectangular_grid(self, *args, **kwargs):
        return self.overlay_controller._draw_equirectangular_grid(*args, **kwargs)
    def _build_equirectangular_grid_path(self, *args, **kwargs):
        return self.overlay_controller._build_equirectangular_grid_path(*args, **kwargs)
    def _build_equirectangular_coast_path(self, *args, **kwargs):
        return self.overlay_controller._build_equirectangular_coast_path(*args, **kwargs)
    def _draw_mercator_coastlines(self, *args, **kwargs):
        return self.overlay_controller._draw_mercator_coastlines(*args, **kwargs)
    def _draw_plate_carree_coastlines(self, *args, **kwargs):
        return self.overlay_controller._draw_plate_carree_coastlines(*args, **kwargs)
    def _draw_equirectangular_coastlines(self, *args, **kwargs):
        return self.overlay_controller._draw_equirectangular_coastlines(*args, **kwargs)
    def _build_mercator_coast_path(self, *args, **kwargs):
        return self.overlay_controller._build_mercator_coast_path(*args, **kwargs)
    def _build_plate_carree_coast_path(self, *args, **kwargs):
        return self.overlay_controller._build_plate_carree_coast_path(*args, **kwargs)
    def _build_geostationary_coast_path(self, *args, **kwargs):
        return self.overlay_controller._build_geostationary_coast_path(*args, **kwargs)
    def _draw_nhc_overlays(self, *args, **kwargs):
        return self.overlay_controller._draw_nhc_overlays(*args, **kwargs)
    def update_overlays(self, *args, **kwargs):
        return self.overlay_controller.update_overlays(*args, **kwargs)
    def _update_overlays_static(self, *args, **kwargs):
        return self.overlay_controller._update_overlays_static(*args, **kwargs)
    def _on_viewport_mouse_moved(self, scene_x: float, scene_y: float):
        self._last_mouse_scene_pos = (scene_x, scene_y)
        self._pending_mouse_pos = (scene_x, scene_y)

        # Info Box: Find nearest track point to mouse
        if not hasattr(self, 'tracks') or not self.tracks:
            return

        nearest_track = None
        min_dist = 15.0 # pixels
        
        proj_cache = getattr(self, '_track_proj_cache', {})
        for track in self.tracks:
            t_id = track.get("id")
            if t_id not in proj_cache:
                continue
            
            for qpt, p in proj_cache[t_id]:
                dist = np.sqrt((qpt.x() - scene_x)**2 + (qpt.y() - scene_y)**2)
                if dist < min_dist:
                    min_dist = dist
                    nearest_track = track
        
        if nearest_track:
            if nearest_track.get("add_to_infobox", False):
                self.viewport_info_box.set_track_info(nearest_track.get("name") or "Track", nearest_track)
                self.viewport_info_box.setVisible(True)
                self.apply_track_info_position()
        elif self.viewport_info_box.isVisible():
            # Only hide if we aren't manually showing a track
            if not self._viewport_infobox_track_id:
                self.viewport_info_box.setVisible(False)

        import time as _t
        click = getattr(self, '_click_log', None)
        if click and click[0] in ('grid', 'coast', 'winds'):
            dt_ms = (_t.perf_counter() - click[1]) * 1000.0
            self.log(f"Displayed - {click[0]} (after {dt_ms:.1f}ms from click, includes paint)")
            self._click_log = None
    def _on_viewport_rect_selected(self, x, y, w, h):
        if not getattr(self, '_contour_active', False):
            return
        data, selected_band = self.contour._get_contour_data()
        if data is None:
            return
        dh, dw = data.shape
        scene = self.graphics_view.scene()
        img_item = next((i for i in reversed(scene.items()) if isinstance(i, QGraphicsPixmapItem)), None) if scene else None
        ox = img_item.pos().x() if img_item else 0.0
        oy = img_item.pos().y() if img_item else 0.0
        if img_item and img_item.pixmap():
            pix_w = img_item.pixmap().width()
            pix_h = img_item.pixmap().height()
            sx = dw / pix_w if pix_w > 0 else 1.0
            sy = dh / pix_h if pix_h > 0 else 1.0
        else:
            sx = sy = 1.0
        col_s = max(0, int(round((x - ox) * sx)))
        row_s = max(0, int(round((y - oy) * sy)))
        col_e = min(dw, int(round((x + w - ox) * sx)))
        row_e = min(dh, int(round((y + h - oy) * sy)))
        if col_e <= col_s or row_e <= row_s:
            return
        region = data[row_s:row_e, col_s:col_e]
        if region.size == 0 or np.all(~np.isfinite(region)):
            return
        self._contour_last_rect = (col_s, row_s, col_e, row_e)
        self.contour._compute_and_draw_contour(region, selected_band, col_s, row_s, col_e, row_e)

    def _get_contour_data(self, *args, **kwargs):
        return self.contour._get_contour_data(*args, **kwargs)
    def _redraw_contour(self, *args, **kwargs):
        return self.contour._redraw_contour(*args, **kwargs)
    def _compute_and_draw_contour(self, *args, **kwargs):
        return self.contour._compute_and_draw_contour(*args, **kwargs)
    def set_contour_overlay(self, *args, **kwargs):
        return self.contour.set_contour_overlay(*args, **kwargs)
    def clear_contour_overlay(self, *args, **kwargs):
        return self.contour.clear_contour_overlay(*args, **kwargs)
    def _get_pixel_latlon(self, *args, **kwargs):
        return self.overlay_controller._get_pixel_latlon(*args, **kwargs)
    def mouseMoveEvent(self, event):

        view_pos = event.position().toPoint()
        scene_pos = self.graphics_view.mapToScene(view_pos)
        self._pending_mouse_pos = (scene_pos.x(), scene_pos.y())
        super().mouseMoveEvent(event)

    def _toggle_animation(self, *args, **kwargs):
        return self.animation_controller._toggle_animation(*args, **kwargs)
    def _on_anim_frame_changed(self, *args, **kwargs):
        return self.animation_controller._on_anim_frame_changed(*args, **kwargs)
    def _on_anim_speed_changed(self, *args, **kwargs):
        return self.animation_controller._on_anim_speed_changed(*args, **kwargs)
    def _compute_anim_interval(self, *args, **kwargs):
        return self.animation_controller._compute_anim_interval(*args, **kwargs)
    def _generate_animation_timestamps(self, *args, **kwargs):
        return self.animation_controller._generate_animation_timestamps(*args, **kwargs)
    def _sync_anim_dates_from_current(self, *args, **kwargs):
        return self.animation_controller._sync_anim_dates_from_current(*args, **kwargs)
    def _sync_anim_sat_type_from_main(self, *args, **kwargs):
        return self.animation_controller._sync_anim_sat_type_from_main(*args, **kwargs)
    def _cancel_anim_prefetch(self, *args, **kwargs):
        return self.animation_controller._cancel_anim_prefetch(*args, **kwargs)
    def _clear_anim_cache(self, *args, **kwargs):
        return self.animation_controller._clear_anim_cache(*args, **kwargs)
    def _find_nearest_valid_frame(self, *args, **kwargs):
        return self.animation_controller._find_nearest_valid_frame(*args, **kwargs)
    def _display_anim_frame(self, *args, **kwargs):
        return self.animation_controller._display_anim_frame(*args, **kwargs)
    def _on_anim_frame_ready(self, *args, **kwargs):
        return self.animation_controller._on_anim_frame_ready(*args, **kwargs)
    def _on_anim_prefetch_progress(self, *args, **kwargs):
        return self.animation_controller._on_anim_prefetch_progress(*args, **kwargs)
    def _on_anim_prefetch_finished(self, *args, **kwargs):
        return self.animation_controller._on_anim_prefetch_finished(*args, **kwargs)
    def _advance_animation(self, *args, **kwargs):
        return self.animation_controller._advance_animation(*args, **kwargs)
    def _export_bitmap(self, *args, **kwargs):
        return self.export_controller._export_bitmap(*args, **kwargs)
    def _export_geotiff(self, *args, **kwargs):
        return self.export_controller._export_geotiff(*args, **kwargs)
    def _export_serial_bitmap(self, *args, **kwargs):
        return self.export_controller._export_serial_bitmap(*args, **kwargs)
    def _export_animation(self, *args, **kwargs):
        return self.export_controller._export_animation(*args, **kwargs)
    def _launch_export_worker(self, frames, file_path, fps=5,
                              with_footer=False, footer_data=None):
        ctx = getattr(self, '_export_ctx', None)
        if ctx:
            try:
                ctx['worker'].cancel()
                ctx['thread'].quit()
                ctx['thread'].wait(3000)
            except RuntimeError:
                pass

        if self._export_progress_bar:
            self.status_bar.removeWidget(self._export_progress_bar)
            self._export_progress_bar.deleteLater()
            self._export_progress_bar = None

        total = sum(1 for f in frames if f is not None)
        self._export_progress_bar = QProgressBar()
        self._export_progress_bar.setRange(0, total)
        self._export_progress_bar.setValue(0)
        self._export_progress_bar.setStyleSheet("""
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
        self._export_progress_bar.setFixedWidth(200)
        self._export_progress_bar.setFormat(f"Exporting: 0/{total}")
        self.status_bar.addPermanentWidget(self._export_progress_bar)
        self.status_bar.showMessage(f"Exporting animation ({total} frames)...")

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

        self._export_ctx = dict(thread=thread, worker=worker)

    def _on_export_progress(self, *args, **kwargs):
        return self.export_controller._on_export_progress(*args, **kwargs)
    def _on_export_finished(self, *args, **kwargs):
        return self.export_controller._on_export_finished(*args, **kwargs)
    def _on_export_thread_finished(self, *args, **kwargs):
        return self.export_controller._on_export_thread_finished(*args, **kwargs)
    def _on_export_error(self, *args, **kwargs):
        return self.export_controller._on_export_error(*args, **kwargs)
    def _get_current_band_label(self, *args, **kwargs):
        return self.export_controller._get_current_band_label(*args, **kwargs)
    def _format_dt_for_footer(self, *args, **kwargs):
        return self.export_controller._format_dt_for_footer(*args, **kwargs)
    def _format_dt_for_footer(self, *args, **kwargs):
        return self.export_controller._format_dt_for_footer(*args, **kwargs)
    def _get_footer_latlon(self, *args, **kwargs):
        return self.export_controller._get_footer_latlon(*args, **kwargs)
    def _qimage_to_numpy(self, *args, **kwargs):
        return self.export_controller._qimage_to_numpy(*args, **kwargs)
    def _qimage_to_numpy(self, *args, **kwargs):
        return self.export_controller._qimage_to_numpy(*args, **kwargs)
    def _point_latlon_str(self, *args, **kwargs):
        return self.export_controller._point_latlon_str(*args, **kwargs)
    def _point_latlon_str(self, *args, **kwargs):
        return self.export_controller._point_latlon_str(*args, **kwargs)
    def _get_image_center_latlon(self, *args, **kwargs):
        return self.export_controller._get_image_center_latlon(*args, **kwargs)
    def _make_footer_parts(self, *args, **kwargs):
        return self.export_controller._make_footer_parts(*args, **kwargs)
    def _add_footer_to_qimage(self, *args, **kwargs):
        return self.export_controller._add_footer_to_qimage(*args, **kwargs)
    def _add_footer_to_frame(self, *args, **kwargs):
        return self.export_controller._add_footer_to_frame(*args, **kwargs)
    def _upscale_single_band_image(self, *args, **kwargs):
        return self.export_controller._upscale_single_band_image(*args, **kwargs)
    def _export_animation_with_footer(self, *args, **kwargs):
        return self.export_controller._export_animation_with_footer(*args, **kwargs)
    def _init_mouse_throttle(self):
        self._mouse_throttle_ms = self.settings.get("mouse_throttle_ms", 80)
        self._pending_mouse_pos = None
        if not hasattr(self, '_mouse_timer') or self._mouse_timer is None:
            self._mouse_timer = QTimer(self)
            self._mouse_timer.timeout.connect(self._process_throttled_mouse)
        self._mouse_timer.setInterval(max(16, self._mouse_throttle_ms))
        if not self._mouse_timer.isActive():
            self._mouse_timer.start()

    def apply_mouse_throttle(self):

        self._mouse_throttle_ms = self.settings.get("mouse_throttle_ms", 80)
        if hasattr(self, '_mouse_timer') and self._mouse_timer:
            self._mouse_timer.setInterval(max(16, self._mouse_throttle_ms))

    def apply_adaptive_settings(self):
        self._adaptive_quality = self.settings.get("adaptive_quality", True)
        self._async_overlays = self.settings.get("async_overlay_rendering", True)
        self._lod_bias = self.settings.get("lod_bias", 0.0)
        self._overlay_throttle_ms = self.settings.get("overlay_update_throttle_ms", 50)
        self._anim_frame_skip = self.settings.get("animation_frame_skip", 0)
        self._numba_enabled = self.settings.get("numba_jit", True) and HAS_NUMBA
        self._zoom_quality_reduced = False
        raw_threads = self.settings.get("max_threads", 0)
        if raw_threads == 0:
            raw_threads = os.cpu_count() or 4
        if raw_threads != self.max_threads:
            self.max_threads = raw_threads
            self._thread_pool.shutdown(wait=False)
            self._thread_pool = ThreadPoolExecutor(max_workers=self.max_threads)
        if hasattr(self, 'graphics_view'):
            self.graphics_view.set_adaptive_quality(self._adaptive_quality)
        self.log(f"Adaptive settings: quality={self._adaptive_quality}, async={self._async_overlays}, lod={self._lod_bias}, overlay_throttle={self._overlay_throttle_ms}ms, anim_skip={self._anim_frame_skip}, numba={self._numba_enabled}")

    def _on_zoom_started(self):
        if self._adaptive_quality and not self._zoom_quality_reduced:
            self._zoom_quality_reduced = True
            old_hints = self.graphics_view.renderHints()
            self.graphics_view.setRenderHint(QPainter.SmoothPixmapTransform, False)
            self.graphics_view.setRenderHint(QPainter.Antialiasing, False)
            self._saved_render_hints = old_hints

    def _on_zoom_stopped(self):
        if self._adaptive_quality and self._zoom_quality_reduced:
            self._zoom_quality_reduced = False
            self.apply_zoom_interpolation()

    def _process_throttled_mouse(self):
        if self._pending_mouse_pos:
            x, y = self._pending_mouse_pos
            self._update_mouse_readout_from_scene_pos(x, y)
            self._refresh_info_box_if_visible()
            self._pending_mouse_pos = None

    def _update_mouse_readout_from_scene_pos(self, img_x: float, img_y: float):

        if not self.graphics_view.scene() or not self.graphics_view.scene().items():
            self.update_secondary_status(None, None, None)
            return
        pixmap_item = None
        for item in reversed(self.graphics_view.scene().items()):
            if isinstance(item, QGraphicsPixmapItem):
                pixmap_item = item
                break
        if not pixmap_item:
            self.update_secondary_status(None, None, None)
            return
        img_w = pixmap_item.pixmap().width()
        img_h = pixmap_item.pixmap().height()
        ox = pixmap_item.pos().x()
        oy = pixmap_item.pos().y()
        px = img_x - ox
        py = img_y - oy
        if px < 0 or py < 0 or px > img_w or py > img_h:
            self.update_secondary_status(None, None, None)
            return

        lat = lon = None
        temp = None
        is_sataid = (hasattr(self, 'type_combo') and self.type_combo.currentText() == "SATAID"
                     and hasattr(self, '_sataid_current_lat') and self._sataid_current_lat is not None
                     and hasattr(self, '_sataid_current_lon') and self._sataid_current_lon is not None)
        if is_sataid:
            try:
                lon_arr = self._sataid_current_lon
                lat_arr = self._sataid_current_lat
                col = int(px / img_w * len(lon_arr))
                row = int(py / img_h * len(lat_arr))
                col = max(0, min(col, len(lon_arr) - 1))
                row = max(0, min(row, len(lat_arr) - 1))
                lon = float(lon_arr[col])
                lat = float(lat_arr[row])
                if hasattr(self, '_sataid_current_data') and self._sataid_current_data is not None:
                    dh, dw = self._sataid_current_data.shape
                    sx = int(px / img_w * dw)
                    sy = int(py / img_h * dh)
                    sx = max(0, min(sx, dw - 1))
                    sy = max(0, min(sy, dh - 1))
                    val = float(self._sataid_current_data[sy, sx])
                    if not np.isnan(val):
                        temp = val
            except Exception:
                pass
        else:
            if self.current_geotransform and self.current_crs:
                try:
                    transform = self.current_geotransform
                    crs_proj = self.current_crs
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
                except Exception:
                    pass

            if self._ir_kelvin is not None:
                kw, kh = self._ir_kelvin.shape[1], self._ir_kelvin.shape[0]
                ix = int(px * kw / img_w) if img_w > 0 else 0
                iy = int(py * kh / img_h) if img_h > 0 else 0
                ix = max(0, min(ix, kw-1))
                iy = max(0, min(iy, kh-1))
                val = self._ir_kelvin[iy, ix]
                if not np.isnan(val):
                    temp = float(val)

        aor_status = self._compute_aor_status(lon, lat) if (lon is not None and lat is not None) else ""
        self.update_secondary_status(lat, lon, temp, aor_status)

    def update_secondary_status(self, lat=None, lon=None, temp=None, aor_status=""):

        if lat is not None and lon is not None:
            unit = getattr(self, '_sataid_units', "K")
            base = f"Lat: {lat:.2f}\u00b0  Lon: {lon:.2f}\u00b0"
            if temp is not None:
                if unit == "celsius":
                    base += f"  |  Temp: {temp:.1f}\u00b0C"
                else:
                    base += f"  |  Temp: {temp:.1f} {unit}"
            else:
                base += "  |  Temp: --"
            if aor_status:
                text = f"{base}  |  AoR: {aor_status}"
            else:
                text = base
        else:
            text = "Lat: --  Lon: --  |  Temp: --"
        self.secondary_status_label.setText(text)
        self.secondary_status_label.setVisible(True)

    def run_bg_to_nc(self):
        if getattr(sys, 'frozen', False) and (top_dir / 'bg_to_nc.exe').exists():
            QProcess.startDetached(str(top_dir / 'bg_to_nc.exe'))
        else:
            script = top_dir / 'Process' / 'tools' / 'bg_to_nc.py'
            if not script.exists():
                self.log(f"bg_to_nc.py not found")
                return
            QProcess.startDetached(sys.executable, [str(script)])

    def run_process_dat(self):
        dialog = DownloaderSelector(self, self.settings)
        if dialog.exec() != QDialog.Accepted or not dialog.selected:
            return
        choice = dialog.selected

        if choice == "aws":
            self._launch_aws_downloader()
        elif choice == "ftp":
            self._launch_ftp_downloader()
        elif choice == "wis":
            self._launch_wis_downloader()

    def _launch_aws_downloader(self):
        if getattr(sys, 'frozen', False):
            exe_path = top_dir / 'ProcessDat.exe'
            if exe_path.exists():
                subprocess.Popen(
                    [str(exe_path)],
                    cwd=str(top_dir),
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                self.log("Launched ProcessDat.exe as independent tool.")
                return
        self._launch_script("Process_dat.py")

    def _launch_ftp_downloader(self):
        self._launch_script("downloader_ftp.py")

    def _launch_wis_downloader(self):
        self._launch_script("downloader_wis.py")

    def _launch_script(self, script_name):
        script_path = top_dir / 'Process' / 'tools' / script_name
        if not script_path.exists():
            QMessageBox.warning(
                self, "Script Not Found",
                f"{script_name} not found.\nExpected at: {script_path}"
            )
            return
        try:
            if sys.platform == "win32":
                python_exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                if not os.path.exists(python_exe):
                    python_exe = sys.executable
            else:
                python_exe = sys.executable
            subprocess.Popen(
                [python_exe, str(script_path)],
                cwd=script_path.parent,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            self.log(f"Launched {script_name} as independent tool.")
        except Exception as e:
            self.log(f"Failed to launch {script_name}: {str(e)}")
            QMessageBox.warning(self, "Launch Failed", f"Could not start {script_name}:\n{e}")

    # -- Download folder auto-refresh -----------------------------
    # Detached downloader processes (ProcessDat.exe / Downloader_ftp.py /
    # downloader_wis.py) write new satellite folders and timesteps directly into
    # the download directory, but the date/time dropdowns only re-scanned the
    # filesystem on satellite/type changes or at startup. These handlers re-run
    # _populate_available_dates() whenever new data lands (watcher + poll).
    # -------------------------------------------------------------------------

    def _init_download_folder_watcher(self):
        self._watched_download_dirs = set()
        self._download_watcher = QFileSystemWatcher(self)
        self._download_watcher.directoryChanged.connect(self._schedule_download_rescan)
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(500)
        self._rescan_timer.timeout.connect(self._on_download_folder_changed)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self._poll_download_folder)
        self._refresh_download_watcher_dirs()
        self._poll_signature = self._download_dir_signature()
        self._poll_timer.start()

    def _schedule_download_rescan(self, path=None):
        if getattr(self, 'is_closing', False) or getattr(self, '_rescan_timer', None) is None:
            return
        if not self._rescan_timer.isActive():
            self._rescan_timer.start()

    def _on_download_folder_changed(self):
        if getattr(self, 'is_closing', False):
            return
        if not getattr(self, 'year_combo', None) or not getattr(self, 'month_combo', None):
            return
        try:
            self._refresh_download_watcher_dirs()
            self._poll_signature = self._download_dir_signature()
            prev = self.year_combo.currentText()
            self._populate_available_dates()
            if self.year_combo.currentText():
                self.status_bar.showMessage("Download folder updated - date/time refreshed", 3000)
            elif prev:
                self.status_bar.showMessage("Download folder updated", 3000)
        except Exception as e:
            self.log(f"Download folder rescan error: {e}")

    def _poll_download_folder(self):
        if getattr(self, 'is_closing', False):
            return
        try:
            sig = self._download_dir_signature()
        except Exception:
            return
        if sig != self._poll_signature:
            self._on_download_folder_changed()

    def _download_dir_signature(self):
        try:
            base = Path(self.input_dir)
            if not base.exists():
                return None
            entries = []
            for p in base.iterdir():
                try:
                    st = p.stat()
                    entries.append((p.name, int(st.st_mtime), st.st_size))
                except Exception:
                    continue
            return (int(base.stat().st_mtime), sorted(entries))
        except Exception:
            return None

    def _refresh_download_watcher_dirs(self):
        try:
            dirs = {str(Path(self.input_dir))}
            base = Path(self.input_dir)
            if base.exists():
                for p in base.iterdir():
                    if p.is_dir():
                        dirs.add(str(p))
            to_add = [d for d in dirs if d not in getattr(self, '_watched_download_dirs', set())]
            to_remove = [d for d in getattr(self, '_watched_download_dirs', set()) if d not in dirs]
            if to_remove:
                self._download_watcher.removePaths(to_remove)
            if to_add:
                self._download_watcher.addPaths(to_add)
            self._watched_download_dirs = dirs
        except Exception as e:
            self.log(f"Download watcher sync error: {e}")

    def check_updates_stub(self):
        release_notes = top_dir / "release-v3.0.5.md"
        snippet = ""
        if release_notes.exists():
            try:
                snippet = release_notes.read_text(encoding="utf-8", errors="replace")[:1200]
            except Exception:
                snippet = ""
        QMessageBox.information(
            self, "Check for Updates",
            "No automatic update server configured yet.\n"
            f"Current version: MonWatch Cyclone V3.0.5.1\n\nRelease notes:\n{snippet}"
        )

    def about_stub(self):
        QMessageBox.about(self, "About Monwatch",
                          "Monwatch - Cyclone V3.0.5.1 | Satellite Renderer\n"
                          "Meteorological Satellite Data Processing System for PWARDS-weather.\n"
                          "Developed and field-tested since 2025 by PWARDS-weather.")

    def fit_to_window(self):

        if hasattr(self, 'graphics_view'):
            self.graphics_view.fit_to_image()

    def toggle_left_panel(self):
        self.left_panel.setVisible(not self.left_panel.isVisible())

    def toggle_left_panel_float(self, float_panel: bool):
        if float_panel:
            if hasattr(self, 'left_panel_float_window') and self.left_panel_float_window:
                return
            
            self.left_panel_float_window = QWidget(self)
            self.left_panel_float_window.setWindowFlags(Qt.Window)
            self.left_panel_float_window.setWindowTitle("Left Panel")
            
            layout = QVBoxLayout(self.left_panel_float_window)
            layout.setContentsMargins(0, 0, 0, 0)
            
            self.left_panel.setParent(self.left_panel_float_window)
            layout.addWidget(self.left_panel)
            self.left_panel_float_window.show()
        else:
            if hasattr(self, 'left_panel_float_window') and self.left_panel_float_window:
                self.left_panel.setParent(self)
                self.splitter.insertWidget(0, self.left_panel)
                self.left_panel_float_window.close()
                self.left_panel_float_window.deleteLater()
                self.left_panel_float_window = None

    def toggle_centerview_float(self, float_view: bool):
        if float_view:
            if hasattr(self, 'centerview_float_window') and self.centerview_float_window:
                return
            
            self.centerview_float_window = QWidget(self)
            self.centerview_float_window.setWindowFlags(Qt.Window)
            self.centerview_float_window.setWindowTitle("Main Viewport")
            
            layout = QVBoxLayout(self.centerview_float_window)
            layout.setContentsMargins(0, 0, 0, 0)
            
            # We float the panel created in _init_center_panel (which is not stored as an attribute)
            # Wait, I need to find that panel. In _init_center_panel: panel = QWidget()... self.splitter.addWidget(panel)
            # I should store that panel as an attribute.
            if hasattr(self, 'center_panel'):
                self.center_panel.setParent(self.centerview_float_window)
                layout.addWidget(self.center_panel)
                self.centerview_float_window.show()
        else:
            if hasattr(self, 'centerview_float_window') and self.centerview_float_window:
                if hasattr(self, 'center_panel'):
                    self.center_panel.setParent(self)
                    self.splitter.insertWidget(1, self.center_panel)
                self.centerview_float_window.close()
                self.centerview_float_window.deleteLater()
                self.centerview_float_window = None

    def toggle_right_panel_float(self, float_panel: bool):
        if float_panel:
            if hasattr(self, 'right_panel_float_window') and self.right_panel_float_window:
                return
            
            self.right_panel_float_window = QWidget(self)
            self.right_panel_float_window.setWindowFlags(Qt.Window)
            self.right_panel_float_window.setWindowTitle("Right Panel")
            
            layout = QVBoxLayout(self.right_panel_float_window)
            layout.setContentsMargins(0, 0, 0, 0)
            
            self.right_tab_widget.setParent(self.right_panel_float_window)
            layout.addWidget(self.right_tab_widget)
            self.right_panel_float_window.show()
        else:
            if hasattr(self, 'right_panel_float_window') and self.right_panel_float_window:
                self.right_tab_widget.setParent(self)
                self.splitter.insertWidget(2, self.right_tab_widget)
                self.right_panel_float_window.close()
                self.right_panel_float_window.deleteLater()
                self.right_panel_float_window = None

    def toggle_right_panel(self):
        if hasattr(self, 'right_tab_widget') and self.right_tab_widget:
            visible = not self.right_tab_widget.isVisible()
            log.info("Toggle right panel: %s", "visible" if visible else "hidden")
            self.right_tab_widget.setVisible(visible)
        elif hasattr(self, 'right_panel') and self.right_panel:
            visible = not self.right_panel.isVisible()
            log.info("Toggle right panel (fallback): %s", "visible" if visible else "hidden")
            self.right_panel.setVisible(visible)

    # -- Multi Viewport methods ---------------------------------------------------

    def _toggle_multi_viewport(self, enabled):
        if enabled:
            if self._mv_manager is None:
                self._mv_manager = MultiViewportManager(self)
            count = self.mv_count_spin.value() if hasattr(self, 'mv_count_spin') else 1
            self._mv_manager.set_count(count)
            self.mv_launch_btn.setEnabled(True)
            self._rebuild_mv_tabs()
        else:
            if self._mv_manager:
                self._mv_manager.destroy()
            self._mv_manager = None
            self.mv_launch_btn.setEnabled(False)
            if hasattr(self, '_mv_viewport_tabs'):
                self._mv_viewport_tabs.clear()

    def _update_multi_viewport_count(self):
        if self._mv_manager:
            count = self.mv_count_spin.value()
            self._mv_manager.set_count(count)
        self._rebuild_mv_tabs()

    def _request_multi_band(self, window_index, band_name, panel=False):
        nc_path = getattr(self, 'current_nc_path', None)
        if not nc_path:
            self.log("No NC path loaded for band rendering.")
            return
        band_file_map = getattr(self, '_band_nc_map', None)
        max_px = self.preview_max_px or 2200
        cache = getattr(self, 'cache', None)
        arr = cache.get_precached(band_name) if cache else None
        if arr is not None:
            self._deliver_multi_band(window_index, band_name, arr, panel=panel)
            return
        def _load():
            try:
                _sat_mv = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
                _eng = get_engine(_sat_mv)
                log.debug("MV band: satellite=%s engine=%s band=%s", _sat_mv, _eng.__module__, band_name)
                result = _eng.band_as_image(
                    Path(nc_path), band_name, max_px, band_file_map=band_file_map)
                QTimer.singleShot(0, self, lambda: self._deliver_multi_band(window_index, band_name, result, panel=panel))
            except Exception as e:
                self.log(f"Error loading band for multi-viewport: {e}")
                try:
                    self.log(traceback.format_exc())
                except Exception:
                    pass
        self._thread_pool.submit(_load)

    def _deliver_multi_band(self, window_index, band_name, arr, panel=False):
        if arr is None:
            self.log(f"Could not render band '{band_name}' for multi-viewport.")
            return
        try:
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
            if arr.ndim == 2:
                arr = np.stack([arr, arr, arr, np.full_like(arr, 255)], axis=-1)
            elif arr.shape[2] == 3:
                h, w = arr.shape[:2]
                arr = np.concatenate([arr, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
            h, w = arr.shape[:2]
            qimg = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(qimg)
            if panel:
                if self._mp_manager and self._mp_manager.is_active():
                    p = self._mp_manager.panel(window_index)
                    if p is not None:
                        p.set_band_pixmap(pix)
                        self.log(f"Delivered band '{band_name}' to VP#{window_index + 1}")
            elif self._mv_manager and window_index < len(self._mv_manager.windows):
                self._mv_manager.windows[window_index].set_band_pixmap(pix)
                self.log(f"Delivered band '{band_name}' to MV#{window_index + 1}")
        except Exception as e:
            self.log(f"Error delivering multi band: {e}")
            try:
                self.log(traceback.format_exc())
            except Exception:
                pass

    def _request_multi_product(self, window_index, product_key, panel=False):
        nc_path = getattr(self, 'current_nc_path', None)
        if not nc_path:
            self.log("No NC path loaded for product rendering.")
            return
        band_file_map = getattr(self, '_band_nc_map', None)
        max_px = self.preview_max_px or 2200
        cache = getattr(self, 'cache', None)
        band_cache = cache.raw if cache else None
        bfm = band_file_map or None
        def _load():
            try:
                _sat_mv = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
                _eng = get_engine(_sat_mv)
                log.debug("MV product: satellite=%s engine=%s product=%s", _sat_mv, _eng.__module__, product_key)
                arr = None
                _prod_info = get_products(_sat_mv).get(product_key)
                if _prod_info and _prod_info.get("color_scale"):
                    ac = getattr(self, 'animation_controller', None)
                    if ac is not None:
                        arr = ac._render_color_scale_from_cache(product_key, _prod_info, band_cache or {})
                        if arr is not None:
                            arr = _eng._decimate_array(arr, max_px)
                if arr is None:
                    arr = _eng.composite_realtime(
                        product_key, Path(nc_path), max_px,
                        band_cache=band_cache, band_file_map=bfm)
                QTimer.singleShot(0, self, lambda: self._deliver_multi_product(window_index, product_key, arr, panel=panel))
            except Exception as e:
                self.log(f"Error loading product for multi-viewport: {e}")
                try:
                    self.log(traceback.format_exc())
                except Exception:
                    pass
        self._thread_pool.submit(_load)

    def _deliver_multi_product(self, window_index, product_key, arr, panel=False):
        if arr is None:
            self.log(f"Could not render product '{product_key}' for multi-viewport.")
            return
        try:
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
            if arr.ndim == 2:
                arr = np.stack([arr, arr, arr, np.full_like(arr, 255)], axis=-1)
            elif arr.shape[2] == 3:
                h, w = arr.shape[:2]
                arr = np.concatenate([arr, np.full((h, w, 1), 255, dtype=np.uint8)], axis=2)
            h, w = arr.shape[:2]
            qimg = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
            pix = QPixmap.fromImage(qimg)
            if panel:
                if self._mp_manager and self._mp_manager.is_active():
                    p = self._mp_manager.panel(window_index)
                    if p is not None:
                        p.set_composite_pixmap(pix)
                        self.log(f"Delivered product '{product_key}' to VP#{window_index + 1}")
            elif self._mv_manager and window_index < len(self._mv_manager.windows):
                self._mv_manager.windows[window_index].set_composite_pixmap(pix)
                self.log(f"Delivered product '{product_key}' to MV#{window_index + 1}")
        except Exception as e:
            self.log(f"Error delivering multi product: {e}")
            try:
                self.log(traceback.format_exc())
            except Exception:
                pass

    def _request_multi_forecast(self, window_index, storm_id, panel=False):
        storm_data = self.nhc_storms.get(storm_id)
        if not storm_data:
            return
        def _gen():
            try:
                pix = self.forecast_controller._generate_forecast_pixmap(storm_id, storm_data)
                QTimer.singleShot(0, self, lambda: self._deliver_multi_forecast(window_index, pix, panel=panel))
            except Exception as e:
                self.log(f"Error generating forecast for multi-viewport: {e}")
        self._thread_pool.submit(_gen)

    def _deliver_multi_forecast(self, window_index, pix, panel=False):
        if not pix:
            return
        if panel:
            if self._mp_manager and self._mp_manager.is_active():
                p = self._mp_manager.panel(window_index)
                if p is not None:
                    p.set_forecast_pixmap(pix)
        elif self._mv_manager and window_index < len(self._mv_manager.windows):
            self._mv_manager.windows[window_index].set_forecast_pixmap(pix)

    def _generate_forecast_pixmap(self, *args, **kwargs):
        return self.forecast_controller._generate_forecast_pixmap(*args, **kwargs)
    def _push_animation_to_multi_viewport(self, pixmap):
        if self._mp_manager and self._mp_manager.is_active():
            for p in self._mp_manager.panels_in_mode("animation"):
                p.set_animation_pixmap(pixmap)
        if self._mv_manager:
            for win in self._mv_manager.get_windows():
                if win.current_mode() == "animation":
                    win.set_animation_pixmap(pixmap)

    def _push_texture_to_multi_globe(self, image=None):
        if self._mp_manager and self._mp_manager.is_active():
            for p in self._mp_manager.panels_in_mode("3d globe"):
                p.push_texture_to_globe(image)
        if self._mv_manager:
            globe_wins = self._mv_manager.get_globe_windows()
            for win in globe_wins:
                win.push_texture_to_globe(image)

    def _push_forecast_to_multi_globe(self, image=None):
        if self._mp_manager and self._mp_manager.is_active():
            for p in self._mp_manager.panels_in_mode("3d globe"):
                p.push_forecast_to_globe(image)
        if self._mv_manager:
            for win in self._mv_manager.get_globe_windows():
                win.push_forecast_to_globe(image)

    @staticmethod
    def _pil_to_qimage(self, *args, **kwargs):
        return self.export_controller._pil_to_qimage(*args, **kwargs)
    def _pil_to_qimage(self, *args, **kwargs):
        return self.export_controller._pil_to_qimage(*args, **kwargs)
    def _pil_to_qimage(self, *args, **kwargs):
        return self.export_controller._pil_to_qimage(*args, **kwargs)
    def toggle_always_on_top(self, checked):
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            if self._left_reveal_button:
                self._left_reveal_button.hide()
            self.left_panel.setVisible(True)
        else:
            self.showFullScreen()
            if self.left_panel_auto_hide:
                self._apply_fullscreen_left_panel_visibility()

    def _update_sataid_tab_visibility(self):
        if not hasattr(self, 'sataid_tab_index') or not hasattr(self, 'type_combo'):
            return
        is_sataid = self.type_combo.currentText() == "SATAID"
        if is_sataid:
            if self.current_mode == "sataid":
                for i in range(self.right_tab_widget.count()):
                    self.right_tab_widget.setTabVisible(i, i == self.sataid_tab_index)
                self.right_tab_widget.setCurrentIndex(self.sataid_tab_index)
            else:
                self.right_tab_widget.setTabVisible(0, True)
                if hasattr(self, '_pro_tab_index'):
                    self.right_tab_widget.setTabVisible(self._pro_tab_index, self.current_mode == "professional")
                if hasattr(self, 'tracks_tab_index'):
                    self.right_tab_widget.setTabVisible(self.tracks_tab_index, self.current_mode in ("professional", "hobby"))
                if hasattr(self, 'animation_tab_index'):
                    self.right_tab_widget.setTabVisible(self.animation_tab_index, True)
                self.right_tab_widget.setTabVisible(self.sataid_tab_index, False)
                if hasattr(self, 'multi_viewport_tab_index'):
                    self.right_tab_widget.setTabVisible(self.multi_viewport_tab_index, True)
        else:
            self.right_tab_widget.setTabVisible(0, True)
            if hasattr(self, '_pro_tab_index'):
                self.right_tab_widget.setTabVisible(self._pro_tab_index, self.current_mode == "professional")
            if hasattr(self, 'tracks_tab_index'):
                self.right_tab_widget.setTabVisible(self.tracks_tab_index, self.current_mode in ("professional", "hobby"))
            if hasattr(self, 'animation_tab_index'):
                self.right_tab_widget.setTabVisible(self.animation_tab_index, True)
            self.right_tab_widget.setTabVisible(self.sataid_tab_index, False)
            if hasattr(self, 'multi_viewport_tab_index'):
                self.right_tab_widget.setTabVisible(self.multi_viewport_tab_index, True)

    def _connect_sataid_signals(self):
        if not hasattr(self, 'sataid_panel'):
            return
        p = self.sataid_panel
        p.bandChanged.connect(self._on_sataid_band_changed)
        p.brightnessChanged.connect(self._on_sataid_brightness)
        p.contrastChanged.connect(self._on_sataid_contrast)
        p.gridToggled.connect(self.toggle_grid)
        p.coastToggled.connect(self.toggle_coastlines)
        p.overlayToggled.connect(self._on_sataid_overlay_toggled)
        p.gridIntervalChanged.connect(self._on_sataid_grid_interval)
        p.functionChanged.connect(self._on_sataid_function_changed)
        p.measModeChanged.connect(self._on_sataid_meas_mode_changed)
        p.playbackStepped.connect(self._on_sataid_playback_step)
        p.playbackPlayToggled.connect(self._on_sataid_playback_toggle)

    def _on_sataid_band_changed(self, band: str):
        for b, cb in self.band_checkboxes.items():
            if b == band:
                cb.blockSignals(True)
                cb.setChecked(True)
                cb.blockSignals(False)
            else:
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
        self.satellite_controller.load_selected_band_or_product()
        self.contour.clear_contour_overlay()

    def _on_sataid_brightness(self, val: float):
        self._sataid_brightness = val
        self._reapply_sataid_adjustments()

    def _on_sataid_contrast(self, val: float):
        self._sataid_contrast = val
        self._reapply_sataid_adjustments()

    def _reapply_sataid_adjustments(self):
        if not hasattr(self, '_sataid_current_data'):
            return
        data = getattr(self, '_sataid_current_data', None)
        if data is None:
            return
        brit = getattr(self, '_sataid_brightness', 0.0)
        cntr = getattr(self, '_sataid_contrast', 1.0)
        self._render_sataid(data, brit, cntr)

    def _render_sataid(self, data: np.ndarray, brit: float = 0.0, cntr: float = 1.0):
        valid = data[~np.isnan(data)]
        if len(valid) == 0:
            return
        vmin, vmax = float(valid.min()), float(valid.max())
        if vmax <= vmin:
            vmax = vmin + 1e-6
        norm = np.clip((data - vmin) / (vmax - vmin), 0, 1)
        norm = np.clip((norm - 0.5) * cntr + 0.5 + brit, 0, 1)
        gray = (norm * 255).astype(np.uint8)
        alpha = np.where(np.isnan(data), 0, 255).astype(np.uint8)

        if gray.ndim == 2:
            h, w = gray.shape
            rgba = np.stack([gray, gray, gray, alpha], axis=-1)
        else:
            h, w = gray.shape[:2]
            rgba = gray if gray.shape[2] == 4 else np.concatenate(
                [gray[:, :, :3],
                 np.full((h, w, 1), 255, dtype=np.uint8)], axis=-1)

        raw_bytes = rgba.tobytes()
        qimg = QImage(raw_bytes, w, h, w * 4, QImage.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qimg)
        self.graphics_view.set_image(pixmap, preserve_view=True, quality_level=1.0, is_original=False)
        self.graphics_view.viewport().update()

    def _on_sataid_grid_interval(self, deg: float):
        self._sataid_grid_spacing = deg
        if hasattr(self, 'update_overlays'):
            self.overlay_controller.update_overlays()

    def _on_sataid_overlay_toggled(self, name: str, checked: bool):
        if name == "wind":
            self.overlay_controller.toggle_winds(checked)
            return
        attr = f"sataid_{name}_enabled"
        setattr(self, attr, checked)
        if hasattr(self, 'update_overlays'):
            self.overlay_controller.update_overlays()

    def _on_sataid_function_changed(self, func: str):
        self.log(f"SATAID function: {func}")

    def _on_sataid_meas_mode_changed(self, mode: str):
        self.log(f"Measure mode: {mode}")
        if mode == "Contour":
            if hasattr(self, 'pro_contours') and not self.pro_contours.isChecked():
                self.pro_contours.blockSignals(True)
                self.pro_contours.setChecked(True)
                self.pro_contours.blockSignals(False)
            self.overlay_controller.toggle_pro_contours(True)
        else:
            if hasattr(self, 'pro_contours') and self.pro_contours.isChecked():
                self.pro_contours.blockSignals(True)
                self.pro_contours.setChecked(False)
                self.pro_contours.blockSignals(False)
            self.overlay_controller.toggle_pro_contours(False)

    def _on_sataid_playback_step(self, step: int):
        times = getattr(self, '_available_raw_times', None)
        if not times:
            self.log("SATAID: no time sequence loaded for playback")
            return
        hour = getattr(self, 'hour_combo', None)
        minute = getattr(self, 'minute_combo', None)
        if hour is None or minute is None:
            return
        current = (hour.currentText() or "") + (minute.currentText() or "")
        if current in times:
            idx = times.index(current) + step
        else:
            idx = 0 if step > 0 else len(times) - 1
        idx = max(0, min(len(times) - 1, idx))
        target = times[idx]
        hour.blockSignals(True)
        minute.blockSignals(True)
        hour.setCurrentText(target[:2])
        minute.setCurrentText(target[2:])
        hour.blockSignals(False)
        minute.blockSignals(False)
        if hasattr(self, 'satellite_controller'):
            self.satellite_controller._filter_minutes_for_hour()
        if hasattr(self, 'auto_load_bands'):
            self.auto_load_bands()
        panel = getattr(self, 'sataid_panel', None)
        if panel is not None:
            panel.set_playback_range(len(times))
            panel.set_playback_position(idx)
            panel.set_time(target)

    def _on_sataid_playback_toggle(self, playing: bool):
        if playing:
            if not hasattr(self, '_sataid_play_timer'):
                from PySide6.QtCore import QTimer
                timer = QTimer(self)
                timer.timeout.connect(lambda: self._on_sataid_playback_step(1))
                self._sataid_play_timer = timer
            interval = 300
            speed = getattr(getattr(self, 'sataid_panel', None), 'speed_slider', None)
            if speed is not None:
                v = speed.value()
                interval = int(2000 * (100 - v) / 100.0) + 100
            self._sataid_play_timer.start(interval)
            self.log("SATAID AUTO playback started")
        else:
            timer = getattr(self, '_sataid_play_timer', None)
            if timer is not None:
                timer.stop()
            self.log("SATAID AUTO playback stopped")

    def _on_tab_context_menu(self, *args, **kwargs):
        return self.forecast_controller._on_tab_context_menu(*args, **kwargs)
    def _save_active_tab(self, *args, **kwargs):
        return self.forecast_controller._save_active_tab(*args, **kwargs)
    def _restore_active_tab(self, *args, **kwargs):
        return self.forecast_controller._restore_active_tab(*args, **kwargs)
    def _on_tab_tear_off(self, *args, **kwargs):
        return self.forecast_controller._on_tab_tear_off(*args, **kwargs)
    def _select_nearest_visible_tab(self, *args, **kwargs):
        return self.forecast_controller._select_nearest_visible_tab(*args, **kwargs)
    def _apply_mode(self, mode):
        self.current_mode = mode
        self.settings.set("mode", mode)

        if hasattr(self, 'options_menu'):
            self.options_menu.menuAction().setVisible(mode == "sataid")

        if hasattr(self, 'right_tab_widget') and hasattr(self, '_pro_tab_index'):
            is_pro = (mode == "professional")
            is_hobby = (mode == "hobby")
            is_casual = (mode == "casual")
            is_sataid = (mode == "sataid")

            if is_sataid:
                for i in range(self.right_tab_widget.count()):
                    self.right_tab_widget.setTabVisible(i, i == self.sataid_tab_index)
                self.right_tab_widget.setCurrentIndex(self.sataid_tab_index)
            else:
                self.right_tab_widget.setTabVisible(0, True)
                self.right_tab_widget.setTabVisible(self._pro_tab_index, is_pro)
                if hasattr(self, 'tracks_tab_index'):
                    self.right_tab_widget.setTabVisible(self.tracks_tab_index, is_pro or is_hobby)
                if hasattr(self, 'animation_tab_index'):
                    self.right_tab_widget.setTabVisible(self.animation_tab_index, is_pro or is_hobby or is_casual)
                if hasattr(self, 'sataid_tab_index'):
                    self.right_tab_widget.setTabVisible(self.sataid_tab_index, False)
                if hasattr(self, 'multi_viewport_tab_index'):
                    self.right_tab_widget.setTabVisible(self.multi_viewport_tab_index, is_pro or is_hobby or is_casual)

            if is_pro:
                self.right_tab_widget.setCurrentIndex(self._pro_tab_index)
                if hasattr(self, 'update_devkit_band_lists') and getattr(self, 'available_bands', None):
                    self.update_devkit_band_lists(self.available_bands)
            self.right_tab_widget.tabBar().setVisible(True)

        if hasattr(self, 'right_tab_widget') and hasattr(self, 'splitter'):
            if mode == "sataid":
                self.right_tab_widget.setMinimumWidth(0)
                self.right_tab_widget.setMaximumWidth(178)
                sizes = self.splitter.sizes()
                if len(sizes) >= 3:
                    total = sum(sizes)
                    self.splitter.setSizes([max(64, int(total*0.08)), max(400, total - sizes[0] - 178), 178])
            else:
                self.right_tab_widget.setMinimumWidth(460)
                self.right_tab_widget.setMaximumWidth(16777215)

        if mode == "professional":
            self.secondary_status_label.setVisible(True)
        else:
            self.secondary_status_label.setVisible(False)

        if hasattr(self, '_press_group'):
            self._press_group.setVisible(mode == "professional")

        if mode == "professional":
            if hasattr(self, 'pro_contours') and not self.pro_contours.isChecked():
                self.pro_contours.blockSignals(True)
                self.pro_contours.setChecked(True)
                self.pro_contours.blockSignals(False)
            self.overlay_controller.toggle_pro_contours(True)
        else:
            if hasattr(self, 'pro_contours') and self.pro_contours.isChecked():
                self.pro_contours.blockSignals(True)
                self.pro_contours.setChecked(False)
                self.pro_contours.blockSignals(False)
            self.overlay_controller.toggle_pro_contours(False)

        self._update_broadcast_mode(mode)

    def _update_broadcast_mode(self, mode):
        if mode == "broadcast":
            if self._broadcast_process is None or self._broadcast_process.poll() is not None:
                self.broadcast_progress.show()
                self.broadcast_progress.repaint()
                broadcast_path = os.path.join(os.path.dirname(__file__), "services", "broadcast_app.py")
                dt_str = ""
                if self.current_datetime:
                    dt = self.current_datetime
                    if isinstance(dt, str):
                        try:
                            parts = dt.replace("_", " ").split()
                            if len(parts) >= 1:
                                ymd = parts[0].split("-")
                                if len(ymd) == 3:
                                    year, month, day = int(ymd[0]), int(ymd[1]), int(ymd[2])
                                    if len(parts) > 1:
                                        hm = parts[1].split("_")[0]
                                        hour = int(hm[:2]) if len(hm) >= 2 else 0
                                        minute = int(hm[2:4]) if len(hm) >= 4 else 0
                                    else:
                                        hour, minute = 0, 0
                                    dt_str = datetime(year, month, day, hour, minute).isoformat()
                        except Exception:
                            pass
                    else:
                        dt_str = dt.isoformat()
                self._broadcast_process = subprocess.Popen(
                    [sys.executable, broadcast_path, dt_str],
                    cwd=os.path.dirname(__file__),
                )
                self.broadcast_progress.hide()
                self._broadcast_forecast_timer.start()
            if hasattr(self, 'graphics_view'):
                self.graphics_view.setVisible(True)
            if hasattr(self, 'right_tab_widget'):
                self.right_tab_widget.setVisible(True)
        else:
            if self._broadcast_process and self._broadcast_process.poll() is None:
                self._broadcast_process.terminate()
                try:
                    self._broadcast_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._broadcast_process.kill()
                    self._broadcast_process.wait(timeout=3)
                self._broadcast_process = None
            self._broadcast_forecast_timer.stop()
            if hasattr(self, 'graphics_view'):
                self.graphics_view.setVisible(True)
            if hasattr(self, 'right_tab_widget'):
                self.right_tab_widget.setVisible(True)

    def _toggle_broadcast(self):
        if self._broadcast_process and self._broadcast_process.poll() is None:
            self._broadcast_process.terminate()
            try:
                self._broadcast_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._broadcast_process.kill()
                self._broadcast_process.wait(timeout=3)
            self._broadcast_process = None
        else:
            self._update_broadcast_mode("broadcast")

    def _export_broadcast_forecast(self):
        try:
            from src.services.forecast_provider import (
                export_forecast_texture,
                export_wind_field_textures,
                export_processed_forecast_texture,
            )
            api_key = self.settings.get("windy_api_key", "")
            method = "raw"
            provider = "windy"
            source = "openmeteo"
            model = ""
            variable = "temp"
            fxx = 0
            prefs_path = os.path.join(
                QStandardPaths.writableLocation(QStandardPaths.TempLocation),
                "broadcast_forecast_source.json",
            )
            if os.path.isfile(prefs_path):
                with open(prefs_path) as f:
                    prefs = json.load(f)
                    method = prefs.get("method", "raw")
                    provider = prefs.get("provider", "windy")
                    source = prefs.get("source", "openmeteo")
                    model = prefs.get("model", "")
                    variable = prefs.get("variable", "temp")
                    fxx = prefs.get("fxx", 0)

            if method == "processed":
                export_processed_forecast_texture(
                    provider=provider, variable=variable, api_key=api_key or None,
                )
            else:
                export_forecast_texture(
                    source=source, model=model, api_key=api_key,
                    variable=variable, fxx=fxx,
                )
                if variable == "wind":
                    export_wind_field_textures(source=source, model=model, fxx=fxx)
        except Exception:
            pass

    def handle_zoom_change(self, zoom_factor):
        self.status_bar.showMessage(f"Zoom: {zoom_factor:.2f}x")

    def adjust_slider(self, direction: int):

        if not hasattr(self, 'graphics_view') or not self.graphics_view.zoom_enabled:
            return
        gv = self.graphics_view
        factor = 1.25 if direction > 0 else (1.0 / 1.25)
        new_zoom = gv.zoom_factor * factor
        if new_zoom < gv.min_zoom:
            factor = gv.min_zoom / gv.zoom_factor
            new_zoom = gv.min_zoom
        elif new_zoom > gv.max_zoom:
            factor = gv.max_zoom / gv.zoom_factor
            new_zoom = gv.max_zoom
        gv.zoom_factor = new_zoom
        gv.scale(factor, factor)
        gv._user_has_zoomed = True
        gv.zoomChanged.emit(gv.zoom_factor)

    def reset_view(self):

        if hasattr(self, 'graphics_view'):
            self.graphics_view.fit_to_image()

    def confirm_load_original(self):

        pass

    def load_full_resolution_image(self):
        nc_path = self.current_original if isinstance(self.current_original, Path) else self.satellite_controller._current_nc_file()
        if not nc_path:
            self.log("No NC file to load full resolution")
            return
        self.setCursor(QCursor(Qt.WaitCursor))
        try:
            _sat = self.sat_combo.currentText() if hasattr(self, 'sat_combo') else "himawari9"
            _eng = get_engine(_sat)
            log.debug("Full-res render: satellite=%s engine=%s", _sat, _eng.__module__)
            if self.selected_product:
                bfm = getattr(self, '_band_nc_map', {}) or None
                arr = _eng.composite_realtime(self.selected_product, nc_path, max_px=100000, band_file_map=bfm)
            else:
                band = next((b for b, cb in self.band_checkboxes.items() if cb.isChecked()), None)
                if band:
                    arr = _eng.band_as_image(nc_path, band, max_px=100000)
                else:
                    arr = None
            if arr is not None:
                h, w = arr.shape[:2]

                qimg = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
                pixmap = QPixmap.fromImage(qimg)
                self.graphics_view.set_image(pixmap, preserve_view=True, quality_level=1.0, is_original=True)
                self.log("Full resolution image loaded")
            else:
                self.log("Could not generate full resolution image")
        except Exception as e:
            self.log(f"Full resolution error: {e}")
        finally:
            self.setCursor(QCursor(Qt.ArrowCursor))

    def _run_latest_symlink(self):
        return self.satellite_controller._run_latest_symlink()

    def choose_file(self):

        fps, _ = QFileDialog.getOpenFileNames(
            self, "Open Image / GeoTIFF",
            str(Path.home()),
            "All Files (*)"
        )
        if fps:
            self._process_files(fps)

    def choose_folder(self):

        folder = QFileDialog.getExistingDirectory(self, "Select Folder with Imagery", str(Path.home()))
        if folder:
            self.log(f"Folder selected for future batch use: {folder}")

            for p in Path(folder).glob("*.tif"):
                self.process_file(str(p))
                break

    def load_preview_images(self):
        if self.current_base:
            return
        startup_mode = self.settings.get("startup_mode", "logo")
        if startup_mode == "logo":
            logo_path = top_dir / 'public' / 'images' / 'MonWatch.png'
            if logo_path.exists():
                pix = QPixmap(str(logo_path))
                self.graphics_view.set_image(pix, preserve_view=False, quality_level=1.0, is_original=False)
                self.log(f'Loaded startup logo: {logo_path.name}')
                return
        if self.latest_path.exists():
            pix = QPixmap(str(self.latest_path))
            self.graphics_view.set_image(pix, preserve_view=False, quality_level=1.0, is_original=False)
            self.log(f'Loaded preview: {self.latest_path.name}')

    def refresh_image(self):
        self.clear_overlays()
        self._last_overlay_key = None
        self._last_grid_cache_key = None
        self._last_coast_cache_key = None
        self._ol_geo_sig = None
        for _attr in ('_grid_path_cache', '_coast_path_cache',
                      '_grid_image_cache', '_coast_image_cache',
                      '_projected_coast_cache', '_projected_grid_cache',
                      '_sataid_coast_path_cache', '_resized_display_cache'):
            _c = getattr(self, _attr, None)
            if isinstance(_c, dict):
                _c.clear()
        self._winds_persist = False
        self._mw_persist = False
        self.overlay_controller.update_overlays()
        if self._gl_mode_active():
            self.gl_map_widget.reset_view()
        self.reset_view()

    def _show_caching_dialog(self, nc_path, band_names):
        """Create and show a loading dialog for background caching."""
        from PySide6.QtWidgets import QApplication
        self._dialog_opened_at = __import__('time').perf_counter()
        self._suppress_display_until_cache_done = True
        self.loading_dialog = CachingDialog(self, title="Loading Satellite Data")
        self.loading_dialog.setLabelText(f"Preparing to cache {len(band_names)} band(s) from {nc_path.name}...")
        self.loading_dialog.show()
        self.loading_dialog.repaint()
        QApplication.processEvents()

    GOES_BAND_MAP = {
            'C01': 'B01', 'C02': 'B03', 'C03': 'B02'
        }
    for _gi in range(4, 17):
        GOES_BAND_MAP[f'C{_gi:02d}'] = f'B{_gi:02d}'

    def _ensure_temp_satellite(self, sat_id, sat_label):
        return self.satellite_controller._ensure_temp_satellite(sat_id, sat_label)

    def _remove_temp_satellite(self):
        return self.satellite_controller._remove_temp_satellite()

    def _on_sat_combo_changed(self, text):
        return self.satellite_controller._on_sat_combo_changed(text)

    def process_dropped_nc_files(self, files):
        return self.satellite_controller.process_dropped_nc_files(files)

    def process_file(self, fp):
        return self.satellite_controller.process_file(fp)

    def _process_files(self, fps):
        return self.satellite_controller._process_files(fps)

    def _handle_sataid_drop(self, path):
        return self.satellite_controller._handle_sataid_drop(path)

    def _handle_sataid_folder_drop(self, folder):
        return self.satellite_controller._handle_sataid_folder_drop(folder)

    def extract_georeferencing(self, tiff_path):
        return self.satellite_controller.extract_georeferencing(tiff_path)

    def clear_overlays(self):
        self.overlay_controller._remove_all_overlay_items()

    def load_current_image(self):
        if not self.current_original:
            return False
        try:
            with Image.open(self.current_original) as img:
                if max(img.width, img.height) > self.preview_max_px:
                    step = max(1, max(img.width, img.height) // self.preview_max_px)
                    img = img.reduce(step)
                if img.mode == 'RGBA':
                    qimg = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888)
                else:
                    rgb = img.convert('RGB')
                    qimg = QImage(rgb.tobytes("raw", "RGB"), rgb.width, rgb.height, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimg)
                self.graphics_view.set_image(pixmap, preserve_view=True, quality_level=1.0, is_original=False)
                self.log(f'Loaded: {self.current_original.name}')
                return True
        except Exception as e:
            self.log(f"Error loading image: {e}")
            return False

    def on_band_checkbox_toggled(self, checked):
        import time as _t
        self.update_selected_bands_label()
        sender = self.sender()
        if checked and sender is not None:
            band = next((b for b, cb in self.band_checkboxes.items() if cb is sender), None)
            if band and self._mp_route_main_selection(band=band):
                return
            for band, cb in self.band_checkboxes.items():
                if cb is not sender:
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            self.selected_product = None
            self._highlight_tile("")
            for band, cb in self.band_checkboxes.items():
                if cb is sender:
                    self._click_log = (band, _t.perf_counter())
                    self.log(f"Clicked - {band}")
                    break
            self.satellite_controller.load_selected_band_or_product()
            self.update_band_info()
            self.contour.clear_contour_overlay()
            if hasattr(self, 'sataid_panel'):
                self.sataid_panel.set_band(band)
        else:
            any_checked = any(cb.isChecked() for cb in self.band_checkboxes.values())
            if not any_checked and not self.selected_product:
                self.load_preview_images()

    def update_selected_bands_label(self):
        checked_count = sum(1 for cb in self.band_checkboxes.values() if cb.isChecked())
        if checked_count == 0:
            self.selected_bands_label.setText("Selected: 0 bands")
        elif checked_count == 1:
            self.selected_bands_label.setText("Selected: 1 band")
        else:
            self.selected_bands_label.setText(f"Selected: {checked_count} bands")

    @staticmethod
    def _restore_combo(combo, prev_text):
        idx = combo.findText(prev_text)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _populate_available_dates(self):
        return self.satellite_controller._populate_available_dates()

    def _discover_available_dates(self):
        return self.satellite_controller._discover_available_dates()

    def _update_month_day_combos(self):
        return self.satellite_controller._update_month_day_combos()

    def _populate_available_times(self, files=None):
        return self.satellite_controller._populate_available_times(files)

    def _filter_minutes_for_hour(self):
        return self.satellite_controller._filter_minutes_for_hour()

    def _load_sataid_bands(self):
        return self.satellite_controller._load_sataid_bands()

    def _cache_sataid_bands(self):
        return self.satellite_controller._cache_sataid_bands()

    def _on_sataid_cache_done(self):
        return self.satellite_controller._on_sataid_cache_done()

    def _load_sataid_band_file(self, band):
        return self.satellite_controller._load_sataid_band_file(band)

    def _load_pwards_stream(self):
        return self.satellite_controller._load_pwards_stream()

    def _on_pwards_config_changed(self):
        return self.satellite_controller._on_pwards_config_changed()

    def _on_pwards_bands_ready(self, band_files):
        return self.satellite_controller._on_pwards_bands_ready(band_files)

    def auto_load_bands(self):
        return self.satellite_controller.auto_load_bands()

    def update_satellite_lon(self):
        return self.satellite_controller.update_satellite_lon()

    def _update_type_options(self):
        return self.satellite_controller._update_type_options()

    def get_current_product(self):
        return self.satellite_controller.get_current_product()

    def get_current_himawari_product(self) -> str:
        return self.get_current_product()

    def _update_anim_band_labels(self, *args, **kwargs):
        return self.animation_controller._update_anim_band_labels(*args, **kwargs)
    def _update_anim_type_options(self, *args, **kwargs):
        return self.animation_controller._update_anim_type_options(*args, **kwargs)
    def get_anim_himawari_product(self, *args, **kwargs):
        return self.animation_controller.get_anim_himawari_product(*args, **kwargs)
    def _update_day_combo(self):
        try:
            year = int(self.year_combo.currentText())
            month = int(self.month_combo.currentText())
            if month == 12:
                last_day = 31
            else:
                first_day_next = date(year, month + 1, 1)
                last_day = (first_day_next - timedelta(days=1)).day
            days = [f"{d:02d}" for d in range(1, last_day + 1)]
            self.day_combo.clear()
            self.day_combo.addItems(days)
        except Exception:
            pass

    def _add_options_menu_items(self, menu):

        a = QAction("&Data list...", self)
        a.setShortcut(QKeySequence("Ctrl+L"))
        menu.addAction(a)

        a = QAction("RGB list", self)
        menu.addAction(a)

        a = QAction("&Bird's-eye", self)
        menu.addAction(a)

        a = QAction("&Geographical view", self)
        menu.addAction(a)

        album_menu = menu.addMenu("Album &View")
        a = QAction("Sensor Album", self)
        a.setShortcut(QKeySequence("Ctrl+J"))
        album_menu.addAction(a)
        a = QAction("Time Series", self)
        a.setShortcut(QKeySequence("Ctrl+V"))
        album_menu.addAction(a)
        a = QAction("NWP Album", self)
        a.setShortcut(QKeySequence("Ctrl+G"))
        album_menu.addAction(a)

        menu.addSeparator()

        erase_menu = menu.addMenu("&Erase")
        a = QAction("Erase Data", self)
        a.setShortcut(QKeySequence("Del"))
        erase_menu.addAction(a)
        a = QAction("Erase All", self)
        a.setShortcut(QKeySequence("Ctrl+Del"))
        erase_menu.addAction(a)
        a = QAction("Erase Radar", self)
        erase_menu.addAction(a)
        a = QAction("Erase NWP", self)
        erase_menu.addAction(a)

        print_menu = menu.addMenu("&Print")
        a = QAction("Print Image", self)
        a.setShortcut(QKeySequence("Ctrl+P"))
        print_menu.addAction(a)
        a = QAction("Print Screen", self)
        a.setShortcut(QKeySequence("Ctrl+H"))
        print_menu.addAction(a)
        a = QAction("Page Setup", self)
        a.setShortcut(QKeySequence("Ctrl+U"))
        print_menu.addAction(a)

        bitmap_menu = menu.addMenu("&Bitmap")
        a = QAction("Output Bitmap", self)
        a.setShortcut(QKeySequence("Ctrl+O"))
        a.triggered.connect(self._export_bitmap)
        bitmap_menu.addAction(a)
        a = QAction("Output GeoTIFF", self)
        a.triggered.connect(self._export_geotiff)
        bitmap_menu.addAction(a)
        a = QAction("Output Serial Bitmap", self)
        a.triggered.connect(self._export_serial_bitmap)
        bitmap_menu.addAction(a)
        a = QAction("Output animation", self)
        a.triggered.connect(self._export_animation)
        bitmap_menu.addAction(a)

        a = QAction("Copy image", self)
        a.setShortcut(QKeySequence("Backspace"))
        menu.addAction(a)

        menu.addSeparator()

        a = QAction("Position adjustment...", self)
        a.setShortcut(QKeySequence("Ctrl+Y"))
        menu.addAction(a)

        a = QAction("Screen size...", self)
        a.setShortcut(QKeySequence("Ctrl+Z"))
        menu.addAction(a)

        a = QAction("Line color...", self)
        a.setShortcut(QKeySequence("Ctrl+C"))
        menu.addAction(a)

        a = QAction("Date&time...", self)
        a.setShortcut(QKeySequence("Ctrl+K"))
        menu.addAction(a)

        a = QAction("Map element...", self)
        a.setShortcut(QKeySequence("Ctrl+Q"))
        menu.addAction(a)

        display_menu = menu.addMenu("Panel displaying")
        for name in ["Secondary Names", "Operation panel", "Message Panel", "EIR Panel"]:
            a = QAction(name, self, checkable=True)
            a.setChecked(name != "Secondary Names")
            if name == "Secondary Names":
                a.toggled.connect(lambda checked: self.sataid_panel.set_secondary_names(not checked))
            display_menu.addAction(a)

        menu.addSeparator()

        a = QAction("ToolTips", self, checkable=True)
        a.setChecked(True)
        menu.addAction(a)

        a = QAction("Zoom ratio", self, checkable=True)
        a.setChecked(True)
        menu.addAction(a)

        a = QAction("Scroll zooming", self, checkable=True)
        a.setChecked(True)
        menu.addAction(a)

    def _create_menu_bar(self):
        menubar = self.menuBar()

        system_menu = menubar.addMenu("System")
        bg_to_nc_action = QAction("Run bg_to_nc", self)
        bg_to_nc_action.triggered.connect(self.run_bg_to_nc)
        system_menu.addAction(bg_to_nc_action)
        process_dat_action = QAction("Open Data Downloader (Process_dat)", self)
        process_dat_action.triggered.connect(self.run_process_dat)
        system_menu.addAction(process_dat_action)
        ascat_action = QAction("ASCAT Data & Metadata...", self)
        ascat_action.triggered.connect(self.open_ascat_window)
        system_menu.addAction(ascat_action)
        microwave_action = QAction("Microwave Data & Metadata...", self)
        microwave_action.triggered.connect(self.open_microwave_window)
        system_menu.addAction(microwave_action)
        system_menu.addSeparator()
        perf_action = QAction("Settings...", self)
        perf_action.triggered.connect(self.open_settings_window)
        system_menu.addAction(perf_action)
        system_menu.addSeparator()
        account_action = QAction("Account...", self)
        account_action.triggered.connect(self.open_account_window)
        system_menu.addAction(account_action)
        system_menu.addSeparator()
        check_updates_action = QAction("Check for Updates", self)
        check_updates_action.triggered.connect(self.check_updates_stub)
        system_menu.addAction(check_updates_action)
        about_action = QAction("About Monwatch", self)
        about_action.triggered.connect(self.about_stub)
        system_menu.addAction(about_action)
        system_menu.addSeparator()
        self.broadcast_action = QAction("Run Broadcasting", self)
        self.broadcast_action.triggered.connect(self._toggle_broadcast)
        system_menu.addAction(self.broadcast_action)

        self.options_menu = menubar.addMenu("&Options")
        self._add_options_menu_items(self.options_menu)
        self.options_menu.menuAction().setVisible(False)

        prefs_menu = menubar.addMenu("Preferences")
        self.mode_group = QActionGroup(self)
        self.professional_action = QAction("Professional", self, checkable=True)
        self.casual_action = QAction("Casual", self, checkable=True)
        self.hobby_action = QAction("Hobby", self, checkable=True)
        self.sataid_action = QAction("SATAID", self, checkable=True)
        self.mode_group.addAction(self.professional_action)
        self.mode_group.addAction(self.casual_action)
        self.mode_group.addAction(self.hobby_action)
        self.mode_group.addAction(self.sataid_action)
        prefs_menu.addAction(self.professional_action)
        prefs_menu.addAction(self.casual_action)
        prefs_menu.addAction(self.hobby_action)
        prefs_menu.addAction(self.sataid_action)
        mode_map = {"professional": self.professional_action, "hobby": self.hobby_action, "casual": self.casual_action, "sataid": self.sataid_action}
        mode_map.get(self.current_mode, self.casual_action).setChecked(True)
        self.professional_action.triggered.connect(lambda: self._apply_mode("professional"))
        self.casual_action.triggered.connect(lambda: self._apply_mode("casual"))
        self.hobby_action.triggered.connect(lambda: self._apply_mode("hobby"))
        self.sataid_action.triggered.connect(lambda: self._apply_mode("sataid"))

        window_menu = menubar.addMenu("Window")
        reset_view_action = QAction("Reset View", self)
        reset_view_action.triggered.connect(self.reset_view)
        window_menu.addAction(reset_view_action)
        fit_window_action = QAction("Fit to Window", self)
        fit_window_action.triggered.connect(self.fit_to_window)
        window_menu.addAction(fit_window_action)
        window_menu.addSeparator()
        toggle_left_action = QAction("Toggle Left Panel", self)
        toggle_left_action.triggered.connect(self.toggle_left_panel)
        window_menu.addAction(toggle_left_action)
        toggle_right_action = QAction("Toggle Right Panel", self)
        toggle_right_action.triggered.connect(self.toggle_right_panel)
        window_menu.addAction(toggle_right_action)
        
        float_right_action = QAction("Float Right Panel", self, checkable=True)
        float_right_action.triggered.connect(lambda checked: self.toggle_right_panel_float(checked))
        window_menu.addAction(float_right_action)

        float_center_action = QAction("Float CenterView", self, checkable=True)
        float_center_action.triggered.connect(lambda checked: self.toggle_centerview_float(checked))
        window_menu.addAction(float_center_action)
        window_menu.addSeparator()

        left_panel_menu = QMenu("Left Panel", self)
        always_visible_action = QAction("Always Visible", self, checkable=True)
        autohide_fs_action = QAction("Hide when fullscreen only", self, checkable=True)
        float_panel_action = QAction("Float Panel", self, checkable=True)
        
        left_panel_menu.addAction(always_visible_action)
        left_panel_menu.addAction(autohide_fs_action)
        left_panel_menu.addAction(float_panel_action)
        window_menu.addMenu(left_panel_menu)

        def set_left_panel_mode(mode):
            self.settings.set("left_panel_mode", mode)
            self.left_panel_auto_hide = (mode == "autohide")
            if mode == "float":
                self.toggle_left_panel_float(True)
            elif hasattr(self, 'left_panel_float_window') and self.left_panel_float_window:
                self.toggle_left_panel_float(False)
            
            if mode != "float":
                self._apply_fullscreen_left_panel_visibility()

        always_visible_action.triggered.connect(lambda: set_left_panel_mode("permanent"))
        autohide_fs_action.triggered.connect(lambda: set_left_panel_mode("autohide"))
        float_panel_action.triggered.connect(lambda checked: set_left_panel_mode("float" if checked else "permanent"))
        mode = self.settings.get("left_panel_mode", "permanent")
        set_left_panel_mode(mode)

        window_menu.addSeparator()
        always_on_top_action = QAction("Always on Top", self, checkable=True)
        always_on_top_action.triggered.connect(self.toggle_always_on_top)
        window_menu.addAction(always_on_top_action)
        fullscreen_action = QAction("Full Screen", self)
        fullscreen_action.triggered.connect(self.toggle_fullscreen)
        window_menu.addAction(fullscreen_action)

    def _apply_fullscreen_left_panel_visibility(self):
        if not self.left_panel_auto_hide:
            self.left_panel.setVisible(True)
            if self._left_reveal_button:
                self._left_reveal_button.setVisible(False)
            return
        self.left_panel.setVisible(False)
        if not self._left_reveal_button:
            self._left_reveal_button = QPushButton(self)
            self._left_reveal_button.setIcon(self._std_icon(QStyle.StandardPixmap.SP_ArrowLeft, 12))
            self._left_reveal_button.setIconSize(QSize(12, 20))
            self._left_reveal_button.setFixedSize(24, 60)
            self._left_reveal_button.setStyleSheet("""
                QPushButton {
                    background: #2A2A2A;
                    color: #CCC;
                    border-top-right-radius: 4px;
                    border-bottom-right-radius: 4px;
                    border: 1px solid #444;
                    font-size: 14px;
                }
                QPushButton:hover {
                    background: #3A3A3A;
                }
            """)
            self._left_reveal_button.move(0, self.height()//2 - 30)
            self._left_reveal_button.show()
            self._left_reveal_button.enterEvent = lambda e: self._temporarily_show_left_panel()
            self._left_reveal_button.leaveEvent = lambda e: self._hide_left_panel_after_delay()
        else:
            self._left_reveal_button.setVisible(True)
            self._left_reveal_button.raise_()

    def _temporarily_show_left_panel(self):
        if self.isFullScreen() and self.left_panel_auto_hide:
            self.left_panel.setVisible(True)
            if self._left_reveal_button:
                self._left_reveal_button.hide()
            QTimer.singleShot(2000, self._hide_left_panel_after_delay)

    def _hide_left_panel_after_delay(self):
        if self.isFullScreen() and self.left_panel_auto_hide and not self._left_reveal_button.underMouse():
            self.left_panel.setVisible(False)
            if self._left_reveal_button:
                self._left_reveal_button.show()

    def open_theme_window(self):
        self._settings_window = SettingsWindow(self, self.settings)
        self._settings_window.tabs.setCurrentIndex(2)
        self._settings_window.show()

    def _apply_custom_styles(self):
        t = getattr(self, '_theme', THEMES["Dark (Default)"])

        def gb_style(title_color, border_color, bg_color=None):
            bg = f"background: {bg_color};" if bg_color else ""
            return f"""
                QGroupBox {{ color: {title_color}; font-weight: bold;
                    border: 1px solid {border_color}; border-radius: 5px;
                    margin-top: 6px; padding-top: 8px; {bg} }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
            """

        if hasattr(self, '_data_group'):
            self._data_group.setStyleSheet(gb_style(t['accent'], t['border2']))
        if hasattr(self, '_dt_group'):
            self._dt_group.setStyleSheet(f"QGroupBox {{ color: {t['accent']}; font-weight:bold; border:1px solid {t['border2']}; }}")
        if hasattr(self, '_bands_group'):
            self._bands_group.setStyleSheet(gb_style("#FF9800", t['border2']))
        if hasattr(self, '_products_group'):
            self._products_group.setStyleSheet(gb_style("#8B5CF6", t['border2']))
        if hasattr(self, '_overlays_group'):
            self._overlays_group.setStyleSheet(gb_style(t['accent'], t['border2']))
        if hasattr(self, '_press_group'):
            self._press_group.setStyleSheet(gb_style(t['accent'], t['border2']))
        if hasattr(self, '_climate_weather_group'):
            self._climate_weather_group.setStyleSheet(gb_style("#4CAF50", t['border2']))
        if hasattr(self, '_band_info_scene'):
            gs = f"""
                QGroupBox {{ color: #4CAF50; font-weight: bold; font-size: 10pt;
                    border: 1px solid #355E35; border-radius: 5px;
                    margin-top: 6px; padding-top: 7px; background: {t['bg']}; }}
                QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; }}
            """
            self._band_info_scene.setStyleSheet(gs)

        if hasattr(self, 'load_bands_btn'):
            self.load_bands_btn.setStyleSheet(f"""
                QPushButton {{ background: {t['accent']}; color: white;
                    padding: 8px; border-radius: 3px; border: 1px solid {t['border2']};
                    font-weight: bold; }}
                QPushButton:hover {{ background: {t['accent2']}; }}
            """)

        if hasattr(self, '_product_filter_btns'):
            for label, fb in self._product_filter_btns.items():
                fb.setStyleSheet(f"""
                    QPushButton {{ background: {t['bg3']}; color: {t['fg3']};
                        border: 1px solid {t['border2']}; border-radius: 3px;
                        font-size: 10px; padding: 0 8px; }}
                    QPushButton:checked {{ background: #3D2B6E; color: #C9B8F8;
                        border: 1px solid #7C5CBF; }}
                    QPushButton:hover:!checked {{ background: {t['menusel']}; }}
                """)

        if hasattr(self, 'overlay_checkboxes'):
            cb_style = f"""
                QCheckBox {{ color: {t['fg2']}; padding: 5px; font-size: 10px;
                    background: {t['bg3']}; border-radius: 3px; }}
                QCheckBox::indicator {{ width: 14px; height: 14px; }}
                QCheckBox:hover:!disabled {{ background: {t['menusel']}; }}
            """
            for cb in self.overlay_checkboxes.values():
                cb.setStyleSheet(cb_style)

        if hasattr(self, 'band_checkboxes'):
            band_cb_style = f"""
                QCheckBox {{ color: {t['fg2']}; padding: 3px; font-size: 10px;
                    background: {t['bg3']}; border-radius: 3px; }}
                QCheckBox::indicator {{ width: 14px; height: 14px; }}
                QCheckBox:hover:!disabled {{ background: {t['menusel']}; }}
            """
            for cb in self.band_checkboxes.values():
                cb.setStyleSheet(band_cb_style)

        if hasattr(self, '_product_tile_widgets'):
            tile_style = f"""
                QPushButton {{ background: {t['bg3']}; border: 1px solid {t['border2']};
                    border-radius: 4px; }}
                QPushButton:disabled {{ background: {t['bg2']}; border: 1px solid {t['border3']}; }}
                QPushButton:hover:!checked {{ background: {t['menusel']}; border-color: {t['fg3']}; }}
                QPushButton:checked {{ background: #2A1F4A; border: 1px solid #8B5CF6; }}
            """
            for tile in self._product_tile_widgets.values():
                tile.setStyleSheet(tile_style)

        if hasattr(self, 'climate_widgets'):
            _cw_cb_style = f"""
                QCheckBox {{ color: {t['fg2']}; padding: 3px 5px; font-size: 10px;
                    background: {t['bg3']}; border-radius: 3px; }}
                QCheckBox::indicator {{ width: 14px; height: 14px; }}
                QCheckBox:hover:!disabled {{ background: {t['menusel']}; }}
            """
            _cw_combo_style = f"""
                QComboBox {{ background: {t['bg3']}; color: {t['fg2']};
                    border: 1px solid {t['border2']}; padding: 2px 4px; font-size: 10px; min-width: 70px; }}
                QComboBox::drop-down {{ border: none; }}
                QComboBox QAbstractItemView {{ background: {t['bg3']}; color: {t['fg2']};
                    selection-background-color: {t['menusel']}; }}
            """
            for code, wdata in self.climate_widgets.items():
                wdata["checkbox"].setStyleSheet(_cw_cb_style)
                wdata["combo"].setStyleSheet(_cw_combo_style)
            if hasattr(self, 'gtwo_cb'):
                self.gtwo_cb.setStyleSheet(_cw_cb_style)

        if hasattr(self, 'selected_bands_label'):
            self.selected_bands_label.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")

        if hasattr(self, 'scene_band_info_lbl'):
            self.scene_band_info_lbl.setStyleSheet(f"color: {t['fg']}; font-size: 10pt; padding: 3px 1px; line-height: 1.3;")

        if hasattr(self, '_left_panel_frame'):
            self._left_panel_frame.setStyleSheet(f"""
                QFrame {{ background: {t['bg2']}; border-right: 1px solid {t['border3']}; }}
            """)

        if hasattr(self, '_action_buttons'):
            btn_colors = ["#5D8AA8", "#4CAF50", "#9C6FD6", "#5D8AA8"]
            for btn, color in zip(self._action_buttons, btn_colors):
                btn.setStyleSheet(f"""
                    QPushButton {{ background: {t['bg3']}; border: none;
                        border-left: 3px solid {color}; border-radius: 4px; text-align: center; }}
                    QPushButton:hover {{ background: {t['menusel']}; }}
                    QPushButton:pressed {{ background: {t['bg2']}; }}
                """)

        if hasattr(self, '_symbol_labels'):
            btn_colors = ["#5D8AA8", "#4CAF50", "#9C6FD6", "#5D8AA8"]
            for lbl, color in zip(self._symbol_labels, btn_colors):
                lbl.setStyleSheet(f"color: {color}; font-size: 18px; font-weight: bold; background: transparent;")

        if hasattr(self, 'tracks_list'):
            self.tracks_list.setStyleSheet(f"QTreeWidget {{ background: {t['bg2']}; color: {t['fg2']}; border: 1px solid {t['border2']}; }} QTreeWidget::item {{ padding: 2px 0px; }}")
        if hasattr(self, '_climate_weather_group'):
            self._climate_weather_group.setStyleSheet(f"QGroupBox {{ color: #4CAF50; font-weight: bold; border: 1px solid {t['border2']}; border-radius: 5px; margin-top: 6px; padding-top: 8px; }} QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}")
        if hasattr(self, 'cw_status_label'):
            self.cw_status_label.setStyleSheet(f"color: {t['fg3']}; font-size: 9px;")
        if hasattr(self, 'fc_status_label'):
            self.fc_status_label.setStyleSheet(f"color: {t['fg3']}; font-size: 9px;")
        if hasattr(self, 'forecast_enable_cb'):
            self.forecast_enable_cb.setStyleSheet(f"QCheckBox {{ color: {t['fg2']}; font-size: 9pt; }}")
        for cb_name in ('forecast_show_trackline_cb', 'forecast_show_points_cb', 'nhc_show_cone_cb',
                        'nhc_show_wind_cb', 'nhc_show_besttrack_cb', 'jma_show_hist_cb',
                        'jma_show_circle_cb', 'jma_show_swa_cb', 'jtwc_show_cone_cb', 'jtwc_show_wind_cb'):
            cb = getattr(self, cb_name, None)
            if cb:
                cb.setStyleSheet(f"QCheckBox {{ color: {t['fg2']}; font-size: 9pt; }}")
        if hasattr(self, 'storm_combo'):
            self.storm_combo.setStyleSheet(f"color: {t['fg2']}; background: {t['bg3']}; border: 1px solid {t['border2']};")

        if hasattr(self, 'alerts_list'):
            self.alerts_list.setStyleSheet(f"QTreeWidget {{ background: {t['bg2']}; color: {t['fg2']}; border: 1px solid {t['border2']}; }} QTreeWidget::item {{ padding: 2px 0px; }}")
        if self.alert_controller:
            self.alert_controller._restyle_alert_items()
        if hasattr(self, 'alert_source_combo'):
            self.alert_source_combo.setStyleSheet(f"QComboBox {{ background: {t['bg3']}; color: {t['fg2']}; border: 1px solid {t['border2']}; padding: 2px 4px; font-size: 9px; min-width: 70px; }} QComboBox::drop-down {{ border: none; }}")
        if hasattr(self, 'alert_read_filter'):
            self.alert_read_filter.setStyleSheet(f"QComboBox {{ background: {t['bg3']}; color: {t['fg2']}; border: 1px solid {t['border2']}; }}")
        if hasattr(self, 'alert_sev_filter'):
            self.alert_sev_filter.setStyleSheet(f"QComboBox {{ background: {t['bg3']}; color: {t['fg2']}; border: 1px solid {t['border2']}; }}")

        self._restyle_pro_tab()

    def apply_theme(self):
        name = self.settings.get("theme_name", "Dark (Default)")
        t = THEMES.get(name, THEMES["Dark (Default)"])
        self._theme = t
        QApplication.instance().setStyleSheet(build_stylesheet(t))

        if hasattr(self, 'graphics_view') and self.graphics_view:
            vb = t.get("viewport_bg", self.settings.get("viewport_bg", "#000000"))
            self.graphics_view.setBackgroundBrush(QBrush(QColor(vb)))

        if hasattr(self, '_update_viewport_animation_bar_visibility'):
            self._update_viewport_animation_bar_visibility()

        self._apply_custom_styles()

    def open_settings_window(self):
        self._settings_window = SettingsWindow(self, self.settings)
        self._settings_window.show()

    def open_account_window(self):
        if hasattr(self, '_account_window') and self._account_window.isVisible():
            self._account_window.raise_()
            return
        self._account_window = AccountWindow(self, self.settings)
        self._account_window.show()

    def open_ascat_window(self):
        self.ascat_controller.open_ascat_window()

    def open_microwave_window(self):
        self.microwave_controller.open_microwave_window()

    def apply_gpu_acceleration(self):
        enabled = self.settings.get("gpu_acceleration", False)

        backend_ok = set_compute_backend(enabled)
        effective_enabled = enabled and backend_ok

        low_vram_detected = False
        total_mb = 0
        if HAS_CUPY and _GPU_FORCE_DISABLE is False:
            try:
                mem = cp.cuda.Device(0).mem_info
                total_mb = mem[1] / (1024**2)
                if total_mb < 1500:
                    low_vram_detected = True
                    target = self.settings.get("gpu_memory_target_mb", 512)
                    self.log(f"Low VRAM GPU detected (~{int(total_mb)}MB). Using conservative target: {target}MB")

                    if self.preview_max_px > 2200:
                        self.preview_max_px = 2200
                        self.log("Low-VRAM: capped preview_max_px to Balanced 2200px")

                    if self.settings.get("cache_size_mb", _default_cache_mb()) > 1024:
                        self.settings.set("cache_size_mb", 1024)
                        self.log("Low-VRAM: reduced cache_size_mb to 1024 for safety")
            except Exception:
                pass

        self.graphics_view.set_gpu_acceleration(effective_enabled)
        if enabled and not backend_ok:
            self.log("GPU acceleration requested but CUDA not available - using NumPy")
        self.log(f"GPU acceleration {'enabled' if effective_enabled else 'disabled'} (low-VRAM mode: {low_vram_detected})")
        return low_vram_detected

    def apply_render_quality(self):
        quality = self.settings.get("render_quality", "2km res")
        self.preview_quality = quality
        if quality == "gridded res":
            # Gridded = tile-based LOD: the source is always read at the band's
            # NATIVE resolution (never decimated). The tile layer materialises
            # only the visible tiles at the stride the current zoom needs, so
            # the source size is not capped by RAM or the viewport. The exact
            # per-band native dims are synced at display time so the quality
            # grid matches the source array 1:1 (no upscale).
            half = self.overlay_controller._compute_disk_half_extent()
            ref = self._ref_grid_0_5km or self._ref_grid_1km or self._ref_grid_size
            if ref:
                max_native_px = max(1024, int(max(ref)))
            else:
                # No reference grid known yet: budget for the 0.5 km full-disk
                # native size (slightly overshoots so no band gets decimated).
                max_native_px = max(1024, int(round(2 * half / 490)))
            self.preview_max_px = max(512, max_native_px)
            self.preview_res_m = max(500, int(round(2 * half / self.preview_max_px)))
        elif quality == "5km res":
            self.preview_res_m = 5000
            half = self.overlay_controller._compute_disk_half_extent()
            self.preview_max_px = max(1, int(round(2 * half / 5000)))
        else:
            res_map = {
                "2km res":  2000,
                "1km res":  1000,
                "0.5km res": 500,
                "Full Res": 0,
            }
            self.preview_res_m = res_map.get(quality, 2000)
            if self.preview_res_m > 0:
                half = self.overlay_controller._compute_disk_half_extent()
                self.preview_max_px = max(1, int(round(2 * half / self.preview_res_m)))
            else:
                self.preview_max_px = 0
        _ram_max = 8192
        if HAS_PSUTIL:
            mem = psutil.virtual_memory()
            avail_gb = mem.available / (1024**3)
            _ram_max = max(2048, int(((avail_gb * 0.6 * 1024**3) / (16 * 4)) ** 0.5))
        if self.preview_max_px > 0 and quality != "gridded res":
            self.preview_max_px = min(self.preview_max_px, _ram_max)

        # Select resolution-appropriate ref_grid
        _grid_map = {
            "Full Res":   self._ref_grid_0_5km or self._ref_grid_1km or self._ref_grid_size,
            "0.5km res":  self._ref_grid_0_5km or self._ref_grid_1km or self._ref_grid_size,
            "1km res":    self._ref_grid_1km or self._ref_grid_size,
            "2km res":    self._ref_grid_size,
            "5km res":    self._ref_grid_size,
            "gridded res": self._ref_grid_size,
        }
        self._ref_grid_size = _grid_map.get(quality, self._ref_grid_size)

        self._last_overlay_key = None
        self._last_grid_cache_key = None
        self._last_coast_cache_key = None
        self._grid_path_cache.clear()
        self._coast_path_cache.clear()
        self._display_image_cache.clear() if hasattr(self, '_display_image_cache') else None
        if hasattr(self, 'cache') and self.cache is not None:
            self.cache.clear_raw()
            self.cache.clear_precached()
            self.cache.clear_rgb()
        self.settings.set("preview_max_px", self.preview_max_px)
        gc = getattr(self, '_goes_cache', None)
        if gc is not None and hasattr(gc, 'loader') and hasattr(gc.loader, 'max_px'):
            gc.loader.max_px = self.preview_max_px
        self.log(f"Preview quality: {quality} -- max_px={self.preview_max_px}, res_m={self.preview_res_m}")

    def enforce_cache_size_limit(self):
        if hasattr(self, 'cache') and self.cache is not None:
            self.cache.enforce_size_limit()

    def apply_zoom_interpolation(self):
        interp = self.settings.get("zoom_interpolation", "Bilinear")
        if interp == "Nearest":
            self.graphics_view.setRenderHint(QPainter.SmoothPixmapTransform, False)
            self.graphics_view.setRenderHint(QPainter.Antialiasing, False)
        elif interp == "Bicubic":
            self.graphics_view.setRenderHint(QPainter.SmoothPixmapTransform, True)
            self.graphics_view.setRenderHint(QPainter.Antialiasing, True)
        else:
            self.graphics_view.setRenderHint(QPainter.SmoothPixmapTransform, True)
            self.graphics_view.setRenderHint(QPainter.Antialiasing, False)
        self.log(f"Zoom interpolation set to {interp}")

    def apply_texture_cache_size(self):
        tex_mb = max(64, min(4096, self.settings.get("texture_cache_size_mb", 256)))
        QImageReader.setAllocationLimit(tex_mb)
        self.log(f"Texture cache size set to {tex_mb}MB")

    PROJECTION_CONFIGS = {
        "plate_carree": {
            "proj_params": {"proj": "longlat", "ellps": "WGS84"},
            "radius_of_influence": 0.1,
            "margin_factor": 0.02,
            "fallback_extent": (-180, -90, 180, 90),
            "label": "Plate Carree"
        },
        "equirectangular": {
            "proj_params": {"proj": "eqc", "ellps": "WGS84", "datum": "WGS84"},
            "radius_of_influence": 50000,
            "margin_factor": 0.0,
            "fallback_extent": (-180, -90, 180, 90),
            "label": "Equirectangular"
        }
    }

    # ------------------------------------------------------------------ #
    # GPU (GLMapWidget) projection engine
    # ------------------------------------------------------------------ #

    def _resolve_render_engine(self):
        """Return 'gpu' or 'cpu' for the current user setting + capability.

        'auto' → use GPU only if a hardware OpenGL renderer is detected.
        'gpu'  → require hardware GL, else fall back to CPU.
        'cpu'  → always CPU.
        """
        pref = self.settings.get("use_gpu_rendering", "auto")
        if pref == "cpu":
            return "cpu"
        ok = getattr(self, "_gl_detected", None)
        if ok is None:
            from src.ui.GLMapWidget import detect_gl_renderer
            try:
                ok, _name = detect_gl_renderer()
            except Exception:
                ok = False
            self._gl_detected = ok
        if ok:
            return "gpu"
        return "cpu"

    def _gl_mode_active(self):
        w = getattr(self, "gl_map_widget", None)
        if w is None:
            return False
        return self.viewport_stack.currentWidget() is w

    def _gl_eff_geotransform(self, img_w, img_h):
        """Scale the native GEOS geotransform to a (possibly decimated) QImage."""
        gt = self._full_disk_geotransform
        if gt is None:
            return None
        native = getattr(self, "_ref_grid_size", None) or (
            getattr(self, "_full_disk_ref_w", None),
            getattr(self, "_full_disk_ref_h", None),
        )
        if native and native[0] and native[1] and img_w > 0 and img_h > 0:
            sx = native[0] / img_w
            sy = native[1] / img_h
        else:
            sx = sy = 1.0
        try:
            return Affine(gt.a * sx, gt.b, gt.c, gt.d, gt.e * sy, gt.f)
        except Exception:
            return gt

    def _apply_visualizer_gl(self, target_proj: str):
        """Show the reprojected imagery through the GPU widget (instant)."""
        mode_map = {
            "equirectangular": "equirectangular",
            "plate_carree": "plate_carree",
            "full_disk": "full_disk",
        }
        mode = mode_map.get(target_proj, target_proj)

        src_img = getattr(self, "_full_disk_scene_image", None)
        if src_img is None or src_img.isNull():
            src_img = getattr(self, "_original_scene_image", None)
        if src_img is None or src_img.isNull():
            self.log("[VISUALIZER] No full-disk image available for GPU projection")
            return False

        crs = getattr(self, "_full_disk_crs", None) or getattr(self, "current_crs", None)
        if crs is None:
            self.log("[VISUALIZER] No source CRS available for GPU projection")
            return False

        try:
            geos_params = glsl_proj.crs_to_geos_params(crs)
        except Exception as exc:
            self.log(f"[VISUALIZER] CRS not a GEOS projection, using CPU path: {exc}")
            return False

        gt = self._gl_eff_geotransform(src_img.width(), src_img.height())
        if gt is None:
            self.log("[VISUALIZER] No source geotransform available for GPU projection")
            return False

        w = self.gl_map_widget
        w.setVisible(True)
        w.set_source(gt, geos_params, (src_img.height(), src_img.width()))
        w.set_image(src_img)
        w.set_projection(mode)
        self.viewport_stack.setCurrentWidget(w)
        self.log(f"[VISUALIZER] GPU projection active: {mode} ({src_img.width()}x{src_img.height()} texture)")

        # Keep downstream CRS/geotransform bookkeeping consistent with the
        # projection being displayed (overlay / coordinate readouts). Reuses
        # the exact same bookkeeping as the CPU path so grid/coast overlays
        # and coordinate readouts stay coherent across engines.
        self._current_visualizer_mode = {
            "equirectangular": "Equirectangular",
            "plate_carree": "Plate Carree",
            "full_disk": "Full Disk",
        }.get(mode, mode)
        self._current_display_projection = mode
        self._visualizer_reprojecting = False
        self._set_display_extent(mode, crs, gt, src_img.width(), src_img.height())
        return True

    def _gl_refresh_image(self):
        """Re-upload the current full-disk image into the GL texture."""
        if not self._gl_mode_active():
            return
        src_img = getattr(self, "_full_disk_scene_image", None)
        if src_img is None or src_img.isNull():
            return
        w = self.gl_map_widget
        gt = self._gl_eff_geotransform(src_img.width(), src_img.height())
        if gt is not None:
            w.set_source(gt, self.gl_map_widget.source_params or {}, (src_img.height(), src_img.width()))
        w.set_image(src_img)
        w.update()

    def apply_visualizer(self):
        visualizer = self.settings.get("visualizer", "Full Disk")
        self.log(f"Visualizer set to {visualizer}")
        if visualizer == "Full Disk":
            was_full_disk = (self._current_display_projection == "full_disk")
            
            if not was_full_disk:
                self.log(f"[VISUALIZER] Switching back to Full Disk - clearing projection")
                self._current_display_projection = "full_disk"
                self._display_projection_extent = None
                self._display_projection_crs = None
                
                # Restore original CRS and geotransform from the full-disk image
                if hasattr(self, '_full_disk_crs') and self._full_disk_crs is not None:
                    self.current_crs = self._full_disk_crs
                    self.current_geotransform = self._full_disk_geotransform
                    self.log(f"[VISUALIZER] Restored original CRS/geotransform")
                
                # Invalidate caches
                self._grid_path_cache = {}
                self._coast_path_cache = {}
                self._grid_image_cache = {}
                self._coast_image_cache = {}
                self._last_grid_cache_key = None
                self._last_coast_cache_key = None
            
            orig = getattr(self, '_full_disk_scene_image', None) or getattr(self, '_original_scene_image', None)
            if orig is not None and not orig.isNull():
                self.graphics_view.set_image(QPixmap.fromImage(orig), preserve_view=was_full_disk)
                self.log("[VISUALIZER] Restored original image")
            # Leave GPU projection page
            if getattr(self, "_gl_mode_active", lambda: False)():
                self.viewport_stack.setCurrentWidget(self.graphics_view)
                self.gl_map_widget.setVisible(False)
                self.log("[VISUALIZER] Left GPU projection page, restored graphics view")
            self._current_visualizer_mode = "Full Disk"
            self._visualizer_reprojecting = False
            
            if self.grid_enabled or self.coast_enabled:
                self.log(f"[VISUALIZER] Re-rendering grid/coast for Full Disk")
                self._last_overlay_key = None
                self.overlay_controller.update_overlays()
            return
        cur_mode = getattr(self, '_current_visualizer_mode', None)
        if cur_mode == visualizer and getattr(self, '_visualizer_reprojecting', False):
            return
        self._visualizer_reprojecting = False
        target_proj = "equirectangular" if visualizer == "Equirectangular" else "plate_carree"

        # GPU path: instant, no resample. Uses the native full-disk source.
        if self._resolve_render_engine() == "gpu":
            if self._apply_visualizer_gl(target_proj):
                self.log(f"[VISUALIZER] Instantly switched to {visualizer} via OpenGL")
                return
            self.log("[VISUALIZER] GPU path unavailable, falling back to CPU WarpedVRT")
        self._reproject_current_image(target_proj)

    def _reproject_current_image(self, target_proj: str):
        if getattr(self, '_visualizer_reprojecting', False):
            return
        self._visualizer_reprojecting = True
        _repro_success = False
        try:
            base = getattr(self, 'current_base', None)
            persistent_cache = eq_cache if target_proj == "equirectangular" else fc_cache

            # Check persistent cache first (survives band switches)
            if base:
                cached_qimage = persistent_cache.get_qimage(base)
                if cached_qimage is not None and not cached_qimage.isNull():
                    self.log(f"[VISUALIZER] Persistent cache HIT for {target_proj} band={base}")
                    out_img = cached_qimage
                    out_w, out_h = out_img.width(), out_img.height()
                    crs_src = getattr(self, 'current_crs', None)
                    gt = getattr(self, 'current_geotransform', None)
                    self.log(f"[VISUALIZER] Using cached reprojected image: {out_w}x{out_h}")
                    out_pix = QPixmap.fromImage(out_img)
                    # Preserve full-disk original before overwriting scene
                    if not getattr(self, '_full_disk_scene_image', None) or self._full_disk_scene_image.isNull():
                        scene = self.graphics_view.scene()
                        if scene:
                            for item in scene.items():
                                if isinstance(item, QGraphicsPixmapItem):
                                    pm = item.pixmap()
                                    if pm and not pm.isNull():
                                        self._full_disk_scene_image = pm.toImage()
                                        break
                    self.graphics_view.set_image(out_pix, preserve_view=False)
                    self._current_visualizer_mode = "Equirectangular" if target_proj == "equirectangular" else "Plate Carree"
                    self._current_display_projection = target_proj
                    self._set_display_extent(target_proj, crs_src, gt, out_w, out_h)
                    
                    # Update CRS and geotransform to reflect the reprojected image
                    if target_proj == "equirectangular":
                        self.current_crs = CRS.from_epsg(4326)
                        extent = self._display_projection_extent
                        if extent:
                            lon_min, lat_min, lon_max, lat_max = extent
                            res_x = (lon_max - lon_min) / out_w
                            res_y = (lat_max - lat_min) / out_h
                            self.current_geotransform = Affine(res_x, 0, lon_min, 0, -res_y, lat_max)
                    elif target_proj == "plate_carree":
                        self.current_crs = CRS.from_epsg(4326)
                        extent = self._display_projection_extent
                        if extent:
                            lon_min, lat_min, lon_max, lat_max = extent
                            res_x = (lon_max - lon_min) / out_w
                            res_y = (lat_max - lat_min) / out_h
                            self.current_geotransform = Affine(res_x, 0, lon_min, 0, -res_y, lat_max)
                    
                    self.log(f"[VISUALIZER] Applied cached {target_proj} successfully")
                    _repro_success = True
                    return

            scene = self.graphics_view.scene()
            if scene is None:
                self.log(f"[VISUALIZER] Scene is None, aborting reprojection")
                return
            main_item = None
            max_area = 0
            min_z = float('inf')
            for item in scene.items():
                if isinstance(item, QGraphicsPixmapItem):
                    pm = item.pixmap()
                    if pm and not pm.isNull():
                        area = pm.width() * pm.height()
                        zv = item.zValue()
                        if area > max_area or (area == max_area and zv < min_z):
                            max_area = area
                            min_z = zv
                            main_item = item
            if main_item is None:
                self.log(f"[VISUALIZER] No pixmap item found in scene")
                return
            main_pix = main_item.pixmap()
            self.log(f"[VISUALIZER] Found scene item: zValue={main_item.zValue()}, size={main_pix.width()}x{main_pix.height()}")
            if main_pix.isNull():
                self.log(f"[VISUALIZER] Main pixmap is null")
                return
            
            self.log(f"[VISUALIZER] Found source image: {main_pix.width()}x{main_pix.height()}")
            src_img = main_pix.toImage()
            out_w, out_h = src_img.width(), src_img.height()
            if not getattr(self, '_full_disk_scene_image', None) or self._full_disk_scene_image.isNull():
                self._full_disk_scene_image = src_img.copy()
                self.log(f"[VISUALIZER] Saved full-disk original scene image")

            crs_src = getattr(self, 'current_crs', None)
            gt = getattr(self, 'current_geotransform', None)
            if crs_src is None or gt is None:
                self.log(f"[VISUALIZER] CRS or geotransform missing, deferring")
                self._visualizer_deferred = target_proj
                return
            
            self.log(f"[VISUALIZER] CRS: {crs_src.to_proj4() if crs_src else 'None'}")
            self.log(f"[VISUALIZER] Geotransform: {gt}")

            # Check temp cache (fast re-entry for same band)
            cache_key = (base or '', target_proj, src_img.width(), src_img.height())
            cached = getattr(self, '_reproject_cache', {}).get(cache_key)
            if cached is not None:
                out_img = cached
                self.log(f"[VISUALIZER] Temp cache HIT for {target_proj}")
            else:
                self.log(f"[VISUALIZER] Cache MISS, computing {target_proj}...")
                out_img = None
                try:
                    from src.services.warped_vrt import reproject_warpedvrt_qimage
                    out_img = reproject_warpedvrt_qimage(src_img, crs_src, gt, target_proj)
                    self.log("[VISUALIZER] WarpedVRT (rasterio) resample complete")
                except Exception as wv_exc:
                    self.log(f"[VISUALIZER] WarpedVRT failed ({wv_exc}), using pyresample fallback")
                    out_img = self.projection._reproject_to(target_proj, src_img, crs_src, gt)
                if out_img is not None:
                    if not hasattr(self, '_reproject_cache'):
                        self._reproject_cache = {}
                    self._reproject_cache[cache_key] = out_img
                    if base:
                        persistent_cache.put_qimage(base, out_img)
                    self.log(f"[VISUALIZER] Cached result: {out_img.width()}x{out_img.height()} (persistent too)")

            if out_img is not None and not out_img.isNull():
                self.log(f"[VISUALIZER] Setting reprojected image: {out_img.width()}x{out_img.height()}")
                # Preserve full-disk original before overwriting scene
                if not getattr(self, '_full_disk_scene_image', None) or self._full_disk_scene_image.isNull():
                    for item in (scene.items() if scene else []):
                        if isinstance(item, QGraphicsPixmapItem):
                            pm = item.pixmap()
                            if pm and not pm.isNull():
                                self._full_disk_scene_image = pm.toImage()
                                break
                out_pix = QPixmap.fromImage(out_img)
                self.graphics_view.set_image(out_pix, preserve_view=False)
                self._current_visualizer_mode = "Equirectangular" if target_proj == "equirectangular" else "Plate Carree"
                self._set_display_extent(target_proj, crs_src, gt, out_w, out_h)
                
                # Update CRS and geotransform to reflect the reprojected image
                if target_proj == "equirectangular":
                    self.current_crs = CRS.from_epsg(4326)
                    # Create geotransform for equirectangular projection
                    extent = self._display_projection_extent
                    if extent:
                        lon_min, lat_min, lon_max, lat_max = extent
                        res_x = (lon_max - lon_min) / out_w
                        res_y = (lat_max - lat_min) / out_h
                        # Top-left corner is (lon_min, lat_max) in Plate Carree
                        self.current_geotransform = Affine(res_x, 0, lon_min, 0, -res_y, lat_max)
                elif target_proj == "plate_carree":
                    self.current_crs = CRS.from_epsg(4326)
                    extent = self._display_projection_extent
                    if extent:
                        lon_min, lat_min, lon_max, lat_max = extent
                        res_x = (lon_max - lon_min) / out_w
                        res_y = (lat_max - lat_min) / out_h
                        self.current_geotransform = Affine(res_x, 0, lon_min, 0, -res_y, lat_max)
                
                self.log(f"[VISUALIZER] Applied {target_proj} successfully")
                _repro_success = True
            else:
                self.log(f"[VISUALIZER] Output image is null or None!")
        finally:
            self._visualizer_reprojecting = False
            if not _repro_success:
                self.log(f"[VISUALIZER] Reprojection failed or incomplete")

    def _set_display_extent(self, target_proj, crs_src, gt, out_w, out_h):
        self._current_display_projection = target_proj
        if target_proj == "equirectangular":
            sat_lon = 140.7
            try:
                if crs_src is not None:
                    crs_dict = crs_src.to_dict()
                    sat_lon = float(crs_dict.get('lon_0', sat_lon))
            except Exception:
                pass
            crop_deg = 85
            lon_min = sat_lon - crop_deg
            lon_max = sat_lon + crop_deg
            lat_min = -crop_deg
            lat_max = crop_deg
            self._display_projection_extent = (lon_min, lat_min, lon_max, lat_max)
            self.log(f"[VISUALIZER] Display extent (Equirectangular): lon=[{lon_min:.1f}, {lon_max:.1f}], lat=[{lat_min:.1f}, {lat_max:.1f}], centered on sat_lon={sat_lon}")
            self._display_projection_crs = CRS.from_epsg(4326)
        else:
            if crs_src is not None and gt is not None:
                try:
                    from pyproj import Transformer
                    geo_to_latlon = Transformer.from_crs(crs_src, "EPSG:4326", always_xy=True)
                    src_corners_geo = [
                        (gt[2], gt[5]),
                        (gt[2] + gt[0] * out_w, gt[5]),
                        (gt[2], gt[5] + gt[4] * out_h),
                        (gt[2] + gt[0] * out_w, gt[5] + gt[4] * out_h),
                    ]
                    src_corners_latlon = []
                    for gx, gy in src_corners_geo:
                        try:
                            lon, lat = geo_to_latlon.transform(gx, gy)
                            if np.isfinite(lon) and np.isfinite(lat):
                                src_corners_latlon.append((lon, lat))
                        except Exception:
                            pass
                    if len(src_corners_latlon) >= 2:
                        lons = [c[0] for c in src_corners_latlon]
                        lats = [c[1] for c in src_corners_latlon]
                        lon_min, lon_max = min(lons), max(lons)
                        lat_min, lat_max = min(lats), max(lats)
                        self._display_projection_extent = (lon_min, lat_min, lon_max, lat_max)
                        self.log(f"[VISUALIZER] Display extent (Plate Carree): lon=[{lon_min:.1f}, {lon_max:.1f}], lat=[{lat_min:.1f}, {lat_max:.1f}]")
                    else:
                        self._display_projection_extent = (-180, -90, 180, 90)
                        self.log(f"[VISUALIZER] Display extent (Plate Carree - fallback): full world")
                except Exception:
                    self._display_projection_extent = (-180, -90, 180, 90)
                    self.log(f"[VISUALIZER] Display extent (Plate Carree - fallback): full world")
            else:
                self._display_projection_extent = (-180, -90, 180, 90)
                self.log(f"[VISUALIZER] Display extent (Plate Carree - fallback): full world")
            self._display_projection_crs = CRS.from_epsg(4326)
        # Invalidate grid/coast caches
        self._grid_path_cache = {}
        self._coast_path_cache = {}
        self._grid_image_cache = {}
        self._coast_image_cache = {}
        self._last_grid_cache_key = None
        self._last_coast_cache_key = None
        if self.grid_enabled or self.coast_enabled:
            self.log(f"[VISUALIZER] Re-rendering grid/coast for {target_proj}")
            self.overlay_controller.update_overlays()
        else:
            self.overlay_controller._update_aor_overlays()

    def _get_source_boundary_points(self, *args, **kwargs):
        return self.projection._get_source_boundary_points(*args, **kwargs)
    def _reproject_to(self, *args, **kwargs):
        return self.projection._reproject_to(*args, **kwargs)
    def _reproject_simple(self, *args, **kwargs):
        return self.projection._reproject_simple(*args, **kwargs)
    def _resample_with_pyresample(self, *args, **kwargs):
        return self.projection._resample_with_pyresample(*args, **kwargs)
    def log(self, msg):
        logging.getLogger(__name__).info(msg)

def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    try:
        import faulthandler
        faulthandler.enable()
    except Exception:
        pass
    app = QApplication(sys.argv)
    _icon_path = top_dir / 'public' / 'images' / 'Monwatch-LOGO.png'
    if _icon_path.exists():
        app.setWindowIcon(QIcon(str(_icon_path)))
    app.setStyleSheet('''
        /* Never include bare QWidget in universal rules -- causes hard crashes on Windows */
        QMainWindow { background: #1E1E1E; color: #DDD; }
        QMenuBar {
            background: #252525;
            color: #CCC;
            border-bottom: 1px solid #333;
            padding: 2px 0;
            font-size: 10px;
        }
        QMenuBar::item { padding: 4px 10px; background: transparent; }
        QMenuBar::item:selected { background: #333; color: #FFF; }
        QMenu {
            background: #252525;
            color: #CCC;
            border: 1px solid #444;
        }
        QMenu::item { padding: 5px 20px 5px 10px; }
        QMenu::item:selected { background: #333; color: #FFF; }
        QSplitter::handle { background: #333; }
        QGroupBox {
            border: 1px solid #383838;
            border-radius: 4px;
            margin-top: 1ex;
            font-weight: bold;
            font-size: 10px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: #AAA;
        }
        QTabWidget::pane { border-top: 1px solid #333; }
        QTabBar::tab {
            background: #252525;
            color: #888;
            border: 1px solid #333;
            padding: 4px 8px;
            font-size: 10px;
        }
        QTabBar::tab:selected { background: #2E2E2E; color: #DDD; border-bottom: none; }
        QSlider::groove:horizontal {
            height: 4px; background: #333; border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #5D8AA8; width: 14px; margin: -5px 0; border-radius: 7px;
        }
        QScrollBar:vertical {
            background: #1E1E1E; width: 8px; margin: 0;
        }
        QScrollBar::handle:vertical {
            background: #444; border-radius: 4px; min-height: 20px;
        }
        QScrollBar::handle:vertical:hover { background: #555; }
        QStatusBar { background: #252525; color: #888; font-size: 9px; border-top: 1px solid #333; }
        QProgressBar { border: 1px solid #444; border-radius: 3px; text-align: center; background: #2A2A2A; }
        QProgressBar::chunk { background: #5D8AA8; border-radius: 2px; }
        QComboBox {
            border: 1px solid #444; border-radius: 3px; padding: 3px 6px;
            background: #2A2A2A; color: #DDD; min-width: 6em;
        }
        QComboBox::drop-down { border: none; width: 18px; }
        QComboBox::down-arrow {
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid #888;
        }
        QComboBox QAbstractItemView {
            background: #2A2A2A; color: #DDD;
            border: 1px solid #444; selection-background-color: #3A3A3A;
        }
        QCheckBox { spacing: 6px; color: #CCC; font-size: 10px; }
        QCheckBox::indicator { width: 14px; height: 14px; border-radius: 2px; }
        QCheckBox::indicator:unchecked { border: 1px solid #555; background: #2A2A2A; }
        QCheckBox::indicator:checked { border: 1px solid #5D8AA8; background: #5D8AA8; }
        QRadioButton { spacing: 6px; color: #CCC; font-size: 10px; }
        QRadioButton::indicator { width: 14px; height: 14px; border-radius: 7px; }
        QRadioButton::indicator:unchecked { border: 1px solid #555; background: #2A2A2A; }
        QRadioButton::indicator:checked { border: 1px solid #5D8AA8; background: #5D8AA8; }
        QLabel { color: #CCC; }
        QTextEdit { background: #181818; color: #CCC; border: 1px solid #333; }
        QProgressDialog { background: #252525; color: #DDD; border: 1px solid #444; }
    ''')
    try:
        window = MainUI()
        window.apply_theme()
        window.show()
        try:
            from src.services import thread_guard
            thread_guard.shutdown_threads()
        except Exception:
            pass
        sys.exit(app.exec())
    except Exception as e:
        print(f"[FATAL] MainUI failed to initialize or run: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        try:
            from src.services import thread_guard
            thread_guard.shutdown_threads()
        except Exception:
            pass
        sys.exit(1)

if __name__ == '__main__':
    main()