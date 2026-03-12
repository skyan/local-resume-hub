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


def test_list_candidate_files_by_ids(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init_schema()

    file_id = db.upsert_resume_file(
        file_path="/tmp/export_target.pdf",
        file_hash="hash_export",
        file_mtime=1,
        file_ctime=1,
        discovered_at=1,
        status="done",
        parse_error=None,
    )
    cid = db.insert_candidate(
        CandidateRecord(
            resume_file_id=file_id,
            name="李四",
            phone=None,
            email=None,
            education=None,
            years_experience=None,
            skills=None,
            applied_position="后端工程师",
        ),
        candidate_hash="hash_export",
        extracted_at=1,
    )

    rows = db.list_candidate_files_by_ids([cid])
    assert len(rows) == 1
    assert rows[0]["candidate_id"] == cid
    assert rows[0]["file_path"] == "/tmp/export_target.pdf"


def test_update_candidate_fields(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init_schema()

    file_id = db.upsert_resume_file(
        file_path="/tmp/update_target.pdf",
        file_hash="hash_update",
        file_mtime=1,
        file_ctime=1,
        discovered_at=1,
        status="done",
        parse_error=None,
    )
    cid = db.insert_candidate(
        CandidateRecord(
            resume_file_id=file_id,
            name="王五",
            phone=None,
            email=None,
            education=None,
            years_experience=None,
            skills=None,
            applied_position="未知岗位",
        ),
        candidate_hash="hash_update",
        extracted_at=1,
    )

    ok = db.update_candidate_fields(cid, {"applied_position": "后端开发工程师", "name": "王五A"})
    assert ok is True
    item = db.get_candidate(cid)
    assert item is not None
    assert item["applied_position"] == "后端开发工程师"
    assert item["name"] == "王五A"


def test_delete_candidate_and_resume(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    db.init_schema()

    file_id = db.upsert_resume_file(
        file_path="/tmp/delete_target.pdf",
        file_hash="hash_delete",
        file_mtime=1,
        file_ctime=1,
        discovered_at=1,
        status="done",
        parse_error=None,
    )
    cid = db.insert_candidate(
        CandidateRecord(
            resume_file_id=file_id,
            name="赵六",
            phone=None,
            email=None,
            education=None,
            years_experience=None,
            skills=None,
            applied_position="后端开发工程师",
        ),
        candidate_hash="hash_delete",
        extracted_at=1,
    )

    removed = db.delete_candidate_and_resume(cid)
    assert removed is not None
    assert removed["candidate_id"] == cid
    assert db.get_candidate(cid) is None
