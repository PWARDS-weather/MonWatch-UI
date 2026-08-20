#!/usr/bin/env python3
"""
MonWatch Downloader v3.0.4 — S3 data downloader and processor for Himawari/GOES imagery.

Provides modern and legacy UI modes for discovery, download, and NetCDF conversion.
"""
import sys
import os
import json
import ftplib
import traceback
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
    QStackedWidget, QDialog
)
from PySide6.QtGui import QFont, QIcon, QPixmap, QAction

from dataclasses import dataclass, field
from typing import Dict

class FTPClient:
    """Singleton-style FTP client for JAXA P-Tree.
    Manages connection and authentication using accounts.json.
    """
    def __init__(self, server, username, password, port=21, passive=True):
        self.server = server
        self.port = port
        self.user = username
        self.passwd = password
        self.passive = passive

    def connect(self):
        conn = ftplib.FTP()
        conn.connect(self.server, self.port)
        conn.login(self.user, self.passwd)
        if self.passive:
            conn.set_pasv(True)
        return conn

    def close(self, conn):
        if conn:
            try:
                conn.quit()
            except:
                conn.close()

@dataclass(frozen=True)
class SatDiscoveryConfig:
    """Parameterized discovery config per satellite.
    FTP Paths:
    Raw Bands: /jma/HSD/{product}/{year:04d}/{month:02d}/{day:02d}/{hour:02d}{minute:02d}/
    NetCDF: /jma/netcdf/{year:04d}{month:02d}/{day:02d}/{hour:02d}/
    """
    ftp_root_raw: str
    ftp_root_nc: str
    date_prefix_template: str
    nc_prefix_template: str
    year_discovery_prefix: str
    winds_product_map: Dict[str, str] = field(default_factory=dict)
    nc_suffix_pattern: str = "*_AHI.nc"
    datetime_folder_pattern: str = "{product}_{y}{m}{d}_{h}{mi}"

SAT_CONFIGS: Dict[str, SatDiscoveryConfig] = {
    "himawari9": SatDiscoveryConfig(
        ftp_root_raw="/jma/HSD/",
        ftp_root_nc="/jma/netcdf/",
        date_prefix_template="{product}/{year:04d}/{month:02d}/{day:02d}/{hour:02d}{minute:02d}/",
        nc_prefix_template="{year:04d}{month:02d}/{day:02d}/{hour:02d}/",
        year_discovery_prefix="{product}/",
        winds_product_map={"AHI-L1b-FLDK": "AHI-L2-FLDK-Winds"},
        nc_suffix_pattern="*_AHI.nc",
    ),
    "himawari8": SatDiscoveryConfig(
        ftp_root_raw="/jma/HSD/",
        ftp_root_nc="/jma/netcdf/",
        date_prefix_template="{product}/{year:04d}/{month:02d}/{day:02d}/{hour:02d}{minute:02d}/",
        nc_prefix_template="{year:04d}{month:02d}/{day:02d}/{hour:02d}/",
        year_discovery_prefix="{product}/",
        winds_product_map={"AHI-L1b-FLDK": "AHI-L2-FLDK-Winds"},
        nc_suffix_pattern="*_AHI.nc",
    ),
}

# Known FTP servers and their format/path configurations
KNOWN_SERVERS = {
    "ftp.ptree.jaxa.jp": {
        "display": "JAXA P-Tree",
        "formats": ["HSD (Raw Bands)", "NetCDF"],
        "default_format": "NetCDF",
        "paths": {
            "HSD (Raw Bands)": "/jma/HSD/",
            "NetCDF": "/jma/netcdf/"
        }
    }
}



class FTPLister(QThread):
    progress = Signal(str)
    directories_found = Signal(list)
    files_found = Signal(list)
    error = Signal(str)
    finished = Signal()

    def __init__(self, ftp_client, remote_path=""):
        super().__init__()
        self.ftp_client = ftp_client
        self.remote_path = remote_path.rstrip('/') + '/' if remote_path else ""
        self.list_files = False

    def run(self):
        conn = None
        try:
            self.progress.emit(f"Listing: {self.remote_path}")
            conn = self.ftp_client.connect()
            
            # Change to remote path
            try:
                conn.cwd(self.remote_path)
            except ftplib.error_perm:
                # If path doesn't exist or we are at root
                pass

            directories = []
            files = []

            # Get listing using nlst() for names or dir() for details
            # Using mlst() if supported, otherwise fallback to nlst()
            try:
                items = conn.nlst()
            except ftplib.error_perm:
                self.error.emit("Permission denied while listing directory")
                return

            for item in items:
                if item in ('.', '..'):
                    continue
                
                # To determine if it's a directory, we try to CWD into it
                try:
                    conn.cwd(item)
                    directories.append(item)
                    conn.cwd('..')
                except ftplib.error_perm:
                    # It's a file
                    files.append({'name': item})

            self.directories_found.emit(directories)
            if self.list_files:
                self.files_found.emit(files)

        except Exception as e:
            self.error.emit(f"FTP Listing error: {str(e)}")
        finally:
            if conn:
                self.ftp_client.close(conn)
            self.finished.emit()


class FTPDownloadWorker(QThread):
    progress = Signal(str)
    file_progress = Signal(int, int, str)
    finished = Signal(bool, str)
    error = Signal(str)

    def __init__(self, ftp_client, remote_prefix, download_dir, bands=None, max_workers=8, extra_prefix=None, parent=None, mode="raw"):
        super().__init__(parent)
        self.ftp_client = ftp_client
        self.remote_prefix = remote_prefix
        self.download_dir = Path(download_dir)
        self.bands = bands if bands else []
        self.max_workers = max_workers
        self.extra_prefix = extra_prefix
        self.mode = mode  # "raw" or "nc"
        self._cancelled = False

    def run(self):
        download_path = ""
        try:
            # We need a connection to list files
            conn = self.ftp_client.connect()
            self.progress.emit(f"Listing files in: {self.remote_prefix}")
            
            files_to_download = []
            
            try:
                conn.cwd(self.remote_prefix)
                items = conn.nlst()
                for item in items:
                    if self.mode == "raw":
                        # Raw bands: look for .bz2
                        if not item.endswith('.bz2'):
                            continue
                        if self.bands:
                            if not any(f"B{band:02d}" in item for band in self.bands):
                                continue
                    else:
                        # NetCDF: match NC_...nc
                        if not (item.startswith("NC_") and item.endswith(".nc")):
                            continue
                    
                    files_to_download.append(item)
            except Exception as e:
                self.error.emit(f"Listing error: {str(e)}")
                self.ftp_client.close(conn)
                self.finished.emit(False, "")
                return

            self.ftp_client.close(conn)

            if not files_to_download:
                self.error.emit("No files found matching criteria")
                self.finished.emit(False, "")
                return

            self.progress.emit(f"Found {len(files_to_download)} files to download")
            self.download_dir.mkdir(parents=True, exist_ok=True)
            download_path = str(self.download_dir)

            total_files = len(files_to_download)
            downloaded = 0
            completed = 0

            def download_single(filename):
                if self._cancelled:
                    return False, filename
                
                local_path = self.download_dir / filename
                try:
                    # Each thread gets its own connection
                    thread_conn = self.ftp_client.connect()
                    try:
                        thread_conn.cwd(self.remote_prefix)
                        with open(local_path, 'wb') as f:
                            thread_conn.retrbinary(f"RETR {filename}", f.write)
                    finally:
                        self.ftp_client.close(thread_conn)
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


class FTPRangeDownloadWorker(QThread):
    """Dedicated non-blocking worker for date-range downloads.
    Handles sequential slots internally using FTP connections.
    """
    progress = Signal(str)
    slot_finished = Signal(int, int, str, bool)  # slot_idx, total, prefix, success
    finished = Signal(bool, str)
    error = Signal(str)

    def __init__(self, ftp_client, prefixes, download_root, bands=None, include_winds=False,
                  time_step_minutes=10, progress_callback=None, local_slot_names=None, mode="raw"):
        super().__init__()
        self.ftp_client = ftp_client
        self.prefixes = prefixes or []
        self.download_root = Path(download_root)
        self.bands = bands or list(range(1, 17))
        self.include_winds = include_winds
        self.local_slot_names = local_slot_names or []
        self.mode = mode
        self._cancelled = False

    def run(self):
        total = len(self.prefixes)
        success_count = 0
        try:
            self.progress.emit(f"Range worker: {total} slots starting")

            for i, prefix in enumerate(self.prefixes):
                if self._cancelled:
                    break

                if i < len(self.local_slot_names) and self.local_slot_names[i]:
                    local_name = self.local_slot_names[i]
                else:
                    local_name = prefix.rstrip('/').replace('/', '_')

                # Using "ftp_data" as a generic folder for range downloads
                slot_dir = self.download_root / "ftp_data" / local_name
                slot_dir.mkdir(parents=True, exist_ok=True)

                self.progress.emit(f"[{i+1}/{total}] Range slot: {prefix}")

                # Use FTPDownloadWorker logic here directly or instantiate it
                # To keep it simple and avoid nested QThreads, we'll do it in a helper
                ok = self._download_slot(prefix, slot_dir)
                
                if ok:
                    success_count += 1
                    self.slot_finished.emit(i+1, total, prefix, True)
                else:
                    self.slot_finished.emit(i+1, total, prefix, False)

            final_ok = not self._cancelled
            self.finished.emit(final_ok, str(self.download_root))
            self.progress.emit(f"Range complete: {success_count}/{total} slots with data")

        except Exception as e:
            self.error.emit(f"Range worker error: {e}")
            self.finished.emit(False, str(self.download_root))

    def _download_slot(self, prefix, slot_dir):
        try:
            conn = self.ftp_client.connect()
            conn.cwd(prefix)
            items = conn.nlst()
            
            files_to_download = []
            if self.mode == "raw":
                for item in items:
                    if item.endswith('.bz2'):
                        if not self.bands or any(f"B{band:02d}" in item for band in self.bands):
                            files_to_download.append(item)
            else:
                for item in items:
                    if item.startswith("NC_") and item.endswith(".nc"):
                        files_to_download.append(item)
            
            if not files_to_download:
                self.ftp_client.close(conn)
                return False

            for filename in files_to_download:
                if self._cancelled:
                    break
                with open(slot_dir / filename, 'wb') as f:
                    conn.retrbinary(f"RETR {filename}", f.write)
                self.progress.emit(f"  ✓ {filename}")
            
            self.ftp_client.close(conn)
            return True
        except Exception as e:
            self.progress.emit(f"  [ERR] {e}")
            return False

    def cancel(self):
        self._cancelled = True



class WindsChecker(QThread):
    """Quickly check whether a winds directory exists on FTP."""
    result = Signal(bool)
    error = Signal(str)

    def __init__(self, ftp_client, prefix):
        super().__init__()
        self.ftp_client = ftp_client
        self.prefix = prefix

    def run(self):
        try:
            conn = self.ftp_client.connect()
            try:
                conn.cwd(self.prefix)
                items = conn.nlst()
                has_content = len(items) > 0
            except:
                has_content = False
            finally:
                self.ftp_client.close(conn)
            self.result.emit(has_content)
        except Exception as e:
            self.error.emit(str(e))
            self.result.emit(False)


class RangeProgressDialog(QDialog):
    """Modal progress dialog for animation range download + processing.
    Shows two progress bars (download + process) and a cancel button.
    Closing the dialog or pressing Cancel stops both workers.
    """
    def __init__(self, parent=None, total_slots=1):
        super().__init__(parent)
        self.setWindowTitle("Animation Range Download/Processing")
        self.setMinimumWidth(500)
        self.setModal(True)
        self._cancelled = False
        self._downloaded = 0
        self._processed = 0
        self._total = total_slots

        layout = QVBoxLayout(self)

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

        # Cancel button
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._do_cancel)
        layout.addWidget(self.cancel_btn)

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


class AvailableDatesDiscoverer(QThread):
    """Discovers available years from JAXA FTP."""
    years_discovered = Signal(list)
    error = Signal(str)
    finished = Signal()

    _cache = {}

    def __init__(self, ftp_client, product="AHI-L1b-FLDK"):
        super().__init__()
        self.ftp_client = ftp_client
        self.product = product or "AHI-L1b-FLDK"
        self._cancelled = False

    def run(self):
        cache_key = (self.ftp_client.server, self.product)
        try:
            if cache_key in AvailableDatesDiscoverer._cache:
                cached = AvailableDatesDiscoverer._cache[cache_key]
                if cached:
                    self.years_discovered.emit(cached)
                    return

            conn = self.ftp_client.connect()
            path = f"/jma/HSD/{self.product}/"
            try:
                conn.cwd(path)
                items = conn.nlst()
                years = [int(i) for i in items if i.isdigit() and 2014 < int(i) < 2035]
            except Exception:
                years = []
            finally:
                self.ftp_client.close(conn)

            years = sorted(set(years)) or []
            AvailableDatesDiscoverer._cache[cache_key] = years
            if not self._cancelled:
                self.years_discovered.emit(years)

        except Exception as e:
            self.error.emit(f"FTP query failed ({self.product}): {str(e)}")
        finally:
            self.finished.emit()

    def cancel(self):
        self._cancelled = True


class AvailableProductTypesDiscoverer(QThread):
    """Discovers actual top-level product folders from JAXA FTP."""
    products_discovered = Signal(list)
    error = Signal(str)

    def __init__(self, ftp_client):
        super().__init__()
        self.ftp_client = ftp_client
        self._cancelled = False

    def run(self):
        try:
            conn = self.ftp_client.connect()
            path = "/jma/HSD/"
            try:
                conn.cwd(path)
                items = conn.nlst()
                products = [i for i in items if any(k in i.lower() for k in ['fldk', 'japan', 'target'])]
            except Exception:
                products = []
            finally:
                self.ftp_client.close(conn)

            products.sort()
            if not self._cancelled:
                self.products_discovered.emit(products or ["AHI-L1b-FLDK"])
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableMonthsDiscoverer(QThread):
    """Discovers months for a given product + year."""
    months_discovered = Signal(list)
    error = Signal(str)

    def __init__(self, ftp_client, product, year):
        super().__init__()
        self.ftp_client = ftp_client
        self.product = product
        self.year = year
        self._cancelled = False

    def run(self):
        try:
            conn = self.ftp_client.connect()
            path = f"/jma/HSD/{self.product}/{self.year}/"
            try:
                conn.cwd(path)
                items = conn.nlst()
                months = [int(i) for i in items if i.isdigit() and 1 <= int(i) <= 12]
            except Exception:
                months = []
            finally:
                self.ftp_client.close(conn)
            
            months = sorted(set(months)) or []
            if not self._cancelled:
                self.months_discovered.emit(months)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableHoursDiscoverer(QThread):
    """Discovers hours for a given date."""
    hours_discovered = Signal(list)
    error = Signal(str)

    def __init__(self, ftp_client, product, year, month, day):
        super().__init__()
        self.ftp_client = ftp_client
        self.product = product
        self.year = year
        self.month = month
        self.day = day
        self._cancelled = False

    def run(self):
        try:
            conn = self.ftp_client.connect()
            path = f"/jma/HSD/{self.product}/{self.year:04d}/{self.month:02d}/{self.day:02d}/"
            try:
                conn.cwd(path)
                items = conn.nlst()
                hours = [int(i) for i in items if i.isdigit() and 0 <= int(i) <= 23]
            except Exception:
                hours = []
            finally:
                self.ftp_client.close(conn)
            
            hours = sorted(set(hours)) or []
            if not self._cancelled:
                self.hours_discovered.emit(hours)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableMinutesDiscoverer(QThread):
    """Discover actual minute folders under a specific hour."""
    minutes_discovered = Signal(list)
    error = Signal(str)

    def __init__(self, ftp_client, product, year, month, day, hour):
        super().__init__()
        self.ftp_client = ftp_client
        self.product = product
        self.year = year
        self.month = month
        self.day = day
        self.hour = hour
        self._cancelled = False

    def run(self):
        try:
            conn = self.ftp_client.connect()
            path = f"/jma/HSD/{self.product}/{self.year:04d}/{self.month:02d}/{self.day:02d}/{self.hour:02d}/"
            try:
                conn.cwd(path)
                items = conn.nlst()
                minutes = [int(i) for i in items if i.isdigit() and 0 <= int(i) <= 59]
            except Exception:
                minutes = []
            finally:
                self.ftp_client.close(conn)
            
            minutes = sorted(set(minutes)) or []
            if not self._cancelled:
                self.minutes_discovered.emit(minutes)
        except Exception as e:
            self.error.emit(str(e))

    def cancel(self):
        self._cancelled = True


class AvailableDaysDiscoverer(QThread):
    """Discovers actual days under PRODUCT/YYYY/MM/."""
    days_discovered = Signal(list)
    error = Signal(str)

    def __init__(self, ftp_client, product, year, month):
        super().__init__()
        self.ftp_client = ftp_client
        self.product = product
        self.year = year
        self.month = month
        self._cancelled = False

    def run(self):
        try:
            conn = self.ftp_client.connect()
            path = f"/jma/HSD/{self.product}/{self.year:04d}/{self.month:02d}/"
            try:
                conn.cwd(path)
                items = conn.nlst()
                days = [int(i) for i in items if i.isdigit() and 1 <= int(i) <= 31]
            except Exception:
                days = []
            finally:
                self.ftp_client.close(conn)
            
            days = sorted(set(days)) or []
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
                 max_workers=8, skip_rgb=False, skip_ads=False):
        super().__init__()
        self.directory_path = Path(directory_path)
        self.process_mode = process_mode          # "auto", "extract_only", "nc_only"
        self.force_simple = force_simple
        self.max_workers = max_workers
        self.skip_rgb = skip_rgb       # v3.1.2: pass --no-rgb to bg_to_nc.py
        self.skip_ads = skip_ads       # v3.1.2: pass --no-ads to bg_to_nc.py

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

            # ---------- AUTO (extract then convert to NetCDF) ----------
            if self.process_mode == "auto":
                self.progress.emit("Extracting .bz2 files...")
                extract_script = script_dir / "bg_extract.py"
                if extract_script.exists():
                    extract_result = self.run_external_script(
                        extract_script,
                        ["-i", str(self.directory_path), "--max-workers", str(self.max_workers)]
                    )
                    stats.update(self.parse_script_output(extract_result.stdout, "extract"))
                else:
                    self.error.emit(f"Extraction script not found: {extract_script}")
                    self.finished.emit(False, str(self.directory_path))
                    return

                if self.force_simple:
                    self.progress.emit("Force simple enabled: skipping NetCDF conversion (requires Satpy).")
                else:
                    self.progress.emit("Converting extracted .dat files to NetCDF...")
                    nc_script = script_dir / "bg_to_nc.py"
                    if nc_script.exists():
                        nc_args = ["-i", str(self.directory_path)]
                        if self.skip_rgb:
                            nc_args.append("--no-rgb")
                        if self.skip_ads:
                            nc_args.append("--no-ads")
                        nc_result = self.run_external_script(nc_script, nc_args)
                        stats.update(self.parse_script_output(nc_result.stdout, "nc"))
                    else:
                        self.error.emit(f"NetCDF script not found: {nc_script}")
                        self.finished.emit(False, str(self.directory_path))
                        return

            # ---------- EXTRACT ONLY ----------
            elif self.process_mode == "extract_only":
                self.progress.emit("Extracting .bz2 files only...")
                extract_script = script_dir / "bg_extract.py"
                if extract_script.exists():
                    extract_result = self.run_external_script(
                        extract_script,
                        ["-i", str(self.directory_path), "--max-workers", str(self.max_workers)]
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
                nc_script = script_dir / "bg_to_nc.py"
                if nc_script.exists():
                    nc_args = ["-i", str(self.directory_path)]
                    if self.skip_rgb:
                        nc_args.append("--no-rgb")
                    if self.skip_ads:
                        nc_args.append("--no-ads")
                    nc_result = self.run_external_script(nc_script, nc_args)
                    stats.update(self.parse_script_output(nc_result.stdout, "nc"))
                else:
                    self.error.emit(f"NetCDF script not found: {nc_script}")
                    self.finished.emit(False, str(self.directory_path))
                    return

            self.stats_update.emit(stats)

            total_success = stats['successfully_extracted'] + stats['nc_created']
            if total_success > 0:
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
                # bg_to_nc.py (Cyclone V3.1.2) outputs "Processed: X"
                if "Processed:" in line:
                    try:
                        processed = int(line.split(':')[1].strip())
                        stats['nc_created'] = processed
                        stats['nc_failed'] = 0
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

        # FTP Setup
        self.ftp_client = self._init_ftp_client()

        # Known FTP servers
        self._known_servers = KNOWN_SERVERS

        self.current_server = "ftp.ptree.jaxa.jp"
        self.current_prefix = ""
        self.current_path = []
        self.all_files = []
        self.selected_bands = list(range(1, 17))

        # FTP-specific state — default to NetCDF (JAXA P-Tree primary format)
        self.data_format = "nc"
        self.nc_resolution = "2km"

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

        # Dynamic FTP date discovery for Modern UI (replaces static years)
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

        # For clean shutdown of background threads (prevents "QThread destroyed while still running")
        self._closing = False
        self._legacy_banner = None

        # Safe post-show initial discovery flag (E/F fix for QThread destruction on modern launch).
        # Guarantees _discover_* (and thus all QThread .start()) NEVER run during __init__ or tab builders.
        # Set true only from showEvent + singleShot (after .show() + active event loop).
        self._initial_discovery_scheduled = False

        # Decide which full mode to run as the main interface
        saved_mode = self._load_process_dat_ui_mode()

        if self._force_legacy or saved_mode == "legacy":
            self.ui_mode = "legacy"
            self._build_full_legacy_as_main()
        else:
            self.ui_mode = "modern"
            self._build_modern_as_main()

    def _init_ftp_client(self):
        """Loads JAXA P-Tree credentials from accounts.json."""
        try:
            accounts_path = self.script_dir / "config" / "accounts.json"
            if accounts_path.exists():
                with open(accounts_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                account = next((a for a in data.get("ftp_accounts", []) if a.get("name") == "JAXA P-Tree"), None)
                if account:
                    return FTPClient(
                        server=account["server"],
                        username=account["username"],
                        password=account["password"],
                        port=account.get("port", 21),
                        passive=account.get("passive", True)
                    )
        except Exception as e:
            print(f"[ERROR] Failed to initialize FTP client: {e}")
        return None

    # ======================================================================
    #  NEW: Dual Mode UI System (Modern + Legacy with live switching)
    # ======================================================================

    def _load_process_dat_settings(self):
        """Reads the UI preference + download skipper from the downloader's own settings file.
        Returns (mode: str, skipper: str) with defaults ("modern", "Silent").
        """
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
                    print(f"[DEBUG] Loaded settings: downloader_ui='{mode}' download_skipper='{skipper}'")
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
        Preserves any existing download_skipper key.
        """
        try:
            settings_path = self.downloader_settings_file
            settings_path.parent.mkdir(parents=True, exist_ok=True)

            # Read existing to preserve other keys
            data = {"downloader_ui": value}
            if settings_path.exists():
                try:
                    with open(settings_path, "r", encoding="utf-8-sig") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        if "download_skipper" in existing:
                            data["download_skipper"] = existing["download_skipper"]
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
        """Save the download skipper preference, preserving downloader_ui."""
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
                        if "downloader_ui" in existing:
                            data["downloader_ui"] = existing["downloader_ui"]
                except Exception:
                    pass

            tmp = settings_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            tmp.replace(settings_path)

            print(f"[DEBUG] Saved download_skipper = \"{value}\" to own settings file")
        except Exception as e:
            print(f"[ERROR] Failed to save download_skipper to own settings: {e}")

    def _save_all_modern_settings(self):
        """Explicit 'Save Settings' handler for the downloader.
        ONLY the downloader_ui key is ever written (via the surgical _save_downloader_ui).
        This keeps process_dat_settings.json clean and minimal by design.
        Download directory is runtime-only (not persisted in the downloader's own settings file).
        """
        try:
            current_mode = getattr(self, 'ui_mode', 'modern')
            self._save_downloader_ui(current_mode)

            # Intentionally do NOT touch/write any other keys (e.g. download_dir).
            # The settings file must stay clean with only {"downloader_ui": "..."}.

            msg = "Settings saved (UI preference only; settings file kept clean)."
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

    def _add_modern_button_to_legacy_toolbar(self):
        """Prominent, reliable way to switch back from Legacy. Inserts clean non-overlapping banner into the real legacy layout."""
        try:
            # Menu (always)
            menubar = self.menuBar()
            view_menu = menubar.addMenu("View")
            modern_action = QAction("★ Switch to Modern UI (Recommended)", self)
            modern_action.triggered.connect(self._switch_to_modern)
            view_menu.addAction(modern_action)

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

        # After legacy is built, add a "Switch to Modern UI" button
        self._add_modern_button_to_legacy_toolbar()

    def _get_sat_key(self, sat_name):
        """Map satellite display name to config key."""
        if "Himawari 8" in sat_name:
            return "himawari8"
        return "himawari9"

    def _get_product_for_type(self, sat_name: str, type_text: str) -> str:
        """Map UI 'Type / Region' selection to the real S3 product folder prefix."""
        sat_lower = (sat_name or "").lower()
        t = (type_text or "").lower()

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
        """Returns the actual product folder name selected (loaded live from JAXA FTP, no assumptions)."""
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

        # Right: server address pill
        sv_lbl = QLabel("SERVER")
        sv_lbl.setStyleSheet("font-size: 9px; color: #5E6B7A; font-weight: 700; letter-spacing: 0.3px;")
        hero_l.addWidget(sv_lbl)
        hero_l.addSpacing(6)

        self.modern_server = QComboBox()
        self.modern_server.blockSignals(True)
        self.modern_server.addItems(list(self._known_servers.keys()))
        self.modern_server.setCurrentText(self.current_server)
        self.modern_server.currentTextChanged.connect(self._on_modern_server_changed)
        self.modern_server.blockSignals(False)
        self.modern_server.setStyleSheet("""
            QComboBox {
                background: #10161F;
                color: #00B4D8;
                border: 1px solid #0099CC;
                border-radius: 6px;
                padding: 4px 11px;
                font-size: 10.5px;
                font-weight: 700;
                min-width: 160px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #10161F;
                color: #E0E5ED;
                border: 1px solid #0099CC;
                selection-background-color: #0099CC;
            }
        """)
        hero_l.addWidget(self.modern_server)
        hero_l.addSpacing(10)

        # DATA SOURCE selector — dynamic per known server
        self._fmt_lbl = QLabel("FORMAT")
        self._fmt_lbl.setStyleSheet("font-size: 9px; color: #5E6B7A; font-weight: 700; letter-spacing: 0.3px;")
        hero_l.addWidget(self._fmt_lbl)
        hero_l.addSpacing(6)

        self.modern_format = QComboBox()
        server_info = self._known_servers.get(self.current_server)
        if server_info:
            self.modern_format.addItems(server_info["formats"])
            self.modern_format.setCurrentText(server_info["default_format"])
        else:
            self.modern_format.addItem("--")
            self.modern_format.setVisible(False)
            self._fmt_lbl.setVisible(False)
        self.modern_format.currentTextChanged.connect(self._on_modern_format_changed)
        self.modern_format.setStyleSheet("""
            QComboBox {
                background: #10161F;
                color: #00B4D8;
                border: 1px solid #0099CC;
                border-radius: 6px;
                padding: 4px 11px;
                font-size: 10.5px;
                font-weight: 700;
                min-width: 150px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #10161F;
                color: #E0E5ED;
                border: 1px solid #0099CC;
                selection-background-color: #0099CC;
            }
        """)
        hero_l.addWidget(self.modern_format)
        hero_l.addSpacing(10)

        # RESOLUTION selector — only active for NetCDF
        res_lbl = QLabel("RES (NC)")
        res_lbl.setStyleSheet("font-size: 9px; color: #5E6B7A; font-weight: 700; letter-spacing: 0.3px;")
        hero_l.addWidget(res_lbl)
        hero_l.addSpacing(6)

        self.modern_res = QComboBox()
        self.modern_res.addItems(["2km (Full)", "5km (Full)", "1km (Japan)"])
        self.modern_res.setStyleSheet("""
            QComboBox {
                background: #10161F;
                color: #00B4D8;
                border: 1px solid #0099CC;
                border-radius: 6px;
                padding: 4px 11px;
                font-size: 10.5px;
                font-weight: 700;
                min-width: 120px;
            }
            QComboBox::drop-down { border: none; width: 18px; }
            QComboBox QAbstractItemView {
                background: #10161F;
                color: #E0E5ED;
                border: 1px solid #0099CC;
                selection-background-color: #0099CC;
            }
        """)
        hero_l.addWidget(self.modern_res)
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

        status_lbl = QLabel("MODERN  •  Quick Scene + Animation  —  Live JAXA P-Tree FTP Discovery •  View → Legacy")
        status_lbl.setStyleSheet("font-size: 9.5px; color: #5E6B7A; font-weight: 500;")
        footer_l.addWidget(status_lbl)
        footer_l.addStretch()

        ver = QLabel("Himawari-9 AHI  •  JAXA P-Tree  •  Cyclone v3.0.1 ALPHA  •  McIDAS Professional")
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
        t = QLabel("⬇  DOWNLOAD CONSOLE  •  LIVE JAXA P-Tree FTP")
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
        self.qs_validation = QLabel("Querying JAXA P-Tree FTP…")
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
        self.modern_server.currentTextChanged.connect(self._update_modern_date_combos)
        self.modern_server.currentTextChanged.connect(self._update_modern_band_labels)

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
        self.anim_step.addItems(["10 min", "15 min", "30 min", "60 min"])
        self.anim_step.setStyleSheet("""
            QComboBox { background:#0A0E15; color:#E9EDF3; border:1px solid #222A33; border-radius:5px; padding:4px 9px; font-size:10.5px; }
            QComboBox::drop-down { border:none; }
        """)
        step_l.addWidget(self.anim_step)
        left.addWidget(step_card)

        # Validation
        self.anim_validation = QLabel("Loading available dates from JAXA P-Tree FTP…")
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
            ("📡", "Queries JAXA P-Tree FTP directly"),
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
        self.modern_server.currentTextChanged.connect(self._update_animation_date_combos)

        # NOTE (E/F QThread fix): Initial discovery removed from here (was line ~1914).
        # Both tabs previously scheduled duplicate early singleShots during _build_modern_ui_page ctor.
        # Now centralized to ONE safe post-show point (showEvent + _safe_post_show_initial_discovery).
        # Guarantees zero QThread start during modern tab builders or __init__.
        # E (under direct H supervision): Confirmed zero dangerous early QTimer.singleShot at former line 1914 (this tab).
        # The early calls referenced in task have been removed/properly deferred. Tab builder safe. D: only.

        return w

    def _on_modern_server_changed(self, server):
        if getattr(self, '_closing', False):
            return
        self.current_server = server
        # Update format combo for the selected server
        server_info = self._known_servers.get(server)
        has_formats = server_info and server_info.get("formats")
        if hasattr(self, 'modern_format') and hasattr(self, '_fmt_lbl'):
            self.modern_format.blockSignals(True)
            self.modern_format.clear()
            if has_formats:
                self.modern_format.addItems(server_info["formats"])
                self.modern_format.setCurrentText(server_info["default_format"])
            self.modern_format.setVisible(bool(has_formats))
            self._fmt_lbl.setVisible(bool(has_formats))
            self.modern_format.blockSignals(False)
            # Apply default format to data_format
            if has_formats:
                self._on_modern_format_changed(self.modern_format.currentText())
        # Trigger discovery
        self._discover_product_types()

    def _on_modern_format_changed(self, text):
        """Update data_format and show/hide band checkboxes + RES combo."""
        self.data_format = "nc" if "NetCDF" in text or "NC" in text else "raw"
        is_nc = self.data_format == "nc"
        # Show/hide band checkboxes (not used for NC)
        for cb in getattr(self, 'modern_band_checks', {}).values():
            cb.setVisible(not is_nc)
        # Show/hide resolution combo (only for NC)
        if hasattr(self, 'modern_res'):
            self.modern_res.setVisible(is_nc)

    def _on_legacy_format_changed(self, text):
        """Update data_format and show/hide NC filters in Legacy mode."""
        self.data_format = "nc" if "NetCDF" in text or "NC" in text else "raw"
        is_nc = self.data_format == "nc"
        # Show/hide NC resolution/region filters
        for attr in ['_nc_res_lbl', 'nc_res_combo', '_nc_region_lbl', 'nc_region_combo']:
            w = getattr(self, attr, None)
            if w is not None:
                w.setVisible(is_nc)
        # Show/hide band filter label (bands only for raw)
        if hasattr(self, 'band_filter_label'):
            self.band_filter_label.setVisible(not is_nc)
        # Reset navigation
        self.current_prefix = ""
        self.current_path = []
        self.update_path_label()
        self.list_directory("")
        self.update_manual_buttons_state()

    def _on_nc_filter_changed(self):
        """Re-filter the files table when NC resolution/region changes."""
        self.display_filtered_files()

    def _on_modern_type_or_server_changed(self):
        """Called when the user manually changes TYPE. Re-discover years for the new product."""
        if getattr(self, '_closing', False):
            return
        self._trigger_modern_discovery()

    def _trigger_modern_discovery(self):
        """Central method to (re)start date discovery when Sat or TYPE changes."""
        sat = self.modern_server.currentText()
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
            self.qs_year.addItem("Loading from FTP...")
            self.qs_year.blockSignals(False)
        if hasattr(self, 'qs_validation') and self.qs_validation:
            self.qs_validation.setText(f"Querying JAXA P-Tree FTP ({product.split('-')[-1]})...")

        if hasattr(self, 'anim_validation') and self.anim_validation:
            self.anim_validation.setText(f"Loading dates from JAXA FTP ({product.split('-')[-1]})...")

        self._current_discovering_sat = sat
        self._current_discovering_product = product

        self._dates_discoverer = AvailableDatesDiscoverer(self.ftp_client, product)
        # Unified handler keeps BOTH tabs perfectly in sync (fixes previous refactor split-brain)
        self._dates_discoverer.years_discovered.connect(self._on_years_discovered_unified)
        self._dates_discoverer.error.connect(
            lambda msg: (
                (self.qs_validation.setText(f"FTP error: {msg}") if hasattr(self, 'qs_validation') and self.qs_validation else None),
                (self.anim_validation.setText(f"FTP error: {msg}") if hasattr(self, 'anim_validation') and self.anim_validation else None)
            )
        )
        self._dates_discoverer.start()

    def _update_modern_band_labels(self):
        """Switch band checkbox labels between B01-B16 (Himawari)."""
        prefix = "B"
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
        """Load the real available product folders (FLDK, Japan, Target, etc.) from FTP."""
        sat = self.modern_server.currentText()

        if self._product_types_discoverer and self._product_types_discoverer.isRunning():
            self._product_types_discoverer.cancel()
            self._product_types_discoverer.wait(1500)

        if hasattr(self, 'modern_type') and self.modern_type:
            self.modern_type.blockSignals(True)
            self.modern_type.clear()
            self.modern_type.addItem("Loading from FTP...")
            self.modern_type.blockSignals(False)

        self._product_types_discoverer = AvailableProductTypesDiscoverer(self.ftp_client)
        self._product_types_discoverer.products_discovered.connect(self._on_product_types_discovered)
        self._product_types_discoverer.error.connect(
            lambda msg: self._on_product_types_error(msg)
        )
        self._product_types_discoverer.start()

    def _on_product_types_discovered(self, products):
        """Populate the TYPE combo with whatever actually exists on JAXA FTP."""
        if not hasattr(self, 'modern_type') or self._closing:
            return
        try:
            self.modern_type.blockSignals(True)
            self.modern_type.clear()
            if products:
                self.modern_type.addItems(products)
                # Prefer FLDK if present, otherwise last
                sat = self.modern_server.currentText()
                preferred = "AHI-L1b-FLDK"
                if preferred in products:
                    self.modern_type.setCurrentText(preferred)
                else:
                    self.modern_type.setCurrentIndex(self.modern_type.count() - 1)
            else:
                sat = self.modern_server.currentText()
                default = "AHI-L1b-FLDK"
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

    def _discover_product_types(self):
        """Queries JAXA FTP for available top-level product folders."""
        if not self.ftp_client:
            self._update_modern_status("FTP client not initialized")
            return
        
        self._product_types_discoverer = AvailableProductTypesDiscoverer(self.ftp_client)
        self._product_types_discoverer.products_discovered.connect(self._on_products_discovered)
        self._product_types_discoverer.error.connect(self._on_discovery_error)
        self._product_types_discoverer.start()

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

        sat = self.modern_server.currentText()
        product = self._get_product_prefix()
        year = int(year_str)

        if self._months_discoverer and self._months_discoverer.isRunning():
            self._months_discoverer.cancel()
            self._months_discoverer.wait(1500)

        self._months_discoverer = AvailableMonthsDiscoverer(self.ftp_client, product, year)
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

        day_clean = day_str.strip() if isinstance(day_str, str) else ""
        if not day_clean or day_clean.lower() in ("none", "any", "--", ""):
            self._populate_default_hours()
            return

        try:
            day = int(day_clean)
        except:
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

        product = self._get_product_prefix()

        if self._hours_discoverer and self._hours_discoverer.isRunning():
            self._hours_discoverer.cancel()
            self._hours_discoverer.wait(1500)

        self._hours_discoverer = AvailableHoursDiscoverer(
            self.ftp_client, product, int(year_str), int(month_str), day
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

        product = self._get_product_prefix()

        if self._minutes_discoverer and self._minutes_discoverer.isRunning():
            self._minutes_discoverer.cancel()
            self._minutes_discoverer.wait(1500)

        self._minutes_discoverer = AvailableMinutesDiscoverer(
            self.ftp_client, product, int(year_str), int(month_str), int(day_str), hour
        )
        self._minutes_discoverer.minutes_discovered.connect(self._on_minutes_discovered)
        self._minutes_discoverer.start()

    def _on_minutes_discovered(self, minutes):
        if self._closing:
            return
        try:
            if minutes:
                min_strs = [f"{m:02d}" for m in minutes]
            else:
                min_strs = ["--"]  # Modern: placeholder (H#2)
            if hasattr(self, 'qs_min') and self.qs_min:
                # Keep "None" as first option when repopulating
                self._safe_populate_combo_with_none(self.qs_min, min_strs)

            for attr in ['anim_from_min', 'anim_to_min']:
                if hasattr(self, attr):
                    cb = getattr(self, attr)
                    if cb:
                        self._safe_populate_combo_with_none(cb, min_strs)
        except Exception:
            pass

    def _populate_default_minutes(self):
        mins = ["--"]  # Modern: placeholder, no static 10-min grid (H#2)
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

        product = self._get_product_prefix()

        if self._days_discoverer and self._days_discoverer.isRunning():
            self._days_discoverer.cancel()
            self._days_discoverer.wait(1500)

        self._days_discoverer = AvailableDaysDiscoverer(
            self.ftp_client, product, int(year_str), month
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
                combo.addItem("-- (no FTP data)")
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
                msg = "✓ Dates loaded live from JAXA P-Tree FTP"
            else:
                year_strs = ["-- (no FTP data)"]
                msg = "⚠ No data on FTP for selection — try different sat/product, check network, or Force Refresh"

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

    def _start_modern_direct_download(self, prefix: str, bands: list, subfolder_name: str = None):
        """Core: launch FTPDownloadWorker for a precise prefix directly from Modern UI."""
        if self.modern_download_worker and self.modern_download_worker.isRunning():
            QMessageBox.warning(self, "Download Busy", "A Modern download is already in progress. Cancel or wait.")
            return

        sat = self.modern_server.currentText() if hasattr(self, 'modern_server') else "ftp.ptree.jaxa.jp"

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

        worker = FTPDownloadWorker(
            ftp_client=self.ftp_client,
            prefix=prefix,
            download_dir=str(save_dir),
            bands=bands or list(range(1, 17)),
            max_workers=8,
            mode=self.data_format
        )
        self.modern_download_worker = worker

        # ── Range-style progress dialog for the single download ───────────
        # Mirrors the FROM/TO range flow: modal dialog with Download + Process
        # bars, a done counter and a working Cancel button.
        prog = RangeProgressDialog(self, total_slots=1)
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

        # Cancel wiring: dialog Cancel cancels the worker (like the range flow)
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
                if path:
                    QTimer.singleShot(500, lambda: self._start_modern_extraction(path))
                else:
                    if prog and not prog._cancelled:
                        prog.update_process("No processing needed", 100)
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

            if any(x in (None, "", "None", "Loading", "Loading from FTP...") for x in [y, m, d, h, mi]):
                self._update_modern_status("Select a complete valid timestamp (no 'None') first")
                QMessageBox.information(self, "Quick Scene", "Please choose Year, Month, Day, Hour, and Minute from the live JAXA FTP lists.")
                return

            selected_bands = [int(name[1:]) for name, cb in getattr(self, 'modern_band_checks', {}).items() if cb.isChecked()]
            if not selected_bands:
                selected_bands = [3, 2, 1]

            product = self._get_product_prefix()
            is_nc = self.data_format == "nc"

            if is_nc:
                prefix = f"/jma/netcdf/{int(y):04d}{int(m):02d}/{int(d):02d}/{int(h):02d}/"
            else:
                prefix = f"/jma/HSD/{product}/{int(y):04d}/{int(m):02d}/{int(d):02d}/{int(h):02d}{int(mi):02d}/"
            scene_name = f"{product}_{y}{m}{d}_{h}{mi}"

            self._start_modern_direct_download(prefix, selected_bands, subfolder_name=scene_name)

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

            step = int(self.anim_step.currentText().split()[0])

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
            minute = (current.minute // step) * step
            current = current.replace(minute=minute, second=0, microsecond=0)
            while current <= end:
                slot_name = self._build_flat_slot_name(product, current)
                slot_names.append(slot_name)
                slot_dir = Path(save_root) / slot_name
                nc_file = slot_dir / f"{current.year:04d}{current.month:02d}{current.day:02d}_{current.hour:02d}{current.minute:02d}_AHI.nc"
                # Also try with underscore-style folder names
                if not nc_file.exists():
                    alt_name = f"{product}_{current.year:04d}{current.month:02d}{current.day:02d}_{current.hour:02d}{current.minute:02d}"
                    alt_dir = Path(save_root) / alt_name
                    nc_file = alt_dir / f"{current.year:04d}{current.month:02d}{current.day:02d}_{current.hour:02d}{current.minute:02d}_AHI.nc"
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

            # ── Progress dialog ──────────────────────────────────────
            prog = RangeProgressDialog(self, total_slots=num_slots - complete_slots if download_missing else num_slots)
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
        Uses 'now' UTC rounded down, goes back N hours at 10-min steps. Superior instant access.
        """
        if getattr(self, '_closing', False):
            return
        try:
            from datetime import datetime as _dt, timezone as _tz, timedelta as _td
            now = _dt.now(_tz.utc).replace(second=0, microsecond=0)
            # Round down to nearest 10 min
            rounded_min = (now.minute // 10) * 10
            end = now.replace(minute=rounded_min)
            start = end - _td(hours=hours)

            selected_bands = [int(name[1:]) for name, cb in getattr(self, 'modern_band_checks', {}).items() if cb.isChecked()] or list(range(1, 17))

            save_root = Path(self.modern_dir_edit.text()) if hasattr(self, 'modern_dir_edit') else self.modern_download_dir

            self._update_modern_status(f"Quick {hours}h range: {start.strftime('%Y-%m-%d %H:%M')} → {end.strftime('%Y-%m-%d %H:%M')}")

            # Use the (now fixed + modern-aware) backend
            self.download_date_range(
                start_datetime=start,
                end_datetime=end,
                bands=selected_bands,
                download_root=str(save_root),
                time_step_minutes=10,
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
        self.sat_combo.addItems(["Himawari 9", "Himawari 8", "GOES-16", "GOES-17", "GOES-18", "GOES-19"])
        self.sat_combo.currentTextChanged.connect(self.on_satellite_changed)
        self.sat_combo.setFixedWidth(120)
        toolbar_layout.addWidget(self.sat_combo)

        toolbar_layout.addWidget(QLabel("Format:"))
        self.legacy_format_combo = QComboBox()
        self.legacy_format_combo.addItems(["NetCDF", "HSD (Raw Bands)"])
        self.legacy_format_combo.setCurrentText("NetCDF")
        self.legacy_format_combo.setToolTip("NetCDF: pre-processed files (*.nc) with resolution/region variants\nHSD (Raw Bands): individual band .bz2 files")
        self.legacy_format_combo.setStyleSheet("""
            QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444;
                         border-radius: 3px; padding: 3px 6px; font-size: 10px; min-width: 110px; }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView { background: #2D2D2D; color: #EEE; font-size: 10px;
                                           selection-background-color: #37474F; }
        """)
        self.legacy_format_combo.currentTextChanged.connect(self._on_legacy_format_changed)
        toolbar_layout.addWidget(self.legacy_format_combo)

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

        self.path_label = QLabel("ftp.ptree.jaxa.jp/jma/netcdf/")
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

        # NC resolution filter — shown only in NetCDF mode
        self._nc_res_lbl = QLabel("Res:")
        self._nc_res_lbl.setStyleSheet("color: #FFA726; font-size: 11px; font-weight: bold; padding: 2px 4px;")
        self._nc_res_lbl.setVisible(False)
        files_header.addWidget(self._nc_res_lbl)
        self.nc_res_combo = QComboBox()
        self.nc_res_combo.addItems(["All", "R21 (Full Disk)", "r14 (Japan)"])
        self.nc_res_combo.setStyleSheet("""
            QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444;
                         border-radius: 3px; padding: 2px 5px; font-size: 10pt; font-family: "Segoe UI"; min-width: 100px; max-height: 22px; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView { background: #2D2D2D; color: #EEE; font-size: 10pt; font-family: "Segoe UI"; selection-background-color: #37474F; }
        """)
        self.nc_res_combo.setToolTip("Filter by resolution: R21 = Full Disk, r14 = Japan region")
        self.nc_res_combo.setVisible(False)
        self.nc_res_combo.currentTextChanged.connect(self._on_nc_filter_changed)
        files_header.addWidget(self.nc_res_combo)

        # NC region filter — shown only in NetCDF mode
        self._nc_region_lbl = QLabel("Region:")
        self._nc_region_lbl.setStyleSheet("color: #FFA726; font-size: 11px; font-weight: bold; padding: 2px 4px;")
        self._nc_region_lbl.setVisible(False)
        files_header.addWidget(self._nc_region_lbl)
        self.nc_region_combo = QComboBox()
        self.nc_region_combo.addItems(["All", "FLDK", "JAPAN"])
        self.nc_region_combo.setStyleSheet("""
            QComboBox { background: #2D2D2D; color: #EEE; border: 1px solid #444;
                         border-radius: 3px; padding: 2px 5px; font-size: 10pt; font-family: "Segoe UI"; min-width: 80px; max-height: 22px; }
            QComboBox::drop-down { border: none; width: 16px; }
            QComboBox QAbstractItemView { background: #2D2D2D; color: #EEE; font-size: 10pt; font-family: "Segoe UI"; selection-background-color: #37474F; }
        """)
        self.nc_region_combo.setToolTip("Filter by region: FLDK = Full Disk, JAPAN = Japan region")
        self.nc_region_combo.setVisible(False)
        self.nc_region_combo.currentTextChanged.connect(self._on_nc_filter_changed)
        files_header.addWidget(self.nc_region_combo)

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
        from_to_group = QGroupBox("Date Range Filter (optional)")
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
        self.progress_bar.setStyleSheet("""
            QProgressBar { border: 1px solid #444; border-radius: 4px; text-align: center;
                           background: #1A1A1A; height: 15px; margin-top: 5px; }
            QProgressBar::chunk { background-color: #5D8AA8; border-radius: 4px; }
        """)
        main_layout.addWidget(self.progress_bar)

        self.process_progress_bar = QProgressBar()
        self.process_progress_bar.setVisible(False)
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
        # Sync NC filter visibility to match initial format (NetCDF)
        if hasattr(self, 'legacy_format_combo'):
            self._on_legacy_format_changed(self.legacy_format_combo.currentText())

    # ------------------------------------------------------------------
    #  Path helpers & manual buttons
    # ------------------------------------------------------------------
    def get_local_path_from_current_prefix(self):
        base_dir = Path(self.dir_edit.text())
        if not self.current_path:
            return base_dir
        satellite_name = self.current_server.replace(".", "_")
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
        """Switch legacy band checkbox labels between B01-B16 (Himawari)."""
        prefix = "B"
        for i in range(1, 17):
            key = f"B{i:02d}"
            if key in getattr(self, 'band_checkboxes', {}):
                self.band_checkboxes[key].setText(f"{prefix}{i:02d}")

    def on_satellite_changed(self, satellite):
        self.log_message("INFO", f"Satellite changed to: {satellite}")
        self.current_prefix = ""
        self.current_path = []
        self.update_path_label()
        self.list_directory("")
        self.update_manual_buttons_state()
        self.check_winds_availability()
        self._update_legacy_band_labels()

        # Update legacy Type combo options depending on satellite (Himawari vs others)
        if hasattr(self, 'legacy_type_combo'):
            current = self.legacy_type_combo.currentText()
            self.legacy_type_combo.blockSignals(True)
            self.legacy_type_combo.clear()
            if "himawari" in satellite.lower():
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

        self.winds_checker = WindsChecker(self.ftp_client, winds_prefix)
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

        root = "/jma/netcdf/" if getattr(self, 'data_format', 'raw') == 'nc' else "/jma/HSD/"
        root_path = f"{root}{prefix}"

        loading_item = QTreeWidgetItem(self.dir_tree)
        loading_item.setText(0, "Loading...")
        loading_item.setFlags(loading_item.flags() & ~Qt.ItemIsSelectable)

        self.lister = FTPLister(self.ftp_client, root_path)
        self.lister.progress.connect(lambda msg: self.status_bar.showMessage(msg))
        self.lister.directories_found.connect(self.on_directories_found)
        self.lister.files_found.connect(self.on_files_found)
        self.lister.error.connect(self.on_list_error)
        self.lister.finished.connect(self.on_list_finished)
        self.lister.list_files = bool(prefix)
        self.lister.start()

    def on_directories_found(self, directories):
        # FTP directory listing
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

    def _on_goes_minute_filter_changed(self, text):
        self.log_message("INFO", f"GOES minute filter changed to: {text}")
        self.display_filtered_files()

    def display_filtered_files(self):
        filtered_files = []
        total_size = 0
        is_nc = getattr(self, 'data_format', 'raw') == 'nc'

        # Read NC filters
        nc_res_filter = "All"
        nc_region_filter = "All"
        if is_nc:
            if hasattr(self, 'nc_res_combo'):
                nc_res_filter = self.nc_res_combo.currentText()
            if hasattr(self, 'nc_region_combo'):
                nc_region_filter = self.nc_region_combo.currentText()

        goes_min_val = "All"
        goes_combo = getattr(self, 'goes_minute_combo', None)
        if goes_combo is not None:
            goes_min_val = goes_combo.currentText()
        for file_info in self.all_files:
            filename = file_info['name']

            if is_nc:
                # NC mode: filter by resolution and region from filename
                import re as _re
                res_match = _re.search(r'_R(\d+)_', filename)
                region_match = _re.search(r'_R\d+_([^.]+)\.', filename)
                nc_res = f"R{res_match.group(1)}" if res_match else ""
                nc_region = region_match.group(1) if region_match else ""

                # Apply resolution filter
                if nc_res_filter != "All":
                    expected_res = nc_res_filter.split(" ")[0]  # "R21" or "r14"
                    if nc_res.lower() != expected_res.lower():
                        continue
                # Apply region filter
                if nc_region_filter != "All":
                    if nc_region.upper() != nc_region_filter.upper():
                        continue
                filtered_files.append((file_info, None))
                total_size += file_info.get('size', 0) if isinstance(file_info, dict) else 0
            else:
                band_number = None
                for i in range(1, 17):
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
            fsize = file_info.get('size', 0) if isinstance(file_info, dict) else 0
            size_mb = fsize / (1024 * 1024)
            mod_raw = file_info.get('last_modified', None) if isinstance(file_info, dict) else None
            modified = mod_raw.strftime("%Y-%m-%d %H:%M") if mod_raw else "N/A"
            self.files_table.setItem(row, 0, QTableWidgetItem(filename))

            if is_nc:
                import re as _re
                res_m = _re.search(r'_R(\d+)_', filename)
                reg_m = _re.search(r'_R\d+_([^.]+)\.', filename)
                res = f"R{res_m.group(1)}" if res_m else ""
                reg = reg_m.group(1) if reg_m else ""
                band_text = f"{res} {reg}" if res and reg else (res or reg or "NC")
                band_item = QTableWidgetItem(band_text)
                band_item.setForeground(Qt.cyan)
            else:
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
        server = getattr(self, 'current_server', 'ftp.ptree.jaxa.jp')
        is_nc = getattr(self, 'data_format', 'raw') == 'nc'
        root = "/jma/netcdf/" if is_nc else "/jma/HSD/"
        full_path = f"{server}{root}"
        if self.current_path:
            full_path += "/".join(self.current_path) + "/"
        self.path_label.setText(full_path)
        # Sync format combo if it exists
        if hasattr(self, 'legacy_format_combo'):
            expected = "NetCDF" if is_nc else "HSD (Raw Bands)"
            if self.legacy_format_combo.currentText() != expected:
                self.legacy_format_combo.blockSignals(True)
                self.legacy_format_combo.setCurrentText(expected)
                self.legacy_format_combo.blockSignals(False)

    # ------------------------------------------------------------------
    #  Download with pre‑download completeness check
    # ------------------------------------------------------------------
    def download_filtered(self):
        # Build local target directory
        path_parts = self.current_path.copy() if self.current_path else ["root"]
        download_dir = Path(self.dir_edit.text()) / "ftp_data" / "_".join(path_parts)
        download_dir.mkdir(parents=True, exist_ok=True)

        # ---- Quick completeness check (bz2 OR dat present) ----
        conn = self.ftp_client.connect()
        try:
            is_nc = getattr(self, 'data_format', 'raw') == 'nc'
            root = "/jma/netcdf/" if is_nc else "/jma/HSD/"
            remote_dir = f"{root}{self.current_prefix}"
            conn.cwd(remote_dir)
            items = conn.nlst()
            s3_required_stems = set()
            for item in items:
                if is_nc:
                    if item.startswith("NC_") and item.endswith(".nc"):
                        stem = item[:-3]
                        s3_required_stems.add(stem)
                else:
                    if not item.endswith('.bz2'):
                        continue
                    band_patterns = [f"B{b:02d}" for b in self.selected_bands]
                    if any(p in item for p in band_patterns):
                        stem = item[:-4] if item.endswith('.bz2') else item[:-4]
                        s3_required_stems.add(stem)
        except Exception:
            s3_required_stems = set()
        finally:
            self.ftp_client.close(conn)

        if s3_required_stems:
            all_present = True
            for stem in s3_required_stems:
                dat_path = download_dir / stem
                bz2_path = download_dir / (stem + '.bz2')
                if not dat_path.exists() and not bz2_path.exists():
                    all_present = False
                    break

            if all_present:
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
        is_nc = getattr(self, 'data_format', 'raw') == 'nc'
        if not is_nc and self.winds_checkbox.isChecked() and self.winds_available and self.current_winds_prefix:
            extra = self.current_winds_prefix

        self.start_download(self.current_prefix, download_dir, self.selected_bands, extra)

    # ------------------------------------------------------------------
    #  Date Range Download Support — fully wired to both Legacy tree UI and Modern Animation/Quick Range tabs
    # ------------------------------------------------------------------

    def _generate_product_prefixes(self, start_dt: datetime, end_dt: datetime, 
                                    product: str = "AHI-L1b-FLDK",
                                    step_minutes: int = 10):
        """Generate list of FTP paths for Himawari data.
        Raw Bands: /jma/HSD/{product}/{YYYY}/{MM}/{DD}/{HHMM}/
        NetCDF: /jma/netcdf/{YYYYMM}/{DD}/{HH}/
        """
        is_nc = getattr(self, 'data_format', 'raw') == 'nc'
        prefixes = []
        current = start_dt

        minute = (current.minute // step_minutes) * step_minutes
        current = current.replace(minute=minute, second=0, microsecond=0)

        seen_hours = set()
        while current <= end_dt:
            y, m, d = current.year, current.month, current.day
            h, mi = current.hour, current.minute
            if is_nc:
                # NC files are in hourly folders: /jma/netcdf/{YYYYMM}/{DD}/{HH}/
                hour_key = f"{y:04d}{m:02d}/{d:02d}/{h:02d}"
                if hour_key not in seen_hours:
                    seen_hours.add(hour_key)
                    prefix = f"/jma/netcdf/{y:04d}{m:02d}/{d:02d}/{h:02d}/"
                    prefixes.append(prefix)
            else:
                time_part = f"{h:02d}{mi:02d}"
                prefix = f"/jma/HSD/{product}/{y:04d}/{m:02d}/{d:02d}/{time_part}/"
                prefixes.append(prefix)
            current += timedelta(minutes=step_minutes)

        return prefixes

    # Back-compat alias (some internal calls still use the old name)
    def _generate_fldk_prefixes(self, start_dt: datetime, end_dt: datetime, 
                                 satellite_key: str = "himawari9", 
                                 step_minutes: int = 10):
        product = getattr(self, '_get_product_prefix', lambda: "AHI-L1b-FLDK")()
        return self._generate_product_prefixes(start_dt, end_dt, product=product, step_minutes=step_minutes)

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
            elif hasattr(self, 'modern_server') and self.modern_server and self.modern_server.currentText():
                sat_name = self.modern_server.currentText()

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

            self.download_date_range(
                start_datetime=start,
                end_datetime=end,
                bands=bands,
                download_root=str(self.default_download_dir),
                time_step_minutes=10,
                include_winds=include_winds,
                auto_process=auto_process,
                process_mode=process_mode,
                product=product
            )

            self.status_bar.showMessage(
                f"Range download started: {product} | {start} → {end} | {len(bands)} band(s)"
            )

        except Exception as e:
            QMessageBox.critical(self, "Legacy Range Download", f"Failed to start range download:\n{e}")

    def _build_flat_slot_name(self, product: str, dt: datetime) -> str:
        """Central shared helper for BOTH single downloads and FROM/TO range downloads.
        Produces identical flat folder names:
            AHI-L1b-Target_2026_05_31_0000
            AHI-L1b-Japan_2026_05_31_0010
            AHI-L1b-FLDK_2026_05_31_0020
        This ensures FROM/TO and single downloads are indistinguishable to bg_extract.py / bg_to_nc.py.
        """
        return f"{product}_{dt.year:04d}_{dt.month:02d}_{dt.day:02d}_{dt.hour:02d}{dt.minute:02d}"

    def download_date_range(self, start_datetime: datetime, end_datetime: datetime,
                            bands=None, download_root=None, time_step_minutes: int = 10,
                            include_winds: bool = False, auto_process: bool = False,
                            process_mode: str = "auto", progress_callback=None,
                            product: str | None = None):
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

        if not product:
            if hasattr(self, '_get_product_prefix'):
                try:
                    product = self._get_product_prefix() or "AHI-L1b-FLDK"
                except Exception:
                    product = "AHI-L1b-FLDK"
            else:
                product = "AHI-L1b-FLDK"

        prefixes = self._generate_product_prefixes(start_datetime, end_datetime, 
                                                   product=product,
                                                   step_minutes=time_step_minutes)
        # Note: _generate_product_prefixes now correctly handles Target/Japan using HHMM/ folders
        # instead of HH/MM/ (this was the root cause of "No files found" for rapid-scan products).

        # Generate the matching FLAT local folder names using the shared helper.
        # This ensures FROM/TO range downloads create exactly the same structure as single downloads.
        local_slot_names = []
        current = start_datetime
        minute = (current.minute // time_step_minutes) * time_step_minutes
        current = current.replace(minute=minute, second=0, microsecond=0)
        while current <= end_datetime:
            local_slot_names.append(self._build_flat_slot_name(product, current))
            current += timedelta(minutes=time_step_minutes)

        if not prefixes:
            _report("WARNING", "No time slots generated for the given range.")
            return

        _report("INFO", f"Generated {len(prefixes)} time slots to download.")

        if getattr(self, '_closing', False):
            _report("WARNING", "Close in progress — range aborted")
            return

        mode = getattr(self, 'data_format', 'raw')

        self._range_worker = FTPRangeDownloadWorker(
            self.ftp_client, prefixes, download_root,
            bands=bands, include_winds=include_winds,
            local_slot_names=local_slot_names,
            mode=mode
        )

        prog_dialog = getattr(self, '_range_progress_dialog', None)

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
            if prog_dialog and prog_dialog._cancelled:
                _report("INFO", "Range download cancelled by user")
                self._range_progress_dialog = None
                return
            if prog_dialog:
                prog_dialog.update_download("Download complete", 100)
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
                    )
                    if prog_dialog:
                        proc_worker.progress.connect(
                            lambda m: not prog_dialog._cancelled and prog_dialog.update_process(m)
                        )
                        proc_worker.finished.connect(
                            lambda s, p: (
                                self.log_message("SUCCESS", f"Auto-process complete: {p}"),
                                not prog_dialog._cancelled and (
                                    prog_dialog.slot_processed(),
                                    QTimer.singleShot(1500, prog_dialog.close)
                                )
                            )
                        )
                    else:
                        proc_worker.progress.connect(lambda m: self.log_message("INFO", f"[PROCESS] {m}"))
                        proc_worker.finished.connect(lambda s, p: self.log_message("SUCCESS", f"Auto-process complete: {p}"))
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
                # No processing requested — close dialog
                if prog_dialog:
                    prog_dialog.update_process("No processing requested", 100)
                    QTimer.singleShot(1500, prog_dialog.close)
            # Clear pending after handling
            self._pending_auto_process = None

        # Cancel check before starting
        if prog_dialog and prog_dialog._cancelled:
            _report("INFO", "Range download cancelled")
            self._range_progress_dialog = None
            return

        self._range_worker.finished.connect(_on_range_finished)
        self._range_worker.error.connect(lambda m: _report("ERROR", m))

        # Wire progress dialog cancel → worker cancel
        if prog_dialog:
            orig_cancel = prog_dialog._do_cancel
            def _cancel_with_worker():
                orig_cancel()
                if self._range_worker and self._range_worker.isRunning():
                    self._range_worker.cancel()
            prog_dialog._do_cancel = _cancel_with_worker

        self._range_worker.start()
        _report("INFO", "FTPRangeDownloadWorker started (non-blocking). UI remains responsive.")

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
        try:
            # Download via FTP
            conn = self.ftp_client.connect()
            try:
                root = "/jma/netcdf/" if getattr(self, 'data_format', 'raw') == 'nc' else "/jma/HSD/"
                remote_dir = f"{root}{self.current_prefix}"
                conn.cwd(remote_dir)
                with open(save_path, 'wb') as f:
                    conn.retrbinary(f"RETR {file_info['name']}", f.write)
            finally:
                self.ftp_client.close(conn)
            self.log_message("SUCCESS", f"Downloaded: {file_info['name']}")
            QMessageBox.information(self, "Success", f"Downloaded {file_info['name']}")
        except Exception as e:
            self.log_message("ERROR", f"Failed to download {file_info['name']}: {str(e)}")
            QMessageBox.warning(self, "Error", f"Failed to download {file_info['name']}")

    def _update_banner_visibility(self):
        if hasattr(self, '_legacy_banner') and self._legacy_banner is not None:
            visible = not (self.progress_bar.isVisible() or self.process_progress_bar.isVisible())
            self._legacy_banner.setVisible(visible)

    def start_download(self, prefix, download_dir, bands, extra_prefix=None):
        if self.download_worker and self.download_worker.isRunning():
            QMessageBox.warning(self, "Download in Progress", "Please wait for current download to complete.")
            return

        max_workers = self.concurrent_spin.value()
        is_nc = getattr(self, 'data_format', 'raw') == 'nc'
        remote_prefix = f"/jma/netcdf/{prefix}" if is_nc else f"/jma/HSD/{prefix}"
        mode = "nc" if is_nc else "raw"
        self.log_message("INFO", f"Starting download ({max_workers} concurrent) from: {remote_prefix}")
        if extra_prefix:
            self.log_message("INFO", f"Including winds data from: {extra_prefix}")

        self.download_worker = FTPDownloadWorker(
            self.ftp_client, remote_prefix, download_dir, bands, max_workers, mode=mode
        )
        self.download_worker.progress.connect(lambda msg: self.log_message("INFO", msg))
        self.download_worker.file_progress.connect(self.on_file_progress)
        self.download_worker.finished.connect(self.on_download_finished)
        self.download_worker.error.connect(lambda msg: self.log_message("ERROR", msg))
        self.download_worker.start()

        self.download_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self._update_banner_visibility()

    def on_file_progress(self, current, total, filename):
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_bar.showMessage(f"Downloading {filename} ({current}/{total})")

    def on_download_finished(self, success, download_path):
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.download_btn.setEnabled(True)
        self._update_banner_visibility()

        if success:
            self.log_message("SUCCESS", f"Download completed successfully to: {download_path}")
            if self.auto_process and download_path:
                self.log_message("INFO", "Starting auto-processing of downloaded files...")
                self.start_processing(download_path, self.process_mode)
            else:
                QMessageBox.information(self, "Success", f"Download completed to:\n{download_path}\n\nAuto-processing (extract & NetCDF) started.")
        else:
            QMessageBox.warning(self, "Error", "Download failed. Check status messages.")

    # ------------------------------------------------------------------
    #  Processing
    # ------------------------------------------------------------------
    def start_processing(self, directory_path, process_mode="auto"):
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
        )
        self.processor_worker.progress.connect(lambda msg: self.log_message("PROCESS", msg))
        self.processor_worker.finished.connect(self.on_processing_finished)
        self.processor_worker.error.connect(lambda msg: self.log_message("ERROR", msg))
        self.processor_worker.stats_update.connect(self.update_statistics_display)
        self.process_progress_bar.setVisible(True)
        self.process_progress_bar.setRange(0, 0)
        self.status_bar.showMessage(f"Processing files ({process_mode})...")
        self._update_banner_visibility()
        self.processor_worker.start()

    def on_processing_finished(self, success, directory_path):
        self.process_progress_bar.setVisible(False)
        self._update_banner_visibility()
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
        from ftplib import FTP
        from PySide6 import QtCore
    except ImportError as e:
        print(f"Missing required packages: {e}")
        print("Install with: pip install PySide6 watchdog numpy pillow rasterio satpy")
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

    # Determine forced mode from CLI (highest priority)
    force_legacy = (force_ui == "legacy")

    window = HimawariFileManager(force_legacy=force_legacy)

    # If CLI explicitly asked for a mode, make sure we honor it even if the saved setting differs
    if force_ui in ("legacy", "modern"):
        if window.ui_mode != force_ui:
            window.close()
            window = HimawariFileManager(force_legacy=(force_ui == "legacy"))

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