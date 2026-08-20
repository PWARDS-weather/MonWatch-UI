import sys, json, os, argparse
import numpy as np

sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), '..', '..')))

def _report_progress(pct):
    print(f"PROGRESS:{pct}", flush=True)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['img', 'array'], required=True)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--min-lon', type=float, required=True)
    parser.add_argument('--max-lon', type=float, required=True)
    parser.add_argument('--min-lat', type=float, required=True)
    parser.add_argument('--max-lat', type=float, required=True)
    parser.add_argument('--src-crs', type=str, default=None)
    parser.add_argument('--geotransform', type=str, default=None)
    parser.add_argument('--min-px', type=float, default=None)
    parser.add_argument('--max-px', type=float, default=None)
    parser.add_argument('--min-py', type=float, default=None)
    parser.add_argument('--max-py', type=float, default=None)
    parser.add_argument('--grid', type=int, default=0)
    parser.add_argument('--coast', type=int, default=1)
    parser.add_argument('--labels', type=int, default=0)
    parser.add_argument('--grid-step', type=float, default=10)
    parser.add_argument('--grid-color', type=str, default='#00feed')
    parser.add_argument('--grid-opacity', type=int, default=160)
    parser.add_argument('--grid-width', type=float, default=1)
    parser.add_argument('--grid-style', type=str, default='dotted')
    parser.add_argument('--coast-color', type=str, default='#00ff00')
    parser.add_argument('--coast-opacity', type=int, default=200)
    parser.add_argument('--coast-width', type=float, default=1)
    parser.add_argument('--coast-style', type=str, default='solid')
    parser.add_argument('--aors', type=str, default=None)
    args = parser.parse_args()

    aors = json.loads(args.aors) if args.aors else None

    from pyproj import CRS
    from rasterio.transform import Affine

    from src.workers.flat_gen import render_flat, render_flat_from_array

    _report_progress(5)

    if args.mode == 'img':
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QIODevice

        img = QImage(args.input)
        if img.isNull():
            print("ERROR: could not load input image", file=sys.stderr)
            sys.exit(1)
        _report_progress(10)

        src_crs = None
        gt = None
        if args.src_crs:
            src_crs = CRS.from_dict(json.loads(args.src_crs))
        if args.geotransform:
            gt_arr = json.loads(args.geotransform)
            gt = Affine(*gt_arr)

        result = render_flat(
            img, args.min_lon, args.max_lon, args.min_lat, args.max_lat,
            src_crs, gt,
            args.min_px, args.max_px, args.min_py, args.max_py,
            grid_enabled=bool(args.grid), coast_enabled=bool(args.coast),
            grid_step=args.grid_step, grid_color=args.grid_color,
            grid_opacity=args.grid_opacity, grid_width=args.grid_width,
            grid_pattern=args.grid_style,
            coast_color=args.coast_color, coast_opacity=args.coast_opacity,
            coast_width=args.coast_width, coast_pattern=args.coast_style,
            labels=bool(args.labels),
            aors=aors
        )
        _report_progress(90)
        result.save(args.output)
        _report_progress(100)

    elif args.mode == 'array':
        data = np.load(args.input)
        _report_progress(10)

        result = render_flat_from_array(
            data, args.min_lon, args.max_lon, args.min_lat, args.max_lat,
            grid_enabled=bool(args.grid), coast_enabled=bool(args.coast),
            grid_step=args.grid_step, grid_color=args.grid_color,
            grid_opacity=args.grid_opacity, grid_width=args.grid_width,
            grid_pattern=args.grid_style,
            coast_color=args.coast_color, coast_opacity=args.coast_opacity,
            coast_width=args.coast_width, coast_pattern=args.coast_style,
            labels=bool(args.labels),
            aors=aors
        )
        _report_progress(90)
        result.save(args.output)
        _report_progress(100)

if __name__ == '__main__':
    main()
