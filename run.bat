@echo off
REM NeuroLens - one-command start for Windows
cd /d "%~dp0"
if not exist .venv (
  py -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo NeuroLens is starting at http://localhost:8000  (Ctrl+C to stop)
echo.
python -m uvicorn backend.app.main:app --reload --port 8000
