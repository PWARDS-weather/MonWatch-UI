# MonWatch-UI Roadmap

MonWatch-UI versions are named after weather phenomena, A to Z.

---

## ✅ v1.x — Aurora
- Initial prototype
- Basic drag-and-drop file loading
- Manual date/time/band folder navigation
- AWS Himawari data support

---

## ✅ v2.x — Blizzard
- CRS georeferencing with lat/lon grid and coastlines
- Multi-quality preview (0.25x, 0.5x, 1x)
- Automatic Python setup via `run.bat`
- Improved UI stability

---

## 🔄 v3.x — Cyclone *(current — V3.0.5 "Packaging & Performance", released 2026-06-14)*
- [x] Professional Tab (devkit-style channel grid, presets, FG+BG, stats)
- [x] Info Box (PAGASA/JMA overlay), View Temps cursor markers, ViewportFrame
- [x] Dedicated Animation tab + prefetch worker + loop playback
- [x] Winds support (AMV) + wind overlay
- [x] Tracks tab + NHC integration (cone, forecast track, points & labels, wind radii, best track)
- [x] Multi-viewport mode (viewport mirror, bands, animation, forecast, 3D globe)
- [x] Contouring (experimental) with multiple algorithms and region selection
- [x] Cartopy-based NHC forecast image generation + optional cartopy grid on exports
- [x] Tear-off / floating panels (center viewport, right panel, tabs)
- [~] Partial GOES (sat dropdown, S3 discovery; incomplete RGB/processing — resize issues remain)
- [ ] PAR/JAR/AoR overlay features — not yet functional on SATAID format
- [ ] Sea Surface Temperature (SST) layer overlay
- [x] Export current view as PNG / JPEG / BMP + MP4 / GIF / AVI animation
- [ ] GeoTIFF export
- [ ] Improved error handling and crash recovery (many bare excepts remain)
- [ ] Metra Weather compatibility exploration

---

## 🔮 v4.x — Derecho *(planned)*
- [~] Support for additional geostationary satellites (GOES partial; Meteosat/GK-2A engines present, incomplete RBG/processing)
- [x] Animated loop playback across time steps (shipped in v3.x)
- [ ] Reliable overlays (PAR, JMA, TCAD, Manila FIR, etc.) on SATAID format

---

## 🔮 v5.x — ENSO *(future)*
- [ ] 3D Earth visualization for combined GeoSat imagery
- [ ] macOS / Linux support exploration

---

> Version names follow the alphabet of weather phenomena:
> A. Aurora · B. Blizzard · C. Cyclone · D. Derecho · E. ENSO · F. Fog · G. Graupel · H. Hail · I. Icing · J. Jetstream · K. Katabatic · L. Lightning · M. Monsoon · N. Nor'easter · O. Orographic · P. Precipitation · Q. Quasigeostrophic · R. Rain · S. Squall · T. Thunderstorm · U. Updraft · V. Vortex · W. Waterspout · X. Xeric · Y. Yamase · Z. Zonda
