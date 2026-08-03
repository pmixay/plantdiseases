@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating the local admin-panel environment...
  where py >nul 2>nul
  if errorlevel 1 (
    python -m venv .venv
  ) else (
    py -3 -m venv .venv
  )
  if errorlevel 1 goto python_error
)

echo Checking admin-panel dependencies...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -r requirements.txt
if errorlevel 1 goto install_error
".venv\Scripts\python.exe" run.py %*
exit /b %errorlevel%

:python_error
echo Python 3.11 or newer is required. Install Python, then run this script again.
exit /b 1

:install_error
echo Could not install the admin-panel dependencies.
exit /b 1
