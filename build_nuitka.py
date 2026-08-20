#!/usr/bin/env python3
# build_nuitka.py — Nuitka standalone build for MonWatch-UI Cyclone V3.0.5.1
# Run from: MonWatch-UI Cyclone V3.0.5.1/ (project root)

import subprocess
import sys
import os
import time
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
ENTRY_POINT = PROJECT_ROOT / "src" / "UI.py"
OUTPUT_DIR = PROJECT_ROOT / "dist" / "MonWatch-UI"
ICON = PROJECT_ROOT / "public" / "images" / "Splash.ico"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"build_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# Packages that need explicit inclusion
INCLUDE_PACKAGES = [
    "src",
    "Process",
    "PySide6",
    "numpy",
    "scipy",
    "xarray",
    "netCDF4",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageFont",
    "tifffile",
    "rasterio",
    "satpy",
    "sataid",
    "sounderpy",
    "metpy",
    "pyproj",
    "cartopy",
    "shapely",
    "shapefile",
    "boto3",
    "botocore",
    "requests",
    "matplotlib",
]

# Data directories to bundle
INCLUDE_DATA_DIRS = [
    ("public/images", "public/images"),
    ("public/sounds", "public/sounds"),
    ("cache", "cache"),
]

# Data files to bundle (single files, not directories)
INCLUDE_DATA_FILES = [
    ("public/update_tracker.json", "public/update_tracker.json"),
]

# Plugins to enable (only pyside6 is needed; numpy deprecated, pkg-resources/multiprocessing auto-enabled)
ENABLE_PLUGINS = [
    "pyside6",
]

# Packages to NOT follow — prevents bloat from test modules and unnecessary packages
NOFOLLOW_IMPORTS = [
    # Heavy packages not needed at runtime
    "IPython",
    "OpenGL",
    "PySide6.scripts",
    "fontTools",
    "PIL.ImageShow",
    "PIL.ImageGrab",
    # Numba / CUDA
    "cupy",
    "numba",
    # psutil / imageio
    "psutil",
    "imageio",
    # numpy test modules
    "numpy.tests",
    "numpy._core.tests",
    "numpy.lib.tests",
    "numpy.fft.tests",
    "numpy.polynomial.tests",
    "numpy.random.tests",
    "numpy.typing.tests",
    "numpy.ma.tests",
    "numpy.matrixlib.tests",
    "numpy.linalg.tests",
    "numpy.conftest",
    # scipy test modules (every subpackage)
    "scipy.tests",
    "scipy.conftest",
    "scipy._lib.tests",
    "scipy.cluster.tests",
    "scipy.constants.tests",
    "scipy.datasets.tests",
    "scipy.differentiate.tests",
    "scipy.fft.tests",
    "scipy.fftpack.tests",
    "scipy.integrate.tests",
    "scipy.interpolate.tests",
    "scipy.io.tests",
    "scipy.linalg.tests",
    "scipy.ndimage.tests",
    "scipy.odr.tests",
    "scipy.optimize.tests",
    "scipy.signal.tests",
    "scipy.sparse.tests",
    "scipy.spatial.tests",
    "scipy.special.tests",
    "scipy.stats.tests",
    # matplotlib tests
    "matplotlib.tests",
    "matplotlib.testing",
    # xarray / pandas tests
    "xarray.tests",
    "pandas.tests",
    # PIL tests
    "PIL.ImageMath",
    # Qt conflicts (just in case)
    "PyQt5",
    "PyQt6",
]


def log(msg: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def build():
    log("=" * 60)
    log("Nuitka Build Started")
    log(f"Project: {PROJECT_ROOT.name}")
    log(f"Entry:   {ENTRY_POINT.relative_to(PROJECT_ROOT)}")
    log(f"Output:  {OUTPUT_DIR}")
    log(f"Icon:    {ICON.relative_to(PROJECT_ROOT)}")
    log(f"Log:     {LOG_FILE.relative_to(PROJECT_ROOT)}")
    log("=" * 60)

    if not ENTRY_POINT.exists():
        log(f"ERROR: Entry point not found: {ENTRY_POINT}")
        return 1
    if not ICON.exists():
        log(f"WARNING: Icon not found: {ICON}")

    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--windows-console-mode=disable",
        f"--windows-icon-from-ico={ICON}",
        "--product-name=MonWatch-UI Cyclone",
        "--product-version=3.0.5.1",
        "--company-name=PWARDS-weather",
        "--copyright=Copyright (C) 2025-2026 PWARDS-weather",
        f"--output-dir={OUTPUT_DIR.parent}",
        f"--output-filename=MonWatch-UI.exe",
        "--remove-output",
        "--assume-yes-for-downloads",
        "--lto=no",
        "--jobs=8",
        "--show-progress",
        "--show-memory",
    ]

    for pkg in INCLUDE_PACKAGES:
        cmd.append(f"--include-package={pkg}")

    for src, dst in INCLUDE_DATA_DIRS:
        src_path = PROJECT_ROOT / src
        if src_path.exists():
            cmd.append(f"--include-data-dir={src}={dst}")
            log(f"Including data dir: {src}")
        else:
            log(f"WARNING: Data dir not found: {src}")

    for src, dst in INCLUDE_DATA_FILES:
        src_path = PROJECT_ROOT / src
        if src_path.exists():
            cmd.append(f"--include-data-file={src}={dst}")
            log(f"Including data file: {src}")
        else:
            log(f"WARNING: Data file not found: {src}")

    for plugin in ENABLE_PLUGINS:
        cmd.append(f"--enable-plugin={plugin}")

    for pkg in NOFOLLOW_IMPORTS:
        cmd.append(f"--nofollow-import-to={pkg}")

    cmd.append(str(ENTRY_POINT))

    log("\nCommand:")
    log(" ".join(cmd))
    log("\nBuilding...\n")

    start_time = time.time()

    try:
        process = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        for line in process.stdout:
            line = line.rstrip()
            print(line)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {line}\n")

        process.wait()
        elapsed = time.time() - start_time

        if process.returncode == 0:
            log(f"\n{'='*60}")
            log(f"BUILD SUCCESSFUL in {elapsed:.1f}s")
            log(f"Output: {OUTPUT_DIR / 'MonWatch-UI.exe'}")
            log(f"{'='*60}")
        else:
            log(f"\n{'='*60}")
            log(f"BUILD FAILED (exit code {process.returncode}) after {elapsed:.1f}s")
            log(f"See log: {LOG_FILE}")
            log(f"{'='*60}")

        return process.returncode

    except KeyboardInterrupt:
        log("\nBuild interrupted by user")
        return 130
    except Exception as e:
        log(f"\nBuild error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(build())