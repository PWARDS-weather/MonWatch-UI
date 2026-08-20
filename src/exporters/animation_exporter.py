# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: exporters/animation_exporter.py
# Description: Animation export utilities with geospatial footer annotation for multi-frame sequences.
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

import os, numpy as np
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import subprocess, tempfile, shutil

_top_dir = Path(__file__).resolve().parent.parent.parent

_logo_cache = {}
_font_cache = {}
_footer_strip_cache = {}


def _cached_logo(logo_path: Path, footer_h: int):
    key = (str(logo_path), footer_h)
    if key not in _logo_cache:
        try:
            logo = Image.open(str(logo_path)).convert("RGBA")
            lh = footer_h - 8
            ratio = lh / logo.height
            logo = logo.resize((int(logo.width * ratio), lh), Image.LANCZOS)
            _logo_cache[key] = logo
        except Exception:
            _logo_cache[key] = None
    return _logo_cache[key]


def _cached_font(font_size: int):
    if font_size not in _font_cache:
        try:
            _font_cache[font_size] = ImageFont.truetype("consola.ttf", font_size)
        except Exception:
            try:
                _font_cache[font_size] = ImageFont.truetype("cour.ttf", font_size)
            except Exception:
                _font_cache[font_size] = ImageFont.load_default()
    return _font_cache[font_size]


def _render_footer_strip(w, footer_h, logo_path, font_size, info_text, brand_text,
                         info_position):
    strip_pil = Image.new("RGB", (w, footer_h), (255, 255, 255))
    draw = ImageDraw.Draw(strip_pil)

    font = _cached_font(font_size)
    try:
        th = draw.textbbox((0, 0), "Ag", font=font)[3]
    except Exception:
        th = font_size

    ty = (footer_h - th) // 2

    logo = _cached_logo(logo_path, footer_h) if logo_path else None
    logo_x = 6
    if logo is not None:
        ly = (footer_h - logo.height) // 2
        strip_pil.paste(logo, (logo_x, ly), logo)
        logo_x += logo.width + 10

    if info_position == "left":
        left_t, right_t = info_text, brand_text
    else:
        left_t, right_t = brand_text, info_text

    draw.text((logo_x + 1, ty + 1), left_t, fill=(255, 255, 255), font=font)
    draw.text((logo_x, ty), left_t, fill=(0, 0, 0), font=font)

    try:
        rtw = draw.textbbox((0, 0), right_t, font=font)[2]
    except Exception:
        try:
            rtw = draw.textlength(right_t, font=font)
        except Exception:
            rtw = len(right_t) * font_size
    rx = w - rtw - 8
    draw.text((rx + 1, ty + 1), right_t, fill=(255, 255, 255), font=font)
    draw.text((rx, ty), right_t, fill=(0, 0, 0), font=font)

    return np.array(strip_pil, dtype=np.uint8)


def _to_rgb(frame):
    ndim = frame.ndim
    if ndim == 2:
        rgb = np.stack([frame] * 3, axis=-1)
    else:
        last = frame.shape[-1]
        if last == 4:
            rgb = frame[..., :3]
        elif last == 3:
            rgb = frame
        elif last == 1:
            rgb = np.concatenate([frame] * 3, axis=-1)
        else:
            rgb = frame[..., :3]
    if rgb.dtype != np.uint8:
        rgb = rgb.astype(np.uint8)
    return rgb


def add_footer_to_frame(frame, sat_name, scan_type, dt_str, band_label,
                        footer_show=None, footer_size="small", text_size="normal",
                        footer_position="bottom", info_position="left",
                        latlon="",
                        info_text=None, brand_text=None):
    if info_text is None:
        fsh = footer_show or {}
        show_sat = fsh.get("satellite", True)
        show_dt = fsh.get("datetime", True)
        show_band = fsh.get("band", True)
        show_latlon = fsh.get("latlon", True)

        parts = [sat_name] if show_sat and sat_name else []
        if scan_type:
            parts.append(scan_type)
        if show_dt and dt_str:
            parts.append(f"{dt_str} UTC")
        if show_band and band_label:
            parts.append(band_label)
        if show_latlon and latlon:
            parts.append(latlon)
        info_text = " | ".join(parts)
    if brand_text is None:
        brand_text = "Made with MonWatch-UI"

    h_img, w_img = frame.shape[:2]
    size_mult = {"small": 1.0, "normal": 1.8, "big": 2.5}.get(footer_size, 1.0)
    footer_h = max(40, min(int(h_img * 0.04), 60))
    footer_h = int(footer_h * size_mult)

    fy = h_img if footer_position != "top" else 0

    tscale = {"small": 1.0, "normal": 1.3, "large": 1.7}.get(text_size, 1.0)
    font_size = max(8, int(footer_h * 0.35 * tscale))

    cache_key = (w_img, footer_h, font_size, info_text, brand_text, info_position)
    if cache_key not in _footer_strip_cache:
        logo_path = _top_dir / "public" / "images" / "Monwatch-LOGO.png"
        if not logo_path.exists():
            logo_path = None
        _footer_strip_cache[cache_key] = _render_footer_strip(
            w_img, footer_h, logo_path, font_size,
            info_text, brand_text, info_position
        )
    footer_strip = _footer_strip_cache[cache_key]

    rgb = _to_rgb(frame)
    out_h = h_img + footer_h
    result = np.empty((out_h, w_img, 3), dtype=np.uint8)
    if fy == 0:
        result[footer_h:] = rgb
        result[:footer_h] = footer_strip
    else:
        result[:h_img] = rgb
        result[h_img:] = footer_strip

    return result


def export_animation_direct(frames, output_path, fps=5,
                            with_footer=False, footer_data=None,
                            progress_callback=None):
    valid = [(i, f) for i, f in enumerate(frames) if f is not None]
    if not valid:
        raise ValueError("No valid frames to export")

    output_path = Path(output_path)
    ext = output_path.suffix.lower()
    h, w = valid[0][1].shape[:2]

    for idx, (orig_i, frame) in enumerate(valid):
        if with_footer and footer_data:
            fd = dict(footer_data)
            ft = fd.pop("frame_texts", None)
            if ft and orig_i in ft:
                fd["info_text"] = ft[orig_i]
            frame = add_footer_to_frame(frame, **fd)
        else:
            frame = _to_rgb(frame)
        valid[idx] = (orig_i, frame)

    h, w = valid[0][1].shape[:2]

    # Pad to even dimensions for codec compatibility (h264/AVC requires even dimensions)
    if w % 2 != 0 or h % 2 != 0:
        new_w = w + (w % 2)
        new_h = h + (h % 2)
        for idx, (orig_i, frame) in enumerate(valid):
            padded = np.zeros((new_h, new_w, 3), dtype=np.uint8)
            padded[:h, :w] = frame
            valid[idx] = (orig_i, padded)
        h, w = new_h, new_w

    if ext == ".gif":
        _export_gif(valid, output_path, fps, progress_callback)
    elif ext == ".avi":
        _export_avi(valid, output_path, fps, progress_callback)
    else:
        _export_mp4(valid, output_path, fps, progress_callback)

    _compress_to_target(output_path)

    return output_path


def _export_mp4(valid, output_path, fps, progress_callback):
    h, w = valid[0][1].shape[:2]
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{w}x{h}",
        "-framerate", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "18",
        "-movflags", "+faststart",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for idx, (_, frame) in enumerate(valid):
            proc.stdin.write(frame.tobytes())
            if progress_callback:
                progress_callback(idx + 1, len(valid))
    except BrokenPipeError:
        pass
    finally:
        proc.stdin.close()
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"ffmpeg exited with code {ret}")


def _export_avi(valid, output_path, fps, progress_callback):
    h, w = valid[0][1].shape[:2]
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{w}x{h}",
        "-framerate", str(fps),
        "-i", "-",
        "-c:v", "mjpeg",
        "-q:v", "3",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for idx, (_, frame) in enumerate(valid):
            proc.stdin.write(frame.tobytes())
            if progress_callback:
                progress_callback(idx + 1, len(valid))
    except BrokenPipeError:
        pass
    finally:
        proc.stdin.close()
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"ffmpeg exited with code {ret}")


def _export_gif(valid, output_path, fps, progress_callback):
    h, w = valid[0][1].shape[:2]
    gif_fps = 5
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{w}x{h}",
        "-framerate", str(fps),
        "-i", "-",
        "-filter_complex",
        f"fps={gif_fps},split[s0][s1];[s0]palettegen=max_colors=256:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5",
        "-gifflags", "+offsetting+transdiff",
        "-loop", "0",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for idx, (_, frame) in enumerate(valid):
            proc.stdin.write(frame.tobytes())
            if progress_callback:
                progress_callback(idx + 1, len(valid))
    except BrokenPipeError:
        pass
    finally:
        proc.stdin.close()
    ret = proc.wait()
    if ret != 0:
        raise RuntimeError(f"ffmpeg exited with code {ret}")


_TARGET_MB = 30


def _compress_to_target(output_path):
    path = Path(output_path)
    if not path.exists():
        return
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb <= _TARGET_MB:
        return

    ext = path.suffix.lower()
    fd, tmp_name = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        if ext == ".gif":
            shutil.move(str(path), str(tmp))
            _compress_gif(tmp, path, size_mb)
        elif ext == ".avi":
            shutil.move(str(path), str(tmp))
            _compress_avi(tmp, path, size_mb)
        else:
            shutil.move(str(path), str(tmp))
            _compress_mp4(tmp, path, size_mb)
    except Exception:
        if not path.exists() and tmp.exists():
            shutil.move(str(tmp), str(path))
        raise
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def _compress_mp4(src, dst, current_mb):
    crf = 23
    while current_mb > _TARGET_MB and crf <= 45:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(src),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "medium", "-crf", str(crf),
            "-movflags", "+faststart",
            str(dst),
        ], check=True, capture_output=True)
        current_mb = dst.stat().st_size / (1024 * 1024) if dst.exists() else current_mb
        crf += 3

    if current_mb > _TARGET_MB:
        scale = 0.75
        while current_mb > _TARGET_MB and scale >= 0.3:
            dst.unlink(missing_ok=True)
            subprocess.run([
                "ffmpeg", "-y", "-i", str(src),
                "-vf", f"scale=iw*{scale}:ih*{scale}:flags=lanczos",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-preset", "medium", "-crf", "28",
                "-movflags", "+faststart",
                str(dst),
            ], check=True, capture_output=True)
            current_mb = dst.stat().st_size / (1024 * 1024) if dst.exists() else current_mb
            scale *= 0.75


def _compress_avi(src, dst, current_mb):
    q = 10
    while current_mb > _TARGET_MB and q <= 31:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(src),
            "-c:v", "mjpeg", "-q:v", str(q),
            str(dst),
        ], check=True, capture_output=True)
        current_mb = dst.stat().st_size / (1024 * 1024) if dst.exists() else current_mb
        q += 5

    if current_mb > _TARGET_MB:
        scale = 0.75
        while current_mb > _TARGET_MB and scale >= 0.3:
            dst.unlink(missing_ok=True)
            subprocess.run([
                "ffmpeg", "-y", "-i", str(src),
                "-vf", f"scale=iw*{scale}:ih*{scale}:flags=lanczos",
                "-c:v", "mjpeg", "-q:v", "15",
                str(dst),
            ], check=True, capture_output=True)
            current_mb = dst.stat().st_size / (1024 * 1024) if dst.exists() else current_mb
            scale *= 0.75


def _compress_gif(src, dst, current_mb):
    max_colors = 128
    gif_fps = 5
    while current_mb > _TARGET_MB:
        dst.unlink(missing_ok=True)
        ff = f"fps={gif_fps},split[s0][s1];[s0]palettegen=max_colors={max_colors}:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(src),
            "-filter_complex", ff,
            "-gifflags", "+offsetting+transdiff", "-loop", "0",
            str(dst),
        ], check=True, capture_output=True)
        current_mb = dst.stat().st_size / (1024 * 1024) if dst.exists() else current_mb
        if max_colors > 16:
            max_colors //= 2
        elif gif_fps > 2:
            gif_fps -= 1
        else:
            break

    if current_mb > _TARGET_MB:
        scale = 0.75
        while current_mb > _TARGET_MB and scale >= 0.3:
            dst.unlink(missing_ok=True)
            ff = f"fps={gif_fps},scale=iw*{scale}:ih*{scale}:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=64:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"
            subprocess.run([
                "ffmpeg", "-y", "-i", str(src),
                "-filter_complex", ff,
                "-gifflags", "+offsetting+transdiff", "-loop", "0",
                str(dst),
            ], check=True, capture_output=True)
            current_mb = dst.stat().st_size / (1024 * 1024) if dst.exists() else current_mb
            scale *= 0.75
