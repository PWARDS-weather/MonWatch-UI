# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: ui/Centerviewport.py
# Description: Central viewport implementation with zoomable graphics view
#              and multi-frame viewport management.
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

import re
from pathlib import Path
from PySide6.QtCore import QPointF, Qt, QMimeData, QUrl, Signal, QTimer
from PySide6.QtGui import (
    QPixmap, QDrag, QMouseEvent,
    QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent,
    QWheelEvent, QImage, QPainter, QFont, QPen, QColor, QPainterPath,
    QBrush, QIcon,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QWidget, QGraphicsView, QGraphicsScene, QMenu, QFrame,
    QGraphicsPixmapItem, QGraphicsRectItem, QSizePolicy, QMessageBox,
    QStyle,
)
from .drag_drop_handler import DragDropHandlerMixin
from .tile_renderer import GridTileLayer


class ZoomableGraphicsView(DragDropHandlerMixin, QGraphicsView):
    zoomChanged = Signal(float)
    maxZoomReached = Signal()
    imageMouseMoved = Signal(float, float)
    imageRectSelected = Signal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._opengl_widget = None
        self.gpu_enabled = False
        self.setScene(QGraphicsScene())

        self.setBackgroundBrush(QBrush(QColor("#000000")))

        self.grid_pattern = "straight"
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.zoom_enabled = True
        self.zoom_factor = 1.0
        self.base_zoom = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 30.0
        self.max_zoom_threshold = 30
        self.using_original = False
        self._user_has_zoomed = False
        self._target_zoom = 1.0
        self._zoom_anim_timer = QTimer(self)
        self._zoom_anim_timer.setInterval(16)
        self._zoom_anim_timer.timeout.connect(self._step_zoom_animation)
        self._adaptive_quality = True
        self._interaction_timer = QTimer(self)
        self._interaction_timer.setSingleShot(True)
        self._interaction_timer.setInterval(300)
        self._interaction_timer.timeout.connect(self._on_interaction_stopped)
        self._rect_start = None
        self._rubber_rect = None
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.tile_layer = GridTileLayer(self)

    def _clear_rubber_rect(self):
        if self._rubber_rect is not None:
            try:
                s = self.scene()
                if s and self._rubber_rect.scene() == s:
                    s.removeItem(self._rubber_rect)
            except RuntimeError:
                pass
            self._rubber_rect = None

    def set_gpu_acceleration(self, enabled):
        if enabled == self.gpu_enabled:
            return
        self.gpu_enabled = enabled
        if enabled:
            if self._opengl_widget is None:
                self._opengl_widget = QOpenGLWidget()
                self._opengl_widget.setAutoFillBackground(False)
            self.setViewport(self._opengl_widget)
            self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
            self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        else:
            self._opengl_widget = None
            self.setViewport(QWidget())
            self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)
            self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
            self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)

    def set_image(self, pixmap, preserve_view=False, quality_level=1.0,
                  is_original=False, scene_pos=None):
        tl = getattr(self, 'tile_layer', None)
        if tl is not None:
            tl.clear()
        if not preserve_view:
            self._user_has_zoomed = False
        if preserve_view and self.scene() and self.scene().items():
            view_center = self.mapToScene(self.viewport().rect().center())
            old_zoom = self.zoom_factor
        else:
            view_center = None
        scene = self.scene()
        win = self.window()
        if hasattr(win, 'clear_temp_markers'):
            win.clear_temp_markers(log=False)
        preserved_items = []
        for it in list(scene.items()):
            try:
                if it.zValue() > 0:
                    preserved_items.append(it)
                    scene.removeItem(it)
            except RuntimeError:
                pass
        scene.clear()
        for it in preserved_items:
            try:
                scene.addItem(it)
            except RuntimeError:
                pass
        win = self.window()
        if hasattr(win, 'clear_temp_markers'):
            win.clear_temp_markers(log=False)
        item = self.scene().addPixmap(pixmap)
        if scene_pos is not None:
            item.setPos(scene_pos)
        if hasattr(win, '_pending_close_dialog') and win._pending_close_dialog:
            try:
                win._pending_close_dialog.set_final("Image displayed in viewport.")
                win._pending_close_dialog.close()
            except Exception:
                pass
            win._pending_close_dialog = None
        if preserve_view and view_center:
            new_center_x = view_center.x() * quality_level
            new_center_y = view_center.y() * quality_level
            self.zoom_factor = old_zoom * (quality_level / self.base_zoom)
            self.base_zoom = quality_level
            self.resetTransform()
            self.scale(self.zoom_factor, self.zoom_factor)
            self.centerOn(QPointF(new_center_x, new_center_y))
        else:
            self.fit_to_image()
            self.base_zoom = quality_level
        self.using_original = is_original
        self.max_zoom = 1000.0 if is_original else 20.0
        self.zoomChanged.emit(self.zoom_factor)

    def set_tiled_image(self, rgba, preserve_view=False, scene_pos=None):
        """Display an RGBA/BGRA source as a zoom-aware tiled LOD layer.

        Only the visible tiles at the current LOD stride are materialised and
        kept in the scene; panning/zooming rebuilds just the visible set.
        """
        if self.scene() and self.scene().items() and preserve_view:
            view_center = self.mapToScene(self.viewport().rect().center())
            old_zoom = self.zoom_factor
        else:
            view_center = None
        win = self.window()
        if hasattr(win, 'clear_temp_markers'):
            win.clear_temp_markers(log=False)
        scene = self.scene()
        preserved_items = []
        for it in list(scene.items()):
            try:
                if it.zValue() > 0:
                    preserved_items.append(it)
                    scene.removeItem(it)
            except RuntimeError:
                pass
        scene.clear()
        for it in preserved_items:
            try:
                scene.addItem(it)
            except RuntimeError:
                pass
        if scene_pos is None:
            scene_pos = QPointF(0, 0)
        self.tile_layer.set_source(rgba, (scene_pos.x(), scene_pos.y()))
        if not self.tile_layer.active:
            return
        self.using_original = False
        self.max_zoom = 1000.0
        if preserve_view and view_center:
            self.zoom_factor = max(self.min_zoom, old_zoom)
            self.resetTransform()
            self.scale(self.zoom_factor, self.zoom_factor)
            self.centerOn(view_center)
        else:
            self._fit_to_tiled_image()
        self.tile_layer.refresh()
        self.zoomChanged.emit(self.zoom_factor)
        self.viewport().update()

    def _fit_to_tiled_image(self):
        rect = self.tile_layer.full_scene_rect()
        if rect is None:
            return
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.centerOn(rect.center())
        self.zoom_factor = self.transform().m11()
        self._user_has_zoomed = False
        self.zoomChanged.emit(self.zoom_factor)

    def _refresh_tiles(self):
        tl = getattr(self, 'tile_layer', None)
        if tl is not None:
            try:
                tl.refresh()
            except Exception:
                pass

    def enable_zoom(self, flag):
        self.zoom_enabled = flag

    def set_adaptive_quality(self, enabled):
        self._adaptive_quality = enabled

    def _on_interaction_stopped(self):
        self._refresh_tiles()
        win = self.window()
        if win and hasattr(win, '_on_zoom_stopped'):
            win._on_zoom_stopped()
        if win and hasattr(win, '_on_coast_interaction_end'):
            win._on_coast_interaction_end()

    def wheelEvent(self, event):
        if not self.zoom_enabled:
            event.ignore()
            return
        if event.angleDelta().y() > 0:
            factor = 1.25
        else:
            factor = 1 / 1.25
        new_zoom = self.zoom_factor * factor
        if new_zoom < self.min_zoom:
            factor = self.min_zoom / self.zoom_factor
            new_zoom = self.min_zoom
        elif new_zoom > self.max_zoom:
            factor = self.max_zoom / self.zoom_factor
            new_zoom = self.max_zoom
        self._user_has_zoomed = True
        target = self.zoom_factor * factor
        self._animate_to_zoom(target)
        win = self.window()
        if win and hasattr(win, '_on_coast_interaction_begin'):
            win._on_coast_interaction_begin()
        self._interaction_timer.start()
        if self._adaptive_quality and win and hasattr(win, '_on_zoom_started'):
            win._on_zoom_started()

    def _animate_to_zoom(self, target_factor):
        self._target_zoom = max(self.min_zoom, min(self.max_zoom, target_factor))
        if not self._zoom_anim_timer.isActive():
            self._zoom_anim_timer.start()

    def _step_zoom_animation(self):
        if abs(self.zoom_factor - self._target_zoom) < 0.001:
            self.zoom_factor = self._target_zoom
            self._zoom_anim_timer.stop()
            self.zoomChanged.emit(self.zoom_factor)
            return
        prev = self.zoom_factor
        self.zoom_factor += (self._target_zoom - self.zoom_factor) * 0.35
        if abs(self.zoom_factor - prev) < 0.0005:
            self.zoom_factor = prev + (0.0005 if self._target_zoom > prev else -0.0005)
        scale_factor = self.zoom_factor / prev
        self.scale(scale_factor, scale_factor)
        self.zoomChanged.emit(self.zoom_factor)
        self._refresh_tiles()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._user_has_zoomed and self.scene() and self.scene().items():
            self.fit_to_image()
        self._refresh_tiles()
        win = self.window()
        if win and hasattr(win, '_position_viewport_animation_bar'):
            win._position_viewport_animation_bar()

    def mousePressEvent(self, event):
        mods = event.modifiers()
        if (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier):
            scene_pos = self.mapToScene(event.pos())
            if event.button() == Qt.LeftButton:
                new_zoom = max(self.min_zoom, self.zoom_factor * 5.0)
                factor = new_zoom / self.zoom_factor
                self.zoom_factor = new_zoom
                self.scale(factor, factor)
                self.centerOn(scene_pos)
                self._user_has_zoomed = True
                self.zoomChanged.emit(self.zoom_factor)
                return
            elif event.button() == Qt.RightButton:
                new_zoom = max(self.min_zoom, self.zoom_factor / 5.0)
                factor = new_zoom / self.zoom_factor
                self.zoom_factor = new_zoom
                self.scale(factor, factor)
                self.centerOn(scene_pos)
                self._user_has_zoomed = True
                self.zoomChanged.emit(self.zoom_factor)
                return
        if (mods & Qt.ControlModifier) and event.button() == Qt.LeftButton:
            sp = self.mapToScene(event.pos())
            self._rect_start = (sp.x(), sp.y())
            return
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton and not event.isAccepted():
            win = self.window()
            if win and hasattr(win, '_remove_point_info_box'):
                win._remove_point_info_box()
        self._interaction_timer.start()
        win = self.window()
        if event.button() == Qt.LeftButton and not (mods & (Qt.ControlModifier | Qt.ShiftModifier)):
            if win and hasattr(win, '_on_coast_interaction_begin'):
                win._on_coast_interaction_begin()
        if self._adaptive_quality and win and hasattr(win, '_on_zoom_started'):
            win._on_zoom_started()

    def mouseMoveEvent(self, event):
        self._interaction_timer.start()
        tl = getattr(self, 'tile_layer', None)
        if tl is not None and tl.active and not self._rect_start:
            self._refresh_tiles()
        if self.scene() and self.scene().items():
            scene_pos = self.mapToScene(event.pos())
            self.imageMouseMoved.emit(scene_pos.x(), scene_pos.y())
        if self._rect_start is not None:
            cur = self.mapToScene(event.pos())
            self._draw_rubber_rect(
                self._rect_start[0], self._rect_start[1], cur.x(), cur.y())
            return
        super().mouseMoveEvent(event)

    def _draw_rubber_rect(self, x1, y1, x2, y2):
        self._clear_rubber_rect()
        s = self.scene()
        if not s:
            return
        r = QGraphicsRectItem(min(x1, x2), min(y1, y2),
                              abs(x2 - x1), abs(y2 - y1))
        r.setPen(QPen(QColor(0, 255, 100, 220), 1.5, Qt.DashLine))
        r.setBrush(QBrush(QColor(0, 255, 100, 30)))
        r.setZValue(100)
        s.addItem(r)
        self._rubber_rect = r

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._rect_start is not None:
            sp = self.mapToScene(event.pos())
            x1, y1 = self._rect_start
            x2, y2 = sp.x(), sp.y()
            self._clear_rubber_rect()
            if abs(x2 - x1) > 5 and abs(y2 - y1) > 5:
                self.imageRectSelected.emit(
                    min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))
            self._rect_start = None
            return
        super().mouseReleaseEvent(event)
        if self._adaptive_quality:
            self._interaction_timer.start()

    def _get_base_image_item(self):
        if not self.scene():
            return None
        for item in reversed(self.scene().items()):
            if isinstance(item, QGraphicsPixmapItem):
                return item
        return None

    def fit_to_image(self):
        target = None
        tl = getattr(self, 'tile_layer', None)
        if tl is not None and tl.active:
            target = tl.full_scene_rect()
        if target is None:
            item = self._get_base_image_item()
            if item:
                target = item.sceneBoundingRect()
            elif self.scene() and self.scene().items():
                target = self.scene().itemsBoundingRect()
        if target is not None:
            self.fitInView(target, Qt.KeepAspectRatio)
            self.centerOn(target.center())
            self.zoom_factor = self.transform().m11()
            self._user_has_zoomed = False
            self.zoomChanged.emit(self.zoom_factor)

    def center_on_image(self):
        tl = getattr(self, 'tile_layer', None)
        if tl is not None and tl.active:
            center = tl.full_scene_rect().center()
            self.centerOn(center)
            return
        item = self._get_base_image_item()
        if item:
            center = item.sceneBoundingRect().center()
            self.centerOn(center)

    def keyPressEvent(self, event):
        if (event.key() == Qt.Key_Space
                and (event.modifiers() & Qt.ControlModifier)):
            win = self.window()
            if win and hasattr(win, 'clear_contour_overlay'):
                win.clear_contour_overlay()
            event.accept()
            return
        super().keyPressEvent(event)

    def _std_icon(self, standard_pixmap, size=14, color="#CCCCCC"):
        pm = self.style().standardIcon(standard_pixmap).pixmap(size, size)
        out = QPixmap(pm.size())
        out.fill(Qt.transparent)
        painter = QPainter(out)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.drawPixmap(0, 0, pm)
        painter.setCompositionMode(QPainter.CompositionMode_SourceAtop)
        painter.fillRect(out.rect(), QColor(color))
        painter.end()
        return QIcon(out)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background: #1f1f1f; color: #ddd; border: 1px solid #444; }
            QMenu::item:selected { background: #4c1d95; color: white; }
            QMenu::item { padding: 4px 20px 4px 6px; }
        """)

        temp_action = menu.addAction(self._std_icon(QStyle.StandardPixmap.SP_CommandLink), "View Temperature (place marker)")
        clear_temps = menu.addAction(self._std_icon(QStyle.StandardPixmap.SP_DialogResetButton), "Clear All Temp Markers")
        truecolor_action = menu.addAction("True Color RGB")
        night_action = menu.addAction("Night Microphysics")
        menu.addSeparator()

        cat_menu = menu.addMenu("Categories")
        cat_menu.addAction("Temperature Products")
        cat_menu.addAction("Forecast / Model Overlays")
        cat_menu.addAction("Moisture / WV")
        cat_menu.addAction("Cloud / Convection RGBs")
        cat_menu.addAction("Aerosol / Dust")

        menu.addSeparator()
        add_file = menu.addAction(self._std_icon(QStyle.StandardPixmap.SP_FileIcon), "Add File...")
        add_folder = menu.addAction(self._std_icon(QStyle.StandardPixmap.SP_DirOpenIcon), "Add Folder...")

        menu.addSeparator()
        forecast_action = menu.addAction("View Forecast")

        if getattr(self.window(), 'current_mode', 'casual') == "professional":
            menu.addSeparator()
            pro_menu = menu.addMenu("Professional")
            pro_menu.addAction("Set as Background Band")
            pro_menu.addAction("Add as Foreground Layer")
            pro_menu.addAction("Open in Professional Tab")
            pro_menu.addSeparator()
            pro_menu.addAction("New Meteorological Track...")

        action = menu.exec(event.globalPos())
        if not action:
            return

        win = self.window()
        text = action.text()

        if action == add_file:
            win.choose_file()
        elif action == add_folder:
            win.choose_folder()
        elif "Temperature" in text or "View Temperature" in text:
            win._view_temperature_at_cursor()
        elif "Clear All Temp Markers" in text:
            win.clear_temp_markers()
        elif "True Color" in text:
            win._quick_load_common_product("true_color")
        elif "Night Microphysics" in text:
            win._quick_load_common_product("night_microphysics")
        elif "Background Band" in text:
            win._pro_set_background_from_context()
        elif "Foreground Layer" in text:
            win._pro_set_foreground_from_context()
        elif "Professional Tab" in text:
            if hasattr(win, 'right_tab_widget') and hasattr(win, '_pro_tab_index'):
                win.right_tab_widget.setCurrentIndex(win._pro_tab_index)
        elif "New Meteorological Track" in text:
            win._show_new_track_dialog()
        elif "View Forecast" in text:
            scene_pos = self.mapToScene(event.pos())
            win._open_forecast_dialog(scene_pos.x(), scene_pos.y())
        elif text in ("Temperature Products", "Forecast / Model Overlays",
                      "Moisture / WV", "Cloud / Convection RGBs",
                      "Aerosol / Dust"):
            win.log(f"Category quick-load: {text} (extend as needed)")


class ViewportFrame(DragDropHandlerMixin, QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("ViewportFrame { border: 1px solid #444; }")

    def resizeEvent(self, event):
        super().resizeEvent(event)
