# 03. 취사선택표

## 1. 분류 원칙

- **채택**: 구조를 유지할 가치가 있고 현재 위험이 제한적이다. 실제 이식 전 공식 계약 대조는 필요하다.
- **수정 후 채택**: 방향은 맞지만 현재 형태로 운영 또는 계약 기준이 될 수 없다.
- **참고만**: 도메인 아이디어나 테스트 사례만 가져간다.
- **비채택**: 현재 공식 기준과 충돌하거나 결함 비용이 재사용 가치보다 크다.

이 표의 “채택”은 `ai_server` 전체를 채택한다는 뜻이 아니다. 공식 Backend가 결정된 뒤 해당
패턴을 그 계약 안에 이식할 수 있다는 뜻이다.

## 2. 채택 후보

| 대상 | 분류 | 유지할 핵심 | 적용 조건 |
|---|---|---|---|
| `StrictModel`, `StableId`, bounded fields | 채택 | 외부 입력 기본 거부와 길이/형식 상한 | 공식 DTO 필드로 재작성 |
| 인증 신원과 body claim 대조 | 채택 | token-derived identity가 권위 | 공식 인증 방식과 role 확정 |
| HMAC credential protector | 채택 | 원문 token/code 미저장, fail-closed pepper | pepper rotation과 복구 정책 추가 |
| pairing code atomic consume | 채택 | `WHERE used_at IS NULL` 조건부 update | rate limit과 attempt audit 추가 |
| Offline Task 조건부 상태 전이 | 채택 | expected status 기반 atomic update | task 계약과 멱등 정책 재설계 |
| RequestContext middleware | 채택 | request ID, body 상한, timeout | streaming/WS 정책 별도 유지 |
| ErrorEnvelope | 채택 | stable code, retryable, safe message | 공식 오류 코드와 동기화 |
| `ChatRequest -> CompanionTurn` 번역 | 채택 | transport DTO와 AI routing 분리 | composition root 분리 |
| Command TTL + allowlist | 채택 | LLM 출력이 아닌 검증된 후보만 전달 | UE Command Gateway 재검증 필수 |
| Mock provider 경계 | 채택 | 외부 LLM 없이 기능 평가 | fallback observability 추가 |
| CI quality workflow | 채택 | lock, Ruff, MyPy strict, pytest | 실제 repository로 이동 후 실행 확인 |

## 3. 수정 후 채택

| 대상 | 필요한 수정 |
|---|---|
| 앱 startup | import-time DB I/O 제거, async lifespan startup, migration/readiness 검증 |
| Chat/Situation | request ID idempotency 저장과 동일 응답 재사용 |
| schema/time | required schema version, interaction mode와 GameWorld/RealWorld 교차 검증 |
| Event/Command Result | stable ID, scope, idempotency, 저장, 허용 source 검증 추가 |
| memory | 현재 단일 `AIRE_OPEN / demo-slot-1 / mako`에서는 profile/save scope를 그대로 사용. 다중 Companion으로 확장할 때만 companion scope를 추가하고, 사용자 조회·삭제·reset은 별도 기능으로 결정 |
| transcript | opt-in, 암호화/권한, 삭제, crash recovery, 다중 process 정책 추가 |
| 증류 queue | revision/CAS 또는 영속 queue로 경쟁·재시작 유실 제거 |
| health | liveness/readiness/capabilities 분리, 실제 의존성 상태 반영 |
| LLM fallback | fallback 유지, 실패 원인 metric·로그·degraded 상태 추가 |
| Offline Task | quantity 경계, task type별 모델, 모든 mutation 멱등성 추가 |
| device pairing | rate limit, bootstrap provisioning, brute-force audit 추가 |
| Admin | 개발 전용 차단 또는 별도 관리 plane, role, audit, rotation 추가 |
| settings | safe Mock example, 평문 remote URL 제거, 오타 검출, secret validation 추가 |
| deployment | single-worker 여부, TLS/proxy, service supervision, backup/restore 정의 |

## 4. 참고만 할 부분

| 대상 | 참고 가치 | 그대로 쓰지 않는 이유 |
|---|---|---|
| Mako intent/command regex | 한국어 테스트 corpus와 fallback 사례 | 게임 Command allowlist와 제품 언어가 확정되지 않음 |
| recipe/resource/enemy/lore dataset | 대사 grounding 예시 | 실제 UE authoritative data와 동기화되지 않음 |
| Situation 자유문장 | 선제 대사 UX 실험 | 검증된 Event 계약을 우회함 |
| Offline Task 시간 계산 | 오프라인 진행 UX 실험 | 자원 권위, inventory 정산, clock policy가 없음 |
| JSONL transcript | append-only 복구 아이디어 | 개인정보·다중 process·삭제 보증 부족 |
| embedding keyword hybrid recall | 검색 품질 실험 | 공식 기억 source/scope/delete 계약 미충족 |
| Admin generic CRUD | QA 데이터 편집 편의 | 공개 운영 권한 모델로 부적합 |

## 5. 비채택 또는 비활성화 대상

| 대상 | 결론 |
|---|---|
| `docs/current/*`의 `/v1/companion/*` 계약 | 레거시 참고 외 사용 금지 |
| `main.py`의 thread + `join()` startup bridge | 제거 |
| Chat/Situation request ID를 상관 ID로만 쓰는 방식 | 공식 side-effect API에는 사용 금지 |
| companion 구분 없는 장기기억 key | 제거 |
| 사용자 삭제 없는 transcript/episodic memory 운영 | 비활성화 |
| 평문 remote LLM URL을 example 기본값으로 제공 | 제거 |
| rate limit 없는 공개 pair/bootstrap endpoint | 외부 노출 금지 |
| 단일 Admin Bearer로 전체 데이터를 조작하는 공개 관리 API | 공개 surface에서 제거 |
| `Scouting`처럼 enum만 있고 행동 모델이 없는 Offline Task | 계약에서 숨김 |

## 6. 추천 채택 시나리오

### 시나리오 A — 기존 공식 Backend 유지

가장 안전한 선택이다.

1. 배포 OpenAPI와 공식 Backend source 계약을 먼저 동기화한다.
2. `ai_server`에서는 입력 검증, credential hash, 조건부 update, middleware, Mock 테스트 패턴만 뽑는다.
3. `ai_server` endpoint나 DTO를 UE/Web에 직접 구현하지 않는다.
4. Mako brain은 공식 `AIService` 입력/출력 경계 뒤의 후보 implementation으로만 평가한다.

### 시나리오 B — `ai_server`를 새 공식 Backend 후보로 승격

팀이 Backend 소유권과 배포를 다시 열 때만 가능하다.

1. 공식 채택 결정을 기록한다.
2. Git repository와 provenance를 복구한다.
3. P0 Gate를 해결하고 OpenAPI를 코드에서 생성·고정한다.
4. Event, Command Result, memory deletion, scope, idempotency를 완성한다.
5. P1 운영 Gate와 실제 migration/restart/backup 복구 테스트를 통과한다.
6. 배포 OpenAPI를 새 계약으로 배포한 뒤 UE/Web DTO를 만든다.

### 시나리오 C — 2026-08-12 tactical freeze

현재 마감에는 이 시나리오를 적용한다.

1. `ai_server` 코드는 수정하지 않는다.
2. UE는 기존 `UAIRECompanionChatComponent`와 HTTP transport를 재사용한다.
3. JSON adapter와 기본 transport만 현재 `ai_server` 계약에 맞춘다.
4. Chat은 `allowed_commands=[]`로 보내 대사만 표시한다.
5. 자동 재시도, WebSocket, Situation, Offline Task, Memory, Admin을 사용하지 않는다.
6. Backend 실패는 기존 8월 12일 로컬 수직 슬라이스 Gate를 실패시키지 않는다.

마감까지는 시나리오 C를 권고한다. 마감 뒤 장기 기준은 시나리오 A와 B 중 다시 결정한다.
