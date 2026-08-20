"""
Standalone PAGASA CAP alert fetcher for subprocess use.
No PySide6 dependency - pure requests-based API client.
"""

import json
import logging
import re
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

PAGASA_CAP_API_URL = "https://www.panahon.gov.ph/api/v1/cap-alerts"

_WARNING_LEVEL_RE = re.compile(r"\b(red|orange|yellow)\s+warning\b", re.IGNORECASE)

ALERT_SEVERITY_ORDER = {
    "Extreme": 5, "Severe": 4, "Moderate": 3, "Minor": 2, "Unknown": 1,
}
ALERT_URGENCY_ORDER = {
    "Immediate": 5, "Expected": 4, "Future": 3, "Past": 2, "Unknown": 1,
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
    data = _get(PAGASA_CAP_API_URL)
    if not data.get("success", False):
        log.warning("PAGASA API returned success=false")
        return []
    return data.get("data", {}).get("alert_data", [])


def parse_pagasa_alert(raw_alert):
    issued_date = raw_alert.get("issued_date", "")
    valid_date = raw_alert.get("valid_date", "")

    event_field = raw_alert.get("event", "").upper()
    if event_field == "NOTE":
        return None

    event_type = raw_alert.get("type", "").upper()
    subtype = raw_alert.get("subtype", "")

    if subtype:
        event = subtype.strip().title()
    else:
        event = CAP_EVENT_TYPE_MAP.get(event_field, event_field)

    severity = "Moderate"
    subtype_lower = subtype.lower()
    if "(final)" in subtype_lower:
        severity = "Unknown"
    elif "extreme" in subtype_lower:
        severity = "Extreme"
    elif "severe" in subtype_lower:
        severity = "Severe"
    elif "moderate" in subtype_lower:
        severity = "Moderate"
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
                        shape_data = json.loads(prov["shape"])
                        polygons.append({
                            "province": prov_name,
                            "type": prov.get("type", "unknown"),
                            "shape": shape_data,
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
                        shape_data = json.loads(prov["shape"])
                        polygons.append({
                            "province": prov_name,
                            "type": prov.get("type", "unknown"),
                            "shape": shape_data,
                        })
                    except Exception:
                        pass

    geometry = None
    if polygons:
        coords = []
        for p in polygons:
            shape_data = p["shape"]
            if isinstance(shape_data[0], list) and isinstance(shape_data[0][0], list):
                converted = []
                for ring in shape_data:
                    converted_ring = []
                    for coord in ring:
                        if len(coord) == 2:
                            converted_ring.append([coord[1], coord[0]])
                    converted.append(converted_ring)
                coords.append(converted)
            else:
                converted = []
                for coord in shape_data:
                    if len(coord) == 2:
                        converted.append([coord[1], coord[0]])
                coords.append([converted])

        geometry = {
            "type": "MultiPolygon",
            "coordinates": coords,
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
        "event_code": {
            "PAGASA": raw_alert.get("event", ""),
            "PAGASA_type": raw_alert.get("type", ""),
        },
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
    sev = ALERT_SEVERITY_ORDER.get(alert.get("severity", ""), 0)
    urg = ALERT_URGENCY_ORDER.get(alert.get("urgency", ""), 0)
    return sev >= 4 and urg >= 4


def severity_score(alert):
    sev = ALERT_SEVERITY_ORDER.get(alert.get("severity", ""), 0)
    urg = ALERT_URGENCY_ORDER.get(alert.get("urgency", ""), 0)
    return sev + urg
