"""Canonical Conversation/Message persistence and Chat operation ledger."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.brain.store import ConversationTurn
from app.db.models import (
    ChatOperationModel,
    CommandCandidateModel,
    ConversationModel,
    MessageModel,
    SourceOutboxModel,
)
from app.db.save_slot_repository import SaveSlotRepository
from app.errors import DuplicateRequestError
from app.models import ChatResponse

if TYPE_CHECKING:
    from app.identity import AuthenticatedDevice
    from app.models import ChatRequest, CommandCandidate


@dataclass(frozen=True, slots=True)
class ChatStart:
    operation: ChatOperationModel
    conversation: ConversationModel
    input_message: MessageModel
    created: bool


class CanonicalChatRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        user_retention_days: int,
        companion_retention_days: int,
        audit_retention_days: int,
    ) -> None:
        self._session = session
        self._user_retention = timedelta(days=user_retention_days)
        self._companion_retention = timedelta(days=companion_retention_days)
        self._audit_retention = timedelta(days=audit_retention_days)

    async def begin(
        self,
        request: ChatRequest,
        identity: AuthenticatedDevice,
        *,
        request_digest: str,
    ) -> ChatStart:
        slot = await SaveSlotRepository(self._session).get_or_create(
            profile_id=identity.profile_id,
            save_slot_id=request.save_slot_id,
        )
        operation = await self._find_operation(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=request.companion_id,
            request_id=request.request_id,
        )
        if operation is not None:
            conversation = await self._conversation_for_operation(operation)
            input_message = await self._message(operation.input_message_id)
            return ChatStart(operation, conversation, input_message, False)

        if (
            request.message_id is not None
            and await self._find_message(request.message_id) is not None
        ):
            raise DuplicateRequestError

        now = datetime.now(UTC)
        conversation = await self._get_or_create_conversation(
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=request.companion_id,
            session_id=request.session_id,
            surface=request.surface.value,
            now=now,
        )
        sequence = await self._next_sequence(conversation.row_id)
        input_message_id = request.message_id or f"message-{uuid4()}"
        input_message = MessageModel(
            row_id=str(uuid4()),
            message_id=input_message_id,
            conversation_row_id=conversation.row_id,
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=request.companion_id,
            request_id=request.request_id,
            sequence=sequence,
            speaker="player",
            source_mode="GameWorld" if request.surface.value == "game" else "RealWorld",
            content=request.user_message,
            content_digest=_content_digest(request.user_message),
            time_context=(
                request.time_context.model_dump(mode="json")
                if request.time_context is not None
                else None
            ),
            storage_class="Transient",
            retention_reason="ChatUserDefault",
            expires_at=now + self._user_retention,
            audit_expires_at=now + self._audit_retention,
            content_deleted_at=None,
            created_at=now,
            delivered_at=now,
        )
        operation = ChatOperationModel(
            row_id=str(uuid4()),
            profile_id=identity.profile_id,
            save_slot_row_id=slot.row_id,
            companion_id=request.companion_id,
            request_id=request.request_id,
            request_digest=request_digest,
            state="Pending",
            input_message_id=input_message_id,
            response_message_id=None,
            response_metadata=None,
            created_at=now,
            updated_at=now,
            completed_at=None,
            audit_expires_at=now + self._audit_retention,
        )
        self._session.add_all((input_message, operation))
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._find_operation(
                profile_id=identity.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id=request.companion_id,
                request_id=request.request_id,
            )
            if existing is None:
                raise
            existing_conversation = await self._conversation_for_operation(existing)
            existing_message = await self._message(existing.input_message_id)
            return ChatStart(
                existing,
                existing_conversation,
                existing_message,
                False,
            )
        return ChatStart(operation, conversation, input_message, True)

    async def history_before(self, message: MessageModel) -> tuple[ConversationTurn, ...]:
        result = await self._session.execute(
            select(MessageModel)
            .where(
                MessageModel.conversation_row_id == message.conversation_row_id,
                MessageModel.sequence < message.sequence,
                MessageModel.content.is_not(None),
                MessageModel.delivered_at.is_not(None),
            )
            .order_by(MessageModel.sequence.desc())
            .limit(12)
        )
        rows = list(reversed(result.scalars().all()))
        turns: list[ConversationTurn] = []
        for row in rows:
            if row.speaker == "player":
                turns.append(ConversationTurn(speaker="player", text=row.content or ""))
            elif row.speaker == "companion":
                turns.append(ConversationTurn(speaker="companion", text=row.content or ""))
        return tuple(turns)

    async def save_generated(
        self,
        start: ChatStart,
        response: ChatResponse,
        *,
        offline_task_plan: dict[str, object] | None = None,
    ) -> MessageModel:
        existing = await self.response_message(start.operation)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        message = MessageModel(
            row_id=str(uuid4()),
            message_id=response.response_id,
            conversation_row_id=start.conversation.row_id,
            profile_id=start.operation.profile_id,
            save_slot_row_id=start.operation.save_slot_row_id,
            companion_id=start.operation.companion_id,
            request_id=start.operation.request_id,
            sequence=start.input_message.sequence + 1,
            speaker="companion",
            source_mode=start.input_message.source_mode,
            content=response.display_text,
            content_digest=_content_digest(response.display_text),
            time_context=start.input_message.time_context,
            storage_class="Transient",
            retention_reason="ChatCompanionDefault",
            expires_at=now + self._companion_retention,
            audit_expires_at=now + self._audit_retention,
            content_deleted_at=None,
            created_at=now,
            delivered_at=None,
        )
        start.operation.response_message_id = response.response_id
        start.operation.response_metadata = _response_metadata(
            response,
            offline_task_plan=offline_task_plan,
        )
        start.operation.state = "Generated"
        start.operation.updated_at = now
        self._session.add(message)
        await self._session.commit()
        return message

    async def complete(
        self,
        start: ChatStart,
        response: ChatResponse,
        candidates: list[CommandCandidate],
    ) -> None:
        now = datetime.now(UTC)
        message = await self.response_message(start.operation)
        if message is None:
            raise RuntimeError("Generated response message is missing.")
        start.operation.response_metadata = _response_metadata(response)
        start.operation.state = "Completed"
        start.operation.updated_at = now
        start.operation.completed_at = now
        message.delivered_at = now
        for candidate in candidates:
            self._session.add(
                CommandCandidateModel(
                    row_id=str(uuid4()),
                    command_id=candidate.command_id,
                    profile_id=start.operation.profile_id,
                    save_slot_row_id=start.operation.save_slot_row_id,
                    companion_id=start.operation.companion_id,
                    session_id=response.session_id,
                    request_id=response.request_id,
                    command_type=candidate.type.value,
                    target_id=candidate.target_id,
                    priority=candidate.priority,
                    issued_at=candidate.issued_at,
                    expires_at=candidate.expires_at,
                    parameters=dict(candidate.parameters),
                    created_at=now,
                    audit_expires_at=now + self._audit_retention,
                )
            )
        for source_id in (start.input_message.row_id, message.row_id):
            self._session.add(
                SourceOutboxModel(
                    source_type="Message",
                    source_id=source_id,
                    state="Pending",
                    lease_token=None,
                    lease_expires_at=None,
                    attempt_count=0,
                    created_at=now,
                    completed_at=None,
                )
            )
        await self._session.commit()

    async def response_message(self, operation: ChatOperationModel) -> MessageModel | None:
        if operation.response_message_id is None:
            return None
        return await self._message(operation.response_message_id)

    async def build_response(self, operation: ChatOperationModel) -> ChatResponse | None:
        if operation.response_metadata is None:
            return None
        message = await self.response_message(operation)
        if message is None or message.content is None:
            return None
        metadata = dict(operation.response_metadata)
        metadata.pop("_offline_task_plan", None)
        metadata["display_text"] = message.content
        return ChatResponse.model_validate(metadata)

    def offline_task_plan(
        self, operation: ChatOperationModel
    ) -> dict[str, object] | None:
        if operation.response_metadata is None:
            return None
        plan = operation.response_metadata.get("_offline_task_plan")
        return dict(plan) if isinstance(plan, dict) else None

    async def rollback(self) -> None:
        await self._session.rollback()

    async def _find_operation(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        companion_id: str,
        request_id: str,
    ) -> ChatOperationModel | None:
        result = await self._session.execute(
            select(ChatOperationModel).where(
                ChatOperationModel.profile_id == profile_id,
                ChatOperationModel.save_slot_row_id == save_slot_row_id,
                ChatOperationModel.companion_id == companion_id,
                ChatOperationModel.request_id == request_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get_or_create_conversation(
        self,
        *,
        profile_id: str,
        save_slot_row_id: str,
        companion_id: str,
        session_id: str,
        surface: str,
        now: datetime,
    ) -> ConversationModel:
        result = await self._session.execute(
            select(ConversationModel).where(
                ConversationModel.profile_id == profile_id,
                ConversationModel.save_slot_row_id == save_slot_row_id,
                ConversationModel.companion_id == companion_id,
                ConversationModel.session_id == session_id,
                ConversationModel.surface == surface,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing
        conversation = ConversationModel(
            row_id=str(uuid4()),
            conversation_id=f"conversation-{uuid4()}",
            profile_id=profile_id,
            save_slot_row_id=save_slot_row_id,
            companion_id=companion_id,
            session_id=session_id,
            surface=surface,
            created_at=now,
        )
        self._session.add(conversation)
        await self._session.flush()
        return conversation

    async def _conversation_for_operation(
        self, operation: ChatOperationModel
    ) -> ConversationModel:
        input_message = await self._message(operation.input_message_id)
        conversation = await self._session.get(
            ConversationModel, input_message.conversation_row_id
        )
        if conversation is None:
            raise RuntimeError("Canonical conversation is missing.")
        return conversation

    async def _message(self, message_id: str) -> MessageModel:
        message = await self._find_message(message_id)
        if message is None:
            raise RuntimeError("Canonical message is missing.")
        return message

    async def _find_message(self, message_id: str) -> MessageModel | None:
        result = await self._session.execute(
            select(MessageModel).where(MessageModel.message_id == message_id)
        )
        return result.scalar_one_or_none()

    async def _next_sequence(self, conversation_row_id: str) -> int:
        result = await self._session.execute(
            select(func.max(MessageModel.sequence)).where(
                MessageModel.conversation_row_id == conversation_row_id
            )
        )
        return int(result.scalar_one_or_none() or 0) + 1


def _content_digest(content: str) -> str:
    return sha256(content.encode("utf-8")).hexdigest()


def _response_metadata(
    response: ChatResponse,
    *,
    offline_task_plan: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = response.model_dump(mode="json")
    payload.pop("display_text", None)
    if offline_task_plan is not None:
        payload["_offline_task_plan"] = offline_task_plan
    return payload
