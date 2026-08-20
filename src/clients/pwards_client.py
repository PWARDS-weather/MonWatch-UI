import requests
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PWARDS_AHI_TO_SATAID = {
    "B01": "VIS01", "B02": "VIS02", "B03": "VIS03",
    "B04": "VIS04", "B05": "VIS05", "B06": "VIS06",
    "B07": "IR07",  "B08": "IR08",  "B09": "IR09",
    "B10": "IR10",  "B11": "IR11",  "B12": "IR12",
    "B13": "IR13",  "B14": "IR14",  "B15": "IR15",
    "B16": "IR16",
}

SATAID_TO_PWARDS_AHI = {v: k for k, v in PWARDS_AHI_TO_SATAID.items()}

PWARDS_BAND_ORDER = [f"B{n:02d}" for n in range(1, 17)]


def fetch_manifest(base_url: str, api_code: str) -> Optional[dict]:
    url = f"{base_url.rstrip('/')}/data/api/sataid/stream"
    try:
        resp = requests.get(url, params={"api_code": api_code}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"PWARDS manifest fetch failed: {e}")
        return None


def download_band_file(base_url: str, api_code: str, path: str) -> Optional[bytes]:
    url = f"{base_url.rstrip('/')}/data/api/sataid/stream/load"
    try:
        resp = requests.get(url, params={"api_code": api_code, "path": path}, timeout=60)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        log.error(f"PWARDS band download failed ({path}): {e}")
        return None
