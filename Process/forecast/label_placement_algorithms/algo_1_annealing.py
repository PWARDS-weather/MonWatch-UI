import math
import random
from .shared_models import PlacedLabel


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          iterations=300, start_temp=20.0, end_temp=0.05):
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
        curvature = 0.0
        if i > 0 and i < n - 1:
            curvature = dx * (lats[i+1] - lats[i-1]) - dy * (lons[i+1] - lons[i-1])
        dirs.append((dx, dy, nx, ny, curvature))

    def bbox(lx, ly, w, h, ha):
        if ha == "right":
            return (lx - w, ly - h * 0.5, lx, ly + h * 0.5)
        return (lx, ly - h * 0.5, lx + w, ly + h * 0.5)

    def overlap(bb1, bb2, pad=3):
        return (bb1[0] + pad < bb2[2] and bb1[2] - pad > bb2[0] and
                bb1[1] + pad < bb2[3] and bb1[3] - pad > bb2[1])

    anchor_dist = 65
    state = []
    for i in range(n):
        nx, ny = dirs[i][2], dirs[i][3]
        curvature = dirs[i][4]
        side = 1 if curvature >= 0 else -1
        if abs(curvature) < 0.001:
            side = 1 if i % 2 == 0 else -1
        ox = nx * anchor_dist * side
        oy = ny * anchor_dist * side - i * 14
        ha = "right" if ox < 0 else "left"
        state.append([ox, oy, ha])

    def score(st):
        total = 0.0
        bbs = []
        for i in range(n):
            ox, oy, ha = st[i]
            ax = (points[i].lon - lon_min) * px_p_deg_lon / pps
            ay = (points[i].lat - lat_min) * px_p_deg_lat / pps
            bb = bbox(ax + ox, ay + oy, points[i].width, points[i].height, ha)
            bbs.append(bb)
            total -= abs(ox) * 0.1
            total -= abs(oy) * 0.05
        for i in range(n):
            for j in range(i + 1, n):
                if overlap(bbs[i], bbs[j]):
                    ox = (bbs[i][0] + bbs[i][2]) * 0.5 - (bbs[j][0] + bbs[j][2]) * 0.5
                    oy = (bbs[i][1] + bbs[i][3]) * 0.5 - (bbs[j][1] + bbs[j][3]) * 0.5
                    overlap_amt = math.hypot(ox, oy)
                    total -= 2000.0 / (overlap_amt + 10)
                    total -= 500
        return total

    cur_score = score(state)
    best_state = [list(s) for s in state]
    best_score = cur_score

    for it in range(iterations):
        temp = start_temp * ((end_temp / start_temp) ** (it / max(iterations - 1, 1)))
        idx = random.randrange(n)
        new_state = [list(s) for s in state]

        ox, oy, ha = new_state[idx]
        move_type = random.random()
        if move_type < 0.4:
            ox += random.uniform(-30, 30)
            oy += random.uniform(-30, 30)
        elif move_type < 0.7:
            ox = -ox * random.uniform(0.5, 1.5)
            oy = -oy * random.uniform(0.5, 1.5)
        else:
            ha = "right" if random.random() < 0.5 else "left"
        ox = max(-300, min(300, ox))
        oy = max(-300, min(300, oy))
        new_state[idx] = [ox, oy, ha]

        new_score = score(new_state)
        delta = new_score - cur_score
        if delta > 0 or random.random() < math.exp(delta / max(temp, 0.001)):
            state = new_state
            cur_score = new_score
            if new_score > best_score:
                best_state = [list(s) for s in new_state]
                best_score = new_score

    result = []
    for i in range(n):
        ox, oy, ha = best_state[i]
        ha = "right" if ox < 0 else "left"
        ax = (points[i].lon - lon_min) * px_p_deg_lon / pps
        ay = (points[i].lat - lat_min) * px_p_deg_lat / pps
        bb = bbox(ax + ox, ay + oy, points[i].width, points[i].height, ha)
        result.append(PlacedLabel(
            id=points[i].id, lon=points[i].lon, lat=points[i].lat,
            ox=ox, oy=oy, ha=ha,
            bbox_left=bb[0], bbox_top=bb[1],
            bbox_right=bb[2], bbox_bottom=bb[3],
            text=points[i].text, priority=points[i].priority
        ))

    return result


def _empty(pt):
    return PlacedLabel(
        id=pt.id, lon=pt.lon, lat=pt.lat,
        ox=0, oy=0, ha="left",
        bbox_left=0, bbox_top=0, bbox_right=0, bbox_bottom=0,
        text=pt.text, priority=pt.priority
    )
