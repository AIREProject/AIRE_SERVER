from __future__ import annotations

import re

_PARTICLES = "을|를|은|는|이|가|의|도|만|와|과|랑|이랑|하고"


def alias_pattern(alias: str) -> re.Pattern[str]:
    """아이템·적 별칭을 조사와 어절 경계까지 포함한 정규식으로 만든다."""

    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<!\w){escaped}(?:{_PARTICLES})?(?!\w)", re.IGNORECASE)


def has_batchim(text: str) -> bool:
    last = text[-1]
    if not ("가" <= last <= "힣"):
        return False
    return (ord(last) - ord("가")) % 28 != 0


def topic(text: str) -> str:
    return f"{text}{'은' if has_batchim(text) else '는'}"


def subject(text: str) -> str:
    """주격 조사 이/가를 받침에 맞춰 붙인다.

    `topic` 과 나눠 두는 이유: 같은 문장 안에서 "골리앗은 코어가 약점이고" 처럼 둘 다
    필요하다. 하나로 뭉뚱그리면 "코어이" 같은 문장이 대사로 나간다.
    """

    return f"{text}{'이' if has_batchim(text) else '가'}"
