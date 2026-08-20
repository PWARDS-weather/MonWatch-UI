import math
from .shared_models import PlacedLabel


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          along_dist=70, stair_step=14):
    n = len(points)
    if n < 2:
        return [_empty(pt) for pt in points]

    pps = max(pts_p_px, 1e-10)
    lons = [p.lon for p in points]
    lats = [p.lat for p in points]

    tangents = []
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
        tangents.append((dx, dy, nx, ny))

    result = []
    for i, pt in enumerate(points):
        dx, dy, nx, ny = tangents[i]

        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
        ay = (pt.lat - lat_min) * px_p_deg_lat / pps

        ox = dx * along_dist
        oy = dy * along_dist - i * stair_step
        ha = "left"

        fx = ax + ox
        fy = ay + oy
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
                if (_overlap(a, b)):
                    dx_px = a.ox - b.ox
                    dy_px = a.oy - b.oy
                    d = math.hypot(dx_px, dy_px)
                    if d < 1:
                        dx_px, dy_px = 1.0, 0.0
                        d = 1.0
                    push = 80.0 / d
                    result[i] = result[i]._replace(ox=result[i].ox + dx_px / d * push,
                                                    oy=result[i].oy + dy_px / d * push)
                    result[j] = result[j]._replace(ox=result[j].ox - dx_px / d * push,
                                                    oy=result[j].oy - dy_px / d * push)

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
