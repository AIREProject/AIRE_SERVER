# 2607241836 저장소 코드 품질 게이트 수립 개발 기록

- 기록일: 2026-07-24 18:36
- 기록 유형: 품질 정책 정비 및 CI 게이트 구축 완료 기록
- 변경 범위: AI_RE 재동기화 예외 폐기, 저장소 전체 Ruff·strict mypy 위반 정리,
  GitHub Actions 품질 게이트 구축과 실행 중복 방지
- 구현 기준: `0774e5c`부터 `aeb85bd`까지 6개 커밋
- API/스키마 버전: 공개 API, `Contracts/`, DB 스키마와 환경 변수 변경 없음
- 후속 범위: Ruff `SIM`·`ANN` 규칙군 도입 검토, 테스트 경고 정리, 필요 시 GitHub
  branch protection에서 `Quality Gate` 필수 체크 지정

## 1. 완료 상태 요약

AI_RE 상류와 재동기화하기 위해 일부 파일의 스타일 위반을 허용하던 정책을 폐기하고, 저장소
전체에 하나의 품질 기준을 적용했다. Ruff 적용 범위를 확장하고 mypy strict 모드를 켰으며,
두 검사를 전체 테스트와 함께 GitHub Actions에서 매 push/PR에 실행하도록 만들었다.

실행 당시 Ruff 자동 수정은 19개 파일에 걸쳐 42건을 처리했다. 이후 FastAPI 의존성 선언,
예외 체이닝, 긴 줄과 mypy 타입 오류를 수동으로 정리했다. 새로 활성화한 `RUF` 규칙에서 발견된
3건도 함께 해소했다. 최종 상태는 Ruff 0건, strict mypy 0건, 전체 테스트 146개 통과다.

CI는 PR마다 실행하고 `main` 직접 push도 검사한다. 같은 ref에 연속 변경이 올라오면 이전 실행을
취소하며, 공급망 변동을 줄이기 위해 `actions/checkout`과 `astral-sh/setup-uv`를 commit SHA로
고정했다.

## 2. 구현 범위와 주요 결정

### 2.1 품질 정책을 저장소 전체 기준으로 교체

`CLAUDE.md`의 "AI_RE에서 가져온 파일은 상류 스타일을 유지한다"는 예외를 삭제했다. 출처는
역사적 맥락으로만 남기고, AI_RE는 더 이상 사용하지 않으며 재동기화 대상도 아니라는 현재
운영 결정을 명시했다.

개발 명령에는 실제 소스 트리를 검사하는 `uv run mypy app`을 추가했다. 과거의 `mypy src`처럼
소스가 없는 경로를 검사하고 성공으로 오인하지 않도록 검사 대상을 `app`으로 고정했다.

### 2.2 Ruff 자동 수정

`uv run ruff check . --fix`로 의미 보존이 가능한 변경을 먼저 분리했다.

- `datetime.timezone.utc`를 Python 3.13의 `datetime.UTC`로 교체
- import 정렬
- 미사용 import 제거
- 마이그레이션 파일 끝 개행 등 Ruff가 제공한 안전한 기계적 수정 적용

애플리케이션, 마이그레이션과 테스트 19개 파일에서 36줄 추가·35줄 삭제가 발생했다. 이 변경은
수동 리팩터링과 섞지 않고 `a92cef1` 단독 커밋으로 남겼다.

### 2.3 수동 lint·타입 오류 해소

자동 수정 후 남은 Ruff 8건과 mypy 4건을 다음과 같이 정리했다.

- `ChatService`의 두 후보 반복문이 같은 `candidate` 이름을 재사용하던 문제를
  `memory_candidate`로 분리했다.
- `DeviceRepository`의 컬렉션 반환 타입을 불변 `list`에서 공변 `Sequence`로 바꿨다.
  `list_devices`뿐 아니라 동일한 어댑터 호환 문제가 있던 `list_pairing_codes`에도 적용했다.
- `system.py`의 FastAPI 의존성을 기본값 호출 방식에서
  `Annotated[Settings, Depends(get_settings)]` 방식으로 옮겼다.
- pairing/chat 저장소의 예외 변환에 `raise ... from error`를 추가해 원인 예외를 명시적으로
  연결했다.
- `tests/test_chat_api.py`의 100자 초과 데이터베이스 URL 조립을 두 줄로 분리했다.

확장 규칙을 켠 뒤 발견된 `RUF` 위반도 정리했다.

- 활성화하지 않은 `BLE001`용 `noqa` 두 곳을 제거하되 예외 처리 의도를 설명하는 주석은 유지
- `LoreRepository._LORE`를 `ClassVar[dict[str, str]]`로 선언해 클래스 상수임을 명시

### 2.4 Ruff 규칙 확장과 strict mypy

`pyproject.toml`의 Ruff 규칙을 다음 범위로 확장했다.

```text
E, F, I, B, UP, ASYNC, C4, PTH, N, T20, RUF
```

async 정확성, 컴프리헨션, pathlib, 네이밍과 `print()` 잔존 검사를 추가했다. 측정 결과 정리
비용이 테스트 한 파일에 집중된 `SIM`과, 테스트 애노테이션 보완이 필요한 `ANN`은 이번 범위에서
제외했다.

mypy에는 `strict = true`를 설정했다. strict 전환 전후의 오류가 동일한 4건이었고, 2.3의 타입
수정 뒤에는 추가 오류 없이 strict 모드가 통과했다.

### 2.5 GitHub Actions 품질 게이트

`.github/workflows/quality.yml`을 추가해 다음 순서를 하나의 `Quality Gate` 작업으로 실행한다.

```text
uv sync --locked --dev
uv run ruff check .
uv run mypy app
uv run pytest
```

워크플로 권한은 `contents: read`만 허용한다. 최초 버전은 모든 push와 PR에서 실행했지만, PR
브랜치 push 때 같은 게이트가 두 번 실행되는 문제가 있어 후속 커밋에서 다음처럼 조정했다.

- `push`는 `main` 브랜치로 제한
- `pull_request` 검사는 유지
- `${{ github.workflow }}-${{ github.ref }}` 기준 concurrency 그룹 추가
- `cancel-in-progress: true`로 같은 ref의 이전 실행 취소
- `actions/checkout`을 `d23441a...` SHA(v6)로 고정
- `astral-sh/setup-uv`는 `0880764...` SHA(v8.1.0), Python은 3.13으로 고정

### 2.6 계획 문서 정합성과 로컬 죽은 자산 정리

구현 계획 `docs/plans/code-quality-gate-plan.md`를 저장소에 추가하고 계획 색인에 연결했다.
같은 커밋에서 `CLAUDE.md`의 provenance 문구도 "상류 재동기화 대상"에서 "역사적 출처"로
바꿔 새 품질 정책과 모순되지 않게 했다.

Git에 추적되지 않던 `src/`의 옛 `.pyc` 15개와 파일이 없던 `alembic/versions/`도 로컬에서
삭제했다. 실제 애플리케이션 소스 `app/`과 Alembic 스크립트 위치 `migrations/`는 변경하지
않았다. 이 두 삭제는 미추적 생성물과 빈 디렉터리 정리라 커밋 diff에는 나타나지 않는다.

`docs/current/`의 구 계약 문서는 각 파일에 레거시 경고가 이미 있어 기존 링크를 보존했다.

## 3. 커밋 기록

| 커밋 | 제목 | 주요 내용 |
|---|---|---|
| `0774e5c` | `docs: enforce repository-wide quality policy` | 예외 정책 폐기, mypy 명령 추가 |
| `a92cef1` | `style: apply automated ruff fixes` | Ruff 자동 수정 42건 분리 적용 |
| `e1c0725` | `refactor: resolve lint and type violations` | 수동 Ruff 8건·mypy 4건 해소 |
| `581afe1` | `ci: enforce code quality gate` | 확장 규칙, strict mypy, GitHub Actions 추가 |
| `b9b63b8` | `docs: drop AI_RE re-sync exemption and record quality plan` | provenance 정합성 수정, 계획 문서·색인 추가 |
| `aeb85bd` | `ci: stop duplicate quality runs and pin checkout` | CI 중복 방지, concurrency와 action SHA 고정 |

## 4. 변경 파일

| 파일 또는 범위 | 변경 내용 |
|---|---|
| `CLAUDE.md` | 저장소 전체 품질 정책, mypy 명령, AI_RE 역사적 provenance 반영 |
| `pyproject.toml` | Ruff 7개 규칙군 추가, mypy strict 활성화 |
| `.github/workflows/quality.yml` | 의존성 설치·lint·type check·test 게이트와 중복 실행 방지 |
| `app/application/chat_service.py` | 후보 반복문 변수 타입 충돌 해소 |
| `app/application/ports/device_repository.py` | 컬렉션 포트 반환 타입을 `Sequence`로 변경 |
| `app/api/routes/system.py` | FastAPI 의존성을 `Annotated` 스타일로 전환 |
| `app/application/pairing_service.py` | UTC 현대화, import 정렬, 예외 원인 체이닝 |
| `app/infrastructure/database/chat_repository.py` | UTC 현대화와 `IntegrityError` 원인 체이닝 |
| `app/infrastructure/ai/companion/lore.py` | `_LORE` 클래스 상수 타입 명시 |
| `app/api/routes/ws_chat.py`, companion `service.py` | 불필요해진 `noqa: BLE001` 제거 |
| `migrations/`, 기타 `app/`, `tests/` | Ruff 자동 수정과 긴 줄 정리 |
| `docs/plans/` | 품질 게이트 계획 문서와 계획 색인 추가 |

공개 API 모델, `Contracts/`, 데이터베이스 마이그레이션 내용과 런타임 설정 값은 바뀌지 않았다.

## 5. 검증 결과

최신 커밋 `aeb85bd` 기준으로 전체 게이트를 다시 실행했다.

```text
uv run ruff check .
All checks passed!

uv run mypy app
Success: no issues found in 55 source files

uv run pytest
146 passed, 2 warnings

git diff --check
통과
```

테스트 경고 중 하나는 기존 Starlette TestClient의 `httpx` deprecation 경고다. 다른 하나는
테스트 종료 시점에 aiosqlite 작업 스레드가 닫힌 이벤트 루프에 결과를 전달하려다 발생하는
간헐적 `PytestUnhandledThreadExceptionWarning`이다. 테스트 실패는 아니지만 별도 수명주기
정리 대상으로 남긴다.

## 6. 후속 범위 및 주의점

- `SIM`은 기존 위반 대부분이 `tests/test_ws_chat_api.py`에 집중되어 있어 별도 리팩터링으로
  진행한다.
- `ANN`은 테스트 helper의 애노테이션 정책을 정한 뒤 활성화한다.
- Starlette/httpx deprecation과 간헐적 aiosqlite 종료 경고를 별도 테스트 인프라 작업으로
  정리한다.
- GitHub Actions 파일만으로 merge 강제가 자동 설정되지는 않는다. 필요하면 저장소 branch
  protection에서 `Quality Gate`를 required status check로 지정한다.
- action을 SHA로 고정했으므로 Dependabot 또는 정기 점검으로 새 보안·호환성 버전을 명시적으로
  갱신한다.
