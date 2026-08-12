# 06. 공식 사용 전 Gate

## 2026-08-12 Fast Track

아래 tactical Gate는 장기 공식 채택 Gate를 대체하지 않는다. Backend를 고치지 않고 현재 코드의
좁은 HTTP Chat subset만 사용하는 마감용 예외다.

- [x] Backend code freeze
- [x] 기존 UE HTTP Chat component 재사용 결정
- [ ] UE request에서 미지원 `interaction_mode` 제거, `surface="game"` 추가
- [ ] UE response/error parser에서 서버에 없는 `schema_version`, `interaction_mode` 필수 검사 제거
- [ ] `MAKO`를 protocol ID `mako`로 client-side mapping
- [ ] `time_context.hour`를 0~23 정수로 직렬화
- [ ] 기본 transport를 HTTP로 고정
- [ ] `allowed_commands=[]`, `recent_event_ids=[]` 고정
- [ ] 자동 retry UI 비활성 또는 호출 금지
- [ ] 정상 대사 1회, timeout, malformed/error, owner destruction 확인
- [ ] Backend 미접속 상태에서도 기존 UE 수직 슬라이스 통과

Fast Track에 포함되지 않은 장기 Gate는 8월 12일 완료 조건으로 사용하지 않는다.

## Gate 0. 소유권과 기준 결정

- [ ] `ai_server`의 소유자와 Git repository/provenance를 확정한다.
- [ ] 기존 `ai_companion_server`를 유지할지 `ai_server`로 교체할지 팀이 명시적으로 결정한다.
- [ ] 배포 OpenAPI, target source, client contract의 우선순위를 하나로 고정한다.
- [ ] Backend/DB/LLM/배포 책임자를 다시 확정한다.

Gate 0이 끝나기 전에는 Fast Track subset 밖의 UE/Web endpoint DTO를 구현하지 않는다.

## Gate 1. 계약 최소선

- [ ] required `schema_version`과 호환 정책
- [ ] Chat request/response idempotency
- [ ] Event 저장과 `event_id` idempotency
- [ ] Command Result 저장과 command lifecycle
- [ ] `recent_event_ids`의 profile/save/companion scope 검증
- [ ] InGame/GameWorld와 Offline/RealWorld 교차 검증
- [ ] stable error code 표
- [ ] 정상·invalid·duplicate·expired fixture
- [ ] OpenAPI를 코드/CI에서 생성하고 breaking diff를 차단

## Gate 2. 인증과 개인정보

- [ ] Pair/register rate limit과 attempt audit
- [ ] bootstrap provisioning과 폐쇄 절차
- [ ] Admin 별도 관리 plane 또는 비활성화
- [ ] secret rotation 절차
- [ ] 다중 Companion을 제품 범위에 넣을 경우 profile/save/companion memory isolation
- [ ] memory 조회·삭제·reset
- [ ] 삭제가 retrieval, prompt, cache, transcript, backup에 반영되는지 검증
- [ ] transcript opt-in, 보존, 암호화/권한, 삭제 정책
- [ ] 실제 대화 원문과 token이 로그에 없음을 검증

## Gate 3. 수명주기와 동시성

- [ ] import-time DB I/O 제거
- [ ] async startup/shutdown과 부분 실패 정책
- [ ] Chat 취소/timeout 후 late side effect 차단
- [ ] memory distillation queue 경쟁 수정
- [ ] 재시작 후 transcript cursor 복구 또는 명시적 폐기 정책
- [ ] single worker 운영 제약 또는 multi-worker safe storage
- [ ] WebSocket head-of-line 정책과 frame auth 비용 측정
- [ ] Offline Task 모든 mutation idempotency
- [ ] quantity와 task type validation

## Gate 4. 운영

- [ ] liveness, readiness, capabilities 분리
- [ ] DB migration head 검증과 실패 시 readiness 차단
- [ ] LLM/embedding provider failure metric과 degraded 상태
- [ ] TLS/reverse proxy와 외부 노출 경로
- [ ] CORS/origin 정책
- [ ] process supervision과 graceful shutdown timeout
- [ ] DB/transcript/log backup과 restore rehearsal
- [ ] migration upgrade, partial failure, downgrade/roll-forward 정책
- [ ] 데이터·로그 용량 상한과 alert

## Gate 5. 검증 행렬

| 영역 | 정상 | Invalid | Timeout/Cancel | Duplicate | Unavailable | Restart/Owner destruction |
|---|---:|---:|---:|---:|---:|---:|
| Chat | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| Situation/Event | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| Command Result | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| Pairing | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| Offline Task | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| Memory | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| Migration | [ ] | [ ] | N/A | [ ] | [ ] | [ ] |
| Backup/Restore | [ ] | [ ] | N/A | N/A | [ ] | [ ] |

## Gate 6. Client 통합

- [ ] 배포 OpenAPI와 local target source가 같은 endpoint와 schema를 노출한다.
- [ ] UE는 async request timeout, cancellation, owner destruction을 처리한다.
- [ ] UE Command Gateway가 type, target, state, expiry, duplicate를 재검증한다.
- [ ] Backend가 없어도 UE local follow/combat/return behavior가 유지된다.
- [ ] WebClient가 UE gameplay state를 직접 변경하지 않는다.
- [ ] Web/UE가 같은 fixture와 error code를 사용한다.
- [ ] 실제 LLM 없이 Mock integration을 통과한다.

## 현재 Gate 상태

| Gate | 상태 | 이유 |
|---|---|---|
| Gate 0 | Blocked | 공식 source/배포 계약과 후보 스냅샷 관계 미결정, `ai_server/.git` 없음 |
| Gate 1 | Not ready | Chat 멱등, Event, Command Result, source 검증 없음 |
| Gate 2 | Not ready | rate limit, companion memory scope, 사용자 삭제 없음 |
| Gate 3 | Not ready | import-time I/O, single-process 상태, 증류 경쟁 |
| Gate 4 | Not ready | readiness, TLS/배포/복구 절차 없음 |
| Gate 5 | Partial | 광범위한 unit/integration test는 있으나 이번 감사에서 실행하지 않음 |
| Gate 6 | Blocked | 최신 배포 OpenAPI 대조 미완료 |

8월 12일 Fast Track은 `Backend Frozen / UE Adapter Pending` 상태다.

## 다음 작업 우선순위

1. 기존 전투·Work·Inventory 수직 슬라이스를 Backend 없이 먼저 고정
2. UE HTTP Chat adapter의 계약 불일치만 수정
3. 사용자 Build와 PIE에서 대사 1회 및 실패 fallback 확인
4. 8월 11일 이후 Backend·Chat 신규 기능 동결
5. 8월 12일 뒤 Backend 기준 저장소와 장기 계약 재결정
6. 이후 Event/Command Result/Memory와 운영 결함 처리
