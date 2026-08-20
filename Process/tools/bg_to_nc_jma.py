#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  MONWATCH  –  CYCLONE  V3.5.x                                                 ║
║  bg_to_nc_jma.py  –  Himawari HSD .DAT/.bz2  →  JMA/MSC NetCDF (lat/lon grid) ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  Python port of the JMA Meteorological Satellite Center (MSC) sample           ║
║  program "hisd2netcdf" (sample_code_netcdf121).                                ║
║                                                                                ║
║  Unlike bg_to_nc.py (which keeps the native geostationary x/y grid + .ads.json ║
║  sidecar), this converter RESAMPLES every band onto a regular equirectangular  ║
║  latitude/longitude grid and writes a self-contained CF-1.4 NetCDF exactly as   ║
║  the JMA sample does:                                                           ║
║    * dimensions : latitude, longitude, start_time, end_time                    ║
║    * variables  : latitude(degrees_north), longitude(degrees_east),             ║
║                   albedo (bands 1-6)  or tbb (bands 7-16) [float, _FillValue]  ║
║                   start_time / end_time as Modified Julian Day doubles          ║
║    * global     : title, institution=MSC/JMA, source, history, Conventions      ║
║                                                                                ║
║  Faithful C-port details (see hisd_read.c / hisd_pixlin2lonlat.c / main.c):     ║
║    - Full HSD header Block 1-11 parse incl. Block 8 nav-correction table and    ║
║      Block 9 observation-time (lineNo/obsMJD) table.                            ║
║    - LRIT/HRIT normalized geostationary projection lonlat<->pixlin.             ║
║    - Per-pixel navigation correction (shift table + rotation) + nearest          ║
║      neighbor read (count = pixel[(int)(lin+0.5)-segLineNo, (int)(pix+0.5)-1])  ║
║    - count -> radiance -> albedo (rad2albedo) or TBB (Planck + c0/c1/c2).       ║
║    - start_time/end_time interpolated from the Block 9 obs-time table using      ║
║      the grid's minLine/maxLine.                                                 ║
║  Vectorized with numpy; supports .bz2 directly and a folder-scan mode.           ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
import sys
import re
import bz2
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

import numpy as np

try:
    import netCDF4
except ImportError:
    netCDF4 = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("cyclone.bg_to_nc_jma")


# ── JMA sample program constants (main.c) ────────────────────────────────────
INVALID_OUTPUT = -1.0             # phys fill value (main.c: INVALID -1)
SCLUNIT        = 2.0 ** -16       # 1.52587890625e-05  (hisd_pixlin2lonlat.c)

DEFAULT_WIDTH  = 5501             # main.c: WIDTH    pixel number
DEFAULT_HEIGHT = 2001             # main.c: HEIGHT   line number
DEFAULT_LTLON  = 90.0             # main.c: LTLON    left top longitude
DEFAULT_LTLAT  = 10.0             # main.c: LTLAT    left top latitude
DEFAULT_DLON   = 0.01             # main.c: DLON     spatial resolution (lon)
DEFAULT_DLAT   = 0.01             # main.c: DLAT     spatial resolution (lat)

_TIME_UNIT = "days since 1858-11-17 0:0:0"   # main.c defNetcdf()


# ══════════════════════════════════════════════════════════════════════════════
#  HSD HEADER STRUCTURES  (hisd.h / hisd_read.c offsets)
# ══════════════════════════════════════════════════════════════════════════════
def _dtypes(endian: str = "<"):
    """Build the HSD header numpy dtypes for a given byte order prefix."""
    return {
        "b1": np.dtype([
            ("hb", "u1"), ("bl", endian + "u2"), ("nhb", endian + "u2"),
            ("bo", "u1"), ("sat", "S16"), ("pcn", "S16"), ("oa", "S4"),
            ("ooi", "S2"), ("tl", endian + "u2"), ("ost", "f8"),
            ("oet", "f8"), ("fct", "f8"), ("thl", endian + "u4"),
            ("tdl", endian + "u4"), ("qf", "u1", (4,)), ("ver", "S32"),
            ("fn", "S128"), ("sp", "S40"),
        ]),
        "b2": np.dtype([
            ("hb", "u1"), ("bl", endian + "u2"), ("bitPix", endian + "u2"),
            ("nPix", endian + "u2"), ("nLin", endian + "u2"),
            ("comp", "u1"), ("sp", "S40"),
        ]),
        "b3": np.dtype([
            ("hb", "u1"), ("bl", endian + "u2"), ("subLon", "f8"),
            ("cfac", endian + "u4"), ("lfac", endian + "u4"),
            ("coff", endian + "f4"), ("loff", endian + "f4"),
            ("satDis", "f8"), ("eqR", "f8"), ("poR", "f8"),
            ("pp1", "f8"), ("pp2", "f8"), ("pp3", "f8"), ("ppSd", "f8"),
            ("rk", endian + "i2"), ("rs", endian + "i2"), ("sp", "S40"),
        ]),
        "b4": np.dtype([
            ("hb", "u1"), ("bl", endian + "u2"), ("navMjd", "f8"),
            ("sspLon", "f8"), ("sspLat", "f8"), ("satDis", "f8"),
            ("ndLon", "f8"), ("ndLat", "f8"), ("sun", "f8", (3,)),
            ("moon", "f8", (3,)), ("sp", "S40"),
        ]),
        "b5": np.dtype([
            ("hb", "u1"), ("bl", endian + "u2"), ("bandNo", endian + "u2"),
            ("waveLen", "f8"), ("bitPix", endian + "u2"),
            ("errCnt", endian + "u2"), ("outCnt", endian + "u2"),
            ("gain", "f8"), ("cnst", "f8"),
        ]),
        "b5ir": np.dtype([
            ("c0", "f8"), ("c1", "f8"), ("c2", "f8"), ("d0", "f8"),
            ("d1", "f8"), ("d2", "f8"), ("c", "f8"), ("h", "f8"),
            ("k", "f8"), ("sp", "S40"),
        ]),
        "b5vis": np.dtype([
            ("rad2alb", "f8"), ("spareV", "S104"),
        ]),
        "b6": np.dtype([("hb", "u1"), ("bl", endian + "u2")]),
        "b7": np.dtype([
            ("hb", "u1"), ("bl", endian + "u2"), ("totSeg", "u1"),
            ("segNo", "u1"), ("strLine", endian + "u2"), ("sp", "S40"),
        ]),
        "b8": np.dtype([
            ("hb", "u1"), ("bl", endian + "u2"), ("R_C", endian + "f4"),
            ("R_L", endian + "f4"), ("R_A", "f8"), ("ncorr", endian + "u2"),
        ]),
        "b8sub": np.dtype([
            ("lineNo", endian + "u2"), ("colShift", endian + "f4"),
            ("lineShift", endian + "f4"),
        ]),
        "b9": np.dtype([("hb", "u1"), ("bl", endian + "u2"),
                        ("nobs", endian + "u2")]),
        "b9sub": np.dtype([("lineNo", endian + "u2"), ("obsMJD", "f8")]),
        "b10": np.dtype([("hb", "u1"), ("bl", endian + "u4"),
                         ("nerr", endian + "u2")]),
        "b11": np.dtype([("hb", "u1"), ("bl", endian + "u2"), ("sp", "S256")]),
    }


def _np_read(buf: bytes, dt, offset: int):
    return np.frombuffer(buf, dtype=dt, count=1, offset=offset)[0]


def _sanitize_name(b: bytes) -> str:
    return b.decode("utf-8", errors="replace").strip("\x00 ")


class HSDNavCorr:
    """Block-8 navigation correction, with the interpolated shift table
    (C: hisd_comp_table())."""
    __slots__ = ("R_C", "R_L", "R_A", "ncorr", "lineNo", "colShift",
                 "lineShift", "start_line", "line_num", "cmp_coff", "cmp_loff")

    def __init__(self, b8, sub, endian: str):
        self.R_C = float(b8["R_C"])
        self.R_L = float(b8["R_L"])
        self.R_A = float(b8["R_A"]) / 1000.0 / 1000.0   # C: RoCorrection/1e6
        self.ncorr = int(b8["ncorr"])
        self.lineNo = sub["lineNo"].astype(np.int64)
        self.colShift = sub["colShift"].astype(np.float64)
        self.lineShift = sub["lineShift"].astype(np.float64)
        self.start_line = None
        self.line_num = None
        self.cmp_coff = None
        self.cmp_loff = None
        if self.ncorr >= 2:
            self.start_line = int(self.lineNo[0])
            self.line_num = int(self.lineNo[-1]) - self.start_line + 1
            if self.line_num >= 2:
                lines = np.arange(self.start_line,
                                  self.start_line + self.line_num)
                self.cmp_coff = np.interp(lines, self.lineNo, self.colShift,
                                          left=self.colShift[0],
                                          right=self.colShift[-1])
                self.cmp_loff = np.interp(lines, self.lineNo, self.lineShift,
                                          left=self.lineShift[0],
                                          right=self.lineShift[-1])


class HSDFile:
    """One Himawari Standard Data (segment) file with fully parsed header."""
    def __init__(self, path):
        path = Path(path)
        self.path = path
        self.name = path.name
        self.is_bz2 = path.suffix.lower() == ".bz2"
        self.buf: Optional[bytes] = None          # decompressed payload (bz2)
        self.header_offset = 0
        self.data_len = 0
        self.endian = "<"
        self.data_dtype = np.dtype("<u2")

        # block1..block5 scalars
        self.sat_name = ""
        self.proc_name = ""
        self.obs_area = ""
        self.timeline = 0
        self.obs_start_mjd = 0.0
        self.obs_end_mjd = 0.0
        self.ver = ""
        self.ncols = 0
        self.nlines = 0
        self.nbits = 0
        self.sub_lon = 140.7
        self.cfac = 20466275
        self.lfac = 20466275
        self.coff = 0.0
        self.loff = 0.0
        self.sat_dis = 0.0
        self.eq_r = 0.0
        self.pol_r = 0.0
        self.pp1 = 0.0
        self.pp2 = 0.0
        self.pp3 = 0.0
        self.pp_sd = 0.0
        self.band_no = 0
        self.wave_len = 0.0
        self.error_cnt = 65535
        self.out_cnt = 65534
        self.gain = 0.0
        self.cnst = 0.0
        # cal extension
        self.c0 = 0.0; self.c1 = 0.0; self.c2 = 0.0
        self.planck = 0.0; self.c_light = 0.0; self.k_bolz = 0.0
        self.rad2albedo = 0.0
        self.is_ir = False
        # block7
        self.total_segments = 1
        self.segment_no = 1
        self.start_line = 1
        # block8 / block9
        self.navcorr: Optional[HSDNavCorr] = None
        self.obs_lines: Optional[np.ndarray] = None   # lineNo[]
        self.obs_mjd: Optional[np.ndarray] = None     # obsMJD[]

        self._pix = None

        self._parse()

    @property
    def end_line(self) -> int:
        return self.start_line + self.nlines - 1

    # ── loading ──────────────────────────────────────────────────────────────
    def _open_bytes(self) -> bytes:
        if self.is_bz2:
            with bz2.open(self.path, "rb") as f:
                return f.read()
        with open(self.path, "rb") as f:
            return f.read()

    def _parse(self):
        data = self._open_bytes()
        self.buf = data
        if len(data) < 282:
            raise ValueError(f"file too small: {self.name}")

        # Determine byte order the way the C sample does: blocklength must be 282.
        endian = "<"
        b1_lt = _np_read(data, _dtypes("<")["b1"], 0)
        if int(b1_lt["bl"]) != 282:
            endian = ">"
            b1 = _np_read(data, _dtypes(">")["b1"], 0)
        else:
            b1 = b1_lt
        dt = _dtypes(endian)
        self.endian = endian
        if int(b1["bl"]) != 282:
            raise ValueError(f"[HSD] block1 length {b1['bl']} != 282 ({self.name})")

        self.sat_name = _sanitize_name(b1["sat"].tobytes())
        self.proc_name = _sanitize_name(b1["pcn"].tobytes())
        self.obs_area = _sanitize_name(b1["oa"].tobytes())
        self.timeline = int(b1["tl"])
        self.obs_start_mjd = float(b1["ost"])
        self.obs_end_mjd = float(b1["oet"])
        self.ver = _sanitize_name(b1["ver"].tobytes())
        self.header_offset = int(b1["thl"])
        self.data_len = int(b1["tdl"])

        off = int(b1["bl"])
        b2 = _np_read(data, dt["b2"], off)
        self.ncols = int(b2["nPix"])
        self.nlines = int(b2["nLin"])
        self.nbits = int(b2["bitPix"])
        off += int(b2["bl"])

        b3 = _np_read(data, dt["b3"], off)
        self.sub_lon = float(b3["subLon"])
        self.cfac = int(b3["cfac"])
        self.lfac = int(b3["lfac"])
        self.coff = float(b3["coff"])
        self.loff = float(b3["loff"])
        self.sat_dis = float(b3["satDis"])
        self.eq_r = float(b3["eqR"])
        self.pol_r = float(b3["poR"])
        self.pp1 = float(b3["pp1"])
        self.pp2 = float(b3["pp2"])
        self.pp3 = float(b3["pp3"])
        self.pp_sd = float(b3["ppSd"])
        off += int(b3["bl"])

        b4 = _np_read(data, dt["b4"], off)
        off += int(b4["bl"])

        b5 = _np_read(data, dt["b5"], off)
        self.band_no = int(b5["bandNo"])
        self.wave_len = float(b5["waveLen"])
        self.error_cnt = int(b5["errCnt"])
        self.out_cnt = int(b5["outCnt"])
        self.gain = float(b5["gain"])
        self.cnst = float(b5["cnst"])
        ext_off = off + int(b5["bl"]) - (
            dt["b5ir"].itemsize if b5["bandNo"] >= 7 else dt["b5vis"].itemsize
        )
        off += int(b5["bl"])
        if int(b5["bandNo"]) >= 7:
            ext = _np_read(data, dt["b5ir"], ext_off)
            self.is_ir = True
            self.c0 = float(ext["c0"]); self.c1 = float(ext["c1"])
            self.c2 = float(ext["c2"]); self.planck = float(ext["h"])
            self.c_light = float(ext["c"]); self.k_bolz = float(ext["k"])
        else:
            ext = _np_read(data, dt["b5vis"], ext_off)
            self.is_ir = False
            self.rad2albedo = float(ext["rad2alb"])
        self.rad2albedo = self.rad2albedo or 0.0

        b6 = _np_read(data, dt["b6"], off)
        off += int(b6["bl"])

        b7 = _np_read(data, dt["b7"], off)
        self.total_segments = int(b7["totSeg"])
        self.segment_no = int(b7["segNo"])
        self.start_line = int(b7["strLine"])
        off += int(b7["bl"])

        b8 = _np_read(data, dt["b8"], off)
        ncorr = int(b8["ncorr"])
        if ncorr > 0:
            sub = np.frombuffer(data, dtype=dt["b8sub"], count=ncorr,
                                offset=off + dt["b8"].itemsize)
            self.navcorr = HSDNavCorr(b8, sub, endian)
        off += int(b8["bl"])

        b9 = _np_read(data, dt["b9"], off)
        nobs = int(b9["nobs"])
        if nobs > 0:
            sub = np.frombuffer(data, dtype=dt["b9sub"], count=nobs,
                                offset=off + dt["b9"].itemsize)
            self.obs_lines = sub["lineNo"].astype(np.int64)
            self.obs_mjd = sub["obsMJD"].astype(np.float64)
        off += int(b9["bl"])

        self.data_dtype = np.dtype(">u2" if self.endian == ">" else "<u2")

    # ── pixel payload ────────────────────────────────────────────────────────
    def pixel_array(self) -> np.ndarray:
        """Return the segment's raw count array as (nLin, nPix) uint16."""
        shape = (self.nlines, self.ncols)
        expect = shape[0] * shape[1] * 2
        if self.is_bz2:
            arr = np.frombuffer(self.buf, dtype=self.data_dtype,
                                count=shape[0] * shape[1],
                                offset=self.header_offset).reshape(shape)
            return arr
        try:
            arr = np.memmap(self.path, dtype=self.data_dtype, mode="r",
                            offset=self.header_offset, shape=shape)
            return arr
        except (ValueError, OSError):
            arr = np.frombuffer(self.buf, dtype=self.data_dtype,
                                count=shape[0] * shape[1],
                                offset=self.header_offset).reshape(shape)
            return arr

    def release(self):
        self.buf = None
        self._pix = None


# ══════════════════════════════════════════════════════════════════════════════
#  PROJECTION  (hisd_pixlin2lonlat.c)
# ══════════════════════════════════════════════════════════════════════════════
def lonlat_to_pixlin_vec(h: HSDFile, lon, lat):
    """Vectorized lonlat_to_pixlin(). Returns (pix, lin, valid) where invalid
    points have pix=lin=-9999 and valid=False (C: ERROR_END)."""
    lon = np.asarray(lon, dtype=np.float64)
    lat = np.asarray(lat, dtype=np.float64)
    valid = np.isfinite(lon) & np.isfinite(lat) & (lat >= -90.0) & (lat <= 90.0)

    lon = lon - 360.0 * np.floor((lon + 180.0) / 360.0)   # wrap to [-180,180)
    lonr = np.deg2rad(lon)
    latr = np.deg2rad(lat)

    phi = np.arctan(h.pp2 * np.tan(latr))
    cos_phi = np.cos(phi)
    denom = 1.0 - h.pp1 * cos_phi * cos_phi
    with np.errstate(invalid="ignore", divide="ignore"):
        re_ = h.pol_r / np.sqrt(np.maximum(denom, 0.0))

    dlon = lonr - np.deg2rad(h.sub_lon)
    r1 = h.sat_dis - re_ * cos_phi * np.cos(dlon)
    r2 = -re_ * cos_phi * np.sin(dlon)
    r3 = re_ * np.sin(phi)
    # (8) check the reverse side of the Earth
    vis = (r1 * (r1 - h.sat_dis) + r2 * r2 + r3 * r3) <= 0.0
    valid &= vis

    rn = np.sqrt(r1 * r1 + r2 * r2 + r3 * r3)
    x = np.rad2deg(np.arctan2(-r2, r1))
    with np.errstate(invalid="ignore", divide="ignore"):
        y = np.rad2deg(np.arcsin(-r3 / rn))

    pix = h.coff + x * SCLUNIT * h.cfac
    lin = h.loff + y * SCLUNIT * h.lfac
    pix = np.where(valid, pix, -9999.0)
    lin = np.where(valid, lin, -9999.0)
    return pix, lin, valid


def pixlin_to_lonlat_vec(h: HSDFile, pix, lin):
    """Vectorized pixlin_to_lonlat(). Returns (lon, lat, valid)."""
    pix = np.asarray(pix, dtype=np.float64)
    lin = np.asarray(lin, dtype=np.float64)
    with np.errstate(invalid="ignore", divide="ignore"):
        x = np.deg2rad((pix - h.coff) / (SCLUNIT * h.cfac))
        y = np.deg2rad((lin - h.loff) / (SCLUNIT * h.lfac))
        cosx = np.cos(x); sinx = np.sin(x)
        cosy = np.cos(y); siny = np.sin(y)

        sd2 = (h.sat_dis * cosx * cosy) ** 2 - \
              (cosy * cosy + h.pp3 * siny * siny) * h.pp_sd
        valid = sd2 >= 0
        sd = np.sqrt(np.where(sd2 >= 0, sd2, 0.0))
        denom = cosy * cosy + h.pp3 * siny * siny
        sn = (h.sat_dis * cosx * cosy - sd) / np.where(denom == 0, 1.0, denom)
        s1 = h.sat_dis - sn * cosx * cosy
        s2 = sn * sinx * cosy
        s3 = -sn * siny
        sxy = np.sqrt(s1 * s1 + s2 * s2)
        lon = np.rad2deg(np.arctan2(s2, s1)) + h.sub_lon
        lat = np.rad2deg(np.arctan(h.pp3 * s3 / np.where(sxy == 0, 1.0, sxy)))
    lon = lon - 360.0 * np.floor((lon + 180.0) / 360.0)
    return np.where(valid, lon, -9999.0), np.where(valid, lat, -9999.0), valid


# ══════════════════════════════════════════════════════════════════════════════
#  COUNT → PHYSICAL  (main.c getData() / hisd_read.c hisd_radiance_to_tbb())
# ══════════════════════════════════════════════════════════════════════════════
def _counts_to_phys(h: HSDFile, counts: np.ndarray) -> np.ndarray:
    """count (uint16 array) -> float32 phys (albedo or TBB), INVALID_OUTPUT where
    count is error/out-of-scan or (IR) radiance <= 0."""
    rad = counts.astype(np.float64) * h.gain + h.cnst     # radiance
    if not h.is_ir:
        phys = h.rad2albedo * rad
        return np.where(
            (counts != h.error_cnt) & (counts != h.out_cnt),
            phys.astype(np.float32), np.float32(INVALID_OUTPUT))
    lam = h.wave_len / 1.0e6          # [um] -> [m]
    r = rad * 1.0e6                   # W/(m^2 sr um) -> W/(m^2 sr m)
    planck_c1 = 2.0 * h.planck * h.c_light ** 2 / lam ** 5
    planck_c2 = h.planck * h.c_light / h.k_bolz / lam
    with np.errstate(invalid="ignore", divide="ignore"):
        te = planck_c2 / np.log(np.where(r > 0.0, planck_c1, 1.0) / np.where(r > 0.0, r, 1.0) + 1.0)
        tb = h.c0 + h.c1 * te + h.c2 * te * te
    ok = (counts != h.error_cnt) & (counts != h.out_cnt) & (r > 0.0)
    return np.where(ok, tb.astype(np.float32), np.float32(INVALID_OUTPUT))


def _scan_times_from_lines(hdrs: List[HSDFile], min_line: float,
                           max_line: float) -> Tuple[float, float]:
    """C main.c step 4: interpolate start/end MJD from the Block-9 obs-time
    tables using the grid's minLine/maxLine."""
    start = None
    end = None
    for h in hdrs:
        obs_lines = h.obs_lines
        obs_mjd = h.obs_mjd
        if obs_lines is None or obs_mjd is None or obs_lines.size == 0:
            continue
        if h.start_line <= min_line <= h.end_line:
            for i in range(1, obs_lines.size):
                if min_line < obs_lines[i]:
                    start = float(obs_mjd[i - 1]); break
                elif min_line == obs_lines[i]:
                    start = float(obs_mjd[i]); break
        if h.start_line <= max_line <= h.end_line:
            for i in range(1, obs_lines.size):
                if max_line < obs_lines[i]:
                    end = float(obs_mjd[i - 1]); break
                elif max_line == obs_lines[i]:
                    end = float(obs_mjd[i]); break
    if start is None:
        start = hdrs[0].obs_start_mjd
    if end is None:
        end = hdrs[0].obs_end_mjd
    return start, end


# ══════════════════════════════════════════════════════════════════════════════
#  CONVERSION  (main.c getData())
# ══════════════════════════════════════════════════════════════════════════════
class GridSpec:
    """Output geographic grid (equirectangular), same fields as main.c param."""
    def __init__(self, width: int, height: int, ltlon: float, ltlat: float,
                 dlon: float, dlat: float):
        self.width = width
        self.height = height
        self.ltlon = ltlon
        self.ltlat = ltlat
        self.dlon = dlon
        self.dlat = dlat

    def lat(self) -> np.ndarray:
        return np.asarray([self.ltlat - self.dlat * i
                           for i in range(self.height)], dtype=np.float64)

    def lon(self) -> np.ndarray:
        return np.asarray([self.ltlon + self.dlon * i
                           for i in range(self.width)], dtype=np.float64)

    def is_full_disk_hint(self) -> bool:
        return self.width <= 0 or self.height <= 0


def convert_band_to_grid(seg_files: List[Path],
                         grid: GridSpec,
                         chunk_rows: int = 512) -> Tuple[np.ndarray, float, float,
                                                          HSDFile, List[HSDFile]]:
    """Resample one band (its segment files) onto the lat/lon grid.

    Returns (phys, start_mjd, end_mjd, h0, hdrs). phys has shape
    (height, width) with INVALID_OUTPUT fill, matching C data->phys layout
    (index = jj*width + ii, jj=lat row top->bottom, ii=lon col left->right).
    """
    hdrs: List[HSDFile] = []
    for f in seg_files:
        try:
            hdrs.append(HSDFile(f))
        except Exception as exc:
            log.warning(f"[HSD] could not read {f.name}: {exc}")
    if not hdrs:
        raise RuntimeError("no readable segment files")
    h0 = hdrs[0]

    lon1d = grid.lon()
    lat1d = grid.lat()
    phys = np.full((grid.height, grid.width), INVALID_OUTPUT, dtype=np.float32)
    min_line = np.inf
    max_line = -np.inf
    seg_pix_cache = {}

    def _seg_pix(idx: int) -> np.ndarray:
        if idx not in seg_pix_cache:
            seg_pix_cache[idx] = hdrs[idx].pixel_array()
        return seg_pix_cache[idx]

    try:
        for r0 in range(0, grid.height, chunk_rows):
            r1 = min(r0 + chunk_rows, grid.height)
            lat_sub = lat1d[r0:r1]
            lon2d, lat2d = np.meshgrid(lon1d, lat_sub)
            pix, lin, visible = lonlat_to_pixlin_vec(h0, lon2d, lat2d)
            linv = np.where(visible, lin, np.nan)
            if visible.any():
                lmn = float(np.nanmin(linv)); lmx = float(np.nanmax(linv))
                if lmn < min_line: min_line = lmn
                if lmx > max_line: max_line = lmx
            done = np.zeros(visible.shape, dtype=bool)

            for idx, hh in enumerate(hdrs):
                sl = float(hh.start_line)
                el = float(hh.end_line)
                # C: startLine-0.5 <= Lin < endLine+0.5
                in_seg = (lin >= sl - 0.5) & (lin < el + 0.5) & visible & ~done
                if not in_seg.any():
                    continue
                lp = pix[in_seg]
                ll_in = lin[in_seg]
                # navigation correction: shift table
                nc = hh.navcorr
                if nc is not None and nc.cmp_coff is not None:
                    ii = np.trunc(ll_in + 0.5).astype(np.int64) - nc.start_line
                    np.clip(ii, 0, nc.line_num - 1, out=ii)
                    lp = lp - nc.cmp_coff[ii]
                    ll_in = ll_in - nc.cmp_loff[ii]
                # rotational correction
                ra = nc.R_A if nc is not None else 0.0
                rc = nc.R_C if nc is not None else 0.0
                rl = nc.R_L if nc is not None else 0.0
                hlin = (ll_in - rl) * np.cos(ra) - (lp - rc) * np.sin(ra) + rl
                hpix = (lp - rc) * np.cos(ra) + (ll_in - rl) * np.sin(ra) + rc
                # nearest neighbor (C: int(x+0.5) truncation toward zero)
                ll_pix = np.trunc(hlin + 0.5).astype(np.int64) - hh.start_line
                pp_pix = np.trunc(hpix + 0.5).astype(np.int64) - 1
                ok = (ll_pix >= 0) & (ll_pix < hh.nlines) & \
                     (pp_pix >= 0) & (pp_pix < hh.ncols)
                if not ok.any():
                    done |= in_seg
                    continue
                seg = _seg_pix(idx)
                cnt = seg[ll_pix[ok], pp_pix[ok]].astype(np.uint16)
                ph = _counts_to_phys(hh, cnt)
                sel_flat = np.flatnonzero(in_seg)[ok]
                phys_flat = phys[r0:r1].reshape(-1)
                keep = ph != INVALID_OUTPUT
                phys_flat[sel_flat[keep]] = ph[keep]
                done |= in_seg
        for idx in seg_pix_cache:
            try:
                seg_pix_cache[idx].flush() if hasattr(seg_pix_cache[idx], "flush") else None
            except Exception:
                pass
    finally:
        for idx in seg_pix_cache:
            seg_pix_cache[idx] = None

    if not (np.isfinite(min_line) and np.isfinite(max_line)):
        raise RuntimeError("no grid point projects onto the Earth disk")

    start_mjd, end_mjd = _scan_times_from_lines(hdrs, min_line, max_line)
    for hh in hdrs:
        hh.release()
    return phys, start_mjd, end_mjd, h0, hdrs


# ══════════════════════════════════════════════════════════════════════════════
#  NETCDF WRITER  (main.c defNetcdf() / putNetcdf())
# ══════════════════════════════════════════════════════════════════════════════
def _fill_grid_from_projection(h0: HSDFile, res: float) -> GridSpec:
    """Auto-derive a full-disk lat/lon grid at `res` degrees from the band's
    projection (used by folder mode when no explicit grid is given)."""
    ncols = h0.ncols
    nrows = h0.nlines * h0.total_segments
    if nrows < 1 or ncols < 1:
        raise RuntimeError("cannot auto-derive grid from empty projection")
    stride = 8
    pix, lin = np.meshgrid(np.arange(1, ncols + 1, stride),
                           np.arange(1, nrows + 1, stride))
    lon, lat, valid = pixlin_to_lonlat_vec(h0, pix.ravel(), lin.ravel())
    if not valid.any():
        raise RuntimeError("auto grid: projection produced no valid points")
    lat_min = float(lat[valid].min()); lat_max = float(lat[valid].max())
    lon_min = float(lon[valid].min()); lon_max = float(lon[valid].max())
    lat_min = max(lat_min, -90.0); lat_max = min(lat_max, 90.0)
    res = abs(res) or 0.02
    height = max(1, int(round((lat_max - lat_min) / res)))
    width = max(1, int(round((lon_max - lon_min) / res)))
    height = min(height, 20000)
    width = min(width, 20000)
    return GridSpec(width, height, lon_min, lat_max, res, res)


def write_jma_netcdf(out_path: Path, h0: HSDFile, grid: GridSpec,
                     phys: np.ndarray, start_mjd: float, end_mjd: float) -> Path:
    """Write the JMA-format NetCDF (faithful port of defNetcdf/putNetcdf)."""
    if netCDF4 is None:
        raise RuntimeError("netCDF4 is required (pip install netCDF4)")
    band = h0.band_no
    if band <= 6:
        phys_name, phys_unit, phys_std = "albedo", "1", "reflectivity"
        title = f"{h0.sat_name} band-{band} ALBEDO"
    else:
        phys_name, phys_unit, phys_std = "tbb", "K", "brightness_temperature"
        title = f"{h0.sat_name} band-{band} TBB"

    lat = grid.lat()
    lon = grid.lon()

    now = datetime.now(timezone.utc)
    fname = out_path.name
    try:
        libver = netCDF4.__netcdf4libversion__
    except Exception:
        libver = getattr(netCDF4, "getlibversion", lambda: "?")()
    history = (f"at {now.hour:02d}:{now.minute:02d}:{now.second:02d} "
               f"{now.month:02d}/{now.day:02d}/{now.year:04d}: file created. "
               f"{fname} (netCDF {libver})")

    with netCDF4.Dataset(str(out_path), "w", format="NETCDF3_CLASSIC") as ds:
        lat_dim = ds.createDimension("latitude", grid.height)
        lon_dim = ds.createDimension("longitude", grid.width)
        ds.createDimension("start_time", 1)
        ds.createDimension("end_time", 1)

        var_lat = ds.createVariable("latitude", "f4", (lat_dim,))
        var_lat.units = "degrees_north"
        var_lat.long_name = "latitude"
        var_lon = ds.createVariable("longitude", "f4", (lon_dim,))
        var_lon.units = "degrees_east"
        var_lon.long_name = "longitude"

        var_phys = ds.createVariable(phys_name, "f4", (lat_dim, lon_dim),
                                     fill_value=np.float32(INVALID_OUTPUT))
        var_phys.units = phys_unit
        var_phys.long_name = phys_std

        var_st = ds.createVariable("start_time", "f8", ())
        var_st.units = _TIME_UNIT
        var_st.standard_name = "time"
        var_st.long_name = "observation start time"
        var_et = ds.createVariable("end_time", "f8", ())
        var_et.units = _TIME_UNIT
        var_et.standard_name = "time"
        var_et.long_name = "observation end time"

        var_lat[:] = lat
        var_lon[:] = lon
        var_phys[:, :] = phys
        var_st[...] = np.float64(start_mjd)
        var_et[...] = np.float64(end_mjd)

        ds.title = title
        ds.institution = "MSC/JMA"
        ds.source = f"{h0.sat_name} satellite observation"
        ds.history = history
        setattr(ds, "Conventions", "CF-1.4")
    return out_path


# ══════════════════════════════════════════════════════════════════════════════
#  HIGH-LEVEL DRIVERS
# ══════════════════════════════════════════════════════════════════════════════
def convert_band_files(seg_files: List[Path], out_path: Path,
                       grid: GridSpec) -> Tuple[bool, Optional[str]]:
    """Convert explicit segment files (1 band) to one JMA NetCDF (C mode)."""
    t0 = perf_counter()
    try:
        phys, start_mjd, end_mjd, h0, hdrs = convert_band_to_grid(seg_files, grid)
        write_jma_netcdf(out_path, h0, grid, phys, start_mjd, end_mjd)
        valid = np.isfinite(phys) & (phys != INVALID_OUTPUT)
        n_valid = int(valid.sum())
        cov = n_valid / phys.size if phys.size else 0.0
        log.info(f"[DONE] {h0.sat_name} band-{h0.band_no:02d} "
                 f"grid={grid.width}x{grid.height} "
                 f"coverage={cov*100:.1f}%  ->  {out_path.name}  "
                 f"({out_path.stat().st_size/1e6:.2f} MB, "
                 f"{perf_counter()-t0:.1f}s)")
        return True, None
    except Exception as exc:
        log.error(f"[FAIL] {out_path.name}: {exc}")
        log.debug(traceback.format_exc())
        return False, str(exc)


def _detect_band(name: str) -> Optional[str]:
    m = re.search(r"_B(\d{2})_", name, re.IGNORECASE)
    return f"B{int(m.group(1)):02d}" if m else None


def _detect_timestamp(name: str) -> Optional[str]:
    m = re.search(r"_(\d{8})_(\d{4,6})_", name)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return None


def find_hsd_files(folder: Path) -> List[Path]:
    out = [p for p in folder.rglob("*")
           if p.is_file() and p.suffix.lower() in (".dat", ".bz2")]
    return sorted(out, key=lambda p: p.name)


def convert_folder(input_dir: Path, out_dir: Path, grid: GridSpec,
                   workers: int = 1,
                   auto_res: Optional[float] = None,
                   delete_sources: bool = False) -> Tuple[int, int]:
    """Folder mode: group .dat/.bz2 by (band, timestamp) and convert each band
    group to its own JMA NetCDF."""
    files = find_hsd_files(input_dir)
    if not files:
        log.error(f"[!] No .dat/.bz2 files in {input_dir}")
        return 0, 0

    groups: Dict[Tuple[str, str], List[Path]] = {}
    for f in files:
        band = _detect_band(f.name)
        if band is None:
            continue
        ts = _detect_timestamp(f.name) or input_dir.name
        groups.setdefault((band, ts), []).append(f)
    if not groups:
        log.error("[!] No filenames with _Bxx_ band tokens found")
        return 0, 0
    log.info(f"[+] {len(groups)} band/timestamp group(s) from {len(files)} files")

    ordered = sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    effective_grid: Optional[GridSpec] = None
    if grid is not None and (grid.width > 0 and grid.height > 0):
        effective_grid = grid

    results = []

    def _work(item):
        (band, ts), segs = item
        name0 = segs[0].name
        stem = name0.replace(".bz2", "").replace(".BZ2", "")
        stem = re.sub(r"[.][Dd][Aa][Tt]$", "", stem)
        stem = re.sub(r"_S\d{4}$", "", stem)
        if stem.startswith("HS_"):
            stem = "NC_" + stem[3:]
        out_nc = out_dir / f"{stem}.nc"
        h0_try = HSDFile(segs[0])
        use_grid = effective_grid
        if use_grid is None:
            use_grid = _fill_grid_from_projection(h0_try, auto_res or 0.02)
        h0_try.release()
        ok, err = convert_band_files(segs, out_nc, use_grid)
        if ok and delete_sources:
            removed = 0
            for sf in segs:
                try:
                    if sf.exists():
                        sf.unlink()
                        removed += 1
                except Exception as exc:
                    log.warning(f"[!] Could not delete {sf.name}: {exc}")
            log.info(f"[~] Removed {removed}/{len(segs)} source file(s) for {band} {ts}")
        return band, ts, out_nc, (ok, err)

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_work, ordered))
    else:
        for item in ordered:
            results.append(_work(item))

    ok = sum(1 for r in results if r[3][0])
    fail = len(results) - ok
    for band, ts, out_nc, (success, err) in results:
        status = "OK" if success else "FAIL"
        log.info(f"[{status}] {band} {ts} -> {out_nc.name}")
    return ok, fail


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════
def _clamp_param(args) -> GridSpec:
    width = args.width if args.width is not None else DEFAULT_WIDTH
    height = args.height if args.height is not None else DEFAULT_HEIGHT
    ltlon = args.lon if args.lon is not None else DEFAULT_LTLON
    ltlat = args.lat if args.lat is not None else DEFAULT_LTLAT
    dlon = args.dlon if args.dlon is not None else DEFAULT_DLON
    dlat = args.dlat if args.dlat is not None else DEFAULT_DLAT
    # C getArg() clamps
    if width < 10: width = 10
    if height < 10: height = 10
    if ltlat < -90.0 or ltlat > 90.0: ltlat = DEFAULT_LTLAT
    if ltlon < -180.0 or ltlon > 180.0: ltlon = DEFAULT_LTLON
    if dlat < 0.0 or dlat > 10.0: dlat = DEFAULT_DLAT
    if dlon < 0.0 or dlon > 10.0: dlon = DEFAULT_DLON
    return GridSpec(int(width), int(height), ltlon, ltlat, dlon, dlat)


def _make_parser():
    import argparse
    p = argparse.ArgumentParser(
        description="Cyclone - Himawari HSD .DAT/.bz2 -> JMA/MSC NetCDF "
                    "(regular lat/lon grid, CF-1.4 self-contained)")
    p.add_argument("-i", "--inputs", action="append", default=[],
                   help="Input .DAT/.bz2 segment file(s). Repeatable. If a "
                        "single argument is a directory, folder mode is used.")
    p.add_argument("-o", "--output", default=None,
                   help="Output .nc file (single mode). In folder mode: "
                        "output directory (default: input directory).")
    p.add_argument("-width", "--width", type=int, default=None,
                   help=f"Output pixel number (default {DEFAULT_WIDTH})")
    p.add_argument("-height", "--height", type=int, default=None,
                   help=f"Output line number (default {DEFAULT_HEIGHT})")
    p.add_argument("-lon", "--lon", type=float, default=None,
                   help=f"Left top longitude (default {DEFAULT_LTLON})")
    p.add_argument("-lat", "--lat", type=float, default=None,
                   help=f"Left top latitude (default {DEFAULT_LTLAT})")
    p.add_argument("-dlon", "--dlon", type=float, default=None,
                   help=f"Spatial resolution, longitude (default {DEFAULT_DLON})")
    p.add_argument("-dlat", "--dlat", type=float, default=None,
                   help=f"Spatial resolution, latitude (default {DEFAULT_DLAT})")
    p.add_argument("--res", type=float, default=None,
                   help="Folder mode: auto-derive full-disk grid at this "
                        "resolution in degrees (any of -width/-height/-lat/-lon "
                        "-dlat/-dlon overrides auto-arrange)")
    p.add_argument("--workers", type=int, default=1,
                   help="Folder mode: parallel band conversions")
    p.add_argument("--delete-sources", action="store_true",
                   help="Folder mode: delete .dat/.bz2 source files for bands "
                        "that were converted successfully (kept on failure)")
    p.add_argument("--profile", action="store_true",
                   help="Print per-stage timing")
    return p


def main(argv=None):
    args = _make_parser().parse_args(argv)

    print("=" * 70)
    print("CYCLONE - HIMAWARI HSD -> JMA/MSC NetCDF (regular lat/lon grid)")
    print("=" * 70)

    if not args.inputs:
        print("error : no input files. See --help.")
        return 1

    single_input = Path(args.inputs[0])
    is_folder = single_input.is_dir()

    if is_folder:
        if len(args.inputs) > 1:
            print("error : folder mode takes exactly one -i directory")
            return 1
        explicit = any(x is not None for x in
                       (args.width, args.height, args.lat, args.lon,
                        args.dlat, args.dlon))
        grid = _clamp_param(args) if explicit else None
        out_dir = Path(args.output) if args.output else single_input
        if args.output and not out_dir.is_dir():
            out_dir.mkdir(parents=True, exist_ok=True)
        ok, fail = convert_folder(single_input, out_dir, grid,
                                  workers=args.workers, auto_res=args.res,
                                  delete_sources=args.delete_sources)
        print("\n" + "=" * 70)
        print(f"SUMMARY: {ok} bands converted, {fail} failed")
        print("=" * 70)
        print("STATISTICS_OUTPUT:")
        print(f"Processed: {ok}")
        print(f"Failed: {fail}")
        return 0 if fail == 0 else 1

    if not args.output:
        print("error : -o <OutFile> required when -i is a file")
        return 1
    seg_files = [Path(x) for x in args.inputs]
    for f in seg_files:
        if not f.exists():
            print(f"error : can not open [{f}]")
            return 1
    out_path = Path(args.output)
    grid = _clamp_param(args)
    ok, err = convert_band_files(seg_files, out_path, grid)
    print("\n" + "=" * 70)
    print(f"SUMMARY: {'OK' if ok else 'FAILED'}"
          + (f"  ({err})" if err else ""))
    print("=" * 70)
    print("STATISTICS_OUTPUT:")
    print(f"Processed: {1 if ok else 0}")
    print(f"Failed: {0 if ok else 1}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())