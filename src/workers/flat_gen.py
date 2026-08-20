import numpy as np
from io import BytesIO
from PySide6.QtGui import QImage
from PySide6.QtCore import Qt

MAX_FIG_INCHES = 20

def _normalize_lon_range(min_lon, max_lon):
    """
    Convert any longitude range to a continuous -180..180 based interval
    where min_lon < max_lon, suitable for Plate Carree.
    """
    min_lon = ((min_lon + 180) % 360) - 180
    max_lon = ((max_lon + 180) % 360) - 180

    if min_lon > max_lon:
        max_lon += 360

    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon
    return min_lon, max_lon

def _resample_to_platecarree(arr_rgba, w, h, src_crs, gt,
                              min_px, max_px, min_py, max_py,
                              min_lon, max_lon, min_lat, max_lat):
    from pyresample.geometry import AreaDefinition
    from pyresample.kd_tree import resample_nearest

    a, b, c, d, e, f = gt[0], gt[1], gt[2], gt[3], gt[4], gt[5]
    left = a * min_px + b * min_py + c
    top = d * min_px + e * min_py + f
    right = a * max_px + b * max_py + c
    bottom = d * max_px + e * max_py + f
    src_extent = (min(left, right), min(bottom, top), max(left, right), max(bottom, top))

    src_crs_dict = src_crs.to_dict()
    src_def = AreaDefinition('src', 'src', 'src', src_crs_dict, w, h, src_extent)

    # Target grid centered on the region (like automata.py's eqc lon_0=lon_norm).
    # A plain longlat target with max_lon > 180 (e.g. full disk 59.7..221.7)
    # makes pyresample drop every target pixel east of lon 180 -> black void.
    # Centering the Plate Carree target keeps all pixel lons within +/-crop,
    # so every target column stays valid while the column order is unchanged.
    # eqc is a projected CRS, so the extent must be in METERS not degrees.
    # eqc lat axis is centred on the reference latitude (lat_ts=0), so the
    # target y-extent must be shifted by center_lat to cover the region.
    central_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    crop_lon = (max_lon - min_lon) / 2.0
    crop_lat = (max_lat - min_lat) / 2.0
    m_per_deg = 111320.0
    tgt_crs = {'proj': 'eqc', 'lon_0': central_lon, 'lat_ts': 0, 'datum': 'WGS84'}
    tgt_extent = (-crop_lon * m_per_deg, (center_lat - crop_lat) * m_per_deg,
                  crop_lon * m_per_deg, (center_lat + crop_lat) * m_per_deg)
    tgt_def = AreaDefinition('flat', 'flat', 'flat', tgt_crs, w, h, tgt_extent)

    result = np.zeros((h, w, 4), dtype=np.uint8)
    roi = max(abs(right - left), abs(top - bottom)) / max(w, h) * 3
    for c in range(4):
        band = arr_rgba[..., c].astype(np.float32)
        resampled = resample_nearest(src_def, band, tgt_def,
                                      radius_of_influence=roi, fill_value=0)
        resampled = np.nan_to_num(resampled, nan=0)
        result[..., c] = np.clip(resampled, 0, 255).astype(np.uint8)
    return result


_MATPLOTLIB_LINESTYLES = {
    'solid': '-',
    'dotted': ':',
    'dashed': '--',
    'dashdot': '-.',
    'crosshatch': '--',
}


def _hex_to_rgba(color_hex, opacity):
    import matplotlib.colors as mcolors
    r, g, b = mcolors.to_rgb(color_hex)
    return (r, g, b, max(0.0, min(1.0, opacity / 255.0)))


def _add_grid_and_labels(ax, extent, grid_step, color, opacity, width, pattern, labels, render_dpi=100):
    import numpy as np
    import matplotlib.ticker as mticker

    linestyle = _MATPLOTLIB_LINESTYLES.get(pattern, '-')
    r, g, b, a = _hex_to_rgba(color, opacity)
    gl = ax.gridlines(draw_labels=False, linewidth=width,
                      color=(r, g, b),
                      alpha=a,
                      linestyle=linestyle)
    gl.xlocator = mticker.FixedLocator(np.arange(-180, 181, grid_step))
    gl.ylocator = mticker.FixedLocator(np.arange(-90, 91, grid_step))

    if not labels:
        return

    import cartopy.crs as ccrs
    min_lon, max_lon, min_lat, max_lat = extent
    lon_vals = np.arange(np.ceil(min_lon / grid_step) * grid_step,
                         np.floor(max_lon / grid_step) * grid_step + grid_step,
                         grid_step)
    lat_vals = np.arange(np.ceil(min_lat / grid_step) * grid_step,
                         np.floor(max_lat / grid_step) * grid_step + grid_step,
                         grid_step)
    # Constant physical label size like automata.py: fs=4 for large sectors
    # (Philippines/WestPac/full disk), fs=8 for smaller storm crops, NOT dpi-scaled.
    span = max(max_lon - min_lon, max_lat - min_lat)
    fs = 4 if span >= 30 else 8
    bbox_w = dict(facecolor='white', alpha=1.0, edgecolor='none', pad=1.0,
                  boxstyle='round,pad=0.3')
    px = (max_lat - min_lat) * 0.0012
    px_lr = (max_lon - min_lon) * 0.0008
    pc = ccrs.PlateCarree()
    zorder = 15

    for lat_val in lat_vals:
        if abs(lat_val) < grid_step / 2.0 and grid_step > 1:
            lat_val = 0.0
        label = f"{lat_val:.0f}°"
        ax.text(min_lon + px_lr, lat_val, label, transform=pc, fontsize=fs,
                color='black', fontweight='bold', ha='left', va='bottom',
                bbox=bbox_w, zorder=zorder)
        ax.text(max_lon - px_lr, lat_val, label, transform=pc, fontsize=fs,
                color='black', fontweight='bold', ha='right', va='bottom',
                bbox=bbox_w, zorder=zorder)
    for lon_val in lon_vals:
        lon_norm = (lon_val + 180) % 360 - 180
        if abs(lon_norm) < grid_step / 2.0 and grid_step > 1:
            lon_norm = 0.0
        label = f"{lon_norm:.0f}°"
        ax.text(lon_val, max_lat + px, label, transform=pc, fontsize=fs,
                color='black', fontweight='bold', ha='left', va='top',
                bbox=bbox_w, zorder=zorder)
        ax.text(lon_val, min_lat - px, label, transform=pc, fontsize=fs,
                color='black', fontweight='bold', ha='left', va='bottom',
                bbox=bbox_w, zorder=zorder)


def _draw_aor_vector(aors, ax, proj):
    """Draw AOR polygons directly as vector lines on the flat projection.

    aors: list of dicts {name, points: [(lon,lat),...], color, pattern}
    Matches the viewport AoR overlay ('aor_pattern' setting, 2px dashed lines).
    Points are geographic lon/lat, so the source transform is the plain
    PlateCarree (NOT the shifted 'proj'), so lon 118 maps to x = 118 - central_lon.
    """
    import cartopy.crs as ccrs
    geo = ccrs.PlateCarree()
    if not aors:
        return
    for aor in aors:
        pts = aor.get("points") or []
        if len(pts) < 2:
            continue
        color = aor.get("color", "#00FF9F")
        pattern = aor.get("pattern", "dashed")
        name = aor.get("name", "")
        lons = [float(p[0]) for p in pts]
        lats = [float(p[1]) for p in pts]
        lons.append(lons[0])
        lats.append(lats[0])
        ax.plot(lons, lats, transform=geo, color=color, linewidth=2,
                linestyle=_MATPLOTLIB_LINESTYLES.get(pattern, '--'),
                zorder=20)
        ax.text(lons[0], lats[0], name, transform=geo, color=color,
                fontsize=8, fontweight='bold', zorder=21)


def _make_cartopy_figure(rgba, w, h, min_lon, max_lon, min_lat, max_lat,
                         cmap=None, vmin=None, vmax=None,
                         grid_enabled=False, coast_enabled=True,
                         grid_step=10, grid_color='#00feed', grid_opacity=160,
                         grid_width=1, grid_pattern='dotted',
                         coast_color='#00ff00', coast_opacity=200,
                         coast_width=1, coast_pattern='solid',
                         labels=False, aors=None):
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    central_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    crop_lon = (max_lon - min_lon) / 2.0
    crop_lat = (max_lat - min_lat) / 2.0

    # Size the figure to the region's geographic aspect (like automata.py's
    # square PlateCarree crops) so imshow's equal-aspect scaling keeps output
    # pixels geographically square. Using the raw array aspect (capture dims,
    # often square) would squeeze non-square regions and leave black bars.
    span_lon = max_lon - min_lon
    span_lat = max_lat - min_lat
    if span_lon > 0 and span_lat > 0:
        out_h = max(1, int(round(w * span_lat / span_lon)))
    else:
        out_h = h
    render_dpi = max(w, out_h) / MAX_FIG_INCHES
    fig = plt.figure(figsize=(w / render_dpi, out_h / render_dpi), dpi=render_dpi)

    proj = ccrs.PlateCarree(central_longitude=central_lon)
    ax = fig.add_axes([0, 0, 1, 1], projection=proj, facecolor='black')

    extent = [-crop_lon, crop_lon, center_lat - crop_lat, center_lat + crop_lat]

    if cmap is None:
        ax.imshow(rgba, extent=extent, transform=proj, origin='upper')
    else:
        ax.imshow(rgba, cmap=cmap, vmin=vmin, vmax=vmax,
                  extent=extent, transform=proj, origin='upper')

    if coast_enabled:
        coast_alpha = _hex_to_rgba(coast_color, coast_opacity)[3]
        coast_ls = _MATPLOTLIB_LINESTYLES.get(coast_pattern, '-')
        ax.add_feature(cfeature.COASTLINE.with_scale('10m'),
                       linewidth=coast_width * 0.5, edgecolor=coast_color,
                       alpha=coast_alpha, linestyle=coast_ls,
                       facecolor='none', zorder=10)
        ax.add_feature(cfeature.BORDERS.with_scale('10m'),
                       linewidth=coast_width * 0.3, edgecolor=coast_color,
                       alpha=coast_alpha * 0.5, linestyle=coast_ls,
                       facecolor='none', zorder=10)

    ax.set_extent(extent, crs=proj)

    if grid_enabled:
        _add_grid_and_labels(ax, (min_lon, max_lon, min_lat, max_lat),
                             grid_step, grid_color, grid_opacity,
                             grid_width, grid_pattern, labels, render_dpi)

    _draw_aor_vector(aors, ax, proj)

    ax.axis('off')

    buf = BytesIO()
    fig.savefig(buf, format='png', dpi=render_dpi, facecolor='black')
    buf.seek(0)
    plt.close(fig)
    return buf


def render_flat(img, min_lon, max_lon, min_lat, max_lat,
                src_crs, gt, min_px, max_px, min_py, max_py,
                grid_enabled=False, coast_enabled=True,
                grid_step=10, grid_color='#00feed', grid_opacity=160,
                grid_width=1, grid_pattern='dotted',
                coast_color='#00ff00', coast_opacity=200,
                coast_width=1, coast_pattern='solid',
                labels=False, aors=None):
    try:
        import cartopy.crs as ccrs
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from pyresample.geometry import AreaDefinition
        from pyresample.kd_tree import resample_nearest
    except ImportError:
        return img

    try:
        # ---- FIX: normalise longitude range ----
        min_lon, max_lon = _normalize_lon_range(min_lon, max_lon)
        # ----------------------------------------

        w, h = img.width(), img.height()
        if w < 100 or h < 100:
            return img

        arr = np.frombuffer(img.constBits(), dtype=np.uint8).reshape(h, w, 4)
        arr_rgba = arr[:, :, [2, 1, 0, 3]].copy()

        result = _resample_to_platecarree(arr_rgba, w, h, src_crs, gt,
                                           min_px, max_px, min_py, max_py,
                                           min_lon, max_lon, min_lat, max_lat)

        buf = _make_cartopy_figure(result, w, h, min_lon, max_lon, min_lat, max_lat,
                                   grid_enabled=grid_enabled,
                                   coast_enabled=coast_enabled,
                                   grid_step=grid_step,
                                   grid_color=grid_color,
                                   grid_opacity=grid_opacity,
                                   grid_width=grid_width,
                                   grid_pattern=grid_pattern,
                                   coast_color=coast_color,
                                   coast_opacity=coast_opacity,
                                   coast_width=coast_width,
                                   coast_pattern=coast_pattern,
                                   labels=labels,
                                   aors=aors)

        result_img = QImage()
        result_img.loadFromData(buf.getvalue())
        buf.close()
        result_img = result_img.convertToFormat(QImage.Format_ARGB32)
        return result_img
    except Exception as e:
        print(f"flat_gen.render_flat error: {e}")
        import traceback
        traceback.print_exc()
        return img


def render_flat_from_array(data, min_lon, max_lon, min_lat, max_lat,
                           grid_enabled=False, coast_enabled=True,
                           grid_step=10, grid_color='#00feed', grid_opacity=160,
                           grid_width=1, grid_pattern='dotted',
                           coast_color='#00ff00', coast_opacity=200,
                           coast_width=1, coast_pattern='solid',
                           labels=False, aors=None):
    try:
        import cartopy.crs as ccrs
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        h, w = data.shape
        img = QImage(data.data, w, h, w, QImage.Format_Grayscale8)
        return img.convertToFormat(QImage.Format_ARGB32)

    try:
        # ---- FIX: normalise longitude range ----
        min_lon, max_lon = _normalize_lon_range(min_lon, max_lon)
        # ----------------------------------------

        h, w = data.shape
        buf = _make_cartopy_figure(data, w, h, min_lon, max_lon, min_lat, max_lat,
                                    cmap='gray_r', vmin=0, vmax=255,
                                    grid_enabled=grid_enabled,
                                    coast_enabled=coast_enabled,
                                    grid_step=grid_step,
                                    grid_color=grid_color,
                                    grid_opacity=grid_opacity,
                                    grid_width=grid_width,
                                    grid_pattern=grid_pattern,
                                    coast_color=coast_color,
                                    coast_opacity=coast_opacity,
                                    coast_width=coast_width,
                                    coast_pattern=coast_pattern,
                                    labels=labels,
                                    aors=aors)
        result = QImage()
        result.loadFromData(buf.getvalue())
        buf.close()
        result = result.convertToFormat(QImage.Format_ARGB32)
        if result.width() != w or result.height() != h:
            result = result.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        return result
    except Exception as e:
        print(f"flat_gen.render_flat_from_array error: {e}")
        import traceback
        traceback.print_exc()
        h, w = data.shape
        img = QImage(data.data, w, h, w, QImage.Format_Grayscale8)
        return img.convertToFormat(QImage.Format_ARGB32)

