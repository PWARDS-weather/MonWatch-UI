import re
import numpy as np
import xarray as xr
from pathlib import Path
from pyproj import CRS
from rasterio.transform import Affine


# JAXA Himawari Standard Data filename pattern:
#   NC_H<sat>_<date>_<time>_R<res>_<area>.<pixels>_<lines>.nc
#   e.g.  NC_H09_20260707_1330_R21_FLDK.07001_06001.nc
#   7001 = pixel count (columns / longitude),  6001 = line count (rows / latitude)
_FILENAME_RE = re.compile(
    r'NC_H(\d{2})_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})_R\d{2}_[^.]+\.\d{5}_\d{5}\.nc$',
    re.IGNORECASE
)


def is_himawari_jaxa_nc(nc_path):
    """Detect whether *nc_path* is a JAXA Himawari Standard Data file."""
    p = Path(nc_path)
    if _FILENAME_RE.search(p.name):
        return True
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            dims = list(ds.dims)
            has_lat = "latitude" in dims
            has_lon = "longitude" in dims
            if not (has_lat and has_lon):
                return False
            has_albedo = any(v.startswith("albedo_") for v in ds.data_vars)
            has_tbb = any(v.startswith("tbb_") for v in ds.data_vars)
            if has_albedo or has_tbb:
                return True
    except Exception:
        pass
    return False


def _satellite_from_filename(name):
    m = re.search(r'NC_H(\d{2})_', str(name))
    return f"himawari{m.group(1)}" if m else "himawari9"


def _timestamp_from_filename(name):
    m = re.search(r'_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})', str(name))
    if m:
        return f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}"
    return None


def _read_band_ids(ds):
    """Return a list of integer band IDs present in the dataset."""
    if "band_id" in ds:
        ids = ds["band_id"].values
        if ids.ndim == 0:
            return [int(ids)]
        return [int(x) for x in ids]
    ids = set()
    for v in ds.data_vars:
        m = re.match(r'albedo_(\d+)', v)
        if m:
            ids.add(int(m.group(1)))
    for v in ds.data_vars:
        m = re.match(r'tbb_(\d+)', v)
        if m:
            ids.add(int(m.group(1)))
    return sorted(ids)


def parse_himawari_jaxa_metadata(nc_path):
    p = Path(nc_path)
    result = {"satellite": _satellite_from_filename(p.name),
              "band_name": None,
              "timestamp": _timestamp_from_filename(p.name)}
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            sat = str(ds.attrs.get("satellite_name", "")).lower()
            if "himawari-9" in sat or "himawari 9" in sat:
                result["satellite"] = "himawari9"
            elif "himawari-8" in sat or "himawari 8" in sat:
                result["satellite"] = "himawari8"
            if "time_coverage_start" in ds.attrs:
                ts = str(ds.attrs["time_coverage_start"])
                m = re.search(r'(\d{4})[_-]?(\d{2})[_-]?(\d{2})[T ]?(\d{2}):?(\d{2})', ts)
                if m:
                    result["timestamp"] = f"{m.group(1)}_{m.group(2)}_{m.group(3)}_{m.group(4)}{m.group(5)}"
            band_ids = _read_band_ids(ds)
            if band_ids:
                result["band_name"] = f"B{band_ids[0]:02d}"
    except Exception:
        pass
    return result


def extract_himawari_jaxa_bands(nc_files):
    """Map band names to file paths.

    ALL bands are in a single file — this returns ``{B01: path, B02: path, …}``
    with every band pointing to the same file.
    """
    if not nc_files:
        return {}
    fp = Path(nc_files[0])
    try:
        with xr.open_dataset(fp, engine="netcdf4") as ds:
            band_ids = _read_band_ids(ds)
    except Exception:
        return {}
    return {f"B{bid:02d}": fp for bid in band_ids}


def read_himawari_jaxa_band_data(nc_path, band):
    """Read *band* data from a JAXA-format Himawari file.

    Returns ``(data, crs_dict, geotransform_list)``.
    Band naming follows ``B01`` … ``B16``.

    - Solar bands (1-6): variable ``albedo_XX``, scale 0.0001, offset 0
    - TIR bands (7-16):  variable ``tbb_XX``, scale 0.01, offset 273.15
    - Missing value: int16 -32768 → NaN
    - Data grid shape is ``(lat, lon)`` = ``(6001, 7001)`` for FD 0.02°
    """
    nc_path = Path(nc_path)
    m = re.match(r'B(\d{2})', band)
    if not m:
        return None, None, None
    band_num = int(m.group(1))
    try:
        with xr.open_dataset(nc_path, mask_and_scale=False, engine="netcdf4") as ds:
            if band_num <= 6:
                var_name = f"albedo_{m.group(1)}"
                scale = 0.0001
                offset = 0.0
            else:
                var_name = f"tbb_{m.group(1)}"
                scale = 0.01
                offset_val = 273.15
                # TBB might use 'offset' attribute name — prefer hardcoded
                scale = 0.01
                offset = 273.15

            if var_name not in ds:
                for v in ds.data_vars:
                    if ds[v].ndim >= 2:
                        var_name = v
                        scale = 1.0
                        offset = 0.0
                        break
            if var_name is None or var_name not in ds:
                return None, None, None

            arr = ds[var_name].values.astype(np.float32)

            # Missing value: int16 -32768
            missing_val = ds[var_name].attrs.get("_FillValue")
            if missing_val is not None:
                arr[arr == missing_val] = np.nan
            missing_val2 = ds[var_name].attrs.get("missing_value")
            if missing_val2 is not None:
                arr[arr == missing_val2] = np.nan
            # JAXA files use int16 with -32768 sentinel
            arr[arr == -32768.0] = np.nan

            if scale != 1.0 or offset != 0.0:
                arr = arr * np.float32(scale) + np.float32(offset)

            # Build CRS + geotransform from the documented grid
            crs = None
            gt = None
            try:
                sub_lon = 140.7
                if "geometry_params" in ds:
                    gp = ds["geometry_params"].values
                    if len(gp) > 0:
                        sub_lon = float(gp[0])
                crs = {
                    "longitude_of_projection_origin": sub_lon,
                    "perspective_point_height": 35785863.0,
                    "sweep_angle_axis": "x",
                }
            except Exception:
                pass

            # Build geotransform from lat/lon coords
            try:
                if "longitude" in ds and "latitude" in ds:
                    lon = ds["longitude"].values
                    lat = ds["latitude"].values
                    if len(lon) > 1 and len(lat) > 1:
                        res_x = float(abs(lon[1] - lon[0])) * 111320.0
                        res_y = float(abs(lat[1] - lat[0])) * 111320.0
                        gt = [res_x, 0.0, float(lon.min()) * 111320.0,
                              0.0, -res_y, float(lat.max()) * 111320.0]
            except Exception:
                pass

            if crs is None:
                crs = {"longitude_of_projection_origin": 140.7,
                       "perspective_point_height": 35785863.0,
                       "sweep_angle_axis": "x"}

            return arr, crs, gt
    except Exception:
        return None, None, None


def extract_himawari_jaxa_crs(nc_path):
    """Return ``(pyproj.CRS, Affine)`` from the file's grid definition."""
    nc_path = Path(nc_path)
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            sub_lon = 140.7
            if "geometry_params" in ds:
                gp = ds["geometry_params"].values
                if len(gp) > 0:
                    sub_lon = float(gp[0])
            sweep = "x"
            p4 = f"+proj=geos +lon_0={sub_lon} +h=35785863.0 +x_0=0 +y_0=0 +ellps=WGS84 +sweep={sweep} +units=m +no_defs"
            crs = CRS.from_proj4(p4)

            gt = None
            if "x" in ds and "y" in ds:
                xv = ds["x"].values
                yv = ds["y"].values
                if len(xv) > 1 and len(yv) > 1:
                    res_x = float(abs(xv[1] - xv[0]))
                    res_y = float(abs(yv[1] - yv[0]))
                    h = 35785863.0
                    abs_max = max(abs(xv.min()), abs(xv.max()),
                                  abs(yv.min()), abs(yv.max()))
                    if abs_max < 1.0:
                        res_x *= h
                        res_y *= h
                        gt = Affine(res_x, 0.0, float(xv.min()) * h,
                                    0.0, -res_y, float(yv.max()) * h)
                    else:
                        gt = Affine(res_x, 0.0, float(xv.min()),
                                    0.0, -res_y, float(yv.max()))
            elif "longitude" in ds and "latitude" in ds:
                lon = ds["longitude"].values
                lat = ds["latitude"].values
                if len(lon) > 1 and len(lat) > 1:
                    res_x = float(abs(lon[1] - lon[0])) * 111320.0
                    res_y = float(abs(lat[1] - lat[0])) * 111320.0
                    gt = Affine(res_x, 0.0, float(lon.min()) * 111320.0,
                                0.0, -res_y, float(lat.max()) * 111320.0)

            return crs, gt
    except Exception:
        return None, None
