# 2607181449 FOLLOW/WAIT Action Intent 개발 기록

- 기록일: 2026-07-18 14:49
- 기록 유형: 기능 개발 완료 기록
- 구현 기준: `08af20e feat: add follow and wait action intents`
- 변경 범위: 결정론적 명령 해석, Action Intent 발급, 내장 수락 대사, 저장·재사용, API 계약 및 회귀 테스트
- API/스키마 버전: `1` / `1.0` 유지

## 1. 완료 상태 요약

`POST /v1/companion/requests`의 `COMMAND` 라우트에서 `FOLLOW_PLAYER`와 `WAIT`를
실행 가능한 Action Intent로 반환하는 서버 수직 슬라이스를 구현했다.

```text
CompanionRequest
→ 기존 Router가 COMMAND 판정
→ CommandIntentParser가 허용된 단일 명령인지 검사
→ follow_player 또는 wait Action Intent 생성
→ LLM 호출 없이 내장 수락 대사 생성
→ ACTION_READY 상태로 결과 저장
→ API 응답 반환
```

지원하지 않거나 안전하게 단일 행동으로 확정할 수 없는 `COMMAND`는 기존 동작을 유지한다.
이 경우 `action=null`, `status=COMPLETED`이며 기존 대사 생성 경로를 사용한다.

## 2. 명령 파서 계약

`src/ai_companion_server/command_intent.py`에 Router와 분리된
`CommandIntentParser`를 추가했다. 파서는 정규화된 전체 문장이 허용 패턴과 완전히
일치할 때만 intent를 반환한다. 문장 일부에서 행동 단어를 찾았다는 이유로 Action
Intent를 만들지 않는다.

### 지원 입력

FOLLOW 계열:

```text
따라와
내 뒤를 따라와 줘
나를 따라와 주세요
follow me
please follow me
follow me please
```

WAIT 계열:

```text
여기서 기다려
여기서 기다려 줄래
잠깐 대기해 줘
wait here
please wait here
wait here please
```

대소문자, 앞뒤·중복 공백, 일반적인 구두점은 정규화한다.

### 안전하게 거부하는 입력

다음 입력은 `COMMAND`로 라우팅될 수 있지만 Action Intent는 만들지 않는다.

```text
따라오지 마
don't follow me
따라왔다가 공격해
따라와 아니면 기다려
지금 멈춰
나무 20개 모아 줘
저 늑대 공격해
```

즉, 부정 명령, 복합 명령, 다중 행동, STOP/GATHER/COMBAT 등 미지원 행동은 현재
실행하지 않는다.

## 3. Action Intent와 응답 계약

지원 명령의 `RequestData.status`와 `StoredRequest.status`는 모두
`ACTION_READY`다. Action Intent에는 다음 필드가 항상 포함된다.

```json
{
  "action_id": "action_<generated-id>",
  "type": "follow_player",
  "tool": "follow_player",
  "parameters": {},
  "client_validation_required": true,
  "issued_at": "<UTC datetime>",
  "expires_at": "<issued_at + 10 seconds>",
  "source_request_id": "<request_id>"
}
```

행동별 parameters는 다음과 같다.

| tool | parameters |
|---|---|
| `follow_player` | `{}` |
| `wait` | `{"duration_mode": "until_new_command"}` |

`action_id`는 `action_` 접두사를 사용한다. 시각은 timezone-aware UTC이며
`expires_at - issued_at`은 정확히 10초다. 실제 행동 실행 전에는 반드시 클라이언트가
최신 게임 상태로 검증해야 한다.

## 4. 대사와 LLM 분리

지원 명령은 LLM provider를 호출하지 않고 다음 내장 템플릿을 사용한다.

| 언어 | FOLLOW | WAIT |
|---|---|---|
| 한국어 | `알겠어. 따라갈게.` | `알겠어. 여기서 기다릴게.` |
| 비한국어 | `Got it. I'll follow you.` | `Got it. I'll wait here.` |

언어는 `input.language`가 `ko`로 시작하는지로 선택한다. 저장 메타데이터는 다음과 같다.

```text
provider=builtin
model=command-template-v1
```

따라서 OpenAI 설정 누락, provider 장애, LLM timeout은 지원 명령의 Action Intent 발급과
수락 대사에 영향을 주지 않는다.

## 5. 저장, 멱등성, 로깅

- 최초 요청 결과 전체를 기존 `RequestStore`에 저장한다.
- 같은 `request_id`가 다시 들어오면 저장된 레코드를 그대로 반환한다.
- 재요청에서는 `trace_id`, `action_id`, `issued_at`, `expires_at`, 대사가 모두 동일하다.
- 완료 로그에 `action_id`와 `tool`을 추가했다.
- 사용자 원문과 snapshot은 완료 로그에 기록하지 않는다.
- 기존 PostgreSQL `result_json` 직렬화 구조를 사용하므로 이번 변경에 migration은 없다.

## 6. capabilities와 API 문서

`GET /v1/capabilities`의 `supported_tools`는 실제 구현된 다음 두 개만 반환한다.

```json
["follow_player", "wait"]
```

`docs/04_api_contract.md`의 명령 성공 예시, Action Intent 공통 스키마, 허용 툴 목록,
capabilities 예시를 실제 서버 계약과 맞췄다.

## 7. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `src/ai_companion_server/command_intent.py` | 지원 명령 enum, 전체 문장 기반 결정론적 파서 |
| `src/ai_companion_server/domain.py` | `ACTION_READY`, `issued_at`, `source_request_id` 추가 |
| `src/ai_companion_server/service.py` | 명령 분기, Action Intent, 내장 대사, 메타데이터와 로그 |
| `src/ai_companion_server/main.py` | capabilities에 실제 지원 툴 광고 |
| `tests/test_command_intent.py` | 파서·서비스·LLM 격리·멱등성·미지원 명령 테스트 |
| `tests/test_api.py` | capabilities 및 FOLLOW API 계약 테스트 |
| `docs/04_api_contract.md` | Action Intent와 capabilities 문서 동기화 |

## 8. 검증 결과

구현 완료 후 다음 품질 게이트를 실행했다.

```text
uv run pytest -q
122 passed, 2 skipped, 1 warning

uv run ruff check .
All checks passed!

uv run mypy src
Success: no issues found in 12 source files

uv lock --check
Resolved 48 packages
```

스킵된 테스트는 외부 PostgreSQL과 실제 OpenAI 설정이 필요한 기존 integration/live LLM
테스트다. 경고 1건은 FastAPI TestClient가 사용하는 Starlette/httpx deprecation 경고다.

기존 Router 100건, Recipe 30건, Lore 30건 평가 회귀도 전체 pytest에 포함되어 통과했다.

## 9. 후속 범위

- 실제 게임 캐릭터 FOLLOW/WAIT 실행
- 클라이언트 snapshot 최신성 검증
- 작업 시작·진행·완료·실패 이벤트
- `STOP_CURRENT_TASK`
- `GATHER_RESOURCE`
- 전투 명령
- 재질의 흐름
- 인벤토리와 제작 가능 여부 판정

이 서버는 행동을 실행하지 않는다. Action Intent를 발급하며 최종 실행 권한과 검증 책임은
클라이언트에 있다.
