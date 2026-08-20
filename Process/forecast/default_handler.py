import sys
import json
import io
import os
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

_log = print

try:
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from cartopy.mpl.gridliner import LONGITUDE_FORMATTER, LATITUDE_FORMATTER
    try:
        _LAND = cfeature.NaturalEarthFeature('physical', 'land', '10m',
            facecolor=cfeature.COLORS['land'], edgecolor='face')
    except Exception:
        _LAND = cfeature.LAND
    try:
        _OCEAN = cfeature.NaturalEarthFeature('physical', 'ocean', '10m',
            facecolor=cfeature.COLORS['water'], edgecolor='face')
    except Exception:
        _OCEAN = cfeature.OCEAN
    try:
        _COASTLINE = cfeature.NaturalEarthFeature('physical', 'coastline', '10m')
    except Exception:
        _COASTLINE = cfeature.COASTLINE
    try:
        _BORDERS = cfeature.NaturalEarthFeature('cultural', 'admin_0_boundary_lines_land', '10m',
            edgecolor='face', facecolor='none')
    except Exception:
        _BORDERS = cfeature.BORDERS
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import matplotlib.patches as mpatches
    import matplotlib.patheffects as path_effects
    import pandas as pd
    from cartopy.geodesic import Geodesic
    from matplotlib.offsetbox import OffsetImage, AnnotationBbox
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

from .utils import (
    _cat_color, _cat_label, _cat_sym, _category_to_sym,
    _parse_dt, _parse_jtwc_dtg, _is_old_point, _add_footer,
    _draw_wind_radii, _build_cone_polygon, _compute_bezier_offsets,
    _compute_curvatures, _cascade_bezier_offsets, _add_psgc_ph_land,
    _add_psgc_boundaries, _parse_kmz,
)

from .shared_flow import (
    _compute_single_extent, _compute_combined_extent,
    _build_track_labels, _find_first_future_idx, _fig_to_pil,
    _find_first_forecast_point, _storage_offset,
    _add_forecast_footer, _add_combined_footer, _add_disclaimer,
    _fetch_jma_model_tracks, _adjust_lons_for_plot, _clean_storm_name,
)

script_dir = Path(__file__).resolve().parent.parent
forecast_layout = "default"

def make_forecast(track_points, probability_circles=None, storm_name="Tropical Cyclone",
                  storm_id="", margin=8.0, dpi=150, figsize=(12, 10),
                  facecolor="white", filename=None, light=True,
                  settings=None, logo_path=None, algorithm="polar",
                    issued_dtg=None, kmz_cone="", kmz_track="", kmz_wind_initial="", **kwargs):
    _basin = kwargs.get("basin", storm_id[:2].upper() if storm_id and len(storm_id) >= 2 else "")
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

    # Extent: dateline-aware
    _dl_min, _dl_max, central_lon, _ = _normalize_track_extent(lons, margin)
    lon_min = _dl_min
    lon_max = _dl_max
    lat_min = max(min(lats) - margin, -90)
    lat_max = min(max(lats) + margin, 90)

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor=facecolor)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    _log(f"[fcst] storm={storm_name} id={storm_id} layout=default light={light} "
         f"dpi={dpi} figsize={figsize} extent=[{lon_min:.2f}, {lon_max:.2f}, {lat_min:.2f}, {lat_max:.2f}]",
         file=sys.stderr)

    # Map setup: light/dark branches (lines 952-985)
    if light:
        ax.set_facecolor('white')
        ax.add_feature(_LAND, edgecolor='gray', linewidth=0.5)
        ax.add_feature(_OCEAN, facecolor='#c8e6f5', alpha=0.5)
        ax.add_feature(_BORDERS, edgecolor='gray', linewidth=0.4, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='gray', linewidth=0.2, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.7, color='gray', alpha=0.6, linestyle='--')
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {'size': round(10 * dpi_scale, 6), 'color': 'black', "weight": "bold"}
        gl.ylabel_style = {'size': round(10 * dpi_scale, 6), 'color': 'black', "rotation": 90, "weight": "bold"}
        gl.xformatter = LONGITUDE_FORMATTER
        gl.yformatter = LATITUDE_FORMATTER
        label_color = 'black'
        label_bg = 'white'
        label_border = 'gray'
        title_color = 'black'
    else:
        ax.set_facecolor('#1a1a2e')
        ax.add_feature(_LAND, edgecolor='gray', linewidth=0.5)
        ax.add_feature(_OCEAN, facecolor='#0d1b2a', alpha=0.8)
        ax.add_feature(_BORDERS, edgecolor='#556677', linewidth=0.3, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#556677', linewidth=0.15, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.5, color='#4a6a8a', alpha=0.6, linestyle='--')
        gl.top_labels = False
        gl.right_labels = False
        gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#c0d0e0', "weight": "bold"}
        gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#c0d0e0', "rotation": 90, "weight": "bold"}
        label_color = 'white'
        label_bg = '#00000088'
        label_border = 'none'
        title_color = '#d0d0e0'

    # PAR: non-pagasa red dashed (lines 998-1007)
    par_pts = [
        (115.0, 5.0), (115.0, 15.0), (120.0, 21.0),
        (120.0, 25.0), (135.0, 25.0), (135.0, 5.0),
    ]
    par_lons = [p[0] for p in par_pts]
    par_lats = [p[1] for p in par_pts]
    ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
            color="#FF3333", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=1)

    # Probability circles – default styling (cone_color = '#60a5fa')
    geod = Geodesic()
    prob_circles = probability_circles or []

    for circ in prob_circles:
        center = circ.get("center")
        clon = circ.get("center_lon")
        clat = circ.get("center_lat")
        if clon is None and center is not None:
            clon = center[1] if len(center) > 1 else None
        if clat is None and center is not None:
            clat = center[0] if len(center) > 0 else None
        radius_m = circ.get("radius_m") or circ.get("radius")
        tangents = circ.get("tangents") or circ.get("tangent", [])
        if clon is None or clat is None or not radius_m:
            continue
        cone_color = '#60a5fa'
        circle_color = '#60a5fa'
        fill_color = '#60a5fa'
        cone_alpha = 0.55
        circle_alpha = 0.6
        fill_alpha = 0.04
        cone_linestyle = '-'
        circle_linestyle = '--'
        cone_lw = 1.5
        circle_pts = geod.circle(lon=clon, lat=clat, radius=radius_m, n_samples=36)
        c_lons = [p[0] for p in circle_pts]
        c_lats = [p[1] for p in circle_pts]
        ax.plot(c_lons + c_lons[:1], c_lats + c_lats[:1],
                color=circle_color, linewidth=1, linestyle=circle_linestyle, alpha=circle_alpha,
                transform=ccrs.PlateCarree(), zorder=2)
        ax.fill(c_lons, c_lats, color=fill_color, alpha=fill_alpha,
                transform=ccrs.PlateCarree(), zorder=2)

    # Cone from KMZ
    if kmz_cone and os.path.exists(kmz_cone) and not prob_circles:
        cone_geoms = _parse_kmz(kmz_cone)
        for c_lons, c_lats in cone_geoms:
            if len(c_lons) < 3:
                continue
            ax.fill(c_lons, c_lats, color='#60a5fa', alpha=0.15,
                    transform=ccrs.PlateCarree(), zorder=1)
            ax.plot(c_lons + [c_lons[0]], c_lats + [c_lats[0]], color='#60a5fa', linewidth=1.0, alpha=0.6, linestyle='--',
                    transform=ccrs.PlateCarree(), zorder=2)

    # Wind initial radii from KMZ
    if kmz_wind_initial and os.path.exists(kmz_wind_initial):
        wr_geoms = _parse_kmz(kmz_wind_initial)
        wr_colors = [('#00C800', 0.20), ('#FFA500', 0.25), ('#FF3232', 0.30)]
        for i, (wr_lons, wr_lats) in enumerate(wr_geoms):
            if len(wr_lons) < 3:
                continue
            c = wr_colors[i % len(wr_colors)]
            ax.fill(wr_lons, wr_lats, color=c[0], alpha=c[1],
                    edgecolor=c[0], linewidth=0.3,
                    transform=ccrs.PlateCarree(), zorder=2)

    # Track line: default colorful (lines 1350-1354)
    if len(track_points) >= 2:
        tlons = [p["lon"] for p in track_points if p.get("lon") is not None]
        tlats = [p["lat"] for p in track_points if p.get("lat") is not None]
        if tlons:
            _plons = _adjust_lons_for_plot(tlons, central_lon)
            track_color = '#ffaa44' if not light else '#00BFFF' if storm_id.startswith('ep') else '#FF6B35' if storm_id.startswith('al') else '#FFD700'
            track_lw = 2.5 if not light else 2
            ax.plot(_plons, tlats, color=track_color, linewidth=track_lw,
                    transform=ccrs.PlateCarree(), zorder=5)

    # Labels styling: default (lines 1444-1447)
    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")

    label_fs = round(6.5 * dpi_scale, 1)
    lb_bbox = dict(boxstyle='round,pad=0.15', fc=label_bg, ec=label_border, alpha=0.85)
    lb_arrow = dict(arrowstyle='-', lw=0.8, color='#666666')

    all_labels = _build_track_labels(
        track_points, "default", show_dt, show_wind,
        time_fmt, utc_offset, wind_unit, issued_dtg
    )

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
    bezier_offsets = _compute_bezier_offsets(track_points, reverse_side=True, initial_offset=30) if algorithm == "bezier" else (_compute_bezier_offsets(track_points) if algorithm == "smart_bezier" else None)
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
    for circ in prob_circles:
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
            _bstair = round(_bi * 5 * _pts_p_px)
            _bol = _boff[0] / max(_pts_p_px * _px_p_deg_lon, 1e-9)
            _boa = (_boff[1] - _bstair) / max(_pts_p_px * _px_p_deg_lat, 1e-9)
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

    if algorithm == "anticlima" and bezier_offsets:
        _a_lons, _a_lats = [], []
        for _ai, _bp in enumerate(track_points):
            if _ai >= len(bezier_offsets):
                break
            _blon = _bp.get("lon")
            _blat = _bp.get("lat")
            if _blon is None or _blat is None:
                continue
            _boff, _bha = bezier_offsets[_ai]
            _stair = round(_ai * 5 * _pts_p_px)
            _dlon = _boff[0] / (_px_p_deg_lon * _pts_p_px)
            _dlat = (_boff[1] - _stair) / (_px_p_deg_lat * _pts_p_px)
            _a_lons.append(_blon + _dlon)
            _a_lats.append(_blat + _dlat)
        if len(_a_lons) >= 2:
            ax.plot(_a_lons, _a_lats, color='#FF6600', linewidth=1.5, linestyle='--',
                    transform=ccrs.PlateCarree(), zorder=4, alpha=0.8,
                    label='_AntiClima offset path')

    _draw_wind_radii(ax, track_points, dpi_scale, "default")

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

    # Draw points and labels
    for i, p in enumerate(track_points):
        pt_lon = p.get("lon")
        pt_lat = p.get("lat")
        intensity = p.get("intensity")
        pcat = p.get("intensity_category", "")
        if pt_lon is None or pt_lat is None:
            continue
        if is_pagasa and is_bt[i]:
            bt_idx = sum(1 for j in range(i) if is_bt[j])
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
        if pcat:
            sym_name = _category_to_sym(pcat, intensity, basin=_basin)
            if sym_name:
                sym_path = sym_dir / f"{sym_name}.png"
                if sym_path.exists():
                    try:
                        sym_pil = Image.open(str(sym_path)).convert("RGBA")
                        sym_w, sym_h = sym_pil.size
                        target_sz = round(14 * dpi_scale)
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
            mk = _cat_color(intensity)
            sz = 9 if intensity is not None and intensity >= 64 else 7
            ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                    transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                    markeredgewidth=0.5)
        if skip[i] or not all_labels[i]:
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
            cross = _curvatures[i] if _curvatures else 0
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
        elif bezier_offsets and i < len(bezier_offsets):
            if algorithm == "bezier":
                xytext = (bezier_offsets[i][0][0], bezier_offsets[i][0][1])
                ha = bezier_offsets[i][1]
            else:
                xytext = (bezier_offsets[i][0][0], bezier_offsets[i][0][1] - _stair_pts)
                ha = bezier_offsets[i][1]
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
                    transform=ccrs.PlateCarree(), zorder=90)

    # Legend: use final_track.png
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

    # Title: default (lines 1875-1880)
    title_parts = [f"{storm_name}"]
    if storm_id:
        title_parts.append(f"({storm_id.upper()})")
    title_parts.append("\u2014 Forecast Track")
    ax.set_title("  ".join(title_parts), fontsize=round(12 * dpi_scale, 1) if light else 11, color=title_color, fontweight='bold' if light else 'normal')

    fig.subplots_adjust(bottom=0.06, left=0.02, right=0.98, top=0.96)
    fig.canvas.draw()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                facecolor=facecolor, edgecolor='none')
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)

    # NO PIL title box for default layout
    # Disclaimer
    _add_disclaimer(pil_img, "default")
    # Footer
    if logo_path:
        fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
        utc_offset = fcst_prefs.get("utc_offset", 0)
        forecast_dt = ""
        if track_points:
            first_dt = track_points[0].get("datetime", "")
            if first_dt:
                try:
                    fdt = _parse_dt(first_dt)
                    if fdt is not None:
                        _utc_off2 = utc_offset - _storage_offset(track_points, forecast_layout)
                        if _utc_off2 != 0:
                            fdt = fdt.replace(tzinfo=timezone.utc) + timedelta(hours=_utc_off2)
                        forecast_dt = fdt.strftime("%Y-%m-%d %H:%M") + (" UTC" if _utc_off2 == 0 else "")
                    else:
                        forecast_dt = first_dt
                except Exception:
                    forecast_dt = first_dt
        time_fmt = fcst_prefs.get("time_format", "military")
        now = datetime.now(timezone.utc)
        if utc_offset != 0:
            now = now + timedelta(hours=utc_offset)
        now_str = now.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else "")
        basin_name = ("East Pacific" if storm_id.startswith("ep")
                      else "Atlantic" if storm_id.startswith("al")
                      else "Central Pacific" if storm_id.startswith("cp")
                      else "Western Pacific")
        pil_img = _add_footer(pil_img, forecast_dt, now_str, basin_name, logo_path, utc_offset, dark=False)

    if filename:
        pil_img.save(filename)
        return filename
    return np.array(pil_img)


def make_combined_forecast(all_tracks, settings=None, logo_path=None, light=True, filename=None,
                           dpi=150, figsize=(12, 10), margin=8.0, algorithm="polar", custom_title="",
                           resolution="10m", **kwargs):
    dpi_scale = dpi / 150.0
    if not all_tracks:
        _log("[fcst] SKIP: no tracks", file=sys.stderr)
        return None

    fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
    all_lons, all_lats = [], []
    for t in all_tracks:
        for p in t.get("track_points", []):
            if p.get("lon") is not None:
                all_lons.append(p["lon"])
            if p.get("lat") is not None:
                all_lats.append(p["lat"])
    if not all_lons:
        _log("[fcst] SKIP: no valid coordinates", file=sys.stderr)
        return None

    # Default extent: dateline-aware
    _dl_min, _dl_max, central_lon, _ = _normalize_track_extent(all_lons, margin)
    _lat_center = (min(all_lats) + max(all_lats)) / 2
    _lat_half = (max(all_lats) - min(all_lats)) / 2 + margin
    _lon_half = max(_dl_max - central_lon, central_lon - _dl_min)
    _fig_aspect = figsize[0] / figsize[1]
    _ext_aspect = _lon_half / _lat_half if _lat_half > 0 else _fig_aspect
    if _ext_aspect < _fig_aspect:
        _lon_half = _lat_half * _fig_aspect
    else:
        _lat_half = _lon_half / _fig_aspect
    lon_min = central_lon - _lon_half
    lon_max = central_lon + _lon_half
    lat_min = max(_lat_center - _lat_half, -90)
    lat_max = min(_lat_center + _lat_half, 90)

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor='white' if light else '#1a1a2e')
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # Map setup: default light/dark
    if light:
        ax.set_facecolor('white')
        ax.add_feature(_LAND, edgecolor='gray', linewidth=0.5)
        ax.add_feature(_OCEAN, facecolor='#c8e6f5', alpha=0.5)
        ax.add_feature(_BORDERS, edgecolor='gray', linewidth=0.4, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='gray', linewidth=0.2, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.7, color='gray', alpha=0.6, linestyle='--')
        gl.top_labels = False; gl.right_labels = False
        gl.xlabel_style = {'size': round(10 * dpi_scale, 6), 'color': 'black', 'weight': 'bold'}
        gl.ylabel_style = {'size': round(10 * dpi_scale, 6), 'color': 'black', 'rotation': 90, 'weight': 'bold'}
        gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
        label_fs = round(6.5 * dpi_scale, 1)
        lb_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='gray', alpha=0.85)
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#666666')
        label_color = 'black'
        pe = None
    else:
        ax.set_facecolor('#1a1a2e')
        ax.add_feature(_LAND, edgecolor='gray', linewidth=0.5)
        ax.add_feature(_OCEAN, facecolor='#0d1b2a', alpha=0.8)
        ax.add_feature(_BORDERS, edgecolor='#556677', linewidth=0.3, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#556677', linewidth=0.15, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.5, color='#4a6a8a', alpha=0.6, linestyle='--')
        gl.top_labels = False; gl.right_labels = False
        gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#c0d0e0', 'weight': 'bold'}
        gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#c0d0e0', 'rotation': 90, 'weight': 'bold'}
        label_fs = round(6.5 * dpi_scale, 1)
        lb_bbox = dict(boxstyle='round,pad=0.15', fc='#1a1a2e', ec='#556677', alpha=0.85)
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#8899aa')
        label_color = 'white'
        pe = None

    # PAR: non-pagasa red dashed
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

    sym_dir = script_dir.parent / "public" / "images" / "symbols"
    TRACK_COLORS = ['#FF6B35', '#00BFFF', '#FFD700', '#00FF9F', '#FF69B4']
    legend_handles = []

    _fig_w_px = figsize[0] * dpi
    _fig_h_px = figsize[1] * dpi
    _ax_w_px = _fig_w_px * 0.96
    _ax_h_px = _fig_h_px * 0.90
    _lon_r = lon_max - lon_min
    _lat_r = lat_max - lat_min
    _px_p_deg_lon = _ax_w_px / _lon_r if _lon_r > 0 else 100
    _px_p_deg_lat = _ax_h_px / _lat_r if _lat_r > 0 else 100
    _pts_p_px = 72.0 / dpi

    # Pre-combine all points for new algos so anticlima sees the full picture
    _combined_bezier_offsets = None
    _track_offset_ranges = []
    if show_labels and algorithm in _NEW_ALGO_NAMES:
        _all_pts = []
        _track_ranges = []
        for t in all_tracks:
            _tpts = t.get("track_points", [])
            if _tpts:
                _track_ranges.append((len(_all_pts), len(_all_pts) + len(_tpts)))
                _all_pts.extend(_tpts)
            else:
                _track_ranges.append((0, 0))
        if _all_pts:
            _combined = _compute_unified_offsets(
                _all_pts, algorithm, _px_p_deg_lon, _px_p_deg_lat,
                lon_min, lat_min, _pts_p_px, None, label_fs,
                None, _fig_w_px, _fig_h_px)
            if _combined is not None:
                _combined_bezier_offsets = _combined
                _track_offset_ranges = _track_ranges

    for idx, t in enumerate(all_tracks):
        pts = t.get("track_points", [])
        prob_circles = t.get("probability_circles", [])
        if not pts:
            continue
        storm_name = t.get("storm_name", t.get("storm_id", f"Track {idx+1}"))
        color = TRACK_COLORS[idx % len(TRACK_COLORS)]
        tlons = [p["lon"] for p in pts if p.get("lon") is not None]
        tlats = [p["lat"] for p in pts if p.get("lat") is not None]
        if len(tlons) < 2:
            continue
        _plons = _adjust_lons_for_plot(tlons, central_lon)
        ax.plot(_plons, tlats, color=color, linewidth=2.5, transform=ccrs.PlateCarree(), zorder=5)

        issued_dtg = t.get("issued_dtg", "")
        if show_labels:
            all_labels = []
            for p in pts:
                pt_lon = p.get("lon"); pt_lat = p.get("lat")
                intensity = p.get("intensity"); dt_str = p.get("datetime", "")
                advanced_hours = p.get("advanced_hours")
                if pt_lon is None or pt_lat is None:
                    all_labels.append(""); continue
                label_parts = []
                dt_obj = None
                if issued_dtg and advanced_hours is not None:
                    base_dt = _parse_jtwc_dtg(issued_dtg)
                    if base_dt is not None:
                        dt_obj = base_dt + timedelta(hours=advanced_hours)
                if dt_obj is None and dt_str:
                    dt_obj = _parse_dt(dt_str)
                if show_dt:
                    if dt_obj is not None:
                        _utc_off = utc_offset - _storage_offset(track_points, forecast_layout)
                        if _utc_off != 0:
                            dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=_utc_off)
                        if issued_dtg and advanced_hours is not None:
                            label_parts.append(dt_obj.strftime("%d%H%M") + "Z")
                        elif time_fmt == "civilian":
                            label_parts.append(dt_obj.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if _utc_off == 0 else ""))
                        else:
                            label_parts.append(dt_obj.strftime("%Y-%m-%d %H:%M") + (" UTC" if _utc_off == 0 else ""))
                    elif dt_str:
                        label_parts.append(dt_str)
                if show_wind and intensity is not None:
                    wind_val = float(intensity)
                    if wind_unit == "kmh":
                        wind_val = round(wind_val * 1.852)
                        label_parts.append(f"{wind_val} km/h")
                    elif wind_unit == "mph":
                        wind_val = round(wind_val * 1.151)
                        label_parts.append(f"{wind_val} mph")
                    elif wind_unit == "ms":
                        wind_val = round(wind_val * 0.514)
                        label_parts.append(f"{wind_val} m/s")
                    else:
                        label_parts.append(f"{wind_val} kt")
                pt_radii = p.get("wind_radii", {})
                if pt_radii:
                    wr_parts = []
                    for kt_key in ("64", "50", "34"):
                        radii = pt_radii.get(kt_key, {})
                        if radii and any(v is not None for v in radii.values()):
                            vals = [str(radii.get(q, "")) for q in ("ne", "se", "sw", "nw")]
                            wr_parts.append(f"{kt_key}:{'/'.join(vals)}")
                    if wr_parts:
                        label_parts.append("|".join(wr_parts))
                all_labels.append("  ".join(label_parts) if label_parts else "")

            skip = [False] * len(pts)
            if len(pts) >= 2:
                bezier_offsets = _compute_bezier_offsets(pts, reverse_side=True, initial_offset=30) if algorithm == "bezier" else (_compute_bezier_offsets(pts) if algorithm == "smart_bezier" else None)
            else:
                bezier_offsets = None
            _cone_radii = {}
            for circ in prob_circles:
                _clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
                _clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
                _rm = circ.get("radius_m") or circ.get("radius")
                if _clon is not None and _clat is not None and _rm:
                    _cone_radii[f"{float(_clat):.4f},{float(_clon):.4f}"] = float(_rm)

            if algorithm in _NEW_ALGO_NAMES:
                _start, _end = _track_offset_ranges[idx] if idx < len(_track_offset_ranges) else (0, 0)
                if _combined_bezier_offsets is not None and _end > _start:
                    bezier_offsets = _combined_bezier_offsets[_start:_end]
                else:
                    bezier_offsets = None
            if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
                bezier_offsets = _cascade_bezier_offsets(
                    bezier_offsets, all_labels, label_fs, pts,
                    _px_p_deg_lon, _px_p_deg_lat, lon_min, lat_min,
                    pts_p_px=_pts_p_px, cone_radii=_cone_radii,
                    max_dist=30 if algorithm == "bezier" else 200
                )

            if algorithm == "anticlima" and bezier_offsets:
                _a_lons, _a_lats = [], []
                for _ai, _bp in enumerate(pts):
                    if _ai >= len(bezier_offsets):
                        break
                    _blon = _bp.get("lon")
                    _blat = _bp.get("lat")
                    if _blon is None or _blat is None:
                        continue
                    _boff, _bha = bezier_offsets[_ai]
                    _stair = round(_ai * 5 * _pts_p_px)
                    _dlon = _boff[0] / (_px_p_deg_lon * _pts_p_px)
                    _dlat = (_boff[1] - _stair) / (_px_p_deg_lat * _pts_p_px)
                    _a_lons.append(_blon + _dlon)
                    _a_lats.append(_blat + _dlat)
                if len(_a_lons) >= 2:
                    ax.plot(_a_lons, _a_lats, color='#FF6600', linewidth=1.5, linestyle='--',
                            transform=ccrs.PlateCarree(), zorder=4, alpha=0.8,
                            label='_AntiClima offset path')

            is_pagasa_track = t.get("source") == "pagasa"
            is_bt = []
            if is_pagasa_track:
                for ti in range(len(pts)):
                    r = pts[ti].get("radius_km", 0)
                    if r == 0 and ti + 1 < len(pts):
                        nr = pts[ti+1].get("radius_km", 0)
                        is_bt.append(nr == 0)
                    else:
                        is_bt.append(False)

            for i, p in enumerate(pts):
                pt_lon = p.get("lon"); pt_lat = p.get("lat")
                intensity = p.get("intensity"); pcat = p.get("intensity_category", "")
                if pt_lon is None or pt_lat is None:
                    continue
                if is_pagasa_track and i < len(is_bt) and is_bt[i]:
                    bt_idx = sum(1 for j in range(i) if j < len(is_bt) and is_bt[j])
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
            if pcat:
                sym_name = _category_to_sym(pcat, intensity, basin=_basin)
                if sym_name:
                    sym_path = sym_dir / f"{sym_name}.png"
                    if sym_path.exists():
                        try:
                            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
                            sym_pil = Image.open(str(sym_path)).convert("RGBA")
                            sym_w, sym_h = sym_pil.size
                            target_sz = round(14 * dpi_scale)
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
                mk = _cat_color(intensity)
                sz = 9 if intensity is not None and intensity >= 64 else 7
                ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                        markeredgewidth=0.5)
            if skip[i] or not all_labels[i]:
                continue
            _key = f"{pt_lat:.4f},{pt_lon:.4f}"
            _r_m = _cone_radii.get(_key, 0)
            if _r_m > 0:
                _cos = math.cos(math.radians(pt_lat))
                _r_deg = _r_m / (111320.0 * max(_cos, 0.01))
                _off_px = _r_deg * min(_px_p_deg_lon, _px_p_deg_lat) * 1.5
                _base = max(round(_off_px * _pts_p_px), 30)
                _x_off = _base; _y_off = round(_base * 0.6)
            else:
                _x_off = 24; _y_off = 14
            if bezier_offsets and i < len(bezier_offsets):
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
        else:
            all_labels = [""] * len(pts)
            skip = [False] * len(pts)
            bezier_offsets = None
            _cone_radii = {}
        legend_handles.append(plt.Line2D([0], [0], color=color, linewidth=2.5, label=storm_name))

    # Combined track legend
    if legend_handles:
        leg_text_color = '#ffffff' if not light else 'black'
        leg = ax.legend(handles=legend_handles, loc='lower left', framealpha=0.85,
                        fontsize=round(8 * dpi_scale),
                        facecolor='white' if light else '#1a1a2e',
                        edgecolor='gray', labelcolor=leg_text_color)
        leg.set_zorder(20)

    # Title: default combined
    storm_names = []
    for _t in all_tracks:
        _raw = _t.get("storm_name", _t.get("storm_id", "T"))
        _src = _t.get("source", "")
        intl_name = _clean_storm_name(_raw, source=_src)
        storm_names.append(f"{intl_name} ({_src.upper()})" if _src else intl_name)
    names_str = ", ".join(storm_names)
    title_color = label_color
    ax.set_title(f"Combined Forecast \u2014 {names_str}",
                 fontsize=round(12 * dpi_scale, 1) if light else 11,
                 color=title_color, fontweight='bold' if light else 'normal')

    fig.subplots_adjust(bottom=0.08)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor=fig.get_facecolor())
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)

    # Disclaimer
    _add_disclaimer(pil_img, "default")
    # Footer
    if logo_path:
        fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
        utc_offset = fcst_prefs.get("utc_offset", 0)
        time_fmt = fcst_prefs.get("time_format", "military")
        now = datetime.now(timezone.utc)
        if utc_offset != 0:
            now = now + timedelta(hours=utc_offset)
        now_str = now.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else "")
        pil_img = _add_footer(pil_img, "", now_str, "Combined", logo_path, utc_offset, dark=False)

    if filename:
        pil_img.save(filename)
        return filename
    return np.array(pil_img)
