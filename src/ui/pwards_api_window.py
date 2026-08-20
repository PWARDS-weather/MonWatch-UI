import logging
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
    QGroupBox, QGridLayout, QPushButton, QMessageBox, QFormLayout,
)

log = logging.getLogger(__name__)


class PWardsApiWindow(QWidget):
    def __init__(self, parent, settings_mgr):
        super().__init__(parent, Qt.Window)
        self.settings = settings_mgr
        self.setWindowTitle("PWARDS Stream API")
        self.setFixedSize(480, 300)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("http://your-server:5000")
        form.addRow("Base URL:", self.base_url_edit)

        self.api_code_edit = QLineEdit()
        self.api_code_edit.setEchoMode(QLineEdit.Password)
        self.api_code_edit.setPlaceholderText("API access code")
        form.addRow("API Code:", self.api_code_edit)

        self.poll_interval_spin = QSpinBox()
        self.poll_interval_spin.setRange(10, 600)
        self.poll_interval_spin.setValue(60)
        self.poll_interval_spin.setSuffix(" sec")
        form.addRow("Poll Interval:", self.poll_interval_spin)

        self.enabled_check = QPushButton()
        self.enabled_check.setCheckable(True)
        self.enabled_check.setText("Stream Disabled")
        self.enabled_check.setStyleSheet(
            "QPushButton:checked { background: #2e7d32; color: white; font-weight: bold; }"
            "QPushButton:!checked { background: #444; color: #aaa; }"
        )
        form.addRow("Status:", self.enabled_check)

        layout.addLayout(form)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

        btn_row = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.test_btn)

        btn_row.addStretch()

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply)
        btn_row.addWidget(apply_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

        self._load_settings()

    def _load_settings(self):
        cfg = self.settings.get("pwards_api", {})
        self.base_url_edit.setText(cfg.get("base_url", ""))
        self.api_code_edit.setText(cfg.get("api_code", ""))
        self.poll_interval_spin.setValue(cfg.get("poll_interval_sec", 60))
        enabled = cfg.get("enabled", False)
        self.enabled_check.setChecked(enabled)
        self.enabled_check.setText("Stream Enabled" if enabled else "Stream Disabled")

    def _apply(self):
        self.settings.set("pwards_api", {
            "base_url": self.base_url_edit.text().strip(),
            "api_code": self.api_code_edit.text().strip(),
            "poll_interval_sec": self.poll_interval_spin.value(),
            "enabled": self.enabled_check.isChecked(),
        })
        self.status_label.setText("Settings saved.")
        self.status_label.setStyleSheet("color: #4CAF50;")
        parent = self.parent()
        if hasattr(parent, '_on_pwards_config_changed'):
            parent._on_pwards_config_changed()

    def _test_connection(self):
        from ..clients.pwards_client import fetch_manifest
        base_url = self.base_url_edit.text().strip()
        api_code = self.api_code_edit.text().strip()
        if not base_url:
            QMessageBox.warning(self, "Missing URL", "Enter the PWARDS API base URL first.")
            return
        self.status_label.setText("Testing...")
        self.status_label.setStyleSheet("color: gray;")
        self.test_btn.setEnabled(False)
        try:
            result = fetch_manifest(base_url, api_code)
            if result is not None:
                slots = len(result.get("available", []))
                latest = result.get("latest_time", "?")
                self.status_label.setText(
                    f"Connected! {slots} slot(s), latest: {latest}"
                )
                self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
            else:
                self.status_label.setText("Connection failed — check URL and code.")
                self.status_label.setStyleSheet("color: red;")
        except Exception as e:
            self.status_label.setText(f"Error: {e}")
            self.status_label.setStyleSheet("color: red;")
        self.test_btn.setEnabled(True)
