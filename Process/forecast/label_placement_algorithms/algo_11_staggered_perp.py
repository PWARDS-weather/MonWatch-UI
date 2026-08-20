import math
from .shared_models import PlacedLabel


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          base_offset=55):
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
            dx = (lons[i + 1] - lons[i - 1]) * 0.5
            dy = (lats[i + 1] - lats[i - 1]) * 0.5
        ln = math.hypot(dx, dy)
        if ln > 1e-10:
            dx /= ln
            dy /= ln
        else:
            dx, dy = 1.0, 0.0
        nx, ny = -dy, dx

        curvature = 0.0
        if 0 < i < n - 1:
            dx_prev = lons[i] - lons[i - 1]
            dy_prev = lats[i] - lats[i - 1]
            dx_next = lons[i + 1] - lons[i]
            dy_next = lats[i + 1] - lats[i]
            ln_prev = math.hypot(dx_prev, dy_prev)
            ln_next = math.hypot(dx_next, dy_next)
            if ln_prev > 1e-10 and ln_next > 1e-10:
                cross_val = (dx_prev / ln_prev) * (dy_next / ln_next) - (dy_prev / ln_prev) * (dx_next / ln_next)
                curvature = cross_val

        dirs.append((nx, ny, curvature))

    result = []
    for i, pt in enumerate(points):
        nx, ny, curvature = dirs[i]
        perp_side = 1
        if abs(curvature) > 0.01:
            perp_side = 1 if curvature > 0 else -1
        else:
            perp_side = 1 if i % 2 == 0 else -1
        offset_dist = base_offset
        ox = nx * offset_dist * perp_side
        oy = ny * offset_dist * perp_side

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

    result = _resolve_collisions(result, n, dirs, points,
                                 px_p_deg_lon, px_p_deg_lat,
                                 lon_min, lat_min, pps, base_offset)

    result = _snap_resolve(result, n, dirs, points,
                           px_p_deg_lon, px_p_deg_lat,
                           lon_min, lat_min, pps, base_offset)

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


def _resolve_collisions(result, n, dirs, points,
                        px_p_deg_lon, px_p_deg_lat,
                        lon_min, lat_min, pps, base_offset):
    for iteration in range(8):
        any_collision = False
        for i in range(n):
            for j in range(i + 1, n):
                a = result[i]
                b = result[j]
                if _overlap(a, b):
                    any_collision = True
                    dx_px = a.ox - b.ox
                    dy_px = a.oy - b.oy
                    d = math.hypot(dx_px, dy_px)
                    if d < 1:
                        dx_px, dy_px = 1.0, 0.0
                        d = 1.0
                    push = (base_offset * 0.8) / max(d, 1)
                    nxi, nyi = dirs[i][0], dirs[i][1]
                    nxj, nyj = dirs[j][0], dirs[j][1]
                    comp_i = abs(dx_px * nxi + dy_px * nyi) / d
                    comp_j = abs(dx_px * nxj + dy_px * nyj) / d
                    if comp_i > 0.1:
                        result[i] = result[i]._replace(
                            ox=result[i].ox + nxi * comp_i * push,
                            oy=result[i].oy + nyi * comp_i * push
                        )
                    if comp_j > 0.1:
                        result[j] = result[j]._replace(
                            ox=result[j].ox - nxj * comp_j * push,
                            oy=result[j].oy - nyj * comp_j * push
                        )

        for i in range(n):
            nxi, nyi = dirs[i][0], dirs[i][1]
            dist = math.hypot(result[i].ox, result[i].oy)
            if dist < base_offset * 0.8:
                nxi, nyi, curvature = dirs[i]
                perp_side = 1 if curvature >= 0 else -1
                result[i] = result[i]._replace(
                    ox=nxi * base_offset * perp_side,
                    oy=nyi * base_offset * perp_side
                )

        if not any_collision:
            break
    return result


def _snap_resolve(result, n, dirs, points,
                  px_p_deg_lon, px_p_deg_lat,
                  lon_min, lat_min, pps, base_offset):
    snap_distances = [0.7, 1.0, 1.3, 1.6, 2.0]
    for sweep in range(3):
        any_overlap = False
        for i in range(n):
            for j in range(i + 1, n):
                if _overlap(result[i], result[j]):
                    any_overlap = True
                    for idx in (i, j):
                        pl = result[idx]
                        pt = points[idx]
                        nxi, nyi, curvature = dirs[idx]
                        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
                        ay = (pt.lat - lat_min) * px_p_deg_lat / pps
                        best_ox = pl.ox
                        best_oy = pl.oy
                        best_score = -999999
                        for side_mult in (1, -1):
                            for dist_mult in snap_distances:
                                ox = nxi * base_offset * dist_mult * side_mult
                                oy = nyi * base_offset * dist_mult * side_mult
                                ha = "right" if ox < 0 else "left"
                                fx = ax + ox
                                fy = ay + oy
                                bb = _bbox(fx, fy, pt.width, pt.height, ha)
                                score = -abs(ox) * 0.5 - abs(oy) * 0.3
                                for k, other in enumerate(result):
                                    if k != idx:
                                        obb = (other.bbox_left, other.bbox_top,
                                               other.bbox_right, other.bbox_bottom)
                                        if _overlap_bb(bb, obb):
                                            score -= 100
                                if score > best_score:
                                    best_score = score
                                    best_ox = ox
                                    best_oy = oy
                        result[idx] = result[idx]._replace(ox=best_ox, oy=best_oy)
                    break
        if not any_overlap:
            break
    return result


def _bbox(lx, ly, w, h, ha):
    if ha == "right":
        return (lx - w, ly - h * 0.5, lx, ly + h * 0.5)
    return (lx, ly - h * 0.5, lx + w, ly + h * 0.5)


def _overlap(a, b, pad=4):
    return (a.bbox_left + pad < b.bbox_right and
            a.bbox_right - pad > b.bbox_left and
            a.bbox_top + pad < b.bbox_bottom and
            a.bbox_bottom - pad > b.bbox_top)


def _overlap_bb(bb1, bb2, pad=4):
    return (bb1[0] + pad < bb2[2] and
            bb1[2] - pad > bb2[0] and
            bb1[1] + pad < bb2[3] and
            bb1[3] - pad > bb2[1])


def _empty(pt):
    return PlacedLabel(
        id=pt.id, lon=pt.lon, lat=pt.lat,
        ox=0, oy=0, ha="left",
        bbox_left=0, bbox_top=0, bbox_right=0, bbox_bottom=0,
        text=pt.text, priority=pt.priority
    )
