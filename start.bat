@echo off
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo Card news bot is running. Closing this window stops it.
.venv\Scripts\python main.py run
pause
