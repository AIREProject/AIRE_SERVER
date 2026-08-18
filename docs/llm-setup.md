# LLM과 장기기억 Embedding 설정

서버는 `mock`, `openai`, `local` 세 LLM provider를 지원합니다. 처음에는 반드시 `mock`으로
서버·DB·Chat을 확인한 뒤 실제 provider를 연결합니다.

## 1. Provider 선택 요약

| 값 | 외부 연결 | 대사 | 장기기억 추출 |
|---|---|---|---|
| `LLM_PROVIDER=mock` | 없음 | 결정론적 fallback 문장 | 생성하지 않음 |
| `LLM_PROVIDER=openai` | OpenAI Responses API | 실제 모델 생성 | 실제 모델 추출·요약 |
| `LLM_PROVIDER=local` | OpenAI-compatible Chat Completions | 실제 로컬 모델 생성 | 실제 로컬 모델 추출·요약 |

Provider 호출에 실패하거나 응답 검증에 실패하면 해당 턴은 Mock fallback으로 복구됩니다.
서버 전체가 중단되지는 않습니다.

## 2. Mock

`.env`가 없거나 다음 값이면 외부 LLM이 필요하지 않습니다.

```dotenv
LLM_PROVIDER=mock
EMBEDDING_PROVIDER=mock
```

Mock은 API·DB·client 연동을 확인하는 개발 기준입니다. 대사와 명령 분류는 동작하지만,
장기기억 후보를 판단하는 가짜 규칙은 사용하지 않기 때문에 새 장기기억을 생성하지 않습니다.

## 3. OpenAI

`.env.example`을 `.env`로 복사하고 다음 값을 채웁니다.

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=replace-with-real-key
OPENAI_MODEL=gpt-5-nano
OPENAI_TIMEOUT_SECONDS=8
```

OpenAI provider는 Responses API와 JSON Schema 구조화 출력을 사용합니다. 지정 모델은 다음을
지원해야 합니다.

- Responses API
- JSON Schema structured output
- 짧은 reasoning 설정

API key가 비어 있으면 오류로 서버를 중단하지 않고 시작 시 Mock provider를 선택합니다.

## 4. OpenAI-compatible Local LLM

Local provider는 OpenAI-compatible `/v1/chat/completions`를 호출합니다.

```dotenv
LLM_PROVIDER=local
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
LOCAL_LLM_API_KEY=not-required
LOCAL_LLM_MODEL=your-model-name
LOCAL_LLM_TIMEOUT_SECONDS=30
```

`LOCAL_LLM_API_KEY`는 현재 provider 선택 조건이므로 비워 두면 Local이 아니라 Mock이
선택됩니다. 인증이 없는 Local server라도 `not-required`처럼 비어 있지 않은 값을 넣습니다.

Local endpoint가 지원해야 하는 기능:

- OpenAI-compatible Chat Completions
- `response_format.type=json_schema`
- strict JSON Schema output
- `conversation_dialogue_output`의 text-only 일상대화 JSON Schema output
- `dialogue_output`의 사실 기반 장면 JSON Schema output
- `recipe_selection`의 검증 후보 ID·confidence JSON Schema output
- 요청 body의 `chat_template_kwargs.enable_thinking=false`를 허용하거나 무시

Base URL은 일반적으로 `/v1`까지 포함합니다. model name은 Local server의 model 목록에 등록된
값과 정확히 같아야 합니다.

## 5. Embedding Provider

장기기억 검색은 LLM provider와 별개로 Embedding provider를 선택합니다. Embedding이 없어도
키워드와 시간 감쇠 검색으로 동작합니다.

### Mock Embedding

```dotenv
EMBEDDING_PROVIDER=mock
```

외부 호출 없이 키워드 검색만 사용합니다.

### OpenAI Embedding

```dotenv
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=replace-with-real-key
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_EMBEDDING_DIMENSIONS=512
EMBEDDING_TIMEOUT_SECONDS=3
```

OpenAI LLM과 같은 `OPENAI_API_KEY`를 사용합니다. Model이 차원 축소를 지원하지 않으면
`OPENAI_EMBEDDING_DIMENSIONS`를 비웁니다.

### Local Embedding

```dotenv
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_MODEL=bge-m3
LOCAL_EMBEDDING_BASE_URL=http://127.0.0.1:8081/v1
LOCAL_EMBEDDING_API_KEY=not-required
LOCAL_EMBEDDING_USER_AGENT=
LOCAL_EMBEDDING_DIMENSIONS=
EMBEDDING_TIMEOUT_SECONDS=3
```

`LOCAL_EMBEDDING_BASE_URL`을 비우면 `LOCAL_LLM_BASE_URL`을 사용합니다. 두 Base URL이 정확히
같을 때만 Local LLM API key를 상속합니다. 다른 서버를 사용할 때는 embedding 전용 key를
명시합니다.

Embedding server가 `dimensions` 인자를 지원하지 않으면 dimensions 값은 비워 둡니다.

## 6. 장기기억 설정

기본 흐름은 다음과 같습니다.

```text
인증된 RealWorld 또는 GameWorld player Message
→ leased source outbox
→ LLM이 기억 유형·중요도만 분류
→ canonical Message 원문을 Memory에 저장
→ 이후 scope와 관련성이 맞는 Active 기억 회수
→ 대사 Prompt의 [기억] 블록에 참고 정보로 전달
```

주요 설정은 다음과 같습니다.

```dotenv
MEMORY_WORKER_ENABLED=true
MEMORY_WORKER_INTERVAL_SECONDS=5
MEMORY_WORKER_LEASE_SECONDS=60
MEMORY_WORKER_MAX_ATTEMPTS=3
MEMORY_WORKER_BATCH_SIZE=32
TRANSCRIPT_ENABLED=false
TRANSCRIPT_DIR=data/transcripts
TRANSCRIPT_RETENTION_DAYS=1
```

주의사항:

- Mock LLM은 Message를 `Reject`로 분류하므로 새 장기기억을 만들지 않습니다.
- Transcript는 개발 진단용이며 꺼져 있어도 canonical Message 기반 저장·회수는 동작합니다.
- LLM 출력은 저장 text로 사용하지 않고 `decision`과 `importance`만 사용합니다.
- Recipe·Command·현재 게임 상태·companion 발화는 Message 기억으로 저장하지 않습니다.
- 기억은 profile + save-slot + companion scope에서 UE/Web에 공유됩니다.
- 인게임 직접 발화는 `Message + GameWorld`, 모바일 직접 발화는 `Message + RealWorld` 출처로
  저장됩니다.
- Web 정정 후에는 최신 정정문만 검색과 Prompt 회상에 사용됩니다.
- delete/reset된 기억과 source가 삭제된 기억은 Prompt에 들어가지 않습니다.
- 기억은 확정 게임 사실이 아니라 대화 참고 정보로만 Prompt에 들어갑니다.
- Embedding이 없어도 사용자가 자신의 취향·과거 기억을 명시적으로 물으면 Active 기억을
  중요도·고정·최근성 순으로 제한해 Prompt에 전달합니다.

## 7. 연결 확인

설정을 바꾼 뒤 서버를 완전히 재시작합니다. `/health`의 `llm_provider`는 설정 문자열을
보여줄 뿐 실제 외부 호출 성공을 검증하지 않습니다.

실제 확인 절차:

1. [API 문서](api-endpoints.md)의 Chat 요청을 보냅니다.
2. 응답 `ai_metadata.provider`와 `model_version`을 확인합니다.
3. Local/OpenAI server의 request log 또는 usage를 확인합니다.
4. 같은 입력에서 고정 Mock 문장만 반복되는지 확인합니다.
5. 서버 로그의 `llm_step` 처리 시간을 확인합니다.

예시 응답 조각:

```json
{
  "ai_metadata": {
    "provider": "local",
    "model_version": "your-model-name",
    "prompt_version": "companion-v4"
  }
}
```

`ai_metadata.provider=mock`이면 provider 선택 단계에서 필수 key가 부족한 것입니다.
`provider=local/openai`라도 개별 호출은 내부 오류 뒤 Mock fallback이 될 수 있으므로 Local/OpenAI
server log도 함께 확인합니다.

## 8. 문제 해결

### Local provider를 설정했는데 Mock이 선택됨

- `LLM_PROVIDER=local` 확인
- `LOCAL_LLM_API_KEY`가 빈 문자열이 아닌지 확인
- `.env` 변경 뒤 서버를 재시작했는지 확인

### Local 요청은 오지만 구조화 분류가 실패함

- `response_format=json_schema` 지원 확인
- model의 JSON Schema 출력 지원 확인
- Local server가 `extra_body`를 거부하는지 확인
- `LOCAL_LLM_MODEL` 이름 확인

실패해도 서버는 Mock fallback을 반환하므로 HTTP 200만 보고 성공이라고 판단하면 안 됩니다.

### Embedding이 생성되지 않음

- `EMBEDDING_PROVIDER`와 model 확인
- Local embedding이면 `LOCAL_EMBEDDING_MODEL` 필수
- `dimensions` 미지원 모델은 dimensions 값을 비움
- 다른 host를 쓸 때 embedding 전용 API key 확인

Embedding 실패는 기억 저장을 막지 않고 키워드 검색으로 폴백합니다.
