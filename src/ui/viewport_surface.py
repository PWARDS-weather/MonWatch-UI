# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Module: ui/viewport_surface.py
# Description: Reusable display surface shared by MultiViewportWindow and the
#              new MultiPanel (2x2) view. Contains the simple image viewer,
#              the forecast viewer and the stacked page widget (viewport mirror,
#              bands, animation, forecast, 3d globe) with overlay handling.
# =============================================================================

import numpy as np
import threading

from PySide6.QtCore import Qt, QTimer, QRectF, QSize, Signal
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QBrush, QFont, QImage,
    QMouseEvent, QTransform, QPen, QPainterPath
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QStackedWidget, QGraphicsView, QGraphicsScene, QSizePolicy,
    QPushButton, QGraphicsPixmapItem, QFrame, QDialog, QDialogButtonBox,
    QApplication, QMessageBox, QComboBox
)

from .Globe3DWidget import Globe3DWidget


class SimpleImageView(QWidget):
    """A simple widget that displays a QPixmap centered and scaled to fit.

    Supports zoom / pan plus a normalized view state that can be shared
    between panels implementing the McIDAS-V "Share Views" concept.
    """

    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = None
        self._zoom = 1.0
        self._offset = [0, 0]
        self._dragging = False
        self._drag_start = None
        self._offset_start = None
        self.setMinimumSize(120, 90)
        self.setMouseTracking(False)
        self.setCursor(Qt.OpenHandCursor)
        self.setStyleSheet("background: #000;")

    def set_pixmap(self, pixmap, keep_view=False):
        if self._pixmap and pixmap and not pixmap.isNull():
            same_size = (self._pixmap.size() == pixmap.size())
        else:
            same_size = False
        old_view = None
        if keep_view and self._pixmap is not None and not self._pixmap.isNull() \
                and pixmap is not None and not pixmap.isNull():
            old_view = self.view_state()
        self._pixmap = pixmap
        if not same_size:
            self._zoom = 1.0
            self._offset = [0, 0]
        if old_view is not None:
            self.apply_view_state(old_view, emit=False)
        self.update()

    def pixmap(self):
        return self._pixmap

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self._pixmap and not self._pixmap.isNull():
            pw = self._pixmap.width()
            ph = self._pixmap.height()
            if pw > 0 and ph > 0:
                vw = self.width()
                vh = self.height()
                scale = min(vw / pw, vh / ph) * self._zoom
                sw = pw * scale
                sh = ph * scale
                x = (vw - sw) / 2 + self._offset[0]
                y = (vh - sh) / 2 + self._offset[1]
                painter.drawPixmap(QRectF(x, y, sw, sh), self._pixmap,
                                   QRectF(0, 0, pw, ph))
        painter.end()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom *= 1.15
        else:
            self._zoom *= 0.85
        self._zoom = max(0.1, min(30.0, self._zoom))
        self.update()
        self.viewChanged.emit()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._drag_start = event.pos()
            self._offset_start = list(self._offset)
            self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._drag_start = None
            self.setCursor(Qt.OpenHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging and self._drag_start is not None:
            dx = event.pos().x() - self._drag_start.x()
            dy = event.pos().y() - self._drag_start.y()
            self._offset = [self._offset_start[0] + dx,
                            self._offset_start[1] + dy]
            self.update()
            self.viewChanged.emit()

    # -- Normalized view state (for shared views between panels) -------------

    def view_state(self):
        """Return (zoom, cx_frac, cy_frac) describing the visible region.

        cx_frac/cy_frac are the visible centre expressed as a fraction of the
        full image pixel width/height, which lets panels with different
        widget sizes share an equivalent view of the same-domain image.
        """
        if not self._pixmap or self._pixmap.isNull():
            return (self._zoom, 0.5, 0.5)
        pw = self._pixmap.width()
        ph = self._pixmap.height()
        vw = max(1, self.width())
        vh = max(1, self.height())
        scale = min(vw / pw, vh / ph) * self._zoom
        sw = pw * scale
        sh = ph * scale
        x = (vw - sw) / 2 + self._offset[0]
        y = (vh - sh) / 2 + self._offset[1]
        cx = (vw / 2 - x) / pw
        cy = (vh / 2 - y) / ph
        return (self._zoom, cx, cy)

    def apply_view_state(self, state, emit=True):
        if not state:
            return
        zoom = float(state[0])
        cx = float(state[1])
        cy = float(state[2])
        self._zoom = max(0.1, min(30.0, zoom))
        if self._pixmap and not self._pixmap.isNull():
            pw = self._pixmap.width()
            ph = self._pixmap.height()
            vw = max(1, self.width())
            vh = max(1, self.height())
            scale = min(vw / pw, vh / ph) * self._zoom
            sw = pw * scale
            sh = ph * scale
            x = vw / 2 - cx * pw
            y = vh / 2 - cy * ph
            self._offset = [x - (vw - sw) / 2, y - (vh - sh) / 2]
        else:
            self._zoom = zoom
        self.update()
        if emit:
            self.viewChanged.emit()


class MirrorView(QGraphicsView):
    """Interactive view of the shared main scene for the 'viewport' page.

    Supports its own wheel zoom and click-drag panning, so a panel placed in
    the main viewport is directly navigable. Emits viewChanged when the view
    moves, which drives the shared-views (Sync) coordinator; unchecking Sync
    leaves the panel fully independent.
    """

    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False
        self._last = None
        self._user_navigated = False
        self.setInteractive(True)
        self.setBackgroundBrush(QBrush(QColor("#000000")))
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setCursor(Qt.OpenHandCursor)

    # -- Navigation -----------------------------------------------------------

    def _fit_scale(self):
        sr = self.scene().sceneRect() if self.scene() is not None else QRectF()
        vp = self.viewport().rect()
        if sr.width() <= 0 or vp.width() <= 0:
            return 1.0
        return vp.width() / sr.width()

    def fit_in_scene(self):
        sc = self.scene()
        if sc is None:
            return
        sr = sc.sceneRect()
        if sr.width() > 0 and sr.height() > 0:
            self.fitInView(sr, Qt.KeepAspectRatio)

    def wheelEvent(self, event):
        self._user_navigated = True
        delta = event.angleDelta().y()
        if delta != 0:
            factor = 1.15 if delta > 0 else 1.0 / 1.15
            self.scale(factor, factor)
            self.viewChanged.emit()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._user_navigated = True
            self._last = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and self._last is not None:
            dx = event.pos().x() - self._last.x()
            dy = event.pos().y() - self._last.y()
            self._last = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - dx)
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - dy)
            self.viewChanged.emit()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)

    # -- Normalized view state (shared views) ----------------------------------

    def view_state(self):
        sc = self.scene()
        vp = self.viewport().rect()
        if sc is None or sc.sceneRect().width() <= 0 or vp.width() <= 0:
            return (1.0, 0.5, 0.5)
        fit = self._fit_scale()
        s = self.transform().m11()
        magnif = (s / fit) if fit else 1.0
        sr = sc.sceneRect()
        tl = self.mapToScene(vp.topLeft())
        br = self.mapToScene(vp.bottomRight())
        cx = ((tl.x() + br.x()) / 2 - sr.x()) / sr.width()
        cy = ((tl.y() + br.y()) / 2 - sr.y()) / sr.height()
        return (max(0.05, min(50.0, magnif)), cx, cy)

    def apply_view_state(self, state, emit=True):
        if not state:
            return
        sc = self.scene()
        vp = self.viewport().rect()
        if sc is None or sc.sceneRect().width() <= 0 or vp.width() <= 0:
            return
        magnif = max(0.05, min(50.0, float(state[0])))
        cx = float(state[1])
        cy = float(state[2])
        sr = sc.sceneRect()
        fit = self._fit_scale()
        self.resetTransform()
        self.scale(fit * magnif, fit * magnif)
        self.centerOn(sr.x() + cx * sr.width(), sr.y() + cy * sr.height())
        if emit:
            self.viewChanged.emit()


class ForecastView(QWidget):
    """Fixed 1:1 display of a forecast pixmap with no zoom/pan."""

    def __init__(self, parent=None, resize_on_set=True):
        super().__init__(parent)
        self._pixmap = None
        self._resize_on_set = resize_on_set
        self.setMinimumSize(100, 100)
        self.setStyleSheet("background: #000;")

    def set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()
        if self._resize_on_set and pixmap and not pixmap.isNull():
            win = self.window()
            if win:
                margin_w = win.frameSize().width() - win.width()
                margin_h = win.frameSize().height() - win.height()
                win.resize(pixmap.width() + margin_w, pixmap.height() + margin_h)

    def sizeHint(self):
        if self._pixmap and not self._pixmap.isNull():
            return self._pixmap.size()
        return QSize(200, 150)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self._pixmap and not self._pixmap.isNull():
            pw = self._pixmap.width()
            ph = self._pixmap.height()
            x = (self.width() - pw) / 2
            y = (self.height() - ph) / 2
            painter.drawPixmap(int(x), int(y), self._pixmap)
        painter.end()


class ViewportSurfaceWidget(QWidget):
    """Reusable display surface of a viewport / panel.

    Page types (mode): viewport (mirror of the main scene), bands, animation,
    forecast, 3d globe. Owns overlay rendering for band/composite images and
    pushes textures to the embedded 3D globe.
    """

    def __init__(self, app_ref, index, parent=None, panel_context=False):
        super().__init__(parent)
        self._app = app_ref
        self.index = index
        self.panel_context = panel_context
        self._anim_pixmap = None
        self._band_pixmap = None
        self._composite_pixmap = None
        self._overlay_image = None
        self._overlay_enabled = True
        self._grid_enabled = True
        self._coast_enabled = True
        self._overlay_dirty = True
        self._mode = "bands" if panel_context else "viewport"
        self._current_band = None
        self._current_product = None
        self._domain_locked = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self._pages = {}
        self._create_pages()

    def _create_pages(self):
        # Page 0: viewport mirror — only for plain windows. Panels are fully
        # independent (a 4-way split of the imagery) and must NOT share the
        # main scene, otherwise a product change in the main viewport would
        # repaint every floating panel regardless of Sync.
        if not self.panel_context:
            p0 = QWidget()
            lo0 = QVBoxLayout(p0)
            lo0.setContentsMargins(0, 0, 0, 0)
            main_view = getattr(self._app, 'graphics_view', None)
            if main_view is not None:
                self._mirror_view = MirrorView()
                scene = main_view.scene()
                if scene:
                    self._mirror_view.setScene(scene)
                self._mirror_timer = QTimer(self)
                self._mirror_timer.setInterval(33)
                self._mirror_timer.timeout.connect(self._sync_mirror)
                self._mirror_timer.start()
                lo0.addWidget(self._mirror_view)
            else:
                lo0.addWidget(QLabel("No main viewport", alignment=Qt.AlignCenter))
            self.stack.addWidget(p0)
            self._pages["viewport"] = p0

        # Page 1: bands
        p1 = QWidget()
        lo1 = QVBoxLayout(p1)
        lo1.setContentsMargins(4, 4, 4, 4)
        self._bands_view = SimpleImageView()
        lo1.addWidget(self._bands_view, 1)
        self.stack.addWidget(p1)
        self._pages["bands"] = p1

        # Page 2: animation
        p2 = QWidget()
        lo2 = QVBoxLayout(p2)
        lo2.setContentsMargins(0, 0, 0, 0)
        self._anim_view = SimpleImageView()
        lo2.addWidget(self._anim_view, 1)
        self.stack.addWidget(p2)
        self._pages["animation"] = p2

        # Page 3: forecast
        p3 = QWidget()
        lo3 = QVBoxLayout(p3)
        lo3.setContentsMargins(4, 4, 4, 4)
        self._forecast_view = ForecastView(resize_on_set=not self.panel_context)
        lo3.addWidget(self._forecast_view, 1)
        self.stack.addWidget(p3)
        self._pages["forecast"] = p3

        # Page 4: 3d globe
        p4 = QWidget()
        lo4 = QVBoxLayout(p4)
        lo4.setContentsMargins(0, 0, 0, 0)
        self._globe = Globe3DWidget()
        lo4.addWidget(self._globe, 1)
        self.stack.addWidget(p4)
        self._pages["3d globe"] = p4

    def _sync_mirror(self):
        mirror = getattr(self, '_mirror_view', None)
        main_view = getattr(self._app, 'graphics_view', None)
        if mirror is None or main_view is None:
            return
        if getattr(mirror, '_user_navigated', False):
            return
        try:
            mp = getattr(self._app, '_mp_manager', None)
            if mp is not None and mp.placement == "main":
                # The panels ARE the display: fit the shared scene once; from
                # there each panel navigates on its own.
                if not getattr(self, '_mirror_main_fitted', False):
                    mirror.fit_in_scene()
                    self._mirror_main_fitted = True
                return
            # Floating / standalone window: mirror the real main viewport until
            # the user navigates this view directly.
            mirror.setTransform(main_view.transform())
            vp = main_view.viewport()
            if vp:
                center = main_view.mapToScene(vp.rect().center())
                mirror.centerOn(center)
        except RuntimeError:
            pass

    # -- Configuration --------------------------------------------------------

    def update_config(self, mode=None, band=None, product=None,
                      overlays_enabled=None, grid_enabled=None,
                      coast_enabled=None, force=False):
        changed = False
        if mode is not None and mode != self._mode:
            self._mode = mode
            if mode in self._pages:
                self.stack.setCurrentWidget(self._pages[mode])
            if mode == "3d globe" and self._globe is not None:
                if self._globe._ready:
                    self.push_texture_to_globe()
                else:
                    self._globe.ready.connect(
                        lambda: self.push_texture_to_globe(), Qt.QueuedConnection)
            changed = True
        if band is not None and (force or band != self._current_band):
            self._current_band = band
            self._current_product = None
            if hasattr(self._app, '_request_multi_band'):
                if self.panel_context:
                    self._app._request_multi_band(self.index, band, panel=True)
                else:
                    self._app._request_multi_band(self.index, band)
            changed = True
        if product is not None and (force or product != self._current_product):
            self._current_product = product
            self._current_band = None
            if hasattr(self._app, '_request_multi_product'):
                if self.panel_context:
                    self._app._request_multi_product(self.index, product, panel=True)
                else:
                    self._app._request_multi_product(self.index, product)
            changed = True
        if overlays_enabled is not None:
            self._overlay_enabled = overlays_enabled
            if self._overlay_enabled and self._band_pixmap is not None:
                self._apply_overlays()
            elif not self._overlay_enabled:
                self._clear_overlays()
            changed = True
        if grid_enabled is not None:
            self._grid_enabled = grid_enabled
            self._overlay_dirty = True
            if self._overlay_enabled and self._band_pixmap is not None:
                self._apply_overlays()
            changed = True
        if coast_enabled is not None:
            self._coast_enabled = coast_enabled
            self._overlay_dirty = True
            if self._overlay_enabled and self._band_pixmap is not None:
                self._apply_overlays()
            changed = True
        return changed

    # -- Pixmap setters ---------------------------------------------------------

    def set_band_pixmap(self, pixmap):
        self._band_pixmap = pixmap
        self._composite_pixmap = pixmap
        keep = self._domain_locked
        self._domain_locked = True
        if self._overlay_enabled:
            try:
                self._apply_overlays(keep_view=keep)
            except Exception as e:
                print(f"[VP{self.index}] Overlay error, showing raw band: {e}")
                self._bands_view.set_pixmap(pixmap, keep_view=keep)
        else:
            self._bands_view.set_pixmap(pixmap, keep_view=keep)

    def set_snapshot_pixmap(self, pixmap):
        """Display a raw scene snapshot (already contains overlays) as-is.

        Used to seed panels from the current main viewport so a freshly
        enabled MultiPanel shows the imagery right away. No overlay pass is
        run because the snapshot already includes what the main scene paints.
        """
        if pixmap is None or pixmap.isNull():
            return
        self._domain_locked = False
        self._band_pixmap = pixmap
        self._composite_pixmap = None
        self._bands_view.set_pixmap(pixmap)

    def set_composite_pixmap(self, pixmap):
        self._composite_pixmap = pixmap
        self._band_pixmap = None
        keep = self._domain_locked
        self._domain_locked = True
        if self._overlay_enabled:
            try:
                self._apply_overlays(keep_view=keep)
            except Exception as e:
                print(f"[VP{self.index}] Overlay error, showing raw composite: {e}")
                self._bands_view.set_pixmap(pixmap, keep_view=keep)
        else:
            self._bands_view.set_pixmap(pixmap, keep_view=keep)

    def set_animation_pixmap(self, pixmap):
        self._anim_view.set_pixmap(pixmap)

    def set_forecast_pixmap(self, pixmap):
        self._forecast_view.set_pixmap(pixmap)

    # -- Overlays ---------------------------------------------------------------

    def _clear_overlays(self):
        self._overlay_image = None
        if self._composite_pixmap is not None:
            self._bands_view.set_pixmap(self._composite_pixmap)

    def _apply_overlays(self, keep_view=False):
        base = self._composite_pixmap
        if base is None or base.isNull():
            return
        overlay = self._render_overlay_image(base.width(), base.height())
        if overlay is None:
            self._bands_view.set_pixmap(base, keep_view=keep_view)
            return
        img = base.toImage().convertToFormat(QImage.Format_ARGB32_Premultiplied)
        painter = QPainter(img)
        painter.drawImage(0, 0, overlay)
        painter.end()
        result = QPixmap.fromImage(img)
        self._bands_view.set_pixmap(result, keep_view=keep_view)

    def _render_overlay_image(self, w, h):
        app = self._app
        crs = getattr(app, 'current_crs', None)
        gt = getattr(app, 'current_geotransform', None)
        if crs is None or gt is None:
            return None
        overlay = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        overlay.fill(Qt.transparent)
        painter = QPainter(overlay)
        painter.setRenderHint(QPainter.Antialiasing, True)
        try:
            display_proj = getattr(app, '_current_display_projection', 'full_disk')
            try:
                crs_p4 = ''
                if crs is not None:
                    try:
                        crs_p4 = crs.to_proj4()
                    except Exception:
                        pass
                print(f"[DIAG-overlay] display_proj={display_proj} grid={self._grid_enabled} crs={crs_p4} gt.a={getattr(gt,'a',None)} gt.c={getattr(gt,'c',None)} gt.f={getattr(gt,'f',None)}")
            except Exception:
                pass
            olc = getattr(app, 'overlay_controller', None)
            if olc is None:
                painter.end()
                return None
            grid_spacing = getattr(app, 'settings', {}).get("grid_spacing_deg", 10)
            sub_step = max(0.1, getattr(app, 'settings', {}).get('grid_sub_step_tenths', 20) * 0.1)
            gc = QColor(getattr(app, 'settings', {}).get("grid_color", "#C8C8C8"))
            gc.setAlpha(getattr(app, 'settings', {}).get("grid_opacity", 160))
            if self._grid_enabled:
                pen = QPen(gc)
                pen.setWidth(getattr(app, 'settings', {}).get("grid_line_width", 1))
                painter.setPen(pen)
                if display_proj in ("equirectangular", "plate_carree"):
                    extent = getattr(app, '_display_projection_extent', None)
                    if extent is not None:
                        if display_proj == "equirectangular":
                            olc._draw_equirectangular_grid(painter, w, h, grid_spacing, sub_step)
                        else:
                            olc._draw_plate_carree_grid(painter, w, h, grid_spacing, sub_step)
                else:
                    from pyproj import Transformer
                    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
                    img_dim = max(w, h)
                    overlay_extent = max(abs(gt.c), abs(gt.c + gt.a * w),
                                         abs(gt.f), abs(gt.f + gt.e * h))
                    effective_res_m = (2 * overlay_extent) / img_dim if img_dim > 0 else abs(gt.a)
                    olc._draw_geostationary_grid(painter, w, h, overlay_extent,
                                                  effective_res_m, transformer,
                                                  grid_spacing, sub_step)
        except Exception as e:
            print(f"[VP{self.index}] Overlay render error: {e}")
        finally:
            painter.end()
        return overlay

    # -- 3D globe ----------------------------------------------------------------

    def push_texture_to_globe(self, image=None):
        globe = getattr(self, '_globe', None)
        if globe is None:
            return
        if image is not None:
            globe.set_texture_image(image)
        else:
            view = getattr(self._app, 'graphics_view', None)
            if view is None:
                return
            scene = view.scene()
            if scene is None:
                return
            items = list(scene.items())
            for item in reversed(items):
                if isinstance(item, QGraphicsPixmapItem):
                    pix = item.pixmap()
                    if not pix.isNull():
                        src_img = pix.toImage()
                        _self = self

                        def _reproject_and_set():
                            result = _self._reproject_to_equirectangular(src_img)
                            QTimer.singleShot(0, _self, lambda: _self._set_globe_texture(result))
                        threading.Thread(target=_reproject_and_set, daemon=True).start()
                    break

    def _set_globe_texture(self, img):
        globe = getattr(self, '_globe', None)
        if globe and img is not None:
            globe.set_texture_image(img)

    def _reproject_to_equirectangular(self, src_img, target_w=1440, target_h=720):
        app = getattr(self, '_app', None)
        if app is None:
            return src_img
        crs_src = getattr(app, 'current_crs', None)
        gt = getattr(app, 'current_geotransform', None)
        if crs_src is None or gt is None:
            return src_img
        from pyproj import Transformer
        try:
            t = Transformer.from_crs("EPSG:4326", crs_src, always_xy=True)
        except Exception:
            return src_img

        src_w, src_h = src_img.width(), src_img.height()
        if src_img.format() != QImage.Format_RGBA8888:
            src_img = src_img.convertToFormat(QImage.Format_RGBA8888)

        ptr = src_img.bits()
        if hasattr(ptr, 'setsize'):
            try:
                ptr.setsize(src_w * src_h * 4)
            except ValueError:
                return src_img
        src_arr = np.frombuffer(ptr, dtype=np.uint8).reshape(src_h, src_w, 4)

        out_h, out_w = max(1, target_h), max(1, target_w)
        lons = np.linspace(0, 360, out_w, endpoint=False) + (180.0 / out_w)
        lats = np.linspace(90, -90, out_h, endpoint=False) + (-90.0 / out_h)
        lon_grid, lat_grid = np.meshgrid(lons, lats)

        x_proj, y_proj = t.transform(lon_grid, lat_grid)

        col = (x_proj - gt.c) / gt.a
        row = (y_proj - gt.f) / gt.e

        valid = np.isfinite(col) & np.isfinite(row)
        col = np.clip(col, 0, src_w - 1.001)
        row = np.clip(row, 0, src_h - 1.001)

        col0 = np.floor(col).astype(np.int32)
        col1 = np.minimum(col0 + 1, src_w - 1)
        row0 = np.floor(row).astype(np.int32)
        row1 = np.minimum(row0 + 1, src_h - 1)
        u = col - col0
        v = row - row0

        top = src_arr[row0, col0] * (1 - u[:, :, None]) + src_arr[row0, col1] * u[:, :, None]
        bot = src_arr[row1, col0] * (1 - u[:, :, None]) + src_arr[row1, col1] * u[:, :, None]
        out_arr = top * (1 - v[:, :, None]) + bot * v[:, :, None]
        out_arr = out_arr.astype(np.uint8)
        out_arr[~valid] = [0, 0, 0, 0]

        return QImage(out_arr.tobytes(), out_w, out_h, out_w * 4, QImage.Format_RGBA8888)

    def push_forecast_to_globe(self, image=None):
        if hasattr(self, '_globe') and self._globe:
            if image is not None:
                self._globe.set_forecast_texture(image)

    # -- Misc -----------------------------------------------------------------

    def current_mode(self):
        return self._mode

    def state(self):
        if self._mode == "viewport" and hasattr(self, '_mirror_view'):
            return self._mirror_view.view_state()
        if self._mode in ("viewport", "bands") and hasattr(self, '_bands_view'):
            return self._bands_view.view_state()
        if self._mode == "animation" and hasattr(self, '_anim_view'):
            return self._anim_view.view_state()
        return None

    def apply_state(self, state):
        if not state:
            return
        if self._mode == "viewport" and hasattr(self, '_mirror_view'):
            self._mirror_view.apply_view_state(state)
        elif self._mode in ("viewport", "bands") and hasattr(self, '_bands_view'):
            self._bands_view.apply_view_state(state)
        elif self._mode == "animation" and hasattr(self, '_anim_view'):
            self._anim_view.apply_view_state(state)

    def cleanup(self):
        if hasattr(self, '_mirror_timer'):
            try:
                self._mirror_timer.stop()
            except RuntimeError:
                pass
        if hasattr(self, '_globe') and self._globe is not None:
            try:
                self._globe.cleanup()
            except RuntimeError:
                pass