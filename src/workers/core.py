# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: workers/core.py
# Description: Core background worker implementations for caching, compositing, export, and animation prefetch operations.
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
import io
import os
import json
import subprocess
import importlib
import logging
from pathlib import Path

_log = logging.getLogger(__name__)

import numpy as np
from scipy.ndimage import zoom
import xarray as xr
import rasterio
from pyproj import CRS
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QObject, Signal

from ..core.Engine import EmbeddedRGBEngine


if getattr(sys, 'frozen', False):
    top_dir = Path(sys.executable).resolve().parent
else:
    top_dir = Path(__file__).resolve().parent.parent.parent


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


def cache_worker_factory(input_dir, cache_dir):
    class CacheWorker(QObject):
        progress = Signal(str)
        finished = Signal(bool)
        cache_activity = Signal(str)
        def __init__(self, input_dir, cache_dir):
            super().__init__()
            self.input_dir = input_dir
            self.cache_dir = cache_dir
            self._is_cancelled = False
        def run(self):
            self.cache_activity.emit(f"Starting cache: {self.input_dir}")
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            success = False
            try:
                cm = CacheManager(input_dir=self.input_dir, cache_dir=self.cache_dir)
                cm.generate_pyramidal_cache()
                success = True
            except Exception as ex:
                self.cache_activity.emit(f"Cache failed: {ex}")
            finally:
                sys.stdout = old_stdout
            if not self._is_cancelled:
                for line in buf.getvalue().splitlines():
                    self.progress.emit(line)
                self.cache_activity.emit("Cache complete" if success else "Cache failed")
                self.finished.emit(success)
        def cancel(self):
            self._is_cancelled = True
    return CacheWorker(input_dir, cache_dir)


class ProcessDatWorker(QObject):
    progress = Signal(str)
    finished = Signal(bool)
    def __init__(self, script_path):
        super().__init__()
        self.script_path = script_path
        self._is_cancelled = False
    def run(self):
        try:
            self.progress.emit(f"Starting process_dat.py at: {self.script_path}")
            use_exe = getattr(sys, 'frozen', False) and (top_dir / 'ProcessDat.exe').exists()
            if use_exe:
                cmd = [str(top_dir / 'ProcessDat.exe')]
                cwd = top_dir
            else:
                cmd = [sys.executable, str(self.script_path)]
                cwd = self.script_path.parent
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.strip():
                        self.progress.emit(line.strip())
                self.finished.emit(True)
            else:
                self.progress.emit(f"Error: {result.stderr}")
                self.finished.emit(False)
        except Exception as e:
            self.progress.emit(f"Failed to run process_dat.py: {str(e)}")
            self.finished.emit(False)
    def cancel(self):
        self._is_cancelled = True


class CompositeWorker(QObject):
    finished = Signal(object)
    progress = Signal(str)

    def __init__(self, product_key, nc_path, max_px=2200, band_cache=None, band_file_map=None, engine_class=None):
        super().__init__()
        self.product_key = product_key
        self.nc_path = nc_path
        self.max_px = max_px
        self.band_cache = band_cache
        self.band_file_map = band_file_map
        self._engine = engine_class or EmbeddedRGBEngine

    def run(self):
        try:
            arr = self._engine.composite_realtime(
                self.product_key, self.nc_path, self.max_px,
                band_cache=self.band_cache, band_file_map=self.band_file_map
            )
            self.finished.emit((arr, self.product_key))
        except Exception as e:
            self.progress.emit(f"Composite error: {e}")
            self.finished.emit((None, self.product_key))


class PrecacheWorker(QObject):
    band_cached = Signal(str, object)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(self, nc_path, band_names, max_px=2200, band_file_map=None):
        super().__init__()
        self.nc_path = nc_path
        self.band_names = band_names
        self.max_px = max_px
        self.band_file_map = band_file_map
        self._is_cancelled = False

    def run(self):
        total = len(self.band_names)
        for idx, band in enumerate(self.band_names):
            if self._is_cancelled:
                break
            arr = EmbeddedRGBEngine.band_as_image(self.nc_path, band, self.max_px, band_file_map=self.band_file_map)
            if arr is not None:
                self.band_cached.emit(band, arr)
            self.progress.emit(idx + 1, total)
        self.finished.emit()

    def cancel(self):
        self._is_cancelled = True


class ScenePreparationWorker(QObject):

    prepared = Signal(object)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, nc_path, lazy_nc=True, band_file_map=None):
        super().__init__()
        self.nc_path = nc_path
        self.lazy_nc = lazy_nc
        self.band_file_map = band_file_map or {}

    def _band_path(self, band: str) -> Path:
        if band in self.band_file_map:
            return Path(self.band_file_map[band])
        return self.nc_path

    def run(self):
        import logging
        _log = logging.getLogger(__name__)
        _log.info(f"[ScenePrep] Starting run for {self.nc_path}")
        try:
            sidecar = self.nc_path.with_suffix(".ads.json")
            if not sidecar.exists():
                parent = self.nc_path.parent
                if parent.is_dir():
                    for f in parent.iterdir():
                        if f.suffix == ".json" and f.name.endswith(".ads.json"):
                            sidecar = f
                            break
            _log.info(f"[ScenePrep] Looking for sidecar: {sidecar}, exists: {sidecar.exists()}")
            band_names = []
            crs, transform = None, None
            ads_data = None

            if sidecar.exists():
                crs_info = None
                try:
                    with open(sidecar, "r", encoding="utf-8") as f:
                        ads_data = json.load(f)
                    band_names = sorted([b.strip() for b in ads_data.get("bands_loaded", [])])
                    _log.info(f"[ScenePrep] Read band_names from ADS: {band_names}")

                    crs_info = ads_data.get("crs")
                except Exception as e:
                    _log.error(f"[ScenePrep] Error reading ADS: {e}")

                if crs_info:
                    try:
                        proj4 = crs_info.get("proj4")
                        wkt = crs_info.get("wkt")
                        if proj4:
                            crs = CRS.from_proj4(proj4)
                        elif wkt:
                            crs = CRS.from_wkt(wkt)
                        else:
                            proj4_str = self._build_proj4_fallback(crs_info)
                            if proj4_str:
                                crs = CRS.from_proj4(proj4_str)
                        geotransform = ads_data.get("geotransform")
                        if geotransform and len(geotransform) == 6:
                            transform = rasterio.transform.Affine(*geotransform)
                        _log.info(f"[ScenePrep] Got CRS: {crs is not None}, transform: {transform is not None}")
                    except Exception as e:
                        _log.error(f"[ScenePrep] Error parsing CRS: {e}")

            _log.info(f"[ScenePrep] After CRS block, band_names={band_names}, crs={crs is not None}, transform={transform is not None}")

            if not band_names:
                _log.info("[ScenePrep] No band_names from ADS, trying NC file")
                self.progress.emit("Opening NC for band list (sidecar missing)...")
                with xr.open_dataset(self.nc_path, engine="netcdf4", mask_and_scale=not self.lazy_nc) as ds:
                    band_names = sorted(v for v in ds.data_vars if (v.startswith("B") and len(v) == 3) or v.startswith("C") or "band" in v.lower())
                    _log.info(f"[ScenePrep] Band names from NC: {band_names}")
                    if not band_names and "Rad" in ds:
                        import re
                        # GOES band mapping to internal Bxx names
                        GOES_BAND_MAP = {
                            'C01': 'B01', 'C02': 'B03', 'C03': 'B02'  # Visible swapped
                        }
                        for i in range(4, 17):
                            GOES_BAND_MAP[f'C{i:02d}'] = f'B{i:02d}'
                            
                        m = re.search(r'C(\d{2})', str(self.nc_path))
                        if m:
                            c_band = f"C{m.group(1)}"
                            band_names = [GOES_BAND_MAP.get(c_band, c_band)]
                            
                        # Extract CRS from GOES projection info
                        try:
                            if 'goes_imager_projection' in ds:
                                proj = ds['goes_imager_projection']
                                h = float(proj.attrs.get('perspective_point_height', 35786023))
                                lon_0 = float(proj.attrs.get('longitude_of_projection_origin', -75))
                                sweep = proj.attrs.get('sweep_angle_axis', 'x')
                                sweep_param = f" +sweep={sweep}" if sweep else ""
                                proj4_str = f"+proj=geos +lon_0={lon_0} +h={h} +x_0=0 +y_0=0 +ellps=WGS84{sweep_param} +units=m +no_defs"
                                crs = CRS.from_proj4(proj4_str)
                                
                                # Extract geotransform from coordinate variables
                                nx = ds.sizes.get('x', 0) or 0
                                ny = ds.sizes.get('y', 0) or 0
                                if 'x' in ds and 'y' in ds:
                                    x = ds['x'].values
                                    y = ds['y'].values
                                    if len(x) > 1 and len(y) > 1:
                                        x_step = float(x[1] - x[0]) if len(x) > 1 else 1.0
                                        y_step = float(y[1] - y[0]) if len(y) > 1 else 1.0
                                        is_pixel_idx = (x_step == 1.0 and float(x[0]) == 0.0) or (x_step == 1.0 and float(x[0]) == 0.5)
                                        if not is_pixel_idx:
                                            abs_max = max(abs(x.min()), abs(x.max()), abs(y.min()),abs(y.max()))
                                            if abs_max > 10000:
                                                x_m = x.astype(float)
                                                y_m = y.astype(float)
                                            else:
                                                x_m = x.astype(float) * h
                                                y_m = y.astype(float) * h
                                            scale_x = float(abs(x_m[1] - x_m[0]))
                                            scale_y = -float(abs(y_m[1] - y_m[0]))
                                            transform = rasterio.transform.from_origin(float(x_m.min()), float(y_m.max()), scale_x, abs(scale_y))
                                if transform is None and nx > 0 and ny > 0:
                                    earth_angular_radius = 0.1518
                                    scale_x = 2.0 * h * earth_angular_radius / nx
                                    x_min = -scale_x * nx / 2.0
                                    y_max = scale_x * ny / 2.0
                                    transform = rasterio.transform.from_origin(x_min, y_max, scale_x, scale_x)
                        except Exception:
                            pass

            ir_kelvin = None
            try:
                self.progress.emit("Loading IR (B13) for temperature readouts...")
                b13_path = self._band_path("B13")
                with xr.open_dataset(b13_path, engine="netcdf4", mask_and_scale=False) as ds:
                    if "B13" in ds:
                        arr = ds["B13"].values.astype(np.float32)
                        h, w = arr.shape
                        max_px = 2200
                        if max(h, w) > max_px:
                            step = max(1, max(h, w) // max_px)
                            new_h = int(np.ceil(h / step))
                            new_w = int(np.ceil(w / step))
                            arr = zoom(arr, (new_h / h, new_w / w), order=1)
                        scale = float(ds["B13"].attrs.get("scale_factor", 1.0))
                        offset = float(ds["B13"].attrs.get("add_offset", 0.0))
                        kelvin = arr * scale + offset
                        kelvin = np.where((kelvin < 150) | (kelvin > 350), np.nan, kelvin)
                        ir_kelvin = kelvin.astype(np.float32)
                _log.info(f"[ScenePrep] IR Kelvin loaded: {ir_kelvin is not None}")
            except Exception as e:
                _log.error(f"[ScenePrep] Failed to load IR Kelvin: {e}")
                self.error.emit(f"Failed to load IR Kelvin: {e}")

            if (crs is None or transform is None) and ads_data:
                try:

                    crs2, trans2 = self._try_extract_crs_from_ads_data(ads_data, self.nc_path)
                    if crs2:
                        crs = crs2
                    if trans2:
                        transform = trans2
                except Exception:
                    pass

            ref_grid_size = None
            ref_grid_1km = None
            ref_grid_0_5km = None
            if ads_data:
                _rw = ads_data.get("ref_grid_w")
                _rh = ads_data.get("ref_grid_h")
                if _rw and _rh:
                    ref_grid_size = (_rw, _rh)
                _rw1 = ads_data.get("ref_grid_1km_w")
                _rh1 = ads_data.get("ref_grid_1km_h")
                if _rw1 and _rh1:
                    ref_grid_1km = (_rw1, _rh1)
                _rw05 = ads_data.get("ref_grid_0_5km_w")
                _rh05 = ads_data.get("ref_grid_0_5km_h")
                if _rw05 and _rh05:
                    ref_grid_0_5km = (_rw05, _rh05)

            result = {
                "nc_path": self.nc_path,
                "band_names": band_names,
                "crs": crs,
                "transform": transform,
                "ir_kelvin": ir_kelvin,
                "ref_grid_size": ref_grid_size,
                "ref_grid_1km": ref_grid_1km,
                "ref_grid_0_5km": ref_grid_0_5km,
            }
            _log.info(f"[ScenePrep] Emitting prepared signal: crs={crs is not None}, transform={transform is not None}, ref_grid={ref_grid_size}, bands={band_names}")
            self.prepared.emit(result)
            _log.info(f"[ScenePrep] Prepared signal emitted")
        except Exception as e:
            _log.error(f"[ScenePrep] Error at end: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))

    def _build_proj4_fallback(self, crs_info):
        try:
            sat_lon = float(crs_info.get("longitude_of_projection_origin", 140.7))
            h = float(crs_info.get("perspective_point_height", 35785863.0))
            a = float(crs_info.get("semi_major_axis", 6378137.0))
            b = float(crs_info.get("semi_minor_axis", 6356752.314140356))
            rf = crs_info.get("inverse_flattening")
            # GOES uses sweep='x' (Western hemisphere, negative longitudes);
            # all other geostationary satellites (Himawari, Meteosat, GK-2A) use sweep='y'
            # Force sweep based on longitude; ignore ads.json which may store 'x' for Himawari
            sweep = "x" if sat_lon < 0 else "y"
            sweep_param = f" +sweep={sweep}" if sweep else ""
            if rf:
                proj4 = f"+proj=geos +lon_0={sat_lon} +h={h} +a={a} +rf={rf}{sweep_param} +units=m +no_defs"
            else:
                proj4 = f"+proj=geos +lon_0={sat_lon} +h={h} +a={a} +b={b}{sweep_param} +units=m +no_defs"
            return proj4
        except Exception:
            return None

    def _try_extract_crs_from_ads_data(self, ads, nc_path):

        try:
            crs_info = ads.get("crs")
            if not crs_info:
                return None, None
            proj4 = crs_info.get("proj4")
            wkt = crs_info.get("wkt")
            if proj4:
                crs = CRS.from_proj4(proj4)
            elif wkt:
                crs = CRS.from_wkt(wkt)
            else:
                proj4_str = self._build_proj4_fallback(crs_info)
                crs = CRS.from_proj4(proj4_str) if proj4_str else None
            transform = None
            geotransform = ads.get("geotransform")
            if geotransform and len(geotransform) == 6:
                transform = rasterio.transform.Affine(*geotransform)

            return crs, transform
        except Exception:
            return None, None


class RawBandCacheWorker(QObject):

    band_cached = Signal(str, object)
    display_ready = Signal(str, object)
    progress    = Signal(int, int)
    finished    = Signal()

    def __init__(self, nc_path, band_names, max_px=2200, priority_band=None, precompute_display=True, band_file_map=None, ref_grid_size=None, target_grid=None):
        super().__init__()
        self.nc_path       = nc_path
        self.band_names    = list(band_names)
        self.max_px        = max_px
        self.priority_band = priority_band
        self._cancelled    = False
        self.precompute_display = precompute_display
        self.band_file_map = band_file_map or {}
        self.ref_grid_size = ref_grid_size
        self.target_grid   = target_grid

    @staticmethod
    def _load_one_standalone(nc_path, band, max_px, band_file_map=None):
        try:
            band_file_map = band_file_map or {}
            file_to_open = band_file_map.get(band, nc_path)
            _log.info(f"_load_one_standalone: band={band}, nc_path={nc_path!r}, file_to_open={file_to_open!r}, exists={Path(file_to_open).exists() if file_to_open else 'N/A'}")
            arr = EmbeddedRGBEngine._read_band_array(file_to_open, band, max_px)
            if arr is None:
                _log.warning(f"No data var for {band} in {file_to_open}")
                return band, None
            _log.info(f"_load_one_standalone: got arr shape={arr.shape}, dtype={arr.dtype}")
            arr = EmbeddedRGBEngine._decimate_array(arr, max_px)
            _log.info(f"_load_one_standalone: after decimate arr shape={arr.shape}")
            return band, arr
        except Exception as e:
            _log.error(f"Exception loading {band} from {nc_path}: {e}")
            import traceback
            traceback.print_exc()
            return band, None

    @staticmethod
    def _build_display(raw):
        try:
            alpha = EmbeddedRGBEngine._earth_mask([raw])
            gray = EmbeddedRGBEngine._linear(raw, None, None, gamma=1.0)
            if alpha.shape != gray.shape:
                alpha = alpha[:gray.shape[0], :gray.shape[1]]
            return np.stack([gray, gray, gray, alpha], axis=-1)
        except Exception as e:
            _log.error(f"RawBandCacheWorker._build_display failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    def run(self):
        if getattr(self, '_running', False):
            return
        self._running = True
        try:
            ordered = []
            if self.priority_band and self.priority_band in self.band_names:
                ordered.append(self.priority_band)
                ordered.extend(b for b in self.band_names if b != self.priority_band)
            else:
                ordered = self.band_names

            total = len(ordered)
            nc_str = str(self.nc_path) if self.nc_path else ""
            _log.info(f"RawBandCacheWorker.run: nc_path={self.nc_path!r}, nc_str={nc_str!r}")
            _log.info(f"RawBandCacheWorker.run: Path(nc_str).exists()={Path(nc_str).exists() if nc_str else 'N/A'}")
            if not nc_str or not Path(nc_str).exists():
                bfm = self.band_file_map or {}
                alt_paths = [str(v) for v in bfm.values() if v]
                alt = alt_paths[0] if alt_paths else None
                if alt and Path(alt).exists():
                    _log.warning(f"RawBandCacheWorker.run: self.nc_path invalid, falling back to {alt}")
                    nc_str = alt
                    self.nc_path = Path(alt)
                else:
                    _log.warning(f"RawBandCacheWorker.run: no valid path found (nc_path={self.nc_path!r}, alt_paths={alt_paths!r}), aborting cache for {ordered}")
                    self.finished.emit()
                    return

            max_workers = max(1, min(os.cpu_count() or 4, total))
            done_count = 0

            bfm = self.band_file_map or {}
            _log.info(f"RawBandCacheWorker.run: bfm={ {k: str(v) for k, v in bfm.items()} }")
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                fut_map = {
                    pool.submit(self._load_one_standalone, nc_str, band, self.max_px, bfm): band
                    for band in ordered
                }

                for fut in as_completed(fut_map):
                    if self._cancelled:
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
                    band, arr = fut.result()
                    done_count += 1
                    if arr is not None:
                        self.band_cached.emit(band, arr)
                        if self.precompute_display:
                            dh, dw = arr.shape[:2]
                            target_h, target_w = dh, dw
                            if self.target_grid is not None:
                                _tw, _th = self.target_grid
                                if _tw > 0 and _th > 0:
                                    target_w, target_h = int(_tw), int(_th)
                            elif self.max_px > 0 and self.ref_grid_size is not None:
                                _rw, _rh = self.ref_grid_size
                                if _rw > 0 and _rh > 0:
                                    target_w, target_h = _rw, _rh
                            elif self.max_px > 0 and max(dw, dh) != self.max_px:
                                _sf = self.max_px / max(dw, dh)
                                target_w = max(1, int(round(dw * _sf)))
                                target_h = max(1, int(round(dh * _sf)))
                            if (target_h != dh or target_w != dw) and target_h > 0 and target_w > 0:
                                zh = target_h / dh
                                zw = target_w / dw
                                arr_resized = zoom(arr, (zh, zw), order=1).astype(arr.dtype)
                            else:
                                arr_resized = arr
                            disp = self._build_display(arr_resized)
                            if disp is not None and not self._cancelled:
                                self.display_ready.emit(band, disp)
                    self.progress.emit(done_count, total)
                if self._cancelled:
                    return
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            self.finished.emit()

    def cancel(self):
        self._cancelled = True


class OverlayPrecacheWorker(QObject):

    grid_ready = Signal(str, list)
    coast_ready = Signal(str, list)
    finished = Signal()

    def __init__(self, crs_proj4, transform_tuple, img_w, img_h,
                 grid_spacing_deg=10, sub_step=0.5,
                 coast_shp_path=None, coast_stride=1):
        super().__init__()
        self.crs_proj4 = crs_proj4
        self.transform_tuple = transform_tuple
        self.img_w = int(img_w)
        self.img_h = int(img_h)
        self.grid_spacing_deg = float(grid_spacing_deg)
        self.coast_stride = max(1, int(coast_stride))
        self.sub_step = float(sub_step)
        self.coast_shp_path = coast_shp_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from pyproj import Transformer
            from rasterio.transform import Affine
            from ..core.helpers import load_border_segments_np, decimate_screen_points
        except Exception:
            self.finished.emit()
            return

        try:
            transformer = Transformer.from_crs("EPSG:4326", self.crs_proj4, always_xy=True)
            inv_transform = ~Affine(*self.transform_tuple)
            try:
                disk_half_extent = abs(self.transform_tuple[2]) * 2
            except Exception:
                disk_half_extent = 5.5e6
            dec_w = self.img_w
            dec_h = self.img_h
            scale = dec_w / 5500.0
            grid_spacing = self.grid_spacing_deg
            sub_step = self.sub_step

            def _project_vector(lons, lats):
                ns = ((np.asarray(lons, dtype=np.float64) + 180.0) % 360.0) - 180.0
                lats_a = np.asarray(lats, dtype=np.float64)
                x_proj, y_proj = transformer.transform(ns, lats_a)
                valid = (np.isfinite(x_proj) & np.isfinite(y_proj)
                         & (np.abs(x_proj) <= disk_half_extent * 1.01)
                         & (np.abs(y_proj) <= disk_half_extent * 1.01))
                cols, rows = inv_transform * (x_proj, y_proj)
                return (np.asarray(cols, dtype=np.float64) * scale,
                        np.asarray(rows, dtype=np.float64) * scale, valid)

            def _entry_from_runs(px, py, valid, min_step_px=1.0):
                out = []
                idx = np.where(valid)[0]
                if len(idx) == 0:
                    return out
                for chunk in np.split(idx, np.where(np.diff(idx) != 1)[0] + 1):
                    run = np.column_stack([px[chunk], py[chunk]])
                    run = decimate_screen_points(run, min_step_px)
                    if len(run) < 2:
                        continue
                    for p in run:
                        out.append((float(p[0]), float(p[1])))
                    out.append(None)
                return out

            def _segments():
                lat_grid = np.arange(-90.0, 90.0 + sub_step * 0.5, sub_step)
                for lon_val in range(-180, 181, int(grid_spacing)):
                    if self._cancelled:
                        return
                    lons = np.full_like(lat_grid, float(lon_val))
                    px, py, valid = _project_vector(lons, lat_grid)
                    entry = _entry_from_runs(px, py, valid)
                    if entry:
                        yield entry
                lon_grid = np.arange(-180.0, 180.0 + sub_step * 0.5, sub_step)
                for lat_val in range(-90, 91, int(grid_spacing)):
                    if self._cancelled:
                        return
                    lats = np.full_like(lon_grid, float(lat_val))
                    px, py, valid = _project_vector(lon_grid, lats)
                    entry = _entry_from_runs(px, py, valid)
                    if entry:
                        yield entry

            grid_paths = list(_segments())
            self.grid_ready.emit("precached", grid_paths)
        except Exception:
            pass

        if self.coast_shp_path and not self._cancelled:
            try:
                coast_segments = []
                # Country-boundary polygon edges (borders layer). Every part is
                # packed as its own segment chain, latitudes are clipped to
                # +/-89.9, and the +360 duplicate wraps the antimeridian.
                for segment in load_border_segments_np(str(self.coast_shp_path), stride=self.coast_stride, double=True):
                    if self._cancelled:
                        break
                    px, py, valid = _project_vector(segment[:, 0], segment[:, 1])
                    entry = _entry_from_runs(px, py, valid)
                    if entry:
                        coast_segments.append(entry)
                self.coast_ready.emit("precached", coast_segments)
            except Exception:
                pass


        self.finished.emit()


class AnimationPrefetchWorker(QObject):

    frame_ready = Signal(int, object)
    band_frame_ready = Signal(int, object)
    progress = Signal(int, int)
    progress_fd = Signal(int, int)
    progress_target = Signal(int, int)
    progress_japan = Signal(int, int)
    finished = Signal()

    def __init__(self, items, engine_class=None, target_item_base=None, item_kinds=None):
        super().__init__()
        self.items = list(items)
        self._cancelled = False
        self._engine = engine_class or EmbeddedRGBEngine
        self._target_item_base = target_item_base
        if item_kinds is not None and len(item_kinds) == len(self.items):
            self._item_kinds = list(item_kinds)
        else:
            tb = self._target_item_base
            self._item_kinds = [
                ('target' if tb is not None and it[0] >= tb else 'fd')
                for it in self.items
            ]

    @staticmethod
    def _display_from_band(_eng, raw, alpha_mask=True):
        """Build a gray RGBA display array from a cached raw band array."""
        if raw is None:
            return None
        gray = _eng._linear(raw, None, None, gamma=1.0)
        if alpha_mask:
            alpha = _eng._earth_mask([raw])
        else:
            alpha = np.full(raw.shape, 255, dtype=np.uint8)
        return np.stack([gray, gray, gray, alpha], axis=-1)

    def run(self):
        total = len(self.items)
        if total == 0:
            self.finished.emit()
            return
        _eng = self._engine

        tb = self._target_item_base
        kinds = self._item_kinds
        fd_total = 0
        tgt_total = 0
        jpn_total = 0
        for k in kinds:
            if k == 'japan':
                jpn_total += 1
            elif k == 'target':
                tgt_total += 1
            else:
                fd_total += 1
        kind_by_seq = {it[0]: k for it, k in zip(self.items, kinds)}

        import traceback as _tb
        _log.info(f"Prefetch: run() started with {len(self.items)} items (full-disk={fd_total}, target={tgt_total}), _cancelled={self._cancelled}, engine={self._engine}")

        def _load_one(item):
            if self._cancelled:
                _log.warning(f"Prefetch: item {item[0]} cancelled immediately")
                return item[0], None, None
            seq_idx, nc_path, checked_bands, active_content, max_px = item[:5]
            band_file_map = item[5] if len(item) > 5 else None
            band_cache = {}
            try:
                if checked_bands:
                    for b in checked_bands:
                        try:
                            file_to_open = nc_path
                            if band_file_map and b in band_file_map:
                                file_to_open = Path(band_file_map[b])
                            raw = _eng._read_band_array(file_to_open, b, max_px=max_px)
                            if raw is not None:
                                raw = _eng._decimate_array(raw, max_px)
                                band_cache[b] = raw
                        except Exception:
                            continue
                ctype, sel = active_content
                if ctype == "PRODUCT":
                    arr = _eng.composite_realtime(sel, nc_path, max_px,
                                                  band_cache=band_cache,
                                                  band_file_map=band_file_map)
                else:
                    raw = band_cache.get(sel)
                    if raw is not None:
                        arr = AnimationPrefetchWorker._display_from_band(_eng, raw)
                    else:
                        arr = _eng.band_as_image(nc_path, sel, max_px, band_file_map=band_file_map)
                _log.info(f"Prefetch: frame {seq_idx} loaded bands={list(band_cache)} active={active_content} "
                          f"arr={'None' if arr is None else 'shape=' + str(arr.shape)}")
                return seq_idx, arr, band_cache
            except Exception:
                _log.error(f"Prefetch: frame {seq_idx} exception: {_tb.format_exc()}")
                return seq_idx, None, band_cache

        max_workers = max(1, min(os.cpu_count() or 4, len(self.items)))
        _log.info(f"Prefetch: max_workers={max_workers}")
        done = 0
        fd_done = 0
        tgt_done = 0
        jpn_done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_load_one, it) for it in self.items]
            for future in as_completed(futures):
                if self._cancelled:
                    _log.warning("Prefetch: cancelled during processing")
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                seq_idx, arr, band_cache = future.result()
                done += 1
                _log.info(f"Prefetch: emitting frame {seq_idx} (done={done}/{total})")
                self.band_frame_ready.emit(seq_idx, band_cache)
                self.frame_ready.emit(seq_idx, arr)
                self.progress.emit(done, total)
                kind = kind_by_seq.get(seq_idx, 'fd')
                if kind == 'japan':
                    jpn_done += 1
                    if jpn_total:
                        self.progress_japan.emit(jpn_done, jpn_total)
                elif kind == 'target' or (tb is not None and seq_idx >= tb):
                    tgt_done += 1
                    if tgt_total:
                        self.progress_target.emit(tgt_done, tgt_total)
                else:
                    fd_done += 1
                    if fd_total:
                        self.progress_fd.emit(fd_done, fd_total)

        _log.info(f"Prefetch: run() done, processed {done}/{total} items")
        self.finished.emit()

    def cancel(self):
        self._cancelled = True


class ExportWorker(QObject):
    progress = Signal(int, int)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, frames, output_path, fps=5, with_footer=False, footer_data=None):
        super().__init__()
        self.frames = frames
        self.output_path = output_path
        self.fps = fps
        self.with_footer = with_footer
        self.footer_data = footer_data
        self._cancelled = False

    def run(self):
        try:
            from ..exporters.animation_exporter import export_animation_direct
            def _cb(cur, tot):
                if self._cancelled:
                    raise InterruptedError("Cancelled")
                self.progress.emit(cur, tot)
            export_animation_direct(
                self.frames, self.output_path, fps=self.fps,
                with_footer=self.with_footer, footer_data=self.footer_data,
                progress_callback=_cb,
            )
            self.finished.emit(str(self.output_path))
        except InterruptedError:
            self.finished.emit("")
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True
