# CAI-P3. 출처 기반 장기기억

- 상태: Planned
- 선행: CAI-P2 Local Deterministic Tests Passed
- 후속: CAI-P4, CAI-P5
- 기존 계획: M05-E03, G5 Persistent Memory

## 목표

LLM이 요약한 문장을 곧바로 사실로 믿지 않고, 검증된 Message/Event 출처를 가진 기억만 저장·검색·삭제해 "기억하는 동료"의 신뢰 경계를 만듭니다.

## 기억 불변식

- 기억은 `profile_id`, `save_slot_id`, `companion_id` 범위 밖에서 보이지 않습니다.
- 각 Active 기억은 하나 이상 유효한 typed Message/Event source를 가집니다.
- 사용자 직접 발화, 검증된 게임 Event, LLM의 해석과 추론을 구분합니다.
- 레시피, 현재 게임 상태, Command 실행 권위는 장기기억이 아닙니다.
- 삭제된 기억은 retrieval, prompt, cache, 재증류에서 즉시 제외됩니다.
- 기억은 대화 참고 정보이며 UE 행동을 직접 강제하지 않습니다.

## 구현 순서

### CAI-P3-T01 candidate 검증과 migration

- [ ] 기존 `player_key` 중심 row를 profile/save/companion scope로 확장하는 migration 전략을 확정합니다.
- [ ] `MemoryType`: `ProfileFact`, `Preference`, `Episode`, `Promise`, `RelationshipEvidence` allowlist를 둡니다.
- [ ] source ID, source type, source mode, occurred time, status, importance, optional pinned 값을 저장합니다.
- [ ] source 없는 candidate, 타 scope source, 사용자 발화가 아닌 현실 사실, LLM 추론은 거부합니다.
- [ ] 기존 source 없는 episodic row는 자동 사실로 승격하지 않고 archive/quarantine 정책으로 이관하며 이관 리포트를 남깁니다.
- [ ] 유사·모순 기억은 병합, 갱신, 보류 중 하나로 명시적으로 처리합니다.

### CAI-P3-T02 retrieval·망각·환각 제어

- [ ] scope, status, type, source mode, 시간, 중요도, query 관련성을 모두 필터·ranking에 반영합니다.
- [ ] keyword와 기존 optional embedding을 먼저 사용하고 Vector DB는 도입하지 않습니다.
- [ ] Prompt에는 상위 소수 결과와 내부 trace ID만 전달하며 토큰 상한을 둡니다.
- [ ] 시간 감쇠는 archive 후보 선정에 사용하되 사용자가 고정한 기억과 법적 삭제를 혼동하지 않습니다.
- [ ] recall 횟수만으로 중요도가 무한 증가하지 않게 cap을 둡니다.
- [ ] 관련 없는 query, 검색 실패, LLM unavailable 상황에서 기억을 지어내지 않습니다.

### CAI-P3-T03 사용자 기억 제어

- [ ] 인증된 사용자 scope의 목록·상세·수정·삭제·초기화 API를 구현합니다.
- [ ] 수정은 원본 source를 바꾸지 않고 사용자 정정 정보와 audit reason을 남깁니다.
- [ ] 삭제·reset은 transaction, cache invalidation, background cursor/tombstone을 하나의 정책으로 처리합니다.
- [ ] Memory 삭제와 원문 Message retention을 분리합니다. 원문 retention 정책과 무관하게 tombstone이 JSONL 재증류로 기억이 부활하는 것을 막습니다.
- [ ] Admin CRUD와 일반 사용자 API의 권한·표시 필드를 분리합니다.

제안 endpoint:

```text
GET    /api/v1/memories
POST   /api/v1/memories/search
PATCH  /api/v1/memories/{memory_id}
DELETE /api/v1/memories/{memory_id}
POST   /api/v1/memories/reset
```

## 변경 예상 위치

| 목적 | 파일 |
|---|---|
| Memory domain/extraction | `app/brain/memory.py`, `app/brain/llm.py`, `app/brain/companion.py` |
| DB/repository | `app/db/models.py`, `app/db/episodic_memory_repository.py`, `app/episodic_memory_store.py`, migration |
| API/auth | `app/models.py`, `app/routes/`, `app/service.py` |
| error mapping | `app/errors.py`, `app/errors_http.py` |
| 삭제·전사 | `app/brain/transcript.py`, background cursor 구현 |
| 테스트 | `test_long_term_memory.py`, `test_episodic_memory_store.py`, 신규 source/delete/API fixture |

## 로컬 테스트 매트릭스

| 경우 | 기대 결과 |
|---|---|
| 직접 모바일 발화 + valid Message source | Preference/ProfileFact 후보만 저장 가능 |
| 없는 source 또는 타 scope source | candidate 거부 |
| LLM의 감정 추론 | 사용자 사실로 저장하지 않음 |
| 관련 query | 제한된 관련 기억과 trace ID만 prompt에 전달 |
| 무관 query | 과거 기억 비언급 |
| 삭제/reset | 검색·prompt·cache·재증류에서 즉시 제외 |
| 서버 재시작 | Active 기억과 tombstone 정책 유지 |
| LLM unavailable | 신규 거짓 기억 없음 |

## 완료 Gate

### Local Deterministic Tests Passed

- [ ] 모든 Active 기억의 source와 scope를 추적합니다.
- [ ] 삭제·reset·restart·foreign scope·unrelated recall fixture를 통과합니다.
- [ ] memory retrieval은 게임 사실과 Command 권위를 바꾸지 않습니다.

### Live Gemma Verification Pending

- [ ] 실제 추출 결과의 source precision/recall과 과도한 추론 비율 측정
- [ ] 실제 한국어 회상 대사의 시간 표현과 선제 언급 비율 평가

G5 Persistent Memory는 실제 사용자 삭제·배포 endpoint 검증 전 `Done`으로 표시하지 않습니다.
