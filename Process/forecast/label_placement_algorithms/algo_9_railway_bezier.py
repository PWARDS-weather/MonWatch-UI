import math
from .shared_models import PlacedLabel


def _catmull_rom(lons, lats, segments_per_segment=10):
    n = len(lons)
    if n == 2:
        out_lons, out_lats = [], []
        for j in range(segments_per_segment + 1):
            t = j / segments_per_segment
            out_lons.append(lons[0] + (lons[1] - lons[0]) * t)
            out_lats.append(lats[0] + (lats[1] - lats[0]) * t)
        return out_lons, out_lats

    out_lons, out_lats = [], []
    for i in range(n - 1):
        p0 = (lons[max(0, i - 1)], lats[max(0, i - 1)])
        p1 = (lons[i], lats[i])
        p2 = (lons[i + 1], lats[i + 1])
        p3 = (lons[min(n - 1, i + 2)], lats[min(n - 1, i + 2)])
        for j in range(segments_per_segment):
            t = j / segments_per_segment
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                        (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                        (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                        (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                        (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out_lons.append(x)
            out_lats.append(y)
    out_lons.append(lons[-1])
    out_lats.append(lats[-1])
    return out_lons, out_lats


def _cone_radius_at(lon, lat, probability_circles):
    if not probability_circles:
        return 0.0
    best_r = 0.0
    for circ in probability_circles:
        clon = circ.get("center_lon") or (circ.get("center")[1] if circ.get("center") and len(circ["center"]) > 1 else None)
        clat = circ.get("center_lat") or (circ.get("center")[0] if circ.get("center") and len(circ["center"]) > 0 else None)
        rm = circ.get("radius_m") or circ.get("radius")
        if clon is None or clat is None or not rm:
            continue
        d = math.hypot(lon - clon, lat - clat) * 111320.0
        if d < float(rm) * 1.1:
            best_r = max(best_r, float(rm))
    return best_r


def _min_offset_for_cone(pt, probability_circles, px_p_deg_lon, px_p_deg_lat, pps):
    if not probability_circles:
        return 0
    cr = _cone_radius_at(pt.lon, pt.lat, probability_circles)
    if cr <= 0:
        return 0
    cos_lat = math.cos(math.radians(pt.lat))
    cr_deg_lon = cr / (111320.0 * max(cos_lat, 0.01))
    cr_deg_lat = cr / 111320.0
    cr_px = max(cr_deg_lon * px_p_deg_lon / pps, cr_deg_lat * px_p_deg_lat / pps) * 1.2
    return cr_px


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          base_offset=60, step=16, max_iter=20,
          probability_circles=None):
    n = len(points)
    if n < 2:
        return [_empty(pt) for pt in points]

    pps = max(pts_p_px, 1e-10)
    lons = [p.lon for p in points]
    lats = [p.lat for p in points]

    interp_lons, interp_lats = _catmull_rom(lons, lats, 10)

    dirs = []
    for i in range(n):
        orig_t = i / (n - 1) if n > 1 else 0
        idx = int(round(orig_t * (len(interp_lons) - 1)))
        idx = max(0, min(idx, len(interp_lons) - 1))
        if idx == 0:
            dx = interp_lons[1] - interp_lons[0]
            dy = interp_lats[1] - interp_lats[0]
        elif idx == len(interp_lons) - 1:
            dx = interp_lons[-1] - interp_lons[-2]
            dy = interp_lats[-1] - interp_lats[-2]
        else:
            dx = (interp_lons[idx+1] - interp_lons[idx-1]) * 0.5
            dy = (interp_lats[idx+1] - interp_lats[idx-1]) * 0.5
        ln = math.hypot(dx, dy)
        if ln > 1e-10:
            dx /= ln
            dy /= ln
        else:
            dx, dy = 1.0, 0.0
        nx, ny = -dy, dx

        curvature = 0.0
        if 0 < i < n - 1:
            dx_prev = interp_lons[idx] - interp_lons[max(0, idx-1)]
            dy_prev = interp_lats[idx] - interp_lats[max(0, idx-1)]
            dx_next = interp_lons[min(len(interp_lons)-1, idx+1)] - interp_lons[idx]
            dy_next = interp_lats[min(len(interp_lats)-1, idx+1)] - interp_lats[idx]
            ln_prev = math.hypot(dx_prev, dy_prev)
            ln_next = math.hypot(dx_next, dy_next)
            if ln_prev > 1e-10 and ln_next > 1e-10:
                curvature = (dx_prev / ln_prev) * (dy_next / ln_next) - (dy_prev / ln_prev) * (dx_next / ln_next)

        dirs.append((dx, dy, nx, ny, curvature))

    curvature_sum = sum(d[4] for d in dirs)
    side = 1 if curvature_sum >= 0 else -1

    result = []
    for i, pt in enumerate(points):
        dx, dy, nx, ny, curvature = dirs[i]
        if abs(curvature) > 0.01:
            rail_side = side if curvature > 0 else -side
        else:
            rail_side = side * (1 if i % 2 == 0 else -1)

        offset = base_offset
        min_cone_px = _min_offset_for_cone(pt, probability_circles,
                                           px_p_deg_lon, px_p_deg_lat, pps)
        if min_cone_px > 0:
            offset = max(offset, min_cone_px)

        ox = nx * offset * rail_side
        oy = ny * offset * rail_side

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

    for iteration in range(8):
        any_overlap = False
        for i in range(n):
            for j in range(i + 1, n):
                a = result[i]
                b = result[j]
                if _overlap(a, b):
                    any_overlap = True
                    dx_px = a.ox - b.ox
                    dy_px = a.oy - b.oy
                    d = math.hypot(dx_px, dy_px)
                    if d < 1:
                        dx_px, dy_px = 1.0, 0.0
                        d = 1.0
                    push = (base_offset * 0.8) / max(d, 1)
                    nxi, nyi = dirs[i][2], dirs[i][3]
                    nxj, nyj = dirs[j][2], dirs[j][3]
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
        if not any_overlap:
            break

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


def _snap_resolve(result, n, dirs, points,
                  px_p_deg_lon, px_p_deg_lat,
                  lon_min, lat_min, pps, base_offset):
    snap_distances = [0.6, 0.8, 1.0, 1.2, 1.4, 1.7, 2.0]
    for sweep in range(3):
        any_overlap = False
        for i in range(n):
            for j in range(i + 1, n):
                if _overlap(result[i], result[j]):
                    any_overlap = True
                    for idx in (i, j):
                        pl = result[idx]
                        pt = points[idx]
                        nxi, nyi = dirs[idx][2], dirs[idx][3]
                        _, _, _, _, curvature = dirs[idx]
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
                                score = -(abs(ox) + abs(oy)) * 0.1
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


def get_curve(points):
    lons = [p.lon for p in points]
    lats = [p.lat for p in points]
    return _catmull_rom(lons, lats, 10)


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
