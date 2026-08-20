# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: ui/settings.py
# Description: Settings management system including user preferences, theme configuration, and application state persistence.
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
import threading
import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
     QComboBox, QCheckBox, QRadioButton, QGroupBox, QGridLayout, QPushButton,
     QDialog, QDialogButtonBox, QScrollArea, QMessageBox, QTabWidget,
     QFileDialog, QKeySequenceEdit, QFormLayout
)

from ..core.helpers import THEMES
from .components import ColorButton

log = logging.getLogger(__name__)

# Ordered (display label, internal value) pairs for the Preview Quality
# dropdown. Internal values stay stable so existing saved settings and the
# render pipeline keep working; only the on-screen order/labels change.
QUALITY_OPTIONS = [
    ("gridded res", "gridded res"),
    ("5km res", "5km res"),
    ("2km res", "2km res"),
    ("1km res", "1km res"),
    ("0.5km res", "0.5km res"),
    ("Full Resolution", "Full Res"),
]
QUALITY_LABELS = [label for label, _ in QUALITY_OPTIONS]
QUALITY_VALUES = [value for _, value in QUALITY_OPTIONS]


class SettingsManager:
    def __init__(self, src_dir):
        if getattr(sys, 'frozen', False):
            self.settings_file = Path(sys.executable).resolve().parent / "config" / "settings.json"
        else:
            self.settings_file = Path(src_dir).parent / "config" / "settings.json"
        self.accounts_file = self.settings_file.with_name("accounts.json")
        self._dirty = False
        self._save_timer = None
        self._debounce_ms = 500
        self.settings = {
            "mode": "casual",
            "theme": "dark",
            "window": {
                "geometry": None,
                "state": None
            },
            "system": {
                "gpu_acceleration": False,
                "use_gpu_rendering": "auto",
                "max_threads": 4,
                "cache_size_mb": 2048,
                "render_quality": "2km res",
                "texture_cache_size_mb": 256,
                "adaptive_quality": True,
                "animation_frame_skip": 0,
                "async_overlay_rendering": True,
                "lod_bias": 0.0,
                "overlay_update_throttle_ms": 50,
                "numba_jit": True,
                "coastline_resolution": "Full"
            },
            "bg_compositing": True,
            "precache_bands": False,
            "lazy_nc": True,
            "fullres_warning_skip": False,
            "auto_fullres_prompt": True,
            "animation_fps": 5,
            "animation_controls_on_viewport": False,
            "old_tracks": "Archive",
            "footer_size": "small",
            "text_size": "normal",
            "footer_show": {"satellite": True, "datetime": True, "band": True, "latlon": True},
            "footer_position": "bottom",
            "footer_info_position": "left",
            "left_panel_mode": "permanent",
            "info_box_position": "top_right",
            "track_info_position": "top_right",
            "info_box_name": "PAGASA",
            "info_box_show_minmax": True,
            "info_box_show_cursor_temp": True,
            "info_box_enabled": False,
            "track_bulletin_position": "top_right",
            "track_bulletin_visible": True,
            "track_bulletin_x": None,
            "track_bulletin_y": None,
            "startup_mode": "logo",
            "viewport_mode": "beta",
            "visualizer": "Full Disk",
            "geotarget_style": "border",
            "geotarget_border_color": "#00E5FF",
            "geotarget_sectors": "Both",
            "grid_enabled": False,
            "coast_enabled": False,
            "winds_enabled": False,
            "pro_contours": False,
            "viewport_bg": "#000000",
            "viewport_bg_use_theme_default": False,
            "cartopy_grid_in_exports": False,
            "grid_pattern": "solid",
            "par_enabled": False,
            "aor_expanded": False,
            "aor_highlight": True,
            "jma_enabled": False,
            "tcad_enabled": False,
            "tcid_enabled": False,
            "fir_enabled": False,
            "par_color": "#00FF9F",
            "jma_color": "#FFAA00",
            "tcad_color": "#FF6B6B",
            "tcid_color": "#4ECDC4",
            "fir_color": "#FFE66D",
            "custom_aor_color": "#00E5FF",
            "pro_show_detailed_stats": False,
            "wind_density": "Normal",
            "gridded_winds": False,
            "windy_api_key": "",
            "metra_api_key": "",
            "cwa_api_key": "",
            "high_res_amv": False,
            "forecast_preferences": {
                "show_date_time": True,
                "time_format": "military",
                "utc_offset": 0,
                "show_wind_speed": True,
                "wind_format": "kt",
                "forecast_layout": "monwatch",
                "legend_position": "bottom_left"
            },
            "climate_overlay_colors": {
                "TC": "#FF6B6B",
                "WET": "#4ECDC4",
                "DRY": "#FFE66D",
                "WARM": "#FF8C42",
                "COLD": "#74B9FF"
            },
            "climate_tc_enabled": False,
            "climate_tc_week": "Week-2",
            "climate_wet_enabled": False,
            "climate_wet_week": "Week-2",
            "climate_dry_enabled": False,
            "climate_dry_week": "Week-2",
            "climate_warm_enabled": False,
            "climate_warm_week": "Week-2",
            "climate_cold_enabled": False,
            "climate_cold_week": "Week-2",
            "paths": {
                "download_folder": "",
                "export_folder": "",
                "plugins_folder": ""
            },
            "alert_enabled": True,
            "alert_poll_interval_min": 10,
            "alert_marine_only": False,
            "alert_zone": "",
            "alert_sound_modern_notif": False,
            "alert_sound_soft_emer": False,
            "alert_retention_days": 7,
            "active_tab": 0,
            "shortcuts": {
                "tab_next": "Ctrl+Tab",
                "tab_prev": "Ctrl+Shift+Tab",
                "tab_jump_0": "Ctrl+1",
                "tab_jump_1": "Ctrl+2",
                "tab_jump_2": "Ctrl+3",
                "tab_jump_3": "Ctrl+4",
                "tab_jump_4": "Ctrl+5",
                "tab_jump_5": "Ctrl+6",
            },
            "quick_generate_projection": "current",
            "pwards_api": {
                "base_url": "",
                "api_code": "",
                "poll_interval_sec": 60,
                "enabled": False,
            },
        }
        self.load()
        self._normalize_after_load()

    def load(self):
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.settings = self._deep_merge(self.settings, data)
            except Exception as e:
                log.warning(f"Failed to load settings: {e}")

    @staticmethod
    def _deep_merge(base, override):
        merged = dict(base)
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = SettingsManager._deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _normalize_after_load(self):
        defaults = {
            "window": {"geometry": None, "state": None},
            "system": {
                "gpu_acceleration": False,
                "use_gpu_rendering": "auto",
                "max_threads": 4,
                "cache_size_mb": 2048,
                "render_quality": "2km res",
                "texture_cache_size_mb": 256,
                "adaptive_quality": True,
                "animation_frame_skip": 0,
                "async_overlay_rendering": True,
                "lod_bias": 0.0,
                "overlay_update_throttle_ms": 50,
                "numba_jit": True,
                "coastline_resolution": "Full",
                "resampling_method": "nearest"
            },
            "bg_compositing": True,
            "precache_bands": False,
            "lazy_nc": True,
            "fullres_warning_skip": False,
            "auto_fullres_prompt": True,
            "animation_fps": 5,
            "animation_controls_on_viewport": False,
            "old_tracks": "Archive",
            "footer_size": "small",
            "text_size": "normal",
            "footer_show": {"satellite": True, "datetime": True, "band": True, "latlon": True},
            "footer_position": "bottom",
            "footer_info_position": "left",
            "left_panel_mode": "permanent",
            "info_box_position": "top_right",
            "track_info_position": "top_right",
            "info_box_name": "PAGASA",
            "info_box_show_minmax": True,
            "info_box_show_cursor_temp": True,
            "info_box_enabled": False,
            "grid_enabled": False,
            "visualizer": "Full Disk",
            "coast_enabled": False,
            "winds_enabled": False,
            "pro_contours": False,
            "viewport_bg": "#000000",
            "viewport_bg_use_theme_default": False,
            "cartopy_grid_in_exports": False,
            "grid_pattern": "solid",
            "par_enabled": False,
            "aor_expanded": False,
            "aor_highlight": True,
            "jma_enabled": False,
            "tcad_enabled": False,
            "tcid_enabled": False,
            "fir_enabled": False,
            "par_color": "#00FF9F",
            "jma_color": "#FFAA00",
            "tcad_color": "#FF6B6B",
            "tcid_color": "#4ECDC4",
            "fir_color": "#FFE66D",
            "custom_aor_color": "#00E5FF",
            "pro_show_detailed_stats": False,
            "wind_density": "Normal",
            "gridded_winds": False,
            "cwa_api_key": "",
            "high_res_amv": False,
            "amv_on_clouds": False,
            "forecast_preferences": {
                "show_date_time": True,
                "time_format": "military",
                "utc_offset": 0,
                "show_wind_speed": True,
                "wind_format": "kt",
                "forecast_layout": "monwatch",
                "legend_position": "bottom_left"
            },
            "climate_overlay_colors": {
                "TC": "#FF6B6B",
                "WET": "#4ECDC4",
                "DRY": "#FFE66D",
                "WARM": "#FF8C42",
                "COLD": "#74B9FF"
            },
            "climate_tc_enabled": False,
            "climate_tc_week": "Week-2",
            "climate_wet_enabled": False,
            "climate_wet_week": "Week-2",
            "climate_dry_enabled": False,
            "climate_dry_week": "Week-2",
            "climate_warm_enabled": False,
            "climate_warm_week": "Week-2",
            "climate_cold_enabled": False,
            "climate_cold_week": "Week-2",
            "alert_enabled": True,
            "alert_poll_interval_min": 10,
            "alert_marine_only": False,
            "alert_zone": "",
            "alert_sound_modern_notif": False,
            "alert_sound_soft_emer": False,
            "alert_retention_days": 7,
            "active_tab": 0,
            "shortcuts": {
                "tab_next": "Ctrl+Tab",
                "tab_prev": "Ctrl+Shift+Tab",
                "tab_jump_0": "Ctrl+1",
                "tab_jump_1": "Ctrl+2",
                "tab_jump_2": "Ctrl+3",
                "tab_jump_3": "Ctrl+4",
                "tab_jump_4": "Ctrl+5",
                "tab_jump_5": "Ctrl+6",
            },
            "pwards_api": {
                "base_url": "",
                "api_code": "",
                "poll_interval_sec": 60,
                "enabled": False,
            },
        }

        changed = False
        for k, v in defaults.items():
            if k not in self.settings:
                self.settings[k] = v
                changed = True
            elif isinstance(v, dict) and isinstance(self.settings.get(k), dict):
                for subk, subv in v.items():
                    if subk not in self.settings[k]:
                        self.settings[k][subk] = subv
                        changed = True
        if changed:
            self.save()

    def save(self):
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            import tempfile
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.settings_file.parent), prefix="settings-", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(self.settings, f, indent=2)
                os.replace(tmp_path, self.settings_file)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            log.error(f"Failed to save settings: {e}")

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        self.settings[key] = value
        if self._debounce_ms > 0:
            self._dirty = True
            if self._save_timer is None or not self._save_timer.is_alive():
                self._save_timer = threading.Timer(self._debounce_ms / 1000.0, self._debounced_save)
                self._save_timer.daemon = True
                self._save_timer.start()
        else:
            self.save()

    def _debounced_save(self):
        if self._dirty:
            self._dirty = False
            self.save()

    def save_immediate(self):
        self._dirty = False
        if self._save_timer and self._save_timer.is_alive():
            self._save_timer.cancel()
        self.save()

    def ensure_key(self, key, default):
        if key not in self.settings:
            self.settings[key] = default
            self.save()
        return self.settings[key]

    def load_accounts(self):
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return (
                    data.get("wis2box_accounts", []),
                    data.get("ftp_accounts", []),
                )
            except Exception:
                pass
        return [], []

    def load_api_accounts(self):
        """HTTPS / token-based downloader accounts (e.g. NASA Earthdata)."""
        if self.accounts_file.exists():
            try:
                with open(self.accounts_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("api_accounts", [])
            except Exception:
                pass
        return []

    def save_accounts(self, wis2box_accounts, ftp_accounts, api_accounts=None):
        if api_accounts is None:
            api_accounts = self.load_api_accounts()
        try:
            self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.accounts_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "wis2box_accounts": wis2box_accounts,
                    "ftp_accounts": ftp_accounts,
                    "api_accounts": api_accounts,
                }, f, indent=2)
            tmp.replace(self.accounts_file)
        except Exception as e:
            log.error(f"Failed to save accounts: {e}")


_APPLY_GRID_KEYS = ("grid_color", "grid_opacity", "grid_line_width", "grid_spacing_deg", "grid_pattern",
                    "grid_sub_step_tenths", "grid_coast_render_mode", "cartopy_grid_in_exports")
_APPLY_COAST_KEYS = ("coast_color", "coast_opacity", "coast_line_width", "coast_pattern", "coastline_resolution")
_APPLY_THEME_KEYS = ("theme_name", "viewport_bg", "viewport_bg_use_theme_default")
_APPLY_OVERLAY_SNAP_KEYS = _APPLY_GRID_KEYS + _APPLY_COAST_KEYS + _APPLY_THEME_KEYS + (
    "wind_density", "gridded_winds", "amv_on_clouds", "viewport_mode", "visualizer")


class SettingsWindow(QWidget):
    _closing = False

    def closeEvent(self, event):
        if not self._closing:
            self._closing = True
            self._save_and_close()
        event.accept()
    def __init__(self, parent, settings_mgr):
        super().__init__(parent, Qt.Window)
        self.settings = settings_mgr
        self.setWindowTitle("Settings")
        self.resize(540, 700)

        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # ----- Performance Tab -----
        perf_tab = QWidget()
        perf_scroll = QScrollArea()
        perf_scroll.setWidgetResizable(True)
        perf_scroll.setWidget(perf_tab)
        perf_layout = QVBoxLayout(perf_tab)

        self.gpu_cb = QCheckBox("GPU Acceleration (OpenGL)")
        self.gpu_cb.setChecked(self.settings.get("gpu_acceleration", False))
        self.gpu_cb.toggled.connect(self.update_gpu_warning)
        perf_layout.addWidget(self.gpu_cb)

        self.gpu_warning = QLabel("Requires restart of current image to take effect")
        self.gpu_warning.setStyleSheet("color: #FFA500; font-size: 8pt; margin-left: 20px;")
        self.gpu_warning.setVisible(self.gpu_cb.isChecked())
        perf_layout.addWidget(self.gpu_warning)

        gpu_status_row = QHBoxLayout()
        try:
            import cupy as cp
            cp.array([1.0])
            if cp.cuda.runtime.getDeviceCount() > 0:
                HAS_CUPY = True
            else:
                HAS_CUPY = False
        except Exception:
            HAS_CUPY = False
        if HAS_CUPY:
            try:
                dev = cp.cuda.Device(0)
                dev_name = cp.cuda.runtime.getDeviceProperties(dev.id).get("name", b"GPU")
                if isinstance(dev_name, bytes):
                    dev_name = dev_name.decode(errors="replace").rstrip("\x00")
            except Exception:
                dev_name = "CUDA GPU"
            gpu_compute_lbl = QLabel(f"GPU Compute: Active ({dev_name})")
            gpu_compute_lbl.setStyleSheet("color: #4CAF50; font-size: 8pt; font-weight: bold;")
        else:
            gpu_compute_lbl = QLabel("GPU Compute: Not available (cupy not installed using CPU)")
            gpu_compute_lbl.setStyleSheet("color: #888; font-size: 8pt;")
        gpu_status_row.addWidget(gpu_compute_lbl)
        gpu_status_row.addStretch()
        perf_layout.addLayout(gpu_status_row)

        try:
            from numba import njit
            HAS_NUMBA = True
        except ImportError:
            HAS_NUMBA = False
        if HAS_NUMBA:
            numba_lbl = QLabel("Numba JIT: Available")
            numba_lbl.setStyleSheet("color: #4CAF50; font-size: 8pt;")
        else:
            numba_lbl = QLabel("Numba JIT: Not available (install numba for extra CPU speedup)")
            numba_lbl.setStyleSheet("color: #888; font-size: 8pt;")
        perf_layout.addWidget(numba_lbl)

        threads_layout = QHBoxLayout()
        threads_layout.addWidget(QLabel("Max Threads:"))
        self.threads_combo = QComboBox()
        self.threads_combo.addItem("Auto", 0)
        for n in range(1, 33):
            self.threads_combo.addItem(str(n), n)
        current_val = self.settings.get("max_threads", 4)
        idx = self.threads_combo.findData(current_val)
        if idx < 0:
            idx = self.threads_combo.findData(4)
        self.threads_combo.setCurrentIndex(idx)
        threads_layout.addWidget(self.threads_combo)
        threads_layout.addStretch()
        perf_layout.addLayout(threads_layout)

        cache_layout = QHBoxLayout()
        cache_layout.addWidget(QLabel("Pre-cache RAM limit (MB):"))
        self.cache_size_spin = QSpinBox()
        self.cache_size_spin.setRange(50, 4096)
        self.cache_size_spin.setSingleStep(100)
        self.cache_size_spin.setValue(self.settings.get("cache_size_mb", 1000))
        cache_layout.addWidget(self.cache_size_spin)
        cache_layout.addStretch()
        perf_layout.addLayout(cache_layout)

        quality_layout = QHBoxLayout()
        quality_layout.addWidget(QLabel("Preview Quality:"))
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(QUALITY_LABELS)
        current_qual = self.settings.get("render_quality", "2km res")
        idx = QUALITY_VALUES.index(current_qual) if current_qual in QUALITY_VALUES else 0
        self.quality_combo.setCurrentIndex(idx)
        quality_layout.addWidget(self.quality_combo)
        quality_layout.addStretch()
        perf_layout.addLayout(quality_layout)

        overlay_res_layout = QHBoxLayout()
        overlay_res_layout.addWidget(QLabel("Overlay Resolution:"))
        self.overlay_mode_combo = QComboBox()
        self.overlay_mode_combo.addItems(["Cached", "Real-time"])
        current_overlay = self.settings.get("overlay_mode", "cached")
        self.overlay_mode_combo.setCurrentIndex(0 if current_overlay == "cached" else 1)
        overlay_res_layout.addWidget(self.overlay_mode_combo)
        overlay_hint = QLabel("(Cached: disk-persistent overlays | Real-time: recalculates on each move)")
        overlay_hint.setStyleSheet("color: #888; font-size: 7pt;")
        overlay_res_layout.addWidget(overlay_hint)
        overlay_res_layout.addStretch()
        perf_layout.addLayout(overlay_res_layout)

        tex_layout = QHBoxLayout()
        tex_layout.addWidget(QLabel("Texture Cache (MB):"))
        self.tex_cache_spin = QSpinBox()
        self.tex_cache_spin.setRange(64, 4096)
        self.tex_cache_spin.setSingleStep(64)
        self.tex_cache_spin.setValue(self.settings.get("texture_cache_size_mb", 256))
        tex_layout.addWidget(self.tex_cache_spin)
        tex_hint = QLabel("(Qt image cache, per-texture)")
        tex_hint.setStyleSheet("color: #888; font-size: 7pt;")
        tex_layout.addWidget(tex_hint)
        tex_layout.addStretch()
        perf_layout.addLayout(tex_layout)

        wind_layout = QHBoxLayout()
        wind_layout.addWidget(QLabel("Wind Density:"))
        self.wind_density_combo = QComboBox()
        self.wind_density_combo.addItem("Low", "Low")
        self.wind_density_combo.addItem("Medium", "Medium")
        self.wind_density_combo.addItem("Normal", "Normal")
        self.wind_density_combo.addItem("High", "High")
        self.wind_density_combo.addItem("Intensive (may crash)", "Intensive")
        _wind_idx = self.wind_density_combo.findData(self.settings.get("wind_density", "Normal"))
        self.wind_density_combo.setCurrentIndex(_wind_idx if _wind_idx >= 0 else 2)
        wind_layout.addWidget(self.wind_density_combo)
        wind_layout.addStretch()
        wind_hint = QLabel("Normal = SATAID-like density")
        wind_hint.setStyleSheet("color: #888; font-size: 7pt;")
        wind_layout.addWidget(wind_hint)
        perf_layout.addLayout(wind_layout)

        grid_wind_layout = QHBoxLayout()
        self.gridded_winds_cb = QCheckBox("Gridded winds")
        self.gridded_winds_cb.setChecked(self.settings.get("gridded_winds", False))
        grid_wind_layout.addWidget(self.gridded_winds_cb)
        grid_wind_hint = QLabel("(show gridded arrows instead of drawn barbs)")
        grid_wind_hint.setStyleSheet("color: #888; font-size: 7pt;")
        grid_wind_layout.addWidget(grid_wind_hint)
        grid_wind_layout.addStretch()
        perf_layout.addLayout(grid_wind_layout)

        adapt_group = QGroupBox("Adaptive Performance")
        adapt_layout = QVBoxLayout(adapt_group)
        self.adaptive_quality_cb = QCheckBox("Adaptive quality during zoom/pan (lower res for 60fps)")
        self.adaptive_quality_cb.setChecked(self.settings.get("adaptive_quality", True))
        adapt_layout.addWidget(self.adaptive_quality_cb)
        self.async_overlays_cb = QCheckBox("Async overlay rendering (grid/coast/winds off main thread)")
        self.async_overlays_cb.setChecked(self.settings.get("async_overlay_rendering", True))
        adapt_layout.addWidget(self.async_overlays_cb)
        self.numba_jit_cb = QCheckBox("Numba JIT acceleration (requires numba)")
        self.numba_jit_cb.setChecked(self.settings.get("numba_jit", True))
        adapt_layout.addWidget(self.numba_jit_cb)
        lod_row = QHBoxLayout()
        lod_row.addWidget(QLabel("LOD Bias (performance vs quality):"))
        self.lod_bias_combo = QComboBox()
        self.lod_bias_combo.addItems(["Max Quality (-1.0)", "Balanced (0.0)", "Performance (+0.5)", "Max Performance (+1.0)"])
        lod_val = self.settings.get("lod_bias", 0.0)
        lod_idx = {-1.0: 0, 0.0: 1, 0.5: 2, 1.0: 3}.get(lod_val, 1)
        self.lod_bias_combo.setCurrentIndex(lod_idx)
        lod_row.addWidget(self.lod_bias_combo)
        lod_row.addStretch()
        adapt_layout.addLayout(lod_row)

        # Resampling method setting
        resample_layout = QHBoxLayout()
        resample_layout.addWidget(QLabel("Resampling Method:"))
        self.resample_method_combo = QComboBox()
        self.resample_method_combo.addItems(["None (Native)", "Nearest Neighbor", "EWA", "Bilinear", "Gradient Search"])
        resample_val = self.settings.get("resampling_method", "nearest")
        resample_map = {
            "none": 0,
            "nearest": 1,
            "ewa": 2,
            "bilinear": 3,
            "gradient_search": 4
        }
        resample_idx = resample_map.get(resample_val, 1)
        self.resample_method_combo.setCurrentIndex(resample_idx)
        resample_layout.addWidget(self.resample_method_combo)
        resample_layout.addStretch()
        adapt_layout.addLayout(resample_layout)
        # Connect the combobox to save the setting when changed
        self.resample_method_combo.currentIndexChanged.connect(self._on_resample_method_changed)
        async_throttle_row = QHBoxLayout()
        async_throttle_row.addWidget(QLabel("Overlay update throttle (ms):"))
        self.overlay_throttle_spin = QSpinBox()
        self.overlay_throttle_spin.setRange(10, 500)
        self.overlay_throttle_spin.setValue(self.settings.get("overlay_update_throttle_ms", 50))
        self.overlay_throttle_spin.setSingleStep(10)
        async_throttle_row.addWidget(self.overlay_throttle_spin)
        async_throttle_row.addStretch()
        adapt_layout.addLayout(async_throttle_row)
        skip_row = QHBoxLayout()
        skip_row.addWidget(QLabel("Anim frame skip (0=off, 1=skip every other):"))
        self.anim_skip_spin = QSpinBox()
        self.anim_skip_spin.setRange(0, 5)
        self.anim_skip_spin.setValue(self.settings.get("animation_frame_skip", 0))
        skip_row.addWidget(self.anim_skip_spin)
        skip_row.addStretch()
        adapt_layout.addLayout(skip_row)
        perf_layout.addWidget(adapt_group)

        comp_group = QGroupBox("Compositing & Caching")
        comp_layout = QVBoxLayout(comp_group)
        self.bg_composite_cb = QCheckBox("Background Compositing (offload RGB to worker thread)")
        self.bg_composite_cb.setChecked(self.settings.get("bg_compositing", True))
        comp_layout.addWidget(self.bg_composite_cb)
        self.precache_cb = QCheckBox("Pre-cache band thumbnails in RAM (faster band switching)")
        self.precache_cb.setChecked(self.settings.get("precache_bands", False))
        comp_layout.addWidget(self.precache_cb)
        self.dual_thread_cache_cb = QCheckBox("Dual-thread band caching (B03 fast-lane on Thread 2)")
        self.dual_thread_cache_cb.setChecked(self.settings.get("dual_thread_cache", True))
        comp_layout.addWidget(self.dual_thread_cache_cb)
        self.auto_composite_cb = QCheckBox("Auto-generate RGB when all required bands cached")
        self.auto_composite_cb.setChecked(self.settings.get("auto_composite_on_cache", True))
        comp_layout.addWidget(self.auto_composite_cb)
        self.lazy_nc_cb = QCheckBox("Lazy NC loading (mask_and_scale=False, lower memory)")
        self.lazy_nc_cb.setChecked(self.settings.get("lazy_nc", True))
        comp_layout.addWidget(self.lazy_nc_cb)
        self.high_res_amv_cb = QCheckBox("High res wind AMV (read C03 data -- heavier I/O)")
        self.high_res_amv_cb.setChecked(self.settings.get("high_res_amv", False))
        comp_layout.addWidget(self.high_res_amv_cb)
        perf_layout.addWidget(comp_group)

        render_group = QGroupBox("Rendering")
        render_layout = QVBoxLayout(render_group)
        self.auto_fullres_cb = QCheckBox("Warn before loading full resolution when button is pressed")
        self.auto_fullres_cb.setChecked(self.settings.get("auto_fullres_prompt", True))
        render_layout.addWidget(self.auto_fullres_cb)
        smooth_layout = QHBoxLayout()
        smooth_layout.addWidget(QLabel("Zoom interpolation:"))
        self.zoom_interp_combo = QComboBox()
        self.zoom_interp_combo.addItems(["Nearest (fastest)", "Bilinear (smooth)", "Bicubic (sharpest)"])
        interp = self.settings.get("zoom_interpolation", "Nearest")
        self.zoom_interp_combo.setCurrentIndex({"Nearest": 0, "Bilinear": 1, "Bicubic": 2}.get(interp, 0))
        smooth_layout.addWidget(self.zoom_interp_combo)
        smooth_layout.addStretch()
        render_layout.addLayout(smooth_layout)
        
        # Grid/Coast rendering mode
        grid_coast_layout = QHBoxLayout()
        grid_coast_layout.addWidget(QLabel("Grid/Coast rendering:"))
        self.grid_coast_mode_combo = QComboBox()
        self.grid_coast_mode_combo.addItems(["Performance (QImage)", "Quality (QGraphicsPathItem)"])
        gc_mode = self.settings.get("grid_coast_render_mode", "performance")
        self.grid_coast_mode_combo.setCurrentText("Performance (QImage)" if gc_mode == "performance" else "Quality (QGraphicsPathItem)")
        grid_coast_layout.addWidget(self.grid_coast_mode_combo)
        grid_coast_layout.addStretch()
        render_layout.addLayout(grid_coast_layout)
        
        grid_sub_layout = QHBoxLayout()
        grid_sub_layout.addWidget(QLabel("Grid sub-sample step ():"))
        self.grid_sub_spin = QSpinBox()
        self.grid_sub_spin.setRange(1, 10)
        self.grid_sub_spin.setSingleStep(1)
        self.grid_sub_spin.setValue(int(self.settings.get("grid_sub_step_tenths", 5)))
        self.grid_sub_spin.setSuffix("  0.1")
        grid_sub_layout.addWidget(self.grid_sub_spin)
        grid_sub_layout.addStretch()
        render_layout.addLayout(grid_sub_layout)
        fullres_btn = QPushButton("Load Full Resolution Now (may lag/crash)")
        fullres_btn.clicked.connect(self.load_full_resolution)
        render_layout.addWidget(fullres_btn)
        perf_layout.addWidget(render_group)
        perf_layout.addStretch()

        self.tabs.addTab(perf_scroll, "Performance")

        # ----- Viewport Tab -----
        viewport_tab = QWidget()
        viewport_layout = QVBoxLayout(viewport_tab)
        viewport_group = QGroupBox("Overlay Rendering Mode")
        viewport_group_layout = QVBoxLayout(viewport_group)
        viewport_desc = QLabel("Beta: static grid/coastline at reference size, image resized to fit\n"
                                "Legacy: grid/coastline scaled to match image resolution")
        viewport_desc.setStyleSheet("color: #AAA; font-size: 10px; padding: 4px;")
        viewport_desc.setWordWrap(True)
        self.viewport_mode_combo = QComboBox()
        self.viewport_mode_combo.addItems(["Beta", "Legacy"])
        current_vp = self.settings.get("viewport_mode", "beta")
        self.viewport_mode_combo.setCurrentText("Beta" if current_vp == "beta" else "Legacy")
        viewport_group_layout.addWidget(viewport_desc)
        viewport_group_layout.addWidget(self.viewport_mode_combo)
        viewport_layout.addWidget(viewport_group)

        vis_group = QGroupBox("Visualizer")
        vis_group_layout = QVBoxLayout(vis_group)
        vis_desc = QLabel("Full Disk: Native satellite imagery\nEquirectangular: Circular Earth disk on lat/lon grid\nPlate Carree: Equirectangular projection")
        vis_desc.setStyleSheet("color: #AAA; font-size: 10px; padding: 4px;")
        vis_desc.setWordWrap(True)
        self.visualizer_combo = QComboBox()
        self.visualizer_combo.addItems(["Full Disk", "Equirectangular", "Plate Carree"])
        current_vis = self.settings.get("visualizer", "Full Disk")
        self.visualizer_combo.setCurrentText(current_vis)
        vis_group_layout.addWidget(vis_desc)
        vis_group_layout.addWidget(self.visualizer_combo)

        render_row = QHBoxLayout()
        render_row.addWidget(QLabel("Projection Engine:"))
        self.gpu_render_combo = QComboBox()
        self.gpu_render_combo.addItem("Auto (GPU preferred)", "auto")
        self.gpu_render_combo.addItem("GPU (OpenGL)", "gpu")
        self.gpu_render_combo.addItem("CPU", "cpu")
        current_engine = self.settings.get("use_gpu_rendering", "auto")
        idx = self.gpu_render_combo.findData(current_engine)
        self.gpu_render_combo.setCurrentIndex(idx if idx >= 0 else 0)
        render_row.addWidget(self.gpu_render_combo)
        render_row.addStretch()
        vis_group_layout.addLayout(render_row)
        render_hint = QLabel("GPU/Instant: OpenGL shader swap (no resample) | CPU: rasterio WarpedVRT")
        render_hint.setStyleSheet("color: #888; font-size: 7pt;")
        vis_group_layout.addWidget(render_hint)
        viewport_layout.addWidget(vis_group)

        viewport_layout.addStretch()
        self.tabs.addTab(viewport_tab, "Viewport")

        # ----- APIs Tab -----
        api_tab = QWidget()
        api_layout = QVBoxLayout(api_tab)
        windy_row = QHBoxLayout()
        windy_row.addWidget(QLabel("Windy API Key:"))
        self.windy_api_edit = QLineEdit(self.settings.get("windy_api_key", ""))
        self.windy_api_edit.setEchoMode(QLineEdit.Password)
        windy_row.addWidget(self.windy_api_edit)
        api_layout.addLayout(windy_row)
        metra_row = QHBoxLayout()
        metra_row.addWidget(QLabel("Metra Weather API Key:"))
        self.metra_api_edit = QLineEdit(self.settings.get("metra_api_key", ""))
        self.metra_api_edit.setEchoMode(QLineEdit.Password)
        metra_row.addWidget(self.metra_api_edit)
        api_layout.addLayout(metra_row)

        cwa_row = QHBoxLayout()
        cwa_row.addWidget(QLabel("CWA Authorization Code:"))
        self.cwa_api_edit = QLineEdit(self.settings.get("cwa_api_key", ""))
        self.cwa_api_edit.setEchoMode(QLineEdit.Password)
        self.cwa_api_edit.setPlaceholderText("Meteorological Open Data Platform Member Authorization Code")
        cwa_row.addWidget(self.cwa_api_edit)
        api_layout.addLayout(cwa_row)

        pwards_group = QGroupBox("PWARDS Stream API")
        pwards_form = QFormLayout(pwards_group)
        self.pwards_url_edit = QLineEdit(self.settings.get("pwards_api", {}).get("base_url", ""))
        self.pwards_url_edit.setPlaceholderText("http://your-server:5000")
        pwards_form.addRow("Base URL:", self.pwards_url_edit)
        self.pwards_code_edit = QLineEdit(self.settings.get("pwards_api", {}).get("api_code", ""))
        self.pwards_code_edit.setEchoMode(QLineEdit.Password)
        self.pwards_code_edit.setPlaceholderText("API access code")
        pwards_form.addRow("API Code:", self.pwards_code_edit)
        self.pwards_interval_spin = QSpinBox()
        self.pwards_interval_spin.setRange(10, 600)
        self.pwards_interval_spin.setValue(self.settings.get("pwards_api", {}).get("poll_interval_sec", 60))
        self.pwards_interval_spin.setSuffix(" sec")
        pwards_form.addRow("Poll Interval:", self.pwards_interval_spin)
        self.pwards_enabled_btn = QPushButton()
        self.pwards_enabled_btn.setCheckable(True)
        pw_enabled = self.settings.get("pwards_api", {}).get("enabled", False)
        self.pwards_enabled_btn.setChecked(pw_enabled)
        self.pwards_enabled_btn.setText("Stream Enabled" if pw_enabled else "Stream Disabled")
        self.pwards_enabled_btn.setStyleSheet(
            "QPushButton:checked { background: #2e7d32; color: white; font-weight: bold; }"
            "QPushButton:!checked { background: #444; color: #aaa; }"
        )
        pwards_form.addRow("Status:", self.pwards_enabled_btn)
        pwards_test_row = QHBoxLayout()
        self.pwards_test_btn = QPushButton("Test Connection")
        self.pwards_test_btn.clicked.connect(self._test_pwards_connection)
        pwards_test_row.addWidget(self.pwards_test_btn)
        self.pwards_status_lbl = QLabel("")
        self.pwards_status_lbl.setStyleSheet("color: #888;")
        pwards_test_row.addWidget(self.pwards_status_lbl)
        pwards_test_row.addStretch()
        pwards_form.addRow("", pwards_test_row)
        pwards_cfg = self.settings.get("pwards_api", {})
        pwards_configured = bool(pwards_cfg.get("enabled") and pwards_cfg.get("base_url") and pwards_cfg.get("api_code"))
        if not pwards_configured:
            pwards_group.setVisible(False)
        api_layout.addWidget(pwards_group)

        api_layout.addStretch()
        self.tabs.addTab(api_tab, "APIs")

        # ----- Paths Tab -----
        paths_tab = QWidget()
        paths_layout = QVBoxLayout(paths_tab)
        paths_layout.setSpacing(12)

        top_dir = Path(__file__).resolve().parent.parent.parent
        paths_data = self.settings.get("paths", {})

        dl_group = QGroupBox("Download Folder")
        dl_layout = QHBoxLayout(dl_group)
        self.dl_path_edit = QLineEdit()
        default_dl = str(top_dir / "data" / "Download")
        self.dl_path_edit.setText(paths_data.get("download_folder", "") or default_dl)
        self.dl_path_edit.setPlaceholderText(default_dl)
        dl_layout.addWidget(self.dl_path_edit)
        dl_browse = QPushButton("Browse")
        dl_browse.clicked.connect(lambda: self._browse_path(self.dl_path_edit))
        dl_layout.addWidget(dl_browse)
        dl_reset = QPushButton("Reset")
        dl_reset.clicked.connect(lambda: self.dl_path_edit.setText(default_dl))
        dl_layout.addWidget(dl_reset)
        paths_layout.addWidget(dl_group)

        ex_group = QGroupBox("Export Folder")
        ex_layout = QHBoxLayout(ex_group)
        self.ex_path_edit = QLineEdit()
        default_ex = str(top_dir / "Exports")
        self.ex_path_edit.setText(paths_data.get("export_folder", "") or default_ex)
        self.ex_path_edit.setPlaceholderText(default_ex)
        ex_layout.addWidget(self.ex_path_edit)
        ex_browse = QPushButton("Browse")
        ex_browse.clicked.connect(lambda: self._browse_path(self.ex_path_edit))
        ex_layout.addWidget(ex_browse)
        ex_reset = QPushButton("Reset")
        ex_reset.clicked.connect(lambda: self.ex_path_edit.setText(default_ex))
        ex_layout.addWidget(ex_reset)
        paths_layout.addWidget(ex_group)

        pl_group = QGroupBox("Plugins Folder")
        pl_layout = QHBoxLayout(pl_group)
        self.pl_path_edit = QLineEdit()
        default_pl = str(top_dir / "plugins")
        self.pl_path_edit.setText(paths_data.get("plugins_folder", "") or default_pl)
        self.pl_path_edit.setPlaceholderText(default_pl)
        self.pl_path_edit.setEnabled(False)
        self.pl_path_edit.setStyleSheet("color: #666;")
        self.pl_path_edit.setToolTip("Plugins are not yet supported. This folder is reserved for future use.")
        pl_group.setToolTip("Plugins are not yet supported. This folder is reserved for future use.")
        pl_layout.addWidget(self.pl_path_edit)
        pl_browse = QPushButton("Browse")
        pl_browse.setEnabled(False)
        pl_layout.addWidget(pl_browse)
        pl_reset = QPushButton("Reset")
        pl_reset.setEnabled(False)
        pl_reset.clicked.connect(lambda: self.pl_path_edit.setText(default_pl))
        pl_layout.addWidget(pl_reset)
        paths_layout.addWidget(pl_group)

        ot_group = QGroupBox("Old NHC Tracks")
        ot_layout = QHBoxLayout(ot_group)
        ot_layout.addWidget(QLabel("Action for storms no longer tracked by NHC:"))
        self.old_tracks_combo = QComboBox()
        self.old_tracks_combo.addItems(["Archive", "Delete"])
        self.old_tracks_combo.setCurrentText(self.settings.get("old_tracks", "Archive"))
        ot_layout.addWidget(self.old_tracks_combo)
        ot_layout.addStretch()
        paths_layout.addWidget(ot_group)

        paths_layout.addStretch()
        self.tabs.addTab(paths_tab, "Paths")

        # ----- Theme Tab -----
        theme_tab = QWidget()
        theme_scroll = QScrollArea()
        theme_scroll.setWidgetResizable(True)
        theme_scroll.setWidget(theme_tab)
        theme_layout = QVBoxLayout(theme_tab)
        theme_layout.setSpacing(12)

        theme_box = QGroupBox("Application Theme")
        theme_box_layout = QVBoxLayout(theme_box)
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(THEMES.keys()))
        current_theme = self.settings.get("theme_name", "Dark (Default)")
        idx = self.theme_combo.findText(current_theme)
        self.theme_combo.setCurrentIndex(max(0, idx))
        theme_row.addWidget(self.theme_combo, 1)
        theme_box_layout.addLayout(theme_row)
        self.preview_label = QLabel("Preview: background / text / accent")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setFixedHeight(32)
        self.preview_label.setStyleSheet("border-radius:4px; font-size:10px; padding:4px;")
        theme_box_layout.addWidget(self.preview_label)
        self.theme_combo.currentTextChanged.connect(self._update_preview)
        self._update_preview(self.theme_combo.currentText())
        theme_layout.addWidget(theme_box)

        vp_box = QGroupBox("Viewport Background")
        vp_box_layout = QVBoxLayout(vp_box)
        self.viewport_bg_use_default_cb = QCheckBox("Use Theme Default")
        self.viewport_bg_use_default_cb.setChecked(self.settings.get("viewport_bg_use_theme_default", False))
        vp_box_layout.addWidget(self.viewport_bg_use_default_cb)
        vp_color_row = QHBoxLayout()
        vp_color_row.addWidget(QLabel("Color:"))
        self.viewport_bg_btn = ColorButton(self.settings.get("viewport_bg", "#000000"))
        self.viewport_bg_btn.colorChanged.connect(lambda c: None)
        vp_color_row.addWidget(self.viewport_bg_btn)
        vp_color_row.addStretch()
        vp_box_layout.addLayout(vp_color_row)
        theme_layout.addWidget(vp_box)
        self.viewport_bg_use_default_cb.toggled.connect(self._on_viewport_bg_use_default_toggled)
        self._on_viewport_bg_use_default_toggled(self.viewport_bg_use_default_cb.isChecked())

        grid_box = QGroupBox("Grid Overlay")
        grid_box_layout = QGridLayout(grid_box)
        grid_box_layout.setColumnStretch(1, 1)
        grid_box_layout.addWidget(QLabel("Color:"), 0, 0)
        self.grid_color_btn = ColorButton(self.settings.get("grid_color", "#C8C8C8"))
        grid_box_layout.addWidget(self.grid_color_btn, 0, 1)
        grid_box_layout.addWidget(QLabel("Opacity (0-255):"), 1, 0)
        self.grid_opacity_spin = QSpinBox()
        self.grid_opacity_spin.setRange(0, 255)
        self.grid_opacity_spin.setValue(self.settings.get("grid_opacity", 160))
        grid_box_layout.addWidget(self.grid_opacity_spin, 1, 1)
        grid_box_layout.addWidget(QLabel("Line Width (px):"), 2, 0)
        self.grid_width_spin = QSpinBox()
        self.grid_width_spin.setRange(1, 10)
        self.grid_width_spin.setValue(self.settings.get("grid_line_width", 1))
        grid_box_layout.addWidget(self.grid_width_spin, 2, 1)
        grid_box_layout.addWidget(QLabel("Spacing (degrees):"), 3, 0)
        self.grid_spacing_spin = QSpinBox()
        self.grid_spacing_spin.setRange(1, 45)
        self.grid_spacing_spin.setValue(self.settings.get("grid_spacing_deg", 10))
        grid_box_layout.addWidget(self.grid_spacing_spin, 3, 1)
        grid_box_layout.addWidget(QLabel("Pattern:"), 4, 0)
        self.grid_pattern_combo = QComboBox()
        self.grid_pattern_combo.addItems(["solid", "dotted", "dashed", "dashdot", "crosshatch"])
        self.grid_pattern_combo.setCurrentText(self.settings.get("grid_pattern", "solid"))
        grid_box_layout.addWidget(self.grid_pattern_combo, 4, 1)
        self.cartopy_grid_cb = QCheckBox("Use Cartopy grid in exported images (requires cartopy)")
        self.cartopy_grid_cb.setChecked(self.settings.get("cartopy_grid_in_exports", False))
        grid_box_layout.addWidget(self.cartopy_grid_cb, 5, 0, 1, 2)
        theme_layout.addWidget(grid_box)

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
        coast_layout.addWidget(QLabel("Resolution:"), 4, 0)
        self.coast_resolution_combo = QComboBox()
        self.coast_resolution_combo.addItems(["Low (every 8th pt)", "Medium (every 4th pt)", "High (every 2nd pt)", "Full (all pts)"])
        _coast_res_map = {"Low": 0, "Medium": 1, "High": 2, "Full": 3}
        _coast_res_rev = {0: "Low", 1: "Medium", 2: "High", 3: "Full"}
        current_res = self.settings.get("coastline_resolution", "Full")
        idx = _coast_res_map.get(current_res, 3)
        self.coast_resolution_combo.setCurrentIndex(idx)
        coast_layout.addWidget(self.coast_resolution_combo, 4, 1)
        theme_layout.addWidget(coast_box)

        aor_box = QGroupBox("AoR (Area of Responsibility)")
        aor_grid = QGridLayout(aor_box)
        aor_grid.setColumnStretch(1, 1)
        aor_grid.addWidget(QLabel("Line Style:"), 0, 0)
        self.aor_pattern_combo = QComboBox()
        self.aor_pattern_combo.addItems(["dashed", "solid", "dotted", "dashdot", "crosshatch"])
        self.aor_pattern_combo.setCurrentText(self.settings.get("aor_pattern", "dashed"))
        aor_grid.addWidget(self.aor_pattern_combo, 0, 1)
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
            aor_grid.addWidget(QLabel(label + ":"), row, 0)
            btn = ColorButton(self.settings.get(key, default))
            aor_grid.addWidget(btn, row, 1)
            self._aor_color_btns[key] = btn
        theme_layout.addWidget(aor_box)

        anim_box = QGroupBox("Animation Playback Controls")
        anim_box_layout = QVBoxLayout(anim_box)
        self.anim_controls_cb = QCheckBox("Show Play/Pause/Speed/Scrub bar at bottom of viewport")
        self.anim_controls_cb.setChecked(self.settings.get("animation_controls_on_viewport", False))
        self.anim_controls_cb.setStyleSheet("font-size: 10px;")
        anim_box_layout.addWidget(self.anim_controls_cb)
        
        self.amv_on_clouds_cb = QCheckBox("AMV's on clouds")
        self.amv_on_clouds_cb.setChecked(self.settings.get("amv_on_clouds", False))
        self.amv_on_clouds_cb.setStyleSheet("font-size: 10px;")
        anim_box_layout.addWidget(self.amv_on_clouds_cb)
        
        theme_layout.addWidget(anim_box)

        ib_group = QGroupBox("Info Box")
        ib_layout = QVBoxLayout(ib_group)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Info Box Name:"))
        self.ib_name_edit = QLineEdit(self.settings.get("info_box_name", "PAGASA"))
        name_row.addWidget(self.ib_name_edit)
        ib_layout.addLayout(name_row)
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Info Box Position:"))
        self.ib_pos_combo = QComboBox()
        self.ib_pos_combo.addItems(["top_right", "top_left", "bottom_right", "bottom_left", "center"])
        self.ib_pos_combo.setCurrentText(self.settings.get("info_box_position", "top_right"))
        pos_row.addWidget(self.ib_pos_combo)
        ib_layout.addLayout(pos_row)
        
        track_pos_row = QHBoxLayout()
        track_pos_row.addWidget(QLabel("Track Legends Position:"))
        self.track_info_pos_combo = QComboBox()
        self.track_info_pos_combo.addItems(["Top Right", "Top Left", "Bottom Left", "Bottom Right"])
        self.track_info_pos_combo.setCurrentText(self.settings.get("track_info_position", "Top Right").replace("_", " ").title())
        track_pos_row.addWidget(self.track_info_pos_combo)
        ib_layout.addLayout(track_pos_row)

        self.force_full_cache_cb = QCheckBox("Force full band cache (ignore RAM limit)")
        self.force_full_cache_cb.setChecked(self.settings.get("force_full_cache", False))
        ib_layout.addWidget(self.force_full_cache_cb)
        throttle_row = QHBoxLayout()
        throttle_row.addWidget(QLabel("Mouse Update Throttle (ms):"))
        self.mouse_throttle_spin = QSpinBox()
        self.mouse_throttle_spin.setRange(0, 500)
        self.mouse_throttle_spin.setValue(self.settings.get("mouse_throttle_ms", 80))
        self.mouse_throttle_spin.setSingleStep(10)
        throttle_row.addWidget(self.mouse_throttle_spin)
        ib_layout.addLayout(throttle_row)
        gpu_mem_row = QHBoxLayout()
        gpu_mem_row.addWidget(QLabel("GPU Memory Target (MB):"))
        self.gpu_mem_target = QSpinBox()
        self.gpu_mem_target.setRange(128, 4096)
        self.gpu_mem_target.setValue(self.settings.get("gpu_memory_target_mb", 512))
        self.gpu_mem_target.setSingleStep(128)
        gpu_mem_row.addWidget(self.gpu_mem_target)
        ib_layout.addLayout(gpu_mem_row)
        theme_layout.addWidget(ib_group)

        contour_box = QGroupBox("Contour Tool")
        contour_box_layout = QVBoxLayout(contour_box)
        self.modern_contour_cb = QCheckBox("Modern Contour (colored fill, white lines)")
        self.modern_contour_cb.setChecked(self.settings.get("modern_contour", False))
        contour_box_layout.addWidget(self.modern_contour_cb)
        theme_layout.addWidget(contour_box)

        theme_layout.addStretch()
        self.tabs.addTab(theme_scroll, "Theme")

        # ----- Preferences Tab -----
        pref_tab = QWidget()
        pref_layout = QVBoxLayout(pref_tab)
        pref_layout.setSpacing(12)

        fcst_group = QGroupBox("Forecast Preferences")
        fcst_layout = QVBoxLayout(fcst_group)
        fcst_prefs = self.settings.get("forecast_preferences", {})

        self.fcst_dt_cb = QCheckBox("Date and time")
        self.fcst_dt_cb.setChecked(fcst_prefs.get("show_date_time", True))
        fcst_layout.addWidget(self.fcst_dt_cb)

        time_fmt_row = QHBoxLayout()
        time_fmt_row.addWidget(QLabel("Time Format:"))
        self.fcst_time_fmt_combo = QComboBox()
        self.fcst_time_fmt_combo.addItems(["Military (24h)", "Civilian (12h)"])
        self.fcst_time_fmt_combo.setCurrentIndex(0 if fcst_prefs.get("time_format", "military") == "military" else 1)
        time_fmt_row.addWidget(self.fcst_time_fmt_combo)
        time_fmt_row.addStretch()
        fcst_layout.addLayout(time_fmt_row)

        utc_row = QHBoxLayout()
        utc_row.addWidget(QLabel("Time Location/UTC:"))
        self.fcst_utc_combo = QComboBox()
        self.fcst_utc_combo.addItems([str(v) for v in range(-12, 13)])
        current_utc = fcst_prefs.get("utc_offset", 0)
        self.fcst_utc_combo.setCurrentIndex(current_utc + 12)
        utc_row.addWidget(self.fcst_utc_combo)
        utc_row.addStretch()
        fcst_layout.addLayout(utc_row)

        self.fcst_wind_cb = QCheckBox("Wind speed")
        self.fcst_wind_cb.setChecked(fcst_prefs.get("show_wind_speed", True))
        fcst_layout.addWidget(self.fcst_wind_cb)

        wind_fmt_row = QHBoxLayout()
        wind_fmt_row.addWidget(QLabel("Wind format:"))
        self.fcst_wind_fmt_combo = QComboBox()
        self.fcst_wind_fmt_combo.addItems(["kt", "km/h", "mph", "m/s"])
        self.fcst_wind_fmt_combo.setCurrentText(fcst_prefs.get("wind_format", "kt"))
        wind_fmt_row.addWidget(self.fcst_wind_fmt_combo)
        wind_fmt_row.addStretch()
        fcst_layout.addLayout(wind_fmt_row)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Forecast Layout:"))
        self.fcst_layout_combo = QComboBox()
        layouts = ["MonWatch-UI", "PAGASA", "PWARDS", "JTWC", "JMA", "NHC (Tropycal)", "Develope"]
        self.fcst_layout_combo.addItems(layouts)
        model = self.fcst_layout_combo.model()
        # model.item(1).setEnabled(False)  # PAGASA -- now enabled
        # model.item(3).setEnabled(False)  # JTWC -- now enabled
        # model.item(4).setEnabled(False)  # JMA -- now enabled
        current_layout = fcst_prefs.get("forecast_layout", "default")
        # Migrate old "default" value to "monwatch" (new application default)
        if current_layout == "default":
            current_layout = "monwatch"
            fcst_prefs["forecast_layout"] = "monwatch"
            self.settings.settings["forecast_preferences"] = fcst_prefs
        layout_keys = ["monwatch", "pagasa", "pwards", "jtwc", "jma", "nhc", "develope"]
        self.fcst_layout_combo.setCurrentIndex(layout_keys.index(current_layout) if current_layout in layout_keys else 0)
        self.fcst_layout_combo.currentIndexChanged.connect(self._on_fcst_layout_changed)
        layout_row.addWidget(self.fcst_layout_combo)
        layout_row.addStretch()
        fcst_layout.addLayout(layout_row)

        legend_row = QHBoxLayout()
        legend_row.addWidget(QLabel("Legend Position:"))
        self.fcst_legend_pos_combo = QComboBox()
        legend_positions = ["Top Right", "Top Left", "Bottom Left (Default)", "Bottom Right"]
        self.fcst_legend_pos_combo.addItems(legend_positions)
        current_legend = fcst_prefs.get("legend_position", "bottom_left")
        legend_keys = ["top_right", "top_left", "bottom_left", "bottom_right"]
        self.fcst_legend_pos_combo.setCurrentIndex(legend_keys.index(current_legend) if current_legend in legend_keys else 2)
        legend_row.addWidget(self.fcst_legend_pos_combo)
        legend_row.addStretch()
        fcst_layout.addLayout(legend_row)

        pref_layout.addWidget(fcst_group)

        label_group = QGroupBox("Labeling")
        label_layout = QVBoxLayout(label_group)
        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Labeling Method:"))
        self.labeling_method_combo = QComboBox()
        methods = ["Polar", "Bezier", "Smart Bezier", "Anti-Clima", "Anti-Clima V2", "Greedy", "Offset",
                    "Force", "Anneal", "MILP", "Railway Bezier", "8-Direction",
                    "Staggered Perp", "Auto"]
        self.labeling_method_combo.addItems(methods)
        current_method = self.settings.get("labeling_method", "polar")
        method_map = {
            "polar": 0, "bezier": 1, "smart_bezier": 2,
            "anticlima": 3, "anticlima_v2": 4, "greedy": 5, "offset": 6, "force": 7,
            "anneal": 8, "milp": 9, "railway_bezier": 10,
            "8direction": 11, "staggered_perp": 12, "auto": 13
        }
        self.labeling_method_combo.setCurrentIndex(method_map.get(current_method, 0))
        label_row.addWidget(self.labeling_method_combo)
        label_row.addStretch()
        label_layout.addLayout(label_row)

        cone_row = QHBoxLayout()
        cone_row.addWidget(QLabel("Cone:"))
        self.cone_combo = QComboBox()
        self.cone_combo.addItems(["Smooth", "Union"])
        current_cone = self.settings.get("cone_method", "smooth")
        self.cone_combo.setCurrentIndex(0 if current_cone == "smooth" else 1)
        cone_row.addWidget(self.cone_combo)
        cone_row.addStretch()
        label_layout.addLayout(cone_row)
        pref_layout.addWidget(label_group)

        alerts_group = QGroupBox("Weather Alerts")
        alerts_layout = QVBoxLayout(alerts_group)
        
        # Main enable checkbox
        self.weather_alerts_enabled_cb = QCheckBox("Enable Weather Alerts")
        self.weather_alerts_enabled_cb.setChecked(self.settings.get("weather_alerts_enabled", True))
        self.weather_alerts_enabled_cb.setStyleSheet("font-weight: bold; font-size: 10px; color: #FF9800;")
        alerts_layout.addWidget(self.weather_alerts_enabled_cb)
        
        retention_row = QHBoxLayout()
        retention_row.addWidget(QLabel("Auto-delete alerts older than:"))
        self.alert_retention_spin = QSpinBox()
        self.alert_retention_spin.setRange(1, 90)
        self.alert_retention_spin.setValue(self.settings.get("alert_retention_days", 7))
        self.alert_retention_spin.setSuffix(" days")
        retention_row.addWidget(self.alert_retention_spin)
        retention_row.addStretch()
        alerts_layout.addLayout(retention_row)
        
        # NWS Settings
        nws_group = QGroupBox("NWS (weather.gov)")
        nws_group.setStyleSheet("QGroupBox { color: #4CAF50; font-weight: bold; border: 1px solid #555; border-radius: 4px; margin-top: 6px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        nws_layout = QVBoxLayout(nws_group)
        self.nws_enabled_cb = QCheckBox("Enable NWS")
        self.nws_enabled_cb.setChecked(self.settings.get("nws_enabled", True))
        nws_layout.addWidget(self.nws_enabled_cb)
        alert_int_row = QHBoxLayout()
        alert_int_row.addWidget(QLabel("Poll interval (minutes):"))
        self.alert_interval_spin = QSpinBox()
        self.alert_interval_spin.setRange(1, 120)
        self.alert_interval_spin.setValue(self.settings.get("alert_poll_interval_min", 10))
        self.alert_interval_spin.setSuffix(" min")
        alert_int_row.addWidget(self.alert_interval_spin)
        alert_int_row.addStretch()
        nws_layout.addLayout(alert_int_row)
        self.alert_marine_cb = QCheckBox("Marine alerts only (for maritime/typhoon ops)")
        self.alert_marine_cb.setChecked(self.settings.get("alert_marine_only", False))
        nws_layout.addWidget(self.alert_marine_cb)
        zone_row = QHBoxLayout()
        zone_row.addWidget(QLabel("NWS Zone (optional, e.g. PHZ001):"))
        self.alert_zone_edit = QLineEdit()
        self.alert_zone_edit.setText(self.settings.get("alert_zone", ""))
        self.alert_zone_edit.setPlaceholderText("e.g. PHZ001, AMZ117")
        zone_row.addWidget(self.alert_zone_edit)
        zone_row.addStretch()
        nws_layout.addLayout(zone_row)
        alerts_layout.addWidget(nws_group)
        
        # PAGASA Settings
        pagasa_group = QGroupBox("PAGASA (panahon.gov.ph)")
        pagasa_group.setStyleSheet("QGroupBox { color: #FF9800; font-weight: bold; border: 1px solid #555; border-radius: 4px; margin-top: 6px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        pagasa_layout = QVBoxLayout(pagasa_group)
        self.pagasa_enabled_cb = QCheckBox("Enable PAGASA")
        self.pagasa_enabled_cb.setChecked(self.settings.get("pagasa_enabled", False))
        pagasa_layout.addWidget(self.pagasa_enabled_cb)
        pagasa_int_row = QHBoxLayout()
        pagasa_int_row.addWidget(QLabel("Poll interval (minutes):"))
        self.pagasa_interval_spin = QSpinBox()
        self.pagasa_interval_spin.setRange(1, 120)
        self.pagasa_interval_spin.setValue(self.settings.get("pagasa_poll_interval_min", 10))
        self.pagasa_interval_spin.setSuffix(" min")
        pagasa_int_row.addWidget(self.pagasa_interval_spin)
        pagasa_int_row.addStretch()
        pagasa_layout.addLayout(pagasa_int_row)
        alerts_layout.addWidget(pagasa_group)
        
        sound_group = QGroupBox("Alert Sounds")
        sound_group.setStyleSheet("QGroupBox { color: #FF9800; font-weight: bold; border: 1px solid #555; border-radius: 4px; margin-top: 6px; padding-top: 10px; } QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }")
        sound_layout = QVBoxLayout(sound_group)
        self.alert_modern_notif_cb = QCheckBox("Use modern notification sound (modern_notif.m4a) instead of default (notif.m4a)")
        self.alert_modern_notif_cb.setChecked(self.settings.get("alert_sound_modern_notif", False))
        sound_layout.addWidget(self.alert_modern_notif_cb)
        self.alert_soft_emer_cb = QCheckBox("Use soft emergency sound (Soft_Emer.m4a) for non-tornado alerts instead of default (Emer.m4a)")
        self.alert_soft_emer_cb.setChecked(self.settings.get("alert_sound_soft_emer", False))
        sound_layout.addWidget(self.alert_soft_emer_cb)
        sound_info = QLabel("Extreme/Severe alerts play Emer.m4a automatically. The full-emergency siren (Fulemer.m4a) is reserved and currently not triggered.")
        sound_info.setStyleSheet("color: #888; font-size: 8pt;")
        sound_info.setWordWrap(True)
        sound_layout.addWidget(sound_info)
        alerts_layout.addWidget(sound_group)
        pref_layout.addWidget(alerts_group)

        pref_layout.addStretch()
        self.tabs.addTab(pref_tab, "Preferences")

        # ----- Startup Tab -----
        startup_tab = QWidget()
        startup_layout = QVBoxLayout(startup_tab)
        startup_group = QGroupBox("Startup Image")
        startup_group_layout = QVBoxLayout(startup_group)
        self.startup_logo_rb = QRadioButton("Logo (MonWatch.png)")
        self.startup_himawari_rb = QRadioButton("Himawari Imagery (latest.png)")
        current_startup = self.settings.get("startup_mode", "logo")
        if current_startup == "logo":
            self.startup_logo_rb.setChecked(True)
        else:
            self.startup_himawari_rb.setChecked(True)
        startup_group_layout.addWidget(self.startup_logo_rb)
        startup_group_layout.addWidget(self.startup_himawari_rb)
        startup_layout.addWidget(startup_group)
        startup_layout.addStretch()
        self.tabs.addTab(startup_tab, "Startup")

        # ----- Footer Tab -----
        footer_tab = QWidget()
        footer_layout = QVBoxLayout(footer_tab)
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Footer Size:"))
        self.footer_size_combo = QComboBox()
        self.footer_size_combo.addItems(["small", "normal", "big"])
        self.footer_size_combo.setCurrentText(self.settings.get("footer_size", "small"))
        size_row.addWidget(self.footer_size_combo)
        size_row.addStretch()
        footer_layout.addLayout(size_row)
        txt_row = QHBoxLayout()
        txt_row.addWidget(QLabel("Text Size:"))
        self.text_size_combo = QComboBox()
        self.text_size_combo.addItems(["small", "normal", "large"])
        self.text_size_combo.setCurrentText(self.settings.get("text_size", "normal"))
        txt_row.addWidget(self.text_size_combo)
        txt_row.addStretch()
        footer_layout.addLayout(txt_row)
        fsh = self.settings.get("footer_show", {})
        self.footer_cb_sat = QCheckBox("Show Satellite name")
        self.footer_cb_sat.setChecked(fsh.get("satellite", True))
        footer_layout.addWidget(self.footer_cb_sat)
        self.footer_cb_dt = QCheckBox("Show Date & Time")
        self.footer_cb_dt.setChecked(fsh.get("datetime", True))
        footer_layout.addWidget(self.footer_cb_dt)
        self.footer_cb_band = QCheckBox("Show Band / Composition")
        self.footer_cb_band.setChecked(fsh.get("band", True))
        footer_layout.addWidget(self.footer_cb_band)
        self.footer_cb_latlon = QCheckBox("Show Lat / Lon")
        self.footer_cb_latlon.setChecked(fsh.get("latlon", True))
        footer_layout.addWidget(self.footer_cb_latlon)
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Footer Position:"))
        self.footer_pos_combo = QComboBox()
        self.footer_pos_combo.addItems(["bottom", "top"])
        self.footer_pos_combo.setCurrentText(self.settings.get("footer_position", "bottom"))
        pos_row.addWidget(self.footer_pos_combo)
        pos_row.addStretch()
        footer_layout.addLayout(pos_row)
        infopos_row = QHBoxLayout()
        infopos_row.addWidget(QLabel("Info Position:"))
        self.footer_infopos_combo = QComboBox()
        self.footer_infopos_combo.addItems(["left", "right"])
        self.footer_infopos_combo.setCurrentText(self.settings.get("footer_info_position", "left"))
        infopos_row.addWidget(self.footer_infopos_combo)
        infopos_row.addStretch()
        footer_layout.addLayout(infopos_row)
        footer_layout.addStretch()
        self.tabs.addTab(footer_tab, "Footer")

        # ----- Keybinds Tab -----
        kb_tab = QWidget()
        kb_layout = QVBoxLayout(kb_tab)
        kb_layout.setSpacing(8)

        kb_title = QLabel("Keyboard & Mouse Shortcuts")
        kb_title.setStyleSheet("font-size:12px; font-weight:bold; color:#4CAF50; padding:4px;")
        kb_layout.addWidget(kb_title)

        mouse_binds = [
            ("Ctrl+Shift+Left Click",  "Zoom in by 5x at cursor position"),
            ("Ctrl+Shift+Right Click", "Zoom out by 5x at cursor position"),
        ]
        for shortcut, desc in mouse_binds:
            row = QHBoxLayout()
            sc_lbl = QLabel(shortcut)
            sc_lbl.setStyleSheet(
                "background:#2a2a2a; color:#FFD700; font-weight:bold; "
                "border:1px solid #555; border-radius:3px; padding:4px 8px; "
                "font-family:'Courier New',monospace; font-size:10px;")
            sc_lbl.setFixedWidth(240)
            ds_lbl = QLabel(desc)
            ds_lbl.setStyleSheet("color:#ccc; font-size:10px; padding-left:8px;")
            row.addWidget(sc_lbl)
            row.addWidget(ds_lbl, 1)
            row.addStretch()
            kb_layout.addLayout(row)

        kb_sep = QLabel("Tab Navigation Shortcuts (click to edit)")
        kb_sep.setStyleSheet("font-size:11px; font-weight:bold; color:#00E5FF; padding:8px 4px 2px 4px;")
        kb_layout.addWidget(kb_sep)

        self._shortcuts_edits = {}
        tab_shortcut_defs = [
            ("tab_next", "Next Tab"),
            ("tab_prev", "Previous Tab"),
            ("tab_jump_0", "Jump to Scene tab"),
            ("tab_jump_1", "Jump to Professional tab"),
            ("tab_jump_2", "Jump to Tracks tab"),
            ("tab_jump_3", "Jump to Animation tab"),
            ("tab_jump_4", "Jump to SATAID tab"),
            ("tab_jump_5", "Jump to Multi-Viewport tab"),
        ]
        saved_shortcuts = self.settings.get("shortcuts", {})
        for key, desc in tab_shortcut_defs:
            row = QHBoxLayout()
            edit = QKeySequenceEdit(saved_shortcuts.get(key, ""))
            edit.setMaximumWidth(200)
            edit.setStyleSheet(
                "QKeySequenceEdit { background:#2a2a2a; color:#FFD700; font-weight:bold; "
                "border:1px solid #555; border-radius:3px; padding:2px 4px; font-size:10px; }")
            ds_lbl = QLabel(desc)
            ds_lbl.setStyleSheet("color:#ccc; font-size:10px; padding-left:8px;")
            reset_btn = QPushButton("X")
            reset_btn.setFixedWidth(24)
            reset_btn.setStyleSheet(
                "QPushButton { background:#3a3a3a; color:#888; border:1px solid #555; border-radius:2px; font-size:8px; }"
                "QPushButton:hover { background:#5a5a5a; color:#fff; }")
            row.addWidget(edit)
            row.addWidget(reset_btn)
            row.addWidget(ds_lbl, 1)
            row.addStretch()
            kb_layout.addLayout(row)
            self._shortcuts_edits[key] = edit

        kb_layout.addStretch()
        self.tabs.addTab(kb_tab, "Keybinds")

        # ----- Buttons -----
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(lambda checked: self._apply())
        btn_layout.addWidget(apply_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        main_layout.addLayout(btn_layout)

    def _browse_path(self, line_edit):
        d = QFileDialog.getExistingDirectory(self, "Choose Folder", line_edit.text())
        if d:
            line_edit.setText(d)

    def _test_pwards_connection(self):
        from ..clients.pwards_client import fetch_manifest
        base_url = self.pwards_url_edit.text().strip()
        api_code = self.pwards_code_edit.text().strip()
        if not base_url:
            QMessageBox.warning(self, "Missing URL", "Enter the PWARDS API base URL first.")
            return
        self.pwards_status_lbl.setText("Testing...")
        self.pwards_status_lbl.setStyleSheet("color: gray;")
        self.pwards_test_btn.setEnabled(False)
        try:
            result = fetch_manifest(base_url, api_code)
            if result is not None:
                slots = len(result.get("available", []))
                latest = result.get("latest_time", "?")
                self.pwards_status_lbl.setText(
                    f"Connected! {slots} slot(s), latest: {latest}"
                )
                self.pwards_status_lbl.setStyleSheet("color: #4CAF50; font-weight: bold;")
            else:
                self.pwards_status_lbl.setText("Connection failed — check URL and code.")
                self.pwards_status_lbl.setStyleSheet("color: red;")
        except Exception as e:
            self.pwards_status_lbl.setText(f"Error: {e}")
            self.pwards_status_lbl.setStyleSheet("color: red;")
        self.pwards_test_btn.setEnabled(True)

    def update_gpu_warning(self, checked):
        self.gpu_warning.setVisible(checked)

    def _on_resample_method_changed(self, index):
        """Handle resampling method combobox change."""
        resample_map = {
            0: "none",
            1: "nearest",
            2: "ewa",
            3: "bilinear",
            4: "gradient_search"
        }
        resample_value = resample_map.get(index, "nearest")
        self.settings.set("resampling_method", resample_value)
        # Log the change for debugging
        if hasattr(self, 'parent') and self.parent() and hasattr(self.parent(), 'log'):
            self.parent().log(f"Resampling method changed to: {resample_value}")

    def load_full_resolution(self):
        skip_warning = self.settings.get("fullres_warning_skip", False)
        if not skip_warning:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Full Resolution Warning")
            msg.setText("Loading the full resolution image may cause the application to lag or crash.\n\nProceed?")
            dont_ask = QCheckBox("Don't show this warning again")
            msg.setCheckBox(dont_ask)
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            if msg.exec() != QMessageBox.Yes:
                return
            if dont_ask.isChecked():
                self.settings.set("fullres_warning_skip", True)
        if self.parent():
            self.parent().load_full_resolution_image()
        QMessageBox.information(self, "Full Resolution", "Full resolution image loaded.")

    def _update_preview(self, name):
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

    def _on_fcst_layout_changed(self, idx):
        layout_keys = ["monwatch", "pagasa", "pwards", "jtwc", "jma", "nhc", "develope"]
        if idx >= 0 and idx < len(layout_keys):
            layout_val = layout_keys[idx]
            fcst_prefs = self.settings.get("forecast_preferences", {}).copy()
            fcst_prefs["forecast_layout"] = layout_val
            self.settings.settings["forecast_preferences"] = fcst_prefs
            self.settings.save_immediate()
            log.info(f"[Settings] Immediate save Forecast Layout: {layout_val}")

    def _apply(self, prev=None):
        if prev is None:
            prev = {k: self.settings.settings.get(k) for k in _APPLY_OVERLAY_SNAP_KEYS}
        self.settings.set("theme_name", self.theme_combo.currentText())
        self.settings.set("grid_color", self.grid_color_btn.color())
        self.settings.set("grid_opacity", self.grid_opacity_spin.value())
        self.settings.set("grid_line_width", self.grid_width_spin.value())
        self.settings.set("grid_spacing_deg", self.grid_spacing_spin.value())
        self.settings.set("grid_pattern", self.grid_pattern_combo.currentText())
        if hasattr(self, 'cartopy_grid_cb'):
            self.settings.set("cartopy_grid_in_exports", self.cartopy_grid_cb.isChecked())
        if self.viewport_bg_use_default_cb.isChecked():
            t = THEMES.get(self.theme_combo.currentText(), THEMES["Dark (Default)"])
            self.settings.set("viewport_bg", t.get("viewport_bg", "#000000"))
        else:
            self.settings.set("viewport_bg", self.viewport_bg_btn.color())
        self.settings.set("viewport_bg_use_theme_default", self.viewport_bg_use_default_cb.isChecked())
        self.settings.set("coast_color", self.coast_color_btn.color())
        self.settings.set("coast_opacity", self.coast_opacity_spin.value())
        self.settings.set("coast_line_width", self.coast_width_spin.value())
        self.settings.set("coast_pattern", self.coast_pattern_combo.currentText())
        _coast_res_rev = {0: "Low", 1: "Medium", 2: "High", 3: "Full"}
        self.settings.set("coastline_resolution", _coast_res_rev.get(self.coast_resolution_combo.currentIndex(), "Full"))
        self.settings.set("aor_pattern", self.aor_pattern_combo.currentText())
        if hasattr(self, 'overlay_mode_combo'):
            overlay_mode = "cached" if self.overlay_mode_combo.currentIndex() == 0 else "realtime"
            self.settings.set("overlay_mode", overlay_mode)
        if hasattr(self, 'labeling_method_combo'):
            idx = self.labeling_method_combo.currentIndex()
            _methods = ["polar", "bezier", "smart_bezier", "anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "railway_bezier", "8direction", "staggered_perp", "auto"]
            labeling_method = _methods[idx] if 0 <= idx < len(_methods) else "polar"
            self.settings.set("labeling_method", labeling_method)
        if hasattr(self, 'cone_combo'):
            cone_method = "smooth" if self.cone_combo.currentIndex() == 0 else "union"
            self.settings.set("cone_method", cone_method)
        for key, btn in getattr(self, '_aor_color_btns', {}).items():
            self.settings.set(key, btn.color())
        self.settings.set("animation_controls_on_viewport", self.anim_controls_cb.isChecked())
        self.settings.set("amv_on_clouds", self.amv_on_clouds_cb.isChecked())
        if hasattr(self, 'visualizer_combo'):
            self.settings.set("visualizer", self.visualizer_combo.currentText())
        if hasattr(self, 'gpu_render_combo'):
            self.settings.set("use_gpu_rendering", self.gpu_render_combo.currentData())
        if hasattr(self, 'dl_path_edit'):
            self.settings.set("paths", {
                "download_folder": self.dl_path_edit.text().strip(),
                "export_folder": self.ex_path_edit.text().strip(),
                "plugins_folder": self.pl_path_edit.text().strip(),
            })
        if self.parent():
            cur = self.settings.settings
            theme_changed = any(prev.get(k) != cur.get(k) for k in _APPLY_THEME_KEYS)
            grid_changed = any(prev.get(k) != cur.get(k) for k in _APPLY_GRID_KEYS)
            coast_changed = any(prev.get(k) != cur.get(k) for k in _APPLY_COAST_KEYS)
            overlay_changed = theme_changed or grid_changed or coast_changed or any(
                prev.get(k) != cur.get(k) for k in ("wind_density", "amv_on_clouds", "viewport_mode", "visualizer"))
            if theme_changed:
                self.parent().apply_theme()
            if grid_changed:
                self.parent()._last_grid_cache_key = None
            if coast_changed:
                self.parent()._last_coast_cache_key = None
            if overlay_changed:
                self.parent()._last_overlay_key = None
            self.parent().update_overlays()
            self.parent().apply_track_info_position()
            if hasattr(self, 'dl_path_edit'):
                paths = self.settings.get("paths", {})
                self.parent().input_dir = paths.get("download_folder", "").strip() or str(
                    Path(__file__).resolve().parent.parent.parent / 'data' / 'Download')
            if hasattr(self, 'weather_alerts_enabled_cb'):
                self.settings.set("weather_alerts_enabled", self.weather_alerts_enabled_cb.isChecked())
                self.settings.set("nws_enabled", self.nws_enabled_cb.isChecked())
                self.settings.set("alert_poll_interval_min", self.alert_interval_spin.value())
                self.settings.set("alert_marine_only", self.alert_marine_cb.isChecked())
                self.settings.set("alert_zone", self.alert_zone_edit.text().strip())
                self.settings.set("pagasa_enabled", self.pagasa_enabled_cb.isChecked())
                self.settings.set("pagasa_poll_interval_min", self.pagasa_interval_spin.value())
                self.settings.set("alert_sound_modern_notif", self.alert_modern_notif_cb.isChecked())
                self.settings.set("alert_sound_soft_emer", self.alert_soft_emer_cb.isChecked())
                self.settings.set("alert_retention_days", self.alert_retention_spin.value())
            if hasattr(self, 'weather_alerts_enabled_cb') and self.parent() and hasattr(self.parent(), '_apply_alert_settings'):
                self.parent()._apply_alert_settings()
                if hasattr(self.parent(), 'alert_controller'):
                    self.parent().alert_controller._cleanup_expired_alerts()
        if hasattr(self, 'visualizer_combo') and self.parent() and prev.get("visualizer") != self.settings.settings.get("visualizer"):
            self.parent().apply_visualizer()
        self.settings.save_immediate()

    def _save_and_close(self):
        prev = {k: self.settings.settings.get(k) for k in _APPLY_OVERLAY_SNAP_KEYS}
        prev_gpu = self.settings.get("gpu_acceleration", False)
        try:
            self.settings.set("gpu_acceleration", self.gpu_cb.isChecked())
            self.settings.set("max_threads", self.threads_combo.currentData())
            self.settings.set("cache_size_mb", self.cache_size_spin.value())
            self.settings.set("render_quality", QUALITY_VALUES[self.quality_combo.currentIndex()])
            self.settings.set("texture_cache_size_mb", self.tex_cache_spin.value())
            self.settings.set("bg_compositing", self.bg_composite_cb.isChecked())
            self.settings.set("precache_bands", self.precache_cb.isChecked())
            self.settings.set("dual_thread_cache", self.dual_thread_cache_cb.isChecked())
            self.settings.set("auto_composite_on_cache", self.auto_composite_cb.isChecked())
            self.settings.set("lazy_nc", self.lazy_nc_cb.isChecked())
            self.settings.set("high_res_amv", self.high_res_amv_cb.isChecked())
            self.settings.set("auto_fullres_prompt", self.auto_fullres_cb.isChecked())
            interp_map = {0: "Nearest", 1: "Bilinear", 2: "Bicubic"}
            self.settings.set("zoom_interpolation", interp_map[self.zoom_interp_combo.currentIndex()])
            self.settings.set("grid_coast_render_mode", "performance" if self.grid_coast_mode_combo.currentText() == "Performance (QImage)" else "quality")
            self.settings.set("grid_sub_step_tenths", self.grid_sub_spin.value())
            self.settings.set("wind_density", self.wind_density_combo.currentData() or "Normal")
            self.settings.set("gridded_winds", self.gridded_winds_cb.isChecked())
            self.settings.set("info_box_name", self.ib_name_edit.text().strip() or "PAGASA")
            self.settings.set("info_box_position", self.ib_pos_combo.currentText())
            self.settings.set("track_info_position", self.track_info_pos_combo.currentText().lower().replace(" ", "_"))
            self.settings.set("footer_size", self.footer_size_combo.currentText() if hasattr(self, 'footer_size_combo') else "small")
            self.settings.set("text_size", self.text_size_combo.currentText() if hasattr(self, 'text_size_combo') else "normal")
            if hasattr(self, 'footer_cb_sat'):
                self.settings.set("footer_show", {
                    "satellite": self.footer_cb_sat.isChecked(),
                    "datetime": self.footer_cb_dt.isChecked(),
                    "band": self.footer_cb_band.isChecked(),
                    "latlon": self.footer_cb_latlon.isChecked(),
                })
            if hasattr(self, 'footer_pos_combo'):
                self.settings.set("footer_position", self.footer_pos_combo.currentText())
            if hasattr(self, 'footer_infopos_combo'):
                self.settings.set("footer_info_position", self.footer_infopos_combo.currentText())
            if hasattr(self, 'startup_logo_rb'):
                self.settings.set("startup_mode", "logo" if self.startup_logo_rb.isChecked() else "himawari")
            if hasattr(self, 'viewport_mode_combo'):
                self.settings.set("viewport_mode", "beta" if self.viewport_mode_combo.currentText() == "Beta" else "legacy")
            if hasattr(self, 'visualizer_combo'):
                self.settings.set("visualizer", self.visualizer_combo.currentText())
            if hasattr(self, 'gpu_render_combo'):
                self.settings.set("use_gpu_rendering", self.gpu_render_combo.currentData())
            self.settings.set("track_bulletin_x", None)
            self.settings.set("track_bulletin_y", None)
            self.settings.set("force_full_cache", self.force_full_cache_cb.isChecked())
            self.settings.set("mouse_throttle_ms", self.mouse_throttle_spin.value())
            self.settings.set("gpu_memory_target_mb", self.gpu_mem_target.value())
            self.settings.set("windy_api_key", self.windy_api_edit.text().strip())
            self.settings.set("metra_api_key", self.metra_api_edit.text().strip())
            self.settings.set("cwa_api_key", self.cwa_api_edit.text().strip())
            self.settings.set("pwards_api", {
                "base_url": self.pwards_url_edit.text().strip(),
                "api_code": self.pwards_code_edit.text().strip(),
                "poll_interval_sec": self.pwards_interval_spin.value(),
                "enabled": self.pwards_enabled_btn.isChecked(),
            })
            self.settings.set("weather_alerts_enabled", self.weather_alerts_enabled_cb.isChecked())
            self.settings.set("nws_enabled", self.nws_enabled_cb.isChecked())
            self.settings.set("alert_poll_interval_min", self.alert_interval_spin.value())
            self.settings.set("alert_marine_only", self.alert_marine_cb.isChecked())
            self.settings.set("alert_zone", self.alert_zone_edit.text().strip())
            self.settings.set("pagasa_enabled", self.pagasa_enabled_cb.isChecked())
            self.settings.set("pagasa_poll_interval_min", self.pagasa_interval_spin.value())
            self.settings.set("alert_sound_modern_notif", self.alert_modern_notif_cb.isChecked())
            self.settings.set("alert_sound_soft_emer", self.alert_soft_emer_cb.isChecked())
            self.settings.set("adaptive_quality", self.adaptive_quality_cb.isChecked())
            self.settings.set("modern_contour", self.modern_contour_cb.isChecked())
            self.settings.set("async_overlay_rendering", self.async_overlays_cb.isChecked())
            self.settings.set("numba_jit", self.numba_jit_cb.isChecked())
            lod_map = {-1.0: 0, 0.0: 1, 0.5: 2, 1.0: 3}
            inv_lod = {v: k for k, v in lod_map.items()}
            self.settings.set("lod_bias", inv_lod.get(self.lod_bias_combo.currentIndex(), 0.0))
            self.settings.set("overlay_update_throttle_ms", self.overlay_throttle_spin.value())
            self.settings.set("animation_frame_skip", self.anim_skip_spin.value())
            overlay_mode = "cached" if self.overlay_mode_combo.currentIndex() == 0 else "realtime"
            self.settings.set("overlay_mode", overlay_mode)
            idx = self.labeling_method_combo.currentIndex()
            _methods = ["polar", "bezier", "smart_bezier", "anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "railway_bezier", "8direction", "staggered_perp", "auto"]
            labeling_method = _methods[idx] if 0 <= idx < len(_methods) else "polar"
            self.settings.set("labeling_method", labeling_method)
            cone_method = "smooth" if self.cone_combo.currentIndex() == 0 else "union"
            self.settings.set("cone_method", cone_method)
            self.settings.set("paths", {
                "download_folder": self.dl_path_edit.text().strip(),
                "export_folder": self.ex_path_edit.text().strip(),
                "plugins_folder": self.pl_path_edit.text().strip(),
            })
            if self.parent():
                paths = self.settings.get("paths", {})
                self.parent().input_dir = paths.get("download_folder", "").strip() or str(
                    Path(__file__).resolve().parent.parent.parent / 'data' / 'Download')
            if hasattr(self, 'old_tracks_combo'):
                self.settings.set("old_tracks", self.old_tracks_combo.currentText())
            # Save forecast preferences
            if hasattr(self, 'fcst_layout_combo'):
                utc_val = int(self.fcst_utc_combo.currentText()) if hasattr(self, 'fcst_utc_combo') else 0
                layout_keys = ["monwatch", "pagasa", "pwards", "jtwc", "jma", "nhc", "develope"]
                layout_val = layout_keys[self.fcst_layout_combo.currentIndex()] if self.fcst_layout_combo.currentIndex() < len(layout_keys) else "monwatch"
                legend_keys = ["top_right", "top_left", "bottom_left", "bottom_right"]
                legend_val = legend_keys[self.fcst_legend_pos_combo.currentIndex()] if hasattr(self, 'fcst_legend_pos_combo') and self.fcst_legend_pos_combo.currentIndex() < len(legend_keys) else "bottom_left"
                
                fcst_prefs = {
                    "show_date_time": self.fcst_dt_cb.isChecked() if hasattr(self, 'fcst_dt_cb') else True,
                    "time_format": "military" if (hasattr(self, 'fcst_time_fmt_combo') and self.fcst_time_fmt_combo.currentIndex() == 0) else "civilian",
                    "utc_offset": utc_val,
                    "show_wind_speed": self.fcst_wind_cb.isChecked() if hasattr(self, 'fcst_wind_cb') else True,
                    "wind_format": self.fcst_wind_fmt_combo.currentText() if hasattr(self, 'fcst_wind_fmt_combo') else "kt",
                    "forecast_layout": layout_val,
                    "legend_position": legend_val,
                }
                log.info(f"[Settings] Saving Forecast Layout: {layout_val} (Index: {self.fcst_layout_combo.currentIndex()})")
                log.info(f"[Settings] Saving full Forecast Preferences: {fcst_prefs}")
                self.settings.settings["forecast_preferences"] = fcst_prefs
        except Exception as e:
            log.error(f"[Settings] Error during save: {e}")
            log.exception(e)

        try:
            if hasattr(self, '_shortcuts_edits'):
                shortcuts = {}
                for key, edit in self._shortcuts_edits.items():
                    seq = edit.keySequence().toString()
                    if seq:
                        shortcuts[key] = seq
                self.settings.set("shortcuts", shortcuts)
        except Exception as e:
            log.error(f"[Settings] Error saving shortcuts: {e}")
        try:
            self._apply(prev)
        except Exception as e:
            log.error(f"[Settings] Error applying settings on close: {e}")
            log.exception(e)
        try:
            if hasattr(self, '_shortcuts_edits') and self.parent() and hasattr(self.parent(), '_rebuild_tab_shortcuts'):
                self.parent()._rebuild_tab_shortcuts()
            if self.parent():
                if prev_gpu != self.settings.get("gpu_acceleration", False):
                    self.parent().apply_gpu_acceleration()
                new_q = QUALITY_VALUES[self.quality_combo.currentIndex()]
                if getattr(self.parent(), 'preview_quality', None) != new_q:
                    self.parent().apply_render_quality()
                self.parent().apply_zoom_interpolation()
                self.parent().apply_texture_cache_size()
                self.parent().enforce_cache_size_limit()
                self.parent().apply_mouse_throttle()
                self.parent().apply_adaptive_settings()
                if hasattr(self.parent(), '_apply_alert_settings'):
                    self.parent()._apply_alert_settings()
                if hasattr(self.parent(), '_on_pwards_config_changed'):
                    self.parent()._on_pwards_config_changed()
        except Exception as e:
            log.error(f"[Settings] Error updating UI after save: {e}")
            log.exception(e)
        try:
            self.settings.save_immediate()
        except Exception as e:
            log.error(f"[Settings] Final save failed: {e}")
