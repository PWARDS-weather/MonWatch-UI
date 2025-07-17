@echo off
setlocal enableextensions disabledelayedexpansion
title PWARDS Installer
color 0A

:: --- ASCII ART HEADER ---
echo(
echo   +============================================================+
echo(  ^|                                                            ^|
echo(  ^|                         PWARDS                             ^|
echo(  ^|   (Pasacao Weather Atmospheric and Real-Time Data System)  ^|
echo   +============================================================+
echo(

:: --- CONFIG ---
set "PYTHON_VER=3.12.4"
set "PYTHON_EXE=python-%PYTHON_VER%-amd64.exe"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VER%/%PYTHON_EXE%"
set "PYTHON_DIR=C:\Python312"

:: Initialize progress bar
call :initProgressBar "#" "."

:: --- MAIN INSTALLATION SEQUENCE ---
call :drawProgressBar 5 "Checking admin privileges..."
net session >nul 2>&1
if %errorlevel% neq 0 (
    call :drawProgressBar 5 "ERROR: Run as Administrator"
    timeout /t 3 >nul
    call :finalizeProgressBar
    exit /b 1
)

call :drawProgressBar 10 "Checking for Python %PYTHON_VER%..."
set "PYTHON_BIN="
set "PYTHON_FOUND="

:: Check system PATH
for /f "delims=" %%I in ('where python 2^>nul') do (
    for /f "tokens=1,2 delims= " %%A in ('"%%I" --version 2^>nul') do (
        if /i "%%A"=="Python" if "%%B"=="%PYTHON_VER%" (
            set "PYTHON_BIN=%%I"
            set "PYTHON_FOUND=1"
            goto python_checks_done
        )
    )
)

:: Check default install location
if exist "%PYTHON_DIR%\python.exe" (
    for /f "tokens=1,2 delims= " %%A in ('"%PYTHON_DIR%\python.exe" --version 2^>nul') do (
        if /i "%%A"=="Python" if "%%B"=="%PYTHON_VER%" (
            set "PYTHON_BIN=%PYTHON_DIR%\python.exe"
            set "PYTHON_FOUND=1"
            goto python_checks_done
        )
    )
)

:python_checks_done
if defined PYTHON_FOUND (
    call :drawProgressBar 20 "Found Python %PYTHON_VER%"
    goto run_application
)

:: Download Python
call :drawProgressBar 30 "Downloading Python %PYTHON_VER%..."
if exist "%PYTHON_EXE%" del "%PYTHON_EXE%"
powershell -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_EXE%' -UseBasicParsing" || (
    call :drawProgressBar 30 "ERROR: Download failed"
    call :finalizeProgressBar
    exit /b 1
)

:: Install Python
call :drawProgressBar 50 "Installing Python..."
start /wait "" "%PYTHON_EXE%" /quiet InstallAllUsers=1 PrependPath=1 TargetDir="%PYTHON_DIR%" || (
    call :drawProgressBar 50 "ERROR: Installation failed"
    call :finalizeProgressBar
    exit /b 1
)
set "PYTHON_BIN=%PYTHON_DIR%\python.exe"
del "%PYTHON_EXE%" >nul 2>&1

:: Verify Python installation
call :drawProgressBar 60 "Verifying Python..."
"%PYTHON_BIN%" --version >nul 2>&1 || (
    call :drawProgressBar 60 "ERROR: Python verification failed"
    call :finalizeProgressBar
    exit /b 1
)

:run_application
call :drawProgressBar 80 "Launching Monson.py in new window..."

:: Get the directory where this batch file is located
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Launch in new window and exit this one
start "" "%PYTHON_BIN%" Monson.py
call :drawProgressBar 100 "Installation Complete!"
timeout /t 2 >nul
call :finalizeProgressBar
exit /b 0

:: --- PROGRESS BAR FUNCTIONS ---
:drawProgressBar value [text]
    if "%~1"=="" goto :eof
    if not defined pb.barArea call :initProgressBar
    setlocal enableextensions enabledelayedexpansion
    set /a "pb.value=%~1 %% 101", "pb.filled=pb.value*pb.barArea/100", "pb.dotted=pb.barArea-pb.filled", "pb.pct=1000+pb.value"
    set "pb.pct=%pb.pct:~-3%"
    if "%~2"=="" ( set "pb.text=" ) else ( 
        set "pb.text=%~2%pb.back%" 
        set "pb.text=!pb.text:~0,%pb.textArea%!"
    )
    <nul set /p "pb.prompt=[!pb.fill:~0,%pb.filled%!!pb.dots:~0,%pb.dotted%!][ %pb.pct% ] %pb.text%!pb.cr!"
    endlocal
    goto :eof

:initProgressBar [fillChar] [dotChar]
    if defined pb.cr call :finalizeProgressBar
    for /f %%a in ('copy "%~f0" nul /z') do set "pb.cr=%%a"
    if "%~1"=="" ( set "pb.fillChar=#" ) else ( set "pb.fillChar=%~1" )
    if "%~2"=="" ( set "pb.dotChar=." ) else ( set "pb.dotChar=%~2" )
    set "pb.console.columns="
    for /f "tokens=2 skip=4" %%f in ('mode con') do if not defined pb.console.columns set "pb.console.columns=%%f"
    set /a "pb.barArea=pb.console.columns/2-2", "pb.textArea=pb.barArea-9"
    set "pb.fill="
    setlocal enableextensions enabledelayedexpansion
    for /l %%p in (1 1 %pb.barArea%) do set "pb.fill=!pb.fill!%pb.fillChar%"
    set "pb.fill=!pb.fill:~0,%pb.barArea%!"
    set "pb.dots=!pb.fill:%pb.fillChar%=%pb.dotChar%!"
    set "pb.back=!pb.fill:~0,%pb.textArea%!
    set "pb.back=!pb.back:%pb.fillChar%= !"
    endlocal & set "pb.fill=%pb.fill%" & set "pb.dots=%pb.dots%" & set "pb.back=%pb.back%"
    goto :eof

:finalizeProgressBar [erase]
    if defined pb.cr (
        if not "%~1"=="" (
            setlocal enabledelayedexpansion
            set "pb.back="
            for /l %%p in (1 1 %pb.console.columns%) do set "pb.back=!pb.back! "
            <nul set /p "pb.prompt=!pb.cr!!pb.back:~1!!pb.cr!"
            endlocal
        )
    )
    for /f "tokens=1 delims==" %%v in ('set pb.') do set "%%v="
    goto :eof