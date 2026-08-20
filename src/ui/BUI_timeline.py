from __future__ import annotations
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QBrush, QMouseEvent, QFontMetrics
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QSlider, QComboBox, QCheckBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QGroupBox, QFormLayout, QScrollArea,
)
from typing import Optional
from ..core.easing import Easing
from ..core.animation_camera import CameraState
from ..core.keyframe_engine import KeyframeTimeline, Keyframe, AnimationPlayer


class TimelineRuler(QWidget):
    seekRequested = Signal(float)
    keyframeDragged = Signal(int, float)
    keyframeSelected = Signal(int)
    addKeyframeAtRequested = Signal(float)

    _KEYFAME_COLOR = QColor("#f472b6")
    _KEYFAME_SEL_COLOR = QColor("#ff8a65")
    _PLAYHEAD_COLOR = QColor("#ff1744")
    _RULER_COLOR = QColor("#555")
    _RULER_TEXT_COLOR = QColor("#aaa")
    _BG_COLOR = QColor("#1a1a1a")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timeline: Optional[KeyframeTimeline] = None
        self._current_time: float = 0.0
        self._px_per_sec: float = 80.0
        self._selected_index: int = -1
        self._dragging_kf: bool = False
        self._drag_kf_index: int = -1
        self._dragging_playhead: bool = False
        self.setMinimumHeight(100)
        self.setMouseTracking(True)

    def set_timeline(self, tl: Optional[KeyframeTimeline]):
        self._timeline = tl
        self._selected_index = -1
        self.update()

    def set_current_time(self, t: float):
        self._current_time = t
        self.update()

    def set_px_per_sec(self, pps: float):
        self._px_per_sec = max(20.0, min(800.0, pps))
        self.update()

    @property
    def duration(self) -> float:
        if self._timeline:
            return max(self._timeline.actual_duration, 10.0)
        return 10.0

    def _time_to_x(self, t: float) -> float:
        return t * self._px_per_sec

    def _x_to_time(self, x: float) -> float:
        return x / self._px_per_sec

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        dur = self.duration
        total_w = dur * self._px_per_sec

        p.fillRect(self.rect(), self._BG_COLOR)

        KH = 50
        PAD = 6

        p.setPen(QPen(self._RULER_COLOR, 1))
        step = 1.0
        if self._px_per_sec < 40:
            step = 5.0
        elif self._px_per_sec < 20:
            step = 10.0
        sec = 0
        while sec <= dur + 1:
            x = self._time_to_x(sec)
            if x > w + 20:
                break
            p.setPen(QPen(self._RULER_COLOR, 1))
            tick_h = 10 if sec % max(1, int(step * 5)) == 0 else 5
            p.drawLine(int(x), int(h - KH - tick_h), int(x), int(h - KH))
            p.setPen(self._RULER_TEXT_COLOR)
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(int(x) + 4, int(h - KH - 2), f"{sec}s")
            sec += step

        sep = QColor("#333")
        p.setPen(QPen(sep, 1))
        p.drawLine(0, int(h - KH), w, int(h - KH))

        if self._timeline:
            for i, kf in enumerate(self._timeline.keyframes):
                x = self._time_to_x(kf.time)
                is_sel = (i == self._selected_index)
                color = self._KEYFAME_SEL_COLOR if is_sel else self._KEYFAME_COLOR
                p.setBrush(QBrush(color))
                p.setPen(QPen(Qt.white, 2 if is_sel else 1))
                dia_size = 8 if is_sel else 6
                pts = [
                    QPointF(x, h - 16),
                    QPointF(x - dia_size, h - 24),
                    QPointF(x, h - 32),
                    QPointF(x + dia_size, h - 24),
                ]
                p.drawPolygon(pts)
                p.setPen(self._RULER_TEXT_COLOR)
                p.setFont(QFont("Segoe UI", 8))
                label = kf.label or f"K{i}"
                p.drawText(int(x) + dia_size + 4, int(h - 20), label)
                if i > 0:
                    prev_kf = self._timeline.keyframes[i - 1]
                    px = self._time_to_x(prev_kf.time)
                    p.setPen(QPen(QColor("#555"), 1, Qt.DashLine))
                    p.drawLine(int(px), int(h - 24), int(x), int(h - 24))

        px = self._time_to_x(self._current_time)
        p.setPen(QPen(self._PLAYHEAD_COLOR, 2))
        p.drawLine(int(px), 0, int(px), int(h - KH + 10))

        p.setBrush(QBrush(self._PLAYHEAD_COLOR))
        p.setPen(Qt.NoPen)
        tri_h = 8
        tri = [
            QPointF(px, 0),
            QPointF(px - tri_h, tri_h + 4),
            QPointF(px + tri_h, tri_h + 4),
        ]
        p.drawPolygon(tri)

        time_label = f"{self._current_time:.1f}s"
        fm = QFontMetrics(QFont("Segoe UI", 10, QFont.Bold))
        tw = fm.horizontalAdvance(time_label) + 12
        th = fm.height() + 6
        label_x = max(2, min(int(px) - tw // 2, w - tw - 2))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0x20, 0x20, 0x30, 220))
        p.drawRoundedRect(label_x, tri_h + 6, tw, th, 4, 4)
        p.setPen(self._PLAYHEAD_COLOR)
        p.setFont(QFont("Segoe UI", 10, QFont.Bold))
        p.drawText(label_x + 6, tri_h + 6 + fm.ascent() + 3, time_label)

        p.end()

    def mousePressEvent(self, event):
        x = event.position().x()
        t = self._x_to_time(x)
        if self._timeline:
            for i, kf in enumerate(self._timeline.keyframes):
                kx = self._time_to_x(kf.time)
                if abs(x - kx) < 10:
                    self._selected_index = i
                    self._dragging_kf = True
                    self._drag_kf_index = i
                    self.keyframeSelected.emit(i)
                    self.update()
                    return
        self._selected_index = -1
        self._dragging_playhead = True
        self.seekRequested.emit(max(0, t))
        self.update()

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self._dragging_kf and self._drag_kf_index >= 0 and self._timeline:
            t = max(0, self._x_to_time(x))
            if self._drag_kf_index < len(self._timeline.keyframes):
                self._timeline.move_keyframe(self._drag_kf_index, t)
                self.keyframeDragged.emit(self._drag_kf_index, t)
                self.update()
        elif self._dragging_playhead:
            t = max(0, self._x_to_time(x))
            self._current_time = t
            self.seekRequested.emit(t)
            self.update()

    def mouseReleaseEvent(self, event):
        self._dragging_kf = False
        self._dragging_playhead = False

    def contextMenuEvent(self, event):
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.setStyleSheet("QMenu{background:#1f1f1f;color:#ddd;border:1px solid #444}"
                        "QMenu::item:selected{background:#4c1d95;color:#fff}"
                        "QMenu::item{padding:4px 20px}")
        act_add = m.addAction("Add Keyframe Here")
        action = m.exec(event.globalPos())
        if action == act_add:
            t = self._x_to_time(event.pos().x())
            self.seekRequested.emit(max(0, t))
            self.addKeyframeAtRequested.emit(max(0, t))


class TimelinePlaybackBar(QWidget):
    playClicked = Signal()
    pauseClicked = Signal()
    stopClicked = Signal()
    speedChanged = Signal(float)
    loopToggled = Signal(bool)
    addKeyframeClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(36)
        layout = QHBoxLayout(self)
        layout.setSpacing(4)
        layout.setContentsMargins(4, 2, 4, 2)

        self.play_btn = QPushButton("\u25B6")
        self.play_btn.setFixedSize(28, 28)
        self.play_btn.setToolTip("Play")
        self.pause_btn = QPushButton("\u23F8")
        self.pause_btn.setFixedSize(28, 28)
        self.pause_btn.setToolTip("Pause")
        self.stop_btn = QPushButton("\u23F9")
        self.stop_btn.setFixedSize(28, 28)
        self.stop_btn.setToolTip("Stop")
        btn_style = "QPushButton{background:#2D2D2D;color:#EEE;border:1px solid #555;border-radius:4px;font-size:14px;}"
        for b in (self.play_btn, self.pause_btn, self.stop_btn):
            b.setStyleSheet(btn_style)

        self.play_btn.clicked.connect(self.playClicked.emit)
        self.pause_btn.clicked.connect(self.pauseClicked.emit)
        self.stop_btn.clicked.connect(self.stopClicked.emit)

        layout.addWidget(self.play_btn)
        layout.addWidget(self.pause_btn)
        layout.addWidget(self.stop_btn)

        layout.addSpacing(8)
        self.add_kf_btn = QPushButton("+ Keyframe")
        self.add_kf_btn.setStyleSheet("QPushButton{background:#2D2D2D;color:#4CAF50;border:1px solid #4CAF50;border-radius:4px;padding:2px 8px;font-size:10px;}")
        self.add_kf_btn.clicked.connect(self.addKeyframeClicked.emit)
        layout.addWidget(self.add_kf_btn)

        layout.addSpacing(8)
        layout.addWidget(QLabel("Speed:"))
        self.speed_combo = QComboBox()
        self.speed_combo.addItems(["0.25x", "0.5x", "1.0x", "2.0x", "4.0x"])
        self.speed_combo.setCurrentText("1.0x")
        self.speed_combo.setStyleSheet("QComboBox{background:#2D2D2D;color:#CCC;border:1px solid #444;padding:2px 4px;font-size:10px;}")
        self.speed_combo.currentTextChanged.connect(lambda t: self.speedChanged.emit(float(t.replace("x", ""))))
        layout.addWidget(self.speed_combo)

        self.loop_cb = QCheckBox("Loop")
        self.loop_cb.setStyleSheet("QCheckBox{color:#BBB;font-size:10px;}")
        self.loop_cb.toggled.connect(self.loopToggled.emit)
        layout.addWidget(self.loop_cb)

        layout.addStretch()

        layout.addWidget(QLabel("Zoom:"))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(20, 200)
        self.zoom_slider.setValue(60)
        self.zoom_slider.setFixedWidth(80)
        self.zoom_slider.setStyleSheet("QSlider::groove:horizontal{height:4px;background:#444;border-radius:2px;}"
                                       "QSlider::handle:horizontal{background:#AAA;width:10px;margin:-3px 0;border-radius:4px;}")
        layout.addWidget(self.zoom_slider)


class TimelinePropertiesPanel(QWidget):
    keyframeUpdated = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._index = -1
        self.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        form = QFormLayout()
        form.setSpacing(2)
        self.lat_spin = QDoubleSpinBox()
        self.lat_spin.setRange(-90, 90)
        self.lat_spin.setDecimals(1)
        self.lon_spin = QDoubleSpinBox()
        self.lon_spin.setRange(-180, 180)
        self.lon_spin.setDecimals(1)
        self.zoom_spin = QDoubleSpinBox()
        self.zoom_spin.setRange(0.2, 10.0)
        self.zoom_spin.setDecimals(1)
        self.pitch_spin = QDoubleSpinBox()
        self.pitch_spin.setRange(-90, 90)
        self.pitch_spin.setDecimals(1)
        self.heading_spin = QDoubleSpinBox()
        self.heading_spin.setRange(-180, 180)
        self.heading_spin.setDecimals(1)
        self.easing_combo = QComboBox()
        self.easing_combo.addItems(Easing.list_names())
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Keyframe label...")
        spin_style = "QDoubleSpinBox{background:#2D2D2D;color:#EEE;border:1px solid #555;padding:1px 3px;font-size:10px;}"
        for s in (self.lat_spin, self.lon_spin, self.zoom_spin, self.pitch_spin, self.heading_spin):
            s.setStyleSheet(spin_style)
        self.easing_combo.setStyleSheet("QComboBox{background:#2D2D2D;color:#CCC;border:1px solid #444;font-size:10px;}")
        self.label_edit.setStyleSheet("QLineEdit{background:#2D2D2D;color:#EEE;border:1px solid #555;padding:1px 3px;font-size:10px;}")

        form.addRow("Lat:", self.lat_spin)
        form.addRow("Lon:", self.lon_spin)
        form.addRow("Zoom:", self.zoom_spin)
        form.addRow("Pitch:", self.pitch_spin)
        form.addRow("Heading:", self.heading_spin)
        form.addRow("Easing:", self.easing_combo)
        form.addRow("Label:", self.label_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self.update_btn = QPushButton("Update")
        self.update_btn.setStyleSheet("QPushButton{background:#4CAF50;color:white;border-radius:3px;padding:2px 8px;font-size:10px;}")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setStyleSheet("QPushButton{background:#d32f2f;color:white;border-radius:3px;padding:2px 8px;font-size:10px;}")
        btn_row.addWidget(self.update_btn)
        btn_row.addWidget(self.delete_btn)
        layout.addLayout(btn_row)

        self.update_btn.clicked.connect(self._emit_update)
        self.delete_btn.clicked.connect(self._emit_delete)

    def _emit_update(self):
        if self._index >= 0:
            self.keyframeUpdated.emit(self._index)

    def _emit_delete(self):
        self.keyframeUpdated.emit(-self._index - 1)

    def show_keyframe(self, index: int, kf: Optional[Keyframe]):
        if kf is None or index < 0:
            self.setVisible(False)
            self._index = -1
            return
        self._index = index
        self.lat_spin.setValue(kf.camera.lat)
        self.lon_spin.setValue(kf.camera.lon)
        self.zoom_spin.setValue(kf.camera.zoom)
        self.pitch_spin.setValue(kf.camera.pitch)
        self.heading_spin.setValue(kf.camera.heading)
        idx = self.easing_combo.findText(kf.easing)
        if idx >= 0:
            self.easing_combo.setCurrentIndex(idx)
        self.label_edit.setText(kf.label)
        self.setVisible(True)

    def get_edited_camera(self) -> CameraState:
        return CameraState(
            lat=self.lat_spin.value(),
            lon=self.lon_spin.value(),
            zoom=self.zoom_spin.value(),
            heading=self.heading_spin.value(),
            pitch=self.pitch_spin.value(),
        )

    def get_edited_easing(self) -> str:
        return self.easing_combo.currentText()

    def get_edited_label(self) -> str:
        return self.label_edit.text()


class TimelinePanel(QWidget):
    seekRequested = Signal(float)
    keyframeSelected = Signal(int)
    addKeyframeRequested = Signal()

    def __init__(self, player: AnimationPlayer, timeline: KeyframeTimeline, parent=None):
        super().__init__(parent)
        self._player = player
        self._timeline = timeline

        layout = QVBoxLayout(self)
        layout.setSpacing(2)
        layout.setContentsMargins(0, 0, 0, 0)

        self.playback_bar = TimelinePlaybackBar()
        layout.addWidget(self.playback_bar)

        self.ruler = TimelineRuler()
        self.ruler.set_timeline(timeline)
        layout.addWidget(self.ruler, 1)

        self.time_label = QLabel("0.0s / 0.0s")
        self.time_label.setStyleSheet("color:#aaa;font-size:10px;padding:2px 6px;background:#222;")
        layout.addWidget(self.time_label)

        self.props = TimelinePropertiesPanel()
        layout.addWidget(self.props)

        self._connect_signals()
        self._update_ui_state()
        dur = self._timeline.actual_duration if self._timeline else 0
        self.time_label.setText(f"0.0s / {dur:.1f}s")

    def _connect_signals(self):
        self.playback_bar.playClicked.connect(self._on_play)
        self.playback_bar.pauseClicked.connect(self._on_pause)
        self.playback_bar.stopClicked.connect(self._on_stop)
        self.playback_bar.speedChanged.connect(lambda s: setattr(self._player, 'speed', s))
        self.playback_bar.loopToggled.connect(lambda b: setattr(self._timeline, 'loop', b))
        self.playback_bar.addKeyframeClicked.connect(self._on_add_keyframe)
        self.playback_bar.zoom_slider.valueChanged.connect(lambda v: self.ruler.set_px_per_sec(v))

        self.ruler.seekRequested.connect(lambda t: self._player.seek(t))
        self.ruler.keyframeSelected.connect(self._on_keyframe_selected)
        self.ruler.seekRequested.connect(lambda t: self._on_seek(t))

        self.props.keyframeUpdated.connect(self._on_props_update)

        self._player.positionChanged.connect(self._on_time_changed)

    def _on_time_changed(self, t: float):
        self.ruler.set_current_time(t)
        dur = self._timeline.actual_duration if self._timeline else 0
        self.time_label.setText(f"{t:.1f}s / {dur:.1f}s")

    def _on_play(self):
        if self._player.is_playing:
            self._player.pause()
        self._player.play()

    def _on_pause(self):
        self._player.pause()

    def _on_stop(self):
        self._player.stop()

    def _on_seek(self, t: float):
        self.ruler.set_current_time(t)

    def _on_add_keyframe(self):
        self.addKeyframeRequested.emit()

    def _on_keyframe_selected(self, index: int):
        kf = self._timeline.keyframes[index] if self._timeline and 0 <= index < len(self._timeline.keyframes) else None
        self.props.show_keyframe(index, kf)
        self.keyframeSelected.emit(index)

    def _on_props_update(self, value: int):
        if value < 0:
            idx = -value - 1
            self._timeline.remove_keyframe(idx)
            self.props.setVisible(False)
            self.ruler.update()
            return
        idx = value
        if 0 <= idx < len(self._timeline.keyframes):
            self._timeline.update_keyframe(
                idx,
                camera=self.props.get_edited_camera(),
                easing=self.props.get_edited_easing(),
                label=self.props.get_edited_label(),
            )
            self.ruler.update()

    def set_timeline(self, new_timeline: KeyframeTimeline):
        self._timeline = new_timeline
        self._player.timeline = new_timeline
        self.ruler.set_timeline(new_timeline)
        self.ruler.update()
        self.props.show_keyframe(-1, None)
        self.props.setVisible(False)
        self._player.seek(0.0)
        dur = self._timeline.actual_duration if self._timeline else 0
        self.time_label.setText(f"0.0s / {dur:.1f}s")

    def _update_ui_state(self):
        self.playback_bar.loop_cb.setChecked(self._timeline.loop)

    def set_camera_state(self, cam: CameraState):
        if hasattr(self, 'ruler'):
            pass
