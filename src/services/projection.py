# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: services/projection.py
# Description: Map projection operations including reprojection, pyresample resampling, and CRS extraction from satellite datasets.
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
import math
from pathlib import Path

import numpy as np
import rasterio
import xarray as xr
from pyproj import CRS
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage

from src.core.helpers import normalize_lon


class ProjectionService:

    def __init__(self, main_ui):
        self.main_ui = main_ui

    def _build_proj4_from_geos_params(self, crs_dict: dict) -> str:
        """Build a GEOS proj4 string that honors the source's own sweep axis.

        Sweep is taken from ``sweep_angle_axis`` / ``sweep`` when the source
        (HSD/ADS sidecar, netCDF, proj4) declares it. Only when the source is
        silent do we guess from the longitude sign (GOES west => 'x', others
        => 'y'). Guessing by longitude alone is wrong for Himawari (+140.7E),
        which uses sweep='x', so the declared value must win.
        """
        default_lon = getattr(self.main_ui, 'satellite_lon', 140.7)
        lon0 = crs_dict.get("longitude_of_projection_origin", default_lon)
        h = crs_dict.get("perspective_point_height", 35785863)
        a = crs_dict.get("semi_major_axis", 6378137.0)
        b = crs_dict.get("semi_minor_axis", 6356752.314140356)
        rf = crs_dict.get("inverse_flattening")
        declared = crs_dict.get("sweep_angle_axis") or crs_dict.get("sweep")
        if declared is None:
            lon = float(lon0)
            declared = "x" if lon < 0 else "y"
        sweep = "x" if str(declared).lower().strip() in ("x", "true", "1") else "y"
        sweep_param = f" +sweep={sweep}" if sweep else ""
        if rf:
            proj4 = f"+proj=geos +lon_0={lon0} +h={h} +a={a} +rf={rf}{sweep_param} +units=m +no_defs"
        else:
            proj4 = f"+proj=geos +lon_0={lon0} +h={h} +a={a} +b={b}{sweep_param} +units=m +no_defs"
        return proj4

    def _is_beta_viewport(self):
        return self.main_ui.settings.get("viewport_mode", "beta") == "beta"

    def _get_ref_grid_size(self):
        """Return (ref_w, ref_h) from GOES cache, inline GOES, ads.json, or fallback."""
        if hasattr(self.main_ui, '_goes_cache') and self.main_ui._goes_cache is not None:
            try:
                sz = self.main_ui._goes_cache.get_ref_grid_size()
                if sz is not None:
                    return sz
            except Exception:
                pass
        if hasattr(self.main_ui, '_goes_geotransform_2km') and self.main_ui._goes_geotransform_2km is not None:
            try:
                gt = self.main_ui._goes_geotransform_2km
                if hasattr(self.main_ui, 'current_geotransform') and self.main_ui.current_geotransform is not None:
                    cgt = self.main_ui.current_geotransform
                    if abs(cgt.a) > 0:
                        x_ext = abs(cgt.c) * 2
                        y_ext = abs(cgt.f) * 2
                        rw = max(1, int(round(x_ext / 2000.0)))
                        rh = max(1, int(round(y_ext / 2000.0)))
                        return (rw, rh)
            except Exception:
                pass
        nc_path = self.main_ui._current_nc_file()
        if nc_path:
            sc = self._find_ads_sidecar(nc_path)
            if sc is not None:
                try:
                    import json as _js
                    with open(sc, "r", encoding='utf-8') as _f:
                        _ad = _js.load(_f)
                    rw = _ad.get("ref_grid_w")
                    rh = _ad.get("ref_grid_h")
                    if rw is not None and rh is not None:
                        return (rw, rh)
                    rw = _ad.get("ref_grid_1km_w")
                    rh = _ad.get("ref_grid_1km_h")
                    if rw is not None and rh is not None:
                        return (rw, rh)
                    rw = _ad.get("ref_grid_0_5km_w")
                    rh = _ad.get("ref_grid_0_5km_h")
                    if rw is not None and rh is not None:
                        return (rw, rh)
                    am = _ad.get("area_meta")
                    if am:
                        sh = am.get("shape")
                        if sh and len(sh) == 2:
                            return (sh[1], sh[0])
                except Exception:
                    pass
        if self.main_ui.current_geotransform is not None and getattr(self.main_ui, 'current_crs', None) is not None:
            try:
                cgt = self.main_ui.current_geotransform
                if abs(cgt.a) > 0:
                    x_ext = abs(cgt.c) * 2
                    y_ext = abs(cgt.f) * 2
                    rw = max(1, int(round(x_ext / 2000.0)))
                    rh = max(1, int(round(y_ext / 2000.0)))
                    return (rw, rh)
            except Exception:
                pass
        return None

    def _find_ads_sidecar(self, nc_path: Path) -> Path | None:
        """Find .ads.json sidecar — try exact match first, then scan for AHI_*.ads.json.

        For multi-sub-area folders (Target R3xx / Japan JPxx) prefer a sidecar whose
        name carries the same sub-area token as the NC, so the CRS/GT/coff_loff always
        match the segment the NC belongs to.
        """
        sidecar = nc_path.with_suffix(".ads.json")
        if sidecar.exists():
            return sidecar
        import re as _re
        _m = _re.search(r'_(R\d{3}|JP\d{2})(?:_|\.|$)', nc_path.name)
        _token = _m.group(1) if _m else None
        _fallback = None
        for f in nc_path.parent.glob("AHI_*.ads.json"):
            if _fallback is None:
                _fallback = f
            if _token is not None and _token in f.name:
                return f
        return _fallback

    def extract_crs_from_ads(self, nc_path: Path):
        from src.parsers.gk2a_parser import is_gk2a_file, extract_gk2a_crs
        if is_gk2a_file(nc_path):
            self.main_ui.log("[CRS] GK-2A file detected - extracting GEOS projection from attrs.")
            return extract_gk2a_crs(nc_path)
        sidecar = self._find_ads_sidecar(nc_path)
        if sidecar is None:
            self.main_ui.log("[CRS] No .ads.json sidecar found - trying GOES NC projection...")
            return self._extract_crs_from_nc(nc_path)
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                ads = json.load(f)
            _rw = ads.get("ref_grid_w")
            _rh = ads.get("ref_grid_h")
            self.main_ui._ref_grid_size = (_rw, _rh) if (_rw and _rh) else None
            _rw1 = ads.get("ref_grid_1km_w")
            _rh1 = ads.get("ref_grid_1km_h")
            self.main_ui._ref_grid_1km = (_rw1, _rh1) if (_rw1 and _rh1) else None
            _rw05 = ads.get("ref_grid_0_5km_w")
            _rh05 = ads.get("ref_grid_0_5km_h")
            self.main_ui._ref_grid_0_5km = (_rw05, _rh05) if (_rw05 and _rh05) else None
            crs_info = ads.get("crs")
            if not crs_info:
                self.main_ui.log("[CRS] No crs field in .ads.json - overlays disabled.")
                return None, None

            proj4 = crs_info.get("proj4")
            wkt   = crs_info.get("wkt")
            if proj4:
                crs = CRS.from_proj4(proj4)
                self.main_ui.log(f"[CRS] Loaded from proj4: {proj4}")
            elif wkt:
                crs = CRS.from_wkt(wkt)
                self.main_ui.log("[CRS] Loaded from WKT.")
            else:
                proj4_str = self._build_proj4_from_geos_params(crs_info)
                crs = CRS.from_proj4(proj4_str)
                self.main_ui.log(f"[CRS] Built PROJ.4: {proj4_str}")

            sector = ads.get("sector", "")
            self.main_ui.log(f"[CRS] Sector from ads.json: {sector}")
            geotransform_2km = ads.get("geotransform_2km")
            if geotransform_2km and len(geotransform_2km) == 6:
                transform = rasterio.transform.Affine(*geotransform_2km)
                self.main_ui.log(f"[CRS] Using 2km ref geotransform: {geotransform_2km}")
                return crs, transform

            geotransform_1km = ads.get("geotransform_1km")
            if geotransform_1km and len(geotransform_1km) == 6:
                transform = rasterio.transform.Affine(*geotransform_1km)
                self.main_ui.log(f"[CRS] Using 1km ref geotransform: {geotransform_1km}")
                return crs, transform

            geotransform_0_5km = ads.get("geotransform_0_5km")
            if geotransform_0_5km and len(geotransform_0_5km) == 6:
                transform = rasterio.transform.Affine(*geotransform_0_5km)
                self.main_ui.log(f"[CRS] Using 0.5km ref geotransform: {geotransform_0_5km}")
                return crs, transform

            geotransform = ads.get("geotransform")
            if geotransform and len(geotransform) == 6:
                transform = rasterio.transform.Affine(*geotransform)
                self.main_ui.log(f"[CRS] Geotransform loaded from sidecar: {geotransform}")
                return crs, transform

            area_meta = ads.get("area_meta")
            if area_meta:
                try:
                    extent = area_meta.get("extent")
                    shape = area_meta.get("shape")
                    if extent and shape and len(extent) == 4 and len(shape) == 2:
                        x_ll, y_ll, x_ur, y_ur = extent
                        nrows, ncols = shape
                        res_x = (x_ur - x_ll) / ncols
                        res_y = (y_ur - y_ll) / nrows
                        transform = rasterio.transform.from_origin(x_ll, y_ur, res_x, res_y)
                        self.main_ui.log(f"[CRS] Geotransform built from area_meta: extent={extent} shape={shape}")
                        return crs, transform
                except Exception as e:
                    self.main_ui.log(f"[CRS] area_meta geotransform build failed: {e}")

            transform = None
            try:
                with xr.open_dataset(nc_path, engine="netcdf4") as ds:
                    self.main_ui.log(f"[CRS] NC coords available: {list(ds.coords)}")
                    if "x" in ds.coords and "y" in ds.coords:
                        x = ds.coords["x"].values
                        y = ds.coords["y"].values
                        self.main_ui.log(f"[CRS] x range: {x.min():.6f} to {x.max():.6f}  shape={x.shape}")
                        self.main_ui.log(f"[CRS] y range: {y.min():.6f} to {y.max():.6f}  shape={y.shape}")
                        if len(x) > 1 and len(y) > 1:
                            h = float(crs_info.get("perspective_point_height", 35785863.0))

                            if abs(float(x.max())) < 1.0:
                                self.main_ui.log(f"[CRS] x/y appear to be radians, scaling by h={h}")
                                x_m = x.astype(float) * h
                                y_m = y.astype(float) * h
                            else:
                                self.main_ui.log("[CRS] x/y appear to be metres already")
                                x_m = x.astype(float)
                                y_m = y.astype(float)
                            res_x = float(abs(x_m[1] - x_m[0]))
                            res_y = float(abs(y_m[1] - y_m[0]))
                            self.main_ui.log(f"[CRS] Derived res_x={res_x:.1f}m  res_y={res_y:.1f}m  x_min={x_m.min():.1f}  y_max={y_m.max():.1f}")
                            transform = rasterio.transform.from_origin(float(x_m.min()), float(y_m.max()), res_x, res_y)
                            self.main_ui.log(f"[CRS] Geotransform from NC x/y: {transform}")
                    else:
                        self.main_ui.log("[CRS] No x/y coords in NC - trying AHI FLDK geometry fallback.")
            except Exception as e:
                self.main_ui.log(f"[CRS] Could not read NC coords: {e}")

            if transform is None:
                try:
                    h = float(crs_info.get("perspective_point_height", 35785863.0))
                    lon0 = float(crs_info.get("longitude_of_projection_origin", 140.7))
                    band_stats = ads.get("band_stats", {})

                    ref_shape = None
                    ref_band = None
                    ref_n = 0
                    for bname, binfo in band_stats.items():
                        shape = binfo.get("shape")
                        if shape and len(shape) == 2:
                            n = shape[0] * shape[1]
                            if ref_shape is None or n < ref_n:
                                ref_shape = shape
                                ref_band = bname
                                ref_n = n
                    if ref_shape is None:
                        ref_shape = [5500, 5500]
                        ref_band = "fallback"
                    nrows, ncols = ref_shape
                    self.main_ui.log(f"[CRS] Fallback using ref band {ref_band} shape {ref_shape}")

                    ts = ads.get("timestamp", "") or str(nc_path)
                    sector = "FLDK"
                    if "Japan" in ts or "JAPAN" in ts.upper():
                        sector = "Japan"
                    elif "Target" in ts or "TARGET" in ts.upper():
                        sector = "Target"
                    self.main_ui.log(f"[CRS] Detected sector for fallback: {sector}")

                    if sector == "FLDK":

                        resolution_map = {5500: 2000.0, 11000: 1000.0, 22000: 500.0}
                        res_m = resolution_map.get(nrows)
                        if res_m is None:
                            res_m = (h * 0.307) / nrows
                        extent = res_m * nrows / 2.0
                        self.main_ui.log(f"[CRS] res_m={res_m}  extent=+/-{extent/1000:.1f}km")
                        transform = rasterio.transform.from_origin(-extent, extent, res_m, res_m)
                    else:

                        res_map = {"Japan": {"B03": 1000.0, "_default": 4000.0},
                                   "Target": {"B03": 500.0, "_default": 2000.0}}
                        sector_res = res_map.get(sector, {})

                        b03 = band_stats.get("B03", {})
                        b03_shape = b03.get("shape") if b03 else None
                        if b03_shape and len(b03_shape) == 2:
                            sub_rows, sub_cols = b03_shape
                            res_m = sector_res.get("B03", 1000.0)
                        else:
                            sub_rows, sub_cols = nrows, ncols
                            res_m = sector_res.get("_default", 4000.0)

                    coff_loff = ads.get("coff_loff")
                    if coff_loff and "COFF" in coff_loff and "LOFF" in coff_loff:
                        coff = float(coff_loff["COFF"])
                        loff = float(coff_loff["LOFF"])
                        cfac = float(coff_loff.get("CFAC", 40932534))
                        lfac = float(coff_loff.get("LFAC", 40932534))
                        sat_h = float(coff_loff.get("distance_from_earth_center", 42164.0)) * 1000.0 - 6378137.0
                        rad_per_pix = 1.0 / cfac
                        x_c = (coff - 5500.5) * sat_h * rad_per_pix
                        y_c = (loff - 5500.5) * sat_h * rad_per_pix
                        self.main_ui.log(f"[CRS] {sector} COFF={coff:.1f} LOFF={loff:.1f} "
                                         f"-> centre_proj=({x_c/1000:.1f}, {y_c/1000:.1f}) km")
                    else:
                        if sector == "Japan":
                            approx_clon, approx_clat = 135.0, 30.0
                        else:
                            approx_clon, approx_clat = 140.0, 20.0
                        lat_r = math.radians(approx_clat)
                        dlon = math.radians(approx_clon - lon0)
                        x_c = h * math.sin(dlon) * math.cos(lat_r)
                        y_c = h * math.sin(lat_r) * math.cos(dlon)
                        self.main_ui.log(f"[CRS] {sector} approx centre -> ({approx_clon},{approx_clat}deg) "
                                         f"-> proj=({x_c/1000:.1f}, {y_c/1000:.1f}) km")
                    half_w = (sub_cols * res_m) / 2.0
                    half_h = (sub_rows * res_m) / 2.0
                    x_origin = x_c - half_w
                    y_origin = y_c + half_h
                    self.main_ui.log(f"[CRS] {sector} res_m={res_m} origin=({x_origin/1000:.0f}, {y_origin/1000:.0f})km")
                    transform = rasterio.transform.from_origin(x_origin, y_origin, res_m, res_m)

                    self.main_ui.log(f"[CRS] Geotransform from fallback: {transform}")
                except Exception as e:
                    self.main_ui.log(f"[CRS] Geometry fallback failed: {e}")

            if transform is None:
                self.main_ui.log("[CRS] Could not build geotransform - overlays disabled.")
            return crs, transform
        except Exception as e:
            self.main_ui.log(f"[CRS] Exception in extract_crs_from_ads: {e}")
            return None, None

    def _extract_crs_from_nc(self, nc_path: Path):
        try:
            with xr.open_dataset(nc_path, engine="netcdf4", mask_and_scale=False) as ds:
                if 'goes_imager_projection' in ds:
                    proj = ds['goes_imager_projection']
                    h = float(proj.attrs.get('perspective_point_height', 35786023))
                    sat_lon = getattr(self.main_ui, 'satellite_lon', 140.7)
                    lon_0 = float(proj.attrs.get('longitude_of_projection_origin', sat_lon))
                    sweep_provided = proj.attrs.get('sweep_angle_axis')
                    sweep = sweep_provided if sweep_provided else ("x" if float(lon_0) < 0 else "y")
                    sweep_param = f" +sweep={sweep}" if sweep else ""
                    proj4_str = f"+proj=geos +lon_0={lon_0} +h={h} +x_0=0 +y_0=0 +ellps=WGS84{sweep_param} +units=m +no_defs"
                    crs = CRS.from_proj4(proj4_str)
                    self.main_ui.log(f"[CRS] Extracted from GOES NC projection: {proj4_str}")

                    scale_x = scale_y = x_min = y_max = None
                    nx = ds.sizes.get('x', 0) or 0
                    ny = ds.sizes.get('y', 0) or 0
                    if 'x' in ds and 'y' in ds:
                        x = ds['x'].values
                        y = ds['y'].values
                        if len(x) > 1 and len(y) > 1:
                            x_step = float(x[1] - x[0]) if len(x) > 1 else 1.0
                            y_step = float(y[1] - y[0]) if len(y) > 1 else 1.0
                            is_pixel_idx = (x_step == 1.0 and float(x[0]) == 0.0) or (x_step == 1.0 and float(x[0]) == 0.5)
                            abs_max = max(abs(x.min()), abs(x.max()), abs(y.min()), abs(y.max()))
                            if not is_pixel_idx and abs_max > 10000:
                                scale_x = x_step
                                scale_y = y_step
                                x_min = float(x[0])
                                y_max = float(y[0])
                                self.main_ui.log(f"[CRS] Geotransform from NC x/y coords: scale={scale_x:.1f}m origin=({x_min:.1f},{y_max:.1f})")

                    if scale_x is None and nx > 0 and ny > 0:
                        earth_angular_radius = 0.1518
                        scale_x = 2.0 * h * earth_angular_radius / nx
                        scale_y = -scale_x
                        x_min = -scale_x * nx / 2.0
                        y_max = -scale_y * ny / 2.0
                        self.main_ui.log(f"[CRS] Built geotransform from projection attrs: nx={nx} ny={ny} res={scale_x:.1f}m")
                    if scale_x is not None:
                        transform = rasterio.transform.Affine(scale_x, 0.0, x_min, 0.0, scale_y, y_max)
                        if nx > 0 and ny > 0:
                            if 'x' in ds and 'y' in ds and len(x) > 1:
                                x_ext = abs(float(x.max()) - float(x.min()))
                                y_ext = abs(float(y.max()) - float(y.min()))
                            else:
                                x_ext = abs(scale_x) * nx
                                y_ext = abs(scale_y) * ny
                            res_2km = 2000.0
                            nx_2km = max(1, int(round(x_ext / res_2km)))
                            ny_2km = max(1, int(round(y_ext / res_2km)))
                            self.main_ui._goes_geotransform_2km = rasterio.transform.Affine(
                                res_2km, 0.0, float(x_min), 0.0, -res_2km, float(y_max)
                            )
                        else:
                            self.main_ui._goes_geotransform_2km = None
                        self.main_ui.log(f"[CRS] Geotransform: {transform}")
                        if self.main_ui._goes_geotransform_2km is not None:
                            self.main_ui._ref_grid_size = (nx_2km, ny_2km)
                            self.main_ui.log(f"[CRS] Using 2km ref geotransform for overlays: grid={nx_2km}x{ny_2km}")
                            return crs, self.main_ui._goes_geotransform_2km
                        return crs, transform
                    self.main_ui.log("[CRS] Could not build geotransform from NC - overlays disabled.")
                    return crs, None
                self.main_ui.log("[CRS] No goes_imager_projection in NC - overlays disabled.")
        except Exception as e:
            self.main_ui.log(f"[CRS] Could not extract CRS from NC: {e}")
        return None, None

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
            native_res_m = abs(gt.a)
            ox = (gt.c + data_extent) / native_res_m
            oy = (data_extent - gt.f) / native_res_m
            return QPointF(ox, oy)
        disk_half = self.main_ui._compute_disk_half_extent()
        native_res_m = abs(gt.a)
        ox = (gt.c + disk_half) / native_res_m
        oy = (disk_half - gt.f) / native_res_m
        return QPointF(ox, oy)

    def _project_lonlat_to_pixel(self, lon, lat):
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

    def _get_source_boundary_points(self, gt, w, h):
        return [
            (gt[2], gt[5]),
            (gt[2] + gt[0] * w, gt[5]),
            (gt[2], gt[5] + gt[4] * h),
            (gt[2] + gt[0] * w, gt[5] + gt[4] * h),
            (gt[2] + gt[0] * w / 2, gt[5]),
            (gt[2] + gt[0] * w / 2, gt[5] + gt[4] * h),
            (gt[2], gt[5] + gt[4] * h / 2),
            (gt[2] + gt[0] * w, gt[5] + gt[4] * h / 2),
        ]

    def _reproject_to(self, target_proj, src_img, crs_src, gt, target_w=0, target_h=0):
        """Reproject geostationary imagery to target projection using pyresample directly."""
        config = self.main_ui.PROJECTION_CONFIGS.get(target_proj)
        if config is None:
            self.main_ui.log(f"[VISUALIZER] Unknown projection: {target_proj}")
            return src_img
        if target_w <= 0 or target_h <= 0:
            target_w = src_img.width()
            target_h = src_img.height()
        out_h, out_w = max(1, target_h), max(1, target_w)

        self.main_ui.log(f"[VISUALIZER] Reprojecting: {target_proj}, src={src_img.width()}x{src_img.height()}, out={out_w}x{out_h}")
        self.main_ui.log(f"[VISUALIZER] Source CRS: {crs_src.to_proj4() if crs_src else 'None'}")
        self.main_ui.log(f"[VISUALIZER] Source geotransform: {gt}")
        try:
            import numpy as np
            import xarray as xr
            from pyresample import get_resampler_needed
            from pyresample.geometry import AreaDefinition

            # Get resampling method from settings
            resampling_method = self.main_ui.settings.get("resampling_method", "nearest")
            # Map UI values to pyresample resamplers
            resampler_map = {
                "none": "native",
                "nearest": "nearest",
                "ewa": "ewa",
                "bilinear": "bilinear",
                "gradient_search": "gradient_search"
            }
            resampler = resampler_map.get(resampling_method, "nearest")

            is_equirectangular = (target_proj == "equirectangular")

            if src_img.format() != QImage.Format_RGBA8888:
                src_img = src_img.convertToFormat(QImage.Format_RGBA8888)
            ptr = src_img.bits()
            if hasattr(ptr, 'setsize'):
                try:
                    ptr.setsize(src_img.width() * src_img.height() * 4)
                except ValueError:
                    pass
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape(src_img.height(), src_img.width(), 4)
            self.main_ui.log(f"[VISUALIZER] Source array shape: {arr.shape}, dtype={arr.dtype}")

            if arr.shape[2] == 4:
                alpha_raw = arr[:, :, 3].astype(np.float32)
                ch0_raw = arr[:, :, 0].astype(np.float32)
                alpha = alpha_raw / 255.0
                data = ch0_raw * alpha
                self.main_ui.log(f"[VISUALIZER] Debug ch0 range=[{ch0_raw.min():.1f}, {ch0_raw.max():.1f}], alpha range=[{alpha_raw.min():.1f}, {alpha_raw.max():.1f}], masked range=[{data.min():.1f}, {data.max():.1f}], fmt={src_img.format()}")
            else:
                data = arr[:, :, 0].astype(np.float32)

            src_area = getattr(self.main_ui, '_source_area_def', None)
            if src_area is None:
                src_w, src_h = src_img.width(), src_img.height()
                self.main_ui.log("[VISUALIZER] Building source AreaDefinition from geotransform")
                # Compute symmetric extent centered at (0,0) for GEOS projection
                half_x = max(abs(gt.c), abs(gt.c + gt.a * (src_w - 1)))
                half_y = max(abs(gt.f), abs(gt.f + gt.e * (src_h - 1)))
                half = max(half_x, half_y)
                area_extent = [-half, -half, half, half]

                # Use CRS as WKT to avoid PROJ4 information loss
                try:
                    crs_arg = crs_src.to_wkt()
                except Exception:
                    crs_arg = crs_src.to_proj4()
                src_area = AreaDefinition(
                    'source_geos',
                    'source_geos',
                    'source',
                    crs_arg,
                    src_w,
                    src_h,
                    area_extent
                )
            else:
                self.main_ui.log(f"[VISUALIZER] Using cached source AreaDefinition: {src_area.area_id}")

            if is_equirectangular:
                target_area = self._create_equirectangular_area_def(src_area, out_w, out_h)
            else:
                target_area = self._create_plate_carree_area_def(src_area, out_w, out_h)

            self.main_ui.log(f"[VISUALIZER] Target area: {target_area.area_id}, extent={target_area.area_extent}, shape={target_area.shape}")

            self.main_ui.log(f"[VISUALIZER] Starting pyresample resample_{resampler}...")
            # Get the resampler function based on settings
            resampler_func = get_resampler_needed(resampler)
            result_arr = resampler_func(
                src_area,
                data,
                target_area,
                radius_of_influence=50000,
                fill_value=np.nan
            )
            self.main_ui.log(f"[VISUALIZER] Resample complete, result shape: {result_arr.shape if result_arr is not None else 'None'}")

            if result_arr is not None:
                valid = result_arr[np.isfinite(result_arr)]
                self.main_ui.log(f"[VISUALIZER] Valid pixels: {len(valid)}, range=[{valid.min() if len(valid) > 0 else 'N/A'}, {valid.max() if len(valid) > 0 else 'N/A'}]")

                if len(valid) > 0:
                    vmin, vmax = valid.min(), valid.max()
                    if vmax > vmin:
                        result_arr = ((result_arr - vmin) / (vmax - vmin) * 255).clip(0, 255).astype(np.uint8)
                    else:
                        result_arr = np.zeros_like(result_arr, dtype=np.uint8)
                        self.main_ui.log("[VISUALIZER] WARNING: All values identical, using zeros")
                else:
                    result_arr = np.zeros_like(result_arr, dtype=np.uint8)
                    self.main_ui.log("[VISUALIZER] WARNING: No valid pixels, using zeros")

                result_rgba = np.stack([result_arr, result_arr, result_arr, np.full_like(result_arr, 255)], axis=-1)
                result_bytes = result_rgba.tobytes()
                result_img = QImage(result_bytes, out_w, out_h, out_w * 4, QImage.Format_RGBA8888)
                self.main_ui.log(f"[VISUALIZER] Created output QImage: {result_img.width()}x{result_img.height()}, null={result_img.isNull()}")

                out_ptr = result_img.bits()
                if hasattr(out_ptr, 'setsize'):
                    out_ptr.setsize(out_w * out_h * 4)
                out_arr_check = np.frombuffer(out_ptr, dtype=np.uint8).reshape(out_h, out_w, 4)
                nonzero = np.count_nonzero(out_arr_check[:, :, 0])
                self.main_ui.log(f"[VISUALIZER] Output non-zero pixels: {nonzero}/{out_w*out_h} ({100*nonzero/(out_w*out_h):.1f}%)")

                return result_img.copy()

            self.main_ui.log("[VISUALIZER] WARNING: result_arr is None, returning source")
            return src_img

        except ImportError as e:
            self.main_ui.log(f"[VISUALIZER] pyresample import failed: {e}, using fallback")
            return self._reproject_simple(target_proj, src_img, crs_src, gt, out_w, out_h)
        except Exception as e:
            self.main_ui.log(f"[VISUALIZER] pyresample reprojection failed: {e}")
            import traceback
            self.main_ui.log(f"[VISUALIZER] Traceback: {traceback.format_exc()}")
            return src_img

    def _create_equirectangular_area_def(self, src_area, out_w, out_h):
        """Create Equirectangular (Plate Carree) AreaDefinition for full disk view.
        
        Uses the same method as VPSIFT.py - metric coordinates based on Earth radius.
        Centered on satellite longitude with crop_deg=85 to show full Earth disk
        with black space around the limbs.
        """
        import numpy as np
        from pyresample.geometry import AreaDefinition

        # Get satellite longitude from source CRS
        try:
            crs_dict = src_area.crs.to_dict()
            sat_lon = float(crs_dict.get('lon_0', 140.7))
        except Exception:
            sat_lon = 140.7

        # Crop degrees - 85 shows full disk with some padding
        crop_deg = 85

        # Earth radius metric conversion for Equirectangular (same as VPSIFT.py)
        R = 6378137.0
        deg2rad = np.pi / 180.0
        
        # Calculate extent in meters
        x_min = -crop_deg * R * deg2rad
        x_max = crop_deg * R * deg2rad
        y_min = -crop_deg * R * deg2rad
        y_max = crop_deg * R * deg2rad

        # Use eqc projection dict (same as VPSIFT.py)
        projection_dict = {'proj': 'eqc', 'lon_0': sat_lon, 'lat_ts': 0}

        self.main_ui.log(f"[VISUALIZER] Equirectangular extent (meters): x=[{x_min/1e6:.1f}, {x_max/1e6:.1f}]Mm, y=[{y_min/1e6:.1f}, {y_max/1e6:.1f}]Mm, centered on sat_lon={sat_lon}")

        return AreaDefinition(
            'equirectangular_fulldisk',
            'Equirectangular full disk view',
            'eqc_fulldisk',
            projection_dict,
            out_w,
            out_h,
            (x_min, y_min, x_max, y_max)
        )

    def _create_plate_carree_area_def(self, src_area, out_w, out_h):
        """Create Plate Carree AreaDefinition covering the visible disk extent."""
        import numpy as np
        from pyresample.geometry import AreaDefinition

        lons, lats = src_area.get_lonlats()
        valid = np.isfinite(lons) & np.isfinite(lats)
        if np.any(valid):
            lon_min, lon_max = float(lons[valid].min()), float(lons[valid].max())
            lat_min, lat_max = float(lats[valid].min()), float(lats[valid].max())
        else:
            self.main_ui.log("[VISUALIZER] No valid lat/lon from source area, using fallback")
            return AreaDefinition(
                'plate_carree_fallback',
                'Plate Carree fallback',
                'eqc_fallback',
                'EPSG:4326',
                out_w,
                out_h,
                (-180, -90, 180, 90)
            )

        return AreaDefinition(
            'plate_carree_fulldisk',
            'Plate Carree full disk view',
            'eqc_fulldisk',
            'EPSG:4326',
            out_w,
            out_h,
            (lon_min, lat_min, lon_max, lat_max)
        )

    def _reproject_simple(self, target_proj, src_img, crs_src, gt, out_w, out_h):
        """Simple fallback reprojection using basic transformer."""
        try:
            import numpy as np
            from pyproj import Transformer

            is_equirectangular = (target_proj == "equirectangular")

            out_img = QImage(out_w, out_h, QImage.Format_RGBA8888)
            out_img.fill(Qt.black)

            if src_img.format() != QImage.Format_RGBA8888:
                src_img = src_img.convertToFormat(QImage.Format_RGBA8888)
            src_ptr = src_img.bits()
            if hasattr(src_ptr, 'setsize'):
                try:
                    src_ptr.setsize(src_img.width() * src_img.height() * 4)
                except ValueError:
                    pass
            src_arr = np.frombuffer(src_ptr, dtype=np.uint8).reshape(src_img.height(), src_img.width(), 4)

            if is_equirectangular:
                try:
                    crs_dict = crs_src.to_dict()
                    sat_lon = float(crs_dict.get('lon_0', 140.7))
                except Exception:
                    sat_lon = 140.7
                target_crs = (f"+proj=eqc +lat_ts=0 +lat_0=0 +lon_0={sat_lon:.6f} "
                              "+x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs")
            else:
                target_crs = "EPSG:4326"

            transformer = Transformer.from_crs(crs_src, target_crs, always_xy=True)

            sh, sw = src_arr.shape[:2]
            out_arr = np.zeros((out_h, out_w, 4), dtype=np.uint8)

            if is_equirectangular:
                # Get satellite longitude for centering
                try:
                    crs_dict = crs_src.to_dict()
                    sat_lon = float(crs_dict.get('lon_0', 140.7))
                except Exception:
                    sat_lon = 140.7
                crop_deg = 85
                lon_min = sat_lon - crop_deg
                lon_max = sat_lon + crop_deg
                lat_min = -crop_deg
                lat_max = crop_deg
                # Convert the degree grid to equirectangular (eqc) meters at lat_ts=0
                r_eq = 6378137.0
                x_min = (lon_min - sat_lon) * math.radians(1.0) * r_eq
                x_max = (lon_max - sat_lon) * math.radians(1.0) * r_eq
                y_min = lat_min * math.radians(1.0) * r_eq
                y_max = lat_max * math.radians(1.0) * r_eq
            else:
                x_min, y_min, x_max, y_max = -180, -90, 180, 90

            step = max(1, min(out_w, out_h) // 200)

            for oy in range(0, out_h, step):
                for ox in range(0, out_w, step):
                    tx = x_min + (ox / out_w) * (x_max - x_min)
                    ty = y_min + (oy / out_h) * (y_max - y_min)

                    try:
                        sx_proj, sy_proj = transformer.transform(tx, ty, direction='INVERSE')

                        src_x = int((sx_proj - gt[2]) / gt[0])
                        src_y = int((sy_proj - gt[5]) / gt[4])

                        if 0 <= src_x < sw and 0 <= src_y < sh:
                            pixel = src_arr[src_y, src_x]
                            out_arr[oy:oy+step, ox:ox+step] = pixel
                    except Exception:
                        pass

            mask = out_arr[:, :, 3] == 0
            if np.any(mask):
                from scipy.ndimage import generic_filter
                for c in range(4):
                    channel = out_arr[:, :, c].astype(np.float32)
                    alpha = out_arr[:, :, 3].astype(np.float32)
                    alpha[alpha == 0] = np.nan
                    filled = generic_filter(channel, lambda x: np.nanmean(x) if np.any(~np.isnan(x)) else 0, size=3)
                    out_arr[:, :, c] = np.where(mask, filled, channel).clip(0, 255).astype(np.uint8)
                out_arr[:, :, 3] = 255

            result_bytes = out_arr.tobytes()
            result_img = QImage(result_bytes, out_w, out_h, out_w * 4, QImage.Format_RGBA8888)
            return result_img

        except Exception as e:
            self.main_ui.log(f"[{target_proj}] Simple reprojection failed: {e}")
            return src_img

    def _resample_with_pyresample(self, src_img, crs_src, gt, tgt_def, desc, radius_of_influence=50000):
        out_w, out_h = tgt_def.width, tgt_def.height
        from pyresample import get_resampler_needed
        from pyresample.geometry import AreaDefinition
        # Get resampling method from settings
        resampling_method = self.main_ui.settings.get("resampling_method", "nearest")
        # Map UI values to pyresample resamplers
        resampler_map = {
            "none": "native",
            "nearest": "nearest",
            "ewa": "ewa",
            "bilinear": "bilinear",
            "gradient_search": "gradient_search"
        }
        resampler = resampler_map.get(resampling_method, "nearest")
        src_w, src_h = src_img.width(), src_img.height()
        if src_w > out_w or src_h > out_h:
            scale = min(out_w / src_w, out_h / src_h)
            if scale < 1.0:
                new_w = max(1, int(src_w * scale))
                new_h = max(1, int(src_h * scale))
                src_img = src_img.scaled(new_w, new_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                src_w = src_img.width()
                src_h = src_img.height()
        half_x = max(abs(gt[2]), abs(gt[2] + gt[0] * (src_w - 1)))
        half_y = max(abs(gt[5]), abs(gt[5] + gt[4] * (src_h - 1)))
        half = max(half_x, half_y)
        src_def = AreaDefinition(
            "src", "source", "src",
            crs_src, src_w, src_h,
            [-half, -half, half, half]
        )
        if src_img.format() != QImage.Format_RGBA8888:
            src_img = src_img.convertToFormat(QImage.Format_RGBA8888)
        ptr = src_img.bits()
        if hasattr(ptr, 'setsize'):
            try:
                ptr.setsize(src_w * src_h * 4)
            except ValueError:
                return None
        src_arr = np.frombuffer(ptr, dtype=np.uint8).reshape(src_h, src_w, 4)
        out_arr = np.zeros((out_h, out_w, 4), dtype=np.uint8)
        for c in range(4):
            band = src_arr[:, :, c].astype(np.float32)
            # Get the resampler function based on settings
            resampler_func = get_resampler_needed(resampler)
            resampled = resampler_func(src_def, band, tgt_def, radius_of_influence=radius_of_influence)
            out_arr[:, :, c] = np.clip(np.nan_to_num(resampled, nan=0), 0, 255).astype(np.uint8)
        return QImage(out_arr.tobytes(), out_w, out_h, out_w * 4, QImage.Format_RGBA8888)
