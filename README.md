# MonWatch-UI Cyclone V3

**Satellite Imagery Analysis Workstation for Meteorological Data Processing**

MonWatch-UI lets you quickly explore raw geostationary satellite data — no web viewer required. Navigate by date, time, and spectral band, preview imagery at multiple quality levels, and view data with CRS georeferencing (lat/lon grid + coastlines) directly from AWS buckets and other sources.

> **Developed by [PWARDS-weather](https://github.com/PWARDS-weather)** — the Pasacao Weather Atmospheric and Real-Time Data System.  
> **Established**: 2025  
> **Status**: Operational and field-tested since July 3, 2026  

---

<img width="1280" height="640" alt="MonWatch-poster" src="https://github.com/user-attachments/assets/cc625f59-a039-4461-a4b3-12d0a413cc53" />

---

<h1 align="center" style="font-size: 3rem; font-weight: 900;">
  <img 
    width="32" 
    height="32" 
    alt="MONWATCH-UI" 
    src="https://github.com/user-attachments/assets/76cd20da-59a5-4b8f-9390-1d7b6525b460"
    style="vertical-align: middle; margin-right: 8px;"
  >
  <img 
    width="32" 
    height="32" 
    alt="splash" 
    src="https://github.com/user-attachments/assets/a7821feb-77fc-4c9d-906a-3cb48aa0e555"
    style="vertical-align: middle; margin-right: 8px;"
  >
 MonWatch-UI 3.0.5.1 – The "Patch" Update
</h1>

**Release date:** August 20, 2026

---

## 🐛 Bug Fixes
- **Packaging**: Resolved several packaging issues that caused installation failures on certain systems.
- **General**: Fixed minor glitches in data loading and UI responsiveness.
- **Georeferencing**: Improved CRS coordinate accuracy for multi-satellite projections.
- **Cache Management**: Fixed pyramidal cache generation bugs affecting multi-resolution access.

---

## 🚀 Full Features

### Core Satellite & Data Integration
- **Multi-Satellite Support**: Full integration with **Himawari 8/9** and **GOES 16–19**, with drag-and-drop loading of AWS files directly into the UI. **GK-2A** and **Meteosat** ingestion is implemented and being completed (RGB processing for these two is still in progress).
- **16 Spectral Bands**: Complete access to B01–B16 / C01–C16 with multi-quality preview (0.25×, 0.5×, 1× resolution).
- **Satpy Integration**: Built on **Satpy** for advanced scene reading and RGB product generation, enabling custom composite creation and false-color imagery alongside the embedded rendering engine.
- **Multiple Scan Sectors**: Full Disk, Japan, Target (Himawari); full-disk coverage for GOES with mesoscale focus options.
- **RGB Composite Products** (28+ defined):
  - **Daytime**: True Color, Natural Color, Day Convection, Day Microphysics, Day Snow-Fog, Fire Temp, Cloud Phase, Day Deep Clouds
  - **Nighttime**: Night Microphysics, Infrared (single-band)
  - **All-weather**: Sandwich, Air Mass, Dust RGB, Volcanic Ash, Differential Water Vapor
  - **GOES-specific**: True Color, True Color Day/Night, Natural-SWIR and Cloud Phase substitutions
  - **Professional color scales**: Dvorak, Dvorak Experimental, BT Enhanced IR, Sandwich IR, Sandwich IR (SATAID LUT), jet/viridis/inferno colormaps, Experimental SST
- **SATAID-format reading**: Direct loading of SATAID scenes (SZDD decompression) with an on-screen SATAID control panel.

### Data Sources
- **WIS2Box** (WMO OGC API) data and metadata access
- **PWARDS stream API** for SATAID-format satellite streaming
- **JAXA FTP (P-Tree)** browsing and direct cloud access to HSD/NetCDF data
- **Dataset tools**: standalone data downloader with AWS S3 + FTP browsing, date/time/band discovery, range downloads, and multi-worker concurrency; raw HSD/DAT to NetCDF conversion with CRS/geotransform generation and calibration.

### Tropical Cyclone Tools
- **Multi-Agency Forecast Data**:
  - NHC (Atlantic/EPAC)
  - JMA (WPAC)
  - JTWC (WPAC/IO)
  - PAGASA (Philippines)
  - CWA (Taiwan)
- **ATCF Data Integration**: Via KnackWX API with forecast track visualization and cone of uncertainty.
- **Styled Forecast Track Generation**: Customizable appearance for agencies including **PAGASA**, **JMA**, **JTWC**, **PWARDS**, **MonWatch-UI**, **NHC**, and **Develope** layouts, plus **Combined Forecast Tracks** merging multiple agencies/storm forecasts on a single map.
- **Historical Best-Track Display**: Complete storm history visualization.
- **Wind Radii Quadrants**: 34/50/64 kt wind field display.
- **Probability Circles & Storm Warning Areas**: Enhanced situational awareness.
- **Cross-Agency Storm Correlation**: Unified track management across all agencies.
- **Custom Meteorological Track Creation**: Drag-and-drop point editing for manual track adjustments with intensity category selection.
- **Smart Label Placement**: 12+ automated labeling algorithms for clean storm point labels (bezier, annealing, force-directed, integer programming, leader lines, and more).

### Satellite Data Related Tools
- **Microwave & Scatterometer**: AMSR2 brightness temperatures, ATMS overpasses, MIMIC TPW, VIIRS DNB overlays, ASCAT ocean-surface winds with KNMI and PO.DAAC swath downloads, and regional wind-band products.
- **Recon Aircraft Tracking**: Live NOAA Hurricane Hunters / USAF WC-130J flight tracking via ADS-B exchange.
- **Weather Alert Map Generation**: PAGASA, NHC, and JMA alert maps with Philippine province/municipality boundaries from the PSGC.

### Animation & Playback
- **Multi-Frame Animation**: Configurable time steps (10 min, 30 min, 1 hr, 3 hr).
- **Background Prefetching**: Parallel I/O for smooth playback.
- **Play/Pause/Speed Control**: Adjustable from 0.5× to 8× speed.
- **Frame Scrubbing**: Timeline slider with precise frame navigation.
- **Loop Mode Toggle**: Continuous playback option.
- **Storm-Following Tracking**: Auto-center on a geographic target or ATCF storm across frames (Target sector).
- **Broadcast Production**: Dedicated broadcast mode with a keyframe timeline, camera animation (position, zoom, heading, pitch), easing, and project save/load. (still in development) 

### Geospatial Overlays
- **Lat/Lon Grid**: Multiple projections — geostationary, equirectangular, Mercator, and Plate Carrée — with configurable spacing, style, opacity, and line width.
- **Coastlines**: Shapefile-based rendering with styling options.
- **Areas of Responsibility (AoR)**: PAR, JMA, TCAD, TCID, Manila FIR, and Custom user-defined boundaries.
- **Political Boundaries**: Philippines admin boundaries (PSGC regions/provinces/municipalities/barangays) with hatch styling.
- **Climate Data Overlays**: CPC weekly global hazard outlooks (TC, WET, DRY, WARM, COLD) with week selection, plus global/regional NHC probability shading.
- **Atmospheric Motion Vectors (AMV)**: Wind barb visualization from embedded wind NetCDF data (GRIB/NetCDF supported).
- **Temperature Readout**: Live temperature-at-cursor sampling with IR Kelvin conversion and on-image temperature markers.
- **Projection Modes**: Full Disk, Equirectangular, and **Flat** (Plate Carrée) projection with GPU-accelerated rendering.

### Weather Alert System
- **NWS Weather Alerts**: Configurable polling interval for USA alerts.
- **PAGASA Tropical Cyclone Alerts**: Real-time Philippine warning system alerts.
- **Severity Levels**: Extreme, Severe, Moderate, Minor with color-coded indicators.
- **Emergency Popup Dialogs**: Flashing border for critical alerts.
- **System Tray Notifications**: Critical priority alerts (Windows 10/11).
- **Text-to-Speech Alert Reading**: Cross-platform audio notifications for critical events.
- **Alert Acknowledgment**: Persistence tracking for acknowledged alerts.
- **Alert Map Generation**: Automatic regional highlight with color-coded severity zones.

### Export Capabilities
- **Current View Export**: PNG with geospatial footer.
- **Bitmap Export**: PNG/JPEG/BMP via file dialog.
- **Serial Bitmap Export**: All bands as individual files.
- **GeoTIFF Export**: Fully georeferenced RGBA rasters with CRS, geotransform, and metadata tags.
- **Animation Export**: MP4, GIF, AVI with configurable FPS and animated footer strip.
- **Forecast Map Export**: Cartography-rendered track maps with cones and labels.
- **Climate Overlay Export**: Individual climate product maps.
- **Quick Generate Region Captures**: Philippines, Japan, PWARDS, West Pacific (Ocean), West Pacific (Whole), Australia, and ATCF Target Areas with flat-projection options.
- **Geospatial Footers**: Coordinates, timestamp, band info, logo, agency branding.

### UI/UX Features
- **Theme Support**: 9 built-in themes (Dark, Light, Midnight Blue, Solarized Dark, High Contrast, Dracula, Nord, Monokai, Warm Amber) with custom accents.
- **Customizable Viewport**: Background color selection.
- **Professional Tab**: Devkit-style channel grid, professional presets, foreground/background emphasis, band statistics, brightness-temperature colormaps and enhancements.
- **Mode Switching**: Professional / Casual / Hobby / SATAID modes.
- **Multi-Viewport**: Synchronized windows for viewport mirroring, single bands, animation, forecast maps, and 3D globe.
- **Tear-Off Tabs**: Detach any panel or tab into its own floating window.
- **Dockable Panels**: Auto-hide and float functionality.
- **Real-Time Cursor Readout**: Live coordinate display.
- **Enhanced Keyboard Shortcuts**: Quick actions for zoom, pan, and layer toggles.
- **System Tray Integration**: Windows 10/11 support.
- **Accounts Management**: WIS2Box, JAXA FTP, and data API accounts with test connections.

### Analysis & Advanced Tools
- **Contour Analysis Tool**: Multiple algorithms (MonWatch, SATAID) with custom levels and Gaussian blur smoothing.
- **Overlay Display Mode**: Contour overlay capability on the scene.
- **AoT — Areas of Target**: Target boxes for tropical cyclones using ATCF data.
- **3D Globe Visualization**: Rotatable globe with equirectangular-reprojected satellite imagery.
- **GPU/CPU Compute Backend**: CuPy GPU acceleration with Numba JIT CPU fallback.

---

*This update is a patch release that also includes the above feature enhancements, plus earlier planned features that have already been completed ahead of schedule.*

---

## 🔜 Upcoming Features (Post v3.0.5.1)

- SATAID UI additional functions & fixes
- Completion of GK-2A and Meteosat RGB processing
- MetraWeather Lightning & Point Forecast integration
- Additional geostationary satellites and improved NWP/model outputs — full GRIB2 integration for model overlays
- Sea Surface Temperature (SST) — real-time layer & analysis
- Reliable AoR/PAR overlays on SATAID scenes
- Enhanced multi-viewport & viewport stacking (multiple bands/models, synchronized animation)
- Improved 3D globe — multi-satellite global rotatable view
- Bird's-eye view — perspective viewing angle
- Diagnostic tools — sounding profiles, cross-sections, storm-relative motion
- Analysis tools — isotherm/isobar drawing, distance/area measurement

---

*Coming Up Next: v3.0.6*

---

## Screenshots

---

<img width="1358" height="642" alt="QG_PWARDS Region_20260705_161856" src="https://github.com/user-attachments/assets/cd194f97-63b6-4978-ba7c-9526102e800f" />

---

<img width="2495" height="1571" alt="QG_Philippines Region_20260705_155523" src="https://github.com/user-attachments/assets/d0bc51f8-0062-4f8c-b6ea-342c1d1801bb" />

---

<img width="1000" height="1000" alt="QG_BAVI_20260705_145144" src="https://github.com/user-attachments/assets/44ddaebb-7748-4305-a63c-f6ff1f7bcf6e" />

---

<img width="1000" height="1000" alt="QG_BAVI_20260705_162053" src="https://github.com/user-attachments/assets/4fb2595d-8921-4f51-a04a-06a9b90f86d8" />

---

<img width="1000" height="1000" alt="QG_INVEST 97P_20260705_122330" src="https://github.com/user-attachments/assets/8a9cf879-46e2-415f-8520-465a88143ced" />

---

<img width="2486" height="1540" alt="QG_Westpac_20260705_160022" src="https://github.com/user-attachments/assets/acd1ab3a-074d-4c59-8506-ae227512223d" />

---

<img width="1200" height="707" alt="Rainfall-Moderate-NCRPRSD-202606301700" src="https://github.com/user-attachments/assets/9238d0ba-4a6e-40d2-8c65-4a106ad77bf2" />

---

<img width="708" height="710" alt="QG_Viewport_20260704_162944" src="https://github.com/user-attachments/assets/35aef485-3148-49a0-bcf6-e46474fe2942" />

---
---

## Requirements

- **Windows** 10 or 11 (*Windows-first build*)
- **Linux / macOS** — supported via `run.sh` or `python -m monwatch`
- Internet connection (for AWS data access)
- No Python pre-installation needed on Windows — `run.bat` sets it up automatically

---

## Installation

### Windows (quick start)

1. Clone or download this repository:
   ```
   git clone https://github.com/PWARDS-weather/MonWatch-UI.git
   cd MonWatch-UI
   ```

2. Run `run.bat` (double-click or right-click → Run as administrator if needed)

3. The app launches automatically after setup

### Install as a Python package (contributors / all platforms)

MonWatch-UI ships as an installable Python package. To install it directly:

```
pip install -e ".[dev]"      # editable install + development tools
pip install -e ".[gpu]"      # optional GPU acceleration (CUDA 12.x)
monwatch                      # launch via the console script
python -m monwatch            # or via the module entry point (`python -m src` also works)
./run.sh                      # or the cross-platform bootstrap (Linux/macOS)
```

---

## Usage

### Drag and Drop
Drag any AWS satellite `.tif`, NetCDF, HSD/DAT, or SATAID file into the main panel to load and preview it.

### Manual Navigation
Click **Open Folder**, then navigate into a date folder → time folder → spectral band to browse available imagery.

### Data Source
Imagery is sourced from AWS-hosted NOAA Himawari buckets and JAXA; tropical cyclone and alert data come from NHC, JMA, JTWC, PAGASA, CWA, and NWS feeds. An internet connection is required to stream data.

---

## Dependencies

| Package | Purpose |
|---|---|
| PySide6 | UI framework |
| Pillow | Image processing |
| tifffile | TIFF/GeoTIFF reading |
| numpy / scipy | Array & scientific operations |
| xarray / netCDF4 | NetCDF data handling |
| rasterio | Geospatial raster operations |
| scikit-image | Image resampling |
| satpy / metpy | Satellite scene reading & meteorological calculations |
| pyproj | Coordinate reference system transformations |
| cartopy | Map projections and cartography |
| shapely / pyshp | Geometry & shapefile handling |
| boto3 / requests | AWS & network access |
| matplotlib | Visualization |
| imageio / ffmpeg | Animation & video export |

There is also optional GPU acceleration (CuPy) and Numba JIT compilation for the compute backend, plus optional `sataid` and `sounderpy` support packages.

---

## Why Windows-first?

MonWatch-UI has been developed and field-tested primarily on Windows 10/11 to serve its target users (meteorology students, researchers, and hobbyists in the Philippines), where Windows is the dominant desktop OS and the environment around JMA/PAGASA satellite tooling is commonly Windows-based. The application is not Windows-only in design:

- Linux build scripts are included.
- `python -m monwatch` and the console script launch the app on any OS with a matching Python environment.
- Windows-only system APIs (window-show detection, background launching, PowerShell sound/TTS) are guarded so the core app runs elsewhere.

## Architecture

MonWatch-UI follows a modular architecture with domain-specific controllers:

- **UI Layer** — Main application window with controller delegation
- **Controllers** — Business logic for alerts, animation, climate, export, forecast, overlays, and satellite data
- **Services** — Shared domain services for contouring, projections, and georeferencing
- **Data Clients** — Agency and provider API integrations (NHC, JMA, JTWC, PAGASA, CWA, NWS, WIS2Box, AWS, JAXA)
- **Workers** — Background threading for caching, prefetching, and exports
- **Core Engine** — Computation backend with NumPy/CuPy support and the RGB compositing engine
- **Processes** — Standalone offline tools for data conversion, forecast map generation, alert maps, climate maps, and cache building

---

## Contributing

Contributions are welcome! To get started:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Set up a dev environment: `pip install -e ".[dev]"` then `pre-commit install`
4. Make your changes, run `ruff check` + `ruff format` on your files, and test them
5. Open a Pull Request with a clear description of what you changed

**Important**: Before submitting modifications, please review the [Developer Notification Policy](#license) below.

For bugs or feature requests, please [open an issue](https://github.com/PWARDS-weather/MonWatch-UI/issues).

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

### Contributing to Satpy / SIFT upstream

MonWatch-UI builds on [Satpy](https://github.com/pytroll/satpy) for satellite
reading and compositing. If you write functionality that belongs in a shared
library rather than this app, please **file an issue or open a pull request
upstream**:

- Satpy: https://github.com/pytroll/satpy — readers, composites, corrections
- SIFT (uwsift): https://github.com/ssec/sift — visualization techniques

Upstream contributions are a great way to grow as a developer, and they let the
whole community benefit.

---

## License

This project is **dual-licensed** under the Apache License, Version 2.0, and the GNU General Public License, Version 3.0.

You may use, modify, and distribute this software under the terms of either license, provided that:
- Any distributed modifications remain open-source
- You comply with both licenses where applicable
- You retain this copyright notice and license headers in all copies

See [LICENSE](LICENSE) for the full text of both licenses.

### Developer Notification Policy

**Important**: As a condition of this open-source license, any party intending to modify, redistribute, or create derivative works of this software **must formally notify the principal developers** prior to such modification or redistribution.

**Notification must be sent via:**
- Email to: **pwards.sci@gmail.com**
- Official GitHub issue/pull request at: https://github.com/PWARDS-weather/MonWatch-UI

**Failure to provide notification constitutes unauthorized modification and distribution, which violates the terms of this license.**

This policy applies to all users, contributors, and distributors of MonWatch-UI.

---

## About PWARDS

**PWARDS** (Pasacao Weather Atmospheric and Real-Time Data System) is an open-source meteorological initiative based in Pasacao, Camarines Sur, Philippines, established in 2025.

PWARDS develops accessible weather monitoring and satellite data processing tools for students, researchers, and hobbyists. The organization maintains several open-source projects including MonWatch-UI, a satellite imagery analysis workstation for cyclone monitoring.

**Contact**: pwards.sci@gmail.com  
**GitHub**: https://github.com/PWARDS-weather  
**Facebook**: https://www.facebook.com/share/14ShA5G2Wcv/

**Principal Developer**: Zero (Prince Al Zhanjie B. Dela Rosa)

### Project governance

- The **PWARDS-weather** GitHub account is the publishing organization; code
  commits are attributed to **individual developer accounts**, so contributions
  remain tied to the people who write them and tracks cleanly as the team grows.
- Maintainers push from their personal accounts; the organization account is
  reserved for releases and repository administration.

---

## Acknowledgments

- **NOAA** — National Oceanic and Atmospheric Administration for Himawari data via AWS
- **JMA** — Japan Meteorological Agency for tropical cyclone forecast data
- **NHC** — National Hurricane Center for Atlantic/EPAC storm data
- **JTWC** — Joint Typhoon Warning Center for Western Pacific cyclone data
- **PAGASA** — Philippine Atmospheric, Geophysical and Astronomical Services Administration
- **CWA** — Taiwan Central Weather Administration
- **EUMETSAT / OSI SAF** — ASCAT wind data

---

> ### Please note that the program is still under active development, and some features are still being improved. If you encounter any issues, reporting them would be greatly appreciated.

---

*MonWatch-UI Cyclone V3.0.5.1 — © 2025-2026 PWARDS-weather*
