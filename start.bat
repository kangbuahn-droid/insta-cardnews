@echo off
chcp 65001 > nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo 카드뉴스 봇 실행 중... 이 창을 닫으면 멈춥니다.
.venv\Scripts\python main.py run
pause
