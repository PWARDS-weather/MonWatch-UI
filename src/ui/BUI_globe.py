from __future__ import annotations
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QColor, QMatrix4x4, QVector3D, QQuaternion, QPainter, QPainterPath, QPen
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QMenu
import numpy as np
import math
from typing import Optional

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
from ..core.animation_camera import CameraState
import os


def _latlon_to_3d(lat_deg, lon_deg, radius=1.0):
    lat_r = math.radians(lat_deg)
    lon_r = math.radians(lon_deg)
    return (
        radius * math.cos(lat_r) * math.cos(lon_r),
        radius * math.sin(lat_r),
        -radius * math.cos(lat_r) * math.sin(lon_r),
    )

def _build_sphere_mesh(lat_seg=48, lon_seg=72, radius=1.0):
    verts, norms, uvs, idx = [], [], [], []
    for i in range(lat_seg + 1):
        theta = math.pi * i / lat_seg
        st, ct = math.sin(theta), math.cos(theta)
        for j in range(lon_seg + 1):
            phi = -2 * math.pi * j / lon_seg
            sp, cp = math.sin(phi), math.cos(phi)
            x = radius * cp * st
            y = radius * ct
            z = radius * sp * st
            verts.extend([x, y, z])
            length = math.sqrt(x*x + y*y + z*z)
            norms.extend([x / length, y / length, z / length])
            uvs.extend([j / lon_seg, i / lat_seg])
    for i in range(lat_seg):
        for j in range(lon_seg):
            a = i * (lon_seg + 1) + j
            b = a + lon_seg + 1
            idx.extend([a, b, a + 1, b, b + 1, a + 1])
    return (np.array(verts, dtype=np.float32),
            np.array(norms, dtype=np.float32),
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

_VSHADER = """
#version 330
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNormal;
layout(location=2) in vec2 aUV;

uniform mat4 uModelView;
uniform mat4 uProjection;
uniform mat3 uNormalMatrix;
uniform float uTime;
uniform float uHeightScale;
uniform sampler2D uHeightmapTex;
uniform bool uUseHeightmap;

out vec3 vNormal;
out vec3 vViewDir;
out vec2 vUV;
out float vHeight;

void main() {
    vec3 pos = aPos;
    float height = 0.0;
    if (uUseHeightmap) {
        height = texture(uHeightmapTex, aUV).r;
        float displacement = height * uHeightScale;
        pos = aPos + aNormal * displacement;
    }
    float isWater = 1.0 - step(0.1, height);
    float wave1 = sin(aUV.x * 80.0 + uTime * 2.5) * 0.002;
    float wave2 = cos(aUV.y * 60.0 + uTime * 1.8) * 0.002;
    float wave = (wave1 + wave2) * 0.5;
    pos += aNormal * wave * isWater * 0.06;

    vec4 viewPos = uModelView * vec4(pos, 1.0);
    vNormal = uNormalMatrix * aNormal;
    vViewDir = normalize(-viewPos.xyz);
    vUV = aUV;
    vHeight = height;
    gl_Position = uProjection * viewPos;
}
"""

_FSHADER = """
#version 330
in vec3 vNormal;
in vec3 vViewDir;
in vec2 vUV;
in float vHeight;

uniform sampler2D uSatTex;
uniform sampler2D uHeightmapTex;
uniform sampler2D uFcastTex;
uniform sampler2D uCloudTex;
uniform vec3 uLightDir;
uniform float uAmbientIntensity;
uniform float uDiffuseIntensity;
uniform float uCloudOpacity;
uniform float uFcastOpacity;
uniform float uTime;
uniform bool uShowSat;
uniform bool uShowFcast;
uniform bool uShowCloud;
uniform bool uShowTopoLand;
uniform bool uShowTopoWater;

out vec4 fC;

void main() {
    vec3 N = normalize(vNormal);
    vec3 L = normalize(uLightDir);
    float ambient = uAmbientIntensity;
    float diff = max(dot(N, L), 0.0);
    float diffuse = diff * uDiffuseIntensity;
    vec3 H = normalize(L + vViewDir);
    float spec = pow(max(dot(N, H), 0.0), 64.0);
    float specular = spec * 0.3;

    vec2 flipUv = vec2(fract(vUV.x + 0.5), vUV.y);

    float isWater;

    vec3 finalColor;

    if (uShowSat) {
        vec4 satColor = texture(uSatTex, flipUv);
        const vec3 oceanColor = vec3(0.024, 0.071, 0.165);
        isWater = 1.0 - smoothstep(0.05, 0.2, distance(satColor.rgb, oceanColor));
        float fresnel = pow(1.0 - max(dot(N, vViewDir), 0.0), 3.0);
        float reflectivity = mix(0.05, 0.6, fresnel) * isWater;
        vec3 skyReflection = vec3(0.2, 0.35, 0.6);
        finalColor = mix(satColor.rgb, skyReflection, reflectivity * 0.6);
    } else {
        isWater = 1.0;
        finalColor = vec3(0.024, 0.071, 0.165);
    }

    if (uShowCloud) {
        vec4 cloud = texture(uCloudTex, flipUv);
        float cloudAlpha = cloud.r * uCloudOpacity;
        if (cloudAlpha > 0.01) {
            finalColor = mix(finalColor, vec3(1.0), cloudAlpha * 0.8);
        }
    }

    if (uShowFcast) {
        vec4 fcastColor = texture(uFcastTex, flipUv);
        finalColor = mix(finalColor, fcastColor.rgb, uFcastOpacity * fcastColor.a);
    }

    // Topographic override
    if (uShowTopoLand || uShowTopoWater) {
        float h = texture(uHeightmapTex, flipUv).r;
        float seaLevel = 0.45;
        if (uShowTopoLand && h > seaLevel) {
            float elev = (h - seaLevel) / (1.0 - seaLevel);
            vec3 col;
            col = mix(vec3(0.12, 0.35, 0.06), vec3(0.35, 0.42, 0.10), smoothstep(0.0, 0.15, elev));
            col = mix(col, vec3(0.45, 0.32, 0.12), smoothstep(0.15, 0.35, elev));
            col = mix(col, vec3(0.55, 0.48, 0.42), smoothstep(0.35, 0.60, elev));
            col = mix(col, vec3(0.90, 0.90, 0.95), smoothstep(0.60, 0.90, elev));
            finalColor = col;
            isWater = 0.0;
        }
        if (uShowTopoWater && h <= seaLevel) {
            float depth = h / seaLevel;
            vec3 col = mix(vec3(0.005, 0.01, 0.04), vec3(0.10, 0.35, 0.50), smoothstep(0.0, 1.0, depth));
            finalColor = col;
            isWater = 1.0;
        }
    }

    // Animated wave foam on water
    float wave1 = sin(vUV.x * 140.0 + uTime * 3.5) * 0.5 + 0.5;
    float wave2 = cos(vUV.y * 110.0 + uTime * 2.8) * 0.5;
    float wave3 = sin((vUV.x * 0.8 - vUV.y * 0.6) * 130.0 + uTime * 4.0) * 0.5;
    float wave4 = cos((vUV.x * 0.5 + vUV.y * 0.7) * 90.0 + uTime * 2.0) * 0.5;
    float wavePattern = (wave1 + wave2 + wave3 + wave4) * 0.25;
    float foam = smoothstep(0.55, 0.8, wavePattern);
    vec3 foamColor = vec3(0.8, 0.92, 1.0);
    finalColor = mix(finalColor, foamColor, foam * isWater * 0.25);

    fC = vec4(finalColor * (ambient + diffuse) + vec3(specular), 1.0);
}
"""

_ATMOS_VS = """
#version 330
layout(location=0) in vec3 aPos;
uniform mat4 uMVP;
uniform float uRadius;
out vec3 vPos;
out vec3 vNorm;
void main(){
    vec3 p = aPos * uRadius;
    vPos = p;
    vNorm = normalize(aPos);
    gl_Position = uMVP * vec4(p, 1.0);
}
"""

_ATMOS_FS = """
#version 330
in vec3 vPos;
in vec3 vNorm;
uniform vec4 uColor;
uniform vec3 uCameraPos;
out vec4 fC;
void main(){
    vec3 normal = normalize(vNorm);
    vec3 viewDir = normalize(vPos - uCameraPos);
    float NdotV = max(dot(normal, -viewDir), 0.0);
    float rim = 1.0 - NdotV;
    float intensity = pow(rim, 3.5);
    float alpha = intensity * uColor.a;
    float scatterBoost = pow(intensity, 1.5) * 0.4;
    vec3 col = uColor.rgb + vec3(0.1, 0.25, 0.5) * scatterBoost;
    fC = vec4(col, alpha);
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

_PART_VS = """
#version 330
layout(location=0) in vec3 aPos;
uniform mat4 uMVP;
uniform float uPointSize;
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    gl_PointSize = uPointSize;
}
"""

_PART_FS = """
#version 330
out vec4 fC;
void main() {
    vec2 c = gl_PointCoord - 0.5;
    float d = length(c);
    if (d > 0.5) discard;
    float a = smoothstep(0.5, 0.0, d);
    fC = vec4(0.7, 0.85, 1.0, a * 0.6);
}
"""

def _generate_earth_texture(width=2048, height=1024):
    img = QImage(width, height, QImage.Format_RGB32)
    img.fill(QColor(0x06, 0x12, 0x2A))
    if not HAS_PYSHP:
        return img
    shp_path = COASTLINE_SHP
    if not shp_path.exists():
        return img
    try:
        sf = shapefile.Reader(str(shp_path))
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        land_pen = QPen(QColor(0x44, 0xAA, 0x66), 3)
        land_pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(land_pen)
        for sr in sf.iterShapes():
            pts = sr.points
            if len(pts) < 2:
                continue
            path = QPainterPath()
            first = True
            for lon, lat in pts:
                if abs(lat) > 89.9:
                    continue
                x = (lon + 180) / 360 * width
                y = (90 - lat) / 180 * height
                if first:
                    path.moveTo(x, y)
                    first = False
                else:
                    path.lineTo(x, y)
            p.drawPath(path)
        p.setPen(QPen(QColor(0x88, 0xBB, 0xFF, 80), 1))
        for lon in range(-180, 181, 30):
            x = (lon + 180) / 360 * width
            p.drawLine(int(x), 0, int(x), height)
        for lat in range(-90, 91, 30):
            y = (90 - lat) / 180 * height
            p.drawLine(0, int(y), width, int(y))
        p.end()
        sf.close()
    except Exception:
        pass
    return img

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


class EnhancedGlobeWidget(QOpenGLWidget):
    cameraChanged = Signal(CameraState)
    zoomChanged = Signal(float)
    ready = Signal()
    captureKeyframeRequested = Signal()
    orderChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 300)
        self.setMouseTracking(True)

        self._camera = CameraState(lat=14.6, lon=121.0, zoom=1.0, heading=0.0, pitch=15.0)

        self._tex_id = 0
        self._fcast_tex_id = 0
        self._cloud_tex_id = 0
        self._heightmap_tex_id = 0
        self._use_heightmap = False
        self._sphere_vao = 0
        self._atmos_vao = 0
        self._sphere_count = 0
        self._grid_vaos = []
        self._coast_vaos = []
        self._prog = 0
        self._atmos_prog = 0
        self._line_prog = 0
        self._part_prog = 0

        self._ready = False
        self._forecast_opacity = 0.0
        
        self._wind_u_data = None
        self._wind_v_data = None
        self._particle_positions = None
        self._particle_velocities = None
        self._particle_vao = 0
        self._particle_vbo = 0
        self._particle_count = 5000
        self._show_wind_particles = False
        self._wind_loaded = False
        self._last_mouse = None
        self._press_pos = None
        self._rotating = False
        self._panning = False

        self._show_satellite = True
        self._show_forecast = True
        self._show_clouds = True
        self._show_grid = True
        self._show_coastlines = True
        self._show_atmosphere = True
        self._show_topo_land = False
        self._show_topo_water = False
        self._layer_order = ["atmosphere", "satellite", "forecast", "clouds", "grid", "coastlines"]

        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(16)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_start = None
        self._anim_target = None
        self._anim_duration = 0.0
        self._anim_elapsed = 0.0

    def camera(self) -> CameraState:
        return self._camera

    def set_camera(self, cam: CameraState, animate: bool = False, duration: float = 1.0):
        if not animate:
            self._camera = cam.copy()
            self.update()
            return
        self._anim_start = self._camera.copy()
        self._anim_target = cam.copy()
        self._anim_duration = max(0.1, duration)
        self._anim_elapsed = 0.0
        self._anim_timer.start()

    def _anim_tick(self):
        self._anim_elapsed += 0.016
        t = min(1.0, self._anim_elapsed / self._anim_duration)
        eased = t * t * (3 - 2 * t)
        self._camera = self._anim_start.interpolate(self._anim_target, eased)
        self.update()
        if t >= 1.0:
            self._anim_timer.stop()

    def set_texture_image(self, image):
        self.makeCurrent()
        if self._tex_id:
            GL.glDeleteTextures(1, [self._tex_id])
        self._tex_id = self._upload_tex(image)
        self.doneCurrent()
        self.update()

    def set_forecast_texture(self, image):
        self.makeCurrent()
        if self._fcast_tex_id:
            GL.glDeleteTextures(1, [self._fcast_tex_id])
        self._fcast_tex_id = self._upload_tex(image)
        self.doneCurrent()
        self.update()

    def set_cloud_texture(self, image):
        self.makeCurrent()
        if self._cloud_tex_id:
            GL.glDeleteTextures(1, [self._cloud_tex_id])
        self._cloud_tex_id = self._upload_tex(image)
        self.doneCurrent()
        self.update()

    def set_forecast_opacity(self, opacity: float):
        self._forecast_opacity = max(0.0, min(1.0, opacity))
        if self._ready:
            self.makeCurrent()
            GL.glUseProgram(self._prog)
            GL.glUniform1f(GL.glGetUniformLocation(self._prog, "uFcastOpacity"), self._forecast_opacity)
            GL.glUseProgram(0)
            self.doneCurrent()
        self.update()

    def set_sun_direction(self, direction: QVector3D):
        self._sun_dir = direction
        self.update()

    def set_sun_azel(self, azimuth_deg: float, elevation_deg: float):
        az = math.radians(azimuth_deg)
        el = math.radians(elevation_deg)
        x = math.cos(el) * math.sin(az)
        y = math.sin(el)
        z = -math.cos(el) * math.cos(az)
        self._sun_dir = QVector3D(x, y, z)
        self.update()

    def set_layer_visible(self, layer: str, visible: bool):
        attr = f"_show_{layer}"
        if not hasattr(self, attr):
            return
        setattr(self, attr, visible)
        if self._ready and layer in ("satellite", "forecast", "clouds", "topo_land", "topo_water"):
            u_name = {"satellite": "uShowSat", "forecast": "uShowFcast", "clouds": "uShowCloud",
                      "topo_land": "uShowTopoLand", "topo_water": "uShowTopoWater"}[layer]
            self.makeCurrent()
            GL.glUseProgram(self._prog)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, u_name), 1 if visible else 0)
            GL.glUseProgram(0)
            self.doneCurrent()
        self.update()

    def set_geos_bounds(self, min_lon: float, max_lon: float, min_lat: float, max_lat: float):
        pass

    @property
    def layer_order(self):
        return list(self._layer_order)

    def move_layer_up(self, name: str):
        if name in self._layer_order:
            i = self._layer_order.index(name)
            if i > 0:
                self._layer_order[i], self._layer_order[i-1] = self._layer_order[i-1], self._layer_order[i]
                self.orderChanged.emit(list(self._layer_order))
                self.update()

    def move_layer_down(self, name: str):
        if name in self._layer_order:
            i = self._layer_order.index(name)
            if i < len(self._layer_order) - 1:
                self._layer_order[i], self._layer_order[i+1] = self._layer_order[i+1], self._layer_order[i]
                self.orderChanged.emit(list(self._layer_order))
                self.update()

    def reset_view(self):
        self.set_camera(CameraState(lat=14.6, lon=121.0, zoom=1.0, heading=0.0, pitch=15.0), animate=True)

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
            self._atmos_prog = _link(_compile(GL.GL_VERTEX_SHADER, _ATMOS_VS),
                                     _compile(GL.GL_FRAGMENT_SHADER, _ATMOS_FS))
            self._line_prog = _link(_compile(GL.GL_VERTEX_SHADER, _LINE_VS),
                                    _compile(GL.GL_FRAGMENT_SHADER, _LINE_FS))
            self._part_prog = _link(_compile(GL.GL_VERTEX_SHADER, _PART_VS),
                                    _compile(GL.GL_FRAGMENT_SHADER, _PART_FS))

            self._build_sphere_vao()
            self._build_atmos_vao()
            self._build_grid_vaos()
            self._build_coast_vaos()
            self._init_particles()

            # Mark ready so _upload_tex works
            self._ready = True

            # Base texture
            tex_img = _generate_earth_texture()
            self._tex_id = self._upload_tex(tex_img)

            # Forecast texture (blank initially)
            blank = QImage(2, 2, QImage.Format_RGBA8888)
            blank.fill(QColor(0, 0, 0, 0))
            self._fcast_tex_id = self._upload_tex(blank)
            self._cloud_tex_id = self._upload_tex(blank)

            # Heightmap
            heightmap_path = os.path.join(os.path.dirname(__file__), "..", "assets", "heightmap.png")
            self._use_heightmap = False
            self._heightmap_tex_id = 0
            if not os.path.exists(heightmap_path):
                try:
                    import PIL.Image as PILImage
                    os.makedirs(os.path.dirname(heightmap_path), exist_ok=True)
                    hm_w, hm_h = 1024, 512
                    xs = np.arange(hm_w, dtype=np.float32) / hm_w * 4 * np.pi
                    ys = np.arange(hm_h, dtype=np.float32) / hm_h * 2 * np.pi
                    xx, yy = np.meshgrid(xs, ys)
                    hm_arr = np.zeros((hm_h, hm_w), dtype=np.float32)
                    for oct in range(8):
                        f = 1.8 ** oct
                        a = 0.35 ** oct
                        hm_arr += a * np.sin(xx * f * 0.7 + yy * f * 0.5)
                    hm_arr = (hm_arr - hm_arr.min()) / (hm_arr.max() - hm_arr.min())
                    hm_arr = (hm_arr * 255).astype(np.uint8)
                    PILImage.fromarray(hm_arr, mode="L").save(heightmap_path)
                except Exception:
                    pass
            if os.path.exists(heightmap_path):
                hm_img = QImage(heightmap_path)
                if not hm_img.isNull():
                    self._heightmap_tex_id = self._upload_tex(hm_img)
                    self._use_heightmap = True

            # Shader uniforms
            GL.glUseProgram(self._prog)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uSatTex"), 0)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uHeightmapTex"), 1)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uFcastTex"), 2)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uCloudTex"), 3)
            GL.glUniform1f(GL.glGetUniformLocation(self._prog, "uHeightScale"), 0.025)
            GL.glUniform1f(GL.glGetUniformLocation(self._prog, "uAmbientIntensity"), 0.15)
            GL.glUniform1f(GL.glGetUniformLocation(self._prog, "uDiffuseIntensity"), 0.85)
            GL.glUniform1f(GL.glGetUniformLocation(self._prog, "uCloudOpacity"), 0.6)
            GL.glUniform1f(GL.glGetUniformLocation(self._prog, "uFcastOpacity"), 0.0)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uUseHeightmap"),
                           1 if self._use_heightmap else 0)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uShowSat"), 1)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uShowFcast"), 1)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uShowCloud"), 1)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uShowTopoLand"), 0)
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "uShowTopoWater"), 0)
            GL.glUseProgram(0)

            self._sun_dir = QVector3D(0.5, -0.7, -0.4)
            self._sun_dir.normalize()
            self._time = 0.0
            self._forecast_opacity = 0.0

            self.ready.emit()
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._ready = False

    def _build_sphere_vao(self):
        verts, norms, uvs, idx = _build_sphere_mesh(48, 72)
        self._sphere_count = len(idx)
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)

        vbo_pos = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo_pos)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(0)

        vbo_norm = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo_norm)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, norms.nbytes, norms, GL.GL_STATIC_DRAW)
        GL.glVertexAttribPointer(1, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(1)

        vbo_uv = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo_uv)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, uvs.nbytes, uvs, GL.GL_STATIC_DRAW)
        GL.glVertexAttribPointer(2, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(2)

        ebo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ebo)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
        GL.glBindVertexArray(0)
        self._sphere_vao = vao

    def _build_atmos_vao(self):
        verts, _, _, idx = _build_sphere_mesh(32, 48, 1.0)
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts, GL.GL_STATIC_DRAW)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(0)
        ebo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ebo)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, idx.nbytes, idx, GL.GL_STATIC_DRAW)
        GL.glBindVertexArray(0)
        self._atmos_vao = vao
        self._atmos_count = len(idx)

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

    def _init_particles(self):
        n = self._particle_count
        np.random.seed(42)
        lats = np.random.uniform(-70, 70, n).astype(np.float32)
        lons = np.random.uniform(-180, 180, n).astype(np.float32)
        self._particle_positions = np.column_stack([lats, lons])
        self._particle_velocities = np.zeros((n, 2), dtype=np.float32)

        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        vbo = GL.glGenBuffers(1)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        empty = np.zeros((n, 3), dtype=np.float32)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, empty.nbytes, empty, GL.GL_DYNAMIC_DRAW)
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, GL.GL_FALSE, 0, None)
        GL.glEnableVertexAttribArray(0)
        GL.glBindVertexArray(0)
        self._particle_vao = vao
        self._particle_vbo = vbo

    def set_wind_data(self, u_data: Optional[np.ndarray], v_data: Optional[np.ndarray]):
        self._wind_u_data = u_data
        self._wind_v_data = v_data
        self._wind_loaded = u_data is not None and v_data is not None

    def set_wind_particles_visible(self, visible: bool):
        self._show_wind_particles = visible
        self.update()

    def _advect_particles(self):
        if not self._show_wind_particles or not self._wind_loaded:
            return
        lats = self._particle_positions[:, 0]
        lons = self._particle_positions[:, 1]
        h, w = self._wind_u_data.shape
        rows = (90.0 - lats) / 180.0 * (h - 1)
        cols = (lons + 180.0) / 360.0 * (w - 1)
        r0 = np.floor(rows).astype(np.int32)
        r1 = np.minimum(r0 + 1, h - 1)
        fr = rows - r0
        c0 = np.floor(cols).astype(np.int32) % w
        c1 = (c0 + 1) % w
        fc = cols - np.floor(cols)
        u = (self._wind_u_data[r0, c0] * (1 - fr) * (1 - fc) +
             self._wind_u_data[r1, c0] * fr * (1 - fc) +
             self._wind_u_data[r0, c1] * (1 - fr) * fc +
             self._wind_u_data[r1, c1] * fr * fc)
        v = (self._wind_v_data[r0, c0] * (1 - fr) * (1 - fc) +
             self._wind_v_data[r1, c0] * fr * (1 - fc) +
             self._wind_v_data[r0, c1] * (1 - fr) * fc +
             self._wind_v_data[r1, c1] * fr * fc)
        dt = 0.5
        dlat = v * dt * 0.005
        dlon = u * dt * 0.005 / np.maximum(np.cos(np.radians(lats)), 0.1)
        lats += dlat
        lons += dlon
        mask = (lats > 85) | (lats < -85)
        n_bad = int(np.sum(mask))
        if n_bad:
            lats[mask] = np.random.uniform(-70, 70, n_bad)
            lons[mask] = np.random.uniform(-180, 180, n_bad)
        lons[:] = ((lons + 180) % 360) - 180

    def _render_particles(self):
        if not self._show_wind_particles or not self._wind_loaded:
            return
        lat_r = np.radians(self._particle_positions[:, 0]).astype(np.float32)
        lon_r = np.radians(self._particle_positions[:, 1]).astype(np.float32)
        r = 1.005
        x = r * np.cos(lat_r) * np.cos(lon_r)
        y = r * np.sin(lat_r)
        z = -r * np.cos(lat_r) * np.sin(lon_r)
        verts = np.column_stack([x, y, z])

        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._particle_vbo)
        GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, verts.nbytes, verts)

        w, h = self.width(), self.height()
        aspect = w / h if h > 0 else 1.0
        zoom = self._camera.zoom
        proj = QMatrix4x4()
        proj.perspective(45.0 / zoom, aspect, 0.1, 10.0)
        view = QMatrix4x4()
        view.translate(0, 0, -2.5)
        rot = QMatrix4x4()
        rot.rotate(self._camera.to_quaternion())
        mvp = proj * view * rot

        GL.glUseProgram(self._part_prog)
        mvp_f = np.array(mvp.data(), dtype=np.float32)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(self._part_prog, "uMVP"),
                              1, GL.GL_FALSE, mvp_f)
        ps = max(2.0, 5.0 / zoom)
        GL.glUniform1f(GL.glGetUniformLocation(self._part_prog, "uPointSize"), ps)
        GL.glBindVertexArray(self._particle_vao)
        GL.glDrawArrays(GL.GL_POINTS, 0, self._particle_count)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)

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
            zoom = self._camera.zoom

            proj = QMatrix4x4()
            proj.perspective(45.0 / zoom, aspect, 0.1, 10.0)
            view = QMatrix4x4()
            view.translate(0, 0, -2.5)
            rot = QMatrix4x4()
            rot.rotate(self._camera.to_quaternion())
            modelView = view * rot
            normalMatrix = modelView.normalMatrix()

            mvp = proj * modelView
            mvp_f = np.array(mvp.data(), dtype=np.float32)
            mv_f = np.array(modelView.data(), dtype=np.float32)
            proj_f = np.array(proj.data(), dtype=np.float32)
            nm_f = np.array(normalMatrix.data(), dtype=np.float32)

            self._time += 0.016
            self._advect_particles()

            # Set sphere program uniforms once (reused if sphere renders)
            sphere_uniforms_set = False
            rendered_blocks = set()

            for layer_name in self._layer_order:
                if layer_name in rendered_blocks:
                    continue
                if layer_name in ("satellite", "forecast", "clouds") and "sphere" not in rendered_blocks:
                    # ── Sphere (satellite / forecast / clouds) ──
                    GL.glUseProgram(self._prog)
                    GL.glUniformMatrix4fv(GL.glGetUniformLocation(self._prog, "uModelView"),
                                          1, GL.GL_FALSE, mv_f)
                    GL.glUniformMatrix4fv(GL.glGetUniformLocation(self._prog, "uProjection"),
                                          1, GL.GL_FALSE, proj_f)
                    GL.glUniformMatrix3fv(GL.glGetUniformLocation(self._prog, "uNormalMatrix"),
                                          1, GL.GL_FALSE, nm_f)
                    GL.glUniform1f(GL.glGetUniformLocation(self._prog, "uTime"), self._time)

                    light_dir_view = modelView.mapVector(self._sun_dir)
                    if light_dir_view.length() > 0:
                        light_dir_view.normalize()
                    else:
                        light_dir_view = QVector3D(0.0, 0.0, -1.0)
                    GL.glUniform3f(GL.glGetUniformLocation(self._prog, "uLightDir"),
                                   light_dir_view.x(), light_dir_view.y(), light_dir_view.z())
                    GL.glActiveTexture(GL.GL_TEXTURE0)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex_id if self._tex_id else 0)
                    GL.glActiveTexture(GL.GL_TEXTURE1)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, self._heightmap_tex_id if self._heightmap_tex_id else 0)
                    GL.glActiveTexture(GL.GL_TEXTURE2)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, self._fcast_tex_id if self._fcast_tex_id else 0)
                    GL.glActiveTexture(GL.GL_TEXTURE3)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, self._cloud_tex_id if self._cloud_tex_id else 0)

                    GL.glBindVertexArray(self._sphere_vao)
                    GL.glDrawElements(GL.GL_TRIANGLES, self._sphere_count, GL.GL_UNSIGNED_INT, None)
                    GL.glBindVertexArray(0)
                    GL.glUseProgram(0)
                    rendered_blocks.add("sphere")
                    rendered_blocks.update(["satellite", "forecast", "clouds"])
                elif layer_name == "atmosphere" and self._show_atmosphere:
                    # ── Atmosphere glow ──
                    GL.glUseProgram(self._atmos_prog)
                    amvp_loc = GL.glGetUniformLocation(self._atmos_prog, "uMVP")
                    acol_loc = GL.glGetUniformLocation(self._atmos_prog, "uColor")
                    arad_loc = GL.glGetUniformLocation(self._atmos_prog, "uRadius")
                    acam_loc = GL.glGetUniformLocation(self._atmos_prog, "uCameraPos")
                    GL.glUniformMatrix4fv(amvp_loc, 1, GL.GL_FALSE, mvp_f)
                    GL.glUniform1f(arad_loc, 1.035)
                    GL.glUniform4f(acol_loc, 0.3, 0.6, 1.0, 0.4)
                    inv_rot, _ = rot.inverted()
                    cam_obj = inv_rot.map(QVector3D(0.0, 0.0, -2.5))
                    GL.glUniform3f(acam_loc, cam_obj.x(), cam_obj.y(), cam_obj.z())
                    GL.glBindVertexArray(self._atmos_vao)
                    GL.glDrawElements(GL.GL_TRIANGLES, self._atmos_count, GL.GL_UNSIGNED_INT, None)
                    GL.glBindVertexArray(0)
                    GL.glUseProgram(0)
                    rendered_blocks.add("atmosphere")
                elif layer_name == "grid" and self._show_grid:
                    # ── Grid lines ──
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
                    GL.glUseProgram(0)
                    rendered_blocks.add("grid")
                elif layer_name == "coastlines" and self._show_coastlines:
                    # ── Coastlines ──
                    GL.glUseProgram(self._line_prog)
                    mloc = GL.glGetUniformLocation(self._line_prog, "uMVP")
                    cloc = GL.glGetUniformLocation(self._line_prog, "uColor")
                    GL.glUniformMatrix4fv(mloc, 1, GL.GL_FALSE, mvp_f)
                    GL.glLineWidth(2.5)
                    GL.glUniform4f(cloc, 0.8, 0.95, 0.8, 1.0)
                    for vao, cnt, _ in self._coast_vaos:
                        GL.glBindVertexArray(vao)
                        GL.glDrawArrays(GL.GL_LINE_STRIP, 0, cnt)
                        GL.glBindVertexArray(0)
                    GL.glUseProgram(0)
                    rendered_blocks.add("coastlines")
            # ── Wind particles ──
            if self._show_wind_particles and self._wind_loaded:
                self._render_particles()

            GL.glLineWidth(1.0)
        except Exception as e:
            import traceback
            traceback.print_exc()
            GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

    def _upload_tex(self, image):
        if not self._ready or image is None or image.isNull():
            return 0
        img = image.convertToFormat(QImage.Format_RGBA8888)
        if img.isNull():
            return 0
        w, h = img.width(), img.height()
        if w < 1 or h < 1:
            return 0
        try:
            data = img.bits().tobytes()
            arr = np.frombuffer(data, dtype=np.uint8).reshape(h, w, 4).copy()
        except Exception:
            return 0
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, w, h, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, arr)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return tex

    # ── interactions ────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._panning = True
            self._last_mouse = event.pos()
            self._press_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
        elif event.button() == Qt.RightButton:
            self._rotating = True
            self._last_mouse = event.pos()
            self._press_pos = event.pos()
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._panning = False
            self._last_mouse = None
            self._press_pos = None
            self.setCursor(Qt.ArrowCursor)
        elif event.button() == Qt.RightButton:
            if self._rotating and self._press_pos is not None:
                dist = (event.pos() - self._press_pos).manhattanLength()
                if dist < 5:
                    self._show_cm(self._press_pos)
            self._rotating = False
            self._last_mouse = None
            self._press_pos = None
        event.accept()

    def mouseMoveEvent(self, event):
        if self._last_mouse is None:
            return
        dx = event.pos().x() - self._last_mouse.x()
        dy = event.pos().y() - self._last_mouse.y()
        if self._panning:
            self._camera.lon -= dx * 0.3
            self._camera.lat += dy * 0.3
            self._camera.lat = max(-90.0, min(90.0, self._camera.lat))
        elif self._rotating:
            self._camera.heading += dx * 0.5
            self._camera.pitch += dy * 0.3
            self._camera.pitch = max(-90.0, min(90.0, self._camera.pitch))
        self._last_mouse = event.pos()
        self.cameraChanged.emit(self._camera)
        self.update()

    def wheelEvent(self, event):
        d = event.angleDelta().y()
        self._camera.zoom *= 1.1 if d > 0 else 0.9
        self._camera.zoom = max(0.2, min(10.0, self._camera.zoom))
        self.zoomChanged.emit(self._camera.zoom)
        self.cameraChanged.emit(self._camera)
        self.update()

    def _show_cm(self, pos):
        m = QMenu(self)
        m.setStyleSheet("QMenu{background:#1f1f1f;color:#ddd;border:1px solid #444}"
                        "QMenu::item:selected{background:#4c1d95;color:#fff}"
                        "QMenu::item{padding:4px 20px}")
        a = m.addAction("Reset View")
        a2 = m.addAction("Capture Camera as Keyframe")
        action = m.exec(self.mapToGlobal(pos))
        if action == a:
            self.reset_view()
        elif action == a2:
            self.captureKeyframeRequested.emit()

    def grab_frame_rgb(self, width: int, height: int) -> Optional[bytes]:
        old_size = self.size()
        self.resize(width, height)
        self.makeCurrent()
        self.resizeGL(width, height)
        self.paintGL()
        data = GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        self.resize(old_size)
        self.doneCurrent()
        if data:
            arr = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            arr = np.flipud(arr)
            return arr.tobytes()
        return None

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
        if self._cloud_tex_id:
            GL.glDeleteTextures(1, [self._cloud_tex_id])
        if self._heightmap_tex_id:
            GL.glDeleteTextures(1, [self._heightmap_tex_id])
        if self._prog:
            GL.glDeleteProgram(self._prog)
        if self._atmos_prog:
            GL.glDeleteProgram(self._atmos_prog)
        if self._line_prog:
            GL.glDeleteProgram(self._line_prog)
        if self._part_prog:
            GL.glDeleteProgram(self._part_prog)
        if self._particle_vao:
            GL.glDeleteVertexArrays(1, [self._particle_vao])
        if self._particle_vbo:
            GL.glDeleteBuffers(1, [self._particle_vbo])
        self.doneCurrent()

    def closeEvent(self, event):
        self._anim_timer.stop()
        self.cleanup()
        super().closeEvent(event)
