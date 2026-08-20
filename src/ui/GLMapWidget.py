# =============================================================================
# GLMapWidget.py — GPU reprojection viewport for MonWatch-UI Cyclone V3
# -----------------------------------------------------------------------------
# A QOpenGLWidget that renders the satellite full-disk image as an OpenGL
# texture and reprojects it *per frame* in the vertex shader (the same trick
# SIFT uses). Switching between "Full Disk", "Equirectangular" and
# "Plate Carree" is therefore instant — it is just a uniform change, never a
# pixel resample.
#
# Copyright (C) 2025-2026 PWARDS-weather
# Licensed under GPLv3, see LICENSE.
# =============================================================================

from __future__ import annotations

import ctypes
import math

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtOpenGLWidgets import QOpenGLWidget

try:
    from OpenGL import GL
    HAS_OPENGL = True
except ImportError:  # pragma: no cover
    HAS_OPENGL = False

from src.services import glsl_proj

MODE_FULL_DISK = glsl_proj.MODE_FULL_DISK
MODE_EQUIRECTANGULAR = glsl_proj.MODE_EQUIRECTANGULAR
MODE_PLATE_CARREE = glsl_proj.MODE_PLATE_CARREE
MODE_NAMES = {
    MODE_FULL_DISK: "full_disk",
    MODE_EQUIRECTANGULAR: "equirectangular",
    MODE_PLATE_CARREE: "plate_carree",
}
MODE_IDS = {v: k for k, v in MODE_NAMES.items()}

_WGS84_A = 6378137.0
_DEFAULT_SAT_LON = 140.7
_CROP_DEG = 85.0
_EQC_EXTENT_M = _CROP_DEG * _WGS84_A * math.radians(1.0)
_DEFAULT_MESH_N = 180


def detect_gl_renderer():
    """Probe the OpenGL stack and return (hardware_ok, renderer_string).

    Returns (False, reason) for software renderers (llvmpipe, SwiftShader,
    Basic Render Driver, ...) or when no usable GL context can be created.
    """
    if not HAS_OPENGL:
        return False, "no-pyopengl"
    try:
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext

        ctx = QOpenGLContext()
        if not ctx.create():
            return False, "context-create-failed"
        surf = QOffscreenSurface()
        surf.setFormat(ctx.format())
        surf.create()
        if not surf.isValid():
            return False, "offscreen-surface-invalid"
        if not ctx.makeCurrent(surf):
            return False, "makeCurrent-failed"
        vendor = GL.glGetString(GL.GL_VENDOR) or b""
        renderer = GL.glGetString(GL.GL_RENDERER) or b""
        version = GL.glGetString(GL.GL_VERSION) or b""
        ctx.doneCurrent()
        surf.destroy()
        name = (vendor + b"|" + renderer + b"|" + version).decode("utf-8", "replace")
        low = name.lower()
        software = any(k in low for k in (
            "llvmpipe", "softpipe", "swiftshader", "basic render", "gdi generic", "software"))
        return (not software), name
    except Exception as exc:  # pragma: no cover
        return False, f"exception: {exc!r}"


class GLMapWidget(QOpenGLWidget):
    """OpenGL viewport that reprojects a single satellite texture on the GPU."""

    viewChanged = Signal()          # camera moved (pan/zoom)
    modeChanged = Signal(str)       # "full_disk" | "equirectangular" | "plate_carree"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(200, 200)

        # Source geometry (native GEOS)
        self.source_gt = None
        self.source_shape = None
        self.source_params = None
        self._mesh_n = _DEFAULT_MESH_N
        self._mesh_vertices = None

        # Texture
        self._image = None

        # Camera (world space)
        self._cx = 0.0
        self._cy = 0.0
        self._half_w = 1.0
        self._half_h = 1.0
        self._cam = None

        # Mode
        self._mode = MODE_FULL_DISK
        self._fit_extent_plate = None   # computed lazily from source lon_0

        # Overlay (deferred; v1 hook so existing grid/coast images can be shown)
        self._overlay = None          # QImage
        self._overlay_extent = None   # (xmin, ymin, xmax, ymax) in world coords
        self._overlay_opacity = 1.0

        # OpenGL resources
        self._initialized = False
        self._gl_current = False        # True inside paintEvent / initializeGL
        self._program = None
        self._vbo = None
        self._tex = None
        self._a_pos = -1
        self._a_tex = -1
        self._u_mode = -1
        self._u_cam = -1
        self._u_alpha = -1
        self._u_tex = -1

        # Interaction
        self._panning = False
        self._last_mouse = None
        self._drag_anchor_world = None
        self._zoom_limits = (1e-5, 1e12)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_source(self, gt, geos_params, shape, mesh_n=None):
        """Configure the source GEOS grid from its geotransform + projection params."""
        self.source_gt = gt
        self.source_params = dict(geos_params)
        self.source_shape = tuple(int(x) for x in shape[:2])
        if mesh_n is not None:
            self._mesh_n = int(mesh_n)
        self._mesh_vertices = self._build_mesh(gt, self.source_params, self.source_shape, self._mesh_n)
        self._fit_extent_plate = self._plate_fit_extent()
        if self._initialized:
            self._gl_guarded(lambda: (self._init_shaders(), self._upload_mesh(), self._update_camera()))
        self.update()

    def set_image(self, qimg: QImage):
        """Upload the (RGBA) display image into the GPU texture."""
        if qimg is None or qimg.isNull():
            return
        self._image = qimg.convertToFormat(QImage.Format_RGBA8888)
        if self._initialized:
            self._gl_guarded(self._upload_texture)
        self.update()

    def set_projection(self, mode: str, fit_extent=None):
        """Instantly switch the display projection.

        mode: 'full_disk' | 'equirectangular' | 'plate_carree'
        """
        mode_id = MODE_IDS.get(mode)
        if mode_id is None:
            raise ValueError(f"Unknown projection mode: {mode!r}")
        self._mode = mode_id
        if fit_extent is not None:
            self._fit_extent_plate = tuple(fit_extent)
        if self._initialized:
            self._gl_guarded(lambda: self._set_mode_uniform())
        self.fit_view()
        self.modeChanged.emit(MODE_NAMES[self._mode])
        self.update()

    def set_mode(self, mode_id: int):
        self.set_projection(MODE_NAMES.get(mode_id, "full_disk"))

    def fit_view(self):
        xmin, ymin, xmax, ymax = self._fit_bbox()
        bb_w = max(xmax - xmin, 1e-9)
        bb_h = max(ymax - ymin, 1e-9)
        aspect = max(self.width(), 1) / max(self.height(), 1)
        half_w = 0.5 * max(bb_w, bb_h * aspect) * 1.03
        half_h = 0.5 * max(bb_h, bb_w / aspect) * 1.03
        self._cx = 0.5 * (xmin + xmax)
        self._cy = 0.5 * (ymin + ymax)
        self._half_w = half_w
        self._half_h = half_h
        if self._initialized:
            self._gl_guarded(self._update_camera)
        self.viewChanged.emit()
        self.update()

    def reset_view(self):
        self.fit_view()

    def set_overlay_pixmap(self, pixmap, world_extent, opacity=1.0):
        """Draw a provided overlay image (grid/coast) on top of the GL scene.

        world_extent: (xmin, ymin, xmax, ymax) in the current mode's world
        coordinates so the overlay stays aligned across projections.
        """
        if pixmap is None:
            self._overlay = None
            self._overlay_extent = None
            self.update()
            return
        self._overlay = pixmap.toImage() if isinstance(pixmap, QPixmap) else pixmap
        self._overlay_extent = tuple(world_extent)
        self._overlay_opacity = float(opacity)
        self.update()

    def clear_overlay(self):
        self.set_overlay_pixmap(None, None)

    def to_qimage(self) -> QImage:
        """Capture the current framebuffer as an RGBA QImage."""
        if not self._initialized:
            return QImage()
        if self._gl_current:
            return self._read_framebuffer()
        self.makeCurrent()
        try:
            return self._read_framebuffer()
        finally:
            self.doneCurrent()

    def _read_framebuffer(self) -> QImage:
        w, h = self.width(), self.height()
        buf = (ctypes.c_uint8 * (w * h * 4))()
        GL.glReadPixels(0, 0, w, h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, buf)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4).copy()
        arr = np.ascontiguousarray(arr[::-1])
        return QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy()

    # ------------------------------------------------------------------ #
    # World <-> widget mapping
    # ------------------------------------------------------------------ #

    def _gl_guarded(self, fn):
        """Run GL code with the widget's context made current (re-entrant safe)."""
        if not self._initialized or not HAS_OPENGL:
            return None
        if not self.isValid():
            return None
        if self._gl_current:
            return fn()
        try:
            self.makeCurrent()
            return fn()
        finally:
            self.doneCurrent()

    def _set_mode_uniform(self):
        if self._program is None:
            return
        GL.glUseProgram(self._program)
        GL.glUniform1i(self._u_mode, self._mode)

    def world_to_widget(self, wx: float, wy: float):
        nx = (wx - self._cx) / self._half_w
        ny = (wy - self._cy) / self._half_h
        px = (nx * 0.5 + 0.5) * self.width()
        py = (0.5 - ny * 0.5) * self.height()
        return px, py

    def widget_to_world(self, px: float, py: float):
        nx = px / self.width() * 2.0 - 1.0
        ny = 1.0 - py / self.height() * 2.0
        return self._cx + nx * self._half_w, self._cy + ny * self._half_h

    # ------------------------------------------------------------------ #
    # Qt OpenGL lifecycle
    # ------------------------------------------------------------------ #

    def initializeGL(self):
        if not HAS_OPENGL:
            self._initialized = False
            return
        self._gl_current = True
        try:
            self._init_shaders()
            self._upload_mesh()
            self._create_texture()
            if self._image is not None:
                self._upload_texture()
            self._update_camera()
            self._initialized = True
            GL.glUseProgram(self._program)
            GL.glUniform1i(self._u_mode, self._mode)
        finally:
            self._gl_current = False

    def resizeGL(self, w, h):
        GL.glViewport(0, 0, w, h)

    def paintEvent(self, event):
        if not self._initialized or not HAS_OPENGL:
            QPainter(self).fillRect(self.rect(), QColor("#000000"))
            return
        self.makeCurrent()
        self._gl_current = True
        try:
            self._draw_scene()
            self._draw_overlay()
        finally:
            self._gl_current = False
            self.doneCurrent()

    # ------------------------------------------------------------------ #
    # Interaction
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._panning = True
            self._last_mouse = event.position()
            self._drag_anchor_world = self.widget_to_world(event.position().x(), event.position().y())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        elif event.button() == Qt.RightButton:
            self.fit_view()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._panning and self._last_mouse is not None:
            dx_px = event.position().x() - self._last_mouse.x()
            dy_px = event.position().y() - self._last_mouse.y()
            self._cx -= dx_px * (2.0 * self._half_w) / max(self.width(), 1)
            self._cy += dy_px * (2.0 * self._half_h) / max(self.height(), 1)
            self._last_mouse = event.position()
            if self._initialized:
                self._gl_guarded(self._update_camera)
            self.viewChanged.emit()
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        self._panning = False
        self._last_mouse = None
        self.setCursor(Qt.OpenHandCursor if False else Qt.ArrowCursor)

    def wheelEvent(self, event):
        factor = 0.85 if event.angleDelta().y() > 0 else 1.0 / 0.85
        wx, wy = self.widget_to_world(event.position().x(), event.position().y())
        self._half_w = min(max(self._half_w * factor, self._zoom_limits[0]), self._zoom_limits[1])
        self._half_h = min(max(self._half_h * factor, self._zoom_limits[0]), self._zoom_limits[1])
        # Keep the world point under the cursor stationary
        nx = (wx - self._cx) / self._half_w
        ny = (wy - self._cy) / self._half_h
        self._cx = wx - nx * self._half_w
        self._cy = wy - ny * self._half_h
        if self._initialized:
            self._gl_guarded(self._update_camera)
        self.viewChanged.emit()
        self.update()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.fit_view()
            event.accept()

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _fit_bbox(self):
        if self._mode == MODE_FULL_DISK:
            return self._source_extents()
        if self._mode == MODE_EQUIRECTANGULAR:
            m = _EQC_EXTENT_M
            return (-m, -m, m, m)
        if self._fit_extent_plate is not None:
            return self._fit_extent_plate
        return (-180.0, -90.0, 180.0, 90.0)

    def _plate_fit_extent(self):
        """lon/lat box (degrees) that exactly contains the visible disk.

        The disk never wraps the antimeridian and its lon range is always
        within the satellite's longitude hemisphere, so a plain box is safe.
        """
        lon_0 = self.source_params.get("lon_0", _DEFAULT_SAT_LON) if self.source_params else _DEFAULT_SAT_LON
        r = _CROP_DEG
        return (lon_0 - r, -r, lon_0 + r, r)

    def _source_half(self):
        if self.source_gt is None or self.source_shape is None:
            return 1.0, 1.0
        gt = self.source_gt
        w, h = self.source_shape[1], self.source_shape[0]
        half_x = max(abs(gt.c), abs(gt.c + gt.a * w))
        half_y = max(abs(gt.f), abs(gt.f + gt.e * h))
        return (half_x, half_y) if half_x or half_y else (1.0, 1.0)

    def _source_extents(self):
        """Image corner extents in GEOS meters: (xmin, ymin, xmax, ymax)."""
        if self.source_gt is None or self.source_shape is None:
            return (-1.0, -1.0, 1.0, 1.0)
        gt = self.source_gt
        w, h = self.source_shape[1], self.source_shape[0]
        xmin = min(gt.c, gt.c + gt.a * w)
        ymin = min(gt.f, gt.f + gt.e * h)
        xmax = max(gt.c, gt.c + gt.a * w)
        ymax = max(gt.f, gt.f + gt.e * h)
        return xmin, ymin, xmax, ymax

    def _build_mesh(self, gt, params, shape, n):
        half_x, half_y = self._source_half()
        xmin, ymin, xmax, ymax = self._source_extents()
        verts = np.empty((n * n * 6, 4), dtype=np.float32)
        idx = 0
        for iy in range(n):
            u1 = 1.0 - iy / n          # v at top of cell
            u0 = 1.0 - (iy + 1) / n
            y1 = ymax - (ymax - ymin) * iy / n
            y0 = ymax - (ymax - ymin) * (iy + 1) / n
            for ix in range(n):
                x0 = xmin + (xmax - xmin) * ix / n
                x1 = xmin + (xmax - xmin) * (ix + 1) / n
                v0 = ix / n
                v1 = (ix + 1) / n
                # (x, y, u, v) triangle strip quad
                quad = (
                    (x0, y1, v0, u1), (x1, y1, v1, u1), (x1, y0, v1, u0),
                    (x0, y1, v0, u1), (x1, y0, v1, u0), (x0, y0, v0, u0),
                )
                verts[idx:idx + 6] = quad
                idx += 6
        return np.ascontiguousarray(verts, dtype=np.float32)

    def _init_shaders(self):
        vert_src = glsl_proj.build_vertex_shader(
            self.source_params if self.source_params is not None else glsl_proj.default_geos_params(),
            glsl_proj.eqc_constants(),
        )
        frag_src = glsl_proj.FRAGMENT_SHADER

        prog = GL.glCreateProgram()
        vs = GL.glCreateShader(GL.GL_VERTEX_SHADER)
        GL.glShaderSource(vs, vert_src)
        GL.glCompileShader(vs)
        if not GL.glGetShaderiv(vs, GL.GL_COMPILE_STATUS):
            raise RuntimeError(f"Vertex shader compile failed: {GL.glGetShaderInfoLog(vs)}")
        fs = GL.glCreateShader(GL.GL_FRAGMENT_SHADER)
        GL.glShaderSource(fs, frag_src)
        GL.glCompileShader(fs)
        if not GL.glGetShaderiv(fs, GL.GL_COMPILE_STATUS):
            raise RuntimeError(f"Fragment shader compile failed: {GL.glGetShaderInfoLog(fs)}")
        GL.glAttachShader(prog, vs)
        GL.glAttachShader(prog, fs)
        GL.glLinkProgram(prog)
        if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
            raise RuntimeError(f"Shader link failed: {GL.glGetProgramInfoLog(prog)}")
        GL.glDeleteShader(vs)
        GL.glDeleteShader(fs)

        self._program = prog
        self._a_pos = GL.glGetAttribLocation(prog, "a_pos")
        self._a_tex = GL.glGetAttribLocation(prog, "a_tex")
        self._u_mode = GL.glGetUniformLocation(prog, "u_mode")
        self._u_cam = GL.glGetUniformLocation(prog, "u_cam")
        self._u_alpha = GL.glGetUniformLocation(prog, "u_alpha")
        self._u_tex = GL.glGetUniformLocation(prog, "u_tex")

    def _upload_mesh(self):
        if self._mesh_vertices is None:
            return
        if self._vbo is None:
            self._vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, self._mesh_vertices.tobytes(), GL.GL_STATIC_DRAW)
        stride = 4 * 4  # x,y,u,v = 4 floats
        GL.glEnableVertexAttribArray(self._a_pos)
        GL.glVertexAttribPointer(self._a_pos, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(self._a_tex)
        GL.glVertexAttribPointer(self._a_tex, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(8))
        self._mesh_count = len(self._mesh_vertices)

    def _create_texture(self):
        self._tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        for pname, value in (
            (GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST),
            (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
            (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE),
            (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE),
        ):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, pname, value)

    def _upload_texture(self):
        if self._tex is None or self._image is None:
            return
        w, h = self._image.width(), self._image.height()
        ptr = self._image.bits()
        if hasattr(ptr, "setsize"):
            try:
                ptr.setsize(w * h * 4)
            except ValueError:
                pass
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, w, 4)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, arr)

    def _update_camera(self):
        m = np.eye(4, dtype=np.float32)
        m[0, 0] = 1.0 / self._half_w
        m[1, 1] = 1.0 / self._half_h
        m[0, 3] = -self._cx / self._half_w
        m[1, 3] = -self._cy / self._half_h
        self._cam = np.ascontiguousarray(m.T.ravel(), dtype=np.float32)
        if self._program is not None:
            GL.glUseProgram(self._program)
            GL.glUniformMatrix4fv(self._u_cam, 1, GL.GL_FALSE, self._cam)

    def _draw_scene(self):
        if self._tex is None or self._vbo is None:
            GL.glClearColor(0.0, 0.0, 0.0, 1.0)
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)
            return
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glViewport(0, 0, self.width(), self.height())
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)

        GL.glUseProgram(self._program)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        GL.glUniform1i(self._u_tex, 0)
        GL.glUniform1i(self._u_mode, self._mode)
        GL.glUniform1f(self._u_alpha, 1.0)
        GL.glUniformMatrix4fv(self._u_cam, 1, GL.GL_FALSE, self._cam)

        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        stride = 16
        GL.glEnableVertexAttribArray(self._a_pos)
        GL.glVertexAttribPointer(self._a_pos, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(0))
        GL.glEnableVertexAttribArray(self._a_tex)
        GL.glVertexAttribPointer(self._a_tex, 2, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(8))

        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, self._mesh_count)

    def _draw_overlay(self):
        if self._overlay is None or self._overlay_extent is None:
            return
        xmin, ymin, xmax, ymax = self._overlay_extent
        p1 = self.world_to_widget(xmin, ymax)
        p2 = self.world_to_widget(xmax, ymin)
        rect = _QRectF(p1[0], p1[1], p2[0] - p1[0], p2[1] - p1[1])
        if rect.width() <= 0 or rect.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setOpacity(self._overlay_opacity)
        painter.drawImage(rect, self._overlay)
        painter.end()


def _QRectF(x, y, w, h):
    from PySide6.QtCore import QRectF
    return QRectF(float(x), float(y), float(w), float(h))