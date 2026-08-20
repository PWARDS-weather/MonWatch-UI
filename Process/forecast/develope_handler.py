"""
develope_handler.py — Development/test layout for visual debugging of label placement algorithms.

Clones the monwatch layout and adds debug overlays:
- Catmull-Rom interpolation curve drawn on map
- Label anchor points with direction arrows
- Label offset lines from anchor to text position
"""

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
    _draw_pil_title_box, _draw_combined_title_box,
    _add_forecast_footer, _add_combined_footer, _add_disclaimer,
    _fetch_jma_model_tracks, _compute_unified_offsets, _NEW_ALGO_NAMES,
    _adjust_lons_for_plot,
)

forecast_layout = "develope"


def _add_debug_curve(ax, track_points, algorithm):
    lons = [p.get("lon") for p in track_points if p.get("lon") is not None]
    lats = [p.get("lat") for p in track_points if p.get("lat") is not None]
    if len(lons) < 2:
        return

    ax.plot(lons, lats, color='white', linewidth=1.0, linestyle=':',
            marker='o', markersize=7, markerfacecolor='yellow',
            markeredgecolor='orange', markeredgewidth=1.0,
            transform=ccrs.PlateCarree(), zorder=100, label='Track pts')

    if algorithm == "railway_bezier":
        try:
            from .label_placement_algorithms.algo_9_railway_bezier import get_curve
            pts_wrapper = [type('pt', (), {'lon': lo, 'lat': la})() for lo, la in zip(lons, lats)]
            interp_lons, interp_lats = get_curve(pts_wrapper)
            ax.plot(interp_lons, interp_lats, color='#FF69B4', linewidth=2.5,
                    transform=ccrs.PlateCarree(), zorder=101,
                    label='Catmull-Rom curve')
        except Exception as exc:
            print(f"[develope] curve error: {exc}", file=sys.stderr)

    if algorithm == "railway_bezier":
        try:
            from .label_placement_algorithms.algo_9_railway_bezier import _catmull_rom
            pts_wrapper = [type('pt', (), {'lon': lo, 'lat': la})() for lo, la in zip(lons, lats)]
            interp_lons, interp_lats = _catmull_rom(lons, lats, 10)
            for i in range(len(interp_lons) - 1):
                ax.plot([interp_lons[i], interp_lons[i+1]],
                        [interp_lats[i], interp_lats[i+1]],
                        color='#FF69B4', linewidth=1.5, alpha=0.6,
                        transform=ccrs.PlateCarree(), zorder=100)
            for i in range(len(lons)):
                orig_t = i / (len(lons) - 1) if len(lons) > 1 else 0
                idx = int(round(orig_t * (len(interp_lons) - 1)))
                idx = max(0, min(idx, len(interp_lons) - 1))
                ax.plot(interp_lons[idx], interp_lats[idx], 'o',
                        color='#FF69B4', markersize=10, markeredgecolor='white',
                        markeredgewidth=1.5,
                        transform=ccrs.PlateCarree(), zorder=102)
                ax.plot([lons[i], interp_lons[idx]], [lats[i], interp_lats[idx]],
                        color='#FF69B4', linewidth=0.8, linestyle='--', alpha=0.5,
                        transform=ccrs.PlateCarree(), zorder=99)
                ax.text(lons[i], lats[i] - 0.5, f'P{i}', fontsize=9, color='cyan',
                        transform=ccrs.PlateCarree(), zorder=200,
                        bbox=dict(boxstyle='round,pad=0.2', facecolor='black', alpha=0.7))
        except Exception as exc:
            print(f"[develope] curve detail error: {exc}", file=sys.stderr)


def _add_debug_labels(ax, fig, track_points, algorithm, lon_min, lon_max, lat_min, lat_max):
    lons = [p.get("lon") for p in track_points if p.get("lon") is not None]
    lats = [p.get("lat") for p in track_points if p.get("lat") is not None]
    if len(lons) < 2:
        return

    _fig_w_px = fig.get_size_inches()[0] * fig.dpi
    _fig_h_px = fig.get_size_inches()[1] * fig.dpi
    _ax_w_px = _fig_w_px * 0.96
    _ax_h_px = _fig_h_px * 0.90
    _lon_r = lon_max - lon_min
    _lat_r = lat_max - lat_min
    _px_p_deg_lon = _ax_w_px / _lon_r if _lon_r > 0 else 100
    _px_p_deg_lat = _ax_h_px / _lat_r if _lat_r > 0 else 100
    _pts_p_px = 72.0 / fig.dpi

    try:
        result = _compute_unified_offsets(
            track_points, algorithm, _px_p_deg_lon, _px_p_deg_lat,
            lon_min, lat_min, _pts_p_px, [], 10,
            None, _fig_w_px, _fig_h_px
        )
    except Exception:
        result = None

    if not result:
        return

    for i, ((ox, oy), ha) in enumerate(result):
        if i >= len(lons):
            break
        lon, lat = lons[i], lats[i]

        ox_deg = ox / max(_px_p_deg_lon * _pts_p_px, 1e-9)
        oy_deg = oy / max(_px_p_deg_lat * _pts_p_px, 1e-9)
        if ha == 'right':
            ox_deg = -ox_deg
            oy_deg = -oy_deg

        label_lon = lon + ox_deg
        label_lat = lat + oy_deg

        ax.plot(lon, lat, 'o', color='cyan', markersize=10,
                markeredgecolor='white', markeredgewidth=1.5,
                transform=ccrs.PlateCarree(), zorder=200)
        ax.plot(label_lon, label_lat, 's', color='lime', markersize=8,
                markeredgecolor='white', markeredgewidth=1.0,
                transform=ccrs.PlateCarree(), zorder=201)
        ax.plot([lon, label_lon], [lat, label_lat], color='yellow',
                linewidth=1.5, linestyle='-', alpha=0.8,
                transform=ccrs.PlateCarree(), zorder=199)

        mid_lon = (lon + label_lon) / 2
        mid_lat = (lat + label_lat) / 2
        dist_deg = math.hypot(ox_deg, oy_deg)
        ax.text(mid_lon, mid_lat, f'd={dist_deg:.1f} deg', fontsize=6,
                color='yellow', transform=ccrs.PlateCarree(), zorder=203,
                bbox=dict(boxstyle='round,pad=0.1', facecolor='black', alpha=0.5))

        ax.text(lon, lat - 0.6, f'P{i} ({ha})', fontsize=8, color='white',
                transform=ccrs.PlateCarree(), zorder=202,
                bbox=dict(boxstyle='round,pad=0.2', facecolor='black', alpha=0.7))

    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], color='white', linestyle=':', marker='o',
               markerfacecolor='yellow', markeredgecolor='orange',
               label='Track pts'),
        Patch(facecolor='cyan', edgecolor='white', label='Anchor'),
        Patch(facecolor='lime', edgecolor='white', label='Label pos'),
        Line2D([0], [0], color='yellow', alpha=0.8, label='Leader'),
    ]
    if algorithm == "railway_bezier":
        legend_elements.insert(
            1, Line2D([0], [0], color='#FF69B4', linewidth=2.5,
                       label='Catmull-Rom')
        )
    try:
        ax.legend(handles=legend_elements, loc='lower left', fontsize=8,
                  framealpha=0.8, facecolor='#222222', edgecolor='white',
                  labelcolor='white')
    except Exception:
        pass


def make_forecast(track_points, probability_circles=None, storm_name="Tropical Cyclone",
                  storm_id="", margin=8.0, dpi=150, figsize=(12, 10),
                  facecolor="white", filename=None, light=True,
                  settings=None, logo_path=None, algorithm="polar",
                   issued_dtg=None, kmz_cone="", kmz_track="", kmz_wind_initial="", **kwargs):
    _src = kwargs.get("source", "")
    _title_info = kwargs.get("title_info", {})
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

    if storm_id:
        _fetch_jma_model_tracks(ax, storm_id)

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
    gl.top_labels = True; gl.right_labels = True; gl.left_labels = True; gl.bottom_labels = True
    gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', "weight": "bold"}
    gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', "rotation": 90, "weight": "bold"}
    gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
    gl.xpadding = 2; gl.ypadding = 2
    label_color = '#ffffff'; label_bg = '#00000088'; label_border = 'none'; title_color = '#ffffff'

    par_pts = [(115.0, 5.0), (115.0, 15.0), (120.0, 21.0), (120.0, 25.0), (135.0, 25.0), (135.0, 5.0)]
    par_lons = [p[0] for p in par_pts]; par_lats = [p[1] for p in par_pts]
    ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
            color="#FF3333", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=1)

    geod = Geodesic()
    cone_method = fcst_prefs.get("cone_method", "smooth")
    if probability_circles:
        if cone_method == "union":
            from shapely.geometry import Point as ShapelyPoint
            from shapely.ops import unary_union
            pag_centers = []; pag_radii = []
            for circ in probability_circles:
                clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
                clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
                radius_m = circ.get("radius_m") or circ.get("radius")
                if clon is not None and clat is not None and radius_m:
                    pag_centers.append((clon, clat)); pag_radii.append(float(radius_m))
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
                ax.fill(poly_lons, poly_lats, color='#60a5fa', alpha=0.15,
                        transform=ccrs.PlateCarree(), zorder=50)
                ax.plot(poly_lons + [poly_lons[0]], poly_lats + [poly_lats[0]],
                        color='#60a5fa', linewidth=1.0, alpha=0.6, linestyle='--',
                        transform=ccrs.PlateCarree(), zorder=51)

    _wr_polys = kwargs.get("wind_radii_polygons", {})
    wr_colors = [('#00C800', 0.20), ('#FFA500', 0.25), ('#FF3232', 0.30)]
    _wr_has_data = any(_wr_polys.get(_kt) for _kt in ("34", "50", "64"))
    if _wr_has_data:
        for _ki, _kt in enumerate(("34", "50", "64")):
            for _coords in _wr_polys.get(_kt, []):
                _lons = [c[0] for c in _coords]; _lats = [c[1] for c in _coords]
                if len(_lons) < 3: continue
                ax.fill(_lons, _lats, color=wr_colors[_ki][0], alpha=wr_colors[_ki][1],
                        edgecolor=wr_colors[_ki][0], linewidth=0.3,
                        transform=ccrs.PlateCarree(), zorder=2)
    elif kmz_wind_initial and os.path.exists(kmz_wind_initial):
        wr_geoms = _parse_kmz(kmz_wind_initial)
        for i, (wr_lons, wr_lats) in enumerate(wr_geoms):
            if len(wr_lons) < 3: continue
            c = wr_colors[i % len(wr_colors)]
            ax.fill(wr_lons, wr_lats, color=c[0], alpha=c[1],
                    edgecolor=c[0], linewidth=0.3,
                    transform=ccrs.PlateCarree(), zorder=2)

    _danger_swath = kwargs.get("danger_swath", [])
    if _danger_swath and not probability_circles:
        _ds_lons = [c[0] for c in _danger_swath]; _ds_lats = [c[1] for c in _danger_swath]
        if len(_ds_lons) >= 3:
            ax.fill(_ds_lons, _ds_lats, color='#60a5fa', alpha=0.15,
                    transform=ccrs.PlateCarree(), zorder=50)
            ax.plot(_ds_lons + [_ds_lons[0]], _ds_lats + [_ds_lats[0]],
                    color='#60a5fa', linewidth=1.0, alpha=0.6, linestyle='--',
                    transform=ccrs.PlateCarree(), zorder=51)
    elif kmz_cone and os.path.exists(kmz_cone) and not probability_circles:
        cone_geoms = _parse_kmz(kmz_cone)
        for c_lons, c_lats in cone_geoms:
            if len(c_lons) < 3: continue
            ax.fill(c_lons, c_lats, color='#60a5fa', alpha=0.15,
                    transform=ccrs.PlateCarree(), zorder=50)
            ax.plot(c_lons + [c_lons[0]], c_lats + [c_lats[0]],
                    color='#60a5fa', linewidth=1.0, alpha=0.6, linestyle='--',
                    transform=ccrs.PlateCarree(), zorder=51)

    _bt_points = kwargs.get("best_track_points", [])
    _bt_line = kwargs.get("best_track_line", [])
    _bt_lons = [p["lon"] for p in _bt_points if p.get("lon") is not None]
    _bt_lats = [p["lat"] for p in _bt_points if p.get("lat") is not None]
    if not _bt_lons and _bt_line:
        _bt_lons = [c[0] for c in _bt_line]; _bt_lats = [c[1] for c in _bt_line]
    if _bt_lons:
        if _bt_line and len(_bt_line) >= 2:
            _btl_lons = [c[0] for c in _bt_line]; _btl_lats = [c[1] for c in _bt_line]
            ax.plot(_btl_lons, _btl_lats, color='#42A5F5', linewidth=1.5, linestyle='-',
                    alpha=0.5, transform=ccrs.PlateCarree(), zorder=4)
        elif len(_bt_lons) >= 2:
            ax.plot(_bt_lons, _bt_lats, color='#42A5F5', linewidth=1.5, linestyle='-',
                    alpha=0.5, transform=ccrs.PlateCarree(), zorder=4)
        for _bti in range(len(_bt_lons)):
            _blon, _blat = _bt_lons[_bti], _bt_lats[_bti]
            if _bti % 2 == 0:
                ax.plot(_blon, _blat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5', markeredgewidth=0.5)
            else:
                ax.plot(_blon, _blat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5', markeredgewidth=0.5, markerfacecolor='white')
                ax.plot(_blon, _blat, 'o', color='#42A5F5', markersize=3,
                        transform=ccrs.PlateCarree(), zorder=7, markeredgecolor='#42A5F5', markeredgewidth=0)

    for circ in probability_circles:
        center = circ.get("center")
        clon = circ.get("center_lon"); clat = circ.get("center_lat")
        if clon is None and center is not None: clon = center[1] if len(center) > 1 else None
        if clat is None and center is not None: clat = center[0] if len(center) > 0 else None
        radius_m = circ.get("radius_m") or circ.get("radius")
        if clon is None or clat is None or not radius_m: continue
        if cone_method == "smooth": pass

    first_future_idx = len(track_points)

    kmz_track_lons = []; kmz_track_lats = []; kmz_point_pts = []
    if kmz_track and os.path.exists(kmz_track):
        track_geoms = _parse_kmz(kmz_track)
        best = max(track_geoms, key=lambda g: len(g[0])) if track_geoms else None
        if best: kmz_track_lons, kmz_track_lats = best
        kmz_point_pts = [(lons[0], lats[0]) for lons, lats in track_geoms if len(lons) == 1]
        if kmz_track_lons and len(kmz_track_lons) >= 2:
            _pkmz_lons = _adjust_lons_for_plot(kmz_track_lons, central_lon)
            ax.plot(_pkmz_lons, kmz_track_lats, linewidth=3, transform=ccrs.PlateCarree(), zorder=4, alpha=0.15)
            ax.plot(_pkmz_lons, kmz_track_lats, linewidth=1.5, transform=ccrs.PlateCarree(), zorder=4, alpha=0.3)
            ax.plot(_pkmz_lons, kmz_track_lats, color='#00fff2', linewidth=1.0, transform=ccrs.PlateCarree(), zorder=5)

    if len(track_points) >= 2 and not kmz_track_lons:
        tlons = [p["lon"] for p in track_points if p.get("lon") is not None]
        tlats = [p["lat"] for p in track_points if p.get("lat") is not None]
        if tlons:
            _ptlons = _adjust_lons_for_plot(tlons, central_lon)
            ax.plot(_ptlons, tlats, linewidth=3, transform=ccrs.PlateCarree(), zorder=4, alpha=0.15)
            ax.plot(_ptlons, tlats, linewidth=1.5, transform=ccrs.PlateCarree(), zorder=4, alpha=0.3)
            ax.plot(_ptlons, tlats, color='#00fff2', linewidth=1.0, transform=ccrs.PlateCarree(), zorder=5)

    label_fs = round(10 * dpi_scale, 1)
    lb_bbox = None
    label_color = '#ffffff'
    lb_arrow = dict(arrowstyle='-', lw=0.8, color='#ffffff')
    ax.spines['top'].set_visible(False); ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_visible(False); ax.spines['left'].set_visible(False)

    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")
    all_labels = _build_track_labels(track_points, forecast_layout, show_dt, show_wind,
                                     time_fmt, utc_offset, wind_unit, issued_dtg)

    skip = [False] * len(track_points)
    sym_dir = script_dir.parent / "public" / "images" / "symbols"
    _fig_w_px = figsize[0] * dpi; _fig_h_px = figsize[1] * dpi
    _ax_w_px = _fig_w_px * 0.96; _ax_h_px = _fig_h_px * 0.90
    _lon_r = lon_max - lon_min; _lat_r = lat_max - lat_min
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
                d = math.hypot(bezier_offsets[j][0][0] - bezier_offsets[j+1][0][0],
                               bezier_offsets[j][0][1] - bezier_offsets[j+1][0][1])
                if d < 50: cramped = True; break
            if cramped and max_curve > 0.05:
                bezier_offsets = [((-ox, -oy), 'left' if ha == 'right' else 'right')
                                  for (ox, oy), ha in bezier_offsets]

    _threshold_deg = 2.0; _expand_deg = 5.0
    _need_left = _need_right = _need_bottom = _need_top = False
    if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
        for _bi, (_boff, _bha) in enumerate(bezier_offsets):
            if _bi >= len(track_points): break
            _bp = track_points[_bi]; _blon = _bp.get("lon"); _blat = _bp.get("lat")
            if _blon is None or _blat is None: continue
            _bol = _boff[0] / max(_pts_p_px * _px_p_deg_lon, 1e-9)
            _boa = _boff[1] / max(_pts_p_px * _px_p_deg_lat, 1e-9)
            _bllon = _blon + _bol; _bllat = _blat + _boa
            if _bllon < lon_min + _threshold_deg: _need_left = True
            if _bllon > lon_max - _threshold_deg: _need_right = True
            if _bllat < lat_min + _threshold_deg: _need_bottom = True
            if _bllat > lat_max - _threshold_deg: _need_top = True
    else:
        for _bi, _bp in enumerate(track_points):
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
            _bstair = round(_bi * 5 * _pts_p_px)
            if algorithm in ("polar", "zigzag") and _curvatures:
                _bcross = _curvatures[_bi] if _bi < len(_curvatures) else 0
                _bside_right = _bcross > 0 if abs(_bcross) > 0.03 else _bi % 2 == 0
            else: _bside_right = _bi % 2 == 0
            if _bside_right: _bol, _boa = -_bx_off, -_by_off - _bstair
            else: _bol, _boa = _bx_off, _by_off - _bstair
            _bol = _bol / max(_pts_p_px * _px_p_deg_lon, 1e-9)
            _boa = _boa / max(_pts_p_px * _px_p_deg_lat, 1e-9)
            if _blon + _bol < lon_min + _threshold_deg: _need_left = True
            if _blon + _bol > lon_max - _threshold_deg: _need_right = True
            if _blat + _boa < lat_min + _threshold_deg: _need_bottom = True
            if _blat + _boa > lat_max - _threshold_deg: _need_top = True
    if _need_left: lon_min -= _expand_deg
    if _need_right: lon_max += _expand_deg
    if _need_bottom: lat_min -= _expand_deg
    if _need_top: lat_max += _expand_deg
    lat_min = max(lat_min, -90); lat_max = min(lat_max, 90)
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    _draw_wind_radii(ax, track_points, dpi_scale, forecast_layout)

    # ── DEBUG OVERLAYS ──
    _add_debug_curve(ax, track_points, algorithm)
    _add_debug_labels(ax, fig, track_points, algorithm, lon_min, lon_max, lat_min, lat_max)
    # ── END DEBUG OVERLAYS ──

    render_pts = []
    if kmz_point_pts:
        for kmz_lon, kmz_lat in kmz_point_pts:
            best_idx = 0; best_d = float('inf')
            for ti, tp in enumerate(track_points):
                tl = tp.get("lon"); ta = tp.get("lat")
                if tl is None or ta is None: continue
                d = (kmz_lon - tl) ** 2 + (kmz_lat - ta) ** 2
                if d < best_d: best_d = d; best_idx = ti
            render_pts.append((kmz_lon, kmz_lat, best_idx))
    else:
        for ti, tp in enumerate(track_points):
            tl = tp.get("lon"); ta = tp.get("lat")
            if tl is None or ta is None: continue
            render_pts.append((tl, ta, ti))

    is_pagasa = any(p.get("cyclone_type") is not None for p in track_points)
    is_bt = []
    if is_pagasa:
        for ti in range(len(track_points)):
            r = track_points[ti].get("radius_km", 0)
            if r == 0 and ti + 1 < len(track_points):
                nr = track_points[ti+1].get("radius_km", 0)
                is_bt.append(nr == 0)
            else: is_bt.append(False)

    for i, (pt_lon, pt_lat, tp_idx) in enumerate(render_pts):
        p = track_points[tp_idx]; intensity = p.get("intensity"); pcat = p.get("intensity_category", "")
        if p.get("cyclone_type", "") == "AA": continue
        is_old = is_pagasa and tp_idx < len(is_bt) and is_bt[tp_idx]
        if is_old:
            bt_idx = sum(1 for j in range(tp_idx) if j < len(is_bt) and is_bt[j])
            if bt_idx % 2 == 0:
                ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5', markeredgewidth=0.5)
            else:
                ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='#42A5F5', markeredgewidth=0.5, markerfacecolor='white')
                ax.plot(pt_lon, pt_lat, 'o', color='#42A5F5', markersize=3,
                        transform=ccrs.PlateCarree(), zorder=7, markeredgecolor='#42A5F5', markeredgewidth=0)
            continue
        used_symbol = False
        if i == 0:
            ax.plot(pt_lon, pt_lat, 'o', color='#03fcfc', markersize=12,
                    transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white', markeredgewidth=1.5, alpha=0.8)
        if pcat:
            sym_name = _category_to_pag_sym(pcat, intensity, basin=_basin)
            if sym_name:
                if not sym_name.startswith("pagcat"):
                    sym_name = sym_name.replace("pag", "")
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
                        ax.add_artist(ab); used_symbol = True
                    except Exception: pass
            if not used_symbol:
                if i == 0:
                    ax.plot(pt_lon, pt_lat, 'o', color='#03fcfc', markersize=10,
                            transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white', markeredgewidth=1.0)
                else:
                    mk = _cat_color(intensity)
                    sz = 9 if intensity is not None and intensity >= 64 else 7
                    ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                            transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white', markeredgewidth=0.5)
        if not all_labels[tp_idx] or skip[tp_idx]: continue
        _key = f"{pt_lat:.4f},{pt_lon:.4f}"
        _r_m = _cone_radii.get(_key, 0)
        if _r_m > 0:
            _cos = math.cos(math.radians(pt_lat))
            _r_deg = _r_m / (111320.0 * max(_cos, 0.01))
            _off_px = _r_deg * min(_px_p_deg_lon, _px_p_deg_lat) * 1.5
            _base = max(round(_off_px * _pts_p_px), 30)
            _x_off = _base; _y_off = round(_base * 0.6)
        else: _x_off = 24; _y_off = 14
        _stair_pts = round(i * 5 * _pts_p_px)
        if algorithm in ("polar", "zigzag"):
            cross = _curvatures[tp_idx] if _curvatures else 0
            if abs(cross) > 0.03: side_right = cross > 0
            else: side_right = i % 2 == 0
            if side_right: xytext = (-_x_off, -_y_off - _stair_pts); ha = 'right'
            else: xytext = (_x_off, _y_off - _stair_pts); ha = 'left'
        elif bezier_offsets and tp_idx < len(bezier_offsets):
            if algorithm == "bezier":
                xytext = (bezier_offsets[tp_idx][0][0], bezier_offsets[tp_idx][0][1])
                ha = bezier_offsets[tp_idx][1]
            else:
                if _r_m > 0:
                    _d = math.hypot(*bezier_offsets[tp_idx][0])
                    if _d > 0:
                        _s = max(_x_off, _d) / _d
                        xytext = (round(bezier_offsets[tp_idx][0][0] * _s), round(bezier_offsets[tp_idx][0][1] * _s) - _stair_pts)
                    else:
                        xytext = (bezier_offsets[tp_idx][0][0], bezier_offsets[tp_idx][0][1] - _stair_pts)
                    ha = bezier_offsets[tp_idx][1]
                else:
                    xytext = (bezier_offsets[tp_idx][0][0], bezier_offsets[tp_idx][0][1] - _stair_pts)
                    ha = bezier_offsets[tp_idx][1]
        else:
            xytext = (_x_off, _y_off) if (i % 2 == 1) else (-_x_off, -_y_off)
            ha = 'left' if xytext[0] > 0 else 'right'
        pe = [path_effects.Stroke(linewidth=3.0, foreground='black'), path_effects.Normal()]
        ax.annotate("", (pt_lon, pt_lat), textcoords="offset points", xytext=xytext,
                    arrowprops={**lb_arrow, 'relpos': (0 if ha == 'left' else 1, 1)},
                    transform=ccrs.PlateCarree(), zorder=5)
        ax.annotate(all_labels[tp_idx], (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    fontsize=label_fs, color=label_color, ha=ha,
                    bbox=lb_bbox, path_effects=pe,
                    transform=ccrs.PlateCarree(), zorder=90)

    legend_path = sym_dir / "monleg.png"
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
                "bottom_left": (0.01, 0.01, 0, 0), "top_left": (0.01, 0.99, 0, 1),
                "top_right": (0.99, 0.99, 1, 1), "bottom_right": (0.99, 0.01, 1, 0),
            }
            lx, ly, ba_x, ba_y = pos_map.get(leg_pos, pos_map["bottom_left"])
            oi = OffsetImage(leg_arr, zoom=1, resample=False)
            ab = AnnotationBbox(oi, (lx, ly), frameon=False,
                                xycoords=ax.transAxes,
                                box_alignment=(ba_x, ba_y), zorder=10)
            ax.add_artist(ab)
        except Exception: pass

    pil_img = _fig_to_pil(fig, dpi, facecolor)
    pil_img = _draw_pil_title_box(pil_img, storm_name, track_points, dpi_scale, dpi, logo_path, "monwatch", utc_offset, source=_src, source_info=_title_info, basin=_basin)

    try:
        _monwatch_path = Path(__file__).resolve().parent.parent.parent / "public" / "images" / "MonWatch-wmark.png"
        if _monwatch_path.exists():
            _mw_logo = Image.open(str(_monwatch_path)).convert("RGBA")
            _mw_h = (int(pil_img.size[1] * 0.05) + 30) * 3
            _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
            _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
            _iw, _ih = pil_img.size
            pil_img.paste(_mw_logo, (int(_iw * 0.01), 0), _mw_logo)
    except Exception: pass

    pil_img = _add_disclaimer(pil_img, forecast_layout)
    pil_img = _add_forecast_footer(pil_img, track_points, storm_id, logo_path, settings, forecast_layout, light)

    if filename:
        pil_img.save(filename)
        return filename
    return np.array(pil_img)


def make_combined_forecast(all_tracks, settings=None, logo_path=None, light=True, filename=None,
                           dpi=150, figsize=(12, 10), margin=8.0, algorithm="polar", custom_title="",
                           resolution="10m", **kwargs):
    from .monwatch_handler import make_combined_forecast as _mw_combined
    return _mw_combined(all_tracks, settings, logo_path, light, filename,
                        dpi, figsize, margin, algorithm, custom_title, resolution, **kwargs)
