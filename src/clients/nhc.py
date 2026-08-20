# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: clients/nhc.py
# Description: National Hurricane Center data client for Atlantic and Eastern Pacific storm data.
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


import os
import json
import zipfile
import xml.etree.ElementTree as ET
import re
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

import requests
import shapefile

from PySide6.QtCore import QObject, Signal

NHC_CURRENT_STORMS_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"


def get_nhc_data_dir():
    from ..core.helpers import top_dir
    d = top_dir / "data" / "nhc_data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download_file(url, dest):
    r = requests.get(url, stream=True, timeout=120)
    r.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)
    return dest


def fetch_active_storms():
    r = requests.get(NHC_CURRENT_STORMS_URL, timeout=30)
    r.raise_for_status()
    return r.json().get("activeStorms", [])


def _dl_zip_and_extract(url, dest_dir):
    """Download a zip and extract into dest_dir. Returns list of extracted .shp paths."""
    ztmp = dest_dir / "_tmp.zip"
    shp_files = []
    try:
        _download_file(url, ztmp)
        with zipfile.ZipFile(ztmp, 'r') as zf:
            zf.extractall(dest_dir)
        shp_files = list(dest_dir.glob("*.shp"))
    finally:
        if ztmp.exists():
            os.remove(ztmp)
    return shp_files


class NHCDownloader(QObject):
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
            self.progress.emit("Fetching active storms from NHC...")
            storms = fetch_active_storms()
            if not storms:
                self.error.emit("No active storms found on NHC.")
                self.finished.emit()
                return

            nhc_dir = get_nhc_data_dir()
            for storm in storms:
                if self._cancelled:
                    break
                storm_id = storm.get("id", "").lower()
                storm_name = storm.get("name", "Unknown")
                classification = storm.get("classification", "")
                basin = "EP" if storm_id.startswith("ep") else "AL" if storm_id.startswith("al") else "CP"
                self.progress.emit(f"Processing {storm_name} ({storm_id})...")

                storm_dir = nhc_dir / storm_id
                storm_dir.mkdir(exist_ok=True)

                result = {
                    "storm_id": storm_id,
                    "storm_name": storm_name,
                    "classification": classification,
                    "basin": basin,
                    "local_dir": str(storm_dir),
                    # 5-day forecast
                    "track_points": [],
                    "cone_polygons": [],
                    "kmz_track": None,
                    "kmz_cone": None,
                    "advisory_number": "",
                    # Wind radii
                    "wind_radii_initial": {"34":[],"50":[],"64":[]},
                    "wind_radii_forecast": {"34":[],"50":[],"64":[]},
                    "kmz_wind_initial": None,
                    "kmz_wind_forecast": None,
                    # Best track
                    "best_track_points": [],
                    "best_track_line": [],
                    "best_track_kmz": None,
                    # Arrival time
                    "kmz_arrival_earliest": None,
                    "kmz_arrival_likely": None,
                    # Wind speed probabilities
                    "prob_polygons_34": [],
                    "prob_polygons_50": [],
                    "prob_polygons_64": [],
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

                # ---- 5-day KMZs (track + cone) ----
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

                # Parse track and cone from KMZ
                kmz_track = storm_dir / f"{storm_id}_TRACK.kmz"
                kmz_cone = storm_dir / f"{storm_id}_CONE.kmz"
                if kmz_track.exists():
                    self.progress.emit(f"  Parsing track KMZ...")
                    result["track_points"] = self._parse_kmz_track_points(kmz_track)
                if kmz_cone.exists():
                    self.progress.emit(f"  Parsing cone KMZ...")
                    result["cone_polygons"] = self._parse_kmz_cone_polygon(kmz_cone)

                # ---- Wind radii (initial + forecast) ----
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
                    kmz_path = storm_dir / f"{storm_id}_{kmz_key}.kmz"
                    if kmz_path.exists():
                        self.progress.emit(f"  Parsing {radii_key} wind radii KMZ...")
                        result[out_key_prefix] = self._parse_kmz_wind_radii(kmz_path)

                # ---- Best track KMZ ----
                bt_kmz = btg.get("kmzFile", f"https://www.nhc.noaa.gov/gis/best_track/{storm_id}_best_track.kmz")
                try:
                    dest = storm_dir / f"{storm_id}_best_track.kmz"
                    _download_file(bt_kmz, dest)
                    result["best_track_kmz"] = str(dest)
                except Exception:
                    pass
                bt_kmz_path = storm_dir / f"{storm_id}_best_track.kmz"
                if bt_kmz_path.exists():
                    self.progress.emit(f"  Parsing best track KMZ...")
                    bt_pts, bt_line = self._parse_kmz_best_track(bt_kmz_path)
                    if bt_pts:
                        result["best_track_points"] = bt_pts
                    if bt_line:
                        result["best_track_line"] = bt_line

                # ---- Arrival time KMZs ----
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

                # ---- Wind speed probabilities ----
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

                self.storm_downloaded.emit(result)
                self.progress.emit(f"  Done with {storm_name}")

            self.finished.emit()
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit()

    # ---- Utility: read DBF field info ----
    def _log_dbf_fields(self, shp_path):
        fields_str = ""
        try:
            with shapefile.Reader(str(shp_path)) as sf:
                raw_fields = [f for f in sf.fields[1:]]
                parts = [f"{f[0]} ({f[1]}{f[2] if len(f)>2 else ''})" for f in raw_fields]
                fields_str = ", ".join(parts)
                self.progress.emit(f"    DBF fields: {fields_str}")
                if len(sf.shapeRecords()) > 0:
                    srec = sf.shapeRecords()[0]
                    keys = [f[0] for f in sf.fields[1:]]
                    vals = [str(v) for v in srec.record]
                    self.progress.emit(f"    Sample: {', '.join(f'{k}={v}' for k,v in zip(keys, vals))}")
        except Exception as e:
            self.progress.emit(f"    DBF read error: {e}")
        return fields_str

    # ---- 5-day forecast points ----
    def _parse_forecast_points(self, shp_path):
        points = []
        try:
            with shapefile.Reader(str(shp_path)) as sf:
                fields = [f[0] for f in sf.fields[1:]]
                basin = "AL"
                for srec in sf.shapeRecords():
                    attrs = dict(zip(fields, srec.record))
                    basin = str(attrs.get("BASIN", "AL")).strip()
                    break
                is_wp = basin.upper() == "WP"
                for srec in sf.shapeRecords():
                    attrs = dict(zip(fields, srec.record))
                    if srec.shape.shapeType != shapefile.POINT:
                        continue
                    lon, lat = srec.shape.points[0]
                    intensity = attrs.get("MAXWIND")
                    dt_formatted = ""
                    # Parse ADVDATE for base month/year
                    _base_dt = None
                    raw_adv = str(attrs.get("ADVDATE", "")).strip()
                    if raw_adv:
                        import re as _re
                        clean = _re.sub(r' [A-Z]{3,4} ', ' ', raw_adv)
                        clean = _re.sub(r' (Mon|Tue|Wed|Thu|Fri|Sat|Sun) ', ' ', clean)
                        for _fmt in ("%I%M %p %b %d %Y", "%I%M%p %b %d %Y"):
                            try:
                                _base_dt = datetime.strptime(clean, _fmt)
                                break
                            except (ValueError, TypeError):
                                continue
                    # Parse VALIDTIME for per-point day/hour/minute
                    raw_vt = str(attrs.get("VALIDTIME", "")).strip()
                    if raw_vt and _base_dt:
                        try:
                            vt = datetime.strptime(raw_vt, "%d/%H%M")
                            vt = vt.replace(year=_base_dt.year, month=_base_dt.month)
                            if vt.day < _base_dt.day:
                                vt = vt.replace(month=_base_dt.month + 1 if _base_dt.month < 12 else 1,
                                                year=_base_dt.year if _base_dt.month < 12 else _base_dt.year + 1)
                            dt_formatted = vt.strftime("%Y-%m-%d %H:%M")
                        except (ValueError, TypeError):
                            pass
                    if not dt_formatted and _base_dt:
                        dt_formatted = _base_dt.strftime("%Y-%m-%d %H:%M")

                    intensity_val = float(intensity) if intensity else None
                    raw_cat = str(attrs.get("STORMTYPE", "") or attrs.get("SS", "") or "").strip()
                    if raw_cat:
                        intensity_cat = raw_cat
                    elif intensity_val is not None:
                        if is_wp:
                            intensity_cat = "STY" if intensity_val >= 130 else "TY" if intensity_val >= 96 else "STS" if intensity_val >= 64 else "TS" if intensity_val >= 34 else "TD"
                        else:
                            intensity_cat = "Major Hurricane" if intensity_val >= 96 else "Hurricane" if intensity_val >= 64 else "TS" if intensity_val >= 34 else "TD"
                    else:
                        intensity_cat = None

                    points.append(OrderedDict([
                        ("lat", lat), ("lon", lon),
                        ("datetime", dt_formatted),
                        ("intensity", intensity_val),
                        ("intensity_category", intensity_cat),
                    ]))
                    points.sort(key=lambda p: p.get("datetime", ""))
        except Exception as e:
            print(f"Forecast points parse error: {e}")
        return points

    # ---- Cone polygon ----
    def _parse_cone_polygon(self, shp_path):
        polygons = []
        try:
            with shapefile.Reader(str(shp_path)) as sf:
                for s in sf.shapes():
                    st = s.shapeType
                    if st in (shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
                        poly = [(float(lon), float(lat)) for lon, lat in s.points]
                        if poly:
                            polygons.append(poly)
                        break
                    if st in (shapefile.POLYLINE, shapefile.POLYLINEZ, shapefile.POLYLINEM):
                        poly = [(float(lon), float(lat)) for lon, lat in s.points]
                        if poly and len(poly) >= 3:
                            polygons.append(poly)
                        break
        except Exception as e:
            print(f"Cone parse error: {e}")
        return polygons

    # ---- Wind radii (wind field shapefiles) ----
    def _parse_wind_radii(self, shp_files):
        """Parse wind radii shapefiles into {34:[polygons], 50:[polygons], 64:[polygons]}."""
        result = {"34":[], "50":[], "64":[]}
        try:
            for shp in shp_files:
                with shapefile.Reader(str(shp)) as sf:
                    fields = [f[0] for f in sf.fields[1:]]
                    for srec in sf.shapeRecords():
                        attrs = dict(zip(fields, srec.record))
                        radii_val = str(attrs.get("RADII", "") or "")
                        # Determine wind speed threshold from field value
                        radii_kt = None
                        if "34" in radii_val:
                            radii_kt = "34"
                        elif "50" in radii_val:
                            radii_kt = "50"
                        elif "64" in radii_val:
                            radii_kt = "64"
                        if radii_kt is None:
                            continue
                        poly = [(float(lon), float(lat)) for lon, lat in srec.shape.points]
                        if poly:
                            result[radii_kt].append(poly)
        except Exception as e:
            print(f"Wind radii parse error: {e}")
        return result

    # ---- Best track ----
    def _parse_best_track(self, shp_path):
        points = []
        try:
            with shapefile.Reader(str(shp_path)) as sf:
                fields = [f[0] for f in sf.fields[1:]]
                for srec in sf.shapeRecords():
                    attrs = dict(zip(fields, srec.record))
                    if srec.shape.shapeType not in (shapefile.POINT, shapefile.POINTZ, shapefile.POINTM):
                        continue
                    lon, lat = srec.shape.points[0]
                    dt_str = attrs.get("ADVDATE", "")
                    intensity = attrs.get("INTENSITY")
                    dt_formatted = ""
                    for fmt in ("%Y%m%d%H%M", "%Y-%m-%d %H:%M", "%Y%m%d_%H%M", "%Y%m%d %H%M", "%Y-%m-%dT%H:%M:%S"):
                        try:
                            dt = datetime.strptime(str(dt_str).strip(), fmt)
                            dt_formatted = dt.strftime("%Y-%m-%d %H:%M")
                            break
                        except (ValueError, TypeError):
                            continue
                    points.append(OrderedDict([
                        ("lat", lat), ("lon", lon),
                        ("datetime", dt_formatted),
                        ("intensity", float(intensity) if intensity else None),
                    ]))
                    points.sort(key=lambda p: p.get("datetime",""))
        except Exception as e:
            print(f"Best track parse error: {e}")
        return points

    # ---- Polyline (best track line, etc.) ----
    def _parse_polyline(self, shp_path):
        lines = []
        try:
            with shapefile.Reader(str(shp_path)) as sf:
                for s in sf.shapes():
                    if s.shapeType in (shapefile.POLYLINE, shapefile.POLYLINEZ, shapefile.POLYLINEM,
                                       shapefile.POLYGON, shapefile.POLYGONZ, shapefile.POLYGONM):
                        pts = [(float(lon), float(lat)) for lon, lat in s.points]
                        if pts:
                            lines.append(pts)
        except Exception as e:
            print(f"Polyline parse error: {e}")
        return lines

    # ---- KMZ/KML parsing helpers ----

    @staticmethod
    def _extract_kml_text(kmz_path):
        """Extract KML text from a KMZ (zip) file."""
        with zipfile.ZipFile(str(kmz_path), 'r') as zf:
            kml_files = [n for n in zf.namelist() if n.lower().endswith(".kml")]
            if not kml_files:
                raise ValueError(f"No KML found in {kmz_path}")
            with zf.open(kml_files[0]) as f:
                return f.read()

    @staticmethod
    def _parse_kml_coords(coord_text):
        """Parse KML coordinate string 'lon,lat,alt lon,lat,alt ...' into [(lon, lat), ...]."""
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

    @staticmethod
    def _get_kml_ns(root):
        """Extract KML namespace from root element tag."""
        tag = root.tag
        m = re.match(r'\{([^}]+)\}', tag)
        return m.group(1) if m else ""

    @staticmethod
    def _local_tag(element):
        """Get the local (namespace-stripped) tag name."""
        tag = element.tag
        return tag.split("}")[-1] if "}" in tag else tag

    @classmethod
    def _find_all_placemarks(cls, root, ns):
        """Yield all Placemark elements from a KML root."""
        if ns:
            for pm in root.iter(f"{{{ns}}}Placemark"):
                yield pm
        else:
            for pm in root.iter("Placemark"):
                yield pm

    @classmethod
    def _find_tag(cls, element, tag, ns):
        """Find first child with given tag."""
        child = element.find(f"{{{ns}}}{tag}") if ns else element.find(tag)
        if child is None and ns:
            child = element.find(tag)
        return child

    @classmethod
    def _findall_tag(cls, element, tag, ns):
        """Find all children with given tag."""
        if ns:
            children = element.findall(f"{{{ns}}}{tag}")
            if children:
                return children
        return element.findall(tag)

    @classmethod
    def _get_placemark_name(cls, pm, ns):
        """Get the name text from a Placemark."""
        name_el = cls._find_tag(pm, "name", ns)
        return name_el.text.strip() if name_el is not None and name_el.text else ""

    @classmethod
    def _get_coordinates_from_geometry(cls, geom, ns):
        """Extract coordinates from any geometry element (Point, LineString, LinearRing, Polygon)."""
        # Direct coordinates child
        coords_el = cls._find_tag(geom, "coordinates", ns)
        if coords_el is not None and coords_el.text:
            return cls._parse_kml_coords(coords_el.text)
        # outerBoundaryIs > LinearRing > coordinates (Polygon)
        obi = cls._find_tag(geom, "outerBoundaryIs", ns)
        if obi is not None:
            lr = cls._find_tag(obi, "LinearRing", ns)
            if lr is not None:
                coords_el = cls._find_tag(lr, "coordinates", ns)
                if coords_el is not None and coords_el.text:
                    return cls._parse_kml_coords(coords_el.text)
        # MultiGeometry
        mg = cls._find_tag(geom, "MultiGeometry", ns)
        if mg is not None:
            for child in list(mg):
                tag = cls._local_tag(child)
                if tag in ("Point", "LineString", "LinearRing", "Polygon"):
                    result = cls._get_coordinates_from_geometry(child, ns)
                    if result:
                        return result
        # Point
        pt = cls._find_tag(geom, "Point", ns)
        if pt is not None:
            coords_el = cls._find_tag(pt, "coordinates", ns)
            if coords_el is not None and coords_el.text:
                return cls._parse_kml_coords(coords_el.text)
        # LineString
        ls = cls._find_tag(geom, "LineString", ns)
        if ls is not None:
            coords_el = cls._find_tag(ls, "coordinates", ns)
            if coords_el is not None and coords_el.text:
                return cls._parse_kml_coords(coords_el.text)
        # LinearRing
        lr = cls._find_tag(geom, "LinearRing", ns)
        if lr is not None:
            coords_el = cls._find_tag(lr, "coordinates", ns)
            if coords_el is not None and coords_el.text:
                return cls._parse_kml_coords(coords_el.text)
        return []

    @classmethod
    def _parse_kmz_track_points(cls, kmz_path):
        """Parse TRACK.kmz into list of point dicts (same format as _parse_forecast_points).

        NHC defines storm category by wind speed thresholds, so category is derived
        from the official Maximum Wind value in each Placemark.
        """
        points = []
        try:
            kml_text = cls._extract_kml_text(kmz_path)
            root = ET.fromstring(kml_text)
            ns = cls._get_kml_ns(root)
            for pm in cls._find_all_placemarks(root, ns):
                pt = cls._find_tag(pm, "Point", ns)
                if pt is None:
                    continue
                coords_list = cls._get_coordinates_from_geometry(pm, ns)
                if not coords_list:
                    continue
                lon, lat = coords_list[0]
                # Extract intensity from description
                desc_el = cls._find_tag(pm, "description", ns)
                desc = ""
                if desc_el is not None:
                    desc = desc_el.text or ""
                    if not desc:
                        desc = "".join(desc_el.itertext())
                intensity_val = None
                if desc:
                    m = re.search(r'Maximum\s+Wind[:\s]+(\d+)\s*knots?', desc, re.IGNORECASE)
                    if m:
                        intensity_val = float(m.group(1))
                # Compute category from intensity (same logic as _parse_forecast_points)
                intensity_cat = None
                if intensity_val is not None:
                    if intensity_val >= 96:
                        intensity_cat = "Major Hurricane"
                    elif intensity_val >= 64:
                        intensity_cat = "Hurricane"
                    elif intensity_val >= 34:
                        intensity_cat = "TS"
                    else:
                        intensity_cat = "TD"
                # Extract timestamp from description (Valid at: ...)
                dt_formatted = ""
                if desc:
                    m3 = re.search(r'Valid\s+at[:\s]+(.+?)(?:</TD|</td|</TD|</td)', desc, re.IGNORECASE | re.DOTALL)
                    if m3:
                        raw = m3.group(1).strip()
                        # Parse "2:00 PM PDT July 17, 2026" or "11:00 PM PDT July 17 2026"
                        m4 = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)\s+\w+\s+(\w+\s+\d+),?\s*(\d{4})', raw)
                        if m4:
                            time_part = m4.group(1)
                            date_str = f"{m4.group(2)} {m4.group(3)}"
                            # Parse date first
                            for dfmt in ("%B %d %Y", "%b %d %Y", "%B %d,%Y", "%b %d,%Y"):
                                try:
                                    dt_date = datetime.strptime(date_str, dfmt)
                                    # Combine
                                    for tfmt in ("%Y-%m-%d %I:%M %p", "%Y-%m-%d %I:%M%p"):
                                        try:
                                            combined = f"{dt_date.strftime('%Y-%m-%d')} {time_part}"
                                            dt2 = datetime.strptime(combined, tfmt)
                                            dt_formatted = dt2.strftime("%Y-%m-%d %H:%M")
                                            break
                                        except Exception:
                                            continue
                                    if dt_formatted:
                                        break
                                except Exception:
                                    continue
                # Also try TimeStamp element
                if not dt_formatted:
                    ts_el = cls._find_tag(pm, "TimeStamp", ns)
                    if ts_el is not None:
                        when_el = cls._find_tag(ts_el, "when", ns)
                        if when_el is not None and when_el.text:
                            raw = when_el.text.strip()
                            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%MZ", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                                try:
                                    dt = datetime.strptime(raw, fmt)
                                    dt_formatted = dt.strftime("%Y-%m-%d %H:%M")
                                    break
                                except (ValueError, TypeError):
                                    continue

                points.append(OrderedDict([
                    ("lat", lat), ("lon", lon),
                    ("datetime", dt_formatted),
                    ("intensity", intensity_val),
                    ("intensity_category", intensity_cat),
                ]))
            points.sort(key=lambda p: p.get("datetime", ""))
        except Exception as e:
            print(f"KMZ track parse error: {e}")
        return points

    @classmethod
    def _parse_kmz_cone_polygon(cls, kmz_path):
        """Parse CONE.kmz into list of polygon coordinate lists."""
        polygons = []
        try:
            kml_text = cls._extract_kml_text(kmz_path)
            root = ET.fromstring(kml_text)
            ns = cls._get_kml_ns(root)
            for pm in cls._find_all_placemarks(root, ns):
                polygon_el = cls._find_tag(pm, "Polygon", ns)
                if polygon_el is not None:
                    coords = cls._get_coordinates_from_geometry(polygon_el, ns)
                    if coords and len(coords) >= 3:
                        polygons.append(coords)
                        break
        except Exception as e:
            print(f"KMZ cone parse error: {e}")
        return polygons

    @classmethod
    def _parse_kmz_wind_radii(cls, kmz_path):
        """Parse wind radii KMZ into {34:[polys], 50:[polys], 64:[polys]}."""
        result = {"34": [], "50": [], "64": []}
        try:
            kml_text = cls._extract_kml_text(kmz_path)
            root = ET.fromstring(kml_text)
            ns = cls._get_kml_ns(root)
            for pm in cls._find_all_placemarks(root, ns):
                name = cls._get_placemark_name(pm, ns)
                radii_kt = None
                if "34" in name:
                    radii_kt = "34"
                elif "50" in name:
                    radii_kt = "50"
                elif "64" in name:
                    radii_kt = "64"
                if radii_kt is None:
                    continue
                polygon_el = cls._find_tag(pm, "Polygon", ns)
                if polygon_el is not None:
                    coords = cls._get_coordinates_from_geometry(polygon_el, ns)
                    if coords:
                        result[radii_kt].append(coords)
        except Exception as e:
            print(f"KMZ wind radii parse error: {e}")
        return result

    @classmethod
    def _parse_kmz_best_track(cls, kmz_path):
        """Parse best_track.kmz into (points_list, line_list).

        Uses styleUrl (#ts, #cat1, #cat2, etc.) as the official NHC category.
        """
        CAT_FROM_STYLE = {
            "ts": "TS", "td": "TD", "ex": "EX", "db": "DB",
            "lo": "LO", "ss": "SS", "sd": "SD", "wv": "WV", "tc": "TC",
            "cat1": "Hurricane", "cat2": "Hurricane",
            "cat3": "Major Hurricane", "cat4": "Major Hurricane", "cat5": "Major Hurricane",
            "ty": "TY", "st": "ST",
        }
        points = []
        lines = []
        try:
            kml_text = cls._extract_kml_text(kmz_path)
            root = ET.fromstring(kml_text)
            ns = cls._get_kml_ns(root)
            for pm in cls._find_all_placemarks(root, ns):
                # Read official NHC category from styleUrl
                official_cat = None
                style_el = cls._find_tag(pm, "styleUrl", ns)
                if style_el is not None and style_el.text:
                    sid = style_el.text.strip().lstrip("#")
                    official_cat = CAT_FROM_STYLE.get(sid)

                mg = cls._find_tag(pm, "MultiGeometry", ns)
                if mg is not None:
                    for child in list(mg):
                        tag = cls._local_tag(child)
                        coords = cls._get_coordinates_from_geometry(child, ns)
                        if not coords:
                            continue
                        if tag == "Point":
                            for lon, lat in coords:
                                points.append(OrderedDict([
                                    ("lat", lat), ("lon", lon),
                                    ("datetime", ""), ("intensity", None),
                                    ("intensity_category", official_cat),
                                ]))
                        elif tag in ("LineString", "LinearRing"):
                            if len(coords) >= 2:
                                lines.append(coords)
                else:
                    pt = cls._find_tag(pm, "Point", ns)
                    if pt is not None:
                        coords = cls._get_coordinates_from_geometry(pm, ns)
                        if coords:
                            for lon, lat in coords:
                                # Extract intensity from description
                                desc_el = cls._find_tag(pm, "description", ns)
                                desc = ""
                                if desc_el is not None:
                                    desc = desc_el.text or ""
                                    if not desc:
                                        desc = "".join(desc_el.itertext())
                                intensity_val = None
                                if desc:
                                    m = re.search(r'(\d+)\s*knots', desc)
                                    if m:
                                        intensity_val = float(m.group(1))
                                # Extract datetime from name
                                name = cls._get_placemark_name(pm, ns)
                                dt_formatted = ""
                                if name:
                                    m2 = re.search(r'(\d{4})\s+UTC\s+(\w+)\s+(\d+)', name)
                                    if m2:
                                        hr = int(m2.group(1)[:2])
                                        mn = int(m2.group(1)[2:])
                                        mon = m2.group(2)
                                        day = int(m2.group(3))
                                        mon_map = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
                                                   "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}
                                        mnum = mon_map.get(mon.upper(), 1)
                                        # We don't have year, use current or infer
                                        yr = datetime.now().year
                                        try:
                                            dt = datetime(yr, mnum, day, hr, mn)
                                            dt_formatted = dt.strftime("%Y-%m-%d %H:%M")
                                        except Exception:
                                            pass
                                points.append(OrderedDict([
                                    ("lat", lat), ("lon", lon),
                                    ("datetime", dt_formatted),
                                    ("intensity", intensity_val),
                                    ("intensity_category", official_cat),
                                ]))
                    ls = cls._find_tag(pm, "LineString", ns)
                    if ls is not None:
                        coords = cls._get_coordinates_from_geometry(pm, ns)
                        if coords and len(coords) >= 2:
                            lines.append(coords)
            points.sort(key=lambda p: p.get("datetime", ""))
        except Exception as e:
            print(f"KMZ best track parse error: {e}")
        return points, lines
