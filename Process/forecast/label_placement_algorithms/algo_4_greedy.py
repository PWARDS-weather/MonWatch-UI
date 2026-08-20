import math
from .shared_models import PlacedLabel


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          base_dist=60, stair_step=14):
    n = len(points)
    if n < 2:
        return [_empty(pt) for pt in points]

    pps = max(pts_p_px, 1e-10)
    lons = [p.lon for p in points]
    lats = [p.lat for p in points]

    dirs = []
    for i in range(n):
        if i == 0:
            dx = lons[1] - lons[0]
            dy = lats[1] - lats[0]
        elif i == n - 1:
            dx = lons[-1] - lons[-2]
            dy = lats[-1] - lats[-2]
        else:
            dx = (lons[i+1] - lons[i-1]) * 0.5
            dy = (lats[i+1] - lats[i-1]) * 0.5
        ln = math.hypot(dx, dy)
        if ln > 1e-10:
            dx /= ln
            dy /= ln
        else:
            dx, dy = 1.0, 0.0
        nx, ny = -dy, dx

        cross_before = 0.0
        cross_after = 0.0
        if i > 0:
            cross_before = dx * (lats[i] - lats[i-1]) - dy * (lons[i] - lons[i-1])
        if i < n - 1:
            cross_after = dx * (lats[i+1] - lats[i]) - dy * (lons[i+1] - lons[i])
        dirs.append((dx, dy, nx, ny, cross_before + cross_after))

    result = []
    placed = []

    for i, pt in enumerate(points):
        dx, dy, nx, ny, curvature = dirs[i]
        base_ox = nx * base_dist
        base_oy = ny * base_dist - i * stair_step
        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
        ay = (pt.lat - lat_min) * px_p_deg_lat / pps

        candidates = []

        for side_label, side_mult in [("left", 1), ("right", -1)]:
            for dist_mult in [0.7, 1.0, 1.3, 1.7, 2.0]:
                for stair_adj in [0, -7, 7, -14, 14]:
                    ox = base_ox * side_mult * dist_mult
                    oy = (base_oy + stair_adj) * side_mult * dist_mult
                    ha = "left"
                    fx = ax + ox
                    fy = ay + oy
                    bb = _bbox(fx, fy, pt.width, pt.height, ha)

                    overlaps = 0
                    overlap_dist = 0.0
                    for pb in placed:
                        if _overlap(bb, pb):
                            overlaps += 1
                            cx = (bb[0] + bb[2]) * 0.5 - (pb[0] + pb[2]) * 0.5
                            cy = (bb[1] + bb[3]) * 0.5 - (pb[1] + pb[3]) * 0.5
                            overlap_dist += math.hypot(cx, cy)

                    score = -overlaps * 1000 - overlap_dist * 2 - abs(ox) * 0.3 - abs(oy) * 0.15
                    if side_mult == 1 and curvature > 0.03:
                        score += 50
                    elif side_mult == -1 and curvature < -0.03:
                        score += 50
                    candidates.append((score, ox, oy, ha, bb))

        best = max(candidates, key=lambda v: v[0])
        _, ox, oy, ha, bb = best
        result.append(PlacedLabel(
            id=pt.id, lon=pt.lon, lat=pt.lat,
            ox=ox, oy=oy, ha=ha,
            bbox_left=bb[0], bbox_top=bb[1],
            bbox_right=bb[2], bbox_bottom=bb[3],
            text=pt.text, priority=pt.priority
        ))
        placed.append(bb)

    return result


def _bbox(lx, ly, w, h, ha):
    if ha == "right":
        return (lx - w, ly - h * 0.5, lx, ly + h * 0.5)
    return (lx, ly - h * 0.5, lx + w, ly + h * 0.5)


def _overlap(bb, pb, pad=4):
    return (bb[0] + pad < pb[2] and bb[2] - pad > pb[0] and
            bb[1] + pad < pb[3] and bb[3] - pad > pb[1])


def _empty(pt):
    return PlacedLabel(
        id=pt.id, lon=pt.lon, lat=pt.lat,
        ox=0, oy=0, ha="left",
        bbox_left=0, bbox_top=0, bbox_right=0, bbox_bottom=0,
        text=pt.text, priority=pt.priority
    )
