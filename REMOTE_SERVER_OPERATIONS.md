# AIRE 서버 원격 운영 가이드

이 문서는 `AIRE_SERVER`의 원격 접속, 코드 배포, 서비스 재시작과 로그 확인을 위한 단일 운영
기준입니다. 관리 PC와 서버가 같은 공유기/LAN에 연결된 상태에서 서버의 사설 IP로 SSH에
접속합니다. 외부망 접속은 이 문서의 범위가 아닙니다.

공인 인터넷에 SSH 22번과 Uvicorn 8000번 포트를 직접 열지 않습니다. 기존 Cloudflare Tunnel은
공개 HTTPS API용으로 유지하며 SSH transport로 사용하지 않습니다.

## 1. 확정된 서버 구성

| 항목 | 현재 값 |
|---|---|
| Linux 사용자 | `mtvs-1` |
| LAN 주소 | `192.168.0.55` — DHCP에 따라 변경될 수 있음 |
| 저장소 | `/home/mtvs-1/workspace/AIRE_SERVER` |
| 서비스 | user systemd `aire-server.service` |
| 배포 명령 | `/home/mtvs-1/.local/bin/deploy-aire-server` |
| 내부 API | `127.0.0.1:8000` |
| 공개 API | `https://traip.mtvs2026.work` |

이 서버는 root systemd 서비스가 아닙니다. 다음 명령을 사용하지 않습니다.

```text
sudo systemctl restart aire-server
uvicorn ... --port 8010
```

정확한 서비스와 포트는 다음과 같습니다.

```bash
systemctl --user restart aire-server.service
systemctl --user status aire-server.service --no-pager
```

## 2. LAN 접속 조건

관리 PC와 서버가 같은 공유기 또는 동일한 사설망에 연결돼 있어야 합니다. 서버 콘솔에서 현재
LAN 주소를 확인합니다.

```bash
hostname -I
```

현재 기준 주소는 `192.168.0.55`입니다. DHCP로 주소가 바뀌면 관리 PC의 SSH 설정도 함께
바꿔야 하므로, 반복 운영할 때는 공유기에서 이 서버의 DHCP 주소를 고정 할당하는 것을
권장합니다.

공유기에서 SSH 22번을 인터넷에 포트포워딩하지 않습니다. 외부 네트워크에서는
`192.168.0.55`에 접근할 수 없는 것이 정상입니다.

## 3. 서버: OpenSSH와 공개 키 등록

OpenSSH를 실행하고 22번 포트가 listen 중인지 확인합니다.

```bash
sudo systemctl enable --now ssh
sudo systemctl status ssh --no-pager
ss -ltn | grep ':22 '
```

Ubuntu `ufw`가 활성 상태라면 먼저 현재 LAN 정책을 확인합니다. 기존 규칙을 모르는 상태에서
방화벽을 끄거나 인터넷 전체에 새 규칙을 추가하지 않습니다.

```bash
sudo ufw status
```

관리 PC에 키가 없다면 관리 PC PowerShell에서 생성합니다.

```powershell
$sshDirectory = Join-Path $env:USERPROFILE ".ssh"
$privateKey = Join-Path $sshDirectory "id_ed25519"

New-Item -ItemType Directory -Force $sshDirectory | Out-Null
if (-not (Test-Path $privateKey)) {
    ssh-keygen -t ed25519 -C "aire-server-admin" -f $privateKey
}

Get-Content "$privateKey.pub"
```

출력된 `ssh-ed25519 ... aire-server-admin` 한 줄만 서버의 `<관리-PC-공개키>` 자리에 붙여서
실행합니다. 개인 키 `id_ed25519`은 서버, Git, 메신저 또는 문서에 복사하지 않습니다.

```bash
install -d -m 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

public_key='<관리-PC-공개키>'
grep -qxF "$public_key" ~/.ssh/authorized_keys || printf '%s\n' "$public_key" >> ~/.ssh/authorized_keys
```

## 4. 관리 PC: LAN 접속과 SSH 별칭 설정

관리 PC가 서버와 같은 LAN에 연결된 상태에서 PowerShell로 먼저 접속합니다.

```powershell
ping 192.168.0.55
ssh mtvs-1@192.168.0.55 "whoami; hostname"
```

`$env:USERPROFILE\.ssh\config`의 AIRE 항목을 다음으로 설정합니다. 기존 `Host aire-server`
항목이 있다면 새 항목을 중복 추가하지 말고 아래 값으로 교체합니다.

```sshconfig
Host aire-server
    HostName 192.168.0.55
    User mtvs-1
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
    ConnectTimeout 10
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

접속을 검증합니다.

```powershell
ssh aire-server "whoami; hostname"
```

정상 기준은 Linux 사용자가 `mtvs-1`로 출력되는 것입니다.

## 5. 원격 운영 사전 점검

서비스와 배포 경로를 변경하기 전에 읽기 전용으로 확인합니다.

```powershell
ssh aire-server "systemctl --user is-active aire-server.service"
ssh aire-server "cd /home/mtvs-1/workspace/AIRE_SERVER && git branch --show-current && git status --short && git rev-parse HEAD"
ssh aire-server "test -x /home/mtvs-1/.local/bin/deploy-aire-server && echo deploy-script-ok"
curl.exe -fsS https://traip.mtvs2026.work/health
```

정상 기준:

- 서비스가 `active`입니다.
- 서버 Git branch가 `main`입니다.
- `git status --short`에 추적 파일 변경이 없습니다.
- 배포 스크립트가 실행 가능합니다.
- 공개 Health가 HTTP 200과 `status=ok`를 반환합니다.

## 6. GitHub 코드 변경 후 한 줄 배포

서버의 Git 추적 파일을 SSH로 직접 수정하지 않습니다. 관리 PC에서 검증한 변경을 `main`에
push한 다음 서버의 기존 배포 스크립트를 실행합니다.

```powershell
ssh aire-server /home/mtvs-1/.local/bin/deploy-aire-server
```

배포 스크립트는 다음 순서로 동작해야 합니다.

1. 서버가 변경 없는 `main`인지 확인
2. 공개 Health와 GitHub 접근 확인
3. `systemctl --user stop aire-server.service`로 정상 종료
4. `data/` 전체를 저장소 밖에 타임스탬프 백업
5. `git pull --ff-only origin main`
6. `uv sync --frozen`
7. `uv run alembic upgrade head`와 `uv run alembic current`
8. `systemctl --user start aire-server.service`
9. 서비스 상태, 로그와 공개 Health 확인

배포 후 확인합니다.

```powershell
ssh aire-server "systemctl --user status aire-server.service --no-pager"
ssh aire-server "journalctl --user -u aire-server.service -n 100 --no-pager"
curl.exe -fsS https://traip.mtvs2026.work/health
curl.exe -fsS -o NUL -w "%{http_code}`n" https://traip.mtvs2026.work/docs
```

## 7. 재시작, 로그와 운영 설정

코드 변경 없이 재시작과 상태 확인만 할 때:

```powershell
ssh aire-server "systemctl --user restart aire-server.service && systemctl --user status aire-server.service --no-pager"
```

최근 로그 100줄:

```powershell
ssh aire-server "journalctl --user -u aire-server.service -n 100 --no-pager"
```

실시간 로그:

```powershell
ssh -t aire-server "journalctl --user -u aire-server.service -f"
```

`.env`는 Git으로 배포하지 않습니다. 변경이 필요하면 SSH로 접속해 기존 파일을 백업하고 직접
편집한 뒤 user service를 재시작합니다.

```bash
cd /home/mtvs-1/workspace/AIRE_SERVER
backup_stamp="$(date +%Y%m%d-%H%M%S)"
cp -a .env ".env.backup-${backup_stamp}"
nano .env

systemctl --user restart aire-server.service
systemctl --user status aire-server.service --no-pager
curl -fsS https://traip.mtvs2026.work/health
```

API key, token과 실제 사용자 대화 원문을 화면 공유, 채팅, 명령 기록 또는 Git에 남기지 않습니다.

## 8. LAN 연결 장애 시 확인 순서

같은 LAN에서 접속되지 않으면 다음 순서로 확인합니다.

관리 PC:

```powershell
ping 192.168.0.55
ssh -vv aire-server "whoami"
```

서버 콘솔:

```bash
hostname -I
sudo systemctl status ssh --no-pager
ss -ltn | grep ':22 '
```

- `hostname -I`의 주소가 바뀌었다면 관리 PC의 `HostName`을 새 LAN 주소로 갱신합니다.
- ping은 되는데 SSH가 실패하면 OpenSSH, LAN 방화벽과 공개 키를 확인합니다.
- SSH는 되는데 배포가 실패하면 Git 상태, private 저장소 인증, migration과 user service 로그를
  확인합니다.
- Cloudflare 공개 Health만 정상인 것은 LAN SSH가 정상이라는 뜻이 아닙니다. 두 경로는 독립적으로
  진단합니다.

## 9. 금지 사항과 복구 원칙

- 공유기에서 SSH 22번 또는 Uvicorn 8000번을 인터넷에 포트 포워딩하지 않습니다.
- Uvicorn을 `0.0.0.0:8000`으로 바꾸거나 worker를 2개 이상 실행하지 않습니다.
- 서버에서 `app/`, `migrations/`, `tests/` 등 Git 추적 파일을 직접 수정하지 않습니다.
- `.env`, `data/`, SSH 개인 키와 Cloudflare token을 Git에 추가하지 않습니다.
- 배포 실패 시 같은 명령을 반복하거나 `git reset --hard`, Alembic downgrade와 DB 교체를
  임의로 실행하지 않습니다.
- migration 실패 후에는 서비스를 성공으로 기록하지 않고 최신 백업과 첫 오류를 확인합니다.

기능별 배포 smoke와 DB 복구는 [`SERVER_UPDATE_GUIDE.md`](SERVER_UPDATE_GUIDE.md)와
[`docs/하는방법.md`](docs/하는방법.md)를 따릅니다.
