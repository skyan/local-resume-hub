from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CandidateRecord:
    resume_file_id: int
    name: str | None
    phone: str | None
    email: str | None
    education: str | None
    years_experience: str | None
    skills: str | None
    applied_position: str | None


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS resume_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT NOT NULL UNIQUE,
                    file_hash TEXT NOT NULL,
                    file_mtime INTEGER NOT NULL,
                    file_ctime INTEGER NOT NULL,
                    discovered_at INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    parse_error TEXT,
                    parse_detail TEXT
                );

                CREATE TABLE IF NOT EXISTS candidates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resume_file_id INTEGER NOT NULL,
                    candidate_hash TEXT NOT NULL,
                    name TEXT,
                    phone TEXT,
                    email TEXT,
                    education TEXT,
                    years_experience TEXT,
                    skills TEXT,
                    applied_position TEXT,
                    extracted_at INTEGER NOT NULL,
                    FOREIGN KEY (resume_file_id) REFERENCES resume_files(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_candidates_candidate_hash
                    ON candidates(candidate_hash);
                CREATE INDEX IF NOT EXISTS idx_resume_mtime
                    ON resume_files(file_mtime DESC);
                CREATE INDEX IF NOT EXISTS idx_candidate_name
                    ON candidates(name);
                CREATE INDEX IF NOT EXISTS idx_candidate_position
                    ON candidates(applied_position);
                """
            )
            self._ensure_column(conn, "resume_files", "parse_detail", "TEXT")

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, column_def: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(r["name"]) for r in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}")

    def upsert_resume_file(
        self,
        *,
        file_path: str,
        file_hash: str,
        file_mtime: int,
        file_ctime: int,
        discovered_at: int,
        status: str,
        parse_error: str | None,
        parse_detail: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO resume_files (file_path, file_hash, file_mtime, file_ctime, discovered_at, status, parse_error, parse_detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_path)
                DO UPDATE SET
                    file_hash=excluded.file_hash,
                    file_mtime=excluded.file_mtime,
                    file_ctime=excluded.file_ctime,
                    discovered_at=excluded.discovered_at,
                    status=excluded.status,
                    parse_error=excluded.parse_error,
                    parse_detail=excluded.parse_detail
                RETURNING id
                """,
                (file_path, file_hash, file_mtime, file_ctime, discovered_at, status, parse_error, parse_detail),
            )
            row = cur.fetchone()
            return int(row["id"])

    def has_candidate_hash(self, candidate_hash: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM candidates WHERE candidate_hash = ? LIMIT 1",
                (candidate_hash,),
            ).fetchone()
            return row is not None

    def get_resume_file(self, file_path: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT id, file_path, file_hash, status, parse_error, parse_detail
                FROM resume_files
                WHERE file_path = ?
                LIMIT 1
                """,
                (file_path,),
            ).fetchone()
            return dict(row) if row else None

    def insert_candidate(self, rec: CandidateRecord, candidate_hash: str, extracted_at: int) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO candidates (
                    resume_file_id, candidate_hash, name, phone, email, education,
                    years_experience, skills, applied_position, extracted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    rec.resume_file_id,
                    candidate_hash,
                    rec.name,
                    rec.phone,
                    rec.email,
                    rec.education,
                    rec.years_experience,
                    rec.skills,
                    rec.applied_position,
                    extracted_at,
                ),
            )
            return int(cur.fetchone()["id"])

    def list_candidates(
        self,
        *,
        q: str | None,
        name: str | None,
        position: str | None,
        date_from: int | None,
        date_to: int | None,
        sort_by: str,
        order: str,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        sort_map = {
            "created_at": "rf.file_mtime",
            "name": "c.name",
            "position": "c.applied_position",
        }
        sort_field = sort_map.get(sort_by, "rf.file_mtime")
        order_sql = "ASC" if order.lower() == "asc" else "DESC"
        offset = (max(page, 1) - 1) * page_size

        filters = []
        args: list[Any] = []

        if q:
            filters.append("(c.name LIKE ? OR c.applied_position LIKE ? OR c.skills LIKE ?)")
            like = f"%{q}%"
            args.extend([like, like, like])
        if name:
            filters.append("c.name LIKE ?")
            args.append(f"%{name}%")
        if position:
            filters.append("c.applied_position LIKE ?")
            args.append(f"%{position}%")
        if date_from is not None:
            filters.append("rf.file_mtime >= ?")
            args.append(date_from)
        if date_to is not None:
            filters.append("rf.file_mtime <= ?")
            args.append(date_to)

        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""

        base_sql = f"""
            FROM candidates c
            JOIN resume_files rf ON c.resume_file_id = rf.id
            {where_sql}
        """

        with self.connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) AS n {base_sql}", args).fetchone()["n"])
            rows = conn.execute(
                f"""
                SELECT
                    c.id,
                    c.name,
                    c.phone,
                    c.email,
                    c.education,
                    c.years_experience,
                    c.skills,
                    c.applied_position,
                    c.extracted_at,
                    rf.file_path,
                    rf.file_mtime,
                    rf.file_ctime,
                    rf.parse_detail
                {base_sql}
                ORDER BY {sort_field} {order_sql}, c.id DESC
                LIMIT ? OFFSET ?
                """,
                [*args, page_size, offset],
            ).fetchall()

            return [dict(r) for r in rows], total

    def get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    c.id,
                    c.name,
                    c.phone,
                    c.email,
                    c.education,
                    c.years_experience,
                    c.skills,
                    c.applied_position,
                    c.extracted_at,
                    rf.file_path,
                    rf.file_mtime,
                    rf.file_ctime,
                    rf.status,
                    rf.parse_error,
                    rf.parse_detail
                FROM candidates c
                JOIN resume_files rf ON c.resume_file_id = rf.id
                WHERE c.id = ?
                """,
                (candidate_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_indexed_file_paths(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT file_path
                FROM resume_files
                WHERE status IN ('done', 'duplicate', 'error')
                """
            ).fetchall()
            return {str(r["file_path"]) for r in rows}

    def list_positions(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT applied_position
                FROM candidates
                WHERE applied_position IS NOT NULL
                  AND TRIM(applied_position) != ''
                GROUP BY applied_position
                ORDER BY COUNT(*) DESC, applied_position ASC
                """
            ).fetchall()
            return [str(r["applied_position"]) for r in rows]
