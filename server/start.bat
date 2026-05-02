@echo off
rem PlantDiseases Server - Windows startup script.
rem
rem Creates a virtual environment, installs CPU-only PyTorch + server
rem dependencies, and starts the FastAPI server.
rem
rem Usage:
rem     start.bat                 start server (default port 8000)
rem     start.bat --port 9000     custom port
rem
rem Models are trained in Colab via train_notebook.ipynb. Drop the
rem resulting detector.{pt,onnx}, classifier.{pth,onnx} and
rem classes.json into server\models\ before starting - the server is
rem fail-fast and will refuse to start without trained weights.
setlocal enabledelayedexpansion

cd /d "%~dp0"

set PORT=8000

:parse_args
if "%~1"=="" goto args_done
if "%~1"=="--port" (
    set PORT=%~2
    shift
    shift
    goto parse_args
)
if "%~1"=="-h" goto show_help
if "%~1"=="--help" goto show_help
echo Unknown option: %~1
exit /b 1

:show_help
echo Usage: start.bat [--port PORT]
echo.
echo   --port PORT   Server port (default: 8000)
echo.
echo Train models in Colab via train_notebook.ipynb, then drop
echo detector.{pt,onnx}, classifier.{pth,onnx} and classes.json into
echo server\models\. The server will not start without them.
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

echo [+] Installing server dependencies...
pip install --quiet -r requirements.txt

if not exist "models" mkdir models
if not exist "logs" mkdir logs

rem Sanity-check trained artefacts before exec'ing uvicorn.
set DETECTOR_PRESENT=0
set CLASSIFIER_PRESENT=0
if exist "models\detector.pt"          set DETECTOR_PRESENT=1
if exist "models\detector.onnx"        set DETECTOR_PRESENT=1
if exist "models\classifier.pth"        set CLASSIFIER_PRESENT=1
if exist "models\classifier.onnx"       set CLASSIFIER_PRESENT=1
if exist "models\classifier_int8.onnx"  set CLASSIFIER_PRESENT=1
if "%DETECTOR_PRESENT%"=="0" goto missing_models
if "%CLASSIFIER_PRESENT%"=="0" goto missing_models
goto start_server

:missing_models
echo [!] Trained model artefacts missing under server\models\.
echo     Need detector.{pt,onnx} AND classifier.{pth,onnx,int8.onnx}.
echo     Train in Colab via train_notebook.ipynb, then copy them in.
exit /b 1

:start_server
echo.
echo [+] Starting server on http://0.0.0.0:%PORT%
echo     API docs:  http://localhost:%PORT%/docs
echo     Health:    http://localhost:%PORT%/api/health
echo.

if not defined FORWARDED_ALLOW_IPS set FORWARDED_ALLOW_IPS=127.0.0.1
uvicorn main:app --host 0.0.0.0 --port %PORT% --forwarded-allow-ips %FORWARDED_ALLOW_IPS% --proxy-headers
