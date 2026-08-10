#!/usr/bin/env bash
# 새 플레이어 기기 연결 흐름을 실제 HTTP 호출로 확인한다.
# register-game → pairing-codes → pair → chat 을 순서대로 밟는다.
#
# 사용법:
#   DEV_GAME_DEVICE_TOKEN=<부트스트랩 토큰> BASE=http://127.0.0.1:8000 bash scripts/onboard_smoke.sh
#
# .env 에서 토큰을 읽어 쓰려면:
#   DEV_GAME_DEVICE_TOKEN=$(grep '^DEV_GAME_DEVICE_TOKEN=' .env | cut -d= -f2) \
#     BASE=http://127.0.0.1:8000 bash scripts/onboard_smoke.sh
#
# 의존성: curl, jq
set -euo pipefail

BASE="${BASE:-http://127.0.0.1:8000}"
TOKEN="${DEV_GAME_DEVICE_TOKEN:?DEV_GAME_DEVICE_TOKEN 환경변수가 필요합니다}"
# 재실행해도 충돌하지 않도록 매번 다른 접미사를 쓴다.
SUFFIX="${SUFFIX:-$$}"

command -v jq >/dev/null || { echo "jq 가 필요합니다"; exit 1; }

echo "== 0) 헬스체크 =="
curl -sf "$BASE/health" >/dev/null && echo "  서버 OK: $BASE"

echo "== 1) 게임(PC) register-game =="
GAME=$(curl -sf -X POST "$BASE/api/v1/devices/register-game" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"request_id\":\"onboard-game-$SUFFIX\"}")
GAME_TOKEN=$(echo "$GAME" | jq -r .device_token)
GAME_PROFILE=$(echo "$GAME" | jq -r .profile_id)
echo "  profile : $GAME_PROFILE"
echo "  role    : $(echo "$GAME" | jq -r .device.role)"
echo "  token   : ${GAME_TOKEN:0:24}..."

echo "== 2) 게임이 페어링 코드 발급 =="
CODE=$(curl -sf -X POST "$BASE/api/v1/devices/pairing-codes" \
  -H "Authorization: Bearer $GAME_TOKEN" -H "Content-Type: application/json" \
  -d "{\"request_id\":\"onboard-code-$SUFFIX\"}" | jq -r .pairing_code)
echo "  pairing_code : $CODE (5분, 1회용)"

echo "== 3) 폰이 코드로 합류 (인증 없음) =="
WEB=$(curl -sf -X POST "$BASE/api/v1/devices/pair" \
  -H "Content-Type: application/json" \
  -d "{\"request_id\":\"onboard-pair-$SUFFIX\",\"pairing_code\":\"$CODE\"}")
WEB_TOKEN=$(echo "$WEB" | jq -r .device_token)
WEB_PROFILE=$(echo "$WEB" | jq -r .profile_id)
echo "  profile : $WEB_PROFILE"
echo "  role    : $(echo "$WEB" | jq -r .device.role)"
echo "  token   : ${WEB_TOKEN:0:24}..."

echo "== 4) 프로필 공유 확인 =="
if [ "$GAME_PROFILE" = "$WEB_PROFILE" ]; then
  echo "  OK: 게임과 폰이 같은 프로필 → 기억 공유"
else
  echo "  FAIL: 프로필이 다릅니다"; exit 1
fi

echo "== 5) 채팅 (게임 창구) =="
CHAT=$(curl -sf -X POST "$BASE/api/v1/chat" \
  -H "Authorization: Bearer $GAME_TOKEN" -H "Content-Type: application/json" \
  -d "{\"request_id\":\"onboard-chat-$SUFFIX\",\"session_id\":\"sess-$SUFFIX\",\"save_slot_id\":\"slot-1\",\"companion_id\":\"mako\",\"user_message\":\"안녕\",\"surface\":\"game\"}")
echo "  마코 응답: $(echo "$CHAT" | jq -r '.display_text // .')"

echo ""
echo "완료: register-game → pairing-codes → pair → chat 흐름 정상."
