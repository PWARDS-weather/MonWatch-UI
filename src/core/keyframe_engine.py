from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, Callable
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from .easing import Easing
from .animation_camera import CameraState
import subprocess


@dataclass
class Keyframe:
    time: float
    camera: CameraState
    easing: str = "ease_in_out_cubic"
    label: str = ""


class KeyframeTimeline:
    def __init__(self, keyframes: list[Keyframe] = None, duration: float = 30.0, loop: bool = False):
        self.keyframes: list[Keyframe] = sorted(keyframes or [], key=lambda kf: kf.time)
        self.duration: float = duration
        self.loop: bool = loop

    @property
    def actual_duration(self) -> float:
        if self.keyframes:
            return max(kf.time for kf in self.keyframes)
        return self.duration

    def add_keyframe(self, camera: CameraState, time: Optional[float] = None, easing: str = "ease_in_out_cubic", label: str = ""):
        if time is None:
            time = self.actual_duration + 2.0
        self.keyframes.append(Keyframe(time=time, camera=camera.copy(), easing=easing, label=label))
        self.keyframes.sort(key=lambda kf: kf.time)

    def remove_keyframe(self, index: int):
        if 0 <= index < len(self.keyframes):
            self.keyframes.pop(index)

    def move_keyframe(self, index: int, new_time: float):
        if 0 <= index < len(self.keyframes):
            self.keyframes[index].time = max(0.0, new_time)
            self.keyframes.sort(key=lambda kf: kf.time)

    def update_keyframe(self, index: int, camera: CameraState = None, easing: str = None, label: str = None):
        if 0 <= index < len(self.keyframes):
            kf = self.keyframes[index]
            if camera is not None:
                kf.camera = camera.copy()
            if easing is not None:
                kf.easing = easing
            if label is not None:
                kf.label = label

    def evaluate(self, t: float) -> CameraState:
        t = max(0.0, t)
        n = len(self.keyframes)
        if n == 0:
            return CameraState()
        if n == 1:
            return self.keyframes[0].camera.copy()
        if t <= self.keyframes[0].time:
            return self.keyframes[0].camera.copy()
        if t >= self.keyframes[-1].time:
            if self.loop:
                t = t % self.actual_duration
            else:
                return self.keyframes[-1].camera.copy()
        for i in range(n - 1):
            kf_a = self.keyframes[i]
            kf_b = self.keyframes[i + 1]
            if kf_a.time <= t < kf_b.time:
                span = kf_b.time - kf_a.time
                if span < 1e-8:
                    return kf_b.camera.copy()
                local_t = (t - kf_a.time) / span
                eased_t = Easing.apply(kf_b.easing, local_t)
                return kf_a.camera.interpolate(kf_b.camera, eased_t)
        return self.keyframes[-1].camera.copy()

    def get_keyframe_at(self, t: float) -> Optional[int]:
        for i, kf in enumerate(self.keyframes):
            if abs(kf.time - t) < 0.05:
                return i
        return None


class AnimationPlayer(QObject):
    frameTick = Signal(CameraState)
    positionChanged = Signal(float)
    finished = Signal()

    def __init__(self, timeline: KeyframeTimeline, parent=None):
        super().__init__(parent)
        self._timeline = timeline
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._current_time: float = 0.0
        self._speed: float = 1.0
        self._playing: bool = False

    @property
    def timeline(self) -> KeyframeTimeline:
        return self._timeline

    @timeline.setter
    def timeline(self, tl: KeyframeTimeline):
        self._timeline = tl
        self._current_time = 0.0

    @property
    def current_time(self) -> float:
        return self._current_time

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, s: float):
        self._speed = max(0.1, min(10.0, s))

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(self):
        if self._timeline.actual_duration < 0.01:
            return
        self._playing = True
        self._timer.start(16)

    def pause(self):
        self._timer.stop()
        self._playing = False

    def stop(self):
        self._timer.stop()
        self._playing = False
        self._current_time = 0.0
        self.frameTick.emit(self._timeline.evaluate(0))
        self.positionChanged.emit(0.0)

    def seek(self, t: float):
        self._current_time = max(0.0, min(t, self._timeline.actual_duration))
        state = self._timeline.evaluate(self._current_time)
        self.frameTick.emit(state)
        self.positionChanged.emit(self._current_time)

    def _tick(self):
        dt = 0.016 * self._speed
        self._current_time += dt
        dur = self._timeline.actual_duration
        if self._current_time >= dur:
            if self._timeline.loop and dur > 0:
                self._current_time = 0.0
                self.positionChanged.emit(0.0)
            else:
                self._current_time = dur
                self.stop()
                self.finished.emit()
                return
        state = self._timeline.evaluate(self._current_time)
        self.frameTick.emit(state)
        self.positionChanged.emit(self._current_time)

    def export_mp4(self, output_path: str, render_func: Callable[[CameraState], Optional[bytes]],
                   fps: int = 30, width: int = 1920, height: int = 1080,
                   progress_callback: Callable[[int], None] = None) -> bool:
        dur = self._timeline.actual_duration
        if dur < 0.01:
            return False
        total_frames = int(dur * fps)
        if total_frames < 1:
            return False
        try:
            proc = subprocess.Popen(
                ["ffmpeg", "-y",
                 "-f", "rawvideo",
                 "-pix_fmt", "rgb24",
                 "-s", f"{width}x{height}",
                 "-r", str(fps),
                 "-i", "pipe:0",
                 "-c:v", "libx264",
                 "-pix_fmt", "yuv420p",
                 "-crf", "18",
                 "-preset", "medium",
                 output_path],
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return False
        frame_size = width * height * 3
        last_report = 0
        for idx in range(total_frames):
            t = idx / fps
            state = self._timeline.evaluate(t)
            frame_data = render_func(state)
            if frame_data is None:
                blank = b"\x00" * frame_size
                proc.stdin.write(blank)
            else:
                proc.stdin.write(frame_data)
            pct = int((idx + 1) / total_frames * 100)
            if pct > last_report and progress_callback:
                progress_callback(pct)
                last_report = pct
        proc.stdin.close()
        proc.wait()
        return proc.returncode == 0
