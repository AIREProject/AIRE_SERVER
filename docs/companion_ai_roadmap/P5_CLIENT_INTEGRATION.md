# CAI-P5. UE/Web 계약과 사용자 제어

- 상태: Planned
- 선행: CAI-P2·P3 Local Deterministic Tests Passed
- 후속: CAI-P6
- 기존 계획: M02, M04, M06

## 목표

기존 Chat 경험을 깨지 않으면서 Web에는 기억 제어를, UE에는 Event·Command Result Outbox를 추가합니다. 모바일은 게임 상태를 직접 변경하지 않고, UE는 계속 최종 실행 권위를 가집니다.

## 계약 원칙

| 영역 | 방침 |
|---|---|
| Chat request | 현재 형식을 가능한 한 유지 |
| Chat response | `used_memory_refs` 등 optional additive field만 고려 |
| Event/Command Result | 새 versioned endpoint, `event_id`/`operation_id` 멱등 |
| Memory | 별도 사용자 scope CRUD |
| Provider | Backend 설정 전용, UE/Web에 API key·원본 응답 미노출 |

새 endpoint는 `AIRE_SERVER` source, migration, tests, 배포 OpenAPI가 동시에 준비되기 전까지 호출 불가입니다.

## 구현 순서

### CAI-P5-T01 계약 freeze와 fixture

- [ ] Event, Command Result, Memory DTO와 error/idempotency 규칙을 source contract에 추가합니다.
- [ ] `schema_version`, `event_id`, `operation_id`, stable entity ID, source/time mode를 fixture로 고정합니다.
- [ ] 배포 `/openapi.json`과 `AIRE_SERVER/docs/api-endpoints.md`, `AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md`를 대조합니다.
- [ ] optional memory references를 gameplay 권위로 해석하지 않는 client parser fixture를 추가합니다.

### CAI-P5-T02 Web Memory와 전송 대기

- [ ] Memory 목록·출처·정정·삭제·reset UI를 추가합니다.
- [ ] 사용자가 자신의 scope만 보도록 API 응답과 UI를 제한합니다.
- [ ] 클라우드 LLM 사용, 기억 저장·삭제 범위를 설명합니다.
- [ ] 네트워크 단절 시 Chat/Event를 bounded pending queue에 넣고 재연결 후 멱등 전송합니다.
- [ ] 모바일 Chat response의 Command Candidate로 UE gameplay를 직접 변경하지 않습니다.

### CAI-P5-T03 UE Event/Result Outbox

- [ ] UE가 session/event/command-result를 durable Outbox에 저장하고 재생합니다.
- [ ] stable entity ID, request/event/operation correlation, expiry와 dedupe를 유지합니다.
- [ ] 잘못된 scope, 늦은 callback, 만료 Candidate, 중복 결과를 Backend와 Gateway 모두에서 거부합니다.
- [ ] Backend/LLM 장애 중 기존 StateTree/GAS/Command Gateway 로컬 행동을 유지합니다.
- [ ] memory reference는 표시·감사용이며 행동 실행 근거가 아닙니다.

## 변경 예상 위치

| 목적 | 위치 |
|---|---|
| Backend contract | `AIRE_SERVER/app/models.py`, `app/routes/`, `docs/api-endpoints.md` |
| Web | `AI_RE/WebApp/src/api/`, `src/main.ts`, Memory UI 및 pending queue 테스트 |
| UE | `AI_RE/UEProject/.../Chat`, `.../Command`, Outbox/HTTP adapter |
| 공유 문서 | `AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md`, `.agents/docs/10_shared_protocols.md` |

## 검증

### Local Deterministic Tests Passed

- [ ] Web parser가 optional 필드를 안전하게 무시/표시합니다.
- [ ] Event/Result duplicate, expiry, invalid scope와 malformed DTO fixture를 통과합니다.
- [ ] Web pending queue는 bounded이며 동일 operation을 중복 전송하지 않습니다.
- [ ] Backend unavailable 시 UE 로컬 AI가 유지됩니다.

### User-run Unreal Verification

- [ ] Development Editor build
- [ ] 관련 Automation
- [ ] PIE에서 정상·timeout·cancel·duplicate·late callback·재시작 확인

Unreal build와 PIE는 사용자가 수행합니다. 실제 배포 OpenAPI가 준비되기 전에는 UE/Web endpoint 호출을 완료로 처리하지 않습니다.
