import os
import json
import logging
import tempfile
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QObject, QTimer, Signal

from ..clients.pwards_client import (
    fetch_manifest, download_band_file,
    PWARDS_AHI_TO_SATAID, PWARDS_BAND_ORDER,
)

log = logging.getLogger(__name__)


class PwardsStreamManager(QObject):
    bands_ready = Signal(dict)
    status_message = Signal(str)
    poll_error = Signal(str)
    connected = Signal(bool)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.settings = settings
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)

        self._latest_time = None
        self._latest_date = None
        self._known_times = set()
        self._band_files = {}
        self._active_slot = None

        self._download_dir = Path(tempfile.gettempdir()) / "pwards_stream"
        self._download_dir.mkdir(parents=True, exist_ok=True)

        self._base_url = ""
        self._api_code = ""
        self._poll_interval_ms = 60000
        self._enabled = False

    def configure(self, base_url="", api_code="", poll_interval_sec=60):
        self._base_url = base_url
        self._api_code = api_code
        self._poll_interval_ms = max(10000, poll_interval_sec * 1000)

    def start(self):
        if not self._base_url or not self._api_code:
            self.status_message.emit("PWARDS API not configured")
            return
        self._enabled = True
        self._poll()
        self._poll_timer.start(self._poll_interval_ms)
        self.status_message.emit(f"PWARDS stream polling every {self._poll_interval_ms//1000}s")

    def stop(self):
        self._enabled = False
        self._poll_timer.stop()
        self._latest_time = None
        self._latest_date = None
        self._band_files.clear()
        self._active_slot = None
        self.status_message.emit("PWARDS stream stopped")

    def poll_now(self):
        self._poll()

    def get_band_file(self, sataid_band: str) -> Path | None:
        return self._band_files.get(sataid_band)

    def get_available_bands(self) -> list:
        return list(self._band_files.keys())

    def get_active_slot_info(self) -> dict | None:
        return self._active_slot

    def _poll(self):
        if not self._enabled:
            return
        result = fetch_manifest(self._base_url, self._api_code)
        if result is None:
            self.connected.emit(False)
            self.poll_error.emit("PWARDS manifest fetch failed")
            return
        self.connected.emit(True)

        latest_time = result.get("latest_time", "")
        latest_date = result.get("latest_date", "")
        available = result.get("available", [])

        if (latest_time, latest_date) == (self._latest_time, self._latest_date):
            return

        self._latest_time = latest_time
        self._latest_date = latest_date
        self.status_message.emit(
            f"PWARDS: new slot {latest_date} {latest_time}"
        )

        slot = None
        for s in available:
            if s.get("time") == latest_time and s.get("date") == latest_date:
                slot = s
                break
        if slot is None and available:
            slot = available[0]

        if slot is None:
            return

        self._active_slot = {
            "time": slot.get("time"),
            "date": slot.get("date"),
        }

        bands = slot.get("bands", {})
        if not bands:
            return

        new_files = {}
        def _download_band(ahi_band, path):
            data = download_band_file(self._base_url, self._api_code, path)
            if data is None:
                return ahi_band, None
            sataid_band = PWARDS_AHI_TO_SATAID.get(ahi_band, ahi_band)
            ext = Path(path).suffix if "." in path else ".Z"
            fname = f"{sataid_band}_{latest_date}.Z{latest_time}{ext}"
            dest = self._download_dir / fname
            try:
                dest.write_bytes(data)
                return ahi_band, (sataid_band, dest)
            except OSError as e:
                log.error(f"Failed to write {dest}: {e}")
                return ahi_band, None

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {
                pool.submit(_download_band, ahi_band, path): ahi_band
                for ahi_band, path in bands.items()
                if ahi_band in PWARDS_AHI_TO_SATAID
            }
            for fut in as_completed(futures):
                ahi_band, result = fut.result()
                if result is not None:
                    sataid_band, fpath = result
                    new_files[sataid_band] = fpath

        if new_files:
            self._band_files = new_files
            self.bands_ready.emit(self._band_files)
            self.status_message.emit(
                f"PWARDS: {len(new_files)} band(s) ready for {latest_date} {latest_time}"
            )
