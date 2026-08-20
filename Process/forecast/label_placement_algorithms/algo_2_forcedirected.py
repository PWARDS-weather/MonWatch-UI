import math
from .shared_models import PlacedLabel


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          iterations=80, spring_k=0.05, repulsion=8000,
          anchor_dist=65, stair_step=14):
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
        dirs.append((dx, dy, nx, ny))

    def bbox(lx, ly, w, h, ha):
        if ha == "right":
            return (lx - w, ly - h * 0.5, lx, ly + h * 0.5)
        return (lx, ly - h * 0.5, lx + w, ly + h * 0.5)

    def overlap(bb1, bb2, pad=4):
        return (bb1[0] + pad < bb2[2] and bb1[2] - pad > bb2[0] and
                bb1[1] + pad < bb2[3] and bb1[3] - pad > bb2[1])

    positions = []
    ha_list = []
    for i in range(n):
        nx, ny = dirs[i][2], dirs[i][3]
        side = 1 if i % 2 == 0 else -1
        ox = nx * anchor_dist * side
        oy = ny * anchor_dist * side - i * stair_step
        positions.append([ox, oy])
        ha_list.append("right" if ox < 0 else "left")

    velocities = [[0.0, 0.0] for _ in range(n)]
    damping = 0.88

    for iteration in range(iterations):
        temp = 1.0 - (iteration / iterations)
        forces = [[0.0, 0.0] for _ in range(n)]

        ow = max(points[0].width, 28)
        oh = max(points[0].height, 18)

        for i in range(n):
            nx, ny = dirs[i][2], dirs[i][3]
            side = 1 if i % 2 == 0 else -1
            ax = nx * anchor_dist * side
            ay = ny * anchor_dist * side - i * stair_step
            dx = ax - positions[i][0]
            dy = ay - positions[i][1]
            forces[i][0] += dx * spring_k * temp
            forces[i][1] += dy * spring_k * temp

        for i in range(n):
            for j in range(i + 1, n):
                dx = positions[i][0] - positions[j][0]
                dy = positions[i][1] - positions[j][1]
                dist = math.hypot(dx, dy)
                min_dist = (ow + ow) * 0.6
                if dist < min_dist:
                    if dist < 1:
                        dist = 1
                    force = repulsion / (dist * dist + 1) * temp * temp
                    fx = dx / dist * force
                    fy = dy / dist * force
                    forces[i][0] += fx
                    forces[i][1] += fy
                    forces[j][0] -= fx
                    forces[j][1] -= fy

        for i in range(n):
            velocities[i][0] = (velocities[i][0] + forces[i][0]) * damping
            velocities[i][1] = (velocities[i][1] + forces[i][1]) * damping
            positions[i][0] += velocities[i][0]
            positions[i][1] += velocities[i][1]
            max_move = 50
            if abs(positions[i][0]) > max_move * 3:
                positions[i][0] = math.copysign(max_move * 3, positions[i][0])

    result = []
    for i in range(n):
        ox, oy = positions[i]
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

    for iteration in range(10):
        changed = False
        for i in range(n):
            for j in range(i + 1, n):
                bb_i = (result[i].bbox_left, result[i].bbox_top,
                        result[i].bbox_right, result[i].bbox_bottom)
                bb_j = (result[j].bbox_left, result[j].bbox_top,
                        result[j].bbox_right, result[j].bbox_bottom)
                if overlap(bb_i, bb_j):
                    ox_i, oy_i = result[i].ox, result[i].oy
                    ox_j, oy_j = result[j].ox, result[j].oy
                    dx = ox_i - ox_j
                    dy = oy_i - oy_j
                    d = math.hypot(dx, dy)
                    if d < 1:
                        d = 1
                        dx, dy = 1.0, 0.0
                    push = 30
                    dx_u, dy_u = dx / d, dy / d
                    result[i] = result[i]._replace(ox=ox_i + dx_u * push,
                                                    oy=oy_i + dy_u * push)
                    result[j] = result[j]._replace(ox=ox_j - dx_u * push,
                                                    oy=oy_j - dy_u * push)
                    ax_i = (result[i].lon - lon_min) * px_p_deg_lon / pps
                    ay_i = (result[i].lat - lat_min) * px_p_deg_lat / pps
                    ax_j = (result[j].lon - lon_min) * px_p_deg_lon / pps
                    ay_j = (result[j].lat - lat_min) * px_p_deg_lat / pps
                    ha_i = "right" if result[i].ox < 0 else "left"
                    ha_j = "right" if result[j].ox < 0 else "left"
                    bb_i = bbox(ax_i + result[i].ox, ay_i + result[i].oy,
                                points[i].width, points[i].height, ha_i)
                    bb_j = bbox(ax_j + result[j].ox, ay_j + result[j].oy,
                                points[j].width, points[j].height, ha_j)
                    result[i] = result[i]._replace(bbox_left=bb_i[0], bbox_top=bb_i[1],
                                                    bbox_right=bb_i[2], bbox_bottom=bb_i[3],
                                                    ha=ha_i)
                    result[j] = result[j]._replace(bbox_left=bb_j[0], bbox_top=bb_j[1],
                                                    bbox_right=bb_j[2], bbox_bottom=bb_j[3],
                                                    ha=ha_j)
                    changed = True
        if not changed:
            break

    return result


def _empty(pt):
    return PlacedLabel(
        id=pt.id, lon=pt.lon, lat=pt.lat,
        ox=0, oy=0, ha="left",
        bbox_left=0, bbox_top=0, bbox_right=0, bbox_bottom=0,
        text=pt.text, priority=pt.priority
    )
