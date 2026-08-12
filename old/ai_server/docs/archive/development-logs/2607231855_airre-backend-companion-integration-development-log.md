# 2607231855 AI_RE 인프라 + 마코 두뇌 단일 서버 통합 개발 기록

- 기록일: 2026-07-23 18:55
- 기록 유형: 아키텍처 통합 완료 기록
- 변경 범위: 저장소 전체 아키텍처 재편 — AI_RE Backend 인프라 도입, 마코 두뇌 이식,
  인프로세스 `AIService` 어댑터, 설정·의존성·툴링 병합, 옛 companion API 계층 은퇴, 문서 갱신
- 구현 기준: 통합 계획(단일 서버 = AI_RE 인프라 + 마코 두뇌), 사용자 확정 결정 3건
- API/스키마 버전: 공개 계약이 옛 `POST /v1/companion/message`에서 AI_RE의
  `POST /api/v1/chat`(chat request/response v1, AIService request v2 / result v1)로 전환
- 후속 범위: `Command.GatherResource` 계약 확장(채집 복원), 재질의 구조화, 메모리 연동,
  AI_RE 상류 인프라 변경분 수동 반영

## 1. 완료 상태 요약

두 백엔드를 하나의 배포 서버로 통합했다. `/home/mtvs-1/workspace/AI_RE/Backend`(디바이스
인증·페어링·SQLite/Alembic 영속화·멱등성·`/api/v1/chat` 계약·미들웨어·에러 envelope·구조화
로깅)를 인프라 뼈대로 도입하고, 이 저장소가 개발해 온 마코 두뇌(2단계 LLM 라우팅, 사실 기반
대사 생성, Mock/OpenAI/Local 공급자, recipes/lore)를 **인프로세스 `AIService` 구현체**로 붙였다.

핸드오프 설계의 책임 경계를 그대로 따른다. Backend가 인증·범위 검증·context 구성·결과
재검증·영속화를 소유하고, AI 측(마코 두뇌)이 `display_text`와 명령 후보 생성을 소유한다.
마코 두뇌는 `get_ai_service`의 `ai_mode="companion"` 분기 하나로 배선되며, AI_RE의
Router/DB/application 코드는 사실상 변경하지 않는다(교체 가능한 seam).

전체 116개 테스트가 통과하고, 신규 companion 코드는 ruff clean이며, 인증→ChatService→
CompanionAIService→재검증→응답의 실제 HTTP 경로를 end-to-end로 검증했다.

## 2. 확정된 결정

사용자와 방향을 확정한 뒤 진행했다.

1. **이 서버 단독 운영** — AI_RE를 별도 앞단으로 배포하지 않는다. 따라서 마코 두뇌는 별도
   HTTP 런타임(`AI_MODE=local`)이 아니라 인프로세스 `AIService`로 붙인다. (HTTP 홉 2회의
   지연은 같은 머신/LAN에서 무시 수준이고, 진짜 지연은 LLM 호출이라 방향 결정 요소가 아님을
   확인했다.)
2. **채집(gather) 보류** — AI_RE `CommandType`에 없는 채집은 1차 통합에서 구조화 명령을
   방출하지 않는다. 계약 확장은 후속.
3. **재질의·recipe·lore는 `display_text`로 흡수** — 별도 clarification 필드나 이벤트
   엔드포인트 없이 `POST /api/v1/chat` 단일 계약으로 통일한다.

## 3. 구현 범위와 주요 결정

### 인프라 스켈레톤 도입

AI_RE `Backend/app/` 전체, `migrations/`, `alembic.ini`, `Contracts/`와 인프라 테스트
(`test_{chat_api,pairing_api,credentials,migrations,request_safety,system,contracts,
mock_ai_service,local_runtime_ai_service}.py`)를 저장소로 도입했다. 패키지 레이아웃은 AI_RE의
`app` 구조를 채택했다. `Contracts` 참조 테스트의 경로 가정을 이 저장소 구조에 맞게
`parents[2]`→`parents[1]`로 조정했다.

### 마코 두뇌 이식

`llm/intent/command_intent/dialogue/recipes/lore` 6개 모듈을
`app/infrastructure/ai/companion/`으로 옮기고 import 경로만 조정했다. `.config`의 Settings 참조는
병합된 `app.settings`로, recipes/lore의 `DialogueFact`는 옛 `domain.py` 대신 신규
`companion/facts.py`로 연결했다. 라우팅 로직 자체는 변경하지 않았다.

### CompanionAIService 어댑터

`companion/service.py`에 `CompanionAIService.generate_chat(AIServiceRequest) → AIServiceResult`를
작성했다. 옛 `RequestService`의 2단계 라우팅 오케스트레이션을 재사용하되, 옛 wire 계약에
묶인 부분(`MessageResponse`/`Clarification`/`ErrorBody`/`client_context`/in-process 재질의 메모리)은
버렸다.

명령 매핑:

| 발화 의도 | command_candidate | 비고 |
|---|---|---|
| follow_player | `Command.Follow` | `allowed_commands`에 있을 때만 |
| wait | `Command.HoldPosition` | 〃 |
| stop_current_task | `Command.CancelCurrent` | 〃. 옛 `active_task.id` 불필요 |
| cancel / gather / unknown | 없음 | `display_text`만 |
| recipe / lore / conversation | 없음 | 사실 기반 `display_text` |

- `allowed_commands` 게이트: 매핑된 명령이 요청 allowlist에 없으면 후보를 방출하지 않는다.
  Backend `ChatService._validate_ai_result`가 allowlist 밖 명령을 `AIServiceInvalidOutput`으로
  거부하므로 두뇌 쪽에서 선제 필터한다.
- `CommandCandidate` 합성: `command_id=command-{uuid4()}`, `request_id` 에코, `issued_at=now(UTC)`,
  `expires_at=issued_at+ttl`.
- lore 조회 키(`location_id`)는 옛 `client_context.location_id` 대신 `game_context`에서 읽는다.
- 채집은 `resolve_gather`로 자연스러운 대사만 만들고 명령은 내지 않는다.
- `memory_candidates`는 항상 빈 배열. 예상치 못한 예외는 `AIServiceUnavailableError`로 변환한다.
- `display_text`는 마코 `render`의 200자 제한을 통과하며 AI_RE의 4000자 제한 안에 든다.

### 설정·의존성·툴링 병합

- `app/settings.py`에 `ai_mode` Literal에 `"companion"`을 추가하고 마코 LLM 설정 필드
  (`llm_provider`, `openai_*`, `local_llm_*`, `classify_*`, `dialogue_*`, `companion_prompt_version`,
  `companion_command_ttl_seconds`)를 추가했다.
- `app/api/dependencies/ai.py`의 `get_ai_service`에 `companion` 분기를 추가하고, 실제 선택될
  공급자 이름·모델을 결정하는 `_companion_provider_metadata`를 두었다.
- `pyproject.toml`을 AI_RE 기반(pythonpath, `[tool.uv] package=false`, sqlalchemy/alembic/aiosqlite/
  tzdata)으로 재작성하고 `openai`·`pytest-asyncio`·ruff·mypy를 더했다. Python은 양쪽 교집합인
  `>=3.13,<3.14`. mypy는 AI_RE 코드에 대량 에러가 나지 않도록 strict 대신 기본으로 두었다.
- `.env.example`을 두 백엔드 설정 병합본으로 재작성했다(`AI_MODE=companion` 기본).

### 옛 companion API 계층 은퇴

`src/ai_companion_server/` 패키지 전체(`main.py`/`domain.py`/`service.py` 포함), 옛 wire 계약
테스트(`tests/test_api.py`, `tests/test_service.py`), `tests/__init__.py`를 제거했다. 두뇌 모듈
테스트인 `test_dialogue.py`·`test_llm.py`는 import를 `app.infrastructure.ai.companion.*`와
`app.settings`로 재조준해 유지했다.

### 검증 중 발견·수정한 버그

companion 모드 스모크 테스트에서 `app/api/routes/system.py`의 `HealthResponse.ai_mode` Literal이
`"companion"`을 거부해 `/health`가 500을 반환하는 것을 발견하고 Literal에 `"companion"`을
추가했다. (기존 `test_system.py`는 mock 모드만 검사해 이 케이스를 놓쳤다.)

## 4. 변경 파일

| 파일 | 변경 내용 |
|---|---|
| `app/` (AI_RE 전체) | 인프라 스켈레톤 도입(신규) |
| `migrations/`, `alembic.ini`, `Contracts/` | Alembic 스키마·계약 자산 도입(신규) |
| `app/infrastructure/ai/companion/` | 두뇌 6개 모듈 이식 + `service.py`(CompanionAIService)·`facts.py`·`__init__.py`(신규) |
| `app/api/dependencies/ai.py` | `ai_mode="companion"` 분기와 공급자 메타데이터 결정 |
| `app/settings.py` | `companion` 모드와 마코 LLM 설정 필드 추가 |
| `app/api/routes/system.py` | `HealthResponse.ai_mode`에 `"companion"` 추가(버그 수정) |
| `pyproject.toml`, `.env.example`, `uv.lock` | 의존성·설정·툴링 병합 |
| `tests/test_companion_ai_service.py` | 발화→(대사, 명령 후보) 매핑 단위 테스트(신규) |
| `tests/test_companion_chat_integration.py` | CompanionAIService→ChatService 재검증 통과 검증(신규) |
| `tests/test_companion_chat_api.py` | 인증 포함 실제 HTTP 경로 end-to-end(신규) |
| `tests/test_*` (AI_RE 인프라) | 도입 + `Contracts` 경로 가정 조정 |
| `tests/test_dialogue.py`, `tests/test_llm.py` | import 재조준(두뇌 테스트 유지) |
| `src/`, `tests/test_api.py`, `tests/test_service.py` | 제거(옛 계약 은퇴) |
| `README.md`, `CLAUDE.md`, `AGENTS.md`, `docs/README.md`, `docs/current/01~05` | 새 아키텍처 반영, alembic 필수 명시, `docs/current` 레거시 배너 |

## 5. 검증 결과

```text
uv run pytest
116 passed, 1 warning

uv run ruff check app/infrastructure/ai/companion tests/test_companion_*.py
All checks passed!

uv run alembic upgrade head
20260715_0001 -> 20260720_0002 정상 적용

end-to-end (companion 모드, 인증된 GameClient):
POST /api/v1/chat  user_message="여기서 기다려"  allowed_commands=["Command.HoldPosition"]
→ 200, display_text="알겠어. 여기서 기다릴게.", command_candidates=[Command.HoldPosition], provider=mock
```

경고 한 건은 기존 Starlette TestClient의 `httpx` deprecation 경고로 이번 변경과 무관하다.
AI_RE 인프라 테스트(인증·페어링·멱등·마이그레이션·계약 fixture)는 그대로 통과한다.

## 6. Provenance와 미수행·후속

- AI_RE `app/`·`migrations/`·`Contracts/`·인프라 테스트는 상류 커밋 `421865a` 기준으로 복사했다.
  fork이므로 이후 AI_RE 인프라 변경분은 수동 반영이 필요하다. 통합 로직은
  `app/infrastructure/ai/companion/`에 격리해 상류 파일 diff를 최소화했다.
- 채집 복원은 공용 계약에 `Command.GatherResource`(Contracts JSON Schema + `CommandType` +
  UE 처리) 추가가 선행되어야 한다. 우리가 계약을 소유하므로 크로스팀 합의 없이 가능하다.
- 재질의 구조화(clarification)와 이벤트(task_completed/failed→대사)는 AI_RE 계약에 자리가 없어
  현재 `display_text`로 흡수한다. 필요 시 `session_id`/DB 기반 상태 또는 GameEvent API 구현 후
  재설계한다.
- 메모리 검색/저장(`retrieved_memories`/`memory_candidates`) 연동은 미구현이다.
- DB 스키마는 AI_RE 설계대로 앱 시작이 아니라 Alembic이 생성한다. 신선한 배포에서 서버 기동
  전 `alembic upgrade head`가 필수이며 이를 README/CLAUDE/AGENTS에 명시했다.
