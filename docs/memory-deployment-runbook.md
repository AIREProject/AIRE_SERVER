# Memory 배포 전 Runbook

## 고정 정책

- `COMPANION_PROMPT_VERSION=companion-v5`
- `TRANSCRIPT_ENABLED=false`, opt-in 개발 Transcript는 최대 1일
- 일반 player/companion Message와 GameEvent 원문은 7일
- content가 제거된 audit/idempotency ledger는 30일
- Active Memory가 참조하는 canonical source는 만료시키지 않음
- Memory는 사용자 delete/reset 전까지 Active이며 시간 감쇠는 검색 순위에만 적용
- Memory worker는 단일 Uvicorn process에서 leased outbox를 소비

## Legacy JSONL

현재 Git tree에는 `data/transcripts/`와 `data/memories/` 원문을 두지 않습니다. importer는
player 원문만 `LegacyUnknown` Message로 enqueue하고, 실제 Memory 승인은 같은 worker가
수행합니다. companion 발화는 가져오지 않으며 Legacy source는 관계 상태 증거가 아닙니다.

```powershell
uv run python -m scripts.import_legacy_transcripts --dry-run --source-dir <backup-path>
uv run python -m scripts.import_legacy_transcripts --apply --source-dir <backup-path>
```

apply 성공 원본은 hash·cursor·건수 report와 함께 `data/transcript_quarantine/`로 이동하고
30일 뒤 retention sweep이 hash를 다시 확인한 뒤 삭제합니다. 과거 Git history는 rewrite하지
않으므로 저장소는 Private 접근 제한을 유지하고, history 보존 예외는 운영 개인정보 목록에
기록합니다.

## 배포 순서

1. 서비스와 DB/WAL 백업
2. `.env` preflight 및 secret 미추적 확인
3. legacy importer `--dry-run`
4. 서비스 정지
5. `uv run alembic upgrade head`
6. 필요한 경우 importer `--apply`
7. 단일 Uvicorn process 재시작
8. `/health`와 `/ready` 확인
9. Event, Memory, Command Result API smoke
10. 공개 `/openapi.json`에 모든 경로가 반영된 뒤 Web `VITE_MEMORY_ENABLED=true`
11. Mobile Memory 목록·검색·정정·고정·삭제·reset 실제 조작

DB 연결 또는 Alembic revision 불일치는 `/ready` 503이며 배포를 중단합니다. LLM 분류 장애는
HTTP 200 `degraded`와 Mock fallback으로 허용하되, 원인을 기록하고 실제 provider 행렬을 별도
검증합니다. Production 배포와 Web 기능 활성화는 명시적 승인 뒤에만 수행합니다.
