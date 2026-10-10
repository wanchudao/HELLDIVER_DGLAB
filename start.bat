@echo off
setlocal
title HELLDIVER_DGLAB V0.3

set "PROJECT_DIR=%~dp0"
set "BACKEND_DIR=%PROJECT_DIR%official_v2\socket\v2\backend"
set "SENDER_MODE=mock"
set "LAUNCH_EXIT=0"

if "%~1"=="" goto check_args
if /I "%~1"=="--help" goto usage
if /I "%~1"=="--hardware" goto choose_hardware
echo ERROR: Unknown option: %~1
exit /b 2

:choose_hardware
set "SENDER_MODE=dglab"

:check_args
if not "%~2"=="" goto bad_args
if not exist "%PROJECT_DIR%src\runner_real.py" goto missing_project
if exist "%PROJECT_DIR%config.ini" goto choose_python
if not exist "%PROJECT_DIR%config.example.ini" goto missing_config

:choose_python
set "PYTHON_EXE=python"
if exist "%PROJECT_DIR%.venv\Scripts\python.exe" set "PYTHON_EXE=%PROJECT_DIR%.venv\Scripts\python.exe"
"%PYTHON_EXE%" -c "import sys; sys.exit(sys.version_info[:2] < (3, 10))" >nul 2>nul
if errorlevel 1 goto missing_python
"%PYTHON_EXE%" -c "import cv2, numpy, mss" >nul 2>nul
if errorlevel 1 goto missing_python_deps
pushd "%PROJECT_DIR%"
if errorlevel 1 goto missing_project

echo =================================================
echo HELLDIVER_DGLAB V0.3 - starts PAUSED
echo Enter a ground mission, then press F8 or click ARM FOR BATTLE.
echo F8 or STOP OUTPUT NOW immediately locks output. ESC / Ctrl+C exits.
echo Use borderless window mode for the control panel.
echo =================================================
if /I "%SENDER_MODE%"=="dglab" goto hardware

echo MOCK mode: events are logged, no device connection.
echo No Node backend or pairing is started in this mode.
"%PYTHON_EXE%" src\runner_real.py --enable-trigger --sender-mode mock
set "LAUNCH_EXIT=%errorlevel%"
goto finish

:hardware
echo HARDWARE mode was explicitly requested with --hardware.
echo Real device output is possible after pairing and explicitly arming with F8/button.
if not exist "config.ini" goto needs_calibration
"%PYTHON_EXE%" -c "from src.config_loader import load_config; c=load_config('config.ini'); raise SystemExit(0 if c.get('calibration', {}).get('calibrated') == 1 else 1)"
if errorlevel 1 goto needs_calibration
"%PYTHON_EXE%" -c "import websocket" >nul 2>nul
if errorlevel 1 goto hardware_python_deps
if not exist "%BACKEND_DIR%package.json" goto missing_backend
if not exist "%BACKEND_DIR%src\index.js" goto missing_backend
where node >nul 2>nul
if errorlevel 1 goto missing_node
where npm >nul 2>nul
if errorlevel 1 goto missing_node
node -e "process.exit(Number(process.versions.node.split('.')[0]) >= 18 ? 0 : 1)" >nul 2>nul
if errorlevel 1 goto missing_node
pushd "%BACKEND_DIR%"
node -e "require('ws'); require('winston'); require('dotenv'); require('uuid')" >nul 2>nul
set "DEPS_EXIT=%errorlevel%"
popd
if not "%DEPS_EXIT%"=="0" goto missing_node_deps

echo Starting the local SOCKET v2 backend in a separate window.
echo Keep the phone and PC on a trusted LAN; pair with the APP.
start "DG-LAB Backend" /D "%BACKEND_DIR%" cmd /k npm start
timeout /t 5 /nobreak >nul
"%PYTHON_EXE%" src\runner_real.py --enable-trigger --sender-mode dglab
set "LAUNCH_EXIT=%errorlevel%"
echo The backend has its own window. Stop it with Ctrl+C when done.
goto finish

:needs_calibration
echo ERROR: Calibrate first: python tools\calibrate.py
echo Calibration must be done in a ground mission, not on the ship.
goto hardware_failed

:hardware_python_deps
echo ERROR: Install websocket-client in the selected Python environment.
goto hardware_failed

:missing_backend
echo ERROR: SOCKET v2 backend files are missing from this project.
goto hardware_failed

:missing_node
echo ERROR: Hardware mode requires Node.js 18+ and npm on PATH.
goto hardware_failed

:missing_node_deps
echo ERROR: Backend dependencies are missing. Run npm install in:
echo "%BACKEND_DIR%"
goto hardware_failed

:hardware_failed
set "LAUNCH_EXIT=1"
goto finish

:finish
popd
echo Runner exited with code %LAUNCH_EXIT%.
pause
exit /b %LAUNCH_EXIT%

:missing_python
echo ERROR: Python 3.10+ is required. A project .venv is preferred.
exit /b 1

:missing_python_deps
echo ERROR: Missing Python dependencies. Install requirements.txt first:
echo "%PYTHON_EXE%" -m pip install -r "%PROJECT_DIR%requirements.txt"
exit /b 1

:missing_project
echo ERROR: Run start.bat from the complete HELLDIVER_DGLAB checkout.
exit /b 1

:missing_config
echo ERROR: Both config.ini and config.example.ini are missing.
exit /b 1

:bad_args
echo ERROR: Use start.bat or start.bat --hardware.
exit /b 2

:usage
echo Usage: start.bat [--hardware]
echo Default: safe MOCK event feedback, no device or Node connection.
echo --hardware: calibrated local config, Node backend and real output.
echo --help: show this help without starting any process or window.
exit /b 0
