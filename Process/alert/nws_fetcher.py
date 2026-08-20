"""
Standalone NWS weather.gov alert fetcher for subprocess use.
No PySide6 dependency - pure requests-based API client.
"""

import logging
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

API_BASE = "https://api.weather.gov"
USER_AGENT = "(MonWatch-Cyclone, weather.gov-alert-client@monwatch.app)"

ALERT_SEVERITY_ORDER = {
    "Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1,
}
ALERT_URGENCY_ORDER = {
    "Immediate": 5, "Expected": 4, "Future": 3, "Past": 2, "Unknown": 1,
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
    params = {"status": status, "limit": min(limit, 500)}
    if severity:
        params["severity"] = severity
    data = _get(f"{API_BASE}/alerts", params=params)
    return data.get("features", [])


def fetch_alerts_for_point(lat, lon, radius_km=40, limit=50):
    params = {"point": f"{lat},{lon}", "limit": min(limit, 500)}
    data = _get(f"{API_BASE}/alerts", params=params)
    return data.get("features", [])


def fetch_alerts_for_zone(zone_id, limit=50):
    params = {"status": "actual", "limit": min(limit, 500)}
    data = _get(f"{API_BASE}/alerts/active/zone/{zone_id}", params=params)
    return data.get("features", [])


def fetch_active_alerts_for_marine():
    data = _get(f"{API_BASE}/alerts/active", params={"region_type": "marine"})
    return data.get("features", [])


def parse_alert(feature):
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
    sev = ALERT_SEVERITY_ORDER.get(alert.get("severity", ""), 0)
    urg = ALERT_URGENCY_ORDER.get(alert.get("urgency", ""), 0)
    return sev >= 4 and urg >= 4


def severity_score(alert):
    sev = ALERT_SEVERITY_ORDER.get(alert.get("severity", ""), 0)
    urg = ALERT_URGENCY_ORDER.get(alert.get("urgency", ""), 0)
    return sev + urg
