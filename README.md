# insta-cardnews

블라인드 등 커뮤니티 글을 AI로 재구성해 인스타그램 카드뉴스(캐러셀)로 만들고, 텔레그램 승인 후 예약 시간에 자동 게시하는 프로그램입니다.

```
글 접수(텔레그램 붙여넣기 / inbox 폴더) → Claude로 슬라이드 작성 → 1080×1350 이미지 렌더
  → 텔레그램 미리보기 [승인/지금 게시/재작성/폐기] → 예약 시간에 Instagram Graph API로 게시
```

## 폴더 구조

| 경로 | 역할 |
| --- | --- |
| `channels/*.yaml` | 채널 설정(말투, 게시 시간, 해시태그, 인스타 계정). 파일 하나 = 채널 하나 |
| `templates/<이름>/card.html` | 카드 디자인(HTML/CSS). 디자인 수정은 여기만 고치면 됨 |
| `app/writer.py` | Claude로 원문 → 슬라이드 JSON 재구성 |
| `app/renderer.py` | HTML 템플릿 → JPEG |
| `app/telegram.py` | 검수 봇 |
| `app/instagram.py` | 캐러셀 게시, 토큰 자동 갱신 |
| `app/pipeline.py` | 단계 연결, 예약 게시 |
| `inbox/<채널>/` | 여기에 `.txt`/`.json` 글을 넣으면 1분 안에 접수 |

## 처음 준비할 것 (한 번만)

### 1. 인스타그램 계정
1. 새 인스타 계정 생성 → 설정 > 계정 유형 > **프로페셔널 계정(크리에이터 또는 비즈니스)** 으로 전환
2. [Meta for Developers](https://developers.facebook.com/)에서 앱 생성 → 사용 사례로 **Instagram API (Instagram 로그인 방식)** 추가
3. 앱의 Instagram 설정에서 내 인스타 계정을 테스터로 추가하고 **액세스 토큰 생성** (권한: `instagram_business_basic`, `instagram_business_content_publish`)
4. 생성된 토큰과 인스타 사용자 ID를 `.env`의 `IG_ACCESS_TOKEN`, `IG_USER_ID`에 입력
   - 내 계정에만 올리는 용도라 앱 심사 없이 개발 모드로 사용 가능합니다.
   - 장기 토큰(60일)은 프로그램이 50일마다 자동 갱신합니다.

### 2. 텔레그램 봇
1. 텔레그램에서 `@BotFather` → `/newbot` → 토큰을 `TELEGRAM_BOT_TOKEN`에 입력
2. 만든 봇에게 아무 메시지나 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates` 에서 `chat.id` 를 찾아 `TELEGRAM_CHAT_ID`에 입력 (이 채팅방 메시지만 처리합니다)

### 3. 이미지 저장소 (Cloudflare R2)
인스타 API는 공개 URL의 이미지만 받습니다.
1. Cloudflare R2 버킷 생성 → 설정에서 **Public access(r2.dev)** 켜기 → 주소를 `PUBLIC_BASE_URL`에
2. R2 API 토큰 생성 → `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ENDPOINT_URL`에 입력

### 4. Claude API 키
[console.anthropic.com](https://console.anthropic.com)에서 키 발급 → `ANTHROPIC_API_KEY`

## 실행

### 서버(권장)
```bash
cp .env.example .env   # 값 채우기
docker compose up -d --build
docker compose logs -f
```
작은 VPS(1 vCPU, 1~2GB RAM)면 충분합니다.

### 로컬에서 카드만 미리 보기
```bash
pip install -r requirements.txt && playwright install chromium
python main.py preview --script examples/sample_script.json   # AI 없이 디자인 확인
python main.py preview examples/sample_post.txt               # AI 작성까지 (API 키 필요)
```
이미지는 `data/images/` 에 저장됩니다.

## 사용법

**글 보내기**: 텔레그램 봇에 블라인드 글을 붙여넣어 보냅니다.
```
제목 한 줄
본문...
---
댓글1
댓글2
```
다른 채널에 넣으려면 첫 줄에 `#채널이름`. 또는 같은 형식의 `.txt`나 JSON(`title`, `body`, `comments`, `url`)을 `inbox/blind/`에 넣어도 됩니다.

**검수**: 미리보기 아래 버튼
- ✅ 승인(예약): `schedule`의 다음 게시 시간에 올라감 (승인된 순서대로 한 슬롯에 1개)
- 🚀 지금 게시
- ✏️ 재작성: 봇 메시지에 답장으로 수정 요청("표지를 더 짧게" 등)
- 🗑 폐기

`/status` 로 대기열 확인.

## 새 채널 추가
1. `channels/blind.yaml` 복사 → `name`, `persona`, `schedule`, `hashtags` 수정
2. 다른 인스타 계정을 쓰면 `instagram.user_id_env`/`token_env`에 새 환경변수 이름을 적고 `.env`에 값 추가
3. 디자인을 바꾸려면 `templates/default`를 복사해 `template:`에 지정

## 운영 원칙
- 원문을 그대로 옮기지 않고 재구성합니다(15자 이하 짧은 인용만). 회사명·실명 등은 AI 단계에서 지우고, 검수 때 한 번 더 확인하세요.
- 하루 1~3개, 좋아요·팔로우 자동화는 하지 않습니다(스팸 판정 방지).
