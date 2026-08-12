# Build 1 플레이어 질답 목록과 데이터 형식

> [!WARNING]
> 레거시 문서 — 독립형 `/v1/companion/*` 계약을 설명합니다. 현행 계약은 `POST /api/v1/chat`
> (루트 `README.md`·`Contracts/` 참조). 마코 두뇌는 최상위 `companion/` 패키지로 이식됨.

이 문서는 Build 1에서 플레이어가 마코에게 말할 수 있는 대표 표현과, 그때 클라이언트가
AI 서버에 보내고 서버가 돌려주는 JSON 예시를 모은다. 정규 계약은
[`02_client_ai_contract.md`](02_client_ai_contract.md)이며, 이 문서는 그 계약을 실제 대화
사례로 풀어 쓴 카탈로그다.

## 1. 공통 규칙

메시지는 다음 엔드포인트로 보낸다.

```text
POST /v1/companion/message
Content-Type: application/json
```

모든 메시지 요청에는 클라이언트가 만든 `request_id`, 플레이어의 한국어 `text`, 요청 시점의
현재 상태인 `client_context`를 넣는다. 클라이언트는 사용자 의도를 추론해서 컨텍스트를
선별하지 않는다. 세 컨텍스트 필드를 항상 같은 구조로 보내고 현재 값이 없으면 `null`로 둔다.

```json
{
  "request_id": "req_000001",
  "text": "플레이어가 마코에게 한 말",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

최상위 요청 필드의 의미는 다음과 같다.

| 키 | 생성 주체 | 설명 |
|---|---|---|
| `request_id` | 클라이언트 | 요청과 응답을 연결하는 불투명한 메시지 식별자다. 요청마다 새 값을 권장한다. |
| `text` | 클라이언트 | 플레이어가 마코에게 한 한국어 발화다. |
| `client_context` | 클라이언트 | 발화 의도와 무관한 현재 게임 상태다. 서버가 라우팅 후 필요한 값만 사용한다. |

`client_context`에 허용되는 키는 세 가지뿐이다.

```json
{
  "location_id": "region_abandoned_mining_village",
  "active_task": null,
  "clarification_id": null
}
```

| 키 | 형식 | 값의 출처와 의미 | 값이 없을 때 |
|---|---|---|---|
| `location_id` | 문자열 또는 `null` | 요청 시점에 플레이어가 있는 현재 지역 ID다. 질문 종류와 관계없이 현재 값을 보낸다. | `null` |
| `active_task` | 객체 또는 `null` | 클라이언트가 관리하는 마코의 현재 작업이다. `id`는 클라이언트가 만든 작업 ID, `type`은 작업 종류다. | `null` |
| `clarification_id` | 문자열 또는 `null` | 서버가 직전 응답에서 열어 둔 pending 재질의 ID다. 다음 발화의 의미와 관계없이 pending 중이면 보낸다. | `null` |

예를 들어 pending 재질의 중 플레이어가 `나무` 대신 `따라와`라고 말해도 클라이언트는
`clarification_id`를 제거하지 않는다. 서버가 새 명령으로 판정하고 pending 상태를 종료한다.

`target`, `target_id`, `nearby_resources`, 화면 선택 대상, 인벤토리와 snapshot은 보내지
않는다. 허용되지 않은 키나 필수 키 누락 등 Pydantic 요청 검증 오류는 HTTP 422다.

메시지 응답은 항상 다음 다섯 키를 갖는다. 해당하지 않는 값은 `null`이다.

```json
{
  "request_id": "req_000001",
  "dialogue": "마코가 플레이어에게 하는 짧은 말",
  "action": null,
  "clarification": null,
  "error": null
}
```

## 2. 일반 대화

일반 대화는 Action 없이 짧은 대사를 반환한다. 이 절을 포함해 문서의 모든 응답 예시는 Mock
공급자가 반환하는 결정론적 폴백 대사다. OpenAI 또는 로컬 공급자에서는 같은 확정 사실과
의도를 유지하되 실제 문구가 달라질 수 있다. 공급자 호출이나 출력 검증이 실패하면 예시의
Mock 문구로 복구한다.

| 플레이어 입력 예시 | Mock 응답 대사 |
|---|---|
| `안녕` | `안녕! 오늘은 어디부터 둘러볼까?` |
| `안녕, 마코` | `안녕! 오늘은 어디부터 둘러볼까?` |
| `고마워` | `별말을 다 해. 필요하면 언제든 불러 줘.` |
| `감사해` | `별말을 다 해. 필요하면 언제든 불러 줘.` |

예를 들어 `안녕` 요청과 응답은 다음과 같다.

```json
// 요청
{
  "request_id": "req_000002",
  "text": "안녕",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
// 응답
{
  "request_id": "req_000002",
  "dialogue": "안녕! 오늘은 어디부터 둘러볼까?",
  "action": null,
  "clarification": null,
  "error": null
}
```

## 3. 따라오기·대기·중지

### 3.1 따라오기

다음과 같은 표현은 `follow_player` Action으로 변환된다.

```text
따라와
나를 따라와 줘
내 뒤를 따라와 주세요
```

```json
// 요청
{
  "request_id": "req_000003",
  "text": "나를 따라와 줘",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
// 응답
{
  "request_id": "req_000003",
  "dialogue": "알겠어. 따라갈게.",
  "action": { "type": "follow_player" },
  "clarification": null,
  "error": null
}
```

서버는 실제 이동을 수행하지 않는다. 클라이언트가 이 Action을 받아 마코를 이동시킨다.

### 3.2 대기

```text
기다려
여기서 기다려 줘
잠깐 대기해 주세요
```

```json
// 요청
{
  "request_id": "req_000004",
  "text": "여기서 기다려 줘",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
// 응답
{
  "request_id": "req_000004",
  "dialogue": "알겠어. 여기서 기다릴게.",
  "action": { "type": "wait" },
  "clarification": null,
  "error": null
}
```

### 3.3 현재 작업 중지

중지 요청에는 클라이언트가 관리하는 현재 작업이 있어야 한다. 서버는 작업의 실행 상태를
조회하지 않고 `active_task.id`를 그대로 `task_id`에 넣는다.

```json
// 요청
{
  "request_id": "req_000005",
  "text": "멈춰",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": { "id": "task_001", "type": "gather_resource" },
    "clarification_id": null
  }
}
```

```json
// 응답
{
  "request_id": "req_000005",
  "dialogue": "알겠어. 지금 하던 일을 멈출게.",
  "action": { "type": "stop_current_task", "task_id": "task_001" },
  "clarification": null,
  "error": null
}
```

활성 작업 없이 `멈춰`라고 하면 `active_task`는 `null`이며 서버는 Action을 만들지 않는다.

```json
{
  "request_id": "req_000006",
  "dialogue": "지금 중지할 작업이 없어.",
  "action": null,
  "clarification": null,
  "error": {
    "code": "UNSUPPORTED_REQUEST",
    "message": "지금 중지할 작업이 없어."
  }
}
```

## 4. 나무·돌 채집

### 4.1 자원 종류가 명확한 경우

나무 또는 목재가 들어간 요청은 `wood`, 돌이 들어간 요청은 `stone`으로 변환한다.

| 플레이어 입력 예시 | `action.resource_type` | 서버 대사 |
|---|---|---|
| `나무를 모아 줘` | `wood` | `알겠어. 근처의 나무를 찾아볼게.` |
| `목재를 가져와 주세요` | `wood` | `알겠어. 근처의 나무를 찾아볼게.` |
| `돌을 캐 줘` | `stone` | `알겠어. 근처의 돌을 찾아볼게.` |
| `돌을 모아 줘` | `stone` | `알겠어. 근처의 돌을 찾아볼게.` |

명확한 나무 요청의 형식은 다음과 같다.

```json
// 요청
{
  "request_id": "req_000007",
  "text": "나무를 모아 줘",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
// 응답
{
  "request_id": "req_000007",
  "dialogue": "알겠어. 근처의 나무를 찾아볼게.",
  "action": { "type": "gather_resource", "resource_type": "wood" },
  "clarification": null,
  "error": null
}
```

명확한 돌 요청은 `resource_type`만 `stone`으로 달라진다.

```json
{
  "request_id": "req_000008",
  "text": "돌을 캐 줘",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
{
  "request_id": "req_000008",
  "dialogue": "알겠어. 근처의 돌을 찾아볼게.",
  "action": { "type": "gather_resource", "resource_type": "stone" },
  "clarification": null,
  "error": null
}
```

서버는 특정 나무나 돌을 고르지 않는다. Action을 받은 클라이언트가 주변에서 해당 종류의
자원을 찾아 실행 가능성을 판단하고 작업을 시작한다.

### 4.2 자원 종류가 모호한 경우

`저것 좀 캐 줘`, `이것 좀 모아 줘`, `자원 좀 캐 줘`, `캐 줘`처럼 종류를 알 수 없는 요청은
한 번만 재질의한다.

```json
// 첫 요청
{
  "request_id": "req_000009",
  "text": "저것 좀 캐 줘",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
// 첫 응답
{
  "request_id": "req_000009",
  "dialogue": "무엇을 캐면 될까?",
  "action": null,
  "clarification": {
    "id": "clarify_<서버가 생성한 값>",
    "options": ["나무", "돌"]
  },
  "error": null
}
```

플레이어가 `나무`를 골랐을 때는 직전 응답의 실제 clarification ID를 컨텍스트에 넣는다.

```json
// 후속 요청
{
  "request_id": "req_000010",
  "text": "나무",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": "clarify_<첫 응답의 실제 값>"
  }
}
```

```json
// 후속 응답
{
  "request_id": "req_000010",
  "dialogue": "알겠어. 근처의 나무를 찾아볼게.",
  "action": { "type": "gather_resource", "resource_type": "wood" },
  "clarification": null,
  "error": null
}
```

`돌`을 보내면 동일한 방식으로 `resource_type: "stone"`이 된다. `나무`와 `돌`이 아닌
답변은 다음처럼 재질의를 종료하며, 같은 ID로 다시 답해도 다시 묻지 않는다.

```json
{
  "request_id": "req_000011",
  "dialogue": "선택을 종료했어. 나무 또는 돌 채집처럼 지원하는 명령으로 다시 말해 줘.",
  "action": null,
  "clarification": null,
  "error": {
    "code": "UNSUPPORTED_REQUEST",
    "message": "선택을 종료했어. 나무 또는 돌 채집처럼 지원하는 명령으로 다시 말해 줘."
  }
}
```

취소(`취소`, `됐어`, `나중에 하자`) 또는 새 명확한 명령(`따라와`, `돌을 캐 줘`)이 들어오면
서버가 기존 pending 재질의를 삭제한다. 클라이언트는 발화의 종류를 판정하지 않으므로, pending
중이라면 취소나 새 명령에도 기존 `clarification_id`를 그대로 첨부한다.

### 4.3 지원하지 않는 채집

Build 1은 수량과 세부 자원을 받지 않는다.

| 플레이어 입력 | 처리 |
|---|---|
| `나무 10개 모아 줘` | 미지원 |
| `가방이 찰 때까지 돌을 캐 줘` | 미지원 |
| `돌 블록을 캐 줘` | 미지원 |
| `부싯돌을 캐 줘` | 미지원 |
| `철광석을 캐 줘` | 미지원 |
| `풀을 캐 줘` | 미지원 |

수량 또는 지원하지 않는 세부 자원을 포함하면 Action 없이 다음 오류를 반환한다.

```json
{
  "request_id": "req_000012",
  "dialogue": "지금은 수량 지정 없이 일반 나무나 돌 채집만 도와줄 수 있어.",
  "action": null,
  "clarification": null,
  "error": {
    "code": "UNSUPPORTED_REQUEST",
    "message": "지금은 수량 지정 없이 일반 나무나 돌 채집만 도와줄 수 있어."
  }
}
```

## 5. 철 도끼 제작법

철 도끼와 제작·재료·방법을 함께 묻는 요청을 지원한다.

```text
철 도끼 만드는 법을 알려 줘
철 도끼 재료가 뭐야?
철도끼 제작법 알려 줘
```

```json
// 요청
{
  "request_id": "req_000013",
  "text": "철 도끼 만드는 법을 알려 줘",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
// 응답
{
  "request_id": "req_000013",
  "dialogue": "철 도끼는 철괴 3개와 나무 2개가 필요하고, 작업대에서 만들 수 있어.",
  "action": null,
  "clarification": null,
  "error": null
}
```

이 응답은 레시피 설명일 뿐이다. 서버는 플레이어 인벤토리를 확인하거나 제작 Action을
발행하지 않는다.

## 6. 지역 세계관

클라이언트는 모든 메시지에 현재 `location_id`를 보내며, 서버는 발화를 지역 질문으로 라우팅한
경우에만 그 값을 사용한다. 현재 알려진 지역 ID는 `region_abandoned_mining_village`다.

```json
// 요청
{
  "request_id": "req_000014",
  "text": "이 마을은 어떤 곳이야?",
  "client_context": {
    "location_id": "region_abandoned_mining_village",
    "active_task": null,
    "clarification_id": null
  }
}
```

```json
// 응답
{
  "request_id": "req_000014",
  "dialogue": "버려진 광산 마을은 오래전 광산이 폐쇄된 뒤 사람들이 떠난 곳이야. 남아 있는 건물과 기록은 조심해서 살펴보자.",
  "action": null,
  "clarification": null,
  "error": null
}
```

`location_id`가 없거나 알려진 값이 아니면 사실을 추측하지 않고 오류를 반환한다.

```json
{
  "request_id": "req_000015",
  "dialogue": "지금 위치에 대해 확인된 이야기는 아직 없어.",
  "action": null,
  "clarification": null,
  "error": {
    "code": "UNSUPPORTED_REQUEST",
    "message": "지금 위치에 대해 확인된 이야기는 아직 없어."
  }
}
```

## 7. 채집 작업 결과 이벤트

클라이언트가 `gather_resource` Action을 실행한 뒤 결과를 서버에 보낸다.

```text
POST /v1/companion/event
Content-Type: application/json
```

### 성공

성공 이벤트는 양의 정수 `amount`를 포함해야 한다.

```json
// 요청
{
  "event": "task_completed",
  "task_id": "task_001",
  "action": "gather_resource",
  "result": { "resource_type": "stone", "amount": 10 }
}
```

```json
// 응답
{
  "task_id": "task_001",
  "dialogue": "돌 10개를 모았어."
}
```

나무 성공 이벤트라면 `resource_type`을 `wood`로 보내고 대사는 `나무 10개를 모았어.`처럼
반환된다.

### 실패

클라이언트가 해당 자원을 찾지 못했거나 접근할 수 없으면 실제 채집을 실행하지 않고 실패
이벤트를 보낸다. 실패 결과에는 `resource_type`만 넣는다.

```json
// 요청
{
  "event": "task_failed",
  "task_id": "task_002",
  "action": "gather_resource",
  "result": { "resource_type": "wood" }
}
```

```json
// 응답
{
  "task_id": "task_002",
  "dialogue": "나무를 찾지 못했거나 가까이 갈 수 없었어."
}
```

## 8. 플레이어-클라이언트-서버 순서

```text
플레이어 발화
  → 클라이언트가 의도 판정 없이 request_id, text와 현재 client_context 구성
  → POST /v1/companion/message
  → 서버가 의도를 판정하고 필요한 컨텍스트만 사용
  → 서버가 dialogue/action/clarification/error 반환
  → 클라이언트가 Action 실행 또는 clarification 표시
  → 채집이면 클라이언트가 자원 탐색·task_id 생성·실행
  → POST /v1/companion/event
  → 서버가 작업 결과 dialogue 반환
```

서버는 월드 엔티티 선택, 이동, 대기, 채집 실행, 작업 상태 저장을 하지 않는다. 플레이어에게
보이는 대화와 클라이언트가 실행할 수 있는 최소 Action을 반환하는 것이 Build 1 서버의
책임이다.
