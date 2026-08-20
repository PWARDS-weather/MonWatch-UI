# =============================================================================
# glsl_proj.py — GPU reprojection shader generation for MonWatch-UI Cyclone V3
# -----------------------------------------------------------------------------
# Ports the PROJ.4 projection math used by SIFT (uwsift/view/transform.py) into
# self-contained GLSL snippets. Only the projections this application needs are
# implemented:
#
#   * GEOS   — geostationary satellite "full disk" (inverse: disk x/y -> lon/lat)
#   * eqc    — equirectangular / Plate Carree in metric units
#   * longlat — naive lon/lat (Plate Carree) in degrees
#
# The vertex shader chain is the exact trick that makes SIFT switch from "disk"
# to "eqc" instantly: the pixel data is never resampled, instead every mesh
# vertex is mapped  source-projection-coords -> lon/lat -> target-projection
# on the GPU at draw time.
#
# Copyright (C) 2025-2026 PWARDS-weather
# Licensed under GPLv3, see LICENSE.
# =============================================================================

from __future__ import annotations

import math

# -----------------------------------------------------------------------------
# Projection parameter helpers (ported from uwsift/view/transform.py)
# -----------------------------------------------------------------------------

_DEFAULT_SAT_LON = 140.7
_WGS84_A = 6378137.0
_WGS84_B = 6356752.3142
_WGS84_RF = 298.257223563


def crs_to_geos_params(crs) -> dict:
    """Extract GEOS projection constants from a pyproj CRS (or proj4 string).

    Returns a flat dict of floats used to format the GLSL shader:
    h, a, b, e, es, lon_0, sweep, flip_axis, radius_g, radius_g_1, C.
    """
    params = _crs_to_dict(crs)

    lon_0 = params.get("lon_0", params.get("longitude_of_projection_origin", _DEFAULT_SAT_LON))
    h = params.get("h", params.get("perspective_point_height", 35785863.0))
    # a / b / rf resolution order: explicit proj4 keys first, then WKT keys
    a = params.get("a", None)
    if a is None:
        a = params.get("semi_major_axis", _WGS84_A)
    b = params.get("b", None)
    if b is None:
        b = params.get("semi_minor_axis", _WGS84_B)
    rf = params.get("rf", params.get("inverse_flattening", None))
    if b is None and rf:
        b = a * (1.0 - 1.0 / rf)

    a = float(a)
    b = float(b)
    h = float(h)
    lon_0 = float(lon_0)

    es = 1.0 - (b * b) / (a * a)
    e = math.sqrt(max(0.0, es))

    # Honor the source's sweep axis (HSD ads.json, netCDF, proj4). GOES,
    # Himawari-8/9 and GK-2A document sweep='x'; Meteosat/MSG sweep='y'.
    sweep = params.get("sweep", "y")
    sweep = str(sweep).lower().strip() in ("x", "true", "1")
    flip_axis = 1.0 if sweep else 0.0

    radius_g_1 = h / a
    radius_g = 1.0 + radius_g_1
    C = radius_g * radius_g - 1.0

    sphere = (abs(a - b) < 1e-9)
    if sphere:
        radius_p = radius_p2 = radius_p_inv2 = 1.0
    else:
        radius_p = math.sqrt(1.0 - es)
        radius_p2 = 1.0 - es
        radius_p_inv2 = 1.0 / radius_p2

    return {
        "h": h,
        "a": a,
        "b": b,
        "e": e,
        "es": es,
        "lon_0": lon_0,
        "sweep_x": float(sweep),
        "flip_axis": flip_axis,
        "radius_g": radius_g,
        "radius_g_1": radius_g_1,
        "C": C,
        "radius_p": radius_p,
        "radius_p2": radius_p2,
        "radius_p_inv2": radius_p_inv2,
        "sphere": sphere,
    }


def default_geos_params() -> dict:
    """Sane defaults (Himawari-like) used until a real source CRS is set."""
    return crs_to_geos_params({"proj": "geos", "lon_0": _DEFAULT_SAT_LON, "h": 35785863.0, "sweep": "x"})


def _crs_to_dict(crs):
    """Return a flat dict from a CRS object / proj4 string / dict."""
    if hasattr(crs, "to_dict"):
        try:
            return crs.to_dict()
        except Exception:
            pass
    if isinstance(crs, str):
        d = {}
        for tok in crs.split():
            if tok.startswith("+"):
                tok = tok[1:]
            if "=" in tok:
                k, v = tok.split("=", 1)
                d[k] = v
            else:
                d[tok] = "true"
        return d
    if isinstance(crs, dict):
        return dict(crs)
    return {}


def eqc_constants(lat_ts: float = 0.0, lat_0: float = 0.0) -> dict:
    """Equirectangular constants (rc = cos(lat_ts), phi0 = radians(lat_0))."""
    return {
        "rc": math.cos(math.radians(lat_ts)),
        "phi0": math.radians(lat_0),
    }


# -----------------------------------------------------------------------------
# GLSL projection functions
# -----------------------------------------------------------------------------

_MATH_HEADER = """
const float M_PI = 3.14159265358979323846;
const float M_HALFPI = 1.57079632679489661923;
"""


def glsl_geos_imap(sphere: bool) -> str:
    """Inverse GEOS projection: disk x/y (meters) -> lon/lat (degrees).

    Returns vec4(lon_deg, lat_deg, pos.z, pos.w). Points outside the visible
    disk return (inf, inf) which the caller must treat as invalid.
    """
    if sphere:
        return """
vec4 geos_imap(vec4 pos) {
    float a = {a};
    float x = pos.x / a;
    float y = pos.y / a;
    float Vx = -1.0;
    float Vy, Vz;
    if ({flip} > 0.5) {
        Vz = tan(y / ({RG} - 1.0));
        Vy = tan(x / ({RG} - 1.0)) * sqrt(1.0 + Vz * Vz);
    } else {
        Vy = tan(x / ({RG} - 1.0));
        Vz = tan(y / ({RG} - 1.0)) * sqrt(1.0 + Vy * Vy);
    }
    float a_ = Vy * Vy + Vz * Vz + Vx * Vx;
    float b = 2.0 * {RG} * Vx;
    float det = b * b - 4.0 * a_ * {C};
    if (det < 0.0) {
        return vec4(1.0 / 0.0, 1.0 / 0.0, pos.z, pos.w);
    }
    float k = (-b - sqrt(det)) / (2.0 * a_);
    Vx = {RG} + k * Vx;
    Vy *= k;
    Vz *= k;
    float lambda = atan(Vy, Vx);
    float phi = atan(Vz * cos(lambda) / Vx);
    return vec4(degrees(lambda) + {LON0}, degrees(phi), pos.z, pos.w);
}
"""
    else:
        return """
vec4 geos_imap(vec4 pos) {
    float a = {a};
    float x = pos.x / a;
    float y = pos.y / a;
    float Vx = -1.0;
    float Vy, Vz;
    if ({flip} > 0.5) {
        Vz = tan(y / ({RG} - 1.0));
        Vy = tan(x / ({RG} - 1.0)) * sqrt(1.0 + Vz * Vz);
    } else {
        Vy = tan(x / ({RG} - 1.0));
        Vz = tan(y / ({RG} - 1.0)) * sqrt(1.0 + Vy * Vy);
    }
    float Vz_p = Vz / {RP};
    float a_ = Vy * Vy + Vz_p * Vz_p + Vx * Vx;
    float b = 2.0 * {RG} * Vx;
    float det = b * b - 4.0 * a_ * {C};
    if (det < 0.0) {
        return vec4(1.0 / 0.0, 1.0 / 0.0, pos.z, pos.w);
    }
    float k = (-b - sqrt(det)) / (2.0 * a_);
    Vx = {RG} + k * Vx;
    Vy *= k;
    Vz *= k;
    float lambda = atan(Vy, Vx);
    float phi = atan(Vz * cos(lambda) / Vx);
    phi = atan({RP2} * tan(phi));
    return vec4(degrees(lambda) + {LON0}, degrees(phi), pos.z, pos.w);
}
"""


def glsl_eqc_map() -> str:
    """Forward eqc projection: lon/lat (degrees) -> x/y (meters).

    Correctly subtracts lon_0 so the disk is centred on the world origin
    (SIFT's version omits lon_0 and is documented as incomplete).
    """
    return """
vec2 eqc_map(vec4 lonlat) {
    float a = {a};
    float rc = {rc};
    float lambda = radians(lonlat.x - {LON0});
    float phi = radians(lonlat.y);
    float x = a * rc * lambda;
    float y = a * (phi - {phi0});
    return vec2(x, y);
}
"""


def glsl_longlat_map() -> str:
    """Plate Carree: world == lon/lat in degrees."""
    return """
vec2 longlat_map(vec4 lonlat) {
    return lonlat.xy;
}
"""


# -----------------------------------------------------------------------------
# Vertex shader builder
# -----------------------------------------------------------------------------

# mode enum, must match GLMapWidget.MODE_*
MODE_FULL_DISK = 0
MODE_EQUIRECTANGULAR = 1
MODE_PLATE_CARREE = 2


def _subst(template: str, fmt: dict) -> str:
    """Substitute {key} placeholders without touching other { } characters."""
    for k, v in fmt.items():
        if isinstance(v, float):
            v = repr(v)
        template = template.replace("{%s}" % k, str(v))
    return template


def build_vertex_shader(geos_params: dict, eqc_params: dict) -> str:
    """Build the complete vertex shader for a GEOS source.

    Inputs:
      attribute vec2 a_pos   – position in source GEOS meters
      attribute vec2 a_tex   – texture coordinate (0..1)
      uniform int    u_mode  – MODE_FULL_DISK / MODE_EQUIRECTANGULAR / MODE_PLATE_CARREE
      uniform mat4   u_cam   – world -> clip matrix

    Output:
      varying vec2 v_tex (and discards off-disk geometry via pos.w trick)
    """
    sphere = bool(geos_params.get("sphere", True))
    imap = glsl_geos_imap(sphere)

    g = dict(geos_params)
    fmt = {
        "a": g["a"],
        "flip": g["flip_axis"],
        "RG": g["radius_g"],
        "C": g["C"],
        "LON0": g["lon_0"],
        "RP": g.get("radius_p", 1.0),
        "RP2": g.get("radius_p2", 1.0),
        "rc": eqc_params.get("rc", 1.0),
        "phi0": eqc_params.get("phi0", 0.0),
    }
    imap = _subst(glsl_geos_imap(sphere), fmt)
    eqc = _subst(glsl_eqc_map(), fmt)
    longlat = glsl_longlat_map()

    return (_MATH_HEADER +
            "\nattribute vec2 a_pos;\nattribute vec2 a_tex;\nuniform int u_mode;\n"
            "uniform mat4 u_cam;\nuniform float u_pad;\nvarying vec2 v_tex;\n\n"
            + imap + "\n\n" + eqc + "\n\n" + longlat + "\n\n"
            + "void main() {\n"
            "    v_tex = a_tex;\n"
            "    vec4 lonlat = geos_imap(vec4(a_pos, 0.0, 1.0));\n"
            "    if (abs(lonlat.x) > 1e8 || abs(lonlat.y) > 1e8) {\n"
            "        // Not on the visible disk: push the vertex off-screen.\n"
            "        gl_Position = vec4(0.0, 0.0, 2.0, 1.0);\n"
            "        v_tex = vec2(-1000.0, -1000.0);\n"
            "        return;\n"
            "    }\n"
            "    vec2 world;\n"
            f"    if (u_mode == {MODE_FULL_DISK}) {{\n"
            "        world = a_pos;\n"
            f"    }} else if (u_mode == {MODE_EQUIRECTANGULAR}) {{\n"
            "        world = eqc_map(lonlat);\n"
            "    } else {\n"
            "        world = longlat_map(lonlat);\n"
            "    }\n"
            "    gl_Position = u_cam * vec4(world, 0.0, 1.0);\n"
            "}\n")


FRAGMENT_SHADER = """
uniform sampler2D u_tex;
varying vec2 v_tex;
uniform float u_alpha;

void main() {
    if (v_tex.x < 0.0 || v_tex.x > 1.0 || v_tex.y < 0.0 || v_tex.y > 1.0) {
        discard;
    }
    vec4 col = texture2D(u_tex, v_tex);
    col.a *= u_alpha;
    gl_FragColor = col;
}
"""


# -----------------------------------------------------------------------------
# CPU-side fallback / preview of the same chain (for tests & tooling)
# -----------------------------------------------------------------------------

def to_world_cpu(x_m: float, y_m: float, mode: int, geos_params: dict, eqc_params: dict) -> tuple:
    """Reproduce the shader chain with numpy/pyproj (used for tests)."""
    from pyproj import Proj

    lon_0 = geos_params["lon_0"]
    a = geos_params["a"]
    h = geos_params["h"]
    sweep_x = bool(geos_params.get("sweep_x"))

    p = Proj(proj="geos", lon_0=lon_0, h=h, a=a, b=geos_params["b"],
             sweep="x" if sweep_x else "y", units="m")
    lon, lat = p(x_m, y_m, inverse=True)
    if mode == MODE_FULL_DISK:
        return x_m, y_m
    if mode == MODE_EQUIRECTANGULAR:
        rc = eqc_params["rc"]
        x = a * rc * math.radians(lon - lon_0)
        y = a * (math.radians(lat) - eqc_params["phi0"])
        return x, y
    return lon, lat