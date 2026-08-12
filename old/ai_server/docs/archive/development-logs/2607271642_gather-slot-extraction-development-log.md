# 2607271642 채집 슬롯 추출과 `Command.GatherResource` 계약 추가 개발 기록

- 기록일: 2026-07-27 16:42
- 기록 유형: 기능 완료 기록(계약 변경 포함)
- 변경 범위: `Command.GatherResource` 계약 추가, `ResourceRepository` 신설, Stage 2 구조화
  출력에 채집 슬롯(`resource`/`quantity`) 편입, `gather_node`의 명령 후보 발행,
  허용 목록 밖 명령의 수락 대사 비대칭 수정
- 기준 커밋: `ee30703`
- API/스키마 버전: 공용 `CommandType`에 값 1개 추가(하위 호환), `POST /api/v1/chat` 계약
  버전 변경 없음
- 후속 범위: Stage 1 + Stage 2 병합(왕복 1회 감소, 이번 범위 제외)

## 1. 완료 상태 요약

채집 자원 판정을 정규식에서 LLM 구조화 슬롯으로 옮기고, 채집이 실제 명령 후보를 내도록
계약을 넓혔다. 세 가지 문제를 한 번에 정리했다.

1. **정규식이 제자리에 없었다.** 문서는 "정규식은 Mock·폴백 전용"이라고 선언했지만
   `gather_node`가 실제 LLM 경로에서도 `resolve_gather`를 호출했다. Stage 1·2는 LLM이
   분류하는데 Stage 3만 정규식이라, LLM이 `gather_resource`로 정확히 분류한
   `"장작 좀 모아줘"`가 `None`으로 떨어져 엉뚱한 미지원 대사를 냈다.
2. **부분 문자열 매칭이 취약했다.** `"돌" in normalized`가 `부싯돌`·`조약돌`을 stone으로
   잡았고, 이를 `_UNSUPPORTED_RESOURCE` 거부 목록으로 막고 있었다. 거부 목록은 원리상
   무한히 늘어난다.
3. **채집이 명령을 내지 못했다.** `CommandType`에 `Command.GatherResource`가 없어 자원을
   정확히 알아들어도 게임에 전달할 방법이 없었다.

전체 230개 테스트가 통과하고 `ruff check .`, `mypy app`이 깨끗하다.

## 2. 설계 결정

### 이해는 LLM, 어휘·정책 확정은 코드

`CommandClassification`이 `command` 하나만 반환하던 것을 `resource`(`ResourceSlot` enum)와
`quantity`(`int | None`)까지 반환하도록 넓혔다. **추가 LLM 호출은 없다** — `classify_command`가
이미 그 문장을 읽고 있었고, 같은 구조화 출력에 필드 두 개가 붙었을 뿐이다.

`resource`를 enum으로 제약한 것이 핵심이다. 자유 문자열로 추출시키면 `"참나무"`,
`"목재 자원"` 같은 값이 돌아와 결국 다시 정규식으로 매칭해야 하므로 문제가 한 칸 이동할
뿐이다. enum이면 스키마 밖 값이 물리적으로 나올 수 없다.

지원 여부와 수량 상한은 여전히 서버가 판정한다. LLM은 `"수량이 언급됐다"`만 보고하고,
그것이 허용 범위인지는 `ResourceRepository`가 정한다.

### 거부 목록이 아니라 허용 목록

`ResourceRepository`가 정식 식별자 → 별칭 표를 소유한다. 허용 목록에 없으면 미지원이므로
`_UNSUPPORTED_RESOURCE` 거부 목록은 삭제했다. 새 자원 추가가 표 한 줄이 된다.

별칭은 조사 경계로 매칭한다.

```
(?:^|\s)돌(?:을|를|은|는|이|가|도|만|와|과|랑|이랑|하고)?(?=\s|$)
```

한국어는 교착어라 단순 토큰 분리로는 `"나무를"`을 못 잡고, 단순 부분 문자열로는
`"부싯돌"`을 잘못 잡는다. 조사 경계 매칭이 둘 다 해결한다.

### 수량 미명시는 실패가 아니다

`quantity is None`은 정상 경로다. `parameters`에서 키를 생략해 게임이 기본량을 정하게
한다. **서버가 기본량을 정하지 않는** 이유는 두 가지다.

- 적당한 채집량은 인벤토리 여유·도구 내구도·주변 자원 밀도에 달려 있는데 companion은
  그 어느 것도 모른다. 상수를 박아 보내면 게임이 상황에 맞게 줄일 수 없다.
- 서버가 기본량을 정하면 그 숫자를 대사에 넣을지도 정해야 하고, 넣으려면 확정 사실에
  실어야 하며(`sanitize`의 숫자 가드), 게임이 실제로 더 적게 캐면 마코가 틀린 말을 한 게
  된다.

결과적으로 플레이어가 수량을 말했을 때만 수량이 흐르고, 말하지 않았으면 마코도 언급하지
않는다. 상한 초과는 clamp하지 않고 명령 없이 미지원 대사로 응답한다 — 조용히 잘라 보내면
마코가 말한 수량과 실제 수량이 달라진다.

### 허용 목록 밖 명령에 수락 대사를 내지 않는다

구현 중 기존 `movement_command_node`에서 발견한 비대칭이다. `allowed_commands`에 없는
명령이 들어오면 명령 후보는 `None`이 되는데 대사는 여전히 `follow_player` 장면의 수락
대사("알겠어. 따라갈게.")가 나갔다. 마코가 따라가겠다고 말해 놓고 아무 명령도 나가지 않는
상태다.

`_COMMANDS` 테이블 주석이 막겠다고 선언한 "대사는 있는데 명령이 없는 비대칭"을 정작 허용
목록 게이트에서 못 막고 있었다. 두 노드 모두 허용 여부를 장면 선택 **전에** 확인하고,
허용되지 않으면 `decline()`으로 `unsupported` 장면만 낸다. `candidate()`는 더 이상
`None`을 반환하지 않으므로 같은 실수를 반복하기 어렵다.

### 분류 토큰 예산 상향

`classify_max_tokens`를 20 → 64로 올렸다. 필드가 셋으로 늘면 20토큰으로는 응답이 잘리고,
`model_validate_json` 실패 → `except` → mock 폴백으로 **조용히** 떨어진다. 폴백 결과가
그럴듯해서 발견이 매우 어려운 종류의 실패라 여유를 크게 뒀다.

## 3. 계약 변경과 하위 호환

`Command.GatherResource`를 `CommandType`과 스키마 세 곳에 추가했다.

| 파일 | 위치 |
|---|---|
| `app/application/models/ai.py` | `CommandType` |
| `Contracts/schemas/command.schema.json` | `type` enum |
| `Contracts/schemas/chat-request.schema.json` | `allowed_commands` enum (`allOf` 내부 2곳) |
| `Contracts/schemas/ai-service-request.schema.json` | `allowed_commands` enum |

`allowed_commands`가 클라이언트가 보내는 **옵트인 목록**이라 구버전 클라이언트에는 영향이
없다. 보내지 않는 클라이언트는 이 명령을 받을 경로 자체가 없다(게이트: `gather_node`의
허용 확인, `chat_service._validate_ai_result`).

**배포 순서는 서버 먼저다.** `chat-request.schema.json`의 enum은 입력 검증이기도 해서,
서버가 값을 받기 전에 클라이언트가 `Command.GatherResource`를 보내면 422로 거부된다.

`test_command_type_enum_matches_schemas`를 추가해 드리프트를 막았다. 기존
`test_contracts.py`는 픽스처↔스키마만 검사해서 `ai.py`에만 추가하고 `Contracts/`를
빠뜨려도 전 테스트가 통과했다. 새 테스트는 스키마를 재귀 순회해 `Command.` 로 시작하는
값을 담은 모든 enum을 찾아 `CommandType`과 대조하므로, `allOf` 내부처럼 중첩된 위치도
누락되지 않는다.

## 4. `parameters` 검증 경계

`chat_service._validate_ai_result`는 `request_id`와 `type ∈ allowed_commands`만 검사하고
`parameters`는 통과시킨다. `parameters`의 의미 검증(어떤 자원이 유효한가, 수량 상한은
얼마인가)은 게임 지식이라 Backend가 갖는 것이 계층 경계 위반이다.

따라서 companion이 최종 방어선이다. `GatherParameters`가 `resource: ResourceId` +
`quantity: int | None = Field(ge=1, le=MAX_GATHER_QUANTITY)`로 상한 검사를 한곳에 모으고,
`gather_node`가 이 모델을 통과시킨 뒤 `model_dump(mode="json", exclude_none=True)` 결과만
`CommandCandidate.parameters`에 넣는다.

## 5. 검증

| 파일 | 추가 검증 |
|---|---|
| `tests/test_resources.py` (신규) | 별칭 매칭, 조사 경계, `부싯돌`/`조약돌` 오탐 회귀, 슬롯 매핑, 수량 정책, `GatherParameters` 상한 |
| `tests/test_llm.py` | `classify_command` 반환 타입 변경, Mock의 슬롯·수량 추출 9경로, strict 스키마의 `required == ["command","resource","quantity"]` |
| `tests/test_companion_graph.py` | 수량 명시/미명시 두 경로의 `parameters`, 미지원 4분기, 허용 목록 밖 명령의 거절 대사 |
| `tests/test_companion_ai_service.py` | 채집 명령 후보 발행, 허용 목록 없을 때 대사 전용 |
| `tests/test_contracts.py` | gather 픽스처 2종, `CommandType` ↔ 스키마 enum 일치 |

`"장작 좀 모아줘"`가 이번 변경의 대표 회귀 케이스다. 이전에는 미지원 대사로 실패했고,
이제 `parameters={"resource": "wood"}` 명령을 발행한다.

## 6. 코드 리뷰에서 잡은 폴백 결함 두 건

구현 후 리뷰에서 Mock·폴백 경로의 결함 두 건이 나와 같은 범위에서 고쳤다. 둘 다
`LLM_PROVIDER=mock` 이거나 실제 공급자가 실패했을 때만 도는 경로지만, 후자는 운영
경로다.

### 복수 자원을 임의의 단일 자원으로 바꾸던 문제

`resolve()`가 첫 일치에서 반환해 `"돌이랑 나무를 모아 줘"`가 `_ALIASES` 정의 순서에 따라
항상 wood가 됐다. 플레이어의 말과 무관하게 표의 순서가 결정한 것이다.

`find_all()`로 바꿔 등장하는 자원을 모두 돌려주고, 서로 다른 자원이 둘 이상이면 이미 있는
`UNSPECIFIED` → 되묻기 경로("무엇을 캐면 될까?")로 보낸다.

**이건 폴백만의 문제가 아니었다.** `ResourceSlot`이 단일 값 enum이라 실제 LLM도 하나를
골라야 하는데 프롬프트에 복수 자원 규칙이 없어 모델 판단에 맡겨져 있었다. 주경로가 정의되지
않은 쪽이 더 심각해서, `_COMMAND_ROUTER_PROMPT`에 같은 규칙("서로 다른 자원을 함께 말하면
하나를 고르지 말고 unspecified")을 넣어 두 경로를 일치시켰다.

### 온전하지 않은 숫자를 수량으로 주워 담던 문제

`(\d+)\s*개`를 부분 검색해 `"1.5개"`가 5로, `"-1개"`가 1로 읽혔다. 원인은 정규식의 부분
검색만이 아니라 `normalize()`가 문장부호를 공백으로 바꾸는 것과의 조합이다 —
`"1.5개"`가 `"1 5개"`가 된 뒤에는 `"5개"`가 온전한 어절로 보인다.

그래서 수량은 **정규화 전 원문**에서 앞 경계를 확인하며 읽도록 바꿨다.

```python
_QUANTITY = re.compile(r"(?<![\d.,\-])(\d+)\s*개")
```

수량이 여럿 나오면(`"20개 30개"`) 어느 쪽인지 확신할 수 없으므로 `None`으로 둔다.

`"스무 개"`처럼 한글 수사는 여전히 `None`이다. 리뷰는 이런 "명시했지만 해석 불가"를
별도 invalid 상태로 거절하자고 제안했으나 채택하지 않았다 — 프롬프트가 이미 `"많이"`,
`"가방 찰 때까지"`를 null로 두라고 지시하고 있어서, 폴백만 거절하면 공급자가 살아 있을
때와 죽었을 때 동작이 갈린다. 읽지 못한 수량은 "미지정"으로 흡수해 게임 기본량으로 캐는
것이 폴백다운 degradation이다.

리뷰가 지적하지 않은 부분도 확인했다. `"0개"`와 상한 초과는 `allows_quantity()`가 이미
막고 있어 명령이 나가지 않는다.

## 7. 후속 범위

**Stage 1 + Stage 2 병합.** 명령 발화마다 LLM 왕복이 하나 줄어드는 가장 큰 레버지만,
그래프 토폴로지와 분류 정확도 회귀를 함께 검증해야 해서 분리했다. 참고로
`openai_timeout_seconds`(호출당 8초)와 `ai_request_timeout_seconds`(전체 10초)가
합성되어 있지 않다 — 순차 3회 호출이므로 그 작업 때 함께 정리할 값어치가 있다.
