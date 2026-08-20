# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: ui/MultiViewportWindow.py
# Description: Multi-viewport window management for comparative satellite imagery analysis.
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
import threading
import math
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QRectF, QSize, QPointF
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QBrush, QFont, QImage,
    QMouseEvent, QTransform, QPen, QPainterPath
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QStackedWidget, QGraphicsView, QGraphicsScene, QSizePolicy,
    QPushButton, QGraphicsPixmapItem, QFrame, QDialog, QDialogButtonBox,
    QApplication, QMessageBox, QComboBox,
)

from .viewport_surface import (
    SimpleImageView as _SimpleImageView,
    ForecastView as _ForecastView,
    ViewportSurfaceWidget,
)


class MultiViewportWindow(QWidget):
    """Single-display viewport window (header + one ViewportSurfaceWidget)."""

    def __init__(self, index, app_ref):
        super().__init__()
        self.index = index
        self._app = app_ref
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle(f"Multi Viewport #{index + 1}")
        self.setMinimumSize(400, 300)
        self.resize(640, 480)
        self.setAttribute(Qt.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setFixedHeight(28)
        header.setStyleSheet("background: #1e1e1e; border-bottom: 1px solid #333;")
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(8, 2, 8, 2)
        self._mode_label = QLabel("viewport")
        self._mode_label.setStyleSheet("color: #CE93D8; font-weight: bold; font-size: 10px;")
        hdr.addWidget(self._mode_label)
        hdr.addStretch()
        idx_lbl = QLabel(f"VP #{index + 1}")
        idx_lbl.setStyleSheet("color: #666; font-weight: bold; font-size: 10px;")
        hdr.addWidget(idx_lbl)
        root.addWidget(header)

        self._surface = ViewportSurfaceWidget(app_ref, index)
        root.addWidget(self._surface, 1)

        self.stack = self._surface.stack
        self._pages = self._surface._pages

    # -- Delegating public API (preserves the old MultiViewportWindow surface) --

    def update_config(self, mode=None, band=None, product=None,
                      overlays_enabled=None, grid_enabled=None,
                      coast_enabled=None):
        changed = self._surface.update_config(
            mode=mode, band=band, product=product,
            overlays_enabled=overlays_enabled,
            grid_enabled=grid_enabled, coast_enabled=coast_enabled)
        if mode is not None:
            self._mode_label.setText(mode)
            self.setWindowTitle(f"Multi Viewport #{self.index + 1} - {mode}")
        return changed

    def set_band_pixmap(self, pixmap):
        self._surface.set_band_pixmap(pixmap)

    def set_composite_pixmap(self, pixmap):
        self._surface.set_composite_pixmap(pixmap)

    def set_animation_pixmap(self, pixmap):
        self._surface.set_animation_pixmap(pixmap)

    def set_forecast_pixmap(self, pixmap):
        self._surface.set_forecast_pixmap(pixmap)

    def push_texture_to_globe(self, image=None):
        self._surface.push_texture_to_globe(image)

    def push_forecast_to_globe(self, image=None):
        self._surface.push_forecast_to_globe(image)

    def current_mode(self):
        return self._surface.current_mode()

    @property
    def surface(self):
        return self._surface

    def surface_widget(self):
        return self._surface

    def _on_forecast_btn(self):
        app = self._app
        nhc_storms = getattr(app, 'nhc_storms', None)
        if not nhc_storms:
            QMessageBox.information(self, "No Storms", "No NHC storm data loaded. Download storm data from the Tracks tab first.")
            return
        storm_keys = list(nhc_storms.keys())
        if not storm_keys:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Forecast Generator -- Viewport #{self.index + 1}")
        dlg.setMinimumWidth(360)
        lo = QVBoxLayout(dlg)
        lo.addWidget(QLabel("Select storm:"))
        storm_combo = QComboBox()
        for sid in storm_keys:
            sd = nhc_storms[sid]
            label = sd.get("storm_name", sid)
            storm_combo.addItem(f"{label} ({sid})", sid)
        lo.addWidget(storm_combo)
        btn_row = QHBoxLayout()
        gen_btn = QPushButton("Generate Forecast")
        gen_btn.setStyleSheet("QPushButton{background:#2E7D32;color:#fff;font-weight:600;padding:6px 16px;border-radius:3px;}")
        btn_row.addWidget(gen_btn)
        close_btn = QPushButton("Close")
        btn_row.addWidget(close_btn)
        lo.addLayout(btn_row)
        close_btn.clicked.connect(dlg.accept)

        def do_generate():
            sid = storm_combo.currentData()
            gen_btn.setEnabled(False)
            gen_btn.setText("Generating...")
            QApplication.processEvents()
            if hasattr(app, '_request_multi_forecast'):
                app._request_multi_forecast(self.index, sid)
            dlg.accept()

        gen_btn.clicked.connect(do_generate)
        dlg.exec()
        gen_btn.setEnabled(True)
        gen_btn.setText("Generate Forecast")

    def closeEvent(self, event):
        if hasattr(self, '_surface'):
            self._surface.cleanup()
        super().closeEvent(event)


class MultiViewportManager:
    def __init__(self, app):
        self.app = app
        self.windows = []

    def set_count(self, n):
        n = max(0, min(5, n))
        while len(self.windows) < n:
            idx = len(self.windows)
            win = MultiViewportWindow(idx, self.app)
            self.windows.append(win)
            win.show()
        while len(self.windows) > n:
            win = self.windows.pop()
            win.close()
            win.deleteLater()

    def get_windows(self):
        return list(self.windows)

    def get_animation_window(self):
        for win in self.windows:
            if win.current_mode() == "animation":
                return win
        return None

    def get_globe_windows(self):
        return [w for w in self.windows if w.current_mode() == "3d globe"]

    def get_viewport_windows(self):
        return [w for w in self.windows if w.current_mode() == "viewport"]

    def get_bands_windows(self):
        return [w for w in self.windows if w.current_mode() == "bands"]

    def destroy(self):
        for win in self.windows:
            try:
                win.close()
                win.deleteLater()
            except RuntimeError:
                pass
        self.windows.clear()
