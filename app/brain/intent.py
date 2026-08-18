from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class TopIntent(StrEnum):
    """사용자 발화를 최상위 처리 경로로 분류한 내부 의도."""

    COMMAND = "command"
    RECIPE = "recipe"
    ENEMY = "enemy"
    LORE = "lore"
    CONVERSATION = "conversation"
    UNKNOWN = "unknown"


class RecipeQueryMode(StrEnum):
    """검증된 Recipe 질의를 처리하는 안정적인 내부 mode."""

    LIST_KNOWN = "list_known"
    DETAIL = "detail"
    COMPARE = "compare"
    AMBIGUOUS = "ambiguous"
    UNKNOWN_RECIPE = "unknown_recipe"


class RequestQueryMode(StrEnum):
    """Recipe 이외의 P0 평가에서 관측하는 최소 요청 목적."""

    CONVERSATION = "conversation"
    PREFERENCE_SHARE = "preference_share"
    INFORMATION_QUESTION = "information_question"
    UNSUPPORTED_FACT = "unsupported_fact"


class TopClassification(BaseModel):
    """LLM 최상위 라우터가 반환해야 하는 구조화된 출력."""

    model_config = ConfigDict(extra="forbid")

    intent: TopIntent


class CommandLabel(StrEnum):
    """명령형 발화를 실행 가능한 명령 계열로 분류한 내부 라벨."""

    FOLLOW_PLAYER = "follow_player"
    WAIT = "wait"
    STOP_CURRENT_TASK = "stop_current_task"
    GATHER_RESOURCE = "gather_resource"
    CRAFT_ITEM = "craft_item"
    ATTACK = "attack"
    RETURN_TO_PLAYER = "return_to_player"
    UNKNOWN = "unknown"


class ResourceSlot(StrEnum):
    """채집 명령이 가리키는 자원. enum이라 LLM이 목록 밖 값을 낼 수 없다."""

    WOOD = "wood"
    STONE = "stone"
    OTHER = "other"  # 자원은 특정됐으나 서버가 지원하지 않는다
    UNSPECIFIED = "unspecified"  # "저것 좀 캐 줘"처럼 자원이 특정되지 않았다


class CommandClassification(BaseModel):
    """LLM 명령 라우터가 반환해야 하는 구조화된 출력."""

    model_config = ConfigDict(extra="forbid")

    # 어느 필드에도 기본값을 주지 않는다. 기본값이 있으면 Pydantic이 required에서
    # 빼는데, OpenAI strict 모드는 모든 프로퍼티가 required여야 해 스키마가 거부된다.
    command: CommandLabel
    resource: ResourceSlot
    quantity: int | None


class PendingResolution(BaseModel):
    """되물은 슬롯에 대한 답인지, 새 주제인지 판정한 구조화된 출력."""

    model_config = ConfigDict(extra="forbid")

    # 위와 같은 이유로 기본값을 주지 않는다.
    is_answer: bool
    resource: ResourceSlot
