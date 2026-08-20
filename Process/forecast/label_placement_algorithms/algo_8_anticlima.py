import math
from .shared_models import PlacedLabel


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          base_offset=15, growth_rate=1.15):
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

    side = 1
    if n >= 3:
        dx0 = lons[1] - lons[0]
        dy0 = lats[1] - lats[0]
        dx1 = lons[2] - lons[1]
        dy1 = lats[2] - lats[1]
        cross = dx0 * dy1 - dy0 * dx1
        side = -1 if cross > 0 else 1

    lat_range = max(lats) - min(lats)
    lon_range = max(lons) - min(lons)
    is_straight = lon_range > 0 and (lat_range / lon_range) < 0.15

    result = []
    cycle_i = 0
    for i, pt in enumerate(points):
        dx, dy, nx, ny = dirs[i]
        factor = growth_rate ** i
        offset_dist = base_offset * factor
        if is_straight:
            straight_offset = base_offset * ((growth_rate + 0.5) ** cycle_i)
            if straight_offset <= offset_dist * 3:
                offset_dist = straight_offset
                cycle_i += 1
            else:
                cycle_i = 0
        ox = nx * offset_dist * side
        oy = ny * offset_dist * side

        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
        ay = (pt.lat - lat_min) * px_p_deg_lat / pps

        fx = ax + ox
        fy = ay + oy
        ha = "left"
        bb = _bbox(fx, fy, pt.width, pt.height, ha)
        result.append(PlacedLabel(
            id=pt.id, lon=pt.lon, lat=pt.lat,
            ox=ox, oy=oy, ha=ha,
            bbox_left=bb[0], bbox_top=bb[1],
            bbox_right=bb[2], bbox_bottom=bb[3],
            text=pt.text, priority=pt.priority
        ))

    for iteration in range(3):
        for i in range(n):
            for j in range(i + 1, n):
                a = result[i]
                b = result[j]
                if _overlap(a, b):
                    dx_px = a.ox - b.ox
                    dy_px = a.oy - b.oy
                    d = math.hypot(dx_px, dy_px)
                    if d < 1:
                        dx_px, dy_px = 1.0, 0.0
                        d = 1.0
                    push = 80.0 / d
                    nxi, nyi = dirs[i][2], dirs[i][3]
                    nxj, nyj = dirs[j][2], dirs[j][3]
                    comp_i = (dx_px * nxi + dy_px * nyi) / d
                    comp_j = (dx_px * nxj + dy_px * nyj) / d
                    result[i] = result[i]._replace(ox=result[i].ox + nxi * comp_i * push,
                                                    oy=result[i].oy + nyi * comp_i * push)
                    result[j] = result[j]._replace(ox=result[j].ox - nxj * comp_j * push,
                                                    oy=result[j].oy - nyj * comp_j * push)

    final = []
    for i, pl in enumerate(result):
        ox, oy = pl.ox, pl.oy
        ha = "right" if ox < 0 else "left"
        ax = (pl.lon - lon_min) * px_p_deg_lon / pps
        ay = (pl.lat - lat_min) * px_p_deg_lat / pps
        bb = _bbox(ax + ox, ay + oy, points[i].width, points[i].height, ha)
        final.append(PlacedLabel(
            id=pl.id, lon=pl.lon, lat=pl.lat,
            ox=ox, oy=oy, ha=ha,
            bbox_left=bb[0], bbox_top=bb[1],
            bbox_right=bb[2], bbox_bottom=bb[3],
            text=pl.text, priority=pl.priority
        ))

    return final


def _bbox(lx, ly, w, h, ha):
    if ha == "right":
        return (lx - w, ly - h * 0.5, lx, ly + h * 0.5)
    return (lx, ly - h * 0.5, lx + w, ly + h * 0.5)


def _overlap(a, b, pad=4):
    return (a.bbox_left + pad < b.bbox_right and
            a.bbox_right - pad > b.bbox_left and
            a.bbox_top + pad < b.bbox_bottom and
            a.bbox_bottom - pad > b.bbox_top)


def _empty(pt):
    return PlacedLabel(
        id=pt.id, lon=pt.lon, lat=pt.lat,
        ox=0, oy=0, ha="left",
        bbox_left=0, bbox_top=0, bbox_right=0, bbox_bottom=0,
        text=pt.text, priority=pt.priority
    )
