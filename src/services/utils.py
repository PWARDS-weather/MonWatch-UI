from datetime import datetime
import math
from PySide6.QtGui import QVector3D


def compute_sun_direction(dt: datetime, lat: float = 0.0) -> QVector3D:
    day_angle = 2 * math.pi * (dt.timetuple().tm_yday - 1) / 365.0
    hour_angle = math.radians((dt.hour + dt.minute / 60.0 - 12) * 15)
    declination = (0.006918 - 0.399912 * math.cos(day_angle) + 0.070257 * math.sin(day_angle)
                   - 0.006758 * math.cos(2 * day_angle) + 0.000907 * math.sin(2 * day_angle)
                   - 0.002697 * math.cos(3 * day_angle) + 0.001480 * math.sin(3 * day_angle))
    lat_r = math.radians(lat)
    elevation = (math.asin(math.sin(lat_r) * math.sin(declination)
                           + math.cos(lat_r) * math.cos(declination) * math.cos(hour_angle)))
    azimuth = math.atan2(-math.sin(hour_angle),
                         math.tan(declination) * math.cos(lat_r) - math.sin(lat_r) * math.cos(hour_angle))
    east = math.cos(elevation) * math.sin(azimuth)
    north = math.cos(elevation) * math.cos(azimuth)
    up = math.sin(elevation)
    vec = QVector3D(east, north, up)
    if vec.length() > 0:
        vec.normalize()
    return vec


def compute_sun_azel(dt: datetime, lat: float = 0.0):
    """Compute sun azimuth (0-360) and elevation (0-90) from a UTC datetime."""
    day_angle = 2 * math.pi * (dt.timetuple().tm_yday - 1) / 365.0
    hour_angle = math.radians((dt.hour + dt.minute / 60.0 - 12) * 15)
    declination = (0.006918 - 0.399912 * math.cos(day_angle) + 0.070257 * math.sin(day_angle)
                   - 0.006758 * math.cos(2 * day_angle) + 0.000907 * math.sin(2 * day_angle)
                   - 0.002697 * math.cos(3 * day_angle) + 0.001480 * math.sin(3 * day_angle))
    lat_r = math.radians(lat)
    elevation = math.asin(math.sin(lat_r) * math.sin(declination)
                          + math.cos(lat_r) * math.cos(declination) * math.cos(hour_angle))
    azimuth = math.atan2(-math.sin(hour_angle),
                         math.tan(declination) * math.cos(lat_r) - math.sin(lat_r) * math.cos(hour_angle))
    az_deg = math.degrees(azimuth) % 360
    el_deg = max(0.0, min(90.0, math.degrees(elevation)))
    return az_deg, el_deg
