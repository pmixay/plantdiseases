@echo off
rem ─────────────────────────────────────────────────────────────────────
rem  PlantDiseases Server — Windows startup script
rem
rem  Creates a virtual environment, installs lightweight (CPU-only)
rem  PyTorch + dependencies, and starts the FastAPI server.
rem
rem  Usage:
rem      start.bat                  &  start server (default port 8000)
rem      start.bat --port 9000      &  custom port
rem      start.bat --train          &  train models before starting
rem ─────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

cd /d "%~dp0"

set PORT=8000
set TRAIN=false

rem ── Parse arguments ────────────────────────────────────────────────
:parse_args
if "%~1"=="" goto args_done
if "%~1"=="--port" (
    set PORT=%~2
    shift
    shift
    goto parse_args
)
if "%~1"=="--train" (
    set TRAIN=true
    shift
    goto parse_args
)
if "%~1"=="-h" goto show_help
if "%~1"=="--help" goto show_help
echo Unknown option: %~1
exit /b 1

:show_help
echo Usage: start.bat [--port PORT] [--train]
echo.
echo   --port PORT   Server port (default: 8000)
echo   --train       Train models before starting the server
exit /b 0

:args_done

echo ======================================================
echo        PlantDiseases Server — Startup
echo ======================================================
echo.

rem ── Check Python ───────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not found in PATH.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [+] Using Python %PYVER%

rem ── Create virtual environment ─────────────────────────────────────
if not exist "venv" (
    echo [+] Creating virtual environment...
    python -m venv venv
)

echo [+] Activating virtual environment...
call venv\Scripts\activate.bat

rem ── Install dependencies ───────────────────────────────────────────
echo [+] Installing PyTorch (CPU-only — lightweight)...
pip install --quiet --upgrade pip
pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu

if "%TRAIN%"=="true" (
    echo [+] Installing training dependencies...
    pip install --quiet -r requirements-train.txt
) else (
    echo [+] Installing server dependencies...
    pip install --quiet -r requirements.txt
)

rem ── Create models directory ────────────────────────────────────────
if not exist "models" mkdir models

rem ── Optional training ──────────────────────────────────────────────
if "%TRAIN%"=="true" (
    echo.
    echo [+] Starting model training...
    python train.py
    echo.
)

rem ── Check models ───────────────────────────────────────────────────
echo.
if exist "models\detector.pth" (
    if exist "models\classifier.pth" (
        echo [+] Both models found — running in FULL mode
        goto start_server
    )
)

if exist "models\detector.pth" (
    echo [!] Only detector model found — running in PARTIAL mode
    goto start_server
)
if exist "models\classifier.pth" (
    echo [!] Only classifier model found — running in PARTIAL mode
    goto start_server
)

echo [!] No trained models found — running in DEMO mode
echo     To train models:  start.bat --train
echo     (requires data\ directory with PlantVillage dataset)

:start_server
echo.
echo [+] Starting server on http://0.0.0.0:%PORT%
echo     API docs:  http://localhost:%PORT%/docs
echo     Health:    http://localhost:%PORT%/api/health
echo.

uvicorn main:app --host 0.0.0.0 --port %PORT%

if errorlevel 1 (
    echo.
    echo Server exited with an error. Press any key to close.
    pause >nul
)
