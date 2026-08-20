import math
from .shared_models import PlacedLabel


def rescue(placed_labels, fig_w_px=1200, fig_h_px=800, margin=20):
    if not placed_labels:
        return []

    used_bbs = []
    result = []

    for pl in placed_labels:
        bb = (pl.bbox_left, pl.bbox_top, pl.bbox_right, pl.bbox_bottom)
        w = pl.bbox_right - pl.bbox_left
        h = pl.bbox_bottom - pl.bbox_top

        if not bb or (bb[2] - bb[0] < 1 and bb[3] - bb[1] < 1):
            ox, oy, ha = pl.ox, pl.oy, pl.ha
            bb = _compute_bb(pl.lon, pl.lat, ox, oy, ha, w, h,
                            fig_w_px, fig_h_px, margin)
            result.append(PlacedLabel(
                id=pl.id, lon=pl.lon, lat=pl.lat,
                ox=ox, oy=oy, ha=ha,
                bbox_left=bb[0], bbox_top=bb[1],
                bbox_right=bb[2], bbox_bottom=bb[3],
                text=pl.text, priority=pl.priority
            ))
            used_bbs.append(bb)
            continue

        ox, oy, ha = pl.ox, pl.oy, pl.ha
        adjusted = False

        for other_bb in used_bbs:
            if _overlap(bb, other_bb, 3):
                direction = _push_away(bb, other_bb)
                ox += direction[0] * 25
                oy += direction[1] * 25
                bb = _compute_bb(pl.lon, pl.lat, ox, oy, ha, w, h,
                                fig_w_px, fig_h_px, margin)
                adjusted = True
                break

        if adjusted:
            for _ in range(5):
                still_overlap = False
                for other_bb in used_bbs:
                    if _overlap(bb, other_bb, 3):
                        direction = _push_away(bb, other_bb)
                        ox += direction[0] * 15
                        oy += direction[1] * 15
                        bb = _compute_bb(pl.lon, pl.lat, ox, oy, ha, w, h,
                                        fig_w_px, fig_h_px, margin)
                        still_overlap = True
                        break
                if not still_overlap:
                    break

        ha = "right" if ox < 0 else "left"
        result.append(PlacedLabel(
            id=pl.id, lon=pl.lon, lat=pl.lat,
            ox=ox, oy=oy, ha=ha,
            bbox_left=bb[0], bbox_top=bb[1],
            bbox_right=bb[2], bbox_bottom=bb[3],
            text=pl.text, priority=pl.priority
        ))
        used_bbs.append(bb)

    return result


def _compute_bb(lon, lat, ox, oy, ha, w, h, fw, fh, margin):
    cx = fw / 2
    cy = fh / 2
    fx = cx + ox
    fy = cy + oy
    if ha == "right":
        return (fx - w, fy - h * 0.5, fx, fy + h * 0.5)
    return (fx, fy - h * 0.5, fx + w, fy + h * 0.5)


def _overlap(bb1, bb2, pad=4):
    return (bb1[0] + pad < bb2[2] and bb1[2] - pad > bb2[0] and
            bb1[1] + pad < bb2[3] and bb1[3] - pad > bb2[1])


def _push_away(bb, other):
    cx = (bb[0] + bb[2]) * 0.5
    cy = (bb[1] + bb[3]) * 0.5
    ox = (other[0] + other[2]) * 0.5
    oy = (other[1] + other[3]) * 0.5
    dx = cx - ox
    dy = cy - oy
    d = math.hypot(dx, dy)
    if d < 1:
        return (1.0, 0.0)
    return (dx / d, dy / d)
