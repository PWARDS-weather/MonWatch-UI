import sys
import json
import io
import os
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

LOG_PREFIX = "[ALERTMAP]"
from pathlib import Path
from datetime import datetime, timezone, timedelta

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
    import shapely.geometry as sgeom
    from shapely.ops import unary_union
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

LOG_PREFIX = "ALERT_MAP:"

script_dir = Path(__file__).resolve().parent
_src_dir = str(script_dir.parent.parent / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
_psgc_data_dir = script_dir.parent.parent / "data" / "psgc_shapefiles"

# Global caches for PSGC shapefiles (loaded once, reused across calls)
_PSGC_CACHE = {
    "loader": None,
    "province_geoms": None,
    "municipality_geoms": None,
}

_SEVERITY_COLORS = {
    "Extreme": "#AA00FF",
    "Severe": "#FF0000",
    "Moderate": "#FF8800",
    "Minor": "#FFDD00",
    "Unknown": "#888888",
}

_SEVERITY_LABELS = {
    "Extreme": "PURPLE - EXTREME",
    "Severe": "RED - SEVERE",
    "Moderate": "ORANGE - MODERATE",
    "Minor": "YELLOW - MINOR",
    "Unknown": "GRAY - UNKNOWN",
}

# PAGASA Regional Services Division (PRSD) bounding boxes
# Format: (min_lon, max_lon, min_lat, max_lat)
PRSD_EXTENTS = {
    "NCRPRSD": (115.75, 125.50, 12.50, 18.00),
    "NLPRSD": (115.50, 125.75, 13.50, 21.25),
    "SLPRSD": (120.75, 125.30, 11.75, 14.50),
    "VPRSD": (110.00, 126.25, 5.00, 18.00),
    "MINPRSD": (119.30, 126.75, 5.30, 10.25),
}

# Map known PAGASA publisher codes to PRSD extent keys
_PRSD_PUBLISHER_MAP = {
    "NCRPRSD": "NCRPRSD",
    "NLPRSD": "NLPRSD",
    "SLPRSD": "SLPRSD",
    "PRSD": "SLPRSD",
    "VPRSD": "VPRSD",
    "VISPRSD": "VPRSD",
    "MINPRSD": "MINPRSD",
}

def _get_psgc_loader():
    """Get or create cached PSGC shapefile loader (singleton)."""
    if _PSGC_CACHE["loader"] is None:
        try:
            from psgc_shapefiles import PSGCShapefileLoader
            _PSGC_CACHE["loader"] = PSGCShapefileLoader(data_dir=_psgc_data_dir)
        except Exception:
            pass
    return _PSGC_CACHE["loader"]

def _get_cached_geoms(level):
    """Get cached geometry list for a PSGC level (provinces/municipalities)."""
    key = f"{level}_geoms"
    if _PSGC_CACHE.get(key) is None:
        loader = _get_psgc_loader()
        if loader:
            _PSGC_CACHE[key] = loader.get_all_geometries(level)
    return _PSGC_CACHE.get(key)

def _prsd_extent_for_alert(alert):
    """Determine PRSD extent from alert's published_by or sender field."""
    pub = (alert.get("published_by") or "").upper().strip().replace("-", "")
    mapped = _PRSD_PUBLISHER_MAP.get(pub)
    if mapped:
        return PRSD_EXTENTS.get(mapped)
    sender = (alert.get("sender") or "").upper().strip().replace("-", "")
    mapped = _PRSD_PUBLISHER_MAP.get(sender)
    if mapped:
        return PRSD_EXTENTS.get(mapped)
    return None

def _get_prsd_name(alert):
    """Get the canonical PRSD abbreviation from an alert."""
    pub = (alert.get("published_by") or alert.get("sender") or "").upper().strip().replace("-", "")
    return _PRSD_PUBLISHER_MAP.get(pub, "")

_SEVERITY_ORDER = {
    "Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1,
}

def _severity_color(severity):
    return _SEVERITY_COLORS.get(severity, "#888888")

def _polygon_from_coords(coords):
    """Convert GeoJSON polygon coords to shapely Polygon (lon, lat)."""
    if not coords or not coords[0]:
        return None
    try:
        ring = [(c[0], c[1]) for c in coords[0] if len(c) >= 2 and c[0] is not None and c[1] is not None]
        if len(ring) < 3:
            return None
        # Check for NaN/Inf
        for x, y in ring:
            if x != x or y != y or abs(x) == float('inf') or abs(y) == float('inf'):
                return None
        return sgeom.Polygon(ring)
    except Exception:
        return None

def _parse_alert_polygons(alert):
    """Extract list of shapely Polygons from alert's geometry."""
    polygons = []
    geo = alert.get("geometry", {})
    if not geo:
        return polygons
    
    def extract_polygons_from_geometry(geom):
        if not geom:
            return
        gtype = geom.get("type")
        coords = geom.get("coordinates", [])
        if gtype == "Polygon":
            p = _polygon_from_coords(coords)
            if p and p.is_valid and not p.is_empty:
                polygons.append(p)
        elif gtype == "MultiPolygon":
            for poly_coords in coords:
                p = _polygon_from_coords(poly_coords)
                if p and p.is_valid and not p.is_empty:
                    polygons.append(p)
        elif gtype == "GeometryCollection":
            for g in geom.get("geometries", []):
                extract_polygons_from_geometry(g)
    
    extract_polygons_from_geometry(geo)
    return polygons

_JAPAN_EXTENT = (125, 150, 25, 47)

def _compute_extent(alerts, margin=2.0, force_philippines=False):
    """Compute map extent from alert polygons with margin."""
    all_lons, all_lats = [], []
    has_pagasa = False
    has_nws = False
    has_jma = False
    for alert in alerts:
        src = alert.get("source", "NWS")
        if src == "PAGASA":
            has_pagasa = True
        elif src == "JMA" or src == "JTWC":
            has_jma = True
        else:
            has_nws = True
        for p in _parse_alert_polygons(alert):
            xs, ys = p.exterior.xy
            all_lons.extend(xs)
            all_lats.extend(ys)
    
    PHILIPPINES_EXTENT = (114, 130, 4, 23)
    
    # For single PAGASA thunderstorm, use PRSD-based zoom if available
    if has_pagasa and not has_nws and not has_jma and len(alerts) == 1:
        prsd_ext = _prsd_extent_for_alert(alerts[0])
        # Also compute extent from actual alert polygons
        alert_polygons = _parse_alert_polygons(alerts[0])
        if alert_polygons:
            all_lons, all_lats = [], []
            for p in alert_polygons:
                xs, ys = p.exterior.xy
                all_lons.extend(xs)
                all_lats.extend(ys)
            if all_lons and all_lats:
                # Tight margin for zoomed-in view (10% of span, min 0.3 deg)
                lon_span_raw = max(all_lons) - min(all_lons)
                lat_span_raw = max(all_lats) - min(all_lats)
                margin_lon = max(lon_span_raw * 0.15, 0.3)
                margin_lat = max(lat_span_raw * 0.15, 0.3)
                
                min_lon = max(min(all_lons) - margin_lon, -180)
                max_lon = min(max(all_lons) + margin_lon, 180)
                min_lat = max(min(all_lats) - margin_lat, -90)
                max_lat = min(max(all_lats) + margin_lat, 90)
                
                # Adjust to match 16:9 figure aspect ratio
                lon_span = max_lon - min_lon
                lat_span = max_lat - min_lat
                target_ratio = 16.0 / 9.0
                current_ratio = lon_span / lat_span
                
                if current_ratio > target_ratio:
                    # Extent is wider than 16:9 - expand lat span
                    new_lat_span = lon_span / target_ratio
                    center_lat = (min_lat + max_lat) / 2
                    min_lat = center_lat - new_lat_span / 2
                    max_lat = center_lat + new_lat_span / 2
                else:
                    # Extent is taller than 16:9 - expand lon span
                    new_lon_span = lat_span * target_ratio
                    center_lon = (min_lon + max_lon) / 2
                    min_lon = center_lon - new_lon_span / 2
                    max_lon = center_lon + new_lon_span / 2
                
                return (min_lon, max_lon, min_lat, max_lat)
        # Fallback to PRSD extent if no polygons
        if prsd_ext:
            return prsd_ext

    if force_philippines or (has_pagasa and not has_nws and not has_jma):
        return PHILIPPINES_EXTENT
    
    # Fallback: if no polygons
    if not all_lons:
        if has_pagasa:
            return PHILIPPINES_EXTENT
        if has_jma:
            return _JAPAN_EXTENT
        # Default fallback (US)
        return (-130, -60, 20, 50)
    
    min_lon = max(min(all_lons) - margin, -180)
    max_lon = min(max(all_lons) + margin, 180)
    min_lat = max(min(all_lats) - margin, -90)
    max_lat = min(max(all_lats) + margin, 90)
    # Ensure minimum extent for context
    if max_lon - min_lon < 5:
        cx = (min_lon + max_lon) / 2
        min_lon = cx - 2.5
        max_lon = cx + 2.5
    if max_lat - min_lat < 5:
        cy = (min_lat + max_lat) / 2
        min_lat = cy - 2.5
        max_lat = cy + 2.5
    return (min_lon, max_lon, min_lat, max_lat)

def _add_footer(pil_img, text, logo_path=None, dark=False):
    mw, mh = pil_img.size
    footer_h = max(32, int(mh * 0.04))
    font_size = max(8, int(footer_h * 0.35))
    try:
        font = ImageFont.truetype("consola.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()
    bg = (0, 0, 0, 255) if dark else (255, 255, 255, 255)
    tc = "white" if dark else "black"
    footer_img = Image.new("RGBA", (mw, footer_h), bg)
    draw = ImageDraw.Draw(footer_img)
    logo_w = 0
    if logo_path and Path(logo_path).exists():
        try:
            logo = Image.open(str(logo_path)).convert("RGBA")
            logo_h = footer_h - 4
            lw = int(logo.width * logo_h / logo.height) if logo.height > 0 else logo_h
            logo = logo.resize((lw, logo_h), Image.LANCZOS)
            footer_img.paste(logo, (4, 2), logo)
            logo_w = lw + 8
        except Exception:
            pass
    bbox = draw.textbbox((0, 0), text, font=font)
    th = bbox[3] - bbox[1]
    draw.text((logo_w + 4, (footer_h - th) // 2), text, fill=tc, font=font)
    brand = "MonWatch-UI"
    bbox2 = draw.textbbox((0, 0), brand, font=font)
    bw = bbox2[2] - bbox2[0]
    draw.text((mw - bw - 8, (footer_h - th) // 2), brand, fill=tc, font=font)
    combined = Image.new("RGBA", (mw, mh + footer_h), (0, 0, 0, 0))
    combined.paste(pil_img, (0, 0), pil_img)
    combined.paste(footer_img, (0, mh), footer_img)
    return combined.convert("RGB")

def _load_alert_settings(settings_path):
    """Load timezone preferences from settings.json."""
    if not settings_path or not os.path.exists(settings_path):
        return {}
    try:
        with open(settings_path) as f:
            s = json.load(f)
        return s.get("forecast_preferences", {})
    except Exception:
        return {}

def _format_local_time(dt_str, utc_offset=8, time_format="civilian"):
    """Parse an ISO datetime string and format to local time.

    If the string has timezone info (e.g. NWS UTC dates with 'Z'), converts
    from UTC to local. If naive (e.g. PAGASA dates), assumes PHT (UTC+8)
    and converts to local. Returns 'June 29, 2026, 11:50PM' or empty string.
    """
    if not dt_str:
        return ""
    try:
        s = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo:
            # Has timezone info (NWS UTC) → convert from UTC to local
            dt_utc = dt.astimezone(timezone.utc).replace(tzinfo=None)
            local_dt = dt_utc + timedelta(hours=utc_offset)
        else:
            # Naive datetime (PAGASA) → assume PHT (UTC+8), convert to local
            local_dt = dt - timedelta(hours=8) + timedelta(hours=utc_offset)
        if time_format == "civilian":
            h = local_dt.hour % 12 or 12
            ampm = "AM" if local_dt.hour < 12 else "PM"
            return f"{local_dt.strftime('%B')} {local_dt.day}, {local_dt.year}, {h}:{local_dt.minute:02d}{ampm}"
        else:
            return f"{local_dt.strftime('%B')} {local_dt.day}, {local_dt.year}, {local_dt.hour:02d}:{local_dt.minute:02d}"
    except Exception:
        return ""

def make_alert_map(alerts, settings=None, logo_path=None, light=False, filename=None, force_philippines=False):
    import sys
    import time
    if not HAS_DEPS:
        return None
    t0 = time.time()
    print(f"{LOG_PREFIX} START {datetime.now().strftime('%H:%M:%S')}", file=sys.stderr)
    dark = not light
    bg_color = '#0a0a1a' if dark else '#f5f5f5'
    label_color = '#cccccc' if dark else '#333333'
    extent = _compute_extent(alerts, margin=2.0, force_philippines=force_philippines)
    min_lon, max_lon, min_lat, max_lat = extent
    central_longitude = (min_lon + max_lon) / 2
    central_latitude = (min_lat + max_lat) / 2
    dpi = 150
    max_dim = 8.0
    # Check if single PAGASA alert - use two-panel layout
    single_alert = alerts[0] if len(alerts) == 1 else None
    is_single_pagasa = single_alert and single_alert.get("source") == "PAGASA"
    
    if is_single_pagasa:
        # Two-panel layout: map (70%) + info sidebar (30%)
        fig_w = max_dim
        fig_h = max_dim * 9.0 / 16.0
        fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
        fig.patch.set_facecolor(bg_color)
        
        # Map subplot (left 70%)
        ax = fig.add_subplot(1, 2, 1, projection=ccrs.PlateCarree(central_longitude=central_longitude), facecolor=bg_color)
        # Info subplot (right 30%)
        ax_info = fig.add_subplot(1, 2, 2)
        ax_info.set_facecolor(bg_color)
        ax_info.axis('off')
        
        # Set map extent
        ax.set_extent(extent, crs=ccrs.PlateCarree())
        ax.add_feature(_OCEAN, zorder=1)
    else:
        # Standard single-panel layout
        fig_w = max_dim
        fig_h = max_dim * 9.0 / 16.0
        fig = plt.figure(figsize=(fig_w, fig_h), dpi=dpi)
        fig.patch.set_facecolor(bg_color)
        proj = ccrs.PlateCarree(central_longitude=central_longitude)
        ax = fig.add_subplot(1, 1, 1, projection=proj, facecolor=bg_color)
        ax.set_extent(extent, crs=ccrs.PlateCarree())
        ax.add_feature(_OCEAN, zorder=1)
    print(f"{LOG_PREFIX} FIGURE_SETUP {time.time()-t0:.1f}s", file=sys.stderr)

    # Detect Philippines-focused extent
    _has_ph = force_philippines or (
        any(a.get("source") == "PAGASA" for a in alerts) and
        not any(a.get("source") in ("NWS", "JMA", "JTWC") for a in alerts)
    )
    if not _has_ph:
        ax.add_feature(_LAND, edgecolor='#000000', linewidth=0.5, alpha=1, zorder=2)
    if _has_ph:
        # Use cached PSGC region SHP files for Philippines land base
        try:
            loader = _get_psgc_loader()
            if loader:
                region_geoms = loader.get_all_geometries("regions")
                if region_geoms:
                    ax.add_geometries(region_geoms, crs=ccrs.PlateCarree(),
                                      facecolor=cfeature.COLORS['land'],
                                      edgecolor='#000000', linewidth=0.5, zorder=3)
        except Exception:
            pass
    else:
        # Non-Philippines views can still use NaturalEarth admin boundaries
        try:
            _STATES = cfeature.NaturalEarthFeature('cultural', 'admin_1_states_provinces_lines', '10m',
                edgecolor=label_color, facecolor='none', linewidth=0.3)
            ax.add_feature(_STATES, alpha=1.0, zorder=3, linewidth=0.5)
        except Exception:
            pass
        try:
            _COUNTIES = cfeature.NaturalEarthFeature('cultural', 'admin_2_counties', '10m',
                edgecolor=label_color, facecolor='none', linewidth=0.25)
            ax.add_feature(_COUNTIES, alpha=1.0, zorder=3)
        except Exception:
            pass
    grid = ax.gridlines(draw_labels=True, linestyle='--', alpha=0.3, color=label_color,
                        linewidth=0.3, xlocs=range(-180, 181, 5), ylocs=range(-90, 91, 5))
    grid.top_labels = False
    grid.right_labels = False
    grid.xlabel_style = {'color': label_color, 'size': 7}
    grid.ylabel_style = {'color': label_color, 'size': 7}
    print(f"{LOG_PREFIX} BASEMAP done {time.time()-t0:.1f}s", file=sys.stderr)
    print(f"{LOG_PREFIX} GRID done {time.time()-t0:.1f}s", file=sys.stderr)
    
    # Check if single PAGASA alert
    single_alert = alerts[0] if len(alerts) == 1 else None
    is_single_pagasa = single_alert and single_alert.get("source") == "PAGASA"
    is_pagasa_ts = is_single_pagasa and "THUNDERSTORM" in single_alert.get("event", "").upper()
    
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    fp = _load_alert_settings(settings)
    utc_offset = fp.get("utc_offset", 8)
    time_format = fp.get("time_format", "civilian")
    
    if is_single_pagasa:
        # For single PAGASA alerts, render PSGC province polygons
        _render_pagasa_provinces(ax, single_alert, dark)
        # Render info panel on the right
        _render_pagasa_info_panel(ax_info, single_alert, dark, label_color, utc_offset, time_format)
    else:
        # Standard severity-based rendering
        _render_severity_polygons(ax, alerts, dark, label_color)
    print(f"{LOG_PREFIX} RENDER done {time.time()-t0:.1f}s", file=sys.stderr)
    
    # Build title
    if is_single_pagasa:
        event_name = single_alert.get("event", "Weather Alert")
        effective = single_alert.get("effective", "")
        local_time = _format_local_time(effective, utc_offset, time_format)
        region = _get_prsd_name(single_alert)
        parts = [event_name]
        if local_time:
            parts.append(local_time)
        if region:
            parts.append(region)
        title = " - ".join(parts)
    else:
        title = f"Weather Alerts  \u2014  {now_str}"
    
    ax.set_title(title, fontsize=12, color=label_color, fontweight='600',
                 fontfamily='sans-serif', pad=10)
    fig.subplots_adjust(bottom=0.08)
    
    if logo_path:
        # Save directly to file (fast), then open for footer
        fig.savefig(filename, dpi=dpi, facecolor=fig.get_facecolor())
        plt.close(fig)
        print(f"{LOG_PREFIX} SAVE done {time.time()-t0:.1f}s", file=sys.stderr)
        
        # Add footer using PIL
        pil_img = Image.open(filename).convert("RGBA")
        footer_text = f"Weather Alerts \u2014 Generated {now_str}  |  {len(alerts)} active alerts"
        pil_img = _add_footer(pil_img, footer_text, logo_path, dark=dark)
        pil_img.save(filename)
        print(f"{LOG_PREFIX} FOOTER done {time.time()-t0:.1f}s", file=sys.stderr)
        return filename
    else:
        # Direct save without PIL overhead
        if filename:
            fig.savefig(filename, dpi=dpi, facecolor=fig.get_facecolor())
            plt.close(fig)
            print(f"{LOG_PREFIX} SAVE done {time.time()-t0:.1f}s", file=sys.stderr)
            return filename
        else:
            buf = io.BytesIO()
            fig.savefig(buf, format='png', dpi=dpi, facecolor=fig.get_facecolor())
            buf.seek(0)
            pil_img = Image.open(buf).convert("RGBA")
            buf.close()
            plt.close(fig)
            return pil_img


def _render_severity_polygons(ax, alerts, dark, label_color):
    """Original severity-based polygon rendering."""
    severity_polygons = {}
    sevs_in_alerts = set()
    for alert in alerts:
        sev = alert.get("severity", "Unknown")
        sevs_in_alerts.add(sev)
        polygons = _parse_alert_polygons(alert)
        if polygons:
            severity_polygons.setdefault(sev, []).extend(polygons)
    sorted_sevs = sorted(sevs_in_alerts, key=lambda s: _SEVERITY_ORDER.get(s, 0), reverse=True)
    legend_handles = []
    for sev in sorted_sevs:
        color = _severity_color(sev)
        polygons = severity_polygons.get(sev)
        if polygons:
            merged = unary_union(polygons) if len(polygons) > 1 else polygons[0]
            if hasattr(merged, 'geoms'):
                for geom in merged.geoms:
                    ax.add_geometries([geom], crs=ccrs.PlateCarree(),
                                      facecolor=color, edgecolor='#000000', linewidth=0.5,
                                      alpha=0.35, zorder=4)
            else:
                ax.add_geometries([merged], crs=ccrs.PlateCarree(),
                                  facecolor=color, edgecolor='#000000', linewidth=0.5,
                                  alpha=0.35, zorder=4)
            legend_handles.append(plt.Line2D([0], [0], color=color, linewidth=3, alpha=0.7, label=_SEVERITY_LABELS.get(sev, sev)))
    if legend_handles:
        leg = ax.legend(handles=legend_handles, loc='lower right', framealpha=0.85,
                        fontsize=8, facecolor='#1a1a1a' if dark else 'white',
                        edgecolor='gray', labelcolor='white' if dark else 'black')
        leg.set_zorder(20)


def _render_pagasa_provinces(ax, alert, dark):
    """Render PAGASA alert provinces using PSGC shapefile boundaries.

    For alerts with affecting/expecting type fields (thunderstorms), uses
    two-color scheme. For other alerts (flood, rainfall), uses a single
    color based on alert severity. Embedded shape data is ignored.
    """
    import sys
    import time
    t_start = time.time()
    print(f"{LOG_PREFIX} _render_pagasa_provinces START", file=sys.stderr)
    provinces = alert.get("provinces", {})
    if not provinces:
        return
    
    psgc = _get_psgc_loader()
    if psgc is None:
        return
    
    # Province border lines from cached SHP data
    prov_geoms = _get_cached_geoms("provinces")
    if prov_geoms:
        ax.add_geometries(prov_geoms, crs=ccrs.PlateCarree(),
                          facecolor='none', edgecolor='#000000', linewidth=1, alpha=1.0, zorder=4)
    # Municipality border lines disabled (too many polygons: 1642)
    # muni_geoms = _get_cached_geoms("municipalities")
    # if muni_geoms:
    #     ax.add_geometries(muni_geoms, crs=ccrs.PlateCarree(),
    #                       facecolor='none', edgecolor='#ffffff', linewidth=0.3, alpha=1.0, zorder=4)
    
    def _normalize_gc(gc):
        """Normalize geocode: extract string from dict format {'value': '087800000'}."""
        if isinstance(gc, dict):
            return str(gc.get("value", "")).strip()
        return str(gc).strip() if gc else ""

    def _lookup_best(gc, prov_name=None):
        """Look up geometry: province first (by geocode then name), then municipality, then barangay."""
        if not gc and not prov_name:
            return None, None
        gc = _normalize_gc(gc)
        geom = psgc.get_province_polygon(gc) if gc else None
        if geom is not None:
            return geom, "province"
        if prov_name:
            geom = psgc.get_province_by_name(prov_name)
            if geom is not None:
                return geom, "province"
        geom = psgc.get_municipality_polygon(gc) if gc else None
        if geom is not None:
            return geom, "municipality"
        geom = psgc.get_barangay_polygon(gc) if gc else None
        if geom is not None:
            return geom, "barangay"
        return None, None

    def _collect_polygons(prov):
        """Collect polygons from a province entry, using PSGC only.
        Prioritizes municipality-level polygons over province-level."""
        gc = prov.get("geocode", "")
        if isinstance(gc, dict):
            gc = gc.get("value", "")
        prov_name = prov.get("province", "")
        ptype = prov.get("type") or "expecting"
        polys = []

        def _gc_str(v):
            if isinstance(v, dict):
                return str(v.get("value", "")).strip()
            return str(v).strip() if v else ""

        # First try: individual municipality by psgc_code (flat list format)
        psgc_code = _gc_str(prov.get("psgc_code", ""))
        if psgc_code:
            geom = psgc.get_municipality_polygon(psgc_code)
            if geom is not None:
                polys.append(geom)
                return polys, ptype
            geom = psgc.get_province_polygon(psgc_code)
            if geom is not None:
                polys.append(geom)
                return polys, ptype

        # Second try: municipality from sub-items (nested format)
        munis = prov.get("municipalities", prov.get("municipality", []))
        if munis:
            items = munis.values() if isinstance(munis, dict) else (munis if isinstance(munis, list) else [])
            for m in items:
                mg = _gc_str(m.get("psgc_code") or m.get("geocode", "") if isinstance(m, dict) else "")
                if mg:
                    geom = psgc.get_municipality_polygon(mg)
                    if geom is not None:
                        polys.append(geom)
            if polys:
                return polys, ptype

        # Fallback: province-level polygon via _lookup_best
        geom, level = _lookup_best(gc, prov_name)
        if geom is not None:
            polys.append(geom)

        return polys, ptype
    
    # Determine color scheme
    _TYPE_COLORS = {
        "purple": ("#AA00FF", "#AA00FF"),
        "red": ("#FF0000", "#FF4444"),
        "orange": ("#FF8800", "#FFAA00"),
        "yellow": ("#CCAA00", "#FFDD00"),
        "affecting": ("#00008B", "#0000CD"),
        "expecting": ("#4169E1", "#87CEFA"),
    }

    # Collect polygons grouped by actual type name
    typed_polygons = {}
    for prov in (provinces.values() if isinstance(provinces, dict) else provinces):
        if not isinstance(prov, dict) or "province" not in prov:
            continue
        polys, ptype = _collect_polygons(prov)
        if not polys:
            continue
        typed_polygons.setdefault(ptype, []).extend(polys)
    print(f"{LOG_PREFIX} _collect_polygons done {time.time()-t_start:.1f}s", file=sys.stderr)

    # Determine if any province has a typed label (affecting/expecting/color)
    has_typed_provinces = any(
        t not in ("", "expecting") for t in typed_polygons
    )

    legend_handles = []
    if has_typed_provinces:
        # Render each type with its mapped color
        type_order = ["purple", "red", "orange", "yellow", "affecting", "expecting"]
        for t in type_order:
            polys = typed_polygons.get(t)
            if not polys:
                continue
            dark_color, light_color = _TYPE_COLORS.get(t, ("#888888", "#888888"))
            color = dark_color if dark else light_color
            for geom in polys:
                ax.add_geometries([geom], crs=ccrs.PlateCarree(),
                                  facecolor=color, edgecolor='#000000', linewidth=0.3,
                                  alpha=1, zorder=5)
            legend_handles.append(plt.Line2D([0], [0], color=color, linewidth=3, alpha=0.7, label=t.title()))
    else:
        # Severity-based single color (flood, rainfall, etc.)
        sev = alert.get("severity", "Moderate")
        base_color = _severity_color(sev)
        for t, polys in typed_polygons.items():
            for geom in polys:
                ax.add_geometries([geom], crs=ccrs.PlateCarree(),
                                  facecolor=base_color, edgecolor='#000000', linewidth=0.3,
                                  alpha=0.4, zorder=5)
        legend_handles.append(plt.Line2D([0], [0], color=base_color, linewidth=3, alpha=0.7, label=alert.get("event", "Advisory")))
    
    if legend_handles:
        leg = ax.legend(handles=legend_handles, loc='lower right', framealpha=0.85,
                        fontsize=8, facecolor='#1a1a1a' if dark else 'white',
                        edgecolor='gray', labelcolor='white' if dark else 'black')
        leg.set_zorder(20)
    print(f"{LOG_PREFIX} _render_pagasa_provinces done {time.time()-t_start:.1f}s", file=sys.stderr)

def _render_pagasa_info_panel(ax, alert, dark, label_color, utc_offset=8, time_format="civilian"):
    """Render info panel for PAGASA alerts on the right side."""
    ax.text(0.05, 0.95, alert.get("event", "Weather Alert"),
            transform=ax.transAxes, fontsize=16, fontweight='bold',
            color='#ffffff' if dark else '#1a1a1a',
            verticalalignment='top', fontfamily='sans-serif')
    
    # Severity
    severity = alert.get("severity", "Unknown")
    sev_color = _severity_color(severity)
    ax.text(0.05, 0.85, f"{severity.upper()}",
            transform=ax.transAxes, fontsize=14, fontweight='600',
            color=sev_color,
            verticalalignment='top', fontfamily='sans-serif')
    
    # Sent by
    sender = alert.get("published_by") or alert.get("sender") or "PAGASA"
    ax.text(0.05, 0.78, f"Sent by: {sender}",
            transform=ax.transAxes, fontsize=11,
            color=label_color,
            verticalalignment='top', fontfamily='sans-serif')
    
    # Date/time
    effective = alert.get("effective", "")
    local_time = _format_local_time(effective, utc_offset, time_format)
    if local_time:
        ax.text(0.05, 0.70, f"Sent: {local_time}",
                transform=ax.transAxes, fontsize=11,
                color=label_color,
                verticalalignment='top', fontfamily='sans-serif')
    
    # Vertical line separator
    ax.plot([0.95, 0.95], [0.1, 0.9], transform=ax.transAxes, color=label_color, linewidth=2, alpha=0.5)
    
    # Info text (description)
    description = alert.get("description", alert.get("headline", ""))
    if description:
        # Wrap text
        words = description.split()
        lines = []
        current_line = ""
        for word in words:
            if len(current_line) + len(word) + 1 <= 50:
                current_line += " " + word if current_line else word
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        if current_line:
            lines.append(current_line)
        
        y_pos = 0.55 if alert.get("effective") else 0.63
        for i, line in enumerate(lines[:8]):  # Max 8 lines
            ax.text(0.05, y_pos - i * 0.05, line,
                    transform=ax.transAxes, fontsize=10,
                    color=label_color,
                    verticalalignment='top', fontfamily='sans-serif')
    
    # Disclaimer at bottom
    disclaimer = "Disclaimer: This is an automated alert from PAGASA. For official advisories, visit www.pagasa.dost.gov.ph"
    ax.text(0.05, 0.05, disclaimer,
            transform=ax.transAxes, fontsize=8, fontstyle='italic',
            color=label_color, alpha=0.7,
            verticalalignment='bottom', fontfamily='sans-serif')

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate a weather alerts map.")
    parser.add_argument("--input", required=True, help="JSON file with alerts array")
    parser.add_argument("--output", required=True, help="Output PNG file path")
    parser.add_argument("--light", action="store_true", help="Use light theme with white background")
    parser.add_argument("--settings", help="Path to settings.json")
    parser.add_argument("--logo", help="Path to logo image for footer")
    parser.add_argument("--force-philippines", action="store_true", help="Force Philippines extent (4N-27N, 114E-125E)")
    args = parser.parse_args()
    if not HAS_DEPS:
        print("ERROR: Missing dependencies", file=sys.stderr)
        sys.exit(1)
    with open(args.input) as f:
        data = json.load(f)
    alerts = data if isinstance(data, list) else data.get("alerts", [])
    if not alerts:
        print("ERROR: No alerts data", file=sys.stderr)
        sys.exit(1)
    out_path = make_alert_map(
        alerts, settings=args.settings,
        logo_path=args.logo, light=args.light, filename=args.output,
        force_philippines=args.force_philippines,
    )
    if out_path:
        print(f"DONE:{args.output}")
    else:
        print("ERROR: Failed to generate alert map", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
