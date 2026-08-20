# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: core/helpers.py
# Description: Utility functions and helper methods for satellite data processing and file operations.
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
import shutil
import importlib.util
import subprocess
import tempfile
import zipfile
import requests
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
from PySide6.QtGui import QColor

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

HAS_GEO = True

if getattr(sys, 'frozen', False):
    top_dir = Path(sys.executable).resolve().parent
    src_dir = top_dir
else:
    top_dir = Path(__file__).resolve().parent.parent.parent
    src_dir = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(src_dir))


def normalize_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


if HAS_NUMBA:
    @njit(cache=True, nogil=True)
    def _point_in_polygon_numba(lon: float, lat: float, polygon: list) -> bool:
        n = len(polygon)
        inside = False
        p1x, p1y = polygon[0]
        for i in range(n):
            p2x, p2y = polygon[i % n]
            if lat > min(p1y, p2y):
                if lat <= max(p1y, p2y):
                    if lon <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or lon <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside


def _point_in_polygon(lon: float, lat: float, polygon: list[tuple[float, float]]) -> bool:
    if not polygon or len(polygon) < 3:
        return False
    x = normalize_lon(lon)
    if HAS_NUMBA:
        return _point_in_polygon_numba(x, lat, polygon)
    n = len(polygon)
    inside = False
    p1x, p1y = normalize_lon(polygon[0][0]), polygon[0][1]
    for i in range(n + 1):
        p2x, p2y = normalize_lon(polygon[i % n][0]), polygon[i % n][1]
        if lat > min(p1y, p2y):
            if lat <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xinters = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xinters:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def _densify_polygon(polygon: list[tuple[float, float]], steps: int = 12) -> list[tuple[float, float]]:
    if not polygon or len(polygon) < 2 or steps < 1:
        return polygon[:]
    dense = []
    n = len(polygon)
    for i in range(n):
        p1 = polygon[i]
        p2 = polygon[(i + 1) % n]
        dense.append(p1)
        for s in range(1, steps):
            t = s / steps
            lon = p1[0] + (p2[0] - p1[0]) * t
            lat = p1[1] + (p2[1] - p1[1]) * t
            dense.append((lon, lat))
    return dense


def _get_nc_glob_pattern(sat: str) -> str:
    s = (sat or "").lower()
    if "goes" in s:
        return "*_ABI*.nc"
    if "gk2a" in s or "gk-2a" in s:
        return "gk2a_*.nc"
    if "meteosat" in s or "msg" in s:
        return "*.nc"
    return "*_B??_*.nc"


def _load_cache_manager():
    if getattr(sys, 'frozen', False):
        cache_path = top_dir / 'Process' / 'caching' / 'maker.py'
    else:
        cache_path = top_dir / 'Process' / 'caching' / 'maker.py'
    spec = importlib.util.spec_from_file_location('caching.maker', cache_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CacheManager


CacheManager = _load_cache_manager()

COASTLINE_URLS = [
    "https://naciscdn.org/naturalearth/10m/physical/ne_10m_coastline.zip",
    "https://github.com/nvkelso/natural-earth-vector/raw/master/110m_physical/ne_110m_coastline.zip"
]
BORDERS_URLS = [
    "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip",
    "https://github.com/nvkelso/natural-earth-vector/raw/master/50m_cultural/ne_50m_admin_0_countries.zip",
]
COASTLINE_DIR = top_dir / "data" / "coastlines"
COASTLINE_SHP = COASTLINE_DIR / "ne_10m_coastline.shp"
BORDERS_SHP = COASTLINE_DIR / "ne_50m_admin_0_countries.shp"


def _download_and_extract(url, target_dir, expect_file):
    print(f"Downloading {url}...")
    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        for chunk in response.iter_content(chunk_size=8192):
            tmp.write(chunk)
        tmp_path = tmp.name
    try:
        with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
            zip_ref.extractall(target_dir)
    finally:
        os.unlink(tmp_path)
    return expect_file.exists()


def ensure_coastline_data():
    """Ensure coastline data is available.

    Mirrors SIFT: the coastline is drawn as part of the borders layer, i.e. it is
    simply the edge of country boundary polygons. The Natural Earth 1:50m admin-0
    country polygons (BORDERS_SHP) are therefore the primary source, with the
    dedicated 10m coastline dataset kept as a fallback.
    """
    if not HAS_GEO:
        return False
    COASTLINE_DIR.mkdir(parents=True, exist_ok=True)
    if BORDERS_SHP.exists():
        return True
    for url in BORDERS_URLS:
        try:
            if _download_and_extract(url, COASTLINE_DIR, BORDERS_SHP):
                print("Borders data ready (admin-0 country polygons).")
                return True
        except Exception as e:
            print(f"Borders download failed ({url}): {e}")
            continue
    if COASTLINE_SHP.exists():
        return True
    for url in COASTLINE_URLS:
        try:
            if _download_and_extract(url, COASTLINE_DIR, COASTLINE_SHP):
                print("Coastline data ready.")
                return True
        except Exception as e:
            print(f"Failed with {url}: {e}")
            continue
    return False


def load_border_segments(shp_path=None, stride=1, double=False):
    """Load country-boundary outline segments SIFT-style.

    Mirrors SIFT's ShapefileLinesVisual: every shape/part is read and each part's
    vertex chain is packed into line segments. The coastline is just the edge of
    the admin-0 country polygons, so no separate coastline dataset is needed.
    Latitudes are clipped to +/-89.9 so PROJ/mercator never blows up, and with
    ``double=True`` a +360-degree-longitude duplicate of the vertex buffer is
    appended so coastlines wrap the antimeridian on both sides of the map.

    Args:
        shp_path: explicit shapefile path, else BORDERS_SHP (fallback COASTLINE_SHP)
        stride: keep every Nth point (>=1 keeps all)
        double: append a duplicated vertex buffer offset by +360 degrees longitude

    Returns:
        list of segments; each segment is a list of (lon, lat) tuples
    """
    if shp_path is None:
        if BORDERS_SHP.exists():
            shp_path = BORDERS_SHP
        elif COASTLINE_SHP.exists():
            shp_path = COASTLINE_SHP
        else:
            return []
    shp_path = Path(shp_path)
    if not shp_path.exists():
        return []
    try:
        import shapefile  # pyshp
    except ImportError:
        return []
    segments = []
    stride = max(1, int(stride))
    with shapefile.Reader(str(shp_path)) as sf:
        for shape in sf.shapes():
            points = shape.points
            if not points:
                continue
            parts = list(shape.parts)
            parts.append(len(points))
            for i in range(len(parts) - 1):
                start, end = parts[i], parts[i + 1]
                if start >= end:
                    continue
                run = points[start:end]
                if stride > 1:
                    run = run[::stride]
                    if not run or run[-1] != points[end - 1]:
                        run = list(run) + [points[end - 1]]
                clipped = [(lon, max(-89.9, min(89.9, lat))) for lon, lat in run]
                if len(clipped) >= 2:
                    segments.append(clipped)
    if double and segments:
        segments.extend([[(lon + 360.0, lat) for lon, lat in seg] for seg in segments])
    return segments


# Process-wide cache so the borders shapefile is decoded only once, SIFT-style,
# instead of being re-read (and re-doubled) on every overlay rebuild.
_BORDER_SEGMENTS_NP_CACHE: dict = {}


def load_border_segments_np(shp_path=None, stride=1, double=False):
    """Load country-boundary outline segments into cached numpy arrays.

    SIFT-hardened equivalent of :func:`load_border_segments`: the shapefile is
    parsed once per process (keyed by path/stride/double) and every segment
    part is returned as a ``(N, 2)`` float64 array of ``(lon, lat)`` so the
    caller can project with vectorized pyproj/numpy calls instead of a per-point
    Python loop. Latitudes are clipped to +/-89.9 (PROJ/mercator safety) and
    with ``double=True`` a +360-degree-longitude duplicate of every segment is
    appended so coastlines wrap the antimeridian on both sides of the map.

    Args:
        shp_path: explicit shapefile path, else BORDERS_SHP (fallback COASTLINE_SHP)
        stride: keep every Nth point (>=1 keeps all)
        double: append a duplicated vertex buffer offset by +360 degrees longitude

    Returns:
        list of segments; each segment is a (N, 2) numpy float64 array of (lon, lat)
    """
    if shp_path is None:
        if BORDERS_SHP.exists():
            shp_path = BORDERS_SHP
        elif COASTLINE_SHP.exists():
            shp_path = COASTLINE_SHP
        else:
            return []
    shp_path = Path(shp_path)
    if not shp_path.exists():
        return []
    stride = max(1, int(stride))
    key = (str(shp_path.resolve()), stride, bool(double))
    cached = _BORDER_SEGMENTS_NP_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        import shapefile  # pyshp
    except ImportError:
        return []
    segments = []
    with shapefile.Reader(str(shp_path)) as sf:
        for shape in sf.shapes():
            points = shape.points
            if not points:
                continue
            pts = np.asarray(points, dtype=np.float64)
            parts = list(shape.parts) + [len(points)]
            for i in range(len(parts) - 1):
                start, end = parts[i], parts[i + 1]
                if start >= end:
                    continue
                run = pts[start:end]
                if stride > 1:
                    run = run[::stride]
                    if len(run) and not np.allclose(run[-1], pts[end - 1]):
                        run = np.concatenate([run, pts[end - 1:end]], axis=0)
                if len(run) < 2:
                    continue
                run = run.copy()
                run[:, 1] = np.clip(run[:, 1], -89.9, 89.9)
                segments.append(run)
    if double and segments:
        offset = np.array([360.0, 0.0])
        segments = segments + [seg + offset for seg in segments]
    _BORDER_SEGMENTS_NP_CACHE[key] = segments
    return segments


def decimate_screen_points(pts, min_step_px=1.0):
    """Drop consecutive points closer than ``min_step_px`` along the polyline.

    Operates in screen/pixel space: the cumulative path length of the input is
    sampled once every ``min_step_px`` (endpoints always kept), so a dense run
    of sub-pixel vertices collapses to about one point per pixel of drawn line.
    This keeps a decimated QPainterPath tiny enough for smooth pan/zoom while
    remaining visibly identical at the baked resolution.

    Args:
        pts: array-like of shape (N, 2) of ``(x, y)`` pixel coordinates.
        min_step_px: minimum separation, in pixels, to retain a point.

    Returns:
        numpy (M, 2) array of the kept points (M <= N).
    """
    arr = np.asarray(pts, dtype=np.float64)
    if arr.ndim != 2 or arr.shape[1] < 2 or arr.shape[0] < 3 or min_step_px <= 0:
        return arr
    n = arr.shape[0]
    d = np.sqrt(np.sum((arr[1:] - arr[:-1]) ** 2, axis=1))
    step = float(min_step_px)
    if d.min() >= step:
        return arr
    cum = np.empty(n, dtype=np.float64)
    cum[0] = 0.0
    np.cumsum(d, out=cum[1:])
    total = cum[-1]
    if total <= step:
        return arr[[0, n - 1]]
    n_targets = int(np.floor(total / step))
    if n_targets <= 0:
        return arr[[0, n - 1]]
    n_targets = min(n_targets, n - 1)
    targets = np.linspace(step, total, n_targets)
    idx = np.searchsorted(cum, targets, side="left")
    idx = np.unique(idx)
    keep = np.zeros(n, dtype=bool)
    keep[0] = True
    keep[idx] = True
    keep[-1] = True
    return arr[keep]


THEMES = {
    "Dark (Default)": {
        "bg":       "#1E1E1E", "bg2": "#252525", "bg3": "#2A2A2A",
        "fg":       "#DDD",    "fg2": "#CCC",    "fg3": "#AAA",
        "border":   "#383838", "border2": "#444", "border3": "#333",
        "accent":   "#5D8AA8", "accent2": "#3A3A3A",
        "menusel":  "#333",
        "viewport_bg": "#000000",
    },
    "Light": {
        "bg":       "#F5F5F5", "bg2": "#EBEBEB", "bg3": "#E0E0E0",
        "fg":       "#1A1A1A", "fg2": "#333",    "fg3": "#555",
        "border":   "#C8C8C8", "border2": "#BBB", "border3": "#CCC",
        "accent":   "#1565C0", "accent2": "#D0D0D0",
        "menusel":  "#D8D8D8",
        "viewport_bg": "#000000",
    },
    "Midnight Blue": {
        "bg":       "#0D1B2A", "bg2": "#132232", "bg3": "#182C40",
        "fg":       "#D0E8FF", "fg2": "#A8CCEE", "fg3": "#7AABDD",
        "border":   "#1E3A55", "border2": "#2A4A6A", "border3": "#1A3048",
        "accent":   "#2196F3", "accent2": "#1A3555",
        "menusel":  "#1A3555",
        "viewport_bg": "#000000",
    },
    "Solarized Dark": {
        "bg":       "#002B36", "bg2": "#073642", "bg3": "#0D3D4A",
        "fg":       "#839496", "fg2": "#93A1A1", "fg3": "#657B83",
        "border":   "#073642", "border2": "#586E75", "border3": "#1A4A56",
        "accent":   "#268BD2", "accent2": "#0A4050",
        "menusel":  "#0A4050",
        "viewport_bg": "#000000",
    },
    "High Contrast": {
        "bg":       "#000000", "bg2": "#111111", "bg3": "#1A1A1A",
        "fg":       "#FFFFFF", "fg2": "#EEEEEE", "fg3": "#CCCCCC",
        "border":   "#444444", "border2": "#666666", "border3": "#333333",
        "accent":   "#FFD600", "accent2": "#222222",
        "menusel":  "#333333",
        "viewport_bg": "#000000",
    },
    "Dracula": {
        "bg":       "#282A36", "bg2": "#21222C", "bg3": "#2D2F3F",
        "fg":       "#F8F8F2", "fg2": "#CDD6F4", "fg3": "#6272A4",
        "border":   "#44475A", "border2": "#555770", "border3": "#383A4A",
        "accent":   "#BD93F9", "accent2": "#383A4A",
        "menusel":  "#44475A",
        "viewport_bg": "#000000",
    },
    "Nord": {
        "bg":       "#2E3440", "bg2": "#3B4252", "bg3": "#434C5E",
        "fg":       "#ECEFF4", "fg2": "#D8DEE9", "fg3": "#81A1C1",
        "border":   "#4C566A", "border2": "#5E6779", "border3": "#434C5E",
        "accent":   "#88C0D0", "accent2": "#3B4252",
        "menusel":  "#4C566A",
        "viewport_bg": "#000000",
    },
    "Monokai": {
        "bg":       "#272822", "bg2": "#1E1F1C", "bg3": "#2D2E27",
        "fg":       "#F8F8F2", "fg2": "#CFCFC2", "fg3": "#75715E",
        "border":   "#3E3D32", "border2": "#49483E", "border3": "#3A3A30",
        "accent":   "#A6E22E", "accent2": "#3E3D32",
        "menusel":  "#49483E",
        "viewport_bg": "#000000",
    },
    "Warm Amber": {
        "bg":       "#1C1610", "bg2": "#241E16", "bg3": "#2C261C",
        "fg":       "#F5DEB3", "fg2": "#DEB887", "fg3": "#C8A870",
        "border":   "#3C3020", "border2": "#4A3C28", "border3": "#332A1A",
        "accent":   "#FF8C00", "accent2": "#332A1A",
        "menusel":  "#3C3020",
        "viewport_bg": "#000000",
    },
}


def build_stylesheet(t: dict) -> str:
    return f"""
        QMainWindow {{ background: {t["bg"]}; color: {t["fg"]}; }}
        QDialog {{ background: {t["bg"]}; color: {t["fg"]}; }}
        QWidget {{ background: {t["bg"]}; color: {t["fg"]}; }}
        QMenuBar {{
            background: {t["bg2"]}; color: {t["fg2"]};
            border-bottom: 1px solid {t["border3"]};
            padding: 2px 0; font-size: 10px;
        }}
        QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
        QMenuBar::item:selected {{ background: {t["menusel"]}; color: {t["fg"]}; }}
        QMenu {{
            background: {t["bg2"]}; color: {t["fg2"]};
            border: 1px solid {t["border2"]};
        }}
        QMenu::item {{ padding: 5px 20px 5px 10px; }}
        QMenu::item:selected {{ background: {t["menusel"]}; color: {t["fg"]}; }}
        QSplitter::handle {{ background: {t["border3"]}; }}
        QGroupBox {{
            background: {t["bg"]}; border: 1px solid {t["border"]}; border-radius: 4px;
            margin-top: 1ex; font-weight: bold; font-size: 10px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin; left: 10px;
            padding: 0 4px; color: {t["fg3"]};
        }}
        QTabWidget {{
            background: {t["bg"]};
        }}
        QTabWidget::pane {{
            background: {t["bg"]}; border: 1px solid {t["border3"]};
            border-radius: 4px; border-top-left-radius: 0;
        }}
        QTabBar {{
            background: {t["bg2"]};
        }}
        QTabBar::tab {{
            background: {t["bg2"]}; color: {t["fg3"]};
            border: 1px solid {t["border3"]}; border-bottom: none;
            padding: 6px 16px; font-size: 10px; font-weight: 600;
            border-radius: 4px 4px 0 0; margin-right: 2px;
            letter-spacing: 0.3px;
        }}
        QTabBar::tab:selected {{ background: {t["bg3"]}; color: {t["accent"]}; border-bottom: none; font-weight: 700; }}
        QTabBar::tab:hover:!selected {{ background: {t["bg3"]}; color: {t["fg2"]}; }}
        QScrollArea {{
            background: {t["bg"]}; border: none;
        }}
        QScrollArea > QWidget > QWidget {{
            background: {t["bg"]};
        }}
        QSlider::groove:horizontal {{ height: 4px; background: {t["border3"]}; border-radius: 2px; }}
        QSlider::handle:horizontal {{
            background: {t["accent"]}; width: 14px; margin: -5px 0; border-radius: 7px;
        }}
        QScrollBar:vertical {{ background: {t["bg"]}; width: 8px; margin: 0; }}
        QScrollBar::handle:vertical {{
            background: {t["border2"]}; border-radius: 4px; min-height: 20px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {t["fg3"]}; }}
        QScrollBar:horizontal {{ background: {t["bg"]}; height: 8px; margin: 0; }}
        QScrollBar::handle:horizontal {{
            background: {t["border2"]}; border-radius: 4px; min-width: 20px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {t["fg3"]}; }}
        QStatusBar {{
            background: {t["bg2"]}; color: {t["fg3"]};
            font-size: 9px; border-top: 1px solid {t["border3"]};
        }}
        QProgressBar {{
            border: 1px solid {t["border2"]}; border-radius: 3px;
            text-align: center; background: {t["bg3"]}; color: {t["fg"]};
        }}
        QProgressBar::chunk {{ background: {t["accent"]}; border-radius: 2px; }}
        QComboBox {{
            border: 1px solid {t["border2"]}; border-radius: 3px; padding: 3px 6px;
            background: {t["bg3"]}; color: {t["fg"]}; min-width: 6em;
        }}
        QComboBox::drop-down {{ border: none; width: 18px; }}
        QComboBox::down-arrow {{
            border-left: 4px solid transparent; border-right: 4px solid transparent;
            border-top: 5px solid {t["fg3"]};
        }}
        QComboBox QAbstractItemView {{
            background: {t["bg3"]}; color: {t["fg"]};
            border: 1px solid {t["border2"]}; selection-background-color: {t["accent2"]};
        }}
        QCheckBox {{ spacing: 6px; color: {t["fg2"]}; font-size: 10px; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 2px; }}
        QCheckBox::indicator:unchecked {{ border: 1px solid {t["fg3"]}; background: {t["bg3"]}; }}
        QCheckBox::indicator:checked {{ border: 1px solid {t["accent"]}; background: {t["accent"]}; }}
        QRadioButton {{ spacing: 6px; color: {t["fg2"]}; font-size: 10px; }}
        QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; }}
        QRadioButton::indicator:unchecked {{ border: 1px solid {t["fg3"]}; background: {t["bg3"]}; }}
        QRadioButton::indicator:checked {{ border: 1px solid {t["accent"]}; background: {t["accent"]}; }}
        QLabel {{ color: {t["fg2"]}; background: transparent; }}
        QTextEdit {{ background: {t["bg3"]}; color: {t["fg2"]}; border: 1px solid {t["border3"]}; }}
        QPlainTextEdit {{ background: {t["bg3"]}; color: {t["fg2"]}; border: 1px solid {t["border3"]}; }}
        QProgressDialog {{ background: {t["bg2"]}; color: {t["fg"]}; border: 1px solid {t["border2"]}; }}
        QPushButton {{
            background: {t["bg2"]}; color: {t["fg2"]};
            border: 1px solid {t["border2"]}; border-radius: 3px; padding: 4px 10px;
        }}
        QPushButton:hover {{ background: {t["bg3"]}; border-color: {t["fg3"]}; }}
        QPushButton:pressed {{ background: {t["accent2"]}; }}
        QSpinBox, QDoubleSpinBox {{
            background: {t["bg3"]}; color: {t["fg"]};
            border: 1px solid {t["border2"]}; border-radius: 3px; padding: 2px 4px;
        }}
        QLineEdit {{
            background: {t["bg3"]}; color: {t["fg"]};
            border: 1px solid {t["border2"]}; border-radius: 3px; padding: 2px 4px;
        }}
        QTreeWidget, QTreeView, QListWidget, QListView, QTableWidget, QTableView {{
            background: {t["bg3"]}; color: {t["fg2"]};
            border: 1px solid {t["border3"]};
            outline: none;
        }}
        QTreeWidget::item, QTreeView::item, QListWidget::item, QTableView::item {{
            padding: 2px 4px; color: {t["fg2"]};
        }}
        QTreeWidget::item:selected, QTreeView::item:selected,
        QListWidget::item:selected, QTableView::item:selected {{
            background: {t["accent2"]}; color: {t["fg"]};
        }}
        QTreeWidget::item:hover, QTreeView::item:hover, QListWidget::item:hover {{
            background: {t["menusel"]};
        }}
        QHeaderView::section {{
            background: {t["bg2"]}; color: {t["fg2"]};
            border: 1px solid {t["border3"]}; padding: 3px 6px;
        }}
    """


def _get_tracks_folder():
    tracks_folder = top_dir / 'data' / 'tracks'
    tracks_folder.mkdir(parents=True, exist_ok=True)
    return tracks_folder

def _get_archived_tracks_folder():
    af = top_dir / 'data' / 'tracks' / 'archived'
    af.mkdir(parents=True, exist_ok=True)
    return af

def _get_archived_nhc_folder():
    af = top_dir / 'data' / 'nhc_data' / 'archived'
    af.mkdir(parents=True, exist_ok=True)
    return af

def _get_archived_jtwc_folder():
    af = top_dir / 'data' / 'jtwc_data' / 'archived'
    af.mkdir(parents=True, exist_ok=True)
    return af

def _get_archived_jma_folder():
    af = top_dir / 'data' / 'jma_data' / 'archived'
    af.mkdir(parents=True, exist_ok=True)
    return af

def _get_archived_pagasa_folder():
    af = top_dir / 'data' / 'pagasa_data' / 'archived'
    af.mkdir(parents=True, exist_ok=True)
    return af


def _archive_storm_dir(storm_dir, archive_dir, log=None):
    """Move a storm directory into the archive, replacing any previous archive copy.

    Args:
        storm_dir: Path of the active storm directory to archive.
        archive_dir: Path of the agency's archive folder (created if missing).
        log: Optional callable(str) for logging failures (e.g. main_ui.log).

    Returns:
        bool: True if the storm directory was archived.
    """
    if not storm_dir.exists():
        return False
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / storm_dir.name
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(storm_dir), str(dest))
        return True
    except Exception as e:
        if log is not None:
            try:
                log(f"Archive error for {storm_dir.name}: {e}")
            except Exception:
                pass
        return False


def _is_permanently_archived(storm_id, agency='pagasa'):
    """Check if a storm is marked as permanently archived.
    
    Args:
        storm_id: The storm identifier (e.g., 'henry', 'ep012026')
        agency: The agency ('pagasa', 'nhc', 'jma', 'jtwc')
    
    Returns:
        bool: True if the storm is permanently archived
    """
    archive_funcs = {
        'pagasa': _get_archived_pagasa_folder,
        'nhc': _get_archived_nhc_folder,
        'jma': _get_archived_jma_folder,
        'jtwc': _get_archived_jtwc_folder,
    }
    get_folder = archive_funcs.get(agency.lower())
    if not get_folder:
        return False
    
    archive_dir = get_folder()
    storm_dir = archive_dir / storm_id.lower()
    marker_file = storm_dir / '.permanently_archived'
    
    return marker_file.exists()


def _mark_permanently_archived(storm_id, agency='pagasa', reason='expired'):
    """Mark a storm as permanently archived.
    
    Args:
        storm_id: The storm identifier
        agency: The agency ('pagasa', 'nhc', 'jma', 'jtwc')
        reason: Reason for permanent archive ('expired', 'dissipated', 'merged', etc.)
    """
    archive_funcs = {
        'pagasa': _get_archived_pagasa_folder,
        'nhc': _get_archived_nhc_folder,
        'jma': _get_archived_jma_folder,
        'jtwc': _get_archived_jtwc_folder,
    }
    get_folder = archive_funcs.get(agency.lower())
    if not get_folder:
        return
    
    archive_dir = get_folder()
    storm_dir = archive_dir / storm_id.lower()
    storm_dir.mkdir(parents=True, exist_ok=True)
    
    marker_file = storm_dir / '.permanently_archived'
    marker_content = {
        'storm_id': storm_id,
        'agency': agency,
        'reason': reason,
        'archived_at': datetime.now(timezone.utc).isoformat(),
        'note': 'This storm is permanently archived. Re-downloads will go to archive unless genuinely new data.'
    }
    
    try:
        with open(marker_file, 'w') as f:
            json.dump(marker_content, f, indent=2)
    except Exception:
        marker_file.touch()


def _unmark_permanently_archived(storm_id, agency='pagasa'):
    """Remove the permanent archive marker from a storm.
    
    Args:
        storm_id: The storm identifier
        agency: The agency ('pagasa', 'nhc', 'jma', 'jtwc')
    
    Returns:
        bool: True if marker was removed, False if not found
    """
    archive_funcs = {
        'pagasa': _get_archived_pagasa_folder,
        'nhc': _get_archived_nhc_folder,
        'jma': _get_archived_jma_folder,
        'jtwc': _get_archived_jtwc_folder,
    }
    get_folder = archive_funcs.get(agency.lower())
    if not get_folder:
        return False
    
    archive_dir = get_folder()
    storm_dir = archive_dir / storm_id.lower()
    marker_file = storm_dir / '.permanently_archived'
    
    if marker_file.exists():
        try:
            marker_file.unlink()
            return True
        except Exception:
            return False
    return False


def _get_permanent_archive_info(storm_id, agency='pagasa'):
    """Get information about a permanently archived storm.
    
    Args:
        storm_id: The storm identifier
        agency: The agency ('pagasa', 'nhc', 'jma', 'jtwc')
    
    Returns:
        dict or None: Archive info dict if marker exists, None otherwise
    """
    archive_funcs = {
        'pagasa': _get_archived_pagasa_folder,
        'nhc': _get_archived_nhc_folder,
        'jma': _get_archived_jma_folder,
        'jtwc': _get_archived_jtwc_folder,
    }
    get_folder = archive_funcs.get(agency.lower())
    if not get_folder:
        return None
    
    archive_dir = get_folder()
    storm_dir = archive_dir / storm_id.lower()
    marker_file = storm_dir / '.permanently_archived'
    
    if not marker_file.exists():
        return None
    
    try:
        with open(marker_file, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {'storm_id': storm_id, 'agency': agency}


def _save_tracks_to_disk(tracks):
    try:
        tracks_folder = _get_tracks_folder()
        # Deduplicate by ID: prefer non-placeholder names over numbered placeholders
        best_by_id = {}
        for t in tracks:
            tid = t.get("id")
            if tid is None:
                continue
            if tid in best_by_id:
                existing_name = best_by_id[tid].get("name", "").replace(" NHC Forecast", "")
                new_name = t.get("name", "").replace(" NHC Forecast", "")
                if _is_placeholder_name(existing_name) and not _is_placeholder_name(new_name):
                    best_by_id[tid] = t
            else:
                best_by_id[tid] = t
        tracks = list(best_by_id.values())
        current_ids = set()
        for track in tracks:
            track_id = track.get("id", str(uuid.uuid4())[:8])
            track_name = track.get("name", "Track").replace("/", "-").replace("\\", "-")
            filename = f"{track_id}_{track_name}.json"
            filepath = tracks_folder / filename
            current_ids.add(track_id)
            out = dict(track)
            out["main_tree"] = out.get("visible", True)
            dopts = out.get("display_options", {})
            out["sub_tree"] = {
                "track_line": dopts.get("show_track_line", True),
                "points": dopts.get("show_points", True),
                "cone": dopts.get("show_cone", True),
                "wind_radii": dopts.get("show_wind_radii", True),
                "best_track": dopts.get("show_best_track", True),
                "hist_path": dopts.get("show_hist_path", True),
                "swa": dopts.get("show_swa", True),
                "prob_circle": dopts.get("show_prob_circle", True),
            }
            out["label_tree"] = {
                "name": dopts.get("show_label_name", True),
                "time": dopts.get("show_label_time", True),
                "speed": dopts.get("show_label_speed", True),
                "category": dopts.get("show_label_category", True),
            }
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(out, f, indent=2, default=str)
        # Remove orphaned files: those whose track ID is not in current set
        # AND files whose ID+name combo doesn't match any current track
        current_names = {t.get("id"): t.get("name", "") for t in tracks}
        for json_file in list(tracks_folder.glob("*.json")):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                fid = data.get("id")
                fname = data.get("name", "")
                if fid not in current_ids:
                    json_file.unlink()
                elif current_names.get(fid) != fname:
                    json_file.unlink()
            except Exception:
                json_file.unlink()
    except Exception as e:
        print(f"Error saving tracks: {e}")


def _is_placeholder_name(name):
    """Check if a storm name is a numbered NHC placeholder (e.g. 'Two-E', 'One', 'Three')."""
    number_words = {
        "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
        "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
        "eighteen", "nineteen", "twenty"
    }
    base = name.strip().lower().rstrip("-ec")
    return base in number_words


def _load_tracks_from_disk():
    try:
        tracks_folder = _get_tracks_folder()
        tracks = []
        for json_file in tracks_folder.glob("*.json"):
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    track = json.load(f)
                if "main_tree" in track:
                    track["visible"] = track["main_tree"]
                if "sub_tree" in track:
                    dopts = track.setdefault("display_options", {})
                    st = track["sub_tree"]
                    dopts["show_track_line"] = st.get("track_line", True)
                    dopts["show_points"] = st.get("points", True)
                    if "cone" in st:
                        dopts["show_cone"] = st["cone"]
                    if "wind_radii" in st:
                        dopts["show_wind_radii"] = st["wind_radii"]
                    if "best_track" in st:
                        dopts["show_best_track"] = st["best_track"]
                    if "hist_path" in st:
                        dopts["show_hist_path"] = st["hist_path"]
                    if "swa" in st:
                        dopts["show_swa"] = st["swa"]
                    if "prob_circle" in st:
                        dopts["show_prob_circle"] = st["prob_circle"]
                if "label_tree" in track:
                    dopts = track.setdefault("display_options", {})
                    lt = track["label_tree"]
                    dopts["show_label_name"] = lt.get("name", True)
                    dopts["show_label_time"] = lt.get("time", True)
                    dopts["show_label_speed"] = lt.get("speed", True)
                    dopts["show_label_category"] = lt.get("category", True)
                tracks.append(track)
            except Exception as e:
                print(f"Error loading track {json_file}: {e}")
        # Deduplicate by ID: if same ID appears multiple times,
        # keep the one with a real name (not a numbered placeholder)
        seen = {}
        for t in tracks:
            tid = t.get("id")
            if tid is None:
                continue
            if tid in seen:
                existing_name = seen[tid].get("name", "").replace(" NHC Forecast", "")
                new_name = t.get("name", "").replace(" NHC Forecast", "")
                if _is_placeholder_name(existing_name) and not _is_placeholder_name(new_name):
                    seen[tid] = t
            else:
                seen[tid] = t
        return list(seen.values())
    except Exception as e:
        print(f"Error loading tracks: {e}")
        return []
