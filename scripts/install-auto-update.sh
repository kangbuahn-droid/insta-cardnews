#!/usr/bin/env bash
# 한 번만 실행: 자동 업데이트(5분마다 GitHub 확인)를 켠다.
set -eu
cd "$(dirname "$0")/.."
DIR=$(pwd)
chmod +x scripts/auto-update.sh

# 카드에 찍히는 인스타 아이디를 .env 로 옮긴다(yaml 을 직접 고치면 업데이트와 충돌하므로).
if ! grep -qE '^CARD_HANDLE=.+' .env 2>/dev/null; then
  handle=""
  for ref in $(git stash list --format=%gd 2>/dev/null) HEAD; do
    for f in channels/ai_tips.yaml channels/ai_news.yaml; do
      h=$(git show "$ref:$f" 2>/dev/null | grep -E '^handle:' | head -1 | sed -E 's/^handle:[[:space:]]*"?([^"]*)"?.*/\1/')
      if [ -n "$h" ] && [ "$h" != "@your_handle" ]; then handle=$h; break 2; fi
    done
  done
  for f in channels/*.yaml; do  # 아직 치우지 않은 직접 수정도 확인
    h=$(grep -E '^handle:' "$f" | head -1 | sed -E 's/^handle:[[:space:]]*"?([^"]*)"?.*/\1/')
    if [ -z "$handle" ] && [ -n "$h" ] && [ "$h" != "@your_handle" ]; then handle=$h; fi
  done
  if [ -n "$handle" ]; then
    printf '\nCARD_HANDLE=%s\n' "$handle" >> .env
    echo "Card handle saved to .env: $handle"
  else
    echo "Card handle not found. Add a line like  CARD_HANDLE=@metafooo  to .env"
  fi
fi

# cron 등록(중복 방지)
line="*/5 * * * * $DIR/scripts/auto-update.sh >> $DIR/data/auto-update.log 2>&1"
mkdir -p data
( crontab -l 2>/dev/null | grep -v 'scripts/auto-update.sh' ; echo "$line" ) | crontab -
echo "Auto update is ON (checks GitHub every 5 minutes)."

# 지금 바로 한 번 적용
git stash push -q -m "before auto-update setup" 2>/dev/null || true
git pull -q --ff-only || true
if docker info >/dev/null 2>&1; then docker compose up -d --build --force-recreate; else sudo docker compose up -d --build --force-recreate; fi
echo "DONE. From now on, code updates are applied automatically."
