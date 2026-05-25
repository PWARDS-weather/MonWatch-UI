#!/usr/bin/env python3
"""
Himawari AHI Satellite Data Processor - ADVANCED RGB GENERATOR
USING SATPY FOR COMPOSITES WHERE AVAILABLE

FIXED: Now correctly uses the input directory when .tif files are directly in it
"""

import sys
import os
import re
from pathlib import Path
from datetime import datetime, timezone
import glob
import numpy as np

# Try to import Satpy for advanced compositing
try:
    import xarray as xr
    from satpy import Scene
    from pyresample.geometry import AreaDefinition
    SATPY_AVAILABLE = True
except ImportError as e:
    print(f"[!] WARNING: Satpy imports failed: {e}")
    print("[!] Using custom compositing only.")
    print("[!] Install with: pip install satpy xarray dask pyresample")
    SATPY_AVAILABLE = False

try:
    import rasterio
    from rasterio.enums import Resampling
    RASTERIO_AVAILABLE = True
except ImportError:
    print("[!] ERROR: Install rasterio: pip install rasterio")
    RASTERIO_AVAILABLE = False

# Optional imports for day/night calculation
try:
    import ephem
    EPHEM_AVAILABLE = True
except ImportError:
    EPHEM_AVAILABLE = False
    print("[!] WARNING: ephem not installed. Day/night detection will be limited.")
    print("[!] Install with: pip install ephem")

try:
    from pyproj import Transformer
    PYPROJ_AVAILABLE = True
except ImportError:
    PYPROJ_AVAILABLE = False
    print("[!] WARNING: pyproj not installed. Per-pixel lat/lon not available.")
    print("[!] Install with: pip install pyproj")

# ============================================================================
# ADVANCED RGB PRODUCT DEFINITIONS
# Recipes based on JMA Himawari RGB Composite Guide & NOAA Quick Guides
# ============================================================================

PRODUCTS = {
    "natural": {
        "name": "Natural Color",
        "bands": ["B05", "B04", "B03"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"band": "B05", "min": 0.0, "max": 99.0, "gamma": 1.0, "type": "reflectance"},
            "G": {"band": "B04", "min": 0.0, "max": 100.0, "gamma": 0.95, "type": "reflectance"},
            "B": {"band": "B03", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"}
        },
        "use_satpy": False,
        "description": "Standard daytime Earth view; snow/ice cyan, vegetation green, clouds white."
    },
    
    "Geo": {
        "name": "Geo Color",
        "bands": ["B03", "B02", "B01"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"band": "B03", "min": 0.0, "max": 200.0, "gamma": 0.8, "type": "reflectance"},
            "G": {"band": "B02", "min": 0.0, "max": 200.0, "gamma": 0.8, "type": "reflectance"},
            "B": {"band": "B01", "min": 0.0, "max": 200.0, "gamma": 0.8, "type": "reflectance"}
        },
        "use_satpy": False,  # Keep as is - you said it's good enough
        "description": "True color approximation using visible bands."
    },
    "sandwich": {
        "name": "Sandwich Product",
        "bands": ["B13", "B03"],
        "channels": ["R", "G", "B"],
        "blend_mode": "multiply",
        "ir_band": {
            "band": "B13",
            "min": 200,
            "max": 240,
            "gamma": 1.0,
            "type": "temperature"
        },
        "vis_band": {
            "band": "B03",
            "min": 0.0,
            "max": 1.0,
            "gamma": 2.2,
            "type": "reflectance"
        },
        "opacity": 0.7,
        "use_satpy": True if SATPY_AVAILABLE else False,
        "description": "High-resolution visible underlaid with IR; cold clouds appear red/orange."
    },
        
    "airmass": {
        "name": "Air Mass RGB",
        "bands": ["B08", "B10", "B12", "B13"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"operation": "diff", "bands": ["B10", "B08"], "min": 0, "max": 25.8, "gamma": 1.0, "type": "temperature_diff"},
            "G": {"operation": "diff", "bands": ["B13", "B12"], "min": -4.3, "max": 41.5, "gamma": 1.0, "type": "temperature_diff"},
            "B": {"band": "B08", "min": 208.0, "max": 242.6, "gamma": 1.0, "type": "temperature"}
        },
        "use_satpy": True if SATPY_AVAILABLE else False,
        "description": "Jet stream, tropopause folding, high-level moisture."
    },
    
    "dust": {
        "name": "Dust RGB",
        "bands": ["B15", "B13", "B14", "B11", "B13"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"operation": "diff", "bands": ["B13", "B15"], "min": -3.0, "max": 7.5, "gamma": 1.0},
            "G": {"operation": "diff", "bands": ["B11", "B14"], "min": -0.5, "max": 15.0, "gamma": 2.2},
            "B": {"band": "B13", "min": 261.5, "max": 289.2, "gamma": 1.0, "invert": True}
        },
        "use_satpy": True if SATPY_AVAILABLE else False,
        "description": "Dust (magenta/pink), thin cirrus (dark), thick clouds (brown)."
    },
    
    "day_convection": {
        "name": "Day Convection RGB",
        "bands": ["B05", "B03", "B07", "B08", "B10", "B13"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"operation": "diff", "bands": ["B08", "B10"], "min": -35.0, "max": 5.0, "gamma": 1.0, "type": "temperature_diff"},
            "G": {"operation": "diff", "bands": ["B07", "B13"], "min": -5.0, "max": 60.0, "gamma": 0.5, "type": "temperature_diff"},
            "B": {"operation": "diff", "bands": ["B05", "B03"], "min": -75.0, "max": 25.0, "gamma": 0.95, "type": "reflectance_diff"}
        },
        "use_satpy": True if SATPY_AVAILABLE else False,
        "description": "Intense convection (yellow), small ice crystals (bright yellow)."
    },
    
    "fire": {
        "name": "Fire Temperature RGB",
        "bands": ["B07", "B06", "B05"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"band": "B07", "min": 273.0, "max": 350.0, "gamma": 1.0, "type": "temperature"},
            "G": {"band": "B06", "min": 0.0, "max": 50.0, "gamma": 1.0, "type": "reflectance"},
            "B": {"band": "B05", "min": 0.0, "max": 50.0, "gamma": 1.0, "type": "reflectance"}
        },
        "use_satpy": True if SATPY_AVAILABLE else False,
        "description": "Hot spots (red/yellow), burn scars (dark)."
    },
    
    "night_microphysics": {
        "name": "Night Microphysics RGB",
        "bands": ["B10", "B03", "B12"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"operation": "diff", "bands": ["B12", "B10"], "min": -4.0, "max": 2.0, "gamma": 1.0, "type": "temperature_diff"},
            "G": {"operation": "diff", "bands": ["B10", "B03"], "min": -4.0, "max": 6.0, "gamma": 1.0, "type": "temperature_diff"},
            "B": {"band": "B10", "min": 243.0, "max": 293.0, "gamma": 1.0, "type": "temperature"}
        },
        "use_satpy": True if SATPY_AVAILABLE else False,
        "description": "Fog/low clouds (aqua), high ice clouds (red/purple)."
    },
    
    "cloud_phase": {
        "name": "Cloud Phase RGB",
        "bands": ["B03", "B05", "B06"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"band": "B03", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"},
            "G": {"band": "B05", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"},
            "B": {"band": "B06", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"}
        },
        "use_satpy": True if SATPY_AVAILABLE else False,
        "description": "Ice clouds (blue/cyan), liquid water clouds (white/green)."
    },
    
    "true": {
        "name": "True Color",
        "bands": ["B03", "B02", "B01"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"band": "B03", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"},
            "G": {"band": "B02", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"},
            "B": {"band": "B01", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"}
        },
        "use_satpy": False,
        "description": "True color approximation (Rayleigh correction recommended)."
    },
    
    "infrared": {
        "name": "Infrared (Standard)",
        "bands": ["B13"],
        "single_band": True,
        "formula": {
            "band": "B13",
            "min": None,
            "max": None,
            "gamma": 1.0,
            "invert": True
        },
        "use_satpy": False,
        "description": "Standard IR: cold clouds white, warm ground black."
    },

    "visible": {
        "name": "Visible",
        "bands": ["B03", "B02", "B01"],
        "channels": ["R", "G", "B"],
        "formula": {
            "R": {"band": "B03", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"},
            "G": {"band": "B02", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"},
            "B": {"band": "B01", "min": 0.0, "max": 100.0, "gamma": 1.0, "type": "reflectance"}
        },
        "use_satpy": False,
        "description": "Visible light composite."
    },
}

BAND_WAVELENGTHS = {
    "B01": 0.47, "B02": 0.64, "B03": 0.86, "B04": 0.86, "B05": 1.6, "B06": 2.3,
    "B07": 3.9, "B08": 6.2, "B10": 7.3, "B11": 8.6, "B12": 9.6, "B13": 10.4, "B14": 11.2, "B15": 12.3, "B16": 13.3
}

SATPY_COMPOSITE_MAP = {
    "airmass": "airmass",
    "dust": "dust",
    "day_convection": "day_convection",
    "fire": "fire_temperature",
    "night_microphysics": "night_microphysics",
    "cloud_phase": "cloud_phase",
    "sandwich": "sandwich",
    "infrared": "infrared",
    "visible": "natural_color"
}

# ============================================================================
# CORE PROCESSING FUNCTIONS
# ============================================================================

def get_platform_from_path(band_dir):
    band_dir_str = str(band_dir)
    if 'himawari9' in band_dir_str.lower():
        return 'Himawari-9', 'ahi'
    elif 'himawari8' in band_dir_str.lower():
        return 'Himawari-8', 'ahi'
    elif 'goes18' in band_dir_str.lower():
        return 'GOES-18', 'abi'
    elif 'goes17' in band_dir_str.lower():
        return 'GOES-17', 'abi'
    elif 'goes16' in band_dir_str.lower():
        return 'GOES-16', 'abi'
    else:
        return 'Himawari-8', 'ahi'

def get_target_shape(product_info):
    return (2200, 2200) if "B02" in product_info["bands"] else (1100, 1100)

def linear_scale_with_gamma(data, min_val, max_val, gamma=1.0, invert=False):
    if max_val <= min_val:
        raise ValueError(f"MAX ({max_val}) must be greater than MIN ({min_val})")
    data_clipped = np.clip(data, min_val, max_val)
    scaled = (data_clipped - min_val) / (max_val - min_val)
    if gamma != 1.0:
        scaled = np.power(scaled, 1.0 / gamma)
    byte_value = (scaled * 255).astype(np.uint8)
    if invert:
        byte_value = 255 - byte_value
    return byte_value

def read_band_data(band_file, nodata_value=None, target_shape=None):
    with rasterio.open(band_file) as src:
        meta = src.meta.copy()
        if target_shape and (src.height != target_shape[0] or src.width != target_shape[1]):
            data = src.read(1, out_shape=target_shape, resampling=Resampling.bilinear)
            meta.update({
                'height': target_shape[0],
                'width': target_shape[1],
                'transform': src.transform * src.transform.scale(
                    (src.width / target_shape[1]), (src.height / target_shape[0])
                )
            })
        else:
            data = src.read(1)
        if nodata_value is None and src.nodata is not None:
            nodata_value = src.nodata
        if nodata_value is not None:
            data = data.astype(np.float32)
            data[data == nodata_value] = np.nan
        return data, meta

def band_difference(band1_data, band2_data):
    if band1_data.shape != band2_data.shape:
        min_h = min(band1_data.shape[0], band2_data.shape[0])
        min_w = min(band1_data.shape[1], band2_data.shape[1])
        band1_data = band1_data[:min_h, :min_w]
        band2_data = band2_data[:min_h, :min_w]
    band1_data = band1_data.astype(np.float32)
    band2_data = band2_data.astype(np.float32)
    diff = band1_data - band2_data
    nan_mask = np.isnan(band1_data) | np.isnan(band2_data)
    diff = np.where(nan_mask, 0, diff)
    return diff

def create_area_definition_from_geotiff(meta, platform, sensor):
    try:
        crs = meta.get('crs')
        if crs is None:
            if 'himawari' in platform.lower() or 'ahi' in sensor.lower():
                proj_dict = {'proj': 'geos', 'lon_0': 140.7, 'h': 35785863, 'x_0': 0, 'y_0': 0, 'units': 'm'}
            else:
                proj_dict = {'proj': 'geos', 'lon_0': -75.0, 'h': 35786023, 'x_0': 0, 'y_0': 0, 'units': 'm'}
        else:
            try:
                proj_dict = crs.to_proj4()
                if isinstance(proj_dict, str):
                    proj_dict = dict(re.findall(r'\+(\w+)=([\w\.\-]+)', proj_dict))
            except:
                proj_dict = {'proj': 'geos', 'lon_0': 140.7, 'h': 35785863, 'x_0': 0, 'y_0': 0, 'units': 'm'}
        transform = meta['transform']
        width = meta['width']
        height = meta['height']
        left = transform[2]
        top = transform[5]
        right = left + transform[0] * width
        bottom = top + transform[4] * height
        area_extent = (left, bottom, right, top)
        area_def = AreaDefinition(
            area_id=f'{platform}_{sensor}_full_disk',
            description=f'Full disk area for {platform} {sensor}',
            proj_id='full_disk',
            projection=proj_dict,
            width=width,
            height=height,
            area_extent=area_extent
        )
        return area_def
    except Exception as e:
        print(f"[!] Warning: Could not create area definition: {e}")
        return None

def create_rgb_with_satpy(product_key, product_info, band_dir, target_shape):
    """Satpy integration using band names as dataset keys."""
    if not SATPY_AVAILABLE:
        return create_rgb_custom(product_key, product_info, band_dir, target_shape)

    try:
        print(f"[SATPY] Creating {product_info['name']} with Satpy...")
        platform, sensor = get_platform_from_path(band_dir)
        scn = Scene()

        for band_name in product_info["bands"]:
            band_file = band_dir / f"{band_name}.tif"
            if not band_file.exists():
                print(f"[!] Missing band for Satpy: {band_file}")
                return False

            data, meta = read_band_data(band_file, target_shape=target_shape)
            area_def = create_area_definition_from_geotiff(meta, platform, sensor)

            # Determine calibration attributes
            if band_name in ["B01", "B02", "B03", "B04", "B05"]:
                calibration = "reflectance"
                units = "%"
                standard_name = "toa_bidirectional_reflectance"
            else:
                calibration = "brightness_temperature"
                units = "K"
                standard_name = "toa_brightness_temperature"

            da = xr.DataArray(
                data,
                dims=['y', 'x'],
                attrs={
                    'name': band_name,
                    'wavelength': BAND_WAVELENGTHS.get(band_name, 10.0),
                    'wavelength_units': 'µm',
                    'units': units,
                    'calibration': calibration,
                    'standard_name': standard_name,
                    'platform_name': platform,
                    'sensor': sensor,
                    'resolution': 1000 if band_name == "B02" else 2000,
                    'modifiers': (),
                    'area': area_def,
                    'start_time': datetime.utcnow(),
                    'end_time': datetime.utcnow()
                }
            )
            scn[band_name] = da

        satpy_name = SATPY_COMPOSITE_MAP.get(product_key, product_key)
        print(f"[SATPY] Loading composite: {satpy_name}")
        scn.load([satpy_name])
        composite = scn[satpy_name]

        if hasattr(composite, 'compute'):
            composite_data = composite.compute().values
        else:
            composite_data = composite.values

        if composite_data.ndim == 2:
            composite_data = np.stack([composite_data, composite_data, composite_data], axis=0)
        elif composite_data.ndim == 3 and composite_data.shape[2] == 3:
            composite_data = np.transpose(composite_data, (2, 0, 1))

        if composite_data.dtype != np.uint8:
            if composite_data.max() <= 1.0 and composite_data.min() >= 0.0:
                composite_data = (composite_data * 255).astype(np.uint8)
            else:
                vmin, vmax = np.nanmin(composite_data), np.nanmax(composite_data)
                if vmax > vmin:
                    composite_data = ((composite_data - vmin) / (vmax - vmin) * 255).astype(np.uint8)
                else:
                    composite_data = composite_data.astype(np.uint8)

        sat_dir = band_dir / "sat"
        sat_dir.mkdir(exist_ok=True)
        output_file = sat_dir / f"{product_key}.tif"
        meta.update({'count': 3, 'dtype': 'uint8', 'nodata': 0,
                     'height': composite_data.shape[1], 'width': composite_data.shape[2]})
        with rasterio.open(output_file, 'w', **meta) as dst:
            dst.write(composite_data)

        print(f"[SATPY OK] Created: {output_file} ({composite_data.nbytes/1024/1024:.1f} MB)")
        return True

    except Exception as e:
        print(f"[SATPY ERROR] Failed: {e}")
        import traceback
        traceback.print_exc()
        print(f"[SATPY] Falling back to custom implementation")
        return create_rgb_custom(product_key, product_info, band_dir, target_shape)

def create_grayscale_product(product_key, product_info, band_dir, target_shape):
    """Single-band grayscale product (e.g., infrared)."""
    if not RASTERIO_AVAILABLE:
        return False

    sat_dir = band_dir / "sat"
    sat_dir.mkdir(exist_ok=True)
    output_file = sat_dir / f"{product_key}.tif"
    if output_file.exists() and output_file.stat().st_size > 1000:
        print(f"[~] Already exists: {output_file}")
        return True

    try:
        print(f"[GRAYSCALE] Creating {product_info['name']}...")
        band_name = product_info["formula"]["band"]
        band_file = band_dir / f"{band_name}.tif"
        if not band_file.exists():
            print(f"[!] Missing {band_file}")
            return False

        data, meta = read_band_data(band_file, target_shape=target_shape)
        if np.any(np.isnan(data)):
            data = np.nan_to_num(data, nan=np.nanmean(data))

        spec = product_info["formula"]
        min_val = spec["min"]
        max_val = spec["max"]

        if min_val is None or max_val is None:
            min_val = np.nanmin(data)
            max_val = np.nanmax(data)
            print(f"[GRAYSCALE] Auto-scaling range: {min_val:.1f} – {max_val:.1f}")

        gray = linear_scale_with_gamma(
            data,
            min_val,
            max_val,
            spec.get("gamma", 1.0),
            spec.get("invert", False)
        )

        rgb = np.stack([gray, gray, gray], axis=0)
        meta.update({'count': 3, 'dtype': 'uint8', 'nodata': 0,
                     'height': rgb.shape[1], 'width': rgb.shape[2]})
        with rasterio.open(output_file, 'w', **meta) as dst:
            dst.write(rgb)

        print(f"[GRAYSCALE OK] Created: {output_file} ({rgb.nbytes/1024/1024:.1f} MB)")
        return True

    except Exception as e:
        print(f"[GRAYSCALE ERROR] {product_info['name']}: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_rgb_custom(product_key, product_info, band_dir, target_shape):
    """Custom RGB implementation with georeferencing preserved."""
    if not RASTERIO_AVAILABLE:
        return False

    sat_dir = band_dir / "sat"
    sat_dir.mkdir(exist_ok=True)
    output_file = sat_dir / f"{product_key}.tif"
    if output_file.exists() and output_file.stat().st_size > 1000:
        print(f"[~] Already exists: {output_file}")
        return True

    try:
        print(f"[CUSTOM] Creating {product_info['name']}...")
        if product_key == "sandwich":
            return create_sandwich_product_enhanced(band_dir, target_shape)

        channels = product_info["channels"]
        formula = product_info["formula"]
        band_cache = {}
        meta_cache = {}
        all_bands = set()
        for ch in channels:
            spec = formula[ch]
            if "operation" in spec and spec["operation"] == "diff":
                all_bands.update(spec["bands"])
            else:
                all_bands.add(spec["band"])

        for band_name in all_bands:
            band_file = band_dir / f"{band_name}.tif"
            if not band_file.exists():
                print(f"[!] Missing {band_file}")
                return False
            data, meta = read_band_data(band_file, target_shape=target_shape)
            band_cache[band_name] = data
            meta_cache[band_name] = meta

        # Use metadata from first band for georeferencing
        ref_meta = meta_cache[list(all_bands)[0]]

        common_shape = band_cache[list(all_bands)[0]].shape
        for band_name in all_bands:
            h, w = band_cache[band_name].shape
            common_shape = (min(common_shape[0], h), min(common_shape[1], w))

        channel_data = {}
        for ch in channels:
            spec = formula[ch]
            if "operation" in spec and spec["operation"] == "diff":
                b1, b2 = spec["bands"]
                d1 = band_cache[b1][:common_shape[0], :common_shape[1]]
                d2 = band_cache[b2][:common_shape[0], :common_shape[1]]
                diff = band_difference(d1, d2)
                channel_data[ch] = linear_scale_with_gamma(
                    diff, spec["min"], spec["max"], spec.get("gamma", 1.0)
                )
            else:
                band_name = spec["band"]
                data = band_cache[band_name][:common_shape[0], :common_shape[1]]
                channel_data[ch] = linear_scale_with_gamma(
                    data, spec["min"], spec["max"],
                    spec.get("gamma", 1.0),
                    spec.get("invert", False)
                )

        final_shape = channel_data[channels[0]].shape
        for ch in channels:
            if ch in channel_data:
                h, w = channel_data[ch].shape
                final_shape = (min(final_shape[0], h), min(final_shape[1], w))
        for ch in channels:
            if ch in channel_data:
                channel_data[ch] = channel_data[ch][:final_shape[0], :final_shape[1]]

        rgb_data = np.stack([channel_data["R"], channel_data["G"], channel_data["B"]], axis=0)
        ref_meta.update({'count': 3, 'dtype': 'uint8', 'nodata': 0,
                         'height': final_shape[0], 'width': final_shape[1]})
        with rasterio.open(output_file, 'w', **ref_meta) as dst:
            dst.write(rgb_data)

        print(f"[CUSTOM OK] Created: {output_file} ({rgb_data.nbytes/1024/1024:.1f} MB)")
        return True

    except Exception as e:
        print(f"[CUSTOM ERROR] {product_info['name']}: {e}")
        import traceback
        traceback.print_exc()
        return False

def calculate_solar_zenith_angle(timestamp, lon, lat):
    """Calculate solar zenith angle (degrees) at given location and time."""
    if not EPHEM_AVAILABLE:
        hour = timestamp.hour + timestamp.minute / 60.0
        sza = 90 - 90 * np.cos((hour - 12) * np.pi / 12)
        return sza

    obs = ephem.Observer()
    obs.lon = str(lon)
    obs.lat = str(lat)
    obs.date = timestamp
    
    sun = ephem.Sun()
    sun.compute(obs)
    return 90 - (float(sun.alt) * 180 / ephem.pi)

def compute_lat_lon_grid(meta, height, width):
    """Compute latitude and longitude grids using actual CRS and transform."""
    if not PYPROJ_AVAILABLE or meta.get('crs') is None:
        return None, None

    try:
        from rasterio.transform import xy
        rows, cols = np.meshgrid(np.arange(height), np.arange(width), indexing='ij')
        xs, ys = xy(meta['transform'], rows, cols)
        transformer = Transformer.from_crs(meta['crs'], 'EPSG:4326', always_xy=True)
        lon, lat = transformer.transform(xs, ys)
        return lat, lon
    except Exception as e:
        print(f"[!] Lat/lon grid computation failed: {e}")
        return None, None

def extract_timestamp_from_path(band_dir):
    """Extract datetime object from directory path."""
    import re
    path_str = str(band_dir)
    match = re.search(r'(\d{12})', path_str)
    if match:
        dt_str = match.group(1)
        return datetime.strptime(dt_str, '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
    print("[!] Could not extract timestamp from path, using current UTC time.")
    return datetime.now(timezone.utc)

def create_sandwich_product_enhanced(band_dir, target_shape):
    if not RASTERIO_AVAILABLE:
        return False
    sat_dir = band_dir / "sat"
    sat_dir.mkdir(exist_ok=True)
    output_file = sat_dir / "sandwich.tif"
    if output_file.exists() and output_file.stat().st_size > 1000:
        print(f"[~] Already exists: {output_file}")
        return True

    try:
        ir_data, meta = read_band_data(band_dir / "B13.tif", target_shape=target_shape)
        vis_data, _ = read_band_data(band_dir / "B03.tif", target_shape=target_shape)

        h = min(ir_data.shape[0], vis_data.shape[0])
        w = min(ir_data.shape[1], vis_data.shape[1])
        ir = ir_data[:h, :w].astype(np.float32)
        vis = vis_data[:h, :w].astype(np.float32)

        vis = np.clip(vis / 100.0, 0, 1)

        # Better IR scaling + invert
        ir_norm = np.clip((ir - 190) / (280 - 190), 0, 1)
        ir_inv = 1.0 - ir_norm

        # Red-orange tint for cold clouds
        r = vis * ir_inv * 2.0
        g = vis * ir_inv * 0.8
        b = vis * ir_inv * 0.4 + vis * 0.3   # keep some blue

        rgb = np.stack([r, g, b], axis=0)
        rgb = np.clip(rgb * 255, 0, 255).astype(np.uint8)

        meta.update({'count': 3, 'dtype': 'uint8', 'nodata': 0, 'height': h, 'width': w})
        with rasterio.open(output_file, 'w', **meta) as dst:
            dst.write(rgb)

        print(f"[OK] Sandwich created: {output_file}")
        return True

    except Exception as e:
        print(f"[!] Sandwich error: {e}")
        return False

def create_rgb_product(product_key, product_info, band_dir):
    target_shape = get_target_shape(product_info)
    print(f"[+] Target resolution: {target_shape[0]}x{target_shape[1]} ({'1km' if target_shape[0]==2200 else '2km'})")
    
    if product_info.get("single_band", False):
        return create_grayscale_product(product_key, product_info, band_dir, target_shape)
    
    if product_info.get("use_satpy", False) and SATPY_AVAILABLE:
        return create_rgb_with_satpy(product_key, product_info, band_dir, target_shape)
    else:
        return create_rgb_custom(product_key, product_info, band_dir, target_shape)

def find_band_files(base_dir):
    """
    FIXED: Correctly finds band directories based on how bg_decode.py saves files
    
    When Process_dat.py calls: bg_product.py -i <datetime_folder> --all
    The .tif files are directly in that datetime folder (not in subdirectories)
    """
    print(f"[+] Searching for band files in: {base_dir}")
    
    band_dirs = []
    
    # PRIMARY: Check if input directory itself has B??.tif files
    # This is the case when bg_decode.py creates files directly in the datetime folder
    tif_files = list(base_dir.glob("B??.tif"))
    if tif_files:
        print(f"    [FOUND] {base_dir} ({len(tif_files)} bands)")
        available = [f.stem for f in tif_files]
        if len(available) >= 3:  # Need at least 3 bands for basic RGB
            band_dirs.append(base_dir)
            print(f"    [VALID] {base_dir} - Bands: {', '.join(sorted(available))}")
        else:
            print(f"    [SKIP] {base_dir} - Only {len(available)} bands: {available}")
    
    # SECONDARY: Search subdirectories if input dir doesn't have files directly
    if not band_dirs:
        print("[+] Input directory has no .tif files, searching subdirectories...")
        
        # Look for AHI-L1b-FLDK folders
        for pattern in [
            f"{base_dir}/**/AHI-L1b-FLDK_*",
            f"{base_dir}/**/himawari*/AHI-L1b-FLDK_*",
        ]:
            for match in glob.glob(pattern, recursive=True):
                match_path = Path(match)
                if match_path.is_dir():
                    sub_tif_files = list(match_path.glob("B??.tif"))
                    if sub_tif_files:
                        print(f"    [FOUND] {match_path} ({len(sub_tif_files)} bands)")
                        available = [f.stem for f in sub_tif_files]
                        if len(available) >= 3:
                            band_dirs.append(match_path)
                            print(f"    [VALID] {match_path} - Bands: {', '.join(sorted(available))}")
        
        # Fallback: Any directory with B??.tif files
        if not band_dirs:
            for tif_file in base_dir.rglob("B??.tif"):
                parent = tif_file.parent
                if parent not in band_dirs:
                    sub_tif_files = list(parent.glob("B??.tif"))
                    if len(sub_tif_files) >= 3:
                        band_dirs.append(parent)
                        print(f"    [FOUND] {parent} ({len(sub_tif_files)} bands)")

    band_dirs = list(set(band_dirs))
    print(f"[+] Found {len(band_dirs)} valid band directories")
    
    return band_dirs

def print_product_summary():
    print("\n" + "="*80)
    print("AVAILABLE RGB PRODUCTS")
    print("="*80)
    for key, info in PRODUCTS.items():
        print(f"\n{key.upper()}: {info['name']}")
        print(f"  Purpose: {info['description']}")
        print(f"  Bands: {', '.join(info['bands'])}")
        print(f"  Satpy: {info.get('use_satpy', False)}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Himawari Advanced RGB Generator")
    parser.add_argument("-i", "--input", required=True, help="Base directory")
    parser.add_argument("--products", default="all", help="Comma-separated products")
    parser.add_argument("--all", action="store_true", help="Create all products")
    parser.add_argument("--list-products", action="store_true", help="List products")
    parser.add_argument("--force-custom", action="store_true", help="Force custom implementation")
    args = parser.parse_args()

    if args.list_products:
        print_product_summary()
        sys.exit(0)

    if not RASTERIO_AVAILABLE:
        print("[!] ERROR: rasterio required. pip install rasterio")
        sys.exit(1)

    if args.force_custom:
        for p in PRODUCTS:
            PRODUCTS[p]["use_satpy"] = False

    base_dir = Path(args.input)
    if not base_dir.exists():
        print(f"[!] Directory not found: {base_dir}")
        sys.exit(1)

    print("\n" + "="*80)
    print("HIMAWARI RGB GENERATOR (FIXED VERSION)")
    print("="*80)
    print(f"Input: {base_dir}")
    print(f"Satpy available: {SATPY_AVAILABLE}")
    print(f"Rasterio available: {RASTERIO_AVAILABLE}")
    print("="*80)

    if args.all or args.products.lower() == "all":
        products_to_create = list(PRODUCTS.keys())
    else:
        products_to_create = [p.strip().lower() for p in args.products.split(',')]
        valid = [p for p in products_to_create if p in PRODUCTS]
        invalid = [p for p in products_to_create if p not in PRODUCTS]
        if invalid:
            print(f"[!] Invalid products: {invalid}")
            print(f"[!] Valid: {', '.join(PRODUCTS.keys())}")
            products_to_create = valid

    print(f"\nProducts to create: {', '.join(products_to_create)}")
    
    band_dirs = find_band_files(base_dir)
    if not band_dirs:
        print("[!] No band files found!")
        sys.exit(1)

    total_created = 0
    total_attempted = 0
    failed_products = []
    
    for i, band_dir in enumerate(band_dirs):
        print(f"\n{'='*80}")
        print(f"[{i+1}/{len(band_dirs)}] Processing: {band_dir}")
        print(f"{'='*80}")
        
        available = [f.stem for f in band_dir.glob("B??.tif")]
        print(f"Available bands: {', '.join(sorted(available))}")
        
        for pk in products_to_create:
            info = PRODUCTS[pk]
            required = info["bands"]
            total_attempted += 1
            
            # Check if all required bands are available
            missing = [b for b in required if b not in available]
            if missing:
                print(f"  [SKIP] {pk}: missing bands {missing}")
                continue
            
            print(f"  [TRY] Creating {pk}...")
            try:
                if create_rgb_product(pk, info, band_dir):
                    total_created += 1
                    print(f"  [OK] {pk} created successfully")
                else:
                    print(f"  [FAIL] {pk} creation failed")
                    failed_products.append((str(band_dir), pk))
            except Exception as e:
                print(f"  [ERROR] {pk}: {e}")
                failed_products.append((str(band_dir), pk))

    print("\n" + "="*80)
    print("RGB GENERATION SUMMARY")
    print("="*80)
    print(f"Band directories processed: {len(band_dirs)}")
    print(f"Products attempted: {total_attempted}")
    print(f"Products created: {total_created}")
    print(f"Products failed: {len(failed_products)}")
    
    if failed_products:
        print("\nFailed products:")
        for dir_path, product in failed_products:
            print(f"  - {product} in {dir_path}")
    
    print("="*80)
    
    # ------------------------- ADDED FOR GUI COMPATIBILITY -------------------------
    # These lines match the exact format expected by Process_dat.py's parse_script_output()
    print(f"Products created: {total_created}")
    print(f"STATISTICS_OUTPUT:")
    print(f"rgb_created: {total_created}")
    print(f"total_attempted: {total_attempted}")
    print(f"directories_processed: {len(band_dirs)}")
    # -------------------------------------------------------------------------------

    if total_created == 0:
        print("\n[!] WARNING: No RGB products were created!")
        sys.exit(1)
    
    print(f"\nCOMPLETE. Processed {len(band_dirs)} dirs, created {total_created} products")


if __name__ == "__main__":
    main()
