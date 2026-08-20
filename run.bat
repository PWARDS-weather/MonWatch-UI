@echo off
setlocal EnableExtensions EnableDelayedExpansion
title PWARDS Installer
color 0A

echo(
echo   +============================================================+
echo(  ^|                                                            ^|
echo(  ^|                         PWARDS                             ^|
echo(  ^|   (Pasacao Weather Atmospheric and Real-Time Data System)  ^|
echo   +============================================================+
echo   +============================================================+
echo(

cd /d "%~dp0"
echo Working directory: %CD%
echo(

set "MIN_VER=3.12"
set "LOG_FILE=%TEMP%\pwards_install.log"

>> "%LOG_FILE%" echo [%date% %time%] Starting from: %CD%

:: --- Find Python ---
call :find_python
if errorlevel 1 (
    call :download_python
    if errorlevel 1 (
        echo [!] Could not find or install Python %MIN_VER%+
        echo     Install it manually from: https://www.python.org/downloads/
        timeout /t 5 >nul
        exit /b 1
    )
)

>> "%LOG_FILE%" echo [%date% %time%] Using Python: %PYTHON_BIN% (%PYTHON_VER%)

:: --- Install dependencies ---
echo [*] Installing dependencies...
>> "%LOG_FILE%" echo [%date% %time%] Installing requirements
%PYTHON_BIN% -m pip install -q --user -r requirements.txt
if errorlevel 1 (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: pip install failed
    echo [!] Some packages failed to install. Check %LOG_FILE%
) else (
    echo [+] Dependencies installed
)

:: --- Launch ---
if not exist "Monson.py" (
    >> "%LOG_FILE%" echo [%date% %time%] ERROR: Monson.py not found
    echo [!] Monson.py not found
    dir /b
    timeout /t 5 >nul
    exit /b 1
)

echo [*] Launching MonWatch-UI...
>> "%LOG_FILE%" echo [%date% %time%] Launching: %PYTHON_BIN% Monson.py
start "" %PYTHON_BIN% Monson.py
echo [+] Log: %LOG_FILE%
timeout /t 2 >nul
exit /b 0


:: --- Functions ---

:find_python
for /f "tokens=2 delims= " %%V in ('"py" -3 --version 2^>nul') do (
    set "PYTHON_BIN=py -3"
    set "PYTHON_VER=%%V"
    >> "%LOG_FILE%" echo [%date% %time%] Found via py launcher: %%V
    exit /b 0
)
for %%I in (
    python
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "C:\Python312\python.exe"
    "%PROGRAMFILES%\Python312\python.exe"
) do (
    for /f "tokens=2" %%V in ('""%%~I" --version 2>&1"') do (
        for /f "tokens=1,2 delims=." %%A in ("%%V") do (
            if %%A.%%B GEQ %MIN_VER% (
                set "PYTHON_BIN=%%~I"
                set "PYTHON_VER=%%V"
                >> "%LOG_FILE%" echo [%date% %time%] Found: %%~I = %%V
                exit /b 0
            )
        )
    )
)
exit /b 1

:download_python
set "DL_VER=3.12.4"
set "INSTALLER=python-%DL_VER%-amd64.exe"
set "URL=https://www.python.org/ftp/python/%DL_VER%/%INSTALLER%"
set "TARGET=%LOCALAPPDATA%\Programs\Python\Python312"

echo [!] Python %MIN_VER%+ not found
echo [*] Downloading Python %DL_VER%...
>> "%LOG_FILE%" echo [%date% %time%] Downloading: %URL%

if exist "%INSTALLER%" del "%INSTALLER%"
powershell -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%URL%' -OutFile '%INSTALLER%' -ErrorAction Stop" || (
    echo [!] Download failed. Get it: %URL%
    >> "%LOG_FILE%" echo [%date% %time%] Download failed
    exit /b 1
)

echo [*] Installing (per-user, no admin needed)...
>> "%LOG_FILE%" echo [%date% %time%] Installing: %INSTALLER%
start /wait "" "%INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 TargetDir="%TARGET%" Include_test=0 Include_launcher=1 || (
    echo [!] Installation failed
    del "%INSTALLER%" 2>nul
    >> "%LOG_FILE%" echo [%date% %time%] Installation failed
    exit /b 1
)
del "%INSTALLER%" 2>nul

if exist "%TARGET%\python.exe" (
    set "PYTHON_BIN=%TARGET%\python.exe"
    for /f "tokens=2" %%V in ('""%TARGET%\python.exe" --version 2>&1"') do set "PYTHON_VER=%%V"
    >> "%LOG_FILE%" echo [%date% %time%] Installed: !PYTHON_VER!
    exit /b 0
)
exit /b 1
