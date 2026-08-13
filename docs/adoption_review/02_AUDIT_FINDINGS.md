# 02. 전역 감사 결과

## 1. 우선순위

- **P0**: 공식 기준 또는 핵심 데이터 보증과 충돌한다. 채택 결정 전에 해결해야 한다.
- **P1**: 배포·지속 운영 전에 해결해야 한다.
- **P2**: 제한된 로컬 평가에는 허용할 수 있으나 계획과 검증이 필요하다.
- **P3**: 문서·유지보수 품질 문제다.

## 2. P0 — 채택 차단 항목

### F-001. 공식 Backend 권위와 후보 구현이 충돌한다

AI_RE 공식 문서는 `ai_companion_server/`와 배포 OpenAPI를 기준으로 지정하지만, 현재 대상은
별도 `ai_server` 스냅샷이다. endpoint와 기능 범위가 다르므로 이 구현만 보고 UE/Web 계약을
만들 수 없다.

- 근거: [`AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md`](../AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md)
- 판정: 전체 직접 채택 금지

### F-002. Chat/Situation의 request ID는 멱등 키가 아니다

Chat와 Situation은 request ID를 header/body 일치와 로그 상관관계에만 사용한다. 요청 저장이나
결과 재사용이 없어 재시도하면 LLM, transcript, memory side effect가 다시 발생한다.

- 근거: [`routes/chat.py`](../ai_server/app/routes/chat.py), [`routes/situations.py`](../ai_server/app/routes/situations.py), [`service.py`](../ai_server/app/service.py)
- 영향: timeout 후 재시도 중복 대사·비용·기억 오염
- 판정: 수정 후 채택

### F-003. Event와 Command Result 계약이 없다

`recent_event_ids`는 형식만 검사하고 사용하지 않는다. Event 저장 API, event idempotency,
Command Result 보고 API가 없다.

- 근거: [`app/models.py`](../ai_server/app/models.py), [`docs/temporary-scaffolds.md`](../ai_server/docs/temporary-scaffolds.md)
- 영향: 실행 결과와 기억 source를 검증할 수 없음
- 판정: 현재 기능 범위에서 제외하거나 계약 결정 후 구현

### F-004. 장기기억 범위에 companion ID가 없다

대화 작업기억은 profile/save/companion/session으로 분리하지만 장기기억 `player_key`는
profile/save만 포함한다. 둘 이상의 companion이 생기면 기억이 섞인다.

- 근거: [`service.py`](../ai_server/app/service.py) `_conversation_key`, `_player_key`
- 영향: 공식 profile/save/companion 범위 요구 불충족
- 판정: 다중 Companion 확장 시 수정 필요. 현재 단일 canonical Companion `mako`와
  `AIRE_OPEN / demo-slot-1`만 사용하는 AX 범위에서는 차단 항목이 아니다.

### F-005. 사용자 기억 조회·삭제·초기화 계약이 없다

기억 삭제는 Admin CRUD로만 가능하며 일반 사용자 범위의 조회·삭제 API, retrieval/cache/prompt
삭제 전파 검증이 없다. 원문 transcript 삭제도 사용자 기능으로 제공되지 않는다.

- 근거: [`routes/admin.py`](../ai_server/app/routes/admin.py), [`main.py`](../ai_server/app/main.py)
- 영향: 개인정보와 기억 통제 요구 불충족
- 판정: 공식 기억 기능으로 비채택

## 3. P1 — 운영 전 필수 수정

### F-006. `/health`가 readiness를 거짓으로 표현한다

DB, migration, credential 설정, LLM 연결 여부와 무관하게 `status=ok`를 반환한다. 시작 시 game
dataset DB 오류도 정적 dataset fallback으로 바꿔 부팅 실패로 드러내지 않는다.

- 근거: [`routes/system.py`](../ai_server/app/routes/system.py), [`main.py`](../ai_server/app/main.py)
- 조치: liveness/readiness 분리, DB migration과 필수 설정 검증, degraded capability 제공

### F-007. 앱 import 시 동기 DB I/O와 thread join을 수행한다

모듈 import의 `app = create_app()`가 DB snapshot을 동기 완료한다. 실행 중 event loop에서는 별도
thread를 만들고 `join()`한다. worker 시작, test isolation, 장애 처리 경계가 불필요하게 복잡하다.

- 근거: [`main.py`](../ai_server/app/main.py) `_run_to_completion`, `_load_startup_game_dataset`
- 조치: lifespan startup에서 async 초기화하고 명시적 failure/degraded 정책 적용

### F-008. 기억 증류 대기열에 새 턴 유실 경쟁이 있다

`_drain()`은 `_Pending` 객체 snapshot으로 await를 포함한 추출·요약을 수행한다. 그 사이
`_enqueue()`가 같은 객체를 갱신해도 이미 계산한 `ended`가 유지되고 마지막 `pop()`이 새 항목까지
제거할 수 있다. transcript에는 남아도 재시작 복구 cursor가 없어 자동 재처리되지 않는다.

- 근거: [`brain/companion.py`](../ai_server/app/brain/companion.py) `_enqueue`, `_drain`, `_drain_one`
- 조치: pending revision/CAS, conversation lock, 완료 시 동일 객체·cursor 재확인, 재시작 복구

### F-009. 단일 프로세스·단일 worker 가정이 계약에 박혀 있다

conversation store, lock, 증류 cursor, episodic memory read-merge-replace lock이 모두 프로세스
로컬이다. JSONL transcript도 process 간 file lock이 없다.

- 근거: [`brain/store.py`](../ai_server/app/brain/store.py), [`brain/transcript.py`](../ai_server/app/brain/transcript.py), [`episodic_memory_store.py`](../ai_server/app/episodic_memory_store.py)
- 조치: 당장은 single worker를 운영 계약으로 고정하거나 영속 queue/transaction으로 재설계

### F-010. Offline Task 수량 검증이 없다

공개 request의 `quantity`는 음수, 0, 과대값을 허용한다. 완료 계산은 이를 그대로 `min()`에
넣어 음수 결과를 저장할 수 있다. `Scouting` enum은 존재하지만 실질 duration/행동 모델이 없다.

- 근거: [`offline_task_models.py`](../ai_server/app/offline_task_models.py), [`offline_task_service.py`](../ai_server/app/offline_task_service.py)
- 조치: `1..MAX_GATHER_QUANTITY` 경계 검증, task type별 계약 분리, Scouting 비노출

### F-011. Pair와 bootstrap 등록에 rate limit이 없다

8자리 pairing code와 공유 bootstrap token endpoint에 attempt 제한, backoff, lockout이 없다.
bootstrap token을 아는 호출자는 새 request ID로 profile을 계속 만들 수 있다.

- 근거: [`pairing_models.py`](../ai_server/app/pairing_models.py), [`pairing_service.py`](../ai_server/app/pairing_service.py), [`docs/handoff.md`](../ai_server/docs/handoff.md)
- 조치: 사설망 한정, edge/server rate limit, bootstrap 폐쇄 또는 별도 provisioning

### F-012. 원문 대화가 평문 JSONL로 저장되며 사용자 삭제 경로가 없다

access log는 원문을 피하지만 transcript는 플레이어 발화와 상황 문장을 로컬 파일에 저장한다.
기본 보존 기간은 30일이며 장기기억은 SQLite에 남는다.

- 근거: [`brain/transcript.py`](../ai_server/app/brain/transcript.py), [`settings.py`](../ai_server/app/settings.py)
- 조치: opt-in 정책, 암호화/권한, 사용자 삭제, backup/retention 문서, 실제 sweep 검증

### F-013. 공개 운영 구성과 복구 절차가 없다

TLS/reverse proxy, service supervision, backup/restore, secret rotation, rate limiting, 배포 manifest가
없다. CORS도 의도적으로 없어 별도 origin 브라우저 HTTP client를 지원하지 않는다.

- 근거: [`docs/websocket-manual-test-spec.md`](../ai_server/docs/websocket-manual-test-spec.md), 저장소 파일 인벤토리
- 조치: 공개 운영 전 별도 운영 설계와 복구 리허설

### F-014. migration이 일부 데이터 실패를 성공으로 숨긴다

0005는 기존 JSON 기억 import의 모든 예외를 삼킨다. 0006 downgrade는 다중 profile 상태에서
unique violation이 가능한 일방향 migration이라고 스스로 명시한다.

- 근거: [`0005_episodic_memories.py`](../ai_server/migrations/versions/0005_episodic_memories.py), [`0006_game_registration_per_profile.py`](../ai_server/migrations/versions/0006_game_registration_per_profile.py)
- 조치: import 실패 보고와 검증 집계, rollback 불가 정책, backup 전제

## 4. P2 — 제한 사용 시 명시할 항목

### F-015. schema version과 시간 모드가 느슨하다

`schema_version`은 `1` 또는 생략이다. `surface`와 `TimeSource` 조합도 강제하지 않아 mobile에서
GameWorld, game에서 RealWorld를 보낼 수 있다.

- 근거: [`models.py`](../ai_server/app/models.py)
- 조치: required schema version, interaction mode와 time source 교차 검증

### F-016. LLM fallback이 광범위한 예외를 정상 결과로 바꾼다

provider의 분류·대사·기억 호출이 대부분 `except Exception` 뒤 Mock fallback으로 전환된다.
사용자 경험은 유지되지만 실제 공급자 장애율과 기억 생성 실패를 구분하기 어렵다.

- 근거: [`brain/llm.py`](../ai_server/app/brain/llm.py), [`brain/dialogue.py`](../ai_server/app/brain/dialogue.py)
- 조치: fallback은 유지하되 구조화된 failure metric과 provider health를 기록

### F-017. 외부 자유 문장을 신뢰된 상황으로 프롬프트에 넣는다

Situation은 최대 네 줄의 자유 문장을 그대로 LLM prompt에 넣는다. Command는 반환하지 않지만
prompt injection, 비밀값 우회, 현실 개인정보 전달을 막는 구조화 allowlist가 없다.

- 근거: [`models.py`](../ai_server/app/models.py), [`brain/llm.py`](../ai_server/app/brain/llm.py)
- 조치: stable event ID + 서버 검증 facts로 교체하거나 명시적 신뢰·개인정보 정책 적용

### F-018. 내부 AI metadata를 client response에 노출한다

Chat/Situation 응답은 provider, model version, prompt version을 항상 포함하고 `/health`도 provider를
노출한다. 디버깅에는 유용하지만 UE/Web 공개 계약과 운영 정보 최소화 원칙에는 맞지 않는다.

- 근거: [`models.py`](../ai_server/app/models.py), [`routes/system.py`](../ai_server/app/routes/system.py)
- 조치: 내부 관측으로 이동하거나 development capability에서만 노출

### F-019. 고정 Admin token 하나가 모든 관리 권한을 가진다

fail-closed 동작은 좋지만 role, audit trail, scope, rotation 모델이 없다. episodic memory와 device를
포함한 전체 CRUD가 같은 secret에 묶인다.

- 근거: [`dependencies.py`](../ai_server/app/dependencies.py), [`routes/admin.py`](../ai_server/app/routes/admin.py)
- 조치: 개발 전용으로 비활성화하거나 별도 관리 plane, audit, 최소 권한 적용

### F-020. 설정 오타를 조용히 무시한다

`Settings`가 `extra="ignore"`를 사용하므로 잘못 쓴 환경변수 이름이 기본값으로 대체될 수 있다.
또 코드 기본은 Mock인데 `.env.example`은 Local LLM과 평문 HTTP endpoint를 기본으로 둔다.

- 근거: [`settings.py`](../ai_server/app/settings.py), [`.env.example`](../ai_server/.env.example)
- 조치: 프로젝트 전용 prefix/명시 검증, example을 Mock/fail-safe로 통일

### F-021. smoke script가 자격 증명 일부와 pairing code를 출력한다

수동 온보딩 편의용이지만 공유 terminal/CI log에 민감값이 남을 수 있다.

- 근거: [`scripts/onboard_smoke.sh`](../ai_server/scripts/onboard_smoke.sh)
- 조치: token 출력 제거, code도 opt-in debug에서만 표시

## 5. 잘 구현된 부분

다음 항목은 유지 또는 이식할 가치가 있다.

- Pydantic `extra="forbid"`, Stable ID, 중복 allowlist와 민감 key 검증
- Bearer token 원문 미저장, HMAC hash와 constant-time 비교
- pairing code 만료·1회 사용과 조건부 update
- Offline Task 생성 unique key와 상태 전이 조건부 update
- HTTP request ID, body 상한, timeout, 표준 ErrorEnvelope
- 인증 신원에서 profile/device를 얻고 body 자기주장을 대조하는 구조
- HTTP DTO를 두뇌에 직접 전달하지 않는 `CompanionTurn` 번역 경계
- LLM이 게임 함수를 직접 호출하지 않고 TTL/allowlist가 있는 Command 후보만 반환하는 구조
- Mock provider와 실제 provider 실패 시 대화 fallback
- locked dependency와 CI의 Ruff/MyPy/pytest quality gate
