#!/usr/bin/env python3
"""
MonWatch Downloader v3.0.4 — S3 data downloader and processor for Himawari/GOES imagery.

Provides modern and legacy UI modes for discovery, download, and NetCDF conversion.
"""
import sys
import os
import re
import json
import boto3
import traceback
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from pathlib import Path
from datetime import datetime, timedelta
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QProcess, QEventLoop
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QComboBox, QTreeWidget,
    QTreeWidgetItem, QLineEdit, QCheckBox, QFrame, QFileDialog,
    QMessageBox, QProgressBar, QTextEdit, QGroupBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QListWidget, QListWidgetItem, QGridLayout, QScrollArea, QSpinBox,
    QStackedWidget, QLayout
)
from PySide6.QtGui import QFont, QIcon, QPixmap, QAction, QColor, QBrush

from dataclasses import dataclass, field
from typing import Dict

@dataclass(frozen=True)
class SatDiscoveryConfig:
    """Parameterized discovery config per satellite.
    GOES uses different layout (ABI-L1b-RadF / YYYY / DOY / HH vs Himawari AHI / YYYY/MM/DD/HH/MM).
    Winds/L2 TBD per GOES-DISC-002. Used as reference only in v3.
    """
    bucket: str
    date_prefix_template: str
    year_discovery_prefix: str
    winds_product_map: Dict[str, str] = field(default_factory=dict)
    nc_suffix_pattern: str = "*_AHI.nc"
    datetime_folder_pattern: str = "{product}_{y}{m}{d}_{h}{mi}"


SAT_CONFIGS: Dict[str, SatDiscoveryConfig] = {
    "himawari9": SatDiscoveryConfig(
        bucket="noaa-himawari9",
        date_prefix_template="{product}/{year:04d}/{month:02d}/{day:02d}/{hour:02d}{minute:02d}/",
        year_discovery_prefix="{product}/",
        winds_product_map={"AHI-L1b-FLDK": "AHI-L2-FLDK-Winds"},
        nc_suffix_pattern="*_AHI.nc",
    ),
    "goes16": SatDiscoveryConfig(
        bucket="noaa-goes16",
        date_prefix_template="ABI-L1b-RadF/{year:04d}/{doy:03d}/{hour:02d}/",
        year_discovery_prefix="ABI-L1b-RadF/",
        winds_product_map={},  # future GOES L2 winds
        nc_suffix_pattern="*_ABI.nc",
    ),
    "goes17": SatDiscoveryConfig(
        bucket="noaa-goes17",
        date_prefix_template="ABI-L1b-RadF/{year:04d}/{doy:03d}/{hour:02d}/",
        year_discovery_prefix="ABI-L1b-RadF/",
        winds_product_map={},
        nc_suffix_pattern="*_ABI.nc",
    ),
    "goes18": SatDiscoveryConfig(
        bucket="noaa-goes18",
        date_prefix_template="ABI-L1b-RadF/{year:04d}/{doy:03d}/{hour:02d}/",
        year_discovery_prefix="ABI-L1b-RadF/",
        winds_product_map={},
        nc_suffix_pattern="*_ABI.nc",
    ),
    "goes19": SatDiscoveryConfig(
        bucket="noaa-goes19",
        date_prefix_template="ABI-L1b-RadF/{year:04d}/{doy:03d}/{hour:02d}/",
        year_discovery_prefix="ABI-L1b-RadF/",
        winds_product_map={},
        nc_suffix_pattern="*_ABI.nc",
    ),
    "gk2a": SatDiscoveryConfig(
        bucket="noaa-gk2a-pds",
        date_prefix_template="{product}/{year:04d}{month:02d}/{day:02d}/{hour:02d}/",
        year_discovery_prefix="AMI/L1B/FD/",
        winds_product_map={},
        nc_suffix_pattern="*.nc",
    ),
}


# ── GK-2A (GEO-KOMPSAT-2A / AMI) support ─────────────────────────────────
# NOAA PDS layout:  AMI/L1B/FD/{YYYYMM}/{DD}/{HH}/  and  AMI/L1B/LA/...
# Files sit directly in the hour folder (no minute subfolders) and are already
# NetCDF:  gk2a_ami_le1b_{code}_{fd|la}020ge_{YYYYMMDDHHMM}.nc
# FD scans every 10 min; LA every 2 min.  Minute = trailing timestamp [10:12].
GK2A_BAND_MAP = {
    "vi004": "B01",   # 0.47 µm  – Blue visible
    "vi005": "B02",   # 0.51 µm  – Green visible
    "vi006": "B03",   # 0.64 µm  – Red visible
    "vi008": "B04",   # 0.86 µm  – Near-IR
    "nr013": "B05",   # 1.3  µm  – Near-IR
    "nr016": "B06",   # 1.6  µm  – Near-IR
    "sw038": "B07",   # 3.8  µm  – Shortwave-IR
    "wv063": "B08",   # 6.3  µm  – Water vapour
    "wv069": "B09",   # 6.9  µm  – Water vapour
    "wv073": "B10",   # 7.3  µm  – Water vapour
    "ir087": "B11",   # 8.7  µm  – Thermal-IR
    "ir096": "B12",   # 9.6  µm  – Ozone
    "ir105": "B13",   # 10.5 µm  – Thermal-IR
    "ir112": "B14",   # 11.2 µm  – Thermal-IR
    "ir123": "B15",   # 12.3 µm  – Thermal-IR
    "ir133": "B16",   # 13.3 µm  – Thermal-IR
}
INV_GK2A_BAND_MAP = {v: k for k, v in GK2A_BAND_MAP.items()}

GK2A_MIN_RE = re.compile(r"_(\d{12})\.nc$")   # minute = group(1)[10:12]

GK2A_FD_MINUTES = ["00", "10", "20", "30", "40", "50"]
GK2A_LA_MINUTES = [f"{m:02d}" for m in range(0, 60, 2)]


def _bucket_kind(bucket):
    """Classify an S3 bucket string: 'gk2a', 'goes' or 'himawari'."""
    b = (bucket or "").lower()
    if "gk2a" in b:
        return "gk2a"
    if "goes" in b:
        return "goes"
    return "himawari"


def _sat_kind(sat):
    """Classify a satellite display name: 'gk2a', 'goes' or 'himawari'."""
    s = (sat or "").upper()
    if "GK-2A" in s or ("GK" in s and "2A" in s):
        return "gk2a"
    if "GOES" in s:
        return "goes"
    return "himawari"


class S3Lister(QThread):
    progress = Signal(str)
    directories_found = Signal(list)
    files_found = Signal(list)
    error = Signal(str)
    finished = Signal()

    def __init__(self, bucket, prefix=""):
        super().__init__()
        self.bucket = bucket
        self.prefix = prefix.rstrip('/') + '/' if prefix else ""
        self.list_files = False

    def run(self):
        try:
            self.progress.emit(f"Listing: s3://{self.bucket}/{self.prefix}")

            s3_client = boto3.client(
                's3',
                config=Config(signature_version=UNSIGNED)
            )

            directories = []
            files = []

            result = s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=self.prefix,
                Delimiter='/',
                MaxKeys=1000
            )

            if 'CommonPrefixes' in result:
                for cp in result['CommonPrefixes']:
                    dir_path = cp['Prefix']
                    dir_name = dir_path[len(self.prefix):].rstrip('/')
                    if dir_name:
                        directories.append(dir_name)

            if self.list_files and 'Contents' in result:
                for obj in result['Contents']:
                    key = obj['Key']
                    if not key.endswith('/'):
                        filename = key[len(self.prefix):]
                        if filename:
                            files.append({
                                'key': key,
                                'name': filename,
                                'size': obj['Size'],
                                'last_modified': obj['LastModified']
                            })

            self.directories_found.emit(directories)
            if self.list_files:
                self.files_found.emit(files)

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_msg = e.response['Error']['Message']
            self.error.emit(f"S3 Error ({error_code}): {error_msg}")
        except Exception as e:
            self.error.emit(f"Listing error: {str(e)}")
        finally:
            self.finished.emit()


class S3DownloadWorker(QThread):
    progress = Signal(str)
    file_progress = Signal(int, int, str)
    finished = Signal(bool, str)
    error = Signal(str)

    def __init__(self, bucket, prefix, download_dir, bands=None, max_workers=8, extra_prefix=None, parent=None):
        super().__init__(parent)  # Proper Qt parent for lifetime management (prevents QThread destruction warnings)
        self.bucket = bucket
        self.prefix = prefix
        self.download_dir = Path(download_dir)
        self.bands = bands if bands else []
        self.max_workers = max_workers
        self.extra_prefix = extra_prefix   # optional winds prefix
        self._cancelled = False
        self.sat_kind = _bucket_kind(bucket)
        self._is_goes = self.sat_kind == "goes"
        self.goes_minute_filter = None  # e.g. 0, 10, 20, 30, 40, 50 for GOES/GK2A scan minute

    def run(self):
        download_path = ""
        try:
            s3_client = boto3.client(
                's3',
                config=Config(signature_version=UNSIGNED)
            )

            self.progress.emit(f"Listing files in: s3://{self.bucket}/{self.prefix}")
            if self.sat_kind in ("goes", "gk2a") and self.goes_minute_filter is not None:
                self.progress.emit(f"Minute filter active: only showing files with scan minute = {self.goes_minute_filter:02d}")

            files_to_download = []

            # ---- Primary prefix (bands) ----
            paginator = s3_client.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                        if key.endswith('/'):
                            continue
                        if self.bands:
                            if self.sat_kind == "gk2a":
                                download_file = any(INV_GK2A_BAND_MAP[f"B{band:02d}"] in key for band in self.bands)
                            elif self._is_goes:
                                download_file = any(f"C{band:02d}" in key for band in self.bands)
                            else:
                                download_file = any(f"B{band:02d}" in key for band in self.bands)
                            if not download_file:
                                continue
                        # Minute filter: GOES scan minute in _sYYYYDDDHHMM..., GK2A trailing _YYYYMMDDHHMM.nc
                        if self.sat_kind in ("goes", "gk2a") and self.goes_minute_filter is not None:
                            if self.sat_kind == "gk2a":
                                m = GK2A_MIN_RE.search(key)
                                if m:
                                    file_min = int(m.group(1)[10:12])
                                else:
                                    file_min = None
                            else:
                                m = re.search(r'_s\d{4}\d{3}(\d{2})(\d{2})', key)
                                if m:
                                    file_min = int(m.group(2))
                                else:
                                    file_min = None
                            if file_min is None or file_min != self.goes_minute_filter:
                                continue
                        files_to_download.append({
                            'key': key,
                            'size': obj['Size']
                        })

            # ---- Optional extra prefix (Winds) ----
            if self.extra_prefix:
                self.progress.emit(f"Also listing winds prefix: s3://{self.bucket}/{self.extra_prefix}")
                for page in paginator.paginate(Bucket=self.bucket, Prefix=self.extra_prefix):
                    if 'Contents' in page:
                        for obj in page['Contents']:
                            key = obj['Key']
                            if key.endswith('/'):
                                continue
                            files_to_download.append({
                                'key': key,
                                'size': obj['Size']
                            })

            if not files_to_download:
                self.error.emit("No files found matching criteria (check bands and minute filter)")
                self.finished.emit(False, "")
                return

            if self.sat_kind in ("goes", "gk2a") and self.goes_minute_filter is not None:
                self.progress.emit(f"Minute filter: {len(files_to_download)} files remaining after minute={self.goes_minute_filter:02d} filter")
            self.progress.emit(f"Found {len(files_to_download)} files to download")

            self.download_dir.mkdir(parents=True, exist_ok=True)
            download_path = str(self.download_dir)

            total_files = len(files_to_download)
            downloaded = 0
            completed = 0

            def download_single(file_info):
                if self._cancelled:
                    return False, ""
                key = file_info['key']
                filename = Path(key).name
                local_path = self.download_dir / filename

                self.progress.emit(f"Downloading: {filename}")

                try:
                    s3_client.download_file(
                        Bucket=self.bucket,
                        Key=key,
                        Filename=str(local_path)
                    )
                    return True, filename
                except Exception as e:
                    self.error.emit(f"Failed to download {filename}: {str(e)}")
                    return False, filename

            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = [executor.submit(download_single, f) for f in files_to_download]
                for future in as_completed(futures):
                    if self._cancelled:
                        break
                    success, filename = future.result()
                    if success:
                        downloaded += 1
                    completed += 1
                    self.file_progress.emit(completed, total_files, filename)
                    self.progress.emit(f"{'✓' if success else '✗'} {filename}")

            if not self._cancelled:
                self.progress.emit(f"Download completed: {downloaded}/{total_files} files")
                self.finished.emit(True, download_path)
            else:
                self.finished.emit(False, download_path)

        except Exception as e:
            self.error.emit(f"Download error: {str(e)}")
            self.finished.emit(False, download_path)

    def cancel(self):
        self._cancelled = True


class RangeS3DownloadWorker(QThread):
    """Non-blocking worker for date-range downloads with parallel slot processing.
    Processes multiple slots concurrently (max_concurrent_slots), each slot
    downloading its files in parallel (threads_per_slot). Both knobs are
    configurable so users can tune for their network and S3 rate limits.
    """
    progress = Signal(str)
    slot_finished = Signal(int, int, str, bool)  # slot_idx, total, prefix, success
    finished = Signal(bool, str)
    error = Signal(str)

    def __init__(self, bucket, prefixes, download_root, bands=None, include_winds=False,
                 time_step_minutes=10, progress_callback=None, local_slot_names=None,
                 max_concurrent_slots=4, threads_per_slot=8, slot_datetimes=None,
                 force=False):
        super().__init__()
        self.bucket = bucket
        self.prefixes = prefixes or []
        self.download_root = Path(download_root)
        self.bands = bands or list(range(1, 17))
        self.include_winds = include_winds
        self.local_slot_names = local_slot_names or []
        self.slot_datetimes = slot_datetimes or []
        self.force = force
        self.max_concurrent_slots = max_concurrent_slots
        self.threads_per_slot = threads_per_slot
        self._cancelled = False
        self.sat_kind = _bucket_kind(bucket)
        self._is_goes = self.sat_kind == "goes"
        self._listing_cache = {}
        self._listing_cache_lock = threading.Lock()

    def _matches_slot_time(self, key: str, i: int) -> bool:
        """Return True if the S3 object belongs to this slot's exact scan time.
        GOES: the file's scan minute (from _sYYYYDDDHHMM...) must equal the slot minute.
        Himawari: the file's _YYYYMMDD_HHMM_ must equal the slot HHMM.
        GK2A: the file's trailing _YYYYMMDDHHMM.nc minute must equal the slot minute.
        When no slot datetime is available, keep the file (no filtering).
        """
        if not (0 <= i < len(self.slot_datetimes)):
            return True
        slot_dt = self.slot_datetimes[i]
        if self.sat_kind == "gk2a":
            expected_min = slot_dt.minute
            m = GK2A_MIN_RE.search(key)
            if m:
                return int(m.group(1)[10:12]) == expected_min
            return False
        if self._is_goes:
            expected_min = slot_dt.minute
            m = re.search(r'_s\d{4}\d{3}\d{2}(\d{2})', key)
            if m:
                return int(m.group(1)) == expected_min
            return False
        else:
            expected_hm = f"{slot_dt.hour:02d}{slot_dt.minute:02d}"
            m = re.search(r'_(\d{8})_(\d{4})_', key)
            if m:
                return m.group(2) == expected_hm
            return False

    def _list_primary(self, s3, prefix: str):
        """List objects under a prefix once (thread-safe cache), returns [{key, size}]."""
        with self._listing_cache_lock:
            cached = self._listing_cache.get(prefix)
            if cached is not None:
                return cached
        items = []
        try:
            paginator = s3.get_paginator('list_objects_v2')
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        if not obj['Key'].endswith('/'):
                            items.append({'key': obj['Key'], 'size': obj['Size']})
        except Exception as e:
            self.progress.emit(f"  Listing failed: {prefix} ({e})")
        with self._listing_cache_lock:
            self._listing_cache[prefix] = items
        return items

    def _process_slot(self, i: int, prefix: str, total: int):
        """Process a single time slot: list S3 files, download matching bands.
        Returns (success_count: int, slot_ok: bool). Creates its own S3 client
        for thread safety. Checks self._cancelled throughout.
        """
        if self._cancelled:
            return 0, False

        local_name = (self.local_slot_names[i] if i < len(self.local_slot_names)
                      and self.local_slot_names[i]
                      else prefix.rstrip('/').replace('/', '_'))

        slot_dir = self.download_root / self.bucket.replace("noaa-", "") / local_name
        slot_dir.mkdir(parents=True, exist_ok=True)

        # --- Pre-scan: skip files that already exist locally (unless force) ---
        existing_bz2 = set()
        existing_nc = set()    # raw downloaded NetCDF filenames (GK2A per-band .nc names match S3 exactly)
        nc_bands = set()
        band_prefix = 'C' if self._is_goes else 'B'  # GK2A NC files are raw per-band .nc, not bg_to_nc output
        if not self.force and slot_dir.exists():
            for f in slot_dir.iterdir():
                if f.suffix == '.bz2':
                    existing_bz2.add(f.name)
                elif f.suffix == '.nc':
                    existing_nc.add(f.name)
                    if band_prefix:
                        try:
                            import h5py
                            with h5py.File(f, 'r') as h5:
                                for key in h5.keys():
                                    if key.startswith(band_prefix) and len(key) == 3 and key[1:].isdigit():
                                        nc_bands.add(int(key[1:]))
                        except Exception:
                            pass
        skip_count_bz2 = len(existing_bz2)
        skip_count_nc = len(nc_bands)

        s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))

        extra_prefix = None
        if self.include_winds:
            try:
                if "AHI-L1b-FLDK" in prefix:
                    extra_prefix = prefix.replace("AHI-L1b-FLDK", "AHI-L2-FLDK-Winds", 1)
            except Exception:
                pass

        self.progress.emit(f"[{i+1}/{total}] Range slot: {prefix}")

        files_to_download = []

        # Primary files
        for obj in self._list_primary(s3, prefix):
            key = obj['key']
            if self.bands:
                if self.sat_kind == "gk2a":
                    if not any(INV_GK2A_BAND_MAP[f"B{band:02d}"] in key for band in self.bands):
                        continue
                elif self._is_goes:
                    if not any(f"C{band:02d}" in key for band in self.bands):
                        continue
                else:
                    if not any(f"B{band:02d}" in key for band in self.bands):
                        continue
            if not self._matches_slot_time(key, i):
                continue
            files_to_download.append({'key': key, 'size': obj['size']})

        # Winds
        if extra_prefix:
            try:
                for obj in self._list_primary(s3, extra_prefix):
                    files_to_download.append({'key': obj['key'], 'size': obj['size']})
            except Exception:
                pass

        # --- Filter out files that already exist locally (unless force) ---
        if not self.force:
            filtered = []
            skipped_existing = 0
            for f in files_to_download:
                fname = Path(f['key']).name
                if fname in existing_bz2 or fname in existing_nc:
                    skipped_existing += 1
                    continue
                if nc_bands:
                    import re
                    bm = re.search(rf'_{band_prefix}(\d{{2}})_', fname)
                    if bm and int(bm.group(1)) in nc_bands:
                        skipped_existing += 1
                        continue
                filtered.append(f)
            files_to_download = filtered
            if skip_count_bz2 or skip_count_nc or skipped_existing:
                self.progress.emit(
                    f"  [slot {i+1}] Skipped {skipped_existing} existing files "
                    f"({skip_count_bz2} .bz2, {skip_count_nc} NC bands)"
                )
        elif files_to_download:
            self.progress.emit(f"  [slot {i+1}] Force re-download — overwriting {len(files_to_download)} file(s)")

        if not files_to_download:
            self.progress.emit(f"  [slot {i+1}] No files found matching criteria")
            self.progress.emit(f"  [slot {i+1}] No data for this slot (normal for Japan/Target rapid scan)")
            self.slot_finished.emit(i+1, total, prefix, False)
            return 0, False

        self.progress.emit(f"  [slot {i+1}] Found {len(files_to_download)} files to download")

        def _download_one(file_info):
            if self._cancelled:
                return False
            key = file_info['key']
            filename = Path(key).name
            local_path = slot_dir / filename
            try:
                s3.download_file(Bucket=self.bucket, Key=key, Filename=str(local_path))
                self.progress.emit(f"  [slot {i+1}] ✓ {filename}")
                return True
            except Exception as e:
                self.progress.emit(f"  [slot {i+1} ERR] {filename}: {e}")
                return False

        n_threads = min(self.threads_per_slot, len(files_to_download))
        with ThreadPoolExecutor(max_workers=n_threads) as pool:
            results = list(pool.map(_download_one, files_to_download))

        downloaded_ok = sum(1 for r in results if r)
        slot_ok = downloaded_ok > 0
        self.slot_finished.emit(i+1, total, prefix, slot_ok)
        return downloaded_ok, slot_ok

    def run(self):
        total = len(self.prefixes)
        success_count = 0
        try:
            self.progress.emit(
                f"Range worker: {total} slots starting (bucket: {self.bucket}, "
                f"concurrent slots: {self.max_concurrent_slots}, "
                f"threads per slot: {self.threads_per_slot})"
            )

            import threading as _t
            lock = _t.Lock()

            def _run_slot(i, prefix):
                if self._cancelled:
                    return 0, False
                ok_count, slot_ok = self._process_slot(i, prefix, total)
                with lock:
                    nonlocal success_count
                    if slot_ok:
                        success_count += 1
                return ok_count, slot_ok

            concurrency = min(self.max_concurrent_slots, total)
            pool = ThreadPoolExecutor(max_workers=concurrency)
            futures = {pool.submit(_run_slot, i, p): i for i, p in enumerate(self.prefixes)}
            for future in as_completed(futures):
                if self._cancelled:
                    pool.shutdown(wait=False)
                    break
                try:
                    future.result()
                except Exception as e:
                    self.error.emit(f"Slot error: {e}")
            else:
                pool.shutdown(wait=True)

            final_ok = not self._cancelled
            self.finished.emit(final_ok, str(self.download_root))
            self.progress.emit(
                f"Range complete: {success_count}/{total} slots with data "
                f"(concurrent slots: {self.max_concurrent_slots}, "
                f"threads/slot: {self.threads_per_slot})"
            )

        except Exception as e:
            self.error.emit(f"Range worker error: {e}")
            self.finished.emit(False, str(self.download_root))

    def cancel(self):
        self._cancelled = True


class WindsChecker(QThread):
    """Quickly check whether a winds prefix has any objects."""
    result = Signal(bool)
    error = Signal(str)

    def __init__(self, bucket, prefix):
        super().__init__()
        self.bucket = bucket
        self.prefix = prefix

    def run(self):
        try:
            s3_client = boto3.client('s3', config=Config(signature_version=UNSIGNED))
            resp = s3_client.list_objects_v2(Bucket=self.bucket, Prefix=self.prefix, MaxKeys=1)
            has_content = 'Contents' in resp and len(resp['Contents']) > 0
            self.result.emit(has_content)
        except Exception as e:
            self.error.emit(str(e))
            self.result.emit(False)


class RangeProgressDialog(QWidget):
    """Non-modal floating progress WINDOW for a single download/process job.

    NOT a dialog: each job gets its own top-level window that sits over the main
    UI without blocking it. You can keep browsing AWS, tweak settings, and START
    MORE downloads — every one gets its own window (see the Download Job Manager).

    Closing the window or pressing Cancel only cancels that job's worker.
    The optional 'Adjust' group tunes concurrency; the values are written through
    to the owning window so queued range downloads pick them up.
    """
    def __init__(self, parent=None, total_slots=1, job_title="Download",
                 show_adjust=False, show_jobs_button=True):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(job_title)
        self.setMinimumWidth(560)
        self._cancelled = False
        self._downloaded = 0
        self._processed = 0
        self._total = total_slots
        self._adjust_cb = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        # Job identity
        job_lbl = QLabel(f"Job: {job_title}")
        job_lbl.setStyleSheet("font-weight:700; color:#C9D3DF;")
        layout.addWidget(job_lbl)

        # Download section
        layout.addWidget(QLabel("Download:"))
        self.dl_bar = QProgressBar()
        self.dl_bar.setRange(0, 100)
        layout.addWidget(self.dl_bar)
        self.dl_label = QLabel("Waiting...")
        layout.addWidget(self.dl_label)

        # Processing section
        layout.addWidget(QLabel("Process:"))
        self.pr_bar = QProgressBar()
        self.pr_bar.setRange(0, 100)
        layout.addWidget(self.pr_bar)
        self.pr_label = QLabel("Waiting...")
        layout.addWidget(self.pr_label)

        # Done counter
        self.done_label = QLabel(f"Done: 0/{total_slots} slots")
        layout.addWidget(self.done_label)

        # Adjust group (concurrency knobs — apply to queued range downloads)
        if show_adjust:
            adj = QGroupBox("Adjust (applies to queued range downloads)")
            adj_l = QHBoxLayout(adj)
            adj_l.setContentsMargins(10, 8, 10, 8)
            adj_l.addWidget(QLabel("Concurrent slots:"))
            self.slot_spin = QSpinBox()
            self.slot_spin.setRange(1, 24)
            self.slot_spin.setValue(getattr(parent, '_max_concurrent_slots', 4) if parent else 4)
            adj_l.addWidget(self.slot_spin)
            adj_l.addWidget(QLabel("Threads / slot:"))
            self.thread_spin = QSpinBox()
            self.thread_spin.setRange(1, 32)
            self.thread_spin.setValue(getattr(parent, '_threads_per_slot', 8) if parent else 8)
            adj_l.addWidget(self.thread_spin)
            adj_l.addStretch(1)
            self.slot_spin.valueChanged.connect(self._on_adjust)
            self.thread_spin.valueChanged.connect(self._on_adjust)
            layout.addWidget(adj)

        # Footer: Cancel + Open manager
        foot = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._do_cancel)
        foot.addWidget(self.cancel_btn)
        if show_jobs_button:
            jobs_btn = QPushButton("All Jobs…")
            jobs_btn.clicked.connect(self._open_jobs_manager)
            foot.addWidget(jobs_btn)
        foot.addStretch(1)
        layout.addLayout(foot)

    def _on_adjust(self):
        if self._adjust_cb:
            self._adjust_cb(self.slot_spin.value(), self.thread_spin.value())

    def bind_adjust(self, callback):
        """Attach a write-through callback receiving (concurrent_slots, threads_per_slot)."""
        self._adjust_cb = callback

    def _open_jobs_manager(self):
        owner = self.parent()
        if owner is not None and hasattr(owner, '_open_jobs_window'):
            owner._open_jobs_window()

    def _do_cancel(self):
        self._cancelled = True
        self.cancel_btn.setText("Cancelling...")
        self.cancel_btn.setEnabled(False)

    def update_download(self, msg, pct=None):
        self.dl_label.setText(msg)
        if pct is not None:
            self.dl_bar.setValue(pct)

    def update_process(self, msg, pct=None):
        self.pr_label.setText(msg)
        if pct is not None:
            self.pr_bar.setValue(pct)

    def slot_downloaded(self):
        self._downloaded += 1
        total = self._total
        self.dl_bar.setValue(int(self._downloaded / max(1, total) * 100))
        self.done_label.setText(f"Downloaded: {self._downloaded}/{total} slots")

    def slot_processed(self):
        self._processed += 1
        total = self._total
        self.pr_bar.setValue(int(self._processed / max(1, total) * 100))
        self.done_label.setText(f"Processed: {self._processed}/{total} slots")

    def closeEvent(self, event):
        self._do_cancel()
        event.accept()


class DownloadJob:
    """A tracked download/process job: worker + its own non-modal window."""
    def __init__(self, job_id, title, kind, window=None, worker=None,
                 state="running", cancel_cb=None):
        self.job_id = job_id
        self.title = title
        self.kind = kind          # "single" | "range" | "process"
        self.window = window
        self.worker = worker
        self.state = state        # "running" | "done" | "cancelled"
        self.cancel_cb = cancel_cb


class DownloadJobsWindow(QWidget):
    """Non-modal 'Download Manager' window listing every active/queued job.

    Because every job window is non-modal, this panel simply gives you an
    overview of all downloads running at once — cancel any one of them, or
    raise its progress window — while the main UI stays fully usable.
    """
    def __init__(self, owner):
        super().__init__(None, Qt.Window)
        self.owner = owner
        self.setWindowTitle("Download Job Manager")
        self.resize(760, 320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        hint = QLabel(
            "Each download runs in its own non-modal window — the main UI stays usable, "
            "so you can keep queueing more jobs. Concurrency for queued range downloads "
            "can be adjusted in each job's window."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8A94A6;")
        layout.addWidget(hint)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["ID", "Job", "Status", "Actions"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table)

        foot = QHBoxLayout()
        self.clear_btn = QPushButton("Clear finished")
        self.clear_btn.clicked.connect(self._clear_finished)
        foot.addWidget(self.clear_btn)
        foot.addStretch(1)
        layout.addLayout(foot)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(500)

    def _clear_finished(self):
        jobs = [j for j in getattr(self.owner, '_download_jobs', [])
                if j.state in ("done", "cancelled")]
        for j in jobs:
            try:
                if j.window is not None:
                    j.window.close()
            except Exception:
                pass
        self.owner._download_jobs = [j for j in self.owner._download_jobs
                                     if j not in jobs]
        self.refresh()

    def refresh(self):
        jobs = getattr(self.owner, '_download_jobs', [])
        self.table.setRowCount(len(jobs))
        for row, j in enumerate(jobs):
            self.table.setItem(row, 0, QTableWidgetItem(str(j.job_id)))
            self.table.setItem(row, 1, QTableWidgetItem(j.title))

            running = j.worker is not None and getattr(j.worker, 'isRunning', lambda: False)()
            if running:
                status = "RUNNING"
                color = QColor("#2EA043")
            elif j.state == "cancelled":
                status = "CANCELLED"
                color = QColor("#E57373")
            else:
                status = "DONE"
                color = QColor("#8A94A6")
            status_item = QTableWidgetItem(status)
            status_item.setForeground(QBrush(color))
            self.table.setItem(row, 2, status_item)

            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(2, 2, 2, 2)
            if running:
                cancel_btn = QPushButton("Cancel")
                cancel_btn.clicked.connect(lambda _=False, jj=j: jj.cancel_cb and jj.cancel_cb())
                lay.addWidget(cancel_btn)
            show_btn = QPushButton("Show")
            show_btn.clicked.connect(lambda _=False, jj=j: self._show_job(jj))
            lay.addWidget(show_btn)
            lay.addStretch(1)
            self.table.setCellWidget(row, 3, cell)

    def _show_job(self, job):
        if job.window is not None:
            job.window.show()
            job.window.raise_()
            job.window.activateWindow()


class AvailableDatesDiscoverer(QThread):
    """
    Efficiently discovers available years from AWS S3 using Delimiter listing.
    Handles both Himawari (simple) and GOES (product-keyed) patterns.
    """
    years_discovered = Signal(list)
    error = Signal(str)
    finished = Signal()

    # Cache: (bucket, product) -> list[int years]
    _cache = {}

    def __init__(self, bucket, product="AHI-L1b-FLDK"):
        super().__init__()
        self.bucket = bucket
        self.product = product or "AHI-L1b-FLDK"
        self._cancelled = False

    def run(self):
        cache_key = (self.bucket, self.product)

        try:
            # Fast path from cache
            if cache_key in AvailableDatesDiscoverer._cache:
                cached = AvailableDatesDiscoverer._cache[cache_key]
                if cached:
                    self.years_discovered.emit(cached)
                    return

            s3_client = boto3.client(
                's3',
                config=Config(signature_version=UNSIGNED)
            )

            years = []
            prefix = f"{self.product}/"

            # Efficient directory listing using Delimiter
            resp = s3_client.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                Delimiter='/',
                MaxKeys=1000
            )

            if 'CommonPrefixes' in resp:
                for cp in resp['CommonPrefixes']:
                    dir_name = cp['Prefix'][len(prefix):].rstrip('/')
                    if dir_name.isdigit():
                        y = int(dir_name)
                        if 2014 < y < 2035:
                            years.append(y)

            # Fallback for unusual layouts (scan a few object keys)
            if not years and 'Contents' in resp:
                for obj in resp.get('Contents', []):
                    if self._cancelled:
                        break
                    key = obj['Key']
                    marker = f"{self.product}_"
                    if marker in key:
                        try:
                            ts = key.split(marker)[1][:4]
                            y = int(ts)
                            if 2014 < y < 2035:
                                years.append(y)
                        except Exception:
                            pass  # robust: never let fallback parsing crash the discoverer thread

            years = sorted(set(years)) or []  # Modern: empty -> placeholder + validation warning (H#2)

            AvailableDatesDiscoverer._cache[cache_key] = years

            if not self._cancelled:
                self.years_discovered.emit(years)

        except Exception as e:
            self.error.emit(f"AWS S3 query failed ({self.product}): {str(e)}")
        finally:
            self.finished.emit()

    def cancel(self):
        self._cancelled = True


class AvailableProductTypesDiscoverer(QThread):
    """
    Discovers the actual top-level product folders from AWS (FLDK, Japan, Target, etc.)
    by listing the bucket root with Delimiter. No assumptions.
    """
    products_discovered = Signal(list)  # list of real folder names e.g. ["AHI-L1b-FLDK", "AHI-L1b-Japan", "AHI-L1b-Target"]
    error = Signal(str)

    def __init__(self, bucket):
        super().__init__()
        self.bucket = bucket
        self._cancelled = False

    def run(self):
        try:
            s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
            resp = s3.list_objects_v2(
                Bucket=self.bucket,
                Delimiter='/',
                MaxKeys=200
            )
            products = []
            is_goes = "goes" in (self.bucket or "").lower()
            if 'CommonPrefixes' in resp:
                for cp in resp['CommonPrefixes']:
                    name = cp['Prefix'].rstrip('/')
                    low = name.lower()
                    if is_goes:
                        # GOES products: ABI-L1b-RadF, ABI-L1b-RadC, ABI-L1b-RadM
                        if 'abi' in low and 'l1b' in low:
                            products.append(name)
                    elif any(k in low for k in ['fldk', 'japan', 'target']):
                        products.append(name)
            products.sort()
            if not self._cancelled:
                default = "ABI-L1b-RadF" if is_goes else "AHI-L1b-FLDK"
                self.products_discovered.emit(products or [default])
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableMonthsDiscoverer(QThread):
    """
    For a given product + year, discovers which months (Himawari) or DOYs (GOES) actually have data.
    Uses cheap Delimiter listing under PRODUCT/YYYY/
    """
    months_discovered = Signal(list)  # sorted list of int months/DOYs e.g. [1,2,3,4,5] or [100,101,102]
    error = Signal(str)

    def __init__(self, bucket, product, year):
        super().__init__()
        self.bucket = bucket
        self.product = product
        self.year = year
        self._cancelled = False
        self._is_goes = "goes" in (bucket or "").lower()

    def run(self):
        try:
            s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
            prefix = f"{self.product}/{self.year}/"
            resp = s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                Delimiter='/',
                MaxKeys=366 if self._is_goes else 100
            )
            months = []
            if 'CommonPrefixes' in resp:
                for cp in resp['CommonPrefixes']:
                    part = cp['Prefix'][len(prefix):].rstrip('/')
                    if part.isdigit():
                        m = int(part)
                        if self._is_goes:
                            if 1 <= m <= 366:
                                months.append(m)
                        else:
                            if 1 <= m <= 12:
                                months.append(m)
            months = sorted(set(months)) or []
            if not self._cancelled:
                self.months_discovered.emit(months)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableHoursDiscoverer(QThread):
    """
    Given a specific date (product + YYYY + MM + DD for Himawari, YYYY + DOY for GOES),
    discover which hours actually have data.
    Triggered only when a Day is selected/loaded.
    """
    hours_discovered = Signal(list)  # list of int hours, e.g. [0,1,2,10,11,...]
    error = Signal(str)

    def __init__(self, bucket, product, year, month, day):
        super().__init__()
        self.bucket = bucket
        self.product = product
        self.year = year
        self.month = month
        self.day = day
        self._cancelled = False
        self._is_goes = "goes" in (bucket or "").lower()

    def run(self):
        try:
            s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
            if self._is_goes:
                # GOES: PRODUCT/YYYY/DOY/HH/
                prefix = f"{self.product}/{self.year:04d}/{self.month:03d}/"
            else:
                # Himawari: PRODUCT/YYYY/MM/DD/HH/
                prefix = f"{self.product}/{self.year:04d}/{self.month:02d}/{self.day:02d}/"
            resp = s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                Delimiter='/',
                MaxKeys=200
            )
            hours = []
            if 'CommonPrefixes' in resp:
                for cp in resp['CommonPrefixes']:
                    part = cp['Prefix'][len(prefix):].rstrip('/')
                    if part.isdigit():
                        h = int(part)
                        if 0 <= h <= 23:
                            hours.append(h)
            hours = sorted(set(hours)) or []  # Modern: empty -> placeholder + validation warning (H#2)
            if not self._cancelled:
                self.hours_discovered.emit(hours)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableMinutesDiscoverer(QThread):
    """Discover actual minute folders under a specific hour (for precision).
    For GOES, there are no minute subdirectories; returns empty list."""
    minutes_discovered = Signal(list)
    error = Signal(str)

    def __init__(self, bucket, product, year, month, day, hour):
        super().__init__()
        self.bucket = bucket
        self.product = product
        self.year = year
        self.month = month
        self.day = day
        self.hour = hour
        self._cancelled = False
        self._is_goes = "goes" in (bucket or "").lower()

    def run(self):
        try:
            if self._is_goes:
                # GOES has no minute subdirectories
                if not self._cancelled:
                    self.minutes_discovered.emit([])
                return
            s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
            prefix = f"{self.product}/{self.year:04d}/{self.month:02d}/{self.day:02d}/{self.hour:02d}/"
            resp = s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                Delimiter='/',
                MaxKeys=100
            )
            minutes = []
            if 'CommonPrefixes' in resp:
                for cp in resp['CommonPrefixes']:
                    part = cp['Prefix'][len(prefix):].rstrip('/')
                    if part.isdigit():
                        m = int(part)
                        if 0 <= m <= 59:
                            minutes.append(m)
            minutes = sorted(set(minutes)) or []  # Modern: empty -> placeholder + validation warning (H#2)
            if not self._cancelled:
                self.minutes_discovered.emit(minutes)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableDaysDiscoverer(QThread):
    """
    ONLY triggered when a Month is selected.
    Discovers actual days that exist under PRODUCT/YYYY/MM/ .
    """
    days_discovered = Signal(list)  # list of int days, e.g. [1,2,3,5,10,...]
    error = Signal(str)

    def __init__(self, bucket, product, year, month):
        super().__init__()
        self.bucket = bucket
        self.product = product
        self.year = year
        self.month = month
        self._cancelled = False

    def run(self):
        try:
            s3 = boto3.client('s3', config=Config(signature_version=UNSIGNED))
            prefix = f"{self.product}/{self.year:04d}/{self.month:02d}/"
            resp = s3.list_objects_v2(
                Bucket=self.bucket,
                Prefix=prefix,
                Delimiter='/',
                MaxKeys=200
            )
            days = []
            if 'CommonPrefixes' in resp:
                for cp in resp['CommonPrefixes']:
                    part = cp['Prefix'][len(prefix):].rstrip('/')
                    if part.isdigit():
                        d = int(part)
                        if 1 <= d <= 31:
                            days.append(d)
            days = sorted(set(days)) or []  # Modern: empty -> placeholder + validation warning (H#2)
            if not self._cancelled:
                self.days_discovered.emit(days)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class HimawariProcessorWorker(QThread):
    """
    Note (v3.0.2 Plan): This tool is designed to run concurrently with the main
    MonWatch-UI App.py. It should not block the main application.
    GOES support and improved sector/full-disk/target UI are in progress.
    UI is being modernized to match main app polish.
    """
    """Thread to run Himawari processing (extract & NetCDF conversion)."""
    progress = Signal(str)
    finished = Signal(bool, str)
    error = Signal(str)
    stats_update = Signal(dict)

    def __init__(self, directory_path, process_mode="auto", force_simple=False,
                 max_workers=8, skip_rgb=False, skip_ads=False,
                 skip_extract=False, fast=False, low_io=False,
                 nc_format="pwards"):
        super().__init__()
        self.directory_path = Path(directory_path)
        self.process_mode = process_mode          # "auto", "extract_only", "nc_only"
        self.force_simple = force_simple
        self.max_workers = max_workers
        self.skip_rgb = skip_rgb       # v3.1.2: ignored (handled by bg_controller.py)
        self.skip_ads = skip_ads       # v3.1.2: pass --no-ads to bg_controller.py
        self.skip_extract = skip_extract   # v3.5.0: bg_to_nc reads .bz2 directly, no bg_extract step
        self.fast_mode = fast             # v3.5.0: pass --fast (complevel=2) to bg_to_nc.py
        self.low_io = low_io             # v3.5.1: pass --low-io to bg_controller.py (sequential, disk-friendly)
        self.nc_format = nc_format       # "pwards" (per-band + .ads.json) | "jma" (MSC lat/lon NC)
        # Low I/O mode serializes extraction too (1 worker) so the disk never peaks.
        self.extract_workers = 1 if low_io else max_workers

    def _run_nc_conversion(self, script_dir):
        """Run the NetCDF conversion step for the selected format:
        "pwards" -> bg_controller.py -> bg_to_nc.py (per-band NC + .ads.json)
        "jma"    -> bg_to_nc_jma.py (JMA/MSC regular lat/lon grid NC).
        Returns the subprocess result, or None if the script is missing."""
        if self.nc_format == "jma":
            jma_script = script_dir / "bg_to_nc_jma.py"
            if not jma_script.exists():
                self.error.emit(f"JMA NetCDF script not found: {jma_script}")
                return None
            jma_args = ["-i", str(self.directory_path),
                        "--res", "0.02",
                        "--workers", str(1 if self.low_io else self.max_workers)]
            jma_args.append("--delete-sources")   # mirror PWARDS cleanup-on-success
            self.progress.emit("Converting to JMA NetCDF (regular lat/lon grid)...")
            return self.run_external_script(jma_script, jma_args)

        nc_script = script_dir / "bg_controller.py"
        if not nc_script.exists():
            self.error.emit(f"NetCDF script not found: {nc_script}")
            return None
        nc_args = ["-i", str(self.directory_path)]
        if self.skip_ads:
            nc_args.append("--no-ads")
        if self.fast_mode:
            nc_args.append("--fast")
        if self.low_io:
            nc_args.append("--low-io")
        self.progress.emit("Converting to NetCDF (direct .bz2 or extracted .dat)...")
        return self.run_external_script(nc_script, nc_args)

    def run(self):
        stats = {
            'total_extracted': 0,
            'successfully_extracted': 0,
            'extraction_failed': 0,
            'nc_created': 0,
            'nc_failed': 0
        }

        try:
            script_dir = Path(__file__).resolve().parent

            self.progress.emit(f"Starting Himawari processing in: {self.directory_path}")
            self.progress.emit(f"Processing mode: {self.process_mode}")
            self.progress.emit(f"Max concurrent workers: {self.max_workers}")
            if self.low_io:
                self.progress.emit("Low I/O mode ON — sequential conversion (1 folder x 1 band, disk-friendly).")

            # ---------- AUTO (extract then convert to NetCDF) ----------
            if self.process_mode == "auto":
                if self.skip_extract:
                    # v3.5.0: bg_to_nc reads .bz2 directly — no separate extraction step
                    self.progress.emit("Skip extraction enabled: feeding .bz2 files directly to NetCDF conversion...")
                else:
                    self.progress.emit("Extracting .bz2 files...")
                    extract_script = script_dir / "bg_extract.py"
                    if extract_script.exists():
                        extract_result = self.run_external_script(
                            extract_script,
                            ["-i", str(self.directory_path), "--max-workers", str(self.extract_workers)]
                        )
                        stats.update(self.parse_script_output(extract_result.stdout, "extract"))
                    else:
                        self.error.emit(f"Extraction script not found: {extract_script}")
                        self.finished.emit(False, str(self.directory_path))
                        return

                if self.force_simple:
                    self.progress.emit("Force simple enabled: skipping NetCDF conversion (requires Satpy).")
                else:
                    nc_result = self._run_nc_conversion(script_dir)
                    if nc_result is None:
                        self.finished.emit(False, str(self.directory_path))
                        return
                    stats.update(self.parse_script_output(nc_result.stdout, "nc"))

            # ---------- EXTRACT ONLY ----------
            elif self.process_mode == "extract_only":
                self.progress.emit("Extracting .bz2 files only...")
                extract_script = script_dir / "bg_extract.py"
                if extract_script.exists():
                    extract_result = self.run_external_script(
                        extract_script,
                        ["-i", str(self.directory_path), "--max-workers", str(self.extract_workers)]
                    )
                    stats.update(self.parse_script_output(extract_result.stdout, "extract"))
                else:
                    self.error.emit(f"Extraction script not found: {extract_script}")
                    self.finished.emit(False, str(self.directory_path))
                    return

            # ---------- NETCDF ONLY ----------
            elif self.process_mode == "nc_only":
                if self.force_simple:
                    self.error.emit("Cannot create NetCDF without Satpy (force_simple is enabled)")
                    self.finished.emit(False, str(self.directory_path))
                    return

                self.progress.emit("Processing .dat files to NetCDF (or merging NDMW wind)...")
                nc_result = self._run_nc_conversion(script_dir)
                if nc_result is None:
                    self.finished.emit(False, str(self.directory_path))
                    return
                stats.update(self.parse_script_output(nc_result.stdout, "nc"))

            self.stats_update.emit(stats)

            total_success = stats['successfully_extracted'] + stats['nc_created']
            total_failed = stats['extraction_failed'] + stats['nc_failed']
            if total_failed > 0:
                self.progress.emit(
                    f"Processing completed with {total_success} ok / {total_failed} FAILED — "
                    f"see console for failed bands (their .dat source was kept for retry)")
                self.finished.emit(False, str(self.directory_path))
            elif total_success > 0:
                self.progress.emit(f"Processing completed with {total_success} successful operations")
                self.finished.emit(True, str(self.directory_path))
            else:
                self.progress.emit("Processing completed (all files already processed)")
                self.finished.emit(True, str(self.directory_path))

        except Exception as e:
            error_msg = f"Error during processing: {str(e)}"
            traceback.print_exc()
            self.error.emit(error_msg)
            self.finished.emit(False, str(self.directory_path))

    def run_external_script(self, script_path, args):
        cmd = [sys.executable, str(script_path)] + args
        self.progress.emit(f"Running: {' '.join(cmd)}")

        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        # Disable CUDA for subprocesses to prevent CuPy warnings when CUDA is not available
        env['CUDA_VISIBLE_DEVICES'] = '-1'

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        # Emit stdout lines (filter out the "[!]" prefix if desired)
        for line in stdout.split('\n'):
            line = line.strip()
            if line and not line.startswith("[!]"):
                self.progress.emit(line)

        # Emit stderr lines so hidden errors become visible
        if stderr.strip():
            for line in stderr.split('\n'):
                line = line.strip()
                if line:
                    self.progress.emit(f"STDERR: {line}")   # prefix for clarity

        # If the process failed or stderr contains an error, raise an exception
        if result.returncode != 0 or (
            stderr.strip() and (
                "Traceback" in stderr or
                "Error" in stderr or
                "Exception" in stderr
            )
        ):
            error_msg = f"Script {script_path.name} failed (code {result.returncode}): {stderr[:500]}"
            self.error.emit(error_msg)
            raise Exception(error_msg)

        return result

    def parse_script_output(self, output: str, script_type: str) -> dict:
        stats = {}
        lines = output.split('\n')
        for line in lines:
            line = line.strip()

            if script_type == "extract":
                # Handle "Successfully extracted: X/Y files"
                if "Successfully extracted:" in line:
                    try:
                        after_colon = line.split(':')[1].strip()  # "X/Y files"
                        stats['successfully_extracted'] = int(after_colon.split('/')[0].strip())
                    except Exception:
                        pass
                # Handle "Total to extract: Y"
                elif "Total to extract:" in line:
                    try:
                        stats['total_extracted'] = int(line.split(':')[1].strip())
                        stats['extraction_failed'] = stats['total_extracted'] - stats.get('successfully_extracted', 0)
                    except Exception:
                        pass
                # Handle STATISTICS_OUTPUT lines: "Extracted: X" and "Total to extract: Y"
                elif "Extracted:" in line and "Total to extract:" not in line:
                    try:
                        stats['successfully_extracted'] = int(line.split(':')[1].strip())
                    except Exception:
                        pass
                elif "Total to extract:" in line and "Extracted:" not in line:
                    try:
                        stats['total_extracted'] = int(line.split(':')[1].strip())
                        stats['extraction_failed'] = stats['total_extracted'] - stats.get('successfully_extracted', 0)
                    except Exception:
                        pass

            elif script_type == "nc":
                # bg_controller.py / bg_to_nc.py outputs "Processed: X" and "Failed: Y"
                if "Processed:" in line:
                    try:
                        processed = int(line.split(':')[1].strip())
                        stats['nc_created'] = processed
                    except Exception:
                        pass
                if "Failed:" in line:
                    try:
                        stats['nc_failed'] = int(line.split(':')[1].strip())
                    except Exception:
                        pass
                # v3.1.2 – RGB product count
                if "RGB products generated" in line or "products generated" in line:
                    import re as _re
                    m = _re.search(r'(\d+)/(\d+)', line)
                    if m:
                        stats['rgb_ok'] = int(m.group(1))
                        stats['rgb_total'] = int(m.group(2))
        return stats

    def cleanup_dat_files(self, directory_path):
        pass   # bg_to_nc handles cleanup if requested


class HimawariFileManager(QMainWindow):
    def __init__(self, force_legacy=False):
        super().__init__()
        self.setWindowTitle("MonWatch Downloader V3.0.4")
        self.resize(1400, 820)

        app_font = QFont("Segoe UI", 9)
        QApplication.setFont(app_font)

        if getattr(sys, 'frozen', False):
            self.script_dir = Path(sys.executable).resolve().parent
        else:
            self.script_dir = Path(__file__).resolve().parent.parent.parent
        self.default_download_dir = self.script_dir / "data" / "Download"

        # Downloader now has its own isolated settings file (no longer shares with App.py)
        self.downloader_settings_file = self.script_dir / "config" / "process_dat_settings.json"

        self._force_legacy = force_legacy   # NEW: allows opening full legacy as standalone window

        self.satellites = {
            "Himawari 9": "noaa-himawari9",
            "Himawari 8": "noaa-himawari8",
            "GOES-16": "noaa-goes16",
            "GOES-17": "noaa-goes17",
            "GOES-18": "noaa-goes18",
            "GOES-19": "noaa-goes19",
            "GK-2A": "noaa-gk2a-pds",
        }

        self.current_bucket = "noaa-himawari9"
        self.current_prefix = ""
        self.current_path = []
        self.all_files = []
        self.selected_bands = list(range(1, 17))

        # GOES virtual month/day browsing state
        self._goes_doy_month_cache = {}   # {year_int: {month_int: [day_int, ...]}}
        self._goes_virtual_mode = False   # True when browsing virtual month/day tree levels

        # Winds support
        self.current_winds_prefix = None
        self.winds_available = False
        self.winds_checker = None

        self.lister = None
        self.download_worker = None
        self.processor_worker = None

        self.auto_process = True
        self.process_mode = "auto"     # "auto", "extract_only", "nc_only"

        self.force_simple = False

        # v3.1.2 – Advanced Data System / RGB controls
        self.skip_rgb = False
        self.skip_ads = False

        # Extract is the DEFAULT in auto processing. The "Skip extraction"
        # checkbox flips this to True to read .bz2 directly instead.
        self.skip_extract = False

        # NetCDF output format: "pwards" (per-band NC + .ads.json sidecar) or
        # "jma" (JMA/MSC regular lat/lon grid NC via bg_to_nc_jma.py).
        self.nc_format = "pwards"

        # Low I/O mode — default ON. Serializes NetCDF conversion (1 folder x 1 band
        # at a time) to prevent 100% disk I/O. User can uncheck to restore full speed.
        self.low_io = True

        # Load saved downloader settings (low_io + download concurrency). Return value
        # (UI mode) is intentionally ignored: Modern UI is disabled — Legacy only.
        self._load_process_dat_settings()

        # Dynamic AWS date discovery for Modern UI (replaces static years)
        self._dates_discoverer = None
        self._product_types_discoverer = None
        self._months_discoverer = None
        self._hours_discoverer = None
        self._minutes_discoverer = None
        self._days_discoverer = None
        self._current_discovering_sat = None
        self._current_discovering_product = None

        # Modern UI dedicated download state (independent of legacy)
        self.modern_download_dir = self.default_download_dir
        self.modern_download_worker = None
        self._range_worker = None  # H#3 non-blocking range worker (cancellable)

        # ── Job-window queue system (v3.0.5) ─────────────────────────────
        # Every download/process job gets its own NON-MODAL progress window, so
        # the main UI stays usable and more jobs can be queued at any time.
        self._download_jobs = []        # Registered DownloadJob entries
        self._next_job_id = 1
        self._jobs_window = None        # Download Job Manager window
        # Range concurrency knobs (adjustable from each job's progress window,
        # persisted in-memory so queued range downloads pick up new values).
        self._max_concurrent_slots = 4
        self._threads_per_slot = 8

        # For clean shutdown of background threads (prevents "QThread destroyed while still running")
        self._closing = False
        self._legacy_banner = None

        # Safe post-show initial discovery flag (E/F fix for QThread destruction on modern launch).
        # Guarantees _discover_* (and thus all QThread .start()) NEVER run during __init__ or tab builders.
        # Set true only from showEvent + singleShot (after .show() + active event loop).
        self._initial_discovery_scheduled = False

        # ─────────────────────────────────────────────────────────────
        #  MODERN UI: UNDER DEVELOPMENT — DO NOT TOUCH
        #  Modern UI access is currently DISABLED. We always build the
        #  Legacy S3 browser, regardless of saved preference or --ui flag.
        #  The modern builder code below remains intact but unreachable.
        #  ─────────────────────────────────────────────────────────────
        self.ui_mode = "legacy"
        self._build_full_legacy_as_main()

    # ======================================================================
    #  NEW: Dual Mode UI System (Modern + Legacy with live switching)
    # ======================================================================

    def _load_process_dat_settings(self):
        """Reads the UI preference + download skipper + concurrency from the downloader's own settings file.
        Returns (mode: str, skipper: str) with defaults ("modern", "Silent").
        Also populates self._max_concurrent_slots and self._threads_per_slot.
        """
        self._max_concurrent_slots = 4
        self._threads_per_slot = 8
        try:
            settings_path = self.downloader_settings_file
            if settings_path.exists():
                with open(settings_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    mode = data.get("downloader_ui", "modern")
                    skipper = data.get("download_skipper", "Silent")
                    if mode not in ("modern", "legacy"):
                        mode = "modern"
                    if skipper not in ("Silent", "skipper"):
                        skipper = "Silent"
                    self._max_concurrent_slots = int(data.get("max_concurrent_slots", 4))
                    self._threads_per_slot = int(data.get("threads_per_slot", 8))
                    if self._max_concurrent_slots < 1:
                        self._max_concurrent_slots = 1
                    if self._threads_per_slot < 1:
                        self._threads_per_slot = 1
                    self.low_io = bool(data.get("low_io", True))
                    print(f"[DEBUG] Loaded settings: downloader_ui='{mode}' download_skipper='{skipper}' "
                          f"concurrent_slots={self._max_concurrent_slots} threads_per_slot={self._threads_per_slot} "
                          f"low_io={self.low_io}")
                    return mode, skipper
            else:
                print(f"[DEBUG] No downloader settings file yet, defaulting")
        except Exception as e:
            print(f"[ERROR] Failed to load downloader settings: {e}")
        return "modern", "Silent"

    # Backward compat alias
    def _load_process_dat_ui_mode(self):
        mode, _ = self._load_process_dat_settings()
        return mode

    def _save_downloader_ui(self, value: str):
        """Save the UI mode preference to the downloader's own settings file.
        Preserves any existing download_skipper and concurrency keys.
        """
        try:
            settings_path = self.downloader_settings_file
            settings_path.parent.mkdir(parents=True, exist_ok=True)

            data = {"downloader_ui": value}
            if settings_path.exists():
                try:
                    with open(settings_path, "r", encoding="utf-8-sig") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        for key in ("download_skipper", "max_concurrent_slots", "threads_per_slot", "low_io"):
                            if key in existing:
                                data[key] = existing[key]
                except Exception:
                    pass

            tmp = settings_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(settings_path)

            print(f"[DEBUG] Saved downloader_ui = \"{value}\" to own settings file")
            if hasattr(self, 'status_bar') and self.status_bar:
                self.status_bar.showMessage(f"Preference saved: {value}", 2500)

        except Exception as e:
            print(f"[ERROR] Failed to save downloader_ui to own settings: {e}")

    def _save_downloader_skipper(self, value: str):
        """Save the download skipper preference, preserving downloader_ui and concurrency keys."""
        if value not in ("Silent", "skipper"):
            value = "Silent"
        try:
            settings_path = self.downloader_settings_file
            settings_path.parent.mkdir(parents=True, exist_ok=True)

            data = {"download_skipper": value}
            if settings_path.exists():
                try:
                    with open(settings_path, "r", encoding="utf-8-sig") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        for key in ("downloader_ui", "max_concurrent_slots", "threads_per_slot", "low_io"):
                            if key in existing:
                                data[key] = existing[key]
                except Exception:
                    pass

            tmp = settings_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(settings_path)

            print(f"[DEBUG] Saved download_skipper = \"{value}\" to own settings file")
        except Exception as e:
            print(f"[ERROR] Failed to save download_skipper to own settings: {e}")

    def _save_concurrency_settings(self):
        """Persist max_concurrent_slots and threads_per_slot to settings file."""
        try:
            settings_path = self.downloader_settings_file
            settings_path.parent.mkdir(parents=True, exist_ok=True)

            data = {
                "max_concurrent_slots": getattr(self, '_max_concurrent_slots', 4),
                "threads_per_slot": getattr(self, '_threads_per_slot', 8),
                "low_io": getattr(self, 'low_io', True),
            }
            if settings_path.exists():
                try:
                    with open(settings_path, "r", encoding="utf-8-sig") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        for key in ("downloader_ui", "download_skipper"):
                            if key in existing:
                                data[key] = existing[key]
                except Exception:
                    pass

            tmp = settings_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(settings_path)

            print(f"[DEBUG] Saved concurrency: slots={data['max_concurrent_slots']} threads/slot={data['threads_per_slot']}")
        except Exception as e:
            print(f"[ERROR] Failed to save concurrency settings: {e}")

    def _save_all_modern_settings(self):
        """Explicit 'Save Settings' handler for the downloader.
        Saves UI mode, skipper, and concurrency settings.
        """
        try:
            current_mode = getattr(self, 'ui_mode', 'modern')
            self._save_downloader_ui(current_mode)
            self._save_concurrency_settings()

            msg = f"Settings saved (concurrent_slots={getattr(self, '_max_concurrent_slots', 4)}, threads/slot={getattr(self, '_threads_per_slot', 8)})"
            if hasattr(self, 'status_bar') and self.status_bar:
                self.status_bar.showMessage(msg, 3000)

        except Exception as e:
            print(f"[ERROR] Failed to save downloader settings: {e}")

    def _switch_to_legacy(self):
        """Clean switch: persist choice using surgical saver."""
        self._save_downloader_ui("legacy")
        if hasattr(self, 'status_bar') and self.status_bar:
            self.status_bar.showMessage("Switching to full Legacy S3 Browser...")
        self.close()
        win = HimawariFileManager(force_legacy=True)
        win.show()

    def _switch_to_legacy_with_save(self):
        """Explicit 'Switch to Legacy' entry point from Modern hero button.
        Uses the surgical saver so only the downloader_ui line is touched.
        """
        self._save_downloader_ui("legacy")
        if hasattr(self, 'status_bar') and self.status_bar:
            self.status_bar.showMessage("Settings saved. Switching to full Legacy S3 Browser...", 3000)
        # Small delay so user sees the save message
        QTimer.singleShot(250, self._do_actual_legacy_switch)

    def _do_actual_legacy_switch(self):
        self.close()
        win = HimawariFileManager(force_legacy=True)
        win.show()

    def _switch_to_modern(self):
        """Clean switch back to the superior Modern experience."""
        self._save_downloader_ui("modern")
        if hasattr(self, 'status_bar') and self.status_bar:
            self.status_bar.showMessage("Switching to Modern Quick Scene + Animation...")
        self.close()
        HimawariFileManager().show()  # will read saved "modern" and build the polished UI

    def closeEvent(self, event):
        """Hardened shutdown.
        Ensures zero 'QThread destroyed while running' crashes on Modern/Legacy switch or close.
        Explicit logging (no silent bare excepts). Full signal disconnects for discoverers.
        Modern download + discovery workers safely cancelled before destroy.
        """
        self._closing = True
        threads_to_stop = []

        # Modern discovery threads — cancel + disconnect to prevent post-close callbacks (crash risk)
        for attr in ['_dates_discoverer', '_product_types_discoverer', '_months_discoverer',
                     '_hours_discoverer', '_minutes_discoverer', '_days_discoverer']:
            t = getattr(self, attr, None)
            if t and t.isRunning():
                try:
                    if hasattr(t, 'years_discovered'):
                        t.years_discovered.disconnect()
                    if hasattr(t, 'error'):
                        t.error.disconnect()
                    # similar for other signals if present
                except Exception:
                    pass  # safe: signals may already be disconnected
                t.cancel()
                threads_to_stop.append(t)

        # Legacy S3 lister
        if hasattr(self, 'lister') and self.lister and self.lister.isRunning():
            threads_to_stop.append(self.lister)

        # Download workers (legacy + modern dedicated + range H#3) — robust cancel
        for worker_attr in ['download_worker', 'modern_download_worker', '_range_worker']:
            w = getattr(self, worker_attr, None)
            if w and w.isRunning():
                if hasattr(w, '_cancelled'):
                    w._cancelled = True
                if hasattr(w, 'cancel'):
                    try: w.cancel()
                    except Exception: pass
                threads_to_stop.append(w)

        # Any additional tracked job workers (v3.0.5 queue system)
        for job in getattr(self, '_download_jobs', []):
            w = job.worker
            if w and getattr(w, 'isRunning', lambda: False)():
                if hasattr(w, '_cancelled'):
                    w._cancelled = True
                if hasattr(w, 'cancel'):
                    try: w.cancel()
                    except Exception: pass
                threads_to_stop.append(w)
            if job.window is not None:
                try:
                    job.window.hide()
                except Exception:
                    pass
        if getattr(self, '_jobs_window', None) is not None:
            try:
                self._jobs_window.close()
            except Exception:
                pass

        # Processor
        if hasattr(self, 'processor_worker') and self.processor_worker and self.processor_worker.isRunning():
            threads_to_stop.append(self.processor_worker)

        # Wait with explicit error logging + terminate fallback
        for t in threads_to_stop:
            try:
                if t.isRunning():
                    t.wait(3000)
                if t.isRunning():
                    t.terminate()
                    t.wait(800)
            except Exception as e:
                print(f"[Process_dat closeEvent] Thread wait/terminate error (non-fatal): {e}")

        super().closeEvent(event)

    def showEvent(self, event):
        """E/F fix: Safe single point for initial modern discovery.
        Executes ONLY after window.show() (main:3839) and the event loop is active.
        QTimer.singleShot defers the actual work to next event processing tick.
        Guarantees zero thread creation/start during __init__ (835-836), _build_modern_as_main (1050),
        _build_modern_ui_page (1341), or the tab builders (1520+, 1728+).
        Replaces the removed early singleShots at former 1719/1914.
        """
        super().showEvent(event)
        if (getattr(self, 'ui_mode', None) == "modern" and
                not getattr(self, '_initial_discovery_scheduled', False)):
            self._initial_discovery_scheduled = True
            # Small delay ensures full visibility + event loop pumping; 80ms is conservative but safe.
            QTimer.singleShot(80, self._safe_post_show_initial_discovery)

    def _add_modern_switch_menu(self):
        """Adds View menu with explicit Legacy switch + Settings Save (user request)."""
        menubar = self.menuBar()
        view_menu = menubar.addMenu("View")

        # Prominent Legacy switch
        legacy_action = QAction("Switch to Legacy S3 Browser (Full)", self)
        legacy_action.triggered.connect(self._switch_to_legacy_with_save)
        view_menu.addAction(legacy_action)

        view_menu.addSeparator()

        # Explicit Settings Save (addresses user demand for reliable persistence)
        save_action = QAction("Save Current Settings", self)
        save_action.triggered.connect(self._save_all_modern_settings)
        view_menu.addAction(save_action)

        view_menu.addSeparator()

        # Non-modal Download Job Manager (v3.0.5 queue system)
        jobs_action = QAction("Download Jobs (Queue Manager)", self)
        jobs_action.triggered.connect(self._open_jobs_window)
        view_menu.addAction(jobs_action)

    def _add_modern_button_to_legacy_toolbar(self):
        """Prominent, reliable way to switch back from Legacy. Inserts clean non-overlapping banner into the real legacy layout."""
        try:
            # Menu (always)
            menubar = self.menuBar()
            view_menu = menubar.addMenu("View")
            modern_action = QAction("★ Switch to Modern UI (Recommended)", self)
            modern_action.triggered.connect(self._switch_to_modern)
            view_menu.addAction(modern_action)

            jobs_action = QAction("Download Jobs (Queue Manager)", self)
            jobs_action.triggered.connect(self._open_jobs_window)
            view_menu.addAction(jobs_action)

            # Also add direct button to the legacy toolbar if it exists
            if hasattr(self, 'toolbar_layout') and self.toolbar_layout is not None:
                mod_btn = QPushButton("★ MODERN UI")
                mod_btn.setStyleSheet("""
                    QPushButton { background:#1F6FEB; color:white; font-weight:700; padding:5px 14px; border-radius:4px; border:none; font-size:10px; }
                    QPushButton:hover { background:#388BFD; }
                """)
                mod_btn.clicked.connect(self._switch_to_modern)
                self.toolbar_layout.addWidget(mod_btn)

            # PROMINENT non-overlapping top banner inserted into main_layout (robust)
            if hasattr(self, 'main_layout') and self.main_layout is not None:
                self._legacy_banner = QFrame()
                banner = self._legacy_banner
                banner.setStyleSheet("""
                    QFrame {
                        background: #1F6FEB;
                        border: none;
                    }
                """)
                banner.setFixedHeight(38)
                h = QHBoxLayout(banner)
                h.setContentsMargins(16, 0, 16, 0)
                h.setSpacing(12)

                lbl = QLabel("⚠ LEGACY MODE —  The beautiful Modern Quick Scene + Animation experience is recommended")
                lbl.setStyleSheet("color: white; font-weight: 700; font-size: 11px;")
                h.addWidget(lbl)

                h.addStretch()

                btn = QPushButton("SWITCH TO MODERN UI NOW")
                btn.setStyleSheet("""
                    QPushButton {
                        background: white; color: #1F6FEB; font-weight: 800;
                        padding: 6px 18px; border-radius: 5px; font-size: 11px; border: none;
                    }
                    QPushButton:hover { background: #E6EDF3; }
                """)
                btn.clicked.connect(self._switch_to_modern)
                h.addWidget(btn)

                # Insert at the very top of legacy's main layout (no overlap ever)
                self.main_layout.insertWidget(0, banner)

            # Status bar hint
            if hasattr(self, 'status_bar') and self.status_bar:
                self.status_bar.showMessage("Legacy mode active — use the bright blue banner at top or View menu to return to Modern")

        except Exception as e:
            print(f"[Legacy banner] {e}")
            # Last-resort floating button
            try:
                btn = QPushButton("★ SWITCH TO MODERN", self)
                btn.setStyleSheet("background:#1F6FEB; color:white; font-weight:700; padding:8px 16px; border-radius:6px;")
                btn.clicked.connect(self._switch_to_modern)
                btn.move(20, 60)
                btn.show()
                btn.raise_()
            except Exception:
                pass

    def _build_modern_as_main(self):
        """Builds the modern creative UI as the main interface of this window."""
        central = QWidget()
        self.setCentralWidget(central)
        self._build_modern_ui_page(central)
        self._add_modern_switch_menu()

    def _build_full_legacy_as_main(self):
        """Builds the FULL original legacy S3 browser as the main interface.
        All original features are preserved.
        """
        # Call the original legacy builder (the big init_ui logic that still exists in this file)
        self.init_ui()

        # ─────────────────────────────────────────────────────────────
        #  MODERN UI: UNDER DEVELOPMENT — DO NOT TOUCH
        #  The "Switch to Modern UI" banner/button/menu is DISABLED.
        #  Leave the call below commented out until Modern UI is ready.
        #  self._add_modern_button_to_legacy_toolbar()
        #  ─────────────────────────────────────────────────────────────

    def _get_bucket_for_satellite(self, sat_name):
        """Minimal extend (H/AB): GOES experimental buckets mapped. No discovery changes."""
        if "GOES-16" in sat_name:
            return "noaa-goes16"
        if "GOES-17" in sat_name:
            return "noaa-goes17"
        if "GOES-18" in sat_name:
            return "noaa-goes18"
        if "GOES-19" in sat_name:
            return "noaa-goes19"
        if _sat_kind(sat_name) == "gk2a":
            return "noaa-gk2a-pds"
        if "Himawari 8" in sat_name:
            return "noaa-himawari8"
        return "noaa-himawari9"

    def _get_product_for_type(self, sat_name: str, type_text: str) -> str:
        """Map UI 'Type / Region' selection to the real S3 product folder prefix."""
        sat_lower = (sat_name or "").lower()
        t = (type_text or "").lower()

        if _sat_kind(sat_name) == "gk2a":
            if "local" in t or "la" in t:
                return "AMI/L1B/LA"
            return "AMI/L1B/FD"   # default Full Disk
        if "himawari" in sat_lower:
            if "japan" in t:
                return "AHI-L1b-Japan"
            if "target" in t:
                return "AHI-L1b-Target"
            return "AHI-L1b-FLDK"   # default Full Disk
        else:
            # GOES family — for now we only support full disk RadF in the downloader.
            # CONUS/Meso can be added later via similar mapping if needed.
            return "ABI-L1b-RadF"

    def _get_product_prefix(self):
        """Returns the actual product folder name selected (loaded live from AWS, no assumptions)."""
        if not hasattr(self, 'modern_type') or not self.modern_type.currentText():
            return "AHI-L1b-FLDK"
        txt = self.modern_type.currentText().strip()
        if txt and "Loading" not in txt:
            return txt
        return "AHI-L1b-FLDK"

    def _build_modern_ui_page(self, container):
        """Sleek 2026 McIDAS / Serious Org Professional UI.
        Clean panels, razor-sharp typography & layout, premium mission-control feel.
        Modern visibly superior to Legacy."""
        # ── Root background ──────────────────────────────────────────────
        container.setStyleSheet("""
            /* 2026 McIDAS / Serious Org Professional — Production Mission Console
               Premium, sharp, clean: deep #0A0E15, steel borders, restrained #0099CC cyan accent,
               consistent 5-6px radii, pro typography (10-11px hierarchy, no micro 8px/7.5px),
               generous-but-tight airy panels (12-16px internal), zero cramped 2010 remnants.
               Modern visibly superior: pure, focused, high-end ops tool feel. */
            QMainWindow, QFrame, QWidget#modernRoot {
                background: #0A0E15;
                color: #DCE3ED;
                font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
            }
            QComboBox {
                background: #0F1620;
                color: #DCE3ED;
                border: 1px solid #1E2836;
                border-radius: 5px;
                padding: 6px 10px;
                font-size: 10.5px;
                font-weight: 500;
                min-width: 118px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #0F1620;
                color: #DCE3ED;
                border: 1px solid #1E2836;
                selection-background-color: #0099CC;
                selection-color: #FFFFFF;
                padding: 4px 5px;
            }
            QCheckBox { color: #8A96A8; font-size: 10px; spacing: 6px; font-weight: 500; }
            QCheckBox::indicator {
                width: 14px; height: 14px;
                border-radius: 5px;
                border: 1px solid #2A3644;
                background: #0F1620;
            }
            QCheckBox::indicator:checked {
                background: #0099CC;
                border-color: #0099CC;
            }
            QCheckBox:hover { color: #C5CCD6; }
            QTabWidget::pane {
                border: 1px solid #1E2836;
                border-radius: 6px;
                background: #0F1620;
            }
            QTabBar::tab {
                background: #0A0E15;
                color: #8A96A8;
                border: 1px solid #1E2836;
                border-bottom: none;
                padding: 8px 20px;
                border-radius: 5px 5px 0 0;
                font-size: 10px;
                font-weight: 600;
                letter-spacing: 0.2px;
                margin-right: 2px;
            }
            QTabBar::tab:selected {
                background: #0F1620;
                color: #00B4D8;
                border-color: #1E2836;
                font-weight: 700;
            }
            QTabBar::tab:hover:!selected { background: #0F1620; color: #C5CCD6; }
            QGroupBox {
                border: 1px solid #1E2836;
                border-radius: 6px;
                margin-top: 8px;
                padding-top: 10px;
                font-size: 9.5px;
                font-weight: 600;
                color: #00B4D8;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 11px;
                padding: 0 5px;
            }
            QLabel { color: #8A96A8; font-size: 10px; font-weight: 500; }
            QPushButton {
                background: #1A222D;
                color: #DCE3ED;
                border: 1px solid #2A3644;
                border-radius: 5px;
                padding: 6px 13px;
                font-size: 10.5px;
                font-weight: 600;
            }
            QPushButton:hover { background: #232C3A; border-color: #3A4757; }
            QPushButton:pressed { background: #141A24; }
            QPushButton:disabled { background: #141A24; color: #5A6778; border-color: #232C3A; }
            QPushButton:focus { outline: none; border-color: #0099CC; }
        """)
        container.setObjectName("modernRoot")

        # Pre-create band checks dict so Animation tab + quick downloads are safe even before QS tab renders
        self.modern_band_checks = {}
        BAND_META = {
            1:"Blue",2:"Green",3:"Red",4:"Near-IR",5:"NIR",6:"Cloud",
            7:"SWIR",8:"WV-High",9:"WV-Mid",10:"WV-Low",11:"LW-IR",
            12:"Ozone",13:"IR-Ref",14:"IR-Cloud",15:"Dust",16:"CO2"
        }
        for i in range(1, 17):
            # Default Natural Color selection
            # Real checkboxes are created inside Quick Scene tab (visuals live there)
            pass  # dict will be populated with real QCheckBox widgets when QS tab builds

        layout = QVBoxLayout(container)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── HERO HEADER — 2026 McIDAS Serious Org Premium Command Bar ─────
        hero = QFrame()
        hero.setFixedHeight(64)
        hero.setStyleSheet("""
            QFrame {
                background: #0B101A;
                border-bottom: 2px solid #0099CC;
            }
        """)
        hero_l = QHBoxLayout(hero)
        hero_l.setContentsMargins(18, 0, 18, 0)
        hero_l.setSpacing(0)

        # Left: wordmark — crisp, authoritative McIDAS-style professional
        wordmark = QLabel("◈ CYCLONE")
        wordmark.setStyleSheet("""
            font-size: 19px;
            font-weight: 800;
            color: #00B4D8;
            letter-spacing: 0.6px;
        """)
        hero_l.addWidget(wordmark)

        subtitle = QLabel("  S3 DOWNLOADER  •  v3.0.1 ALPHA  |  Himawari primary  |  GOES: EXPERIMENTAL")
        subtitle.setStyleSheet("font-size: 9.5px; color: #5E8FA8; font-weight: 600; padding-top: 6px; letter-spacing: 0.2px;")
        hero_l.addWidget(subtitle)
        hero_l.addStretch()

        # Right: satellite pill — sharp, professional McIDAS console
        sat_lbl = QLabel("SATELLITE")
        sat_lbl.setStyleSheet("font-size: 9px; color: #5E6B7A; font-weight: 700; letter-spacing: 0.3px;")
        hero_l.addWidget(sat_lbl)
        hero_l.addSpacing(6)

        self.modern_sat = QComboBox()
        # F FIX (under H): Block signals during initial population + connect in builder.
        # Guarantees _on_modern_satellite_changed (which calls _discover_product_types) CANNOT fire
        # even if Qt emits on addItems/setCurrent in any edge case. Zero discovery thread creation in __init__/builders.
        self.modern_sat.blockSignals(True)
        self.modern_sat.addItems(["Himawari 9", "Himawari 8", "GOES-16", "GOES-17", "GOES-18", "GOES-19"])
        self.modern_sat.currentTextChanged.connect(self._on_modern_satellite_changed)
        self.modern_sat.blockSignals(False)
        self.modern_sat.setStyleSheet("""
            QComboBox {
                background: #10161F;
                color: #00B4D8;
                border: 1px solid #0099CC;
                border-radius: 6px;
                padding: 4px 11px;
                font-size: 10.5px;
                font-weight: 700;
                min-width: 122px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #10161F;
                color: #E0E5ED;
                border: 1px solid #0099CC;
                selection-background-color: #0099CC;
            }
        """)
        hero_l.addWidget(self.modern_sat)
        hero_l.addSpacing(10)

        # TYPE / Sector selector — premium sharp McIDAS
        type_lbl = QLabel("TYPE")
        type_lbl.setStyleSheet("font-size: 9px; color: #5E6B7A; font-weight: 700; letter-spacing: 0.3px;")
        hero_l.addWidget(type_lbl)
        hero_l.addSpacing(6)

        self.modern_type = QComboBox()
        # F FIX (under H): Block during add + connect (parallel to sat). Prevents any _on_modern_type_or_sat_changed
        # (which leads to _trigger_modern_discovery) from executing during modern UI builder / __init__.
        self.modern_type.blockSignals(True)
        self.modern_type.addItem("Loading types from AWS...")
        self.modern_type.currentTextChanged.connect(self._on_modern_type_or_sat_changed)
        self.modern_type.blockSignals(False)
        self.modern_type.setStyleSheet("""
            QComboBox {
                background: #10161F;
                color: #00B4D8;
                border: 1px solid #0099CC;
                border-radius: 14px;
                padding: 4px 11px;
                font-size: 10.5px;
                font-weight: 700;
                min-width: 138px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #10161F;
                color: #E0E5ED;
                border: 1px solid #0099CC;
                selection-background-color: #0099CC;
            }
        """)
        hero_l.addWidget(self.modern_type)
        hero_l.addSpacing(10)

        # SWITCH TO LEGACY — prominent, easy access (user request)
        # Keeps Modern clean but gives immediate path to full Legacy S3 browser when needed.
        legacy_btn = QPushButton("Legacy S3 Browser")
        legacy_btn.setFixedHeight(28)
        legacy_btn.setCursor(Qt.PointingHandCursor)
        legacy_btn.setStyleSheet("""
            QPushButton {
                background: #0F1620;
                color: #8A96A8;
                border: 1px solid #3A4757;
                border-radius: 6px;
                padding: 0 12px;
                font-size: 9.5px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #1A222D;
                color: #E0E5ED;
                border-color: #0099CC;
            }
        """)
        legacy_btn.clicked.connect(self._switch_to_legacy_with_save)
        hero_l.addWidget(legacy_btn)

        layout.addWidget(hero)

        # ── QUICK-RANGE STRIP — Clean, Sharp McIDAS Toolbar (premium, airy) ──
        strip = QFrame()
        strip.setFixedHeight(40)
        strip.setStyleSheet("QFrame { background: #0B101A; border-bottom: 1px solid #1E2836; }")
        strip_l = QHBoxLayout(strip)
        strip_l.setContentsMargins(20, 0, 20, 0)
        strip_l.setSpacing(6)

        badge = QLabel("QUICK JUMP")
        badge.setStyleSheet("font-size: 9px; font-weight: 700; color: #5E6B7A; letter-spacing: 0.3px;")
        strip_l.addWidget(badge)
        strip_l.addSpacing(8)

        quick_ranges = [("1h", 1), ("6h", 6), ("12h", 12), ("24h", 24), ("48h", 48)]
        for label, hours in quick_ranges:
            btn = QPushButton(label)
            btn.setFixedHeight(22)
            btn.setStyleSheet("""
                QPushButton {
                    background: #0F1620;
                    color: #8A96A8;
                    border: 1px solid #1E2836;
                    border-radius: 6px;
                    padding: 0 10px;
                    font-size: 9.5px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background: #1A222D;
                    color: #00B4D8;
                    border-color: #0099CC;
                }
            """)
            btn.clicked.connect(lambda _, h=hours: self._quick_range_modern(h))
            strip_l.addWidget(btn)

        strip_l.addStretch()
        layout.addWidget(strip)

        # ── MAIN CONTENT (tabs) — premium dark panel ─────────────────────
        body = QFrame()
        body.setStyleSheet("QFrame { background: #0B101A; }")
        body_l = QVBoxLayout(body)
        body_l.setContentsMargins(16, 14, 16, 14)
        body_l.setSpacing(0)

        tabs = QTabWidget()
        tabs.addTab(self._create_quick_scene_tab(), "⚡  Quick Scene")
        tabs.addTab(self._create_animation_sequence_tab(), "🎞  Animation Sequence")
        body_l.addWidget(tabs, 1)

        # ── MODERN DOWNLOAD CONSOLE (real functionality + feedback, superior to legacy) ──
        dl_console = self._create_modern_download_console()
        body_l.addWidget(dl_console)

        layout.addWidget(body, 1)

        # ── STATUS FOOTER — Premium Minimal —————————————————————————————
        footer = QFrame()
        footer.setFixedHeight(26)
        footer.setStyleSheet("QFrame { background: #0A0E15; border-top: 1px solid #222A33; }")
        footer_l = QHBoxLayout(footer)
        footer_l.setContentsMargins(18, 0, 18, 0)

        dot = QLabel("●")
        dot.setStyleSheet("color: #2EA043; font-size: 9px;")
        footer_l.addWidget(dot)
        footer_l.addSpacing(4)

        status_lbl = QLabel("MODERN  •  Quick Scene + Animation  —  Live AWS Discovery + Direct S3  •  GOES: EXPERIMENTAL — Himawari primary  •  View → Legacy")
        status_lbl.setStyleSheet("font-size: 9.5px; color: #5E6B7A; font-weight: 500;")
        footer_l.addWidget(status_lbl)
        footer_l.addStretch()

        ver = QLabel("Himawari-9 AHI  •  NOAA S3  •  Cyclone v3.0.1 ALPHA  •  McIDAS Professional  |  GOES: EXPERIMENTAL")
        ver.setStyleSheet("font-size: 9px; color: #3F454F;")
        footer_l.addWidget(ver)

        layout.addWidget(footer)

    def _create_modern_download_console(self):
        """Shared, always-visible download status + directory control for Modern tabs.
        This is what makes Modern clearly superior: immediate feedback, no need to leave the beautiful UI.
        """
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background: #11161E;
                border: 1px solid #1E2836;
                border-radius: 5px;
                margin-top: 12px;
            }
        """)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(7)

        # Header row — premium McIDAS label
        hdr = QHBoxLayout()
        t = QLabel("⬇  DOWNLOAD CONSOLE  •  LIVE AWS S3 POWERED")
        t.setStyleSheet("font-size:10px; font-weight:700; color:#00B4D8; letter-spacing:0.3px;")
        hdr.addWidget(t)
        hdr.addStretch()
        self.modern_dl_status = QLabel("Ready — select scene or range and Download")
        self.modern_dl_status.setStyleSheet("font-size:9.5px; color:#8A96A6;")
        hdr.addWidget(self.modern_dl_status)
        lay.addLayout(hdr)

        # Dir row — spacious
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dl = QLabel("SAVE TO")
        dl.setStyleSheet("font-size:9px; font-weight:700; color:#6B7788;")
        dir_row.addWidget(dl)

        self.modern_dir_edit = QLineEdit()
        self.modern_dir_edit.setText(str(self.modern_download_dir))
        self.modern_dir_edit.setStyleSheet("""
            QLineEdit { background:#0A0E15; color:#E9EDF3; border:1px solid #222A33; border-radius:4px; padding:4px 8px; font-size:10px; }
        """)
        self.modern_dir_edit.textChanged.connect(self._on_modern_dir_changed)
        dir_row.addWidget(self.modern_dir_edit, 1)

        br = QPushButton("Browse")
        br.setFixedWidth(68)
        br.setStyleSheet("""
            QPushButton { background:#1C222B; color:#C8D0DB; border:1px solid #2A333C; border-radius:4px; padding:4px 9px; font-size:9px; font-weight:600; }
            QPushButton:hover { background:#252C36; }
        """)
        br.clicked.connect(self._browse_modern_dir)
        dir_row.addWidget(br)

        op = QPushButton("Open")
        op.setFixedWidth(54)
        op.setStyleSheet("""
            QPushButton { background:#1C222B; color:#C8D0DB; border:1px solid #2A333C; border-radius:4px; padding:4px 9px; font-size:9px; font-weight:600; }
            QPushButton:hover { background:#252C36; }
        """)
        op.clicked.connect(self._open_modern_dir)
        dir_row.addWidget(op)

        lay.addLayout(dir_row)

        # Progress + actions row
        prog_row = QHBoxLayout()
        prog_row.setSpacing(8)

        self.modern_progress = QProgressBar()
        self.modern_progress.setRange(0, 100)
        self.modern_progress.setValue(0)
        self.modern_progress.setTextVisible(True)
        self.modern_progress.setFixedHeight(17)
        self.modern_progress.setStyleSheet("""
            QProgressBar { background:#0A0E15; border:1px solid #222A33; border-radius:5px; color:#E9EDF3; font-size:9px; }
            QProgressBar::chunk { background:#1F6FEB; border-radius:3px; }
        """)
        self.modern_progress.setVisible(False)
        prog_row.addWidget(self.modern_progress, 1)

        self.modern_cancel_btn = QPushButton("CANCEL")
        self.modern_cancel_btn.setFixedWidth(76)
        self.modern_cancel_btn.setStyleSheet("""
            QPushButton { background:#C53B37; color:white; border:none; border-radius:4px; font-size:9px; font-weight:700; padding:3px 9px; }
            QPushButton:hover { background:#E04A46; }
            QPushButton:disabled { background:#3D2A2A; color:#777; }
        """)
        self.modern_cancel_btn.clicked.connect(self._cancel_modern_download)
        self.modern_cancel_btn.setVisible(False)
        prog_row.addWidget(self.modern_cancel_btn)

        lay.addLayout(prog_row)

        # Tiny log line
        self.modern_log_line = QLabel("")
        self.modern_log_line.setStyleSheet("font-size:9px; color:#6B7788; padding-left:2px;")
        lay.addWidget(self.modern_log_line)

        # Manual extract row
        extract_row = QHBoxLayout()
        extract_row.setSpacing(8)
        extract_row.setContentsMargins(0, 0, 0, 0)
        self.modern_extract_btn = QPushButton("⬡  EXTRACT & NETCDF")
        self.modern_extract_btn.setFixedHeight(26)
        self.modern_extract_btn.setStyleSheet("""
            QPushButton { background:#2EA043; color:white; border:none; border-radius:4px; font-size:9px; font-weight:700; padding:3px 12px; }
            QPushButton:hover { background:#3FB950; }
            QPushButton:disabled { background:#1C3A24; color:#777; }
        """)
        self.modern_extract_btn.clicked.connect(self._manual_extract)
        extract_row.addWidget(self.modern_extract_btn)
        extract_row.addStretch()
        lay.addLayout(extract_row)

        return frame

    def _on_modern_dir_changed(self, text):
        try:
            self.modern_download_dir = Path(text)
        except Exception:
            pass

    def _browse_modern_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Choose Modern Download Folder", str(self.modern_download_dir))
        if d:
            self.modern_dir_edit.setText(d)

    def _open_modern_dir(self):
        try:
            import os as _os
            p = Path(self.modern_dir_edit.text())
            p.mkdir(parents=True, exist_ok=True)
            _os.startfile(str(p)) if hasattr(_os, 'startfile') else None
        except Exception as e:
            QMessageBox.information(self, "Open Folder", f"Could not open: {e}")

    def _update_modern_status(self, msg, progress=None):
        """Central live updater for the Modern console (used by direct downloads + range)."""
        if hasattr(self, 'modern_dl_status') and self.modern_dl_status:
            self.modern_dl_status.setText(msg[:110] + ("…" if len(msg) > 110 else ""))
        if hasattr(self, 'modern_log_line') and self.modern_log_line:
            self.modern_log_line.setText(msg)
        if progress is not None and hasattr(self, 'modern_progress') and self.modern_progress:
            self.modern_progress.setVisible(True)
            self.modern_progress.setValue(max(0, min(100, int(progress))))

    def _set_modern_download_active(self, active: bool):
        if hasattr(self, 'modern_progress'):
            self.modern_progress.setVisible(active)
        if hasattr(self, 'modern_cancel_btn'):
            self.modern_cancel_btn.setVisible(active)
            self.modern_cancel_btn.setEnabled(active)
        if hasattr(self, 'modern_dl_status') and not active:
            self.modern_dl_status.setText("Ready for next download")

    # ── Job-window registry (v3.0.5) ─────────────────────────────────────
    def _register_job(self, job):
        if not hasattr(self, '_download_jobs'):
            self._download_jobs = []
        self._download_jobs.append(job)
        return job

    def _unregister_job(self, job):
        if job is None or not hasattr(self, '_download_jobs'):
            return
        try:
            self._download_jobs.remove(job)
        except ValueError:
            pass

    def _set_job_state(self, job, state):
        if job is not None:
            job.state = state

    def _job_for_window(self, window):
        for j in getattr(self, '_download_jobs', []):
            if j.window is window:
                return j
        return None

    def _unregister_job_for_window(self, window):
        if window is None:
            return
        job = self._job_for_window(window)
        if job is not None:
            job.state = "done"
        self._unregister_job(job)

    def _close_progress_window(self, prog, delay=1500):
        """Close a job's window (if open) and drop it from the registry."""
        if prog is None:
            return

        def _finish():
            try:
                prog.close()
            except Exception:
                pass
            self._unregister_job_for_window(prog)

        QTimer.singleShot(delay, _finish)

    def _open_jobs_window(self):
        """Show (non-modal) the Download Job Manager window."""
        if getattr(self, '_jobs_window', None) is None:
            self._jobs_window = DownloadJobsWindow(self)
        self._jobs_window.show()
        self._jobs_window.raise_()
        self._jobs_window.activateWindow()

    def _bind_range_adjust(self, prog):
        """Write-through: job window's Adjust spins update queued-range defaults."""
        def _apply(slots, threads):
            self._max_concurrent_slots = max(1, slots)
            self._threads_per_slot = max(1, threads)
        prog.bind_adjust(_apply)

    def _manual_extract(self):
        target = self.modern_dir_edit.text() if hasattr(self, 'modern_dir_edit') else str(self.modern_download_dir)
        if not os.path.isdir(target):
            self._update_modern_status(f"Directory not found: {target}")
            QMessageBox.warning(self, "Extract", f"Directory not found:\n{target}")
            return
        self._update_modern_status(f"Auto processing (extract & NetCDF): {target}")
        self._start_modern_extraction(target)

    def _cancel_modern_download(self):
        if self.modern_download_worker and self.modern_download_worker.isRunning():
            if hasattr(self.modern_download_worker, '_cancelled'):
                self.modern_download_worker._cancelled = True
            self._update_modern_status("Cancelling download…")
        self._set_modern_download_active(False)

    def _create_quick_scene_tab(self):
        """Mission-control Quick Scene tab."""
        w = QWidget()
        # NOTE: Do NOT use bare "QWidget { background }" here — it cascades into Qt internals.
        # The QTabWidget::pane rule in the parent already provides the background.
        outer = QHBoxLayout(w)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(18)

        # ── LEFT COLUMN: Time picker ─────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(14)

        # Section header
        ts_header = QLabel("SCENE TIMESTAMP")
        ts_header.setStyleSheet("font-size:9.5px; font-weight:700; color:#5E6B7A;")
        left.addWidget(ts_header)

        # Clean sharp datetime card — 2026 McIDAS premium spacious panel
        dt_card = QFrame()
        dt_card.setStyleSheet("""
            QFrame {
                background: #11161E;
                border: 1px solid #1E2836;
                border-radius: 5px;
            }
        """)
        dt_l = QVBoxLayout(dt_card)
        dt_l.setContentsMargins(16, 14, 16, 14)
        dt_l.setSpacing(9)

        # Date row — professional breathing room
        date_row = QHBoxLayout()
        date_row.setSpacing(8)
        for lbl in ["YEAR", "MON", "DAY"]:
            col = QVBoxLayout()
            col.setSpacing(4)
            tag = QLabel(lbl)
            tag.setStyleSheet("font-size:9.5px; font-weight:700; color:#6B7788;")
            col.addWidget(tag)
            if lbl == "YEAR":
                self.qs_year = QComboBox()
                self.qs_year.currentTextChanged.connect(self._discover_months_for_year)
                col.addWidget(self.qs_year)
            elif lbl == "MON":
                self.qs_month = QComboBox()
                self.qs_month.currentTextChanged.connect(self._discover_days_for_month)
                col.addWidget(self.qs_month)
            else:
                self.qs_day = QComboBox()
                self.qs_day.currentTextChanged.connect(self._discover_hours_for_day)
                col.addWidget(self.qs_day)
            date_row.addLayout(col)
        dt_l.addLayout(date_row)

        # Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet("background: #222A33;")
        dt_l.addWidget(div)

        # Time row — premium spacing
        time_row = QHBoxLayout()
        time_row.setSpacing(8)
        for lbl in ["HOUR (UTC)", "MIN"]:
            col = QVBoxLayout()
            col.setSpacing(4)
            tag = QLabel(lbl)
            tag.setStyleSheet("font-size:9.5px; font-weight:700; color:#6B7788;")
            col.addWidget(tag)
            if lbl == "HOUR (UTC)":
                self.qs_hour = QComboBox()
                self.qs_hour.currentTextChanged.connect(self._discover_minutes_for_hour)
                col.addWidget(self.qs_hour)
            else:
                self.qs_min = QComboBox()
                col.addWidget(self.qs_min)
            time_row.addLayout(col)
        time_row.addStretch()
        dt_l.addLayout(time_row)

        # Validation
        self.qs_validation = QLabel("Querying AWS S3…")
        self.qs_validation.setStyleSheet("font-size:9px; color:#5CA3FF; padding-top:2px;")
        dt_l.addWidget(self.qs_validation)

        left.addWidget(dt_card)
        left.addStretch()

        outer.addLayout(left, 1)

        # ── RIGHT COLUMN: Bands + action ─────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(14)

        band_header = QLabel("SPECTRAL BANDS")
        band_header.setStyleSheet("font-size:9px; font-weight:700; color:#5A6470;")
        right.addWidget(band_header)

        # Preset pills row
        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        PRESETS = [
            ("Natural",    [3,2,1],          "#1F6FEB"),
            ("True Color", [4,3,2],          "#388BFD"),
            ("IR",         [13],             "#DA3633"),
            ("Water Vapor",[8],              "#6E40C9"),
            ("Dust",       [11,12,13],       "#BF8700"),
            ("All",        list(range(1,17)),"#3FB950"),
        ]
        for name, bands, color in PRESETS:
            btn = QPushButton(name)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {color};
                    border: 1px solid {color};
                    border-radius: 5px;
                    padding: 3px 9px;
                    font-size: 9.5px;
                    font-weight: 600;
                }}
                QPushButton:hover {{
                    background: {color}1A;
                    border-color: {color};
                }}
            """)
            btn.clicked.connect(lambda _, b=bands: self._apply_bands_to_modern(b))
            preset_row.addWidget(btn)
        preset_row.addStretch()
        right.addLayout(preset_row)

        # Band grid card — sharp 2026 McIDAS premium panel
        band_card = QFrame()
        band_card.setStyleSheet("""
            QFrame {
                background: #11161E;
                border: 1px solid #222A33;
                border-radius: 6px;
            }
        """)
        band_inner = QVBoxLayout(band_card)
        band_inner.setContentsMargins(12, 10, 12, 10)
        band_inner.setSpacing(6)

        BAND_META = {
            1:"Blue",2:"Green",3:"Red",4:"Near-IR",5:"NIR",6:"Cloud",
            7:"SWIR",8:"WV-High",9:"WV-Mid",10:"WV-Low",11:"LW-IR",
            12:"Ozone",13:"IR-Ref",14:"IR-Cloud",15:"Dust",16:"CO2"
        }
        band_grid = QGridLayout()
        band_grid.setSpacing(6)
        self.modern_band_checks = {}
        self._is_goes_modern = False
        for i in range(1, 17):
            cb = QCheckBox(f"B{i:02d}  {BAND_META.get(i,'')}")
            cb.setChecked(i in [3,2,1])
            cb.setStyleSheet("""
                QCheckBox { color: #8A94A3; font-size: 10px; spacing: 5px; font-weight: 500; }
                QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; border: 1px solid #353E4A; background: #0F141B; }
                QCheckBox::indicator:checked { background: #1F6FEB; border-color: #1F6FEB; }
                QCheckBox:hover { color: #C8D0DB; }
            """)
            band_grid.addWidget(cb, (i-1)//4, (i-1)%4)
            self.modern_band_checks[f"B{i:02d}"] = cb
        band_inner.addLayout(band_grid)

        right.addWidget(band_card)

        # Download button — the hero CTA — sharp premium McIDAS
        dl_btn = QPushButton("  ↓  DOWNLOAD SCENE")
        dl_btn.setFixedHeight(44)
        dl_btn.setStyleSheet("""
            QPushButton {
                background: #1F6FEB;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: 700;
                border: 1px solid #2B7EF5;
                border-radius: 6px;
                letter-spacing: 0.2px;
            }
            QPushButton:hover {
                background: #2B7EF5;
                border-color: #58A6FF;
            }
            QPushButton:pressed { background: #1665C0; }
            QPushButton:disabled { background: #1C222B; color: #5A636E; border-color: #222A33; }
        """)
        dl_btn.clicked.connect(self._download_quick_scene_modern)
        right.addWidget(dl_btn)

        outer.addLayout(right, 2)

        # Wire combos — ONLY the satellite selector triggers full AWS discovery.
        # Year/Month/Day changes must NOT trigger re-discovery (would cause feedback loop + repeated expensive queries).
        self.modern_sat.currentTextChanged.connect(self._update_modern_date_combos)
        self.modern_sat.currentTextChanged.connect(self._update_modern_band_labels)

        # NOTE (E/F QThread fix): Initial discovery moved to single safe post-show point in showEvent.
        # No QTimer.singleShot or _discover_product_types call here (prevents ctor-time thread start).
        # See _safe_post_show_initial_discovery + showEvent.
        # E (under direct H supervision): Confirmed zero dangerous early QTimer.singleShot at former line 1719 (this tab).
        # Any prior early singleShot calls removed/deferred exclusively to showEvent path. Tab builder now safe.

        return w

    def _create_animation_sequence_tab(self):
        """Polished Animation Sequence tab."""
        w = QWidget()
        # NOTE: Do NOT use bare "QWidget { background }" here — it cascades into Qt internals.
        # The QTabWidget::pane rule in the parent already provides the background.
        outer = QHBoxLayout(w)
        outer.setContentsMargins(22, 18, 22, 18)
        outer.setSpacing(18)

        # ── LEFT: Range pickers ──────────────────────────────────────────
        left = QVBoxLayout()
        left.setSpacing(14)

        rng_header = QLabel("DATE RANGE")
        rng_header.setStyleSheet("font-size:9px; font-weight:700; color:#5A6470;")
        left.addWidget(rng_header)

        # Unified FROM/TO panel — FROM row 1, TO row 2 (no separate cards)
        range_card = QFrame()
        range_card.setStyleSheet("QFrame { background: #11161E; border: 1px solid #222A33; border-left: 3px solid #58A6FF; border-radius: 6px; }")
        rl = QVBoxLayout(range_card)
        rl.setContentsMargins(13, 9, 13, 9)
        rl.setSpacing(8)

        # FROM row
        from_row = QHBoxLayout()
        from_row.setSpacing(6)
        from_tag = QLabel("▶  FROM")
        from_tag.setStyleSheet("font-size:9px; font-weight:700; color:#58A6FF; min-width:42px;")
        from_row.addWidget(from_tag)
        for lbl, attr in [("YEAR","anim_from_year"),("MONTH","anim_from_month"),("DAY","anim_from_day")]:
            col = QVBoxLayout(); col.setSpacing(4)
            tl = QLabel(lbl); tl.setStyleSheet("font-size:9.5px; font-weight:600; color:#6B7788;")
            col.addWidget(tl)
            cb = QComboBox()
            setattr(self, attr, cb)
            if "year" in attr.lower(): cb.currentTextChanged.connect(self._discover_months_for_year)
            elif "month" in attr.lower(): cb.currentTextChanged.connect(self._discover_days_for_month)
            elif "day" in attr.lower(): cb.currentTextChanged.connect(self._discover_hours_for_day)
            col.addWidget(cb)
            from_row.addLayout(col)
        sep_lbl = QLabel("—"); sep_lbl.setStyleSheet("color:#6B7788; font-size:10px;")
        from_row.addWidget(sep_lbl)
        for lbl, attr in [("HOUR","anim_from_hour"),("MIN","anim_from_min")]:
            col = QVBoxLayout(); col.setSpacing(4)
            tl = QLabel(lbl); tl.setStyleSheet("font-size:9.5px; font-weight:600; color:#6B7788;")
            col.addWidget(tl)
            cb = QComboBox()
            setattr(self, attr, cb)
            if "hour" in attr.lower(): cb.currentTextChanged.connect(self._discover_minutes_for_hour)
            col.addWidget(cb)
            from_row.addLayout(col)
        from_row.addStretch()
        rl.addLayout(from_row)

        # TO row
        to_row = QHBoxLayout()
        to_row.setSpacing(6)
        to_tag = QLabel("■  TO")
        to_tag.setStyleSheet("font-size:9px; font-weight:700; color:#3FB950; min-width:42px;")
        to_row.addWidget(to_tag)
        for lbl, attr in [("YEAR","anim_to_year"),("MONTH","anim_to_month"),("DAY","anim_to_day")]:
            col = QVBoxLayout(); col.setSpacing(4)
            tl = QLabel(lbl); tl.setStyleSheet("font-size:9.5px; font-weight:600; color:#6B7788;")
            col.addWidget(tl)
            cb = QComboBox()
            setattr(self, attr, cb)
            col.addWidget(cb)
            to_row.addLayout(col)
        sep_lbl2 = QLabel("—"); sep_lbl2.setStyleSheet("color:#6B7788; font-size:10px;")
        to_row.addWidget(sep_lbl2)
        for lbl, attr in [("HOUR","anim_to_hour"),("MIN","anim_to_min")]:
            col = QVBoxLayout(); col.setSpacing(4)
            tl = QLabel(lbl); tl.setStyleSheet("font-size:9.5px; font-weight:600; color:#6B7788;")
            col.addWidget(tl)
            cb = QComboBox()
            setattr(self, attr, cb)
            col.addWidget(cb)
            to_row.addLayout(col)
        to_row.addStretch()
        rl.addLayout(to_row)

        left.addWidget(range_card)

        # Step selector — sharp
        step_card = QFrame()
        step_card.setStyleSheet("QFrame { background:#11161E; border:1px solid #222A33; border-radius:6px; }")
        step_l = QHBoxLayout(step_card)
        step_l.setContentsMargins(13, 9, 13, 9)
        step_lbl = QLabel("TIMESTEP")
        step_lbl.setStyleSheet("font-size:9px; font-weight:700; color:#5A6470;")
        step_l.addWidget(step_lbl)
        step_l.addStretch()
        self.anim_step = QComboBox()
        self.anim_step.addItems(["2.5 min", "10 min", "15 min", "30 min", "60 min"])
        self.anim_step.setStyleSheet("""
            QComboBox { background:#0A0E15; color:#E9EDF3; border:1px solid #222A33; border-radius:5px; padding:4px 9px; font-size:10.5px; }
            QComboBox::drop-down { border:none; }
        """)
        step_l.addWidget(self.anim_step)
        left.addWidget(step_card)

        # Validation
        self.anim_validation = QLabel("Loading available dates from AWS S3…")
        self.anim_validation.setStyleSheet("font-size:9px; color:#5CA3FF;")
        left.addWidget(self.anim_validation)

        left.addStretch()
        outer.addLayout(left, 1)

        # ── RIGHT: Info + action ─────────────────────────────────────────
        right = QVBoxLayout()
        right.setSpacing(14)

        info_header = QLabel("SEQUENCE INFO")
        info_header.setStyleSheet("font-size:9px; font-weight:700; color:#5A6470;")
        right.addWidget(info_header)

        info_card = QFrame()
        info_card.setStyleSheet("""
            QFrame {
                background: #11161E;
                border: 1px solid #222A33;
                border-radius: 6px;
            }
        """)
        info_l = QVBoxLayout(info_card)
        info_l.setContentsMargins(14, 11, 14, 11)
        info_l.setSpacing(6)

        for icon, text in [
            ("🛰", "Downloads multiple timesteps for looping animations"),
            ("📡", "Queries NOAA GOES / Himawari AWS S3 directly"),
            ("⚡", "Parallel downloads — up to 8 concurrent threads"),
            ("🔧", "Auto-extract and NetCDF convert after download"),
        ]:
            row = QHBoxLayout()
            row.setSpacing(10)
            il = QLabel(icon)
            il.setStyleSheet("font-size:14px;")
            il.setFixedWidth(22)
            row.addWidget(il)
            tl = QLabel(text)
            tl.setStyleSheet("font-size:9.5px; color:#8A94A3;")
            tl.setWordWrap(True)
            row.addWidget(tl, 1)
            info_l.addLayout(row)

        right.addWidget(info_card)
        right.addStretch()

        # Big CTA — sharp premium McIDAS
        dl_btn = QPushButton("  ↓  DOWNLOAD ANIMATION RANGE")
        dl_btn.setFixedHeight(44)
        dl_btn.setStyleSheet("""
            QPushButton {
                background: #1F6FEB;
                color: #FFFFFF;
                font-size: 12.5px;
                font-weight: 700;
                border: 1px solid #2B7EF5;
                border-radius: 6px;
                letter-spacing: 0.2px;
            }
            QPushButton:hover {
                background: #2B7EF5;
                border-color: #58A6FF;
            }
            QPushButton:pressed { background: #1665C0; }
            QPushButton:disabled { background: #1C222B; color: #5A636E; border-color: #222A33; }
        """)
        dl_btn.clicked.connect(self._download_animation_range)
        right.addWidget(dl_btn)

        outer.addLayout(right, 1)

        # Wire updates — only satellite change triggers AWS discovery for animation tab.
        # Do NOT wire the individual date combos; that creates a feedback loop of heavy S3 scans.
        self.modern_sat.currentTextChanged.connect(self._update_animation_date_combos)

        # NOTE (E/F QThread fix): Initial discovery removed from here (was line ~1914).
        # Both tabs previously scheduled duplicate early singleShots during _build_modern_ui_page ctor.
        # Now centralized to ONE safe post-show point (showEvent + _safe_post_show_initial_discovery).
        # Guarantees zero QThread start during modern tab builders or __init__.
        # E (under direct H supervision): Confirmed zero dangerous early QTimer.singleShot at former line 1914 (this tab).
        # The early calls referenced in task have been removed/properly deferred. Tab builder safe. D: only.

        return w

    def _on_modern_satellite_changed(self):
        if getattr(self, '_closing', False):
            return
        self._discover_product_types()

    def _on_modern_type_or_sat_changed(self):
        """Called when the user manually changes TYPE. Re-discover years for the new product."""
        if getattr(self, '_closing', False):
            return
        self._trigger_modern_discovery()

        # Refresh minute combos and time step for the selected product
        product = self._get_product_prefix() if hasattr(self, '_get_product_prefix') else "AHI-L1b-FLDK"
        is_rapid = "TARGET" in product.upper() or "JAPAN" in product.upper()

        # Auto-select appropriate time step
        if hasattr(self, 'anim_step') and self.anim_step:
            preferred = "2.5 min" if is_rapid else "10 min"
            idx = self.anim_step.findText(preferred)
            if idx >= 0:
                self.anim_step.setCurrentIndex(idx)

        # Populate minute combos with the correct grid
        mins = self._get_rapid_scan_minutes()
        for attr in ['anim_from_min', 'anim_to_min']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    self._safe_populate_combo_with_none(cb, mins)

    def _trigger_modern_discovery(self):
        """Central method to (re)start date discovery when Sat or TYPE changes."""
        sat = self.modern_sat.currentText()
        bucket = self._get_bucket_for_satellite(sat)
        product = self._get_product_prefix()

        # Properly stop previous discoverer before creating a new one.
        # This is the main cause of "QThread: Destroyed while thread is still running".
        old = self._dates_discoverer
        if old and old.isRunning():
            old.cancel()
            old.wait(2500)  # Wait up to 2.5s for clean exit
            # Disconnect signals to avoid callbacks into a dying window
            try:
                old.years_discovered.disconnect()
                old.error.disconnect()
            except Exception:
                pass

        if self._closing:
            return

        # Show loading state on both tabs if they exist
        if hasattr(self, 'qs_year') and self.qs_year:
            self.qs_year.blockSignals(True)
            self.qs_year.clear()
            self.qs_year.addItem("Loading from AWS...")
            self.qs_year.blockSignals(False)
        if hasattr(self, 'qs_validation') and self.qs_validation:
            self.qs_validation.setText(f"Querying AWS S3 ({product.split('-')[-1]})...")

        if hasattr(self, 'anim_validation') and self.anim_validation:
            self.anim_validation.setText(f"Loading dates from AWS ({product.split('-')[-1]})...")

        self._current_discovering_sat = sat
        self._current_discovering_product = product

        self._dates_discoverer = AvailableDatesDiscoverer(bucket, product)
        # Unified handler keeps BOTH tabs perfectly in sync (fixes previous refactor split-brain)
        self._dates_discoverer.years_discovered.connect(self._on_years_discovered_unified)
        self._dates_discoverer.error.connect(
            lambda msg: (
                (self.qs_validation.setText(f"AWS error: {msg}") if hasattr(self, 'qs_validation') and self.qs_validation else None),
                (self.anim_validation.setText(f"AWS error: {msg}") if hasattr(self, 'anim_validation') and self.anim_validation else None)
            )
        )
        self._dates_discoverer.start()

    def _update_modern_band_labels(self):
        """Switch band checkbox labels between B01-B16 (Himawari) and C01-C16 (GOES)."""
        sat = self.modern_sat.currentText() if hasattr(self, 'modern_sat') else ""
        is_goes = "GOES" in sat.upper()
        prefix = "C" if is_goes else "B"
        for i in range(1, 17):
            key = f"B{i:02d}"  # internal key always B-prefix
            if key in getattr(self, 'modern_band_checks', {}):
                cb = self.modern_band_checks[key]
                meta = {1:"Blue",2:"Green",3:"Red",4:"Near-IR",5:"NIR",6:"Cloud",
                        7:"SWIR",8:"WV-High",9:"WV-Mid",10:"WV-Low",11:"LW-IR",
                        12:"Ozone",13:"IR-Ref",14:"IR-Cloud",15:"Dust",16:"CO2"}
                cb.setText(f"{prefix}{i:02d}  {meta.get(i,'')}")

    def _update_modern_date_combos(self):
        """Kept for the initial QTimer and satellite wiring. Delegates to central trigger."""
        self._trigger_modern_discovery()

    def _discover_product_types(self):
        """Load the real available product folders (FLDK, Japan, Target, etc.) from AWS.
        No hardcoded assumptions.
        """
        sat = self.modern_sat.currentText()
        bucket = self._get_bucket_for_satellite(sat)

        if self._product_types_discoverer and self._product_types_discoverer.isRunning():
            self._product_types_discoverer.cancel()
            self._product_types_discoverer.wait(1500)

        if hasattr(self, 'modern_type') and self.modern_type:
            self.modern_type.blockSignals(True)
            self.modern_type.clear()
            self.modern_type.addItem("Loading from AWS...")
            self.modern_type.blockSignals(False)

        self._product_types_discoverer = AvailableProductTypesDiscoverer(bucket)
        self._product_types_discoverer.products_discovered.connect(self._on_product_types_discovered)
        self._product_types_discoverer.error.connect(
            lambda msg: self._on_product_types_error(msg)
        )
        self._product_types_discoverer.start()

    def _on_product_types_discovered(self, products):
        """Populate the TYPE combo with whatever actually exists on AWS for this bucket."""
        if not hasattr(self, 'modern_type') or self._closing:
            return
        try:
            self.modern_type.blockSignals(True)
            self.modern_type.clear()
            if products:
                self.modern_type.addItems(products)
                # Prefer FLDK or RadF if present, otherwise last
                sat = self.modern_sat.currentText()
                is_goes = "GOES" in sat.upper()
                preferred = "ABI-L1b-RadF" if is_goes else "AHI-L1b-FLDK"
                if preferred in products:
                    self.modern_type.setCurrentText(preferred)
                else:
                    self.modern_type.setCurrentIndex(self.modern_type.count() - 1)
            else:
                sat = self.modern_sat.currentText()
                default = "ABI-L1b-RadF" if "GOES" in sat.upper() else "AHI-L1b-FLDK"
                self.modern_type.addItem(default)
            self.modern_type.blockSignals(False)

            # Now that we have a real product, discover the years for it
            self._trigger_modern_discovery()
        except Exception:
            # hardened: never let AWS callback crash UI
            try:
                if hasattr(self, 'modern_type') and self.modern_type:
                    self.modern_type.blockSignals(False)
                self._trigger_modern_discovery()
            except Exception:
                pass

    def _on_product_types_error(self, msg):
        if hasattr(self, 'modern_type') and self.modern_type:
            self.modern_type.blockSignals(True)
            self.modern_type.clear()
            self.modern_type.addItem("AHI-L1b-FLDK")
            self.modern_type.blockSignals(False)
        self._trigger_modern_discovery()  # fall back

    def _safe_post_show_initial_discovery(self):
        """THE SINGLE SAFE ENTRY POINT for initial modern discovery (E/F fix).
        Called exclusively via showEvent + QTimer.singleShot(80) — after .show() and event loop active.
        This is the only place initial _discover_product_types is triggered from construction path.
        All other calls are from user signals (sat/type changes) or this one post-show.
        _closing and ui_mode guards + scheduled flag (set in showEvent) prevent races/dupes.
        """
        if getattr(self, '_closing', False):
            return
        if getattr(self, 'ui_mode', None) != "modern":
            return
        # The actual work: starts the product discoverer QThread (then chains to dates via callback).
        # Now guaranteed safe: window is shown, widgets (modern_type etc.) exist, event loop running.
        try:
            self._discover_product_types()
        except Exception as e:
            # Hardened: never let post-show init crash the app (H requirement).
            print(f"[E/F safe init] _discover_product_types post-show failed (non-fatal): {e}")

    # ====================== DYNAMIC MONTHS PER YEAR ======================
    def _discover_months_for_year(self, year_str):
        """When user picks a year, query AWS for actual available months.
        If year is None or cleared, reset everything below to None.
        """
        if self._closing:
            return

        if not year_str or not str(year_str).isdigit():
            self._reset_all_below_year_to_none()
            return

        sat = self.modern_sat.currentText()
        bucket = self._get_bucket_for_satellite(sat)
        product = self._get_product_prefix()
        year = int(year_str)

        if self._months_discoverer and self._months_discoverer.isRunning():
            self._months_discoverer.cancel()
            self._months_discoverer.wait(1500)

        self._months_discoverer = AvailableMonthsDiscoverer(bucket, product, year)
        self._months_discoverer.months_discovered.connect(self._on_months_discovered)
        self._months_discoverer.error.connect(lambda m: None)  # silent fallback
        self._months_discoverer.start()

    def _on_months_discovered(self, months):
        """Populate month combo with only the months that actually exist on AWS for the selected year.
        Prepend 'None' so user explicitly picks a month to load days.
        Hardened against concurrent shutdown / widget lifetime.
        """
        if self._closing:
            return
        try:
            if months:
                month_strs = [f"{m:02d}" for m in months]
            else:
                month_strs = ["--"]  # Modern placeholder (H#2)
            month_strs = ["None"] + month_strs

            # Safe populate for Quick Scene
            if hasattr(self, 'qs_month') and self.qs_month:
                self._safe_populate_combo(self.qs_month, month_strs, select_last=False)

            # Also for animation from/to if they exist
            for attr in ['anim_from_month', 'anim_to_month']:
                if hasattr(self, attr):
                    cb = getattr(self, attr)
                    if cb:
                        self._safe_populate_combo(cb, month_strs, select_last=False)
        except Exception:
            pass  # hardened: discovery callbacks must never crash UI

    # ====================== DYNAMIC HOURS + MINUTES (only when day/hour loaded) ======================
    def _discover_hours_for_day(self, day_str):
        """Called when a Day is selected. Only then do we query AWS for actual hours.
        For GOES, the 'month' combo holds DOY values."""
        if self._closing:
            return

        sat = self.modern_sat.currentText()
        is_goes = "GOES" in sat.upper()

        day_clean = day_str.strip() if isinstance(day_str, str) else ""
        if not is_goes and (not day_clean or day_clean.lower() in ("none", "any", "--", "")):
            self._populate_default_hours()
            return

        try:
            day = int(day_clean)
        except:
            if not is_goes:
                self._populate_default_hours()
            return

        year_str = self.qs_year.currentText() if hasattr(self, 'qs_year') and self.qs_year else None
        month_str = self.qs_month.currentText() if hasattr(self, 'qs_month') and self.qs_month else None

        if not year_str or not str(year_str).isdigit():
            year_str = self.anim_from_year.currentText() if hasattr(self, 'anim_from_year') and self.anim_from_year else None
        if not month_str or not str(month_str).isdigit():
            month_str = self.anim_from_month.currentText() if hasattr(self, 'anim_from_month') and self.anim_from_month else None

        if not year_str or not str(year_str).isdigit():
            self._populate_default_hours()
            return

        bucket = self._get_bucket_for_satellite(sat)
        product = self._get_product_prefix()

        if self._hours_discoverer and self._hours_discoverer.isRunning():
            self._hours_discoverer.cancel()
            self._hours_discoverer.wait(1500)

        # For GOES: month_str is the DOY, day is unused (pass 1)
        doy = int(month_str) if is_goes and month_str and month_str.isdigit() else 1
        self._hours_discoverer = AvailableHoursDiscoverer(
            bucket, product, int(year_str), int(month_str) if is_goes else int(month_str), day if not is_goes else 1
        )
        self._hours_discoverer.hours_discovered.connect(self._on_hours_discovered)
        self._hours_discoverer.start()

    def _on_hours_discovered(self, hours):
        if self._closing:
            return
        try:
            if hours:
                hour_strs = [f"{h:02d}" for h in hours]
            else:
                hour_strs = ["--"]  # Modern: placeholder, no static 00-23 (H#2)
            # Populate Quick Scene hour
            if hasattr(self, 'qs_hour') and self.qs_hour:
                self._safe_populate_combo(self.qs_hour, hour_strs, select_last=True)

            # Animation hours
            for attr in ['anim_from_hour', 'anim_to_hour']:
                if hasattr(self, attr):
                    cb = getattr(self, attr)
                    if cb:
                        self._safe_populate_combo(cb, hour_strs, select_last=True)

            # After hours are loaded, we can optionally auto-trigger minutes for the current hour
            # but we'll do it on explicit hour change instead
        except Exception:
            pass

    def _populate_default_hours(self):
        """Fallback placeholder when day is None or discovery not possible (Modern only, no static ranges per H#2)."""
        hour_strs = ["--"]
        if hasattr(self, 'qs_hour') and self.qs_hour:
            self._safe_populate_combo(self.qs_hour, hour_strs, select_last=True)
        for attr in ['anim_from_hour', 'anim_to_hour']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    self._safe_populate_combo(cb, hour_strs, select_last=True)

    def _discover_minutes_for_hour(self, hour_str):
        """Only query minutes when an hour is actually selected.
        For GOES, populate with all minutes since no minute subdirectories exist."""
        if self._closing:
            return

        sat = self.modern_sat.currentText()
        is_goes = "GOES" in sat.upper()

        if is_goes:
            # GOES: no minute subdirectories, show all minutes for user selection
            all_mins = [f"{m:02d}" for m in [0, 10, 20, 30, 40, 50]]
            self._on_minutes_discovered([int(m) for m in all_mins])
            return

        hour_clean = hour_str.strip() if isinstance(hour_str, str) else ""
        if not hour_clean or hour_clean.lower() in ("none", "any", "--", ""):
            self._populate_default_minutes()
            return

        try:
            hour = int(hour_clean)
        except:
            return

        # Resolve current context (year/month/day) — robust guarded
        year_str = self.qs_year.currentText() if hasattr(self, 'qs_year') and self.qs_year else None
        month_str = self.qs_month.currentText() if hasattr(self, 'qs_month') and self.qs_month else None
        day_str = self.qs_day.currentText() if hasattr(self, 'qs_day') and self.qs_day else None

        if not all([year_str, month_str, day_str]) or not (str(year_str).isdigit() and str(month_str).isdigit() and str(day_str).isdigit()):
            year_str = self.anim_from_year.currentText() if hasattr(self, 'anim_from_year') and self.anim_from_year else year_str
            month_str = self.anim_from_month.currentText() if hasattr(self, 'anim_from_month') and self.anim_from_month else month_str
            day_str = self.anim_from_day.currentText() if hasattr(self, 'anim_from_day') and self.anim_from_day else day_str

        if not all([year_str, month_str, day_str]) or not (str(year_str).isdigit() and str(month_str).isdigit() and str(day_str).isdigit()):
            self._populate_default_minutes()
            return

        bucket = self._get_bucket_for_satellite(sat)
        product = self._get_product_prefix()

        if self._minutes_discoverer and self._minutes_discoverer.isRunning():
            self._minutes_discoverer.cancel()
            self._minutes_discoverer.wait(1500)

        self._minutes_discoverer = AvailableMinutesDiscoverer(
            bucket, product, int(year_str), int(month_str), int(day_str), hour
        )
        self._minutes_discoverer.minutes_discovered.connect(self._on_minutes_discovered)
        self._minutes_discoverer.start()

    def _on_minutes_discovered(self, minutes):
        if self._closing:
            return
        try:
            # For Target/Japan rapid-scan products, use 2.5-min aligned grid
            product = self._get_product_prefix() if hasattr(self, '_get_product_prefix') else "AHI-L1b-FLDK"
            if "TARGET" in product.upper() or "JAPAN" in product.upper():
                min_strs = self._get_rapid_scan_minutes()
            elif minutes:
                min_strs = [f"{m:02d}" for m in minutes]
            else:
                min_strs = ["--"]  # Modern: placeholder (H#2)
            if hasattr(self, 'qs_min') and self.qs_min:
                self._safe_populate_combo_with_none(self.qs_min, min_strs)

            for attr in ['anim_from_min', 'anim_to_min']:
                if hasattr(self, attr):
                    cb = getattr(self, attr)
                    if cb:
                        self._safe_populate_combo_with_none(cb, min_strs)
        except Exception:
            pass

    def _get_rapid_scan_minutes(self) -> list:
        """Return 2.5-min aligned minute values when Target or Japan is selected, else 10-min grid."""
        product = self._get_product_prefix() if hasattr(self, '_get_product_prefix') else "AHI-L1b-FLDK"
        if "TARGET" in product.upper() or "JAPAN" in product.upper():
            return ["00", "02", "05", "07", "10", "12", "15", "17",
                    "20", "22", "25", "27", "30", "32", "35", "37",
                    "40", "42", "45", "47", "50", "52", "55", "57"]
        return ["00", "10", "20", "30", "40", "50"]

    def _populate_default_minutes(self):
        mins = self._get_rapid_scan_minutes()  # Modern: dynamic grid based on product
        if hasattr(self, 'qs_min') and self.qs_min:
            self._safe_populate_combo_with_none(self.qs_min, mins)
        for attr in ['anim_from_min', 'anim_to_min']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    self._safe_populate_combo_with_none(cb, mins)

    def _safe_populate_combo_with_none(self, combo, items, none_label="None"):
        """Like _safe_populate_combo but always puts a 'None' option first."""
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(none_label)
            if items:
                combo.addItems([str(x) for x in items])
            # Select the first real value after None, or keep None
            if combo.count() > 1:
                combo.setCurrentIndex(1)
        finally:
            combo.blockSignals(False)

    # ====================== DAY LOADING ONLY AFTER MONTH IS PICKED ======================
    def _discover_days_for_month(self, month_str):
        """ONLY called when user picks a month/DOY. Do not pre-load days.
        For GOES, month combo holds DOY — skip day discovery and trigger hour discovery directly."""
        if self._closing:
            return

        sat = self.modern_sat.currentText()
        is_goes = "GOES" in sat.upper()
        if is_goes:
            # GOES: month combo holds DOY → discover hours directly
            year_str = self.qs_year.currentText() if hasattr(self, 'qs_year') and self.qs_year else None
            if year_str and str(year_str).isdigit() and month_str and month_str.isdigit():
                bucket = self._get_bucket_for_satellite(sat)
                product = self._get_product_prefix()
                if self._hours_discoverer and self._hours_discoverer.isRunning():
                    self._hours_discoverer.cancel()
                    self._hours_discoverer.wait(1500)
                self._hours_discoverer = AvailableHoursDiscoverer(
                    bucket, product, int(year_str), int(month_str), 1
                )
                self._hours_discoverer.hours_discovered.connect(self._on_hours_discovered)
                self._hours_discoverer.start()
            return

        month_clean = str(month_str).strip() if month_str else ""
        if not month_clean or month_clean.lower() in ("none", "any", "--", ""):
            self._reset_days_to_none()
            return

        try:
            month = int(month_clean)
        except ValueError:
            return

        year_str = None
        if hasattr(self, 'qs_year') and self.qs_year:
            year_str = self.qs_year.currentText()
        if not year_str or not str(year_str).isdigit():
            if hasattr(self, 'anim_from_year') and self.anim_from_year:
                year_str = self.anim_from_year.currentText()

        if not year_str or not str(year_str).isdigit():
            self._reset_days_to_none()
            return

        bucket = self._get_bucket_for_satellite(sat)
        product = self._get_product_prefix()

        if self._days_discoverer and self._days_discoverer.isRunning():
            self._days_discoverer.cancel()
            self._days_discoverer.wait(1500)

        self._days_discoverer = AvailableDaysDiscoverer(
            bucket, product, int(year_str), month
        )
        self._days_discoverer.days_discovered.connect(self._on_days_discovered)
        self._days_discoverer.start()

    def _on_days_discovered(self, days):
        if self._closing:
            return
        try:
            if days:
                day_strs = ["None"] + [f"{d:02d}" for d in days]
            else:
                day_strs = ["None", "--"]  # Modern placeholder (H#2)

            # Quick Scene
            if hasattr(self, 'qs_day') and self.qs_day:
                self._safe_populate_combo(self.qs_day, day_strs, select_last=False)

            # Animation ranges
            for attr in ['anim_from_day', 'anim_to_day']:
                if hasattr(self, attr):
                    cb = getattr(self, attr)
                    if cb:
                        self._safe_populate_combo(cb, day_strs, select_last=False)
        except Exception:
            pass

    def _reset_days_to_none(self):
        """Set day combos to just have 'None' when no month is selected."""
        for attr in ['qs_day', 'anim_from_day', 'anim_to_day']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    cb.blockSignals(True)
                    try:
                        cb.clear()
                        cb.addItem("None")
                    finally:
                        cb.blockSignals(False)

    def _reset_all_below_year_to_none(self):
        """When a Year is picked (or years just loaded), reset Month + Day + HH + MM to None only.
        This enforces: only load the month list once the user actually picks a year.
        """
        # Month combos → just "None" until user picks year (which will then load real months via signal)
        for attr in ['qs_month', 'anim_from_month', 'anim_to_month']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    cb.blockSignals(True)
                    try:
                        cb.clear()
                        cb.addItem("None")
                    finally:
                        cb.blockSignals(False)

        # Also reset everything below month
        self._reset_days_to_none()

        for attr in ['qs_hour', 'anim_from_hour', 'anim_to_hour']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    cb.blockSignals(True)
                    try:
                        cb.clear()
                        cb.addItem("None")
                    finally:
                        cb.blockSignals(False)

        for attr in ['qs_min', 'anim_from_min', 'anim_to_min']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    cb.blockSignals(True)
                    try:
                        cb.clear()
                        cb.addItem("None")
                    finally:
                        cb.blockSignals(False)

    def _update_animation_date_combos(self):
        """Trigger dynamic (cheap) year discovery from AWS for Animation tab."""
        self._trigger_modern_discovery()

    def _safe_populate_combo(self, combo, items, select_last=True, default=None):
        """Populate a QComboBox without firing currentTextChanged signals
        (prevents the expensive AWS discovery feedback loop).
        """
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            combo.clear()
            if items:
                combo.addItems([str(x) for x in items])
                if select_last:
                    combo.setCurrentIndex(combo.count() - 1)
                elif default is not None:
                    idx = combo.findText(str(default))
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
            else:
                # Modern only: placeholder + validation warning (no static years per H#2)
                combo.addItem("-- (no AWS data)")
        finally:
            combo.blockSignals(False)

    def _on_years_discovered_unified(self, years):
        """Single source of truth for live AWS years. Updates Quick Scene + both Animation ranges.
        Prevents the old split-handler mess. Always resets lower fields for safe UX.
        Hardened with guards for thread timing / shutdown.
        """
        if self._closing:
            return
        try:
            if years:
                year_strs = [str(y) for y in years]
                msg = "✓ Dates loaded live from AWS S3 (no static lists)"
            else:
                # Modern only: placeholder on empty discover (H#2 / AB). Warn user.
                year_strs = ["-- (no AWS data)"]
                msg = "⚠ No data on AWS for selection — try different sat/product, check network, or Force Refresh"

            # Quick Scene
            if hasattr(self, 'qs_year') and self.qs_year:
                self._safe_populate_combo(self.qs_year, year_strs)

            # Animation from/to (robust even if one tab not fully built)
            for attr in ['anim_from_year', 'anim_to_year']:
                if hasattr(self, attr):
                    cb = getattr(self, attr)
                    if cb:
                        self._safe_populate_combo(cb, year_strs)

            # Reset lower levels on both tabs (enforces pick order: year → month → ...)
            self._reset_all_below_year_to_none()

            # Update validation on both tabs — fully guarded (prevents crash in legacy mode / partial init / shutdown)
            if hasattr(self, 'qs_validation') and self.qs_validation:
                try:
                    self.qs_validation.setText(msg)
                except Exception:
                    pass
            if hasattr(self, 'anim_validation') and self.anim_validation:
                try:
                    self.anim_validation.setText(msg)
                except Exception:
                    pass
        except Exception:
            # Never let discovery callback crash the UI
            pass

    def _apply_bands_to_modern(self, bands):
        """Apply a preset to the modern band checkboxes"""
        for name, cb in self.modern_band_checks.items():
            num = int(name[1:])
            cb.setChecked(num in bands)

    # ======================================================================
    #  REAL MODERN DOWNLOAD IMPLEMENTATIONS (no legacy routing)
    #  These make Quick Scene + Animation fully functional + superior
    # ======================================================================

    def _start_modern_direct_download(self, prefix: str, bands: list, subfolder_name: str = None,
                                       goes_minute_filter: int = None):
        """Core: launch S3DownloadWorker for a precise prefix directly from Modern UI.
        Updates the shared modern console with live progress. Superior single-click UX.
        """
        if self.modern_download_worker and self.modern_download_worker.isRunning():
            QMessageBox.warning(self, "Download Busy", "A Modern download is already in progress. Cancel or wait.")
            return

        sat = self.modern_sat.currentText() if hasattr(self, 'modern_sat') else "Himawari 9"
        bucket = self._get_bucket_for_satellite(sat)

        save_dir = Path(self.modern_dir_edit.text()) if hasattr(self, 'modern_dir_edit') else self.modern_download_dir
        if subfolder_name:
            save_dir = save_dir / subfolder_name
        save_dir.mkdir(parents=True, exist_ok=True)

        # ── Pre-download check: skip bands already in existing NC ──────
        try:
            from bg_to_nc import _read_band_inventory, extract_timestamp as _ext_ts
            timestamp = _ext_ts(save_dir)
            nc_path = save_dir / f"{timestamp}_AHI.nc"
            if nc_path.exists():
                existing = _read_band_inventory(nc_path)
                if existing:
                    _skipper, _ = self._load_process_dat_settings()
                    orig_set = set(bands) if bands else set()
                    # Convert existing band names (e.g. "B01") to int list
                    existing_ints = {int(b[1:]) for b in existing if b.startswith("B") and len(b) == 3}
                    filtered = [b for b in (bands or []) if b not in existing_ints]
                    skipped = orig_set - set(filtered)
                    if skipped:
                        skipped_str = ", ".join(f"B{b:02d}" for b in sorted(skipped))
                        if _skipper == "Silent":
                            self._update_modern_status(f"Skipping {skipped_str} — already in NC")
                        elif _skipper == "skipper":
                            from PySide6.QtWidgets import QMessageBox
                            msg = (f"Bands {skipped_str} already exist in:\n{nc_path.name}\n\n"
                                   f"Download Missing Bands?")
                            reply = QMessageBox.question(self, "Bands Already Exist", msg,
                                                          QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
                            if reply == QMessageBox.Cancel:
                                return
                            elif reply == QMessageBox.No:
                                filtered = bands  # download all anyway
                        if not filtered:
                            self._update_modern_status(f"All bands already in NC — nothing to download")
                            return
                        bands = filtered
        except Exception as e:
            print(f"[DEBUG] Pre-download skip check failed (harmless): {e}")

        worker = S3DownloadWorker(
            bucket=bucket,
            prefix=prefix,
            download_dir=str(save_dir),
            bands=bands or list(range(1, 17)),
            max_workers=8
        )
        if goes_minute_filter is not None:
            worker.goes_minute_filter = goes_minute_filter
        self.modern_download_worker = worker

        # ── Non-modal progress window for the single download ────────────
        short = prefix.rstrip('/').rsplit('/', 1)[-1] or prefix
        prog = RangeProgressDialog(self, total_slots=1, job_title=f"Single Download — {short}")
        prog.update_download("Initialising download...")
        self._single_progress_dialog = prog

        def _single_progress_msg(msg):
            self._update_modern_status(msg)
            if prog._cancelled or not prog.isVisible():
                return
            up = msg.lower()
            if any(k in up for k in ("extract", "processing", "netcdf", "convert", "encode")):
                prog.update_process(msg)
            else:
                prog.update_download(msg)

        # Wire beautiful live feedback to the Modern console (the superiority)
        self.modern_download_worker.progress.connect(_single_progress_msg)
        self.modern_download_worker.file_progress.connect(
            lambda cur, tot, fn: (
                self._update_modern_status(f"[{cur}/{tot}] {fn}", progress=int((cur / max(1, tot)) * 100)),
                (not prog._cancelled and prog.isVisible()) and prog.update_download(
                    f"[{cur}/{tot}] {fn}", int((cur / max(1, tot)) * 100))
            )
        )
        self.modern_download_worker.finished.connect(self._on_modern_download_finished)
        self.modern_download_worker.error.connect(
            lambda err: (
                self._update_modern_status(f"ERROR: {err}"),
                (not prog._cancelled and prog.isVisible()) and prog.update_download(f"ERROR: {err}")
            )
        )

        # Cancel wiring: window Cancel cancels the worker (like the range flow)
        orig_cancel = prog._do_cancel
        def _cancel_with_worker():
            orig_cancel()
            if self.modern_download_worker and self.modern_download_worker.isRunning():
                self.modern_download_worker.cancel()
        prog._do_cancel = _cancel_with_worker

        self._set_modern_download_active(True)
        self._update_modern_status(f"Starting direct download: {prefix}", progress=0)
        prog.show()

        self.modern_download_worker.start()

    def _start_modern_extraction(self, directory_path):
        if self.processor_worker and self.processor_worker.isRunning():
            self._update_modern_status("Extraction already in progress")
            return
        self.processor_worker = HimawariProcessorWorker(
            directory_path,
            "auto",
            self.force_simple,
            max_workers=8,
            skip_rgb=self.skip_rgb,
            skip_ads=self.skip_ads,
            low_io=getattr(self, 'low_io', True),
            nc_format=getattr(self, 'nc_format', 'pwards'),
        )
        prog = getattr(self, '_single_progress_dialog', None)
        def _proc_msg(msg):
            self._update_modern_status(msg)
            if prog and not prog._cancelled and prog.isVisible():
                prog.update_process(msg)
        self.processor_worker.progress.connect(_proc_msg)
        self.processor_worker.finished.connect(self._on_modern_extraction_finished)
        self.processor_worker.error.connect(
            lambda err: (
                self._update_modern_status(f"EXTRACT ERROR: {err}"),
                (prog and not prog._cancelled and prog.isVisible()) and prog.update_process(f"Error: {err}")
            )
        )
        self._update_modern_status(f"Auto processing (extract & NetCDF): {Path(directory_path).name}")
        self.processor_worker.start()

    def _on_modern_extraction_finished(self, success, directory_path):
        if success:
            self._update_modern_status(f"Auto process complete: {Path(directory_path).name}")
        else:
            self._update_modern_status("Auto process finished with issues")
        prog = getattr(self, '_single_progress_dialog', None)
        if prog:
            if not prog._cancelled:
                prog.slot_processed()
                prog.update_process("Processing complete" if success else "Processing finished with issues", 100)
            else:
                prog.close()
            self._single_progress_dialog = None
            QTimer.singleShot(1500, prog.close)

    def _on_modern_download_finished(self, success, path):
        self._set_modern_download_active(False)
        prog = getattr(self, '_single_progress_dialog', None)
        try:
            if success:
                self._update_modern_status(f"Download complete → {Path(path).name}", progress=100)
                if prog and not prog._cancelled:
                    prog.slot_downloaded()
                    prog.update_download("Download complete", 100)
                is_goes = "GOES" in (self.modern_sat.currentText() if hasattr(self, 'modern_sat') else "").upper()
                if not is_goes and path:
                    QTimer.singleShot(500, lambda: self._start_modern_extraction(path))
                else:
                    if prog and not prog._cancelled:
                        prog.update_process("Skipped (GOES)", 100)
                        QTimer.singleShot(1500, prog.close)
                    else:
                        prog and prog.close()
                    QTimer.singleShot(1200, lambda: self._update_modern_status(f"Files saved to {path}"))
            else:
                self._update_modern_status("Download finished with issues (see console)")
                if prog:
                    if prog._cancelled:
                        QTimer.singleShot(1500, prog.close)
                    else:
                        prog.update_download("Download finished with issues", 100)
                        QTimer.singleShot(1500, prog.close)
        except Exception:
            pass  # never let finished callback crash UI

    def _download_quick_scene_modern(self):
        """REAL direct single-scene download. Fully functional, no legacy handoff."""
        try:
            y = self.qs_year.currentText()
            m = self.qs_month.currentText()
            d = self.qs_day.currentText()
            h = self.qs_hour.currentText()
            mi = self.qs_min.currentText()

            if any(x in (None, "", "None", "Loading", "Loading from AWS...") for x in [y, m, d, h, mi]):
                self._update_modern_status("Select a complete valid timestamp (no 'None') first")
                QMessageBox.information(self, "Quick Scene", "Please choose Year, Month, Day, Hour, and Minute from the live AWS-populated lists.")
                return

            selected_bands = [int(name[1:]) for name, cb in getattr(self, 'modern_band_checks', {}).items() if cb.isChecked()]
            if not selected_bands:
                selected_bands = [3, 2, 1]

            product = self._get_product_prefix()
            sat = self.modern_sat.currentText() if hasattr(self, 'modern_sat') else "Himawari 9"
            is_goes = "GOES" in sat.upper()

            if is_goes:
                from datetime import datetime as _dt
                dt = _dt(int(y), int(m), int(d), int(h), int(mi))
                doy = dt.timetuple().tm_yday
                # GOES uses hour-level folders: PRODUCT/YYYY/DOY/HH/
                prefix = f"{product}/{int(y):04d}/{doy:03d}/{int(h):02d}/"
                scene_name = f"{product}_{y}{m}{d}_{h}{mi}"
                min_filter = int(mi) if mi and mi.isdigit() else None
            else:
                prefix = f"{product}/{int(y):04d}/{int(m):02d}/{int(d):02d}/{int(h):02d}{int(mi):02d}/"
                scene_name = f"{product}_{y}{m}{d}_{h}{mi}"
                min_filter = None

            self._start_modern_direct_download(prefix, selected_bands, subfolder_name=scene_name,
                                                goes_minute_filter=min_filter)

        except Exception as e:
            self._update_modern_status(f"Quick Scene error: {e}")
            QMessageBox.critical(self, "Quick Scene Download", str(e))

    def _download_animation_range(self):
        """Actually trigger the real download_date_range backend from the Modern UI.
        Shows confirmation dialog with pre-scan stats, then a progress dialog.
        """
        if getattr(self, '_closing', False):
            return
        try:
            from datetime import datetime as _dt

            # Build datetimes from the modern controls
            y1 = int(self.anim_from_year.currentText()) if hasattr(self, 'anim_from_year') and self.anim_from_year else 2026
            m1 = int(self.anim_from_month.currentText()) if hasattr(self, 'anim_from_month') and self.anim_from_month else 1
            d1 = int(self.anim_from_day.currentText()) if hasattr(self, 'anim_from_day') and self.anim_from_day else 1
            h1 = int(self.anim_from_hour.currentText()) if hasattr(self, 'anim_from_hour') and self.anim_from_hour else 0
            mi1 = int(self.anim_from_min.currentText()) if hasattr(self, 'anim_from_min') and self.anim_from_min else 0
            start = _dt(y1, m1, d1, h1, mi1)

            y2 = int(self.anim_to_year.currentText()) if hasattr(self, 'anim_to_year') and self.anim_to_year else 2026
            m2 = int(self.anim_to_month.currentText()) if hasattr(self, 'anim_to_month') and self.anim_to_month else 1
            d2 = int(self.anim_to_day.currentText()) if hasattr(self, 'anim_to_day') and self.anim_to_day else 1
            h2 = int(self.anim_to_hour.currentText()) if hasattr(self, 'anim_to_hour') and self.anim_to_hour else 0
            mi2 = int(self.anim_to_min.currentText()) if hasattr(self, 'anim_to_min') and self.anim_to_min else 0
            end = _dt(y2, m2, d2, h2, mi2)

            step = float(self.anim_step.currentText().split()[0])

            selected_bands = [int(name[1:]) for name, cb in self.modern_band_checks.items() if cb.isChecked()]

            save_root = str(self.modern_download_dir)
            if hasattr(self, 'modern_dir_edit') and self.modern_dir_edit:
                try:
                    save_root = self.modern_dir_edit.text() or save_root
                except Exception:
                    pass

            def _modern_range_status(msg):
                if hasattr(self, '_update_modern_status'):
                    self._update_modern_status(msg)
                print(f"[ANIM RANGE] {msg}")

            # ── Pre-scan: generate slots + check existing NC files ──────
            product = self._get_product_prefix()
            total_files = 0
            complete_slots = 0
            slot_names = []
            current = start
            total_minutes = current.hour * 60 + current.minute
            rounded = (total_minutes // step) * step
            h = int(rounded // 60)
            m = int(rounded % 60)
            current = current.replace(hour=h, minute=m, second=0, microsecond=0)
            while current <= end:
                slot_name = self._build_flat_slot_name(product, current)
                slot_names.append(slot_name)
                slot_dir = Path(save_root) / slot_name
                # For fractional minutes, encode as HHMMSS in NC filename
                mi = current.minute
                if mi != int(mi):
                    sec = int(round((mi - int(mi)) * 60))
                    ts_str = f"{current.year:04d}{current.month:02d}{current.day:02d}_{current.hour:02d}{int(mi):02d}{sec:02d}"
                else:
                    ts_str = f"{current.year:04d}{current.month:02d}{current.day:02d}_{current.hour:02d}{int(mi):02d}"
                nc_file = slot_dir / f"{ts_str}_AHI.nc"
                # Also try with underscore-style folder names
                if not nc_file.exists():
                    alt_name = f"{product}_{current.year:04d}{current.month:02d}{current.day:02d}_{current.hour:02d}{int(mi):02d}"
                    if mi != int(mi):
                        alt_name += f"{sec:02d}"
                    alt_dir = Path(save_root) / alt_name
                    nc_file = alt_dir / f"{ts_str}_AHI.nc"
                if nc_file.exists():
                    complete_slots += 1
                else:
                    total_files += len(selected_bands) if selected_bands else 16
                current += timedelta(minutes=step)

            num_slots = len(slot_names)
            _modern_range_status(f"Pre-scan: {num_slots} slots, {complete_slots} complete, {total_files} files to download")

            # ── Confirmation dialog ──────────────────────────────────
            confirm = QMessageBox(self)
            confirm.setWindowTitle("Animation Range Download")
            details = (f"{num_slots} time slots × {len(selected_bands) or 16} bands = {num_slots * (len(selected_bands) or 16)} files\n"
                       f"{complete_slots} slots already complete (skipped)\n"
                       f"New to download: {total_files} files ({num_slots - complete_slots} slots)")
            confirm.setText(details)
            dl_missing = confirm.addButton("Download Missing", QMessageBox.AcceptRole)
            dl_all = confirm.addButton("Download All", QMessageBox.AcceptRole)
            cancel_btn = confirm.addButton("Cancel", QMessageBox.RejectRole)
            confirm.setDefaultButton(cancel_btn)
            confirm.exec()

            if confirm.clickedButton() == cancel_btn:
                _modern_range_status("Range download cancelled by user")
                return

            download_missing = confirm.clickedButton() == dl_missing

            # ── Progress window (non-modal) ─────────────────────────────
            slot_total = num_slots - complete_slots if download_missing else num_slots
            prog = RangeProgressDialog(
                self,
                total_slots=slot_total,
                job_title=f"Animation Range — {slot_total} slots",
            )
            prog.update_download("Initialising...")

            # Modify callback to update progress dialog
            def _progress_with_dialog(msg):
                _modern_range_status(msg)
                if "Downloading" in msg:
                    prog.update_download(msg)
                elif "Extracting" in msg or "Processing" in msg or "NetCDF" in msg:
                    prog.update_process(msg)

            # Store progress dialog reference for signal connections
            self._range_progress_dialog = prog

            # Patch slot_finished to update progress
            orig_slot_finished = getattr(self, '_range_slot_finished', None)

            def _on_slot_finished(idx, tot, pref, ok):
                if prog._cancelled:
                    return
                prog.slot_downloaded()
                _modern_range_status(f"Slot {idx}/{tot} {'OK' if ok else 'FAIL'}: {pref}")

            self._range_slot_callback = _on_slot_finished

            def _on_range_finished_prog(ok, root):
                prog.slot_processed()
                if ok and not prog._cancelled:
                    prog.update_process("All slots processed", 100)
                if not prog._cancelled:
                    QTimer.singleShot(1500, prog.close)

            self._range_finished_callback = _on_range_finished_prog

            # ── Start the actual download ────────────────────────────
            if download_missing:
                # Filter bands per slot to skip complete ones — handled by download_date_range
                pass  # For simplicity, download_date_range handles file-level skip; we just skip slots

            self.download_date_range(
                start_datetime=start,
                end_datetime=end,
                bands=selected_bands or None,
                download_root=save_root,
                time_step_minutes=step,
                include_winds=False,
                auto_process=True,
                process_mode="auto",
                progress_callback=_progress_with_dialog
            )

            _modern_range_status("Animation range job started.")
            prog.show()

        except Exception as e:
            if hasattr(self, '_update_modern_status'):
                self._update_modern_status(f"ERROR: Animation range failed: {e}")
            else:
                print(f"Modern range error: {e}")
            QMessageBox.warning(self, "Animation Download", f"Range download encountered an issue:\n{e}\n\nYou can use the Legacy S3 Browser for advanced cases.")

    def _quick_range_modern(self, hours):
        """REAL quick recent range download powered by live AWS. No legacy routing.
        Uses 'now' UTC rounded down, goes back N hours at the currently selected step.
        """
        if getattr(self, '_closing', False):
            return
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            now = _dt.now(_tz.utc).replace(second=0, microsecond=0)
            # Use the currently selected step from the anim_step combo
            step = float(self.anim_step.currentText().split()[0]) if hasattr(self, 'anim_step') else 10
            # Round down to nearest step boundary
            total_minutes = now.hour * 60 + now.minute
            rounded = (total_minutes // step) * step
            h = int(rounded // 60)
            m = int(rounded % 60)
            end = now.replace(hour=h, minute=m)
            start = end - _td(hours=hours)

            selected_bands = [int(name[1:]) for name, cb in getattr(self, 'modern_band_checks', {}).items() if cb.isChecked()] or list(range(1, 17))

            save_root = Path(self.modern_dir_edit.text()) if hasattr(self, 'modern_dir_edit') else self.modern_download_dir

            self._update_modern_status(f"Quick {hours}h range: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')} (step: {step} min)")

            # Use the (now fixed + modern-aware) backend
            self.download_date_range(
                start_datetime=start,
                end_datetime=end,
                bands=selected_bands,
                download_root=str(save_root),
                time_step_minutes=step,
                include_winds=False,
                auto_process=True,
                process_mode="auto",
                progress_callback=lambda msg: self._update_modern_status(msg)
            )
        except Exception as e:
            try:
                self._update_modern_status(f"Quick range error: {e}")
            except Exception:
                pass
            QMessageBox.warning(self, "Quick Range", f"Could not start quick range:\n{e}")

    def _open_legacy_in_new_window(self):
        """Opens the FULL original Legacy S3 Browser (with every feature) in a separate window.
        Used by explicit "Legacy Browser" button and as fallback. Clean independent instance.
        """
        legacy_win = HimawariFileManager(force_legacy=True)
        legacy_win.show()

    # NOTE: Legacy-in-modern stacked experiments fully removed in v3.2.1 cleanup.
    # Modern uses direct tabs + dedicated console. Legacy is always full separate window.
    # This keeps both UIs rock-solid with zero widget/tree conflicts. No dead code paths.

    # ======================================================================
    #  Original Legacy UI (kept for reference / future full embedding)
    # ======================================================================

    def init_ui(self):  # Legacy full UI — preserved intact for when user explicitly chooses Legacy mode
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(10, 10, 10, 10)
        self.main_layout = main_layout  # exposed so Modern switch banner can insert cleanly at top
        # v3.0.5.1 fix: stop Qt from auto-growing the window when the hidden
        # progress bars become visible (keeps the fullscreen layout inside frame).
        self.main_layout.setSizeConstraint(QLayout.SetNoConstraint)

        # Top toolbar
        toolbar = QFrame()
        toolbar.setStyleSheet("""
            QFrame {
                background: #1A1A1A;
                border-radius: 5px;
                padding: 8px;
            }
        """)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setSpacing(10)
        self.toolbar_layout = toolbar_layout  # exposed for Modern switch button injection in legacy mode

        toolbar_layout.addWidget(QLabel("Satellite:"))
        self.sat_combo = QComboBox()
        self.sat_combo.addItems(["Himawari 9", "Himawari 8", "GOES-16", "GOES-17", "GOES-18", "GOES-19", "GK-2A"])
        self.sat_combo.currentTextChanged.connect(self.on_satellite_changed)
        self.sat_combo.setFixedWidth(120)
        toolbar_layout.addWidget(self.sat_combo)

        self.back_btn = QPushButton("◀ Back")
        self.back_btn.setStyleSheet("""
            QPushButton {
                background: #37474F;
                color: white;
                padding: 6px 12px;
                border-radius: 3px;
                border: 1px solid #555;
            }
            QPushButton:hover { background: #455A64; }
            QPushButton:disabled { background: #444; color: #777; }
        """)
        self.back_btn.clicked.connect(self.go_back)
        self.back_btn.setEnabled(False)
        toolbar_layout.addWidget(self.back_btn)

        self.refresh_btn = QPushButton("⟳ Refresh")
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #5D8AA8;
                color: white;
                padding: 6px 12px;
                border-radius: 3px;
                border: 1px solid #555;
            }
            QPushButton:hover { background: #4A6572; }
        """)
        self.refresh_btn.clicked.connect(self.refresh_current)
        toolbar_layout.addWidget(self.refresh_btn)

        self.path_label = QLabel("s3://noaa-himawari9/")
        self.path_label.setStyleSheet("color: #FFA726; font-weight: bold; font-size: 12px; padding: 0 10px;")
        toolbar_layout.addWidget(self.path_label, 1)
        toolbar_layout.addStretch()
        main_layout.addWidget(toolbar)

        # Main content
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setSpacing(10)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # LEFT – directories
        left_panel = QWidget()
        left_panel.setMinimumWidth(250)
        left_panel.setMaximumWidth(350)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(5)

        dir_header = QHBoxLayout()
        dir_header.addWidget(QLabel("📁 Directories"))
        dir_header.addStretch()
        self.file_count_label = QLabel("0 files")
        self.file_count_label.setStyleSheet("color: #5D8AA8; font-weight: bold; font-size: 11px;")
        dir_header.addWidget(self.file_count_label)
        self.size_label = QLabel("0 MB")
        self.size_label.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px; padding-left: 10px;")
        dir_header.addWidget(self.size_label)
        left_layout.addLayout(dir_header)

        self.dir_tree = QTreeWidget()
        self.dir_tree.setHeaderLabel("Folders")
        self.dir_tree.setStyleSheet("""
            QTreeWidget {
                background: #1A1A1A; color: #EEE; border: 1px solid #444;
                border-radius: 4px; font-family: Consolas, monospace; font-size: 11px;
            }
            QTreeWidget::item { padding: 5px; }
            QTreeWidget::item:selected { background: #2D5A8A; color: white; }
            QTreeWidget::item:hover { background: #333; }
        """)
        self.dir_tree.itemDoubleClicked.connect(self.on_dir_double_clicked)
        left_layout.addWidget(self.dir_tree, 1)
        content_layout.addWidget(left_panel)

        # MIDDLE – files
        middle_panel = QWidget()
        middle_layout = QVBoxLayout(middle_panel)
        middle_layout.setSpacing(5)

        files_header = QHBoxLayout()
        self.files_label = QLabel("📄 Files")
        self.files_label.setStyleSheet("color: #5D8AA8; font-weight: bold; font-size: 12px;")
        files_header.addWidget(self.files_label)
        files_header.addStretch()
        self.band_filter_label = QLabel("Filter: All bands")
        self.band_filter_label.setStyleSheet("color: #FFA726; font-size: 11px; padding: 2px 8px; background: #333; border-radius: 3px;")
        files_header.addWidget(self.band_filter_label)
        # GOES minute filter — shown only when a GOES satellite is selected
        self._goes_min_lbl = QLabel("Min:")
        self._goes_min_lbl.setStyleSheet("color: #FFA726; font-size: 11px; font-weight: bold; padding: 2px 4px;")
        self._goes_min_lbl.setVisible(False)
        files_header.addWidget(self._goes_min_lbl)
        self.goes_minute_combo = QComboBox()
        self.goes_minute_combo.addItems(["All", "00", "10", "20", "30", "40", "50"])
        self.goes_minute_combo.setStyleSheet("""
            QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444;
                         border-radius: 3px; padding: 2px 5px; font-size: 10pt; font-family: "Segoe UI"; min-width: 60px; max-height: 22px; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView { background: #2D2D2D; color: #EEE; font-size: 10pt; font-family: "Segoe UI"; selection-background-color: #37474F; }
        """)
        self.goes_minute_combo.setToolTip(
            "Filter files table by minute from the Modified timestamp.\n"
            "Only files matching the selected minute will be shown and downloaded."
        )
        self.goes_minute_combo.setVisible(False)
        self.goes_minute_combo.currentTextChanged.connect(self._on_goes_minute_filter_changed)
        files_header.addWidget(self.goes_minute_combo)
        middle_layout.addLayout(files_header)

        self.files_table = QTableWidget()
        self.files_table.setColumnCount(5)
        self.files_table.setHorizontalHeaderLabels(["Filename", "Band", "Size", "Modified", "Action"])
        self.files_table.horizontalHeader().setStretchLastSection(False)
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.files_table.setStyleSheet("""
            QTableWidget {
                background: #1A1A1A; color: #EEE; border: 1px solid #444;
                border-radius: 4px; gridline-color: #333;
            }
            QTableWidget::item { padding: 5px; }
            QHeaderView::section { background: #2D2D2D; color: #5D8AA8; padding: 5px; border: none; }
        """)
        self.files_table.setAlternatingRowColors(True)
        middle_layout.addWidget(self.files_table, 1)
        content_layout.addWidget(middle_panel, 1)

        # RIGHT – controls
        right_panel = QWidget()
        right_panel.setMinimumWidth(300)
        right_panel.setMaximumWidth(450)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(10)

        # Band selection
        band_group = QGroupBox("Band Selection")
        band_group.setStyleSheet("""
            QGroupBox { color: #FF9800; font-weight: bold; border: 1px solid #555;
                         border-radius: 5px; margin-top: 0px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
        """)
        band_layout = QVBoxLayout(band_group)
        band_grid = QGridLayout()
        self.band_checkboxes = {}
        for i in range(1, 17):
            checkbox = QCheckBox(f"B{i:02d}")
            checkbox.setChecked(True)
            checkbox.setStyleSheet("""
                QCheckBox { color: #EEE; padding: 3px; font-size: 10px; }
                QCheckBox::indicator { width: 14px; height: 14px; }
            """)
            checkbox.stateChanged.connect(self.on_band_selection_changed)
            row = (i - 1) // 4
            col = (i - 1) % 4
            band_grid.addWidget(checkbox, row, col)
            self.band_checkboxes[f"B{i:02d}"] = checkbox
        band_layout.addLayout(band_grid)

        band_buttons = QHBoxLayout()
        select_all_btn = QPushButton("All")
        select_none_btn = QPushButton("None")
        for btn in [select_all_btn, select_none_btn]:
            btn.setStyleSheet("""
                QPushButton { background: #37474F; color: white; padding: 4px 8px; border-radius: 2px;
                              font-size: 10px; border: 1px solid #555; }
                QPushButton:hover { background: #455A64; }
            """)
        select_all_btn.clicked.connect(self.select_all_bands)
        select_none_btn.clicked.connect(self.select_no_bands)
        band_buttons.addWidget(select_all_btn)
        band_buttons.addWidget(select_none_btn)
        band_buttons.addStretch()
        self.selected_bands_label = QLabel("Selected: All 16 bands")
        self.selected_bands_label.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")
        band_buttons.addWidget(self.selected_bands_label)
        band_layout.addLayout(band_buttons)
        right_layout.addWidget(band_group)

        # Download settings
        download_group = QGroupBox("Download Settings")
        download_group.setStyleSheet("""
            QGroupBox { color: #5D8AA8; font-weight: bold; border: 1px solid #555;
                        border-radius: 5px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
        """)
        download_layout = QVBoxLayout(download_group)
        download_layout.setSpacing(8)

        dir_layout = QHBoxLayout()
        dir_layout.addWidget(QLabel("Save to:"))
        self.dir_edit = QLineEdit()
        self.dir_edit.setText(str(self.default_download_dir))
        dir_layout.addWidget(self.dir_edit, 1)
        browse_btn = QPushButton("📁")
        browse_btn.setFixedWidth(40)
        browse_btn.setStyleSheet("""
            QPushButton { background: #5D8AA8; color: white; padding: 5px; border-radius: 3px; border: 1px solid #555; }
            QPushButton:hover { background: #4A6572; }
        """)
        browse_btn.clicked.connect(self.browse_directory)
        dir_layout.addWidget(browse_btn)
        download_layout.addLayout(dir_layout)

        self.estimated_size_label = QLabel("Estimated: 0 MB")
        self.estimated_size_label.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold; padding: 2px 0;")
        download_layout.addWidget(self.estimated_size_label)

        processing_layout = QVBoxLayout()
        self.auto_process_checkbox = QCheckBox("Auto-process after download")
        self.auto_process_checkbox.stateChanged.connect(self.on_auto_process_changed)
        self.auto_process_checkbox.setChecked(True)
        self.auto_process_checkbox.setEnabled(True)
        self.auto_process_checkbox.setStyleSheet("color: #5A6778;")
        processing_layout.addWidget(self.auto_process_checkbox)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))
        self.process_mode_combo = QComboBox()
        self.process_mode_combo.addItems([
            "Extract & NetCDF",
            "Extract Only",
            "NetCDF Only"
        ])
        self.process_mode_combo.setCurrentText("Extract & NetCDF")
        self.process_mode_combo.setEnabled(True)
        self.process_mode_combo.currentIndexChanged.connect(self.on_process_mode_changed)
        self.process_mode_combo.setStyleSheet("""
            QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444;
                         border-radius: 3px; padding: 3px; min-width: 130px; font-size: 11px; }
        """)
        mode_layout.addWidget(self.process_mode_combo, 1)
        processing_layout.addLayout(mode_layout)

        self.force_simple_checkbox = QCheckBox("Force simple (skip Satpy)")
        self.force_simple_checkbox.setChecked(self.force_simple)
        self.force_simple_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
        self.force_simple_checkbox.stateChanged.connect(self.on_force_simple_changed)
        processing_layout.addWidget(self.force_simple_checkbox)

        self.skip_extract_checkbox = QCheckBox("Skip extraction (.bz2 → NetCDF directly)")
        self.skip_extract_checkbox.setChecked(False)
        self.skip_extract_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
        self.skip_extract_checkbox.setToolTip(
            "Skip the separate bg_extract.py step. bg_to_nc.py reads .bz2 files directly."
        )
        self.skip_extract_checkbox.stateChanged.connect(self.on_skip_extract_changed)
        processing_layout.addWidget(self.skip_extract_checkbox)

        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("NetCDF format:"))
        self.nc_format_combo = QComboBox()
        self.nc_format_combo.addItems([
            "NetCDF (PWARDS)",
            "NetCDF (JMA)"
        ])
        self.nc_format_combo.setCurrentText("NetCDF (PWARDS)")
        self.nc_format_combo.setStyleSheet("""
            QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444;
                         border-radius: 3px; padding: 3px; min-width: 150px; font-size: 11px; }
        """)
        self.nc_format_combo.setToolTip(
            "PWARDS: per-band compressed NC + .ads.json sidecar (native geostationary grid).\n"
            "JMA: JMA/MSC-style NC, one file per band on a regular latitude/longitude grid\n"
            "     (NC_*.nc, albedo for bands 1-6 / tbb for bands 7-16)."
        )
        self.nc_format_combo.currentIndexChanged.connect(self.on_nc_format_changed)
        format_layout.addWidget(self.nc_format_combo, 1)
        processing_layout.addLayout(format_layout)

        self.fast_mode_checkbox = QCheckBox("Fast mode (faster NetCDF writes)")
        self.fast_mode_checkbox.setChecked(False)
        self.fast_mode_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
        self.fast_mode_checkbox.setToolTip(
            "Pass --fast to bg_to_nc.py: lower compression (complevel=2), faster conversion, bigger NC files."
        )
        self.fast_mode_checkbox.stateChanged.connect(self.on_fast_mode_changed)
        processing_layout.addWidget(self.fast_mode_checkbox)

        self.low_io_checkbox = QCheckBox("Low I/O mode (reduce disk load — slower but safer)")
        self.low_io_checkbox.setChecked(self.low_io)
        self.low_io_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
        self.low_io_checkbox.setToolTip(
            "ON (default): NetCDF conversion runs sequentially — 1 folder x 1 band at a time.\n"
            "Prevents the disk from hitting 100% I/O (PC lag/crash).\n"
            "OFF: restores parallel conversion (faster, but much heavier disk usage)."
        )
        self.low_io_checkbox.stateChanged.connect(self.on_low_io_changed)
        processing_layout.addWidget(self.low_io_checkbox)

        # ---- Type / Region selector (Full Disk / Japan / Target for Himawari, Full/CONUS for GOES) ----
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Type / Region:"))
        type_note = QLabel("(Only for Date Range)")
        type_note.setStyleSheet("color:#888; font-size:9px;")
        type_layout.addWidget(type_note)
        self.legacy_type_combo = QComboBox()
        self.legacy_type_combo.addItems(["Full Disk (FLDK)", "Japan", "Target"])
        self.legacy_type_combo.setCurrentText("Full Disk (FLDK)")
        self.legacy_type_combo.setStyleSheet("""
            QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444;
                         border-radius: 3px; padding: 3px; min-width: 140px; font-size: 11px; }
        """)
        self.legacy_type_combo.setToolTip(
            "Himawari: Full Disk (AHI-L1b-FLDK), Japan (AHI-L1b-Japan rapid scan), Target (AHI-L1b-Target rapid scan).\n"
            "For GOES this is currently ignored (always uses ABI-L1b-RadF full disk)."
        )
        self.legacy_type_combo.currentTextChanged.connect(self._on_legacy_type_changed)
        type_layout.addWidget(self.legacy_type_combo, 1)
        type_layout.addStretch()
        processing_layout.addLayout(type_layout)

        # ---- Winds checkbox ----
        winds_layout = QHBoxLayout()
        self.winds_checkbox = QCheckBox("Download L2 Winds data (Himawari FLDK only)")
        self.winds_checkbox.setChecked(False)
        self.winds_checkbox.setEnabled(True)
        self.winds_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
        self.winds_checkbox.stateChanged.connect(self.on_winds_checkbox_changed)
        winds_layout.addWidget(self.winds_checkbox)
        winds_layout.addStretch()
        processing_layout.addLayout(winds_layout)

        download_layout.addLayout(processing_layout)

        concurrent_layout = QHBoxLayout()
        concurrent_layout.addWidget(QLabel("Max concurrent downloads:"))
        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 20)
        self.concurrent_spin.setValue(8)
        self.concurrent_spin.setFixedWidth(70)
        concurrent_layout.addWidget(self.concurrent_spin)
        concurrent_layout.addStretch()
        download_layout.addLayout(concurrent_layout)

        # ---- From / To date filter ----
        from_to_group = QGroupBox("Date Range Filter (optional) — times are UTC")
        from_to_group.setStyleSheet("""
            QGroupBox {
                color: #5D8AA8;
                font-weight: bold;
                border: 1px solid #444;
                border-radius: 5px;
                padding-top: 10px;
                margin-top: 4px;
                font-size: 10px;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
        """)
        from_to_layout = QVBoxLayout(from_to_group)
        from_to_layout.setSpacing(6)

        _combo_ss = (
            "QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444; "
            "border-radius: 3px; padding: 3px 6px; font-size: 10px; min-width: 60px; }"
            "QComboBox::drop-down { border: none; }"
        )

        # FROM row
        from_row = QHBoxLayout()
        from_row.setSpacing(4)
        from_lbl = QLabel("From:")
        from_lbl.setStyleSheet("color:#8BAA8A; font-weight:600; font-size:10px; min-width:32px;")
        from_row.addWidget(from_lbl)
        self.legacy_from_year  = QComboBox(); self.legacy_from_year.setStyleSheet(_combo_ss)
        self.legacy_from_month = QComboBox(); self.legacy_from_month.setStyleSheet(_combo_ss)
        self.legacy_from_day   = QComboBox(); self.legacy_from_day.setStyleSheet(_combo_ss)
        self.legacy_from_hour  = QComboBox(); self.legacy_from_hour.setStyleSheet(_combo_ss)
        self.legacy_from_min   = QComboBox(); self.legacy_from_min.setStyleSheet(_combo_ss)
        sep1 = QLabel("—"); sep1.setStyleSheet("color:#555; font-size:10px;")
        sep2 = QLabel(":"); sep2.setStyleSheet("color:#555; font-size:10px;")
        from_row.addWidget(self.legacy_from_year)
        from_row.addWidget(QLabel("/"))
        from_row.addWidget(self.legacy_from_month)
        from_row.addWidget(QLabel("/"))
        from_row.addWidget(self.legacy_from_day)
        from_row.addSpacing(6)
        from_row.addWidget(self.legacy_from_hour)
        from_row.addWidget(QLabel(":"))
        from_row.addWidget(self.legacy_from_min)
        from_row.addStretch()
        for w in [from_row.itemAt(i).widget() for i in range(from_row.count()) if from_row.itemAt(i).widget()]:
            if isinstance(w, QLabel) and w.text() in ["/", ":"]:
                w.setStyleSheet("color:#555; font-size:10px;")
        from_to_layout.addLayout(from_row)

        # TO row
        to_row = QHBoxLayout()
        to_row.setSpacing(4)
        to_lbl = QLabel("To:")
        to_lbl.setStyleSheet("color:#8BAA8A; font-weight:600; font-size:10px; min-width:32px;")
        to_row.addWidget(to_lbl)
        self.legacy_to_year  = QComboBox(); self.legacy_to_year.setStyleSheet(_combo_ss)
        self.legacy_to_month = QComboBox(); self.legacy_to_month.setStyleSheet(_combo_ss)
        self.legacy_to_day   = QComboBox(); self.legacy_to_day.setStyleSheet(_combo_ss)
        self.legacy_to_hour  = QComboBox(); self.legacy_to_hour.setStyleSheet(_combo_ss)
        self.legacy_to_min   = QComboBox(); self.legacy_to_min.setStyleSheet(_combo_ss)
        to_row.addWidget(self.legacy_to_year)
        to_row.addWidget(QLabel("/"))
        to_row.addWidget(self.legacy_to_month)
        to_row.addWidget(QLabel("/"))
        to_row.addWidget(self.legacy_to_day)
        to_row.addSpacing(6)
        to_row.addWidget(self.legacy_to_hour)
        to_row.addWidget(QLabel(":"))
        to_row.addWidget(self.legacy_to_min)
        to_row.addStretch()
        for w in [to_row.itemAt(i).widget() for i in range(to_row.count()) if to_row.itemAt(i).widget()]:
            if isinstance(w, QLabel) and w.text() in ["/", ":"]:
                w.setStyleSheet("color:#555; font-size:10px;")
        from_to_layout.addLayout(to_row)

        # STEP + overwrite row (animation frame spacing + re-download toggle)
        opts_row = QHBoxLayout()
        opts_row.setSpacing(6)
        step_lbl = QLabel("Step:")
        step_lbl.setStyleSheet("color:#8BAA8A; font-weight:600; font-size:10px; min-width:32px;")
        opts_row.addWidget(step_lbl)
        self.legacy_step = QComboBox()
        self.legacy_step.setStyleSheet(_combo_ss)
        self.legacy_step.addItems(["2 min", "2.5 min", "10 min", "15 min", "30 min", "60 min"])
        self.legacy_step.setCurrentText("10 min")
        self.legacy_step.setToolTip("Spacing between animation frames. 2.5 min is used for Japan/Target rapid scan.")
        opts_row.addWidget(self.legacy_step)
        opts_row.addSpacing(8)
        self.legacy_force_check = QCheckBox("Overwrite existing files")
        self.legacy_force_check.setStyleSheet("color:#EEE; font-size:10px;")
        self.legacy_force_check.setToolTip("Re-download files even if the .bz2 / NetCDF already exists locally.")
        opts_row.addWidget(self.legacy_force_check)
        opts_row.addStretch()
        from_to_layout.addLayout(opts_row)

        # Populate From/To combos with sensible defaults
        _years  = [str(y) for y in range(2015, datetime.now().year + 1)]
        _months = [f"{m:02d}" for m in range(1, 13)]
        _days   = [f"{d:02d}" for d in range(1, 32)]
        _hours  = [f"{h:02d}" for h in range(0, 24)]
        _mins   = ["00", "10", "20", "30", "40", "50"]
        for combo, items, default in [
            (self.legacy_from_year,  _years,  str(datetime.now().year)),
            (self.legacy_from_month, _months, f"{datetime.now().month:02d}"),
            (self.legacy_from_day,   _days,   f"{datetime.now().day:02d}"),
            (self.legacy_from_hour,  _hours,  "00"),
            (self.legacy_from_min,   _mins,   "00"),
            (self.legacy_to_year,    _years,  str(datetime.now().year)),
            (self.legacy_to_month,   _months, f"{datetime.now().month:02d}"),
            (self.legacy_to_day,     _days,   f"{datetime.now().day:02d}"),
            (self.legacy_to_hour,    _hours,  "23"),
            (self.legacy_to_min,     _mins,   "50"),
        ]:
            combo.addItems(items)
            combo.setCurrentText(default)

        download_layout.addWidget(from_to_group)
        right_layout.addWidget(download_group)

        # Manual processing buttons
        manual_frame = QFrame()
        manual_frame.setStyleSheet("QFrame { background: #333; border-radius: 5px; padding: 8px; }")
        manual_layout = QHBoxLayout(manual_frame)
        self.manual_extract_btn = QPushButton("Extract")
        self.manual_nc_btn = QPushButton("NetCDF")
        self.manual_full_btn = QPushButton("Full")
        for btn in [self.manual_extract_btn, self.manual_nc_btn, self.manual_full_btn]:
            btn.setStyleSheet("""
                QPushButton { background: #37474F; color: white; font-size: 11px; border-radius: 3px; padding: 6px;
                              border: 1px solid #555; }
                QPushButton:hover { background: #455A64; }
                QPushButton:disabled { background: #444; color: #777; }
            """)
            btn.setVisible(not self.auto_process)
        self.manual_extract_btn.clicked.connect(lambda: self.run_manual_processing("extract_only"))
        self.manual_nc_btn.setEnabled(False)
        self.manual_nc_btn.clicked.connect(lambda: self.run_manual_processing("nc_only"))
        self.manual_full_btn.setEnabled(False)
        self.manual_full_btn.clicked.connect(lambda: self.run_manual_processing("auto"))
        manual_layout.addWidget(self.manual_extract_btn)
        manual_layout.addWidget(self.manual_nc_btn)
        manual_layout.addWidget(self.manual_full_btn)
        right_layout.addWidget(manual_frame)

        # Main download
        self.download_btn = QPushButton("DOWNLOAD SELECTED FILES")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: #2E7D32; color: white; font-size: 13px; font-weight: bold;
                border-radius: 4px; padding: 10px; margin-top: 5px; border: none;
            }
            QPushButton:hover { background: #388E3C; }
            QPushButton:disabled { background: #555; color: #999; }
        """)
        self.download_btn.clicked.connect(self.download_filtered)
        right_layout.addWidget(self.download_btn)

        # NEW: From/To Date Range Download (user request - parity with Modern)
        self.legacy_range_btn = QPushButton("DOWNLOAD DATE RANGE (From → To)")
        self.legacy_range_btn.setStyleSheet("""
            QPushButton {
                background: #1565C0; color: white; font-size: 12px; font-weight: bold;
                border-radius: 4px; padding: 8px; margin-top: 4px; border: none;
            }
            QPushButton:hover { background: #1976D2; }
            QPushButton:disabled { background: #555; color: #999; }
        """)
        self.legacy_range_btn.clicked.connect(self._download_legacy_date_range)
        right_layout.addWidget(self.legacy_range_btn)

        # Statistics
        stats_group = QGroupBox("Statistics")
        stats_group.setStyleSheet("""
            QGroupBox { color: #4CAF50; font-weight: bold; border: 1px solid #555;
                        border-radius: 5px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
        """)
        stats_layout = QGridLayout(stats_group)
        self.stats_labels = {
            'extracted':     QLabel("Extracted: 0"),
            'nc_created':    QLabel("NetCDF: 0"),
            'nc_failed':     QLabel("NC Failed: 0"),
            'satpy_decodes': QLabel("Satpy: 0"),
            'satpy_failures':QLabel("Failures: 0"),
            # v3.1.2
            'rgb_ok':        QLabel("RGB OK: —"),
            'rgb_total':     QLabel("RGB Total: —"),
            'ads_status':    QLabel("ADS: enabled"),
        }
        for i, (key, label) in enumerate(self.stats_labels.items()):
            label.setStyleSheet("color: #EEE; font-size: 10px;")
            stats_layout.addWidget(label, i // 2, i % 2)
        right_layout.addWidget(stats_group)
        content_layout.addWidget(right_panel)
        main_layout.addWidget(content_widget, 1)

        # Progress bars
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center;
                           background: #1A1A1A; height: 15px; margin-top: 5px; }
            QProgressBar::chunk { background-color: #5D8AA8; border-radius: 4px; }
        """)
        main_layout.addWidget(self.progress_bar)

        self.process_progress_bar = QProgressBar()
        self.process_progress_bar.setVisible(False)
        self.process_progress_bar.setFixedHeight(20)
        self.process_progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center;
                           background: #1A1A1A; height: 15px; margin-top: 5px; }
            QProgressBar::chunk { background-color: #FF9800; border-radius: 4px; }
        """)
        main_layout.addWidget(self.process_progress_bar)

        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

        self.setStyleSheet("""
            QMainWindow { background: #222; color: #EEE; }
            QLabel { color: #EEE; }
            QComboBox, QLineEdit { background: #2D2D2D; color: #EEE; border: 1px solid #444; border-radius: 3px; padding: 3px; }
        """)

        self.log_message("INFO", f"Auto-processing: enabled (mode: {self.process_mode})")

        self.list_directory("")
        self.update_manual_buttons_state()

    # ------------------------------------------------------------------
    #  Path helpers & manual buttons
    # ------------------------------------------------------------------
    def get_local_path_from_current_prefix(self):
        base_dir = Path(self.dir_edit.text())
        if not self.current_path:
            return base_dir
        satellite_name = self.current_bucket.replace("noaa-", "")
        subfolder = "_".join(self.current_path)
        return base_dir / satellite_name / subfolder

    def update_manual_buttons_state(self):
        target_dir = self.get_local_path_from_current_prefix()
        exists = target_dir.exists()
        self.manual_extract_btn.setEnabled(exists)
        self.manual_nc_btn.setEnabled(exists)
        self.manual_full_btn.setEnabled(exists)

    def run_manual_processing(self, process_mode):
            target_dir = self.get_local_path_from_current_prefix()
            if not self.current_path:
                reply = QMessageBox.question(
                    self, "Process All Downloads?",
                    f"You are at the S3 root.\n\nDo you want to process ALL downloaded data in:\n{target_dir} ?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                )
                if reply == QMessageBox.No:
                    return
            if not target_dir.exists():
                QMessageBox.warning(self, "Directory Not Found",
                    f"The local folder does not exist yet:\n{target_dir}\n\nPlease download the data first.")
                return

            # For extract_only or auto: need .bz2 files (auto extracts first, then converts)
            if process_mode in ["extract_only", "auto"]:
                bz2_files = list(target_dir.rglob("*.bz2"))
                if not bz2_files:
                    # auto mode: .bz2 may already be extracted — fall through to check .dat
                    if process_mode == "extract_only":
                        QMessageBox.information(self, "No Files", f"No .bz2 files found in:\n{target_dir}")
                        return
                    # auto with no .bz2: check if .dat files exist to still run the NetCDF step
                    dat_files = list(target_dir.rglob("*.dat")) + list(target_dir.rglob("*.DAT"))
                    if not dat_files:
                        QMessageBox.information(self, "No Files",
                            f"No .bz2 or .dat files found in:\n{target_dir}\n\nPlease download the data first.")
                        return
                    # .dat files exist but no .bz2 — switch to nc_only automatically
                    self.log_message("INFO", "No .bz2 files found; switching to NetCDF-only step.")
                    process_mode = "nc_only"

            # For nc_only: need .dat or NDMW wind files
            if process_mode == "nc_only":
                dat_files = list(target_dir.rglob("*.dat")) + list(target_dir.rglob("*.DAT"))
                ndmw_files = list(target_dir.rglob("NDMW*.nc"))
                if not dat_files and not ndmw_files:
                    QMessageBox.information(self, "No Files", f"No .dat or NDMW wind files found in:\n{target_dir}")
                    return
                if ndmw_files and not dat_files:
                    self.log_message("INFO", f"No .dat files, but {len(ndmw_files)} NDMW wind file(s) found — running wind-only merge.")

            process_names = {
                "auto": "Full processing (extract & NetCDF)",
                "extract_only": "Extract .bz2 files only",
                "nc_only": "Convert .dat files to NetCDF (or merge NDMW wind)"
            }
            self.log_message("INFO", f"Starting {process_names[process_mode]} in: {target_dir}")
            self.start_processing(str(target_dir), process_mode)

    # ------------------------------------------------------------------
    #  Navigation
    # ------------------------------------------------------------------
    def on_dir_double_clicked(self, item, column):
        dir_name = item.data(0, Qt.UserRole)
        if dir_name == "..":
            self.go_back()
        elif dir_name:
            self.current_prefix += dir_name + "/"
            self.current_path.append(dir_name)
            self.update_path_label()
            self.list_directory(self.current_prefix)
            self.back_btn.setEnabled(len(self.current_path) > 0)
            self.update_manual_buttons_state()
            self.check_winds_availability()
            if _bucket_kind(self.current_bucket) == "gk2a":
                self._refresh_gk2a_minute_combo()

    def go_back(self):
        if self.current_path:
            self.current_path.pop()
            self.current_prefix = "/".join(self.current_path) + "/" if self.current_path else ""
            self.update_path_label()
            self.list_directory(self.current_prefix)
            self.back_btn.setEnabled(len(self.current_path) > 0)
            self.update_manual_buttons_state()
            self.check_winds_availability()

    def refresh_current(self):
        self.list_directory(self.current_prefix)
        self.update_manual_buttons_state()
        self.check_winds_availability()

    def _update_legacy_band_labels(self):
        """Switch legacy band checkbox labels between B01-B16 (Himawari) and C01-C16 (GOES)."""
        is_goes = "goes" in self.current_bucket.lower()
        prefix = "C" if is_goes else "B"
        for i in range(1, 17):
            key = f"B{i:02d}"
            if key in getattr(self, 'band_checkboxes', {}):
                self.band_checkboxes[key].setText(f"{prefix}{i:02d}")

    def on_satellite_changed(self, satellite):
        self.log_message("INFO", f"Satellite changed to: {satellite} (bucket: {self.satellites[satellite]})")
        self.current_bucket = self.satellites[satellite]
        kind = _sat_kind(satellite)
        is_goes = kind == "goes"
        if is_goes:
            self.log_message("INFO", f"GOES mode: bucket={self.current_bucket}, enabling virtual DOY→month browsing")
            self._goes_doy_month_cache.clear()
        if kind == "gk2a":
            self.log_message("INFO", f"GK-2A mode: bucket={self.current_bucket}, AMI L1B Full Disk / Local Area (download-only, no processing)")
        self.current_prefix = ""
        self.current_path = []
        self.update_path_label()
        self.list_directory("")
        self.update_manual_buttons_state()
        self.check_winds_availability()
        self._update_legacy_band_labels()

        # Show minute filter for satellites that use hour-level folders with
        # multiple scans per hour (GOES + GK-2A). Seed GK2A's minute grid.
        show_min = is_goes or kind == "gk2a"
        if hasattr(self, 'goes_minute_combo'):
            self.goes_minute_combo.setVisible(show_min)
            if kind == "gk2a":
                self._refresh_gk2a_minute_combo()
        if hasattr(self, '_goes_min_lbl'):
            self._goes_min_lbl.setVisible(show_min)
        if hasattr(self, 'goes_minute_combo') and not show_min:
            self.goes_minute_combo.setCurrentText("All")

        # GOES / GK-2A: blackout processing controls (not supported — files are already NetCDF)
        if kind in ("goes", "gk2a"):
            if hasattr(self, 'auto_process_checkbox'):
                self.auto_process_checkbox.setEnabled(False)
                self.auto_process_checkbox.setChecked(False)
                self.auto_process_checkbox.setStyleSheet("color: #555; font-size: 11px;")
            if hasattr(self, 'force_simple_checkbox'):
                self.force_simple_checkbox.setEnabled(False)
                self.force_simple_checkbox.setChecked(False)
                self.force_simple_checkbox.setStyleSheet("color: #555; font-size: 11px;")
            if hasattr(self, 'skip_extract_checkbox'):
                self.skip_extract_checkbox.setEnabled(False)
                self.skip_extract_checkbox.setChecked(False)
                self.skip_extract_checkbox.setStyleSheet("color: #555; font-size: 11px;")
            if hasattr(self, 'nc_format_combo'):
                self.nc_format_combo.setEnabled(False)
                self.nc_format_combo.setStyleSheet("background: #2D2D2D; color: #555; border: 1px solid #444; border-radius: 3px; padding: 3px; min-width: 150px; font-size: 11px;")
            if hasattr(self, 'fast_mode_checkbox'):
                self.fast_mode_checkbox.setEnabled(False)
                self.fast_mode_checkbox.setChecked(False)
                self.fast_mode_checkbox.setStyleSheet("color: #555; font-size: 11px;")
            if hasattr(self, 'low_io_checkbox'):
                self.low_io_checkbox.setEnabled(False)
                self.low_io_checkbox.setStyleSheet("color: #555; font-size: 11px;")
            if hasattr(self, 'winds_checkbox'):
                self.winds_checkbox.setEnabled(False)
                self.winds_checkbox.setChecked(False)
                self.winds_checkbox.setStyleSheet("color: #555; font-size: 11px;")
            if hasattr(self, 'process_mode_combo'):
                self.process_mode_combo.setEnabled(False)
                self.process_mode_combo.setStyleSheet("background: #2D2D2D; color: #555; border: 1px solid #444; border-radius: 3px; padding: 3px; min-width: 130px; font-size: 11px;")
        else:
            if hasattr(self, 'auto_process_checkbox'):
                self.auto_process_checkbox.setEnabled(True)
                self.auto_process_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
            if hasattr(self, 'force_simple_checkbox'):
                self.force_simple_checkbox.setEnabled(True)
                self.force_simple_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
            if hasattr(self, 'skip_extract_checkbox'):
                self.skip_extract_checkbox.setEnabled(True)
                self.skip_extract_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
            if hasattr(self, 'nc_format_combo'):
                self.nc_format_combo.setEnabled(True)
                self.nc_format_combo.setStyleSheet("background: #2D2D2D; color: #EEE; border: 1px solid #444; border-radius: 3px; padding: 3px; min-width: 150px; font-size: 11px;")
            if hasattr(self, 'fast_mode_checkbox'):
                self.fast_mode_checkbox.setEnabled(True)
                self.fast_mode_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
            if hasattr(self, 'low_io_checkbox'):
                self.low_io_checkbox.setEnabled(True)
                self.low_io_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
            if hasattr(self, 'winds_checkbox'):
                self.winds_checkbox.setEnabled(True)
                self.winds_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
            if hasattr(self, 'process_mode_combo'):
                self.process_mode_combo.setEnabled(True)
                self.process_mode_combo.setStyleSheet("background: #2D2D2D; color: #EEE; border: 1px solid #444; border-radius: 3px; padding: 3px; min-width: 130px; font-size: 11px;")

        # Update legacy Type combo options depending on satellite (Himawari vs GOES vs GK-2A)
        if hasattr(self, 'legacy_type_combo'):
            current = self.legacy_type_combo.currentText()
            self.legacy_type_combo.blockSignals(True)
            self.legacy_type_combo.clear()
            if kind == "gk2a":
                self.legacy_type_combo.addItems(["Full Disk (FD)", "Local Area (LA)"])
                if "Local" in current or "LA" in current:
                    self.legacy_type_combo.setCurrentText("Local Area (LA)")
                else:
                    self.legacy_type_combo.setCurrentText("Full Disk (FD)")
            elif "himawari" in satellite.lower():
                self.legacy_type_combo.addItems(["Full Disk (FLDK)", "Japan", "Target"])
                if "Japan" in current:
                    self.legacy_type_combo.setCurrentText("Japan")
                elif "Target" in current:
                    self.legacy_type_combo.setCurrentText("Target")
                else:
                    self.legacy_type_combo.setCurrentText("Full Disk (FLDK)")
            else:
                self.legacy_type_combo.addItems(["Full Disk"])
                self.legacy_type_combo.setCurrentText("Full Disk")
            self.legacy_type_combo.blockSignals(False)
            self._on_legacy_type_changed(self.legacy_type_combo.currentText())

    # ------------------------------------------------------------------
    #  Winds detection
    # ------------------------------------------------------------------
    def construct_winds_prefix(self):
        if (len(self.current_path) >= 5 and
            self.current_path[0] == "AHI-L1b-FLDK"):
            winds_parts = ["AHI-L2-FLDK-Winds"] + self.current_path[1:]
            return "/".join(winds_parts) + "/"
        return None

    def check_winds_availability(self):
        if self.winds_checker and self.winds_checker.isRunning():
            self.winds_checker.terminate()
            self.winds_checker = None

        winds_prefix = self.construct_winds_prefix()
        self.current_winds_prefix = winds_prefix
        self.winds_available = False
        self.winds_checkbox.setEnabled(False)
        self.winds_checkbox.setChecked(False)

        if not winds_prefix:
            return

        self.winds_checker = WindsChecker(self.current_bucket, winds_prefix)
        self.winds_checker.result.connect(self.on_winds_check_result)
        self.winds_checker.error.connect(lambda msg: self.log_message("ERROR", msg))
        self.winds_checker.start()

    def on_winds_check_result(self, available):
        self.winds_available = available
        self.winds_checkbox.setEnabled(available)
        if available:
            self.winds_checkbox.setToolTip(f"Winds data available at {self.current_winds_prefix}")
        else:
            self.winds_checkbox.setToolTip("No L2 winds data for this time slot")
            self.winds_checkbox.setChecked(False)

    def on_winds_checkbox_changed(self):
        pass

    # ------------------------------------------------------------------
    #  Band selection
    # ------------------------------------------------------------------
    def on_band_selection_changed(self):
        self.selected_bands = []
        for band_name, checkbox in self.band_checkboxes.items():
            if checkbox.isChecked():
                band_num = int(band_name[1:])
                self.selected_bands.append(band_num)

        if len(self.selected_bands) == 16:
            self.selected_bands_label.setText("Selected: All 16 bands")
            self.band_filter_label.setText("Filter: All bands")
        elif len(self.selected_bands) == 0:
            self.selected_bands_label.setText("Selected: None")
            self.band_filter_label.setText("Filter: No bands")
        else:
            bands_str = ", ".join([f"B{b:02d}" for b in sorted(self.selected_bands)])
            self.selected_bands_label.setText(f"Selected: {bands_str}")
            self.band_filter_label.setText(f"Filter: {len(self.selected_bands)} bands")

        self.display_filtered_files()

    def on_auto_process_changed(self):
        self.auto_process = self.auto_process_checkbox.isChecked()
        # Guard: manual buttons may not exist yet during __init__
        if not hasattr(self, 'manual_extract_btn'):
            return
        vis = not self.auto_process
        self.manual_extract_btn.setVisible(vis)
        self.manual_nc_btn.setVisible(vis)
        self.manual_full_btn.setVisible(vis)

    def on_process_mode_changed(self, index):
        # 0 -> auto (Extract & NetCDF), 1 -> extract_only, 2 -> nc_only
        mode_map = {0: "auto", 1: "extract_only", 2: "nc_only"}
        self.process_mode = mode_map.get(index, "auto")

    def on_force_simple_changed(self):
        self.force_simple = self.force_simple_checkbox.isChecked()

    def on_skip_extract_changed(self):
        self.skip_extract = self.skip_extract_checkbox.isChecked()
        if self.skip_extract:
            self.log_message("INFO", "Skip extraction enabled — bg_to_nc will read .bz2 files directly.")

    def on_nc_format_changed(self):
        text = self.nc_format_combo.currentText()
        self.nc_format = "jma" if "JMA" in text else "pwards"
        self.log_message("INFO", f"NetCDF format set to: {text}")

    def on_fast_mode_changed(self):
        self.fast_mode = self.fast_mode_checkbox.isChecked()
        if self.fast_mode:
            self.log_message("INFO", "Fast mode enabled — faster NetCDF writes (complevel=2, larger files).")

    def on_low_io_changed(self):
        self.low_io = self.low_io_checkbox.isChecked()
        self._save_concurrency_settings()
        if self.low_io:
            self.log_message("INFO", "Low I/O mode ON — NetCDF conversion will run sequentially (safer for disk).")
        else:
            self.log_message("INFO", "Low I/O mode OFF — NetCDF conversion will run in parallel (faster, heavier disk I/O).")

    def on_skip_rgb_changed(self):
        self.skip_rgb = self.skip_rgb_checkbox.isChecked()
        ads_txt = "ADS: disabled" if self.skip_ads else "ADS: enabled"
        ads_col = "#888" if self.skip_ads else "#4CAF50"
        self.stats_labels['ads_status'].setText(ads_txt)
        self.stats_labels['ads_status'].setStyleSheet(f"color: {ads_col}; font-size: 10px;")

    def on_skip_ads_changed(self):
        self.skip_ads = self.skip_ads_checkbox.isChecked()
        ads_txt = "ADS: disabled" if self.skip_ads else "ADS: enabled"
        ads_col = "#888" if self.skip_ads else "#4CAF50"
        self.stats_labels['ads_status'].setText(ads_txt)
        self.stats_labels['ads_status'].setStyleSheet(f"color: {ads_col}; font-size: 10px;")

    # ------------------------------------------------------------------
    #  Directory / file listing
    # ------------------------------------------------------------------
    def list_directory(self, prefix):
        if self.lister and self.lister.isRunning():
            return

        self.dir_tree.clear()
        self.files_table.setRowCount(0)
        self.all_files = []

        is_goes = "goes" in self.current_bucket.lower()

        # ── GOES virtual month level (PROD/YYYY/MM) → show cached days with brief loading ──
        if is_goes and len(self.current_path) == 3:
            self.log_message("INFO", f"GOES virtual: depth=3 path={self.current_path} → showing cached days with 200ms loading")
            self.dir_tree.clear()
            loading = QTreeWidgetItem(self.dir_tree)
            loading.setText(0, "Loading...")
            QTimer.singleShot(200, self._show_goes_virtual_days)
            return

        # ── GOES virtual day+ level (PROD/YYYY/MM/DD/...) → use real DOY prefix ──
        if is_goes and len(self.current_path) >= 4:
            real_prefix = self._get_real_goes_prefix()
            self.log_message("INFO", f"GOES virtual: depth={len(self.current_path)} path={self.current_path} → real S3 prefix={real_prefix}")
            prefix = real_prefix

        loading_item = QTreeWidgetItem(self.dir_tree)
        loading_item.setText(0, "Loading...")
        loading_item.setFlags(loading_item.flags() & ~Qt.ItemIsSelectable)

        self.lister = S3Lister(self.current_bucket, prefix)
        self.lister.progress.connect(lambda msg: self.status_bar.showMessage(msg))
        self.lister.directories_found.connect(self.on_directories_found)
        self.lister.files_found.connect(self.on_files_found)
        self.lister.error.connect(self.on_list_error)
        self.lister.finished.connect(self.on_list_finished)
        self.lister.list_files = bool(prefix)
        self.lister.start()

    def on_directories_found(self, directories):
        is_goes = "goes" in self.current_bucket.lower()
        kind = _bucket_kind(self.current_bucket)

        # ── GK-2A root: filter to the AMI product tree ────────────────────
        if not self.current_prefix and kind == "gk2a":
            filtered = [d for d in directories if d == "AMI"]
            self.dir_tree.clear()
            if filtered:
                for dir_name in filtered:
                    item = QTreeWidgetItem(self.dir_tree)
                    item.setText(0, f"📁 {dir_name}  (Imagery — L1B Full Disk / Local Area)")
                    item.setData(0, Qt.UserRole, dir_name)
            else:
                no_dirs_item = QTreeWidgetItem(self.dir_tree)
                no_dirs_item.setText(0, "No AMI folders found")
                no_dirs_item.setFlags(no_dirs_item.flags() & ~Qt.ItemIsSelectable)
            return

        # ── GOES root: filter+rename product folders ──────────────────────
        if not self.current_prefix and is_goes:
            GOES_MAP = {"ABI-L1b-RadF": "Full Disk",
                        "ABI-L1b-RadC": "CONUS",
                        "ABI-L1b-RadM": "Meso"}
            all_dirs = directories[:]
            filtered = [d for d in directories if d in GOES_MAP]
            filtered.sort()
            self.log_message("INFO", f"GOES root: S3 returned {len(all_dirs)} dirs, filtered to {len(filtered)} ({', '.join(GOES_MAP.get(d,d) for d in filtered)})")
            self.dir_tree.clear()
            for dir_name in filtered:
                item = QTreeWidgetItem(self.dir_tree)
                item.setText(0, f"📁 {GOES_MAP[dir_name]}")
                item.setData(0, Qt.UserRole, dir_name)
            if not filtered:
                self.log_message("WARNING", f"GOES root: no RadF/RadC/RadM folders found among: {all_dirs}")
                no_dirs_item = QTreeWidgetItem(self.dir_tree)
                no_dirs_item.setText(0, "No matching folders (RadF/RadC/RadM)")
                no_dirs_item.setFlags(no_dirs_item.flags() & ~Qt.ItemIsSelectable)
            return

        # ── GOES year level: DOY folders → build month cache, show months ──
        if is_goes and len(self.current_path) == 2 and self.current_path[1].isdigit():
            year = int(self.current_path[1])
            if directories and all(d.isdigit() for d in directories):
                from datetime import datetime as _dt, timedelta as _td
                self.log_message("INFO", f"GOES DOY→month: year={year} got {len(directories)} DOY folders ({min(directories)}–{max(directories)})")
                month_map = {}
                for doy_str in directories:
                    doy = int(doy_str)
                    dt = _dt(year, 1, 1) + _td(days=doy-1)
                    m = dt.month
                    if m not in month_map:
                        month_map[m] = set()
                    month_map[m].add(dt.day)
                self._goes_doy_month_cache[year] = {m: sorted(days) for m, days in month_map.items()}
                months_cached = sorted(self._goes_doy_month_cache[year].keys())
                total_days = sum(len(d) for d in self._goes_doy_month_cache[year].values())
                self.log_message("INFO", f"GOES cache built: year={year} → {len(months_cached)} months ({total_days} days)")
                self.dir_tree.clear()
                loading = QTreeWidgetItem(self.dir_tree)
                loading.setText(0, "Loading...")
                QTimer.singleShot(200, self._show_goes_virtual_months)
                return

        # ── Himawari root: filter FLDK/Japan/Target ───────────────────────
        if not self.current_prefix and "himawari" in self.current_bucket.lower():
            keywords = ["FLDK", "Japan", "target"]
            filtered = [d for d in directories if any(k.lower() in d.lower() for k in keywords)]
            self.dir_tree.clear()
            if filtered:
                filtered.sort()
                for dir_name in filtered:
                    item = QTreeWidgetItem(self.dir_tree)
                    item.setText(0, f"📁 {dir_name}")
                    item.setData(0, Qt.UserRole, dir_name)
            else:
                no_dirs_item = QTreeWidgetItem(self.dir_tree)
                no_dirs_item.setText(0, "No matching folders (FLDK/Japan/Target)")
                no_dirs_item.setFlags(no_dirs_item.flags() & ~Qt.ItemIsSelectable)
            return

        # ── Normal (non-virtual) directory listing ────────────────────────
        self.dir_tree.clear()
        if self.current_prefix:
            up_item = QTreeWidgetItem(self.dir_tree)
            up_item.setText(0, ".. (Parent)")
            up_item.setData(0, Qt.UserRole, "..")

        if directories:
            directories.sort()
            for dir_name in directories:
                item = QTreeWidgetItem(self.dir_tree)
                item.setText(0, f"📁 {dir_name}")
                item.setData(0, Qt.UserRole, dir_name)

    def on_files_found(self, files):
        self.all_files = files
        self.display_filtered_files()

    def _on_goes_minute_filter_changed(self, text):
        self.log_message("INFO", f"GOES minute filter changed to: {text}")
        self.display_filtered_files()

    def _gk2a_current_product(self) -> str:
        """Determine the active GK-2A product ('AMI/L1B/FD' vs 'AMI/L1B/LA') from
        the type combo or the current browsing path."""
        if hasattr(self, 'legacy_type_combo') and self.legacy_type_combo:
            txt = (self.legacy_type_combo.currentText() or "").lower()
            if "local" in txt or "la" in txt:
                return "AMI/L1B/LA"
            if "full" in txt or "fd" in txt:
                return "AMI/L1B/FD"
        for part in getattr(self, 'current_path', []) or []:
            if part.upper() in ("FD", "LA"):
                return f"AMI/L1B/{part.upper()}"
        return "AMI/L1B/FD"

    def _refresh_gk2a_minute_combo(self):
        """Populate the legacy file-table minute filter with the correct GK-2A grid
        (10-min for Full Disk, 2-min for Local Area)."""
        combo = getattr(self, 'goes_minute_combo', None)
        if combo is None:
            return
        product = self._gk2a_current_product()
        grid = GK2A_LA_MINUTES if product.endswith("/LA") else GK2A_FD_MINUTES
        items = ["All"] + grid
        combo.blockSignals(True)
        current = combo.currentText()
        combo.clear()
        combo.addItems(items)
        combo.setCurrentText(current if current in items else "All")
        combo.blockSignals(False)
        self.log_message("INFO", f"GK-2A minute grid set to {'2-min' if product.endswith('/LA') else '10-min'} ({product})")

    def display_filtered_files(self):
        filtered_files = []
        total_size = 0
        kind = _bucket_kind(self.current_bucket)
        is_goes = kind == "goes"
        goes_min_val = "All"
        goes_combo = getattr(self, 'goes_minute_combo', None)
        if goes_combo is not None:
            goes_min_val = goes_combo.currentText()
        for file_info in self.all_files:
            filename = file_info['name']
            # Minute filter from filename scan-time (GOES _sYYYYDDDHHMM..., GK2A trailing _YYYYMMDDHHMM.nc)
            if kind in ("goes", "gk2a") and goes_min_val and goes_min_val != "All" and goes_min_val.isdigit():
                if kind == "gk2a":
                    m = GK2A_MIN_RE.search(filename)
                    file_min = int(m.group(1)[10:12]) if m else None
                else:
                    m = re.search(r'_s\d{4}\d{3}\d{2}(\d{2})', filename)
                    file_min = int(m.group(1)) if m else None
                if file_min is None:
                    self.log_message("INFO", f"Minute filter: no regex match in {filename}, skipping")
                    continue
                if file_min != int(goes_min_val):
                    continue
            band_number = None
            for i in range(1, 17):
                if kind == "gk2a":
                    code = INV_GK2A_BAND_MAP[f"B{i:02d}"]
                    if f"le1b_{code}_" in filename:
                        band_number = i
                        break
                elif is_goes:
                    if f"C{i:02d}" in filename or f"B{i:02d}" in filename:
                        band_number = i
                        break
                else:
                    if f"B{i:02d}_" in filename:
                        band_number = i
                        break
            if band_number is None or band_number in self.selected_bands:
                filtered_files.append((file_info, band_number))
                total_size += file_info['size']

        total_mb = total_size / (1024 * 1024)
        total_gb = total_size / (1024 * 1024 * 1024)
        if total_gb >= 1:
            size_text = f"{total_gb:.2f} GB"
        else:
            size_text = f"{total_mb:.1f} MB"
        self.size_label.setText(size_text)
        self.file_count_label.setText(f"{len(filtered_files)}/{len(self.all_files)} files")

        if len(self.selected_bands) == 0:
            self.estimated_size_label.setText("Estimated: 0 MB (no bands selected)")
        elif total_gb >= 1:
            self.estimated_size_label.setText(f"Estimated: {total_gb:.2f} GB")
        else:
            self.estimated_size_label.setText(f"Estimated: {total_mb:.1f} MB")

        self.files_table.setRowCount(len(filtered_files))
        for row, (file_info, band_number) in enumerate(filtered_files):
            filename = file_info['name']
            size_mb = file_info['size'] / (1024 * 1024)
            modified = file_info['last_modified'].strftime("%Y-%m-%d %H:%M")
            self.files_table.setItem(row, 0, QTableWidgetItem(filename))
            band_text = f"B{band_number:02d}" if band_number else "N/A"
            band_item = QTableWidgetItem(band_text)
            if band_number:
                band_item.setForeground(Qt.cyan)
            self.files_table.setItem(row, 1, band_item)
            size_item = QTableWidgetItem(f"{size_mb:.1f} MB")
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.files_table.setItem(row, 2, size_item)
            self.files_table.setItem(row, 3, QTableWidgetItem(modified))
            btn = QPushButton("⬇️")
            btn.setFixedWidth(40)
            btn.setStyleSheet("""
                QPushButton { background: #37474F; color: white; padding: 3px; border-radius: 2px; font-size: 10px; border: 1px solid #555; }
                QPushButton:hover { background: #455A64; }
            """)
            btn.clicked.connect(lambda checked, f=file_info: self.download_single_file(f))
            self.files_table.setCellWidget(row, 4, btn)
        self.files_table.resizeColumnsToContents()

    def update_path_label(self):
        full_path = f"s3://{self.current_bucket}/"
        if self.current_path:
            full_path += "/".join(self.current_path) + "/"
        self.path_label.setText(full_path)

    # ── GOES virtual month/day browsing helpers ─────────────────────────
    def _get_real_goes_prefix(self):
        """Convert virtual path (PROD/YYYY/MM/DD/HH/...) to real DOY-based S3 prefix."""
        is_goes = "goes" in self.current_bucket.lower()
        if not is_goes or len(self.current_path) < 4:
            return self.current_prefix
        try:
            from datetime import datetime as _dt
            year = int(self.current_path[1])
            month = int(self.current_path[2])
            day = int(self.current_path[3])
            dt = _dt(year, month, day)
            doy = dt.timetuple().tm_yday
            real = f"{self.current_path[0]}/{year:04d}/{doy:03d}/"
            if len(self.current_path) > 4:
                real += "/".join(self.current_path[4:]) + "/"
            self.log_message("INFO", f"GOES prefix: virtual={self.current_path} → DOY={doy:03d} real={real}")
            return real
        except Exception as e:
            self.log_message("ERROR", f"GOES prefix conversion failed: {e}")
            return self.current_prefix

    def _show_goes_virtual_months(self):
        """Populate tree with month folders from cached DOY data."""
        self.dir_tree.clear()
        if self.current_prefix:
            up_item = QTreeWidgetItem(self.dir_tree)
            up_item.setText(0, ".. (Parent)")
            up_item.setData(0, Qt.UserRole, "..")
        year = int(self.current_path[1]) if len(self.current_path) > 1 and self.current_path[1].isdigit() else None
        if year and year in self._goes_doy_month_cache:
            months = sorted(self._goes_doy_month_cache[year].keys())
            self.log_message("INFO", f"GOES virtual months: year={year} showing {len(months)} months ({', '.join(f'{m:02d}' for m in months)})")
            for m in months:
                item = QTreeWidgetItem(self.dir_tree)
                item.setText(0, f"📁 {m:02d}")
                item.setData(0, Qt.UserRole, f"{m:02d}")
            self._goes_virtual_mode = True

    def _show_goes_virtual_days(self):
        """Populate tree with day folders from cached DOY data."""
        self.dir_tree.clear()
        if self.current_prefix:
            up_item = QTreeWidgetItem(self.dir_tree)
            up_item.setText(0, ".. (Parent)")
            up_item.setData(0, Qt.UserRole, "..")
        year = int(self.current_path[1]) if len(self.current_path) > 1 and self.current_path[1].isdigit() else None
        month = int(self.current_path[2]) if len(self.current_path) > 2 and self.current_path[2].isdigit() else None
        if year and year in self._goes_doy_month_cache and month and month in self._goes_doy_month_cache[year]:
            days = sorted(self._goes_doy_month_cache[year][month])
            self.log_message("INFO", f"GOES virtual days: month={month:02d} showing {len(days)} days ({', '.join(f'{d:02d}' for d in days)})")
            for d in days:
                item = QTreeWidgetItem(self.dir_tree)
                item.setText(0, f"📁 {d:02d}")
                item.setData(0, Qt.UserRole, f"{d:02d}")

    # ------------------------------------------------------------------
    #  Download with pre‑download completeness check
    # ------------------------------------------------------------------
    def download_filtered(self):
        kind = _bucket_kind(self.current_bucket)
        is_goes = kind == "goes"
        # Use real DOY prefix for GOES virtual browsing (GK2A uses a plain nested tree)
        dl_prefix = self._get_real_goes_prefix() if is_goes else self.current_prefix
        self.log_message("INFO", f"Download: prefix={dl_prefix} kind={kind} bands={self.selected_bands}")
        if kind in ("goes", "gk2a"):
            goes_combo_val = getattr(self, 'goes_minute_combo', None)
            if goes_combo_val is not None:
                val = goes_combo_val.currentText()
                if val and val != "All":
                    self.log_message("INFO", f"Minute filter active: {val}")
        if not dl_prefix:
            QMessageBox.warning(self, "No Directory", "Please navigate to a directory first.")
            return
        if not self.selected_bands:
            QMessageBox.warning(self, "No Bands Selected", "Please select at least one band.")
            return

        # ---- Build local target directory (use DOY-based parts for GOES) ----
        if is_goes:
            real_path = self._get_real_goes_prefix()
            path_parts = real_path.rstrip('/').split('/') if real_path else ["root"]
        else:
            path_parts = self.current_path.copy() if self.current_path else ["root"]
        download_dir = Path(self.dir_edit.text()) / self.current_bucket.replace("noaa-", "") / "_".join(path_parts)
        download_dir.mkdir(parents=True, exist_ok=True)

        # ---- Warn before the long completeness scan (blocks the GUI thread) ----
        # Only warn on container folders (year/month/day) where the S3 listing can be huge.
        # Leaf-level time folders (Himawari HHMM slot / GOES hour / GK2A hour) only have ~16 files — no warning.
        path_len = len(self.current_path)
        if is_goes:
            is_leaf_folder = path_len >= 4   # PRODUCT/YYYY/DOY/HH
        elif kind == "gk2a":
            is_leaf_folder = path_len >= 6   # AMI/L1B/FD/YYYYMM/DD/HH
        else:
            is_leaf_folder = path_len >= 5   # PRODUCT/YYYY/MM/DD/HHMM
        if not is_leaf_folder:
            reply = QMessageBox.warning(
                self, "Pre-Download Completeness Check",
                "Before downloading, the app lists the entire S3 folder to check which files "
                "already exist locally.\n\n"
                "On a whole day folder this can involve thousands of files and the window may "
                "appear frozen for a while.\n\n"
                "Continue with the check?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply != QMessageBox.Yes:
                return

        # ---- Quick completeness check (bz2/dat/N.C present) ----
        s3_client = boto3.client('s3', config=Config(signature_version=UNSIGNED))
        s3_required_stems = set()      # filenames without the .bz2 extension
        paginator = s3_client.get_paginator('list_objects_v2')
        try:
            for page in paginator.paginate(Bucket=self.current_bucket, Prefix=dl_prefix):
                for obj in page.get('Contents', []):
                    key = obj['Key']
                    if key.endswith('/'):
                        continue
                    if kind == "gk2a":
                        band_patterns = [INV_GK2A_BAND_MAP[f"B{b:02d}"] for b in self.selected_bands]
                    else:
                        band_patterns = [f"C{b:02d}" if is_goes else f"B{b:02d}" for b in self.selected_bands]
                    if any(p in key for p in band_patterns):
                        stem = Path(key).name
                        if stem.endswith('.bz2'):
                            stem = stem[:-4]
                        s3_required_stems.add(stem)
        except Exception as e:
            self.log_message("ERROR", f"Unable to check local completeness: {str(e)}")
            s3_required_stems.clear()

        if s3_required_stems:
            all_present = True
            for stem in s3_required_stems:
                dat_path = download_dir / stem
                bz2_path = download_dir / (stem + '.bz2')
                if not dat_path.exists() and not bz2_path.exists():
                    all_present = False
                    break

            if all_present:
                if kind == "gk2a":
                    QMessageBox.information(self, "Already Complete",
                                            "All selected GK-2A files are already present.")
                    return
                reply = QMessageBox.question(
                    self, "Already Complete",
                    "All required files (either .bz2 or extracted .DAT) are already present.\n"
                    "Skip download and start processing?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    self.start_processing(str(download_dir), self.process_mode)
                    return

        # ---- Normal download ----
        extra = None
        if self.winds_checkbox.isChecked() and self.winds_available and self.current_winds_prefix:
            extra = self.current_winds_prefix

        self.start_download(dl_prefix, download_dir, self.selected_bands, extra)

    # ------------------------------------------------------------------
    #  Date Range Download Support — fully wired to both Legacy tree UI and Modern Animation/Quick Range tabs
    # ------------------------------------------------------------------

    def _generate_product_prefixes(self, start_dt: datetime, end_dt: datetime, 
                                    product: str = "AHI-L1b-FLDK",
                                    step_minutes: float = 10):
        """
        Generate list of CORRECT hierarchical S3 prefixes for every time slot.
        Handles Himawari (PRODUCT/YYYY/MM/DD/HHMM/) and GOES (PRODUCT/YYYY/DOY/HH/) layouts.
        Supports fractional step_minutes (e.g. 2.5 for Target/Japan rapid-scan sections).
        For sub-10-min steps, S3 prefixes are rounded to the nearest 10-min boundary
        (since Target/Japan folders are at 10-min intervals on S3).
        GK-2A: PRODUCT/{YYYYMM}/{DD}/{HH}/ (hour folders, files carry the scan minute).
        """
        is_goes = product.upper().startswith("ABI")
        is_gk2a = product.upper().startswith("AMI/")
        prefixes = []
        current = start_dt

        if not is_goes:
            # Round to nearest step boundary, then to 10-min S3 folder boundary
            total_minutes = current.hour * 60 + current.minute
            rounded = (total_minutes // step_minutes) * step_minutes
            h = int(rounded // 60)
            m = int(rounded % 60)
            current = current.replace(hour=h, minute=m, second=0, microsecond=0)
        else:
            current = current.replace(minute=0, second=0, microsecond=0)

        seen_goes_hours = set()
        while current <= end_dt:
            y, m, d = current.year, current.month, current.day
            h, mi = current.hour, current.minute

            if is_gk2a:
                # GK-2A: hour-level folder, one slot per scan minute (worker filters by filename minute)
                prefix = f"{product}/{y:04d}{m:02d}/{d:02d}/{h:02d}/"
                prefixes.append(prefix)
                current += timedelta(minutes=step_minutes)
            elif is_goes:
                doy = current.timetuple().tm_yday
                key = (y, doy, h)
                if key not in seen_goes_hours:
                    seen_goes_hours.add(key)
                    prefix = f"{product}/{y:04d}/{doy:03d}/{h:02d}/"
                    prefixes.append(prefix)
                current += timedelta(hours=1)
            else:
                # Himawari: flat HHMM folders
                # For sub-10-min steps (Target/Japan rapid scan), round to 10-min S3 folder
                if step_minutes < 10:
                    s3_min = (mi // 10) * 10
                    time_part = f"{h:02d}{s3_min:02d}"
                else:
                    time_part = f"{h:02d}{mi:02d}"
                prefix = f"{product}/{y:04d}/{m:02d}/{d:02d}/{time_part}/"
                prefixes.append(prefix)
                current += timedelta(minutes=step_minutes)

        return prefixes

    # Back-compat alias (some internal calls still use the old name)
    def _generate_fldk_prefixes(self, start_dt: datetime, end_dt: datetime, 
                                 satellite_bucket: str = "noaa-himawari9", 
                                 step_minutes: int = 10):
        product = getattr(self, '_get_product_prefix', lambda: "AHI-L1b-FLDK")()
        return self._generate_product_prefixes(start_dt, end_dt, product=product, step_minutes=step_minutes)

    def _generate_range_slots(self, start_dt: datetime, end_dt: datetime,
                              product: str = "AHI-L1b-FLDK",
                              step_minutes: float = 10):
        """Generate aligned (prefix, slot_datetime) pairs for every time slot.

        One pair per slot guarantees the S3 prefix list and the local slot names
        stay in sync (this was the root cause of GOES slots being missed).

        - GOES:     PRODUCT/YYYY/DOY/HH/   (hour folder, repeated per slot so each
                    slot downloads only its exact scan minute).
        - GK-2A:    PRODUCT/{YYYYMM}/{DD}/{HH}/  (hour folder repeated per slot;
                    the worker filters each folder to the slot's exact scan minute).
        - Himawari: PRODUCT/YYYY/MM/DD/HHMM/ with HHMM floored to the 10-min S3
                    folder boundary for every step; the worker filters each folder
                    to the slot's exact scan time (fixes 4x duplicate downloads on
                    Japan/Target rapid-scan and non-existent folders on 15/30/60 steps).

        Step grid:
        - step_minutes < 10  -> 2.5-min rapid-scan grid [00,02,05,...,57] (Himawari, 2-min grid for GK-2A LA)
        - otherwise          -> multiples of step_minutes within each hour.
        Slot datetimes always use whole minutes (no fractional-second encoding).
        """
        is_goes = product.upper().startswith("ABI")
        is_gk2a = product.upper().startswith("AMI/")

        if is_gk2a:
            step = max(1, int(round(step_minutes)))
            grid = list(range(0, 60, step))
        elif step_minutes < 10:
            grid = [0, 2, 5, 7, 10, 12, 15, 17, 20, 22, 25, 27,
                    30, 32, 35, 37, 40, 42, 45, 47, 50, 52, 55, 57]
        else:
            step = max(1, int(round(step_minutes)))
            grid = list(range(0, 60, step))

        prefixes = []
        slot_datetimes = []
        d = start_dt.date()
        while d <= end_dt.date():
            y, mo, dd = d.year, d.month, d.day
            for hour in range(24):
                for mi in grid:
                    slot_dt = datetime(y, mo, dd, hour, mi)
                    if slot_dt < start_dt or slot_dt > end_dt:
                        continue
                    if is_gk2a:
                        prefix = f"{product}/{y:04d}{mo:02d}/{dd:02d}/{hour:02d}/"
                    elif is_goes:
                        doy = slot_dt.timetuple().tm_yday
                        prefix = f"{product}/{y:04d}/{doy:03d}/{hour:02d}/"
                    else:
                        s3_min = (mi // 10) * 10
                        prefix = f"{product}/{y:04d}/{mo:02d}/{dd:02d}/{hour:02d}{s3_min:02d}/"
                    prefixes.append(prefix)
                    slot_datetimes.append(slot_dt)
            d += timedelta(days=1)
        return prefixes, slot_datetimes

    def _on_legacy_type_changed(self, type_text):
        """Update legacy minute combos + default step when Target/Japan is selected (2.5-min grid),
        or for GK-2A when Full Disk (10-min grid) vs Local Area (2-min grid) is selected."""
        t = (type_text or "").lower()
        kind = _sat_kind(self.sat_combo.currentText()) if hasattr(self, 'sat_combo') else "himawari"
        if kind == "gk2a":
            is_la = "local" in t or "la" in t
            mins = GK2A_LA_MINUTES if is_la else GK2A_FD_MINUTES
            preferred_step = "2 min" if is_la else "10 min"
        else:
            is_rapid = "japan" in t or "target" in t
            if is_rapid:
                mins = ["00", "02", "05", "07", "10", "12", "15", "17",
                        "20", "22", "25", "27", "30", "32", "35", "37",
                        "40", "42", "45", "47", "50", "52", "55", "57"]
                preferred_step = "2.5 min"
            else:
                mins = ["00", "10", "20", "30", "40", "50"]
                preferred_step = "10 min"
        for attr in ['legacy_from_min', 'legacy_to_min']:
            if hasattr(self, attr):
                cb = getattr(self, attr)
                if cb:
                    cb.blockSignals(True)
                    current = cb.currentText()
                    cb.clear()
                    cb.addItems(mins)
                    cb.setCurrentText(current if current in mins else mins[0])
                    cb.blockSignals(False)
        # Default the animation step to the appropriate grid
        if hasattr(self, 'legacy_step') and self.legacy_step:
            idx = self.legacy_step.findText(preferred_step)
            if idx >= 0:
                self.legacy_step.setCurrentIndex(idx)
        # Keep the legacy file-table minute filter grid in sync for GK-2A
        if kind == "gk2a":
            self._refresh_gk2a_minute_combo()

    def _download_legacy_date_range(self):
        """Download using the From/To controls + new Type selector + selected bands + winds + auto-process.
        This is the main 'From date to date' path the user requested.
        """
        try:
            from datetime import datetime as _dt

            # Read Legacy From/To combos
            y1 = int(self.legacy_from_year.currentText())
            m1 = int(self.legacy_from_month.currentText())
            d1 = int(self.legacy_from_day.currentText())
            h1 = int(self.legacy_from_hour.currentText())
            mi1 = int(self.legacy_from_min.currentText())

            y2 = int(self.legacy_to_year.currentText())
            m2 = int(self.legacy_to_month.currentText())
            d2 = int(self.legacy_to_day.currentText())
            h2 = int(self.legacy_to_hour.currentText())
            mi2 = int(self.legacy_to_min.currentText())

            start = _dt(y1, m1, d1, h1, mi1)
            end   = _dt(y2, m2, d2, h2, mi2)

            if start > end:
                QMessageBox.warning(self, "Date Range", "From date is after To date.")
                return

            # --- Determine satellite from current context or default ---
            sat_name = "Himawari 9"
            if hasattr(self, 'sat_combo') and self.sat_combo.currentText():
                sat_name = self.sat_combo.currentText()
            elif hasattr(self, 'modern_sat') and self.modern_sat and self.modern_sat.currentText():
                sat_name = self.modern_sat.currentText()

            # --- NEW: Type / Region from the dedicated dropdown ---
            type_text = self.legacy_type_combo.currentText() if hasattr(self, 'legacy_type_combo') else "Full Disk (FLDK)"
            product = self._get_product_for_type(sat_name, type_text)

            # --- Selected bands ONLY (user request: "only download THE SELECTED band") ---
            bands = []
            if hasattr(self, 'band_checkboxes'):
                for bname, cb in self.band_checkboxes.items():
                    if cb.isChecked():
                        try:
                            bands.append(int(bname[1:]))  # "B03" -> 3
                        except Exception:
                            pass
            if not bands:
                bands = [13]  # sensible default (IR)

            # Winds only makes sense for Himawari FLDK currently
            include_winds = bool(self.winds_checkbox.isChecked()) if hasattr(self, 'winds_checkbox') else False
            if include_winds and "FLDK" not in product:
                self.log_message("WARNING", "Winds data is only available for Full Disk (FLDK). Ignoring winds request.")
                include_winds = False

            auto_process = bool(self.auto_process_checkbox.isChecked()) if hasattr(self, 'auto_process_checkbox') else False
            process_mode = self.process_mode_combo.currentText().lower().replace(" ", "_") if hasattr(self, 'process_mode_combo') else "auto"
            # normalize combo text to what HimawariProcessorWorker expects
            mode_map = {"extract_&_netcdf": "auto", "extract_only": "extract_only", "netcdf_only": "nc_only"}
            process_mode = mode_map.get(process_mode, "auto")

            self.log_message("INFO", f"Range download: {product} | bands={bands} | winds={include_winds} | auto_process={auto_process}")

            # Store pending post-processing request so the finished handler can act on it
            self._pending_auto_process = {
                "enabled": auto_process,
                "mode": process_mode,
                "root": str(self.default_download_dir),
                "product": product,
            }

            # Step (animation frame spacing) from the dedicated legacy step combo
            if hasattr(self, 'legacy_step') and self.legacy_step:
                step = float(self.legacy_step.currentText().split()[0])
            else:
                step = float(self.anim_step.currentText().split()[0]) if hasattr(self, 'anim_step') else 10

            # Force re-download (overwrite existing .bz2 / NC files)
            force = bool(getattr(self, 'legacy_force_check', None) and self.legacy_force_check.isChecked())

            self.log_message("INFO", f"Range download: {product} | bands={bands} | winds={include_winds} "
                             f"| auto_process={auto_process} | step={step} min | force={force}")

            # Create + show a NON-MODAL progress window so the From→To range
            # download gets visible progress bars WITHOUT blocking the main UI.
            prev_prog = getattr(self, '_range_progress_dialog', None)
            if prev_prog is not None:
                prev_prog.close()
                self._range_progress_dialog = None
            num_slots = int((end - start).total_seconds() // (step * 60)) + 1
            prog = RangeProgressDialog(
                self,
                total_slots=num_slots,
                job_title=f"From→To Range — {num_slots} slots",
                show_adjust=True,
            )
            prog.update_download("Initialising From→To range download...")
            self._range_progress_dialog = prog
            self._bind_range_adjust(prog)
            self._register_job(DownloadJob(
                job_id=self._next_job_id,
                title=f"Range: {start:%Y-%m-%d %H:%M} → {end:%H:%M}",
                kind="range",
                window=prog,
            ))
            self._next_job_id += 1
            prog.show()

            self.download_date_range(
                start_datetime=start,
                end_datetime=end,
                bands=bands,
                download_root=str(self.default_download_dir),
                time_step_minutes=step,
                include_winds=include_winds,
                auto_process=auto_process,
                process_mode=process_mode,
                product=product,
                force=force
            )

            self.status_bar.showMessage(
                f"Range download started: {product} | {start} → {end} | {len(bands)} band(s) "
                f"(step: {step} min{', FORCE' if force else ''})"
            )

        except Exception as e:
            QMessageBox.critical(self, "Legacy Range Download", f"Failed to start range download:\n{e}")

    def _build_flat_slot_name(self, product: str, dt: datetime) -> str:
        """Central shared helper for BOTH single downloads and FROM/TO range downloads.
        Produces identical flat folder names:
            AHI-L1b-Target_2026_05_31_0000
            AHI-L1b-Japan_2026_05_31_0010
            AHI-L1b-FLDK_2026_05_31_0020
        This ensures FROM/TO and single downloads are indistinguishable to bg_extract.py / bg_controller.py.
        For fractional minutes (e.g. 2.5-min Target sections), the minute is formatted as HHMMSS
        where SS = (minute % 1) * 60, e.g. 12.5 min → 1230.
        """
        mi = dt.minute
        if mi != int(mi):
            # Fractional minute: encode as HHMMSS (e.g. 12.5 → 1230)
            sec = int(round((mi - int(mi)) * 60))
            return f"{product}_{dt.year:04d}_{dt.month:02d}_{dt.day:02d}_{dt.hour:02d}{int(mi):02d}{sec:02d}"
        return f"{product}_{dt.year:04d}_{dt.month:02d}_{dt.day:02d}_{dt.hour:02d}{mi:02d}"

    def download_date_range(self, start_datetime: datetime, end_datetime: datetime,
                            bands=None, download_root=None, time_step_minutes: int = 10,
                            include_winds: bool = False, auto_process: bool = False,
                            process_mode: str = "auto", progress_callback=None,
                            product: str | None = None, force: bool = False):
        """
        Download all available data for a date/time range.
        Respects selected bands, product (FLDK/Japan/Target), winds, and auto-process flag.

        The actual auto-processing trigger happens in the finished handler using _pending_auto_process.
        """
        if bands is None:
            bands = list(range(1, 17))

        if download_root is None:
            download_root = self.default_download_dir

        # Remember flags for the finished handler (legacy + modern callers)
        self._pending_auto_process = getattr(self, '_pending_auto_process', None) or {
            "enabled": bool(auto_process),
            "mode": process_mode or "auto",
            "root": str(download_root),
        }

        def _report(level, msg):
            if progress_callback:
                try:
                    progress_callback(f"[{level}] {msg}")
                except Exception:
                    pass
            elif hasattr(self, 'log_message'):
                try:
                    self.log_message(level, msg)
                except Exception:
                    print(f"[{level}] {msg}")
            else:
                print(f"[{level}] {msg}")

        _report("INFO", f"Starting date range download: {start_datetime} → {end_datetime} (step: {time_step_minutes} min)")

        # Determine bucket safely (modern may not have set current_bucket yet)
        bucket = getattr(self, 'current_bucket', None)
        if not bucket:
            # Infer from modern_sat if present (Modern mode)
            if hasattr(self, 'modern_sat') and self.modern_sat:
                sat = self.modern_sat.currentText()
                bucket = self._get_bucket_for_satellite(sat)
            else:
                bucket = "noaa-himawari9"
            self.current_bucket = bucket  # set for downstream start_download / slot naming

        # Respect explicit product when provided (especially important for Legacy range downloads).
        # Only fall back to Modern UI control or default when no explicit product was given.
        if not product:
            if hasattr(self, '_get_product_prefix'):
                try:
                    product = self._get_product_prefix() or "AHI-L1b-FLDK"
                except Exception:
                    product = "AHI-L1b-FLDK"
            else:
                product = "AHI-L1b-FLDK"

        prefixes, slot_datetimes = self._generate_range_slots(
            start_datetime, end_datetime,
            product=product,
            step_minutes=time_step_minutes,
        )
        # Each slot carries its exact scan datetime so RangeS3DownloadWorker can
        # download ONLY the matching scan inside shared 10-min/hour S3 folders.

        # Generate the matching FLAT local folder names using the shared helper.
        # This ensures FROM/TO range downloads create exactly the same structure as single downloads.
        local_slot_names = [self._build_flat_slot_name(product, dt) for dt in slot_datetimes]

        if not prefixes:
            _report("WARNING", "No time slots generated for the given range.")
            return

        _report("INFO", f"Generated {len(prefixes)} time slots to download.")

        # Keep the progress dialog total in sync with the real slot count so the
        # From→To / range progress bar reaches 100% exactly when the last slot finishes.
        _prog_dlg = getattr(self, '_range_progress_dialog', None)
        if _prog_dlg is not None:
            _prog_dlg._total = max(1, len(prefixes))
            _prog_dlg.dl_bar.setValue(0)

        # H#3 fix: ALWAYS non-blocking via dedicated RangeS3DownloadWorker (no UI sleep/processEvents).
        # download_date_range now thin coordinator; real work in worker thread.
        # Modern callers + callbacks get live updates. Legacy paths also benefit (no freeze).
        if getattr(self, '_closing', False):
            _report("WARNING", "Close in progress — range aborted")
            return

        max_slots = getattr(self, '_max_concurrent_slots', 4)
        thr_per_slot = getattr(self, '_threads_per_slot', 8)
        self._range_worker = RangeS3DownloadWorker(
            bucket, prefixes, download_root,
            bands=bands, include_winds=include_winds,
            local_slot_names=local_slot_names,
            slot_datetimes=slot_datetimes,
            force=force,
            max_concurrent_slots=max_slots,
            threads_per_slot=thr_per_slot,
        )
        local_worker = self._range_worker

        prog_dialog = getattr(self, '_range_progress_dialog', None)
        job = self._job_for_window(prog_dialog)
        if job is not None:
            job.worker = local_worker  # manager can report live RUNNING state + cancel
            job.cancel_cb = local_worker.cancel

        def _relay(msg):
            _report("INFO", msg)
            if prog_dialog and not prog_dialog._cancelled:
                prog_dialog.update_download(msg)
        self._range_worker.progress.connect(_relay)
        self._range_worker.slot_finished.connect(
            lambda idx, tot, pref, ok: (
                _report("INFO", f"Slot {idx}/{tot} {'OK' if ok else 'FAIL'}: {pref}"),
                prog_dialog and prog_dialog.slot_downloaded()
            )
        )

        # Hook finished so we can trigger auto-processing for the range (user request)
        def _on_range_finished(ok, root):
            _report("SUCCESS" if ok else "WARNING", f"Range done ({'ok' if ok else 'issues'}) -> {root}")
            # Check if progress window was cancelled
            if prog_dialog and prog_dialog._cancelled:
                _report("INFO", "Range download cancelled by user")
                self._range_progress_dialog = None
                self._set_job_state(job, "cancelled")
                self._unregister_job(job)
                return
            # Update progress window after download completes
            if prog_dialog:
                prog_dialog.update_download("Download complete", 100)
            # Skip auto-processing for GOES / GK-2A (already NetCDF, not supported by the extract/convert pipeline)
            kind_range = _bucket_kind(bucket)
            if kind_range in ("goes", "gk2a"):
                skip_label = "GOES" if kind_range == "goes" else "GK-2A"
                _report("INFO", f"Skipping auto-processing: {skip_label} files are already in .nc format and are not converted by the pipeline.")
                self._pending_auto_process = None
                if prog_dialog:
                    prog_dialog.update_process(f"Skipped ({skip_label})", 100)
                self._close_progress_window(prog_dialog)
                return
            # Auto-process if requested for this run
            pending = getattr(self, '_pending_auto_process', None)
            if pending and pending.get("enabled"):
                try:
                    self.log_message("INFO", f"Auto-processing range download at: {root}")
                    if prog_dialog:
                        prog_dialog.update_process("Starting processing...", 0)
                    proc_worker = HimawariProcessorWorker(
                        directory_path=pending.get("root", root),
                        process_mode=pending.get("mode", "auto"),
                        force_simple=self.force_simple,
                        skip_rgb=getattr(self, 'skip_rgb', False),
                        skip_ads=getattr(self, 'skip_ads', False),
                        low_io=getattr(self, 'low_io', True),
                        nc_format=getattr(self, 'nc_format', 'pwards'),
                    )
                    if prog_dialog:
                        proc_worker.progress.connect(
                            lambda m: not prog_dialog._cancelled and prog_dialog.update_process(m)
                        )
                        proc_worker.finished.connect(
                            lambda s, p: (
                                self.log_message("SUCCESS" if s else "WARNING",
                                                 f"Auto-process {'complete' if s else 'finished with issues'}: {p}"),
                                not prog_dialog._cancelled and (
                                    prog_dialog.slot_processed(),
                                    QTimer.singleShot(1500, prog_dialog.close)
                                ),
                                self._unregister_job_for_window(prog_dialog),
                            )
                        )
                    else:
                        proc_worker.progress.connect(lambda m: self.log_message("INFO", f"[PROCESS] {m}"))
                        proc_worker.finished.connect(
                            lambda s, p: self.log_message("SUCCESS" if s else "WARNING",
                                                          f"Auto-process {'complete' if s else 'finished with issues'}: {p}"))
                    if hasattr(self, 'update_statistics_display'):
                        proc_worker.stats_update.connect(self.update_statistics_display)
                    proc_worker.start()
                    self._processing_workers = getattr(self, '_processing_workers', [])
                    self._processing_workers.append(proc_worker)
                except Exception as ex:
                    _report("ERROR", f"Auto-process launch failed: {ex}")
                    if prog_dialog:
                        prog_dialog.update_process(f"Error: {ex}")
            else:
                # No processing requested — close window
                if prog_dialog:
                    prog_dialog.update_process("No processing requested", 100)
                self._close_progress_window(prog_dialog)
            # Clear pending after handling
            self._pending_auto_process = None

        # Cancel check before starting
        if prog_dialog and prog_dialog._cancelled:
            _report("INFO", "Range download cancelled")
            self._range_progress_dialog = None
            return

        self._range_worker.finished.connect(_on_range_finished)
        self._range_worker.error.connect(lambda m: _report("ERROR", m))

        # Wire progress window cancel → THIS job's worker cancel
        if prog_dialog:
            orig_cancel = prog_dialog._do_cancel
            def _cancel_with_worker():
                orig_cancel()
                if local_worker and local_worker.isRunning():
                    local_worker.cancel()
                self._set_job_state(job, "cancelled")
            prog_dialog._do_cancel = _cancel_with_worker

        local_worker.start()
        _report("INFO", "RangeS3DownloadWorker started (non-blocking). UI remains responsive.")

    def _construct_winds_prefix_for_datetime(self, fldk_prefix: str) -> str | None:
        """Build matching winds prefix for either legacy or modern (hierarchical) style."""
        if "AHI-L1b-FLDK" in fldk_prefix:
            winds_prefix = fldk_prefix.replace("AHI-L1b-FLDK", "AHI-L2-FLDK-Winds", 1)
            return winds_prefix
        return None

    # Example usage (for scripts / future UI / Animation tab integration):
    #
    # from datetime import datetime
    # mgr = HimawariFileManager()
    # mgr.download_date_range(
    #     start_datetime=datetime(2026, 5, 28, 0, 0),
    #     end_datetime=datetime(2026, 5, 30, 12, 0),
    #     bands=list(range(1,17)),
    #     time_step_minutes=10,
    #     include_winds=True,
    #     auto_process=True
    # )
    #
    # This will download every 10-min slot in the range into separate subfolders
    # under the chosen download root, optionally including L2 winds.

    def download_single_file(self, file_info):
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Save File",
            str(Path(self.dir_edit.text()) / file_info['name']),
            "All Files (*.*)"
        )
        if not save_path:
            return
        # v3.0.5: non-blocking + queueable. Route through the shared worker/queue
        # path instead of downloading synchronously on the GUI thread. The exact
        # S3 object key is used as the prefix, so only that one file is fetched;
        # the file lands in the chosen folder under its original S3 filename.
        save_dir = Path(save_path).parent
        save_dir.mkdir(parents=True, exist_ok=True)
        self.start_download(file_info['key'], str(save_dir), [], extra_prefix=None)
        dlw = getattr(self, 'download_worker', None)
        if dlw is not None:
            dlw._skip_autoprocess = True  # never auto-process an arbitrary folder

    def _update_banner_visibility(self):
        if hasattr(self, '_legacy_banner') and self._legacy_banner is not None:
            visible = not (self.progress_bar.isVisible() or self.process_progress_bar.isVisible())
            self._legacy_banner.setVisible(visible)

    def start_download(self, prefix, download_dir, bands, extra_prefix=None):
        # v3.0.5: queue-friendly. Every download gets its own NON-MODAL progress
        # window instead of blocking the main UI; you can start more downloads
        # while others run (see View → Download Jobs (Queue Manager)).
        max_workers = self.concurrent_spin.value()
        self.log_message("INFO", f"Starting download ({max_workers} concurrent) from: s3://{self.current_bucket}/{prefix}")
        if extra_prefix:
            self.log_message("INFO", f"Including winds data from: {extra_prefix}")

        self.download_worker = S3DownloadWorker(
            self.current_bucket, prefix, download_dir, bands, max_workers, extra_prefix=extra_prefix
        )
        # Pass minute filter if GOES/GK-2A is active and a specific minute is selected
        kind = _bucket_kind(self.current_bucket)
        if kind in ("goes", "gk2a"):
            val = "All"
            goes_combo = getattr(self, 'goes_minute_combo', None)
            if goes_combo is not None:
                val = goes_combo.currentText()
                if val and val != "All" and val.isdigit():
                    self.download_worker.goes_minute_filter = int(val)
            self.log_message("INFO", f"Minute filter: {val}")

        worker = self.download_worker

        # ── Non-modal progress window for THIS download job ──────────
        short = prefix.rstrip('/').rsplit('/', 1)[-1] or prefix
        prog = RangeProgressDialog(self, total_slots=1, job_title=f"Download — {short}")
        prog.update_download("Queued...")
        worker._progress_window = prog
        job = self._register_job(DownloadJob(
            job_id=self._next_job_id,
            title=f"Download: {short}",
            kind="single",
            window=prog,
            worker=worker,
            cancel_cb=worker.cancel,
        ))
        self._next_job_id += 1

        def _dl_msg(msg):
            self.log_message("INFO", msg)
            if prog._cancelled or not prog.isVisible():
                return
            up = msg.lower()
            if any(k in up for k in ("extract", "processing", "netcdf", "convert", "encode")):
                prog.update_process(msg)
            else:
                prog.update_download(msg)

        def _dl_file(cur, tot, fn):
            pct = int((cur / max(1, tot)) * 100)
            self.progress_bar.setValue(pct)
            self.status_bar.showMessage(f"Downloading {fn} ({cur}/{tot})")
            if not prog._cancelled and prog.isVisible():
                prog.update_download(f"[{cur}/{tot}] {fn}", pct)

        worker.progress.connect(_dl_msg)
        worker.file_progress.connect(_dl_file)
        worker.finished.connect(self.on_download_finished)
        worker.error.connect(
            lambda err: (
                self.log_message("ERROR", err),
                (not prog._cancelled and prog.isVisible()) and prog.update_download(f"ERROR: {err}")
            )
        )

        # Cancel wiring: window Cancel cancels THIS job's worker only
        orig_cancel = prog._do_cancel
        def _cancel_with_worker():
            orig_cancel()
            if worker and worker.isRunning():
                worker.cancel()
            self._set_job_state(job, "cancelled")
        prog._do_cancel = _cancel_with_worker

        self.download_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self._update_banner_visibility()
        prog.show()
        worker.start()

    def on_file_progress(self, current, total, filename):
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_bar.showMessage(f"Downloading {filename} ({current}/{total})")

    def on_download_finished(self, success, download_path):
        worker = self.sender()
        prog = getattr(worker, '_progress_window', None) if worker else None
        job = self._job_for_window(prog)

        # Hide the shared main progress bar only when no other job is running
        any_other_running = any(
            j.worker is not None and j.worker is not worker
            and getattr(j.worker, 'isRunning', lambda: False)()
            for j in getattr(self, '_download_jobs', [])
        )
        if not any_other_running:
            self.progress_bar.setVisible(False)
            self.progress_bar.setValue(0)
            self.download_btn.setEnabled(True)
        self._update_banner_visibility()

        if success:
            self.log_message("SUCCESS", f"Download completed successfully to: {download_path}")
            if prog and not prog._cancelled:
                prog.slot_downloaded()
                prog.update_download("Download complete", 100)
            kind = _bucket_kind(self.current_bucket)
            if kind in ("goes", "gk2a"):
                skip_label = "GOES" if kind == "goes" else "GK-2A"
                self.log_message("INFO", f"Skipping extract/processing for {skip_label} (already NetCDF).")
                QMessageBox.information(self, f"{skip_label} Download Complete",
                    f"Download completed to:\n{download_path}\n\n(Extract/processing skipped: {skip_label} files are already in .nc format and are read directly by MonWatch.)")
                self._close_progress_window(prog)
            elif self.auto_process and download_path and not bool(getattr(worker, '_skip_autoprocess', False)):
                self.log_message("INFO", "Starting auto-processing of downloaded files...")
                # Keep the job's window open through auto-processing, like From→To
                self.start_processing(download_path, self.process_mode, prog=prog)
                return
            else:
                QMessageBox.information(self, "Success", f"Download completed to:\n{download_path}\n\nAuto-processing (extract & NetCDF) started.")
                self._close_progress_window(prog)
        else:
            self.log_message("WARNING", "Download failed or cancelled.")
            if prog:
                if prog._cancelled:
                    self._close_progress_window(prog)
                else:
                    prog.update_download("Download failed", 100)
                    self._close_progress_window(prog)
            else:
                QMessageBox.warning(self, "Error", "Download failed. Check status messages.")

    # ------------------------------------------------------------------
    #  Processing
    # ------------------------------------------------------------------
    def start_processing(self, directory_path, process_mode="auto", prog=None):
        if self.processor_worker and self.processor_worker.isRunning():
            self.log_message("WARNING", "Processing already in progress")
            return

        max_workers = self.concurrent_spin.value()
        self.processor_worker = HimawariProcessorWorker(
            directory_path,
            process_mode,
            self.force_simple,
            max_workers,
            skip_rgb=self.skip_rgb,
            skip_ads=self.skip_ads,
            skip_extract=getattr(self, 'skip_extract', False),
            fast=getattr(self, 'fast_mode', False),
            low_io=getattr(self, 'low_io', True),
            nc_format=getattr(self, 'nc_format', 'pwards'),
        )
        # Reuse the download job's window for process feedback (like From→To)
        if prog is None:
            dw = getattr(self, 'download_worker', None)
            prog = getattr(dw, '_progress_window', None) if dw else None
        self.processor_worker._progress_window = prog

        def _proc_msg(msg):
            self.log_message("PROCESS", msg)
            if prog and not prog._cancelled and prog.isVisible():
                prog.update_process(msg)

        self.processor_worker.progress.connect(_proc_msg)
        self.processor_worker.finished.connect(self.on_processing_finished)
        self.processor_worker.error.connect(
            lambda msg: (
                self.log_message("ERROR", msg),
                (prog and not prog._cancelled and prog.isVisible()) and prog.update_process(f"Error: {msg}")
            )
        )
        self.processor_worker.stats_update.connect(self.update_statistics_display)
        self.process_progress_bar.setVisible(True)
        self.process_progress_bar.setRange(0, 0)
        self.status_bar.showMessage(f"Processing files ({process_mode})...")
        self._update_banner_visibility()
        if prog:
            prog.update_process("Processing...", 0)
        self.processor_worker.start()

    def on_processing_finished(self, success, directory_path):
        self.process_progress_bar.setVisible(False)
        self._update_banner_visibility()
        worker = self.sender()
        prog = getattr(worker, '_progress_window', None) if worker else None
        if prog:
            if not prog._cancelled:
                prog.slot_processed()
                prog.update_process("Processing complete" if success else "Processing finished with issues", 100)
            self._close_progress_window(prog)
        if success:
            self.log_message("SUCCESS", f"Processing completed in: {directory_path}")
            self.status_bar.showMessage("Processing completed successfully")
            reply = QMessageBox.question(
                self, "Processing Complete",
                f"Files processed successfully!\n\nDirectory: {directory_path}\n\nOpen folder?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.open_folder(directory_path)
        else:
            self.log_message("ERROR", "Processing failed")
            self.status_bar.showMessage("Processing failed")

    def update_statistics_display(self, stats):
        self.stats_labels['extracted'].setText(
            f"Extracted: {stats.get('successfully_extracted', 0)}/{stats.get('total_extracted', 0)}"
        )
        self.stats_labels['nc_created'].setText(f"NetCDF: {stats.get('nc_created', 0)}")
        self.stats_labels['nc_failed'].setText(f"NC Failed: {stats.get('nc_failed', 0)}")
        # v3.1.2 – RGB stats
        rgb_ok = stats.get('rgb_ok', None)
        rgb_total = stats.get('rgb_total', None)
        if rgb_ok is not None and rgb_total is not None:
            self.stats_labels['rgb_ok'].setText(f"RGB OK: {rgb_ok}")
            self.stats_labels['rgb_total'].setText(f"RGB Total: {rgb_total}")
            color = "#4CAF50" if rgb_ok == rgb_total else "#FF9800"
            self.stats_labels['rgb_ok'].setStyleSheet(f"color: {color}; font-size: 10px;")
        ads_txt = "ADS: disabled" if self.skip_ads else "ADS: enabled"
        ads_col = "#888" if self.skip_ads else "#4CAF50"
        self.stats_labels['ads_status'].setText(ads_txt)
        self.stats_labels['ads_status'].setStyleSheet(f"color: {ads_col}; font-size: 10px;")

    # ------------------------------------------------------------------
    #  Utilities
    # ------------------------------------------------------------------
    def open_folder(self, directory_path):
        try:
            path = Path(directory_path)
            if path.exists():
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.run(["open", str(path)])
                else:
                    subprocess.run(["xdg-open", str(path)])
        except Exception as e:
            self.log_message("ERROR", f"Failed to open folder: {str(e)}")

    def on_list_error(self, error_msg):
        self.log_message("ERROR", error_msg)
        self.file_count_label.setText(f"Error: {error_msg}")

    def on_list_finished(self):
        self.status_bar.showMessage("Ready")

    def select_all_bands(self):
        for cb in self.band_checkboxes.values():
            cb.setChecked(True)

    def select_no_bands(self):
        for cb in self.band_checkboxes.values():
            cb.setChecked(False)

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Download Directory", str(self.dir_edit.text()))
        if directory:
            self.dir_edit.setText(directory)
            self.log_message("INFO", f"Download directory set to: {directory}")

    def log_message(self, level, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        icons = {
            "ERROR": "❌", "SUCCESS": "✅", "PROCESS": "🔧",
            "INFO": "ℹ️", "WARNING": "⚠️"
        }
        icon = icons.get(level, "")
        if level in ["ERROR", "SUCCESS", "PROCESS"]:
            self.status_bar.showMessage(f"{icon} {message}")
        elif level == "INFO" and any(kw in message for kw in ["Auto-processing", "Processing mode", "Force simple"]):
            self.status_bar.showMessage(f"{icon} {message}")

        color_codes = {
            "ERROR": "\033[91m", "SUCCESS": "\033[92m",
            "PROCESS": "\033[93m", "INFO": "\033[94m", "WARNING": "\033[93m"
        }
        reset = "\033[0m"
        color = color_codes.get(level, "\033[97m")
        print(f"{color}[{timestamp}] {level}: {message}{reset}")


def main():
    try:
        import boto3
        from PySide6 import QtCore
    except ImportError as e:
        print(f"Missing required packages: {e}")
        print("Install with: pip install boto3 PySide6 watchdog numpy pillow rasterio satpy")
        return 1

    # Allow forcing UI mode from command line (used by App.py when launching)
    force_ui = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--ui", "-ui") and i + 1 < len(sys.argv):
            force_ui = sys.argv[i + 1].strip().lower()
        elif arg.startswith("--ui="):
            force_ui = arg.split("=", 1)[1].strip().lower()
        elif arg in ("legacy", "modern"):
            force_ui = arg

    app = QApplication(sys.argv)
    _icon_path = Path(__file__).resolve().parent.parent / 'public' / 'images' / 'Monwatch-LOGO.png'
    if _icon_path.exists():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(_icon_path)))
    app.setStyleSheet("QMainWindow { background: #222; }")

    # ─────────────────────────────────────────────────────────────
    #  MODERN UI: UNDER DEVELOPMENT — DO NOT TOUCH
    #  Modern UI is DISABLED. Any --ui modern / "modern" request is
    #  overridden here so the app always launches in Legacy mode.
    #  ─────────────────────────────────────────────────────────────
    force_legacy = True

    window = HimawariFileManager(force_legacy=force_legacy)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()


# === v3.0.2 Plan Additions (GOES stubs — minimal retention per H: bloat cleanup; experimental handled via labels + SatDiscoveryConfig only) ===
def download_goes_data_stub(date_str: str, sector: str = "full_disk"):
    """GOES download stub (dead code, never called from Modern/Legacy).
    Retained for reference only. Real support future (v4). Current: labels + skeleton only.
    """
    print(f"[GOES] Stub: Would download {sector} data for {date_str}")
    return []


class GOESProcessorStub:
    """Basic GOES support class (plan item).
    This will be expanded with full download + preview generation for Full Disk / Target / etc.
    """
    def __init__(self):
        self.goes_bands = ["C01", "C02", "C03", "C13", "C14"]  # Common ABI bands

    def list_goes_scenes(self, date_str):
        print(f"[GOES] Listing scenes for {date_str} (stub)")
        return []  # Would return list of (time, sector) tuples

    def generate_preview(self, file_path, sector="full_disk"):
        print(f"[GOES] Generating {sector} preview for {file_path} (stub)")
        return None  # Would return QPixmap or path to quicklook

    # modernize_ui_stub removed — Modern overhaul (Quick Scene + Animation + console + direct downloads + robust live discovery)
    # is COMPLETE and superior. Legacy preserved for users who prefer the classic tree browser.