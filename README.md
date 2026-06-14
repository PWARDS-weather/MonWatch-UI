<p align="center">
  <img width="1280" height="640" alt="MonWatch-poster" src="https://github.com/user-attachments/assets/2f3e4df5-3b2a-4762-90ab-b4ee17770bf0" />
</p>

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
 MonWatch-UI v3.0.5
</h1>

**Release date:** June 14, 2026

**A Windows tool for browsing, previewing, and analyzing satellite imagery — now with full GOES-16/17/18/19 support, NHC hurricane tracks, multi‑viewport previews, and powerful performance optimizations.**

MonWatch-UI lets you explore geostationary satellite data from multiple sources: Himawari AWS, GOES ABI (NetCDF), local SATAID (`.Z`) archives, and more. Navigate by date, time, and spectral band, preview at high quality, view with georeferenced overlays, and switch between four interface modes tailored for professionals, enthusiasts, and users familiar with the classic SATAID workflow.

> Developed by [PWARDS-weather](https://github.com/PWARDS-weather) — the Pasacao Weather Atmospheric and Real-Time Data System.

---

<p align="center">
  <a href="https://github.com/user-attachments/assets/5cd2af74-750f-49cf-b33c-0e32ddc1f231">
    <img src="https://github.com/user-attachments/assets/5cd2af74-750f-49cf-b33c-0e32ddc1f231" alt="MonWatch Demo" width="800"/>
  </a>
</p>

---

## ✨ Latest Features

### 1. Tracks Tab & NHC Integration
- Full track management: create, edit, delete meteorological tracks (Typhoon, Hurricane, Tropical Storm, etc.) and custom AoR tracks.
- **NHC Downloader** – fetches the latest Atlantic / East Pacific tropical cyclone advisories from nhc.noaa.gov in a background thread.
- Overlay NHC data on the viewport: cone of uncertainty, forecast track line, points & labels, wind radii, and best track — each with independent visibility toggles.
- Storm selector combo to focus on a specific downloaded storm.
- Data is cached to disk (JSON metadata, shapefiles, KMZ) and auto-archives stale storms on startup.
- Floating info box on the viewport (configurable position: top/bottom-left/right).

### 2. Multi‑Viewport (In Progress)
- Up to 5 independent windows, each with a mode selector:
  - **Viewport** – mirrors the main graphics view (non-interactive, synced every 33ms).
  - **Bands** – displays a single band with zoom/pan.
  - **Animation** – plays animation sequences.
  - **Forecast** – renders NHC forecast tracks via Cartopy + matplotlib.
  - **3D Globe** – embeds a rotatable globe with equirectangular-reprojected satellite imagery.
- The **Multi‑Viewport tab** in the main window is still a placeholder while we refine the UX.

### 3. Contouring (Experimental)
- Drag a rectangle on the viewport while contour mode is active to select a region.
- Multiple algorithm options: MonWatch-UI Default, SATAID-style, PWARDS (currently disabled).
- Configurable contour levels, Gaussian blur smoothing, Kelvin‑to‑Celsius conversion for IR/WV bands.
- Grid and coastline overlay on the contour plot.
- Works with both GOES/ Himawari NC bands and SATAID data.

### 4. Animation Object Tracking, Generation & Image Generation
- In **Target Area** mode, a **Track** checkbox centers the viewport on each frame as the animation advances.
- Export formats: PNG, JPEG, BMP, MP4, GIF, AVI with configurable footer bar (satellite, datetime, band, lat/lon).
- *(Full Disk tracking is not yet supported – IBTrACS integration is still in development.)*

### 5. Cartopy Export & Forecast Generation
- Optional Cartopy grid overlay on exported images (settings toggle).
- Cartopy-based NHC forecast track image generation for the multi‑viewport Forecast mode – includes land/ocean background, cone polygons, colored intensity points, and labeled annotations.

### 6. Tear-off / Floating Panels
- The **Center Viewport** and **Right Panel** can be detached into floating windows by opening the window menu and clicking "Float Right Panel", "Float CenterView" or Opening the Left Panel Menu and Clicking "Float Left Panel".
- Drag any tab from the **right panel** (Tracks, Animation, Tools, etc.) out of the window to detach it into its own floating window.
- Re-dock by closing the floating window.

---

## ⚡ Performance

### 1. Overlay Rendering Rewritten (cached layers)
- The old combined `QImage` overlay (grid + coast + NHC on one bitmap) is now **three separate cached layers**:
  - **Grid** – cached `QGraphicsPixmapItem`, re-rendered only when CRS/geotransform or grid settings change.
  - **Coastlines** – same pattern, independent cache.
  - **NHC overlays** – separate `QGraphicsPathItem`s, redrawn only when NHC toggles or data updates.
- **Result:** toggling one overlay no longer forces a full redraw of all others.

### 2. Fixed `update_overlays()` Early-Return (was always bypassed)
- The cache-key comparison at the top of `update_overlays()` depended on `_overlay_item`, which was set to `None` by `_remove_all_overlay_items()` on every call — so **every overlay update re-rendered everything**.
- Fixed: the key is now computed directly from the scene pixmap item, and `_last_overlay_key` is actually saved at the end of the function.

### 3. Image Resize Cached Per Band + Pre-Resized in Background Worker
- Switching bands triggered `skimage.transform.resize` on a ~3000×2400 RGBA array **every time** (~2 seconds of UI freeze).
- **Fix:** results are cached by `(band, original_w, original_h, ref_w, ref_h)`. Repeated switches to the same band are instant.
- Additionally, the `RawBandCacheWorker` now pre-resizes display images in its **thread pool** during the initial caching phase — so even the *first* display of each band arrives at the correct size.
- > **Note:** The first time you load a particular date/scene, it will be slower while bands are fetched, calibrated, and cached. After that, any future loads of the same data will be significantly faster.

### 4. `ref_grid_size` Extracted Earlier (during metadata load)
- Previously the reference grid dimensions were only read from the ADS sidecar during `extract_crs_from_ads()`, which ran during the *first display* — too late for the caching workers.
- **Fix:** both the GOES multi-file path and the generic NC path now extract `ref_grid_w`/`ref_grid_h` from the sidecar while loading metadata, before any caching begins.

### General
- **App icon** – window title bar and taskbar icon now set to `Monwatch-LOGO.png`.

---

## 📦 How to Run

1. Extract the `.rar` archive – you'll get a `MonWatch` folder.
2. Open `MonWatch\Windows\` and double‑click `MonWatch.exe`.
3. *(Optional – Developer)* To compile your own `.exe` from source: `pip install -r requirements.txt && pyinstaller MonWatch.spec`.

---

## ⚠️ Security & Packaging – Resolved

| Issue | Status |
|-------|--------|
| `run.exe` flagged as Trojan (Phonzy, Malware.AI) | **FIXED** – now using proper PyInstaller build (`MonWatch.exe`) instead of BAT‑to‑EXE converter |
| Third‑party BAT‑to‑EXE converter stub | **REMOVED** |
| False positives from heuristic engines | **BYPASSED** – legitimate PyInstaller binary, no suspicious stubs |

**Current distribution:** `.rar` archive → `MonWatch\Windows\` contains the portable build including `MonWatch.exe`.

---

## 🔧 Linux Compatibility
- Linux build will be in v3.0.6

---

## All Features (Cumulative)

- **Full GOES-16/17/18/19 Support** – Native ABI NetCDF reading (C01–C16), all sectors (Full Disk, CONUS, Mesoscale)
- **Full SATAID Support** – Native handling of `.Z` files, dedicated SATAID mode & tab, classic interface replication
- **Four UI Modes** – Casual, Professional, Hobby, SATAID
- **NHC Hurricane Tracks** – download and overlay real‑time cyclone data from nhc.noaa.gov (new in v3.0.5)
- **Multi‑Viewport** – up to 5 independent satellite, forecast, and globe views (in progress, v3.0.5)
- **Contouring** – experimental region‑based temperature contouring (v3.0.5)
- **Animation export** – MP4, GIF, AVI, etc., with tracking and customizable footer (v3.0.5)
- **Cartopy‑based forecast maps** – NHC forecast track generation (v3.0.5)
- **Floating panels** – detachable viewport, right panel, and individual tabs
- **Drag‑and‑drop loading** – Drop Himawari `.tif` (deprecated), NetCDF, GOES `.nc`, or SATAID `.Z` files directly
- **Robust crash logging** – Global exception hook + threading support
- **Live brightness/contrast controls** and SATAID Cache Manager
- **Dynamic Date & Time selector** – shows only dates with actual data
- **Non‑blocking caching bands dialogue** – prevents interface freezes
- **CRS georeferencing** – Lat/lon grid + coastline overlays (standard formats)
- **Automatic setup** – first‑launch dependency handling (no admin required)
- **RGB composites** – JMA recipes support (more planned)
- **Area of Responsibility (AoR) overlays** – PAGASA PAR, JMA, TCAD, TCID, Manila FIR
- **Wind data** – NDMW Level‑2 wind data merged into AHI NetCDF, wind overlays (improving)
- **Performance optimizations** – cached overlay layers, pre‑resized imagery, smart cache management

---

## Requirements

- Windows 10 or 11 (Linux soon)
- **RAM:** 2 GB minimum (4 GB recommended for smooth multi‑viewport and animation)
- Internet connection (for AWS data access; offline SATAID/GOES processing works without internet)
- No Python pre‑installation needed – the portable build includes everything

---

## Installation

### Portable Build (Recommended)
1. Download the latest `.rar` archive from [Releases](https://github.com/PWARDS-weather/MonWatch-UI/releases).
2. Extract the archive – you’ll get a `MonWatch` folder.
3. Navigate to `MonWatch\Windows\` and run **`MonWatch.exe`**.

### From Source
1. Clone the repository:  
   `git clone https://github.com/PWARDS-weather/MonWatch-UI.git`
2. Install dependencies:  
   `pip install -r requirements.txt`
3. Run the application:  
   `python main.py`

---

## Usage

**Drag and Drop**  
Drop any supported file (`.nc`, GOES `.nc`, SATAID `.Z*`) into the main panel to load and preview it.

**Manual Navigation**  
Click the **Scene** tab to browse a dynamically dated dropdown box.

- **For Himawari/GOES data:** set the Satellite to Himawari/GOES then select the dynamically changing date dropdown box → time dropdown box → then click **Load Bands** to load all data into cache.
- **For SATAID files:** set the Satellite to **Himawari**, then select the type (or scroll down the type list until it says "SATAID"). The dates will then automatically update for the SATAID archives.
**Manual Navigation**

**For Visualizing**
Click the **Scene** tab to browse a dynamically dated dropdown box.

- **For Himawari/GOES data:** select the dynamically changing date dropdown box → time dropdown box → then click **Load Bands** to load all data into cache.
- **For SATAID files:** set the Satellite to **Himawari**, then select the type (or scroll down the type list until it says "SATAID"). The dates will then automatically update for the SATAID archives.

## For Downloading Satellite Data

1. In the **Left Panel**, find and click the button labeled **D** or **Download**.  
   A new window will open with the following options:

   - **Satellite Type**
   - **Band Selection** – choose which bands to download
   - **Download Settings**  
     - **Path**  
     - **Processing**  
       - **Mode**: Extract Only, Process Only (NetCDF), or Extract & NetCDF (Default)
   - **Region** – only available for date range selection
   - **Download L2 Winds** – only available when Full Disk is selected
   - **Date Range From** / **Date Range To**
   - **Download Button** – single download only
   - **Download Button Sequence**

Full documentation coming soon.
---

**Interface Modes**  
Switch between Casual, Professional, Hobby, and SATAID modes from the menu.

**Data Sources**  
- NOAA Himawari AWS buckets (requires internet)
- GOES ABI NetCDF files (local or remote)
- Local SATAID archives (`.Z` format)
- NetCDF/TIFF files processed by the included tools

---

## Dependencies

All dependencies are installed automatically by the portable build or via `pip`. Core packages:

| Package | Purpose |
|---|---|
| PySide6 | UI framework |
| Pillow | Image processing |
| tifffile | TIFF/GeoTIFF reading |
| numpy | Array operations |
| xarray, netCDF4 | SATAID, GOES & NetCDF data handling |
| boto3, botocore | AWS S3 access for Himawari data |
| satpy | Satellite product generation & compositing |
| rasterio | Geospatial raster I/O |
| pyproj | Coordinate transformations |
| pyshp | Shapefile support for overlays |
| cartopy | Map projections & georeferencing |
| requests | HTTP downloads |
| cupy-cuda12x | GPU-accelerated array operations (optional) |
| numba | JIT-compiled numerical routines (optional) |

---

## Legend

- `[x]` — Fully added / completed in a recent release
- `[x+]` — Improving / in active development
- `[~]` — Planned / in the works
- `[ ]` — Not yet started

### What’s already here

- [x] Full GOES-16/17/18/19 support (C01–C16, all ABI sectors)
- [x] Four UI modes: Casual, Professional, Hobby, SATAID
- [x] NHC integration – download and overlay real‑time cyclone advisories
- [x] Multi‑viewport (up to 5 windows, in progress)
- [x] Experimental contouring
- [x] Animation tracking & export (MP4, GIF, AVI, etc.)
- [x] Cartopy‑based forecast maps
- [x] Tear‑off floating panels for viewport, right panel, and tabs
- [x] Performance overhaul – cached overlay layers, pre‑resized images
- [x] Robust crash logging and threading
- [x] SATAID Cache Manager
- [x] RGB composites (JMA recipes)
- [x] Range‑download overhaul with non‑blocking workers
- [x] Priority B03 band caching
- [x] Dynamic Date & Time selector

### 🔄 Currently In Progress / Planned

- [x+] SATAID UI additional functions and UI fixes
- [x+] Wind Overlay improvements + AMV (wind vectors) bug fixes
- [~] MetraWeather Lightning & Point Forecast integration
- [~] Other geostationary satellites: Meteosat, FY‑4
- [~] NWP & forecasting models – full GRIB2 integration
- [~] Multi‑viewport & stacking viewport – view multiple bands/models simultaneously
- [~] Sea Surface Temperature (SST) – real‑time layers and analysis
- [~] 3D Globe – multi‑satellite rotatable view
- [~] Bird’s‑eye view – perspective viewing angle
- [~] Diagnostic tools – sounding profiles, cross‑section analysis
- [~] Analysis tools – contouring, isotherm/isobar drawing, distance/area measurement
- [~] Tracks tab + more customizability options
- [~] General GUI improvements (ongoing)
- [~] Export current view as PNG / GeoTIFF

---

## Roadmap

The team is dedicated to making MonWatch-UI the most powerful, free satellite & weather analysis platform. Upcoming releases:

- **v3.0.6** – SATAID UI enhancements, MetraWeather Lightning, GRIB2 NWP, Multi‑viewport, 3D Globe, Diagnostic & Analysis tools
- **v4 "Derecho"** – Plugin system, Animation overhaul, MonWatch-UI Bundle System
- **v5 "ENSO"** – Polar satellite imagery, Advanced 3D features, Automated testing suite
- **v6** – Space Weather UI, Seismology UI

---

## Known Issues (v3.0.5)

- **SATAID UI band names** – only S1 through S9 and EIRc/EIRm are correctly labeled; all other band names are currently incorrect or mismatched.
- **Overlays (PAR, AoRs, etc.) on SATAID format** – only Grid and Coastline work reliably. Other overlays may cause the app to break. Disable them when working with SATAID files.
- **GOES band images** – do not resize properly in certain viewport configurations. Workaround: disable grid and coastline before loading.

> **Note:** This release is primarily a packaging & performance test build. The next release (v3.0.6) will be a stable build with the above bugs resolved.

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

> Himawari‑8/9 data courtesy of the Japan Meteorological Agency (JMA), openly distributed via NOAA AWS Open Data Program. GOES data courtesy of NOAA/NESDIS.

---

## 🌧️ MonWatch-UI (Names and Meanings) 

**International name (acronym):**  
`MonWatch-UI` = **Mon**soon + **Watch**ing + **UI**

**Local name (Filipino acronym):**  
`TINGIN` = **T**ropical **I**ntegrated **N**ear‑real‑time **G**uidance **I**nterface **N**etwork
*(or)* `SILIP` = **S**atellite **I**nteractive **L**ow‑level **I**maging **P**rocessor

---

## 🗒️ JMA Official Response (June 2026)

In June 2026, the Japan Meteorological Agency officially confirmed that MonWatch-UI’s use of Himawari data via NOAA AWS is fully compliant with the WMO Unified Data Policy. No formal agreement or authentication mechanism is required from JMA. This clearance reinforces our commitment to providing free and lawful access to geostationary satellite data.

> *“Himawari data distributed via NOAA's AWS servers are the data provided to NOAA by JMA on a free and unrestricted basis based on the WMO Unified Data Policy. Therefore, JMA has no issue with your use of the data in accordance with the condition of use of NOAA's AWS servers.”* – Satellite Program Division, JMA

### 📬 NOAA Open Data Dissemination (NODD) Inquiry – June 2026

To further ensure compliance with NOAA’s AWS Open Data terms, we sent an inquiry to the NOAA NODD Team on 9 June 2026. In that email, we confirmed:

- Anonymous, read‑only GET requests to the `noaa-himawari9` bucket
- On‑the‑fly generation of RGB composites and overlays (local only, no redistribution)
- Full quotation of JMA’s favourable response

**As of this release, we have not received a reply from NOAA.** The project continues to operate strictly within publicly documented AWS Open Data terms and JMA’s confirmed permissions. If we receive a response, this section will be updated accordingly.

---

## Screenshots (v3.0.5)

---

https://github.com/user-attachments/assets/7d2897b5-1fb4-4cbf-9703-4c553e9645f5

---

https://github.com/user-attachments/assets/ef438df7-2793-4975-8a54-c28e8f12a845

---

<img width="1880" height="1037" alt="monwatch_20260613_150319" src="https://github.com/user-attachments/assets/4982b317-a75f-406b-95b9-c396761a41ec" />

---

<img width="1880" height="1037" alt="monwatch_20260613_145726" src="https://github.com/user-attachments/assets/a86b6a45-6ea1-48be-9934-891eda0e3d58" />

---

<img width="1880" height="1037" alt="monwatch_20260613_143646" src="https://github.com/user-attachments/assets/cadde61c-3038-43f6-bc15-99275dc6a2ee" />

---

<img width="1880" height="1037" alt="monwatch_20260613_142631" src="https://github.com/user-attachments/assets/d92384ef-9116-47c9-a15a-2ad1e6f654c5" />

---

<img width="1882" height="1037" alt="monwatch_20260612_171816" src="https://github.com/user-attachments/assets/41edb047-92a2-4e2a-a11e-04c31479ebc2" />

---

<img width="1340" height="1348" alt="forecast_Cristina_ep032026_20260611_052108" src="https://github.com/user-attachments/assets/06dd66b2-539f-44f8-8243-f3eba388ab15" />

---

### 🌐 Check Out Our Website

**pwards.loophole.site**

---

*Happy forecasting – from both sides of the Pacific, and everywhere in between!*  
– The PWARDS Weather Team
