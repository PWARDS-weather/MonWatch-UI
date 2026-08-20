from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QTreeWidget, QTreeWidgetItem,
)


class BroadcastDataPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 4, 4, 4)

        header = QLabel("BROADCAST ELEMENTS")
        header.setStyleSheet("color: #f472b6; font-weight: bold; font-size: 11px; padding-bottom: 2px;")
        layout.addWidget(header)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setStyleSheet(
            "QTreeWidget { background: #1f1f1f; color: #ddd; border: 1px solid #444; }"
            "QTreeWidget::item { padding: 3px 0px; }"
        )
        self.tree.setIndentation(12)
        layout.addWidget(self.tree, 1)

        self._populate()

        btn_row = QHBoxLayout()
        btn_style = (
            "QPushButton { background: #2D2D2D; color: #AAA; border: 1px solid #444; "
            "padding: 3px 8px; border-radius: 3px; font-size: 10px; }"
            "QPushButton:hover { background: #3D3D3D; }"
            "QPushButton:disabled { color: #555; }"
        )
        self.add_btn = QPushButton("Add")
        self.remove_btn = QPushButton("Remove")
        self.edit_btn = QPushButton("Edit")
        for btn in (self.add_btn, self.remove_btn, self.edit_btn):
            btn.setStyleSheet(btn_style)
            btn_row.addWidget(btn)
        self.remove_btn.setEnabled(False)
        self.edit_btn.setEnabled(False)
        layout.addLayout(btn_row)

    def _populate(self):
        grey = QColor("#666")
        white = QColor("#ddd")

        def _add(name, active=True):
            item = QTreeWidgetItem([name])
            if not active:
                item.setForeground(0, grey)
                item.setFlags(item.flags() & ~Qt.ItemIsSelectable)
            else:
                item.setForeground(0, white)
            self.tree.addTopLevelItem(item)

        _add("01 Camera Keyframes", active=True)
        _add("02 NWP Wind Data", active=False)
        _add("03 NWP Temperature", active=False)
        _add("04 Satellite Imagery", active=False)
        _add("05 TC Tracks", active=False)
        _add("06 Radio Sounde", active=False)
