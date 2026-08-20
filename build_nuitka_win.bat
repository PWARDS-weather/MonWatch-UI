@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set OUTDIR=dist\MonWatch\Windows
set LOGFILE=build_nuitka_log.txt

echo ============================================ > "%LOGFILE%"
echo Building MonWatch with Nuitka for Windows >> "%LOGFILE%"
echo ============================================ >> "%LOGFILE%"
echo. >> "%LOGFILE%"
echo %date% %time% >> "%LOGFILE%"
echo. >> "%LOGFILE%"

echo ============================================
echo  Building MonWatch with Nuitka for Windows
echo ============================================
echo.

:: Install Nuitka and dependencies
python -m pip install -q nuitka zstandard 2>nul
if errorlevel 1 (
    echo [!] Failed to install Nuitka.
    exit /b 1
)

echo [*] Running Nuitka...
echo [*] Running Nuitka... >> "%LOGFILE%"
python -m nuitka ^
    --standalone ^
    --windows-console-mode=disable ^
    --lto=no ^
    --jobs=8 ^
    --assume-yes-for-downloads ^
    --windows-icon-from-ico=public\images\Splash.ico ^
    --plugin-enable=pyside6 ^
    --include-package=rasterio ^
    --include-package-data=rasterio ^
    --include-package=satpy ^
    --include-package-data=satpy ^
    --include-package=pyproj ^
    --include-package-data=pyproj ^
    --include-package=shapely ^
    --include-package-data=shapely ^
    --include-package=matplotlib ^
    --include-package-data=matplotlib ^
    --include-package=cartopy ^
    --include-package-data=cartopy ^
    --include-package=tifffile ^
    --include-package-data=tifffile ^
    --include-package=boto3 ^
    --include-package-data=boto3 ^
    --include-package=botocore ^
    --include-package-data=botocore ^
    --include-package=xarray ^
    --include-package-data=xarray ^
    --include-package=scipy ^
    --include-package-data=scipy ^
    --include-package=netCDF4 ^
    --include-package-data=netCDF4 ^
    --include-package=sataid ^
    --include-package-data=sataid ^
    --include-package=sounderpy ^
    --include-package-data=sounderpy ^
    --include-package=metpy ^
    --include-package-data=metpy ^
    --include-package=shapefile ^
    --include-package=certifi ^
    --include-package-data=certifi ^
    --include-package=urllib3 ^
    --include-package-data=urllib3 ^
    --include-data-dir=public=public ^
    --include-data-dir=data=data ^
    --include-data-dir=Process=Process ^
    --nofollow-import-to=IPython ^
    --nofollow-import-to=OpenGL ^
    --nofollow-import-to=PySide6.scripts ^
    --nofollow-import-to=fontTools ^
    --nofollow-import-to=cupy ^
    --nofollow-import-to=numba ^
    --nofollow-import-to=psutil ^
    --nofollow-import-to=imageio ^
    --nofollow-import-to=PyQt5 ^
    --nofollow-import-to=PyQt6 ^
    --nofollow-import-to=numpy.tests ^
    --nofollow-import-to=numpy._core.tests ^
    --nofollow-import-to=numpy.lib.tests ^
    --nofollow-import-to=numpy.fft.tests ^
    --nofollow-import-to=numpy.polynomial.tests ^
    --nofollow-import-to=numpy.random.tests ^
    --nofollow-import-to=numpy.typing.tests ^
    --nofollow-import-to=numpy.ma.tests ^
    --nofollow-import-to=numpy.matrixlib.tests ^
    --nofollow-import-to=numpy.linalg.tests ^
    --nofollow-import-to=numpy.conftest ^
    --nofollow-import-to=scipy.tests ^
    --nofollow-import-to=scipy.conftest ^
    --nofollow-import-to=scipy._lib.tests ^
    --nofollow-import-to=scipy.cluster.tests ^
    --nofollow-import-to=scipy.constants.tests ^
    --nofollow-import-to=scipy.datasets.tests ^
    --nofollow-import-to=scipy.differentiate.tests ^
    --nofollow-import-to=scipy.fft.tests ^
    --nofollow-import-to=scipy.fftpack.tests ^
    --nofollow-import-to=scipy.integrate.tests ^
    --nofollow-import-to=scipy.interpolate.tests ^
    --nofollow-import-to=scipy.io.tests ^
    --nofollow-import-to=scipy.linalg.tests ^
    --nofollow-import-to=scipy.ndimage.tests ^
    --nofollow-import-to=scipy.odr.tests ^
    --nofollow-import-to=scipy.optimize.tests ^
    --nofollow-import-to=scipy.signal.tests ^
    --nofollow-import-to=scipy.sparse.tests ^
    --nofollow-import-to=scipy.spatial.tests ^
    --nofollow-import-to=scipy.special.tests ^
    --nofollow-import-to=scipy.stats.tests ^
    --nofollow-import-to=matplotlib.tests ^
    --nofollow-import-to=matplotlib.testing ^
    --nofollow-import-to=xarray.tests ^
    --nofollow-import-to=pandas.tests ^
    --output-dir=%OUTDIR% ^
    --output-filename=MonWatch ^
    --remove-output ^
    src\UI.py >> "%LOGFILE%" 2>&1

if errorlevel 1 (
    echo [!] Nuitka build failed - check "%LOGFILE%" for details.
    echo [!] Nuitka build failed >> "%LOGFILE%"
    exit /b 1
)

echo [+] Nuitka completed successfully. >> "%LOGFILE%"

:: Nuitka creates a .dist folder for standalone
if exist "%OUTDIR%\UI.dist" (
    ren "%OUTDIR%\UI.dist" MonWatch_dist
    echo [+] Renamed UI.dist to MonWatch_dist >> "%LOGFILE%"
)

if exist "%OUTDIR%\launcher.dist" (
    ren "%OUTDIR%\launcher.dist" MonWatch_dist
    echo [+] Renamed launcher.dist to MonWatch_dist >> "%LOGFILE%"
)

echo [*] Copying shortcut script...
copy scripts\create_shortcut_win.vbs "%OUTDIR%\" >nul 2>nul

echo. >> "%LOGFILE%"
echo [+] Build complete: %OUTDIR%\ >> "%LOGFILE%"
echo %date% %time% >> "%LOGFILE%"
echo.
echo [+] Build complete: %OUTDIR%\
echo     Log saved to: %CD%\%LOGFILE%
echo.
pause
