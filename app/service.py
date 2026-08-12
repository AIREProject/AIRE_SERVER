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
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
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
    ResourceFacts,
    SituationTurn,
    ThreatFacts,
    WorkFacts,
    WorldContextFacts,
)
from app.brain.enemies import EnemyRepository
from app.brain.llm import build_llm_provider
from app.brain.memory import LongTermStore
from app.brain.recipes import RecipeRepository
from app.brain.resources import MAX_GATHER_QUANTITY, ResourceId
from app.brain.store import InMemoryConversationStore
from app.brain.transcript import FileTranscriptStore, TranscriptStore
from app.db.connection import Database
from app.db.offline_task_repository import SqlAlchemyOfflineTaskRepository
from app.db.save_slot_repository import SaveSlotRepository
from app.embedding import build_embedding_provider
from app.episodic_memory_store import EpisodicMemoryStore
from app.errors import (
    AIServiceInvalidOutputError,
    AIServiceTimeoutError,
    AIServiceUnavailableError,
    UnknownCompanionError,
)
from app.game_context_models import GameContextV1
from app.gamedata.dataset import DATASET, GameDataSet
from app.identity import DeviceRole
from app.models import (
    AIMetadata,
    ChatRequest,
    ChatResponse,
    CommandCandidate,
    CommandType,
    SituationRequest,
    SituationResponse,
)
from app.offline_task_models import CreateOfflineTaskRequest, OfflineTaskType
from app.offline_task_service import OfflineTaskService
from app.settings import Settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.credentials import CredentialProtector
    from app.identity import AuthenticatedDevice

# 채집 액션의 자원 식별자 -> Offline_Task 가 참조하는 게임 아이템 카탈로그 ID.
# app/brain/resources.py 의 ResourceId 는 브레인이 채집을 판단하는 축이고, 이 매핑은
# 그 판단을 Offline_Task 계약(app/offline_task_models.py)으로 옮기는 순수 번역이다.
_GATHER_ITEM_IDS: dict[ResourceId, str] = {
    ResourceId.WOOD: "Branch",
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
    ) -> None:
        self._brain = brain
        self._metadata = metadata
        self._ai_timeout_seconds = ai_timeout_seconds
        self._command_ttl_seconds = command_ttl_seconds
        # 임시 발판(→ `_location_id`, docs/temporary-scaffolds.md §1). 게임이 위치를
        # 보내기 시작하면 이 필드째 지운다.
        self._default_location_id = default_location_id

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
        long_term: LongTermStore | None = None
        if settings.long_term_memory_enabled:
            long_term = EpisodicMemoryStore(
                database, embedding_model=selected_embedding.model_version
            )
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
                long_term=long_term,
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
        )

    async def create_response(
        self,
        request: ChatRequest,
        identity: AuthenticatedDevice,
        session: AsyncSession,
        protector: CredentialProtector,
    ) -> ChatResponse:
        identity.validate_claims(request.profile_id, request.device_id)
        if request.companion_id not in COMPANION_PROFILES:
            raise UnknownCompanionError(request.companion_id)
        await SaveSlotRepository(session).get_or_create(
            profile_id=identity.profile_id, save_slot_id=request.save_slot_id
        )

        turn = CompanionTurn(
            text=request.user_message,
            surface=request.surface,
            allowed_actions=frozenset(request.allowed_commands),
            world_context=self._world_context_facts(request.game_context),
            game_time=request.time_context,
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
        )
        try:
            async with asyncio.timeout(self._ai_timeout_seconds):
                reply = await self._brain.respond(turn)
        except TimeoutError as error:
            raise AIServiceTimeoutError from error
        except Exception as error:  # 두뇌 장애는 표준 오류로 변환한다.
            raise AIServiceUnavailableError from error

        offline_task_id: str | None = None
        if (
            identity.role is DeviceRole.WEB_CLIENT
            and reply.action is not None
            and reply.action.type is CommandType.GATHER_RESOURCE
        ):
            # 이 대화엔 명령을 즉시 받을 살아있는 GameClient가 없다 — 명령 후보 대신
            # Offline_Task 를 등록하고, 게임 쪽엔 만료되는 명령 후보를 주지 않는다.
            offline_task_id = await self._create_gather_task(
                request, identity, session, reply.action
            )
            candidates: list[CommandCandidate] = []
        else:
            candidates = self._candidates(request, reply)
        self._assert_within_allowlist(request, candidates)
        return ChatResponse(
            request_id=request.request_id,
            message_id=request.message_id,
            session_id=request.session_id,
            save_slot_id=request.save_slot_id,
            companion_id=request.companion_id,
            response_id=f"response-{uuid4()}",
            display_text=reply.text,
            command_candidates=candidates,
            offline_task_id=offline_task_id,
            ai_metadata=self._metadata,
        )

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

        identity.validate_claims(request.profile_id, request.device_id)
        if request.companion_id not in COMPANION_PROFILES:
            raise UnknownCompanionError(request.companion_id)
        await SaveSlotRepository(session).get_or_create(
            profile_id=identity.profile_id, save_slot_id=request.save_slot_id
        )

        turn = SituationTurn(
            situation=tuple(request.situation),
            surface=request.surface,
            game_time=request.time_context,
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
        )
        try:
            async with asyncio.timeout(self._ai_timeout_seconds):
                display_text = await self._brain.react(turn)
        except TimeoutError as error:
            raise AIServiceTimeoutError from error
        except Exception as error:  # 두뇌 장애는 표준 오류로 변환한다.
            raise AIServiceUnavailableError from error

        return SituationResponse(
            request_id=request.request_id,
            session_id=request.session_id,
            save_slot_id=request.save_slot_id,
            companion_id=request.companion_id,
            response_id=f"response-{uuid4()}",
            display_text=display_text,
            ai_metadata=self._metadata,
        )

    async def _create_gather_task(
        self,
        request: ChatRequest,
        identity: AuthenticatedDevice,
        session: AsyncSession,
        action: CompanionAction,
    ) -> str:
        """채집 액션을 Offline_Task(Gathering) 로 등록하고 새 task_id 를 돌려준다.

        `request.request_id` 를 그대로 작업 생성 요청의 request_id 로 재사용한다 —
        `OfflineTaskService.create` 는 (profile_id, save_slot_id, request_id) 로 이미
        멱등이라, 챗 요청이 재시도돼도 작업이 중복 생성되지 않는다.
        """

        resource = action.parameters.get("resource")
        item_id = _GATHER_ITEM_IDS.get(ResourceId(resource)) if isinstance(resource, str) else None
        if item_id is None:
            # gather_node 는 지원 자원만 액션으로 내보낸다 — 여기 오면 그래프 회귀다.
            raise AIServiceInvalidOutputError
        # 플레이어가 수량을 말하지 않으면(브레인이 키 자체를 생략) 상한치를 요청 수량으로
        # 삼는다 — 살아있는 GameClient가 없어 서버가 대신 "얼마나 모았는지" 시간으로
        # 역산해야 하므로, 상한이 없으면 그 계산의 기준을 정할 수 없다.
        quantity = action.parameters.get("quantity")
        if not isinstance(quantity, int):
            quantity = MAX_GATHER_QUANTITY
        create_request = CreateOfflineTaskRequest(
            request_id=request.request_id,
            save_slot_id=request.save_slot_id,
            task_type=OfflineTaskType.GATHERING,
            item_id=item_id,
            quantity=quantity,
        )
        result = await OfflineTaskService(SqlAlchemyOfflineTaskRepository(session)).create(
            create_request, identity, auto_start=True
        )
        return result.task.task_id

    async def aclose(self) -> None:
        """앱 종료 시 두뇌가 보유한 HTTP 클라이언트 등을 정리한다."""

        await self._brain.aclose()

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


def _player_key(
    protector: CredentialProtector, *, profile_id: str, save_slot_id: str
) -> str:
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
