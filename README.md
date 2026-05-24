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

## Screenshots

| Quality 0.25x | Quality 0.5x | Quality 1x |
|---|---|---|
| *(screenshot)* | *(screenshot)* | *(screenshot)* |

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

## Roadmap

Planned features for upcoming releases:

- [ ] Sea Surface Temperature (SST) layer overlay
- [ ] Support for additional geostationary satellites (GOES, Meteosat)
- [ ] 3D Earth visualization for combined GeoSat imagery
- [ ] Export current view as PNG / GeoTIFF
- [ ] Metra Weather compatibility exploration

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
