# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/jtwc.py
# Description: Joint Typhoon Warning Center data client for Western Pacific cyclone information.
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


import re
import time
import zipfile
import io as _io
import datetime as _dt
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from collections import OrderedDict

import requests
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)

BASE_URL = "https://www.metoc.navy.mil/jtwc/products/"

BULLETIN_URLS = {
    "wp": "https://www.metoc.navy.mil/jtwc/products/abpwweb.txt",
    "io": "https://www.metoc.navy.mil/jtwc/products/abioweb.txt",
    "sh": "https://www.metoc.navy.mil/jtwc/products/abshweb.txt",
    "ep": "https://www.metoc.navy.mil/jtwc/products/abepweb.txt",
}
BASIN_SUFFIX = {"wp": ["W"], "io": ["B", "A"], "sh": ["S", "P"], "ep": ["E"]}

JTWC_CATEGORIES = [
    (130, "Super Typhoon"),
    (64,  "Typhoon"),
    (34,  "Tropical Storm"),
    (0,   "Tropical Depression"),
]


def _kt_to_category(kt):
    if kt is None:
        return None
    for threshold, cat in JTWC_CATEGORIES:
        if kt >= threshold:
            return cat
    return None


def get_jtwc_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "jtwc_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- KML/KMZ parsing utilities ----

def _extract_kml_from_kmz(kmz_path):
    """Extract KML text from a KMZ (zip) file."""
    with zipfile.ZipFile(str(kmz_path), 'r') as zf:
        kml_files = [n for n in zf.namelist() if n.lower().endswith(".kml")]
        if not kml_files:
            raise ValueError(f"No KML found in {kmz_path}")
        with zf.open(kml_files[0]) as f:
            return f.read().decode("utf-8", errors="replace")


def _get_kml_ns(root):
    """Extract KML namespace from root element tag."""
    tag = root.tag
    m = re.match(r'\{([^}]+)\}', tag)
    return m.group(1) if m else ""


def _local_tag(element):
    tag = element.tag
    return tag.split("}")[-1] if "}" in tag else tag


def _find_tag(element, tag, ns):
    child = element.find(f"{{{ns}}}{tag}") if ns else element.find(tag)
    if child is None and ns:
        child = element.find(tag)
    return child


def _find_all_placemarks(root, ns):
    if ns:
        for pm in root.iter(f"{{{ns}}}Placemark"):
            yield pm
    else:
        for pm in root.iter("Placemark"):
            yield pm


def _get_placemark_name(pm, ns):
    name_el = _find_tag(pm, "name", ns)
    return name_el.text.strip() if name_el is not None and name_el.text else ""


def _get_placemark_description(pm, ns):
    desc_el = _find_tag(pm, "description", ns)
    if desc_el is not None:
        return desc_el.text or "".join(desc_el.itertext()) or ""
    return ""


def _parse_kml_coords(coord_text):
    """Parse KML coordinate string into [(lon, lat), ...]."""
    pts = []
    for part in coord_text.strip().split():
        part = part.strip()
        if not part:
            continue
        vals = part.split(",")
        if len(vals) >= 2:
            try:
                pts.append((float(vals[0]), float(vals[1])))
            except (ValueError, TypeError):
                continue
    return pts


def _get_coordinates_from_geometry(geom, ns):
    """Extract coordinates from any KML geometry element."""
    coords_el = _find_tag(geom, "coordinates", ns)
    if coords_el is not None and coords_el.text:
        return _parse_kml_coords(coords_el.text)
    obi = _find_tag(geom, "outerBoundaryIs", ns)
    if obi is not None:
        lr = _find_tag(obi, "LinearRing", ns)
        if lr is not None:
            coords_el = _find_tag(lr, "coordinates", ns)
            if coords_el is not None and coords_el.text:
                return _parse_kml_coords(coords_el.text)
    mg = _find_tag(geom, "MultiGeometry", ns)
    if mg is not None:
        for child in list(mg):
            tag = _local_tag(child)
            if tag in ("Point", "LineString", "LinearRing", "Polygon"):
                result = _get_coordinates_from_geometry(child, ns)
                if result:
                    return result
    return []


def _extract_kmz_icons(kmz_path, icons_dir):
    """Extract PNG icon images from a JTWC KMZ file."""
    try:
        icons_dir = Path(icons_dir)
        icons_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(str(kmz_path), 'r') as zf:
            for name in zf.namelist():
                if name.lower().startswith("images/") and name.lower().endswith(".png"):
                    out_path = icons_dir / Path(name).name
                    with zf.open(name) as src, open(out_path, 'wb') as dst:
                        dst.write(src.read())
    except Exception as e:
        log.warning("Failed to extract KMZ icons: %s", e)


def _get_placemark_icon_href(pm, ns):
    """Extract icon href from a Placemark's inline Style."""
    style_el = _find_tag(pm, "Style", ns)
    if style_el is None:
        return ""
    icon_style = _find_tag(style_el, "IconStyle", ns)
    if icon_style is None:
        return ""
    icon_el = _find_tag(icon_style, "Icon", ns)
    if icon_el is None:
        return ""
    href_el = _find_tag(icon_el, "href", ns)
    if href_el is not None and href_el.text:
        return Path(href_el.text.strip()).name
    return ""


def _parse_jtwc_kmz(kmz_path, output_dir=None):
    """Parse JTWC KMZ file into structured data.

    JTWC KMZ structure (observed):
      Document > Folder(name=storm_name) > Folder(name="Forecast")
        Placemarks with Point -- forecast track points
        Placemarks with Polygon -- wind radii (RADIUS OF XX KT WINDS)
        Placemark "34 knot Danger Swath" -- Polygon (forecast cone)
        Placemark "wpXX Storm Track" -- LineString of full track
      Folder(name="Previous Best Track Posits")
        Placemarks with Point -- best track history
        Placemark "Best Track" -- LineString

    Returns
    -------
    dict with keys: storm_name, storm_num, classification, issued_dtg,
        track_points, wind_radii_polygons, danger_swath, track_line,
        best_track_points, best_track_line, icons_dir, success
    """
    result = {
        "storm_name": "",
        "storm_num": "",
        "classification": "",
        "issued_dtg": "",
        "track_points": [],
        "wind_radii_polygons": {"34": [], "50": [], "64": []},
        "danger_swath": [],
        "track_line": [],
        "best_track_points": [],
        "best_track_line": [],
        "icons_dir": "",
        "kmz_cone": "",
        "kmz_track": "",
        "success": True,
    }
    try:
        kml_text = _extract_kml_from_kmz(kmz_path)
        root = ET.fromstring(kml_text)
        ns = _get_kml_ns(root)

        # Find storm name from top-level Folder name
        storm_name = ""
        for folder in root.iter(f"{{{ns}}}Folder" if ns else "Folder"):
            fname = _get_placemark_name(folder, ns)
            if fname and fname not in ("Forecast", "Previous Best Track Posits"):
                storm_name = fname

        result["storm_name"] = storm_name

        # Parse forecast and best track from specific subfolders only
        for folder in root.iter(f"{{{ns}}}Folder" if ns else "Folder"):
            folder_name = _get_placemark_name(folder, ns)
            if folder_name not in ("Forecast", "Previous Best Track Posits"):
                continue

            for pm in _find_all_placemarks(folder, ns):
                name = _get_placemark_name(pm, ns)
                desc = _get_placemark_description(pm, ns)

                # --- Track points (Placemarks with Point) ---
                pt_el = _find_tag(pm, "Point", ns)
                if pt_el is not None:
                    coords = _get_coordinates_from_geometry(pt_el, ns)
                    if not coords:
                        continue
                    lon, lat = coords[0]

                    # Extract intensity from name: "23/18Z - 35 knots" or "(... - 25 knots)"
                    intensity = None
                    m = re.search(r'-\s*(\d+)\s*knots?', name, re.IGNORECASE)
                    if m:
                        intensity = int(m.group(1))
                    elif not folder_name or "Best" not in folder_name:
                        m = re.search(r'(\d+)\s*KTS', desc, re.IGNORECASE)
                        if m:
                            intensity = int(m.group(1))

                    # Extract TAU from description HTML: "TAU X"
                    advanced_hours = 0
                    if desc:
                        m = re.search(r'TAU\s+(\d+)', desc)
                        if m:
                            advanced_hours = int(m.group(1))

                    # Extract time from name: "23/18Z" -> "231800Z"
                    dtg = ""
                    m_dtg = re.match(r'(\d{2})/(\d{2})Z', name)
                    if m_dtg:
                        dtg = m_dtg.group(1) + m_dtg.group(2) + "00Z"
                    elif re.match(r'\d{10}Z', name):  # best track: "26072200Z"
                        dtg = name[:6] + "Z"

                    category = _kt_to_category(intensity)

                    # Parse wind radii from description text
                    pt_radii = _parse_wind_radii(desc)

                    # Extract icon href from inline Style
                    icon_href = _get_placemark_icon_href(pm, ns)

                    pt_dict = OrderedDict([
                        ("lat", lat),
                        ("lon", lon),
                        ("advanced_hours", advanced_hours),
                        ("intensity", intensity),
                        ("intensity_category", category),
                        ("wind_radii", pt_radii),
                        ("icon", icon_href),
                    ])
                    if dtg:
                        pt_dict["dtg"] = dtg

                    if folder_name == "Previous Best Track Posits":
                        result["best_track_points"].append(pt_dict)
                    else:
                        result["track_points"].append(pt_dict)

                        # Set issued_dtg from first forecast point that has a dtg
                        if not result["issued_dtg"] and dtg:
                            result["issued_dtg"] = dtg

                        # Extract storm_num and classification from initial point name
                        if not result["storm_num"]:
                            m = re.search(r'(\d{2})[WSBAPEC]', name)
                            if m:
                                result["storm_num"] = m.group(1)
                        if not result["classification"]:
                            m = re.search(r'(TROPICAL\s+(DEPRESSION|STORM)|TYPHOON|SUPER\s+TYPHOON|HURRICANE)',
                                          name, re.IGNORECASE)
                            if m:
                                result["classification"] = m.group(1).upper()

                # --- Wind radii & danger swath polygons ---
                poly_el = _find_tag(pm, "Polygon", ns)
                if poly_el is not None:
                    coords = _get_coordinates_from_geometry(poly_el, ns)
                    if coords and len(coords) >= 3:
                        name_upper = name.upper()
                        if "DANGER" in name_upper:
                            result["danger_swath"] = coords
                        elif "34" in name_upper:
                            result["wind_radii_polygons"]["34"].append(coords)
                        elif "50" in name_upper:
                            result["wind_radii_polygons"]["50"].append(coords)
                        elif "64" in name_upper:
                            result["wind_radii_polygons"]["64"].append(coords)

                # --- Track line (LineString) ---
                ls_el = _find_tag(pm, "LineString", ns)
                if ls_el is not None and "Storm Track" in name:
                    line_coords = _get_coordinates_from_geometry(ls_el, ns)
                    if line_coords:
                        result["track_line"] = line_coords
                elif ls_el is not None and "Best Track" in name:
                    line_coords = _get_coordinates_from_geometry(ls_el, ns)
                    if line_coords:
                        result["best_track_line"] = line_coords

        result["track_points"].sort(key=lambda p: p.get("advanced_hours", 0))
        result["best_track_points"].sort(key=lambda p: p.get("advanced_hours", 0))

        # Add 'datetime' to each track point in "%Y-%m-%d %H:%M" format
        # (same format NHC uses), derived from issued_dtg + advanced_hours.
        # This is required by consumers that render PIL title boxes and track labels.
        _ref_dtg = result.get("issued_dtg", "")
        if _ref_dtg and _ref_dtg.endswith("Z"):
            try:
                _dd = int(_ref_dtg[0:2]); _hh = int(_ref_dtg[2:4]); _mm = int(_ref_dtg[4:6])
                _now = _dt.datetime.now(_dt.timezone.utc)
                _yr, _mo = _now.year, _now.month
                try:
                    _base_dt = _dt.datetime(_yr, _mo, _dd, _hh, _mm, tzinfo=_dt.timezone.utc)
                except ValueError:
                    if _mo == 1: _base_dt = _dt.datetime(_yr - 1, 12, _dd, _hh, _mm, tzinfo=_dt.timezone.utc)
                    else: _base_dt = _dt.datetime(_yr, _mo - 1, _dd, _hh, _mm, tzinfo=_dt.timezone.utc)
                for _p in result["track_points"]:
                    _ah = _p.get("advanced_hours")
                    if _ah is not None:
                        _p["datetime"] = (_base_dt + _dt.timedelta(hours=_ah)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        # Fallback: use raw dtg string for any point that still lacks datetime
        for _p in result["track_points"]:
            if "datetime" not in _p or not _p.get("datetime"):
                _dtg = _p.get("dtg")
                if _dtg:
                    _p["datetime"] = _dtg

        # kmz_cone is NOT set here because the full KMZ contains best track
        # geometry (LineString + Points) that would be mistakenly rendered as a
        # cone polygon.  The danger_swath polygon coordinates are already
        # extracted above and passed directly to the handler.

        # Extract PNG icons from KMZ if output_dir given
        if output_dir:
            icons_dir = Path(output_dir) / "icons"
            _extract_kmz_icons(kmz_path, icons_dir)
            if icons_dir.exists():
                result["icons_dir"] = str(icons_dir)

    except Exception as e:
        log.error("Failed to parse JTWC KMZ: %s", e)
        result["success"] = False
        result["error"] = str(e)

    if not result["classification"]:
        result["classification"] = "INVEST"
    return result


def download_storm_kmz(storm_id, output_dir=None):
    """Download the JTWC KMZ file for a storm and parse it.

    Parameters
    ----------
    storm_id : str
        e.g. "wp1126"
    output_dir : str or Path, optional
        Base output directory. Defaults to get_jtwc_data_dir().

    Returns
    -------
    dict with parsed KMZ data and file download status
    """
    if output_dir is None:
        output_dir = get_jtwc_data_dir()
    storm_dir = Path(output_dir) / storm_id
    storm_dir.mkdir(parents=True, exist_ok=True)

    url = BASE_URL + f"{storm_id}.kmz"
    dest = storm_dir / f"{storm_id}.kmz"

    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30, stream=True)
            if 400 <= r.status_code < 500 and r.status_code != 429:
                log.warning("Skipped (%d): %s", r.status_code, url)
                return {
                    "storm_id": storm_id,
                    "local_dir": str(storm_dir),
                    "success": False,
                    "error": f"HTTP {r.status_code}",
                    "track_points": [],
                }
            r.raise_for_status()
            with open(dest, 'wb') as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            break
        except requests.RequestException as e:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning("Retry %d/%d for %s in %ds: %s", attempt + 1, 3, url, wait, e)
                time.sleep(wait)
            else:
                log.error("Failed after 3 attempts: %s — %s", url, e)
                return {
                    "storm_id": storm_id,
                    "local_dir": str(storm_dir),
                    "success": False,
                    "error": str(e),
                    "track_points": [],
                }

    parsed = _parse_jtwc_kmz(dest, output_dir=storm_dir)
    parsed["storm_id"] = storm_id
    parsed["local_dir"] = str(storm_dir)
    parsed["file_path"] = str(dest)
    parsed["basin"] = storm_id[:2].upper()
    if "success" not in parsed:
        parsed["success"] = True
    return parsed


def _download_file(url):
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=30, stream=True)
            if 400 <= r.status_code < 500 and r.status_code != 429:
                log.warning("Skipped (%d): %s", r.status_code, url)
                return None
            r.raise_for_status()
            log.debug("_download_file(%s): %d bytes, status=%d", url, len(r.text), r.status_code)
            return r.text
        except requests.RequestException as e:
            if attempt < 2:
                wait = 2 ** attempt
                log.warning("Retry %d/%d for %s in %ds: %s", attempt + 1, 3, url, wait, e)
                time.sleep(wait)
            else:
                log.error("Failed after 3 attempts: %s — %s", url, e)
    return None


def _parse_wind_radii(text):
    """Extract wind radii by threshold from a section of warning text."""
    radii = {}
    for kt in (34, 50, 64):
        radii[str(kt)] = {"ne": None, "se": None, "sw": None, "nw": None}
        m = re.search(
            rf"RADIUS OF {kt:03d} KT WINDS\s*-\s*"
            r"(\d+)\s*NM\s*NORTHEAST\s*QUADRANT\s*"
            r"(\d+)\s*NM\s*SOUTHEAST\s*QUADRANT\s*"
            r"(\d+)\s*NM\s*SOUTHWEST\s*QUADRANT\s*"
            r"(\d+)\s*NM\s*NORTHWEST\s*QUADRANT",
            text, re.IGNORECASE
        )
        if m:
            radii[str(kt)] = {
                "ne": int(m.group(1)),
                "se": int(m.group(2)),
                "sw": int(m.group(3)),
                "nw": int(m.group(4)),
            }
    return radii


def _parse_forecast_positions(text):
    """Parse all forecast track positions from a JTWC warning text."""
    points = []
    parts = text.split("FORECASTS:")
    forecast_text = parts[1] if len(parts) >= 2 else ""

    blocks = re.split(r"\n[ \t]*-{3,}[ \t]*\n", forecast_text)
    for block in blocks:
        m = re.search(
            r"(\d+)\s*HRS?,?\s*VALID\s*AT:\s*\n\s*"
            r"(\d{6})Z?\s*-{3,}\s+"
            r"([\d.]+)([NS])\s+([\d.]+)([EW])",
            block, re.IGNORECASE
        )
        if not m:
            continue
        hrs = int(m.group(1))
        lat = float(m.group(3)) * (1 if m.group(4) == "N" else -1)
        lon = float(m.group(5)) * (1 if m.group(6) == "E" else -1)

        wind_m = re.search(r"MAX SUSTAINED WINDS\s*-\s*(\d+)\s*KT", block)
        intensity = int(wind_m.group(1)) if wind_m else None

        pt_radii = _parse_wind_radii(block)

        points.append(OrderedDict([
            ("lat", lat),
            ("lon", lon),
            ("advanced_hours", hrs),
            ("intensity", intensity),
            ("intensity_category", _kt_to_category(intensity)),
            ("wind_radii", pt_radii),
        ]))
    return points


def parse_tc_warning(text):
    """Parse JTWC TC warning text (WTPN31 / web.txt format) into structured data.

    Returns
    -------
    dict with keys: storm_name, storm_num, classification, warning_num,
        current_position, movement, max_winds_kt, gusts_kt,
        central_pressure, wave_height_ft, wind_radii, track_points
    """
    text = text.replace("\r\n", "\n")
    result = {
        "storm_name": "",
        "storm_num": "",
        "classification": "",
        "warning_num": "",
        "current_position": {},
        "movement": {},
        "max_winds_kt": None,
        "gusts_kt": None,
        "central_pressure": None,
        "wave_height_ft": None,
        "wind_radii": {},
        "track_points": [],
    }

    m = re.search(
        r"SUBJ/([\w\s]+?)\s+(\d{2})([WSBAPEC])\s+\((\w+)\)\s+WARNING\s+NR\s+(\d+)",
        text
    )
    if m:
        result["classification"] = m.group(1).strip()
        result["storm_num"] = m.group(2)
        result["storm_name"] = m.group(4)
        result["warning_num"] = m.group(5)

    m = re.search(
        r"WARNING POSITION:\s*\n\s*(\d{6})Z\s*-{3,}\s*NEAR\s+"
        r"([\d.]+)([NS])\s+([\d.]+)([EW])",
        text
    )
    if m:
        result["current_position"] = {
            "lat": float(m.group(2)) * (1 if m.group(3) == "N" else -1),
            "lon": float(m.group(4)) * (1 if m.group(5) == "E" else -1),
        }
        result["issued_dtg"] = m.group(1)

    m = re.search(r"MOVEMENT PAST SIX HOURS\s*-\s*(\d+)\s*DEGREES\s*AT\s+(\d+)\s*KTS", text)
    if m:
        result["movement"] = {
            "direction_deg": int(m.group(1)),
            "speed_kt": int(m.group(2)),
        }

    m = re.search(r"MAX SUSTAINED WINDS\s*-\s*(\d+)\s*KT,\s*GUSTS\s*(\d+)\s*KT", text)
    if m:
        result["max_winds_kt"] = int(m.group(1))
        result["gusts_kt"] = int(m.group(2))

    m = re.search(r"MINIMUM CENTRAL PRESSURE.*?IS\s+(\d+)\s*MB", text)
    if m:
        result["central_pressure"] = int(m.group(1))

    m = re.search(r"MAXIMUM\s+SIGNIFICANT WAVE HEIGHT.*?IS\s+([\d.]+)\s*FEET", text)
    if m:
        result["wave_height_ft"] = float(m.group(1))

    result["wind_radii"] = _parse_wind_radii(text)
    result["track_points"] = _parse_forecast_positions(text)

    return result


def download_storm_package(storm_id, output_dir=None):
    """Download the JTWC warning text for a storm and parse it.

    Parameters
    ----------
    storm_id : str
        e.g. "wp0726"
    output_dir : str or Path, optional
        Base output directory. Defaults to get_jtwc_data_dir().

    Returns
    -------
    dict with parsed warning data and file download status
    """
    if output_dir is None:
        output_dir = get_jtwc_data_dir()
    storm_dir = Path(output_dir) / storm_id
    storm_dir.mkdir(parents=True, exist_ok=True)

    url = BASE_URL + f"{storm_id}web.txt"
    dest = storm_dir / f"{storm_id}web.txt"

    text = _download_file(url)
    if text is None:
        return {
            "storm_id": storm_id,
            "local_dir": str(storm_dir),
            "success": False,
            "error": "download failed or file not available",
            "track_points": [],
        }

    dest.write_text(text, encoding="utf-8")
    parsed = parse_tc_warning(text)
    parsed["storm_id"] = storm_id
    parsed["local_dir"] = str(storm_dir)
    parsed["file_path"] = str(dest)
    parsed["success"] = True
    parsed["basin"] = storm_id[:2].upper()
    parsed["classification"] = parsed.get("classification") or "INVEST"
    return parsed


def fetch_active_storm_ids():
    """Parse JTWC area bulletins to extract active storm IDs.

    Falls back to JMA typhoon numbers if bulletins are unreachable.
    JMA's ``typhoonNumber`` format is ``YYNN`` -> JTWC ``wpNNYY``.

    Returns
    -------
    list[str]  e.g. ["wp0726", ...]
    """
    ids = []
    current_yy = _dt.datetime.utcnow().strftime("%y")

    for basin, url in BULLETIN_URLS.items():
        try:
            r = requests.get(url, timeout=30)
            if r.status_code != 200:
                continue
            text = r.text
            suffixes = BASIN_SUFFIX.get(basin)
            if not suffixes:
                continue
            year_match = re.search(r"Z[A-Z]{3}(\d{4})", text)
            yy = year_match.group(1)[-2:] if year_match else current_yy
            for suffix in suffixes:
                for m in re.finditer(r"\b(\d{2})" + suffix + r"\b", text):
                    num_str = m.group(1)
                    if int(num_str) >= 90:
                        continue
                    sid = f"{basin}{num_str}{yy}"
                    if sid not in ids:
                        ids.append(sid)
        except Exception as exc:
            log.warning("Failed to fetch %s bulletin: %s", basin, exc)

    if not ids:
        log.info("Bulletins unreachable -- trying JMA typhoon data as fallback...")
        try:
            from . import jma
            for storm in jma.fetch_target_tc():
                tn = storm.get("typhoonNumber", "")
                if tn and tn.isdigit() and len(tn) == 4:
                    sid = f"wp{tn[2:]}{tn[:2]}"
                    if sid not in ids:
                        ids.append(sid)
        except Exception as exc:
            log.warning("JMA fallback also failed: %s", exc)

    return ids


def probe_storm_ids(candidates):
    """Check which candidate storm IDs exist on the JTWC server.

    Probes each candidate's web.txt URL (HEAD request).  Storms whose
    product returns HTTP 200 are added to the returned list.

    Parameters
    ----------
    candidates : list[str]
        Storm IDs to probe, e.g. ``["ep0626", "sh0126"]``.

    Returns
    -------
    list[str]
        Subset of candidates that exist on the JTWC server.
    """
    found = []
    for sid in candidates:
        try:
            r = requests.head(BASE_URL + f"{sid}.kmz", timeout=10)
            if r.status_code == 200:
                found.append(sid)
        except Exception:
            pass
    return found


class JTWCStormDownloader(QObject):
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
            self.progress.emit("Fetching active JTWC storm IDs from area bulletins...")
            ids = fetch_active_storm_ids()
            if not ids:
                self.progress.emit("No storm IDs from JTWC bulletins.")
                self.error.emit("No active JTWC storms found. Try providing storm IDs manually.")
                self.finished.emit()
                return

            self.progress.emit(f"Found {len(ids)} active JTWC storm(s): {', '.join(ids)}")

            jtwc_dir = get_jtwc_data_dir()
            for storm_id in ids:
                if self._cancelled:
                    break

                self.progress.emit(f"Downloading JTWC {storm_id} KMZ...")
                result = download_storm_kmz(storm_id, jtwc_dir)

                if result.get("success"):
                    n = len(result.get("track_points", []))
                    self.progress.emit(f"  Parsed {n} forecast points for {storm_id}")
                else:
                    self.progress.emit(f"  KMZ unavailable for {storm_id}")

                # Also fetch web.txt to get the warning number
                try:
                    web_result = download_storm_package(storm_id, jtwc_dir)
                    if web_result.get("success"):
                        wn = web_result.get("warning_num", "")
                        if wn:
                            result["warning_num"] = wn
                            self.progress.emit(f"  Warning NR: {wn}")
                except Exception:
                    pass

                self.storm_downloaded.emit(result)
                self.progress.emit(f"  Done with {storm_id}")

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit()
