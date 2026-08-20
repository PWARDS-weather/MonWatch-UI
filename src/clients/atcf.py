# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/atcf.py
# Description: ATCF (Automated Tropical Cyclone Forecast) format parser and data handler.
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


import re
import json
import gzip
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import requests
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

# ATCF a-deck sources (public)
NHC_ATCF_URL = "https://ftp.nhc.noaa.gov/atcf/aid_public/"
JTWC_BASE = "https://www.metoc.navy.mil/jtwc/products/"
JTWC_BULLETINS = {
    "wp": JTWC_BASE + "abpwweb.txt",
    "io": JTWC_BASE + "abioweb.txt",
    "sh": JTWC_BASE + "abshweb.txt",
}
BASIN_SUFFIX = {"wp": "W", "io": "B", "sh": "S"}

JTWC_CATEGORIES = [
    (130, "Super Typhoon"),
    (64,  "Typhoon"),
    (34,  "Tropical Storm"),
    (0,   "Tropical Depression"),
]

NHC_CATEGORIES = [
    (137, "Category 5"),
    (113, "Category 4"),
    (96,  "Category 3"),
    (83,  "Category 2"),
    (64,  "Category 1"),
    (34,  "Tropical Storm"),
    (0,   "Tropical Depression"),
]


def _kt_to_jtwc_cat(kt):
    if kt is None:
        return None
    for threshold, cat in JTWC_CATEGORIES:
        if kt >= threshold:
            return cat
    return None


def _kt_to_nhc_cat(kt):
    if kt is None:
        return None
    for threshold, cat in NHC_CATEGORIES:
        if kt >= threshold:
            return cat
    return None


def get_atcf_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "atcf_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download_text(url):
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
        except requests.RequestException:
            if attempt < 2:
                import time
                time.sleep(2 ** attempt)
    return None


def _download_gz(url):
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                return gzip.decompress(r.content).decode("utf-8")
        except requests.RequestException:
            if attempt < 2:
                import time
                time.sleep(2 ** attempt)
    return None


def parse_jtwc_atcf(storm_id, text):
    """Parse JTWC web.txt format into ATCF-like structure."""
    storm = {
        "storm_id": storm_id,
        "basin": storm_id[:2].upper(),
        "storm_name": "",
        "current_lat": None,
        "current_lon": None,
        "max_winds_kt": None,
        "gusts_kt": None,
        "central_pressure": None,
        "category": None,
        "issued_dtg": "",
        "movement_dir": None,
        "movement_speed": None,
    }
    m = re.search(
        r"SUBJ/([\w\s]+?)\s+(\d{2})([WSB])\s+\((\w+)\)\s+WARNING\s+NR\s+(\d+)",
        text
    )
    if m:
        storm["storm_name"] = m.group(4)
    m = re.search(
        r"WARNING POSITION:\s*\n\s*(\d{6})Z\s*-{3,}\s*NEAR\s+"
        r"([\d.]+)([NS])\s+([\d.]+)([EW])",
        text
    )
    if m:
        storm["issued_dtg"] = m.group(1)
        storm["current_lat"] = float(m.group(2)) * (1 if m.group(3) == "N" else -1)
        storm["current_lon"] = float(m.group(4)) * (1 if m.group(5) == "E" else -1)
    m = re.search(r"MAX SUSTAINED WINDS\s*-\s*(\d+)\s*KT,\s*GUSTS\s*(\d+)\s*KT", text)
    if m:
        storm["max_winds_kt"] = int(m.group(1))
        storm["gusts_kt"] = int(m.group(2))
        storm["category"] = _kt_to_jtwc_cat(storm["max_winds_kt"])
    m = re.search(r"MINIMUM CENTRAL PRESSURE.*?IS\s+(\d+)\s*MB", text)
    if m:
        storm["central_pressure"] = int(m.group(1))
    m = re.search(r"MOVEMENT PAST SIX HOURS\s*-\s*(\d+)\s*DEGREES\s*AT\s+(\d+)\s*KTS", text)
    if m:
        storm["movement_dir"] = int(m.group(1))
        storm["movement_speed"] = int(m.group(2))
    return storm


def _parse_atcf_deck_line(line: str) -> Optional[dict]:
    """Parse a single ATCF a-deck line (CARQ/OFCL at tau=0). Returns dict or None."""
    line = line.strip()
    if not line:
        return None
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 12:
        return None

    try:
        basin = parts[0].upper()
        cyclone_num = parts[1]
        dtg = parts[2]
        tech_num = parts[3]
        tech = parts[4].upper()
        tau = int(parts[5])
        lat_str = parts[6]
        lon_str = parts[7]
        max_wind = parts[8]
        min_pres = parts[9]
        storm_type = parts[10].upper()

        if tech not in ("CARQ", "OFCL") or tau != 0:
            return None

        if not max_wind or max_wind == "0":
            return None

        lat = _parse_atcf_lat(lat_str)
        lon = _parse_atcf_lon(lon_str, basin)
        if lat is None or lon is None:
            return None

        wind_kt = int(max_wind) if max_wind.isdigit() else None
        pressure = int(min_pres) if min_pres.isdigit() else None

        name = parts[-2].strip() if len(parts) > 2 else ""

        cat = _determine_category(storm_type, wind_kt, basin)

        return {
            "storm_id": f"{basin.lower()}{cyclone_num}",
            "basin": basin,
            "storm_name": name or f"{basin}{cyclone_num}",
            "current_lat": lat,
            "current_lon": lon,
            "max_winds_kt": wind_kt,
            "gusts_kt": None,
            "central_pressure": pressure,
            "category": cat,
            "issued_dtg": dtg,
            "movement_dir": None,
            "movement_speed": None,
        }
    except Exception:
        return None


def _parse_atcf_lat(lat_str: str) -> Optional[float]:
    """Parse ATCF latitude string like '218N' -> 21.8"""
    lat_str = lat_str.strip().upper()
    if not lat_str or lat_str[-1] not in ("N", "S"):
        return None
    try:
        deg = int(lat_str[:-1]) // 10
        tenth = int(lat_str[:-1]) % 10
        val = deg + tenth / 10.0
        return val if lat_str[-1] == "N" else -val
    except Exception:
        return None


def _parse_atcf_lon(lon_str: str, basin: str) -> Optional[float]:
    """Parse ATCF longitude string like '998W' -> -99.8 or '1010W' -> -101.0"""
    lon_str = lon_str.strip().upper()
    if not lon_str or lon_str[-1] not in ("E", "W"):
        return None
    try:
        digits = lon_str[:-1]
        if len(digits) == 3:
            deg = int(digits[:2])
            tenth = int(digits[2])
        elif len(digits) == 4:
            deg = int(digits[:3])
            tenth = int(digits[3])
        else:
            return None
        val = deg + tenth / 10.0
        return -val if lon_str[-1] == "W" else val
    except Exception:
        return None


def _determine_category(storm_type: str, wind_kt: Optional[int], basin: str) -> str:
    if not storm_type:
        return "Invest" if wind_kt is None else "Tropical Depression"
    st = storm_type.upper()
    if st in ("DB", "LO", "LPA", "INVEST", "INV"):
        return "Invest"
    if st == "TD":
        return "Tropical Depression"
    if st == "TS":
        return "Tropical Storm"
    if st in ("HU", "TY"):
        if wind_kt is not None:
            return _kt_to_nhc_cat(wind_kt) if basin in ("AL", "EP", "CP") else _kt_to_jtwc_cat(wind_kt)
        return "Hurricane" if basin in ("AL", "EP", "CP") else "Typhoon"
    if st in ("STY", "SS"):
        return "Super Typhoon"
    if st in ("EX", "ET"):
        return "Extratropical"
    return "Invest" if wind_kt is None else "Tropical Depression"


def _parse_jtwc_invests_from_bulletin(basin: str, text: str, yy: str) -> list:
    """Parse invests from JTWC bulletin text (ABPW/ABIO/ABSH)."""
    storms = []
    suffix = BASIN_SUFFIX.get(basin, "")

    pattern = re.compile(
        r"\(INVEST\s+(\d{2})" + suffix + r"\)[.\s\S]*?"
        r"NEAR\s+([\d.]+)([NS])\s+([\d.]+)([EW])[.\s\S]*?"
        r"MAXIMUM SUSTAINED SURFACE WINDS ARE ESTIMATED AT\s+(\d+)\s*(?:TO\s*(\d+))?\s*KNOTS[.\s\S]*?"
        r"MINIMUM SEA LEVEL PRESSURE IS ESTIMATED TO BE NEAR\s+(\d+)\s*MB",
        re.IGNORECASE
    )

    for m in pattern.finditer(text):
        num = m.group(1)
        lat = float(m.group(2)) * (1 if m.group(3) == "N" else -1)
        lon = float(m.group(4)) * (1 if m.group(5) == "E" else -1)
        wind_min = int(m.group(6))
        wind_max = int(m.group(7)) if m.group(7) else wind_min
        pressure = int(m.group(8))

        sid = f"{basin}{num}{yy}"
        storm = {
            "storm_id": sid,
            "basin": basin.upper(),
            "storm_name": f"INVEST {num}{suffix}",
            "current_lat": lat,
            "current_lon": lon,
            "max_winds_kt": (wind_min + wind_max) // 2,
            "gusts_kt": None,
            "central_pressure": pressure,
            "category": "Invest",
            "issued_dtg": "",
            "movement_dir": None,
            "movement_speed": None,
            "source": "JTWC",
        }
        storms.append(storm)

    return storms


def _map_cyclone_nature_to_category(nature, winds):
    """Map KnackWX cyclone_nature codes to category strings."""
    mapping = {
        "DB": "Invest",
        "LO": "Invest",
        "LPA": "Invest",
        "INVEST": "Invest",
        "INV": "Invest",
        "TD": "Tropical Depression",
        "TS": "Tropical Storm",
        "ST": "Super Typhoon",
        "TY": "Typhoon",
        "HU": "Hurricane",
        "SD": "Subtropical Depression",
        "SS": "Subtropical Storm",
        "EX": "Extratropical",
        "ET": "Extratropical",
        "DS": "Disturbance",
        "WV": "Tropical Wave",
        "MX": "Monsoon Depression",
    }
    base_cat = mapping.get(nature.upper(), "Invest")
    
    # Override with intensity-based category if winds available
    if winds is not None and base_cat in ["Tropical Storm", "Typhoon", "Hurricane", "Severe Tropical Storm"]:
        if nature.upper() == "HU":
            if winds >= 130:
                return "Major Hurricane"
            elif winds >= 64:
                return "Hurricane"
        else:
            if winds >= 130:
                return "Super Typhoon"
            elif winds >= 64:
                return "Typhoon"
        if winds >= 34:
            return "Tropical Storm"
        else:
            return "Tropical Depression"
    return base_cat


def fetch_knackwx_atcf():
    """
    Fetch ATCF data from KnackWX API v2.
    
    Returns list of storm dictionaries with standardized fields.
    Primary data source for ATCF overlay in MonWatch-UI.
    """
    try:
        response = requests.get("https://api.knackwx.com/atcf/v2", timeout=30)
        if response.status_code == 200:
            data = response.json()
            storms = []
            for storm in data:
                cyclone_nature = storm.get("cyclone_nature", "")
                category = _map_cyclone_nature_to_category(
                    cyclone_nature,
                    storm.get("winds")
                )
                storms.append({
                    "atcf_id": storm.get("atcf_id"),
                    "long_atcf_id": storm.get("long_atcf_id"),
                    "storm_name": storm.get("storm_name", "INVEST"),
                    "current_lat": storm.get("latitude"),
                    "current_lon": storm.get("longitude"),
                    "max_winds_kt": storm.get("winds"),
                    "central_pressure": storm.get("pressure"),
                    "category": category,
                    "analysis_time": storm.get("analysis_time"),
                    "basin": storm.get("basin"),
                    "cyclone_nature": cyclone_nature,
                    "atcf_sector_file": storm.get("atcf_sector_file"),
                    "interp_sector_file": storm.get("interp_sector_file"),
                    "source": "KnackWX",
                    "transitioned_from": storm.get("transitioned_from"),
                    "origin_basin": storm.get("origin_basin"),
                    "movespeed": storm.get("movespeed"),
                    "movedir": storm.get("movedir"),
                })
            log.info(f"KnackWX ATCF: fetched {len(storms)} storms")
            return storms
        else:
            log.warning(f"KnackWX ATCF: HTTP {response.status_code}")
    except requests.RequestException as e:
        log.warning(f"KnackWX ATCF fetch failed: {e}")
    except Exception as e:
        log.warning(f"KnackWX ATCF parse error: {e}")
    return []


def fetch_all_active_storms():
    """
    Fetch all active storms AND invests from ATCF sources (NHC a-deck + JTWC).
    
    CORPORATE NOTE: For technical reasons, we'll be using KnackWX API for now.
    This legacy NHC/JTWC fetcher is preserved for future re-enablement.
    Primary ATCF data source: fetch_knackwx_atcf()
    
    Returns:
        list: Deduplicated list of active storm dictionaries.
    """
    # TEMPORARILY DISABLED - Using KnackWX API instead
    # Original NHC/JTWC fetching code preserved below for future reference:
    r"""
    storms = []

    # NHC ATCF a-deck files (storms + invests)
    try:
        current_yyyy = datetime.now(timezone.utc).strftime("%Y")
        basins = [("al", "AL"), ("ep", "EP"), ("cp", "CP")]
        nhc_systems = {}  # storm_id -> list of parsed entries
        for basin_lc, basin_uc in basins:
            for num in range(1, 50):
                num_str = f"{num:02d}"
                url = f"{NHC_ATCF_URL}a{basin_lc}{num_str}{current_yyyy}.dat.gz"
                text = _download_gz(url)
                if text:
                    for line in text.strip().split("\n"):
                        parsed = _parse_atcf_deck_line(line)
                        if parsed:
                            nhc_systems.setdefault(parsed["storm_id"], []).append(parsed)
            # Invests 90-99
            for num in range(90, 100):
                num_str = str(num)
                url = f"{NHC_ATCF_URL}a{basin_lc}{num_str}{current_yyyy}.dat.gz"
                text = _download_gz(url)
                if text:
                    for line in text.strip().split("\n"):
                        parsed = _parse_atcf_deck_line(line)
                        if parsed:
                            nhc_systems.setdefault(parsed["storm_id"], []).append(parsed)

        # Deduplicate NHC systems: keep latest advisory per storm_id
        for sid, entries in nhc_systems.items():
            if entries:
                # Sort by issued_dtg descending, take first
                entries.sort(key=lambda e: e.get("issued_dtg", ""), reverse=True)
                best = entries[0]
                best["source"] = "NHC"
                storms.append(best)

    except Exception as e:
        log.warning(f"ATCF NHC a-deck fetch failed: {e}")

    # JTWC bulletins (active storms + invests)
    current_yy = datetime.now(timezone.utc).strftime("%y")
    for basin, url in JTWC_BULLETINS.items():
        try:
            text = _download_text(url)
            if not text:
                continue
            suffix = BASIN_SUFFIX.get(basin)
            year_match = re.search(r"Z[A-Z]{3}(\d{4})", text)
            yy = year_match.group(1)[-2:] if year_match else current_yy

            # Regular storms (01-49)
            storm_nums = set(re.findall(r"\b(\d{2})" + suffix + r"\b", text))
            for num in storm_nums:
                if num.startswith("9"):
                    continue  # skip invests, handled below
                sid = f"{basin}{num}{yy}"
                web_url = JTWC_BASE + f"{sid}web.txt"
                web_text = _download_text(web_url)
                if web_text:
                    storm = parse_jtwc_atcf(sid, web_text)
                    storm["source"] = "JTWC"
                    storms.append(storm)

            # Invests (90-99) - parse from bulletin directly
            invest_storms = _parse_jtwc_invests_from_bulletin(basin, text, yy)
            storms.extend(invest_storms)

        except Exception as e:
            log.warning(f"ATCF JTWC fetch failed for {basin}: {e}")

    # Final deduplication by storm_id (keep first)
    seen = set()
    unique = []
    for s in storms:
        sid = s.get("storm_id", "").upper()
        if sid and sid not in seen:
            seen.add(sid)
            unique.append(s)

    return unique
    """
    return []  # Use fetch_knackwx_atcf() instead


class ATCFClient(QObject):
    stormsUpdated = Signal(list)
    progress = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False
        self._last_storms = []

    def cancel(self):
        self._cancelled = True

    def get_last_storms(self):
        return self._last_storms

    def run(self):
        try:
            self.progress.emit("Fetching ATCF storm data...")
            storms = fetch_all_active_storms()
            if self._cancelled:
                return
            if storms:
                self._last_storms = storms
                self.progress.emit(f"ATCF: {len(storms)} active system(s) found")
                try:
                    atcf_dir = get_atcf_data_dir()
                    meta = {"fetched": datetime.now(timezone.utc).isoformat(), "storms": storms}
                    (atcf_dir / "latest.json").write_text(
                        json.dumps(meta, indent=2, default=str), encoding="utf-8"
                    )
                except Exception as e:
                    log.warning(f"Failed to save ATCF data: {e}")
                self.stormsUpdated.emit(storms)
            else:
                self.progress.emit("ATCF: no active systems found")
            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit()