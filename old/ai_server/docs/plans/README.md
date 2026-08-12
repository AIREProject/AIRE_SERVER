# 구현 계획 공간

`plans/`는 확정되어 곧 진행할 **구현 계획**을 담는다. `backlog/`(미구현 후보 나열)나
`current/`(구현 계약)와 구분된다.

- `backlog/`: 아직 결정되지 않은 후보 기능
- `plans/`: 방향과 설계가 확정되어 구현을 진행하는(또는 진행할) 계획
- `current/`: 완료·검증되어 확정된 구현 계약

`plans/`의 문서는 규범적 계약이 아니다. 계획이 구현·검증되면 해당 서술을 `current/`로
승격하고, 계획 문서는 갱신하거나 `archive/`로 옮긴다.

## 문서 목록

진행 중인 계획이 없다. 직전 계획 세 건은 모두 구현·검증을 마치고 `archive/`로 옮겼다.

- 취소 명령 통합(`CommandLabel.CANCEL` → `STOP_CURRENT_TASK`, 단일 `_COMMANDS` 테이블)
- 저장소 코드 품질 게이트(ruff 규칙 확장·mypy strict·CI)
- [마코 라우팅 LangGraph `StateGraph` 리팩토링](../archive/langgraph-companion-refactor-plan.md) —
  `_route` 계열 조건 분기를 `graph.py`로 이관. 동작 보존(기존 테스트 무변경 통과)으로 완료.

완료된 계획은 `archive/`에 보관한다.
