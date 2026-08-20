@echo off
cd /d "%~dp0"
set OUTDIR=dist\MonWatch\Windows
echo ============================================
echo  Building MonWatch for Windows
echo ============================================
echo.
py -3 -m pip install -q pyinstaller 2>nul
if errorlevel 1 (
    echo [!] Failed to install PyInstaller. Make sure Python 3 is installed.
    exit /b 1
)
echo [*] Running PyInstaller...
py -3 -m PyInstaller MonWatch.spec --distpath %OUTDIR% --workpath build_win --noconfirm
if errorlevel 1 (
    echo [!] PyInstaller build failed
    exit /b 1
)
echo [*] Flattening output structure...
if exist "%OUTDIR%\MonWatch" (
    echo [*] Copying files from subfolder...
    robocopy "%OUTDIR%\MonWatch" "%OUTDIR%" /e /move /njh /njs /ndl >nul
    rmdir /s /q "%OUTDIR%\MonWatch" 2>nul
)
echo [*] Moving data folders from _internal to root...
for %%d in (public data Process) do (
    if exist "%OUTDIR%\_internal\%%d" (
        if not exist "%OUTDIR%\%%d" (
            move /y "%OUTDIR%\_internal\%%d" "%OUTDIR%\" >nul 2>nul
        )
    )
)
if exist "%OUTDIR%\_internal\src" (
    if exist "%OUTDIR%\src" (
        rmdir /s /q "%OUTDIR%\src"
    )
    move /y "%OUTDIR%\_internal\src" "%OUTDIR%\" >nul 2>nul
)
echo [*] Copying shortcut script...
copy scripts\create_shortcut_win.vbs "%OUTDIR%\" >nul
echo.
echo [+] Build complete: %OUTDIR%\
echo     %OUTDIR%\MonWatch.exe
echo.
