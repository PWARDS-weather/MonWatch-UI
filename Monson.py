import threading
import subprocess
import sys
import os
import logging
from bootloader import Bootloader

# -----------------------------
# Setup: silence logs globally
# -----------------------------
logging.getLogger().setLevel(logging.WARNING)  # only warnings/errors

# -----------------------------
# Windows-specific: hide console windows
# -----------------------------
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

# -----------------------------
# Bootloader UI
# -----------------------------
boot = Bootloader()

def update(msg, percent):
    boot.update_status(msg, percent)

# -----------------------------
# Background tasks
# -----------------------------
def background_tasks():
    try:
        # Step 1: Install requirements silently
        update("Installing requirements...", 10)
        subprocess.run(
            [pythonw, "-m", "pip", "install", "-r", "requirements.txt"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            check=True
        )

        # Step 2: Run latest_symlink.py silently
        update("Running latest_symlink.py...", 50)
        project_root = os.path.abspath(os.path.dirname(__file__))
        process_path = os.path.join("Process", "latest_symlink.py")
        subprocess.run(
            [pythonw, process_path],
            cwd=project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            check=True
        )

        # Step 3: Launch App.py silently
        update("Launching App.py...", 80)
        subprocess.Popen(
            [pythonw, os.path.join("src", "App.py")],
            cwd=project_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW
        )

        # Step 4: Finished
        update("All systems go!", 100)

    except Exception as e:
        update(f"Error: {e}", 0)
        raise
    finally:
        # Close bootloader UI
        boot.finish()

# -----------------------------
# Start background tasks thread
# -----------------------------
threading.Thread(target=background_tasks, daemon=True).start()

# -----------------------------
# Run bootloader UI (blocking)
# -----------------------------
boot.run()