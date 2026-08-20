# =============================================================================
# MonWatch-UI Cyclone V3 â€” Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: core/GK2A_Products.py
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
    "gk2a_true": {'name': 'GK-2A True Color', 'satellite': 'gk2a', 'bands': ['B03', 'B02', 'B01'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B03', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}, 'G': {'band': 'B02', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}, 'B': {'band': 'B01', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}}, 'description': 'True color RGB for GK-2A AMI (reflectance %).', 'tag': 'DAY'},
    "gk2a_natural": {'name': 'GK-2A Natural Color', 'satellite': 'gk2a', 'bands': ['B05', 'B04', 'B03'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B05', 'min': 0.0, 'max': 99.0, 'gamma': 3.0}, 'G': {'band': 'B04', 'min': 0.0, 'max': 100.0, 'gamma': 2.95}, 'B': {'band': 'B03', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}}, 'description': 'Natural color for GK-2A (snow/ice cyan, vegetation green).', 'tag': 'DAY'},
    "gk2a_infrared": {'name': 'GK-2A Infrared', 'satellite': 'gk2a', 'bands': ['B13'], 'single_band': True, 'formula': {'band': 'B13', 'min': None, 'max': None, 'gamma': 0.5, 'invert': True}, 'description': 'IR brightness temperature for GK-2A.', 'tag': 'ALL'},
    "gk2a_airmass": {'name': 'GK-2A Air Mass', 'satellite': 'gk2a', 'bands': ['B08', 'B10', 'B12', 'B13'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B08', 'band2': 'B10', 'min': -25.8, 'max': 0, 'gamma': 1.0}, 'G': {'band1': 'B12', 'band2': 'B13', 'min': -41.5, 'max': 4.3, 'gamma': 1.0}, 'B': {'band': 'B08', 'min': 208.0, 'max': 242.6, 'gamma': 1.0, 'invert': True}}, 'description': 'Jet streams, tropopause folds, WV for GK-2A.', 'tag': 'ALL'},
    "gk2a_sandwich": {'name': 'GK-2A Sandwich', 'satellite': 'gk2a', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'sandwich', 'description': 'VIS underlaid with IR; cold clouds red/orange for GK-2A.', 'tag': 'ALL'},
    "gk2a_true_daynight": {'name': 'GK-2A True Color Day/Night', 'satellite': 'gk2a', 'bands': ['B13', 'B03', 'B02', 'B01'], 'channels': ['R', 'G', 'B'], 'special': 'true_daynight', 'description': 'True Color by day, IR night background with cold cloud overlay for GK-2A.', 'tag': 'ALL'},
    "gk2a_dust": {'name': 'GK-2A Dust RGB', 'satellite': 'gk2a', 'bands': ['B15', 'B13', 'B14', 'B11'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B15', 'band2': 'B13', 'min': -7.5, 'max': 3, 'gamma': 1.0}, 'G': {'band1': 'B14', 'band2': 'B11', 'min': -0.5, 'max': 15.0, 'gamma': 2.2}, 'B': {'band': 'B13', 'min': 261.5, 'max': 289.2, 'gamma': 1.0}}, 'description': 'Dust magenta/pink for GK-2A.', 'tag': 'ALL'},
    "gk2a_pro_dvorak": {'name': 'Dvorak Enhancement', 'satellite': 'gk2a', 'color_scale': {'colormap': 'Dvorak (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 323.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Dvorak TC intensity enhancement via color scale (GK-2A).', 'tag': 'PROFESSIONAL'},
    "gk2a_pro_dvorak_experimental": {'name': 'Dvorak Experimental (Node)', 'satellite': 'gk2a', 'color_scale': {'colormap': 'Dvorak Experimental', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.15, 'vmax': 323.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Experimental Dvorak TC intensity enhancement via node-based color scale (GK-2A).', 'tag': 'PROFESSIONAL'},
    "gk2a_pro_bt_enhanced": {'name': 'BT Enhanced (IR)', 'satellite': 'gk2a', 'color_scale': {'colormap': 'BT Enhanced (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 323.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Enhanced brightness temperature IR via color scale (GK-2A).', 'tag': 'PROFESSIONAL'},
    "gk2a_pro_sandwich_ir_sataid": {'name': 'Sandwich IR (SATAID)', 'satellite': 'gk2a', 'color_scale': {'colormap': 'Sandwich IR (SATAID)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 343.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'SATAID Sandwich IR enhancement via Sandwich.dat 256-entry LUT (GK-2A).', 'tag': 'PROFESSIONAL'},
    "gk2a_pro_sandwich_sataid": {'name': 'Sandwich (SATAID)', 'satellite': 'gk2a', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'sandwich', 'sataid_ir': True, 'description': 'SATAID Sandwich: VIS underlaid with IR; cold clouds via Sandwich.dat 256-entry LUT. Auto day/night switching via SZA (GK-2A).', 'tag': 'ALL'},
    "gk2a_pro_jet": {'name': 'Jet Colormap', 'satellite': 'gk2a', 'color_scale': {'colormap': 'jet', 'mode': 'rad', 'gamma': 1.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Jet colormap applied to the active band (GK-2A).', 'tag': 'PROFESSIONAL'},
    "gk2a_pro_experimental_sst": {'name': 'Experimental SST (IR)', 'satellite': 'gk2a', 'color_scale': {'colormap': 'SST (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 273.15, 'vmax': 313.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Experimental sea surface temperature from IR bands. Yellow-red overlay for 0-40Â°C (GK-2A).', 'tag': 'PROFESSIONAL'},
    "gk2a_false_color": {'name': 'GK-2A False Color RGB', 'satellite': 'gk2a', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'false_color', 'description': 'VIS/IR false color composite with SZA normalization for GK-2A AMI.', 'tag': 'ALL'},
    "gk2a_false_color_adv": {'name': 'GK-2A False Color (Advance)', 'satellite': 'gk2a', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'false_color_adv', 'description': 'VIS/IR false color advance composite for GK-2A AMI. Enhanced RGB weighting for deep convective vs visible cloud discrimination.', 'tag': 'ALL'},
    "gk2a_true_color_unidata": {'name': 'True Color (Unidata)', 'satellite': 'gk2a', 'bands': ['B03', 'B04', 'B01'], 'channels': ['R', 'G', 'B'], 'r_band': 'B03', 'g_band': 'B04', 'b_band': 'B01', 'ir_band': None, 'special': 'true_color_unidata', 'natural': False, 'contrast': 105, 'description': 'Unidata True Color Recipe: clip, gamma 2.2, CIMSS synthetic green. GK-2A R=B03(0.64), G=B04(0.86), B=B01(0.47) reflectance. (day)', 'tag': 'DAY'},
    "gk2a_true_daynight_unidata": {'name': 'True Color Day/Night (Unidata)', 'satellite': 'gk2a', 'bands': ['B13', 'B03', 'B04', 'B01'], 'channels': ['R', 'G', 'B'], 'r_band': 'B03', 'g_band': 'B04', 'b_band': 'B01', 'ir_band': 'B13', 'special': 'true_color_unidata', 'natural': False, 'contrast': 105, 'description': 'Unidata True Color by day with clean IR (B13) night cloud overlay for GK-2A AMI. Auto day/night blend via solar zenith angle.', 'tag': 'ALL'},
}

def get_products_for_satellite(sat: str) -> dict:
    """Return RGB_PRODUCTS entries matching the given satellite family."""
    return RGB_PRODUCTS

_TAG_COLORS = {"DAY": "#E65100", "NIGHT": "#1A237E", "ALL": "#1B5E20", "PROFESSIONAL": "#6A1B9A"}
