import subprocess
import sys
import os
from bootloader import Bootloader

boot = Bootloader()

def update(msg, percent):
    boot.update_status(msg, percent)

boot.start()

try:
    update("Installing requirements...", 25)
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    update("Running latest_symlink.py...", 60)
    subprocess.check_call([sys.executable, os.path.join("Process", "latest_symlink.py")])

    update("Launching App.py...", 90)
    subprocess.Popen([sys.executable, os.path.join("src", "App.py")])  # Keeps App.py running

    update("Done!", 100)

except Exception as e:
    update(f"Error: {e}", 0)
    raise

finally:
    boot.finish()
