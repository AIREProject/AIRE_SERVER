# 2607241349 WebSocket 채팅 트랜스포트 병행 추가 개발 기록

- 기록일: 2026-07-24 13:49
- 기록 유형: 기능 개발 완료 기록
- 변경 범위: `POST /api/v1/chat`(HTTP)과 동일한 페이로드를 지속 연결로 주고받는 WebSocket
  엔드포인트 2종 신설, 인증 코어·에러 매핑 추출(동작 무변경), 클라이언트 연동 가이드 작성
- 구현 기준: 작업 트리 (미커밋)
- API/스키마 버전: 신규 WS 엔드포인트 `/api/v1/game/chat`·`/api/v1/mobile/chat`. 페이로드는
  기존 chat request/response v1 그대로 재사용(신규 스키마 없음). WS 봉투는 `type`+`payload`,
  `Contracts/` 정식 문서화는 Phase 2로 연기
- 후속 범위: Phase 2 스트리밍(`chat_delta`/`chat_commit`), 서버→클라이언트 푸시, WS 봉투
  스키마 `Contracts/` 문서화. 스트리밍 충돌 해소 방식은 미결정

## 1. 완료 상태 요약

게임 클라이언트가 HTTP 요청/응답으로만 대화하던 것을, **HTTP는 유지한 채** WebSocket
트랜스포트를 병행 추가했다(Phase 1). 목적은 응답 스트리밍과 연결 유지/지연 감소지만, 이번
단계는 트랜스포트만 얹고 스트리밍은 넣지 않았다.

핵심은 **`ChatService`와 마코 두뇌를 한 줄도 바꾸지 않은 것**이다. `ChatService.create_response`
가 이미 트랜스포트에 독립적(`ChatRequest`+`AuthenticatedDevice`→`ChatResponse`)이라, WS
핸들러는 봉투를 벗겨 기존 오케스트레이션을 그대로 호출한다. 인증·재검증·멱등성·영속화는
전부 재사용된다.

엔드포인트 2종은 **인증 수단만 다르고**(game=핸드셰이크 헤더, mobile=첫 메시지) 인증 완료 후
동일한 메시지 루프로 합류한다. 인가(역할↔모드)는 여전히 `ChatService`가 단독 판단한다.

신규 WS 테스트 21개가 통과하고 신규 코드는 ruff clean이며, 실제 마코 두뇌(`AI_MODE=companion`)로
한 연결에서 3턴 연속 대화와 명령 방출(`Follow`/`HoldPosition`/`CancelCurrent`)을 확인했다.

## 2. 스트리밍을 이번 범위에서 제외한 이유

스트리밍은 현재 안전 모델과 충돌한다. `app/infrastructure/ai/companion/dialogue.py`의
`sanitize()`는 **완성된 전체 텍스트**를 대상으로 "확정 사실에 없는 숫자가 나오면 통째로 거부"
하는 환각 가드를 수행하고, `render()`는 실패 시 고정 폴백 대사로 갈아끼운다. 토큰을 먼저
흘려보내면 이 검증·회수가 불가능해진다. 따라서 Phase 1은 트랜스포트만, 스트리밍은 충돌 해소
방식(검증 후 청크 vs 진짜 토큰 스트리밍+최종 commit)을 정한 뒤 Phase 2로 분리했다.

## 3. 확정된 결정

사용자와 방향을 확정한 뒤 진행했다.

1. **HTTP 병행 유지** — `POST /api/v1/chat`은 그대로 두고 WS를 추가한다(교체 아님). 게임
   클라이언트가 점진적으로 전환할 수 있다.
2. **엔드포인트 분리** — `/api/v1/game/chat`(네이티브, 헤더 인증)과 `/api/v1/mobile/chat`
   (브라우저, 첫 메시지 인증). 브라우저 JS `WebSocket` API가 커스텀 헤더를 못 붙이는 제약을
   엔드포인트로 정직하게 드러낸다.
3. **메시지당 새 DB 세션** — 연결당 공유가 아니라 메시지마다 세션을 열고 닫는다. HTTP 경로와
   동일한 격리를 얻고 상태 누수를 피한다.
4. **`Contracts/` 문서화는 Phase 2로 연기** — 스트리밍에서 `chat_delta`/`chat_commit`이
   추가될 것이 확실해 지금 박아두면 곧 고치게 된다.

## 4. 구현 범위와 주요 결정

### WS 엔드포인트와 공유 메시지 루프 (`app/api/routes/ws_chat.py`, 신규)

라우터 하나에 엔드포인트 둘, 공유 루프 하나로 구성했다.

- **game — 헤더 인증**: `accept()` 전에 `Authorization: Bearer` 헤더를 검증하고, 실패하면
  연결 자체를 수립하지 않는다(close 1008).
- **mobile — 첫 메시지 인증**: `accept()` 후 첫 프레임의 `{"type":"auth","token":...}`으로
  인증한다. 헤더 방식에 없는 방어를 추가했다 — auth가 아닌 선행 프레임 즉시 거절,
  `ws_auth_timeout_seconds`(기본 10초) 내 미인증 시 종료, **auth 프레임 본문은 어느 경로로도
  로깅하지 않음**(토큰이 본문에 실려 오므로).
- **공유 루프**: 메시지마다 크기 제한(수동)·`request_id` 컨텍스트·타임아웃·완료 로깅을 적용하고,
  새 세션으로 `ChatService`를 돌려 `chat_response` 봉투로 응답한다.

### WS는 미들웨어를 타지 않는다는 점 대응

`RequestContextMiddleware`가 `scope["type"] != "http"`로 WS를 통과시켜, request-id 발급·본문
크기 제한·타임아웃·완료 로깅이 WS에 걸리지 않는다. 이를 공유 루프에서 메시지 단위로 대체했다.
미들웨어 자체는 HTTP 경로 리스크를 피하려 수정하지 않았다.

### 봉투 프로토콜

- 클라이언트→서버: `{"type":"auth",...}`(mobile 전용), `{"type":"chat","payload": ChatRequest}`
- 서버→클라이언트: `{"type":"chat_response","payload": ChatResponse}`,
  `{"type":"error","payload": ErrorEnvelope}`
- 페이로드 스키마는 HTTP와 100% 동일. 미지의 `type`은 에러 응답 후 연결 유지(Phase 2 확장 대비).

### 인증 코어 추출 (`app/api/dependencies/auth.py`, 동작 무변경)

`get_authenticated_device` 본문의 토큰 검증을 트랜스포트 독립 함수
`authenticate_device_token(token, settings, session)`으로 추출했다. HTTP 의존성은 이를 호출하는
얇은 래퍼가 되고, WS 두 경로도 같은 함수를 쓴다. HTTP 동작은 완전 불변이다.

### 에러 매핑 추출 (`app/api/errors/handlers.py`, 동작 무변경)

`register_error_handlers` 내부에 있던 애플리케이션 오류→(status, code, message, retryable)
매핑을 모듈 상수 `APPLICATION_ERROR_MAP`으로 올렸다. HTTP 핸들러와 WS 루프가 공유한다(WS는
status_code를 버리고 code/message/retryable만 사용).

### 연결 유지 정책

애플리케이션 오류(AI 장애·중복·검증 실패·크기 초과·타임아웃)는 `type:"error"` 메시지로
내려주고 **소켓을 유지**한다. 인증 실패·프로토콜 위반만 close 1008로 끊는다.

## 5. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/api/routes/ws_chat.py` | WS 엔드포인트 2종 + 인증 2종 + 공유 메시지 루프(신규) |
| `app/api/dependencies/auth.py` | `authenticate_device_token` 추출, HTTP 의존성은 래퍼로(동작 무변경) |
| `app/api/errors/handlers.py` | `APPLICATION_ERROR_MAP` 모듈 상수 추출(동작 무변경) |
| `app/main.py` | WS 라우터 `include_router` 등록 |
| `app/settings.py` | `ws_auth_timeout_seconds`(기본 10초) 추가 |
| `tests/test_ws_chat_api.py` | WS 경로 21개 테스트(신규) |
| `docs/websocket-client-guide.md` | 클라이언트 연동 가이드(신규) |
| `docs/README.md` | 현행 문서 섹션에 가이드 링크 추가 |

> `auth.py`·`handlers.py`는 AI_RE 상류(`421865a`) import 파일이다. 순수 추출(동작 무변경)
> 수준으로만 손댔으며, 이후 AI_RE 인프라 재동기화 시 이 두 파일의 diff에 주의한다.

## 6. 검증 결과

```text
uv run pytest tests/test_ws_chat_api.py
21 passed

uv run ruff check app/api/routes/ws_chat.py tests/test_ws_chat_api.py
All checks passed!

전체 스위트: 신규 WS 테스트 21개 통과. ruff 총계는 변경 전과 동일(34)로,
신규 파일은 clean이고 잔여 34건은 AI_RE 상류 스타일(auth.py UP017, handlers.py I001 등)이다.

end-to-end (companion 모드, 인증된 GameClient, 단일 WS 연결에서 3턴 연속):
"따라와"     → chat_response, "알겠어. 따라갈게.",         [Command.Follow]
"여기서 기다려" → chat_response, "알겠어. 여기서 기다릴게.",   [Command.HoldPosition]
"그만"       → chat_response, "알겠어. 지금 하던 일을 멈출게.", [Command.CancelCurrent]
```

테스트로 고정한 주요 동작:
- WS `chat_response` 페이로드가 HTTP `POST /api/v1/chat` 응답과 일치(생성 필드 제외).
- game: 유효 헤더 수락, 헤더 누락·무효 토큰·스킴 누락·폐기 디바이스는 close 1008.
- mobile: 첫 메시지 인증 성공, auth 선행 위반·무효 토큰은 close 1008, 토큰 로그 미출력.
- 공유 루프: 단일 연결 다중 메시지, 멱등 재전송, 중복/잘못된 봉투/비-JSON/크기초과/AI 장애는
  에러 응답 후 연결 유지, 역할↔모드 불일치는 `ChatService`가 `UnauthorizedDevice`로 판단.

## 7. 후속 범위 및 주의점

- **Phase 2 스트리밍**: `chat_delta`/`chat_commit` 타입을 이 연결 위에 얹는다. `sanitize`
  충돌 해소 방식(검증 후 청크 vs 진짜 토큰 스트리밍+최종 commit)을 먼저 결정해야 하며,
  결정 후 `Contracts/`에 WS 봉투 스키마를 일괄 문서화한다.
- **서버 푸시**: 현재는 요청-응답만 지원한다. 이벤트 알림 등 서버 시작 메시지는 미구현.
- **메시지 직렬 처리**: 단일 연결에서 메시지는 순차 처리된다(파이프라이닝 이득 없음). 단일 워커
  SQLite MVP 전제와 일치한다.
- **미커밋 상태**: 이번 기록 시점에 변경은 작업 트리에만 있고 커밋되지 않았다.
```
