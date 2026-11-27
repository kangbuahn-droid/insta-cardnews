@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo [1/4] 가상환경 만드는 중...
python -m venv .venv || (echo Python이 설치되지 않았어요. python.org 에서 설치하세요. & pause & exit /b 1)
echo [2/4] 패키지 설치 중...
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
echo [3/4] 카드 렌더링용 브라우저 설치 중...
.venv\Scripts\python -m playwright install chromium
echo [4/4] 설정 파일 열기
if not exist .env copy .env.example .env > nul
notepad .env
echo 설치 완료. .env 를 저장했으면 start.bat 을 실행하세요.
pause
