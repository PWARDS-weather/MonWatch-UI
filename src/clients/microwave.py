# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: clients/microwave.py
# Description: Passive-microwave data client. Discovers the latest near-real-time
#              microwave brightness-temperature imagery of active tropical
#              cyclones (NOAA Manati GCOM-W1 / AMSR2 storm products) plus the
#              NOAA Open Data Dissemination (NODD) JPSS near-real-time archive
#              (ATMS 88.2 GHz SDR + GEO granules, VIIRS SDR) and the NASA
#              Earthdata catalog, mirroring the app's ASCAT client structure.
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


import re
import os
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

MANATI_BASE = "https://manati.star.nesdis.noaa.gov"
AMSR2_STORM_PAGE = MANATI_BASE + "/datasets/AMSR2Storm.php"
AMSR2_SUB_PAGE = MANATI_BASE + "/datasets/Amsr2StormSub.php"
AMSR2_IMG_DIR = "gcom_images/amsr2_storm"

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MonWatch-UI/3.0.5"

REGION_NAMES = {
    "1": "Atlantic",
    "2": "East Pacific",
    "3": "Central Pacific",
    "4": "West Pacific",
    "5": "Indian Ocean",
    "6": "Southern Hemisphere",
}

AMSR2_BASINS = [
    "Atlantic", "East Pacific", "Central Pacific", "West Pacific",
    "Indian Ocean", "Southern Hem.",
]

# NOAA Open Data Dissemination (NODD) JPSS public buckets. Same source the
# PWARDS ADT tool uses for near-real-time passive microwave (ATMS 88.2 GHz).
NODD_BUCKETS = [
    "noaa-nesdis-n20-pds",
    "noaa-nesdis-n21-pds",
    "noaa-nesdis-snpp-pds",
]
NODD_REGION = "us-east-1"
NODD_ATMS_PRODUCT = "ATMS-SDR"
NODD_ATMS_GEO_PRODUCT = "ATMS-SDR-GEO"
NODD_VIIRS_PRODUCTS = [
    "VIIRS-DNB-SDR", "VIIRS-DNB-GEO",
    "VIIRS-I1-SDR", "VIIRS-I2-SDR", "VIIRS-I3-SDR",
]
# Raw thermal VIIRS imagery band (11.45 um) brightness temperature for the
# overlay, plus its terrain-corrected geolocation. Both anonymous on NODD.
NODD_VIIRS_I5_PRODUCT = "VIIRS-I5-SDR"
NODD_VIIRS_IMG_GEO_PRODUCT = "VIIRS-IMG-GEO-TC"

ATMS_88_INDEX = 15
ATMS_88_GHZ = 88.2

MAX_OVERPASS_AGE_HOURS = 12.0
MAX_OVERPASS_DISTANCE_KM = 400.0
_GRAN_RE = re.compile(r"_d(?P<day>\d{8})_t(?P<t>\d{7})_e(?P<e>\d{7})_"
                      r"b(?P<burst>\d{5})_")

NASA_COLLECTIONS = [
    {
        "name": "NASA Earthdata — GPM GMI brightness temperatures",
        "product_id": "GPM_2AGPROFGMI",
        "status": "NASA Earthdata",
        "updated": "",
        "url": "https://search.earthdata.nasa.gov/search?q=GPM_2AGPROFGMI",
    },
    {
        "name": "NASA Earthdata — GCOM-W1 AMSR2 L2 (JAXA) brightness temperatures",
        "product_id": "A2_CSR_TB",
        "status": "NASA Earthdata",
        "updated": "",
        "url": "https://search.earthdata.nasa.gov/search?q=GCOM-W1%20AMSR2%20TB",
    },
    {
        "name": "NASA Earthdata — VIIRS SDR (Suomi NPP / NOAA-20 / NOAA-21)",
        "product_id": "VIIRS-SDR",
        "status": "NASA Earthdata",
        "updated": "",
        "url": "https://search.earthdata.nasa.gov/search?q=VIIRS%20SDR",
    },
    {
        "name": "NASA Earthdata — ATMS SDR (JPSS)",
        "product_id": "ATMS-SDR",
        "status": "NASA Earthdata",
        "updated": "",
        "url": "https://search.earthdata.nasa.gov/search?q=ATMS%20SDR",
    },
    {
        "name": "NOAA Open Data Dissemination — JPSS registry",
        "product_id": "NODD-JPSS",
        "status": "AWS Open Data",
        "updated": "",
        "url": "https://registry.opendata.aws/noaa-jpss/",
    },
]

# NASA LANCE / LAADS near-real-time VIIRS SDR imagery-band granules, served
# over plain HTTPS with an Earthdata Login bearer token (free account).
LANCE_NRT_BASE = "https://nrt3.modaps.eosdis.nasa.gov/archive/allData/5200"
VIIRS_NRT_COLLECTIONS = [
    # 375 m I-band L1B calibrated radiance (+ brightness-temperature LUT for
    # the two thermal emissive bands I4 = 3.74 um and I5 = 11.45 um).
    {"short_name": "VNP02IMG_NRT", "satellite": "Suomi NPP"},
    # Terrain-corrected geolocation for the imagery-resolution bands.
    {"short_name": "VNP03IMG_NRT", "satellite": "Suomi NPP"},
]
VIIRS_NRT_MAX_AGE_HOURS = 48.0
VIIRS_I5_LUT = "I05_brightness_temperature_lut"

# MIMIC-TC2 (CIMSS/SSEC, University of Wisconsin) — free near-real-time
# coherent 89 GHz brightness-temperature fields of active tropical cyclones.
# Each active storm (e.g. 2026_16W) publishes 15-minute NetCDF granules at
#   <BASE>/<storm_dir>/web/data/<YYYYMMDDTHHMM>.nc
# containing regular lat/lon axes and a 'mimic_tc_89GHz_bt' uint8 field
# (BT_K = uint8 * 0.5 + 160.0, _FillValue 255, units K).
MIMIC_TC2_BASE = "https://tropic.ssec.wisc.edu/real-time/mimtc2"
MIMIC_STORM_RE = re.compile(r"(\d{4}_\d{1,2}[A-Za-z])/", re.I)
MIMIC_NC_RE = re.compile(r"(\d{8}T\d{6})\.nc", re.I)
MIMIC_NC_MAX_AGE_HOURS = 24.0

_LISTING_CACHE = {}


def get_microwave_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "microwave_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_nodd_cache_dir():
    d = get_microwave_data_dir() / "nodac_mw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url, timeout=40):
    r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
    r.raise_for_status()
    return r.text


def _scalar_attr(value, default=0.0):
    """Coerce an HDF5/netCDF attribute (scalar, 1-d array, bytes) to a float."""
    import numpy as np
    if value is None:
        return float(default)
    try:
        return float(np.asarray(value).reshape(-1)[0])
    except Exception:
        try:
            return float(value)
        except Exception:
            return float(default)


def _region_name(region):
    return REGION_NAMES.get(str(region), "")


# ---------------------------------------------------------------------------
# NOAA Manati AMSR2 (GCOM-W1) storm products
# ---------------------------------------------------------------------------

def parse_amsr2_storm_listing(html, satellite):
    """Parse the Manati AMSR2 storm page into a list of storm dicts."""
    storms = []
    basin_matches = list(re.finditer(r'<font color="black">\s*([^<]+?)\s*</font>',
                                     html, re.I))
    storm_matches = list(
        re.finditer(r'href="(/datasets/Amsr2StormSub\.php\?[^"]*?)"', html, re.I)
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
        if not storm_full or storm_full.lower() == "default":
            continue
        region_m = re.search(r'region=(\d+)', href)
        region = region_m.group(1) if region_m else ""
        basin_name = (basin or _region_name(region) or "Unknown")
        if basin_name.strip().lower() == "cental pacific":
            basin_name = "Central Pacific"
        storms.append({
            "satellite": satellite,
            "instrument": "AMSR2",
            "storm_id": storm_full,
            "storm_name": storm_full,
            "basin": basin_name,
            "region": region,
            "url": MANATI_BASE + href,
        })
    seen = set()
    out = []
    for s in storms:
        key = (s["satellite"], s["storm_id"])
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


def parse_amsr2_storm_passes(html):
    """Parse an AMSR2 storm page for the latest brightness-temperature images.

    Each image filename looks like
      202608132130_37h_GW1AM2_202608131358_084D_L1DLBTBR_2210210_h5_PEILOU_png.png
    where the leading 12 digits are the display (pass) time, the channel token
    is 37h/37v/89h/89v and the second 12-digit block is the sensing time.
    """
    img_re = re.compile(
        r'href="([^"]+?gcom_images/amsr2_storm/[^"]+?/'
        r'(?P<disp>\d{12})_(?P<ch>\d{2,3})(?P<pol>[hv])_GW1AM2_'
        r'(?P<obs>\d{12})_[^"]+\.png)"', re.I)
    passes = []
    seen = set()
    for m in img_re.finditer(html):
        rel = m.group(1)
        url = (MANATI_BASE + "/" + rel[3:]
               if rel.startswith("../") else MANATI_BASE + "/" + rel.lstrip("/"))
        try:
            disp = datetime.strptime(m.group("disp"), "%Y%m%d%H%M").replace(
                tzinfo=timezone.utc)
            obs = datetime.strptime(m.group("obs"), "%Y%m%d%H%M").replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        ch = int(m.group("ch"))
        ghz = 89.0 if ch >= 80 else (36.5 if ch >= 30 else float(ch))
        pol = m.group("pol").upper()
        key = (disp, ghz, pol)
        if key in seen:
            continue
        seen.add(key)
        passes.append({
            "time": disp,
            "sensing_time": obs,
            "time_str": disp.strftime("%Y-%m-%d %H:%M UTC"),
            "channel_ghz": ghz,
            "polarization": pol,
            "channel": f"BrightnessTemperature_{ch}GHz_{pol}",
            "url": url,
        })
    passes.sort(key=lambda p: p["time"], reverse=True)
    return passes


def _parse_revised(html):
    m = re.search(r"Revised:\s*(\d{1,2}\s+\w+\s+\d{4}\s+\d{1,2}:\d{2}\s+[AP]M)",
                  html, re.I)
    if not m:
        return ""
    try:
        return datetime.strptime(m.group(1), "%d %B %Y %I:%M %p").strftime(
            "%Y-%m-%d %H:%M UTC")
    except Exception:
        return ""


def collect_amsr2_data(progress=None):
    """Gather the latest AMSR2 storm products and active-storm metadata."""
    now = datetime.now(timezone.utc)
    year = now.year
    errors = []
    storms = []
    products = []

    def log(msg):
        if progress:
            progress(msg)

    try:
        log("Fetching GCOM-W1 / AMSR2 active storms...")
        html = _get(AMSR2_STORM_PAGE, timeout=40)
        storms = parse_amsr2_storm_listing(html, "GCOM-W1 / AMSR2")
        log(f"  Found {len(storms)} active storm product(s)")
        updated = _parse_revised(html)
        products.append({
            "name": "NOAA Manati — GCOM-W1/AMSR2 Storm Products",
            "product_id": "AMSR2Storm",
            "status": "Active",
            "updated": updated,
            "url": AMSR2_STORM_PAGE,
        })
        products.append({
            "name": "NOAA Manati — Tropical Cyclone Monitor",
            "product_id": "CycloneMonitor",
            "status": "Active",
            "updated": updated,
            "url": MANATI_BASE + "/datasets/CycloneMonitor.php",
        })
        products.append({
            "name": "NOAA Manati — GCOM2 Data Products",
            "product_id": "GCOM2Data",
            "status": "Active",
            "updated": updated,
            "url": MANATI_BASE + "/datasets/GCOM2Data.php",
        })
    except Exception as e:
        errors.append(f"AMSR2 storm listing: {e}")
        log(f"  Failed: {e}")

    for storm in storms:
        try:
            log(f"Fetching {storm['storm_name']} latest AMSR2 passes...")
            detail_url = (
                f"{AMSR2_SUB_PAGE}?&myyear={year}&STORM={storm['storm_id']}"
                f"&all_flag=0&region={storm['region']}&product=0"
            )
            detail_html = _get(detail_url, timeout=40)
            passes = parse_amsr2_storm_passes(detail_html)
            storm["passes"] = passes
            storm["latest_pass"] = passes[0]["time_str"] if passes else None
            storm["latest_pass_utc"] = passes[0]["time"] if passes else None
            storm["pass_count"] = len(passes)
            storm["product_url"] = detail_url
        except Exception as e:
            errors.append(f"{storm['storm_name']} detail: {e}")

    return {
        "satellites": ["GCOM-W1 / AMSR2"],
        "storms": storms,
        "products": products,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# NOAA Open Data Dissemination (NODD) JPSS — ATMS + VIIRS near-real-time
# ---------------------------------------------------------------------------

_s3_client = None


def _s3():
    """Lazily-created unsigned boto3 S3 client (public NODD buckets)."""
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.client import Config
        _s3_client = boto3.client("s3", region_name=NODD_REGION,
                                  config=Config(signature_version=UNSIGNED))
    except Exception as e:
        log.warning("boto3 unavailable (%s); NODD microwave disabled", e)
        _s3_client = False
    return _s3_client


def _granule_core(key):
    m = _GRAN_RE.search(key)
    if not m:
        return None
    return (m.group("day"), m.group("t"), m.group("e"), m.group("burst"))


def _granule_dt(key):
    m = _GRAN_RE.search(key)
    if not m:
        return None
    try:
        day = datetime.strptime(m.group("day"), "%Y%m%d")
        t = m.group("t")
        hh, mm, ss, dec = int(t[0:2]), int(t[2:4]), int(t[4:6]), int(t[6])
        return day + timedelta(hours=hh, minutes=mm, seconds=ss,
                               microseconds=dec * 1e5)
    except Exception:
        return None


def _satellite_for_key(key):
    k = (key or "").lower()
    if "_npp_" in k:
        return "Suomi NPP"
    if "_j01_" in k:
        return "NOAA-20"
    if "_j02_" in k:
        return "NOAA-21"
    return "JPSS"


def _list_keys(bucket, product, date):
    """List granule keys for one product and date, cached."""
    prefix = f"{product}/{date.year:04d}/{date.month:02d}/{date.day:02d}/"
    now = time.time()
    cached = _LISTING_CACHE.get((bucket, prefix))
    if cached is not None and now - cached[0] < 600:
        return cached[1]
    client = _s3()
    if not client:
        return []
    keys = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix,
                                       PaginationConfig={"PageSize": 1000}):
            for obj in page.get("Contents", []):
                k = obj["Key"]
                if _GRAN_RE.search(k) and k.endswith(".h5"):
                    keys.append(k)
    except Exception as e:
        log.warning("NODD listing %s/%s failed: %s", bucket, prefix, e)
        return []
    _LISTING_CACHE[(bucket, prefix)] = (now, keys)
    return keys


def _download(key, bucket, cache_dir):
    """Download an S3 object to the local cache, return the local path."""
    if not cache_dir:
        cache_dir = get_nodd_cache_dir()
    rel = key.lstrip("/")
    local = os.path.join(str(cache_dir), rel)
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local
    client = _s3()
    if not client:
        return None
    try:
        os.makedirs(os.path.dirname(local), exist_ok=True)
        client.download_file(bucket, key, local)
        return local
    except Exception as e:
        log.debug("NODD download %s failed: %s", key, e)
        try:
            os.remove(local)
        except Exception:
            pass
        return None


def _read_h5(path, group):
    try:
        import h5py
        with h5py.File(path, "r") as f:
            if group in f:
                import numpy as np
                return np.asarray(f[group][:])
    except Exception as e:
        log.debug("NODD read %s failed: %s", os.path.basename(path), e)
    return None


def _atms_coverage_stats(sdr_path, geo_path, storm_lat, storm_lon):
    """Count usable 88.2 GHz samples near the storm from an SDR+GEO pair."""
    import numpy as np
    tb = _read_h5(sdr_path, "/All_Data/ATMS-SDR_All/BrightnessTemperature")
    fac = _read_h5(sdr_path, "/All_Data/ATMS-SDR_All/BrightnessTemperatureFactors")
    qf = _read_h5(sdr_path, "/All_Data/ATMS-SDR_All/QF20_ATMSSDR")
    lat = _read_h5(geo_path, "/All_Data/ATMS-SDR-GEO_All/Latitude")
    lon = _read_h5(geo_path, "/All_Data/ATMS-SDR-GEO_All/Longitude")
    if tb is None or fac is None or lat is None or lon is None:
        return None
    if tb.ndim != 3 or tb.shape[2] <= ATMS_88_INDEX:
        return None
    tb88 = np.asarray(tb[..., ATMS_88_INDEX], dtype=np.float64) * float(fac[0])
    tb88 += float(fac[1]) if len(fac) > 1 else 0.0
    if qf is not None and qf.shape[:2] == tb88.shape:
        tb88 = np.where(qf[..., ATMS_88_INDEX] != 0, np.nan, tb88)
    glat = np.asarray(lat, dtype=np.float64)
    glon = np.asarray(lon, dtype=np.float64)
    d = np.hypot((glat - float(storm_lat)) * 111.0,
                 (glon - float(storm_lon)) * 111.0
                 * np.cos(np.radians(float(storm_lat))))
    near = d <= 150.0
    ok = near & np.isfinite(tb88)
    if int(ok.sum()) == 0:
        return {"coverage_points": 0, "min_distance_km": round(float(np.nanmin(d)), 1),
                "tb_min": None, "tb_max": None}
    return {
        "coverage_points": int(ok.sum()),
        "min_distance_km": round(float(np.nanmin(d)), 1),
        "tb_min": round(float(np.nanmin(tb88[ok])), 1),
        "tb_max": round(float(np.nanmax(tb88[ok])), 1),
    }


def _atms_arrays(sdr_path, geo_path, max_points=8000):
    """Return (lat, lon, tb88) subsampled arrays for the whole 88.2 GHz swath,
    suitable for a viewport overlay. Returns (None, None, None) on failure."""
    import numpy as np
    tb = _read_h5(sdr_path, "/All_Data/ATMS-SDR_All/BrightnessTemperature")
    fac = _read_h5(sdr_path, "/All_Data/ATMS-SDR_All/BrightnessTemperatureFactors")
    qf = _read_h5(sdr_path, "/All_Data/ATMS-SDR_All/QF20_ATMSSDR")
    lat = _read_h5(geo_path, "/All_Data/ATMS-SDR-GEO_All/Latitude")
    lon = _read_h5(geo_path, "/All_Data/ATMS-SDR-GEO_All/Longitude")
    if tb is None or fac is None or lat is None or lon is None:
        return None, None, None
    if tb.ndim != 3 or tb.shape[2] <= ATMS_88_INDEX:
        return None, None, None
    tb88 = np.asarray(tb[..., ATMS_88_INDEX], dtype=np.float64) * float(fac[0])
    tb88 += float(fac[1]) if len(fac) > 1 else 0.0
    if qf is not None and qf.shape[:2] == tb88.shape:
        tb88 = np.where(qf[..., ATMS_88_INDEX] != 0, np.nan, tb88)
    glat = np.asarray(lat, dtype=np.float64)
    glon = np.asarray(lon, dtype=np.float64)
    if glat.shape != tb88.shape or glon.shape != tb88.shape:
        return None, None, None
    glat = glat.ravel()
    glon = glon.ravel()
    tb88 = tb88.ravel()
    ok = np.isfinite(tb88) & np.isfinite(glat) & np.isfinite(glon)
    idx = np.flatnonzero(ok)
    if idx.size == 0:
        return None, None, None
    if idx.size > max_points:
        step = max(1, idx.size // max_points)
        idx = idx[::step]
    return glat[idx], glon[idx], tb88[idx]


def find_atms_overpass(lat, lon, target_dt=None, max_age_hours=None,
                       max_distance_km=None, cache_dir=None, progress=None):
    """Find + download the ATMS granule whose 88.2 GHz swath passes nearest to
    (lat, lon) within the age window. Returns an info dict or None."""
    import numpy as np
    max_age_hours = max_age_hours or MAX_OVERPASS_AGE_HOURS
    max_distance_km = max_distance_km or MAX_OVERPASS_DISTANCE_KM
    if target_dt is None:
        target_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    if getattr(target_dt, "tzinfo", None) is not None:
        target_dt = target_dt.replace(tzinfo=None)

    window = timedelta(hours=max_age_hours)
    start, end = target_dt - window, target_dt + window
    dates = {target_dt.date(), start.date(), end.date()}

    candidates = []
    for bucket in NODD_BUCKETS:
        for date in sorted(dates):
            geo_keys = _list_keys(bucket, NODD_ATMS_GEO_PRODUCT, date)
            sdr_keys = {_granule_core(k): k
                        for k in _list_keys(bucket, NODD_ATMS_PRODUCT, date)}
            for gk in geo_keys:
                gdt = _granule_dt(gk)
                if gdt is None or gdt < start or gdt >= end:
                    continue
                sk = sdr_keys.get(_granule_core(gk))
                if sk:
                    candidates.append((bucket, gk, sk, gdt))

    # NODD ATMS granules are short (~1 min) pole-to-pole swath segments, so
    # the right overpass is the one whose GEO footprint passes nearest the
    # target point. Two-stage probe to keep downloads low:
    #   1. Sample every Nth candidate (by time) and probe those in parallel
    #      to find a coarse temporal neighborhood of the overpass.
    #   2. Probe densely around the best coarse hit to pick the precise
    #      granule whose 88.2 GHz swath passes nearest the point.

    def _probe(cand):
        bucket, gk, sk, gdt = cand
        gl = _download(gk, bucket, cache_dir)
        if gl is None:
            return None
        glat = _read_h5(gl, "/All_Data/ATMS-SDR-GEO_All/Latitude")
        glon = _read_h5(gl, "/All_Data/ATMS-SDR-GEO_All/Longitude")
        if glat is None or glon is None:
            return None
        d = np.hypot((np.asarray(glat) - float(lat)) * 111.0,
                     (np.asarray(glon) - float(lon)) * 111.0
                     * np.cos(np.radians(float(lat))))
        return (float(np.nanmin(d)), cand, gl)

    def _probe_many(probe_list):
        results = []
        if not probe_list:
            return results
        if len(probe_list) > 1:
            try:
                import concurrent.futures as _cf
                workers = max(2, min(8, len(probe_list)))
                with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = [r for r in ex.map(_probe, probe_list) if r]
            except Exception:
                results = [r for r in (_probe(c) for c in probe_list) if r]
        else:
            results = [r for r in (_probe(c) for c in probe_list) if r]
        return results

    candidate_pool = sorted(candidates,
                            key=lambda c: (c[3] - target_dt).total_seconds())

    if not candidate_pool:
        if progress:
            progress(f"No ATMS 88.2 GHz granule covers {lat:.1f}N {lon:.1f}E "
                     f"within {max_age_hours:g}h")
        return None

    # Coarse sampling must be dense enough that the true overpass granule is
    # always within (sample spacing / 2) + dense-window of a sampled hit.
    # Granules cadence ~4-5 min; use ~25 min coarse spacing and probe ±17 min
    # densely around the best coarse hit — those bounds always bracket the
    # exact granule while keeping downloads small.
    sample_step = max(1, len(candidate_pool) // 40)
    sample = candidate_pool[::sample_step]
    sample_hits = _probe_many(sample)
    if not sample_hits:
        if progress:
            progress(f"No ATMS 88.2 GHz granule covers {lat:.1f}N {lon:.1f}E "
                     f"within {max_age_hours:g}h")
        return None

    sample_hits.sort(key=lambda h: h[0])
    best_coarse = sample_hits[0]
    best_coarse_t = best_coarse[1][3]
    # Probe a short time neighborhood (±17 min) around the coarse overpass so
    # the precisely-nearest granule is found regardless of cadence.
    dense = [c for c in candidate_pool
             if abs((c[3] - best_coarse_t).total_seconds()) <= 17 * 60]
    if len(dense) > 80:
        dense = sorted(dense,
                       key=lambda c: abs((c[3] - best_coarse_t).total_seconds()))
        dense = dense[:80]
    dense_hits = _probe_many(dense)
    all_hits = sample_hits + dense_hits
    all_hits.sort(key=lambda h: (h[0], abs((h[1][3] - target_dt).total_seconds())))

    best = None
    for mind, cand, gl in all_hits:
        if best is None or mind <= best[5]:
            best = (cand[0], cand[1], cand[2], gl, cand[3], mind)
    if best is None or best[5] > max_distance_km:
        _nearest = min(all_hits, key=lambda h: h[0])
        if best is None and _nearest is not None:
            best = (_nearest[1][0], _nearest[1][1], _nearest[1][2],
                    _nearest[2], _nearest[1][3], _nearest[0])
        if progress:
            how = (f"{best[5]:.0f} km away" if best else "no granule found")
            progress(f"Nearest ATMS 88.2 GHz granule is {how}; "
                     f"{max_distance_km:g} km was requested")

    bucket, gk, sk, gl, dt, mind = best
    sl = _download(sk, bucket, cache_dir)
    if sl is None:
        return None
    stats = _atms_coverage_stats(sl, gl, lat, lon)
    swath_lat, swath_lon, swath_tb = _atms_arrays(sl, gl)
    if progress:
        progress(f"Downloaded ATMS granule {os.path.basename(sk)}")
    return {
        "time": dt,
        "time_str": dt.strftime("%Y-%m-%d %H:%M UTC"),
        "satellite": _satellite_for_key(sk),
        "bucket": bucket,
        "sdr_key": sk,
        "geo_key": gk,
        "sdr_file": sl,
        "geo_file": gl,
        "storm_latitude": float(lat),
        "storm_longitude": float(lon),
        "age_hours": round(abs((dt - target_dt).total_seconds()) / 3600.0, 3),
        "distance_km": round(mind, 1),
        "channel_ghz": ATMS_88_GHZ,
        "coverage_points": (stats or {}).get("coverage_points", 0),
        "tb_min": (stats or {}).get("tb_min"),
        "tb_max": (stats or {}).get("tb_max"),
        "swath_lat": swath_lat,
        "swath_lon": swath_lon,
        "swath_tb": swath_tb,
    }


def _viirs_i5_arrays(sdr_path, geo_path, max_points=8000):
    """Return (lat, lon, i5_tb) subsampled arrays for the whole VIIRS I5
    (11.45 um) brightness-temperature swath. Returns (None,)*3 on failure."""
    import numpy as np
    tb = _read_h5(sdr_path, "/All_Data/VIIRS-I5-SDR_All/BrightnessTemperature")
    fac = _read_h5(sdr_path, "/All_Data/VIIRS-I5-SDR_All/BrightnessTemperatureFactors")
    qf = _read_h5(sdr_path, "/All_Data/VIIRS-I5-SDR_All/QF1_VIIRSIBANDSDR")
    lat = _read_h5(geo_path, "/All_Data/VIIRS-IMG-GEO-TC_All/Latitude")
    lon = _read_h5(geo_path, "/All_Data/VIIRS-IMG-GEO-TC_All/Longitude")
    if tb is None or fac is None or lat is None or lon is None:
        return None, None, None
    tb = np.asarray(tb, dtype=np.float64) * float(fac[0])
    tb += float(fac[1]) if len(fac) > 1 else 0.0
    if qf is not None and qf.shape == tb.shape:
        tb = np.where(qf != 0, np.nan, tb)
    glat = np.asarray(lat, dtype=np.float64)
    glon = np.asarray(lon, dtype=np.float64)
    if glat.shape != tb.shape or glon.shape != tb.shape:
        return None, None, None
    glat = glat.ravel()
    glon = glon.ravel()
    tb = tb.ravel()
    ok = np.isfinite(tb) & np.isfinite(glat) & np.isfinite(glon)
    idx = np.flatnonzero(ok)
    if idx.size == 0:
        return None, None, None
    if idx.size > max_points:
        step = max(1, idx.size // max_points)
        idx = idx[::step]
    return glat[idx], glon[idx], tb[idx]


def _viirs_i5_coverage_stats(sdr_path, geo_path, storm_lat, storm_lon):
    """Count usable I5 brightness-temperature samples near a storm."""
    import numpy as np
    tb = _read_h5(sdr_path, "/All_Data/VIIRS-I5-SDR_All/BrightnessTemperature")
    fac = _read_h5(sdr_path, "/All_Data/VIIRS-I5-SDR_All/BrightnessTemperatureFactors")
    qf = _read_h5(sdr_path, "/All_Data/VIIRS-I5-SDR_All/QF1_VIIRSIBANDSDR")
    lat = _read_h5(geo_path, "/All_Data/VIIRS-IMG-GEO-TC_All/Latitude")
    lon = _read_h5(geo_path, "/All_Data/VIIRS-IMG-GEO-TC_All/Longitude")
    if tb is None or fac is None or lat is None or lon is None:
        return None
    tb = np.asarray(tb, dtype=np.float64) * float(fac[0])
    tb += float(fac[1]) if len(fac) > 1 else 0.0
    if qf is not None and qf.shape == tb.shape:
        tb = np.where(qf != 0, np.nan, tb)
    glat = np.asarray(lat, dtype=np.float64)
    glon = np.asarray(lon, dtype=np.float64)
    d = np.hypot((glat - float(storm_lat)) * 111.0,
                 (glon - float(storm_lon)) * 111.0
                 * np.cos(np.radians(float(storm_lat))))
    near = d <= 150.0
    ok = near & np.isfinite(tb)
    if int(ok.sum()) == 0:
        return {"coverage_points": 0, "min_distance_km": round(float(np.nanmin(d)), 1),
                "tb_min": None, "tb_max": None}
    return {
        "coverage_points": int(ok.sum()),
        "min_distance_km": round(float(np.nanmin(d)), 1),
        "tb_min": round(float(np.nanmin(tb[ok])), 1),
        "tb_max": round(float(np.nanmax(tb[ok])), 1),
    }


# ---------------------------------------------------------------------------
# NASA LANCE / LAADS near-real-time VIIRS L1B via Earthdata Login
# ---------------------------------------------------------------------------

_LANCE_NC_RE = re.compile(r"VNP0[23]IMG_NRT\.A\d{7}\.\d{4}\.\d{3}\.nc")


def _lance_keys(product, date):
    """List LANCE NRT granule filenames for one product + date (anonymous)."""
    try:
        doy = date.timetuple().tm_yday
    except Exception:
        return []
    url = f"{LANCE_NRT_BASE}/{product}/{date.year:04d}/{doy:03d}/"
    now = time.time()
    cached = _LISTING_CACHE.get(("lance", url))
    if cached is not None and now - cached[0] < 600:
        return cached[1]
    names = []
    try:
        html = _get(url, timeout=40)
        names = sorted(set(_LANCE_NC_RE.findall(html)))
    except Exception as e:
        log.warning("LANCE listing %s failed: %s", url, e)
    _LISTING_CACHE[("lance", url)] = (now, names)
    return names


def _lance_core(name):
    m = re.search(r"\.A(\d{7}\.\d{4})\.", name or "")
    return m.group(1) if m else None


def _lance_granule_dt(name):
    m = re.search(r"\.A(\d{4})(\d{3})\.(\d{2})(\d{2})\.", name or "")
    if not m:
        return None
    try:
        return (datetime(int(m.group(1)), 1, 1)
                + timedelta(days=int(m.group(2)) - 1,
                            hours=int(m.group(3)),
                            minutes=int(m.group(4))))
    except Exception:
        return None


def _lance_download(name, cache_dir, token=None, progress=None):
    """Download one LANCE NRT granule with an Earthdata bearer token."""
    if not cache_dir:
        cache_dir = get_nodd_cache_dir() / "lance_nrt"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    local = cache_dir / name
    if local.exists() and local.stat().st_size > 0:
        return str(local)
    date = _lance_granule_dt(name)
    if date is None:
        return None
    product = "VNP02IMG_NRT" if name.startswith("VNP02") \
        else "VNP03IMG_NRT"
    doy = date.timetuple().tm_yday
    url = f"{LANCE_NRT_BASE}/{product}/{date.year:04d}/{doy:03d}/{name}"
    if not token:
        return None
    from .podaac_swath import _download_with_token
    try:
        _download_with_token(url, token, local, progress=progress)
        if local.exists() and local.stat().st_size > 0:
            return str(local)
    except Exception as e:
        log.debug("LANCE download %s failed: %s", name, e)
    try:
        local.unlink()
    except Exception:
        pass
    return None


def _viirs_l1b_i5_arrays(sdr_path, geo_path, max_points=8000):
    """Return (lat, lon, i5_tb) subsampled arrays for the whole VIIRS I5
    (11.45 um) brightness-temperature swath from L1B (VNP02/VNP03IMG)
    netCDF-4 granules. I5 is stored as scaled radiance integers; BT comes
    from the file's 1-D brightness-temperature LUT (SI -> K)."""
    import numpy as np
    si = _read_h5(sdr_path, "observation_data/I05")
    lut = _read_h5(sdr_path, f"observation_data/{VIIRS_I5_LUT}")
    qf = _read_h5(sdr_path, "observation_data/I05_quality_flags")
    lat = _read_h5(geo_path, "geolocation_data/latitude")
    lon = _read_h5(geo_path, "geolocation_data/longitude")
    if si is None or lut is None or lat is None or lon is None:
        return None, None, None
    si = np.asarray(si)
    lut = np.asarray(lut, dtype=np.float64).ravel()
    idx = np.asarray(si, dtype=np.int64)
    ok = (idx >= 0) & (idx < lut.size)
    tb = np.full(idx.shape, np.nan, dtype=np.float64)
    tb[ok] = lut[idx[ok]]
    if qf is not None and np.asarray(qf).shape == tb.shape:
        tb = np.where(np.asarray(qf) != 0, np.nan, tb)
    glat = np.asarray(lat, dtype=np.float64)
    glon = np.asarray(lon, dtype=np.float64)
    if glat.shape != tb.shape or glon.shape != tb.shape:
        return None, None, None
    glat = glat.ravel()
    glon = glon.ravel()
    tb = tb.ravel()
    okidx = np.isfinite(tb) & np.isfinite(glat) & np.isfinite(glon)
    idx2 = np.flatnonzero(okidx)
    if idx2.size == 0:
        return None, None, None
    if idx2.size > max_points:
        step = max(1, idx2.size // max_points)
        idx2 = idx2[::step]
    return glat[idx2], glon[idx2], tb[idx2]


def _viirs_l1b_i5_coverage_stats(sdr_path, geo_path, storm_lat, storm_lon):
    """Count usable I5 brightness-temperature samples near a storm from a
    VNP02IMG/VNP03IMG L1B pair."""
    import numpy as np
    si = _read_h5(sdr_path, "observation_data/I05")
    lut = _read_h5(sdr_path, f"observation_data/{VIIRS_I5_LUT}")
    qf = _read_h5(sdr_path, "observation_data/I05_quality_flags")
    lat = _read_h5(geo_path, "geolocation_data/latitude")
    lon = _read_h5(geo_path, "geolocation_data/longitude")
    if si is None or lut is None or lat is None or lon is None:
        return None
    si = np.asarray(si)
    lut = np.asarray(lut, dtype=np.float64).ravel()
    idx = np.asarray(si, dtype=np.int64)
    ok = (idx >= 0) & (idx < lut.size)
    tb = np.full(idx.shape, np.nan, dtype=np.float64)
    tb[ok] = lut[idx[ok]]
    if qf is not None and np.asarray(qf).shape == tb.shape:
        tb = np.where(np.asarray(qf) != 0, np.nan, tb)
    glat = np.asarray(lat, dtype=np.float64)
    glon = np.asarray(lon, dtype=np.float64)
    d = np.hypot((glat - float(storm_lat)) * 111.0,
                 (glon - float(storm_lon)) * 111.0
                 * np.cos(np.radians(float(storm_lat))))
    near = d <= 150.0
    okfill = near & np.isfinite(tb)
    if int(okfill.sum()) == 0:
        return {"coverage_points": 0, "min_distance_km": round(float(np.nanmin(d)), 1),
                "tb_min": None, "tb_max": None}
    return {
        "coverage_points": int(okfill.sum()),
        "min_distance_km": round(float(np.nanmin(d)), 1),
        "tb_min": round(float(np.nanmin(tb[okfill])), 1),
        "tb_max": round(float(np.nanmax(tb[okfill])), 1),
    }


def find_viirs_i5_overpass_earthdata(lat, lon, account, target_dt=None,
                                     max_age_hours=None, max_distance_km=None,
                                     cache_dir=None, progress=None):
    """Find + download the NASA LANCE NRT VIIRS I5 (11.45 um) L1B overpass
    nearest to (lat, lon) using the stored NASA Earthdata account (first
    choice). Returns the same info-dict shape as the NODD path, or None."""
    import numpy as np
    from ..clients.podaac_swath import get_urs_token

    username = (account or {}).get("username") or ""
    password = (account or {}).get("password") or ""
    if not username or not password:
        if progress:
            progress("NASA Earthdata account has no username/password.")
        return None
    if progress:
        progress("Getting Earthdata Login token for LANCE NRT VIIRS...")
    try:
        token = get_urs_token(username, password)
    except Exception as e:
        if progress:
            progress(f"Earthdata Login token failed: {e}")
        return None

    max_age_hours = max_age_hours or VIIRS_NRT_MAX_AGE_HOURS
    max_distance_km = max_distance_km or MAX_OVERPASS_DISTANCE_KM
    if target_dt is None:
        target_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    if getattr(target_dt, "tzinfo", None) is not None:
        target_dt = target_dt.replace(tzinfo=None)

    window = timedelta(hours=max_age_hours)
    start, end = target_dt - window, target_dt + window
    dates = {target_dt.date(), start.date(), end.date()}

    candidates = []
    for date in sorted(dates):
        sdr_by_core = {_lance_core(n): n
                       for n in _lance_keys("VNP02IMG_NRT", date)}
        for gn in _lance_keys("VNP03IMG_NRT", date):
            gdt = _lance_granule_dt(gn)
            if gdt is None or gdt < start or gdt >= end:
                continue
            sn = sdr_by_core.get(_lance_core(gn))
            if sn:
                candidates.append(("LANCE", gn, sn, gdt))

    def _probe(cand):
        _src, gn, sn, gdt = cand
        gl = _lance_download(gn, cache_dir, token=token)
        if gl is None:
            return None
        glat = _read_h5(gl, "geolocation_data/latitude")
        glon = _read_h5(gl, "geolocation_data/longitude")
        if glat is None or glon is None:
            return None
        d = np.hypot((np.asarray(glat) - float(lat)) * 111.0,
                     (np.asarray(glon) - float(lon)) * 111.0
                     * np.cos(np.radians(float(lat))))
        return (float(np.nanmin(d)), cand, gl)

    def _probe_many(probe_list):
        results = []
        if not probe_list:
            return results
        if len(probe_list) > 1:
            try:
                import concurrent.futures as _cf
                workers = max(2, min(8, len(probe_list)))
                with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = [r for r in ex.map(_probe, probe_list) if r]
            except Exception:
                results = [r for r in (_probe(c) for c in probe_list) if r]
        else:
            results = [r for r in (_probe(c) for c in probe_list) if r]
        return results

    candidate_pool = sorted(candidates,
                            key=lambda c: (c[3] - target_dt).total_seconds())

    if not candidate_pool:
        if progress:
            progress(f"No LANCE VIIRS I5 granule covers {lat:.1f}N {lon:.1f}E "
                     f"within {max_age_hours:g}h")
        return None

    sample_step = max(1, len(candidate_pool) // 40)
    sample = candidate_pool[::sample_step]
    sample_hits = _probe_many(sample)
    if not sample_hits:
        if progress:
            progress(f"No LANCE VIIRS I5 granule covers {lat:.1f}N {lon:.1f}E "
                     f"within {max_age_hours:g}h")
        return None

    sample_hits.sort(key=lambda h: h[0])
    best_coarse = sample_hits[0]
    best_coarse_t = best_coarse[1][3]
    dense = [c for c in candidate_pool
             if abs((c[3] - best_coarse_t).total_seconds()) <= 17 * 60]
    if len(dense) > 80:
        dense = sorted(dense,
                       key=lambda c: abs((c[3] - best_coarse_t).total_seconds()))
        dense = dense[:80]
    dense_hits = _probe_many(dense)
    all_hits = sample_hits + dense_hits
    all_hits.sort(key=lambda h: (h[0], abs((h[1][3] - target_dt).total_seconds())))

    best = None
    for mind, cand, gl in all_hits:
        if best is None or mind <= best[5]:
            best = (cand[0], cand[1], cand[2], gl, cand[3], mind)
    if best is None or best[5] > max_distance_km:
        _nearest = min(all_hits, key=lambda h: h[0])
        if best is None and _nearest is not None:
            best = (_nearest[1][0], _nearest[1][1], _nearest[1][2],
                    _nearest[2], _nearest[1][3], _nearest[0])
        if progress:
            how = (f"{best[5]:.0f} km away" if best else "no granule found")
            progress(f"Nearest LANCE VIIRS I5 granule is {how}; "
                     f"{max_distance_km:g} km was requested")

    _src, gk, sk, gl, dt, mind = best
    sl = _lance_download(sk, cache_dir, token=token)
    if sl is None:
        return None
    stats = _viirs_l1b_i5_coverage_stats(sl, gl, lat, lon)
    swath_lat, swath_lon, swath_tb = _viirs_l1b_i5_arrays(sl, gl)
    if progress:
        progress(f"Downloaded VIIRS I5 granule {os.path.basename(sk)}")
    return {
        "time": dt,
        "time_str": dt.strftime("%Y-%m-%d %H:%M UTC"),
        "satellite": "Suomi NPP",
        "bucket": "NASA Earthdata — LANCE NRT",
        "sdr_key": sk,
        "geo_key": gk,
        "sdr_file": sl,
        "geo_file": gl,
        "storm_latitude": float(lat),
        "storm_longitude": float(lon),
        "age_hours": round(abs((dt - target_dt).total_seconds()) / 3600.0, 3),
        "distance_km": round(mind, 1),
        "channel_ghz": None,
        "channel_um": 11.45,
        "coverage_points": (stats or {}).get("coverage_points", 0),
        "tb_min": (stats or {}).get("tb_min"),
        "tb_max": (stats or {}).get("tb_max"),
        "swath_lat": swath_lat,
        "swath_lon": swath_lon,
        "swath_tb": swath_tb,
        "source": "viirs_i5",
    }


def find_viirs_i5_overpass(lat, lon, target_dt=None, max_age_hours=None,
                           max_distance_km=None, cache_dir=None, progress=None,
                           account=None):
    """Find + download the VIIRS I5 (11.45 um) overpass nearest to (lat, lon).
    Uses the same two-stage probe as ATMS. When a NASA Earthdata account is
    given, the NASA LANCE NRT archive is tried first; NODD JPSS is the
    fallback."""
    if account:
        info = find_viirs_i5_overpass_earthdata(
            lat, lon, account, target_dt=target_dt,
            max_age_hours=max_age_hours, max_distance_km=max_distance_km,
            cache_dir=cache_dir, progress=progress)
        if info is not None:
            return info
        if progress:
            progress("NASA Earthdata had no usable VIIRS I5 granule — "
                     "falling back to NODD JPSS...")
    import numpy as np
    from ..clients.microwave import MAX_OVERPASS_AGE_HOURS as _DEF_AGE
    from ..clients.microwave import MAX_OVERPASS_DISTANCE_KM as _DEF_DIST
    max_age_hours = max_age_hours or _DEF_AGE
    max_distance_km = max_distance_km or _DEF_DIST
    if target_dt is None:
        target_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    if getattr(target_dt, "tzinfo", None) is not None:
        target_dt = target_dt.replace(tzinfo=None)

    window = timedelta(hours=max_age_hours)
    start, end = target_dt - window, target_dt + window
    dates = {target_dt.date(), start.date(), end.date()}

    candidates = []
    for bucket in NODD_BUCKETS:
        for date in sorted(dates):
            geo_keys = _list_keys(bucket, NODD_VIIRS_IMG_GEO_PRODUCT, date)
            sdr_keys = {_granule_core(k): k
                        for k in _list_keys(bucket, NODD_VIIRS_I5_PRODUCT, date)}
            for gk in geo_keys:
                gdt = _granule_dt(gk)
                if gdt is None or gdt < start or gdt >= end:
                    continue
                sk = sdr_keys.get(_granule_core(gk))
                if sk:
                    candidates.append((bucket, gk, sk, gdt))

    def _probe(cand):
        bucket, gk, sk, gdt = cand
        gl = _download(gk, bucket, cache_dir)
        if gl is None:
            return None
        glat = _read_h5(gl, "/All_Data/VIIRS-IMG-GEO-TC_All/Latitude")
        glon = _read_h5(gl, "/All_Data/VIIRS-IMG-GEO-TC_All/Longitude")
        if glat is None or glon is None:
            return None
        d = np.hypot((np.asarray(glat) - float(lat)) * 111.0,
                     (np.asarray(glon) - float(lon)) * 111.0
                     * np.cos(np.radians(float(lat))))
        return (float(np.nanmin(d)), cand, gl)

    def _probe_many(probe_list):
        results = []
        if not probe_list:
            return results
        if len(probe_list) > 1:
            try:
                import concurrent.futures as _cf
                workers = max(2, min(8, len(probe_list)))
                with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
                    results = [r for r in ex.map(_probe, probe_list) if r]
            except Exception:
                results = [r for r in (_probe(c) for c in probe_list) if r]
        else:
            results = [r for r in (_probe(c) for c in probe_list) if r]
        return results

    candidate_pool = sorted(candidates,
                            key=lambda c: (c[3] - target_dt).total_seconds())

    if not candidate_pool:
        if progress:
            progress(f"No VIIRS I5 granule covers {lat:.1f}N {lon:.1f}E "
                     f"within {max_age_hours:g}h")
        return None

    sample_step = max(1, len(candidate_pool) // 40)
    sample = candidate_pool[::sample_step]
    sample_hits = _probe_many(sample)
    if not sample_hits:
        if progress:
            progress(f"No VIIRS I5 granule covers {lat:.1f}N {lon:.1f}E "
                     f"within {max_age_hours:g}h")
        return None

    sample_hits.sort(key=lambda h: h[0])
    best_coarse = sample_hits[0]
    best_coarse_t = best_coarse[1][3]
    dense = [c for c in candidate_pool
             if abs((c[3] - best_coarse_t).total_seconds()) <= 17 * 60]
    if len(dense) > 80:
        dense = sorted(dense,
                       key=lambda c: abs((c[3] - best_coarse_t).total_seconds()))
        dense = dense[:80]
    dense_hits = _probe_many(dense)
    all_hits = sample_hits + dense_hits
    all_hits.sort(key=lambda h: (h[0], abs((h[1][3] - target_dt).total_seconds())))

    best = None
    for mind, cand, gl in all_hits:
        if best is None or mind <= best[5]:
            best = (cand[0], cand[1], cand[2], gl, cand[3], mind)
    if best is None or best[5] > max_distance_km:
        _nearest = min(all_hits, key=lambda h: h[0])
        if best is None and _nearest is not None:
            best = (_nearest[1][0], _nearest[1][1], _nearest[1][2],
                    _nearest[2], _nearest[1][3], _nearest[0])
        if progress:
            how = (f"{best[5]:.0f} km away" if best else "no granule found")
            progress(f"Nearest VIIRS I5 granule is {how}; "
                     f"{max_distance_km:g} km was requested")

    bucket, gk, sk, gl, dt, mind = best
    sl = _download(sk, bucket, cache_dir)
    if sl is None:
        return None
    stats = _viirs_i5_coverage_stats(sl, gl, lat, lon)
    swath_lat, swath_lon, swath_tb = _viirs_i5_arrays(sl, gl)
    if progress:
        progress(f"Downloaded VIIRS I5 granule {os.path.basename(sk)}")
    return {
        "time": dt,
        "time_str": dt.strftime("%Y-%m-%d %H:%M UTC"),
        "satellite": _satellite_for_key(sk),
        "bucket": bucket,
        "sdr_key": sk,
        "geo_key": gk,
        "sdr_file": sl,
        "geo_file": gl,
        "storm_latitude": float(lat),
        "storm_longitude": float(lon),
        "age_hours": round(abs((dt - target_dt).total_seconds()) / 3600.0, 3),
        "distance_km": round(mind, 1),
        "channel_ghz": None,
        "channel_um": 11.45,
        "coverage_points": (stats or {}).get("coverage_points", 0),
        "tb_min": (stats or {}).get("tb_min"),
        "tb_max": (stats or {}).get("tb_max"),
        "swath_lat": swath_lat,
        "swath_lon": swath_lon,
        "swath_tb": swath_tb,
        "source": "viirs_i5",
    }


class VIIRSI5Downloader(QObject):
    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, lat, lon, target_dt=None, cache_dir=None, parent=None,
                 account=None):
        super().__init__(parent)
        self.lat = lat
        self.lon = lon
        self.target_dt = target_dt
        self.cache_dir = cache_dir
        self.account = account
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = find_viirs_i5_overpass(
                self.lat, self.lon, target_dt=self.target_dt,
                cache_dir=self.cache_dir, account=self.account,
                progress=lambda m: self.progress.emit(m))
            if self._cancelled:
                self.finished.emit()
                return
            if info is None:
                self.error.emit(
                    "No VIIRS I5 (11.45 um) SDR granule covers that position "
                    "within the age window.")
                return
            self.result.emit(info)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# ---------------------------------------------------------------------------
# NOAA OSPO — raw GCOM-W1 AMSR2 L1B brightness-temperature granules
# ---------------------------------------------------------------------------

AMSR2_L1B_BASE = "https://satepsanone.nesdis.noaa.gov/pub/AMSR2/L1B"
AMSR2_L1B_SCAN_EPOCH = datetime(1993, 1, 1, tzinfo=timezone.utc)


def read_amsr2_l1b(path, channel="89.0GHz-A", pol="H", max_points=12000):
    """Read raw AMSR2 L1B brightness temperature into (lat, lon, tb) arrays.

    BT is uint16 increments of 0.01 K; quality flag 0 = good; geolocation is
    the per-footprint 89A/B observation point. Returns (None,)*3 on failure.
    """
    import numpy as np
    try:
        import h5py
    except Exception:
        return None, None, None
    bt_name = f"Brightness Temperature ({channel},{pol})"
    lat_name = f"Latitude of Observation Point for 89A" if channel.startswith("89") \
        else "Latitude of Observation Point for 89A"
    lon_name = lat_name.replace("Latitude", "Longitude")
    try:
        with h5py.File(path, "r") as f:
            bt = np.asarray(f[bt_name][:], dtype=np.float64) * 0.01
            lat = np.asarray(f[lat_name][:], dtype=np.float64)
            lon = np.asarray(f[lon_name][:], dtype=np.float64)
            qf_name = "Pixel Data Quality 89" if channel.startswith("89") \
                else "Pixel Data Quality 6 to 36"
            if qf_name in f:
                qf = np.asarray(f[qf_name][:], dtype=np.int64)
                if qf.shape == bt.shape:
                    bt = np.where(qf != 0, np.nan, bt)
    except Exception as e:
        log.debug("AMSR2 L1B read %s failed: %s", os.path.basename(path), e)
        return None, None, None
    if bt.ndim != 2 or lat.shape != bt.shape or lon.shape != bt.shape:
        return None, None, None
    glat = lat.ravel()
    glon = lon.ravel()
    tb = bt.ravel()
    ok = (np.isfinite(glat) & np.isfinite(glon) & np.isfinite(tb)
          & (glat != 0.0) & (glon != 0.0))
    glat, glon, tb = glat[ok], glon[ok], tb[ok]
    if glat.size == 0:
        return None, None, None
    if glat.size > max_points:
        step = max(1, int(np.ceil(glat.size / max_points)))
        glat, glon, tb = glat[::step], glon[::step], tb[::step]
    return glat, glon, tb


def list_amsr2_l1b_ospo(progress=None):
    """List the raw AMSR2 L1B granule files NOAA OSPO publishes publicly."""
    try:
        html = _get(AMSR2_L1B_BASE + "/", timeout=40)
        names = re.findall(r"(GW1AM2_\d{12}_\d{3}[AD]_L1DLBTBR_\d{7}\.h5)",
                           html, re.I)
        granules = []
        for n in sorted(set(names)):
            m = re.search(r"GW1AM2_(\d{12})_(\d{3}[AD])_", n)
            if not m:
                continue
            try:
                t = datetime.strptime(m.group(1), "%Y%m%d%H%M").replace(
                    tzinfo=timezone.utc)
            except ValueError:
                continue
            granules.append({
                "name": n,
                "time": t,
                "time_str": t.strftime("%Y-%m-%d %H:%M UTC"),
                "orbit": m.group(2),
                "url": AMSR2_L1B_BASE + "/" + n,
            })
        granules.sort(key=lambda g: g["time"])
        return granules
    except Exception as e:
        log.warning("OSPO AMSR2 L1B listing failed: %s", e)
        return []


def find_amsr2_l1b(lat, lon, target_dt=None, max_age_hours=None,
                   max_distance_km=None, cache_dir=None, progress=None):
    """Find the OSPO raw AMSR2 L1B granule nearest in time to (lat, lon),
    download it, and return the 89.0 GHz-A field for a viewport overlay."""
    import numpy as np
    if max_age_hours is None:
        max_age_hours = 12.0
    if max_distance_km is None:
        max_distance_km = 400.0
    if target_dt is None:
        target_dt = datetime.now(timezone.utc).replace(tzinfo=None)
    if getattr(target_dt, "tzinfo", None) is not None:
        target_dt = target_dt.replace(tzinfo=None)
    target_dt = target_dt.replace(tzinfo=timezone.utc)

    granules = list_amsr2_l1b_ospo(progress=progress)
    if not granules:
        if progress:
            progress("No raw AMSR2 L1B granules in the OSPO archive.")
        return None
    window = timedelta(hours=max_age_hours)
    near = [g for g in granules
            if abs((g["time"] - target_dt).total_seconds()) <= window.total_seconds()]
    if not near:
        near = granules
    near.sort(key=lambda g: abs((g["time"] - target_dt).total_seconds()))
    g = near[0]

    if cache_dir is None:
        cache_dir = get_microwave_data_dir() / "amsr2_l1b"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    local = cache_dir / g["name"]
    if not (local.exists() and local.stat().st_size > 0):
        if progress:
            progress(f"Downloading AMSR2 L1B {g['name']} "
                     f"({g['time_str']})...")
        with requests.get(g["url"], timeout=300, stream=True,
                          headers={"User-Agent": _UA}) as r:
            r.raise_for_status()
            tmp = local.with_suffix(local.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        fh.write(chunk)
            tmp.replace(local)

    swath_lat, swath_lon, swath_tb = read_amsr2_l1b(str(local))
    if swath_lat is None:
        if progress:
            progress(f"AMSR2 L1B {g['name']} could not be read.")
        return None
    d = np.hypot((swath_lat - float(lat)) * 111.0,
                 (swath_lon - float(lon)) * 111.0
                 * np.cos(np.radians(float(lat))))
    mind = float(np.nanmin(d)) if d.size else float("inf")
    tb_ok = swath_tb[np.isfinite(swath_tb)]
    if progress:
        progress(f"Fetched AMSR2 L1B {g['name']} 89.0 GHz-A")
    return {
        "time": g["time"],
        "time_str": g["time_str"],
        "satellite": "GCOM-W1 / AMSR2",
        "storm": "raw L1B",
        "bucket": "NOAA OSPO satepsanone.nesdis.noaa.gov",
        "sdr_file": str(local),
        "sdr_key": g["name"],
        "geo_file": str(local),
        "storm_latitude": float(lat),
        "storm_longitude": float(lon),
        "age_hours": round(abs((g["time"] - target_dt).total_seconds()) / 3600.0, 3),
        "distance_km": round(mind, 1),
        "channel_ghz": 89.0,
        "coverage_points": int(np.sum(np.isfinite(swath_tb))),
        "tb_min": round(float(np.nanmin(tb_ok)), 1) if tb_ok.size else None,
        "tb_max": round(float(np.nanmax(tb_ok)), 1) if tb_ok.size else None,
        "swath_lat": swath_lat,
        "swath_lon": swath_lon,
        "swath_tb": swath_tb,
        "source": "amsr2_raw",
    }


class AMSR2RawDownloader(QObject):
    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, lat, lon, target_dt=None, cache_dir=None, parent=None):
        super().__init__(parent)
        self.lat = lat
        self.lon = lon
        self.target_dt = target_dt
        self.cache_dir = cache_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = find_amsr2_l1b(
                self.lat, self.lon, target_dt=self.target_dt,
                cache_dir=self.cache_dir,
                progress=lambda m: self.progress.emit(m))
            if self._cancelled:
                self.finished.emit()
                return
            if info is None:
                self.error.emit(
                    "No raw AMSR2 L1B granule available near that position.")
                return
            self.result.emit(info)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


def collect_amsr2_raw_status(progress=None):
    """Latest availability of the NOAA OSPO raw AMSR2 L1B archive."""
    products = []
    errors = []
    try:
        granules = list_amsr2_l1b_ospo(progress)
        updated = ""
        if granules:
            latest = granules[-1]
            updated = latest["time_str"]
        products.append({
            "name": "NOAA OSPO — GCOM-W1 AMSR2 L1B raw (89 GHz)",
            "product_id": "AMSR2-L1B",
            "status": (f"{len(granules)} granule(s) online"
                       if granules else "Archive unreachable"),
            "updated": updated,
            "url": AMSR2_L1B_BASE + "/",
        })
    except Exception as e:
        errors.append(f"AMSR2 L1B (OSPO): {e}")
    return {"products": products, "errors": errors}


def _latest_nodd_granule(product, date):
    """Latest granule time (aware datetime) + count for a product today."""
    latest = None
    count = 0
    for bucket in NODD_BUCKETS:
        keys = _list_keys(bucket, product, date)
        for k in keys:
            count += 1
            dt = _granule_dt(k)
            if dt is not None and (latest is None or dt > latest):
                latest = dt
    return latest, count


def collect_nodd_status(progress=None):
    """Latest availability of NODD ATMS / VIIRS granules for the Products tab."""
    products = []
    errors = []
    today = datetime.now(timezone.utc).date()

    def log(msg):
        if progress:
            progress(msg)

    for product, label in (
        (NODD_ATMS_PRODUCT, "ATMS 88.2 GHz SDR (N20/N21/SNPP)"),
        ("VIIRS-DNB-SDR", "VIIRS Day/Night Band SDR"),
        ("VIIRS-I1-SDR", "VIIRS I1 (640 m) SDR"),
    ):
        try:
            log(f"Checking NODD {product} availability...")
            latest, count = _latest_nodd_granule(product, today)
            updated = ""
            if latest is not None:
                updated = latest.replace(tzinfo=timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC")
            products.append({
                "name": f"NODD JPSS — {label}",
                "product_id": product,
                "status": "Active" if count else "No granules today",
                "updated": updated,
                "url": "https://registry.opendata.aws/noaa-jpss/",
            })
        except Exception as e:
            errors.append(f"NODD {product}: {e}")
            log(f"  Failed: {e}")
    return {"products": products, "errors": errors}


# ---------------------------------------------------------------------------
# MIMIC-TC2 (CIMSS/SSEC) — free coherent 89 GHz TC brightness-temperature fields
# ---------------------------------------------------------------------------

def _mimic_get(url, timeout=40):
    return _get(url, timeout=timeout)


def list_mimic_storms(progress=None):
    """List active MIMIC-TC2 storm directories (e.g. 2026_16W)."""
    try:
        html = _mimic_get(MIMIC_TC2_BASE + "/", timeout=40)
        dirs = sorted({m.group(1) for m in MIMIC_STORM_RE.finditer(html)})
        storms = []
        for d in dirs:
            basin = d.rsplit("_", 1)[-1] if "_" in d else ""
            storms.append({
                "storm_dir": d,
                "storm_id": basin,
                "basin": basin,
                "instrument": "MIMIC-TC2 89 GHz",
                "url": f"{MIMIC_TC2_BASE}/{d}/",
            })
        return storms
    except Exception as e:
        log.warning("MIMIC-TC2 storm listing failed: %s", e)
        return []


def _mimic_latest_nc(storm_dir):
    """Return (url, time) of the newest 15-minute NetCDF granule for a storm."""
    idx_url = f"{MIMIC_TC2_BASE}/{storm_dir}/web/data/"
    html = _mimic_get(idx_url, timeout=40)
    hits = []
    now = datetime.now(timezone.utc)
    for m in MIMIC_NC_RE.finditer(html):
        try:
            t = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if abs((t - now).total_seconds()) <= MIMIC_NC_MAX_AGE_HOURS * 3600:
            hits.append((t, m.group(1)))
    if not hits:
        return None
    hits.sort(key=lambda h: h[0], reverse=True)
    t, token = hits[0]
    return f"{MIMIC_TC2_BASE}/{storm_dir}/web/data/{token}.nc", t


def _download_mimic_nc(storm_dir, cache_dir=None, progress=None):
    """Download the latest MIMIC-TC2 NetCDF granule for a storm, cache it, and
    return (local_path, url, valid_time)."""
    if cache_dir is None:
        cache_dir = get_microwave_data_dir() / "mimic_tc2"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    found = _mimic_latest_nc(storm_dir)
    if found is None:
        return None, None, None
    url, valid_time = found
    local = cache_dir / os.path.basename(url)
    if not (local.exists() and local.stat().st_size > 0):
        if progress:
            progress(f"Downloading MIMIC-TC2 {os.path.basename(url)}...")
        with requests.get(url, timeout=120, stream=True,
                          headers={"User-Agent": _UA}) as r:
            r.raise_for_status()
            tmp = local.with_suffix(local.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        fh.write(chunk)
            tmp.replace(local)
    return str(local), url, valid_time


def read_mimic_nc(path, max_points=20000):
    """Read a MIMIC-TC2 NetCDF into (lat, lon, tb_k) 1-D arrays.

    BT_K = uint8 * scale_factor + add_offset; _FillValue (255) -> NaN.
    """
    import numpy as np
    try:
        import h5py
    except Exception:
        return None, None, None
    try:
        with h5py.File(path, "r") as f:
            lat = np.asarray(f["latitude"][:], dtype=np.float64)
            lon = np.asarray(f["longitude"][:], dtype=np.float64)
            bt_raw = f["mimic_tc_89GHz_bt"]
            bt = np.asarray(bt_raw[:], dtype=np.float64)
            scale = _scalar_attr(bt_raw.attrs.get("scale_factor"), 1.0)
            off = _scalar_attr(bt_raw.attrs.get("add_offset"), 0.0)
            fill = np.asarray(bt_raw.attrs.get("_FillValue", None))
    except Exception as e:
        log.debug("MIMIC NC read failed: %s", e)
        return None, None, None
    if bt.ndim != 2:
        return None, None, None
    bt = bt * scale + off
    if fill is not None:
        fill_v = float(np.asarray(fill).reshape(-1)[0])
        bt = np.where(bt == fill_v, np.nan, bt)
    glat, glon = np.meshgrid(lat, lon, indexing="ij")
    glat = glat.ravel()
    glon = glon.ravel()
    tb = bt.ravel()
    ok = np.isfinite(glat) & np.isfinite(glon) & np.isfinite(tb)
    glat, glon, tb = glat[ok], glon[ok], tb[ok]
    if glat.size == 0:
        return None, None, None
    if glat.size > max_points:
        step = max(1, int(np.ceil(glat.size / max_points)))
        glat, glon, tb = glat[::step], glon[::step], tb[::step]
    return glat, glon, tb


def find_mimic_storm(lat, lon, target_dt=None, max_age_hours=None,
                     cache_dir=None, progress=None):
    """Find the MIMIC-TC2 storm whose grid covers (lat, lon) and download its
    latest NetCDF. Returns an info dict compatible with the ATMS overlay.

    Storm dirs encode the JTWC/NHC storm number + basin (e.g. 2026_16W).
    """
    import numpy as np
    if max_age_hours is None:
        max_age_hours = MIMIC_NC_MAX_AGE_HOURS
    storms = list_mimic_storms(progress=progress)
    if not storms:
        if progress:
            progress("No MIMIC-TC2 storm products available.")
        return None

    # The grid is storm-relative (a box centered on the storm), so the nearest
    # active storm to the request point is a good candidate; then we validate
    # by checking the actual grid bounds of its latest granule.
    candidates = []
    for s in storms:
        sd = s["storm_dir"]
        m = re.match(r"(\d{4})_(\d{1,2})([A-Za-z])", sd)
        if not m:
            continue
        lat_d = float(m.group(2)) * 1.0  # placeholder, not used
        candidates.append(s)
    if not candidates:
        return None

    best = None
    best_score = None
    for s in candidates:
        local, url, vt = _download_mimic_nc(s["storm_dir"], cache_dir=cache_dir,
                                            progress=progress)
        if local is None:
            continue
        glat, glon, tb = read_mimic_nc(local, max_points=6000)
        if glat is None:
            continue
        in_box = (np.min(glat) <= float(lat) <= np.max(glat)
                  and np.min(glon) <= float(lon) <= np.max(glon))
        center_d = float(np.hypot(
            (np.mean(glat) - float(lat)) * 111.0,
            (np.mean(glon) - float(lon)) * 111.0
            * np.cos(np.radians(float(lat)))))
        score = (0 if in_box else 1, center_d)
        if best_score is None or score < best_score:
            best_score = score
            best = (s, local, url, vt, glat, glon, tb)
    if best is None:
        if progress:
            progress("No MIMIC-TC2 storm granule could be read.")
        return None
    s, local, url, vt, glat, glon, tb = best
    in_box = (np.min(glat) <= float(lat) <= np.max(glat)
              and np.min(glon) <= float(lon) <= np.max(glon))
    if not in_box:
        if progress:
            progress(f"MIMIC-TC2 storm {s['storm_dir']} grid does not cover "
                     f"{lat:.1f}N {lon:.1f}E; nearest granule used.")
    # Re-read full resolution for the overlay.
    glat, glon, tb = read_mimic_nc(local, max_points=20000)
    tb_ok = tb[np.isfinite(tb)] if tb is not None else np.array([])
    return {
        "time": vt,
        "time_str": vt.strftime("%Y-%m-%d %H:%M UTC") if vt else "?",
        "satellite": f"MIMIC-TC2 ({s['basin']})",
        "storm": s["storm_dir"],
        "bucket": "CIMSS/SSEC tropic.ssec.wisc.edu",
        "sdr_file": local,
        "sdr_key": os.path.basename(url or ""),
        "geo_file": local,
        "storm_latitude": float(lat),
        "storm_longitude": float(lon),
        "age_hours": round(abs((vt - datetime.now(timezone.utc)).total_seconds())
                           / 3600.0, 3) if vt else None,
        "distance_km": 0.0,
        "channel_ghz": 89.0,
        "coverage_points": int(np.sum(np.isfinite(tb))) if tb is not None else 0,
        "tb_min": round(float(np.nanmin(tb_ok)), 1) if tb_ok.size else None,
        "tb_max": round(float(np.nanmax(tb_ok)), 1) if tb_ok.size else None,
        "swath_lat": glat,
        "swath_lon": glon,
        "swath_tb": tb,
        "source": "mimic_tc2",
    }


def collect_mimic_status(progress=None):
    """Latest availability of MIMIC-TC2 storm products for the Products tab."""
    products = []
    errors = []
    try:
        storms = list_mimic_storms(progress)
        latest = ""
        count = len(storms)
        if storms:
            for s in storms:
                found = _mimic_latest_nc(s["storm_dir"])
                if found is not None:
                    latest = max(latest, found[1].strftime("%Y-%m-%d %H:%M UTC"))
        products.append({
            "name": "CIMSS/SSEC — MIMIC-TC2 89 GHz coherent TB fields",
            "product_id": "MIMIC-TC2",
            "status": "Active" if count else "No active storms",
            "updated": latest,
            "url": MIMIC_TC2_BASE + "/",
        })
    except Exception as e:
        errors.append(f"MIMIC-TC2: {e}")
    return {"products": products, "errors": errors}


class MIMICDownloader(QObject):
    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, lat, lon, target_dt=None, cache_dir=None, parent=None):
        super().__init__(parent)
        self.lat = lat
        self.lon = lon
        self.target_dt = target_dt
        self.cache_dir = cache_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = find_mimic_storm(
                self.lat, self.lon, target_dt=self.target_dt,
                cache_dir=self.cache_dir,
                progress=lambda m: self.progress.emit(m))
            if self._cancelled:
                self.finished.emit()
                return
            if info is None:
                self.error.emit(
                    "No MIMIC-TC2 89 GHz field covers that position.")
                return
            self.result.emit(info)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# ---------------------------------------------------------------------------
# Top-level collection
# ---------------------------------------------------------------------------

def collect_microwave_data(progress=None):
    """Gather AMSR2 storms, NODD availability and NASA Earthdata sources."""
    now = datetime.now(timezone.utc)
    errors = []
    products = []
    storms = []

    def log(msg):
        if progress:
            progress(msg)

    amsr2 = collect_amsr2_data(progress)
    storms = amsr2["storms"]
    products.extend(amsr2["products"])
    errors.extend(amsr2["errors"])

    nodd = collect_nodd_status(progress)
    products.extend(nodd["products"])
    errors.extend(nodd["errors"])

    mimic = collect_mimic_status(progress)
    products.extend(mimic["products"])
    errors.extend(mimic["errors"])

    amsr2raw = collect_amsr2_raw_status(progress)
    products.extend(amsr2raw["products"])
    errors.extend(amsr2raw["errors"])

    for c in NASA_COLLECTIONS:
        products.append({**c})

    return {
        "fetched_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "satellites": ["GCOM-W1 / AMSR2", "JPSS ATMS", "JPSS VIIRS",
                       "MIMIC-TC2 89 GHz", "AMSR2 L1B 89 GHz (OSPO raw)"],
        "storms": storms,
        "products": products,
        "errors": errors,
    }


class MicrowaveDownloader(QObject):
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
            data = collect_microwave_data(progress=lambda m: self.progress.emit(m))
            if self._cancelled:
                self.finished.emit()
                return
            self.result.emit(data)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------

def download_amsr2_pass(storm, dest_dir=None, progress=None):
    """Download the newest AMSR2 storm pass images (37/89 GHz) to disk."""
    if dest_dir is None:
        dest_dir = get_microwave_data_dir() / "amsr2"
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    storm_id = (storm or {}).get("storm_id") or "storm"
    detail_url = (storm or {}).get("product_url")
    if not detail_url:
        year = datetime.now(timezone.utc).year
        detail_url = (
            f"{AMSR2_SUB_PAGE}?&myyear={year}&STORM={storm_id}"
            f"&all_flag=0&region={(storm or {}).get('region', '')}&product=0"
        )
    html = _get(detail_url, timeout=40)
    passes = parse_amsr2_storm_passes(html)
    if not passes:
        raise RuntimeError(f"No AMSR2 passes found for {storm_id}")
    latest_time = passes[0]["time"]
    sel = [p for p in passes if p["time"] == latest_time]
    file_paths = []
    for p in sel:
        if progress:
            progress(f"Downloading AMSR2 {p['channel_ghz']:g} GHz "
                     f"{p['polarization']}-pol image...")
        fname = f"{storm_id}_{latest_time:%Y%m%d_%H%M}_{p['channel_ghz']:g}_{p['polarization']}.png"
        fpath = dest_dir / fname
        with requests.get(p["url"], timeout=120, stream=True,
                          headers={"User-Agent": _UA}) as r:
            r.raise_for_status()
            with open(fpath, "wb") as fh:
                for chunk in r.iter_content(chunk_size=262144):
                    if chunk:
                        fh.write(chunk)
        file_paths.append(str(fpath))
    if not file_paths:
        raise RuntimeError(f"Could not download AMSR2 images for {storm_id}")
    channels = sorted({f"{p['channel_ghz']:g} GHz {p['polarization']}"
                       for p in sel})
    return {
        "storm": storm_id,
        "storm_name": storm_id,
        "time": latest_time,
        "time_str": latest_time.strftime("%Y-%m-%d %H:%M UTC"),
        "channels": channels,
        "file_paths": file_paths,
        "file_path": file_paths[0],
    }


class AMSR2PassDownloader(QObject):
    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, storm, dest_dir=None, parent=None):
        super().__init__(parent)
        self.storm = storm
        self.dest_dir = dest_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = download_amsr2_pass(
                self.storm, dest_dir=self.dest_dir,
                progress=lambda m: self.progress.emit(m))
            if self._cancelled:
                self.finished.emit()
                return
            self.result.emit(info)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class ATMSOverpassDownloader(QObject):
    progress = Signal(str)
    result = Signal(dict)
    error = Signal(str)
    finished = Signal()

    def __init__(self, lat, lon, target_dt=None, cache_dir=None, parent=None):
        super().__init__(parent)
        self.lat = lat
        self.lon = lon
        self.target_dt = target_dt
        self.cache_dir = cache_dir
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            info = find_atms_overpass(
                self.lat, self.lon, target_dt=self.target_dt,
                cache_dir=self.cache_dir,
                progress=lambda m: self.progress.emit(m))
            if self._cancelled:
                self.finished.emit()
                return
            if info is None:
                self.error.emit(
                    "No ATMS 88.2 GHz granule covers that position within the "
                    "age window.")
                return
            self.result.emit(info)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


def remove_microwave_cache():
    """Delete downloaded microwave files (data/microwave_data/)."""
    dest = get_microwave_data_dir()
    removed = []
    for p in dest.rglob("*"):
        if p.is_file():
            try:
                p.unlink()
                removed.append(p.name)
            except Exception as e:
                log.warning("Could not delete %s: %s", p.name, e)
    for p in sorted((d for d in dest.rglob("*") if d.is_dir()), reverse=True):
        try:
            p.rmdir()
        except Exception:
            pass
    return removed
