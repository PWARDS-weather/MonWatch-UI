"""
CLIMA MAKER  –  CPC Climate & Weather Overlay Renderer
Generates probability-overlay PNG maps using cartopy & matplotlib.
Usage:  python clima_maker.py --code TC --week 2 --output out.png
"""
import sys, json, io, os, math, zipfile, tempfile, re
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
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import requests
    import shapefile
    HAS_DEPS = True
except ImportError as e:
    HAS_DEPS = False
    _log(f"[clima] Missing dependency: {e}", file=sys.stderr)

try:
    _LAND = cfeature.NaturalEarthFeature('physical', 'land', '50m', facecolor=cfeature.COLORS['land'], edgecolor='face')
    _OCEAN = cfeature.NaturalEarthFeature('physical', 'ocean', '50m', facecolor=cfeature.COLORS['water'], edgecolor='face')
    _COASTLINE = cfeature.NaturalEarthFeature('physical', 'coastline', '50m')
    _BORDERS = cfeature.NaturalEarthFeature('cultural', 'admin_0_boundary_lines_land', '50m', edgecolor='face', facecolor='none')
except Exception:
    try:
        _LAND = cfeature.LAND
        _OCEAN = cfeature.OCEAN
        _COASTLINE = cfeature.COASTLINE
        _BORDERS = cfeature.BORDERS
    except Exception:
        _LAND = _OCEAN = _COASTLINE = _BORDERS = None

HAZARD_META = {
    "TC":   {"label": "Tropical Cyclone Formation Probability", "default_color": "#FF6B6B"},
    "WET":  {"label": "Enhanced Precipitation Probability",      "default_color": "#4ECDC4"},
    "DRY":  {"label": "Suppressed Precipitation Probability",    "default_color": "#FFE66D"},
    "WARM": {"label": "Above Average Temperatures Probability",  "default_color": "#FF8C42"},
    "COLD": {"label": "Below Average Temperatures Probability",  "default_color": "#74B9FF"},
}

BASE_SHP_URL = "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/ghaz/shps/W{week}_{code}_latest.zip"
BASE_KMZ_URL = "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/ghaz/kmzs/W{week}_{code}.kmz"
BASE_KML_URL = "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/ghaz/kmzs/W{week}_{code}.kml"

CPC_INDEX_URL = "https://www.cpc.ncep.noaa.gov/products/precip/CWlink/ghaz/index.php"

def fetch_cpc_index_info():
    try:
        r = requests.get(CPC_INDEX_URL, timeout=15)
        r.raise_for_status()
        html = r.text
        last_updated = ""
        valid = ""
        m = re.search(r'Last Updated\s*[-\u2013]\s*(\d{2}/\d{2}/\d{2,4})', html)
        if m: last_updated = m.group(1)
        m = re.search(r'Valid\s*[-\u2013]\s*(\d{2}/\d{2}/\d{2,4})\s*[-\u2013]\s*(\d{2}/\d{2}/\d{2,4})', html)
        if m: valid = f"{m.group(1)} - {m.group(2)}"
        return {"last_updated": last_updated, "valid": valid}
    except Exception as e:
        _log(f"[clima] Failed to fetch CPC index: {e}", file=sys.stderr)
        return None

def download_climate_data(code, week, cache_dir):
    week_str = str(week)
    dest = Path(cache_dir) / f"{code}_W{week_str}"
    dest.mkdir(parents=True, exist_ok=True)
    shp_path = dest / f"W{week_str}_{code}.shp"
    if shp_path.exists():
        return str(shp_path)
    url = BASE_SHP_URL.format(week=week_str, code=code)
    _log(f"[clima] Downloading {url} ...", file=sys.stderr)
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
        tmp_path = tmp.name
        for chunk in r.iter_content(8192):
            tmp.write(chunk)
        tmp.close()
        with zipfile.ZipFile(tmp_path, 'r') as zf:
            zf.extractall(str(dest))
        os.unlink(tmp_path)
        for f in dest.glob("*.shp"):
            return str(f)
        _log(f"[clima] No .shp found in extracted zip for {code} W{week_str}", file=sys.stderr)
        return None
    except Exception as e:
        _log(f"[clima] Download failed: {e}", file=sys.stderr)
        return None

def parse_shapefile(shp_path):
    try:
        sf = shapefile.Reader(shp_path)
        shapes = []
        for shape in sf.shapes():
            points = shape.points
            parts = shape.parts.tolist() if hasattr(shape.parts, 'tolist') else list(shape.parts)
            parts.append(len(points))
            for i in range(len(parts) - 1):
                ring = points[parts[i]:parts[i+1]]
                if len(ring) >= 3:
                    shapes.append(ring)
        sf.close()
        return shapes
    except Exception as e:
        _log(f"[clima] Shapefile parse error: {e}", file=sys.stderr)
        return []

def render_climate_map(code, week, shp_path, output_path, color="#FF6B6B", light=True, dpi=150, settings=None):
    if not HAS_DEPS:
        _log("[clima] Missing dependencies", file=sys.stderr)
        return None
    meta = HAZARD_META.get(code, {"label": code, "default_color": color})
    label = meta["label"]
    polygons = parse_shapefile(shp_path)
    if not polygons:
        _log(f"[clima] No polygons found for {code} W{week}", file=sys.stderr)
        return None

    lons = [p[0] for poly in polygons for p in poly]
    lats = [p[1] for poly in polygons for p in poly]
    if not lons:
        return None

    fig = plt.figure(figsize=(16, 10), dpi=dpi, facecolor='white' if light else '#1E1E1E')
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=0))
    ax.set_extent([0, 359, -60, 60], crs=ccrs.PlateCarree())

    if light:
        ax.set_facecolor('#deecf4')
        if _LAND is not None: ax.add_feature(_LAND, facecolor='#e8e0d8', edgecolor='gray', linewidth=0.5)
        if _OCEAN is not None: ax.add_feature(_OCEAN, facecolor='#deecf4')
        if _BORDERS is not None: ax.add_feature(_BORDERS, edgecolor='#888', linewidth=0.3, linestyle=':')
        gl = ax.gridlines(draw_labels=True, dms=True, linewidth=0.5, color='gray', alpha=0.5, linestyle='--')
        gl.top_labels = gl.right_labels = False
        gl.xlabel_style = {'size': 9, 'color': 'black', 'weight': 'bold'}
        gl.ylabel_style = {'size': 9, 'color': 'black', 'rotation': 90, 'weight': 'bold'}
        title_color = 'black'
    else:
        ax.set_facecolor('#1a1a2e')
        if _LAND is not None: ax.add_feature(_LAND, edgecolor='gray', linewidth=0.5)
        if _OCEAN is not None: ax.add_feature(_OCEAN, facecolor='#0d1b2a', alpha=0.8)
        if _BORDERS is not None: ax.add_feature(_BORDERS, edgecolor='#556677', linewidth=0.3, linestyle=':')
        gl = ax.gridlines(draw_labels=True, dms=True, linewidth=0.5, color='#4a6a8a', alpha=0.5, linestyle='--')
        gl.top_labels = gl.right_labels = False
        gl.xlabel_style = {'size': 9, 'color': '#c0d0e0', 'weight': 'bold'}
        gl.ylabel_style = {'size': 9, 'color': '#c0d0e0', 'rotation': 90, 'weight': 'bold'}
        title_color = '#d0d0e0'

    from matplotlib.patches import Polygon as MplPolygon
    from matplotlib.collections import PatchCollection
    import matplotlib.patheffects as path_effects

    c = color
    try:
        from matplotlib.colors import to_rgba
        rgba = to_rgba(c)
    except Exception:
        rgba = (1, 0.42, 0.42, 0.35)
    fill_rgba = (rgba[0], rgba[1], rgba[2], 0.35)
    edge_rgba = (rgba[0], rgba[1], rgba[2], 0.8)

    patches = []
    for poly in polygons:
        pts = np.array(poly)
        patches.append(MplPolygon(pts, closed=True))

    pc = PatchCollection(patches, facecolor=fill_rgba, edgecolor=edge_rgba, linewidth=0.8, transform=ccrs.PlateCarree(), zorder=5)
    ax.add_collection(pc)

    week_label = f"Week {week}" if week in (2, 3) else f"W{week}"
    title_text = f"{label}  ({week_label})"
    ax.set_title(title_text, fontsize=13, color=title_color, fontweight='bold', pad=10)

    fig.subplots_adjust(bottom=0.05, left=0.02, right=0.98, top=0.95)
    fig.canvas.draw()
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight', facecolor='white' if light else '#1E1E1E', edgecolor='none')
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer_h = 36
    try:
        font = ImageFont.truetype("consola.ttf", 12)
    except Exception:
        font = ImageFont.load_default()
    mw, mh = pil_img.size
    footer_img = Image.new("RGBA", (mw, footer_h), (0, 0, 0, 255))
    draw = ImageDraw.Draw(footer_img)
    left_text = f"{label} ({code})  |  {week_label}  |  CPC NOAA  |  Generated {now_str}"
    bbox = draw.textbbox((0, 0), left_text, font=font)
    th = bbox[3] - bbox[1]
    draw.text((8, (footer_h - th) // 2), left_text, fill='white', font=font)
    bbox2 = draw.textbbox((0, 0), "MonWatch-UI", font=font)
    bw = bbox2[2] - bbox2[0]
    draw.text((mw - bw - 8, (footer_h - th) // 2), "MonWatch-UI", fill='white', font=font)
    combined = Image.new("RGBA", (mw, mh + footer_h), (0, 0, 0, 0))
    combined.paste(pil_img, (0, 0), pil_img)
    combined.paste(footer_img, (0, mh), footer_img)

    if output_path:
        combined.convert("RGB").save(output_path)
        _log(f"[clima] Saved: {output_path}", file=sys.stderr)
        return output_path
    return np.array(combined)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate CPC climate overlay map")
    parser.add_argument("--code", required=True, help="Hazard code: TC, WET, DRY, WARM, COLD")
    parser.add_argument("--week", required=True, help="Week number: 2 or 3")
    parser.add_argument("--output", required=True, help="Output PNG file path")
    parser.add_argument("--color", default=None, help="Fill color hex (e.g. #FF6B6B)")
    parser.add_argument("--light", action="store_true", help="Light theme")
    parser.add_argument("--settings", help="Path to settings.json")
    parser.add_argument("--cache", default=None, help="Cache directory for shapefiles")
    args = parser.parse_args()

    if not HAS_DEPS:
        _log("ERROR: Missing dependencies (cartopy, matplotlib, PIL, shapefile, requests)", file=sys.stderr)
        sys.exit(1)

    code = args.code.upper()
    week = args.week
    meta = HAZARD_META.get(code)
    if not meta:
        _log(f"ERROR: Unknown hazard code: {code}", file=sys.stderr)
        sys.exit(1)

    color = args.color or meta["default_color"]
    settings = {}
    if args.settings:
        try:
            with open(args.settings) as f:
                settings = json.load(f)
        except Exception:
            pass

    cache_dir = args.cache or str(Path(tempfile.gettempdir()) / "monwatch_climate")
    shp_path = download_climate_data(code, week, cache_dir)
    if not shp_path:
        _log(f"ERROR: Failed to get shapefile for {code} W{week}", file=sys.stderr)
        sys.exit(1)

    out = render_climate_map(code, week, shp_path, args.output, color=color, light=args.light, settings=settings)
    if out:
        _log(f"DONE:{args.output}")
    else:
        _log("ERROR: Failed to render climate map", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
