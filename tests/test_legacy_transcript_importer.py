import argparse
import hashlib
import json
from pathlib import Path

from scripts.import_legacy_transcripts import _run


async def test_legacy_import_dry_run_reports_hash_cursor_without_mutation(
    tmp_path: Path, capsys: object
) -> None:
    source_dir = tmp_path / "legacy"
    quarantine_dir = tmp_path / "quarantine"
    source_dir.mkdir()
    source = source_dir / "conversation.jsonl"
    source.write_text(
        "\n".join(
            (
                '{"seq":1,"speaker":"player","text":"나는 비를 좋아해",'
                '"at":"2026-08-01T00:00:00Z"}',
                '{"seq":2,"speaker":"companion","text":"알겠어",'
                '"at":"2026-08-01T00:00:01Z"}',
                "{not-json",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()

    result = await _run(
        argparse.Namespace(
            dry_run=True,
            apply=False,
            source_dir=source_dir,
            quarantine_dir=quarantine_dir,
        )
    )

    assert result == 0
    assert source.exists()
    assert not quarantine_dir.exists()
    output = capsys.readouterr().out  # type: ignore[attr-defined]
    report = json.loads(output)[0]
    assert report["sha256"] == expected_hash
    assert report["cursor"] == 1
    assert report["queued_messages"] == 1
    assert report["skipped_rows"] == 1
    assert report["invalid_rows"] == 1
