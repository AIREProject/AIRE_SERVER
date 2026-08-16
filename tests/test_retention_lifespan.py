"""FastAPI retention scheduler wiring."""

import asyncio

from app.main import _run_retention_loop


async def test_periodic_retention_loop_repeats_and_cancels_cleanly() -> None:
    reached = asyncio.Event()

    class RecordingRetention:
        def __init__(self) -> None:
            self.calls = 0

        async def sweep(self) -> None:
            self.calls += 1
            if self.calls == 2:
                reached.set()

    retention = RecordingRetention()
    task = asyncio.create_task(
        _run_retention_loop(retention, interval_seconds=0.001)  # type: ignore[arg-type]
    )
    await asyncio.wait_for(reached.wait(), timeout=1)
    task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass

    assert retention.calls == 2
