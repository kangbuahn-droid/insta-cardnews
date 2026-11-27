#!/usr/bin/env bash
# GitHub 에 새 코드가 올라오면 자동으로 받아서 봇을 다시 빌드·재시작한다.
# install-auto-update.sh 가 cron 에 5분마다 실행되도록 등록한다.
set -u
cd "$(dirname "$0")/.." || exit 1
exec 9>/tmp/insta-cardnews-update.lock
flock -n 9 || exit 0   # 이미 업데이트 중이면 건너뜀

docker_cmd() { if docker info >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi; }
notify() {
  local token chat
  token=$(grep -E '^TELEGRAM_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '\r "'"'")
  chat=$(grep -E '^TELEGRAM_CHAT_ID=' .env | cut -d= -f2- | tr -d '\r "'"'")
  [ -n "$token" ] && [ -n "$chat" ] && curl -s -m 20 "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" >/dev/null
}

branch=$(git rev-parse --abbrev-ref HEAD)
git fetch -q origin "$branch" || { echo "$(date) fetch failed"; exit 1; }
local_rev=$(git rev-parse HEAD); remote_rev=$(git rev-parse "origin/$branch")
[ "$local_rev" = "$remote_rev" ] && exit 0

echo "$(date) update $local_rev -> $remote_rev"
# 서버에서 직접 고친 파일이 있으면 치워두고(git stash) 받는다. 설정은 .env 에 두면 안전하다.
if ! git diff --quiet || ! git diff --cached --quiet; then
  git stash push -q -m "auto-update $(date +%F_%T)"
fi
if ! git merge -q --ff-only "origin/$branch"; then
  notify "⚠️ 자동 업데이트 실패: 코드를 받지 못했어요. 서버에서 확인이 필요해요."
  exit 1
fi
summary=$(git log --format='- %s' "$local_rev..$remote_rev" | head -5)
git log -1 --format='%cd %s' --date=format:'%m/%d %H:%M' > data/version.txt
if docker_cmd compose up -d --build --force-recreate >/tmp/insta-cardnews-build.log 2>&1; then
  notify "🔄 새 기능이 적용됐어요
$summary"
  docker_cmd image prune -f >/dev/null 2>&1
else
  notify "⚠️ 자동 업데이트 중 빌드 실패. 이전 버전이 계속 돌고 있어요.
$(tail -5 /tmp/insta-cardnews-build.log)"
  exit 1
fi
