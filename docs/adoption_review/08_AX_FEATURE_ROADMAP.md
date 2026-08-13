# 08. AX 기준 기능 로드맵

작성일: 2026-08-10  
전제: 여기서 `AX`는 현재 `ai_server`와 AI_RE의 MAKO 로컬 AI·Work·Inventory 구현을 함께
사용하는 기준을 뜻한다. 별도 AX 구현 저장소가 있다면 그 경로를 받은 뒤 DTO와 실행 경계만
다시 대조한다.

## 0. 제품 Identity 기준

현재 AX 제품은 `AIRE_OPEN / demo-slot-1 / mako` 하나만 사용한다. `AIRE_GAME`과 `AIRE_WEB`은
같은 단일 플레이어가 쓰는 UE와 Mobile Web surface이며, 같은 기억·Offline Task·마지막 승인
Snapshot을 공유한다. 여러 사용자, 여러 Save Slot 또는 여러 Companion을 분리하는 기능은 현재
로드맵 범위 밖이다.

프로토콜의 profile/save/companion 필드는 제거하지 않는다. canonical 값 검증과 잘못된 요청
거부에 사용하고, 향후 제품 범위가 실제로 확장될 때만 분리 정책을 다시 설계한다.

## 1. 결론

목표 기능은 모두 필요하지만 2026-08-12까지 한 번에 연결하지 않는다. 1차 마감은 기존
Combat·Work·Inventory 로컬 수직 슬라이스를 유지하고, HTTP Chat은 대사 표시까지만 선택적으로
붙인다. 행동 명령, 월드 Context, 서버 Inventory 조회와 Offline 정산은 마감 뒤 아래 순서로
구현한다.

1. UE Command Gateway와 제한된 Chat 행동 명령
2. UE World Context Snapshot과 Chat Context 전달
3. UE SaveGame·동기화 Outbox와 서버 Game State Snapshot
4. Offline Task 정산 원장과 정확히 한 번 적용

핵심 원칙은 다음과 같다.

- 플레이 중 전투·이동·WorkOrder·Inventory의 최종 권위는 UE다.
- LLM과 모바일은 행동 또는 작업을 제안하며 UE 상태를 직접 변경하지 않는다.
- UE와 모바일은 동일한 단일 플레이어의 상태와 장기기억을 공유한다.
- 서버는 인증, 마지막 승인 Snapshot, Offline Task 상태와 정산 원장을 소유한다.
- 게임 종료 HTTP 한 번에 정확성을 의존하지 않는다. 로컬 저장과 재전송 가능한 Outbox가
  정확성을 담당한다.
- Actor 이름, UObject 경로와 포인터를 프로토콜 ID로 사용하지 않는다.

## 2. 현재 구현 대비 판정

| 요구 기능 | 현재 상태 | Backend 무수정 범위 | 최종 구현에 필요한 것 |
|---|---|---|---|
| 채팅을 통한 MAKO 행동 제어 | 서버는 Command 후보를 반환할 수 있으나 UE가 파싱·실행하지 않음 | HTTP 대사 표시 | UE Command Gateway. 결과 보고는 후속 API 필요 |
| MAKO/Storage 저장·조회 | UE Session Snapshot과 원자 mutation은 구현됨. SaveGame·서버 API는 없음 | 인게임 로컬 조회·변경 | 로컬 SaveGame/Outbox, 서버 Snapshot GET/PUT |
| UE World 감시와 LLM Context | Threat·Work·Harvest 실행 정보는 로컬 컴포넌트에 있음. Context 조합기·안정 Entity ID 없음 | 로컬 판단과 UI | UE Context Snapshot, 안정 ID/Tag, 서버가 구조화 Context를 실제 Prompt에 사용 |
| 모바일 Offline 작업과 게임 반영 | 서버 OfflineTask CRUD·시간 진행은 있음. Inventory 지급 원장·적용 영수증은 없음 | 작업 생성·목록 Prototype | Settlement 원장, UE apply/ack 멱등성, revision/cursor |
| 주기·행동·종료 Sync | 관련 endpoint와 durable client Outbox 없음 | 불가 | dirty checkpoint, 로컬 Outbox, 서버 Snapshot revision |

현재 `POST /api/v1/chat`의 `game_context`는 값을 받을 수 있지만 실제 서비스는 사실상
`location_id`만 사용한다. UE가 나무·적·작업대·Inventory를 넣어 보내기만 해서는 LLM이 그
상태를 알게 되지 않는다. 이 부분은 Backend의 Context 소비 로직을 최소 수정해야 한다.

## 3. 데이터 권위

| 데이터 | 쓰기 권위 | 다른 쪽의 역할 |
|---|---|---|
| MAKO 이동·전투·현재 Target | UE | 서버/LLM은 후보 명령만 제시 |
| 현재 WorkOrder | UE | 서버/모바일은 Offline Task 또는 명령 의도만 기록 |
| MAKO Inventory·Shared Storage | UE | 서버는 마지막 승인 Snapshot을 조회용으로 보관 |
| 주변 적·자원·작업대 | UE | 서버/LLM은 요청에 포함된 제한된 Snapshot만 신뢰 수준과 시각을 표시해 사용 |
| Offline Task 상태·경과 시간 | 서버 시간 기준 서버 | UE는 결과 후보를 검증하고 로컬 상태에 적용 |
| Offline 보상 적용 여부 | 서버 Settlement 원장 + UE local mutation ledger | 양쪽이 같은 `settlement_id`/`operation_id`로 중복 방지 |
| 대화·장기 기억 | 서버 | UE는 현재 대사와 필요한 최소 Context만 전송 |

서버 Snapshot은 UE Inventory의 두 번째 쓰기 권위가 아니다. 모바일에서 보여 주는 값은
`last_synced_at`과 revision을 함께 가진 마지막 관찰값이며, UE가 실행 중일 때 발생하는
conflict는 UE 최신 Snapshot을 기준으로 해소한다.

## 4. 기능 1 — 채팅을 통한 MAKO 행동 제어

### 재사용할 구현

- `ai_server`는 `command_candidates`를 반환하고 issued/expires 시각과 parameter를 제한한다.
- 실제 후보는 Follow, HoldPosition, CancelCurrent, GatherResource, Attack, Switch 여섯 종류다.
- UE에는 Threat, WorkOrder, StateTree와 GAS 실행기가 이미 있다.
- UE Chat은 timeout, 취소와 늦은 callback 무시 수명주기를 이미 갖고 있다.

### 빠진 실행 경계

신규 `UAIRECompanionCommandGatewayComponent` 하나가 외부 명령의 유일한 진입점이 되어야 한다.
Gateway는 다음 순서로 처리한다.

```text
HTTP Chat response
→ Command candidate DTO 파싱
→ request/command ID, type allowlist, expires_at, 중복 여부 검증
→ 현재 UE Threat·Work·Inventory·StateTree 정책으로 실행 가능 여부 재검증
→ 로컬 typed request로 변환
→ UE가 최종 수락 또는 거부
→ local result 기록
```

Combat·Survival 정책은 외부 명령보다 우선한다. Backend가 끊겨도 기존 로컬 AI는 그대로
동작해야 한다.

### 명령 개방 순서

| 순서 | 명령 | 채택 조건 |
|---|---|---|
| 1 | `CancelCurrent` | 현재 WorkOrder ID와 취소 가능 상태를 검증 |
| 2 | `GatherResource` | 주변의 유효한 자원 후보를 UE가 고르고 고갈·거리·접근 가능 여부를 검증 |
| 3 | `Attack` | UE가 이미 선택한 hostile/alive Threat만 대상으로 허용 |
| 4 | Follow/Hold/Return | production direct-command state와 취소·전투 선점 규칙 구현 뒤 개방 |

`Switch`는 현재 서버 내부에서 ReturnToPlayer 의미로 쓰이지만 이름이 모호하다. 계약을
`Command.ReturnToPlayer`로 정리하기 전에는 허용하지 않는다. UE는 실제 지원하는 명령만
`allowed_commands`에 넣고, enum 전체를 한꺼번에 개방하지 않는다.

## 5. 기능 2 — MAKO/Shared Storage 저장과 조회

### 유지할 UE 구조

현재 `UAIREGameplayInventorySubsystem`의 아래 계약은 그대로 유지한다.

- `AIRE.Inventory.MAKO`: 20칸과 Equipment
- `AIRE.Inventory.SharedStorage`: 50칸
- Session ID, Container별 revision, mutation GUID
- `AlreadyApplied`, `RevisionConflict`와 전체 연산 원자성

서버가 Item Add/Remove/Transfer를 직접 실행하게 만들지 않는다.

### 먼저 필요한 UE 작업

현재 Inventory는 GameInstance Session 수명이고 SaveGame 영속성이 아니다. 따라서 서버 Sync보다
먼저 다음 두 로컬 기능이 필요하다.

1. versioned Inventory SaveGame export/import
2. 아직 서버가 확인하지 않은 Snapshot을 보관하는 durable Outbox

종료 직전 HTTP 전송은 best effort로만 사용한다. 전송 실패 시 다음 실행에서 Outbox를 다시
보내야 한다.

### Backend 최소 계약 제안

새 리소스 하나로 MAKO와 Shared Storage의 마지막 승인 상태를 함께 보관한다.

```text
PUT /api/v1/game-state
GET /api/v1/game-state?save_slot_id={id}&companion_id=mako
```

필수 의미 필드는 다음과 같다.

- `schema_version`, `snapshot_id`, `state_version`
- token에서 결정한 `profile_id`, 명시한 `save_slot_id`, `companion_id`
- `world_session_id`, `captured_at`, `last_synced_at`
- MAKO/Shared Storage의 `container_id`, `revision`, Item ID별 count
- MAKO Equipment 요약
- 마지막 Offline Task cursor

서버는 `(profile, save_slot, companion, snapshot_id)` 중복 요청을 같은 결과로 처리하고,
낮은 `state_version`의 덮어쓰기를 conflict로 거부한다. 모바일 조회 화면에는 반드시
`last_synced_at`을 표시한다.

## 6. 기능 3 — UE World 감시와 LLM Context

### 수집 방식

World 전체를 주기적으로 `GetAllActorsOfClass`로 훑지 않는다. MAKO가 이미 소유한 컴포넌트와
센서가 알고 있는 상태를 event-driven cache에 기록하고 Chat 요청 시 값 Snapshot으로 조합한다.

최초 Context는 다음만 보낸다.

```json
{
  "location_id": "forest_camp",
  "threat": { "present": true, "count": 1, "nearest_kind": "enemy.goblin" },
  "nearby_resources": [{ "kind": "resource.wood", "count": 3 }],
  "available_workstations": ["workstation.crafting.basic"],
  "current_work": { "type": "Crafting", "state": "PausedByCombat" },
  "inventory_summary": { "wood": 8, "free_mako_slots": 4, "free_storage_slots": 12 }
}
```

이 값은 설명용 Context다. 실제 행동 대상을 지정하려면 별도로 안정 `entity_id`를 Actor에
매핑하는 registry가 필요하다. Actor 이름, class name과 UObject path는 전송하지 않는다.

### Backend 최소 수정

현재 Chat service가 `game_context`의 구조화 필드를 검증하고 `CompanionTurn`/LLM Prompt에
제한된 사실로 전달하도록 수정한다. 자유 문장을 그대로 넣지 않고 allowlist된 key와 bounded
array를 사용한다. LLM 출력은 여전히 사실이나 명령으로 바로 실행하지 않고 Command Gateway를
통과한다.

서버에 마지막 월드 상태를 남길 때도 별도 월드 API를 늘리지 않고 기능 2의 Game State
Snapshot에 축약본을 포함한다.

## 7. 기능 4 — 모바일 Offline Task와 양방향 Sync

### 현재 코드에서 재사용할 부분

- WebClient의 task 생성과 조회
- GameClient의 task start/complete/claim role 분리
- `(profile, save_slot, creation_request_id)` task 생성 멱등성
- 서버 시각 기준 경과 시간 계산

현재 `Claimed`는 상태 문자열일 뿐 Inventory 지급 영수증이 아니다. 이 상태만 보고 UE에 Item을
넣으면 ack 유실 시 중복 지급 또는 미지급이 생긴다. 현재 형태를 최종 정산으로 사용하지 않는다.

### 필요한 정상 흐름

```text
모바일: Offline Task 생성
→ 서버: 시간과 상한으로 결과 확정 + Settlement 원장 생성
→ UE 시작/재접속: cursor 이후 Settlement 조회
→ UE: profile/save/item/content version과 local revision 검증
→ UE: settlement_id를 mutation ID로 사용해 local Inventory에 정확히 한 번 적용
→ UE: 같은 operation_id로 서버 apply/ack 재시도
→ 서버: 적용 영수증을 멱등 저장
```

Offline 결과가 현재 Inventory 공간에 들어가지 않으면 부분 지급하지 않는다. Pending 상태로
남기거나 명시적 WorldDrop/Storage 정책을 적용한 뒤 결과를 기록한다.

### Backend 최소 계약 추가

1. task 완료와 같은 DB transaction에서 `OfflineTaskSettlement` 생성
2. `settlement_id`, `task_revision`, reward, finalized_at과 apply receipt 저장
3. GameClient 전용 idempotent apply/ack endpoint
4. monotonic cursor를 사용하는 task changes 조회

예시 endpoint 이름은 다음과 같다. 최종 명칭은 Backend owner가 OpenAPI에 확정한다.

```text
GET  /api/v1/tasks/changes?save_slot_id={id}&cursor={cursor}
POST /api/v1/tasks/{task_id}/apply
```

`apply` 요청에는 `operation_id`, `settlement_id`, `expected_task_revision`, UE
`world_session_id`와 적용 결과 revision이 포함되어야 한다. 동일 operation 재전송은 같은
영수증을 반환해야 한다.

### UE → 서버 갱신 시점

- Inventory/Work 결과를 원자 commit한 뒤 dirty 표시
- 여러 변경은 짧게 debounce해 한 Snapshot으로 합침
- 게임 실행 중 30~60초마다 dirty 상태일 때만 checkpoint
- save/map 전환 때 즉시 enqueue
- 게임 종료 때 best-effort flush
- 다음 시작 때 미확인 Outbox를 먼저 재전송

정확성은 종료 flush가 아니라 SaveGame과 Outbox replay가 보장한다.

## 8. Backend 최소 변경 Pack

전체 Backend를 갈아엎지 않고 목표 기능을 만들려면 아래 세 묶음은 피할 수 없다.

| 순서 | 변경 | 필요한 요구 |
|---|---|---|
| B1 | Chat의 구조화 `game_context` 검증·Prompt 소비 | 월드 상태를 아는 LLM |
| B2 | versioned Game State Snapshot GET/PUT과 테이블 | Inventory·World 마지막 상태 조회, 양방향 Sync |
| B3 | Offline Settlement 원장, apply/ack 멱등성, changes cursor | 모바일 작업을 UE에 정확히 한 번 반영 |

Command Result/Event endpoint, Session start/end, 사용자 데이터 삭제, rate limit과 운영 hardening은
필요하지만 위 세 묶음 뒤에 진행한다. 단, 공개 배포 전에는 보안·운영 항목도 완료해야 한다.

## 9. 추가로 필요한 기능

사용자 요구 1~4를 안정적으로 연결하려면 다음 항목도 필요하다.

1. **안정 ID Registry**: Item, resource kind, enemy kind, workstation과 실제 Actor 매핑
2. **Command Result lifecycle**: Accepted/Rejected/Expired/Cancelled/Failed와 사유
3. **Content version**: UE와 서버 Item/Recipe ID dataset 불일치 거부
4. **Capability allowlist**: 현재 UE build가 지원하는 명령과 Context version만 전송
5. **SaveGame + Outbox**: 종료 실패와 재시작 뒤에도 Snapshot/Event 재전송
6. **Conflict 정책**: stale snapshot, task revision과 Inventory capacity 실패 처리
7. **시간 분리**: GameWorld 시간, 서버 RealWorld 시간과 client 표시 시간을 혼용하지 않음
8. **관찰 가능성**: request/command/task/settlement ID로 추적하되 대화 원문과 token은 로그에서 제외

## 10. 2026-08-12 컷

### 반드시 완료

- Backend 없이 기존 Combat·Work·Inventory 수직 슬라이스 통과
- 같은 WorkOrder/Delivery/mutation 재호출 시 중복 보상 없음
- MAKO와 Shared Storage의 로컬 Snapshot/revision UI 확인
- 사용자 Unreal Build와 PIE 결과 기록

### 시간이 남을 때만

- 현재 HTTP Chat DTO 차이만 수정
- `allowed_commands=[]` 대사 한 번 표시
- Backend 실패·timeout·EndPlay가 로컬 AI를 막지 않음 확인

### 8월 12일 뒤

- Command Gateway와 명령별 resolver
- World Context Snapshot
- SaveGame/Outbox와 Game State API
- Offline Settlement/ack/cursor
- 모바일 전체 흐름

요구 1~4를 8월 12일 완료 조건에 넣지 않는다. 현재 구현에서 그것은 작은 연결 작업이 아니라
새 client 실행 경계, 영속 저장 계약과 서버 정산 원장을 동시에 만드는 범위다.

## 11. 채택 근거

- 현재 마감 Gate: [`2026_08_12_ue_vertical_slice.md`](../.agents/docs/planning/2026_08_12_ue_vertical_slice.md)
- UE Context 방향: [`03_context_manager.md`](../.agents/docs/03_context_manager.md)
- Offline 정산 방향: [`05_idle_simulation.md`](../.agents/docs/05_idle_simulation.md)
- Inventory 권위와 exact-once: [`GAMEPLAY_INVENTORY.md`](../AI_RE/Docs/UE/GAMEPLAY_INVENTORY.md)
- 현재 Server Chat 모델: [`models.py`](../ai_server/app/models.py)
- 현재 Offline Task 모델: [`offline_task_models.py`](../ai_server/app/offline_task_models.py)
