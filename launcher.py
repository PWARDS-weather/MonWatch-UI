import sys
import os
import logging

logging.getLogger().setLevel(logging.WARNING)

if getattr(sys, 'frozen', False):
    _base = os.path.dirname(sys.executable)
    sys.path.insert(0, _base)
    sys.path.insert(0, os.path.join(_base, 'src'))
    from src.UI import MainUI
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    import src.services.thread_guard

    app = QApplication(sys.argv)
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    window = MainUI()
    window.apply_theme()
    window.show()
    try:
        exit_code = app.exec()
    finally:
        try:
            import src.services.thread_guard as thread_guard
            thread_guard.shutdown_threads()
        except Exception:
            pass
    sys.exit(exit_code)
else:
    import Monson
    Monson.main()