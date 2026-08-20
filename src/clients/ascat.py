# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: clients/ascat.py
# Description: ASCAT (Advanced SCATterometer) data client. Discovers the latest
#              near-real-time ASCAT ocean-surface wind products and active
#              tropical-cyclone storm products from public sources (NOAA NESDIS
#              Manati and the KNMI / EUMETSAT OSI SAF scatterometer centre).
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
# =============================================================================


import re
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import requests
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MANATI_BASE = "https://manati.star.nesdis.noaa.gov"
MANATI_STORM_PAGES = {
    "Metop-B / ASCAT-B": MANATI_BASE + "/datasets/ASCATBStorm.php",
    "Metop-C / ASCAT-C": MANATI_BASE + "/datasets/ASCATCStorm.php",
}

MANATI_SUB_NAMES = {
    "Metop-B / ASCAT-B": "ASCATBStormSub",
    "Metop-C / ASCAT-C": "ASCATCStormSub",
}

# KNMI / EUMETSAT OSI SAF scatterometer quicklook product pages
KNMI_PRODUCT_PAGES = {
    "OSI SAF ASCAT-B 25 km Winds (OSI-102-b)": "https://scatterometer.knmi.nl/ascat_b_osi_25_prod/",
    "OSI SAF ASCAT-C 25 km Winds (OSI-102-c)": "https://scatterometer.knmi.nl/ascat_c_osi_25_prod/",
    "OSI SAF ASCAT-B Coastal 12.5 km (OSI-104-b)": "https://scatterometer.knmi.nl/ascat_b_osi_co_prod/",
    "OSI SAF ASCAT-C Coastal 12.5 km (OSI-104-c)": "https://scatterometer.knmi.nl/ascat_c_osi_co_prod/",
}

# Anonymous NetCDF wind download sources (ERDDAP griddap). The NOAA
# CoastWatch fleet hosts ASCAT scatterometer and blended sea-surface wind
# grids as downloadable NetCDF files. Hosts are probed in order; if the
# dedicated ASCAT host is unreachable, the verified blended-winds mirror and
# (when needed) the fresh CCMP 6-hourly NRT analysis on the Pacific Islands
# OceanWatch mirror are used as fallbacks so a real wind NetCDF is always
# available to load.
ERDDAP_HOSTS = [
    ("NOAA CoastWatch (pfeg) — ASCAT/blended winds", "https://coastwatch.pfeg.noaa.gov/erddap"),
    ("NOAA CoastWatch (NOAA) — blended winds", "https://coastwatch.noaa.gov/erddap"),
    ("NOAA OceanWatch (PIFSC) — CCMP NRT winds", "https://oceanwatch.pifsc.noaa.gov/erddap"),
]

# Known near-real-time wind-grid datasets (host, dataset_id) that are STILL
# being updated. These are probed FIRST so a genuinely live field (e.g. the
# 6-hourly CCMP v2.1 NRT analysis on PIFSC, or the CoastWatch NRT blended
# winds) always wins over dead/stale grids whose newest time can be months
# old (the science-quality blended winds currently stall around mid-2026).
PREFERRED_NRT_DATASETS = [
    ("https://oceanwatch.pifsc.noaa.gov/erddap", "ccmp-daily-v2-1-NRT"),
    ("https://coastwatch.noaa.gov/erddap", "noaacwBlendednrtWinds6hr"),
    ("https://coastwatch.pfeg.noaa.gov/erddap", "erdQAwind"),
]

NRT_HOST_LABELS = {
    "ccmp-daily-v2-1-NRT": "NOAA OceanWatch (PIFSC) — CCMP NRT winds",
    "noaacwBlendednrtWinds6hr": "NOAA CoastWatch (NOAA) — blended NRT winds",
    "erdQAwind": "NOAA CoastWatch (pfeg) — blended winds",
}

# Any candidate whose newest available time is older than this many hours is
# treated as stale and only used as a last resort (with a warning).
MAX_WIND_AGE_HOURS = 96.0

DEFAULT_WIND_REGION = {  # Western North Pacific + South China Sea
    "lat_min": 0.0,
    "lat_max": 40.0,
    "lon_min": 110.0,
    "lon_max": 170.0,
}

REGION_NAMES = {
    "1": "Atlantic",
    "2": "East Pacific",
    "3": "Central Pacific",
    "4": "West Pacific",
    "5": "Indian Ocean",
    "6": "Southern Hemisphere",
}

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MonWatch-UI/3.0.5"


def get_ascat_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "ascat_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_local_swath_passes():
    """Return the ASCAT swath passes already downloaded to data/ascat_data/.

    Each orbit file becomes one pass, numbered 1..N oldest-first so a HIGHER
    pass number means a NEWER/later pass (Pass N is the latest). Gridded wind
    analysis files (``ascat_wind_*.nc``) are skipped because they do not match
    the orbit-swath filename pattern.

    Returns a list of dicts: {pass_no, file_path, dt, satellite, sampling}.
    """
    from ..clients.knmi_swath import _parse_file_name

    dest = get_ascat_data_dir()
    by_orbit = {}
    for p in sorted(dest.iterdir(), key=lambda x: x.name):
        name = p.name
        if not (name.lower().endswith(".nc") or name.lower().endswith(".gz")):
            continue
        meta = _parse_file_name(name)
        if meta is None:
            continue
        key = (meta["satellite"], meta["dt"].strftime("%Y%m%d%H%M"))
        prefer = meta["sampling"] != "coa"
        old = by_orbit.get(key)
        if old is None or (prefer and not old[0]):
            by_orbit[key] = (prefer, meta["dt"], p)

    ranked = sorted(by_orbit.values(), key=lambda x: x[1])  # oldest first
    out = []
    for i, (_, dt, path) in enumerate(ranked, start=1):
        meta = _parse_file_name(path.name) or {}
        out.append({
            "pass_no": i,
            "file_path": str(path),
            "dt": dt,
            "satellite": meta.get("satellite", "ASCAT"),
            "sampling": meta.get("sampling", ""),
        })
    return out


def _get(url, timeout=40):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
    r.raise_for_status()
    return r.text


def _region_name(region):
    return REGION_NAMES.get(str(region), "")


def _split_storm(storm_full):
    """Split '16W.NANGKA' into ('16W', 'NANGKA')."""
    storm_full = (storm_full or "").strip()
    if "." in storm_full:
        number, _, name = storm_full.partition(".")
        return number, name
    return storm_full, storm_full


def parse_manati_storm_listing(html, satellite):
    """Parse an ASCAT storm-listing page into a list of storm dicts."""
    storms = []
    basin_matches = list(re.finditer(r'<font color="black">\s*([^<]+?)\s*</font>', html, re.I))
    storm_matches = list(
        re.finditer(r'<a[^>]+href="(/datasets/ASCAT[BC]StormSub\.php\?[^"]*?)"[^>]*>', html, re.I)
    )
    basin_i = 0
    basin = ""
    for sm in storm_matches:
        while basin_i < len(basin_matches) and basin_matches[basin_i].start() < sm.start():
            basin = basin_matches[basin_i].group(1).strip()
            basin_i += 1
        href = sm.group(1)
        m = re.search(r'STORM=([^&"]+)', href)
        if not m:
            continue
        storm_full = m.group(1).strip()
        region_m = re.search(r'region=(\d+)', href)
        region = region_m.group(1) if region_m else ""
        number, name = _split_storm(storm_full)
        storms.append({
            "satellite": satellite,
            "storm_id": storm_full,
            "storm_number": number,
            "storm_name": name or storm_full,
            "basin": basin or _region_name(region),
            "region": region,
            "url": MANATI_BASE + href,
        })
    # Deduplicate (same storm can appear under multiple product blocks)
    seen = set()
    out = []
    for s in storms:
        key = (s["satellite"], s["storm_id"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def parse_manati_storm_detail(html):
    """Parse a storm sub-page (product=1) for latest pass imagery."""
    img_re = re.compile(r'href="(\.\./ascat_images/[^"]+\.(?:png|gif|jpg|jpeg))"', re.I)
    passes = []
    for m in img_re.finditer(html):
        rel = m.group(1)
        url = MANATI_BASE + "/" + rel[3:] if rel.startswith("../") else MANATI_BASE + "/" + rel.lstrip("/")
        fname = url.rsplit("/", 1)[-1]
        tm = re.search(r'ascat(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})', fname)
        if not tm:
            continue
        try:
            res, yy, mm, dd, hh = (int(g) for g in tm.groups())
            year = 2000 + yy
            pass_time = datetime(year, mm, dd, hh, 0, tzinfo=timezone.utc)
        except ValueError:
            continue
        pass_type = "ascending" if "_as." in fname else ("descending" if "_ds." in fname else "unknown")
        passes.append({
            "time": pass_time,
            "time_str": pass_time.strftime("%Y-%m-%d %H:%M UTC"),
            "resolution_km": res,
            "pass_type": pass_type,
            "url": url,
        })
    passes.sort(key=lambda p: p["time"], reverse=True)
    return passes


def parse_knmi_quicklook(html):
    """Parse a KNMI OSI SAF scatterometer quicklook page for product metadata."""
    data = {}
    m = re.search(r"Updated\s*@\s*([\d\- :]+)\s*utc", html, re.I)
    data["updated"] = m.group(1).strip() if m else None
    m = re.search(r"(OSI-\d+[a-z]?(?:-[a-z]{1,2})?)", html)
    data["product_id"] = m.group(1) if m else None
    m = re.search(r"status:\s*([a-z]+)", html, re.I)
    data["status"] = m.group(1).strip() if m else None
    m = re.search(r"doi:\s*<a[^>]*>\s*([^<]+)</a>", html, re.I)
    data["doi"] = m.group(1).strip() if m else None
    m = re.search(r"https://[^\"']*ascworldview_[01]\.jpg", html)
    data["map_url"] = m.group(0) if m else None
    return data


def collect_ascat_data(progress=None):
    """Gather the latest ASCAT products and active storm metadata.

    Returns a dict with 'fetched_at', 'satellites', 'storms', 'products' and 'errors'.
    """
    now = datetime.now(timezone.utc)
    year = now.year
    errors = []
    products = []
    storms = []

    def log(msg):
        if progress:
            progress(msg)

    # 1. NOAA Manati storm listings (Metop-B and Metop-C)
    for sat, url in MANATI_STORM_PAGES.items():
        try:
            log(f"Fetching {sat} active storms...")
            html = _get(url, timeout=40)
            found = parse_manati_storm_listing(html, sat)
            storms.extend(found)
            log(f"  Found {len(found)} active storm product(s)")
        except Exception as e:
            errors.append(f"{sat} listing: {e}")
            log(f"  Failed: {e}")

    # 2. Per-storm detail (latest pass imagery) for every active storm
    for storm in storms:
        try:
            log(f"Fetching {storm['storm_name']} latest ASCAT pass...")
            sub = MANATI_SUB_NAMES.get(storm["satellite"], "ASCATCStormSub")
            detail_url = (
                f"{MANATI_BASE}/datasets/{sub}.php?&myyear={year}&STORM={storm['storm_id']}"
                f"&all_flag=0&region={storm['region']}&product=1"
            )
            html = _get(detail_url, timeout=40)
            passes = parse_manati_storm_detail(html)
            storm["passes"] = passes
            storm["latest_pass"] = passes[0]["time_str"] if passes else None
            storm["latest_pass_utc"] = passes[0]["time"] if passes else None
            storm["pass_count"] = len(passes)
            storm["product_url"] = detail_url
        except Exception as e:
            errors.append(f"{storm['storm_name']} detail: {e}")

    # 3. KNMI / EUMETSAT OSI SAF quicklook product pages
    for name, url in KNMI_PRODUCT_PAGES.items():
        try:
            log(f"Fetching {name}...")
            html = _get(url, timeout=40)
            meta = parse_knmi_quicklook(html)
            products.append({"name": name, "url": url, **meta})
        except Exception as e:
            errors.append(f"{name}: {e}")

    return {
        "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "satellites": list(MANATI_STORM_PAGES.keys()),
        "storms": storms,
        "products": products,
        "errors": errors,
    }


class ASCATDownloader(QObject):
    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            data = collect_ascat_data(progress=lambda m: self.progress.emit(m))
            if self._cancelled:
                self.finished.emit()
                return
            self.result.emit(data)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# ---------------------------------------------------------------------------
# NetCDF wind download (loadable by the app's wind-overlay pipeline)
# ---------------------------------------------------------------------------

def find_wind_dataset(base_url, prefer_ascat=True, timeout=40):
    """Locate sea-surface-wind datasets on an ERDDAP server.

    Uses the ERDDAP search API to find candidate wind grids, then ranks them
    by how recently they were updated (newest data first). Returns a list of
    (dataset_id, title) tuples, or an empty list.
    """
    terms = (["ascat"] if prefer_ascat else []) + ["surface wind"]
    candidates = {}
    per_term = max(2.0, timeout / max(1, len(terms)))
    for term in terms:
        url = base_url + "/search/index.json"
        params = {
            "searchFor": term,
            "itemsPerPage": "50",
            "page": "1",
        }
        try:
            r = requests.get(url, params=params, timeout=per_term, headers={"User-Agent": _UA})
            r.raise_for_status()
            j = r.json()
            table = j.get("table", {})
            colnames = table.get("columnNames", [])
            try:
                idx_title = colnames.index("Title")
                idx_dsid = colnames.index("Dataset ID")
            except ValueError:
                continue
        except Exception:
            continue
        rows = table.get("rows", []) or []
        for row in rows:
            if len(row) <= max(idx_title, idx_dsid):
                continue
            title = (row[idx_title] if len(row) > idx_title else "") or ""
            dsid = (row[idx_dsid] if len(row) > idx_dsid else "") or ""
            if not dsid:
                continue
            candidates.setdefault(dsid, title)

    # Score titles/IDs. Favour genuine sea-surface wind grids, near real-time
    # and 6-hourly; heavily penalise ice/current/SST/salinity lookalikes.
    def score(dsid, title):
        tl = f"{title} {dsid}".lower()
        s = 0
        if "winds" in tl or "wind " in tl or tl.endswith("wind"):
            s += 60
        if "ascat" in tl or "scatterometer" in tl:
            s += 40
        if "nrt" in tl or "near real time" in tl or "near real-time" in tl:
            s += 15
        if "6hr" in tl or "6 hour" in tl:
            s += 10
        for bad in ("wind stress", "ice", "current", "salinity", "sst", "temperature",
                    "height", "ssh", "ease-2", "classification"):
            if bad in tl:
                s -= 100
        return s

    scored = [(dsid, title, score(dsid, title)) for dsid, title in candidates.items()]
    scored = [c for c in scored if c[2] > 0]
    if not scored:
        return []

    # Rank the top candidates by newest available data.
    ranked = []
    for dsid, title, s in scored:
        latest = _latest_dataset_time(base_url, dsid, timeout=timeout)
        ranked.append((latest, s, dsid, title))
    ranked.sort(key=lambda r: (r[0] or "", r[1]), reverse=True)
    return [(r[2], r[3]) for r in ranked]


def _parse_time(value):
    """Parse an ISO-ish timestamp (with optional Z) into an aware datetime."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _age_hours(latest):
    """Age (hours) of a dataset's newest time, or None if unparseable."""
    dt = _parse_time(latest)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _is_fresh(latest, max_age_hours=MAX_WIND_AGE_HOURS):
    """True when the newest time is known and within the freshness window."""
    age = _age_hours(latest)
    return age is not None and age <= max_age_hours


def _latest_dataset_time(base_url, dsid, timeout=40):
    """Return the newest available time (as str) for an ERDDAP griddap dataset,
    or None if it cannot be determined."""
    url = f"{base_url}/griddap/{dsid}.json?time%5B(last)%5D"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
        r.raise_for_status()
        j = r.json()
        rows = j.get("table", {}).get("rows", []) or []
        if rows and rows[0]:
            return rows[0][0]
    except Exception:
        return None
    return None


def _dataset_info(base_url, dsid, timeout=30):
    """Fetch ERDDAP dataset metadata (variable names + their dimensions)."""
    url = f"{base_url}/info/{dsid}/index.json"
    r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
    r.raise_for_status()
    j = r.json()
    var_dims = {}
    for row in j["table"]["rows"]:
        if row[0] == "variable" and len(row) > 4:
            var_dims[row[1]] = [d.strip() for d in str(row[4]).split(",")]
    return var_dims


def _constraint_seg(dim_name, lat_min, lat_max, lon_min, lon_max):
    """Build one griddap dimension constraint for the query string."""
    d = (dim_name or "").lower()
    if d in ("latitude", "lat"):
        return f"%5B({lat_min}):({lat_max})%5D"
    if d in ("longitude", "lon"):
        return f"%5B({lon_min}):({lon_max})%5D"
    return "%5B(last)%5D"  # time and any extra scalar dim -> latest


def download_latest_wind_nc(dest_dir=None, region=None, prefer_ascat=True, progress=None):
    """Download the latest wind NetCDF (ascat or blended) to disk.

    Known near-real-time datasets are probed first so a live field always wins
    over a stale grid; only then is a generic ERDDAP discovery pass used as a
    fallback. Returns a dict: {
        'file_path', 'dataset_id', 'title', 'host', 'time_str',
        'variables', 'lat', 'lon', 'u_var', 'v_var', 'err' (optional)
    } or raises an exception if no source works.
    """
    if dest_dir is None:
        dest_dir = get_ascat_data_dir()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    region = region or DEFAULT_WIND_REGION
    lon_min = float(region.get("lon_min", DEFAULT_WIND_REGION["lon_min"]))
    lon_max = float(region.get("lon_max", DEFAULT_WIND_REGION["lon_max"]))
    lat_min = float(region.get("lat_min", DEFAULT_WIND_REGION["lat_min"]))
    lat_max = float(region.get("lat_max", DEFAULT_WIND_REGION["lat_max"]))
    if lon_min > lon_max:
        # ERDDAP griddap needs a non-wrapping longitude range; a full-disk
        # box that crosses the antimeridian degrades to the whole globe.
        lon_min, lon_max = -180.0, 180.0

    last_err = None
    if progress:
        progress("Searching hosts for the freshest wind NetCDF...")
    import concurrent.futures as _cf

    def _attempt(base_url, host_name, dsid, title, latest=""):
        """Try to download a single candidate; returns the info dict."""
        var_dims = _dataset_info(base_url, dsid)
        u_var = next((v for v in var_dims if v.lower() in ("u_wind", "u", "uwnd", "ucur")), None)
        v_var = next((v for v in var_dims if v.lower() in ("v_wind", "v", "vwnd", "vcur")), None)
        if u_var is None or v_var is None:
            raise RuntimeError(f"no u/v wind variables in {dsid}")

        def _query_for(var, var_dims):
            dims = var_dims.get(var, [])
            if not any(d.lower() in ("latitude", "lat") for d in dims) or \
               not any(d.lower() in ("longitude", "lon") for d in dims):
                return None
            segs = "".join(_constraint_seg(d, lat_min, lat_max, lon_min, lon_max) for d in dims)
            return f"{var}{segs}"

        q_u = _query_for(u_var, var_dims)
        q_v = _query_for(v_var, var_dims)
        if q_u is None or q_v is None:
            raise RuntimeError(f"{dsid}: wind vars missing lat/lon dims")
        constraints = f"?{q_u},{q_v}"
        url = f"{base_url}/griddap/{dsid}.nc{constraints}"
        if progress:
            progress(f"Downloading {dsid} ({latest or '?'}) over "
                     f"{lat_min}-{lat_max}N {lon_min}-{lon_max}E...")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        fname = f"ascat_wind_{dsid}_{stamp}.nc"
        fpath = dest_dir / fname
        with requests.get(url, timeout=180, stream=True, headers={"User-Agent": _UA}) as r:
            r.raise_for_status()
            with open(fpath, "wb") as fh:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        fh.write(chunk)

        # Read back time + grid extents to return metadata.
        import xarray as xr
        with xr.open_dataset(fpath) as ds:
            dtime = next((d for d in ds.dims if d.lower() in ("time", "time_utc")), None)
            dlat = next((d for d in ds.dims if d.lower() in ("latitude", "lat")), None)
            dlon = next((d for d in ds.dims if d.lower() in ("longitude", "lon")), None)
            time_val = ds[dtime].values[0] if dtime else None
            time_str = str(time_val) if time_val is not None else "unknown"
            lat_min_actual = float(ds[dlat].values.min())
            lat_max_actual = float(ds[dlat].values.max())
            lon_min_actual = float(ds[dlon].values.min())
            lon_max_actual = float(ds[dlon].values.max())
            u_ok = u_var in ds.data_vars
            v_ok = v_var in ds.data_vars
        if progress:
            progress(f"Saved {fpath.name} — {time_str}")
        return {
            "file_path": str(fpath),
            "dataset_id": dsid,
            "title": title,
            "host": host_name,
            "time_str": time_str,
            "variables": list(var_dims.keys()),
            "lat": [lat_min_actual, lat_max_actual],
            "lon": [lon_min_actual, lon_max_actual],
            "u_var": u_var,
            "v_var": v_var,
            "u_ok": u_ok,
            "v_ok": v_ok,
        }

    # ---- Phase A: known near-real-time datasets first (live field wins). ----
    nrt_probes = []
    with _cf.ThreadPoolExecutor(max_workers=max(1, len(PREFERRED_NRT_DATASETS))) as pool:
        futs = {pool.submit(_latest_dataset_time, b, d, 15): (b, d)
                for b, d in PREFERRED_NRT_DATASETS}
        for fut in _cf.as_completed(futs):
            base_url, dsid = futs[fut]
            try:
                latest = fut.result()
                nrt_probes.append((latest, base_url, dsid))
            except Exception as e:
                last_err = f"{dsid}: {e}"
    nrt_probes.sort(key=lambda x: (x[0] or ""), reverse=True)
    for latest, base_url, dsid in nrt_probes:
        if latest is None:
            continue
        if not _is_fresh(latest):
            if progress:
                progress(f"  {dsid}: newest data is {latest} "
                         f"({_age_hours(latest):.0f}h old) — too stale, skipping")
            last_err = f"{dsid}: newest data {latest} older than {MAX_WIND_AGE_HOURS:.0f}h"
            continue
        host_name = NRT_HOST_LABELS.get(dsid, base_url)
        try:
            info = _attempt(base_url, host_name, dsid, dsid, latest)
            if progress:
                progress(f"Using near-real-time {dsid} ({latest})")
            return info
        except Exception as e:
            last_err = f"{dsid}: {e}"
            if progress:
                progress(f"  {dsid} failed: {e}")

    # ---- Phase B: generic discovery across every reachable host. ----
    hosts = list(ERDDAP_HOSTS)
    all_candidates = []

    def _probe_host(host_name, base_url):
        found = []
        try:
            found = find_wind_dataset(base_url, prefer_ascat=prefer_ascat, timeout=18)
        except Exception as e:
            return host_name, base_url, [], f"search failed: {e}"
        return host_name, base_url, found, None

    with _cf.ThreadPoolExecutor(max_workers=len(hosts)) as pool:
        results = list(pool.map(lambda h: _probe_host(*h), hosts))

    # Probe the freshness of every candidate in parallel so slow/dead ones
    # cannot stall the freshest-source decision.
    probe_tasks = [(base_url, dsid) for _, base_url, found, _ in results for dsid, _ in (found or [])]
    with _cf.ThreadPoolExecutor(max_workers=min(8, max(1, len(probe_tasks)))) as pool:
        latest_by = dict(zip(
            probe_tasks,
            pool.map(lambda t: _latest_dataset_time(t[0], t[1], timeout=12), probe_tasks),
        ))

    for host_name, base_url, found, err in results:
        if err:
            last_err = f"{host_name}: {err}"
            if progress:
                progress(f"  {host_name} search failed: {err}")
            continue
        if not found:
            last_err = f"{host_name}: no wind datasets"
            continue
        for dsid, title in found:
            latest = latest_by.get((base_url, dsid)) or ""
            all_candidates.append((latest, base_url, host_name, dsid, title))
        if progress and all_candidates:
            freshest_now = max(c[0] for c in all_candidates)
            progress(f"  {host_name}: freshest candidate at {freshest_now or 'unknown'}")

    # Fresh candidates first, then the least-stale stale ones (never let a
    # dead grid beat a live one just because it appears earlier in a search).
    all_candidates.sort(
        key=lambda c: (0 if _is_fresh(c[0]) else 1, c[0] or "", c[3]),
        reverse=True,
    )

    for latest, base_url, host_name, dsid, title in all_candidates:
        if not _is_fresh(latest):
            age = _age_hours(latest)
            age_txt = f"{age:.0f}h old" if age is not None else "unknown age"
            if progress:
                progress(f"  {dsid}: newest data {latest or '?'} ({age_txt}) — stale fallback")
        try:
            return _attempt(base_url, host_name, dsid, title, latest)
        except Exception as e:
            last_err = f"{host_name}: {e}"
            if progress:
                progress(f"  {host_name} failed: {e}")
            continue
    raise RuntimeError(f"Could not download wind NetCDF: {last_err or 'no hosts tried'}")


class ASCATNetCDFDownloader(QObject):
    """Background worker that downloads the latest wind NetCDF to disk."""

    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, region=None, dest_dir=None, prefer_ascat=True, parent=None):
        super().__init__(parent)
        self.region = region
        self.dest_dir = dest_dir
        self.prefer_ascat = prefer_ascat
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = download_latest_wind_nc(
                dest_dir=self.dest_dir,
                region=self.region,
                prefer_ascat=self.prefer_ascat,
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
