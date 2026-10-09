@echo off
rem PlantDiseases Server - Windows startup script.
rem
rem Creates a virtual environment, installs the server dependencies, and
rem starts the FastAPI server.
rem
rem Usage:
rem     start.bat                 start server (default port 8000)
rem     start.bat --port 9000     custom port
rem
rem Models are trained only in Colab via train_notebook.ipynb. Extract one
rem complete notebook-produced bundle into server\models\, including
rem bundle_manifest.json and nested directories. Do not mix runs. Startup
rem verifies every manifest-bound artefact before loading a model.
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
echo Train models in Colab via train_notebook.ipynb, then extract one complete
echo bundle into server\models\, including bundle_manifest.json and all nested
echo paths. Do not copy loose files or combine different runs.
exit /b 0

:args_done

echo PlantDiseases Server - startup
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not found in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/ - 3.14 is what CI tests.
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

pip install --quiet --upgrade pip

echo [+] Installing server dependencies...
pip install --quiet -r requirements.txt

if not exist "models" mkdir models
if not exist "logs" mkdir logs

rem Shallow preflight only. The lifespan verifier authoritatively validates the
rem complete manifest, hashes, serving contract, and exact selected graph.
set DETECTOR_PRESENT=0
set BUNDLE_MANIFEST_PRESENT=0
if exist "models\detector.onnx"        set DETECTOR_PRESENT=1
if exist "models\bundle_manifest.json"  set BUNDLE_MANIFEST_PRESENT=1
if "%DETECTOR_PRESENT%"=="0" goto missing_models
if "%BUNDLE_MANIFEST_PRESENT%"=="0" goto missing_models
echo [+] Verifying immutable model bundle...
python tools\verify_bundle.py models
if errorlevel 1 exit /b 1
goto start_server

:missing_models
echo [!] Complete model bundle missing under server\models\.
echo     Extract one full archive produced by train_notebook.ipynb,
echo     preserving bundle_manifest.json and all nested paths.
echo     Do not copy loose artefacts or combine different runs.
exit /b 1

:start_server
echo.
echo [+] Starting server on http://0.0.0.0:%PORT%
echo     API docs:  http://localhost:%PORT%/docs
echo     Health:    http://localhost:%PORT%/api/health
echo.

if not defined FORWARDED_ALLOW_IPS set FORWARDED_ALLOW_IPS=127.0.0.1
uvicorn main:app --host 0.0.0.0 --port %PORT% --forwarded-allow-ips %FORWARDED_ALLOW_IPS% --proxy-headers --limit-concurrency 64 --timeout-keep-alive 5 --no-access-log
