# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/pagasa_alerts.py
# Description: PAGASA alert fetching client for public storm warning signals.
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


import logging
import re
from datetime import datetime, timezone

import requests
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

PAGASA_CAP_API_URL = "https://www.panahon.gov.ph/api/v1/cap-alerts"

_WARNING_LEVEL_RE = re.compile(r"\b(red|orange|yellow)\s+warning\b", re.IGNORECASE)

ALERT_SEVERITY_ORDER = {
    "Extreme": 5,
    "Severe": 4,
    "Moderate": 3,
    "Minor": 2,
    "Unknown": 1,
}
ALERT_URGENCY_ORDER = {
    "Immediate": 5,
    "Expected": 4,
    "Future": 3,
    "Past": 2,
    "Unknown": 1,
}

CAP_EVENT_TYPE_MAP = {
    "THUNDERSTORM": "Thunderstorm",
    "RAINFALL": "Rainfall",
    "FLOOD": "Flood",
    "NOTE": "Note",
}

def _headers():
    return {
        "User-Agent": "(MonWatch-Cyclone, pagasa-alert-client@monwatch.app)",
        "Accept": "application/json",
    }

def _get(url, params=None, timeout=30):
    resp = requests.get(url, headers=_headers(), params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def fetch_pagasa_alerts():
    """Fetch CAP alerts from PAGASA API.
    
    Returns
    -------
    list[dict]  Parsed alert dicts.
    """
    data = _get(PAGASA_CAP_API_URL)
    if not data.get("success", False):
        log.warning("PAGASA API returned success=false")
        return []
    return data.get("data", {}).get("alert_data", [])

def parse_pagasa_alert(raw_alert):
    """Parse a raw PAGASA CAP alert into a structured dict compatible with NWS format.
    
    Returns keys: id, headline, description, severity, urgency,
                  certainty, event, effective, expires, sender,
                  affected_zones, polygon, geocode, geometry
    """
    issued_date = raw_alert.get("issued_date", "")
    valid_date = raw_alert.get("valid_date", "")
    
    # Filter out NOTE events (redirect notices, not real alerts)
    event_field = raw_alert.get("event", "").upper()
    if event_field == "NOTE":
        return None
    
    event_type = raw_alert.get("type", "").upper()
    subtype = raw_alert.get("subtype", "")
    
    # Use subtype as event name with Title Case (e.g. "thunderstorm advisory" → "Thunderstorm Advisory")
    if subtype:
        event = subtype.strip().title()
    else:
        event = CAP_EVENT_TYPE_MAP.get(event_field, event_field)
    
    severity = "Moderate"
    # Check subtype for severity hints first (e.g. "General Flood Advisory (Moderate)" → Moderate)
    subtype_lower = subtype.lower()
    if "(final)" in subtype_lower:
        severity = "Unknown"
    elif "extreme" in subtype_lower:
        severity = "Extreme"
    elif "severe" in subtype_lower:
        severity = "Severe"
    elif "moderate" in subtype_lower:
        severity = "Moderate"
    # Fall back to event-based defaults when subtype has no severity keyword
    elif event_field == "THUNDERSTORM":
        severity = "Moderate"
    elif event_field == "FLOOD":
        severity = "Moderate"
    elif event_field == "RAINFALL":
        severity = "Moderate"

    # Color-coded warning levels (RED/ORANGE/YELLOW) in the message override
    # the generic default above. Any color level → emergency (EMER); only
    # color-coded RED → full emergency (FULEMER).
    message = raw_alert.get("message", "")
    color_level = ""
    if event_field in ("RAINFALL", "FLOOD") and message:
        levels = {w.lower() for w in _WARNING_LEVEL_RE.findall(message)}
        if levels:
            if "red" in levels:
                color_level = "RED"
                severity = "Extreme"
            elif "orange" in levels:
                color_level = "ORANGE"
                severity = "Severe"
            elif "yellow" in levels:
                color_level = "YELLOW"
                severity = "Severe"

    urgency = "Expected"
    if "affecting" in raw_alert.get("provinces", {}):
        urgency = "Immediate"
    
    provinces = raw_alert.get("provinces", {})
    affected_zones = []
    polygons = []
    geocode = {}
    
    if isinstance(provinces, dict):
        for key, prov in provinces.items():
            if isinstance(prov, dict) and "province" in prov:
                prov_name = prov["province"]
                affected_zones.append(prov_name)
                if "geocode" in prov:
                    geocode[prov_name] = prov["geocode"]
                if "shape" in prov and prov["shape"]:
                    try:
                        import json
                        shape_data = json.loads(prov["shape"])
                        polygons.append({
                            "province": prov_name,
                            "type": prov.get("type", "unknown"),
                            "shape": shape_data
                        })
                    except Exception:
                        pass
    elif isinstance(provinces, list):
        for prov in provinces:
            if isinstance(prov, dict) and "province" in prov:
                prov_name = prov["province"]
                affected_zones.append(prov_name)
                if "geocode" in prov:
                    geocode[prov_name] = prov["geocode"]
                if "shape" in prov and prov["shape"]:
                    try:
                        import json
                        shape_data = json.loads(prov["shape"])
                        polygons.append({
                            "province": prov_name,
                            "type": prov.get("type", "unknown"),
                            "shape": shape_data
                        })
                    except Exception:
                        pass
    
    geometry = None
    if polygons:
        # Convert lat/lon to lon/lat for GeoJSON
        coords = []
        for p in polygons:
            shape_data = p["shape"]
            # Handle nested arrays (polygons with holes)
            if isinstance(shape_data[0], list) and isinstance(shape_data[0][0], list):
                # Already in [[[lon, lat], ...]] format - check if it's lat/lon
                # Assume it's [lat, lon] format and convert to [lon, lat]
                converted = []
                for ring in shape_data:
                    converted_ring = []
                    for coord in ring:
                        if len(coord) == 2:
                            converted_ring.append([coord[1], coord[0]])  # lat/lon -> lon/lat
                    converted.append(converted_ring)
                coords.append(converted)
            else:
                # Simple case
                converted = []
                for coord in shape_data:
                    if len(coord) == 2:
                        converted.append([coord[1], coord[0]])
                coords.append([converted])
        
        geometry = {
            "type": "MultiPolygon",
            "coordinates": coords
        }
    
    return {
        "id": raw_alert.get("identifier", ""),
        "headline": raw_alert.get("headline", ""),
        "description": raw_alert.get("message", ""),
        "instruction": raw_alert.get("optional_message", ""),
        "severity": severity,
        "urgency": urgency,
        "certainty": "Observed" if urgency == "Immediate" else "Likely",
        "event": event,
        "event_code": {"PAGASA": raw_alert.get("event", ""), "PAGASA_type": raw_alert.get("type", "")},
        "effective": issued_date,
        "expires": valid_date,
        "sender": raw_alert.get("published_by", "PAGASA"),
        "published_by": raw_alert.get("published_by", ""),
        "affected_zones": affected_zones,
        "provinces": provinces,
        "geocode": geocode,
        "parameters": {
            "PAGASA_identifier": [raw_alert.get("identifier", "")],
            "PAGASA_event": [raw_alert.get("event", "")],
            "PAGASA_type": [raw_alert.get("type", "")],
            "PAGASA_subtype": [raw_alert.get("subtype", "")],
            "PAGASA_published_by": [raw_alert.get("published_by", "")],
            "PAGASA_weather_systems": [raw_alert.get("weather_systems", "")],
            "PAGASA_expecting_intensity": [raw_alert.get("expecting_intensity", "")],
            "PAGASA_generated_message": [raw_alert.get("generated_message", "")],
            "PAGASA_color_level": [color_level] if color_level else [],
        },
        "geometry": geometry,
        "source": "PAGASA",
        "color_level": color_level,
    }

def is_emergency(alert):
    """Check if an alert qualifies as emergency (Extreme/Severe + Immediate/Expected)."""
    sev = ALERT_SEVERITY_ORDER.get(alert.get("severity", ""), 0)
    urg = ALERT_URGENCY_ORDER.get(alert.get("urgency", ""), 0)
    return sev >= 4 and urg >= 4

def severity_score(alert):
    """Return numeric severity+urgency score (max 10)."""
    sev = ALERT_SEVERITY_ORDER.get(alert.get("severity", ""), 0)
    urg = ALERT_URGENCY_ORDER.get(alert.get("urgency", ""), 0)
    return sev + urg

class PAGASAAlertFetcher(QObject):
    """Threaded worker that fetches alerts from PAGASA CAP API.
    
    Signals
    -------
    alerts_fetched : list[dict]
        Parsed alert dicts.
    error : str
        Error message on failure.
    """
    
    alerts_fetched = Signal(list)
    error = Signal(str)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False
    
    def cancel(self):
        self._cancelled = True
    
    def run(self):
        try:
            raw = fetch_pagasa_alerts()
            
            if self._cancelled:
                return
            
            parsed = [parse_pagasa_alert(a) for a in raw]
            parsed = [a for a in parsed if a is not None]
            parsed.sort(key=severity_score, reverse=True)
            self.alerts_fetched.emit(parsed)
            
        except requests.RequestException as e:
            if not self._cancelled:
                self.error.emit(f"PAGASA CAP API: {e}")
        except Exception as e:
            if not self._cancelled:
                self.error.emit(f"PAGASA CAP parse error: {e}")