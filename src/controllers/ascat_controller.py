# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: controllers/ascat_controller.py
# Description: Controller for ASCAT scatterometer data discovery. Runs the
#              ASCATDownloader on a background QThread and feeds the results
#              into the ASCAT metadata window.
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


from PySide6.QtCore import QObject, QThread

# Must match the target-count map used by the viewport wind renderer
# (overlay_controller._compute_channel). The ASCAT loaders feed raw wind
# points straight into that renderer, which down-samples to the density
# target — so the loaders should keep AT LEAST as many points as the
# highest density wants, otherwise Intensive would be capped to a handful.
WIND_DENSITY_TARGETS = {
    "Low": 200,
    "Medium": 1000,
    "Normal": 3000,
    "High": 10000,
    "Intensive": 1500000,
}


def _wind_density_target(main_ui, default="Normal"):
    density = str(main_ui.settings.get("wind_density", default))
    return WIND_DENSITY_TARGETS.get(density, WIND_DENSITY_TARGETS[default])


class AscatController(QObject):
    """Coordinates one-click ASCAT data discovery + metadata window."""

    def __init__(self, main_ui):
        parent = main_ui if isinstance(main_ui, QObject) else None
        super().__init__(parent)
        self.main_ui = main_ui
        self.window = None
        self._thread = None
        self._worker = None
        self._busy = False
        self._dl_busy = False
        self._dl_thread = None
        self._dl_worker = None
        self._last_result = None
        self._last_nc_info = None
        self._local_swath_files = []

    # ------------------------------------------------------------------ UI
    def open_ascat_window(self):
        if self.window is not None and self.window.isVisible():
            self.window.raise_()
            self.window.activateWindow()
            self.window._populate_region_storms()
            return
        from ..ui.ascat_window import AscatWindow
        # Parentless on purpose: an (owned) child of the always-on-top main
        # window would stay pinned above other apps on Windows.
        self.window = AscatWindow(self.main_ui, parent=None)
        self.window.refresh_requested.connect(self.fetch_latest)
        self.window.download_requested.connect(self.download_latest_nc)
        self.window.swath_requested.connect(self.download_latest_swath)
        self.window.podaac_requested.connect(self.download_podaac_swath)
        self.window.region_crop_requested.connect(self.recrop_swath)
        self.window.show()
        self.window._populate_region_storms()
        if self._last_result:
            self.window.set_data(self._last_result)
        self.fetch_latest()

    def on_atcf_updated(self):
        """Refill the ASCAT focus-storm dropdown after ATCF data loads."""
        if self.window is None:
            return
        try:
            self.window._populate_region_storms()
        except Exception as e:
            self.main_ui.log(f"[ASCAT] storm dropdown refresh failed: {e}")

    def close_window(self):
        if self.window is not None:
            self.window.close()
            self.window = None

    def _parse_info_dt(self, info):
        """Best-effort parse of an ASCAT info dict 'time_str' to UTC."""
        from datetime import datetime, timezone
        ts = info.get("time_str") or ""
        if not ts:
            return None
        ts = str(ts).replace(" UTC", "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            except Exception:
                continue
        return None

    def remove_ascat_cache(self):
        """Delete downloaded ASCAT swath/netcdf files and clear the overlay.

        Returns the list of deleted filenames.
        """
        from pathlib import Path
        from ..clients.ascat import get_ascat_data_dir
        try:
            dest = Path(get_ascat_data_dir())
        except Exception as e:
            self.main_ui.log(f"[ASCAT] cache dir lookup failed: {e}")
            return []
        removed = []
        if dest.exists():
            for pattern in ("*.nc", "*.nc.gz", "*.gz"):
                for p in dest.glob(pattern):
                    try:
                        p.unlink()
                        removed.append(p.name)
                    except Exception as e:
                        self.main_ui.log(f"[ASCAT] could not delete {p.name}: {e}")
        # Clear the in-memory overlay so stale barbs disappear.
        mw = self.main_ui
        oc = getattr(mw, "overlay_controller", None)
        for target in (mw, oc):
            if target is None:
                continue
            if hasattr(target, "current_winds_uv"):
                target.current_winds_uv = None
            if hasattr(target, "winds_enabled"):
                target.winds_enabled = False
        for c in (getattr(mw, "_wind_proj_cache", None),
                  getattr(oc, "_wind_proj_cache", None)):
            if isinstance(c, dict):
                try:
                    c.clear()
                except Exception:
                    pass
        if hasattr(mw, "_ascat_wind_dt"):
            mw._ascat_wind_dt = None
        self._local_swath_files = []
        try:
            cb = getattr(mw, "overlay_checkboxes", {}) or {}
            wind_cb = cb.get("Show Winds (AMV)")
            if wind_cb is not None:
                wind_cb.blockSignals(True)
                wind_cb.setChecked(False)
                wind_cb.blockSignals(False)
        except Exception:
            pass
        if hasattr(mw, "_draw_winds_overlay"):
            try:
                # _draw_winds_overlay early-returns while _winds_persist is
                # True (the "barbs stick" guard); drop it so the overlay is
                # actually cleared and the viewport refreshes.
                mw._winds_persist = False
                if oc is not None:
                    oc._winds_persist = False
                mw._draw_winds_overlay()
            except Exception as e:
                self.main_ui.log(f"[ASCAT] overlay clear after cache removal: {e}")
        self.main_ui.log(f"[ASCAT] ASCAT cache cleared: {len(removed)} file(s) removed")
        return removed

    # -------------------------------------------------------------- fetch
    def fetch_latest(self):
        if self._busy:
            return
        self._busy = True
        if self.window is not None:
            self.window.set_status("Fetching latest ASCAT data...")

        from ..clients.ascat import ASCATDownloader
        worker = ASCATDownloader()
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_progress)
        worker.result.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._thread = thread
        self._worker = worker
        thread.start()

    # ------------------------------------------------------------ signals
    def _on_progress(self, msg):
        self.main_ui.log(f"[ASCAT] {msg}")
        if self.window is not None:
            self.window.set_status(msg)

    def _on_result(self, data):
        self._busy = False
        self._last_result = data
        if self.window is not None:
            self.window.set_data(data)
        self.main_ui.log(
            f"[ASCAT] Latest data fetched: {len(data.get('storms', []))} "
            f"storm product(s), {len(data.get('products', []))} OSI SAF product(s)."
        )

    def _on_error(self, msg):
        self._busy = False
        self.main_ui.log(f"[ASCAT] error: {msg}")
        if self.window is not None:
            self.window.set_status(f"Error: {msg}")

    # ------------------------------------------------------- download+load
    def download_latest_nc(self, region=None):
        """One-click: download the latest wind NetCDF and load it into the app."""
        if getattr(self, "_dl_busy", False):
            return
        self._dl_busy = True
        if self.window is not None:
            self.window.set_download_status("Downloading latest wind NetCDF...", "busy")

        from ..clients.ascat import ASCATNetCDFDownloader
        worker = ASCATNetCDFDownloader(region=region)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_dl_progress)
        worker.result.connect(self._on_dl_result)
        worker.error.connect(self._on_dl_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._dl_thread = thread
        self._dl_worker = worker
        thread.start()

    def _on_dl_progress(self, msg):
        self.main_ui.log(f"[ASCAT] {msg}")
        if self.window is not None:
            self.window.set_download_status(msg, "busy")

    def _on_dl_result(self, info):
        self._dl_busy = False
        self._last_nc_info = info
        path = info.get("file_path", "")
        self.main_ui.log(
            f"[ASCAT] Wind NetCDF downloaded: {path} "
            f"({info.get('dataset_id', '?')}, {info.get('time_str', '?')})"
        )
        # The freshly scraped ASCAT pass times (e.g. 13:21Z / 14:21Z for the
        # active storm) are MORE authoritative for "how fresh is the real
        # ASCAT data" than the downloadable analysis/hybrid grid's valid time.
        # Use the newest scraped pass as the reference for the age warning so
        # a genuinely recent Metop-B/C pass is not reported as old just
        # because the anonymous NetCDF grid is a 6-hourly analysis.
        storm_latest_utc = self._latest_scraped_pass()
        # Load the wind field into the viewport using the app's AMV pipeline.
        loaded = self._load_into_view(info)
        warning = self._compute_age_warning(info, storm_latest_utc)
        payload = {**info, "loaded": loaded, "warning": warning,
                   "storm_latest_pass": storm_latest_utc}
        if self.window is not None:
            self.window.set_download_result(payload)

    def _latest_scraped_pass(self):
        """Return the newest ASCAT pass datetime scraped from Manati, or None."""
        try:
            storms = (self._last_result or {}).get("storms", []) or []
            utcs = [s.get("latest_pass_utc") for s in storms]
            utcs = [u for u in utcs if u is not None]
            return max(utcs) if utcs else None
        except Exception:
            return None

    def _on_dl_error(self, msg):
        self._dl_busy = False
        self.main_ui.log(f"[ASCAT] wind download error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"Download failed: {msg}", "error")

    # --------------------------------------------------- KNMI swath (real-time)
    def download_latest_swath(self, region=None):
        """One-click: pull real-time ASCAT swath passes from KNMI OSI SAF FTP
        and load them into the app. Requires a stored 'KNMI OSI SAF' FTP
        account (free credentials from scat@knmi.nl)."""
        if getattr(self, "_swath_busy", False):
            return
        from ..clients.knmi_swath import find_knmi_ftp_account
        acc = find_knmi_ftp_account(self.main_ui.settings)
        if acc is None:
            msg = ("No KNMI OSI SAF FTP account configured. Add it in the "
                   "Accounts window (name 'KNMI OSI SAF', server "
                   "'ftppro.knmi.nl', credentials from scat@knmi.nl).")
            self.main_ui.log(f"[ASCAT] {msg}")
            if self.window is not None:
                self.window.set_download_status(msg, "error")
            return
        self._swath_busy = True
        if self.window is not None:
            self.window.set_download_status("Downloading real-time ASCAT swath...", "busy")

        from ..clients.knmi_swath import KNMISwathDownloader
        worker = KNMISwathDownloader(account=acc, region=region)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_dl_progress)
        worker.result.connect(self._on_swath_result)
        worker.error.connect(self._on_swath_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._swath_thread = thread
        self._swath_worker = worker
        thread.start()

    def _on_swath_result(self, info):
        self._swath_busy = False
        self._last_nc_info = info
        self._local_swath_files = [p.get("file_path") for p in (info.get("passes") or [])
                                   if p.get("file_path")]
        self.main_ui.log(
            f"[ASCAT] Real-time swath: {info.get('pass_count', 0)} pass(es), "
            f"newest {info.get('time_str', '?')}, "
            f"{info.get('points', info.get('pass_count', 0))} wind cells."
        )
        loaded = self._load_swath_into_view(info)
        payload = {**info, "loaded": loaded, "warning": ""}
        if self.window is not None:
            self.window.set_download_result(payload)
        self._refresh_pass_combo(info)

    def _on_swath_error(self, msg):
        self._swath_busy = False
        self.main_ui.log(f"[ASCAT] swath download error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"Swath download failed: {msg}", "error")

    # ---------------------------------------- PO.DAAC swath (free, HTTPS)
    def download_podaac_swath(self, region=None):
        """One-click: pull real-time ASCAT swath passes from the NASA PO.DAAC
        cloud archive over HTTPS. Requires a stored 'NASA Earthdata' account
        (free, instant self-service sign-up at https://urs.earthdata.nasa.gov)."""
        if getattr(self, "_podaac_busy", False):
            return
        from ..clients.podaac_swath import find_earthdata_account
        acc = find_earthdata_account(self.main_ui.settings)
        if acc is None:
            msg = ("No NASA Earthdata account configured. Add it in the "
                   "Accounts window (name 'NASA Earthdata', server "
                   "'urs.earthdata.nasa.gov'); create a free account at "
                   "https://urs.earthdata.nasa.gov")
            self.main_ui.log(f"[ASCAT] {msg}")
            if self.window is not None:
                self.window.set_download_status(msg, "error")
            return
        self._podaac_busy = True
        if self.window is not None:
            self.window.set_download_status("Downloading real-time ASCAT swath (NASA PO.DAAC)...", "busy")

        from ..clients.podaac_swath import PODAACSwathDownloader
        worker = PODAACSwathDownloader(account=acc, region=region)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_dl_progress)
        worker.result.connect(self._on_podaac_result)
        worker.error.connect(self._on_podaac_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._podaac_thread = thread
        self._podaac_worker = worker
        thread.start()

    def _on_podaac_result(self, info):
        self._podaac_busy = False
        self._last_nc_info = info
        self._local_swath_files = [p.get("file_path") for p in (info.get("passes") or [])
                                   if p.get("file_path")]
        self.main_ui.log(
            f"[ASCAT] Real-time swath (PO.DAAC): {info.get('pass_count', 0)} pass(es), "
            f"newest {info.get('time_str', '?')}, "
            f"{info.get('points', info.get('pass_count', 0))} wind cells."
        )
        loaded = self._load_swath_into_view(info)
        payload = {**info, "loaded": loaded, "warning": ""}
        if self.window is not None:
            self.window.set_download_result(payload)
        self._refresh_pass_combo(info)

    def _on_podaac_error(self, msg):
        self._podaac_busy = False
        self.main_ui.log(f"[ASCAT] PO.DAAC swath download error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"PO.DAAC swath download failed: {msg}", "error")

    # ------------------------------------------------ local cache re-crop
    def recrop_swath(self, region=None):
        """Adjust the wind overlay to a new region using already-downloaded
        local swath .nc granules (re-read + filter, no re-download)."""
        if getattr(self, "_crop_busy", False):
            return
        if getattr(self, "_podaac_busy", False) or getattr(self, "_swath_busy", False):
            return
        from pathlib import Path
        from ..clients.ascat import get_ascat_data_dir
        from ..clients.knmi_swath import select_local_swath_files
        files = [f for f in (self._local_swath_files or [])
                 if Path(str(f).strip('"')).exists()]
        if not files:
            # Fresh session: fall back to whatever orbits are already on disk.
            try:
                dest = Path(get_ascat_data_dir())
                files = [str(p) for p in dest.glob("*.nc")
                         if p.stat().st_size > 0]
            except Exception:
                files = []
        if not files:
            if self.window is not None:
                self.window.set_download_status(
                    "No cached swath files — fetch a Real-Time Swath pass first.",
                    "error")
            return
        files = select_local_swath_files(files, region)
        if not files:
            if self.window is not None:
                self.window.set_download_status(
                    "No cached swath files match the current region.", "error")
            return
        self._crop_busy = True
        if self.window is not None:
            self.window.set_download_status(
                f"Re-cropping {len(files)} cached swath file(s) to new region...", "busy")

        from ..clients.knmi_swath import LocalSwathCropWorker
        worker = LocalSwathCropWorker(files, region=region)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_dl_progress)
        worker.result.connect(self._on_crop_result)
        worker.error.connect(self._on_crop_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._crop_thread = thread
        self._crop_worker = worker
        thread.start()

    def _on_crop_result(self, info):
        self._crop_busy = False
        if not isinstance(info, dict):
            if self.window is not None:
                self.window.set_download_status(
                    "No cached swath wind data in the new region.", "error")
            return
        self._last_nc_info = info
        loaded = self._load_swath_into_view(info)
        payload = {**info, "loaded": loaded, "warning": ""}
        self.main_ui.log(
            f"[ASCAT] Re-cropped cached swath to new region: "
            f"{info.get('pass_count', 0)} pass(es), "
            f"{info.get('points', 0)} wind cells."
        )
        if self.window is not None:
            self.window.set_download_result(payload)
        self._refresh_pass_combo(info)

    def _on_crop_error(self, msg):
        self._crop_busy = False
        self.main_ui.log(f"[ASCAT] swath re-crop error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"Swath re-crop failed: {msg}", "error")

    def _load_swath_into_view(self, info):
        """Publish pre-converted swath point arrays into the wind overlay."""
        try:
            import numpy as np
            wind_data = info.get("wind_data")
            if not wind_data or "lat" not in wind_data:
                return False
            n = len(wind_data["lat"])
            if n == 0:
                return False
            # Keep up to the density target (defaults to Intensive-grade). The
            # viewport renderer down-samples further to the current setting,
            # so a thick swath still saturates the requested density.
            step = max(1, int(np.ceil(n / max(1, _wind_density_target(self.main_ui)))))
            idx = slice(None, None, step)
            payload = {
                "ASCAT": {
                    "lat": np.asarray(wind_data["lat"], dtype=np.float64)[idx],
                    "lon": np.asarray(wind_data["lon"], dtype=np.float64)[idx],
                    "u": np.asarray(wind_data["u"], dtype=np.float32)[idx],
                    "v": np.asarray(wind_data["v"], dtype=np.float32)[idx],
                    "qi": None,
                }
            }
            if "sat" in wind_data:
                payload["ASCAT"]["sat"] = np.asarray(wind_data["sat"])[idx]
            if "pass" in wind_data:
                payload["ASCAT"]["pass"] = np.asarray(wind_data["pass"], dtype=np.int32)[idx]
            dts = [p.get("dt") for p in (info.get("passes") or []) if p.get("dt")]
            self.main_ui._ascat_wind_dt = max(dts) if dts else self._parse_info_dt(info)
            self._publish_wind_data(payload)
            self.main_ui.log(
                f"[ASCAT] Loaded {int(np.ceil(n / step))} real-time swath wind "
                f"points into viewport"
            )
            return True
        except Exception as e:
            self.main_ui.log(f"[ASCAT] swath load into view failed: {e}")
            return False

    def _refresh_pass_combo(self, info):
        """Sync the main window's ASCAT pass dropdown with the loaded passes."""
        try:
            passes = (info or {}).get("passes") or []
            numbered = [p for p in passes if p.get("pass_no") is not None]
            fn = getattr(self.main_ui, "_populate_ascat_passes", None)
            if fn is not None:
                fn(numbered)
        except Exception as e:
            self.main_ui.log(f"[ASCAT] pass dropdown refresh failed: {e}")

    def _publish_wind_data(self, wind_data):
        """Push wind point data through the app's AMV overlay pipeline."""
        mw = self.main_ui
        oc = getattr(mw, "overlay_controller", None)
        for ch in list(wind_data):
            if isinstance(wind_data[ch], dict):
                wind_data[ch]["source"] = "ascat"
        for target in (mw, oc):
            if target is None:
                continue
            if hasattr(target, "current_winds_uv"):
                target.current_winds_uv = wind_data
            if hasattr(target, "winds_enabled"):
                target.winds_enabled = True
        # Previous AMV projections are cached per-datetime/geometry; a
        # fresh ASCAT field must not reuse stale projected pixels.
        if oc is not None:
            try:
                oc._wind_proj_cache = {}
            except Exception:
                pass
        try:
            mw._wind_proj_cache.clear()
        except Exception:
            pass
        # Force the "Show Winds (AMV)" checkbox on so it stays visible and
        # the wind barbs are toggled on; skip _on_winds_loaded, whose
        # _update_winds_checkbox_state() would disable it (it checks for
        # wind_*-prefixed vars in the CURRENT imagery NC file).
        cb = getattr(mw, "overlay_checkboxes", {}).get("Show Winds (AMV)") if isinstance(
            getattr(mw, "overlay_checkboxes", None), dict) else None
        if cb is not None:
            cb.blockSignals(True)
            cb.setEnabled(True)
            cb.setChecked(True)
            cb.blockSignals(False)
        if hasattr(mw, "_draw_winds_overlay"):
            # ASCAT is a MICROWAVE surface-wind product — it observes
            # through clouds. Override amv_on_clouds just for this draw so
            # the IR cloud mask (cold pixels < 235K) doesn't strip the
            # storm-basin winds. Restore afterwards.
            _prev = None
            _settings = getattr(mw, "settings", None)
            if _settings is not None:
                _prev = _settings.get("amv_on_clouds")
                _setter = getattr(_settings, "set", None)
                if _setter is not None:
                    _setter("amv_on_clouds", True)
                else:
                    try:
                        _settings["amv_on_clouds"] = True
                    except TypeError:
                        pass
            try:
                mw._winds_persist = False
                mw._draw_winds_overlay()
            except Exception as e:
                self.main_ui.log(f"[ASCAT] wind overlay draw: {e}")
            finally:
                if _settings is not None and _prev is not None:
                    if _setter is not None:
                        _setter("amv_on_clouds", _prev)
                    else:
                        try:
                            _settings["amv_on_clouds"] = _prev
                        except TypeError:
                            pass

    def has_available_ascat(self):
        """True when ASCAT wind data already exists locally or in memory.

        Kept cheap so the "Show Winds (AMV)" checkbox refresh can call it on
        every scene/overlay update without stalling the UI.
        """
        mw = self.main_ui
        if mw is not None and bool(getattr(mw, "current_winds_uv", None)):
            return True
        if self._last_nc_info is not None:
            return True
        if self._local_swath_files:
            return True
        try:
            from pathlib import Path
            from ..clients.ascat import get_ascat_data_dir
            d = Path(get_ascat_data_dir())
            if d.exists():
                for p in d.iterdir():
                    name = p.name.lower()
                    if name.endswith(".nc") or name.endswith(".nc.gz") or name.endswith(".gz"):
                        return True
        except Exception:
            pass
        return False

    def reload_cached_into_view(self):
        """Re-publish the last ASCAT wind field (swath or gridded product)."""
        info = self._last_nc_info
        if not isinstance(info, dict):
            return False
        try:
            if info.get("wind_data"):
                return self._load_swath_into_view(info)
            if info.get("file_path") and info.get("u_var") and info.get("v_var"):
                return self._load_into_view(info)
        except Exception as e:
            self.main_ui.log(f"[ASCAT] cached reload failed: {e}")
        return False

    def _load_into_view(self, info):
        """Read the downloaded NC, convert the grid to wind points and feed
        them into the app's wind-overlay pipeline (channel 'ASCAT')."""
        try:
            import numpy as np
            import xarray as xr
            path = info.get("file_path")
            if not path:
                return False
            with xr.open_dataset(path) as ds:
                u_var = info.get("u_var")
                v_var = info.get("v_var")
                if u_var not in ds or v_var not in ds:
                    return False
                lat = np.asarray(ds["latitude"].values, dtype=np.float64)
                lon = np.asarray(ds["longitude"].values, dtype=np.float64)
                u = ds[u_var].values.squeeze().astype(np.float32)
                v = ds[v_var].values.squeeze().astype(np.float32)
            if u.ndim != 2:
                return False
            # Flatten grid -> point observations, dropping masked cells.
            lon2d, lat2d = np.meshgrid(lon, lat)
            u_flat = u.ravel()
            v_flat = v.ravel()
            ok = np.isfinite(u_flat) & np.isfinite(v_flat) & (u_flat != 0) & (v_flat != 0)
            if not np.any(ok):
                return False
            # Subsample to the density target so the viewport renderer can
            # saturate dim (max-density) settings such as Intensive.
            n = int(ok.sum())
            step = max(1, int(np.ceil(n / max(1, _wind_density_target(self.main_ui)))))
            idx = np.flatnonzero(ok)[::step]
            wind_data = {
                "ASCAT": {
                    "lat": lat2d.ravel()[idx].astype(np.float64),
                    "lon": lon2d.ravel()[idx].astype(np.float64),
                    "u": u_flat[idx].astype(np.float32),
                    "v": v_flat[idx].astype(np.float32),
                    "qi": None,
                }
            }
            self.main_ui._ascat_wind_dt = self._parse_info_dt(info)
            self._publish_wind_data(wind_data)
            self.main_ui.log(f"[ASCAT] Loaded {len(idx)} ASCAT wind points into viewport")
            return True
        except Exception as e:
            self.main_ui.log(f"[ASCAT] load into view failed: {e}")
            return False

    def _compute_age_warning(self, info, storm_latest_utc=None):
        """Compare the ASCAT data time against the loaded imagery time.

        ``storm_latest_utc`` (optional) is the newest real ASCAT pass time
        scraped from the Manati product listings; when present it is used as
        the authoritative "data time" since the downloadable grid may be a
        coarser analysis product.
        """
        try:
            from datetime import datetime, timezone
            t_str = info.get("time_str", "")
            if not t_str:
                return ""
            asc_dt = None
            try:
                asc_dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
            except ValueError:
                pass
            if asc_dt is None:
                return ""
            if not asc_dt.tzinfo:
                asc_dt = asc_dt.replace(tzinfo=timezone.utc)
            cur = getattr(self.main_ui, "current_datetime", None)
            if not cur:
                return ""
            cur_dt = None
            try:
                import re as _re
                m = _re.fullmatch(r"(\d{4})_(\d{2})_(\d{2})_(\d{2})(\d{2})", str(cur))
                if m:
                    cur_dt = datetime(*[int(g) for g in m.groups()], tzinfo=timezone.utc)
            except Exception:
                pass
            if cur_dt is None:
                return ""
            # Use the genuine scraped ASCAT pass if it is newer than the grid.
            ref_dt = asc_dt
            if storm_latest_utc is not None:
                try:
                    if storm_latest_utc.tzinfo is None:
                        storm_latest_utc = storm_latest_utc.replace(tzinfo=timezone.utc)
                    if storm_latest_utc > ref_dt:
                        ref_dt = storm_latest_utc
                except Exception:
                    pass
            delta = (cur_dt - ref_dt).total_seconds() / 3600.0
            label = "Latest ASCAT pass" if (ref_dt != asc_dt) else "ASCAT data"
            if delta > 6:
                return (f"{label} ({ref_dt:%Y-%m-%d %H:%M}Z) is {delta:.0f} hours "
                        f"OLDER than the loaded imagery ({cur_dt:%Y-%m-%d %H:%M}Z).")
            if delta < -6:
                return (f"{label} ({ref_dt:%Y-%m-%d %H:%M}Z) is NEWER than the "
                        f"loaded imagery ({cur_dt:%Y-%m-%d %H:%M}Z).")
            return ""
        except Exception:
            return ""
