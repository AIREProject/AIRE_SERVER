"""어떤 컴패니언이 답하는지에 대한 레지스트리 — 배관만 있고 콘텐츠는 없다.

`SURFACE_PROFILES`(`dialogue.py`)와 같은 모양의 확장점이다. 지금은 마코 하나만 등록돼
있고, `llm.py` 의 프롬프트는 여전히 마코 하나만 안다 — 두 번째 페르소나를 실제로
말하게 하려면 이 딕셔너리에 항목을 늘리는 것과는 별개로 `llm.py` 의 프롬프트 자체를
컴패니언별로 나누는 작업이 필요하다.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompanionProfile:
    display_name: str


COMPANION_PROFILES: dict[str, CompanionProfile] = {
    "mako": CompanionProfile(display_name="마코"),
}
