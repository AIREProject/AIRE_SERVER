# 문서 안내

> [!WARNING]
> **`current/`의 문서는 레거시 계약입니다.** 여기 담긴 `POST /v1/companion/message`·
> `/v1/companion/event`, 채집(gather)·재질의(clarification) 흐름은 이 저장소가 AI_RE Backend
> 인프라와 통합되기 **이전**의 독립형 companion 서버 계약입니다.
>
> 현행 계약은 `POST /api/v1/chat`이며, 권위 있는 정의는 다음에 있습니다.
> - `app/models.py` (in-code Pydantic 계약 — 유일한 권위)
> - 저장소 루트 `README.md`, `CLAUDE.md` (아키텍처 개요)
>
> `Contracts/`(JSON Schema·픽스처)는 디바이스 인증 시절의 계약을 기술하고 있어 삭제됐습니다.
>
> `current/` 문서는 마코 두뇌의 의도 분류·대사 생성 설계를 이해하는 참고 자료로만 보세요.
> 그 두뇌는 현재 `app/brain/` 에 있고, `app/service.py` 가 게임 계약과 이어 줍니다.

## 현행 문서

- `handoff.md` — **인수인계 겸 온보딩 문서. 처음이라면 여기부터.** 30분 안에 서버를 띄우는
  법, 코드 지도, 명세 대비 완료 대조표, **명세와 다른 4가지**, 남은 일의 우선순위,
  되돌리기 전에 알아야 할 설계 결정, 실제로 물릴 만한 함정.
- `api-endpoints.md` — **엔드포인트 전체 명세와 마코 기능별 요청/응답 방법.** 헬스체크·
  디바이스 페어링·채팅(HTTP/WS)·Offline_Task를 한 곳에 모으고, 명령·채집·되묻기·제작법·
  적 공략·세계관·창구·기억을 각각 어떤 데이터로 요청해 무엇을 받는지 예시로 적었다. 새 클라이언트는
  여기부터 본다.
- `websocket-client-guide.md` — WebSocket(`/api/v1/chat`) 클라이언트 연동 가이드.
  메시지 봉투·에러·대화 스코프 규칙. HTTP `POST /api/v1/chat`과 페이로드 스키마가 동일하다.
- `websocket-manual-test-spec.md` — WS를 사람이 손으로 한 단계씩 확인하는 절차. 토큰 발급부터
  브라우저 콘솔/Python/`websocat`별 실행 명령, 오류 후 연결 유지 확인, 문제 해결 표까지.
- `temporary-scaffolds.md` — 클라이언트가 준비되지 않아 서버가 임시로 대신 채우는 값, 그리고
  내부 개발 단계라 **일부러 포기한 보증**(인증·멱등성·영속화)의 목록과 **제거/복구 절차**.
  조건이 충족됐는데 아무도 기억하지 못해 영구히 남는 것을 막는다.
- `game-data.md` — ERD 스키마와 채팅 데이터 명세를 구분한 Item/Recipe/Smelting/Enemies/Location
  데이터, 한국어 별칭 검수표와 현재 데이터의 불일치 목록.

## current/ (레거시 companion 계약 — 참고용)

1. `01_current_scope.md` — Build 1 범위
2. `02_client_ai_contract.md` — 옛 `/v1/companion/*` 요청·응답
3. `03_runtime_flow.md` — 2단계 라우팅·대사 생성 흐름(현 두뇌 설계의 기반)
4. `04_test_checklist.md`
5. `05_player_qa_catalog.md` — 대화 사례 카탈로그

`plans/`는 진행(예정) 구현 계획, `backlog/`은 미구현 후보, `archive/`는 과거 목표 아키텍처와
개발 기록을 보존한다. 모두 규범적 계약이 아니다.
