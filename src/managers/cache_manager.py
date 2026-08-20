# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: managers/cache_manager.py
# Description: Runtime cache management for satellite band data and processed imagery.
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


class RuntimeCacheManager:
    """Three-level LRU cache with running byte counters and amortized eviction.

    Tiers:
      precached  – RGBA uint8 ndarrays (ready for display)
      raw        – float32 band ndarrays (from NetCDF)
      rgb        – QImage composited products

    Eviction is deferred (once per ENFORCE_EVERY puts) to keep puts O(1) amortized.
    Running byte counters make size checks O(1).
    """

    ENFORCE_EVERY = 5

    def __init__(self, settings=None, log_func=None):
        self.precached = OrderedDict()
        self.raw = OrderedDict()
        self.rgb = OrderedDict()

        self._precached_bytes = 0
        self._raw_bytes = 0
        self._rgb_bytes = 0

        self._put_count = 0
        self._pressure_hit = False
        self.settings = settings
        self.log = log_func or (lambda msg: None)

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _byte_size(obj):
        if hasattr(obj, 'nbytes'):
            return obj.nbytes
        if hasattr(obj, 'byteCount'):
            return obj.byteCount()
        return 0

    def total_bytes(self):
        return self._precached_bytes + self._raw_bytes + self._rgb_bytes

    def count_raw(self):
        return len(self.raw)

    # -- precached --------------------------------------------------------

    def get_precached(self, band):
        arr = self.precached.get(band)
        if arr is not None:
            self.precached.move_to_end(band)
        return arr

    def put_precached(self, band, arr):
        old_sz = self._byte_size(self.precached.pop(band, None))
        self.precached[band] = arr
        self._precached_bytes += self._byte_size(arr) - old_sz
        self._maybe_enforce()

    # -- raw --------------------------------------------------------------

    def get_raw(self, band):
        arr = self.raw.get(band)
        if arr is not None:
            self.raw.move_to_end(band)
        return arr

    def put_raw(self, band, arr):
        old_sz = self._byte_size(self.raw.pop(band, None))
        self.raw[band] = arr
        self._raw_bytes += self._byte_size(arr) - old_sz
        self._maybe_enforce()

    # -- rgb --------------------------------------------------------------

    def get_rgb(self, key):
        qi = self.rgb.get(key)
        if qi is not None:
            self.rgb.move_to_end(key)
        return qi

    def put_rgb(self, key, qimage):
        old_sz = self._byte_size(self.rgb.pop(key, None))
        self.rgb[key] = qimage
        self._rgb_bytes += self._byte_size(qimage) - old_sz
        self._maybe_enforce()

    # -- bulk operations --------------------------------------------------

    def clear_all(self):
        self.precached.clear()
        self.raw.clear()
        self.rgb.clear()
        self._precached_bytes = 0
        self._raw_bytes = 0
        self._rgb_bytes = 0
        self.log("All runtime caches cleared")

    def clear_raw(self):
        self.raw.clear()
        self._raw_bytes = 0

    def clear_rgb(self):
        self.rgb.clear()
        self._rgb_bytes = 0

    def clear_precached(self):
        self.precached.clear()
        self._precached_bytes = 0

    # -- amortised eviction -----------------------------------------------

    def _maybe_enforce(self):
        self._put_count += 1
        over_limit = self.total_bytes() > self._get_max_bytes()
        if over_limit or self._put_count % self.ENFORCE_EVERY == 0:
            self.enforce_size_limit()

    def _get_max_bytes(self):
        max_mb = self.settings.get("cache_size_mb", 1000) if self.settings else 1000
        return max_mb * 1024 * 1024

    def enforce_size_limit(self):
        max_bytes = self._get_max_bytes()
        force_full = self.settings.get("force_full_cache", False) if self.settings else False
        if force_full:
            return

        total = self.total_bytes()
        if total <= max_bytes:
            return

        self.log(f"⚠ RAM LIMIT EXCEEDED: {total/1024/1024:.0f}MB / {max_bytes/1024/1024:.0f}MB — Increase cache_size_mb in settings if you have more RAM, otherwise performance will be bottlenecked")

        # Eviction order: oldest first (OrderedDict maintains insertion order,
        # which matches LRU because we move_to_end on access).
        for d, counter_name in [
            (self.precached, '_precached_bytes'),
            (self.raw, '_raw_bytes'),
            (self.rgb, '_rgb_bytes'),
        ]:
            while d and total > max_bytes:
                _key, val = d.popitem(last=False)
                sz = self._byte_size(val)
                total -= sz
                setattr(self, counter_name, getattr(self, counter_name) - sz)

        self._pressure_hit = True

        if total > max_bytes:
            self.clear_all()
            self.log("Emergency: cleared ALL caches to respect limit")
