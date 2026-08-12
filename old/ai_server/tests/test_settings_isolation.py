"""테스트 도구 자체의 검증 — `make_settings` 가 오타를 삼키지 않는지 본다.

다른 테스트들이 "넘긴 값이 실제로 적용됐다"를 전제로 단언하므로, 그 전제가 깨지면
단언이 조용히 다른 것을 검사하게 된다. 여기서 전제를 직접 확인한다.
"""

from pathlib import Path

import pytest

from tests.conftest import make_settings


def test_make_settings_rejects_an_unknown_field_name() -> None:
    # `Settings` 는 extra="ignore" 라 이 오타를 그냥 무시하고 기본값으로 돌아간다.
    with pytest.raises(TypeError, match="max_request_body"):
        make_settings(max_request_body=8)


def test_make_settings_applies_a_valid_override() -> None:
    assert make_settings(max_request_body_bytes=8).max_request_body_bytes == 8


def test_the_long_term_memory_directory_never_points_at_the_real_one() -> None:
    """서버를 한 번 띄운 개발자의 기억 파일을 테스트가 읽고 쓰면 안 된다."""

    assert make_settings().long_term_memory_dir != Path("data/memories")


def test_the_transcript_directory_never_points_at_the_real_one() -> None:
    """같은 이유다. 이쪽은 증류된 줄이 아니라 **대화 원문**이라 더하다."""

    assert make_settings().transcript_dir != Path("data/transcripts")
