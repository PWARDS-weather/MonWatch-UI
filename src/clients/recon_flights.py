# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/recon_flights.py
# Description: Live flight-data client for weather reconnaissance aircraft
#   (NOAA Hurricane Hunters, USAF WC-130J Typhoon Hunters and the NOAA
#   research fleet). Primary source: adsb.fi open data (free, no key, global
#   ADS-B/MLAT feed); fallback source: OpenSky Network anonymous state vectors.
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
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

# adsb.fi open data (public, no API key, ~1 request/second).
ADSB_FI_BASE = "https://opendata.adsb.fi/api/v2"

# OpenSky Network anonymous fallback (limited daily credits; use sparingly).
OPENSKY_ALL_URL = "https://opensky-network.org/api/states/all"

USER_AGENT = "MonWatch-Cyclone/recon-tracker (pwards.sci@gmail.com)"

# Minimum gap between upstream requests so the public APIs do not rate-limit
# an entire live-scan run (adsb.fi public limit is 1 request/second).
_MIN_REQUEST_GAP = 1.1
_last_request_ts = [0.0]


def _pace():
    """Space outgoing requests so public rate limits are respected."""
    dt = time.time() - _last_request_ts[0]
    if dt < _MIN_REQUEST_GAP:
        time.sleep(_MIN_REQUEST_GAP - dt)
    _last_request_ts[0] = time.time()


def _headers():
    return {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }


# ---------------------------------------------------------------------------
# Known weather-reconnaissance / research aircraft.
# Map keys are the FAA registration (N-number); NOAA hurricane hunters and
# the research fleet all use the "...RF" registration family.
# ---------------------------------------------------------------------------
NOAA_AIRCRAFT = {
    "N42RF": {"callsign": "NOAA42", "label": "NOAA42 'Kermit' (WP-3D Orion)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N43RF": {"callsign": "NOAA43", "label": "NOAA43 'Miss Piggy' (WP-3D Orion)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N49RF": {"callsign": "NOAA49", "label": "NOAA49 'Gonzo' (G-IV)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N69RF": {"callsign": "NOAA69", "label": "NOAA69 (Gulfstream G550)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N65RF": {"callsign": "NOAA65", "label": "NOAA65 (King Air 360CER)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N67RF": {"callsign": "NOAA67", "label": "NOAA67 (King Air 350CER)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N68RF": {"callsign": "NOAA68", "label": "NOAA68 (King Air 350CER)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N46RF": {"callsign": "NOAA46", "label": "NOAA46 (Twin Otter)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N48RF": {"callsign": "NOAA48", "label": "NOAA48 (Twin Otter)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N56RF": {"callsign": "NOAA56", "label": "NOAA56 (Twin Otter)", "country": "USA (NOAA)", "color": "#FF9800"},
    "N57RF": {"callsign": "NOAA57", "label": "NOAA57 (Twin Otter)", "country": "USA (NOAA)", "color": "#FF9800"},
}

# US Air Force / Air Force Reserve WC-130J "Hurricane Hunter" fleet of the
# 53rd Weather Reconnaissance Squadron (callsign family "TEAL##").
TEAL_WC130_RANGE = range(70, 90)

# NASA hurricane & storm research aircraft (Global Hawk, P-3 Orion, DC-8,
# WB-57, ER-2). These fly tropical-cyclone research missions in both basins.
NASA_AIRCRAFT = {
    "N426NA": {"callsign": "NASA426", "label": "NASA426 (P-3B Orion)", "country": "USA (NASA)", "color": "#9C27B0"},
    "N427NA": {"callsign": "NASA427", "label": "NASA427 (P-3B Orion)", "country": "USA (NASA)", "color": "#9C27B0"},
    "N817NA": {"callsign": "NASA817", "label": "NASA817 (DC-8-72)", "country": "USA (NASA)", "color": "#9C27B0"},
    "N808NA": {"callsign": "NASA808", "label": "NASA808 (WB-57F)", "country": "USA (NASA)", "color": "#9C27B0"},
    "N909NA": {"callsign": "NASA909", "label": "NASA909 (ER-2)", "country": "USA (NASA)", "color": "#9C27B0"},
    "N910NA": {"callsign": "NASA910", "label": "NASA910 (ER-2)", "country": "USA (NASA)", "color": "#9C27B0"},
    "N971NA": {"callsign": "NASA971", "label": "NASA971 (Global Hawk)", "country": "USA (NASA)", "color": "#9C27B0"},
    "N972NA": {"callsign": "NASA972", "label": "NASA972 (Global Hawk)", "country": "USA (NASA)", "color": "#9C27B0"},
}

# Everything we know by registration (NOAA research fleet + NASA airborne science).
ALL_RECON_AIRCRAFT = {**NOAA_AIRCRAFT, **NASA_AIRCRAFT}


def default_callsign_candidates():
    """Return every likely recon callsign so targeted queries stay small."""
    calls = [info["callsign"] for info in ALL_RECON_AIRCRAFT.values()]
    calls += [f"TEAL{n}" for n in TEAL_WC130_RANGE]
    return list(dict.fromkeys(calls))


def _known_profile(reg, callsign):
    if reg:
        prof = ALL_RECON_AIRCRAFT.get(reg.upper())
        if prof:
            return prof
    for r, prof in ALL_RECON_AIRCRAFT.items():
        if prof["callsign"] and prof["callsign"].upper() == (callsign or "").upper():
            return prof
    return None


def _is_recon_ac(ac):
    """Decide whether an upstream aircraft object is a weather-recon aircraft.

    Matches the NOAA research fleet, NASA airborne-science aircraft and the
    USAF/Reserve WC-130J "TEAL" Hurricane Hunters. Military aircraft from
    other nations are NOT force-included -- their C-130/P-3 transports are
    indistinguishable from recon flights on ADS-B, so only the curated
    registration/callsign lists are trusted for them.
    """
    cs = (ac.get("callsign") or ac.get("flight") or "").strip()
    reg = (ac.get("registration") or "").strip()
    typ = (ac.get("type") or "").strip()

    if re.match(r"^NOAA\d+$", cs, re.IGNORECASE):
        return True
    if re.match(r"^TEAL\d*$", cs, re.IGNORECASE):
        return True
    if re.match(r"^NASA\d{2,3}$", cs, re.IGNORECASE):
        return True
    if reg and re.match(r"^N\d{2,3}RF$", reg, re.IGNORECASE):
        return True
    if _known_profile(reg, cs):
        return True
    # USAF WC-130J weather-recon aircraft transiting with generic AF callsigns.
    if re.match(r"^AF\d{2,5}\s*$", cs, re.IGNORECASE) and typ.upper() in (
        "C30J", "C-130J", "C130J", "WC130", "WC-130J", "WC-130H", "C-130H", "C130H"
    ):
        return True
    # Air Force Reserve Command WC-130J (e.g. "AFRC631") on C-130J types.
    if re.match(r"^AFRC\d{3}$", cs, re.IGNORECASE) and typ.upper() in (
        "C30J", "C-130J", "C130J", "WC130", "WC-130J", "WC-130H"
    ):
        return True
    return False


def _label_for(reg, callsign, typ):
    prof = _known_profile(reg, callsign)
    if prof:
        return prof["label"]
    cs = (callsign or "").strip()
    if re.match(r"^TEAL\d*$", cs, re.IGNORECASE):
        return f"{cs.upper()} (WC-130J Hurricane Hunter)"
    if reg and re.match(r"^N\d{2,3}RF$", reg, re.IGNORECASE):
        return f"{reg.upper()} (NOAA research aircraft)"
    if re.match(r"^NASA\d{2,3}$", cs, re.IGNORECASE):
        return f"{cs.upper()} (NASA research aircraft)"
    return f"{cs or reg or 'Unknown'}" + (f" ({typ})" if typ else "")


def _country_for(reg, callsign, typ):
    """Country/operator string used by the live recon status window."""
    prof = _known_profile(reg, callsign)
    if prof:
        return prof.get("country", "USA")
    cs = (callsign or "").upper()
    if cs.startswith("TEAL") or (cs.startswith("AF") and typ.upper().startswith(("C30J", "C-130", "C130", "WC"))):
        return "USA (USAF)"
    if cs.startswith("AFRC"):
        return "USA (USAF Reserve)"
    return "USA"


def _normalize_adsbfi(ac):
    """Normalize an adsb.fi aircraft object into a MonWatch recon record."""
    reg = (ac.get("r") or "").strip()
    callsign = (ac.get("flight") or "").strip()
    typ = (ac.get("t") or "").strip()
    lat = ac.get("lat")
    lon = ac.get("lon")
    alt = ac.get("alt_baro")
    if isinstance(alt, str) and alt.strip().lower() == "ground":
        alt_ft = None
        on_ground = True
    else:
        alt_ft = float(alt) if alt is not None else None
        on_ground = bool(ac.get("on_ground") or False)

    hex_id = (ac.get("hex") or "").lower().lstrip("~")
    record = {
        "hex": hex_id,
        "callsign": callsign or reg,
        "registration": reg,
        "type": typ,
        "lat": float(lat) if lat is not None else None,
        "lon": float(lon) if lon is not None else None,
        "alt_ft": alt_ft,
        "speed_kt": float(ac["gs"]) if ac.get("gs") is not None else None,
        "track_deg": float(ac["track"]) if ac.get("track") is not None else None,
        "seen": float(ac["seen"]) if ac.get("seen") is not None else None,
        "on_ground": on_ground,
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    record["label"] = _label_for(reg, callsign, typ)
    record["country"] = _country_for(reg, callsign, typ)
    return record


def fetch_adsbfi_recon(callsigns=None, include_mil=True, timeout=30):
    """Fetch live recon aircraft from adsb.fi.

    Two cheap queries are combined: a targeted ``callsign`` lookup covering
    every known NOAA/TEAL callsign, plus a military scan that catches WC-130J
    weather aircraft whose callsigns vary from the curated list.
    """
    callsigns = callsigns or default_callsign_candidates()
    seen = {}

    def _add(raw):
        if not _is_recon_ac(raw):
            return
        rec = _normalize_adsbfi(raw)
        if rec["hex"] or rec["lat"] is not None:
            seen.setdefault(rec["hex"] or rec["callsign"], rec)

    query = ",".join(sorted(set(callsigns)))
    if query:
        _pace()
        try:
            resp = requests.get(
                f"{ADSB_FI_BASE}/callsign/{query}",
                headers=_headers(), timeout=timeout,
            )
            if resp.status_code == 200:
                for raw in (resp.json().get("ac") or []):
                    _add(raw)
            elif resp.status_code == 429:
                log.warning("adsb.fi rate-limited during callsign query")
        except requests.RequestException as exc:
            log.warning("adsb.fi callsign query failed: %s", exc)

    if include_mil:
        _pace()
        try:
            resp = requests.get(
                f"{ADSB_FI_BASE}/mil",
                headers=_headers(), timeout=timeout,
            )
            if resp.status_code == 200:
                for raw in (resp.json().get("ac") or []):
                    _add(raw)
            elif resp.status_code == 429:
                log.warning("adsb.fi rate-limited during military scan")
        except requests.RequestException as exc:
            log.warning("adsb.fi military scan failed: %s", exc)

    return list(seen.values())


def fetch_opensky_recon(timeout=30):
    """Fallback: fetch recon aircraft from OpenSky Network anonymous feed.

    OpenSky returns raw state vectors; NOAA/TEAL callsigns are filtered
    locally. This is kept as a fallback because anonymous OpenSky accounts
    have a daily credit budget.
    """
    _pace()
    resp = requests.get(OPENSKY_ALL_URL, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    now = datetime.now(timezone.utc)
    out = []
    for s in data.get("states") or []:
        if not s or len(s) < 11:
            continue
        hex_id = (s[0] or "").lower()
        callsign = (s[1] or "").strip()
        lon, lat = s[5], s[6]
        alt_m = s[7]
        velocity = s[9]
        track = s[10]
        if not _is_recon_ac({"callsign": callsign}):
            continue
        if lat is None or lon is None:
            continue
        out.append({
            "hex": hex_id,
            "callsign": callsign,
            "registration": "",
            "type": "",
            "lat": float(lat),
            "lon": float(lon),
            "alt_ft": (float(alt_m) * 3.280839895) if alt_m is not None else None,
            "speed_kt": (float(velocity) * 1.943844492) if velocity is not None else None,
            "track_deg": float(track) if track is not None else None,
            "seen": 0,
            "on_ground": bool(s[8]),
            "updated": now.isoformat(timespec="seconds"),
        })
        out[-1]["label"] = _label_for("", callsign, "")
        out[-1]["country"] = _country_for("", callsign, "")

    # Best effort de-duplication on (callsign, lat, lon).
    unique = {}
    for rec in out:
        unique.setdefault((rec["callsign"], round(rec["lat"], 3), round(rec["lon"], 3)), rec)
    return list(unique.values())


def fetch_recon_aircraft(timeout=30):
    """Fetch the latest recon-aircraft positions.

    Returns ``(aircraft_list, source_name)``. adsb.fi is used first; OpenSky
    is the offline/non-answering fallback.
    """
    try:
        aircraft = fetch_adsbfi_recon(timeout=timeout)
        return aircraft, "adsb.fi"
    except Exception as exc:
        log.warning("adsb.fi unavailable (%s); falling back to OpenSky", exc)
    try:
        aircraft = fetch_opensky_recon(timeout=timeout)
        return aircraft, "OpenSky"
    except Exception as exc:
        log.warning("OpenSky fallback failed: %s", exc)
        return [], "unavailable"