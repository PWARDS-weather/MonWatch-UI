# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/weathergov.py
# Description: NWS Weather.gov alert client for US-based severe weather notifications.
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
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

API_BASE = "https://api.weather.gov"

USER_AGENT = "(MonWatch-Cyclone, weather.gov-alert-client@monwatch.app)"

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


def _headers():
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/geo+json",
        "Feature-Flags": "alert-certainty",
    }


def _get(url, params=None, timeout=30):
    resp = requests.get(url, headers=_headers(), params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def fetch_alerts(status="actual", severity=None, limit=50):
    """Fetch NWS alerts from api.weather.gov.

    Parameters
    ----------
    status : str
        'actual', 'exercise', 'system', 'test', or 'draft'
    severity : str or None
        One of: 'Extreme', 'Severe', 'Moderate', 'Minor'
    limit : int
        Max results (default 50, max 500).

    Returns
    -------
    list[dict]  Parsed alert features.
    """
    params = {"status": status, "limit": min(limit, 500)}
    if severity:
        params["severity"] = severity
    data = _get(f"{API_BASE}/alerts", params=params)
    return data.get("features", [])


def fetch_alerts_for_point(lat, lon, radius_km=40, limit=50):
    """Fetch alerts for a specific geographic point (e.g. typhoon position)."""
    params = {
        "point": f"{lat},{lon}",
        "limit": min(limit, 500),
    }
    data = _get(f"{API_BASE}/alerts", params=params)
    return data.get("features", [])


def fetch_alerts_for_zone(zone_id, limit=50):
    """Fetch alerts for a specific NWS zone (e.g. PHZ001 for Philippine waters)."""
    params = {"status": "actual", "limit": min(limit, 500)}
    data = _get(f"{API_BASE}/alerts/active/zone/{zone_id}", params=params)
    return data.get("features", [])


def fetch_active_alerts_for_marine():
    """Fetch active marine alerts using the NWS API region_type=marine filter."""
    data = _get(f"{API_BASE}/alerts/active", params={"region_type": "marine"})
    return data.get("features", [])


def parse_alert(feature):
    """Parse a raw alert GeoJSON feature into a structured dict.

    Returns keys: id, headline, description, severity, urgency,
                  certainty, event, effective, expires, sender,
                  affected_zones, polygon, geocode, geometry
    """
    props = feature.get("properties", {})
    return {
        "id": props.get("id", ""),
        "headline": props.get("headline", ""),
        "description": props.get("description", ""),
        "instruction": props.get("instruction", ""),
        "severity": props.get("severity", "Unknown"),
        "urgency": props.get("urgency", "Unknown"),
        "certainty": props.get("certainty", "Unknown"),
        "event": props.get("event", ""),
        "event_code": props.get("eventCode", {}),
        "effective": props.get("effective", ""),
        "expires": props.get("expires", ""),
        "sender": props.get("senderName", ""),
        "affected_zones": props.get("affectedZones", []),
        "geocode": props.get("geocode", {}),
        "parameters": props.get("parameters", {}),
        "geometry": feature.get("geometry"),
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


class WeatherGovAlertFetcher(QObject):
    """Threaded worker that fetches alerts from weather.gov API.

    Signals
    -------
    alerts_fetched : list[dict]
        Parsed alert dicts.
    error : str
        Error message on failure.
    """

    alerts_fetched = Signal(list)
    error = Signal(str)

    def __init__(self, lat=None, lon=None, zone=None, marine=False, parent=None):
        super().__init__(parent)
        self.lat = lat
        self.lon = lon
        self.zone = zone
        self.marine = marine
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            raw = []
            if self.marine:
                raw = fetch_active_alerts_for_marine()
            elif self.zone:
                raw = fetch_alerts_for_zone(self.zone)
            elif self.lat is not None and self.lon is not None:
                raw = fetch_alerts_for_point(self.lat, self.lon)
            else:
                raw = fetch_alerts(severity="Severe")

            if self._cancelled:
                return

            parsed = [parse_alert(f) for f in raw]
            parsed.sort(key=severity_score, reverse=True)
            self.alerts_fetched.emit(parsed)

        except requests.RequestException as e:
            if not self._cancelled:
                self.error.emit(f"weather.gov API: {e}")
        except Exception as e:
            if not self._cancelled:
                self.error.emit(f"weather.gov parse error: {e}")
