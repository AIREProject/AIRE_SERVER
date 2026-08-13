# AX-I09 서버 반영 체크리스트

## 현재 상태

- Branch: `63-feat-ax-i09-backend-b2-game-state-snapshot-api`
- 범위: Game State Snapshot API 로컬 구현과 migration `0009`
- 공개 서버 반영: 미수행
- 공개 `/openapi.json` 확인: 미수행
- 상태: 배포 전 `Review`

서버에 접근할 수 있을 때 아래 파일이 포함된 이 Branch의 AX-I09 commit을 배포합니다. 기존
서버의 `.env`, DB, 인증 token, Docker/서비스 설정을 이 저장소 파일로 덮어쓰지 않습니다.

## 이 Commit의 변경 파일

### 서버 Runtime과 DB

- `app/game_state_models.py`
- `app/db/game_state_repository.py`
- `app/game_state_service.py`
- `app/routes/game_state.py`
- `app/db/models.py`
- `app/errors.py`
- `app/errors_http.py`
- `app/main.py`
- `migrations/versions/0009_game_state.py`

### 검증과 계약 문서

- `tests/test_game_state_api.py`
- `tests/test_game_state_migration.py`
- `docs/api-endpoints.md`
- `docs/game-data.md`
- `docs/AX_I09_SERVER_DEPLOYMENT_CHECKLIST.md`

`docs/adoption_review/09_AX_IMPLEMENTATION_PLAN.md`의 작업 디렉터리 변경은 기존 사용자
변경이며 이 Commit에 포함하지 않습니다.

## 서버 접근 가능 시 적용 순서

1. 현재 배포 revision, 실행 방식, 서비스 이름과 배포 디렉터리를 기록합니다.
2. DB와 기존 배포 폴더, `.env`, Docker/서비스 설정을 백업합니다.
3. 이 AX-I09 commit을 서버 배포 디렉터리에 반영합니다.
4. 현재 운영 방식에 맞춰 잠긴 의존성을 준비합니다.

   ```text
   uv sync --frozen
   ```

5. API process를 올리기 전에 migration을 적용하고 revision `0009`를 확인합니다.

   ```text
   uv run alembic upgrade head
   uv run alembic current
   ```

6. 기존 Docker Compose 또는 systemd 운영 절차로 API를 재시작합니다. 실제 서비스 이름과
   배포 경로를 추측하지 말고 `docs/하는방법.md`에서 확인한 값을 사용합니다.
7. `/health`, `/openapi.json`, `/docs`를 확인하고 OpenAPI에 GET/PUT
   `/api/v1/game-state`가 모두 공개됐는지 확인합니다.
8. 실제 GameClient token으로 정상 PUT→GET을 확인합니다. PUT은 body의 `operation_id`와 같은
   `X-Request-ID`, 전송할 원문 UTF-8 body bytes의 소문자 SHA-256
   `X-Content-SHA256`을 사용합니다. token과 실제 Snapshot body는 기록하거나 commit하지
   않습니다.
9. 다음 오류 smoke를 수행하고 기존 Snapshot이 바뀌지 않았는지 확인합니다.

   - 같은 operation과 같은 bytes 재전송: 최초 HTTP 200 응답 재현
   - 같은 operation과 다른 bytes: `409 DuplicateRequest`
   - 같거나 낮은 새 `state_version`: `409 GameStateVersionConflict`
   - WebClient PUT: 403, WebClient GET: 200
   - 존재하지 않는 scope GET: `404 GameStateNotFound`

10. 경로, method, status, error code, 응답 body hash와 확인 시각을 배포 기록에 남깁니다.

Migration이나 재시작이 실패하면 성공으로 기록하지 않습니다. 실제 Snapshot이 저장된 뒤에는
데이터 삭제를 동반하는 downgrade를 임의로 실행하지 말고 DB·배포 폴더 백업으로 복구할지 먼저
결정합니다.

## 로컬 검증 증거

- Backend 전체 pytest 통과
- AX-I09 API·migration 테스트 26개 통과
- 동시 version 요청 테스트 10회 반복 통과
- Ruff 통과
- mypy 통과
- Unreal build 또는 실행 없음
