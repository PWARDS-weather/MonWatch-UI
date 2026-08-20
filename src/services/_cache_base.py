import time
from collections import OrderedDict

import numpy as np
from PySide6.QtGui import QImage


class ProjectionCache:
    """Per-projection LRU cache for resampled band data.

    Three tiers per band key:
      raw     – float32 array (calibrated, reprojected)
      rgba    – uint8 RGBA array (display-ready)
      qimage  – QImage (GPU-ready)

    Keys are band names (str) or composite names (str).
    """

    ENFORCE_EVERY = 5

    def __init__(self, name, max_mb=512, log_func=None):
        self.name = name
        self.max_mb = max_mb
        self.log = log_func or (lambda msg: None)

        self.raw = OrderedDict()
        self.rgba = OrderedDict()
        self.qimage = OrderedDict()

        self._raw_bytes = 0
        self._rgba_bytes = 0
        self._qimage_bytes = 0
        self._put_count = 0

    # -- public getters / setters ------------------------------------------

    def get_raw(self, key):
        arr = self.raw.get(key)
        if arr is not None:
            self.raw.move_to_end(key)
        return arr

    def put_raw(self, key, arr):
        old = self.raw.pop(key, None)
        self.raw[key] = arr
        self._raw_bytes += self._nbytes(arr) - self._nbytes(old)
        self._maybe_enforce()

    def get_rgba(self, key):
        arr = self.rgba.get(key)
        if arr is not None:
            self.rgba.move_to_end(key)
        return arr

    def put_rgba(self, key, arr):
        old = self.rgba.pop(key, None)
        self.rgba[key] = arr
        self._rgba_bytes += self._nbytes(arr) - self._nbytes(old)
        self._maybe_enforce()

    def get_qimage(self, key):
        qi = self.qimage.get(key)
        if qi is not None:
            self.qimage.move_to_end(key)
        return qi

    def put_qimage(self, key, qi):
        old = self.qimage.pop(key, None)
        self.qimage[key] = qi
        self._qimage_bytes += self._nbytes(qi) - self._nbytes(old)
        self._maybe_enforce()

    def has(self, key):
        return key in self.raw or key in self.rgba or key in self.qimage

    def has_raw(self, key):
        return key in self.raw

    def remove(self, key):
        for d, counter in [
            (self.raw, '_raw_bytes'),
            (self.rgba, '_rgba_bytes'),
            (self.qimage, '_qimage_bytes'),
        ]:
            arr = d.pop(key, None)
            if arr is not None:
                setattr(self, counter, getattr(self, counter) - self._nbytes(arr))

    def clear(self):
        self.raw.clear()
        self.rgba.clear()
        self.qimage.clear()
        self._raw_bytes = 0
        self._rgba_bytes = 0
        self._qimage_bytes = 0

    def total_bytes(self):
        return self._raw_bytes + self._rgba_bytes + self._qimage_bytes

    def count(self):
        return len(self.raw) + len(self.rgba) + len(self.qimage)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _nbytes(obj):
        if obj is None:
            return 0
        if hasattr(obj, 'nbytes'):
            return obj.nbytes
        if hasattr(obj, 'byteCount'):
            return obj.byteCount()
        if isinstance(obj, QImage):
            return obj.width() * obj.height() * 4
        return 0

    def _maybe_enforce(self):
        self._put_count += 1
        over = self.total_bytes() > self.max_mb * 1024 * 1024
        if over or self._put_count % self.ENFORCE_EVERY == 0:
            self._enforce()

    def _enforce(self):
        limit = self.max_mb * 1024 * 1024
        total = self.total_bytes()
        if total <= limit:
            return
        for d, counter in [
            (self.qimage, '_qimage_bytes'),
            (self.rgba, '_rgba_bytes'),
            (self.raw, '_raw_bytes'),
        ]:
            while d and total > limit:
                k, v = d.popitem(last=False)
                sz = self._nbytes(v)
                total -= sz
                setattr(self, counter, getattr(self, counter) - sz)
