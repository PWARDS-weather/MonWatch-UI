import numpy as np
import math
import os
from typing import Optional, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QStandardPaths, QSaveFile, QIODevice
from PySide6.QtGui import QImage

_HERBIE_CACHE: dict = {}
_HERBIE_RUN_INFO: dict = {}


def _temperature_color(temp_c: float) -> Tuple[int, int, int, int]:
    if temp_c < -20:
        return (80, 0, 120, 255)
    elif temp_c < -10:
        return (0, 0, 180, 255)
    elif temp_c < 0:
        return (0, 120, 200, 255)
    elif temp_c < 10:
        return (100, 180, 255, 255)
    elif temp_c < 20:
        return (100, 220, 100, 255)
    elif temp_c < 30:
        return (255, 200, 50, 255)
    elif temp_c < 40:
        return (255, 100, 0, 255)
    else:
        return (180, 0, 0, 255)


def _wind_color(speed: float) -> Tuple[int, int, int, int]:
    """Wind speed color palette (m/s)."""
    if speed < 2:
        return (100, 220, 100, 180)
    elif speed < 5:
        return (50, 200, 150, 200)
    elif speed < 10:
        return (50, 150, 220, 220)
    elif speed < 15:
        return (100, 100, 240, 230)
    elif speed < 20:
        return (200, 50, 200, 240)
    elif speed < 30:
        return (240, 50, 100, 240)
    else:
        return (180, 40, 40, 255)


def _precip_color(mm: float) -> Tuple[int, int, int, int]:
    if mm < 0.1:
        return (0, 0, 0, 0)
    elif mm < 1:
        return (100, 200, 100, 180)
    elif mm < 5:
        return (50, 150, 50, 200)
    elif mm < 10:
        return (0, 100, 200, 220)
    elif mm < 20:
        return (0, 50, 200, 240)
    elif mm < 50:
        return (200, 0, 0, 240)
    else:
        return (150, 0, 0, 255)


def _fake_gradient(width=2048, height=1024) -> np.ndarray:
    lat = np.deg2rad(90.0 - np.arange(height) / height * 180.0)[:, None]
    lon = np.deg2rad(np.arange(width) / width * 360.0)[None, :]
    temp = 30.0 * np.cos(lat) - 10.0 + 5.0 * np.sin(lon * 0.5)
    return _rasterize_to_equirect(temp, height, width, _temperature_color)


def _fetch_gfs_openmeteo() -> Optional[np.ndarray]:
    """Fetch GFS 2m temperature grid via Open-Meteo (free, no key needed, ~10 deg grid)."""
    for attempt in range(3):
        try:
            import urllib.request
            import json
            import time as _time
            lats = np.arange(-80, 81, 10.0)
            lons = np.arange(-180, 180, 10.0)
            lon_g, lat_g = np.meshgrid(lons, lats)
            lat_pairs = lat_g.ravel()
            lon_pairs = lon_g.ravel()
            lat_str = ",".join(f"{x:.1f}" for x in lat_pairs)
            lon_str = ",".join(f"{x:.1f}" for x in lon_pairs)
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat_str}&longitude={lon_str}"
                f"&hourly=temperature_2m&forecast_days=1"
                f"&timezone=UTC"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "MonWatch/3.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode())
            if isinstance(data, dict) and data.get("error"):
                return None
            temps = [d["hourly"]["temperature_2m"][0] for d in data]
            grid = np.array(temps, dtype=np.float32).reshape(len(lats), len(lons))
            grid = np.roll(grid, shift=grid.shape[1] // 2, axis=1)
            return grid
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                _time.sleep(5)
                continue
            return None
        except Exception:
            return None
    return None


def _fetch_herbie(source="gfs", model="0p25", variable="temp", fxx=0) -> Optional[np.ndarray]:
    """Fetch 2m temperature or 10m wind grid via Herbie.
    Returns float32 array shape (nlat, nlon), values in °C or m/s, lon 0→360°.
    For wind, returns speed = sqrt(u²+v²).
    For 'wind_uv', returns tuple (u_grid, v_grid).
    Results are cached in _HERBIE_CACHE.  _HERBIE_RUN_INFO is set to
    {"run": run_dt_str, "max_fxx": max_fxx} on success.
    """
    global _HERBIE_CACHE, _HERBIE_RUN_INFO
    try:
        from herbie import Herbie
        import warnings, logging
        warnings.filterwarnings("ignore")
        logging.getLogger("herbie").setLevel(logging.ERROR)
        from datetime import datetime, timedelta
        import itertools as _it

        var_map = {
            "gfs": ("gfs", {
                "temp": ("TMP:2 m above ground", "t2m"),
                "wind": ("UGRD:10 m above ground", "u10"),
                "wind_v": ("VGRD:10 m above ground", "v10"),
            }, 384),
            "ecmwf": ("ecmwf", {
                "temp": ("2t", "t2m"),
                "wind": ("10u", "u10"),
                "wind_v": ("10v", "v10"),
            }, 240),
        }
        if source not in var_map:
            return None

        herbie_model, var_map_inner, max_fxx = var_map[source]
        if variable == "wind_uv":
            keys = ["wind", "wind_v"]
        elif variable == "wind":
            keys = ["wind"]
        else:
            keys = ["temp"]

        if source == "gfs":
            product = "pgrb2.0p25" if model in ("0p25", "") else "pgrb2.0p50"
        else:
            product = "oper" if model in ("oper", "hres", "") else "ens"

        now = datetime.utcnow()
        for days_ago, hour in _it.product(range(3), [18, 12, 6, 0]):
            run_dt = (now - timedelta(days=days_ago)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )
            if run_dt > now:
                continue
            cache_key = f"{source}_{model}_{variable}_{run_dt.isoformat()}_{fxx}"
            if cache_key in _HERBIE_CACHE:
                val = _HERBIE_CACHE[cache_key]
                _HERBIE_RUN_INFO = {"run": run_dt.isoformat(), "max_fxx": max_fxx}
                return val
            try:
                H = Herbie(run_dt, model=herbie_model, fxx=fxx, product=product)
                results = {}
                for k in keys:
                    search_key, var_name = var_map_inner[k]
                    result = H.xarray(search_key)
                    if isinstance(result, list):
                        for d in result:
                            if var_name in d:
                                arr = d[var_name].values.astype(np.float32)
                                break
                        else:
                            arr = None
                    else:
                        arr = result[var_name].values.astype(np.float32)
                    if k == "temp":
                        arr = arr - 273.15
                    if arr is not None and source == "ecmwf":
                        arr = np.roll(arr, shift=arr.shape[1] // 2, axis=1)
                    results[k] = arr

                if variable == "wind_uv":
                    u, v = results.get("wind"), results.get("wind_v")
                    if u is None or v is None:
                        continue
                    _HERBIE_CACHE[cache_key] = (u, v)
                elif variable == "wind":
                    arr = results.get("wind")
                    if arr is None:
                        continue
                    v_arr = results.get("wind_v")
                    if v_arr is not None:
                        arr = np.sqrt(arr * arr + v_arr * v_arr)
                    _HERBIE_CACHE[cache_key] = arr
                else:
                    _HERBIE_CACHE[cache_key] = results.get("temp")
                _HERBIE_RUN_INFO = {"run": run_dt.isoformat(), "max_fxx": max_fxx}
                return _HERBIE_CACHE[cache_key]
            except Exception:
                continue
        return None
    except ImportError:
        return None
    except Exception:
        return None


def get_herbie_run_info() -> dict:
    """Return last successful run info dict: {"run": str, "max_fxx": int}."""
    return dict(_HERBIE_RUN_INFO)


def _rasterize_to_equirect(grid: np.ndarray, out_h: int, out_w: int,
                           color_fn: Callable) -> np.ndarray:
    gh, gw = grid.shape
    # Bilinear interpolation
    r_in = np.clip(np.arange(out_h) * (gh - 1) / max(out_h - 1, 1), 0, gh - 1)
    c_in = (np.arange(out_w) * gw / out_w + gw * 0.5) % gw
    r0 = np.floor(r_in).astype(np.int32)
    r1 = np.minimum(r0 + 1, gh - 1)
    fr = (r_in - r0).astype(np.float32)
    c0 = np.floor(c_in).astype(np.int32) % gw
    c1 = (c0 + 1) % gw
    fc = (c_in - c0).astype(np.float32)
    v00 = grid[r0[:, None], c0[None, :]]
    v10 = grid[r1[:, None], c0[None, :]]
    v01 = grid[r0[:, None], c1[None, :]]
    v11 = grid[r1[:, None], c1[None, :]]
    vals = (v00 * (1.0 - fr[:, None]) * (1.0 - fc[None, :]) +
            v10 * fr[:, None] * (1.0 - fc[None, :]) +
            v01 * (1.0 - fr[:, None]) * fc[None, :] +
            v11 * fr[:, None] * fc[None, :])
    nan_mask = np.isnan(vals)
    vec_color = np.vectorize(color_fn, otypes=[np.uint8] * 4)
    channels = vec_color(np.where(nan_mask, 0.0, vals))
    rgba = np.stack(channels, axis=-1)
    rgba[nan_mask] = (0, 0, 0, 0)
    return rgba


def clear_herbie_cache():
    _HERBIE_CACHE.clear()
    _HERBIE_RUN_INFO.clear()


def export_forecast_texture(source="openmeteo", model="", api_key="", width=2048, height=1024, variable="temp", fxx=0):
    temp_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
    out_path = os.path.join(temp_dir, "broadcast_forecast_temp.png")
    final_path = os.path.join(temp_dir, "broadcast_forecast.png")

    grid = None
    color_fn = _temperature_color if variable == "temp" else _wind_color
    herbie_var = "temp" if variable == "temp" else "wind"
    if source == "gfs":
        grid = _fetch_herbie(source="gfs", model=model or "0p25", variable=herbie_var, fxx=fxx)
    elif source == "ecmwf":
        grid = _fetch_herbie(source="ecmwf", model=model or "oper", variable=herbie_var, fxx=fxx)
    if grid is None and variable == "temp":
        grid = _fetch_gfs_openmeteo()
    if grid is not None:
        rgba = _rasterize_to_equirect(grid, height, width, color_fn)
    else:
        rgba = _fake_gradient(width, height)

    h, w = rgba.shape[:2]
    qimg = QImage(rgba.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
    sf = QSaveFile(out_path)
    if sf.open(QIODevice.WriteOnly):
        qimg.save(sf, "PNG")
        sf.commit()
    if os.path.exists(final_path):
        os.remove(final_path)
    os.rename(out_path, final_path)
    return final_path


def export_wind_field_textures(source="gfs", model="0p25", width=2048, height=1024, fxx=0):
    """Export U and V wind component PNGs for particle advection (8-bit encoded)."""
    temp_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
    result = _fetch_herbie(source=source, model=model, variable="wind_uv", fxx=fxx)
    if result is None:
        return
    u_grid, v_grid = result
    gh, gw = u_grid.shape
    out_u = np.zeros((height, width), dtype=np.float32)
    out_v = np.zeros((height, width), dtype=np.float32)
    r_frac = np.arange(height, dtype=np.float32) * (gh - 1) / max(height - 1, 1)
    c_frac = (np.arange(width, dtype=np.float32) * gw / width + gw * 0.5) % gw
    r0 = np.floor(r_frac).astype(np.int32)
    r1 = np.minimum(r0 + 1, gh - 1)
    rf = r_frac - r0
    c0 = np.floor(c_frac).astype(np.int32) % gw
    c1 = (c0 + 1) % gw
    cf = c_frac - c0
    u00 = u_grid[r0[:, None], c0[None, :]]
    u10 = u_grid[r1[:, None], c0[None, :]]
    u01 = u_grid[r0[:, None], c1[None, :]]
    u11 = u_grid[r1[:, None], c1[None, :]]
    out_u = (u00 * (1 - rf[:, None]) * (1 - cf[None, :]) +
             u10 * rf[:, None] * (1 - cf[None, :]) +
             u01 * (1 - rf[:, None]) * cf[None, :] +
             u11 * rf[:, None] * cf[None, :])
    v00 = v_grid[r0[:, None], c0[None, :]]
    v10 = v_grid[r1[:, None], c0[None, :]]
    v01 = v_grid[r0[:, None], c1[None, :]]
    v11 = v_grid[r1[:, None], c1[None, :]]
    out_v = (v00 * (1 - rf[:, None]) * (1 - cf[None, :]) +
             v10 * rf[:, None] * (1 - cf[None, :]) +
             v01 * (1 - rf[:, None]) * cf[None, :] +
             v11 * rf[:, None] * cf[None, :])

    def _save_8bit(arr, name):
        scaled = np.clip((arr + 50.0) * 255.0 / 100.0, 0, 255).astype(np.uint8)
        img = QImage(scaled.tobytes(), width, height, width, QImage.Format_Grayscale8)
        temp_path = os.path.join(temp_dir, name + "_temp.png")
        final_path = os.path.join(temp_dir, name + ".png")
        sf = QSaveFile(temp_path)
        if sf.open(QIODevice.WriteOnly):
            img.save(sf, "PNG")
            sf.commit()
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(temp_path, final_path)

    _save_8bit(out_u, "broadcast_wind_u")
    _save_8bit(out_v, "broadcast_wind_v")


def _fetch_openmeteo_grid(variable="temp") -> Optional[np.ndarray]:
    """Fetch gridded forecast from Open-Meteo (free, global, no key needed)."""
    try:
        import urllib.request, json
        lats = np.arange(-80, 81, 7.5)
        lons = np.arange(-180, 180, 7.5)
        lon_g, lat_g = np.meshgrid(lons, lats)
        lat_pairs = lat_g.ravel()
        lon_pairs = lon_g.ravel()
        lat_str = ",".join(f"{x:.2f}" for x in lat_pairs)
        lon_str = ",".join(f"{x:.2f}" for x in lon_pairs)
        param = "temperature_2m" if variable == "temp" else (
            "precipitation" if variable == "precip" else "wind_speed_10m")
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat_str}&longitude={lon_str}"
            f"&hourly={param}&forecast_days=1&timezone=UTC"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "MonWatch/3.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read().decode())
        if isinstance(data, dict) and data.get("error"):
            return None
        vals = [d["hourly"][param][0] for d in data]
        grid = np.array(vals, dtype=np.float32).reshape(len(lats), len(lons))
        grid = np.roll(grid, shift=grid.shape[1] // 2, axis=1)
        return grid
    except Exception:
        return None


def export_processed_forecast_texture(provider="windy", variable="temp", api_key=None,
                                       width=2048, height=1024):
    """Fetch processed forecast via Open-Meteo (free global grid) and export PNG.
    Provider selection (Windy/AccuWeather/OpenWeatherMap) is a label — all use
    Open-Meteo as the backend since it's the only practical free global gridded API.
    """
    temp_dir = QStandardPaths.writableLocation(QStandardPaths.TempLocation)
    out_path = os.path.join(temp_dir, "broadcast_forecast_temp.png")
    final_path = os.path.join(temp_dir, "broadcast_forecast.png")

    grid = _fetch_openmeteo_grid(variable=variable)

    if grid is not None:
        color_fn = _temperature_color if variable == "temp" else _wind_color
        rgba = _rasterize_to_equirect(grid, height, width, color_fn)
    else:
        rgba = _fake_gradient(width, height)

    h, w = rgba.shape[:2]
    qimg = QImage(rgba.tobytes(), w, h, w * 4, QImage.Format_RGBA8888)
    sf = QSaveFile(out_path)
    if sf.open(QIODevice.WriteOnly):
        qimg.save(sf, "PNG")
        sf.commit()
    if os.path.exists(final_path):
        os.remove(final_path)
    os.rename(out_path, final_path)
    return final_path
