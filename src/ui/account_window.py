import logging
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QSpinBox,
    QComboBox, QCheckBox, QGroupBox, QGridLayout, QPushButton,
    QTabWidget, QListWidget, QListWidgetItem, QMessageBox,
    QDialog, QFormLayout, QDialogButtonBox, QPlainTextEdit,
)

log = logging.getLogger(__name__)


# ── Recognized account templates ────────────────────────────────────
# "Easy template" presets that pre-fill the account dialogs for known
# free data providers (read: real-time ASCAT swath downloaders). When an
# added/edited account is recognized (matched by server/host or name) the
# app pops a dialog describing the downloader(s) it unlocks and where to
# use them.
#
# Two kinds of downloader accounts exist:
#   * FTP accounts   — classic FTP/SFTP servers (KNMI OSI SAF).
#   * Data API (HTTPS) — HTTPS login + bearer-token services (NASA
#     Earthdata / PO.DAAC). These are NOT FTP; the host is the login/API
#     endpoint, never an FTP server.

ACCOUNT_TEMPLATES = {
    "knmi_osi_saf": {
        "label": "KNMI OSI SAF — Real-Time ASCAT Swath (FTP)",
        "account": {
            "name": "KNMI OSI SAF",
            "server": "ftppro.knmi.nl",
            "port": 21,
            "username": "",
            "password": "",
            "passive": True,
            "remote_dir": "",
        },
        "title": "Real-Time ASCAT Swath (KNMI FTP)",
        "sections": [
            ("Downloaders unlocked", "ASCAT scatterometer swath winds — the "
             "genuine orbit (strip-shaped) wind measurements from the KNMI / "
             "EUMETSAT OSI SAF, drawn as u/v wind barbs on the globe."),
            ("Data", "OSI-102 / OSI-104 Level-2 near-real-time swath NetCDFs, "
             "Metop-B and Metop-C. ~2 h after each pass; only the last ~3 "
             "days are kept on the FTP."),
            ("Setup", "Credentials are free — email scat@knmi.nl to request an "
             "OSI SAF FTP account."),
            ("Where to use it", "System menu → 'ASCAT Data & Metadata...' → "
             "'Real-Time Swath (KNMI FTP)' (purple button)."),
        ],
    },
}

API_ACCOUNT_TEMPLATES = {
    "nasa_earthdata": {
        "label": "NASA Earthdata — Real-Time ASCAT Swath (HTTPS, no FTP)",
        "account": {
            "name": "NASA Earthdata",
            "host": "urs.earthdata.nasa.gov",
            "username": "",
            "password": "",
        },
        "title": "Real-Time ASCAT Swath (NASA PO.DAAC)",
        "sections": [
            ("Downloaders unlocked", "Same OSI SAF swath winds mirrored by the "
             "NASA PO.DAAC cloud archive and downloaded over plain HTTPS — "
             "u/v barbs on the globe, no FTP needed."),
            ("Data", "PO.DAAC mirrors the KNMI OSI SAF Level-2 NRT swath "
             "products (25 km and 12.5 km coastal, Metop-B and Metop-C). "
             "~2-3 h latency; orbits are kept in the archive."),
            ("Setup", "This is a NASA Earthdata Login account (free, instant "
             "self-service sign-up at https://urs.earthdata.nasa.gov). NASA "
             "retired FTP; downloads use HTTPS with a bearer token issued "
             "from this login."),
            ("Where to use it", "System menu → 'ASCAT Data & Metadata...' → "
             "'Real-Time Swath (NASA PO.DAAC)' (green button)."),
        ],
    },
}


def recognize_account(acc):
    """Return the FTP template key for a known account, or None."""
    if not isinstance(acc, dict):
        return None
    name = (acc.get("name") or "").strip().lower()
    server = (acc.get("server") or "").strip().lower()
    for key, tpl in ACCOUNT_TEMPLATES.items():
        t_acc = tpl["account"]
        if name == t_acc["name"].lower() or server == t_acc["server"].lower():
            return key
    return None


def recognize_api_account(acc):
    """Return the data-API template key for a known account, or None."""
    if not isinstance(acc, dict):
        return None
    name = (acc.get("name") or "").strip().lower()
    host = (acc.get("host") or "").strip().lower()
    for key, tpl in API_ACCOUNT_TEMPLATES.items():
        t_acc = tpl["account"]
        if name == t_acc["name"].lower() or host == t_acc["host"].lower():
            return key
    return None


# ── Dialog: Add/Edit WIS Account ────────────────────────────────────

class WISAccountDialog(QDialog):
    def __init__(self, parent=None, account=None):
        super().__init__(parent)
        self.setWindowTitle("WIS Account" if not account else "Edit WIS Account")
        self.setFixedSize(480, 400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. My WIS Server")
        form.addRow("Name:", self.name_edit)

        self.api_url_edit = QLineEdit()
        self.api_url_edit.setPlaceholderText("http://localhost/oapi")
        form.addRow("API URL:", self.api_url_edit)

        self.api_type_combo = QComboBox()
        self.api_type_combo.addItems(["pygeoapi"])
        form.addRow("API Type:", self.api_type_combo)

        self.backend_url_edit = QLineEdit()
        self.backend_url_edit.setPlaceholderText("http://elasticsearch:9200")
        form.addRow("Backend URL:", self.backend_url_edit)

        self.auth_token_edit = QLineEdit()
        self.auth_token_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Auth Token:", self.auth_token_edit)

        self.mgmt_token_edit = QLineEdit()
        self.mgmt_token_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Management Token:", self.mgmt_token_edit)

        self.mqtt_broker_edit = QLineEdit()
        form.addRow("MQTT Broker:", self.mqtt_broker_edit)

        self.mqtt_topic_edit = QLineEdit()
        form.addRow("MQTT Topic:", self.mqtt_topic_edit)

        layout.addLayout(form)

        if account:
            self.name_edit.setText(account.get("name", ""))
            self.api_url_edit.setText(account.get("api_url", ""))
            self.api_type_combo.setCurrentText(account.get("api_type", "pygeoapi"))
            self.backend_url_edit.setText(account.get("backend_url", ""))
            self.auth_token_edit.setText(account.get("auth_token", ""))
            self.mgmt_token_edit.setText(account.get("mgmt_token", ""))
            self.mqtt_broker_edit.setText(account.get("mqtt_broker", ""))
            self.mqtt_topic_edit.setText(account.get("mqtt_topic", ""))

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing Field", "Account name is required.")
            return
        if not self.api_url_edit.text().strip():
            QMessageBox.warning(self, "Missing Field", "API URL is required.")
            return
        self.accept()

    def get_account(self):
        return {
            "name": self.name_edit.text().strip(),
            "api_url": self.api_url_edit.text().strip(),
            "api_type": self.api_type_combo.currentText(),
            "backend_url": self.backend_url_edit.text().strip(),
            "auth_token": self.auth_token_edit.text().strip(),
            "mgmt_token": self.mgmt_token_edit.text().strip(),
            "mqtt_broker": self.mqtt_broker_edit.text().strip(),
            "mqtt_topic": self.mqtt_topic_edit.text().strip(),
        }


# ── Dialog: Add/Edit FTP Account ────────────────────────────────────

class FTPAccountDialog(QDialog):
    def __init__(self, parent=None, account=None):
        super().__init__(parent)
        self.setWindowTitle("FTP Account" if not account else "Edit FTP Account")
        self.setFixedSize(450, 320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. JAXA Himawari")
        form.addRow("Name:", self.name_edit)

        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("ftp.ptree.jaxa.jp")
        form.addRow("Server:", self.server_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(21)
        form.addRow("Port:", self.port_spin)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("username")
        form.addRow("Username:", self.user_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.pass_edit)

        self.passive_cb = QCheckBox("Passive Mode")
        self.passive_cb.setChecked(True)
        form.addRow("", self.passive_cb)

        self.remote_dir_edit = QLineEdit()
        self.remote_dir_edit.setPlaceholderText("/path/to/data")
        form.addRow("Remote Directory:", self.remote_dir_edit)

        layout.addLayout(form)

        if account:
            self.name_edit.setText(account.get("name", ""))
            self.server_edit.setText(account.get("server", ""))
            self.port_spin.setValue(account.get("port", 21))
            self.user_edit.setText(account.get("username", ""))
            self.pass_edit.setText(account.get("password", ""))
            self.passive_cb.setChecked(account.get("passive", True))
            self.remote_dir_edit.setText(account.get("remote_dir", ""))

        info = QLabel("Example: ftp://account@ftp.ptree.jaxa.jp")
        info.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(info)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing Field", "Account name is required.")
            return
        if not self.server_edit.text().strip():
            QMessageBox.warning(self, "Missing Field", "Server address is required.")
            return
        self.accept()

    def get_account(self):
        return {
            "name": self.name_edit.text().strip(),
            "server": self.server_edit.text().strip(),
            "port": self.port_spin.value(),
            "username": self.user_edit.text().strip(),
            "password": self.pass_edit.text().strip(),
            "passive": self.passive_cb.isChecked(),
            "remote_dir": self.remote_dir_edit.text().strip(),
        }


# ── Dialog: Add/Edit Data API (HTTPS) Account ──────────────────────

class APITemplateDialog(QDialog):
    """Credentials for HTTPS / bearer-token downloaders (e.g. NASA Earthdata).

    These are NOT FTP accounts — the host is the login/API endpoint (e.g.
    urs.earthdata.nasa.gov) used to mint a download token over HTTPS.
    """

    def __init__(self, parent=None, account=None):
        super().__init__(parent)
        self.setWindowTitle("Data API Account" if not account else "Edit Data API Account")
        self.setFixedSize(480, 260)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)

        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. NASA Earthdata")
        form.addRow("Name:", self.name_edit)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("urs.earthdata.nasa.gov")
        form.addRow("Login / API Host:", self.host_edit)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Earthdata Login user")
        form.addRow("Username:", self.user_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.pass_edit)

        layout.addLayout(form)

        if account:
            self.name_edit.setText(account.get("name", ""))
            self.host_edit.setText(account.get("host", ""))
            self.user_edit.setText(account.get("username", ""))
            self.pass_edit.setText(account.get("password", ""))

        info = QLabel(
            "HTTPS/token service, NOT an FTP server. Downloads use a bearer "
            "token issued by this login (e.g. NASA Earthdata for PO.DAAC)."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(info)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _validate(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Missing Field", "Account name is required.")
            return
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "Missing Field", "Login / API host is required.")
            return
        self.accept()

    def get_account(self):
        return {
            "name": self.name_edit.text().strip(),
            "host": self.host_edit.text().strip(),
            "username": self.user_edit.text().strip(),
            "password": self.pass_edit.text().strip(),
        }


# ── Account Window ──────────────────────────────────────────────────

class _LayoutReadWorker(QThread):
    """Background thread that reads an FTP account's directory layout."""

    finished = Signal(bool, str, str)  # ok, text, error

    def __init__(self, account, parent=None):
        super().__init__(parent)
        self.account = account

    def run(self):
        try:
            from ..clients.ftp_client import load_jaxa_account, read_layout_report
            acc = dict(self.account or {})
            account = {
                "server": acc.get("server") or "ftp.ptree.jaxa.jp",
                "port": int(acc.get("port") or 21),
                "username": acc.get("username") or "",
                "password": acc.get("password") or "",
                "passive": bool(acc.get("passive", True)),
            }
            # Legacy JAXA P-Tree layout read: fall back to the free account.
            fallback = load_jaxa_account()
            if not account["username"]:
                account["username"] = fallback["username"]
                account["password"] = fallback["password"]
            ok, _, text, error = read_layout_report(
                account=account, max_depth=4, include_pub=False
            )
            self.finished.emit(ok, text, error or "")
        except Exception as e:
            self.finished.emit(False, "", str(e))


class AccountWindow(QWidget):
    def __init__(self, parent, settings_mgr):
        super().__init__(parent, Qt.Window)
        self.settings = settings_mgr
        self.setWindowTitle("Account")
        self.resize(560, 520)

        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self._build_wis_tab()
        self._build_ftp_tab()
        self._build_api_tab()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply)
        btn_layout.addWidget(apply_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        main_layout.addLayout(btn_layout)

        self._load_accounts()

    def closeEvent(self, event):
        """Safely stop a running layout reader thread before closing."""
        worker = getattr(self, "_layout_worker", None)
        if worker is not None:
            try:
                self._layout_worker = None
                if worker.isRunning():
                    worker.wait(3000)
                if worker.isRunning():
                    worker.terminate()
                    worker.wait(800)
            except Exception as e:
                log.debug(f"[AccountWindow] layout worker cleanup: {e}")
        super().closeEvent(event)

    # ── WIS Tab ──────────────────────────────────────────────────────

    def _build_wis_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        header = QLabel("WIS Accounts")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px 0;")
        layout.addWidget(header)

        self.wis_list = QListWidget()
        self.wis_list.setAlternatingRowColors(True)
        self.wis_list.itemDoubleClicked.connect(self._wis_edit_selected)
        layout.addWidget(self.wis_list, 1)

        btn_row = QHBoxLayout()
        self.wis_add_btn = QPushButton("+")
        self.wis_add_btn.setFixedWidth(32)
        self.wis_add_btn.clicked.connect(self._wis_add)
        btn_row.addWidget(self.wis_add_btn)

        self.wis_edit_btn = QPushButton("Edit")
        self.wis_edit_btn.clicked.connect(self._wis_edit_selected)
        btn_row.addWidget(self.wis_edit_btn)

        self.wis_delete_btn = QPushButton("Delete")
        self.wis_delete_btn.clicked.connect(self._wis_delete)
        btn_row.addWidget(self.wis_delete_btn)

        self.wis_test_btn = QPushButton("Test Connection")
        self.wis_test_btn.clicked.connect(self._wis_test_selected)
        btn_row.addWidget(self.wis_test_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.wis_status = QLabel("")
        layout.addWidget(self.wis_status)

        self.tabs.addTab(tab, "WIS Account")

    # ── FTP Tab ──────────────────────────────────────────────────────

    def _build_ftp_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        header = QLabel("FTP Accounts")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px 0;")
        layout.addWidget(header)

        self.ftp_list = QListWidget()
        self.ftp_list.setAlternatingRowColors(True)
        self.ftp_list.itemDoubleClicked.connect(self._ftp_edit_selected)
        layout.addWidget(self.ftp_list, 1)

        btn_row = QHBoxLayout()
        self.ftp_add_btn = QPushButton("+")
        self.ftp_add_btn.setFixedWidth(32)
        self.ftp_add_btn.clicked.connect(self._ftp_add)
        btn_row.addWidget(self.ftp_add_btn)

        self.ftp_edit_btn = QPushButton("Edit")
        self.ftp_edit_btn.clicked.connect(self._ftp_edit_selected)
        btn_row.addWidget(self.ftp_edit_btn)

        self.ftp_delete_btn = QPushButton("Delete")
        self.ftp_delete_btn.clicked.connect(self._ftp_delete)
        btn_row.addWidget(self.ftp_delete_btn)

        self.ftp_test_btn = QPushButton("Test Connection")
        self.ftp_test_btn.clicked.connect(self._ftp_test_selected)
        btn_row.addWidget(self.ftp_test_btn)

        self.ftp_layout_btn = QPushButton("View Layout")
        self.ftp_layout_btn.setToolTip(
            "Read and display the directory tree of the selected FTP server "
            "(JAXA P-Tree legacy layout)."
        )
        self.ftp_layout_btn.clicked.connect(self._ftp_view_layout_selected)
        btn_row.addWidget(self.ftp_layout_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        tpl_row = QHBoxLayout()
        tpl_lbl = QLabel("Easy template:")
        tpl_lbl.setStyleSheet("font-weight: bold;")
        tpl_row.addWidget(tpl_lbl)
        self.ftp_template_combo = QComboBox()
        self.ftp_template_combo.addItem("Select a free data template…")
        for tpl in ACCOUNT_TEMPLATES.values():
            self.ftp_template_combo.addItem(tpl["label"])
        self.ftp_template_combo.setMinimumWidth(340)
        tpl_row.addWidget(self.ftp_template_combo)
        self.ftp_template_btn = QPushButton("Add from Template")
        self.ftp_template_btn.setToolTip(
            "Pre-fills the FTP account dialog with a recognized free data "
            "provider so you only type the username/password."
        )
        self.ftp_template_btn.clicked.connect(self._ftp_add_template)
        tpl_row.addWidget(self.ftp_template_btn)
        tpl_row.addStretch()
        layout.addLayout(tpl_row)

        self.ftp_status = QLabel("")
        layout.addWidget(self.ftp_status)

        self.tabs.addTab(tab, "FTP Account")

    # ── Data API (HTTPS) Tab ────────────────────────────────────────

    def _build_api_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        header = QLabel("Data API (HTTPS) Accounts")
        header.setStyleSheet("font-size: 13px; font-weight: bold; padding: 4px 0;")
        layout.addWidget(header)

        sub = QLabel(
            "Login credentials for HTTPS / bearer-token data services "
            "(e.g. NASA Earthdata). These are NOT FTP servers."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #888; font-size: 9pt;")
        layout.addWidget(sub)

        self.api_list = QListWidget()
        self.api_list.setAlternatingRowColors(True)
        self.api_list.itemDoubleClicked.connect(self._api_edit_selected)
        layout.addWidget(self.api_list, 1)

        btn_row = QHBoxLayout()
        self.api_add_btn = QPushButton("+")
        self.api_add_btn.setFixedWidth(32)
        self.api_add_btn.clicked.connect(self._api_add)
        btn_row.addWidget(self.api_add_btn)

        self.api_edit_btn = QPushButton("Edit")
        self.api_edit_btn.clicked.connect(self._api_edit_selected)
        btn_row.addWidget(self.api_edit_btn)

        self.api_delete_btn = QPushButton("Delete")
        self.api_delete_btn.clicked.connect(self._api_delete)
        btn_row.addWidget(self.api_delete_btn)

        self.api_verify_btn = QPushButton("Verify Credentials")
        self.api_verify_btn.setToolTip(
            "Asks the provider to issue a download token with these "
            "credentials (network call)."
        )
        self.api_verify_btn.clicked.connect(self._api_verify_selected)
        btn_row.addWidget(self.api_verify_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        tpl_row = QHBoxLayout()
        tpl_lbl = QLabel("Easy template:")
        tpl_lbl.setStyleSheet("font-weight: bold;")
        tpl_row.addWidget(tpl_lbl)
        self.api_template_combo = QComboBox()
        self.api_template_combo.addItem("Select a free data template…")
        for tpl in API_ACCOUNT_TEMPLATES.values():
            self.api_template_combo.addItem(tpl["label"])
        self.api_template_combo.setMinimumWidth(340)
        tpl_row.addWidget(self.api_template_combo)
        self.api_template_btn = QPushButton("Add from Template")
        self.api_template_btn.setToolTip(
            "Pre-fills this dialog with a recognized HTTPS data provider so "
            "you only type the username/password."
        )
        self.api_template_btn.clicked.connect(self._api_add_template)
        tpl_row.addWidget(self.api_template_btn)
        tpl_row.addStretch()
        layout.addLayout(tpl_row)

        self.api_status = QLabel("")
        layout.addWidget(self.api_status)

        self.tabs.addTab(tab, "Data API (HTTPS)")

    # ── WIS Actions ──────────────────────────────────────────────────

    def _wis_add(self):
        dialog = WISAccountDialog(self)
        if dialog.exec() == QDialog.Accepted:
            acc = dialog.get_account()
            item = QListWidgetItem(acc["name"])
            item.setData(Qt.UserRole, acc)
            self.wis_list.addItem(item)
            self.wis_list.setCurrentItem(item)
            self.wis_status.setText(f"Added WIS account: {acc['name']}")
            self.wis_status.setStyleSheet("color: #4CAF50;")

    def _wis_edit_selected(self):
        item = self.wis_list.currentItem()
        if not item:
            return
        acc = item.data(Qt.UserRole)
        dialog = WISAccountDialog(self, acc)
        if dialog.exec() == QDialog.Accepted:
            new_acc = dialog.get_account()
            item.setText(new_acc["name"])
            item.setData(Qt.UserRole, new_acc)
            self.wis_status.setText(f"Updated WIS account: {new_acc['name']}")
            self.wis_status.setStyleSheet("color: #4CAF50;")

    def _wis_delete(self):
        item = self.wis_list.currentItem()
        if not item:
            return
        name = item.text()
        if QMessageBox.question(self, "Delete", f"Delete WIS account '{name}'?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.wis_list.takeItem(self.wis_list.row(item))
            self.wis_status.setText(f"Deleted WIS account: {name}")
            self.wis_status.setStyleSheet("color: orange;")

    def _wis_test_selected(self):
        item = self.wis_list.currentItem()
        if not item:
            self.wis_status.setText("Select an account first")
            self.wis_status.setStyleSheet("color: orange;")
            return
        acc = item.data(Qt.UserRole)
        from ..clients.wis2box_client import WIS2BoxClient
        self.wis_status.setText("Testing...")
        self.wis_status.setStyleSheet("color: gray;")
        self.wis_test_btn.setEnabled(False)
        client = WIS2BoxClient(acc["api_url"], acc.get("auth_token"))
        ok, result = client.test_connection()
        self.wis_test_btn.setEnabled(True)
        if ok:
            self.wis_status.setText(f"{acc['name']}: Connected")
            self.wis_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.wis_status.setText(f"{acc['name']}: {result}")
            self.wis_status.setStyleSheet("color: red;")

    # ── FTP Actions ──────────────────────────────────────────────────

    def _ftp_add(self):
        dialog = FTPAccountDialog(self)
        if dialog.exec() == QDialog.Accepted:
            acc = dialog.get_account()
            item = QListWidgetItem(acc["name"])
            item.setData(Qt.UserRole, acc)
            self.ftp_list.addItem(item)
            self.ftp_list.setCurrentItem(item)
            self.ftp_status.setText(f"Added FTP account: {acc['name']}")
            self.ftp_status.setStyleSheet("color: #4CAF50;")
            self._show_feature_dialog(acc)

    def _ftp_add_template(self):
        idx = self.ftp_template_combo.currentIndex()
        if idx <= 0:
            self.ftp_status.setText("Pick a template from the list first")
            self.ftp_status.setStyleSheet("color: orange;")
            return
        key = list(ACCOUNT_TEMPLATES.keys())[idx - 1]
        tpl = ACCOUNT_TEMPLATES[key]
        dialog = FTPAccountDialog(self, dict(tpl["account"]))
        if dialog.exec() == QDialog.Accepted:
            acc = dialog.get_account()
            item = QListWidgetItem(acc["name"])
            item.setData(Qt.UserRole, acc)
            self.ftp_list.addItem(item)
            self.ftp_list.setCurrentItem(item)
            self.ftp_status.setText(f"Added FTP account: {acc['name']}")
            self.ftp_status.setStyleSheet("color: #4CAF50;")
            self._show_feature_dialog(acc)
        self.ftp_template_combo.setCurrentIndex(0)

    def _show_feature_dialog(self, acc):
        """If the account is recognized, show what it unlocks + where."""
        key = recognize_account(acc)
        if key is None:
            return
        tpl = ACCOUNT_TEMPLATES[key]
        self._show_feature_box(acc, tpl["title"], tpl["sections"])

    def _show_api_feature_dialog(self, acc):
        key = recognize_api_account(acc)
        if key is None:
            return
        tpl = API_ACCOUNT_TEMPLATES[key]
        self._show_feature_box(acc, tpl["title"], tpl["sections"])

    def _show_feature_box(self, acc, title, sections):
        parts = [f"<b style=\"color:#1565C0; font-size:11pt;\">{title}</b>"]
        for heading, text in sections:
            parts.append(f"<p><b>{heading}:</b> {text}</p>")
        html = ("<html><body style=\"font-size:10pt;\">"
                + "".join(parts) + "</body></html>")
        box = QMessageBox(self)
        box.setWindowTitle(f"Account recognized: {acc.get('name', '')}")
        box.setIcon(QMessageBox.Information)
        box.setTextFormat(Qt.RichText)
        box.setText(html)
        box.addButton(QMessageBox.Ok)
        box.exec()

    def _ftp_edit_selected(self):
        item = self.ftp_list.currentItem()
        if not item:
            return
        acc = item.data(Qt.UserRole)
        dialog = FTPAccountDialog(self, acc)
        if dialog.exec() == QDialog.Accepted:
            new_acc = dialog.get_account()
            item.setText(new_acc["name"])
            item.setData(Qt.UserRole, new_acc)
            self.ftp_status.setText(f"Updated FTP account: {new_acc['name']}")
            self.ftp_status.setStyleSheet("color: #4CAF50;")
            self._show_feature_dialog(new_acc)

    def _ftp_delete(self):
        item = self.ftp_list.currentItem()
        if not item:
            return
        name = item.text()
        if QMessageBox.question(self, "Delete", f"Delete FTP account '{name}'?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.ftp_list.takeItem(self.ftp_list.row(item))
            self.ftp_status.setText(f"Deleted FTP account: {name}")
            self.ftp_status.setStyleSheet("color: orange;")

    def _ftp_test_selected(self):
        item = self.ftp_list.currentItem()
        if not item:
            self.ftp_status.setText("Select an account first")
            self.ftp_status.setStyleSheet("color: orange;")
            return
        acc = item.data(Qt.UserRole)
        from ..clients.ftp_client import test_ftp_connection
        self.ftp_status.setText("Testing...")
        self.ftp_status.setStyleSheet("color: gray;")
        self.ftp_test_btn.setEnabled(False)
        ok, msg = test_ftp_connection(
            acc["server"], acc.get("port", 21),
            acc.get("username", ""), acc.get("password", ""),
            acc.get("passive", True)
        )
        self.ftp_test_btn.setEnabled(True)
        if ok:
            self.ftp_status.setText(f"{acc['name']}: Connected")
            self.ftp_status.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.ftp_status.setText(f"{acc['name']}: {msg}")
            self.ftp_status.setStyleSheet("color: red;")

    def _ftp_view_layout_selected(self):
        item = self.ftp_list.currentItem()
        if not item:
            self.ftp_status.setText("Select an account first")
            self.ftp_status.setStyleSheet("color: orange;")
            return
        acc = item.data(Qt.UserRole)
        self.ftp_status.setText(f"Reading layout of {acc['name']}...")
        self.ftp_status.setStyleSheet("color: gray;")
        self.ftp_layout_btn.setEnabled(False)

        self._layout_worker = _LayoutReadWorker(acc)
        self._layout_worker.finished.connect(self._on_layout_ready)
        self._layout_worker.start()

    def _on_layout_ready(self, ok, text, error):
        self.ftp_layout_btn.setEnabled(True)
        if not ok:
            self.ftp_status.setText(f"Layout error: {error}")
            self.ftp_status.setStyleSheet("color: red;")
            QMessageBox.critical(self, "Layout Error",
                                 f"Could not read the FTP layout:\n{error}")
            return
        self.ftp_status.setText("Layout ready")
        self.ftp_status.setStyleSheet("color: green; font-weight: bold;")

        dlg = QDialog(self)
        dlg.setWindowTitle("FTP Layout (JAXA P-Tree legacy)")
        dlg.resize(760, 640)
        lay = QVBoxLayout(dlg)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        editor.setStyleSheet("""
            QPlainTextEdit {
                font-family: 'Consolas', 'Courier New', monospace;
                background: #10161F; color: #DFE6F0;
                border: 1px solid #2A3540; border-radius: 4px;
            }
        """)
        lay.addWidget(editor, 1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.accept)
        lay.addWidget(close_btn, alignment=Qt.AlignCenter)
        dlg.exec()

    # ── Data API (HTTPS) Actions ────────────────────────────────────

    def _api_add(self):
        dialog = APITemplateDialog(self)
        if dialog.exec() == QDialog.Accepted:
            acc = dialog.get_account()
            item = QListWidgetItem(acc["name"])
            item.setData(Qt.UserRole, acc)
            self.api_list.addItem(item)
            self.api_list.setCurrentItem(item)
            self.api_status.setText(f"Added API account: {acc['name']}")
            self.api_status.setStyleSheet("color: #4CAF50;")
            self._show_api_feature_dialog(acc)

    def _api_add_template(self):
        idx = self.api_template_combo.currentIndex()
        if idx <= 0:
            self.api_status.setText("Pick a template from the list first")
            self.api_status.setStyleSheet("color: orange;")
            return
        key = list(API_ACCOUNT_TEMPLATES.keys())[idx - 1]
        tpl = API_ACCOUNT_TEMPLATES[key]
        dialog = APITemplateDialog(self, dict(tpl["account"]))
        if dialog.exec() == QDialog.Accepted:
            acc = dialog.get_account()
            item = QListWidgetItem(acc["name"])
            item.setData(Qt.UserRole, acc)
            self.api_list.addItem(item)
            self.api_list.setCurrentItem(item)
            self.api_status.setText(f"Added API account: {acc['name']}")
            self.api_status.setStyleSheet("color: #4CAF50;")
            self._show_api_feature_dialog(acc)
        self.api_template_combo.setCurrentIndex(0)

    def _api_edit_selected(self):
        item = self.api_list.currentItem()
        if not item:
            return
        acc = item.data(Qt.UserRole)
        dialog = APITemplateDialog(self, acc)
        if dialog.exec() == QDialog.Accepted:
            new_acc = dialog.get_account()
            item.setText(new_acc["name"])
            item.setData(Qt.UserRole, new_acc)
            self.api_status.setText(f"Updated API account: {new_acc['name']}")
            self.api_status.setStyleSheet("color: #4CAF50;")
            self._show_api_feature_dialog(new_acc)

    def _api_delete(self):
        item = self.api_list.currentItem()
        if not item:
            return
        name = item.text()
        if QMessageBox.question(self, "Delete", f"Delete API account '{name}'?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.api_list.takeItem(self.api_list.row(item))
            self.api_status.setText(f"Deleted API account: {name}")
            self.api_status.setStyleSheet("color: orange;")

    def _api_verify_selected(self):
        item = self.api_list.currentItem()
        if not item:
            self.api_status.setText("Select an account first")
            self.api_status.setStyleSheet("color: orange;")
            return
        acc = item.data(Qt.UserRole)
        username = acc.get("username", "")
        password = acc.get("password", "")
        if not username or not password:
            self.api_status.setText(f"{acc['name']}: needs username and password")
            self.api_status.setStyleSheet("color: orange;")
            return
        from ..clients.podaac_swath import get_urs_token
        self.api_status.setText("Verifying...")
        self.api_status.setStyleSheet("color: gray;")
        self.api_verify_btn.setEnabled(False)
        try:
            token = get_urs_token(username, password)
            tail = f"{token[:8]}…" if token else "none"
            self.api_status.setText(f"{acc['name']}: token issued ({tail})")
            self.api_status.setStyleSheet("color: green; font-weight: bold;")
        except Exception as e:
            self.api_status.setText(f"{acc['name']}: {e}")
            self.api_status.setStyleSheet("color: red;")
        self.api_verify_btn.setEnabled(True)

    # ── Load / Save ──────────────────────────────────────────────────

    def _load_accounts(self):
        self.wis_list.clear()
        wis_accounts, ftp_accounts = self.settings.load_accounts()
        for acc in wis_accounts:
            item = QListWidgetItem(acc.get("name", "Unnamed"))
            item.setData(Qt.UserRole, acc)
            self.wis_list.addItem(item)

        self.ftp_list.clear()
        for acc in ftp_accounts:
            item = QListWidgetItem(acc.get("name", "Unnamed"))
            item.setData(Qt.UserRole, acc)
            self.ftp_list.addItem(item)

        self.api_list.clear()
        for acc in self.settings.load_api_accounts():
            item = QListWidgetItem(acc.get("name", "Unnamed"))
            item.setData(Qt.UserRole, acc)
            self.api_list.addItem(item)

    def _apply(self):
        wis_accounts = []
        for i in range(self.wis_list.count()):
            wis_accounts.append(self.wis_list.item(i).data(Qt.UserRole))
        ftp_accounts = []
        for i in range(self.ftp_list.count()):
            ftp_accounts.append(self.ftp_list.item(i).data(Qt.UserRole))
        api_accounts = []
        for i in range(self.api_list.count()):
            api_accounts.append(self.api_list.item(i).data(Qt.UserRole))
        self.settings.save_accounts(wis_accounts, ftp_accounts, api_accounts)
        log.info(f"Saved {len(wis_accounts)} WIS, {len(ftp_accounts)} FTP and "
                 f"{len(api_accounts)} data-API accounts to accounts.json")
