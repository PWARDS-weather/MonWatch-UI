# =============================================================================
# warped_vrt.py — CPU reprojection fallback for MonWatch-UI Cyclone V3
# -----------------------------------------------------------------------------
# rasterio's WarpedVRT performs the disk -> equirectangular / plate-carree warp
# lazily in C on read. This is the fallback engine used when the OpenGL shader
# renderer (GLMapWidget) is unavailable (software GL, drivers with no GL, or the
# user disabled GPU rendering). It is an order of magnitude faster than the
# previous pure-Python pyproj loop, though each switch still does CPU work.
#
# Copyright (C) 2025-2026 PWARDS-weather
# Licensed under GPLv3, see LICENSE.
# =============================================================================

from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QImage

_DEFAULT_SAT_LON = 140.7
_WGS84_A = 6378137.0
_CROP_DEG = 85.0


def qimage_to_rgba(qimg: QImage) -> np.ndarray:
    """Convert a QImage into an (h, w, 4) uint8 RGBA array."""
    if qimg.isNull():
        raise ValueError("Cannot convert a null QImage to RGBA")
    if qimg.format() != QImage.Format_RGBA8888:
        qimg = qimg.convertToFormat(QImage.Format_RGBA8888)
    w, h = qimg.width(), qimg.height()
    ptr = qimg.bits()
    if hasattr(ptr, "setsize"):
        try:
            ptr.setsize(w * h * 4)
        except ValueError:
            pass
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 4).copy()
    return arr


def _rgba_to_qimage(arr: np.ndarray, w: int, h: int) -> QImage:
    img = QImage(arr.tobytes(), w, h, w * 4, QImage.Format_RGBA8888).copy()
    return img


def _eqc_dst_setup(out_w: int, out_h: int, sat_lon: float):
    """Equirectangular target grid identical to the app's AreaDefinition."""
    import rasterio
    from rasterio.transform import from_origin

    crop_m = _CROP_DEG * _WGS84_A * math.radians(1.0)
    dst_crs = f"+proj=eqc +lon_0={sat_lon:.6f} +lat_ts=0 +lat_0=0 +datum=WGS84 +units=m +no_defs"
    x_min, y_max = -crop_m, crop_m
    dx = (2 * crop_m) / out_w
    dy = (2 * crop_m) / out_h
    transform = from_origin(x_min, y_max, dx, dy)
    return rasterio.crs.CRS.from_string(dst_crs), transform, out_w, out_h


def _plate_carree_dst_setup(out_w: int, out_h: int, crs_src, gt, sat_lon: float):
    """EPSG:4326 grid covering the visible disk lon/lat bounds.

    The visible disk is always centred on the satellite longitude: lon is
    lon_0 +/- the disk half-angle and lat +/- the same. Deriving bounds from
    the source corners is unreliable because GEOS disks wrap the antimeridian
    (the sub-satellite point can fall outside a corner-derived lon box).
    """
    import rasterio
    from rasterio.transform import from_bounds

    half = _CROP_DEG
    lon_min = sat_lon - half
    lon_max = sat_lon + half
    lat_min = -half
    lat_max = half

    transform = from_bounds(lon_min, lat_min, lon_max, lat_max, out_w, out_h)
    return rasterio.crs.CRS.from_epsg(4326), transform, out_w, out_h


def reproject_warpedvrt_qimage(
    src_qimage: QImage,
    crs_src,
    gt,
    target_proj: str,
    out_w: int = 0,
    out_h: int = 0,
    sat_lon: float | None = None,
    resampling: str = "bilinear",
    radius_of_influence: float = 50000.0,
) -> QImage:
    """Warp a display QImage from its native (GEOS) grid to a target projection.

    Parameters
    ----------
    src_qimage : QImage        source display image (RGBA)
    crs_src : pyproj.CRS       source (typically GEOS) CRS
    gt : Affine                source geotransform
    target_proj : str          'equirectangular' or 'plate_carree'
    out_w, out_h : int         output grid size (auto-derived if <= 0)
    sat_lon : float            satellite longitude for eqc centering
    resampling: str            rasterio resampling algorithm name

    Returns
    -------
    QImage (RGBA8888) of the reprojected image.
    """
    src_w = src_qimage.width()
    src_h = src_qimage.height()
    if out_w <= 0 or out_h <= 0:
        out_w, out_h = src_w, src_h

    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    if sat_lon is None:
        try:
            crs_dict = crs_src.to_dict()
            sat_lon = float(crs_dict.get("lon_0", crs_dict.get("longitude_of_projection_origin", _DEFAULT_SAT_LON)))
        except Exception:
            sat_lon = _DEFAULT_SAT_LON

    resampler = Resampling[resampling.lower()] if resampling.lower() in Resampling.__members__ else Resampling.bilinear

    if target_proj == "equirectangular":
        dst_crs, dst_transform, out_w, out_h = _eqc_dst_setup(out_w, out_h, sat_lon)
    else:
        dst_crs, dst_transform, out_w, out_h = _plate_carree_dst_setup(out_w, out_h, crs_src, gt, sat_lon)

    rgba = qimage_to_rgba(src_qimage)
    h, w = rgba.shape[:2]

    # RGBA displayed image: black/transparent space pixels (alpha==0) are
    # masked so bilinear warp does not smear space into the disk.
    mask = rgba[:, :, 3] != 0

    with rasterio.io.MemoryFile() as memfile:
        with memfile.open(
            driver="GTiff", width=w, height=h, count=4, dtype="uint8", nodata=0,
            crs=crs_src, transform=gt,
        ) as dst:
            for i in range(4):
                band = np.ma.array(rgba[:, :, i], mask=~mask)
                dst.write(band, i + 1)
        # Reopen reading from the memory file
        with memfile.open() as src:
            with WarpedVRT(
                src,
                crs=dst_crs,
                transform=dst_transform,
                width=out_w,
                height=out_h,
                resampling=resampler,
                src_nodata=0,
            ) as vrt:
                warped = vrt.read()

    warped = np.stack(warped, axis=-1).astype(np.uint8)[:, :, :4]
    if warped.shape[-1] == 4:
        alpha = warped[:, :, 3]
        # Harden faint bilinear edges
        alpha[np.where((alpha > 0) & (alpha < 96))] = 255
        warped[:, :, 3] = alpha

    return _rgba_to_qimage(warped, out_w, out_h)