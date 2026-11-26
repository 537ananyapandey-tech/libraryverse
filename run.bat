@echo off
cd /d "%~dp0"
title LIBRARYVERSE
set PY=python
where python >nul 2>nul || set PY=py
where %PY% >nul 2>nul || (
  echo Python was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add Python to PATH" in the installer, then double-click run.bat again.
  pause
  exit /b 1
)
echo Installing requirements (first run only)...
%PY% -m pip install -r requirements.txt
echo.
echo Starting LIBRARYVERSE. Your browser will open in a few seconds.
echo Keep this window open. Close it to stop the server.
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:5000"
%PY% app.py
pause
