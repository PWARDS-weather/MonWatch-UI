from __future__ import annotations
from dataclasses import dataclass
import math
from PySide6.QtGui import QQuaternion, QVector3D


@dataclass
class CameraState:
    lat: float = 14.6
    lon: float = 121.0
    zoom: float = 1.0
    heading: float = 0.0
    pitch: float = 15.0

    def to_quaternion(self) -> QQuaternion:
        lon_rot = QQuaternion.fromAxisAndAngle(QVector3D(0, 1, 0), -(self.lon + 90))
        lat_rot = QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), self.lat)
        heading_rot = QQuaternion.fromAxisAndAngle(QVector3D(0, 0, 1), self.heading)
        pitch_rot = QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), self.pitch)
        return lon_rot * lat_rot * heading_rot * pitch_rot

    def interpolate(self, other: CameraState, t: float) -> CameraState:
        dlon = (other.lon - self.lon + 540) % 360 - 180
        return CameraState(
            lat=self.lat + (other.lat - self.lat) * t,
            lon=self.lon + dlon * t,
            zoom=self.zoom + (other.zoom - self.zoom) * t,
            heading=self.heading + (other.heading - self.heading) * t,
            pitch=self.pitch + (other.pitch - self.pitch) * t,
        )

    def copy(self) -> CameraState:
        return CameraState(self.lat, self.lon, self.zoom, self.heading, self.pitch)

    def delta(self, other: CameraState) -> float:
        return abs(self.lat - other.lat) + abs(self.lon - other.lon) + abs(self.zoom - other.zoom) * 2
