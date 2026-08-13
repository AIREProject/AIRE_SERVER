# 07. 2026-08-12 Backend 동결 사용 계획

> 이 문서는 최초 UE-only 마감 컷을 보존한다. 완료된 고정 공개 인증과 현재 UE/Web 병렬 착수,
> canonical identity 결정은 [`09_AX_IMPLEMENTATION_PLAN.md`](09_AX_IMPLEMENTATION_PLAN.md)를
> 우선한다.

## 1. 결정

2026-08-12 1차 마감까지 `ai_server`는 수정하지 않는다. 이미 구현된 UE Chat client를 현재
`ai_server`의 HTTP 계약에 맞추는 최소 client 변경만 허용한다.

기존 8월 12일 수직 슬라이스의 필수 완료 조건은 Combat·Work·Inventory이며 Backend·LLM은
명시적인 제외 범위다. 따라서 Chat 통합은 optional demo enhancement이고 기존 로컬 Gate를
절대 막지 않는다.

## 2. 마감용 지원 범위

### 사용

- `POST /api/v1/chat`
- HTTP transport
- 같은 단일 플레이어의 GameClient와 WebClient
- 고정 Bearer `AIRE_GAME`, `AIRE_WEB`
- canonical identity `AIRE_OPEN / demo-slot-1 / mako`
- 기존 `UAIRECompanionChatComponent`
- 인게임 text request와 `display_text` 응답
- 서버 ErrorEnvelope 표시
- timeout/cancel/EndPlay cleanup

### 사용하지 않음

- WebSocket
- `/api/v1/situations`
- `/api/v1/tasks`
- `/api/v1/admin/*`
- pairing UI와 랜덤 device token 발급
- 장기기억 검증과 사용자 삭제
- Event/Command Result
- Command candidate 실행
- 자동 retry
- Backend code, migration, schema 수정

## 3. 현재 UE 구현에서 어긋난 부분

| 위치 | 현재 UE | 현재 ai_server | 최소 방향 |
|---|---|---|---|
| Chat request | `interaction_mode="InGame"` | 해당 field 없음, extra 거부 | field 제거 |
| Chat request | `surface` 없음 | `surface="game"` | field 추가 |
| Chat request | `hour` float | 0~23 integer | client에서 정수 변환 |
| Companion ID | `MAKO` | registry key `mako` | Chat protocol mapping만 소문자 |
| Chat response | `schema_version` 필수 파싱 | response에 없음 | 필수 검사 제거 |
| Chat response | `interaction_mode` 필수 파싱 | response에 없음 | 필수 검사 제거 |
| Error response | `schema_version` 필수 파싱 | ErrorEnvelope에 없음 | 필수 검사 제거 |
| Transport | WebSocket 기본 | WS frame별 token 계약 불일치 | HTTP 기본 |
| Register request | `schema_version` 포함 | request는 `request_id`만 허용 | 자동 등록을 쓸 경우 field 제거 |
| Retry | 같은 request 재전송 | Chat dedupe 없음 | 1차 UI에서 retry 호출 금지 |

근거:

- UE: [`AIREChatJsonAdapter.cpp`](../AI_RE/UEProject/Source/AI_RE/LMK/MAKO/Private/Chat/Transport/AIREChatJsonAdapter.cpp)
- UE: [`AIREChatSettings.h`](../AI_RE/UEProject/Source/AI_RE/LMK/MAKO/Public/Chat/Contracts/AIREChatSettings.h)
- UE: [`AIRECompanionChatComponent.cpp`](../AI_RE/UEProject/Source/AI_RE/LMK/MAKO/Components/Private/Chat/AIRECompanionChatComponent.cpp)
- Server: [`app/models.py`](../ai_server/app/models.py)
- Server: [`app/routes/chat.py`](../ai_server/app/routes/chat.py)

## 4. 고정 request profile

UE와 Web은 서로 다른 사용자가 아니다. 둘 다 `AIRE_OPEN / demo-slot-1 / mako`를 사용하며
장기기억과 Offline Task를 의도적으로 공유한다. Web 요청은 같은 body에서 `surface="mobile"`을
사용한다.

```json
{
  "schema_version": 1,
  "request_id": "request-uuid",
  "session_id": "session-uuid",
  "save_slot_id": "demo-slot-1",
  "companion_id": "mako",
  "message_id": "message-uuid",
  "user_message": "안녕",
  "surface": "game",
  "time_context": {
    "source": "GameWorld",
    "day": 1,
    "hour": 12,
    "period": "Afternoon"
  },
  "recent_event_ids": [],
  "game_context": {},
  "allowed_commands": []
}
```

`allowed_commands=[]`이므로 1차 client는 Command를 요청하지 않는다. 응답의
`command_candidates`, `offline_task_id`, `ai_metadata`는 알 수 있는 선택 field로 무시한다.

## 5. 인증 선택

- UE는 모든 제품 요청에 `Authorization: Bearer AIRE_GAME`을 사용한다.
- Web은 모든 제품 요청에 `Authorization: Bearer AIRE_WEB`을 사용한다.
- random device 등록, pairing, token 저장, refresh와 revoke 흐름은 제품 경로에서 사용하지 않는다.
- 누락되거나 다른 Bearer가 들어오면 기존 401 흐름으로 실패 처리한다.

## 6. Client 변경 예산

허용 파일은 원칙적으로 아래 세 개 이내로 제한한다.

1. `AIREChatJsonAdapter.cpp` — request/response/error JSON 계약
2. `AIREChatSettings.h` 또는 기존 config override — HTTP 기본값
3. `AIRECompanionChatComponent.cpp` — 자동 등록 body 또는 protocol companion ID mapping이 필요한 경우

새 subsystem, 새 transport, 새 manager, 새 dependency를 만들지 않는다. 기존 Chat UI와
credential store를 재사용한다.

## 7. 재시도와 실패 정책

- Chat request는 자동 재시도하지 않는다.
- timeout 후 같은 request를 다시 보내지 않는다.
- 사용자가 새로 전송하면 새 request/message ID를 만든다.
- HTTP/validation/credential 실패는 Chat UI에만 표시한다.
- Backend 실패가 StateTree, GAS, WorkOrder, Inventory를 변경하지 않는다.
- 늦은 callback은 기존 generation과 weak binding으로 무시한다.
- 8월 12일 데모 중 서버가 불안정하면 Fake Chat 또는 Chat 비활성 상태로 로컬 수직 슬라이스를 진행한다.

## 8. 일정

### 2026-08-10

- [x] Backend freeze와 최소 HTTP subset 결정
- [x] UE/server 계약 차이 식별
- [ ] client adapter 변경
- [ ] 정적 diff review

### 2026-08-11 오전

- [ ] 사용자 Unreal Build
- [ ] 정상 HTTP Chat 1회
- [ ] invalid token, timeout, malformed/error 표시
- [ ] EndPlay 중 request cancellation 확인
- [ ] Backend 미접속 중 로컬 Companion 기능 유지 확인

### 2026-08-11 오후 이후

- [ ] 신규 Backend/Chat 기능 동결
- [ ] Blocker만 수정
- [ ] 기존 Combat·Work·Inventory Gate 우선 검증

### 2026-08-12

- [ ] 로컬 수직 슬라이스 필수 Gate 통과
- [ ] Backend가 가능하면 인게임 대사 1회 시연
- [ ] Backend가 불가능해도 로컬 Gate 성공 유지
- [ ] 실제 Build/PIE 결과 기록

## 9. 1차 완료 조건

다음 네 가지면 충분하다.

1. Backend source는 변경되지 않았다.
2. UE가 HTTP Chat 한 번을 보내고 `display_text`를 표시한다.
3. 실패·timeout·EndPlay에서 crash나 local gameplay 중단이 없다.
4. Backend 없이 기존 8월 12일 Combat·Work·Inventory 수직 슬라이스가 통과한다.

Command 실행, 기억, 모바일, WebSocket, 운영 hardening은 1차 완료 조건이 아니다.
