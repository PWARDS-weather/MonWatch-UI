#!/bin/bash
set -e
cd "$(dirname "$0")"
OUTDIR="dist/MonWatch/Linux"
echo "============================================"
echo " Building MonWatch with Nuitka for Linux"
echo "============================================"
echo ""

# Install Nuitka and dependencies
pip install -q nuitka zstandard 2>/dev/null || true

echo "[*] Running Nuitka..."
python3 -m nuitka \
    --standalone \
    --assume-yes-for-downloads \
    --plugin-enable=pyside6 \
    --include-package=rasterio \
    --include-package-data=rasterio \
    --include-package=satpy \
    --include-package-data=satpy \
    --include-package=pyproj \
    --include-package-data=pyproj \
    --include-package=shapely \
    --include-package-data=shapely \
    --include-package=matplotlib \
    --include-package-data=matplotlib \
    --include-package=cartopy \
    --include-package-data=cartopy \
    --include-package=tifffile \
    --include-package-data=tifffile \
    --include-package=boto3 \
    --include-package-data=boto3 \
    --include-package=botocore \
    --include-package-data=botocore \
    --include-package=xarray \
    --include-package-data=xarray \
    --include-package=scipy \
    --include-package-data=scipy \
    --include-package=netCDF4 \
    --include-package-data=netCDF4 \
    --include-package=sataid \
    --include-package-data=sataid \
    --include-package=sounderpy \
    --include-package-data=sounderpy \
    --include-package=metpy \
    --include-package-data=metpy \
    --include-package=shapefile \
    --include-package=cupy \
    --include-package-data=cupy \
    --include-package=numba \
    --include-package-data=numba \
    --include-package=certifi \
    --include-package-data=certifi \
    --include-package=urllib3 \
    --include-package-data=urllib3 \
    --include-data-dir=public=public \
    --include-data-dir=data=data \
    --include-data-dir=Process=Process \
    --output-dir="$OUTDIR" \
    --output-filename=MonWatch \
    --remove-output \
    src/UI.py

# Nuitka creates a .dist folder for standalone
# Move contents to OUTDIR to match previous structure
if [ -d "$OUTDIR/launcher.dist" ]; then
    mv "$OUTDIR/launcher.dist" "$OUTDIR/MonWatch_dist"
    # If the user wants the binary at the root of OUTDIR
    # we can move it, but standalone usually keeps everything in one folder.
fi

echo "[*] Copying shortcut scripts..."
cp scripts/setup.sh "$OUTDIR/" 2>/dev/null || true
cp scripts/runner.sh "$OUTDIR/" 2>/dev/null || true
chmod +x "$OUTDIR/runner.sh" 2>/dev/null || true

echo ""
echo "[+] Build complete: $OUTDIR/"
echo ""
