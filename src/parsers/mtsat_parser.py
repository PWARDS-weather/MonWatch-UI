import re
import math
from functools import lru_cache
import numpy as np
import xarray as xr
from pathlib import Path
from pyproj import CRS
from rasterio.transform import Affine


# =============================================================================
# MTSAT-1R / MTSAT-2 (Himawari-6/7) NetCDF reader
#
# JMA MSC reprocessed products look like:
#   MT1_YYYYMMDD_HHMM_LLL_BBB_SATXXX_CHALL.nc      (single file, all channels)
# with band variables `*_count` (raw DN) plus embedded 1024-entry
# calibration lookup tables (`*_albedo_table` / `*_temperature_table`)
# and 1-D lat/lon grids.
#
# Channels -> internal band names (closest AHI-like spectral slot so shared
# products such as Infrared/Sandwich/False Color work):
#   vis  0.7 um   -> B03   (AHI B03 0.64 um red-ish VIS)
#   ir4  3.7 um   -> B07   (AHI B07 3.9 um shortwave IR)
#   ir3  6.8 um   -> B08   (AHI B08 6.2 um water vapour)
#   ir1 10.8 um   -> B13   (AHI B13 10.4 um thermal IR)
#   ir2 12.0 um   -> B14   (AHI B14 11.2 um thermal IR)
# =============================================================================

_FILE_NAME_RE = re.compile(
    r'MT\d_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_',
    re.IGNORECASE
)

MTSAT_BAND_MAP = {
    "vis": "B03",
    "ir4": "B07",
    "ir3": "B08",
    "ir1": "B13",
    "ir2": "B14",
}

INV_MTSAT_BAND_MAP = {v: k for k, v in MTSAT_BAND_MAP.items()}

# band -> (count variable, grid prefix, table variable, invalid-count attr)
_BAND_META = {
    "B03": ("vis_count",     "vis", "vis_albedo_table",     "vis_invalid_count"),
    "B07": ("ir4_count",     "ir",  "ir4_temperature_table", "ir4_invalid_count"),
    "B08": ("ir3_count",     "ir",  "ir3_temperature_table", "ir3_invalid_count"),
    "B13": ("ir1_count",     "ir",  "ir1_temperature_table", "ir1_invalid_count"),
    "B14": ("ir2_count",     "ir",  "ir2_temperature_table", "ir2_invalid_count"),
}

# MTSAT platform ids (JMA satellite_id codes used at MSC)
_MTSAT_IDS = {150, 151, 152, 170, 171, 172, 250}

_SAT_LABELS = {
    171: ("mtsat-1r", "MTSAT-1R"),
    172: ("mtsat-2", "MTSAT-2"),
}
_SAT_LABEL_FALLBACK = ("mtsat", "MTSAT")


def _read_satellite_name(ds):
    """Read the satellite_name from attrs or the char coordinate."""
    try:
        v = ds.attrs.get("satellite_name")
        if isinstance(v, bytes):
            return v.decode("utf-8", errors="replace").strip()
        if v is not None:
            return str(v).strip()
    except Exception:
        pass
    try:
        coord = ds.get("satellite_name")
        if coord is not None:
            vals = np.asarray(coord.values).ravel()
            raw = b"".join(
                (int(x) if isinstance(x, np.generic) else x) for x in vals
                if isinstance(x, (int, np.integer))
            )
            if isinstance(raw, (bytes, bytearray)):
                return bytes(raw).decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return ""


@lru_cache(maxsize=256)
def is_mtsat_file(nc_path):
    p = Path(nc_path)
    if _FILE_NAME_RE.search(p.name):
        return True
    try:
        from src.core.nc_lock import netcdf_read_lock
        with netcdf_read_lock:
            with xr.open_dataset(p, engine="netcdf4") as ds:
                name = _read_satellite_name(ds).upper()
                if "MTSAT" in name:
                    return True
                sid = ds.get("satellite_id")
                if sid is not None:
                    try:
                        if int(sid.values.ravel()[0]) in _MTSAT_IDS:
                            return True
                    except Exception:
                        pass
                if {k for k in ("vis_count", "ir1_count", "ir2_count",
                                "ir3_count", "ir4_count")} & set(ds.data_vars):
                    if ds.get("vis_albedo_table") is not None or \
                            ds.get("ir1_temperature_table") is not None:
                        return True
    except Exception:
        pass
    return False


def parse_mtsat_metadata(nc_path):
    p = Path(nc_path)
    result = {
        "satellite": "mtsat",
        "instrument": "IMAGER",
        "band_name": None,
        "timestamp": None,
        "satellite_lon": 140.0,
        "satellite_id": None,
    }
    m = _FILE_NAME_RE.search(p.name)
    if m:
        result["timestamp"] = f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}"
    try:
        with xr.open_dataset(p, engine="netcdf4") as ds:
            if "data_start_time" in ds.attrs:
                ts = ds.attrs["data_start_time"]
                if ts and len(ts) >= 5:
                    result["timestamp"] = (f"{int(ts[0]):04d}_{int(ts[1]):02d}_"
                                           f"{int(ts[2]):02d}_{int(ts[3]):02d}{int(ts[4]):02d}")
            sid = ds.get("satellite_id")
            if sid is not None:
                try:
                    sid_val = int(sid.values.ravel()[0])
                    result["satellite_id"] = sid_val
                    if sid_val in _SAT_LABELS:
                        result["satellite"] = _SAT_LABELS[sid_val][0]
                except Exception:
                    pass
            ssp = ds.get("nominal_ssp_longitude")
            if ssp is not None:
                try:
                    result["satellite_lon"] = float(ssp.values.ravel()[0])
                except Exception:
                    pass
    except Exception:
        pass
    if result["satellite_id"] in _SAT_LABELS:
        result["sat_label"] = _SAT_LABELS[result["satellite_id"]][1]
    else:
        result["sat_label"] = _SAT_LABEL_FALLBACK[1]
    return result


def extract_mtsat_bands(nc_files):
    """Map the channels embedded in the (typically single) MTSAT NetCDF file.

    Returns ``{band_name: Path}`` where every channel lives in the same file.
    """
    band_map = {}
    for fp in nc_files:
        if not is_mtsat_file(fp):
            continue
        try:
            with xr.open_dataset(fp, engine="netcdf4", mask_and_scale=False) as ds:
                present = set(ds.data_vars)
            meta = _BAND_META
            for band, (count_var, _, _, _) in meta.items():
                if count_var in present:
                    band_map[band] = Path(fp)
        except Exception:
            pass
    return band_map


def extract_mtsat_crs(nc_path, band=None):
    """Return (CRS, Affine geotransform) for an MTSAT file.

    The MSC/MSIAL projection metadata embedded in the file declares a
    geographic (lat/lon) projection: ``nPrjMode=5`` with a standard point
    and pixel sizes given in degrees (``fStdLon/fStdLat`` + ``fXsize/
    fYsize``). So the imagery is rectilinear in longitude/latitude and we
    expose it as EPSG:4326 with a degree-based geotransform (matching the
    app's plate-carree / equirectangular pathway). Pass *band* to pick that
    channel's grid resolution; otherwise the IR grid is used.
    """
    nc_path = Path(nc_path)
    try:
        with xr.open_dataset(nc_path, engine="netcdf4", mask_and_scale=False) as ds:
            grid_prefix = "ir"
            if band in _BAND_META:
                grid_prefix = _BAND_META[band][1]
            lon = ds[f"{grid_prefix}_longitude"].values
            lat = ds[f"{grid_prefix}_latitude"].values
            if len(lon) < 2 or len(lat) < 2:
                return CRS.from_epsg(4326), None
            res_lon = float(abs(lon[1] - lon[0]))
            res_lat = float(abs(lat[1] - lat[0]))
            lon_min = float(lon.min())
            lat_max = float(lat.max())
            gt = Affine(res_lon, 0.0, lon_min, 0.0, -res_lat, lat_max)
            return CRS.from_epsg(4326), gt
    except Exception:
        return None, None


def extract_mtsat_extent(nc_path, band=None):
    """Return the (lon_min, lat_min, lon_max, lat_max) box for the file's grid."""
    nc_path = Path(nc_path)
    try:
        with xr.open_dataset(nc_path, engine="netcdf4", mask_and_scale=False) as ds:
            grid_prefix = "ir"
            if band in _BAND_META:
                grid_prefix = _BAND_META[band][1]
            lon = ds[f"{grid_prefix}_longitude"].values
            lat = ds[f"{grid_prefix}_latitude"].values
            return (float(lon.min()), float(lat.min()),
                    float(lon.max()), float(lat.max()))
    except Exception:
        return None


def read_mtsat_band_data(nc_path, band, max_px=0):
    """Read and calibrate one MTSAT channel.

    Returns ``(arr, crs, geotransform)``. ``arr`` is float32:
      - VIS  -> albedo (0..1)  from the embedded albedo table
      - IR   -> brightness temperature (Kelvin) from the temperature table
    Invalid counts (``-1`` / ``-32768`` / out of table range) become NaN.
    """
    nc_path = Path(nc_path)
    if band not in _BAND_META:
        return None, None, None
    count_var, grid_prefix, table_var, invalid_var = _BAND_META[band]
    try:
        from src.core.nc_lock import netcdf_read_lock
        with netcdf_read_lock:
            with xr.open_dataset(nc_path, mask_and_scale=False, engine="netcdf4") as ds:
                if count_var not in ds or table_var not in ds:
                    return None, None, None
                counts = ds[count_var].values
                h, w = counts.shape
                if max_px > 0 and max(h, w) > max_px:
                    step = max(1, math.ceil(max(h, w) / max_px))
                    counts = counts[::step, ::step]
                table = np.asarray(ds[table_var].values, dtype=np.float32)
                n_levels = int(table.size)
                arr = np.full(counts.shape, np.nan, dtype=np.float32)
                valid = (counts >= 0) & (counts < n_levels)
                arr[valid] = table[counts[valid]]
                invalid_flag = None
                if invalid_var in ds.attrs:
                    try:
                        invalid_flag = int(ds.attrs[invalid_var])
                    except Exception:
                        invalid_flag = None
                if invalid_flag is None:
                    invalid_flag = -1
                arr[counts == invalid_flag] = np.nan
                arr[counts == -32768] = np.nan
                arr = arr.astype(np.float32)
            crs, gt = extract_mtsat_crs(nc_path, band)
            return arr, crs, gt
    except Exception:
        return None, None, None