# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: exporters/geotiff_exporter.py
# Description: GeoTIFF export utilities — RGBA raster writing with full
# georeferencing (CRS + geotransform) and rich TIFF metadata tags including
# satellite/band/product, acquisition time, and lat/lon bounds.
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

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.enums import ColorInterp
from rasterio.transform import Affine

_SOFTWARE = "MonWatch-UI Cyclone V3"

_TAG_PREFIX = "MONWATCH_"


def display_geotransform(gt: Affine | None, img_w: int):
    """Map a native-grid Affine onto a displayed image width.

    The viewport shows the satellite grid downscaled to an arbitrary display
    width. This mirrors the exact scale factor used by the footer lat/lon
    annotation so a GeoTIFF of the displayed image stays pixel-accurate.

    Args:
        gt: Native geotransform (rasterio Affine).
        img_w: Displayed image width (pixels) the transform should cover.

    Returns:
        tuple (Affine, float): geotransform in display-pixel space and the
        native-pixels-per-display-pixel scale factor.
    """
    if gt is None or img_w <= 0:
        return gt, 1.0
    native_res_m = abs(gt.a)
    native_extent = abs(gt.c)
    if native_res_m <= 0 or native_extent <= 0:
        return gt, 1.0
    native_grid_w = int(round(2.0 * native_extent / native_res_m))
    if native_grid_w <= 0:
        return gt, 1.0
    scale = native_grid_w / float(img_w)
    if abs(scale - 1.0) < 1e-12:
        return gt, 1.0
    return (
        Affine(gt.a * scale, gt.b * scale, gt.c,
               gt.d * scale, gt.e * scale, gt.f),
        scale,
    )


def translated_geotransform(gt: Affine | None, dx_pix: float, dy_pix: float) -> Affine | None:
    """Translate a geotransform so pixel (dx_pix, dy_pix) becomes the origin."""
    if gt is None:
        return None
    return Affine(gt.a, gt.b, gt.c + gt.a * dx_pix,
                  gt.d, gt.e, gt.f + gt.e * dy_pix)


def corner_bounds_4326(crs, transform: Affine | None, width: int, height: int):
    """Project the four image corners to EPSG:4326 lat/lon.

    Args:
        crs: Source CRS (pyproj CRS or compatible).
        transform: Source geotransform.
        width, height: Image dimensions in pixels.

    Returns:
        tuple (min_lon, max_lon, min_lat, max_lat) or None if unavailable.
    """
    if crs is None or transform is None:
        return None
    try:
        from pyproj import Transformer
        transformer = Transformer.from_crs(CRS.from_user_input(crs), "EPSG:4326",
                                           always_xy=True)
    except Exception:
        return None
    lons, lats = [], []
    for col, row in ((0, 0), (width, 0), (width, height), (0, height)):
        try:
            px, py = transform * (col, row)
            lon, lat = transformer.transform(px, py)
        except Exception:
            continue
        if np.isfinite(lon) and np.isfinite(lat):
            lons.append(float(lon))
            lats.append(float(lat))
    if not lons:
        return None
    return min(lons), max(lons), min(lats), max(lats)


def _format_datetime(dt_str: str) -> str:
    """Normalize MonWatch datetime strings to ISO-8601 UTC.

    Handles every datetime layout used by the UI: ``YYYY_MM_DD_HHMM``,
    ``YYYYMMDD_HHMM``, ``YYYY_MM_DD`` and ``YYYYMMDD`` (with ``-`` also
    accepted as a separator).
    """
    if not dt_str:
        return ""
    try:
        import re
        s = str(dt_str).strip()
        m = re.fullmatch(r"(\d{4})[-_](\d{2})[-_](\d{2})[-_](\d{2})(\d{2})", s)
        if m:
            y, mo, d, h, mi = m.groups()
            return f"{y}-{mo}-{d}T{h}:{mi}:00Z"
        m = re.fullmatch(r"(\d{8})[-_](\d{2})(\d{2})", s)
        if m:
            y, mo, d = m.group(1)[:4], m.group(1)[4:6], m.group(1)[6:8]
            return f"{y}-{mo}-{d}T{m.group(2)}:{m.group(3)}:00Z"
        m = re.fullmatch(r"(\d{4})[-_](\d{2})[-_](\d{2})", s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T00:00:00Z"
        m = re.fullmatch(r"(\d{8})", s)
        if m:
            return (f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:8]}"
                    f"T00:00:00Z")
        if re.fullmatch(r"(\d{4})[-_](\d{2})[-_](\d{2})[T ](\d{2})[:.]?(\d{2})", s):
            m = re.fullmatch(r"(\d{4})[-_](\d{2})[-_](\d{2})[T ](\d{2})[:.]?(\d{2})", s)
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}T{m.group(4)}:{m.group(5)}:00Z"
        return s.replace("_", "-")
    except Exception:
        return str(dt_str).replace("_", "-")


def build_tags(satellite: str = "", scan_type: str = "", band: str = "",
               product: str = "", dt_str: str = "", crs=None,
               transform: Affine | None = None, width: int = 0,
               height: int = 0, footer_info: str = "") -> dict:
    """Build the full GeoTIFF metadata tag set.

    Everything discoverable about the scene is embedded as TIFF tags
    (readable via rasterio ``src.tags()`` or any GIS tool): satellite,
    band/product, scan type, acquisition time, projection, and the
    geographic bounds + center lat/lon of the raster.

    Args:
        satellite: Satellite name (e.g. ``Himawari-9``).
        scan_type: Scan / observation type label.
        band: Spectral band or RGB product label.
        product: MonWatch product/algorithm key, if any.
        dt_str: Acquisition datetime string from the UI.
        crs: Raster CRS.
        transform: Raster geotransform.
        width, height: Raster dimensions in pixels.
        footer_info: Combined footer line (metadata summary) to embed.

    Returns:
        dict of TIFF tag keys -> string values.
    """
    bounds = corner_bounds_4326(crs, transform, width, height) if transform is not None else None
    resolution = float(abs(transform.a)) if transform is not None else 0.0

    crs_proj4, crs_wkt, crs_epsg = "", "", ""
    crs_name = ""
    if crs is not None:
        try:
            pcrs = CRS.from_user_input(crs)
            crs_proj4 = pcrs.to_proj4()
            crs_wkt = pcrs.to_wkt()
            crs_epsg = str(pcrs.to_epsg() or "")
            crs_name = pcrs.name or ""
        except Exception:
            pass

    tags = {
        f"{_TAG_PREFIX}SATELLITE": (satellite or ""),
        f"{_TAG_PREFIX}SCAN_TYPE": (scan_type or ""),
        f"{_TAG_PREFIX}BAND": (band or ""),
        f"{_TAG_PREFIX}PRODUCT": (product or ""),
        f"{_TAG_PREFIX}DATETIME": _format_datetime(dt_str),
        f"{_TAG_PREFIX}PROJECTION_NAME": crs_name,
        f"{_TAG_PREFIX}CRS_PROJ4": crs_proj4,
        f"{_TAG_PREFIX}CRS_WKT": crs_wkt,
        f"{_TAG_PREFIX}EPSG": crs_epsg,
        f"{_TAG_PREFIX}RESOLUTION_METERS": f"{resolution:.6f}",
        f"{_TAG_PREFIX}WIDTH": str(int(width)),
        f"{_TAG_PREFIX}HEIGHT": str(int(height)),
        f"{_TAG_PREFIX}SOFTWARE": _SOFTWARE,
        "AREA_OR_POINT": "Area",
    }
    if footer_info:
        tags[f"{_TAG_PREFIX}FOOTER"] = footer_info
    if bounds:
        min_lon, max_lon, min_lat, max_lat = bounds
        tags[f"{_TAG_PREFIX}MIN_LON"] = f"{min_lon:.6f}"
        tags[f"{_TAG_PREFIX}MAX_LON"] = f"{max_lon:.6f}"
        tags[f"{_TAG_PREFIX}MIN_LAT"] = f"{min_lat:.6f}"
        tags[f"{_TAG_PREFIX}MAX_LAT"] = f"{max_lat:.6f}"
        tags[f"{_TAG_PREFIX}CENTER_LON"] = f"{(min_lon + max_lon) / 2.0:.6f}"
        tags[f"{_TAG_PREFIX}CENTER_LAT"] = f"{(min_lat + max_lat) / 2.0:.6f}"
    return tags


def write_rgba_geotiff(path, rgba: np.ndarray, transform: Affine | None,
                       crs, tags: dict | None = None) -> Path:
    """Write an RGBA 8-bit GeoTIFF with full georeferencing + metadata.

    The output is a floating-point-free, interoperable, RGBA geostacked
    TIF: bands 1-3 are RGB and band 4 is explicitly tagged as alpha.
    Metadata (satellite, band, datetime, lat/lon bounds, projection, ...)
    is embedded as TIFF tags through ``build_tags`` when supplied as a dict.

    Args:
        path: Destination ``.tif`` path (str or Path).
        rgba: (height, width, 4) uint8 RGBA array (or RGB/Gray promoted).
        transform: Geotransform of the output grid.
        crs: Source CRS (pyproj CRS / EPSG / WKT / proj4).
        tags: Optional dict written as TIFF tags.

    Returns:
        Path to the written file.

    Raises:
        ValueError: If the array cannot be promoted to RGBA.
    """
    path = Path(path)
    arr = np.asarray(rgba)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr, np.full_like(arr, 255, dtype=np.uint8)], axis=-1)
    elif arr.ndim == 3:
        if arr.shape[2] == 1:
            arr = np.concatenate([arr] * 3 + [np.full(arr.shape[:2] + (1,), 255, np.uint8)], axis=-1)
        elif arr.shape[2] == 3:
            alpha = np.full(arr.shape[:2] + (1,), 255, dtype=np.uint8)
            arr = np.concatenate([arr, alpha], axis=-1)
        elif arr.shape[2] != 4:
            raise ValueError(f"Cannot write {arr.shape[2]}-band array as RGBA")
    else:
        raise ValueError(f"Cannot write {arr.ndim}-dimensional array as RGBA")
    height, width = arr.shape[:2]

    crs_obj = CRS.from_user_input(crs) if crs is not None else None
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 4,
        "dtype": "uint8",
        "crs": crs_obj,
        "transform": transform,
        "tiled": False,
        "interleave": "pixel",
    }
    with rasterio.open(path, "w", **profile) as dst:
        for band_idx in range(4):
            dst.write(arr[..., band_idx], band_idx + 1)
        try:
            dst.colorinterp = [
                ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha,
            ]
        except Exception:
            pass
        if tags:
            dst.update_tags(**tags)
    return path
