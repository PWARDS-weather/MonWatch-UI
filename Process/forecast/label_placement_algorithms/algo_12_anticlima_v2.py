import math
from .shared_models import PlacedLabel


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


def _min_offset_for_cone(pt, probability_circles, side, px_p_deg_lon, px_p_deg_lat, pps):
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
          base_offset=20, probability_circles=None):
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
        dirs.append((dx, dy, nx, ny))

    side = 1
    if n >= 3:
        dx0 = lons[1] - lons[0]
        dy0 = lats[1] - lats[0]
        dx1 = lons[2] - lons[1]
        dy1 = lats[2] - lats[1]
        cross = dx0 * dy1 - dy0 * dx1
        side = -1 if cross > 0 else 1

    pixel_dists = [0.0] * n
    for i in range(n - 1):
        dlon = (lons[i + 1] - lons[i]) * px_p_deg_lon / pps
        dlat = (lats[i + 1] - lats[i]) * px_p_deg_lat / pps
        pixel_dists[i] = pixel_dists[i + 1] = math.hypot(dlon, dlat)
    pixel_dists[0] = pixel_dists[1] if n > 1 else 0
    pixel_dists[-1] = pixel_dists[-2] if n > 1 else 0
    close_threshold = 80.0

    placed = []

    for i, pt in enumerate(points):
        nx, ny = dirs[i][2], dirs[i][3]
        offset = base_offset
        if pixel_dists[i] < close_threshold:
            offset = base_offset * 1.3

        min_cone_px = _min_offset_for_cone(pt, probability_circles, side,
                                           px_p_deg_lon, px_p_deg_lat, pps)
        if min_cone_px > 0:
            offset = max(offset, min_cone_px)

        ox = nx * offset * side
        oy = ny * offset * side

        ax = (pt.lon - lon_min) * px_p_deg_lon / pps
        ay = (pt.lat - lat_min) * px_p_deg_lat / pps
        fx = ax + ox
        fy = ay + oy
        ha = "left"
        bb = _bbox(fx, fy, pt.width, pt.height, ha)

        label = PlacedLabel(
            id=pt.id, lon=pt.lon, lat=pt.lat,
            ox=ox, oy=oy, ha=ha,
            bbox_left=bb[0], bbox_top=bb[1],
            bbox_right=bb[2], bbox_bottom=bb[3],
            text=pt.text, priority=pt.priority
        )

        for push_pass in range(20):
            collides = False
            for prev in placed:
                if _overlap(label, prev):
                    collides = True
                    push_dist = max(prev.bbox_bottom - label.bbox_top + 8, 8)
                    new_oy = label.oy - push_dist
                    ax_upd = (pt.lon - lon_min) * px_p_deg_lon / pps
                    ay_upd = (pt.lat - lat_min) * px_p_deg_lat / pps
                    new_bb = _bbox(ax_upd + ox, ay_upd + new_oy, pt.width, pt.height, ha)
                    label = label._replace(
                        oy=new_oy,
                        bbox_top=new_bb[1], bbox_bottom=new_bb[3]
                    )

                    if min_cone_px > 0:
                        cur_dist = math.hypot(ox, label.oy)
                        if cur_dist < min_cone_px:
                            new_oy = -(math.sqrt(max(min_cone_px**2 - ox**2, 0)))
                            new_bb = _bbox(ax_upd + ox, ay_upd + new_oy,
                                           pt.width, pt.height, ha)
                            label = label._replace(
                                oy=new_oy,
                                bbox_top=new_bb[1], bbox_bottom=new_bb[3]
                            )
                    break
            if not collides:
                break

        placed.append(label)

    final = []
    for i, pl in enumerate(placed):
        ox, oy = pl.ox, pl.oy
        ha = "right" if ox < 0 else "left"
        ax = (points[i].lon - lon_min) * px_p_deg_lon / pps
        ay = (points[i].lat - lat_min) * px_p_deg_lat / pps
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
