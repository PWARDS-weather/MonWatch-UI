import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import threading
import time
import os

class Bootloader:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Installing PWARDS...")
        self.root.geometry("520x420")
        self.root.configure(bg="white")
        self.root.resizable(False, False)

        # Try loading the logo
        img_path = os.path.join("public", "images", "PWARDS.png")
        if os.path.exists(img_path):
            try:
                img = Image.open(img_path).resize((300, 200), Image.ANTIALIAS)
                self.photo = ImageTk.PhotoImage(img)
                tk.Label(self.root, image=self.photo, bg="white").pack(pady=(20, 10))
            except Exception as e:
                print(f"Failed to load image: {e}")
                tk.Label(self.root, text="PWARDS", font=("Arial", 24), bg="white").pack(pady=(20, 10))
        else:
            print("Logo not found at:", img_path)
            tk.Label(self.root, text="PWARDS", font=("Arial", 24), bg="white").pack(pady=(20, 10))

        # Status text
        self.status = tk.StringVar(value="Starting...")
        tk.Label(self.root, textvariable=self.status, font=("Segoe UI", 12), bg="white").pack()

        # Progress bar (green themed)
        style = ttk.Style(self.root)
        style.theme_use("default")
        style.configure("TProgressbar", troughcolor="white", bordercolor="white", background="#4CAF50", lightcolor="#4CAF50", darkcolor="#4CAF50")

        self.progress = ttk.Progressbar(self.root, length=400, mode="determinate", style="TProgressbar")
        self.progress.pack(pady=20)

        # Start in separate thread
        self.thread = threading.Thread(target=self.root.mainloop)

    def start(self):
        self.thread.start()
        time.sleep(0.5)

    def update_status(self, message, percent):
        self.status.set(message)
        self.progress["value"] = percent
        self.root.update_idletasks()

    def finish(self):
        time.sleep(1)
        self.root.quit()
