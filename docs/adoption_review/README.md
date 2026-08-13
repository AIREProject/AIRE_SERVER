# ai_server 채택 검토 문서

검토일: 2026-08-10  
대상: `C:\workspace\Github\AI_RE\ai_server`

## 결론

2026-08-12 1차 마감까지 Backend 변경은 완료된 고정 공개 인증에 한정하고,
`ai_server`의 현재 HTTP Chat 계약에 UE와 Web client를 최소 수정해 맞춘다. 추가 Backend 기능
확장은 마감 뒤로 넘긴다.

마감용 사용 범위는 다음으로 제한한다.

- `POST /api/v1/chat` HTTP 한 경로
- 같은 단일 플레이어의 UE GameClient와 Mobile WebClient
- canonical identity `AIRE_OPEN / demo-slot-1 / mako`
- UE는 `AIRE_GAME`, Web은 `AIRE_WEB` 고정 Bearer 사용
- `companion_id="mako"`, `surface="game"`, `schema_version=1`
- `allowed_commands=[]`인 대사 표시 전용
- 자동 재시도 없음
- Backend 실패가 전투·Work·Inventory 로컬 수직 슬라이스를 막지 않음

이 결정은 8월 12일 데모용 tactical baseline이다. `ai_server` 전체를 장기 공식 Backend로
승격하거나 운영 결함을 승인한다는 의미는 아니다.

- 현재 AI_RE 공유 문서는 `ai_companion_server/`와 배포 서버 OpenAPI를 공식 기준으로 명시한다.
- `ai_server`는 그 기준과 다른 `/api/v1/*` 계약, 기기 페어링, Offline Task, 상황 대화,
  관리자 CRUD, 장기기억을 함께 가진 별도 후보 구현이다.
- 입력 검증, 토큰 해시, 조건부 DB 상태 전이, 요청 제한, Mock LLM 경계 등은 재사용 가치가 있다.
- Chat 멱등성, Event/Command Result, 기억 범위와 삭제, 운영 readiness, rate limit,
  단일 프로세스 상태와 원문 전사 문제를 해결하기 전에는 공개 또는 지속 운영에 사용하지 않는다.

현재 권고는 다음과 같다.

| 사용 목적 | 판정 |
|---|---|
| 코드 패턴과 테스트 사례 참고 | 가능 |
| 로컬 단일 프로세스 Mock 실험 | 수정 및 수동 검증 후 가능 |
| 8/12 UE 최소 HTTP Chat DTO 기준 | 가능, [마감 계획](07_2026-08-12_BACKEND_FREEZE_PLAN.md) 범위만 |
| 장기 UE/Web 전체 DTO·endpoint 기준 | 별도 계약 결정 전 불가 |
| 사설망 팀 개발 서버 | 계약 결정과 P0/P1 조치 후 재검토 |
| 인터넷 공개 데모 | 고정 공개 인증으로 동작. 보안 격리는 제공하지 않는다는 제품 결정을 적용 |
| 장기 지속 운영 | 별도 재검토 |

## 문서 권위

이 폴더는 기존 문서 갱신 작업과 분리한 감사 결과다. 기존 문서의 권위를 변경하지 않는다.

1. 현재 AI_RE 공식 기준: [`AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md`](../AI_RE/Docs/Backend/EXTERNAL_SERVER_INTEGRATION.md)
2. `ai_server` 현행 코드 계약: [`ai_server/app/models.py`](../ai_server/app/models.py) 및 각 `routes/`
3. `ai_server/docs/current/`: 저장소 자체가 레거시 `/v1/companion/*` 계약이라고 경고하므로 참고만 한다.

8월 12일까지는 `ai_server/app/models.py`의 좁은 HTTP Chat subset을 client adapter 기준으로
사용한다. 장기 공식 Backend를 바꾸려면 별도의 팀 결정과 배포 OpenAPI 동기화가 필요하다.

## 검사 범위와 제한

검사한 범위:

- `app/` Python 파일 62개
- Alembic migration 7개
- 테스트 파일 38개, 정적으로 확인된 test 함수 369개
- `.env.example`, `pyproject.toml`, `uv.lock`
- GitHub Actions quality workflow
- `README.md`, 현행/레거시/운영 문서, smoke script

검사 방식:

- 정적 코드·문서·설정·migration·테스트 대조
- endpoint, 인증, 데이터 범위, 비동기 수명주기, 실패 경로 추적
- 기존 AI_RE 계약·책임 경계와 비교

수행하지 않은 항목:

- 빌드, 컴파일, 서버 실행
- 의존성 설치
- pytest, Ruff, MyPy 실제 실행
- migration upgrade/downgrade 실제 실행
- 배포 OpenAPI 실시간 대조

현재 작업 환경에는 `ai_server/.git`, `ai_server/.venv`, 공식 기준으로 적힌
`ai_companion_server/` 디렉터리가 없고 Python 품질 도구가 PATH에 없었다. 따라서 target source의
`docs/current/`는 이 작업에서 읽을 수 없었다. 배포 OpenAPI도 브라우저 보안 정책에 의해 접근이
차단되어 실시간 검증하지 못했다. 이 문서는 코드 스냅샷 감사이며, target source와 최신 배포
계약 확인을 대신하지 않는다.

## 문서 목록

- [01_IMPLEMENTATION_MAP.md](01_IMPLEMENTATION_MAP.md) — 실제 구조, 데이터 흐름, endpoint와 상태 소유권
- [02_AUDIT_FINDINGS.md](02_AUDIT_FINDINGS.md) — 우선순위별 결함과 근거
- [03_ADOPTION_MATRIX.md](03_ADOPTION_MATRIX.md) — 채택/수정 후 채택/참고/비채택 분류
- [04_CURRENT_CODE_CONTRACT.md](04_CURRENT_CODE_CONTRACT.md) — 코드가 실제로 노출하는 후보 계약
- [05_LOCAL_EVALUATION_RUNBOOK.md](05_LOCAL_EVALUATION_RUNBOOK.md) — 안전한 로컬 평가 절차
- [06_ADOPTION_GATES.md](06_ADOPTION_GATES.md) — 공식 사용 전 결정·수정·검증 Gate
- [07_2026-08-12_BACKEND_FREEZE_PLAN.md](07_2026-08-12_BACKEND_FREEZE_PLAN.md) — Backend 동결과 UE 최소 적응 계획
- [08_AX_FEATURE_ROADMAP.md](08_AX_FEATURE_ROADMAP.md) — 채팅 행동 제어, 월드 Context, Inventory 동기화와 Offline Task 방향
- [09_AX_IMPLEMENTATION_PLAN.md](09_AX_IMPLEMENTATION_PLAN.md) — Core 12개와 Mobile Web 4개 구현 Task 순서
- [10_BACKEND_RUN_GUIDE.md](10_BACKEND_RUN_GUIDE.md) — `.env` 없는 최소 Backend 실행과 고정 Bearer 확인
