import os
import json
import zipfile
import xml.etree.ElementTree as ET
import re
import logging
import traceback
from pathlib import Path
from datetime import datetime, timedelta
from collections import OrderedDict

import requests
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

CWA_NS = "urn:cwa:gov:tw:cwacommon:0.1"
CWA_BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005"

CWA_CATEGORY_THRESHOLDS = [
    (67, "Super Typhoon"),
    (33, "Typhoon"),
    (25, "Severe Tropical Storm"),
    (17, "Tropical Storm"),
    (0,  "Tropical Depression"),
]

def _mps_to_kt(mps):
    if mps is None:
        return None
    try:
        return round(float(mps) * 1.944, 1)
    except (ValueError, TypeError):
        return None

def _kmh_to_kt(kmh):
    if kmh is None:
        return None
    try:
        return round(float(kmh) * 0.54, 1)
    except (ValueError, TypeError):
        return None

def _wind_to_category(mps):
    if mps is None:
        return None
    try:
        mps_f = float(mps)
    except (ValueError, TypeError):
        return None
    for threshold, cat in CWA_CATEGORY_THRESHOLDS:
        if mps_f >= threshold:
            return cat
    return None

def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def _to_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None

def _parse_cwa_datetime(dt_str):
    if not dt_str:
        return ""
    dt_str = dt_str.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(dt_str.replace("+08:00", "").replace("Z", ""), fmt)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            continue
    return dt_str

def _ns_tag(ns, tag):
    if not ns:
        return tag
    prefix = ""
    rest = tag
    if rest.startswith(".//"):
        prefix = ".//"
        rest = rest[3:]
    elif rest.startswith("//"):
        prefix = "//"
        rest = rest[2:]
    elif rest.startswith("./"):
        prefix = "./"
        rest = rest[2:]
    segments = rest.split("/")
    ns_segments = [f"{{{ns}}}{s}" for s in segments]
    return prefix + "/".join(ns_segments)

def _find_text(parent, tag, ns=""):
    el = parent.find(_ns_tag(ns, tag))
    if el is not None and el.text:
        return el.text.strip()
    return None

def _find_int(parent, tag, ns=""):
    v = _find_text(parent, tag, ns)
    if v is not None:
        try:
            return int(v)
        except (ValueError, TypeError):
            pass
    return None

def _find_float(parent, tag, ns=""):
    v = _find_text(parent, tag, ns)
    if v is not None:
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
    return None

def _find_text_by_lang(parent, tag, ns="", lang="en-us"):
    for el in parent.findall(_ns_tag(ns, tag)):
        lang_attr = el.get("lang", "")
        if lang_attr == lang:
            return el.text.strip() if el.text else None
    first = parent.find(_ns_tag(ns, tag))
    if first is not None and first.text:
        return first.text.strip()
    return None

def _ensure_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]

def get_cwa_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "cwa_data"
    d.mkdir(parents=True, exist_ok=True)
    return d

def fetch_typhoon_tracks(auth_code, **params):
    params["Authorization"] = auth_code
    params.setdefault("format", "JSON")
    r = requests.get(CWA_BASE_URL, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def extract_tcs(raw):
    records = raw.get("records") or {}
    tcs_container = None
    if isinstance(records, dict):
        tcs_container = records.get("TropicalCyclones")
    if tcs_container is None:
        cwaopendata = raw.get("cwaopendata") or {}
        dataset = cwaopendata.get("Dataset") or {}
        tcs_container = dataset.get("TropicalCyclones")
    if tcs_container is None and isinstance(records, dict):
        dataset = records.get("dataset") or {}
        if isinstance(dataset, dict):
            return _ensure_list(dataset.get("AnalysisData") or []), _ensure_list(dataset.get("ForecastData") or [])
    if tcs_container is None:
        return [], []
    return _ensure_list(tcs_container.get("TropicalCyclone") or [])


class CWAParser:

    @staticmethod
    def detect_format(path):
        path = Path(path)
        if not path.exists():
            return None
        data = path.read_bytes()[:4096]
        raw = data.decode("utf-8", errors="ignore").strip()
        if raw.startswith("<?xml") or raw.startswith("<"):
            if "cwaopendata" in raw or "CWA-TropicalCyclone" in raw:
                return "cwa_xml"
            return "kml"
        if raw.startswith("{"):
            if '"cwaopendata"' in raw or '"CwaTdNo"' in raw:
                return "cwa_json"
            if '"records"' in raw or '"success"' in raw:
                return "cwa_json"
            return "json_unknown"
        return None

    @classmethod
    def parse(cls, path):
        fmt = cls.detect_format(path)
        if fmt == "cwa_xml":
            return cls.parse_xml(path)
        elif fmt == "cwa_json":
            return cls.parse_json(path)
        return None

    @classmethod
    def parse_xml(cls, path):
        tree = ET.parse(str(path))
        root = tree.getroot()
        tag = root.tag
        ns = CWA_NS if CWA_NS in tag else ""
        tc = root.find(_ns_tag(ns, ".//TropicalCyclone"))
        if tc is None:
            return None
        year = _find_text(tc, "Year", ns)
        cwa_td_no = _find_text(tc, "CwaTdNo", ns)
        storm_id = cwa_td_no or "unknown"
        storm_name = f"CWA TC-{storm_id}" if cwa_td_no else "CWA Tropical Cyclone"
        points = []
        analysis = tc.find(_ns_tag(ns, ".//AnalysisData/Fix"))
        if analysis is not None:
            pt = cls._parse_fix_element(analysis, ns, is_analysis=True)
            if pt:
                points.append(pt)
        forecast_container = tc.find(_ns_tag(ns, ".//ForecastData"))
        if forecast_container is not None:
            for fix_el in forecast_container.findall(_ns_tag(ns, "Fix")):
                pt = cls._parse_fix_element(fix_el, ns, is_analysis=False)
                if pt:
                    points.append(pt)
        points.sort(key=lambda p: p.get("advanced_hours", 0))
        return cls._build_track_entry(storm_id, storm_name, year, points)

    @classmethod
    def parse_json(cls, path):
        with open(str(path), "r", encoding="utf-8") as f:
            data = json.load(f)
        cwa = data.get("cwaopendata") or data
        dataset = cwa.get("Dataset") or {}
        tcs = dataset.get("TropicalCyclones") or {}
        tc = tcs.get("TropicalCyclone") or tcs
        if isinstance(tc, list):
            tc = tc[0] if tc else {}
        if not tc:
            records = data.get("records") or {}
            tc = records
        if not tc.get("CwaTdNo"):
            tcs_list = extract_tcs(data)
            if tcs_list and isinstance(tcs_list, list):
                tc = tcs_list[0]
            else:
                return None
        year = tc.get("Year", "")
        cwa_td_no = str(tc.get("CwaTdNo") or "")
        storm_id = cwa_td_no or "unknown"
        storm_name = f"CWA TC-{storm_id}" if cwa_td_no else "CWA Tropical Cyclone"
        points = []
        analysis_raw = _ensure_list(tc.get("AnalysisData", {}).get("Fix", []))
        if analysis_raw:
            pt = cls._parse_fix_json(analysis_raw[-1], is_analysis=True)
            if pt:
                points.append(pt)
        forecast_raw = tc.get("ForecastData", {}).get("Fix", [])
        for fix_item in _ensure_list(forecast_raw):
            pt = cls._parse_fix_json(fix_item, is_analysis=False)
            if pt:
                points.append(pt)
        points.sort(key=lambda p: p.get("advanced_hours", 0))
        return cls._build_track_entry(storm_id, storm_name, year, points)

    @staticmethod
    def _parse_fix_element(el, ns, is_analysis=False):
        dt_str = _find_text(el, "DateTime" if is_analysis else "InitialTime", ns)
        lon = _find_float(el, "CoordinateLongitude", ns)
        lat = _find_float(el, "CoordinateLatitude", ns)
        max_wind = _find_int(el, "MaxWindSpeed", ns)
        gust = _find_int(el, "MaxGustSpeed", ns)
        pressure = _find_int(el, "Pressure", ns)
        speed = _find_float(el, "MovingSpeed", ns)
        direction = _find_text(el, "MovingDirection", ns)
        prediction = _find_text_by_lang(el, "MovingPrediction", ns, "en-us")
        forecast_hour = 0 if is_analysis else (_find_int(el, "ForecastHour", ns) or 0)
        c15_el = el.find(_ns_tag(ns, "Circle15ms"))
        circle_15 = _find_float(c15_el, "Radius", ns) if c15_el is not None else None
        c25_el = el.find(_ns_tag(ns, "Circle25ms"))
        circle_25 = _find_float(c25_el, "Radius", ns) if c25_el is not None else None
        prob_radius = _find_int(el, "Radius70PercentProbability", ns)
        pt = OrderedDict()
        pt["lon"] = lon if lon is not None else 0.0
        pt["lat"] = lat if lat is not None else 0.0
        pt["datetime"] = _parse_cwa_datetime(dt_str)
        pt["intensity"] = _mps_to_kt(max_wind)
        pt["wind_gust_kt"] = _mps_to_kt(gust)
        pt["pressure"] = pressure
        pt["course"] = direction or ""
        pt["speed_kt"] = _kmh_to_kt(speed)
        pt["intensity_category"] = _wind_to_category(max_wind)
        pt["advanced_hours"] = forecast_hour
        pt["moving_prediction"] = prediction or ""
        pt["circle_15ms_km"] = circle_15
        pt["circle_25ms_km"] = circle_25
        pt["prob_circle_km"] = prob_radius
        pt["is_forecast"] = not is_analysis
        return pt

    @staticmethod
    def _parse_fix_json(fix_item, is_analysis=False):
        if not isinstance(fix_item, dict):
            return None
        dt_key = "DateTime" if is_analysis else "InitialTime"
        dt_str = fix_item.get(dt_key, "")
        lon = fix_item.get("CoordinateLongitude")
        lat = fix_item.get("CoordinateLatitude")
        max_wind = fix_item.get("MaxWindSpeed")
        gust = fix_item.get("MaxGustSpeed")
        pressure = fix_item.get("Pressure")
        speed = fix_item.get("MovingSpeed")
        direction = fix_item.get("MovingDirection", "")
        predictions = fix_item.get("MovingPrediction", [])
        prediction = ""
        if isinstance(predictions, list):
            for p in predictions:
                if isinstance(p, dict):
                    lang = p.get("lang") or p.get("@lang", "")
                    text = p.get("value") or p.get("#text", "")
                    if lang == "en-us":
                        prediction = text
                        break
            if not prediction:
                for p in predictions:
                    if isinstance(p, dict):
                        prediction = p.get("value") or p.get("#text", "")
                        break
        elif isinstance(predictions, dict):
            prediction = predictions.get("value") or predictions.get("#text", "")
        forecast_hour = 0 if is_analysis else _to_int(fix_item.get("ForecastHour")) or 0
        c15 = fix_item.get("Circle15ms")
        circle_15 = _to_float(c15.get("Radius")) if isinstance(c15, dict) else None
        c25 = fix_item.get("Circle25ms")
        circle_25 = _to_float(c25.get("Radius")) if isinstance(c25, dict) else None
        prob_radius = _to_int(fix_item.get("Radius70PercentProbability"))
        pt = OrderedDict()
        try:
            pt["lon"] = float(lon) if lon is not None else 0.0
        except (ValueError, TypeError):
            pt["lon"] = 0.0
        try:
            pt["lat"] = float(lat) if lat is not None else 0.0
        except (ValueError, TypeError):
            pt["lat"] = 0.0
        pt["datetime"] = _parse_cwa_datetime(str(dt_str)) if dt_str else ""
        pt["intensity"] = _mps_to_kt(_to_float(max_wind))
        pt["wind_gust_kt"] = _mps_to_kt(_to_float(gust))
        pt["pressure"] = _to_int(pressure)
        pt["course"] = str(direction) if direction else ""
        pt["speed_kt"] = _kmh_to_kt(_to_float(speed))
        pt["intensity_category"] = _wind_to_category(_to_float(max_wind))
        pt["advanced_hours"] = forecast_hour if forecast_hour is not None else 0
        pt["moving_prediction"] = prediction or ""
        pt["circle_15ms_km"] = circle_15
        pt["circle_25ms_km"] = circle_25
        pt["prob_circle_km"] = prob_radius
        pt["is_forecast"] = not is_analysis
        return pt

    @staticmethod
    def _build_track_entry(storm_id, storm_name, year, points):
        if not points:
            return None
        try:
            _year = int(year)
        except (ValueError, TypeError):
            _year = 2026
        track_id = f"cwa_{storm_id}"
        max_intensity = max((p.get("intensity") or 0) for p in points)
        if max_intensity >= 64:
            storm_type = "Typhoon"
        elif max_intensity >= 34:
            storm_type = "Tropical Storm"
        else:
            storm_type = "Tropical Depression"
        entry = {
            "id": track_id,
            "name": f"{storm_name} ({_year})",
            "agency": "cwa",
            "type": storm_type,
            "year": _year,
            "basin": "Western Pacific",
            "notes": f"CWA forecast from cwa.gov.tw (TC-{storm_id})",
            "color": "#00BCD4",
            "visible": True,
            "add_to_infobox": False,
            "display_options": {
                "show_track_line": True,
                "show_points": True,
                "show_cone": True,
                "cone_mode": "cwa_standard",
                "show_wind_radii": False,
                "show_best_track": False,
                "show_labels": True,
                "show_label_name": True,
                "show_label_time": True,
                "show_label_speed": False,
                "show_label_category": True,
                "show_hist_path": False,
                "show_swa": False,
                "show_prob_circle": True,
            },
            "points": [{
                "lon": p["lon"],
                "lat": p["lat"],
                "datetime": p.get("datetime", ""),
                "intensity": p.get("intensity"),
                "intensity_category": p.get("intensity_category"),
                "pressure": p.get("pressure"),
                "wind_gust_kt": p.get("wind_gust_kt"),
                "course": p.get("course", ""),
                "speed_kt": p.get("speed_kt"),
                "advanced_hours": p.get("advanced_hours", 0),
                "moving_prediction": p.get("moving_prediction", ""),
                "circle_15ms_km": p.get("circle_15ms_km"),
                "circle_25ms_km": p.get("circle_25ms_km"),
                "prob_circle_km": p.get("prob_circle_km"),
                "is_forecast": p.get("is_forecast", False),
            } for p in points],
        }
        return entry

    @classmethod
    def _build_entries_from_raw(cls, raw):
        entries = []
        tc_list = extract_tcs(raw)
        if not tc_list or not isinstance(tc_list, list):
            return entries
        for tc in tc_list:
            if not isinstance(tc, dict):
                continue
            cwa_td_no = tc.get("CwaTdNo") or ""
            if not cwa_td_no:
                continue
            storm_id = str(cwa_td_no)
            year = tc.get("Year", 2026)
            storm_name = "CWA TC-%s" % storm_id
            points = []
            analysis_raw = _ensure_list(tc.get("AnalysisData", {}).get("Fix", []))
            if analysis_raw:
                pt = cls._parse_fix_json(analysis_raw[-1], is_analysis=True)
                if pt:
                    points.append(pt)
            forecast_raw = tc.get("ForecastData", {}).get("Fix", [])
            for fix_item in _ensure_list(forecast_raw):
                pt = cls._parse_fix_json(fix_item, is_analysis=False)
                if pt:
                    points.append(pt)
            points.sort(key=lambda p: p.get("advanced_hours", 0))
            entry = cls._build_track_entry(storm_id, storm_name, year, points)
            if entry:
                entries.append(entry)
        return entries


class CWADownloader(QObject):
    progress = Signal(str)
    storm_downloaded = Signal(dict)
    finished = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            from ..ui.settings import SettingsManager
            auth_code = ""
            try:
                sm = SettingsManager(Path(__file__).resolve().parent.parent)
                auth_code = sm.get("cwa_api_key", "")
            except Exception:
                pass

            if not auth_code:
                self.error.emit("CWA API authorization code not configured. Set it in Settings > APIs.")
                self.finished.emit()
                return

            self.progress.emit("Fetching CWA typhoon tracks...")
            raw = fetch_typhoon_tracks(auth_code)

            if "cwaopendata" not in raw:
                if raw.get("success") != "true":
                    self.error.emit("CWA API returned unsuccessful response.")
                    self.finished.emit()
                    return

            cwa_dir = get_cwa_data_dir()
            entries = CWAParser._build_entries_from_raw(raw)
            if not entries:
                self.error.emit("No CWA typhoon data found.")
                self.finished.emit()
                return

            for entry in entries:
                if self._cancelled:
                    break

                td_no = entry["id"][4:]
                self.progress.emit("Processing %s..." % entry["name"])

                storm_dir = cwa_dir / td_no
                storm_dir.mkdir(exist_ok=True)

                meta_path = storm_dir / "%s_meta.json" % td_no
                try:
                    meta = {
                        "storm_id": td_no,
                        "storm_name": entry["name"],
                        "classification": entry.get("type", "Tropical Depression"),
                        "basin": "Western Pacific",
                        "local_dir": str(storm_dir),
                        "track_points": entry["points"],
                        "track_history": [[p["lat"], p["lon"]] for p in entry["points"] if not p.get("is_forecast")],
                    }
                    with open(meta_path, 'w') as f:
                        json.dump(meta, f, indent=2, default=str)
                except Exception:
                    pass

                self.storm_downloaded.emit(entry)
                self.progress.emit("  Done with %s" % entry["name"])

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit()
