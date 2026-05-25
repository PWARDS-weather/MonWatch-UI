import sys
import os
import io
from datetime import date, timedelta
from pathlib import Path
import importlib.util
import subprocess
import logging
import requests
import zipfile
import tempfile
from PIL import Image
from PySide6.QtCore import QPointF, Qt, QUrl, QMimeData, QObject, Signal, QThread, QSize, QTimer, QProcess, QDate, QDateTime
Image.MAX_IMAGE_PIXELS = None

# Geospatial imports
try:
    import rasterio
    from pyproj import Transformer, CRS
    import shapefile
    HAS_GEO = True
except ImportError:
    HAS_GEO = False
    print("WARNING: Install rasterio, pyproj, and pyshp for grid/coastline features.")

from PySide6.QtCore import QPointF, Qt, QUrl, QMimeData, QObject, Signal, QThread, QSize, QTimer, QProcess
from PySide6.QtGui import (
    QPixmap, QIcon, QDrag, QMouseEvent,
    QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent,
    QWheelEvent, QImage, QPainter, QFont, QPen, QColor, QPainterPath,
    QImageReader
)
QImageReader.setAllocationLimit(2048)

# OpenGL import – used only when user enables GPU
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QGroupBox, QRadioButton,
    QPushButton, QTabWidget, QStatusBar, QHBoxLayout, QLabel, QSplitter,
    QListWidget, QListWidgetItem, QTextEdit, QSlider, QFrame,
    QFileDialog, QSizePolicy, QGraphicsView, QGraphicsScene, QMenu,
    QProgressDialog, QMessageBox, QCheckBox, QSpinBox, QComboBox,
    QProgressBar, QDateEdit, QScrollArea, QButtonGroup, QDateTimeEdit,
    QGraphicsPixmapItem, QGraphicsTextItem, QGraphicsPathItem, QGridLayout
)

# --- logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- paths ---
top_dir = Path(__file__).resolve().parent.parent
src_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(src_dir))

def _load_cache_manager():
    spec = importlib.util.spec_from_file_location('cache', top_dir / 'Process' / 'cache.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CacheManager

CacheManager = _load_cache_manager()

# --- Coastline data ---
COASTLINE_URLS = [
    "https://naciscdn.org/naturalearth/10m/physical/ne_10m_coastline.zip",
    "https://github.com/nvkelso/natural-earth-vector/raw/master/110m_physical/ne_110m_coastline.zip"
]
COASTLINE_DIR = top_dir / "data" / "coastlines"
COASTLINE_SHP = COASTLINE_DIR / "ne_10m_coastline.shp"

def ensure_coastline_data():
    if not HAS_GEO:
        return False
    if COASTLINE_SHP.exists():
        return True
    COASTLINE_DIR.mkdir(parents=True, exist_ok=True)
    for url in COASTLINE_URLS:
        try:
            print(f"Downloading coastline data from {url}...")
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                for chunk in response.iter_content(chunk_size=8192):
                    tmp.write(chunk)
                tmp_path = tmp.name
            with zipfile.ZipFile(tmp_path, 'r') as zip_ref:
                zip_ref.extractall(COASTLINE_DIR)
            os.unlink(tmp_path)
            print("Coastline data ready.")
            return True
        except Exception as e:
            print(f"Failed with {url}: {e}")
            continue
    return False

def cache_worker_factory(input_dir, cache_dir):
    class CacheWorker(QObject):
        progress = Signal(str)
        finished = Signal(bool)
        cache_activity = Signal(str)
        def __init__(self, input_dir, cache_dir):
            super().__init__()
            self.input_dir = input_dir
            self.cache_dir = cache_dir
            self._is_cancelled = False
        def run(self):
            self.cache_activity.emit(f"Starting cache: {self.input_dir}")
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            success = False
            try:
                cm = CacheManager(input_dir=self.input_dir, cache_dir=self.cache_dir)
                cm.generate_pyramidal_cache()
                success = True
            except Exception as ex:
                self.cache_activity.emit(f"Cache failed: {ex}")
            finally:
                sys.stdout = old_stdout
            if not self._is_cancelled:
                for line in buf.getvalue().splitlines():
                    self.progress.emit(line)
                self.cache_activity.emit("Cache complete" if success else "Cache failed")
                self.finished.emit(success)
        def cancel(self):
            self._is_cancelled = True
    return CacheWorker(input_dir, cache_dir)

class FileListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setMinimumHeight(150)
    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        mime = QMimeData()
        url = QUrl.fromLocalFile(item.data(Qt.UserRole))
        mime.setUrls([url])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)

class ZoomableGraphicsView(QGraphicsView):
    zoomChanged = Signal(float)
    maxZoomReached = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._opengl_widget = None
        self.gpu_enabled = False
        self.setScene(QGraphicsScene())
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.zoom_enabled = True
        self.zoom_factor = 1.0
        self.base_zoom = 1.0
        self.min_zoom = 0.1
        self.max_zoom = 20.0
        self.max_zoom_threshold = 5.5
        self.using_original = False
        self._drag_active = False

    def set_gpu_acceleration(self, enabled):
        if enabled == self.gpu_enabled:
            return
        self.gpu_enabled = enabled
        if enabled:
            if self._opengl_widget is None:
                self._opengl_widget = QOpenGLWidget()
            self.setViewport(self._opengl_widget)
            self.setViewportUpdateMode(QGraphicsView.FullViewportUpdate)
        else:
            self.setViewport(None)
            self.setViewportUpdateMode(QGraphicsView.MinimalViewportUpdate)

    def set_image(self, pixmap: QPixmap, preserve_view=False, quality_level=1.0, is_original=False):
        if preserve_view and self.scene() and self.scene().items():
            view_center = self.mapToScene(self.viewport().rect().center())
            old_zoom = self.zoom_factor
        else:
            view_center = None
        self.scene().clear()
        self.scene().addPixmap(pixmap)
        if preserve_view and view_center:
            new_center_x = view_center.x() * quality_level
            new_center_y = view_center.y() * quality_level
            self.zoom_factor = old_zoom * (quality_level / self.base_zoom)
            self.base_zoom = quality_level
            self.resetTransform()
            self.scale(self.zoom_factor, self.zoom_factor)
            self.centerOn(QPointF(new_center_x, new_center_y))
        else:
            self.fitInView(self.scene().itemsBoundingRect(), Qt.KeepAspectRatio)
            self.zoom_factor = 1.0
            self.base_zoom = quality_level
        self.using_original = is_original
        self.max_zoom = 1000.0 if is_original else 20.0
        self.zoomChanged.emit(self.zoom_factor)

    def enable_zoom(self, flag: bool):
        self.zoom_enabled = flag

    def wheelEvent(self, event: QWheelEvent):
        if not self.zoom_enabled:
            event.ignore()
            return
        if event.angleDelta().y() > 0:
            factor = 1.25
        else:
            factor = 1 / 1.25
        new_zoom = self.zoom_factor * factor
        if new_zoom < self.min_zoom:
            factor = self.min_zoom / self.zoom_factor
            new_zoom = self.min_zoom
        elif new_zoom > self.max_zoom:
            factor = self.max_zoom / self.zoom_factor
            new_zoom = self.max_zoom
        if (self.zoom_factor < self.max_zoom_threshold and 
            new_zoom >= self.max_zoom_threshold and 
            not self.using_original):
            self.maxZoomReached.emit()
            return
        self.zoom_factor = new_zoom
        self.scale(factor, factor)
        self.zoomChanged.emit(self.zoom_factor)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_active = True
        else:
            event.ignore()
    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls() and self._drag_active:
            event.acceptProposedAction()
        else:
            event.ignore()
    def dragLeaveEvent(self, event: QDragLeaveEvent):
        self._drag_active = False
        event.accept()
    def dropEvent(self, event: QDropEvent):
        self._drag_active = False
        for url in event.mimeData().urls():
            fp = url.toLocalFile()
            if fp.lower().endswith(('.tif', '.tiff')):
                self.window().process_file(fp)
        event.acceptProposedAction()
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        add_file = menu.addAction("Add File")
        add_folder = menu.addAction("Add Folder")
        action = menu.exec(event.globalPos())
        if action == add_file:
            self.window().choose_file()
        elif action == add_folder:
            self.window().choose_folder()

class ViewportFrame(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("ViewportFrame { border: 1px solid #444; }")
        self.logo_overlay = QLabel(self)
        self.logo_overlay.setAlignment(Qt.AlignTop | Qt.AlignRight)
        self.logo_overlay.setAttribute(Qt.WA_TranslucentBackground)
        self.logo_overlay.setStyleSheet("background: transparent;")
        self.logo_overlay.show()
        self._drag_active = False
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_logo_position()
    def update_logo_position(self):
        if self.logo_overlay.isVisible() and self.logo_overlay.pixmap() and not self.logo_overlay.pixmap().isNull():
            margin = 10
            logo_size = self.logo_overlay.sizeHint()
            x = self.width() - logo_size.width() - margin
            self.logo_overlay.move(x, margin)
    def set_logo(self, pixmap: QPixmap):
        if pixmap.isNull():
            self.logo_overlay.hide()
        else:
            scaled_pix = pixmap.scaledToWidth(80, Qt.SmoothTransformation)
            self.logo_overlay.setPixmap(scaled_pix)
            self.logo_overlay.show()
            self.update_logo_position()
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_active = True
        else:
            event.ignore()
    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls() and self._drag_active:
            event.acceptProposedAction()
        else:
            event.ignore()
    def dragLeaveEvent(self, event: QDragLeaveEvent):
        self._drag_active = False
        event.accept()
    def dropEvent(self, event: QDropEvent):
        self._drag_active = False
        for url in event.mimeData().urls():
            fp = url.toLocalFile()
            if fp.lower().endswith(('.tif', '.tiff')):
                self.window().process_file(fp)
        event.acceptProposedAction()

class ProcessDatWorker(QObject):
    progress = Signal(str)
    finished = Signal(bool)
    def __init__(self, script_path):
        super().__init__()
        self.script_path = script_path
        self._is_cancelled = False
    def run(self):
        try:
            self.progress.emit(f"Starting process_dat.py at: {self.script_path}")
            result = subprocess.run([sys.executable, str(self.script_path)], capture_output=True, text=True, cwd=self.script_path.parent)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.strip():
                        self.progress.emit(line.strip())
                self.finished.emit(True)
            else:
                self.progress.emit(f"Error: {result.stderr}")
                self.finished.emit(False)
        except Exception as e:
            self.progress.emit(f"Failed to run process_dat.py: {str(e)}")
            self.finished.emit(False)
    def cancel(self):
        self._is_cancelled = True

class MainUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Monwatch UI Blizzard V2.1.3 BETA – Satellite Renderer")
        self.resize(1440, 900)
        QApplication.setFont(QFont("Segoe UI", 9))

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        self._prevent_duplicate_events = False

        self.input_dir = str(top_dir / 'public' / 'Download')
        self.cache_dir = str(top_dir / 'cache' / 'images')
        os.makedirs(self.cache_dir, exist_ok=True)

        images_dir = top_dir / 'public' / 'images'
        self.latest_path = images_dir / 'latest.png'
        self.pwards_path = images_dir / 'PWARDS.png'
        self.logo_path = images_dir / 'PWARDS.png'

        self.current_base = None
        self.current_original = None
        self.current_zoom = 1.0
        self.is_closing = False
        self.active_thread = None
        self.active_worker = None
        self.use_cache = True
        self.progressive_rendering = False
        self.current_quality_level = 0.25
        self._suppress_cache_enable_warning = False
        self._suppress_cache_disable_warning = False
        self._reload_timer = None
        self.cache_size_mb = 1000
        self.gpu_acceleration = False
        self.max_threads = 4
        self.render_quality = "Balanced"

        self.process_dat_thread = None
        self.process_dat_worker = None

        self.current_satellite = "himawari9"
        self.current_datetime = None
        self.available_bands = []
        self.band_checkboxes = {}
        self.product_checkboxes = {}
        self.selected_product = None
        self.bands_grid_layout = None
        self.products_grid_layout = None
        self.overlays_grid_layout = None

        # Geospatial overlays
        self.current_geotransform = None
        self.current_crs = None
        self.grid_overlay_items = []
        self.coast_overlay_items = []
        self.grid_enabled = False
        self.coast_enabled = False
        self.static_coastlines = False
        self.earth_radius_pixels = None
        self.satellite_lon = 140.7  # Default Himawari

        if HAS_GEO:
            ensure_coastline_data()

        container = QWidget()
        self.setCentralWidget(container)
        self.main_layout = QHBoxLayout(container)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(2)
        self.main_layout.addWidget(self.splitter)

        self.left_panel = self._init_left_panel()
        self._init_center_panel()
        self.right_panel = self._init_right_panel()
        self.load_preview_images()

        if self.logo_path.exists():
            self.viewport_frame.set_logo(QPixmap(str(self.logo_path)))
        self.splitter.setSizes([200, 800, 300])
        self.reset_view()

    def _init_left_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        if self.logo_path.exists():
            logo_label = QLabel()
            logo_pix = QPixmap(str(self.logo_path)).scaledToWidth(150, Qt.SmoothTransformation)
            logo_label.setPixmap(logo_pix)
            logo_label.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo_label)
        button_data = [
            ('📥 Fetch L1b Data', lambda: self.log('Fetching satellite data...'), "#4A6572"),
            ('🖼 Generate Image', lambda: self.log('Processing image...'), "#2E7D32"),
            ('⚙ Download Satellite Data', self.run_process_dat, "#9C27B0"),
            ('🔄 Refresh', self.refresh_image, "#0277BD"),
        ]
        for text, callback, color in button_data:
            btn = QPushButton(text)
            btn.setStyleSheet(f"""
                QPushButton {{ background: {color}; color: white; padding: 12px; border-radius: 4px; font-weight: bold; }}
                QPushButton:hover {{ background: #455A64; border: 1px solid {color}; }}
                QPushButton:pressed {{ background: #37474F; }}
            """)
            btn.clicked.connect(callback)
            layout.addWidget(btn)
        layout.addStretch()
        self.splitter.addWidget(panel)
        return panel

    def _init_center_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        self.graphics_view = ZoomableGraphicsView()
        self.graphics_view.zoomChanged.connect(self.handle_zoom_change)
        self.graphics_view.maxZoomReached.connect(self.confirm_load_original)
        self.viewport_frame = ViewportFrame(self)
        fl = QVBoxLayout(self.viewport_frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.graphics_view)
        btns = QHBoxLayout()
        for txt, val in [('🔍-', -10), ('🔍+', 10)]:
            btn = QPushButton(txt)
            btn.setStyleSheet("padding: 6px;")
            btn.clicked.connect(lambda _, v=val: self.adjust_slider(v))
            btns.addWidget(btn)
        layout.addWidget(self.viewport_frame, 1)
        layout.addLayout(btns)
        self.splitter.addWidget(panel)

    def _init_right_panel(self):
        self.sidebar = QWidget()
        sidebar_layout = QVBoxLayout(self.sidebar)
        sidebar_layout.setSpacing(8)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)

        # ----- Data Selection Panel -----
        data_group = QGroupBox("Select Data & Product")
        data_group.setStyleSheet("""
            QGroupBox {
                color: #5D8AA8;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        data_layout = QVBoxLayout(data_group)

        sat_layout = QHBoxLayout()
        sat_layout.addWidget(QLabel("Satellite:"))
        self.sat_combo = QComboBox()
        self.sat_combo.addItems(["himawari8", "himawari9", "goes16", "goes17"])
        self.sat_combo.setCurrentText("himawari9")
        self.sat_combo.currentTextChanged.connect(self.update_satellite_lon)
        self.sat_combo.setStyleSheet("""
            QComboBox {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
                min-width: 120px;
            }
        """)
        sat_layout.addWidget(self.sat_combo)
        data_layout.addLayout(sat_layout)

        date_layout = QHBoxLayout()
        date_layout.addWidget(QLabel("Year:"))
        self.year_combo = QComboBox()
        self.year_combo.addItems([str(y) for y in range(2020, 2031)])
        self.year_combo.setCurrentText(str(QDate.currentDate().year()))
        self.year_combo.setStyleSheet("""
            QComboBox {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
            }
        """)
        date_layout.addWidget(self.year_combo)

        date_layout.addWidget(QLabel("Month:"))
        self.month_combo = QComboBox()
        self.month_combo.addItems([f"{m:02d}" for m in range(1, 13)])
        self.month_combo.setCurrentText(f"{QDate.currentDate().month():02d}")
        self.month_combo.setStyleSheet("""
            QComboBox {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
            }
        """)
        date_layout.addWidget(self.month_combo)

        date_layout.addWidget(QLabel("Day:"))
        self.day_combo = QComboBox()
        self._update_day_combo()
        self.day_combo.setStyleSheet("""
            QComboBox {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
            }
        """)
        date_layout.addWidget(self.day_combo)

        self.year_combo.currentTextChanged.connect(self._update_day_combo)
        self.month_combo.currentTextChanged.connect(self._update_day_combo)

        data_layout.addLayout(date_layout)

        time_layout = QHBoxLayout()
        time_layout.addWidget(QLabel("Hour (UTC):"))
        self.hour_combo = QComboBox()
        self.hour_combo.addItems([f"{h:02d}" for h in range(0, 24)])
        self.hour_combo.setCurrentText(QDateTime.currentDateTime().toString("HH"))
        self.hour_combo.setStyleSheet("""
            QComboBox {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
            }
        """)
        time_layout.addWidget(self.hour_combo)

        time_layout.addWidget(QLabel("Minute:"))
        self.minute_combo = QComboBox()
        self.minute_combo.addItems([f"{m:02d}" for m in range(0, 60, 10)])
        self.minute_combo.setCurrentText(f"{(QDateTime.currentDateTime().time().minute() // 10) * 10:02d}")
        self.minute_combo.setStyleSheet("""
            QComboBox {
                background: #2D2D2D;
                color: #EEE;
                border: 1px solid #444;
                border-radius: 3px;
                padding: 3px;
            }
        """)
        time_layout.addWidget(self.minute_combo)

        data_layout.addLayout(time_layout)

        # Auto-load bands when date/time changes
        self.sat_combo.currentTextChanged.connect(self.auto_load_bands)
        self.year_combo.currentTextChanged.connect(self.auto_load_bands)
        self.month_combo.currentTextChanged.connect(self.auto_load_bands)
        self.day_combo.currentTextChanged.connect(self.auto_load_bands)
        self.hour_combo.currentTextChanged.connect(self.auto_load_bands)
        self.minute_combo.currentTextChanged.connect(self.auto_load_bands)

        self.load_bands_btn = QPushButton("Load Available Bands")
        self.load_bands_btn.setStyleSheet("""
            QPushButton {
                background: #5D8AA8;
                color: white;
                padding: 8px;
                border-radius: 3px;
                border: 1px solid #444;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #4A6572;
            }
        """)
        self.load_bands_btn.clicked.connect(self.load_bands_for_date)
        data_layout.addWidget(self.load_bands_btn)

        # ----- Bands Group (Process_dat.py layout style) -----
        bands_group = QGroupBox("Band Selection")
        bands_group.setStyleSheet("""
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
        bands_layout = QVBoxLayout(bands_group)

        self.bands_grid_layout = QGridLayout()
        self.bands_grid_layout.setSpacing(5)
        bands_layout.addLayout(self.bands_grid_layout)

        self.selected_bands_label = QLabel("Selected: 0 bands")
        self.selected_bands_label.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")
        data_layout.addWidget(bands_group)

        # ----- RGB Products Group (Process_dat.py layout style) -----
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

        self.products_grid_layout = QGridLayout()
        self.products_grid_layout.setSpacing(5)
        products_layout.addLayout(self.products_grid_layout)

        product_names = [
            "Natural Color", "Geo Color", "Sandwich Product", "Air Mass RGB",
            "Dust RGB", "Day Convection RGB", "Fire Temperature RGB",
            "Night Microphysics RGB", "Cloud Phase RGB", "True Color",
            "Infrared (Standard)"
        ]
        
        for i, name in enumerate(product_names):
            cb = QCheckBox(name)
            cb.setStyleSheet("""
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
                QCheckBox:disabled {
                    color: #555;
                    background: #222;
                }
            """)
            cb.toggled.connect(self.on_product_checkbox_toggled)
            self.product_checkboxes[name] = cb
            
            row = i // 3
            col = i % 3
            self.products_grid_layout.addWidget(cb, row, col)

        product_buttons = QHBoxLayout()


        self.selected_products_label = QLabel("Selected: 0 products")
        self.selected_products_label.setStyleSheet("color: #4CAF50; font-size: 10px; font-weight: bold;")
        data_layout.addWidget(products_group)

        # ----- Overlays Group (Matching Bands/RGB Layout) -----
        overlays_group = QGroupBox("Overlays")
        overlays_group.setStyleSheet("""
            QGroupBox {
                color: #5D8AA8;
                font-weight: bold;
                border: 1px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
        """)
        overlays_layout = QVBoxLayout(overlays_group)

        self.overlays_grid_layout = QGridLayout()
        self.overlays_grid_layout.setSpacing(5)
        overlays_layout.addLayout(self.overlays_grid_layout)

        overlay_items = [
            ("Show Grid", self.toggle_grid),
            ("Show Coastlines", self.toggle_coastlines),
            ("Show Info Box", self.show_info_placeholder)
        ]

        self.overlay_checkboxes = {}
        # Find this section in _init_right_panel and update:
        for i, (name, callback) in enumerate(overlay_items):
            cb = QCheckBox(name)
            cb.setStyleSheet("""
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
            # CHANGE: Use toggled instead of stateChanged for cleaner boolean
            cb.toggled.connect(callback)
            self.overlay_checkboxes[name] = cb
            
            row = i // 3
            col = i % 3
            self.overlays_grid_layout.addWidget(cb, row, col)


        data_layout.addWidget(overlays_group)

        sidebar_layout.addWidget(data_group, 1)

        # ----- Tab widget -----
        self.tab_widget = QTabWidget()
        self.tab_widget.setTabPosition(QTabWidget.West)
        self.tab_widget.setStyleSheet("""
            QTabBar::tab {
                width: 30px;
                height: 30px;
                padding: 0;
                background: #333;
                border: 1px solid #444;
            }
            QTabBar::tab:selected {
                background: #555;
            }
            QTabWidget::pane {
                border: none;
                background: #222;
            }
        """)

        self.scene_tab = self._create_scene_tab()
        self.design_tab = self._create_design_tab()
        self.performance_tab = self._create_performance_tab()
        self.development_tab = self._create_development_tab()

        self.tab_widget.addTab(self.scene_tab, "🎬")
        self.tab_widget.setTabToolTip(0, "Scene")
        self.tab_widget.addTab(self.design_tab, "🎨")
        self.tab_widget.setTabToolTip(1, "Design")
        self.tab_widget.addTab(self.performance_tab, "⚙")
        self.tab_widget.setTabToolTip(2, "Performance")
        self.tab_widget.addTab(self.development_tab, "🛠")
        self.tab_widget.setTabToolTip(3, "Development/Settings")

        sidebar_layout.addWidget(self.tab_widget, 4)

        self.splitter.addWidget(self.sidebar)
        self.sidebar.setMinimumWidth(300)
        return self.sidebar

    def update_satellite_lon(self):
        sat = self.sat_combo.currentText()
        if "himawari" in sat:
            self.satellite_lon = 140.7
        elif "goes16" in sat:
            self.satellite_lon = -75.2
        elif "goes17" in sat:
            self.satellite_lon = -137.2
        else:
            self.satellite_lon = 140.7

    def _update_day_combo(self):
        try:
            year = int(self.year_combo.currentText())
            month = int(self.month_combo.currentText())
            if month == 12:
                last_day = 31
            else:
                first_day_next = date(year, month + 1, 1)
                last_day = (first_day_next - timedelta(days=1)).day
            days = [f"{d:02d}" for d in range(1, last_day + 1)]
            self.day_combo.clear()
            self.day_combo.addItems(days)
            current_day = self.day_combo.currentText()
            if current_day and int(current_day) <= last_day:
                self.day_combo.setCurrentText(current_day)
            else:
                self.day_combo.setCurrentIndex(0)
        except Exception:
            pass

    def auto_load_bands(self):
        QTimer.singleShot(500, self.load_bands_for_date)

    def load_bands_for_date(self):
        sat = self.sat_combo.currentText()
        year = self.year_combo.currentText()
        month = self.month_combo.currentText()
        day = self.day_combo.currentText()
        hour = self.hour_combo.currentText()
        minute = self.minute_combo.currentText()
        self.current_datetime = f"{year}_{month}_{day}_{hour}{minute}"
        self.log(f"Selected datetime: {self.current_datetime}")
        base_path = Path(self.input_dir) / sat
        if not base_path.exists():
            self.log(f"Satellite directory not found: {base_path}")
            self.status_bar.showMessage("Satellite directory not found")
            return
        
        # Flexible folder search
        folder_pattern = f"*{self.current_datetime}*"
        matches = list(base_path.glob(folder_pattern))
        
        if not matches:
            self.log(f"No data folder found for {self.current_datetime} in {base_path}")
            # --- NEW CODE START: List available folders for debugging ---
            all_folders = [f.name for f in base_path.iterdir() if f.is_dir()]
            if all_folders:
                self.log(f"Available folders in {base_path.name}: {', '.join(all_folders[:5])}...")
                self.status_bar.showMessage(f"No data for {self.current_datetime}. Check Year (Try 2026?)")
            else:
                self.status_bar.showMessage("Satellite directory is empty")
            # --- NEW CODE END ---
            
            return
            
        data_folder = matches[0]
        
        band_files = list(data_folder.glob("B*.tif")) + list(data_folder.glob("B*.tiff"))
        band_names = sorted([f.stem for f in band_files])
        self.available_bands = band_names
        if band_names:
            self.log(f"Found bands: {', '.join(band_names)}")
        else:
            self.log("No band files found in data folder")
            self.status_bar.showMessage("No band TIFFs found")
            return
        
        for cb in self.band_checkboxes.values():
            cb.deleteLater()
        self.band_checkboxes.clear()
        while self.bands_grid_layout.count():
            item = self.bands_grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        row, col = 0, 0
        max_cols = 4
        for band in band_names:
            cb = QCheckBox(band)
            cb.setStyleSheet("""
                QCheckBox {
                    color: #EEE;
                    padding: 3px;
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
            cb.toggled.connect(self.on_band_checkbox_toggled)
            self.bands_grid_layout.addWidget(cb, row, col)
            self.band_checkboxes[band] = cb
            col += 1
            if col >= max_cols:
                col = 0
                row += 1
        
        for cb in self.product_checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        
        self.update_selected_bands_label()
        self.load_selected_band_or_product()
        self.status_bar.showMessage(f"Loaded {len(band_names)} bands for {sat} {self.current_datetime}")

    def select_all_bands(self):
        for cb in self.band_checkboxes.values():
            cb.setChecked(True)

    def select_no_bands(self):
        for cb in self.band_checkboxes.values():
            cb.setChecked(False)

    def select_all_products(self):
        for cb in self.product_checkboxes.values():
            cb.setChecked(True)

    def select_no_products(self):
        for cb in self.product_checkboxes.values():
            cb.setChecked(False)

    def select_default_products(self):
        for name, cb in self.product_checkboxes.items():
            if name in ["Natural Color", "True Color"]:
                cb.setChecked(True)
            else:
                cb.setChecked(False)

    def on_band_checkbox_toggled(self, checked):
        self.update_selected_bands_label()
        sender = self.sender()
        if checked:
            for band, cb in self.band_checkboxes.items():
                if cb is not sender:
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            for cb in self.product_checkboxes.values():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
            self.load_selected_band_or_product()
        else:
            any_band_checked = any(cb.isChecked() for cb in self.band_checkboxes.values())
            any_product_checked = any(cb.isChecked() for cb in self.product_checkboxes.values())
            if not any_band_checked and not any_product_checked:
                self.load_preview_images()

    def on_product_checkbox_toggled(self, checked):
        sender = self.sender()
        if checked:
            for name, cb in self.product_checkboxes.items():
                if cb is not sender:
                    cb.blockSignals(True)
                    cb.setChecked(False)
                    cb.blockSignals(False)
            for cb in self.band_checkboxes.values():
                cb.blockSignals(True)
                cb.setChecked(False)
                cb.blockSignals(False)
            self.load_selected_band_or_product()
        else:
            any_band_checked = any(cb.isChecked() for cb in self.band_checkboxes.values())
            any_product_checked = any(cb.isChecked() for cb in self.product_checkboxes.values())
            if not any_product_checked and not any_band_checked:
                self.load_preview_images()

    def update_selected_bands_label(self):
        checked_count = sum(1 for cb in self.band_checkboxes.values() if cb.isChecked())
        if checked_count == 0:
            self.selected_bands_label.setText("Selected: 0 bands")
        elif checked_count == 1:
            self.selected_bands_label.setText("Selected: 1 band")
        else:
            self.selected_bands_label.setText(f"Selected: {checked_count} bands")

    def load_selected_band_or_product(self):
        selected_band = None
        for band, cb in self.band_checkboxes.items():
            if cb.isChecked():
                selected_band = band
                break
        selected_product = None
        for prod, cb in self.product_checkboxes.items():
            if cb.isChecked():
                selected_product = prod
                break
        
        sat = self.sat_combo.currentText()
        year = self.year_combo.currentText()
        month = self.month_combo.currentText()
        day = self.day_combo.currentText()
        hour = self.hour_combo.currentText()
        minute = self.minute_combo.currentText()
        dt_str = f"{year}_{month}_{day}_{hour}{minute}"
        self.current_datetime = dt_str
        
        base = Path(self.input_dir) / sat
        if not base.exists():
            self.log(f"Satellite directory not found: {base}")
            return

        # FIX: Use flexible glob search for folder instead of strict construction
        folder_pattern = f"*{dt_str}*"
        matches = list(base.glob(folder_pattern))
        if not matches:
            self.log(f"Data folder not found for {dt_str}")
            self.status_bar.showMessage("Data folder not found")
            return
        target = matches[0]

        if selected_band:
            band_path = target / f"{selected_band}.tif"
            if not band_path.exists():
                band_path = target / f"{selected_band}.tiff"
            if band_path.exists():
                self.process_file(str(band_path))
                return
            else:
                self.log(f"Band file not found: {band_path}")
            return

        if selected_product:
            self.log(f"Loading product: {selected_product}")
            self.status_bar.showMessage(f"Loading {selected_product}...")
            product_file_map = {
                "Natural Color": "natural", "Geo Color": "Geo", "Sandwich Product": "sandwich",
                "Air Mass RGB": "airmass", "Dust RGB": "dust", "Day Convection RGB": "day_convection",
                "Fire Temperature RGB": "fire", "Night Microphysics RGB": "night_microphysics",
                "Cloud Phase RGB": "cloud_phase", "True Color": "true", "Infrared (Standard)": "infrared",
            }
            filename_base = product_file_map.get(selected_product)
            if not filename_base:
                self.log(f"No mapping defined for product: {selected_product}")
                self.status_bar.showMessage(f"Unknown product: {selected_product}")
                return
            
            composite_path = target / "sat" / f"{filename_base}.tif"
            if not composite_path.exists():
                composite_path = target / "sat" / f"{filename_base}.tiff"
            if composite_path.exists():
                self.process_file(str(composite_path))
            else:
                self.log(f"Composite file not found: {composite_path}")
                self.status_bar.showMessage(f"Missing: {filename_base}.tif")
        
        if not selected_band and not selected_product:
            self.load_preview_images()

    def safe_remove_overlay_items(self, items_list, scene):
        if not scene: 
            items_list.clear()
            return
        for item in list(items_list):
            if item is not None:
                try:
                    scene.removeItem(item)
                except RuntimeError:
                    pass
        items_list.clear()

    def clear_overlays(self):
        scene = self.graphics_view.scene()
        self.safe_remove_overlay_items(self.grid_overlay_items, scene)
        self.safe_remove_overlay_items(self.coast_overlay_items, scene)

    def toggle_grid(self, checked):
        self.grid_enabled = checked
        self.log(f"Grid toggled: {self.grid_enabled}")
        
        if not self.current_geotransform or not self.current_crs:
            if self.grid_enabled:
                self.log("Warning: No georeferencing info available. Grid will draw when image loads.")
            return
        
        if self.grid_enabled:
            self.update_overlays(redraw_grid=True, redraw_coast=False)
        else:
            self.clear_overlays()
            self.update_overlays(redraw_grid=True, redraw_coast=False)

    def toggle_coastlines(self, checked):
        self.coast_enabled = checked
        self.log(f"Coastlines toggled: {self.coast_enabled}")
        
        if not self.current_geotransform or not self.current_crs:
            if self.coast_enabled:
                self.log("Warning: No georeferencing info available. Coastlines will draw when image loads.")
            return
        
        if self.coast_enabled:
            self.update_overlays(redraw_grid=False, redraw_coast=True)
        else:
            self.clear_overlays()
            self.update_overlays(redraw_grid=False, redraw_coast=True)

    def show_info_placeholder(self, state):
        if state == Qt.Checked:
            self.status_bar.showMessage("Info Box: Feature coming soon.", 3000)
            self.log("Info Box: Feature coming soon.")

    def update_overlays(self, redraw_grid=True, redraw_coast=True):
        if not HAS_GEO or not self.current_geotransform or not self.current_crs:
            return
        scene = self.graphics_view.scene()
        if not scene:
            return
        if redraw_grid:
            self.safe_remove_overlay_items(self.grid_overlay_items, scene)
            if self.grid_enabled:
                self._draw_grid()
        if redraw_coast:
            self.safe_remove_overlay_items(self.coast_overlay_items, scene)
            if self.coast_enabled:
                self._draw_coastlines()

    def _get_pixmap_item(self):
        scene = self.graphics_view.scene()
        if not scene:
            return None
        for item in scene.items():
            if isinstance(item, QGraphicsPixmapItem):
                return item
        return None

    def _compute_earth_disk_radius(self):
        if self.current_geotransform is None or self.current_crs is None:
            return None
        try:
            pixmap_item = self._get_pixmap_item()
            if not pixmap_item:
                return None
            img_width = pixmap_item.pixmap().width()
            img_height = pixmap_item.pixmap().height()
            center_x = img_width / 2.0
            center_y = img_height / 2.0
            try:
                central_meridian = self.current_crs.coordinate_operation.params[0].value
            except:
                central_meridian = 140.7
            transformer = Transformer.from_crs("EPSG:4326", self.current_crs, always_xy=True)
            lon_limb = central_meridian + 80
            x_geo, y_geo = transformer.transform(lon_limb, 0)
            inv_transform = ~self.current_geotransform
            col, row = inv_transform * (x_geo, y_geo)
            radius = ((col - center_x)**2 + (row - center_y)**2)**0.5
            return radius
        except Exception as e:
            self.log(f"Could not compute Earth radius: {e}")
            return None

    def _point_in_earth_disk(self, col, row, center, radius):
        if radius is None:
            return True
        dx = col - center.x()
        dy = row - center.y()
        return (dx*dx + dy*dy) <= (radius * radius)

    def _is_point_visible(self, lat, lon):
        """Check if a lat/lon point is on the visible hemisphere of the satellite"""
        import math
        lat_rad = math.radians(lat)
        lon_rad = math.radians(lon)
        sat_lon_rad = math.radians(self.satellite_lon)
        
        # Cosine of the angle between the point vector and satellite vector
        # If > 0, the point is on the visible side (within 90 degrees of subsatellite point)
        cos_angle = math.cos(lat_rad) * math.cos(lon_rad - sat_lon_rad)
        return cos_angle > 0.05  # Small margin to avoid limb issues

    def _draw_grid(self):
        if self.current_geotransform is None:
            return
        try:
            transformer = Transformer.from_crs("EPSG:4326", self.current_crs, always_xy=True)
            inv_transform = ~self.current_geotransform
            pixmap_item = self._get_pixmap_item()
            if not pixmap_item:
                return
            img_width = pixmap_item.pixmap().width()
            img_height = pixmap_item.pixmap().height()
            center = QPointF(img_width/2.0, img_height/2.0)
            radius = self._compute_earth_disk_radius()
            pen = QPen(QColor(255, 255, 100, 220), 2.0, Qt.SolidLine)
            font = QFont("Arial", 8)
            font.setBold(True)
            def add_label(text, pos):
                item = self.graphics_view.scene().addText(text, font)
                item.setDefaultTextColor(QColor(255, 255, 200, 240))
                item.setPos(pos)
                self.grid_overlay_items.append(item)
            
            # Fix: Draw paths that break when leaving the Earth disk OR when on the back side
            for lat in range(-60, 61, 10):
                path = QPainterPath()
                first = True
                last_valid_point = None
                for lon in range(-180, 181, 2):
                    # Check visibility (Front/Back check)
                    if not self._is_point_visible(lat, lon):
                        first = True
                        continue

                    try:
                        x_geo, y_geo = transformer.transform(lon, lat)
                        col, row = inv_transform * (x_geo, y_geo)
                        if 0 <= col < img_width and 0 <= row < img_height:
                            p = QPointF(col, row)
                            # Check Disk Mask (Edge check)
                            if self._point_in_earth_disk(p.x(), p.y(), center, radius):
                                if first:
                                    path.moveTo(p)
                                    first = False
                                    if not last_valid_point:
                                        last_valid_point = p
                                else:
                                    path.lineTo(p)
                                last_valid_point = p
                            else:
                                # Break the line if outside disk
                                first = True
                    except:
                        first = True
                
                if not path.isEmpty():
                    item = self.graphics_view.scene().addPath(path, pen)
                    self.grid_overlay_items.append(item)
                    if last_valid_point:
                        add_label(f"{lat}°", last_valid_point)

            try:
                central_meridian = self.current_crs.coordinate_operation.params[0].value
            except:
                central_meridian = 140.7
            lon_min = central_meridian - 60
            lon_max = central_meridian + 60
            for lon in range(int(lon_min), int(lon_max)+1, 10):
                path = QPainterPath()
                first = True
                last_valid_point = None
                for lat in range(-60, 61, 2):
                    # Check visibility (Front/Back check)
                    if not self._is_point_visible(lat, lon):
                        first = True
                        continue

                    try:
                        x_geo, y_geo = transformer.transform(lon, lat)
                        col, row = inv_transform * (x_geo, y_geo)
                        if 0 <= col < img_width and 0 <= row < img_height:
                            p = QPointF(col, row)
                            # Check Disk Mask (Edge check)
                            if self._point_in_earth_disk(p.x(), p.y(), center, radius):
                                if first:
                                    path.moveTo(p)
                                    first = False
                                    if not last_valid_point:
                                        last_valid_point = p
                                else:
                                    path.lineTo(p)
                                last_valid_point = p
                            else:
                                first = True
                    except:
                        first = True
                
                if not path.isEmpty():
                    item = self.graphics_view.scene().addPath(path, pen)
                    self.grid_overlay_items.append(item)
                    if last_valid_point:
                        add_label(f"{lon}°", last_valid_point)
        except Exception as e:
            self.log(f"Error drawing grid: {e}")

    def _draw_coastlines(self):
        if not COASTLINE_SHP.exists():
            self.log("Coastline data not available.")
            return
        try:
            sf = shapefile.Reader(str(COASTLINE_SHP))
            transformer = Transformer.from_crs("EPSG:4326", self.current_crs, always_xy=True)
            inv_transform = ~self.current_geotransform
            pixmap_item = self._get_pixmap_item()
            if not pixmap_item:
                return
            img_width = pixmap_item.pixmap().width()
            img_height = pixmap_item.pixmap().height()
            center = QPointF(img_width/2.0, img_height/2.0)
            radius = self._compute_earth_disk_radius()
            pen = QPen(QColor(100, 255, 100, 220), 1.5)
            
            def in_disk(p):
                return self._point_in_earth_disk(p.x(), p.y(), center, radius)

            for shape in sf.shapes():
                path = QPainterPath()
                first = True
                for part_idx, start in enumerate(shape.parts):
                    end = shape.parts[part_idx+1] if part_idx+1 < len(shape.parts) else len(shape.points)
                    for i in range(start, end):
                        lon, lat = shape.points[i]
                        # Check visibility (Front/Back check)
                        if not self._is_point_visible(lat, lon):
                            first = True
                            continue

                        try:
                            x_geo, y_geo = transformer.transform(lon, lat)
                            col, row = inv_transform * (x_geo, y_geo)
                            if 0 <= col < img_width and 0 <= row < img_height:
                                p = QPointF(col, row)
                                if in_disk(p):
                                    if first:
                                        path.moveTo(p)
                                        first = False
                                    else:
                                        path.lineTo(p)
                                else:
                                    # Break line if outside disk
                                    first = True
                            else:
                                first = True
                        except:
                            first = True
                if not path.isEmpty():
                    item = self.graphics_view.scene().addPath(path, pen)
                    self.coast_overlay_items.append(item)
        except Exception as e:
            self.log(f"Error drawing coastlines: {e}")

    def extract_georeferencing(self, tiff_path):
        if not HAS_GEO:
            return None, None
        try:
            with rasterio.open(tiff_path) as src:
                transform = src.transform
                crs = src.crs
                if crs is None:
                    self.log("TIFF has no CRS, overlays disabled.")
                    return None, None
                crs_proj = CRS.from_wkt(crs.to_wkt())
                return transform, crs_proj
        except Exception as e:
            self.log(f"Failed to read georeferencing: {e}")
            return None, None

    def closeEvent(self, event):
        self.is_closing = True
        if self.active_worker:
            self.active_worker.cancel()
        if self.active_thread and self.active_thread.isRunning():
            self.active_thread.quit()
            self.active_thread.wait(2000)
        if self._reload_timer and self._reload_timer.isActive():
            self._reload_timer.stop()
        if self.process_dat_worker:
            self.process_dat_worker.cancel()
        if self.process_dat_thread and self.process_dat_thread.isRunning():
            self.process_dat_thread.quit()
            self.process_dat_thread.wait(2000)
        super().closeEvent(event)

    def _create_scene_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        render_group = QGroupBox("Rendering")
        render_layout = QVBoxLayout(render_group)
        self.cache_checkbox = QCheckBox("Use Cache")
        self.cache_checkbox.setChecked(self.use_cache)
        self.cache_checkbox.stateChanged.connect(self.toggle_cache)
        render_layout.addWidget(self.cache_checkbox)
        self.progressive_checkbox = QCheckBox("Progressive Rendering")
        self.progressive_checkbox.setChecked(self.progressive_rendering)
        self.progressive_checkbox.setEnabled(not self.use_cache)
        self.progressive_checkbox.stateChanged.connect(self.toggle_progressive_rendering)
        render_layout.addWidget(self.progressive_checkbox)
        layout.addWidget(render_group)
        quality_group = QGroupBox("Quality Settings")
        quality_layout = QVBoxLayout(quality_group)
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("Quality Level:"))
        self.quality_label = QLabel("0.25")
        slider_layout.addWidget(self.quality_label)
        quality_layout.addLayout(slider_layout)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(1, 3)
        self.slider.setValue(1)
        self.slider.setTickPosition(QSlider.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.setSingleStep(1)
        self.slider.valueChanged.connect(self.handle_quality_change)
        quality_layout.addWidget(self.slider)
        layout.addWidget(quality_group)
        reset_btn = QPushButton("Reset View")
        reset_btn.clicked.connect(self.reset_view)
        reset_btn.setStyleSheet("QPushButton { padding: 8px; background: #5D4037; color: white; border-radius: 4px; } QPushButton:hover { background: #6D5047; }")
        layout.addWidget(reset_btn)
        layout.addStretch()
        return tab

    def _create_design_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        theme_group = QGroupBox("UI Theme")
        theme_layout = QVBoxLayout(theme_group)
        themes = [("Dark Theme", "dark"), ("Light Theme", "light"), ("Blue Theme", "blue"), ("Green Theme", "green"), ("Purple Theme", "purple")]
        self.theme_buttons = []
        for name, theme_id in themes:
            btn = QRadioButton(name)
            btn.theme_id = theme_id
            if theme_id == "dark":
                btn.setChecked(True)
            self.theme_buttons.append(btn)
            theme_layout.addWidget(btn)
        layout.addWidget(theme_group)
        layout_group = QGroupBox("Layout Options")
        layout_options = QVBoxLayout(layout_group)
        layouts = [("Default", "default"), ("Viewport Left", "left"), ("Viewport Right", "right"), ("Vertical Panels", "vertical"), ("Maximized View", "maximized")]
        self.layout_buttons = []
        for name, layout_id in layouts:
            btn = QRadioButton(name)
            btn.layout_id = layout_id
            if layout_id == "default":
                btn.setChecked(True)
            self.layout_buttons.append(btn)
            layout_options.addWidget(btn)
        layout.addWidget(layout_group)
        apply_btn = QPushButton("Apply Settings")
        apply_btn.clicked.connect(self.apply_design_settings)
        apply_btn.setStyleSheet("QPushButton { padding: 8px; background: #0277BD; color: white; border-radius: 4px; font-weight: bold; } QPushButton:hover { background: #0288D1; }")
        layout.addWidget(apply_btn)
        layout.addStretch()
        return tab

    def _create_performance_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        gpu_group = QGroupBox("GPU Acceleration")
        gpu_layout = QVBoxLayout(gpu_group)
        self.gpu_checkbox = QCheckBox("Enable GPU Rendering")
        self.gpu_checkbox.setChecked(self.gpu_acceleration)
        self.gpu_checkbox.stateChanged.connect(self.toggle_gpu_acceleration)
        gpu_layout.addWidget(self.gpu_checkbox)
        gpu_info = QLabel("Note: GPU acceleration requires compatible hardware. May cause glitches on some systems.")
        gpu_info.setStyleSheet("color: #888; font-size: 9pt;")
        gpu_info.setWordWrap(True)
        gpu_layout.addWidget(gpu_info)
        layout.addWidget(gpu_group)
        cache_group = QGroupBox("Cache Management")
        cache_layout = QVBoxLayout(cache_group)
        cache_size_layout = QHBoxLayout()
        cache_size_layout.addWidget(QLabel("Cache Size:"))
        self.cache_size_label = QLabel(f"{self.cache_size_mb} MB")
        cache_size_layout.addWidget(self.cache_size_label)
        cache_size_layout.addStretch()
        cache_layout.addLayout(cache_size_layout)
        self.cache_size_slider = QSlider(Qt.Horizontal)
        self.cache_size_slider.setRange(100, 5000)
        self.cache_size_slider.setValue(self.cache_size_mb)
        self.cache_size_slider.setTickPosition(QSlider.TicksBelow)
        self.cache_size_slider.setTickInterval(500)
        self.cache_size_slider.valueChanged.connect(self.update_cache_size_label)
        cache_layout.addWidget(self.cache_size_slider)
        self.cache_usage_bar = QProgressBar()
        self.cache_usage_bar.setRange(0, 100)
        self.cache_usage_bar.setValue(45)
        self.cache_usage_bar.setTextVisible(True)
        self.cache_usage_bar.setFormat("Cache Usage: %p%")
        cache_layout.addWidget(self.cache_usage_bar)
        layout.addWidget(cache_group)
        thread_group = QGroupBox("Thread Management")
        thread_layout = QVBoxLayout(thread_group)
        thread_count_layout = QHBoxLayout()
        thread_count_layout.addWidget(QLabel("Max Render Threads:"))
        self.thread_spinbox = QSpinBox()
        self.thread_spinbox.setRange(1, 16)
        self.thread_spinbox.setValue(self.max_threads)
        self.thread_spinbox.valueChanged.connect(self.update_max_threads)
        thread_count_layout.addWidget(self.thread_spinbox)
        thread_count_layout.addStretch()
        thread_layout.addLayout(thread_count_layout)
        layout.addWidget(thread_group)
        quality_group = QGroupBox("Render Quality")
        quality_layout = QVBoxLayout(quality_group)
        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["Performance", "Balanced", "Quality", "Ultra Quality"])
        self.quality_combo.setCurrentText(self.render_quality)
        self.quality_combo.currentTextChanged.connect(self.update_render_quality)
        quality_layout.addWidget(self.quality_combo)
        quality_info = QLabel("Higher quality uses more memory and processing power")
        quality_info.setStyleSheet("color: #888; font-size: 9pt;")
        quality_info.setWordWrap(True)
        quality_layout.addWidget(quality_info)
        layout.addWidget(quality_group)
        static_group = QGroupBox("Coastline Performance")
        static_layout = QVBoxLayout(static_group)
        self.static_coast_checkbox = QCheckBox("Static Coastlines (improves pan/zoom performance)")
        self.static_coast_checkbox.setChecked(self.static_coastlines)
        self.static_coast_checkbox.stateChanged.connect(self.toggle_static_coastlines)
        static_layout.addWidget(self.static_coast_checkbox)
        layout.addWidget(static_group)
        actions_group = QGroupBox("Performance Actions")
        actions_layout = QVBoxLayout(actions_group)
        optimize_btn = QPushButton("Optimize Performance")
        optimize_btn.clicked.connect(self.optimize_performance)
        optimize_btn.setStyleSheet("QPushButton { padding: 8px; background: #2E7D32; color: white; border-radius: 4px; } QPushButton:hover { background: #388E3C; }")
        actions_layout.addWidget(optimize_btn)
        benchmark_btn = QPushButton("Run Benchmark")
        benchmark_btn.clicked.connect(self.run_benchmark)
        benchmark_btn.setStyleSheet("QPushButton { padding: 8px; background: #F57C00; color: white; border-radius: 4px; } QPushButton:hover { background: #FF9800; }")
        actions_layout.addWidget(benchmark_btn)
        layout.addWidget(actions_group)
        layout.addStretch()
        return tab

    def _create_development_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        log_group = QGroupBox("Log Console")
        log_layout = QVBoxLayout(log_group)
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet('QTextEdit { background: #111; color: #0f0; font-family: Consolas, monospace; font-size: 10pt; border: none; }')
        log_layout.addWidget(self.log_console, 1)
        clear_btn = QPushButton("Clear Log")
        clear_btn.setStyleSheet("padding: 8px; background: #37474F; color: white;")
        clear_btn.clicked.connect(lambda: self.log_console.clear())
        log_layout.addWidget(clear_btn)
        layout.addWidget(log_group)
        input_group = QGroupBox("Input Directory")
        input_layout = QVBoxLayout(input_group)
        self.input_dir_label = QLabel(self.input_dir)
        self.input_dir_label.setWordWrap(True)
        input_layout.addWidget(self.input_dir_label)
        change_btn = QPushButton("Change Input Directory")
        change_btn.setStyleSheet("padding: 8px; background: #37474F; color: white;")
        change_btn.clicked.connect(self.choose_folder)
        input_layout.addWidget(change_btn)
        layout.addWidget(input_group)
        cache_group = QGroupBox("Cache Directory")
        cache_layout = QVBoxLayout(cache_group)
        self.cache_dir_label = QLabel(self.cache_dir)
        self.cache_dir_label.setWordWrap(True)
        cache_layout.addWidget(self.cache_dir_label)
        clear_cache_btn = QPushButton("Clear Cache")
        clear_cache_btn.setStyleSheet("padding: 8px; background: #D84315; color: white;")
        clear_cache_btn.clicked.connect(self.clear_cache)
        cache_layout.addWidget(clear_cache_btn)
        layout.addWidget(cache_group)
        debug_group = QGroupBox("Debug Tools")
        debug_layout = QVBoxLayout(debug_group)
        debug_cache_btn = QPushButton("Debug Cache")
        debug_cache_btn.setStyleSheet("padding: 8px; background: #5D4037; color: white;")
        debug_cache_btn.clicked.connect(self.run_debug_cache)
        debug_layout.addWidget(debug_cache_btn)
        layout.addWidget(debug_group)
        layout.addStretch()
        return tab

    def toggle_static_coastlines(self, state):
        self.static_coastlines = (state == Qt.Checked)
        self.log(f"Static coastlines {'enabled' if self.static_coastlines else 'disabled'}")
        if self.coast_enabled:
            self.update_overlays(redraw_grid=False, redraw_coast=True)

    def toggle_gpu_acceleration(self, state):
        self.gpu_acceleration = self.gpu_checkbox.isChecked()
        self.graphics_view.set_gpu_acceleration(self.gpu_acceleration)
        self.log(f"GPU acceleration {'enabled' if self.gpu_acceleration else 'disabled'}")

    def update_cache_size_label(self, value):
        self.cache_size_mb = value
        size_str = f"{value/1000:.1f} GB" if value >= 1000 else f"{value} MB"
        self.cache_size_label.setText(size_str)
        self.log(f"Cache size set to: {size_str}")

    def update_max_threads(self, value):
        self.max_threads = value
        self.log(f"Maximum render threads set to: {value}")

    def update_render_quality(self, quality):
        self.render_quality = quality
        self.log(f"Render quality set to: {quality}")
        if quality == "Performance":
            self.current_quality_level = 0.25
            self.slider.setValue(1)
        elif quality == "Balanced":
            self.current_quality_level = 0.5
            self.slider.setValue(2)
        elif quality == "Quality":
            self.current_quality_level = 1.0
            self.slider.setValue(3)
        elif quality == "Ultra Quality":
            self.progressive_rendering = True
            self.progressive_checkbox.setChecked(True)
            self.log("Ultra Quality enabled - using progressive rendering")

    def optimize_performance(self):
        self.log("Optimizing performance settings...")
        self.cache_size_slider.setValue(2000)
        self.thread_spinbox.setValue(4)
        self.quality_combo.setCurrentText("Balanced")
        self.gpu_checkbox.setChecked(True)
        self.log("Performance optimization complete!")
        self.status_bar.showMessage("Performance optimized")

    def run_benchmark(self):
        self.log("Starting performance benchmark...")
        self.status_bar.showMessage("Running benchmark...")
        import time
        start_time = time.time()
        for i in range(5):
            time.sleep(0.1)
            self.log(f"Benchmark progress: {(i+1)*20}%")
        elapsed = time.time() - start_time
        self.log(f"Benchmark completed in {elapsed:.2f} seconds")
        self.log(f"Performance Score: {1000/elapsed:.0f} ops/sec")
        self.status_bar.showMessage("Benchmark completed")

    def run_process_dat(self):
        script_path = top_dir / 'Process' / 'process_dat.py'
        if not script_path.exists():
            self.log(f"Error: process_dat.py not found at {script_path}")
            QMessageBox.warning(self, "Script Not Found", f"process_dat.py not found at:\n{script_path}")
            return
        self.process_dat_progress = QProgressDialog("Running process_dat.py...", "Cancel", 0, 0, self)
        self.process_dat_progress.setWindowTitle("Processing Data")
        self.process_dat_progress.setWindowModality(Qt.WindowModal)
        self.process_dat_progress.show()
        self.process_dat_worker = ProcessDatWorker(script_path)
        self.process_dat_thread = QThread()
        self.process_dat_worker.moveToThread(self.process_dat_thread)
        self.process_dat_worker.progress.connect(self.log)
        self.process_dat_worker.finished.connect(self._process_dat_finished)
        self.process_dat_thread.started.connect(self.process_dat_worker.run)
        self.process_dat_worker.finished.connect(self.process_dat_thread.quit)
        self.process_dat_worker.finished.connect(self.process_dat_worker.deleteLater)
        self.process_dat_thread.finished.connect(self.process_dat_thread.deleteLater)
        self.process_dat_progress.canceled.connect(self.process_dat_worker.cancel)
        self.process_dat_thread.start()
        self.log("Started process_dat.py script")

    def _process_dat_finished(self, success):
        if self.process_dat_progress:
            self.process_dat_progress.close()
        if success:
            self.log("process_dat.py completed successfully!")
            self.status_bar.showMessage("Data processing complete")
            QMessageBox.information(self, "Process Complete", "process_dat.py has finished processing data.\nCheck the log for details.")
        else:
            self.log("process_dat.py failed or was cancelled")
            self.status_bar.showMessage("Data processing failed")
        self.process_dat_worker = None
        self.process_dat_thread = None

    def apply_design_settings(self):
        theme = next((btn.theme_id for btn in self.theme_buttons if btn.isChecked()), "dark")
        layout = next((btn.layout_id for btn in self.layout_buttons if btn.isChecked()), "default")
        self.log(f"Applied: Theme={theme}, Layout={layout}")
        self.apply_theme(theme)
        self.apply_layout(layout)
        self.current_layout = layout

    def apply_theme(self, theme):
        palette = QPalette()
        if theme == "dark":
            palette.setColor(QPalette.Window, QColor(53,53,53))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(25,25,25))
            palette.setColor(QPalette.AlternateBase, QColor(53,53,53))
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(53,53,53))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.Highlight, QColor(142,45,197))
            palette.setColor(QPalette.HighlightedText, Qt.white)
        elif theme == "light":
            palette.setColor(QPalette.Window, QColor(240,240,240))
            palette.setColor(QPalette.WindowText, Qt.black)
            palette.setColor(QPalette.Base, QColor(255,255,255))
            palette.setColor(QPalette.AlternateBase, QColor(233,231,227))
            palette.setColor(QPalette.Text, Qt.black)
            palette.setColor(QPalette.Button, QColor(240,240,240))
            palette.setColor(QPalette.ButtonText, Qt.black)
            palette.setColor(QPalette.Highlight, QColor(0,120,215))
            palette.setColor(QPalette.HighlightedText, Qt.white)
        elif theme == "blue":
            palette.setColor(QPalette.Window, QColor(30,60,90))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(20,40,60))
            palette.setColor(QPalette.AlternateBase, QColor(40,80,120))
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(30,60,90))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.Highlight, QColor(100,160,220))
            palette.setColor(QPalette.HighlightedText, Qt.black)
        elif theme == "green":
            palette.setColor(QPalette.Window, QColor(40,80,60))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(25,50,35))
            palette.setColor(QPalette.AlternateBase, QColor(60,100,80))
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(40,80,60))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.Highlight, QColor(80,160,120))
            palette.setColor(QPalette.HighlightedText, Qt.black)
        elif theme == "purple":
            palette.setColor(QPalette.Window, QColor(50,40,80))
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(35,25,60))
            palette.setColor(QPalette.AlternateBase, QColor(65,50,100))
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(50,40,80))
            palette.setColor(QPalette.ButtonText, Qt.white)
            palette.setColor(QPalette.Highlight, QColor(142,45,197))
            palette.setColor(QPalette.HighlightedText, Qt.white)
        QApplication.instance().setPalette(palette)

    def apply_layout(self, layout_id):
        self.left_panel.setVisible(True)
        self.right_panel.setVisible(True)
        center_widget = None
        for i in range(self.splitter.count()):
            widget = self.splitter.widget(i)
            if widget not in (self.left_panel, self.right_panel):
                center_widget = widget
                break
        if not center_widget:
            if self.splitter.count() > 1:
                center_widget = self.splitter.widget(1)
            else:
                self.log("Error: Center content not found")
                return
        if layout_id == "default":
            order = [self.left_panel, center_widget, self.right_panel]
            sizes = [200, 800, 300]
        elif layout_id == "left":
            order = [center_widget, self.left_panel, self.right_panel]
            sizes = [800, 200, 300]
        elif layout_id == "right":
            order = [self.left_panel, self.right_panel, center_widget]
            sizes = [300, 200, 800]
        elif layout_id == "vertical":
            self.splitter.setOrientation(Qt.Vertical)
            order = [self.left_panel, center_widget, self.right_panel]
            sizes = [200, 600, 200]
            self.log("Vertical layout applied")
        elif layout_id == "maximized":
            self.left_panel.setVisible(False)
            self.right_panel.setVisible(False)
            order = [center_widget]
            sizes = [1440]
            self.log("Maximized view mode")
        else:
            order = [self.left_panel, center_widget, self.right_panel]
            sizes = [200, 800, 300]
        for index, widget in enumerate(order):
            current_index = self.splitter.indexOf(widget)
            if current_index != index:
                self.splitter.insertWidget(index, widget)
        self.splitter.setSizes(sizes)

    def toggle_cache(self, state):
        if self._prevent_duplicate_events:
            return
        self._prevent_duplicate_events = True
        if self.cache_checkbox.isChecked():
            self._handle_enable_cache()
        else:
            self._handle_disable_cache()
        self.schedule_reload()
        self._prevent_duplicate_events = False

    def _handle_enable_cache(self):
        if self._suppress_cache_enable_warning:
            self.use_cache = True
            self._update_cache_state()
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Enable Cache")
        msg.setText("Enabling cache will regenerate cached images.\nThis may take some time.")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        dont_ask = QCheckBox("Don't ask me again", msg)
        msg.setCheckBox(dont_ask)
        if msg.exec() == QMessageBox.Yes:
            if dont_ask.isChecked():
                self._suppress_cache_enable_warning = True
            self.use_cache = True
            self._update_cache_state()
        else:
            self.cache_checkbox.setChecked(False)

    def _handle_disable_cache(self):
        if self._suppress_cache_disable_warning:
            self.use_cache = False
            self._update_cache_state()
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Disable Cache")
        msg.setText("Disabling cache will use original images directly.\nThis requires more system resources.")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        dont_ask = QCheckBox("Don't ask me again", msg)
        msg.setCheckBox(dont_ask)
        if msg.exec() == QMessageBox.Yes:
            if dont_ask.isChecked():
                self._suppress_cache_disable_warning = True
            self.use_cache = False
            self._update_cache_state()
        else:
            self.cache_checkbox.setChecked(True)

    def _update_cache_state(self):
        self.log(f"Cache {'enabled' if self.use_cache else 'disabled'}")
        self.progressive_checkbox.setEnabled(not self.use_cache)
        if self.use_cache:
            self.progressive_rendering = False
            self.progressive_checkbox.setChecked(False)
        if self.use_cache and self.current_original:
            self.process_file(self.current_original)

    def toggle_progressive_rendering(self, state):
        if self._prevent_duplicate_events:
            return
        self._prevent_duplicate_events = True
        self.progressive_rendering = self.progressive_checkbox.isChecked()
        self.log(f"Progressive rendering {'enabled' if self.progressive_rendering else 'disabled'}")
        if not self.use_cache:
            self.schedule_reload()
        self._prevent_duplicate_events = False

    def handle_quality_change(self, value):
        scale_map = {1: 0.25, 2: 0.5, 3: 1.0}
        self.current_quality_level = scale_map[value]
        if self.current_quality_level.is_integer():
            quality_str = str(int(self.current_quality_level))
        else:
            quality_str = f"{self.current_quality_level:.2f}".rstrip('0').rstrip('.')
        self.quality_label.setText(quality_str)
        self.schedule_reload()

    def schedule_reload(self):
        if self._reload_timer and self._reload_timer.isActive():
            self._reload_timer.stop()
        if self.current_base:
            self._reload_timer = QTimer.singleShot(500, self.load_current_image)

    def clear_cache(self):
        self.log("Clearing cache...")
        try:
            cache_path = Path(self.cache_dir)
            if cache_path.exists():
                files = list(cache_path.glob('*'))
                file_count = len(files)
                for file in files:
                    if file.is_file():
                        file.unlink()
                self.log(f"Cleared {file_count} cached files")
                self.status_bar.showMessage(f"Cache cleared ({file_count} files)")
                self.cache_usage_bar.setValue(0)
            else:
                self.log("Cache directory does not exist")
        except Exception as e:
            self.log(f"Error clearing cache: {str(e)}")

    def log(self, msg):
        if self.is_closing:
            return
        try:
            if hasattr(self, 'log_console'):
                self.log_console.append(msg)
                self.log_console.verticalScrollBar().setValue(self.log_console.verticalScrollBar().maximum())
        except RuntimeError:
            pass
        logging.info(msg)

    def populate_file_list(self):
        self.file_list.clear()
        p = Path(self.input_dir)
        if p.exists():
            files = list(p.glob('*.tif')) + list(p.glob('*.tiff'))
            if not files:
                self.file_list.addItem('No TIFF files found')
                return
            for f in files:
                it = QListWidgetItem(f.name)
                it.setData(Qt.UserRole, str(f))
                self.file_list.addItem(it)
        else:
            self.file_list.addItem('Directory not found')

    def file_item_double_clicked(self, item):
        file_path = item.data(Qt.UserRole)
        if file_path:
            self.process_file(file_path)

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'Select Folder', self.input_dir)
        if d:
            self.input_dir = d
            self.input_dir_label.setText(d)
            self.populate_file_list()
            self.log(f'Selected folder: {d}')
            self.status_bar.showMessage(f"Folder: {d}")

    def choose_file(self):
        p, _ = QFileDialog.getOpenFileName(self, 'Open TIFF File', self.input_dir, 'TIFF Files (*.tif *.tiff)')
        if p:
            self.process_file(p)

    def process_file(self, fp):
        self.log(f'Processing file: {fp}')
        self.status_bar.showMessage(f"Processing: {Path(fp).name}...")
        self.current_original = fp
        self.clear_overlays()
        transform, crs = self.extract_georeferencing(fp)
        self.current_geotransform = transform
        self.current_crs = crs
        if transform is not None and crs is not None:
            self.log("Georeferencing info loaded – grid/coastlines available.")
        else:
            self.log("No georeferencing info – overlays disabled for this image.")
        if not self.use_cache:
            self.current_base = Path(fp).stem
            self.current_zoom = 1.0
            self.slider.setValue(100)
            self.load_current_image()
            self.log(f'Loaded directly: {fp}')
            self.status_bar.showMessage(f"Loaded: {Path(fp).name}")
            self.reset_view()
            return
        self.log(f'Starting cache generation: {fp}')
        self.status_bar.showMessage(f"Caching: {Path(fp).name}...")
        if self.active_worker:
            self.active_worker.cancel()
        if self.active_thread and self.active_thread.isRunning():
            self.active_thread.quit()
            self.active_thread.wait(2000)
        wk = cache_worker_factory(os.path.dirname(fp), self.cache_dir)
        th = QThread(self)
        self.active_worker = wk
        self.active_thread = th
        wk.moveToThread(th)
        wk.progress.connect(self.log, Qt.QueuedConnection)
        wk.cache_activity.connect(self.log, Qt.QueuedConnection)
        wk.finished.connect(self._after_cache, Qt.QueuedConnection)
        th.started.connect(wk.run)
        wk.finished.connect(th.quit)
        th.finished.connect(th.deleteLater)
        wk.finished.connect(wk.deleteLater)
        th.start()

    def _after_cache(self, success):
        if self.is_closing or not success or not self.current_original:
            return
        self.current_base = Path(self.current_original).stem
        self.current_zoom = 0.25
        self.slider.setValue(25)
        self.load_current_image()
        self.log(f'Completed: {self.current_original}')
        self.status_bar.showMessage(f"Loaded: {self.current_base}")
        self.active_worker = None
        self.active_thread = None
        self.reset_view()

    def load_current_image(self):
        if not self.current_base or self.is_closing:
            return
        if self.use_cache:
            quality_str = f"{self.current_quality_level:.2f}".rstrip('0').rstrip('.')
            if quality_str.endswith('.'):
                quality_str = quality_str[:-1]
            if self.current_quality_level == 1.0:
                file_path = Path(self.cache_dir) / f'{self.current_base}_x1.0.png'
                if not file_path.exists():
                    file_path = Path(self.cache_dir) / f'{self.current_base}_0.png'
            else:
                file_path = Path(self.cache_dir) / f'{self.current_base}_x{quality_str}.png'
            if not file_path.exists():
                file_path = Path(self.cache_dir) / f'{self.current_base}_x1.0.png'
        else:
            file_path = Path(self.current_original)
        try:
            if self.use_cache:
                pixmap = QPixmap(str(file_path))
            else:
                with Image.open(file_path) as img:
                    if self.current_quality_level < 1.0:
                        new_width = int(img.width * self.current_quality_level)
                        new_height = int(img.height * self.current_quality_level)
                        img = img.resize((new_width, new_height), Image.LANCZOS)
                    if img.mode == 'RGBA':
                        qimage = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888)
                    else:
                        rgb_img = img.convert('RGB')
                        qimage = QImage(rgb_img.tobytes("raw", "RGB"), rgb_img.width, rgb_img.height, QImage.Format_RGB888)
                    pixmap = QPixmap.fromImage(qimage)
            preserve = self.graphics_view.scene() and self.graphics_view.scene().items()
            self.graphics_view.set_image(pixmap, preserve, self.current_quality_level, not self.use_cache)
            self.log(f'Loaded: {file_path.name} (Quality: {self.current_quality_level})')
            self.status_bar.showMessage(f"Loaded: {file_path.name}")
            
            # FIX: Always try to update overlays after image loads
            if self.current_geotransform and self.current_crs:
                self.log(f"Updating overlays (Grid: {self.grid_enabled}, Coast: {self.coast_enabled})")
                self.update_overlays(redraw_grid=self.grid_enabled, redraw_coast=self.coast_enabled)
            else:
                self.log("No georeferencing – overlays disabled for this image.")
        except Exception as e:
            self.log(f"Error loading image: {str(e)}")
            self.status_bar.showMessage(f"Error: {str(e)}")

    def confirm_load_original(self):
        if (not self.current_original or self.graphics_view.using_original or self.is_closing or not self.use_cache):
            return
        reply = QMessageBox.question(self, "High Resolution Warning", "Are you sure you want to load the full resolution TIFF?\nThis may require significant system resources.", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.load_original_tiff()
        else:
            self.graphics_view.zoom_factor = self.graphics_view.max_zoom_threshold - 0.1
            self.graphics_view.resetTransform()
            self.graphics_view.scale(self.graphics_view.zoom_factor, self.graphics_view.zoom_factor)

    def load_original_tiff(self):
        if not self.current_original or self.is_closing:
            return
        try:
            progress = QProgressDialog("Loading original TIFF...", "Cancel", 0, 0, self)
            progress.setWindowTitle("Loading High Resolution")
            progress.setWindowModality(Qt.WindowModal)
            progress.setCancelButton(None)
            progress.show()
            QApplication.processEvents()
            self.log(f"Loading original TIFF: {Path(self.current_original).name}")
            self.status_bar.showMessage("Loading original TIFF...")
            with Image.open(self.current_original) as img:
                if img.mode == 'RGBA':
                    qimage = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888)
                else:
                    rgb_img = img.convert('RGB')
                    qimage = QImage(rgb_img.tobytes("raw", "RGB"), rgb_img.width, rgb_img.height, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimage)
            preserve = self.graphics_view.scene() and self.graphics_view.scene().items()
            self.graphics_view.set_image(pixmap, preserve, 0.25, True)
            self.log("Loaded original TIFF at full resolution")
            self.status_bar.showMessage(f"Original TIFF: {Path(self.current_original).name}")
            if self.current_geotransform and self.current_crs:
                self.update_overlays(redraw_grid=True, redraw_coast=True)
        except Exception as e:
            self.log(f"Error loading original TIFF: {str(e)}")
            self.status_bar.showMessage(f"TIFF load failed: {str(e)}")
        finally:
            progress.close()

    def handle_zoom_change(self, zoom_factor):
        if self.is_closing:
            return
        try:
            zoom_str = f"{zoom_factor:.2f}".rstrip('0').rstrip('.')
            if zoom_str.endswith('.'):
                zoom_str = zoom_str[:-1]
            self.status_bar.showMessage(f"Zoom: {zoom_str}x")
        except RuntimeError:
            pass

    def adjust_slider(self, d):
        self.slider.setValue(max(25, min(100, self.slider.value() + d)))

    def reset_view(self):
        if self.graphics_view.scene() and self.graphics_view.scene().items():
            self.graphics_view.fitInView(self.graphics_view.scene().itemsBoundingRect(), Qt.KeepAspectRatio)
            self.graphics_view.zoom_factor = 0.25
            self.graphics_view.zoomChanged.emit(0.25)
            self.log("View reset to fit image")

    def load_preview_images(self):
        if self.current_base or self.is_closing:
            self.reset_view()
            return
        for path in (self.latest_path, self.pwards_path):
            if path.exists():
                pix = QPixmap(str(path))
                self.graphics_view.set_image(pix)
                self.log(f'Loaded preview: {path.name}')
                self.status_bar.showMessage(f"Preview: {path.name}")
                self.reset_view()
                break

    def refresh_image(self):
        self.load_preview_images()
        if self.current_original:
            self.process_file(self.current_original)

    def run_debug_cache(self):
        files = list(Path(self.input_dir).glob('*.tif')) + list(Path(self.input_dir).glob('*.tiff'))
        if files:
            self.process_file(str(files[0]))
        else:
            self.log("No TIFF files found for debugging")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet('''
        QMainWindow, QWidget { background: #222; color: #EEE; }
        QSplitter::handle { background: #444; }
        QGroupBox { border: 1px solid #444; border-radius: 5px; margin-top: 1ex; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; color: #BBB; }
        QTabWidget::pane { border-top: 1px solid #444; }
        QSlider::groove:horizontal { height: 8px; background: #333; border-radius: 4px; }
        QSlider::handle:horizontal { background: #5D8AA8; width: 16px; margin: -4px 0; border-radius: 8px; }
        QProgressDialog { background: #333; color: #EEE; border: 1px solid #555; }
        QProgressBar { border: 1px solid #555; border-radius: 3px; text-align: center; }
        QProgressBar::chunk { background: #5D8AA8; border-radius: 2px; }
        QComboBox { border: 1px solid #555; border-radius: 3px; padding: 3px; background: #333; min-width: 6em; }
        QComboBox::drop-down { border: none; }
        QComboBox::down-arrow { image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 5px solid white; }
        QSpinBox { border: 1px solid #555; border-radius: 3px; padding: 3px; background: #333; }
        QCheckBox { spacing: 5px; color: #EEE; }
        QCheckBox::indicator { width: 16px; height: 16px; }
        QCheckBox::indicator:unchecked { border: 1px solid #777; background: #333; }
        QCheckBox::indicator:checked { border: 1px solid #777; background: #5D8AA8; }
        QCheckBox::indicator:disabled { border: 1px solid #444; background: #222; }
        QRadioButton { spacing: 5px; color: #EEE; }
        QRadioButton::indicator { width: 16px; height: 16px; }
        QRadioButton::indicator::unchecked { border: 1px solid #777; border-radius: 8px; background: #333; }
        QRadioButton::indicator:checked { border: 1px solid #777; border-radius: 8px; background: #5D8AA8; }
        QRadioButton::indicator:disabled { border: 1px solid #444; background: #222; }
    ''')
    window = MainUI()
    window.show()
    sys.exit(app.exec())
