# 생존 크래프팅 AI 동료 시스템 설계

> 보관용 목표 아키텍처 참고 자료이며 현재 Build 1의 구현 계약이 아니다.

## 최소 행동 기반 AI 스냅샷·라우팅·대사 생성 구조

---

## 1. 문서 목적

이 문서는 생존 크래프팅 게임의 AI 동료 NPC가 수행해야 하는 최소 행동을 기준으로 다음 항목을 정의한다.

- 클라이언트가 AI 서버로 전달해야 하는 `AIContextSnapshot`
- 사용자 요청과 인게임 트리거의 처리 경로
- LLM 라우터와 기능별 파이프라인의 책임
- 클라이언트와 AI 서버의 역할 구분
- 애매한 사용자 요청을 처리하는 재질의(Human-in-the-Loop) 방식
- AI 동료와의 유대감을 살리기 위한 대사 사전 생성 방식
- 장기 기억과 관계 정보의 비동기 처리 방식

핵심 목표는 다음과 같다.

> LLM은 라우팅, 비정형 자연어 해석, 대사 생성에만 사용한다.  
> 인벤토리 비교, 제작 가능 여부, 수량 계산과 같은 결정적 판정은 서버 코드가 수행한다.  
> 실제 게임 행동과 아이템 소비 여부는 클라이언트가 최신 상태로 다시 검증한다.  
> 반복적인 고정 대사는 최소화하되, 필요한 대사는 미리 생성하여 응답 속도와 캐릭터성을 동시에 확보한다.

---

## 2. AI 동료 최소 기능 범위

본 설계가 지원하는 최소 기능은 다음과 같다.

1. 플레이어 따라가기·대기하기
2. 주변 주요 사건에 반응하기
3. 간단한 일상 대화
4. 레시피와 제작 방법 알려주기
5. 세계관과 발견 정보 설명하기
6. 제한된 채집·수확·운반 명령 수행
7. 전투 중 간단한 지시 수행
8. 현재 작업 상태와 결과 보고
9. 플레이어가 질문했을 때 게임 진행 도움 제공
10. 위험 경고
11. 함께한 경험 기억하고 언급하기

### MVP에서 보류하는 기능

- 플레이어가 막혔는지 AI가 자율적으로 판단하여 먼저 개입하는 기능
- 복잡한 건축 자동화
- 복잡한 전술 명령과 다단계 전투 계획
- 생산 시설 전체 자동 운영
- 장거리 자율 탐험
- 퀘스트 자동 해결
- 여러 AI 동료 간 자율 협업
- 감정에 따른 복잡한 명령 거부
- 연애·호감도 분기

플레이어가 진행 방법을 직접 질문하는 경우에는 `GAME_HELP` 파이프라인으로 답변하되, AI가 먼저 개입하는 기능은 보류한다.

---

# 3. 전체 요청 처리 구조

AI 서버의 첫 번째 입구에서 요청 출처를 구분한다.

```text
AI Server Request Gateway
        │
        ├─ USER_REQUEST
        │     └─ LLM Router
        │            └─ Route Dispatcher
        │                   ├─ COMMAND Pipeline
        │                   ├─ RECIPE Pipeline
        │                   ├─ LORE Pipeline
        │                   ├─ GAME_HELP Pipeline
        │                   ├─ CONVERSATION Pipeline
        │                   ├─ MEMORY_RECALL Pipeline
        │                   └─ CLARIFICATION Resume
        │
        └─ GAME_TRIGGER
              └─ Event Dispatcher
                     ├─ DANGER_WARNING Pipeline
                     ├─ TASK_RESULT Pipeline
                     ├─ MAJOR_EVENT_REACTION Pipeline
                     └─ MEMORY_BACKGROUND Pipeline

각 사용자 파이프라인
        ↓
필수 슬롯·엔티티·권한 검증
        ├─ 충분함 → 계속 실행
        └─ 부족함 → PENDING_CLARIFICATION 저장
                         ↓
                   플레이어에게 재질의
                         ↓
                 다음 USER_REQUEST로 응답
                         ↓
                   기존 파이프라인 재개
```

## 3.1 사용자 요청

플레이어의 음성·텍스트 입력은 종류와 관계없이 모두 LLM 라우터를 통과한다.

클라이언트는 다음 두 가지를 함께 전송한다.

1. 사용자 질문 또는 명령 원문
2. 질문이 발생한 순간의 `AIContextSnapshot`

라우터에는 원문만 전달한다.  
스냅샷 전체는 라우터 컨텍스트에 넣지 않는다.

진행 중인 재질의가 있다면 클라이언트는 `clarification_id`를 함께 보낸다.

```json
{
  "origin": "USER_REQUEST",
  "text": "돌 원석 말한 거야",
  "clarification_id": "clarify_382",
  "snapshot": {
    "...": "AIContextSnapshot"
  }
}
```

라우터는 이 입력이 새로운 독립 요청인지 기존 재질의에 대한 응답인지 구분한다.  
기존 슬롯과 새 응답의 병합 및 파이프라인 재개는 해당 기능 파이프라인이 담당한다.

## 3.2 인게임 트리거

클라이언트가 발생시킨 이벤트는 라우터를 거치지 않는다.

예시:

- 적 접근
- 플레이어 체력 위험
- 작업 성공·실패
- 첫 지역 발견
- 첫 거점 완성
- 주요 전투 종료
- 기억 정리 요청

클라이언트가 `trigger_type`을 지정하고, 이벤트에 필요한 데이터만 함께 보낸다.

---

# 4. 역할과 책임 분리

## 4.1 LLM 라우터

라우터는 다음 작업만 수행한다.

- 사용자 요청의 목적에 맞는 파이프라인 선택
- 검색 또는 후속 처리에 사용할 주요 키워드 추출
- 재질의 응답인지 일반 요청인지 구분
- 라우팅 확신도 반환

라우터가 하지 않는 작업:

- 툴 이름 결정
- 게임 아이템 ID 결정
- 수량 결정
- 게임 행동 가능 여부 판정
- 레시피·세계관 답변 작성
- 플레이어에게 전달할 대사 작성
- 스냅샷에서 필요한 필드 직접 선택

### 라우터 입력

```json
{
  "text": "근처에서 나무 20개만 모아 줘"
}
```

### 라우터 출력

```json
{
  "route": "COMMAND",
  "keywords": ["나무", "20개", "모으기"],
  "confidence": 0.98
}
```

### 권장 라우트

```text
COMMAND
RECIPE
LORE
GAME_HELP
CONVERSATION
MEMORY_RECALL
CLARIFICATION_RESPONSE
UNKNOWN
```

---

## 4.2 기능별 파이프라인

각 파이프라인의 **서버 코드**는 다음 세 가지를 입력으로 받는다.

1. 사용자 요청 원문
2. 라우터가 추출한 키워드
3. 코드가 `AIContextSnapshot`에서 추출한 기능별 데이터

```text
Pipeline Server Input
├─ original_text
├─ route_keywords
└─ selected_snapshot_fields
```

각 파이프라인에 필요한 필드는 코드에 미리 정의한다.

예시:

```text
RECIPE Pipeline Server Code
→ inventory.items
→ inventory.revision
→ progression.unlocked_recipe_ids
→ crafting.nearby_station_types
→ interaction.focus_target

LORE Pipeline Server Code
→ location.current_region_id
→ interaction.focus_target
→ progression.story_stage
→ progression.story_flags
→ progression.discovered_lore_ids
```

여기서 `selected_snapshot_fields`는 **LLM 입력이 아니라 서버 코드의 판정 재료**다.

서버 코드는 스냅샷과 DB를 사용해 다음 결과를 먼저 만든다.

```text
구조화 데이터 조회
→ 엔티티 정규화
→ 조건 비교
→ 가능 여부·부족 수량·접근 가능 정보 판정
→ Dialogue Fact 생성
```

그 후 LLM에는 원본 스냅샷이 아니라, 코드가 확정한 `Dialogue Fact`만 전달한다.

```text
LLM Input
├─ 사용자 질문
├─ 캐릭터·관계 요약
└─ 서버 코드가 확정한 Dialogue Fact
```

예시:

```json
{
  "question": "철 도끼 지금 만들 수 있어?",
  "dialogue_fact": {
    "craftable": false,
    "missing_items": [
      {
        "display_name": "철 주괴",
        "missing_quantity": 1
      }
    ]
  }
}
```

LLM은 계산하거나 판정하지 않고 다음과 같이 표현만 담당한다.

> 아직은 안 돼. 철 주괴가 하나 부족해.

LLM이 스냅샷 전체를 읽거나 필요한 필드를 직접 고르게 하지 않는다.

---

## 4.3 클라이언트와 서버 코드의 판정 책임

클라이언트는 게임 세계의 최신 상태를 보유하는 최종 진실의 원천이다.

### 클라이언트 책임

- 실제 이동
- MoveTo와 경로 탐색
- 작업 대상 검색
- 채집·수확·운반 실행
- 실제 아이템 증감
- 장비와 도구의 최신 상태
- 위험 감지
- 작업 성공·실패
- 스토리 진행 상태
- 카메라 라인트레이스와 플레이어 핑
- 작업 상태 UI
- 툴 실행 직전 최종 검증

### AI 서버 코드 책임

AI 서버 코드는 클라이언트가 보낸 요청 시점 스냅샷을 이용해 **질문에 답하기 위한 정보성 판정**을 수행한다.

- 보유 재료와 레시피 요구량 비교
- 부족 재료와 부족 수량 계산
- 레시피 해금 여부 확인
- 사용 가능한 제작 시설 확인
- 발견한 세계관 정보의 접근 범위 판정
- 구조화된 실패 코드 해석
- LLM에 전달할 확정 사실 생성

예를 들어 “지금 철 도끼를 만들 수 있어?”라는 질문에는 서버 코드가 스냅샷 기준으로 답한다.

반면 “철 도끼를 만들어 줘”라는 실행 명령은 클라이언트가 실제 실행 직전에 최신 인벤토리로 다시 검증한다.

```text
정보 질문
→ 서버 코드의 스냅샷 기준 판정
→ LLM은 결과 표현

게임 행동
→ AI 서버가 툴 호출 생성
→ 클라이언트가 최신 상태로 최종 검증
→ 실행 또는 실패 이벤트 반환
```

LLM이 반환한 툴 호출도 클라이언트가 검증한 뒤 실행한다.

---

# 5. AIContextSnapshot 설계

## 5.1 설계 원칙

`AIContextSnapshot`은 게임 전체 상태가 아니다.

> AI 동료의 최소 기능을 처리할 가능성이 있는 데이터만 포함한 AI 전용 통합 스냅샷이다.

스냅샷은 서버로 한 번에 전달하지만, 각 파이프라인의 서버 코드만 필요한 필드를 선택해 사용한다.  
LLM에는 선택된 원본 필드가 아니라 서버 코드가 계산·검증한 결과만 전달한다.

## 5.2 공통 구조

```json
{
  "schema_version": "1.0",
  "snapshot_id": "snap_10482",
  "captured_at": "2026-07-15T13:00:00+09:00",
  "game_time": {
    "day": 12,
    "time": "21:30"
  },

  "player": {},
  "companion": {},
  "interaction": {},
  "location": {},
  "survival": {},
  "inventory": {},
  "crafting": {},
  "progression": {},
  "combat": {},
  "task": {},
  "relationship": {},
  "scene": {}
}
```

---

# 6. 스냅샷 필드 정의

## 6.1 player

플레이어의 현재 행동과 입력 해석에 필요한 최소 정보다.

```json
{
  "player": {
    "player_id": "player_01",
    "current_activity": "exploring",
    "is_in_combat": false,
    "is_in_cutscene": false,
    "current_goal_id": "build_first_shelter"
  }
}
```

### 사용 기능

- 일상 대화
- 게임 진행 질문
- 현재 목표를 참조하는 모호한 명령
- 대사 길이와 긴급도 조절

### 제외 데이터

- 플레이어의 프레임 단위 위치
- 이동 속도
- 애니메이션 상태 전체
- 전체 입력 기록

---

## 6.2 companion

AI 동료의 현재 작업 상태와 대화 가능 여부를 나타낸다.

```json
{
  "companion": {
    "companion_id": "companion_luna",
    "state": "working",
    "current_task_id": "task_883",
    "current_action": "gather_resource",
    "combat_mode": "follow_player",
    "can_receive_command": true
  }
}
```

### 사용 기능

- “지금 뭐 하고 있어?” 질문
- 현재 작업 취소·변경
- 전투 명령 전환과 중지
- 명령 충돌 처리
- 상황에 맞는 대화 생성

### 제외 데이터

- AI 동료 좌표
- 네비게이션 경로
- 행동 트리 내부 상태
- 애니메이션 세부 상태

---

## 6.3 interaction

“이것”, “저기”, “이 마을” 같은 지시 대상을 해석하기 위한 참조 정보다.

```json
{
  "interaction": {
    "ping_target": {
      "entity_ref": "ore_iron_104",
      "entity_type": "resource_node",
      "display_name": "철광석"
    },
    "selected_target": null,
    "camera_trace_target": {
      "entity_ref": "mine_entrance_01",
      "entity_type": "world_object",
      "display_name": "무너진 광산 입구"
    }
  }
}
```

### 대상 결정 우선순위

1. 사용자가 발화에서 직접 언급한 대상
2. 플레이어 핑 대상
3. 현재 선택 대상
4. 카메라 라인트레이스 대상
5. 현재 지역
6. 최근 대화 대상

좌표 대신 `entity_ref`를 사용한다.  
실제 좌표와 이동 처리는 클라이언트가 담당한다.

---

## 6.4 location

현재 지역과 세계관 질문에 필요한 정보다.

```json
{
  "location": {
    "current_region_id": "abandoned_mining_town",
    "current_region_name": "버려진 광산 마을",
    "sub_area_id": "mine_square",
    "is_base": false
  }
}
```

### 사용 기능

- 세계관 질문
- 상황 기반 일상 대화
- 지역 첫 발견 반응
- “여기는 어디야?” 질문

---

## 6.5 survival

생존 상태에 대한 사용자 질문과 상황 대화에 필요한 요약값이다.

```json
{
  "survival": {
    "health_state": "normal",
    "hunger_state": "low",
    "thirst_state": "normal",
    "temperature_state": "cold",
    "status_effect_ids": []
  }
}
```

연속 수치 전체가 아니라 의미 있는 구간 값만 전달하는 것을 기본으로 한다.

### 사용 기능

- “지금 상태 괜찮아?”
- 생존 관련 게임 도움 질문
- 상황에 맞는 대화

### 주의

긴급 위험 감지는 이 스냅샷을 읽어 판단하지 않는다.  
클라이언트가 위험을 감지한 뒤 별도의 `GAME_TRIGGER`를 보낸다.

---

## 6.6 inventory

레시피와 제작 가능 여부 질문을 **서버 코드가 판정**하기 위해 사용하는 아이템 ID와 수량이다.

이 데이터는 기본적으로 LLM 컨텍스트에 직접 넣지 않는다.

```json
{
  "inventory": {
    "revision": 184,
    "items": [
      {
        "item_id": "wood",
        "quantity": 8
      },
      {
        "item_id": "iron_ingot",
        "quantity": 2
      }
    ],
    "free_slot_count": 4
  }
}
```

`display_name`은 게임 데이터 DB에서 조회할 수 있으므로 스냅샷에서 생략할 수 있다.  
`revision`은 스냅샷이 어느 인벤토리 상태를 기준으로 만들어졌는지 식별하는 데 사용한다.

### 서버 코드 사용 기능

- “지금 만들 수 있어?”
- “무엇이 부족해?”
- “내가 철 주괴를 가지고 있어?”
- 현재 재료를 기준으로 한 게임 도움
- 목표 제작물에 필요한 부족 수량 계산

### 처리 원칙

```text
Recipe DB
+ Snapshot Inventory
+ Station State
+ Unlock State
→ Recipe Evaluation Code
→ Dialogue Fact
→ LLM 대사 표현
```

예시 코드 판정 결과:

```json
{
  "evaluation_type": "craftability",
  "snapshot_revision": 184,
  "craftable": false,
  "missing_items": [
    {
      "item_id": "iron_ingot",
      "display_name": "철 주괴",
      "required": 3,
      "owned": 2,
      "missing": 1
    }
  ]
}
```

LLM에는 전체 인벤토리가 아니라 다음처럼 확정 결과만 전달한다.

```json
{
  "craftable": false,
  "missing_items": [
    {
      "display_name": "철 주괴",
      "missing": 1
    }
  ]
}
```

### 최신성 원칙

- 정보 질문은 사용자 요청 시점의 스냅샷 기준으로 서버 코드가 판정한다.
- 실제 제작·소비 명령은 클라이언트가 실행 직전에 최신 인벤토리로 재검증한다.
- 스냅샷이 누락되었거나 지나치게 오래된 경우에만 최신 인벤토리 슬라이스를 클라이언트에 재요청한다.

---

## 6.7 crafting

현재 사용 가능한 제작 시설과 레시피 접근 범위다.

```json
{
  "crafting": {
    "nearby_station_types": [
      "workbench_level_2",
      "furnace"
    ],
    "opened_crafting_menu": "workbench_level_2",
    "focused_recipe_id": null
  }
}
```

### 사용 기능

- 레시피 질문
- 제작 장소 안내
- 현재 제작 가능 여부 판정
- “이 작업대에서 뭘 만들 수 있어?” 질문

레시피 원본과 전체 제작 트리는 AI 서버의 구조화된 DB에 저장한다.

---

## 6.8 progression

스토리와 발견 범위를 제한하기 위한 정보다.

```json
{
  "progression": {
    "story_stage": 3,
    "story_flags": [
      "entered_mining_town",
      "read_warning_notice"
    ],
    "unlocked_recipe_ids": [
      "stone_axe",
      "furnace",
      "iron_ingot"
    ],
    "discovered_item_ids": [
      "wood",
      "iron_ore",
      "iron_ingot"
    ],
    "discovered_lore_ids": [
      "miner_diary_01",
      "warning_notice_02"
    ],
    "visited_region_ids": [
      "forest",
      "abandoned_mining_town"
    ]
  }
}
```

### 사용 기능

- 레시피 공개 범위 제한
- 세계관 스포일러 방지
- 플레이어가 발견한 정보만 설명
- 현재 진행 단계에 맞는 게임 도움

---

## 6.9 combat

전투 명령의 자연어 해석과 현재 전투 모드 확인에 필요한 최소 정보다.

```json
{
  "combat": {
    "is_in_combat": true,
    "companion_combat_mode": "defend_player",
    "active_target_ref": "enemy_wolf_03",
    "active_target_name": "늑대",
    "hostile_count_band": "few",
    "retreat_available": true
  }
}
```

### 사용 기능

- “저 늑대 공격해.”
- “나를 지켜.”
- “거기서 버텨.”
- “뒤로 물러나.”
- “공격 멈춰.”
- “가까운 적부터 상대해.”

### 포함 원칙

전투 스냅샷에는 전투 명령 해석에 필요한 요약값만 넣는다.

포함 가능:

- 현재 전투 여부
- AI 동료의 현재 전투 모드
- 현재 공격 대상 참조
- 대략적인 적 수 구간
- 후퇴 명령 사용 가능 여부

제외:

- 모든 적의 좌표
- 모든 적의 체력과 스탯
- 공격 경로
- 스킬 쿨다운 전체
- 명중률 계산
- 피해량 계산
- 전투 AI 내부 상태

실제 타깃 선택, 사거리, 경로, 공격 가능 여부, 스킬 사용 가능 여부는 클라이언트가 판정한다.

---

## 6.10 task

현재 AI 동료가 받은 명령과 진행 상태다.

```json
{
  "task": {
    "task_id": "task_883",
    "original_request": "나무 20개 모아 줘",
    "action": "gather_resource",
    "target_id": "resource_wood",
    "requested_quantity": 20,
    "state": "RUNNING",
    "progress_current": 8,
    "progress_target": 20,
    "last_failure_code": null
  }
}
```

### 사용 기능

- “지금 뭐 하고 있어?”
- 작업 취소·변경
- 작업 결과 대화
- 작업 상태 UI

작업 진행은 클라이언트가 관리한다.  
AI 서버는 필요할 때 상태를 말로 표현한다.

---

## 6.11 relationship

AI 동료와의 유대감을 대화에 반영하기 위한 최소 관계 정보다.

```json
{
  "relationship": {
    "companion_name": "루나",
    "player_preferred_name": "대장",
    "relationship_stage": "trusted",
    "current_emotion_tag": "calm",
    "recent_memory_refs": [
      "mem_first_base",
      "mem_first_boss"
    ]
  }
}
```

### 사용 기능

- 일상 대화
- 주요 사건 반응
- 기억 회상
- 플레이어 호칭
- 관계 단계에 따른 말투 변화

### 주의

전체 장기 기억 원문을 스냅샷에 넣지 않는다.

`recent_memory_refs`를 바탕으로 AI 서버의 기억 저장소에서 현재 요청과 관련된 기억만 최대 1~2개 조회한다.

---

## 6.12 scene

현재 장면의 대화 분위기를 결정하는 간단한 태그다.

```json
{
  "scene": {
    "scene_tag": "returning_home",
    "weather_tag": "snow",
    "time_tag": "night",
    "recent_event_code": "snowfield_discovered",
    "dialogue_allowed": true,
    "dialogue_length": "short"
  }
}
```

### 사용 기능

- 일상 대화
- 자발적 사건 반응
- 귀환·휴식·탐험 대사
- 대사 길이 제한

---

# 7. 기능별 스냅샷 사용 데이터

아래의 “서버 코드 사용 필드”는 파이프라인 내부 코드가 읽는 데이터다.  
LLM에는 이 필드의 원본 전체가 아니라 코드가 정리한 결과만 전달한다.

| 기능 | 서버 코드 사용 필드 | LLM에 전달하는 정보 |
|---|---|---|
| 따라오기·대기 | 원칙적으로 없음 | 수락 대사에 필요한 행동 종류 |
| “저기 가” 등 지시 명령 | `interaction` | 확정된 행동과 대상 이름 |
| 주변 사건 반응 | `scene`, `relationship` | 사건 요약, 관계 단계 |
| 일상 대화 | `player`, `companion`, `location`, `relationship`, `scene` | 장면 태그, 관련 기억 최대 1개 |
| 기본 레시피 설명 | `interaction`, `progression` | DB에서 조회한 레시피 사실 |
| 제작 가능 여부 | `interaction`, `inventory`, `crafting`, `progression` | `craftable`, 부족 재료, 부족 수량 |
| 세계관 설명 | `interaction`, `location`, `progression`, `relationship` | 진행도 필터를 통과한 근거 |
| 채집·수확·운반 | `interaction`, `companion`, `task` | 확정된 행동·대상·수량 |
| 전투 중 간단한 지시 | `interaction`, `combat`, `companion` | 확정된 전투 행동·대상 선택 방식 |
| 작업 상태 질문 | `task`, `companion` | 현재 상태와 진행 결과 |
| 게임 도움 질문 | `player`, `survival`, `inventory`, `crafting`, `progression` | 서버 코드가 계산한 도움 사실 |
| 위험 질문 | `survival`, `scene` | 상태 요약 또는 경고 사실 |
| 기억 회상 | `relationship`, `location`, `scene` | 관련 기억 최대 1~2개 |

### 데이터 흐름 예시

```text
inventory.items
→ 서버 코드가 레시피와 비교
→ missing iron_ingot = 1
→ LLM에는 “철 주괴 1개 부족”만 전달
```

---

# 8. 단순 명령 파이프라인

## 8.1 처리 흐름

```text
사용자 발화
→ Router: COMMAND
→ Command Parser
→ Entity Resolver
→ Tool Schema Validator
→ 클라이언트 툴 호출
→ 클라이언트 성공·실패 판정
```

## 8.2 최소 명령 구조

```json
{
  "action": "gather_resource",
  "raw_target": "나무",
  "quantity_mode": "exact",
  "quantity": 20,
  "target_ref": null
}
```

## 8.3 최종 툴 호출

```json
{
  "tool": "gather_resource",
  "target_id": "resource_wood",
  "quantity_mode": "exact",
  "quantity": 20
}
```

라우터가 툴 이름·대상·수량을 결정하지 않는다.  
`COMMAND` 파이프라인이 사용자 원문에서 이를 추출한다.

---


# 9. 재질의(Human-in-the-Loop) 설계

## 9.1 적용 여부

현재 설계에는 재질의 과정이 필요하다.

다음 요청은 LLM이나 파이프라인이 임의로 추측해 실행하면 안 된다.

- 대상이 여러 개로 해석되는 명령
- 툴 호출에 필요한 필수 슬롯이 빠진 명령
- 게임 엔티티 매핑 확신도가 낮은 명령
- 카메라 대상, 핑 대상, 발화 대상이 서로 충돌하는 경우
- STT 신뢰도가 낮아 행동 의미가 달라질 수 있는 경우
- 파괴적이거나 되돌릴 수 없는 행동
- 후속 질문의 대상이 이전 대화만으로 확정되지 않는 경우

예시:

> 돌 좀 가져와.

가능한 해석:

- 돌 원석 채집
- 돌 블록 운반
- 부싯돌 수집

결과가 달라질 수 있으므로 임의 실행하지 않고 필요한 정보만 다시 묻는다.

---

## 9.2 현재 설계에 맞는 처리 방식

서버가 파이프라인 실행을 열린 상태로 유지하면서 플레이어 응답을 기다리게 하지 않는다.

대신 현재 요청을 종료하기 전에 재개 정보를 저장한다.

```text
사용자 요청
→ LLM Router
→ 기능 파이프라인
→ 필수 슬롯·엔티티·권한 검증
→ 정보 부족 또는 확인 필요
→ PendingClarification 저장
→ 재질의 대사 반환
→ 현재 요청 종료
```

플레이어의 다음 응답은 새로운 `USER_REQUEST`로 들어온다.

```text
플레이어 응답
→ Request Gateway
→ LLM Router
→ CLARIFICATION_RESPONSE
→ PendingClarification 조회
→ 기존 슬롯과 새 응답 병합
→ 같은 기능 파이프라인의 검증 단계부터 재개
```

이 방식은 다음 원칙을 모두 유지한다.

- 모든 사용자 요청은 LLM 라우터를 통과한다.
- 라우터는 라우팅만 담당한다.
- 슬롯 검증과 재질의 판단은 각 파이프라인이 담당한다.
- 긴 서버 요청이나 열린 LLM 세션을 유지하지 않는다.
- 다음 응답 시점의 최신 스냅샷으로 실행 가능 여부를 다시 검증한다.

---

## 9.3 재질의 판단 책임

라우터는 `COMMAND`, `RECIPE`, `LORE` 같은 파이프라인만 결정한다.

재질의 필요 여부는 각 파이프라인의 서버 코드가 판단한다.

### COMMAND 파이프라인

- 툴 호출에 필요한 대상이 있는가
- 대상 엔티티가 하나로 확정되는가
- 수량이나 목적지가 필수인 행동인가
- 파괴적 행동에 추가 확인이 필요한가
- 현재 명령과 새 명령의 관계가 명확한가

### RECIPE 파이프라인

- 질문 대상 레시피가 확정되는가
- “지금 만들 수 있어?”가 가리키는 아이템이 존재하는가
- 같은 별칭을 쓰는 아이템 후보가 여러 개인가

### LORE 파이프라인

- “이곳”, “저 사람”, “그 사건”의 대상이 확정되는가
- 현재 지역과 카메라 대상 중 질문 대상을 고를 수 있는가
- 플레이어가 원하는 설명 범위가 명확한가

### GAME_HELP 파이프라인

- 질문 대상 시스템이나 목표가 확정되는가
- 플레이어가 힌트를 원하는지 구체적인 해결법을 원하는지 구분되는가

---

## 9.4 재질의 발생 조건

다음 조건 중 하나가 참이면 `PENDING_CLARIFICATION` 상태를 만든다.

```text
required_slot_missing == true
entity_resolution_confidence < threshold
multiple_valid_candidates == true
destructive_action_confirmation_required == true
stt_confidence < threshold
reference_target_conflict == true
```

### 적용 원칙

- 결과를 크게 바꾸는 정보만 다시 묻는다.
- 한 번에 하나의 핵심 질문만 한다.
- 이미 확정된 정보를 다시 묻지 않는다.
- 후보가 있다면 2~4개 선택지로 좁혀 제시한다.
- 위험하지 않은 사소한 누락값은 게임의 기본 정책으로 처리할 수 있다.
- 단순 명령마다 과도하게 확인하지 않는다.

---

## 9.5 PendingClarification 데이터 구조

```json
{
  "clarification_id": "clarify_382",
  "session_id": "session_01",
  "companion_id": "companion_luna",
  "pipeline": "COMMAND",

  "original_request": "돌 좀 가져와",
  "route_keywords": ["돌", "가져오기"],

  "resolved_slots": {
    "action": "gather_or_fetch"
  },

  "missing_slots": [
    "target_id"
  ],

  "candidate_options": [
    {
      "candidate_id": "resource_stone",
      "display_name": "돌 원석"
    },
    {
      "candidate_id": "item_stone_block",
      "display_name": "돌 블록"
    },
    {
      "candidate_id": "resource_flint",
      "display_name": "부싯돌"
    }
  ],

  "expected_response_type": "candidate_selection",
  "clarification_count": 1,
  "created_at": "2026-07-15T15:00:00+09:00",
  "expires_at": "2026-07-15T15:05:00+09:00",
  "status": "PENDING"
}
```

저장 대상:

- 원래 요청
- 선택된 파이프라인
- 이미 확정된 슬롯
- 아직 필요한 슬롯
- 후보 목록
- 기대하는 응답 유형
- 재질의 횟수와 만료 시각

전체 `AIContextSnapshot`은 저장하지 않는 것을 기본으로 한다.  
게임 상태는 다음 사용자 응답에 포함된 최신 스냅샷을 사용한다.

---

## 9.6 재질의 응답과 파이프라인 재개

플레이어:

> 돌 원석 말한 거야.

라우터 출력:

```json
{
  "route": "CLARIFICATION_RESPONSE",
  "keywords": ["돌 원석"],
  "confidence": 0.99
}
```

서버는 `clarification_id`로 기존 상태를 찾은 뒤 새 응답을 병합한다.

```json
{
  "pipeline": "COMMAND",
  "original_request": "돌 좀 가져와",
  "resolved_slots": {
    "action": "gather_resource",
    "target_id": "resource_stone"
  },
  "missing_slots": []
}
```

필수 슬롯이 모두 채워졌다면 동일한 COMMAND 파이프라인의 검증 단계부터 재개한다.

```json
{
  "tool": "gather_resource",
  "target_id": "resource_stone",
  "quantity_mode": "some"
}
```

실행 직전에는 최신 스냅샷과 클라이언트 상태로 다시 검증한다.

---

## 9.7 재질의 대사 생성

재질의 대사는 다음 정보만 사용한다.

- 부족한 슬롯 종류
- 후보의 표시 이름
- 행동 결과에 영향을 주는 짧은 설명
- 캐릭터와 관계 단계

LLM에 전체 스냅샷을 전달하지 않는다.

```json
{
  "clarification_type": "target_selection",
  "candidates": [
    "돌 원석",
    "돌 블록",
    "부싯돌"
  ],
  "relationship_stage": "trusted"
}
```

대사 예시:

> 돌 원석을 말하는 거야, 아니면 돌 블록이나 부싯돌을 말하는 거야?

자주 발생하는 재질의는 캐릭터별 사전 생성 대사 팩을 사용할 수 있다.

```text
대상 불명확
수량 불명확
목적지 불명확
후속 질문 대상 불명확
파괴 행동 확인
```

후보 이름처럼 동적인 부분만 슬롯으로 삽입한다.

---

## 9.8 파괴적 행동 확인

다음 행동은 해석이 명확하더라도 실행 전에 플레이어 확인을 받는다.

- 건축물 철거
- 희귀 아이템 폐기 또는 소비
- 저장 물자 대량 사용
- 되돌릴 수 없는 선택
- 다른 보관함의 중요 물품 이동

```json
{
  "clarification_type": "destructive_confirmation",
  "action": "demolish_structure",
  "target_ref": "wall_104",
  "consequence_facts": [
    "연결된 보관함이 바닥에 떨어질 수 있음",
    "복구에 목재 12개가 필요함"
  ],
  "expected_response_type": "yes_no"
}
```

NPC:

> 이 벽을 철거하면 붙어 있는 보관함도 떨어질 수 있어. 정말 철거할까?

플레이어가 동의해도 클라이언트가 실행 직전에 다시 검증한다.

---

## 9.9 취소·주제 전환·만료

플레이어는 재질의 도중 다음과 같이 응답할 수 있다.

- “됐어.”
- “그냥 따라와.”
- “나중에 하자.”

처리 규칙:

```text
명시적 취소
→ PendingClarification 상태를 CANCELLED로 변경

새로운 독립 요청
→ 기존 재질의를 CANCELLED 또는 SUSPENDED 처리
→ 새 요청을 일반 파이프라인으로 전달

응답 시간 초과
→ PendingClarification 상태를 EXPIRED로 변경
```

MVP에서는 동료 하나당 활성 재질의를 하나만 유지하고, 명확한 새 명령이 들어오면 기존 재질의를 취소하는 방식이 단순하다.

---

## 9.10 작업 상태 UI 연동

재질의 상태도 현재 명령 UI에 표시한다.

```text
현재 요청: 돌 좀 가져와
상태: 추가 정보 필요
질문: 어떤 종류의 돌을 말하나요?
선택지: 돌 원석 / 돌 블록 / 부싯돌
```

추가 상태:

```text
NEEDS_CLARIFICATION
WAITING_FOR_PLAYER
RESUMING
EXPIRED
```

UI에는 LLM 내부 추론이 아니라 실제 서버 상태만 표시한다.

---

## 9.11 재질의 반복 제한

재질의가 반복되면 대화 흐름이 크게 나빠진다.

MVP 권장값:

- 한 요청에서 최대 재질의 2회
- 두 번째 재질의 후에도 확정할 수 없으면 실행하지 않음
- 가능한 경우 텍스트 질문과 함께 UI 선택지를 제공
- 실패 이유와 대상을 지정하는 방법을 짧게 안내

예시:

> 아직 어떤 대상을 말하는지 확실하지 않아. 직접 가리키거나 이름을 말해 줘.

---

## 9.12 처음부터 다시 실행하지 않는 이유

재질의 응답마다 원래 요청을 버리고 전체 파이프라인을 처음부터 실행하면 다음 문제가 생긴다.

- 원래 요청의 행동과 목적을 잃을 수 있음
- 이미 해석한 슬롯을 다시 계산함
- “그거”, “두 개” 같은 짧은 응답을 독립 요청으로 오해함
- 불필요한 LLM 호출이 늘어남

따라서 다음 방식을 사용한다.

```text
원래 요청과 확정 슬롯 저장
→ 사용자 응답으로 부족 슬롯만 보완
→ 같은 파이프라인의 검증 단계부터 재개
```

단, 게임 상태는 바뀔 수 있으므로 실제 판정에는 재질의 응답 시점의 최신 스냅샷을 사용한다.

---

# 10. 전투 중 간단한 지시 파이프라인

## 10.1 지원 범위

MVP에서는 전투 중 다음과 같은 짧고 즉시 이해 가능한 명령만 지원한다.

```text
ATTACK_TARGET
ATTACK_NEAREST
DEFEND_PLAYER
HOLD_POSITION
FALL_BACK
STOP_ATTACK
RETURN_TO_PLAYER
```

플레이어 발화 예시:

- “저 늑대 공격해.”
- “가까운 적부터 잡아.”
- “나를 지켜.”
- “거기서 버텨.”
- “뒤로 물러나.”
- “공격 멈춰.”
- “내 쪽으로 와.”

지원하지 않는 범위:

- 여러 단계를 순서대로 수행하는 전술
- 적별 세부 스킬 로테이션
- 지형을 이용한 복잡한 포위
- 장시간 자율 전투 계획
- 플레이어 대신 전체 전투 지휘
- 자유 문장으로 생성된 임의 전투 스킬

---

## 10.2 처리 흐름

```text
사용자 전투 명령
→ LLM Router: COMMAND
→ Command Pipeline이 combat_command로 분류
→ 전투 행동과 원시 대상 표현 추출
→ Entity Resolver 또는 Target Selector 해석
→ Combat Tool Schema 검증
→ 클라이언트 툴 호출
→ 클라이언트가 최신 전투 상태로 최종 판정
```

라우터는 `COMMAND`만 결정한다.  
`ATTACK_TARGET`, `DEFEND_PLAYER` 같은 세부 행동은 COMMAND 파이프라인이 추출한다.

---

## 10.3 명령 출력 구조

### 명시적 대상 공격

플레이어:

> 저 늑대 공격해.

파이프라인 출력:

```json
{
  "command_type": "combat",
  "action": "attack_target",
  "raw_target": "저 늑대",
  "target_ref": "enemy_wolf_03"
}
```

최종 툴 호출:

```json
{
  "tool": "combat_attack_target",
  "target_ref": "enemy_wolf_03"
}
```

### 대상 선택 규칙을 포함한 공격

플레이어:

> 가까운 적부터 상대해.

```json
{
  "command_type": "combat",
  "action": "attack_by_selector",
  "target_selector": "nearest_hostile"
}
```

LLM이 적 목록을 보고 직접 대상을 선택하지 않는다.  
클라이언트가 `nearest_hostile` 정책으로 실제 대상을 결정한다.

### 플레이어 방어

플레이어:

> 나를 지켜.

```json
{
  "tool": "combat_set_mode",
  "mode": "defend_player"
}
```

### 후퇴

플레이어:

> 뒤로 물러나.

```json
{
  "tool": "combat_fall_back",
  "destination_policy": "toward_player_safe_side"
}
```

실제 안전 지점과 경로는 클라이언트가 결정한다.

---

## 10.4 전투 대상 결정 우선순위

명시적인 공격 대상이 필요한 명령은 다음 순서로 해석한다.

1. 발화에서 직접 언급한 엔티티
2. 플레이어 핑 대상
3. 현재 선택 대상
4. 카메라 라인트레이스 대상
5. 명시된 대상 선택 규칙
6. 현재 AI 동료 공격 대상

예시:

> 저 녀석 공격해.

```text
핑 대상이 적임
→ 핑 대상을 사용

핑 없음 + 카메라 대상이 적임
→ 카메라 대상을 사용

후보가 여러 개이고 확정 불가
→ 재질의 또는 핑 요청
```

전투 중에는 긴 재질의를 피해야 하므로, 가능한 경우 UI의 핑과 타깃 표시를 우선 사용한다.

---

## 10.5 재질의 규칙

전투 명령도 오해 시 결과가 크게 달라지는 경우에는 재질의한다.

재질의가 필요한 예:

- 아군과 적이 함께 있는 방향을 “저쪽 공격해”라고 지시
- 같은 이름의 적이 여러 개이며 특정 대상 공격이 중요함
- STT 오류로 “공격해”와 “공격하지 마”가 불확실함
- 대상이 적인지 상호작용 오브젝트인지 불명확함

재질의 대사:

> 어느 적을 말하는 거야? 직접 가리켜 줘.

그러나 다음 명령은 대상이 없어도 즉시 실행할 수 있다.

- 나를 지켜
- 공격 멈춰
- 내 쪽으로 와
- 뒤로 물러나
- 거기서 버텨

### 전투 중 재질의 제한

- 선택지가 복잡하면 음성 재질의보다 타깃 핑 UI를 안내한다.
- 한 번의 재질의로 확정되지 않으면 공격 명령을 실행하지 않는다.
- `STOP_ATTACK`, `FALL_BACK`처럼 안전을 높이는 명령은 보수적으로 즉시 실행한다.

---

## 10.6 클라이언트 최종 검증

AI 서버는 전투 행동 의도만 반환한다.

클라이언트는 다음을 최종 판정한다.

- 대상이 실제 적인지
- 대상이 살아 있는지
- 공격 가능 거리인지
- 경로가 존재하는지
- AI 동료가 행동 가능한 상태인지
- 현재 행동을 중단할 수 있는지
- 해당 전투 행동이 게임 규칙상 허용되는지
- 아군 피해 위험이 있는지

검증 실패 시 클라이언트가 이벤트를 반환한다.

```json
{
  "origin": "GAME_TRIGGER",
  "trigger_type": "TASK_FAILED",
  "payload": {
    "task_id": "combat_task_31",
    "action": "attack_target",
    "target_ref": "enemy_wolf_03",
    "failure_code": "TARGET_UNREACHABLE"
  }
}
```

AI 서버는 확정된 실패 사실만 바탕으로 대사를 생성한다.

> 저쪽으로는 갈 수 없어. 다른 길을 찾아야 해.

---

## 10.7 툴 호출과 대사 순서

전투 명령은 반응 속도가 중요하므로 툴 호출을 먼저 전달한다.

```text
Combat Tool 검증
→ 클라이언트에 즉시 전송
→ 사전 생성된 짧은 수락 대사 출력
```

예시:

```json
{
  "action": {
    "tool": "combat_set_mode",
    "mode": "defend_player"
  },
  "dialogue_ref": "combat_defend_accept:02"
}
```

대사 예시:

- “알겠어. 네 곁을 지킬게.”
- “응, 가까이 붙어 있을게.”
- “내가 막아 볼게.”

작업 성공이나 적 처치 여부는 클라이언트 결과가 확정된 뒤에만 말한다.

---

## 10.8 전투 대사 사전 생성

전투 중에는 긴 실시간 생성보다 짧은 사전 생성 대사 팩을 우선한다.

대사 팩 기준:

```text
combat_action
combat_result
persona_version
relationship_stage
severity
```

필요한 대사 유형:

```text
ATTACK_ACCEPT
DEFEND_ACCEPT
HOLD_ACCEPT
FALL_BACK_ACCEPT
STOP_ATTACK_ACCEPT
TARGET_UNREACHABLE
ACTION_BLOCKED
PLAYER_IN_DANGER
COMBAT_WON
COMBAT_LOST
```

위험 경고와 마찬가지로 전투 대사는 기본 1문장으로 제한한다.

---

## 10.9 작업 상태 UI

전투 명령도 일반 작업과 동일한 UI에서 확인할 수 있다.

```text
현재 명령: 플레이어 방어
상태: 전투 중
현재 대상: 늑대
전투 모드: 플레이어 주변 방어
```

내부 예시:

```json
{
  "task_id": "combat_task_31",
  "original_request": "나를 지켜",
  "normalized_action": "defend_player",
  "state": "RUNNING",
  "active_target_ref": "enemy_wolf_03"
}
```

---

# 11. 게임 엔티티 매핑

LLM이 게임 내부 ID를 자유롭게 생성하게 하지 않는다.

## 11.1 처리 순서

```text
사용자 표현 추출
→ 문자열 정규화
→ 별칭 사전 조회
→ 퍼지 매칭
→ 현재 파이프라인 문맥으로 후보 정렬
→ 확신이 낮으면 확인 질문
```

## 11.2 별칭 사전 예시

```json
{
  "resource_wood": [
    "나무",
    "목재",
    "통나무",
    "장작",
    "wood",
    "log"
  ],
  "resource_stone": [
    "돌",
    "석재",
    "바위",
    "stone"
  ]
}
```

## 11.3 잘못된 LLM 출력 방지

LLM 출력:

```json
{
  "raw_target": "튼튼한 숲 나무"
}
```

Entity Resolver 결과:

```json
{
  "status": "resolved",
  "target_id": "resource_wood",
  "confidence": 0.86
}
```

후보가 여러 개거나 확신이 낮으면 툴을 실행하지 않는다.

```json
{
  "status": "needs_clarification",
  "candidates": [
    "resource_stone",
    "item_stone_block"
  ]
}
```

---

# 12. 수량 표현 처리

수량은 숫자만 추출하는 것이 아니라 의미 유형을 함께 분류한다.

## 정확한 수량

> 나무 20개 모아 줘.

```json
{
  "quantity_mode": "exact",
  "quantity": 20
}
```

## 모호한 수량

> 나무 좀 모아 줘.

```json
{
  "quantity_mode": "some",
  "quantity": null
}
```

## 목표 기반 수량

> 집 지을 만큼 나무 모아 줘.

```json
{
  "quantity_mode": "goal_required",
  "goal_ref": "current_build_goal",
  "quantity": null
}
```

## 조건 기반 수량

> 가방 가득 나무 채워 와.

```json
{
  "quantity_mode": "until_inventory_full",
  "quantity": null
}
```

실제 수량은 클라이언트 게임 규칙이 계산한다.

---

# 13. 레시피 파이프라인

## 13.1 기본 레시피 질문

```text
“철 도끼 어떻게 만들어?”
→ RECIPE 라우팅
→ 아이템 엔티티 매핑
→ Recipe DB 조회
→ 발견·해금 범위 검증
→ Dialogue Fact 생성
→ 대사 생성
```

기본 제작법 질문에는 인벤토리가 필요하지 않다.

서버 코드가 Recipe DB에서 구조화된 사실을 조회한다.

```json
{
  "recipe_fact": {
    "item_id": "iron_axe",
    "item_name": "철 도끼",
    "ingredients": [
      {
        "item_id": "iron_ingot",
        "name": "철 주괴",
        "count": 3
      },
      {
        "item_id": "wood",
        "name": "목재",
        "count": 2
      }
    ],
    "station_id": "workbench_level_2",
    "station_name": "2단계 작업대"
  }
}
```

LLM에는 이 확정 사실과 사용자 질문만 전달한다.

```json
{
  "question": "철 도끼 어떻게 만들어?",
  "dialogue_fact": {
    "item_name": "철 도끼",
    "ingredients": [
      ["철 주괴", 3],
      ["목재", 2]
    ],
    "station_name": "2단계 작업대"
  }
}
```

## 13.2 제작 가능 여부 질문

> 철 도끼 지금 만들 수 있어?

이 질문에서는 인벤토리·제작 시설·해금 상태가 필요하지만, LLM이 해당 원본을 직접 읽어 판정하지 않는다.

```text
사용자 질문
→ RECIPE 라우팅
→ 제작 대상 해석
→ Recipe DB 조회
→ Snapshot에서 inventory/crafting/progression 추출
→ Recipe Evaluation Code 실행
→ Dialogue Fact 생성
→ LLM이 짧은 대사로 표현
```

서버 코드 입력:

```json
{
  "recipe": {
    "item_id": "iron_axe",
    "ingredients": {
      "iron_ingot": 3,
      "wood": 2
    },
    "required_station": "workbench_level_2"
  },
  "inventory": {
    "revision": 184,
    "items": {
      "iron_ingot": 2,
      "wood": 8
    }
  },
  "crafting": {
    "nearby_station_types": [
      "workbench_level_2"
    ]
  },
  "progression": {
    "unlocked_recipe_ids": [
      "iron_axe"
    ]
  }
}
```

서버 코드 판정 결과:

```json
{
  "evaluation_type": "craftability",
  "snapshot_revision": 184,
  "craftable": false,
  "recipe_unlocked": true,
  "station_available": true,
  "missing_items": [
    {
      "item_id": "iron_ingot",
      "display_name": "철 주괴",
      "required": 3,
      "owned": 2,
      "missing": 1
    }
  ]
}
```

LLM 입력:

```json
{
  "question": "철 도끼 지금 만들 수 있어?",
  "dialogue_fact": {
    "craftable": false,
    "missing_items": [
      {
        "display_name": "철 주괴",
        "missing": 1
      }
    ]
  }
}
```

LLM 출력:

> 아직은 안 돼. 철 주괴가 하나 부족해.

## 13.3 실제 제작 명령

> 철 도끼 만들어 줘.

정보 질문과 달리 실제 제작은 아이템을 소비하므로 클라이언트가 최종 판정한다.

```text
AI 서버
→ craft_item 툴 호출 생성

클라이언트
→ 최신 인벤토리 확인
→ 레시피 해금 확인
→ 제작 시설 확인
→ 가능하면 실행
→ 불가능하면 TASK_FAILED 이벤트 반환
```

AI 서버의 스냅샷 기반 판정 결과는 안내 또는 선행 검증에 사용할 수 있지만 실행 권한을 갖지 않는다.

## 13.4 클라이언트 재요청 기준

기본적으로 사용자 요청 시 함께 받은 스냅샷으로 서버 코드가 판정한다.  
다음 상황에서만 최신 데이터 슬라이스를 클라이언트에 다시 요청한다.

- `inventory` 필드가 누락됨
- 스냅샷의 유효 시간이 초과됨
- 여러 플레이어 또는 시스템이 동시에 인벤토리를 수정함
- 원격 창고·제작대 내부 재료처럼 스냅샷 범위 밖의 데이터가 필요함
- 플레이어가 질문한 뒤 실제 실행까지 긴 시간이 지남

MVP에서는 추가 왕복을 줄이기 위해 요청 시점 스냅샷을 기본으로 사용하고, 실제 실행 단계에서만 클라이언트가 재검증한다.

---

# 14. 세계관 파이프라인

## 14.1 질문 대상 결정

> 이 마을은 왜 버려진 거야?

다음 순서로 질문 대상을 결정한다.

1. 발화에 명시된 엔티티
2. 플레이어 핑
3. 현재 선택 대상
4. 카메라 라인트레이스 대상
5. 현재 지역
6. 최근 대화 대상

## 14.2 스토리 진행에 따른 검색 범위

세계관 DB를 진행할 때마다 물리적으로 확장하지 않는다.  
전체 세계관 데이터에 접근 조건을 지정하고, 검색 전에 코드로 필터링한다.

```json
{
  "lore_id": "mine_disaster_true_cause",
  "unlock_stage": 5,
  "required_flags": [
    "read_chief_miner_diary",
    "entered_lower_mine"
  ],
  "spoiler_level": 3
}
```

플레이어 상태:

```json
{
  "story_stage": 3,
  "story_flags": [
    "entered_mining_town",
    "read_warning_notice"
  ]
}
```

검색 필터:

```text
unlock_stage <= story_stage
required_flags 충족
spoiler_level 허용 범위 이내
```

LLM에게 스포일러 방지를 맡기지 않는다.  
검색 단계에서 접근할 수 없는 정보를 제거한다.

---

# 15. 위험 경고 파이프라인

위험은 클라이언트가 감지한다.

```json
{
  "origin": "GAME_TRIGGER",
  "trigger_type": "DANGER_WARNING",
  "payload": {
    "danger_type": "hostile_enemy",
    "clock_direction": 3,
    "vertical_direction": "level",
    "distance_band": "near",
    "severity": "high",
    "enemy_type": "wolf",
    "count": 1
  }
}
```

AI 대사:

> 3시 방향에 늑대가 있어!

## 처리 원칙

- 사용자 요청이 아니므로 라우터를 거치지 않는다.
- 방향 계산은 플레이어 또는 카메라 기준으로 클라이언트가 수행한다.
- 긴급 경고 UI와 기본 음성은 AI 응답을 기다리지 않고 즉시 출력한다.
- AI 서버의 대사는 캐릭터성 강화를 위한 보조 출력이다.

---

# 16. 클라이언트 판정 실패 처리

LLM은 게임 행동 가능 여부를 완전히 알 수 없다.

```text
플레이어 명령
→ AI 서버가 툴 호출 생성
→ 클라이언트가 실행 가능 여부 판정
→ 실패 시 GAME_TRIGGER 전송
→ 실패 대사 생성
```

실패 이벤트:

```json
{
  "origin": "GAME_TRIGGER",
  "trigger_type": "TASK_FAILED",
  "payload": {
    "task_id": "task_192",
    "action": "gather_resource",
    "target_id": "resource_iron_ore",
    "failure_code": "REQUIRED_TOOL_MISSING",
    "required_item_id": "tool_pickaxe"
  }
}
```

권장 실패 코드:

```text
TARGET_NOT_FOUND
TARGET_UNREACHABLE
REQUIRED_TOOL_MISSING
TOOL_BROKEN
INVENTORY_FULL
DANGER_DETECTED
PERMISSION_DENIED
ACTION_NOT_SUPPORTED
TARGET_AMBIGUOUS
TARGET_NOT_HOSTILE
TARGET_DEAD
COMBAT_ACTION_BLOCKED
FRIENDLY_FIRE_RISK
```

---

# 17. 작업 상태 UI

AI 동료가 현재 무엇을 하고 있는지 플레이어가 항상 확인할 수 있어야 한다.

```json
{
  "task_id": "task_192",
  "original_request": "나무 20개 모아 줘",
  "normalized_action": "gather_resource",
  "target_name": "목재",
  "requested_quantity": 20,
  "state": "RUNNING",
  "progress": {
    "current": 8,
    "target": 20
  }
}
```

## 내부 상태

```text
RECEIVED
ROUTING
RESOLVING_TARGET
VALIDATING
NEEDS_CLARIFICATION
WAITING_FOR_PLAYER
RESUMING
QUEUED
RUNNING
BLOCKED
COMPLETED
FAILED
CANCELLED
EXPIRED
```

## 플레이어 표시

```text
현재 명령: 목재 20개 채집
진행 상황: 8 / 20
상태: 채집 중
```

UI는 LLM의 생각을 표시하는 것이 아니라 오케스트레이터와 클라이언트가 관리하는 실제 상태를 표시한다.

---

# 18. 대사 생성 전략

## 18.1 핵심 원칙

AI 동료와의 유대감이 중요하므로 같은 상황에 고정된 한두 문장을 반복하는 방식은 지양한다.

다만 모든 대사를 실시간으로 생성하면 다음 문제가 발생한다.

- 지연 증가
- GPU 자원 경쟁
- 긴급 대사 출력 지연
- 캐릭터 말투 불안정
- 사실과 다른 대사 생성
- 같은 정보를 불필요하게 길게 설명

따라서 다음 방식을 사용한다.

> 고정 템플릿은 안전용 최소 수준으로만 유지하고, 자주 필요한 대사는 캐릭터·관계·상황별로 미리 생성하여 대사 풀로 저장한다.

---

## 18.2 대사 종류별 처리

| 대사 종류 | 처리 방식 |
|---|---|
| 긴급 위험 경고 | 즉시 출력 가능한 최소 안전 대사 보유 |
| 단순 명령 수락 | 사전 생성된 캐릭터별 대사 풀 |
| 전투 명령 수락·중단·후퇴 | 사전 생성된 짧은 전투 대사 풀 |
| 작업 진행 대사 | 명령 시작 시 예상 상황별로 비동기 사전 생성 |
| 작업 성공·부분 성공·실패 | 작업 진행 중 미리 생성하거나 결과 확정 후 짧게 생성 |
| 지역·사건 반응 | 지역과 사건별 대사 풀 사전 생성 |
| 일상 대화 | 실시간 생성 |
| 세계관 답변 | 검색 결과를 기반으로 실시간 생성 |
| 레시피 답변 | 구조화된 사실을 기반으로 실시간 또는 캐시 생성 |
| 기억 회상 | 관련 기억을 조회한 뒤 실시간 생성 |

---

## 18.3 캐릭터별 대사 팩 사전 생성

동료 캐릭터가 생성되거나 관계 단계가 변경될 때 다음 기준으로 대사 팩을 생성한다.

```text
dialogue_intent
event_code
persona_version
relationship_stage
scene_tag
emotion_tag
```

예시 키:

```text
COMMAND_ACCEPT_GATHER
+ persona_luna_v2
+ relationship_trusted
+ scene_exploration
```

생성 결과:

```json
{
  "dialogue_pack_id": "pack_luna_gather_trusted_01",
  "lines": [
    "좋아. 근처부터 살펴볼게.",
    "알겠어. 너무 멀리 가지 않고 모아 올게.",
    "응, 필요한 만큼 챙겨 볼게.",
    "그럼 주변에 쓸 만한 게 있는지 찾아볼게."
  ]
}
```

같은 대사를 반복하지 않도록 최근 사용 이력을 관리한다.

---

## 18.4 작업 시작 시 결과 대사 미리 생성

채집처럼 시간이 걸리는 작업은 명령을 받은 직후 예상 가능한 결과 대사를 비동기로 생성한다.

예시 작업:

```json
{
  "action": "gather_resource",
  "target_name": "목재",
  "quantity": 20
}
```

작업이 진행되는 동안 다음 상황의 대사를 미리 준비한다.

```text
SUCCESS
PARTIAL_SUCCESS
TOOL_BROKEN
INVENTORY_FULL
TARGET_NOT_FOUND
DANGER_DETECTED
PLAYER_CANCELLED
```

예시:

```json
{
  "SUCCESS": [
    "목재 스무 개, 전부 모았어.",
    "말한 만큼 챙겨 왔어. 여기 있어."
  ],
  "TOOL_BROKEN": [
    "열네 개까지 모았는데 도끼가 버티지 못했어.",
    "조금 더 모으려 했는데 도끼가 부러졌어."
  ]
}
```

결과가 확정되면 해당 결과 코드와 일치하는 대사만 사용한다.

이 방식은 다음 장점이 있다.

- 완료 순간 바로 대사 출력 가능
- 작업 결과와 대사 불일치 방지
- 캐릭터성 유지
- 실시간 LLM 호출 감소
- 반복 템플릿 감소

---

## 18.5 실시간 대사가 필요한 경우

다음 경우에는 실시간 LLM 대사를 생성한다.

- 플레이어의 자유로운 일상 대화
- 세계관 질문
- 관련 기억을 활용하는 대화
- 단순 대사 풀로 대응하기 어려운 복합 상황
- 모호한 명령에 대한 확인 질문
- 플레이어가 감정적으로 반응한 사건

실시간 대사 컨텍스트는 다음으로 제한한다.

```text
캐릭터 페르소나 요약
관계 단계
사용자 발화
현재 장면 태그
관련 사건 1개
관련 기억 최대 1개
직전 대화 최대 2턴
```

출력 제한:

```text
기본 1~2문장
요청하지 않은 정보 추가 금지
같은 내용을 반복 설명하지 않기
세계관·레시피 사실 임의 생성 금지
```

---

## 18.6 최소 안전 대사

다음 상황에는 LLM이 실패해도 즉시 출력할 수 있는 최소 대사를 유지한다.

- 긴급 위험
- 명령 접수
- 명령 취소
- 행동 불가
- 서버 오류
- 대상 불명확

이 대사들은 일반적인 반복 대사로 사용하기보다 실패 대비용으로만 사용한다.

예시:

```text
위험: "조심해!"
접수: "알겠어."
취소: "멈출게."
불가: "지금은 할 수 없어."
불명확: "어느 쪽을 말하는 거야?"
```

---

# 19. 툴 호출과 대사 전달 순서

## 19.1 단순 명령

툴 호출은 대사 생성을 기다리지 않는다.

```text
Command Pipeline
→ 툴 호출 검증
→ 클라이언트에 즉시 전달
→ 사전 생성된 수락 대사 출력
```

응답 예시:

```json
{
  "action": {
    "name": "gather_resource",
    "target_id": "resource_wood",
    "quantity": 20
  },
  "dialogue_ref": "dialogue_pack:gather_accept:03"
}
```

## 19.2 심화 질문

세계관 검색과 같이 시간이 오래 걸리는 파이프라인은 짧은 대기 대사를 먼저 출력한다.

대기 대사는 별도 LLM 호출보다 사전 생성된 대사 팩을 사용한다.

```text
“잠깐, 우리가 본 기록을 정리해 볼게.”
“관련된 단서가 있었던 것 같아.”
“잠시만. 기억을 맞춰 볼게.”
```

```text
사용자 질문
├─ 대기 대사 즉시 출력
└─ Lore Retrieval + 답변 생성
       └─ 최종 대사 출력
```

---

# 20. 장기 기억 처리

장기 기억은 실시간 사용자 응답 경로와 분리한다.

## 20.1 원본 로그 저장

대화와 주요 이벤트는 우선 append-only 로그에 저장한다.

```text
대화 원문
중요 이벤트
전투 결과
퀘스트 완료
지역 발견
플레이어 선택
관계 변화
```

## 20.2 비동기 기억 워커

플레이어 요청이 없거나 서버가 유휴 상태일 때 다음 작업을 수행한다.

1. 기존 대화 세션 요약
2. 전투·이벤트 기록 요약
3. 감정 유형과 감정 강도 평가
4. 기억 후보 생성
5. 중복 기억 병합
6. 기억 중요도 계산
7. 장기 기억 저장

## 20.3 기억 종류

### 영구 기억

- 플레이어가 정한 동료 이름
- 플레이어가 선호하는 호칭
- 중요한 관계 선택
- 장기 목표
- 플레이어가 명시적으로 기억해 달라고 한 내용

### 중요 에피소드

- 첫 거점
- 첫 강적 처치
- 큰 실패
- 거점 파괴
- 주요 퀘스트 완료
- 함께 극복한 위기

### 세션 요약

- 오늘 방문한 지역
- 수행한 주요 작업
- 획득한 중요한 아이템
- 세션의 전반적인 감정

## 20.4 기억 중요도

초기에는 다음 요소를 조합한다.

```text
게임 이벤트 중요도
+ 감정 강도
+ 첫 경험 여부
+ 반복 언급 횟수
+ 플레이어의 명시적 중요 표현
```

망각과 감쇠 알고리즘은 후속 설계로 분리한다.  
MVP에서는 기억 종류와 중요도 점수만 저장해도 충분하다.

---

# 21. 사용자 요청 예시

## 21.1 따라와

사용자 요청:

```json
{
  "origin": "USER_REQUEST",
  "text": "따라와",
  "snapshot": {
    "...": "AIContextSnapshot"
  }
}
```

라우터:

```json
{
  "route": "COMMAND",
  "keywords": ["따라오기"]
}
```

명령 파이프라인:

```json
{
  "action": "follow_player"
}
```

클라이언트:

```text
FollowTarget = PlayerCharacter
State = Following
```

플레이어 위치는 AI 서버에 필요하지 않다.

---

## 21.2 나무 20개 모아 줘

라우터:

```json
{
  "route": "COMMAND",
  "keywords": ["나무", "20개", "모으기"]
}
```

명령 파이프라인:

```json
{
  "action": "gather_resource",
  "raw_target": "나무",
  "quantity_mode": "exact",
  "quantity": 20
}
```

Entity Resolver:

```json
{
  "target_id": "resource_wood"
}
```

클라이언트 툴 호출:

```json
{
  "tool": "gather_resource",
  "target_id": "resource_wood",
  "quantity": 20
}
```

---

## 21.3 저 늑대 공격해

라우터:

```json
{
  "route": "COMMAND",
  "keywords": ["늑대", "공격"]
}
```

COMMAND 파이프라인:

```json
{
  "command_type": "combat",
  "action": "attack_target",
  "raw_target": "저 늑대",
  "target_ref": "enemy_wolf_03"
}
```

클라이언트 툴 호출:

```json
{
  "tool": "combat_attack_target",
  "target_ref": "enemy_wolf_03"
}
```

클라이언트가 대상과 전투 가능 여부를 최신 상태로 검증한다.

---

## 21.4 철 도끼 어떻게 만들어?

라우터:

```json
{
  "route": "RECIPE",
  "keywords": ["철 도끼", "제작법"]
}
```

파이프라인 서버 코드 사용 필드:

```text
interaction
progression.unlocked_recipe_ids
progression.discovered_item_ids
```

기본 제작법 질문에는 인벤토리가 필요하지 않다.  
Recipe DB 검색 결과를 구조화한 뒤 LLM에는 확정된 레시피 사실만 전달한다.

“지금 만들 수 있어?”와 같은 제작 가능 여부 질문일 때만 서버 코드가 추가로 다음 필드를 사용한다.

```text
inventory
crafting
progression.unlocked_recipe_ids
```

서버 코드가 제작 가능 여부와 부족 수량을 계산하고, LLM에는 그 결과만 전달한다.

---

## 21.5 이 마을은 왜 버려진 거야?

라우터:

```json
{
  "route": "LORE",
  "keywords": ["마을", "버려진 이유"]
}
```

파이프라인 사용 필드:

```text
interaction.ping_target
interaction.selected_target
interaction.camera_trace_target
location.current_region_id
progression.story_stage
progression.story_flags
progression.discovered_lore_ids
```

스토리 진행 상태로 검색 결과를 필터링한 뒤 LLM이 짧게 답한다.

---

## 21.6 3시 방향에 적 출현

클라이언트 트리거:

```json
{
  "origin": "GAME_TRIGGER",
  "trigger_type": "DANGER_WARNING",
  "payload": {
    "danger_type": "hostile_enemy",
    "clock_direction": 3,
    "distance_band": "near",
    "enemy_type": "wolf"
  }
}
```

라우팅 없이 위험 경고 파이프라인으로 진입한다.

---

# 22. 최종 설계 원칙

1. 사용자 요청은 모두 LLM 라우터를 통과한다.
2. 인게임 트리거 요청은 라우터를 거치지 않는다.
3. 클라이언트는 사용자 원문과 AI 전용 통합 스냅샷을 함께 보낸다.
4. 라우터에는 사용자 원문만 전달한다.
5. 라우터는 파이프라인과 검색 키워드만 결정한다.
6. 각 파이프라인의 서버 코드는 필요한 스냅샷 필드만 추출한다.
7. 스냅샷 필드를 그대로 LLM에 전달하지 않는다.
8. 인벤토리 비교, 제작 가능 여부, 부족 수량과 조건 검사는 서버 코드가 수행한다.
9. LLM에는 서버 코드가 확정한 `Dialogue Fact`만 전달하며, LLM은 대사 표현만 담당한다.
10. 정보 질문은 사용자 요청 시점의 스냅샷을 기준으로 서버 코드가 판정한다.
11. 아이템을 소비하거나 게임 상태를 바꾸는 실행은 클라이언트가 최신 상태로 최종 검증한다.
12. LLM이 게임 내부 ID를 직접 확정하지 않도록 Entity Resolver를 둔다.
13. 세계관 정보는 스토리 단계와 플래그로 검색 단계에서 제한한다.
14. 작업 상태는 클라이언트와 오케스트레이터의 실제 상태를 UI로 표시한다.
15. 고정 대사 템플릿은 안전용 최소 수준만 유지한다.
16. 반복적으로 필요한 대사는 캐릭터·관계·상황별로 미리 생성한다.
17. 시간이 걸리는 작업은 실행 중 성공·실패 대사를 비동기로 사전 생성한다.
18. 장기 기억은 실시간 응답과 분리하여 비동기 워커로 처리한다.
19. 필수 슬롯이나 대상이 불명확하면 해당 파이프라인이 `PENDING_CLARIFICATION`을 생성한다.
20. 재질의 응답도 새로운 사용자 요청으로 받아 LLM 라우터를 통과시킨다.
21. `clarification_id`로 원래 요청과 이미 확정된 슬롯을 복원하고 부족한 정보만 병합한다.
22. 재질의 이후 게임 상태 판정에는 응답 시점의 최신 스냅샷을 사용한다.
23. 재질의 횟수를 제한하고 끝내 확정할 수 없으면 추측하여 실행하지 않는다.
24. 전투 명령도 `COMMAND` 라우트로 처리하고 세부 행동과 대상은 COMMAND 파이프라인에서 추출한다.
25. LLM은 전투 대상 좌표나 전술을 결정하지 않고, 클라이언트가 타깃 선택 정책과 실제 행동을 판정한다.
26. 전투 중에는 툴 호출을 우선 전달하고 대사는 짧은 사전 생성 대사 팩을 사용한다.
27. 공격 중지·후퇴처럼 안전을 높이는 명령은 빠르게 처리하고, 공격 대상이 불명확하면 실행하지 않는다.

---

# 23. 한 줄 정의

> 클라이언트는 AI용 게임 상태를 제공하고, 라우터는 사용자 의도만 분류한다. 각 파이프라인은 필요한 데이터를 코드로 판정하고, 정보가 부족하면 재질의 상태를 저장해 다음 사용자 응답으로 이어서 처리한다. LLM에는 확정된 사실만 전달하며 실제 게임 실행은 클라이언트가 최신 상태로 검증한다.
