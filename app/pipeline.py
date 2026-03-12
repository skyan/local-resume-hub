from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from app.config import SUPPORTED_EXTENSIONS, Settings
from app.db import CandidateRecord, Database
from app.extractors import extract_candidate_info, extract_text_with_method, merge_candidate_info
from app.llm import LLMEnhancer


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ProcessResult:
    status: str
    error: str | None = None


class ResumeEventHandler(FileSystemEventHandler):
    def __init__(self, service: "ResumeIngestionService"):
        self.service = service

    def on_created(self, event):  # type: ignore[override]
        if not event.is_directory:
            self.service.enqueue_threadsafe(Path(event.src_path))

    def on_modified(self, event):  # type: ignore[override]
        if not event.is_directory:
            self.service.enqueue_threadsafe(Path(event.src_path))


class ResumeIngestionService:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.queue: asyncio.Queue[Path] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.observer: Observer | None = None
        self.llm = LLMEnhancer(settings.dashscope_api_key, settings.llm_model)
        self._seen: dict[str, float] = {}
        self._progress_cache: dict[str, Any] = {
            "total_files": 0,
            "indexed_files": 0,
            "pending_files": 0,
            "percentage": 0.0,
            "last_updated": int(time.time()),
        }
        self._progress_cache_ts: float = 0.0

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        await self.enqueue_existing_files()
        self.start_watcher()
        asyncio.create_task(self.worker(), name="resume-worker")
        asyncio.create_task(self.periodic_scan(), name="resume-periodic-scan")

    async def stop(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=3)

    def start_watcher(self) -> None:
        handler = ResumeEventHandler(self)
        self.observer = Observer()
        self.observer.schedule(handler, str(self.settings.resume_root), recursive=True)
        self.observer.start()

    async def enqueue_existing_files(self) -> None:
        root = self.settings.resume_root
        if not root.exists():
            return
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                await self.queue.put(p)
        self._progress_cache_ts = 0.0

    async def periodic_scan(self) -> None:
        while True:
            await asyncio.sleep(self.settings.scan_interval_seconds)
            await self.enqueue_existing_files()

    def enqueue_threadsafe(self, path: Path) -> None:
        if self.loop is None:
            return
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        now = time.time()
        key = str(path)
        last = self._seen.get(key, 0)
        if now - last < 1.5:
            return
        self._seen[key] = now

        asyncio.run_coroutine_threadsafe(self.queue.put(path), self.loop)

    async def worker(self) -> None:
        while True:
            path = await self.queue.get()
            try:
                await self.process_file(path)
            except Exception:
                pass
            finally:
                self.queue.task_done()

    async def process_file(self, path: Path) -> ProcessResult:
        if not path.exists() or not path.is_file():
            return ProcessResult(status="skipped")

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return ProcessResult(status="skipped")

        try:
            raw = path.read_bytes()
            file_hash = hashlib.sha256(raw).hexdigest()
            stat = path.stat()
        except Exception as e:
            self.db.upsert_resume_file(
                file_path=str(path),
                file_hash="",
                file_mtime=int(time.time()),
                file_ctime=int(time.time()),
                discovered_at=int(time.time()),
                status="error",
                parse_error=f"read_error:{e}",
                parse_detail="read_error",
            )
            return ProcessResult(status="error", error=str(e))

        existing = self.db.get_resume_file(str(path))
        if existing and existing.get("file_hash") == file_hash:
            # Same file path and unchanged content: keep prior parse result, don't overwrite with duplicate.
            keep_status = str(existing.get("status") or "done")
            if keep_status == "processing":
                keep_status = "done"
            self.db.upsert_resume_file(
                file_path=str(path),
                file_hash=file_hash,
                file_mtime=int(stat.st_mtime),
                file_ctime=int(stat.st_ctime),
                discovered_at=int(time.time()),
                status=keep_status,
                parse_error=existing.get("parse_error"),
                parse_detail=existing.get("parse_detail"),
            )
            return ProcessResult(status="unchanged")

        resume_file_id = self.db.upsert_resume_file(
            file_path=str(path),
            file_hash=file_hash,
            file_mtime=int(stat.st_mtime),
            file_ctime=int(stat.st_ctime),
            discovered_at=int(time.time()),
            status="processing",
            parse_error=None,
            parse_detail="processing",
        )

        if self.db.has_candidate_hash(file_hash):
            self.db.upsert_resume_file(
                file_path=str(path),
                file_hash=file_hash,
                file_mtime=int(stat.st_mtime),
                file_ctime=int(stat.st_ctime),
                discovered_at=int(time.time()),
                status="duplicate",
                parse_error=None,
                parse_detail="duplicate(hash)",
            )
            return ProcessResult(status="duplicate")

        text, parse_method = await asyncio.to_thread(extract_text_with_method, path)
        candidate = extract_candidate_info(text, path.name)
        llm_used = False

        llm_reason = ""
        needs_key_fields = (not candidate.name) or (candidate.applied_position in {None, "", "未知岗位"})
        should_use_llm = self.settings.enable_llm_enhance and self.llm.enabled() and (
            candidate.confidence < 0.6 or needs_key_fields
        )
        logger.info(
            "ingest.parse file=%s method=%s confidence=%.2f needs_key_fields=%s should_use_llm=%s",
            path.name,
            parse_method,
            candidate.confidence,
            needs_key_fields,
            should_use_llm,
        )
        if should_use_llm:
            enhanced, err = await self.llm.enhance_with_meta(text=text, filename=path.name)
            if enhanced:
                candidate = merge_candidate_info(candidate, enhanced)
                llm_used = True
                llm_reason = "llm_enhanced"
                logger.info("ingest.llm_success file=%s", path.name)
            else:
                llm_reason = f"llm_failed({err or 'empty'})"
                logger.warning("ingest.llm_failed file=%s reason=%s", path.name, err or "empty")

        self.db.insert_candidate(
            CandidateRecord(
                resume_file_id=resume_file_id,
                name=candidate.name,
                phone=candidate.phone,
                email=candidate.email,
                education=candidate.education,
                years_experience=candidate.years_experience,
                skills=candidate.skills,
                applied_position=candidate.applied_position,
            ),
            candidate_hash=file_hash,
            extracted_at=int(time.time()),
        )

        self.db.upsert_resume_file(
            file_path=str(path),
            file_hash=file_hash,
            file_mtime=int(stat.st_mtime),
            file_ctime=int(stat.st_ctime),
            discovered_at=int(time.time()),
            status="done",
            parse_error=None,
            parse_detail=_compose_parse_detail(parse_method=parse_method, llm_used=llm_used, llm_reason=llm_reason),
        )
        self._progress_cache_ts = 0.0
        return ProcessResult(status="done")

    async def get_progress(self) -> dict[str, Any]:
        now = time.time()
        if now - self._progress_cache_ts <= 5:
            return dict(self._progress_cache)

        progress = await asyncio.to_thread(self._calc_progress_sync)
        self._progress_cache = progress
        self._progress_cache_ts = now
        return dict(progress)

    def _calc_progress_sync(self) -> dict[str, Any]:
        root = self.settings.resume_root
        if not root.exists():
            return {
                "total_files": 0,
                "indexed_files": 0,
                "pending_files": 0,
                "percentage": 0.0,
                "last_updated": int(time.time()),
            }

        current_paths: set[str] = set()
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS:
                current_paths.add(str(p))

        total = len(current_paths)
        indexed_paths = self.db.get_indexed_file_paths()
        indexed = len(current_paths & indexed_paths)
        pending = max(total - indexed, 0)
        percentage = round((indexed / total) * 100, 2) if total > 0 else 100.0

        return {
            "total_files": total,
            "indexed_files": indexed,
            "pending_files": pending,
            "percentage": percentage,
            "last_updated": int(time.time()),
        }


def _compose_parse_detail(parse_method: str, llm_used: bool, llm_reason: str) -> str:
    parts = [parse_method]
    if llm_used:
        parts.append("llm_enhanced")
    elif llm_reason:
        parts.append(llm_reason)
    detail = " + ".join(parts)
    return detail[:240]
