import sys
import os
import boto3
import traceback
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from pathlib import Path
from datetime import datetime
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QProcess
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QComboBox, QTreeWidget,
    QTreeWidgetItem, QLineEdit, QCheckBox, QFrame, QFileDialog,
    QMessageBox, QProgressBar, QTextEdit, QGroupBox, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QListWidget, QListWidgetItem, QGridLayout, QScrollArea, QSpinBox
)
from PySide6.QtGui import QFont, QIcon, QPixmap


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
 
    def __init__(self, bucket, prefix, download_dir, bands=None, max_workers=8):
        super().__init__()
        self.bucket = bucket
        self.prefix = prefix
        self.download_dir = Path(download_dir)
        self.bands = bands if bands else []
        self.max_workers = max_workers
        self._cancelled = False
     
    def run(self):
        download_path = ""
        try:
            s3_client = boto3.client(
                's3',
                config=Config(signature_version=UNSIGNED)
            )
         
            self.progress.emit(f"Listing files in: s3://{self.bucket}/{self.prefix}")
         
            files_to_download = []
            paginator = s3_client.get_paginator('list_objects_v2')
         
            for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix):
                if 'Contents' in page:
                    for obj in page['Contents']:
                        key = obj['Key']
                     
                        if key.endswith('/'):
                            continue
                         
                        if self.bands:
                            download_file = any(f"B{band:02d}" in key for band in self.bands)
                            if not download_file:
                                continue
                     
                        files_to_download.append({
                            'key': key,
                            'size': obj['Size']
                        })
         
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


class HimawariProcessorWorker(QThread):
    """Thread to run Himawari processing by calling external bg_*.py scripts"""
    progress = Signal(str)
    finished = Signal(bool, str)
    error = Signal(str)
    stats_update = Signal(dict)
 
    def __init__(self, directory_path, process_mode="auto", create_rgb=True, force_simple=False, max_workers=8):
        super().__init__()
        self.directory_path = Path(directory_path)
        self.process_mode = process_mode
        self.create_rgb = create_rgb
        self.force_simple = force_simple
        self.max_workers = max_workers
     
    def run(self):
        stats = {
            'total_extracted': 0,
            'successfully_extracted': 0,
            'extraction_failed': 0,
            'total_combined': 0,
            'successfully_combined': 0,
            'combination_failed': 0,
            'tiff_created': 0,
            'rgb_created': 0,
            'satpy_decodes': 0,
            'satpy_failures': 0
        }
     
        try:
            script_dir = Path(__file__).resolve().parent
         
            self.progress.emit(f"Starting Himawari processing in: {self.directory_path}")
            self.progress.emit(f"Processing mode: {self.process_mode}")
            self.progress.emit(f"Max concurrent workers: {self.max_workers}")
         
            if self.process_mode == "auto":
                self.progress.emit("Step 1: Extracting .bz2 files...")
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
             
                if not self.force_simple:
                    self.progress.emit("Step 2: Combining .dat files into GeoTIFFs...")
                    decode_script = script_dir / "bg_decode.py"
                    if decode_script.exists():
                        decode_result = self.run_external_script(
                            decode_script,
                            ["-i", str(self.directory_path)]
                        )
                        stats.update(self.parse_script_output(decode_result.stdout, "combine"))
                     
                        self.cleanup_dat_files(self.directory_path)
                    else:
                        self.error.emit(f"Decoding script not found: {decode_script}")
             
                if self.create_rgb and not self.force_simple:
                    self.progress.emit("Step 3: Creating RGB products...")
                    product_script = script_dir / "bg_product.py"
                    if product_script.exists():
                        try:
                            product_result = self.run_external_script(
                                product_script,
                                ["-i", str(self.directory_path), "--all"]
                            )
                         
                            product_stats = self.parse_script_output(product_result.stdout, "rgb")
                            stats['rgb_created'] = product_stats.get('rgb_created', 0)
                            self.progress.emit(f"Created {stats['rgb_created']} RGB products")
                         
                        except Exception as e:
                            self.progress.emit(f"Warning: Failed to create RGB products: {str(e)}")
                    else:
                        self.error.emit(f"Product script not found: {product_script}")
             
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
                 
            elif self.process_mode == "combine_only":
                if self.force_simple:
                    self.error.emit("Cannot combine without Satpy (force_simple is enabled)")
                    self.finished.emit(False, str(self.directory_path))
                    return
             
                self.progress.emit("Combining .dat files only...")
                decode_script = script_dir / "bg_decode.py"
                if decode_script.exists():
                    decode_result = self.run_external_script(
                        decode_script,
                        ["-i", str(self.directory_path)]
                    )
                    stats.update(self.parse_script_output(decode_result.stdout, "combine"))
                 
                    self.cleanup_dat_files(self.directory_path)
                else:
                    self.error.emit(f"Decoding script not found: {decode_script}")
                    self.finished.emit(False, str(self.directory_path))
                    return
                 
            elif self.process_mode == "rgb_only":
                if not self.create_rgb:
                    self.error.emit("RGB creation is disabled")
                    self.finished.emit(False, str(self.directory_path))
                    return
             
                if self.force_simple:
                    self.error.emit("Cannot create RGB without Satpy (force_simple is enabled)")
                    self.finished.emit(False, str(self.directory_path))
                    return
             
                self.progress.emit("Creating RGB products only...")
                product_script = script_dir / "bg_product.py"
                if product_script.exists():
                    try:
                        product_result = self.run_external_script(
                            product_script,
                            ["-i", str(self.directory_path), "--all"]
                        )
                     
                        product_stats = self.parse_script_output(product_result.stdout, "rgb")
                        stats['rgb_created'] = product_stats.get('rgb_created', 0)
                        self.progress.emit(f"Created {stats['rgb_created']} RGB products")
                     
                    except Exception as e:
                        self.progress.emit(f"Warning: Failed to create RGB products: {str(e)}")
                else:
                    self.error.emit(f"Product script not found: {product_script}")
                    self.finished.emit(False, str(self.directory_path))
                    return
         
            self.stats_update.emit(stats)
         
            total_success = (
                stats['successfully_extracted'] +
                stats['successfully_combined'] +
                stats['rgb_created']
            )
         
            if total_success > 0:
                self.progress.emit(f"Processing completed with {total_success} successful operations")
                self.finished.emit(True, str(self.directory_path))
            else:
                self.progress.emit("Processing completed (all files were already processed)")
                self.finished.emit(True, str(self.directory_path))
             
        except Exception as e:
            error_msg = f"Error during processing: {str(e)}"
            traceback.print_exc()
            self.error.emit(error_msg)
            self.finished.emit(False, str(self.directory_path))
 
    def run_external_script(self, script_path, args):
        """Run an external Python script and capture its output"""
        cmd = [sys.executable, str(script_path)] + args
        self.progress.emit(f"Running: {' '.join(cmd)}")
     
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
     
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line and not line.startswith("[!]"):
                self.progress.emit(line)
     
        if result.returncode != 0:
            error_msg = f"Script {script_path.name} failed: {result.stderr}"
            self.error.emit(error_msg)
            raise Exception(error_msg)
     
        return result
 
    def parse_script_output(self, output: str, script_type: str) -> dict:
        """Parse statistics from script output"""
        stats = {}
     
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
         
            if script_type == "extract":
                if "Extracted:" in line and "Total to extract:" in line:
                    parts = line.split(',')
                    for part in parts:
                        if "Extracted:" in part:
                            try:
                                stats['successfully_extracted'] = int(part.split(':')[1].strip())
                            except:
                                pass
                        elif "Total to extract:" in part:
                            try:
                                stats['total_extracted'] = int(part.split(':')[1].strip())
                                stats['extraction_failed'] = stats['total_extracted'] - stats.get('successfully_extracted', 0)
                            except:
                                pass
             
                if "Successfully extracted:" in line:
                    try:
                        stats['successfully_extracted'] = int(line.split(':')[1].strip())
                    except:
                        pass
                elif "Total extracted:" in line:
                    try:
                        stats['total_extracted'] = int(line.split(':')[1].strip())
                    except:
                        pass
                 
            elif script_type == "combine":
                if "Successfully processed:" in line:
                    try:
                        stats['successfully_combined'] = int(line.split(':')[1].strip())
                        stats['tiff_created'] = stats['successfully_combined']
                    except:
                        pass
                elif "Total groups:" in line:
                    try:
                        stats['total_combined'] = int(line.split(':')[1].strip())
                        stats['combination_failed'] = stats['total_combined'] - stats.get('successfully_combined', 0)
                    except:
                        pass
                elif "Processed groups:" in line and "Total groups:" in line:
                    parts = line.split(',')
                    for part in parts:
                        if "Processed groups:" in part:
                            try:
                                stats['successfully_combined'] = int(part.split(':')[1].strip())
                                stats['tiff_created'] = stats['successfully_combined']
                            except:
                                pass
                        elif "Total groups:" in part:
                            try:
                                stats['total_combined'] = int(part.split(':')[1].strip())
                                stats['combination_failed'] = stats['total_combined'] - stats.get('successfully_combined', 0)
                            except:
                                pass
                 
            elif script_type == "rgb":
                if "Products created:" in line:
                    try:
                        stats['rgb_created'] = int(line.split(':')[1].strip())
                    except:
                        pass
                if "COMPLETE" in line and "created" in line:
                    try:
                        parts = line.split(',')
                        for part in parts:
                            if 'created' in part:
                                stats['rgb_created'] = int(part.split()[-1])
                    except:
                        pass
     
        return stats
 
    def cleanup_dat_files(self, directory_path):
        """Delete .dat files after successful decoding"""
        try:
            dat_files = []
            for ext in ['.dat', '.DAT']:
                dat_files.extend(list(directory_path.rglob(f"*{ext}")))
         
            deleted_count = 0
         
            for dat_file in dat_files:
                try:
                    dat_file.unlink()
                    deleted_count += 1
                except Exception as e:
                    self.progress.emit(f"Warning: Could not delete {dat_file.name}: {str(e)}")
         
            if deleted_count > 0:
                self.progress.emit(f"Cleaned up {deleted_count} .dat files")
             
        except Exception as e:
            self.progress.emit(f"Warning: Could not clean up .dat files: {str(e)}")


class HimawariFileManager(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Himawari S3 File Manager with Advanced Processing")
        self.resize(1400, 800)

        app_font = QFont("Segoe UI", 9)
        QApplication.setFont(app_font)

        self.script_dir = Path(__file__).resolve().parent.parent
        self.default_download_dir = self.script_dir / "public" / "Download"

        self.satellites = {
            "Himawari 9": "noaa-himawari9",
            "Himawari 8": "noaa-himawari8"
        }

        self.current_bucket = "noaa-himawari9"
        self.current_prefix = ""
        self.current_path = []
        self.all_files = []
        self.selected_bands = list(range(1, 17))

        # RGB product definitions with icons
        self.rgb_products = {
            "True Color": {
                "bands": ["B03", "B02", "B01"],
                "description": "Natural color (Red, Green, Blue)",
                "required_bands": [1, 2, 3],
                "type": "rgb",
                "icon": ""
            },
            "Sandwich": {
                "bands": ["B13", "B13", "B02"],
                "description": "Severe storm tracking",
                "required_bands": [2, 13],
                "type": "rgb",
                "icon": ""
            },
            "Vegetation": {
                "bands": ["B04", "B03", "B02"],
                "description": "False color for vegetation",
                "required_bands": [2, 3, 4],
                "type": "rgb",
                "icon": ""
            },
            "Day Convection": {
                "bands": ["B13", "B10", "B07"],
                "description": "Daytime convection monitoring",
                "required_bands": [7, 10, 13],
                "type": "rgb",
                "icon": ""
            },
            "Air Mass": {
                "bands": ["B13", "B10", "B08"],
                "description": "Air mass analysis for jet streams",
                "required_bands": [8, 10, 13],
                "type": "rgb",
                "icon": ""
            },
            "Dust": {
                "bands": ["B13", "B11", "B08"],
                "description": "Dust detection",
                "required_bands": [8, 11, 13],
                "type": "rgb",
                "icon": ""
            },
            "Night Micro": {
                "bands": ["B13", "B10", "B07"],
                "description": "Nighttime cloud microphysics",
                "required_bands": [7, 10, 13],
                "type": "rgb",
                "icon": ""
            },
            "Cloud Phase": {
                "bands": ["B11", "B08", "B13"],
                "description": "Cloud phase discrimination",
                "required_bands": [8, 11, 13],
                "type": "rgb",
                "icon": ""
            },
            "Snow Fog": {
                "bands": ["B04", "B02", "B01"],
                "description": "Snow and fog discrimination",
                "required_bands": [1, 2, 4],
                "type": "rgb",
                "icon": ""
            },
            "Natural+IR": {
                "bands": ["B03", "B02", "B01", "B13"],
                "description": "Enhanced natural with IR",
                "required_bands": [1, 2, 3, 13],
                "type": "enhanced",
                "icon": ""
            },
            "Single Bands": {
                "bands": ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B09", "B10", "B11", "B12", "B13", "B14", "B15", "B16"],
                "description": "Individual GeoTIFF for each band",
                "required_bands": [],
                "type": "tiff",
                "icon": ""
            },
            "All RGB": {
                "bands": ["All"],
                "description": "Create all available RGB products",
                "required_bands": [],
                "type": "all_rgb",
                "icon": ""
            }
        }

        self.lister = None
        self.download_worker = None
        self.processor_worker = None

        self.auto_process = True
        self.process_mode = "auto"
        self.create_rgb = True
        self.force_simple = False

        self.selected_products = ["All RGB"]

        self.init_ui()
        self.update_product_checkboxes_state()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(10, 10, 10, 10)

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

        toolbar_layout.addWidget(QLabel("Satellite:"))
        self.sat_combo = QComboBox()
        self.sat_combo.addItems(["Himawari 9", "Himawari 8"])
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
            QPushButton:hover {
                background: #455A64;
            }
            QPushButton:disabled {
                background: #444;
                color: #777;
            }
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
            QPushButton:hover {
                background: #4A6572;
            }
        """)
        self.refresh_btn.clicked.connect(self.refresh_current)
        toolbar_layout.addWidget(self.refresh_btn)

        self.path_label = QLabel("s3://noaa-himawari9/")
        self.path_label.setStyleSheet("color: #FFA726; font-weight: bold; font-size: 12px; padding: 0 10px;")
        toolbar_layout.addWidget(self.path_label, 1)

        toolbar_layout.addStretch()
        main_layout.addWidget(toolbar)

        # Main content area
        content_widget = QWidget()
        content_layout = QHBoxLayout(content_widget)
        content_layout.setSpacing(10)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # ===== LEFT PANEL - DIRECTORY BROWSER =====
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
                background: #1A1A1A;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 4px;
                font-family: Consolas, monospace;
                font-size: 11px;
            }
            QTreeWidget::item {
                padding: 5px;
            }
            QTreeWidget::item:selected {
                background: #2D5A8A;
                color: white;
            }
            QTreeWidget::item:hover {
                background: #333;
            }
        """)
        self.dir_tree.itemDoubleClicked.connect(self.on_dir_double_clicked)
        left_layout.addWidget(self.dir_tree, 1)

        content_layout.addWidget(left_panel)

        # ===== MIDDLE PANEL - FILES =====
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

        middle_layout.addLayout(files_header)

        self.files_table = QTableWidget()
        self.files_table.setColumnCount(5)
        self.files_table.setHorizontalHeaderLabels(["Filename", "Band", "Size", "Modified", "Action"])
        self.files_table.horizontalHeader().setStretchLastSection(False)
        self.files_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.files_table.setStyleSheet("""
            QTableWidget {
                background: #1A1A1A;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 4px;
                gridline-color: #333;
            }
            QTableWidget::item {
                padding: 5px;
            }
            QHeaderView::section {
                background: #2D2D2D;
                color: #5D8AA8;
                padding: 5px;
                border: none;
            }
        """)
        self.files_table.setAlternatingRowColors(True)
        middle_layout.addWidget(self.files_table, 1)

        content_layout.addWidget(middle_panel, 1)

        # ===== RIGHT PANEL - CONTROLS =====
        right_panel = QWidget()
        right_panel.setMinimumWidth(300)
        right_panel.setMaximumWidth(450)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(10)

        # Band selection
        band_group = QGroupBox("Band Selection")
        band_group.setStyleSheet("""
            QGroupBox {
                color: #FF9800;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 0px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)

        band_layout = QVBoxLayout(band_group)

        band_grid = QGridLayout()
        self.band_checkboxes = {}
        for i in range(1, 17):
            checkbox = QCheckBox(f"B{i:02d}")
            checkbox.setChecked(True)
            checkbox.setStyleSheet("""
                QCheckBox {
                    color: #EEE;
                    padding: 3px;
                    font-size: 10px;
                }
                QCheckBox::indicator {
                    width: 14px;
                    height: 14px;
                }
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
                QPushButton {
                    background: #37474F;
                    color: white;
                    padding: 4px 8px;
                    border-radius: 2px;
                    font-size: 10px;
                    border: 1px solid #555;
                }
                QPushButton:hover {
                    background: #455A64;
                }
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

        # Download controls
        download_group = QGroupBox("Download Settings")
        download_group.setStyleSheet("""
            QGroupBox {
                color: #5D8AA8;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
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
            QPushButton {
                background: #5D8AA8;
                color: white;
                padding: 5px;
                border-radius: 3px;
                border: 1px solid #555;
            }
            QPushButton:hover {
                background: #4A6572;
            }
        """)
        browse_btn.clicked.connect(self.browse_directory)
        dir_layout.addWidget(browse_btn)
        download_layout.addLayout(dir_layout)

        self.estimated_size_label = QLabel("Estimated: 0 MB")
        self.estimated_size_label.setStyleSheet("color: #4CAF50; font-size: 11px; font-weight: bold; padding: 2px 0;")
        download_layout.addWidget(self.estimated_size_label)

        processing_layout = QVBoxLayout()

        self.auto_process_checkbox = QCheckBox("Auto-process after download")
        self.auto_process_checkbox.setChecked(self.auto_process)
        self.auto_process_checkbox.setStyleSheet("color: #EEE;")
        self.auto_process_checkbox.stateChanged.connect(self.on_auto_process_changed)
        processing_layout.addWidget(self.auto_process_checkbox)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Mode:"))

        self.process_mode_combo = QComboBox()
        self.process_mode_combo.addItems([
            "Full Auto",
            "Extract Only",
            "Combine Only",
            "RGB Only"
        ])
        self.process_mode_combo.currentIndexChanged.connect(self.on_process_mode_changed)
        self.process_mode_combo.setStyleSheet("""
            QComboBox {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
                min-width: 120px;
                font-size: 11px;
            }
        """)
        mode_layout.addWidget(self.process_mode_combo, 1)
        processing_layout.addLayout(mode_layout)

        self.force_simple_checkbox = QCheckBox("Force simple (skip Satpy)")
        self.force_simple_checkbox.setChecked(self.force_simple)
        self.force_simple_checkbox.setStyleSheet("color: #EEE; font-size: 11px;")
        self.force_simple_checkbox.stateChanged.connect(self.on_force_simple_changed)
        processing_layout.addWidget(self.force_simple_checkbox)

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

        right_layout.addWidget(download_group)

        # ===== PRODUCTS SELECTION - GRID LAYOUT LIKE BANDS =====
        products_group = QGroupBox("RGB Products")
        products_group.setStyleSheet("""
            QGroupBox {
                color: #8B5CF6;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)

        products_layout = QVBoxLayout(products_group)

        # Product info label
        self.product_info_label = QLabel("Select products to generate")
        self.product_info_label.setStyleSheet("color: #AAA; font-size: 10px; padding: 2px;")
        products_layout.addWidget(self.product_info_label)

        # Products grid - similar to bands grid
        products_grid = QGridLayout()
        products_grid.setSpacing(5)
        self.product_checkboxes = {}

        # Create product checkboxes in a grid (3 columns)
        product_items = list(self.rgb_products.items())
        for idx, (product_name, product_info) in enumerate(product_items):
            checkbox = QCheckBox(f"{product_info['icon']} {product_name}")
            
            # Set default states
            if product_name == "All RGB":
                checkbox.setChecked(True)
                checkbox.setStyleSheet("""
                    QCheckBox {
                        color: #4CAF50;
                        font-weight: bold;
                        padding: 5px;
                        font-size: 10px;
                        background: #2D3A2D;
                        border-radius: 3px;
                    }
                    QCheckBox::indicator {
                        width: 14px;
                        height: 14px;
                    }
                    QCheckBox:disabled {
                        color: #555;
                        background: #222;
                    }
                """)
            elif product_info["type"] == "tiff":
                checkbox.setChecked(True)
                checkbox.setStyleSheet("""
                    QCheckBox {
                        color: #2196F3;
                        padding: 5px;
                        font-size: 10px;
                        background: #2D3A4D;
                        border-radius: 3px;
                    }
                    QCheckBox::indicator {
                        width: 14px;
                        height: 14px;
                    }
                    QCheckBox:disabled {
                        color: #555;
                        background: #222;
                    }
                """)
            else:
                checkbox.setChecked(False)
                checkbox.setStyleSheet("""
                    QCheckBox {
                        color: #EEE;
                        padding: 5px;
                        font-size: 10px;
                        background: #2D2D2D;
                        border-radius: 3px;
                    }
                    QCheckBox::indicator {
                        width: 14px;
                        height: 14px;
                    }
                    QCheckBox:disabled {
                        color: #555;
                        background: #222;
                    }
                    QCheckBox:hover:!disabled {
                        background: #3D3D3D;
                    }
                """)

            tooltip = f"{product_info.get('description', '')}\nBands: {', '.join(product_info.get('bands', []))}"
            checkbox.setToolTip(tooltip)
            checkbox.stateChanged.connect(self.on_product_selection_changed)
            self.product_checkboxes[product_name] = checkbox

            # Grid layout - 3 columns
            row = idx // 3
            col = idx % 3
            products_grid.addWidget(checkbox, row, col)

        products_layout.addLayout(products_grid)

        # Product control buttons
        product_buttons = QHBoxLayout()
        select_all_products_btn = QPushButton("All")
        select_none_products_btn = QPushButton("None")
        select_default_btn = QPushButton("Default")

        for btn in [select_all_products_btn, select_none_products_btn, select_default_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background: #37474F;
                    color: white;
                    padding: 4px 8px;
                    border-radius: 2px;
                    font-size: 10px;
                    border: 1px solid #555;
                }
                QPushButton:hover {
                    background: #455A64;
                }
            """)
            btn.setFixedHeight(22)

        select_all_products_btn.clicked.connect(self.select_all_products)
        select_none_products_btn.clicked.connect(self.select_no_products)
        select_default_btn.clicked.connect(self.select_default_products)
        product_buttons.addWidget(select_all_products_btn)
        product_buttons.addWidget(select_none_products_btn)
        product_buttons.addWidget(select_default_btn)
        product_buttons.addStretch()

        # Selected products count
        self.selected_products_label = QLabel("Selected: 2 products")
        self.selected_products_label.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")
        product_buttons.addWidget(self.selected_products_label)
        products_layout.addLayout(product_buttons)

        right_layout.addWidget(products_group)

        # Manual processing buttons
        manual_frame = QFrame()
        manual_frame.setStyleSheet("QFrame { background: #333; border-radius: 5px; padding: 8px; }")
        manual_layout = QVBoxLayout(manual_frame)

        manual_buttons = QGridLayout()

        self.manual_extract_btn = QPushButton("Extract")
        self.manual_combine_btn = QPushButton("Decode")
        self.manual_rgb_btn = QPushButton("Product")
        self.manual_full_btn = QPushButton("Full")

        for btn in [self.manual_extract_btn, self.manual_combine_btn, self.manual_rgb_btn, self.manual_full_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background: #37474F;
                    color: white;
                    font-size: 11px;
                    border-radius: 3px;
                    padding: 6px;
                    border: 1px solid #555;
                }
                QPushButton:hover {
                    background: #455A64;
                }
                QPushButton:disabled {
                    background: #444;
                    color: #777;
                }
            """)
            btn.setVisible(not self.auto_process)

        self.manual_extract_btn.clicked.connect(lambda: self.run_manual_processing("extract_only"))
        self.manual_combine_btn.clicked.connect(lambda: self.run_manual_processing("combine_only"))
        self.manual_rgb_btn.clicked.connect(lambda: self.run_manual_processing("rgb_only"))
        self.manual_full_btn.clicked.connect(lambda: self.run_manual_processing("auto"))

        manual_buttons.addWidget(self.manual_extract_btn, 0, 0)
        manual_buttons.addWidget(self.manual_combine_btn, 0, 1)
        manual_buttons.addWidget(self.manual_rgb_btn, 1, 0)
        manual_buttons.addWidget(self.manual_full_btn, 1, 1)
        manual_layout.addLayout(manual_buttons)

        right_layout.addWidget(manual_frame)

        # Main download button
        self.download_btn = QPushButton("DOWNLOAD SELECTED FILES")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background: #2E7D32;
                color: white;
                font-size: 13px;
                font-weight: bold;
                border-radius: 4px;
                padding: 10px;
                margin-top: 5px;
                border: none;
            }
            QPushButton:hover {
                background: #388E3C;
            }
            QPushButton:disabled {
                background: #555;
                color: #999;
            }
        """)
        self.download_btn.clicked.connect(self.download_filtered)
        right_layout.addWidget(self.download_btn)

        # Statistics
        stats_group = QGroupBox("Statistics")
        stats_group.setStyleSheet("""
            QGroupBox {
                color: #4CAF50;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)

        stats_layout = QGridLayout(stats_group)

        self.stats_labels = {
            'extracted': QLabel("Extracted: 0"),
            'combined': QLabel("Combined: 0"),
            'tiff_created': QLabel("GeoTIFF: 0"),
            'rgb_created': QLabel("RGB: No"),
            'satpy_decodes': QLabel("Satpy: 0"),
            'satpy_failures': QLabel("Failures: 0")
        }

        for i, (key, label) in enumerate(self.stats_labels.items()):
            label.setStyleSheet("color: #EEE; font-size: 10px;")
            stats_layout.addWidget(label, i // 2, i % 2)

        right_layout.addWidget(stats_group)

        content_layout.addWidget(right_panel)

        main_layout.addWidget(content_widget, 1)

        # Progress bars at bottom
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 4px;
                text-align: center;
                background: #1A1A1A;
                height: 15px;
                margin-top: 5px;
            }
            QProgressBar::chunk {
                background-color: #5D8AA8;
                border-radius: 4px;
            }
        """)
        main_layout.addWidget(self.progress_bar)

        self.process_progress_bar = QProgressBar()
        self.process_progress_bar.setVisible(False)
        self.process_progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #444;
                border-radius: 4px;
                text-align: center;
                background: #1A1A1A;
                height: 15px;
                margin-top: 5px;
            }
            QProgressBar::chunk {
                background-color: #FF9800;
                border-radius: 4px;
            }
        """)
        main_layout.addWidget(self.process_progress_bar)

        # Status bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

        # Set window style
        self.setStyleSheet("""
            QMainWindow {
                background: #222;
                color: #EEE;
            }
            QLabel {
                color: #EEE;
            }
            QComboBox, QLineEdit {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
            }
        """)

        self.log_message("INFO", f"Auto-processing {'enabled' if self.auto_process else 'disabled'}")
        self.log_message("INFO", f"Processing mode: {self.process_mode}")

        self.list_directory("")
        self.update_manual_buttons_state()

    def get_local_path_from_current_prefix(self):
        """Return the local download path corresponding to the current S3 prefix."""
        base_dir = Path(self.dir_edit.text())
        if not self.current_path:
            return base_dir

        satellite_name = self.current_bucket.replace("noaa-", "")
        subfolder = "_".join(self.current_path)
        local_path = base_dir / satellite_name / subfolder
        return local_path

    def update_manual_buttons_state(self):
        """Enable/disable manual processing buttons based on existence of the target local folder."""
        target_dir = self.get_local_path_from_current_prefix()
        exists = target_dir.exists()
        self.manual_extract_btn.setEnabled(exists)
        self.manual_combine_btn.setEnabled(exists)
        self.manual_rgb_btn.setEnabled(exists)
        self.manual_full_btn.setEnabled(exists)

    def run_manual_processing(self, process_mode):
        """Manually trigger processing on the folder matching the current S3 view."""
        target_dir = self.get_local_path_from_current_prefix()

        if not self.current_path:
            reply = QMessageBox.question(
                self,
                "Process All Downloads?",
                f"You are at the S3 root.\n\nDo you want to process ALL downloaded data in:\n{target_dir} ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.No:
                return

        if not target_dir.exists():
            QMessageBox.warning(
                self,
                "Directory Not Found",
                f"The local folder does not exist yet:\n{target_dir}\n\nPlease download the data first."
            )
            return

        if process_mode in ["extract_only", "auto"]:
            bz2_files = list(target_dir.rglob("*.bz2"))
            if not bz2_files:
                QMessageBox.information(self, "No Files",
                                      f"No .bz2 files found in:\n{target_dir}")
                return

        if process_mode in ["combine_only", "auto"]:
            dat_files = list(target_dir.rglob("*.dat"))
            if not dat_files:
                QMessageBox.information(self, "No Files",
                                      f"No .dat files found in:\n{target_dir}")
                return

        if process_mode == "rgb_only":
            rgb_selected = any(
                product in self.selected_products and
                self.rgb_products.get(product, {}).get("type") in ["rgb", "all_rgb", "enhanced"]
                for product in self.selected_products
            )
            if not rgb_selected:
                QMessageBox.warning(self, "No RGB Products",
                                  "No RGB products selected for generation.")
                return

        process_names = {
            "auto": "Full processing (extract, combine, GeoTIFF, RGB)",
            "extract_only": "Extract .bz2 files only",
            "combine_only": "Combine .DAT segments only",
            "rgb_only": "Generate RGB products only"
        }

        self.log_message("INFO", f"Starting {process_names[process_mode]} in: {target_dir}")
        self.log_message("INFO", f"Selected products: {', '.join(self.selected_products)}")
        self.start_processing(str(target_dir), process_mode)

    def on_dir_double_clicked(self, item, column):
        """Navigate into a directory"""
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

    def go_back(self):
        """Go back to parent directory"""
        if self.current_path:
            self.current_path.pop()
            self.current_prefix = "/".join(self.current_path) + "/" if self.current_path else ""
            self.update_path_label()
            self.list_directory(self.current_prefix)
            self.back_btn.setEnabled(len(self.current_path) > 0)
            self.update_manual_buttons_state()

    def refresh_current(self):
        """Refresh current directory"""
        self.list_directory(self.current_prefix)
        self.update_manual_buttons_state()

    def on_satellite_changed(self, satellite):
        self.current_bucket = self.satellites[satellite]
        self.current_prefix = ""
        self.current_path = []
        self.update_path_label()
        self.list_directory("")
        self.update_manual_buttons_state()
        self.log_message("INFO", f"Selected satellite: {satellite}")

    def update_product_checkboxes_state(self):
        """Update product checkbox states based on selected bands"""
        for product_name, product_info in self.rgb_products.items():
            checkbox = self.product_checkboxes[product_name]

            if product_name == "All RGB":
                can_create_any = False
                for other_name, other_info in self.rgb_products.items():
                    if other_info["type"] == "rgb" and other_name != product_name:
                        if self.can_create_product(other_info):
                            can_create_any = True
                            break
                checkbox.setEnabled(can_create_any)
                if not can_create_any:
                    checkbox.setToolTip("No RGB products available with selected bands")
                else:
                    checkbox.setToolTip("Create all RGB products that have required bands available")
                continue

            if product_name == "Single Bands":
                checkbox.setEnabled(len(self.selected_bands) > 0)
                if len(self.selected_bands) == 0:
                    checkbox.setToolTip("No bands selected for GeoTIFF creation")
                else:
                    checkbox.setToolTip("Create individual GeoTIFF files for each available band in /Sat directory")
                continue

            if self.can_create_product(product_info):
                checkbox.setEnabled(True)
                bands = ", ".join(product_info["bands"])
                checkbox.setToolTip(f"{product_info['description']}\n\nRequired bands: {bands}\n\n✅ Available")
                # Update background color to show enabled state
                if product_info["type"] == "tiff":
                    checkbox.setStyleSheet("""
                        QCheckBox {
                            color: #2196F3;
                            padding: 5px;
                            font-size: 10px;
                            background: #2D3A4D;
                            border-radius: 3px;
                        }
                        QCheckBox::indicator {
                            width: 14px;
                            height: 14px;
                        }
                        QCheckBox:hover:!disabled {
                            background: #3D4A5D;
                        }
                    """)
                else:
                    checkbox.setStyleSheet("""
                        QCheckBox {
                            color: #EEE;
                            padding: 5px;
                            font-size: 10px;
                            background: #2D2D2D;
                            border-radius: 3px;
                        }
                        QCheckBox::indicator {
                            width: 14px;
                            height: 14px;
                        }
                        QCheckBox:hover:!disabled {
                            background: #3D3D3D;
                        }
                    """)
            else:
                checkbox.setEnabled(False)
                missing = self.get_missing_bands(product_info)
                bands = ", ".join(product_info["bands"])
                checkbox.setToolTip(f"{product_info['description']}\n\nRequired bands: {bands}\n\n❌ Missing bands: {', '.join(missing)}")
                checkbox.setStyleSheet("""
                    QCheckBox {
                        color: #555;
                        padding: 5px;
                        font-size: 10px;
                        background: #222;
                        border-radius: 3px;
                    }
                    QCheckBox::indicator {
                        width: 14px;
                        height: 14px;
                    }
                """)

    def can_create_product(self, product_info):
        """Check if a product can be created with currently selected bands"""
        required_bands = product_info.get("required_bands", [])

        if not required_bands:
            return len(self.selected_bands) > 0

        return all(band in self.selected_bands for band in required_bands)

    def get_missing_bands(self, product_info):
        """Get list of missing bands for a product"""
        required_bands = product_info.get("required_bands", [])
        missing = []
        for band in required_bands:
            if band not in self.selected_bands:
                missing.append(f"B{band:02d}")
        return missing

    def on_band_selection_changed(self):
        """Update selected bands and refresh file display"""
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

        self.update_product_checkboxes_state()
        self.display_filtered_files()

    def on_auto_process_changed(self):
        """Handle auto-processing checkbox change"""
        self.auto_process = self.auto_process_checkbox.isChecked()

        self.manual_extract_btn.setVisible(not self.auto_process)
        self.manual_combine_btn.setVisible(not self.auto_process)
        self.manual_rgb_btn.setVisible(not self.auto_process)
        self.manual_full_btn.setVisible(not self.auto_process)

        status = "enabled" if self.auto_process else "disabled"
        self.log_message("INFO", f"Auto-processing {status}")

    def on_process_mode_changed(self, index):
        """Handle processing mode combo box change"""
        mode_map = {
            0: "auto",
            1: "extract_only",
            2: "combine_only",
            3: "rgb_only"
        }
        self.process_mode = mode_map.get(index, "auto")
        self.log_message("INFO", f"Processing mode set to: {self.process_mode}")

    def on_force_simple_changed(self):
        """Handle force simple checkbox change"""
        self.force_simple = self.force_simple_checkbox.isChecked()
        status = "enabled" if self.force_simple else "disabled"
        self.log_message("INFO", f"Force simple combination {status}")

    def on_product_selection_changed(self):
        """Handle product checkbox changes"""
        sender = self.sender()
        if not sender:
            return

        for checkbox in self.product_checkboxes.values():
            checkbox.blockSignals(True)

        try:
            self.selected_products = []
            for product_name, checkbox in self.product_checkboxes.items():
                if checkbox.isChecked():
                    self.selected_products.append(product_name)

            product_name = None
            for name, checkbox in self.product_checkboxes.items():
                if checkbox == sender:
                    product_name = name
                    break

            if product_name:
                product_info = self.rgb_products.get(product_name)

                if product_name == "All RGB":
                    if sender.isChecked():
                        for p_name, checkbox in self.product_checkboxes.items():
                            p_info = self.rgb_products.get(p_name)
                            if p_info and p_info.get("type") == "rgb" and p_name != "All RGB":
                                checkbox.setChecked(False)

                elif product_info and product_info.get("type") == "rgb" and sender.isChecked():
                    all_checkbox = self.product_checkboxes.get("All RGB")
                    if all_checkbox and all_checkbox.isChecked():
                        all_checkbox.setChecked(False)

            self.selected_products = []
            for product_name, checkbox in self.product_checkboxes.items():
                if checkbox.isChecked():
                    self.selected_products.append(product_name)

            # Update selected products count label
            self.selected_products_label.setText(f"Selected: {len(self.selected_products)} products")

            # Update product info label
            if self.selected_products:
                self.product_info_label.setText(f"Ready to generate: {', '.join(self.selected_products)}")
            else:
                self.product_info_label.setText("Select products to generate")

            self.log_message("INFO", f"Products selected: {len(self.selected_products)}")

        finally:
            for checkbox in self.product_checkboxes.values():
                checkbox.blockSignals(False)

    def select_all_products(self):
        """Select all available products"""
        for checkbox in self.product_checkboxes.values():
            checkbox.blockSignals(True)
        try:
            for product_name, checkbox in self.product_checkboxes.items():
                if checkbox.isEnabled():
                    checkbox.setChecked(True)
            self.selected_products = [pn for pn, cb in self.product_checkboxes.items() if cb.isChecked()]
            self.selected_products_label.setText(f"Selected: {len(self.selected_products)} products")
            self.log_message("INFO", f"Selected all products. Total: {len(self.selected_products)}")
        finally:
            for checkbox in self.product_checkboxes.values():
                checkbox.blockSignals(False)

    def select_no_products(self):
        """Deselect all products"""
        for checkbox in self.product_checkboxes.values():
            checkbox.blockSignals(True)
        try:
            for checkbox in self.product_checkboxes.values():
                checkbox.setChecked(False)
            self.selected_products = []
            self.selected_products_label.setText("Selected: 0 products")
            self.product_info_label.setText("Select products to generate")
            self.log_message("INFO", "Deselected all products.")
        finally:
            for checkbox in self.product_checkboxes.values():
                checkbox.blockSignals(False)

    def select_default_products(self):
        """Select default products"""
        for checkbox in self.product_checkboxes.values():
            checkbox.blockSignals(True)
        try:
            for product_name, checkbox in self.product_checkboxes.items():
                if product_name == "All RGB" or product_name == "Single Bands":
                    checkbox.setChecked(True)
                else:
                    checkbox.setChecked(False)
            self.selected_products = [pn for pn, cb in self.product_checkboxes.items() if cb.isChecked()]
            self.selected_products_label.setText(f"Selected: {len(self.selected_products)} products")
            self.log_message("INFO", f"Selected default products. Total: {len(self.selected_products)}")
        finally:
            for checkbox in self.product_checkboxes.values():
                checkbox.blockSignals(False)

    def list_directory(self, prefix):
        """List directories and files in the given prefix"""
        if self.lister and self.lister.isRunning():
            return

        self.dir_tree.clear()
        self.files_table.setRowCount(0)
        self.all_files = []

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
        """Populate directory tree with found directories"""
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
        elif not self.current_prefix:
            no_dirs_item = QTreeWidgetItem(self.dir_tree)
            no_dirs_item.setText(0, "Select satellite to start")
            no_dirs_item.setFlags(no_dirs_item.flags() & ~Qt.ItemIsSelectable)

    def on_files_found(self, files):
        """Store all files and display filtered ones"""
        self.all_files = files
        self.display_filtered_files()

    def display_filtered_files(self):
        """Display files filtered by selected bands"""
        filtered_files = []
        total_size = 0

        for file_info in self.all_files:
            filename = file_info['name']

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
                QPushButton {
                    background: #37474F;
                    color: white;
                    padding: 3px;
                    border-radius: 2px;
                    font-size: 10px;
                    border: 1px solid #555;
                }
                QPushButton:hover {
                    background: #455A64;
                }
            """)
            btn.clicked.connect(lambda checked, f=file_info: self.download_single_file(f))
            self.files_table.setCellWidget(row, 4, btn)

        self.files_table.resizeColumnsToContents()

    def update_path_label(self):
        """Update the path display"""
        full_path = f"s3://{self.current_bucket}/"
        if self.current_path:
            full_path += "/".join(self.current_path) + "/"
        self.path_label.setText(full_path)

    def download_filtered(self):
        """Download all filtered files in current directory"""
        if not self.current_prefix:
            QMessageBox.warning(self, "No Directory", "Please navigate to a directory first.")
            return

        if not self.selected_bands:
            QMessageBox.warning(self, "No Bands Selected",
                              "Please select at least one band to download.")
            return

        path_parts = self.current_path.copy()
        if not path_parts:
            path_parts = ["root"]

        download_dir = Path(self.dir_edit.text()) / self.current_bucket.replace("noaa-", "") / "_".join(path_parts)
        download_dir.mkdir(parents=True, exist_ok=True)

        self.start_download(self.current_prefix, download_dir, self.selected_bands)

    def download_single_file(self, file_info):
        """Download a single file"""
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save File",
            str(Path(self.dir_edit.text()) / file_info['name']),
            "All Files (*.*)"
        )
        if not save_path:
            return

        try:
            s3_client = boto3.client(
                's3',
                config=Config(signature_version=UNSIGNED)
            )
            s3_client.download_file(
                Bucket=self.current_bucket,
                Key=file_info['key'],
                Filename=save_path
            )
            self.log_message("SUCCESS", f"Downloaded: {file_info['name']}")
            QMessageBox.information(self, "Success", f"Downloaded {file_info['name']}")
        except Exception as e:
            self.log_message("ERROR", f"Failed to download {file_info['name']}: {str(e)}")
            QMessageBox.warning(self, "Error", f"Failed to download {file_info['name']}")

    def start_download(self, prefix, download_dir, bands):
        if self.download_worker and self.download_worker.isRunning():
            QMessageBox.warning(self, "Download in Progress",
                              "Please wait for current download to complete.")
            return

        max_workers = self.concurrent_spin.value()

        self.log_message("INFO", f"Starting parallel download ({max_workers} concurrent) from: s3://{self.current_bucket}/{prefix}")
        self.log_message("INFO", f"Selected bands: {', '.join([f'B{b}' for b in bands])}")

        self.download_worker = S3DownloadWorker(self.current_bucket, prefix, download_dir, bands, max_workers)
        self.download_worker.progress.connect(lambda msg: self.log_message("INFO", msg))
        self.download_worker.file_progress.connect(self.on_file_progress)
        self.download_worker.finished.connect(self.on_download_finished)
        self.download_worker.error.connect(lambda msg: self.log_message("ERROR", msg))
        self.download_worker.start()

        self.download_btn.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)

    def on_file_progress(self, current, total, filename):
        progress = int((current / total) * 100) if total > 0 else 0
        self.progress_bar.setValue(progress)
        self.status_bar.showMessage(f"Downloading {filename} ({current}/{total})")

    def on_download_finished(self, success, download_path):
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.download_btn.setEnabled(True)

        if success:
            self.log_message("SUCCESS", f"Download completed successfully to: {download_path}")
            if self.auto_process and download_path:
                self.log_message("INFO", "Starting auto-processing of downloaded files...")
                self.log_message("INFO", f"Auto-processing directory: {download_path}")
                self.start_processing(download_path, self.process_mode)
            else:
                QMessageBox.information(self, "Success",
                                      f"Download completed to:\n{download_path}")
        else:
            QMessageBox.warning(self, "Error", "Download failed. Check status messages.")

    def start_processing(self, directory_path, process_mode="auto"):
        """Start Himawari processing"""
        if self.processor_worker and self.processor_worker.isRunning():
            self.log_message("WARNING", "Processing already in progress")
            return

        create_rgb = any(
            product in self.selected_products and
            self.rgb_products.get(product, {}).get("type") in ["rgb", "all_rgb", "enhanced"]
            for product in self.selected_products
        )

        max_workers = self.concurrent_spin.value()

        self.processor_worker = HimawariProcessorWorker(
            directory_path,
            process_mode,
            create_rgb,
            self.force_simple,
            max_workers
        )
        self.processor_worker.progress.connect(lambda msg: self.log_message("PROCESS", msg))
        self.processor_worker.finished.connect(self.on_processing_finished)
        self.processor_worker.error.connect(lambda msg: self.log_message("ERROR", msg))
        self.processor_worker.stats_update.connect(self.update_statistics_display)
        self.process_progress_bar.setVisible(True)
        self.process_progress_bar.setRange(0, 0)
        self.status_bar.showMessage(f"Processing files ({process_mode})...")
        self.processor_worker.start()

    def on_processing_finished(self, success, directory_path):
        self.process_progress_bar.setVisible(False)

        if success:
            self.log_message("SUCCESS", f"Processing completed in: {directory_path}")
            self.status_bar.showMessage("Processing completed successfully")

            reply = QMessageBox.question(
                self,
                "Processing Complete",
                f"Files processed successfully!\n\nDirectory: {directory_path}\n\nOpen folder?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self.open_folder(directory_path)
        else:
            self.log_message("ERROR", "Processing failed")
            self.status_bar.showMessage("Processing failed")

    def update_statistics_display(self, stats):
        """Update statistics display with new data"""
        self.stats_labels['extracted'].setText(
            f"Extracted: {stats.get('successfully_extracted', 0)}/{stats.get('total_extracted', 0)}"
        )
        self.stats_labels['combined'].setText(
            f"Combined: {stats.get('successfully_combined', 0)}/{stats.get('total_combined', 0)}"
        )
        self.stats_labels['tiff_created'].setText(
            f"GeoTIFF: {stats.get('tiff_created', 0)}"
        )
        self.stats_labels['rgb_created'].setText(
            f"RGB: {'Yes' if stats.get('rgb_created', 0) > 0 else 'No'}"
        )
        self.stats_labels['satpy_decodes'].setText(
            f"Satpy: {stats.get('satpy_decodes', 0)}"
        )
        self.stats_labels['satpy_failures'].setText(
            f"Failures: {stats.get('satpy_failures', 0)}"
        )

    def open_folder(self, directory_path):
        """Open folder in file explorer"""
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
        for checkbox in self.band_checkboxes.values():
            checkbox.setChecked(True)

    def select_no_bands(self):
        for checkbox in self.band_checkboxes.values():
            checkbox.setChecked(False)

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            str(self.dir_edit.text())
        )
        if directory:
            self.dir_edit.setText(directory)
            self.log_message("INFO", f"Download directory set to: {directory}")

    def log_message(self, level, message):
        timestamp = datetime.now().strftime("%H:%M:%S")

        icons = {
            "ERROR": "❌",
            "SUCCESS": "✅",
            "PROCESS": "🔧",
            "INFO": "ℹ️",
            "WARNING": "⚠️"
        }
        icon = icons.get(level, "")

        if level in ["ERROR", "SUCCESS", "PROCESS"]:
            self.status_bar.showMessage(f"{icon} {message}")
        elif level == "INFO" and any(keyword in message for keyword in ["Auto-processing", "Processing mode", "Products selected", "Force simple"]):
            self.status_bar.showMessage(f"{icon} {message}")

        color_codes = {
            "ERROR": "\033[91m",
            "SUCCESS": "\033[92m",
            "PROCESS": "\033[93m",
            "INFO": "\033[94m",
            "WARNING": "\033[93m"
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
 
    app = QApplication(sys.argv)
 
    app.setStyleSheet("""
        QMainWindow {
            background: #222;
        }
    """)
 
    window = HimawariFileManager()
    window.show()
 
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
