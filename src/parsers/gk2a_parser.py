import re
import math
import numpy as np
import xarray as xr
from pathlib import Path
from pyproj import CRS
from rasterio.transform import Affine


_FILE_NAME_RE = re.compile(
    r'gk2a_ami_le1b_([a-z]{2}\d{3})_fd(\d{3})ge_(\d{12})\.nc$',
    re.IGNORECASE
)

GK2A_BAND_MAP = {
    "vi004": "B01",   # 0.47 µm  – Blue visible
    "vi005": "B02",   # 0.51 µm  – Green visible
    "vi006": "B03",   # 0.64 µm  – Red visible
    "vi008": "B04",   # 0.86 µm  – Near-IR
    "nr013": "B05",   # 1.3  µm  – Near-IR
    "nr016": "B06",   # 1.6  µm  – Near-IR
    "sw038": "B07",   # 3.8  µm  – Shortwave-IR
    "wv063": "B08",   # 6.3  µm  – Water vapour
    "wv069": "B09",   # 6.9  µm  – Water vapour
    "wv073": "B10",   # 7.3  µm  – Water vapour
    "ir087": "B11",   # 8.7  µm  – Thermal-IR
    "ir096": "B12",   # 9.6  µm  – Ozone
    "ir105": "B13",   # 10.5 µm  – Thermal-IR
    "ir112": "B14",   # 11.2 µm  – Thermal-IR
    "ir123": "B15",   # 12.3 µm  – Thermal-IR
    "ir133": "B16",   # 13.3 µm  – Thermal-IR
}

INV_GK2A_BAND_MAP = {v: k for k, v in GK2A_BAND_MAP.items()}

IR_BANDS = {"B07", "B08", "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"}
VIS_BANDS = {"B01", "B02", "B03", "B04", "B05", "B06"}


def is_gk2a_file(nc_path):
    p = Path(nc_path)
    name = p.name.lower()
    if name.startswith("gk2a"):
        return True
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            sat = str(ds.attrs.get("satellite_name", "")).lower()
            if "gk-2a" in sat or "gk2a" in sat:
                return True
            inst = str(ds.attrs.get("instrument", "")).lower()
            if "ami" in inst:
                return True
            proj = str(ds.attrs.get("projection_type", "")).lower()
            if proj == "geos":
                return True
    except Exception:
        pass
    return False


def parse_gk2a_metadata(nc_path):
    p = Path(nc_path)
    name = p.stem
    result = {
        "satellite": "gk2a",
        "instrument": "AMI",
        "band_name": None,
        "band_code": None,
        "timestamp": None,
        "satellite_lon": 128.2,
    }
    m = _FILE_NAME_RE.search(p.name)
    if m:
        band_code = m.group(1)
        ts = m.group(3)
        result["band_name"] = GK2A_BAND_MAP.get(band_code)
        result["band_code"] = band_code
        result["timestamp"] = f"{ts[:4]}_{ts[4:6]}_{ts[6:8]}_{ts[8:10]}{ts[10:12]}"
    else:
        ts_match = re.search(r'(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})', name)
        if ts_match:
            result["timestamp"] = f"{ts_match.group(1)}_{ts_match.group(2)}_{ts_match.group(3)}_{ts_match.group(4)}{ts_match.group(5)}"
        for code, band in GK2A_BAND_MAP.items():
            if code in name.lower():
                result["band_name"] = band
                result["band_code"] = code
                break
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            if "time_coverage_start" in ds.attrs:
                ts = str(ds.attrs["time_coverage_start"])
                tm = re.search(r'(\d{4})[_-]?(\d{2})[_-]?(\d{2})[T ]?(\d{2}):?(\d{2})', ts)
                if tm:
                    result["timestamp"] = f"{tm.group(1)}_{tm.group(2)}_{tm.group(3)}_{tm.group(4)}{tm.group(5)}"
            sub_lon = ds.attrs.get("sub_longitude")
            if sub_lon is not None:
                lon_val = float(sub_lon)
                if abs(lon_val) < 6.3:
                    lon_val = math.degrees(lon_val)
                result["satellite_lon"] = lon_val
    except Exception:
        pass
    return result


def extract_gk2a_bands(nc_files):
    band_map = {}
    for fp in nc_files:
        name = Path(fp).name
        m = _FILE_NAME_RE.search(name)
        if m:
            band_code = m.group(1)
            band = GK2A_BAND_MAP.get(band_code)
            if band:
                band_map[band] = Path(fp)
                continue
        name_lower = name.lower()
        found = False
        for code, band in GK2A_BAND_MAP.items():
            if code in name_lower:
                band_map[band] = Path(fp)
                found = True
                break
        if not found:
            try:
                with xr.open_dataset(fp, engine="netcdf4") as ds:
                    for var in ds.data_vars:
                        vlow = var.lower()
                        for code, band in GK2A_BAND_MAP.items():
                            if code in vlow:
                                band_map[band] = Path(fp)
                                found = True
                                break
                        if found:
                            break
            except Exception:
                pass
    return band_map


def _compute_geotransform_from_cfac(ds):
    coff = float(ds.attrs.get("coff", 0))
    loff = float(ds.attrs.get("loff", 0))

    # AMI L1B declares the native ground resolution in channel_spatial_resolution
    # (a string in km, e.g. "2.0" / "1.0" / "0.5"). The raw cfac/lfac values are
    # scaled such that h/cfac is NOT arc-length metres -- naively using that
    # produced a ~2 m pixel size (1000x too small) that shrank full-disk IR
    # imagery down to a handful of pixels and broke every display/overlay
    # placement computation for GK-2A.
    try:
        res_m = float(str(ds.attrs.get("channel_spatial_resolution", "2.0"))) * 1000.0
    except (TypeError, ValueError):
        res_m = 0.0
    if not res_m or not math.isfinite(res_m) or res_m <= 0.0:
        cfac = float(ds.attrs.get("cfac", 0))
        lfac = float(ds.attrs.get("lfac", 0))
        if cfac == 0 or lfac == 0:
            res_m = 2000.0
        else:
            try:
                h = float(ds.attrs.get("nominal_satellite_height", 42164000.0))
                # AMI/other GEOS cfac convention: h / cfac already yields the
                # pixel pitch in kilometres (e.g. ~2.064 for a 2 km product),
                # so scale up to metres here.
                res_m = (h / abs(float(cfac))) * 1000.0
            except (TypeError, ValueError, ZeroDivisionError):
                res_m = 2000.0

    # AMI images are stored north-up (row 0 = north). The sub-satellite point
    # sits at raster index (COFF, LOFF), which maps to the projection origin,
    # so the affine is centred there with `c` on the west (negative) edge and
    # `f` on the north (positive) edge.
    x_ul = -coff * res_m
    y_ul = +loff * res_m

    return Affine(res_m, 0.0, x_ul, 0.0, -res_m, y_ul)


def _build_gk2a_crs(ds):
    lon0 = float(ds.attrs.get("sub_longitude", 2.2375))
    if abs(lon0) < 10:
        lon0 = math.degrees(lon0)
    h = float(ds.attrs.get("nominal_satellite_height", 42164000.0))
    req = float(ds.attrs.get("earth_equatorial_radius", 6378137.0))
    rpol = float(ds.attrs.get("earth_polar_radius", 6356752.3))
    sweep = ds.attrs.get("sweep_angle_axis", "x")

    p4 = (
        f"+proj=geos +lon_0={lon0} +h={h} +x_0=0 +y_0=0 "
        f"+a={req} +b={rpol} +sweep={sweep} +units=m +no_defs"
    )
    return CRS.from_proj4(p4)


def read_gk2a_band_data(nc_path, band):
    nc_path = Path(nc_path)
    try:
        with xr.open_dataset(nc_path, mask_and_scale=False, engine="netcdf4") as ds:
            var_name = None
            if "image_pixel_values" in ds:
                var_name = "image_pixel_values"
            elif band in ds:
                var_name = band
            else:
                for var in ds.data_vars:
                    vlow = var.lower()
                    if "image_pixel" in vlow or "data" in vlow or "rad" in vlow:
                        var_name = var
                        break
                if var_name is None:
                    for var in ds.data_vars:
                        if ds[var].ndim >= 2:
                            var_name = var
                            break

            if var_name is None:
                return None, None, None

            raw = ds[var_name].values
            arr = raw.astype(np.float32)

            if raw.dtype.kind in ("u", "i"):
                qa_bits = ds[var_name].attrs.get("number_of_data_quality_flag_bits_per_pixel", 2)
                if qa_bits > 0:
                    qa_mask = raw & ((1 << qa_bits) - 1)
                    bad_qa = (qa_mask >= 2)
                    arr = (raw >> qa_bits).astype(np.float32)
                    arr[bad_qa] = np.nan

            fill = ds[var_name].attrs.get("_FillValue")
            if fill is not None:
                arr[raw == fill] = np.nan
            missing = ds[var_name].attrs.get("missing_value")
            if missing is not None:
                arr[raw == missing] = np.nan

            gain = float(ds.attrs.get("DN_to_Radiance_Gain", 1.0))
            offset = float(ds.attrs.get("DN_to_Radiance_Offset", 0.0))

            band_name = ds[var_name].attrs.get("channel_name", band)
            is_ir = band in IR_BANDS or (band_name.startswith("IR") or band_name.startswith("WV") or band_name.startswith("SW"))

            if is_ir:
                c0 = ds.attrs.get("Teff_to_Tbb_c0")
                c1 = ds.attrs.get("Teff_to_Tbb_c1")
                c2 = ds.attrs.get("Teff_to_Tbb_c2")
                if c0 is not None and c1 is not None and c2 is not None:
                    c0 = float(c0)
                    c1 = float(c1)
                    c2 = float(c2)
                    mask = np.isfinite(arr)
                    if gain != 1.0 or offset != 0.0:
                        teff = offset - gain * arr[mask]
                    else:
                        teff = arr[mask]
                    tb = c0 + c1 * teff + c2 * teff * teff
                    arr[mask] = tb
                elif gain != 1.0 or offset != 0.0:
                    arr = arr * gain + offset
            else:
                if gain != 1.0 or offset != 0.0:
                    arr = arr * gain + offset
                albedo_c = ds.attrs.get("Radiance_to_Albedo_c")
                if albedo_c is not None:
                    arr = arr * float(albedo_c)

            crs = _build_gk2a_crs(ds)
            gt = _compute_geotransform_from_cfac(ds)

            return arr, crs, gt
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None, None, None


def extract_gk2a_crs(nc_path):
    nc_path = Path(nc_path)
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            crs = _build_gk2a_crs(ds)
            gt = _compute_geotransform_from_cfac(ds)
            return crs, gt
    except Exception:
        return None, None