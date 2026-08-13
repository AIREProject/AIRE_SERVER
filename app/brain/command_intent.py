from __future__ import annotations

import re

from .enemies import EnemyRepository
from .intent import CommandLabel, ResourceSlot
from .recipes import RecipeRepository
from .resources import ResourceRepository

# 2단계 LLM 라우터의 결정론적 폴백과 Mock 분류기가 함께 사용하는 패턴이다. 정적 `DATASET`
# 기반 기본 인스턴스로 모듈 임포트 시점에 한 번 계산한다 — DB 로 넘어간 게임데이터(§app/main.py)
# 는 검증된 사실 응답(`fact_for`)에만 반영되고, 여긴 여전히 `dataset.py` 기준이다.
_RECIPE_ACTION = r"(?:만드|만들|제작|레시피|재료|방법)"
_RECIPE_ALIASES = "|".join(
    re.escape(alias).replace(r"\ ", r"\s+")
    for alias in sorted(RecipeRepository().result_aliases(), key=len, reverse=True)
)
RECIPE_PATTERN = re.compile(
    rf"(?:{_RECIPE_ALIASES}).*{_RECIPE_ACTION}|{_RECIPE_ACTION}.*(?:{_RECIPE_ALIASES})",
    re.IGNORECASE,
)
_ENEMY_ACTION = r"(?:약점|잡|공략|상대|처치|어떻게)"
_ENEMY_ALIASES = "|".join(
    re.escape(alias).replace(r"\ ", r"\s+")
    for alias in sorted(EnemyRepository().name_aliases(), key=len, reverse=True)
)
ENEMY_PATTERN = re.compile(
    rf"(?:{_ENEMY_ALIASES}).*{_ENEMY_ACTION}|{_ENEMY_ACTION}.*(?:{_ENEMY_ALIASES})",
    re.IGNORECASE,
)
LORE_PATTERN = re.compile(
    r"(?:마을|지역|여기).*(?:역사|유래|어떤 곳|무슨 곳)|(?:역사|유래|세계관)"
)
CONVERSATION_PATTERN = re.compile(r"(?:안녕|반가워|고마워|감사|잘 지냈|어때)")


class CommandIntentParser:
    """Mock 명령 분류와 채집 슬롯 해소를 결정론적으로 수행한다."""

    # 각 패턴은 normalize()로 문장부호와 중복 공백을 제거한 문자열에 적용한다.
    _FOLLOW = re.compile(r"(?:내 뒤를 |나를 )?따라와?(?: 줘| 주세요| 줄래)?$")
    _WAIT = re.compile(r"(?:여기서 |잠깐 )?(?:기다려|대기해)(?: 줘| 주세요| 줄래)?$")
    _STOP = re.compile(
        r"(?:이제 |작업 |하던 거 )?(?:멈춰|그만|중지해)(?: 줘| 주세요)?"
        r"|됐어|취소|나중에 하자"
    )
    _RETURN = re.compile(
        r"(?:내 (?:옆|곁)으로 )?(?:돌아와|이리 와)(?: 줘| 주세요| 줄래)?$"
    )
    _GATHER_VERB = re.compile(
        r"(?:모아|캐|채집해|가져와)(?:\s*(?:줘|주세요|줄래))?$"
    )
    # "잡"/"처치"/"상대"/"어떻게"는 _ENEMY_ACTION 이 이미 쓰는 단어라, 여기 섞으면
    # "참호병 어떻게 잡아?" 같은 질문이 공격 명령으로 오분류된다.
    _ATTACK_VERB = re.compile(
        r"(?:공격해|공격하자|공격|싸워|쳐부숴|무찔러|물리쳐)(?:\s*(?:줘|주세요|줄래))?$"
    )
    # 수량은 더 이상 미지원 신호가 아니라 추출 대상이다. 앞뒤 경계를 확인해
    # "1.5개"의 5, "-1개"의 1처럼 온전하지 않은 숫자를 주워 담지 않는다.
    _QUANTITY = re.compile(r"(?<![\d.,\-])(\d+)\s*개")
    # Game GatherResource의 첫 수직 슬라이스에서는 수량을 해석할 수 있어도 후보를
    # 만들지 않는다. 이 패턴은 정수 추출에 실패하는 표현(소수·음수·한글 수사·막연한
    # 양)까지 포함해, `resolve_quantity()`의 None과 "수량을 말하지 않음"을 구분한다.
    _GATHER_QUANTITY = re.compile(
        r"(?:[-+]?\d[\d.,]*\s*개|"
        r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|몇)\s*개|"
        r"많이|잔뜩|조금|전부|모두|다\s*(?:캐|모아|모으|채집|가져)|"
        r"가방\s*찰\s*때까지|가능한\s*만큼|최대한|수량)"
    )
    _GATHER_QUESTION_WORDS = re.compile(
        r"(?:어떻게|방법|(?:캐|모으|채집|가져)(?:는|하는)?\s*법|"
        r"가능|할\s*수|뭘|무엇|뭐|왜|어디)"
    )
    _GATHER_QUESTION_ENDING = re.compile(
        r"(?:할까|될까|가능할까|할까요|될까요|인가요|나요|니)$"
    )
    _GATHER_REQUEST_SUFFIX = re.compile(r"(?:줘|주세요|줄래)$")
    _GATHER_REFERENCE = re.compile(r"(?:캐|채집|모으|모아|가져|자원)")
    _AMBIGUOUS_REFERENCE = re.compile(r"(?:저것|이것|그것|무언가|뭔가|자원)")
    _BARE_GATHER = re.compile(
        r"(?:좀 )?(?:모아|캐|채집해|가져와)(?:\s*(?:줘|주세요|줄래))?"
    )

    _resources = ResourceRepository()

    @classmethod
    def classify_simple_command(cls, text: str) -> CommandLabel | None:
        """자원 인자가 필요 없는 명령을 판별한다. Mock 공급자와 폴백 전용이다."""

        normalized = cls.normalize(text)
        if cls._FOLLOW.fullmatch(normalized):
            return CommandLabel.FOLLOW_PLAYER
        if cls._WAIT.fullmatch(normalized):
            return CommandLabel.WAIT
        if cls._STOP.fullmatch(normalized):
            return CommandLabel.STOP_CURRENT_TASK
        if cls._RETURN.fullmatch(normalized):
            return CommandLabel.RETURN_TO_PLAYER
        return None

    @classmethod
    def resolve_gather(cls, text: str) -> tuple[ResourceSlot, int | None]:
        """채집 발화에서 자원 슬롯과 수량을 뽑는다. Mock 공급자와 폴백 전용이다."""

        normalized = cls.normalize(text)
        quantity = cls.resolve_quantity(text)

        resources = cls._resources.find_all(normalized)
        if len(resources) > 1:
            # 여러 자원을 함께 말했다. 하나를 임의로 고르지 않고 되묻는다.
            return ResourceSlot.UNSPECIFIED, quantity
        if resources:
            return ResourceSlot(resources[0].value), quantity
        if cls._AMBIGUOUS_REFERENCE.search(normalized) or cls._BARE_GATHER.fullmatch(
            normalized
        ):
            return ResourceSlot.UNSPECIFIED, quantity
        # 채집 동사는 있는데 아는 자원이 없으면 지원하지 않는 자원을 말한 것이다.
        return ResourceSlot.OTHER, quantity

    @classmethod
    def resolve_quantity(cls, text: str) -> int | None:
        """20개처럼 온전한 정수 수량만 반환하고, 확신할 수 없으면 None으로 둔다.

        `normalize()` 는 문장부호를 공백으로 바꾸므로 "1.5개"가 "1 5개"가 된다.
        정규화 전 원문에서 읽어야 잘린 숫자를 수량으로 오인하지 않는다.
        """

        quantities = {int(value) for value in cls._QUANTITY.findall(text)}
        # 수량이 여럿이면 어느 쪽인지 확신할 수 없으므로 지정되지 않은 것으로 둔다.
        return quantities.pop() if len(quantities) == 1 else None

    @classmethod
    def has_gather_quantity(cls, text: str) -> bool:
        """채집 발화에 수량 또는 양을 나타내는 표현이 있는지 판정한다.

        ``resolve_quantity()``는 실행 가능한 정수만 반환하므로, Game 표면의 strict
        후보 경계에서는 ``1.5개``나 ``많이`` 같은 malformed/모호한 양도 별도로
        거부해야 한다. 호출자는 이미 채집 경로에 들어온 텍스트만 전달한다.
        """

        return cls._GATHER_QUANTITY.search(text.casefold()) is not None

    @classmethod
    def is_gather_question(cls, text: str) -> bool:
        """채집 방법·가능 여부를 묻는 질문인지 보수적으로 판정한다.

        일반적인 ``방법`` 질문(예: 철검 만드는 방법)은 채집 참조가 없으면 제외한다.
        원문 물음표와 한국어 의문형 어미를 함께 보아 정규화 과정에서 사라지는
        문장부호도 놓치지 않는다.
        """

        normalized = cls.normalize(text)
        if not normalized or cls._GATHER_REFERENCE.search(normalized) is None:
            return False
        # 한국어의 공손한 요청형은 물음표를 붙여도 명령이다("캐줄래?"). 반면
        # 동사만 남은 "캐?"는 방법/가능 질문으로 취급해 Game 후보를 막는다.
        punctuation_question = "?" in text or "\uFF1F" in text
        if punctuation_question and cls._GATHER_REQUEST_SUFFIX.search(normalized):
            punctuation_question = False
        return bool(
            punctuation_question
            or cls._GATHER_QUESTION_WORDS.search(normalized)
            or cls._GATHER_QUESTION_ENDING.search(normalized)
        )

    @classmethod
    def is_gather_command(cls, text: str) -> bool:
        """Mock 공급자에서만 채집 동사가 있는지 확인한다."""

        return cls._GATHER_VERB.search(cls.normalize(text)) is not None

    @classmethod
    def is_attack_command(cls, text: str) -> bool:
        """Mock 공급자에서만 공격 동사가 있는지 확인한다. 적 이름이 앞에 붙어도 매칭된다."""

        return cls._ATTACK_VERB.search(cls.normalize(text)) is not None

    @staticmethod
    def normalize(text: str) -> str:
        """대소문자, 문장부호, 공백 차이를 없애 정규식 비교를 안정화한다."""

        normalized = text.casefold().strip()
        normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
        return " ".join(normalized.split())
