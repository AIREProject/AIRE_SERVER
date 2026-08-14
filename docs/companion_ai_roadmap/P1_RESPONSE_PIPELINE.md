# CAI-P1. 응답 파이프라인 정상화

- 상태: Planned
- 선행: CAI-P0 Local Deterministic Tests Passed
- 후속: D1 Provider 결정, CAI-P2
- 기존 계획: M05-E01, M02 Chat baseline

## 목표

일반 질문과 레시피 목록 질문을 고정 "몰라" 응답으로 보내지 않고, 검증된 게임 사실 안에서 MAKO가 자연스럽고 안전하게 답하도록 만듭니다.

## 핵심 결정

- 게임 사실은 LLM이 만들지 않습니다. Repository가 stable ID 기반 사실을 반환합니다.
- LLM은 모호한 요청 해석과 검증된 사실의 표현만 담당합니다.
- 명령 수락 표현은 실제 Command Candidate가 존재할 때만 허용합니다.
- 이 단계에서는 운영 Provider를 Gemma 하나로 유지합니다. GPT 비교는 D1에서만 수행합니다.

## 구현 순서

### CAI-P1-T01 요청 목적과 Query Mode

- [ ] `TopIntent`의 기존 Command/Recipe/Enemy/Lore 경계를 유지하면서 일반 질문·일상·감정·선호 공유가 `unknown`으로 불필요하게 떨어지지 않게 합니다.
- [ ] `RecipeQueryMode`에 `ListKnown`, `Detail`, `Compare`, `Ambiguous`, `UnknownRecipe`를 둡니다.
- [ ] "레시피 알고 있는 거 있어?"를 `ListKnown`으로 해석해 허용된 목록을 반환합니다.
- [ ] "돌도끼 레시피 알려줘"를 stable recipe ID의 `Detail`로 해석합니다.
- [ ] 두 대상이 검증되었을 때만 `Compare`를 수행합니다.
- [ ] "그거 어떻게 만들어?"는 최근 문맥의 검증된 대상이 하나일 때만 해석하고, 아니면 하나의 구체적 질문을 합니다.
- [ ] unknown display name, recipe ID, 재료·수량·작업대는 추측하지 않습니다.

### CAI-P1-T02 Repository 응답과 fallback 정책

- [ ] Recipe repository에 목록·상세·비교용 검증 결과를 추가합니다.
- [ ] repository가 답할 수 있는 질문은 LLM 실패와 무관하게 안전한 사실 응답을 반환합니다.
- [ ] 지원 가능 질문을 generic `unsupported`로 보내지 않습니다.
- [ ] fallback reason에 따라 재시도 안내, 한 번의 명확화 질문, 근거 있는 미지원 안내를 구분합니다.
- [ ] "모른다"는 game data와 대화 문맥 모두 근거가 없을 때만 사용합니다.

### CAI-P1-T03 Persona·생성 검증

- [ ] MAKO의 성격 축, 질문에 답하는 원칙, 금기, 관계 단계별 어조를 versioned prompt에 명시합니다.
- [ ] 대사가 요청 목적과 허용된 fact/memory reference 밖으로 나가지 않는지 검증합니다.
- [ ] Candidate가 없으면 실행을 약속하거나 수락하는 문장을 차단합니다.
- [ ] sanitizer 실패와 Provider 실패를 같은 문장으로 숨기지 않고 P0 provenance를 남깁니다.
- [ ] 반복 fixture에서 동일한 안전 결론과 response source를 유지합니다.

## 변경 예상 위치

| 목적 | 파일 |
|---|---|
| intent/query mode | `app/brain/intent.py`, `app/brain/llm.py` |
| graph state와 route | `app/brain/graph.py` |
| recipe 조회 | `app/brain/recipes.py`, `app/gamedata/dataset.py` |
| 대사/검증/prompt | `app/brain/dialogue.py`, `app/brain/llm.py` |
| 검증 | `tests/test_recipes.py`, `tests/test_companion_graph.py`, `tests/test_llm.py`, P0 fixture |

## 필수 fixture

| 입력 | 기대 처리 |
|---|---|
| 레시피 알고 있는 거 있어? | `recipe/ListKnown`, 고정 목록, fallback 없음 |
| 돌도끼 레시피 알려줘 | `recipe/Detail`, stone axe 사실만 사용 |
| 돌도끼와 돌곡괭이 비교해줘 | `recipe/Compare`, `recipe-3`과 `recipe-4`가 모두 있을 때만 비교 |
| 그거 어떻게 만들어? | 최근 대상 하나일 때 Detail, 아니면 명확화 |
| 존재하지 않는 제작법 | 추측 금지, 근거 있는 미지원 안내 |
| 따라와 | command route와 Candidate 검증 유지 |
| 일반 질문/감정 표현 | greeting 고정 응답 대신 요청 핵심에 반응 |

## 완료 Gate

### Local Deterministic Tests Passed

- [ ] 레시피 목록·상세·비교 fixture가 100% 통과합니다.
- [ ] 허용되지 않은 game fact와 Command false acceptance가 0건입니다.
- [ ] 평가셋 기준 핵심 질문 응답률 90% 이상, 불필요한 거절 5% 이하 목표를 만족합니다.
- [ ] provider/schema/sanitizer fallback의 reason과 source가 P0 telemetry에 남습니다.

### Live Gemma Verification Pending

- [ ] 실제 Gemma에서 한국어 표현·persona·지시 준수·응답 편차 측정
- [ ] D1에서 Gemma와 OpenAI를 같은 fixture로 비교

실제 모델 품질이 기준 미달일 때만 Provider를 변경합니다. P1 구현만으로 GPT 사용을 확정하지 않습니다.
