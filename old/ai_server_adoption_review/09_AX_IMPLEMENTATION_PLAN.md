# 09. AX 구현 계획

작성일: 2026-08-10  
기준: HTTP transport, 현재 `ai_server` 최대 재사용, Backend 변경 최소화, UE가 live gameplay와
Inventory의 최종 권위

## 0. 현재 제품 불변식

2026-08-12 데모와 이후 AX 구현은 계정이 여러 개인 서비스를 전제로 하지 않는다.

- 플레이어는 한 명이며 canonical profile은 `AIRE_OPEN`이다.
- Save Slot은 하나이며 canonical ID는 `demo-slot-1`이다.
- Companion은 하나이며 canonical ID는 `mako`다.
- `AIRE_GAME`과 `AIRE_WEB`은 서로 다른 사용자가 아니라 같은 플레이어가 사용하는 UE와 Mobile
  Web surface다.
- 두 surface가 같은 대화 장기기억, Offline Task와 마지막 승인 상태를 보는 것은 의도된 동작이다.
- 계정 선택, 사용자 분리, 다중 Save Slot과 다중 Companion 지원은 현재 제품 범위에 넣지 않는다.

기존 profile/save/companion 검증과 저장 필드는 방어적 계약 및 향후 호환성을 위해 유지한다.
이를 다중 사용자 기능으로 확장하지는 않는다.

### LLM Provider 독립성 결정

2026-08-11 결정으로 현재 LLM의 응답 품질이 의도와 다를 수 있음을 인정하되, 현재 AX 범위에서
Provider, Runtime과 Prompt 동작을 교체하지 않는다. 이후 품질·지연·비용 평가 결과에 따라 다른
상용 API를 채택할 수 있도록 다음 경계를 유지한다.

- UE와 Web은 Backend의 공개 Chat 계약만 사용하고 특정 Provider SDK, API key, 원본 응답 형식과
  오류 형식을 알지 않는다.
- Backend 도메인 입력·출력은 Provider 중립적인 canonical model로 유지하고, 현재
  `LLMProvider` 구현은 그 경계 뒤의 기본 Adapter로 취급한다.
- Provider별 요청 조립, 응답 해석과 오류 변환은 Adapter 내부에 격리한다. Provider 원본 출력은
  외부 입력으로 취급해 canonical model로 변환한 뒤 Schema와 도메인 규칙을 다시 검증한다.
- `ai_metadata.provider`, `model_version`, `prompt_version`은 관찰과 품질 비교에만 사용한다.
  UE/Web이나 Gameplay 로직은 이 값으로 동작을 분기하지 않는다.
- Provider 선택, 모델명, timeout과 secret은 Backend 설정에서만 관리한다. API key를 UE/Web의
  설정, 저장소, 로그와 응답에 포함하지 않는다.
- Provider timeout, unavailable, rate limit과 invalid output은 안정적인 공통 실패로 정규화하며,
  Backend 장애 시 Local AI 유지와 현재 Mock fallback 정책을 보존한다.
- LLM 생성과 Embedding은 독립된 교체 경계로 유지한다. 한쪽 Provider 변경이 다른 쪽이나
  UE/Web 계약 변경을 강제하지 않는다.
- 현재 단계에서 사용하지 않을 상용 Provider Adapter와 capability abstraction을 미리 구현하지
  않는다. 실제 교체 결정이 내려졌을 때 필요한 Adapter만 추가한다.

2026-08-11 사용자 결정으로 별도 Backend 담당자는 없으며, 이후 필요한 Backend·LLM 변경도
파트너가 직접 수행한다. 단, Provider 선택과 교체는 현재 마감 범위가 아니며 품질·지연·비용
평가로 필요성이 확인될 때만 별도 Task로 연다.

### 2026-08-11 마감 축소 결정 — 예약 작업 UE 실행 Prototype

2026-08-12 1차 마감에서는 서버가 게임 종료 중 경과시간을 계산해 보상을 정산하는 전체
Offline Settlement를 구현하지 않는다. 대신 이미 배포된 Offline Task 상태 API와 기존 UE Local
Work를 연결해 다음 사용자 경험만 먼저 검증한다.

```text
Mobile Task 생성
→ UE가 Pending 목록을 요청 시 한 번 조회
→ GameClient start
→ 기존 MAKO Gathering/Crafting WorkOrder 실제 실행
→ Inventory 결과와 처리한 task_id를 같은 local persistence 경계로 확정
→ GameClient complete/claim
```

이 Prototype은 게임이 실행된 뒤 MAKO가 실제 작업을 수행하는 **예약 작업**이다. 게임이 꺼진
동안 서버가 작업을 완료하거나 보상을 생성하는 진정한 Offline Simulation이 아니다.

마감 포함 범위:

- Gathering 한 종류와 UE에서 이미 검증된 Crafting Recipe 한 종류
- 명시적인 `item_id` → UE Resource/Recipe stable ID allowlist
- UE 시작 또는 UE 사용자 동기화에 의한 단발 조회; polling 없음
- Local Work 성공 뒤에만 complete/claim
- Inventory 결과와 처리한 `task_id`를 함께 저장하고 재조회·재시작 중복 지급 방지
- 자원·재료·작업대 부족과 Backend 단절 시 Task를 지급 완료로 가장하지 않고 Local AI 유지

마감 제외 범위:

- AX-I05 구조화 LLM Context, Event ingest와 Command Result
- Conversation/Memory/관계 상태와 LLM Provider 평가·교체
- AX-I09/I10 Game State Snapshot, AX-W03 모바일 상태 조회
- AX-I11/I12 Settlement 원장·apply/ack, AX-W04 전체 Offline E2E
- 서버 RealWorld 경과 평가, 모바일 UE Inventory 표시와 전체 장애 매트릭스

이 축소는 정식 AX-I08~I12의 계약과 완료 조건을 대체하지 않는다. Prototype 성공만으로 AX-G3,
AX-G4 또는 Offline Task를 `Done` 처리하지 않고 `Review` 근거로만 사용한다.

## 1. 작업 구성

전체 구현을 Core 12개 Task와 Web 4개 Task, 5개 통합 Gate로 나눈다. 2026-08-12 마감에는
정식 Gate 밖의 `AX-P01` 예약 작업 Prototype을 추가한다.

```text
AX-I01 인증 계약
  ├→ AX-I02 UE 고정 인증 + HTTP Chat
  └→ AX-W01 Web 고정 인증 + Mobile Chat → AX-W02 Offline Task UI

AX-I02
      → AX-I03 Command Gateway Core ───────────────┐
      → AX-I04 World Context v1 → AX-I05 Backend B1│
                                                   ↓
                                      AX-I06 행동 명령 수직 슬라이스

AX-I07 Inventory SaveGame → AX-I08 Sync Outbox ──────────────┐
               └→ AX-P01 예약 작업→UE Local Work Prototype
AX-I09 Backend B2 Game State ┬→ AX-I10 UE Snapshot Sync ─────┤
                             └→ AX-W03 Mobile 상태 조회 ─────┤
AX-I11 Backend B3 Offline Settlement ─────────────────────────┤
                                                             ↓
                            AX-I12 UE Offline 적용 + AX-W04 Mobile E2E
```

Backend 변경은 `AX-I05`, `AX-I09`, `AX-I11` 세 Task로 제한한다. 조건부 Provider 교체는 현재
Core Task 수와 이 세 변경에 포함하지 않으며, 품질 평가 뒤 파트너 결정이 있을 때만 별도
Adapter 작업으로 연다. 나머지는 UE/Web client, 계약 또는 통합 검증 작업이다. Web은 현재
Vite+strict TypeScript 기반과 Chat UI가 있으므로 새 프로젝트를 만들지 않고 기존 `WebApp/`을
갱신한다.

## 2. Task 목록

### AX-I01. 인증 계약 확정

- 종류: Decision/Contract
- 선행: 없음
- 목표: 변경할 인증 방식과 UE의 책임을 확정한다.

확정 항목:

- credential 발급 endpoint와 request/response
- HTTP header 또는 cookie 형식
- 만료, refresh, revoke와 logout 규칙
- 401/403 의미와 재인증 정책
- UE 직접 등록 또는 외부 로그인 결과 주입 여부
- credential 저장 위치와 로그 금지 항목

완료 조건:

- 정상·만료·폐기·서버 장애 흐름의 request/response 예시가 있다.
- Chat 요청 자동 재전송 여부를 명시한다. 기본값은 자동 재전송 금지다.
- 인증 정보에서 `profile_id`를 결정하며 body의 profile 주장을 신뢰하지 않는다.

2026-08-10 결정:

- GameClient Bearer는 고정 공개값 `AIRE_GAME`을 사용한다.
- WebClient Bearer는 고정 공개값 `AIRE_WEB`을 사용한다.
- 두 역할은 고정 `AIRE_OPEN` profile을 공유한다.
- 두 역할은 고정 `demo-slot-1` Save Slot과 `mako` Companion을 공유한다.
- UE와 Web은 같은 단일 플레이어의 두 surface이며, 장기기억과 Task 공유가 정상 동작이다.
- 등록·pairing·랜덤 token 발급은 UE/Web 제품 경로에서 사용하지 않는다.
- 별도 mode switch, 만료, refresh, revoke와 credential 보안 저장을 두지 않는다.
- 누락·다른 Bearer는 기존 의존성 흐름대로 거부한다.
- Admin 인증은 이번 결정의 범위에 포함하지 않는다.
- 공개 인터넷에서 신원 위조, Task/상태 변조와 LLM 호출 남용이 가능함을 승인한 마감용 계약이다.

### AX-I02. Auth Provider 분리와 HTTP Chat 기준선

- 종류: UE
- 선행: AX-I01
- 대응 계획: `M04-E01-T02`, `M04-E02-T01`의 tactical subset
- 목표: Chat Component에서 인증 발급·저장 방식을 분리하고 현재 HTTP Chat 계약과 맞춘다.

구현 범위:

- 새 인증 방식의 credential 획득과 request header 적용
- 인증 거부를 Provider에 통지하고 명시적 실패 처리
- `POST /api/v1/chat` request/response/ErrorEnvelope 정합화
- 공개 ChatResponse만 해석하고 Provider 원본 응답이나 SDK 타입을 UE에 도입하지 않음
- `ai_metadata`는 진단 정보로만 보존하고 Provider 또는 모델별 Gameplay 분기를 만들지 않음
- `surface="game"`, 정수 hour, protocol companion ID 적용
- Transport 기본값 HTTP
- 이 단계의 `allowed_commands=[]`

완료 조건:

- 정상 대사 한 번과 401/403·timeout·malformed response를 구분한다.
- credential과 대화 원문을 로그에 출력하지 않는다.
- EndPlay 이후 callback이 상태를 변경하지 않는다.
- Backend 장애가 Local AI, Work와 Inventory를 중단하지 않는다.
- 공개 Chat 계약이 유지되는 Provider 교체는 UE 코드 변경을 요구하지 않는다.

### AX-I03. Command Candidate DTO와 Gateway Core

- 종류: UE
- 선행: AX-I02
- 대응 계획: `M04-E02-T01`, `M04-E03-T01`
- 목표: 외부 후보를 실행하지 않은 채 안전하게 수신·검증·거부할 수 있게 한다.

구현 범위:

- Command Candidate USTRUCT와 JSON parser
- request/command ID 상관관계
- type allowlist, issued/expires 검증
- bounded processed-command ID cache
- `Accepted/Rejected/Expired/Failed` local result
- 신규 `UAIRECompanionCommandGatewayComponent`

완료 조건:

- malformed, unsupported, expired와 duplicate 후보가 실행되지 않는다.
- `allowed_commands=[]`이면 어떤 후보도 수락하지 않는다.
- 이 Task에서는 StateTree, GAS와 Inventory를 변경하지 않는다.

### AX-I04. UE World Context v1과 안정 ID

- 종류: UE
- 선행: AX-I02
- 대응 계획: `M04-E02-T02`
- 목표: MAKO가 이미 알고 있는 상태를 bounded value Snapshot으로 조합한다.

최초 필드:

- GameWorld time과 `location_id`
- Threat present/count/nearest kind
- 주변 resource kind/count
- 사용 가능한 workstation stable tag
- current Work type/state
- MAKO/Storage Inventory 요약과 빈 Slot 수

구현 원칙:

- World 전수 검색을 하지 않는다.
- 기존 Threat, WorkOrder, Harvest/Workbench sensor의 상태를 request 시 조합한다.
- Actor name, class path와 UObject pointer를 전송하지 않는다.
- 배열, 문자열과 Entity 수에 상한을 둔다.

완료 조건:

- 같은 로컬 상태에서 결정적인 Snapshot을 만든다.
- 파괴된 Target과 만료된 감지 항목이 포함되지 않는다.
- GameWorld와 RealWorld 시간을 섞지 않는다.

### AX-I05. Backend B1 — 구조화 Chat Context 소비

- 종류: Backend 최소 변경 1/3
- 선행: AX-I04의 Context v1 계약
- 목표: 현재 `location_id`만 읽는 Chat service가 허용된 World Context를 실제 LLM 입력으로 사용한다.

구현 범위:

- 자유 `dict` 대신 versioned/allowlisted Context model
- key, ID, array와 전체 크기 제한
- Provider 중립적인 `CompanionTurn`과 Prompt 입력에 구조화 사실 전달
- 기존 `LLMProvider` Port와 현재 Provider 선택을 유지하고 Provider별 형식을 서비스 계층이나
  공개 Chat DTO로 누출하지 않음
- unknown/unsupported version 명시적 거부
- Prompt에 credential, raw UObject 값과 임의 현실 원문이 들어가지 않도록 검증

완료 조건:

- 나무·적·작업대 유무가 LLM Context에 반영된다.
- 잘못된 ID, 과대 배열, 금지 key와 unsupported version이 거부된다.
- Provider 원본의 malformed/unsupported 출력이 canonical ChatResponse로 가장되지 않고 공통
  invalid output 또는 안전한 fallback으로 처리된다.
- LLM이 낸 결과는 여전히 AX-I03 Gateway 없이는 실행되지 않는다.

### AX-I06. 행동 명령 수직 슬라이스

- 종류: UE
- 선행: AX-I03, AX-I04, AX-I05
- 대응 계획: `M04-E03-T01`, `M04-E03-T02`
- 목표: 지원 명령만 동적으로 허용하고 UE가 최종 실행 여부를 결정한다.

명령별 하위 Gate:

1. `CancelCurrent`: 현재 WorkOrder ID와 취소 가능 상태 검증
2. `GatherResource`: kind 일치, 반경, 고갈, 접근 가능 여부를 UE가 검사해 local Target 선택
3. `Attack`: 현재 UE가 선택한 hostile/alive Threat만 허용

Follow/Hold/Return은 production direct-command state가 준비된 뒤 별도 확장한다. 의미가 모호한
`Switch`는 허용하지 않는다.

완료 조건:

- Survival/Combat 로컬 우선순위가 외부 명령보다 우선한다.
- command 중복, 만료와 Target 파괴가 안전하게 종료된다.
- 각 command는 최종 local result 하나를 가진다.
- Backend 단절 뒤 기존 Local AI로 복귀한다.

### AX-I07. Inventory SaveGame export/import

- 종류: UE
- 선행: 현재 Gameplay Inventory local Gate
- 대응 계획: `M03-E08-T03`
- 목표: GameInstance Session 상태를 versioned local persistence로 보존한다.

구현 범위:

- MAKO/Shared Storage Container Snapshot과 Equipment 저장
- local format version, profile/save/companion scope
- Item/content version 검증
- import candidate와 operation ID
- atomic import, duplicate와 stale revision 거부

완료 조건:

- 저장·재실행 뒤 동일 Item 수량과 Equipment 상태를 복구한다.
- 깨진 파일, 다른 save slot, unknown item과 duplicate import가 상태를 부분 변경하지 않는다.
- 서버 없이 정상 동작한다.

### AX-I08. Durable Sync Outbox

- 종류: UE
- 선행: AX-I07
- 목표: 종료 HTTP 한 번이 아니라 local queue replay로 동기화 신뢰성을 만든다.

구현 범위:

- Snapshot/event operation ID와 body hash
- disk-persisted pending/ack 상태
- bounded queue와 합칠 수 있는 Snapshot coalescing
- timeout, cancellation과 다음 실행 replay
- 종료 시 best-effort flush

완료 조건:

- 요청 성공 후 ack가 유실되어도 같은 operation ID로 재전송한다.
- process 종료·재시작 뒤 pending entry를 잃지 않는다.
- queue가 무한 증가하지 않고 credential/대화 원문을 저장하지 않는다.

### AX-P01. 예약 작업 → UE Local Work Prototype

- 종류: UE/Web/기존 Backend 통합 Prototype
- 선행: AX-W02, AX-I07, 현재 Local Gathering/Crafting Gate
- 목표: 모바일에서 만든 예약 작업을 게임 실행 뒤 기존 MAKO WorkOrder로 실제 수행한다.

구현 범위:

- `AIRE_GAME`으로 Pending Task 목록 조회와 strict response validation
- allowlist된 한 종류 Gathering과 한 종류 Crafting만 UE Resource/Recipe stable ID로 매핑
- start 성공 뒤 기존 WorkOrder 요청, Local Work 성공 뒤 Inventory 결과 확정
- 같은 저장 경계에 처리한 `task_id`를 bounded ledger로 기록
- local commit 뒤 complete/claim; 통신 실패 시 재실행에서 상태 갱신만 재시도
- 자동 polling, 자동 Task 재실행과 무한 queue 금지

완료 조건:

- 모바일에서 생성한 Gathering/Crafting 각 한 건이 실제 MAKO Work를 거쳐 Inventory에 반영된다.
- 같은 Task 재조회와 프로세스 재시작이 Inventory를 두 번 증가시키지 않는다.
- 자원·재료·작업대 부족, Work 실패와 local save 실패에서는 claim하지 않는다.
- Backend 단절이 Local AI·기존 Work·Inventory를 중단시키지 않는다.
- `Claimed`는 여전히 서버 Task 상태이며 정식 Settlement receipt로 표현하지 않는다.

제외 범위:

- 서버 경과시간 평가와 Offline 보상 생성
- Game State Snapshot, Settlement ledger와 apply/ack receipt
- LLM 기반 작업 선택, World Context, Event/Command Result
- AX-G3/AX-G4 완료 판정

### AX-I09. Backend B2 — Game State Snapshot API

- 종류: Backend 최소 변경 2/3
- 선행: AX-I07의 저장 Schema, AX-I01의 인증 scope
- 목표: 모바일 조회와 재접속용 마지막 승인 상태를 저장한다.

계약 후보:

```text
PUT /api/v1/game-state
GET /api/v1/game-state?save_slot_id={id}&companion_id=mako
```

구현 범위:

- `(profile, save_slot, companion)` scope
- `snapshot_id`, monotonic `state_version`, captured/last_synced 시각
- Inventory revision/Item count/Equipment와 축약 World Context
- duplicate request same result, lower version conflict
- migration, repository, service, route와 tests

완료 조건:

- 다른 profile/save/companion 값을 조회하거나 덮어쓸 수 없다.
- 동일 snapshot 재요청이 중복 version을 만들지 않는다.
- 모바일 응답에 `last_synced_at`이 있다.

### AX-I10. UE↔Server Snapshot 동기화

- 종류: UE integration
- 선행: AX-I08, AX-I09
- 목표: 의미 있는 UE 변경을 서버 Game State Snapshot에 반영한다.

갱신 시점:

- Inventory/Work atomic commit 뒤 dirty
- 짧은 debounce
- 실행 중 30~60초 dirty-only checkpoint
- save/map 전환 enqueue
- 종료 best-effort flush
- 다음 시작 Outbox replay

완료 조건:

- stale upload가 최신 상태를 덮지 않는다.
- timeout과 재시작 뒤 같은 operation을 Outbox에서 재전송한다.

### AX-I11. Backend B3 — Offline Settlement 원장

- 종류: Backend 최소 변경 3/3
- 선행: AX-I09, 기존 OfflineTask
- 목표: 현재 status-only Claim을 정확히 한 번 적용 가능한 정산으로 확장한다.

구현 범위:

- task 완료와 같은 transaction의 `OfflineTaskSettlement`
- `settlement_id`, reward, task revision, finalized_at
- monotonic changes cursor
- GameClient 전용 apply/ack
- `(task_id, operation_id)` 멱등 영수증
- server RealWorld time과 작업별 duration/resource cap

완료 조건:

- Web collect와 Game apply race가 중복 보상을 만들지 않는다.
- ack 유실 뒤 같은 operation 재전송은 같은 receipt를 반환한다.
- profile/save mismatch, stale task revision과 unsupported task가 거부된다.

### AX-I12. UE Offline 적용과 Backend E2E

- 종류: UE/Backend integration
- 선행: AX-I08, AX-I10, AX-I11
- 목표: 모바일 작업이 재접속한 UE Inventory에 정확히 한 번 반영되는 전체 흐름을 완성한다.

흐름:

```text
모바일 task 생성
→ 서버 deferred 평가와 Settlement 확정
→ UE cursor 이후 Settlement 조회
→ UE scope/content/capacity/revision 검증
→ settlement_id 기반 local atomic mutation
→ Outbox로 apply/ack
→ 모바일 최종 상태와 최신 Snapshot 조회
```

완료 조건:

- 정상, duplicate, timeout, ack loss, restart와 stale revision 경로를 통과한다.
- local apply와 server ack 사이 crash 후 재시도해도 보상이 한 번만 들어간다.
- 공간 부족에서는 부분 지급하지 않고 명시적인 Pending/Rejected 상태를 유지한다.
- UE 종료 중 callback이 파괴 객체를 접근하지 않는다.

## 3. Mobile Web Task

### AX-W01. `AIRE_WEB` 고정 인증과 Mobile Chat

- 종류: Web
- 선행: AX-I01, 공개 Backend 재시작
- 목표: 기존 pairing 기반 WebApp을 고정 `AIRE_WEB`과 현재 `/api/v1/chat` 계약에 맞춘다.

현재 재사용 범위:

- Vite + strict TypeScript 기반
- 모바일 Chat 화면, 입력, message rendering과 오류 표시
- API client와 request ID 생성

수정 범위:

- pairing code, random device token 저장과 revoke UI를 제품 경로에서 제거
- 모든 Web 요청에 `Authorization: Bearer AIRE_WEB`
- `companion_id="mako"`, `save_slot_id="demo-slot-1"`
- 구형 `interaction_mode` 제거, `surface="mobile"` 추가
- 현재 ChatResponse와 ErrorEnvelope parser로 교체
- Provider 원본 응답을 해석하거나 `ai_metadata.provider/model_version`으로 UI 동작을 분기하지 않음
- 최초에는 `allowed_commands=[]`

완료 조건:

- 모바일 브라우저에서 MAKO에게 한 번 보내고 `display_text`를 표시한다.
- 새로고침 뒤 pairing 화면으로 돌아가지 않는다.
- timeout, invalid JSON과 Backend 미접속을 UI에 표시한다.
- Chat 실패가 같은 메시지를 자동 재전송하지 않는다.
- 공개 Chat 계약이 유지되는 Provider 교체는 Web 코드 변경을 요구하지 않는다.

### AX-W02. Offline Task 요청과 목록 UI

- 종류: Web
- 선행: AX-W01, 기존 OfflineTask API
- 목표: 모바일에서 Gathering/Crafting 요청을 만들고 현재 진행 상태를 조회한다.

구현 범위:

- `POST /api/v1/tasks`, `GET /api/v1/tasks`
- Pending/InProgress/Completed/Claimed 표시
- Gathering item/quantity 입력과 진행량 표시
- 지원되지 않는 Scouting과 잘못된 Crafting 요청 비활성화
- 기존 `/collect`는 상태 Prototype으로만 사용

완료 조건:

- 동일 request ID 재시도가 task를 중복 생성하지 않는다.
- WebClient가 GameClient 전용 start/complete/claim을 호출하지 않는다.
- `Completed`를 UE Inventory 지급 완료로 표시하지 않는다.

### AX-W03. Mobile Game State 조회

- 종류: Web
- 선행: AX-I09
- 목표: 서버가 보관한 마지막 MAKO/Storage/World Snapshot을 모바일에서 표시한다.

표시 범위:

- MAKO Inventory와 Equipment 요약
- Shared Storage Item 수량
- current Work와 축약 World Context
- `last_synced_at`, `state_version`과 offline/stale 표시

완료 조건:

- 모바일이 서버 Snapshot을 수정하지 않고 읽기만 한다.
- 마지막 동기화 시각을 숨기지 않는다.
- Snapshot이 없거나 오래됐을 때 현재 인게임 상태처럼 표현하지 않는다.

### AX-W04. Mobile Offline Settlement E2E

- 종류: Web integration
- 선행: AX-W02, AX-W03, AX-I11, AX-I12
- 목표: 모바일 작업 요청부터 UE 적용 영수증까지 사용자에게 일관된 상태를 표시한다.

완료 조건:

- Task와 Settlement 상태를 구분한다.
- UE 적용 전 Completed 결과를 지급 완료라고 표시하지 않는다.
- UE apply/ack 뒤 최신 Game State를 다시 조회한다.
- duplicate, conflict, capacity failure와 Backend 재시작 상태를 표현한다.

Memory 탭은 일반 사용자 Memory 조회·삭제 API가 생기기 전까지 placeholder로 유지한다.

## 4. 통합 Gate

| Gate | 포함 Task | 통과 기준 |
|---|---|---|
| AX-G0 Auth | I01 | 새 인증 계약과 실패 정책 확정 |
| AX-G1 HTTP Chat | I02, W01 | UE와 Mobile에서 인증된 대사 요청 1회와 실패 격리 |
| AX-G2 Command | I03~I06 | Context 기반 후보가 UE 검증 뒤에만 실행 |
| AX-G3 State Sync | I07~I10, W03 | Save/restart/모바일 조회에서 Snapshot과 revision 일치 |
| AX-G4 Offline | I11~I12, W02, W04 | 모바일 작업 보상이 crash/retry에도 정확히 한 번 적용 |

Gate가 실패한 상태에서 후속 Task를 완료 처리하지 않는다.

## 5. 권장 착수 순서

### 2026-08-12 1차 마감 전

기존 `G-UE-VS`의 Local Work·Inventory 기반과 완료된 AX-W02를 재사용한다. 새 Backend/LLM
기능은 추가하지 않고 다음 순서만 진행한다.

1. AX-I07 Inventory SaveGame export/import
2. AX-P01 예약 작업 → UE Local Work Prototype
3. Gathering 한 종류와 Crafting 한 종류의 사용자 Build/PIE 확인
4. 같은 Task 재조회·재시작 시 Inventory 중복 지급이 없는지 확인

AX-I07에는 HTTP나 Offline Task 상태 전환을 넣지 않는다. AX-P01이 AX-I07의 bounded operation
ledger를 사용해 `task_id`를 영속 처리 ID로 연결한다. 이 마감 결과는 AX-G3/AX-G4가 아니라 별도
Prototype `Review`로 기록한다.

### 마감 이후 Iteration 1 — UE/Mobile 대화와 행동

1. AX-I01
2. AX-I02와 AX-W01 병렬
3. AX-W02
4. AX-I03
5. AX-I04
6. AX-I05
7. AX-I06

### Iteration 2 — 저장과 조회

1. AX-I07
2. AX-I08
3. AX-I09
4. AX-I10과 AX-W03 병렬

AX-I07과 AX-I09는 Schema가 합의되면 UE/Backend에서 병렬 구현할 수 있다. 같은 파일을 동시에
수정하지 않는다.

### Iteration 3 — Offline 작업

1. AX-I11
2. AX-I12
3. AX-W04

### 조건부 LLM Provider 교체

현재 Provider의 현상 유지를 기본값으로 한다. 대화·명령 해석·기억 품질이 합의된 평가 기준을
충족하지 못하고 파트너가 다른 상용 API 사용을 결정한 경우에만 별도 Adapter 작업을
연다. 이 작업은 AX Core Task의 선행조건이 아니며 다음 조건을 모두 만족해야 완료할 수 있다.

1. 기존 Provider 중립 `LLMProvider` 입력·출력과 공개 Chat 계약을 변경하지 않는다.
2. Mock, 현재 Provider와 신규 Adapter가 같은 계약 fixture를 통과한다.
3. 정상, timeout, unavailable, rate limit, malformed output과 cancellation을 공통 결과로 검증한다.
4. 동일 품질 fixture에서 대화·Command·Memory 결과와 지연·오류율·예상 비용을 비교해 선택 근거를
   기록한다.
5. secret과 공급자 원본 대화 데이터가 UE/Web, 응답과 일반 로그에 노출되지 않는다.
6. 기존 계약으로 표현할 수 없는 필수 capability가 확인되면 공급자 이름 분기를 추가하지 않고
   먼저 canonical 계약과 버전 정책을 합의한다.

## 6. 현재 첫 착수 Task

AX-I01 고정 공개 인증은 완료됐다. 공개 Backend가 새 코드로 열렸다고 가정하면 다음 두 Task를
서로 다른 파일 경계에서 바로 병렬 착수한다.

1. AX-I02: UE HTTP Chat JSON 계약 정합화
2. AX-W01: Web `AIRE_WEB` 고정 인증과 Mobile Chat 계약 정합화

두 Task 모두 canonical identity `AIRE_OPEN / demo-slot-1 / mako`를 사용한다. 별도 로그인,
사용자 선택과 Save Slot 선택 UI는 만들지 않는다.

AX-I02는 다음 파일 경계에서 시작한다.

- `LMK/MAKO/Public/Chat/Auth/`
- `LMK/MAKO/Private/Chat/Auth/`
- `AIRECompanionChatComponent.h/.cpp`
- `AIREChatJsonAdapter.cpp`
- `AIREChatSettings.h`

사용자가 현재 이동·삭제 중인 `.uasset`과 `.umap`은 수정하지 않는다. Unreal Build, Editor
asset 설정과 PIE 검증은 사용자 수행 항목으로 남긴다.

AX-W01은 다음 파일 경계만 수정한다.

- `WebApp/src/main.ts`
- `WebApp/src/api/client.ts`
- `WebApp/src/auth/credentials.ts` 제거 또는 제품 경로 제외
- `WebApp/src/config.ts`
- 필요한 범위의 `WebApp/src/style.css`, `WebApp/README.md`

병렬 착수용 로컬 산출물:

- AX-I02 Issue: [`AX_I02_UE_HTTP_CHAT_ISSUE_DRAFT.md`](../.agents/docs/handoffs/AX_I02_UE_HTTP_CHAT_ISSUE_DRAFT.md)
- AX-I02 Context: [`AX_I02_UE_HTTP_CHAT_CONTEXT.md`](../.agents/docs/handoffs/AX_I02_UE_HTTP_CHAT_CONTEXT.md)
- AX-W01 Issue: [`AX_W01_WEB_MOBILE_CHAT_ISSUE_DRAFT.md`](../.agents/docs/handoffs/AX_W01_WEB_MOBILE_CHAT_ISSUE_DRAFT.md)
- AX-W01 Context: [`AX_W01_WEB_MOBILE_CHAT_CONTEXT.md`](../.agents/docs/handoffs/AX_W01_WEB_MOBILE_CHAT_CONTEXT.md)

## 7. 관련 문서

- 기능 방향: [`08_AX_FEATURE_ROADMAP.md`](08_AX_FEATURE_ROADMAP.md)
- 8월 12일 컷: [`07_2026-08-12_BACKEND_FREEZE_PLAN.md`](07_2026-08-12_BACKEND_FREEZE_PLAN.md)
- 현재 LLM/Embedding 설정: [`llm-setup.md`](../../docs/llm-setup.md)
- 기존 UE 통합 계획: [`M04_ue_backend_integration.md`](../.agents/docs/planning/M04_ue_backend_integration.md)
- Inventory 계약: [`GAMEPLAY_INVENTORY.md`](../AI_RE/Docs/UE/GAMEPLAY_INVENTORY.md)
