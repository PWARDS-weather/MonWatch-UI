import os
import requests
from datetime import datetime, timedelta
from pathlib import Path

# Base directories
process_dir = Path.cwd()
base_dir = process_dir.parent
images_dir = base_dir / "public" / "images"
images_dir.mkdir(parents=True, exist_ok=True)

latest_filename = "latest.png"
latest_path = images_dir / latest_filename

# Generate URL for current UTC
def generate_image_url(year, month, day, hour, minute):
    time_str = f"{hour:02}{minute:02}"
    trm_url = f"https://www.data.jma.go.jp/mscweb/data/himawari/img/se2/se2_trm_{time_str}.jpg"
    infa_url = f"https://www.data.jma.go.jp/mscweb/data/himawari/img/se2/se2_b13_{time_str}.jpg"
    return trm_url, infa_url

# Determine current nearest 10-min UTC timestamp
def get_latest_utc_rounded():
    from datetime import timezone
    now = datetime.now(timezone.utc) - timedelta(minutes=10)
    rounded_minute = (now.minute // 10) * 10
    return now.year, now.month, now.day, now.hour, rounded_minute

# Download image
def download_image(url, save_path):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(response.content)
            print(f"[DONE] Downloaded latest image from {url}")
            return True
        else:
            print(f"[WARN] Failed to fetch from {url} (status: {response.status_code})")
            return False
    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
        return False

# Main logic
def update_latest():
    y, m, d, h, min10 = get_latest_utc_rounded()
    trm_url, infa_url = generate_image_url(y, m, d, h, min10)

    # Use TRM for daytime (08:00–16:00 PHT == 00–08 UTC)
    ph_hour = (h + 8) % 24
    selected_url = trm_url if 8 <= ph_hour <= 16 else infa_url

    print(f"[INFO] Using {'TRM' if selected_url == trm_url else 'INFA'} based on PHT hour: {ph_hour}")
    success = download_image(selected_url, latest_path)

    if not success:
        print("[FAIL] Could not download satellite image.")

if __name__ == "__main__":
    update_latest()
