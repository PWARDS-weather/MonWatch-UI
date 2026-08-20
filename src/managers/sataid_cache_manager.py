# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: managers/sataid_cache_manager.py
# Description: Satellite ID file cache management for metadata tracking.
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

import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot, QEventLoop
from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QProgressBar,
    QVBoxLayout,
)

from ..parsers.sataid_reader import read_sataid_cached


# -------------------------------------------------------------------- #
#  Modal progress dialog  (unchanged API)
# -------------------------------------------------------------------- #
class _SataidCachingDialog(QDialog):
    log_received = Signal(str)

    def __init__(self, parent=None, title="SATAID Caching",
                 main_text="CACHING SATAID BANDS — PLEASE WAIT"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumSize(460, 320)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        self.title_label = QLabel(main_text)
        self.title_label.setStyleSheet("font-weight:bold; color:#FFF; font-size:12px;")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setMinimumHeight(22)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("Initializing...")
        self.status_label.setStyleSheet("color:#DDD; font-size:11px;")
        self.status_label.setWordWrap(False)
        self.status_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.status_label.setMinimumWidth(420)
        self.status_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(self.status_label, 1)
        self._last_text = ""
        self._last_pct = -1

    @Slot(str)
    def on_log(self, msg):
        self.log_received.emit(msg)

    def update_progress(self, text, pct):
        if text != self._last_text:
            self.status_label.setText(text)
            self._last_text = text
        if pct != self._last_pct:
            self.progress_bar.setValue(pct)
            self._last_pct = pct
        self.status_label.repaint()
        self.progress_bar.repaint()
        QApplication.processEvents(QEventLoop.AllEvents, 30)

    def set_final(self, text):
        self.status_label.setText(text)
        self.progress_bar.setValue(100)
        self.status_label.repaint()
        self.progress_bar.repaint()
        QApplication.processEvents(QEventLoop.AllEvents, 30)


# -------------------------------------------------------------------- #
#  Parallel worker  (decompresses N files at once via ThreadPoolExecutor)
# -------------------------------------------------------------------- #
class SataidCacheWorker(QObject):
    progress = Signal(str, int)
    log_message = Signal(str)
    file_cached = Signal(str)
    finished = Signal()

    def __init__(self, files, cache_manager):
        super().__init__()
        self.files = files
        self.cache = cache_manager
        self._is_cancelled = False

    def run(self):
        total = len(self.files)
        if total == 0:
            self.finished.emit()
            return

        self.log_message.emit(
            f"SATAID cache: caching {total} band(s) with parallel decompression")

        max_workers = max(1, min(8, total))
        done = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            fut_map = {}
            for f in self.files:
                if self._is_cancelled:
                    break
                self.progress.emit(f"{f.name} Decompressing", 0)
                fut = pool.submit(self._load_one, f, f.name)
                fut_map[fut] = f

            for fut in as_completed(fut_map):
                if self._is_cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                f = fut_map[fut]
                done += 1
                try:
                    f_name, sat = fut.result()
                    self.progress.emit(f"{f_name} Caching", int((done / total) * 100))
                    self.cache.put(f_name, sat)
                    self.file_cached.emit(f_name)
                    self.log_message.emit(
                        f"SATAID cache: cached {f_name} ({sat.data.shape[1]}x{sat.data.shape[0]})")
                except Exception as e:
                    self.log_message.emit(f"SATAID cache: FAILED {f.name}: {e}")

        self.progress.emit(f"Caching complete ({self.cache.count()} band(s)).", 100)
        self.log_message.emit(
            f"SATAID cache: {self.cache.count()} band(s) in memory")
        self.finished.emit()

    def _load_one(self, f, f_name):
        self.progress.emit(f"{f_name} Decompressing...", 0)
        sat = read_sataid_cached(f)
        self.progress.emit(f"{f_name} Caching...", 50)
        return f_name, sat

    def cancel(self):
        self._is_cancelled = True


# -------------------------------------------------------------------- #
#  SataidCacheManager with LRU eviction and size limit
# -------------------------------------------------------------------- #
class SataidCacheManager:
    """In-memory SATAID band cache with LRU eviction and byte-based limit.

    Uses OrderedDict (insertion-order = access order via move_to_end).
    Default limit: 2 GB raw data.
    """

    def __init__(self, max_bytes: int = 2 * 1024 * 1024 * 1024):
        self._cache = OrderedDict()
        self._max_bytes = max_bytes
        self._current_bytes = 0

    # -- public API ------------------------------------------------------

    def get(self, filename: str):
        sat = self._cache.get(filename)
        if sat is not None:
            self._cache.move_to_end(filename)
        return sat

    def put(self, filename: str, sat):
        if filename in self._cache:
            self._current_bytes -= self._sat_bytes(self._cache[filename])
            self._cache[filename] = sat
            self._cache.move_to_end(filename)
        else:
            self._cache[filename] = sat
        self._current_bytes += self._sat_bytes(sat)
        self._evict_if_needed()

    def is_cached(self, filename: str) -> bool:
        return filename in self._cache

    def count(self) -> int:
        return len(self._cache)

    def clear(self):
        self._cache.clear()
        self._current_bytes = 0

    # -- eviction --------------------------------------------------------

    def _evict_if_needed(self):
        while self._current_bytes > self._max_bytes and self._cache:
            _key, sat = self._cache.popitem(last=False)
            self._current_bytes -= self._sat_bytes(sat)

    @staticmethod
    def _sat_bytes(sat) -> int:
        try:
            return sat.data.nbytes
        except Exception:
            return 0

    # -- background batch caching (with dialog) --------------------------

    def cache_all(self, files: list, parent=None, log_func=None, on_finished=None):
        todo = [f for f in files if f.name not in self._cache]
        if not todo:
            if on_finished:
                on_finished()
            return

        dialog = _SataidCachingDialog(parent)
        dialog.show()

        if log_func:
            log_func(f"SATAID cache: caching {len(todo)} band(s) in background")

        self._thread = QThread()
        self._worker = SataidCacheWorker(todo, self)
        self._worker.moveToThread(self._thread)
        self._worker.progress.connect(dialog.update_progress)
        self._worker.log_message.connect(dialog.on_log)
        if log_func:
            dialog.log_received.connect(log_func)
        self._worker.finished.connect(dialog.close)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        if on_finished:
            self._worker.finished.connect(on_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

    def cancel(self):
        if hasattr(self, '_worker') and self._worker is not None:
            self._worker.cancel()
