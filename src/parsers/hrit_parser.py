import re
import math
import struct
from functools import lru_cache
import numpy as np
from pathlib import Path
from pyproj import CRS
from rasterio.transform import Affine


# =============================================================================
# JMA MSC HRIT/MTSAT raw binary reader (as-downloaded from MSC GDS)
#
# Files look like:
#   HRIT_MTSAT1_YYYYMMDD_HHMM_DK01IR1        (IR1 IR 10.8 um)
#   HRIT_MTSAT1_YYYYMMDD_HHMM_DK01IR2        (IR2 IR 12.0 um)
#   HRIT_MTSAT1_YYYYMMDD_HHMM_DK01IR3        (IR3 WV  6.8 um)
#   HRIT_MTSAT1_YYYYMMDD_HHMM_DK01IR4        (IR4 SW  3.7 um)
#   HRIT_MTSAT1_YYYYMMDD_HHMM_DK01VIS        (VIS 0.7 um)
#
# Structure: big-endian header written as a sequence of records, each
#   [1 byte record type][2 byte BE record length], followed by a raw
#   big-endian uint16 pixel grid.  Total_Header_Length is a BE uint32 at
#   bytes 4:8 of the file.  The navigation record declares GEOS(140.00)
#   with CFAC/LFAC/COFF/LOFF; the data-function/halftone record carries a
#   piecewise-linear calibration table (KELVIN for IR, ALBEDO(%) for VIS).
#
# Channels -> internal band names (closest AHI-like spectral slot so shared
# products such as Infrared/Sandwich/False Color work):
#   vis  0.7 um   -> B03   (AHI B03 0.64 um red-ish VIS)
#   ir4  3.7 um   -> B07   (AHI B07 3.9 um shortwave IR)
#   ir3  6.8 um   -> B08   (AHI B08 6.2 um water vapour)
#   ir1 10.8 um   -> B13   (AHI B13 10.4 um thermal IR)
#   ir2 12.0 um   -> B14   (AHI B14 11.2 um thermal IR)
# =============================================================================

# MTSAT-1R/2: geostationary orbit at 140E.  All imagery is full-disk.
_MTSAT_SEMI_MAJOR = 42164000.0        # nominal satellite-to-Earth-centre
_MTSAT_EARTH_REQ = 6378137.0          # GRS80 equatorial radius (m)
_MTSAT_EARTH_RPOL = 6356752.3         # GRS80 polar radius (m)

_HRIT_FILE_RE = re.compile(
    r'HRIT_MTSAT1_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_DK01(IR1|IR2|IR3|IR4|VIS)$',
    re.IGNORECASE
)

HRIT_BAND_MAP = {
    "IR1": "B13",
    "IR2": "B14",
    "IR3": "B08",
    "IR4": "B07",
    "VIS": "B03",
}

IR_BANDS = {"B07", "B08", "B13", "B14"}

# JMA standard scaling: CFAC/LFAC convert pixel offsets into scanning angles.
# rad/pixel = (2^16 / CFAC) * (pi / 180).  COFF/LOFF are the centre pixel.
_STD_DEG_PER_PX_EXP = 16.0


@lru_cache(maxsize=256)
def is_hrit_file(nc_path):
    p = Path(nc_path)
    if _HRIT_FILE_RE.search(p.name):
        return True
    if p.name.upper().startswith("HRIT_MTSAT"):
        return True
    return False


def _channel_from_name(name):
    m = _HRIT_FILE_RE.search(name)
    if m:
        return m.group(6).upper()
    um = re.search(r'(IR1|IR2|IR3|IR4|VIS)', name, re.IGNORECASE)
    if um:
        return um.group(1).upper()
    return None


def _iter_records(raw: bytes):
    """Yield (record_type, payload_bytes) for each numbered record.

    Records are [1B type][1B spare][2B BE length][payload]; walk is bounded
    by the file's declared Total_Header_Length (BE u32 at bytes 4:8).
    """
    if len(raw) < 8:
        return
    hdr_len = struct.unpack(">I", raw[4:8])[0]
    if hdr_len <= 0 or hdr_len > len(raw):
        hdr_len = len(raw)
    off = 0
    while off + 3 <= hdr_len:
        rec_type = raw[off]
        rec_len = struct.unpack(">H", raw[off + 1:off + 3])[0]
        if rec_len < 3 or off + rec_len > hdr_len:
            break
        yield rec_type, raw[off + 3:off + rec_len]
        off += rec_len


def _parse_navigation(payload: bytes):
    """Parse the type-2 navigation record.

    Declares 'GEOS(<lon>)' followed by `ProjectionCoefficients`:
    CFAC, LFAC (int32 BE), COFF, LOFF (int32 BE).
    """
    if len(payload) < 20:
        return None
    txt = payload.decode("ascii", errors="ignore")
    lon0 = 140.0
    m = re.search(r'GEOS\(([-0-9.]+)\)', txt)
    if m:
        try:
            lon0 = float(m.group(1))
        except ValueError:
            pass
    # ProjectionCoefficients are the trailing 4 int32 (short header + padded
    # ASCII header precedes them)
    nums = payload[-16:]
    cfac, lfac, coff, loff = struct.unpack(">4i", nums)
    return {
        "projection": "GEOS",
        "lon0": lon0,
        "cfac": cfac,
        "lfac": lfac,
        "coff": coff,
        "loff": loff,
    }


def _parse_structure(payload: bytes):
    """Parse the type-1 structure record (nc / nl / compression / nb)."""
    txt = payload.decode("ascii", errors="ignore")
    nc = nl = None
    comp = 0
    nb = 16
    m = re.search(r'NC:=(\d+)', txt)
    if m:
        nc = int(m.group(1))
    m = re.search(r'NL:=(\d+)', txt)
    if m:
        nl = int(m.group(1))
    m = re.search(r'COMP:(\d)', txt)
    if m:
        comp = int(m.group(1))
    m = re.search(r'NB:=(\d+)', txt)
    if m:
        nb = int(m.group(1))
    # fallback: raw u16 BE values directly after the ASCII prefix (probe-verified)
    if nc is None and len(payload) >= 13:
        nc = struct.unpack(">H", payload[9:11])[0]
    if nl is None and len(payload) >= 13:
        nl = struct.unpack(">H", payload[11:13])[0]
    return {"nc": nc, "nl": nl, "comp": comp, "nb": nb}


def _parse_halftone(payload: bytes):
    """Parse the type-3 data-function record.

    Returns ``{_unit, _name, pairs: [(x0, y0), ...], unit}`` where *_unit is
    e.g. 'KELVIN' or 'ALBEDO(%)', and pairs are the piecewise-linear
    calibation breakpoints `NN:=value`.
    """
    txt = payload.decode("ascii", errors="ignore")
    unit = None
    name = None
    m = re.search(r'_UNIT:=([A-Za-z%()0-9]+)', txt)
    if m:
        unit = m.group(1)
    m = re.search(r'_NAME:=(\w+)', txt)
    if m:
        name = m.group(1)
    pairs = []
    for mm in re.finditer(r'(\d+):=([-0-9.]+)', txt):
        pairs.append((int(mm.group(1)), float(mm.group(2))))
    return {"_unit": unit, "_name": name, "pairs": pairs, "unit": unit}


def _parse_annotation(payload: bytes) -> str:
    return payload.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def parse_hrit_metadata(nc_path):
    p = Path(nc_path)
    result = {
        "satellite": "mtsat",
        "instrument": "IMAGER",
        "band_name": None,
        "timestamp": None,
        "satellite_lon": 140.0,
    }
    m = _HRIT_FILE_RE.search(p.name)
    if m:
        result["timestamp"] = f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}"
        result["band_name"] = HRIT_BAND_MAP.get(m.group(6))
    return result


def extract_hrit_bands(nc_files):
    """Map each HRIT file onto an internal band name (B03/B07/B08/B13/B14)."""
    band_map = {}
    for fp in nc_files:
        p = Path(fp)
        ch = _channel_from_name(p.name)
        if ch and ch in HRIT_BAND_MAP:
            band_map[HRIT_BAND_MAP[ch]] = p
    return band_map


def _read_header_block(nc_path):
    p = Path(nc_path)
    hdr_len = 0
    with open(p, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            return None
        hdr_len = struct.unpack(">I", head[4:8])[0]
        if hdr_len <= 0 or hdr_len > 1_000_000:
            return None
        f.seek(0)
        raw_hdr = f.read(hdr_len)
    return raw_hdr


def _grid_shape(file_size, hdr_len, raw_hdr=None):
    """Return (nlines, ncols) for the raw grid, or None if unknown."""
    n_pix = (file_size - hdr_len) // 2
    if n_pix <= 0:
        return None
    nc = nl = None
    if raw_hdr:
        for rec_type, payload in _iter_records(raw_hdr):
            if rec_type == 1:
                st = _parse_structure(payload)
                nc = st.get("nc")
                nl = st.get("nl")
                break
    if nc and nl and nc * nl == n_pix:
        return (nl, nc)
    side = int(round(math.sqrt(n_pix)))
    if side * side == n_pix:
        return (side, side)
    return None


def _read_grid(nc_path, max_px=0, dtype=np.float32):
    """Read the raw BE uint16 pixel grid from a HRIT file.

    Data begins right after Total_Header_Length (BE u32 at bytes 4:8).
    When *max_px* is given the grid is strided down during the read so only a
    small fraction of the file is touched (large VIS grids stay fast).
    Returns ``(data, (step_y, step_x))`` or ``(None, None)``.
    """
    p = Path(nc_path)
    size = p.stat().st_size
    with open(p, "rb") as f:
        head = f.read(8)
        if len(head) < 8:
            return None, None
        hdr_len = struct.unpack(">I", head[4:8])[0]
        if hdr_len <= 0 or hdr_len > size:
            return None, None
        raw_hdr = f.read(hdr_len - 8)
    full_raw = head + raw_hdr if hdr_len >= 8 else None
    shape = _grid_shape(size, hdr_len, full_raw)
    if shape is None:
        return None, None
    nl, nc = shape

    mm = np.memmap(p, dtype=">u2", mode="r", offset=hdr_len, shape=(nl, nc))
    try:
        if max_px > 0 and max(nl, nc) > max_px:
            step_y = max(1, math.ceil(nl / max_px))
            step_x = max(1, math.ceil(nc / max_px))
            data = mm[::step_y, ::step_x].astype(dtype)
        else:
            step_y = step_x = 1
            data = mm.astype(dtype)
    finally:
        del mm
    return data, (step_y, step_x)


@lru_cache(maxsize=128)
def _get_enum_tables(nc_path):
    """Extract (grid, unit, pairs) or None from a HRIT file's halftone record.

    Returns ``{"unit": str, "table": np.ndarray(float32, len 65536)}`` where
    table[x] holds the calibrated value for count x (NaN for impossible
    counts).  Breakpoints are linearly interpolated.
    """
    p = Path(nc_path)
    raw_hdr = _read_header_block(p)
    if raw_hdr is None:
        return None
    for rec_type, payload in _iter_records(raw_hdr):
        if rec_type == 3:
            ht = _parse_halftone(payload)
            pairs = ht.get("pairs") or []
            unit = ht.get("unit") or ""
            if not pairs:
                return {"unit": unit, "table": None}
            xs = np.array([x for x, _ in pairs], dtype=np.float32)
            ys = np.array([y for _, y in pairs], dtype=np.float32)
            # build a 65536-entry LUT (counts are uint16)
            table = np.full(65536, np.nan, dtype=np.float32)
            # fill up to the last declared breakpoint
            max_x = int(xs[-1])
            if max_x >= 65536:
                max_x = 65535
            table[: max_x + 1] = np.interp(
                np.arange(max_x + 1, dtype=np.float32), xs, ys
            )
            return {"unit": unit, "table": table}
    return None


def extract_hrit_crs(nc_path, band=None):
    """Return (CRS, Affine geotransform) for a HRIT file.

    The satellite is an MTSAT-1R at GEOS ~140E; imagery is stored in the
    satellite's native geostationary projection (no embedded lat/lon grid),
    so we expose it as the GEOS CRS + a matching geotransform (mirroring the
    GK-2A pathway).  Resolution comes from CFAC/LFAC.
    """
    nc_path = Path(nc_path)
    raw_hdr = _read_header_block(nc_path)
    if raw_hdr is None:
        return None, None
    nav = None
    nc = nl = None
    for rec_type, payload in _iter_records(raw_hdr):
        if rec_type == 1:
            st = _parse_structure(payload)
            nc = st.get("nc")
            nl = st.get("nl")
        elif rec_type == 2 and nav is None:
            nav = _parse_navigation(payload)
    if nav is None:
        return None, None

    lon0 = nav["lon0"]
    cfac = nav.get("cfac") or 0
    coff = nav.get("coff") or 0
    loff = nav.get("loff") or 0

    if not nc:
        nc = 11000 if "VIS" in Path(nc_path).name.upper() else 2750
    if not nl:
        nl = nc

    deg_per_px = (2 ** _STD_DEG_PER_PX_EXP) / float(cfac) if cfac else 0.006288
    rad_per_px = math.radians(deg_per_px)
    h = _MTSAT_SEMI_MAJOR - _MTSAT_EARTH_REQ

    p4 = (
        f"+proj=geos +lon_0={lon0} +h={h} +x_0=0 +y_0=0 "
        f"+a={_MTSAT_EARTH_REQ} +b={_MTSAT_EARTH_RPOL} +sweep=x +units=m +no_defs"
    )
    crs = CRS.from_proj4(p4)

    res = h * rad_per_px
    x_ul = (0 - coff) * res
    res_y = -res                        # row 0 is the top (north)
    y_ul = (0 - loff) * res_y           # = +loff * res
    # affine: column -> +x, row -> +y (north-up); geos origin at subpoint.
    # Return a real rasterio.Affine so downstream consumers that use
    # Affine-style access (gt.c, ~gt, gt*(x,y)) work.
    from rasterio.transform import Affine
    gt = Affine(res, 0.0, x_ul, 0.0, res_y, y_ul)
    return crs, gt


def extract_hrit_extent(nc_path, band=None):
    """Return the lon/lat box covering the full earth disk, or None."""
    crs, gt = extract_hrit_crs(nc_path, band)
    if crs is None or gt is None:
        return None
    from pyproj import Transformer
    fwd = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    res = gt[0]
    coff = -gt[2] / res
    loff = gt[5] / abs(gt[4])
    limb_rad = math.asin(_MTSAT_EARTH_REQ / _MTSAT_SEMI_MAJOR)
    rad_per_px = abs(res) / (_MTSAT_SEMI_MAJOR - _MTSAT_EARTH_REQ)
    limb_px = limb_rad / rad_per_px if rad_per_px else 0.5
    cx = coff * res
    cy = loff * res
    # sample the disk boundary ring for a robust lon/lat box (just inside the
    # limb, since the exact limb is singular in the GEOS transform)
    n = 24
    ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
    xs = cx + limb_px * res * 0.97 * np.cos(ang)
    ys = cy + limb_px * res * 0.97 * np.sin(ang)
    lons, lats = fwd.transform(list(xs), list(ys))
    lons = np.asarray(lons, dtype=np.float64)
    lats = np.asarray(lats, dtype=np.float64)
    valid = np.isfinite(lons) & np.isfinite(lats)
    if not valid.any():
        return None
    return (float(lons[valid].min()), float(lats[valid].min()),
            float(lons[valid].max()), float(lats[valid].max()))


def read_hrit_band_data(nc_path, band, max_px=0):
    """Read and calibrate one HRIT channel.

    Returns ``(arr, crs, geotransform)``.  ``arr`` is float32:
      - VIS  -> albedo (%)     (0..100) from the HALFTONE ALBEDO table
      - IR   -> brightness temperature (Kelvin) from the KELVIN table
    Off-disk / invalid counts become NaN.
    """
    nc_path = Path(nc_path)
    grid, (step_y, step_x) = _read_grid(nc_path, max_px=max_px)
    if grid is None:
        return None, None, None

    crs, gt = extract_hrit_crs(nc_path, band)

    # Mask off-disk counts at NATIVE resolution so decimation does not smear
    # space values into the disk
    if crs is not None and gt is not None:
        try:
            off_disk = _make_off_disk_mask(grid.shape, gt, (step_y, step_x))
            grid = grid.copy()
            grid[off_disk] = -1
        except Exception:
            pass

    en = _get_enum_tables(nc_path)
    if en and en.get("table") is not None:
        t = en["table"]                       # float32 LUT of length 65536
        counts = grid.astype(np.int64)
        np.clip(counts, 0, 65535, out=counts)
        arr = t[counts]
        arr = arr.astype(np.float32)
    else:
        arr = grid.astype(np.float32)

    arr[grid < 0] = np.nan
    return arr, crs, gt


def _make_off_disk_mask(shape, gt, stride=(1, 1)):
    """Return a bool mask marking pixels outside the earth disk.

    Uses the GEOS geometry: pixel distance from the subpoint (COFF, LOFF)
    against the limb radius derived from the geotransform.  *stride* is the
    ``(step_y, step_x)`` used to decimate the grid; COFF/LOFF (native pixel
    units) are shifted into the decimated index space and the limb radius is
    scaled by the same factors.
    """
    step_y, step_x = stride
    h, w = shape[:2]
    coff = -gt[2] / gt[0] / step_x
    loff = gt[5] / abs(gt[4]) / step_y
    rr = np.sqrt((np.arange(w, dtype=np.float32)[None, :] - coff) ** 2 +
                 (np.arange(h, dtype=np.float32)[:, None] - loff) ** 2)
    rad_per_px = abs(gt[0]) / (_MTSAT_SEMI_MAJOR - _MTSAT_EARTH_REQ)
    limb_rad = math.asin(_MTSAT_EARTH_REQ / _MTSAT_SEMI_MAJOR)
    limb_px = limb_rad / rad_per_px
    limb_px_x = limb_px / step_x
    limb_px_y = limb_px / step_y
    xx = (np.arange(w, dtype=np.float32)[None, :] - coff) / (limb_px_x or 1.0)
    yy = (np.arange(h, dtype=np.float32)[:, None] - loff) / (limb_px_y or 1.0)
    return (xx * xx + yy * yy) > (1.02 * 1.02)