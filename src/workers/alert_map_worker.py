# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: workers/alert_map_worker.py
# Description: Background worker for asynchronous task processing.
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

from PySide6.QtCore import QThread, Signal


class AlertMapWorker(QThread):
    map_generated = Signal(str)
    map_failed = Signal(str)

    def __init__(self, alerts, output_path, settings_path=None, logo_path=None, light=False, force_philippines=False):
        super().__init__()
        self.alerts = alerts
        self.output_path = output_path
        self.settings_path = settings_path
        self.logo_path = logo_path
        self.light = light
        self._force_philippines = force_philippines

    def run(self):
        try:
            import matplotlib
            matplotlib.use("Agg")
            import sys
            from pathlib import Path
            process_dir = str(Path(__file__).resolve().parent.parent.parent / "Process")
            if process_dir not in sys.path:
                sys.path.insert(0, process_dir)
            from Process.alert.common import make_alert_map
            import logging
            logging.getLogger(__name__).info(f"Worker started: {self.output_path}")
            result = make_alert_map(
                self.alerts,
                settings=self.settings_path,
                logo_path=self.logo_path,
                light=self.light,
                filename=self.output_path,
                force_philippines=self._force_philippines,
            )
            logging.getLogger(__name__).info(f"Worker completed: {self.output_path}, result={result}")
            if result:
                self.map_generated.emit(self.output_path)
            else:
                self.map_failed.emit("make_alert_map returned None")
        except Exception as e:
            import logging, traceback
            logging.getLogger(__name__).error(f"Worker failed: {e}\n{traceback.format_exc()}")
            self.map_failed.emit(str(e))

    def __del__(self):
        if self.isRunning():
            self.wait(5000)
