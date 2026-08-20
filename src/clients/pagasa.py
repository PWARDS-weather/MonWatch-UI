# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/pagasa.py
# Description: PAGASA data client for Philippine tropical cyclone bulletins and forecasts.
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


import json
import datetime
from pathlib import Path
from collections import OrderedDict

import requests
from PySide6.QtCore import QObject, Signal

PAGASA_TC_URL = "https://pubfiles.pagasa.dost.gov.ph/tamss/weather/cyclone.dat"

PAGASA_CATEGORY_MAP = {
    "TD": "Tropical Depression",
    "TS": "Tropical Storm",
    "STS": "Severe Tropical Storm",
    "TY": "Typhoon",
    "STY": "Super Typhoon",
    "LPA": "Low Pressure Area",
}


def get_latest_forecast_datetime(track_points):
    """Get the latest forecast datetime from PAGASA track points.
    
    Args:
        track_points: List of track point dicts with 'datetime' field
    
    Returns:
        datetime or None: The latest forecast datetime, or None if no points
    """
    if not track_points:
        return None
    
    latest_dt = None
    for pt in track_points:
        dt_str = pt.get('datetime', '')
        if not dt_str:
            continue
        try:
            dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            if latest_dt is None or dt > latest_dt:
                latest_dt = dt
        except ValueError:
            continue
    
    return latest_dt


def get_initial_forecast_datetime(track_points):
    """Get the datetime of the PAGASA initial point (current observed position).

    PAGASA track points are classified by radius_km:
      - radius_km > 0                    -> forecast point
      - radius_km == 0, next radius == 0 -> best track (past positions)
      - radius_km == 0, next radius > 0  -> initial point

    The initial point is the transition point from best track to forecast.

    Args:
        track_points: List of PAGASA track point dicts.

    Returns:
        datetime or None: Timezone-aware UTC datetime of the initial point,
        or None if it cannot be determined.
    """
    if not track_points:
        return None

    initial = None
    for i, p in enumerate(track_points):
        r = p.get("radius_km", 0)
        if r > 0:
            initial = track_points[i - 1] if i > 0 else p
            break
        if r == 0 and i + 1 < len(track_points):
            nr = track_points[i + 1].get("radius_km", 0)
            if nr > 0:
                initial = p
                break
    if initial is None:
        initial = track_points[0]

    dt_str = initial.get("datetime", "")
    if not dt_str:
        return None
    try:
        dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        # PAGASA stores datetimes in Philippine local time (+8)
        return dt.replace(tzinfo=datetime.timezone.utc) - datetime.timedelta(hours=8)
    except ValueError:
        return None


def get_pagasa_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "pagasa_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_cyclone_data():
    r = requests.get(PAGASA_TC_URL, timeout=30)
    r.raise_for_status()
    return r.text


def parse_storm_name(raw_name):
    name = raw_name.strip()
    local_name = name
    intl_name = ""
    if "{" in name and "}" in name:
        local_name = name.split("{")[0].strip()
        intl_name = name.split("{")[1].split("}")[0].strip()
    return local_name, intl_name


def parse_cyclone_text(text):
    storms = []
    # Split by EOF markers between storms
    blocks = text.replace("\r\n", "\n").split("\nEOF\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if not lines:
            continue
        raw_name = lines[0].strip()
        if not raw_name or raw_name.upper() == "EOF":
            continue
        local_name, intl_name = parse_storm_name(raw_name)
        info = OrderedDict()
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            cyclone_type = parts[0]
            date_str = parts[1]
            time_str = parts[2]
            lat = parts[3]
            lon = parts[4]
            radius = parts[5]
            dt_key = f"{date_str} {time_str}"
            info[dt_key] = {
                "cyclone_type": cyclone_type,
                "date": date_str,
                "time": time_str,
                "latitude": lat,
                "longitude": lon,
                "radius": radius,
            }
        if info:
            storms.append({
                "cyclone_name": raw_name,
                "info": info,
            })
    return storms


class PAGASAStormDownloader(QObject):
    progress = Signal(str)
    storm_downloaded = Signal(dict)
    finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit("Fetching PAGASA cyclone data...")
            raw_text = fetch_cyclone_data()
            raw = parse_cyclone_text(raw_text)
            if not raw:
                self.error.emit("No cyclone data from PAGASA.")
                self.finished.emit()
                return

            pagasa_dir = get_pagasa_data_dir()

            for storm in raw:
                if self._cancelled:
                    break

                raw_name = storm.get("cyclone_name", "")
                local_name, intl_name = parse_storm_name(raw_name)
                storm_id = local_name.lower().replace(" ", "_") or intl_name.lower().replace(" ", "_")
                if not storm_id:
                    continue

                storm_name = local_name if local_name else intl_name
                info = storm.get("info", {})
                if not info:
                    continue

                self.progress.emit(f"Processing {storm_name}...")

                storm_dir = pagasa_dir / storm_id
                storm_dir.mkdir(exist_ok=True)

                track_points = []
                latest_type = ""
                for dt_key in sorted(info.keys()):
                    entry = info[dt_key]
                    cyclone_type = entry.get("cyclone_type", "")
                    if cyclone_type:
                        latest_type = cyclone_type
                    lat = float(entry.get("latitude", 0))
                    lon = float(entry.get("longitude", 0))
                    radius = entry.get("radius", "0")
                    try:
                        radius_km = int(radius) if radius else 0
                    except (ValueError, TypeError):
                        radius_km = 0

                    dt_str = entry.get("date", "") + " " + entry.get("time", "")
                    cat_full = PAGASA_CATEGORY_MAP.get(cyclone_type, cyclone_type)

                    pt = OrderedDict([
                        ("lat", lat),
                        ("lon", lon),
                        ("datetime", dt_str.strip()),
                        ("intensity", None),
                        ("intensity_category", cat_full),
                        ("cyclone_type", cyclone_type),
                        ("radius_km", radius_km),
                    ])
                    track_points.append(pt)

                result = {
                    "storm_id": storm_id,
                    "storm_name": storm_name,
                    "local_name": local_name,
                    "intl_name": intl_name,
                    "raw_name": raw_name,
                    "classification": PAGASA_CATEGORY_MAP.get(latest_type, latest_type),
                    "basin": "Western Pacific",
                    "local_dir": str(storm_dir),
                    "track_points": track_points,
                }

                meta_path = storm_dir / f"{storm_id}_meta.json"
                try:
                    with open(meta_path, 'w') as f:
                        json.dump(result, f, indent=2, default=str)
                except Exception:
                    pass

                self.storm_downloaded.emit(result)
                self.progress.emit(f"  Done with {storm_name}")

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit()
