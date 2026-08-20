from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QWidget, QMessageBox,
)


class DownloaderSelector(QDialog):
    def __init__(self, parent=None, settings_mgr=None):
        super().__init__(parent)
        self.settings = settings_mgr
        self.selected = None
        self.setWindowTitle("Which Downloader?")
        self.setFixedSize(480, 360)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel("Which Downloader?")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 8px;")
        layout.addWidget(title)

        self.aws_btn = self._make_card(
            "AWS (S3) (Free)",
            "Direct download from AWS S3 buckets\n(Himawari, GOES, etc.)",
            "#5D8AA8"
        )
        self.aws_btn.clicked.connect(lambda: self._pick("aws"))
        layout.addWidget(self.aws_btn)

        self.ftp_btn = self._make_card(
            "FTP (Professionals)",
            "Download via FTP from satellite data providers\n(JAXA ptree, NOAA, etc.)",
            "#9C6FD6"
        )
        self.ftp_btn.clicked.connect(lambda: self._pick("ftp"))
        layout.addWidget(self.ftp_btn)

        self.wis_btn = self._make_card(
            "WIS (Professionals)",
            "Download via WIS2Box OGC API\n(WMO Information System)",
            "#4CAF50"
        )
        self.wis_btn.clicked.connect(lambda: self._pick("wis"))
        layout.addWidget(self.wis_btn)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn, alignment=Qt.AlignCenter)

    def _make_card(self, title, desc, color):
        btn = QPushButton()
        btn.setFixedHeight(70)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: #2a2a2a;
                border-left: 4px solid {color};
                border-radius: 6px;
                text-align: left;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background: #3a3a3a;
            }}
            QPushButton:pressed {{
                background: #252525;
            }}
        """)
        inner = QVBoxLayout(btn)
        inner.setContentsMargins(8, 4, 8, 4)
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color};")
        inner.addWidget(title_lbl)
        desc_lbl = QLabel(desc)
        desc_lbl.setStyleSheet("font-size: 9px; color: #aaa;")
        inner.addWidget(desc_lbl)
        return btn

    def _pick(self, choice):
        if self.settings:
            wis_accounts, ftp_accounts = self.settings.load_accounts()
            if choice == "ftp":
                accounts = ftp_accounts
            elif choice == "wis":
                accounts = wis_accounts
            else:
                accounts = ["aws"]
            if not accounts:
                QMessageBox.warning(
                    self, "No Account Found",
                    f"No {choice.upper()} account configured.\n"
                    "Please set one up in System \u2192 Account first."
                )
                return
        self.selected = choice
        self.accept()
