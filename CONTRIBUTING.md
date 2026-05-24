# Contributing to MonWatch-UI

Thanks for your interest in contributing to MonWatch-UI! This project is maintained by [PWARDS-weather](https://github.com/PWARDS-weather) and welcomes contributions from the community — whether you're fixing a bug, improving the UI, or adding new features.

---

## Getting Started

### 1. Fork and Clone

```
git clone https://github.com/YOUR-USERNAME/MonWatch-UI.git
cd MonWatch-UI
```

### 2. Set Up Your Environment

Make sure you have Python 3.10+ installed, then install dependencies:

```
pip install -r requirements.txt
```

### 3. Run the App

```
python Monson.py
```

Or just double-click `run.bat` on Windows.

---

## How to Contribute

### Reporting Bugs

Open an [issue](https://github.com/PWARDS-weather/MonWatch-UI/issues) and include:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Your OS and Python version

### Suggesting Features

Open an issue with the label `enhancement`. Describe the feature and why it would be useful for meteorology or satellite data workflows.

### Submitting a Pull Request

1. Create a new branch from `main`:
   ```
   git checkout -b feature/your-feature-name
   ```
2. Make your changes
3. Test that the app still launches and works correctly
4. Commit with a clear message:
   ```
   git commit -m "Add: brief description of your change"
   ```
5. Push and open a Pull Request against `main`

---

## Code Style

- Keep it readable — this project is also meant to help beginners learn
- Add comments where the logic isn't obvious
- Avoid introducing new dependencies unless necessary — keep the install lightweight

---

## Areas Where Help Is Needed

If you're looking for somewhere to start, these are current priorities:

- [ ] Sea Surface Temperature (SST) layer support
- [ ] Export view as PNG or GeoTIFF
- [ ] Support for GOES and Meteosat satellite data
- [ ] UI stability improvements and error handling
- [ ] macOS/Linux compatibility exploration

---

## Questions?

Open an issue or reach out via the [PWARDS Facebook page](https://www.facebook.com/share/14ShA5G2Wcv/).

Thanks for helping make MonWatch-UI better!
