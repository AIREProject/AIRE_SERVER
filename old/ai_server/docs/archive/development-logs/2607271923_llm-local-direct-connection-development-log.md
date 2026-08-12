# 2607271923 LLM 로컬 직결과 AIService 싱글턴 개발 기록

- 기록일: 2026-07-27 19:23
- 기록 유형: 성능 개선 기록(코드 변경 + 환경 설정)
- 변경 범위: `.env` LLM base URL 로컬 직결, `AIService` 를 lifespan 싱글턴으로 승격,
  `LLMProvider.aclose()` 도입
- 기준 커밋: `17219c3`
- API/스키마 버전: 변경 없음
- 후속 범위: llama.cpp 서버 튜닝(코드 변경 아님, 아래 6절)

## 1. 완료 상태 요약

세 LLM 스텝(스텝1·2 라우터, 대사 생성)에 스텝별 타이밍 계측(`TimingLLMProvider`)을 넣고 실제
로컬 LLM으로 측정한 결과, 지연이 토큰 생성이 아니라 **호출당 고정 오버헤드에 지배**됨을
확인했다(분류·대사 모두 ~480~510ms). 그 오버헤드의 정체는 앱 코드가 아니라 **경로**였다.

전체 테스트가 통과하고(로컬 `.env` 를 비운 기준 245개) `ruff check app`, `mypy app` 이 깨끗하다.

## 2. 원인: 바로 옆 프로세스를 지구 밖으로 돌려 부르고 있었다

백엔드가 LLM을 `https://mtvs2026.work/v1` 로 불렀는데, 이는 **Cloudflare 엣지 → 터널
(cloudflared) → localhost:8080** 우회다. 그런데 llama-server(llama.cpp)는 **백엔드와 같은
머신** `0.0.0.0:8080` 에 떠 있었다.

- 같은 `/health` 실측: `127.0.0.1:8080` 직결 **0.4ms** vs `mtvs2026.work` 경유 **~840ms**.
- `classify_top` 실측(직결 vs 경유): 웜 **148ms vs 440ms**, 6초 유휴 후 **151ms vs 985ms**.

즉 Cloudflare 우회가 콜드스타트와 정상 지연의 대부분이었고, httpx/서버 keepalive(~5s)로 인한
유휴 재콜드도 우회 경로에서만 생기던 문제였다.

## 3. 로컬 직결 (핵심 레버)

`.env` 의 `LOCAL_LLM_BASE_URL` 을 `http://127.0.0.1:8080/v1` 로 바꿨다.

- `localhost` 가 아니라 `127.0.0.1` 로 고정한다 — `localhost` 는 `::1`(IPv6) 우선 해석이
  가능한데 llama-server 는 IPv4 `0.0.0.0` 바인딩이다.
- Cloudflare 터널은 외부 클라이언트용으로 그대로 두고, 백엔드만 직결한다.
- API 키(`LOCAL_LLM_API_KEY`)는 여전히 필요하다(llama-server `--api-key`).

로컬 직결 후 `classify_top` 이 트래픽 패턴과 무관하게 ~150ms로 안정된다(웜 대비 ~3배, 유휴
재콜드 대비 ~6배). **앞서 검토했던 keepalive 상향·warmup 핑은 도입하지 않았다** — localhost
재연결은 공짜라 유휴 재콜드 문제 자체가 없어졌기 때문이다.

## 4. AIService 싱글턴 (위생 개선)

`app.state.database` 패턴을 미러링해, 요청/연결마다 `AsyncOpenAI` 클라이언트를 재생성하고
LangGraph를 재컴파일하던 것을 제거했다. localhost 전환 후 지연 이득은 작지만(TLS 핸드셰이크
비용이 이미 사라졌다) 자원·구조 위생상 유지할 가치가 있다. 컴파일된 그래프는 불변·동시안전이고
`AsyncOpenAI` 도 동시호출 안전하므로 단일 인스턴스 공유는 설계 의도와 일치한다.

| 파일 | 변경 |
|---|---|
| `app/api/dependencies/ai.py` | 조립 로직을 `build_ai_service(settings) -> AIService` 로 추출. `get_ai_service` 는 `app.state.ai_service` 싱글턴을 반환만 한다. |
| `app/main.py` | `create_app` 이 `build_ai_service` 를 한 번 호출해 `app.state.ai_service` 에 저장. lifespan 종료 시 `aclose()` 가 있으면 호출(hasattr 가드) 뒤 `database.dispose()`. |
| `app/infrastructure/ai/companion/service.py` | `self._llm` 참조 보관, `async def aclose()` → `self._llm.aclose()`. |
| `app/infrastructure/ai/companion/llm.py` | `LLMProvider.aclose()` 추가(기본/Mock no-op, Local/OpenAI 는 `client.close()`, `TimingLLMProvider` 는 inner 위임). |

### `get_ai_service` 가 `Request` 가 아니라 `HTTPConnection` 을 받는 이유

이 의존성은 HTTP `POST /api/v1/chat` 과 WebSocket 라우트(`ws_chat.py`) 양쪽에서 쓰인다.
FastAPI 는 HTTP 연결에는 `Request` 를, WS 연결에는 `WebSocket` 을 주입하므로 `Request` 로
좁히면 **WS 경로에서 주입이 안 돼 `TypeError`** 가 난다. 둘의 공통 상위 타입인
`starlette.requests.HTTPConnection` 을 받으면 FastAPI 가 두 경우 모두 주입한다.
(`.app.state` 접근만 필요하므로 이 상위 타입으로 충분하다.)

## 5. 검증

| 파일 | 추가 검증 |
|---|---|
| `tests/test_chat_api.py` | `get_ai_service` 를 두 번 호출해 같은 `app.state.ai_service` 인스턴스(`is`)를 돌려주는 싱글턴 확인 |
| `tests/test_companion_ai_service.py` | `CompanionAIService.aclose()` 가 `TimingLLMProvider` → 감싼 공급자로 위임됨 확인 |

로컬 개발 `.env` 가 `AI_MODE=companion` 을 두는 탓에, `.env` 를 둔 채 `pytest` 를 돌리면
기본 설정을 가정하는 6개 테스트가 실패한다. 이는 기준 커밋에서도 동일한 기존 현상이며 이번
변경과 무관하다 — `.env` 를 비우면 전부 통과한다.

## 6. 후속 범위: llama.cpp 서버 튜닝 (코드 변경 아님)

로컬 직결 후 남는 ~150ms 는 순수 추론(prefill 지배적)이다. 필요 시 별도 진행:

- **`--parallel 1` → 상향**: 슬롯 1개라 요청이 직렬화된다. 단일 유저는 무방하나 동시 플레이어가
  생기면 큐잉되므로 멀티플레이 시 상향(`--ctx-size` 동반 고려).
- **프롬프트 프리픽스 캐싱**: 세 콜의 긴 고정 시스템 프롬프트가 슬롯 1개에서 번갈아 들어가 매번
  전체 prefill 을 재수행한다. `cache_prompt` 활용을 위해 콜 유형별 슬롯 분리 또는 시스템
  프롬프트 축소 검토 — prefill 지배 구간의 실질 레버.
- **`--ubatch-size 256 → 512`**: prefill 처리량 향상, prefill 바운드라 체감 큼. A/B 권장.
- **투기적 디코딩(`--spec-type draft-mtp`)**: 생성만 가속하고 분류 출력은 몇 토큰뿐이라 라우터
  콜엔 draft 오버헤드가 손해일 수 있음. 라우터에 대해 on/off 비교.
