# AIRE_SERVER 실서버 갱신 방법

이 문서는 GitHub `main` 브랜치의 최신 코드를 공개 서버
`https://traip.mtvs2026.work`에 반영하는 체크리스트입니다.

저장소에는 Dockerfile과 Compose 파일이 없습니다. 서버가 별도 Docker 설정을 사용한다면
아래 **Docker Compose 방식**을, 아니라면 **systemd 방식**을 사용합니다. 실제 배포 경로와
서비스 이름을 모르면 추측하지 말고 먼저 서버 관리자에게 확인합니다.

## 1. 갱신 전 확인

서버에서 실제 값을 기록합니다.

```bash
deploy_dir="/실제/AIRE_SERVER/경로"
service_name="실제-systemd-service-name"

cd "$deploy_dir"
git branch --show-current
git rev-parse HEAD
git status --short
curl -fsS https://traip.mtvs2026.work/health
```

다음 조건이면 갱신을 중단합니다.

- `main` 브랜치가 아님
- `git status --short`에 추적 파일 변경이 있음
- `.env` 또는 운영 DB/volume의 위치를 모름
- 현재 Health가 이미 실패함
- 실행 방식이나 서비스 이름을 모름

`.env`, `data/`, 인증 토큰의 내용은 출력하거나 Git에 추가하지 않습니다.

원격에 반영될 커밋을 먼저 확인합니다.

```bash
git fetch origin main
git log --oneline --decorate HEAD..origin/main
```

## 2-A. systemd + Uvicorn 방식

SQLite를 안전하게 백업하기 위해 서비스를 먼저 정상 종료합니다.

```bash
cd "$deploy_dir"
sudo systemctl stop "$service_name"
sudo systemctl status "$service_name" --no-pager
```

운영 데이터가 저장소의 `data/`에 있다면 삭제하지 말고 별도 위치에 백업합니다.

```bash
backup_stamp="$(date +%Y%m%d-%H%M%S)"
cp -a "$deploy_dir/data" "${deploy_dir}_data_backup_${backup_stamp}"
```

코드, 의존성, DB schema 순서로 갱신합니다.

```bash
git pull --ff-only origin main
uv sync --frozen
uv run alembic upgrade head
uv run alembic current
```

Migration이 실패하면 서비스를 시작하지 말고 오류와 백업 경로를 기록합니다.

정상이면 서비스를 다시 시작합니다.

```bash
sudo systemctl start "$service_name"
sudo systemctl status "$service_name" --no-pager
sudo journalctl -u "$service_name" -n 100 --no-pager
```

## 2-B. Docker Compose 방식

이 방식은 서버의 별도 배포 설정에 Compose 파일이 실제로 있을 때만 사용합니다.

```bash
cd "$deploy_dir"
docker compose config --services
docker compose ps
```

출력에서 API service 이름을 확인한 뒤 아래 값을 채웁니다.

```bash
api_service="실제-api-service-name"
```

서비스를 멈추고 기존 DB bind mount 또는 named volume이 보존되는지 확인합니다.

```bash
docker compose stop "$api_service"
docker compose ps
```

그다음 코드를 받고 image, migration, container 순서로 갱신합니다.

```bash
git pull --ff-only origin main
docker compose build "$api_service"
docker compose run --rm "$api_service" uv run alembic upgrade head
docker compose run --rm "$api_service" uv run alembic current
docker compose up -d --no-deps "$api_service"
docker compose ps
docker compose logs --tail 100 "$api_service"
```

Registry image를 사용하는 서버라면 `docker compose build` 대신 관리자가 지정한 tag를
받도록 Compose 설정을 갱신한 후 `docker compose pull "$api_service"`을 사용합니다.

`docker compose down`, `docker volume rm`, `docker system prune`은 이 절차에서 사용하지
않습니다.

## 3. 공개 서버 검증

서버 재시작 직후 다음 세 항목을 확인합니다.

```bash
curl -fsS https://traip.mtvs2026.work/health
curl -fsS -o /dev/null -w '%{http_code}\n' https://traip.mtvs2026.work/docs
curl -fsS https://traip.mtvs2026.work/openapi.json | grep -q 'CraftItem' \
  && echo 'CraftItem contract OK' \
  || echo 'CraftItem contract missing'
```

정상 기준:

- Health HTTP `200`, `status=ok`
- Swagger HTTP `200`
- 이번 AX-I06 배포에서는 OpenAPI에 `CraftItem`이 존재
- Health의 `llm_provider`가 배포 전 값과 동일
- 서비스 로그에 반복 재시작, traceback, DB table 오류가 없음

Health는 DB와 LLM까지 보장하지 않습니다. 이어서 실제 UE 또는
[`docs/하는방법.md`](docs/하는방법.md)의 Chat smoke 요청으로 다음을 확인합니다.

1. `안녕` — 일반 대화 응답
2. `철검 제작법 알려줘` — 제작법 설명만 반환하고 Command 후보는 없음
3. `철검 만들어줘` — `CraftItem` 후보와 정확한 parameters
   `{"recipe_id":"recipe-11","quantity":1}` 반환
4. `철검 2개 만들어줘` — 지원하지 않는 수량이므로 Craft 후보 없음

## 4. 실패 시 처리

- 같은 migration이나 재시작 명령을 무작정 반복하지 않습니다.
- `.env`, `data/`, Docker volume과 백업을 삭제하지 않습니다.
- `git reset --hard`를 사용하지 않습니다.
- DB migration downgrade는 데이터 손실 가능성을 검토하기 전에는 실행하지 않습니다.
- 새 코드만 문제이고 migration 호환성이 확인된 경우에만 알려진 정상 커밋으로 코드를
  되돌린 뒤 의존성을 다시 맞추고 서비스를 재시작합니다.

담당자에게 다음 정보만 전달합니다. `.env` 내용과 인증 header는 전달하지 않습니다.

```text
배포 시각:
배포 전 커밋:
배포 후 커밋:
배포 방식: systemd / Docker Compose
Alembic current 결과:
Health 결과:
Swagger HTTP 상태:
Chat smoke 결과:
서비스 로그의 첫 오류:
운영 data/volume 위치:
백업 위치:
```

폴더 전체를 복사해 교체하는 배포라면 이 문서의 Git 갱신 절차 대신
[`docs/하는방법.md`](docs/하는방법.md)를 처음부터 끝까지 따릅니다.
