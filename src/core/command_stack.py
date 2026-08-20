from typing import List, Optional, Callable
from src.core.keyframe_engine import KeyframeTimeline


class Command:
    def execute(self):
        pass

    def undo(self):
        pass


class AddKeyframeCommand(Command):
    def __init__(self, timeline: KeyframeTimeline, camera, time: float,
                 easing: str = "ease_in_out_cubic", label: str = ""):
        self.timeline = timeline
        self.camera = camera
        self.time = time
        self.easing = easing
        self.label = label
        self._added_index = -1

    def execute(self):
        self.timeline.add_keyframe(self.camera, self.time, self.easing, self.label)
        for i, kf in enumerate(self.timeline.keyframes):
            if kf.camera == self.camera and abs(kf.time - self.time) < 1e-6:
                self._added_index = i
                break

    def undo(self):
        if self._added_index >= 0:
            self.timeline.remove_keyframe(self._added_index)


class DeleteKeyframeCommand(Command):
    def __init__(self, timeline: KeyframeTimeline, index: int):
        self.timeline = timeline
        self.index = index
        self._deleted_kf = None

    def execute(self):
        if 0 <= self.index < len(self.timeline.keyframes):
            self._deleted_kf = self.timeline.keyframes[self.index]
            self.timeline.remove_keyframe(self.index)

    def undo(self):
        if self._deleted_kf is not None:
            self.timeline.keyframes.insert(self.index, self._deleted_kf)
            self.timeline.keyframes.sort(key=lambda kf: kf.time)


class MoveKeyframeCommand(Command):
    def __init__(self, timeline: KeyframeTimeline, index: int, new_time: float):
        self.timeline = timeline
        self.index = index
        self.new_time = new_time
        self.old_time = timeline.keyframes[index].time if index < len(timeline.keyframes) else 0.0

    def execute(self):
        if 0 <= self.index < len(self.timeline.keyframes):
            self.timeline.keyframes[self.index].time = self.new_time
            self.timeline.keyframes.sort(key=lambda kf: kf.time)

    def undo(self):
        if 0 <= self.index < len(self.timeline.keyframes):
            self.timeline.keyframes[self.index].time = self.old_time
            self.timeline.keyframes.sort(key=lambda kf: kf.time)


class CommandStack:
    def __init__(self):
        self.undo_stack: List[Command] = []
        self.redo_stack: List[Command] = []
        self.changed_callback: Optional[Callable] = None

    def push(self, cmd: Command):
        cmd.execute()
        self.undo_stack.append(cmd)
        self.redo_stack.clear()
        if self.changed_callback:
            self.changed_callback()

    def undo(self):
        if not self.undo_stack:
            return
        cmd = self.undo_stack.pop()
        cmd.undo()
        self.redo_stack.append(cmd)
        if self.changed_callback:
            self.changed_callback()

    def redo(self):
        if not self.redo_stack:
            return
        cmd = self.redo_stack.pop()
        cmd.execute()
        self.undo_stack.append(cmd)
        if self.changed_callback:
            self.changed_callback()

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
