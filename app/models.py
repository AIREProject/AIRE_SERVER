"""서버가 게임 클라이언트와 주고받는 계약.

`POST /api/v1/chat` 과 채팅 WebSocket 이 공유한다. 유일한 권위이며, 여기 없는 필드는
계약에 없다(`extra="forbid"`).
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# game_context 는 위치 조회에만 쓰이지만, 나중에 프롬프트로 흘러갈 수 있는 확장 필드라
# 비밀값으로 보이는 키는 애초에 막는다. 신원 인증과는 별개의 LLM 입력 안전장치다.
FORBIDDEN_AI_CONTEXT_KEYS = frozenset(
    {
        "authorization",
        "connectionstring",
        "databaseconnectionstring",
        "databaseurl",
        "dbpassword",
        "password",
        "secret",
        "token",
    }
)


def validate_ai_context_values(value: JsonValue) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = "".join(
                character for character in key.casefold() if character.isalnum()
            )
            if normalized_key in FORBIDDEN_AI_CONTEXT_KEYS or normalized_key.endswith(
                ("password", "secret", "token")
            ):
                raise ValueError("Game context contains a forbidden sensitive key.")
            validate_ai_context_values(nested_value)
    elif isinstance(value, list):
        for nested_value in value:
            validate_ai_context_values(nested_value)


class Surface(StrEnum):
    """마코가 지금 말하고 있는 창구.

    **말투만 가른다.** 무엇을 할 수 있는지는 계속 `allowed_commands` 하나가 정한다 —
    "모바일이니까 명령이 없다" 가 아니라 "모바일 클라이언트가 아직 빈 목록을 보낸다" 이고,
    둘을 붙여 놓으면 모바일에 작업 지시가 생기는 날 그 조건문을 전부 되돌려야 한다.
    창구별로 달라지는 것은 `brain/dialogue.py` 의 `SURFACE_PROFILES` 에만 있다.
    """

    GAME = "game"
    MOBILE = "mobile"


class TimeSource(StrEnum):
    """시간 맥락의 기준."""

    GAME_WORLD = "GameWorld"
    REAL_WORLD = "RealWorld"


class TimeContext(StrictModel):
    """클라이언트가 알려 주는 한 턴의 시간 맥락."""

    source: TimeSource
    day: int = Field(ge=0, le=100_000)
    hour: int = Field(ge=0, le=23)
    period: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z]+$")


class CommandType(StrEnum):
    """게임의 명령 프로토콜.

    마코가 실제로 내는 것은 여섯 가지(`Follow` / `HoldPosition` / `CancelCurrent` /
    `GatherResource` / `Attack` / `Switch`)뿐이고, 나머지는 게임이 `allowed_commands` 로
    보낼 수 있는 값이다. 무엇을 낼 수 있는지는 `brain/graph.py` 의 라우팅 표가 정한다.
    """

    FOLLOW = "Command.Follow"
    HOLD_POSITION = "Command.HoldPosition"
    RETURN_TO_PLAYER = "Command.ReturnToPlayer"
    ENGAGE_TARGET = "Command.EngageTarget"
    DISTRACT_TARGET = "Command.DistractTarget"
    MOVE_TO_LOCATION = "Command.MoveToLocation"
    CANCEL_CURRENT = "Command.CancelCurrent"
    GATHER_RESOURCE = "Command.GatherResource"
    ATTACK = "Command.Attack"
    SWITCH = "Command.Switch"


StableId = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]


class CommandCandidate(StrictModel):
    command_id: str = Field(min_length=1, max_length=128)
    request_id: str = Field(min_length=1, max_length=128)
    type: CommandType
    target_id: str | None = Field(default=None, max_length=128)
    priority: Literal["Low", "Normal", "High", "Critical"] = "Normal"
    issued_at: datetime
    expires_at: datetime
    parameters: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_expiration(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("Command expiration must be later than issue time.")
        if len(self.parameters) > 16:
            raise ValueError("Command parameters must contain at most 16 properties.")
        return self


class AIMetadata(StrictModel):
    provider: str = Field(min_length=1, max_length=64)
    model_version: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(min_length=1, max_length=128)


class ChatRequest(StrictModel):
    request_id: StableId
    # 클라이언트가 계약 버전을 함께 보낼 수 있다. 현재는 1만 읽고 검증한다.
    schema_version: Literal[1] | None = None
    # 같은 프로필+세이브슬롯이라도 세션이 다르면 다른 대화다. 대화 기억이 섞이지 않게 하는
    # 축 중 하나 — 신원 자체는 더 이상 이 모델이 아니라 인증된 `AuthenticatedDevice` 가 준다
    # (`app/dependencies.py`, `docs/temporary-scaffolds.md` §2).
    session_id: StableId
    # 한 프로필 안에서 진행을 가르는 축. 인증된 profile_id 와 함께 장기기억 스코프가 된다.
    save_slot_id: StableId
    # 지금은 "mako" 하나만 유효하다(`app/brain/companions.py`). 배관만 갖춘 확장점이다.
    companion_id: StableId
    # 아래 둘은 클라이언트가 실어 보내는 신원 주장이다. 생략 가능하고, 있으면 인증된 신원과
    # 대조한다(`AuthenticatedDevice.validate_claims`) — 다르면 400 이 아니라 신원 위조
    # 시도이므로 403(`IdentityScopeMismatchError`) 이다.
    profile_id: StableId | None = None
    device_id: StableId | None = None
    # 클라이언트 채팅 버블의 식별자. 응답에서 그대로 되돌려 준다.
    message_id: StableId | None = None
    user_message: str = Field(min_length=1, max_length=2000)
    # 이 말이 어느 창구에서 왔는지. 마코의 말투만 바꾼다. 생략하면 게임이다 — 기존 게임
    # 클라이언트는 이 필드를 몰라도 되고, 모르는 창구 이름은 `extra="forbid"` 와 함께 422 다.
    surface: Surface = Surface.GAME
    time_context: TimeContext | None = None
    recent_event_ids: list[StableId] = Field(default_factory=list, max_length=32)
    game_context: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_commands: list[CommandType] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_context_and_commands(self) -> Self:
        if len(self.game_context) > 32:
            raise ValueError("Game context must contain at most 32 properties.")
        validate_ai_context_values(self.game_context)
        if len(self.allowed_commands) != len(set(self.allowed_commands)):
            raise ValueError("Allowed commands must be unique.")
        if len(self.recent_event_ids) != len(set(self.recent_event_ids)):
            raise ValueError("Recent event ids must be unique.")
        return self


class ChatResponse(StrictModel):
    request_id: StableId
    message_id: StableId | None = None
    session_id: StableId
    save_slot_id: StableId
    companion_id: StableId
    response_id: StableId
    display_text: str = Field(min_length=1, max_length=4000)
    command_candidates: list[CommandCandidate] = Field(default_factory=list, max_length=4)
    # 모바일 채팅이 채집 요청을 Offline_Task 로 등록했을 때만 채워진다. 전체 상태가 필요하면
    # 기존 GET /api/v1/tasks 로 조회한다 — offline_task_models.py 가 이미 이 파일을 임포트하므로
    # 반대 방향으로 그 모델을 여기서 쓰면 순환 임포트가 된다.
    offline_task_id: StableId | None = None
    ai_metadata: AIMetadata


SituationLine = Annotated[str, Field(min_length=1, max_length=200)]


class SituationRequest(StrictModel):
    """게임 클라이언트가 코드로 트리거하는 상황 이벤트 — 마코가 먼저 말을 거는 계기.

    `user_message` 가 없다. 무슨 상황인지는 클라이언트가 이미 판단해 보낸다 — 서버는 다시
    분류하지 않고 대사만 만든다. 그래서 명령 판단에 쓰이는 `game_context`/`allowed_commands`
    도 없다: 명령은 여전히 `POST /chat` 하나로만 난다.
    """

    request_id: StableId
    schema_version: Literal[1] | None = None
    # chat 과 같은 값을 주면 같은 대화 기억(`[최근 대화]`)에 얹힌다.
    session_id: StableId
    save_slot_id: StableId
    companion_id: StableId
    profile_id: StableId | None = None
    device_id: StableId | None = None
    # 클라이언트가 관찰한 상황을 자유 문장으로 나열한다. 검증된 사실이 아니라 그대로
    # 프롬프트에 실린다 — `docs/temporary-scaffolds.md` §3 의 `recent_event_ids` 와 달리
    # 이건 임시 발판이 아니라 확정된 신뢰 모델이다.
    situation: list[SituationLine] = Field(min_length=1, max_length=4)
    surface: Surface = Surface.GAME
    time_context: TimeContext | None = None

    @model_validator(mode="after")
    def validate_situation(self) -> Self:
        if any(not line.strip() for line in self.situation):
            raise ValueError("Situation lines must not be blank.")
        return self


class SituationResponse(StrictModel):
    request_id: StableId
    session_id: StableId
    save_slot_id: StableId
    companion_id: StableId
    response_id: StableId
    display_text: str = Field(min_length=1, max_length=4000)
    ai_metadata: AIMetadata
