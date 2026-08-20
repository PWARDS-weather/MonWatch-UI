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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from time import perf_counter
from collections import Counter
import struct
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import xarray as xr

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

# ══════════════════════════════════════════════════════════════════════════════
#  ADVANCED DATA SYSTEM  (with CRS support in sidecar)
# ══════════════════════════════════════════════════════════════════════════════
class AdvancedDataSystem:
    VERSION = "1.3.0"

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
        self.ref_grid_w: Optional[int] = None
        self.ref_grid_h: Optional[int] = None
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

    def set_ref_grid_size(self, w: int, h: int) -> None:
        self.ref_grid_w = w
        self.ref_grid_h = h

    def set_area_meta(self, meta: dict) -> None:
        self.area_meta = meta

    def set_coff_loff(self, cl: Dict[str, float]) -> None:
        self.coff_loff = cl

    def set_sector(self, sector: str) -> None:
        self.sector = sector

    def register_band(self, band: str, data: np.ndarray, attrs: dict = None):
        valid = data[np.isfinite(data)]
        if valid.size == 0:
            self.qc_flags[band] = 2
            self.band_stats[band] = {"qc": "FAILED", "reason": "all-NaN"}
            self._derecho_band_hook(band, None)
            return
        coverage = valid.size / data.size
        stats = {
            "qc": "OK" if coverage >= 0.80 else "PARTIAL",
            "shape": list(data.shape),
            "coverage_pct": round(coverage * 100, 2),
            "min": float(np.nanmin(valid)),
            "max": float(np.nanmax(valid)),
            "mean": float(np.nanmean(valid)),
            "std": float(np.nanstd(valid)),
            "p5": float(np.nanpercentile(valid, 5)),
            "p95": float(np.nanpercentile(valid, 95)),
        }
        if attrs:
            stats["units"] = attrs.get("units", "unknown")
            stats["calibration"] = attrs.get("calibration", "unknown")
            stats["wavelength"] = attrs.get("wavelength", "unknown")
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
        if self.ref_grid_w is not None and self.ref_grid_h is not None:
            d["ref_grid_w"] = self.ref_grid_w
            d["ref_grid_h"] = self.ref_grid_h
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
                for key in ["crs", "geotransform", "geotransform_2km", "area_meta", "coff_loff", "sector"]:
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
            # Decompress .bz2 to a temp file for memmap access
            with bz2.open(filepath, "rb") as f:
                raw = f.read()
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

            # Calibration extension (VIS or IR specific)
            f.seek(b1_len + b2_len + b3_len + b4_len)
            if band_num in _HSD_VIS_BANDS:
                cal_ext = np.fromfile(f, dtype=_HSD_VISCAL, count=1)
                cal_type = "VIS"
                # b5_len already includes the VIS extension size
                data_offset = b1_len + b2_len + b3_len + b4_len + b5_len
            else:
                # Skip b5, read IR extension after it
                f.seek(b1_len + b2_len + b3_len + b4_len + b5_len)
                cal_ext = np.fromfile(f, dtype=_HSD_IRCAL, count=1)
                cal_type = "IR"
                # b5_len does NOT include IR extension (different from VIS)
                data_offset = b1_len + b2_len + b3_len + b4_len + b5_len + int(_HSD_IRCAL.itemsize)

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


def _read_hsd_band_data(filepath: Path, hdr: dict) -> Optional[np.ndarray]:
    """Read Block 12 pixel data from a .DAT or .bz2 file.

    Uses hdr['data_offset'] and hdr['data_shape'] to locate the raw <u2 counts.
    Returns float32 array, or None on failure.
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
            data = buf.view(dtype="<u2").reshape(nlines, ncols).astype(np.float32)
        else:
            # Use memmap for large .dat files (B03 FLDK)
            if nlines * ncols > 50_000_000:  # >50M pixels -> use memmap
                mm = np.memmap(str(filepath), dtype="<u2", mode="r",
                               offset=offset, shape=(nlines, ncols))
                data = mm.astype(np.float32)
                del mm
            else:
                with open(str(filepath), "rb") as f:
                    f.seek(offset)
                    data = np.fromfile(f, dtype="<u2",
                                       count=nlines * ncols).reshape(nlines, ncols).astype(np.float32)
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


def _compute_2km_geotransform(area):
    """Compute a 2km-resolution geotransform from any area definition.

    For non-FLDK sectors, the extent covers the sub-image area.
    This resamples the area extent to a 2km grid so overlay rendering
    always has a consistent reference resolution.
    """
    try:
        x_ll, y_ll, x_ur, y_ur = area.area_extent
        res_2km = 2000.0
        ncols_2km = max(1, int(round((x_ur - x_ll) / res_2km)))
        nrows_2km = max(1, int(round((y_ur - y_ll) / res_2km)))
        x_origin = x_ll
        y_origin = y_ur
        gt_2km = [res_2km, 0.0, float(x_origin), 0.0, -res_2km, float(y_origin)]
        return gt_2km, {
            "extent": [float(x_ll), float(y_ll), float(x_ur), float(y_ur)],
            "shape": [int(nrows_2km), int(ncols_2km)],
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
    m = re.search(r'(\d{8})_(\d{4,6})', folder.name)
    return f"{m.group(1)}_{m.group(2)}" if m else folder.name


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


_B03_FULL_SHAPE = (22000, 22000)
_B03_TILE_ROWS = 4400
_B03_NSEGMENTS = 5
_B03_REQUIRED_FREE_GB = 2.5


def _extract_b03_chunked(da, tmp_dir: Path, profile: bool = False) -> Tuple[np.ndarray, dict]:
    t0 = perf_counter() if profile else None
    mmap_path = tmp_dir / "b03_tmp.mmap"
    free_bytes = shutil.disk_usage(tmp_dir).free
    needed_bytes = int(_B03_REQUIRED_FREE_GB * 1024**3)
    if free_bytes < needed_bytes:
        raise OSError(
            f"Not enough free disk space: {free_bytes // 1024**3:.1f} GB free, "
            f"need at least {_B03_REQUIRED_FREE_GB} GB for B03 temporary file"
        )
    mmap_arr = np.memmap(mmap_path, dtype=np.float32, mode="w+", shape=_B03_FULL_SHAPE)
    mmap_arr[:] = np.nan
    attrs = _sanitize_attrs(dict(da.attrs))
    total_rows = da.shape[0]
    chunk_rows = _B03_TILE_ROWS
    loaded_segs = 0
    for seg_idx in range(_B03_NSEGMENTS):
        row_start = seg_idx * chunk_rows
        row_end = min(row_start + chunk_rows, total_rows)
        try:
            chunk = da[row_start:row_end].compute()
            tile = chunk.values if hasattr(chunk, "values") else np.asarray(chunk)
            if tile.dtype != np.float32:
                tile = tile.astype(np.float32)
            mmap_arr[row_start:row_end, :] = tile
            del chunk, tile
            loaded_segs += 1
            log.info(f"    [B03] chunk {seg_idx+1:02d}/{_B03_NSEGMENTS} rows {row_start}–{row_end-1} OK")
        except Exception as exc:
            log.warning(f"    [B03] chunk {seg_idx+1} FAILED (rows {row_start}-{row_end-1}): {exc}")
            if loaded_segs == 0:
                mmap_arr._mmap.close()
                mmap_path.unlink(missing_ok=True)
                raise RuntimeError("B03: all chunks failed to load")
    mmap_arr.flush()
    mmap_arr._mmap.close()
    del mmap_arr
    if profile and t0 is not None:
        dur = perf_counter() - t0
        log.info(f"[TIME] stage=b03_chunked duration={dur:.2f}s")
        if _HAS_PSUTIL:
            mem = psutil.Process().memory_info().rss / 1e6
            log.info(f"[MEM] after B03 chunked: {mem:.1f} MB RSS")
    return np.memmap(mmap_path, dtype=np.float32, mode="r", shape=_B03_FULL_SHAPE), attrs


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




def process_to_nc(
    datetime_folder: Path,
    keep_dat: bool = False,
    ads_enabled: bool = True,
    profile: bool = False,
    fast_test: bool = False,
    workers: int = 1,
    small: bool = False,
) -> Tuple[bool, Optional[Path]]:
    log.info(f"[+] {datetime_folder.name}")
    t_total = perf_counter() if profile else None

    if not _check_disk_space(datetime_folder, required_gb=4.0):
        log.error("Aborting due to insufficient disk space. Free up space and try again.")
        return False, None

    dat_files = list({p.resolve() for p in datetime_folder.rglob("*.dat")} | {p.resolve() for p in datetime_folder.rglob("*.DAT")})
    bz2_files = list({p.resolve() for p in datetime_folder.rglob("*.bz2")} | {p.resolve() for p in datetime_folder.rglob("*.BZ2")})
    if not dat_files and not bz2_files:
        log.warning(f"[!] No .dat or .bz2 files in {datetime_folder}")
        return False, None
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
    ads = AdvancedDataSystem(timestamp) if ads_enabled else None
    if ads:
        ads.add_provenance("start", f"folder={datetime_folder.name} dat_count={len(dat_files)} bz2_count={len(bz2_files)}")

    from satpy import Scene
    scn = None
    ds_out = None
    mmap_path = None
    area_def = None
    _temp_files = []  # track temp .dat files from .bz2 decompression
    _temp_dir = None  # temp dir for .bz2 decompression

    try:
        # If only .bz2 available, decompress to temp .dat files for satpy
        if not dat_files and bz2_files:
            log.info(f"    No .dat files found — decompressing {len(bz2_files)} .bz2 files to temp .dat for satpy")
            _temp_dir = tempfile.mkdtemp(prefix="hsd_bz2_")
            for src in bz2_files:
                dst = Path(_temp_dir) / src.name.replace(".bz2", "").replace(".BZ2", "")
                try:
                    with bz2.open(src, "rb") as f_in:
                        data = f_in.read()
                    with open(dst, "wb") as f_out:
                        f_out.write(data)
                    _temp_files.append(dst)
                except Exception as e:
                    log.warning(f"    Failed to decompress {src.name}: {e}")
            if _temp_files:
                dat_files = _temp_files
                log.info(f"    Decompressed {len(_temp_files)} .bz2 files to temp .dat")
            else:
                log.error("    No usable data files found")
                return False, None

        filenames = [str(f) for f in dat_files]

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
        sample_names = [Path(f).name for f in dat_files[:5]]
        log.info(f"  Sample .dat filenames: {sample_names} (total {len(dat_files)})")

        # ── TIMING: scn.load() ───────────────────────────────────────────
        t0 = perf_counter() if profile else None
        all_bands = [f"B{i:02d}" for i in range(1, 17)]

        loaded_bands = []
        loaded_data = {}   # compat (Target/Japan): band -> (arr, sanitized_attrs, shape)
        scn = None         # only created for FLDK path

        if sector == "FLDK":
            # ========== FLDK PATH — KEPT EXACTLY AS BEFORE (no behavior change) ==========
            log.info("  Using FLDK loader (single Scene with all files)")
            scn = Scene(reader="ahi_hsd", filenames=filenames)
            if profile and t0 is not None:
                log.info(f"[TIME] stage=scene_create duration={perf_counter()-t0:.2f}s")
                t0 = perf_counter()   # restart for the actual load phase

            try:
                # Primary attempt: load everything Satpy can provide
                scn.load(all_bands)
                loaded_bands = [b for b in all_bands if b in scn]
                log.info(f"  ✓ Native load succeeded — {len(loaded_bands)} bands")
            except Exception as e:
                log.warning(f"  Bulk load encountered issues ({e}) — falling back to per-band loading...")
                loaded_bands = []
                for b in all_bands:
                    try:
                        scn.load([b])
                        loaded_bands.append(b)
                        log.info(f"    ✓ Loaded {b}")
                    except Exception as band_err:
                        log.info(f"    ⚠ Could not load {b} — {band_err}")

            # Ultra fallback: if almost nothing loaded, grab whatever Satpy sees
            if len(loaded_bands) < 4 and scn.available_dataset_names():
                try:
                    avail = list(scn.available_dataset_names())[:12]
                    scn.load(avail)
                    loaded_bands = list(set(loaded_bands) | set(avail))
                    log.info(f"  ✓ Ultra-fallback loaded additional datasets")
                except Exception:
                    pass

            if not loaded_bands:
                raise RuntimeError(f"Could not load any bands for folder {folder_name} — check that files match HS_H09_ pattern")

            log.info(f"  ✅ Final loaded bands for {sector}: {loaded_bands}")
        else:
            # ========== COMPATIBILITY LOADER FOR TARGET + JAPAN (PICK BEST SUB-AREA) ==========
            # Japan/Target data frequently contains multiple sub-area tiles (JP01-JP04 / R301-R304)
            # in the same folder. Satpy's ahi_hsd reader cannot composite them in one Scene
            # ("conflicting sizes for dimension 'y'"). We load each (band, sub-area) separately,
            # then pick the sub-area that gave us the most bands, discarding the rest.
            log.info(f"  Using micro-group loader (band + sub-area) for {sector}")

            micro_groups: Dict[tuple, list] = {}
            for f in dat_files:
                name = Path(f).name
                for i in range(1, 17):
                    b = f"B{i:02d}"
                    if f"_{b}_" in name:
                        area = _extract_area_token(name) or "UNK"
                        key = (b, area)
                        micro_groups.setdefault(key, []).append(str(f))
                        break

            if not micro_groups:
                log.warning("  No Bxx tokens found — falling back to single Scene")
                micro_groups[("ALL", "UNK")] = [str(f) for f in dat_files]

            log.info(f"  Micro-groups: { {f'{k[0]}/{k[1]}': len(v) for k, v in micro_groups.items()} }")

            # Load each (band, sub-area) separately, storing in all_segments[band][area]
            all_segments: Dict[str, Dict[str, tuple]] = {}  # band -> {area: (arr, attrs, shape, area_def or None)}
            for (b, area), files_group in micro_groups.items():
                try:
                    scn_b = Scene(reader="ahi_hsd", filenames=files_group)
                    scn_b.load([b])
                    if b in scn_b:
                        da = scn_b[b]
                        arr = da.values.copy()
                        attrs = _sanitize_attrs(dict(da.attrs))
                        shp = arr.shape
                        seg_area = da.attrs.get("area") if hasattr(da, "attrs") else None
                        all_segments.setdefault(b, {})[area] = (arr, attrs, shp, seg_area)
                        log.info(f"    ✓ Loaded {b} from {area} shape={shp}")
                    else:
                        log.info(f"    ⚠ {b}/{area} not present")
                    del scn_b
                except Exception as band_err:
                    log.info(f"    ⚠ Could not load {b}/{area}: {band_err}")

            # Pick the sub-area that covers the most bands
            area_band_count: Counter = Counter()
            for b, segments in all_segments.items():
                for area in segments:
                    area_band_count[area] += 1
            best_area = area_band_count.most_common(1)[0][0] if area_band_count else "UNK"
            dropped_bands = [b for b, segs in all_segments.items() if best_area not in segs]
            if len(area_band_count) > 1:
                log.warning(
                    f"  Multiple sub-areas: {dict(area_band_count)}. Using {best_area} ({area_band_count[best_area]} bands). "
                    f"Dropped bands not in {best_area}: {dropped_bands}"
                )

            # Populate loaded_data / loaded_bands from the chosen sub-area only
            for b, segments in all_segments.items():
                if best_area in segments:
                    arr, attrs, shp, seg_area = segments[best_area]
                    loaded_data[b] = (arr, attrs, shp)
                    loaded_bands.append(b)
                    if area_def is None and seg_area is not None:
                        area_def = seg_area

            if not loaded_bands:
                raise RuntimeError(
                    f"Could not load any bands for folder {folder_name}. "
                    f"Sample names seen: {sample_names}."
                )

            log.info(f"  ✅ Final loaded bands for {sector}: {loaded_bands} (sub-area: {best_area})")

        # Post-load resolution sanity for sectors (extra guard against silent mismatches)
        # Only meaningful for FLDK path (compat already isolated per-band so shapes are per-band by definition)
        if sector in ("Japan", "Target") and loaded_bands and scn is not None:
            try:
                shapes = {}
                for b in loaded_bands[:4]:  # sample a few
                    if b in scn:
                        shp = scn[b].shape
                        shapes[b] = shp
                if shapes:
                    unique_shapes = set(shapes.values())
                    if len(unique_shapes) > 1:
                        log.warning(f"  [{sector}] WARNING: loaded bands have non-uniform shapes {shapes} — output will still use unified y_2km/x_2km dims")
                    else:
                        log.info(f"  [{sector}] All sampled bands share consistent shape {list(unique_shapes)[0]} (using unified 2km dims in NC)")
            except Exception:
                pass  # never break processing on a diagnostic

        if profile and t0 is not None and sector == "FLDK":
            log.info(f"[TIME] stage=scn_load duration={perf_counter()-t0:.2f}s")
            if _HAS_PSUTIL:
                mem = psutil.Process().memory_info().rss / 1e6
                log.info(f"[MEM] after scn.load: {mem:.1f} MB RSS")

        # ═════════════════════════════════════════════════════════════════
        #  EXTRACT CALIBRATION DATA (from HSD headers — needs all bands)
        # ═════════════════════════════════════════════════════════════════
        cal_dict = {}
        hsd_header = None
        cal_src_files = dat_files if dat_files else bz2_files
        for src in cal_src_files:
            if len(cal_dict) >= 16:
                break
            try:
                h = _parse_full_hsd_header(src)
                if h and h.get("band_number", 0) > 0:
                    if hsd_header is None:
                        hsd_header = h
                    row = _extract_calibration_data(h)
                    if row and row["band"] not in cal_dict:
                        cal_dict[row["band"]] = row
            except Exception:
                pass
        if cal_dict:
            log.info(f"  [CAL] Extracted calibration for {len(cal_dict)} bands")

        # Build global attrs shared by all per-band NC files (keep minimal — shared metadata goes to ads.json)
        product = folder_name[:-(len(timestamp) + 1)]
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
        if scn is not None and "B01" in scn:
            for k, v in scn["B01"].attrs.items():
                if k not in global_attrs:
                    try:
                        global_attrs[k] = _sanitize_attrs({k: v})[k]
                    except Exception:
                        pass
        elif sector in ("Japan", "Target") and "B01" in loaded_data:
            _, b01_attrs, _ = loaded_data["B01"]
            for k in ("platform_name", "sensor", "start_time", "end_time"):
                if k in b01_attrs and k not in global_attrs:
                    global_attrs[k] = b01_attrs[k]
        # Sector metadata
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
            if hsd_header:
                seg = hsd_header.get("segment_number", 0)
                total_seg = hsd_header.get("total_segments", 0)
                global_attrs["target_segment"] = f"{seg}/{total_seg}"

        # ═════════════════════════════════════════════════════════════════
        #  WRITE EACH BAND TO ITS OWN NC FILE  (packed int16)
        # ═════════════════════════════════════════════════════════════════

        # Pre-extract area_def and CRS from first band for FLDK path
        if sector == "FLDK" and loaded_bands:
            for band in loaded_bands:
                try:
                    _da = scn[band]
                    if "area" in _da.attrs:
                        area_def = _da.attrs["area"]
                        break
                except Exception:
                    continue
        if ads and area_def:
            ads.set_crs(_crs_from_area(area_def))
            if ads.geotransform is None:
                gt, am = _geotransform_from_area(area_def)
                if gt: ads.set_geotransform(gt)
                if am and ads.area_meta is None: ads.set_area_meta(am)

        comp_level = 1 if fast_test else (9 if small else 6)
        written_bands = []
        _band_results = []
        b03_mmap_ref = None

        def _write_one_band(band):
            nonlocal mmap_path, b03_mmap_ref
            t_band = perf_counter() if profile else None
            log.info(f"  [{band}] materialising...")
            _arr = None
            _attrs = None
            try:
                if sector in ("Japan", "Target") and band in loaded_data:
                    _arr, _attrs, _shp = loaded_data[band]
                    if band == "B03" and fast_test:
                        return band, False, None, None
                else:
                    _da = scn[band]
                    if _da.dtype != np.float32:
                        _da = _da.astype(np.float32)
                    if band == "B03" and not fast_test:
                        try:
                            _arr, _attrs = _extract_b03_chunked(_da, datetime_folder, profile=profile)
                            mmap_path = datetime_folder / "b03_tmp.mmap"
                            b03_mmap_ref = _arr
                        except OSError as e:
                            log.warning(f"  [B03] Skipped due to disk space: {e}")
                            return band, False, None, None
                    elif band == "B03":
                        return band, False, None, None
                    else:
                        _arr = _da.values.copy()
                        _attrs = _sanitize_attrs(dict(_da.attrs))

                band_nc = datetime_folder / f"{product}_{band}_{timestamp}.nc"
                _ok = _write_band_nc(band_nc, band, _arr, cal_dict.get(band), 0, global_attrs, complevel=comp_level)
                if _ok:
                    valid = _arr[np.isfinite(_arr)]
                    vmin = float(valid.min()) if valid.size else float("nan")
                    vmax = float(valid.max()) if valid.size else float("nan")
                    log.info(f"  [{band}] shape={_arr.shape}  min={vmin:.3f}  max={vmax:.3f}  ->  {band_nc.name}")
                if profile and t_band is not None:
                    log.info(f"[TIME] stage=band_{band} duration={perf_counter()-t_band:.2f}s")
                return band, _ok, _arr if _ok else None, _attrs if _ok else None
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

        for band, _ok, _arr, _attrs in _band_results:
            if _ok:
                written_bands.append(band)
                if ads:
                    ads.register_band(band, _arr, _attrs)
            elif ads:
                ads.register_failed_band(band, "processing_failed")

        if not written_bands:
            log.error("[!] Zero bands written — aborting.")
            return False, None

        failed_to_load = sorted(set(all_bands) - set(loaded_bands))
        failed_to_write = sorted(set(loaded_bands) - set(written_bands))
        log.info(f"[+] Wrote {len(written_bands)} bands to per-band NC files")
        if failed_to_load:
            log.info(f"    Bands not loaded: {failed_to_load}")
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
        if ads and hsd_header:
            cl_compat = {
                "COFF": hsd_header.get("coff", 0),
                "LOFF": hsd_header.get("loff", 0),
                "sub_lon": hsd_header.get("sub_lon", 0),
                "CFAC": hsd_header.get("cfac", 0),
                "LFAC": hsd_header.get("lfac", 0),
                "distance_from_earth_center": hsd_header.get("distance_from_earth_center", 0),
                "earth_equatorial_radius": hsd_header.get("earth_equatorial_radius", 0),
                "earth_polar_radius": hsd_header.get("earth_polar_radius", 0),
            }
            ads.set_coff_loff(cl_compat)
            ads.observation_start_time = hsd_header.get("observation_start_time")
            ads.observation_end_time = hsd_header.get("observation_end_time")
            # Compute solar geometry from observation time (HSD header's sun_position is
            # in an inertial frame and is practically frozen across hours — useless for SZA)
            solar = _compute_solar_geometry(ads.observation_start_time) if ads.observation_start_time else {}
            if solar:
                ads.solar_geometry = solar
            # Still store raw HSD sun_position for reference (debugging only)
            sp = hsd_header.get("block4", {}).get("sun_position")
            if sp is not None:
                ads.sun_position = [float(v) for v in sp]
            log.info(f"  [HSD] COFF={hsd_header.get('coff', 0):.1f}  LOFF={hsd_header.get('loff', 0):.1f}  "
                     f"sub_lon={hsd_header.get('sub_lon', 0):.4f}  "
                     f"scan={ads.observation_start_time}–{ads.observation_end_time}")

        # --- 2km reference geotransform ---
        if ads and area_def:
            try:
                gt2, am = _compute_2km_geotransform(area_def)
                if gt2:
                    ads.set_geotransform_2km(gt2)
                    if am:
                        sh = am.get("shape", [0, 0])
                        if len(sh) == 2:
                            ads.set_ref_grid_size(sh[1], sh[0])
                    log.info(f"  [CRS] 2km ref geotransform: {gt2}")
            except Exception: pass

        # Write ADS sidecar — name AHI_<sector>_<timestamp>.ads.json, not band-specific
        if ads and written_bands:
            sidecar_name = f"AHI_{sector}_{timestamp}.ads.json"
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
                    for key in ["crs", "geotransform", "geotransform_2km", "area_meta", "coff_loff", "sector", "observation_start_time", "observation_end_time", "sun_position", "solar_geometry", "physical_constants"]:
                        val = existing.get(key)
                        if val is not None and getattr(ads, key, None) is None:
                            setattr(ads, key, val)
                    log.info(f"[ADS] Merged existing sidecar ({len(existing.get('band_stats', {}))} existing bands)")
                except Exception as exc:
                    log.warning(f"[ADS] Could not merge existing sidecar: {exc}")

            # Then merge any old-format band-specific sidecars and delete them
            for prev_sc in sorted(datetime_folder.glob(f"*.ads.json")):
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

            ref_nc = datetime_folder / f"{product}_{written_bands[0]}_{timestamp}.nc"
            ads.nc_path = str(ref_nc)
            ads.nc_size_mb = round(ref_nc.stat().st_size / 1e6, 2) if ref_nc.exists() else 0.0
            ads.write_sidecar(ref_nc, sidecar_path=sidecar_path)
            ads._derecho_ready_hook(sidecar_path, ads.to_dict())

        # Merge NDMW wind data if present
        if written_bands:
            ref_nc = datetime_folder / f"{product}_{written_bands[0]}_{timestamp}.nc"
            _merge_wind_data_if_present(ref_nc, datetime_folder, ads_enabled)

        # Cleanup B03 memmap
        if b03_mmap_ref is not None:
            try:
                if hasattr(b03_mmap_ref, '_mmap'):
                    b03_mmap_ref._mmap.close()
            except Exception:
                pass
            b03_mmap_ref = None
        if mmap_path and mmap_path.exists():
            try:
                gc.collect()
                mmap_path.unlink()
                log.info("[~] Removed B03 temp memmap")
            except Exception as exc:
                log.warning(f"[~] Could not remove B03 memmap: {exc}")

        # Delete .dat files
        if not keep_dat:
            removed = 0
            for f in dat_files:
                try:
                    if f.exists(): f.unlink(); removed += 1
                except Exception: pass
            log.info(f"[~] Removed {removed}/{len(dat_files)} .dat files")
            if ads: ads.add_provenance("cleanup", f"{removed} .dat files removed")

        if profile and t_total is not None:
            log.info(f"[TIME] stage=TOTAL duration={perf_counter()-t_total:.2f}s")

        return True, datetime_folder

    except Exception as exc:
        log.error(f"[!] Failed: {exc}")
        traceback.print_exc()
        return False, None
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

def _write_band_nc(output_path: Path, band: str, data: np.ndarray,
                   cal_data: dict = None, qc_val: int = 0,
                   global_attrs: dict = None,
                   complevel: int = 6) -> bool:
    """Write one band as a compact NC file with packed int16.
    CRS and shared metadata go in the ads.json sidecar only.
    """
    import netCDF4 as _nc4
    try:
        valid = data[np.isfinite(data)]
        if valid.size == 0:
            log.warning(f"  [{band}] No valid data — skipping")
            return False
        dmin = float(valid.min())
        dmax = float(valid.max())
        scale = (dmax - dmin) / 65534.0
        if scale == 0:
            scale = 1.0
        offset = dmin + 32767.0 * scale
        valid = np.isfinite(data)
        packed = np.empty(data.shape, dtype=np.int16)
        if valid.any():
            packed[valid] = np.round((data[valid] - offset) / scale).astype(np.int16)
        packed[~valid] = -32768
        log.info(f"  [{band}] packed int16  range=[{packed.min()},{packed.max()}]  "
                 f"scale={scale:.6e}  offset={offset:.4f}  ({output_path.name})")
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
            # Per-band QC flag
            _qc = _ds.createVariable("qc_flags", "i1", ())
            _qc.long_name = "Band quality flag"
            _qc.flag_values = "0, 1, 2"
            _qc.flag_meanings = "pass questionable failed"
            _qc[:] = np.int8(qc_val)
            # Per-band calibration scalars
            if cal_data:
                for _field in ["radiance_slope", "radiance_intercept",
                               "reflectance_albedo_coeff", "c0_rad2tb", "c1_rad2tb", "c2_rad2tb"]:
                    _v = _ds.createVariable(f"cal_{_field}", "f4", ())
                    _v[:] = np.float32(cal_data[_field])
                _v = _ds.createVariable("cal_calibration_units", str, ())
                _v[...] = cal_data.get("calibration_units", "")
                _v = _ds.createVariable("cal_resolution_m", "i4", ())
                _v[:] = np.int32(cal_data.get("resolution_m", 2000))
            # Minimal global attrs
            if global_attrs:
                for _k, _v in global_attrs.items():
                    try:
                        setattr(_ds, _k, _v)
                    except Exception:
                        pass
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
    parser.add_argument("--keep", action="store_true", help="Keep .dat files after conversion (ignored when reading .bz2 directly)")
    parser.add_argument("--bz2", action="store_true", help="Read .bz2 files directly (skip extraction, decompress to temp)")
    parser.add_argument("--no-ads", action="store_true", help="Skip ADS sidecar generation")
    parser.add_argument("--profile", action="store_true", help="Enable perf_counter timing instrumentation")
    parser.add_argument("--fast-test", action="store_true", help="Quick benchmark: complevel=1 + skip B03 memmap")
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
    print("=" * 70)

    folders = find_datetime_folders(input_dir)
    if not folders:
        log.error("[!] No supported AHI-L1b-(FLDK|Japan|Target)_* folders found (and no fallback .dat files)")
        sys.exit(1)

    success = 0
    for folder in folders:
        ok, _ = process_to_nc(
            folder,
            keep_dat=args.keep,
            ads_enabled=not args.no_ads,
            profile=args.profile,
            fast_test=args.fast_test,
            workers=args.workers,
            small=args.small,
        )
        if ok:
            success += 1

    print("\n" + "=" * 70)
    print(f"SUMMARY: {success}/{len(folders)} folders converted")
    print("=" * 70)
    print(f"Successfully processed: {success}")
    print("STATISTICS_OUTPUT:")
    print(f"Processed: {success}")


if __name__ == "__main__":
    main()