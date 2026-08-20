import json
import os
from typing import Optional
from src.data.models import BroadcastProject, Section, Segment, Clip
from src.core.keyframe_engine import KeyframeTimeline, Keyframe
from src.core.animation_camera import CameraState


def save_project(project: BroadcastProject, path: str):
    data = {
        "sections": {
            sid: {"name": s.name, "segment_refs": s.segment_refs}
            for sid, s in project.sections.items()
        },
        "segments": {
            sid: {
                "name": s.name,
                "clip_refs": [
                    {
                        "clip_id": r["clip"].id,
                        "transition": r.get("transition", "cut"),
                        "duration": r.get("duration", 0.5),
                    }
                    for r in s.clip_refs
                ],
            }
            for sid, s in project.segments.items()
        },
        "clips": {
            cid: {
                "name": c.name,
                "duration": c.duration,
                "keyframes": [
                    {
                        "time": kf.time,
                        "lat": kf.camera.lat,
                        "lon": kf.camera.lon,
                        "zoom": kf.camera.zoom,
                        "heading": kf.camera.heading,
                        "pitch": kf.camera.pitch,
                        "easing": kf.easing,
                        "label": kf.label,
                    }
                    for kf in c.timeline.keyframes
                ],
            }
            for cid, c in project.clips.items()
        },
        "active_section_id": project.active_section_id,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_project(path: str) -> Optional[BroadcastProject]:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)

    project = BroadcastProject()

    # Load clips
    for cid, cdata in data.get("clips", {}).items():
        tl = KeyframeTimeline(keyframes=[], duration=cdata.get("duration", 5.0), loop=False)
        for kfd in cdata.get("keyframes", []):
            cam = CameraState(
                lat=kfd.get("lat", 0.0),
                lon=kfd.get("lon", 0.0),
                zoom=kfd.get("zoom", 1.0),
                heading=kfd.get("heading", 0.0),
                pitch=kfd.get("pitch", 15.0),
            )
            tl.add_keyframe(
                camera=cam,
                time=kfd.get("time", 0.0),
                easing=kfd.get("easing", "ease_in_out_cubic"),
                label=kfd.get("label", ""),
            )
        clip = Clip(id=cid, name=cdata.get("name", "Clip"),
                    duration=cdata.get("duration", 5.0), timeline=tl)
        project.clips[cid] = clip

    # Load segments
    for sid, sdata in data.get("segments", {}).items():
        seg = Segment(id=sid, name=sdata.get("name", "Segment"))
        for r in sdata.get("clip_refs", []):
            clip = project.clips.get(r["clip_id"])
            if clip:
                seg.clip_refs.append({
                    "clip": clip,
                    "transition": r.get("transition", "cut"),
                    "duration": r.get("duration", 0.5),
                })
        project.segments[sid] = seg

    # Load sections
    for sid, secdata in data.get("sections", {}).items():
        sec = Section(id=sid, name=secdata.get("name", "Section"),
                      segment_refs=secdata.get("segment_refs", []))
        project.sections[sid] = sec

    project.active_section_id = data.get("active_section_id")
    return project
