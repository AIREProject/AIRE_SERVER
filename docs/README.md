# AIRE Server 문서 안내

이 폴더에는 현재 코드로 서버를 설치하고 운영하는 데 필요한 문서만 둡니다. 과거
`/v1/companion/*`, random device pairing 중심 문서와 구현 전 계획은 제거했습니다.

## 처음 보는 사람의 읽기 순서

1. [인수인계·운영 가이드](handoff.md)
2. [LLM 설정](llm-setup.md)
3. [API 사용법](api-endpoints.md)
4. [게임 데이터](game-data.md)
5. [현재 제한과 임시 경계](temporary-scaffolds.md)

## 문서별 역할

| 문서 | 용도 |
|---|---|
| `handoff.md` | 새 PC 설치, DB migration, 서버 시작, 업데이트, 백업·복구와 장애 대응 |
| `llm-setup.md` | Mock/OpenAI/Local LLM 및 장기기억 Embedding 설정 |
| `api-endpoints.md` | 클라이언트가 호출하는 현행 `/api/v1/*` 계약과 예시 |
| `game-data.md` | Item/Recipe/Enemy seed 데이터와 DB 반영 절차 |
| `temporary-scaffolds.md` | 현재 의도적으로 남긴 제한, 미구현 기능과 확장 전 확인사항 |

## 권위 순서

문서와 구현이 다르면 다음 순서로 판단합니다.

1. 실행 중인 서버의 `/openapi.json`
2. `app/models.py`, 각 `app/routes/*.py`, `app/settings.py`
3. Alembic `migrations/versions/*.py`
4. 이 폴더의 운영 문서

Swagger UI는 `/docs`, OpenAPI JSON은 `/openapi.json`에서 확인할 수 있습니다.

## 현재 제품 기준

- 단일 플레이어: `AIRE_OPEN`
- 단일 Save Slot: `demo-slot-1`
- 단일 Companion: `mako`
- UE Bearer: `AIRE_GAME`
- Web Bearer: `AIRE_WEB`
- 기본 Transport: HTTP `POST /api/v1/chat`
- 기본 LLM: `mock`
- 기본 DB: SQLite `data/companion.db`

고정 Bearer 두 개는 같은 플레이어의 서로 다른 client role입니다. 다중 사용자, 로그인,
Save Slot 선택과 Companion 선택은 현재 제품 범위에 없습니다.
