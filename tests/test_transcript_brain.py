"""두뇌가 작업 기억의 잘림과 전사 원문을 서로 섞지 않는지 검증한다."""

from pathlib import Path

from app.brain.companion import CompanionBrain
from app.brain.contract import CompanionTurn
from app.brain.llm import MockLLMProvider
from app.brain.transcript import FileTranscriptStore


async def test_brain_transcript_keeps_the_full_player_message(tmp_path: Path) -> None:
    transcript = FileTranscriptStore(directory=tmp_path, max_conversations=10)
    brain = CompanionBrain(MockLLMProvider(), transcript=transcript)
    player_message = "가" * 2000

    await brain.respond(CompanionTurn(text=player_message, conversation_key="conv-1"))
    entries = await transcript.read("conv-1", since=0, limit=10)
    await brain.aclose()

    assert entries[0].text == player_message
    assert len(entries[0].text) == 2000
    assert len(entries[1].text) <= 200
