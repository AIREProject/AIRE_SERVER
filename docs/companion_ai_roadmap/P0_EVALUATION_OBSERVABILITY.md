# CAI-P0. 평가 기준선과 관측성

- 상태: Ready
- 선행: 없음. Local SQLite와 `AI_MODE=mock`으로 착수 가능
- 후속: CAI-P1
- 기존 계획: M05-E01, G1 Mock/Schema
- 완료 상태: 실제 Gemma·배포 검증 전까지 `Local Deterministic Tests Passed`까지만 가능

## 목표

응답이 나쁜 이유를 "모델 성능"으로 추측하지 않고, 요청 하나가 어떤 분류·저장소·Provider·fallback을 거쳐 최종 응답에 도달했는지 재현 가능하게 만듭니다.

## 범위와 비범위

포함:

- 개인정보 없는 고정 평가 fixture
- 테스트 전용 정상·실패 LLM Provider
- 요청 단위 응답 출처와 fallback reason
- Provider 중립 평가 runner와 결과 리포트

제외:

- 실제 Gemma 성능 판정 또는 GPT 전환
- Prompt 대규모 개편과 Recipe 기능 수정(CAI-P1)
- 실제 대화 원문을 테스트 fixture로 복제

## 구현 순서

### CAI-P0-T01 평가 fixture 계약

- [ ] `tests/fixtures/companion_ai/`에 개인정보 없는 JSON fixture를 둡니다.
- [ ] fixture는 입력, 기대 intent/query mode, 허용 fact ID, 금지 사실, Command 허용 여부, 기대 fallback, DB side effect를 포함합니다.
- [ ] recipe 목록·상세·비교·후속 질문, 일반 질문, 감정·선호 공유, 지원 불가 요청을 포함합니다.
- [ ] 정상, timeout, malformed JSON, empty text, invalid command candidate를 별도 fixture로 둡니다.
- [ ] source 없는/타 scope memory candidate fixture는 CAI-P3 schema가 생긴 뒤 실행할 예약 fixture로만 기록합니다.
- [ ] 생성 대사의 exact match 대신 의미 계약을 assertion합니다.

### CAI-P0-T02 테스트 Provider와 평가 runner

- [ ] `LLMProvider` 경계를 유지한 `ScriptedLLMProvider`를 테스트에서만 주입합니다.
- [ ] `InvalidLLMProvider`로 timeout, schema 오류, 빈 응답과 금지 Candidate를 재현합니다.
- [ ] `MockLLMProvider`는 운영 안전 fallback 검증용으로 유지합니다.
- [ ] fixture를 Local/OpenAI provider에도 같은 입력으로 실행할 수 있는 runner를 추가합니다.
- [ ] 결과에는 fixture ID, provider/model/prompt version, 실행 시각과 pass/fail만 기록하고 원문·token은 남기지 않습니다.

### CAI-P0-T03 요청 telemetry

다음 필드를 요청 종료 시 JSON log 한 건으로 기록합니다.

| 분류 | 필드 |
|---|---|
| 이해 | `top_intent`, `query_mode`, `selected_route` |
| 근거 | `repository_match`, 선택적 memory reference 수량 |
| Provider | `provider`, `model_version`, `prompt_version`, 단계별 latency |
| 실패 | `provider_call_succeeded`, `provider_fallback_used`, schema/sanitizer 결과 |
| 결과 | `final_response_source`, `final_fallback_reason` |

- [ ] raw 사용자 텍스트, 인증 token, API key, Prompt 전문은 로그에 남기지 않습니다.
- [ ] `final_response_source`는 `game_repository`, `local_llm`, `openai`, `mock_fallback`, `fixed_fallback`, `validation_rejection` 중 하나입니다.
- [ ] configured provider와 실제 선택된 effective provider를 구분해 기록합니다.
- [ ] trace는 graph 내부 로그 호출 대신 reply/service 경계의 내부 `ResponseProvenance`로 모아 HTTP와 WebSocket에서 한 번만 기록합니다.
- [ ] HTTP 200이라도 fallback 여부가 telemetry에서 드러나야 합니다.
- [ ] timeout, invalid JSON, sanitizer 거부가 서로 다른 reason으로 기록됩니다.

### CAI-P0-T04 기준 리포트

- [ ] 분류 정확도, 핵심 질문 응답률, 무관 응답, 불필요한 거절, 환각, 불필요한 되묻기와 구조화 출력 실패율을 계산합니다.
- [ ] fixture별 실패 원인과 response source를 요약합니다.
- [ ] 리포트는 비식별 통계와 fixture ID만 포함합니다.
- [ ] 실제 Gemma 접근 전에는 `Live Gemma Verification Pending`으로 명시합니다.

## 변경 예상 위치

| 목적 | 파일 |
|---|---|
| Provider test double | `app/brain/llm.py` 또는 `tests/` 전용 helper |
| route/provenance seam | `app/brain/graph.py`, `app/brain/dialogue.py` |
| response metadata 조립 | `app/service.py`, `app/models.py` |
| 요청 종료 로그 | `app/logging.py`, `app/middleware.py` |
| 평가·관측성 검증 | `tests/test_llm.py`, `tests/test_companion_graph.py`, `tests/test_access_log.py`, 신규 P0 테스트 |

외부 Chat response에 telemetry를 노출하지 않습니다. 필요하면 테스트·관리자 경로에서만 확인하며, Provider 세부 정보는 UE/Web gameplay 분기에 사용하지 않습니다.

## 로컬 테스트 매트릭스

| 경우 | 기대 결과 |
|---|---|
| Scripted 정상 분류·대사 | route와 response source가 fixture와 일치 |
| repository 즉시 응답 | LLM 호출 없이 `game_repository` 기록 |
| provider timeout | 안전 fallback과 `provider_timeout` 기록 |
| malformed structured output | validation fallback과 `invalid_structured_output` 기록 |
| empty/제한 초과 대사 | sanitizer rejection reason 기록 |
| 요청 재시도 | P2 전에는 현재 비멱등 동작을 telemetry로 식별만 하고 exact-once를 주장하지 않음 |

## 완료 Gate

### Local Deterministic Tests Passed

- [ ] fixture, Scripted/Invalid Provider, runner가 동작합니다.
- [ ] 정상·invalid·timeout·fallback 경로가 모두 구분됩니다.
- [ ] 개인정보 없는 기준 리포트를 생성합니다.
- [ ] P1이 필요한 route/provenance 입력을 받을 수 있습니다.

### Live Gemma Verification Pending

- [ ] 실제 model revision, context length, quantization, license 기록
- [ ] 실제 JSON schema 성공률·한국어 응답 품질·latency 측정

위 Live 항목은 원격 서버 접근 전 완료로 표시하지 않습니다.
