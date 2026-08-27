"""Kakao adapter와 Backend 사이의 private integration contract."""

from typing import Literal, Self

from pydantic import Field, model_validator

from app.model_types import StrictModel
from app.models import ChatRequest, Surface


class KakaoIntegrationUser(StrictModel):
    id: str = Field(min_length=1, max_length=70)
    type: Literal["botUserKey"]


class KakaoIntegrationChatRequest(StrictModel):
    bot_id: str = Field(min_length=1, max_length=70)
    user: KakaoIntegrationUser
    chat: ChatRequest

    @model_validator(mode="after")
    def validate_fixed_scope(self) -> Self:
        if self.chat.surface is not Surface.MOBILE:
            raise ValueError("Kakao integration requires mobile chat.")
        if self.chat.save_slot_id != "demo-slot-1":
            raise ValueError("Kakao integration requires demo-slot-1.")
        if self.chat.companion_id != "mako":
            raise ValueError("Kakao integration requires the mako companion.")
        if self.chat.session_id != "kakao":
            raise ValueError("Kakao integration requires the kakao session.")
        if self.chat.profile_id is not None or self.chat.device_id is not None:
            raise ValueError("Kakao integration does not accept client identity claims.")
        if self.chat.allowed_commands:
            raise ValueError("Kakao integration does not accept command capabilities.")
        if self.chat.recent_event_ids:
            raise ValueError("Kakao integration does not accept game event references.")
        return self
