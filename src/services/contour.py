# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: services/contour.py
# Description: Contour line computation and rendering for meteorological parameter visualization.
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
from scipy.ndimage import gaussian_filter
from matplotlib import pyplot as plt
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPen, QColor, QFont, QPainter, QImage, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem
from ..core.helpers import decimate_screen_points


class ContourService:
    def __init__(self, main_ui):
        self.main_ui = main_ui

    def _get_contour_data(self):
        selected_band = None
        if hasattr(self.main_ui, 'band_checkboxes'):
            selected_band = next((b for b, cb in self.main_ui.band_checkboxes.items() if cb.isChecked()), None)
        data = None
        if selected_band and hasattr(self.main_ui, 'cache') and hasattr(self.main_ui.cache, 'raw') and selected_band in self.main_ui.cache.raw:
            data = self.main_ui.cache.raw[selected_band]
        elif hasattr(self.main_ui, '_sataid_current_data') and self.main_ui._sataid_current_data is not None:
            data = self.main_ui._sataid_current_data
            selected_band = selected_band or "SATAID"
        if data is None and hasattr(self.main_ui, 'cache') and hasattr(self.main_ui.cache, 'raw'):
            for band in self.main_ui.cache.raw:
                data = self.main_ui.cache.raw[band]
                selected_band = band
                break
        return data, selected_band

    def _redraw_contour(self):
        if not getattr(self.main_ui, '_contour_active', False):
            return
        rect = getattr(self.main_ui, '_contour_last_rect', None)
        if rect is None:
            return
        col_s, row_s, col_e, row_e = rect
        data, selected_band = self._get_contour_data()
        if data is None:
            return
        dh, dw = data.shape
        col_s = max(0, min(col_s, dw))
        row_s = max(0, min(row_s, dh))
        col_e = min(col_e, dw)
        row_e = min(row_e, dh)
        if col_e <= col_s or row_e <= row_s:
            return
        region = data[row_s:row_e, col_s:col_e]
        if region.size == 0 or np.all(~np.isfinite(region)):
            return
        self._compute_and_draw_contour(region, selected_band, col_s, row_s, col_e, row_e)

    def _compute_and_draw_contour(self, region, band_name, col_s, row_s, col_e, row_e):
        rh, rw = region.shape
        self.main_ui.log(f"Contouring region {rw}x{rh} at ({col_s},{row_s})...")
        self.main_ui.status_bar.showMessage("Smoothing data...")
        QApplication.processEvents()
        bn = (band_name or "").upper()
        if bn.startswith("IR") or bn.startswith("WV") or bn.startswith("I2") or bn.startswith("I4"):
            valid_check = region[np.isfinite(region)]
            if len(valid_check) > 0 and float(np.mean(valid_check)) > 100:
                region = region.astype(np.float64) - 273.15
        try:
            smoothed = gaussian_filter(region.astype(np.float64), sigma=1.0)
        except Exception:
            smoothed = region.astype(np.float64)
        valid = smoothed[np.isfinite(smoothed)]
        if len(valid) <= 10:
            self.main_ui.log("Contour: not enough valid data")
            self.main_ui.status_bar.showMessage("Contour: not enough valid data")
            return
        vmin, vmax = np.percentile(valid, [1, 99])
        if abs(vmax - vmin) < 1e-10:
            self.main_ui.log("Contour: data range too small")
            self.main_ui.status_bar.showMessage("Contour: data range too small")
            return
        dr = vmax - vmin
        if dr >= 50:
            step = 10.0
        elif dr >= 20:
            step = 5.0
        elif dr >= 10:
            step = 2.0
        elif dr >= 4:
            step = 1.0
        elif dr >= 1.5:
            step = 0.5
        elif dr >= 0.3:
            step = 0.1
        else:
            step = dr / 5.0
        lo = np.floor(vmin / step) * step
        hi = np.ceil(vmax / step) * step
        levels = np.round(np.arange(lo, hi + step * 0.5, step), 10)
        self.main_ui.status_bar.showMessage(f"Computing contour ({len(levels)} levels, step={step})...")
        QApplication.processEvents()
        try:
            fig, ax = plt.subplots()
            cs = ax.contour(smoothed, levels=levels, linewidths=0)
            segments = []
            labels = []
            for i in range(len(cs.levels)):
                lv = cs.levels[i]
                best_seg = None
                best_len = 0
                for seg in cs.allsegs[i]:
                    if len(seg) >= 2:
                        segments.append(seg)
                        if len(seg) > best_len:
                            best_len = len(seg)
                            best_seg = seg
                if best_seg is not None and i % 2 == 0:
                    mid = len(best_seg) // 2
                    labels.append((lv, best_seg[mid]))
            plt.close(fig)
            if segments:
                self.set_contour_overlay(segments, col_s, row_s, labels=labels)
                self.main_ui.log(f"Contour: {len(segments)} segments, {len(labels)} labels (step={step})")
                self.main_ui.status_bar.showMessage(f"Contour: {len(levels)} levels, {len(labels)} labels")
            else:
                self.clear_contour_overlay()
                self.main_ui.status_bar.showMessage("Contour: no segments")
        except Exception as e:
            self.main_ui.log(f"Contour error: {e}")
            import traceback
            self.main_ui.log(traceback.format_exc())
            self.main_ui.status_bar.showMessage("Contour error")

    def set_contour_overlay(self, segments, col_s, row_s, labels=None):
        self.clear_contour_overlay()
        scene = self.main_ui.graphics_view.scene()
        if not scene:
            return
        ox = oy = 0.0
        pix_w = pix_h = 0
        for item in reversed(scene.items()):
            if isinstance(item, QGraphicsPixmapItem):
                ox = item.pos().x()
                oy = item.pos().y()
                p = item.pixmap()
                if p:
                    pix_w = p.width()
                    pix_h = p.height()
                break
        if pix_w == 0 or pix_h == 0:
            return
        data, _ = self._get_contour_data()
        if data is not None:
            dh, dw = data.shape
            inv_sx = pix_w / dw if (pix_w > 0 and dw > 0) else 1.0
            inv_sy = pix_h / dh if (pix_h > 0 and dh > 0) else 1.0
        else:
            inv_sx = inv_sy = 1.0
        img = QImage(pix_w, pix_h, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        for verts in segments:
            if len(verts) < 2:
                continue
            v = np.asarray(verts, dtype=np.float64)
            if v.ndim != 2 or v.shape[1] < 2 or v.shape[0] < 2:
                continue
            px = (col_s + v[:, 0]) * inv_sx
            py = (row_s + v[:, 1]) * inv_sy
            run = np.column_stack([px, py])
            run = decimate_screen_points(run, min_step_px=1.0)
            if len(run) < 2:
                continue
            poly = QPolygonF()
            for p in run:
                poly.append(QPointF(p[0], p[1]))
            painter.setPen(QPen(QColor(0, 0, 0, 180), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(poly)
            painter.setPen(QPen(QColor(0, 255, 255, 230), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(poly)
        if labels:
            font = QFont('monospace', 7)
            painter.setFont(font)
            for val, pos in labels:
                lx = (col_s + float(pos[0])) * inv_sx
                ly = (row_s + float(pos[1])) * inv_sy
                text = f'{val:.1f}' if abs(val) < 100 else f'{val:.0f}'
                r = QRectF(0, 0, 200, 20)
                r.moveCenter(QPointF(lx, ly))
                painter.setPen(QColor(0, 255, 255))
                painter.drawText(r, Qt.AlignCenter, text)
        painter.end()
        pix_item = QGraphicsPixmapItem(QPixmap.fromImage(img))
        pix_item.setPos(ox, oy)
        pix_item.setZValue(50)
        scene.addItem(pix_item)
        self.main_ui._contour_overlay_items.append(pix_item)

    def clear_contour_overlay(self):
        scene = self.main_ui.graphics_view.scene()
        for item in self.main_ui._contour_overlay_items:
            if scene and item.scene() == scene:
                scene.removeItem(item)
        self.main_ui._contour_overlay_items.clear()
