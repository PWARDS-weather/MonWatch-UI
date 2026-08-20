# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: ui/TearOffTabBar.py
# Description: Custom tear-off tab bar widget for detachable tab functionality.
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

import logging

from PySide6.QtCore import Qt, Signal, QPoint, QObject, QEvent, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QTabBar, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout
)

log = logging.getLogger(__name__)


class TabTearOffFilter(QObject):
    tearOffRequested = Signal(int, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_pos = None
        self._press_tab = -1
        self._tear_threshold = 20

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            me = QMouseEvent(event)
            if me.button() == Qt.LeftButton:
                self._press_pos = me.pos()
                self._press_tab = obj.tabAt(me.pos())
                log.info("Tab press: tab=%d at=(%d,%d)", self._press_tab, me.pos().x(), me.pos().y())
        elif event.type() == QEvent.MouseMove:
            if self._press_tab >= 0 and self._press_pos:
                me = QMouseEvent(event)
                if me.buttons() == Qt.LeftButton and not obj.rect().contains(me.pos()):
                    delta = (me.pos() - self._press_pos).manhattanLength()
                    if delta > self._tear_threshold:
                        log.info("Tab tear-off: tab=%d delta=%d", self._press_tab, delta)
                        tab = self._press_tab
                        gpos = obj.mapToGlobal(me.pos())
                        self._press_tab = -1
                        self._press_pos = None
                        QTimer.singleShot(0, lambda t=tab, p=gpos: self.tearOffRequested.emit(t, p))
                        return True
        elif event.type() == QEvent.MouseButtonRelease:
            log.info("Tab release: was_tab=%d", self._press_tab)
            self._press_tab = -1
            self._press_pos = None
        return super().eventFilter(obj, event)


class TabFloatingWindow(QWidget):
    def __init__(self, tab_text: str, tab_widget: QWidget, tab_icon, tab_tooltip: str,
                 tab_index: int, main_app):
        super().__init__()
        self._main_app = main_app
        self._tab_widget = tab_widget
        self._tab_text = tab_text
        self._tab_icon = tab_icon
        self._tab_tooltip = tab_tooltip
        self._tab_index = tab_index

        log.info("Floating window created: tab='%s' index=%d", tab_text, tab_index)

        self.setWindowFlags(Qt.Window)
        self.setWindowTitle(tab_text)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setMinimumSize(300, 200)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(tab_widget)
        tab_widget.show()

        self._setup_title_bar()

        hint = tab_widget.sizeHint()
        self.resize(max(480, hint.width()), max(360, hint.height() + 28))

    def _setup_title_bar(self):
        bar = QWidget()
        bar.setFixedHeight(28)
        bar.setStyleSheet("background: #252525; border-bottom: 1px solid #444;")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(8, 0, 4, 0)

        lbl = QLabel(self._tab_text)
        lbl.setStyleSheet("color: #DDD; font-size: 10px; font-weight: bold; background: transparent;")
        bar_layout.addWidget(lbl)
        bar_layout.addStretch()

        dock_btn = QPushButton("\u25A9")
        dock_btn.setFixedSize(22, 22)
        dock_btn.setToolTip("Dock tab back to main window")
        dock_btn.setStyleSheet(
            "QPushButton { background: #3a3a3a; color: #aaa; border: 1px solid #555;"
            " border-radius: 3px; font-size: 12px; }"
            "QPushButton:hover { background: #5a5a5a; color: #fff; }"
        )
        dock_btn.clicked.connect(self.close)
        bar_layout.addWidget(dock_btn)
        self.layout().insertWidget(0, bar)

    def closeEvent(self, event):
        if hasattr(self, '_tab_widget') and self._tab_widget:
            log.info("Floating window closing: re-attaching tab '%s'", self._tab_text)
            self._reattach_tab()
        event.accept()
        self.deleteLater()

    def _reattach_tab(self):
        app = getattr(self._main_app, 'main_ui', self._main_app)
        if not hasattr(app, 'right_tab_widget'):
            log.warning("Cannot reattach tab: main app has no right_tab_widget")
            return
        tw = app.right_tab_widget
        if not tw.isVisible():
            tw.setVisible(True)
            log.info("Right panel shown (tab reattached)")
        if not self._tab_widget:
            log.warning("Cannot reattach tab: tab_widget is None")
            return
        tab_widget = self._tab_widget
        tab_widget.setParent(None)
        tab_widget.hide()
        pos = min(self._tab_index, tw.count())
        if self._tab_icon and not self._tab_icon.isNull():
            idx = tw.insertTab(pos, tab_widget, self._tab_icon, self._tab_text)
        else:
            idx = tw.insertTab(pos, tab_widget, self._tab_text)
        if self._tab_tooltip:
            tw.setTabToolTip(idx, self._tab_tooltip)
        tw.setCurrentIndex(idx)
        log.info("Tab reattached: '%s' at index %d (original %d)", self._tab_text, idx, self._tab_index)
        self._tab_widget = None
