import sys
import json
import io
import os
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

from .utils import (
    _log, HAS_DEPS,
    ccrs, cfeature, plt, LONGITUDE_FORMATTER, LATITUDE_FORMATTER,
    _LAND, _OCEAN, _COASTLINE, _BORDERS,
    _add_psgc_ph_land, _add_psgc_boundaries,
    np, Image, ImageDraw, ImageFont,
    mpatches, path_effects, pd,
    Geodesic, OffsetImage, AnnotationBbox,
    _parse_kmz, _parse_dt, _parse_jtwc_dtg,
    _add_footer, _cat_color, _cat_label, _cat_sym,
    _category_to_sym, _compute_bezier_offsets, _compute_curvatures,
    _cascade_bezier_offsets, _build_cone_polygon,
)
from .shared_flow import (
    _compute_single_extent, _compute_combined_extent,
    _build_track_labels, _find_first_future_idx, _fig_to_pil,
    _draw_pil_title_box, _draw_combined_title_box,
    _add_forecast_footer, _add_combined_footer, _add_disclaimer,
    _fetch_jma_model_tracks, _compute_unified_offsets, _NEW_ALGO_NAMES,
    _normalize_track_extent, _adjust_lons_for_plot, _clean_storm_name,
)

script_dir = Path(__file__).resolve().parent.parent
forecast_layout = "nhc"


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

    # DEFAULT extent (dateline-aware)
    _dl_min, _dl_max, central_lon, _ = _normalize_track_extent(lons, margin)
    lon_min = _dl_min
    lon_max = _dl_max
    lat_min = max(min(lats) - margin, -90)
    lat_max = min(max(lats) + margin, 90)

    # NHC tropycal path: use native rendering for official NHC forecast maps
    if storm_id:
        try:
            from tropycal import realtime as _tc
            _rt = _tc.Realtime()
            _storm = _rt.get_storm(storm_id)
            if _storm is not None:
                _ax = _storm.plot_forecast_realtime(
                    track_labels="fhr_wind_kt", cone_days=5,
                    domain="dynamic_forecast",
                    map_prop={'figsize': figsize, 'linewidth': 0.6,
                              'land_color': '#e8e0d8', 'ocean_color': '#deecf4'},
                    prop={'cone_lw': 1.5, 'cone_alpha': 0.15},
                )
                _fig = _ax.figure
                _buf = io.BytesIO()
                _fig.savefig(_buf, format='png', dpi=dpi, bbox_inches='tight',
                             facecolor=facecolor, edgecolor='none')
                _buf.seek(0)
                _pil_img = Image.open(_buf).convert("RGBA")
                _buf.close()
                plt.close(_fig)
                if light and logo_path:
                    _storm_name = _storm.to_dict().get("name", storm_name)
                    try:
                        _td = _storm.to_dataframe()
                        _forecast_dt = ""
                        if _td is not None and len(_td) > 0:
                            _row = _td.iloc[0]
                            if hasattr(_row.get('time'), 'strftime'):
                                _fdt = _row['time']
                                _forecast_dt = _fdt.strftime("%Y-%m-%d %H:%M UTC")
                    except Exception:
                        _forecast_dt = ""
                    _now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    _basin = ("East Pacific" if storm_id.startswith("ep")
                              else "Atlantic" if storm_id.startswith("al")
                              else "Central Pacific")
                    _utc_offset = fcst_prefs.get("utc_offset", 0)
                    _pil_img = _add_footer(_pil_img, _forecast_dt, _now_utc, _basin, logo_path, _utc_offset)
                if filename:
                    _pil_img.save(filename)
                    return filename
                return np.array(_pil_img)
        except Exception:
            pass

    # Fallback to manual drawing

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor=facecolor)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
    _log(f"[fcst] storm={storm_name} id={storm_id} layout=nhc light={light} "
         f"dpi={dpi} figsize={figsize} extent=[{lon_min:.2f}, {lon_max:.2f}, {lat_min:.2f}, {lat_max:.2f}]",
         file=sys.stderr)

    # Default light/dark map style (common.py lines 952-985)
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

    # NHC-style track line (common.py lines 1346-1349)
    if len(track_points) >= 2:
        tlons = [p["lon"] for p in track_points if p.get("lon") is not None]
        tlats = [p["lat"] for p in track_points if p.get("lat") is not None]
        if tlons:
            _plons = _adjust_lons_for_plot(tlons, central_lon)
            track_color = '#cc0000'
            track_lw = 2.5
            ax.plot(_plons, tlats, color=track_color, linewidth=track_lw,
                    transform=ccrs.PlateCarree(), zorder=5)

    # NHC-style labels (common.py lines 1440-1443)
    label_fs = round(6.5 * dpi_scale, 1)
    lb_bbox = dict(boxstyle='round,pad=0.15', fc=label_bg, ec='#222222', alpha=0.9)
    lb_arrow = dict(arrowstyle='-', lw=1.0, color='#333333')

    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")

    sym_dir = script_dir.parent / "public" / "images" / "symbols"

    # Build labels for each point
    all_labels = _build_track_labels(track_points, forecast_layout, show_dt, show_wind,
                                     time_fmt, utc_offset, wind_unit, issued_dtg)

    # Draw points and labels
    for i, p in enumerate(track_points):
        pt_lon = p.get("lon")
        pt_lat = p.get("lat")
        intensity = p.get("intensity")
        pcat = p.get("intensity_category", "")
        if pt_lon is None or pt_lat is None:
            continue

        # Draw symbol
        used_symbol = False
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

        # Label
        if not all_labels[i]:
            continue
        _x_off, _y_off = 24, 14
        _stair_pts = round(i * 5 * (72.0 / dpi))
        if i % 2 == 0:
            xytext = (-_x_off, -_y_off - _stair_pts)
            ha = 'right'
        else:
            xytext = (_x_off, _y_off - _stair_pts)
            ha = 'left'

        ax.annotate("", (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    arrowprops={**lb_arrow, 'relpos': (0 if ha == 'left' else 1, 1)},
                    transform=ccrs.PlateCarree(), zorder=1)
        ax.annotate(all_labels[i], (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    fontsize=label_fs, color=label_color, ha=ha,
                    bbox=lb_bbox,
                    transform=ccrs.PlateCarree(), zorder=90)

    # Legend: 'final_track.png'
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

    # NHC-style title
    title_parts = [f"NHC FORECAST  |  {storm_name}  ({storm_id.upper()})"]
    ax.set_title("  ".join(title_parts), fontsize=round(10 * dpi_scale, 1),
                 color=title_color, fontweight='bold', fontfamily='monospace')

    # Convert fig to PIL
    pil_img = _fig_to_pil(fig, dpi, facecolor)

    # Disclaimer
    pil_img = _add_disclaimer(pil_img, forecast_layout)

    # Footer with logo
    pil_img = _add_forecast_footer(pil_img, track_points, storm_id, logo_path, settings, forecast_layout, light)

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

    # Use shared combined extent (default, not cone-aware)
    extent_result = _compute_combined_extent(all_tracks, forecast_layout, margin, dpi, figsize, logo_path)
    if extent_result[0] is None:
        _log("[fcst] SKIP: no valid coordinates", file=sys.stderr)
        return None
    lon_min, lon_max, lat_min, lat_max, central_lon = extent_result

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor='white' if light else '#1a1a2e')
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # Default light/dark map style
    if light:
        ax.set_facecolor('white')
        ax.add_feature(_LAND, edgecolor='gray', linewidth=0.5)
        ax.add_feature(_OCEAN, facecolor='#c8e6f5', alpha=0.5)
        ax.add_feature(_BORDERS, edgecolor='gray', linewidth=0.4, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='gray', linewidth=0.2, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.7, color='gray', alpha=0.6, linestyle='--')
        gl.top_labels = False; gl.right_labels = False
        gl.xlabel_style = {'size': round(10 * dpi_scale, 6), 'color': 'black', "weight": "bold"}
        gl.ylabel_style = {'size': round(10 * dpi_scale, 6), 'color': 'black', "rotation": 90, "weight": "bold"}
        gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
        label_color = 'black'
    else:
        ax.set_facecolor('#1a1a2e')
        ax.add_feature(_LAND, edgecolor='gray', linewidth=0.5)
        ax.add_feature(_OCEAN, facecolor='#0d1b2a', alpha=0.8)
        ax.add_feature(_BORDERS, edgecolor='#556677', linewidth=0.3, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#556677', linewidth=0.15, linestyle=':', zorder=3)
        gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                          linewidth=0.5, color='#4a6a8a', alpha=0.6, linestyle='--')
        gl.top_labels = False; gl.right_labels = False
        gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#c0d0e0', "weight": "bold"}
        gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#c0d0e0', "rotation": 90, "weight": "bold"}
        label_color = 'white'

    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")
    show_labels = fcst_prefs.get("show_labels", True)

    label_fs = round(6.5 * dpi_scale, 1)
    lb_bbox = dict(boxstyle='round,pad=0.15', fc='white' if light else '#00000088', ec='#222222', alpha=0.9)
    lb_arrow = dict(arrowstyle='-', lw=1.0, color='#333333')

    TRACK_COLORS = ['#FF6B35', '#00BFFF', '#FFD700', '#00FF9F', '#FF69B4']
    sym_dir = script_dir.parent / "public" / "images" / "symbols"

    for idx, t in enumerate(all_tracks):
        pts = t.get("track_points", [])
        if not pts:
            continue
        storm_name = t.get("storm_name", t.get("storm_id", f"Track {idx+1}"))
        color = TRACK_COLORS[idx % len(TRACK_COLORS)]
        tlons = [p["lon"] for p in pts if p.get("lon") is not None]
        tlats = [p["lat"] for p in pts if p.get("lat") is not None]
        if len(tlons) < 2:
            continue

        # Track line
        _plons = _adjust_lons_for_plot(tlons, central_lon)
        ax.plot(_plons, tlats, color=color, linewidth=2.5,
                transform=ccrs.PlateCarree(), zorder=5)

        # Labels
        issued_dtg = t.get("issued_dtg", "")
        if show_labels:
            all_labels = _build_track_labels(pts, forecast_layout, show_dt, show_wind,
                                             time_fmt, utc_offset, wind_unit,
                                             issued_dtg)
        else:
            all_labels = [""] * len(pts)

        for i, p in enumerate(pts):
            pt_lon = p.get("lon")
            pt_lat = p.get("lat")
            intensity = p.get("intensity")
            pcat = p.get("intensity_category", "")
            if pt_lon is None or pt_lat is None:
                continue

            used_symbol = False
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

            if not all_labels[i]:
                continue
            _x_off, _y_off = 24, 14
            if (idx + i) % 2 == 0:
                xytext = (-_x_off, -_y_off)
                ha = 'right'
            else:
                xytext = (_x_off, _y_off)
                ha = 'left'

            ax.annotate("", (pt_lon, pt_lat),
                        textcoords="offset points", xytext=xytext,
                        arrowprops={**lb_arrow, 'relpos': (0 if ha == 'left' else 1, 1)},
                        transform=ccrs.PlateCarree(), zorder=1)
            ax.annotate(all_labels[i], (pt_lon, pt_lat),
                        textcoords="offset points", xytext=xytext,
                        fontsize=label_fs, color=label_color, ha=ha,
                        bbox=lb_bbox,
                        transform=ccrs.PlateCarree(), zorder=90)

    # NHC-style title for combined
    storm_names = []
    for _t in all_tracks:
        _raw = _t.get("storm_name", _t.get("storm_id", ""))
        _src = _t.get("source", "")
        intl_name = _clean_storm_name(_raw, source=_src)
        if _src:
            storm_names.append(f"{intl_name} ({_src.upper()})")
        else:
            storm_names.append(intl_name)
    names_str = ", ".join(storm_names)
    ax.set_title(f"NHC COMBINED FORECAST  |  {names_str}",
                 fontsize=round(10 * dpi_scale, 1),
                 color=label_color, fontweight='bold', fontfamily='monospace')

    # Convert fig to PIL
    pil_img = _fig_to_pil(fig, dpi, fig.get_facecolor())

    # Disclaimer
    pil_img = _add_disclaimer(pil_img, forecast_layout)

    # Footer with logo
    pil_img = _add_combined_footer(pil_img, logo_path, settings, forecast_layout)

    if filename:
        pil_img.save(filename)
        return filename

    return np.array(pil_img)
