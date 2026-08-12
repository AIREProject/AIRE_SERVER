# Build 1 런타임 흐름

> [!WARNING]
> 레거시 문서 — 독립형 `/v1/companion/*` 계약을 설명합니다. 현행 계약은 `POST /api/v1/chat`
> (루트 `README.md`·`Contracts/` 참조). 마코 두뇌는 최상위 `companion/` 패키지로 이식됨.

메시지 흐름은 2단계 LLM 분류와 결정론적 디스패치로 이뤄진다. 재질의 ID가 있으면 먼저
메모리의 pending 항목을 한 번 소비한다. `나무`와 `돌`은 각각 `wood`와 `stone` Action으로
결정론적으로 변환하며, 다른 답변에는 지원 표현을 안내하고 종료한다.

pending 답변이 아니면 Stage 1 라우터(`classify_top`)가 발화를 `command / recipe / lore /
conversation / unknown` 중 하나로 분류한다. `command`이면 Stage 2 라우터(`classify_command`)가
`follow_player / wait / stop_current_task / gather_resource / cancel / unknown`으로 분류하고,
`gather_resource`는 Stage 3에서 자원과 수량을 결정론적으로 해소해 `wood`/`stone` Action,
재질의, 또는 미지원 응답으로 나눈다.

LLM은 의도 분류와 플레이어에게 보일 대사 생성을 분리해서 담당한다. 코드가 먼저 장면과
저장소·파서·Action에서 확정한 사실을 `DialogueSpec`으로 조립하고, 공급자는 그 사실을 마코의
말투로 옮긴다. 생성 대사는 길이와 숫자 사실을 `sanitize`로 검증하며, 공급자 호출이나 검증이
실패하면 장면별 기존 템플릿으로 복구한다. Action·재질의 선택지·오류 코드와 메시지는 계속
결정론적이다. 기본 Mock 공급자는 정규식 분류와 템플릿 대사를 결정론적으로 재현한다. 실제
공급자의 분류 호출이나 구조화 출력 파싱이 실패해도 같은 Mock 폴백으로 복구한다.
정규식 명령 판별은 Mock/폴백의 단순 명령 분류와 Stage 3의 채집 자원 해소에만 남아 있으며,
프로덕션 명령 분기는 Stage 2가 반환한 라벨을 직접 사용한다.

명확한 Action을 받은 클라이언트는 가까운 자원을 직접 찾고 `task_id`를 생성한다. 클라이언트만
`IDLE/RUNNING/COMPLETED/FAILED/CANCELLED`를 관리한다. 서버는 엔티티, 경로, 접근 가능성이나
인벤토리를 판정하지 않는다.

이벤트 흐름은 `handle_event`가 성공/실패 사실을 조립하고 사실 기반 생성 대사를 반환하는
구조다. 생성 실패 시에는 기존 성공/실패 템플릿 대사로 복구한다.
공개 응답에는 LLM provider, model, trace ID, 검색 fact 등 내부 정보를 포함하지 않는다.
