import math
from .shared_models import PlacedLabel


ANGLES_DEG = [0, 45, 90, 135, 180, 225, 270, 315]
DIRECTION_NAMES = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def place(points, px_p_deg_lon=100, px_p_deg_lat=100,
          lon_min=0, lat_min=0, pts_p_px=1.0,
          base_offset=65, w_overlap_label=10.0, w_angular=3.0):
    n = len(points)
    if n < 2:
        return [_empty(pt) for pt in points]

    pps = max(pts_p_px, 1e-10)
    lons = [p.lon for p in points]
    lats = [p.lat for p in points]

    candidates = []
    for deg in ANGLES_DEG:
        rad = math.radians(deg)
        candidates.append((math.cos(rad), math.sin(rad)))

    incoming_angles = [None] * n
    outgoing_angles = [None] * n
    curvatures = [0.0] * n
    for i in range(n):
        if i > 0:
            dx_in = lons[i] - lons[i - 1]
            dy_in = lats[i] - lats[i - 1]
            incoming_angles[i] = math.degrees(math.atan2(dx_in, dy_in)) % 360
        if i < n - 1:
            dx_out = lons[i + 1] - lons[i]
            dy_out = lats[i + 1] - lats[i]
            outgoing_angles[i] = math.degrees(math.atan2(dx_out, dy_out)) % 360
        if 0 < i < n - 1:
            dx_prev = lons[i] - lons[i - 1]
            dy_prev = lats[i] - lats[i - 1]
            dx_next = lons[i + 1] - lons[i]
            dy_next = lats[i + 1] - lats[i]
            ln_prev = math.hypot(dx_prev, dy_prev)
            ln_next = math.hypot(dx_next, dy_next)
            if ln_prev > 1e-10 and ln_next > 1e-10:
                curvatures[i] = (dx_prev / ln_prev) * (dy_next / ln_next) - (dy_prev / ln_prev) * (dx_next / ln_next)

    side_pref = 1
    avg_curv = sum(curvatures) / max(n, 1)
    if abs(avg_curv) > 0.01:
        side_pref = 1 if avg_curv > 0 else -1

    result = []
    placed_bboxes = []

    for i, pt in enumerate(points):
        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
        ay = (pt.lat - lat_min) * px_p_deg_lat / pps

        sector_weights = [1.0] * 8
        if incoming_angles[i] is not None:
            _weight_sectors_for_angle(sector_weights, incoming_angles[i], 0.3)
        if outgoing_angles[i] is not None:
            _weight_sectors_for_angle(sector_weights, outgoing_angles[i], 0.3)

        angular_dir = _preferred_reading_direction(incoming_angles[i], outgoing_angles[i])
        if angular_dir is not None:
            pref_sector = int(round(angular_dir / 45)) % 8
            sector_weights[pref_sector] *= 0.5
            sector_weights[(pref_sector + 1) % 8] *= 0.7
            sector_weights[(pref_sector - 1) % 8] *= 0.7

        best_score = -999999
        best_ox = 0
        best_oy = 0
        best_ha = "left"
        best_candidate_idx = 0

        dist_mult_range = [0.7, 1.0, 1.3, 1.6, 2.0]

        for dist_mult in dist_mult_range:
            for j, (dx, dy) in enumerate(candidates):
                weight = sector_weights[j]
                if weight < 0.15:
                    continue

                ox = dx * base_offset * dist_mult
                oy = dy * base_offset * dist_mult
                ha = "right" if ox < 0 else "left"
                fx = ax + ox
                fy = ay + oy
                bb = _bbox(fx, fy, pt.width, pt.height, ha)

                overlap_label = 0
                for pb in placed_bboxes:
                    if _overlap_bb(bb, pb):
                        overlap_label += 1

                angular_penalty = 0
                if angular_dir is not None:
                    actual_angle = ANGLES_DEG[j]
                    dev = min(abs(actual_angle - angular_dir),
                              360 - abs(actual_angle - angular_dir))
                    angular_penalty = dev / 45.0

                perp_angle = (ANGLES_DEG[j] + 90) % 360
                perp_pref = None
                if incoming_angles[i] is not None:
                    perp_pref = (incoming_angles[i] + 90) % 360
                    if 180 < perp_pref < 360:
                        perp_pref = perp_pref - 180 if perp_pref > 270 else perp_pref
                if perp_pref is not None:
                    perp_dev = min(abs(perp_pref - perp_angle), 360 - abs(perp_pref - perp_angle))
                    perp_penalty = perp_dev / 45.0
                else:
                    perp_penalty = 0

                dist_from_track = math.hypot(ox, oy)
                dist_penalty = dist_from_track / (base_offset * 2.0)

                cost = (w_overlap_label * overlap_label * (2.0 - weight) +
                        w_angular * angular_penalty * (1.0 + 0.5 * (1.0 - weight)) +
                        perp_penalty * 1.5 +
                        dist_penalty * 0.5)
                score = -cost + weight * 5

                if side_pref > 0 and ox > 0:
                    score += 2
                elif side_pref < 0 and ox < 0:
                    score += 2

                if abs(angular_dir - ANGLES_DEG[j]) < 45:
                    score += 3

                if score > best_score:
                    best_score = score
                    best_ox = ox
                    best_oy = oy
                    best_ha = ha
                    best_candidate_idx = j

        placed_bboxes.append(_bbox(ax + best_ox, ay + best_oy,
                                   pt.width, pt.height, best_ha))
        result.append(PlacedLabel(
            id=pt.id, lon=pt.lon, lat=pt.lat,
            ox=best_ox, oy=best_oy, ha=best_ha,
            bbox_left=placed_bboxes[-1][0],
            bbox_top=placed_bboxes[-1][1],
            bbox_right=placed_bboxes[-1][2],
            bbox_bottom=placed_bboxes[-1][3],
            text=pt.text, priority=pt.priority
        ))

    result = _iterative_relaxation(result, n, candidates, points,
                                   px_p_deg_lon, px_p_deg_lat,
                                   lon_min, lat_min, pps, base_offset)

    result = _snap_resolve(result, n, candidates, points,
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


def _weight_sectors_for_angle(sector_weights, angle_deg, penalty_weight):
    sector = int(round(angle_deg / 45)) % 8
    sector_weights[sector] *= (1.0 - penalty_weight * 0.8)
    sector_weights[(sector + 1) % 8] *= (1.0 - penalty_weight * 0.5)
    sector_weights[(sector - 1) % 8] *= (1.0 - penalty_weight * 0.5)


def _preferred_reading_direction(in_angle, out_angle):
    if in_angle is None and out_angle is None:
        return None
    if in_angle is None:
        avg = out_angle
    elif out_angle is None:
        avg = in_angle
    else:
        avg = (in_angle + out_angle) / 2
    pref_sector = int(round(avg / 45)) % 8
    return ANGLES_DEG[(pref_sector + 4) % 8]


def _iterative_relaxation(result, n, candidates, points,
                          px_p_deg_lon, px_p_deg_lat,
                          lon_min, lat_min, pps, base_offset):
    dist_mult_range = [0.7, 1.0, 1.3, 1.6, 2.0, 2.5]
    for iteration in range(8):
        any_collision = False
        new_result = list(result)
        for i in range(n):
            for j in range(i + 1, n):
                if _overlap(new_result[i], new_result[j]):
                    any_collision = True
                    for idx in (i, j):
                        pl = new_result[idx]
                        pt = points[idx]
                        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
                        ay = (pt.lat - lat_min) * px_p_deg_lat / pps
                        best_score = -999999
                        best_ox = pl.ox
                        best_oy = pl.oy
                        best_ha = pl.ha
                        dist_scale = 1.0 + iteration * 0.25
                        for dist_mult in dist_mult_range:
                            for dx, dy in candidates:
                                ox = dx * base_offset * dist_mult * dist_scale
                                oy = dy * base_offset * dist_mult * dist_scale
                                ha = "right" if ox < 0 else "left"
                                fx = ax + ox
                                fy = ay + oy
                                bb = _bbox(fx, fy, pt.width, pt.height, ha)
                                score = 0
                                for k, other in enumerate(new_result):
                                    if k != idx:
                                        if _overlap_bb(bb, (other.bbox_left, other.bbox_top,
                                                             other.bbox_right, other.bbox_bottom)):
                                            score -= 10
                                score -= (abs(ox) + abs(oy)) * 0.1
                                if score > best_score:
                                    best_score = score
                                    best_ox = ox
                                    best_oy = oy
                                    best_ha = ha
                        new_result[idx] = new_result[idx]._replace(
                            ox=best_ox, oy=best_oy, ha=best_ha
                        )
        result = new_result
        if not any_collision:
            break
    return result


def _snap_resolve(result, n, candidates, points,
                  px_p_deg_lon, px_p_deg_lat,
                  lon_min, lat_min, pps, base_offset):
    dist_mult_range = [0.6, 0.8, 1.0, 1.2, 1.4, 1.7, 2.0]
    for sweep in range(3):
        any_overlap = False
        for i in range(n):
            for j in range(i + 1, n):
                if _overlap(result[i], result[j]):
                    any_overlap = True
                    for idx in (i, j):
                        pl = result[idx]
                        pt = points[idx]
                        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
                        ay = (pt.lat - lat_min) * px_p_deg_lat / pps
                        best_ox = pl.ox
                        best_oy = pl.oy
                        best_score = -999999
                        for dist_mult in dist_mult_range:
                            for dx, dy in candidates:
                                ox = dx * base_offset * dist_mult
                                oy = dy * base_offset * dist_mult
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
