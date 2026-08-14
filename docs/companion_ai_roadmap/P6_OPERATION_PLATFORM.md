# CAI-P6. 운영 안정화와 두 번째 게임 Adapter

- 상태: Planned
- 선행: CAI-P4 실제 Cross-device Gate, CAI-P5 배포 계약
- 기존 계획: M07, G7 Recovery

## 목표

서버 측 기억과 대화를 안전하게 복구·삭제·관찰할 수 있게 만들고, 첫 게임에서 검증한 경계를 두 번째 게임에 최소 Adapter로 적용할 수 있는 기반을 준비합니다.

## 운영 권위

현재 코드·운영 절차의 1차 근거는 다음 문서입니다.

- `AIRE_SERVER/docs/handoff.md`
- `AIRE_SERVER/docs/하는방법.md`
- `AIRE_SERVER/docs/llm-setup.md`
- `AIRE_SERVER/docs/temporary-scaffolds.md`

과거 C PC 자체 구축 문서는 역사적 참고이며, 실제 배포와 복구의 권위가 아닙니다.

## 구현 순서

### CAI-P6-T01 DB·복구

- [ ] 단일 사용자 demo는 server-side SQLite를 유지하고, 다중 user/worker 전 PostgreSQL 전환 기준을 문서화합니다.
- [ ] DB와 transcript, memory tombstone, migration version, 설정의 백업 범위를 정의합니다.
- [ ] 실제 backup→새 저장소 restore→Profile/Device/Memory 일치 검증을 수행합니다.
- [ ] migration rollback·복구 실패가 데이터 손상으로 이어지지 않게 합니다.
- [ ] DB와 Local LLM Runtime 포트를 UE/Web에 직접 노출하지 않습니다.

### CAI-P6-T02 보안·개인정보·관측

- [ ] HTTPS, token rotation, device role, request size/rate limit을 점검합니다.
- [ ] 로그와 prompt에서 token·불필요한 현실 대화 원문을 제거합니다.
- [ ] 삭제 기억이 backup 정책, retrieval, prompt, cache에서 어떻게 처리되는지 명시합니다.
- [ ] chat/LLM/DB latency, fallback, error, memory retrieval, 비용 지표를 수집합니다.
- [ ] health는 DB/LLM readiness와 구분해 보고합니다.

### CAI-P6-T03 두 번째 게임 Adapter Pilot

첫 게임의 CAI-G4/G6이 통과한 뒤에만 시작합니다.

- [ ] `game_id`, `content_version`, `schema_version`과 namespaced stable ID를 고정합니다.
- [ ] 각 게임은 Identity/Capability, Context Provider, Command Resolver, Event Sink, Snapshot/Outbox를 구현합니다.
- [ ] 공통화는 HTTP transport, schema validation, stable ID, operation correlation에 제한합니다.
- [ ] game-specific State/AI/Inventory와 최종 실행 권위는 Adapter가 소유합니다.
- [ ] 범용 SDK, 모든 명령 통합, 게임 데이터 공유는 Pilot 범위에서 제외합니다.

## 검증

| 경우 | 기대 결과 |
|---|---|
| Backend 재부팅 | 문서화된 순서로 서비스·기억 복구 |
| backup restore | source·memory·삭제 상태 정책이 일치 |
| LLM 미준비 | 오류가 성공처럼 표시되지 않고 UE local AI 유지 |
| 민감 로그 점검 | token·불필요한 원문 없음 |
| 두 번째 게임 unknown ID | Adapter가 거부, 원본 게임 상태 불변 |

## 완료 Gate

### G7 Recovery

- [ ] HTTPS와 단일 진입점
- [ ] DB/LLM 직접 노출 차단
- [ ] backup/restore 실제 검증
- [ ] token·로그·기억 삭제 보안 점검
- [ ] latency·fallback·VRAM/메모리·오류 예산 기록

### Adapter Pilot Gate

- [ ] 첫 게임과 두 번째 게임에서 같은 versioned Event/Result 계약을 안전하게 해석
- [ ] 어느 게임도 다른 게임의 entity ID나 gameplay state를 직접 변경하지 않음

이 단계는 실제 운영과 배포 검증이 필수입니다. 로컬 fixture만으로 `Done` 처리하지 않습니다.
