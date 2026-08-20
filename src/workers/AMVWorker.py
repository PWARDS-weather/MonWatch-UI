# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: workers/AMVWorker.py
# Description: Atmospheric Motion Vector computation and processing worker.
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

import numpy as np
import xarray as xr
from pathlib import Path

from PySide6.QtCore import QObject, Signal


WIND_VAR_SUFFIXES = [
    "Wind_Speed_Shear", "Latitude", "Longitude", "Wind_Speed", "Wind_Dir",
    "UComponent1", "VComponent1", "MedianPress", "QI", "Altitude", "SatZen",
    "AMVChannel", "Target_Type", "BestFitPresLvl", "ExpectedErr",
]


def has_wind_data(nc_path: Path) -> bool:
    """Quick synchronous check for wind variables in NC file metadata."""
    if not nc_path or not nc_path.exists():
        return False
    try:
        with xr.open_dataset(nc_path, engine="netcdf4") as ds:
            return any(v.startswith("wind_") for v in ds.data_vars)
    except Exception:
        return False


class AMVWorker(QObject):
    winds_loaded = Signal(dict)    # {channel: wind_data_dict}
    progress = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(self, nc_path: Path, high_res_amv: bool = False):
        super().__init__()
        self.nc_path = nc_path
        self.high_res_amv = high_res_amv
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if not self.nc_path or not self.nc_path.exists():
            self.error.emit("No NC file found for AMV loading.")
            self.finished.emit()
            return

        try:
            with xr.open_dataset(self.nc_path, engine="netcdf4") as ds:
                wind_vars = [v for v in ds.data_vars if v.startswith("wind_")]
                if not wind_vars:
                    self.error.emit("No AMV wind data in NC file.")
                    self.finished.emit()
                    return

                self.progress.emit(f"Found {len(wind_vars)} wind variables")

                channels = {}
                for v in wind_vars:
                    if self._cancelled:
                        return
                    actual_var = None
                    for suffix in WIND_VAR_SUFFIXES:
                        if v.endswith(suffix):
                            actual_var = suffix
                            break
                    if actual_var is None:
                        continue
                    ch = v[len("wind_"):].split("_")[0]
                    channels.setdefault(ch, {})[actual_var] = ds[v].values

                wind_data = {}
                for ch, vars_dict in channels.items():
                    if self._cancelled:
                        return
                    if not self.high_res_amv and ch.upper() == "C03":
                        self.progress.emit(f"Skipping {ch} (high_res_amv disabled)")
                        continue
                    lat = vars_dict.get("Latitude")
                    lon = vars_dict.get("Longitude")

                    def _first(*keys):
                        for k in keys:
                            val = vars_dict.get(k)
                            if val is not None:
                                return val
                        return None

                    u = _first("UComponent1", "u_wind", "u", "U")
                    v = _first("VComponent1", "v_wind", "v", "V")
                    qi = vars_dict.get("QI")
                    spd = _first("Wind_Speed", "wind_speed", "speed")
                    direc = _first("Wind_Dir", "wind_direction", "direction", "wind_dir")

                    if lat is None or lon is None:
                        continue
                    if u is None and v is None and spd is not None and direc is not None:
                        rad = np.deg2rad(direc.astype(np.float64))
                        u = (-spd * np.sin(rad)).astype(np.float32)
                        v = (-spd * np.cos(rad)).astype(np.float32)
                    if u is None or v is None:
                        continue

                    n = len(lat)
                    _arrays = {"lat": lat, "lon": lon, "u": u, "v": v}
                    if qi is not None:
                        _arrays["qi"] = qi
                    _lens = {k: len(a) for k, a in _arrays.items()}
                    if len(set(_lens.values())) > 1:
                        _min = min(_lens.values())
                        for _k, _a in _arrays.items():
                            _arrays[_k] = _a[:_min]
                        lat, lon, u, v = _arrays["lat"], _arrays["lon"], _arrays["u"], _arrays["v"]
                        qi = _arrays.get("qi")
                        n = _min

                    wind_data[ch] = {
                        "lat": lat.astype(np.float64),
                        "lon": lon.astype(np.float64),
                        "u": u.astype(np.float32),
                        "v": v.astype(np.float32),
                        "qi": qi.astype(np.float32) if qi is not None else None,
                        "press": vars_dict.get("MedianPress", None).astype(np.float32) if vars_dict.get("MedianPress") is not None else None,
                        "source": "amv",
                    }


                if not wind_data:
                    self.error.emit("No usable AMV wind observations found.")
                    self.finished.emit()
                    return

                self.winds_loaded.emit(wind_data)

        except Exception as e:
            if not self._cancelled:
                self.error.emit(f"AMV loading failed: {e}")
        finally:
            self.finished.emit()
