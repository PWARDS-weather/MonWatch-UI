#!/bin/bash
set -e
cd "$(dirname "$0")"
OUTDIR="dist/MonWatch/Linux"
echo "============================================"
echo " Building MonWatch for Linux"
echo "============================================"
echo ""
pip install -q pyinstaller 2>/dev/null || true
echo "[*] Running PyInstaller..."
python3 -m PyInstaller MonWatch.spec \
    --distpath "$OUTDIR" \
    --workpath build_linux \
    --noconfirm
echo "[*] Flattening output structure..."
if [ -d "$OUTDIR/MonWatch" ]; then
    mv "$OUTDIR/MonWatch/MonWatch" "$OUTDIR/" 2>/dev/null || true
    mv "$OUTDIR/MonWatch/_internal" "$OUTDIR/" 2>/dev/null || true
    rm -rf "$OUTDIR/MonWatch"
fi
echo "[*] Moving data folders to root..."
for dir in public data Process; do
    if [ -d "$OUTDIR/_internal/$dir" ]; then
        mv "$OUTDIR/_internal/$dir" "$OUTDIR/" 2>/dev/null || true
    fi
done
echo "[*] Copying shortcut scripts..."
cp scripts/setup.sh "$OUTDIR/"
cp scripts/runner.sh "$OUTDIR/"
chmod +x "$OUTDIR/runner.sh"
chmod +x "$OUTDIR/MonWatch" 2>/dev/null || true
echo ""
echo "[+] Build complete: $OUTDIR/"
echo "    $OUTDIR/MonWatch (binary)"
echo "    $OUTDIR/runner.sh (launcher)"
echo "    $OUTDIR/setup.sh (desktop entry setup)"
echo ""
