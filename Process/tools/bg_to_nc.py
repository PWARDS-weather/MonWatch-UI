#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MONWATCH  –  CYCLONE  V3.5.0   (optimized speed + size)                      ║
║  bg_to_nc.py  –  Himawari AHI  .DAT/.bz2  →  single  _AHI.nc  + CRS          ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  CHANGELOG  v3.5.0                                                           ║
║  * Parallel band processing via --workers N (ThreadPoolExecutor)             ║
║  * Default compression: zlib+shuffle level 6 (sweet spot speed/size)         ║
║  * --small mode: maximum compression level 9 for smallest NC files           ║
║  * B03 chunk rows doubled (4400 → 5 segments instead of 10)                 ║
║  * Reduced gc.collect() calls (4 removed) for faster execution               ║
║  * Calibration header parsing stops early after 16 bands found              ║
║  * Sidecar merge: existing .ads.json data preserved across runs              ║
║  * Eliminated redundant 2nd HSD header parse (reuses cal_dict)               ║
║  * v3.4.0 changelog preserved below                                          ║
║    - Full HSD header parser – Block 1–11 + calibration extension             ║
║    - Direct .bz2 support (no separate extraction step)                       ║
║    - Calibration coefficients stored as structured NC variable               ║
║    - Japan/Target sector metadata added as NC attributes                     ║
║    - Fixed `_sanitize_attrs` – list/tuple attributes preserve structure      ║
║    - Improved type handling in attribute sanitisation                        ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys
import re
import gc
import json
import shutil
import traceback
import logging
import bz2
import tempfile
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from time import perf_counter
from collections import Counter
import struct
from concurrent.futures import ThreadPoolExecutor
import threading

import numpy as np
import xarray as xr
import dask

# ── Optional memory profiling ────────────────────────────────────────────────
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# ── Logging – write to STDOUT so Process_dat.py does not see errors ──────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("cyclone.bg_to_nc")

# ── Satpy version diagnostic ─────────────────────────────────────────────────
try:
    import satpy as _satpy_check
    log.info(f"[env] satpy {_satpy_check.__version__}  @ {_satpy_check.__file__}")
    log.info(f"[env] python {sys.version.split()[0]}  @ {sys.executable}")
    from satpy.dataset.dataid import WavelengthRange as _WR
    _WR(0.5, 0.6, 0.7)
    log.info("[env] WavelengthRange OK")
    del _satpy_check, _WR
except Exception as _env_exc:
    log.error(f"[env] Satpy environment check FAILED: {_env_exc}")
    log.error("[env] Try:  pip uninstall satpy -y && pip install satpy==0.60.0")

# netCDF4/HDF5 is not thread-safe for concurrent file writes. All NC file
# writes (createVariable + slicing + attr assignments) must be serialized via
# this lock, otherwise parallel band writers race and throw "NetCDF: HDF error".
_NC_WRITE_LOCK = threading.Lock()

def _histogram_quantiles(valid: np.ndarray, dmin: float, dmax: float,
                         bins: int = 65536) -> Tuple[float, float]:
    """p5/p95 via a histogram of `bins` bins over [dmin, dmax] — O(n) instead
    of np.nanpercentile's O(n log n) sort. Rounding-level accuracy vs the
    legacy percentile (same bin count as the B03 streaming path), so sidecar
    p5/p95 stay comparable."""
    import math as _math
    if valid.size == 0 or not (_math.isfinite(dmin) and _math.isfinite(dmax)):
        return float("nan"), float("nan")
    if dmax <= dmin:
        return float(dmin), float(dmax)
    hist, edges = np.histogram(valid, bins=bins, range=(dmin, dmax))
    cdf = np.cumsum(hist)
    total = float(cdf[-1])
    if total <= 0.0:
        return float("nan"), float("nan")
    bin_width = (dmax - dmin) / bins

    def _quantile(q: float) -> float:
        target = q * total
        idx = int(np.searchsorted(cdf, target))
        idx = min(max(idx, 0), bins - 1)
        prev_c = float(cdf[idx - 1]) if idx > 0 else 0.0
        cur_c = float(cdf[idx])
        lo = dmin + idx * bin_width
        frac = 0.5 if cur_c <= prev_c else (target - prev_c) / (cur_c - prev_c)
        return lo + frac * bin_width

    return _quantile(0.05), _quantile(0.95)


def _compute_band_stats(band: str, data: np.ndarray, attrs: dict = None) -> dict:
    """Compute the exact band_stats dict used by AdvancedDataSystem.register_band.

    Mirrors the legacy computation so pre-computing stats in worker threads
    produces byte-identical sidecar values.
    """
    valid = data[np.isfinite(data)]
    if valid.size == 0:
        return {"qc": "FAILED", "reason": "all-NaN"}
    coverage = valid.size / data.size
    dmin = float(np.nanmin(valid))
    dmax = float(np.nanmax(valid))
    p5, p95 = _histogram_quantiles(valid, dmin, dmax)
    stats = {
        "qc": "OK" if coverage >= 0.80 else "PARTIAL",
        "shape": list(data.shape),
        "coverage_pct": round(coverage * 100, 2),
        "min": dmin,
        "max": dmax,
        "mean": float(np.nanmean(valid)),
        "std": float(np.nanstd(valid)),
        "p5": p5,
        "p95": p95,
    }
    if attrs:
        stats["units"] = attrs.get("units", "unknown")
        stats["calibration"] = attrs.get("calibration", "unknown")
        stats["wavelength"] = attrs.get("wavelength", "unknown")
    return stats


# ══════════════════════════════════════════════════════════════════════════════
#  ADVANCED DATA SYSTEM  (with CRS support in sidecar)
# ══════════════════════════════════════════════════════════════════════════════
class AdvancedDataSystem:
    VERSION = "1.4.0"   # bumped to reflect multi-resolution geotransforms

    def __init__(self, timestamp: str, satellite: str = "Himawari-9"):
        self.timestamp = timestamp
        self.satellite = satellite
        self.created_utc = datetime.now(timezone.utc).isoformat()
        self.nc_path = ""
        self.nc_size_mb = 0.0
        self.band_stats: Dict[str, dict] = {}
        self.qc_flags: Dict[str, int] = {}
        self.provenance: List[dict] = []
        self.crs: Optional[Dict[str, any]] = None
        self.geotransform: Optional[list] = None
        self.geotransform_2km: Optional[list] = None
        self.geotransform_1km: Optional[list] = None
        self.geotransform_0_5km: Optional[list] = None
        self.ref_grid_w: Optional[int] = None
        self.ref_grid_h: Optional[int] = None
        self.ref_grid_1km_w: Optional[int] = None
        self.ref_grid_1km_h: Optional[int] = None
        self.ref_grid_0_5km_w: Optional[int] = None
        self.ref_grid_0_5km_h: Optional[int] = None
        self.area_meta: Optional[dict] = None
        self.coff_loff: Optional[Dict[str, float]] = None
        self.sector: Optional[str] = None
        self.observation_start_time: Optional[str] = None
        self.observation_end_time: Optional[str] = None
        self.sun_position: Optional[list] = None
        self.solar_geometry: Optional[dict] = None
        self.physical_constants: Optional[dict] = None
        self._derecho_hooks: Dict[str, object] = {}

    def set_crs(self, crs_dict: Dict[str, any]) -> None:
        self.crs = crs_dict

    def set_geotransform(self, gt: list) -> None:
        self.geotransform = gt

    def set_geotransform_2km(self, gt: list) -> None:
        self.geotransform_2km = gt

    def set_geotransform_1km(self, gt: list) -> None:
        self.geotransform_1km = gt

    def set_geotransform_0_5km(self, gt: list) -> None:
        self.geotransform_0_5km = gt

    def set_ref_grid_size(self, w: int, h: int) -> None:
        self.ref_grid_w = w
        self.ref_grid_h = h

    def set_ref_grid_1km(self, w: int, h: int) -> None:
        self.ref_grid_1km_w = w
        self.ref_grid_1km_h = h

    def set_ref_grid_0_5km(self, w: int, h: int) -> None:
        self.ref_grid_0_5km_w = w
        self.ref_grid_0_5km_h = h

    def set_area_meta(self, meta: dict) -> None:
        self.area_meta = meta

    def set_coff_loff(self, cl: Dict[str, float]) -> None:
        self.coff_loff = cl

    def set_sector(self, sector: str) -> None:
        self.sector = sector

    def register_band(self, band: str, data: np.ndarray = None, attrs: dict = None,
                      stats: dict = None):
        if stats is None:
            stats = _compute_band_stats(band, data, attrs)
        if stats.get("qc") == "FAILED":
            self.qc_flags[band] = 2
            self.band_stats[band] = stats
            self._derecho_band_hook(band, None)
            return
        self.band_stats[band] = stats
        self.qc_flags[band] = 0 if stats["qc"] == "OK" else 1
        self._derecho_band_hook(band, stats)

    def register_failed_band(self, band: str, reason: str):
        self.band_stats[band] = {"qc": "FAILED", "reason": reason}
        self.qc_flags[band] = 2

    def add_provenance(self, action: str, detail: str):
        self.provenance.append({
            "utc": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "detail": detail,
        })

    def bands_ok(self) -> List[str]:
        return [b for b, q in self.qc_flags.items() if q == 0]

    def bands_available(self) -> List[str]:
        return [b for b, q in self.qc_flags.items() if q <= 1]

    def scale_range(self, band: str) -> Tuple[float, float]:
        s = self.band_stats.get(band, {})
        return s.get("p5", 0.0), s.get("p95", 100.0)

    def to_dict(self) -> dict:
        loaded = [b for b, q in self.qc_flags.items() if q <= 1]
        failed = [b for b, q in self.qc_flags.items() if q == 2]
        d = {
            "format": "PWARDS ADS format system v1.0",
            "ads_version": self.VERSION,
            "satellite": self.satellite,
            "timestamp": self.timestamp,
            "created_utc": self.created_utc,
            "nc_path": self.nc_path,
            "nc_size_mb": self.nc_size_mb,
            "bands_loaded": sorted(loaded),
            "bands_failed": sorted(failed),
            "band_count": len(loaded),
            "band_stats": self.band_stats,
            "qc_flags": self.qc_flags,
            "provenance": self.provenance,
        }
        if self.crs is not None:
            d["crs"] = self.crs
        if self.geotransform is not None:
            d["geotransform"] = self.geotransform
        if self.geotransform_2km is not None:
            d["geotransform_2km"] = self.geotransform_2km
        if self.geotransform_1km is not None:
            d["geotransform_1km"] = self.geotransform_1km
        if self.geotransform_0_5km is not None:
            d["geotransform_0_5km"] = self.geotransform_0_5km
        if self.ref_grid_w is not None and self.ref_grid_h is not None:
            d["ref_grid_w"] = self.ref_grid_w
            d["ref_grid_h"] = self.ref_grid_h
        if self.ref_grid_1km_w is not None and self.ref_grid_1km_h is not None:
            d["ref_grid_1km_w"] = self.ref_grid_1km_w
            d["ref_grid_1km_h"] = self.ref_grid_1km_h
        if self.ref_grid_0_5km_w is not None and self.ref_grid_0_5km_h is not None:
            d["ref_grid_0_5km_w"] = self.ref_grid_0_5km_w
            d["ref_grid_0_5km_h"] = self.ref_grid_0_5km_h
        if self.area_meta is not None:
            d["area_meta"] = self.area_meta
        if self.coff_loff is not None:
            d["coff_loff"] = self.coff_loff
        if self.sector is not None:
            d["sector"] = self.sector
        if self.observation_start_time is not None:
            d["observation_start_time"] = self.observation_start_time
        if self.observation_end_time is not None:
            d["observation_end_time"] = self.observation_end_time
        if self.sun_position is not None:
            d["sun_position"] = self.sun_position
        if self.solar_geometry is not None:
            d["solar_geometry"] = self.solar_geometry
        if self.physical_constants is not None:
            d["physical_constants"] = self.physical_constants
        return d

    def write_sidecar(self, nc_path: Path, sidecar_path: Path = None) -> Path:
        self.nc_path = str(nc_path)
        self.nc_size_mb = round(nc_path.stat().st_size / 1e6, 2)
        sidecar = sidecar_path or nc_path.with_suffix(".ads.json")
        data = self
        if sidecar.exists():
            try:
                with open(sidecar, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                self.band_stats.update(existing.get("band_stats", {}))
                self.qc_flags.update(existing.get("qc_flags", {}))
                self.provenance.extend(existing.get("provenance", []))
                for key in ["crs", "geotransform", "geotransform_2km",
                            "geotransform_1km", "geotransform_0_5km", "area_meta", "coff_loff", "sector"]:
                    val = existing.get(key)
                    if val is not None and getattr(self, key, None) is None:
                        setattr(self, key, val)
                log.info(f"[ADS] Merged existing sidecar ({len(existing.get('band_stats', {}))} existing bands)")
            except Exception as exc:
                log.warning(f"[ADS] Could not merge existing sidecar: {exc}")
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(data.to_dict(), f, indent=2)
        log.info(f"[ADS] Sidecar: {sidecar.name}  ({self.nc_size_mb} MB nc)")
        return sidecar

    def embed_in_dataset(self, ds: xr.Dataset) -> xr.Dataset:
        loaded = self.bands_available()
        ds.attrs["ads_version"] = self.VERSION
        ds.attrs["ads_created_utc"] = self.created_utc
        ds.attrs["ads_bands_loaded"] = " ".join(sorted(loaded))
        ds.attrs["ads_bands_in_nc"] = " ".join(sorted(loaded))
        ds.attrs["ads_band_count"] = len(loaded)
        ds.attrs["crs_location"] = "sidecar_ads.json"
        return ds

    @classmethod
    def load_sidecar(cls, nc_path: Path) -> Optional["AdvancedDataSystem"]:
        sidecar = nc_path.with_suffix(".ads.json")
        if not sidecar.exists():
            return None
        try:
            with open(sidecar, "r", encoding="utf-8") as f:
                data = json.load(f)
            ads = cls(data.get("timestamp", ""), data.get("satellite", ""))
            ads.created_utc = data.get("created_utc", "")
            ads.nc_path = data.get("nc_path", str(nc_path))
            ads.nc_size_mb = data.get("nc_size_mb", 0.0)
            ads.band_stats = data.get("band_stats", {})
            ads.qc_flags = data.get("qc_flags", {})
            ads.provenance = data.get("provenance", [])
            ads.crs = data.get("crs", None)
            ads.geotransform = data.get("geotransform", None)
            ads.geotransform_2km = data.get("geotransform_2km", None)
            ads.geotransform_1km = data.get("geotransform_1km", None)
            ads.geotransform_0_5km = data.get("geotransform_0_5km", None)
            ads.ref_grid_w = data.get("ref_grid_w", None)
            ads.ref_grid_h = data.get("ref_grid_h", None)
            ads.ref_grid_1km_w = data.get("ref_grid_1km_w", None)
            ads.ref_grid_1km_h = data.get("ref_grid_1km_h", None)
            ads.ref_grid_0_5km_w = data.get("ref_grid_0_5km_w", None)
            ads.ref_grid_0_5km_h = data.get("ref_grid_0_5km_h", None)
            ads.area_meta = data.get("area_meta", None)
            return ads
        except Exception as exc:
            log.warning(f"[ADS] Could not load sidecar {sidecar.name}: {exc}")
            return None

    def _derecho_band_hook(self, band: str, stats: Optional[dict]): pass
    def _derecho_ready_hook(self, nc_path: Path, ads_dict: dict): pass
    def _derecho_alert_hook(self, alert_type: str, payload: dict): pass


# ══════════════════════════════════════════════════════════════════════════════
#  HSD HEADER PARSING – Full (Blocks 1–11 + Calibration Extension)
# ══════════════════════════════════════════════════════════════════════════════
# HSD format reference (satpy ahi_hsd.py):
#   Block 1: Basic Information
#   Block 2: Data Information
#   Block 3: Projection Information (COFF, LOFF, sub_lon)
#   Block 4: Navigation Information
#   Block 5: Calibration Info + calibration extension (VIS or IR specific)
#   Block 6: Inter-Calibration Information (GSICS)
#   Block 7: Segment Information
#   Block 8: Navigation Correction Information
#   Block 9: Observation Time Information
#   Block 10: Error Information
#   Block 11: Spare
#   Block 12: Image Data Section (raw <u2 counts)
#
# Each block: [hblock_number(u1), blocklength(u2), ...data... ]
# ---------------------------------------------------------------------------

# HSD struct definitions (mirrors satpy's ahi_hsd.py)
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

# Block 5 is actually "Calibration Info" in satpy's numbering.
# The user's "Block 5 (Data Position)" corresponds to HSD Segment Info (block7).
# We store both.

# Band metadata: which bands are VIS (1-6) vs IR (7-16)
_HSD_VIS_BANDS = set(range(1, 7))
_HSD_IR_BANDS = set(range(7, 17))


def _read_np_field(val, idx=0):
    """Extract a scalar from a numpy void/array field."""
    if hasattr(val, "item"):
        return val.item()
    if isinstance(val, np.ndarray):
        return val.flat[idx].item() if val.size > idx else 0.0
    return float(val) if val is not None else 0.0


_HSD_HEADER_PREFIX_BYTES = 32768  # entire HSD header (Blocks 1-11) is ~1.5 KB


def _bz2_decompress_prefix(filepath: Path, max_bytes: int = _HSD_HEADER_PREFIX_BYTES) -> bytes:
    """Stream-decompress only the first max_bytes of a .bz2 file.

    bz2 streams cannot be seeked, but the HSD header is tiny, so decompressing
    just a prefix avoids decompressing the entire (up to ~1 GB) file when only
    the header blocks are needed.
    """
    out = bytearray()
    dec = bz2.BZ2Decompressor()
    with open(filepath, "rb") as f:
        while len(out) < max_bytes:
            chunk = f.read(65536)
            if not chunk:
                break
            out += dec.decompress(chunk)
            if dec.eof:
                break
    return bytes(out[:max_bytes])


def _parse_full_hsd_header(filepath: Path) -> Optional[dict]:
    """Read ALL HSD header blocks from a .DAT or .bz2 file.

    Handles .bz2 by decompressing to a temp file first.
    Returns dict with keys:
        block1..block11, calibration_ext (VIS or IR),
        cal_type ("VIS"/"IR"), data_offset (byte offset of Block 12),
        data_shape (nlines, ncols), nbits_per_pixel,
        band_number, sub_lon, coff, loff, cfac, lfac,
        earth_equatorial_radius, earth_polar_radius,
        distance_from_earth_center,
        segment_number, total_segments,
        observation_start_time, observation_end_time,
        gain_count2rad, offset_count2rad,
        central_wavelength,
    Returns None on failure.
    """
    is_bz2 = filepath.suffix.lower() == ".bz2"
    temp_path = None
    try:
        if is_bz2:
            # Only the header blocks are needed — stream-decompress a small prefix
            # (a full-file decompress can be ~1 GB just to read a ~1.5 KB header).
            raw = _bz2_decompress_prefix(filepath, _HSD_HEADER_PREFIX_BYTES)
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".hsd")
            temp_path = Path(temp.name)
            temp.write(raw)
            temp.close()
            fpath = str(temp_path)
        else:
            fpath = str(filepath)

        with open(fpath, "rb") as f:
            # ----- Block 1: Basic Information -----
            b1 = np.fromfile(f, dtype=_HSD_BASIC_INFO, count=1)
            if b1.size == 0:
                return None
            b1_len = int(b1["blocklength"].item())
            f.seek(b1_len)

            # ----- Block 2: Data Information -----
            b2 = np.fromfile(f, dtype=_HSD_DATA_INFO, count=1)
            if b2.size == 0:
                return None
            b2_len = int(b2["blocklength"].item())
            f.seek(b1_len + b2_len)

            # ----- Block 3: Projection Information -----
            b3 = np.fromfile(f, dtype=_HSD_PROJ_INFO, count=1)
            if b3.size == 0:
                return None
            b3_len = int(b3["blocklength"].item())
            f.seek(b1_len + b2_len + b3_len)

            # ----- Block 4: Navigation Information -----
            b4 = np.fromfile(f, dtype=_HSD_NAV_INFO, count=1)
            if b4.size == 0:
                return None
            b4_len = int(b4["blocklength"].item())
            f.seek(b1_len + b2_len + b3_len + b4_len)

            # ----- Block 5: Calibration Information -----
            b5 = np.fromfile(f, dtype=_HSD_CAL_INFO, count=1)
            if b5.size == 0:
                return None
            band_num = int(b5["band_number"].item())
            b5_len = int(b5["blocklength"].item())
            b5_start = b1_len + b2_len + b3_len + b4_len

            # Calibration extension (VIS or IR specific) sits immediately AFTER
            # the block5 struct. block5.blocklength already includes it (both
            # VIS and IR), so data_offset never adds the extension size again.
            f.seek(b5_start + int(_HSD_CAL_INFO.itemsize))
            if band_num in _HSD_VIS_BANDS:
                cal_ext = np.fromfile(f, dtype=_HSD_VISCAL, count=1)
                cal_type = "VIS"
                data_offset = b5_start + b5_len
            else:
                cal_ext = np.fromfile(f, dtype=_HSD_IRCAL, count=1)
                cal_type = "IR"
                data_offset = b5_start + b5_len

            # ----- Block 6: Inter-Calibration Information -----
            f.seek(data_offset)
            b6 = np.fromfile(f, dtype=_HSD_INTERCAL, count=1)
            if b6.size == 0:
                return None
            b6_len = int(b6["blocklength"].item())
            data_offset += b6_len

            # ----- Block 7: Segment Information -----
            f.seek(data_offset)
            b7 = np.fromfile(f, dtype=_HSD_SEGMENT_INFO, count=1)
            if b7.size == 0:
                return None
            b7_len = int(b7["blocklength"].item())
            data_offset += b7_len

            # ----- Block 8: Navigation Correction Info -----
            f.seek(data_offset)
            b8 = np.fromfile(f, dtype=_HSD_NAVCORR_INFO, count=1)
            if b8.size == 0:
                return None
            num_corr = int(b8["numof_correction_info_data"].item())
            corr_size = int(_HSD_NAVCORR_SUB.itemsize) * num_corr
            b8_len = int(b8["blocklength"].item())
            data_offset += b8_len

            # ----- Block 9: Observation Time Info -----
            f.seek(data_offset)
            b9 = np.fromfile(f, dtype=_HSD_OBS_TIME_INFO, count=1)
            if b9.size == 0:
                return None
            num_obs = int(b9["number_of_observation_times"].item())
            obs_size = int(_HSD_OBS_LINE_TIME.itemsize) * num_obs
            b9_len = int(b9["blocklength"].item())
            data_offset += b9_len

            # ----- Block 10: Error Info -----
            f.seek(data_offset)
            b10 = np.fromfile(f, dtype=_HSD_ERROR_INFO, count=1)
            if b10.size == 0:
                return None
            num_err = int(b10["number_of_error_info_data"].item())
            err_size = int(_HSD_ERROR_LINE.itemsize) * num_err
            b10_len = int(b10["blocklength"].item())
            data_offset += b10_len

            # ----- Block 11: Spare -----
            f.seek(data_offset)
            b11 = np.fromfile(f, dtype=_HSD_SPARE, count=1)
            if b11.size == 0:
                return None
            b11_len = int(b11["blocklength"].item())
            data_offset += b11_len

            # ----- Block 12: Image Data starts here -----
            ncols = int(b2["number_of_columns"].item())
            nlines = int(b2["number_of_lines"].item())

            # Build header dict
            sat = _read_np_field(b1["satellite"])
            sat_name = sat.decode("utf-8", errors="replace").strip() \
                if isinstance(sat, bytes) else str(sat).strip()

            obs_area = _read_np_field(b1["observation_area"])
            obs_area = obs_area.decode("utf-8", errors="replace").strip() \
                if isinstance(obs_area, bytes) else str(obs_area).strip()

            def _mjd_to_dt(mjd):
                return datetime(1858, 11, 17) + timedelta(days=float(mjd))

            obs_start = _mjd_to_dt(b1["observation_start_time"].item())
            obs_end = _mjd_to_dt(b1["observation_end_time"].item())

            hdr = {
                "block1": _sanitize_np_struct(b1),
                "block2": _sanitize_np_struct(b2),
                "block3": _sanitize_np_struct(b3),
                "block4": _sanitize_np_struct(b4),
                "block5": _sanitize_np_struct(b5),
                "calibration_ext": _sanitize_np_struct(cal_ext),
                "cal_type": cal_type,
                "block6": _sanitize_np_struct(b6),
                "block7": _sanitize_np_struct(b7),
                "block8": _sanitize_np_struct(b8),
                "block9": _sanitize_np_struct(b9),
                "block10": _sanitize_np_struct(b10),
                "block11": _sanitize_np_struct(b11),
                "data_offset": int(data_offset),
                "data_shape": (int(nlines), int(ncols)),
                "satellite_name": sat_name,
                "observation_area": obs_area,
                "band_number": int(band_num),
                "sub_lon": float(b3["sub_lon"].item()),
                "coff": float(b3["COFF"].item()),
                "loff": float(b3["LOFF"].item()),
                "cfac": int(b3["CFAC"].item()),
                "lfac": int(b3["LFAC"].item()),
                "earth_equatorial_radius": float(b3["earth_equatorial_radius"].item()) * 1000.0,
                "earth_polar_radius": float(b3["earth_polar_radius"].item()) * 1000.0,
                "distance_from_earth_center": float(b3["distance_from_earth_center"].item()),
                "nbits_per_pixel": int(b2["number_of_bits_per_pixel"].item()),
                "segment_number": int(b7["segment_sequence_number"].item()),
                "total_segments": int(b7["total_number_of_segments"].item()),
                "observation_start_time": obs_start.isoformat(),
                "observation_end_time": obs_end.isoformat(),
                "gain_count2rad": float(b5["gain_count2rad_conversion"].item()),
                "offset_count2rad": float(b5["offset_count2rad_conversion"].item()),
                "central_wavelength": float(b5["central_wave_length"].item()),
            }

            # VIS-specific calibration fields
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

    except Exception as exc:
        log.warning(f"[HSD] Header parse failed for {filepath.name}: {exc}")
        return None
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def _sanitize_np_struct(struct_arr):
    """Convert a numpy structured array (count=1) to a plain dict of scalars."""
    d = {}
    for name in struct_arr.dtype.names:
        val = struct_arr[name]
        if val.size == 1:
            v = val.flat[0]
            if isinstance(v, bytes):
                v = v.decode("utf-8", errors="replace").strip()
            elif hasattr(v, "item"):
                v = v.item()
            d[name] = v
        else:
            d[name] = [x.item() if hasattr(x, "item") else str(x) for x in val.flat]
    return d


def _extract_calibration_data(hdr: dict) -> Optional[dict]:
    """Extract per-band calibration coefficients from a single HSD header.

    Returns a dict with keys: band, radiance_slope, radiance_intercept,
    reflectance_albedo_coeff, c0_rad2tb, c1_rad2tb, c2_rad2tb,
    calibration_units, central_wavelength_um, resolution_m.
    Returns None if hdr is None.
    """
    if hdr is None:
        return None
    band_num = hdr.get("band_number", 0)
    band_name = f"B{band_num:02d}"
    is_vis = band_num in _HSD_VIS_BANDS
    resolution = {1: 1000, 2: 1000, 3: 500, 4: 1000}.get(band_num, 2000)

    if is_vis:
        albedo = hdr.get("albedo_coeff", 0.0)
        cal_units = "reflectance" if albedo != 0 else "radiance"
        row = dict(
            band=band_name,
            radiance_slope=float(hdr.get("gain_count2rad", 0.0)),
            radiance_intercept=float(hdr.get("offset_count2rad", 0.0)),
            reflectance_albedo_coeff=float(albedo),
            c0_rad2tb=0.0, c1_rad2tb=0.0, c2_rad2tb=0.0,
            calibration_units=cal_units,
            central_wavelength_um=float(hdr.get("central_wavelength", 0.0)),
            resolution_m=int(resolution),
        )
    else:
        row = dict(
            band=band_name,
            radiance_slope=float(hdr.get("gain_count2rad", 0.0)),
            radiance_intercept=float(hdr.get("offset_count2rad", 0.0)),
            reflectance_albedo_coeff=0.0,
            c0_rad2tb=float(hdr.get("c0_rad2tb", 0.0)),
            c1_rad2tb=float(hdr.get("c1_rad2tb", 0.0)),
            c2_rad2tb=float(hdr.get("c2_rad2tb", 0.0)),
            calibration_units="brightness_temperature",
            central_wavelength_um=float(hdr.get("central_wavelength", 0.0)),
            resolution_m=int(resolution),
        )
    return row


def _read_hsd_band_data(filepath: Path, hdr: dict, dtype=np.float32) -> Optional[np.ndarray]:
    """Read Block 12 pixel data from a .DAT or .bz2 file.

    Uses hdr['data_offset'] and hdr['data_shape'] to locate the raw <u2 counts.
    Returns array of the requested dtype (default float32), or None on failure.
    """
    if hdr is None:
        return None
    is_bz2 = filepath.suffix.lower() == ".bz2"
    nlines, ncols = hdr["data_shape"]
    offset = hdr["data_offset"]
    temp_path = None

    try:
        if is_bz2:
            with bz2.open(filepath, "rb") as f:
                raw = f.read()
            buf = np.frombuffer(raw, dtype=np.uint8, offset=offset)
            data = buf.view(dtype="<u2").reshape(nlines, ncols).astype(dtype)
        else:
            # Use memmap for large .dat files (B03 FLDK)
            if nlines * ncols > 50_000_000:  # >50M pixels -> use memmap
                mm = np.memmap(str(filepath), dtype="<u2", mode="r",
                               offset=offset, shape=(nlines, ncols))
                data = mm.astype(dtype)
                del mm
            else:
                with open(str(filepath), "rb") as f:
                    f.seek(offset)
                    data = np.fromfile(f, dtype="<u2",
                                       count=nlines * ncols).reshape(nlines, ncols).astype(dtype)
        return data
    except Exception as exc:
        log.warning(f"[HSD] Data read failed for {filepath.name}: {exc}")
        return None
    finally:
        if temp_path and Path(temp_path).exists():
            try:
                Path(temp_path).unlink()
            except Exception:
                pass


# HSD coercion (backward compat alias)
_parse_hsd_proj_info = _parse_full_hsd_header


# ══════════════════════════════════════════════════════════════════════════════
#  FAST HSD BAND READER  (replaces satpy Scene for conversion)
# ══════════════════════════════════════════════════════════════════════════════
# Reads raw <u2 counts via numpy, assembles all segment files into the full-disk
# array, applies the same VIS/IR calibration as satpy's ahi_hsd reader, and
# masks space pixels to NaN. Pure numpy (GIL released) so bands can be processed
# in parallel worker threads without satpy/xarray/dask overhead.

# Per-file header cache so a file's HSD header is parsed at most once per run.
_hdr_cache: Dict[str, Optional[dict]] = {}
_area_def_cache: Dict[tuple, object] = {}
_space_mask_cache: Dict[tuple, np.ndarray] = {}


def _get_hdr(path) -> Optional[dict]:
    """Cached HSD header parse keyed by resolved path."""
    key = str(path)
    h = _hdr_cache.get(key)
    if h is None:
        h = _parse_full_hsd_header(path)
        _hdr_cache[key] = h
    return h


def _build_area_def(hdr: dict):
    """Build the full-disk AreaDefinition for an HSD band.

    Uses the raw projection LOFF/COFF (positive) with the full image size
    (segment rows x total segments), reproducing the exact full-disk extent
    and geostationary space mask that satpy produces after stacking the
    segment files. Verified pixel-identical to satpy 0.60 for B01/B13.
    """
    nrows, ncols = hdr["data_shape"]
    total_segments = int(hdr.get("total_segments", 1))
    if total_segments < 1:
        total_segments = max(1, nrows)
    full_rows = nrows * total_segments
    key = (int(hdr["cfac"]), int(hdr["lfac"]), float(hdr["coff"]), float(hdr["loff"]),
           full_rows, ncols, float(hdr["sub_lon"]),
           float(hdr["earth_equatorial_radius"]), float(hdr["earth_polar_radius"]),
           float(hdr["distance_from_earth_center"]))
    if key in _area_def_cache:
        return _area_def_cache[key]
    try:
        from satpy.readers.core._geos_area import get_area_extent, get_area_definition
        pdict = {
            "cfac": np.uint32(hdr["cfac"]),
            "lfac": np.uint32(hdr["lfac"]),
            "coff": np.float32(hdr["coff"]),
            "loff": np.float32(hdr["loff"]),
            "a": float(hdr["earth_equatorial_radius"]),
            "h": float(hdr["distance_from_earth_center"]) * 1000.0 - float(hdr["earth_equatorial_radius"]),
            "b": float(hdr["earth_polar_radius"]),
            "ssp_lon": float(hdr["sub_lon"]),
            "nlines": full_rows,
            "ncols": ncols,
            "scandir": "N2S",
        }
        aex = get_area_extent(pdict)
        pdict["a_name"] = str(hdr.get("observation_area", "FLDK"))
        pdict["a_desc"] = "AHI {} area".format(pdict["a_name"])
        pdict["p_id"] = "geosh{}".format(str(hdr.get("satellite_name", "9"))[-1] or "9")
        area = get_area_definition(pdict, aex)
        _area_def_cache[key] = area
        return area
    except Exception as exc:
        log.warning(f"[HSD] area def build failed: {exc}")
        return None


def _band_space_mask(area, shape: Tuple[int, int]) -> np.ndarray:
    """Geostationary space mask (True inside the Earth disk), cached per area."""
    key = (area.shape, str(area.crs), area.area_extent)
    if key in _space_mask_cache:
        return _space_mask_cache[key]
    try:
        from satpy.readers.core.utils import get_geostationary_mask
        m = get_geostationary_mask(area, chunks=(1100, 1100))
        mask = m.compute() if hasattr(m, "compute") else np.asarray(m)
        mask = np.asarray(mask, dtype=bool)
        if mask.shape != shape:
            mask = np.ones(shape, dtype=bool)
        _space_mask_cache[key] = mask
        return mask
    except Exception:
        mask = np.ones(shape, dtype=bool)
        _space_mask_cache[key] = mask
        return mask


def _calibrate_counts(hdr: dict, counts: np.ndarray) -> np.ndarray:
    """Apply satpy-identical VIS/IR calibration to raw counts (float32 in/out).

    counts must already have error/outside-scan pixels set to NaN.
    VIS → reflectance (%): (counts*gain+offset) * albedo_coeff * 100, clipped ≥0.
    IR  → brightness temperature (K): Planck-inverse + c0/c1/c2 polynomial.
    """
    if hdr["cal_type"] == "VIS":
        g = float(hdr.get("cali_gain", 0.0))
        o = float(hdr.get("cali_offset", 0.0))
        if g == 0.0 and o == 0.0:
            g = float(hdr["gain_count2rad"])
            o = float(hdr["offset_count2rad"])
        rad = counts * g + o
        return np.clip(rad * float(hdr["albedo_coeff"]) * 100.0, 0.0, None).astype(np.float32)
    rad = counts * float(hdr["gain_count2rad"]) + float(hdr["offset_count2rad"])
    rad = np.where(rad == 0.0, np.nan, rad)
    cwl = float(hdr["central_wavelength"]) * 1e-6
    hh = float(hdr["planck_constant"])
    cc = float(hdr["speed_of_light"])
    kk = float(hdr["boltzmann_constant"])
    a_ = (hh * cc) / (kk * cwl)
    te = a_ / np.log((2.0 * hh * cc ** 2) / (rad * 1.0e6 * cwl ** 5) + 1.0)
    tb = float(hdr["c0_rad2tb"]) + float(hdr["c1_rad2tb"]) * te + float(hdr["c2_rad2tb"]) * te ** 2
    return np.clip(tb, 0.0, None).astype(np.float32)


def _band_attrs(hdr: dict) -> dict:
    """Construct the satpy-like DataArray attrs for a band from its HSD header."""
    band_num = int(hdr["band_number"])
    resolution = {1: 1000, 2: 1000, 3: 500, 4: 1000}.get(band_num, 2000)
    is_vis = hdr["cal_type"] == "VIS"
    cwl = float(hdr["central_wavelength"])
    return {
        "units": "%" if is_vis else "K",
        "calibration": "reflectance" if is_vis else "brightness_temperature",
        "wavelength": (cwl - 0.05, cwl, cwl + 0.05),
        "resolution": resolution,
        "platform_name": str(hdr.get("satellite_name", "Himawari-9")),
        "sensor": "AHI",
        "start_time": hdr.get("observation_start_time"),
        "end_time": hdr.get("observation_end_time"),
        "area": None,  # filled by caller once the AreaDefinition is built
        "name": f"B{band_num:02d}",
        "band": f"B{band_num:02d}",
        "calibration_type": hdr["cal_type"],
    }


def _read_band_assembled(band: str, files: List[Path]):
    """Read + calibrate one band from all its segment files into one full-disk
    float32 array with space pixels set to NaN (mirrors satpy ahi_hsd output).

    files: every HSD file for this band within a single sub-area group.
    Returns (array, sanitized_attrs, area_def, cal_row) or (None, None, None, None).
    """
    segs = []
    for f in files:
        hdr = _get_hdr(f)
        if hdr is None:
            continue
        data = _read_hsd_band_data(f, hdr)
        if data is None:
            continue
        segs.append((int(hdr.get("segment_number", 0)), hdr, data))
    if not segs:
        return None, None, None, None

    segs.sort(key=lambda s: s[0])
    hdr0 = segs[0][1]
    nlines_seg, ncols = hdr0["data_shape"]
    total_segments = max(int(hdr.get("total_segments", 1)) for _, hdr, _ in segs)
    if total_segments < 1:
        total_segments = len(segs)
    nrows = nlines_seg * total_segments

    # Allocate full-disk array and place each segment at its row offset.
    arr = np.full((nrows, ncols), np.nan, dtype=np.float32)
    area_def = None
    err = int(hdr0["block5"].get("count_value_error_pixels", 65535))
    outside = int(hdr0["block5"].get("count_value_outside_scan_pixels", 65535))
    for seg_no, hdr, data in segs:
        r0 = max(0, seg_no - 1) * nlines_seg
        r1 = min(r0 + data.shape[0], nrows)
        counts = data.astype(np.float32)
        counts[(data == err) | (data == outside)] = np.nan
        arr[r0:r1, :] = _calibrate_counts(hdr, counts)
        if area_def is None:
            area_def = _build_area_def(hdr)
    del counts, data

    # Space mask: NaN pixels outside the Earth disk (satpy parity).
    if area_def is not None:
        try:
            mask = _band_space_mask(area_def, arr.shape)
            arr[~mask] = np.nan
        except Exception:
            pass

    attrs = _band_attrs(hdr0)
    if area_def is not None:
        attrs["area"] = area_def
    return arr, attrs, area_def, hdr0


class _LazyBandView:
    """Lazy row-sliceable calibrated band (used for B03's 22000x22000 grid).

    Only the segment files overlapping the requested row range are read +
    calibrated, so the full float32 array is never materialised. Mirrors
    _read_band_assembled output semantics (NaN space mask included).
    Exposes .shape and .attrs so the B03 tiled writer can consume it directly.
    """

    def __init__(self, band: str, files: List[Path]):
        self._band = band
        self._files = list(files)
        self.shape = (0, 0)
        self.attrs = {}
        self._hdr0 = None
        self._segs = []
        self._area_def = None
        self._space_mask = None
        self._raw_counts_cache = {}
        ncols = 0
        for f in self._files:
            hdr = _get_hdr(f)
            if hdr is None:
                continue
            self._segs.append((int(hdr.get("segment_number", 0)), hdr, f))
            ncols = max(ncols, int(hdr["data_shape"][1]))
        if not self._segs:
            return
        self._segs.sort(key=lambda s: s[0])
        self._hdr0 = self._segs[0][1]
        nlines_seg = int(self._hdr0["data_shape"][0])
        total_segments = max(int(h.get("total_segments", 1)) for _, h, _ in self._segs)
        if total_segments < 1:
            total_segments = len(self._segs)
        self.shape = (nlines_seg * total_segments, ncols)
        self.attrs = _band_attrs(self._hdr0)
        self._area_def = _build_area_def(self._hdr0)
        if self._area_def is not None:
            self.attrs["area"] = self._area_def

    def _raw_counts(self, seg_no: int, hdr: dict, f: Path) -> Optional[np.ndarray]:
        """Read raw <u2 counts for one segment, cached so the two-pass B03
        writer decompresses each .bz2 exactly once."""
        key = int(seg_no)
        cached = self._raw_counts_cache.get(key)
        if cached is not None:
            return cached
        raw = _read_hsd_band_data(f, hdr, dtype=np.uint16)
        if raw is None:
            return None
        self._raw_counts_cache[key] = raw
        return raw

    def _calibrate_seg(self, seg_no: int, hdr: dict, f: Path) -> Optional[np.ndarray]:
        data = self._raw_counts(seg_no, hdr, f)
        if data is None:
            return None
        err = int(hdr["block5"].get("count_value_error_pixels", 65535))
        outside = int(hdr["block5"].get("count_value_outside_scan_pixels", 65535))
        counts = data.astype(np.float32)
        counts[(data == err) | (data == outside)] = np.nan
        return _calibrate_counts(hdr, counts)

    def __getitem__(self, key):
        if not self._segs:
            raise IndexError("no segments")
        rsl = key[0] if isinstance(key, tuple) else key
        r0, r1, _ = rsl.indices(self.shape[0])
        nrows = r1 - r0
        out = np.full((nrows, self.shape[1]), np.nan, dtype=np.float32)
        nlines_seg = int(self._hdr0["data_shape"][0])
        for seg_no, hdr, f in self._segs:
            s0 = max(0, seg_no - 1) * nlines_seg
            s1 = s0 + int(hdr["data_shape"][0])
            if s1 <= r0 or s0 >= r1:
                continue
            seg = self._calibrate_seg(seg_no, hdr, f)
            if seg is None:
                continue
            o0 = max(r0, s0)
            o1 = min(r1, s1)
            out[o0 - r0:o1 - r0, :] = seg[o0 - s0:o1 - s0, :]
        if self._area_def is not None:
            try:
                if self._space_mask is None:
                    self._space_mask = _band_space_mask(self._area_def, self.shape)
                out[~self._space_mask[r0:r1, :]] = np.nan
            except Exception:
                pass
        return out

    @property
    def values(self):
        return self[0:self.shape[0]]


# ══════════════════════════════════════════════════════════════════════════════
#  SOLAR GEOMETRY  (compute from observation time, NOT from HSD header's frozen value)
# ══════════════════════════════════════════════════════════════════════════════

def _compute_solar_geometry(obs_time_iso: str, satellite_lon: float = 140.7) -> dict:
    """Compute solar declination and hour angle at satellite longitude from UTC observation time.
    
    Uses standard NOAA solar position algorithm (no external deps).
    Returns dict with:
        solar_declination_deg  - declination of the Sun in degrees
        solar_hour_angle_deg   - hour angle at satellite sub-point in degrees
        sun_earth_distance_au  - Earth-Sun distance in AU
    With these + pixel lat/lon, SZA = arccos(sin(lat)*sin(dec) + cos(lat)*cos(dec)*cos(HA + lon_diff))
    """
    import math
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(obs_time_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    except Exception:
        return {}

    doy = dt.timetuple().tm_yday
    year = dt.year
    utc_hour = dt.hour + dt.minute / 60.0 + dt.second / 3600.0

    # Julian Day (simplified)
    jd = 367 * year - (7 * (year + (dt.month + 9) // 12)) // 4 + (275 * dt.month) // 9 + dt.day + 1721013.5 + utc_hour / 24.0

    # Julian Century
    jc = (jd - 2451545.0) / 36525.0

    # Mean anomaly (degrees)
    M = math.radians((357.5291 + 35999.0503 * jc) % 360)
    # Mean longitude
    L0 = math.radians((280.46646 + 36000.76983 * jc) % 360)
    # Equation of center
    C = (1.914602 - 0.004817 * jc - 0.000014 * jc * jc) * math.sin(M) + (0.019993 - 0.000101 * jc) * math.sin(2 * M) + 0.000289 * math.sin(3 * M)
    # Ecliptic longitude
    lambda_sun = math.radians(math.degrees(L0 + math.radians(C)) % 360)
    # Obliquity
    epsilon = math.radians(23.439291 - 0.0130042 * jc)
    # Solar declination
    dec = math.degrees(math.asin(math.sin(epsilon) * math.sin(lambda_sun)))
    # Equation of time (degrees)
    y = math.tan(epsilon / 2.0) ** 2
    eot_deg = (y * math.sin(2 * L0) - 2 * 0.0167 * math.sin(M) + 4 * 0.0167 * y * math.sin(M) * math.cos(2 * L0) - 0.5 * y * y * math.sin(4 * L0) - 1.25 * 0.0167 * 0.0167 * math.sin(2 * M))
    eot_deg = math.degrees(eot_deg)

    # Solar hour angle (degrees) at satellite longitude
    # HA = (UTC_hour * 15 + satellite_lon - 180 + EoT_correction) mod 360
    ha = (utc_hour * 15.0 + satellite_lon - 180.0 + eot_deg) % 360.0
    if ha > 180:
        ha -= 360

    # Earth-Sun distance (AU)
    sun_dist = 1.000001018 * (1.0 + 0.016708 * math.cos(M) + 0.000142 * math.cos(2 * M) + 0.000008 * math.cos(3 * M))

    return {
        "solar_declination_deg": round(dec, 6),
        "solar_hour_angle_deg": round(ha, 6),
        "sun_earth_distance_au": round(sun_dist, 8),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  CRS UTILITIES
# ══════════════════════════════════════════════════════════════════════════════
def _crs_from_area(area) -> Dict[str, any]:
    proj_dict = area.proj_dict
    crs_attrs = {
        "grid_mapping_name": "geostationary",
        "longitude_of_projection_origin": proj_dict.get("lon_0", 140.7),
        "latitude_of_projection_origin": proj_dict.get("lat_0", 0.0),
        "perspective_point_height": proj_dict.get("h", 35785831.0),
        "semi_major_axis": 6378137.0,
        "semi_minor_axis": 6356752.314140356,
        "inverse_flattening": 298.257222101,
    }
    if "x_0" in proj_dict:
        crs_attrs["false_easting"] = proj_dict["x_0"]
    if "y_0" in proj_dict:
        crs_attrs["false_northing"] = proj_dict["y_0"]
    crs_attrs["sweep_angle_axis"] = proj_dict.get("sweep", "x")
    return crs_attrs


def _geotransform_from_area(area):
    """Extract a 6-element geotransform (Affine) and area metadata from a satpy AreaDefinition.

    Returns (geotransform, area_extent_dict) where geotransform is
    [res_x, 0, x_origin, 0, -res_y, y_origin] and area_extent_dict
    contains [x_ll, y_ll, x_ur, y_ur] and shape [nrows, ncols].
    """
    try:
        x_ll, y_ll, x_ur, y_ur = area.area_extent
        nrows, ncols = area.shape
        res_x = (x_ur - x_ll) / ncols
        res_y = (y_ur - y_ll) / nrows
        x_origin = x_ll
        y_origin = y_ur
        gt = [float(res_x), 0.0, float(x_origin), 0.0, float(-res_y), float(y_origin)]
        area_meta = {
            "extent": [float(x_ll), float(y_ll), float(x_ur), float(y_ur)],
            "shape": [int(nrows), int(ncols)],
        }
        return gt, area_meta
    except Exception:
        return None, None


def _compute_resampled_geotransform(area, resolution: float = 2000.0):
    """Compute a resampled geotransform at the given resolution from any area definition.

    For non-FLDK sectors, the extent covers the sub-image area.
    This resamples the area extent to a uniform grid so overlay rendering
    always has a consistent reference resolution.
    """
    try:
        x_ll, y_ll, x_ur, y_ur = area.area_extent
        ncols = max(1, int(round((x_ur - x_ll) / resolution)))
        nrows = max(1, int(round((y_ur - y_ll) / resolution)))
        x_origin = x_ll
        y_origin = y_ur
        gt = [resolution, 0.0, float(x_origin), 0.0, -resolution, float(y_origin)]
        return gt, {
            "extent": [float(x_ll), float(y_ll), float(x_ur), float(y_ur)],
            "shape": [int(nrows), int(ncols)],
        }
    except Exception:
        return None, None


# ══════════════════════════════════════════════════════════════════════════════
#  CONVERSION PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
def find_datetime_folders(base_dir: Path) -> List[Path]:
    """Robust discovery of datetime folders for ANY sector (FLDK / Japan / Target).
    Supports both quick-scene folders named AHI-L1b-XXX_YYYY... and deep range
    structures (by falling back to any dir containing .dat or .bz2 when no named folders).
    Now also discovers folders with .bz2 files (direct read path).
    """
    sector_patterns = ["AHI-L1b-FLDK_*", "AHI-L1b-Japan_*", "AHI-L1b-Target_*"]
    folders = []
    for pat in sector_patterns:
        folders.extend([p for p in base_dir.rglob(pat) if p.is_dir()])
    if not folders:
        has_dat = any(base_dir.rglob("*.dat")) or any(base_dir.rglob("*.DAT"))
        has_bz2 = any(base_dir.rglob("*.bz2")) or any(base_dir.rglob("*.BZ2"))
        if has_dat or has_bz2:
            folders = [base_dir]
    return folders


def extract_timestamp(folder: Path) -> str:
    """Extract the timestamp suffix from a datetime folder name.

    Returns the exact suffix of the folder name so that the caller's
    `product = folder_name[:-(len(timestamp) + 1)]` slicing stays correct.

    Supports three naming styles (all anchored to end-of-name):
      * YYYYMMDD_HHMM         (quick-scene)      -> "20260808_0000"
      * YYYY_MM_DD_HHMM       (range download)   -> "2026_08_08_0000"
      * YYYY_MM_DD            (date-only folder) -> "2026_08_07"
    Falls back to the full folder name if nothing matches.
    """
    name = folder.name
    m = re.search(r'(\d{8})_(\d{4,6})$', name)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    m = re.search(r'(\d{4})_(\d{2})_(\d{2})_(\d{4,6})$', name)
    if m:
        return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}"
    m = re.search(r'(\d{4})_(\d{2})_(\d{2})$', name)
    if m:
        return f"{m.group(1)}_{m.group(2)}_{m.group(3)}"
    return name


def _detect_sector(folder: Path) -> str:
    """Robust sector detection by walking the folder and all ancestor directory names.
    Critical for range downloads where leaf dirs are just '00'/'10' etc but ancestors
    contain 'AHI-L1b-Japan' or 'AHI-L1b-Target' in the path (from RangeS3DownloadWorker slots).
    Also works for quick-scene folders named with the product prefix.
    """
    # Check the folder itself and every parent up to root
    candidates = [folder] + list(folder.parents)
    for p in candidates:
        name_upper = p.name.upper()
        if "JAPAN" in name_upper:
            return "Japan"
        if "TARGET" in name_upper:
            return "Target"
        if "FLDK" in name_upper:
            return "FLDK"
    return "FLDK"


_AREA_TOKEN_RE = re.compile(r'_(R\d{3}|JP\d{2})_', re.IGNORECASE)

def _extract_area_token(filename: str) -> Optional[str]:
    """Extract the sub-area token from a Himawari HSD filename.
    Examples: 'R301', 'R302', 'JP01', 'JP04', etc.
    Used to split mixed Target rapid-scan folders that contain multiple Rxxx regions.
    """
    m = _AREA_TOKEN_RE.search(Path(filename).name)
    return m.group(1) if m else None


_BAND_DIM: Dict[str, str] = {
    "B01": "1km", "B02": "1km", "B03": "500m", "B04": "1km",
    **{f"B{i:02d}": "2km" for i in range(5, 17)},
}


def _sanitize_attrs(attrs: dict) -> dict:
    SAFE = (str, int, float, bytes, bool)
    def _val(v):
        if isinstance(v, SAFE):
            return v
        if hasattr(v, "item") and not isinstance(v, np.ndarray):
            return v.item()
        if isinstance(v, np.ndarray):
            if v.dtype.kind == 'U':
                return '|'.join(str(x) for x in v.flat) if v.size > 1 else str(v.flat[0])
            if v.dtype.kind in ('i', 'u', 'f', 'c'):
                return v
            return str(v)
        if isinstance(v, dict):
            try:
                return json.dumps(
                    {str(ik): (float(iv) if hasattr(iv, "item") else str(iv))
                     for ik, iv in v.items()}
                )
            except Exception:
                return str(v)
        if isinstance(v, (list, tuple)):
            safe_items = [_val(x) for x in v]
            return str(safe_items)
        return str(v)
    return {k: _val(v) for k, v in attrs.items()}


_B03_TILE_ROWS = 4400
_B03_HIST_BINS = 65536  # streaming p5/p95 histogram resolution


def _pick_nc_engine() -> str:
    try:
        import netCDF4
        return "netcdf4"
    except ImportError:
        try:
            import scipy
            log.warning("[!] netCDF4 not installed → falling back to scipy (slow, no compression)")
            return "scipy"
        except ImportError:
            raise RuntimeError("No NetCDF backend. Run: pip install netCDF4")


def _check_disk_space(path: Path, required_gb: float = 4.0) -> bool:
    free_bytes = shutil.disk_usage(path).free
    free_gb = free_bytes / (1024**3)
    if free_gb < required_gb:
        log.error(f"⚠️  Insufficient disk space: {free_gb:.1f} GB free, need at least {required_gb} GB")
        return False
    log.info(f"✓ Disk space check passed: {free_gb:.1f} GB free")
    return True








def _merge_wind_data_if_present(nc_path: Path, datetime_folder: Path, ads_enabled: bool):
    """Look for NDMW*.nc wind data files in the folder and merge key wind variables
    (Latitude, Longitude, Wind_Speed, Wind_Dir, MedianPress, QI, u/v components)
    into the main NC file under 'wind_<channel>_*' prefixed names.
    Merges ALL wind files (each from a different channel: C08CS, C08CT, C09CS, etc.).
    Per-file dedup: skips any channel whose wind variables already exist in the NC."""
    try:
        import netCDF4 as nc4
        import xarray as xr
        import re

        wind_files = sorted(datetime_folder.glob("NDMW*.nc"))
        if not wind_files:
            return

        log.info(f"  [Wind] Found {len(wind_files)} NDMW files, merging into NC...")
        key_vars = ["Latitude", "Longitude", "Wind_Speed", "Wind_Dir",
                     "MedianPress", "QI", "UComponent1", "VComponent1",
                     "Altitude", "SatZen", "AMVChannel", "Target_Type",
                     "Wind_Speed_Shear", "BestFitPresLvl", "ExpectedErr"]

        # Pre-scan the NC to collect already-merged channel tags (per-file dedup)
        existing_channels = set()
        try:
            with xr.open_dataset(nc_path, engine="netcdf4") as ds:
                for v in ds.data_vars:
                    if v.startswith("wind_") and v.count("_") >= 2:
                        ch = v.split("_", 2)[1]
                        existing_channels.add(ch)
        except Exception:
            pass

        merged_count = 0
        for wf in wind_files:
            try:
                # Determine channel tag from filename (e.g. C03CT, C08CS, C14CT)
                ch_match = re.search(r'NDMW-AHI-(C\d{2}[A-Za-z]+)_', wf.name)
                if not ch_match:
                    log.warning(f"  [Wind] Could not parse channel tag from {wf.name}, skipping")
                    continue
                channel_tag = ch_match.group(1)

                # Per-file dedup: skip if this channel's data is already in the NC
                if channel_tag in existing_channels:
                    log.info(f"  [Wind] {channel_tag} already in NC — skipping {wf.name}")
                    try:
                        wf.unlink()
                        log.info(f"  [Wind] Deleted orphaned source {wf.name}")
                    except OSError:
                        pass
                    continue

                # Read wind data from source file (close before writing NC + deleting)
                with xr.open_dataset(wf, engine="netcdf4") as wds:
                    buf_dim = None
                    for d in wds.dims:
                        if "buffer" in d.lower():
                            buf_dim = d
                            break
                    if buf_dim is None:
                        log.warning(f"  [Wind] No buffer dim in {wf.name}, skipping")
                        continue

                    additions = {}
                    for var in key_vars:
                        if var in wds:
                            data = wds[var].values
                            safe_var = f"wind_{channel_tag}_{var}"
                            additions[safe_var] = data

                    if not additions:
                        continue

                    n_obs = list(additions.values())[0].shape[0]
                    wind_dim = f"_wind_nobs_{channel_tag}"

                # xarray Dataset closed — safe to write to NC and delete source
                with nc4.Dataset(str(nc_path), "a") as nc:
                    if wind_dim not in nc.dimensions:
                        nc.createDimension(wind_dim, n_obs)
                    for var_name, data in additions.items():
                        if var_name not in nc.variables:
                            var_obj = nc.createVariable(var_name, data.dtype, (wind_dim,))
                            var_obj[:] = data
                    nc.wind_data_merged = "true"
                    nc.wind_source_files = " ".join(f.name for f in wind_files)
                merged_count += 1
                existing_channels.add(channel_tag)
                log.info(f"  [Wind] Merged {wf.name} as {channel_tag} ({n_obs} obs)")

                # Delete source wind file (file handle already closed by xarray)
                try:
                    wf.unlink()
                    log.info(f"  [Wind] Deleted source {wf.name}")
                except OSError as e:
                    log.warning(f"  [Wind] Could not delete {wf.name}: {e}")
            except Exception as e:
                log.warning(f"  [Wind] Could not merge {wf.name}: {e}")

        if merged_count > 0:
            log.info(f"  [Wind] {merged_count}/{len(wind_files)} wind files merged successfully")
        else:
            log.info(f"  [Wind] All {len(wind_files)} wind files already present in NC — nothing to merge")
    except Exception as e:
        log.warning(f"  [Wind] Wind merge skipped: {e}")




def _convert_area(
    datetime_folder: Path,
    folder_name: str,
    sector: str,
    area,
    timestamp: str,
    available_bands,
    cal_dict,
    hsd_header,
    loaded_data,
    band_meta,
    loaded_bands,
    area_def,
    input_files,
    keep_dat: bool,
    ads_enabled: bool,
    profile: bool,
    fast_test: bool,
    workers: int,
    small: bool,
    fast: bool,
) -> Tuple[int, List]:
    """Write one sub-area's prepared bands to per-band NC files + sidecar.

    Returns (bands_written, failed_bands).
    """
    area_suffix = f"_{area}" if area else ""
    product = folder_name[:-(len(timestamp) + 1)]

    # Build global attrs shared by all per-band NC files (keep minimal -- shared metadata goes to ads.json)
    global_attrs = {
        "satellite": "Himawari-9",
        "timestamp": timestamp,
        "source": "Himawari AHI L1b",
        "sector": sector,
        "product": folder_name,
    }
    # Store physical constants in ads (shared, not in each NC)
    phys_constants = {
        "speed_of_light": 299792458.0,
        "planck_constant": 6.62607015e-34,
        "boltzmann_constant": 1.380649e-23,
    }
    if "B01" in loaded_data:
        _, b01_attrs, _ = loaded_data["B01"]
        for k in ("platform_name", "sensor", "start_time", "end_time"):
            if k in b01_attrs and k not in global_attrs:
                global_attrs[k] = b01_attrs[k]
    # Sector + sub-area metadata
    if sector == "Japan":
        global_attrs["japan_region_1"] = (
            "NE-Japan: 33.0N-46.0N, 130.0E-148.0E | "
            "Hokkaido, Northern/Eastern Honshu (Tokyo), Sea of Japan"
        )
        global_attrs["japan_region_2"] = (
            "SW-Japan: 24.0N-35.0N, 122.0E-137.0E | "
            "Western Honshu, Kyushu, Shikoku, Okinawa, East China Sea"
        )
        global_attrs["sector_description"] = "Japan rapid-scan: two-region coverage"
    elif sector == "Target":
        global_attrs["sector_description"] = "Target rapid-scan: 1000x1000km sub-region"
        if area:
            global_attrs["target_segment"] = area
        elif hsd_header:
            seg = hsd_header.get("segment_number", 0)
            total_seg = hsd_header.get("total_segments", 0)
            global_attrs["target_segment"] = f"{seg}/{total_seg}"
    if area:
        global_attrs["sub_area"] = area

    # Per-area HSD header for COFF/LOFF + observation times
    # (storm-following grids vary per sub-area)
    area_hdr0 = None
    for _b in band_meta:
        _h = band_meta[_b][2]
        if _h is not None:
            area_hdr0 = _h
            break
    hdr_use = area_hdr0 or hsd_header

    ads = AdvancedDataSystem(timestamp) if ads_enabled else None
    if ads:
        ads.add_provenance("start", f"folder={datetime_folder.name} area={area or 'full'}")

    # Pre-extract area_def and CRS from first band (already set during load)
    if ads and area_def:
        ads.set_crs(_crs_from_area(area_def))
        if ads.geotransform is None:
            gt, am = _geotransform_from_area(area_def)
            if gt: ads.set_geotransform(gt)
            if am and ads.area_meta is None: ads.set_area_meta(am)

    comp_level = 1 if fast_test else (2 if fast else (9 if small else 3))
    written_bands = []
    _band_results = []

    def _write_one_band(band):
        t_band = perf_counter() if profile else None
        log.info(f"  [{band}] materialising...")
        _arr = None
        _attrs = None
        _stats = None
        try:
            if band not in loaded_data:
                return band, False, None, None
            _spec, _attrs, _shp = loaded_data[band]
            if band == "B03" and fast_test:
                return band, False, None, None

            band_nc = datetime_folder / f"{product}_{band}_{timestamp}{area_suffix}.nc"
            if band == "B03" and not fast_test:
                # B03 streamed: spec is a _LazyBandView -- write tiles directly
                _ok, _stats = _write_b03_nc_tiled(
                    band_nc, band, _spec, cal_dict.get(band),
                    global_attrs, complevel=comp_level, profile=profile)
                if _ok:
                    log.info(f"  [{band}] shape={_spec.shape}  ->  {band_nc.name}")
                if profile and t_band is not None:
                    log.info(f"[TIME] stage=band_{band} duration={perf_counter()-t_band:.2f}s")
                return band, _ok, _stats if _ok else None, None

            # Non-B03: materialise the assembled band inside this worker.
            if isinstance(_spec, _LazyBandView):
                _arr, _arr_attrs, _area, _hdr0 = _read_band_assembled(band, _spec._files)
                if _arr is None:
                    return band, False, None, None
                _attrs = _arr_attrs
            else:
                _arr, _arr_attrs, _area, _hdr0 = _read_band_assembled(band, _spec)
                if _arr is None:
                    return band, False, None, None
                _attrs = _arr_attrs
            _shp = _arr.shape

            _ok = _write_band_nc(band_nc, band, _arr, cal_dict.get(band), 0, global_attrs, complevel=comp_level)
            if _ok:
                valid = _arr[np.isfinite(_arr)]
                vmin = float(valid.min()) if valid.size else float("nan")
                vmax = float(valid.max()) if valid.size else float("nan")
                log.info(f"  [{band}] shape={_arr.shape}  min={vmin:.3f}  max={vmax:.3f}  ->  {band_nc.name}")
                _stats = _compute_band_stats(band, _arr, _attrs)
                del _arr  # free the band array inside the worker thread
            if profile and t_band is not None:
                log.info(f"[TIME] stage=band_{band} duration={perf_counter()-t_band:.2f}s")
            return band, _ok, _stats if _ok else None, _attrs if _ok else None
        except Exception as exc:
            log.warning(f"  [{band}] FAILED: {exc}")
            log.warning(traceback.format_exc())
            return band, False, None, None

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            _band_results = list(executor.map(_write_one_band, list(loaded_bands)))
    else:
        for band in list(loaded_bands):
            _band_results.append(_write_one_band(band))

    for band, _ok, _stats, _attrs in _band_results:
        if _ok:
            written_bands.append(band)
            if ads:
                ads.register_band(band, None, _attrs, stats=_stats)
        elif ads:
            ads.register_failed_band(band, "processing_failed")

    if not written_bands:
        log.error(f"[!] Zero bands written for area {area or 'full'} -- skipping.")
        return 0, []

    failed_to_load = sorted(set(available_bands) - set(loaded_bands))
    failed_to_write = sorted(set(loaded_bands) - set(written_bands))
    log.info(f"[+] Wrote {len(written_bands)} bands (area {area or 'full'}) to per-band NC files")
    if failed_to_load:
        log.warning(f"    Bands present in .dat but NOT loaded: {failed_to_load}")
    if failed_to_write:
        log.warning(f"    Loaded but NOT written: {failed_to_write}")

    if ads:
        ads.set_sector(sector)
        ads.add_provenance("bands_written",
                           f"ok={len(written_bands)} failed_load={len(failed_to_load)} "
                           f"failed_write={len(failed_to_write)}")

    # Store shared physical constants in ads
    if ads:
        ads.physical_constants = phys_constants

    # --- HSD COFF/LOFF + scan times + sun position from already-parsed header ---
    if ads and hdr_use:
        cl_compat = {
            "COFF": hdr_use.get("coff", 0),
            "LOFF": hdr_use.get("loff", 0),
            "sub_lon": hdr_use.get("sub_lon", 0),
            "CFAC": hdr_use.get("cfac", 0),
            "LFAC": hdr_use.get("lfac", 0),
            "distance_from_earth_center": hdr_use.get("distance_from_earth_center", 0),
            "earth_equatorial_radius": hdr_use.get("earth_equatorial_radius", 0),
            "earth_polar_radius": hdr_use.get("earth_polar_radius", 0),
        }
        ads.set_coff_loff(cl_compat)
        ads.observation_start_time = hdr_use.get("observation_start_time")
        ads.observation_end_time = hdr_use.get("observation_end_time")
        # Compute solar geometry from observation time (HSD header's sun_position is
        # in an inertial frame and is practically frozen across hours -- useless for SZA)
        solar = _compute_solar_geometry(ads.observation_start_time) if ads.observation_start_time else {}
        if solar:
            ads.solar_geometry = solar
        # Still store raw HSD sun_position for reference (debugging only)
        sp = hdr_use.get("block4", {}).get("sun_position")
        if sp is not None:
            ads.sun_position = [float(v) for v in sp]
        log.info(f"  [HSD] COFF={hdr_use.get('coff', 0):.1f}  LOFF={hdr_use.get('loff', 0):.1f}  "
                 f"sub_lon={hdr_use.get('sub_lon', 0):.4f}  "
                 f"scan={ads.observation_start_time}~{ads.observation_end_time}")

    # --- Reference geotransforms at multiple resolutions ---
    if ads and area_def:
        try:
            gt2, am2 = _compute_resampled_geotransform(area_def, 2000.0)
            if gt2:
                ads.set_geotransform_2km(gt2)
                if am2:
                    sh = am2.get("shape", [0, 0])
                    if len(sh) == 2:
                        ads.set_ref_grid_size(sh[1], sh[0])
                log.info(f"  [CRS] 2km ref geotransform: {gt2}")
        except Exception: pass
        try:
            gt1, am1 = _compute_resampled_geotransform(area_def, 1000.0)
            if gt1:
                ads.set_geotransform_1km(gt1)
                if am1:
                    sh = am1.get("shape", [0, 0])
                    if len(sh) == 2:
                        ads.set_ref_grid_1km(sh[1], sh[0])
                log.info(f"  [CRS] 1km ref geotransform: {gt1}")
        except Exception: pass
        try:
            gt05, am05 = _compute_resampled_geotransform(area_def, 500.0)
            if gt05:
                ads.set_geotransform_0_5km(gt05)
                if am05:
                    sh = am05.get("shape", [0, 0])
                    if len(sh) == 2:
                        ads.set_ref_grid_0_5km(sh[1], sh[0])
                log.info(f"  [CRS] 0.5km ref geotransform: {gt05}")
        except Exception: pass

    # Write ADS sidecar -- one per sub-area, named AHI_<sector>_<timestamp>[_<area>].ads.json
    if ads and written_bands:
        sidecar_name = f"AHI_{sector}_{timestamp}{area_suffix}.ads.json"
        sidecar_path = datetime_folder / sidecar_name

        # First: explicitly merge any existing sidecar at the same path (preserves B03/B13
        # from prior runs when processing partial batches)
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                ads.band_stats.update(existing.get("band_stats", {}))
                ads.qc_flags.update(existing.get("qc_flags", {}))
                ads.provenance.extend(existing.get("provenance", []))
                for key in ["crs", "geotransform", "geotransform_2km", "geotransform_1km", "geotransform_0_5km", "area_meta", "coff_loff", "sector", "observation_start_time", "observation_end_time", "sun_position", "solar_geometry", "physical_constants"]:
                    val = existing.get(key)
                    if val is not None and getattr(ads, key, None) is None:
                        setattr(ads, key, val)
                log.info(f"[ADS] Merged existing sidecar ({len(existing.get('band_stats', {}))} existing bands)")
            except Exception as exc:
                log.warning(f"[ADS] Could not merge existing sidecar: {exc}")

        # Merge-and-delete old-format band-specific sidecars -- only in single-area folders.
        # Per-area folders keep their own sidecar; never delete another sub-area's sidecar.
        if not area:
            for prev_sc in sorted(datetime_folder.glob("*.ads.json")):
                if prev_sc == sidecar_path:
                    continue
                try:
                    with open(prev_sc, "r", encoding="utf-8") as f:
                        prev = json.load(f)
                    ads.band_stats.update(prev.get("band_stats", {}))
                    ads.qc_flags.update(prev.get("qc_flags", {}))
                    ads.provenance.extend(prev.get("provenance", []))
                    log.info(f"[ADS] Merged legacy sidecar {prev_sc.name}")
                    prev_sc.unlink()
                except Exception:
                    pass

        ref_nc = datetime_folder / f"{product}_{written_bands[0]}_{timestamp}{area_suffix}.nc"
        ads.nc_path = str(ref_nc)
        ads.nc_size_mb = round(ref_nc.stat().st_size / 1e6, 2) if ref_nc.exists() else 0.0
        ads.write_sidecar(ref_nc, sidecar_path=sidecar_path)
        ads._derecho_ready_hook(sidecar_path, ads.to_dict())

    # Merge NDMW wind data if present
    if written_bands:
        ref_nc = datetime_folder / f"{product}_{written_bands[0]}_{timestamp}{area_suffix}.nc"
        _merge_wind_data_if_present(ref_nc, datetime_folder, ads_enabled)

    # Delete source files (.dat AND .bz2) -- ONLY for bands that now have a
    # completed NC file. NEVER delete source data for failed/unwritten bands:
    # a re-run needs the source to recover without re-downloading the whole
    # slot (this is exactly how B03's source data was lost before).
    if not keep_dat:
        removed = 0
        kept = 0
        kept_bands = []
        _area_upper = (area or "").upper()
        for f in input_files:
            if _area_upper and _area_upper not in f.name.upper():
                continue
            _band = None
            for _i in range(1, 17):
                if f"_B{_i:02d}_" in f.name:
                    _band = f"B{_i:02d}"
                    break
            _has_nc = False
            if _band is not None:
                _has_nc = (datetime_folder / f"{product}_{_band}_{timestamp}{area_suffix}.nc").is_file()
            if _has_nc:
                try:
                    if f.exists():
                        f.unlink()
                        removed += 1
                except Exception:
                    kept += 1
            else:
                kept += 1
                if _band is not None and _band not in kept_bands:
                    kept_bands.append(_band)
        log.info(f"[~] Removed {removed}/{len(input_files)} source .dat/.bz2 files (kept {kept} -- no completed NC)")
        if kept_bands:
            log.warning(f"[!] Kept source .dat/.bz2 for bands WITHOUT a completed NC: {sorted(kept_bands)} -- re-run to retry them")
            if ads:
                ads.add_provenance("cleanup",
                                   f"{removed} source files removed, {kept} kept ({sorted(kept_bands) or 'none'})")

    return len(written_bands), sorted(set(failed_to_load) | set(failed_to_write))


def process_to_nc(
    datetime_folder: Path,
    keep_dat: bool = False,
    ads_enabled: bool = True,
    profile: bool = False,
    fast_test: bool = False,
    workers: int = 1,
    small: bool = False,
    fast: bool = False,
) -> Tuple[bool, Optional[Path], int, int]:
    """Convert a datetime folder to per-band NC files.

    Returns (ok, datetime_folder, bands_ok, bands_failed):
        ok           - True if all present bands were converted
        datetime_folder - folder processed (None on abort)
        bands_ok     - number of bands written successfully
        bands_failed - number of bands that failed to load or write
    """
    log.info(f"[+] {datetime_folder.name}")
    t_total = perf_counter() if profile else None

    if not _check_disk_space(datetime_folder, required_gb=4.0):
        log.error("Aborting due to insufficient disk space. Free up space and try again.")
        return False, None, 0, 0

    dat_files, bz2_files = [], []
    for p in datetime_folder.rglob("*"):
        if p.is_file():
            _sfx = p.suffix.lower()
            if _sfx == ".dat":
                dat_files.append(p.resolve())
            elif _sfx == ".bz2":
                bz2_files.append(p.resolve())
    dat_files = list(dict.fromkeys(dat_files))
    bz2_files = list(dict.fromkeys(bz2_files))
    if not dat_files and not bz2_files:
        log.warning(f"[!] No .dat or .bz2 files in {datetime_folder}")
        return False, None, 0, 0
    # If we have .bz2 but no .dat, prefer .bz2 (direct read)
    if bz2_files and not dat_files and not keep_dat:
        log.info(f"    Using direct .bz2 → NC path ({len(bz2_files)} .bz2 files)")
    elif bz2_files and dat_files:
        # Both exist — prefer .dat (already extracted)
        pass

    # Combine dat and bz2 for discovery; use dat first, bz2 as fallback
    all_input_files = dat_files + bz2_files

    timestamp = extract_timestamp(datetime_folder)

    # ---- Determine which bands the .dat files contain ----
    available_bands = set()
    for f in dat_files:
        for i in range(1, 17):
            if f"_B{i:02d}_" in f.name:
                available_bands.add(f"B{i:02d}")
                break
    log.info(f"  Bands available from .dat files: {sorted(available_bands)}")

    log.info(f"    {len(dat_files)} .dat / {len(bz2_files)} .bz2 files")

    scn = None
    ds_out = None
    area_def = None
    _temp_files = []  # track temp .dat files from .bz2 decompression
    _temp_dir = None  # temp dir for .bz2 decompression

    try:
        folder_name = datetime_folder.name
        sector = _detect_sector(datetime_folder)

        # Additional filename-based detection (very useful for Target/Japan)
        # Target files often contain "R3" (e.g. R301, R302), Japan files contain "JP"
        if any("R3" in f.name or "Target" in f.name for f in dat_files):
            sector = "Target"
        elif any("JP" in f.name for f in dat_files):
            sector = "Japan"

        log.info(f"  Detected sector: {sector} (from path + filenames)")

        # Always log a few real filenames — invaluable for diagnosing "hsd_bXX not found" on Japan/Target downloads
        sample_names = [Path(f).name for f in (dat_files or bz2_files)[:5]]
        log.info(f"  Sample filenames: {sample_names} (total {len(dat_files)} .dat / {len(bz2_files)} .bz2)")

        # Group input files (both .dat and .bz2) by band token. When both exist
        # for the same band, prefer the already-extracted .dat (drop the .bz2 copy).
        band_files: Dict[str, List[str]] = {}
        for f in dat_files + bz2_files:
            name = Path(f).name
            for i in range(1, 17):
                b = f"B{i:02d}"
                if f"_{b}_" in name:
                    band_files.setdefault(b, []).append(str(f))
                    break
        for b in list(band_files):
            group = band_files[b]
            if any(str(x).lower().endswith(".dat") for x in group):
                band_files[b] = [x for x in group if str(x).lower().endswith(".dat")]
        available_bands = set(band_files)
        log.info(f"  Bands available: {sorted(available_bands)}")

        # ── TIMING: fast reader load ─────────────────────────────────────
        t0 = perf_counter() if profile else None

        loaded_bands = []
        # band -> (spec, attrs, shape); spec is a _LazyBandView (B03, streamed)
        # or the list of input Paths (materialised inside the worker thread).
        loaded_data = {}
        band_meta = {}   # band -> (attrs, area_def, hdr0) built from headers only
        scn = None       # kept None — satpy Scene is no longer used
        micro_groups = None
        areas = [None]

        if sector == "FLDK":
            # ========== FLDK PATH — custom numpy reader (direct .dat/.bz2) ==========
            log.info("  Using custom fast HSD reader (pure numpy, direct .dat/.bz2)")
            for b in sorted(band_files):
                try:
                    files = [Path(x) for x in band_files[b]]
                    hdr0 = _get_hdr(files[0])
                    if hdr0 is None:
                        continue
                    attrs = _band_attrs(hdr0)
                    area = _build_area_def(hdr0)
                    if area is not None:
                        attrs["area"] = area
                    if b == "B03":
                        spec = _LazyBandView(b, files)
                    else:
                        spec = files
                    band_meta[b] = (attrs, area, hdr0)
                    loaded_data[b] = (spec, attrs, None)
                    loaded_bands.append(b)
                    if area_def is None and area is not None:
                        area_def = area
                    log.info(f"    ✓ Ready {b} shape={(hdr0['data_shape'][0]*int(hdr0.get('total_segments',1)), hdr0['data_shape'][1])}")
                except Exception as e:
                    log.warning(f"    ⚠ Could not prepare {b}: {e}")
        else:
            # ========== MICRO-GROUP LOADER FOR TARGET + JAPAN (PER SUB-AREA) ==========
            # Japan/Target data frequently contains multiple sub-area tiles (JP01-JP04 / R301-R304)
            # in the same folder. We group files by (band, sub-area) and produce one per-band
            # NC set + one sidecar per sub-area, instead of picking a single best sub-area.
            log.info(f"  Using micro-group loader (band + sub-area) for {sector}")

            micro_groups = {}
            for b, group in band_files.items():
                for f in group:
                    area_token = _extract_area_token(Path(f).name) or "UNK"
                    key = (b, area_token)
                    micro_groups.setdefault(key, []).append(f)

            if not micro_groups:
                log.warning("  No Bxx tokens found -- grouping everything under UNK")
                micro_groups[("ALL", "UNK")] = list(dat_files + bz2_files)

            log.info(f"  Micro-groups: { {f'{k[0]}/{k[1]}': len(v) for k, v in micro_groups.items()} }")

            _tokens = sorted({a for (_b, a) in micro_groups}, key=str)
            if len(_tokens) == 1 and _tokens[0] == "UNK":
                areas = [None]
            else:
                areas = _tokens
            log.info(f"  Sub-areas to convert: {[a or 'full' for a in areas]}")

        if profile and t0 is not None:
            log.info(f"[TIME] stage=custom_load duration={perf_counter()-t0:.2f}s")
            if _HAS_PSUTIL:
                mem = psutil.Process().memory_info().rss / 1e6
                log.info(f"[MEM] after custom load: {mem:.1f} MB RSS")

        # ═════════════════════════════════════════════════════════════════
        #  EXTRACT CALIBRATION DATA (from HSD headers — needs all bands)
        # ═════════════════════════════════════════════════════════════════
        cal_dict = {}
        hsd_header = None
        cal_src_files = dat_files if dat_files else bz2_files

        def _parse_cal_src(src):
            try:
                h = _get_hdr(src)
                if h and h.get("band_number", 0) > 0:
                    return h, _extract_calibration_data(h)
            except Exception:
                pass
            return None, None

        _cal_workers = workers if workers > 1 else 1
        if _cal_workers > 1:
            with ThreadPoolExecutor(max_workers=_cal_workers) as _ex:
                _cal_results = list(_ex.map(_parse_cal_src, cal_src_files))
        else:
            _cal_results = [_parse_cal_src(src) for src in cal_src_files]
        for h, row in _cal_results:
            if h is not None:
                if hsd_header is None:
                    hsd_header = h
                if row and row["band"] not in cal_dict:
                    cal_dict[row["band"]] = row
            if len(cal_dict) >= 16:
                break
        if cal_dict:
            log.info(f"  [CAL] Extracted calibration for {len(cal_dict)} bands")

        agg_written = 0
        agg_failed = set()

        for area in areas:
            _area_label = area or "full"
            log.info(f"  -- Converting sub-area: {_area_label} --")
            _avail_bands = available_bands

            if sector != "FLDK":
                # Prepare this sub-area's bands (header-only, no data read yet)
                loaded_data = {}
                band_meta = {}
                loaded_bands = []
                area_def = None
                _area_key = area if area is not None else "UNK"
                _avail_bands = {b for (_b, a) in micro_groups if a == _area_key}
                for (b, a), files_group in micro_groups.items():
                    if a != _area_key:
                        continue
                    try:
                        files = [Path(x) for x in files_group]
                        hdr0 = _get_hdr(files[0])
                        if hdr0 is None:
                            continue
                        attrs = _band_attrs(hdr0)
                        seg_area = _build_area_def(hdr0)
                        if seg_area is not None:
                            attrs["area"] = seg_area
                        spec = _LazyBandView(b, files) if b == "B03" else files
                        band_meta[b] = (attrs, seg_area, hdr0)
                        loaded_data[b] = (spec, attrs, None)
                        loaded_bands.append(b)
                        if area_def is None and seg_area is not None:
                            area_def = seg_area
                        log.info(f"    ✓ Ready {b}/{_area_key}")
                    except Exception as band_err:
                        log.info(f"    ⚠ Could not prepare {b}/{_area_key}: {band_err}")
                if not loaded_bands:
                    log.warning(f"[!] No bands loaded for sub-area {_area_key} -- skipping")
                    continue

            _w, _fail = _convert_area(
                datetime_folder=datetime_folder,
                folder_name=folder_name,
                sector=sector,
                area=area,
                timestamp=timestamp,
                available_bands=_avail_bands,
                cal_dict=cal_dict,
                hsd_header=hsd_header,
                loaded_data=loaded_data,
                band_meta=band_meta,
                loaded_bands=loaded_bands,
                area_def=area_def,
                input_files=all_input_files,
                keep_dat=keep_dat,
                ads_enabled=ads_enabled,
                profile=profile,
                fast_test=fast_test,
                workers=workers,
                small=small,
                fast=fast,
            )
            agg_written += _w
            agg_failed.update(_fail)

        if agg_written == 0:
            log.error("[!] Zero bands written -- aborting.")
            return False, datetime_folder, 0, 0

        if profile and t_total is not None:
            log.info(f"[TIME] stage=TOTAL duration={perf_counter()-t_total:.2f}s")

        failed_bands = sorted(agg_failed)
        print(f"[RESULT] folder={datetime_folder.name} ok={agg_written} "
              f"fail={len(failed_bands)} failed_bands={','.join(failed_bands) or 'none'}")
        if failed_bands:
            log.warning(f"[!] {len(failed_bands)} band(s) failed ({failed_bands}) -- folder FAILED")
            return False, datetime_folder, agg_written, len(failed_bands)
        return True, datetime_folder, agg_written, len(failed_bands)
    except Exception as exc:
        log.error(f"[!] Failed: {exc}")
        traceback.print_exc()
        return False, None, 0, 0
    finally:
        if scn is not None:
            del scn
        # Clean up temp files from .bz2 decompression
        if _temp_files:
            for tf in _temp_files:
                try:
                    if tf.exists(): tf.unlink()
                except Exception: pass
        if _temp_dir:
            try:
                import shutil
                shutil.rmtree(_temp_dir, ignore_errors=True)
            except Exception: pass
        gc.collect()


# ══════════════════════════════════════════════════════════════════════════════
#  PER-BAND NC WRITER  (packed int16 + scale_factor/add_offset)
# ══════════════════════════════════════════════════════════════════════════════

def _pack_to_int16(data: np.ndarray, offset: float, inv_scale: float) -> np.ndarray:
    """Pack a float array to int16 with scale/add_offset (multiply-based, single mask)."""
    packed = np.empty(data.shape, dtype=np.int16)
    finite = np.isfinite(data)
    if finite.any():
        vals = (data[finite] - offset) * inv_scale
        packed[finite] = np.round(vals).astype(np.int16)
    packed[~finite] = -32768
    return packed


def _write_band_metadata(_ds, band: str, cal_data: dict, qc_val: int,
                         global_attrs: dict) -> None:
    """Write the QC flag, calibration scalars and global attrs shared by all
    per-band NC writers (full-band path and B03 tiled path)."""
    _qc = _ds.createVariable("qc_flags", "i1", ())
    _qc.long_name = "Band quality flag"
    _qc.flag_values = "0, 1, 2"
    _qc.flag_meanings = "pass questionable failed"
    _qc[:] = np.int8(qc_val)
    if cal_data:
        for _field in ["radiance_slope", "radiance_intercept",
                       "reflectance_albedo_coeff", "c0_rad2tb", "c1_rad2tb", "c2_rad2tb"]:
            _v = _ds.createVariable(f"cal_{_field}", "f4", ())
            _v[:] = np.float32(cal_data[_field])
        _v = _ds.createVariable("cal_calibration_units", str, ())
        _v[...] = cal_data.get("calibration_units", "")
        _v = _ds.createVariable("cal_resolution_m", "i4", ())
        _v[:] = np.int32(cal_data.get("resolution_m", 2000))
    if global_attrs:
        for _k, _v in global_attrs.items():
            try:
                setattr(_ds, _k, _v)
            except Exception:
                pass


def _fetch_block(da, r0: int, r1: int) -> np.ndarray:
    """Return da[r0:r1] as a float32 numpy array.

    Accepts a dask/xarray DataArray (calls .compute()), a _LazyBandView
    (returns a calibrated numpy slice directly) or a plain numpy array.
    """
    chunk = da[r0:r1]
    if hasattr(chunk, "compute"):
        chunk = chunk.compute()
    tile = chunk.values if hasattr(chunk, "values") else np.asarray(chunk)
    if tile.dtype != np.float32:
        tile = tile.astype(np.float32)
    return tile


def _write_b03_nc_tiled(output_path: Path, band: str, da,
                        cal_data: dict = None, global_attrs: dict = None,
                        complevel: int = 6, profile: bool = False) -> Tuple[bool, Optional[dict]]:
    """Write B03 (500m full-disk) directly into the NC variable in row-tiles,
    bypassing the old ~1.9 GB float32 temp memmap.

    Pass A: sequential tile-by-tile NaN-safe global min/max for the int16 scale
            (reads ~1-2 segments at a time — keeps disk I/O low, never spikes).
    Pass B: each tile is computed once, packed, written, and streamed into
    exact min/max/mean/std/coverage accumulators + a 65536-bin histogram for
    p5/p95 (rounding-level difference vs np.nanpercentile, same sidecar schema).
    """
    import netCDF4 as _nc4
    t0 = perf_counter() if profile else None
    nrows, ncols = da.shape
    if nrows == 0 or ncols == 0:
        log.warning(f"  [{band}] Empty array — skipping")
        return False, None
    chunk_rows = _B03_TILE_ROWS

    # ── Pass A: tile-by-tile NaN-safe min/max (sequential, low I/O) ──
    dmin = np.inf
    dmax = -np.inf
    try:
        for r0 in range(0, nrows, chunk_rows):
            r1 = min(r0 + chunk_rows, nrows)
            tile = _fetch_block(da, r0, r1)
            if tile.dtype != np.float32:
                tile = tile.astype(np.float32)
            valid = tile[np.isfinite(tile)]
            if valid.size:
                dmin = min(dmin, float(np.nanmin(valid)))
                dmax = max(dmax, float(np.nanmax(valid)))
            del tile, valid
        if not (np.isfinite(dmin) and np.isfinite(dmax)):
            log.warning(f"  [{band}] No valid data — skipping")
            return False, None
        scale = (dmax - dmin) / 65534.0
        if scale == 0:
            scale = 1.0
        offset = dmin + 32767.0 * scale
        inv_scale = 1.0 / scale
    except Exception as exc:
        log.warning(f"  [{band}] Stats pass failed: {exc}")
        return False, None

    n_total = nrows * ncols
    n_valid = 0
    vmin = np.inf
    vmax = -np.inf
    cnt = 0.0
    mean = 0.0
    m2 = 0.0
    hist = np.zeros(_B03_HIST_BINS, dtype=np.int64)
    try:
        with _NC_WRITE_LOCK:
            with _nc4.Dataset(str(output_path), "w", format="NETCDF4") as _ds:
                _ds.createDimension("y", nrows)
                _ds.createDimension("x", ncols)
                _var = _ds.createVariable(band, "i2", ("y", "x"),
                                          zlib=True, complevel=complevel, shuffle=True,
                                          fill_value=-32768)
                _var.central_wavelength_um = (
                    float(cal_data["central_wavelength_um"]) if cal_data else 0.0
                )
                if "units" in _var.ncattrs():
                    _var.units = cal_data.get("calibration_units", "")
                _write_band_metadata(_ds, band, cal_data, 0, global_attrs)

                for seg_idx, r0 in enumerate(range(0, nrows, chunk_rows)):
                    r1 = min(r0 + chunk_rows, nrows)
                    tile = _fetch_block(da, r0, r1)
                    packed = _pack_to_int16(tile, offset, inv_scale)
                    _var[r0:r1, :] = packed
                    del packed

                    finite = np.isfinite(tile)
                    nv = int(finite.sum())
                    if nv:
                        vals = tile[finite]
                        n_valid += nv
                        vmin = min(vmin, float(vals.min()))
                        vmax = max(vmax, float(vals.max()))
                        cnt_b = float(nv)
                        mean_b = float(vals.mean(dtype=np.float64))
                        delta = mean_b - mean
                        if cnt == 0.0:
                            mean = mean_b
                            m2 = float(((vals - mean_b) ** 2).sum())
                        else:
                            mean = mean + delta * cnt_b / (cnt + cnt_b)
                            m2 = (m2 + float(((vals - mean_b) ** 2).sum())
                                  + delta * delta * cnt * cnt_b / (cnt + cnt_b))
                        cnt += cnt_b
                        _h, _ = np.histogram(vals, bins=_B03_HIST_BINS, range=(dmin, dmax))
                        hist += _h
                        del vals
                    del tile, finite
                    log.info(f"    [B03] tile {seg_idx+1:03d} rows {r0}–{r1-1} OK")
                # CRITICAL: set scale_factor/add_offset AFTER writing packed values.
                # netCDF4 auto-scales on WRITE when these attrs exist, which would
                # corrupt every already-packed int16 value (B03 came out as noise).
                _var.scale_factor = np.float32(scale)
                _var.add_offset = np.float32(offset)
        log.info(f"  [{band}] packed int16  scale={scale:.6e}  offset={offset:.4f}  ({output_path.name})")
    except Exception as _exc:
        log.warning(f"  [{band}] Failed to write NC: {_exc}")
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return False, None

    if n_valid == 0:
        return False, None
    coverage = n_valid / n_total
    std = math.sqrt(m2 / cnt) if cnt > 0 else float("nan")
    bin_width = (dmax - dmin) / _B03_HIST_BINS
    cdf = np.cumsum(hist)
    total = float(cdf[-1]) if cdf.size else 0.0

    def _quantile(q: float) -> float:
        if total <= 0.0:
            return float("nan")
        target = q * total
        idx = int(np.searchsorted(cdf, target))
        idx = min(max(idx, 0), _B03_HIST_BINS - 1)
        prev_c = float(cdf[idx - 1]) if idx > 0 else 0.0
        cur_c = float(cdf[idx])
        lo = dmin + idx * bin_width
        frac = 0.5 if cur_c <= prev_c else (target - prev_c) / (cur_c - prev_c)
        return lo + frac * bin_width

    stats = {
        "qc": "OK" if coverage >= 0.80 else "PARTIAL",
        "shape": [nrows, ncols],
        "coverage_pct": round(coverage * 100, 2),
        "min": float(vmin),
        "max": float(vmax),
        "mean": float(mean),
        "std": float(std),
        "p5": _quantile(0.05),
        "p95": _quantile(0.95),
    }
    attrs = _sanitize_attrs(dict(da.attrs))
    stats["units"] = attrs.get("units", "unknown")
    stats["calibration"] = attrs.get("calibration", "unknown")
    stats["wavelength"] = attrs.get("wavelength", "unknown")
    if profile and t0 is not None:
        log.info(f"[TIME] stage=b03_tiled duration={perf_counter()-t0:.2f}s")
    return True, stats


def _write_band_nc(output_path: Path, band: str, data: np.ndarray,
                   cal_data: dict = None, qc_val: int = 0,
                   global_attrs: dict = None,
                   complevel: int = 6) -> bool:
    """Write one band as a compact NC file with packed int16.
    CRS and shared metadata go in the ads.json sidecar only.
    """
    import netCDF4 as _nc4
    try:
        finite = np.isfinite(data)
        valid = data[finite]
        if valid.size == 0:
            log.warning(f"  [{band}] No valid data — skipping")
            return False
        dmin = float(valid.min())
        dmax = float(valid.max())
        scale = (dmax - dmin) / 65534.0
        if scale == 0:
            scale = 1.0
        offset = dmin + 32767.0 * scale
        inv_scale = 1.0 / scale
        packed = np.empty(data.shape, dtype=np.int16)
        if finite.any():
            vals = (valid - offset) * inv_scale
            packed[finite] = np.round(vals).astype(np.int16)
        packed[~finite] = -32768
        log.info(f"  [{band}] packed int16  range=[{packed.min()},{packed.max()}]  "
                 f"scale={scale:.6e}  offset={offset:.4f}  ({output_path.name})")
        with _NC_WRITE_LOCK:
            with _nc4.Dataset(str(output_path), "w", format="NETCDF4") as _ds:
                _ds.createDimension("y", data.shape[0])
                _ds.createDimension("x", data.shape[1])
                _var = _ds.createVariable(band, "i2", ("y", "x"),
                                          zlib=True, complevel=complevel, shuffle=True,
                                          fill_value=-32768)
                _var[:] = packed
                _var.scale_factor = np.float32(scale)
                _var.add_offset = np.float32(offset)
                _var.central_wavelength_um = (
                    float(cal_data["central_wavelength_um"]) if cal_data else 0.0
                )
                if "units" in _var.ncattrs():
                    _var.units = cal_data.get("calibration_units", "")
                _write_band_metadata(_ds, band, cal_data, qc_val, global_attrs)
        return True
    except Exception as _exc:
        log.warning(f"  [{band}] Failed to write NC: {_exc}")
        if output_path.exists():
            try:
                output_path.unlink()
            except Exception:
                pass
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════
def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Cyclone V3.5.0 – Himawari .dat/.bz2 → NetCDF (parallel + compression)"
    )
    parser.add_argument("-i", "--input", required=True, help="Directory containing AHI-L1b-FLDK_* / AHI-L1b-Japan_* / AHI-L1b-Target_* folders (or any tree with .dat or .bz2 files)")
    parser.add_argument("--keep", action="store_true", help="Keep source .dat/.bz2 files after conversion")
    parser.add_argument("--bz2", action="store_true", help="Read .bz2 files directly (skip extraction, decompress to temp)")
    parser.add_argument("--no-ads", action="store_true", help="Skip ADS sidecar generation")
    parser.add_argument("--profile", action="store_true", help="Enable perf_counter timing instrumentation")
    parser.add_argument("--fast-test", action="store_true", help="Quick benchmark: complevel=1 + skip B03 memmap")
    parser.add_argument("--fast", action="store_true", help="Faster compression: complevel=2 (bigger NC files, faster writes)")
    parser.add_argument("--workers", type=int, default=1, help="Parallel band workers (default: 1 = sequential)")
    parser.add_argument("--small", action="store_true", help="Max compression mode: complevel=9 for smallest NC files")
    parser.add_argument("--merge-wind", action="store_true", help="Merge NDMW wind data into existing NC files (no .dat processing)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    if not input_dir.exists():
        log.error(f"[!] Directory not found: {input_dir}")
        sys.exit(1)

    print("=" * 70)
    print("CYCLONE V3.5.0 - HIMAWARI AHI .DAT/.bz2 -> NetCDF (optimized speed & size)")
    print("=" * 70)

    if args.merge_wind:
        # Standalone wind merge mode: find all folders with NDMW*.nc + *_B*.nc
        print("[Wind Merge] Scanning for folders with NDMW wind data + band NC files...")
        merged = 0
        for nc_file in input_dir.rglob("*_B??_*.nc"):
            folder = nc_file.parent
            wind_files = list(folder.glob("NDMW*.nc"))
            if wind_files:
                print(f"  {folder.name}: {len(wind_files)} wind files -> {nc_file.name}")
                _merge_wind_data_if_present(nc_file, folder, True)
                merged += 1
        print(f"\n[Wind Merge] Done. Processed {merged} folders.")
        return

    if args.profile:
        print("[!] Profiling enabled — timing logs prefixed with [TIME]")
    if args.fast_test:
        print("[!] Fast-test mode — B03 memmap skipped")
    if args.workers > 1:
        print(f"[!] Parallel band processing: {args.workers} workers")
    if args.small:
        print("[!] Small mode — maximum compression (complevel=9)")
    if args.fast:
        print("[!] Fast mode — faster compression (complevel=2, bigger NC files)")
    print("=" * 70)

    folders = find_datetime_folders(input_dir)
    if not folders:
        log.error("[!] No supported AHI-L1b-(FLDK|Japan|Target)_* folders found (and no fallback .dat files)")
        sys.exit(1)

    success = 0
    failed = 0
    bands_ok = 0
    bands_failed = 0
    for folder in folders:
        ok, _, bok, bfail = process_to_nc(
            folder,
            keep_dat=args.keep,
            ads_enabled=not args.no_ads,
            profile=args.profile,
            fast_test=args.fast_test,
            workers=args.workers,
            small=args.small,
            fast=args.fast,
        )
        if ok:
            success += 1
        else:
            failed += 1
        bands_ok += bok
        bands_failed += bfail

    print("\n" + "=" * 70)
    print(f"SUMMARY: {success}/{len(folders)} folders converted  "
          f"({bands_ok} bands ok, {bands_failed} bands failed)")
    print("=" * 70)
    print(f"Successfully processed: {success}")
    print(f"Failed folders: {failed}")
    print("STATISTICS_OUTPUT:")
    print(f"Processed: {bands_ok}")
    print(f"Failed: {bands_failed}")


if __name__ == "__main__":
    main()