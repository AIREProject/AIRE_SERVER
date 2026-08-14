# CAI-P4. 관계 상태와 Cross-device 유대 수직 슬라이스

- 상태: Planned
- 선행: CAI-P3 Local Deterministic Tests Passed
- 후속: CAI-P5, CAI-P6
- 기존 계획: M05-E04, M06, G6 Core Bond Demo

## 목표

사용자가 모바일에서 직접 공유한 내용을 MAKO가 다음 인게임의 관련 상황에서만 올바른 시간 맥락으로 회상하고, 그 근거가 관계 변화와 대사 어조에 제한적으로 반영되는 경험을 만듭니다.

## 관계 상태 원칙

- 관계는 숫자 UI가 아니라 제한된 의미 상태(`Low`, `Growing`, `High`)로 표현합니다.
- 상태 변경은 검증된 Message/Event 근거와 Backend 규칙만 사용합니다.
- LLM은 관계를 점수화하거나 상태를 변경하지 않고 대사 어조만 조정합니다.
- 반복 채팅만으로 상태를 무한히 높일 수 없도록 cooldown, cap, event 다양성 규칙을 둡니다.
- 관계는 전투·생존·GAS 안전 규칙을 덮어쓰지 않습니다.

## 구현 순서

### CAI-P4-T01 관계 domain

- [ ] Relationship state와 변화 사유, source reference, occurred time, audit trail을 저장합니다.
- [ ] `RelationshipEvidence` memory와 상태 계산을 분리합니다.
- [ ] 상태 전이는 Backend rule table로 제한하고, LLM output에는 read-only presentation state만 전달합니다.
- [ ] 관계가 없는 대화, 반복 메시지, 삭제된 source는 상태 변경 근거로 쓰지 않습니다.
- [ ] 사용자 UI에는 수치가 아닌 대화·무드 차이만 노출합니다.

### CAI-P4-T02 로컬 핵심 시나리오 fixture

```text
1. 모바일 사용자가 밤에 혼자 다니는 것이 무섭다고 직접 말한다.
2. RealWorld Message와 stable source ID가 저장된다.
3. source 검증을 통과한 Preference/ProfileFact가 승격된다.
4. 다음 인게임 세션에서 UE가 밤 지역 진입 Event를 보낸 것으로 fixture가 재현한다.
5. Backend가 관련 기억만 선택한다.
6. MAKO가 과거형으로 자연스럽게 참고하는 대사 Candidate를 생성한다.
7. Follow/보호 행동은 UE Gateway가 별도로 검증한다.
8. 기억 삭제 뒤 다음 세션에서 다시 언급하지 않는다.
```

- [ ] source, scope, RealWorld/GameWorld time, 관련/무관 상황, 삭제·재시작을 fixture에 넣습니다.
- [ ] 대사 exact match가 아니라 사실·시간·출처·비언급 의미 계약을 검사합니다.
- [ ] LLM 실패·기억 없음·Backend unavailable 대조 시나리오를 포함합니다.

### CAI-P4-T03 실제 3-device Gate 준비

- [ ] Mobile Message, UE Event, memory retrieval, Chat response의 audit correlation을 정의합니다.
- [ ] Backend 장애 중 UE 로컬 AI 유지와 모바일 전송 대기 행동을 명시합니다.
- [ ] 반복 횟수, 재시작 순서, 허용되지 않는 회상·행동을 G6 fixture에 기록합니다.

## 변경 예상 위치

| 목적 | 파일 |
|---|---|
| 관계 domain/service | `app/brain/`, `app/db/models.py`, repository, migration |
| prompt presentation state | `app/brain/dialogue.py`, `app/brain/llm.py` |
| cross-device fixture | `tests/fixtures/companion_ai/`, memory/relationship 통합 테스트 |
| 계획 연결 | `.agents/docs/planning/M05_llm_relationship_memory.md`, `M06_cross_device_bond_demo.md` |

## 완료 Gate

### Local Deterministic Tests Passed

- [ ] mobile source → memory → game Event → related recall 흐름을 SQLite fixture로 재현합니다.
- [ ] 다른 save/companion, 무관 상황, 삭제 후, restart 후 negative case를 통과합니다.
- [ ] relation state가 Command/GAS 권위를 변경하지 않습니다.

### Deployed Integration Pending

- [ ] 실제 UE Event endpoint와 모바일 Message가 같은 profile/save/companion scope로 연결됨
- [ ] 실제 디바이스 3회 반복, Backend/LLM 재시작, 모바일 단절·복구 검증

CAI-G4는 G5와 G6을 잇는 내부 수직 슬라이스 Gate입니다. 실제 G6은 배포 계약과 3-device 검증이 끝날 때만 통과합니다.
