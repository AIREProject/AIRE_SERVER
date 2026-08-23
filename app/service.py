"""요청 한 턴을 처리한다: `ChatRequest` → 마코 → `ChatResponse`.

번역은 **여기 한 번뿐이다.** 두뇌에게 `ChatRequest` 를 그대로 주지 않는 이유는 HTTP 계약의
모양이 두뇌로 새어 들어가기 때문이고, 반대로 라우트가 직접 매핑하지 않는 이유는 아래 네
값이 **요청이 아니라 앱 조립 시점에만** 알 수 있기 때문이다 — 특히 `AIMetadata` 는
`build_llm_provider` 의 **폴백까지 반영된** 선택 결과라 `Settings` 만으로 재구성하면
거짓말이 된다(`LLM_PROVIDER=openai` + 키 없음 → 실제로는 mock).

서버는 대화 상태를 보관하지 않는다 — 기억은 전부 마코가 자기 저장소에 들고 있고, 서버는
그것을 찾을 불투명한 키 둘만 넘긴다(`conversation_key`, `player_key`). **신원은 여기서
보관하지 않는다**: `ChatRequest` 자체는 더 이상 신원을 담지 않고, 인증된
`AuthenticatedDevice` 와 그 인증을 만든 `CredentialProtector` 를 호출자가 매 요청마다
넘긴다 — `docs/temporary-scaffolds.md` §2 가 예고한 대로 `player_name` 자기신고를
대체했다. `CompanionService`는 요청마다 전달받은 protector로 profile/save 범위의 안정적인
player key를 만든다. 별도 pepper가 없는 단일 플레이어 demo에서는 고정 demo key를 사용한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from app.brain import (
    COMPANION_PROFILES,
    CompanionAction,
    CompanionBrain,
    CompanionReply,
    CompanionTurn,
    InventoryFacts,
    InventoryItemFacts,
    MemoryScope,
    ResourceFacts,
    SituationTurn,
    ThreatFacts,
    WorkFacts,
    WorldContextFacts,
)
from app.brain.contract import BrainProvenance, ResponseProvenance
from app.brain.enemies import EnemyRepository
from app.brain.gametime import KST, period_code_for_hour
from app.brain.llm import build_llm_provider
from app.brain.memory import MemoryClassification
from app.brain.recipes import RecipeRepository
from app.brain.resources import MAX_GATHER_QUANTITY, ResourceId
from app.brain.store import InMemoryConversationStore
from app.brain.transcript import FileTranscriptStore, TranscriptStore
from app.db.canonical_repository import CanonicalChatRepository
from app.db.connection import Database
from app.db.offline_task_repository import SqlAlchemyOfflineTaskRepository
from app.db.save_slot_repository import SaveSlotRepository
from app.embedding import build_embedding_provider
from app.errors import (
    AIServiceInvalidOutputError,
    AIServiceTimeoutError,
    AIServiceUnavailableError,
    DuplicateRequestError,
    IdempotencyRecordExpiredError,
    InsufficientCraftingMaterialsError,
    InventorySnapshotRequiredError,
    UnknownCompanionError,
)
from app.game_context_models import GameContextV1
from app.gamedata.dataset import DATASET, GameDataSet
from app.identity import DeviceRole
from app.logging import log_response_provenance
from app.models import (
    AIMetadata,
    ChatRequest,
    ChatResponse,
    CommandCandidate,
    CommandType,
    SituationRequest,
    SituationResponse,
    Surface,
    TimeContext,
    TimeSource,
)
from app.offline_task_models import CreateOfflineTaskRequest, OfflineTaskType
from app.offline_task_service import OfflineTaskService
from app.relationship_service import RelationshipPresentationStore
from app.settings import Settings
from app.source_memory_store import SourceBackedMemoryStore

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.credentials import CredentialProtector
    from app.identity import AuthenticatedDevice

# 채집 액션의 자원 식별자 -> Offline_Task 가 참조하는 게임 아이템 카탈로그 ID.
# app/brain/resources.py 의 ResourceId 는 브레인이 채집을 판단하는 축이고, 이 매핑은
# 그 판단을 Offline_Task 계약(app/offline_task_models.py)으로 옮기는 순수 번역이다.
_GATHER_ITEM_IDS: dict[ResourceId, str] = {
    ResourceId.WOOD: "PlantStem",
    ResourceId.STONE: "Stone",
}


class CompanionService:
    """마코 두뇌에 전선 장부(식별자·발급/만료 시각)를 붙여 게임에 내보낸다."""

    def __init__(
        self,
        brain: CompanionBrain,
        *,
        metadata: AIMetadata,
        ai_timeout_seconds: float,
        command_ttl_seconds: float = 30.0,
        default_location_id: str | None = None,
        configured_provider: str | None = None,
        user_message_retention_days: int = 7,
        companion_message_retention_days: int = 7,
        audit_retention_days: int = 30,
    ) -> None:
        self._brain = brain
        self._metadata = metadata
        self._ai_timeout_seconds = ai_timeout_seconds
        self._command_ttl_seconds = command_ttl_seconds
        self._configured_provider = configured_provider or metadata.provider
        # 임시 발판(→ `_location_id`, docs/temporary-scaffolds.md §1). 게임이 위치를
        # 보내기 시작하면 이 필드째 지운다.
        self._default_location_id = default_location_id
        self._user_message_retention_days = user_message_retention_days
        self._companion_message_retention_days = companion_message_retention_days
        self._audit_retention_days = audit_retention_days
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._chat_lock_users: dict[str, int] = {}

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        database: Database,
        *,
        game_dataset: GameDataSet | None = None,
    ) -> CompanionService:
        """서버 설정과 DB로 두뇌까지 조립한다. 앱 수명 동안 한 번만 호출된다.

        `game_dataset` 을 넘기면 그걸로 `RecipeRepository`/`EnemyRepository` 를 만든다 — 앱
        시작 시점에 DB 를 한 번 읽어 만든 스냅샷이다(`app/main.py`, `app/db/game_data_loader.py`).
        생략하면(모든 기존 호출부·테스트) 정적 `DATASET` 을 그대로 쓴다.
        """

        selected = build_llm_provider(settings)
        selected_embedding = build_embedding_provider(settings)
        effective_dataset = game_dataset or DATASET
        transcript: TranscriptStore | None = None
        if settings.transcript_enabled:
            transcript = FileTranscriptStore(
                directory=settings.transcript_dir,
                max_conversations=settings.transcript_max_conversations,
            )
        return cls(
            CompanionBrain(
                selected.provider,
                store=InMemoryConversationStore(
                    pending_ttl_seconds=settings.companion_pending_ttl_seconds,
                    idle_ttl_seconds=settings.companion_conversation_idle_ttl_seconds,
                    max_entries=settings.companion_memory_max_entries,
                ),
                recipes=RecipeRepository(effective_dataset),
                enemies=EnemyRepository(effective_dataset),
                # P3-T01은 canonical source 검증 전의 transcript 기반 기억 저장을
                # 중단했다. T02는 검증된 source-backed 기억만 별도 읽기 경로로 연결한다.
                long_term=None,
                source_memory=SourceBackedMemoryStore(database),
                relationship_presentation=RelationshipPresentationStore(database),
                transcript=transcript,
                embedder=selected_embedding.provider,
                embedding_model=selected_embedding.model_version,
                embedding_timeout_seconds=settings.embedding_timeout_seconds,
                recall_limit=settings.long_term_recall_limit,
                extract_every_n_turns=settings.long_term_extract_every_n_turns,
                quiet_seconds=settings.long_term_quiet_seconds,
                session_end_seconds=settings.long_term_session_end_seconds,
                tick_seconds=settings.long_term_tick_seconds,
                transcript_retention_days=settings.transcript_retention_days,
            ),
            metadata=AIMetadata(
                provider=selected.name,
                model_version=selected.model_version,
                prompt_version=settings.companion_prompt_version,
            ),
            ai_timeout_seconds=settings.ai_request_timeout_seconds,
            command_ttl_seconds=settings.companion_command_ttl_seconds,
            default_location_id=settings.companion_default_location_id,
            configured_provider=selected.configured_name,
            user_message_retention_days=settings.user_message_retention_days,
            companion_message_retention_days=settings.companion_message_retention_days,
            audit_retention_days=settings.audit_retention_days,
        )

    async def create_response(
        self,
        request: ChatRequest,
        identity: AuthenticatedDevice,
        session: AsyncSession,
        protector: CredentialProtector,
    ) -> ChatResponse:
        started_at = perf_counter()
        identity.validate_claims(request.profile_id, request.device_id)
        if request.companion_id not in COMPANION_PROFILES:
            raise UnknownCompanionError(request.companion_id)
        lock_key = json.dumps(
            [identity.profile_id, request.save_slot_id, request.companion_id, request.request_id],
            separators=(",", ":"),
        )
        async with self._chat_lock(lock_key):
            repository = CanonicalChatRepository(
                session,
                user_retention_days=self._user_message_retention_days,
                companion_retention_days=self._companion_message_retention_days,
                audit_retention_days=self._audit_retention_days,
            )
            start = await repository.begin(
                request,
                identity,
                request_digest=_chat_request_digest(request, identity.role),
            )
            if start.operation.request_digest != _chat_request_digest(request, identity.role):
                raise DuplicateRequestError
            if start.operation.state == "Completed":
                replay = await repository.build_response(start.operation)
                if replay is None:
                    raise IdempotencyRecordExpiredError
                return replay

            prepared = None
            task_created = False
            response = await repository.build_response(start.operation)
            if response is None:
                history = await repository.history_before(start.input_message)
                game_time = request.time_context
                if request.surface is Surface.MOBILE or (
                    game_time is not None and game_time.source is TimeSource.REAL_WORLD
                ):
                    kst_now = datetime.now(KST)
                    game_time = TimeContext(
                        source=TimeSource.REAL_WORLD,
                        day=kst_now.day,
                        hour=kst_now.hour,
                        period=period_code_for_hour(kst_now.hour),
                    )
                turn = CompanionTurn(
                    text=request.user_message,
                    surface=request.surface,
                    allowed_actions=frozenset(request.allowed_commands),
                    world_context=self._world_context_facts(request.game_context),
                    game_time=game_time,
                    conversation_key=start.conversation.conversation_id,
                    player_key=_player_key(
                        protector,
                        profile_id=identity.profile_id,
                        save_slot_id=request.save_slot_id,
                    ),
                    companion_id=request.companion_id,
                    memory_scope=MemoryScope(
                        profile_id=identity.profile_id,
                        save_slot_row_id=start.conversation.save_slot_row_id,
                        companion_id=request.companion_id,
                    ),
                )
                try:
                    async with asyncio.timeout(self._ai_timeout_seconds):
                        if hasattr(self._brain, "prepare_response"):
                            prepared = await self._brain.prepare_response(turn, history=history)
                            reply = prepared.reply
                        else:
                            reply = await self._brain.respond(turn)
                except TimeoutError as error:
                    raise AIServiceTimeoutError from error
                except Exception as error:
                    raise AIServiceUnavailableError from error

                offline_task_id: str | None = None
                offline_task_plan: dict[str, object] | None = None
                if (
                    identity.role is DeviceRole.WEB_CLIENT
                    and reply.action is not None
                    and reply.action.type in {CommandType.GATHER_RESOURCE, CommandType.CRAFT_ITEM}
                ):
                    offline_task_id = f"task-{uuid4()}"
                    offline_task_plan = self._offline_task_plan(reply.action)
                    candidates: list[CommandCandidate] = []
                else:
                    candidates = self._candidates(request, reply)
                self._assert_within_allowlist(request, candidates)
                display_text = reply.text
                if offline_task_id is not None and offline_task_plan is not None:
                    try:
                        await self._create_offline_task(
                            request,
                            identity,
                            session,
                            offline_task_plan,
                            task_id=offline_task_id,
                        )
                        task_created = True
                    except InventorySnapshotRequiredError:
                        display_text = (
                            "게임 인벤토리를 아직 동기화하지 못했어. "
                            "게임에 한 번 접속한 뒤 다시 부탁해 줘."
                        )
                        offline_task_id = None
                        offline_task_plan = None
                        prepared = None
                    except InsufficientCraftingMaterialsError:
                        display_text = (
                            "지금 서버에 저장된 나무가 부족해서 제작을 시작하지 못했어. "
                            "엉성한 붕대 하나에 나무 2개가 필요해."
                        )
                        offline_task_id = None
                        offline_task_plan = None
                        prepared = None
                response = ChatResponse(
                    request_id=request.request_id,
                    message_id=start.operation.input_message_id,
                    session_id=request.session_id,
                    save_slot_id=request.save_slot_id,
                    companion_id=request.companion_id,
                    response_id=f"response-{uuid4()}",
                    display_text=display_text,
                    command_candidates=candidates,
                    offline_task_id=offline_task_id,
                    ai_metadata=self._metadata,
                )
                await repository.save_generated(
                    start,
                    response,
                    offline_task_plan=offline_task_plan,
                )
                self._log_provenance(
                    request_id=request.request_id,
                    surface=request.surface.value,
                    brain=reply.provenance,
                    started_at=started_at,
                )

            if response.offline_task_id is not None and not task_created:
                offline_task_plan = repository.offline_task_plan(start.operation)
                if offline_task_plan is None:
                    raise AIServiceInvalidOutputError
                await self._create_offline_task(
                    request,
                    identity,
                    session,
                    offline_task_plan,
                    task_id=response.offline_task_id,
                )
            await repository.complete(start, response, response.command_candidates)
            if prepared is not None:
                await self._brain.commit_response(prepared)
            return response

    async def create_situation_response(
        self,
        request: SituationRequest,
        identity: AuthenticatedDevice,
        session: AsyncSession,
        protector: CredentialProtector,
    ) -> SituationResponse:
        """플레이어 발화 없이, 클라이언트가 알려 온 상황에 마코가 먼저 말을 걸게 한다.

        `create_response` 와 달리 라우팅을 거치지 않는다 — 무슨 상황인지는 클라이언트가
        코드로 이미 판단해 보냈다. 그래서 명령 후보도, `_assert_within_allowlist` 도 없다
        — 낼 수 있는 행동이 없다.
        """

        started_at = perf_counter()
        identity.validate_claims(request.profile_id, request.device_id)
        if request.companion_id not in COMPANION_PROFILES:
            raise UnknownCompanionError(request.companion_id)
        slot = await SaveSlotRepository(session).get_or_create(
            profile_id=identity.profile_id, save_slot_id=request.save_slot_id
        )
        game_time = request.time_context
        if request.surface is Surface.MOBILE or (
            game_time is not None and game_time.source is TimeSource.REAL_WORLD
        ):
            kst_now = datetime.now(KST)
            game_time = TimeContext(
                source=TimeSource.REAL_WORLD,
                day=kst_now.day,
                hour=kst_now.hour,
                period=period_code_for_hour(kst_now.hour),
            )
        turn = SituationTurn(
            situation=tuple(request.situation),
            surface=request.surface,
            game_time=game_time,
            conversation_key=_conversation_key(
                protector,
                profile_id=identity.profile_id,
                save_slot_id=request.save_slot_id,
                companion_id=request.companion_id,
                session_id=request.session_id,
            ),
            player_key=_player_key(
                protector, profile_id=identity.profile_id, save_slot_id=request.save_slot_id
            ),
            companion_id=request.companion_id,
            memory_scope=MemoryScope(
                profile_id=identity.profile_id,
                save_slot_row_id=slot.row_id,
                companion_id=request.companion_id,
            ),
        )
        try:
            async with asyncio.timeout(self._ai_timeout_seconds):
                reply = await self._brain.react_with_provenance(turn)
        except TimeoutError as error:
            raise AIServiceTimeoutError from error
        except Exception as error:  # 두뇌 장애는 표준 오류로 변환한다.
            raise AIServiceUnavailableError from error

        response = SituationResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            save_slot_id=request.save_slot_id,
            companion_id=request.companion_id,
            response_id=f"response-{uuid4()}",
            display_text=reply.text,
            ai_metadata=self._metadata,
        )
        self._log_provenance(
            request_id=request.request_id,
            surface=request.surface.value,
            brain=reply.provenance,
            started_at=started_at,
        )
        return response

    def _log_provenance(
        self,
        *,
        request_id: str,
        surface: str,
        brain: BrainProvenance | None,
        started_at: float,
    ) -> None:
        if brain is None:
            return
        calls = brain.provider_calls
        log_response_provenance(
            ResponseProvenance(
                request_id=request_id,
                surface=surface,
                top_intent=brain.top_intent,
                query_mode=brain.query_mode,
                selected_route=brain.selected_route,
                repository_match=brain.repository_match,
                fact_ids=brain.fact_ids,
                configured_provider=self._configured_provider,
                effective_provider=brain.effective_provider,
                provider_call_succeeded=(all(call.succeeded for call in calls) if calls else None),
                provider_fallback_used=any(call.fallback_used for call in calls),
                final_fallback_reason=brain.final_fallback_reason,
                final_response_source=brain.final_response_source,
                model_version=self._metadata.model_version,
                prompt_version=self._metadata.prompt_version,
                sanitizer_succeeded=brain.sanitizer_succeeded,
                duration_ms=round((perf_counter() - started_at) * 1000, 3),
                provider_calls=calls,
            )
        )

    async def _create_offline_task(
        self,
        request: ChatRequest,
        identity: AuthenticatedDevice,
        session: AsyncSession,
        plan: dict[str, object],
        *,
        task_id: str,
    ) -> str:
        """검증된 모바일 액션을 Offline Task로 등록하고 새 task_id를 돌려준다.

        `request.request_id` 를 그대로 작업 생성 요청의 request_id 로 재사용한다 —
        `OfflineTaskService.create` 는 (profile_id, save_slot_id, request_id) 로 이미
        멱등이라, 챗 요청이 재시도돼도 작업이 중복 생성되지 않는다.
        """

        item_id = plan.get("item_id")
        quantity = plan.get("quantity")
        task_type_value = plan.get("task_type")
        if (
            not isinstance(item_id, str)
            or not isinstance(quantity, int)
            or not isinstance(task_type_value, str)
        ):
            raise AIServiceInvalidOutputError
        try:
            task_type = OfflineTaskType(task_type_value)
        except ValueError as error:
            raise AIServiceInvalidOutputError from error
        create_request = CreateOfflineTaskRequest(
            request_id=request.request_id,
            save_slot_id=request.save_slot_id,
            task_type=task_type,
            item_id=item_id,
            quantity=quantity,
        )
        result = await OfflineTaskService(SqlAlchemyOfflineTaskRepository(session)).create(
            create_request,
            identity,
            auto_start=True,
            commit=False,
            task_id=task_id,
        )
        return result.task.task_id

    @staticmethod
    def _offline_task_plan(action: CompanionAction) -> dict[str, object]:
        if action.type is CommandType.GATHER_RESOURCE:
            resource = action.parameters.get("resource")
            item_id = (
                _GATHER_ITEM_IDS.get(ResourceId(resource)) if isinstance(resource, str) else None
            )
            if item_id is None:
                raise AIServiceInvalidOutputError
            quantity = action.parameters.get("quantity")
            if not isinstance(quantity, int):
                quantity = MAX_GATHER_QUANTITY
            return {
                "task_type": OfflineTaskType.GATHERING.value,
                "item_id": item_id,
                "quantity": quantity,
            }
        if action.type is CommandType.CRAFT_ITEM:
            recipe_id = action.parameters.get("recipe_id")
            quantity = action.parameters.get("quantity")
            if recipe_id != "recipe-1" or type(quantity) is not int or not 1 <= quantity <= 50:
                raise AIServiceInvalidOutputError
            return {
                "task_type": OfflineTaskType.CRAFTING.value,
                "item_id": "ShoddyBandage",
                "quantity": quantity,
            }
        raise AIServiceInvalidOutputError

    async def aclose(self) -> None:
        """앱 종료 시 두뇌가 보유한 HTTP 클라이언트 등을 정리한다."""

        await self._brain.aclose()

    async def classify_memory(self, text: str) -> MemoryClassification:
        return await self._brain.classify_memory(text)

    async def embed_memory_text(self, text: str) -> tuple[tuple[float, ...] | None, str | None]:
        return await self._brain.embed_memory_text(text)

    @asynccontextmanager
    async def _chat_lock(self, key: str) -> AsyncIterator[None]:
        lock = self._chat_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._chat_locks[key] = lock
        self._chat_lock_users[key] = self._chat_lock_users.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._chat_lock_users[key] -= 1
            if self._chat_lock_users[key] == 0:
                del self._chat_lock_users[key]
                del self._chat_locks[key]

    def _candidates(
        self,
        request: ChatRequest,
        reply: CompanionReply,
    ) -> list[CommandCandidate]:
        if reply.action is None:
            return []
        return [self._candidate(request, reply.action)]

    def _candidate(self, request: ChatRequest, action: CompanionAction) -> CommandCandidate:
        """두뇌가 정한 행동에 전선 장부를 붙여 명령 후보로 만든다."""

        issued_at = datetime.now(UTC)
        return CommandCandidate(
            command_id=f"command-{uuid4()}",
            request_id=request.request_id,
            type=action.type,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=self._command_ttl_seconds),
            parameters=action.parameters,
        )

    @staticmethod
    def _assert_within_allowlist(
        request: ChatRequest,
        candidates: Iterable[CommandCandidate],
    ) -> None:
        """`graph.py` 가 결정하고, 여기서 단언한다.

        두뇌는 이미 `allowed_actions` 로 거른다(`graph.py` 의 두 멤버십 검사). 이 단언은
        그 검사의 회귀를 잡기 위한 것이지 신뢰할 수 없는 출력을 검사하는 것이 아니다 —
        두뇌는 같은 프로세스, 같은 저장소 안에 있다. 서로 다른 이유를 가진 두 지점이라서
        남길 값어치가 있고, 게임이 허락하지 않은 명령을 받는 일만은 없어야 한다.
        """

        allowed = set(request.allowed_commands)
        for candidate in candidates:
            if candidate.type not in allowed:
                raise AIServiceInvalidOutputError

    def _world_context_facts(self, context: GameContextV1 | None) -> WorldContextFacts:
        """검증된 HTTP Context를 provider-neutral immutable facts로 한 번만 번역한다."""

        if context is None:
            return WorldContextFacts(location_id=self._default_location_id)

        location_id = context.location_id or self._default_location_id
        current_work = (
            WorkFacts(
                type=context.current_work.type.value,
                state=context.current_work.state.value,
            )
            if context.current_work is not None
            else None
        )
        resources = tuple(
            ResourceFacts(kind=resource.kind, count=resource.count)
            for resource in sorted(context.nearby_resources, key=lambda item: item.kind)
        )
        inventories = tuple(
            InventoryFacts(
                container_id=inventory.container_id.value,
                free_slots=inventory.free_slots,
                item_totals=tuple(
                    InventoryItemFacts(item_id=item.item_id, count=item.count)
                    for item in sorted(inventory.item_totals, key=lambda item: item.item_id)
                ),
                truncated=inventory.truncated,
            )
            for inventory in sorted(context.inventories, key=lambda item: item.container_id.value)
        )
        return WorldContextFacts(
            is_available=True,
            location_id=location_id,
            threat=ThreatFacts(
                present=context.threat.present,
                count=context.threat.count,
                nearest_kind=context.threat.nearest_kind,
            ),
            nearby_resources=resources,
            available_workstations=tuple(sorted(context.available_workstations)),
            current_work=current_work,
            inventories=inventories,
        )


def _conversation_key(
    protector: CredentialProtector,
    *,
    profile_id: str,
    save_slot_id: str,
    companion_id: str,
    session_id: str,
) -> str:
    """마코가 대화를 식별할 불투명 키를 만든다.

    프로필·세이브슬롯·컴패니언·세션이 스코프다 — 넷 중 하나라도 다르면 다른 대화이고,
    기억이 섞이면 안 된다. `POST /chat` 과 `POST /situations` 가 같은 네 값을 보내면 같은
    키를 내야 상황 이벤트가 채팅과 같은 대화 기억을 잇는다 — 그래서 이 함수는 `ChatRequest`
    가 아니라 원시 값을 받는다.

    구분자로 이어 붙이지 않고 JSON 배열로 직렬화한다. 각 값이 `:` 를 포함할 수 있어
    `"a:b" + "c"` 와 `"a" + "b:c"` 가 같은 문자열이 되고, 서로 다른 두 대화가 한 기억을
    공유하게 된다. JSON 은 각 항목을 따옴표로 감싸 이 모호성을 없앤다.

    해시는 `CredentialProtector` 의 HMAC 이다 — `profile_id` 는 이제 인증된 값이라,
    여기서 감춰야 진짜로 신원을 감춘 것이 된다(`docs/temporary-scaffolds.md` §2).
    """

    scope = json.dumps(
        [profile_id, save_slot_id, companion_id, session_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return protector.hash_value("conversation-key", scope)


def _player_key(protector: CredentialProtector, *, profile_id: str, save_slot_id: str) -> str:
    """마코가 사람을 식별할 불투명 키를 만든다.

    `_conversation_key` 와 달리 프로필·세이브슬롯만이 스코프다 — 장기기억은 세션과
    컴패니언을 넘어 이어져야 하고, 세션이 섞이면 안 되는 것은 대화 기억 쪽이다. 같은
    (프로필, 세이브슬롯) 은 항상 같은 키를 낸다.

    같은 이유로 JSON 배열로 직렬화하고, 같은 이유로 HMAC 을 씌운다. 여기서는 이유가
    하나 더 있다 — 이 값이 그대로 파일 이름이 된다.
    """

    scope = json.dumps(
        [profile_id, save_slot_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return protector.hash_value("player-key", scope)


def _chat_request_digest(request: ChatRequest, role: DeviceRole) -> str:
    request_payload = request.model_dump(mode="json")
    request_payload.pop("profile_id", None)
    request_payload.pop("device_id", None)
    request_payload["allowed_commands"] = sorted(request_payload["allowed_commands"])
    request_payload["recent_event_ids"] = sorted(request_payload["recent_event_ids"])
    context = request_payload.get("game_context")
    if isinstance(context, dict):
        context["nearby_resources"] = sorted(
            context["nearby_resources"], key=lambda item: item["kind"]
        )
        context["available_workstations"] = sorted(context["available_workstations"])
        for inventory in context["inventories"]:
            inventory["item_totals"] = sorted(
                inventory["item_totals"], key=lambda item: item["item_id"]
            )
        context["inventories"] = sorted(
            context["inventories"], key=lambda item: item["container_id"]
        )
    payload = json.dumps(
        {"device_role": role.value, "request": request_payload},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
