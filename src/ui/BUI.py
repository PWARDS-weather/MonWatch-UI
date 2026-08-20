from __future__ import annotations
import os
import json
from datetime import datetime, timedelta
from typing import Optional
from PySide6.QtCore import Qt, QTimer, QStandardPaths, QFileSystemWatcher
from PySide6.QtGui import QAction, QKeySequence, QImage, QVector3D
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QMessageBox, QFileDialog, QProgressDialog, QLabel, QMenu, QMenuBar,
    QTreeWidget, QTreeWidgetItem,
)
from ..core.animation_camera import CameraState
from ..core.keyframe_engine import KeyframeTimeline, AnimationPlayer
from ..core.easing import Easing
from ..data.models import BroadcastProject, Section, Segment, Clip
from ..core.command_stack import CommandStack, AddKeyframeCommand
from ..services.utils import compute_sun_direction
from .BUI_globe import EnhancedGlobeWidget
from .BUI_timeline import TimelinePanel
from .BUI_data_panel import BroadcastDataPanel
from .BUI_overlay_panel import BroadcastOverlayPanel


class BroadcastWindow(QMainWindow):
    def __init__(self, main_ui=None, start_datetime=None):
        super().__init__()
        self._main_ui = main_ui
        self._start_dt = self._parse_datetime(start_datetime) if start_datetime else datetime.utcnow()

        self._project = BroadcastProject()
        self._command_stack = CommandStack()
        self._current_clip: Optional[Clip] = None

        self._active_clip_timeline = KeyframeTimeline(keyframes=[], duration=30.0, loop=False)
        self._player = AnimationPlayer(self._active_clip_timeline, self)

        self._build_menu_bar()
        self._build_ui()
        self._connect_signals()
        self._apply_theme()
        self._set_default_view()
        self._reset_view_action.triggered.connect(self.globe.reset_view)
        self._setup_file_watchers()
        self._sync_sun_from_datetime()

    def _parse_datetime(self, dt_str: str) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            try:
                parts = dt_str.replace("Z", "+00:00").split("+")
                dt = datetime.fromisoformat(parts[0])
                if len(parts) > 1:
                    offset = parts[1].replace(":", "")
                    sign = 1 if offset[0] != '-' else -1
                    hrs = int(offset[-4:-2]) if len(offset) > 2 else 0
                    mins = int(offset[-2:]) if len(offset) > 2 else 0
                    dt -= timedelta(hours=sign*hrs, minutes=sign*mins)
                return dt
            except Exception:
                return None

    def _apply_theme(self):
        try:
            from ..core.helpers import THEMES, build_stylesheet
            self.setStyleSheet(build_stylesheet(THEMES["Dark (Default)"]))
        except Exception:
            self.setStyleSheet("QMainWindow{background:#121212;}")

    def _build_menu_bar(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        save_action = QAction("Save Project", self)
        save_action.setShortcut(QKeySequence("Ctrl+S"))
        save_action.triggered.connect(self._save_project)
        file_menu.addAction(save_action)
        open_action = QAction("Open Project", self)
        open_action.setShortcut(QKeySequence("Ctrl+O"))
        open_action.triggered.connect(self._open_project)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        export_action = QAction("Export MP4...", self)
        export_action.setShortcut(QKeySequence("Ctrl+E"))
        export_action.triggered.connect(self._export_mp4)
        file_menu.addAction(export_action)
        file_menu.addSeparator()
        close_action = QAction("Close Broadcasting", self)
        close_action.setShortcut(QKeySequence("Escape"))
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        edit_menu = mb.addMenu("&Edit")
        undo_action = QAction("Undo", self)
        undo_action.setShortcut(QKeySequence("Ctrl+Z"))
        undo_action.triggered.connect(self._undo)
        edit_menu.addAction(undo_action)
        redo_action = QAction("Redo", self)
        redo_action.setShortcut(QKeySequence("Ctrl+Y"))
        redo_action.triggered.connect(self._redo)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()
        add_kf_action = QAction("Add Keyframe", self)
        add_kf_action.setShortcut(QKeySequence("K"))
        add_kf_action.triggered.connect(self._add_keyframe)
        edit_menu.addAction(add_kf_action)

        view_menu = mb.addMenu("&View")
        self._reset_view_action = QAction("Reset Camera", self)
        self._reset_view_action.setShortcut(QKeySequence("R"))
        view_menu.addAction(self._reset_view_action)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setSpacing(4)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # -- Left sidebar (Choreographer) --
        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderLabel("Storyboard")
        self.tree_widget.setMinimumWidth(180)
        self.tree_widget.setMaximumWidth(350)
        self.tree_widget.setIndentation(15)
        self.tree_widget.setStyleSheet("""
            QTreeWidget { background: #1a1a1a; color: #ddd; border: none; outline: 0; }
            QTreeWidget::item:selected { background: #2a2a2a; color: #f472b6; }
            QTreeWidget::item:hover { background: #252525; }
        """)
        self.tree_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_widget.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree_widget.itemClicked.connect(self._on_tree_selected)
        main_layout.addWidget(self.tree_widget, 0)

        left_splitter = QSplitter(Qt.Vertical)
        left_splitter.setHandleWidth(3)
        left_splitter.setStyleSheet("QSplitter::handle{background:#333;}")

        self.globe = EnhancedGlobeWidget()

        self.timeline = TimelinePanel(self._player, self._active_clip_timeline)
        self.timeline.setMinimumHeight(120)

        left_splitter.addWidget(self.globe)
        left_splitter.addWidget(self.timeline)
        left_splitter.setStretchFactor(0, 1)
        left_splitter.setStretchFactor(1, 0)
        left_splitter.setSizes([700, 200])

        right_panel = QWidget()
        right_panel.setFixedWidth(280)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(4)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.data_panel = BroadcastDataPanel()
        self.overlay_panel = BroadcastOverlayPanel()

        right_layout.addWidget(self.data_panel, 1)
        right_layout.addWidget(self.overlay_panel, 0)

        main_layout.addWidget(left_splitter, 1)
        main_layout.addWidget(right_panel, 0)

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet("color:#888;font-size:9px;padding:2px 8px;")
        right_layout.addWidget(self._status_label)

    def _connect_signals(self):
        self.globe.captureKeyframeRequested.connect(self._add_keyframe)
        self._player.frameTick.connect(self._on_animation_frame)
        self.timeline.addKeyframeRequested.connect(self._add_keyframe)
        self.timeline.ruler.addKeyframeAtRequested.connect(self._add_keyframe_at)
        self.timeline.ruler.keyframeDragged.connect(self._sync_keyframes)
        self.timeline.props.keyframeUpdated.connect(self._sync_keyframes)
        self._player.positionChanged.connect(self._on_timeline_tick)

    def _set_default_view(self):
        self.globe.set_camera(CameraState(lat=14.6, lon=121.0, zoom=1.0, heading=0.0, pitch=15.0), animate=False)
        self.overlay_panel.set_layer_order(self.globe.layer_order)
        self.globe.orderChanged.connect(lambda o: self.overlay_panel.set_layer_order(o))

    def _sync_sun_from_datetime(self):
        if self._start_dt:
            self.overlay_panel.set_sun_from_datetime(self._start_dt)

    def _on_animation_frame(self, state: CameraState):
        self.globe.set_camera(state, animate=False)

    def _on_timeline_tick(self, t: float):
        current_dt = self._start_dt + timedelta(seconds=t)
        sun_dir = compute_sun_direction(current_dt, lat=self.globe.camera().lat)
        self.globe.set_sun_direction(sun_dir)
        self.overlay_panel.set_sun_from_datetime(current_dt)

    def _add_keyframe(self):
        if self._current_clip is None:
            return
        cam = self.globe.camera().copy()
        t = self._player.current_time
        cmd = AddKeyframeCommand(self._active_clip_timeline, cam, t)
        self._command_stack.push(cmd)
        self.timeline.ruler.update()
        self._sync_keyframes()
        self._status_label.setText(f"Keyframe added at {t:.1f}s")

    def _add_keyframe_at(self, t: float):
        if self._current_clip is None:
            return
        cam = self.globe.camera().copy()
        cmd = AddKeyframeCommand(self._active_clip_timeline, cam, t)
        self._command_stack.push(cmd)
        self.timeline.ruler.update()
        self._sync_keyframes()
        self._status_label.setText(f"Keyframe added at {t:.1f}s")

    def _sync_keyframes(self):
        if self._current_clip is not None:
            clip_tl = self._current_clip.timeline
            clip_tl.keyframes.clear()
            for kf in self._active_clip_timeline.keyframes:
                clip_tl.add_keyframe(kf.camera.copy(), kf.time, kf.easing, kf.label)

    def _undo(self):
        self._command_stack.undo()
        self.timeline.ruler.update()
        self._sync_keyframes()

    def _redo(self):
        self._command_stack.redo()
        self.timeline.ruler.update()
        self._sync_keyframes()

    # ── Left sidebar tree ──────────────────────────────────────────────────

    def _populate_tree(self):
        self.tree_widget.clear()
        for sec_id, section in self._project.sections.items():
            sec_item = QTreeWidgetItem([section.name])
            sec_item.setData(0, Qt.UserRole, ("section", sec_id))
            sec_item.setToolTip(0, f"Section: {section.name}")
            self.tree_widget.addTopLevelItem(sec_item)
            for seg_id in section.segment_refs:
                seg = self._project.segments.get(seg_id)
                if seg is None:
                    continue
                seg_item = QTreeWidgetItem([seg.name])
                seg_item.setData(0, Qt.UserRole, ("segment", seg_id))
                seg_item.setToolTip(0, f"Segment: {seg.name}")
                sec_item.addChild(seg_item)
                for ref in seg.clip_refs:
                    clip = ref.get("clip")
                    if clip is None:
                        continue
                    clip_item = QTreeWidgetItem([clip.name])
                    clip_item.setData(0, Qt.UserRole, ("clip", clip.id))
                    clip_item.setToolTip(0, f"Clip: {clip.name}")
                    seg_item.addChild(clip_item)
        self.tree_widget.expandAll()

    def _show_tree_context_menu(self, pos):
        item = self.tree_widget.itemAt(pos)
        m = QMenu(self)
        m.setStyleSheet(
            "QMenu{background:#1f1f1f;color:#ddd;border:1px solid #444}"
            "QMenu::item:selected{background:#4c1d95;color:#fff}"
            "QMenu::item{padding:4px 20px}"
        )
        if item is None:
            m.addAction("Add Section").triggered.connect(self._add_section)
        else:
            data = item.data(0, Qt.UserRole)
            if data is None:
                m.addAction("Add Section").triggered.connect(self._add_section)
            else:
                kind, _ = data
                if kind == "section":
                    m.addAction("Add Segment").triggered.connect(
                        lambda: self._add_segment(item))
                    m.addAction("Rename Section").triggered.connect(
                        lambda: self._rename_item(item))
                    m.addAction("Delete Section").triggered.connect(
                        lambda: self._delete_section(item))
                elif kind == "segment":
                    m.addAction("Add Clip").triggered.connect(
                        lambda: self._add_clip(item))
                    m.addAction("Rename Segment").triggered.connect(
                        lambda: self._rename_item(item))
                    m.addAction("Delete Segment").triggered.connect(
                        lambda: self._delete_segment(item))
                elif kind == "clip":
                    m.addAction("Rename Clip").triggered.connect(
                        lambda: self._rename_item(item))
                    m.addAction("Delete Clip").triggered.connect(
                        lambda: self._delete_clip(item))
        m.exec(self.tree_widget.viewport().mapToGlobal(pos))

    def _add_section(self):
        sec = Section(name=f"Section {len(self._project.sections) + 1}")
        self._project.sections[sec.id] = sec
        if self._project.active_section_id is None:
            self._project.active_section_id = sec.id
        self._populate_tree()

    def _add_segment(self, section_item):
        data = section_item.data(0, Qt.UserRole)
        if data is None:
            return
        _, sec_id = data
        section = self._project.sections.get(sec_id)
        if section is None:
            return
        seg = Segment(name=f"Segment {len(section.segment_refs) + 1}")
        self._project.segments[seg.id] = seg
        section.segment_refs.append(seg.id)
        self._populate_tree()

    def _add_clip(self, segment_item):
        data = segment_item.data(0, Qt.UserRole)
        if data is None:
            return
        _, seg_id = data
        seg = self._project.segments.get(seg_id)
        if seg is None:
            return
        clip = Clip(name=f"Clip {len(seg.clip_refs) + 1}")
        self._project.clips[clip.id] = clip
        seg.clip_refs.append({"clip": clip, "transition": "cut", "duration": 0.5})
        self._populate_tree()

    def _rename_item(self, item):
        from PySide6.QtWidgets import QInputDialog
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        kind, id_ = data
        old = item.text(0)
        new, ok = QInputDialog.getText(self, "Rename", "New name:", text=old)
        if ok and new.strip():
            if kind == "section":
                sec = self._project.sections.get(id_)
                if sec:
                    sec.name = new.strip()
            elif kind == "segment":
                seg = self._project.segments.get(id_)
                if seg:
                    seg.name = new.strip()
            elif kind == "clip":
                clip = self._project.clips.get(id_)
                if clip:
                    clip.name = new.strip()
            self._populate_tree()

    def _delete_section(self, item):
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        _, sec_id = data
        if sec_id in self._project.sections:
            del self._project.sections[sec_id]
        self._populate_tree()

    def _delete_segment(self, item):
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        _, seg_id = data
        if seg_id in self._project.segments:
            del self._project.segments[seg_id]
        for sec in self._project.sections.values():
            if seg_id in sec.segment_refs:
                sec.segment_refs.remove(seg_id)
        self._populate_tree()

    def _delete_clip(self, item):
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        _, clip_id = data
        if clip_id in self._project.clips:
            del self._project.clips[clip_id]
        for seg in self._project.segments.values():
            seg.clip_refs = [r for r in seg.clip_refs
                             if r.get("clip") is None or r["clip"].id != clip_id]
        self._populate_tree()

    def _on_tree_selected(self, item, column):
        data = item.data(0, Qt.UserRole)
        if data is None:
            return
        kind, id_ = data
        if kind == "clip":
            clip = self._project.clips.get(id_)
            if clip is not None:
                self._switch_to_clip(clip)

    def _switch_to_clip(self, clip: Clip):
        self._current_clip = clip
        self._active_clip_timeline.keyframes.clear()
        for kf in clip.timeline.keyframes:
            self._active_clip_timeline.add_keyframe(
                kf.camera.copy(), kf.time, kf.easing, kf.label)
        self._player.timeline = self._active_clip_timeline
        self._player.seek(0.0)
        self.timeline.set_timeline(self._active_clip_timeline)
        self._command_stack.clear()
        if self._active_clip_timeline.keyframes:
            state = self._active_clip_timeline.keyframes[0].camera
            self.globe.set_camera(state, animate=False)

    # ── File watchers ──────────────────────────────────────────────────────

    def _setup_file_watchers(self):
        temp_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
        self._temp_broadcast_dir = temp_dir

        self._sat_watcher = QFileSystemWatcher(self)
        sat_path = os.path.join(temp_dir, "broadcast_satellite.png")
        if os.path.isfile(sat_path):
            self._sat_watcher.addPath(sat_path)
        self._sat_watcher.fileChanged.connect(self._on_satellite_updated)

        self._cloud_watcher = QFileSystemWatcher(self)
        cloud_path = os.path.join(temp_dir, "broadcast_clouds.png")
        if os.path.isfile(cloud_path):
            self._cloud_watcher.addPath(cloud_path)
        self._cloud_watcher.fileChanged.connect(self._on_cloud_updated)

        self._fcast_watcher = QFileSystemWatcher(self)
        fcast_path = os.path.join(temp_dir, "broadcast_forecast.png")
        if os.path.isfile(fcast_path):
            self._fcast_watcher.addPath(fcast_path)
        self._fcast_watcher.fileChanged.connect(self._on_forecast_updated)

        self._wind_u_watcher = QFileSystemWatcher(self)
        wu_path = os.path.join(temp_dir, "broadcast_wind_u.png")
        if os.path.isfile(wu_path):
            self._wind_u_watcher.addPath(wu_path)
        self._wind_u_watcher.fileChanged.connect(self._on_wind_updated)

        self._wind_v_watcher = QFileSystemWatcher(self)
        wv_path = os.path.join(temp_dir, "broadcast_wind_v.png")
        if os.path.isfile(wv_path):
            self._wind_v_watcher.addPath(wv_path)
        self._wind_v_watcher.fileChanged.connect(self._on_wind_updated)

        # Load initial textures once the globe is ready
        if self.globe._ready:
            self._load_initial_broadcast_textures()
        else:
            self.globe.ready.connect(self._load_initial_broadcast_textures)

    def _load_initial_broadcast_textures(self):
        d = self._temp_broadcast_dir
        for name, method in [("broadcast_satellite.png", "set_texture_image"),
                              ("broadcast_clouds.png", "set_cloud_texture"),
                              ("broadcast_forecast.png", "set_forecast_texture")]:
            path = os.path.join(d, name)
            if os.path.exists(path):
                img = QImage(path)
                if not img.isNull():
                    getattr(self.globe, method)(img)
        self._load_wind_data()

    def _on_satellite_updated(self, path):
        if os.path.exists(path):
            img = QImage(path)
            if not img.isNull():
                self.globe.set_texture_image(img)

    def _on_cloud_updated(self, path):
        if os.path.exists(path):
            img = QImage(path)
            if not img.isNull():
                self.globe.set_cloud_texture(img)

    def _on_forecast_updated(self, path):
        if os.path.exists(path):
            img = QImage(path)
            if not img.isNull():
                self.globe.set_forecast_texture(img)

    def _load_wind_data(self):
        import numpy as np
        d = self._temp_broadcast_dir
        u_path = os.path.join(d, "broadcast_wind_u.png")
        v_path = os.path.join(d, "broadcast_wind_v.png")
        if not (os.path.exists(u_path) and os.path.exists(v_path)):
            return
        u_img = QImage(u_path)
        v_img = QImage(v_path)
        if u_img.isNull() or v_img.isNull():
            return
        u_img = u_img.convertToFormat(QImage.Format_Grayscale8)
        v_img = v_img.convertToFormat(QImage.Format_Grayscale8)
        u_arr = np.frombuffer(u_img.bits().tobytes(), dtype=np.uint8).reshape(
            u_img.height(), u_img.width()).astype(np.float32)
        v_arr = np.frombuffer(v_img.bits().tobytes(), dtype=np.uint8).reshape(
            v_img.height(), v_img.width()).astype(np.float32)
        u_arr = u_arr * 100.0 / 255.0 - 50.0
        v_arr = v_arr * 100.0 / 255.0 - 50.0
        self.globe.set_wind_data(u_arr, v_arr)

    def _on_wind_updated(self, path):
        self._load_wind_data()

    # ── Project save / load ────────────────────────────────────────────────

    def _save_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Project", "broadcast.json",
                                               "JSON (*.json)")
        if not path:
            return
        data = {
            "sections": {sid: {"name": s.name, "segment_refs": s.segment_refs}
                         for sid, s in self._project.sections.items()},
            "segments": {sid: {"name": s.name,
                               "clip_refs": [{"clip_id": r["clip"].id,
                                               "transition": r.get("transition", "cut"),
                                               "duration": r.get("duration", 0.5)}
                                              for r in s.clip_refs]}
                         for sid, s in self._project.segments.items()},
            "clips": {cid: {"name": c.name, "duration": c.duration,
                             "keyframes": [{"time": kf.time,
                                            "lat": kf.camera.lat,
                                            "lon": kf.camera.lon,
                                            "zoom": kf.camera.zoom,
                                            "heading": kf.camera.heading,
                                            "pitch": kf.camera.pitch,
                                            "easing": kf.easing,
                                            "label": kf.label}
                                           for kf in c.timeline.keyframes]}
                      for cid, c in self._project.clips.items()},
            "active_section_id": self._project.active_section_id,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self._status_label.setText(f"Project saved to {os.path.basename(path)}")

    def _open_project(self):
        from ..project_serializer import load_project
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "",
                                               "JSON (*.json)")
        if not path:
            return
        try:
            project = load_project(path)
            if project is None:
                QMessageBox.warning(self, "Error", "Failed to load project.")
                return
            self._project = project
            self._populate_tree()
            self._status_label.setText(f"Loaded {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load project: {e}")

    # ── Export with segment support ────────────────────────────────────────

    def _export_mp4(self):
        if self._current_clip is None:
            QMessageBox.warning(self, "Export", "Select a Clip first.")
            return
        dur = self._active_clip_timeline.actual_duration
        if dur < 0.5:
            QMessageBox.warning(self, "Export", "Add keyframes first before exporting.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Broadcast", "broadcast.mp4",
                                               "MP4 Video (*.mp4)")
        if not path:
            return
        prog = QProgressDialog("Exporting...", "Cancel", 0, 100, self)
        prog.setWindowTitle("Export Broadcast")
        prog.setWindowModality(Qt.WindowModal)
        prog.show()

        def _render_cb(state: CameraState):
            self._on_animation_frame(state)
            return self.globe.grab_frame_rgb(1920, 1080)

        def _progress(pct: int):
            prog.setValue(pct)

        success = self._player.export_mp4(
            output_path=path,
            render_func=_render_cb,
            fps=30,
            width=1920,
            height=1080,
            progress_callback=_progress,
        )

        prog.close()
        if success:
            QMessageBox.information(self, "Export Complete", f"Saved to:\n{path}")
        else:
            QMessageBox.warning(self, "Export Failed",
                                "Export failed. Make sure ffmpeg is installed and accessible in PATH.")

    def closeEvent(self, event):
        self._player.stop()
        self.globe.cleanup()
        if self._main_ui and hasattr(self._main_ui, '_broadcast_window'):
            self._main_ui._broadcast_window = None
        event.accept()
