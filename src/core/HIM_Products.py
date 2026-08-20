# =============================================================================
# MonWatch-UI Cyclone V3 â€” Satellite Imagery Analysis Workstation
# Meteorological Satellite Data Processing System for PWARDS-weather
# =============================================================================
#
# Organized and documented for open-source distribution and WMO compliance.
# Developed and field-tested since 2025 by PWARDS-weather.
# Operational since July 3, 2026.
#
# Module: core/HIM_Products.py
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
    "natural": {'name': 'Natural Color', 'satellite': 'himawari', 'bands': ['B05', 'B04', 'B03'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B05', 'min': 0.0, 'max': 99.0, 'gamma': 3.0}, 'G': {'band': 'B04', 'min': 0.0, 'max': 100.0, 'gamma': 2.95}, 'B': {'band': 'B03', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}}, 'description': 'Snow/ice cyan, vegetation green, clouds white. (reflectance %)', 'tag': 'DAY'},
    "true": {'name': 'True Color', 'satellite': 'himawari', 'bands': ['B03', 'B02', 'B01'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B03', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}, 'G': {'band': 'B02', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}, 'B': {'band': 'B01', 'min': 0.0, 'max': 100.0, 'gamma': 3.0}}, 'description': 'Rayleigh-corrected true color. (reflectance %)', 'tag': 'DAY'},
    "sandwich": {'name': 'Sandwich', 'satellite': 'himawari', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'sandwich', 'description': 'VIS underlaid with IR; cold clouds red/orange. Auto day/night switching via SZA.', 'tag': 'ALL'},
    "true_daynight": {'name': 'True Color Day/Night', 'satellite': 'himawari', 'bands': ['B13', 'B03', 'B02', 'B01'], 'channels': ['R', 'G', 'B'], 'special': 'true_daynight', 'description': 'True Color by day, IR night background with cold cloud overlay. Auto day/night switching via SZA.', 'tag': 'ALL'},
    "airmass": {'name': 'Air Mass', 'satellite': 'himawari', 'bands': ['B08', 'B10', 'B12', 'B13'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B08', 'band2': 'B10', 'min': -25.8, 'max': 0, 'gamma': 1.0}, 'G': {'band1': 'B12', 'band2': 'B13', 'min': -41.5, 'max': 4.3, 'gamma': 1.0}, 'B': {'band': 'B08', 'min': 208.0, 'max': 242.6, 'gamma': 1.0, 'invert': True}}, 'description': 'Jet streams, tropopause folds, WV.', 'tag': 'ALL'},
    "dust": {'name': 'Dust RGB', 'satellite': 'himawari', 'bands': ['B15', 'B13', 'B14', 'B11'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B15', 'band2': 'B13', 'min': -7.5, 'max': 3, 'gamma': 1.0}, 'G': {'band1': 'B14', 'band2': 'B11', 'min': -0.5, 'max': 15.0, 'gamma': 2.2}, 'B': {'band': 'B13', 'min': 261.5, 'max': 289.2, 'gamma': 1.0}}, 'description': 'Dust magenta/pink; thin cirrus dark; thick clouds brown.', 'tag': 'ALL'},
    "day_convection": {'name': 'Day Convection', 'satellite': 'himawari', 'bands': ['B05', 'B03', 'B07', 'B08', 'B10', 'B13'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B08', 'band2': 'B10', 'min': -35.0, 'max': 5.0, 'gamma': 1.0}, 'G': {'band1': 'B07', 'band2': 'B13', 'min': -5.0, 'max': 60.0, 'gamma': 0.5}, 'B': {'band1': 'B03', 'band2': 'B05', 'min': -10.0, 'max': 70.0, 'gamma': 0.95, 'invert': True}}, 'description': 'Intense convection yellow; small ice crystals bright.', 'tag': 'DAY'},
    "fire": {'name': 'Fire Temp', 'satellite': 'himawari', 'bands': ['B07', 'B06', 'B05'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B07', 'min': 273.0, 'max': 350.0, 'gamma': 1.0}, 'G': {'band': 'B06', 'min': 0.0, 'max': 50.0, 'gamma': 1.0}, 'B': {'band': 'B05', 'min': 0.0, 'max': 50.0, 'gamma': 1.0}}, 'description': 'Hot spots red/yellow; burn scars dark.', 'tag': 'DAY'},
    "night_microphysics": {'name': 'Night Microphys.', 'satellite': 'himawari', 'bands': ['B10', 'B13', 'B15', 'B03'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B15', 'band2': 'B13', 'min': -7.5, 'max': 3, 'gamma': 1}, 'G': {'band1': 'B03', 'band2': 'B10', 'min': -2.9, 'max': 7, 'gamma': 1}, 'B': {'band': 'B13', 'min': 243.7, 'max': 293.2, 'gamma': 1}}, 'description': 'Fog/low clouds aqua; high ice clouds red/purple.', 'tag': 'NIGHT'},
    "cloud_phase": {'name': 'Cloud Phase', 'satellite': 'himawari', 'bands': ['B05', 'B06', 'B02'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B05', 'min': 1, 'max': 50, 'gamma': 1.0}, 'G': {'band': 'B06', 'min': 1, 'max': 86, 'gamma': 1.0}, 'B': {'band': 'B02', 'min': 0, 'max': 86, 'gamma': 1.0}}, 'description': 'Ice clouds blue/cyan; liquid water white/green. (reflectance %)', 'tag': 'DAY'},
    "infrared": {'name': 'Infrared', 'satellite': 'himawari', 'bands': ['B13'], 'single_band': True, 'formula': {'band': 'B13', 'min': None, 'max': None, 'gamma': 0.5, 'invert': True}, 'description': 'Cold clouds white; warm ground black.', 'tag': 'ALL'},
    "day_microphysics": {'name': 'Day Microphys.', 'satellite': 'himawari', 'bands': ['B07', 'B04', 'B13'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B04', 'min': 0, 'max': 121.4829, 'gamma': 0.95}, 'G': {'band': 'B07', 'min': 196.5965, 'max': 352.6322, 'gamma': 1}, 'B': {'band': 'B13', 'min': 203.5, 'max': 303.2, 'gamma': 1.0}}, 'description': 'Ice vs water cloud discrimination.', 'tag': 'DAY'},
    "volcanic_ash": {'name': 'Ash RGB', 'satellite': 'himawari', 'bands': ['B11', 'B13', 'B14', 'B15'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B15', 'band2': 'B13', 'min': -7.5, 'max': 3.0, 'gamma': 1.0}, 'G': {'band1': 'B14', 'band2': 'B11', 'min': -5.9, 'max': 5.1, 'gamma': 0.5}, 'B': {'band': 'B13', 'min': 243.6, 'max': 303.2, 'gamma': 1.0}}, 'description': 'Ash cloud detection and tracking.', 'tag': 'ALL'},
    "Day_Deep": {'name': 'Day Deep Clouds', 'satellite': 'himawari', 'bands': ['B08', 'B13', 'B03'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B08', 'band2': 'B13', 'min': -35, 'max': 5, 'gamma': 1.0}, 'G': {'band': 'B03', 'min': 70, 'max': 100, 'gamma': 2}, 'B': {'band': 'B13', 'min': 243.6, 'max': 292.6, 'gamma': 1.0}}, 'description': 'Ice-top storms cyan; warm-top storms yellow.', 'tag': 'DAY'},
    "dif_wat": {'name': 'Differential Water Vapor', 'satellite': 'himawari', 'bands': ['B10', 'B08'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band1': 'B10', 'band2': 'B08', 'min': -3.0, 'max': 30.0, 'gamma': 0.7, 'invert': True}, 'G': {'band': 'B10', 'min': 213.2, 'max': 278.2, 'gamma': 0.7, 'invert': True}, 'B': {'band': 'B08', 'min': 208.5, 'max': 243.9, 'gamma': 0.7, 'invert': True}}, 'description': 'Fog yellow; clear dark; high cloud red.', 'tag': 'ALL'},
    "day_snowfog": {'name': 'Day Snow-Fog', 'satellite': 'himawari', 'bands': ['B04', 'B06', 'B07'], 'channels': ['R', 'G', 'B'], 'formula': {'R': {'band': 'B04', 'min': 0, 'max': 121.4829, 'gamma': 1}, 'G': {'band': 'B06', 'min': 0, 'max': 90.1865, 'gamma': 1}, 'B': {'band': 'B07', 'min': 196.5965, 'max': 352.6322, 'gamma': 1}}, 'description': 'Fog detection in daytime (reflectance differences).', 'tag': 'DAY'},
    "pro_dvorak": {'name': 'Dvorak Enhancement', 'satellite': 'himawari', 'color_scale': {'colormap': 'Dvorak (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 323.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Dvorak TC intensity enhancement via color scale.', 'tag': 'PROFESSIONAL'},
    "pro_dvorak_experimental": {'name': 'Dvorak Experimental (Node)', 'satellite': 'himawari', 'color_scale': {'colormap': 'Dvorak Experimental', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.15, 'vmax': 323.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Experimental Dvorak TC intensity enhancement via node-based color scale.', 'tag': 'PROFESSIONAL'},
    "pro_bt_enhanced": {'name': 'BT Enhanced (IR)', 'satellite': 'himawari', 'color_scale': {'colormap': 'BT Enhanced (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 323.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Enhanced brightness temperature IR via color scale.', 'tag': 'PROFESSIONAL'},
    "pro_sandwich_ir": {'name': 'Sandwich IR', 'satellite': 'himawari', 'color_scale': {'colormap': 'Sandwich (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 323.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Sandwich IR enhancement via color scale.', 'tag': 'PROFESSIONAL'},
    "pro_sandwich_ir_sataid": {'name': 'Sandwich IR (SATAID)', 'satellite': 'himawari', 'color_scale': {'colormap': 'Sandwich IR (SATAID)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 173.0, 'vmax': 343.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'SATAID Sandwich IR enhancement via Sandwich.dat 256-entry LUT (-73.15 to -33.15 C).', 'tag': 'PROFESSIONAL'},
    "pro_sandwich_sataid": {'name': 'Sandwich (SATAID)', 'satellite': 'himawari', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'sandwich', 'sataid_ir': True, 'description': 'SATAID Sandwich: VIS underlaid with IR; cold clouds via Sandwich.dat 256-entry LUT. Auto day/night switching via SZA.', 'tag': 'ALL'},
    "pro_jet": {'name': 'Jet Colormap', 'satellite': 'himawari', 'color_scale': {'colormap': 'jet', 'mode': 'rad', 'gamma': 1.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Jet colormap applied to the active band.', 'tag': 'PROFESSIONAL'},
    "pro_viridis": {'name': 'Viridis Colormap', 'satellite': 'himawari', 'color_scale': {'colormap': 'viridis', 'mode': 'rad', 'gamma': 1.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Viridis colormap applied to the active band.', 'tag': 'PROFESSIONAL'},
    "pro_inferno": {'name': 'Inferno Colormap', 'satellite': 'himawari', 'color_scale': {'colormap': 'inferno', 'mode': 'rad', 'gamma': 1.0}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Inferno colormap applied to the active band.', 'tag': 'PROFESSIONAL'},
    "pro_experimental_sst": {'name': 'Experimental SST (IR)', 'satellite': 'himawari', 'color_scale': {'colormap': 'SST (IR)', 'mode': 'bt', 'gamma': 1.0, 'vmin': 273.15, 'vmax': 313.15}, 'bands': [], 'channels': ['R', 'G', 'B'], 'description': 'Experimental sea surface temperature from IR bands. Yellow-red overlay for 0-40Â°C.', 'tag': 'PROFESSIONAL'},
    "false_color": {'name': 'False Color RGB', 'satellite': 'himawari', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'false_color', 'description': 'VIS/IR false color composite with SZA normalization. Vegetation green, convective clouds pink.', 'tag': 'ALL'},
    "false_color_adv": {'name': 'False Color (Advance)', 'satellite': 'himawari', 'bands': ['B13', 'B03'], 'channels': ['R', 'G', 'B'], 'special': 'false_color_adv', 'description': 'VIS/IR false color advance composite. Enhanced RGB weighting for deep convective vs visible cloud discrimination.', 'tag': 'ALL'},
    "true_color_unidata": {'name': 'True Color (Unidata)', 'satellite': 'himawari', 'bands': ['B03', 'B04', 'B01'], 'channels': ['R', 'G', 'B'], 'r_band': 'B03', 'g_band': 'B04', 'b_band': 'B01', 'ir_band': None, 'special': 'true_color_unidata', 'natural': False, 'contrast': 105, 'description': 'Unidata True Color Recipe: clip, gamma 2.2, CIMSS synthetic green. Himawari R=B03(0.64), G=B04(0.86), B=B01(0.47) reflectance. (day)', 'tag': 'DAY'},
    "true_daynight_unidata": {'name': 'True Color Day/Night (Unidata)', 'satellite': 'himawari', 'bands': ['B13', 'B03', 'B04', 'B01'], 'channels': ['R', 'G', 'B'], 'r_band': 'B03', 'g_band': 'B04', 'b_band': 'B01', 'ir_band': 'B13', 'special': 'true_color_unidata', 'natural': False, 'contrast': 105, 'description': 'Unidata True Color by day with clean IR (B13) night cloud overlay. Auto day/night blend via solar zenith angle.', 'tag': 'ALL'},
}

def get_products_for_satellite(sat: str) -> dict:
    """Return RGB_PRODUCTS entries matching the given satellite family."""
    return RGB_PRODUCTS

_TAG_COLORS = {"DAY": "#E65100", "NIGHT": "#1A237E", "ALL": "#1B5E20", "PROFESSIONAL": "#6A1B9A"}
