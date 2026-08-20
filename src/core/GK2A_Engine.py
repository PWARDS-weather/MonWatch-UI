# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: core/GK2A_Engine.py
# Description: Embedded RGB computation engine with NumPy/CuPy backend for satellite band compositing.
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

import re
import numpy as np
import xarray as xr
from scipy.ndimage import zoom
from pathlib import Path
import struct
import bz2
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    import cupy as _cp_module
    HAS_CUPY_IMPORT = True
except ImportError:
    _cp_module = None
    HAS_CUPY_IMPORT = False

try:
    from numba import njit, prange
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False

HAS_CUPY = False
cp = None
xp = np

_current_backend = "numpy"
_GPU_FORCE_DISABLE = False

def set_compute_backend(use_gpu: bool):
    global xp, cp, HAS_CUPY, _current_backend, _GPU_FORCE_DISABLE
    if not use_gpu:
        xp = np
        cp = None
        HAS_CUPY = False
        _current_backend = "numpy"
        _GPU_FORCE_DISABLE = True
        return False
    if use_gpu and HAS_CUPY_IMPORT and _cp_module is not None:
        try:
            test = _cp_module.array([1.0, 2.0, 3.0])
            _ = float(_cp_module.sum(test))
            if _cp_module.cuda.runtime.getDeviceCount() > 0:
                test2 = _cp_module.array([1.0, 2.0])
                _ = float(_cp_module.clip(test2, 0.0, 1.5).sum())
                cp = _cp_module
                HAS_CUPY = True
                xp = cp
                _current_backend = "cupy"
                _GPU_FORCE_DISABLE = False
                return True
        except Exception:
            pass
    xp = np
    cp = None
    HAS_CUPY = False
    _current_backend = "numpy"
    _GPU_FORCE_DISABLE = False
    return False

def get_current_backend():
    return _current_backend

from .GK2A_Products import RGB_PRODUCTS


if HAS_NUMBA:
    @njit(cache=True, nogil=True)
    def _numba_linear_nogpu(data, vmin, vmax, gamma, invert):
        data = data.astype(np.float64)
        out = np.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
        if gamma != 1.0:
            out = np.power(out, 1.0 / gamma)
        byte = (out * 255).astype(np.uint8)
        if invert:
            result = (255 - byte).astype(np.uint8)
        else:
            result = byte.astype(np.uint8)
        return result

    @njit(cache=True, nogil=True, parallel=True)
    def _numba_earth_mask_nogpu(arrays_data, h, w):
        alpha = np.full((h, w), 255, dtype=np.uint8)
        for arr in arrays_data:
            for y in prange(h):
                for x in prange(w):
                    if y < arr.shape[0] and x < arr.shape[1] and np.isnan(arr[y, x]):
                        alpha[y, x] = 0
        return alpha

    @njit(cache=True, nogil=True)
    def _numba_point_in_polygon(lon, lat, polygon):
        n = len(polygon)
        inside = False
        p1x = polygon[0][0]
        p1y = polygon[0][1]
        for i in range(n):
            p2x = polygon[i % n][0]
            p2y = polygon[i % n][1]
            if lat > min(p1y, p2y):
                if lat <= max(p1y, p2y):
                    if lon <= max(p1x, p2x):
                        if p1y != p2y:
                            xinters = (lat - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                        if p1x == p2x or lon <= xinters:
                            inside = not inside
            p1x, p1y = p2x, p2y
        return inside
else:
    def _numba_point_in_polygon(lon, lat, polygon):
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


# ── HSD Reader Helpers (copied from bg_to_nc.py) ──────────────────────────────

_HSD_BASIC_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("total_number_of_hblocks", "<u2"), ("byte_order", "u1"),
    ("satellite", "S16"), ("proc_center_name", "S16"),
    ("observation_area", "S4"), ("other_observation_info", "S2"),
    ("observation_timeline", "<u2"), ("observation_start_time", "f8"),
    ("observation_end_time", "f8"), ("file_creation_time", "f8"),
    ("total_header_length", "<u4"), ("total_data_length", "<u4"),
    ("quality_flag1", "u1"), ("quality_flag2", "u1"),
    ("quality_flag3", "u1"), ("quality_flag4", "u1"),
    ("file_format_version", "S32"), ("file_name", "S128"),
    ("spare", "S40"),
])
_HSD_DATA_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("number_of_bits_per_pixel", "<u2"),
    ("number_of_columns", "<u2"), ("number_of_lines", "<u2"),
    ("compression_flag_for_data", "u1"), ("spare", "S40"),
])
_HSD_PROJ_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("sub_lon", "f8"), ("CFAC", "<u4"), ("LFAC", "<u4"),
    ("COFF", "f4"), ("LOFF", "f4"),
    ("distance_from_earth_center", "f8"),
    ("earth_equatorial_radius", "f8"), ("earth_polar_radius", "f8"),
    ("req2_rpol2_req2", "f8"), ("rpol2_req2", "f8"),
    ("req2_rpol2", "f8"), ("coeff_for_sd", "f8"),
    ("resampling_types", "<i2"), ("resampling_size", "<i2"),
    ("spare", "S40"),
])
_HSD_NAV_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("navigation_info_time", "f8"), ("SSP_longitude", "f8"),
    ("SSP_latitude", "f8"), ("distance_earth_center_to_satellite", "f8"),
    ("nadir_longitude", "f8"), ("nadir_latitude", "f8"),
    ("sun_position", "f8", (3,)), ("moon_position", "f8", (3,)),
    ("spare", "S40"),
])
_HSD_CAL_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("band_number", "<u2"), ("central_wave_length", "f8"),
    ("valid_number_of_bits_per_pixel", "<u2"),
    ("count_value_error_pixels", "<u2"),
    ("count_value_outside_scan_pixels", "<u2"),
    ("gain_count2rad_conversion", "f8"),
    ("offset_count2rad_conversion", "f8"),
])
_HSD_IRCAL = np.dtype([
    ("c0_rad2tb_conversion", "f8"), ("c1_rad2tb_conversion", "f8"),
    ("c2_rad2tb_conversion", "f8"), ("c0_tb2rad_conversion", "f8"),
    ("c1_tb2rad_conversion", "f8"), ("c2_tb2rad_conversion", "f8"),
    ("speed_of_light", "f8"), ("planck_constant", "f8"),
    ("boltzmann_constant", "f8"), ("spare", "S40"),
])
_HSD_VISCAL = np.dtype([
    ("coeff_rad2albedo_conversion", "f8"),
    ("coeff_update_time", "f8"),
    ("cali_gain_count2rad_conversion", "f8"),
    ("cali_offset_count2rad_conversion", "f8"),
    ("spare", "S80"),
])
_HSD_INTERCAL = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("gsics_calibration_intercept", "f8"),
    ("gsics_calibration_slope", "f8"),
    ("gsics_calibration_coeff_quadratic_term", "f8"),
    ("gsics_std_scn_radiance_bias", "f8"),
    ("gsics_std_scn_radiance_bias_uncertainty", "f8"),
    ("gsics_std_scn_radiance", "f8"),
    ("gsics_correction_starttime", "f8"),
    ("gsics_correction_endtime", "f8"),
    ("gsics_radiance_validity_upper_lim", "f4"),
    ("gsics_radiance_validity_lower_lim", "f4"),
    ("gsics_filename", "S128"), ("spare", "S56"),
])
_HSD_SEGMENT_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("total_number_of_segments", "u1"),
    ("segment_sequence_number", "u1"),
    ("first_line_number_of_image_segment", "u2"), ("spare", "S40"),
])
_HSD_NAVCORR_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("center_column_of_rotation", "f4"),
    ("center_line_of_rotation", "f4"),
    ("amount_of_rotational_correction", "f8"),
    ("numof_correction_info_data", "<u2"),
])
_HSD_NAVCORR_SUB = np.dtype([
    ("line_number_after_rotation", "<u2"),
    ("shift_amount_for_column_direction", "f4"),
    ("shift_amount_for_line_direction", "f4"),
])
_HSD_OBS_TIME_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"),
    ("number_of_observation_times", "<u2"),
])
_HSD_OBS_LINE_TIME = np.dtype([
    ("line_number", "<u2"), ("observation_time", "f8"),
])
_HSD_ERROR_INFO = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u4"),
    ("number_of_error_info_data", "<u2"),
])
_HSD_ERROR_LINE = np.dtype([
    ("line_number", "<u2"), ("numof_error_pixels_per_line", "<u2"),
])
_HSD_SPARE = np.dtype([
    ("hblock_number", "u1"), ("blocklength", "<u2"), ("spare", "S256"),
])

def _read_np_field(val, idx=0):
    if hasattr(val, "item"): return val.item()
    if isinstance(val, np.ndarray):
        return val.flat[idx].item() if val.size > idx else 0.0
    return float(val) if val is not None else 0.0

def _sanitize_np_struct(struct_arr):
    d = {}
    for name in struct_arr.dtype.names:
        val = struct_arr[name]
        if val.size == 1:
            v = val.flat[0]
            if isinstance(v, bytes): v = v.decode("utf-8", errors="replace").strip()
            elif hasattr(v, "item"): v = v.item()
            d[name] = v
        else:
            d[name] = [x.item() if hasattr(x, "item") else str(x) for x in val.flat]
    return d

def _parse_full_hsd_header(filepath: Path) -> Optional[dict]:
    is_bz2 = filepath.suffix.lower() == ".bz2"
    temp_path = None
    try:
        if is_bz2:
            with bz2.open(filepath, "rb") as f: raw = f.read()
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".hsd")
            temp_path = Path(temp.name)
            temp.write(raw)
            temp.close()
            fpath = str(temp_path)
        else: fpath = str(filepath)
        with open(fpath, "rb") as f:
            b1 = np.fromfile(f, dtype=_HSD_BASIC_INFO, count=1)
            if b1.size == 0: return None
            b1_len = int(b1["blocklength"].item())
            f.seek(b1_len)
            b2 = np.fromfile(f, dtype=_HSD_DATA_INFO, count=1)
            if b2.size == 0: return None
            b2_len = int(b2["blocklength"].item())
            f.seek(b1_len + b2_len)
            b3 = np.fromfile(f, dtype=_HSD_PROJ_INFO, count=1)
            if b3.size == 0: return None
            b3_len = int(b3["blocklength"].item())
            f.seek(b1_len + b2_len + b3_len)
            b4 = np.fromfile(f, dtype=_HSD_NAV_INFO, count=1)
            if b4.size == 0: return None
            b4_len = int(b4["blocklength"].item())
            f.seek(b1_len + b2_len + b3_len + b4_len)
            b5 = np.fromfile(f, dtype=_HSD_CAL_INFO, count=1)
            if b5.size == 0: return None
            band_num = int(b5["band_number"].item())
            b5_len = int(b5["blocklength"].item())
            f.seek(b1_len + b2_len + b3_len + b4_len)
            if band_num in set(range(1, 7)):
                cal_ext = np.fromfile(f, dtype=_HSD_VISCAL, count=1)
                cal_type = "VIS"
                data_offset = b1_len + b2_len + b3_len + b4_len + b5_len
            else:
                f.seek(b1_len + b2_len + b3_len + b4_len + b5_len)
                cal_ext = np.fromfile(f, dtype=_HSD_IRCAL, count=1)
                cal_type = "IR"
                data_offset = b1_len + b2_len + b3_len + b4_len + b5_len + int(_HSD_IRCAL.itemsize)
            f.seek(data_offset)
            b6 = np.fromfile(f, dtype=_HSD_INTERCAL, count=1)
            if b6.size == 0: return None
            b6_len = int(b6["blocklength"].item())
            data_offset += b6_len
            f.seek(data_offset)
            b7 = np.fromfile(f, dtype=_HSD_SEGMENT_INFO, count=1)
            if b7.size == 0: return None
            b7_len = int(b7["blocklength"].item())
            data_offset += b7_len
            f.seek(data_offset)
            b8 = np.fromfile(f, dtype=_HSD_NAVCORR_INFO, count=1)
            if b8.size == 0: return None
            b8_len = int(b8["blocklength"].item())
            data_offset += b8_len
            f.seek(data_offset)
            b9 = np.fromfile(f, dtype=_HSD_OBS_TIME_INFO, count=1)
            if b9.size == 0: return None
            b9_len = int(b9["blocklength"].item())
            data_offset += b9_len
            f.seek(data_offset)
            b10 = np.fromfile(f, dtype=_HSD_ERROR_INFO, count=1)
            if b10.size == 0: return None
            b10_len = int(b10["blocklength"].item())
            data_offset += b10_len
            f.seek(data_offset)
            b11 = np.fromfile(f, dtype=_HSD_SPARE, count=1)
            if b11.size == 0: return None
            b11_len = int(b11["blocklength"].item())
            data_offset += b11_len
            ncols = int(b2["number_of_columns"].item())
            nlines = int(b2["number_of_lines"].item())
            sat = _read_np_field(b1["satellite"])
            sat_name = sat.decode("utf-8", errors="replace").strip() if isinstance(sat, bytes) else str(sat).strip()
            obs_area = _read_np_field(b1["observation_area"])
            obs_area = obs_area.decode("utf-8", errors="replace").strip() if isinstance(obs_area, bytes) else str(obs_area).strip()
            def _mjd_to_dt(mjd): return datetime(1858, 11, 17) + timedelta(days=float(mjd))
            obs_start = _mjd_to_dt(b1["observation_start_time"].item())
            obs_end = _mjd_to_dt(b1["observation_end_time"].item())
            hdr = {
                "block1": _sanitize_np_struct(b1), "block2": _sanitize_np_struct(b2),
                "block3": _sanitize_np_struct(b3), "block4": _sanitize_np_struct(b4),
                "block5": _sanitize_np_struct(b5), "calibration_ext": _sanitize_np_struct(cal_ext),
                "cal_type": cal_type, "block6": _sanitize_np_struct(b6),
                "block7": _sanitize_np_struct(b7), "block8": _sanitize_np_struct(b8),
                "block9": _sanitize_np_struct(b9), "block10": _sanitize_np_struct(b10),
                "block11": _sanitize_np_struct(b11), "data_offset": int(data_offset),
                "data_shape": (int(nlines), int(ncols)), "satellite_name": sat_name,
                "observation_area": obs_area, "band_number": int(band_num),
                "sub_lon": float(b3["sub_lon"].item()), "coff": float(b3["COFF"].item()),
                "loff": float(b3["LOFF"].item()), "cfac": int(b3["CFAC"].item()),
                "lfac": int(b3["LFAC"].item()), "earth_equatorial_radius": float(b3["earth_equatorial_radius"].item()) * 1000.0,
                "earth_polar_radius": float(b3["earth_polar_radius"].item()) * 1000.0,
                "distance_from_earth_center": float(b3["distance_from_earth_center"].item()),
                "nbits_per_pixel": int(b2["number_of_bits_per_pixel"].item()),
                "segment_number": int(b7["segment_sequence_number"].item()),
                "total_segments": int(b7["total_number_of_segments"].item()),
                "observation_start_time": obs_start.isoformat(), "observation_end_time": obs_end.isoformat(),
                "gain_count2rad": float(b5["gain_count2rad_conversion"].item()),
                "offset_count2rad": float(b5["offset_count2rad_conversion"].item()),
                "central_wavelength": float(b5["central_wave_length"].item()),
            }
            if cal_type == "VIS":
                hdr["albedo_coeff"] = float(cal_ext["coeff_rad2albedo_conversion"].item())
                hdr["cali_gain"] = float(cal_ext["cali_gain_count2rad_conversion"].item())
                hdr["cali_offset"] = float(cal_ext["cali_offset_count2rad_conversion"].item())
            else:
                hdr["c0_rad2tb"] = float(cal_ext["c0_rad2tb_conversion"].item())
                hdr["c1_rad2tb"] = float(cal_ext["c1_rad2tb_conversion"].item())
                hdr["c2_rad2tb"] = float(cal_ext["c2_rad2tb_conversion"].item())
                hdr["speed_of_light"] = float(cal_ext["speed_of_light"].item())
                hdr["planck_constant"] = float(cal_ext["planck_constant"].item())
                hdr["boltzmann_constant"] = float(cal_ext["boltzmann_constant"].item())
            return hdr
    except Exception: return None
    finally:
        if temp_path and temp_path.exists(): temp_path.unlink()

def _read_hsd_band_data(filepath: Path, hdr: dict) -> Optional[np.ndarray]:
    if hdr is None: return None
    is_bz2 = filepath.suffix.lower() == ".bz2"
    nlines, ncols = hdr["data_shape"]
    offset = hdr["data_offset"]
    try:
        if is_bz2:
            with bz2.open(filepath, "rb") as f: raw = f.read()
            buf = np.frombuffer(raw, dtype=np.uint8, offset=offset)
            data = buf.view(dtype="<u2").reshape(nlines, ncols).astype(np.float32)
        else:
            if nlines * ncols > 50_000_000:
                mm = np.memmap(str(filepath), dtype="<u2", mode="r", offset=offset, shape=(nlines, ncols))
                data = mm.astype(np.float32)
                del mm
            else:
                with open(str(filepath), "rb") as f:
                    f.seek(offset)
                    data = np.fromfile(f, dtype="<u2", count=nlines * ncols).reshape(nlines, ncols).astype(np.float32)
        return data
    except Exception: return None

# Module-level SZA cache keyed by (scene_path, h, w) so composites skip re-computing
# solar geometry every time a product is generated. Bounded; cleared when full.
_SZA_CACHE = {}


class EmbeddedRGBEngine:

    @staticmethod
    def _active_xp():
        if _GPU_FORCE_DISABLE or not HAS_CUPY or cp is None:
            return np
        return cp

    @staticmethod
    def _to_xp(arr: np.ndarray):
        if _GPU_FORCE_DISABLE or not HAS_CUPY or cp is None:
            return np.asarray(arr)
        try:
            return cp.asarray(arr)
        except Exception:
            return np.asarray(arr)

    @staticmethod
    def _to_np(arr) -> np.ndarray:
        if _GPU_FORCE_DISABLE or not HAS_CUPY or cp is None:
            return np.asarray(arr)
        if isinstance(arr, cp.ndarray):
            try:
                return cp.asnumpy(arr)
            except Exception:
                return np.asarray(arr)
        return np.asarray(arr)

    @staticmethod
    def _linear(arr: np.ndarray, vmin, vmax,
                gamma: float = 1.0, invert: bool = False,
                brightness: float = 0.0, contrast: float = 1.0) -> np.ndarray:
        use_xp = EmbeddedRGBEngine._active_xp()
        data = EmbeddedRGBEngine._to_xp(arr).astype(use_xp.float32)
        data = use_xp.nan_to_num(data, nan=0.0)
        if vmin is None:
            vmin = float(use_xp.min(data))
        if vmax is None:
            vmax = float(use_xp.max(data))
        if vmax <= vmin:
            vmax = vmin + 1e-6
        if HAS_NUMBA and (not HAS_CUPY or _GPU_FORCE_DISABLE):
            out = _numba_linear_nogpu(np.asarray(data), vmin, vmax, gamma, invert)
            return out
        out = use_xp.clip((data - vmin) / (vmax - vmin), 0.0, 1.0)
        if gamma != 1.0:
            out = use_xp.power(out, 1.0 / gamma)
        if brightness != 0.0 or contrast != 1.0:
            out = use_xp.clip((out - 0.5) * contrast + 0.5 + brightness, 0.0, 1.0)
        byte = (out * 255).astype(use_xp.uint8)
        result = (255 - byte).astype(use_xp.uint8) if invert else byte
        return EmbeddedRGBEngine._to_np(result)

    @staticmethod
    def _earth_mask(arrays: list) -> np.ndarray:
        use_xp = EmbeddedRGBEngine._active_xp()
        h, w = arrays[0].shape[:2]
        if HAS_NUMBA and (not HAS_CUPY or _GPU_FORCE_DISABLE):
            np_arrays = [np.asarray(a) for a in arrays]
            return _numba_earth_mask_nogpu(np_arrays, h, w)
        alpha = use_xp.full((h, w), 255, dtype=use_xp.uint8)
        for arr in arrays:
            a = EmbeddedRGBEngine._to_xp(arr).astype(use_xp.float32)
            alpha[use_xp.isnan(a)] = 0
        return EmbeddedRGBEngine._to_np(alpha)

    @staticmethod
    def _attach_alpha(rgb_u8: np.ndarray, alpha: np.ndarray) -> np.ndarray:
        h, w = rgb_u8.shape[:2]
        return np.concatenate([rgb_u8, alpha[:h, :w, np.newaxis]], axis=-1)

    @staticmethod
    def _decimate_array(arr: np.ndarray, max_px: int = 2200) -> np.ndarray:
        h, w = arr.shape[:2]
        if max_px <= 0 or max(h, w) <= max_px:
            return arr
        import math
        step = max(1, math.ceil(max(h, w) / max_px))
        new_h = math.ceil(h / step)
        new_w = math.ceil(w / step)
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

    @staticmethod
    def _read_band_array(file_to_open, band, max_px=0):
        from src.core.nc_lock import netcdf_read_lock
        with netcdf_read_lock:
            return EmbeddedRGBEngine._read_band_array_locked(file_to_open, band, max_px)

    @staticmethod
    def _read_band_array_locked(file_to_open, band, max_px=0):
        # Handle .dat / .DAT files (HSD format)
        if isinstance(file_to_open, Path) and file_to_open.suffix.lower() == ".dat":
            hdr = _parse_full_hsd_header(file_to_open)
            if hdr is None:
                return None
            if hdr.get("band_number") != int(band.replace("B", "")):
                return None
            arr = _read_hsd_band_data(file_to_open, hdr)
            if arr is None:
                return None
            return arr

        with xr.open_dataset(file_to_open, mask_and_scale=False) as ds:
            var_name = band if band in ds else None
            if var_name is None:
                for candidate in ["Rad", "image_pixel_values", "data", "radiance"]:
                    if candidate in ds:
                        var_name = candidate
                        break
            if var_name is None:
                name_lower = str(file_to_open).lower()
                from src.parsers.gk2a_parser import GK2A_BAND_MAP, INV_GK2A_BAND_MAP
                from src.parsers.meteosat_parser import METEOSAT_BAND_MAP, INV_METEOSAT_BAND_MAP
                expected_code = INV_GK2A_BAND_MAP.get(band)
                if expected_code:
                    for var in ds.data_vars:
                        if expected_code in var.lower():
                            var_name = var
                            break
                if var_name is None:
                    expected_code = INV_METEOSAT_BAND_MAP.get(band)
                    if expected_code:
                        for var in ds.data_vars:
                            if var.upper() == expected_code or expected_code in var.upper():
                                var_name = var
                                break
                if var_name is None:
                    # JAXA Himawari format: albedo_XX / tbb_XX
                    _bm = re.match(r'B(\d{2})', band)
                    if _bm:
                        _bn = int(_bm.group(1))
                        _candidate = f"albedo_{_bm.group(1)}" if _bn <= 6 else f"tbb_{_bm.group(1)}"
                        if _candidate in ds:
                            var_name = _candidate
            if var_name is None:
                for var in ds.data_vars:
                    if ds[var].ndim >= 2:
                        var_name = var
                        break
            if var_name is None:
                return None
            if max_px > 0 and ds[var_name].ndim >= 2:
                _h = ds[var_name].shape[0]
                _w = ds[var_name].shape[1]
                if max(_h, _w) > max_px:
                    import math
                    _step = max(1, math.ceil(max(_h, _w) / max_px))
                    arr = ds[var_name][::_step, ::_step].values
                else:
                    arr = ds[var_name].values
            else:
                arr = ds[var_name].values

            # GK-2A NetCDF calibration - do QA stripping on raw integer data first
            if "image_pixel_values" in ds or ds.attrs.get("satellite_name", "").lower().startswith("gk-2a"):
                try:
                    from src.parsers.gk2a_parser import IR_BANDS
                except ImportError:
                    IR_BANDS = {"B07", "B08", "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"}
                band_name = ds[var_name].attrs.get("channel_name", band)
                is_ir = band in IR_BANDS or band_name.startswith(("IR", "WV", "SW"))
                qa_bits = ds[var_name].attrs.get("number_of_data_quality_flag_bits_per_pixel", 2)
                if qa_bits > 0 and arr.dtype.kind in ("u", "i"):
                    # Extract QA flags before shifting
                    qa_mask = arr & ((1 << qa_bits) - 1)
                    # QA flag meanings: 0=good, 1=conditionally_usable, 2=out_of_scan_area, 3=error
                    # Set out-of-scan-area (2) and error (3) pixels to NaN
                    bad_qa = (qa_mask >= 2)
                    arr = (arr >> qa_bits).astype(np.float32)
                    arr[bad_qa] = np.nan
                else:
                    arr = arr.astype(np.float32)

                # JAXA / MTG FCI int16 sentinel
                arr[arr == -32768.0] = np.nan
                fill_val = ds[var_name].attrs.get('_FillValue')
                if fill_val is not None:
                    arr[arr == fill_val] = np.nan
                missing_val = ds[var_name].attrs.get('missing_value')
                if missing_val is not None:
                    arr[arr == missing_val] = np.nan

                gain = float(ds.attrs.get("DN_to_Radiance_Gain", 1.0))
                offset = float(ds.attrs.get("DN_to_Radiance_Offset", 0.0))
                if is_ir:
                    c0 = ds.attrs.get("Teff_to_Tbb_c0")
                    c1 = ds.attrs.get("Teff_to_Tbb_c1")
                    c2 = ds.attrs.get("Teff_to_Tbb_c2")
                    if c0 is not None and c1 is not None and c2 is not None:
                        c0 = float(c0)
                        c1 = float(c1)
                        c2 = float(c2)
                        mask = np.isfinite(arr) & (arr > 0)
                        teff = offset - gain * arr[mask]
                        arr[mask] = c0 + c1 * teff + c2 * teff * teff
                    elif gain != 1.0 or offset != 0.0:
                        arr = arr * gain + offset
                else:
                    if gain != 1.0 or offset != 0.0:
                        arr = arr * gain + offset
                    albedo_c = ds.attrs.get("Radiance_to_Albedo_c")
                    if albedo_c is not None:
                        arr = arr * float(albedo_c)
            else:
                arr = arr.astype(np.float32)
                # JAXA / MTG FCI int16 sentinel
                arr[arr == -32768.0] = np.nan
                fill_val = ds[var_name].attrs.get('_FillValue')
                if fill_val is not None:
                    arr[arr == fill_val] = np.nan
                missing_val = ds[var_name].attrs.get('missing_value')
                if missing_val is not None:
                    arr[arr == missing_val] = np.nan
                scale = ds[var_name].attrs.get('scale_factor', 1.0)
                offset = ds[var_name].attrs.get('add_offset', 0.0)
                if scale != 1.0 or offset != 0.0:
                    arr = arr * np.float32(scale) + np.float32(offset)
            return arr


    @classmethod
    def available_for_nc(cls, nc_path: Path, band_names: list = None) -> list:
        try:
            if band_names:
                present = set(band_names)
            else:
                with xr.open_dataset(nc_path) as ds:
                    present = {v for v in ds.data_vars if v.startswith("B") and len(v) == 3}
            return [k for k, info in RGB_PRODUCTS.items()
                    if all(b in present for b in info["bands"])]
        except Exception:
            return []

    @classmethod
    def band_as_image(cls, nc_path: Path, band: str, max_px: int = 2200,
                      band_file_map: dict = None) -> "np.ndarray | None":
        try:
            file_to_open = nc_path
            if band_file_map and band in band_file_map:
                file_to_open = Path(band_file_map[band])
            arr = cls._read_band_array(file_to_open, band)
            if arr is None:
                return None
            arr = cls._decimate_array(arr, max_px)
            alpha = cls._earth_mask([arr])
            gray = cls._linear(arr, None, None, gamma=1.0)
            rgba = np.stack([gray, gray, gray, alpha], axis=-1)
            return rgba
        except Exception:
            return None

    @classmethod
    def composite_realtime(cls, product_key: str, nc_path: Path = None,
                           max_px: int = 2200, band_cache: dict = None,
                           band_file_map: dict = None) -> "np.ndarray | None":
        info = RGB_PRODUCTS.get(product_key)
        if not info:
            return None

        def get_band(band):
            nonlocal band_file_map
            if band_cache and band in band_cache:
                arr = band_cache[band].astype(np.float32)
                arr = cls._decimate_array(arr, max_px)
                return arr
            elif nc_path:
                file_to_open = nc_path
                if band_file_map and band in band_file_map:
                    file_to_open = Path(band_file_map[band])
                arr = cls._read_band_array(file_to_open, band)
                if arr is None:
                    return None
                arr = cls._decimate_array(arr, max_px)
                return arr
            else:
                return None

        formula = info.get("formula", {})
        needed_bands = set()
        if info.get("special"):
            needed_bands.update(info["bands"])
        elif info.get("single_band"):
            needed_bands.add(formula["band"])
        else:
            for ch in info["channels"]:
                spec = formula[ch]
                if "band1" in spec:
                    needed_bands.add(spec["band1"])
                    if "band2" in spec:
                        needed_bands.add(spec["band2"])
                elif "band" in spec:
                    needed_bands.add(spec["band"])
                elif "bands" in spec:
                    needed_bands.update(spec["bands"])
        if not info.get("special"):
            needed_bands.update(info["bands"])

        cache = {}
        for b in needed_bands:
            arr = get_band(b)
            if arr is None:
                return None
            cache[b] = arr

        if not info.get("single_band") and info.get("special") not in ("sandwich", "geocolor", "true_daynight", "false_color", "false_color_adv", "true_color_unidata"):
            if max_px == 0:
                _smallest = max(cache.values(), key=lambda a: a.shape[0] * a.shape[1])
            else:
                _smallest = min(cache.values(), key=lambda a: a.shape[0] * a.shape[1])
            target_h, target_w = _smallest.shape
            for b, arr in list(cache.items()):
                if arr.shape != (target_h, target_w):
                    zh = target_h / arr.shape[0]
                    zw = target_w / arr.shape[1]
                    cache[b] = zoom(arr, (zh, zw), order=1)

        if info.get("special") == "sandwich":
            return cls._sandwich(cache, nc_path, sataid_ir=bool(info.get("sataid_ir")))
        if info.get("special") == "true_color_unidata":
            return cls._true_color_unidata(cache, nc_path, max_px=max_px, info=info)
        if info.get("special") == "true_daynight":
            return cls._true_color_daynight(cache, nc_path, max_px=max_px)
        if info.get("special") == "geocolor":
            return cls._geocolor_composite(nc_path, cache, max_px, band_file_map=band_file_map)
        if info.get("special") == "false_color":
            return cls._false_color(cache, nc_path)
        if info.get("special") == "false_color_adv":
            return cls._false_color_adv(cache, nc_path)

        alpha = cls._earth_mask(list(cache.values()))

        if info.get("single_band"):
            spec = formula
            gray = cls._linear(cache[spec["band"]], spec.get("min"), spec.get("max"),
                               spec.get("gamma", 1.0), spec.get("invert", False))
            rgb_u8 = np.stack([gray, gray, gray], axis=-1)
        else:
            channels = []
            for ch in info["channels"]:
                spec = formula.get(ch)
                if not spec:
                    raise ValueError(f"Missing formula for channel {ch} in product {product_key}")

                if "band1" in spec and "band2" in spec:
                    d = cache[spec["band1"]] - cache[spec["band2"]]
                elif spec.get("operation") == "diff" and "bands" in spec:
                    b1, b2 = spec["bands"]
                    d = cache[b1] - cache[b2]
                elif spec.get("operation") == "weighted_sum" and "bands" in spec and "weights" in spec:
                    d = sum(w * cache[b] for w, b in zip(spec["weights"], spec["bands"]))
                elif "band" in spec:
                    d = cache[spec["band"]]
                elif "bands" in spec and len(spec["bands"]) > 0:
                    d = cache[spec["bands"][0]]
                else:
                    raise ValueError(f"Channel {ch} in product {product_key} has no usable band definition: {spec}")

                channels.append(
                    cls._linear(d, spec.get("min"), spec.get("max"),
                                spec.get("gamma", 1.0), spec.get("invert", False))
                )
            h = min(c.shape[0] for c in channels)
            w = min(c.shape[1] for c in channels)
            rgb_u8 = np.stack([c[:h, :w] for c in channels], axis=-1)

        return cls._attach_alpha(rgb_u8, alpha)

    @staticmethod
    def _sandwich_ir_lookup(bt_kelvin: np.ndarray) -> np.ndarray:
        """Apply the sandwich IR colormap (BT Celsius → RGB) from App.py._sandwich_ir_cmap."""
        celsius = np.asarray(bt_kelvin, dtype=np.float32) - 273.15
        rgb = np.zeros((*celsius.shape, 3), dtype=np.float32)

        NEW_RED   = np.array([251/255.0, 5/255.0, 0.0])
        ORANGE    = np.array([1.0, 0.5, 0.0])
        YELLOW    = np.array([1.0, 1.0, 0.0])
        GREEN     = np.array([0.0, 1.0, 0.0])
        CYAN      = np.array([0.0, 1.0, 1.0])
        DARK_BLUE = np.array([14/255.0, 14/255.0, 146/255.0])
        GREY      = np.array([0.5, 0.5, 0.5])
        BLACK     = np.array([0.0, 0.0, 0.0])

        def _lerp(c1, c2, t):
            t = np.asarray(t, dtype=np.float32)
            return c1 * (1.0 - t[..., np.newaxis]) + c2 * t[..., np.newaxis]

        t = celsius
        mask = t <= -72
        rgb[mask] = NEW_RED

        m = (t > -72) & (t <= -65)
        rgb[m] = _lerp(NEW_RED, ORANGE, (t[m] + 72) / 7.0)

        m = (t > -65) & (t <= -58)
        rgb[m] = _lerp(ORANGE, YELLOW, (t[m] + 65) / 7.0)

        m = (t > -58) & (t <= -52)
        rgb[m] = _lerp(GREEN, CYAN, (t[m] + 58) / 6.0)

        m = (t > -52) & (t <= -32)
        rgb[m] = _lerp(CYAN, DARK_BLUE, (t[m] + 52) / 20.0)

        m = (t > -32) & (t <= -25)
        rgb[m] = DARK_BLUE

        m = t > -25
        rgb[m] = _lerp(GREY, BLACK, (t[m] + 25) / 75.0)
        rgb[m] = np.clip(rgb[m], 0.0, 1.0)

        return rgb

    @staticmethod
    def _sandwich_sataid_ir_lookup(bt_kelvin: np.ndarray) -> np.ndarray:
        """Apply the SATAID sandwich IR colormap (BT Celsius → RGB) from Sandwich.dat 256-entry LUT."""
        celsius = np.asarray(bt_kelvin, dtype=np.float32) - 273.15
        rgb = np.zeros((*celsius.shape, 3), dtype=np.float32)

        DARK_BLUE = np.array([0, 0, 131], dtype=np.float64)
        BLUE      = np.array([0, 0, 255], dtype=np.float64)
        CYAN      = np.array([0, 255, 255], dtype=np.float64)
        GREEN     = np.array([0, 255, 0], dtype=np.float64)
        YELLOW    = np.array([255, 255, 0], dtype=np.float64)
        RED       = np.array([255, 0, 0], dtype=np.float64)
        DARK_RED  = np.array([131, 0, 0], dtype=np.float64)

        lut = np.zeros((256, 3), dtype=np.float64)
        def _lerp(c1, c2, t):
            t = np.asarray(t, dtype=np.float64)
            return c1 * (1.0 - t) + c2 * t

        for i in range(0, 34):
            lut[i] = _lerp(DARK_BLUE, BLUE, i / 33.0)
        for i in range(34, 99):
            lut[i] = _lerp(BLUE, CYAN, (i - 34) / (98 - 34))
        for i in range(99, 163):
            if i <= 130:
                lut[i] = _lerp(CYAN, GREEN, (i - 99) / (130 - 99))
            else:
                lut[i] = _lerp(GREEN, YELLOW, (i - 131) / (162 - 131))
        for i in range(163, 226):
            lut[i] = _lerp(YELLOW, RED, (i - 163) / (225 - 163))
        for i in range(226, 256):
            lut[i] = _lerp(RED, DARK_RED, (i - 226) / (255 - 226))
        lut /= 255.0

        LUT_MIN = -73.15
        LUT_MAX = -33.15
        GRAY_MAX = 70.0

        t = celsius

        below = t <= LUT_MIN
        rgb[below] = lut[0]

        in_lut = (t > LUT_MIN) & (t <= LUT_MAX)
        if in_lut.any():
            f = (t[in_lut] - LUT_MIN) / (LUT_MAX - LUT_MIN) * 255.0
            j = np.floor(f).astype(np.int64)
            frac = f - j
            rgb[in_lut] = lut[j] * (1.0 - frac[..., np.newaxis]) + lut[np.clip(j + 1, 0, 255)] * frac[..., np.newaxis]

        warm = t > LUT_MAX
        g = 1.0 - (t - LUT_MAX) / (GRAY_MAX - LUT_MAX)
        g = np.clip(g, 0.0, 1.0)
        rgb[warm] = g[warm, np.newaxis]

        return np.clip(rgb, 0.0, 1.0)

    @classmethod
    def _sandwich(cls, cache: dict, nc_path: Path = None, sataid_ir: bool = False) -> np.ndarray:
        import time as _time
        _t0 = _time.time()
        use_xp = cls._active_xp()
        _xp_name = "cupy" if hasattr(use_xp, "cuda") else "numpy"
        print(f"[SANDWICH] start | xp={_xp_name} | bands in cache: {list(cache.keys())}")

        # Resize bands to common grid to avoid aliasing from resolution mismatch
        _target_band = min(["B13", "B03"], key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        target_h, target_w = cache[_target_band].shape
        print(f"[SANDWICH] target grid: {target_h}x{target_w}")
        b13_shape_before = cache["B13"].shape
        b03_shape_before = cache["B03"].shape
        for b in ("B13", "B03"):
            arr = cache[b]
            if arr.shape != (target_h, target_w):
                zh = target_h / arr.shape[0]
                zw = target_w / arr.shape[1]
                print(f"[SANDWICH] zooming {b}: {arr.shape} -> {target_h}x{target_w} (zh={zh:.4f}, zw={zw:.4f})")
                arr = zoom(arr, (zh, zw), order=1)
                cache[b] = arr
        print(f"[SANDWICH] B13 shape: {b13_shape_before} -> {cache['B13'].shape}")
        print(f"[SANDWICH] B03 shape: {b03_shape_before} -> {cache['B03'].shape}")

        vis_raw = cls._to_xp(cache["B03"]).astype(use_xp.float32)
        ir_raw  = cls._to_xp(cache["B13"]).astype(use_xp.float32)
        h, w = target_h, target_w
        print(f"[SANDWICH] vis_raw dtype={vis_raw.dtype} min={float(use_xp.nanmin(vis_raw)):.4f} max={float(use_xp.nanmax(vis_raw)):.4f} nan={int(use_xp.isnan(vis_raw).sum())}")
        print(f"[SANDWICH] ir_raw dtype={ir_raw.dtype} min={float(use_xp.nanmin(ir_raw)):.4f} max={float(use_xp.nanmax(ir_raw)):.4f} nan={int(use_xp.isnan(ir_raw).sum())}")

        # Track true no-data (both bands NaN) for alpha; fill Vis NaN with 0
        # (night side has no solar reflection → treat as black, not missing data)
        _nan_alpha = use_xp.isnan(vis_raw) & use_xp.isnan(ir_raw)
        _vis_nan = int(use_xp.isnan(vis_raw).sum())
        vis_raw = use_xp.where(use_xp.isnan(vis_raw), use_xp.float32(0), vis_raw)
        print(f"[SANDWICH] NaN fill: vis_nan={_vis_nan}→0 | alpha_both={int(_nan_alpha.sum())}/{h*w}")

        # --- Scale B13 to Kelvin ---
        scale13, off13 = cls._get_band_scale_offset(nc_path, "B13") if nc_path else (1.0, 0.0)
        ir = ir_raw * scale13 + off13
        # Fill IR NaN with 300K (warm neutral background)
        _ir_nan = int(use_xp.isnan(ir).sum())
        ir = use_xp.where(use_xp.isnan(ir), use_xp.float32(300.0), ir)
        print(f"[SANDWICH] B13 scale={scale13:.6f} off={off13:.6f} | ir_K min={float(use_xp.nanmin(ir)):.2f} max={float(use_xp.nanmax(ir)):.2f} ir_nan={_ir_nan}→300K")

        # --- SZA: day/night determination (mirrors VP-SIFT) ---
        sza = None
        if nc_path is not None:
            sza = cls._compute_solar_zenith_angle(nc_path, h, w)
        if sza is None:
            print(f"[SANDWICH] SZA unavailable, falling back to zeros (all day)")
            sza = np.zeros((h, w), dtype=np.float32)
        sza = cls._to_xp(sza)
        sza_nan_before = int(use_xp.isnan(sza).sum())
        sza = use_xp.nan_to_num(sza, nan=90.0)
        print(f"[SANDWICH] SZA shape={sza.shape} nan_before={sza_nan_before} min={float(use_xp.nanmin(sza)):.2f} max={float(use_xp.nanmax(sza)):.2f}")

        # ---- VP-SIFT-style pipeline (apply_rgb_corrections) adapted for B03 + B13 ----

        # 1. Base Normalization (mirrors VP-SIFT lines 373-376)
        _needs_div = bool(use_xp.nanmax(vis_raw) > 1.0)
        if _needs_div:
            print(f"[SANDWICH] step1: vis_raw max>1, dividing by 100")
        else:
            print(f"[SANDWICH] step1: vis_raw max<=1, keeping as-is")
        vis_norm = use_xp.clip(
            vis_raw / 100.0 if _needs_div else vis_raw,
            0.0, 1.0
        )
        print(f"[SANDWICH] step1: vis_norm min={float(use_xp.nanmin(vis_norm)):.4f} max={float(use_xp.nanmax(vis_norm)):.4f}")

        # 2. Solar Airmass (mirrors VP-SIFT lines 378-385)
        cos_sza = use_xp.clip(use_xp.cos(use_xp.radians(sza)), 0.33, 1.0)
        cos2_sza = use_xp.clip(use_xp.cos(use_xp.radians(sza)), 0.40, 1.0)
        path_sun = 0.8 / cos2_sza      # Rayleigh path length
        path_sun_a = 1.0 / cos_sza     # Brightness normalization
        print(f"[SANDWICH] step2: cos_sza range=[{float(use_xp.nanmin(cos_sza)):.4f},{float(use_xp.nanmax(cos_sza)):.4f}]")
        print(f"[SANDWICH] step2: cos2_sza range=[{float(use_xp.nanmin(cos2_sza)):.4f},{float(use_xp.nanmax(cos2_sza)):.4f}]")
        print(f"[SANDWICH] step2: path_sun range=[{float(use_xp.nanmin(path_sun)):.4f},{float(use_xp.nanmax(path_sun)):.4f}]")
        print(f"[SANDWICH] step2: path_sun_a range=[{float(use_xp.nanmin(path_sun_a)):.4f},{float(use_xp.nanmax(path_sun_a)):.4f}]")

        # 3. SZA Illumination Brightening (mirrors VP-SIFT lines 387-390)
        vis_bright = vis_norm * path_sun_a
        print(f"[SANDWICH] step3: vis_bright min={float(use_xp.nanmin(vis_bright)):.4f} max={float(use_xp.nanmax(vis_bright)):.4f} nan={int(use_xp.isnan(vis_bright).sum())}")

        # 4. Rayleigh Subtraction (mirrors VP-SIFT lines 392-413)
        # B03 = red band (0.64µm) → use R coefficient 0.011
        rayleigh_vis = 0.011 * path_sun
        vis_corr = use_xp.clip(vis_bright - rayleigh_vis, 0.0, 1.0)
        print(f"[SANDWICH] step4: rayleigh_vis range=[{float(use_xp.nanmin(rayleigh_vis)):.4f},{float(use_xp.nanmax(rayleigh_vis)):.4f}]")
        print(f"[SANDWICH] step4: vis_corr min={float(use_xp.nanmin(vis_corr)):.4f} max={float(use_xp.nanmax(vis_corr)):.4f} nan={int(use_xp.isnan(vis_corr).sum())}")

        # 5. Day/Night Blending Masks (mirrors VP-SIFT lines 415-421)
        day_weight = use_xp.clip((90.0 - sza) / 5.0, 0.0, 1.0)
        night_weight = 1.0 - day_weight
        _day_px = int((day_weight > 0.5).sum())
        _night_px = int((day_weight < 0.5).sum())
        _terminator_px = int((day_weight == 0.5).sum())
        print(f"[SANDWICH] step5: day_weight range=[{float(use_xp.nanmin(day_weight)):.4f},{float(use_xp.nanmax(day_weight)):.4f}]")
        print(f"[SANDWICH] step5: day_px>{_day_px} night_px>{_night_px} terminator={_terminator_px}")

        vis_day = vis_corr * day_weight
        print(f"[SANDWICH] step5: vis_day min={float(use_xp.nanmin(vis_day)):.4f} max={float(use_xp.nanmax(vis_day)):.4f}")

        # 6. IR Night Background (mirrors VP-SIFT lines 423-425)
        ir_norm = use_xp.clip((313.15 - ir) / (313.15 - 173.15), 0.0, 1.0)
        ir_layer = use_xp.power(ir_norm, 1.5) * 2
        print(f"[SANDWICH] step6: ir_norm min={float(use_xp.nanmin(ir_norm)):.4f} max={float(use_xp.nanmax(ir_norm)):.4f} nan={int(use_xp.isnan(ir_norm).sum())}")
        print(f"[SANDWICH] step6: ir_layer min={float(use_xp.nanmin(ir_layer)):.4f} max={float(use_xp.nanmax(ir_layer)):.4f}")

        # 7. Combine VIS day + IR night into 3-channel (mirrors VP-SIFT lines 427-429)
        r_final = vis_day + (ir_layer * night_weight)
        g_final = vis_day + (ir_layer * night_weight)
        b_final = vis_day + (ir_layer * night_weight)
        print(f"[SANDWICH] step7: r_final min={float(use_xp.nanmin(r_final)):.4f} max={float(use_xp.nanmax(r_final)):.4f} nan={int(use_xp.isnan(r_final).sum())}")

        # 8. Saturation Boost (mirrors VP-SIFT lines 431-436)
        saturation_factor = 1.33
        luminance = 0.2989 * r_final + 0.5870 * g_final + 0.1140 * b_final
        r_final = use_xp.clip(luminance + saturation_factor * (r_final - luminance), 0.0, 1.0)
        g_final = use_xp.clip(luminance + saturation_factor * (g_final - luminance), 0.0, 1.0)
        b_final = use_xp.clip(luminance + saturation_factor * (b_final - luminance), 0.0, 1.0)
        print(f"[SANDWICH] step8: sat_boost applied factor={saturation_factor} | r range=[{float(use_xp.nanmin(r_final)):.4f},{float(use_xp.nanmax(r_final)):.4f}]")

        # 9. IR overlay using sandwich_ir colormap (cold clouds)
        ir_cpu = cls._to_np(ir)
        if sataid_ir:
            ir_rgb = cls._to_xp(cls._sandwich_sataid_ir_lookup(ir_cpu)).astype(use_xp.float32)
        else:
            ir_rgb = cls._to_xp(cls._sandwich_ir_lookup(ir_cpu)).astype(use_xp.float32)
        bt_thresh = 248.15
        cold_mask = ir < bt_thresh
        _cold_px = int(cold_mask.sum())
        print(f"[SANDWICH] step9: BT threshold={bt_thresh}K | cold_pixels={_cold_px}/{h*w} ({100*_cold_px/(h*w):.1f}%)")
        rgb = use_xp.stack([r_final, g_final, b_final], axis=-1)
        rgb = use_xp.where(cold_mask[:, :, None], ir_rgb, rgb)

        # Build alpha mask from the original both-bands-NaN mask, then return RGBA
        _nan_mask = _nan_alpha
        rgb = use_xp.nan_to_num(rgb, nan=0.0, posinf=1.0, neginf=0.0)
        rgb = use_xp.clip(rgb, 0.0, 1.0)
        _neg = int((rgb < 0).sum())
        _over = int((rgb > 1).sum())
        print(f"[SANDWICH] cleanup: alpha_nan={int(_nan_mask.sum())} neg_clipped={_neg} over_clipped={_over}")

        rgb_u8 = (rgb * 255).astype(use_xp.uint8)
        alpha_ch = use_xp.where(_nan_mask, use_xp.uint8(0), use_xp.uint8(255))
        rgba_u8 = use_xp.concatenate([rgb_u8, alpha_ch[:, :, None]], axis=-1)
        _t1 = _time.time()
        print(f"[SANDWICH] done in {_t1-_t0:.3f}s | rgba_u8 shape={rgba_u8.shape} dtype={rgba_u8.dtype}")
        return cls._to_np(rgba_u8)

    @classmethod
    def _true_color_daynight(cls, cache: dict, nc_path: Path = None, max_px: int = 0) -> np.ndarray:
        import time as _time
        _t0 = _time.time()
        use_xp = cls._active_xp()
        _xp_name = "cupy" if hasattr(use_xp, "cuda") else "numpy"
        print(f"[TC_DN] start | xp={_xp_name} | bands in cache: {list(cache.keys())}")

        print(f"[TC_DN] shapes: B13={cache['B13'].shape}, B03={cache['B03'].shape}, B02={cache['B02'].shape}, B01={cache['B01'].shape}")
        if max_px == 0:
            _target_band = max(["B13", "B03", "B02", "B01"], key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        else:
            _target_band = min(["B13", "B03", "B02", "B01"], key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        target_h, target_w = cache[_target_band].shape
        print(f"[TC_DN] target grid: {target_h}x{target_w}")
        for b in ("B13", "B03", "B02", "B01"):
            arr = cache[b]
            if arr.shape != (target_h, target_w):
                zh = target_h / arr.shape[0]
                zw = target_w / arr.shape[1]
                print(f"[TC_DN] zooming {b}: {arr.shape} -> {target_h}x{target_w} (zh={zh:.4f}, zw={zw:.4f})")
                arr = zoom(arr, (zh, zw), order=1)
                cache[b] = arr

        b03_raw = cls._to_xp(cache["B03"]).astype(use_xp.float32)
        b02_raw = cls._to_xp(cache["B02"]).astype(use_xp.float32)
        b01_raw = cls._to_xp(cache["B01"]).astype(use_xp.float32)
        ir_raw  = cls._to_xp(cache["B13"]).astype(use_xp.float32)
        h, w = target_h, target_w

        _nan_alpha = use_xp.isnan(b03_raw) & use_xp.isnan(ir_raw)
        b03_raw = use_xp.where(use_xp.isnan(b03_raw), use_xp.float32(0), b03_raw)
        b02_raw = use_xp.where(use_xp.isnan(b02_raw), use_xp.float32(0), b02_raw)
        b01_raw = use_xp.where(use_xp.isnan(b01_raw), use_xp.float32(0), b01_raw)
        print(f"[TC_DN] NaN fill done | alpha_both={int(_nan_alpha.sum())}/{h*w}")

        scale13, off13 = cls._get_band_scale_offset(nc_path, "B13") if nc_path else (1.0, 0.0)
        ir = ir_raw * scale13 + off13
        ir = use_xp.where(use_xp.isnan(ir), use_xp.float32(300.0), ir)
        print(f"[TC_DN] B13 scale={scale13:.6f} off={off13:.6f}")

        _needs_div = float(use_xp.nanmax(b03_raw)) > 1.0
        if _needs_div:
            b03 = use_xp.clip(b03_raw / 100.0, 0.0, 1.0)
            b02 = use_xp.clip(b02_raw / 100.0, 0.0, 1.0)
            b01 = use_xp.clip(b01_raw / 100.0, 0.0, 1.0)
        else:
            b03 = use_xp.clip(b03_raw, 0.0, 1.0)
            b02 = use_xp.clip(b02_raw, 0.0, 1.0)
            b01 = use_xp.clip(b01_raw, 0.0, 1.0)

        gamma_tc = 1.0
        b03_g = use_xp.power(b03, 1.0 / gamma_tc)
        b02_g = use_xp.power(b02, 1.0 / gamma_tc)
        b01_g = use_xp.power(b01, 1.0 / gamma_tc)
        print(f"[TC_DN] gamma={gamma_tc} applied to TC bands")

        sza = None
        if nc_path is not None:
            sza = cls._compute_solar_zenith_angle(nc_path, h, w)
        if sza is None:
            print(f"[TC_DN] SZA unavailable, falling back to zeros (all day)")
            sza = np.zeros((h, w), dtype=np.float32)
        sza = cls._to_xp(sza)
        sza = use_xp.nan_to_num(sza, nan=90.0)
        print(f"[TC_DN] SZA shape={sza.shape}")

        cos_sza = use_xp.clip(use_xp.cos(use_xp.radians(sza)), 0.33, 1.0)
        cos2_sza = use_xp.clip(use_xp.cos(use_xp.radians(sza)), 0.40, 1.0)
        path_sun = 0.8 / cos2_sza
        path_sun_a = 1.0 / cos_sza

        day_weight = use_xp.clip((90.0 - sza) / 5.0, 0.0, 1.0)
        night_weight = 1.0 - day_weight
        _day_px = int((day_weight > 0.5).sum())
        _night_px = int((day_weight < 0.5).sum())
        print(f"[TC_DN] day_px={_day_px} night_px={_night_px}")

        rayleigh_r = 0.011 * path_sun
        rayleigh_g = 0.038 * path_sun
        rayleigh_b = 0.050 * path_sun

        b03_day = use_xp.clip(b03_g * path_sun_a, 0.0, 1.0)
        b02_day = use_xp.clip(b02_g * path_sun_a, 0.0, 1.0)
        b01_day = use_xp.clip(b01_g * path_sun_a, 0.0, 1.0)
        b03_corr = use_xp.clip(b03_day - rayleigh_r, 0.0, 1.0)
        b02_corr = use_xp.clip(b02_day - rayleigh_g, 0.0, 1.0)
        b01_corr = use_xp.clip(b01_day - rayleigh_b, 0.0, 1.0)
        day_rgb = use_xp.stack([b03_corr, b02_corr, b01_corr], axis=-1) * day_weight[:, :, None]

        ir_norm = use_xp.clip((313.15 - ir) / (313.15 - 173.15), 0.0, 1.0)
        ir_layer = use_xp.power(ir_norm, 1.5) * 2
        ir_3ch = use_xp.stack([ir_layer, ir_layer, ir_layer], axis=-1)
        night_rgb = ir_3ch * night_weight[:, :, None]

        rgb = day_rgb + night_rgb
        print(f"[TC_DN] blended rgb range=[{float(use_xp.nanmin(rgb)):.4f},{float(use_xp.nanmax(rgb)):.4f}]")

        saturation_factor = 1.33
        luminance = 0.2989 * rgb[:,:,0] + 0.5870 * rgb[:,:,1] + 0.1140 * rgb[:,:,2]
        r_final = use_xp.clip(luminance + saturation_factor * (rgb[:,:,0] - luminance), 0.0, 1.0)
        g_final = use_xp.clip(luminance + saturation_factor * (rgb[:,:,1] - luminance), 0.0, 1.0)
        b_final = use_xp.clip(luminance + saturation_factor * (rgb[:,:,2] - luminance), 0.0, 1.0)
        rgb = use_xp.stack([r_final, g_final, b_final], axis=-1)
        print(f"[TC_DN] sat_boost applied factor={saturation_factor}")

        rgb = use_xp.clip(rgb, 0.0, 1.0)
        rgb_u8 = (rgb * 255).astype(use_xp.uint8)
        alpha_ch = use_xp.where(_nan_alpha, use_xp.uint8(0), use_xp.uint8(255))
        rgba_u8 = use_xp.concatenate([rgb_u8, alpha_ch[:, :, None]], axis=-1)
        _t1 = _time.time()
        print(f"[TC_DN] done in {_t1-_t0:.3f}s | rgba_u8 shape={rgba_u8.shape}")
        return cls._to_np(rgba_u8)

    @classmethod
    def _geocolor_composite(cls, nc_path: Path, cache: dict, max_px: int, band_file_map: dict = None) -> np.ndarray:
        b01 = cache.get("B01")
        b02 = cache.get("B02")
        b03 = cache.get("B03")
        b07 = cache.get("B07")
        b13 = cache.get("B13")
        if any(x is None for x in (b01, b02, b03, b07, b13)):
            return None

        _band_list = [b01, b02, b03, b07, b13]
        _idx = min(range(len(_band_list)), key=lambda i: _band_list[i].shape[0] * _band_list[i].shape[1])
        target_h, target_w = _band_list[_idx].shape
        for name in ["B01", "B02", "B03", "B07", "B13"]:
            arr = cache[name]
            if arr.shape != (target_h, target_w):
                zh = target_h / arr.shape[0]
                zw = target_w / arr.shape[1]
                cache[name] = zoom(arr, (zh, zw), order=1)

        b01 = cache["B01"]
        b02 = cache["B02"]
        b03 = cache["B03"]
        b07 = cache["B07"]
        b13 = cache["B13"]
        alpha = cls._earth_mask([b01, b02, b03, b07, b13])

        sza = cls._compute_solar_zenith_angle(nc_path, target_h, target_w)
        if sza is None:
            sza = np.zeros((target_h, target_w), dtype=np.float32)

        rayleigh_factors = {"B01": 0.25, "B02": 0.10, "B03": 0.05}
        def rayleigh_correct(arr, factor):
            r_toa = np.clip(arr / 100.0, 0.0, 1.0)
            r_corr = r_toa - factor * r_toa
            r_corr = np.clip(r_corr, 0.0, 1.0)
            return r_corr * 100.0

        day_mask = sza <= 85.0
        r_corr = rayleigh_correct(b03, rayleigh_factors["B03"])
        g_corr = rayleigh_correct(b02, rayleigh_factors["B02"])
        b_corr = rayleigh_correct(b01, rayleigh_factors["B01"])
        day_rgb = np.stack([(r_corr * 2.55).astype(np.uint8),
                            (g_corr * 2.55).astype(np.uint8),
                            (b_corr * 2.55).astype(np.uint8)], axis=-1)

        night_mask = sza >= 95.0
        scale13, off13 = cls._get_band_scale_offset(nc_path, "B13", band_file_map=band_file_map)
        bt13 = b13 * scale13 + off13
        bt13 = np.clip(bt13, 150, 350)
        high_cloud_alpha = np.clip((240.0 - bt13) / 30.0, 0.0, 1.0)
        high_cloud_rgb = np.full((target_h, target_w, 3), 255, dtype=np.uint8)

        scale7, off7 = cls._get_band_scale_offset(nc_path, "B07", band_file_map=band_file_map)
        bt7 = b07 * scale7 + off7
        btd = bt13 - bt7
        low_cloud_alpha = np.clip(btd / 5.0, 0.0, 1.0)
        low_cloud_rgb = np.stack([np.full((target_h, target_w), 255, dtype=np.uint8),
                                  np.full((target_h, target_w), 128, dtype=np.uint8),
                                  np.full((target_h, target_w), 255, dtype=np.uint8)], axis=-1)

        night_bg = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        night_bg[:,:,0] = 20
        night_bg[:,:,1] = 30
        night_bg[:,:,2] = 50

        night_bg_xp   = cls._to_xp(night_bg).astype(np.float32)
        lca           = cls._to_xp(low_cloud_alpha)
        hca           = cls._to_xp(high_cloud_alpha)
        low_rgb_xp    = cls._to_xp(low_cloud_rgb).astype(np.float32)
        high_rgb_xp   = cls._to_xp(high_cloud_rgb).astype(np.float32)

        night_rgb_xp = night_bg_xp * (1 - lca[:, :, None])  + low_rgb_xp  * lca[:, :, None]
        night_rgb_xp = night_rgb_xp * (1 - hca[:, :, None]) + high_rgb_xp * hca[:, :, None]
        night_rgb_xp = np.clip(night_rgb_xp, 0, 255).astype(np.uint8)

        sza_xp    = cls._to_xp(sza)
        day_xp    = cls._to_xp(day_rgb).astype(np.float32)
        blend_mask_xp = (sza_xp > 85.0) & (sza_xp < 95.0)
        blend_w_xp    = np.where(blend_mask_xp,
                                  (sza_xp - 85.0) / 10.0,
                                  np.zeros_like(sza_xp, dtype=np.float32))

        bw3 = blend_w_xp[:, :, None]
        final_xp = (
            np.where((sza_xp <= 85.0)[:, :, None], day_xp, 0.0) +
            np.where((sza_xp >= 95.0)[:, :, None], night_rgb_xp.astype(np.float32), 0.0) +
            np.where(blend_mask_xp[:, :, None],
                     (1 - bw3) * day_xp + bw3 * night_rgb_xp.astype(np.float32), 0.0)
        )
        gamma = 1.8
        final_xp = np.clip(final_xp, 0, 255)
        final_xp = (final_xp / 255.0) ** (1.0 / gamma) * 255.0
        final_rgb = cls._to_np(final_xp.astype(np.uint8))
        return cls._attach_alpha(final_rgb, alpha)

    @classmethod
    def _false_color(cls, cache: dict, nc_path: Path = None) -> np.ndarray:
        use_xp = cls._active_xp()

        bands = list(cache.keys())
        if len(bands) != 2:
            return None

        _target_band = min(bands, key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        target_h, target_w = cache[_target_band].shape
        for b in bands:
            arr = cache[b]
            if arr.shape != (target_h, target_w):
                zh = target_h / arr.shape[0]
                zw = target_w / arr.shape[1]
                cache[b] = zoom(arr, (zh, zw), order=1)

        b0_num = int(bands[0].replace("B", ""))
        b1_num = int(bands[1].replace("B", ""))
        if b0_num < b1_num:
            vis_raw = cls._to_xp(cache[bands[0]]).astype(use_xp.float32)
            ir_raw  = cls._to_xp(cache[bands[1]]).astype(use_xp.float32)
        else:
            vis_raw = cls._to_xp(cache[bands[1]]).astype(use_xp.float32)
            ir_raw  = cls._to_xp(cache[bands[0]]).astype(use_xp.float32)

        h, w = target_h, target_w

        _nan_alpha = use_xp.isnan(vis_raw) & use_xp.isnan(ir_raw)
        vis_raw = use_xp.where(use_xp.isnan(vis_raw), use_xp.float32(0), vis_raw)

        scale_ir, off_ir = cls._get_band_scale_offset(nc_path, bands[0] if b0_num > b1_num else bands[1]) if nc_path else (1.0, 0.0)
        ir = ir_raw * scale_ir + off_ir
        ir = use_xp.where(use_xp.isnan(ir), use_xp.float32(300.0), ir)

        sza = None
        if nc_path is not None:
            sza = cls._compute_solar_zenith_angle(nc_path, h, w)
        if sza is None:
            sza = np.zeros((h, w), dtype=np.float32)
        sza = cls._to_xp(sza)
        sza = use_xp.nan_to_num(sza, nan=90.0)

        vis_norm = use_xp.clip(
            vis_raw / 100.0 if bool(use_xp.nanmax(vis_raw) > 1.0) else vis_raw,
            0.0, 1.0
        )

        cos_sza = use_xp.clip(use_xp.cos(use_xp.radians(sza)), 0.25, 1.0)
        path_sun = 1.0 / cos_sza
        vis_bright = vis_norm * path_sun

        day_weight = use_xp.clip((90.0 - sza) / 5.0, 0.0, 1.0)
        night_weight = 1.0 - day_weight

        ir_norm = use_xp.clip((323.15 - ir) / (313.15 - 173.15), 0.0, 1.0)
        ir_layer = use_xp.power(ir_norm, 1.1)

        r = vis_bright + (ir_layer * night_weight * 0.45) + (ir_layer * day_weight * 0.2)
        g = vis_bright * 0.9 + (ir_layer * night_weight * 0.45) + (ir_layer * day_weight * 0.25)
        b = vis_bright * 0.1 + ir_layer

        rgb = use_xp.clip(use_xp.stack([r, g, b], axis=-1), 0.0, 1.0)
        rgb_u8 = (rgb * 255).astype(use_xp.uint8)
        alpha_ch = use_xp.where(_nan_alpha, use_xp.uint8(0), use_xp.uint8(255))
        rgba_u8 = use_xp.concatenate([rgb_u8, alpha_ch[:, :, None]], axis=-1)
        return cls._to_np(rgba_u8)

    @classmethod
    def _true_color_unidata(cls, cache: dict, nc_path: Path = None, max_px: int = 0, info: dict = None) -> np.ndarray:
        import time as _time
        _t0 = _time.time()
        use_xp = cls._active_xp()
        _xp_name = "cupy" if hasattr(use_xp, "cuda") else "numpy"
        from .unidata_truecolor import (
            blend_daynight,
            clean_ir,
            contrast_correction,
            make_natural_color,
            make_true_color,
            overlay_night,
        )

        info = info or {}
        r_band = info.get("r_band")
        g_band = info.get("g_band")
        b_band = info.get("b_band")
        ir_band = info.get("ir_band")
        natural = bool(info.get("natural"))
        contrast = info.get("contrast")
        print(f"[TC_UD] start | xp={_xp_name} | r={r_band} g={g_band} b={b_band} ir={ir_band} natural={natural}")

        bands = [r_band, g_band, b_band]
        if ir_band:
            bands.append(ir_band)
        for b in bands:
            if b not in cache:
                print(f"[TC_UD] missing band {b} in cache -> None")
                return None

        if max_px == 0:
            _target_band = max(bands, key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        else:
            _target_band = min(bands, key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        target_h, target_w = cache[_target_band].shape
        print(f"[TC_UD] target grid: {target_h}x{target_w}")
        for b in bands:
            arr = cache[b]
            if arr.shape != (target_h, target_w):
                zh = target_h / arr.shape[0]
                zw = target_w / arr.shape[1]
                print(f"[TC_UD] zooming {b}: {arr.shape} -> {target_h}x{target_w}")
                arr = zoom(arr, (zh, zw), order=1)
                cache[b] = arr

        r_raw = cls._to_xp(cache[r_band]).astype(use_xp.float32)
        g_raw = cls._to_xp(cache[g_band]).astype(use_xp.float32)
        b_raw = cls._to_xp(cache[b_band]).astype(use_xp.float32)

        _nan_alpha = use_xp.isnan(r_raw) | use_xp.isnan(g_raw) | use_xp.isnan(b_raw)
        r_raw = use_xp.where(use_xp.isnan(r_raw), use_xp.float32(0), r_raw)
        g_raw = use_xp.where(use_xp.isnan(g_raw), use_xp.float32(0), g_raw)
        b_raw = use_xp.where(use_xp.isnan(b_raw), use_xp.float32(0), b_raw)

        if natural:
            rgb = make_natural_color(use_xp, r_raw, g_raw, b_raw)
        else:
            rgb = make_true_color(use_xp, r_raw, g_raw, b_raw)
        print(f"[TC_UD] day rgb range=[{float(use_xp.nanmin(rgb)):.4f},{float(use_xp.nanmax(rgb)):.4f}]")

        if contrast:
            rgb = contrast_correction(use_xp, rgb, contrast)

        if ir_band:
            ir_raw = cls._to_xp(cache[ir_band]).astype(use_xp.float32)
            _nan_alpha = _nan_alpha | use_xp.isnan(ir_raw)
            scale, off = cls._get_band_scale_offset(nc_path, ir_band) if nc_path else (1.0, 0.0)
            ir = ir_raw * scale + off
            ir_layer = clean_ir(use_xp, ir)
            print(f"[TC_UD] ir_band={ir_band} scale={scale:.6f} off={off:.6f}")
            sza = None
            if nc_path is not None:
                sza = cls._compute_solar_zenith_angle(nc_path, target_h, target_w)
            if sza is not None:
                sza = cls._to_xp(sza)
                sza = use_xp.nan_to_num(sza, nan=90.0)
                night_rgb = use_xp.stack([ir_layer, ir_layer, ir_layer], axis=-1)
                rgb = blend_daynight(use_xp, rgb, night_rgb, sza)
                print("[TC_UD] SZA day/night blend applied")
            else:
                rgb = overlay_night(use_xp, rgb, ir_layer)
                print("[TC_UD] SZA unavailable -> Unidata max-blend overlay applied")

        rgb_u8 = (use_xp.clip(rgb, 0.0, 1.0) * 255).astype(use_xp.uint8)
        alpha_ch = use_xp.where(_nan_alpha, use_xp.uint8(0), use_xp.uint8(255))
        rgba_u8 = use_xp.concatenate([rgb_u8, alpha_ch[:, :, None]], axis=-1)
        _t1 = _time.time()
        print(f"[TC_UD] done in {_t1-_t0:.3f}s | rgba_u8 shape={rgba_u8.shape}")
        return cls._to_np(rgba_u8)

    @classmethod
    def _false_color_adv(cls, cache: dict, nc_path: Path = None) -> np.ndarray:
        use_xp = cls._active_xp()

        bands = list(cache.keys())
        if len(bands) != 2:
            return None

        _target_band = min(bands, key=lambda b: cache[b].shape[0] * cache[b].shape[1])
        target_h, target_w = cache[_target_band].shape
        for b in bands:
            arr = cache[b]
            if arr.shape != (target_h, target_w):
                zh = target_h / arr.shape[0]
                zw = target_w / arr.shape[1]
                cache[b] = zoom(arr, (zh, zw), order=1)

        b0_num = int(bands[0].replace("B", ""))
        b1_num = int(bands[1].replace("B", ""))
        if b0_num < b1_num:
            vis_raw = cls._to_xp(cache[bands[0]]).astype(use_xp.float32)
            ir_raw  = cls._to_xp(cache[bands[1]]).astype(use_xp.float32)
        else:
            vis_raw = cls._to_xp(cache[bands[1]]).astype(use_xp.float32)
            ir_raw  = cls._to_xp(cache[bands[0]]).astype(use_xp.float32)

        h, w = target_h, target_w

        _nan_alpha = use_xp.isnan(vis_raw) & use_xp.isnan(ir_raw)
        vis_raw = use_xp.where(use_xp.isnan(vis_raw), use_xp.float32(0), vis_raw)

        scale_ir, off_ir = cls._get_band_scale_offset(nc_path, bands[0] if b0_num > b1_num else bands[1]) if nc_path else (1.0, 0.0)
        ir = ir_raw * scale_ir + off_ir
        ir = use_xp.where(use_xp.isnan(ir), use_xp.float32(300.0), ir)

        sza = None
        if nc_path is not None:
            sza = cls._compute_solar_zenith_angle(nc_path, h, w)
        if sza is None:
            sza = np.zeros((h, w), dtype=np.float32)
        sza = cls._to_xp(sza)
        sza = use_xp.nan_to_num(sza, nan=90.0)

        vis_norm = use_xp.clip(
            vis_raw / 100.0 if bool(use_xp.nanmax(vis_raw) > 1.0) else vis_raw,
            0.0, 1.0
        )

        cos_sza = use_xp.clip(use_xp.cos(use_xp.radians(sza)), 0.25, 1.0)
        path_sun = 1.0 / cos_sza
        vis_bright = vis_norm * path_sun

        day_weight = use_xp.clip((90.0 - sza) / 5.0, 0.0, 1.0)
        night_weight = 1.0 - day_weight

        ir_norm = use_xp.clip((323.15 - ir) / (313.15 - 173.15), 0.0, 1.0)
        ir_layer = use_xp.power(ir_norm, 1.1)

        r = use_xp.power(vis_bright, 0.88) * 0.9 + (ir_layer * night_weight * 0.5) + (ir_layer * day_weight * 0.25)
        g = vis_bright * 0.75 + (ir_layer * night_weight * 0.6) + (ir_layer * day_weight * 0.44)
        b = vis_bright * 0.1 + (ir_layer * day_weight) + (ir_layer * night_weight)

        rgb = use_xp.clip(use_xp.stack([r, g, b], axis=-1), 0.0, 1.0)
        rgb_u8 = (rgb * 255).astype(use_xp.uint8)
        alpha_ch = use_xp.where(_nan_alpha, use_xp.uint8(0), use_xp.uint8(255))
        rgba_u8 = use_xp.concatenate([rgb_u8, alpha_ch[:, :, None]], axis=-1)
        return cls._to_np(rgba_u8)

    @staticmethod
    def _compute_solar_zenith_angle(nc_path, h, w):
        _key = (str(nc_path), int(h), int(w))
        _cached = _SZA_CACHE.get(_key)
        if _cached is not None:
            return _cached
        try:
            from pyproj import CRS, Transformer
            from datetime import datetime, timezone
            import re

            # If nc_path is a synthetic per-band file, try to find original NC file
            _orig_path = nc_path
            try:
                _p = Path(nc_path)
                _orig_files = list(_p.parent.glob("*C??*.nc")) or list(_p.parent.glob("*_G??_*.nc"))
                if _orig_files:
                    _orig_path = _orig_files[0]
            except Exception:
                pass

            with xr.open_dataset(_orig_path, engine="netcdf4", mask_and_scale=False) as ds:
                # --- Extract projection CRS ---
                proj = None
                for pname in ('goes_imager_projection', 'projection',
                              'fixed_projection', 'geostationary_projection'):
                    if pname in ds:
                        proj = ds[pname]
                        break
                if proj is None:
                    for v in ds.data_vars:
                        if 'longitude_of_projection_origin' in ds[v].attrs:
                            proj = ds[v]
                            break
                if proj is None:
                    for attr in ('longitude_of_projection_origin', 'satellite_longitude',
                                 'goes_imager_projection_longitude'):
                        if attr in ds.attrs:
                            sat_lon = float(ds.attrs[attr])
                            sat_h = float(ds.attrs.get('perspective_point_height',
                                        ds.attrs.get('satellite_height', 35785863.0)))
                            sweep = ds.attrs.get('sweep_angle_axis', '')
                            break
                    else:
                        # Try ads.json sidecar
                        sidecar = Path(str(nc_path).rsplit('.', 1)[0] + '.ads.json')
                        if not sidecar.exists():
                            for _f in nc_path.parent.glob("AHI_*.ads.json"):
                                sidecar = _f
                                break
                        if sidecar.exists():
                            try:
                                import json
                                with open(sidecar) as f:
                                    ads = json.load(f)
                                crs_info = ads.get('crs', {})
                                sat_lon = float(crs_info.get('longitude_of_projection_origin',
                                              ads.get('satellite_longitude', 140.7)))
                                sat_h = float(crs_info.get('perspective_point_height',
                                              ads.get('satellite_height', 35785863.0)))
                                sweep = crs_info.get('sweep_angle_axis', '')
                            except Exception:
                                return np.zeros((h, w), dtype=np.float32)
                        else:
                            return np.zeros((h, w), dtype=np.float32)
                else:
                    sat_lon = float(proj.attrs.get('longitude_of_projection_origin', 140.7))
                    sat_h = float(proj.attrs.get('perspective_point_height', 35785863.0))
                    sweep = proj.attrs.get('sweep_angle_axis', '')
                sweep_param = f" +sweep={sweep}" if sweep else ""
                proj4 = f"+proj=geos +lon_0={sat_lon} +h={sat_h} +x_0=0 +y_0=0 +ellps=WGS84{sweep_param} +units=m +no_defs"
                crs = CRS.from_proj4(proj4)

                # --- Extract observation time ---
                obs_dt = None
                for attr in ('time_coverage_start', 'date_created', 'time_coverage_end'):
                    if attr in ds.attrs:
                        try:
                            from dateutil import parser as dtparser
                            obs_dt = dtparser.parse(str(ds.attrs[attr]))
                            break
                        except Exception:
                            continue
                if obs_dt is None and 'time_parameters' in ds.attrs:
                    try:
                        import json
                        tp = ds.attrs['time_parameters']
                        if isinstance(tp, str):
                            tp = json.loads(tp)
                        if isinstance(tp, dict):
                            for k in ('observation_start_time', 'nominal_start_time'):
                                if k in tp:
                                    from dateutil import parser as dtparser
                                    obs_dt = dtparser.parse(str(tp[k]))
                                    break
                    except Exception:
                        pass
                if obs_dt is None:
                    for attr in ('start_time', 'end_time'):
                        if attr in ds.attrs:
                            try:
                                from dateutil import parser as dtparser
                                obs_dt = dtparser.parse(str(ds.attrs[attr]))
                                break
                            except Exception:
                                continue
                if obs_dt is None and 'timestamp' in ds.attrs:
                    import re
                    m = re.search(r'(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})', str(ds.attrs['timestamp']))
                    if m:
                        from datetime import datetime, timezone
                        obs_dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                                          int(m.group(4)), int(m.group(5)), tzinfo=timezone.utc)
                if obs_dt is None and 'time' in ds.coords:
                    try:
                        tval = ds.coords['time'].values
                        if tval.size > 0:
                            t0 = tval.flat[0]
                            if hasattr(t0, 'item'):
                                t0 = t0.item()
                            from pandas import Timestamp
                            obs_dt = Timestamp(t0).to_pydatetime()
                    except Exception:
                        pass
                if obs_dt is None or obs_dt.tzinfo is None:
                    obs_dt = obs_dt.replace(tzinfo=timezone.utc) if obs_dt else datetime.now(timezone.utc)

                # --- Extract x/y coordinate arrays ---
                x, y = None, None
                if 'x' in ds.coords and 'y' in ds.coords:
                    x = ds.coords['x'].values.astype(float)
                    y = ds.coords['y'].values.astype(float)
                if x is None or y is None:
                    # Reconstruct coordinates from ADS sidecar or 'area' attribute
                    try:
                        ncols = ds.sizes.get('x', 0)
                        nrows = ds.sizes.get('y', 0)
                        if ncols > 0 and nrows > 0:
                            import json as _json
                            _ads = None
                            for _pat in ('AHI_*.ads.json', '*.ads.json'):
                                for _f in Path(nc_path).parent.glob(_pat):
                                    try:
                                        with open(_f) as _fh:
                                            _ads = _json.load(_fh)
                                        break
                                    except Exception:
                                        pass
                                if _ads:
                                    break
                            if _ads:
                                _am = _ads.get('area_meta')
                                if isinstance(_am, dict) and 'extent' in _am:
                                    ext = _am['extent']
                                    x = np.linspace(ext[0], ext[2], ncols)
                                    y = np.linspace(ext[3], ext[1], nrows)
                                if x is None:
                                    _gt = _ads.get('geotransform')
                                    if isinstance(_gt, (list, tuple)) and len(_gt) == 6:
                                        x = _gt[2] + np.arange(ncols) * _gt[0]
                                        y = _gt[5] + np.arange(nrows) * _gt[4]
                            # Fallback: parse from 'area' attribute
                            if x is None or y is None:
                                area_str = ds.attrs.get('area', '')
                                if area_str and 'Area extent:' in area_str:
                                    ext_part = area_str.split('Area extent:')[1].strip()
                                    cleaned = ext_part.replace('np.float64(', '').replace(')', '').strip('()')
                                    vals = [float(v.strip()) for v in cleaned.split(',')]
                                    if len(vals) == 4:
                                        x_min, y_min, x_max, y_max = vals
                                        x = np.linspace(x_min, x_max, ncols)
                                        y = np.linspace(y_max, y_min, nrows)
                    except Exception:
                        pass
                if x is None or y is None:
                    return np.zeros((h, w), dtype=np.float32)
                abs_max = max(abs(x.min()), abs(x.max()), abs(y.min()), abs(y.max()))
                if abs_max < 1.0:
                    x = x * sat_h
                    y = y * sat_h

                # Decimate to target h,w
                step_y = max(1, len(y) // h)
                step_x = max(1, len(x) // w)
                y_sub = y[::step_y][:h]
                x_sub = x[::step_x][:w]

                xx, yy = np.meshgrid(x_sub, y_sub)
                transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
                lons, lats = transformer.transform(xx, yy)

                # --- Solar position calculation ---
                from pyorbital.astronomy import sun_zenith_angle
                sza = sun_zenith_angle(obs_dt, lons, lats).astype(np.float32)
                if len(_SZA_CACHE) > 8:
                    _SZA_CACHE.clear()
                _SZA_CACHE[_key] = sza
                return sza

        except Exception as e:
            print(f"[SZA] ERROR: {e}")
            traceback.print_exc()
            return np.zeros((h, w), dtype=np.float32)

    _scale_offset_cache = {}

    @classmethod
    def _get_band_scale_offset(cls, nc_path, band, band_file_map=None):
        if band_file_map and band in band_file_map:
            nc_path = Path(band_file_map[band])
        cache_key = (str(nc_path), band)
        if cache_key in cls._scale_offset_cache:
            return cls._scale_offset_cache[cache_key]
        try:
            with xr.open_dataset(nc_path) as ds:
                scale = ds[band].attrs.get("scale_factor", 1.0)
                offset = ds[band].attrs.get("add_offset", 0.0)
                result = (float(scale), float(offset))
                cls._scale_offset_cache[cache_key] = result
                return result
        except Exception:
            return 1.0, 0.0

    @classmethod
    def clear_scale_offset_cache(cls):
        cls._scale_offset_cache.clear()
