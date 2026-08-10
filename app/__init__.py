"""마코 동료 서버.

**이 파일은 비워 둔다.** `from .main import app` 을 넣으면 `uvicorn app:app` 이 되지만,
`import app.brain.graph` 하나에도 FastAPI·Starlette 전체가 딸려 온다(실측: 넣기 전 False,
넣은 뒤 True). 두뇌 모듈은 웹 프레임워크 없이 import 되는 편이 낫고, 실행 대상은
`app.main:app` 하나로 충분하다.

순환 import 때문은 아니다 — `app/models.py` 와 `app/settings.py` 가 leaf 라 지금 구조에서
순환은 성립하지 않는다.
"""
