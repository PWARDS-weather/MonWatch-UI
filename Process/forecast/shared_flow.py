import sys
import json
import io
import os
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta

_log = print  # simple print-based logging to stderr

_NEW_ALGO_NAMES = {"anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp", "auto",
                   "railway_bezier", "8direction", "staggered_perp"}
try:
    from .label_placement_algorithms.integration_hub import (
        run_new_label_pipeline as _shared_new_pipeline
    )
    from .label_placement_algorithms.shared_models import (
        adapt_to_offsets_flat as _shared_new_flat
    )
    _HAS_NEW = True
except ImportError:
    _HAS_NEW = False
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
    from .utils import HAS_DEPS, _LAND, _OCEAN, _COASTLINE, _BORDERS
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
    HAS_DEPS = True

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
def _add_footer(pil_img, forecast_dt_str, now_utc_str, basin, logo_path=None, utc_offset=0, dark=False, tz_label=None):
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
    if tz_label:
        utc_label = tz_label
    else:
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

def _compute_unified_offsets(track_points, algorithm, px_p_deg_lon, px_p_deg_lat,
                              lon_min, lat_min, pts_p_px, all_labels, fontsize,
                              probability_circles=None, fig_w_px=1200, fig_h_px=800):
    if algorithm in _NEW_ALGO_NAMES and _HAS_NEW:
        try:
            canvas_w = fig_w_px
            canvas_h = fig_h_px
            result = _shared_new_pipeline(
                track_points,
                canvas_w=canvas_w, canvas_h=canvas_h,
                px_p_deg_lon=px_p_deg_lon, px_p_deg_lat=px_p_deg_lat,
                lon_min=lon_min, lat_min=lat_min, pts_p_px=pts_p_px,
                strategy=algorithm,
                probability_circles=probability_circles
            )
            return result
        except Exception as exc:
            _log(f"[new_algo] New pipeline failed ({exc}), falling back to polar", file=sys.stderr)
    return None

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


# ── Shared: dateline crossing helpers ──
def _normalize_track_extent(lons, margin=8.0):
    """
    Compute (lon_min, lon_max, central_lon) for a set of longitudes,
    handling tracks that cross the antimeridian (180/-180 dateline).
    Also returns shifted_lons suitable for plotting across the dateline.

    For dateline-crossing tracks, lon_min/lon_max may exceed 180 or be
    below -180; cartopy handles these correctly with PlateCarree.
    """
    if not lons:
        return -180, 180, 0, []

    norm = [((float(lon) + 180.0) % 360.0) - 180.0 for lon in lons]
    min_n = min(norm)
    max_n = max(norm)

    if max_n - min_n > 180:
        # Track crosses the dateline — shift negatives to 0..360 range
        shifted = [lon if lon >= 0 else lon + 360.0 for lon in norm]
        min_s = min(shifted)
        max_s = max(shifted)

        center_s = (min_s + max_s) / 2.0
        central_lon = center_s - 360.0 if center_s > 180.0 else center_s

        half_span = max(max_s - center_s, center_s - min_s) + margin
        lon_min = center_s - half_span
        lon_max = center_s + half_span
        return lon_min, lon_max, central_lon, shifted
    else:
        central_lon = (min_n + max_n) / 2.0
        lon_min = min_n - margin
        lon_max = max_n + margin
        return lon_min, lon_max, central_lon, norm


def _adjust_lons_for_plot(lons, central_lon):
    """
    Adjust longitudes to be continuous around central_lon so that
    lines drawn across the dateline connect correctly.
    """
    if not lons:
        return lons
    norm = [((float(lon) + 180.0) % 360.0) - 180.0 for lon in lons]
    adjusted = []
    for lon in norm:
        adj = lon
        while adj < central_lon - 180.0:
            adj += 360.0
        while adj > central_lon + 180.0:
            adj -= 360.0
        adjusted.append(adj)
    # Unwrap so consecutive points differ by < 180 deg, preventing spurious
    # lines drawn across the whole map when a track crosses the dateline.
    for i in range(1, len(adjusted)):
        delta = adjusted[i] - adjusted[i-1]
        if delta > 180.0:
            adjusted[i] -= 360.0
        elif delta < -180.0:
            adjusted[i] += 360.0
    return adjusted


# ── Shared: extent computation (single track) ──
def _compute_single_extent(track_points, probability_circles, kmz_cone,
                           margin, dpi, figsize, layout, logo_path,
                           danger_swath=None, best_track_points=None):
    """Copy verbatim from common.py lines 670-736"""
    # Copy the exact code from common.py lines 670-736
    lons = [p.get("lon") for p in track_points if p.get("lon") is not None]
    lats = [p.get("lat") for p in track_points if p.get("lat") is not None]
    if best_track_points:
        lons += [p.get("lon") for p in best_track_points if p.get("lon") is not None]
        lats += [p.get("lat") for p in best_track_points if p.get("lat") is not None]
    if not lons:
        return None, None, None, None, None

    # Detect dateline crossing
    _dl_lon_min, _dl_lon_max, _dl_center, _dl_shifted = _normalize_track_extent(lons, 0)
    _dateline_cross = (_dl_lon_max - _dl_lon_min) > 180
    _dl_min = min(_dl_shifted)
    _dl_max = max(_dl_shifted)

    if layout in ("pwards", "pagasa", "monwatch"):
        fp = _find_first_forecast_point(track_points, layout)
        if fp and fp.get("lon") is not None and fp.get("lat") is not None:
            _c_lon_center = fp["lon"]
            if _dateline_cross and _c_lon_center < 0:
                _c_lon_center += 360.0
            _c_lat_center = fp["lat"]
        else:
            cone_lon_min, cone_lon_max = _dl_min, _dl_max
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
            elif danger_swath:
                _ds_lons = [c[0] for c in danger_swath]
                _ds_lats = [c[1] for c in danger_swath]
                if _ds_lons:
                    cone_lon_min = min(cone_lon_min, min(_ds_lons))
                    cone_lon_max = max(cone_lon_max, max(_ds_lons))
                    cone_lat_min = min(cone_lat_min, min(_ds_lats))
                    cone_lat_max = max(cone_lat_max, max(_ds_lats))
            _c_lon_center = (cone_lon_min + cone_lon_max) / 2
            _c_lat_center = (cone_lat_min + cone_lat_max) / 2
        # Calculate cone extent for proper margins (use dateline-shifted lons)
        cone_lon_min, cone_lon_max = _dl_min, _dl_max
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
        elif danger_swath:
            _ds_lons = [c[0] for c in danger_swath]
            _ds_lats = [c[1] for c in danger_swath]
            if _ds_lons:
                cone_lon_min = min(cone_lon_min, min(_ds_lons))
                cone_lon_max = max(cone_lon_max, max(_ds_lons))
                cone_lat_min = min(cone_lat_min, min(_ds_lats))
                cone_lat_max = max(cone_lat_max, max(_ds_lats))
        if layout == "monwatch":
            _c_lon_half = max(abs(cone_lon_max - _c_lon_center), abs(_c_lon_center - cone_lon_min)) + 5.0
            _c_lat_half = max(abs(cone_lat_max - _c_lat_center), abs(_c_lat_center - cone_lat_min)) + 5.0
        else:
            _c_lon_half = (cone_lon_max - cone_lon_min) / 2 + 5.0
            _c_lat_half = (cone_lat_max - cone_lat_min) / 2 + 5.0
        if layout == "pwards" and logo_path:
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
        lon_min = _c_lon_center - _c_lon_half
        lon_max = _c_lon_center + _c_lon_half
        lat_min = max(_c_lat_center - _c_lat_half, -90)
        lat_max = min(_c_lat_center + _c_lat_half, 90)
        central_lon = _c_lon_center
    else:
        _dl_lon_min, _dl_lon_max, _dl_central, _ = _normalize_track_extent(lons, margin)
        lon_min = _dl_lon_min
        lon_max = _dl_lon_max
        lat_min = max(min(lats) - margin, -90)
        lat_max = min(max(lats) + margin, 90)
        central_lon = _dl_central
    return lon_min, lon_max, lat_min, lat_max, central_lon


# ── Shared: extent computation (combined) ──
def _compute_combined_extent(all_tracks, layout, margin, dpi, figsize, logo_path):
    """Copy from common.py lines 2118-2202"""
    all_lons, all_lats = [], []
    first_fp_lon = None
    first_fp_lat = None
    for t in all_tracks:
        _pts = t.get("track_points", [])
        for p in _pts:
            if p.get("lon") is not None: all_lons.append(p["lon"])
            if p.get("lat") is not None: all_lats.append(p["lat"])
        _bt_pts = t.get("best_track_points", [])
        for p in _bt_pts:
            if p.get("lon") is not None: all_lons.append(p["lon"])
            if p.get("lat") is not None: all_lats.append(p["lat"])
        if first_fp_lon is None and first_fp_lat is None:
            fp = _find_first_forecast_point(_pts, layout)
            if fp and fp.get("lon") is not None and fp.get("lat") is not None:
                first_fp_lon = fp["lon"]
                first_fp_lat = fp["lat"]
    if not all_lons:
        return None, None, None, None, None
    # Detect dateline crossing across all tracks
    _dl_lon_min, _dl_lon_max, _dl_center, _dl_shifted = _normalize_track_extent(all_lons, 0)
    _dateline_cross = (_dl_lon_max - _dl_lon_min) > 180
    _dl_min = min(_dl_shifted)
    _dl_max = max(_dl_shifted)

    if layout in ("pwards", "pagasa", "monwatch"):
        # Compute cone extent from all tracks (using shifted lons as base)
        cone_lon_min, cone_lon_max = _dl_min, _dl_max
        cone_lat_min, cone_lat_max = min(all_lats), max(all_lats)
        for t in all_tracks:
            _pts = t.get("track_points", []); _circles = t.get("probability_circles", [])
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
        # Center on first forecast point if available (skip for multi-storm combined)
        if first_fp_lon is not None and first_fp_lat is not None and layout != "monwatch":
            _c_lon_center = first_fp_lon
            if _dateline_cross and _c_lon_center < 0:
                _c_lon_center += 360.0
            _c_lat_center = first_fp_lat
        else:
            _c_lon_center = (cone_lon_min + cone_lon_max) / 2
            _c_lat_center = (cone_lat_min + cone_lat_max) / 2
        # Add 5 degree margin around cone
        if layout == "monwatch":
            _c_lon_half = max(abs(cone_lon_max - _c_lon_center), abs(_c_lon_center - cone_lon_min)) + 5.0
            _c_lat_half = max(abs(cone_lat_max - _c_lat_center), abs(_c_lat_center - cone_lat_min)) + 5.0
        else:
            _c_lon_half = (cone_lon_max - cone_lon_min) / 2 + 5.0
            _c_lat_half = (cone_lat_max - cone_lat_min) / 2 + 5.0
        if layout == "pwards" and logo_path:
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
        lon_min = _c_lon_center - _c_lon_half
        lon_max = _c_lon_center + _c_lon_half
        lat_min = max(_c_lat_center - _c_lat_half, -90)
        lat_max = min(_c_lat_center + _c_lat_half, 90)
        central_lon = _c_lon_center
    else:
        _dl_lon_min, _dl_lon_max, _dl_central, _ = _normalize_track_extent(all_lons, margin)
        _lat_center = (min(all_lats) + max(all_lats)) / 2
        _lat_half = (max(all_lats) - min(all_lats)) / 2 + margin
        _lon_center = _dl_central
        _lon_half = max(_dl_lon_max - _dl_central, _dl_central - _dl_lon_min) + margin
        _fig_aspect = figsize[0] / figsize[1]
        _ext_aspect = _lon_half / _lat_half if _lat_half > 0 else _fig_aspect
        if _ext_aspect < _fig_aspect: _lon_half = _lat_half * _fig_aspect
        else: _lat_half = _lon_half / _fig_aspect
        lon_min = _lon_center - _lon_half
        lon_max = _lon_center + _lon_half
        lat_min = max(_lat_center - _lat_half, -90)
        lat_max = min(_lat_center + _lat_half, 90)
        central_lon = _lon_center
    return lon_min, lon_max, lat_min, lat_max, central_lon


# ── Shared: build labels ──
def _build_track_labels(track_points, forecast_layout, show_dt, show_wind,
                        time_fmt, utc_offset, wind_unit, issued_dtg):
    """Copy from common.py lines 1449-1527"""
    all_labels = []
    for p in track_points:
        pt_lon = p.get("lon"); pt_lat = p.get("lat")
        intensity = p.get("intensity"); dt_str = p.get("datetime", "")
        advanced_hours = p.get("advanced_hours")
        if pt_lon is None or pt_lat is None:
            all_labels.append(""); continue
        label_parts = []
        dt_obj = None
        if issued_dtg and advanced_hours is not None:
            base_dt = _parse_jtwc_dtg(issued_dtg)
            if base_dt is not None: dt_obj = base_dt + timedelta(hours=advanced_hours)
        if dt_obj is None and dt_str: dt_obj = _parse_dt(dt_str)
        if forecast_layout in ("pagasa", "pwards", "monwatch"):
            if show_dt and dt_obj is not None:
                _utc_off = utc_offset - _storage_offset(track_points, forecast_layout)
                if _utc_off != 0: dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=_utc_off)
                h, m = dt_obj.hour, dt_obj.minute
                if m >= 30: h += 1
                if h >= 24: h -= 24
                if h == 0: hour_str = "12AM"
                elif h < 12: hour_str = f"{h}AM"
                elif h == 12: hour_str = "12PM"
                else: hour_str = f"{h - 12}PM"
                label_parts.append(f"{hour_str} {dt_obj.day} {dt_obj.strftime('%b.')} {dt_obj.year} ({dt_obj.strftime('%a')})")
            elif show_dt and dt_str: label_parts.append(dt_str)
        else:
            if show_dt:
                if dt_obj is not None:
                    _so_val = _storage_offset(track_points, forecast_layout)
                    _utc_off = utc_offset - _so_val
                    import sys; print(f"[DBG] dt_str={dt_str!r} dt_obj={dt_obj} utc_offset={utc_offset} _so={_so_val} _utc_off={_utc_off}", file=sys.stderr)
                    if _utc_off != 0: dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=_utc_off)
                    if issued_dtg and advanced_hours is not None: label_parts.append(dt_obj.strftime("%d%H%M") + "Z")
                    elif time_fmt == "civilian": label_parts.append(dt_obj.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if _utc_off == 0 else ""))
                    else: label_parts.append(dt_obj.strftime("%Y-%m-%d %H:%M") + (" UTC" if _utc_off == 0 else ""))
                elif dt_str: label_parts.append(dt_str)
            if show_wind and intensity is not None:
                wind_val = float(intensity)
                if wind_unit == "kmh": wind_val = round(wind_val * 1.852); label_parts.append(f"{wind_val} km/h")
                elif wind_unit == "mph": wind_val = round(wind_val * 1.151); label_parts.append(f"{wind_val} mph")
                elif wind_unit == "ms": wind_val = round(wind_val * 0.514); label_parts.append(f"{wind_val} m/s")
                else: label_parts.append(f"{wind_val} kt")
            pt_radii = p.get("wind_radii", {})
            if pt_radii:
                wr_parts = []
                for kt_key in ("64", "50", "34"):
                    radii = pt_radii.get(kt_key, {})
                    if radii and any(v is not None for v in radii.values()):
                        vals = [str(radii.get(q, "")) for q in ("ne", "se", "sw", "nw")]
                        wr_parts.append(f"{kt_key}:{'/'.join(vals)}")
                if wr_parts: label_parts.append("|".join(wr_parts))
        all_labels.append("  ".join(label_parts) if label_parts else "")
    return all_labels


# ── Shared: find first future idx (PAGASA) ──
def _find_first_future_idx(track_points, prob_circles):
    """Copy from common.py lines 1272-1289"""
    first_future_idx = len(track_points)
    if prob_circles:
        _circ_centers = []
        for _c in prob_circles:
            _clon = _c.get("center_lon") or (_c.get("center")[1] if _c.get("center") and len(_c["center"]) > 1 else None)
            _clat = _c.get("center_lat") or (_c.get("center")[0] if _c.get("center") and len(_c["center"]) > 0 else None)
            if _clon is not None and _clat is not None: _circ_centers.append((_clat, _clon))
        for _i, _p in enumerate(track_points):
            _plon = _p.get("lon"); _plat = _p.get("lat")
            if _plon is not None and _plat is not None:
                for _cc in _circ_centers:
                    if abs(_plat - _cc[0]) < 0.01 and abs(_plon - _cc[1]) < 0.01:
                        first_future_idx = _i - 1; break
            if first_future_idx < len(track_points): break
    return first_future_idx


# ── Shared: save fig to PIL ──
def _fig_to_pil(fig, dpi, facecolor):
    """Save matplotlib figure to PIL Image. From common.py lines 1882-1890"""
    fig.subplots_adjust(bottom=0.06, left=0.02, right=0.98, top=0.96)
    fig.canvas.draw()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor=facecolor, edgecolor='none')
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)
    return pil_img


# ── Shared: timezone helpers ──
_AGENCY_STORAGE_OFFSET = {"pagasa": 8, "cwa": 8, "jma": 0, "jtwc": 0, "nhc": 0, "pwards": 0, "monwatch": 0, "default": 0}

def _storage_offset(track_points=None, forecast_layout="", source=""):
    """Return the timezone offset of stored datetime data for this agency.
    PAGASA/CWA store in +8, others store in UTC (0)."""
    if source:
        return _AGENCY_STORAGE_OFFSET.get(source, 0)
    if any(p.get("cyclone_type") is not None for p in track_points or []):
        return 8
    if any(p.get("circle_15ms_km") is not None or p.get("moving_prediction") for p in track_points or []):
        return 8
    return _AGENCY_STORAGE_OFFSET.get(forecast_layout, 0)

# ── Shared: PAGASA/PWARDS PIL title box ──
def _is_pagasa_data(track_points):
    return any(p.get("cyclone_type") is not None for p in track_points) if track_points else False

def _find_first_forecast_point(track_points, forecast_layout):
    if not track_points:
        return None
    if forecast_layout in ("pagasa", "pwards", "monwatch") and _is_pagasa_data(track_points):
        for i, p in enumerate(track_points):
            r = p.get("radius_km", 0)
            if r > 0:
                return p
            if r == 0 and i + 1 < len(track_points):
                nr = track_points[i+1].get("radius_km", 0)
                if nr > 0:
                    return p
        return track_points[0]
    return track_points[0]

def _draw_pil_title_box(pil_img, storm_name, track_points, dpi_scale, dpi, logo_path, forecast_layout, utc_offset=0,
                        source=None, source_info=None, basin=""):
    try:
        draw = ImageDraw.Draw(pil_img)
        iw, ih = pil_img.size
        fp = _find_first_forecast_point(track_points, forecast_layout)
        first_intensity = fp.get("intensity") if fp else None
        first_cat = fp.get("intensity_category", "") if fp else ""
        cat_label = _cat_label(first_intensity, source=source, basin=basin) if first_intensity is not None else (first_cat or "TD")
        sname = storm_name.upper() if storm_name else "TROPICAL CYCLONE"
        dt_str = fp.get("datetime", "") if fp else ""
        dt_obj = _parse_dt(dt_str) if dt_str else None
        if dt_obj:
            _utc_off = utc_offset - _storage_offset(track_points, forecast_layout)
            if _utc_off != 0:
                dt_obj = dt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=_utc_off)
            day_str = dt_obj.strftime("%d"); month_str = dt_obj.strftime("%B")
            year_str = str(dt_obj.year); h, m = dt_obj.hour, dt_obj.minute
            if m >= 30: h += 1
            if h >= 24: h -= 24
            if h == 0: hour_str = "12AM"
            elif h < 12: hour_str = f"{h}AM"
            elif h == 12: hour_str = "12PM"
            else: hour_str = f"{h - 12}PM"
        else: day_str = month_str = year_str = hour_str = ""
        if source and source_info:
            _src_label = source.upper()
            if source == "jtwc":
                _sn = source_info.get("storm_name", sname)
                _cat = source_info.get("category", cat_label)
                _wn = source_info.get("warning_nr", 1)
                row1 = f"Track and Intensity Forecast of {_cat} {_sn} (JTWC)"
                _bd = source_info.get("bulletin_day")
                _bt = source_info.get("bulletin_time", "")
                if _bd and _bt:
                    _h = int(_bt[:2]) if len(_bt) >= 2 else h; _m = int(_bt[2:4]) if len(_bt) >= 4 else 0
                    if _m >= 30: _h += 1
                    if _h >= 24: _h -= 24
                    _hs = "12AM" if _h == 0 else f"{_h}AM" if _h < 12 else "12PM" if _h == 12 else f"{_h - 12}PM"
                    row2 = f"{_bd} July 2026, {_hs} Forecast Bulletin #{_wn}"
                else:
                    row2 = f"{day_str} {month_str} {year_str}, {hour_str} Forecast Bulletin #{_wn}" if day_str else f"Forecast Bulletin #{_wn}"
            elif source == "nhc":
                _sn = source_info.get("storm_name", sname)
                _an = source_info.get("advisory_num", 1)
                row1 = f"Track and Intensity Forecast of {cat_label} {_sn} (NHC)"
                row2 = f"{day_str} {month_str} {year_str}, {hour_str} Advisory #{_an}" if day_str else f"Advisory #{_an}"
            elif source == "cwa":
                _sn = source_info.get("storm_name", sname)
                _lt = source_info.get("latest_time", dt_str)
                _fc = source_info.get("fix_count", 1)
                row1 = f"Track and Intensity Forecast of {cat_label} {_sn} (CWA)"
                _lt_obj = _parse_dt(_lt) if _lt else dt_obj
                if _lt_obj:
                    _so = _storage_offset(track_points, forecast_layout, source)
                    _off = utc_offset - _so
                    if _lt and _off != 0:
                        _lt_obj = _lt_obj.replace(tzinfo=timezone.utc) + timedelta(hours=_off)
                    _d = _lt_obj.strftime("%d"); _m = _lt_obj.strftime("%B"); _y = str(_lt_obj.year)
                    _h = _lt_obj.hour; _min = _lt_obj.minute
                    if _min >= 30: _h += 1
                    if _h >= 24: _h -= 24
                    _hs = "12AM" if _h == 0 else f"{_h}AM" if _h < 12 else "12PM" if _h == 12 else f"{_h - 12}PM"
                    row2 = f"{_d} {_m} {_y}, {_hs} | Forecast Count #{_fc}"
                else:
                    row2 = f"Forecast Count #{_fc}"
            elif source == "jma":
                _sn = source_info.get("storm_name", sname)
                _iss = source_info.get("issuance", dt_str)
                _tc = source_info.get("typhoon_count", 1)
                row1 = f"Track and Intensity Forecast of {cat_label} {_sn} (JMA)"
                _iss_obj = _parse_dt(_iss) if _iss else dt_obj
                if _iss_obj:
                    _so = _storage_offset(track_points, forecast_layout, source)
                    _off = utc_offset - _so
                    if _iss and _off != 0:
                        _iss_obj = _iss_obj.replace(tzinfo=timezone.utc) + timedelta(hours=_off)
                    _d = _iss_obj.strftime("%d"); _m = _iss_obj.strftime("%B"); _y = str(_iss_obj.year)
                    _h = _iss_obj.hour; _min = _iss_obj.minute
                    if _min >= 30: _h += 1
                    if _h >= 24: _h -= 24
                    _hs = "12AM" if _h == 0 else f"{_h}AM" if _h < 12 else "12PM" if _h == 12 else f"{_h - 12}PM"
                    row2 = f"{_d} {_m} {_y}, {_hs} | Forecast Count #{_tc}"
                else:
                    row2 = f"Forecast Count #{_tc}"
            elif source == "pagasa":
                _rn = source_info.get("raw_name", sname)
                _bc = source_info.get("bulletin_count", 1)
                _dn = _rn.replace("{", " {").replace("}", "}") if "{" in _rn else _rn
                row1 = f"Track and Intensity Forecast of {cat_label} {_dn} (PAGASA)"
                row2 = f"{day_str} {month_str} {year_str}, {hour_str} Forecast Bulletin #{_bc}" if day_str else f"Forecast Bulletin #{_bc}"
            else:
                row1 = f"Track and Intensity Forecast of {cat_label} {sname}"
                row2 = f"{day_str} {month_str} {year_str}, {hour_str} Forecast Bulletin #1" if day_str else ""
        else:
            row1 = f"Track and Intensity Forecast of {cat_label} {sname}"
            row2 = f"{day_str} {month_str} {year_str}, {hour_str} Forecast Bulletin #1" if day_str else ""
        tfs, sfs = round(36 * dpi_scale), round(24 * dpi_scale)
        title_font = sub_font = None
        for _ in range(10):
            try: tf = ImageFont.truetype("segoeuib.ttf", tfs)
            except Exception:
                try: tf = ImageFont.truetype("segoeui.ttf", tfs)
                except Exception:
                    try: tf = ImageFont.truetype("arialbd.ttf", tfs)
                    except Exception: tf = ImageFont.load_default()
            try: sf = ImageFont.truetype("segoeui.ttf", sfs)
            except Exception:
                try: sf = ImageFont.truetype("arial.ttf", sfs)
                except Exception: sf = ImageFont.load_default()
            b1 = draw.textbbox((0, 0), row1, font=tf); b2 = draw.textbbox((0, 0), row2, font=sf)
            tw = max(b1[2] - b1[0], b2[2] - b2[0]); th = (b1[3] - b1[1]) + (b2[3] - b2[1]) + 1
            if tw < iw * 0.85 and tfs > 10 and sfs > 8: title_font, sub_font = tf, sf; break
            tfs -= 2; sfs = max(8, tfs - 4)
        if title_font is None: title_font, sub_font = tf, sf
        pad_x, pad_y = 40, 20
        box_w = tw + pad_x * 2; box_h = th + pad_y * 2
        box_x = (iw - box_w) // 2; box_y = 50
        if forecast_layout == "monwatch":
            draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=3)
            draw.rectangle([box_x + 4, box_y + 4, box_x + box_w - 4, box_y + box_h - 4], outline='black', width=2)
            draw.rectangle([box_x + 7, box_y + 7, box_x + box_w - 7, box_y + box_h - 7], outline='black', width=1)
            draw.rectangle([box_x + 8, box_y + 8, box_x + box_w - 8, box_y + box_h - 8], fill='#607d8b')
        else:
            draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=3)
            draw.rectangle([box_x + 4, box_y + 4, box_x + box_w - 4, box_y + box_h - 4], outline='white', width=2)
            draw.rectangle([box_x + 7, box_y + 7, box_x + box_w - 7, box_y + box_h - 7], outline='black', width=1)
            draw.rectangle([box_x + 8, box_y + 8, box_x + box_w - 8, box_y + box_h - 8], fill='black')
        cx = box_x + box_w // 2
        cy1 = box_y + pad_y + (b1[3] - b1[1]) // 2 - 4
        cy2 = cy1 + (b1[3] - b1[1]) // 2 + 10 + (b2[3] - b2[1]) // 2
        draw.text((cx, cy1), row1, fill='white', font=title_font, anchor='mm')
        if row2: draw.text((cx, cy2), row2, fill='white', font=sub_font, anchor='mm')
        if forecast_layout == "monwatch":
            _dlines = ["THIS IS AN EXPERIMENTAL FORECAST MADE BY MONWATCH-UI - IT MAY BE DERIVED FROM OFFICIAL SOURCES OR BE A CUSTOM-MADE EXPERIMENTAL FORECAST.",
                       "PLEASE EXERCISE CAUTION AND CONSULT OFFICIAL SOURCES FOR AUTHORITATIVE INFORMATION."]
            _dfs = max(14, int(ih * 0.018))
            try: _dfnt = ImageFont.truetype("segoeuib.ttf", _dfs)
            except Exception:
                try: _dfnt = ImageFont.truetype("arialbd.ttf", _dfs)
                except Exception: _dfnt = ImageFont.load_default()
            _lh = draw.textbbox((0, 0), _dlines[0], font=_dfnt)[3] - draw.textbbox((0, 0), _dlines[0], font=_dfnt)[1]
            _dth = _lh * 2 + 4
            _by = ih - _dth - 40
            for _dx in (-1, 0, 1):
                for _dy in (-1, 0, 1):
                    draw.text((iw // 2 + _dx, _by + _dy), _dlines[0], fill='black', font=_dfnt, anchor='mm')
                    draw.text((iw // 2 + _dx, _by + _lh + 4 + _dy), _dlines[1], fill='black', font=_dfnt, anchor='mm')
            draw.text((iw // 2, _by), _dlines[0], fill='white', font=_dfnt, anchor='mm')
            draw.text((iw // 2, _by + _lh + 4), _dlines[1], fill='white', font=_dfnt, anchor='mm')
        if forecast_layout == "pwards" and logo_path:
            try:
                _mw_logo = Image.open(str(logo_path)).convert("RGBA")
                _splash_path = Path(str(logo_path)).parent / "splash.png"
                _splash_logo = Image.open(str(_splash_path)).convert("RGBA") if _splash_path.exists() else None
                _mw_h = int(box_h * 1.4); _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
                _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
                _mw_x = box_x - _mw_w - 20; _mw_y = box_y + (box_h - _mw_h) // 2
                pil_img.paste(_mw_logo, (_mw_x, _mw_y), _mw_logo)
                if _splash_logo:
                    _s_h = int(box_h * 1.3); _s_w = int(_splash_logo.width * _s_h / _splash_logo.height)
                    _splash_logo = _splash_logo.resize((_s_w, _s_h), Image.LANCZOS)
                    _s_x = box_x + box_w + 15; _s_y = box_y + (box_h - _s_h) // 2
                    pil_img.paste(_splash_logo, (_s_x, _s_y), _splash_logo)
            except Exception: pass
    except Exception: pass
    return pil_img


# ── Shared: combined PIL title box ──
def _draw_combined_title_box(pil_img, names_str, custom_title, dpi_scale, dpi, logo_path, forecast_layout, track_points=None, utc_offset=0,
                             all_tracks=None):
    try:
        draw = ImageDraw.Draw(pil_img); iw, ih = pil_img.size
        row1 = f"Track and Intensity | Combined Forecast of {names_str}"
        row2 = "This is an Experimental Product containing multiple forecast from different agencies"
        tfs, sfs = round(36 * dpi_scale), round(24 * dpi_scale)
        title_font = sub_font = None
        for _ in range(10):
            try: tf = ImageFont.truetype("segoeuib.ttf", tfs)
            except Exception:
                try: tf = ImageFont.truetype("segoeui.ttf", tfs)
                except Exception:
                    try: tf = ImageFont.truetype("arialbd.ttf", tfs)
                    except Exception: tf = ImageFont.load_default()
            try: sf = ImageFont.truetype("segoeui.ttf", sfs)
            except Exception:
                try: sf = ImageFont.truetype("arial.ttf", sfs)
                except Exception: sf = ImageFont.load_default()
            b1 = draw.textbbox((0, 0), row1, font=tf); b2 = draw.textbbox((0, 0), row2, font=sf)
            tw = max(b1[2] - b1[0], b2[2] - b2[0]); th = (b1[3] - b1[1]) + (b2[3] - b2[1]) + 1
            if tw < iw * 0.85 and tfs > 10 and sfs > 8: title_font, sub_font = tf, sf; break
            tfs -= 2; sfs = max(8, tfs - 4)
        if title_font is None: title_font, sub_font = tf, sf
        pad_x, pad_y = 40, 20
        box_w = tw + pad_x * 2; box_h = th + pad_y * 2
        box_x = (iw - box_w) // 2; box_y = 50
        if forecast_layout == "monwatch":
            draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=3)
            draw.rectangle([box_x + 4, box_y + 4, box_x + box_w - 4, box_y + box_h - 4], outline='black', width=2)
            draw.rectangle([box_x + 7, box_y + 7, box_x + box_w - 7, box_y + box_h - 7], outline='black', width=1)
            draw.rectangle([box_x + 8, box_y + 8, box_x + box_w - 8, box_y + box_h - 8], fill='#607d8b')
        else:
            draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=3)
            draw.rectangle([box_x + 4, box_y + 4, box_x + box_w - 4, box_y + box_h - 4], outline='white', width=2)
            draw.rectangle([box_x + 7, box_y + 7, box_x + box_w - 7, box_y + box_h - 7], outline='black', width=1)
            draw.rectangle([box_x + 8, box_y + 8, box_x + box_w - 8, box_y + box_h - 8], fill='black')
        cx = box_x + box_w // 2
        cy1 = box_y + pad_y + (b1[3] - b1[1]) // 2 - 4
        cy2 = cy1 + (b1[3] - b1[1]) // 2 + 10 + (b2[3] - b2[1]) // 2
        draw.text((cx, cy1), row1, fill='white', font=title_font, anchor='mm')
        if row2: draw.text((cx, cy2), row2, fill='white', font=sub_font, anchor='mm')
        if forecast_layout == "monwatch":
            _dlines = ["THIS IS AN EXPERIMENTAL FORECAST MADE BY MONWATCH-UI - IT MAY BE DERIVED FROM OFFICIAL SOURCES OR BE A CUSTOM-MADE EXPERIMENTAL FORECAST.",
                       "PLEASE EXERCISE CAUTION AND CONSULT OFFICIAL SOURCES FOR AUTHORITATIVE INFORMATION."]
            _dfs = max(14, int(ih * 0.018))
            try: _dfnt = ImageFont.truetype("segoeuib.ttf", _dfs)
            except Exception:
                try: _dfnt = ImageFont.truetype("arialbd.ttf", _dfs)
                except Exception: _dfnt = ImageFont.load_default()
            _lh = draw.textbbox((0, 0), _dlines[0], font=_dfnt)[3] - draw.textbbox((0, 0), _dlines[0], font=_dfnt)[1]
            _dth = _lh * 2 + 4
            _by = ih - _dth - 40
            for _dx in (-1, 0, 1):
                for _dy in (-1, 0, 1):
                    draw.text((iw // 2 + _dx, _by + _dy), _dlines[0], fill='black', font=_dfnt, anchor='mm')
                    draw.text((iw // 2 + _dx, _by + _lh + 4 + _dy), _dlines[1], fill='black', font=_dfnt, anchor='mm')
            draw.text((iw // 2, _by), _dlines[0], fill='white', font=_dfnt, anchor='mm')
            draw.text((iw // 2, _by + _lh + 4), _dlines[1], fill='white', font=_dfnt, anchor='mm')
        if forecast_layout == "pwards" and logo_path:
            try:
                _mw_logo = Image.open(str(logo_path)).convert("RGBA")
                _splash_path = Path(str(logo_path)).parent / "splash.png"
                _splash_logo = Image.open(str(_splash_path)).convert("RGBA") if _splash_path.exists() else None
                _mw_h = int(box_h * 1.4); _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
                _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
                _mw_x = box_x - _mw_w - 20; _mw_y = box_y + (box_h - _mw_h) // 2
                pil_img.paste(_mw_logo, (_mw_x, _mw_y), _mw_logo)
                if _splash_logo:
                    _s_h = int(box_h * 1.3); _s_w = int(_splash_logo.width * _s_h / _splash_logo.height)
                    _splash_logo = _splash_logo.resize((_s_w, _s_h), Image.LANCZOS)
                    _s_x = box_x + box_w + 15; _s_y = box_y + (box_h - _s_h) // 2
                    pil_img.paste(_splash_logo, (_s_x, _s_y), _splash_logo)
            except Exception: pass
    except Exception: pass
    return pil_img


# ── Shared: footer ──
def _add_forecast_footer(pil_img, track_points, storm_id, logo_path, settings, forecast_layout, light):
    """Add footer with logo to PIL image. Adapted from common.py lines 2047-2081"""
    if not logo_path or forecast_layout in ("pwards", "monwatch"):
        return pil_img
    _utc_off = 0
    if settings:
        _utc_off = settings.get("forecast_preferences", {}).get("utc_offset", 0)
    if forecast_layout == "pagasa":
        _utc_off = 0
    forecast_dt = ""
    if track_points:
        fp = _find_first_forecast_point(track_points, forecast_layout)
        first_dt = fp.get("datetime", "") if fp else ""
        if first_dt:
            try:
                fdt = _parse_dt(first_dt)
                if fdt is not None:
                    fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
                    utc_offset = _utc_off
                    time_fmt = fcst_prefs.get("time_format", "military")
                    so = _storage_offset(track_points, forecast_layout)
                    _utc_off2 = utc_offset - so
                    if _utc_off2 != 0: fdt = fdt.replace(tzinfo=timezone.utc) + timedelta(hours=_utc_off2)
                    _suffix = " PHT" if forecast_layout == "pagasa" else (" UTC" if _utc_off2 == 0 else "")
                    if time_fmt == "civilian": forecast_dt = fdt.strftime("%Y-%m-%d %I:%M %p") + _suffix
                    else: forecast_dt = fdt.strftime("%Y-%m-%d %H:%M") + _suffix
                else: forecast_dt = first_dt
            except Exception: forecast_dt = first_dt
    fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
    utc_offset = _utc_off
    time_fmt = fcst_prefs.get("time_format", "military")
    now = datetime.now(timezone.utc)
    if utc_offset != 0: now = now + timedelta(hours=utc_offset)
    if time_fmt == "civilian": now_str = now.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if utc_offset == 0 else "")
    else: now_str = now.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else "")
    basin_name = ("East Pacific" if storm_id.startswith("ep")
                  else "Atlantic" if storm_id.startswith("al")
                  else "Central Pacific" if storm_id.startswith("cp")
                  else "Western Pacific")
    tz_label = "PHT" if forecast_layout == "pagasa" else None
    pil_img = _add_footer(pil_img, forecast_dt, now_str, basin_name, logo_path, utc_offset, dark=False, tz_label=tz_label)
    return pil_img


# ── Shared: combined footer ──
def _add_combined_footer(pil_img, logo_path, settings, forecast_layout):
    """Add footer for combined forecast. Adapted from common.py lines 3076-3087"""
    if not logo_path or forecast_layout in ("pwards", "monwatch"):
        return pil_img
    fcst_prefs = settings.get("forecast_preferences", {}) if settings else {}
    utc_offset = fcst_prefs.get("utc_offset", 0)
    time_fmt = fcst_prefs.get("time_format", "military")
    now = datetime.now(timezone.utc)
    if utc_offset != 0: now = now + timedelta(hours=utc_offset)
    if time_fmt == "civilian": now_str = now.strftime("%Y-%m-%d %I:%M %p") + (" UTC" if utc_offset == 0 else "")
    else: now_str = now.strftime("%Y-%m-%d %H:%M") + (" UTC" if utc_offset == 0 else "")
    pil_img = _add_footer(pil_img, "", now_str, "Combined", logo_path, utc_offset, dark=False)
    return pil_img


# ── Shared: disclaimer ──
def _add_disclaimer(pil_img, forecast_layout):
    """Add disclaimer text. From common.py lines 2031-2045"""
    if forecast_layout in ("pwards", "monwatch"):
        return pil_img
    try:
        draw = ImageDraw.Draw(pil_img)
        iw, ih = pil_img.size
        disclaimer_text = ("THIS IS AN EXPERIMENTAL FORECAST MADE BY MONWATCH-UI - IT MAY BE DERIVED FROM OFFICIAL SOURCES OR BE A CUSTOM-MADE EXPERIMENTAL FORECAST.\nPLEASE EXERCISE CAUTION AND CONSULT OFFICIAL SOURCES FOR AUTHORITATIVE INFORMATION.")
        fs = max(7, int(ih * 0.011))
        try: fnt = ImageFont.truetype("consola.ttf", fs)
        except Exception: fnt = ImageFont.load_default()
        bb = draw.textbbox((0, 0), disclaimer_text, font=fnt)
        th = bb[3] - bb[1]
        draw.text((iw // 2, ih - th - 8), disclaimer_text, fill=(140, 140, 140), font=fnt, anchor='mb')
    except Exception: pass
    return pil_img


# ── Shared: get NHC storm data from tropycal ──
def _fetch_nhc_storm_data(storm_id, track_points, storm_name):
    """Fetch NHC storm data from tropycal. From common.py lines 1357-1401"""
    try:
        from tropycal import realtime as _tc
        _rt = _tc.Realtime()
        _storm = _rt.get_storm(storm_id)
        _td = _storm.to_dataframe()
        if _td is not None and len(_td) > 0:
            _pts = []
            for _, row in _td.iterrows():
                if pd.isna(row.get('lat')) or pd.isna(row.get('lon')): continue
                pt = {
                    "lon": float(row['lon']), "lat": float(row['lat']),
                    "datetime": row['time'].strftime("%Y-%m-%d %H:%M") if hasattr(row['time'], 'strftime') else str(row['time']),
                    "intensity": float(row['vmax']) if not pd.isna(row.get('vmax', None)) else None,
                    "intensity_category": str(row.get('type', '')) if not pd.isna(row.get('type', None)) else "",
                }
                if not pd.isna(row.get('mslp', None)): pt["mslp"] = float(row['mslp'])
                _pts.append(pt)
            if _pts:
                track_points = _pts
                storm_name = _storm.to_dict().get("name", storm_name)
        try:
            _fcst = _storm.get_forecast()
            if _fcst and 'track_points' in _fcst:
                for _fp in _fcst['track_points']:
                    if _fp.get('lat') is not None and _fp.get('lon') is not None:
                        _fp_dict = {
                            "lon": float(_fp['lon']), "lat": float(_fp['lat']),
                            "datetime": str(_fp.get('time', '')),
                            "intensity": float(_fp.get('vmax', 0)) if _fp.get('vmax') is not None else None,
                            "intensity_category": str(_fp.get('type', '')),
                        }
                        if not any(abs(t["lon"] - _fp_dict["lon"]) < 0.01 and abs(t["lat"] - _fp_dict["lat"]) < 0.01 for t in track_points):
                            track_points.append(_fp_dict)
        except Exception: pass
    except Exception: pass
    return track_points, storm_name


# ── Shared: fetch JMA model tracks ──
# ── Alias for combined labels (identical to _build_track_labels) ──
def _build_combined_labels(pts, forecast_layout, show_dt, show_wind,
                           time_fmt, utc_offset, wind_unit, issued_dtg):
    """Alias for _build_track_labels used in combined forecasts."""
    return _build_track_labels(pts, forecast_layout, show_dt, show_wind,
                               time_fmt, utc_offset, wind_unit, issued_dtg)


def _clean_storm_name(raw_name, source=""):
    """Clean storm name by stripping source suffixes, CWA artifacts, and year-in-parentheses."""
    import re as _re_cn
    name = raw_name.strip() if raw_name else ""
    for _sfx in [" JMA FORECAST", " JTWC FORECAST", " NHC FORECAST", " JMA", " JTWC", " NHC", " CWA"]:
        if name.upper().endswith(_sfx):
            name = name[:-_sfx.__len__()]
            break
    _src = source.upper() if source else ""
    if _src == "CWA" and name.upper().startswith("CWA "):
        name = name[4:].strip()
    if _src == "CWA" and name.upper().endswith(" CWA"):
        name = name[:-4].strip()
    name = _re_cn.sub(r'\s*\(\d{4}\)', '', name).strip()
    if "{" in name and "}" in name:
        name = name.split("{")[0].strip()
    return name


def _fetch_jma_model_tracks(ax, storm_id):
    """Fetch JMA model tracks from tropycal and plot on ax. From common.py lines 795-804"""
    try:
        from tropycal import realtime as _jma_rt
        _jma_data = _jma_rt(jtwc=True, jtwc_source="jtwc")
        _jma_storm = _jma_data.get_storm(storm_id)
        if _jma_storm is not None:
            _jma_storm.plot_models(forecast='latest', ax=ax, cartopy_proj=ccrs.PlateCarree())
    except Exception: pass
