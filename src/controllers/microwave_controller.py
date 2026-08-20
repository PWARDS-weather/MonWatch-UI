# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: controllers/microwave_controller.py
# Description: Controller for passive-microwave data discovery. Runs the
#              microwave client (Manati AMSR2 + NODD JPSS) on background
#              QThreads and feeds the results into the microwave window.
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


from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QGraphicsPixmapItem

from ..core.helpers import normalize_lon


# Labels and per-source overlay checkbox keys (the renderer is a single shared
# TB channel, so the source picks which toggle the UI shows as active).
_MW_LABELS = {
    "atms": "ATMS 88.2 GHz",
    "mimic_tc2": "MIMIC-TC2 89 GHz",
    "viirs_i5": "VIIRS I5 11.45 um",
    "amsr2_raw": "AMSR2 L1B 89 GHz",
}
_MW_CHECKBOX_KEYS = {
    "atms": "Show Microwave",
    "mimic_tc2": "Show Microwave",
    "viirs_i5": "Show Microwave",
    "amsr2_raw": "Show Microwave",
}


class MicrowaveController(QObject):
    """Coordinates one-click microwave data discovery + metadata window."""

    def __init__(self, main_ui):
        parent = main_ui if isinstance(main_ui, QObject) else None
        super().__init__(parent)
        self.main_ui = main_ui
        self.window = None
        self._thread = None
        self._worker = None
        self._busy = False
        self._amsr2_busy = False
        self._amsr2_thread = None
        self._amsr2_worker = None
        self._atms_busy = False
        self._atms_thread = None
        self._atms_worker = None
        self._mimic_busy = False
        self._mimic_thread = None
        self._mimic_worker = None
        self._viirs_busy = False
        self._viirs_thread = None
        self._viirs_worker = None
        self._amsr2raw_busy = False
        self._amsr2raw_thread = None
        self._amsr2raw_worker = None
        self._last_result = None
        self._last_atms_info = None
        self._last_mimic_info = None
        self._last_viirs_info = None
        self._last_amsr2raw_info = None

    # ------------------------------------------------------------------ UI
    def open_microwave_window(self):
        if self.window is not None and self.window.isVisible():
            self.window.raise_()
            self.window.activateWindow()
            self.window._populate_region_storms()
            return
        from ..ui.microwave_window import MicrowaveWindow
        self.window = MicrowaveWindow(self.main_ui, parent=None)
        self.window.refresh_requested.connect(self.fetch_latest)
        self.window.amsr2_requested.connect(self.download_amsr2)
        self.window.atms_requested.connect(self.download_atms)
        self.window.mimic_requested.connect(self.download_mimic)
        self.window.viirs_requested.connect(self.download_viirs)
        self.window.amsr2raw_requested.connect(self.download_amsr2_raw)
        self.window.show()
        self.window._populate_region_storms()
        if self._last_result:
            self.window.set_data(self._last_result)
        self.fetch_latest()

    def on_atcf_updated(self):
        """Refill the microwave focus-storm dropdown after ATCF data loads."""
        if self.window is None:
            return
        try:
            self.window._populate_region_storms()
        except Exception as e:
            self.main_ui.log(f"[MICROWAVE] storm dropdown refresh failed: {e}")

    def close_window(self):
        if self.window is not None:
            self.window.close()
            self.window = None

    # -------------------------------------------------------------- fetch
    def fetch_latest(self):
        if self._busy:
            return
        self._busy = True
        if self.window is not None:
            self.window.set_status("Fetching latest microwave data...")

        from ..clients.microwave import MicrowaveDownloader
        worker = MicrowaveDownloader()
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
        self.main_ui.log(f"[MICROWAVE] {msg}")
        if self.window is not None:
            self.window.set_status(msg)

    def _on_result(self, data):
        self._busy = False
        self._last_result = data
        if self.window is not None:
            self.window.set_data(data)
        self.main_ui.log(
            f"[MICROWAVE] Latest data fetched: {len(data.get('storms', []))} "
            f"AMSR2 storm product(s), {len(data.get('products', []))} source(s)."
        )

    def _on_error(self, msg):
        self._busy = False
        self.main_ui.log(f"[MICROWAVE] error: {msg}")
        if self.window is not None:
            self.window.set_status(f"Error: {msg}")

    # ------------------------------------------------------- AMSR2 download
    def download_amsr2(self, storm):
        """Download the latest AMSR2 brightness-temperature pass images."""
        if getattr(self, "_amsr2_busy", False):
            return
        self._amsr2_busy = True
        if self.window is not None:
            self.window.set_download_status(
                "Downloading latest AMSR2 pass images...", "busy")

        from ..clients.microwave import AMSR2PassDownloader
        worker = AMSR2PassDownloader(storm=storm)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_amsr2_progress)
        worker.result.connect(self._on_amsr2_result)
        worker.error.connect(self._on_amsr2_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._amsr2_thread = thread
        self._amsr2_worker = worker
        thread.start()

    def _on_amsr2_progress(self, msg):
        self.main_ui.log(f"[MICROWAVE] {msg}")
        if self.window is not None:
            self.window.set_download_status(msg, "busy")

    def _on_amsr2_result(self, info):
        self._amsr2_busy = False
        self.main_ui.log(
            f"[MICROWAVE] AMSR2 pass downloaded: {info.get('file_path')} "
            f"({info.get('time_str', '?')}, {', '.join(info.get('channels') or [])})"
        )
        if self.window is not None:
            self.window.set_amsr2_result(info)

    def _on_amsr2_error(self, msg):
        self._amsr2_busy = False
        self.main_ui.log(f"[MICROWAVE] AMSR2 download error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"AMSR2 download failed: {msg}", "error")

    # ------------------------------------------------------- ATMS download
    def download_atms(self, storm):
        """Find + download the NODD ATMS 88.2 GHz overpass over a position."""
        if getattr(self, "_atms_busy", False):
            return
        lat, lon = self._storm_latlon(storm)
        if lat is None or lon is None:
            msg = ("No position available — pick an active storm in the "
                   "focus-storm box first.")
            self.main_ui.log(f"[MICROWAVE] {msg}")
            if self.window is not None:
                self.window.set_download_status(msg, "error")
            return
        self._atms_busy = True
        if self.window is not None:
            self.window.set_download_status(
                "Searching NODD JPSS for an ATMS 88.2 GHz overpass...", "busy")

        from ..clients.microwave import ATMSOverpassDownloader
        worker = ATMSOverpassDownloader(lat=lat, lon=lon)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_atms_progress)
        worker.result.connect(self._on_atms_result)
        worker.error.connect(self._on_atms_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._atms_thread = thread
        self._atms_worker = worker
        thread.start()

    def _storm_latlon(self, storm):
        """Best-effort (lat, lon) for a storm dict or the viewport centre.

        The viewport fallback mirrors the cursor-readout maths in UI.py
        (scale the displayed pixmap pixel to the native grid, run it through
        the geotransform, then reproject to EPSG:4326).
        """
        if isinstance(storm, dict):
            lat = storm.get("current_lat")
            lon = storm.get("current_lon")
            if lat is not None and lon is not None:
                try:
                    return float(lat), float(lon)
                except Exception:
                    pass
        mw = self.main_ui
        gv = getattr(mw, "graphics_view", None)
        if gv is None or gv.scene() is None:
            return None, None
        try:
            pixmap_item = None
            for item in reversed(gv.scene().items()):
                if isinstance(item, QGraphicsPixmapItem):
                    pixmap_item = item
                    break
            if pixmap_item is None:
                return None, None
            img_w = pixmap_item.pixmap().width()
            img_h = pixmap_item.pixmap().height()
            if img_w <= 0 or img_h <= 0:
                return None, None
            scene_pt = gv.mapToScene(gv.viewport().rect().center())
            px = scene_pt.x() - pixmap_item.pos().x()
            py = scene_pt.y() - pixmap_item.pos().y()
            if px < 0 or py < 0 or px > img_w or py > img_h:
                px, py = img_w / 2.0, img_h / 2.0

            track_crs = getattr(mw, "_anim_track_crs", None)
            track_gt = getattr(mw, "_anim_track_gt", None)
            if track_crs is not None and track_gt is not None:
                crs = track_crs
                transform = track_gt
            else:
                crs = mw.current_crs
                transform = mw.current_geotransform
            if crs is None or transform is None:
                return None, None

            native_res_m = abs(transform.a)
            native_extent = abs(transform.c)
            native_grid_w = int(round(2 * native_extent / native_res_m))
            scale = native_grid_w / img_w if img_w > 0 else 1.0
            x_native = px * scale
            y_native = py * scale
            x_proj, y_proj = transform * (x_native, y_native)
            from pyproj import Transformer
            transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
            lon, lat = transformer.transform(x_proj, y_proj)
            if lon is None or lat is None:
                return None, None
            return float(lat), float(normalize_lon(lon))
        except Exception:
            return None, None

    def _on_atms_progress(self, msg):
        self.main_ui.log(f"[MICROWAVE] {msg}")
        if self.window is not None:
            self.window.set_download_status(msg, "busy")

    def _on_atms_result(self, info):
        self._atms_busy = False
        self._last_atms_info = info
        self.main_ui.log(
            f"[MICROWAVE] ATMS overpass: {info.get('satellite', '?')} "
            f"{info.get('time_str', '?')}, "
            f"{info.get('coverage_points', 0)} 88.2 GHz samples within 150 km."
        )
        loaded = self._load_tb_into_view(info)
        payload = {**info, "loaded": loaded, "warning": ""}
        if self.window is not None:
            self.window.set_atms_result(payload)

    def _on_atms_error(self, msg):
        self._atms_busy = False
        self.main_ui.log(f"[MICROWAVE] ATMS download error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"ATMS download failed: {msg}", "error")

    # ------------------------------------------------------ MIMIC-TC2 download
    def download_mimic(self, storm=None):
        """Find + download the MIMIC-TC2 89 GHz field over a position."""
        if getattr(self, "_mimic_busy", False):
            return
        lat, lon = self._storm_latlon(storm)
        if lat is None or lon is None:
            self.main_ui.log("[MICROWAVE] MIMIC-TC2: no position available.")
            return
        self._mimic_busy = True
        if self.window is not None:
            self.window.set_download_status(
                "Fetching CIMSS MIMIC-TC2 89 GHz field...", "busy")

        from ..clients.microwave import MIMICDownloader
        worker = MIMICDownloader(lat=lat, lon=lon)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_mimic_progress)
        worker.result.connect(self._on_mimic_result)
        worker.error.connect(self._on_mimic_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._mimic_thread = thread
        self._mimic_worker = worker
        thread.start()

    def _on_mimic_progress(self, msg):
        self.main_ui.log(f"[MICROWAVE] {msg}")
        if self.window is not None:
            self.window.set_download_status(msg, "busy")

    def _on_mimic_result(self, info):
        self._mimic_busy = False
        self._last_mimic_info = info
        self.main_ui.log(
            f"[MICROWAVE] MIMIC-TC2 field: {info.get('storm', '?')} "
            f"{info.get('time_str', '?')}, "
            f"{info.get('coverage_points', 0)} 89 GHz samples.")
        loaded = self._load_tb_into_view(info)
        payload = {**info, "loaded": loaded, "warning": ""}
        if self.window is not None:
            self.window.set_mimic_result(payload)

    def _on_mimic_error(self, msg):
        self._mimic_busy = False
        self.main_ui.log(f"[MICROWAVE] MIMIC-TC2 error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"MIMIC-TC2 failed: {msg}", "error")

    # ----------------------------------------------------- VIIRS I5 download
    def download_viirs(self, storm=None):
        """Find + download the VIIRS I5 (11.45 um) overpass over a position.

        Uses the stored NASA Earthdata account (NASA LANCE NRT archive) when
        one is configured, falling back to NODD JPSS."""
        if getattr(self, "_viirs_busy", False):
            return
        lat, lon = self._storm_latlon(storm)
        if lat is None or lon is None:
            self.main_ui.log("[MICROWAVE] VIIRS I5: no position available.")
            return
        from ..clients.podaac_swath import find_earthdata_account
        account = find_earthdata_account(self.main_ui.settings)
        self._viirs_busy = True
        if self.window is not None:
            if account is not None:
                self.window.set_download_status(
                    "Searching NASA Earthdata (LANCE NRT) for a VIIRS I5 "
                    "11.45 um overpass...", "busy")
            else:
                self.window.set_download_status(
                    "Searching NODD JPSS for a VIIRS I5 11.45 um overpass...",
                    "busy")

        from ..clients.microwave import VIIRSI5Downloader
        worker = VIIRSI5Downloader(lat=lat, lon=lon, account=account)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_viirs_progress)
        worker.result.connect(self._on_viirs_result)
        worker.error.connect(self._on_viirs_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._viirs_thread = thread
        self._viirs_worker = worker
        thread.start()

    def _on_viirs_progress(self, msg):
        self.main_ui.log(f"[MICROWAVE] {msg}")
        if self.window is not None:
            self.window.set_download_status(msg, "busy")

    def _on_viirs_result(self, info):
        self._viirs_busy = False
        self._last_viirs_info = info
        self.main_ui.log(
            f"[MICROWAVE] VIIRS I5 overpass: {info.get('satellite', '?')} "
            f"{info.get('time_str', '?')}, "
            f"{info.get('coverage_points', 0)} 11.45 um samples within 150 km.")
        loaded = self._load_tb_into_view(info)
        payload = {**info, "loaded": loaded, "warning": ""}
        if self.window is not None:
            self.window.set_viirs_result(payload)

    def _on_viirs_error(self, msg):
        self._viirs_busy = False
        self.main_ui.log(f"[MICROWAVE] VIIRS I5 error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"VIIRS I5 failed: {msg}", "error")

    # ------------------------------------------------------ AMSR2 raw download
    def download_amsr2_raw(self, storm=None):
        """Find + download the OSPO raw GCOM-W1 AMSR2 L1B 89 GHz granule."""
        if getattr(self, "_amsr2raw_busy", False):
            return
        lat, lon = self._storm_latlon(storm)
        if lat is None or lon is None:
            self.main_ui.log("[MICROWAVE] AMSR2 L1B: no position available.")
            return
        self._amsr2raw_busy = True
        if self.window is not None:
            self.window.set_download_status(
                "Searching NOAA OSPO for a raw AMSR2 L1B 89 GHz granule...",
                "busy")

        from ..clients.microwave import AMSR2RawDownloader
        worker = AMSR2RawDownloader(lat=lat, lon=lon)
        thread = QThread()
        worker.moveToThread(thread)
        worker.progress.connect(self._on_amsr2raw_progress)
        worker.result.connect(self._on_amsr2raw_result)
        worker.error.connect(self._on_amsr2raw_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.started.connect(worker.run)
        self._amsr2raw_thread = thread
        self._amsr2raw_worker = worker
        thread.start()

    def _on_amsr2raw_progress(self, msg):
        self.main_ui.log(f"[MICROWAVE] {msg}")
        if self.window is not None:
            self.window.set_download_status(msg, "busy")

    def _on_amsr2raw_result(self, info):
        self._amsr2raw_busy = False
        self._last_amsr2raw_info = info
        self.main_ui.log(
            f"[MICROWAVE] AMSR2 L1B: {info.get('sdr_key', '?')} "
            f"{info.get('time_str', '?')}, "
            f"{info.get('coverage_points', 0)} raw 89 GHz samples.")
        loaded = self._load_tb_into_view(info)
        payload = {**info, "loaded": loaded, "warning": ""}
        if self.window is not None:
            self.window.set_amsr2raw_result(payload)

    def _on_amsr2raw_error(self, msg):
        self._amsr2raw_busy = False
        self.main_ui.log(f"[MICROWAVE] AMSR2 L1B error: {msg}")
        if self.window is not None:
            self.window.set_download_status(f"AMSR2 L1B failed: {msg}", "error")

    # ------------------------------------------------- overlay (like ASCAT)
    def reload_cached_atms_into_view(self):
        """Re-publish the last ATMS TB swath into the viewport overlay."""
        info = self._last_atms_info
        if not isinstance(info, dict):
            return False
        try:
            return self._load_tb_into_view(info)
        except Exception as e:
            self.main_ui.log(f"[MICROWAVE] cached overlay reload failed: {e}")
            return False

    def reload_cached_mimic_into_view(self):
        """Re-publish the last MIMIC-TC2 89 GHz field into the viewport."""
        info = self._last_mimic_info
        if not isinstance(info, dict):
            return False
        try:
            return self._load_tb_into_view(info)
        except Exception as e:
            self.main_ui.log(f"[MICROWAVE] cached MIMIC overlay reload failed: {e}")
            return False

    def reload_cached_viirs_into_view(self):
        """Re-publish the last VIIRS I5 11.45 um swath into the viewport."""
        info = self._last_viirs_info
        if not isinstance(info, dict):
            return False
        try:
            return self._load_tb_into_view(info)
        except Exception as e:
            self.main_ui.log(f"[MICROWAVE] cached VIIRS overlay reload failed: {e}")
            return False

    def reload_cached_amsr2raw_into_view(self):
        """Re-publish the last raw AMSR2 L1B 89 GHz swath into the viewport."""
        info = self._last_amsr2raw_info
        if not isinstance(info, dict):
            return False
        try:
            return self._load_tb_into_view(info)
        except Exception as e:
            self.main_ui.log(f"[MICROWAVE] cached AMSR2 overlay reload failed: {e}")
            return False

    def _load_tb_into_view(self, info):
        """Publish microwave brightness-temperature points (ATMS 88.2 GHz or
        MIMIC-TC2 89 GHz) into the overlay pipeline (scalar channel 'MW88')."""
        import numpy as np
        lat = info.get("swath_lat")
        lon = info.get("swath_lon")
        tb = info.get("swath_tb")
        source = info.get("source", "atms")
        label = _MW_LABELS.get(source, "ATMS 88.2 GHz")
        if lat is None or lon is None or tb is None or len(lat) == 0:
            return False
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        tb = np.asarray(tb, dtype=np.float64)
        ok = np.isfinite(lat) & np.isfinite(lon) & np.isfinite(tb)
        lat, lon, tb = lat[ok], lon[ok], tb[ok]
        # Cap point count; the overlay raster bakes once so a thick swath
        # stays light. Keep at least as many points as a busy overlay wants.
        MAX_POINTS = 40000
        if lat.size > MAX_POINTS:
            step = max(1, int(np.ceil(lat.size / MAX_POINTS)))
            lat, lon, tb = lat[::step], lon[::step], tb[::step]
        wind_data = {
            "MW88": {
                "lat": lat.astype(np.float64),
                "lon": lon.astype(np.float64),
                "tb": tb.astype(np.float64),
                "source": source,
            }
        }
        self._publish_tb_data(wind_data)
        self.main_ui.log(
            f"[MICROWAVE] Loaded {int(lat.size)} {label} TB points "
            f"into viewport")
        return True

    def _publish_tb_data(self, wind_data):
        """Push microwave TB point data through the overlay pipeline."""
        mw = self.main_ui
        oc = getattr(mw, "overlay_controller", None)
        # The renderer reads a single channel dict {lat, lon, tb}; take the
        # first channel of the payload.
        channel = wind_data.get("MW88")
        if channel is None:
            channel = next(iter(wind_data.values()), wind_data)
        src = channel.get("source") if isinstance(channel, dict) else None
        mw = self.main_ui
        oc = getattr(mw, "overlay_controller", None)
        src_combo = getattr(mw, "mw_src_combo", None)
        if src_combo is not None:
            try:
                src_combo.blockSignals(True)
                idx = src_combo.findData(src if src in ("atms", "mimic_tc2", "viirs_i5", "amsr2_raw") else None)
                if idx >= 0:
                    src_combo.setCurrentIndex(idx)
                src_combo.blockSignals(False)
            except Exception:
                pass
        cb_key = _MW_CHECKBOX_KEYS.get(
            src, "Show Microwave")
        for target in (mw, oc):
            if target is None:
                continue
            if hasattr(target, "microwave_tb_data"):
                target.microwave_tb_data = channel
            if hasattr(target, "microwave_enabled"):
                target.microwave_enabled = True
        cb = getattr(mw, "overlay_checkboxes", {}) \
            if isinstance(getattr(mw, "overlay_checkboxes", None), dict) else {}
        mw_cb = cb.get(cb_key)
        if mw_cb is not None:
            mw_cb.blockSignals(True)
            mw_cb.setEnabled(True)
            mw_cb.setChecked(True)
            mw_cb.blockSignals(False)
        if hasattr(mw, "_draw_microwave_overlay"):
            try:
                mw._mw_persist = False
                mw._draw_microwave_overlay()
            except Exception as e:
                self.main_ui.log(f"[MICROWAVE] overlay draw: {e}")

    # -------------------------------------------------------------- cache
    def remove_microwave_cache(self):
        """Delete downloaded microwave files and clear any TB overlay.

        Returns the list removed.
        """
        from ..clients.microwave import remove_microwave_cache as _rm
        try:
            removed = _rm()
        except Exception as e:
            self.main_ui.log(f"[MICROWAVE] cache removal failed: {e}")
            return []
        # Clear the in-memory TB overlay so stale dots disappear.
        mw = self.main_ui
        oc = getattr(mw, "overlay_controller", None)
        for target in (mw, oc):
            if target is None:
                continue
            for attr in ("microwave_tb_data", "microwave_enabled", "_mw_persist"):
                if hasattr(target, attr):
                    try:
                        setattr(target, attr, None if attr.startswith("microwave_tb") else False)
                    except Exception:
                        pass
        if oc is not None and hasattr(oc, "clear_microwave_overlay"):
            try:
                oc.clear_microwave_overlay()
            except Exception as e:
                self.main_ui.log(f"[MICROWAVE] overlay clear: {e}")
        try:
            cb = getattr(mw, "overlay_checkboxes", {}) or {}
            mw_cb = cb.get("Show Microwave")
            if mw_cb is not None:
                mw_cb.blockSignals(True)
                mw_cb.setChecked(False)
                mw_cb.blockSignals(False)
        except Exception:
            pass
        self._last_atms_info = None
        self._last_mimic_info = None
        self._last_viirs_info = None
        self._last_amsr2raw_info = None
        self.main_ui.log(
            f"[MICROWAVE] Microwave cache cleared: {len(removed)} file(s) removed")
        return removed

    def has_available_microwave(self):
        """True when microwave files exist locally or a TB overlay is loaded."""
        if bool(getattr(self.main_ui, "microwave_tb_data", None)):
            return True
        if self._last_atms_info is not None:
            return True
        if self._last_mimic_info is not None:
            return True
        if self._last_viirs_info is not None:
            return True
        if self._last_amsr2raw_info is not None:
            return True
        try:
            from pathlib import Path
            from ..clients.microwave import get_microwave_data_dir
            d = Path(get_microwave_data_dir())
            if d.exists():
                for p in d.iterdir():
                    if p.is_file():
                        return True
        except Exception:
            pass
        return False
