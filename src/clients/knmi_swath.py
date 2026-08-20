# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: clients/knmi_swath.py
# Description: Real-time ASCAT L2 swath client for the KNMI / EUMETSAT OSI SAF
#              scatterometer centre. Pulls the true near-real-time ASCAT orbit
#              (swath) wind files from the OSI SAF FTP server (ftppro.knmi.nl),
#              converts the per-wind-vector-cell speed/direction into u/v, and
#              hands them to the app's wind-overlay pipeline. Unlike the
#              gridded CCMP/blended analyses these are genuine swath strips.
#
#              The OSI SAF FTP requires free credentials, which are stored as a
#              normal FTP account in the app's Accounts window (name
#              "KNMI OSI SAF", server "ftppro.knmi.nl", email scat@knmi.nl to
#              request an account).
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


import ftplib
import gzip
import logging
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

KNMI_FTP_HOST = "ftppro.knmi.nl"
KNMI_FTP_PORT = 21
KNMI_ACCOUNT_NAME = "KNMI OSI SAF"

# Product directories on the OSI SAF FTP (near-real-time files, last ~3 days).
# The 25-km product directory names are confirmed by the OSI SAF product pages;
# the coastal directory names are probed defensively in order.
KNMI_PRODUCT_DIRS = {
    "Metop-B / ASCAT-B 25 km (OSI-102-b)": ["ascat_b_osi"],
    "Metop-C / ASCAT-C 25 km (OSI-102-c)": ["ascat_c_osi"],
    "Metop-B / ASCAT-B Coastal 12.5 km (OSI-104-b)": [
        "ascat_b_osi_co", "ascat_b_co_osi", "ascat_b_co"],
    "Metop-C / ASCAT-C Coastal 12.5 km (OSI-104-c)": [
        "ascat_c_osi_co", "ascat_c_co_osi", "ascat_c_co"],
}

# e.g. ascat_20260814_010011_metopb_00000_eps_o_250_1135_ovw.l2.nc(.gz)
_FILE_RE = re.compile(
    r"ascat_(\d{8})_(\d{6})_metop(b|c)_\d+_eps_(o|t)_(250|125|coa)(?:_\d+)*_ovw\.l2\.nc(?:\.gz)?$",
    re.I,
)

# Only look back this far for passes on the FTP (it keeps ~3 days anyway).
SWATH_MAX_AGE_HOURS = 72.0
# Max orbit files to download before giving up on region coverage.
SWATH_MAX_FILES = 8

# Per-region pass budgets.  Full-disk views want enough consecutive overpass
# strips to sweep the disk left to right (~12 orbits ~ 25° apart across a 160°
# geo disk), while focused basin/storm boxes only need the freshest passes to
# stay current.
FULL_DISK_MAX_FILES = 12
FOCUSED_MAX_FILES = 4

# A region is treated as "full disk" when either dimension spans most of a
# hemisphere (geo disk ~160° across).
FULL_DISK_MIN_LAT_SPAN = 120.0
FULL_DISK_MIN_LON_SPAN = 120.0

_DEFAULT_REGION = {  # Western North Pacific + South China Sea
    "lat_min": 0.0,
    "lat_max": 40.0,
    "lon_min": 110.0,
    "lon_max": 170.0,
}


def find_knmi_ftp_account(settings):
    """Return the stored FTP account dict for the KNMI OSI SAF, or None."""
    try:
        load = getattr(settings, "load_accounts", None)
        if load is None:
            return None
        _, ftp_accounts = load()
    except Exception as e:
        log.error(f"[ASCAT] Could not read FTP accounts: {e}")
        return None
    host = KNMI_FTP_HOST.lower()
    for acc in ftp_accounts or []:
        server = (acc.get("server") or "").lower()
        name = (acc.get("name") or "").lower()
        if server in (host, "ftp://" + host, "ftps://" + host) or name == KNMI_ACCOUNT_NAME.lower():
            return acc
    return None


def _connect(acc):
    ftp = ftplib.FTP()
    ftp.connect(
        acc.get("server", KNMI_FTP_HOST),
        int(acc.get("port", KNMI_FTP_PORT)),
        timeout=30,
    )
    ftp.login(acc.get("username", ""), acc.get("password", ""))
    ftp.set_pasv(acc.get("passive", True))
    ftp.sendcmd("TYPE I")
    return ftp


def _close(ftp):
    if ftp is None:
        return
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


def _parse_file_name(fname):
    m = _FILE_RE.match(fname.strip())
    if not m:
        return None
    ymd, hms, sat, srv, samp = m.groups()
    try:
        dt = datetime(
            int(ymd[0:4]), int(ymd[4:6]), int(ymd[6:8]),
            int(hms[0:2]), int(hms[2:4]), int(hms[4:6]),
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return {
        "dt": dt,
        "satellite": f"Metop-{sat.upper()}",
        "service": "operational" if srv.lower() == "o" else "test",
        "sampling": samp,
    }


def region_spread(region):
    """Return (lat_span, lon_span) in degrees for a region dict, unwrapping
    antimeridian-crossing longitudes (lon_min > lon_max)."""
    region = region or _DEFAULT_REGION
    lat_min = float(region.get("lat_min", -90.0))
    lat_max = float(region.get("lat_max", 90.0))
    lon_min = float(region.get("lon_min", -180.0))
    lon_max = float(region.get("lon_max", 180.0))
    lat_span = lat_max - lat_min
    lon_span = (lon_max - lon_min) if lon_min <= lon_max else (lon_max - lon_min + 360.0)
    return max(0.0, lat_span), max(0.0, lon_span)


def is_full_disk_region(region, min_lat_span=FULL_DISK_MIN_LAT_SPAN,
                        min_lon_span=FULL_DISK_MIN_LON_SPAN):
    """True when ``region`` is a wide full-disk-like box (covers most of a
    geostationary disk) rather than a focused basin/storm box."""
    lat_span, lon_span = region_spread(region)
    return lat_span >= min_lat_span or lon_span >= min_lon_span


def dedupe_orbits(files):
    """Reduce orbit files to one per (satellite, overpass minute), preferring
    the 25-km open-ocean file over the coastal one; returns newest first."""
    def stamp(f):
        return str(f.get("satellite", "")) + "|" + (
            f.get("dt") or datetime.min.replace(tzinfo=timezone.utc)
        ).strftime("%Y%m%d%H%M")

    def is_coastal(f):
        return (str(f.get("sampling", "")).lower() == "coa"
                or "coa" in str(f.get("name", "")).lower())

    seen = set()
    out = []
    for f in sorted(files, key=is_coastal):  # non-coastal first wins collisions
        k = stamp(f)
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    out.sort(key=lambda f: f.get("dt") or datetime.min.replace(tzinfo=timezone.utc),
             reverse=True)
    return out


def select_local_swath_files(file_paths, region=None):
    """Pick which cached swath .nc files to re-crop for ``region`` with no
    network access: every distinct cached overpass for full-disk views, but
    only the freshest few for focused boxes. Returned list is newest first."""
    paths = [str(p) for p in (file_paths or [])]
    by_orbit = {}
    for raw in paths:
        meta = _parse_file_name(Path(raw).name)
        if meta is None:
            continue
        key = (meta["satellite"], meta["dt"].strftime("%Y%m%d%H%M"))
        prefer = meta["sampling"] != "coa"
        old = by_orbit.get(key)
        if old is None or (prefer and not old[0]):
            by_orbit[key] = (prefer, meta["dt"], raw)
    ranked = sorted(by_orbit.values(), key=lambda x: x[1], reverse=True)
    if is_full_disk_region(region):
        return [raw for _, _, raw in ranked]
    return [raw for _, _, raw in ranked[:FOCUSED_MAX_FILES]]


def list_recent_swath_files(acc, max_age_hours=SWATH_MAX_AGE_HOURS, progress=None):
    """List recent L2 swath orbit files across KNMI product dirs, newest first.

    Returns (found, errors) where ``found`` is a list of dicts with 'dt',
    'satellite', 'sampling', 'name' and 'path', sorted newest first.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max_age_hours)
    found = []
    errors = []
    ftp = None
    try:
        ftp = _connect(acc)
        for product, dirs in KNMI_PRODUCT_DIRS.items():
            remote_dir = None
            entries = []
            for d in dirs:
                try:
                    entries = ftp.nlst(d) or []
                    remote_dir = d
                    break
                except ftplib.all_errors:
                    continue
            if remote_dir is None:
                errors.append(f"{product}: product directory not found")
                continue
            for name in entries:
                base = Path(name).name
                meta = _parse_file_name(base)
                if meta is None:
                    continue
                if meta["dt"] < cutoff:
                    continue
                found.append({
                    "dt": meta["dt"],
                    "satellite": meta["satellite"],
                    "sampling": meta["sampling"],
                    "name": base,
                    "path": f"{remote_dir}/{base}",
                })
            if progress:
                progress(f"  {product}: {sum(1 for f in found if f['path'].startswith(remote_dir))} recent file(s)")
    except ftplib.all_errors as e:
        errors.append(f"FTP error: {e}")
    finally:
        _close(ftp)
    found.sort(key=lambda f: f["dt"], reverse=True)
    return found, errors


def _download_file(ftp, remote_path, dest_dir):
    name = Path(remote_path).name
    local_raw = dest_dir / name
    with open(local_raw, "wb") as fh:
        ftp.retrbinary("RETR " + remote_path, fh.write)
    if name.endswith(".gz"):
        local_nc = dest_dir / name[:-3]
        with gzip.open(local_raw, "rb") as gz, open(local_nc, "wb") as out:
            shutil.copyfileobj(gz, out)
        local_raw.unlink(missing_ok=True)
        return local_nc
    return local_raw


def read_swath_wind_arrays(file_path, region=None):
    """Read an OSI SAF L2 swath NetCDF into flat point arrays.

    Returns a dict with 'lat', 'lon', 'u', 'v' arrays for cells inside
    ``region`` that pass the quality filter, or None when unusable.
    """
    import numpy as np
    import xarray as xr

    region = region or {}
    lon_min = float(region.get("lon_min", _DEFAULT_REGION["lon_min"]))
    lon_max = float(region.get("lon_max", _DEFAULT_REGION["lon_max"]))
    lat_min = float(region.get("lat_min", _DEFAULT_REGION["lat_min"]))
    lat_max = float(region.get("lat_max", _DEFAULT_REGION["lat_max"]))

    with xr.open_dataset(file_path) as ds:
        lat_v = next((v for v in ("lat", "latitude") if v in ds.variables), None)
        lon_v = next((v for v in ("lon", "longitude") if v in ds.variables), None)
        if lat_v is None or lon_v is None:
            return None
        lat = np.asarray(ds[lat_v].values, dtype=np.float64)
        lon = np.asarray(ds[lon_v].values, dtype=np.float64)
        if lat.ndim == 1 and lon.ndim == 1:
            lat, lon = np.meshgrid(lat, lon, indexing="ij")

        ws_v = next((v for v in ("wind_speed", "ws", "model_speed") if v in ds.variables), None)
        wd_v = next((v for v in ("wind_dir", "wd", "model_dir") if v in ds.variables), None)
        if ws_v is None or wd_v is None:
            return None
        ws = np.asarray(ds[ws_v].values, dtype=np.float64)
        wd = np.asarray(ds[wd_v].values, dtype=np.float64)
        # Drop leading singleton dims (e.g. time) but never collapse below the
        # 2D wind-vector-cell grid.
        while ws.ndim > lat.ndim:
            ws = ws[0]
            wd = wd[0]
        if ws.ndim != lat.ndim or ws.shape != lat.shape:
            return None

        qf = None
        if "wvc_quality_flag" in ds.variables:
            qf = np.asarray(ds["wvc_quality_flag"].values)
            while qf.ndim > ws.ndim:
                qf = qf[0]

    # Normalise longitudes to [-180, 180] and mask bad/out-of-region cells.
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    ok = np.isfinite(lat) & np.isfinite(lon) & np.isfinite(ws) & np.isfinite(wd)
    ok &= (ws > 0.1) & (ws <= 60.0)
    ok &= (lat >= lat_min - 1.0) & (lat <= lat_max + 1.0)
    if lon_min <= lon_max:
        lons_x = np.where(lon < lon_min - 180.0, lon + 360.0, lon)
        lons_x = np.where(lon > lon_max + 180.0, lon - 360.0, lons_x)
        ok &= (lons_x >= lon_min - 1.0) & (lons_x <= lon_max + 1.0)
    else:
        # Antimeridian-crossing box (lon_min > lon_max, e.g. a full
        # geostationary disk): include points in [lon_min, 180] or
        # [-180, lon_max] of the encoded longitudes.
        lons_x = lon
        ok &= (lon >= lon_min - 1.0) | (lon <= lon_max + 1.0)
    if qf is not None and qf.shape == ws.shape:
        # OSI SAF L2 WVC quality flag is a bitfield; the low 6 bits hold a
        # quality grade (0 = best), bits >= 6 are error flags. Drop cells that
        # are too far from the GMF, redundant, rain-flagged, over land/ice or
        # failed QC — see ASCAT Product Manual wvc_quality_flag flag_masks.
        #
        # xarray may decode the fill value to NaN, which turns the array into
        # float and breaks bitwise ops — treat NaN flags as bad cells.
        if qf.dtype.kind == "f":
            finite = np.isfinite(qf)
            qf = np.where(finite, qf, 0).astype(np.int64)
            ok &= finite
        else:
            qf = qf.astype(np.int64)
        bad = (64 | 128 | 512 | 1024 | 8192 | 16384 | 32768 | 65536 | 131072)
        ok &= (qf & bad) == 0
    if not np.any(ok):
        return None

    lat_f = lat[ok]
    lon_f = lons_x[ok]
    ws_f = ws[ok]
    wd_f = wd[ok]
    dir_rad = np.deg2rad(wd_f)
    u = -ws_f * np.sin(dir_rad)
    v = -ws_f * np.cos(dir_rad)
    out = {"lat": lat_f, "lon": lon_f, "u": u, "v": v}
    fname = Path(file_path).name.lower()
    if "metopb" in fname:
        out["sat"] = np.full(len(lat_f), 0, dtype=np.int8)  # Metop-B (ASCAT-B)
    elif "metopc" in fname:
        out["sat"] = np.full(len(lat_f), 1, dtype=np.int8)  # Metop-C (ASCAT-C)
    return out


def download_latest_swath(acc, dest_dir=None, region=None, progress=None,
                          max_files=None):
    """Download ASCAT swath passes that cover ``region``.

    Full-disk regions take enough consecutive overpass strips to sweep the
    disk left to right; focused basin/storm boxes only take the freshest
    passes. Returns an info dict (mirroring the gridded-wind downloader) plus
    a 'wind_data' key holding the combined point arrays, or raises RuntimeError.
    """
    import numpy as np

    if dest_dir is None:
        from .ascat import get_ascat_data_dir
        dest_dir = get_ascat_data_dir()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        progress("Connecting to KNMI OSI SAF FTP...")
    files, errors = list_recent_swath_files(acc, progress=progress)
    if not files:
        raise RuntimeError(
            f"No recent ASCAT swath files found. "
            f"{'; '.join(errors) if errors else 'No matches in the last 72h.'}"
        )

    # One file per overpass (drop 25-km/coastal duplicates), sized to the
    # number of passes that actually carry wind cells for the region, not just
    # the freshest handful: the newest orbits may sweep far from a small storm
    # box, so keep trying progressively older overpasses until the box is
    # covered (or a safety budget runs out).
    want = dedupe_orbits(files)
    if max_files is None:
        target = FULL_DISK_MAX_FILES if is_full_disk_region(region) \
            else FOCUSED_MAX_FILES
    else:
        target = max_files
    attempt_budget = min(len(want), target * 2 + 4)
    if not want:
        raise RuntimeError("No ASCAT swath files selected for the region.")

    ftp = _connect(acc)
    pass_info = []
    combined = None
    try:
        for i, f in enumerate(want[:attempt_budget]):
            if len(pass_info) >= target:
                break
            # Orbit granules are immutable: reuse an already-downloaded file
            # instead of re-fetching it when only the crop region changed.
            cached = dest_dir / (f["name"][:-3] if f["name"].endswith(".gz") else f["name"])
            if cached.exists() and cached.stat().st_size > 0:
                if progress:
                    progress(f"  {f['name']}: using cached local file")
                path = cached
            else:
                if progress:
                    progress(f"[{i + 1}/{min(attempt_budget, len(want))}] "
                             f"Downloading {f['name']} ({f['dt']:%Y-%m-%d %H:%MZ})...")
                path = _download_file(ftp, f["path"], dest_dir)
            data = read_swath_wind_arrays(path, region)
            if data is None or len(data["lat"]) == 0:
                if progress:
                    progress(f"  {f['name']}: no wind data in region — skipped")
                try:
                    path.unlink()
                except Exception:
                    pass
                continue
            k = len(pass_info)
            pass_info.append({
                "file_path": str(path),
                "dt": f["dt"],
                "points": int(len(data["lat"])),
                "satellite": f["satellite"],
                "sampling": f["sampling"],
            })
            _keys = ["lat", "lon", "u", "v"]
            if "sat" in data:
                _keys.append("sat")
            _tags = np.full(len(data["lat"]), k, dtype=np.int32)
            if combined is None:
                combined = {kk: np.asarray(data[kk]) for kk in _keys}
                combined["pass_tag"] = _tags
            else:
                for kk in _keys:
                    if kk not in combined:
                        combined[kk] = np.asarray(data[kk])
                    else:
                        combined[kk] = np.concatenate([combined[kk], np.asarray(data[kk])])
                combined["pass_tag"] = np.concatenate([combined["pass_tag"], _tags])
            if progress:
                progress(f"  {f['name']}: {len(data['lat'])} wind points in region")
    finally:
        _close(ftp)

    # Number the passes 1..N (1 = oldest, N = newest/latest) and tag every
    # wind point with its pass number so the viewport can filter by pass.
    if pass_info:
        _order = sorted(
            range(len(pass_info)),
            key=lambda k: pass_info[k].get("dt") or datetime.min.replace(tzinfo=timezone.utc),
        )
        _rank = {k: r for r, k in enumerate(_order, start=1)}
        for k, _p in enumerate(pass_info):
            _p["pass_no"] = _rank[k]
        if combined is not None and "pass_tag" in combined:
            combined["pass"] = np.asarray(
                [_rank[t] for t in combined.pop("pass_tag")], dtype=np.int32)

    if not pass_info:
        raise RuntimeError(
            "Downloaded ASCAT passes but none contained wind data in the "
            "requested region."
        )
    newest = max(p["dt"] for p in pass_info)
    lat_min_r = float(np.min(combined["lat"]))
    lat_max_r = float(np.max(combined["lat"]))
    lon_min_r = float(np.min(combined["lon"]))
    lon_max_r = float(np.max(combined["lon"]))
    return {
        "file_path": pass_info[0]["file_path"],
        "dataset_id": "KNMI-OSI-SAF-ASCAT-L2-swath",
        "title": f"KNMI OSI SAF ASCAT swath ({len(pass_info)} pass(es))",
        "host": KNMI_FTP_HOST,
        "time_str": newest.strftime("%Y-%m-%d %H:%M UTC"),
        "lat": [lat_min_r, lat_max_r],
        "lon": [lon_min_r, lon_max_r],
        "pass_count": len(pass_info),
        "passes": pass_info,
        "points": int(len(combined["lat"])),
        "variables": ["lat", "lon", "wind_speed", "wind_dir"],
        "u_var": "wind_speed->u",
        "v_var": "wind_speed->v",
        "wind_data": combined,
    }


def load_local_swath_files(file_paths, region=None, progress=None):
    """Combine wind vectors from already-downloaded local swath .nc files,
    filtering cells to ``region`` without any network access.

    Returns an info dict (same shape as ``download_latest_swath``) including
    'wind_data', or None when no valid cells cover the region.
    """
    import numpy as np

    pass_info = []
    combined = None
    for raw in file_paths or []:
        path = Path(str(raw).strip('"'))
        if not path.exists() or path.stat().st_size == 0:
            continue
        data = read_swath_wind_arrays(path, region)
        if data is None or len(data["lat"]) == 0:
            if progress:
                progress(f"  {path.name}: no wind data in region — skipped")
            continue
        meta = _parse_file_name(path.name)
        k = len(pass_info)
        pass_info.append({
            "file_path": str(path),
            "dt": meta["dt"] if meta else None,
            "points": int(len(data["lat"])),
            "satellite": meta["satellite"] if meta else "ASCAT",
            "sampling": meta["sampling"] if meta else "",
        })
        _keys = ["lat", "lon", "u", "v"]
        if "sat" in data:
            _keys.append("sat")
        _tags = np.full(len(data["lat"]), k, dtype=np.int32)
        if combined is None:
            combined = {kk: np.asarray(data[kk]) for kk in _keys}
            combined["pass_tag"] = _tags
        else:
            for kk in _keys:
                if kk not in combined:
                    combined[kk] = np.asarray(data[kk])
                else:
                    combined[kk] = np.concatenate([combined[kk], np.asarray(data[kk])])
            combined["pass_tag"] = np.concatenate([combined["pass_tag"], _tags])
        if progress:
            progress(f"  {path.name}: {len(data['lat'])} wind points in region")

    # Number the passes 1..N (1 = oldest, N = newest/latest) and tag every
    # wind point with its pass number so the viewport can filter by pass.
    if pass_info:
        _order = sorted(
            range(len(pass_info)),
            key=lambda k: pass_info[k].get("dt") or datetime.min.replace(tzinfo=timezone.utc),
        )
        _rank = {k: r for r, k in enumerate(_order, start=1)}
        for k, _p in enumerate(pass_info):
            _p["pass_no"] = _rank[k]
        if combined is not None and "pass_tag" in combined:
            combined["pass"] = np.asarray(
                [_rank[t] for t in combined.pop("pass_tag")], dtype=np.int32)

    if not pass_info or combined is None or len(combined["lat"]) == 0:
        return None
    newest = None
    for p in pass_info:
        if p["dt"] is not None and (newest is None or p["dt"] > newest):
            newest = p["dt"]
    return {
        "file_path": pass_info[0]["file_path"],
        "dataset_id": "cached-OSI-SAF-ASCAT-L2-swath",
        "title": f"ASCAT swath from local cache ({len(pass_info)} pass(es))",
        "host": "local",
        "time_str": newest.strftime("%Y-%m-%d %H:%M UTC") if newest else "unknown",
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


class LocalSwathCropWorker(QObject):
    """Background worker that re-crops already-downloaded local swath files
    to a new region without touching the network."""

    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, file_paths, region=None, parent=None):
        super().__init__(parent)
        self.file_paths = file_paths
        self.region = region
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = load_local_swath_files(
                self.file_paths,
                region=self.region,
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


class KNMISwathDownloader(QObject):
    """Background worker that pulls real-time ASCAT swath passes from KNMI."""

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
            info = download_latest_swath(
                self.account,
                dest_dir=self.dest_dir,
                region=self.region,
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
