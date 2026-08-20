import sys
import json
import os
from pathlib import Path

# Accept project root from command line (default: derive from script location)
_top_dir = Path(__file__).resolve().parent.parent.parent
for i, arg in enumerate(sys.argv):
    if arg == "--project-root" and i + 1 < len(sys.argv):
        _top_dir = Path(sys.argv[i + 1])
        break
_top_dir_str = str(_top_dir.resolve())
if _top_dir_str not in sys.path:
    sys.path.insert(0, _top_dir_str)
_src_dir = str((_top_dir / "src").resolve())
# Debug: log resolved paths
print(json.dumps({"type": "debug", "top_dir": str(_top_dir), "src_dir": _src_dir}), flush=True)

from PySide6.QtCore import QCoreApplication

# Filter sys.argv to only pass recognized Qt args to QCoreApplication
_qt_argv = [sys.argv[0]]
for i, arg in enumerate(sys.argv[1:], 1):
    if arg.startswith("-") and not arg.startswith("--"):
        _qt_argv.append(arg)
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
            _qt_argv.append(sys.argv[i + 1])
app = QCoreApplication(_qt_argv)

def _log_line(**kwargs):
    kwargs.setdefault("type", "progress")
    print(json.dumps(kwargs), flush=True)

# --- NHC ---
from src.clients.nhc import NHCDownloader, fetch_active_storms, get_nhc_data_dir, _dl_zip_and_extract, _download_file

def run_nhc():
    _log_line(agency="nhc", message="Fetching active storms from NHC...")
    storms = fetch_active_storms()
    if not storms:
        _log_line(type="error", agency="nhc", message="No active storms found on NHC.")
        _log_line(type="finished", agency="nhc")
        return

    nhc_dir = get_nhc_data_dir()
    for storm in storms:
        storm_id = storm.get("id", "").lower()
        storm_name = storm.get("name", "Unknown")
        classification = storm.get("classification", "")
        basin = "EP" if storm_id.startswith("ep") else "AL" if storm_id.startswith("al") else "CP"
        _log_line(agency="nhc", message=f"Processing {storm_name} ({storm_id})...")

        storm_dir = nhc_dir / storm_id
        storm_dir.mkdir(exist_ok=True)

        result = {
            "storm_id": storm_id, "storm_name": storm_name,
            "classification": classification, "basin": basin,
            "local_dir": str(storm_dir),
            "track_points": [], "cone_polygons": [],
            "kmz_track": None, "kmz_cone": None, "advisory_number": "",
            "wind_radii_initial": {"34":[],"50":[],"64":[]},
            "wind_radii_forecast": {"34":[],"50":[],"64":[]},
            "kmz_wind_initial": None, "kmz_wind_forecast": None,
            "best_track_points": [], "best_track_line": [], "best_track_kmz": None,
            "kmz_arrival_earliest": None, "kmz_arrival_likely": None,
            "prob_polygons_34": [], "prob_polygons_50": [], "prob_polygons_64": [],
        }

        ft = storm.get("forecastTrack", {})
        tc = storm.get("trackCone", {})
        iwe = storm.get("initialWindExtent", {})
        fwr = storm.get("forecastWindRadiiGIS", {})
        btg = storm.get("bestTrackGIS", {})
        eat = storm.get("earliestArrivalTimeTSWindsGIS", {})
        mlt = storm.get("mostLikelyTimeTSWindsGIS", {})
        wsp = storm.get("windSpeedProbabilitiesGIS", {})

        result["advisory_number"] = ft.get("advNum", "") or tc.get("advNum", "")

        # ---- 5-day Track and Cone KMZs ----
        for key, kmz_key, url_key, fallback in [
            ("kmz_track", "TRACK", "kmzFile", f"https://www.nhc.noaa.gov/storm_graphics/api/{storm_id.upper()}_TRACK_latest.kmz"),
            ("kmz_cone", "CONE", "kmzFile", f"https://www.nhc.noaa.gov/storm_graphics/api/{storm_id.upper()}_CONE_latest.kmz"),
        ]:
            url = ft.get(url_key, fallback) if key == "kmz_track" else tc.get(url_key, fallback)
            try:
                dest = storm_dir / f"{storm_id}_{kmz_key}.kmz"
                _download_file(url, dest)
                result[key] = str(dest)
            except Exception:
                pass

        # Parse track and cone from KMZ (already downloaded above)
        kmz_track = storm_dir / f"{storm_id}_TRACK.kmz"
        kmz_cone = storm_dir / f"{storm_id}_CONE.kmz"
        if kmz_track.exists():
            _log_line(agency="nhc", message=f"  Parsing track KMZ...")
            result["track_points"] = NHCDownloader._parse_kmz_track_points(kmz_track)
        if kmz_cone.exists():
            _log_line(agency="nhc", message=f"  Parsing cone KMZ...")
            result["cone_polygons"] = NHCDownloader._parse_kmz_cone_polygon(kmz_cone)

        # Wind radii KMZs
        for radii_key, zdict, out_key_prefix, out_dest_key, kmz_key in [
            ("initial", iwe, "wind_radii_initial", "kmz_wind_initial", "initialradii"),
            ("forecast", fwr, "wind_radii_forecast", "kmz_wind_forecast", "forecastradii"),
        ]:
            kmz_u = zdict.get("kmzFile", f"https://www.nhc.noaa.gov/storm_graphics/api/{storm_id.upper()}_{kmz_key}_latest.kmz")
            try:
                dest = storm_dir / f"{storm_id}_{kmz_key}.kmz"
                _download_file(kmz_u, dest)
                result[out_dest_key] = str(dest)
            except Exception:
                pass
            # Parse wind radii from downloaded KMZ
            kmz_path = storm_dir / f"{storm_id}_{kmz_key}.kmz"
            if kmz_path.exists():
                _log_line(agency="nhc", message=f"  Parsing {radii_key} wind radii KMZ...")
                result[out_key_prefix] = NHCDownloader._parse_kmz_wind_radii(kmz_path)

        # Best track KMZ
        bt_kmz = btg.get("kmzFile", f"https://www.nhc.noaa.gov/gis/best_track/{storm_id}_best_track.kmz")
        try:
            dest = storm_dir / f"{storm_id}_best_track.kmz"
            _download_file(bt_kmz, dest)
            result["best_track_kmz"] = str(dest)
        except Exception:
            pass
        bt_kmz_path = storm_dir / f"{storm_id}_best_track.kmz"
        if bt_kmz_path.exists():
            _log_line(agency="nhc", message=f"  Parsing best track KMZ...")
            bt_pts, bt_line = NHCDownloader._parse_kmz_best_track(bt_kmz_path)
            if bt_pts:
                result["best_track_points"] = bt_pts
            if bt_line:
                result["best_track_line"] = bt_line

        # Arrival time KMZs
        for arr_key, arr_dict, suffix in [
            ("kmz_arrival_earliest", eat, "earliest_reasonable_toa_34"),
            ("kmz_arrival_likely", mlt, "most_likely_toa_34"),
        ]:
            url = arr_dict.get("kmzFile", f"https://www.nhc.noaa.gov/storm_graphics/api/{storm_id.upper()}_{suffix}.kmz")
            try:
                dest = storm_dir / f"{storm_id}_{suffix}.kmz"
                _download_file(url, dest)
                result[arr_key] = str(dest)
            except Exception:
                pass

        # Wind speed probabilities
        for prob_key, prob_field, prob_kt in [
            ("prob_polygons_34", "kmzFile34kt", 34),
            ("prob_polygons_50", "kmzFile50kt", 50),
            ("prob_polygons_64", "kmzFile64kt", 64),
        ]:
            url = wsp.get(prob_field)
            if url:
                try:
                    dest = storm_dir / f"{storm_id}_prob{prob_kt}kt.kmz"
                    _download_file(url, dest)
                    result[prob_key] = str(dest)
                except Exception:
                    pass

        # Save per-storm meta
        meta_path = storm_dir / f"{storm_id}_meta.json"
        try:
            meta = {k: v for k, v in result.items() if k not in ('cone_polygons',)}
            with open(meta_path, 'w') as f:
                json.dump(meta, f, indent=2, default=str)
        except Exception:
            pass

        _log_line(type="storm", agency="nhc", data=result)
        _log_line(agency="nhc", message=f"  Done with {storm_name}")

    _log_line(type="finished", agency="nhc")

# --- JMA ---
from src.clients.jma import fetch_target_tc, fetch_past_tracks, fetch_specifications, fetch_forecast, get_jma_data_dir
from collections import OrderedDict

def run_jma():
    _log_line(agency="jma", message="Fetching JMA typhoon list...")
    target_list = fetch_target_tc()
    if not target_list:
        _log_line(type="error", agency="jma", message="No typhoon data from JMA.")
        _log_line(type="finished", agency="jma")
        return

    past_tracks = None
    try:
        _log_line(agency="jma", message="Fetching past tracks...")
        past_tracks = fetch_past_tracks()
    except Exception as e:
        _log_line(agency="jma", message=f"Past tracks unavailable: {e}")

    past_lookup = {}
    if isinstance(past_tracks, list):
        for pt in past_tracks:
            tc = pt.get("tropicalCyclone", "")
            if tc:
                past_lookup[tc] = pt

    jma_dir = get_jma_data_dir()

    for storm in target_list:
        tropical_cyclone = storm.get("tropicalCyclone", "")
        typhoon_number = storm.get("typhoonNumber", "")
        jma_category = storm.get("category", "")
        tc_id = tropical_cyclone
        if not typhoon_number or not typhoon_number.isdigit():
            storm_id = tropical_cyclone
            storm_name = tropical_cyclone
        else:
            storm_id = typhoon_number
            storm_name = tc_id or f"TC{storm_id}"

        _log_line(agency="jma", message=f"Processing {storm_name}...")

        storm_dir = jma_dir / storm_id
        storm_dir.mkdir(exist_ok=True)

        # Track history
        track_history = []
        track1 = storm.get("track1", {})
        for pt in track1.get("preTyphoon", []):
            if len(pt) >= 2:
                track_history.append([float(pt[0]), float(pt[1])])
        for pt in track1.get("typhoon", []):
            if len(pt) >= 2:
                track_history.append([float(pt[0]), float(pt[1])])

        if tc_id in past_lookup and not track_history:
            pt_data = past_lookup[tc_id]
            for key in ("preTyphoon", "typhoon"):
                for coord in pt_data.get("track1", {}).get(key, []):
                    if len(coord) >= 2:
                        track_history.append([float(coord[0]), float(coord[1])])

        result = {
            "storm_id": storm_id, "tc_id": tc_id,
            "storm_name": storm_name, "typhoon_number": typhoon_number,
            "jma_category": jma_category, "classification": "Typhoon",
            "basin": "Western Pacific", "local_dir": str(storm_dir),
            "track_history": track_history, "track_points": [],
            "probability_circles": [], "storm_warning_areas": [],
        }

        # Specifications and forecast
        spec_data = None
        forecast = None
        try:
            spec_data = fetch_specifications(tc_id)
        except Exception as e:
            _log_line(agency="jma", message=f"  Specifications fetch failed: {e}")

        storm_name_en = ""
        if isinstance(spec_data, list) and spec_data:
            title_entry = spec_data[0]
            if isinstance(title_entry, dict) and title_entry.get("part") == "title":
                nd = title_entry.get("name", {})
                storm_name_en = nd.get("en", "") or nd.get("jp", "")
        if storm_name_en:
            result["storm_name"] = storm_name_en

        try:
            forecast = fetch_forecast(tc_id)
            result["jma_raw"] = forecast
        except Exception as e:
            _log_line(agency="jma", message=f"  Forecast fetch failed: {e}")

        spec_lookup = {}
        if isinstance(spec_data, list):
            for sp in spec_data:
                if not isinstance(sp, dict):
                    continue
                if sp.get("part") == "title" or not isinstance(sp.get("part"), dict):
                    continue
                ah = sp.get("advancedHours", 0)
                spec_lookup[ah] = sp

        if forecast:
            for entry in forecast:
                if not isinstance(entry, dict):
                    continue
                if entry.get("part") == "title":
                    continue
                center = entry.get("center")
                if center and len(center) >= 2:
                    lat, lon = float(center[0]), float(center[1])
                    ah = entry.get("advancedHours", 0)
                    vt = entry.get("validtime", {})
                    dt_utc = vt.get("UTC", "") if isinstance(vt, dict) else ""

                    sp = spec_lookup.get(ah, {})
                    cat_en = None; wind_kt = None; gust_kt = None
                    pressure_val = None; location = None; course = None
                    speed_kt = None; prob_circle_km = None; storm_warning_km = None
                    scale = None; intensity_label = None

                    if sp:
                        cat = sp.get("category", {})
                        cat_en = cat.get("en") if isinstance(cat, dict) else None
                        mw = sp.get("maximumWind", {})
                        if isinstance(mw, dict):
                            sus = mw.get("sustained", {})
                            wind_kt = int(sus["kt"]) if isinstance(sus, dict) and sus.get("kt", "").lstrip("-").isdigit() else None
                            gust = mw.get("gust", {})
                            gust_kt = int(gust["kt"]) if isinstance(gust, dict) and gust.get("kt", "").lstrip("-").isdigit() else None
                            if wind_kt == 0 and sus.get("kt") == "-":
                                wind_kt = None
                        pressure_val = sp.get("pressure")
                        loc = sp.get("location", "")
                        location = loc if loc != "-" else None
                        course = sp.get("course", "").replace("-", "") or None
                        spd = sp.get("speed", {})
                        if isinstance(spd, dict):
                            sk = spd.get("kt", "")
                            speed_kt = int(sk) if sk and sk != "-" and sk.lstrip("-").isdigit() else None
                        pcr = sp.get("probabilityCircleRadius", {})
                        if isinstance(pcr, dict):
                            km = pcr.get("km")
                            prob_circle_km = int(km) if km else None
                        sw = sp.get("stormWarning", [])
                        if sw and isinstance(sw, list) and len(sw) > 0:
                            rng = sw[0].get("range", {})
                            rkm = rng.get("km")
                            storm_warning_km = int(rkm) if rkm else None
                        sc = sp.get("scale", "")
                        scale = sc if sc and sc != "-" else None
                        il = sp.get("intensity", "")
                        intensity_label = il if il and il != "-" else None

                    ic = cat_en or (jma_category if ah == 0 else None)
                    intensity_kt = wind_kt

                    pt = OrderedDict([
                        ("lat", lat), ("lon", lon),
                        ("datetime", dt_utc), ("advanced_hours", ah),
                        ("intensity", intensity_kt),
                        ("intensity_category", ic),
                        ("pressure", pressure_val),
                        ("wind_gust_kt", gust_kt),
                        ("location", location), ("course", course),
                        ("speed_kt", speed_kt), ("scale", scale),
                        ("intensity_label", intensity_label),
                        ("prob_circle_km", prob_circle_km),
                        ("storm_warning_km", storm_warning_km),
                    ])
                    result["track_points"].append(pt)

                prob = entry.get("probabilityCircle")
                if prob:
                    result["probability_circles"].append({
                        "radius": prob.get("radius", 0),
                        "tangent": prob.get("tangent", []),
                        "center": center,
                    })
                swa = entry.get("stormWarningArea")
                if swa:
                    result["storm_warning_areas"].append({
                        "arc": swa.get("arc", []),
                        "line": swa.get("line", []),
                    })

        # Save meta
        meta_path = storm_dir / f"{storm_id}_meta.json"
        try:
            with open(meta_path, 'w') as f:
                json.dump(result, f, indent=2, default=str)
        except Exception:
            pass

        _log_line(type="storm", agency="jma", data=result)
        _log_line(agency="jma", message=f"  Done with {storm_name}")

    _log_line(type="finished", agency="jma")

# --- JTWC ---
from src.clients.jtwc import fetch_active_storm_ids, probe_storm_ids, download_storm_kmz, get_jtwc_data_dir

def _probe_extra_jtwc_ids(ids):
    """Probe for EP storm IDs using NHC active-storm data."""
    nhc_ep_candidates = []
    try:
        from src.clients.nhc import fetch_active_storms as _nhc_fetch
        for s in _nhc_fetch():
            sid = s.get("id", "").lower()
            if sid.startswith("ep") and len(sid) >= 6:
                nhc_ep_candidates.append(f"ep{sid[2:4]}{sid[-2:]}")
    except Exception:
        pass
    if nhc_ep_candidates:
        discovered = probe_storm_ids(nhc_ep_candidates)
        for dsid in discovered:
            if dsid not in ids:
                ids.append(dsid)

def run_jtwc():
    _log_line(agency="jtwc", message="Fetching active JTWC storm IDs from area bulletins...")
    ids = fetch_active_storm_ids()

    # Probe for EP storms via NHC cross-ref (EP bulletin is often blocked)
    _probe_extra_jtwc_ids(ids)

    # Also accept --jtwc-extra IDs from caller (when available from UI state)
    extra_jtwc_ids = []
    collect = False
    for arg in sys.argv:
        if arg == "--jtwc-extra":
            collect = True
        elif collect and not arg.startswith("-"):
            extra_jtwc_ids.append(arg)
        elif collect and arg.startswith("-"):
            collect = False
    if extra_jtwc_ids:
        discovered = probe_storm_ids(extra_jtwc_ids)
        for dsid in discovered:
            if dsid not in ids:
                ids.append(dsid)
    if not ids:
        _log_line(type="error", agency="jtwc", message="No active JTWC storms found.")
        _log_line(type="finished", agency="jtwc")
        return

    _log_line(agency="jtwc", message=f"Found {len(ids)} active JTWC storm(s): {', '.join(ids)}")
    jtwc_dir = get_jtwc_data_dir()

    for storm_id in ids:
        _log_line(agency="jtwc", message=f"Downloading JTWC {storm_id} KMZ...")
        result = download_storm_kmz(storm_id, jtwc_dir)

        if result.get("success"):
            n = len(result.get("track_points", []))
            _log_line(agency="jtwc", message=f"  Parsed {n} forecast points for {storm_id}")
        else:
            _log_line(agency="jtwc", message=f"  KMZ unavailable for {storm_id}")

        # Save meta JSON for loading from disk (only on success)
        if result.get("success"):
            storm_dir = jtwc_dir / storm_id
            storm_dir.mkdir(exist_ok=True)
            meta_path = storm_dir / f"{storm_id}_meta.json"
            try:
                with open(meta_path, 'w') as f:
                    json.dump(result, f, indent=2, default=str)
            except Exception:
                pass

        _log_line(type="storm", agency="jtwc", data=result)
        _log_line(agency="jtwc", message=f"  Done with {storm_id}")

_log_line(type="finished", agency="jtwc")

# --- PAGASA ---
from src.clients.pagasa import fetch_cyclone_data, parse_cyclone_text, parse_storm_name, get_pagasa_data_dir

PAGASA_CATEGORY_MAP = {
    "TD": "Tropical Depression", "TS": "Tropical Storm", "STS": "Severe Tropical Storm",
    "TY": "Typhoon", "STY": "Super Typhoon", "LPA": "Low Pressure Area",
}

def run_pagasa():
    _log_line(agency="pagasa", message="Fetching PAGASA cyclone data...")
    raw_text = fetch_cyclone_data()
    raw = parse_cyclone_text(raw_text)
    if not raw:
        _log_line(type="error", agency="pagasa", message="No cyclone data from PAGASA.")
        _log_line(type="finished", agency="pagasa")
        return

    pagasa_dir = get_pagasa_data_dir()

    for storm in raw:
        raw_name = storm.get("cyclone_name", "")
        local_name, intl_name = parse_storm_name(raw_name)
        storm_id = local_name.lower().replace(" ", "_") or intl_name.lower().replace(" ", "_")
        if not storm_id:
            continue

        storm_name = local_name if local_name else intl_name
        info = storm.get("info", {})
        if not info:
            continue

        _log_line(agency="pagasa", message=f"Processing {storm_name}...")

        storm_dir = pagasa_dir / storm_id
        storm_dir.mkdir(exist_ok=True)

        track_points = []
        latest_type = ""
        for dt_key in sorted(info.keys()):
            entry = info[dt_key]
            cyclone_type = entry.get("cyclone_type", "")
            if cyclone_type:
                latest_type = cyclone_type
            lat = float(entry.get("latitude", 0))
            lon = float(entry.get("longitude", 0))
            radius = entry.get("radius", "0")
            try:
                radius_km = int(radius) if radius else 0
            except (ValueError, TypeError):
                radius_km = 0
            dt_str = entry.get("date", "") + " " + entry.get("time", "")
            cat_full = PAGASA_CATEGORY_MAP.get(cyclone_type, cyclone_type)

            pt = OrderedDict([
                ("lat", lat), ("lon", lon),
                ("datetime", dt_str.strip()),
                ("intensity", None),
                ("intensity_category", cat_full),
                ("cyclone_type", cyclone_type),
                ("radius_km", radius_km),
            ])
            track_points.append(pt)

        result = {
            "storm_id": storm_id, "storm_name": storm_name,
            "local_name": local_name, "intl_name": intl_name,
            "raw_name": raw_name,
            "classification": PAGASA_CATEGORY_MAP.get(latest_type, latest_type),
            "basin": "Western Pacific", "local_dir": str(storm_dir),
            "track_points": track_points,
        }

        meta_path = storm_dir / f"{storm_id}_meta.json"
        try:
            with open(meta_path, 'w') as f:
                json.dump(result, f, indent=2, default=str)
        except Exception:
            pass

        _log_line(type="storm", agency="pagasa", data=result)
        _log_line(agency="pagasa", message=f"  Done with {storm_name}")

    _log_line(type="finished", agency="pagasa")

# --- CWA ---
from src.clients.cwa import CWADownloader, fetch_typhoon_tracks, get_cwa_data_dir, CWAParser, extract_tcs
from datetime import datetime

def run_cwa():
    _log_line(agency="cwa", message="Fetching CWA typhoon tracks...")
    from src.ui.settings import SettingsManager
    _top_dir_cwa = Path(__file__).resolve().parent.parent.parent
    _src_dir_cwa = str((_top_dir_cwa / "src").resolve())
    sm = SettingsManager(_src_dir_cwa)
    auth_code = sm.get("cwa_api_key", "")
    if not auth_code:
        _log_line(type="error", agency="cwa", message="CWA API authorization code not configured.")
        _log_line(type="finished", agency="cwa")
        return

    raw = fetch_typhoon_tracks(auth_code)
    if not raw:
        _log_line(type="error", agency="cwa", message="CWA API returned empty response.")
        _log_line(type="finished", agency="cwa")
        return

    if "cwaopendata" not in raw and raw.get("success") != "true":
        _log_line(type="error", agency="cwa", message="CWA API returned unsuccessful response.")
        _log_line(type="finished", agency="cwa")
        return

    # Parse using CWAParser (same logic as drag/drop)
    entries = CWAParser._build_entries_from_raw(raw)
    if not entries:
        _log_line(type="error", agency="cwa", message="No CWA typhoon data found.")
        _log_line(type="finished", agency="cwa")
        return

    cwa_dir = get_cwa_data_dir()

    for entry in entries:
        td_no = entry["id"][4:]  # strip "cwa_" prefix
        _log_line(agency="cwa", message=f"Processing {entry['name']}...")

        storm_dir = cwa_dir / td_no
        storm_dir.mkdir(exist_ok=True)

        # Find the raw TC from the response to save original structure
        raw_tc = None
        for tc in extract_tcs(raw):
            if str(tc.get("CwaTdNo", "")) == td_no:
                raw_tc = tc
                break

        # Save raw CWA file in original cwaopendata format
        if raw_tc:
            cwa_output = {
                "cwaopendata": {
                    "Identifier": f"CWA-TropicalCyclone_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    "Sender": "fss@wfc.cwa.gov.tw",
                    "Sent": datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00'),
                    "Status": "Actual",
                    "MsgType": "Update",
                    "Scope": "Public",
                    "Dataset": {
                        "TropicalCyclones": {
                            "TropicalCyclone": raw_tc
                        }
                    }
                }
            }
            cwa_path = storm_dir / f"{td_no}_cwa.json"
            try:
                with open(cwa_path, 'w', encoding='utf-8') as f:
                    json.dump(cwa_output, f, indent=2, ensure_ascii=False, default=str)
            except Exception:
                pass

        _log_line(type="storm", agency="cwa", data={
            "storm_id": td_no,
            "storm_name": entry["name"],
            "classification": entry.get("type", "Tropical Depression"),
            "track_points": entry["points"],
            "track_history": [[p["lat"], p["lon"]] for p in entry["points"] if not p.get("is_forecast")],
        })
        _log_line(agency="cwa", message=f"  Done with {entry['name']}")

    _log_line(type="finished", agency="cwa")

# --- Main ---
if __name__ == "__main__":
    agencies = ["nhc", "jma", "jtwc", "pagasa", "cwa"]

    # Check if specific agency was requested (--agency flag)
    for i, arg in enumerate(sys.argv):
        if arg == "--agency" and i + 1 < len(sys.argv):
            requested = sys.argv[i + 1]
            if requested in agencies:
                agencies = [requested]
            break

    for agency in agencies:
        _log_line(type="start", agency=agency, message=f"Starting {agency.upper()} download...")
        try:
            if agency == "nhc":
                run_nhc()
            elif agency == "jma":
                run_jma()
            elif agency == "jtwc":
                run_jtwc()
            elif agency == "pagasa":
                run_pagasa()
            elif agency == "cwa":
                run_cwa()
        except Exception as e:
            _log_line(type="error", agency=agency, message=str(e))
            _log_line(type="finished", agency=agency)

    _log_line(type="all_done")
