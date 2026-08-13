# AIRE_SERVER Linux 원격 배포 구성

이 문서는 Linux 공개 서버에서 `AIRE_SERVER`를 systemd로 실행하고, 이후 개발 PC에서 SSH 한
줄로 코드를 갱신하는 절차입니다. 저장소 기준 실행 방식은 Python 3.13, `uv`, Alembic,
Uvicorn 단일 worker입니다. 저장소에는 Dockerfile과 Compose 파일이 없으므로 서버에 별도
Docker 구성이 확인되지 않는 경우에만 이 절차를 사용합니다.

`<linux-user>`, `<server-host>`, `<deploy-dir>`는 예시 문자가 아니라 서버에서 확인한 실제
값으로 바꿔야 합니다. 기존 서비스, `.env`, `data/`, reverse proxy 설정을 확인하기 전에는
중지하거나 덮어쓰지 않습니다.

## 1. 기존 서버 상태 확인

SSH로 서버에 접속한 뒤 다음 명령은 읽기 전용으로 실행합니다.

```bash
pwd
whoami
ps aux | grep -E '[u]vicorn|[g]unicorn'
systemctl list-units --type=service --all | grep -Ei 'aire|mako|uvicorn'
find /home /opt /srv -maxdepth 4 -type d -name AIRE_SERVER 2>/dev/null
```

기존 배포 폴더를 찾았다면 다음을 확인합니다.

```bash
cd <deploy-dir>

git branch --show-current
git rev-parse HEAD
git status --short
test -f .env && echo '.env exists' || echo '.env missing'
test -f data/companion.db && echo 'database exists' || echo 'database not found here'
uv --version
uv run alembic current
curl -fsS https://traip.mtvs2026.work/health
```

다음 중 하나라도 해당하면 구성을 중단하고 기존 운영 방식을 먼저 확인합니다.

- Docker container나 Compose 프로젝트가 이미 실행 중입니다.
- 실행 중인 Uvicorn의 working directory가 확인한 저장소와 다릅니다.
- Git branch가 `main`이 아니거나 추적 파일 변경이 있습니다.
- `.env` 또는 운영 DB 위치를 모릅니다.
- 기존 Health가 실패합니다.
- 서버가 private GitHub 저장소를 읽을 수 없습니다.

`.env`, 인증 token과 실제 사용자 데이터의 내용은 출력하거나 Git에 추가하지 않습니다.

## 2. 저장소와 런타임 준비

서버에는 Git, Python 3.13과 `uv`가 필요합니다. 기존 배포 폴더가 없다면 private 저장소용
read-only Deploy Key를 서버에 구성한 뒤 clone합니다. 개인 access token을 remote URL에
포함하지 않습니다.

```bash
git clone git@github.com:AIREProject/AIRE_SERVER.git <deploy-dir>
cd <deploy-dir>

uv sync --frozen
mkdir -p data
uv run alembic upgrade head
uv run alembic current
```

기존 운영 `.env`와 `data/`가 있다면 새 파일로 덮어쓰지 말고 기존 위치를 그대로 보존합니다.
새 서버를 Mock 설정으로 시작하는 경우에만 빈 `data/`를 만들 수 있습니다.

## 3. systemd 서비스 최초 구성

먼저 실제 배포 경로와 Linux 실행 계정을 기록합니다.

```bash
cd <deploy-dir>
pwd
whoami
test -x .venv/bin/uvicorn && echo 'uvicorn executable OK'
```

`/etc/systemd/system/aire-server.service`를 다음 내용으로 만듭니다. `<linux-user>`와
`<deploy-dir>`를 위에서 확인한 값으로 바꿉니다.

```ini
[Unit]
Description=AIRE FastAPI Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<linux-user>
WorkingDirectory=<deploy-dir>
ExecStart=<deploy-dir>/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

공개 HTTPS는 기존 reverse proxy가 `127.0.0.1:8010`으로 전달하는 구성을 유지합니다. Uvicorn
worker는 SQLite와 process-local 상태 때문에 1개만 사용합니다.

서비스를 등록하고 최초 기동을 확인합니다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now aire-server
sudo systemctl status aire-server --no-pager
sudo journalctl -u aire-server -n 100 --no-pager

curl -fsS http://127.0.0.1:8010/health
curl -fsS https://traip.mtvs2026.work/health
```

내부 Health와 공개 Health가 모두 HTTP 200이고 `status=ok`여야 합니다. 실패하면 반복 재시작하지
말고 `journalctl`의 첫 오류와 reverse proxy 설정을 확인합니다.

## 4. 반복 배포 스크립트 설치

다음 스크립트를 서버의 `/usr/local/sbin/deploy-aire-server`에 설치합니다. `DEPLOY_DIR`는 실제
경로로 바꿉니다. 이 스크립트는 배포 전 상태가 깨끗한 `main`인지 확인하고, SQLite를 정상 종료
후 백업한 다음 코드, 의존성, migration 순서로 갱신합니다.

```bash
#!/usr/bin/env bash
set -euo pipefail

DEPLOY_DIR="<deploy-dir>"
SERVICE_NAME="aire-server"
PUBLIC_HEALTH_URL="https://traip.mtvs2026.work/health"

cd "$DEPLOY_DIR"

if [ ! -x "$DEPLOY_DIR/.venv/bin/uvicorn" ]; then
    echo "Deployment aborted: Uvicorn executable is missing." >&2
    exit 1
fi

if [ ! -d "$DEPLOY_DIR/data" ]; then
    echo "Deployment aborted: data directory is missing." >&2
    exit 1
fi

if [ "$(git branch --show-current)" != "main" ]; then
    echo "Deployment aborted: current branch is not main." >&2
    exit 1
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Deployment aborted: tracked working tree changes exist." >&2
    git status --short
    exit 1
fi

curl -fsS "$PUBLIC_HEALTH_URL" >/dev/null
git fetch origin main
git log --oneline --decorate HEAD..origin/main

sudo systemctl stop "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager || true

backup_stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="${DEPLOY_DIR}_data_backup_${backup_stamp}"
cp -a "$DEPLOY_DIR/data" "$backup_dir"
echo "Database backup: $backup_dir"

git pull --ff-only origin main
uv sync --frozen
uv run alembic upgrade head
uv run alembic current

sudo systemctl start "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager

for attempt in 1 2 3 4 5; do
    if curl -fsS "$PUBLIC_HEALTH_URL"; then
        echo
        echo "Deployment completed successfully."
        exit 0
    fi
    sleep 2
done

echo "Deployment failed: public health check did not recover." >&2
sudo journalctl -u "$SERVICE_NAME" -n 100 --no-pager
exit 1
```

설치 후 소유권과 실행 권한을 제한합니다.

```bash
sudo chown root:root /usr/local/sbin/deploy-aire-server
sudo chmod 0755 /usr/local/sbin/deploy-aire-server
```

배포 계정이 비밀번호 입력 없이 원격 배포해야 한다면 `visudo`로 다음 명령만 허용합니다.
`<linux-user>`를 실제 배포 계정으로 바꿉니다. 명령의 실제 경로는 `command -v systemctl`과
`command -v journalctl`로 확인합니다.

```sudoers
<linux-user> ALL=(root) NOPASSWD: /usr/bin/systemctl stop aire-server, /usr/bin/systemctl start aire-server, /usr/bin/systemctl status aire-server --no-pager, /usr/bin/journalctl -u aire-server -n 100 --no-pager
```

광범위한 passwordless sudo나 root SSH 로그인은 사용하지 않습니다.

## 5. 개발 PC에서 원격 갱신

서버 구성과 SSH key 인증이 끝나면 개발 PC에서 다음 한 줄만 실행합니다.

```powershell
ssh <linux-user>@<server-host> /usr/local/sbin/deploy-aire-server
```

커밋을 명시적으로 서버에 반영하려면 먼저 로컬 변경을 검증하고 `main`에 push한 뒤 위 명령을
실행합니다. 서버에 파일을 직접 복사해 Git 작업 트리를 수정하지 않습니다.

## 6. 실패 처리

- `git pull`, `uv sync` 또는 migration이 실패하면 스크립트는 서비스를 다시 시작하지 않습니다.
- 같은 migration이나 재시작을 무작정 반복하지 않습니다.
- `.env`, `data/`, 백업 파일을 삭제하지 않습니다.
- `git reset --hard`, Alembic downgrade와 SQLite 파일 교체를 임의로 실행하지 않습니다.
- `sudo systemctl status aire-server --no-pager`와
  `sudo journalctl -u aire-server -n 100 --no-pager`에서 첫 오류를 확인합니다.
- 새 코드만 문제이고 migration 호환성이 확인된 경우에만 알려진 정상 커밋으로 복구합니다.
- DB 변경이 포함됐다면 코드만 되돌리지 말고 백업 복원 여부를 먼저 결정합니다.

기존 서버 갱신과 기능별 smoke test는 [`SERVER_UPDATE_GUIDE.md`](SERVER_UPDATE_GUIDE.md)와
[`docs/하는방법.md`](docs/하는방법.md)를 이어서 확인합니다.
