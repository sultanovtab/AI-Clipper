@echo off
setlocal
cd /d D:\AI-Clipper

set "PY=D:\AI-Clipper\.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:8765"

if not exist "%PY%" (
    echo.
    echo ERROR: Python environment not found.
    echo %PY%
    echo.
    pause
    exit /b 1
)

"%PY%" -c "import fastapi, uvicorn" >nul 2>&1

if errorlevel 1 (
    echo.
    echo ERROR: Required packages are missing.
    echo Run:
    echo "%PY%" -m pip install -r "D:\AI-Clipper\requirements.txt"
    echo.
    pause
    exit /b 1
)

echo Starting AI Clipper...

start "AI Clipper Server" /D "D:\AI-Clipper" "%PY%" -m uvicorn app:app --host 127.0.0.1 --port 8765

timeout /t 2 /nobreak >nul

start "" "%URL%"

exit /b 0
