# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: controllers/alert_controller.py
# Description: Tropical cyclone alert generation and management for satellite-based warning systems (PAGASA, NWS, JTWC).
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


import sys
import json
import os
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from PySide6.QtCore import QObject, Signal, QTimer, Qt, QSize
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox,
    QTreeWidget, QTreeWidgetItem, QCheckBox, QDialog, QTextEdit,
    QMessageBox, QSystemTrayIcon, QApplication,
)

from src.managers.alert_manager import (
    AlertManager, play_notification, play_emergency, is_expired,
    _load_ack_ids, _save_ack_ids, stop_sounds, speak_alert,
)
from src.workers.alert_map_worker import AlertMapWorker
from src.core.helpers import THEMES

if getattr(sys, 'frozen', False):
    top_dir = Path(sys.executable).resolve().parent
    src_dir = top_dir
else:
    top_dir = Path(__file__).resolve().parent.parent.parent
    src_dir = Path(__file__).resolve().parent.parent

log = logging.getLogger(__name__)


_ALERT_TRIM_KEYS = ("provinces", "geometry", "geocode", "parameters")
_ALERT_RETENTION_DAYS = 7
_ALERT_SAVE_DEBOUNCE_MS = 2000


def _trim_alert(alert):
    c = dict(alert)
    for k in _ALERT_TRIM_KEYS:
        c.pop(k, None)
    return c


def is_old(alert, days=_ALERT_RETENTION_DAYS):
    if days <= 0:
        return False
    key = alert.get("effective") or alert.get("effective_date") or alert.get("expires") or ""
    if not key:
        return False
    try:
        dt = datetime.fromisoformat(str(key).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return dt < cutoff
    except Exception:
        return False


def _write_alerts_json(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


class AlertController(QObject):
    """Tropical cyclone alert generation and management controller.

    Handles alert creation, notification, and display for tropical cyclone
    warnings from PAGASA, NWS, JTWC, and other meteorological agencies.
    Manages alert persistence, acknowledgment tracking, and emergency
    notification systems.

    Attributes:
        main_ui (MainUI): Reference to the main UI instance for state access.

    Signals:
        alert_generated (AlertData): Emitted when a new alert is created.
        emergency_alert (AlertData): Emitted for emergency-level alerts.
        alert_acknowledged (str): Emitted when an alert is acknowledged.

    Note:
        Alert data is persisted to disk for crash recovery and audit trails.
    """
    new_alert = Signal(object)
    emergency_alert = Signal(object)
    alert_acknowledged = Signal(str)

    def __init__(self, main_ui):
        super().__init__()
        self.main_ui = main_ui

    def _init_alert_system(self):

        """Initialize the alert management system.

        Sets up alert handlers, notification callbacks, and UI connections.
        Configures emergency alert thresholds and notification sounds.

        Side Effects:
            - Connects alert signals to UI handlers
            - Initializes alert storage structures
            - Loads persisted alerts from disk
        """
        use_subproc = self.main_ui.settings.get("alert_use_subprocess", True)
        self.main_ui.alert_manager = AlertManager(self.main_ui, self.main_ui.settings, use_subprocess=use_subproc)
        self.main_ui.alert_manager.new_alert.connect(self._on_new_alert)
        self.main_ui.alert_manager.emergency_alert.connect(self._on_emergency_alert)
        self.main_ui.alert_manager.fulemer_alert.connect(self._on_fulemer_alert)
        self.main_ui.alert_manager.alert_summary.connect(self._on_alert_summary)
        self.main_ui.alert_manager.poll_error.connect(lambda m: self.main_ui.log(f"Alert: {m}"))

        _icon_path = top_dir / 'public' / 'images' / 'Monwatch-LOGO.png'
        self.main_ui._tray_icon = QSystemTrayIcon(self.main_ui)
        if _icon_path.exists():
            self.main_ui._tray_icon.setIcon(QIcon(str(_icon_path)))
        else:
            self.main_ui._tray_icon.setIcon(self.main_ui.style().standardIcon(self.main_ui.style().SP_ComputerIcon))
        self.main_ui._tray_icon.setToolTip("MonWatch - Cyclone")
        self.main_ui._tray_icon.activated.connect(lambda reason: (
            self.main_ui.show(), self.main_ui.activateWindow(), self.main_ui.raise_(), self._show_alert_popup()
        ) if reason == QSystemTrayIcon.Trigger else (
            self.main_ui.show(), self.main_ui.activateWindow(), self.main_ui.raise_()
        ) if reason == QSystemTrayIcon.DoubleClick else None)
        self.main_ui._tray_icon.show()

        alert_interval = self.main_ui.settings.get("alert_poll_interval_min", 10)
        weather_alerts_enabled = self.main_ui.settings.get("weather_alerts_enabled", True)
        nws_enabled = self.main_ui.settings.get("nws_enabled", True)
        pagasa_enabled = self.main_ui.settings.get("pagasa_enabled", False)

        alert_sources = []
        if nws_enabled:
            alert_sources.append("NWS")
        if pagasa_enabled:
            alert_sources.append("PAGASA")

        self.main_ui.alert_manager.configure(
            marine_only=self.main_ui.settings.get("alert_marine_only", False),
            zone=self.main_ui.settings.get("alert_zone", ""),
            use_modern_notif=self.main_ui.settings.get("alert_sound_modern_notif", False),
            use_soft_emer=self.main_ui.settings.get("alert_sound_soft_emer", False),
            alert_sources=alert_sources,
        )
        if weather_alerts_enabled:
            self.main_ui.alert_manager.start(alert_interval)

        self.main_ui._alert_btn = QPushButton("\u26a1")
        self.main_ui._alert_btn.setFixedWidth(28)
        self.main_ui._alert_btn.setFixedHeight(20)
        self.main_ui._alert_btn.setStyleSheet(
            "QPushButton { background: #2a2a2a; color: #888; border: 1px solid #444; border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: #3a3a3a; color: #FFD700; }"
        )
        self.main_ui._alert_btn.setToolTip("No active alerts")
        self.main_ui._alert_btn.clicked.connect(self._show_alert_popup)
        self.main_ui.status_bar.addPermanentWidget(self.main_ui._alert_btn)

        self.main_ui._last_alerts = []
        self.main_ui._acknowledged_alert_ids = set()
        self.main_ui._acknowledged_alert_ids = _load_ack_ids()
        self._load_last_alerts()

        self.main_ui._expiry_cleanup_timer = QTimer(self.main_ui)
        self.main_ui._expiry_cleanup_timer.timeout.connect(self._cleanup_expired_alerts)
        self.main_ui._expiry_cleanup_timer.start(300000)

        self._alerts_save_timer = QTimer(self.main_ui)
        self._alerts_save_timer.setSingleShot(True)
        self._alerts_save_timer.setInterval(_ALERT_SAVE_DEBOUNCE_MS)
        self._alerts_save_timer.timeout.connect(self._save_last_alerts)

    def _create_alerts_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header = QLabel("WEATHER ALERTS")
        header.setStyleSheet("color: #FF9800; font-weight: bold; font-size: 11px;")
        header_row.addWidget(header)
        self.main_ui.alert_source_combo = QComboBox()
        self.main_ui.alert_source_combo.addItem("ALL")
        self.main_ui.alert_source_combo.addItem("NWS")
        self.main_ui.alert_source_combo.addItem("PAGASA")
        self.main_ui.alert_source_combo.setStyleSheet("QComboBox { background: #2D2D2D; color: #CCC; border: 1px solid #444; padding: 2px 4px; font-size: 9px; min-width: 70px; } QComboBox::drop-down { border: none; }")
        self.main_ui.alert_source_combo.currentTextChanged.connect(lambda _: self._apply_alert_filters())
        header_row.addWidget(self.main_ui.alert_source_combo)
        header_row.addStretch()
        layout.addLayout(header_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        filter_row.addWidget(QLabel("Status:"))
        self.main_ui.alert_read_filter = QComboBox()
        self.main_ui.alert_read_filter.addItems(["All", "Unread", "Read"])
        self.main_ui.alert_read_filter.currentTextChanged.connect(lambda _: self._apply_alert_filters())
        filter_row.addWidget(self.main_ui.alert_read_filter)
        filter_row.addWidget(QLabel("Severity:"))
        self.main_ui.alert_sev_filter = QComboBox()
        self.main_ui.alert_sev_filter.addItems(["All", "Extreme", "Severe", "Moderate", "Minor"])
        self.main_ui.alert_sev_filter.currentTextChanged.connect(lambda _: self._apply_alert_filters())
        filter_row.addWidget(self.main_ui.alert_sev_filter)
        filter_row.addStretch()
        layout.addLayout(filter_row)

        self.main_ui.alerts_list = QTreeWidget()
        self.main_ui.alerts_list.setHeaderHidden(True)
        self.main_ui.alerts_list.setStyleSheet("QTreeWidget { background: #1f1f1f; color: #ddd; border: 1px solid #444; } QTreeWidget::item { padding: 2px 0px; }")
        self.main_ui.alerts_list.setIndentation(20)
        self.main_ui.alerts_list.setExpandsOnDoubleClick(True)
        layout.addWidget(self.main_ui.alerts_list, 1)

        self._rebuild_alerts_list()

        btn_row = QHBoxLayout()
        disable_btn = QPushButton("Disable All")
        disable_btn.setStyleSheet("QPushButton { background: #5f1a1a; color: #FF6666; font-weight: bold; padding: 6px 10px; font-size: 9px; } QPushButton:hover { background: #7a2222; }")
        disable_btn.clicked.connect(lambda: self._set_all_alert_checks(False))
        btn_row.addWidget(disable_btn)
        enable_btn = QPushButton("Enable All")
        enable_btn.setStyleSheet("QPushButton { background: #1a3f1a; color: #66FF66; font-weight: bold; padding: 6px 10px; font-size: 9px; } QPushButton:hover { background: #2a5f2a; }")
        enable_btn.clicked.connect(lambda: self._set_all_alert_checks(True))
        btn_row.addWidget(enable_btn)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.setStyleSheet("QPushButton { background: #1e3a5f; color: #90d5ff; font-weight: bold; padding: 6px 14px; } QPushButton:hover { background: #2a4a7f; }")
        refresh_btn.clicked.connect(self.main_ui.alert_manager.poll_now)
        btn_row.addWidget(refresh_btn)
        acknowledge_btn = QPushButton("Acknowledge Selected")
        acknowledge_btn.setStyleSheet("QPushButton { background: #2a5f2a; color: #90EE90; font-weight: bold; padding: 6px 14px; } QPushButton:hover { background: #3a7a3a; }")
        acknowledge_btn.clicked.connect(self._acknowledge_selected)
        btn_row.addWidget(acknowledge_btn)
        gen_whole_btn = QPushButton("Generate Whole")
        gen_whole_btn.setStyleSheet("QPushButton { background: #3a5f1e; color: #b0ff90; font-weight: bold; padding: 6px 14px; } QPushButton:hover { background: #4a7f2a; }")
        gen_whole_btn.clicked.connect(self._generate_whole_alert_map)
        btn_row.addWidget(gen_whole_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._alerts_refresh_timer = QTimer(self)
        self._alerts_refresh_timer.setSingleShot(True)
        self._alerts_refresh_timer.timeout.connect(self._populate_alerts_list)
        self.main_ui.alert_manager.new_alert.connect(lambda a: self._alerts_refresh_timer.start(500))
        self.main_ui.alert_manager.emergency_alert.connect(lambda a: self._alerts_refresh_timer.start(500))
        self.main_ui.alert_manager.fulemer_alert.connect(lambda a: self._alerts_refresh_timer.start(500))

        return container

    def _apply_alert_filters(self):
        if not hasattr(self.main_ui, 'alerts_list') or not hasattr(self.main_ui, 'alert_read_filter'):
            return
        read_filter = self.main_ui.alert_read_filter.currentText()
        sev_filter = self.main_ui.alert_sev_filter.currentText()
        source_filter = self.main_ui.alert_source_combo.currentText()
        for i in range(self.main_ui.alerts_list.topLevelItemCount()):
            item = self.main_ui.alerts_list.topLevelItem(i)
            aid = item.data(0, Qt.UserRole)
            sev = item.data(0, Qt.UserRole + 1)
            src = item.data(0, Qt.UserRole + 2)
            acknowledged = aid in getattr(self.main_ui, '_acknowledged_alert_ids', set())
            hidden = False
            if read_filter == "Read" and not acknowledged:
                hidden = True
            elif read_filter == "Unread" and acknowledged:
                hidden = True
            if sev_filter != "All" and sev != sev_filter:
                hidden = True
            if source_filter != "ALL" and src != source_filter:
                hidden = True
            item.setHidden(hidden)

    def _build_alert_item(self, a):
        t = getattr(self.main_ui, '_theme', None)
        if not t or not isinstance(t, dict) or 'fg2' not in t:
            t = THEMES["Dark (Default)"]
        sev = a.get("severity", "Unknown")
        aid = a.get("id", "")
        event = a.get("event", "Alert")
        headline = a.get("headline", "")
        source = a.get("source", "NWS")
        acknowledged = aid in getattr(self.main_ui, '_acknowledged_alert_ids', set())
        sev_color = {"Extreme": "#AA00FF", "Severe": "#FF0000", "Moderate": "#FF8800", "Minor": "#FFDD00"}.get(sev, "#888")
        item = QTreeWidgetItem()
        item.setData(0, Qt.UserRole, aid)
        item.setData(0, Qt.UserRole + 1, sev)
        item.setData(0, Qt.UserRole + 2, source)
        wid = QWidget()
        wl = QHBoxLayout(wid)
        wl.setContentsMargins(4, 1, 4, 1)
        wl.setSpacing(4)
        cb = QCheckBox()
        cb.setChecked(False)
        cb.setStyleSheet("QCheckBox::indicator { width: 14px; height: 14px; }")
        wl.addWidget(cb)
        display = f"[{sev}] {event}"
        nl = QLabel(display)
        nl.setObjectName("alert_sev_label")
        nl.setStyleSheet(f"color: {sev_color}; font-size: 10px; font-weight: bold;")
        wl.addWidget(nl)
        hl = QLabel(headline[:80])
        hl.setObjectName("alert_headline")
        hl.setStyleSheet(f"color: {t['fg3']}; font-size: 9px;")
        hl.setWordWrap(False)
        wl.addWidget(hl)
        wl.addStretch()
        tts_btn = QPushButton("\U0001f50a")
        tts_btn.setFixedHeight(22)
        tts_btn.setFixedWidth(30)
        tts_btn.setToolTip("Read alert aloud (TTS)")
        tts_btn.setStyleSheet(f"QPushButton {{ background: {t['bg3']}; color: #4CAF50; border: 1px solid {t['border2']}; border-radius: 3px; font-size: 12px; }} QPushButton:hover {{ background: {t['menusel']}; color: #8BC34A; }}")
        tts_btn.clicked.connect(lambda checked=False, aa=a: self._speak_alert(aa))
        wl.addWidget(tts_btn)
        detail_btn = QPushButton("Details")
        detail_btn.setFixedHeight(22)
        detail_btn.setStyleSheet(f"QPushButton {{ background: {t['bg3']}; color: {t['fg']}; font-size: 9px; padding: 1px 6px; border: 1px solid {t['border2']}; }} QPushButton:hover {{ background: {t['menusel']}; }}")
        detail_btn.clicked.connect(lambda checked=False, aa=a: self._show_alert_detail(aa))
        wl.addWidget(detail_btn)
        gen_btn = QPushButton("Generate")
        gen_btn.setFixedHeight(22)
        gen_btn.setStyleSheet(f"QPushButton {{ background: {t['bg3']}; color: {t['fg']}; font-size: 9px; padding: 1px 6px; border: 1px solid {t['border2']}; }} QPushButton:hover {{ background: {t['menusel']}; }}")
        gen_btn.clicked.connect(lambda checked=False, aa=a: self._generate_single_alert_map(aa))
        wl.addWidget(gen_btn)
        if acknowledged:
            item.setForeground(0, QColor(t['fg3']))
            item.setDisabled(True)
            wid.setStyleSheet(f"QWidget {{ background: {t['bg2']}; }} QLabel {{ color: {t['border3']} !important; }} QPushButton {{ color: {t['border3']} !important; background: {t['bg3']} !important; }}")
        item.setSizeHint(0, QSize(0, 24))
        return item, wid

    def _restyle_alert_items(self):
        t = getattr(self.main_ui, '_theme', None)
        if not t or not isinstance(t, dict) or 'fg2' not in t:
            t = THEMES["Dark (Default)"]
        if not hasattr(self.main_ui, 'alerts_list'):
            return
        for i in range(self.main_ui.alerts_list.topLevelItemCount()):
            item = self.main_ui.alerts_list.topLevelItem(i)
            wid = self.main_ui.alerts_list.itemWidget(item, 0)
            if not wid:
                continue
            aid = item.data(0, Qt.UserRole)
            acknowledged = aid in getattr(self.main_ui, '_acknowledged_alert_ids', set())
            hl = wid.findChild(QLabel, "alert_headline")
            if hl:
                hl.setStyleSheet(f"color: {t['fg3']}; font-size: 9px;")
            for child in wid.findChildren(QPushButton):
                txt = child.text()
                if txt == "\U0001f50a":
                    child.setStyleSheet(f"QPushButton {{ background: {t['bg3']}; color: #4CAF50; border: 1px solid {t['border2']}; border-radius: 3px; font-size: 12px; }} QPushButton:hover {{ background: {t['menusel']}; color: #8BC34A; }}")
                else:
                    child.setStyleSheet(f"QPushButton {{ background: {t['bg3']}; color: {t['fg']}; font-size: 9px; padding: 1px 6px; border: 1px solid {t['border2']}; }} QPushButton:hover {{ background: {t['menusel']}; }}")
            if acknowledged:
                wid.setStyleSheet(f"QWidget {{ background: {t['bg2']}; }} QLabel {{ color: {t['border3']} !important; }} QPushButton {{ color: {t['border3']} !important; background: {t['bg3']} !important; }}")
                item.setForeground(0, QColor(t['fg3']))

    def _rebuild_alerts_list(self):
        if not hasattr(self.main_ui, 'alerts_list'):
            return
        self.main_ui.alerts_list.blockSignals(True)
        self.main_ui.alerts_list.clear()
        sev_rank = {"Extreme": 4, "Severe": 3, "Moderate": 2, "Minor": 1, "Unknown": 0}
        sorted_alerts = sorted(
            getattr(self.main_ui, '_last_alerts', []),
            key=lambda a: (sev_rank.get(a.get("severity", ""), 0), a.get("effective", "")),
            reverse=True,
        )
        for a in sorted_alerts:
            item, wid = self._build_alert_item(a)
            self.main_ui.alerts_list.addTopLevelItem(item)
            self.main_ui.alerts_list.setItemWidget(item, 0, wid)
        self.main_ui.alerts_list.blockSignals(False)
        self._apply_alert_filters()

    def _populate_alerts_list(self):
        self._rebuild_alerts_list()

    def _last_alerts_path(self):
        return top_dir / "cache" / "last_alerts.json"

    def _retention_days(self):
        try:
            return max(0, int(self.main_ui.settings.get("alert_retention_days", _ALERT_RETENTION_DAYS)))
        except Exception:
            return _ALERT_RETENTION_DAYS

    def _is_stale(self, alert, days=None):
        if days is None:
            days = self._retention_days()
        if is_expired(alert):
            return True
        if alert.get("expires", ""):
            return False
        return is_old(alert, days=days)

    def _load_last_alerts(self):
        p = self._last_alerts_path()
        try:
            if p.exists():
                with open(p) as f:
                    raw = json.load(f)
                    if isinstance(raw, list):
                        days = self._retention_days()
                        self.main_ui._last_alerts = [
                            a for a in raw if not self._is_stale(a, days=days)
                        ]
                        self._schedule_save_last_alerts()
        except Exception:
            pass

    def _save_last_alerts(self, sync=False):
        p = self._last_alerts_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            data = [_trim_alert(a) for a in self.main_ui._last_alerts]
            text = json.dumps(data)
            pool = getattr(self.main_ui, '_thread_pool', None)
            if sync or pool is None:
                _write_alerts_json(p, text)
            else:
                pool.submit(_write_alerts_json, p, text)
        except Exception:
            pass

    def _schedule_save_last_alerts(self):
        if hasattr(self, '_alerts_save_timer'):
            self._alerts_save_timer.start()
        else:
            self._save_last_alerts()

    def _cleanup_expired_alerts(self):
        before = len(self.main_ui._last_alerts)
        days = self._retention_days()
        self.main_ui._last_alerts = [
            a for a in self.main_ui._last_alerts if not self._is_stale(a, days=days)
        ]
        if len(self.main_ui._last_alerts) != before:
            self._rebuild_alerts_list()
            self._save_last_alerts()

    def _on_new_alert(self, alert):

        """Handle incoming alert notification.

        Processes new alert data from agency sources and updates
        the alert display. Triggers notification if alert meets
        severity thresholds.

        Args:
            alert_data (dict): Alert information from agency API.

        Side Effects:
            - Updates alert list UI
            - May trigger audio notification
            - Persists alert to disk
        """
        aid = alert.get("id", "")
        for i, existing in enumerate(self.main_ui._last_alerts):
            if existing.get("id") == aid:
                self.main_ui._last_alerts[i] = alert
                break
        else:
            self.main_ui._last_alerts.insert(0, alert)
        days = self._retention_days()
        self.main_ui._last_alerts = [a for a in self.main_ui._last_alerts if not self._is_stale(a, days=days)]
        if len(self.main_ui._last_alerts) > 200:
            self.main_ui._last_alerts = self.main_ui._last_alerts[:200]
        self._schedule_save_last_alerts()
        headline = alert.get("headline", alert.get("event", "Unknown alert"))
        source = alert.get("source", "NWS")
        self.main_ui.log(f"{source} Alert: {headline}")
        sev = alert.get("severity", "")
        if sev == "Minor":
            return
        self.main_ui.status_bar.showMessage(f"{headline}", 8000)
        self.new_alert.emit(alert)

    def _on_emergency_alert(self, alert):

        """Handle emergency-level alert.

        Displays emergency popup with high-visibility styling and
        audio notification. Emergency alerts bypass normal filtering
        and are always shown to the user.

        Args:
            alert_data (dict): Emergency alert information.

        Side Effects:
            - Shows emergency popup dialog
            - Plays emergency alert sound
            - Logs emergency event
        """
        headline = alert.get("headline", alert.get("event", "EMERGENCY ALERT"))
        source = alert.get("source", "NWS")
        self.main_ui.log(f"{source} EMERGENCY: {headline}")
        self.main_ui.status_bar.showMessage(f"EMERGENCY: {headline}", 15000)
        self.main_ui._tray_icon.showMessage(
            "EMERGENCY ALERT", headline,
            QSystemTrayIcon.Critical, 15000
        )
        self.main_ui._alert_btn.setStyleSheet(
            "QPushButton { background: #5f1a1a; color: #FF4444; border: 1px solid #FF4444; border-radius: 3px; font-size: 10px; font-weight: bold; }"
            "QPushButton:hover { background: #7a2222; }"
        )
        QTimer.singleShot(0, lambda: self._show_emergency_popup(alert, is_fulemer=False))
        self.emergency_alert.emit(alert)

    def _on_fulemer_alert(self, alert):
        headline = alert.get("headline", alert.get("event", "FULL EMERGENCY"))
        self.main_ui.log(f"FULL EMERGENCY: {headline}")
        self.main_ui.status_bar.showMessage(f"FULL EMERGENCY: {headline}", 20000)
        self.main_ui._tray_icon.showMessage(
            "FULL EMERGENCY", headline,
            QSystemTrayIcon.Critical, 20000
        )
        self.main_ui._alert_btn.setStyleSheet(
            "QPushButton { background: #8B0000; color: #FF0000; border: 2px solid #FF0000; border-radius: 3px; font-size: 10px; font-weight: bold; }"
            "QPushButton:hover { background: #AA0000; }"
        )
        QTimer.singleShot(0, lambda: self._show_emergency_popup(alert, is_fulemer=True))

    def _set_all_alert_checks(self, checked):
        for i in range(self.main_ui.alerts_list.topLevelItemCount()):
            item = self.main_ui.alerts_list.topLevelItem(i)
            wid = self.main_ui.alerts_list.itemWidget(item, 0)
            if wid:
                cb = wid.findChild(QCheckBox)
                if cb:
                    cb.setChecked(checked)

    def _acknowledge_selected(self):

        """Acknowledge selected alerts.

        Marks selected alerts as acknowledged and updates their
        visual state. Acknowledged alerts are dimmed but remain
        in the list for audit purposes.

        Side Effects:
            - Updates alert acknowledgment status
            - Refreshes alert list display
            - Persists acknowledgment to disk
        """
        stop_sounds()
        self.main_ui._alert_btn.setStyleSheet(
            "QPushButton { background: #2a2a2a; color: #888; border: 1px solid #444; border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: #3a3a3a; color: #FFD700; }"
        )
        new_ack = False
        for i in range(self.main_ui.alerts_list.topLevelItemCount()):
            item = self.main_ui.alerts_list.topLevelItem(i)
            wid = self.main_ui.alerts_list.itemWidget(item, 0)
            if wid:
                cb = wid.findChild(QCheckBox)
                if cb and cb.isChecked():
                    aid = item.data(0, Qt.UserRole)
                    if aid and aid not in self.main_ui._acknowledged_alert_ids:
                        self.main_ui._acknowledged_alert_ids.add(aid)
                        new_ack = True
                    item.setForeground(0, QColor("#555"))
                    item.setDisabled(True)
                    wid.setStyleSheet("QWidget { background: #1a1a1a; } QLabel { color: #555 !important; } QPushButton { color: #444 !important; background: #222 !important; }")
        if new_ack:
            _save_ack_ids(self.main_ui._acknowledged_alert_ids)
        self.main_ui.log("Selected alerts acknowledged")

    def _acknowledge_all_alerts(self):
        stop_sounds()
        self.main_ui._alert_btn.setStyleSheet(
            "QPushButton { background: #2a2a2a; color: #888; border: 1px solid #444; border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: #3a3a3a; color: #FFD700; }"
        )
        new_ack = False
        for i in range(self.main_ui.alerts_list.topLevelItemCount()):
            item = self.main_ui.alerts_list.topLevelItem(i)
            wid = self.main_ui.alerts_list.itemWidget(item, 0)
            if wid:
                aid = item.data(0, Qt.UserRole)
                if aid and aid not in self.main_ui._acknowledged_alert_ids:
                    self.main_ui._acknowledged_alert_ids.add(aid)
                    new_ack = True
                item.setForeground(0, QColor("#555"))
                item.setDisabled(True)
                wid.setStyleSheet("QWidget { background: #1a1a1a; } QLabel { color: #555 !important; } QPushButton { color: #444 !important; background: #222 !important; }")
        if new_ack:
            _save_ack_ids(self.main_ui._acknowledged_alert_ids)
        self.main_ui.log("All alerts acknowledged")

    def _on_alert_summary(self, summary):
        self.main_ui._alert_btn.setToolTip(summary)
        if "No active" in summary:
            self.main_ui._alert_btn.setStyleSheet(
                "QPushButton { background: #2a2a2a; color: #888; border: 1px solid #444; border-radius: 3px; font-size: 10px; }"
                "QPushButton:hover { background: #3a3a3a; color: #FFD700; }"
            )

    def _apply_alert_settings(self):
        weather_alerts_enabled = self.main_ui.settings.get("weather_alerts_enabled", True)
        nws_enabled = self.main_ui.settings.get("nws_enabled", True)
        pagasa_enabled = self.main_ui.settings.get("pagasa_enabled", False)
        interval = self.main_ui.settings.get("alert_poll_interval_min", 10)
        marine = self.main_ui.settings.get("alert_marine_only", False)
        zone = self.main_ui.settings.get("alert_zone", "")
        use_modern = self.main_ui.settings.get("alert_sound_modern_notif", False)
        use_soft = self.main_ui.settings.get("alert_sound_soft_emer", False)

        alert_sources = []
        if nws_enabled:
            alert_sources.append("NWS")
        if pagasa_enabled:
            alert_sources.append("PAGASA")

        use_subproc = self.main_ui.settings.get("alert_use_subprocess", True)
        self.main_ui.alert_manager.configure(marine_only=marine, zone=zone,
                                     use_modern_notif=use_modern,
                                     use_soft_emer=use_soft,
                                     alert_sources=alert_sources,
                                     use_subprocess=use_subproc)
        if weather_alerts_enabled:
            self.main_ui.alert_manager.start(interval)
        else:
            self.main_ui.alert_manager.stop()

    def _show_alert_popup(self):
        if not self.main_ui._last_alerts:
            QMessageBox.information(self.main_ui, "Weather Alerts", self.main_ui._alert_btn.toolTip())
            return
        dlg = QDialog(self.main_ui)
        dlg.setWindowTitle("Weather Alerts")
        dlg.resize(600, 400)
        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        html = ""
        for a in reversed(self.main_ui._last_alerts[-20:]):
            sev = a.get("severity", "")
            sev_color = {"Extreme": "#AA00FF", "Severe": "#FF0000", "Moderate": "#FF8800", "Minor": "#FFDD00"}.get(sev, "#888")
            html += (
                f"<div style='border-left: 4px solid {sev_color}; padding: 6px; margin: 4px; background: #1e1e1e;'>"
                f"<b style='color: {sev_color};'>{a.get('event', 'Alert')}</b>"
                f"<span style='color: #aaa; margin-left: 12px;'>{sev}</span><br>"
                f"<span style='color: #ddd;'>{a.get('headline', '')}</span><br>"
                f"<span style='color: #888; font-size: 9px;'>{a.get('effective', '')[:19]} \u2014 {a.get('expires', '')[:19]}</span>"
                f"</div>"
            )
        text.setHtml(html)
        layout.addWidget(text)
        btn_row = QHBoxLayout()
        details_btn = QPushButton("Read Alerts")
        details_btn.setStyleSheet("QPushButton { background: #1e3a5f; color: #90d5ff; font-weight: bold; padding: 6px 14px; } QPushButton:hover { background: #2a4a7f; }")
        details_btn.clicked.connect(lambda: (dlg.accept(), self._switch_to_alerts_tab()))
        btn_row.addWidget(details_btn)
        ack_btn = QPushButton("Acknowledge All")
        ack_btn.setStyleSheet("QPushButton { background: #5f1a1a; color: #FF4444; font-weight: bold; padding: 6px 14px; } QPushButton:hover { background: #7a2222; }")
        ack_btn.clicked.connect(lambda: (dlg.accept(), self._acknowledge_all_alerts()))
        btn_row.addWidget(ack_btn)
        generate_btn = QPushButton("Generate")
        generate_btn.setStyleSheet("QPushButton { background: #6d28d9; color: #c4b5fd; font-weight: bold; padding: 6px 14px; } QPushButton:hover { background: #7c3aed; }")
        generate_btn.clicked.connect(lambda: (dlg.accept(), self._generate_alert_map()))
        btn_row.addWidget(generate_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        dlg.exec()

    def _show_emergency_popup(self, alert=None, is_fulemer=False):
        if getattr(self.main_ui, '_emergency_dialog_open', False):
            return
        self.main_ui._emergency_dialog_open = True
        dlg = QDialog(self.main_ui)
        dlg.setWindowTitle("EMERGENCY WEATHER ALERTS")
        dlg.resize(650, 450)
        dlg.setWindowFlags(dlg.windowFlags() | Qt.WindowStaysOnTopHint)
        dlg.finished.connect(lambda: setattr(self.main_ui, '_emergency_dialog_open', False))
        layout = QVBoxLayout(dlg)
        header_color = "#FF0000" if is_fulemer else "#FF4444"
        label_text = "FULL EMERGENCY" if is_fulemer else "EMERGENCY"
        header = QLabel(f'<h2 style="color: {header_color}; text-align: center;">\U0001F6A8 {label_text} ALERT IN EFFECT</h2>')
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)
        text = QTextEdit()
        text.setReadOnly(True)
        html = ""
        ack_ids = getattr(self.main_ui, '_acknowledged_alert_ids', set())
        if is_fulemer:
            extreme_alerts = [a for a in reversed(self.main_ui._last_alerts)
                              if a.get("severity", "") == "Extreme"
                              and a.get("id", "") not in ack_ids]
            if not extreme_alerts:
                extreme_alerts = [a for a in reversed(self.main_ui._last_alerts)
                                  if a.get("severity", "") == "Extreme"][:1]
            for a in extreme_alerts:
                sev = "Extreme"
                sev_color = "#AA00FF"
                event = a.get("event", "Alert")
                headline = a.get("headline", "")
                desc = a.get("description", "").replace("\n", "<br>")
                instruction = a.get("instruction", "").replace("\n", "<br>")
                effective = a.get("effective", "")[:19] if a.get("effective") else ""
                expires = a.get("expires", "")[:19] if a.get("expires") else ""
                sender = a.get("sender", "")
                areas = ", ".join(a.get("affected_zones", []))
                html += (
                    f"<div style='border-left: 4px solid {sev_color}; padding: 8px; margin: 4px; background: #1e1e1e;'>"
                    f"<b style='color: {sev_color}; font-size: 14px;'>{event}</b>"
                    f"<span style='color: #aaa; margin-left: 12px;'>{sev}</span><br>"
                    f"<span style='color: #ddd; font-size: 12px;'>{headline}</span><br><br>"
                    f"<span style='color: #bbb; font-size: 11px;'>{desc}</span><br><br>"
                )
                if instruction:
                    html += f"<span style='color: #ffaa66; font-size: 11px;'><b>Instructions:</b> {instruction}</span><br><br>"
                html += (
                    f"<span style='color: #888; font-size: 9px;'>Issued: {effective} | Expires: {expires}</span><br>"
                    f"<span style='color: #888; font-size: 9px;'>Sender: {sender} | Areas: {areas}</span>"
                    f"</div>"
                )
        else:
            emergency_sevs = {"Severe", "Extreme"}
            unread = [a for a in reversed(self.main_ui._last_alerts)
                      if a.get("id", "") not in ack_ids
                      and a.get("severity", "") in emergency_sevs][:10]
            if not unread:
                unread = [a for a in reversed(self.main_ui._last_alerts)
                          if a.get("severity", "") in emergency_sevs][:5]
            for a in unread:
                sev = a.get("severity", "")
                sev_color = {"Extreme": "#AA00FF", "Severe": "#FF0000", "Moderate": "#FF8800", "Minor": "#FFDD00"}.get(sev, "#888")
                html += (
                    f"<div style='border-left: 4px solid {sev_color}; padding: 6px; margin: 4px; background: #1e1e1e;'>"
                    f"<b style='color: {sev_color};'>{a.get('event', 'Alert')}</b>"
                    f"<span style='color: #aaa; margin-left: 12px;'>{sev}</span><br>"
                    f"<span style='color: #ddd;'>{a.get('headline', '')}</span><br>"
                    f"<span style='color: #888; font-size: 9px;'>{a.get('effective', '')[:19]} \u2014 {a.get('expires', '')[:19]}</span>"
                    f"</div>"
                )
        if not html:
            html = "<p style='color: #888; text-align: center;'>No unread alerts.</p>"
        text.setHtml(html)
        layout.addWidget(text)
        btn_row = QHBoxLayout()
        read_btn = QPushButton("Read All Alerts")
        read_btn.setStyleSheet("QPushButton { background: #1e3a5f; color: #90d5ff; font-weight: bold; padding: 8px 16px; } QPushButton:hover { background: #2a4a7f; }")
        read_btn.clicked.connect(lambda: (dlg.accept(), self._switch_to_alerts_tab()))
        btn_row.addWidget(read_btn)
        ack_btn = QPushButton("Acknowledge")
        ack_btn.setStyleSheet("QPushButton { background: #5f1a1a; color: #FF4444; font-weight: bold; padding: 8px 16px; } QPushButton:hover { background: #7a2222; }")
        ack_btn.clicked.connect(lambda: (dlg.accept(), self._acknowledge_all_alerts()))
        btn_row.addWidget(ack_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        flash_timer = QTimer(dlg)
        flash_count = 0
        flash_color = "#8B0000" if is_fulemer else "#5f1a1a"
        def flash():
            nonlocal flash_count
            flash_count += 1
            if flash_count % 2 == 0:
                dlg.setStyleSheet(f"QDialog {{ border: 4px solid {flash_color}; }}")
                dlg.setWindowOpacity(1.0)
            else:
                dlg.setStyleSheet(f"QDialog {{ border: 4px solid {flash_color}; background-color: {flash_color}; }}")
                dlg.setWindowOpacity(0.85)
            dlg.activateWindow()
            dlg.raise_()
            QApplication.alert(dlg, 0)
            if flash_count > 30:
                dlg.setStyleSheet("")
                dlg.setWindowOpacity(1.0)
                flash_timer.stop()
        flash_timer.timeout.connect(flash)
        flash_timer.start(500)
        dlg.exec()
        flash_timer.stop()
        dlg.setStyleSheet("")
        dlg.setWindowOpacity(1.0)

    def _switch_to_alerts_tab(self):
        if hasattr(self.main_ui, 'alerts_tab_index'):
            self.main_ui.right_tab_widget.setCurrentIndex(self.main_ui.alerts_tab_index)
            self._apply_alert_filters()

    @staticmethod
    def _make_pagasa_filename(alert):
        event = alert.get("event", "Weather") or "Weather"
        atype = event.split()[0].replace("/", "-").replace(" ", "_")
        sev = alert.get("severity", "Moderate") or "Moderate"
        pub = (alert.get("published_by") or alert.get("sender") or "").strip().upper().replace("-", "").replace(" ", "")
        region = pub if pub else "Unknown"
        eff = alert.get("effective") or alert.get("issued_date") or ""
        try:
            dt_str = eff.replace("Z", "+00:00")
            dt = datetime.fromisoformat(dt_str)
            if dt.tzinfo:
                pht = timezone(timedelta(hours=8))
                dt = dt.astimezone(pht)
            ts = dt.strftime("%Y%m%d%H%M")
        except Exception:
            try:
                dt = datetime.strptime(eff.replace("T", " ")[:19], "%Y-%m-%d %H:%M:%S")
                ts = dt.strftime("%Y%m%d%H%M")
            except Exception:
                ts = datetime.now().strftime("%Y%m%d%H%M")
        return f"{atype}-{sev}-{region}-{ts}.png"

    def _generate_alert_map(self):

        """Generate alert map visualization.

        Creates map overlay showing alert areas with color-coded
        severity levels. Uses agency-provided shapefiles or
        generates geometric alert zones.

        Returns:
            QImage: Rendered alert map overlay.

        Note:
            Supports both polygon-based and radius-based alert zones.
        """
        export_dir = self.main_ui.settings.get("export_folder", str(top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        alerts_data = getattr(self.main_ui, '_last_alerts', [])
        first = next((a for a in alerts_data if a.get("event")), None) or (alerts_data[0] if alerts_data else None)
        if first and first.get("source") == "PAGASA":
            fname = self._make_pagasa_filename(first)
        else:
            fname = f"alerts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        settings_path = str(self.main_ui.settings.settings_file) if hasattr(self.main_ui.settings, 'settings_file') else ""
        logo_path = str(top_dir / "public" / "images" / "Monwatch-LOGO.png")

        worker = AlertMapWorker(
            alerts=alerts_data,
            output_path=fpath,
            settings_path=settings_path,
            logo_path=logo_path,
            light=True,
            force_philippines=False,
        )
        worker.map_generated.connect(lambda out: self.main_ui.log(f"Alert map generated: {out}"))
        worker.map_failed.connect(lambda err: self.main_ui.log(f"Alert map failed: {err}"))
        worker.finished.connect(lambda: (self.main_ui.map_progress.hide(), setattr(self.main_ui, '_alert_map_worker', None)))
        self.main_ui._alert_map_worker = worker
        self.main_ui.map_progress.show()
        worker.start()
        self.main_ui.log(f"Generating alert map -> {os.path.basename(fpath)}")

    def _cleanup_temp(self, tmp_path):
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass

    def _show_alert_detail(self, alert):
        dlg = QDialog(self.main_ui)
        dlg.setWindowTitle(alert.get("event", "Alert Detail"))
        dlg.resize(550, 350)
        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        sev = alert.get("severity", "")
        sev_color = {"Extreme": "#AA00FF", "Severe": "#FF0000", "Moderate": "#FF8800", "Minor": "#FFDD00"}.get(sev, "#888")
        html = (
            f"<div style='padding: 8px; background: #1e1e1e;'>"
            f"<h2 style='color: {sev_color}; margin: 0;'>{alert.get('event', 'Alert')}</h2>"
            f"<p style='color: #aaa;'><b>Severity:</b> {sev}  |  <b>Urgency:</b> {alert.get('urgency', '')}  |  <b>Certainty:</b> {alert.get('certainty', '')}</p>"
            f"<hr style='border-color: #444;'>"
            f"<p style='color: #ddd; font-size: 11pt;'>{alert.get('headline', '')}</p>"
            f"<p style='color: #bbb;'>{alert.get('description', '').replace(chr(10), '<br>')}</p>"
            f"<hr style='border-color: #444;'>"
            f"<p style='color: #FFD700;'><b>Instructions:</b> {alert.get('instruction', 'N/A').replace(chr(10), '<br>')}</p>"
            f"<p style='color: #888; font-size: 9px;'>"
            f"Effective: {alert.get('effective', '')[:19]}  |  Expires: {alert.get('expires', '')[:19]}"
            f"<br>Sender: {alert.get('sender', '')}</p>"
            f"</div>"
        )
        text.setHtml(html)
        layout.addWidget(text)
        btn_row = QHBoxLayout()
        gen_btn = QPushButton("Generate")
        gen_btn.setStyleSheet("QPushButton { background: #3a5f1e; color: #b0ff90; font-weight: bold; padding: 6px 14px; } QPushButton:hover { background: #4a7f2a; }")
        gen_btn.clicked.connect(lambda: (dlg.accept(), self._generate_single_alert_map(alert)))
        btn_row.addWidget(gen_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        dlg.exec()

    def _speak_alert(self, alert):
        speak_alert(alert, mode="full")
        self.main_ui.log(f"TTS: Reading alert - {alert.get('event', 'Alert')}")

    def _generate_single_alert_map(self, alert):
        export_dir = self.main_ui.settings.get("export_folder", str(top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        if alert.get("source") == "PAGASA":
            fname = self._make_pagasa_filename(alert)
        else:
            fname = f"alert_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        settings_path = str(self.main_ui.settings.settings_file) if hasattr(self.main_ui.settings, 'settings_file') else ""
        logo_path = str(top_dir / "public" / "images" / "Monwatch-LOGO.png")

        worker = AlertMapWorker(
            alerts=[alert],
            output_path=fpath,
            settings_path=settings_path,
            logo_path=logo_path,
            light=True,
            force_philippines=False,
        )
        worker.map_generated.connect(lambda out: self.main_ui.log(f"Alert map generated: {out}"))
        worker.map_failed.connect(lambda err: self.main_ui.log(f"Alert map failed: {err}"))
        worker.finished.connect(lambda: (self.main_ui.map_progress.hide(), setattr(self.main_ui, '_single_alert_worker', None)))
        self.main_ui._single_alert_worker = worker
        self.main_ui.map_progress.show()
        worker.start()
        self.main_ui.log(f"Generating alert map -> {os.path.basename(fpath)}")

    def _get_himawari_product_for_alert(self, alert):
        source = alert.get("source", "NWS")
        if source == "PAGASA":
            return "FLDK"
        elif alert.get("source") in ("JMA", "JTWC") or "Japan" in alert.get("headline", ""):
            return "Japan"
        return "FLDK"

    def _generate_whole_alert_map(self):
        export_dir = self.main_ui.settings.get("export_folder", str(top_dir / "Exports"))
        os.makedirs(export_dir, exist_ok=True)
        alerts = getattr(self.main_ui, '_last_alerts', [])
        first = next((a for a in alerts if a.get("event")), None) or (alerts[0] if alerts else None)
        if first and first.get("source") == "PAGASA":
            fname = self._make_pagasa_filename(first)
        else:
            fname = f"alert_whole_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        fpath = os.path.join(export_dir, fname)
        settings_path = str(self.main_ui.settings.settings_file) if hasattr(self.main_ui.settings, 'settings_file') else ""
        logo_path = str(top_dir / "public" / "images" / "Monwatch-LOGO.png")

        worker = AlertMapWorker(
            alerts=alerts,
            output_path=fpath,
            settings_path=settings_path,
            logo_path=logo_path,
            light=True,
            force_philippines=True,
        )
        worker.map_generated.connect(lambda out: self.main_ui.log(f"Whole alert map generated: {out}"))
        worker.map_failed.connect(lambda err: self.main_ui.log(f"Whole alert map failed: {err}"))
        worker.finished.connect(lambda: (self.main_ui.map_progress.hide(), setattr(self.main_ui, '_whole_alert_worker', None)))
        self.main_ui._whole_alert_worker = worker
        self.main_ui.map_progress.show()
        worker.start()
        self.main_ui.log(f"Generating whole alert map -> {os.path.basename(fpath)}")
