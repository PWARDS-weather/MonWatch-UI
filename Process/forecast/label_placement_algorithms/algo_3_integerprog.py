import math
from .shared_models import PlacedLabel


def solve(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          base_dist=65, stair_step=14):
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

    def overlap(bb1, bb2, pad=4):
        return (bb1[0] + pad < bb2[2] and bb1[2] - pad > bb2[0] and
                bb1[1] + pad < bb2[3] and bb1[3] - pad > bb2[1])

    candidates_per_point = []
    for i in range(n):
        pt = points[i]
        dx, dy, nx, ny, curvature = dirs[i]
        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
        ay = (pt.lat - lat_min) * px_p_deg_lat / pps
        cands = []
        for side in [1, -1]:
            for dist_mult in [0.7, 0.85, 1.0, 1.2, 1.4, 1.7, 2.0]:
                for stair_adj in [0, -7, 7]:
                    ox = nx * base_dist * side * dist_mult
                    oy = (ny * base_dist * side - i * stair_step + stair_adj) * dist_mult
                    ha = "right" if ox < 0 else "left"
                    fx = ax + ox
                    fy = ay + oy
                    bb = bbox(fx, fy, pt.width, pt.height, ha)
                    penalty = abs(ox) * 0.3 + abs(oy) * 0.15
                    cands.append((ox, oy, ha, bb, penalty))
        cands.sort(key=lambda c: c[4])
        candidates_per_point.append(cands[:8])

    if n <= 8:
        best_solution = None
        best_cost = float('inf')

        def search(idx, chosen, cost):
            nonlocal best_solution, best_cost
            if cost >= best_cost:
                return
            if idx == n:
                best_solution = list(chosen)
                best_cost = cost
                return
            for cand in candidates_per_point[idx]:
                ox, oy, ha, bb, penalty = cand
                overlaps = False
                for prev in chosen:
                    if prev is not None and overlap(bb, prev[3]):
                        overlaps = True
                        break
                if overlaps:
                    continue
                chosen.append(cand)
                search(idx + 1, chosen, cost + penalty)
                chosen.pop()
            if len(chosen) < idx + 1:
                cheapest = candidates_per_point[idx][0]
                chosen.append(cheapest)
                search(idx + 1, chosen, cost + cheapest[4] + 2000)
                chosen.pop()

        search(0, [], 0)
        if best_solution is None:
            best_solution = [c[0] for c in candidates_per_point]
    else:
        best_solution = []
        placed_bbs = []
        for i in range(n):
            pt = points[i]
            dx, dy, nx, ny, curvature = dirs[i]
            ax = (pt.lon - lon_min) * px_p_deg_lon / pps
            ay = (pt.lat - lat_min) * px_p_deg_lat / pps
            chosen = None
            for cand in candidates_per_point[i]:
                ox, oy, ha, bb, penalty = cand
                conflicts = False
                for pb in placed_bbs:
                    if overlap(bb, pb):
                        conflicts = True
                        break
                if not conflicts:
                    chosen = cand
                    break
            if chosen is None:
                chosen = candidates_per_point[i][0]
            best_solution.append(chosen)
            placed_bbs.append(chosen[3])

    result = []
    for i in range(n):
        ox, oy, ha, bb, _ = best_solution[i]
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
