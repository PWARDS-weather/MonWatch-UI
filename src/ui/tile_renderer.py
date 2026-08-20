# =============================================================================
# MonWatch-UI Cyclone V3 - Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: ui/tile_renderer.py
# Description: Tile-based level-of-detail rendering for the "gridded"
#              preview quality, adapted from the approach used by uwsift
#              (view/tile_calculator.py + view/visuals.py) and customized for
#              MonWatch's PySide6/QGraphicsView pipeline.
#
# Concepts (mirroring uwsift):
#   - Stride (LOD) selection: how many source pixels cover one screen pixel.
#     Zoomed out -> big stride (coarse subsample); zoomed in -> stride 1
#     (full available resolution). The coarsest level is the "overview"
#     stride, sized so the whole image fits inside a single tile.
#   - Visible tiles only: only the (iy, ix) tile indices that intersect the
#     current viewport are ever materialised.
#   - Strided source reads: each tile reads a strided slice of the source
#     array, so only the chunk covering the viewport is touched instead of
#     rebuilding/resampling the whole image.
#   - LRU tile cache: a fixed-size tile cache keeps memory flat; when the
#     cache fills, the least-recently-used texture is evicted.
#
# Principal Developer: Zero (Prince Al Zhanjie B. Dela Rosa)
# Affiliation: PWARDS-weather
# Copyright (C) 2025-2026 PWARDS-weather
# =============================================================================

import math
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QGraphicsPixmapItem

# Render each tile as a 256x256 texture (matches uwsift default tile size).
DEFAULT_TILE_SIZE = 256
# Preferred number of source pixels per screen pixel. Higher = sharper but
# more expensive; 2.0 is uwsift's default.
PREFERRED_SCREEN_TO_TEXTURE_RATIO = 2.0
# Fixed number of textured tiles kept in memory (the "texture atlas" slots).
DEFAULT_ATLAS_CAPACITY = 64


class TileCalculator:
    """Pure logic for stride/LOD selection and visible-tile bookkeeping.

    The source image is addressed in source-pixel coordinates; the graphics
    view is configured so one scene unit equals one source pixel, keeping the
    math self-contained and independent of the widget's zoom transform.
    """

    def __init__(self, image_shape, tile_shape=(DEFAULT_TILE_SIZE, DEFAULT_TILE_SIZE)):
        self.image_shape = (int(image_shape[0]), int(image_shape[1]))
        self.tile_shape = (int(tile_shape[0]), int(tile_shape[1]))
        self.overview_stride = self._calc_overview_stride()

    def _calc_overview_stride(self):
        """Smallest (coarsest-safe) stride so the whole image fits in one tile."""
        sy = max(1, int(math.floor(self.image_shape[0] / self.tile_shape[0])))
        sx = max(1, int(math.floor(self.image_shape[1] / self.tile_shape[1])))
        return max(sy, sx)

    def calc_stride(self, src_pixels_per_screen):
        """Pick a conservative stride given source pixels per screen pixel.

        Mirrors uwsift's ``calc_stride``: gather at least
        PREFERRED_SCREEN_TO_TEXTURE_RATIO source pixels per rendered pixel so
        we never undersample below the useful level, and never exceed the
        overview stride while zoomed all the way out.
        """
        raw = src_pixels_per_screen * PREFERRED_SCREEN_TO_TEXTURE_RATIO
        return min(self.overview_stride, max(1, int(math.ceil(raw))))

    def visible_tiles(self, stride, view_rect):
        """Return [(iy, ix), ...] tile indices intersecting ``view_rect``.

        ``view_rect`` is a QRectF in source-pixel scene coordinates (origin
        top-left). Tile (iy, ix) covers source rows/cols
        [i * tile_side * stride, (i + 1) * tile_side * stride).
        """
        h, w = self.image_shape
        ts = self.tile_shape[0] * stride
        if ts <= 0:
            return []
        ncols = max(1, int(math.ceil(w / ts)))
        nrows = max(1, int(math.ceil(h / ts)))

        x0 = view_rect.left()
        y0 = view_rect.top()
        x1 = view_rect.right()
        y1 = view_rect.bottom()

        ix0 = max(0, int(math.floor(x0 / ts)))
        iy0 = max(0, int(math.floor(y0 / ts)))
        ix1 = min(ncols - 1, int(math.ceil(x1 / ts)) - 1)
        iy1 = min(nrows - 1, int(math.ceil(y1 / ts)) - 1)

        if ix1 < ix0 or iy1 < iy0 or ix0 >= ncols or iy0 >= nrows:
            return []
        return [(iy, ix) for iy in range(iy0, iy1 + 1) for ix in range(ix0, ix1 + 1)]

    def tile_rect(self, iy, ix, stride):
        """Source-pixel rectangle (x0, y0, x1, y1) covered by tile (iy, ix)."""
        ts = self.tile_shape[0] * stride
        h, w = self.image_shape
        x0 = ix * ts
        y0 = iy * ts
        x1 = min(x0 + ts, w)
        y1 = min(y0 + ts, h)
        return (x0, y0, x1, y1)


class LruTileAtlas:
    """Fixed-capacity LRU cache of rendered tiles, keyed by (stride, iy, ix)."""

    def __init__(self, capacity):
        self.capacity = max(1, int(capacity))
        self._cache = OrderedDict()

    def get(self, key):
        value = self._cache.get(key)
        if value is not None:
            self._cache.move_to_end(key)
        return value

    def put(self, key, value):
        if key in self._cache:
            self._cache.move_to_end(key)
            return None
        self._cache[key] = value
        evicted_key = None
        if len(self._cache) > self.capacity:
            evicted_key, _ = self._cache.popitem(last=False)
        return evicted_key

    def clear(self):
        self._cache.clear()

    def __len__(self):
        return len(self._cache)


class GridTileLayer(QObject):
    """Renders a base BGRA/RGBA source as zoom-appropriate visible tiles.

    The layer owns the scene placement of the tiles: each tile becomes a
    ``QGraphicsPixmapItem`` scaled so its scene footprint equals the source
    pixels it covers. Only tiles intersecting the current viewport exist at
    any moment, and the whole set is rebuilt whenever the LOD stride changes.
    """

    def __init__(self, graphics_view, log_func=None,
                 tile_size=DEFAULT_TILE_SIZE, atlas_capacity=DEFAULT_ATLAS_CAPACITY):
        super().__init__()
        self.view = graphics_view
        self.log = log_func or (lambda msg: None)
        self.tile_shape = (int(tile_size), int(tile_size))
        self.atlas = LruTileAtlas(atlas_capacity)
        self.calc = None
        self._source = None
        self._source_h = 0
        self._source_w = 0
        self._items = {}
        self._built_stride = None
        self._origin_x = 0.0
        self._origin_y = 0.0

    # -- source management -------------------------------------------------

    def set_source(self, rgba, scene_origin=None):
        """Adopt a full-resolution (W, H, 3|4) uint8 array as the source."""
        self.clear()
        if rgba is None:
            return
        arr = np.asarray(rgba)
        if arr.ndim != 3 or arr.shape[2] < 3:
            self.log("Grid tile source must be (H, W, 3|4) -- ignored.")
            return
        arr = np.ascontiguousarray(arr)
        if arr.shape[2] == 3:
            alpha = np.full(arr.shape[:2] + (1,), 255, dtype=np.uint8)
            arr = np.concatenate([arr, alpha], axis=-1)
        self._source = arr
        self._source_h, self._source_w = arr.shape[:2]
        self.calc = TileCalculator((self._source_h, self._source_w), self.tile_shape)
        if scene_origin is None:
            scene_origin = (0.0, 0.0)
        self._origin_x = float(scene_origin[0])
        self._origin_y = float(scene_origin[1])

    @property
    def active(self):
        return self._source is not None

    def source_shape(self):
        if not self.active:
            return None
        return (self._source_h, self._source_w)

    def full_scene_rect(self):
        """QRectF of the whole source in scene (source-pixel) coordinates."""
        if not self.active:
            return None
        return QRectF(self._origin_x, self._origin_y, float(self._source_w), float(self._source_h))

    def clear(self):
        scene = self.view.scene() if self.view is not None else None
        if scene is not None:
            for item in self._items.values():
                try:
                    if item.scene():
                        scene.removeItem(item)
                except RuntimeError:
                    pass
        self._items.clear()
        self._source = None
        self._source_h = 0
        self._source_w = 0
        self.calc = None
        self._built_stride = None
        self.atlas.clear()

    # -- the actual tile refresh --------------------------------------------

    def refresh(self):
        """Recompute stride + visible tiles from the current view transform."""
        if not self.active:
            return
        vp = self.view.viewport()
        if vp is None:
            return
        try:
            view_rect = self.view.mapToScene(vp.rect())
            zoom = self.view.transform().m11()
        except Exception:
            return
        if zoom is None or zoom == 0:
            return
        src_px_per_screen = abs(1.0 / zoom)
        stride = self.calc.calc_stride(src_px_per_screen)
        visible = self.view.mapToScene(vp.rect())
        view_rect = visible.boundingRect() if hasattr(visible, 'boundingRect') else visible
        self._update_tiles(stride, view_rect)

    def _update_tiles(self, stride, view_rect):
        scene = self.view.scene()
        if scene is None:
            return

        if stride != self._built_stride:
            for item in self._items.values():
                try:
                    if item.scene():
                        scene.removeItem(item)
                except RuntimeError:
                    pass
            self._items.clear()
            self._built_stride = stride

        tiles = self.calc.visible_tiles(stride, view_rect)
        new_keys = set()
        ts = self.tile_shape[0] * stride
        for (iy, ix) in tiles:
            key = (self._built_stride, iy, ix)
            new_keys.add(key)
            if key in self._items:
                continue
            pixmap = self.atlas.get(key)
            if pixmap is None:
                pixmap = self._render_tile(iy, ix, stride)
                if pixmap is None:
                    continue
                self.atlas.put(key, pixmap)
            x0, y0, _, _ = self.calc.tile_rect(iy, ix, stride)
            item = QGraphicsPixmapItem(pixmap)
            item.setPos(QPointF(self._origin_x + x0, self._origin_y + y0))
            item.setScale(stride)
            item.setZValue(0)
            item.setData(0, key)
            item.setFlag(QGraphicsPixmapItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)
            scene.addItem(item)
            self._items[key] = item

        stale = [k for k in self._items if k not in new_keys]
        for key in stale:
            item = self._items.pop(key)
            try:
                if item.scene():
                    scene.removeItem(item)
            except RuntimeError:
                pass

    def _render_tile(self, iy, ix, stride):
        """Strided read of the tile's source chunk -> QPixmap.

        Mirrors uwsift's ``_slice_texture_tile``: only the strided slice over
        the viewport chunk is materialised, never the whole array.
        """
        try:
            x0, y0, x1, y1 = self.calc.tile_rect(iy, ix, stride)
            block = self._source[y0:y1:stride, x0:x1:stride]
            if block is None or block.size == 0:
                return None
            bh, bw = block.shape[:2]
            qimg = QImage(block.tobytes(), int(bw), int(bh), int(bw) * 4, QImage.Format_RGBA8888)
            if qimg.isNull():
                return None
            return QPixmap.fromImage(qimg)
        except Exception as exc:
            self.log(f"Grid tile render failed ({iy},{ix},{stride}): {exc}")
            return None

    def set_alpha_hint(self, message):
        """Convenience hook so callers can surface tile-mode status."""
        self._alpha_hint = message