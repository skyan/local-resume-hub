from pathlib import Path

from app.db import CandidateRecord, Database


def test_candidate_hash_deduplicate(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init_schema()

    file_id = db.upsert_resume_file(
        file_path="/tmp/a.pdf",
        file_hash="hash_a",
        file_mtime=1,
        file_ctime=1,
        discovered_at=1,
        status="done",
        parse_error=None,
    )

    db.insert_candidate(
        CandidateRecord(
            resume_file_id=file_id,
            name="张三",
            phone="13800138000",
            email="z@example.com",
            education="本科",
            years_experience="3年",
            skills="Python",
            applied_position="后端工程师",
        ),
        candidate_hash="hash_a",
        extracted_at=1,
    )

    assert db.has_candidate_hash("hash_a") is True
