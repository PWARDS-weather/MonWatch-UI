import math
from collections import namedtuple

Point = namedtuple("Point", [
    "id", "lon", "lat", "priority", "hour", "text",
    "width", "height", "intensity"
])

PlacedLabel = namedtuple("PlacedLabel", [
    "id", "lon", "lat", "ox", "oy", "ha",
    "bbox_left", "bbox_top", "bbox_right", "bbox_bottom",
    "text", "priority"
])


def adapt_from_track_points(track_points):
    converted = []
    for i, pt in enumerate(track_points):
        lon = pt.get("lon")
        lat = pt.get("lat")
        if lon is None or lat is None:
            continue
        raw_text = str(pt.get("intensity_label", "")) or ""
        priority = float(pt.get("advanced_hours", 0))
        hour = int(pt.get("advanced_hours", 0))
        w = max(len(raw_text) * 7, 28)
        h = 22
        converted.append(Point(
            id=i, lon=lon, lat=lat,
            priority=priority, hour=hour,
            text=raw_text, width=w, height=h,
            intensity=pt.get("intensity", 0)
        ))
    return converted


def adapt_to_offsets(placed_labels):
    result = []
    for pl in placed_labels:
        ox = round(pl.ox, 1)
        oy = round(pl.oy, 1)
        ha = pl.ha
        result.append(((ox, oy), ha))
    return result


def adapt_to_offsets_flat(placed_labels):
    result = []
    for pl in placed_labels:
        ox = round(pl.ox, 1)
        oy = round(pl.oy, 1)
        ha = pl.ha
        result.append((ox, oy, ha))
    return result
