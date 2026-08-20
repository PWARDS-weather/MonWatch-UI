# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Module: core/unidata_truecolor.py
# Description: Satellite-agnostic implementation of the Unidata GOES-16
# "True Color Recipe" (https://unidata.github.io/python-gallery/examples/
# mapping_GOES16_TrueColor.html) generalized for GOES ABI, Himawari AHI,
# GK-2A AMI, MTG FCI and Meteosat MSG/SEVIRI.
#
# The recipe:
#   1. Clip reflectance channels 0..1 (auto-normalizes 0..100 % data).
#   2. Gamma-correct each channel  value^(1/gamma) with gamma = 2.2.
#   3. "True Green"  G_true = 0.45*R + 0.1*G + 0.45*B  (CIMSS synthetic green).
#   4. Stack R/G/B.  Optionally:
#       - overlay clean IR (Band 13 / IR 10.8 um) for nighttime pixels, and
#       - apply a contrast correction for a livelier image.
#
# Every function accepts an ``xp`` argument (numpy or cupy) so the engine can
# keep working on the GPU while the pixel math stays in one shared place.
#
# Principal Developer: Zero (Prince Al Zhanjie B. Dela Rosa)
# Affiliation: PWARDS-weather
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# =============================================================================

DEFAULT_GAMMA = 2.2

# Clean IR normalization window (Kelvin), from the Unidata recipe.
CLEAN_IR_MIN = 90.0
CLEAN_IR_MAX = 313.0
# Softening applied so night clouds aren't blown out when overlaid.
CLEAN_IR_DIVISOR = 1.4
# Default contrast amount used by contrast_correction().
DEFAULT_CONTRAST = 105


def normalize_reflectance(xp, arr):
    """Map a reflectance band (0..1 or 0..100 and NaN-padded) to 0..1.

    NaN pixels (space / missing) are forced to 0 so the night side renders
    as black instead of being treated as missing data.
    """
    a = xp.asarray(arr, dtype=xp.float32)
    a = xp.where(xp.isnan(a), xp.float32(0.0), a)
    if a.size and float(xp.max(a)) > 1.0:
        a = a / xp.float32(100.0)
    return xp.clip(a, xp.float32(0.0), xp.float32(1.0))


def apply_gamma(xp, arr, gamma=DEFAULT_GAMMA):
    """Apply gamma correction  value^(1/gamma)  over the 0..1 domain."""
    if gamma is None or float(gamma) == 1.0:
        return arr
    return xp.power(xp.clip(arr, xp.float32(0.0), xp.float32(1.0)), 1.0 / gamma)


def true_green(xp, r, g, b, weights=(0.45, 0.1, 0.45)):
    """CIMSS synthetic green: clip(0.45R + 0.1G + 0.45B)."""
    w_r, w_g, w_b = weights
    return xp.clip(w_r * r + w_g * g + w_b * b, xp.float32(0.0), xp.float32(1.0))


def make_true_color(xp, r, veg, b, gamma=DEFAULT_GAMMA):
    """Build a (H, W, 3) float RGB 0..1 array using the full recipe.

    ``r``, ``veg`` and ``b`` are the raw red / veggie-NIR / blue bands
    (0..1 or 0..100, already resampled to a common grid). Gamma is applied
    per-channel *before* the synthetic-green mix, matching the Unidata order.
    """
    r = normalize_reflectance(xp, r)
    veg = normalize_reflectance(xp, veg)
    b = normalize_reflectance(xp, b)
    r_g = apply_gamma(xp, r, gamma)
    veg_g = apply_gamma(xp, veg, gamma)
    b_g = apply_gamma(xp, b, gamma)
    g_true = true_green(xp, r_g, veg_g, b_g)
    return xp.stack([r_g, g_true, b_g], axis=-1)


def make_natural_color(xp, r, g, b, gamma=DEFAULT_GAMMA):
    """Build a (H, W, 3) float RGB 0..1 array without synthetic green.

    Used by satellites without a dedicated blue/green band pair (MSG/SEVIRI),
    where red = VIS008, green = IR016, blue = VIS006 is the standard
    natural-color substitute.
    """
    r = normalize_reflectance(xp, r)
    g = normalize_reflectance(xp, g)
    b = normalize_reflectance(xp, b)
    return xp.stack([apply_gamma(xp, r, gamma),
                     apply_gamma(xp, g, gamma),
                     apply_gamma(xp, b, gamma)], axis=-1)


def clean_ir(xp, ir_kelvin, vmin=CLEAN_IR_MIN, vmax=CLEAN_IR_MAX,
             divisor=CLEAN_IR_DIVISOR):
    """Normalize clean IR brightness temperature to a grey RGB layer.

    (bt - 90)/(313 - 90) -> clip -> invert so cold clouds are white ->
    soften by /1.4 for a subtle overlay.
    """
    ir = xp.asarray(ir_kelvin, dtype=xp.float32)
    ir = xp.where(xp.isfinite(ir), ir, xp.float32(300.0))
    ir = xp.clip((ir - vmin) / (vmax - vmin), xp.float32(0.0), xp.float32(1.0))
    ir = 1.0 - ir
    return xp.clip(ir / divisor, xp.float32(0.0), xp.float32(1.0))


def overlay_night(xp, rgb, ir_layer):
    """Max-blend the clean-IR grey layer into each RGB channel.

    Where the visible true color is black (night), the IR fills it in.
    """
    return xp.stack([xp.maximum(rgb[..., 0], ir_layer),
                     xp.maximum(rgb[..., 1], ir_layer),
                     xp.maximum(rgb[..., 2], ir_layer)], axis=-1)


def contrast_correction(xp, color, contrast=DEFAULT_CONTRAST):
    """Modify the contrast of an RGB float 0..1 array.

    See: https://www.dfstudios.co.uk/articles/programming/image-programming-
    algorithms/image-processing-algorithms-part-5-contrast-adjustment/
    """
    f = (259.0 * (contrast + 255.0)) / (255.0 * 259.0 - contrast)
    return xp.clip(f * (color - 0.5) + 0.5, xp.float32(0.0), xp.float32(1.0))


def blend_daynight(xp, day_rgb, night_rgb, sza):
    """Blend day colour and night IR by solar zenith angle.

    sza <= 85 deg -> pure day; sza >= 95 deg -> pure night; a smooth ramp
    bridges the terminator. Mirrors the VP-SIFT-style weighting already used
    by the engines' ``_true_color_daynight``.
    """
    sza = xp.asarray(sza, dtype=xp.float32)
    day_weight = xp.clip((95.0 - sza) / 10.0, xp.float32(0.0), xp.float32(1.0))
    day_weight = day_weight[..., None]
    return day_rgb * day_weight + night_rgb * (1.0 - day_weight)
