"""Focused persistence rules for the Event and Command Result service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.credentials import CredentialProtector
from app.db.event_repository import SqlAlchemyEventRepository
from app.db.models import CommandCandidateModel
from app.errors import (
    CommandCandidateNotFoundError,
    CommandResultTransitionError,
    DuplicateRequestError,
)
from app.event_models import CreateCommandResultRequest, CreateGameEventRequest
from app.event_service import EventService
from tests.conftest import make_authenticated_device, make_database, make_settings

PROTECTOR = CredentialProtector(SecretStr("test-only-pepper-not-for-production"))


def _game_time() -> dict[str, object]:
    return {"source": "GameWorld", "day": 2, "hour": 3, "period": "Night"}


def _event(event_id: str = "event-1") -> CreateGameEventRequest:
    return CreateGameEventRequest.model_validate(
        {
            "schema_version": 1,
            "event_id": event_id,
            "session_id": "session-1",
            "save_slot_id": "slot-1",
            "companion_id": "mako",
            "type": "Event.Danger.Detected",
            "occurred_at": "2026-08-16T01:00:00Z",
            "time_context": _game_time(),
            "actor_id": "actor-1",
            "target_ids": ["target-1"],
            "payload": {},
        }
    )


def _result(operation_id: str, status: str) -> CreateCommandResultRequest:
    return CreateCommandResultRequest.model_validate(
        {
            "schema_version": 1,
            "operation_id": operation_id,
            "session_id": "session-1",
            "save_slot_id": "slot-1",
            "companion_id": "mako",
            "command_id": "command-1",
            "request_id": "chat-1",
            "type": "Command.Follow",
            "status": status,
            "reason": "None",
            "occurred_at": "2026-08-16T01:00:00Z",
            "time_context": _game_time(),
        }
    )


@pytest.mark.asyncio
async def test_event_is_scoped_idempotent_and_server_assigns_importance() -> None:
    database = await make_database(make_settings(llm_provider="mock"))
    identity, _ = await make_authenticated_device(database, PROTECTOR)
    async with database.session_factory() as session:
        service = EventService(
            SqlAlchemyEventRepository(session), event_retention_days=7, audit_retention_days=30
        )
        first = await service.create_event(_event(), identity, body_hash="a" * 64)
        replay = await service.create_event(_event(), identity, body_hash="a" * 64)

        assert first.importance == "High"
        assert replay == first
        with pytest.raises(DuplicateRequestError):
            await service.create_event(_event(), identity, body_hash="b" * 64)


@pytest.mark.asyncio
async def test_command_result_requires_candidate_and_strict_transitions() -> None:
    database = await make_database(make_settings(llm_provider="mock"))
    identity, _ = await make_authenticated_device(database, PROTECTOR)
    async with database.session_factory() as session:
        repository = SqlAlchemyEventRepository(session)
        service = EventService(repository, event_retention_days=7, audit_retention_days=30)

        with pytest.raises(CommandCandidateNotFoundError):
            await service.create_command_result(
                _result("missing", "Accepted"), identity, body_hash="a" * 64
            )

        slot = await repository.get_or_create_slot(
            profile_id=identity.profile_id, save_slot_id="slot-1"
        )
        now = datetime.now(UTC)
        session.add(
            CommandCandidateModel(
                row_id=str(uuid4()),
                command_id="command-1",
                profile_id=identity.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id="mako",
                session_id="session-1",
                request_id="chat-1",
                command_type="Command.Follow",
                target_id=None,
                priority="Normal",
                issued_at=now,
                expires_at=now,
                parameters={},
                created_at=now,
                audit_expires_at=now,
            )
        )
        await session.commit()

        with pytest.raises(CommandResultTransitionError):
            await service.create_command_result(
                _result("bad-initial", "Running"), identity, body_hash="0" * 64
            )
        accepted = await service.create_command_result(
            _result("accepted", "Accepted"), identity, body_hash="a" * 64
        )
        replay = await service.create_command_result(
            _result("accepted", "Accepted"), identity, body_hash="a" * 64
        )
        running = await service.create_command_result(
            _result("running", "Running"), identity, body_hash="b" * 64
        )
        succeeded = await service.create_command_result(
            _result("succeeded", "Succeeded"), identity, body_hash="c" * 64
        )
        assert accepted == replay
        assert running.status == "Running"
        assert succeeded.status == "Succeeded"
        with pytest.raises(CommandResultTransitionError):
            await service.create_command_result(
                _result("late", "Succeeded"), identity, body_hash="d" * 64
            )
