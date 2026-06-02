# MonWatch-UI

**A Windows tool for browsing and previewing NOAA Himawari satellite imagery hosted on AWS.**

MonWatch-UI lets you quickly explore raw Himawari geostationary satellite data — no web viewer required. Navigate by date, time, and spectral band, preview imagery at multiple quality levels, and view data with CRS georeferencing (lat/lon grid + coastlines) directly from AWS buckets.

> Developed by [PWARDS-weather](https://github.com/PWARDS-weather) — the Pasacao Weather Atmospheric and Real-Time Data System.

---

<img width="1666" height="1031" alt="image" src="https://github.com/user-attachments/assets/b4bc4cd7-ba65-43a3-ad16-537c745fe201" />

---

## Features

- **Drag-and-drop loading** — drop AWS Himawari files directly into the UI
- **Manual folder navigation** — browse by date → time → spectral band
- **CRS georeferencing** — lat/lon grid overlay with coastline rendering
- **Multi-quality preview** — view imagery at 0.25x, 0.5x, and 1x resolution
- **Automatic Python setup** — `run.bat` handles all dependencies on first launch
- **No configuration needed** — works out of the box on Windows 10/11

---

## Requirements

- Windows 10 or 11
- Internet connection (for AWS data access)
- No Python pre-installation needed — `run.bat` sets it up automatically

---

## Installation

1. Clone or download this repository:
   ```
   git clone https://github.com/PWARDS-weather/MonWatch-UI.git
   ```
2. Run `run.bat` (double-click or right-click → Run as administrator if needed)
3. The app launches automatically after setup

---

## Usage

**Drag and Drop**
Drag any AWS Himawari `.tif` or compatible file into the main panel to load and preview it.

**Manual Navigation**
Click **Open Folder**, then navigate into a date folder → time folder → spectral band to browse available imagery.

**Data Source**
All imagery is sourced from AWS-hosted NOAA Himawari buckets. An internet connection is required to stream data.

---

## Dependencies

| Package | Purpose |
|---|---|
| PySide6 | UI framework |
| Pillow | Image processing |
| tifffile | TIFF/GeoTIFF reading |
| numpy | Array operations |

All dependencies are installed automatically by `run.bat` via pip.

---

## Legend
- `[x]` — Fully added / completed in a recent release
- `[x+]` — Improving / in active development (partial or ongoing work)
- `[\~]` — Planned / in the works (explicitly mentioned for future)
- `[ ]` — Not yet started

### Animation & Visualization
- [x] Brand new Animation tab — play, pause, scrub, loop + smooth real-time zoom/pan with prefetching
- [x] Live mouse readout (Latitude/Longitude, B13 Brightness Temperature, AoR status instantly)
- [x] Proper handling for storms crossing the dateline (Pacific systems now display correctly)
- [x] Three simple UI modes: Professional, Casual, and Hobby (automatically adjust visible tabs/options)
- [x] Cleaner map overlays with gridlines + coastlines + better viewport centering
- [x] Better performance on lower-VRAM GPUs with smart cache management
- [x] Area of Responsibility (AoR) overlays added: PAGASA (PAR), JMA, TCAD, TCID, Manila FIR


### Data Processing & Download Pipeline
- [x] Complete range-download overhaul using non-blocking `RangeS3DownloadWorker` (full UI responsiveness)
- [x] Full support for Japan and Target rapid-scan sectors (correct HHMM filename prefixes, mixed rapid-scan folders)
- [x] Consistent flat local folder naming for both range and single-file downloads
- [x] Pre-download check that automatically skips already-downloaded `.bz2` / `.DAT` files
- [x] Automatically starts processing after download finishes
- [x] Improved product-type detection, wind data filtering, and sector handling
- [x] Smarter micro-group loader using band + area tokens (`Rxxx/JPxx`) for mixed rapid-scan folders
- [x] Incremental processing — only missing bands are handled
- [x] NDMW Level-2 wind data is now automatically merged into the `_AHI.nc` file
- [x] Per-band dimension naming to prevent shape conflicts
- [x] Improved ADS sidecar files with geotransform, area metadata, and full band inventory
- [x] New standalone `--merge-wind` command-line option
- [x] Large NetCDF files now load smoothly in the background without freezing the app
- [x] Priority B03 band caching + overall improved caching system

### Other Recent Additions
- [x] RGB composites support (JMA recipes; NOAA + other GEO satellites planned for future)
- [x] Multi-layer product improvements and general stability/usability enhancements


## 🔄 Currently In Progress / Planned
Explicitly listed in the **Cyclone 3.0.2 release notes** under "In the works".

- [x+] Support for additional geostationary satellites (GOES, Meteosat) 
  *(GOES integration planned for v3.0.4 or earlier)*
- [x+] API system for Point Forecast (Windy & MetraWeather)  
  *(Metra Weather compatibility exploration — actively in development)*
- [x+] Wind Overlay improvements + AMV (wind vectors) bug fixes and enhancements
- [\~] Tracks tab + more customizability options
- [\~] General GUI improvements (ongoing)


## Roadmap

Planned features for upcoming releases:

- [ ] Sea Surface Temperature (SST) layer overlay
- [x+] Support for additional geostationary satellites (GOES, Meteosat) 
  *(Improving — GOES targeted for v3.0.4 or earlier; see "In Progress" section above)*
- [ ] 3D Earth visualization for combined GeoSat imagery
- [ ] Export current view as PNG / GeoTIFF  
  *(Internal GeoTIFF + NetCDF generation via DAT>NC Processor is actively improved, but no dedicated user-facing "Export current view" UI feature yet)*
- [x+] Metra Weather compatibility exploration  
  *(Improving — API system for point forecasts actively in development; see "In Progress" section above)*

---

## Contributing

Contributions are welcome! To get started:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes and test them
4. Open a Pull Request with a clear description of what you changed

For bugs or feature requests, please [open an issue](https://github.com/PWARDS-weather/MonWatch-UI/issues).

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## About PWARDS

PWARDS (Pasacao Weather Atmospheric and Real-Time Data System) is a small open-source weather initiative based in Pasacao, Camarines Sur, Philippines, focused on building accessible meteorology tools for students and hobbyists.

[GitHub](https://github.com/PWARDS-weather) · [Facebook](https://www.facebook.com/share/14ShA5G2Wcv/)

> Himawari-8/9 data courtesy of the Japan Meteorological Agency (JMA), openly distributed via NOAA AWS Open Data Program.
