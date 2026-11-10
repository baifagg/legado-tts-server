@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found. Run: python install.py
    pause
    exit /b 1
  )
  set "PY=python"
)

echo Starting Legado TTS Server ...
"%PY%" run.py --port 18760
pause
