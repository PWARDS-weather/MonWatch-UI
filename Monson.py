import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from ctypes import wintypes

from bootloader import Bootloader

logging.getLogger().setLevel(logging.WARNING)

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

python_exe = sys.executable
pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
if not os.path.exists(pythonw):
    pythonw = python_exe

project_root = os.path.dirname(os.path.abspath(__file__))


def _wait_for_monwatch_window(process, timeout=60):
    """Poll until a window titled 'Monwatch' appears or process dies."""
    if sys.platform != "win32":
        time.sleep(3)
        return process.poll() is None

    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    start = time.time()
    found = False

    def enum_proc(hwnd, _lparam):
        nonlocal found
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        if "Monwatch" in buf.value:
            found = True
            return False
        return True

    enum_callback = WNDENUMPROC(enum_proc)

    while time.time() - start < timeout:
        if process.poll() is not None:
            return False
        found = False
        user32.EnumWindows(enum_callback, 0)
        if found:
            return True
        time.sleep(0.5)

    return False


def main():
    boot = Bootloader()

    def update(msg, percent):
        boot.update_status(msg, percent)

    def background_tasks():
        try:
            update("Installing requirements...", 10)
            subprocess.run(
                [pythonw, "-m", "pip", "install", "--user", "-r", "requirements.txt"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                check=True
            )

            update("Launching MonWatch...", 60)
            process = subprocess.Popen(
                [pythonw, "-m", "src"],
                cwd=project_root,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW
            )

            update("Waiting for MonWatch to start...", 80)
            if _wait_for_monwatch_window(process):
                update("MonWatch is ready!", 100)
            else:
                update("MonWatch failed to start", 0)

        except Exception as e:
            update(f"Error: {e}", 0)
            raise
        finally:
            boot.finish()

    threading.Thread(target=background_tasks, daemon=True).start()
    boot.run()


if __name__ == '__main__':
    main()
