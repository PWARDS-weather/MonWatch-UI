# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/agency_tracker.py
# Description: Multi-agency storm tracking system for consolidating forecasts from different meteorological agencies.
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
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if getattr(sys, 'frozen', False):
    _top_dir = Path(sys.executable).resolve().parent
else:
    _top_dir = Path(__file__).resolve().parent.parent.parent

TRACKER_PATH = _top_dir / "public" / "update_tracker.json"

AGENCY_SCHEDULES = {
    "nhc":    [3, 9, 15, 21],
    "jma":    [0, 3, 6, 9, 12, 15, 18, 21],
    "jtwc":   [3, 9, 15, 21],
    "pagasa": [5, 11, 17, 23],
    "cwa":    [0, 3, 6, 9, 12, 15, 18, 21],
}


def _default_tracker():
    return {a: {"last_update": None, "last_slot_hour": None} for a in AGENCY_SCHEDULES}


def load_tracker():
    if TRACKER_PATH.exists():
        try:
            return json.loads(TRACKER_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _default_tracker()


def save_tracker(data):
    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACKER_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_current_slot(agency):
    schedule = AGENCY_SCHEDULES.get(agency)
    if not schedule:
        return None, None
    now = datetime.now(timezone.utc)
    h = now.hour
    sorted_h = sorted(schedule)
    slot = None
    for s in reversed(sorted_h):
        if s <= h:
            slot = s
            break
    if slot is None:
        slot = sorted_h[-1]
    today = now.strftime("%Y-%m-%d")
    if slot > h:
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        return slot, yesterday
    return slot, today


def should_update(agency):
    entry = load_tracker().get(agency, {})
    last_update = entry.get("last_update")
    last_slot = entry.get("last_slot_hour")
    slot, slot_date = get_current_slot(agency)
    if slot is None:
        return True
    if not last_update or last_slot is None:
        return True
    if last_slot != slot:
        return True
    try:
        last_dt = datetime.fromisoformat(last_update.replace("Z", "+00:00"))
        if last_dt.strftime("%Y-%m-%d") != slot_date:
            return True
        return False
    except Exception:
        return True


def mark_updated(agency):
    data = load_tracker()
    now = datetime.now(timezone.utc)
    slot, _ = get_current_slot(agency)
    data[agency] = {
        "last_update": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_slot_hour": slot
    }
    save_tracker(data)
