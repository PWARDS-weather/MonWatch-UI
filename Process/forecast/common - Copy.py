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
        # Strip XML namespace to make parsing namespace-agnostic
        raw = _re.sub(r'\sxmlns(:\w+)?=["\'][^"\']*["\']', '', raw, count=1)
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
        if intensity >= 130: return "Super Typhoon"
        if intensity >= 64: return "Hurricane"
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
    if basin in ("CP", "EP") and cat in ("TY", "STY", "Typhoon", "Super Typhoon"):
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
    if basin in ("CP", "EP") and cat in ("TY", "STY", "Typhoon", "Super Typhoon"):
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
    if basin in ("CP", "EP") and cat in ("TY", "STY", "Typhoon", "Super Typhoon"):
        return "Hu"
    return _MAP.get(cat)
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


def _compute_bezier_offsets(track_points):
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
        nx, ny = -dy, dx
        ox = nx * 60
        oy = ny * 60
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
        return (b1[0] + 4 < b2[2] and b1[2] - 4 > b2[0] and
                b1[1] + 4 < b2[3] and b1[3] - 4 > b2[1])

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
                        dist = rp + step
            ox, oy = pnx * dist, pny * dist
        # Resolve label overlap: push by step until clear
        for _ in range(max_iter):
            bb = _mkbox(i, ox, oy, ha)
            if bb is None:
                break
            if not any(pb is not None and _overlap(bb, pb) for pb in placed):
                break
            dist += step
            ox, oy = pnx * dist, pny * dist

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
def make_forecast(track_points, probability_circles=None, storm_name="Tropical Cyclone",
                  storm_id="", margin=8.0, dpi=150, figsize=(12, 10),
                  facecolor="white", filename=None, light=True,
                  settings=None, logo_path=None, algorithm="polar",
                  issued_dtg=None, kmz_cone="", kmz_track="", kmz_wind_initial=""):
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
    forecast_layout = fcst_prefs.get("forecast_layout", "default")

    if forecast_layout in ("pwards", "pagasa"):
        # Build cone polygon first to get its true extent, then add 5° margin on each side
        cone_lon_min, cone_lon_max = min(lons), max(lons)
        cone_lat_min, cone_lat_max = min(lats), max(lats)
        if probability_circles:
            _poly_lons, _poly_lats = _build_cone_polygon(track_points, probability_circles)
            if _poly_lons:
                cone_lon_min = min(cone_lon_min, min(_poly_lons))
                cone_lon_max = max(cone_lon_max, max(_poly_lons))
                cone_lat_min = min(cone_lat_min, min(_poly_lats))
                cone_lat_max = max(cone_lat_max, max(_poly_lats))
        elif kmz_cone and os.path.exists(kmz_cone):
            _cone_geoms = _parse_kmz(kmz_cone)
            for _clons, _clats in _cone_geoms:
                if _clons:
                    cone_lon_min = min(cone_lon_min, min(_clons))
                    cone_lon_max = max(cone_lon_max, max(_clons))
                    cone_lat_min = min(cone_lat_min, min(_clats))
                    cone_lat_max = max(cone_lat_max, max(_clats))
        _c_lon_center = (cone_lon_min + cone_lon_max) / 2
        _c_lat_center = (cone_lat_min + cone_lat_max) / 2
        _c_lon_half = (cone_lon_max - cone_lon_min) / 2 + 5.0
        _c_lat_half = (cone_lat_max - cone_lat_min) / 2 + 5.0
        # Add left/right margin for PWARDS logos flanking the title box
        if forecast_layout == "pwards" and logo_path:
            _iw = figsize[0] * dpi
            _dpi_scale = dpi / 150.0
            _tfs = round(36 * _dpi_scale)
            _est_th = (_tfs * dpi / 72) * 2 + 20
            _est_box_h = _est_th + 40
            _est_mw_h = int(_est_box_h * 1.4)
            _est_mw_w = int(_est_mw_h * 4800 / 3200)
            _est_sw_h = int(_est_box_h * 1.3)
            _est_sw_w = _est_sw_h
            _est_box_w = int(_iw * 0.85)
            _est_box_x = (_iw - _est_box_w) // 2
            _left_logo_px = _est_box_x - _est_mw_w - 20
            _right_logo_px = _est_box_x + _est_box_w + 15 + _est_sw_w
            _cone_lon_span = cone_lon_max - cone_lon_min
            _deg_per_px = _cone_lon_span / _iw if _cone_lon_span > 0 else 0
            _logo_lon_margin = max(0, -_left_logo_px * _deg_per_px, (_right_logo_px - _iw) * _deg_per_px)
            _c_lon_half += _logo_lon_margin
        _c_fig_aspect = figsize[0] / figsize[1]
        _c_ext_aspect = _c_lon_half / _c_lat_half if _c_lat_half > 0 else _c_fig_aspect
        if _c_ext_aspect < _c_fig_aspect:
            _c_lon_half = _c_lat_half * _c_fig_aspect
        else:
            _c_lat_half = _c_lon_half / _c_fig_aspect
        lon_min = max(_c_lon_center - _c_lon_half, -180)
        lon_max = min(_c_lon_center + _c_lon_half, 180)
        lat_min = max(_c_lat_center - _c_lat_half, -90)
        lat_max = min(_c_lat_center + _c_lat_half, 90)
    else:
        lon_min = max(min(lons) - margin, -180)
        lon_max = min(max(lons) + margin, 180)
        lat_min = max(min(lats) - margin, -90)
        lat_max = min(max(lats) + margin, 90)
    central_lon = (lon_min + lon_max) / 2

    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor=facecolor)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())
    _log(f"[fcst] storm={storm_name} id={storm_id} layout={forecast_layout} light={light} "
         f"dpi={dpi} figsize={figsize} extent=[{lon_min:.2f}, {lon_max:.2f}, {lat_min:.2f}, {lat_max:.2f}]",
         file=sys.stderr)
    _log(f"[fcst] _LAND kwargs facecolor={_LAND.kwargs.get('facecolor', '?') if hasattr(_LAND,'kwargs') else '?'} "
         f"_OCEAN kwargs facecolor={_OCEAN.kwargs.get('facecolor', '?') if hasattr(_OCEAN,'kwargs') else '?'} "
         f"_COASTLINE scale={getattr(_COASTLINE,'scale','?')}", file=sys.stderr)

    # NHC layout: use tropycal's native rendering for official NHC forecast maps
    if forecast_layout == "nhc" and storm_id:
        try:
            from tropycal import realtime as _tc
            _rt = _tc.Realtime()
            _storm = _rt.get_storm(storm_id)
            if _storm is not None:
                plt.close(fig)
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
                    _basin = "East Pacific" if storm_id.startswith("ep") else "Atlantic" if storm_id.startswith("al") else "Central Pacific"
                    _utc_offset = fcst_prefs.get("utc_offset", 0)
                    _pil_img = _add_footer(_pil_img, _forecast_dt, _now_utc, _basin, logo_path, _utc_offset)
                if filename:
                    _pil_img.save(filename)
                    return filename
                return np.array(_pil_img)
        except Exception:
            pass

    # JMA / PWARDS layout: fetch model tracks from tropycal JMA domain and overlay on existing ax
    if forecast_layout in ("jma", "pwards") and storm_id:
        try:
            from tropycal import realtime as _jma_rt
            _jma_data = _jma_rt(jtwc=True, jtwc_source="jtwc")
            _jma_storm = _jma_data.get_storm(storm_id)
            if _jma_storm is not None:
                _jma_storm.plot_models(forecast='latest', ax=ax, cartopy_proj=ccrs.PlateCarree())
                print(f"JMA model tracks plotted for {storm_id}", file=sys.stderr)
        except Exception as _jma_err:
            pass

    _log(f"[fcst] Layout branch: {forecast_layout} (light={light})", file=sys.stderr)
    if forecast_layout == "pwards":
        _log(f"[fcst]   pwards: sea=#000000 land=#002f2f land_outline=#00a1a1 grid=#343434(2px dashed)", file=sys.stderr)
        facecolor = '#000000'
        fig.patch.set_facecolor('#000000')
        ax.set_facecolor('#000000')
        ax.add_feature(_LAND, edgecolor='#03fcfc', linewidth=5, alpha=1, facecolor='#002f2f')
        _add_psgc_ph_land(ax, facecolor='#002f2f', edgecolor='#03fcfc', linewidth=5, zorder=2)
        ax.add_feature(_OCEAN, facecolor='#000000', alpha=1.0)
        #ax.add_feature(_COASTLINE, edgecolor='#002f2f', linewidth=1)
        ax.add_feature(_BORDERS, edgecolor='#00a1a1', linewidth=1, linestyle=':')
        _add_psgc_boundaries(ax, edgecolor='#00a1a1', linewidth=0.5, linestyle=':', zorder=3)
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
    elif forecast_layout == "jma":
        _log(f"[fcst]   jma light={light}: ocean=#BED2FE/%230d1520 land=#445C45 edge", file=sys.stderr)
        if light:
            ax.set_facecolor('#BED2FE')
            ax.add_feature(_LAND, facecolor='#7f997f', edgecolor='#334c33', linewidth=0.9)
            ax.add_feature(_OCEAN, facecolor='#BED2FE', alpha=0.6)
            # ax.add_feature(_COASTLINE, edgecolor='#445C45', linewidth=0.6)
            ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.3, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.15, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
            gl.top_labels = True
            gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', "weight": "bold"}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', "rotation": 90, "weight": "bold"}
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
            # ax.add_feature(_COASTLINE, edgecolor='#445C45', linewidth=0.6)
            ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.2, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.1, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
            gl.top_labels = True
            gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', "weight": "bold"}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', "rotation": 90, "weight": "bold"}
            label_color = '#F1F5FE'
            label_bg = '#1a1a2e'
            label_border = 'none'
            title_color = '#F1F5FE'
    elif forecast_layout == "jtwc":
        _log(f"[fcst]   jtwc light={light}: ocean=#B8C8D8/%232a3a4a land=#A1783F edge", file=sys.stderr)
        if light:
            ax.set_facecolor('#B8C8D8')
            ax.add_feature(_LAND, edgecolor='#A1783F', linewidth=0.4)
            ax.add_feature(_OCEAN, facecolor='#B8C8D8', alpha=0.6)
            #ax.add_feature(_COASTLINE, edgecolor='#A1783F', linewidth=0.6)
            ax.add_feature(_BORDERS, edgecolor='#A1783F', linewidth=0.3, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#A1783F', linewidth=0.15, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#E14348', alpha=0.3, linestyle='-')
            gl.top_labels = True
            gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', "weight": "bold"}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', "rotation": 90, "weight": "bold"}
            gl.xformatter = LONGITUDE_FORMATTER
            gl.yformatter = LATITUDE_FORMATTER
            label_color = '#000000'
            label_bg = 'white'
            label_border = 'none'
            title_color = '#000000'
        else:
            ax.set_facecolor('#1a1a2e')
            ax.add_feature(_LAND, edgecolor='#A1783F', linewidth=0.4)
            ax.add_feature(_OCEAN, facecolor='#2a3a4a', alpha=0.9)
            #ax.add_feature(_COASTLINE, edgecolor='#A1783F', linewidth=0.6)
            ax.add_feature(_BORDERS, edgecolor='#A1783F', linewidth=0.2, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#A1783F', linewidth=0.1, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#E14348', alpha=0.3, linestyle='-')
            gl.top_labels = True
            gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#E14348', "weight": "bold"}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#E14348', "rotation": 90, "weight": "bold"}
            label_color = '#E14348'
            label_bg = '#1a1a2e'
            label_border = 'none'
            title_color = '#E14348'
    elif forecast_layout == "pagasa":
        _log(f"[fcst]   pagasa light={light}: ocean=#BEE8FE/%232a4a5a land=#EFEFDB edge=#161D15", file=sys.stderr)
        if light:
            ax.set_facecolor('#BEE8FE')
            ax.add_feature(_OCEAN, facecolor='#BEE8FE', alpha=1.0, zorder=0)
            ax.add_feature(_LAND, facecolor='#FFEAC0', edgecolor='#161D15', linewidth=0.4, zorder=1)
            _add_psgc_ph_land(ax, facecolor='#FFEAC0', edgecolor='#161D15', linewidth=0.4, zorder=2)
            ax.add_feature(_BORDERS, edgecolor='#161D15', linewidth=0.3, linestyle=':', zorder=1)
            _add_psgc_boundaries(ax, edgecolor='#161D15', linewidth=0.15, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=1.0, color='#A9C6D6', alpha=0.5, linestyle='-')
            gl.top_labels = True
            gl.right_labels = True
            gl.xlabel_style = {'size': round(10 * dpi_scale, 1), 'color': '#000000'}
            gl.ylabel_style = {'size': round(10 * dpi_scale, 1), 'color': '#000000', "rotation": 90}
            gl.xformatter = LONGITUDE_FORMATTER
            gl.yformatter = LATITUDE_FORMATTER
            gl.xpadding = 2
            gl.ypadding = 2
            label_color = '#000000'
            label_bg = 'white'
            label_border = 'none'
            title_color = '#000000'
        else:
            ax.set_facecolor('#1a1a2e')
            ax.add_feature(_OCEAN, facecolor='#2a4a5a', alpha=1.0, zorder=0)
            ax.add_feature(_LAND, facecolor='#EFEFDB', edgecolor='#161D15', linewidth=0.4, zorder=1)
            _add_psgc_ph_land(ax, facecolor='#EFEFDB', edgecolor='#161D15', linewidth=0.4, zorder=2)
            ax.add_feature(_BORDERS, edgecolor='#161D15', linewidth=0.2, linestyle=':', zorder=1)
            _add_psgc_boundaries(ax, edgecolor='#161D15', linewidth=0.1, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=1.0, color='#A9C6D6', alpha=0.3, linestyle='-')
            gl.top_labels = True
            gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#A9C6D6', "weight": "bold"}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#A9C6D6', "rotation": 90, "weight": "bold"}
            gl.xpadding = 2
            gl.ypadding = 2
            label_color = '#A9C6D6'
            label_bg = '#1a1a2e'
            label_border = 'none'
            title_color = '#A9C6D6'
    elif light:
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

    # ── PAR (Philippine Area of Responsibility) overlay (red boundary line) ──
    if forecast_layout == "pagasa":
        par_pts = [
            (115.0, 5.0), (115.0, 15.0), (120.0, 21.0),
            (120.0, 25.0), (135.0, 25.0), (135.0, 5.0),
        ]
        par_lons = [p[0] for p in par_pts]
        par_lats = [p[1] for p in par_pts]
        ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
                color="#02131D", linewidth=1.5, linestyle=(0, (5, 5)),
                transform=ccrs.PlateCarree(), zorder=1)
    elif forecast_layout != "jtwc":
        par_pts = [
            (115.0, 5.0), (115.0, 15.0), (120.0, 21.0),
            (120.0, 25.0), (135.0, 25.0), (135.0, 5.0),
        ]
        par_lons = [p[0] for p in par_pts]
        par_lats = [p[1] for p in par_pts]
        ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
                color="#FF3333", linewidth=1.5, linestyle='--',
                transform=ccrs.PlateCarree(), zorder=1)

    # ── Probability circles – tangent polylines + dashed circles (overlay style) ──
    geod = Geodesic()
    prob_circles = probability_circles or []

    # Cone: build forecast track cone (pagasa / pwards)
    cone_method = fcst_prefs.get("cone_method", "smooth")
    if forecast_layout in ("pagasa", "pwards") and prob_circles:
        if cone_method == "union":
            _log(f"[fcst] {forecast_layout} cone: union method from {len(prob_circles)} prob circles", file=sys.stderr)
            from shapely.geometry import Point as ShapelyPoint
            from shapely.ops import unary_union
            pag_centers = []
            pag_radii = []
            for circ in prob_circles:
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
                    fill_color = '#FFFFFF' if forecast_layout == "pagasa" else '#00FFF2'
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
            _log(f"[fcst] {forecast_layout} cone: smooth method from {len(track_points)} track points, {len(prob_circles)} prob circles", file=sys.stderr)
            poly_lons, poly_lats = _build_cone_polygon(track_points, prob_circles)
            if poly_lons:
                _log(f"[fcst] {forecast_layout} cone polygon: {len(poly_lons)} vertices", file=sys.stderr)
                fill_c = '#FFFFFF' if forecast_layout == "pagasa" else '#60a5fa'
                fill_a = 0.25 if forecast_layout == "pagasa" else 0.15
                out_c = 'black' if forecast_layout == "pagasa" else '#60a5fa'
                out_ls = '-' if forecast_layout == "pagasa" else '--'
                out_lw = 2.0 if forecast_layout == "pagasa" else 1.0
                z = 50 if forecast_layout == "pwards" else 1
                ax.fill(poly_lons, poly_lats, color=fill_c, alpha=fill_a,
                        transform=ccrs.PlateCarree(), zorder=z)
                ax.plot(poly_lons + [poly_lons[0]], poly_lats + [poly_lats[0]], color=out_c, linewidth=out_lw, alpha=0.6, linestyle=out_ls,
                        transform=ccrs.PlateCarree(), zorder=z + 1)
            else:
                _log(f"[fcst] {forecast_layout} cone: _build_cone_polygon returned None", file=sys.stderr)

    # Wind initial radii from KMZ (*initialradii.kmz)
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

    # Cone from KMZ (*CONE.kmz)
    if kmz_cone and os.path.exists(kmz_cone) and not prob_circles:
        cone_geoms = _parse_kmz(kmz_cone)
        for c_lons, c_lats in cone_geoms:
            if len(c_lons) < 3:
                continue
            fill_c = '#60a5fa'
            fill_a = 0.15
            out_c = '#60a5fa'
            out_ls = '--'
            out_lw = 1.0
            z = 50 if forecast_layout == "pwards" else 1
            ax.fill(c_lons, c_lats, color=fill_c, alpha=fill_a,
                    transform=ccrs.PlateCarree(), zorder=z)
            ax.plot(c_lons + [c_lons[0]], c_lats + [c_lats[0]], color=out_c, linewidth=out_lw, alpha=0.6, linestyle=out_ls,
                    transform=ccrs.PlateCarree(), zorder=z + 1)

    _log(f"[fcst] Cone style: layout={forecast_layout} circles={len(prob_circles)} kmz_cone={bool(kmz_cone)}", file=sys.stderr)
    for circ in prob_circles:
        # Parse center coordinates
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
        if forecast_layout == "jma":
            cone_color = '#F1F5FE'
            circle_color = '#F1F5FE'
            fill_color = '#F1F5FE'
            cone_alpha = 0.7
            circle_alpha = 0.6
            fill_alpha = 0.04
            cone_linestyle = '-'
            circle_linestyle = '--'
            cone_lw = 1.5
        elif forecast_layout == "pagasa":
            cone_color = '#5B7081'
            circle_color = '#5B7081'
            fill_color = '#D3D9E0'
            cone_alpha = 0.5
            circle_alpha = 1.0
            fill_alpha = 0.5
            cone_linestyle = '-'
            circle_linestyle = '-'
            cone_lw = 1.5
            _log(f"[fcst]   PAGASA circle[{prob_circles.index(circ)}]: lon={clon:.2f} lat={clat:.2f} "
                 f"radius_m={radius_m:.0f} tangents={len(tangents)} "
                 f"cone={cone_color} circle={circle_color} fill={fill_color} "
                 f"cone_a={cone_alpha} circle_a={circle_alpha} fill_a={fill_alpha}", file=sys.stderr)
        elif forecast_layout == "jtwc":
            cone_color = '#E14348'
            circle_color = '#E14348'
            fill_color = '#94C2C8'
            cone_alpha = 0.7
            circle_alpha = 0.6
            fill_alpha = 0.3
            cone_linestyle = '-'
            circle_linestyle = '--'
            cone_lw = 1.0
        else:
            cone_color = '#60a5fa'
            circle_color = '#60a5fa'
            fill_color = '#60a5fa'
            cone_alpha = 0.55
            circle_alpha = 0.6
            fill_alpha = 0.04
            cone_linestyle = '-'
            circle_linestyle = '--'
            cone_lw = 1.5
        # Tangent lines (cone wedge) - DISABLED
        # for seg in tangents:
        #    if len(seg) < 2:
        #        continue
        #    seg_lons = [float(pt[1]) for pt in seg]
        #    seg_lats = [float(pt[0]) for pt in seg]
        #    ax.plot(seg_lons, seg_lats, color=cone_color, linewidth=cone_lw, alpha=cone_alpha,
        #            linestyle=cone_linestyle,
        #            transform=ccrs.PlateCarree(), zorder=2)
        if forecast_layout in ("pagasa", "pwards") and cone_method == "smooth":
            pass  # smooth cone polygon renders everything
            # Draw outer arc only (fill already drawn from full envelope above)
            # if len(tangents) >= 2:
            #     t0_lat, t0_lon = float(tangents[0][-1][0]), float(tangents[0][-1][1])
            #     t1_lat, t1_lon = float(tangents[1][-1][0]), float(tangents[1][-1][1])
            #     circle_pts = geod.circle(lon=clon, lat=clat, radius=radius_m, n_samples=72)
            #     c_lons = circle_pts[:, 0]
            #     c_lats = circle_pts[:, 1]
            #     n = len(c_lons)
            #     def _sqdist(lon, lat, tlon, tlat):
            #         return (lon - tlon)**2 + (lat - tlat)**2
            #     i0 = min(range(n), key=lambda i: _sqdist(c_lons[i], c_lats[i], t0_lon, t0_lat))
            #     i1 = min(range(n), key=lambda i: _sqdist(c_lons[i], c_lats[i], t1_lon, t1_lat))
            #     fwd = [(i0 + j) % n for j in range(n)]
            #     fwd_end = fwd.index(i1)
            #     fwd_arc = fwd[:fwd_end + 1]
            #     rev = [(i0 - j) % n for j in range(n)]
            #     rev_end = rev.index(i1)
            #     rev_arc = rev[:rev_end + 1]
            #     all_sum_lat = sum(float(pt[0]) for seg in tangents for pt in seg)
            #     all_sum_lon = sum(float(pt[1]) for seg in tangents for pt in seg)
            #     n_all = sum(len(seg) for seg in tangents)
            #     cent_lat, cent_lon = all_sum_lat / n_all, all_sum_lon / n_all
            #     cent_azi = geod.inverse([clon, clat], [cent_lon, cent_lat])[0, 1]
            #     def _arc_mid_azi(indices):
            #         mid = indices[len(indices)//2]
            #         return geod.inverse([clon, clat], [c_lons[mid], c_lats[mid]])[0, 1]
            #     def _azi_diff(a, b):
            #         d = abs(a - b) % 360
            #         return min(d, 360 - d)
            #     fwd_azi_diff = _azi_diff(_arc_mid_azi(fwd_arc), cent_azi)
            #     rev_azi_diff = _azi_diff(_arc_mid_azi(rev_arc), cent_azi)
            #     cap = rev_arc if fwd_azi_diff < rev_azi_diff else fwd_arc
            #     cap_lons = [c_lons[i] for i in cap]
            #     cap_lats = [c_lats[i] for i in cap]
            #     ax.plot(cap_lons + cap_lons[:1], cap_lats + cap_lats[:1],
            #             color=cone_color, linewidth=cone_lw, linestyle=circle_linestyle,
            #             alpha=circle_alpha, transform=ccrs.PlateCarree(), zorder=2)
            
            # else:
            #     circle_pts = geod.circle(lon=clon, lat=clat, radius=radius_m, n_samples=72)
            #     c_lons = [p[0] for p in circle_pts]
            #     c_lats = [p[1] for p in circle_pts]
            #     #ax.plot(c_lons + c_lons[:1], c_lats + c_lats[:1],
            #     #        color=cone_color, linewidth=cone_lw, linestyle='-',
            #     #        alpha=circle_alpha, transform=ccrs.PlateCarree(), zorder=2)
        elif forecast_layout == "jtwc":
            # Cone with smooth sides and semi-circle cap
            if len(tangents) >= 2:
                t0_lat, t0_lon = float(tangents[0][-1][0]), float(tangents[0][-1][1])
                t1_lat, t1_lon = float(tangents[1][-1][0]), float(tangents[1][-1][1])
                # Centroid of all tangent points = approach direction
                all_sum_lat = sum(float(pt[0]) for seg in tangents for pt in seg)
                all_sum_lon = sum(float(pt[1]) for seg in tangents for pt in seg)
                n_all = sum(len(seg) for seg in tangents)
                cent_lat, cent_lon = all_sum_lat / n_all, all_sum_lon / n_all
                # Full circle at endpoint
                circle_pts = geod.circle(lon=clon, lat=clat, radius=radius_m, n_samples=36)
                c_lons = circle_pts[:, 0]
                c_lats = circle_pts[:, 1]
                n = len(c_lons)
                # Nearest circle index to each tangent endpoint
                def _sqdist(lon, lat, tlon, tlat):
                    return (lon - tlon)**2 + (lat - tlat)**2
                i0 = min(range(n), key=lambda i: _sqdist(c_lons[i], c_lats[i], t0_lon, t0_lat))
                i1 = min(range(n), key=lambda i: _sqdist(c_lons[i], c_lats[i], t1_lon, t1_lat))
                # Azimuth from circle center to centroid (approach direction)
                cent_azi = geod.inverse([clon, clat], [cent_lon, cent_lat])[0, 1]
                def _arc_mid_azi(indices):
                    mid = indices[len(indices)//2]
                    return geod.inverse([clon, clat], [c_lons[mid], c_lats[mid]])[0, 1]
                def _azi_diff(a, b):
                    d = abs(a - b) % 360
                    return min(d, 360 - d)
                fwd = [(i0 + j) % n for j in range(n)]
                fwd_end = fwd.index(i1)
                fwd_arc = fwd[:fwd_end + 1]
                rev = [(i0 - j) % n for j in range(n)]
                rev_end = rev.index(i1)
                rev_arc = rev[:rev_end + 1]
                cap = fwd_arc if _azi_diff(_arc_mid_azi(fwd_arc), cent_azi) > _azi_diff(_arc_mid_azi(rev_arc), cent_azi) else rev_arc
                # Fill polygon: tangent[0] + cap arc + reverse(tangent[1])
                poly_lons = [float(pt[1]) for pt in tangents[0]]
                poly_lats = [float(pt[0]) for pt in tangents[0]]
                for i in cap:
                    poly_lons.append(c_lons[i])
                    poly_lats.append(c_lats[i])
                for pt in reversed(tangents[1]):
                    poly_lons.append(float(pt[1]))
                    poly_lats.append(float(pt[0]))
                ax.fill(poly_lons, poly_lats, color=fill_color, alpha=fill_alpha,
                        transform=ccrs.PlateCarree(), zorder=1)
        else:
            # Circle at endpoint
            circle_pts = geod.circle(lon=clon, lat=clat, radius=radius_m, n_samples=36)
            c_lons = [p[0] for p in circle_pts]
            c_lats = [p[1] for p in circle_pts]
            ax.plot(c_lons + c_lons[:1], c_lats + c_lats[:1],
                    color=circle_color, linewidth=1, linestyle=circle_linestyle, alpha=circle_alpha,
                    transform=ccrs.PlateCarree(), zorder=2)
            ax.fill(c_lons, c_lats, color=fill_color, alpha=fill_alpha,
                    transform=ccrs.PlateCarree(), zorder=2)

    # PAGASA: find first track point with a matching probability circle (past vs future)
    first_future_idx = len(track_points)
    if forecast_layout == "pagasa" and prob_circles:
        _circ_centers = []
        for _c in prob_circles:
            _clon = _c.get("center_lon") or (_c.get("center")[1] if _c.get("center") and len(_c["center"]) > 1 else None)
            _clat = _c.get("center_lat") or (_c.get("center")[0] if _c.get("center") and len(_c["center"]) > 0 else None)
            if _clon is not None and _clat is not None:
                _circ_centers.append((_clat, _clon))
        for _i, _p in enumerate(track_points):
            _plon = _p.get("lon")
            _plat = _p.get("lat")
            if _plon is not None and _plat is not None:
                for _cc in _circ_centers:
                    if abs(_plat - _cc[0]) < 0.01 and abs(_plon - _cc[1]) < 0.01:
                        first_future_idx = _i - 1
                        break
            if first_future_idx < len(track_points):
                break

    # Forecast track from KMZ (*TRACK.kmz) — use longest LineString
    kmz_track_lons = []
    kmz_track_lats = []
    kmz_point_pts = []  # Point placemarks from TRACK.kmz
    if kmz_track and os.path.exists(kmz_track):
        track_geoms = _parse_kmz(kmz_track)
        # Pick the longest geometry (LineString with most points)
        best = max(track_geoms, key=lambda g: len(g[0])) if track_geoms else None
        if best:
            kmz_track_lons, kmz_track_lats = best
        # Extract Point placemarks (single-coordinate geometries)
        kmz_point_pts = [(lons[0], lats[0]) for lons, lats in track_geoms if len(lons) == 1]
        if kmz_track_lons and len(kmz_track_lons) >= 2:
            if forecast_layout == "pwards":
                ax.plot(kmz_track_lons, kmz_track_lats, linewidth=6,
                        transform=ccrs.PlateCarree(), zorder=4, alpha=0.15)
                ax.plot(kmz_track_lons, kmz_track_lats, linewidth=3,
                        transform=ccrs.PlateCarree(), zorder=4, alpha=0.3)
                ax.plot(kmz_track_lons, kmz_track_lats, color='#00fff2', linewidth=2.0,
                        transform=ccrs.PlateCarree(), zorder=5)

    if len(track_points) >= 2 and not kmz_track_lons:
        tlons = [p["lon"] for p in track_points if p.get("lon") is not None]
        tlats = [p["lat"] for p in track_points if p.get("lat") is not None]
        if tlons:
            if forecast_layout == "pwards":
                pwards_track_color = '#00fff2'
                pwards_glow_color = '#00fff2'
                ax.plot(tlons, tlats, linewidth=6,
                        transform=ccrs.PlateCarree(), zorder=4, alpha=0.15)
                ax.plot(tlons, tlats, linewidth=3,
                        transform=ccrs.PlateCarree(), zorder=4, alpha=0.3)
                ax.plot(tlons, tlats, color=pwards_track_color, linewidth=2.0,
                        transform=ccrs.PlateCarree(), zorder=5)
            elif forecast_layout == "jma":
                ax.plot(tlons, tlats, color='#F1F5FE', linewidth=2.0, linestyle='--',
                        transform=ccrs.PlateCarree(), zorder=5)
            elif forecast_layout == "pagasa":
                if first_future_idx > 0:
                    past_lons = tlons[:first_future_idx]
                    past_lats = tlats[:first_future_idx]
                    if len(past_lons) >= 2:
                        ax.plot(past_lons, past_lats, color='#4488ff', linewidth=2.0, linestyle='-',
                                transform=ccrs.PlateCarree(), zorder=5)
                    future_lons = tlons[first_future_idx:]
                    future_lats = tlats[first_future_idx:]
                    if len(future_lons) >= 2:
                        ax.plot(future_lons, future_lats, color='#5B7081', linewidth=2.0, linestyle='-',
                                transform=ccrs.PlateCarree(), zorder=5)
                else:
                    ax.plot(tlons, tlats, color='#5B7081', linewidth=2.0, linestyle='-',
                            transform=ccrs.PlateCarree(), zorder=5)
            elif forecast_layout == "jtwc":
                ax.plot(tlons, tlats, color='#E14348', linewidth=2.0, linestyle='--',
                        transform=ccrs.PlateCarree(), zorder=5)
            elif forecast_layout == "nhc":
                track_color = '#cc0000'; track_lw = 2.5
                ax.plot(tlons, tlats, color=track_color, linewidth=track_lw,
                        transform=ccrs.PlateCarree(), zorder=5)
            else:
                track_color = '#ffaa44' if not light else '#00BFFF' if storm_id.startswith('ep') else '#FF6B35' if storm_id.startswith('al') else '#FFD700'
                track_lw = 2.5 if not light else 2
                ax.plot(tlons, tlats, color=track_color, linewidth=track_lw,
                        transform=ccrs.PlateCarree(), zorder=5)

    # NHC (Tropycal) — fetch real-time NHC data if layout is nhc
    if fcst_prefs.get("forecast_layout") == "nhc" and storm_id:
        try:
            from tropycal import realtime as _tc
            _rt = _tc.Realtime()
            _storm = _rt.get_storm(storm_id)
            _td = _storm.to_dataframe()
            if _td is not None and len(_td) > 0:
                # Convert tropycal track points to our format
                _pts = []
                for _, row in _td.iterrows():
                    if pd.isna(row.get('lat')) or pd.isna(row.get('lon')):
                        continue
                    pt = {
                        "lon": float(row['lon']),
                        "lat": float(row['lat']),
                        "datetime": row['time'].strftime("%Y-%m-%d %H:%M") if hasattr(row['time'], 'strftime') else str(row['time']),
                        "intensity": float(row['vmax']) if not pd.isna(row.get('vmax', None)) else None,
                        "intensity_category": str(row.get('type', '')) if not pd.isna(row.get('type', None)) else "",
                    }
                    if not pd.isna(row.get('mslp', None)):
                        pt["mslp"] = float(row['mslp'])
                    _pts.append(pt)
                if _pts:
                    track_points = _pts
                    storm_name = _storm.to_dict().get("name", storm_name)
            # Try to get forecast track too
            try:
                _fcst = _storm.get_forecast()
                if _fcst and 'track_points' in _fcst:
                    for _fp in _fcst['track_points']:
                        if _fp.get('lat') is not None and _fp.get('lon') is not None:
                            _fp_dict = {
                                "lon": float(_fp['lon']),
                                "lat": float(_fp['lat']),
                                "datetime": str(_fp.get('time', '')),
                                "intensity": float(_fp.get('vmax', 0)) if _fp.get('vmax') is not None else None,
                                "intensity_category": str(_fp.get('type', '')),
                            }
                            # Only add if it's not a duplicate of last obs point
                            if not any(abs(t["lon"] - _fp_dict["lon"]) < 0.01 and abs(t["lat"] - _fp_dict["lat"]) < 0.01 for t in track_points):
                                 track_points.append(_fp_dict)
            except Exception:
                pass
        except Exception as _tc_err:
            # Tropycal unavailable or storm not found — use existing data with NHC styling
            pass
    show_dt = fcst_prefs.get("show_date_time", True)
    show_wind = fcst_prefs.get("show_wind_speed", True)
    time_fmt = fcst_prefs.get("time_format", "military")
    utc_offset = fcst_prefs.get("utc_offset", 0)
    wind_unit = fcst_prefs.get("wind_format", "kt")

    # Layout-specific styling
    if forecast_layout == "pwards":
        label_fs = round(10 * dpi_scale, 1)
        lb_bbox = None
        label_color = '#ffffff'
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#ffffff')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['bottom'].set_visible(False)
        ax.spines['left'].set_visible(False)
    elif forecast_layout == "jma":
        label_fs = round(6.5 * dpi_scale, 1)
        if light:
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='#000000', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
        else:
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='#1a1a2e', ec='#445C45', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#445C45')
    elif forecast_layout == "pagasa":
        label_fs = round(10 * dpi_scale, 1)
        lb_bbox = None
        label_color = '#000000'
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
    elif forecast_layout == "jtwc":
        label_fs = round(6.5 * dpi_scale, 1)
        if light:
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='#000000', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
        else:
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='#1a1a2e', ec='#E14348', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#E14348')
    elif forecast_layout == "nhc":
        label_fs = round(6.5 * dpi_scale, 1)
        lb_bbox = dict(boxstyle='round,pad=0.15', fc=label_bg, ec='#222222', alpha=0.9)
        lb_arrow = dict(arrowstyle='-', lw=1.0, color='#333333')
    else:
        label_fs = round(6.5 * dpi_scale, 1)
        lb_bbox = dict(boxstyle='round,pad=0.15', fc=label_bg, ec=label_border, alpha=0.85)
        lb_arrow = dict(arrowstyle='-', lw=0.8, color='#666666')

    # Build labels for each point
    all_labels = []
    for p in track_points:
        pt_lon = p.get("lon")
        pt_lat = p.get("lat")
        intensity = p.get("intensity")
        dt_str = p.get("datetime", "")
        advanced_hours = p.get("advanced_hours")
        if pt_lon is None or pt_lat is None:
            all_labels.append("")
            continue
        label_parts = []

        # Derive datetime from issued_dtg + advanced_hours (JTWC style)
        dt_obj = None
        if issued_dtg and advanced_hours is not None:
            base_dt = _parse_jtwc_dtg(issued_dtg)
            if base_dt is not None:
                dt_obj = base_dt + timedelta(hours=advanced_hours)

        # Fallback to parsing dt_str
        if dt_obj is None and dt_str:
            dt_obj = _parse_dt(dt_str)

        if forecast_layout in ("pagasa", "pwards"):
            if show_dt and dt_obj is not None:
                if utc_offset != 0:
                    dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=utc_offset)
                h, m = dt_obj.hour, dt_obj.minute
                if m >= 30: h += 1
                if h >= 24: h -= 24
                if h == 0: hour_str = "12AM"
                elif h < 12: hour_str = f"{h}AM"
                elif h == 12: hour_str = "12PM"
                else: hour_str = f"{h - 12}PM"
                label_parts.append(f"{hour_str} {dt_obj.day} {dt_obj.strftime('%b.')} {dt_obj.year} ({dt_obj.strftime('%a')})")
            elif show_dt and dt_str:
                label_parts.append(dt_str)
        else:
            if show_dt:
                if dt_obj is not None:
                    if utc_offset != 0:
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=utc_offset)
                    # Use DDHHMMZ format when derived from issued_dtg (JTWC style)
                    if issued_dtg and advanced_hours is not None:
                        label_parts.append(dt_obj.strftime("%d%H%M") + "Z")
                    elif time_fmt == "civilian":
                        label_parts.append(dt_obj.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if utc_offset == 0 else ""))
                    else:
                        label_parts.append(dt_obj.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else ""))
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

            # Append wind radii summary when available
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

    # Cramped system disabled
    skip = [False] * len(track_points)

    # Draw points and labels, alternating above/below
    sym_dir = script_dir.parent / "public" / "images" / "symbols"
    bezier_offsets = _compute_bezier_offsets(track_points) if algorithm in ("bezier", "smart_bezier") else None
    _lons = [p.get("lon") for p in track_points]
    _lats = [p.get("lat") for p in track_points]
    _curvatures = None
    if algorithm in ("polar", "zigzag") and len(track_points) >= 2:
        _curvatures = _compute_curvatures(_lons, _lats)
    # Cone radius lookup for outside-cone label placement
    _cone_radii = {}
    for circ in prob_circles:
        _clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
        _clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
        _rm = circ.get("radius_m") or circ.get("radius")
        if _clon is not None and _clat is not None and _rm:
            _cone_radii[f"{float(_clat):.4f},{float(_clon):.4f}"] = float(_rm)
    _fig_w_px = figsize[0] * dpi
    _fig_h_px = figsize[1] * dpi
    _ax_w_px = _fig_w_px * 0.96
    _ax_h_px = _fig_h_px * 0.90
    _lon_r = lon_max - lon_min
    _lat_r = lat_max - lat_min
    _px_p_deg_lon = _ax_w_px / _lon_r if _lon_r > 0 else 100
    _px_p_deg_lat = _ax_h_px / _lat_r if _lat_r > 0 else 100
    _pts_p_px = 72.0 / dpi

    if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
        bezier_offsets = _cascade_bezier_offsets(
            bezier_offsets, all_labels, label_fs, track_points,
            _px_p_deg_lon, _px_p_deg_lat, lon_min, lat_min,
            pts_p_px=_pts_p_px, cone_radii=_cone_radii
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

    # Expand map boundary if labels would extend too close to the edge
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
    lon_min = max(lon_min, -180)
    lon_max = min(lon_max, 180)
    lat_min = max(lat_min, -90)
    lat_max = min(lat_max, 90)
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # Draw wind radii quadrant arcs from JTWC-style wind_radii data
    _draw_wind_radii(ax, track_points, dpi_scale, forecast_layout)

    # Build render list: use KMZ Point positions when available, matched to track_points for labels
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
        is_old = forecast_layout == "pagasa" and tp_idx < first_future_idx
        used_symbol = False
        if forecast_layout == "pwards" and i == 0:
            ax.plot(pt_lon, pt_lat, 'o', color='#03fcfc', markersize=12,
                    transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                    markeredgewidth=1.5, alpha=0.8)
        if not is_old:
            if pcat:
                if forecast_layout in ("pagasa", "pwards"):
                    sym_name = _category_to_pag_sym(pcat, intensity)
                elif forecast_layout == "jtwc":
                    sym_name = _category_to_jtwc_sym(pcat, intensity)
                else:
                    sym_name = _category_to_sym(pcat, intensity)
                if sym_name:
                    sym_path = sym_dir / f"{sym_name}.png"
                    if sym_path.exists():
                        try:
                            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
                            sym_pil = Image.open(str(sym_path)).convert("RGBA")
                            sym_w, sym_h = sym_pil.size
                            target_sz = round(32 * dpi_scale) if sym_name.startswith("pagcat") else round(14 * dpi_scale)
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
                    if is_old:
                        ax.plot(pt_lon, pt_lat, 'o', color='#4488ff', markersize=6,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                        markeredgewidth=0.5)
            elif forecast_layout == "pwards" and i == 0:
                ax.plot(pt_lon, pt_lat, 'o', color='#03fcfc', markersize=10,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                        markeredgewidth=1.0)
            else:
                mk = _cat_color(intensity)
                sz = 9 if intensity is not None and intensity >= 64 else 7
                ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                        transform=ccrs.PlateCarree(), zorder=6, markeredgecolor='white',
                        markeredgewidth=0.5)
        if is_old or not all_labels[tp_idx] or skip[tp_idx]:
            continue
        # Cone-aware offset: place labels outside the cone boundary
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
        elif algorithm in ("bezier", "smart_bezier") and bezier_offsets and tp_idx < len(bezier_offsets):
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
        if forecast_layout == "pwards":
            pe = [path_effects.Stroke(linewidth=3.0, foreground='black'),
                  path_effects.Normal()]
        elif forecast_layout == "pagasa":
            pe = [path_effects.Stroke(linewidth=3.0, foreground='white'),
                  path_effects.Normal()]
        else:
            pe = None
        ax.annotate("", (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    arrowprops={**lb_arrow, 'relpos': (0 if ha == 'left' else 1, 1)},
                    transform=ccrs.PlateCarree(), zorder=1)
        ax.annotate(all_labels[tp_idx], (pt_lon, pt_lat),
                    textcoords="offset points", xytext=xytext,
                    fontsize=label_fs, color=label_color, ha=ha,
                    bbox=lb_bbox,
                    path_effects=pe,
                    transform=ccrs.PlateCarree(), zorder=90)

    # Add legend
    if forecast_layout in ("pagasa", "pwards"):
        legend_path = sym_dir / "pagleg.jpg"
    elif forecast_layout == "jtwc":
        legend_path = None
    else:
        legend_path = sym_dir / "final_track.png"
    if legend_path and legend_path.exists():
        try:
            leg_pil = Image.open(str(legend_path)).convert("RGBA")
            from matplotlib.offsetbox import OffsetImage, AnnotationBbox
            leg_w, leg_h = leg_pil.size
            
            # Target height: 150 pixels. (If you have a 300px original, this will downsample it perfectly)
            leg_target_h = round(150 * dpi_scale)
            leg_scale = leg_target_h / leg_h
            leg_new_w, leg_new_h = max(1, int(leg_w * leg_scale)), max(1, int(leg_h * leg_scale))
            
            # Downsample to 150px exactly once using high-quality LANCZOS
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
            
            # resample=False prevents Matplotlib from applying any extra blur/smoothing
            oi = OffsetImage(leg_arr, zoom=1, resample=False)
            ab = AnnotationBbox(oi, (lx, ly), frameon=False,
                                 xycoords=ax.transAxes,
                                 box_alignment=(ba_x, ba_y), zorder=10)
            ax.add_artist(ab)
        except Exception:
            pass

    if forecast_layout == "jtwc":
        # Custom JTWC Legend
        leg_pos = fcst_prefs.get("legend_position", "bottom_right")
        pos_map = {
            "bottom_left":  (0.01, 0.01, 0, 0),
            "top_left":     (0.01, 0.99, 0, 1),
            "top_right":    (0.99, 0.99, 1, 1),
            "bottom_right": (0.99, 0.01, 1, 0),
        }
        lx, ly, ba_x, ba_y = pos_map.get(leg_pos, pos_map["bottom_left"])
        
        legend_text = (
            "Tropical Depression (TD): ≤ 33 kt\n"
            "Tropical Storm (TS): 34 – 63 kt\n"
            "Typhoon (TY): 64 – 129 kt\n"
            "Super Typhoon (STY): ≥ 130 kt"
        )
        
        bbox_face = 'white' if light else '#222222'
        bbox_edge = 'gray' if light else '#444444'
        text_color = 'black' if light else 'white'
        
        ax.text(lx, ly, legend_text, 
                transform=ax.transAxes, 
                fontsize=round(9 * dpi_scale, 1), 
                verticalalignment='bottom' if ba_y == 0 else 'top', 
                horizontalalignment='right' if ba_x == 1 else 'left',
                color=text_color,
                bbox=dict(facecolor=bbox_face, alpha=0.8, edgecolor=bbox_edge, boxstyle='round,pad=0.5'),
                zorder=10)


    basin = storm_id[:2].upper() if len(storm_id) >= 2 else ""
    season = storm_id[2:6] if len(storm_id) >= 6 else ""
    if forecast_layout == "pwards":
        pass
    elif forecast_layout == "jma":
        title_color_jma = '#000000' if light else '#F1F5FE'
        title_text = f"{storm_name.upper()} ({storm_id.upper()}) — Forecast Track — JMA"
        ax.set_title(title_text, fontsize=round(11 * dpi_scale, 1), color=title_color_jma, fontweight='600', fontfamily='sans-serif', pad=12)
    elif forecast_layout == "pagasa":
        pass  # title drawn on PIL image after save
    elif forecast_layout == "jtwc":
        title_color_jtwc = '#000000' if light else '#E14348'
        title_text = f"{storm_name.upper()} ({storm_id.upper()}) — Forecast Track — JTWC"
        ax.set_title(title_text, fontsize=round(11 * dpi_scale, 1), color=title_color_jtwc, fontweight='600', fontfamily='sans-serif', pad=12)
    elif forecast_layout == "nhc":
        title_parts = [f"NHC FORECAST  |  {storm_name}  ({storm_id.upper()})"]
        ax.set_title("  ".join(title_parts), fontsize=round(10 * dpi_scale, 1), color=title_color, fontweight='bold', fontfamily='monospace')
    else:
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

    # ── PAGASA / PWARDS title box (drawn on PIL image, auto-sized to text) ──
    if forecast_layout in ("pagasa", "pwards"):
        try:
            draw = ImageDraw.Draw(pil_img)
            iw, ih = pil_img.size
            # Category and text content
            first_intensity = track_points[0].get("intensity") if track_points else None
            first_cat = track_points[0].get("intensity_category", "") if track_points else ""
            cat_label = _cat_label(first_intensity) if first_intensity is not None else (first_cat or "TD")
            
            # Parse storm_name for PAGASA: "Intl {Local}" format
            sname = storm_name.upper() if storm_name else "TROPICAL CYCLONE"
            if forecast_layout == "pagasa" and "{" in sname and "}" in sname:
                # Format: "Intl {Local}" -> show "Intl {Local}"
                pass  # Keep as-is since it's already formatted
            elif forecast_layout == "pagasa":
                # If no international name in storm_name, just use the name
                pass
            
            dt_str = track_points[0].get("datetime", "") if track_points else ""
            dt_obj = _parse_dt(dt_str) if dt_str else None
            if dt_obj:
                day_str = dt_obj.strftime("%d")
                month_str = dt_obj.strftime("%B")
                year_str = str(dt_obj.year)
                h, m = dt_obj.hour, dt_obj.minute
                if m >= 30: h += 1
                if h >= 24: h -= 24
                if h == 0: hour_str = "12AM"
                elif h < 12: hour_str = f"{h}AM"
                elif h == 12: hour_str = "12PM"
                else: hour_str = f"{h - 12}PM"
            else:
                day_str, month_str, year_str, hour_str = "", "", "", ""
            row1 = f"Track and Intensity Forecast of {cat_label} {sname}"
            row2 = f"{day_str} {month_str} {year_str}, {hour_str} Forecast Bulletin #1" if day_str else ""
            # Pick font sizes (start big, shrink to fit)
            tfs, sfs = round(36 * dpi_scale), round(24 * dpi_scale)
            title_font = sub_font = None
            for _ in range(10):
                try:
                    tf = ImageFont.truetype("segoeuib.ttf", tfs)
                except Exception:
                    try:
                        tf = ImageFont.truetype("segoeui.ttf", tfs)
                    except Exception:
                        try:
                            tf = ImageFont.truetype("arialbd.ttf", tfs)
                        except Exception:
                            tf = ImageFont.load_default()
                try:
                    sf = ImageFont.truetype("segoeui.ttf", sfs)
                except Exception:
                    try:
                        sf = ImageFont.truetype("arial.ttf", sfs)
                    except Exception:
                        sf = ImageFont.load_default()
                b1 = draw.textbbox((0, 0), row1, font=tf)
                b2 = draw.textbbox((0, 0), row2, font=sf)
                tw = max(b1[2] - b1[0], b2[2] - b2[0])
                th = (b1[3] - b1[1]) + (b2[3] - b2[1]) + 1
                if tw < iw * 0.85 and tfs > 10 and sfs > 8:
                    title_font, sub_font = tf, sf
                    break
                tfs -= 2
                sfs = max(8, tfs - 4)
            if title_font is None:
                title_font = tf
                sub_font = sf
            # Size box to text
            pad_x = 40
            pad_y = 20
            box_w = tw + pad_x * 2
            box_h = th + pad_y * 2
            box_x = (iw - box_w) // 2
            box_y = 50
            # Triple border + fill
            draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=3)
            draw.rectangle([box_x + 4, box_y + 4, box_x + box_w - 4, box_y + box_h - 4], outline='white', width=2)
            draw.rectangle([box_x + 7, box_y + 7, box_x + box_w - 7, box_y + box_h - 7], outline='black', width=1)
            draw.rectangle([box_x + 8, box_y + 8, box_x + box_w - 8, box_y + box_h - 8], fill='black')
            # Centered text
            cx = box_x + box_w // 2
            cy1 = box_y + pad_y + (b1[3] - b1[1]) // 2 - 4 
            cy2 = cy1 + (b1[3] - b1[1]) // 2 + 10 + (b2[3] - b2[1]) // 2
            draw.text((cx, cy1), row1, fill='white', font=title_font, anchor='mm')
            if row2:
                draw.text((cx, cy2), row2, fill='white', font=sub_font, anchor='mm')
            # ── PWARDS: left logo (MonWatch) + right logo (splash/PWARDS) ──
            if forecast_layout == "pwards" and logo_path:
                try:
                    _mw_logo = Image.open(str(logo_path)).convert("RGBA")
                    _splash_path = logo_path.parent / "splash.png"
                    _splash_logo = Image.open(str(_splash_path)).convert("RGBA") if _splash_path.exists() else None
                    _mw_h = int(box_h * 1.4)
                    _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
                    _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
                    _mw_x = box_x - _mw_w - 20
                    _mw_y = box_y + (box_h - _mw_h) // 2
                    pil_img.paste(_mw_logo, (_mw_x, _mw_y), _mw_logo)
                    if _splash_logo:
                        _s_h = int(box_h * 1.3)
                        _s_w = int(_splash_logo.width * _s_h / _splash_logo.height)
                        _splash_logo = _splash_logo.resize((_s_w, _s_h), Image.LANCZOS)
                        _s_x = box_x + box_w + 15
                        _s_y = box_y + (box_h - _s_h) // 2
                        pil_img.paste(_splash_logo, (_s_x, _s_y), _splash_logo)
                except Exception:
                    pass
        except Exception:
            pass

    # ── Top-right logo overlay inside the map (PWARDS: MonWatch-UI + PWARDS splash) ──
    if False:  # disabled; logos now placed in title box above
        try:
            _mw_logo_img = Image.open(str(logo_path)).convert("RGBA")
            _splash_path = logo_path.parent / "splash.png"
            _splash_img = Image.open(str(_splash_path)).convert("RGBA") if _splash_path.exists() else None
            _lh = 100
            _lw = int(_mw_logo_img.width * _lh / _mw_logo_img.height) if _mw_logo_img.height > 0 else _lh
            _mw_logo_img = _mw_logo_img.resize((_lw, _lh), Image.LANCZOS)
            _pad = 20
            if _splash_img:
                _sh = 90
                _sw = int(_splash_img.width * _sh / _splash_img.height) if _splash_img.height > 0 else _sh
                _splash_img = _splash_img.resize((_sw, _sh), Image.LANCZOS)
                _total_w = _lw + _sw + 12
                _tx = pil_img.width - _total_w - _pad
                _ty = _pad + 60  # below title area, inside map
                pil_img.paste(_mw_logo_img, (_tx, _ty + (_sh - _lh) // 2), _mw_logo_img)
                pil_img.paste(_splash_img, (_tx + _lw + 12, _ty), _splash_img)
            else:
                _tx = pil_img.width - _lw - _pad
                _ty = _pad + 60
                pil_img.paste(_mw_logo_img, (_tx, _ty), _mw_logo_img)
        except Exception as _logo_err:
            pass

    # Disclaimer text on map portion (before footer)
    if forecast_layout != "pwards":
        try:
            draw = ImageDraw.Draw(pil_img)
            iw, ih = pil_img.size
            disclaimer_text = "THIS IS AN EXPERIMENTAL FORECAST MADE BY MONWATCH-UI — IT MAY BE DERIVED FROM OFFICIAL SOURCES OR BE A CUSTOM-MADE EXPERIMENTAL FORECAST.\nPLEASE EXERCISE CAUTION AND CONSULT OFFICIAL SOURCES FOR AUTHORITATIVE INFORMATION."
            fs = max(7, int(ih * 0.011))
            try:
                fnt = ImageFont.truetype("consola.ttf", fs)
            except Exception:
                fnt = ImageFont.load_default()
            bb = draw.textbbox((0, 0), disclaimer_text, font=fnt)
            th = bb[3] - bb[1]
            draw.text((iw // 2, ih - th - 8), disclaimer_text, fill=(140, 140, 140), font=fnt, anchor='mb')
        except Exception:
            pass

    # Add footer with logo (respect civilian time and UTC offset)
    if logo_path and (light or forecast_layout == "pwards"):
        forecast_dt = ""
        if track_points:
            first_dt = track_points[0].get("datetime", "")
            if first_dt:
                try:
                    fdt = _parse_dt(first_dt)
                    if fdt is not None:
                        fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
                        utc_offset = fcst_prefs.get("utc_offset", 0)
                        time_fmt = fcst_prefs.get("time_format", "military")
                        if utc_offset != 0:
                            fdt = fdt.replace(tzinfo=timezone.utc) + timedelta(hours=utc_offset)
                        if time_fmt == "civilian":
                            forecast_dt = fdt.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if utc_offset == 0 else "")
                        else:
                            forecast_dt = fdt.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else "")
                    else:
                        forecast_dt = first_dt
                except Exception:
                    forecast_dt = first_dt
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
        basin_name = "East Pacific" if storm_id.startswith("ep") else "Atlantic" if storm_id.startswith("al") else "Central Pacific" if storm_id.startswith("cp") else "Western Pacific"
        if forecast_layout != "pwards":
            pil_img = _add_footer(pil_img, forecast_dt, now_str, basin_name, logo_path, utc_offset, dark=False)

    if filename:
        pil_img.save(filename)
        return filename

    return np.array(pil_img)
def make_combined_forecast(all_tracks, settings=None, logo_path=None, light=True, filename=None,
                           dpi=150, figsize=(12, 10), margin=8.0, algorithm="polar", custom_title="",
                           resolution="10m"):
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
    forecast_layout = fcst_prefs.get("forecast_layout", "default")
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
    if forecast_layout in ("pagasa", "pwards"):
        figsize = (24, 13.5)
    if forecast_layout in ("pwards", "pagasa"):
        # Build cone polygons for all tracks to get true cone extent, then add 5° margin
        cone_lon_min, cone_lon_max = min(all_lons), max(all_lons)
        cone_lat_min, cone_lat_max = min(all_lats), max(all_lats)
        for t in all_tracks:
            _pts = t.get("track_points", [])
            _circles = t.get("probability_circles", [])
            _kmz_cone = t.get("kmz_cone", "")
            if _circles:
                _plons, _plats = _build_cone_polygon(_pts, _circles)
                if _plons:
                    cone_lon_min = min(cone_lon_min, min(_plons))
                    cone_lon_max = max(cone_lon_max, max(_plons))
                    cone_lat_min = min(cone_lat_min, min(_plats))
                    cone_lat_max = max(cone_lat_max, max(_plats))
            elif _kmz_cone and os.path.exists(_kmz_cone):
                _cgeoms = _parse_kmz(_kmz_cone)
                for _clons, _clats in _cgeoms:
                    if _clons:
                        cone_lon_min = min(cone_lon_min, min(_clons))
                        cone_lon_max = max(cone_lon_max, max(_clons))
                        cone_lat_min = min(cone_lat_min, min(_clats))
                        cone_lat_max = max(cone_lat_max, max(_clats))
        _c_lon_center = (cone_lon_min + cone_lon_max) / 2
        _c_lat_center = (cone_lat_min + cone_lat_max) / 2
        _c_lon_half = (cone_lon_max - cone_lon_min) / 2 + 5.0
        _c_lat_half = (cone_lat_max - cone_lat_min) / 2 + 5.0
        # Add left/right margin for PWARDS logos flanking the title box
        if forecast_layout == "pwards" and logo_path:
            _iw = figsize[0] * dpi
            _dpi_scale = dpi / 150.0
            _tfs = round(36 * _dpi_scale)
            _est_th = (_tfs * dpi / 72) * 2 + 20
            _est_box_h = _est_th + 40
            _est_mw_h = int(_est_box_h * 1.4)
            _est_mw_w = int(_est_mw_h * 4800 / 3200)
            _est_sw_h = int(_est_box_h * 1.3)
            _est_sw_w = _est_sw_h
            _est_box_w = int(_iw * 0.85)
            _est_box_x = (_iw - _est_box_w) // 2
            _left_logo_px = _est_box_x - _est_mw_w - 20
            _right_logo_px = _est_box_x + _est_box_w + 15 + _est_sw_w
            _cone_lon_span = cone_lon_max - cone_lon_min
            _deg_per_px = _cone_lon_span / _iw if _cone_lon_span > 0 else 0
            _logo_lon_margin = max(0, -_left_logo_px * _deg_per_px, (_right_logo_px - _iw) * _deg_per_px)
            _c_lon_half += _logo_lon_margin
        _c_fig_aspect = figsize[0] / figsize[1]
        _c_ext_aspect = _c_lon_half / _c_lat_half if _c_lat_half > 0 else _c_fig_aspect
        if _c_ext_aspect < _c_fig_aspect:
            _c_lon_half = _c_lat_half * _c_fig_aspect
        else:
            _c_lat_half = _c_lon_half / _c_fig_aspect
        lon_min = max(_c_lon_center - _c_lon_half, -180)
        lon_max = min(_c_lon_center + _c_lon_half, 180)
        lat_min = max(_c_lat_center - _c_lat_half, -90)
        lat_max = min(_c_lat_center + _c_lat_half, 90)
        central_lon = _c_lon_center
    else:
        _lon_center = (min(all_lons) + max(all_lons)) / 2
        _lat_center = (min(all_lats) + max(all_lats)) / 2
        _lon_half = (max(all_lons) - min(all_lons)) / 2 + margin
        _lat_half = (max(all_lats) - min(all_lats)) / 2 + margin
        _fig_aspect = figsize[0] / figsize[1]
        _ext_aspect = _lon_half / _lat_half if _lat_half > 0 else _fig_aspect
        if _ext_aspect < _fig_aspect:
            _lon_half = _lat_half * _fig_aspect
        else:
            _lat_half = _lon_half / _fig_aspect
        lon_min = max(_lon_center - _lon_half, -180)
        lon_max = min(_lon_center + _lon_half, 180)
        lat_min = max(_lat_center - _lat_half, -90)
        lat_max = min(_lat_center + _lat_half, 90)
        central_lon = _lon_center
    fig = plt.figure(figsize=figsize, dpi=dpi, facecolor='white' if light else '#1a1a2e')
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=central_lon))
    ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

    # ── Layout-specific map styling (exact per-layout from single-track make_forecast) ──
    _log(f"[fcst] Layout branch: {forecast_layout} (light={light})", file=sys.stderr)
    if forecast_layout == "pwards":
        _log(f"[fcst]   pwards: sea=#000000 land=#002f2f land_outline=#03fcfc grid=#343434(2px dashed)", file=sys.stderr)
        fig.patch.set_facecolor('#000000')
        ax.set_facecolor('#000000')
        ax.add_feature(_OCEAN, facecolor='#000000', alpha=1.0, zorder=0)
        ax.add_feature(_LAND, edgecolor='#03fcfc', linewidth=0.5, alpha=1, facecolor='#002f2f', zorder=1)
        _add_psgc_ph_land(ax, facecolor='#002f2f', edgecolor='#03fcfc', linewidth=0.5, zorder=2)
        ax.add_feature(_BORDERS, edgecolor='#00a1a1', linewidth=1, linestyle=':', zorder=2)
        _add_psgc_boundaries(ax, edgecolor='#00a1a1', linewidth=0.5, linestyle=':', zorder=3)
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
    elif forecast_layout == "jma":
        _log(f"[fcst]   jma light={light}: ocean=#BED2FE/%230d1520 land=#445C45 edge", file=sys.stderr)
        if light:
            ax.set_facecolor('#BED2FE')
            ax.add_feature(_LAND, facecolor='#7f997f', edgecolor='#334c33', linewidth=0.9)
            ax.add_feature(_OCEAN, facecolor='#BED2FE', alpha=0.6)
            # ax.add_feature(_COASTLINE, edgecolor='#445C45', linewidth=0.6)
            ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.3, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.15, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
            gl.top_labels = True; gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'weight': 'bold'}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'rotation': 90, 'weight': 'bold'}
            gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
            label_fs = round(6.5 * dpi_scale, 1)
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='#000000', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
            label_color = '#000000'
        else:
            ax.set_facecolor('#0d1520')
            ax.add_feature(_LAND, facecolor='#7f997f', edgecolor='#334c33', linewidth=0.9)
            ax.add_feature(_OCEAN, facecolor='#0d1520', alpha=0.9)
            # ax.add_feature(_COASTLINE, edgecolor='#445C45', linewidth=0.6)
            ax.add_feature(_BORDERS, edgecolor='#445C45', linewidth=0.2, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#445C45', linewidth=0.1, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#8593b2', alpha=1, linestyle='-')
            gl.top_labels = True; gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', 'weight': 'bold'}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#F1F5FE', 'rotation': 90, 'weight': 'bold'}
            gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
            label_fs = round(6.5 * dpi_scale, 1)
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='#1a1a2e', ec='#445C45', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#445C45')
            label_color = '#F1F5FE'
        pe = None
    elif forecast_layout == "jtwc":
        _log(f"[fcst]   jtwc light={light}: ocean=#B8C8D8/%232a3a4a land=#A1783F edge", file=sys.stderr)
        if light:
            ax.set_facecolor('#B8C8D8')
            ax.add_feature(_LAND, edgecolor='#A1783F', linewidth=0.4)
            ax.add_feature(_OCEAN, facecolor='#B8C8D8', alpha=0.6)
            ax.add_feature(_BORDERS, edgecolor='#A1783F', linewidth=0.3, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#A1783F', linewidth=0.15, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#E14348', alpha=0.3, linestyle='-')
            gl.top_labels = True; gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'weight': 'bold'}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#000000', 'rotation': 90, 'weight': 'bold'}
            gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
            label_fs = round(6.5 * dpi_scale, 1)
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='white', ec='#000000', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
            label_color = '#000000'
        else:
            ax.set_facecolor('#1a1a2e')
            ax.add_feature(_LAND, edgecolor='#A1783F', linewidth=0.4)
            ax.add_feature(_OCEAN, facecolor='#2a3a4a', alpha=0.9)
            ax.add_feature(_BORDERS, edgecolor='#A1783F', linewidth=0.2, linestyle=':')
            _add_psgc_boundaries(ax, edgecolor='#A1783F', linewidth=0.1, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=0.3, color='#E14348', alpha=0.3, linestyle='-')
            gl.top_labels = True; gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#E14348', 'weight': 'bold'}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#E14348', 'rotation': 90, 'weight': 'bold'}
            label_fs = round(6.5 * dpi_scale, 1)
            lb_bbox = dict(boxstyle='round,pad=0.15', fc='#1a1a2e', ec='#E14348', alpha=0.9)
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#E14348')
            label_color = '#E14348'
        pe = None
    elif forecast_layout == "pagasa":
        _log(f"[fcst]   pagasa light={light}: ocean=#BEE8FE/%232a4a5a land=#FFEAC0 edge=#161D15", file=sys.stderr)
        if light:
            ax.set_facecolor('#BEE8FE')
            ax.add_feature(_OCEAN, facecolor='#BEE8FE', alpha=1.0, zorder=0)
            ax.add_feature(_LAND, facecolor='#FFEAC0', edgecolor='#161D15', linewidth=0.4, zorder=1)
            _add_psgc_ph_land(ax, facecolor='#FFEAC0', edgecolor='#161D15', linewidth=0.4, zorder=2)
            ax.add_feature(_BORDERS, edgecolor='#161D15', linewidth=0.3, linestyle=':', zorder=1)
            _add_psgc_boundaries(ax, edgecolor='#161D15', linewidth=0.15, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=1.0, color='#A9C6D6', alpha=0.5, linestyle='-')
            gl.top_labels = True; gl.right_labels = True
            gl.xlabel_style = {'size': round(10 * dpi_scale, 1), 'color': '#000000'}
            gl.ylabel_style = {'size': round(10 * dpi_scale, 1), 'color': '#000000', 'rotation': 90}
            gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
            gl.xpadding = 2; gl.ypadding = 2
            label_fs = round(10 * dpi_scale, 1)
            lb_bbox = None
            label_color = '#000000'
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#000000')
            pe = [path_effects.Stroke(linewidth=3.0, foreground='white'), path_effects.Normal()]
        else:
            ax.set_facecolor('#1a1a2e')
            ax.add_feature(_OCEAN, facecolor='#2a4a5a', alpha=1.0, zorder=0)
            ax.add_feature(_LAND, facecolor='#EFEFDB', edgecolor='#161D15', linewidth=0.4, zorder=1)
            _add_psgc_ph_land(ax, facecolor='#EFEFDB', edgecolor='#161D15', linewidth=0.4, zorder=2)
            ax.add_feature(_BORDERS, edgecolor='#161D15', linewidth=0.2, linestyle=':', zorder=1)
            _add_psgc_boundaries(ax, edgecolor='#161D15', linewidth=0.1, linestyle=':', zorder=3)
            gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                              linewidth=1.0, color='#A9C6D6', alpha=0.3, linestyle='-')
            gl.top_labels = True; gl.right_labels = True
            gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#A9C6D6', 'weight': 'bold'}
            gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#A9C6D6', 'rotation': 90, 'weight': 'bold'}
            gl.xpadding = 2; gl.ypadding = 2
            label_fs = round(10 * dpi_scale, 1)
            lb_bbox = None
            label_color = '#A9C6D6'
            lb_arrow = dict(arrowstyle='-', lw=0.8, color='#A9C6D6')
            pe = [path_effects.Stroke(linewidth=3.0, foreground='white'), path_effects.Normal()]
    elif light:
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

    # ── PAR overlay ──
    if forecast_layout == "pagasa":
        par_pts = [(115.0, 5.0), (115.0, 15.0), (120.0, 21.0), (120.0, 25.0), (135.0, 25.0), (135.0, 5.0)]
        par_lons = [p[0] for p in par_pts]
        par_lats = [p[1] for p in par_pts]
        ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
                color="#02131D", linewidth=1.5, linestyle=(0, (5, 5)),
                transform=ccrs.PlateCarree(), zorder=1)
    elif forecast_layout != "jtwc":
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

    # ── Pre-compute cone paths + track points for bezier collision detection ──
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
                        _cz = 50 if forecast_layout == "pwards" else 1
                        _ca = 0.40 if forecast_layout == "pwards" else 0.10
                        _elw = 1.5 if forecast_layout == "pwards" else 1.0
                        _els = '-' if forecast_layout == "pwards" else '--'
                        ax.fill(poly_lons, poly_lats, color=color, alpha=_ca,
                                edgecolor=color, linewidth=_elw, linestyle=_els,
                                transform=ccrs.PlateCarree(), zorder=_cz)
            except Exception:
                pass
        elif prob_circles:
            poly_lons, poly_lats = _build_cone_polygon(pts, prob_circles)
            if poly_lons and poly_lats:
                _cz = 50 if forecast_layout == "pwards" else 1
                _ca = 0.40 if forecast_layout == "pwards" else 0.10
                _elw = 1.5 if forecast_layout == "pwards" else 1.0
                _els = '-' if forecast_layout == "pwards" else '--'
                ax.fill(poly_lons, poly_lats, color=color, alpha=_ca,
                        edgecolor=color, linewidth=_elw, linestyle=_els,
                        transform=ccrs.PlateCarree(), zorder=_cz)

        # NHC-specific: load cone/wind radii from KMZ files (same as make_forecast)
        if source == "nhc":
            kmz_cone = t.get("kmz_cone", "")
            kmz_wind_initial = t.get("kmz_wind_initial", "")
            try:
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
                        ca = 0.40 if forecast_layout == "pwards" else 0.10
                        elw = 1.5 if forecast_layout == "pwards" else 1.0
                        els = '-' if forecast_layout == "pwards" else '--'
                        cz = 50 if forecast_layout == "pwards" else 1
                        ax.fill(c_lons, c_lats, color=color, alpha=ca,
                                transform=ccrs.PlateCarree(), zorder=cz)
                        ax.plot(c_lons + [c_lons[0]], c_lats + [c_lats[0]], color=color, linewidth=elw, alpha=0.6, linestyle=els,
                                transform=ccrs.PlateCarree(), zorder=cz + 1)
            except Exception:
                pass

        # Track line — NHC loads from KMZ *TRACK.kmz first (same as make_forecast)
        track_tlons, track_tlats = tlons, tlats
        if source == "nhc":
            kmz_track = t.get("kmz_track", "")
            if kmz_track and os.path.exists(kmz_track):
                try:
                    track_geoms = _parse_kmz(kmz_track)
                    best = max(track_geoms, key=lambda g: len(g[0])) if track_geoms else None
                    if best:
                        track_tlons, track_tlats = best
                except Exception:
                    pass
        # PAGASA: find first track point with a matching probability circle
        first_future_idx = len(pts)
        if forecast_layout == "pagasa" and prob_circles:
            _circ_centers = []
            for _c in prob_circles:
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
            ax.plot(track_tlons, track_tlats, color=color, linewidth=2.5, transform=ccrs.PlateCarree(), zorder=5)

        _sz = 100 if forecast_layout == "pwards" else 7
        # Symbols + labels (exact code path from single-track make_forecast)
        issued_dtg = t.get("issued_dtg", "")
        all_labels = []
        for p in pts:
            pt_lon = p.get("lon")
            pt_lat = p.get("lat")
            intensity = p.get("intensity")
            dt_str = p.get("datetime", "")
            advanced_hours = p.get("advanced_hours")
            if pt_lon is None or pt_lat is None:
                all_labels.append("")
                continue
            label_parts = []
            dt_obj = None
            if issued_dtg and advanced_hours is not None:
                base_dt = _parse_jtwc_dtg(issued_dtg)
                if base_dt is not None:
                    dt_obj = base_dt + timedelta(hours=advanced_hours)
            if dt_obj is None and dt_str:
                dt_obj = _parse_dt(dt_str)
            if forecast_layout in ("pagasa", "pwards"):
                if show_dt and dt_obj is not None:
                    if utc_offset != 0:
                        dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=utc_offset)
                    h, m = dt_obj.hour, dt_obj.minute
                    if m >= 30: h += 1
                    if h >= 24: h -= 24
                    if h == 0: hour_str = "12AM"
                    elif h < 12: hour_str = f"{h}AM"
                    elif h == 12: hour_str = "12PM"
                    else: hour_str = f"{h - 12}PM"
                    label_parts.append(f"{hour_str} {dt_obj.day} {dt_obj.strftime('%b.')} {dt_obj.year} ({dt_obj.strftime('%a')})")
                elif show_dt and dt_str:
                    label_parts.append(dt_str)
            else:
                if show_dt:
                    if dt_obj is not None:
                        if utc_offset != 0:
                            dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=utc_offset)
                        if issued_dtg and advanced_hours is not None:
                            label_parts.append(dt_obj.strftime("%d%H%M") + "Z")
                        elif time_fmt == "civilian":
                            label_parts.append(dt_obj.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if utc_offset == 0 else ""))
                        else:
                            label_parts.append(dt_obj.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else ""))
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

        # Cramped system disabled
        skip = [False] * len(pts)
        if len(pts) >= 2:
            bezier_offsets = _compute_bezier_offsets(pts) if algorithm in ("bezier", "smart_bezier") else None
        else:
            bezier_offsets = None
        _cone_radii = {}
        for circ in prob_circles:
            _clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
            _clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
            _rm = circ.get("radius_m") or circ.get("radius")
            if _clon is not None and _clat is not None and _rm:
                _cone_radii[f"{float(_clat):.4f},{float(_clon):.4f}"] = float(_rm)
        _fig_w_px = figsize[0] * dpi
        _fig_h_px = figsize[1] * dpi
        _ax_w_px = _fig_w_px * 0.96
        _ax_h_px = _fig_h_px * 0.90
        _lon_r = lon_max - lon_min
        _lat_r = lat_max - lat_min
        _px_p_deg_lon = _ax_w_px / _lon_r if _lon_r > 0 else 100
        _px_p_deg_lat = _ax_h_px / _lat_r if _lat_r > 0 else 100
        _pts_p_px = 72.0 / dpi

        if algorithm in ("bezier", "smart_bezier") and bezier_offsets:
            bezier_offsets = _cascade_bezier_offsets(
                bezier_offsets, all_labels, label_fs, pts,
                _px_p_deg_lon, _px_p_deg_lat, lon_min, lat_min,
                pts_p_px=_pts_p_px, cone_radii=_cone_radii
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

        # Expand map boundary if labels would extend too close to the edge
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
        lon_min = max(lon_min, -180)
        lon_max = min(lon_max, 180)
        lat_min = max(lat_min, -90)
        lat_max = min(lat_max, 90)
        ax.set_extent([lon_min, lon_max, lat_min, lat_max], crs=ccrs.PlateCarree())

        _draw_wind_radii(ax, pts, dpi_scale, forecast_layout)

        for i, p in enumerate(pts):
            pt_lon = p.get("lon")
            pt_lat = p.get("lat")
            intensity = p.get("intensity")
            pcat = p.get("intensity_category", "")
            if pt_lon is None or pt_lat is None:
                continue
            is_old = forecast_layout == "pagasa" and i < first_future_idx
            used_symbol = False
            if not is_old:
                # Derive category from intensity if missing (same as single-process forecast)
                if not pcat and intensity is not None:
                    if intensity >= 96: pcat = "Major Hurricane"
                    elif intensity >= 64: pcat = "Hurricane"
                    elif intensity >= 50: pcat = "STS"
                    elif intensity >= 34: pcat = "TS"
                    else: pcat = "TD"
                if pcat:
                    if forecast_layout in ("pagasa", "pwards"):
                        sym_name = _category_to_pag_sym(pcat, intensity)
                    elif forecast_layout == "jtwc":
                        sym_name = _category_to_jtwc_sym(pcat, intensity)
                    elif source == "nhc":
                        sym_name = _category_to_sym(pcat, intensity)
                    else:
                        sym_name = _category_to_sym(pcat, intensity)
                    if sym_name:
                        sym_path = sym_dir / f"{sym_name}.png"
                        if sym_path.exists():
                            try:
                                from matplotlib.offsetbox import OffsetImage, AnnotationBbox
                                sym_pil = Image.open(str(sym_path)).convert("RGBA")
                                sym_w, sym_h = sym_pil.size
                                target_sz = round(32 * dpi_scale) if sym_name.startswith("pagcat") else round(14 * dpi_scale)
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
                            except Exception:
                                pass
            if not used_symbol:
                if is_old:
                    ax.plot(pt_lon, pt_lat, 'o', color='#4488ff', markersize=6,
                            transform=ccrs.PlateCarree(), zorder=_sz - 1, markeredgecolor='white',
                            markeredgewidth=0.5)
                elif source == "nhc":
                    color = 'red' if intensity and intensity >= 64 else 'darkorange' if intensity and intensity >= 34 else 'yellow'
                    sz = 40 if intensity and intensity >= 64 else 30 if intensity and intensity >= 34 else 20
                    ax.scatter(pt_lon, pt_lat, color=color, s=sz, edgecolors='black',
                               linewidth=0.5, transform=ccrs.PlateCarree(), zorder=6, alpha=0.8)
                else:
                    mk = _cat_color(intensity)
                    sz = 9 if intensity is not None and intensity >= 64 else 7
                    ax.plot(pt_lon, pt_lat, 'o', color=mk, markersize=sz,
                            transform=ccrs.PlateCarree(), zorder=_sz - 1, markeredgecolor='white',
                            markeredgewidth=0.5)
            if is_old or not all_labels[i] or skip[i]:
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
            elif algorithm in ("bezier", "smart_bezier") and bezier_offsets and i < len(bezier_offsets):
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
                # Combined: check bezier label collision with other tracks
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

    if legend_handles:
        leg_text_color = '#ffffff' if forecast_layout == 'pwards' else ('#ffffff' if not light else 'black')
        leg = ax.legend(handles=legend_handles, loc='lower left', framealpha=0.85,
                        fontsize=round(8 * dpi_scale),
                        facecolor='#000000' if forecast_layout == 'pwards' else ('white' if light else '#1a1a2e'),
                        edgecolor='gray', labelcolor=leg_text_color)
        leg.set_zorder(20)
    # ── Per-layout title ──
    storm_names = []
    for _t in all_tracks:
        _raw = _t.get("storm_name", _t.get("storm_id", f"T{idx+1}"))
        for _suffix in [" JMA FORECAST", " JTWC FORECAST", " NHC FORECAST"]:
            if _raw.upper().endswith(_suffix):
                _raw = _raw[:-_suffix.__len__()]
                break
        _src = _t.get("source", "").upper()
        
        # Parse for "Intl {Local}" format to extract international name
        intl_name = _raw.strip()
        if _src == "CWA" and intl_name.upper().startswith("CWA "):
            intl_name = intl_name[4:].strip()
        if "{" in _raw and "}" in _raw:
            # Format is "Intl {Local}"
            intl_name = _raw.split("{")[0].strip()
        
        if _src:
            storm_names.append(f"{intl_name} ({_src})")
        else:
            storm_names.append(intl_name)
    names_str = ", ".join(storm_names)
    if forecast_layout == "pwards":
        pass  # title drawn on PIL image
    elif forecast_layout == "pagasa":
        pass  # title drawn on PIL image
    elif forecast_layout == "jma":
        title_color_jma = '#000000' if light else '#F1F5FE'
        ax.set_title(f"{names_str} — Combined Forecast — JMA",
                     fontsize=round(11 * dpi_scale, 1), color=title_color_jma,
                     fontweight='600', fontfamily='sans-serif', pad=12)
    elif forecast_layout == "jtwc":
        title_color_jtwc = '#000000' if light else '#E14348'
        ax.set_title(f"{names_str} — Combined Forecast — JTWC",
                     fontsize=round(11 * dpi_scale, 1), color=title_color_jtwc,
                     fontweight='600', fontfamily='sans-serif', pad=12)
    else:
        title_color = label_color
        ax.set_title(f"Combined Forecast — {names_str}",
                     fontsize=round(12 * dpi_scale, 1) if light else 11,
                     color=title_color, fontweight='bold' if light else 'normal')

    fig.subplots_adjust(bottom=0.08)
    pil_img = None
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor=fig.get_facecolor())
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)

    # ── PAGASA / PWARDS title box (drawn on PIL image, auto-sized to text) ──
    if forecast_layout in ("pagasa", "pwards"):
        try:
            draw = ImageDraw.Draw(pil_img)
            iw, ih = pil_img.size
            cat_label = "Combined"
            # Parse names_str to extract just international names for display
            display_names = []
            for n in names_str.split(","):
                n = n.strip()
                # Format is "Intl (Agency)" - extract Intl
                if "(" in n and ")" in n:
                    intl = n.split("(")[0].strip()
                    display_names.append(intl)
                else:
                    display_names.append(n)
            sname = ", ".join(display_names) if display_names else "TROPICAL CYCLONES"
            row1 = f"Track and Intensity of {cat_label} {sname}"
            from datetime import datetime as _dt_now
            _now_utc = datetime.now(timezone.utc)
            day_str = _now_utc.strftime("%d")
            month_str = _now_utc.strftime("%B")
            year_str = str(_now_utc.year)
            h, m = _now_utc.hour, _now_utc.minute
            if m >= 30: h += 1
            if h >= 24: h -= 24
            if h == 0: hour_str = "12AM"
            elif h < 12: hour_str = f"{h}AM"
            elif h == 12: hour_str = "12PM"
            else: hour_str = f"{h - 12}PM"
            _subtitle = custom_title if custom_title else "Combined Forecast"
            row2 = f"{day_str} {month_str} {year_str}, {hour_str} {_subtitle}" if day_str else ""
            tfs, sfs = round(36 * dpi_scale), round(24 * dpi_scale)
            title_font = sub_font = None
            for _ in range(10):
                try:
                    tf = ImageFont.truetype("segoeuib.ttf", tfs)
                except Exception:
                    try:
                        tf = ImageFont.truetype("segoeui.ttf", tfs)
                    except Exception:
                        try:
                            tf = ImageFont.truetype("arialbd.ttf", tfs)
                        except Exception:
                            tf = ImageFont.load_default()
                try:
                    sf = ImageFont.truetype("segoeui.ttf", sfs)
                except Exception:
                    try:
                        sf = ImageFont.truetype("arial.ttf", sfs)
                    except Exception:
                        sf = ImageFont.load_default()
                b1 = draw.textbbox((0, 0), row1, font=tf)
                b2 = draw.textbbox((0, 0), row2, font=sf)
                tw = max(b1[2] - b1[0], b2[2] - b2[0])
                th = (b1[3] - b1[1]) + (b2[3] - b2[1]) + 1
                if tw < iw * 0.85 and tfs > 10 and sfs > 8:
                    title_font, sub_font = tf, sf
                    break
                tfs -= 2
                sfs = max(8, tfs - 4)
            if title_font is None:
                title_font = tf
                sub_font = sf
            pad_x = 40
            pad_y = 20
            box_w = tw + pad_x * 2
            box_h = th + pad_y * 2
            box_x = (iw - box_w) // 2
            box_y = 50
            draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=3)
            draw.rectangle([box_x + 4, box_y + 4, box_x + box_w - 4, box_y + box_h - 4], outline='white', width=2)
            draw.rectangle([box_x + 7, box_y + 7, box_x + box_w - 7, box_y + box_h - 7], outline='black', width=1)
            draw.rectangle([box_x + 8, box_y + 8, box_x + box_w - 8, box_y + box_h - 8], fill='black')
            cx = box_x + box_w // 2
            cy1 = box_y + pad_y + (b1[3] - b1[1]) // 2 - 4
            cy2 = cy1 + (b1[3] - b1[1]) // 2 + 10 + (b2[3] - b2[1]) // 2
            draw.text((cx, cy1), row1, fill='white', font=title_font, anchor='mm')
            if row2:
                draw.text((cx, cy2), row2, fill='white', font=sub_font, anchor='mm')
            # PWARDS side logos
            if forecast_layout == "pwards" and logo_path:
                try:
                    _mw_logo = Image.open(str(logo_path)).convert("RGBA")
                    _splash_path = Path(str(logo_path)).parent / "splash.png"
                    _splash_logo = Image.open(str(_splash_path)).convert("RGBA") if _splash_path.exists() else None
                    _mw_h = int(box_h * 1.4)
                    _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
                    _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
                    _mw_x = box_x - _mw_w - 20
                    _mw_y = box_y + (box_h - _mw_h) // 2
                    pil_img.paste(_mw_logo, (_mw_x, _mw_y), _mw_logo)
                    if _splash_logo:
                        _s_h = int(box_h * 1.3)
                        _s_w = int(_splash_logo.width * _s_h / _splash_logo.height)
                        _splash_logo = _splash_logo.resize((_s_w, _s_h), Image.LANCZOS)
                        _s_x = box_x + box_w + 15
                        _s_y = box_y + (box_h - _s_h) // 2
                        pil_img.paste(_splash_logo, (_s_x, _s_y), _splash_logo)
                except Exception:
                    pass
        except Exception:
            pass

    # Disclaimer text on map portion (before footer)
    if forecast_layout != "pwards":
        try:
            draw = ImageDraw.Draw(pil_img)
            iw, ih = pil_img.size
            disclaimer_text = "THIS IS AN EXPERIMENTAL FORECAST MADE BY MONWATCH-UI — IT MAY BE DERIVED FROM OFFICIAL SOURCES OR BE A CUSTOM-MADE EXPERIMENTAL FORECAST.\nPLEASE EXERCISE CAUTION AND CONSULT OFFICIAL SOURCES FOR AUTHORITATIVE INFORMATION."
            fs = max(7, int(ih * 0.011))
            try:
                fnt = ImageFont.truetype("consola.ttf", fs)
            except Exception:
                fnt = ImageFont.load_default()
            bb = draw.textbbox((0, 0), disclaimer_text, font=fnt)
            th = bb[3] - bb[1]
            draw.text((iw // 2, ih - th - 8), disclaimer_text, fill=(140, 140, 140), font=fnt, anchor='mb')
        except Exception:
            pass

    # ── Footer ──
    if logo_path and forecast_layout != "pwards":
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
        pil_img = _add_footer(pil_img, "", now_str, "Combined", logo_path, utc_offset, dark=(forecast_layout == "pwards"))
    if filename:
        pil_img.save(filename)
        return filename
    return np.array(pil_img)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate a forecast map for a tropical cyclone.")
    parser.add_argument("--storm_id", help="Storm identifier (e.g. al082025)")
    parser.add_argument("--input", required=True, help="JSON file with storm data")
    parser.add_argument("--output", required=True, help="Output PNG file path")
    parser.add_argument("--light", action="store_true", help="Use light theme with white background")
    parser.add_argument("--settings", help="Path to settings.json for forecast preferences")
    parser.add_argument("--logo", help="Path to logo image for footer")
    parser.add_argument("--layout", default="pwards", help="Forecast layout (default, pagasa, jma, jtwc, pwards, nhc)")
    parser.add_argument("--algorithm", default="polar", choices=["polar", "zigzag", "bezier", "cone", "smart_bezier"],
                        help="Label placement algorithm (polar=alternating offset, zigzag=same as polar, bezier=curve-normal offset, smart_bezier=bezier with cramp-aware side flip)")
    parser.add_argument("--cone", default="smooth", choices=["smooth", "union"],
                        help="Cone construction method (smooth=Catmull-Rom offsets, union=shapely circle union)")
    parser.add_argument("--margin", type=float, default=8.0,
                        help="Map extent margin in degrees around track")
    parser.add_argument("--combined", action="store_true", help="Input JSON contains combined_tracks array")
    args = parser.parse_args()

    if not HAS_DEPS:
        print("ERROR: Missing dependencies", file=sys.stderr)
        sys.exit(1)

    with open(args.input) as f:
        data = json.load(f)

    settings = {}
    if args.settings:
        try:
            with open(args.settings) as f:
                settings = json.load(f)
        except Exception:
            pass

    if args.layout:
        if 'forecast_preferences' not in settings:
            settings['forecast_preferences'] = {}
        settings['forecast_preferences']['forecast_layout'] = args.layout
    if args.cone:
        if 'forecast_preferences' not in settings:
            settings['forecast_preferences'] = {}
        settings['forecast_preferences']['cone_method'] = args.cone
    if args.algorithm:
        if 'forecast_preferences' not in settings:
            settings['forecast_preferences'] = {}
        settings['forecast_preferences']['algorithm'] = args.algorithm

    logo_path = Path(args.logo) if args.logo else None

    if args.combined or "combined_tracks" in data:
        all_tracks = data.get("combined_tracks", [])
        if not all_tracks:
            print("ERROR: No combined tracks", file=sys.stderr)
            sys.exit(1)
        custom_title = data.get("custom_title", "")
        resolution = data.get("resolution", "110m")
        print(f"Generating combined forecast for {len(all_tracks)} tracks...", file=sys.stderr)
        out_path = make_combined_forecast(
            all_tracks, settings=settings, logo_path=logo_path, light=args.light,
            filename=args.output, margin=args.margin, algorithm=args.algorithm,
            custom_title=custom_title, resolution=resolution,
        )
    else:
        storm_name = data.get("storm_name", args.storm_id or "Tropical Cyclone")
        track_points = data.get("track_points", [])
        probability_circles = data.get("probability_circles", [])
        kmz_cone = data.get("kmz_cone", "")
        kmz_track = data.get("kmz_track", "")
        kmz_wind_initial = data.get("kmz_wind_initial", "")
        issued_dtg = data.get("issued_dtg")
        if not track_points:
            print("ERROR: No track points", file=sys.stderr)
            sys.exit(1)
        print(f"Generating forecast for {storm_name} ({args.storm_id})...", file=sys.stderr)
        out_path = make_forecast(
            track_points, probability_circles, storm_name, args.storm_id or "",
            kmz_cone=kmz_cone, kmz_track=kmz_track, kmz_wind_initial=kmz_wind_initial,
            light=args.light, settings=settings, logo_path=logo_path,
            filename=args.output, algorithm=args.algorithm, margin=args.margin,
            issued_dtg=issued_dtg
        )
    if out_path:
        print(f"DONE:{args.output}")
    else:
        print("ERROR: Failed to generate forecast", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
