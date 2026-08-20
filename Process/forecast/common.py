"""
common.py — Main controller for forecast map generation.

Usage:
    python common.py --input data.json --output output.png --layout pagasa [options]

This module reads input data, determines the forecast layout, and dispatches
to the appropriate *_handler module for rendering.
"""

import sys
import json
import importlib
from pathlib import Path

# ── Ensure package imports work when run as a script ──
_script_path = Path(__file__).resolve()
_pkg_root = _script_path.parent.parent.parent  # project root (3 levels up)
if str(_pkg_root) not in sys.path:
    sys.path.insert(0, str(_pkg_root))
_pkg_parent = _script_path.parent.parent  # Process/
if str(_pkg_parent) not in sys.path:
    sys.path.insert(0, str(_pkg_parent))

# Now absolute imports work
from Process.forecast.utils import HAS_DEPS

# Map layout names to handler modules
HANDLER_MAP = {
    "pagasa": "Process.forecast.pagasa_handler",
    "nhc":    "Process.forecast.nhc_handler",
    "jma":    "Process.forecast.jma_handler",
    "jtwc":   "Process.forecast.jtwc_handler",
    "monwatch":"Process.forecast.monwatch_handler",
    "develope":"Process.forecast.develope_handler",
    "default":"Process.forecast.default_handler",
}

def _import_handler(layout):
    mod_name = HANDLER_MAP.get(layout, "Process.forecast.default_handler")
    return importlib.import_module(mod_name)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate a forecast map for a tropical cyclone.")
    parser.add_argument("--storm_id", help="Storm identifier (e.g. al082025)")
    parser.add_argument("--input", required=True, help="JSON file with storm data")
    parser.add_argument("--output", required=True, help="Output PNG file path")
    parser.add_argument("--light", action="store_true", help="Use light theme with white background")
    parser.add_argument("--settings", help="Path to settings.json for forecast preferences")
    parser.add_argument("--logo", help="Path to logo image for footer")
    parser.add_argument("--layout", default="monwatch",
                        help="Forecast layout (default, pagasa, jma, jtwc, monwatch, develope, nhc)")
    parser.add_argument("--algorithm", default="polar",
                        choices=["polar", "zigzag", "bezier", "cone", "smart_bezier",
                                 "anticlima", "anticlima_v2", "greedy", "offset", "force", "anneal", "milp",
                                 "railway_bezier", "8direction", "staggered_perp", "auto"],
                        help="Label placement algorithm")
    parser.add_argument("--cone", default="smooth", choices=["smooth", "union"],
                        help="Cone construction method")
    parser.add_argument("--margin", type=float, default=8.0,
                        help="Map extent margin in degrees around track")
    parser.add_argument("--combined", action="store_true",
                        help="Input JSON contains combined_tracks array")
    args = parser.parse_args()

    # Check basic deps
    if not HAS_DEPS:
        print("ERROR: Missing dependencies", file=sys.stderr)
        sys.exit(1)

    # Read input JSON
    with open(args.input) as f:
        data = json.load(f)

    # Read settings
    settings = {}
    if args.settings:
        try:
            with open(args.settings) as f:
                settings = json.load(f)
        except Exception:
            pass

    # Merge CLI overrides into settings
    fcst_prefs = settings.setdefault("forecast_preferences", {})
    if args.layout:
        fcst_prefs["forecast_layout"] = args.layout
    if args.cone:
        fcst_prefs["cone_method"] = args.cone
    if args.algorithm:
        fcst_prefs["algorithm"] = args.algorithm

    layout = fcst_prefs.get("forecast_layout", "default").lower()
    # Migrate old "default" value to "monwatch" (new application default)
    if layout == "default":
        layout = "monwatch"
        fcst_prefs["forecast_layout"] = "monwatch"

    # Import the correct handler module
    try:
        handler = _import_handler(layout)
    except ImportError:
        print(f"WARNING: No handler for layout '{layout}', falling back to default", file=sys.stderr)
        handler = _import_handler("default")

    logo_path = Path(args.logo) if args.logo else None

    if args.combined or "combined_tracks" in data:
        all_tracks = data.get("combined_tracks", [])
        if not all_tracks:
            print("ERROR: No combined tracks", file=sys.stderr)
            sys.exit(1)
        custom_title = data.get("custom_title", "")
        resolution = data.get("resolution", "110m")
        show_labels = data.get("show_labels", True)
        fcst_prefs["show_labels"] = show_labels
        print(f"Generating combined forecast for {len(all_tracks)} tracks ({layout})...", file=sys.stderr)
        out_path = handler.make_combined_forecast(
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
        # Pre-parsed JTWC KMZ data (NHC-style): danger swath + wind radii polygons
        danger_swath = data.get("danger_swath", [])
        wind_radii_polygons = data.get("wind_radii_polygons", {})
        best_track_points = data.get("best_track_points", [])
        best_track_line = data.get("best_track_line", [])
        issued_dtg = data.get("issued_dtg")
        basin = data.get("basin", "")
        source = data.get("source", "")
        title_info = data.get("title_info", {})
        if not track_points:
            print("ERROR: No track points", file=sys.stderr)
            sys.exit(1)
        print(f"Generating forecast for {storm_name} ({args.storm_id}) ({layout})...", file=sys.stderr)
        out_path = handler.make_forecast(
            track_points, probability_circles, storm_name, args.storm_id or "",
            kmz_cone=kmz_cone, kmz_track=kmz_track, kmz_wind_initial=kmz_wind_initial,
            danger_swath=danger_swath, wind_radii_polygons=wind_radii_polygons,
            best_track_points=best_track_points, best_track_line=best_track_line,
            light=args.light, settings=settings, logo_path=logo_path,
            filename=args.output, algorithm=args.algorithm, margin=args.margin,
            issued_dtg=issued_dtg,
            basin=basin,
            source=source,
            title_info=title_info,
        )

    if out_path:
        print(f"DONE:{args.output}")
    else:
        print("ERROR: Failed to generate forecast", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
