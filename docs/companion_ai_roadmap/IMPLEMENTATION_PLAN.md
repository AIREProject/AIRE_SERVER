# Companion AI 구현 계획

- 상태: Planned
- 작성일: 2026-08-14
- 구현 원칙: 로컬 결정론 검증 → 현재 응답 품질 → 영속 출처 → 기억 → 관계·멀티플랫폼 → 실제 LLM·배포 검증 → 운영·확장
- Backend 기본 Provider: Local LLM(Gemma), 품질 Gate 실패 시 조건부 OpenAI 전환

## 1. 목표와 완료 정의

이 계획의 핵심 완료 상태는 다음과 같습니다.

> 사용자가 모바일에서 직접 공유한 사실이나 선호가 검증된 출처와 함께 저장되고, 다음 게임 세션의 관련 상황에서만 MAKO의 자연스러운 대화에 반영되며, 사용자가 삭제하면 즉시 다시 사용되지 않는다.

동시에 현재의 기본 대화가 다음 조건을 만족해야 합니다.

- 일반적인 질문이 불필요하게 `unknown`으로 빠지지 않습니다.
- "레시피 알고 있는 거 있어?"와 "돌도끼 레시피 알려줘"를 구분해 답합니다.
- 게임 사실은 DB 근거 없이 생성하지 않습니다.
- Local LLM 실패, 검증 실패, Mock/fixed fallback을 요청 단위로 식별할 수 있습니다.
- Backend나 LLM 장애 시 UE 로컬 AI는 계속 동작합니다.

## 2. 전체 의존 순서

```text
CAI-P0 평가·관측성
  └─ CAI-P1 응답 라우팅·대화 품질
       ├─ D1 Gemma 유지 / GPT 전환 판정
       └─ CAI-P2 Message·Event 영속화
            └─ CAI-P3 출처 기반 장기기억
                 └─ CAI-P4 관계 상태·Cross-device 수직 슬라이스
                      └─ CAI-P5 UE/Web 사용자 제어·동기화
                           └─ CAI-P6 운영 안정화·두 번째 게임 Adapter
```

P0와 P1이 통과하기 전 장기기억을 실제 사용자 대화에 활성화하지 않습니다. 잘못된 라우팅과 응답을 기억에 연결하면 오류가 장기적으로 증폭되기 때문입니다.

각 단계의 파일·테스트·계약 수준 작업은 다음 실행 문서에서 관리합니다. 이 문서는 전체 의존성과 Gate만 관리합니다.

| 단계 | 실행 문서 | 기존 AIRE 계획 연결 |
|---|---|---|
| P0 | [P0_EVALUATION_OBSERVABILITY.md](P0_EVALUATION_OBSERVABILITY.md) | M05-E01, G1 Mock/Schema |
| P1 | [P1_RESPONSE_PIPELINE.md](P1_RESPONSE_PIPELINE.md) | M05-E01, M02 Chat baseline |
| P2 | [P2_MESSAGE_EVENT.md](P2_MESSAGE_EVENT.md) | M05-E02, M04 Event/Result |
| P3 | [P3_SOURCE_MEMORY.md](P3_SOURCE_MEMORY.md) | M05-E03, G5 Persistent Memory |
| P4 | [P4_RELATIONSHIP_CROSS_DEVICE.md](P4_RELATIONSHIP_CROSS_DEVICE.md) | M05-E04, M06, G6 Core Bond Demo |
| P5 | [P5_CLIENT_INTEGRATION.md](P5_CLIENT_INTEGRATION.md) | M02/M04/M06 |
| P6 | [P6_OPERATION_PLATFORM.md](P6_OPERATION_PLATFORM.md) | M07, G7 Recovery |

### 2.1 로컬 시뮬레이션 우선 실행 기조

원격 Backend와 실제 Gemma에 실시간으로 접속할 수 없어도 구현은 계속합니다. 현재 `LLMProvider` 경계를 유지하고 테스트에서 결과를 주입합니다.

```text
Test Request
  → ScriptedLLMProvider 또는 InvalidLLMProvider
  → Chat routing / Game repository
  → Message·Event·Memory·Relationship logic
  → Local SQLite
  → Semantic assertion
```

테스트 Provider의 역할은 다음과 같이 분리합니다.

| Provider | 용도 |
|---|---|
| `MockLLMProvider` | 운영 장애 시 안전 fallback 동작 확인 |
| `ScriptedLLMProvider` | 입력별 정상 구조화 결과를 반환하는 Backend 기능 테스트 |
| `InvalidLLMProvider` | timeout, malformed JSON, empty text, invalid candidate 방어 테스트 |
| `LocalLLMProvider` | 접근 가능 시 실제 Gemma 통합·품질 검증 |
| `OpenAIProvider` | D1 비교가 필요할 때만 동일 평가셋으로 검증 |

제안 테스트 자산 위치:

```text
tests/fixtures/companion_ai/
tests/test_companion_ai_evaluation.py
tests/test_companion_ai_observability.py
```

fixture는 최종 생성 문장 하나를 정답으로 고정하지 않고 다음 의미 계약을 기록합니다.

- 기대 intent와 query mode
- 허용 game fact ID와 memory source ID
- Command Candidate 허용 여부
- fallback 허용 여부와 기대 reason
- 반드시 포함할 의미와 포함하면 안 되는 사실
- 예상 DB side effect와 idempotency 결과

### 2.2 완료 상태 표기

실제 LLM이 없어도 다음 구현 단계로 진행할 수 있도록 상태를 분리합니다.

| 상태 | 의미 |
|---|---|
| `Implementation Complete` | 코드와 migration이 작성됨 |
| `Local Deterministic Tests Passed` | fixture와 테스트 Provider 기반 검증 통과 |
| `Live Gemma Verification Pending` | 실제 Gemma 품질·지연·구조화 출력 미검증 |
| `Deployed Integration Pending` | 배포 OpenAPI와 실제 UE/Web 종단 검증 미완료 |
| `Done` | 문서에 정의된 실제 LLM·배포 포함 전체 Gate 통과 |

운영 환경 결과를 예상값으로 만들어 `Done` 처리하지 않습니다. 단, `Local Deterministic Tests Passed`까지 확보하면 후속 DB·Memory·Client 계약 구현은 계속할 수 있습니다.

## 3. CAI-P0 평가 기준선과 관측성

### CAI-P0-T01 고정 평가셋

#### 구현

- [ ] 실제 Transcript 원본은 읽기 전용으로 보존합니다.
- [ ] 개인정보, 인증정보, 테스트성 발화를 제거합니다.
- [ ] 150~200개 대표 발화를 선정합니다.
- [ ] 각 발화에 기대 route, query mode, 허용 사실, 금지 응답을 라벨링합니다.
- [ ] 레시피 목록·상세·비교·후속 질문을 별도 fixture로 둡니다.
- [ ] 일반 대화, 감정 표현, 선호 공유, 회상 질문과 지원 불가 요청을 포함합니다.
- [ ] Local/OpenAI Provider가 같은 입력으로 평가되도록 Provider 중립 runner를 둡니다.
- [ ] 입력별 구조화 결과를 반환하는 `ScriptedLLMProvider`를 테스트 전용으로 둡니다.
- [ ] timeout, malformed output, 빈 응답과 invalid candidate를 반환하는 `InvalidLLMProvider`를 테스트 전용으로 둡니다.

#### 검증

- [ ] 평가 결과에 model, model revision, prompt version과 실행 시각이 남습니다.
- [ ] 분류 정확도, 핵심 질문 응답률, 무관 응답, 불필요한 거절, 환각과 되묻기 비율을 계산합니다.
- [ ] 평가셋 원문에 개인 식별 정보가 없습니다.
- [ ] 생성 문장 exact match가 아니라 의미 계약과 허용 근거를 검증합니다.

### CAI-P0-T02 요청 단위 응답 출처 로그

#### 구현

- [ ] `top_intent`, `query_mode`, `selected_route`를 기록합니다.
- [ ] `repository_match`와 사용한 stable fact ID를 기록합니다.
- [ ] `provider_call_succeeded`, `provider_fallback_used`를 기록합니다.
- [ ] JSON Schema와 dialogue sanitizer 실패를 구분합니다.
- [ ] `final_response_source`와 `final_fallback_reason`을 기록합니다.
- [ ] provider/model/prompt version과 단계별 latency를 기록합니다.
- [ ] 원문 대화와 API key는 운영 로그에 남기지 않습니다.

`final_response_source` 최소 후보:

```text
game_repository
local_llm
openai
mock_fallback
fixed_fallback
validation_rejection
```

#### 검증

- [ ] HTTP 200이어도 실제 Provider fallback 여부를 판별할 수 있습니다.
- [ ] "레시피 알고 있어?" 한 요청이 실패한 단계를 로그만으로 재구성할 수 있습니다.
- [ ] timeout, invalid JSON, sanitizer 거부 테스트가 서로 다른 reason을 남깁니다.

### CAI-G0 완료 Gate

- [ ] 변경 전 기준 리포트가 생성됩니다.
- [ ] 실제 모델 정체, revision, context length, quantization과 license가 기록됩니다.
- [ ] Local 성공과 Mock/fixed fallback이 분리 측정됩니다.

실제 Gemma에 접근할 수 없으면 두 번째 항목과 실제 Provider 측정은 `Live Gemma Verification Pending`으로 유지합니다. Scripted/Invalid Provider 기반 로컬 Gate를 통과하면 P1 이후 구현은 계속할 수 있습니다.

예상: 3~5 작업일

## 4. CAI-P1 현재 응답 품질 정상화

### CAI-P1-T01 요청 목적과 Recipe Query Mode

#### 구현

- [ ] `conversation`을 인사·감사에 한정하지 않고 질문, 일상, 감정, 선호 공유, 도움 요청을 포괄하도록 재정의합니다.
- [ ] 명령과 정보 질문을 계속 분리합니다.
- [ ] Recipe에 `ListKnown`, `Detail`, `Compare`, `Ambiguous`, `UnknownRecipe` mode를 둡니다.
- [ ] "레시피 알고 있어?"는 현재 알려진 레시피 목록을 조회합니다.
- [ ] "돌도끼 레시피 알려줘"는 stable recipe ID의 상세 사실을 조회합니다.
- [ ] "그거 어떻게 만들어?"는 검증된 최근 대상이 하나일 때만 후속 문맥을 사용합니다.
- [ ] 모호한 경우 하나의 구체적인 질문만 반환합니다.

#### 검증

- [ ] 제작법 질문과 제작 명령이 섞이지 않습니다.
- [ ] 목록 요청이 특정 아이템 명칭 없이 성공합니다.
- [ ] unknown display name이나 recipe ID를 추측하지 않습니다.
- [ ] 게임 DB 결과가 없으면 재료·수량·작업대를 생성하지 않습니다.

### CAI-P1-T02 Fallback·Persona·응답 검증 정책

#### 구현

- [ ] "모른다"는 검증된 근거가 없을 때만 사용합니다.
- [ ] 지원 가능 질문을 generic unsupported 응답으로 보내지 않습니다.
- [ ] 실패 이유에 따라 짧은 재시도 안내, 구체적 되묻기, 안전 fallback을 구분합니다.
- [ ] MAKO의 성격 축, 관계 단계별 어조, 금기, 질문 응답 원칙을 Prompt에 명시합니다.
- [ ] Command Candidate가 없으면 명령을 수락한 표현을 금지합니다.
- [ ] 생성 대사가 요청 핵심과 사용한 사실 범위를 벗어나지 않는지 검증합니다.
- [ ] 검증 실패 시 동일한 모호한 거절을 반복하지 않습니다.

#### 검증

- [ ] 핵심 질문 응답률을 평가 리포트에 포함합니다.
- [ ] 불필요한 "몰라/안 돼" 비율을 측정합니다.
- [ ] 실행되지 않는 명령을 수락하는 대사가 없습니다.
- [ ] 게임 사실 환각이 없습니다.
- [ ] 동일 fixture를 반복해 응답 편차를 기록합니다.

### CAI-G1 완료 Gate

초기 목표치는 평가셋 확정 시 조정할 수 있지만 조용히 낮추지 않습니다.

- [ ] 핵심 질문 응답률 90% 이상
- [ ] 불필요한 거절 5% 이하
- [ ] 구조화 출력 실패 1% 이하
- [ ] 검증된 게임 사실 환각 0건
- [ ] Command false acceptance 0건
- [ ] 레시피 목록·상세·비교 필수 fixture 100% 통과

예상: 1~2주

## 5. D1 LLM Provider 결정 Gate

P1 완료 후 같은 평가셋으로 Local Gemma와 승인된 OpenAI 모델을 개발 환경에서 각각 실행합니다. 운영 요청을 양쪽에 동시에 보내지 않습니다.

실제 Provider 접근 전에는 D1을 결정하지 않습니다. 그동안 Local Gemma 유지가 기본 설정이며, 아래 항목은 `Live Gemma Verification Pending`으로 기록합니다.

### Gemma 유지

- [ ] CAI-G1을 통과합니다.
- [ ] 한국어 지시 준수와 구조화 출력 실패율이 허용 범위입니다.
- [ ] 서버 GPU에서 목표 지연과 동시 요청 수를 만족합니다.
- [ ] 모델 card, license와 revision을 재현할 수 있습니다.

### OpenAI 전환 검토

다음 중 하나가 반복되면 전환 후보로 올립니다.

- [ ] 구조 개선 후에도 평범한 한국어 요청을 반복 오해합니다.
- [ ] JSON Schema 또는 instruction-following 실패가 Gate를 넘습니다.
- [ ] 짧은 대화 문맥과 MAKO persona 유지가 기준 미달입니다.
- [ ] 같은 평가셋에서 OpenAI 결과가 유의미하게 우수합니다.

전환은 `LLM_PROVIDER` 설정과 Backend Provider adapter 안에서 수행하며 UE/Web Chat 계약을 변경하지 않습니다.

## 6. CAI-P2 Conversation·Message·GameEvent 영속화

### CAI-P2-T01 Canonical Message 저장

#### 구현

- [ ] Conversation과 Message migration을 추가합니다.
- [ ] stable `conversation_id`, `message_id`와 idempotency key를 저장합니다.
- [ ] profile/save/companion scope를 모두 포함합니다.
- [ ] `surface`, `source_mode`, RealWorld/GameWorld 시간을 분리합니다.
- [ ] 재시도 요청이 Message와 LLM side effect를 중복 생성하지 않게 합니다.

### CAI-P2-T02 검증된 Event·Command Result

#### 구현

- [ ] versioned GameEvent schema와 저장소를 추가합니다.
- [ ] `event_id`와 `operation_id`를 멱등 처리합니다.
- [ ] profile/save/companion/device 범위를 검증합니다.
- [ ] payload type allowlist와 크기 제한을 적용합니다.
- [ ] Command Result가 원래 Candidate/operation과 연결되는지 검증합니다.
- [ ] 제안 endpoint를 구현 계약에 반영한 뒤에만 배포합니다.

제안 경계이며 현재 호출 가능한 계약이 아닙니다.

```text
POST /api/v1/events
POST /api/v1/command-results
```

### CAI-G2 완료 Gate

- [ ] 서버 재시작 후 Message/Event가 유지됩니다.
- [ ] 동일 idempotency key 재시도에 side effect가 한 번만 발생합니다.
- [ ] 다른 profile/save/companion 출처를 참조할 수 없습니다.
- [ ] InGame과 Offline 시간이 혼합되지 않습니다.

예상: 2~3주

## 7. CAI-P3 출처 기반 장기기억

### CAI-P3-T01 Memory Candidate와 승격

#### 구현

- [ ] Memory에 `profile_id`, `save_slot_id`, `companion_id` scope를 저장합니다.
- [ ] `memory_type`, `source_ids`, `source_mode`, `occurred_at`, `status`를 저장합니다.
- [ ] type은 `ProfileFact`, `Preference`, `Episode`, `Promise`, `RelationshipEvidence` allowlist로 제한합니다.
- [ ] 원본 Message/Event의 존재, scope와 직접 발화 여부를 Backend가 검증합니다.
- [ ] LLM의 감정 해석이나 추론을 사용자 사실로 저장하지 않습니다.
- [ ] 게임 레시피와 현재 gameplay state를 사용자 장기기억으로 저장하지 않습니다.
- [ ] 유사·충돌 기억의 병합, 갱신, 보류 정책을 정의합니다.

### CAI-P3-T02 검색·망각·환각 제어

#### 구현

- [ ] scope, type, 시간, 중요도와 현재 질문 관련성을 함께 ranking합니다.
- [ ] 오래된 기억은 시간 감쇠를 적용합니다.
- [ ] 중요한 기억과 사용자가 고정한 기억은 별도 보존 규칙을 둡니다.
- [ ] 반복 회상 횟수만으로 기억 중요도가 무제한 상승하지 않게 합니다.
- [ ] 낮은 점수 기억은 삭제가 아니라 archive 후보로 전환합니다.
- [ ] 관련 상위 소수 기억만 Prompt에 전달합니다.
- [ ] 기억은 대화 참고 정보이며 게임 사실이나 Command 권위로 승격하지 않습니다.
- [ ] 회상 결과에 내부 memory/source reference를 추적합니다.

### CAI-P3-T03 사용자 기억 제어

#### 구현

- [ ] 사용자 범위의 기억 목록과 상세 조회를 제공합니다.
- [ ] 허용 범위 안에서 수정·삭제·전체 초기화를 제공합니다.
- [ ] 삭제 transaction이 검색, Prompt, cache와 background 작업에 즉시 전파됩니다.
- [ ] 삭제된 원문 Transcript를 재증류해 기억이 부활하지 않게 tombstone/cursor 정책을 둡니다.

제안 경계이며 현재 호출 가능한 계약이 아닙니다.

```text
GET    /api/v1/memories
PATCH  /api/v1/memories/{memory_id}
DELETE /api/v1/memories/{memory_id}
POST   /api/v1/memories/reset
```

### CAI-G3 완료 Gate

- [ ] 출처 없는 후보가 저장되지 않습니다.
- [ ] 타 scope source와 기억을 사용할 수 없습니다.
- [ ] 서버 재시작 전후 같은 기억을 회상합니다.
- [ ] 관련 없는 질문에서 임의의 과거 기억을 언급하지 않습니다.
- [ ] 삭제 직후 검색과 대사에서 기억이 사라집니다.
- [ ] LLM unavailable 상태에서도 거짓 기억을 생성하지 않습니다.

예상: 3~5주

## 8. CAI-P4 관계 상태와 Cross-device 수직 슬라이스

### CAI-P4-T01 관계 의미 상태

#### 구현

- [ ] 숫자 친밀도 대신 `Low`, `Growing`, `High` 등 제한된 의미 상태를 정의합니다.
- [ ] 상태 변경은 검증된 Event/Message 근거와 Backend 규칙으로만 수행합니다.
- [ ] 반복 채팅만으로 관계가 무제한 상승하지 않게 cooldown과 cap을 둡니다.
- [ ] LLM은 상태를 변경하지 않고 대사 어조에만 사용합니다.
- [ ] 관계 상태를 전투 수치나 GAS 보정에 직접 사용하지 않습니다.
- [ ] 사용자가 숫자 점수를 보지 않도록 UI 노출을 제한합니다.

### CAI-P4-T02 핵심 종단 시나리오

#### 기준 시나리오

```text
1. 모바일에서 사용자가 "난 밤에 혼자 다니는 게 무서워"라고 직접 말한다.
2. Message가 RealWorld source와 stable ID로 저장된다.
3. 검증된 Preference/ProfileFact가 같은 source ID로 승격된다.
4. 다음 게임 세션에서 UE가 밤 지역 진입 Event를 전송한다.
5. Backend가 관련 기억만 검색한다.
6. MAKO가 과거형으로 자연스럽게 회상한다.
7. 실제 Follow/보호 행동은 UE Command Gateway가 다시 검증한다.
8. 사용자가 기억을 삭제하면 다음 세션에서 다시 언급하지 않는다.
```

#### 검증

- [ ] 다른 save/companion에서는 회상되지 않습니다.
- [ ] 낮 지역이나 관련 없는 질문에서는 억지로 언급하지 않습니다.
- [ ] RealWorld 사건을 현재 GameWorld 사건처럼 표현하지 않습니다.
- [ ] Backend 또는 LLM 장애 중 UE 로컬 행동은 유지됩니다.
- [ ] 서버·클라이언트 재시작을 포함해 같은 시나리오를 3회 반복합니다.

### CAI-G4 완료 Gate

- [ ] 모바일 발화 → 기억 → 다음 인게임 관련 대화가 증명됩니다.
- [ ] UE Event → 관계 근거 → 다음 모바일/게임 대화가 증명됩니다.
- [ ] 기억 삭제가 양쪽 surface에 전파됩니다.
- [ ] 사용한 memory/source ID를 내부 감사 로그에서 추적할 수 있습니다.

예상: 2~4주

## 9. CAI-P5 UE/Web 연결

### Web

- [ ] 기존 Chat 요청·응답을 호환 유지합니다.
- [ ] Memory 목록, 출처, 수정, 삭제와 전체 초기화 화면을 구현합니다.
- [ ] 클라우드 LLM 사용과 기억 저장 범위를 안내합니다.
- [ ] 네트워크 단절 시 메시지를 전송 대기로 표시하고 재연결 후 멱등 전송합니다.
- [ ] 모바일 Chat 응답의 Command Candidate로 UE를 직접 변경하지 않습니다.

### UE

- [ ] Event와 Command Result Outbox를 추가합니다.
- [ ] stable entity ID와 operation correlation을 유지합니다.
- [ ] 중복, 만료, 잘못된 scope와 late callback을 거부합니다.
- [ ] Backend 장애 시 기존 Companion/StateTree/GAS 동작을 유지합니다.
- [ ] 선택적인 memory reference 필드를 gameplay 권위로 사용하지 않습니다.

### 계약 영향

| 영역 | 변경 수준 | 방침 |
|---|---|---|
| 기존 Chat request | 최소 | 가능한 한 필드 유지 |
| 기존 Chat response | additive | 선택 필드만 추가 |
| Event | 신규 | 별도 endpoint와 schema version |
| Command Result | 신규 | operation idempotency 필수 |
| Memory | 신규 | 사용자 scope CRUD |
| Provider 설정 | Backend 전용 | UE/Web 변경 없음 |

Unreal C++ 변경은 자동 빌드하지 않습니다. 사용자가 Development Editor build, Automation과 PIE를 실행해 검증합니다.

예상: 2~3주

## 10. CAI-P6 운영 안정화와 타 게임 확장

### 데이터베이스와 운영

- [ ] 단일 사용자 데모는 서버 측 SQLite를 유지합니다.
- [ ] 다중 사용자·다중 worker 전 PostgreSQL 전환 여부를 결정합니다.
- [ ] DB를 UE/Web에 직접 노출하지 않습니다.
- [ ] migration rollback과 backup/restore를 검증합니다.
- [ ] HTTPS, token rotation, 개인정보 삭제와 민감 로그 필터를 적용합니다.
- [ ] LLM latency, fallback, 비용, memory retrieval 품질 지표를 운영 대시보드에 연결합니다.
- [ ] Backend 중단 시 모바일은 실패를 성공처럼 표시하지 않고 전송 대기 또는 명시적 오류로 처리합니다.

### 두 번째 게임 Adapter Pilot

첫 게임의 CAI-G4가 통과한 뒤에만 착수합니다.

- [ ] Identity/Capability
- [ ] Context Provider
- [ ] Command Resolver
- [ ] Event Sink
- [ ] Snapshot/Outbox
- [ ] `game_id`, `content_version`, `schema_version`과 namespaced stable ID

공통화 대상은 HTTP transport, schema validation, stable ID와 operation correlation으로 제한합니다. 게임별 State/AI/Inventory와 실행 판정은 각 게임 adapter가 소유합니다.

예상: 3~6주

## 11. 전체 예상 범위

한 명의 집중 개발 기준이며 외부 배포와 UE 사용자 검증 대기 시간은 별도입니다.

| 구간 | 예상 |
|---|---:|
| P0 평가·관측성 | 3~5일 |
| P1 응답 품질 | 1~2주 |
| D1 Provider 판정 | 2~3일 |
| P2 Message·Event | 2~3주 |
| P3 장기기억 | 3~5주 |
| P4 관계·Cross-device | 2~4주 |
| P5 UE/Web | 2~3주 |
| P6 운영·Adapter | 3~6주 |

- 대화 품질 개선 MVP: P0~P1
- "나를 기억하는 동료" 핵심 데모: P0~P5
- 상용 운영과 타 게임 확장 기반: P0~P6
- 전체 예상: 약 12~20 집중 개발 주

## 12. 구현 시 변경 예상 위치

| 목적 | 우선 확인 위치 |
|---|---|
| Provider·Prompt·구조화 출력 | `app/brain/llm.py` |
| 라우팅·노드·fallback | `app/brain/graph.py`, `app/brain/intent.py` |
| 작업기억·대화 오케스트레이션 | `app/brain/store.py`, `app/brain/companion.py` |
| 기억 모델·ranking | `app/brain/memory.py`, `app/episodic_memory_store.py` |
| DB schema | `app/db/models.py`, `migrations/versions/` |
| 외부 API | `app/models.py`, `app/routes/` |
| 설정·Provider 선택 | `app/settings.py`, `app/service.py` |
| Backend 검증 | `tests/test_companion_graph.py`, `tests/test_companion_chat_api.py`, `tests/test_long_term_memory.py` 및 신규 계약 테스트 |
| UE/Web 계약 | `AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md`와 각 client adapter |

## 13. 작업 시작 규칙

각 Task는 다음 순서로 수행합니다.

1. 실패를 재현하는 fixture/test를 먼저 추가합니다.
2. 현재 배포 OpenAPI와 source contract 차이를 확인합니다.
3. 최소 구현으로 테스트를 통과시킵니다.
4. timeout, invalid, duplicate, unavailable과 restart 경로를 검증합니다.
5. Backend 모델·migration·문서·UE/Web DTO가 영향을 받으면 함께 동기화합니다.
6. Gate 근거가 없으면 체크박스와 상태를 완료로 바꾸지 않습니다.
7. 실제 LLM이 필요한 항목은 `Live Gemma Verification Pending`으로 남기고 예상 결과로 대체 완료하지 않습니다.

첫 착수 Task는 **CAI-P0-T01 고정 평가셋**과 **CAI-P0-T02 요청 단위 응답 출처 로그**입니다.
