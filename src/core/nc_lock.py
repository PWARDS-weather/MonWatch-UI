# =============================================================================
# MonWatch-UI Cyclone V3 — Satellite Imagery Analysis Workstation
# =============================================================================
#
# Module: core/nc_lock.py
# Description: Global re-entrant lock serializing access to the netCDF4/HDF5 C
# library. xarray's FileManager only serializes opens of the SAME file path;
# concurrent opens of different NetCDF files from multiple threads (animation
# prefetch workers, scene loading, playback) call the non-thread-safe HDF5 C
# library in parallel, which can raise a native 'Windows fatal exception:
# access violation' that cannot be caught by Python. All satellite band reads
# funnel through this lock so only one thread is inside netCDF4 at a time.
# RLock is used because engine read paths re-enter parser read functions.
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
# =============================================================================

import threading

netcdf_read_lock = threading.RLock()