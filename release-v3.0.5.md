<img width="1280" height="640" alt="MonWatch-poster" src="https://github.com/user-attachments/assets/2f3e4df5-3b2a-4762-90ab-b4ee17770bf0" />

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
 MonWatch-UI 3.0.5 – The "Packaging & Performance" Update
</h1>

**Release date:** June 14, 2026

---

## ✨ Features

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
- **App icon** – window title-bar and taskbar icon now set to `Monwatch-LOGO.png`.

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
- Linux build script (`build_linux.sh`) created (requires testing on a Linux host — untested in this release).

---

## 🐞 Known Bug (carried over from 3.0.3)

- **SATAID UI band names – only S1 through S9 and EIRc/EIRm are correctly labeled. All other band names (secondary names, other channels) are currently incorrect or mismatched.**
- **Overlays (PAR, AoRs, etc.) – only Grid and Coastline work reliably. Other overlays (PAGASA PAR, JMA, TCAD, Manila FIR, etc.) do NOT function on SATAID format and may cause the app to break.**
- **GOES band images do not resize properly in certain viewport configurations.**
  > **Recommended:** To resolve this, disable grid and coastline before loading the imagery.

> **Note:** This release is primarily a **packaging & performance test build** and a test build for the new display and overlay system. The next release will be a stable build with the above bugs resolved.

---

## 🔗 Links

- **GitHub Release:** https://github.com/PWARDS-weather/MonWatch-UI/releases/tag/v3.0.5-Cyclone

---

### 🌐 Check Out Our Website

**pwards.loophole.site**

---

*Happy forecasting – from both sides of the Pacific, and everywhere in between!*  
– The PWARDS Weather Team
