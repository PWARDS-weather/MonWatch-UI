#!/usr/bin/env bash
# MonWatch-UI cross-platform bootstrap (Linux/macOS).
# Installs the package into the current user environment, then launches via
# `python -m src` (equivalent to the `monwatch` console script).
set -e
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
    else
        PYTHON_BIN="$(command -v python)"
    fi
fi

if ! "$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    echo "[!] Python 3.10+ not found. Install it first, e.g.:"
    echo "    apt install python3 python3-pip python3-venv   (Debian/Ubuntu)"
    echo "    dnf install python3 python3-pip                (Fedora)"
    echo "    brew install python                             (macOS)"
    exit 1
fi

echo "[*] Using Python: $("$PYTHON_BIN" --version)"
if ! "$PYTHON_BIN" -c 'import src' >/dev/null 2>&1; then
    echo "[*] Installing MonWatch-UI (editable) and dependencies..."
    "$PYTHON_BIN" -m pip install --user -e .
fi

echo "[*] Launching MonWatch-UI..."
exec "$PYTHON_BIN" -m src "$@"