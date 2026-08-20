# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# =============================================================================
# Module: ui/ReconWindow.py
# Description: Detached, non-topmost live status window for tracked
#   weather-reconnaissance aircraft. Lists every aircraft currently in the
#   scan with its first-seen time, current lat/lon and flight status.
#   Kept deliberately simple and independent of the main window so it can be
#   moved / left open while the main window stays in the foreground.
# =============================================================================

from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class ReconWindow(QWidget):
    """Detached live table of tracked weather-recon aircraft.

    A plain top-level window (never setAlwaysOnTop, no special flags) that the
    operator can position anywhere. ``update_rows`` is called by the main UI
    after every live scan.
    """

    COLUMNS = ["Aircraft", "Callsign", "Status", "First Seen (UTC)", "Last Fix (UTC)",
               "Lat", "Lon", "Alt (ft)", "Speed (kt)", "Country"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recon Aircraft — Live")
        self.resize(980, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._header = QLabel("No recon scan yet — enable \"Recon Planes\" in the main window.")
        self._header.setStyleSheet("color: #B0BEC5; font-size: 11px;")
        layout.addWidget(self._header)

        self._table = QTableWidget(0, len(self.COLUMNS))
        self._table.setHorizontalHeaderLabels(self.COLUMNS)
        self._table.setAlternatingRowColors(True)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 220)
        self._table.setColumnWidth(1, 90)
        self._table.setColumnWidth(2, 90)
        self._table.setColumnWidth(3, 150)
        self._table.setColumnWidth(4, 150)
        self._table.setStyleSheet(
            "QTableWidget { background: #1E1E2E; color: #E0E0E0; gridline-color: #333;"
            "  border: 1px solid #3A3A3A; font-size: 11px; }"
            "QHeaderView::section { background: #2D2D3D; color: #90CAF9; padding: 4px;"
            "  border: 1px solid #3A3A3A; font-weight: bold; }"
            "QTableWidget::item { padding: 2px 4px; }"
            "QTableWidget::item:selected { background: #264653; }"
        )
        layout.addWidget(self._table)

        self._status_bar = QLabel("")
        self._status_bar.setStyleSheet("color: #78909C; font-size: 10px;")
        layout.addWidget(self._status_bar)

        self.setStyleSheet("QWidget { background: #1E1E2E; }")

    def clear_rows(self):
        self._table.setRowCount(0)

    def update_rows(self, aircraft, tracks=None, last_scan=None, source=None, scan_msg=None):
        """Refresh the table from a recon scan result.

        Parameters
        ----------
        aircraft : list[dict]
            Normalized recon records (hex, callsign, registration, lat, lon,
            alt_ft, speed_kt, track_deg, on_ground, updated, label, country).
        tracks : dict, optional
            hex -> list of {"lat":.., "lon":.., "t":iso-utc} history points.
        last_scan : datetime, optional
            UTC timestamp of the last completed scan.
        source : str, optional
            Data source name ("adsb.fi", "OpenSky", ...).
        """
        aircraft = aircraft or []
        tracks = tracks or {}
        now = datetime.now(timezone.utc)

        if last_scan is None and not aircraft:
            self._header.setText("No recon aircraft currently airborne.")
            self.clear_rows()
            self._status_bar.setText("")
            return

        self._table.setRowCount(len(aircraft))
        for row, ac in enumerate(aircraft):
            hex_id = ac.get("hex") or ""
            history = tracks.get(hex_id) or []
            first_seen = ""
            if history and history[0].get("t"):
                try:
                    first_seen = datetime.fromisoformat(history[0]["t"]).strftime("%H:%M:%S")
                except Exception:
                    first_seen = str(history[0].get("t"))[:19]

            last_fix = ac.get("updated") or ""
            if last_fix:
                try:
                    last_fix = datetime.fromisoformat(last_fix).strftime("%H:%M:%S")
                except Exception:
                    pass

            lat = ac.get("lat")
            lon = ac.get("lon")
            lat_s = f"{lat:.3f}" if lat is not None else "--"
            lon_s = f"{lon:.3f}" if lon is not None else "--"
            alt = ac.get("alt_ft")
            alt_s = f"{alt:,.0f}" if alt is not None else "--"
            speed = ac.get("speed_kt")
            speed_s = f"{speed:.0f}" if speed is not None else "--"

            status, status_color = self._status_for(ac, now)

            label = ac.get("label") or ac.get("callsign") or hex_id or "--"
            callsign = (ac.get("callsign") or "").strip() or "--"
            country = ac.get("country") or "--"

            row_items = [label, callsign, status, first_seen, last_fix,
                         lat_s, lon_s, alt_s, speed_s, country]
            for col, text in enumerate(row_items):
                item = QTableWidgetItem(text)
                if col == 2:
                    item.setForeground(QColor(status_color))
                    item.setTextAlignment(Qt.AlignCenter)
                if col in (5, 6):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._table.setItem(row, col, item)

        if aircraft:
            self._header.setText(f"{len(aircraft)} weather-recon aircraft tracked")
        else:
            self._header.setText("No recon aircraft currently airborne.")

        src = f"  •  Source: {source}" if source else ""
        if last_scan is not None:
            ts = last_scan.strftime("%H:%M:%S UTC") if hasattr(last_scan, "strftime") else str(last_scan)
            self._status_bar.setText(f"Last scan: {ts}{src}  •  Auto-refresh every 60 s")
        else:
            self._status_bar.setText(src.strip("  •  "))
        if scan_msg:
            self._status_bar.setText(f"{scan_msg}  •  {self._status_bar.text()}")

    @staticmethod
    def _status_for(ac, now):
        """Return (status_text, color_hex) for an aircraft record."""
        if ac.get("on_ground"):
            return "ON GROUND", "#FFB74D"
        if ac.get("lat") is None or ac.get("lon") is None:
            return "NO FIX", "#EF5350"
        updated = ac.get("updated")
        if updated:
            try:
                dt = datetime.fromisoformat(updated)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).total_seconds() > 300:
                    return "STALE", "#EF5350"
            except Exception:
                pass
        return "IN FLIGHT", "#66BB6A"
