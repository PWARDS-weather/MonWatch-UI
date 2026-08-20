import re
from pathlib import Path
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QMessageBox


class DragDropHandlerMixin:
    """Mixin providing external file drag-and-drop support."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_active = False
        self._orig_stylesheet = ""

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_active = True
            self._orig_stylesheet = self.styleSheet()
            try:
                self.setStyleSheet(self._orig_stylesheet + """
                    ZoomableGraphicsView, ViewportFrame {
                        border: 2px solid #00BCD4;
                    }
                """)
            except Exception:
                pass
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent):
        if event.mimeData().hasUrls() and self._drag_active:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent):
        self._drag_active = False
        try:
            self.setStyleSheet(self._orig_stylesheet)
        except Exception:
            pass
        event.accept()

    def dropEvent(self, event: QDropEvent):
        self._drag_active = False
        try:
            self.setStyleSheet(self._orig_stylesheet)
        except Exception:
            pass
        try:
            self._process_drop(event)
        except Exception as e:
            try:
                self.window().log(f"Drop error: {e}")
            except Exception:
                pass
            import traceback
            traceback.print_exc()
        event.acceptProposedAction()

    def _process_drop(self, event: QDropEvent):
        urls = event.mimeData().urls()
        nc_files = []
        sataid_files = []
        sataid_folders = []
        shp_files = []
        cwa_or_kml_files = []
        dat_files = []
        hrit_files = []
        unrecognized = []
        for url in urls:
            fp = url.toLocalFile()
            if not fp:
                continue
            p = Path(fp)
            if p.is_dir() and p.name.lower().endswith('.nc'):
                subs = sorted((str(f) for f in p.glob('*.nc') if f.is_file()))
                if subs:
                    nc_files.extend(subs)
                else:
                    unrecognized.append(fp)
                continue
            low = fp.lower()
            if p.is_dir() and p.name.upper().startswith('HRIT_'):
                subs = sorted((str(f) for f in p.glob('*')
                               if f.is_file() and f.name.upper().startswith('HRIT_')
                               and not f.name.endswith('.gz')))
                if subs:
                    hrit_files.extend(subs)
                else:
                    unrecognized.append(fp)
                continue
            if low.endswith('.nc'):
                nc_files.append(fp)
            elif p.name.upper().startswith('HRIT_MTSAT') and not low.endswith('.gz'):
                hrit_files.append(fp)
            elif (low.endswith('.z') or 'SATAID' in fp.upper()
                    or re.search(r'\.z\d{4}$', low)):
                if Path(fp).is_dir():
                    sataid_folders.append(fp)
                else:
                    sataid_files.append(fp)
            elif low.endswith('.shp'):
                shp_files.append(fp)
            elif low.endswith(('.xml', '.kml', '.kmz', '.json')):
                cwa_or_kml_files.append(fp)
            elif low.endswith('.dat'):
                dat_files.append(fp)
            else:
                unrecognized.append(fp)
        all_known = (nc_files or sataid_files or sataid_folders
                     or shp_files or cwa_or_kml_files or dat_files or hrit_files)
        if unrecognized and not all_known:
            QMessageBox.warning(self, "Unsupported Format",
                f"Cannot open:\n{chr(10).join(unrecognized)}"
                f"\n\nUnsupported or unrecognized file format.")
            return
        win = self.window()
        if nc_files:
            try:
                win.process_dropped_nc_files(nc_files)
                self._log(win, f"Dropped {len(nc_files)} NetCDF file(s)")
            except Exception as e:
                self._log(win, f"CRITICAL ERROR calling process_dropped_nc_files: {e}")
                import traceback
                self._log(win, traceback.format_exc())
        if hrit_files:
            try:
                win.process_dropped_nc_files(hrit_files)
                self._log(win, f"Dropped {len(hrit_files)} MTSAT HRIT file(s)")
            except Exception as e:
                self._log(win, f"CRITICAL ERROR calling process_dropped_nc_files: {e}")
                import traceback
                self._log(win, traceback.format_exc())
        if sataid_files:
            win._process_files(sataid_files)
            self._log(win, f"Dropped {len(sataid_files)} SATAID file(s)")
        if sataid_folders:
            for fp in sataid_folders:
                win._handle_sataid_folder_drop(Path(fp))
                self._log(win, f"Dropped SATAID folder: {Path(fp).name}")
        if shp_files:
            try:
                win._load_dropped_shapefile(shp_files[0])
                self._log(win, f"Dropped shapefile: {Path(shp_files[0]).name}")
            except Exception as e:
                self._log(win, f"Error loading dropped shapefile: {e}")
                import traceback
                self._log(win, traceback.format_exc())
        if cwa_or_kml_files:
            kmz_kml = [f for f in cwa_or_kml_files if f.lower().endswith(('.kml', '.kmz'))]
            if kmz_kml:
                win._remove_dropped_kml_overlay()
            for fp in cwa_or_kml_files:
                try:
                    win._load_dropped_cwa_or_kml(fp, clear_existing=False)
                    self._log(win, f"Dropped: {Path(fp).name}")
                except Exception as e:
                    self._log(win, f"Error loading dropped file: {e}")
                    import traceback
                    self._log(win, traceback.format_exc())
        if dat_files:
            for fp in dat_files:
                try:
                    win._load_dropped_pagasa_dat(fp)
                    self._log(win, f"Dropped PAGASA track: {Path(fp).name}")
                except Exception as e:
                    self._log(win, f"Error loading PAGASA .dat: {e}")
                    import traceback
                    self._log(win, traceback.format_exc())
        if unrecognized and all_known:
            QMessageBox.warning(self, "Unsupported Format",
                f"Cannot open:\n{chr(10).join(unrecognized)}"
                f"\n\nUnsupported or unrecognized file format.")

    def _log(self, win, msg):
        try:
            win.log(msg)
            if hasattr(win, 'status_bar'):
                win.status_bar.showMessage(msg, 5000)
        except Exception:
            pass
