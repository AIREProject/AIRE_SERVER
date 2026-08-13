# AIRE_SERVER 원격 배포 구성 안내

이 문서의 기존 generic 예시는 실제 서버 구성과 달랐습니다. 현재 서버는 root systemd와
8010번 포트가 아니라 다음 구성을 사용합니다.

- Linux 사용자: `mtvs-1`
- 저장소: `/home/mtvs-1/workspace/AIRE_SERVER`
- 서비스: user systemd `aire-server.service`
- 내부 API: `127.0.0.1:8000`
- 배포 명령: `/home/mtvs-1/.local/bin/deploy-aire-server`
- 원격 SSH: 같은 LAN의 `192.168.0.55`

최초 구성, SSH key, LAN 접속, 한 줄 배포와 장애 대응의 단일 기준은
[`REMOTE_SERVER_OPERATIONS.md`](REMOTE_SERVER_OPERATIONS.md)입니다. 이 파일의 과거 root
systemd/8010 예시를 사용하지 않습니다.
