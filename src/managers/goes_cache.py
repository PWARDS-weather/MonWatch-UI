# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: managers/goes_cache.py
# Description: GOES satellite data cache management and retrieval.
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
import re
import json
import hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from scipy.ndimage import zoom
import xarray as xr

# ---- optional dependencies ------------------------------------------------
try:
    import dask.array as da
    HAS_DASK = True
except ImportError:
    HAS_DASK = False

try:
    from numba import njit, prange
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

# ---- constants -------------------------------------------------------------

GOES_BAND_MAP = {
    'C01': 'B01', 'C02': 'B03', 'C03': 'B02',
}
for _i in range(4, 17):
    GOES_BAND_MAP[f'C{_i:02d}'] = f'B{_i:02d}'

INV_GOES_BAND_MAP = {v: k for k, v in GOES_BAND_MAP.items()}

CACHE_DIR_NAME = ".goes_cache"

GOES_BAND_SCALE_HINT = {
    "B01": (0.0, 1.0), "B02": (0.0, 1.0), "B03": (0.0, 1.0),
    "B04": (0.0, 1.0), "B05": (0.0, 1.0), "B06": (0.0, 1.0),
    "B07": (200.0, 320.0), "B08": (200.0, 320.0), "B09": (200.0, 320.0),
    "B10": (200.0, 320.0), "B11": (200.0, 320.0), "B12": (200.0, 320.0),
    "B13": (200.0, 320.0), "B14": (200.0, 320.0), "B15": (200.0, 320.0),
    "B16": (200.0, 320.0),
}

# ---- helpers ---------------------------------------------------------------

def _auto_range(data: np.ndarray, low_pct: float = 1.0, high_pct: float = 98.0):
    valid = data[np.isfinite(data)]
    if len(valid) == 0:
        return None, None
    p_low, p_high = np.percentile(valid, [low_pct, high_pct])
    if p_high - p_low < 1.0:
        return float(valid.min()), float(valid.max())
    return float(p_low), float(p_high)


def _scene_hash(folder: Path) -> str:
    hasher = hashlib.md5()
    for p in sorted(folder.glob("*.nc")):
        hasher.update(p.name.encode())
        try:
            stat = p.stat()
            hasher.update(str(stat.st_size).encode())
            hasher.update(str(stat.st_mtime).encode())
        except Exception:
            pass
    return hasher.hexdigest()[:16]


def _cache_dir_for(folder: Path) -> Path:
    return folder / CACHE_DIR_NAME


def _read_band_array(file_to_open, band, max_px=0):
    with xr.open_dataset(file_to_open, mask_and_scale=False) as ds:
        var_name = band if band in ds else ("Rad" if "Rad" in ds else None)
        if var_name is None:
            return None
        if max_px > 0 and ds[var_name].ndim >= 2:
            _h = ds[var_name].shape[0]
            _w = ds[var_name].shape[1]
            if max(_h, _w) > max_px:
                import math
                _step = max(1, math.ceil(max(_h, _w) / max_px))
                arr = ds[var_name][::_step, ::_step].values.astype(np.float32)
            else:
                arr = ds[var_name].values.astype(np.float32)
        else:
            arr = ds[var_name].values.astype(np.float32)
        fill = ds[var_name].attrs.get('_FillValue')
        if fill is not None:
            arr[arr == fill] = np.nan
        missing = ds[var_name].attrs.get('missing_value')
        if missing is not None:
            arr[arr == missing] = np.nan
        scale = ds[var_name].attrs.get('scale_factor')
        offset = ds[var_name].attrs.get('add_offset')
        if scale is not None and offset is not None:
            arr = np.where(np.isfinite(arr), arr * np.float32(scale) + np.float32(offset), np.nan)
    return arr

def _decimate_array(arr: np.ndarray, max_px: int = 2200) -> np.ndarray:
    if max_px <= 0:
        return arr
    h, w = arr.shape[:2]
    if max(h, w) <= max_px:
        return arr
    step = max(1, int(np.ceil(max(h, w) / max_px)))
    new_h = int(np.ceil(h / step))
    new_w = int(np.ceil(w / step))
    zoom_factors = (new_h / h, new_w / w) + (1,) * max(0, arr.ndim - 2)
    has_nan = np.isnan(arr).any()
    if has_nan:
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
        result = np.where(mask_resized, np.nan, result)
        return result.astype(arr.dtype)
    return zoom(arr, zoom_factors, order=1).astype(arr.dtype)

# ---- disk cache (persistent .npz thumbnails) ------------------------------

class GOESDiskCache:
    """Pre-computed RGBA thumbnails saved as .npz files beside the data folder."""

    def __init__(self, data_folder: Path):
        self.data_folder = Path(data_folder)
        self._cache_dir = _cache_dir_for(data_folder)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _thumb_path(self, band: str, max_px: int) -> Path:
        return self._cache_dir / f"thumb_{band}_{max_px}.npz"

    def has(self, band: str, max_px: int = 2200) -> bool:
        return self._thumb_path(band, max_px).exists()

    def put(self, band: str, rgba: np.ndarray, max_px: int = 2200):
        np.savez_compressed(str(self._thumb_path(band, max_px)), rgba=rgba)

    def get(self, band: str, max_px: int = 2200) -> np.ndarray | None:
        p = self._thumb_path(band, max_px)
        if p.exists():
            return np.load(str(p))["rgba"]
        return None

    def _rgb_path(self, product_key: str, max_px: int) -> Path:
        return self._cache_dir / f"rgb_{product_key}_{max_px}.npz"

    def has_rgb(self, product_key: str, max_px: int = 2200) -> bool:
        return self._rgb_path(product_key, max_px).exists()

    def get_rgb(self, product_key: str, max_px: int = 2200) -> np.ndarray | None:
        p = self._rgb_path(product_key, max_px)
        if p.exists():
            return np.load(str(p))["rgba"]
        return None

    def put_rgb(self, product_key: str, rgba: np.ndarray, max_px: int = 2200):
        np.savez_compressed(str(self._rgb_path(product_key, max_px)), rgba=rgba)

    def purge(self):
        if self._cache_dir.exists():
            import shutil
            shutil.rmtree(str(self._cache_dir))

# ---- metadata cache (CRS + band list per scene) --------------------------

class GOESSceneMeta:
    """Cached CRS, geotransform, and band list for a GOES scene folder."""

    def __init__(self, data_folder: Path):
        self.data_folder = Path(data_folder)
        self._meta_path = _cache_dir_for(data_folder) / "scene_meta.json"
        self._cached = None

    def _load_nc_meta(self) -> dict | None:
        nc_files = sorted(self.data_folder.glob("*.nc"))
        if not nc_files:
            return None
        try:
            with xr.open_dataset(nc_files[0], engine="netcdf4", mask_and_scale=False) as ds:
                meta = {"band_names": []}
                if "Rad" in ds:
                    for f in nc_files:
                        m = re.search(r'C(\d{2})', f.name)
                        if m:
                            internal = GOES_BAND_MAP.get(f"C{m.group(1)}")
                            if internal:
                                meta["band_names"].append(internal)
                    meta["band_names"] = sorted(set(meta["band_names"]))
                else:
                    meta["band_names"] = sorted(
                        v for v in ds.data_vars
                        if (v.startswith("B") and len(v) == 3) or v.startswith("C")
                    )
                if 'goes_imager_projection' in ds:
                    proj = ds['goes_imager_projection']
                    meta["crs"] = {
                        "longitude_of_projection_origin": float(proj.attrs.get('longitude_of_projection_origin', -75.0)),
                        "perspective_point_height": float(proj.attrs.get('perspective_point_height', 35786023)),
                        "sweep_angle_axis": proj.attrs.get('sweep_angle_axis', 'x'),
                    }
                    if 'x' in ds and 'y' in ds:
                        try:
                            x = ds['x'].values
                            y = ds['y'].values
                            if len(x) > 1 and len(y) > 1:
                                x_step = float(x[1] - x[0]) if len(x) > 1 else 1.0
                                is_pixel_idx = (x_step == 1.0 and float(x[0]) == 0.0) or (x_step == 1.0 and float(x[0]) == 0.5)
                                if not is_pixel_idx:
                                    abs_max = max(abs(x.min()), abs(x.max()), abs(y.min()), abs(y.max()))
                                    h_val = meta["crs"]["perspective_point_height"]
                                    if abs_max < 1.0:
                                        x_m = x.astype(float) * h_val
                                        y_m = y.astype(float) * h_val
                                    else:
                                        x_m = x.astype(float)
                                        y_m = y.astype(float)
                                    res_x = float(abs(x_m[1] - x_m[0]))
                                    res_y = float(abs(y_m[1] - y_m[0]))
                                    meta["geotransform"] = [
                                        res_x, 0.0, float(x_m.min()),
                                        0.0, -res_y, float(y_m.max())
                                    ]
                                    # Compute 2km reference geotransform from the same extent
                                    x_extent = float(x_m.max()) - float(x_m.min())
                                    y_extent = float(y_m.max()) - float(y_m.min())
                                    ncols_2km = max(1, int(round(x_extent / 2000.0)))
                                    nrows_2km = max(1, int(round(y_extent / 2000.0)))
                                    meta["ref_grid_w"] = ncols_2km
                                    meta["ref_grid_h"] = nrows_2km
                                    meta["geotransform_2km"] = [
                                        2000.0, 0.0, float(x_m.min()),
                                        0.0, -2000.0, float(y_m.max())
                                    ]
                            if meta.get("geotransform") is None:
                                h_val = meta["crs"]["perspective_point_height"]
                                nx = ds.sizes.get('x', 0) or 0
                                ny = ds.sizes.get('y', 0) or 0
                                if nx > 0 and ny > 0:
                                    earth_angular_radius = 0.1518
                                    res = 2.0 * h_val * earth_angular_radius / nx
                                    meta["geotransform"] = [
                                        res, 0.0, -res * nx / 2.0,
                                        0.0, -res, res * ny / 2.0
                                    ]
                        except Exception:
                            pass
                return meta
        except Exception:
            return None

    def load(self, force=False) -> dict:
        if not force and self._cached is not None:
            return self._cached
        _cache_dir_for(self.data_folder).mkdir(parents=True, exist_ok=True)
        if not force and self._meta_path.exists():
            try:
                with open(self._meta_path, "r", encoding='utf-8') as f:
                    self._cached = json.load(f)
                return self._cached
            except Exception:
                pass
        meta = self._load_nc_meta()
        if meta:
            try:
                with open(self._meta_path, "w", encoding='utf-8') as f:
                    json.dump(meta, f)
            except Exception:
                pass
        self._cached = meta or {}
        return self._cached

    def get_band_names(self) -> list:
        return self.load().get("band_names", [])

    def get_crs_dict(self) -> dict:
        return self.load().get("crs", {})

    def get_geotransform(self) -> list | None:
        gt = self.load().get("geotransform")
        if gt and len(gt) == 6:
            return gt
        return None

    def get_geotransform_2km(self) -> list | None:
        return self.load().get("geotransform_2km")

    def get_ref_grid_size(self) -> tuple | None:
        meta = self.load()
        w = meta.get("ref_grid_w")
        h = meta.get("ref_grid_h")
        if w is not None and h is not None:
            return (w, h)
        return None

# ---- parallel band loader --------------------------------------------------

class GOESBandLoader:
    """Load GOES bands from a folder with parallel I/O and optional dask lazy reads."""

    def __init__(self, data_folder: Path, max_px: int = 2200, use_dask: bool = False, log_func=None):
        self.data_folder = Path(data_folder)
        self.max_px = max_px
        self.use_dask = use_dask and HAS_DASK
        self.log = log_func or (lambda msg: None)
        self._band_nc_map = self._build_file_map()

    def _build_file_map(self) -> dict:
        band_map = {}
        for f in self.data_folder.glob("*_ABI*.nc"):
            m = re.search(r'C(\d{2})', f.name)
            if m:
                internal = GOES_BAND_MAP.get(f"C{m.group(1)}")
                if internal:
                    band_map[internal] = f
        if not band_map:
            for f in self.data_folder.glob("*.nc"):
                m = re.search(r'C(\d{2})', f.name)
                if m:
                    internal = GOES_BAND_MAP.get(f"C{m.group(1)}")
                    if internal:
                        band_map[internal] = f
        return band_map

    def get_file_map(self) -> dict:
        return dict(self._band_nc_map)

    def get_band_names(self) -> list:
        return sorted(self._band_nc_map.keys())

    def load_band(self, band: str) -> np.ndarray | None:
        nc = self._band_nc_map.get(band)
        if nc is None or not nc.exists():
            return None
        try:
            arr = _read_band_array(nc, band, self.max_px)
            if arr is None:
                return None
            return _decimate_array(arr, self.max_px)
        except Exception as e:
            self.log(f"GOES load error {band}: {e}")
            return None

    def load_all_bands(self, bands: list[str] | None = None) -> dict:
        if bands is None:
            bands = self.get_band_names()
        result = {}
        if not bands:
            return result
        with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 4, len(bands))) as pool:
            fut_map = {pool.submit(self.load_band, b): b for b in bands}
            for fut in as_completed(fut_map):
                b = fut_map[fut]
                arr = fut.result()
                if arr is not None:
                    result[b] = arr
        return result

    def load_band_lazy(self, band: str):
        nc = self._band_nc_map.get(band)
        if nc is None or not nc.exists():
            return None
        try:
            ds = xr.open_dataset(nc, mask_and_scale=False, chunks="auto")
            var = band if band in ds else ("Rad" if "Rad" in ds else None)
            if var is None:
                ds.close()
                return None
            arr = da.from_array(ds[var], chunks="auto").astype(np.float32)
            fill = ds[var].attrs.get('_FillValue')
            if fill is not None:
                arr = da.where(arr == fill, np.nan, arr)
            ds.close()
            if self.max_px:
                h, w = arr.shape[:2] if hasattr(arr, 'shape') else (0, 0)
                if max(h, w) > self.max_px:
                    arr = arr.compute() if hasattr(arr, 'compute') else arr
                    step = max(1, int(np.ceil(max(h, w) / self.max_px)))
                    new_h = int(np.ceil(h / step))
                    new_w = int(np.ceil(w / step))
                    arr = zoom(arr, (new_h / h, new_w / w) + (1,) * max(0, arr.ndim - 2), order=1)
            return arr
        except Exception:
            return None

# ---- top-level GOES cache manager -----------------------------------------

class GOESCacheManager:
    """Unified GOES caching: scene metadata, disk thumbnails, parallel band loading,
    and integration with the in-memory RuntimeCacheManager."""

    def __init__(self, data_folder: Path, runtime_cache=None, settings=None, log_func=None, band_nc_map=None):
        self.data_folder = Path(data_folder)
        self.runtime = runtime_cache
        self.settings = settings or {}
        self.log = log_func or (lambda msg: None)
        self.band_nc_map = band_nc_map or {}

        self.meta = GOESSceneMeta(data_folder)
        self.disk = GOESDiskCache(data_folder)

        max_px = self.settings.get("preview_max_px", 2200) if hasattr(self.settings, 'get') else 2200
        if hasattr(self.settings, 'get') and not hasattr(self.settings, 'keys'):
            max_px = 2200
        elif isinstance(self.settings, dict):
            max_px = self.settings.get("preview_max_px", 2200)
        self.loader = GOESBandLoader(data_folder, max_px=max_px,
                                      use_dask=self.settings.get("lazy_load", False) if isinstance(self.settings, dict) else False,
                                      log_func=self.log)

    def get_band_names(self) -> list:
        return self.loader.get_band_names() or self.meta.get_band_names()

    def get_file_map(self) -> dict:
        return self.loader.get_file_map()

    def get_crs(self):
        cd = self.meta.get_crs_dict()
        if cd:
            from pyproj import CRS
            lon0 = cd.get("longitude_of_projection_origin", -75.0)
            h = cd.get("perspective_point_height", 35786023)
            sweep = cd.get("sweep_angle_axis", "x")
            sp = f" +sweep={sweep}" if sweep else ""
            proj4 = f"+proj=geos +lon_0={lon0} +h={h} +x_0=0 +y_0=0 +ellps=WGS84{sp} +units=m +no_defs"
            try:
                return CRS.from_proj4(proj4)
            except Exception:
                return None
        return None

    def get_geotransform(self):
        gt = self.meta.get_geotransform()
        if gt:
            import rasterio
            return rasterio.transform.Affine(*gt)
        return None

    def get_geotransform_2km(self):
        meta = self.meta.load()
        gt = meta.get("geotransform_2km")
        if gt and len(gt) == 6:
            import rasterio
            return rasterio.transform.Affine(*gt)
        return None

    def get_ref_grid_size(self):
        return self.meta.get_ref_grid_size()

    def get_rgb_composite(self, product_key: str, max_px: int = 2200) -> np.ndarray | None:
        if self.runtime:
            qi = self.runtime.get_rgb(product_key)
            if qi is not None:
                buf = bytes(qi.bits())
                arr = np.frombuffer(buf, dtype=np.uint8).reshape(qi.height(), qi.width(), 4)
                return arr
        return self.disk.get_rgb(product_key, max_px)

    def put_rgb_composite(self, product_key: str, rgba: np.ndarray, max_px: int = 2200):
        self.disk.put_rgb(product_key, rgba, max_px)

    def purge_rgb_cache(self):
        import re as _re
        for f in self._cache_dir.glob("rgb_*.npz"):
            try:
                f.unlink()
            except Exception:
                pass

    def ensure_band_cached(self, band: str, cache: bool = True) -> np.ndarray | None:
        _max_px = getattr(self.loader, 'max_px', 2200)
        if cache and self.runtime:
            arr = self.runtime.get_raw(band)
            if arr is not None:
                return arr
        # Try per-band NC file first (fast, already decimated at write time)
        _nc_path = self.band_nc_map.get(band)
        if _nc_path and Path(_nc_path).exists():
            try:
                with xr.open_dataset(_nc_path, engine="netcdf4", mask_and_scale=False) as _ds:
                    if band in _ds:
                        arr = _ds[band].values.astype(np.float32)
                        if _max_px > 0:
                            arr = _decimate_array(arr, _max_px)
                        if cache and self.runtime:
                            self.runtime.put_raw(band, arr)
                        return arr
            except Exception:
                pass
        arr = self.loader.load_band(band)
        if arr is not None and cache and self.runtime:
            self.runtime.put_raw(band, arr)
        return arr

    def _get_esun(self, band: str) -> float:
        """Read esun from the NC file for a given band, or return 0 if unavailable."""
        try:
            fm = self.loader.get_file_map() if hasattr(self, 'loader') else {}
            fp = fm.get(band)
            if fp is not None and fp.exists():
                with xr.open_dataset(fp, mask_and_scale=False) as ds:
                    if "esun" in ds:
                        return float(ds["esun"].values)
        except Exception:
            pass
        return 0.0

    def _read_doy(self) -> int:
        """Read day-of-year from first NC filename in the data folder."""
        import re
        try:
            for f in sorted(self.data_folder.glob("*.nc")):
                m = re.search(r'_s(\d{4})(\d{3})', f.name)
                if m:
                    return int(m.group(2))
        except Exception:
            pass
        return 166

    def _compute_reflectance(self, raw: np.ndarray, band: str, esun: float, doy: int) -> np.ndarray:
        """Convert L1b radiance to reflectance for VIS bands."""
        import math
        if esun <= 0:
            return raw
        d = 1.0 / math.sqrt(1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0))
        pi = math.pi
        return np.where(np.isfinite(raw), (pi * raw) / (esun * d * d), np.nan)

    def _get_planck(self, band: str):
        """Read planck_fk1 and planck_fk2 from the NC file for a given band."""
        try:
            fm = self.loader.get_file_map() if hasattr(self, 'loader') else {}
            fp = fm.get(band)
            if fp is not None and fp.exists():
                with xr.open_dataset(fp, mask_and_scale=False) as ds:
                    fk1 = float(ds["planck_fk1"].values) if "planck_fk1" in ds else 0.0
                    fk2 = float(ds["planck_fk2"].values) if "planck_fk2" in ds else 0.0
                    return fk1, fk2
        except Exception:
            pass
        return 0.0, 0.0

    def ensure_display_ready(self, band: str) -> np.ndarray | None:
        _max_px = getattr(self.loader, 'max_px', 2200)
        arr = self.disk.get(band, max_px=_max_px)
        if arr is None:
            raw = self.ensure_band_cached(band, cache=False)
            if raw is None:
                return None
            esun = self._get_esun(band)
            doy = self._read_doy()
            fk1, fk2 = self._get_planck(band)
            arr = self._build_display(raw, band=band, esun=esun, doy=doy,
                                      planck_fk1=fk1, planck_fk2=fk2)
            if arr is not None:
                self.disk.put(band, arr, max_px=_max_px)
        return arr

    def preload_scene(self, bands: list[str] | None = None, precompute_display=True) -> dict:
        if bands is None:
            bands = self.get_band_names()
        raw_map = self.loader.load_all_bands(bands)
        result = {}
        doy = self._read_doy()
        for band, raw in raw_map.items():
            if raw is None:
                continue
            if self.runtime:
                self.runtime.put_raw(band, raw)
            if precompute_display:
                esun = self._get_esun(band)
                fk1, fk2 = self._get_planck(band)
                disp = self._build_display(raw, band=band, esun=esun, doy=doy,
                                           planck_fk1=fk1, planck_fk2=fk2)
                if disp is not None:
                    if self.runtime:
                        self.runtime.put_precached(band, disp)
                    self.disk.put(band, disp, max_px=getattr(self.loader, 'max_px', 2200))
                    result[band] = disp
                else:
                    result[band] = raw
            else:
                result[band] = raw
        return result

    def load_band_for_display(self, band: str) -> np.ndarray | None:
        _max_px = getattr(self.loader, 'max_px', 2200)
        if self.runtime:
            arr = self.runtime.get_precached(band)
            if arr is not None:
                return arr
        arr = self.disk.get(band, max_px=_max_px)
        if arr is None:
            raw = self.ensure_band_cached(band)
            if raw is None:
                return None
            esun = self._get_esun(band)
            doy = self._read_doy()
            fk1, fk2 = self._get_planck(band)
            arr = self._build_display(raw, band=band, esun=esun, doy=doy,
                                      planck_fk1=fk1, planck_fk2=fk2)
            if arr is not None:
                self.disk.put(band, arr, max_px=_max_px)
        if arr is not None and self.runtime:
            self.runtime.put_precached(band, arr)
        return arr

    @staticmethod
    def _build_display(raw: np.ndarray, band: str = None,
                       esun: float = 0.0, doy: int = 166,
                       planck_fk1: float = 0.0, planck_fk2: float = 0.0) -> np.ndarray:
        try:
            from ..core.Engine import EmbeddedRGBEngine
            import math
            data = raw.copy()
            vmin, vmax = None, None
            if band:
                band_num = int(band.replace("B", ""))
                if 1 <= band_num <= 6:
                    if esun > 0.0:
                        d = 1.0 / math.sqrt(1.0 + 0.033 * math.cos(2.0 * math.pi * doy / 365.0))
                        pi = math.pi
                        data = np.where(np.isfinite(data), (pi * data) / (esun * d * d), np.nan)
                    vmin, vmax = _auto_range(data)
                    if vmin is None:
                        vmin, vmax = 0.0, 100.0
                elif 7 <= band_num <= 16:
                    if planck_fk1 > 0.0 and planck_fk2 > 0.0:
                        data = np.where(np.isfinite(data) & (data > 0),
                                        planck_fk2 / np.log(planck_fk1 / data + 1.0),
                                        np.nan)
                    vmin, vmax = _auto_range(data)
                    if vmin is None:
                        vmin, vmax = 180.0, 330.0
            alpha = EmbeddedRGBEngine._earth_mask([data])
            gray = EmbeddedRGBEngine._linear(data, vmin, vmax, gamma=1.0)
            if alpha.shape != gray.shape:
                alpha = alpha[:gray.shape[0], :gray.shape[1]]
            return np.stack([gray, gray, gray, alpha], axis=-1)
        except Exception:
            return None

    def purge_disk_cache(self):
        self.disk.purge()

# ---- convenience: detect GOES folder --------------------------------------

def is_goes_folder(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for f in folder.glob("*_ABI*.nc"):
        if re.search(r'C(\d{2})', f.name):
            return True
    for f in folder.glob("*.nc"):
        if re.search(r'C(\d{2})', f.name):
            return True
    return False


def is_gk2a_folder(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for f in folder.glob("gk2a_*.nc"):
        return True
    for f in folder.glob("*.nc"):
        try:
            with xr.open_dataset(f, engine="netcdf4") as ds:
                sat = str(ds.attrs.get("satellite_name", "")).lower()
                if "gk-2a" in sat or "gk2a" in sat:
                    return True
                inst = str(ds.attrs.get("instrument", "")).lower()
                if "ami" in inst:
                    return True
        except Exception:
            pass
    return False


def is_meteosat_folder(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for f in folder.glob("*.nc"):
        try:
            with xr.open_dataset(f, engine="netcdf4") as ds:
                inst = str(ds.attrs.get("institution", "")).lower()
                if "eumetsat" in inst:
                    return True
                sat = str(ds.attrs.get("satellite_name", "")).lower()
                if "meteosat" in sat or "msg" in sat:
                    return True
        except Exception:
            pass
        name = f.name.lower()
        if "meteosat" in name or "msg" in name or "seviri" in name:
            return True
    return False
