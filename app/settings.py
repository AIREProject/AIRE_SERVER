from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    # request_complete/ws_message_complete 이벤트만 걸러 stdout 과 별도로 이 파일에도 남긴다.
    # 코딩 에이전트가 서버를 재기동하거나 터미널을 스크롤해도 요청 이력을 파일로 조회할 수
    # 있게 하기 위해서다. 대화 텍스트는 이 로그 스트림에 넣지 않는다.
    access_log_enabled: bool = True
    access_log_path: Path = Path("data/requests.log")
    access_log_max_bytes: int = Field(default=10_485_760, ge=1)
    access_log_backup_count: int = Field(default=5, ge=0)
    max_request_body_bytes: int = Field(default=262_144, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    ai_request_timeout_seconds: float = Field(default=10.0, gt=0)

    # 고정 공개 Bearer AIRE_GAME/AIRE_WEB은 pepper 없이 동작한다. 아래 값은 호환성을 위해
    # 남긴 기존 랜덤 device token과 pairing 경로만 보호한다.
    database_url: str = "sqlite+aiosqlite:///./data/companion.db"
    device_credential_pepper: SecretStr | None = None
    pairing_code_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    # register-game 부트스트랩 전용 고정 토큰. 첫 GameClient 디바이스가 없는 상태에서
    # 인증된 신원이 아직 없으니, 이 값과 일치하는 Bearer 토큰만 register-game 을 허용한다.
    dev_game_device_token: SecretStr | None = None
    # 프로필당 등록 가능한 디바이스 수. 초과하면 등록을 거부한다(자동 해지 없음).
    max_devices_per_profile: int = Field(default=20, ge=1)
    # 관리자 CRUD(app/routes/admin.py) 전용 고정 토큰. 비어 있으면 해당 라우터 전체가
    # AdminAuthenticationUnavailableError(503) 로 실패한다 — dev_game_device_token 과 같은 이유다.
    admin_api_token: SecretStr | None = None

    # 마코 두뇌의 LLM 공급자 설정. mock 은 외부 호출이 없다.
    llm_provider: Literal["mock", "openai", "local"] = "mock"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5-nano"
    openai_timeout_seconds: float = Field(default=8.0, gt=0)
    local_llm_base_url: str = "http://mtvs2026.work/v1"
    local_llm_api_key: str | None = None
    local_llm_model: str = "balanced-q4-k-m-mtp"
    local_llm_timeout_seconds: float = Field(default=30.0, gt=0)
    # 장기기억 임베딩. mock 은 외부 호출 없이 기존 키워드 검색만 사용한다.
    embedding_provider: Literal["mock", "openai", "local"] = "mock"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int | None = Field(default=512, ge=1)
    local_embedding_model: str = ""
    # 임베딩 서버는 대사 LLM 과 **다른 호스트일 수 있다.** 비워 두면 대사 서버를 그대로 쓰고,
    # 그때만 대사용 키를 물려받는다 — 다른 호스트에 남의 키를 보내지 않기 위해서다.
    local_embedding_base_url: str = ""
    local_embedding_api_key: str | None = None
    # 일부 프록시(Cloudflare 등)는 브라우저가 아닌 요청을 막는다. 필요할 때만 채운다.
    local_embedding_user_agent: str = ""
    # 차원 축소를 지원하지 않는 모델(bge-m3 등)은 비워 둔다.
    local_embedding_dimensions: int | None = None
    embedding_timeout_seconds: float = Field(default=3.0, gt=0)
    classify_temperature: float = Field(default=0.0, ge=0)
    # 명령 분류는 command/resource/quantity 세 필드를 내므로 20토큰으로는 잘린다.
    # 잘리면 검증 실패 → mock 폴백으로 조용히 떨어지므로 여유를 둔다.
    classify_max_tokens: int = Field(default=64, ge=1)
    dialogue_temperature: float = Field(default=0.6, ge=0)
    dialogue_max_tokens: int = Field(default=160, ge=1)
    # 기억 추출은 프로필과 에피소드를 한 번에 내므로 대사보다 예산이 커야 한다.
    # 잘리면 검증 실패 → 빈 추출로 조용히 떨어진다. 아래 둘도 같다.
    memory_extract_max_tokens: int = Field(default=512, ge=1)
    # 세션 요약은 한 줄만 낸다.
    memory_summary_max_tokens: int = Field(default=256, ge=1)
    # 통합은 상한만큼의 줄을 다시 쓸 수 있어 가장 크다.
    memory_consolidate_max_tokens: int = Field(default=1024, ge=1)
    companion_prompt_version: Literal["companion-v3"] = "companion-v3"
    companion_command_ttl_seconds: float = Field(default=30.0, gt=0)
    # 되묻기 슬롯의 수명. 정상 흐름에서는 다음 턴에 소비되거나 버려지므로, 이 값은
    # 대화를 그냥 떠난 경우를 정리하는 안전망이다.
    companion_pending_ttl_seconds: float = Field(default=120.0, gt=0)
    # 대화 기억 전체의 유휴 수명. 되묻기 슬롯보다 길다 — 슬롯은 금방 낡지만 대화는 이어진다.
    # 프롬프트에 실을 턴 수·길이 상한은 설정이 아니라 companion/store.py 의 상수다.
    companion_conversation_idle_ttl_seconds: float = Field(default=1800.0, gt=0)
    companion_memory_max_entries: int = Field(default=1000, ge=1)
    # 세션을 넘는 장기기억. 런타임은 SQLite episodic_memories를 사용하고, 이 경로는 0005
    # 마이그레이션이 기존 JSON 기억을 읽을 때만 사용한다. 원본 파일은 자동 삭제하지 않는다.
    # 한 플레이어가 들고 갈 기억 수와 한 줄 길이 상한은 설정이 아니라 memory.py 의 상수다.
    long_term_memory_enabled: bool = True
    long_term_memory_dir: Path = Path("data/memories")
    # 이전 파일 저장소와의 호환을 위해 남긴 설정. 런타임 SQLite에는 적용하지 않는다.
    long_term_memory_max_players: int = Field(default=500, ge=1)
    # 몇 왕복이 밀리면 증분 추출을 돌릴지. 매 턴 뽑으면 같은 대화로 LLM 을 계속 호출한다.
    long_term_extract_every_n_turns: int = Field(default=3, ge=1)
    # 한 턴의 프롬프트에 실을 기억 수. 0 이면 회수하지 않는다(추출은 계속한다).
    long_term_recall_limit: int = Field(default=3, ge=0)
    # 마지막 턴 이후 이만큼 조용하면 남은 구간을 증분 추출한다. "가라앉았다" 이지 "끝났다"
    # 가 아니다 — 이 트리거가 있어야 3의 배수로 끝나지 않은 대화도 기억을 남긴다.
    long_term_quiet_seconds: float = Field(default=90.0, gt=0)
    # 마지막 턴 이후 이만큼 지나면 대화가 끝난 것으로 보고 세션 요약을 한 번 만든다.
    long_term_session_end_seconds: float = Field(default=600.0, gt=0)
    # 증류 루프가 대기열을 확인하는 주기. 판정은 전부 CompanionBrain._drain 이 한다.
    long_term_tick_seconds: float = Field(default=15.0, gt=0)
    # 증류의 원본이 되는 전사(app/brain/transcript.py). **끄면 새 장기기억이 생기지 않는다** —
    # 추출은 전사에 대한 커서 작업이라 읽을 로그가 없다. 이미 있는 기억의 회수는 계속된다.
    transcript_enabled: bool = False
    transcript_dir: Path = Path("data/transcripts")
    # 개발 재현용 전사만 허용한다. 운영 기본은 비활성화이고 24시간보다 길게 둘 수 없다.
    transcript_retention_days: int = Field(default=1, ge=1, le=1)
    # 다음 seq 를 파일에서 다시 읽지 않으려고 들고 있는 대화 수. 캐시일 뿐이다.
    transcript_max_conversations: int = Field(default=1000, ge=1)
    user_message_retention_days: int = Field(default=7, ge=1, le=7)
    companion_message_retention_days: int = Field(default=7, ge=1, le=7)
    game_event_retention_days: int = Field(default=7, ge=1, le=7)
    audit_retention_days: int = Field(default=30, ge=1, le=30)
    retention_sweep_interval_seconds: float = Field(default=3600.0, ge=60.0, le=3600.0)
    legacy_memory_quarantine_dir: Path = Path("data/memory_quarantine")
    legacy_memory_quarantine_days: int = Field(default=7, ge=1, le=7)
    # 임시 발판: 게임 클라이언트가 game_context.location_id 를 채우기 전까지 세계관 질문을
    # 시험하기 위한 대체 위치. 비워 두면(기본값) 동작은 이 설정이 없던 때와 같다.
    # 클라이언트가 보내기 시작하면 지운다 — 제거 절차는 docs/temporary-scaffolds.md §1.
    companion_default_location_id: str | None = None
    # LLM 스텝(스텝1·2 라우터, 되묻기 해소, 대사 생성)별 지연 시간을 구조화 로그로 남긴다.
    llm_step_timing: bool = True

    # .env 에서 "비워 둔다"고 안내하는 선택적 정수 필드(임베딩 차원)는 빈 문자열로 온다.
    # int | None 은 빈 문자열을 None 으로 못 받으므로 여기서 None 으로 정규화한다 —
    # bge-m3 처럼 차원 축소를 지원하지 않는 모델에서 .env.example 그대로 기동되게 한다.
    @field_validator(
        "openai_embedding_dimensions",
        "local_embedding_dimensions",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
