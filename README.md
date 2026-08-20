# MonWatch-UI Cyclone V3

**Satellite Imagery Analysis Workstation for Meteorological Data Processing**

MonWatch-UI lets you quickly explore raw Himawari geostationary satellite data — no web viewer required. Navigate by date, time, and spectral band, preview imagery at multiple quality levels, and view data with CRS georeferencing (lat/lon grid + coastlines) directly from AWS buckets.

> **Developed by [PWARDS-weather](https://github.com/PWARDS-weather)** — the Pasacao Weather Atmospheric and Real-Time Data System.  
> **Established**: 2025  
> **Status**: Operational and field-tested since July 3, 2026  
> **Compliance**: WMO (World Meteorological Organization) standards compatible

---

<img width="1666" height="1031" alt="image" src="https://github.com/user-attachments/assets/b4bc4cd7-ba65-43a3-ad16-537c745fe201" />

---

## Features

- **Drag-and-drop loading** — drop AWS Himawari files directly into the UI
- **Manual folder navigation** — browse by date → time → spectral band
- **CRS georeferencing** — lat/lon grid overlay with a vector coastline drawn from the Natural Earth 1:50m admin-0 country polygons (a SIFT-style "borders" layer, not a baked image)
- **Multi-quality preview** — view imagery at 0.25x, 0.5x, and 1x resolution
- **Automatic Python setup** — `run.bat` handles all dependencies on first launch
- **No configuration needed** — works out of the box on Windows 10/11
- **Multi-agency data support** — NHC, JMA, JTWC, PAGASA tropical cyclone forecasts
- **Real-time alerts** — Emergency alert notifications for severe weather
- **Animation playback** — Multi-frame satellite imagery sequences
- **Export capabilities** — PNG/JPEG/BMP stills and animated MP4/GIF/AVI export with geospatial footers

---

## Screenshots

| Quality 0.25x | Quality 0.5x | Quality 1x |
|---|---|---|
| <img width="418" height="300" alt="image" src="https://github.com/user-attachments/assets/babf6eea-12f8-47eb-8bed-d74dfcb275f1" /> | <img width="268" height="194" alt="image" src="https://github.com/user-attachments/assets/39175537-1564-4961-be0c-32c85ec5c645" /> | <img width="339" height="252" alt="image" src="https://github.com/user-attachments/assets/a2c865e9-2af3-4a95-8c2e-b167bc528176" /> |

## Requirements

- **Windows** 10 or 11 (*Windows-first build — see [Why Windows-first?](#why-windows-first)*)
- **Linux / macOS** — supported via `run.sh` or `python -m src`
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

MonWatch-UI ships as an installable Python package (`pyproject.toml`). To install
it directly:

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
Drag any AWS Himawari `.tif` or compatible file into the main panel to load and preview it.

### Manual Navigation
Click **Open Folder**, then navigate into a date folder → time folder → spectral band to browse available imagery.

### Data Source
All imagery is sourced from AWS-hosted NOAA Himawari buckets. An internet connection is required to stream data.

---

## Dependencies

| Package | Purpose |
|---|---|
| PySide6 | UI framework |
| Pillow | Image processing |
| tifffile | TIFF/GeoTIFF reading |
| numpy | Array operations |
| xarray | NetCDF data handling |
| rasterio | Geospatial raster operations |
| pyproj | Coordinate reference system transformations |
| cartopy | Map projections and cartography |

All dependencies are declared in `pyproject.toml` (installed automatically by
`pip install -e .`). The `requirements.txt` file is retained for the Windows
`run.bat` launcher. GPU-only and niche packages (`cupy-cuda12x`, `numba`,
`sataid`, `sounderpy`) are optional extras, not base requirements.

---

## Why Windows-first?

MonWatch-UI has been developed and field-tested primarily on Windows 10/11 to
serve its target users (meteorology students, researchers, and hobbyists in the
Philippines), where Windows is the dominant desktop OS and the environment
around JMA/PAGASA satellite tooling is commonly Windows-based. The application
is not Windows-only in design:

- Linux build scripts (`build_linux.sh`, `build_nuitka_linux.sh`) are included.
- `python -m monwatch`, the `monwatch` console script, and `run.sh` launch the app on
  any OS with a matching Python environment.
- Windows-only system APIs (window-show detection, `pythonw`, `CREATE_NO_WINDOW`,
  PowerShell sound/TTS) are guarded so the core app runs elsewhere.

## Architecture

MonWatch-UI follows a modular architecture with domain-specific controllers:

- **UI Layer** (`src/UI.py`) — Main application window with controller delegation
- **Controllers** (`src/controllers/`) — Business logic for alerts, animation, climate, export, forecast, overlays, and satellite data
- **Services** (`src/services/`) — Shared domain services for contouring and map projection
- **Data Clients** (`src/clients/`) — Agency API integrations (NHC, JMA, JTWC, PAGASA)
- **Workers** (`src/workers/`) — Background threading for cache operations and exports
- **Core Engine** (`src/core/`) — Computation backend with NumPy/CuPy support

---

## Roadmap

Planned features for upcoming releases:

- [ ] Sea Surface Temperature (SST) layer overlay
- [ ] Support for additional geostationary satellites (GOES, Meteosat)
- [ ] 3D Earth visualization for combined GeoSat imagery
- [ ] Export current view as PNG / GeoTIFF
- [ ] Atmospheric Motion Vectors (AMV) visualization
- [ ] Enhanced multi-viewport support for comparative analysis

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
  (MonWatch-UI's borders-overlay style is modeled on SIFT's approach)

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

---

*MonWatch-UI Cyclone V3.0.5.1 — © 2025-2026 PWARDS-weather*