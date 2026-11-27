@echo off
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0"
.venv\Scripts\python main.py preview --script examples\sample_script.json
start "" "data\images\blind-preview"
pause
