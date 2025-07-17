import sys
import os
import io
from pathlib import Path
import importlib.util
from PySide6.QtCore import QPointF
import logging
from PIL import Image
Image.MAX_IMAGE_PIXELS = None

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel,
    QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QTextEdit, QSlider, QFrame,
    QFileDialog, QSizePolicy, QGraphicsView, QGraphicsScene, QSplitter,
    QStatusBar, QMenu, QProgressDialog, QMessageBox
)
from PySide6.QtGui import (
    QPixmap, QIcon, QDrag, QMouseEvent,
    QDragEnterEvent, QDropEvent, QWheelEvent, QImage, QPainter, QFont
)
from PySide6.QtCore import Qt, QUrl, QMimeData, QObject, Signal, QThread, QSize
from PIL import ImageQt

# --- logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# --- paths ---
top_dir = Path(__file__).resolve().parent.parent
src_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(src_dir))

# load cache manager
def _load_cache_manager():
    spec = importlib.util.spec_from_file_location(
        'cache', top_dir / 'Process' / 'cache.py'
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CacheManager

CacheManager = _load_cache_manager()

# --- worker factory ---
def cache_worker_factory(input_dir, cache_dir):
    class CacheWorker(QObject):
        progress = Signal(str)
        finished = Signal(str)
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
            try:
                cm = CacheManager(input_dir=self.input_dir, cache_dir=self.cache_dir)
                cm.generate_pyramidal_cache()
            except Exception as ex:
                self.cache_activity.emit(f"Cache failed: {ex}")
            finally:
                sys.stdout = old_stdout

            if not self._is_cancelled:
                for line in buf.getvalue().splitlines():
                    self.progress.emit(line)
                self.cache_activity.emit("Cache complete")
                self.finished.emit(self.input_dir)

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
    maxZoomReached = Signal()  # New signal for max zoom

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.max_zoom_threshold = 5.5  # Threshold for loading original TIFF
        self.using_original = False  # Track if we're using original TIFF

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
        self.zoomChanged.emit(self.zoom_factor)

    def enable_zoom(self, flag: bool):
        self.zoom_enabled = flag

    def wheelEvent(self, event: QWheelEvent):
        if not self.zoom_enabled:
            event.ignore()
            return
            
        # Calculate new zoom factor
        if event.angleDelta().y() > 0:
            factor = 1.25
        else:
            factor = 1 / 1.25
            
        new_zoom = self.zoom_factor * factor
        
        # Check if we're crossing the max zoom threshold
        if (self.zoom_factor < self.max_zoom_threshold and 
            new_zoom >= self.max_zoom_threshold and 
            not self.using_original):
            self.maxZoomReached.emit()
            return
            
        # Apply zoom constraints
        if new_zoom < self.min_zoom:
            factor = self.min_zoom / self.zoom_factor
        elif new_zoom > self.max_zoom:
            factor = self.max_zoom / self.zoom_factor
            
        self.zoom_factor *= factor
        self.scale(factor, factor)
        self.zoomChanged.emit(self.zoom_factor)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # type: ignore
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            fp = url.toLocalFile()
            if fp.lower().endswith(('.tif', '.tiff')):
                self.window().process_file(fp)
        event.acceptProposedAction()
        
    def contextMenuEvent(self, event):
        menu = QMenu(self)
        add_file = menu.addAction("Add File")
        add_folder = menu.addAction("Add Folder")
        
        action = menu.exec_(event.globalPos())
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
        self.logo_overlay.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.logo_overlay.pixmap():
            margin = 10
            logo_size = self.logo_overlay.sizeHint()
            x = self.width() - logo_size.width() - margin
            self.logo_overlay.move(x, margin)

    def set_logo(self, pixmap: QPixmap):
        if pixmap.isNull():
            self.logo_overlay.hide()
            return
            
        scaled_pix = pixmap.scaledToWidth(80, Qt.SmoothTransformation)
        self.logo_overlay.setPixmap(scaled_pix)
        self.logo_overlay.show()
        self.resizeEvent(None)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # type: ignore
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            fp = url.toLocalFile()
            if fp.lower().endswith(('.tif', '.tiff')):
                self.window().process_file(fp)
        event.acceptProposedAction()


class MainUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Monwatch UI – Satellite Renderer")
        self.resize(1440, 900)
        
        app_font = QFont("Segoe UI", 9)
        QApplication.setFont(app_font)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")

        self.input_dir = str(top_dir / 'test')
        self.cache_dir = str(top_dir / 'cache' / 'images')
        os.makedirs(self.cache_dir, exist_ok=True)

        images_dir = top_dir / 'public' / 'images'
        self.latest_path = images_dir / 'latest.png'
        self.pwards_path = images_dir / 'PWARDS.png'
        self.logo_path = images_dir / 'logo.png'

        self.current_base = None
        self.current_original = None  # Store path to original TIFF
        self.current_zoom = 1.0
        self.zoom_levels = [0.25, 0.5, 1.0]
        self.is_closing = False
        self.active_thread = None
        self.active_worker = None
        self.using_original = False  # Track if we're using original TIFF

        container = QWidget()
        self.setCentralWidget(container)
        self.main_layout = QHBoxLayout(container)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(2)
        self.main_layout.addWidget(self.splitter)

        self._init_left_panel()
        self._init_center_panel()
        self._init_right_panel()

        self.load_preview_images()
        self.splitter.setSizes([200, 800, 300])

    def closeEvent(self, event):
        self.is_closing = True
        if self.active_worker:
            self.active_worker.cancel()
        if self.active_thread and self.active_thread.isRunning():
            self.active_thread.quit()
            self.active_thread.wait(2000)
        super().closeEvent(event)

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
            ('🔄 Refresh', self.refresh_image, "#0277BD"),
            ('🔍 Reset View', self.reset_view, "#5D4037"),
            ('🧪 Debug Cache', self.run_debug_cache, "#D84315"),
        ]
        
        for text, callback, color in button_data:
            btn = QPushButton(text)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {color};
                    color: white;
                    padding: 12px;
                    border-radius: 4px;
                    font-weight: bold;
                }}
                QPushButton:hover {{ background: #455A64; }}
            """)
            btn.clicked.connect(callback)
            layout.addWidget(btn)
        
        layout.addStretch()
        panel.setMinimumWidth(180)
        self.splitter.addWidget(panel)

    def _init_center_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        
        self.graphics_view = ZoomableGraphicsView()
        self.graphics_view.setScene(QGraphicsScene())
        self.graphics_view.zoomChanged.connect(self.handle_zoom_change)
        self.graphics_view.maxZoomReached.connect(self.confirm_load_original)  # Connect to confirmation

        self.viewport_frame = ViewportFrame(self)
        fl = QVBoxLayout(self.viewport_frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(self.graphics_view)

        if self.logo_path.exists():
            self.viewport_frame.set_logo(QPixmap(str(self.logo_path)))

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(100)
        self.slider.valueChanged.connect(self.handle_quality_change)
        
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(QLabel("Quality:"))
        slider_layout.addWidget(self.slider)
        
        btns = QHBoxLayout()
        for txt, val in [('🔍-', -10), ('🔍+', 10)]:
            btn = QPushButton(txt)
            btn.setStyleSheet("padding: 6px;")
            btn.clicked.connect(lambda _, v=val: self.adjust_slider(v))
            btns.addWidget(btn)
        
        layout.addWidget(self.viewport_frame, 1)
        layout.addLayout(slider_layout)
        layout.addLayout(btns)
        
        self.splitter.addWidget(panel)

    def _init_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(8)
        
        file_btns = QHBoxLayout()
        btn_styles = """
            QPushButton { 
                padding: 8px; 
                border-radius: 4px; 
                background: #37474F; 
                color: white; 
            }
            QPushButton:hover { background: #455A64; }
        """
        
        add_btn = QPushButton("Add File")
        add_btn.setIcon(QIcon.fromTheme("list-add"))
        add_btn.clicked.connect(self.choose_file)
        add_btn.setStyleSheet(btn_styles)
        
        folder_btn = QPushButton("Open Folder")
        folder_btn.setIcon(QIcon.fromTheme("folder-open"))
        folder_btn.clicked.connect(self.choose_folder)
        folder_btn.setStyleSheet(btn_styles)
        
        file_btns.addWidget(add_btn)
        file_btns.addWidget(folder_btn)
        layout.addLayout(file_btns)
        
        file_label = QLabel('🗂 Files')
        file_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(file_label)
        
        self.file_list = FileListWidget()
        self.populate_file_list()
        layout.addWidget(self.file_list)
        
        log_label = QLabel('📝 System Log')
        log_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(log_label)
        
        self.log_console = QTextEdit()
        self.log_console.setReadOnly(True)
        self.log_console.setStyleSheet('''
            QTextEdit {
                background: #111; 
                color: #0f0; 
                font-family: Consolas, monospace;
                font-size: 10pt;
            }
        ''')
        
        clear_btn = QPushButton("Clear Log")
        clear_btn.setStyleSheet(btn_styles)
        clear_btn.clicked.connect(lambda: self.log_console.clear())
        
        log_layout = QVBoxLayout()
        log_layout.addWidget(self.log_console)
        log_layout.addWidget(clear_btn)
        
        layout.addLayout(log_layout)
        
        self.splitter.addWidget(panel)
        panel.setMinimumWidth(280)

    def log(self, msg):
        if self.is_closing:
            return
            
        try:
            if hasattr(self, 'log_console'):
                self.log_console.append(msg)
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

    def choose_folder(self):
        d = QFileDialog.getExistingDirectory(self, 'Select Folder', self.input_dir)
        if d: 
            self.input_dir = d
            self.populate_file_list()
            self.log(f'Selected folder: {d}')
            self.status_bar.showMessage(f"Folder: {d}")

    def choose_file(self):
        p, _ = QFileDialog.getOpenFileName(
            self, 
            'Open TIFF File', 
            self.input_dir, 
            'TIFF Files (*.tif *.tiff)'
        )
        if p: 
            self.process_file(p)

    def process_file(self, fp):
        self.log(f'Starting cache generation: {fp}')
        self.status_bar.showMessage(f"Caching: {Path(fp).name}...")
        self.current_original = fp  # Store original TIFF path
        
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
        wk.finished.connect(lambda _: self._after_cache(fp), Qt.QueuedConnection)
        
        th.started.connect(wk.run)
        wk.finished.connect(th.quit)
        th.finished.connect(th.deleteLater)
        wk.finished.connect(wk.deleteLater)
        
        th.start()

    def _after_cache(self, fp):
        if self.is_closing:
            return
            
        self.current_base = Path(fp).stem
        self.current_zoom = 1.0
        self.slider.setValue(100)
        self.using_original = False
        self.load_current_image()
        self.log(f'Completed: {fp}')
        self.status_bar.showMessage(f"Loaded: {self.current_base}")
        self.active_worker = None
        self.active_thread = None

    def handle_quality_change(self, value):
        if not self.current_base or self.is_closing:
            return
        
        # Reset to cached image when quality changes
        self.using_original = False
        
        quality_index = min(int(value / 33.34), 2)
        new_zoom = self.zoom_levels[quality_index]

        if new_zoom != self.current_zoom:
            self.current_zoom = new_zoom
            self.load_current_image()

    def load_current_image(self):
        if not self.current_base or self.is_closing: 
            return
        
        if self.using_original:
            self.load_original_tiff()
            return
            
        zoom_level = self.current_zoom
    
        if zoom_level == 1.0:
            file_path = Path(self.cache_dir) / f'{self.current_base}_x1.0.png'
            if not file_path.exists():
                file_path = Path(self.cache_dir) / f'{self.current_base}_0.png'
        else:
            file_path = Path(self.cache_dir) / f'{self.current_base}_x{zoom_level:.2f}.png'
    
        if not file_path.exists():
            self.log(f"Image not found: {file_path}")
            return
        
        try:
            img = Image.open(file_path)
            qimg = ImageQt.ImageQt(img.convert('RGBA'))
            pixmap = QPixmap.fromImage(qimg)
        
            preserve = self.graphics_view.scene() and self.graphics_view.scene().items()
            self.graphics_view.set_image(pixmap, preserve, zoom_level)
        
            self.log(f'Loaded: {file_path.name} (Quality: {zoom_level:.2f})')
        except Exception as e:
            self.log(f"Error loading image: {str(e)}")

    def confirm_load_original(self):
        """Show confirmation dialog before loading original TIFF"""
        if not self.current_original or self.using_original or self.is_closing:
            return
            
        reply = QMessageBox.question(
            self,
            "High Resolution Warning",
            "Are you sure your PC can handle loading the full TIFF?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            # Enable max zoom for TIFF
            self.graphics_view.max_zoom = 1000.0
            self.load_original_tiff()
        else:
            # Reset zoom to just below threshold
            self.graphics_view.zoom_factor = self.graphics_view.max_zoom_threshold - 0.1
            self.graphics_view.resetTransform()
            self.graphics_view.scale(self.graphics_view.zoom_factor, self.graphics_view.zoom_factor)

    def load_original_tiff(self):
        """Load the original TIFF file directly at full resolution"""
        if not self.current_original or self.using_original or self.is_closing:
            return
            
        try:
            # Show progress dialog
            progress = QProgressDialog(
                "Loading original TIFF...", 
                "Cancel", 
                0, 
                0, 
                self
            )
            progress.setWindowTitle("Loading High Resolution")
            progress.setWindowModality(Qt.WindowModal)
            progress.setCancelButton(None)
            progress.show()
            QApplication.processEvents()
            
            # Load original TIFF
            self.log(f"Loading original TIFF: {Path(self.current_original).name}")
            self.status_bar.showMessage("Loading original TIFF...")
            
            # Open with PIL and convert to QPixmap
            with Image.open(self.current_original) as img:
                # Get current viewport size
                viewport_size = self.graphics_view.viewport().size()
                
                # Calculate downsampling ratio to fit viewport
                width_ratio = img.width / viewport_size.width()
                height_ratio = img.height / viewport_size.height()
                downscale_factor = max(width_ratio, height_ratio) / 10
                
                # Downsample if necessary for initial display
                if downscale_factor > 1:
                    new_width = int(img.width / downscale_factor)
                    new_height = int(img.height / downscale_factor)
                    img = img.resize((new_width, new_height), Image.LANCZOS)
                
                qimg = ImageQt.ImageQt(img.convert('RGBA'))
                pixmap = QPixmap.fromImage(qimg)
            
            # Preserve current view
            preserve = self.graphics_view.scene() and self.graphics_view.scene().items()
            self.graphics_view.set_image(pixmap, preserve, 1.0, True)
            
            self.using_original = True
            self.log("Loaded original TIFF at full resolution")
            self.status_bar.showMessage(f"Original TIFF: {Path(self.current_original).name}")
            
        except Exception as e:
            self.log(f"Error loading original TIFF: {str(e)}")
            self.status_bar.showMessage(f"TIFF load failed: {str(e)}")
        finally:
            progress.close()

    def handle_zoom_change(self, zoom_factor):
        if self.is_closing:
            return
        try:
            self.status_bar.showMessage(f"Zoom: {zoom_factor:.2f}x")
        except RuntimeError:
            pass

    def adjust_slider(self, d): 
        self.slider.setValue(max(0, min(100, self.slider.value() + d)))
        
    def reset_view(self):
        if self.graphics_view.scene() and self.graphics_view.scene().items():
            self.graphics_view.fitInView(
                self.graphics_view.scene().itemsBoundingRect(), 
                Qt.KeepAspectRatio
            )
            self.graphics_view.zoom_factor = 1.0
            self.graphics_view.zoomChanged.emit(1.0)
            self.log("View reset to fit image")
            self.using_original = False  # Reset to cached image
            self.graphics_view.max_zoom = 5.0  # Reset max zoom

    def load_preview_images(self):
        if self.current_base or self.is_closing: 
            return
            
        for path in (self.latest_path, self.pwards_path):
            if path.exists():
                pix = QPixmap(str(path))
                self.graphics_view.set_image(pix)
                self.log(f'Loaded preview: {path.name}')
                self.status_bar.showMessage(f"Preview: {path.name}")
                break

    def refresh_image(self): 
        self.load_preview_images()

    def run_debug_cache(self):
        files = list(Path(self.input_dir).glob('*.tif')) + list(Path(self.input_dir).glob('*.tiff'))
        if files: 
            self.process_file(str(files[0]))
        else:
            self.log("No TIFF files found for debugging")

if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyleSheet('''
        QMainWindow { 
            background: #222; 
            color: #EEE;
        }
        QSplitter::handle { 
            background: #444; 
        }
        QListWidget {
            background: #1A1A1A;
            color: #DDD;
            border: 1px solid #444;
        }
        QSlider::groove:horizontal {
            height: 8px;
            background: #333;
            border-radius: 4px;
        }
        QSlider::handle:horizontal {
            background: #5D8AA8;
            width: 16px;
            margin: -4px 0;
            border-radius: 8px;
        }
        QProgressDialog {
            background: #333;
            color: #EEE;
        }
    ''')
    window = MainUI()
    window.show()
    sys.exit(app.exec())