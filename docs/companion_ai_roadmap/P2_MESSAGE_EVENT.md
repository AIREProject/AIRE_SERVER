# CAI-P2. Conversation·Message·GameEvent 영속화

- 상태: Planned
- 선행: CAI-P1 Local Deterministic Tests Passed
- 후속: CAI-P3, CAI-P5
- 기존 계획: M05-E02, M04 Event/Command Result

## 목표

파일 transcript와 process memory만으로는 출처·재시도·범위를 보장할 수 없으므로, 기억과 관계의 근거가 되는 canonical Message/Event를 서버 DB에 영속화합니다.

## 설계 경계

- canonical DB Message/Event가 새로운 기억의 권위 source입니다. `ConversationMemory`와 JSONL transcript는 디버그·재증류 보조 자료로만 유지하며 권위 source가 아닙니다.
- canonical DB 저장은 Chat 성공 전에 동기적으로 보장합니다. 저장에 실패하면 성공 응답으로 가장하지 않으며, 기존 transcript의 예외 무시 동작을 canonical audit에 재사용하지 않습니다.
- 실제 API 명세는 migration·테스트·배포 OpenAPI 확인 전까지 제안입니다.
- 인증된 `profile_id`와 device role이 권위이며, request body의 자기신고 ID를 신뢰하지 않습니다.

## 구현 순서

### CAI-P2-T01 canonical Conversation·Message

- [ ] Alembic head 뒤 migration으로 Conversation과 Message를 추가합니다.
- [ ] Conversation에는 stable ID, profile/save/companion, session, surface와 생성 시간을 둡니다.
- [ ] Message에는 stable message ID, conversation ID, speaker, source mode, occurred/received time, 제한된 text와 request idempotency 정보를 둡니다.
- [ ] RealWorld와 GameWorld 시간은 같은 필드에 섞지 않고 source mode와 시간 컨텍스트를 함께 보관합니다.
- [ ] 같은 idempotency key 재시도는 동일 결과를 반환하고 Message·LLM side effect를 중복하지 않습니다.
- [ ] 같은 key에 다른 본문이 오면 명시적으로 거부합니다.
- [ ] 기존 Game State의 scoped operation/body hash replay 패턴과 중앙 오류 매핑을 재사용합니다.

### CAI-P2-T02 GameEvent·Command Result

- [ ] versioned Event model과 payload allowlist를 정의합니다.
- [ ] `event_id`와 `operation_id`를 scope 안에서 멱등 처리합니다.
- [ ] payload 크기, 지원 schema version, device role, profile/save/companion 범위를 검증합니다.
- [ ] Command Result는 원래 Candidate/operation과 연결될 때만 저장합니다.
- [ ] low-level event 폭주를 제한하고 중요도는 서버 규칙으로 정합니다.
- [ ] route·Pydantic model·OpenAPI·fixture는 함께 변경합니다.

제안 endpoint:

```text
POST /api/v1/events
POST /api/v1/command-results
```

## 변경 예상 위치

| 목적 | 파일 |
|---|---|
| DB model/repository | `app/db/models.py`, 신규 repository, `migrations/versions/` |
| request/service | `app/models.py`, `app/service.py`, `app/routes/` |
| error mapping | `app/errors.py`, `app/errors_http.py` |
| brain source 연결 | `app/brain/contract.py`, `app/brain/companion.py`, `app/brain/transcript.py` |
| 문서/계약 | `docs/api-endpoints.md`, `AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md` |
| 테스트 | chat/service/transcript 신규 DB·idempotency·Event 계약 테스트 |

## 로컬 테스트 매트릭스

| 경우 | 기대 결과 |
|---|---|
| Message 저장 후 서버 재시작 | 같은 scope에서 source ID로 조회 가능 |
| 동일 request/event/operation 재시도 | side effect 한 번, 동일 결과 |
| 같은 idempotency key 다른 payload | conflict/validation error |
| 타 profile/save/companion source | 저장·조회·연결 거부 |
| RealWorld Message와 GameWorld Event | 시간 mode가 보존되고 혼합되지 않음 |
| oversize/unknown version/malformed payload | 저장 전 거부 |
| late Command Result | 현재 operation과 맞지 않으면 거부 |

## 완료 Gate

### Local Deterministic Tests Passed

- [ ] SQLite migration, restart, scope, duplicate, conflict, invalid fixture를 통과합니다.
- [ ] Message/Event은 기억 후보의 stable source로 전달됩니다.
- [ ] retry가 transcript·memory background 작업을 중복 실행하지 않습니다.
- [ ] background cursor의 restart/race repair와 late side effect를 별도 fixture로 검증합니다.

### Deployed Integration Pending

- [ ] 배포 OpenAPI에 Event/Command Result가 반영됨
- [ ] UE/Web이 실제 HTTPS endpoint와 idempotency를 검증함

P2는 실제 Gemma가 없어도 완료 가능한 데이터 기반 작업입니다. 다만 배포 API가 없으면 client가 endpoint를 호출하게 하지 않습니다.
