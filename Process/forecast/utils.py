import sys
import json
import io
import os
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

_log = print  # simple print-based logging to stderr
def _log_feature(name, feature):
    try:
        kws = feature.kwargs if hasattr(feature, 'kwargs') else {}
        fc = kws.get('facecolor', 'default')
        ec = kws.get('edgecolor', 'default')
        scale = getattr(feature, 'scale', '?')
        if isinstance(fc, (list, tuple)) and len(fc) >= 3:
            fc_hex = '#{:02X}{:02X}{:02X}'.format(int(fc[0]*255), int(fc[1]*255), int(fc[2]*255))
        else:
            fc_hex = str(fc)
        _log(f"[fcst] FEATURE {name}: scale={scale}, facecolor={fc_hex}, edgecolor={ec}", file=sys.stderr)
    except Exception:
        pass

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
    _log_feature("_LAND", _LAND)
    _log_feature("_OCEAN", _OCEAN)
    _log_feature("_COASTLINE", _COASTLINE)
    _log_feature("_BORDERS", _BORDERS)

    def _add_psgc_ph_land(ax, facecolor, edgecolor, linewidth, zorder):
        try:
            src_dir = str(Path(__file__).resolve().parent.parent.parent / "src")
            if src_dir not in sys.path:
                sys.path.insert(0, src_dir)
            from psgc_shapefiles import add_psgc_land_to_axis
            data_dir = Path(__file__).resolve().parent.parent.parent / "data" / "psgc_shapefiles"
            add_psgc_land_to_axis(ax, data_dir=data_dir,
                                  facecolor=facecolor, edgecolor=edgecolor,
                                  linewidth=linewidth, zorder=zorder)
        except Exception:
            pass

    def _add_psgc_boundaries(ax, edgecolor, linewidth, linestyle, zorder):
        try:
            src_dir = str(Path(__file__).resolve().parent.parent.parent / "src")
            if src_dir not in sys.path:
                sys.path.insert(0, src_dir)
            from psgc_shapefiles import add_psgc_boundaries_to_axis
            data_dir = Path(__file__).resolve().parent.parent.parent / "data" / "psgc_shapefiles"
            add_psgc_boundaries_to_axis(ax, data_dir=data_dir,
                                        edgecolor=edgecolor, linewidth=linewidth,
                                        linestyle=linestyle, zorder=zorder)
        except Exception:
            pass
except ImportError:
    HAS_DEPS = False

script_dir = Path(__file__).resolve().parent.parent

def _parse_kmz(kmz_path):
    """Extract geometry coordinates from a KMZ (zipped KML) file.
    Handles any KML namespace (opengis, earth.google, etc.).
    Returns list of (lons, lats) tuples.
    """
    import zipfile, re as _re
    result = []
    try:
        with zipfile.ZipFile(kmz_path, 'r') as z:
            kml_name = [n for n in z.namelist() if n.endswith('.kml')][0]
            raw = z.read(kml_name).decode('utf-8', errors='replace')
        # Strip ALL XML namespace declarations to make parsing namespace-agnostic.
        # count=1 only removes the FIRST xmlns, which fails if xmlns:gx appears
        # before the default xmlns.  Removing all (count=0) guarantees that
        # every element becomes namespace-free.
        raw = _re.sub(r'\sxmlns(:\w+)?=["\'][^"\']*["\']', '', raw)
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw)
        tag = root.tag.split('}')[0] + '}' if '}' in root.tag else ''
        for placemark in root.findall(f'.//{tag}Placemark'):
            for geom_tag in (f'{tag}Polygon/{tag}outerBoundaryIs/{tag}LinearRing/{tag}coordinates',
                             f'{tag}LineString/{tag}coordinates',
                             f'{tag}Point/{tag}coordinates'):
                coords_elem = placemark.find(f'.//{geom_tag}')
                if coords_elem is not None and coords_elem.text and coords_elem.text.strip():
                    pts = []
                    for token in coords_elem.text.strip().split():
                        parts = token.split(',')
                        if len(parts) >= 2:
                            pts.append((float(parts[0]), float(parts[1])))
                    if pts:
                        result.append(([p[0] for p in pts], [p[1] for p in pts]))
                    break
    except Exception:
        pass
    return result

def _cat_color(intensity):
    if intensity is None:
        return '#44aaff'
    if intensity >= 137:
        return '#ff0066'
    if intensity >= 113:
        return '#ff3333'
    if intensity >= 96:
        return '#ff6666'
    if intensity >= 83:
        return '#ff8844'
    if intensity >= 64:
        return '#ffaa44'
    if intensity >= 50:
        return '#ffcc44'
    if intensity >= 34:
        return '#ffee66'
    return '#44aaff'


def _cat_label(intensity, source=None, basin=None):
    if intensity is None:
        return "Tropical Depression"
    if source == "nhc":
        if intensity >= 137: return "Category 5"
        if intensity >= 113: return "Category 4"
        if intensity >= 96: return "Major Hurricane"
        if intensity >= 83: return "Category 2"
        if intensity >= 64: return "Category 1"
        if intensity >= 50: return "Severe Tropical Storm"
        if intensity >= 34: return "Tropical Storm"
        return "Tropical Depression"
    if source == "jtwc" and basin in ("CP", "EP"):
        if intensity >= 137: return "Category 5"
        if intensity >= 113: return "Category 4"
        if intensity >= 96: return "Major Hurricane"
        if intensity >= 83: return "Category 2"
        if intensity >= 64: return "Category 1"
        if intensity >= 50: return "Severe Tropical Storm"
        if intensity >= 34: return "Tropical Storm"
        return "Tropical Depression"
    if intensity >= 137:
        return "Super Typhoon"
    if intensity >= 113:
        return "Typhoon"
    if intensity >= 96:
        return "Typhoon"
    if intensity >= 83:
        return "Typhoon"
    if intensity >= 64:
        return "Typhoon"
    if intensity >= 50:
        return "Severe Tropical Storm"
    if intensity >= 34:
        return "Tropical Storm"
    return "Tropical Depression"


def _cat_sym(intensity):
    if intensity is None:
        return "\u25cf"
    if intensity >= 137:
        return "\u2b24"
    if intensity >= 113:
        return "\u2b24"
    if intensity >= 96:
        return "\u2b24"
    if intensity >= 64:
        return "\u2b24"
    if intensity >= 50:
        return "\u25c6"
    if intensity >= 34:
        return "\u25b2"
    return "\u25cf"


def _category_to_sym(cat, intensity, basin=None):
    _MAP = {
        "LPA": "lpa", "LOW": "lpa", "TD": "td", "TS": "ts", "STS": "sts", "TY": "ty", "STY": "sty",
        "STD": "td", "SSN": "ssn", "SSS": "sss",
    }
    if basin in ("CP", "EP") and cat in ("TY", "Typhoon"):
        return "Hu"
    if cat in ("Hurricane", "Major Hurricane") and intensity is not None:
        if intensity >= 137: return "cat5"
        if intensity >= 113: return "cat4"
        if intensity >= 96: return "cat3"
        if intensity >= 83: return "cat2"
        if intensity >= 64: return "cat1"
    return _MAP.get(cat)

def _category_to_pag_sym(cat, intensity, basin=None):
    _MAP = {
        "LPA": "paglpa", "LOW": "paglpa", "TD": "pagtd", "TS": "pagts", "STS": "pagsts",
        "TY": "pagty", "STY": "pagsty",
        "STD": "pagtd", "SSN": "pagtd", "SSS": "pagts",
        "Low Pressure Area": "paglpa",
        "Tropical Depression": "pagtd",
        "Tropical Storm": "pagts",
        "Severe Tropical Storm": "pagsts",
        "Typhoon": "pagty",
        "Super Typhoon": "pagsty",
    }
    if basin in ("CP", "EP") and cat in ("TY", "Typhoon"):
        return "pagHu"
    if cat in ("Hurricane", "Major Hurricane") and intensity is not None:
        if intensity >= 137: return "pagcat5"
        if intensity >= 113: return "pagcat4"
        if intensity >= 96: return "pagcat3"
        if intensity >= 83: return "pagcat2"
        if intensity >= 64: return "pagcat1"
    return _MAP.get(cat)

def _category_to_jtwc_sym(cat, intensity, basin=None):
    _MAP = {
        "LPA": "JTWC_td", "LOW": "JTWC_td",
        "TD": "JTWC_td", "TS": "JTWC_ts", "STS": "JTWC_ts",
        "TY": "JTWC_ty", "STY": "JTWC_ty",
        "STD": "JTWC_td", "SSN": "JTWC_td", "SSS": "JTWC_ts",
        "Tropical Depression": "JTWC_td",
        "Tropical Storm": "JTWC_ts",
        "Typhoon": "JTWC_ty",
        "Super Typhoon": "JTWC_ty",
    }
    if basin in ("CP", "EP") and cat in ("TY", "Typhoon"):
        return "Hu"
    return _MAP.get(cat)


def _parse_dt(dt_str):
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _is_old_point(p):
    dt_str = p.get("datetime", "")
    if not dt_str:
        return False
    dt_obj = _parse_dt(dt_str)
    if dt_obj is None:
        return False
    return dt_obj < datetime.utcnow()


def _parse_jtwc_dtg(dtg_str, ref_date=None):
    if not dtg_str or not dtg_str.endswith('Z'):
        return None
    try:
        dd = int(dtg_str[0:2])
        hh = int(dtg_str[2:4])
        mm = int(dtg_str[4:6])
    except (ValueError, IndexError):
        return None
    if ref_date is None:
        ref_date = datetime.now(timezone.utc)
    year, month = ref_date.year, ref_date.month
    try:
        dt = datetime(year, month, dd, hh, mm, tzinfo=timezone.utc)
    except ValueError:
        if month == 1:
            dt = datetime(year - 1, 12, dd, hh, mm, tzinfo=timezone.utc)
        else:
            dt = datetime(year, month - 1, dd, hh, mm, tzinfo=timezone.utc)
    return dt
def _add_footer(pil_img, forecast_dt_str, now_utc_str, basin, logo_path=None, utc_offset=0, dark=False):
    mw, mh = pil_img.size
    footer_h = max(36, int(mh * 0.04))
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
    if logo_path and logo_path.exists():
        try:
            logo = Image.open(str(logo_path)).convert("RGBA")
            logo_h = footer_h - 4
            lw = int(logo.width * logo_h / logo.height) if logo.height > 0 else logo_h
            logo = logo.resize((lw, logo_h), Image.LANCZOS)
            footer_img.paste(logo, (4, 2), logo)
            logo_w = lw + 8
        except Exception:
            pass
    utc_label = f"UTC{utc_offset:+d}" if utc_offset != 0 else "UTC"
    left_text = f"{utc_label} Forecast {forecast_dt_str} \u2014 Created {now_utc_str}  |  {basin}"
    bbox = draw.textbbox((0, 0), left_text, font=font)
    th = bbox[3] - bbox[1]
    draw.text((logo_w + 4, (footer_h - th) // 2), left_text, fill=tc, font=font)
    brand = "MonWatch-UI"
    bbox2 = draw.textbbox((0, 0), brand, font=font)
    bw = bbox2[2] - bbox2[0]
    draw.text((mw - bw - 8, (footer_h - th) // 2), brand, fill=tc, font=font)
    combined = Image.new("RGBA", (mw, mh + footer_h), (0, 0, 0, 0))
    combined.paste(pil_img, (0, 0), pil_img)
    combined.paste(footer_img, (0, mh), footer_img)
    return combined.convert("RGB")


def _draw_wind_radii(ax, track_points, dpi_scale=1.0, layout=None):
    if not track_points:
        return
    NM_TO_DEG_LAT = 1852.0 / 111320.0
    WR_COLORS = {
        "34": ("#00C800", 0.25),
        "50": ("#FFA500", 0.30),
        "64": ("#FF3232", 0.35),
    }
    for p in track_points:
        pt_lon = p.get("lon")
        pt_lat = p.get("lat")
        pt_radii = p.get("wind_radii", {})
        if pt_lon is None or pt_lat is None or not pt_radii:
            continue
        cos_lat = math.cos(math.radians(pt_lat))
        for kt_key, (fill_color, alpha) in WR_COLORS.items():
            radii = pt_radii.get(kt_key, {})
            if not radii or all(v is None for v in radii.values()):
                continue
            for quad, start_deg, end_deg in [("ne", -90, 0), ("se", 0, 90), ("sw", 90, 180), ("nw", 180, 270)]:
                r_nm = radii.get(quad)
                if r_nm is None:
                    continue
                r_deg_lon = r_nm * NM_TO_DEG_LAT / max(cos_lat, 0.01)
                r_deg_lat = r_nm * NM_TO_DEG_LAT
                n_steps = 6
                poly_lons = [pt_lon]
                poly_lats = [pt_lat]
                for i in range(n_steps + 1):
                    a = math.radians(start_deg + (end_deg - start_deg) * i / n_steps)
                    px = pt_lon + r_deg_lon * math.sin(a)
                    py = pt_lat + r_deg_lat * math.cos(a)
                    poly_lons.append(px)
                    poly_lats.append(py)
                ax.fill(poly_lons, poly_lats, color=fill_color, alpha=alpha,
                        edgecolor=fill_color, linewidth=0.5,
                        transform=ccrs.PlateCarree(), zorder=3)


def _compute_bezier_offsets(track_points, reverse_side=False, initial_offset=60):
    n = len(track_points)
    if n < 2:
        return None
    lons = [p.get("lon") for p in track_points]
    lats = [p.get("lat") for p in track_points]
    offsets = []
    for i in range(n):
        if lons[i] is None or lats[i] is None:
            offsets.append(((0, 0), 'left'))
            continue
        if i == 0:
            dx = lons[1] - lons[0] if lons[1] is not None else 1.0
            dy = lats[1] - lats[0] if lats[1] is not None else 0.0
        elif i == n - 1:
            dx = lons[-1] - lons[-2] if lons[-2] is not None else 1.0
            dy = lats[-1] - lats[-2] if lats[-2] is not None else 0.0
        else:
            dx = (lons[i+1] - lons[i-1]) / 2.0
            dy = (lats[i+1] - lats[i-1]) / 2.0
        length = math.hypot(dx, dy)
        if length > 1e-10:
            dx /= length
            dy /= length
        else:
            dx, dy = 1.0, 0.0
        if reverse_side:
            nx, ny = dy, -dx
        else:
            nx, ny = -dy, dx
        ox = nx * initial_offset
        oy = ny * initial_offset
        ha = 'right' if ox < 0 else 'left'
        offsets.append(((ox, oy), ha))
    return offsets


def _compute_curvatures(lons, lats):
    n = len(lons)
    if n < 2:
        return [0] * n
    dirs = []
    for i in range(n):
        if lons[i] is None or lats[i] is None:
            dirs.append((0, 0))
            continue
        if i == 0:
            dx = lons[1] - lons[0] if lons[1] is not None else 0
            dy = lats[1] - lats[0] if lats[1] is not None else 0
        elif i == n - 1:
            dx = lons[-1] - lons[-2] if lons[-2] is not None else 0
            dy = lats[-1] - lats[-2] if lats[-2] is not None else 0
        else:
            dx = (lons[i+1] - lons[i-1]) / 2.0
            dy = (lats[i+1] - lats[i-1]) / 2.0
        length = math.hypot(dx, dy)
        if length > 1e-10:
            dx /= length
            dy /= length
        else:
            dx, dy = 1.0, 0.0
        dirs.append((dx, dy))
    curvatures = []
    for i in range(n):
        if lons[i] is None or lats[i] is None:
            curvatures.append(0)
            continue
        if i == 0 and n >= 3:
            cross = dirs[0][0] * dirs[1][1] - dirs[0][1] * dirs[1][0]
        elif i == n - 1 and n >= 3:
            cross = dirs[n-2][0] * dirs[n-1][1] - dirs[n-2][1] * dirs[n-1][0]
        elif 0 < i < n - 1:
            cross = dirs[i-1][0] * dirs[i+1][1] - dirs[i-1][1] * dirs[i+1][0]
        else:
            cross = 0
        curvatures.append(cross)
    return curvatures


def _cascade_bezier_offsets(offsets, labels, fontsize, track_points,
                            px_p_deg_lon, px_p_deg_lat, lon_min, lat_min,
                            pts_p_px=None, cone_radii=None, step=16, max_iter=20, max_dist=200):
    if not offsets or not labels or len(track_points) < 2:
        return offsets  
    n = min(len(offsets), len(labels), len(track_points))
    if pts_p_px is None or pts_p_px < 1e-10:
        pts_p_px = 1.0
    pps = pts_p_px
    text_w = [max(len(lbl) * fontsize * 0.72 + 14, 28) for lbl in labels]
    text_h = [fontsize * 1.5 + 8 for lbl in labels]

    def _bbox(lx, ly, w, h, ha):
        if ha == 'right':
            return (lx - w, ly - h / 2, lx, ly + h / 2)
        elif ha == 'left':
            return (lx, ly - h / 2, lx + w, ly + h / 2)
        return (lx - w / 2, ly - h / 2, lx + w / 2, ly + h / 2)

    def _overlap(b1, b2):
        if b1 is None or b2 is None:
            return False
        margin = 4
        return (b1[0] - margin < b2[2] and b1[2] + margin > b2[0] and
                b1[1] - margin < b2[3] and b1[3] + margin > b2[1])

    def _mkbox(i, ox, oy, ha):
        p = track_points[i]
        lon, lat = p.get("lon"), p.get("lat")
        if lon is None or lat is None:
            return None
        ax = (lon - lon_min) * px_p_deg_lon / pps
        ay = (lat - lat_min) * px_p_deg_lat / pps
        return _bbox(ax + ox, ay + oy, text_w[i], text_h[i], ha)

    def _score(i, ox, oy, ha, placed):
        bb = _mkbox(i, ox, oy, ha)
        if bb is None:
            return -999
        s = 0
        for pb in placed:
            if pb is not None and _overlap(bb, pb):
                s -= 1
        if cone_radii:
            p = track_points[i]
            lon, lat = p.get("lon"), p.get("lat")
            if lon is not None:
                key = f"{float(lat):.4f},{float(lon):.4f}"
                rm = cone_radii.get(key, 0)
                if rm > 0:
                    rp = rm / 111320.0 * px_p_deg_lon / pps
                    if math.hypot(ox, oy) < rp:
                        s -= 5
        return s

    result = list(offsets)
    placed = [None] * n

    for i in range(n):
        base_ox, base_oy = result[i][0]
        base_d = math.hypot(base_ox, base_oy)
        if base_d < 1:
            placed[i] = _mkbox(i, 0, 0, 'left')
            continue
        nx, ny = base_ox / base_d, base_oy / base_d
        cur_ha = result[i][1]
        opp_ha = 'right' if -base_ox < 0 else 'left'

        cur_s = _score(i, nx * base_d, ny * base_d, cur_ha, placed)
        opp_s = _score(i, -nx * base_d, -ny * base_d, opp_ha, placed)

        if opp_s > cur_s:
            ox, oy, ha = -nx * base_d, -ny * base_d, opp_ha
            pnx, pny = -nx, -ny
        else:
            ox, oy, ha = nx * base_d, ny * base_d, cur_ha
            pnx, pny = nx, ny

        dist = math.hypot(ox, oy)
        # Resolve cone conflict: push to just past cone boundary
        if cone_radii:
            p = track_points[i]
            lon, lat = p.get("lon"), p.get("lat")
            if lon is not None:
                key = f"{float(lat):.4f},{float(lon):.4f}"
                rm = cone_radii.get(key, 0)
                if rm > 0:
                    rp = rm / 111320.0 * px_p_deg_lon / pps
                    if dist < rp:
                        dist = min(rp + step, max_dist)
            ox, oy = pnx * dist, pny * dist
        # Resolve label overlap: try opposite side first, then staircase perpendicular
        stair_dir = 1
        for _ in range(max_iter):
            if dist > max_dist:
                break
            bb = _mkbox(i, ox, oy, ha)
            if bb is None:
                break
            overlap_found = any(pb is not None and _overlap(bb, pb) for pb in placed)
            if not overlap_found:
                break
            # Try opposite side
            opp_ox, opp_oy = -pnx * dist, -pny * dist
            opp_ha = 'left' if ha == 'right' else 'right'
            opp_bb = _mkbox(i, opp_ox, opp_oy, opp_ha)
            if opp_bb is not None and not any(pb is not None and _overlap(opp_bb, pb) for pb in placed):
                ox, oy, ha = opp_ox, opp_oy, opp_ha
                pnx, pny = -pnx, -pny
                continue
            # Staircase perpendicular to track (step up/down)
            step_dist = step * stair_dir
            ox += pnx * step_dist
            oy += pny * step_dist
            stair_dir *= -1

        result[i] = ((round(ox), round(oy)), ha)
        placed[i] = _mkbox(i, ox, oy, ha)

    return result
def _build_cone_polygon(track_points, prob_circles):
    if len(track_points) < 2 or not prob_circles:
        return None, None
    circ_lookup = {}
    for circ in prob_circles:
        center = circ.get("center")
        clon = circ.get("center_lon")
        clat = circ.get("center_lat")
        if clon is None and center is not None:
            clon = center[1] if len(center) > 1 else None
        if clat is None and center is not None:
            clat = center[0] if len(center) > 0 else None
        if clon is None or clat is None:
            continue
        r = circ.get("radius_m") or circ.get("radius")
        if not r:
            continue
        circ_lookup[f"{clat:.4f},{clon:.4f}"] = float(r)
    pts = []
    for tp in track_points:
        lon = tp.get("lon")
        lat = tp.get("lat")
        if lon is None or lat is None:
            continue
        radius = 0.0
        for key, r in circ_lookup.items():
            k_lat, k_lon = key.split(",")
            k_lat, k_lon = float(k_lat), float(k_lon)
            if abs(lat - k_lat) < 0.01 and abs(lon - k_lon) < 0.01:
                radius = r
                break
        if radius == 0.0:
            best_dist = float('inf')
            for key, r in circ_lookup.items():
                k_lat, k_lon = key.split(",")
                k_lat, k_lon = float(k_lat), float(k_lon)
                d = math.sqrt((lon - k_lon)**2 + (lat - k_lat)**2)
                d_m = d * 111320.0
                if d < best_dist and d_m <= r * 1.5:
                    best_dist = d
                    radius = r
        pts.append((lon, lat, radius))
    if len(pts) < 2:
        return None, None
    def _cr_pts(arr, n):
        if len(arr) < 2:
            return arr
        if len(arr) == 2:
            out = []
            for j in range(n):
                t = j / n
                out.append(tuple(a + (b - a) * t for a, b in zip(arr[0], arr[1])))
            out.append(arr[-1])
            return out
        out = []
        for i in range(len(arr) - 1):
            p0 = arr[max(0, i - 1)]
            p1 = arr[i]
            p2 = arr[i + 1]
            p3 = arr[min(len(arr) - 1, i + 2)]
            for j in range(n):
                t = j / n
                t2 = t * t
                t3 = t2 * t
                out.append(tuple(
                    0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2 + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
                    for k in range(len(arr[0]))
                ))
        out.append(arr[-1])
        return out
    interp = _cr_pts(pts, 20)
    n = len(interp)
    if len(pts) >= 2:
        last_dx = pts[-1][0] - pts[-2][0]
        last_dy = pts[-1][1] - pts[-2][1]
        last_h = math.hypot(last_dx, last_dy)
    else:
        last_dx, last_dy, last_h = 0, 0, 0
    left_lons, left_lats = [], []
    right_lons, right_lats = [], []
    for i, (lon, lat, r) in enumerate(interp):
        if i == 0:
            dx = interp[1][0] - interp[0][0]
            dy = interp[1][1] - interp[0][1]
        elif i == n - 1 and last_h > 1e-10:
            dx, dy = last_dx, last_dy
        else:
            dx = interp[i+1][0] - interp[i-1][0]
            dy = interp[i+1][1] - interp[i-1][1]
        h = math.hypot(dx, dy)
        if h < 1e-10:
            if i > 0:
                dx = interp[i][0] - interp[i-1][0]; dy = interp[i][1] - interp[i-1][1]
            elif i < n - 1:
                dx = interp[i+1][0] - interp[i][0]; dy = interp[i+1][1] - interp[i][1]
            else:
                dx, dy = 1.0, 0.0
            h = math.hypot(dx, dy)
            if h < 1e-10:
                left_lons.append(lon); left_lats.append(lat)
                right_lons.append(lon); right_lats.append(lat)
                continue
        nx = -dy / h
        ny = dx / h
        cos_lat = math.cos(math.radians(lat))
        r_lon = r / (111320.0 * cos_lat) if cos_lat > 0.01 else r / 111320.0
        r_lat = r / 111320.0
        left_lons.append(lon + nx * r_lon)
        left_lats.append(lat + ny * r_lat)
        right_lons.append(lon - nx * r_lon)
        right_lats.append(lat - ny * r_lat)
    poly_lons = list(left_lons)
    poly_lats = list(left_lats)
    last_lon, last_lat, last_r = pts[-1]
    if last_r > 0.001 and last_h > 1e-10:
        heading = math.atan2(last_dy, last_dx)
        cos_lat = math.cos(math.radians(last_lat))
        r_lon_cap = last_r / (111320.0 * cos_lat) if cos_lat > 0.01 else last_r / 111320.0
        r_lat_cap = last_r / 111320.0
        start_a = heading + math.pi / 2
        end_a = heading - math.pi / 2
        for k in range(1, 25):
            t = k / 24
            a = start_a + (end_a - start_a) * t
            poly_lons.append(last_lon + math.cos(a) * r_lon_cap)
            poly_lats.append(last_lat + math.sin(a) * r_lat_cap)
        poly_lons.extend(right_lons[-2::-1])
        poly_lats.extend(right_lats[-2::-1])
    else:
        poly_lons.extend(right_lons[::-1])
        poly_lats.extend(right_lats[::-1])
    return poly_lons, poly_lats


def _resolve_ha(ha):
    if ha == "topleft":
        return "left", "top"
    return ha, None
