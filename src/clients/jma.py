# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/jma.py
# Description: Japan Meteorological Agency data client for tropical cyclone forecast retrieval.
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


import os
import json
import math
from pathlib import Path
from collections import OrderedDict

import requests
from PySide6.QtCore import QObject, Signal

JMA_TARGET_URL = "https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"
JMA_FORECAST_URL_TEMPLATE = "https://www.jma.go.jp/bosai/typhoon/data/{tc_id}/forecast.json"
JMA_SPEC_URL_TEMPLATE = "https://www.jma.go.jp/bosai/typhoon/data/{tc_id}/specifications.json"
JMA_PAST_TRACKS_URL = "https://www.jma.go.jp/bosai/typhoon/data/pastTracks.json"


def get_jma_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "jma_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_target_tc():
    r = requests.get(JMA_TARGET_URL, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_forecast(tc_id):
    url = JMA_FORECAST_URL_TEMPLATE.format(tc_id=tc_id)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_specifications(tc_id):
    url = JMA_SPEC_URL_TEMPLATE.format(tc_id=tc_id)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_past_tracks():
    r = requests.get(JMA_PAST_TRACKS_URL, timeout=30)
    r.raise_for_status()
    return r.json()


class JMAStormDownloader(QObject):
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
            self.progress.emit("Fetching JMA typhoon list...")
            target_list = fetch_target_tc()
            if not target_list:
                self.error.emit("No typhoon data from JMA.")
                self.finished.emit()
                return

            past_tracks = None
            try:
                self.progress.emit("Fetching past tracks...")
                past_tracks = fetch_past_tracks()
            except Exception as e:
                self.progress.emit(f"Past tracks unavailable: {e}")

            # Build a lookup from pastTracks by tropicalCyclone for merging
            past_lookup = {}
            if isinstance(past_tracks, list):
                for pt in past_tracks:
                    tc = pt.get("tropicalCyclone", "")
                    if tc:
                        past_lookup[tc] = pt

            jma_dir = get_jma_data_dir()

            for storm in target_list:
                if self._cancelled:
                    break

                tropical_cyclone = storm.get("tropicalCyclone", "")
                typhoon_number = storm.get("typhoonNumber", "")
                jma_category = storm.get("category", "")
                tc_id = tropical_cyclone
                if not typhoon_number or not typhoon_number.isdigit():
                    storm_id = tropical_cyclone
                    storm_name = tropical_cyclone
                else:
                    storm_id = typhoon_number
                    storm_name = tc_id or f"TC{storm_id}"

                self.progress.emit(f"Processing {storm_name}...")

                storm_dir = jma_dir / storm_id
                storm_dir.mkdir(exist_ok=True)

                track_history = []
                pre_typhoon = []
                typhoon = []
                track1 = storm.get("track1", {})
                pre_t = track1.get("preTyphoon", [])
                typh_t = track1.get("typhoon", [])
                for pt in pre_t:
                    if len(pt) >= 2:
                        c = [float(pt[0]), float(pt[1])]
                        track_history.append(c)
                        pre_typhoon.append(c)
                for pt in typh_t:
                    if len(pt) >= 2:
                        c = [float(pt[0]), float(pt[1])]
                        track_history.append(c)
                        typhoon.append(c)

                # Merge past track data from pastTracks.json (archive data)
                if tc_id in past_lookup and not track_history:
                    pt_data = past_lookup[tc_id]
                    pt_track1 = pt_data.get("track1", {})
                    for key in ("preTyphoon", "typhoon"):
                        for coord in pt_track1.get(key, []):
                            if len(coord) >= 2:
                                c = [float(coord[0]), float(coord[1])]
                                track_history.append(c)
                                if key == "preTyphoon":
                                    pre_typhoon.append(c)
                                else:
                                    typhoon.append(c)

                result = {
                    "storm_id": storm_id,
                    "tc_id": tc_id,
                    "storm_name": storm_name,
                    "typhoon_number": typhoon_number,
                    "jma_category": jma_category,
                    "classification": "Typhoon",
                    "basin": "Western Pacific",
                    "local_dir": str(storm_dir),
                    "track_history": track_history,
                    "track": {
                        "preTyphoon": pre_typhoon,
                        "typhoon": typhoon,
                    },
                    "track_points": [],
                    "probability_circles": [],
                    "storm_warning_areas": [],
                }

                # Fetch specifications.json (rich data) and forecast.json (cone geometry)
                spec_data = None
                forecast = None
                try:
                    spec_data = fetch_specifications(tc_id)
                except Exception as e:
                    self.progress.emit(f"  Specifications fetch failed: {e}")

                # Extract English name from specifications title entry
                storm_name_en = ""
                if isinstance(spec_data, list) and spec_data:
                    title_entry = spec_data[0]
                    if isinstance(title_entry, dict) and title_entry.get("part") == "title":
                        name_dict = title_entry.get("name", {})
                        storm_name_en = name_dict.get("en", "") or name_dict.get("jp", "")
                if storm_name_en:
                    result["storm_name"] = storm_name_en

                try:
                    forecast = fetch_forecast(tc_id)
                    result["jma_raw"] = forecast
                    # Extract best track from forecast entry with advancedHours=0
                    if isinstance(forecast, list):
                        for _entry in forecast:
                            if isinstance(_entry, dict) and _entry.get("part") != "title" and _entry.get("advancedHours") == 0:
                                _track = _entry.get("track")
                                if isinstance(_track, dict) and (_track.get("preTyphoon") or _track.get("typhoon")):
                                    result["best_track"] = {
                                        "part": _entry.get("part"),
                                        "advancedHours": 0,
                                        "validtime": _entry.get("validtime"),
                                        "track": _track,
                                    }
                                break
                except Exception as e:
                    self.progress.emit(f"  Forecast fetch failed: {e}")

                # Build a lookup from specifications by advancedHours
                spec_lookup = {}
                if isinstance(spec_data, list):
                    for sp in spec_data:
                        if not isinstance(sp, dict):
                            continue
                        if sp.get("part") == "title" or not isinstance(sp.get("part"), dict):
                            continue
                        ah = sp.get("advancedHours", 0)
                        spec_lookup[ah] = sp

                # Merge data: use forecast for positions + cone geo, specs for rich metadata
                seen_ah = set()
                if forecast:
                    for entry in forecast:
                        if not isinstance(entry, dict):
                            continue
                        part = entry.get("part", "")
                        if part == "title":
                            continue
                        center = entry.get("center")
                        if center and len(center) >= 2:
                            lat, lon = float(center[0]), float(center[1])
                            ah = entry.get("advancedHours", 0)
                            vt = entry.get("validtime", {})
                            dt_utc = vt.get("UTC", "") if isinstance(vt, dict) else ""
                            seen_ah.add(ah)

                            # Get rich metadata from specifications if available
                            sp = spec_lookup.get(ah, {})
                            cat_en = None
                            wind_kt = None
                            gust_kt = None
                            pressure_val = None
                            location = None
                            course = None
                            speed_kt = None
                            prob_circle_km = None
                            storm_warning_km = None
                            scale = None
                            intensity_label = None
                            if sp:
                                cat = sp.get("category", {})
                                cat_en = cat.get("en") if isinstance(cat, dict) else None
                                mw = sp.get("maximumWind", {})
                                if isinstance(mw, dict):
                                    sus = mw.get("sustained", {})
                                    wind_kt = int(sus["kt"]) if isinstance(sus, dict) and sus.get("kt", "").lstrip("-").isdigit() else None
                                    gust = mw.get("gust", {})
                                    gust_kt = int(gust["kt"]) if isinstance(gust, dict) and gust.get("kt", "").lstrip("-").isdigit() else None
                                    if wind_kt == 0 and sus.get("kt") == "-":
                                        wind_kt = None
                                pressure_val = sp.get("pressure")
                                loc = sp.get("location", "")
                                location = loc if loc != "-" else None
                                course = sp.get("course", "").replace("-", "") or None
                                spd = sp.get("speed", {})
                                if isinstance(spd, dict):
                                    sk = spd.get("kt", "")
                                    speed_kt = int(sk) if sk and sk != "-" and sk.lstrip("-").isdigit() else None
                                pcr = sp.get("probabilityCircleRadius", {})
                                if isinstance(pcr, dict):
                                    km = pcr.get("km")
                                    prob_circle_km = int(km) if km else None
                                sw = sp.get("stormWarning", [])
                                if sw and isinstance(sw, list) and len(sw) > 0:
                                    rng = sw[0].get("range", {})
                                    rkm = rng.get("km")
                                    storm_warning_km = int(rkm) if rkm else None
                                sc = sp.get("scale", "")
                                scale = sc if sc and sc != "-" else None
                                il = sp.get("intensity", "")
                                intensity_label = il if il and il != "-" else None

                            # Category chain: spec category > targetTc jma_category > None
                            ic = cat_en or (jma_category if ah == 0 else None)
                            # Intensity (kt) from specifications
                            intensity_kt = wind_kt

                            pt = OrderedDict([
                                ("lat", lat),
                                ("lon", lon),
                                ("datetime", dt_utc),
                                ("advanced_hours", ah),
                                ("intensity", intensity_kt),
                                ("intensity_category", ic),
                                ("pressure", pressure_val),
                                ("wind_gust_kt", gust_kt),
                                ("location", location),
                                ("course", course),
                                ("speed_kt", speed_kt),
                                ("scale", scale),
                                ("intensity_label", intensity_label),
                                ("prob_circle_km", prob_circle_km),
                                ("storm_warning_km", storm_warning_km),
                            ])
                            result["track_points"].append(pt)

                        # Cone geometry from forecast (tangent lines + radius in meters)
                        prob = entry.get("probabilityCircle")
                        if prob:
                            result["probability_circles"].append({
                                "radius": prob.get("radius", 0),
                                "tangent": prob.get("tangent", []),
                                "center": center,
                            })

                        swa = entry.get("stormWarningArea")
                        if swa:
                            result["storm_warning_areas"].append({
                                "arc": swa.get("arc", []),
                                "line": swa.get("line", []),
                            })

                # For any specifications entries not covered by forecast, add them
                for ah, sp in sorted(spec_lookup.items()):
                    if ah in seen_ah:
                        continue
                    pos = sp.get("position", {})
                    center = pos.get("deg") if isinstance(pos, dict) else None
                    if not center or len(center) < 2:
                        continue
                    lat, lon = float(center[0]), float(center[1])
                    vt = sp.get("validtime", {})
                    dt_utc = vt.get("UTC", "") if isinstance(vt, dict) else ""
                    cat = sp.get("category", {})
                    cat_en = cat.get("en") if isinstance(cat, dict) else None
                    mw = sp.get("maximumWind", {})
                    wind_kt = None
                    if isinstance(mw, dict):
                        sus = mw.get("sustained", {})
                        wind_kt = int(sus["kt"]) if isinstance(sus, dict) and sus.get("kt", "").lstrip("-").isdigit() else None
                    pt = OrderedDict([
                        ("lat", lat), ("lon", lon),
                        ("datetime", dt_utc),
                        ("advanced_hours", ah),
                        ("intensity", wind_kt),
                        ("intensity_category", cat_en),
                    ])
                    result["track_points"].append(pt)

                try:
                    meta_path = storm_dir / f"{storm_id}_meta.json"
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
