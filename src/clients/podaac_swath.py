# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: clients/podaac_swath.py
# Description: Free, no-cost real-time ASCAT L2 swath client backed by the
#              NASA Physical Oceanography DAAC (PO.DAAC) cloud archive. PO.DAAC
#              mirrors the exact same KNMI / EUMETSAT OSI SAF near-real-time
#              orbit (swath) wind files that the KNMI FTP serves, with ~2-3 h
#              latency, but over plain HTTPS. Discovery (CMR granule search) is
#              fully anonymous; the actual .nc files require a free Earthdata
#              Login account (self-service at https://urs.earthdata.nasa.gov).
#              The account is stored as a normal FTP-style account named
#              "NASA Earthdata" with server "urs.earthdata.nasa.gov".
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
# Copyright (C) 2025-2026 PWARDS-weather (Pasacao Weather Atmospheric
# and Real-Time Data System)
# =============================================================================


import base64
import json
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

CMR_BASE = "https://cmr.earthdata.nasa.gov/search"
ARCHIVE_BASE = "https://archive.podaac.earthdata.nasa.gov"
URS_BASE = "https://urs.earthdata.nasa.gov"
PODAAC_BUCKET = "podaac-ops-cumulus-protected"
EARTHDATA_ACCOUNT_NAME = "NASA Earthdata"

# Free near-real-time OSI SAF ASCAT L2 swath collections mirrored by PO.DAAC.
PODAAC_COLLECTIONS = [
    {"short_name": "ASCATB-L2-25km", "satellite": "Metop-B", "sampling": "250"},
    {"short_name": "ASCATC-L2-25km", "satellite": "Metop-C", "sampling": "250"},
    {"short_name": "ASCATB-L2-Coastal", "satellite": "Metop-B", "sampling": "125"},
    {"short_name": "ASCATC-L2-Coastal", "satellite": "Metop-C", "sampling": "125"},
]

PODAAC_MAX_AGE_HOURS = 72.0
PODAAC_MAX_FILES = 8

_DEFAULT_REGION = {  # Western North Pacific + South China Sea
    "lat_min": 0.0,
    "lat_max": 40.0,
    "lon_min": 110.0,
    "lon_max": 170.0,
}


def find_earthdata_account(settings):
    """Return the stored account dict for NASA Earthdata, or None.

    Earthdata is an HTTPS / bearer-token login (PO.DAAC retired FTP), so it
    lives in the app's data-API (api_accounts) list keyed by host
    'urs.earthdata.nasa.gov'. Legacy entries stored under ftp_accounts are
    still honoured.
    """
    try:
        load = getattr(settings, "load_accounts", None)
        load_api = getattr(settings, "load_api_accounts", None)
        if load is None:
            return None
        api_accounts = load_api() if load_api is not None else []
        _, ftp_accounts = load()
    except Exception as e:
        log.error(f"[ASCAT] Could not read accounts: {e}")
        return None

    def matches(acc):
        host = (acc.get("host") or "").lower()
        server = (acc.get("server") or "").lower()
        name = (acc.get("name") or "").lower()
        if host in ("urs.earthdata.nasa.gov", "earthdata.nasa.gov"):
            return True
        if server in ("urs.earthdata.nasa.gov", "earthdata.nasa.gov"):
            return True
        return name == EARTHDATA_ACCOUNT_NAME.lower()

    for acc in list(api_accounts or []) + list(ftp_accounts or []):
        if matches(acc):
            return acc
    return None


def _get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _polygons_bbox(polygons):
    """Best-effort bounding box over a CMR granule polygon list.

    CMR returns each orbit ring as a single string of space-separated
    'lat lon' coordinate pairs (antimeridian-crossing orbits add a second,
    wrap-around ring). Parse every pair so the bbox is the real footprint
    envelope; a coarse/failed parse would otherwise make every orbit look as
    if it covered the requested region.
    """
    lats, lons = [], []
    for poly in polygons or []:
        for chunk in poly or []:
            tokens = chunk.replace(",", " ").split()
            if len(tokens) < 2 or len(tokens) % 2 != 0:
                continue
            try:
                nums = [float(t) for t in tokens]
            except ValueError:
                continue
            lats.extend(nums[0::2])
            lons.extend(nums[1::2])
    if not lats or not lons:
        return None
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    if lon_max - lon_min > 180.0:
        lon_min, lon_max = -180.0, 180.0
    return {"lat_min": lat_min, "lat_max": lat_max,
            "lon_min": lon_min, "lon_max": lon_max}


def _region_bboxes(region):
    """CMR ``bounding_box`` strings for a region dict, or None for no filter.

    Antimeridian-crossing boxes (lon_min > lon_max, e.g. a full-disk view)
    are split into the two physical boxes the swath readers imply:
    [lon_min, 180] plus [-180, lon_max].
    """
    if not region:
        return None
    lat_min = float(region.get("lat_min", _DEFAULT_REGION["lat_min"]))
    lat_max = float(region.get("lat_max", _DEFAULT_REGION["lat_max"]))
    lon_min = float(region.get("lon_min", _DEFAULT_REGION["lon_min"]))
    lon_max = float(region.get("lon_max", _DEFAULT_REGION["lon_max"]))
    if lon_min <= lon_max:
        return [f"{lon_min},{lat_min},{lon_max},{lat_max}"]
    boxes = [f"{lon_min},{lat_min},180.0,{lat_max}"]
    if lon_max > -180.0:
        boxes.append(f"-180.0,{lat_min},{lon_max},{lat_max}")
    return boxes


def _bbox_overlaps(bbox, region):
    if bbox is None:
        return True
    if region is None:
        return True
    lon_min = float(region.get("lon_min", _DEFAULT_REGION["lon_min"]))
    lon_max = float(region.get("lon_max", _DEFAULT_REGION["lon_max"]))
    lat_min = float(region.get("lat_min", _DEFAULT_REGION["lat_min"]))
    lat_max = float(region.get("lat_max", _DEFAULT_REGION["lat_max"]))
    if bbox["lat_max"] < lat_min or bbox["lat_min"] > lat_max:
        return False
    b_lon_min = bbox["lon_min"]
    b_lon_max = bbox["lon_max"]
    if lon_min > lon_max:
        # Region crosses the antimeridian (e.g. full geostationary disk):
        # covered by [lon_min, 180] + [-180, lon_max].
        return (b_lon_max >= lon_min - 0.5) or (b_lon_min <= lon_max + 0.5)
    if b_lon_max < lon_min or b_lon_min > lon_max:
        return False
    return True


def discover_orbits(region=None, max_age_hours=PODAAC_MAX_AGE_HOURS, progress=None):
    """Anonymous CMR search for recent ASCAT NRT orbit granules.

    Returns a list (newest first) of dicts with 'dt', 'satellite', 'sampling',
    'name' (…ovw.l2.nc), 'collection' and 'url'.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=max_age_hours)
    found = []
    for col in PODAAC_COLLECTIONS:
        params = [
            ("short_name", col["short_name"]),
            ("sort_key", "-start_date"),
            ("page_size", "100"),
            ("temporal", f"{start.strftime('%Y-%m-%dT%H:%M:%SZ')},{now.strftime('%Y-%m-%dT%H:%M:%SZ')}"),
        ]
        # Server-side spatial filter: return only orbits whose swath footprint
        # actually intersects the requested box (an ASCAT orbit is pole-to-pole
        # and its envelope spans every longitude, so a client-side bbox check
        # cannot tell which overpasses cover a small storm box).
        bboxes = _region_bboxes(region)
        if bboxes:
            for bb in bboxes:
                params.append(("bounding_box", bb))
        url = f"{CMR_BASE}/granules.json?{urllib.parse.urlencode(params)}"
        try:
            data = _get_json(url)
        except Exception as e:
            if progress:
                progress(f"  CMR error for {col['short_name']}: {e}")
            continue
        entries = (data.get("feed") or {}).get("entry") or []
        for ent in entries:
            granule = ent.get("producer_granule_id") or ""
            if not granule:
                continue
            t_start = ent.get("time_start", "")
            try:
                dt = datetime.fromisoformat(t_start.replace("Z", "+00:00"))
            except Exception:
                dt = None
            if dt is None:
                continue
            name = granule if granule.endswith(".nc") else granule + ".nc"
            found.append({
                "dt": dt,
                "satellite": col["satellite"],
                "sampling": col["sampling"],
                "name": name,
                "collection": col["short_name"],
                "url": f"{ARCHIVE_BASE}/{PODAAC_BUCKET}/{col['short_name']}/{name}",
                "_bbox": _polygons_bbox(ent.get("polygons")),
            })
        if progress:
            overlaps = sum(1 for f in found if f["collection"] == col["short_name"]
                           and _bbox_overlaps(f["_bbox"], region))
            progress(f"  {col['short_name']}: {len(entries)} orbit(s), "
                     f"{overlaps} overlap the basin region")
    found = [f for f in found if _bbox_overlaps(f["_bbox"], region)]
    for f in found:
        f.pop("_bbox", None)
    found.sort(key=lambda f: f["dt"], reverse=True)
    return found


def get_urs_token(username, password):
    """Return an Earthdata Login bearer token for the given account.

    Uses the documented User Tokens API: lists existing tokens first, then
    creates one via find_or_create_token if none exist.
    """
    basic = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    headers = {"Authorization": f"Basic {basic}"}

    try:
        tokens = _get_json(f"{URS_BASE}/api/users/tokens", headers)
        if isinstance(tokens, list) and tokens:
            token = tokens[0].get("access_token")
            if token:
                return token
    except Exception as e:
        log.warning(f"[ASCAT] URS list tokens failed: {e}")

    req = urllib.request.Request(
        f"{URS_BASE}/api/users/find_or_create_token",
        method="POST",
        headers={**headers, "Content-Length": "0"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    token = data.get("access_token") if isinstance(data, dict) else None
    if not token:
        raise RuntimeError("Earthdata Login could not issue a token for the account.")
    return token


def _download_with_token(url, token, dest_path, progress=None):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "MonWatch-UI-Cyclone/3.0 (PWARDS-weather; meteorology analysis)",
    })
    with urllib.request.urlopen(req, timeout=120) as r, open(dest_path, "wb") as fh:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)


def download_orbits(account, region=None, dest_dir=None, progress=None,
                    max_files=None):
    """Download the newest PO.DAAC ASCAT swath orbits that cover ``region``.

    Full-disk regions take enough consecutive orbit strips to sweep the disk
    left to right; focused basin/storm boxes only take the freshest passes.
    Returns the same info-dict shape as the KNMI swath downloader (including
    'wind_data'), or raises RuntimeError.
    """
    import numpy as np

    from .ascat import get_ascat_data_dir
    from .knmi_swath import (read_swath_wind_arrays, dedupe_orbits,
                             is_full_disk_region, FULL_DISK_MAX_FILES,
                             FOCUSED_MAX_FILES)

    if dest_dir is None:
        dest_dir = get_ascat_data_dir()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        progress("Discovering recent ASCAT orbits on NASA PO.DAAC (CMR)...")
    orbits = discover_orbits(region=region, progress=progress)
    if not orbits:
        raise RuntimeError(
            "No recent ASCAT orbits found on PO.DAAC for this region/time window."
        )

    # One file per overpass (drop 25-km/coastal duplicates), sized to the
    # number of passes that actually carry wind cells for the region, not just
    # the freshest handful: the newest orbits may sweep far from a small storm
    # box, so keep trying progressively older overpasses until the box is
    # covered (or a safety budget runs out).
    want = dedupe_orbits(orbits)
    if max_files is None:
        target = FULL_DISK_MAX_FILES if is_full_disk_region(region) \
            else FOCUSED_MAX_FILES
    else:
        target = max_files
    attempt_budget = min(len(want), target * 2 + 4)
    if not want:
        raise RuntimeError("No PO.DAAC ASCAT orbits selected for the region.")

    username = account.get("username", "")
    password = account.get("password", "")
    if not username or not password:
        raise RuntimeError(
            "NASA Earthdata account needs a username and password "
            "(free sign-up at https://urs.earthdata.nasa.gov)."
        )
    if progress:
        progress("Getting Earthdata Login token...")
    token = get_urs_token(username, password)

    pass_info = []
    combined = None
    for i, orb in enumerate(want[:attempt_budget]):
        if len(pass_info) >= target:
            break
        if progress:
            progress(f"[{i + 1}/{min(attempt_budget, len(want))}] "
                     f"Downloading {orb['name']} "
                     f"({orb['satellite']}, {orb['dt']:%Y-%m-%d %H:%MZ})...")
        local = dest_dir / orb["name"]
        # Orbit granules are immutable: reuse an already-downloaded file
        # instead of re-fetching it when only the crop region changed.
        if local.exists() and local.stat().st_size > 0:
            if progress:
                progress(f"  {orb['name']}: using cached local file")
        else:
            try:
                _download_with_token(orb["url"], token, local, progress=progress)
            except Exception as e:
                if progress:
                    progress(f"  {orb['name']}: download failed ({e}) — skipped")
                continue
        data = read_swath_wind_arrays(local, region)
        if data is None or len(data["lat"]) == 0:
            if progress:
                progress(f"  {orb['name']}: no wind data in region — skipped")
            try:
                local.unlink()
            except Exception:
                pass
            continue
        pass_info.append({
            "file_path": str(local),
            "dt": orb["dt"],
            "points": int(len(data["lat"])),
            "satellite": orb["satellite"],
            "sampling": orb["sampling"],
        })
        _keys = ["lat", "lon", "u", "v"]
        if "sat" in data:
            _keys.append("sat")
        if combined is None:
            combined = {k: np.asarray(data[k]) for k in _keys}
        else:
            for k in _keys:
                if k not in combined:
                    combined[k] = np.asarray(data[k])
                else:
                    combined[k] = np.concatenate([combined[k], np.asarray(data[k])])
        if progress:
            progress(f"  {orb['name']}: {len(data['lat'])} wind points in region")

    if not pass_info:
        raise RuntimeError(
            "Downloaded PO.DAAC orbits but none contained wind data in the "
            "requested region."
        )
    newest = max(p["dt"] for p in pass_info)
    return {
        "file_path": pass_info[0]["file_path"],
        "dataset_id": "NASA-PODAAC-ASCAT-L2-swath",
        "title": f"NASA PO.DAAC ASCAT swath ({len(pass_info)} pass(es))",
        "host": "archive.podaac.earthdata.nasa.gov",
        "time_str": newest.strftime("%Y-%m-%d %H:%M UTC"),
        "lat": [float(np.min(combined["lat"])), float(np.max(combined["lat"]))],
        "lon": [float(np.min(combined["lon"])), float(np.max(combined["lon"]))],
        "pass_count": len(pass_info),
        "passes": pass_info,
        "points": int(len(combined["lat"])),
        "variables": ["lat", "lon", "wind_speed", "wind_dir"],
        "u_var": "wind_speed->u",
        "v_var": "wind_speed->v",
        "wind_data": combined,
    }


class PODAACSwathDownloader(QObject):
    """Background worker that pulls real-time ASCAT swath passes from PO.DAAC."""

    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, account, region=None, dest_dir=None, parent=None):
        super().__init__(parent)
        self.account = account
        self.region = region
        self.dest_dir = dest_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = download_orbits(
                self.account,
                region=self.region,
                dest_dir=self.dest_dir,
                progress=lambda m: self.progress.emit(m),
            )
            if self._cancelled:
                self.finished.emit()
                return
            self.result.emit(info)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()
