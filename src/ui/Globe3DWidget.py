# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: ui/Globe3DWidget.py
# Description: 3D globe visualization widget for geostationary satellite data display.
#
# Principal Developer: Zero (Prince Al Zhanjie B. Dela Rosa)
# Affiliation: PWARDS-weather
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# See also: https://www.apache.org/licenses/LICENSE-2.0
#
# Copyright (C) 2025-2026 PWARDS-weather (Pasacao Weather Atmospheric
# and Real-Time Data System)
#
# --- OPEN-SOURCE POLICY ---
# Redistribution or modification without formally notifying PWARDS-weather
# developers constitutes unauthorized use and violates the license terms.
# Developers must be notified via email or GitHub issue before any changes
# are distributed. See LICENSE file for complete terms.
# =============================================================================

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QColor, QMouseEvent, QMatrix4x4, QVector3D, QQuaternion
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QMenu
import numpy as np
import math

try:
    from OpenGL import GL
    HAS_OPENGL = True
except ImportError:
    HAS_OPENGL = False

try:
    import shapefile
    HAS_PYSHP = True
except ImportError:
    HAS_PYSHP = False

from ..core.helpers import COASTLINE_SHP

# ── geometry generators ─────────────────────────────────────────────────────

def _latlon_to_3d(lat_deg, lon_deg, radius=1.0):
    lat_r = math.radians(lat_deg)
    lon_r = math.radians(lon_deg)
    return (
        radius * math.cos(lat_r) * math.cos(lon_r),
        radius * math.sin(lat_r),
        -radius * math.cos(lat_r) * math.sin(lon_r),
    )


def _build_sphere_mesh(lat_seg=48, lon_seg=72, radius=1.0):
    verts, uvs, idx = [], [], []
    for i in range(lat_seg + 1):
        theta = math.pi * i / lat_seg
        st, ct = math.sin(theta), math.cos(theta)
        for j in range(lon_seg + 1):
            phi = -2 * math.pi * j / lon_seg
            sp, cp = math.sin(phi), math.cos(phi)
            verts.extend([radius * cp * st, radius * ct, radius * sp * st])
            uvs.extend([j / lon_seg, i / lat_seg])
    for i in range(lat_seg):
        for j in range(lon_seg):
            a = i * (lon_seg + 1) + j
            b = a + lon_seg + 1
            idx.extend([a, b, a + 1, b, b + 1, a + 1])
    return (np.array(verts, dtype=np.float32),
            np.array(uvs, dtype=np.float32),
            np.array(idx, dtype=np.uint32))


def _build_grid_lines(lat_step=30, lon_step=30, radius=1.01):
    lines = []
    for lat in range(-90, 91, lat_step):
        pts = []
        for lon in range(0, 361, 5):
            pts.extend(_latlon_to_3d(lat, lon, radius))
        lines.append(pts)
    for lon in range(0, 360, lon_step):
        pts = []
        for lat in range(-90, 91, 5):
            pts.extend(_latlon_to_3d(lat, lon, radius))
        lines.append(pts)
    eq = []
    for lon in range(0, 361, 2):
        eq.extend(_latlon_to_3d(0, lon, radius * 1.005))
    lines.append(eq)
    return lines


def _load_coastlines(radius=1.05):
    if not HAS_PYSHP:
        return []
    shp_path = COASTLINE_SHP
    if not shp_path.exists():
        return []
    try:
        sf = shapefile.Reader(str(shp_path))
        out = []
        for sr in sf.iterShapes():
            pts = sr.points
            if len(pts) < 3:
                continue
            flat = []
            for lon, lat in pts:
                if abs(lat) > 89.9:
                    continue
                flat.extend(_latlon_to_3d(lat, lon, radius))
            if flat:
                out.append(flat)
        sf.close()
        return out
    except Exception:
        return []

# ── OpenGL helpers ──────────────────────────────────────────────────────────

_VSHADER = """
#version 330
layout(location=0) in vec3 aPos;
layout(location=1) in vec2 aUV;
uniform mat4 uMVP;
out vec2 vUV;
void main(){ gl_Position=uMVP*vec4(aPos,1); vUV=aUV; }
"""

_FSHADER = """
#version 330
in vec2 vUV;
uniform sampler2D uTex;
uniform bool uUseTex;
uniform vec4 uColor;
out vec4 fC;
void main(){
    if(uUseTex){
        fC=texture(uTex,vUV);
    }else{
        fC=uColor;
    }
}
"""

_LINE_VS = """
#version 330
layout(location=0) in vec3 aPos;
uniform mat4 uMVP;
void main(){ gl_Position=uMVP*vec4(aPos,1); }
"""

_LINE_FS = """
#version 330
uniform vec4 uColor;
out vec4 fC;
void main(){ fC=uColor; }
"""


def _compile(stype, src):
    s = GL.glCreateShader(stype)
    GL.glShaderSource(s, src)
    GL.glCompileShader(s)
    if not GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS):
        log = GL.glGetShaderInfoLog(s)
        GL.glDeleteShader(s)
        raise RuntimeError(f"Shader error: {log}")
    return s


def _link(vs, fs):
    p = GL.glCreateProgram()
    GL.glAttachShader(p, vs)
    GL.glAttachShader(p, fs)
    GL.glLinkProgram(p)
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    return p


# ── main widget ─────────────────────────────────────────────────────────────

class Globe3DWidget(QOpenGLWidget):
    mouseMoved = Signal(float, float)
    zoomChanged = Signal(float)
    ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setMouseTracking(True)

        self._rotation = (QQuaternion.fromAxisAndAngle(QVector3D(0, 1, 0), -90) *
                          QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), 15))
        self._zoom = 1.0
        self._target_zoom = 1.0
        self._pan = QVector3D(0, 0, 0)

        self._tex_id = 0
        self._fcast_tex_id = 0
        self._sphere_vao = 0
        self._sphere_count = 0
        self._grid_vaos = []
        self._coast_vaos = []
        self._prog = 0
        self._line_prog = 0

        self._ready = False
        self._last_mouse = None
        self._rotating = False
        self._roll = None
        self._panning = False

        self._zoom_timer = QTimer(self)
        self._zoom_timer.setInterval(16)
        self._zoom_timer.timeout.connect(self._step_zoom)
        self._zoom_timer.start()

    # ── OpenGL lifecycle ────────────────────────────────────────────────────

    def initializeGL(self):
        if not HAS_OPENGL:
            return
        try:
            GL.glClearColor(0.04, 0.04, 0.08, 1.0)
            GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

            self._prog = _link(_compile(GL.GL_VERTEX_SHADER, _VSHADER),
                               _compile(GL.GL_FRAGMENT_SHADER, _FSHADER))
            self._line_prog = _link(_compile(GL.GL_VERTEX_SHADER, _LINE_VS),
                                    _compile(GL.GL_FRAGMENT_SHADER, _LINE_FS))

            self._build_sphere_vao()
            self._build_grid_vaos()
            self._build_coast_vaos()
            self._ready = True
            self.ready.emit()
        except Exception as e:
            self._ready = False

    def _build_sphere_vao(self):
        verts, uvs, idx = _build_sphere_mesh(48, 72)
        self._sphere_count = len(idx)
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(0)
        tbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, tbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, uvs.nbytes, uvs, GL.GL_STATIC_DRAW)
        GL.glVertexAttribPointer(1, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(1)
        ebo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ebo)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
        GL.glBindVertexArray(0)
        self._sphere_vao = vao

    def _build_line_vao(self, flat_verts):
        arr = np.array(flat_verts, dtype=np.float32)
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, GL.GL_STATIC_DRAW)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(0)
        GL.glBindVertexArray(0)
        return vao, len(arr) // 3, vbo

    def _build_grid_vaos(self):
        lines = _build_grid_lines(30, 30, 1.01)
        for l in lines:
            vao, cnt, vbo = self._build_line_vao(l)
            self._grid_vaos.append((vao, cnt, vbo))

    def _build_coast_vaos(self):
        lines = _load_coastlines(1.008)
        for l in lines:
            vao, cnt, vbo = self._build_line_vao(l)
            self._coast_vaos.append((vao, cnt, vbo))

    def resizeGL(self, w, h):
        if self._ready:
            GL.glViewport(0, 0, w, h)

    def paintGL(self):
        if not self._ready:
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
            return
        try:
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
            w, h = self.width(), self.height()
            aspect = w / h if h > 0 else 1.0

            proj = QMatrix4x4()
            proj.perspective(45.0 / self._zoom, aspect, 0.1, 10.0)
            view = QMatrix4x4()
            view.translate(0, 0, -2.5)
            view.translate(self._pan)
            rot = QMatrix4x4()
            rot.rotate(self._rotation)
            mvp = proj * view * rot
            mvp_f = np.array(mvp.data(), dtype=np.float32)

            # sphere
            GL.glUseProgram(self._prog)
            mvp_loc = GL.glGetUniformLocation(self._prog, "uMVP")
            tex_loc = GL.glGetUniformLocation(self._prog, "uTex")
            use_loc = GL.glGetUniformLocation(self._prog, "uUseTex")
            col_loc = GL.glGetUniformLocation(self._prog, "uColor")

            GL.glUniformMatrix4fv(mvp_loc, 1, GL.GL_FALSE, mvp_f)

            if self._tex_id != 0:
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex_id)
                GL.glUniform1i(tex_loc, 0)
                GL.glUniform1i(use_loc, 1)
            else:
                GL.glUniform1i(use_loc, 0)
                GL.glUniform4f(col_loc, 0.08, 0.12, 0.25, 1.0)

            GL.glBindVertexArray(self._sphere_vao)
            GL.glDrawElements(GL.GL_TRIANGLES, self._sphere_count, GL.GL_UNSIGNED_INT, None)
            GL.glBindVertexArray(0)
            GL.glUseProgram(0)

            # forecast overlay
            if self._fcast_tex_id != 0:
                GL.glUseProgram(self._prog)
                GL.glUniformMatrix4fv(mvp_loc, 1, GL.GL_FALSE, mvp_f)
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._fcast_tex_id)
                GL.glUniform1i(tex_loc, 0)
                GL.glUniform1i(use_loc, 1)
                GL.glBindVertexArray(self._sphere_vao)
                GL.glDrawElements(GL.GL_TRIANGLES, self._sphere_count, GL.GL_UNSIGNED_INT, None)
                GL.glBindVertexArray(0)
                GL.glUseProgram(0)

            # grid lines
            GL.glUseProgram(self._line_prog)
            mloc = GL.glGetUniformLocation(self._line_prog, "uMVP")
            cloc = GL.glGetUniformLocation(self._line_prog, "uColor")
            GL.glUniformMatrix4fv(mloc, 1, GL.GL_FALSE, mvp_f)
            GL.glLineWidth(1.5)
            GL.glUniform4f(cloc, 0.4, 0.6, 0.8, 0.6)
            for vao, cnt, _ in self._grid_vaos:
                GL.glBindVertexArray(vao)
                GL.glDrawArrays(GL.GL_LINE_STRIP, 0, cnt)
                GL.glBindVertexArray(0)

            # coastlines
            GL.glLineWidth(2.5)
            GL.glUniform4f(cloc, 0.8, 0.95, 0.8, 1.0)
            for vao, cnt, _ in self._coast_vaos:
                GL.glBindVertexArray(vao)
                GL.glDrawArrays(GL.GL_LINE_STRIP, 0, cnt)
                GL.glBindVertexArray(0)
            GL.glLineWidth(1.0)
            GL.glUseProgram(0)
        except Exception:
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

    # ── texture upload ──────────────────────────────────────────────────────

    def _upload_tex(self, image):
        if not self._ready or image is None or image.isNull():
            print("[UPLOAD] not ready or null image")
            return 0
        img = image.convertToFormat(QImage.Format_RGBA8888)
        if img.isNull():
            print("[UPLOAD] convertToFormat failed")
            return 0
        w, h = img.width(), img.height()
        if w < 2 or h < 2:
            print(f"[UPLOAD] image too small: {w}x{h}")
            return 0
        try:
            data = img.bits().tobytes()
            arr = np.frombuffer(data, dtype=np.uint8).reshape(h, w, 4).copy()
        except Exception as e:
            print(f"[UPLOAD] failed to read image data: {e}")
            return 0
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, w, h, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, arr)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        print(f"[UPLOAD] tex_id={tex} ({w}x{h})")
        return tex

    def set_texture_image(self, image):
        print(f"[SETTEX] set_texture_image called, image null={image.isNull() if image else 'None'}, w={image.width() if image else 0}")
        self.makeCurrent()
        if self._tex_id:
            GL.glDeleteTextures(1, [self._tex_id])
        self._tex_id = self._upload_tex(image)
        print(f"[SETTEX] new _tex_id={self._tex_id}")
        self.doneCurrent()
        self.update()

    def set_forecast_texture(self, image):
        self.makeCurrent()
        if self._fcast_tex_id:
            GL.glDeleteTextures(1, [self._fcast_tex_id])
        self._fcast_tex_id = self._upload_tex(image)
        self.doneCurrent()
        self.update()

    # ── interactions ────────────────────────────────────────────────────────

    def reset_view(self):
        self._rotation = (QQuaternion.fromAxisAndAngle(QVector3D(0, 1, 0), 0) *
                          QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), 15))
        self._zoom = self._target_zoom = 1.0
        self._pan = QVector3D(0, 0, 0)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._rotating = True
            self._roll = 'orbit'
            self._last_mouse = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.MiddleButton:
            self._rotating = True
            self._roll = 'roll'
            self._last_mouse = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton:
            self._show_cm(event.pos())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._rotating = False
            self._roll = None
            self._last_mouse = None
            self.setCursor(Qt.ArrowCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._rotating and self._last_mouse is not None:
            dx = event.pos().x() - self._last_mouse.x()
            dy = event.pos().y() - self._last_mouse.y()
            if self._roll == 'orbit':
                if dx:
                    self._rotation = (QQuaternion.fromAxisAndAngle(QVector3D(0, 1, 0), dx * 0.5) *
                                      self._rotation)
                if dy:
                    self._rotation = self._rotation * QQuaternion.fromAxisAndAngle(QVector3D(1, 0, 0), dy * 0.5)
            elif self._roll == 'roll':
                if dx:
                    self._rotation = self._rotation * QQuaternion.fromAxisAndAngle(QVector3D(0, 0, 1), dx * 0.5)
            self._last_mouse = event.pos()
            self.update()
        elif self._panning and self._last_mouse is not None:
            d = event.pos() - self._last_mouse
            self._pan += QVector3D(d.x() * 0.003 * self._zoom, -d.y() * 0.003 * self._zoom, 0)
            self._last_mouse = event.pos()
            self.update()

    def wheelEvent(self, event):
        d = event.angleDelta().y()
        self._target_zoom *= 1.1 if d > 0 else 0.9
        self._target_zoom = max(0.2, min(10.0, self._target_zoom))

    def _step_zoom(self):
        if abs(self._zoom - self._target_zoom) < 0.001:
            self._zoom = self._target_zoom
            return
        self._zoom += (self._target_zoom - self._zoom) * 0.25
        self.zoomChanged.emit(self._zoom)
        self.update()

    def _show_cm(self, pos):
        m = QMenu(self)
        m.setStyleSheet("QMenu{background:#1f1f1f;color:#ddd;border:1px solid #444}"
                        "QMenu::item:selected{background:#4c1d95;color:#fff}"
                        "QMenu::item{padding:4px 20px}")
        a = m.addAction("Reset View")
        if m.exec(self.mapToGlobal(pos)) == a:
            self.reset_view()

    def cleanup(self):
        if not self._ready:
            return
        self.makeCurrent()
        if self._sphere_vao:
            GL.glDeleteVertexArrays(1, [self._sphere_vao])
        for vao, _, vbo in self._grid_vaos + self._coast_vaos:
            GL.glDeleteVertexArrays(1, [vao])
            GL.glDeleteBuffers(1, [vbo])
        if self._tex_id:
            GL.glDeleteTextures(1, [self._tex_id])
        if self._fcast_tex_id:
            GL.glDeleteTextures(1, [self._fcast_tex_id])
        if self._prog:
            GL.glDeleteProgram(self._prog)
        if self._line_prog:
            GL.glDeleteProgram(self._line_prog)
        self.doneCurrent()

    def closeEvent(self, event):
        self._zoom_timer.stop()
        self.cleanup()
        super().closeEvent(event)
