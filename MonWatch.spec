# -*- mode: python ; coding: utf-8 -*-
import sys
import os
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

block_cipher = None

try:
    root = os.path.dirname(os.path.abspath(__file__))
except NameError:
    root = os.getcwd()

# Collect all rasterio submodules to avoid missing C extensions
rasterio_submods = collect_submodules('rasterio')
rasterio_datas = collect_data_files('rasterio')
rasterio_bins = collect_dynamic_libs('rasterio')

a = Analysis(
    ['launcher.py'],
    pathex=[root],
    binaries=rasterio_bins,
    datas=[
        (os.path.join(root, 'public'), 'public'),
        (os.path.join(root, 'data'), 'data'),
        (os.path.join(root, 'Process'), 'Process'),
        (os.path.join(root, 'src'), 'src'),
    ] + rasterio_datas,
    hiddenimports=[
        'PySide6.QtOpenGLWidgets',
        'numpy',
        'scipy',
        'scipy.ndimage',
        'rasterio',
    ] + rasterio_submods + [
        'satpy',
        'pyproj',
        'shapely',
        'shapely.geometry',
        'pygrib',
        'GDAL',
        'skimage',
        'skimage.transform',
        'skimage.measure',
        'vispy',
        'vispy.app',
        'vispy.scene',
        'matplotlib',
        'matplotlib.backends.backend_agg',
        'cartopy',
        'cartopy.crs',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'tifffile',
        'imageio',
        'imageio.v2',
        'ffmpeg_python',
        'boto3',
        'botocore',
        'requests',
        'dask',
        'xarray',
        'yaml',
        'SQLAlchemy',
        'appdirs',
        'cupy',
        'numba',
        'src',
        'src.App',
        'src.Engine',
        'src.Workers',
        'src.helpers',
        'src.settings',
        'src.components',
        'src.dialogs',
        'src.products',
        'src.Centerviewport',
        'src.MultiViewportWindow',
        'src.Globe3DWidget',
        'src.nhc_client',
        'src.goes_cache',
        'src.sataid_reader',
        'src.sataid_cache_manager',
        'src.cache_manager',
        'src.animation_exporter',
        'src.AMVWorker',
        'src.TearOffTabBar',
    ],
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PyQt4', 'PySide2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MonWatch',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(root, 'public', 'images', 'Splash.ico')
    if os.path.exists(os.path.join(root, 'public', 'images', 'Splash.ico'))
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MonWatch',
)
