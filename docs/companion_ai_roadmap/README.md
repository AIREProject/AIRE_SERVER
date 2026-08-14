# Companion AI 품질·기억·멀티플랫폼 개선 로드맵

- 상태: Planned
- 작성일: 2026-08-14
- 대상: `AIRE_SERVER` Chat/Companion 파이프라인과 이에 연결되는 UE/Web 계약
- 연계 계획: `M05 Local LLM & Relationship Memory`, `M06 Cross-device Bond Demo`

## 1. 목적

이 폴더는 다음 문제를 하나의 실행 순서로 관리합니다.

1. 기본 챗봇보다 낮게 느껴지는 현재 응답 품질
2. 과도한 "몰라", "안 돼", "다른 걸 하자" 응답
3. 일반적인 레시피 목록 질문을 처리하지 못하는 좁은 요청 분류
4. Local LLM 실패와 Mock/fixed fallback을 구분하기 어려운 관측성
5. 출처·삭제·범위가 보장되지 않는 후보 장기기억
6. 관계 변화가 실제 기억과 행동 근거에 연결되지 않은 상태
7. 모바일 대화가 다음 인게임 MAKO 대화에 이어지지 않는 상태
8. 타 게임 연동과 상용 운영으로 확장하기 어려운 계약·운영 경계

세부 작업과 완료 Gate는 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)를 따릅니다.

## 실행 문서

| 단계 | 문서 | 상태 | 핵심 산출물 |
|---|---|---|---|
| CAI-P0 | [평가·관측성](P0_EVALUATION_OBSERVABILITY.md) | Ready | fixture, 테스트 Provider, 응답 출처 로그 |
| CAI-P1 | [응답 파이프라인](P1_RESPONSE_PIPELINE.md) | Planned | 요청 목적, Recipe Query Mode, fallback 정책 |
| CAI-P2 | [Message·Event](P2_MESSAGE_EVENT.md) | Planned | canonical 원본, 멱등 Event/Result 계약 |
| CAI-P3 | [출처 기반 기억](P3_SOURCE_MEMORY.md) | Planned | source 검증, 검색·망각, 사용자 기억 제어 |
| CAI-P4 | [관계·Cross-device](P4_RELATIONSHIP_CROSS_DEVICE.md) | Planned | 관계 의미 상태와 핵심 유대 수직 슬라이스 |
| CAI-P5 | [UE/Web 연동](P5_CLIENT_INTEGRATION.md) | Planned | Event/Result Outbox, Memory UI, 계약 동기화 |
| CAI-P6 | [운영·플랫폼](P6_OPERATION_PLATFORM.md) | Planned | 복구·보안·지표, 두 번째 게임 Adapter Pilot |

## 2. 현재 판단

현재 응답 이탈은 Local LLM의 성능만으로 설명할 수 없습니다.

| 문제 | 현재 구현의 영향 | 우선 해결 방향 |
|---|---|---|
| 좁은 최상위 의도 | 일반 질문이 `unknown` 또는 부적절한 경로로 이동 | 실제 요청 목적 중심으로 라우팅 확장 |
| 특정 명칭 의존 레시피 조회 | "레시피 알고 있어?"가 특정 레시피를 찾지 못해 고정 실패 응답 | 목록·상세·비교·모호 요청을 분리 |
| 불투명한 fallback | Local 호출 실패가 정상 HTTP 응답과 섞임 | 요청 단위 응답 출처와 실패 이유 기록 |
| 짧은 작업기억 | 최근 3왕복만으로 장기적인 동료 경험이 약함 | 영속 Message/Event와 출처 기반 기억 구현 |
| 후보 기억의 약한 출처 | LLM 해석이 사용자 사실처럼 남을 위험 | 원본 `message_id`/`event_id` 검증 후 승격 |
| 사용자 기억 제어 부재 | 잘못된 기억을 사용자가 고칠 수 없음 | 조회·수정·삭제·초기화 API와 UI 제공 |
| 관계 상태 부재 | 기억이 있어도 관계 변화로 이어지지 않음 | 검증된 Event 기반 의미 상태로 관리 |
| Event/Result 왕복 부재 | 모바일과 게임 경험의 인과관계를 입증하기 어려움 | 멱등 Event·Command Result 계약 추가 |

## 3. 고정 결정

### 3.1 LLM Provider

- 운영 요청 하나에서 Gemma와 GPT를 동시에 호출하지 않습니다.
- 초기 기본 Provider는 현재 Local LLM(Gemma)을 유지합니다.
- 먼저 라우팅, fallback, Prompt, 게임 사실 조회와 관측성을 개선합니다.
- 같은 고정 평가셋에서 Gemma가 품질 Gate를 통과하지 못할 때만 OpenAI Provider를 운영 후보로 전환합니다.
- Provider 선택은 Backend 설정에만 존재하며 UE/Web은 Provider API key나 원본 응답 형식을 알지 않습니다.
- Mock은 장애 복구용이며 실제 모델 성공으로 표시하지 않습니다.

### 3.2 데이터 권위

- 레시피, 아이템, 적, 현재 게임 상태와 Command 실행 가능 여부는 LLM이 아니라 게임 데이터와 UE가 결정합니다.
- 장기기억과 관계 상태의 권위는 Backend DB가 가집니다.
- 게임 PC에는 SaveGame, 최근 캐시, 전송 대기 Outbox만 둡니다.
- LLM 출력은 항상 검증되지 않은 Candidate로 취급합니다.

### 3.3 오프라인 의미

- 게임 PC가 꺼져 있어도 Backend가 실행 중이고 모바일이 인터넷에 연결돼 있으면 모바일 대화를 지원합니다.
- 인터넷 또는 Backend가 끊긴 모바일은 메시지를 로컬 전송 대기열에 보관하고 재연결 후 전송합니다.
- 모바일 기기 자체 LLM과 다중 원본 DB 동기화가 필요한 완전 네트워크 오프라인 대화는 이 로드맵의 핵심 범위에서 제외합니다.

### 3.4 클라이언트 계약

- 기존 `POST /api/v1/chat` 요청·응답은 가능한 한 호환 유지합니다.
- Memory, Event, Command Result는 별도 versioned API로 추가합니다.
- 선택 응답 필드는 기존 UE/Web parser가 무시할 수 있도록 additive change로 설계합니다.
- 모바일은 UE gameplay를 직접 변경하지 않습니다.

### 3.5 실제 서버·LLM 없이 진행하는 원칙

- 원격 서버와 실제 Gemma에 실시간으로 접속할 수 없는 상태를 구현 중단 사유로 보지 않습니다.
- 로컬 Backend, SQLite, 고정 fixture와 테스트 전용 Scripted LLM Provider로 계약·라우팅·DB·기억·관계 로직을 먼저 구현합니다.
- 정상 결과뿐 아니라 timeout, malformed output, 빈 응답, 잘못된 source와 환각 후보를 fixture로 재현합니다.
- 생성 대사는 정확한 문장 일치보다 intent, query mode, 사용 근거, 금지 사실과 fallback 여부를 검증합니다.
- 실제 Gemma 품질, Prompt 준수, latency, GPU/VRAM과 배포 환경 결과는 추정값으로 통과 처리하지 않습니다.
- 각 Task는 `구현 완료`, `로컬 결정론 테스트 통과`, `실제 Gemma 검증 대기`, `배포 통합 검증 대기`를 분리해 기록합니다.
- 로컬 결정론 테스트가 통과하면 다음 단계 구현을 계속할 수 있지만, 실제 LLM과 배포 Gate는 `Pending`으로 남깁니다.

## 4. 재사용할 현재 자산

- `app/brain/llm.py`의 Mock/OpenAI/Local Provider 경계
- `app/brain/graph.py`의 명령과 게임 지식 처리 노드
- DB 기반 Item/Recipe/Enemy 데이터와 검증된 사실 생성
- Transcript, `episodic_memories`, embedding과 기억 ranking 코드
- UE Command Gateway의 allowlist, expiry, dedupe와 로컬 실행 권위
- Web/UE의 기존 Chat 요청과 strict 외부 응답 검증

## 5. 새로 필요하거나 크게 바꿀 영역

- 요청 단위 관측 로그와 고정 평가 도구
- 요청 목적·레시피 query mode·일반 대화 정책
- Conversation/Message/GameEvent canonical 영속화
- source-validated Memory schema와 사용자 CRUD
- 관계 의미 상태와 변경 근거
- Event/Command Result 멱등 API
- Web Memory 관리 화면과 UE Outbox
- 배포 계약 일치, 백업·복구, 개인정보와 다중 worker 정책

## 6. 범위 제한

다음은 선행 Gate가 통과하기 전 구현하지 않습니다.

- Local LLM 파인튜닝 또는 MAKO 전용 LoRA
- 요청마다 Local/GPT를 동시에 호출하는 운영 구조
- 예상 결과를 실제 Gemma 검증 증거로 간주하는 완료 처리
- 기억을 기반으로 한 전투 수치·GAS 자동 변경
- 범용 게임 SDK 또는 모든 게임 명령을 포괄하는 플랫폼
- 모바일 on-device LLM과 양방향 DB conflict resolution
- Vector DB 선도입

## 7. 문서 권위

이 폴더는 구현 계획이며 현재 호출 가능한 API 계약이 아닙니다.

권위 순서는 다음과 같습니다.

1. 배포 서버 `/openapi.json`
2. 현재 `app/`, `migrations/`, `tests/`
3. `docs/api-endpoints.md`, `docs/temporary-scaffolds.md`
4. 이 폴더의 계획 문서

계획의 제안 endpoint는 구현·migration·테스트·배포 OpenAPI 확인 전까지 사용할 수 없는 것으로 취급합니다.
