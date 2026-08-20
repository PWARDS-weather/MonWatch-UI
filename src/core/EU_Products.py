# =============================================================================
# MonWatch-UI Cyclone V3 â€” Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: core/EU_Products.py
# Description: RGB product definitions and spectral band configurations for satellite imagery compositing.
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


RGB_PRODUCTS = {
    "ms_airmass": {'name': 'Meteosat Air Mass', 'satellite': 'meteosat', 'bands': ['B07', 'B09', 'B10', 'B11'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B07', 'band2': 'B09', 'min': -25.8, 'max': 0, 'gamma': 1.0}, 'G': {'band1': 'B10', 'band2': 'B11', 'min': -41.5, 'max': 4.3, 'gamma': 1.0}, 'B': {'band': 'B07', 'min': 208.0, 'max': 242.6, 'gamma': 1.0, 'invert': True}}, 'description': 'Jet streams and air mass analysis for Meteosat SEVIRI.', 'tag': 'ALL'},
    "ms_infrared": {'name': 'Meteosat Infrared', 'satellite': 'meteosat', 'bands': ['B09'], 'single_band': True, 'formula': {'band': 'B09', 'min': None, 'max': None, 'gamma': 0.5, 'invert': True}, 'description': 'IR 10.8um brightness temperature for Meteosat.', 'tag': 'ALL'},
    "ms_natural": {'name': 'Meteosat Natural Color', 'satellite': 'meteosat', 'bands': ['B03', 'B02', 'B01'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B03', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}, 'G': {'band': 'B02', 'min': 0.0, 'max': 100.0, 'gamma': 2.95}, 'B': {'band': 'B01', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}}, 'description': 'Natural color for Meteosat SEVIRI.', 'tag': 'DAY'},
    "ms_dust": {'name': 'Meteosat Dust RGB', 'satellite': 'meteosat', 'bands': ['B11', 'B09', 'B10', 'B08'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B11', 'band2': 'B09', 'min': -7.5, 'max': 3, 'gamma': 1.0}, 'G': {'band1': 'B10', 'band2': 'B08', 'min': -0.5, 'max': 15.0, 'gamma': 2.2}, 'B': {'band': 'B09', 'min': 261.5, 'max': 289.2, 'gamma': 1.0}}, 'description': 'Dust detection for Meteosat SEVIRI.', 'tag': 'ALL'},
    "ms_night_microphysics": {'name': 'Meteosat Night Microphys.', 'satellite': 'meteosat', 'bands': ['B07', 'B09', 'B11', 'B01'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B11', 'band2': 'B09', 'min': -7.5, 'max': 3, 'gamma': 1}, 'G': {'band1': 'B01', 'band2': 'B07', 'min': -2.9, 'max': 7, 'gamma': 1}, 'B': {'band': 'B09', 'min': 243.7, 'max': 293.2, 'gamma': 1}}, 'description': 'Fog/low clouds detection for Meteosat.', 'tag': 'NIGHT'},
    "ms_pro_dvorak": {'name': 'Dvorak Enhancement', 'satellite': 'meteosat', 'color_scale': {'colormap': 'Dvorak (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 323.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Dvorak TC intensity enhancement via color scale (Meteosat).', 'tag': 'PROFESSIONAL'},
    "ms_pro_dvorak_experimental": {'name': 'Dvorak Experimental (Node)', 'satellite': 'meteosat', 'color_scale': {'colormap': 'Dvorak Experimental', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.15, 'vmax': 323.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Experimental Dvorak TC intensity enhancement via node-based color scale (Meteosat).', 'tag': 'PROFESSIONAL'},
    "ms_pro_bt_enhanced": {'name': 'BT Enhanced (IR)', 'satellite': 'meteosat', 'color_scale': {'colormap': 'BT Enhanced (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 323.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Enhanced brightness temperature IR via color scale (Meteosat).', 'tag': 'PROFESSIONAL'},
    "ms_pro_sandwich_ir_sataid": {'name': 'Sandwich IR (SATAID)', 'satellite': 'meteosat', 'color_scale': {'colormap': 'Sandwich IR (SATAID)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 343.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'SATAID Sandwich IR enhancement via Sandwich.dat 256-entry LUT (Meteosat).', 'tag': 'PROFESSIONAL'},
    "ms_pro_sandwich_sataid": {'name': 'Sandwich (SATAID)', 'satellite': 'meteosat', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'sandwich', 'sataid_ir': True, 'description': 'SATAID Sandwich: VIS underlaid with IR; cold clouds via Sandwich.dat 256-entry LUT. Auto day/night switching via SZA (Meteosat).', 'tag': 'ALL'},
    "ms_pro_jet": {'name': 'Jet Colormap', 'satellite': 'meteosat', 'color_scale': {'colormap': 'jet', 'mode': 'rad', 'gamma': 1.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Jet colormap applied to the active band (Meteosat).', 'tag': 'PROFESSIONAL'},
    "ms_pro_experimental_sst": {'name': 'Experimental SST (IR)', 'satellite': 'meteosat', 'color_scale': {'colormap': 'SST (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 273.15, 'vmax': 313.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Experimental sea surface temperature from IR bands. Yellow-red overlay for 0-40Â°C (Meteosat).', 'tag': 'PROFESSIONAL'},
    "ms_false_color": {'name': 'Meteosat False Color RGB', 'satellite': 'meteosat', 'bands': ['B09', 'B01'], 'channels': ['R', 'G', 'B'], 'special': 'false_color', 'description': 'VIS/IR false color composite with SZA normalization for Meteosat SEVIRI (VIS006 + IR108).', 'tag': 'ALL'},
    "ms_false_color_adv": {'name': 'Meteosat False Color (Advance)', 'satellite': 'meteosat', 'bands': ['B09', 'B01'], 'channels': ['R', 'G', 'B'], 'special': 'false_color_adv', 'description': 'VIS/IR false color advance composite for Meteosat SEVIRI. Enhanced RGB weighting for deep convective vs visible cloud discrimination.', 'tag': 'ALL'},
    "ms_true_color_unidata": {'name': 'True Color (Natural) (Unidata)', 'satellite': 'meteosat', 'bands': ['B02', 'B03', 'B01'], 'channels': ['R', 'G', 'B'], 'r_band': 'B02', 'g_band': 'B03', 'b_band': 'B01', 'ir_band': None, 'special': 'true_color_unidata', 'natural': True, 'contrast': 105, 'description': 'Unidata-style True Color for Meteosat SEVIRI (no native blue/green: natural-color substitute R=VIS008, G=IR016, B=VIS006). Clip, gamma 2.2. (day)', 'tag': 'DAY'},
    "ms_true_daynight_unidata": {'name': 'True Color Day/Night (Natural) (Unidata)', 'satellite': 'meteosat', 'bands': ['B09', 'B02', 'B03', 'B01'], 'channels': ['R', 'G', 'B'], 'r_band': 'B02', 'g_band': 'B03', 'b_band': 'B01', 'ir_band': 'B09', 'special': 'true_color_unidata', 'natural': True, 'contrast': 105, 'description': 'Unidata-style True Color by day with clean IR (B09/IR108) night cloud overlay for Meteosat SEVIRI. Auto day/night blend via solar zenith angle.', 'tag': 'ALL'},
}

def get_products_for_satellite(sat: str) -> dict:
    """Return RGB_PRODUCTS entries matching the given satellite family."""
    return RGB_PRODUCTS

_TAG_COLORS = {"DAY": "#E65100", "NIGHT": "#1A237E", "ALL": "#1B5E20", "PROFESSIONAL": "#6A1B9A"}
