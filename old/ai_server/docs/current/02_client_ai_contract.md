# 클라이언트-AI 계약

> [!WARNING]
> 레거시 문서 — 독립형 `/v1/companion/*` 계약을 설명합니다. 현행 계약은 `POST /api/v1/chat`
> (루트 `README.md`·`Contracts/` 참조). 마코 두뇌는 최상위 `companion/` 패키지로 이식됨.

계약 버전은 URL의 `/v1`만으로 표현한다. 버전 헤더, health, capabilities, 요청 조회와 SSE는
없다. Pydantic 검증에 실패한 요청은 HTTP 422다.

## 1. 메시지 요청

`POST /v1/companion/message`

클라이언트는 사용자의 의도를 판정하거나 의도에 따라 컨텍스트 필드를 선별하지 않는다.
모든 메시지에 요청 시점의 현재 상태를 동일한 `client_context` 구조로 첨부한다. 값이 없으면
해당 키를 빼는 대신 `null`을 보낸다. 서버가 텍스트를 라우팅한 뒤 필요한 필드만 사용한다.

```text
클라이언트: 현재 상태를 기계적으로 첨부
서버: 사용자 의도를 판정하고 필요한 컨텍스트만 소비
```

### 1.1 표준 요청 형식

```json
{
  "request_id": "req_000001",
  "text": "나무를 모아 줘",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

서버 입력 모델은 상태가 없는 단순 호출을 위해 `client_context` 또는 내부 필드의 생략도
허용하지만, Build 1 표준 게임 클라이언트는 위의 고정 구조를 사용한다.

### 1.2 최상위 요청 필드

| 필드 | 형식 | 필수 | 생성 주체 | 설명 |
|---|---|---|---|---|
| `request_id` | 문자열, 1~128자 | 예 | 클라이언트 | 요청과 응답을 연결하는 불투명한 메시지 식별자다. 요청마다 새 값을 권장하며 서버 저장이나 멱등 키로 사용하지 않는다. |
| `text` | 문자열, 1~1000자 | 예 | 클라이언트 | 플레이어의 한국어 발화다. 서버는 앞뒤 공백을 제거하며 빈 문자열은 거부한다. |
| `client_context` | 객체 | 표준 클라이언트에서 예 | 클라이언트 | 사용자 의도와 무관하게 요청 시점의 현재 상태를 담는다. 허용 필드는 아래 세 개뿐이다. |

### 1.3 `client_context` 필드

| 필드 | 형식 | 값의 출처 | 값이 없을 때 | 서버가 사용하는 경우 |
|---|---|---|---|---|
| `location_id` | 문자열 또는 `null` | 클라이언트가 알고 있는 플레이어의 현재 지역 ID | `null` | 라우팅 결과가 지역 세계관 질문일 때만 조회 키로 사용한다. |
| `active_task` | 객체 또는 `null` | 클라이언트가 관리하는 마코의 현재 실행 작업 | `null` | 라우팅 결과가 작업 중지 명령일 때만 사용한다. |
| `clarification_id` | 문자열 또는 `null` | 서버가 직전 응답에서 열어 둔 pending 재질의 ID | `null` | pending 재질의의 다음 플레이어 메시지를 처리할 때 사용한다. |

`location_id`는 질문 내용을 보고 채우는 값이 아니다. 클라이언트는 일반 대화나 채집 명령에도
현재 지역을 알고 있다면 같은 값을 보낸다. 현재 알려진 Build 1 지역은
`region_abandoned_mining_village`다.

`active_task`는 서버 Action을 실행하는 클라이언트가 소유하는 상태다.

| 하위 필드 | 형식 | 설명 |
|---|---|---|
| `active_task.id` | 문자열, 1~128자 | 클라이언트가 작업 시작 시 만든 `task_id`다. 중지 Action에서 그대로 반환된다. |
| `active_task.type` | 문자열, 1~64자 | 실행 중 작업의 종류다. Build 1 채집 작업은 `gather_resource`를 사용한다. |

`clarification_id`도 후속 텍스트의 의미를 클라이언트가 판단해 선택하는 값이 아니다. pending
재질의가 있으면 플레이어의 다음 메시지가 `나무`, `취소`, `따라와` 중 무엇이든 같은 ID를
첨부한다. 서버가 답변, 취소 또는 새 명령인지 판정하고 pending 상태를 종료한다.

다음 필드는 `client_context`에 허용하지 않는다.

- `target`, `target_id`, 엔티티 ID
- `nearby_resources`, 화면 선택 대상과 핑
- 인벤토리, 접근 가능성, 경로와 전체 snapshot

## 2. 메시지 응답

서버는 다음 다섯 필드를 항상 반환한다. 해당 결과가 없으면 `null`이다.

```json
{
  "request_id": "req_000001",
  "dialogue": "알겠어. 근처의 나무를 찾아볼게.",
  "action": { "type": "gather_resource", "resource_type": "wood" },
  "clarification": null,
  "error": null
}
```

| 필드 | 형식 | 설명 |
|---|---|---|
| `request_id` | 문자열 | 요청의 `request_id`를 그대로 반환한다. |
| `dialogue` | 문자열 | 플레이어에게 표시할 마코의 대사다. |
| `action` | Action 객체 또는 `null` | 클라이언트가 실행할 구조화 명령이다. 서버가 실제 월드 행동을 실행하지는 않는다. |
| `clarification` | 객체 또는 `null` | 플레이어에게 선택을 요청할 때만 존재한다. Action과 동시에 반환하지 않는다. |
| `error` | 오류 객체 또는 `null` | 지원하지 않는 요청이면 `code`와 사용자 안내용 `message`를 담는다. |

### 2.1 허용 Action

| `action.type` | 추가 필드 | 의미 |
|---|---|---|
| `follow_player` | 없음 | 클라이언트가 마코를 플레이어에게 따라오게 한다. |
| `wait` | 없음 | 클라이언트가 마코를 현재 위치에서 기다리게 한다. |
| `stop_current_task` | `task_id` | 클라이언트가 해당 현재 작업을 중지한다. |
| `gather_resource` | `resource_type` | 클라이언트가 가까운 해당 종류 자원을 찾아 채집한다. |

```json
{ "type": "follow_player" }
{ "type": "wait" }
{ "type": "stop_current_task", "task_id": "task_001" }
{ "type": "gather_resource", "resource_type": "wood" }
{ "type": "gather_resource", "resource_type": "stone" }
```

`resource_type`은 `wood` 또는 `stone`만 허용한다. 이는 자원 종류이며 특정 월드 엔티티를
가리키지 않는다.

### 2.2 재질의

모호한 `저것 좀 캐 줘`에는 Action 없이 재질의를 반환한다.

```json
{
  "request_id": "req_000002",
  "dialogue": "무엇을 캐면 될까?",
  "action": null,
  "clarification": {
    "id": "clarify_01",
    "options": ["나무", "돌"]
  },
  "error": null
}
```

| 하위 필드 | 형식 | 설명 |
|---|---|---|
| `clarification.id` | 문자열 | 서버가 생성한 pending 재질의 ID다. 클라이언트는 pending 상태가 끝날 때까지 다음 메시지에 첨부한다. |
| `clarification.options` | 문자열 배열 | 플레이어에게 표시하고 후속 텍스트로 전송할 선택지다. Build 1은 `나무`, `돌` 두 값만 사용한다. |

다른 답변이면 재질의를 한 번만 종료한다. 취소 또는 새 명확한 명령도 기존 재질의를 삭제한다.

### 2.3 오류

| 하위 필드 | 형식 | 설명 |
|---|---|---|
| `error.code` | 문자열 | 클라이언트 분기용 오류 코드다. Build 1의 지원 범위 오류는 `UNSUPPORTED_REQUEST`다. |
| `error.message` | 문자열 | 플레이어에게 표시할 수 있는 안전한 오류 설명이다. |

요청 JSON 구조 자체가 잘못된 경우에는 위 메시지 응답이 아니라 FastAPI의 HTTP 422 검증
응답을 반환한다.

## 3. 작업 이벤트

클라이언트는 Action을 받은 뒤 가까운 자원을 직접 찾고, 실행 가능하면 `task_id`를 생성해
작업을 수행한다. 완료 또는 실패 결과는 다음 엔드포인트로 보낸다.

`POST /v1/companion/event`

### 3.1 이벤트 요청 필드

| 필드 | 형식 | 설명 |
|---|---|---|
| `event` | `task_completed` 또는 `task_failed` | 클라이언트가 판정한 작업 결과다. |
| `task_id` | 문자열, 1~128자 | 클라이언트가 작업 시작 시 생성한 식별자다. |
| `action` | `gather_resource` | 결과가 어떤 Action에서 발생했는지 나타낸다. Build 1 이벤트는 채집만 허용한다. |
| `result` | 객체 | 채집한 자원 종류와, 성공일 때 수량을 담는다. |
| `result.resource_type` | `wood` 또는 `stone` | 채집을 시도한 자원 종류다. |
| `result.amount` | 1 이상의 정수 | 성공 이벤트에 필수다. 실패 이벤트에서는 생략한다. |

```json
{
  "event": "task_completed",
  "task_id": "task_001",
  "action": "gather_resource",
  "result": {
    "resource_type": "stone",
    "amount": 10
  }
}
```

### 3.2 이벤트 응답 필드

| 필드 | 형식 | 설명 |
|---|---|---|
| `task_id` | 문자열 | 이벤트 요청의 `task_id`를 그대로 반환한다. |
| `dialogue` | 문자열 | 플레이어에게 표시할 작업 결과 대사다. |

```json
{
  "task_id": "task_001",
  "dialogue": "돌 10개를 모았어."
}
```

자원을 찾을 수 없거나 접근할 수 없으면 클라이언트는 채집을 실행하지 않고 `task_failed`를
보낸다. 서버는 작업 성공 여부나 월드 상태를 자체 판정하지 않는다.
