import numpy as np
from typing import Tuple, Callable, Optional
from src.data.models import Segment, Clip
from src.core.animation_camera import CameraState


def evaluate_segment(
    segment: Segment,
    global_time: float,
    render_func: Callable[[CameraState, dict], Optional[np.ndarray]],
) -> Tuple[Optional[np.ndarray], CameraState]:
    cumulative = 0.0
    for idx, ref in enumerate(segment.clip_refs):
        clip: Clip = ref["clip"]
        clip_duration = clip.duration
        trans_type = ref.get("transition", "cut")
        trans_dur = ref.get("duration", 0.0)
        block_end = cumulative + clip_duration + trans_dur

        if global_time < block_end:
            local_t = global_time - cumulative
            if trans_type in ("crossfade", "camera_match") and local_t > clip_duration:
                alpha = (local_t - clip_duration) / trans_dur if trans_dur > 0 else 1.0
                alpha = max(0.0, min(1.0, alpha))
                state_a = clip.timeline.evaluate(clip_duration)
                img_a = render_func(state_a, clip.layer_overrides)
                if idx + 1 < len(segment.clip_refs):
                    next_ref = segment.clip_refs[idx + 1]
                    next_clip = next_ref["clip"]
                    next_local = local_t - clip_duration
                    state_b = next_clip.timeline.evaluate(next_local)
                    img_b = render_func(state_b, next_clip.layer_overrides)
                    if img_a is not None and img_b is not None and trans_type == "crossfade":
                        blended_img = (img_a * (1.0 - alpha) + img_b * alpha).astype(np.uint8)
                    else:
                        blended_img = img_b if img_b is not None else img_a
                    blended_cam = CameraState.interpolate(state_a, state_b, alpha)
                    return blended_img, blended_cam
                return img_a, state_a

            t = min(local_t, clip_duration)
            state = clip.timeline.evaluate(t)
            img = render_func(state, clip.layer_overrides)
            return img, state

        cumulative += clip_duration + trans_dur

    last_ref = segment.clip_refs[-1]
    last_clip = last_ref["clip"]
    state = last_clip.timeline.evaluate(last_clip.duration)
    return render_func(state, last_clip.layer_overrides), state
