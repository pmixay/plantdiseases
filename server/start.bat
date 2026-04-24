@echo off
rem PlantDiseases Server - Windows startup script.
rem
rem Creates a virtual environment, installs CPU-only PyTorch + server
rem dependencies, and starts the FastAPI server.
rem
rem Usage:
rem     start.bat                 start server (default port 8000)
rem     start.bat --port 9000     custom port
rem     start.bat --train         train models before starting
setlocal enabledelayedexpansion

cd /d "%~dp0"

set PORT=8000
set TRAIN=false

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

echo PlantDiseases Server - startup
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not found in PATH.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo Make sure "Add Python to PATH" is checked during installation.
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [+] Using Python %PYVER%

if not exist "venv" (
    echo [+] Creating virtual environment...
    python -m venv venv
)

echo [+] Activating virtual environment...
call venv\Scripts\activate.bat

echo [+] Installing PyTorch (CPU-only)...
pip install --quiet --upgrade pip
pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu

if "%TRAIN%"=="true" (
    echo [+] Installing training dependencies...
    pip install --quiet -r requirements-train.txt
) else (
    echo [+] Installing server dependencies...
    pip install --quiet -r requirements.txt
)

if not exist "models" mkdir models
if not exist "logs" mkdir logs

if "%TRAIN%"=="true" (
    echo.
    echo [+] Starting model training...
    python train.py
    echo.
)

echo.
if exist "models\detector.pt" (
    if exist "models\classifier.pth" (
        echo [+] Both models found - running in FULL mode
        goto start_server
    )
)
if exist "models\detector.pt" (
    echo [!] Only detector model found - running in PARTIAL mode
    goto start_server
)
if exist "models\classifier.pth" (
    echo [!] Only classifier model found - running in PARTIAL mode
    goto start_server
)
echo [!] No trained models found - running in DEMO mode
echo     Train in Colab via train_notebook.ipynb, then copy detector.pt,
echo     classifier.pth and classes.json into server\models\

:start_server
echo.
echo [+] Starting server on http://0.0.0.0:%PORT%
echo     API docs:  http://localhost:%PORT%/docs
echo     Health:    http://localhost:%PORT%/api/health
echo.

if not defined FORWARDED_ALLOW_IPS set FORWARDED_ALLOW_IPS=127.0.0.1
uvicorn main:app --host 0.0.0.0 --port %PORT% --forwarded-allow-ips %FORWARDED_ALLOW_IPS% --proxy-headers
