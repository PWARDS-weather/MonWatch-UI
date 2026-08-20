# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Module: ui/MultiPanelWindow.py
# Description: MultiPanel — a 2x2 (four-panel) view mirroring the McIDAS-V
#              "Four Panels" display tab. A viewport (the main display or a
#              multi-viewport window) can be split into 4 independently
#              configurable panels with an optional shared (synced) view.
#
# Panels share the same display-surface machinery as the existing
# MultiViewportWindow (viewport mirror / bands / animation / forecast /
# 3d globe) via ViewportSurfaceWidget (see ui/viewport_surface.py).
# =============================================================================

from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QFrame, QPushButton, QCheckBox, QSizePolicy,
    QAbstractButton, QAbstractSlider, QScrollBar, QComboBox,
)

from .viewport_surface import ViewportSurfaceWidget


class ViewportPanel(QWidget):
    """One cell of a MultiPanel layout: header (name, mode, Sync check) plus
    a ViewportSurfaceWidget display surface.

    Panels can be selected by clicking anywhere in the cell (Ctrl+click toggles
    a multi-selection). The selected set drives where config changes land, and
    the header is highlighted while the panel is selected.
    """

    viewChanged = Signal(object)
    clicked = Signal(object, bool)  # (panel, ctrl)

    def __init__(self, app_ref, index, parent=None):
        super().__init__(parent)
        self.index = index
        self._app = app_ref
        self._selected = False
        self.setObjectName("ViewportPanel")
        self.setMinimumSize(160, 120)
        self.setStyleSheet(
            "ViewportPanel { border: 1px solid #333; background: #101010; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = QFrame()
        self._header.setFixedHeight(24)
        self._header.setStyleSheet("background: #151515; border-bottom: 1px solid #333;")
        hdr = QHBoxLayout(self._header)
        hdr.setContentsMargins(6, 2, 6, 2)
        hdr.setSpacing(6)
        self._name_lbl = QLabel(f"Panel {index + 1}")
        self._name_lbl.setStyleSheet("color: #8AB4F8; font-weight: bold; font-size: 9px;")
        hdr.addWidget(self._name_lbl)
        self._mode_lbl = QLabel("viewport")
        self._mode_lbl.setStyleSheet("color: #CE93D8; font-size: 9px;")
        hdr.addWidget(self._mode_lbl)
        hdr.addStretch()
        self._sync_cb = QCheckBox("Sync")
        self._sync_cb.setStyleSheet("QCheckBox { color: #888; font-size: 8px; }")
        self._sync_cb.setChecked(True)
        hdr.addWidget(self._sync_cb)
        root.addWidget(self._header)

        self.surface = ViewportSurfaceWidget(app_ref, index, panel_context=True)
        root.addWidget(self.surface, 1)

        # Route simple-image view changes up for the shared-views coordinator.
        # Panels have no mirror page (they are independent), so only the
        # standalone image view is connected.
        try:
            self.surface._bands_view.viewChanged.connect(self._emit_view_changed)
            self.surface._anim_view.viewChanged.connect(self._emit_view_changed)
        except AttributeError:
            pass

    def _emit_view_changed(self):
        self.viewChanged.emit(self)

    # -- Delegation -----------------------------------------------------------

    def update_config(self, mode=None, band=None, product=None,
                      overlays_enabled=None, grid_enabled=None,
                      coast_enabled=None, force=False):
        changed = self.surface.update_config(
            mode=mode, band=band, product=product,
            overlays_enabled=overlays_enabled,
            grid_enabled=grid_enabled, coast_enabled=coast_enabled,
            force=force)
        if mode is not None:
            self._mode_lbl.setText(mode)
        return changed

    def set_band_pixmap(self, pixmap):
        self.surface.set_band_pixmap(pixmap)

    def set_snapshot_pixmap(self, pixmap):
        self.surface.set_snapshot_pixmap(pixmap)

    def set_composite_pixmap(self, pixmap):
        self.surface.set_composite_pixmap(pixmap)

    def set_animation_pixmap(self, pixmap):
        self.surface.set_animation_pixmap(pixmap)

    def set_forecast_pixmap(self, pixmap):
        self.surface.set_forecast_pixmap(pixmap)

    def push_texture_to_globe(self, image=None):
        self.surface.push_texture_to_globe(image)

    def push_forecast_to_globe(self, image=None):
        self.surface.push_forecast_to_globe(image)

    def current_mode(self):
        return self.surface.current_mode()

    def sync_enabled(self):
        return self._sync_cb.isChecked()

    # -- Selection / header highlight -----------------------------------------

    def set_selected(self, selected):
        self._selected = bool(selected)
        self._update_header_style()

    def is_selected(self):
        return self._selected

    def _update_header_style(self):
        if self._selected:
            panel_border = "#00E5FF"
            hdr_bg = "#1E3A5F"
            hdr_border = "#00E5FF"
            name_color = "#FFFFFF"
        else:
            panel_border = "#333"
            hdr_bg = "#151515"
            hdr_border = "#333"
            name_color = "#8AB4F8"
        self.setStyleSheet(
            f"ViewportPanel {{ border: 1px solid {panel_border}; background: #101010; }}")
        self._header.setStyleSheet(
            f"background: {hdr_bg}; border-bottom: 1px solid {hdr_border};")
        self._name_lbl.setStyleSheet(
            f"color: {name_color}; font-weight: bold; font-size: 9px;")

    def cleanup(self):
        if hasattr(self, 'surface') and self.surface is not None:
            self.surface.cleanup()


class PanelViewSync:
    """McIDAS-V "Share Views" coordinator for a set of panels.

    When the master Sync toggle is enabled, a panel whose own Sync check is on
    propagates its normalized view state (zoom + visible centre) to the other
    synced panels.
    """

    def __init__(self, panels):
        self.panels = list(panels)
        self.master_enabled = False
        self._suppress = False
        for p in self.panels:
            p.viewChanged.connect(self._on_view_changed)

    def set_master(self, enabled):
        self.master_enabled = bool(enabled)

    def _on_view_changed(self, source):
        if not self.master_enabled or self._suppress:
            return
        if not source.sync_enabled():
            return
        state = source.surface.state()
        if state is None:
            return
        self._suppress = True
        try:
            for p in self.panels:
                if p is source:
                    continue
                if p.sync_enabled():
                    p.surface.apply_state(state)
        finally:
            self._suppress = False


class ViewportContainer(QWidget):
    """Hosts an R x C grid of ViewportPanels plus the shared-views coordinator."""

    def __init__(self, app_ref, rows=2, cols=2, parent=None):
        super().__init__(parent)
        self._app = app_ref
        self._rows = rows
        self._cols = cols
        self._panels = []
        self._selected = set()
        self._last_clicked = None
        self._selection_cb = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(2)
        self._sync = None
        self._rebuild()

    def _rebuild(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._panels = []
        self._selected = set()
        self._last_clicked = None
        for r in range(self._rows):
            for c in range(self._cols):
                idx = r * self._cols + c
                panel = ViewportPanel(self._app, idx)
                self._grid.addWidget(panel, r, c)
                self._panels.append(panel)
        for p in self._panels:
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            p.installEventFilter(self)
            for child in p.findChildren(QWidget):
                child.installEventFilter(self)
        self._sync = PanelViewSync(self._panels)

    def panels(self):
        return list(self._panels)

    def panel(self, i):
        if 0 <= i < len(self._panels):
            return self._panels[i]
        return None

    def set_sync_master(self, enabled):
        if self._sync is not None:
            self._sync.set_master(enabled)

    def sync_master_enabled(self):
        return bool(self._sync and self._sync.master_enabled)

    # -- Selection --------------------------------------------------------------

    def set_selection_cb(self, cb):
        self._selection_cb = cb

    def selected_panels(self):
        return [p for p in self._panels if p.is_selected()]

    def last_clicked(self):
        return self._last_clicked

    def select_only(self, panel):
        self._selected = {panel}
        self._last_clicked = panel
        for p in self._panels:
            p.set_selected(p is panel)
        self._emit_selection()

    def toggle_selection(self, panel):
        self._last_clicked = panel
        if panel.is_selected():
            self._selected.discard(panel)
            panel.set_selected(False)
        else:
            self._selected.add(panel)
            panel.set_selected(True)
        self._emit_selection()

    def _emit_selection(self):
        if self._selection_cb is not None:
            try:
                self._selection_cb(self.selected_panels(), self._last_clicked)
            except Exception:
                pass

    @staticmethod
    def _is_interactive(obj):
        return isinstance(obj, (QAbstractButton, QAbstractSlider, QComboBox))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            panel = None
            w = obj
            while w is not None:
                if isinstance(w, ViewportPanel):
                    panel = w
                    break
                w = w.parentWidget()
            if panel is not None and not self._is_interactive(obj):
                ctrl = bool(event.modifiers() & Qt.ControlModifier)
                if ctrl:
                    self.toggle_selection(panel)
                else:
                    self.select_only(panel)
        return False

    def cleanup(self):
        for p in self._panels:
            try:
                p.cleanup()
            except RuntimeError:
                pass


class _PanelHostHeader(QWidget):
    """Shared compact header used by both the floating MultiPanelWindow and
    the embedded main-viewport split host."""

    def __init__(self, title, on_sync, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setStyleSheet("background: #1e1e1e; border-bottom: 1px solid #333;")
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(8)
        self._title = QLabel(title)
        self._title.setStyleSheet("color: #00E5FF; font-weight: bold; font-size: 10px;")
        h.addWidget(self._title)
        self.sync_cb = QCheckBox("Sync Views (Share Views)")
        self.sync_cb.setStyleSheet("QCheckBox { color: #B0BEC5; font-size: 9px; }")
        self.sync_cb.setChecked(True)
        self.sync_cb.toggled.connect(on_sync)
        h.addWidget(self.sync_cb)
        h.addStretch()


class MultiPanelWindow(QWidget):
    """Floating 2x2 four-panel window (McIDAS-V "Four Panels" tab analog)."""

    def __init__(self, index, app_ref):
        super().__init__()
        self.index = index
        self._app = app_ref
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle(f"Multi Panel #{index + 1} — 4-Panel Split")
        self.setMinimumSize(560, 420)
        self.resize(900, 640)
        self.setAttribute(Qt.WA_DeleteOnClose)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _PanelHostHeader(
            f"Multi Panel #{index + 1}", self._on_sync_master)
        root.addWidget(self._header)

        self.container = ViewportContainer(app_ref, 2, 2)
        root.addWidget(self.container, 1)

    def _on_sync_master(self, checked):
        if not hasattr(self, 'container'):
            return
        self.container.set_sync_master(checked)
        app = getattr(self, '_app', None)
        if app is not None and hasattr(app, 'mp_sync_master_cb'):
            app.mp_sync_master_cb.setChecked(checked)

    def panels(self):
        return self.container.panels()

    def bind_selection(self, cb):
        self.container.set_selection_cb(cb)

    def closeEvent(self, event):
        if hasattr(self, 'container'):
            self.container.cleanup()
        super().closeEvent(event)


class MainSplitHost(QWidget):
    """Embedded 2x2 four-panel surface used when the MAIN viewport is split."""

    def __init__(self, app_ref, parent=None):
        super().__init__(parent)
        self._app = app_ref
        self.setObjectName("MainSplitHost")
        self.setMinimumSize(400, 300)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._header = _PanelHostHeader(
            "Main Viewport — 4-Panel Split", self._on_sync_master)
        root.addWidget(self._header)

        self.container = ViewportContainer(app_ref, 2, 2)
        root.addWidget(self.container, 1)

    def _on_sync_master(self, checked):
        if not hasattr(self, 'container'):
            return
        self.container.set_sync_master(checked)
        app = getattr(self, '_app', None)
        if app is not None and hasattr(app, 'mp_sync_master_cb'):
            app.mp_sync_master_cb.setChecked(checked)

    def panels(self):
        return self.container.panels()

    def bind_selection(self, cb):
        self.container.set_selection_cb(cb)

    def cleanup(self):
        if hasattr(self, 'container'):
            self.container.cleanup()


class MultiPanelManager:
    """Manages the active MultiPanel layout.

    Placement is either 'main' (embedded in the main center viewport), or
    'floating' (a separate MultiPanelWindow). Only one is active at a time,
    mirroring McIDAS-V's single Four-Panels display tab.
    """

    def __init__(self, app):
        self.app = app
        self.windows = []
        self.main_host = None
        self.placement = "none"

    # -- Placement -----------------------------------------------------------

    def attach_main_host(self, host):
        self.main_host = host
        self._apply_visibility()

    def set_placement(self, placement):
        placement = placement if placement in ("main", "floating", "none") else "none"
        self.placement = placement
        if placement == "floating" and not self.windows:
            win = MultiPanelWindow(0, self.app)
            self.windows.append(win)
        self._apply_visibility()

    def _apply_visibility(self):
        stack = getattr(self.app, 'viewport_stack', None)
        main_view = getattr(self.app, 'graphics_view', None)
        if self.placement == "floating":
            if self.windows:
                self.windows[0].show()
            if self.main_host is not None:
                self.main_host.setVisible(False)
        elif self.placement == "main":
            for w in self.windows:
                w.hide()
            if self.main_host is not None:
                self.main_host.setVisible(True)
        else:
            for w in self.windows:
                w.hide()
            if self.main_host is not None:
                self.main_host.setVisible(False)
        if stack is not None and main_view is not None:
            if self.placement == "main" and self.main_host is not None:
                stack.setCurrentWidget(self.main_host)
            else:
                stack.setCurrentWidget(main_view)

    # -- Panel access ----------------------------------------------------------

    def active_container(self):
        if self.placement == "main":
            if self.main_host is not None:
                return self.main_host.container
            return None
        if self.placement == "floating" and self.windows:
            return self.windows[0].container
        return None

    def active_header(self):
        if self.placement == "main":
            return self.main_host._header if self.main_host is not None else None
        if self.placement == "floating" and self.windows:
            return self.windows[0]._header
        return None

    def panels(self):
        c = self.active_container()
        if c is None:
            return []
        return c.panels()

    def panel(self, i):
        ps = self.panels()
        if 0 <= i < len(ps):
            return ps[i]
        return None

    def panels_in_mode(self, mode):
        return [p for p in self.panels() if p.current_mode() == mode]

    def selected_panels(self):
        c = self.active_container()
        if c is None:
            return []
        return c.selected_panels()

    def last_clicked_panel(self):
        c = self.active_container()
        if c is None:
            return None
        return c.last_clicked()

    def bind_selection(self, cb):
        c = self.active_container()
        if c is not None and hasattr(c, 'set_selection_cb'):
            c.set_selection_cb(cb)

    def is_active(self):
        return self.placement in ("main", "floating")

    # -- Teardown ---------------------------------------------------------------

    def destroy(self):
        for w in self.windows:
            try:
                w.close()
                w.deleteLater()
            except RuntimeError:
                pass
        self.windows.clear()
        if self.main_host is not None:
            try:
                self.main_host.cleanup()
            except RuntimeError:
                pass