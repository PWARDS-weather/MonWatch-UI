import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from src.ui.BUI import BroadcastWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MonWatch Broadcasting")

    start_datetime = sys.argv[1] if len(sys.argv) > 1 else None
    window = BroadcastWindow(main_ui=None, start_datetime=start_datetime)
    window.setWindowTitle("MonWatch Broadcasting")
    window.resize(1280, 800)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
