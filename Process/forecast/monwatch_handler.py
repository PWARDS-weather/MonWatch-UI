import sys, json, io, os, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import matplotlib.patches as mpatches
import matplotlib.patheffects as path_effects
import pandas as pd
from cartopy.geodesic import Geodesic
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from .utils import (
    HAS_DEPS, _log, _LAND, _OCEAN, _COASTLINE, _BORDERS,
    _add_psgc_ph_land, _add_psgc_boundaries, script_dir,
    _parse_kmz, _build_cone_polygon, _cat_color, _cat_label,
    _cat_sym, _category_to_sym, _category_to_pag_sym, _category_to_jtwc_sym,
    _parse_dt, _is_old_point, _parse_jtwc_dtg,
    _draw_wind_radii, _compute_bezier_offsets, _compute_curvatures,
    _cascade_bezier_offsets, _add_footer,
)
from .shared_flow import (
    _compute_single_extent, _compute_combined_extent,
    _build_track_labels, _find_first_future_idx, _fig_to_pil,
    _find_first_forecast_point,
    _draw_pil_title_box, _draw_combined_title_box,
    _add_forecast_footer, _add_combined_footer, _add_disclaimer,
    _fetch_jma_model_tracks, _compute_unified_offsets, _NEW_ALGO_NAMES,
    _clean_storm_name,
    _adjust_lons_for_plot,
)

forecast_layout = "monwatch"

def make_forecast(track_points, probability_circles=None, storm_name="Tropical Cyclone",
                  storm_id="", margin=8.0, dpi=150, figsize=(12, 10),
                  facecolor="white", filename=None, light=True,
                  settings=None, logo_path=None, algorithm="polar",
                   issued_dtg=None, kmz_cone="", kmz_track="", kmz_wind_initial="", **kwargs):
    _basin = kwargs.get("basin", storm_id[:2].upper() if storm_id and len(storm_id) >= 2 else "")
    _src = kwargs.get("source", "")
    _title_info = kwargs.get("title_info", {})
    dpi_scale = dpi / 150.0
    if not HAS_DEPS or not track_points:
        _log(f"[fcst] SKIP: HAS_DEPS={HAS_DEPS}, track_points={bool(track_points)}", file=sys.stderr)
        return None
    lons = [p.get("lon") for p in track_points if p.get("lon") is not None]
    lats = [p.get("lat") for p in track_points if p.get("lat") is not None]
    if not lons:
        _log(f"[fcst] SKIP: no valid lon/lat in {len(track_points)} track points", file=sys.stderr)
        return None
    fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
    # Widen figure if logo would be too close to title box
    _iw_est = figsize[0] * dpi
    _ih_est = figsize[1] * dpi
    _mw_w_est = ((int(_ih_est * 0.05) + 30) * 3) * 2
    _logo_right_est = int(_iw_est * 0.01) + _mw_w_est
    _gap_est = int(_iw_est * 0.075) - _logo_right_est
    if _gap_est < 450:
        figsize = (figsize[0] + (450 - _gap_est) / dpi, figsize[1])
    ext = _compute_single_extent(track_points, probability_circles, kmz_cone, margin, dpi, figsize, "monwatch", logo_path,
                                 danger_swath=kwargs.get("danger_swath", []),
                                 best_track_points=kwargs.get("best_track_points", []))
    if ext[0] is None:
        return None
    lon_min, lon_max, lat_min, lat_max, central_lon = ext
    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor=facecolor)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
    _log(f"[fcst] storm={storm_name} id={storm_id} layout={forecast_layout} light={light} "
         f"dpi={dpi} figsize={figsize} extent=[{lon_min:.2f}, {lon_max:.2f}, {lat_min:.2f}, {lat_max:.2f}]",
         file=sys.stderr)

    # JMA model tracks overlaid for MonWatch-UI
    if storm_id:
        _fetch_jma_model_tracks(ax, storm_id)

    # MonWatch-UI dark theme map setup
    _log(f"[fcst]   monwatch: sea=#3b4b5b land=#607d8b land_outline=#03fcfc grid=#343434(2px dashed)", file=sys.stderr)
    facecolor = '#3b4b5b'
    fig.patch.set_facecolor('#3b4b5b')
    ax.set_facecolor('#3b4b5b')
    ax.add_feature(_LAND, edgecolor='#03fcfc', linewidth=5, alpha=1, facecolor='#607d8b')
    _add_psgc_ph_land(ax, facecolor='#607d8b', edgecolor='#03fcfc', linewidth=5, zorder=2)
    ax.add_feature(_OCEAN, facecolor='#3b4b5b', alpha=1.0)
    ax.add_feature(_BORDERS, edgecolor='#3b4b5b', linewidth=1, linestyle=':')
    ax.add_feature(cfeature.STATES.with_scale('10m'), linewidth=0.5, edgecolor='#2a3a3a', alpha=0.6)
    _add_psgc_boundaries(ax, edgecolor='#3b4b5b', linewidth=0.5, linestyle=':', zorder=3)
    gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                      linewidth=2.0, color='#343434', alpha=1.0, linestyle='--')
    gl.top_labels = True
    gl.right_labels = True
    gl.left_labels = True
    gl.bottom_labels = True
    gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', "weight": "bold"}
    gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', "rotation": 90, "weight": "bold"}
    gl.xformatter = LONGITUDE_FORMATTER
    gl.yformatter = LATITUDE_FORMATTER
    gl.xpadding = 2
    gl.ypadding = 2
    label_color = '#ffffff'
    label_bg = '#00000088'
    label_border = 'none'
    title_color = '#ffffff'

    # PAR: non-pagasa red dashed
    par_pts = [
        (115.0, 5.0), (115.0, 15.0), (120.0, 21.0),
        (120.0, 25.0), (135.0, 25.0), (135.0, 5.0),
    ]
    par_lons = [p[0] for p in par_pts]
    par_lats = [p[1] for p in par_pts]
    ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
            color="#FF3333", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=1)

    # Cone
    geod = Geodesic()
    cone_method = fcst_prefs.get("cone_method", "smooth")
    if probability_circles:
        if cone_method == "union":
            from shapely.geometry import Point as ShapelyPoint
            from shapely.ops import unary_union
            pag_centers = []
            pag_radii = []
            for circ in probability_circles:
                clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
                clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
                radius_m = circ.get("radius_m") or circ.get("radius")
                if clon is not None and clat is not None and radius_m:
                    pag_centers.append((clon, clat))
                    pag_radii.append(float(radius_m))
            if len(pag_centers) >= 2:
                ref_lat = sum(lat for _, lat in pag_centers) / len(pag_centers)
                ref_lon = sum(lon for lon, _ in pag_centers) / len(pag_centers)
                cos_ref = math.cos(math.radians(ref_lat))
                def _to_xy(lon, lat):
                    return ((lon - ref_lon) * 111320.0 * cos_ref, (lat - ref_lat) * 111320.0)
                def _from_xy(x, y):
                    return (ref_lon + x / (111320.0 * cos_ref), ref_lat + y / 111320.0)
                circles = [ShapelyPoint(_to_xy(lon, lat)).buffer(r, resolution=36) for lon, lat, r in zip(
                    [c[0] for c in pag_centers], [c[1] for c in pag_centers], pag_radii)]
                merged = unary_union(circles)
                if merged is not None and not merged.is_empty:
                    fill_color = '#00FFF2'
                    if merged.geom_type == 'Polygon':
                        coords = list(merged.exterior.coords)
                        poly_lons, poly_lats = zip(*[_from_xy(x, y) for x, y in coords])
                        ax.fill(poly_lons, poly_lats, color=fill_color, alpha=0.2, transform=ccrs.PlateCarree(), zorder=1)
                    elif merged.geom_type == 'MultiPolygon':
                        for poly in merged.geoms:
                            coords = list(poly.exterior.coords)
                            poly_lons, poly_lats = zip(*[_from_xy(x, y) for x, y in coords])
                            ax.fill(poly_lons, poly_lats, color=fill_color, alpha=0.2, transform=ccrs.PlateCarree(), zorder=1)
        else:
            poly_lons, poly_lats = _build_cone_polygon(track_points, probability_circles)
            if poly_lons:
                fill_c = '#60a5fa'
                fill_a = 0.15
                out_c = '#60a5fa'
                out_ls = '--'
                out_lw = 1.0
                z = 50
                ax.fill(poly_lons, poly_lats, color=fill_c, alpha=fill_a,
                        transform=ccrs.PlateCarree(), zorder=z)
                ax.plot(poly_lons + [poly_lons[0]], poly_lats + [poly_lats[0]], color=out_c, linewidth=out_lw, alpha=0.6, linestyle=out_ls,
                        transform=ccrs.PlateCarree(), zorder=z + 1)

    # Wind initial radii — pre-parsed (JTWC/NHC-style) or KMZ re-parse
    _wr_polys = kwargs.get("wind_radii_polygons", {})
    wr_colors = [('#00C800', 0.20), ('#FFA500', 0.25), ('#FF3232', 0.30)]
    _wr_has_data = any(_wr_polys.get(_kt) for _kt in ("34", "50", "64"))
    if _wr_has_data:
        for _ki, _kt in enumerate(("34", "50", "64")):
            for _coords in _wr_polys.get(_kt, []):
                _lons = [c[0] for c in _coords]
                _lats = [c[1] for c in _coords]
                if len(_lons) < 3:
                    continue
                ax.fill(_lons, _lats, color=wr_colors[_ki][0], alpha=wr_colors[_ki][1],
                        edgecolor=wr_colors[_ki][0], linewidth=0.3,
                        transform=ccrs.PlateCarree(), zorder=2)
    elif kmz_wind_initial and os.path.exists(kmz_wind_initial):
        wr_geoms = _parse_kmz(kmz_wind_initial)
        for i, (wr_lons, wr_lats) in enumerate(wr_geoms):
            if len(wr_lons) < 3:
                continue
            c = wr_colors[i % len(wr_colors)]
            ax.fill(wr_lons, wr_lats, color=c[0], alpha=c[1],
                    edgecolor=c[0], linewidth=0.3,
                    transform=ccrs.PlateCarree(), zorder=2)

    # Danger swath (pre-parsed JTWC) or KMZ cone
    _danger_swath = kwargs.get("danger_swath", [])
    if _danger_swath and not probability_circles:
        _ds_lons = [c[0] for c in _danger_swath]
        _ds_lats = [c[1] for c in _danger_swath]
        if len(_ds_lons) >= 3:
            ax.fill(_ds_lons, _ds_lats, color='#60a5fa', alpha=0.15,
                    transform=ccrs.PlateCarree(), zorder=50)
            ax.plot(_ds_lons + [_ds_lons[0]], _ds_lats + [_ds_lats[0]], color='#60a5fa', linewidth=1.0, alpha=0.6, linestyle='--',
                    transform=ccrs.PlateCarree(), zorder=51)
    elif kmz_cone and os.path.exists(kmz_cone) and not probability_circles:
        cone_geoms = _parse_kmz(kmz_cone)
        for c_lons, c_lats in cone_geoms:
            if len(c_lons) < 3:
                continue
            fill_c = '#60a5fa'
            fill_a = 0.15
            out_c = '#60a5fa'
            out_ls = '--'
            out_lw = 1.0
            z = 50
            ax.fill(c_lons, c_lats, color=fill_c, alpha=fill_a,
                    transform=ccrs.PlateCarree(), zorder=z)
            ax.plot(c_lons + [c_lons[0]], c_lats + [c_lats[0]], color=out_c, linewidth=out_lw, alpha=0.6, linestyle=out_ls,
                    transform=ccrs.PlateCarree(), zorder=z + 1)

    # Best track — PAGASA-style alternating blue dots
    _bt_points = kwargs.get("best_track_points", [])
    _bt_line = kwargs.get("best_track_line", [])
    _bt_lons = [p["lon"] for p in _bt_points if p.get("lon") is not None]
    _bt_lats = [p["lat"] for p in _bt_points if p.get("lat") is not None]
    if not _bt_lons and _bt_line:
        _bt_lons = [c[0] for c in _bt_line]
        _bt_lats = [c[1] for c in _bt_line]
    if _bt_lons:
        if _bt_line and len(_bt_line) >= 2:
            _btl_lons = [c[0] for c in _bt_line]
            _btl_lats = [c[1] for c in _bt_line]
            ax.plot(_btl_lons, _btl_lats, color='#42A5F5', linewidth=1.5, linestyle='-',
                    alpha=0.5, transform=ccrs.PlateCarree(), zorder=4)
        elif len(_bt_lons) >= 2:
            ax.plot(_bt_lons, _bt_lats, color='#42A5F5', linewidth=1.5, linestyle='-',
                    alpha=0.5, transform=ccrs.PlateCarree(), zorder=4)
        for _bti in range(len(_bt_lons)):
            _blon, _blat = _bt_lons[_bti], _bt_lats[_bti]
            if _bti % 2 == 0:
                ax.plot(_blon, _blat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5',
                        markeredgewidth=0.5)
            else:
                ax.plot(_blon, _blat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5',
                        markeredgewidth=0.5, markerfacecolor='white')
                ax.plot(_blon, _blat, 'o', color='#42A5F5', markersize=3,
                        transform=ccrs.PlateCarree(), zorder=7, markeredgecolor='#42A5F5',
                        markeredgewidth=0)

    # Probability circle styling
    for circ in probability_circles:
        center = circ.get("center")
        clon = circ.get("center_lon")
        clat = circ.get("center_lat")
        if clon is None and center is not None:
            clon = center[1] if len(center) > 1 else None
        if clat is None and center is not None:
            clat = center[0] if len(center) > 0 else None
        radius_m = circ.get("radius_m") or circ.get("radius")
        if clon is None or clat is None or not radius_m:
            continue
        if cone_method == "smooth":
            pass

    # First future index not used for monwatch, keep at len
    first_future_idx = len(track_points)

    # KMZ track
    kmz_track_lons = []
    kmz_track_lats = []
    kmz_point_pts = []
    if kmz_track and os.path.exists(kmz_track):
        track_geoms = _parse_kmz(kmz_track)
        best = max(track_geoms, key=lambda g: len(g[0])) if track_geoms else None
        if best:
            kmz_track_lons, kmz_track_lats = best
        kmz_point_pts = [(lons[0], lats[0]) for lons, lats in track_geoms if len(lons) == 1]
        if kmz_track_lons and len(kmz_track_lons) >= 2:
            ax.plot(kmz_track_lons, kmz_track_lats, linewidth=3,
                    transform=ccrs.PlateCarree(), zorder=4, alpha=0.15)
            ax.plot(kmz_track_lons, kmz_track_lats, linewidth=1.5,
                    transform=ccrs.PlateCarree(), zorder=4, alpha=0.3)
            ax.plot(kmz_track_lons, kmz_track_lats, color='#00fff2', linewidth=1.0,
                    transform=ccrs.PlateCarree(), zorder=5)

    # Track line (non-KMZ)
    if len(track_points) >= 2 and not kmz_track_lons:
        tlons = [p["lon"] for p in track_points if p.get("lon") is not None]
        tlats = [p["lat"] for p in track_points if p.get("lat") is not None]
        if tlons:
            _ptlons = _adjust_lons_for_plot(tlons, central_lon)
            pwards_track_color = '#00fff2'
            pwards_glow_color = '#00fff2'
            ax.plot(_ptlons, tlats, linewidth=3,
                    transform=ccrs.PlateCarree(), zorder=4, alpha=0.15)
            ax.plot(_ptlons, tlats, linewidth=1.5,
                    transform=ccrs.PlateCarree(), zorder=4, alpha=0.3)
            ax.plot(_ptlons, tlats, color=pwards_track_color, linewidth=1.0,
                    transform=ccrs.PlateCarree(), zorder=5)

    # Label styles
    if forecast_layout == "monwatch":
        label_fs = round(10 * dpi_scale, 1)
        lb_bbox = None
        label_color = '#ffffff'
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#ffffff')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_visible(False)
        ax.spines['left'].set_visible(False)

    # Build labels
    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")
    all_labels = _build_track_labels(track_points, forecast_layout, show_dt, show_wind,
                                     time_fmt, utc_offset, wind_unit, issued_dtg)

    # Bezier/label placement algorithm setup
    skip = [False] * len(track_points)
    sym_dir = script_dir.parent / "public" / "images" / "symbols"
    _fig_w_px = figsize[0] * dpi
    _fig_h_px = figsize[1] * dpi
    _ax_w_px = _fig_w_px * 0.96
    _ax_h_px = _fig_h_px * 0.90
    _lon_r = lon_max - lon_min
    _lat_r = lat_max - lat_min
    _px_p_deg_lon = _ax_w_px / _lon_r if _lon_r > 0 else 100
    _px_p_deg_lat = _ax_h_px / _lat_r if _lat_r > 0 else 100
    _pts_p_px = 72.0 / dpi
    bezier_offsets = _compute_bezier_offsets(track_points, reverse_side=(algorithm == "bezier"), initial_offset=30 if algorithm == "bezier" else 60) if algorithm in ("bezier", "smart_bezier") else None
    if algorithm in _NEW_ALGO_NAMES:
        _new_result = _compute_unified_offsets(
            track_points, algorithm, _px_p_deg_lon, _px_p_deg_lat,
            lon_min, lat_min, _pts_p_px, all_labels, label_fs,
            probability_circles, _fig_w_px, _fig_h_px)
        if _new_result is not None:
            bezier_offsets = _new_result
    _lons = [p.get("lon") for p in track_points]
    _lats = [p.get("lat") for p in track_points]
    _curvatures = None
    if algorithm in ("polar", "zigzag") and len(track_points) >= 2:
        _curvatures = _compute_curvatures(_lons, _lats)
    _cone_radii = {}
    for circ in probability_circles:
        _clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
        _clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
        _rm = circ.get("radius_m") or circ.get("radius")
        if _clon is not None and _clat is not None and _rm:
            _cone_radii[f"{float(_clat):.4f},{float(_clon):.4f}"] = float(_rm)

    if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
        bezier_offsets = _cascade_bezier_offsets(
            bezier_offsets, all_labels, label_fs, track_points,
            _px_p_deg_lon, _px_p_deg_lat, lon_min, lat_min,
            pts_p_px=_pts_p_px, cone_radii=_cone_radii,
            max_dist=30 if algorithm == "bezier" else 200
        )
        if algorithm == "smart_bezier" and bezier_offsets:
            _curvatures = _compute_curvatures(_lons, _lats)
            max_curve = max(abs(c) for c in _curvatures) if _curvatures else 0
            cramped = False
            for j in range(len(bezier_offsets) - 1):
                d = math.hypot(
                    bezier_offsets[j][0][0] - bezier_offsets[j+1][0][0],
                    bezier_offsets[j][0][1] - bezier_offsets[j+1][0][1]
                )
                if d < 50:
                    cramped = True
                    break
            if cramped and max_curve > 0.05:
                bezier_offsets = [
                    ((-ox, -oy), 'left' if ha == 'right' else 'right')
                    for (ox, oy), ha in bezier_offsets
                ]

    # Expand map boundary if labels extend too close to edge
    _threshold_deg = 2.0
    _expand_deg = 5.0
    _need_left = _need_right = _need_bottom = _need_top = False
    if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
        for _bi, (_boff, _bha) in enumerate(bezier_offsets):
            if _bi >= len(track_points):
                break
            _bp = track_points[_bi]
            _blon = _bp.get("lon")
            _blat = _bp.get("lat")
            if _blon is None or _blat is None:
                continue
            _bol = _boff[0] / max(_pts_p_px * _px_p_deg_lon, 1e-9)
            _boa = _boff[1] / max(_pts_p_px * _px_p_deg_lat, 1e-9)
            _bllon = _blon + _bol
            _bllat = _blat + _boa
            if _bllon < lon_min + _threshold_deg: _need_left = True
            if _bllon > lon_max - _threshold_deg: _need_right = True
            if _bllat < lat_min + _threshold_deg: _need_bottom = True
            if _bllat > lat_max - _threshold_deg: _need_top = True
    else:
        for _bi, _bp in enumerate(track_points):
            _blon = _bp.get("lon")
            _blat = _bp.get("lat")
            if _blon is None or _blat is None:
                continue
            _br_m = _cone_radii.get(f"{_blat:.4f},{_blon:.4f}", 0)
            if _br_m > 0:
                _bcos = math.cos(math.radians(_blat))
                _br_deg = _br_m / (111320.0 * max(_bcos, 0.01))
                _boff_px = _br_deg * min(_px_p_deg_lon, _px_p_deg_lat) * 1.5
                _bbase = max(round(_boff_px * _pts_p_px), 30)
                _bx_off, _by_off = _bbase, round(_bbase * 0.6)
            else:
                _bx_off, _by_off = 24, 14
            _bstair = round(_bi * 5 * _pts_p_px)
            if algorithm in ("polar", "zigzag") and _curvatures:
                _bcross = _curvatures[_bi] if _bi < len(_curvatures) else 0
                _bside_right = _bcross > 0 if abs(_bcross) > 0.03 else _bi % 2 == 0
            else:
                _bside_right = _bi % 2 == 0
            if _bside_right:
                _bol, _boa = -_bx_off, -_by_off - _bstair
            else:
                _bol, _boa = _bx_off, _by_off - _bstair
            _bol = _bol / max(_pts_p_px * _px_p_deg_lon, 1e-9)
            _boa = _boa / max(_pts_p_px * _px_p_deg_lat, 1e-9)
            _bllon = _blon + _bol
            _bllat = _blat + _boa
            if _bllon < lon_min + _threshold_deg: _need_left = True
            if _bllon > lon_max - _threshold_deg: _need_right = True
            if _bllat < lat_min + _threshold_deg: _need_bottom = True
            if _bllat > lat_max - _threshold_deg: _need_top = True
    if _need_left: lon_min -= _expand_deg
    if _need_right: lon_max += _expand_deg
    if _need_bottom: lat_min -= _expand_deg
    if _need_top: lat_max += _expand_deg
    lat_min = max(lat_min, -90)
    lat_max = min(lat_max, 90)
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # Wind radii
    _draw_wind_radii(ax, track_points, dpi_scale, forecast_layout)

    # Render points and labels
    render_pts = []
    if kmz_point_pts:
        for kmz_lon, kmz_lat in kmz_point_pts:
            best_idx = 0
            best_d = float('inf')
            for ti, tp in enumerate(track_points):
                tl = tp.get("lon")
                ta = tp.get("lat")
                if tl is None or ta is None:
                    continue
                d = (kmz_lon - tl) ** 2 + (kmz_lat - ta) ** 2
                if d < best_d:
                    best_d = d
                    best_idx = ti
            render_pts.append((kmz_lon, kmz_lat, best_idx))
    else:
        for ti, tp in enumerate(track_points):
            tl = tp.get("lon")
            ta = tp.get("lat")
            if tl is None or ta is None:
                continue
            render_pts.append((tl, ta, ti))

    # PAGASA best track detection
    is_pagasa = any(p.get("cyclone_type") is not None for p in track_points)
    is_bt = []
    if is_pagasa:
        for ti in range(len(track_points)):
            r = track_points[ti].get("radius_km", 0)
            if r == 0 and ti + 1 < len(track_points):
                nr = track_points[ti+1].get("radius_km", 0)
                is_bt.append(nr == 0)
            else:
                is_bt.append(False)

    for i, (pt_lon, pt_lat, tp_idx) in enumerate(render_pts):
        p = track_points[tp_idx]
        intensity = p.get("intensity")
        pcat = p.get("intensity_category", "")
        if p.get("cyclone_type", "") == "AA":
            continue
        is_old = is_pagasa and tp_idx < len(is_bt) and is_bt[tp_idx]
        if is_old:
            bt_idx = sum(1 for j in range(tp_idx) if j < len(is_bt) and is_bt[j])
            if bt_idx % 2 == 0:
                ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5',
                        markeredgewidth=0.5)
            else:
                ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5',
                        markeredgewidth=0.5, markerfacecolor='white')
                ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=3,
                        transform=ccrs.PlateCarree(), zorder=7, markeredgecolor='#42A5F5',
                        markeredgewidth=0)
            continue
        used_symbol = False
        if forecast_layout == "monwatch" and i == 0:
            ax.plot(pt_lon, pt_lat, 'o', color='#03fcfc', markersize=12,
                    transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                    markeredgewidth=1.5, alpha=0.8)
        if pcat:
            sym_name = _category_to_pag_sym(pcat, intensity, basin=_basin)
            if sym_name and forecast_layout == "monwatch":
                if not sym_name.startswith("pagcat"):
                    sym_name = sym_name.replace("pag", "")
            if sym_name:
                sym_path = sym_dir / f"{sym_name}.png"
                if sym_path.exists():
                    try:
                        sym_pil = Image.open(str(sym_path)).convert("RGBA")
                        sym_w, sym_h = sym_pil.size
                        target_sz = round(32 * dpi_scale) if "cat" in sym_name else round(14 * dpi_scale)
                        scale = target_sz / max(sym_w, sym_h)
                        new_w, new_h = max(1, int(sym_w * scale)), max(1, int(sym_h * scale))
                        sym_pil = sym_pil.resize((new_w, new_h), Image.LANCZOS)
                        sym_arr = np.array(sym_pil)
                        oi = OffsetImage(sym_arr, zoom=1, resample=True)
                        ab = AnnotationBbox(oi, (pt_lon, pt_lat), frameon=False,
                                            xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
                                            box_alignment=(0.5, 0.5), zorder=7)
                        ax.add_artist(ab)
                        used_symbol = True
                    except Exception:
                        pass
            if not used_symbol:
                if forecast_layout == "monwatch" and i == 0:
                    ax.plot(pt_lon, pt_lat, 'o', color='#03fcfc', markersize=10,
                            transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                            markeredgewidth=1.0)
                else:
                    mk = _cat_color(intensity)
                    sz = 9 if intensity is not None and intensity >= 64 else 7
                    ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                            transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                            markeredgewidth=0.5)
        if not all_labels[tp_idx] or skip[tp_idx]:
            continue
        _key = f"{pt_lat:.4f},{pt_lon:.4f}"
        _r_m = _cone_radii.get(_key, 0)
        if _r_m > 0:
            _cos = math.cos(math.radians(pt_lat))
            _r_deg = _r_m / (111320.0 * max(_cos, 0.01))
            _off_px = _r_deg * min(_px_p_deg_lon, _px_p_deg_lat) * 1.5
            _base = max(round(_off_px * _pts_p_px), 30)
            _x_off = _base
            _y_off = round(_base * 0.6)
        else:
            _x_off = 24
            _y_off = 14
        _stair_pts = round(i * 5 * _pts_p_px)
        if algorithm in ("polar", "zigzag"):
            cross = _curvatures[tp_idx] if _curvatures else 0
            if abs(cross) > 0.03:
                side_right = cross > 0
            else:
                side_right = i % 2 == 0
            if side_right:
                xytext = (-_x_off, -_y_off - _stair_pts)
                ha = 'right'
            else:
                xytext = (_x_off, _y_off - _stair_pts)
                ha = 'left'
        elif bezier_offsets and tp_idx < len(bezier_offsets):
            if algorithm == "bezier":
                xytext = (bezier_offsets[tp_idx][0][0], bezier_offsets[tp_idx][0][1])
                ha = bezier_offsets[tp_idx][1]
            else:
                xytext = (bezier_offsets[tp_idx][0][0], bezier_offsets[tp_idx][0][1] - _stair_pts)
                ha = bezier_offsets[tp_idx][1]
        else:
            xytext = (_x_off, _y_off) if (i % 2 == 1) else (-_x_off, -_y_off)
            ha = 'left' if xytext[0] > 0 else 'right'
        pe = [path_effects.Stroke(linewidth=3.0, foreground='black'),
              path_effects.Normal()]
        ax.annotate("", (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    arrowprops={**lb_arrow, 'relpos': (0 if ha == 'left' else 1, 1)},
                    transform=ccrs.PlateCarree(), zorder=5)
        ax.annotate(all_labels[tp_idx], (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    fontsize=label_fs, color=label_color, ha=ha,
                    bbox=lb_bbox,
                    path_effects=pe,
                    transform=ccrs.PlateCarree(), zorder=90)
    
    # Legend
    if forecast_layout == "monwatch":
        legend_path = sym_dir / "monleg.png"
    elif forecast_layout in ("pagasa", "pwards"):
        legend_path = sym_dir / "pagleg.jpg"
    elif forecast_layout == "jtwc":
        legend_path = None
    else:
        legend_path = sym_dir / "final_track.png"
    if legend_path and legend_path.exists():
        try:
            leg_pil = Image.open(str(legend_path)).convert("RGBA")
            leg_w, leg_h = leg_pil.size
            leg_target_h = round(150 * dpi_scale)
            leg_scale = leg_target_h / leg_h
            leg_new_w, leg_new_h = max(1, int(leg_w * leg_scale)), max(1, int(leg_h * leg_scale))
            leg_pil = leg_pil.resize((leg_new_w, leg_new_h), Image.LANCZOS)
            leg_arr = np.array(leg_pil)
            leg_pos = fcst_prefs.get("legend_position", "bottom_right")
            pos_map = {
                "bottom_left":  (0.01, 0.01, 0, 0),
                "top_left":     (0.01, 0.99, 0, 1),
                "top_right":    (0.99, 0.99, 1, 1),
                "bottom_right": (0.99, 0.01, 1, 0),
            }
            lx, ly, ba_x, ba_y = pos_map.get(leg_pos, pos_map["bottom_left"])
            oi = OffsetImage(leg_arr, zoom=1, resample=False)
            ab = AnnotationBbox(oi, (lx, ly), frameon=False,
                                 xycoords=ax.transAxes,
                                 box_alignment=(ba_x, ba_y), zorder=10)
            ax.add_artist(ab)
        except Exception:
            pass

    # Title: no matplotlib title for monwatch
    pass

    # Save figure to PIL
    pil_img = _fig_to_pil(fig, dpi, facecolor)

    # MonWatch-UI PIL title box (no logos beside title box)
    pil_img = _draw_pil_title_box(pil_img, storm_name, track_points, dpi_scale, dpi, logo_path, "monwatch", utc_offset, source=_src, source_info=_title_info, basin=_basin)

    # Place MonWatch logo on top left (10% border margin, 3x size)
    try:
        _monwatch_path = Path(__file__).resolve().parent.parent.parent / "public" / "images" / "MonWatch-wmark.png"
        if _monwatch_path.exists():
            _mw_logo = Image.open(str(_monwatch_path)).convert("RGBA")
            _mw_h = (int(pil_img.size[1] * 0.05) + 30) * 3
            _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
            _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
            _iw, _ih = pil_img.size
            pil_img.paste(_mw_logo, (int(_iw * 0.01), 0), _mw_logo)
    except Exception:
        pass

    # Disclaimer: skip for monwatch
    pil_img = _add_disclaimer(pil_img, forecast_layout)

    # Footer: monwatch does NOT use standard footer (handled above)
    pil_img = _add_forecast_footer(pil_img, track_points, storm_id, logo_path, settings, forecast_layout, light)

    if filename:
        pil_img.save(filename)
        return filename
    return np.array(pil_img)


def make_combined_forecast(all_tracks, settings=None, logo_path=None, light=True, filename=None,
                           dpi=150, figsize=(12, 10), margin=8.0, algorithm="polar", custom_title="",
                           resolution="10m", **kwargs):
    global _LAND, _OCEAN, _COASTLINE, _BORDERS
    try:
        _LAND = cfeature.NaturalEarthFeature('physical', 'land', resolution,
            facecolor=cfeature.COLORS['land'], edgecolor='face')
    except Exception:
        _LAND = cfeature.LAND
    try:
        _OCEAN = cfeature.NaturalEarthFeature('physical', 'ocean', resolution,
            facecolor=cfeature.COLORS['water'], edgecolor='face')
    except Exception:
        _OCEAN = cfeature.OCEAN
    try:
        _COASTLINE = cfeature.NaturalEarthFeature('physical', 'coastline', resolution)
    except Exception:
        _COASTLINE = cfeature.COASTLINE
    try:
        _BORDERS = cfeature.NaturalEarthFeature('cultural', 'admin_0_boundary_lines_land', resolution,
            edgecolor='face', facecolor='none')
    except Exception:
        _BORDERS = cfeature.BORDERS
    TRACK_COLORS = ['#FF6B35', '#00BFFF', '#FFD700', '#00FF9F', '#FF69B4']
    dpi_scale = dpi / 150.0
    if not all_tracks:
        _log("[fcst] SKIP: no tracks", file=sys.stderr)
        return None
    fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
    figsize = (24, 13.5)
    # Widen figure if logo would be too close to title box
    _iw_est = figsize[0] * dpi
    _ih_est = figsize[1] * dpi
    _mw_w_est = ((int(_ih_est * 0.05) + 30) * 3) * 2
    _logo_right_est = int(_iw_est * 0.01) + _mw_w_est
    _gap_est = int(_iw_est * 0.075) - _logo_right_est
    if _gap_est < 50:
        figsize = (figsize[0] + (50 - _gap_est) / dpi, figsize[1])
    ext = _compute_combined_extent(all_tracks, "monwatch", margin, dpi, figsize, logo_path)
    if ext[0] is None:
        return None
    lon_min, lon_max, lat_min, lat_max, central_lon = ext
    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor='white' if light else '#1a1a2e')
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # MonWatch-UI combined map setup (dark theme)
    _log(f"[fcst]   monwatch: sea=#3b4b5b land=#607d8b land_outline=#03fcfc grid=#343434(2px dashed)", file=sys.stderr)
    fig.patch.set_facecolor('#3b4b5b')
    ax.set_facecolor('#3b4b5b')
    ax.add_feature(_OCEAN, facecolor='#3b4b5b', alpha=1.0, zorder=0)
    ax.add_feature(_LAND, edgecolor='#03fcfc', linewidth=0.5, alpha=1, facecolor='#607d8b', zorder=1)
    _add_psgc_ph_land(ax, facecolor='#607d8b', edgecolor='#03fcfc', linewidth=0.5, zorder=2)
    ax.add_feature(_BORDERS, edgecolor='#3b4b5b', linewidth=1, linestyle=':', zorder=2)
    ax.add_feature(cfeature.STATES.with_scale('10m'), linewidth=0.5, edgecolor='#2a3a3a', alpha=0.6, zorder=2)
    _add_psgc_boundaries(ax, edgecolor='#3b4b5b', linewidth=0.5, linestyle=':', zorder=3)
    gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                      linewidth=1.5, color='#666666', alpha=0.7, linestyle='--')
    gl.top_labels = True; gl.right_labels = True; gl.left_labels = True; gl.bottom_labels = True
    gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', 'weight': 'bold'}
    gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', 'rotation': 90, 'weight': 'bold'}
    gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
    gl.xpadding = 2; gl.ypadding = 2
    label_fs = round(10 * dpi_scale, 1)
    lb_bbox = None
    label_color = '#ffffff'
    lb_arrow = dict(arrowstyle='-', lw=0.8, color='#ffffff')
    pe = [path_effects.Stroke(linewidth=3.0, foreground='black'), path_effects.Normal()]

    # PAR red dashed
    par_pts = [(115.0, 5.0), (115.0, 15.0), (120.0, 21.0), (120.0, 25.0), (135.0, 25.0), (135.0, 5.0)]
    par_lons = [p[0] for p in par_pts]
    par_lats = [p[1] for p in par_pts]
    ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
            color="#FF3333", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=1)

    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")
    cone_method = fcst_prefs.get("cone_method", "smooth")
    show_labels = fcst_prefs.get("show_labels", True)

    # Pre-compute cone paths for bezier collision
    _comb_cone_paths = []
    _comb_track_pts = []
    for _tidx, _t in enumerate(all_tracks):
        _pts = _t.get("track_points", [])
        _pcs = _t.get("probability_circles", [])
        _comb_track_pts.append([(p.get("lon"), p.get("lat")) for p in _pts if p.get("lon") is not None and p.get("lat") is not None])
        _cp = None
        if _pcs and len(_pts) >= 2:
            try:
                if cone_method == "union":
                    from shapely.geometry import Point as ShapelyPoint
                    from shapely.ops import unary_union
                    _rl = sum(p.get("lat") for p in _pts if p.get("lat") is not None) / max(sum(1 for p in _pts if p.get("lat") is not None), 1)
                    _rln = sum(p.get("lon") for p in _pts if p.get("lon") is not None) / max(sum(1 for p in _pts if p.get("lon") is not None), 1)
                    _cr = math.cos(math.radians(_rl))
                    def _txy(lon, lat):
                        return ((lon - _rln) * 111320.0 * _cr, (lat - _rl) * 111320.0)
                    def _fxy(x, y):
                        return (_rln + x / (111320.0 * _cr), _rl + y / 111320.0)
                    _cc = []
                    for _c in _pcs:
                        _clon = _c.get("center_lon") or (_c.get("center")[1] if _c.get("center") and len(_c["center"]) > 1 else None)
                        _clat = _c.get("center_lat") or (_c.get("center")[0] if _c.get("center") and len(_c["center"]) > 0 else None)
                        _rm = _c.get("radius_m") or _c.get("radius")
                        if _clon is not None and _clat is not None and _rm:
                            _cc.append(ShapelyPoint(_txy(_clon, _clat)).buffer(_rm, resolution=36))
                    if _cc:
                        _mg = unary_union(_cc)
                        if _mg and not _mg.is_empty:
                            _pxy = _mg.exterior.coords[:]
                            _plon = [_fxy(x, y)[0] for x, y in _pxy]
                            _plat = [_fxy(x, y)[1] for x, y in _pxy]
                            if _plon and _plat:
                                from matplotlib.path import Path as MPath
                                _cp = MPath(list(zip(_plon, _plat)))
                else:
                    _plon2, _plat2 = _build_cone_polygon(_pts, _pcs)
                    if _plon2 and _plat2:
                        from matplotlib.path import Path as MPath
                        _cp = MPath(list(zip(_plon2, _plat2)))
            except Exception:
                _cp = None
        _comb_cone_paths.append(_cp)

    sym_dir = script_dir.parent / "public" / "images" / "symbols"
    legend_handles = []
    for idx, t in enumerate(all_tracks):
        pts = t.get("track_points", [])
        probability_circles = t.get("probability_circles", [])
        if not pts:
            continue
        storm_name = t.get("storm_name", t.get("storm_id", f"Track {idx+1}"))
        source = t.get("source", "")
        color = TRACK_COLORS[idx % len(TRACK_COLORS)]
        tlons = [p["lon"] for p in pts if p.get("lon") is not None]
        tlats = [p["lat"] for p in pts if p.get("lat") is not None]
        if len(tlons) < 2:
            continue

        # Cone
        if probability_circles and cone_method == "union":
            try:
                from shapely.geometry import Point as ShapelyPoint
                from shapely.ops import unary_union
                ref_lat = sum(tlats) / len(tlats)
                ref_lon = sum(tlons) / len(tlons)
                cos_ref = math.cos(math.radians(ref_lat))
                def _to_xy(lon, lat):
                    return ((lon - ref_lon) * 111320.0 * cos_ref, (lat - ref_lat) * 111320.0)
                def _from_xy(x, y):
                    return (ref_lon + x / (111320.0 * cos_ref), ref_lat + y / 111320.0)
                circles = []
                for circ in probability_circles:
                    clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
                    clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
                    rm = circ.get("radius_m") or circ.get("radius")
                    if clon is not None and clat is not None and rm:
                        circles.append(ShapelyPoint(_to_xy(clon, clat)).buffer(rm, resolution=36))
                if circles:
                    merged = unary_union(circles)
                    if merged and not merged.is_empty:
                        poly_xy = merged.exterior.coords[:]
                        poly_lons = [_from_xy(x, y)[0] for x, y in poly_xy]
                        poly_lats = [_from_xy(x, y)[1] for x, y in poly_xy]
                        _cz = 50
                        _ca = 0.40
                        _elw = 1.5
                        _els = '-'
                        ax.fill(poly_lons, poly_lats, color=color, alpha=_ca,
                                edgecolor=color, linewidth=_elw, linestyle=_els,
                                transform=ccrs.PlateCarree(), zorder=_cz)
            except Exception:
                pass
        elif probability_circles:
            poly_lons, poly_lats = _build_cone_polygon(pts, probability_circles)
            if poly_lons and poly_lats:
                _cz = 50
                _ca = 0.40
                _elw = 1.5
                _els = '-'
                ax.fill(poly_lons, poly_lats, color=color, alpha=_ca,
                        edgecolor=color, linewidth=_elw, linestyle=_els,
                        transform=ccrs.PlateCarree(), zorder=_cz)

        # Wind initial radii — pre-parsed (JTWC/NHC-style) or KMZ re-parse
        _wr_polys = t.get("wind_radii_polygons", {})
        _kmz_wind_initial = t.get("kmz_wind_initial", "")
        wr_colors = [('#00C800', 0.20), ('#FFA500', 0.25), ('#FF3232', 0.30)]
        _wr_has_data = any(_wr_polys.get(_kt) for _kt in ("34", "50", "64"))
        if _wr_has_data:
            for _ki, _kt in enumerate(("34", "50", "64")):
                for _coords in _wr_polys.get(_kt, []):
                    _lons = [c[0] for c in _coords]
                    _lats = [c[1] for c in _coords]
                    if len(_lons) < 3: continue
                    ax.fill(_lons, _lats, color=wr_colors[_ki][0], alpha=wr_colors[_ki][1],
                            edgecolor=wr_colors[_ki][0], linewidth=0.3,
                            transform=ccrs.PlateCarree(), zorder=2)
        elif _kmz_wind_initial and os.path.exists(_kmz_wind_initial):
            wr_geoms = _parse_kmz(_kmz_wind_initial)
            for i, (wr_lons, wr_lats) in enumerate(wr_geoms):
                if len(wr_lons) < 3: continue
                c = wr_colors[i % len(wr_colors)]
                ax.fill(wr_lons, wr_lats, color=c[0], alpha=c[1],
                        edgecolor=c[0], linewidth=0.3,
                        transform=ccrs.PlateCarree(), zorder=2)

        # Danger swath (pre-parsed JTWC) or KMZ cone
        _danger_swath = t.get("danger_swath", [])
        _kmz_cone = t.get("kmz_cone", "")
        print(f"DEBUG combined: _danger_swath={len(_danger_swath)}, prob_circles={bool(probability_circles)}, source={source}")
        if _danger_swath and not probability_circles:
            _ds_lons = [c[0] for c in _danger_swath]
            _ds_lats = [c[1] for c in _danger_swath]
            print(f"DEBUG combined: _ds_lons={len(_ds_lons)}, _ds_lats={len(_ds_lats)}")
            if len(_ds_lons) >= 3:
                ax.fill(_ds_lons, _ds_lats, color=color, alpha=0.15,
                        transform=ccrs.PlateCarree(), zorder=50)
                ax.plot(_ds_lons + [_ds_lons[0]], _ds_lats + [_ds_lats[0]],
                        color=color, linewidth=1.0, alpha=0.6, linestyle='--',
                        transform=ccrs.PlateCarree(), zorder=51)
        elif _kmz_cone and os.path.exists(_kmz_cone) and not probability_circles:
            cone_geoms = _parse_kmz(_kmz_cone)
            for c_lons, c_lats in cone_geoms:
                if len(c_lons) < 3: continue
                ax.fill(c_lons, c_lats, color=color, alpha=0.15,
                        transform=ccrs.PlateCarree(), zorder=50)
                ax.plot(c_lons + [c_lons[0]], c_lats + [c_lats[0]],
                        color=color, linewidth=1.0, alpha=0.6, linestyle='--',
                        transform=ccrs.PlateCarree(), zorder=51)

        # Track line
        track_tlons, track_tlats = tlons, tlats
        if source in ("nhc", "jtwc"):
            kmz_track = t.get("kmz_track", "")
            if isinstance(kmz_track, list) and kmz_track:
                # Pre-parsed track line from JTWC KMZ
                track_tlons, track_tlats = zip(*kmz_track) if kmz_track else (tlons, tlats)
            elif kmz_track and os.path.exists(kmz_track):
                try:
                    track_geoms = _parse_kmz(kmz_track)
                    best = max(track_geoms, key=lambda g: len(g[0])) if track_geoms else None
                    if best:
                        track_tlons, track_tlats = best
                except Exception:
                    pass
        track_tlons = _adjust_lons_for_plot(list(track_tlons), central_lon)
        first_future_idx = len(pts)
        if forecast_layout == "pagasa" and probability_circles:
            _circ_centers = []
            for _c in probability_circles:
                _clon = _c.get("center_lon") or (_c.get("center")[1] if _c.get("center") and len(_c["center"]) > 1 else None)
                _clat = _c.get("center_lat") or (_c.get("center")[0] if _c.get("center") and len(_c["center"]) > 0 else None)
                if _clon is not None and _clat is not None:
                    _circ_centers.append((_clat, _clon))
            for _i, _p in enumerate(pts):
                _plon = _p.get("lon")
                _plat = _p.get("lat")
                if _plon is not None and _plat is not None:
                    for _cc in _circ_centers:
                        if abs(_plat - _cc[0]) < 0.01 and abs(_plon - _cc[1]) < 0.01:
                            first_future_idx = _i - 1
                            break
                if first_future_idx < len(pts):
                    break
        if forecast_layout == "pagasa" and first_future_idx > 0:
            past_lons2 = track_tlons[:first_future_idx]
            past_lats2 = track_tlats[:first_future_idx]
            if len(past_lons2) >= 2:
                ax.plot(past_lons2, past_lats2, color='#4488ff', linewidth=2.5, transform=ccrs.PlateCarree(), zorder=5)
            future_lons2 = track_tlons[first_future_idx:]
            future_lats2 = track_tlats[first_future_idx:]
            if len(future_lons2) >= 2:
                ax.plot(future_lons2, future_lats2, color=color, linewidth=2.5, transform=ccrs.PlateCarree(), zorder=5)
        else:
            # PWARDS glowing track for combined
            ax.plot(track_tlons, track_tlats, linewidth=3,
                    transform=ccrs.PlateCarree(), zorder=4, alpha=0.15)
            ax.plot(track_tlons, track_tlats, linewidth=1.5,
                    transform=ccrs.PlateCarree(), zorder=4, alpha=0.3)
            ax.plot(track_tlons, track_tlats, color=color, linewidth=1.0,
                    transform=ccrs.PlateCarree(), zorder=5)
        # Build labels
        issued_dtg = t.get("issued_dtg", "")
        _fig_w_px = figsize[0] * dpi
        _fig_h_px = figsize[1] * dpi
        _ax_w_px = _fig_w_px * 0.96
        _ax_h_px = _fig_h_px * 0.90
        _lon_r = lon_max - lon_min
        _lat_r = lat_max - lat_min
        _px_p_deg_lon = _ax_w_px / _lon_r if _lon_r > 0 else 100
        _px_p_deg_lat = _ax_h_px / _lat_r if _lat_r > 0 else 100
        _pts_p_px = 72.0 / dpi
        if show_labels:
            all_labels = _build_track_labels(pts, forecast_layout, show_dt, show_wind,
                                             time_fmt, utc_offset, wind_unit, issued_dtg)

            skip = [False] * len(pts)
            if len(pts) >= 2:
                bezier_offsets = _compute_bezier_offsets(pts, reverse_side=(algorithm == "bezier"), initial_offset=30 if algorithm == "bezier" else 60) if algorithm in ("bezier", "smart_bezier") else None
            else:
                bezier_offsets = None
            _cone_radii = {}
            for circ in probability_circles:
                _clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
                _clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
                _rm = circ.get("radius_m") or circ.get("radius")
                if _clon is not None and _clat is not None and _rm:
                    _cone_radii[f"{float(_clat):.4f},{float(_clon):.4f}"] = float(_rm)

            if algorithm in _NEW_ALGO_NAMES:
                _new_result = _compute_unified_offsets(
                    pts, algorithm, _px_p_deg_lon, _px_p_deg_lat,
                    lon_min, lat_min, _pts_p_px, all_labels, label_fs,
                    None, _fig_w_px, _fig_h_px)
                if _new_result is not None:
                    bezier_offsets = _new_result
            if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
                bezier_offsets = _cascade_bezier_offsets(
                    bezier_offsets, all_labels, label_fs, pts,
                    _px_p_deg_lon, _px_p_deg_lat, lon_min, lat_min,
                    pts_p_px=_pts_p_px, cone_radii=_cone_radii,
                    max_dist=30 if algorithm == "bezier" else 200
                )
                if algorithm == "smart_bezier" and bezier_offsets:
                    _clons = [p.get("lon") for p in pts]
                    _clats = [p.get("lat") for p in pts]
                    _curvatures = _compute_curvatures(_clons, _clats)
                    max_curve = max(abs(c) for c in _curvatures) if _curvatures else 0
                    cramped = False
                    for j in range(len(bezier_offsets) - 1):
                        d = math.hypot(bezier_offsets[j][0][0] - bezier_offsets[j+1][0][0],
                                       bezier_offsets[j][0][1] - bezier_offsets[j+1][0][1])
                        if d < 50:
                            cramped = True
                            break
                    if cramped and max_curve > 0.05:
                        bezier_offsets = [((-ox, -oy), 'left' if ha == 'right' else 'right')
                                          for (ox, oy), ha in bezier_offsets]

            # Expand map boundary if labels extend too close to edge
            _threshold_deg = 2.0
            _expand_deg = 5.0
            _need_left = _need_right = _need_bottom = _need_top = False
            if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
                for _bi, (_boff, _bha) in enumerate(bezier_offsets):
                    if _bi >= len(pts): break
                    _bp = pts[_bi]; _blon = _bp.get("lon"); _blat = _bp.get("lat")
                    if _blon is None or _blat is None: continue
                    _bol = _boff[0] / max(_pts_p_px * _px_p_deg_lon, 1e-9)
                    _boa = _boff[1] / max(_pts_p_px * _px_p_deg_lat, 1e-9)
                    _bllon = _blon + _bol; _bllat = _blat + _boa
                    if _bllon < lon_min + _threshold_deg: _need_left = True
                    if _bllon > lon_max - _threshold_deg: _need_right = True
                    if _bllat < lat_min + _threshold_deg: _need_bottom = True
                    if _bllat > lat_max - _threshold_deg: _need_top = True
            else:
                for _bi, _bp in enumerate(pts):
                    _blon = _bp.get("lon"); _blat = _bp.get("lat")
                    if _blon is None or _blat is None: continue
                    _br_m = _cone_radii.get(f"{_blat:.4f},{_blon:.4f}", 0)
                    if _br_m > 0:
                        _bcos = math.cos(math.radians(_blat))
                        _br_deg = _br_m / (111320.0 * max(_bcos, 0.01))
                        _boff_px = _br_deg * min(_px_p_deg_lon, _px_p_deg_lat) * 1.5
                        _bbase = max(round(_boff_px * _pts_p_px), 30)
                        _bx_off, _by_off = _bbase, round(_bbase * 0.6)
                    else: _bx_off, _by_off = 24, 14
                    _bside_right = _bi % 2 == 0
                    if _bside_right: _bol, _boa = -_bx_off, -_by_off
                    else: _bol, _boa = _bx_off, _by_off
                    _bol = _bol / max(_pts_p_px * _px_p_deg_lon, 1e-9)
                    _boa = _boa / max(_pts_p_px * _px_p_deg_lat, 1e-9)
                    _bllon = _blon + _bol; _bllat = _blat + _boa
                    if _bllon < lon_min + _threshold_deg: _need_left = True
                    if _bllon > lon_max - _threshold_deg: _need_right = True
                    if _bllat < lat_min + _threshold_deg: _need_bottom = True
                    if _bllat > lat_max - _threshold_deg: _need_top = True
            if _need_left: lon_min -= _expand_deg
            if _need_right: lon_max += _expand_deg
            if _need_bottom: lat_min -= _expand_deg
            if _need_top: lat_max += _expand_deg
            lat_min = max(lat_min, -90); lat_max = min(lat_max, 90)
            ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
        else:
            all_labels = [""] * len(pts)
            skip = [False] * len(pts)
            bezier_offsets = None
            _cone_radii = {}

        _draw_wind_radii(ax, pts, dpi_scale, forecast_layout)

        is_pagasa_track = source == "pagasa"
        is_bt = []
        if is_pagasa_track:
            for ti in range(len(pts)):
                r = pts[ti].get("radius_km", 0)
                if r == 0 and ti + 1 < len(pts):
                    nr = pts[ti+1].get("radius_km", 0)
                    is_bt.append(nr == 0)
                else:
                    is_bt.append(False)

        _sz = 50
        for i, p in enumerate(pts):
            pt_lon = p.get("lon"); pt_lat = p.get("lat")
            intensity = p.get("intensity"); pcat = p.get("intensity_category", "")
            if pt_lon is None or pt_lat is None: continue
            if p.get("cyclone_type", "") == "AA": continue
            is_old = is_pagasa_track and i < len(is_bt) and is_bt[i]
            if is_old:
                bt_idx = sum(1 for j in range(i) if j < len(is_bt) and is_bt[j])
                if bt_idx % 2 == 0:
                    ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=6,
                            transform=ccrs.PlateCarree(), zorder=_sz - 1, markeredgecolor='#42A5F5',
                            markeredgewidth=0.5)
                else:
                    ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=6,
                            transform=ccrs.PlateCarree(), zorder=_sz - 1, markeredgecolor='#42A5F5',
                            markeredgewidth=0.5, markerfacecolor='white')
                    ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=3,
                            transform=ccrs.PlateCarree(), zorder=_sz, markeredgecolor='#42A5F5',
                            markeredgewidth=0)
                continue
            used_symbol = False
            if not pcat and intensity is not None:
                if intensity >= 96: pcat = "Major Hurricane"
                elif intensity >= 64: pcat = "Hurricane"
                elif intensity >= 50: pcat = "STS"
                elif intensity >= 34: pcat = "TS"
                else: pcat = "TD"
            if pcat:
                _track_basin = t.get("basin", "")
                sym_name = _category_to_pag_sym(pcat, intensity, basin=_track_basin)
                if sym_name and forecast_layout == "monwatch":
                    if not sym_name.startswith("pagcat"):
                        sym_name = sym_name.replace("pag", "")
                if sym_name:
                    sym_path = sym_dir / f"{sym_name}.png"
                    if sym_path.exists():
                        try:
                            sym_pil = Image.open(str(sym_path)).convert("RGBA")
                            sym_w, sym_h = sym_pil.size
                            target_sz = round(32 * dpi_scale) if "cat" in sym_name else round(14 * dpi_scale)
                            scale = target_sz / max(sym_w, sym_h)
                            new_w, new_h = max(1, int(sym_w * scale)), max(1, int(sym_h * scale))
                            sym_pil = sym_pil.resize((new_w, new_h), Image.LANCZOS)
                            sym_arr = np.array(sym_pil)
                            oi = OffsetImage(sym_arr, zoom=1, resample=True)
                            ab = AnnotationBbox(oi, (pt_lon, pt_lat), frameon=False,
                                                xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
                                                box_alignment=(0.5, 0.5), zorder=_sz)
                            ax.add_artist(ab)
                            used_symbol = True
                        except Exception: pass
            if not used_symbol:
                mk = _cat_color(intensity)
                sz = 9 if intensity is not None and intensity >= 64 else 7
                ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                        transform=ccrs.PlateCarree(), zorder=_sz - 1, markeredgecolor='white', markeredgewidth=0.5)
            if not all_labels[i] or skip[i]: continue
            _key = f"{pt_lat:.4f},{pt_lon:.4f}"
            _r_m = _cone_radii.get(_key, 0)
            if _r_m > 0:
                _cos = math.cos(math.radians(pt_lat))
                _r_deg = _r_m / (111320.0 * max(_cos, 0.01))
                _off_px = _r_deg * min(_px_p_deg_lon, _px_p_deg_lat) * 1.5
                _base = max(round(_off_px * _pts_p_px), 30)
                _x_off = _base; _y_off = round(_base * 0.6)
            else: _x_off = 24; _y_off = 14
            if algorithm in ("polar", "zigzag"):
                if (i - sum(skip[:i])) % 2 == 0:
                    xytext = (-_x_off, -_y_off); ha = 'right'
                else:
                    xytext = (_x_off, _y_off); ha = 'left'
            elif bezier_offsets and i < len(bezier_offsets):
                if algorithm == "bezier":
                    xytext, ha = bezier_offsets[i]
                else:
                    if _r_m > 0:
                        _d = math.hypot(*bezier_offsets[i][0])
                        if _d > 0:
                            _s = max(_x_off, _d) / _d
                            xytext = (round(bezier_offsets[i][0][0] * _s), round(bezier_offsets[i][0][1] * _s))
                        else:
                            xytext = bezier_offsets[i][0]
                        ha = bezier_offsets[i][1]
                    else:
                        xytext, ha = bezier_offsets[i]
                if len(all_tracks) > 1 and all_labels[i]:
                    _ol = xytext[0] * _pts_p_px / max(_px_p_deg_lon, 1)
                    _oa = xytext[1] * _pts_p_px / max(_px_p_deg_lat, 1)
                    _lab_lon = pt_lon + _ol; _lab_lat = pt_lat + _oa
                    _hit = False
                    for _ot_idx in range(len(all_tracks)):
                        if _ot_idx == idx: continue
                        _cp = _comb_cone_paths[_ot_idx]
                        if _cp is not None:
                            try:
                                if _cp.contains_point((_lab_lon, _lab_lat)): _hit = True; break
                            except Exception: pass
                        if not _hit:
                            for _opl in _comb_track_pts[_ot_idx]:
                                _opl_lon, _opl_lat = _opl
                                if _opl_lon is None or _opl_lat is None: continue
                                if math.hypot(_lab_lon - _opl_lon, _lab_lat - _opl_lat) < 0.3: _hit = True; break
                        if _hit: break
                    if _hit:
                        xytext = (-xytext[0], -xytext[1])
                        ha = 'right' if xytext[0] < 0 else 'left'
            else:
                xytext = (_x_off, _y_off) if (i % 2 == 1) else (-_x_off, -_y_off)
                ha = 'left' if xytext[0] > 0 else 'right'
            ax.annotate("", (pt_lon, pt_lat),
                        textcoords="offset points", xytext=xytext,
                        arrowprops={**lb_arrow, 'relpos': (0 if ha == 'left' else 1, 1)},
                        transform=ccrs.PlateCarree(), zorder=1)
            ax.annotate(all_labels[i], (pt_lon, pt_lat),
                        textcoords="offset points", xytext=xytext,
                        fontsize=label_fs, color=label_color, ha=ha,
                        bbox=lb_bbox,
                        path_effects=pe,
                        transform=ccrs.PlateCarree(), zorder=90)
        _lab_name = _clean_storm_name(storm_name, source=t.get("source", ""))
        _lab_src = t.get("source", "").upper()
        _first_fp = _find_first_forecast_point(pts, forecast_layout)
        _lab_dt = _first_fp.get("datetime", "") if _first_fp else ""
        _lab_dt_obj = _parse_dt(_lab_dt) if _lab_dt else None
        if _lab_dt_obj:
            _h = _lab_dt_obj.hour; _m = _lab_dt_obj.minute
            if _m >= 30: _h += 1
            _hs = f"{_h % 12 or 12}{'AM' if _h < 12 else 'PM'}"
            _d = _lab_dt_obj.strftime("%d"); _mo = _lab_dt_obj.strftime("%B"); _y = str(_lab_dt_obj.year)
            _lab_label = f"{_lab_name} ({_lab_src}) | {_hs} - {_d} {_mo}, {_y}"
        else:
            _lab_label = f"{_lab_name} ({_lab_src})" if _lab_src else _lab_name
        legend_handles.append(plt.Line2D([0], [0], color=color, linewidth=2.5, label=_lab_label))

    # Legend for combined
    if legend_handles:
        leg = ax.legend(handles=legend_handles, loc='lower left', framealpha=0.85,
                        fontsize=round(8 * dpi_scale),
                        facecolor='#000000', edgecolor='gray', labelcolor='#ffffff')
        leg.set_zorder(20)

    # Title: no matplotlib title for monwatch
    pass

    pil_img = None
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor=fig.get_facecolor())
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)

    # Combined title box
    storm_names = []
    for _t in all_tracks:
        _raw = _t.get("storm_name", _t.get("storm_id", f"T{idx+1}"))
        _src = _t.get("source", "")
        intl_name = _clean_storm_name(_raw, source=_src)
        if _src:
            storm_names.append(f"{intl_name} ({_src.upper()})")
        else:
            storm_names.append(intl_name)
    names_str = ", ".join(storm_names)
    pil_img = _draw_combined_title_box(pil_img, names_str, custom_title, dpi_scale, dpi, logo_path, "monwatch", all_tracks=all_tracks)

    # Place MonWatch logo on top left (10% border margin, 3x size)
    try:
        _monwatch_path = Path(__file__).resolve().parent.parent.parent / "public" / "images" / "MonWatch-wmark.png"
        if _monwatch_path.exists():
            _mw_logo = Image.open(str(_monwatch_path)).convert("RGBA")
            _mw_h = (int(pil_img.size[1] * 0.05) + 30) * 3
            _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
            _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
            _iw, _ih = pil_img.size
            pil_img.paste(_mw_logo, (int(_iw * 0.01), 0), _mw_logo)
    except Exception:
        pass

    # Disclaimer: skip for monwatch
    pil_img = _add_disclaimer(pil_img, forecast_layout)

    # Footer: skip for monwatch
    pil_img = _add_combined_footer(pil_img, logo_path, settings, forecast_layout)

    if filename:
        pil_img.save(filename)
        return filename
    return np.array(pil_img)