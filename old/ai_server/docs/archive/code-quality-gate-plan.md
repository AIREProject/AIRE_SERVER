# AI_RE 스타일 정책 폐기와 코드 품질 게이트 수립

> **다른 계획과의 관계**: 이 계획은
> [cancel-command-consolidation-plan.md](cancel-command-consolidation-plan.md)(1단계) 및
> [langgraph-companion-refactor-plan.md](langgraph-companion-refactor-plan.md)(2단계)와
> **독립적이며 병행 가능하다.** 정리 대상은 전부 AI_RE 인프라 쪽이고, LangGraph 작업은
> `app/infrastructure/ai/companion/`(이미 lint·타입 clean)만 건드리기 때문이다.

## Context

[CLAUDE.md:68](../../CLAUDE.md#L68)에는 다음 예외 규정이 있다:

> New code should be ruff-clean; **imported AI_RE files retain upstream style**
> (e.g. FastAPI `Depends` defaults) **to ease re-syncing**.

이 규정은 AI_RE 업스트림과 재동기화할 필요가 있다는 전제 위에 있었다.
**AI_RE 서버를 더 이상 쓰지 않고 이 서버만 운영하기로 결정되면서 그 전제가 사라졌다.**
따라서 예외 규정을 폐기하고, 저장소 전체를 단일 품질 기준으로 되돌린다.

### 현재 누적 상태

| 도구 | 결과 |
|---|---|
| `uv run ruff check .` | **37건** (29건 자동 수정 가능) |
| `uv run mypy app` | **4건** (2개 파일) |

**분포가 정책의 흔적을 그대로 보여준다** — 마코 두뇌(`app/infrastructure/ai/companion/`)는
ruff·mypy 모두 **0건**이고, 37건 전부가 AI_RE에서 가져온 인프라와 일부 테스트에 몰려 있다.

---

## 측정 결과

계획을 추정이 아니라 실측 위에 세우기 위해 먼저 비용을 측정했다.

### 1. mypy strict 전환 비용 = 0

```powershell
uv run mypy app            # Found 4 errors in 2 files (checked 55 source files)
uv run mypy --strict app   # Found 4 errors in 2 files (checked 55 source files)  ← 동일
```

현재 `[tool.mypy]`에는 `strict` 설정이 **없어 기본(관대) 모드로 돌고 있었다.** 그럼에도
`--strict`를 켠 결과가 **완전히 동일하다.** 즉 이 코드베이스는 이미 실질적으로 strict-clean이며,
**아래 4건만 없애면 `strict = true`를 공짜로 켤 수 있다.**

`app/infrastructure/ai/companion/`만 `--strict`로 검사하면 **0건**이다.

### 2. ruff 규칙 확장 비용

| 규칙군 | 추가 위반 | 성격 |
|---|---|---|
| `ASYNC` | **0** | async 정확성 — 이 코드베이스에 가치가 크다 |
| `C4` | **0** | 컴프리헨션 |
| `PTH` | **0** | pathlib |
| `N` | **0** | 네이밍 규약 |
| `T20` | **0** | `print()` 잔존 검출 |
| `RUF` | 4 | ruff 고유 규칙 |
| `ANN` | 3 | 누락 애노테이션(2건은 테스트의 `Any`) |
| `SIM` | 17 | **16건이 `tests/test_ws_chat_api.py` 한 파일에 집중** |

**5개 규칙군(`ASYNC`,`C4`,`PTH`,`N`,`T20`)은 지금 켜도 위반이 0건이다.** 미래 방어를 공짜로 얻는다.

### 3. 현재 37건의 내역

| 규칙 | 건수 | 내용 | 자동 수정 |
|---|---|---|---|
| `UP017` | 16 | `datetime.timezone.utc` → `datetime.UTC` | ✅ |
| `I001` | 11 | import 정렬 | ✅ |
| `B904` | 5 | `except` 안에서 `raise ... from err` 누락 | ❌ |
| `B008` | 2 | 인자 기본값의 `Depends()` 호출 | ❌ |
| `F401` | 2 | 미사용 import | ✅ |
| `E501` | 1 | 100자 초과 | ❌ |

파일별로는 [pairing_service.py](../../app/application/pairing_service.py)가 12건으로 가장 많다.

### 4. mypy 4건의 정체 (둘 다 런타임 버그 아님)

**① [chat_service.py:136,138,139](../../app/application/chat_service.py#L125-L141) — 루프 변수 이름 재사용**

```python
for candidate in result.command_candidates:   # 125행: candidate = CommandCandidate 로 고정
    ...
for candidate in result.memory_candidates:    # 136행: 같은 이름에 MemoryCandidate
    if candidate.source_mode is not ...       # 138행: CommandCandidate엔 없는 속성
```

mypy는 변수 타입을 첫 바인딩에서 고정하므로 항의한다. 파이썬 런타임은 정상이다.

**② [devices.py:63](../../app/api/routes/devices.py#L63) — `list`의 불변성(invariance)**

포트는 `list[DeviceRecord]`(Protocol)를 선언하는데
([device_repository.py:51,53](../../app/application/ports/device_repository.py#L51))
어댑터는 `list[DeviceModel]`(SQLAlchemy 모델)을 반환한다
([device_repository.py:56,62](../../app/infrastructure/database/device_repository.py#L56)).
`DeviceModel`이 프로토콜을 구조적으로 만족해도 **`list`는 불변이라 서브타입이 아니다.**

---

## 진짜 원인: 강제 게이트가 없다

37건이 쌓인 이유는 예외 규정만이 아니다. **아무것도 강제하지 않는다:**

- `.github/workflows` **없음**
- `.pre-commit-config.yaml` **없음**
- `[tool.mypy]`에 `strict` **없음** (기본 모드로 동작 중)
- [CLAUDE.md](../../CLAUDE.md) Commands 절에 **mypy 명령 자체가 없음**

**정책만 폐기하고 게이트를 세우지 않으면 반드시 재발한다.** 3단계가 이 계획의 핵심이다.

---

## 계획

### 0단계 — 정책 문구 교체

[CLAUDE.md:68](../../CLAUDE.md#L68)에서 AI_RE 재동기화 예외를 삭제하고 단일 기준으로 대체한다.

```diff
- Ruff selects `E,F,I,B,UP`. New code should be ruff-clean; imported AI_RE files retain
- upstream style (e.g. FastAPI `Depends` defaults) to ease re-syncing.
+ Ruff selects `E,F,I,B,UP,ASYNC,C4,PTH,N,T20,RUF`. MyPy is strict. The whole repository
+ must stay ruff- and mypy-clean — there is no imported-file exemption.
```

Commands 절에 누락된 타입 체크 명령도 추가한다:

```powershell
uv run mypy app                 # 타입 체크 (현재 CLAUDE.md에 없음)
```

> **주의**: `uv run mypy src`가 아니다. `src/`에는 `.py` 소스가 없어 이 명령은 **아무것도 검사하지
> 않고 통과한다**(4단계에서 삭제). 실제로 이 함정 때문에 앞선 계획서 두 건에 잘못된 검증 명령이
> 들어갔다가 수정됐다.

### 1단계 — 자동 수정 (29건)

```powershell
uv run ruff check . --fix       # UP017 16 + I001 11 + F401 2
uv run pytest                   # 회귀 확인
```

기계적 변환이라 리뷰 부담이 낮다. **단독 커밋으로 분리**해야 이후 diff를 읽을 수 있다.

### 2단계 — 수동 수정 (11건)

| 대상 | 건수 | 수정 방법 |
|---|---|---|
| `chat_service.py` 루프 변수 | 3 | 두 번째 루프 변수를 `memory_candidate`로 rename → 3건 동시 해결 |
| `devices.py` 변성 | 1 | 포트 반환 타입을 `Sequence[DeviceRecord]`(공변)로 변경 |
| `B904` | 5 | `raise X from err` 추가 ([pairing_service.py](../../app/application/pairing_service.py) 4, [chat_repository.py:132](../../app/infrastructure/database/chat_repository.py#L132) 1) |
| `B008` | 2 | `Annotated[X, Depends(y)]` 스타일로 마이그레이션 |
| `E501` | 1 | 줄 분할 |

**`B008`은 억지 억제가 아니라 실질적 현대화다.** [system.py:25,37](../../app/api/routes/system.py#L25)의
`Depends()` 기본값을 `Annotated`로 옮기면 규칙 위반이 정당하게 사라지고,
[ai.py:28](../../app/api/dependencies/ai.py#L28)이 이미 쓰고 있는 스타일과 **일관성도 올라간다.**

`B904`는 원인 예외가 사라지는 문제는 아니지만(파이썬이 `__context__`로 암묵 체이닝),
**"의도한 예외 변환"인지 "예외 처리 중 터진 사고"인지 구분이 안 된다.**

### 3단계 — 게이트 수립 (핵심)

`pyproject.toml`:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC", "C4", "PTH", "N", "T20", "RUF"]
#                                   └────── 추가 비용 0건 ──────┘   └ 4건 ┘

[tool.mypy]
python_version = "3.13"
plugins = ["pydantic.mypy"]
strict = true          # ← 2단계로 4건을 없앤 뒤 켜면 추가 비용 0
```

- **`SIM`/`ANN`은 이번엔 보류한다.** `SIM` 17건 중 16건이
  [tests/test_ws_chat_api.py](../../tests/test_ws_chat_api.py) 한 파일에 몰려 있어 별도 정리
  작업으로 분리하는 편이 낫다.
- **CI 또는 pre-commit으로 강제한다.** `ruff check .` + `mypy app` + `pytest` 세 가지를 게이트로
  묶는다. 이것이 없으면 0~2단계는 일회성 청소로 끝난다.

### 4단계 — 죽은 자산 정리

| 대상 | 상태 | 조치 |
|---|---|---|
| `src/` | git **미추적**, 옛 빌드 `.pyc` 15개뿐 | 삭제 |
| `alembic/` | git **미추적**, **빈 디렉터리**. `alembic.ini`의 `script_location`은 `migrations/`를 가리킴 | 삭제 |
| `docs/current/*` | 레거시 표시된 옛 `/v1/companion/*` 계약 문서 | `archive/` 이동 검토 |

**`src/` 삭제는 단순 청소가 아니다.** 이 디렉터리가 남아 있는 한 `mypy src`가 조용히 통과하며,
타입 검사를 했다고 오인하게 만든다. 실제로 그 함정이 이미 한 번 발생했다(0단계 주의 참고).

`docs/current/*`는 상단에 `> [!WARNING] 레거시 문서` 표시가 이미 붙어 있으므로 급하지 않다.
다만 [03_runtime_flow.md:13](../../docs/current/03_runtime_flow.md#L13)은 Stage 2 라벨 목록에
여전히 `cancel`을 포함하고 있어 1단계 작업과 어긋난다.

---

## 실행 순서와 우선순위

**가치 순위**는 3단계(게이트) > 0단계(정책) > 2단계(수동) > 1단계(자동) > 4단계(정리)지만,
**의존성 때문에 실행 순서는 0 → 1 → 2 → 3 → 4**다. `strict = true`는 4건을 없앤 뒤에만 켤 수 있다.

각 단계는 **별도 커밋**으로 분리한다. 특히 1단계는 여러 파일을 기계적으로 건드리므로 다른
변경과 섞이면 리뷰가 불가능해진다.

> **선행**: 워킹 트리에 커밋되지 않은
> [1단계 CANCEL 통합](cancel-command-consolidation-plan.md) 작업이 있다면 **먼저 커밋한다.**
> 대량 자동 수정과 섞이면 diff를 읽을 수 없다.

---

## 검증

각 단계 후 동일하게 실행한다.

```powershell
uv run ruff check .
uv run mypy app                 # `mypy src` 아님
uv run pytest
```

**단계별 기대값:**

| 단계 후 | ruff | mypy |
|---|---|---|
| 0 (정책만) | 37 | 4 |
| 1 (자동 수정) | 8 | 4 |
| 2 (수동 수정) | **0** | **0** |
| 3 (규칙 확장 + strict) | **0** | **0** |

3단계에서 규칙을 확장해도 0을 유지하는 것이 측정으로 보장된다(`ASYNC`/`C4`/`PTH`/`N`/`T20` 0건,
`RUF` 4건은 2단계와 함께 처리). **`RUF` 4건이 3단계에서 새로 드러나므로 그때 함께 정리한다.**

---

## 리스크

| 리스크 | 완화 |
|---|---|
| `ruff --fix` 29건이 런타임 동작을 바꿈 | `UP017`/`I001`/`F401`은 의미 보존 변환. 단독 커밋 + `pytest` 전량 통과로 확인 |
| `strict = true`가 예상 밖 오류를 유발 | `--strict` 실측 결과가 기본 모드와 동일함을 이미 확인. 2단계 완료 후 켠다 |
| `B008` → `Annotated` 마이그레이션이 FastAPI 의존성 주입을 깨뜨림 | `ai.py`에 동일 패턴이 이미 동작 중. API 테스트(`test_system.py` 등)로 확인 |
| 게이트 도입 후 기존 작업 흐름이 막힘 | 0~2단계로 위반을 0으로 만든 **뒤에** 게이트를 켠다(순서가 이를 보장) |
