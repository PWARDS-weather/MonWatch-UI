"""JMA forecast handler."""
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
    _build_track_labels, _build_combined_labels,
    _find_first_future_idx, _fig_to_pil,
    _draw_pil_title_box, _draw_combined_title_box,
    _add_forecast_footer, _add_disclaimer,
    _compute_unified_offsets, _NEW_ALGO_NAMES,
    _normalize_track_extent, _adjust_lons_for_plot, _clean_storm_name,
)

forecast_layout = "jma"

def make_forecast(track_points, probability_circles=None, storm_name="Tropical Cyclone",
                  storm_id="", margin=8.0, dpi=150, figsize=(12, 10),
                  facecolor="white", filename=None, light=True,
                  settings=None, logo_path=None, algorithm="polar",
                   issued_dtg=None, kmz_cone="", kmz_track="", kmz_wind_initial="", **kwargs):
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

    # JMA extent: DEFAULT (dateline-aware)
    _dl_min, _dl_max, central_lon, _ = _normalize_track_extent(lons, margin)
    lon_min = _dl_min
    lon_max = _dl_max
    lat_min = max(min(lats) - margin, -90)
    lat_max = min(max(lats) + margin, 90)

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor=facecolor)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    _log(f"[fcst] storm={storm_name} id={storm_id} layout=jma light={light} "
         f"dpi={dpi} figsize={figsize} extent=[{lon_min:.2f}, {lon_max:.2f}, {lat_min:.2f}, {lat_max:.2f}]",
         file=sys.stderr)

    # JMA model tracks from tropycal
    if storm_id:
        try:
            from tropycal import realtime as _jma_rt
            _jma_data = _jma_rt(jtwc=True, jtwc_source="jtwc")
            _jma_storm = _jma_data.get_storm(storm_id)
            if _jma_storm is not None:
                _jma_storm.plot_models(forecast='latest', ax=ax, cartopy_proj=ccrs.PlateCarree())
                _log(f"JMA model tracks plotted for {storm_id}", file=sys.stderr)
        except Exception:
            pass

    # JMA map setup
    _log(f"[fcst]   jma light={light}: ocean=#BED2FE/%230d1520 land=#445C45 edge", file=sys.stderr)
    if light:
        ax.set_facecolor('#BED2FE')
        ax.add_feature(_LAND, facecolor='#7f997f', edgecolor='#334c33', linewidth=0.9)
        ax.add_feature(_OCEAN, facecolor='#BED2FE', alpha=0.6)
        ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.3, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.15, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
        gl.top_labels = True
        gl.right_labels = True
        gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'weight': 'bold'}
        gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'rotation': 90, 'weight': 'bold'}
        gl.xformatter = LONGITUDE_FORMATTER
        gl.yformatter = LATITUDE_FORMATTER
        label_color = '#000000'
        label_bg = 'white'
        label_border = 'none'
        title_color = '#000000'
    else:
        ax.set_facecolor('#0d1520')
        ax.add_feature(_LAND, facecolor='#7f997f', edgecolor='#334c33', linewidth=0.9)
        ax.add_feature(_OCEAN, facecolor='#0d1520', alpha=0.9)
        ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.2, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.1, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
        gl.top_labels = True
        gl.right_labels = True
        gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', 'weight': 'bold'}
        gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', 'rotation': 90, 'weight': 'bold'}
        gl.xformatter = LONGITUDE_FORMATTER
        gl.yformatter = LATITUDE_FORMATTER
        label_color = '#F1F5FE'
        label_bg = '#1a1a2e'
        label_border = 'none'
        title_color = '#F1F5FE'

    # PAR (non-pagasa style - red dashed line)
    par_pts = [
        (115.0, 5.0), (115.0, 15.0), (120.0, 21.0),
        (120.0, 25.0), (135.0, 25.0), (135.0, 5.0),
    ]
    par_lons = [p[0] for p in par_pts]
    par_lats = [p[1] for p in par_pts]
    ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
            color="#FF3333", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=1)

    # Probability circles
    geod = Geodesic()
    prob_circles = probability_circles or []

    # JMA-style probability circle styling
    _log(f"[fcst] Cone style: layout=jma circles={len(prob_circles)} kmz_cone={bool(kmz_cone)}", file=sys.stderr)
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

        cone_color = '#F1F5FE'
        circle_color = '#F1F5FE'
        fill_color = '#F1F5FE'
        cone_alpha = 0.7
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

    # KMZ wind / cone
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
    if kmz_cone and os.path.exists(kmz_cone) and not prob_circles:
        cone_geoms = _parse_kmz(kmz_cone)
        for c_lons, c_lats in cone_geoms:
            if len(c_lons) < 3:
                continue
            ax.fill(c_lons, c_lats, color='#F1F5FE', alpha=0.04,
                    transform=ccrs.PlateCarree(), zorder=1)
            ax.plot(c_lons + [c_lons[0]], c_lats + [c_lats[0]], color='#F1F5FE', linewidth=1.0, alpha=0.6, linestyle='--',
                    transform=ccrs.PlateCarree(), zorder=2)

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

    # Track line - JMA dashed style
    if len(track_points) >= 2 and not kmz_track_lons:
        tlons = [p["lon"] for p in track_points if p.get("lon") is not None]
        tlats = [p["lat"] for p in track_points if p.get("lat") is not None]
        if tlons:
            _plons = _adjust_lons_for_plot(tlons, central_lon)
            ax.plot(_plons, tlats, color='#F1F5FE', linewidth=2.0, linestyle='--',
                    transform=ccrs.PlateCarree(), zorder=5)

    # Label style - JMA
    label_fs = round(6.5 * dpi_scale, 1)
    if light:
        lb_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='#000000', alpha=0.9)
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
    else:
        lb_bbox = dict(boxstyle='round,pad=0.15', fc='#1a1a2e', ec='#445C45', alpha=0.9)
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#445C45')

    # Build labels
    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")
    all_labels = _build_track_labels(track_points, "jma", show_dt, show_wind, time_fmt, utc_offset, wind_unit, issued_dtg)
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

    # Expand map boundary if needed
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

    # Wind radii
    _draw_wind_radii(ax, track_points, dpi_scale, "jma")

    # Render points + labels
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

    for i, (pt_lon, pt_lat, tp_idx) in enumerate(render_pts):
        p = track_points[tp_idx]
        intensity = p.get("intensity")
        pcat = p.get("intensity_category", "")
        used_symbol = False
        if not pcat:
            pass
        if pcat:
            sym_name = _category_to_sym(pcat, intensity)
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
        ax.annotate("", (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    arrowprops={**lb_arrow, 'relpos': (0 if ha == 'left' else 1, 1)},
                    transform=ccrs.PlateCarree(), zorder=1)
        ax.annotate(all_labels[tp_idx], (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    fontsize=label_fs, color=label_color, ha=ha,
                    bbox=lb_bbox,
                    path_effects=None,
                    transform=ccrs.PlateCarree(), zorder=90)

    # JMA Legend: final_track.png
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

    # Title - JMA style
    title_color_jma = '#000000' if light else '#F1F5FE'
    title_text = f"{storm_name.upper()} ({storm_id.upper()}) \u2014 Forecast Track \u2014 JMA"
    ax.set_title(title_text, fontsize=round(11 * dpi_scale, 1), color=title_color_jma, fontweight='600', fontfamily='sans-serif', pad=12)

    # Save to PIL (NO PIL title box for JMA)
    pil_img = _fig_to_pil(fig, dpi, facecolor)

    # Disclaimer
    _add_disclaimer(pil_img, "jma")

    # Footer
    pil_img = _add_forecast_footer(pil_img, track_points, storm_id, logo_path, settings, "jma", light)

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

    # JMA combined extent: DEFAULT (not cone-aware)
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
    # Combined extent: dateline-aware
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

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor='white' if light else '#0d1520')
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # JMA map setup (combined)
    _log(f"[fcst]   jma light={light}: ocean=#BED2FE/%230d1520 land=#445C45 edge", file=sys.stderr)
    if light:
        ax.set_facecolor('#BED2FE')
        ax.add_feature(_LAND, facecolor='#7f997f', edgecolor='#334c33', linewidth=0.9)
        ax.add_feature(_OCEAN, facecolor='#BED2FE', alpha=0.6)
        ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.3, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.15, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
        gl.top_labels = True
        gl.right_labels = True
        gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'weight': 'bold'}
        gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'rotation': 90, 'weight': 'bold'}
        gl.xformatter = LONGITUDE_FORMATTER
        gl.yformatter = LATITUDE_FORMATTER
        label_color = '#000000'
        label_fs = round(6.5 * dpi_scale, 1)
        lb_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='#000000', alpha=0.9)
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
    else:
        ax.set_facecolor('#0d1520')
        ax.add_feature(_LAND, facecolor='#7f997f', edgecolor='#334c33', linewidth=0.9)
        ax.add_feature(_OCEAN, facecolor='#0d1520', alpha=0.9)
        ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.2, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.1, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
        gl.top_labels = True
        gl.right_labels = True
        gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', 'weight': 'bold'}
        gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', 'rotation': 90, 'weight': 'bold'}
        gl.xformatter = LONGITUDE_FORMATTER
        gl.yformatter = LATITUDE_FORMATTER
        label_color = '#F1F5FE'
        label_fs = round(6.5 * dpi_scale, 1)
        lb_bbox = dict(boxstyle='round,pad=0.15', fc='#1a1a2e', ec='#445C45', alpha=0.9)
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#445C45')

    pe = None

    # PAR (default non-pagasa, red dashed)
    par_pts = [
        (115.0, 5.0), (115.0, 15.0), (120.0, 21.0),
        (120.0, 25.0), (135.0, 25.0), (135.0, 5.0),
    ]
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

    # Pre-compute cone paths + track points for bezier collision detection
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
        prob_circles = t.get("probability_circles", [])
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
        if prob_circles and cone_method == "union":
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
                for circ in prob_circles:
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
                        ax.fill(poly_lons, poly_lats, color=color, alpha=0.10,
                                edgecolor=color, linewidth=1.0, linestyle='--',
                                transform=ccrs.PlateCarree(), zorder=1)
            except Exception:
                pass
        elif prob_circles:
            poly_lons, poly_lats = _build_cone_polygon(pts, prob_circles)
            if poly_lons and poly_lats:
                ax.fill(poly_lons, poly_lats, color=color, alpha=0.10,
                        edgecolor=color, linewidth=1.0, linestyle='--',
                        transform=ccrs.PlateCarree(), zorder=1)

        # Track line (JMA-style for each track in combined)
        _plons = _adjust_lons_for_plot(tlons, central_lon)
        ax.plot(_plons, tlats, color=color, linewidth=2.0, linestyle='--',
                transform=ccrs.PlateCarree(), zorder=5)

        # Symbols + labels
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
            all_labels = _build_combined_labels(pts, "jma", show_dt, show_wind, time_fmt, utc_offset, wind_unit, issued_dtg)
            skip = [False] * len(pts)
            bezier_offsets = _compute_bezier_offsets(pts, reverse_side=(algorithm == "bezier"), initial_offset=30 if algorithm == "bezier" else 60) if algorithm in ("bezier", "smart_bezier") and len(pts) >= 2 else None
            _cone_radii = {}
            for circ in prob_circles:
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

            # Expand boundaries
            _threshold_deg = 2.0
            _expand_deg = 5.0
            _need_left = _need_right = _need_bottom = _need_top = False
            if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
                for _bi, (_boff, _bha) in enumerate(bezier_offsets):
                    if _bi >= len(pts):
                        break
                    _bp = pts[_bi]
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
                for _bi, _bp in enumerate(pts):
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
                    _bside_right = _bi % 2 == 0
                    if _bside_right:
                        _bol, _boa = -_bx_off, -_by_off
                    else:
                        _bol, _boa = _bx_off, _by_off
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
        else:
            all_labels = [""] * len(pts)
            skip = [False] * len(pts)
            bezier_offsets = None
            _cone_radii = {}

        _draw_wind_radii(ax, pts, dpi_scale, "jma")

        for i, p in enumerate(pts):
            pt_lon = p.get("lon")
            pt_lat = p.get("lat")
            intensity = p.get("intensity")
            pcat = p.get("intensity_category", "")
            if pt_lon is None or pt_lat is None:
                continue
            used_symbol = False
            if not pcat and intensity is not None:
                if intensity >= 96: pcat = "Major Hurricane"
                elif intensity >= 64: pcat = "Hurricane"
                elif intensity >= 50: pcat = "STS"
                elif intensity >= 34: pcat = "TS"
                else: pcat = "TD"
            if pcat:
                sym_name = _category_to_sym(pcat, intensity)
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
                                                box_alignment=(0.5, 0.5), zorder=100)
                            ax.add_artist(ab)
                            used_symbol = True
                        except Exception:
                            pass
            if not used_symbol:
                mk = _cat_color(intensity)
                sz = 9 if intensity is not None and intensity >= 64 else 7
                ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                        transform=ccrs.PlateCarree(), zorder=99, markeredgecolor='white',
                        markeredgewidth=0.5)
            if not all_labels[i] or skip[i]:
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
            if algorithm in ("polar", "zigzag"):
                if (i - sum(skip[:i])) % 2 == 0:
                    xytext = (-_x_off, -_y_off)
                    ha = 'right'
                else:
                    xytext = (_x_off, _y_off)
                    ha = 'left'
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
                    _lab_lon = pt_lon + _ol
                    _lab_lat = pt_lat + _oa
                    _hit = False
                    for _ot_idx in range(len(all_tracks)):
                        if _ot_idx == idx:
                            continue
                        _cp = _comb_cone_paths[_ot_idx]
                        if _cp is not None:
                            try:
                                if _cp.contains_point((_lab_lon, _lab_lat)):
                                    _hit = True
                                    break
                            except Exception:
                                pass
                        if not _hit:
                            for _opl in _comb_track_pts[_ot_idx]:
                                _opl_lon, _opl_lat = _opl
                                if _opl_lon is None or _opl_lat is None:
                                    continue
                                if math.hypot(_lab_lon - _opl_lon, _lab_lat - _opl_lat) < 0.3:
                                    _hit = True
                                    break
                        if _hit:
                            break
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
        legend_handles.append(plt.Line2D([0], [0], color=color, linewidth=2.5, label=storm_name))

    # Legend
    if legend_handles:
        leg_text_color = '#ffffff' if not light else 'black'
        leg = ax.legend(handles=legend_handles, loc='lower left', framealpha=0.85,
                        fontsize=round(8 * dpi_scale),
                        facecolor='white' if light else '#1a1a2e', edgecolor='gray',
                        labelcolor=leg_text_color)
        leg.set_zorder(20)

    # Title - JMA-style combined
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
    title_color_jma = '#000000' if light else '#F1F5FE'
    ax.set_title(f"{names_str} \u2014 Combined Forecast \u2014 JMA",
                 fontsize=round(11 * dpi_scale, 1), color=title_color_jma,
                 fontweight='600', fontfamily='sans-serif', pad=12)

    # PIL processing
    fig.subplots_adjust(bottom=0.08)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor=fig.get_facecolor())
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)

    # No PIL title box for JMA

    # Disclaimer
    _add_disclaimer(pil_img, "jma")

    # Footer
    if logo_path:
        fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
        utc_offset = fcst_prefs.get("utc_offset", 0)
        time_fmt = fcst_prefs.get("time_format", "military")
        now = datetime.now(timezone.utc)
        if utc_offset != 0:
            now = now + timedelta(hours=utc_offset)
        if time_fmt == "civilian":
            now_str = now.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if utc_offset == 0 else "")
        else:
            now_str = now.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else "")
        pil_img = _add_footer(pil_img, "", now_str, "Combined", logo_path, utc_offset, dark=False)

    if filename:
        pil_img.save(filename)
        return filename
    return np.array(pil_img)
