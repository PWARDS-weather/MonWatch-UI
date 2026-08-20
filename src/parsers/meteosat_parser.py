import re
import numpy as np
import xarray as xr
from pathlib import Path
from pyproj import CRS
from rasterio.transform import Affine


# ---- MTG FCI (Meteosat Third Generation) ----
# L2:  M3_FCI_YYYYMMDD_HHMM_RFL001_FLDK.XXXXX_YYYYY.nc
# L3:  M3_FCI_YYYYMMDD_HHMM_1H_RFL001_FLDK.XXXXX_YYYYY.nc
_MTG_FCI_RE = re.compile(
    r'M(\d)_FCI_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})'
    r'(?:_1H)?_RFL\d{3}_FLDK\.\d{5}_\d{5}\.nc$',
    re.IGNORECASE
)

# ---- SEVIRI (legacy Meteosat) band map ----
METEOSAT_BAND_MAP = {
    "VIS006": "B01", "VIS008": "B02",
    "IR_016": "B03", "IR_039": "B04",
    "WV_062": "B05", "WV_073": "B06",
    "IR_087": "B07", "IR_097": "B08",
    "IR_108": "B09", "IR_120": "B10",
    "IR_134": "B11", "HRV": "B12",
}

INV_METEOSAT_BAND_MAP = {v: k for k, v in METEOSAT_BAND_MAP.items()}

# ---- MTG FCI L2/L3 PAR variable metadata (from spec) ----
MTG_FCI_VARS = {
    "TAOT_02":       {"scale": 0.005, "offset": 0.0,   "name": "Total atmospheric optical thickness band 2"},
    "TAAE":          {"scale": 0.001, "offset": -1.0,  "name": "Total atmospheric Angstrom exponent"},
    "PAR":           {"scale": 0.1,   "offset": 0.0,   "name": "Photosynthetically active radiation"},
    "SWR":           {"scale": 0.05,  "offset": 0.0,   "name": "Shortwave radiation"},
    "UVA":           {"scale": 0.005, "offset": 0.0,   "name": "Ultraviolet-A radiation"},
    "UVB":           {"scale": 0.001, "offset": 0.0,   "name": "Ultraviolet-B radiation"},
    "QA_flag":       {"scale": 1.0,   "offset": 0.0,   "name": "Quality assurance flag"},
    "Sample_number": {"scale": 1.0,   "offset": 0.0,   "name": "Number of samples in average"},
}

METEOSAT_IDS = {
    "meteosat-9": 0, "meteosat-10": 0, "meteosat-11": 0, "meteosat-12": 0,
    "meteosat 9": 0, "meteosat 10": 0, "meteosat 11": 0, "meteosat 12": 0,
    "msg1": 0, "msg2": 0, "msg3": 0, "msg4": 0,
}


def _detect_meteosat_id(nc_path, ds=None):
    name = Path(nc_path).stem.lower()
    for kw in ["meteosat-12", "meteosat 12", "mtg"]:
        if kw in name:
            return "meteosat12"
    for kw in ["meteosat-11", "meteosat 11", "msg4"]:
        if kw in name:
            return "meteosat11"
    for kw in ["meteosat-10", "meteosat 10", "msg3"]:
        if kw in name:
            return "meteosat10"
    for kw in ["meteosat-9", "meteosat 9", "msg2"]:
        if kw in name:
            return "meteosat9"
    if ds is not None:
        for attr_key in ["satellite_name", "satellite", "platform_name"]:
            val = str(ds.attrs.get(attr_key, "")).lower()
            for kw, sid in [("mtg", "meteosat12"), ("msg4", "meteosat11"),
                            ("msg3", "meteosat10"), ("msg2", "meteosat9"),
                            ("meteosat-12", "meteosat12"), ("meteosat-11", "meteosat11"),
                            ("meteosat-10", "meteosat10"), ("meteosat-9", "meteosat9")]:
                if kw in val:
                    return sid
    return "meteosat11"


def is_meteosat_file(nc_path):
    p = Path(nc_path)
    # MTG FCI format
    if _MTG_FCI_RE.search(p.name):
        return True
    # Legacy SEVIRI format
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            inst = str(ds.attrs.get("institution", "")).lower()
            if "eumetsat" in inst:
                return True
            conv = str(ds.attrs.get("Conventions", ""))
            if "cf" in conv.lower():
                has_lonlat = all(d in ds.dims for d in ["lon", "lat"]) or \
                             all(d in ds.dims for d in ["longitude", "latitude"])
                if has_lonlat:
                    return True
            sat = str(ds.attrs.get("satellite_name", "")).lower()
            if "meteosat" in sat or "msg" in sat:
                return True
    except Exception:
        pass
    name = p.stem.lower()
    for kw in ["meteosat", "msg", "seviri"]:
        if kw in name:
            return True
    return False


def is_mtg_fci_file(nc_path):
    """Check whether the file is an MTG FCI PAR product (L2 or L3)."""
    return bool(_MTG_FCI_RE.search(Path(nc_path).name))


def parse_meteosat_metadata(nc_path):
    p = Path(nc_path)
    result = {"satellite": "meteosat11", "instrument": "SEVIRI",
              "band_name": None, "timestamp": None, "satellite_lon": 0.0}

    # MTG FCI path
    m = _MTG_FCI_RE.search(p.name)
    if m:
        sat_num = m.group(1)
        result["satellite"] = f"meteosat{sat_num}"
        result["instrument"] = "FCI"
        result["timestamp"] = f"{m.group(2)}_{m.group(3)}_{m.group(4)}_{m.group(5)}{m.group(6)}"
        # MTG satellites near 0° longitude
        result["satellite_lon"] = 0.0
        try:
            with xr.open_dataset(nc_path, engine="netcdf4") as ds:
                if "time_coverage_start" in ds.attrs:
                    ts = str(ds.attrs["time_coverage_start"])
                    tm = re.search(r'(\d{4})[_-]?(\d{2})[_-]?(\d{2})[T ]?(\d{2}):?(\d{2})', ts)
                    if tm:
                        result["timestamp"] = f"{tm.group(1)}_{tm.group(2)}_{tm.group(3)}_{tm.group(4)}{tm.group(5)}"
                # Detect variables present
                present = [v for v in MTG_FCI_VARS if v in ds.data_vars]
                if present:
                    result["band_name"] = present[0]
        except Exception:
            pass
        return result

    # Legacy SEVIRI path
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            sid = _detect_meteosat_id(nc_path, ds)
            result["satellite"] = sid
            num = re.search(r'(\d+)', sid)
            if num:
                m_lon = {9: 0, 10: 0, 11: 0, 12: 0}
                result["satellite_lon"] = m_lon.get(int(num.group(1)), 0.0)
            if "time_coverage_start" in ds.attrs:
                ts = str(ds.attrs["time_coverage_start"])
                tm = re.search(r'(\d{4})[_-]?(\d{2})[_-]?(\d{2})[T ]?(\d{2}):?(\d{2})', ts)
                if tm:
                    result["timestamp"] = f"{tm.group(1)}_{tm.group(2)}_{tm.group(3)}_{tm.group(4)}{tm.group(5)}"
            name_lower = p.stem.lower()
            for code, band in METEOSAT_BAND_MAP.items():
                if code.lower() in name_lower:
                    result["band_name"] = band
                    result["band_code"] = code
                    break
            if result["band_name"] is None:
                for var in ds.data_vars:
                    vup = var.upper()
                    for code, band in METEOSAT_BAND_MAP.items():
                        if code in vup:
                            result["band_name"] = band
                            result["band_code"] = code
                            break
                    if result["band_name"]:
                        break
    except Exception:
        pass
    ts_match = re.search(r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})', p.stem)
    if ts_match and result.get("timestamp") is None:
        result["timestamp"] = f"{ts_match.group(1)}_{ts_match.group(2)}_{ts_match.group(3)}_{ts_match.group(4)}{ts_match.group(5)}"
    return result


def extract_meteosat_bands(nc_files):
    band_map = {}
    for fp in nc_files:
        # MTG FCI — single file, multiple variables treated as bands
        if _MTG_FCI_RE.search(Path(fp).name):
            try:
                with xr.open_dataset(fp, engine="netcdf4") as ds:
                    for v in ds.data_vars:
                        if v in MTG_FCI_VARS:
                            band_map[v] = Path(fp)
            except Exception:
                pass
            continue

        # Legacy SEVIRI — per-band file naming
        name = Path(fp).stem.upper()
        found = False
        for code, band in METEOSAT_BAND_MAP.items():
            if code in name:
                band_map[band] = Path(fp)
                found = True
                break
        if not found:
            try:
                with xr.open_dataset(fp, engine="netcdf4") as ds:
                    for var in ds.data_vars:
                        vup = var.upper()
                        for code, band in METEOSAT_BAND_MAP.items():
                            if code in vup:
                                band_map[band] = Path(fp)
                                found = True
                                break
                        if found:
                            break
            except Exception:
                pass
    return band_map


def read_meteosat_band_data(nc_path, band):
    """Read band data from a Meteosat file (MTG FCI or legacy SEVIRI).

    For MTG FCI, *band* is a variable name (e.g. ``PAR``, ``SWR``).
    For SEVIRI, *band* is an internal band name (e.g. ``B01``, ``B09``).
    Returns ``(data, crs_dict, geotransform)``.
    """
    nc_path = Path(nc_path)
    is_mtg = _MTG_FCI_RE.search(nc_path.name)
    try:
        with xr.open_dataset(nc_path, mask_and_scale=False, engine="netcdf4") as ds:
            var_name = None
            if is_mtg:
                # MTG FCI: band is the variable name itself (PAR, SWR, …)
                if band in ds:
                    var_name = band
                if var_name is None:
                    for v in ds.data_vars:
                        if ds[v].ndim >= 2:
                            var_name = v
                            break
            else:
                # Legacy SEVIRI
                code = INV_METEOSAT_BAND_MAP.get(band)
                if code:
                    for var in ds.data_vars:
                        if var.upper() == code:
                            var_name = var
                            break
                if var_name is None and band in ds:
                    var_name = band
                if var_name is None:
                    for var in ds.data_vars:
                        if ds[var].ndim >= 2:
                            var_name = var
                            break
            if var_name is None:
                return None, None, None

            arr = ds[var_name].values.astype(np.float32)

            # Missing value: int16 -32768 or _FillValue / missing_value
            fill = ds[var_name].attrs.get("_FillValue")
            if fill is not None:
                arr[arr == fill] = np.nan
            missing = ds[var_name].attrs.get("missing_value")
            if missing is not None:
                arr[arr == missing] = np.nan
            arr[arr == -32768.0] = np.nan

            # Apply scale/offset from attrs, with known spec defaults
            if is_mtg and var_name in MTG_FCI_VARS:
                spec = MTG_FCI_VARS[var_name]
                scale = ds[var_name].attrs.get("scale_factor", spec["scale"])
                offset = ds[var_name].attrs.get("add_offset", spec["offset"])
            else:
                scale = ds[var_name].attrs.get("scale_factor", 1.0)
                offset = ds[var_name].attrs.get("add_offset", 0.0)
            if scale != 1.0 or offset != 0.0:
                arr = arr * np.float32(scale) + np.float32(offset)

            # Build CRS / geotransform
            crs = None
            gt = None
            if is_mtg:
                # MTG FCI: regular lat/lon grid
                try:
                    if "longitude" in ds and "latitude" in ds:
                        lon = ds["longitude"].values
                        lat = ds["latitude"].values
                        if len(lon) > 1 and len(lat) > 1:
                            res_x = float(abs(lon[1] - lon[0])) * 111320.0
                            res_y = float(abs(lat[1] - lat[0])) * 111320.0
                            gt = [res_x, 0.0, float(lon.min()) * 111320.0,
                                  0.0, -res_y, float(lat.max()) * 111320.0]
                            crs = {
                                "longitude_of_projection_origin": 0.0,
                                "perspective_point_height": 35785863.0,
                                "sweep_angle_axis": "x",
                            }
                except Exception:
                    pass
            else:
                # Legacy SEVIRI: GEOS projection
                gm = ds[var_name].attrs.get("grid_mapping")
                if gm and gm in ds:
                    proj = ds[gm]
                    lon0 = float(proj.attrs.get("longitude_of_projection_origin", 0.0))
                    h = float(proj.attrs.get("perspective_point_height", 35785863.0))
                    sweep = proj.attrs.get("sweep_angle_axis", "x")
                    crs = {
                        "longitude_of_projection_origin": lon0,
                        "perspective_point_height": h,
                        "sweep_angle_axis": sweep,
                    }
                if "x" in ds and "y" in ds:
                    try:
                        xv = ds["x"].values
                        yv = ds["y"].values
                        if len(xv) > 1 and len(yv) > 1:
                            res_x = float(abs(xv[1] - xv[0]))
                            res_y = float(abs(yv[1] - yv[0]))
                            if crs:
                                h_val = crs["perspective_point_height"]
                                is_unit = max(abs(xv.min()), abs(xv.max()),
                                              abs(yv.min()), abs(yv.max())) < 1.0
                                if is_unit:
                                    gt = [res_x * h_val, 0.0, float(xv.min()) * h_val,
                                          0.0, -res_y * h_val, float(yv.max()) * h_val]
                                else:
                                    gt = [res_x, 0.0, float(xv.min()),
                                          0.0, -res_y, float(yv.max())]
                            else:
                                gt = [res_x, 0.0, float(xv.min()),
                                      0.0, -res_y, float(yv.max())]
                    except Exception:
                        pass

            return arr, crs, gt
    except Exception:
        return None, None, None


def extract_meteosat_crs(nc_path):
    nc_path = Path(nc_path)
    is_mtg = _MTG_FCI_RE.search(nc_path.name)
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            if is_mtg:
                # MTG FCI: geographic lat/lon grid
                crs = CRS.from_proj4("+proj=geos +lon_0=0 +h=35785863.0 +x_0=0 +y_0=0 +ellps=WGS84 +sweep=x +units=m +no_defs")
                gt = None
                if "longitude" in ds and "latitude" in ds:
                    lon = ds["longitude"].values
                    lat = ds["latitude"].values
                    if len(lon) > 1 and len(lat) > 1:
                        res_x = float(abs(lon[1] - lon[0])) * 111320.0
                        res_y = float(abs(lat[1] - lat[0])) * 111320.0
                        gt = Affine(res_x, 0.0, float(lon.min()) * 111320.0,
                                    0.0, -res_y, float(lat.max()) * 111320.0)
                return crs, gt

            # Legacy SEVIRI
            proj_params = None
            for var in ds.data_vars:
                gm = ds[var].attrs.get("grid_mapping")
                if gm and gm in ds:
                    p = ds[gm]
                    lon0 = float(p.attrs.get("longitude_of_projection_origin", 0.0))
                    h = float(p.attrs.get("perspective_point_height", 35785863.0))
                    sweep = p.attrs.get("sweep_angle_axis", "x")
                    proj_params = (lon0, h, sweep)
                    break
            if proj_params is None:
                proj_params = (0.0, 35785863.0, "x")
            lon0, h, sweep = proj_params
            sweep_param = f" +sweep={sweep}" if sweep else ""
            p4 = f"+proj=geos +lon_0={lon0} +h={h} +x_0=0 +y_0=0 +ellps=WGS84{sweep_param} +units=m +no_defs"
            crs = CRS.from_proj4(p4)
            gt = None
            if "x" in ds and "y" in ds:
                xv = ds["x"].values
                yv = ds["y"].values
                if len(xv) > 1 and len(yv) > 1:
                    res_x = float(abs(xv[1] - xv[0]))
                    res_y = float(abs(yv[1] - yv[0]))
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
            return crs, gt
    except Exception:
        return None, None
