# 문서 아카이브

완료되었거나 더 이상 활성 작업 기준으로 사용하지 않는 문서를 보관한다.

아카이브 문서는 당시 구현 상태와 의사결정을 재현하기 위한 기록이다. 현재 개발 기준은
`docs/current/`의 문서 네 개를 우선한다.

## 개발 기록

개발 완료 시점의 구현 범위, 주요 결정, 검증 결과와 후속 범위를 기록한다.

| 기록일 | 문서 | 구현 기준 | 기록 유형 |
|---|---|---|---|
| 2026-07-17 20:48 | [2607172048 AI 동료 서버 개발 완료 기록](development-logs/2607172048_ai-companion-server-development-log.md) | `9477270` | 개발 완료 |
| 2026-07-18 14:49 | [2607181449 FOLLOW/WAIT Action Intent 개발 기록](development-logs/2607181449_follow-wait-action-intent-development-log.md) | `08af20e` | 기능 완료 |
| 2026-07-18 16:31 | [2607181631 재질의 계약 개발 기록](development-logs/2607181631_clarification-contract-development-log.md) | `f14a641` | 기능 완료 |
| 2026-07-18 17:49 | [2607181749 GATHER_RESOURCE Mock Action Intent 개발 기록](development-logs/2607181749_gather-resource-action-intent-development-log.md) | `632e8c9` | 기능 완료 |
| 2026-07-20 13:51 | [2607201351 Mock Client 계약 검증기 개발 기록](development-logs/2607201351_mock-client-contract-validator-development-log.md) | 작업 트리 | 기능 완료 |
| 2026-07-20 17:54 | [2607201754 Build 1 최소 시스템 재구성 개발 기록](development-logs/2607201754_build-1-minimal-system-rebuild-development-log.md) | 작업 트리 | 기능 완료 |
| 2026-07-23 09:29 | [2607230929 LLM Stage 1 Top Router 개발 기록](development-logs/2607230929_llm-stage-1-top-router-development-log.md) | 작업 트리 | 기능 완료 |
| 2026-07-23 10:08 | [2607231008 LLM Stage 2 Command Pipeline 개발 기록](development-logs/2607231008_llm-stage-2-command-pipeline-development-log.md) | 작업 트리 | 기능 완료 |
| 2026-07-23 11:01 | [2607231101 정규식 파이프라인 정리 개발 기록](development-logs/2607231101_regex-pipeline-cleanup-development-log.md) | 작업 트리 | 리팩터링 완료 |
| 2026-07-23 13:28 | [2607231328 사실 기반 LLM 대사 개발 기록](development-logs/2607231328_fact-grounded-llm-dialogue-development-log.md) | 작업 트리 | 기능 완료 |
| 2026-07-23 18:55 | [2607231855 AI_RE 인프라 + 마코 두뇌 통합 개발 기록](development-logs/2607231855_airre-backend-companion-integration-development-log.md) | `e06c3d1` | 아키텍처 통합 완료 |
| 2026-07-24 13:49 | [2607241349 WebSocket 채팅 트랜스포트 개발 기록](development-logs/2607241349_websocket-chat-transport-development-log.md) | 작업 트리 | 기능 완료 |
| 2026-07-24 18:36 | [2607241836 저장소 코드 품질 게이트 수립 개발 기록](development-logs/2607241836_code-quality-gate-development-log.md) | `0774e5c..aeb85bd` | 품질 정책·CI 구축 완료 |
| 2026-07-24 | [260724 CANCEL 라벨 통합과 명령 테이블 대칭화 개발 기록](development-logs/260724_cancel-command-consolidation-development-log.md) | `f6cafda` | 동작 버그 수정 완료 |
| 2026-07-27 10:09 | [2607271009 마코 라우팅 LangGraph StateGraph 리팩토링 개발 기록](development-logs/2607271009_langgraph-companion-refactor-development-log.md) | `9d76fa6` | 동작 보존 리팩터링 완료 |
| 2026-07-27 16:42 | [2607271642 채집 슬롯 추출과 `Command.GatherResource` 계약 추가 개발 기록](development-logs/2607271642_gather-slot-extraction-development-log.md) | `ee30703` | 기능 완료 |

## 설계 계획

구현이 완료되어 보관하는 착수 시점의 계획 문서다. 현행 기준은 `docs/current/`를 따른다.

- [LLM 2단계 의도 라우터 리팩토링 계획](llm_two_stage_router_plan.md) — 전체 2단계 라우터 설계와 Phase 1(Top Router)
- [Stage 2 — Command Pipeline 구현 계획](stage2_command_pipeline_plan.md) — Phase 2·3(명령 분류 + 인자 해소)
- [LLM으로 대체된 정규식 파이프라인 제거 계획](regex_pipeline_cleanup_plan.md) — 라우터 도입 후 남은 정규식 잔재 정리
- [마코 라우팅 LangGraph StateGraph 리팩토링 계획](langgraph-companion-refactor-plan.md) — `_route` 조건 분기를 `graph.py`의 StateGraph로 이관(LangGraph-only, 동작 보존)
