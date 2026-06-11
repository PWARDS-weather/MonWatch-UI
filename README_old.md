<img width="1280" height="640" alt="MonWatch-poster" src="https://github.com/user-attachments/assets/2f3e4df5-3b2a-4762-90ab-b4ee17770bf0" />

<img width="128" height="128" alt="splash" src="https://github.com/user-attachments/assets/a7821feb-77fc-4c9d-906a-3cb48aa0e555" />
<img width="128" height="128" alt="MONWATCH-UI" src="https://github.com/user-attachments/assets/76cd20da-59a5-4b8f-9390-1d7b6525b460" />



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
  MonWatch-UI v3.0.3
</h1>

**A Windows tool for browsing, previewing, and analyzing satellite imagery — now with native SATAID support.**

MonWatch-UI lets you explore geostationary satellite data from multiple sources: Himawari AWS, local SATAID (`.Z`) archives, and more. Navigate by date, time, and spectral band, preview at multiple quality levels, view with georeferenced overlays, and switch between four interface modes tailored for professionals, enthusiasts, and users familiar with the classic SATAID workflow.

> Developed by [PWARDS-weather](https://github.com/PWARDS-weather) — the Pasacao Weather Atmospheric and Real-Time Data System.

---

https://github.com/user-attachments/assets/5cd2af74-750f-49cf-b33c-0e32ddc1f231

---

## Features

- **Full SATAID Support** – Native handling of `.Z` files, dedicated SATAID mode & tab, classic interface replication
- **Four UI Modes** – Casual (simplified), Professional (advanced), Hobby (weather enthusiasts), SATAID (classic interface)
- **Drag-and-drop loading** – Drop Himawari `.tif` (depreciated), NetCDF, or SATAID `.Z` files directly into the UI
- **Floating panels** – Detach Center Viewport & Right Panel for multi‑monitor setups
- **Robust crash logging** – Global exception hook + threading support (no more silent crashes)
- **Live brightness/contrast controls** and SATAID Cache Manager
- **Dynamic Date & Time selector** – Shows only dates with actual data available
- **Non‑blocking caching bands dialogue** – Prevents interface freezes during band caching
- **CRS georeferencing** – Lat/lon grid + coastline overlays (works on standard formats)
- **Multi-quality preview** – View imagery at 0.25x, 0.5x, and 1x resolution **(removed)**
- **Automatic setup** – `run.exe` handles all dependencies on first launch (no admin required)
- **RGB composites** – Support for JMA recipes, with more planned
- **Area of Responsibility (AoR) overlays** – PAGASA PAR, JMA, TCAD, TCID, Manila FIR

---

## Requirements

- Windows 10 or 11
- **RAM:** 2 GB minimum, 4 GB required for smooth performance
- Internet connection (for AWS data access; offline SATAID processing works without internet)
- No Python pre-installation needed – `run.exe` sets up a portable Python environment automatically

---

## Installation

1. Clone or download this repository:
 `git clone https://github.com/PWARDS-weather/MonWatch-UI.git`
2. Run `run.exe` (double‑click)
3. The app launches automatically after initial dependency installation

---

## Usage

**Drag and Drop**  
Drop any supported file (`.tif` (depreciated), `.nc`, SATAID `.Z*`) into the main panel to load and preview it.

**Manual Navigation**  
Click **Open Folder** to browse local directories. For Himawari data, navigate a date → time → spectral band structure. For SATAID files, select the folder containing `.Z*` archives.

**Interface Modes**  
Switch between Casual, Professional, Hobby, and SATAID modes from the menu. Each mode tailors the visible panels and options to your workflow.

**Data Sources**  
- NOAA Himawari AWS buckets (requires internet)  
- Local SATAID archives (`.Z` format)  
- NetCDF/TIFF files processed by the included tools

---

## Dependencies

All dependencies are installed automatically by `run.exe` via pip. The core packages include:

| Package | Purpose |
|---|---|
| PySide6 | UI framework |
| Pillow | Image processing |
| tifffile | TIFF/GeoTIFF reading |
| numpy | Array operations |
| xarray, netCDF4 | SATAID & NetCDF data handling |
| boto3, botocore | AWS S3 access for Himawari data |
| satpy | Satellite product generation & compositing |
| rasterio | Geospatial raster I/O |
| pyproj | Coordinate transformations |
| pyshp | Shapefile support for overlays |
| cartopy | Map projections & georeferencing |
| requests | HTTP downloads |
| cupy-cuda12x | GPU-accelerated array operations (optional) |
| numba | JIT-compiled numerical routines (optional) |

*(Full list with exact versions is in `requirements.txt`.)*

---

## Legend
- `[x]` — Fully added / completed in a recent release
- `[x+]` — Improving / in active development (partial or ongoing work)
- `[~]` — Planned / in the works (explicitly mentioned for future)
- `[ ]` — Not yet started

### Animation & Visualization
- [x] Brand new Animation tab — play, pause, scrub, loop + smooth real‑time zoom/pan with prefetching
- [x] Live mouse readout (Latitude/Longitude, B13 Brightness Temperature, AoR status instantly)
- [x] Proper handling for storms crossing the dateline (Pacific systems now display correctly)
- [x] Three UI modes: Professional, Casual, and Hobby (now four with SATAID mode)
- [x] Cleaner map overlays with gridlines + coastlines + better viewport centering
- [x] Better performance on lower‑VRAM GPUs with smart cache management
- [x] Area of Responsibility (AoR) overlays: PAGASA (PAR), JMA, TCAD, TCID, Manila FIR
- [x] Floating panels — detachable Center Viewport & Right Panel
- [x] SATAID‑style Options menu restored
- [x] Dynamic Date & Time selector (shows only available data)
- [x] Non‑blocking caching bands dialogue
- [x] Live brightness/contrast controls for SATAID imagery

### Data Processing & Download Pipeline
- [x] Complete range‑download overhaul using non‑blocking `RangeS3DownloadWorker` (full UI responsiveness)
- [x] Full support for Japan and Target rapid‑scan sectors (correct HHMM filename prefixes, mixed rapid‑scan folders)
- [x] Consistent flat local folder naming for both range and single‑file downloads
- [x] Pre‑download check that automatically skips already‑downloaded `.bz2` / `.DAT` files
- [x] Automatically starts processing after download finishes
- [x] Improved product‑type detection, wind data filtering, and sector handling
- [x] Smarter micro‑group loader using band + area tokens (`Rxxx/JPxx`) for mixed rapid‑scan folders
- [x] Incremental processing — only missing bands are handled
- [x] NDMW Level‑2 wind data is now automatically merged into the `_AHI.nc` file
- [x] Per‑band dimension naming to prevent shape conflicts
- [x] Improved ADS sidecar files with geotransform, area metadata, and full band inventory
- [x] New standalone `--merge-wind` command‑line option
- [x] Large NetCDF files now load smoothly in the background without freezing the app
- [x] Priority B03 band caching + overall improved caching system
- [x] **bg_to_nc.py v3.3.2** – fixed `_sanitize_attrs` to preserve list/tuple attributes, improved numpy handling

### Other Recent Additions
- [x] RGB composites support (JMA recipes; NOAA + other GEO satellites planned for future)
- [x] Multi‑layer product improvements and general stability/usability enhancements
- [x] SATAID Cache Manager
- [x] Enhanced Professional Devkit – improved Channel Editor with RGB/Single/Dual/FG+BG modes and live preview
- [x] Complete modular code refactor (split into `products.py`, `Engine.py`, `Workers.py`, etc.) for maintainability
- [x] Windy Point forecast integration (live point forecasts inside Cyclone)

---

## 🔄 Currently In Progress / Planned

- [x+] SATAID UI additional functions and UI fixes
- [~] MetraWeather Lightning & Point Forecast integration
- [~] Support for other geostationary satellites: GOES, Meteosat, FY‑4, etc. *(GOES targeted for v3.0.4)*
- [~] NWP & forecasting models – full GRIB2 integration for model data overlays
- [~] Multi‑viewport & stacking viewport – view multiple bands and forecast models simultaneously, with synchronized animation
- [~] Sea Surface Temperature (SST) – real‑time SST layers and analysis
- [~] 3D Globe – multi‑satellite imagery on a rotatable 3D view
- [~] Bird’s‑eye view – perspective viewing angle
- [~] Diagnostic tools – sounding profiles, cross‑section analysis, storm relative motion
- [~] Analysis tools – contouring, isotherm/isobar drawing, distance/area measurement
- [x+] Wind Overlay improvements + AMV (wind vectors) bug fixes and enhancements
- [~] Tracks tab + more customizability options
- [~] General GUI improvements (ongoing)
- [~] Export current view as PNG / GeoTIFF

---

## Roadmap

The team is dedicated to making MonWatch-UI the most powerful, free satellite & weather analysis platform. Upcoming features include:

- [ ] SST overlay
- [~] Additional geostationary satellites (GOES, Meteosat, FY‑4)
- [~] 3D Earth visualization
- [~] Multi‑viewport / stacking viewport
- [~] Full GRIB2 model integration
- [~] Diagnostic and analysis tools
- [~] Export current view as PNG / GeoTIFF

---

## Contributing

Contributions are welcome! To get started:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes and test them
4. Open a Pull Request with a clear description

For bugs or feature requests, please [open an issue](https://github.com/PWARDS-weather/MonWatch-UI/issues).

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## About PWARDS

PWARDS (Pasacao Weather Atmospheric and Real-Time Data System) is a small open‑source weather initiative based in Pasacao, Camarines Sur, Philippines, focused on building accessible meteorology tools for students and hobbyists.

[GitHub](https://github.com/PWARDS-weather) · [Facebook](https://www.facebook.com/share/14ShA5G2Wcv/)

> Himawari‑8/9 data courtesy of the Japan Meteorological Agency (JMA), openly distributed via NOAA AWS Open Data Program.

---

## Screenshots (v3.0.3)

---

<img width="459" height="345" alt="SATAID mode" src="https://github.com/user-attachments/assets/1fb71f0c-9dce-4827-af92-7833a4529d8f" />

---

<img width="1919" height="1032" alt="Professional mode with floating panels" src="https://github.com/user-attachments/assets/f7ffd837-f864-42a0-b90b-1984c9055172" />

---

<img width="455" height="346" alt="Hobby mode scene tab" src="https://github.com/user-attachments/assets/409e1fec-17fe-4517-8d24-733d695ab43d" />

---

<img width="1919" height="1031" alt="Animation tab and overlays" src="https://github.com/user-attachments/assets/a7b8c5cb-fbda-428b-8281-0f61dfe072d1" />

---

<img width="1919" height="1034" alt="SATAID classic interface" src="https://github.com/user-attachments/assets/8c3af3f3-ec25-4e05-9ffc-8afe718d41f9" />

---

🌐 **Website:** [pwards.loophole.site](https://pwards.loophole.site)  
*Happy forecasting!*  
– The PWARDS Weather Team
