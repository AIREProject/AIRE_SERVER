# 다른 컴퓨터에서 AIRE 서버 원격 관리하기

이 문서는 다른 Windows/Linux PC에서 SSH로 AIRE 서버에 접속해 코드를 배포하고, 운영 설정을
수정하고, 서비스를 재시작하거나 로그를 확인하는 방법입니다. 현재 서버 구성에 맞춘 값은
다음과 같습니다.

| 항목 | 현재 값 |
|---|---|
| SSH 사용자 | `mtvs-1` |
| 서버 LAN 주소 | `192.168.0.55` |
| 저장소 | `/home/mtvs-1/workspace/AIRE_SERVER` |
| 서비스 | user systemd `aire-server.service` |
| 배포 명령 | `/home/mtvs-1/.local/bin/deploy-aire-server` |
| 내부 API | `127.0.0.1:8000` |
| 공개 API | `https://traip.mtvs2026.work` |

LAN 주소는 DHCP에 따라 바뀔 수 있습니다. 반복해서 사용할 PC라면 공유기에서 서버에 DHCP
고정 할당을 설정하거나, 승인된 VPN의 고정 주소를 사용합니다. 현재 Cloudflare Tunnel은
HTTPS API용이며 SSH 접속 주소가 아닙니다. SSH 포트와 Uvicorn 포트를 인터넷에 임의로
노출하지 않습니다.

## 1. 최초 SSH 키 등록

관리 PC에 OpenSSH가 설치되어 있는지 확인합니다.

```powershell
ssh -V
```

개인 키가 없다면 관리 PC에서 한 번만 생성합니다. 기본 경로를 사용하고 개인 키 파일은
서버나 Git에 복사하지 않습니다.

```powershell
ssh-keygen -t ed25519 -C "aire-server-admin"
```

서버와 같은 LAN 또는 승인된 VPN에 연결한 상태에서 공개 키를 등록합니다. 처음 한 번은 서버
계정 비밀번호가 필요할 수 있습니다.

Windows PowerShell:

```powershell
Get-Content "$env:USERPROFILE\.ssh\id_ed25519.pub" |
    ssh mtvs-1@192.168.0.55 "umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
```

Linux/macOS:

```bash
ssh-copy-id mtvs-1@192.168.0.55
```

접속을 확인합니다.

```powershell
ssh mtvs-1@192.168.0.55 "whoami; hostname"
```

서버의 host key 지문이 예상과 다르다는 경고가 나오면 무시하거나 기존 키를 바로 삭제하지
말고 서버가 재설치됐는지 먼저 확인합니다.

## 2. SSH 별칭 만들기

관리 PC의 `~/.ssh/config`에 다음을 추가하면 이후 주소 대신 `aire-server`를 사용할 수 있습니다.

```sshconfig
Host aire-server
    HostName 192.168.0.55
    User mtvs-1
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

```powershell
ssh aire-server "whoami"
```

## 3. GitHub 코드 변경 후 서버 배포

서버의 추적 파일을 SSH로 직접 수정하지 않습니다. 관리 PC에서 코드를 수정하고 `main`에
push한 다음, 서버의 검증된 배포 스크립트를 호출합니다.

관리 PC의 저장소에서:

```powershell
git switch main
git pull --ff-only origin main

# 파일 수정 후 프로젝트 검증
uv sync --frozen
uv run pytest
uv run ruff check .
uv run mypy

# 실제 수정한 파일만 명시적으로 추가
git add <수정한-파일>
git commit -m "변경 내용 요약"
git push origin main
```

push가 완료된 뒤 원격 배포합니다.

```powershell
ssh aire-server /home/mtvs-1/.local/bin/deploy-aire-server
```

배포 스크립트는 다음 순서로 동작합니다.

1. 서버가 변경 없는 `main`인지 확인
2. 공개 Health와 GitHub 접근 확인
3. 서비스를 정상 종료
4. `data/` 전체를 저장소 바깥에 타임스탬프 백업
5. `git pull --ff-only origin main`
6. `uv sync --frozen`
7. `uv run alembic upgrade head`
8. 서비스 시작 후 공개 Health 확인

`git pull`, 의존성 설치 또는 migration이 실패하면 원인을 확인하기 전에 같은 명령을 반복하지
않습니다. 서버의 `.env`, `data/`, 백업 디렉터리를 삭제하거나 `git reset --hard`를 실행하지
않습니다.

## 4. 서버 재시작과 상태 확인

코드 변경 없이 재시작만 할 때:

```powershell
ssh aire-server "systemctl --user restart aire-server.service"
```

재시작과 상태 확인을 함께 할 때:

```powershell
ssh aire-server "systemctl --user restart aire-server.service && systemctl --user status aire-server.service --no-pager"
```

Health를 확인합니다.

```powershell
curl.exe -fsS https://traip.mtvs2026.work/health
curl.exe -fsS -o NUL -w "%{http_code}`n" https://traip.mtvs2026.work/docs
```

정상 기준은 Health HTTP 200과 `status=ok`, Swagger HTTP 200입니다.

## 5. 로그 확인

최근 100줄:

```powershell
ssh aire-server "journalctl --user -u aire-server.service -n 100 --no-pager"
```

실시간 로그를 보고 종료할 때는 `Ctrl+C`를 누릅니다.

```powershell
ssh -t aire-server "journalctl --user -u aire-server.service -f"
```

반복 재시작, traceback, `no such table`, LLM 연결 오류가 있는지 첫 오류부터 확인합니다. 로그를
공유할 때 `.env`, 인증 header, 사용자 대화 원문은 포함하지 않습니다.

## 6. 운영 `.env` 변경

`.env`는 Git으로 배포하지 않습니다. 설정 변경이 필요한 경우 SSH로 접속해 기존 파일을 먼저
백업하고 편집합니다. API key와 token 값을 화면 공유, 채팅 또는 명령 기록에 남기지 않습니다.

```bash
ssh aire-server
cd /home/mtvs-1/workspace/AIRE_SERVER

backup_stamp="$(date +%Y%m%d-%H%M%S)"
cp -a .env ".env.backup-${backup_stamp}"
nano .env

systemctl --user restart aire-server.service
systemctl --user status aire-server.service --no-pager
curl -fsS https://traip.mtvs2026.work/health
```

설정은 hot reload되지 않으므로 `.env` 변경 후 서비스를 완전히 재시작해야 합니다. Health는
실제 LLM 연결까지 검증하지 않으므로 provider를 바꿨다면 Chat 요청도 한 번 확인합니다.

## 7. 서버에서 허용되는 변경 범위

- 가능: `.env` 변경, 운영 로그 확인, 서비스 재시작, 검증된 배포 스크립트 실행
- 주의: `data/` 복원이나 DB 파일 교체는 서비스를 먼저 종료하고 백업을 확인한 뒤 진행
- 금지: 서버에서 `app/`, `migrations/`, `tests/` 등 Git 추적 파일 직접 수정
- 금지: `.env`, `data/`, SSH 개인 키, Cloudflare token을 Git에 추가
- 금지: Uvicorn worker를 2개 이상 실행하거나 포트 8000을 외부 인터페이스에 직접 공개

소스 긴급 수정이 필요해도 관리 PC에서 커밋과 검증을 마친 뒤 `main`에 push하고 배포 스크립트를
사용합니다. 이렇게 해야 다음 pull에서 충돌하지 않고 배포 이력을 Git으로 추적할 수 있습니다.

## 8. 문제 해결

### SSH 연결 실패

관리 PC가 서버와 같은 LAN/VPN인지, 주소가 바뀌지 않았는지 확인합니다. 서버 콘솔에서 다음을
확인합니다.

```bash
hostname -I
systemctl status ssh --no-pager
ss -ltn | grep ':22 '
```

### 배포가 `tracked working tree changes exist`로 중단

서버의 추적 파일이 직접 수정된 상태입니다. 덮어쓰거나 reset하지 말고 먼저 차이를 확인합니다.

```bash
ssh aire-server
cd /home/mtvs-1/workspace/AIRE_SERVER
git status --short
git diff
```

변경의 소유자와 필요성을 확인한 다음 Git에 반영할지 되돌릴지 결정합니다. `data/transcripts/`의
미추적 운영 파일은 배포 스크립트를 막지 않습니다.

### 서비스가 시작되지 않음

```powershell
ssh aire-server "systemctl --user status aire-server.service --no-pager"
ssh aire-server "journalctl --user -u aire-server.service -n 100 --no-pager"
```

DB migration 상태가 의심되면 서비스를 반복 재시작하지 말고 다음 결과를 확인합니다.

```powershell
ssh aire-server "cd /home/mtvs-1/workspace/AIRE_SERVER && uv run alembic current"
```

운영 데이터 복구가 필요하면 자동으로 판단해 DB를 교체하지 말고 최신 백업 경로와 오류를 먼저
기록합니다.
