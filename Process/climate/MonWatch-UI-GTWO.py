"""
MonWatch-UI-GTWO  --  NHC Graphical Tropical Weather Outlook Map Generator
MonWatch-UI dark-theme style (copied from Process/forecast/monwatch_handler.py).

Generates a single map with GTWO probability polygons/points,
PAR/TCAD/TCID boundaries, legend, disclaimer, and watermark.

Usage:
  python MonWatch-UI-GTWO.py \\
      --shp-dir /path/to/gtwo_shapefiles \\
      --output out.png \\
      [--settings settings.json]
"""
import sys, json, io, os, math
from pathlib import Path
from datetime import datetime, timezone

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
    import shapefile
    HAS_DEPS = True
except ImportError as e:
    HAS_DEPS = False
    _log(f"[gtwo] Missing dependency: {e}", file=sys.stderr)

_LAND = _OCEAN = _COASTLINE = _BORDERS = None
try:
    _LAND = cfeature.NaturalEarthFeature('physical', 'land', '50m',
        facecolor=cfeature.COLORS['land'], edgecolor='face')
    _OCEAN = cfeature.NaturalEarthFeature('physical', 'ocean', '50m',
        facecolor=cfeature.COLORS['water'], edgecolor='face')
    _COASTLINE = cfeature.NaturalEarthFeature('physical', 'coastline', '50m')
    _BORDERS = cfeature.NaturalEarthFeature('cultural', 'admin_0_boundary_lines_land', '50m',
        edgecolor='face', facecolor='none')
except Exception:
    try:
        _LAND = cfeature.LAND
        _OCEAN = cfeature.OCEAN
        _COASTLINE = cfeature.COASTLINE
        _BORDERS = cfeature.BORDERS
    except Exception:
        pass


def _gtwo_prob_color(prob_val):
    if prob_val is None:
        return (1.0, 0.65, 0.0, 0.25), (1.0, 0.55, 0.0, 0.8)
    if prob_val >= 60:
        return (1.0, 0.2, 0.2, 0.35), (1.0, 0.12, 0.12, 0.86)
    if prob_val >= 40:
        return (1.0, 0.65, 0.0, 0.3), (1.0, 0.55, 0.0, 0.8)
    return (1.0, 0.86, 0.2, 0.35), (1.0, 0.78, 0.12, 0.8)


def parse_gtwo_shapefiles(shp_dir):
    """Parse GTWO areas and points shapefiles from directory."""
    shp_dir = Path(shp_dir)
    polygons = []
    points = []

    for f in sorted(shp_dir.glob("*areas*.shp")):
        try:
            sf = shapefile.Reader(str(f))
            fields = [ff[0] for ff in sf.fields if ff[0] not in ('DeletionFlag',)]
            prob_field = None
            for candidate in ('PROB', 'PROBABILITY', 'PCT', 'PERCENT', 'RISK', 'DN', 'VALUE', 'CAT', 'CATEGORY', 'LABEL', 'TEXT'):
                if candidate in fields:
                    prob_field = candidate
                    break
            records = sf.records()
            has_records = len(records) > 0 and len(records) == len(sf.shapes())
            for idx, shape in enumerate(sf.shapes()):
                prob_val = None
                if has_records and prob_field:
                    raw = records[idx].as_dict() if hasattr(records[idx], 'as_dict') else None
                    if raw is None:
                        try:
                            raw = dict(zip(fields, list(records[idx])))
                        except Exception:
                            raw = {}
                    if raw:
                        val = raw.get(prob_field, raw.get(prob_field.upper(), raw.get(prob_field.lower())))
                        if val is not None:
                            try:
                                val_s = str(val).replace('%', '').replace(' ', '').strip()
                                prob_val = int(float(val_s))
                                if prob_val <= 1:
                                    prob_val = int(prob_val * 100)
                            except Exception:
                                try:
                                    cat_map = {'LOW': 10, 'MEDIUM': 50, 'HIGH': 90, 'MED': 50, 'ELEVATED': 40, 'CRITICAL': 70, 'EXTREME': 95}
                                    prob_val = cat_map.get(val_s.upper())
                                except Exception:
                                    pass
                rings = []
                pts = shape.points
                parts = shape.parts.tolist() if hasattr(shape.parts, 'tolist') else list(shape.parts)
                parts.append(len(pts))
                for i in range(len(parts) - 1):
                    ring = pts[parts[i]:parts[i+1]]
                    if len(ring) >= 3:
                        rings.append(ring)
                if rings:
                    polygons.append({"rings": rings, "prob": prob_val})
            sf.close()
        except Exception as e:
            _log(f"[gtwo] Error parsing areas shapefile {f}: {e}", file=sys.stderr)

    for f in sorted(shp_dir.glob("*points*.shp")):
        try:
            sf = shapefile.Reader(str(f))
            fields = [ff[0] for ff in sf.fields if ff[0] not in ('DeletionFlag',)]
            prob_field = None
            for candidate in ('PROB', 'PROBABILITY', 'PCT', 'PERCENT', 'RISK', 'DN', 'VALUE', 'CAT', 'CATEGORY', 'LABEL', 'TEXT'):
                if candidate in fields:
                    prob_field = candidate
                    break
            records = sf.records()
            has_records = len(records) > 0 and len(records) == len(sf.shapes())
            for idx, shape in enumerate(sf.shapes()):
                prob_val = None
                if has_records and prob_field:
                    raw = records[idx].as_dict() if hasattr(records[idx], 'as_dict') else None
                    if raw is None:
                        try:
                            raw = dict(zip(fields, list(records[idx])))
                        except Exception:
                            raw = {}
                    if raw:
                        val = raw.get(prob_field, raw.get(prob_field.upper(), raw.get(prob_field.lower())))
                        if val is not None:
                            try:
                                val_s = str(val).replace('%', '').replace(' ', '').strip()
                                prob_val = int(float(val_s))
                                if prob_val <= 1:
                                    prob_val = int(prob_val * 100)
                            except Exception:
                                try:
                                    cat_map = {'LOW': 10, 'MEDIUM': 50, 'HIGH': 90, 'MED': 50, 'ELEVATED': 40, 'CRITICAL': 70, 'EXTREME': 95}
                                    prob_val = cat_map.get(val_s.upper())
                                except Exception:
                                    pass
                pt = shape.points[0]
                lon = pt[0] - 360 if pt[0] > 180 else pt[0]
                lat = pt[1]
                points.append({"lon": lon, "lat": lat, "prob": prob_val})
            sf.close()
        except Exception as e:
            _log(f"[gtwo] Error parsing points shapefile {f}: {e}", file=sys.stderr)

    return polygons, points


def render_gtwo_map(shp_dir, output_path, dpi=150):
    if not HAS_DEPS:
        _log("[gtwo] Missing dependencies", file=sys.stderr)
        return None

    polygons, points = parse_gtwo_shapefiles(shp_dir)

    if not polygons and not points:
        _log("[gtwo] No GTWO data found", file=sys.stderr)
        return None

    dpi_scale = dpi / 150.0

    fig = plt.figure(figsize=(20, 12), dpi=dpi, facecolor='#3b4b5b')
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree(central_longitude=180))
    ax.set_extent([0, 359, -60, 60], crs=ccrs.PlateCarree())

    fig.patch.set_facecolor('#3b4b5b')
    ax.set_facecolor('#3b4b5b')
    if _OCEAN is not None:
        ax.add_feature(_OCEAN, facecolor='#3b4b5b', alpha=1.0, zorder=0)
    if _LAND is not None:
        ax.add_feature(_LAND, edgecolor='#03fcfc', linewidth=0.5, alpha=1, facecolor='#607d8b', zorder=1)
    if _BORDERS is not None:
        ax.add_feature(_BORDERS, edgecolor='#3b4b5b', linewidth=1, linestyle=':', zorder=2)
    try:
        ax.add_feature(cfeature.STATES.with_scale('10m'), linewidth=0.5, edgecolor='#2a3a3a', alpha=0.6, zorder=2)
    except Exception:
        pass

    gl = ax.gridlines(draw_labels=True, dms=True, x_inline=False, y_inline=False,
                      linewidth=1.5, color='#666666', alpha=0.7, linestyle='--')
    gl.top_labels = True; gl.right_labels = True; gl.left_labels = True; gl.bottom_labels = True
    gl.xlabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', 'weight': 'bold'}
    gl.ylabel_style = {'size': round(8 * dpi_scale, 1), 'color': '#ffffff', 'rotation': 90, 'weight': 'bold'}
    gl.xformatter = LONGITUDE_FORMATTER; gl.yformatter = LATITUDE_FORMATTER
    gl.xpadding = 2; gl.ypadding = 2

    # -- Domain boundaries: PAR, TCAD, TCID --
    par_pts = [(115.0, 5.0), (115.0, 15.0), (120.0, 21.0), (120.0, 25.0), (135.0, 25.0), (135.0, 5.0)]
    par_lons = [p[0] for p in par_pts]; par_lats = [p[1] for p in par_pts]
    ax.plot(par_lons + par_lons[:1], par_lats + par_lats[:1],
            color="#FF3333", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=3)

    tcad_pts = [(114.0, 4.0), (114.0, 27.0), (145.0, 27.0), (145.0, 4.0)]
    tcad_lons = [p[0] for p in tcad_pts]; tcad_lats = [p[1] for p in tcad_pts]
    ax.plot(tcad_lons + tcad_lons[:1], tcad_lats + tcad_lats[:1],
            color="#FF6B6B", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=3)

    tcid_pts = [(110.0, 0.0), (110.0, 27.0), (155.0, 27.0), (155.0, 0.0)]
    tcid_lons = [p[0] for p in tcid_pts]; tcid_lats = [p[1] for p in tcid_pts]
    ax.plot(tcid_lons + tcid_lons[:1], tcid_lats + tcid_lats[:1],
            color="#4ECDC4", linewidth=1.5, linestyle='--',
            transform=ccrs.PlateCarree(), zorder=3)

    from matplotlib.patches import Polygon as MplPolygon
    from matplotlib.collections import PatchCollection
    import matplotlib.patches as mpatches

    # -- Render polygon areas --
    for poly_data in polygons:
        fill_rgba, edge_rgba = _gtwo_prob_color(poly_data["prob"])
        patches = []
        for ring in poly_data["rings"]:
            pts = np.array(ring)
            patches.append(MplPolygon(pts, closed=True))
        if patches:
            pc = PatchCollection(patches, facecolor=fill_rgba, edgecolor=edge_rgba,
                                 linewidth=0.8, transform=ccrs.PlateCarree(), zorder=5)
            ax.add_collection(pc)

    # -- Render point dots --
    for pt_data in points:
        fill_rgba, edge_rgba = _gtwo_prob_color(pt_data["prob"])
        ax.plot(pt_data["lon"], pt_data["lat"], 'o',
                color=fill_rgba, markersize=6,
                markeredgecolor=edge_rgba, markeredgewidth=1.0,
                transform=ccrs.PlateCarree(), zorder=6)

    # -- Legend --
    prob_levels = [
        (None, "Outlook Area", "#FFA500"),
        (10, "Low (< 40%)", "#FFC800"),
        (40, "Medium (40-60%)", "#FFA500"),
        (60, "High (> 60%)", "#FF3232"),
    ]
    legend_handles = []
    for pval, plabel, pcolor in prob_levels:
        if pval is None:
            has_any = any(pd["prob"] is None for pd in polygons) or any(pt["prob"] is None for pt in points)
        else:
            if pval >= 60:
                has_any = any(pd["prob"] is not None and pd["prob"] >= 60 for pd in polygons) or any(pt["prob"] is not None and pt["prob"] >= 60 for pt in points)
            elif pval >= 40:
                has_any = any(pd["prob"] is not None and 40 <= pd["prob"] < 60 for pd in polygons) or any(pt["prob"] is not None and 40 <= pt["prob"] < 60 for pt in points)
            else:
                has_any = any(pd["prob"] is not None and pd["prob"] < 40 for pd in polygons) or any(pt["prob"] is not None and pt["prob"] < 40 for pt in points)
        if has_any:
            legend_handles.append(mpatches.Patch(color=pcolor, label=plabel))

    domain_legend = [
        mpatches.Patch(color='none', edgecolor="#FF3333", linestyle='--', linewidth=1.5, label='PAR'),
        mpatches.Patch(color='none', edgecolor="#FF6B6B", linestyle='--', linewidth=1.5, label='TCAD'),
        mpatches.Patch(color='none', edgecolor="#4ECDC4", linestyle='--', linewidth=1.5, label='TCID'),
    ]
    all_legend = domain_legend + legend_handles
    if all_legend:
        leg = ax.legend(handles=all_legend, loc='lower left', framealpha=0.85,
                        fontsize=round(8 * dpi_scale),
                        facecolor='#000000', edgecolor='gray', labelcolor='#ffffff')
        leg.set_zorder(20)

    fig.subplots_adjust(bottom=0.05, left=0.02, right=0.98, top=0.95)
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                facecolor=fig.get_facecolor(), edgecolor='none')
    buf.seek(0)
    pil_img = Image.open(buf).convert("RGBA")
    buf.close()
    plt.close(fig)

    iw, ih = pil_img.size
    draw = ImageDraw.Draw(pil_img)

    # ── Title box (MonWatch-UI triple-border style) ──
    row1 = "NHC Graphical Tropical Weather Outlook"
    row2 = "Tropical Cyclone Formation Probability"

    tfs = round(32 * dpi_scale); sfs = round(20 * dpi_scale)
    title_font = sub_font = None
    for _ in range(10):
        try: tf = ImageFont.truetype("segoeuib.ttf", tfs)
        except Exception:
            try: tf = ImageFont.truetype("arialbd.ttf", tfs)
            except Exception: tf = ImageFont.load_default()
        try: sf = ImageFont.truetype("segoeui.ttf", sfs)
        except Exception:
            try: sf = ImageFont.truetype("arial.ttf", sfs)
            except Exception: sf = ImageFont.load_default()
        b1 = draw.textbbox((0, 0), row1, font=tf)
        b2 = draw.textbbox((0, 0), row2, font=sf)
        tw = max(b1[2] - b1[0], b2[2] - b2[0])
        th = (b1[3] - b1[1]) + (b2[3] - b2[1]) + 1
        if tw < iw * 0.85 and tfs > 10 and sfs > 8: title_font, sub_font = tf, sf; break
        tfs -= 2; sfs = max(8, tfs - 4)
    if title_font is None: title_font, sub_font = tf, sf

    pad_x, pad_y = 40, 20
    box_w = tw + pad_x * 2; box_h = th + pad_y * 2
    box_x = (iw - box_w) // 2; box_y = 50
    draw.rectangle([box_x, box_y, box_x + box_w, box_y + box_h], outline='black', width=3)
    draw.rectangle([box_x + 4, box_y + 4, box_x + box_w - 4, box_y + box_h - 4], outline='black', width=2)
    draw.rectangle([box_x + 7, box_y + 7, box_x + box_w - 7, box_y + box_h - 7], outline='black', width=1)
    draw.rectangle([box_x + 8, box_y + 8, box_x + box_w - 8, box_y + box_h - 8], fill='#607d8b')
    cx = box_x + box_w // 2
    cy1 = box_y + pad_y + (b1[3] - b1[1]) // 2 - 4
    cy2 = cy1 + (b1[3] - b1[1]) // 2 + 10 + (b2[3] - b2[1]) // 2
    draw.text((cx, cy1), row1, fill='white', font=title_font, anchor='mm')
    draw.text((cx, cy2), row2, fill='white', font=sub_font, anchor='mm')

    # ── MonWatch watermark (top-left) ──
    try:
        _script_dir = Path(__file__).resolve().parent.parent.parent
        _wmark_path = _script_dir / "public" / "images" / "MonWatch-wmark.png"
        if _wmark_path.exists():
            _mw_logo = Image.open(str(_wmark_path)).convert("RGBA")
            _mw_h = (int(ih * 0.05) + 30) * 3
            _mw_w = int(_mw_logo.width * _mw_h / _mw_logo.height)
            _mw_logo = _mw_logo.resize((_mw_w, _mw_h), Image.LANCZOS)
            pil_img.paste(_mw_logo, (int(iw * 0.01), 0), _mw_logo)
    except Exception:
        pass

    # ── Disclaimer ──
    dlines = [
        "THIS IS AN EXPERIMENTAL TROPICAL WEATHER OUTLOOK MAP GENERATED BY MONWATCH-UI - IT IS DERIVED FROM NHC DATA",
        "AND MAY CONTAIN EXPERIMENTAL PRODUCTS. PLEASE EXERCISE CAUTION AND CONSULT OFFICIAL SOURCES",
        "AT NHC.NOAA.GOV FOR AUTHORITATIVE TROPICAL WEATHER FORECAST INFORMATION.",
    ]
    dfs = max(14, int(ih * 0.018))
    try: dfnt = ImageFont.truetype("segoeuib.ttf", dfs)
    except Exception:
        try: dfnt = ImageFont.truetype("arialbd.ttf", dfs)
        except Exception: dfnt = ImageFont.load_default()
    lh = draw.textbbox((0, 0), dlines[0], font=dfnt)[3] - draw.textbbox((0, 0), dlines[0], font=dfnt)[1]
    dth = lh * len(dlines) + 4
    by = ih - dth - 40
    for li, line in enumerate(dlines):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                draw.text((iw // 2 + dx, by + li * lh + dy), line, fill='black', font=dfnt, anchor='mm')
        draw.text((iw // 2, by + li * lh), line, fill='white', font=dfnt, anchor='mm')

    # ── Footer bar ──
    footer_h = 36
    try: fnt = ImageFont.truetype("consola.ttf", 12)
    except Exception: fnt = ImageFont.load_default()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer_img = Image.new("RGBA", (iw, footer_h), (0, 0, 0, 200))
    fdraw = ImageDraw.Draw(footer_img)
    left_text = f"NHC Graphical Tropical Weather Outlook  |  {now_str}"
    bbox = fdraw.textbbox((0, 0), left_text, font=fnt)
    th = bbox[3] - bbox[1]
    fdraw.text((8, (footer_h - th) // 2), left_text, fill='white', font=fnt)
    bbox2 = fdraw.textbbox((0, 0), "MonWatch-UI", font=fnt)
    bw = bbox2[2] - bbox2[0]
    fdraw.text((iw - bw - 8, (footer_h - th) // 2), "MonWatch-UI", fill='white', font=fnt)
    combined = Image.new("RGBA", (iw, ih + footer_h), (0, 0, 0, 0))
    combined.paste(pil_img, (0, 0), pil_img)
    combined.paste(footer_img, (0, ih), footer_img)

    if output_path:
        combined.convert("RGB").save(output_path)
        _log(f"[gtwo] Saved: {output_path}", file=sys.stderr)
        return output_path
    return np.array(combined)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate NHC GTWO map (MonWatch-UI style)")
    parser.add_argument("--shp-dir", required=True, help="Directory containing GTWO shapefiles")
    parser.add_argument("--output", required=True, help="Output PNG file path")
    parser.add_argument("--settings", help="Path to settings.json")
    args = parser.parse_args()

    if not HAS_DEPS:
        _log("ERROR: Missing dependencies (cartopy, matplotlib, PIL, shapefile)", file=sys.stderr)
        sys.exit(1)

    out = render_gtwo_map(args.shp_dir, args.output)
    if out:
        _log(f"DONE:{args.output}")
    else:
        _log("ERROR: Failed to render GTWO map", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
