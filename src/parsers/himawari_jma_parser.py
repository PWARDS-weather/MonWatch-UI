import re
import numpy as np
import xarray as xr
from pathlib import Path
from pyproj import CRS
from rasterio.transform import Affine


def is_himawari_jma_nc(nc_path):
    p = Path(nc_path)
    if re.search(r'NC_H\d{2}_\d{8}_\d{4}_', p.name):
        return True
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            dims = list(ds.dims)
            has_lonlat = ("lon" in dims and "lat" in dims) or ("longitude" in dims and "latitude" in dims)
            if not has_lonlat:
                return False
            sat = str(ds.attrs.get("satellite_name", "")).lower()
            if "himawari" in sat:
                return True
            prod = str(ds.attrs.get("product_name", "")).lower()
            if "ahi" in prod:
                return True
            title = str(ds.attrs.get("title", "")).lower()
            if "himawari" in title or "ahi" in title:
                return True
    except Exception:
        pass
    return False


def parse_himawari_jma_metadata(nc_path):
    p = Path(nc_path)
    result = {"satellite": "himawari", "band_name": None,
              "timestamp": None}
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
            for var in ds.data_vars:
                m = re.match(r'B(\d{2})', var)
                if m:
                    result["band_name"] = f"B{m.group(1)}"
                    break
    except Exception:
        pass
    return result


def extract_himawari_jma_bands(nc_files):
    band_map = {}
    for fp in nc_files:
        try:
            with xr.open_dataset(fp, engine="netcdf4") as ds:
                for var in ds.data_vars:
                    m = re.match(r'B(\d{2})', var)
                    if m:
                        band_map[f"B{m.group(1)}"] = Path(fp)
        except Exception:
            pass
    return band_map


def _lonlat_to_geos_affine(lon, lat, crs_dict):
    """Build an Affine geotransform from a rectilinear lon/lat grid using the
    true geostationary projection instead of a plate-carree approximation.

    The geostationary map is nonlinear, so a least-squares affine fit over a
    subsample of the full grid is used to approximate the projected plane.
    """
    if len(lon) < 2 or len(lat) < 2:
        return None
    try:
        from pyproj import Transformer
        lon0 = float(crs_dict.get("longitude_of_projection_origin", 140.7))
        h = float(crs_dict.get("perspective_point_height", 35785863.0))
        sweep = crs_dict.get("sweep_angle_axis", "x")
        p4 = (f"+proj=geos +lon_0={lon0} +h={h} +x_0=0 +y_0=0 +ellps=WGS84 "
              f"+sweep={sweep} +units=m +no_defs")
        geos_crs = CRS.from_proj4(p4)
        trans = Transformer.from_crs("EPSG:4326", geos_crs, always_xy=True)
        n_lon = len(lon)
        n_lat = len(lat)
        step = max(1, int((n_lon * n_lat) ** 0.5) // 45)
        cols = np.arange(0, n_lon, step)
        rows = np.arange(0, n_lat, step)
        cc, rr = np.meshgrid(cols, rows)
        pts = trans.transform(np.asarray(lon, dtype=np.float64)[cc.ravel()],
                              np.asarray(lat, dtype=np.float64)[rr.ravel()])
        xs = np.asarray(pts[0], dtype=np.float64)
        ys = np.asarray(pts[1], dtype=np.float64)
        good = np.isfinite(xs) & np.isfinite(ys)
        n_good = int(np.count_nonzero(good))
        if n_good < 3:
            return None
        col_idx = cc.ravel().astype(np.float64)[good]
        row_idx = rr.ravel().astype(np.float64)[good]
        design = np.stack([np.ones(n_good), col_idx, row_idx], axis=-1)
        coef = np.linalg.lstsq(design, np.stack([xs[good], ys[good]], axis=-1), rcond=None)[0]
        c, a, b = float(coef[0, 0]), float(coef[1, 0]), float(coef[2, 0])
        f, d, e = float(coef[0, 1]), float(coef[1, 1]), float(coef[2, 1])
        if abs(a) < 1e-12 and abs(b) < 1e-12 or (abs(d) < 1e-12 and abs(e) < 1e-12):
            return None
        return Affine(a, b, c, d, e, f)
    except Exception:
        return None


def read_himawari_jma_band_data(nc_path, band):
    nc_path = Path(nc_path)
    try:
        with xr.open_dataset(nc_path, mask_and_scale=False, engine="netcdf4") as ds:
            var_name = band if band in ds else None
            if var_name is None:
                for var in ds.data_vars:
                    if ds[var].ndim >= 2:
                        var_name = var
                        break
            if var_name is None:
                return None, None, None
            arr = ds[var_name].values.astype(np.float32)
            fill = ds[var_name].attrs.get("_FillValue")
            if fill is not None:
                arr[arr == fill] = np.nan
            missing = ds[var_name].attrs.get("missing_value")
            if missing is not None:
                arr[arr == missing] = np.nan
            scale = ds[var_name].attrs.get("scale_factor", 1.0)
            offset = ds[var_name].attrs.get("add_offset", 0.0)
            if scale != 1.0 or offset != 0.0:
                arr = arr * np.float32(scale) + np.float32(offset)
            crs = None
            gt = None
            if "goes_imager_projection" in ds:
                proj = ds["goes_imager_projection"]
                lon0 = float(proj.attrs.get("longitude_of_projection_origin", 140.7))
                h = float(proj.attrs.get("perspective_point_height", 35785863.0))
                sweep = proj.attrs.get("sweep_angle_axis", "x")
                crs = {
                    "longitude_of_projection_origin": lon0,
                    "perspective_point_height": h,
                    "sweep_angle_axis": sweep,
                }
            if crs is None:
                crs = {
                    "longitude_of_projection_origin": 140.7,
                    "perspective_point_height": 35785863.0,
                    "sweep_angle_axis": "x",
                }
            h = float(crs["perspective_point_height"])
            if "x" in ds and "y" in ds and len(ds["x"]) > 1 and len(ds["y"]) > 1:
                xv = ds["x"].values
                yv = ds["y"].values
                res_x = float(abs(xv[1] - xv[0]))
                res_y = float(abs(yv[1] - yv[0]))
                abs_max = max(abs(xv.min()), abs(xv.max()), abs(yv.min()), abs(yv.max()))
                if abs_max < 1.0:
                    res_x *= h
                    res_y *= h
                    gt = [res_x, 0.0, float(xv.min()) * h,
                          0.0, -res_y, float(yv.max()) * h]
                else:
                    gt = [res_x, 0.0, float(xv.min()),
                          0.0, -res_y, float(yv.max())]
            elif "lon" in ds and "lat" in ds and len(ds["lon"]) > 1 and len(ds["lat"]) > 1:
                aff = _lonlat_to_geos_affine(ds["lon"].values, ds["lat"].values, crs)
                if aff is not None:
                    gt = [aff.a, 0.0, aff.c, 0.0, aff.e, aff.f]
            return arr, crs, gt
    except Exception:
        return None, None, None


def extract_himawari_jma_crs(nc_path):
    nc_path = Path(nc_path)
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            if "goes_imager_projection" in ds:
                proj = ds["goes_imager_projection"]
                lon0 = float(proj.attrs.get("longitude_of_projection_origin", 140.7))
                h = float(proj.attrs.get("perspective_point_height", 35785863.0))
                sweep = proj.attrs.get("sweep_angle_axis", "x")
                sweep_param = f" +sweep={sweep}" if sweep else ""
                p4 = f"+proj=geos +lon_0={lon0} +h={h} +x_0=0 +y_0=0 +ellps=WGS84{sweep_param} +units=m +no_defs"
                crs = CRS.from_proj4(p4)
            else:
                p4 = "+proj=geos +lon_0=140.7 +h=35785863.0 +x_0=0 +y_0=0 +ellps=WGS84 +sweep=x +units=m +no_defs"
                crs = CRS.from_proj4(p4)
                h = 35785863.0
            gt = None
            if "x" in ds and "y" in ds:
                xv = ds["x"].values
                yv = ds["y"].values
                if len(xv) > 1 and len(yv) > 1:
                    res_x = float(abs(xv[1] - xv[0]))
                    res_y = float(abs(yv[1] - yv[0]))
                    abs_max = max(abs(xv.min()), abs(xv.max()), abs(yv.min()), abs(yv.max()))
                    if abs_max < 1.0:
                        res_x *= h
                        res_y *= h
                        gt = Affine(res_x, 0.0, float(xv.min()) * h, 0.0, -res_y, float(yv.max()) * h)
                    else:
                        gt = Affine(res_x, 0.0, float(xv.min()), 0.0, -res_y, float(yv.max()))
            elif "lon" in ds and "lat" in ds:
                lon = ds["lon"].values
                lat = ds["lat"].values
                if len(lon) > 1 and len(lat) > 1:
                    gt = _lonlat_to_geos_affine(lon, lat, {
                        "longitude_of_projection_origin": lon0 if "goes_imager_projection" in ds else 140.7,
                        "perspective_point_height": h,
                        "sweep_angle_axis": sweep if "goes_imager_projection" in ds else "x",
                    })
            return crs, gt
    except Exception:
        return None, None
