# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: parsers/sataid_reader.py
# Description: Satellite ID file format parser for metadata extraction.
#
# Principal Developer: Zero (Prince Al Zhanjie B. Dela Rosa)
# Affiliation: PWARDS-weather
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# See also: https://www.apache.org/licenses/LICENSE-2.0
#
# Copyright (C) 2025-2026 PWARDS-weather (Pasacao Weather Atmospheric
# and Real-Time Data System)
#
# --- OPEN-SOURCE POLICY ---
# Redistribution or modification without formally notifying PWARDS-weather
# developers constitutes unauthorized use and violates the license terms.
# Developers must be notified via email or GitHub issue before any changes
# are distributed. See LICENSE file for complete terms.
# =============================================================================

import struct
import time
import tempfile
from pathlib import Path
import numpy as np
import sataid

_SZDD_MAGIC = b"SZDD"
_cache_dir = None
_CACHE_MAX_AGE = 3600
_CACHE_MAX_BYTES = 512 * 1024 * 1024

# -------------------------------------------------------------------- #
#  Pure-Python SZDD (MS COMPRESS / LZSS) decompressor
#  Replaces subprocess call to expand.exe, ~10-50x faster per file.
# -------------------------------------------------------------------- #
#  Header: SZDD(4) + type(1) + date(2) + time(2) = 9 bytes
#  Compressed data uses LZSS with 4096-byte sliding window,
#  12-bit offset, 4-bit length-3 (match length 3..18),
#  grouped in 8-operation blocks prefixed by a flag byte.
# -------------------------------------------------------------------- #


def _read_le16(buf, pos):
    return buf[pos] | (buf[pos + 1] << 8)


def _decompress_szdd(data: bytes) -> bytes:
    if len(data) < 9 or data[:4] != _SZDD_MAGIC:
        raise ValueError("Not a valid SZDD file")
    if data[4] != 0x41:
        raise ValueError(f"Unsupported SZDD compression type: 0x{data[4]:02x}")

    compressed = memoryview(data)[9:]
    src_len = len(compressed)
    src_pos = 0

    ring = bytearray(4096)
    ring_pos = 0x10
    out = bytearray()

    while src_pos < src_len:
        flag_byte = compressed[src_pos]
        src_pos += 1
        for bit in range(8):
            if src_pos >= src_len:
                break
            if flag_byte & (0x80 >> bit):
                lit = compressed[src_pos]
                src_pos += 1
                ring[ring_pos] = lit
                out.append(lit)
                ring_pos = (ring_pos + 1) & 0xFFF
            else:
                if src_pos + 1 >= src_len:
                    break
                hi = compressed[src_pos]
                lo = compressed[src_pos + 1]
                src_pos += 2
                length = ((hi >> 4) & 0x0F) + 3
                offset = ((hi & 0x0F) << 8) | lo
                match_pos = (ring_pos - offset - 1) & 0xFFF
                for _ in range(length):
                    b = ring[match_pos]
                    ring[ring_pos] = b
                    out.append(b)
                    ring_pos = (ring_pos + 1) & 0xFFF
                    match_pos = (match_pos + 1) & 0xFFF

    return bytes(out)


# -------------------------------------------------------------------- #
#  Temp cache helpers
# -------------------------------------------------------------------- #


def _ensure_cache_dir():
    global _cache_dir
    if _cache_dir is None:
        _cache_dir = Path(tempfile.gettempdir()) / "sataid_cache"
        _cache_dir.mkdir(parents=True, exist_ok=True)
        _clean_stale_cache()
    return _cache_dir


def _clean_stale_cache():
    global _cache_dir
    if _cache_dir is None:
        return
    now = time.time()
    total = 0
    try:
        for p in list(_cache_dir.iterdir()):
            if p.is_file():
                age = now - p.stat().st_mtime
                if age > _CACHE_MAX_AGE:
                    p.unlink(missing_ok=True)
                else:
                    total += p.stat().st_size
        if total > _CACHE_MAX_BYTES:
            files = sorted(_cache_dir.iterdir(), key=lambda p: p.stat().st_mtime)
            for p in files:
                if total <= _CACHE_MAX_BYTES:
                    break
                sz = p.stat().st_size
                p.unlink(missing_ok=True)
                total -= sz
    except Exception:
        pass


# -------------------------------------------------------------------- #
#  Public API
# -------------------------------------------------------------------- #


def is_sataid_file(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        with open(path, "rb") as f:
            return f.read(4) == _SZDD_MAGIC
    except Exception:
        return False


def decompress_bytes(data: bytes) -> bytes:
    """Decompress a SZDD-compressed byte buffer in pure Python."""
    return _decompress_szdd(data)


def decompress_file(path: Path) -> bytes:
    """Read a SZDD file and decompress it, using a temp-file cache.

    The raw (decompressed) output is cached under %TEMP%/sataid_cache/
    so that repeated reads of the same file avoid re-decompression.
    """
    cache_dir = _ensure_cache_dir()
    cache_path = cache_dir / f"{path.name}.raw"

    if cache_path.exists():
        return cache_path.read_bytes()

    raw = path.read_bytes()
    data = _decompress_szdd(raw)
    try:
        cache_path.write_bytes(data)
    except Exception:
        pass
    return data


def read_sataid_cached(path: Path):
    """Read a SATAID file via sataid library (handles both raw & SZDD).

    The sataid library natively reads *.Z0000 raw files and SZDD-compressed
    *.wis files.  This wrapper caches the parsed SataidArray in a temp
    file so that repeated reads of the same path skip re-parsing.
    """
    import pickle
    cache_dir = _ensure_cache_dir()
    cache_path = cache_dir / f"{path.name}.sataid"

    if cache_path.exists():
        with open(cache_path, 'rb') as f:
            return pickle.load(f)

    sat = sataid.read_sataid(str(path))
    try:
        with open(cache_path, 'wb') as f:
            pickle.dump(sat, f)
    except Exception:
        pass
    return sat


def list_sataid_files(date_str: str = None) -> list:
    top_dir = Path(__file__).parent.parent.parent
    download_dir = top_dir / "data" / "Download"
    if not download_dir.exists():
        return []

    results = []
    base_pat = f"SATAID_{date_str}_*" if date_str else "SATAID_*"
    valid_prefixes = ("IR", "WV", "VS", "S1", "I2", "I4", "S2")
    for folder in sorted(download_dir.glob(base_pat)):
        for child in sorted(folder.rglob("*")):
            if child.is_file() and child.name.startswith(valid_prefixes):
                results.append(child)
    return results


_EXTENDED_PREFIXES = (
    "VIS01", "VIS02", "VIS03", "VIS04", "VIS05", "VIS06",
    "IR07", "IR08", "IR09", "IR10", "IR11", "IR12", "IR13", "IR14", "IR15", "IR16",
)


def parse_filename(filename: str) -> dict:
    stem = Path(filename).stem.split(".")[0]
    band_part = stem[:-8] if len(stem) > 8 and stem[-8:].isdigit() else stem
    date_str = stem[-8:] if len(stem) >= 8 and stem[-8:].isdigit() else ""
    for key in _EXTENDED_PREFIXES:
        if band_part.startswith(key):
            return {"band": key, "date": date_str, "filename": stem}
    for key in sorted(["IR", "WV", "VS", "S1", "I2", "I4", "S2"], key=len, reverse=True):
        if band_part.startswith(key):
            return {"band": key, "date": date_str, "filename": stem}
    return {"band": band_part, "date": date_str, "filename": stem}
