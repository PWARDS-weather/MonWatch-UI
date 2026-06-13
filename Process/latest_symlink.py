import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ✅ Simplified paths - runs from project root
root_dir = Path(__file__).resolve().parents[1]  # Go up one level from 'Process/'
images_dir = root_dir / "public" / "images"
latest_path = images_dir / "latest.png"

def generate_image_url(year, month, day, hour, minute):
    time_str = f"{hour:02}{minute:02}"
    trm_url = f"https://www.data.jma.go.jp/mscweb/data/himawari/img/se2/se2_trm_{time_str}.jpg"
    infa_url = f"https://www.data.jma.go.jp/mscweb/data/himawari/img/se2/se2_b13_{time_str}.jpg"
    return trm_url, infa_url

def get_latest_utc_rounded():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Step 1: Round down to nearest 10-minute mark
    rounded_minute = (now.minute // 10) * 10
    rounded_time = now.replace(minute=rounded_minute, second=0, microsecond=0)
    # Step 2: Subtract 10 minutes from that rounded time
    final_time = rounded_time - timedelta(minutes=30)
    return final_time.year, final_time.month, final_time.day, final_time.hour, final_time.minute

def download_image(url, save_path):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            with open(save_path, "wb") as f:
                f.write(response.content)
            # Verify file was actually written
            if save_path.exists() and os.path.getsize(save_path) > 0:
                print(f"[DONE] Downloaded latest image from {url}")
                return True
            else:
                print(f"[ERROR] File not created: {save_path}")
        else:
            print(f"[WARN] Failed to fetch from {url} (status: {response.status_code})")
        return False
    except Exception as e:
        print(f"[ERROR] Download failed: {e}")
        return False

def update_latest():
    y, m, d, h, min10 = get_latest_utc_rounded()
    trm_url, infa_url = generate_image_url(y, m, d, h, min10)

    ph_hour = (h + 8) % 24
    selected_url = trm_url if 8 <= ph_hour <= 16 else infa_url

    print(f"[INFO] Using {'TRM' if selected_url == trm_url else 'INFA'} based on PHT hour: {ph_hour}")
    print(f"[INFO] Saving to: {latest_path.absolute()}")
    success = download_image(selected_url, latest_path)

    if not success:
        print("[FAIL] Could not download satellite image.")
        sys.exit(1)

if __name__ == "__main__":
    update_latest()