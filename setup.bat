@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo [1/4] Creating virtual env...
python -m venv .venv || (echo Python not found. Install it from python.org first. & pause & exit /b 1)
echo [2/4] Installing packages...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
echo [3/4] Installing browser for card rendering...
.venv\Scripts\python -m playwright install chromium
echo [4/4] Opening .env - fill in your keys and save.
if not exist .env copy .env.example .env > nul
notepad .env
echo Done. After saving .env, double-click start.bat
pause
