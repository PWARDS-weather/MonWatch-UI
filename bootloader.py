import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import os

class Bootloader:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Installing PWARDS...")
        self.root.geometry("520x420")
        self.root.configure(bg="#1e1e1e")  # Dark background
        self.root.resizable(False, False)

        # Logo
        img_path = os.path.join("public", "images", "PWARDS.png")
        if os.path.exists(img_path):
            try:
                img = Image.open(img_path).resize((300, 200), Image.Resampling.LANCZOS)
                self.photo = ImageTk.PhotoImage(img)
                tk.Label(self.root, image=self.photo, bg="#1e1e1e").pack(pady=(20, 10))
            except Exception as e:
                print(f"Failed to load image: {e}")
                tk.Label(self.root, text="PWARDS", font=("Arial", 24), fg="white", bg="#1e1e1e").pack(pady=(20, 10))
        else:
            print("Logo not found at:", img_path)
            tk.Label(self.root, text="PWARDS", font=("Arial", 24), fg="white", bg="#1e1e1e").pack(pady=(20, 10))

        # Status label
        self.status = tk.StringVar(value="Starting...")
        self.status_label = tk.Label(self.root, textvariable=self.status, font=("Segoe UI", 12), fg="white", bg="#1e1e1e")
        self.status_label.pack()

        # Styled progress bar
        style = ttk.Style(self.root)
        style.theme_use("default")
        style.configure("TProgressbar", troughcolor="#2e2e2e", bordercolor="#2e2e2e",
                        background="#00b894", lightcolor="#00b894", darkcolor="#00b894")

        self.progress = ttk.Progressbar(self.root, length=400, mode="determinate", style="TProgressbar")
        self.progress.pack(pady=20)

        self.progress_value = 0
        self.status_queue = []

    def update_status(self, message, percent):
        self.status.set(message)
        self.animate_progress_to(percent)

    def animate_progress_to(self, target):
        # Smooth animation of progress bar
        def step():
            current = self.progress["value"]
            if current < target:
                self.progress["value"] = min(current + 1, target)
                self.root.after(15, step)
            elif current > target:
                self.progress["value"] = max(current - 1, target)
                self.root.after(15, step)
        step()

    def run(self):
        self.root.mainloop()

    def finish(self):
        self.status.set("Finished!")
        self.progress["value"] = 100
        self.root.after(1000, self.root.quit)
